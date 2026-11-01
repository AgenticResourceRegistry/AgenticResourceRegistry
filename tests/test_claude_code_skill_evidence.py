# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Claude Code skill evidence, checked against the recorded Claude Code 2.1.286 sessions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from services.session_parsers.skill_evidence import extract_skill_evidence, kind_recorded

FIXTURES = Path(__file__).parent / "fixtures" / "component_insights" / "claude_code"
USER_SKILL = "/home/fixture/.claude/skills/observal-probe/SKILL.md"
PROJECT_SKILL = "/home/fixture/project/.claude/skills/project-probe/SKILL.md"


def _lines(name: str) -> list[str]:
    return (FIXTURES / name).read_text(encoding="utf-8").splitlines()


def _rows(lines: list[str]) -> list[dict]:
    return [{"is_source_record": 1, "line_offset": index, "raw_line": line} for index, line in enumerate(lines)]


def _evidence(lines: list[str]):
    extraction = extract_skill_evidence("claude-code", _rows(lines))
    assert extraction.status == "supported"
    return extraction.evidence


def _facts(lines: list[str]) -> list[tuple]:
    return [(e.kind, e.scope, e.alias, e.result_state, e.source_block_key) for e in _evidence(lines)]


def _sha(path: str) -> str:
    return hashlib.sha256(path.encode()).hexdigest()


def test_model_chosen_skill_is_a_confirmed_load_of_the_named_file():
    (fact,) = _evidence(_lines("skill_session_model_read.jsonl"))
    assert (fact.kind, fact.scope, fact.alias, fact.result_state) == ("load", "user", "observal-probe", "success")
    assert fact.location_sha256 == _sha(USER_SKILL) and fact.tool_use_id.startswith("toolu_fixture_")


def test_slash_command_is_an_invocation_with_or_without_arguments():
    for name in ("skill_session_slash_command.jsonl", "skill_session_slash_args.jsonl"):
        (fact,) = _evidence(_lines(name))
        assert (fact.kind, fact.scope, fact.alias, fact.source_block_key) == (
            "invoked",
            "user",
            "observal-probe",
            "skill-invoked:0",
        ), name
        assert fact.location_sha256 == _sha(USER_SKILL)


def test_command_tags_typed_by_the_user_are_not_an_invocation():
    assert _facts(_lines("skill_session_literal_text.jsonl")) == []


def test_pasted_expansion_text_without_the_harness_flag_is_not_an_invocation():
    lines = _lines("skill_session_slash_command.jsonl")
    forged = []
    for line in lines:
        record = json.loads(line)
        record.pop("isMeta", None)  # what a user could type: same text, no harness flag
        forged.append(json.dumps(record))
    assert _facts(forged) == []


def test_an_expansion_without_its_matching_command_is_not_an_invocation():
    lines = _lines("skill_session_slash_command.jsonl")
    edited = []
    for line in lines:
        record = json.loads(line)
        if (
            isinstance(record.get("message", {}).get("content"), str)
            and "<command-name>" in record["message"]["content"]
        ):
            record["message"]["content"] = record["message"]["content"].replace("observal-probe", "other-probe")
        edited.append(json.dumps(record))
    assert _facts(edited) == []


def test_project_skill_is_bound_to_the_session_cwd():
    (fact,) = _evidence(_lines("skill_session_project_skill.jsonl"))
    assert (fact.kind, fact.scope, fact.alias, fact.result_state) == ("load", "project", "project-probe", "success")
    assert fact.location_sha256 == _sha(PROJECT_SKILL)


def test_an_unknown_skill_names_no_file_and_yields_no_fact():
    assert _facts(_lines("skill_session_unknown_skill.jsonl")) == []


def test_a_failed_result_with_an_expansion_is_an_attempt_not_a_load():
    lines = _lines("skill_session_model_read.jsonl")
    edited = []
    for line in lines:
        record = json.loads(line)
        for block in record.get("message", {}).get("content", []) if isinstance(record.get("message"), dict) else []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                block["is_error"] = True
        edited.append(json.dumps(record))
    assert [(f.kind, f.result_state) for f in _evidence(edited)] == [("load", "error")]


def test_a_foreign_or_plugin_location_is_a_different_digest_or_no_fact():
    lines = _lines("skill_session_model_read.jsonl")
    foreign = [line.replace("/home/fixture/.claude/", "/tmp/another-user/.claude/") for line in lines]
    (fact,) = _evidence(foreign)
    assert fact.location_sha256 == _sha("/tmp/another-user/.claude/skills/observal-probe/SKILL.md") != _sha(USER_SKILL)
    plugin = [
        line.replace("/home/fixture/.claude/skills/", "/home/fixture/.claude/plugins/x/skills/") for line in lines
    ]
    assert _facts(plugin) == []


def test_skill_listing_is_never_reported_as_availability():
    assert all(
        e.kind != "available" for name in FIXTURES.glob("skill_session_*.jsonl") for e in _evidence(_lines(name.name))
    )
    assert not kind_recorded("claude-code", "available")
    assert kind_recorded("claude-code", "invoked") and kind_recorded("claude-code", "load")
    assert kind_recorded("pi", "available") and not kind_recorded("pi", "invoked")


def test_facts_carry_no_paths_or_skill_text():
    rendered = repr(_evidence(_lines("skill_session_slash_args.jsonl")))
    assert "/home/fixture" not in rendered and "PROBE-7F3A" not in rendered and "extra words" not in rendered
