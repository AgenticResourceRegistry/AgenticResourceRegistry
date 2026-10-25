# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""With draft installs allowed, the creator can install their pending agent again by version."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.routes.agent.install import install_agent
from models.agent import AgentStatus
from schemas.agent import AgentInstallRequest
from tests import discovery_support as ds


@pytest.fixture
async def session():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    async with maker() as db:
        yield db
    await engine.dispose()


async def _install(db, agent, user, *, drafts_allowed: bool = True, **request):
    with (
        patch("api.routes.agent.install._ds.get_sync_bool", return_value=drafts_allowed),
        patch("api.routes.agent.install._ds.get_bool", AsyncMock(return_value=False)),
        patch("api.routes.config.derive_endpoints", AsyncMock(return_value={"api": "http://observal.test"})),
        patch("services.download_tracker.record_agent_download", AsyncMock()),
    ):
        return await install_agent(
            str(agent.id),
            AgentInstallRequest(harness="claude-code", options={"scope": "project"}, **request),
            request=None,
            db=db,
            current_user=user,
        )


async def test_creator_reinstalls_pending_agent_by_its_version(session):
    owner = await ds.user(session)
    agent = await ds.agent(session, owner, status=AgentStatus.pending)
    await session.commit()

    first = await _install(session, agent, owner)
    # A re-pull pins the version installed before; that request used to 404.
    again = await _install(session, agent, owner, version=first.version)

    assert (first.version, again.version) == ("3.1.0", "3.1.0")


async def test_pending_version_stays_hidden_from_other_users(session):
    owner = await ds.user(session)
    other = await ds.user(session)
    agent = await ds.agent(session, owner, status=AgentStatus.pending)
    await session.commit()

    with pytest.raises(HTTPException) as refused:
        await _install(session, agent, other, version="3.1.0")

    assert refused.value.status_code == 404


async def test_pending_version_needs_draft_installs_enabled(session):
    owner = await ds.user(session)
    agent = await ds.agent(session, owner, status=AgentStatus.pending)
    await session.commit()

    with pytest.raises(HTTPException) as refused:
        await _install(session, agent, owner, drafts_allowed=False, version="3.1.0")

    assert refused.value.status_code == 404
