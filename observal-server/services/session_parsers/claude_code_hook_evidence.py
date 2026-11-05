# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Claude Code hook evidence from canonical session JSONL source records only.

Observed in Claude Code 2.1.286 sessions (``tests/fixtures/component_insights/claude_code``):

* **ran_with_output**: an ``attachment`` of type ``hook_success`` with
  ``hookEvent``, ``command`` (verbatim as configured) and ``exitCode: 0``.
  Claude Code writes it only when the hook printed output. A silent success
  leaves no record.
* **failed**: an ``attachment`` of type ``hook_non_blocking_error`` with the
  same fields and a non-zero ``exitCode``.
* **blocked**: no hook attachment. The blocked tool's ``tool_result`` has
  ``is_error: true`` and the content ``"<Event>:<Tool> hook error: [<command>]: ..."``,
  and the record carries ``toolDenialKind``, which Claude Code sets and tool
  output cannot.

Session context: ``entrypoint`` is ``sdk-cli`` for headless ``-p`` runs and
``cli`` for interactive ones. ``agent-setting`` records name the active
``--agent``. A subagent's own transcript has ``isSidechain: true`` and an
``agentId`` on its records but no ``agent-setting`` and no agent name
(``gate_session_headless_subagent.jsonl``), so it records that a subagent ran,
not which one. Its records' ``sessionId`` is the parent session's id.

