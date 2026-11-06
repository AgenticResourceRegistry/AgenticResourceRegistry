# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the insights self-learn pipeline."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_report(
    narrative: dict | None = None,
    status: str = "completed",
    applied_at=None,
):
    """Create a mock InsightReport."""
    report = MagicMock()
    report.id = uuid.uuid4()
    report.agent_id = uuid.uuid4()
    report.status = MagicMock(value=status)
    report.status.__eq__ = lambda self, other: self.value == (other.value if hasattr(other, "value") else other)
    report.narrative = narrative
    report.applied_at = applied_at
    report.applied_items = None
    return report


def _make_agent(name="test-agent", owner="test-owner"):
    """Create a mock Agent."""
    agent = MagicMock()
    agent.id = uuid.uuid4()
    agent.name = name
    agent.owner = owner
    agent.created_by = uuid.uuid4()
    return agent


def _make_version(version="1.0.0", prompt="You are a helpful assistant.", status="approved"):
    """Create a mock AgentVersion."""
    ver = MagicMock()
    ver.id = uuid.uuid4()
    ver.version = version
    ver.prompt = prompt
    ver.model_name = "claude-sonnet-4-20250514"
    ver.model_config_json = {}
    ver.models_by_harness = {}
    ver.external_mcps = []
    ver.supported_harnesses = ["claude-code"]
    ver.is_prerelease = False
    ver.components = []
    ver.status = MagicMock(value=status)
    ver.status.__eq__ = lambda self, other: self.value == (other.value if hasattr(other, "value") else other)
    return ver


def _make_user():
    """Create a mock User."""
    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = "owner@test.com"
    return user


class TestBuildAdditionsText:
    """Unit tests for _build_additions_text."""

    def test_basic_additions(self):
        from services.insights.self_learn import _build_additions_text

        additions = [
            {"addition": "Always use TypeScript", "why": "User repeatedly asks for TS", "where": "system_prompt"},
            {"addition": "Run tests before committing", "why": "Frequent test failures", "where": "AGENTS.md"},
        ]
        result = _build_additions_text(additions)
        assert "Always use TypeScript" in result
        assert "Run tests before committing" in result
        assert "Reason: User repeatedly asks for TS" in result

    def test_empty_additions(self):
        from services.insights.self_learn import _build_additions_text

        result = _build_additions_text([])
        assert result == ""

    def test_skips_empty_text(self):
        from services.insights.self_learn import _build_additions_text

        additions = [
            {"addition": "", "why": "no text", "where": "system_prompt"},
            {"addition": "Valid addition", "why": "", "where": "system_prompt"},
        ]
        result = _build_additions_text(additions)
        assert "Valid addition" in result
        assert "no text" not in result


class TestIsSkillSuggestion:
    """Unit tests for _is_skill_suggestion."""

    def test_detects_skill(self):
        from services.insights.self_learn import _is_skill_suggestion

        assert _is_skill_suggestion({"feature": "Custom skill"}) is True
        assert _is_skill_suggestion({"feature": "Skill for testing"}) is True

    def test_rejects_non_skill(self):
        from services.insights.self_learn import _is_skill_suggestion

        assert _is_skill_suggestion({"feature": "MCP server"}) is False
        assert _is_skill_suggestion({"feature": "Lifecycle hook"}) is False


class TestIsHookSuggestion:
    """Unit tests for _is_hook_suggestion."""

    def test_detects_hook(self):
        from services.insights.self_learn import _is_hook_suggestion

        assert _is_hook_suggestion({"feature": "Lifecycle hook"}) is True
        assert _is_hook_suggestion({"feature": "Pre-commit validation"}) is True

    def test_rejects_non_hook(self):
        from services.insights.self_learn import _is_hook_suggestion

        assert _is_hook_suggestion({"feature": "Custom skill"}) is False
        assert _is_hook_suggestion({"feature": "MCP server"}) is False


