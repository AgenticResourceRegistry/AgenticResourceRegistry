# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Agent routes pin components through the lock service against a real registry."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from api.routes import agent_versions, bulk
from models.agent import AgentVersion
from models.agent_component import AgentComponent
from models.mcp import ListingStatus, McpVersion
from schemas.agent import AgentVersionCreateRequest, ComponentRef
from schemas.bulk import BulkAgentItem, BulkAgentRequest
from tests import discovery_support as ds


def test_agent_pin_column_accepts_versioned_skill_folder_digest():
    digest = "observal-content-v2:sha256:" + "0" * 64
    assert AgentComponent.__table__.columns.resolved_digest.type.length >= len(digest)


@pytest.fixture
async def registry(monkeypatch):
    monkeypatch.setattr(agent_versions.inbox, "on_publish", AsyncMock())
    monkeypatch.setattr(bulk.inbox, "on_publish", AsyncMock())
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    async with maker() as db:
        owner = await ds.user(db)
        mcp = await ds.mcp(db, owner)  # 1.4.2
        agent = await ds.agent(db, owner, components=[("mcp", mcp.id, "GitHub")])
        link = (await db.execute(select(AgentComponent))).scalar_one()
        link.resolved_version = "1.4.2"
        link.resolved_version_id = mcp.latest_version_id
        await db.commit()
        yield db, owner, mcp, agent
    await engine.dispose()


async def _approve_mcp_release(db, listing, owner, version: str) -> McpVersion:
    row = McpVersion(
        id=uuid.uuid4(),
        listing_id=listing.id,
        version=version,
        description="GitHub tools",
        status=ListingStatus.approved,
        transport="stdio",
        command="npx",
        args=["-y", f"@acme/github@{version}"],
        released_by=owner.id,
        released_at=ds.NOW,
    )
    db.add(row)
    await db.flush()
    listing.latest_version_id = row.id
    await db.commit()
    return row


async def _release(db, owner, agent, mcp, version: str, **options) -> AgentVersion:
    ref = {"component_type": "mcp", "component_id": mcp.id, **options.pop("ref", {})}
    request = AgentVersionCreateRequest(
        version=version,
        description="release",
        prompt="Review carefully.",
        model_name="claude-sonnet-4",
        components=[ComponentRef(**ref)],
        **options,
    )
    await agent_versions._create_agent_version(str(agent.id), request, db, owner)
    return (
        await db.execute(select(AgentVersion).where(AgentVersion.agent_id == agent.id, AgentVersion.version == version))
    ).scalar_one()


async def _pins(db, version: AgentVersion) -> list[tuple[str, uuid.UUID | None]]:
    rows = (
        (await db.execute(select(AgentComponent).where(AgentComponent.agent_version_id == version.id))).scalars().all()
    )
    return [(row.resolved_version, row.resolved_version_id) for row in rows]


async def test_release_keeps_existing_pins_when_components_have_new_versions(registry):
    db, owner, mcp, agent = registry
    original = mcp.latest_version_id
    await _approve_mcp_release(db, mcp, owner, "2.0.0")

    released = await _release(db, owner, agent, mcp, "3.2.0")

    assert await _pins(db, released) == [("1.4.2", original)]
    lock = json.loads(released.lock_snapshot)
    assert lock["components"][0]["version"] == "1.4.2"
    assert lock["components"][0]["digest"].startswith("sha256:")


async def test_release_refresh_moves_unversioned_components_to_latest(registry):
    db, owner, mcp, agent = registry
    newest = await _approve_mcp_release(db, mcp, owner, "2.0.0")

    released = await _release(db, owner, agent, mcp, "3.2.0", refresh_components=True)

    assert await _pins(db, released) == [("2.0.0", newest.id)]


async def test_release_honours_an_explicit_component_version(registry):
    db, owner, mcp, agent = registry
    await _approve_mcp_release(db, mcp, owner, "2.0.0")
    middle = await _approve_mcp_release(db, mcp, owner, "1.5.0")

    released = await _release(db, owner, agent, mcp, "3.2.0", ref={"version": "1.5.0"})

    assert await _pins(db, released) == [("1.5.0", middle.id)]


async def test_bulk_create_pins_the_real_release_instead_of_latest(registry):
    db, owner, mcp, _agent = registry
    request = BulkAgentRequest(
        agents=[
            BulkAgentItem(
                name="bulk-reviewer",
                version="1.0.0",
                owner="alice",
                prompt="Review.",
                model_name="claude-sonnet-4",
                components=[{"component_type": "mcp", "component_id": str(mcp.id)}],
            )
        ]
    )

    result = await bulk.bulk_create_agents(request, db, owner)

    assert result.created == 1
    created = (
        await db.execute(select(AgentVersion).join(AgentVersion.agent).where(AgentVersion.version == "1.0.0"))
    ).scalar_one()
    assert await _pins(db, created) == [("1.4.2", mcp.latest_version_id)]


