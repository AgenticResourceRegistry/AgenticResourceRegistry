# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for agent listing endpoint access.

Anonymous callers are rejected by default. The public registry dependency can
admit guests while authenticated callers continue to receive their user scope.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_client():
    from httpx import ASGITransport, AsyncClient

    from api.ratelimit import limiter
    from main import app

    limiter.enabled = False
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    )


def _user(role="user"):
    from models.user import User, UserRole

    u = MagicMock(spec=User)
    u.id = uuid.uuid4()
    u.role = getattr(UserRole, role)
    u.username = "testuser"
    u.email = "test@example.com"
    return u


def _mock_db():
    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_result.scalar_one.return_value = 0
    mock_result.all.return_value = []
    db.execute = AsyncMock(return_value=mock_result)
    db.scalar = AsyncMock(return_value=0)
    return db


# ── Integration: GET /api/v1/agents ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_anonymous_rejected_from_list_agents():
    """Anonymous callers get 401 from the agent list endpoint."""
    from main import app

    app.dependency_overrides.clear()

    try:
        async with _make_client() as client:
            r = await client.get("/api/v1/agents")
        assert r.status_code == 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_public_registry_guest_can_list_agents():
    """The public registry dependency admits a guest without widening data scope."""
    from api.deps import get_db, get_registry_user
    from main import app

    mock = _mock_db()

    async def _fake_db():
        yield mock

    app.dependency_overrides[get_db] = _fake_db
    app.dependency_overrides[get_registry_user] = lambda: None

    try:
        async with _make_client() as client:
            response = await client.get("/api/v1/agents")
        assert response.status_code == 200
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_authenticated_user_can_list_agents():
    """Authenticated users get a 200 from the agent list endpoint."""
    from api.deps import get_db, get_registry_user
    from main import app

    user = _user()
    mock = _mock_db()

    async def _fake_db():
        yield mock

    app.dependency_overrides[get_db] = _fake_db
    app.dependency_overrides[get_registry_user] = lambda: user

    try:
        async with _make_client() as client:
            r = await client.get("/api/v1/agents")
        assert r.status_code == 200
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_agent_list_applies_parameterized_search_filters():
    from fastapi import Response

    from api.routes.agent.crud import list_agents

    mock = _mock_db()
    user = _user()
    await list_agents(
        response=Response(),
        search="review",
        namespace="Alice",
        category="Testing",
        harness=None,
        limit=50,
        offset=0,
        db=mock,
        current_user=user,
    )

    statements = [call.args[0] for call in mock.execute.await_args_list]
    sql = " ".join(str(statement) for statement in statements)
    params = {value for statement in statements for value in statement.compile().params.values()}
    assert "agents.namespace" in sql
    assert "agents.category" in sql
    assert {"alice", "Testing"}.issubset(params)
