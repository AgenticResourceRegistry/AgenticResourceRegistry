# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""A component report carries outcomes, attention items and a model reading beside its unchanged counts."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.component_activity import hook_queries, skill_queries
from services.component_activity.hook_queries import build_hook_coverage
from services.component_activity.skill_queries import build_skill_coverage
from services.insights import component_report

OUTCOMES = {"version": 1, "state": "insufficient", "gap": {"state": "insufficient"}}
ANALYSIS = {"version": 1, "state": "assessed", "suggestions": []}


def _report(kind):
    now = datetime.now(UTC)
    return SimpleNamespace(
        subject_type="component",
        component_type=kind,
        component_name="user/thing",
        project_id="default",
        component_id=uuid.uuid4(),
        component_version_id=None,
        component_version=None,
        period_start=now - timedelta(days=14),
        period_end=now,
        triggered_by=uuid.uuid4(),
    )


@pytest.fixture
def interpretation(monkeypatch):
    outcomes = AsyncMock(return_value=(OUTCOMES, {"friction_notes": []}))
    analysis = AsyncMock(return_value=ANALYSIS)
    monkeypatch.setattr(component_report, "component_outcomes", outcomes)
    monkeypatch.setattr(component_report, "component_analysis", analysis)
    return SimpleNamespace(outcomes=outcomes, analysis=analysis)


def _skill_summary():
    coverage = build_skill_coverage(
        {"present_sessions": 8, "present_users": 2},
        {"projection_complete_sessions": 8, "observed_sessions": 0, "supported_present_sessions": 8},
        515,
    )
    return {
        "present_sessions": 8,
        "present_users": 2,
        "available_sessions": 8,
        "loaded_sessions": 0,
        "confirmed_loads": 0,
        "load_attempts": 0,
        "invoked_sessions": 0,
        "invocations": 0,
        "harness_distribution": {"claude-code": 8},
        "version_distribution": {},
        "activation_actions": {},
        "coverage": coverage,
    }


def _hook_summary():
    coverage = build_hook_coverage(
        {"present_sessions": 9, "present_users": 1}, {"eligible_sessions": 9, "observed_sessions": 9}, 515
    )
    return {
        "present_sessions": 9,
        "present_users": 1,
        "eligible_sessions": coverage.eligibility.eligible_sessions,
        "sessions_with_recorded_run": 9,
        "runs_with_output": 1,
        "silent_runs": None,
        "failures": 9,
        "blocks": 0,
        "harness_distribution": {"claude-code": 9},
        "version_distribution": {},
        "activation_actions": {},
        "coverage": coverage,
    }


@pytest.mark.asyncio
async def test_a_skill_report_gets_outcomes_attention_and_analysis(monkeypatch, interpretation):
    monkeypatch.setattr(skill_queries, "skill_activity_summary", AsyncMock(return_value=_skill_summary()))
    db = object()
    content = await component_report.generate_component_content(_report("skill"), db)
    narrative = content["narrative"]
    assert narrative["outcomes"] == OUTCOMES and narrative["analysis"] == ANALYSIS
    assert [item["id"] for item in narrative["attention"]] == ["skill_never_loaded"]
    assert interpretation.outcomes.await_args.args[1] is db, "facets are read through the job's own session"
    args = interpretation.analysis.await_args.args
    assert args[1]["confirmed_loads"] == 0 and args[2] is narrative["attention"] and args[3] == OUTCOMES
    assert content["metrics"]["present_sessions"] == 8 and "summary" in narrative, "the counts are unchanged"


@pytest.mark.asyncio
async def test_a_hook_report_gets_the_hook_rules(monkeypatch, interpretation):
    monkeypatch.setattr(hook_queries, "hook_activity_summary", AsyncMock(return_value=_hook_summary()))
    content = await component_report.generate_component_content(_report("hook"), object())
    assert [item["id"] for item in content["narrative"]["attention"]] == ["hook_failing"]
    assert content["narrative"]["analysis"] == ANALYSIS


@pytest.mark.asyncio
async def test_an_mcp_report_keeps_its_call_findings_beside_the_new_sections(monkeypatch, interpretation):
    from schemas.component_activity import ActivityCoverage, CallCoverage, PresenceCoverage, ProjectionCoverage

    summary = {
        "present_sessions": 8,
        "present_users": 1,
        "observed_sessions": 6,
        "observed_calls": 10,
        "result_states": {"success": 5, "error": 4, "unknown": 1},
        "harness_distribution": {"claude-code": 8},
        "version_distribution": {},
        "activation_actions": {},
        "coverage": ActivityCoverage(
            presence=PresenceCoverage(present_sessions=8),
            projection=ProjectionCoverage(projection_complete_sessions=8),
            calls=CallCoverage(),
            attribution_state="observed",
        ),
    }
    monkeypatch.setattr(component_report, "activity_summary", AsyncMock(return_value=summary))
    findings = AsyncMock(return_value={"version": 4, "state": "unknown", "findings": []})
    monkeypatch.setattr("services.insights.component_evidence.component_findings", findings)
    content = await component_report.generate_component_content(_report("mcp"), object())
    narrative = content["narrative"]
    assert narrative["component_analysis"]["version"] == 4 and narrative["analysis"] == ANALYSIS
    assert [item["id"] for item in narrative["attention"]] == ["mcp_call_errors"]
    assert content["metrics"]["observed_calls"] == 10
