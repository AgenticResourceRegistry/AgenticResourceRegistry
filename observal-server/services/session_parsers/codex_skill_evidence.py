# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Codex 0.163.0/0.165.0 project/user skill inventory and native selection provenance.

Codex writes the skills inventory in a developer ``response_item`` as a
``## Skills`` block with a ``### Skill roots`` table and an ``### Available
skills`` list. The inventory names a root and SKILL.md for each entry. Only a
single, exact project ``<cwd>/.agents/skills/<alias>/SKILL.md`` or native
non-project ``.agents/skills/<alias>/SKILL.md`` location can be a candidate;
only a verified contemporaneous pin at the exact location and scope can match.
A non-project inventory root alone does not prove the user's home. For either
scope, a no-selection denominator additionally needs this listing's attributed
native offer. Selection is a separate first-party
``skills.selected_skill_instructions`` content kind, with one tagged, rooted
SKILL.md expansion linked to a user-text message in the same turn. Pasted
markup, an ordinary file read or a ``$name`` user message alone never qualifies.
No prompt, description, instruction body or path is published.

Other Codex versions and missing/ambiguous inventories are unsupported, not
measured zero. Parent-workspace roots cannot match a different pinned home.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
from collections import defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .base import load_line
from .skill_evidence import SkillEvidence, SkillEvidenceExtraction

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_TESTED_VERSIONS = frozenset({"0.163.0", "0.165.0"})
_ALIAS = r"[A-Za-z0-9_-]{1,128}"
_ROOT = re.compile(r"- `(r[0-9]{1,3})` = `(/[^`\r\n]{1,4096})`\Z")
_ENTRY = re.compile(
    rf"- (?P<alias>{_ALIAS}): [^\r\n]{{1,4096}} \(file: (?P<root>r[0-9]{{1,3}})/(?P=alias)/SKILL\.md\)\Z"
)
_SELECTION = re.compile(
    rf"<skill>\n<name>(?P<alias>{_ALIAS})</name>\n<path>(?P<path>/[^<>\r\n]{{1,4096}}/SKILL\.md)</path>\n(?P<body>[\s\S]{{0,65536}})</skill>\Z"
)


def _mac_tmp(path: str) -> str:
    """Compare macOS's canonical /private/tmp to the recorded /tmp cwd."""
    return path.removeprefix("/private") if path.startswith("/private/tmp/") else path


def _time(record: dict, row: Mapping[str, object]) -> datetime | None:
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
        if parsed.year != 1970:
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    return None


