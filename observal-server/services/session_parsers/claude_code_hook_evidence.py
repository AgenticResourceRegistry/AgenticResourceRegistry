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
``--agent``.

Runs of Observal's own telemetry hooks (``-m observal_cli.hooks.*``) are not
evidence: they are never a registry component, and counting them would report
an attribution gap where there is none.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

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
        entrypoints: set[str] = set()
        agents: set[str] = set()
        for offset, record, row in records:
            kind = str_field(record, "type")
            entrypoint = str_field(record, "entrypoint")
            if entrypoint:
                entrypoints.add(entrypoint)
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
        return HookEvidenceExtraction(
            status="supported",
            evidence=tuple(evidence),
            # Recorded: agent frontmatter hooks did not run under headless -p (fixtures).
            session=HookSession(headless=headless, agents=frozenset(agents), agent_hooks_run_headless=False),
            malformed_source_records=malformed,
        )
