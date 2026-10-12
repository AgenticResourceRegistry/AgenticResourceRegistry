# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Pending public agent pins must block skill privatization before review."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from api.routes import review
from api.routes.registry import VisibilityUpdateRequest, update_registry_visibility
from models.agent import AgentStatus
from models.agent_component import AgentComponent
from models.mcp import ListingStatus
from models.skill import SkillListing, SkillVersion
from models.team import TeamMembership, TeamRole
from models.user import UserRole
from tests import discovery_support as ds


@pytest.mark.asyncio
async def test_pending_public_agent_pin_blocks_skill_privatization():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            team = await ds.team_with_member(db, owner)
            member = (await db.execute(select(TeamMembership).where(TeamMembership.team_id == team.id))).scalar_one()
            member.role = TeamRole.owner
            listing = await ds.skill(db, owner, team_id=team.id)
            await ds.agent(
                db,
                owner,
                status=AgentStatus.pending,
                components=[("skill", listing.id, listing.name)],
            )
            await db.commit()
            listing_id = listing.id
        async with maker() as db:
            with pytest.raises(HTTPException) as blocked:
                await update_registry_visibility(
                    "skill",
                    str(listing_id),
                    VisibilityUpdateRequest(visibility="team"),
                    SimpleNamespace(state=SimpleNamespace()),
                    db,
                    owner,
                )
            assert blocked.value.status_code == 409
            assert "pending or approved public agent" in blocked.value.detail
            await db.rollback()
            listing = await db.get(SkillListing, listing_id)
            assert not listing.is_private
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_pending_public_agent_cannot_pin_archived_skill_awaiting_global_review():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            reviewer = await ds.user(db, role=UserRole.reviewer)
            listing = await ds.skill(db, owner, status=ListingStatus.archived)
            version = await db.get(SkillVersion, listing.latest_version_id)
            version.requires_global_review = True
            agent = await ds.agent(
                db,
                owner,
                status=AgentStatus.pending,
                components=[("skill", listing.id, listing.name)],
            )
            await db.commit()
            agent_id = agent.id
        async with maker() as db:
            with pytest.raises(HTTPException) as blocked:
                await review.approve_agent(agent_id, None, db, reviewer)
            assert blocked.value.status_code == 422
            assert blocked.value.detail["blocking_components"][0]["status"] == "pending_public_review"
            await db.rollback()
    finally:
        await engine.dispose()


async def test_explicit_missing_skill_pin_blocks_public_agent_approval():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            reviewer = await ds.user(db, role=UserRole.reviewer)
            listing = await ds.skill(db, owner)
            agent = await ds.agent(
                db,
                owner,
                status=AgentStatus.pending,
                components=[("skill", listing.id, listing.name)],
            )
            component = (
                await db.execute(select(AgentComponent).where(AgentComponent.component_id == listing.id))
            ).scalar_one()
            component.resolved_version_id = uuid.uuid4()
            component.resolved_version = "1.2.0"
            await db.commit()
            agent_id = agent.id
        async with maker() as db:
            with pytest.raises(HTTPException) as blocked:
                await review.approve_agent(agent_id, None, db, reviewer)
            assert blocked.value.status_code == 422
            assert blocked.value.detail["blocking_components"][0]["status"] == "missing_pin"
            await db.rollback()
    finally:
        await engine.dispose()


async def test_pending_public_agent_cannot_be_approved_when_pinned_skill_is_private():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            reviewer = await ds.user(db, role=UserRole.reviewer)
            team = await ds.team_with_member(db, owner)
            listing = await ds.skill(db, owner, team_id=team.id, is_private=True)
            agent = await ds.agent(
                db,
                owner,
                status=AgentStatus.pending,
                components=[("skill", listing.id, listing.name)],
            )
            await db.commit()
            agent_id = agent.id
        async with maker() as db:
            with pytest.raises(HTTPException) as blocked:
                await review.approve_agent(agent_id, None, db, reviewer)
            assert blocked.value.status_code == 422
            assert blocked.value.detail["blocking_components"][0]["status"] == "not_public"
            await db.rollback()
    finally:
        await engine.dispose()
