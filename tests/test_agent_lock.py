# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The agent lock service: digests, pin resolution, pinned content, and lock documents."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from models.agent import AgentVersion
from models.agent_component import AgentComponent
from models.mcp import ListingStatus, McpVersion
from models.skill import SkillVersion
from models.user import UserRole
from services import agent_lock
from services.skill_revisions import skill_content_revision
from tests import discovery_support as ds


@pytest.fixture
async def session():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    async with maker() as db:
        yield db
    await engine.dispose()


async def _mcp_version(db, listing, owner, version, *, status=ListingStatus.approved, args=None, set_latest=True):
    row = McpVersion(
        id=uuid.uuid4(),
        listing_id=listing.id,
        version=version,
        description="GitHub tools",
        status=status,
        transport="stdio",
        command="npx",
        args=args or ["-y", f"@acme/github@{version}"],
        environment_variables=[{"name": "GITHUB_TOKEN", "required": True}],
        released_by=owner.id,
        released_at=ds.NOW,
    )
    db.add(row)
    await db.flush()
    if set_latest:
        listing.latest_version_id = row.id
        await db.flush()
    return row


def _mcp_row(**overrides):
    values = {
        "description": "d",
        "supported_harnesses": ["kiro"],
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "pkg@1"],
        "environment_variables": [{"name": "TOKEN", "required": True, "value": "secret-a"}],
        "mcp_validated": False,
        "tools_schema": None,
        "status": ListingStatus.pending,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


# ── Digest ───────────────────────────────────────────────────────────────


def test_digest_ignores_review_metadata_and_environment_values():
    base = agent_lock.content_digest("mcp", _mcp_row())

    assert base.startswith("sha256:")
    assert agent_lock.content_digest("mcp", _mcp_row(status=ListingStatus.approved, mcp_validated=True)) == base
    assert agent_lock.content_digest("mcp", _mcp_row(tools_schema={"tools": [{"name": "x"}]})) == base
    secret_changed = _mcp_row(environment_variables=[{"name": "TOKEN", "required": True, "value": "secret-b"}])
    assert agent_lock.content_digest("mcp", secret_changed) == base


def test_digest_changes_with_install_content():
    base = agent_lock.content_digest("mcp", _mcp_row())

    assert agent_lock.content_digest("mcp", _mcp_row(args=["-y", "pkg@2"])) != base
    assert agent_lock.content_digest("mcp", _mcp_row(environment_variables=[{"name": "OTHER"}])) != base


def test_resource_skill_v2_digest_binds_all_v1_fields_and_decoded_file_modes():
    row = SimpleNamespace(
        delivery_mode="registry_direct",
        description="Description",
        supported_harnesses=["pi"],
        git_url=None,
        git_ref=None,
        skill_path="/",
        slash_command=None,
        task_type="general",
        target_agents=[],
        script_content=None,
        script_filename=None,
        skill_md_content="---\nname: example\ndescription: Example\n---\n# Example\n",
        extra_files=[{"path": "scripts/run.sh", "content": "echo test", "executable": True}],
    )
    original = agent_lock.content_digest("skill", row)
    assert original.startswith("observal-content-v2:sha256:")
    row.extra_files = [{"path": "scripts/run.sh", "content": "ZWNobyB0ZXN0", "encoding": "base64", "executable": True}]
    assert agent_lock.content_digest("skill", row) == original  # Same decoded bytes.
    row.extra_files[0]["executable"] = False
    assert agent_lock.content_digest("skill", row) != original
    row.extra_files[0]["executable"] = True
    row.description = "Changed installation metadata"
    assert agent_lock.content_digest("skill", row) != original
    row.description = "Description"
    row.extra_files = []
    assert agent_lock.content_digest("skill", row).startswith("sha256:")  # Legacy v1 unchanged.
    row.script_filename, row.script_content = "empty.sh", ""
    assert agent_lock.content_digest("skill", row).startswith("observal-content-v2:sha256:")


# ── Latest release ───────────────────────────────────────────────────────


