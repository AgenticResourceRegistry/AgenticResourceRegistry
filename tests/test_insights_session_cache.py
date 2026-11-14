# SPDX-FileCopyrightText: 2026 Santhiya Manivannan <70739919+sanraj2000@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for session_cache — facets DB caching layer.

Covers extract_and_cache_facets (hit/miss paths), store_facets
(insert vs update), and load_cached_facets single-session wrapper.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("DEPLOYMENT_MODE") != "enterprise",
    reason="Insights session_cache tests require DEPLOYMENT_MODE=enterprise",
)


def _facet_data() -> dict:
    return {
        "underlying_goal": "add a feature",
        "goal_categories": ["implement_feature"],
        "outcome": "fully_achieved",
        "user_satisfaction": "happy",
        "agent_helpfulness": "essential",
        "session_type": "single_task",
        "complexity": "medium",
        "friction_points": [],
        "primary_success_factors": ["correct_code_edits"],
        "tools_effective": ["edit"],
        "tools_problematic": [],
        "repeated_instructions": [],
        "brief_summary": "User added a feature successfully.",
    }


class TestExtractAndCacheFacets:
    @pytest.mark.asyncio
    async def test_returns_cached_facets_when_available(self, monkeypatch):
        from services.insights import facets

        cached = _facet_data()
        monkeypatch.setattr(facets, "load_cached_facets", AsyncMock(return_value=cached))
        extract_mock = AsyncMock()
        monkeypatch.setattr(facets, "extract_facets", extract_mock)

        result = await facets.extract_and_cache_facets("s1", "transcript...", {}, "agent-1", MagicMock())
        assert result == cached
        extract_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_calls_extract_when_not_cached(self, monkeypatch):
        from services.insights import facets

        monkeypatch.setattr(facets, "load_cached_facets", AsyncMock(return_value=None))
        expected = _facet_data()
        extract_mock = AsyncMock(return_value=expected)
        store_mock = AsyncMock()
        monkeypatch.setattr(facets, "extract_facets", extract_mock)
        monkeypatch.setattr(facets, "store_facets", store_mock)

        result = await facets.extract_and_cache_facets("s1", "x" * 200, {}, "agent-1", MagicMock())
        assert result == expected
        extract_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_stores_facets_after_extraction(self, monkeypatch):
        from services.insights import facets

        monkeypatch.setattr(facets, "load_cached_facets", AsyncMock(return_value=None))
        extracted = _facet_data()
        monkeypatch.setattr(facets, "extract_facets", AsyncMock(return_value=extracted))
        store_mock = AsyncMock()
        monkeypatch.setattr(facets, "store_facets", store_mock)

        db = MagicMock()
        await facets.extract_and_cache_facets("s1", "x" * 200, {}, "agent-1", db)

        store_mock.assert_called_once_with("s1", "agent-1", extracted, db)

    @pytest.mark.asyncio
    async def test_does_not_store_when_extraction_returns_empty(self, monkeypatch):
        from services.insights import facets

        monkeypatch.setattr(facets, "load_cached_facets", AsyncMock(return_value=None))
        monkeypatch.setattr(facets, "extract_facets", AsyncMock(return_value={}))
        store_mock = AsyncMock()
        monkeypatch.setattr(facets, "store_facets", store_mock)

        await facets.extract_and_cache_facets("s1", "x" * 200, {}, "agent-1", MagicMock())
        store_mock.assert_not_called()


class TestStoreFacets:
    @pytest.mark.asyncio
    async def test_creates_new_record_when_none_exists(self, monkeypatch):
        from services.insights import facets as f_mod

        fake_facets_model = MagicMock()
        new_record_instance = MagicMock()
        fake_facets_model.return_value = new_record_instance
        monkeypatch.setattr(f_mod, "get_facets_model", lambda: fake_facets_model)

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        _agent_id = "550e8400-e29b-41d4-a716-446655440000"
        with patch("sqlalchemy.select", return_value=MagicMock(where=MagicMock(return_value=MagicMock()))):
            await f_mod.store_facets("s1", _agent_id, _facet_data(), mock_db)

        mock_db.add.assert_called_once_with(new_record_instance)
        mock_db.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_updates_existing_record(self, monkeypatch):
        from services.insights import facets as f_mod

        fake_facets_model = MagicMock()
        monkeypatch.setattr(f_mod, "get_facets_model", lambda: fake_facets_model)

        existing = MagicMock()
        existing.facets = {}
        existing.extracted_at = None

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        mock_db.execute = AsyncMock(return_value=mock_result)

        _agent_id = "550e8400-e29b-41d4-a716-446655440000"
        new_data = _facet_data()
        with patch("sqlalchemy.select", return_value=MagicMock(where=MagicMock(return_value=MagicMock()))):
            await f_mod.store_facets("s1", _agent_id, new_data, mock_db)

        assert existing.facets == new_data
        assert existing.extracted_at is not None
        mock_db.add.assert_not_called()
        mock_db.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_none_agent_id(self, monkeypatch):
        from services.insights import facets as f_mod

        fake_facets_model = MagicMock()
        fake_facets_model.return_value = MagicMock()
        monkeypatch.setattr(f_mod, "get_facets_model", lambda: fake_facets_model)

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        # Should not raise even when agent_id is empty/None
        with patch("sqlalchemy.select", return_value=MagicMock(where=MagicMock(return_value=MagicMock()))):
            await f_mod.store_facets("s1", "", _facet_data(), mock_db)
        mock_db.flush.assert_called_once()


class TestLoadCachedFacets:
    @pytest.mark.asyncio
    async def test_single_session_wrapper_delegates_to_batch(self, monkeypatch):
        from services.insights import facets as f_mod

        cached = _facet_data()
        batch_mock = AsyncMock(return_value={"s1": cached})
        monkeypatch.setattr(f_mod, "load_cached_facets_batch", batch_mock)

        result = await f_mod.load_cached_facets("s1", MagicMock())
        assert result == cached
        batch_mock.assert_called_once()
        assert batch_mock.call_args[0][0] == ["s1"]

    @pytest.mark.asyncio
    async def test_returns_none_when_session_not_found(self, monkeypatch):
        from services.insights import facets as f_mod

        monkeypatch.setattr(f_mod, "load_cached_facets_batch", AsyncMock(return_value={}))

        result = await f_mod.load_cached_facets("missing-session", MagicMock())
        assert result is None
