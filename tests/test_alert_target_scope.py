# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Alert metrics aggregate every user's sessions, so only admins and agent owners may watch a target."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import api.routes.alert as alert_routes
import services.alert_evaluator as evaluator
from models.agent import Agent
from models.alert import AlertRule
from models.base import Base
from models.user import User, UserRole
from schemas.alert import AlertRuleCreate


def _user(role=UserRole.user, name="member"):
    return User(id=uuid.uuid4(), email=f"{name}@example.test", username=name, name=name, role=role)


def _agent(owner, *, namespace, co_authors=()):
    return Agent(
        id=uuid.uuid4(),
        name="reviewer",
        namespace=namespace,
        slug="reviewer",
        owner=namespace,
        created_by=owner.id,
        co_authors=[str(uid) for uid in co_authors],
    )


@pytest_asyncio.fixture
async def world():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner, coauthor, stranger, admin = (
        _user(name="owner"),
        _user(name="co"),
        _user(name="stranger"),
        _user(UserRole.admin, name="admin"),
    )
    agent = _agent(owner, namespace="team-a", co_authors=[coauthor.id])
    async with sessions() as db:
        db.add_all([owner, coauthor, stranger, admin, agent])
        await db.commit()
        yield db, owner, coauthor, stranger, admin, agent
    await engine.dispose()


@pytest.mark.asyncio
async def test_who_may_watch_what(world):
    db, owner, coauthor, stranger, admin, agent = world
    watch = evaluator.watchable_target_id

    assert await watch(db, owner, "agent", str(agent.id)) == str(agent.id)
    assert await watch(db, coauthor, "agent", str(agent.id)) == str(agent.id)
    assert await watch(db, owner, "agent", "team-a/reviewer") == str(agent.id)  # canonicalised to the UUID
    assert await watch(db, stranger, "agent", str(agent.id)) is None  # can see it, but does not own it
    assert await watch(db, owner, "agent", str(uuid.uuid4())) is None
    for target_type in ("all", "mcp"):  # both aggregate the whole deployment
        assert await watch(db, owner, target_type, "") is None
    assert await watch(db, None, "agent", str(agent.id)) is None  # creator deleted
    assert await watch(db, admin, "all", "") == ""
    assert await watch(db, admin, "agent", str(agent.id)) == str(agent.id)


@pytest.mark.asyncio
async def test_create_alert_rejects_targets_the_caller_may_not_watch(world, monkeypatch):
    db, owner, _coauthor, stranger, _admin, agent = world
    monkeypatch.setattr(alert_routes, "is_private_url", lambda _url: False)

    for target_type, target_id in (("all", ""), ("mcp", "some-mcp"), ("agent", str(agent.id))):
        body = AlertRuleCreate(
            name="spy",
            metric="token_usage",
            threshold=0,
            condition="above",
            target_type=target_type,
            target_id=target_id,
        )
        with pytest.raises(HTTPException) as exc:
            await alert_routes.create_alert(body, db=db, current_user=stranger)
        assert exc.value.status_code == 403

    body = AlertRuleCreate(
        name="mine",
        metric="token_usage",
        threshold=0,
        condition="above",
        target_type="agent",
        target_id="team-a/reviewer",
    )
    created = await alert_routes.create_alert(body, db=db, current_user=owner)
    assert created.target_id == str(agent.id)


@pytest.mark.asyncio
async def test_evaluator_skips_rules_whose_creator_may_not_watch_the_target(world):
    db, owner, _coauthor, stranger, _admin, agent = world
    allowed = AlertRule(
        name="ok",
        metric="token_usage",
        threshold=0,
        condition="above",
        target_type="agent",
        target_id=str(agent.id),
        created_by=owner.id,
        status="active",
    )
    # Created before the gate existed: a non-owner watching the whole deployment.
    legacy = AlertRule(
        name="spy",
        metric="token_usage",
        threshold=0,
        condition="above",
        target_type="all",
        target_id="",
        created_by=stranger.id,
        status="active",
    )
    db.add_all([allowed, legacy])
    await db.commit()

    class _Session:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_exc):
            return False

    metric = AsyncMock(return_value=None)
    with (
        patch.object(evaluator, "async_session", _Session),
        patch.object(evaluator, "_query_metric", metric),
        patch("services.webhook_delivery.flush_delivery_records", AsyncMock()),
    ):
        await evaluator.evaluate_alerts({})

    assert [call.args[2] for call in metric.await_args_list] == [str(agent.id)]  # the legacy rule never ran