def test_latest_release_uses_semver_and_skips_prereleases_and_unapproved_rows():
    def row(version, status=ListingStatus.approved):
        return SimpleNamespace(version=version, status=status)

    rows = [
        row("1.9.0"),
        row("1.10.0"),
        row("2.0.0-beta.1"),
        row("3.0.0", ListingStatus.pending),
        row("0.1.0", ListingStatus.rejected),
    ]

    assert agent_lock.latest_release(rows).version == "1.10.0"
    assert agent_lock.latest_release([row("1.0.0"), row("1.1.0", ListingStatus.archived)]).version == "1.1.0"
    assert agent_lock.latest_release([row("1.0.0", ListingStatus.pending)]) is None


# ── Write path ───────────────────────────────────────────────────────────


async def test_attach_pins_latest_release_explicit_version_and_carries_previous_pins(session):
    owner = await ds.user(session)
    listing = await ds.mcp(session, owner)  # 1.4.2
    await _mcp_version(session, listing, owner, "2.0.0")
    latest = await agent_lock.attach_pinned_components(
        session, uuid.uuid4(), [{"component_type": "mcp", "component_id": listing.id}]
    )
    explicit = await agent_lock.attach_pinned_components(
        session, uuid.uuid4(), [{"component_type": "mcp", "component_id": listing.id, "version": "1.4.2"}]
    )
    await _mcp_version(session, listing, owner, "3.0.0")
    carried = await agent_lock.attach_pinned_components(
        session, uuid.uuid4(), [{"component_type": "mcp", "component_id": listing.id}], previous=explicit
    )
    refreshed = await agent_lock.attach_pinned_components(
        session, uuid.uuid4(), [{"component_type": "mcp", "component_id": listing.id}], previous=explicit, refresh=True
    )

    assert [c.resolved_version for c in (*latest, *explicit, *carried, *refreshed)] == [
        "2.0.0",
        "1.4.2",
        "1.4.2",
        "3.0.0",
    ]
    assert latest[0].resolved_version_id is not None
    assert latest[0].resolved_digest.startswith("sha256:")
    assert carried[0].resolved_version_id == explicit[0].resolved_version_id


async def test_attach_pins_owner_pending_version_only_when_approval_is_not_required(session):
    owner = await ds.user(session)
    listing = await ds.mcp(session, owner, status=ListingStatus.pending)
    refs = [{"component_type": "mcp", "component_id": listing.id}]

    pinned = await agent_lock.attach_pinned_components(session, uuid.uuid4(), refs, current_user=owner)
    with pytest.raises(HTTPException) as error:
        await agent_lock.attach_pinned_components(
            session, uuid.uuid4(), refs, require_approved=True, current_user=owner
        )

    assert pinned[0].resolved_version == "1.4.2"
    assert error.value.status_code == 400
    assert error.value.detail[0]["reason"] == "mcp has no approved version to pin"


async def test_attach_never_pins_an_unapproved_release_the_caller_may_not_read(session):
    """A pin renders the release into the agent's snapshot and install, so it
    follows the unapproved-version read rule: owners, admins and reviewers only."""
    owner = await ds.user(session)
    other = await ds.user(session)
    reviewer = await ds.user(session, role=UserRole.reviewer)
    listing = await ds.mcp(session, owner)  # approved 1.4.2
    await _mcp_version(session, listing, owner, "2.0.0", status=ListingStatus.pending, set_latest=False)
    requested = [{"component_type": "mcp", "component_id": listing.id, "version": "2.0.0"}]

    for caller in (other, None):
        for require_approved in (False, True):
            with pytest.raises(HTTPException) as error:
                await agent_lock.attach_pinned_components(
                    session, uuid.uuid4(), requested, require_approved=require_approved, current_user=caller
                )
            # Reported exactly like a version that does not exist: no status leak.
            assert error.value.detail[0]["reason"] == "mcp version '2.0.0' does not exist"

    by_owner = await agent_lock.attach_pinned_components(session, uuid.uuid4(), requested, current_user=owner)
    by_reviewer = await agent_lock.attach_pinned_components(session, uuid.uuid4(), requested, current_user=reviewer)
    assert [by_owner[0].resolved_version, by_reviewer[0].resolved_version] == ["2.0.0", "2.0.0"]


async def test_attach_does_not_fall_back_to_someone_elses_pending_release(session):
    owner = await ds.user(session)
    other = await ds.user(session)
    listing = await ds.mcp(session, owner, status=ListingStatus.pending)

    with pytest.raises(HTTPException) as error:
        await agent_lock.attach_pinned_components(
            session, uuid.uuid4(), [{"component_type": "mcp", "component_id": listing.id}], current_user=other
        )

    assert error.value.detail[0]["reason"] == "mcp has no approved version to pin"


