# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""When a gated agent hook could run: placement-aware eligibility and unknown subagent sessions."""

from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "component_insights" / "claude_code"


def _facts(name: str):
    from services.session_parsers.hook_evidence import extract_hook_evidence

    lines = (FIXTURES / name).read_text(encoding="utf-8").splitlines()
    rows = [{"is_source_record": 1, "line_offset": i, "raw_line": line} for i, line in enumerate(lines)]
    return extract_hook_evidence("claude-code", rows)


def test_only_a_subagents_own_transcript_is_marked_as_one():
    assert _facts("gate_session_headless_subagent.jsonl").session.subagent is True
    for name in (
        "gate_session_headless_subagent_main.jsonl",
        "gate_session_headless_agent.jsonl",
        "hook_session_agent_interactive.jsonl",
    ):
        assert _facts(name).session.subagent is False, name


def _candidate(binding: str, placement: str, component_id: str = "hook-1") -> dict:
    return {
        "component_id": component_id,
        "component_version_id": "",
        "identity_status": "resolved",
        "verification_status": "verified",
        "location_sha256": binding,
        "binding_agent": "probe-agent",
        "binding_placement": placement,
    }


def _contexts(result) -> dict[str, str]:
    return {
        row["component_id"]: row["evidence_kind"].removeprefix("hook_context_")
        for row in result.rows
        if row["evidence_kind"].startswith("hook_context_")
    }


def _match(extraction, candidates):
    from services.component_activity.hook_matcher import match_hook_evidence

    layer_hash = "v2_" + "e" * 60
    offsets = {fact.source_line_offset: layer_hash for fact in extraction.evidence} | {0: layer_hash}
    return match_hook_evidence(extraction, {layer_hash: candidates}, offsets)


def test_a_gated_hook_could_run_in_a_headless_session_of_its_agent():
    from services.session_parsers.hook_evidence import HookEvidenceExtraction, HookSession, hook_binding_sha256

    binding = hook_binding_sha256("PreToolUse", "x")
    headless = HookEvidenceExtraction("supported", (), HookSession(True, frozenset({"probe-agent"})))
    assert _contexts(_match(headless, [_candidate(binding, "gated_settings")])) == {"hook-1": "eligible"}
    assert _contexts(_match(headless, [_candidate(binding, "frontmatter")])) == {"hook-1": "headless"}
    unknown_mode = HookEvidenceExtraction("supported", (), HookSession(None, frozenset({"probe-agent"})))
    assert _contexts(_match(unknown_mode, [_candidate(binding, "gated_settings")])) == {"hook-1": "eligible"}
    plain = HookEvidenceExtraction("supported", (), HookSession(True, frozenset()))
    assert _contexts(_match(plain, [_candidate(binding, "gated_settings")])) == {"hook-1": "agent_inactive"}
    other = HookEvidenceExtraction("supported", (), HookSession(False, frozenset({"other-agent"})))
    assert _contexts(_match(other, [_candidate(binding, "gated_settings")])) == {"hook-1": "agent_inactive"}


def test_recorded_gated_runs_attribute_and_an_unidentified_subagent_is_unknown_not_inactive():
    """Real recordings: the gated failure runs in the headless agent and in its subagent transcript."""
    from services.session_parsers.hook_evidence import hook_binding_sha256

    prefix = (
        "PYTHONPATH=/home/fixture/observal-src /home/fixture/.local/share/observal/bin/python3 "
        "-P -m observal_cli.hook_gate --agent probe-agent --command "
    )
    fail_hook = _candidate(hook_binding_sha256("PostToolUse", f"{prefix}.claude/hooks/probe-fail.sh"), "gated_settings")
    pre_hook = _candidate(
        hook_binding_sha256("PreToolUse", f"{prefix}.claude/hooks/probe-pre.sh"), "gated_settings", "hook-2"
    )
    agent = _match(_facts("gate_session_headless_agent.jsonl"), [fail_hook, pre_hook])
    assert _contexts(agent) == {"hook-1": "eligible", "hook-2": "eligible"}
    assert [row["evidence_kind"] for row in agent.rows if row["component_id"] == "hook-1"].count("hook_failed") == 1
    subagent = _match(_facts("gate_session_headless_subagent.jsonl"), [fail_hook, pre_hook])
    assert _contexts(subagent) == {"hook-1": "eligible", "hook-2": "agent_unknown"}
    main = _match(_facts("gate_session_headless_subagent_main.jsonl"), [fail_hook, pre_hook])
    assert _contexts(main) == {"hook-1": "agent_inactive", "hook-2": "agent_inactive"}


def test_unknown_subagent_sessions_are_excluded_from_the_denominator_and_explained():
    from services.component_activity.hook_queries import build_hook_coverage

    coverage = build_hook_coverage(
        {"present_sessions": 3},
        {"eligible_sessions": 1, "agent_unknown_sessions": 2, "observed_sessions": 1},
        1,
    )
    assert coverage.eligibility.agent_unknown_sessions == 2
    assert coverage.usage_rate_denominator_sessions == 1
    assert "subagent_agent_unknown" in coverage.reasons


def test_the_report_explains_unknown_subagent_sessions():
    from services.insights.component_report import generate_hook_sections

    summary = {
        "present_sessions": 3,
        "eligible_sessions": 1,
        "sessions_with_recorded_run": 1,
        "runs_with_output": 0,
        "failures": 1,
        "blocks": 0,
    }
    eligibility = {
        "eligible_sessions": 1,
        "headless_sessions": 0,
        "agent_inactive_sessions": 0,
        "mode_unknown_sessions": 0,
        "agent_unknown_sessions": 2,
    }
    coverage = {
        "attribution_state": "observed",
        "eligibility": eligibility,
        "reasons": ["subagent_agent_unknown"],
        "limitations": [],
    }
    text = generate_hook_sections(summary, coverage)["summary"]
    assert "In 2 subagent sessions neither the subagent nor its parent session recorded which agent ran" in text
    legacy = {**coverage, "eligibility": {k: v for k, v in eligibility.items() if k != "agent_unknown_sessions"}}
    assert "subagent" not in generate_hook_sections(summary, legacy)["summary"], "older stored coverage still renders"
