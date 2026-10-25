# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Hook and skill evidence read recorded Claude Code 2.1.310 sessions as they read 2.1.286 ones.

The fixtures are sanitized copies of real headless sessions (see the README beside them); the
expected facts are what the raw transcripts show.
"""

from __future__ import annotations

from pathlib import Path

from observal_shared.harness_registry import HARNESS_REGISTRY
from services.session_parsers.hook_evidence import extract_hook_evidence, hook_binding_sha256
from services.session_parsers.skill_evidence import extract_skill_evidence

FIXTURES = Path(__file__).parent / "fixtures" / "component_insights" / "claude_code"
FLAKY = hook_binding_sha256("PostToolUse", ".claude/hooks/flaky-lint-hook.sh")
SYNTAX = hook_binding_sha256("PostToolUse", ".claude/hooks/python-syntax-check.sh")
BLOCK = hook_binding_sha256("PreToolUse", ".claude/hooks/block-destructive-commands.sh")


def _rows(name: str) -> list[dict]:
    lines = (FIXTURES / name).read_text(encoding="utf-8").splitlines()
    return [{"is_source_record": 1, "line_offset": i, "raw_line": line} for i, line in enumerate(lines)]


def test_the_recorded_version_is_inside_the_tested_range():
    low, high = HARNESS_REGISTRY["claude-code"]["hook_evidence_tested_versions"]
    assert low <= (2, 1, 299) <= (2, 1, 310) <= high


def test_settings_hooks_record_output_and_failures_and_skip_observal_telemetry():
    extraction = extract_hook_evidence("claude-code", _rows("hook_session_standalone_output_2_1_310.jsonl"))
    assert extraction.status == "supported"
    # Two failed lint runs and one syntax check that printed output. The Stop session push is Observal's own.
    assert [(e.kind, e.binding_sha256) for e in extraction.evidence] == [
        ("failed", FLAKY),
        ("failed", FLAKY),
        ("ran_with_output", SYNTAX),
    ]
    assert extraction.session.harness_version_unverified is False
    assert extraction.session.headless is True


def test_a_blocked_tool_call_is_a_block():
    extraction = extract_hook_evidence("claude-code", _rows("hook_session_standalone_blocked_2_1_310.jsonl"))
    assert [(e.kind, e.binding_sha256) for e in extraction.evidence] == [("blocked", BLOCK)]
    assert extraction.evidence[0].tool_use_id.startswith("toolu_fixture_")
    assert extraction.session.harness_version_unverified is False


def test_a_skill_load_is_a_confirmed_load_of_the_project_skill():
    extraction = extract_skill_evidence("claude-code", _rows("skill_session_agent_load_2_1_310.jsonl"))
    assert extraction.status == "supported"
    assert [(e.kind, e.alias, e.scope, e.result_state) for e in extraction.evidence] == [
        ("load", "code-review-checklist", "project", "success")
    ]
