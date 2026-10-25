# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Outcome comparison for a component report: counts, the gap rule, and what is spent and kept."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.insights import component_outcomes as co
from services.insights.scope import SessionKey


def _facet(outcome="fully_achieved", satisfaction="satisfied", friction=()):
    return {
        "outcome": outcome,
        "user_satisfaction": satisfaction,
        "friction_points": [{"type": t, "severity": s, "description": d} for t, s, d in friction],
    }


def _side(achieved, not_achieved, analyzed=None, friction_types=()):
    return {
        "achieved": achieved,
        "not_achieved": not_achieved,
        "analyzed": analyzed if analyzed is not None else achieved + not_achieved,
        "friction_types": [list(item) for item in friction_types],
    }


def test_summarize_counts_outcomes_satisfaction_and_friction_once_per_session():
    facets = [
        _facet("fully_achieved", "happy"),
        _facet("mostly_achieved", "likely_satisfied"),
        _facet("partially_achieved", "dissatisfied", [("tool_failed", "major", "x"), ("tool_failed", "minor", "y")]),
        _facet("not_achieved", "frustrated", [("buggy_code", "blocking", "z")]),
        _facet("unclear", "unsure"),
    ]
    side = co.summarize_cohort(12, facets)
    assert (side["sessions"], side["analyzed"]) == (12, 5)
    assert (side["achieved"], side["not_achieved"], side["unclear"]) == (2, 2, 1)
    assert (side["satisfied"], side["dissatisfied"]) == (2, 2)
    assert (side["with_friction"], side["blocking_friction"], side["tool_failed"]) == (2, 1, 1)
    assert dict(side["friction_types"]) == {"tool_failed": 1, "buggy_code": 1}, "two tool_failed points are one session"


def test_summarize_tolerates_missing_and_malformed_fields():
    side = co.summarize_cohort(0, [{}, {"friction_points": ["junk", None, {"type": None}]}])
    assert side["analyzed"] == 2 and side["unclear"] == 2 and side["with_friction"] == 1
    assert side["friction_types"] == [["unknown", 1]]


@pytest.mark.parametrize(
    ("with_side", "without", "state"),
    [
        (_side(9, 0), _side(20, 10), "insufficient"),  # fewer than 10 known on one side
        (_side(12, 8), _side(13, 7), "no_difference"),  # 60% against 65%
        (_side(6, 14), _side(20, 10), "lower"),  # 30% against 67%
        (_side(18, 2), _side(10, 10), "higher"),  # 90% against 50%
        (_side(12, 8), _side(11, 9), "no_difference"),
        (_side(14, 6), _side(11, 9), "no_difference"),  # 70% against 55%: a 15 point gap that is within noise
    ],
)
def test_gap_needs_enough_sessions_a_real_gap_and_significance(with_side, without, state):
    assert co.outcome_gap(with_side, without)["state"] == state


def test_gap_reports_both_rates_and_their_difference():
    gap = co.outcome_gap(_side(6, 14), _side(20, 10))
    assert (gap["rate_with"], gap["rate_without"]) == (0.3, 0.667)
    assert gap["difference"] == -0.367


def test_all_sessions_achieved_on_both_sides_is_no_difference_not_a_division_error():
    assert co.outcome_gap(_side(10, 0), _side(10, 0))["state"] == "no_difference"


def test_friction_notes_are_redacted_truncated_worst_first_and_capped():
    secret = "sk-ant-api03-" + "a" * 40
    facets = [
        _facet(
            friction=[
                ("wrong_approach", "minor", "minor note"),
                ("tool_failed", "blocking", f"key {secret} " + "x" * 400),
            ]
        )
    ] + [_facet(friction=[("slow_or_verbose", "major", f"note {i}")]) for i in range(30)]
    notes = co.friction_notes(facets)
    assert len(notes) == co.MAX_FRICTION_NOTES
    assert notes[0]["severity"] == "blocking" and secret not in notes[0]["description"]
    assert len(notes[0]["description"]) <= co.MAX_NOTE_CHARS
    assert [n["severity"] for n in notes] == sorted((n["severity"] for n in notes), key=co._SEVERITY_ORDER.get)


def test_friction_notes_ignore_empty_and_non_text_descriptions():
    notes = co.friction_notes(
        [
            _facet(friction=[("a", "minor", "  "), ("b", "weird", "real note")]),
            {"friction_points": [{"description": 5}]},
        ]
    )
    assert notes == [{"type": "b", "severity": "minor", "description": "real note"}]


def _report(triggered_by=True):
    now = datetime.now(UTC)
    return SimpleNamespace(
        project_id="default",
        component_type="skill",
        component_id=uuid.uuid4(),
        component_version_id=None,
        period_start=now - timedelta(days=14),
        period_end=now,
        triggered_by=uuid.uuid4() if triggered_by else None,
    )


def _rows(prefix, n):
    return [{"user_id": f"{prefix}{i}", "harness": "claude-code", "session_id": f"s{i}"} for i in range(n)]