async def test_attach_rejects_an_unknown_requested_version_without_adding_rows(session):
    owner = await ds.user(session)
    listing = await ds.mcp(session, owner)

    with pytest.raises(HTTPException) as error:
        await agent_lock.attach_pinned_components(
            session, uuid.uuid4(), [{"component_type": "mcp", "component_id": listing.id, "version": "9.9.9"}]
        )

    assert error.value.detail[0]["reason"] == "mcp version '9.9.9' does not exist"
    assert not session.new


# ── Read path ────────────────────────────────────────────────────────────


def test_pinned_listing_keeps_listing_identity_and_serves_pinned_content():
    listing_id = uuid.uuid4()
    listing = SimpleNamespace(id=listing_id, name="GitHub", status=ListingStatus.archived, command="latest")
    version = SimpleNamespace(id=uuid.uuid4(), command="pinned", source_url="https://git.example/pinned")
    proxy = agent_lock.PinnedListing(listing, version)

    # SimpleNamespace has no __table__, so only the fixed identity extras apply;
    # real listings route every listing column (including id) to the listing.
    assert proxy.command == "pinned"
    assert proxy.git_url == "https://git.example/pinned"
    assert proxy.name == "GitHub"
    assert proxy.listing_status == ListingStatus.archived


async def test_pinned_listing_routes_orm_listing_columns_to_the_listing(session):
    owner = await ds.user(session)
    listing = await ds.mcp(session, owner)
    pinned = await _mcp_version(session, listing, owner, "0.9.0", set_latest=False)

    proxy = agent_lock.PinnedListing(listing, pinned)

    assert proxy.id == listing.id
    assert proxy.slug == listing.slug
    assert proxy.version == "0.9.0"
    assert proxy.args == ["-y", "@acme/github@0.9.0"]


async def test_load_reports_fallbacks_digest_mismatches_and_unapproved_pins(session):
    owner = await ds.user(session)
    locked = await ds.mcp(session, owner, name="Locked")
    legacy = await ds.mcp(session, owner, name="Legacy")
    tampered = await ds.mcp(session, owner, name="Tampered")
    locked_row = await _mcp_version(session, locked, owner, "0.9.0", set_latest=False)
    components = [
        SimpleNamespace(
            component_type="mcp",
            component_id=locked.id,
            resolved_version="0.9.0",
            resolved_version_id=locked_row.id,
            resolved_digest=agent_lock.content_digest("mcp", locked_row),
        ),
        SimpleNamespace(
            component_type="mcp",
            component_id=legacy.id,
            resolved_version="latest",
            resolved_version_id=None,
            resolved_digest=None,
        ),
        SimpleNamespace(
            component_type="mcp",
            component_id=tampered.id,
            resolved_version="1.4.2",
            resolved_version_id=None,
            resolved_digest="sha256:" + "0" * 64,
        ),
    ]
    listings = {"mcp": {row.id: row for row in (locked, legacy, tampered)}}

    loaded = await agent_lock.load_pinned_listings(session, components, listings)

    assert [entry["source"] for entry in loaded.entries] == ["lock", "fallback-latest", "version"]
    assert loaded.status == "partial"
    assert loaded.listings["mcp"][locked.id].version == "0.9.0"
    assert loaded.listings["mcp"][legacy.id].version == "1.4.2"
    assert loaded.problems == ["mcp 'Legacy' is not locked", "mcp 'Tampered' 1.4.2 changed after it was locked"]


async def test_resource_skill_requires_exact_v2_pin_even_in_non_strict_mode(session):
    owner = await ds.user(session)
    listing = await ds.skill(session, owner)
    row = (await session.execute(select(SkillVersion))).scalar_one()
    original_v1 = agent_lock.content_digest("skill", row)
    row.extra_files = [{"path": "templates/new.txt", "content": "new"}]
    component = SimpleNamespace(
        component_type="skill",
        component_id=listing.id,
        resolved_version=row.version,
        resolved_version_id=row.id,
        resolved_digest=original_v1,
    )
    with pytest.raises(HTTPException, match="pinned folder differs") as refused:
        await agent_lock.load_pinned_listings(session, [component], {"skill": {listing.id: listing}})
    assert refused.value.status_code == 409
    row.content_revision = skill_content_revision(listing, row)
    component.resolved_digest = agent_lock.content_digest("skill", row)
    loaded = await agent_lock.load_pinned_listings(session, [component], {"skill": {listing.id: listing}})
    assert loaded.entries[0]["digest"] == component.resolved_digest
    assert loaded.problems == []
    component.resolved_version_id = None
    with pytest.raises(HTTPException, match="exact v2 version pin"):
        await agent_lock.load_pinned_listings(session, [component], {"skill": {listing.id: listing}})


