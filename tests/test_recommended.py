# SPDX-FileCopyrightText: 2026 Chandhini Veerabuthiran <Chandhini03@users.noreply.github.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the admin recommended toggle endpoint (PATCH /api/v1/admin/recommended)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.deps import get_current_user, get_db
from api.routes.admin import router
from models.user import UserRole

# ── Helpers ──────────────────────────────────────────────


def _user(*, role: UserRole = UserRole.admin) -> MagicMock:
    u = MagicMock()
    u.id = uuid.uuid4()
    u.role = role
    u.email = "admin@example.com"
    u.username = "admin"
    return u


def _mock_db(entity=None):
    """Return a mock async session. When *entity* is provided, ``execute`` will
    return a result whose ``scalar_one_or_none`` yields it."""
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.delete = MagicMock()

    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=entity)
    db.execute = AsyncMock(return_value=result)
    return db


def _app(user=None, db=None):
    user = user or _user()
    db = db or _mock_db()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    return app, db, user


# ── Tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_recommended_success():
    entity_id = uuid.uuid4()
    entity = MagicMock()
    entity.is_recommended = False
    entity.id = entity_id

    async def _refresh(obj):
        obj.is_recommended = True

    app, db, _ = _app(db=_mock_db(entity))
    db.refresh = _refresh

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(
            "/api/v1/admin/recommended",
            json={"entity_type": "agent", "entity_id": str(entity_id), "recommended": True},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["entity_type"] == "agent"
    assert body["entity_id"] == str(entity_id)
    assert body["is_recommended"] is True
    assert entity.is_recommended is True


@pytest.mark.asyncio
async def test_set_recommended_removes():
    entity_id = uuid.uuid4()
    entity = MagicMock()
    entity.is_recommended = True
    entity.id = entity_id

    async def _refresh(obj):
        obj.is_recommended = False

    app, db, _ = _app(db=_mock_db(entity))
    db.refresh = _refresh

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(
            "/api/v1/admin/recommended",
            json={"entity_type": "mcp", "entity_id": str(entity_id), "recommended": False},
        )

    assert resp.status_code == 200
    assert resp.json()["is_recommended"] is False
    assert entity.is_recommended is False


@pytest.mark.asyncio
async def test_set_recommended_not_found():
    app, _, _ = _app(db=_mock_db(entity=None))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(
            "/api/v1/admin/recommended",
            json={"entity_type": "skill", "entity_id": str(uuid.uuid4()), "recommended": True},
        )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_set_recommended_invalid_entity_type():
    app, _, _ = _app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(
            "/api/v1/admin/recommended",
            json={"entity_type": "invalid", "entity_id": str(uuid.uuid4()), "recommended": True},
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_set_recommended_forbidden_for_non_admin():
    user = _user(role=UserRole.user)
    app, _, _ = _app(user=user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(
            "/api/v1/admin/recommended",
            json={"entity_type": "agent", "entity_id": str(uuid.uuid4()), "recommended": True},
        )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_set_recommended_forbidden_for_reviewer():
    user = _user(role=UserRole.reviewer)
    app, _, _ = _app(user=user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(
            "/api/v1/admin/recommended",
            json={"entity_type": "hook", "entity_id": str(uuid.uuid4()), "recommended": True},
        )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_set_recommended_all_component_types():
    """Ensure the endpoint accepts every valid entity_type."""
    for entity_type in ("agent", "mcp", "skill", "hook", "prompt", "sandbox"):
        entity_id = uuid.uuid4()
        entity = MagicMock()
        entity.is_recommended = False
        entity.id = entity_id

        async def _refresh(obj):
            obj.is_recommended = True

        app, db, _ = _app(db=_mock_db(entity))
        db.refresh = _refresh

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.patch(
                "/api/v1/admin/recommended",
                json={"entity_type": entity_type, "entity_id": str(entity_id), "recommended": True},
            )

        assert resp.status_code == 200, f"Failed for entity_type={entity_type}"
        assert resp.json()["entity_type"] == entity_type


@pytest.mark.asyncio
async def test_set_recommended_keeps_updated_at_and_reaches_discovery():
    """Curation is not an edit: the listing's updated_at stays put, and discovery sees the flag."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from models.discovery_entry import DiscoveryEntry
    from models.skill import SkillListing
    from services.discovery.projection import reproject_all
    from tests import discovery_support as fx

    engine = fx.make_engine()
    try:
        sessions = await fx.create_schema(engine)
        stamp = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
        async with sessions() as db:
            admin = await fx.user(db, role=UserRole.admin)
            skill = await fx.skill(db, admin)
            skill.updated_at = stamp
            await db.commit()
            skill_id = skill.id

        async def db_override():
            async with sessions() as db:
                yield db

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user] = lambda: admin
        app.dependency_overrides[get_db] = db_override
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.patch(
                "/api/v1/admin/recommended",
                json={"entity_type": "skill", "entity_id": str(skill_id), "recommended": True},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_recommended"] is True

        async with sessions() as db:
            row = (await db.execute(select(SkillListing).where(SkillListing.id == skill_id))).scalar_one()
            assert row.is_recommended is True
            assert row.updated_at.replace(tzinfo=UTC) == stamp
            await reproject_all(db, ctx=fx.CTX)
            entry = (
                await db.execute(select(DiscoveryEntry).where(DiscoveryEntry.local_entity_id == skill_id))
            ).scalar_one()
            assert entry.raw_entry["obs:recommended"] is True
    finally:
        await engine.dispose()
