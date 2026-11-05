# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Claude Code skill evidence from canonical session JSONL source records only.

Observed in Claude Code 2.1.286 sessions (``tests/fixtures/component_insights/claude_code``):

* **load**: the model calls the ``Skill`` tool (``input.skill``). Claude Code
  links a ``tool_result`` (``is_error`` on failure, ``toolUseResult.success``
  on success) and writes the expanded skill as a harness record with
  ``isMeta: true`` and ``sourceToolUseID`` set to the call id, whose text
  begins ``Base directory for this skill: <dir>``. Only a call with a single
  successful result **and** a single linked expansion is a confirmed load;
  the expansion names the file, so a call without one (an unknown skill, for
  example) cannot be attributed to any install.
* **invoked**: ``/name`` is stored as a user record whose content is exactly
  ``<command-message>name</command-message>\\n<command-name>/name</command-name>``
  (optionally ``\\n<command-args>...</command-args>``), followed by an
  ``isMeta: true`` expansion with the same ``promptId`` and no
  ``sourceToolUseID``. ``isMeta`` is written by Claude Code, not by user text:
  the same tags typed into a prompt produce an ordinary user record with no
  expansion, which is not an invocation.
* **available** is never emitted. The ``skill_listing`` attachment lists
  names only, with no location, so it cannot be tied to the verified file.

A location is a candidate install only in Claude Code's layout:
``<session cwd>/.claude/skills/<alias>`` (project scope) or
``.../skills/<alias>`` outside any ``plugins`` directory (user scope). As for
Pi, the layout is not proof: every fact carries the SHA-256 of
``<dir>/SKILL.md`` and the matcher accepts it only when it equals the verified
install's fingerprinted path. Only the scope, alias and that digest leave
this module; paths, prompts and skill text do not.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from .base import load_line, str_field
from .skill_evidence import SkillEvidence, SkillEvidenceExtraction

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_ALIAS = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_TOOL_ID = re.compile(r"[\x21-\x7e]{1,256}\Z")
_BASE_DIR = re.compile(r"Base directory for this skill: ([^\n]{1,4096})\n")
_COMMAND = re.compile(
    r"<command-message>([^<\n]{1,128})</command-message>\n<command-name>/([^<\n]{1,128})</command-name>"
    r"(?:\n<command-args>[\s\S]*</command-args>)?\Z"
)

Scope = Literal["user", "project"]


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


def _install(base_dir: str, cwd: str) -> tuple[Scope, str] | None:
    """The (scope, alias) a skill base directory belongs to, if it is in Claude Code's layout."""
    base = base_dir.rstrip("/")
    head, _, alias = base.rpartition("/")
    if not _ALIAS.fullmatch(alias) or not head.endswith("/skills"):
        return None
    if cwd and head == cwd.rstrip("/") + "/.claude/skills":
        return "project", alias
    if "/plugins/" in head + "/":
        return None  # plugin skills are namespaced and not Observal installs
    return "user", alias


def location_sha256(base_dir: str) -> str:
    """Digest of ``<dir>/SKILL.md``, compared with the verifier's (``observal_cli.layer``)."""
    return hashlib.sha256(f"{base_dir.rstrip('/')}/SKILL.md".encode()).hexdigest()


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict) and isinstance(b.get("text"), str))
    return ""


class ClaudeCodeSkillEvidenceExtractor:
    # The skill listing has names only, so availability cannot be tied to a file.
    observed_kinds: frozenset = frozenset({"load", "invoked"})

    def extract(self, rows: Sequence[Mapping[str, object]]) -> SkillEvidenceExtraction:
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

        cwd = next((str_field(r, "cwd") for _, r, _ in records if str_field(r, "cwd")), "")
        calls: list[tuple[int, int, str, datetime | None]] = []  # offset, block index, call id, time
        results: dict[str, list[tuple[int, Literal["unknown", "success", "error"]]]] = defaultdict(list)
        tool_expansions: dict[str, list[str]] = defaultdict(list)  # call id -> base dirs
        commands: dict[str, list[str]] = defaultdict(list)  # promptId -> command names
        invocations: list[tuple[int, str, str, datetime | None]] = []  # offset, promptId, base dir, time

        for offset, record, row in records:
            kind = str_field(record, "type")
            message = record.get("message")
            if kind not in ("user", "assistant") or not isinstance(message, dict):
                continue
            content = message.get("content")
            if kind == "assistant":
                for index, block in enumerate(content if isinstance(content, list) else []):
                    if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Skill":
                        call_id = block.get("id") if isinstance(block.get("id"), str) else ""
                        calls.append(
                            (offset, index, call_id if _TOOL_ID.fullmatch(call_id) else "", _event_time(record, row))
                        )
                continue
            prompt_id = record.get("promptId") if isinstance(record.get("promptId"), str) else ""
            if record.get("isMeta") is True:
                match = _BASE_DIR.match(_text(content))
                if not match:
                    continue
                source = record.get("sourceToolUseID")
                if isinstance(source, str) and source:
                    tool_expansions[source].append(match.group(1))
                elif "sourceToolUseID" not in record and prompt_id:
                    invocations.append((offset, prompt_id, match.group(1), _event_time(record, row)))
                continue
            if isinstance(content, str):
                command = _COMMAND.fullmatch(content)
                if command and command.group(1) == command.group(2) and prompt_id:
                    commands[prompt_id].append(command.group(1))
                continue
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                call_id = block.get("tool_use_id")
                if not isinstance(call_id, str) or not _TOOL_ID.fullmatch(call_id):
                    continue
                outcome = record.get("toolUseResult")
                if block.get("is_error") is True:
                    state: Literal["unknown", "success", "error"] = "error"
                elif isinstance(outcome, dict) and outcome.get("success") is True and "is_error" not in block:
                    state = "success"
                else:
                    state = "unknown"
                results[call_id].append((offset, state))

        evidence: list[SkillEvidence] = []
        call_counts = Counter(call_id for _, _, call_id, _ in calls if call_id)
        for offset, index, call_id, time in calls:
            unique = bool(call_id) and call_counts[call_id] == 1
            expansions = tool_expansions.get(call_id, []) if unique else []
            install = _install(expansions[0], cwd) if len(expansions) == 1 else None
            if install is None:
                malformed += int(len(expansions) > 1)
                continue  # no single linked expansion: the call names no file
            linked = results.get(call_id, [])
            state = linked[0][1] if len(linked) == 1 and linked[0][0] > offset else "unknown"
            evidence.append(
                SkillEvidence(
                    "load",
                    *install,
                    offset,
                    f"skill-load:{index}",
                    time,
                    state,
                    call_id,
                    location_sha256(expansions[0]),
                )
            )
        for offset, prompt_id, base_dir, time in invocations:
            install = _install(base_dir, cwd)
            # The harness wrote this expansion for exactly one matching /name command in the same prompt.
            if install is None or commands.get(prompt_id, []) != [install[1]]:
                continue
            evidence.append(
                SkillEvidence(
                    "invoked", *install, offset, "skill-invoked:0", time, location_sha256=location_sha256(base_dir)
                )
            )
        return SkillEvidenceExtraction(status="supported", evidence=tuple(evidence), malformed_source_records=malformed)
