# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""Pi MCP activity facts from canonical session JSONL source records only.

Pi has no built-in MCP. MCP servers run through ``pi-mcp-adapter``, which
exposes them either through its ``mcp`` proxy tool (``mcp({server, tool})``),
per-server ``mcp__<server>`` namespace proxies, or directly registered tools.
The model-visible tool name therefore does not identify the server reliably.
The adapter does record the *configured server name* it actually dispatched
to in the tool result's ``details``:

* proxy calls: ``{"mode": "call", "server": ..., "tool": ..., ...}``
* direct tools: ``{"server": ..., "tool": ..., ...}``

That adapter-produced identity, linked to exactly one earlier call by its
tool-call id, is the only attribution source. Arguments are never read except
for the presence of a proxy ``tool`` key, and no transcript text, argument
value, or result output (``details.mcpResult``) leaves this module.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

from .base import load_line, str_field
from .invocation_types import InvocationExtraction, SourceInvocation

# Provider tool-call ids vary (e.g. OpenAI Responses ``call_x|fc_y``); require
# bounded visible ASCII so an id is an exact, unambiguous link key.
_TOOL_ID = re.compile(r"[\x21-\x7e]{1,256}\Z")
# The alias alphabet Observal installs and verifies (see ``observal_cli.layer``).
_SERVER = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_MAX_TOOL_NAME = 256
_PROXY_TOOL = "mcp"

State = Literal["unknown", "success", "error"]


def _tool_id(value: object) -> str:
    return value if isinstance(value, str) and _TOOL_ID.fullmatch(value) else ""


def _name(value: object) -> str:
    return value if isinstance(value, str) and 0 < len(value) <= _MAX_TOOL_NAME else ""


def _event_time(record: dict, row: Mapping[str, object]) -> datetime | None:
    for value in (record.get("timestamp"), row.get("timestamp")):
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
        else:
            continue
        if parsed.year == 1970:
            continue
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    return None


class _Result:
    """One tool result's safe MCP facts; never retains content or mcpResult."""

    __slots__ = ("identity", "mcp_attempt", "mode", "offset", "server", "state", "tool", "tool_name")

    def __init__(self, offset: int, message: dict) -> None:
        self.offset = offset
        self.tool_name = _name(message.get("toolName"))
        details = message.get("details")
        details = details if isinstance(details, dict) else {}
        mode = details.get("mode")
        self.mode = mode if isinstance(mode, str) else None
        server = details.get("server")
        self.server = server if isinstance(server, str) and _SERVER.fullmatch(server) else ""
        self.tool = _name(details.get("tool"))
        # ``mode`` is present only on proxy results; search/describe/list/status
        # results also carry a server name but are not invocations. Direct-tool
        # results carry ``tool`` on success and only ``error`` on failure.
        error = details.get("error")
        self.identity = bool(self.server) and (
            self.mode == "call" or (self.mode is None and (bool(self.tool) or isinstance(error, str)))
        )
        self.mcp_attempt = self.identity or self.mode == "call"
        is_error = message.get("isError")
        self.state: State = "unknown"
        if type(is_error) is bool:
            self.state = "error" if is_error or isinstance(error, str) else "success"


def _direct_prefixes(server: str) -> tuple[str, ...]:
    """Direct-tool name prefixes for the adapter's ``server``, ``short`` and ``mcp`` modes."""
    short = re.sub(r"-?mcp$", "", server, flags=re.IGNORECASE) or "mcp"
    return (f"{server}_", f"{short}_", f"mcp__{server}_")


def _consistent(call_name: str, result: _Result) -> bool:
    """The result's identity must describe the tool surface that was called."""
    if result.tool_name != call_name:
        return False
    if result.mode == "call":
        return call_name == _PROXY_TOOL or call_name.startswith("mcp__")
    if call_name == _PROXY_TOOL:
        return False
    prefixes = _direct_prefixes(result.server)
    if not result.tool:
        # A failed direct call reports ``{error, server}`` only.
        return call_name.startswith(prefixes)
    # ``formatToolName``: dots become ``_``; ``none`` mode uses the bare tool name.
    tool = result.tool.replace(".", "_")
    return call_name == tool or any(call_name == f"{prefix}{tool}" for prefix in prefixes)