class CodexSkillEvidenceExtractor:
    observed_kinds: frozenset = frozenset({"available", "invoked"})
    observed_scopes: frozenset = frozenset({"project", "user"})
    requires_verified_scope: bool = True

    def extract(self, rows: Sequence[Mapping[str, object]]) -> SkillEvidenceExtraction:
        records: list[tuple[int, dict, Mapping[str, object]]] = []
        malformed = 0
        for row in rows:
            if row.get("is_source_record") != 1:
                continue
            offset, raw = row.get("line_offset"), row.get("raw_line")
            if type(offset) is not int or offset < 0 or not isinstance(raw, str):
                malformed += 1
                continue
            record = load_line(raw)
            if record is None:
                malformed += 1
                continue
            records.append((offset, record, row))
        records.sort(key=lambda item: item[0])
        # A corrupt or missing canonical record might contain the only skill
        # selection. Never promote an incomplete stream into a measured zero.
        if malformed or [offset for offset, _, _ in records] != list(range(len(records))):
            return SkillEvidenceExtraction("unsupported", (), malformed)

        metas = [record.get("payload") for _, record, _ in records if record.get("type") == "session_meta"]
        if len(metas) != 1 or not isinstance(metas[0], dict) or metas[0].get("cli_version") not in _TESTED_VERSIONS:
            return SkillEvidenceExtraction("unsupported", (), malformed)
        cwd = metas[0].get("cwd")
        if not isinstance(cwd, str) or not cwd.startswith("/") or posixpath.normpath(cwd) != cwd:
            return SkillEvidenceExtraction("unsupported", (), malformed)
        project_skills = _mac_tmp(cwd).rstrip("/") + "/.agents/skills"
        candidates: dict[str, list[SkillEvidence]] = defaultdict(list)
        alias_occurrences: dict[str, int] = defaultdict(int)
        inventories = 0
        supported_root_seen = False
        ambiguous = False
        for offset, record, row in records:
            if record.get("type") != "response_item":
                continue
            payload = record.get("payload")
            if not isinstance(payload, dict) or payload.get("type") != "message" or payload.get("role") != "developer":
                continue
            blocks = payload.get("content")
            if not isinstance(blocks, list):
                continue
            for block in blocks:
                if not isinstance(block, dict) or block.get("type") != "input_text":
                    continue
                text = block.get("text")
                if not isinstance(text, str):
                    continue
                # Codex prefixes this native developer block with a short
                # internal header before the skills section. Never search
                # arbitrary prompt text for a pasted imitation of the list.
                heading = text.find("## Skills\n")
                if heading not in (0, 22) or (heading and text[heading - 1] != "\n"):
                    continue
                roots_text, separator, entries_text = text[heading:].partition("### Available skills\n")
                if not separator or "### Skill roots\n" not in roots_text:
                    continue
                inventories += 1
                roots: dict[str, list[str]] = defaultdict(list)
                for line in roots_text.splitlines():
                    match = _ROOT.fullmatch(line)
                    if line.startswith("- `r") and not match:
                        ambiguous = True
                    if match:
                        root = match.group(2)
                        if posixpath.normpath(root) != root or root.startswith("//"):
                            ambiguous = True
                            continue
                        roots[match.group(1)].append(root)
                        supported_root_seen |= _mac_tmp(root) == project_skills or root.endswith("/.agents/skills")
                ambiguous |= any(len(locations) != 1 for locations in roots.values())
                for line in entries_text.splitlines():
                    match = _ENTRY.fullmatch(line)
                    if line.startswith("- ") and not match:
                        ambiguous = True
                    if not match:
                        continue
                    alias, root_id = match.group("alias", "root")
                    alias_occurrences[alias] += 1
                    if len(roots[root_id]) != 1:
                        ambiguous = True
                        continue
                    root = roots[root_id][0]
                    if _mac_tmp(root) == project_skills:
                        scope = "project"
                    elif root.endswith("/.agents/skills"):
                        # The native inventory cannot identify HOME by itself.
                        # Match only the verified user pin at this exact path;
                        # parent workspace roots with another path remain unmatched.
                        scope = "user"
                    else:
                        continue
                    location = f"{root}/{alias}/SKILL.md"
                    candidates[alias].append(
                        SkillEvidence(
                            "available",
                            scope,
                            alias,
                            offset,
                            f"skill-available:{scope}:{alias}",
                            _time(record, row),
                            location_sha256=hashlib.sha256(location.encode()).hexdigest(),
                        )
                    )
        # An unfamiliar root may be a symlink or changed inventory layout;
        # duplicated roots/aliases make absence and scope ambiguous.
        if inventories != 1 or not supported_root_seen or ambiguous or any(n != 1 for n in alias_occurrences.values()):
            return SkillEvidenceExtraction("unsupported", (), malformed)
        evidence = [items[0] for _, items in sorted(candidates.items())]
        prompt_turns: dict[str, int] = {}
        combined_context_turns: set[str] = set()
        seen_selections: set[tuple[str, str]] = set()
        selected: list[SkillEvidence] = []
        for offset, record, row in records:
            if record.get("type") != "response_item":
                continue
            payload = record.get("payload")
            if not isinstance(payload, dict) or payload.get("type") != "message" or payload.get("role") != "user":
                continue
            metadata = payload.get("internal_chat_message_metadata_passthrough")
            if not isinstance(metadata, dict):
                return SkillEvidenceExtraction("unsupported", (), malformed)
            kinds, turn_id = metadata.get("content_item_kinds"), metadata.get("turn_id")
            if not isinstance(turn_id, str) or not turn_id:
                return SkillEvidenceExtraction("unsupported", (), malformed)
            # Codex 0.163.0 can supply AGENTS.md and environment context in
            # one native user-context record before the typed turn. This exact
            # pair is not a skill selection. Other multi-kind records remain
            # unsupported rather than becoming an inferred no-selection.
            if kinds == ["agents_md.instructions", "environments.environment_context"]:
                blocks = payload.get("content")
                if (
                    turn_id in combined_context_turns
                    or turn_id in prompt_turns
                    or not isinstance(blocks, list)
                    or len(blocks) != 2
                    or any(
                        not isinstance(block, dict)
                        or block.get("type") != "input_text"
                        or not isinstance(block.get("text"), str)
                        or not block["text"]
                        for block in blocks
                    )
                ):
                    return SkillEvidenceExtraction("unsupported", (), malformed)
                combined_context_turns.add(turn_id)
                continue
            if not isinstance(kinds, list) or len(kinds) != 1:
                return SkillEvidenceExtraction("unsupported", (), malformed)
            kind = kinds[0]
            if kind == "user.text":
                if turn_id in prompt_turns:
                    return SkillEvidenceExtraction("unsupported", (), malformed)
                prompt_turns[turn_id] = offset
                continue
            if kind == "environments.environment_context":
                continue
            if kind != "skills.selected_skill_instructions" or prompt_turns.get(turn_id, offset) >= offset:
                return SkillEvidenceExtraction("unsupported", (), malformed)
            blocks = payload.get("content")
            if not isinstance(blocks, list) or len(blocks) != 1 or not isinstance(blocks[0], dict):
                return SkillEvidenceExtraction("unsupported", (), malformed)
            text = blocks[0].get("text")
            if blocks[0].get("type") != "input_text" or not isinstance(text, str):
                return SkillEvidenceExtraction("unsupported", (), malformed)
            match = _SELECTION.fullmatch(text)
            if match is None or "<skill>" in match.group("body"):
                return SkillEvidenceExtraction("unsupported", (), malformed)
            alias, path = match.group("alias", "path")
            if (
                (turn_id, alias) in seen_selections
                or posixpath.normpath(path) != path
                or len(candidates.get(alias, ())) != 1
                or hashlib.sha256(path.encode()).hexdigest() != candidates[alias][0].location_sha256
            ):
                return SkillEvidenceExtraction("unsupported", (), malformed)
            time = _time(record, row)
            if time is None:
                return SkillEvidenceExtraction("unsupported", (), malformed)
            seen_selections.add((turn_id, alias))
            selected.append(
                SkillEvidence(
                    "invoked",
                    candidates[alias][0].scope,
                    alias,
                    offset,
                    f"skill-invoked:{candidates[alias][0].scope}:{alias}",
                    time,
                    location_sha256=hashlib.sha256(path.encode()).hexdigest(),
                )
            )
        # Without a typed user turn, an old or truncated transcript cannot
        # prove the absence of a selected-skill message (no zero denominator).
        if not prompt_turns or not combined_context_turns.issubset(prompt_turns):
            return SkillEvidenceExtraction("unsupported", (), malformed)
        return SkillEvidenceExtraction("supported", tuple((*evidence, *selected)), malformed)
