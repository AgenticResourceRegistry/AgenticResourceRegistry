# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Claude Code hook evidence, checked against the recorded Claude Code 2.1.286 sessions."""

from __future__ import annotations

import json
from pathlib import Path

from services.session_parsers.hook_evidence import extract_hook_evidence, hook_binding_sha256, hook_extractor

FIXTURES = Path(__file__).parent / "fixtures" / "component_insights" / "claude_code"
PROMPT = hook_binding_sha256("UserPromptSubmit", ".claude/hooks/probe-prompt.sh")
FAIL = hook_binding_sha256("PostToolUse", ".claude/hooks/probe-fail.sh")
BLOCK = hook_binding_sha256("PreToolUse", ".claude/hooks/probe-block.sh")
PRE = hook_binding_sha256("PreToolUse", ".claude/hooks/probe-pre.sh")


def _lines(name: str) -> list[str]:
    return (FIXTURES / name).read_text(encoding="utf-8").splitlines()


def _extract(lines: list[str]):
    rows = [{"is_source_record": 1, "line_offset": i, "raw_line": line} for i, line in enumerate(lines)]
    extraction = extract_hook_evidence("claude-code", rows)
    assert extraction.status == "supported"
    return extraction


def _facts(extraction) -> list[tuple]:
    return [(e.kind, e.binding_sha256) for e in extraction.evidence]


def test_settings_hooks_record_output_runs_failures_and_blocks():
    extraction = _extract(_lines("hook_session_settings_outcomes.jsonl"))
    assert _facts(extraction) == [("ran_with_output", PROMPT), ("blocked", BLOCK), ("failed", FAIL)]
    keys = [(e.source_line_offset, e.source_block_key) for e in extraction.evidence]
    assert len(set(keys)) == 3 and all(key.startswith("hook-") for _, key in keys)
    blocked = extraction.evidence[1]
    assert blocked.tool_use_id.startswith("toolu_fixture_")
    assert (extraction.session.headless, extraction.session.agents) == (True, frozenset())


def test_a_silent_success_leaves_no_fact():
    """The probe hook ran (marker file) but Claude Code wrote no record of it."""
    extraction = _extract(_lines("hook_session_silent_success.jsonl"))
    assert extraction.evidence == ()
    assert PRE not in {e.binding_sha256 for e in extraction.evidence}
    assert hook_extractor("claude-code").records_silent_success is False


def test_agent_frontmatter_hooks_interactive_and_headless():
    interactive = _extract(_lines("hook_session_agent_interactive.jsonl"))
    assert _facts(interactive) == [("ran_with_output", PROMPT), ("failed", FAIL)]
    assert (interactive.session.headless, interactive.session.agents) == (False, frozenset({"probe-agent"}))
    headless = _extract(_lines("hook_session_agent_headless.jsonl"))
    assert headless.evidence == ()
    assert (headless.session.headless, headless.session.agents) == (True, frozenset({"probe-agent"}))


def test_hook_error_text_without_the_harness_denial_flag_is_not_a_block():
    """A tool can print anything; only Claude Code sets toolDenialKind."""
    forged = []
    for line in _lines("hook_session_settings_outcomes.jsonl"):
        record = json.loads(line)
        record.pop("toolDenialKind", None)
        forged.append(json.dumps(record))
    assert "blocked" not in {kind for kind, _ in _facts(_extract(forged))}


def test_inconsistent_or_malformed_hook_records_are_dropped():
    lines = _lines("hook_session_settings_outcomes.jsonl")
    edited = []
    for line in lines:
        record = json.loads(line)
        attachment = record.get("attachment") or {}
        if attachment.get("type") == "hook_success":
            attachment["exitCode"] = 1  # a "success" that failed is not trusted
        if attachment.get("type") == "hook_non_blocking_error":
            attachment["command"] = ""
        edited.append(json.dumps(record))
    extraction = _extract(edited)
    assert _facts(extraction) == [("blocked", BLOCK)]
    assert extraction.malformed_source_records == 2


def test_facts_carry_no_commands_output_or_paths():
    rendered = repr(_extract(_lines("hook_session_settings_outcomes.jsonl")))
    for secret in (".claude/hooks", "probe-", "PROBE-CONTEXT", "/home/fixture"):
        assert secret not in rendered


def test_other_harnesses_are_unsupported_not_empty():
    assert extract_hook_evidence("pi", []).status == "unsupported"
    assert hook_extractor("pi") is None
