# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Tested Codex CLI MCP calls from native item-completed records only.

Native ``event_msg`` items identify the configured server, tool, completion
status and explicit MCP result error flag. An executed tool error has status
``failed`` with a non-null result whose ``isError`` is true. A denied approval
also has status ``failed`` but no result: decline the entire session rather
than reporting it as a use or measured zero. Model-generated function calls,
code-mode text, prompts, arguments and result bodies are not identity evidence.
"""

from __future__ import annotations

import posixpath
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from observal_shared.harness_registry import HARNESS_REGISTRY

from .base import load_line
from .invocation_types import InvocationExtraction, SourceInvocation

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_TESTED_VERSIONS = frozenset(HARNESS_REGISTRY["codex"]["mcp_native_tested_versions"])
_ALIAS = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_TOOL = re.compile(r"[A-Za-z0-9_.-]{1,256}\Z")
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def _time(record: dict) -> datetime | None:
    """Never substitute ingestion time for a missing native call timestamp."""
    value = record.get("timestamp")
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if 1971 <= parsed.year < 2100:
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    return None


class CodexMCPInvocationExtractor:
    def extract(self, rows: Sequence[Mapping[str, object]]) -> InvocationExtraction:
        metas: list[dict] = []
        calls: list[SourceInvocation] = []
        ids: set[str] = set()
        pending: set[str] = set()
        malformed = 0
        ambiguous = False
        for row in rows:
            if row.get("is_source_record") != 1:
                continue
            offset, raw = row.get("line_offset"), row.get("raw_line")
            if (
                type(offset) is not int
                or offset < 0
                or not isinstance(raw, str)
                or row.get("raw_line_truncated") not in (0, None)
            ):
                malformed += 1
                continue
            record = load_line(raw)
            if record is None:
                malformed += 1
                continue
            if record.get("type") == "session_meta":
                payload = record.get("payload")
                metas.append(payload if isinstance(payload, dict) else {})
                continue
            if record.get("type") != "event_msg":
                continue
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            item = payload.get("item")
            if not isinstance(item, dict) or item.get("type") != "McpToolCall":
                continue
            if payload.get("type") != "item_completed":
                if payload.get("type") == "item_started":
                    started_id = item.get("id")
                    if not isinstance(started_id, str) or not _ID.fullmatch(started_id) or started_id in pending:
                        ambiguous = True
                    else:
                        pending.add(started_id)
                else:
                    # A new lifecycle form needs its own controlled native run.
                    ambiguous = True
                continue
            # An executed error has failed status AND an explicit error result.
            # A declined permission has no result and cannot supply a call or zero.
            server, tool, call_id = item.get("server"), item.get("tool"), item.get("id")
            result = item.get("result")
            if (
                not isinstance(server, str)
                or not _ALIAS.fullmatch(server)
                or not isinstance(tool, str)
                or not _TOOL.fullmatch(tool)
                or not isinstance(call_id, str)
                or not _ID.fullmatch(call_id)
                or call_id in ids
                or not isinstance(result, dict)
                or type(result.get("isError")) is not bool
                or (item.get("status"), result["isError"]) not in (("completed", False), ("failed", True))
            ):
                ambiguous = True
                continue
            event_time = _time(record)
            if event_time is None:
                ambiguous = True
                continue
            ids.add(call_id)
            pending.discard(call_id)
            calls.append(
                SourceInvocation(
                    offset,
                    f"id:{call_id}",
                    tool,
                    call_id,
                    event_time,
                    "error" if result["isError"] else "success",
                    mcp_server=server,
                )
            )
        if (
            malformed
            or ambiguous
            or pending
            or len(metas) != 1
            or metas[0].get("cli_version") not in _TESTED_VERSIONS
            or not isinstance(metas[0].get("cwd"), str)
            or not metas[0]["cwd"].startswith("/")
            or posixpath.normpath(metas[0]["cwd"]) != metas[0]["cwd"]
        ):
            return InvocationExtraction("unsupported", (), malformed)
        return InvocationExtraction("supported", tuple(calls), 0)
