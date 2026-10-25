# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Who may compare an unapproved fork with its upstream."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.deps import get_db, get_registry_user
from api.routes import component_forks, skill
from api.routes.agent import fork as agent_fork_routes
from api.routes.agent import router as agent_router
from models.agent import Agent, AgentStatus, AgentVersion
from models.base import Base
from models.mcp import ListingStatus
from models.skill import SkillListing, SkillVersion
from models.team import Team, TeamMembership, TeamRole
from models.user import User, UserRole


def _user(role=UserRole.user):
    name = uuid.uuid4().hex[:10]
    return User(id=uuid.uuid4(), email=f"{name}@example.test", username=name, name=name, password_hash="x", role=role)


def _skill_pair(owner, team, fork_status):
    source = SkillListing(
        id=uuid.uuid4(),
        name="Source",
        namespace="team",
        slug="source",
        owner="team",
        submitted_by=owner.id,
        team_id=team.id,
        is_private=True,
        co_authors=[],
    )
    base = SkillVersion(
        id=uuid.uuid4(),
        listing_id=source.id,
        version="1.0.0",
        status=ListingStatus.approved,
        description="Original",
        task_type="review",
        delivery_mode="registry_direct",
        skill_md_content="# Review",
        supported_harnesses=["kiro"],
        released_by=owner.id,
        released_at=datetime.now(UTC),
    )
    fork = SkillListing(
        id=uuid.uuid4(),
        name="Fork",
        namespace="team",
        slug="fork",
        owner="team",
        submitted_by=owner.id,
        team_id=team.id,
        is_private=True,
        co_authors=[],
        forked_from_id=source.id,
        forked_from_version_id=base.id,
        forked_from_ref="team/source@1.0.0",
        forked_at=datetime.now(UTC),
    )
    draft = SkillVersion(
        id=uuid.uuid4(),
        listing_id=fork.id,
        version="1.0.0",
        status=fork_status,
        description="Changed by the fork",
        task_type="review",
        delivery_mode="registry_direct",
        skill_md_content="# Review",
        supported_harnesses=["kiro"],
        released_by=owner.id,
        released_at=datetime.now(UTC),
    )
    source.latest_version_id = base.id
    fork.latest_version_id = draft.id
    return [source, base, fork, draft], fork


def _agent_pair(owner, team, fork_status):
    def agent(name, **extra):
        return Agent(
            id=uuid.uuid4(),
            name=name,
            namespace="team",
            slug=name.lower(),
            owner="team",
            created_by=owner.id,
            team_id=team.id,
            is_private=True,
            co_authors=[],
            **extra,
        )

    def version(agent_id, status, prompt):
        return AgentVersion(
            id=uuid.uuid4(),
            agent_id=agent_id,
            version="1.0.0",
            status=status,
            description="d",
            prompt=prompt,
            model_name="m",
            model_config_json={},
            models_by_harness={},
            external_mcps=[],
            supported_harnesses=[],
            required_capabilities=[],
            inferred_supported_harnesses=[],
            released_by=owner.id,
        )

    source = agent("Source")
    base = version(source.id, AgentStatus.approved, "Original")
    fork = agent(
        "Fork",
        forked_from_id=source.id,
        forked_from_version_id=base.id,
        forked_from_ref="team/source@1.0.0",
        forked_at=datetime.now(UTC),
    )
    draft = version(fork.id, fork_status, "Changed by the fork")
    source.latest_version_id = base.id
    fork.latest_version_id = draft.id
    return [source, base, fork, draft], fork


@pytest.mark.asyncio
@pytest.mark.parametrize(("kind", "path"), [("skill", "skills"), ("agent", "agents")])
async def test_team_reviewer_compares_pending_fork_but_not_others_drafts(kind, path, monkeypatch):
    monkeypatch.setattr(component_forks.dynamic_settings, "get_bool", AsyncMock(return_value=True))
    monkeypatch.setattr(agent_fork_routes.dynamic_settings, "get_bool", AsyncMock(return_value=True))
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            owner, reviewer, member = _user(), _user(), _user()
            team = Team(id=uuid.uuid4(), name="Team", handle="team", is_private=True, created_by=owner.id)
            db.add_all([owner, reviewer, member, team])
            await db.flush()
            db.add_all(
                [
                    TeamMembership(team_id=team.id, user_id=owner.id, role=TeamRole.member),
                    TeamMembership(team_id=team.id, user_id=reviewer.id, role=TeamRole.reviewer),
                    TeamMembership(team_id=team.id, user_id=member.id, role=TeamRole.member),
                ]
            )
            pair = _skill_pair if kind == "skill" else _agent_pair
            pending_status = ListingStatus.pending if kind == "skill" else AgentStatus.pending
            draft_status = ListingStatus.draft if kind == "skill" else AgentStatus.draft
            rows, fork = pair(owner, team, pending_status)
            for row in rows:
                db.add(row)
                await db.flush()
            await db.commit()

            app = FastAPI()
            app.include_router(skill.router)
            app.include_router(agent_router)

            async def session():
                yield db

            caller = {"user": reviewer}
            app.dependency_overrides[get_db] = session
            app.dependency_overrides[get_registry_user] = lambda: caller["user"]
            url = f"/api/v1/{path}/{fork.id}/fork-diff"
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(url)
                assert response.status_code == 200, response.text
                assert "Changed by the fork" in response.json()["diff"]

                # A plain team member has no review role: an unapproved fork does not exist for them.
                caller["user"] = member
                hidden = await client.get(url)
                assert hidden.status_code == 404 and hidden.json()["detail"] == "Fork not found"

                # A reviewer's scope is the review queue, never someone else's draft.
                caller["user"] = reviewer
                version_model = SkillVersion if kind == "skill" else AgentVersion
                draft = await db.get(version_model, fork.latest_version_id)
                draft.status = draft_status
                await db.commit()
                assert (await client.get(url)).status_code == 404
    finally:
        await engine.dispose()