async def test_yaml_snapshot_describes_the_pinned_prompt_release(registry):
    from models.prompt import PromptVersion
    from services.agent_snapshot import build_yaml_snapshot

    db, owner, _mcp, agent = registry
    prompt = await ds.prompt(db, owner)  # 0.3.0 "Write release notes for: {{changes}}"
    version = AgentVersion(
        id=uuid.uuid4(),
        agent_id=agent.id,
        version="5.0.0",
        description="notes",
        prompt="Summarise.",
        model_name="claude-sonnet-4",
        released_by=owner.id,
        released_at=ds.NOW,
    )
    db.add(version)
    await db.flush()
    db.add(
        AgentComponent(
            agent_version_id=version.id,
            component_type="prompt",
            component_id=prompt.id,
            component_name="Release Notes",
            resolved_version="0.3.0",
        )
    )
    newer = PromptVersion(
        id=uuid.uuid4(),
        listing_id=prompt.id,
        version="1.0.0",
        description="rewrite",
        status=ListingStatus.approved,
        category="documentation",
        template="A DIFFERENT TEMPLATE",
        released_by=owner.id,
        released_at=ds.NOW,
    )
    db.add(newer)
    await db.flush()
    prompt.latest_version_id = newer.id

    snapshot = await build_yaml_snapshot(version, db)

    assert "Write release notes for" in snapshot
    assert "A DIFFERENT TEMPLATE" not in snapshot


async def test_draft_cannot_pin_and_render_another_users_unreviewed_release(registry):
    """Regression: a draft pinned someone else's pending prompt release and then
    read its template back through the draft's YAML snapshot."""
    from fastapi import HTTPException

    from api.routes.agent import draft
    from models.prompt import PromptVersion
    from schemas.agent import AgentCreateRequest

    db, owner, _mcp, _agent = registry
    prompt = await ds.prompt(db, owner)  # approved 0.3.0
    db.add(
        PromptVersion(
            id=uuid.uuid4(),
            listing_id=prompt.id,
            version="1.0.0",
            description="not reviewed yet",
            status=ListingStatus.pending,
            category="documentation",
            template="UNREVIEWED TEMPLATE",
            released_by=owner.id,
            released_at=ds.NOW,
        )
    )
    intruder = await ds.user(db)
    await db.commit()
    prompt_id, owner_id = prompt.id, owner.id

    def request(name: str) -> AgentCreateRequest:
        return AgentCreateRequest(
            name=name,
            version="0.1.0",
            owner="someone",
            description="probe",
            prompt="probe",
            model_name="claude-sonnet-4",
            components=[ComponentRef(component_type="prompt", component_id=prompt_id, version="1.0.0")],
        )

    with pytest.raises(HTTPException) as error:
        await draft.save_draft(request("probe"), db, intruder)
    # A request that fails is rolled back, never committed.
    await db.rollback()
    owner = await db.get(type(owner), owner_id)

    assert error.value.status_code == 400
    assert error.value.detail[0]["reason"] == "prompt version '1.0.0' does not exist"
    snapshots = (await db.execute(select(AgentVersion.yaml_snapshot))).scalars().all()
    assert not any("UNREVIEWED TEMPLATE" in (snapshot or "") for snapshot in snapshots)

    # The component's owner, building the agent and the release together, still can.
    await draft.save_draft(request("own-draft"), db, owner)
    snapshots = (await db.execute(select(AgentVersion.yaml_snapshot))).scalars().all()
    assert any("UNREVIEWED TEMPLATE" in (snapshot or "") for snapshot in snapshots)


