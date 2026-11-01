# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Pi skill evidence from canonical session JSONL source records only.

Observed in Pi 0.99.2 sessions (``tests/fixtures/component_insights/pi``):

* **available**: the ``system`` message's ``sections.skills`` lists each
  advertised skill as ``<skill><name/><description/><location/></skill>``
  inside ``<available_skills>``. ``location`` is the absolute SKILL.md path.
* **load**: an assistant ``toolCall`` named ``read`` whose
  ``arguments.path`` is a location advertised *at that point* in the session,
  linked to its ``toolResult`` by tool-call id. Only a linked, successful read
  is a confirmed load.
* **invoked** is never emitted. ``/skill:name`` is expanded into an ordinary
  user message containing ``<skill name="..." location="...">``; Pi records
  no origin that separates it from the same text typed or pasted by the user
  (Pi 0.99.2 ``_expandSkillCommand``), so it is not evidence of invocation.

A location is a candidate install only in Pi's own layout:
``.../.pi/agent/skills/<alias>/SKILL.md`` (user scope) or
``<session cwd>/.pi/skills/<alias>/SKILL.md`` (project scope). Anything else,
such as ``~/.agents/skills``, yields no evidence. The layout alone does not
prove the location is *this* installation's skill, so every fact carries the
SHA-256 of the exact location and the matcher accepts it only when it equals
the verified install's fingerprinted path. Only the scope, alias and that
digest leave this module; locations, descriptions and skill text do not.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from .base import load_line, str_field
from .skill_evidence import SkillEvidence, SkillEvidenceExtraction

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_ALIAS = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_TOOL_ID = re.compile(r"[\x21-\x7e]{1,256}\Z")
_ADVERTISED = re.compile(r"<skill>\s*<name>.*?</name>.*?<location>(.*?)</location>\s*</skill>", re.S)
_USER_LAYOUT = re.compile(r"/\.pi/agent/skills/([^/]+)/SKILL\.md\Z")
_MAX_LOCATIONS = 512


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


def _install(location: str, cwd: str) -> tuple[Literal["user", "project"], str] | None:
    """The (scope, alias) an advertised SKILL.md location belongs to, if any."""
    if not isinstance(location, str) or len(location) > 4096:
        return None
    match = _USER_LAYOUT.search(location)
    if match:
        scope: Literal["user", "project"] = "user"
        alias = match.group(1)
    elif cwd and location.startswith(cwd.rstrip("/") + "/.pi/skills/"):
        rest = location[len(cwd.rstrip("/") + "/.pi/skills/") :]
        alias, _, tail = rest.partition("/")
        if tail != "SKILL.md":
            return None
        scope = "project"
    else:
        return None
    return (scope, alias) if _ALIAS.fullmatch(alias) else None


def location_sha256(location: str) -> str:
    """Digest of a recorded SKILL.md path, compared with the verifier's (``observal_cli.layer``)."""
    return hashlib.sha256(location.encode("utf-8")).hexdigest()


class PiSkillEvidenceExtractor:
    # /skill:name leaves no distinguishable origin in Pi sessions: never "invoked".
    observed_kinds: frozenset = frozenset({"available", "load"})

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

        cwd = next((str_field(r, "cwd") for _, r, _ in records if str_field(r, "type") == "session"), "")
        evidence: list[SkillEvidence] = []
        advertised: dict[str, tuple[Literal["user", "project"], str]] = {}
        # offset, block index, call id, (scope, alias) and location bound when the read happened, time
        reads: list[tuple[int, int, str, tuple[Literal["user", "project"], str], str, datetime | None]] = []
        results: dict[str, list[tuple[int, Literal["unknown", "success", "error"]]]] = {}

        for offset, record, row in records:
            if str_field(record, "type") != "message":
                continue
            message = record.get("message")
            if not isinstance(message, dict):
                malformed += 1
                continue
            role, time = message.get("role"), _event_time(record, row)
            if role == "system":
                sections = message.get("sections")
                skills = sections.get("skills") if isinstance(sections, dict) else None
                if not isinstance(skills, str):
                    continue
                # A later system record (e.g. after /reload) replaces what is advertised.
                advertised = {}
                for location in _ADVERTISED.findall(skills)[:_MAX_LOCATIONS]:
                    install = _install(location.strip(), cwd)
                    if install and install not in advertised.values():
                        advertised[location.strip()] = install
                        key = f"skill-available:{install[0]}:{install[1]}"
                        digest = location_sha256(location.strip())
                        evidence.append(SkillEvidence("available", *install, offset, key, time, location_sha256=digest))
            elif role == "assistant":
                for index, block in enumerate(message.get("content") or []):
                    if not isinstance(block, dict) or block.get("type") != "toolCall" or block.get("name") != "read":
                        continue
                    arguments = block.get("arguments")
                    location = arguments.get("path") if isinstance(arguments, dict) else None
                    if isinstance(location, str) and location in advertised:
                        call_id = block.get("id") if isinstance(block.get("id"), str) else ""
                        call_id = call_id if _TOOL_ID.fullmatch(call_id or "") else ""
                        # Bind the install now: a later /reload may advertise different skills.
                        reads.append((offset, index, call_id, advertised[location], location, time))
            elif role == "toolResult":
                call_id = message.get("toolCallId")
                if isinstance(call_id, str) and _TOOL_ID.fullmatch(call_id):
                    flag = message.get("isError")
                    state: Literal["unknown", "success", "error"] = (
                        ("error" if flag else "success") if type(flag) is bool else "unknown"
                    )
                    results.setdefault(call_id, []).append((offset, state))

        call_counts = Counter(call_id for _, _, call_id, _, _, _ in reads if call_id)
        for offset, index, call_id, install, location, time in reads:
            unique = bool(call_id) and call_counts[call_id] == 1
            linked = results.get(call_id, ()) if unique else ()
            state = linked[0][1] if len(linked) == 1 and linked[0][0] > offset else "unknown"
            # The block index is unique within a line; a call id is not always present.
            key = f"skill-load:{index}"
            evidence.append(
                SkillEvidence(
                    "load", *install, offset, key, time, state, call_id if unique else "", location_sha256(location)
                )
            )
        return SkillEvidenceExtraction(status="supported", evidence=tuple(evidence), malformed_source_records=malformed)