class PiInvocationExtractor:
    def extract(self, rows: Sequence[Mapping[str, object]]) -> InvocationExtraction:
        # (offset, block index, tool name, call id, time, proxy call request)
        calls: list[tuple[int, int, str, str, datetime | None, bool]] = []
        results: dict[str, list[_Result]] = {}
        orphan_attempts: list[tuple[int, int, datetime | None, _Result]] = []
        malformed = 0
        for row in rows:
            if row.get("is_source_record") != 1:
                continue
            offset = row.get("line_offset")
            raw_line = row.get("raw_line")
            if type(offset) is not int or offset < 0 or not isinstance(raw_line, str):
                malformed += 1
                continue
            record = load_line(raw_line)
            if record is None:
                malformed += 1
                continue
            if str_field(record, "type") != "message":
                continue
            message = record.get("message")
            if not isinstance(message, dict):
                malformed += 1
                continue
            role = str_field(message, "role")
            time = _event_time(record, row)
            if role == "assistant":
                content = message.get("content")
                if not isinstance(content, list):
                    malformed += 1
                    continue
                for index, block in enumerate(content):
                    if not isinstance(block, dict) or str_field(block, "type") != "toolCall":
                        continue
                    name = _name(block.get("name"))
                    arguments = block.get("arguments")
                    proxy_call = name == _PROXY_TOOL and isinstance(arguments, dict) and "tool" in arguments
                    calls.append((offset, index, name, _tool_id(block.get("id")), time, proxy_call))
            elif role == "toolResult":
                result = _Result(offset, message)
                linked_id = _tool_id(message.get("toolCallId"))
                if linked_id:
                    results.setdefault(linked_id, []).append(result)
                elif result.mcp_attempt:
                    orphan_attempts.append((offset, len(orphan_attempts), time, result))

        # Ids reused across calls cannot establish one result link.
        call_counts = Counter(call[3] for call in calls if call[3])
        line_counts = Counter((call[0], call[3]) for call in calls if call[3])
        invocations: list[SourceInvocation] = []
        linked_ids: set[str] = set()
        for offset, index, name, call_id, time, proxy_call in calls:
            unique_in_line = bool(call_id and line_counts[offset, call_id] == 1)
            block_key = f"id:{call_id}" if unique_in_line else f"index:{index}"
            linked = results.get(call_id, ()) if call_id else ()
            unique = call_counts[call_id] == 1 and len(linked) == 1 and linked[0].offset > offset
            if unique:
                linked_ids.add(call_id)
                result = linked[0]
                if result.identity and _consistent(name, result):
                    invocations.append(
                        SourceInvocation(
                            offset,
                            block_key,
                            result.tool or name,
                            call_id,
                            time,
                            result.state,
                            mcp_server=result.server,
                        )
                    )
                elif proxy_call or name.startswith("mcp__") or (result.mcp_attempt and name == _PROXY_TOOL):
                    # An MCP proxy call whose server identity is absent or does
                    # not describe this call: measurable use, attribution impossible.
                    invocations.append(SourceInvocation(offset, block_key, name, call_id, time, "unknown", ""))
                # Any other tool's MCP-shaped details are not claimed as MCP use.
                continue
            if proxy_call or name.startswith("mcp__") or any(result.mcp_attempt for result in linked):
                # An MCP request without one linkable result keeps unknown state.
                invocations.append(SourceInvocation(offset, block_key, name, call_id, time, "unknown", ""))
            # A direct tool without a linkable result is indistinguishable from
            # any other extension tool; it is not claimed as an MCP call.
        for call_id, linked in results.items():
            if call_id in linked_ids or call_counts[call_id]:
                continue
            for result in linked:
                if result.mcp_attempt:
                    orphan_attempts.append((result.offset, len(orphan_attempts), None, result))
        for offset, ordinal, time, result in orphan_attempts:
            # A result with no call cannot anchor an attributable invocation.
            invocations.append(SourceInvocation(offset, f"orphan:{ordinal}", result.tool_name, "", time, "unknown", ""))
        return InvocationExtraction(
            status="supported", invocations=tuple(invocations), malformed_source_records=malformed
        )