async def test_version_review_requires_approved_pins_and_freezes_the_lock(registry, monkeypatch):
    from models.user import UserRole
    from schemas.agent import AgentVersionReviewRequest

    db, owner, mcp, agent = registry
    monkeypatch.setattr(agent_versions.inbox, "on_review_decided", AsyncMock())
    pending = McpVersion(
        id=uuid.uuid4(),
        listing_id=mcp.id,
        version="2.0.0",
        description="pending release",
        status=ListingStatus.pending,
        transport="stdio",
        command="npx",
        args=["-y", "@acme/github@2.0.0"],
        released_by=owner.id,
        released_at=ds.NOW,
    )
    db.add(pending)
    await db.flush()
    candidate = AgentVersion(
        id=uuid.uuid4(),
        agent_id=agent.id,
        version="4.0.0",
        description="candidate",
        prompt="Review.",
        model_name="claude-sonnet-4",
        status=agent_versions.AgentStatus.pending,
        released_by=owner.id,
        released_at=ds.NOW,
    )
    db.add(candidate)
    await db.flush()
    db.add(
        AgentComponent(
            agent_version_id=candidate.id,
            component_type="mcp",
            component_id=mcp.id,
            component_name="GitHub",
            resolved_version="2.0.0",
            resolved_version_id=pending.id,
        )
    )
    await db.commit()
    reviewer = await ds.user(db, role=UserRole.admin)
    approve = AgentVersionReviewRequest(action="approve")

    with pytest.raises(agent_versions.HTTPException) as blocked:
        await agent_versions._review_agent_version(str(agent.id), "4.0.0", approve, db, reviewer)
    pending.status = ListingStatus.approved
    await db.commit()
    await agent_versions._review_agent_version(str(agent.id), "4.0.0", approve, db, reviewer)

    assert blocked.value.status_code == 422
    assert blocked.value.detail["blocking_components"][0]["version"] == "2.0.0"
    await db.refresh(candidate)
    lock = json.loads(candidate.lock_snapshot)
    assert (lock["agent"]["version"], lock["status"], lock["components"][0]["version"]) == ("4.0.0", "locked", "2.0.0")


async def test_exact_agent_version_review_refuses_public_agent_with_private_skill(monkeypatch):
    from fastapi import HTTPException

    from models.agent import AgentStatus
    from models.user import UserRole
    from schemas.agent import AgentVersionReviewRequest

    monkeypatch.setattr(agent_versions.inbox, "on_review_decided", AsyncMock())
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            reviewer = await ds.user(db, role=UserRole.admin)
            team = await ds.team_with_member(db, owner)
            skill = await ds.skill(db, owner, team_id=team.id, is_private=True)
            agent = await ds.agent(db, owner, status=AgentStatus.pending, components=[("skill", skill.id, skill.name)])
            await db.commit()

            with pytest.raises(HTTPException) as blocked:
                await agent_versions._review_agent_version(
                    str(agent.id), "3.1.0", AgentVersionReviewRequest(action="approve"), db, reviewer
                )
            assert blocked.value.status_code == 422
            assert any(item["status"] == "not_public" for item in blocked.value.detail["blocking_components"])
    finally:
        await engine.dispose()


async def test_lock_endpoint_serves_the_frozen_snapshot_and_builds_one_for_legacy_versions(registry):
    from services.agent_lock import lock_agent_version

    db, owner, _mcp, agent = registry
    version = (await db.execute(select(AgentVersion).where(AgentVersion.agent_id == agent.id))).scalar_one()

    legacy = await agent_versions._get_agent_version_lock(str(agent.id), "3.1.0", db, owner)
    await lock_agent_version(db, agent, version)
    await db.commit()
    frozen = await agent_versions._get_agent_version_lock(str(agent.id), "3.1.0", db, owner)

    assert version.lock_snapshot is not None
    assert frozen == json.loads(version.lock_snapshot)
    assert legacy["digest"] == frozen["digest"]
    assert frozen["components"][0]["version"] == "1.4.2"


async def test_release_harness_configs_come_from_the_pinned_mcp_release(registry):
    """Release-time configs are stored and served, so they must match the lock."""
    db, owner, mcp, agent = registry
    await _approve_mcp_release(db, mcp, owner, "2.0.0")

    version = await _release(db, owner, agent, mcp, "3.2.0", supported_harnesses=["claude-code"])

    assert [pinned for pinned, _id in await _pins(db, version)] == ["1.4.2"]
    config = json.dumps(version.harness_configs["claude-code"])
    assert "@modelcontextprotocol/server-github" in config
    assert "@acme/github@2.0.0" not in config


async def test_outdated_endpoint_reports_pins_behind_the_latest_release(registry):
    db, owner, mcp, agent = registry
    before = await agent_versions._get_agent_version_outdated(str(agent.id), "3.1.0", db, owner)
    await _approve_mcp_release(db, mcp, owner, "2.0.0")
    after = await agent_versions._get_agent_version_outdated(str(agent.id), "3.1.0", db, owner)

    assert before["summary"] == {"total": 1, "outdated": 0, "unlocked": 0, "archived": 0}
    assert after["summary"]["outdated"] == 1
    assert (after["components"][0]["pinned_version"], after["components"][0]["latest_version"]) == ("1.4.2", "2.0.0")
