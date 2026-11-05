# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for bulk agent creation endpoint.

Covers successful creation, dry-run preview, duplicate deduplication,
and validation of empty requests.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.deps import get_current_user, get_db
from api.routes.bulk import router
from models.user import User, UserRole


@pytest.fixture(autouse=True)
def _mock_review_boundary(monkeypatch):
    """Bulk routing tests use a mocked session; review persistence is tested separately."""
    monkeypatch.setattr("api.routes.bulk.submit_for_review", AsyncMock(return_value=SimpleNamespace(number=42)))


# ── Helpers ──────────────────────────────────────────────


def _user(**kw):
    u = MagicMock(spec=User)
    u.id = kw.get("id", uuid.uuid4())
    u.role = kw.get("role", UserRole.user)
    u.email = kw.get("email", "test@example.com")
    u.name = kw.get("name", "Test User")
    u.username = kw.get("username", "testuser")
    return u


def _mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.delete = MagicMock()
    db.flush = AsyncMock()
    # begin_nested() hands back a transaction used as an async context manager,
    # not something awaited. Left to AsyncMock the call would return a coroutine
    # and `async with` would reject it, so the savepoint is modelled explicitly.
    nested = MagicMock()
    nested.__aenter__ = AsyncMock(return_value=nested)
    nested.__aexit__ = AsyncMock(return_value=False)
    db.begin_nested = MagicMock(return_value=nested)
    return db


def _app_with(user=None, db=None):
    user = user or _user()
    db = db or _mock_db()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    return app, db, user


def _empty_result():
    """DB result that returns None for scalar_one_or_none (name not found).

    scalars().all() answers empty too: creating an agent now notifies the
    reviewers who own the pending queue, and a registry with no users has no
    reviewers to notify.
    """
    r = MagicMock()
    r.scalar_one_or_none.return_value = None
    r.scalars.return_value.all.return_value = []
    return r


def _exists_result():
    """DB result that returns a truthy value (name already exists)."""
    r = MagicMock()
    r.scalar_one_or_none.return_value = uuid.uuid4()
    return r


def _agent_item(name: str, **overrides) -> dict:
    """Build a minimal bulk agent item dict."""
    item = {"name": name}
    item.update(overrides)
    return item


# ═══════════════════════════════════════════════════════════
# bulk_create_agents (POST /api/v1/bulk/agents)
# ═══════════════════════════════════════════════════════════


class TestBulkCreate:
    """Test successful bulk agent creation."""

    @pytest.mark.asyncio
    async def test_creates_multiple_agents(self):
        """Posting multiple agents returns correct created counts."""
        app, db, _ = _app_with()

        # Each agent triggers a name-existence check (returns not found)
        # then _create_single_agent does flush calls
        db.execute = AsyncMock(return_value=_empty_result())

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/bulk/agents",
                json={
                    "agents": [
                        _agent_item("agent-one"),
                        _agent_item("agent-two"),
                        _agent_item("agent-three"),
                    ]
                },
            )

        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 3
        assert data["created"] == 3
        assert data["skipped"] == 0
        assert data["errors"] == 0
        assert data["partial"] is False
        assert data["dry_run"] is False
        assert len(data["results"]) == 3
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failing_item_is_isolated_from_the_rest_of_the_batch(self):
        """A failed item must not take its neighbours or its own rows with it.

        Every item shares one transaction and _create_single_agent flushes as it
        goes, so without a per-item SAVEPOINT the rows a failed item already
        wrote stay in the transaction and the final commit persists a half-built
        agent the response reported as an error.
        """
        app, db, _ = _app_with()
        db.execute = AsyncMock(return_value=_empty_result())

        # Three flushes per item (agent, version, then the lock); no reviewers
        # exist here, so delivery adds none. Failing the fourth fails the SECOND
        # item, leaving a neighbour on each side to prove the batch carried on.
        flushes = {"n": 0}

        async def flaky_flush(*_a, **_k):
            flushes["n"] += 1
            if flushes["n"] == 4:
                raise RuntimeError("simulated database failure")

        db.flush = AsyncMock(side_effect=flaky_flush)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/bulk/agents",
                json={
                    "agents": [
                        _agent_item("agent-one"),
                        _agent_item("agent-two"),
                        _agent_item("agent-three"),
                    ]
                },
            )

        assert r.status_code == 200
        data = r.json()
        assert data["created"] == 2
        assert data["errors"] == 1
        assert data["partial"] is True

        by_name = {item["name"]: item for item in data["results"]}
        assert by_name["agent-one"]["status"] == "created"
        assert by_name["agent-two"]["status"] == "error"
        assert by_name["agent-two"]["error"] == "Agent could not be created"
        assert "simulated database failure" not in str(data)
        # The item after the failure still ran, which is the part a poisoned
        # transaction would have taken away.
        assert by_name["agent-three"]["status"] == "created"

        # One savepoint per attempted item, so the failure had somewhere to roll
        # back to instead of leaving its rows in the shared transaction.
        assert db.begin_nested.call_count == 3

    @pytest.mark.asyncio
    async def test_response_results_have_correct_status(self):
        """Each result item shows status='created'."""
        app, db, _ = _app_with()
        db.execute = AsyncMock(return_value=_empty_result())

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/bulk/agents",
                json={"agents": [_agent_item("new-agent")]},
            )

        assert r.status_code == 200
        result = r.json()["results"][0]
        assert result["name"] == "new-agent"
        assert result["status"] == "created"