@pytest.fixture
def cohorts(monkeypatch):
    present, absent = _rows("p", 12), _rows("a", 11)
    monkeypatch.setattr(co, "presence_sample", AsyncMock(return_value=(present, 40)))
    monkeypatch.setattr(co, "absence_sample", AsyncMock(return_value=(absent, 90)))
    monkeypatch.setattr("services.dynamic_settings.get", AsyncMock(return_value=None))
    return SimpleNamespace(present=present, absent=absent)


def _cached(monkeypatch, by_prefix):
    async def load(keys, db):
        return {k: by_prefix[k.user_id[0]] for k in keys if k.user_id[0] in by_prefix}

    monkeypatch.setattr(co, "load_cached_facets_batch", load)


@pytest.mark.asyncio
async def test_outcomes_compare_the_sampled_sessions_with_and_without_the_component(monkeypatch, cohorts):
    _cached(
        monkeypatch,
        {"p": _facet("not_achieved", "dissatisfied", [("tool_failed", "major", "tool broke")]), "a": _facet()},
    )
    outcomes, context = await co.component_outcomes(_report(), None)
    assert outcomes["state"] == "assessed"
    assert (outcomes["with"]["sessions"], outcomes["with"]["sampled"], outcomes["with"]["analyzed"]) == (40, 12, 12)
    assert (outcomes["without"]["sessions"], outcomes["without"]["analyzed"]) == (90, 11)
    assert outcomes["gap"]["state"] == "lower"
    assert "lacks this component" in outcomes["comparison"]
    assert context["friction_notes"][0]["description"] == "tool broke"
    assert "tool broke" not in str(outcomes), "friction text is for the model, never stored"
    co.absence_sample.assert_awaited_once()
    assert co.absence_sample.await_args.args[4] == ["claude-code"], "compared on the harnesses of the present sessions"


@pytest.mark.asyncio
async def test_a_scheduled_report_reads_cached_facets_and_spends_no_model_calls(monkeypatch, cohorts):
    _cached(monkeypatch, {"p": _facet()})
    transcript = AsyncMock(return_value="a transcript")
    extract = AsyncMock()
    monkeypatch.setattr(co, "build_session_transcript", transcript)
    monkeypatch.setattr("services.insights.generator._extract_facets_batch", extract)
    outcomes, _ = await co.component_outcomes(_report(triggered_by=False), None)
    assert outcomes["with"]["analyzed"] == 12 and outcomes["without"]["analyzed"] == 0
    transcript.assert_not_awaited()
    extract.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_requested_report_extracts_missing_facets_within_its_budget(monkeypatch, cohorts):
    _cached(monkeypatch, {})
    monkeypatch.setattr(co, "MAX_NEW_FACETS", 15)
    monkeypatch.setattr(co, "build_session_transcript", AsyncMock(return_value="a transcript"))

    async def extract(transcripts, session_metas, agent_id, db, max_concurrent):
        assert agent_id == "" and max_concurrent == 5
        return {key: _facet() for key in transcripts}

    monkeypatch.setattr("services.insights.generator._extract_facets_batch", extract)
    outcomes, _ = await co.component_outcomes(_report(), None)
    assert outcomes["with"]["analyzed"] == 12, "sessions with the component come first"
    assert outcomes["without"]["analyzed"] == 3, "what is left of the budget goes to the comparison"
    assert co.build_session_transcript.await_count == 15


@pytest.mark.asyncio
async def test_cached_facets_do_not_count_against_the_budget(monkeypatch, cohorts):
    _cached(monkeypatch, {"p": _facet()})
    monkeypatch.setattr(co, "MAX_NEW_FACETS", 5)
    monkeypatch.setattr(co, "build_session_transcript", AsyncMock(return_value="a transcript"))

    async def extract(transcripts, **_):
        return {key: _facet() for key in transcripts}

    monkeypatch.setattr("services.insights.generator._extract_facets_batch", extract)
    outcomes, _ = await co.component_outcomes(_report(), None)
    assert (outcomes["with"]["analyzed"], outcomes["without"]["analyzed"]) == (12, 5)


@pytest.mark.asyncio
async def test_no_present_sessions_with_facets_is_insufficient_not_a_comparison(monkeypatch, cohorts):
    _cached(monkeypatch, {"a": _facet()})
    outcomes, context = await co.component_outcomes(_report(triggered_by=False), None)
    assert outcomes["state"] == "insufficient" and outcomes["gap"]["state"] == "insufficient"
    assert context == {"friction_notes": []}


@pytest.mark.asyncio
async def test_a_failed_read_says_unavailable_and_logs_the_error_type_only(monkeypatch, cohorts, capsys):
    monkeypatch.setattr(co, "presence_sample", AsyncMock(side_effect=RuntimeError("password=hunter2")))
    seen = []
    monkeypatch.setattr(co.optic, "warning", lambda message, *args: seen.append((message, args)))
    outcomes, context = await co.component_outcomes(_report(), None)
    assert outcomes == {"version": co.OUTCOMES_VERSION, "state": "unavailable"} and context == {}
    assert seen and "RuntimeError" in seen[0][1] and "hunter2" not in str(seen)


def test_cohort_keys_are_scoped_sessions():
    key = SessionKey("default", "u", "claude-code", "s")
    assert key.as_tuple() == ("default", "u", "claude-code", "s")
