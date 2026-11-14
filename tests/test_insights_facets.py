# SPDX-FileCopyrightText: 2026 Santhiya Manivannan <70739919+sanraj2000@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for facets.py — per-session LLM facet extraction and aggregation."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("DEPLOYMENT_MODE") != "enterprise",
    reason="Insights facets tests require DEPLOYMENT_MODE=enterprise",
)


def _full_facet(
    goal: str = "fix_bug",
    outcome: str = "fully_achieved",
    satisfaction: str = "satisfied",
    helpfulness: str = "very_helpful",
    session_type: str = "single_task",
    complexity: str = "low",
) -> dict:
    return {
        "underlying_goal": "fix the login bug",
        "goal_categories": [goal],
        "outcome": outcome,
        "user_satisfaction": satisfaction,
        "agent_helpfulness": helpfulness,
        "session_type": session_type,
        "complexity": complexity,
        "friction_points": [],
        "primary_success_factors": ["correct_code_edits"],
        "tools_effective": ["bash", "edit"],
        "tools_problematic": [],
        "repeated_instructions": [],
        "brief_summary": "User wanted to fix a bug; agent did it.",
    }


class TestExtractFacets:
    @pytest.mark.asyncio
    async def test_returns_empty_for_short_transcript(self, monkeypatch):
        from services.insights import facets

        call_mock = AsyncMock()
        monkeypatch.setattr(facets, "get_call_model", lambda: call_mock)

        import services.dynamic_settings as ds

        monkeypatch.setattr(ds, "get", AsyncMock(return_value=None))

        result = await facets.extract_facets("s1", "hi", {})
        assert result == {}
        call_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_empty_for_blank_transcript(self, monkeypatch):
        from services.insights import facets

        call_mock = AsyncMock()
        monkeypatch.setattr(facets, "get_call_model", lambda: call_mock)

        import services.dynamic_settings as ds

        monkeypatch.setattr(ds, "get", AsyncMock(return_value=None))

        result = await facets.extract_facets("s1", "   ", {})
        assert result == {}

    @pytest.mark.asyncio
    async def test_calls_model_with_long_transcript(self, monkeypatch):
        from services.insights import facets

        expected = _full_facet()
        call_mock = AsyncMock(return_value=expected)
        monkeypatch.setattr(facets, "get_call_model", lambda: call_mock)

        import services.dynamic_settings as ds

        monkeypatch.setattr(ds, "get", AsyncMock(return_value=None))

        transcript = "x" * 200
        result = await facets.extract_facets("s1", transcript, {})
        assert result == expected
        call_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_model_exception_returns_empty(self, monkeypatch):
        from services.insights import facets

        call_mock = AsyncMock(side_effect=RuntimeError("LLM unavailable"))
        monkeypatch.setattr(facets, "get_call_model", lambda: call_mock)

        import services.dynamic_settings as ds

        monkeypatch.setattr(ds, "get", AsyncMock(return_value=None))

        result = await facets.extract_facets("s1", "x" * 200, {})
        assert result == {}

    @pytest.mark.asyncio
    async def test_model_non_dict_response_returns_empty(self, monkeypatch):
        from services.insights import facets

        call_mock = AsyncMock(return_value=None)
        monkeypatch.setattr(facets, "get_call_model", lambda: call_mock)

        import services.dynamic_settings as ds

        monkeypatch.setattr(ds, "get", AsyncMock(return_value=None))

        result = await facets.extract_facets("s1", "x" * 200, {})
        assert result == {}

    @pytest.mark.asyncio
    async def test_uses_model_override_from_settings(self, monkeypatch):
        from services.insights import facets

        call_mock = AsyncMock(return_value=_full_facet())
        monkeypatch.setattr(facets, "get_call_model", lambda: call_mock)

        import services.dynamic_settings as ds

        monkeypatch.setattr(ds, "get", AsyncMock(return_value="claude-opus-4"))

        await facets.extract_facets("s1", "x" * 200, {})
        _, kwargs = call_mock.call_args
        assert kwargs.get("model_override") == "claude-opus-4"