The parent session records which agent it spawned
(``gate_session_headless_subagent_main.jsonl``): an assistant ``tool_use``
named ``Agent`` with ``input.subagent_type``, and the ``user`` record carrying
its ``tool_result`` with a structured ``toolUseResult`` holding the same
``agentId`` and ``agentType``. ``toolUseResult`` is a top-level record field
Claude Code writes, not tool output text, and is trusted only when it is linked
by ``tool_use_id`` to exactly one ``Agent`` call with the same
``subagent_type`` (an MCP tool's structured result cannot pose as one).
``subagent_results`` and ``agent_tool_calls`` read those records; any
unreadable or disagreeing record leaves the subagent unresolved.

Claude Code writes its version on each record (``version``). Records and hook
inputs were proven only on the registry's ``hook_evidence_tested_versions``; a
session recorded on any other version (or none) sets
``harness_version_unverified``, so its hooks without a recorded run are
``version_unverified`` rather than eligible with no runs.

Runs of Observal's own telemetry hooks (``-m observal_cli.hooks.*``) are not
evidence: they are never a registry component, and counting them would report
an attribution gap where there is none.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from observal_shared.harness_registry import HARNESS_REGISTRY

from .base import load_line, str_field
from .hook_evidence import (
    HookEvidence,
    HookEvidenceExtraction,
    HookSession,
    hook_binding_sha256,
    is_observal_telemetry_hook,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_EVENT = re.compile(r"[A-Za-z]{1,64}\Z")
_TOOL_ID = re.compile(r"[\x21-\x7e]{1,256}\Z")
_AGENT = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")
_BLOCKED = re.compile(r"(?P<event>[A-Za-z]{1,64}):[^ \n]{1,256} hook error: \[(?P<command>[^\n]{1,4096}?)\]: ")
_OUTCOMES = {"hook_success": "ran_with_output", "hook_non_blocking_error": "failed"}
_MAX_COMMAND = 4096
# Subagent and session ids, and Agent tool-call ids, used as exact link keys and as
# substring needles over stored JSON lines: this alphabet needs no JSON escaping.
_LINK_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
# An agent type is only compared, never stored; plugin agents are ``plugin:name``.
_AGENT_TYPE = re.compile(r"[\x21-\x7e]{1,256}\Z")
_VERSION = re.compile(r"(\d{1,4})\.(\d{1,4})\.(\d{1,6})\Z")
_TESTED_MIN, _TESTED_MAX = HARNESS_REGISTRY["claude-code"]["hook_evidence_tested_versions"]


def _version_verified(versions: set[str]) -> bool:
    """Every Claude Code version the session recorded is inside the tested range (and one was recorded)."""
    if not versions:
        return False
    for version in versions:
        match = _VERSION.fullmatch(version)
        if match is None or not _TESTED_MIN <= tuple(int(part) for part in match.groups()) <= _TESTED_MAX:
            return False
    return True


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


def _binding(event: object, command: object) -> str | None:
    if not isinstance(event, str) or not _EVENT.fullmatch(event):
        return None
    if not isinstance(command, str) or not command or len(command) > _MAX_COMMAND:
        return None
    return hook_binding_sha256(event, command)


class ClaudeCodeHookEvidenceExtractor:
    observed_kinds: frozenset = frozenset({"ran_with_output", "failed", "blocked"})
    records_silent_success = False

    def extract(self, rows: Sequence[Mapping[str, object]]) -> HookEvidenceExtraction:
        records: list[tuple[int, dict, Mapping[str, object]]] = []
        malformed = 0
        for row in rows:
            if row.get("is_source_record") != 1:
                continue
            offset, raw_line = row.get("line_offset"), row.get("raw_line")
            if type(offset) is not int or offset < 0 or not isinstance(raw_line, str):
                malformed += 1
                continue
            record = load_line(raw_line)
            if record is None:
                malformed += 1
                continue
            records.append((offset, record, row))
        records.sort(key=lambda item: item[0])

        evidence: list[HookEvidence] = []
        versions: set[str] = set()
        entrypoints: set[str] = set()
        agents: set[str] = set()
        sidechain: set[bool] = set()
        subagent_ids = False
        # (sessionId, agentId) on sidechain records; None marks a record without a valid pair.
        links: set[tuple[str, str] | None] = set()
        for offset, record, row in records:
            kind = str_field(record, "type")
            version = record.get("version")
            if isinstance(version, str):
                versions.add(version)
            entrypoint = str_field(record, "entrypoint")
            if entrypoint:
                entrypoints.add(entrypoint)
            if isinstance(record.get("isSidechain"), bool):
                sidechain.add(record["isSidechain"])
                subagent_ids = subagent_ids or (record["isSidechain"] and isinstance(record.get("agentId"), str))
                if record["isSidechain"]:
                    parent, agent_id = record.get("sessionId"), record.get("agentId")
                    links.add((parent, agent_id) if _is_link_id(parent) and _is_link_id(agent_id) else None)
            if kind == "agent-setting":
                agent = record.get("agentSetting")
                if isinstance(agent, str) and _AGENT.fullmatch(agent):
                    agents.add(agent)
                continue
            attachment = record.get("attachment")
            if kind == "attachment" and isinstance(attachment, dict) and attachment.get("type") in _OUTCOMES:
                outcome = _OUTCOMES[attachment["type"]]
                code = attachment.get("exitCode")
                consistent = type(code) is int and ((code == 0) == (outcome == "ran_with_output"))
                command = attachment.get("command")
                if isinstance(command, str) and is_observal_telemetry_hook(command):
                    continue  # Observal's own session push, not a component
                binding = _binding(attachment.get("hookEvent"), command)
                if binding is None or not consistent:
                    malformed += 1
                    continue
                tool_use_id = attachment.get("toolUseID")
                evidence.append(
                    HookEvidence(
                        outcome,  # type: ignore[arg-type]
                        binding,
                        offset,
                        "hook-run:0",
                        _event_time(record, row),
                        tool_use_id if isinstance(tool_use_id, str) and _TOOL_ID.fullmatch(tool_use_id) else "",
                    )
                )
                continue
            message = record.get("message")
            if kind != "user" or not record.get("toolDenialKind") or not isinstance(message, dict):
                continue
            content = message.get("content")
            for index, block in enumerate(content if isinstance(content, list) else []):
                if (
                    not isinstance(block, dict)
                    or block.get("type") != "tool_result"
                    or block.get("is_error") is not True
                ):
                    continue
                text = block.get("content")
                match = _BLOCKED.match(text) if isinstance(text, str) else None
                if match and is_observal_telemetry_hook(match["command"]):
                    continue
                binding = _binding(match["event"], match["command"]) if match else None
                if binding is None:
                    continue
                tool_use_id = block.get("tool_use_id")
                evidence.append(
                    HookEvidence(
                        "blocked",
                        binding,
                        offset,
                        f"hook-blocked:{index}",
                        _event_time(record, row),
                        tool_use_id if isinstance(tool_use_id, str) and _TOOL_ID.fullmatch(tool_use_id) else "",
                    )
                )
        headless = (
            True
            if entrypoints and all(value.startswith("sdk") for value in entrypoints)
            else False
            if entrypoints == {"cli"}
            else None
        )
        subagent = sidechain == {True} and subagent_ids
        # Only one consistent (parent, subagent) pair on every sidechain record is a link.
        link = next(iter(links)) if subagent and len(links) == 1 else None
        return HookEvidenceExtraction(
            status="supported",
            evidence=tuple(evidence),
            # Recorded: agent frontmatter hooks did not run under headless -p (fixtures).
            session=HookSession(
                headless=headless,
                agents=frozenset(agents),
                agent_hooks_run_headless=False,
                # Only when every record that declares isSidechain is one: older
                # transcripts inlined sidechain records next to main-thread ones.
                subagent=subagent,
                parent_session_id=link[0] if link else "",
                subagent_id=link[1] if link else "",
                harness_version_unverified=not _version_verified(versions),
            ),
            malformed_source_records=malformed,
        )

    @staticmethod
    def subagent_results(
        rows: Sequence[Mapping[str, object]], parent_session_id: str, subagent_id: str
    ) -> dict[str, str] | None:
        """Main-thread tool results in the parent that name ``subagent_id``: tool-use id -> agent type.

        ``rows`` must hold every parent source record that mentions the subagent
        id. None when any of them is unreadable, so a disagreeing record is never
        silently missed. Records that are not a single structured tool result of
        the parent's main thread are not spawn results and are skipped.
        """
        results: dict[str, set[str]] = {}
        for row in rows:
            record = _readable(row)
            if record is None:
                return None
            spawn = record.get("toolUseResult")
            if (
                record.get("sessionId") != parent_session_id
                or str_field(record, "type") != "user"
                or record.get("isSidechain") is not False
                or not isinstance(spawn, dict)
                or spawn.get("agentId") != subagent_id
            ):
                continue
            message = record.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            blocks = content if isinstance(content, list) else []
            tool_results = [block for block in blocks if isinstance(block, dict) and block.get("type") == "tool_result"]
            if len(tool_results) != 1:
                continue
            tool_use_id, agent_type = tool_results[0].get("tool_use_id"), spawn.get("agentType")
            if _is_link_id(tool_use_id) and isinstance(agent_type, str) and _AGENT_TYPE.fullmatch(agent_type):
                results.setdefault(tool_use_id, set()).add(agent_type)
        if any(len(types) != 1 for types in results.values()):
            return None
        return {tool_use_id: next(iter(types)) for tool_use_id, types in results.items()}

    @staticmethod
    def agent_tool_calls(
        rows: Sequence[Mapping[str, object]], parent_session_id: str, tool_use_ids: frozenset[str]
    ) -> dict[str, str] | None:
        """The parent's main-thread ``Agent`` calls with these ids: tool-use id -> requested ``subagent_type``.

        ``rows`` must hold every parent source record that mentions any of the
        ids. A call with another tool name, or without a ``subagent_type``, is
        left out: it is not a named Agent spawn. None when any row is unreadable,
        or when an id names a call outside the parent's main thread or more than
        one distinct call.
        """
        calls: dict[str, set[tuple[str, str]]] = {}
        for row in rows:
            record = _readable(row)
            if record is None:
                return None
            message = record.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if str_field(record, "type") != "assistant" or not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                tool_use_id = block.get("id")
                if tool_use_id not in tool_use_ids:
                    continue
                if record.get("sessionId") != parent_session_id or record.get("isSidechain") is not False:
                    return None
                tool_input = block.get("input")
                requested = tool_input.get("subagent_type") if isinstance(tool_input, dict) else None
                name = block.get("name")
                calls.setdefault(tool_use_id, set()).add(
                    (name if isinstance(name, str) else "", requested if isinstance(requested, str) else "")
                )
        if any(len(found) != 1 for found in calls.values()):
            return None
        named: dict[str, str] = {}
        for tool_use_id, found in calls.items():
            name, requested = next(iter(found))
            if name == "Agent" and requested:
                named[tool_use_id] = requested
        return named

    @staticmethod
    def resolve_agent(results: Mapping[str, str], calls: Mapping[str, str]) -> str:
        """The one agent type the parent recorded for a subagent, or '' when unknown or disagreeing.

        A result counts only when its linked call is a named ``Agent`` spawn asking
        for that same type. A linked spawn that asked for another type, or results
        naming more than one type, leave it unknown.
        """
        types: set[str] = set()
        for tool_use_id, agent_type in results.items():
            requested = calls.get(tool_use_id)
            if requested is None:
                continue
            if requested != agent_type:
                return ""
            types.add(agent_type)
        return next(iter(types)) if len(types) == 1 else ""


def _is_link_id(value: object) -> bool:
    return isinstance(value, str) and bool(_LINK_ID.fullmatch(value))


def _readable(row: Mapping[str, object]) -> dict | None:
    """The stored record as JSON, or None when it is truncated or not a JSON object."""
    raw_line = row.get("raw_line")
    if row.get("raw_line_truncated") or not isinstance(raw_line, str):
        return None
    return load_line(raw_line)
