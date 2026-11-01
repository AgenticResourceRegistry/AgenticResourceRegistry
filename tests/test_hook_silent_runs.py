# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Silent hook runs count where the harness records them, and are unknown elsewhere.

Regression: Pi's extension recorded silent successes, but no evidence kind carried
them, so a hook that ran quietly in every session read as "no recorded runs".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

PERIOD = (datetime.now(UTC) - timedelta(days=7), datetime.now(UTC))
PRESENCE = {"present_sessions": 2, "present_users": 1}


async def _summary(monkeypatch, harnesses: dict[str, int], aggregate: dict) -> dict:
    from services.component_activity import hook_queries

    rows = {
        id(hook_queries._HOOK_SUMMARY): [aggregate],
        id(hook_queries._HARNESSES): [{"harness": h, "sessions": n} for h, n in harnesses.items()],
        id(hook_queries._ACTIVATIONS): [{}],
    }

    async def fake_rows(sql, _params):
        return rows[id(sql)]

    monkeypatch.setattr(hook_queries, "_rows", fake_rows)
    monkeypatch.setattr(hook_queries, "presence_coverage", AsyncMock(return_value=PRESENCE))
    monkeypatch.setattr(hook_queries, "presence_version_distribution", AsyncMock(return_value={}))
    return await hook_queries.hook_activity_summary("p", "00000000-0000-4000-8000-0000000000c1", None, PERIOD)


QUIET = {
    "projection_complete_sessions": 2,
    "supported_present_sessions": 2,
    "eligible_sessions": 2,
    "observed_sessions": 2,
    "total_silent_runs": 4,
}


@pytest.mark.asyncio
async def test_pi_counts_silent_runs_and_is_not_a_lower_bound(monkeypatch):
    summary = await _summary(monkeypatch, {"pi": 2}, QUIET)
    coverage = summary["coverage"]
    assert summary["silent_runs"] == 4
    assert summary["sessions_with_recorded_run"] == 2
    assert coverage.attribution_state == "observed"
    assert not any(item.startswith("silent_success_unrecorded") for item in coverage.limitations)
    assert "silent_runs_not_recorded_on_some_harnesses" not in coverage.reasons


@pytest.mark.asyncio
async def test_claude_code_silent_runs_are_unknown_never_zero(monkeypatch):
    aggregate = QUIET | {"observed_sessions": 0, "total_silent_runs": 0}
    summary = await _summary(monkeypatch, {"claude-code": 2}, aggregate)
    assert summary["silent_runs"] is None
    assert any(item.startswith("silent_success_unrecorded") for item in summary["coverage"].limitations)


@pytest.mark.asyncio
async def test_a_mixed_cohort_counts_partially_and_says_so(monkeypatch):
    summary = await _summary(monkeypatch, {"pi": 1, "claude-code": 1}, QUIET | {"total_silent_runs": 1})
    coverage = summary["coverage"]
    assert summary["silent_runs"] == 1
    assert "silent_runs_not_recorded_on_some_harnesses" in coverage.reasons
    assert any(item.startswith("silent_success_unrecorded") for item in coverage.limitations)


def test_sessions_count_silent_runs_toward_eligibility_and_observation():
    from services.component_activity import hook_queries

    assert "e.runs_with_output + e.silent_runs + e.failures + e.blocks > 0, 'eligible'" in hook_queries._HOOK_SESSIONS
    assert "runs_with_output + silent_runs + failures + blocks > 0)" in hook_queries._HOOK_SUMMARY


def _sections(silent_runs, reasons=()):
    from services.insights.component_report import generate_hook_sections

    summary = {
        "present_sessions": 2,
        "eligible_sessions": 2,
        "sessions_with_recorded_run": 0,
        "runs_with_output": 0,
        "silent_runs": silent_runs,
        "failures": 0,
        "blocks": 0,
    }
    eligibility = dict.fromkeys(
        ("eligible_sessions", "headless_sessions", "agent_inactive_sessions", "mode_unknown_sessions"), 0
    )
    coverage = {
        "attribution_state": "no_recorded_runs",
        "eligibility": eligibility,
        "reasons": list(reasons),
        "limitations": [],
    }
    return generate_hook_sections(summary, coverage)["summary"]


def test_no_recorded_runs_wording_depends_on_whether_silent_runs_are_recorded():
    assert "leaves no record" in _sections(None)
    assert "leaves no record" in _sections(0, ["silent_runs_not_recorded_on_some_harnesses"])
    recorded = _sections(0)
    assert "leaves no record" not in recorded
    assert "record silent runs too, and none was recorded" in recorded
