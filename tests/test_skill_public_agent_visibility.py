# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Pending public agent pins must block skill privatization before review."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from api.routes.registry import VisibilityUpdateRequest, update_registry_visibility
from models.agent import AgentStatus
from models.skill import SkillListing
from models.team import TeamMembership, TeamRole
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
