# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Policy writes validate input, enforce ownership and refresh the review gate."""

import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.deps import get_current_user, get_db
from api.routes.reviews.policy import router
from models import Base
from models.enterprise_config import EnterpriseConfig
from models.team import Team, TeamMembership, TeamRole
from models.user import User, UserRole


@pytest.mark.asyncio
async def test_review_policy_admin_and_team_owner(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        admin = User(username="admin", name="Admin", email="a@x.test", role=UserRole.super_admin)
        owner = User(username="owner", name="Owner", email="o@x.test", role=UserRole.user)
        stranger = User(username="stranger", name="Stranger", email="s@x.test", role=UserRole.user)
        team = Team(handle="test-team", name="Test team", created_by=owner.id)
        db.add_all((admin, owner, stranger))
        await db.flush()
        team.created_by = owner.id
        db.add(team)
        await db.flush()
        db.add(TeamMembership(team_id=team.id, user_id=owner.id, role=TeamRole.owner))
        await db.commit()
        team_id = team.id
    app = FastAPI()
    app.include_router(router)
    selected = [stranger]

    async def db_dep():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = db_dep
    app.dependency_overrides[get_current_user] = lambda: selected[0]

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr("services.dynamic_settings.invalidate", noop)
    monkeypatch.setattr("services.dynamic_settings.get", noop)
    url = f"/api/v1/teams/{team_id}/review-policy"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.put("/api/v1/admin/review-policy", json={})).status_code == 403
        denied_write = await client.put(url, json={})
        denied_read = await client.get(url)
        assert (denied_write.status_code, denied_write.json()["detail"]) == (
            403,
            "Only team owners may edit review policy",
        )
        # A read must not be told it tried to edit.
        assert (denied_read.status_code, denied_read.json()["detail"]) == (
            403,
            "Only team owners may view review policy",
        )
        selected[0] = owner
        assert (await client.put(url, json={"required_approvals": {"skill": 6}})).status_code == 422
        result = await client.put(url, json={"required_approvals": {"skill": 2}})
        assert result.status_code == 200, result.text
        selected[0] = admin
        result = await client.put("/api/v1/admin/review-policy", json={"required_approvals": {"skill": 3}})
        assert result.status_code == 200, result.text
    async with factory() as db:
        assert len((await db.scalars(select(EnterpriseConfig))).all()) == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_generic_settings_cannot_bypass_review_policy_permissions():
    from fastapi import HTTPException

    from api.routes.admin.enterprise_settings import delete_setting, get_setting, upsert_setting
    from schemas.admin import EnterpriseConfigUpdate

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        admin = User(username="admin", name="Admin", email="a@x.test", role=UserRole.admin)
        db.add(admin)
        db.add(EnterpriseConfig(key="review.policy", value='{"required_approvals":{"skill":2}}'))
        await db.flush()
        for key in ("review.policy", f"review.policy.team.{uuid.uuid4()}"):
            for op in (
                get_setting(key, db=db, current_user=admin),
                upsert_setting(key, EnterpriseConfigUpdate(value='{"auto_publish":true}'), db=db, current_user=admin),
                delete_setting(key, db=db, current_user=admin),
            ):
                with pytest.raises(HTTPException) as error:
                    await op
                assert error.value.status_code == 409
        row = await db.scalar(select(EnterpriseConfig).where(EnterpriseConfig.key == "review.policy"))
        assert row.value == '{"required_approvals":{"skill":2}}'
    await engine.dispose()


@pytest.mark.asyncio
async def test_policy_for_explicit_override_does_not_read_stale_cache(monkeypatch):
    from types import SimpleNamespace

    from services.review.policy import policy_for

    async def stale(*args, **kwargs):
        return '{"required_approvals": {"skill": 1}}'

    monkeypatch.setattr("services.dynamic_settings.get", stale)
    review = SimpleNamespace(team_id=None, is_private=False)
    policy = await policy_for(review, org_raw={"required_approvals": {"skill": 3}})
    assert policy.required_approvals["skill"] == 3


@pytest.mark.asyncio
async def test_visibility_change_cannot_lower_public_approval_bar(monkeypatch):
    from types import SimpleNamespace

    from services.review.policy import policy_for

    review = SimpleNamespace(team_id=uuid.uuid4(), is_private=True)
    subject = SimpleNamespace(team_id=review.team_id, is_private=False)

    async def target(db, row):
        return subject, None

    monkeypatch.setattr("services.review.decisions._target", target)
    effective = await policy_for(
        review, db=object(), org_raw={"required_approvals": {"skill": 3}}, team_raw={"required_approvals": {"skill": 1}}
    )
    assert effective.required_approvals["skill"] == 3
    assert effective.source == "organization + teamspace"
