# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Pi skill evidence extraction, checked against the recorded Pi 0.99.2 sessions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from services.session_parsers.skill_evidence import extract_skill_evidence

FIXTURES = Path(__file__).parent / "fixtures" / "component_insights" / "pi"


def _rows(lines: list[str]) -> list[dict]:
    return [{"is_source_record": 1, "line_offset": index, "raw_line": line} for index, line in enumerate(lines)]


def _fixture(name: str) -> list[str]:
    return (FIXTURES / name).read_text(encoding="utf-8").splitlines()


def _facts(lines: list[str]) -> list[tuple]:
    extraction = extract_skill_evidence("pi", _rows(lines))
    assert extraction.status == "supported"
    return [(e.kind, e.scope, e.alias, e.source_line_offset, e.result_state) for e in extraction.evidence]


def _keys(lines: list[str]) -> list[tuple[int, str]]:
    return [(e.source_line_offset, e.source_block_key) for e in extract_skill_evidence("pi", _rows(lines)).evidence]


def test_model_chosen_load_is_available_then_loaded():
    assert _facts(_fixture("skill_session_model_read.jsonl")) == [
        ("available", "user", "observal-probe", 3, "unknown"),
        ("load", "user", "observal-probe", 5, "success"),
    ]


def test_slash_command_counts_only_the_models_read_never_an_invocation():
    """Pi stores /skill:name as an ordinary user message, so it is not invocation evidence."""
    assert _facts(_fixture("skill_session_slash_command.jsonl")) == [
        ("available", "user", "observal-probe", 3, "unknown"),
        ("load", "user", "observal-probe", 5, "success"),
    ]


def test_literal_skill_block_typed_by_the_user_is_not_an_invocation():
    lines = _fixture("skill_session_model_read.jsonl")

    def literal(record):
        record["message"]["content"] = [
            {
                "type": "text",
                "text": f'Please document this literal example: <skill name="observal-probe" location="{PROBE}">'
                "not actually invoked</skill>",
            }
        ]

    facts = _facts(_edit(lines, 4, literal))
    assert [fact for fact in facts if fact[0] == "invoked" or fact[3] == 4] == []


def test_every_fact_carries_the_digest_of_the_exact_recorded_location():
    """Same layout and alias elsewhere is a different location, so the matcher can refuse it."""
    lines = _fixture("skill_session_model_read.jsonl")
    digest = hashlib.sha256(PROBE.encode()).hexdigest()
    facts = extract_skill_evidence("pi", _rows(lines)).evidence
    assert {fact.location_sha256 for fact in facts} == {digest}

    foreign = "/tmp/another-user/.pi/agent/skills/observal-probe/SKILL.md"
    moved = [line.replace(PROBE, foreign) for line in lines]
    facts = extract_skill_evidence("pi", _rows(moved)).evidence
    assert [(f.kind, f.alias) for f in facts] == [("available", "observal-probe"), ("load", "observal-probe")]
    assert {fact.location_sha256 for fact in facts} == {hashlib.sha256(foreign.encode()).hexdigest()} != {digest}


def test_extractor_digest_equals_the_verifiers_digest_for_the_same_install(monkeypatch):
    """The CLI verifier and the extractor hash the same absolute path for an active user skill."""
    from observal_cli.layer import skill_location_sha256

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/home/fixture")))
    facts = extract_skill_evidence("pi", _rows(_fixture("skill_session_model_read.jsonl"))).evidence
    assert facts[0].location_sha256 == skill_location_sha256("pi", "user", None, "observal-probe")
    assert skill_location_sha256("pi", "user", None, "observal-probe") != skill_location_sha256(
        "pi", "user", None, "second-probe"
    )


def test_facts_carry_no_paths_or_skill_text():
    extraction = extract_skill_evidence("pi", _rows(_fixture("skill_session_slash_command.jsonl")))
    rendered = repr(extraction)
    assert "/home/fixture" not in rendered and "PROBE-7F3A" not in rendered and "SKILL.md" not in rendered


def _edit(lines: list[str], index: int, edit) -> list[str]:
    record = json.loads(lines[index])
    edit(record)
    return [*lines[:index], json.dumps(record), *lines[index + 1 :]]


def test_a_read_of_a_path_that_was_not_advertised_is_not_a_load():
    lines = _fixture("skill_session_model_read.jsonl")

    def elsewhere(record):
        record["message"]["content"][0]["arguments"]["path"] = "/home/fixture/notes/SKILL.md"

    assert [f[0] for f in _facts(_edit(lines, 5, elsewhere))] == ["available"]


