# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Skill component reports are deterministic and never borrow MCP semantics."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.component_activity import skill_queries
from services.component_activity.skill_queries import build_skill_coverage
from services.insights import component_report


def _summary(observed: int, complete: int) -> dict:
    coverage = build_skill_coverage(
        {"present_sessions": 2, "present_users": 1},
        {"projection_complete_sessions": complete, "observed_sessions": observed, "supported_present_sessions": 2},
        515,
    )
    return {
        "present_sessions": 2,
        "present_users": 1,
        "available_sessions": 2,
        "loaded_sessions": observed,
        "confirmed_loads": observed,
        "load_attempts": 1,
        "invoked_sessions": 0,
        "invocations": 0,
        "harness_distribution": {"pi": 2},
        "version_distribution": {},
        "activation_actions": {"context_sessions": 0, "next_session_sessions": 0, "scope": "component"},
        "coverage": coverage,
    }


def _report() -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        subject_type="component",
        component_type="skill",
        project_id="default",
        component_id=uuid.uuid4(),
        component_version_id=None,
        component_version=None,
        period_start=now - timedelta(days=14),
        period_end=now,
        triggered_by=uuid.uuid4(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("observed", "complete", "phrase"),
    [
        (1, 2, "entered context"),
        (0, 2, "does not prove the skill was never used"),
        (0, 0, "no use conclusion can be drawn"),
    ],
)
async def test_skill_report_states_what_the_evidence_does_and_does_not_show(monkeypatch, observed, complete, phrase):
    monkeypatch.setattr(skill_queries, "skill_activity_summary", AsyncMock(return_value=_summary(observed, complete)))
    content = await component_report.generate_component_content(_report())
    assert phrase in content["narrative"]["summary"]
    metrics = content["metrics"]
    assert (metrics["confirmed_loads"], metrics["load_attempts"]) == (observed, 1)
    for mcp_only in ("observed_calls", "result_states", "cohort_collision_calls"):
        assert mcp_only not in metrics
    assert "component_analysis" not in content["narrative"], "no model interpretation for skills"
    assert any(item.startswith("entered_context_not_helped") for item in content["coverage"]["limitations"])
    assert "calls" not in content["coverage"]


@pytest.mark.asyncio
async def test_unsupported_component_types_are_rejected():
    report = _report()
    report.component_type = "prompt"
    with pytest.raises(ValueError, match="Unsupported report subject"):
        await component_report.generate_component_content(report)


def _hook_summary(aggregate: dict) -> dict:
    from services.component_activity.hook_queries import build_hook_coverage

    coverage = build_hook_coverage({"present_sessions": 3, "present_users": 1}, aggregate, 515)
    return {
        "present_sessions": 3,
        "present_users": 1,
        "eligible_sessions": coverage.eligibility.eligible_sessions,
        "sessions_with_recorded_run": coverage.observed_sessions,
        "runs_with_output": 1,
        "silent_runs": None,
        "failures": 1,
        "blocks": 0,
        "harness_distribution": {"claude-code": 3},
        "version_distribution": {},
        "activation_actions": {"context_sessions": 0, "next_session_sessions": 0, "scope": "component"},
        "coverage": coverage,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("aggregate", "phrases"),
    [
        ({"eligible_sessions": 2, "observed_sessions": 1}, ["recorded runs", "not what it changed"]),
        ({"eligible_sessions": 2}, ["does not show the hook never ran"]),
        ({"headless_sessions": 3}, ["no conclusion can be drawn", "ran headless"]),
    ],
)
async def test_hook_report_states_what_runs_do_and_do_not_show(monkeypatch, aggregate, phrases):
    from services.component_activity import hook_queries

    monkeypatch.setattr(hook_queries, "hook_activity_summary", AsyncMock(return_value=_hook_summary(aggregate)))
    report = _report()
    report.component_type = "hook"
    content = await component_report.generate_component_content(report)
    for phrase in phrases:
        assert phrase in content["narrative"]["summary"]
    for other in ("observed_calls", "loaded_sessions", "invocations"):
        assert other not in content["metrics"]
    assert "component_analysis" not in content["narrative"]
    assert any(item.startswith("effect_not_observed") for item in content["coverage"]["limitations"])