class TestAggregateFacets:
    def test_empty_returns_empty_dict(self):
        from services.insights.facets import aggregate_facets

        assert aggregate_facets([]) == {}

    def test_counts_sessions(self):
        from services.insights.facets import aggregate_facets

        result = aggregate_facets([_full_facet(), _full_facet()])
        assert result["sessions_with_facets"] == 2

    def test_goal_categories_sorted_by_count(self):
        from services.insights.facets import aggregate_facets

        facets_list = [
            _full_facet(goal="fix_bug"),
            _full_facet(goal="fix_bug"),
            _full_facet(goal="implement_feature"),
        ]
        result = aggregate_facets(facets_list)
        top = result["goal_categories"][0]
        assert top == ("fix_bug", 2)

    def test_outcomes_counted(self):
        from services.insights.facets import aggregate_facets

        facets_list = [_full_facet(outcome="fully_achieved"), _full_facet(outcome="not_achieved")]
        result = aggregate_facets(facets_list)
        assert result["outcomes"]["fully_achieved"] == 1
        assert result["outcomes"]["not_achieved"] == 1

    def test_satisfaction_counted(self):
        from services.insights.facets import aggregate_facets

        result = aggregate_facets([_full_facet(satisfaction="happy")])
        assert result["satisfaction"]["happy"] == 1

    def test_friction_types_counted(self):
        from services.insights.facets import aggregate_facets

        f = _full_facet()
        f["friction_points"] = [
            {"type": "wrong_approach", "description": "went off rails", "severity": "major"},
            {"type": "wrong_approach", "description": "again", "severity": "minor"},
            {"type": "buggy_code", "description": "broken", "severity": "blocking"},
        ]
        result = aggregate_facets([f])
        friction_map = dict(result["friction_types"])
        assert friction_map["wrong_approach"] == 2
        assert friction_map["buggy_code"] == 1

    def test_tools_effective_aggregated(self):
        from services.insights.facets import aggregate_facets

        f1 = _full_facet()
        f1["tools_effective"] = ["bash", "edit"]
        f2 = _full_facet()
        f2["tools_effective"] = ["bash"]
        result = aggregate_facets([f1, f2])
        tool_map = dict(result["tools_effective"])
        assert tool_map["bash"] == 2
        assert tool_map["edit"] == 1

    def test_tools_problematic_with_dict_format(self):
        from services.insights.facets import aggregate_facets

        f = _full_facet()
        f["tools_problematic"] = [{"tool": "bash", "reason": "timeout"}]
        result = aggregate_facets([f])
        tool_map = dict(result["tools_problematic"])
        assert tool_map["bash"] == 1

    def test_repeated_instructions_deduplicated_by_frequency(self):
        from services.insights.facets import aggregate_facets

        instr = "always show diffs before editing"
        f1 = _full_facet()
        f1["repeated_instructions"] = [instr]
        f2 = _full_facet()
        f2["repeated_instructions"] = [instr]
        f3 = _full_facet()
        f3["repeated_instructions"] = ["other instruction appearing once"]
        result = aggregate_facets([f1, f2, f3])
        # Only instructions with frequency >= 2 appear in repeated_summary
        summaries = [r["instruction"] for r in result["repeated_instructions"]]
        assert instr.lower() in summaries

    def test_instructions_with_frequency_one_excluded(self):
        from services.insights.facets import aggregate_facets

        f = _full_facet()
        f["repeated_instructions"] = ["unique instruction only once"]
        result = aggregate_facets([f])
        assert result["repeated_instructions"] == []

    def test_skips_empty_facet_dicts(self):
        from services.insights.facets import aggregate_facets

        result = aggregate_facets([{}, _full_facet()])
        # Empty facets are skipped; should still count the non-empty one
        assert result["sessions_with_facets"] == 2  # len(all_facets) counts all including {}
        # But outcomes etc. should only include data from the non-empty one
        assert result["outcomes"].get("fully_achieved") == 1


class TestLoadCachedFacetsBatch:
    @pytest.mark.asyncio
    async def test_empty_session_ids_returns_empty(self):
        from services.insights.facets import load_cached_facets_batch

        result = await load_cached_facets_batch([], MagicMock())
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_facets_keyed_by_session_id(self, monkeypatch):
        from services.insights import facets

        fake_row = MagicMock()
        fake_row.session_id = "s1"
        fake_row.facets = {"outcome": "fully_achieved"}

        mock_model = MagicMock()
        monkeypatch.setattr(facets, "get_facets_model", lambda: mock_model)

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [fake_row]
        mock_db.execute = AsyncMock(return_value=mock_result)

        # Patch sqlalchemy.select at import-time location to bypass SA coercion
        with patch("sqlalchemy.select", return_value=MagicMock(where=MagicMock(return_value=MagicMock()))):
            result = await facets.load_cached_facets_batch(["s1"], mock_db)
        assert "s1" in result
        assert result["s1"]["outcome"] == "fully_achieved"

    @pytest.mark.asyncio
    async def test_rows_with_none_facets_excluded(self, monkeypatch):
        from services.insights import facets

        fake_row = MagicMock()
        fake_row.session_id = "s1"
        fake_row.facets = None

        mock_model = MagicMock()
        monkeypatch.setattr(facets, "get_facets_model", lambda: mock_model)

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [fake_row]
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch("sqlalchemy.select", return_value=MagicMock(where=MagicMock(return_value=MagicMock()))):
            result = await facets.load_cached_facets_batch(["s1"], mock_db)
        assert result == {}