def test_skills_outside_pi_install_layout_yield_no_evidence():
    """A ~/.agents/skills copy is not an Observal Pi install: fail closed."""
    lines = _fixture("skill_session_model_read.jsonl")
    agents_copy = "/home/fixture/.agents/skills/observal-probe/SKILL.md"

    def advertise_agents_copy(record):
        sections = record["message"]["sections"]
        sections["skills"] = sections["skills"].replace(
            "/home/fixture/.pi/agent/skills/observal-probe/SKILL.md", agents_copy
        )

    def read_agents_copy(record):
        record["message"]["content"][0]["arguments"]["path"] = agents_copy

    assert _facts(_edit(_edit(lines, 3, advertise_agents_copy), 5, read_agents_copy)) == []


def test_project_scope_install_is_recognised_under_the_session_cwd():
    lines = _fixture("skill_session_model_read.jsonl")
    project_copy = "/home/fixture/project/.pi/skills/observal-probe/SKILL.md"

    def advertise(record):
        sections = record["message"]["sections"]
        sections["skills"] = sections["skills"].replace(
            "/home/fixture/.pi/agent/skills/observal-probe/SKILL.md", project_copy
        )

    def read(record):
        record["message"]["content"][0]["arguments"]["path"] = project_copy

    assert _facts(_edit(_edit(lines, 3, advertise), 5, read)) == [
        ("available", "project", "observal-probe", 3, "unknown"),
        ("load", "project", "observal-probe", 5, "success"),
    ]


def test_a_failed_read_and_an_unlinked_read_are_reported_as_such():
    lines = _fixture("skill_session_model_read.jsonl")

    def fail(record):
        record["message"]["isError"] = True

    assert _facts(_edit(lines, 6, fail))[-1][-1] == "error"

    def unlink(record):
        record["message"]["toolCallId"] = "some-other-call"

    assert _facts(_edit(lines, 6, unlink))[-1][-1] == "unknown"


def test_harness_without_an_extractor_is_unsupported_not_empty():
    from observal_shared.harness_registry import HARNESS_REGISTRY

    unsupported = next(
        name for name, entry in sorted(HARNESS_REGISTRY.items()) if not entry.get("skill_evidence_extractor")
    )
    assert extract_skill_evidence(unsupported, []).status == "unsupported"


def test_malformed_records_are_counted_not_guessed():
    extraction = extract_skill_evidence("pi", _rows(["not json", *_fixture("skill_session_model_read.jsonl")]))
    assert extraction.malformed_source_records == 1
    assert [e.kind for e in extraction.evidence] == ["available", "load"]


PROBE = "/home/fixture/.pi/agent/skills/observal-probe/SKILL.md"
SECOND = "/home/fixture/.pi/agent/skills/second-probe/SKILL.md"
_SECOND_SKILL = (
    "\n  <skill>\n    <name>second-probe</name>\n    <description>A second synthetic skill.</description>"
    f"\n    <location>{SECOND}</location>\n  </skill>"
)


def _advertise_second(record):
    sections = record["message"]["sections"]
    sections["skills"] = sections["skills"].replace("</available_skills>", _SECOND_SKILL + "\n</available_skills>")


def test_two_advertised_skills_have_distinct_keys_on_one_line():
    lines = _edit(_fixture("skill_session_model_read.jsonl"), 3, _advertise_second)
    facts = [f for f in _facts(lines) if f[0] == "available"]
    assert [(f[2], f[3]) for f in facts] == [("observal-probe", 3), ("second-probe", 3)]
    keys = _keys(lines)
    assert len(keys) == len(set(keys)), "every fact has a unique (line, block key)"
    assert all(key.startswith("skill-") for _, key in keys), "namespaced away from MCP block keys"


def test_a_reload_does_not_erase_or_reattribute_an_earlier_load():
    """The install a read refers to is bound when the read happens."""
    lines = _fixture("skill_session_model_read.jsonl")
    reload = json.loads(lines[3])

    def only_second(record):
        sections = record["message"]["sections"]
        sections["skills"] = sections["skills"].replace(PROBE, SECOND).replace("observal-probe", "second-probe")

    reload_line = json.dumps(reload)
    later = _edit([*lines, reload_line], len(lines), only_second)
    loads = [f for f in _facts(later) if f[0] == "load"]
    assert loads == [("load", "user", "observal-probe", 5, "success")]


def test_only_a_successful_linked_read_is_a_confirmed_load():
    lines = _fixture("skill_session_model_read.jsonl")
    extraction = extract_skill_evidence("pi", _rows(lines))
    (load,) = [e for e in extraction.evidence if e.kind == "load"]
    assert load.result_state == "success" and load.tool_use_id, "confirmed, with its call id"

    def fail(record):
        record["message"]["isError"] = True

    (attempt,) = [e for e in extract_skill_evidence("pi", _rows(_edit(lines, 6, fail))).evidence if e.kind == "load"]
    assert attempt.result_state == "error", "a failed read is an attempt, not a load"