# ═══════════════════════════════════════════════════════════
# Dry run
# ═══════════════════════════════════════════════════════════


class TestBulkDryRun:
    """Test dry_run=true returns preview without persisting."""

    @pytest.mark.asyncio
    async def test_dry_run_returns_preview(self):
        """With dry_run=True, agents are previewed but not committed."""
        app, db, _ = _app_with()
        db.execute = AsyncMock(return_value=_empty_result())

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/bulk/agents",
                json={
                    "agents": [_agent_item("preview-agent")],
                    "dry_run": True,
                },
            )

        assert r.status_code == 200
        data = r.json()
        assert data["dry_run"] is True
        assert data["created"] == 1
        # Commit should NOT be called in dry-run mode
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dry_run_reports_invalid_components_as_partial(self):
        app, db, _ = _app_with()
        db.execute = AsyncMock(return_value=_empty_result())

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/bulk/agents",
                json={
                    "agents": [
                        _agent_item(
                            "invalid-agent",
                            components=[{"component_type": "skill"}],
                        )
                    ],
                    "dry_run": True,
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["created"] == 0
        assert data["errors"] == 1
        assert data["partial"] is True
        assert data["results"][0]["status"] == "error"
        assert data["results"][0]["error"] == "Agent definition is invalid"
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dry_run_no_agent_ids(self):
        """Dry-run results should not include agent_id values."""
        app, db, _ = _app_with()
        db.execute = AsyncMock(return_value=_empty_result())

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/bulk/agents",
                json={
                    "agents": [_agent_item("dry-agent")],
                    "dry_run": True,
                },
            )

        result = r.json()["results"][0]
        assert result["agent_id"] is None


# ═══════════════════════════════════════════════════════════
# Deduplication
# ═══════════════════════════════════════════════════════════


class TestBulkDedup:
    """Test that agents with duplicate names are skipped."""

    @pytest.mark.asyncio
    async def test_skips_duplicate_names(self):
        """Agents with names that already exist for the user are skipped."""
        app, db, _ = _app_with()

        # First agent name exists, second does not
        # First name check hits, second misses; every later query (component
        # resolution, inbox recipients) answers empty rather than exhausting.
        script = iter([_exists_result(), _empty_result()])
        db.execute = AsyncMock(side_effect=lambda *a, **k: next(script, _empty_result()))

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/bulk/agents",
                json={
                    "agents": [
                        _agent_item("existing-agent"),
                        _agent_item("new-agent"),
                    ]
                },
            )

        assert r.status_code == 200
        data = r.json()
        assert data["created"] == 1
        assert data["skipped"] == 1
        assert data["results"][0]["status"] == "skipped"
        assert data["results"][1]["status"] == "created"

    @pytest.mark.asyncio
    async def test_same_request_duplicates_are_skipped_in_dry_run(self):
        app, db, _ = _app_with()
        db.execute = AsyncMock(return_value=_empty_result())

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/bulk/agents",
                json={
                    "agents": [_agent_item("Review Agent"), _agent_item("review-agent")],
                    "dry_run": True,
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["created"] == 1
        assert data["skipped"] == 1
        assert data["errors"] == 0
        assert data["partial"] is False
        assert data["results"][1]["status"] == "skipped"
        assert "batch" in data["results"][1]["error"]

    @pytest.mark.asyncio
    async def test_skipped_result_includes_error_message(self):
        """Skipped results include an error message explaining the skip."""
        app, db, _ = _app_with()
        db.execute = AsyncMock(return_value=_exists_result())

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/bulk/agents",
                json={"agents": [_agent_item("dup-agent")]},
            )

        result = r.json()["results"][0]
        assert result["status"] == "skipped"
        assert result["error"] is not None


# ═══════════════════════════════════════════════════════════
# Validation
# ═══════════════════════════════════════════════════════════


class TestBulkValidation:
    """Test request validation for bulk endpoint."""

    @pytest.mark.asyncio
    async def test_rejects_empty_agent_list(self):
        """An empty agents list returns 422."""
        app, db, _ = _app_with()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/bulk/agents",
                json={"agents": []},
            )

        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_missing_agents_field(self):
        """Missing agents field returns 422."""
        app, db, _ = _app_with()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/bulk/agents",
                json={},
            )

        assert r.status_code == 422