async def test_agent_review_blockers_reject_resource_bundle_with_old_digest(session):
    owner = await ds.user(session)
    listing = await ds.skill(session, owner)
    row = (await session.execute(select(SkillVersion))).scalar_one()
    v1 = agent_lock.content_digest("skill", row)
    row.extra_files = [{"path": "templates/new.txt", "content": "new"}]
    component = SimpleNamespace(
        component_type="skill",
        component_id=listing.id,
        component_name="example",
        resolved_version=row.version,
        resolved_version_id=row.id,
        resolved_digest=v1,
    )
    blockers = await agent_lock.pinned_component_blockers(session, [component])
    assert blockers[0]["status"] == "invalid_bundle_pin"
    row.content_revision = skill_content_revision(listing, row)
    component.resolved_digest = agent_lock.content_digest("skill", row)
    assert await agent_lock.pinned_component_blockers(session, [component]) == []


async def test_agent_review_blocks_v2_skill_pin_after_stored_files_are_stripped(session):
    owner = await ds.user(session)
    listing = await ds.skill(session, owner)
    row = (await session.execute(select(SkillVersion))).scalar_one()
    row.extra_files = [{"path": "templates/new.txt", "content": "new"}]
    component = SimpleNamespace(
        component_type="skill",
        component_id=listing.id,
        component_name="example",
        resolved_version=row.version,
        resolved_version_id=row.id,
        resolved_digest=agent_lock.content_digest("skill", row),
    )
    row.extra_files = []
    blockers = await agent_lock.pinned_component_blockers(session, [component])
    assert [item["status"] for item in blockers] == ["invalid_bundle_pin"]


async def test_lock_document_is_persisted_and_digest_is_stable(session):
    owner = await ds.user(session)
    skill = await ds.skill(session, owner, version="1.2.0")
    agent = await ds.agent(session, owner, components=[("skill", skill.id, "Security Review")])
    component = (await session.execute(select(AgentComponent))).scalar_one()
    component.resolved_version = "1.2.0"
    version = (await session.execute(select(AgentVersion))).scalar_one()

    first = await agent_lock.lock_agent_version(session, agent, version)
    second = await agent_lock.build_lock_document(session, agent, version)

    stored = json.loads(version.lock_snapshot)
    assert stored["lock_version"] == 1
    assert stored["status"] == "locked"
    assert stored["components"][0]["version"] == "1.2.0"
    assert stored["components"][0]["qualified_name"] == f"acme/{skill.slug}"
    assert component.resolved_version_id == skill.latest_version_id
    assert first["digest"] == second["digest"]


async def test_freshness_and_blockers_follow_the_pinned_version(session):
    owner = await ds.user(session)
    skill = await ds.skill(session, owner, version="1.0.0")
    pinned_row = (await session.execute(select(SkillVersion))).scalar_one()
    await ds.add_skill_version(session, skill, owner, version="1.1.0", status=ListingStatus.approved)
    pending_row = await ds.add_skill_version(session, skill, owner, version="2.0.0", status=ListingStatus.pending)
    current = SimpleNamespace(
        component_type="skill",
        component_id=skill.id,
        component_name="",
        resolved_version="1.0.0",
        resolved_version_id=pinned_row.id,
    )
    pending = SimpleNamespace(
        component_type="skill",
        component_id=skill.id,
        component_name="",
        resolved_version="2.0.0",
        resolved_version_id=pending_row.id,
    )

    freshness = await agent_lock.pin_freshness(session, [current])
    blockers = await agent_lock.pinned_component_blockers(session, [current, pending])

    assert freshness[0]["pinned_version"] == "1.0.0"
    assert freshness[0]["latest_version"] == "1.1.0"
    assert freshness[0]["outdated"] is True
    assert [(b["version"], b["status"]) for b in blockers] == [("2.0.0", "pending")]
