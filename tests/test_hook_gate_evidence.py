# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Recorded Claude Code sessions with gated agent hooks in settings.json."""

from __future__ import annotations

from pathlib import Path

from services.session_parsers.hook_evidence import extract_hook_evidence, hook_binding_sha256

FIXTURES = Path(__file__).parent / "fixtures" / "component_insights" / "claude_code"
PREFIX = (
    "PYTHONPATH=/home/fixture/observal-src /home/fixture/.local/share/observal/bin/python3 "
    "-P -m observal_cli.hook_gate --agent probe-agent --command "
)


def _gated(event: str, script: str) -> str:
    return hook_binding_sha256(event, f"{PREFIX}.claude/hooks/{script}")


def _facts(name: str):
    lines = (FIXTURES / name).read_text(encoding="utf-8").splitlines()
    rows = [{"is_source_record": 1, "line_offset": i, "raw_line": line} for i, line in enumerate(lines)]
    extraction = extract_hook_evidence("claude-code", rows)
    return [(e.kind, e.binding_sha256) for e in extraction.evidence], extraction.session


def test_headless_agent_session_records_runs_under_the_exact_gated_command():
    facts, session = _facts("gate_session_headless_agent.jsonl")
    assert facts == [
        ("ran_with_output", _gated("UserPromptSubmit", "probe-prompt.sh")),
        ("blocked", _gated("PreToolUse", "probe-block.sh")),
        ("failed", _gated("PostToolUse", "probe-fail.sh")),
    ]
    assert (session.headless, session.agents) == (True, frozenset({"probe-agent"}))


def test_other_agent_and_plain_sessions_record_nothing():
    for name in (
        "gate_session_headless_other_agent.jsonl",
        "gate_session_headless_plain.jsonl",
        "gate_session_headless_subagent_main.jsonl",
    ):
        assert _facts(name)[0] == [], name


def test_a_subagent_run_is_recorded_in_the_subagent_transcript():
    facts, _ = _facts("gate_session_headless_subagent.jsonl")
    assert facts == [("failed", _gated("PostToolUse", "probe-fail.sh"))]
