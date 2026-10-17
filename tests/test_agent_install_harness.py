# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Agent installs only target harnesses the agent version lists as supported (OBS-1421).

The install route used to generate config for any harness. Pulling an agent
for a harness outside its ``supported_harnesses`` succeeded silently and left a
partial install: components the harness has no place for, or puts where it
never loads them.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from models.agent import AgentVersion
from tests import discovery_support as ds
from tests.test_agent_install_pins import _install, _pinned_agent


@pytest.fixture
async def session():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    async with maker() as db:
        yield db
    await engine.dispose()


async def _agent(db, kind: str, supported: list[str]):
    owner = await ds.user(db)
    _listing, agent, _marker = await _pinned_agent(db, owner, kind)
    version = (await db.execute(select(AgentVersion))).scalar_one()
    version.supported_harnesses = supported
    await db.commit()
    return owner, agent


async def test_install_refuses_a_harness_the_agent_version_does_not_support(session):
    owner, agent = await _agent(session, "mcp", ["claude-code", "kiro"])
    with pytest.raises(HTTPException) as refused:
        await _install(session, agent, owner, harness="pi")
    assert refused.value.status_code == 409
    assert refused.value.detail.startswith(
        f"Agent {agent.name} 3.1.0 does not support pi; it supports claude-code, kiro. Install it for one of those"
    )
    assert "--allow-unsupported-harness" in refused.value.detail


async def test_allowed_unsupported_harness_installs_with_a_warning(session):
    owner, agent = await _agent(session, "mcp", ["claude-code", "kiro"])
    response = await _install(session, agent, owner, harness="pi", allow_unsupported_harness=True)
    assert response.harness == "pi" and response.config_snippet
    assert response.warnings[0] == (
        f"Agent {agent.name} 3.1.0 does not list pi as supported (it supports claude-code, kiro). "
        "Components may be missing or not loaded; check the install before relying on it."
    )


async def test_warning_names_components_the_harness_cannot_install(session):
    # Cursor has no skills capability.
    owner, agent = await _agent(session, "skill", ["claude-code"])
    response = await _install(session, agent, owner, harness="cursor", allow_unsupported_harness=True)
    assert "cursor cannot install its skill components." in response.warnings[0]


@pytest.mark.parametrize(("supported", "harness"), [(["claude-code", "kiro"], "kiro"), ([], "pi")])
async def test_supported_or_unrestricted_harness_installs_without_a_warning(session, supported, harness):
    owner, agent = await _agent(session, "mcp", supported)
    response = await _install(session, agent, owner, harness=harness)
    assert not any("as supported" in warning for warning in response.warnings)
