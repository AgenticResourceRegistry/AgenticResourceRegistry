# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Automated-check settings: admins edit the public scope, team owners edit their teamspace."""

import json

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.deps import get_current_user, get_db
from api.routes.reviews.check_settings import router
from models import Base
from models.enterprise_config import EnterpriseConfig
from models.team import Team, TeamMembership, TeamRole
from models.user import User, UserRole


@pytest.mark.asyncio
async def test_check_settings_permissions_validation_and_persistence(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        users = {
            "admin": User(username="admin", name="A", email="a@x.test", role=UserRole.admin),
            "reviewer": User(username="rev", name="R", email="r@x.test", role=UserRole.reviewer),
            "owner": User(username="owner", name="O", email="o@x.test", role=UserRole.user),
            "stranger": User(username="stranger", name="S", email="s@x.test", role=UserRole.user),
        }
        db.add_all(users.values())
        await db.flush()
        team = Team(handle="t", name="T", created_by=users["owner"].id)
        db.add(team)
        await db.flush()
        db.add(TeamMembership(team_id=team.id, user_id=users["owner"].id, role=TeamRole.owner))
        await db.commit()
        team_id = team.id
    app = FastAPI()
    app.include_router(router)
    selected = [users["stranger"]]

    async def db_dep():
        async with factory() as db:
            yield db

    async def noop(*args, **kwargs):
        return None

    app.dependency_overrides[get_db] = db_dep
    app.dependency_overrides[get_current_user] = lambda: selected[0]
    monkeypatch.setattr("services.dynamic_settings.invalidate", noop)
    monkeypatch.setattr("services.dynamic_settings.get", noop)
    org, team_url = "/api/v1/admin/review-checks", f"/api/v1/teams/{team_id}/review-checks"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for who in ("stranger", "reviewer"):
            selected[0] = users[who]
            assert (await client.get(org)).status_code == 403 and (await client.put(org, json={})).status_code == 403
        assert (await client.get(team_url)).status_code == 403
        selected[0] = users["admin"]  # Admin level, not only super admin.
        listing = (await client.get(org)).json()
        by_id = {c["id"]: c for c in listing["checks"]}
        assert by_id["secrets"]["locked"] and by_id["secrets"]["required"]
        assert by_id["git_source"]["enabled"] and not by_id["remote_mcp"]["enabled"]
        for bad in (
            {"checks": {"secrets": {"enabled": False}}},
            {"checks": {"git_source": {"enabled": False, "required": True}}},
            {"checks": {"made_up": {"enabled": True}}},
            {"checks": {"git_source": {"enabled": "true"}}},
            {"surprise": 1},
        ):
            assert (await client.put(org, json=bad)).status_code == 422, bad
        saved = await client.put(org, json={"checks": {"remote_mcp": {"enabled": True, "required": True}}})
        assert saved.status_code == 200, saved.text
        assert {c["id"]: c for c in saved.json()["checks"]}["remote_mcp"]["required"] is True
        selected[0] = users["owner"]
        assert (await client.put(org, json={})).status_code == 403
        assert (await client.put(team_url, json={"checks": {"script_lint": {"enabled": False}}})).status_code == 200
    async with factory() as db:
        stored = {row.key: json.loads(row.value) for row in (await db.scalars(select(EnterpriseConfig))).all()}
    assert stored["review.checks"]["checks"]["remote_mcp"]["required"] is True
    assert stored[f"review.checks.team.{team_id}"] == {"checks": {"script_lint": {"enabled": False, "required": False}}}
    await engine.dispose()
