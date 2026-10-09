# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Hook evidence from an untested Claude Code version is unknown, never a measured zero.

Claude Code ships several releases a week and the gated-hook opt-in no longer
refuses untested versions. Instead, a session written by a version outside the
registry's ``hook_evidence_tested_versions`` is ``version_unverified``: excluded
from the denominator, with any run it did record still counted.
"""

from __future__ import annotations

import json
from pathlib import Path

from observal_shared.harness_registry import HARNESS_REGISTRY
from services.component_activity.hook_matcher import match_hook_evidence
from services.component_activity.hook_queries import _HOOK_SESSIONS, build_hook_coverage
from services.insights.component_report import generate_hook_sections
from services.session_parsers.hook_evidence import (
    HookEvidence,
    HookEvidenceExtraction,
    HookSession,
    extract_hook_evidence,
    hook_binding_sha256,
)

FIXTURES = Path(__file__).parent / "fixtures" / "component_insights" / "claude_code"
HASH = "v2_" + "e" * 60
BINDING = hook_binding_sha256("PostToolUse", ".claude/hooks/probe-fail.sh")


def _rows(lines: list[str]) -> list[dict]:
    rows, offset = [], 0
    for line in lines:
        rows.append({"is_source_record": 1, "line_offset": offset, "raw_line": line})
        offset += len(line.encode()) + 1
    return rows


def _session(version: str | None) -> list[str]:
    """A real Claude Code 2.1.286 recording, its version rewritten (or removed)."""
    out = []
    for line in (FIXTURES / "hook_session_settings_outcomes.jsonl").read_text().splitlines():
        record = json.loads(line)
        if "version" in record:
            if version is None:
                del record["version"]
            else:
                record["version"] = version
        out.append(json.dumps(record))
    return out


def _facts(extraction) -> list[tuple]:
    return [(fact.kind, fact.binding_sha256, fact.tool_use_id) for fact in extraction.evidence]


def test_the_tested_range_is_shared_by_the_cli_and_the_server():
    from observal_cli import agent_hooks

    tested = HARNESS_REGISTRY["claude-code"]["hook_evidence_tested_versions"]
    assert (agent_hooks.TESTED_CLAUDE_CODE_MIN, agent_hooks.TESTED_CLAUDE_CODE_MAX) == tested == ((2, 1, 286),) * 2


def test_recorded_version_decides_whether_the_session_is_verified():
    recorded = extract_hook_evidence("claude-code", _rows(_session("2.1.286")))
    assert recorded.session.harness_version_unverified is False
    for version in ("2.1.287", "2.2.0", "2.1.285", "not-a-version", None):
        extraction = extract_hook_evidence("claude-code", _rows(_session(version)))
        assert extraction.session.harness_version_unverified is True, version
        # The evidence itself is still read: a run whose record matched is real.
        assert _facts(extraction) == _facts(recorded) != []


def test_mixed_versions_in_one_session_are_unverified():
    lines = _session("2.1.286")
    record = json.loads(lines[-1])
    record["version"] = "2.1.300"
    lines[-1] = json.dumps(record)
    assert extract_hook_evidence("claude-code", _rows(lines)).session.harness_version_unverified is True


def _hook(**overrides) -> dict:
    return {
        "component_id": "hook-1",
        "component_version_id": "",
        "identity_status": "resolved",
        "verification_status": "verified",
        "location_sha256": BINDING,
        "binding_agent": "",
    } | overrides


def _contexts(facts, **session) -> dict[str, str]:
    extraction = HookEvidenceExtraction("supported", tuple(facts), HookSession(**session))
    result = match_hook_evidence(extraction, {HASH: [_hook()]}, {0: HASH, 5: HASH})
    return {row["component_id"]: row["evidence_kind"] for row in result.rows if "context" in row["evidence_kind"]}


def test_without_a_recorded_run_the_hook_is_version_unverified_not_eligible():
    assert _contexts([], harness_version_unverified=True) == {"hook-1": "hook_context_version_unverified"}
    assert _contexts([]) == {"hook-1": "hook_context_eligible"}


def test_a_recorded_run_still_counts_on_an_unverified_version():
    run = HookEvidence("failed", BINDING, 5, "hook-run:0", None)
    assert _contexts([run], harness_version_unverified=True) == {"hook-1": "hook_context_eligible"}


def test_version_unverified_is_excluded_from_the_denominator_and_explained():
    assert "e.ctx_version_unverified > 0, 'version_unverified'" in _HOOK_SESSIONS
    # Recorded runs win over every exclusion; version_unverified is checked before headless/agent states.
    assert (
        _HOOK_SESSIONS.index("'eligible'")
        < _HOOK_SESSIONS.index("'version_unverified'")
        < _HOOK_SESSIONS.index("'headless'")
    )
    aggregate = {"projection_complete_sessions": 3, "eligible_sessions": 1, "version_unverified_sessions": 2}
    coverage = build_hook_coverage({"present_sessions": 3, "present_users": 1}, aggregate, 1)
    assert coverage.usage_rate_denominator_sessions == 1
    assert coverage.eligibility.version_unverified_sessions == 2
    assert "harness_version_unverified" in coverage.reasons
    summary = {
        "present_sessions": 3,
        "eligible_sessions": 1,
        "sessions_with_recorded_run": 0,
        "runs_with_output": 0,
        "silent_runs": None,
        "failures": 0,
        "blocks": 0,
    }
    text = generate_hook_sections(summary, coverage.model_dump(mode="json"))["summary"]
    assert "2 sessions were recorded by a harness version whose hook records have not been verified yet" in text