class TestSlugifyName:
    """Unit tests for _slugify, _extract_keywords, and _derive_name."""

    def test_basic_slugify(self):
        from services.insights.self_learn import _slugify

        assert _slugify("My Agent") == "my-agent"

    def test_derive_name_reasonable_length(self):
        from services.insights.self_learn import _derive_name

        result = _derive_name("my-agent", "a" * 100)
        assert len(result) <= 48

    def test_extract_keywords(self):
        from services.insights.self_learn import _extract_keywords

        # Should extract meaningful words, skip filler
        result = _extract_keywords("PR review workflow that checks tests and security")
        assert "review" in result
        assert "checks" in result
        assert "that" not in result

    def test_derive_name_produces_readable_slug(self):
        from services.insights.self_learn import _derive_name

        # Should NOT produce 'ultra-pi-hook-that-checks-whether-planned-file-e'
        name = _derive_name("ultra-pi", "A hook that checks whether planned file edits are within scope")
        assert len(name) <= 48
        assert "that" not in name
        assert "whether" not in name
        # Should contain meaningful words
        assert "ultra-pi" in name

    def test_removes_special_chars(self):
        from services.insights.self_learn import _slugify

        assert _slugify("test@agent#1!") == "test-agent-1"


class TestApplyInsightSuggestions:
    """Integration tests for apply_insight_suggestions."""

    @pytest.mark.asyncio
    async def test_raises_when_report_not_found(self):
        from services.insights.self_learn import apply_insight_suggestions

        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

        with pytest.raises(ValueError, match="Report not found"):
            await apply_insight_suggestions(str(uuid.uuid4()), db, uuid.uuid4())

    @pytest.mark.asyncio
    async def test_raises_when_not_completed(self):
        from services.insights.self_learn import apply_insight_suggestions

        report = _make_report(status="running")
        # Make the status comparison work for the enum check
        from models.insight_report import InsightReportStatus

        report.status = InsightReportStatus.running

        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=report)))

        with pytest.raises(ValueError, match="not completed"):
            await apply_insight_suggestions(str(report.id), db, uuid.uuid4())

    @pytest.mark.asyncio
    async def test_raises_when_already_applied(self):
        from models.insight_report import InsightReportStatus
        from services.insights.self_learn import apply_insight_suggestions

        report = _make_report()
        report.status = InsightReportStatus.completed
        report.applied_at = datetime.now(UTC)

        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=report)))

        with pytest.raises(ValueError, match="already been applied"):
            await apply_insight_suggestions(str(report.id), db, uuid.uuid4())

    @pytest.mark.asyncio
    async def test_raises_when_no_suggestions(self):
        from models.insight_report import InsightReportStatus
        from services.insights.self_learn import apply_insight_suggestions

        report = _make_report(narrative={"suggestions": {}})
        report.status = InsightReportStatus.completed

        agent = _make_agent()
        user = _make_user()

        call_count = [0]

        def _mock_execute_side_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                # Report lookup
                result.scalar_one_or_none = MagicMock(return_value=report)
            elif call_count[0] == 2:
                # Agent lookup
                result.scalar_one_or_none = MagicMock(return_value=agent)
            elif call_count[0] == 3:
                # User lookup
                result.scalar_one_or_none = MagicMock(return_value=user)
            else:
                result.scalar_one_or_none = MagicMock(return_value=None)
            return result

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=_mock_execute_side_effect)

        with pytest.raises(ValueError, match="no suggestions"):
            await apply_insight_suggestions(str(report.id), db, uuid.uuid4())


class TestApplyEndpoint:
    """Tests for the POST /api/v1/insights/reports/{id}/apply endpoint."""

    @pytest.mark.asyncio
    async def test_requires_authenticated_user(self):
        """The endpoint requires authentication; owner/edit authorization is tested separately."""
        from httpx import ASGITransport, AsyncClient

        from main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(f"/api/v1/insights/reports/{uuid.uuid4()}/apply")
        # Should be 401 (no auth), verifies the route exists and is guarded
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_returns_401_without_auth(self):
        """Should return 401 when no token provided."""
        from httpx import ASGITransport, AsyncClient

        from main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                f"/api/v1/insights/reports/{uuid.uuid4()}/apply",
            )
        assert r.status_code == 401
