# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Optional real-PostgreSQL row-contention test for skill review decisions.

Run with OBSERVAL_TEST_POSTGRES_URL pointing at a disposable test database.
Each test creates and removes its own schema; never point it at production.
"""

import asyncio
import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.routes import agent_versions, component_versions, registry, review, skill, skill_files
from api.routes._skill_lock import lock_skill_version
from api.routes.agent.install import install_agent
from models.agent import Agent, AgentStatus, AgentVersion
from models.agent_component import AgentComponent
from models.base import Base
from models.component_bundle import ComponentBundle
from models.mcp import ListingStatus
from models.skill import SkillListing, SkillVersion
from models.team import TeamMembership, TeamRole
from models.user import User, UserRole
from schemas.agent import AgentInstallRequest, AgentVersionReviewRequest
from schemas.component_version import VersionPublishRequest, VersionReviewRequest
from schemas.skill import SkillCandidateDraftRequest
from schemas.skill_resources import SkillDraftRebaseRequest, SkillFileOperations
from services.agent_lock import lock_agent_version
from services.skill_revisions import skill_content_revision
from tests import discovery_support as ds

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def pg_store():
    url = os.environ.get("OBSERVAL_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set OBSERVAL_TEST_POSTGRES_URL to an isolated PostgreSQL test database")
    schema = f"phase2_{uuid.uuid4().hex}"
    admin_engine = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": f"{schema},public"}})
    try:
        async with admin_engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(text(f'CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA "{schema}"'))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=[*ds.TABLES, ComponentBundle.__table__])
        yield async_sessionmaker(engine, expire_on_commit=False), engine
    finally:
        await engine.dispose()
        async with admin_engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()


@pytest.mark.parametrize("author_path", ["draft", "publish"])
async def test_author_snapshot_refuses_pointer_advanced_during_listing_lock(pg_store, author_path):
    maker, engine = pg_store
    md = "---\nname: example\ndescription: Reviewed\n---\n# Example\n"
    async with maker() as db:
        owner = await ds.user(db)
        listing = await ds.skill(db, owner, status=ListingStatus.approved, content=md)
        old = await db.get(SkillVersion, listing.latest_version_id)
        next_release = await ds.add_skill_version(
            db, listing, owner, version="1.3.0", status=ListingStatus.approved, content=md, set_latest=False
        )
        revision = skill_content_revision(listing, old)
        await db.commit()
        listing_id, old_id, next_id, owner_id = listing.id, old.id, next_release.id, owner.id

    async with maker() as writer, maker() as authoring:
        await lock_skill_version(writer, listing_id, old_id)
        listing = await writer.get(SkillListing, listing_id)
        listing.latest_version_id = next_id
        await writer.flush()
        attempted = asyncio.Event()

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def observe_author_lock(_conn, _cursor, statement, _params, _context, _many):
            if "skill_listings.latest_version_id" in statement and "FOR UPDATE" in statement.upper():
                attempted.set()

        owner = await authoring.get(User, owner_id)
        if author_path == "draft":
            action = skill_files.create_skill_candidate_draft(
                str(listing_id),
                SkillCandidateDraftRequest(
                    base_version_id=old_id,
                    observed_base_revision=revision,
                    version="1.4.0",
                    description="New draft",
                ),
                Response(),
                authoring,
                owner,
            )
        else:
            action = component_versions._publish_version(
                str(listing_id),
                VersionPublishRequest(version="1.4.0", description="One shot"),
                SkillListing,
                SkillVersion,
                "skill",
                authoring,
                owner,
            )
        task = asyncio.create_task(action)
        try:
            await asyncio.wait_for(attempted.wait(), timeout=5)
            assert not task.done(), "author must wait for concurrent release writer"
            await writer.commit()
            with pytest.raises(HTTPException, match="Skill latest release changed") as blocked:
                await asyncio.wait_for(task, timeout=5)
            assert blocked.value.status_code == 409
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await writer.rollback()
            await authoring.rollback()
    async with maker() as db:
        assert (
            await db.scalar(
                select(SkillVersion.id).where(SkillVersion.listing_id == listing_id, SkillVersion.version == "1.4.0")
            )
        ) is None


async def test_legacy_review_rechecks_pending_count_after_author_lock_wait(pg_store):
    maker, engine = pg_store
    async with maker() as db:
        owner = await ds.user(db)
        reviewer = await ds.user(db, role=UserRole.admin)
        listing = await ds.skill(db, owner, status=ListingStatus.pending)
        await db.commit()
        listing_id, version_id, owner_id, reviewer_id = listing.id, listing.latest_version_id, owner.id, reviewer.id

    async with maker() as writer, maker() as reviewing:
        stale = (await reviewing.execute(select(SkillListing).where(SkillListing.id == listing_id))).scalar_one()
        assert [version.id for version in stale.versions if version.status == ListingStatus.pending] == [version_id]
        await lock_skill_version(writer, listing_id, version_id)
        owner = await writer.get(User, owner_id)
        await ds.add_skill_version(
            writer,
            await writer.get(SkillListing, listing_id),
            owner,
            version="1.3.0",
            status=ListingStatus.pending,
            set_latest=False,
        )
        await writer.flush()
        attempted = asyncio.Event()

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def observe_review_lock(_conn, _cursor, statement, _params, _context, _many):
            if "skill_listings.id" in statement and "FOR UPDATE" in statement.upper():
                attempted.set()

        reviewer = await reviewing.get(User, reviewer_id)
        task = asyncio.create_task(review._lock_skill_decision(reviewing, stale, reviewer, stale.versions[0]))
        try:
            await asyncio.wait_for(attempted.wait(), timeout=5)
            await asyncio.sleep(0.15)
            assert not task.done(), "legacy review must wait for author to publish the new pending version"
            await writer.commit()
            with pytest.raises(HTTPException, match="Select an exact pending skill version") as blocked:
                await asyncio.wait_for(task, timeout=5)
            assert blocked.value.status_code == 409
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await writer.rollback()
            await reviewing.rollback()


async def test_selected_install_waits_for_private_transition_and_refuses_stale_public_read(pg_store):
    maker, engine = pg_store
    async with maker() as db:
        author = await ds.user(db)
        reader = await ds.user(db)
        listing = await ds.skill(db, author)
        await db.commit()
        listing_id, version_id, reader_id = listing.id, listing.latest_version_id, reader.id

    async with maker() as writer, maker() as installer:
        stale = (await installer.execute(select(SkillListing).where(SkillListing.id == listing_id))).scalar_one()
        await lock_skill_version(writer, listing_id, version_id)
        row = await writer.get(SkillListing, listing_id)
        row.is_private = True
        await writer.flush()
        lock_attempted = asyncio.Event()

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def observe_share(_conn, _cursor, statement, _params, _context, _many):
            if "skill_listings.id" in statement and "FOR SHARE" in statement.upper():
                lock_attempted.set()

        reader = await installer.get(type(reader), reader_id)
        task = asyncio.create_task(skill._selected_skill_release(stale, installer, reader, requested="1.2.0"))
        try:
            await asyncio.wait_for(lock_attempted.wait(), timeout=5)
            await asyncio.sleep(0.15)
            assert not task.done(), "selected install must wait on skill visibility writer"
            await writer.commit()
            with pytest.raises(HTTPException) as blocked:
                await asyncio.wait_for(task, timeout=5)
            assert blocked.value.status_code == 404
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await writer.rollback()
            await installer.rollback()


async def test_selected_private_skill_waits_for_team_revocation_and_refuses_bytes(pg_store):
    maker, engine = pg_store
    async with maker() as db:
        owner = await ds.user(db)
        reader = await ds.user(db)
        team = await ds.team_with_member(db, owner)
        membership = TeamMembership(id=uuid.uuid4(), team_id=team.id, user_id=reader.id, role=TeamRole.member)
        db.add(membership)
        listing = await ds.skill(db, owner, team_id=team.id, is_private=True)
        await db.commit()
        listing_id, member_id, reader_id, version_id = listing.id, membership.id, reader.id, listing.latest_version_id

    async with maker() as writer, maker() as installer:
        stale = await installer.get(SkillListing, listing_id)
        member = (
            await writer.execute(select(TeamMembership).where(TeamMembership.id == member_id).with_for_update())
        ).scalar_one()
        await writer.delete(member)
        await writer.flush()
        attempted = asyncio.Event()

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def observe_grant_share(_conn, _cursor, statement, _params, _context, _many):
            if "team_memberships" in statement and "FOR SHARE" in statement.upper():
                attempted.set()

        reader = await installer.get(User, reader_id)
        task = asyncio.create_task(skill._selected_skill_release(stale, installer, reader, requested="1.2.0"))
        try:
            await asyncio.wait_for(attempted.wait(), timeout=5)
            await asyncio.sleep(0.15)
            assert not task.done(), "private skill read must wait for membership revocation"
            await writer.commit()
            with pytest.raises(HTTPException) as denied:
                await asyncio.wait_for(task, timeout=5)
            assert denied.value.status_code == 404
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await writer.rollback()
            await installer.rollback()
    async with maker() as db:
        assert (await db.get(SkillVersion, version_id)).download_count == 0


async def test_team_revocation_waits_for_inflight_private_skill_install(pg_store):
    maker, engine = pg_store
    async with maker() as db:
        owner = await ds.user(db)
        reader = await ds.user(db)
        team = await ds.team_with_member(db, owner)
        member = TeamMembership(id=uuid.uuid4(), team_id=team.id, user_id=reader.id, role=TeamRole.member)
        db.add(member)
        listing = await ds.skill(db, owner, team_id=team.id, is_private=True)
        await db.commit()
        listing_id, member_id, reader_id = listing.id, member.id, reader.id

    async with maker() as installer, maker() as writer:
        stale = await installer.get(SkillListing, listing_id)
        reader = await installer.get(User, reader_id)
        _, selected = await skill._selected_skill_release(stale, installer, reader, requested="1.2.0")
        assert selected.status == ListingStatus.approved
        blocked_at_delete = asyncio.Event()

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def observe_delete(_conn, _cursor, statement, _params, _context, _many):
            if "DELETE FROM TEAM_MEMBERSHIPS" in statement.upper().replace('"', ""):
                blocked_at_delete.set()

        member = await writer.get(TeamMembership, member_id)

        async def revoke():
            await writer.delete(member)
            await writer.flush()
            await writer.commit()

        task = asyncio.create_task(revoke())
        try:
            await asyncio.wait_for(blocked_at_delete.wait(), timeout=5)
            await asyncio.sleep(0.15)
            assert not task.done(), "revocation must wait until private bytes are consumed"
            await installer.rollback()
            await asyncio.wait_for(task, timeout=5)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await installer.rollback()
            await writer.rollback()
    async with maker() as db:
        assert await db.get(TeamMembership, member_id) is None


async def test_agent_pinned_private_skill_refuses_revoked_team_membership(pg_store, monkeypatch):
    maker, engine = pg_store
    async with maker() as db:
        owner = await ds.user(db)
        reader = await ds.user(db)
        team = await ds.team_with_member(db, owner)
        member = TeamMembership(id=uuid.uuid4(), team_id=team.id, user_id=reader.id, role=TeamRole.member)
        db.add(member)
        listing = await ds.skill(db, owner, team_id=team.id, is_private=True)
        agent = await ds.agent(db, owner, components=[("skill", listing.id, listing.name)])
        agent.is_private = True
        agent.team_id = team.id
        link = (await db.execute(select(AgentComponent).where(AgentComponent.component_id == listing.id))).scalar_one()
        link.resolved_version_id = listing.latest_version_id
        agent_version = (await db.execute(select(AgentVersion).where(AgentVersion.agent_id == agent.id))).scalar_one()
        await lock_agent_version(db, agent, agent_version)
        await db.commit()
        agent_id, member_id, reader_id = agent.id, member.id, reader.id

    async with maker() as writer, maker() as installer:
        locked = (
            await writer.execute(select(TeamMembership).where(TeamMembership.id == member_id).with_for_update())
        ).scalar_one()
        await writer.delete(locked)
        await writer.flush()
        attempted = asyncio.Event()

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def observe_agent_grant(_conn, _cursor, statement, _params, _context, _many):
            if "team_memberships" in statement and "FOR SHARE" in statement.upper():
                attempted.set()

        monkeypatch.setattr("api.routes.config.derive_endpoints", AsyncMock(return_value={"api": "http://test"}))
        reader = await installer.get(User, reader_id)
        task = asyncio.create_task(
            install_agent(
                str(agent_id),
                AgentInstallRequest(harness="pi", options={"scope": "project"}),
                request=None,
                db=installer,
                current_user=reader,
            )
        )
        try:
            await asyncio.wait_for(attempted.wait(), timeout=5)
            await asyncio.sleep(0.15)
            assert not task.done(), "pinned agent must wait for the private skill membership decision"
            await writer.commit()
            with pytest.raises(HTTPException) as refused:
                await asyncio.wait_for(task, timeout=5)
            assert refused.value.status_code == 404
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await writer.rollback()
            await installer.rollback()


async def test_rebase_waits_for_pending_review_transition_and_keeps_saved_bytes(pg_store):
    maker, engine = pg_store
    async with maker() as db:
        author = await ds.user(db)
        listing = await ds.skill(db, author)
        base = await db.get(SkillVersion, listing.latest_version_id)
        base.skill_md_content = "---\nname: tested\ndescription: Original\n---\n# Original\n"
        await db.flush()
        created = await skill_files.create_skill_candidate_draft(
            str(listing.id),
            SkillCandidateDraftRequest(
                base_version_id=base.id,
                observed_base_revision=skill_content_revision(listing, base),
                version="1.5.0",
                description=base.description,
            ),
            Response(),
            db,
            author,
        )
        next_release = await ds.add_skill_version(db, listing, author, version="1.4.0", status=ListingStatus.approved)
        next_release.delivery_mode = "registry_direct"
        next_release.skill_md_content = base.skill_md_content
        await db.flush()
        latest_revision = skill_content_revision(listing, next_release)
        await db.commit()
        listing_id, draft_id, current_id, author_id = listing.id, created.version_id, next_release.id, author.id

    async with maker() as writer, maker() as editor:
        await lock_skill_version(writer, listing_id, draft_id)
        row = await writer.get(SkillVersion, draft_id)
        row.status = ListingStatus.pending
        await writer.flush()
        attempted = asyncio.Event()

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def observe_author_lock(_conn, _cursor, statement, _params, _context, _many):
            if "skill_listings.latest_version_id" in statement and "FOR UPDATE" in statement.upper():
                attempted.set()

        author = await editor.get(User, author_id)
        task = asyncio.create_task(
            skill_files.rebase_skill_draft(
                str(listing_id),
                draft_id,
                SkillDraftRebaseRequest(
                    observed_revision=created.revision,
                    current_version_id=current_id,
                    observed_current_revision=latest_revision,
                ),
                Response(),
                editor,
                author,
            )
        )
        try:
            await asyncio.wait_for(attempted.wait(), timeout=5)
            await asyncio.sleep(0.15)
            assert not task.done(), "rebase must wait on the listing/version review writer"
            await writer.commit()
            with pytest.raises(HTTPException) as refused:
                await asyncio.wait_for(task, timeout=5)
            assert refused.value.status_code == 409
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await writer.rollback()
            await editor.rollback()
    async with maker() as db:
        draft = await db.get(SkillVersion, draft_id)
        assert draft.status == ListingStatus.pending
        assert draft.content_revision == created.revision
        assert draft.base_version_id == base.id


async def test_pinned_agent_install_waits_for_global_review_and_refuses_stale_bytes(pg_store, monkeypatch):
    maker, engine = pg_store
    async with maker() as db:
        owner = await ds.user(db)
        reader = await ds.user(db)
        listing = await ds.skill(db, owner)
        agent = await ds.agent(db, owner, components=[("skill", listing.id, listing.name)])
        link = (await db.execute(select(AgentComponent).where(AgentComponent.component_id == listing.id))).scalar_one()
        link.resolved_version_id = listing.latest_version_id
        agent_version = (await db.execute(select(AgentVersion).where(AgentVersion.agent_id == agent.id))).scalar_one()
        await lock_agent_version(db, agent, agent_version)
        await db.commit()
        listing_id, version_id, agent_id, reader_id = listing.id, listing.latest_version_id, agent.id, reader.id

    async with maker() as writer, maker() as installer:
        await lock_skill_version(writer, listing_id, version_id)
        version = await writer.get(SkillVersion, version_id)
        version.requires_global_review = True
        version.status = ListingStatus.pending
        await writer.flush()
        lock_attempted = asyncio.Event()

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def observe_agent_share(_conn, _cursor, statement, _params, _context, _many):
            if "skill_listings.id" in statement and "FOR SHARE" in statement.upper():
                lock_attempted.set()

        monkeypatch.setattr("api.routes.config.derive_endpoints", AsyncMock(return_value={"api": "http://test"}))
        reader = await installer.get(type(reader), reader_id)
        task = asyncio.create_task(
            install_agent(
                str(agent_id),
                AgentInstallRequest(harness="pi", options={"scope": "project"}),
                request=None,
                db=installer,
                current_user=reader,
            )
        )
        try:
            await asyncio.wait_for(lock_attempted.wait(), timeout=5)
            await asyncio.sleep(0.15)
            assert not task.done(), "agent install must wait for the global-review writer"
            await writer.commit()
            with pytest.raises(HTTPException) as blocked:
                await asyncio.wait_for(task, timeout=5)
            assert blocked.value.status_code in (404, 409)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await writer.rollback()
            await installer.rollback()


async def test_privatization_waits_for_inflight_selected_install(pg_store):
    maker, engine = pg_store
    async with maker() as db:
        author = await ds.user(db)
        reader = await ds.user(db)
        listing = await ds.skill(db, author)
        await db.commit()
        listing_id, version_id, reader_id = listing.id, listing.latest_version_id, reader.id

    async with maker() as installer, maker() as writer:
        stale = (await installer.execute(select(SkillListing).where(SkillListing.id == listing_id))).scalar_one()
        reader = await installer.get(type(reader), reader_id)
        _, selected = await skill._selected_skill_release(stale, installer, reader, requested="1.2.0")
        selected_id = selected.id
        assert selected_id == version_id
        lock_attempted = asyncio.Event()

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def observe_writer(_conn, _cursor, statement, _params, _context, _many):
            if "skill_listings.latest_version_id" in statement and "FOR UPDATE" in statement.upper():
                lock_attempted.set()

        task = asyncio.create_task(lock_skill_version(writer, listing_id, version_id))
        try:
            await asyncio.wait_for(lock_attempted.wait(), timeout=5)
            await asyncio.sleep(0.15)
            assert not task.done(), "writer must wait until the selected release is consumed"
            await installer.rollback()
            _, locked = await asyncio.wait_for(task, timeout=5)
            assert locked.id == selected_id
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await installer.rollback()
            await writer.rollback()


async def test_review_waits_for_writer_and_reloads_resource_bytes(pg_store, monkeypatch):
    maker, engine = pg_store
    async with maker() as db:
        admin = await ds.user(db, role=UserRole.admin)
        listing = await ds.skill(db, admin, status=ListingStatus.pending)
        await db.commit()
        listing_id = listing.id

    async with maker() as reviewer, maker() as author:
        stale = (await reviewer.execute(select(SkillListing).where(SkillListing.id == listing_id))).scalar_one()
        assert stale.latest_version.extra_files == []
        monkeypatch.setattr(review, "_find_listing", AsyncMock(return_value=("skill", stale)))
        await lock_skill_version(author, listing_id, stale.latest_version_id)
        version = (await author.execute(select(SkillVersion).where(SkillVersion.listing_id == listing_id))).scalar_one()
        version.extra_files = [{"path": "assets/data.txt", "content": "unreviewed"}]
        await author.flush()

        lock_attempted = asyncio.Event()

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def observe_lock(_conn, _cursor, statement, _params, _context, _many):
            if "skill_listings.latest_version_id" in statement and "FOR UPDATE" in statement.upper():
                lock_attempted.set()

        task = asyncio.create_task(review.approve(str(listing_id), reviewer, admin))
        try:
            await asyncio.wait_for(lock_attempted.wait(), timeout=5)
            await asyncio.sleep(0.15)
            assert not task.done(), "review must wait at the writer's listing lock"
            await author.commit()
            with pytest.raises(HTTPException) as exc:
                await asyncio.wait_for(task, timeout=5)
            assert exc.value.status_code == 409
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await author.rollback()
            await reviewer.rollback()

    async with maker() as db:
        row = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing_id))).scalar_one()
        assert row.status == ListingStatus.pending
        assert row.extra_files == [{"path": "assets/data.txt", "content": "unreviewed"}]


async def test_exact_review_waits_for_withdrawn_version_and_refuses_old_observation(pg_store):
    maker, engine = pg_store
    async with maker() as db:
        author = await ds.user(db)
        reviewer = await ds.user(db, role=UserRole.reviewer)
        listing = await ds.skill(db, author, status=ListingStatus.pending)
        version = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing.id))).scalar_one()
        original_revision = skill_content_revision(listing, version)
        await db.commit()
        listing_id, version_id = listing.id, version.id

    async with maker() as author_db, maker() as reviewer_db:
        await lock_skill_version(author_db, listing_id, version_id)
        lock_attempted = asyncio.Event()

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def observe_lock(_conn, _cursor, statement, _params, _context, _many):
            if "skill_listings.latest_version_id" in statement and "FOR UPDATE" in statement.upper():
                lock_attempted.set()

        task = asyncio.create_task(
            review.decide_skill_version(
                str(listing_id),
                version_id,
                VersionReviewRequest(action="approve", observed_revision=original_revision),
                reviewer_db,
                reviewer,
            )
        )
        try:
            await asyncio.wait_for(lock_attempted.wait(), timeout=5)
            await asyncio.sleep(0.15)
            assert not task.done()
            version = await author_db.get(SkillVersion, version_id)
            version.review_epoch = 1
            version.status = ListingStatus.draft
            version.content_revision = skill_content_revision(listing, version)
            await author_db.commit()
            with pytest.raises(HTTPException) as blocked:
                await asyncio.wait_for(task, timeout=5)
            assert blocked.value.status_code == 422
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await author_db.rollback()
            await reviewer_db.rollback()
    async with maker() as db:
        version = await db.get(SkillVersion, version_id)
        assert version.status == ListingStatus.draft and version.review_epoch == 1


async def test_candidate_review_waits_for_new_approved_base_and_rejects_stale_ancestry(pg_store):
    maker, engine = pg_store
    async with maker() as db:
        author = await ds.user(db)
        reviewer = await ds.user(db, role=UserRole.reviewer)
        listing = await ds.skill(db, author, status=ListingStatus.approved)
        base_id = listing.latest_version_id
        candidate = await ds.add_skill_version(
            db, listing, author, version="3.0.0", status=ListingStatus.pending, set_latest=False
        )
        candidate.base_version_id = base_id
        candidate.base_revision = skill_content_revision(listing, await db.get(SkillVersion, base_id))
        candidate.content_revision = skill_content_revision(listing, candidate)
        revision, listing_id, candidate_id = candidate.content_revision, listing.id, candidate.id
        successor = await ds.add_skill_version(
            db, listing, author, version="2.0.0", status=ListingStatus.approved, set_latest=False
        )
        await db.commit()
        successor_id = successor.id

    async with maker() as author_db, maker() as reviewer_db:
        await lock_skill_version(author_db, listing_id, successor_id)
        listing = await author_db.get(SkillListing, listing_id)
        listing.latest_version_id = successor_id
        await author_db.flush()
        lock_attempted = asyncio.Event()

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def observe_lock(_conn, _cursor, statement, _params, _context, _many):
            if "skill_listings.latest_version_id" in statement and "FOR UPDATE" in statement.upper():
                lock_attempted.set()

        task = asyncio.create_task(
            review.decide_skill_version(
                str(listing_id),
                candidate_id,
                VersionReviewRequest(action="approve", observed_revision=revision),
                reviewer_db,
                reviewer,
            )
        )
        try:
            await asyncio.wait_for(lock_attempted.wait(), timeout=5)
            await asyncio.sleep(0.15)
            assert not task.done()
            await author_db.commit()
            with pytest.raises(HTTPException) as stale:
                await asyncio.wait_for(task, timeout=5)
            assert stale.value.status_code == 409
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await author_db.rollback()
            await reviewer_db.rollback()
    async with maker() as db:
        listing = await db.get(SkillListing, listing_id)
        candidate = await db.get(SkillVersion, candidate_id)
        assert listing.latest_version_id == successor_id
        assert candidate.status == ListingStatus.pending


async def test_public_agent_review_waits_for_concurrent_skill_privatization(pg_store):
    maker, engine = pg_store
    async with maker() as db:
        author = await ds.user(db)
        reviewer = await ds.user(db, role=UserRole.reviewer)
        team = await ds.team_with_member(db, author)
        listing = await ds.skill(db, author, team_id=team.id)
        agent = await ds.agent(db, author, status=AgentStatus.pending, components=[("skill", listing.id, listing.name)])
        await db.commit()
        skill_id, skill_version_id, agent_id = listing.id, listing.latest_version_id, agent.id

    async with maker() as author_db, maker() as reviewer_db:
        await lock_skill_version(author_db, skill_id, skill_version_id)
        listing = await author_db.get(SkillListing, skill_id)
        listing.is_private = True
        await author_db.flush()
        share_attempted = asyncio.Event()

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def observe_share(_conn, _cursor, statement, _params, _context, _many):
            if "skill_listings.is_private" in statement and "FOR SHARE" in statement.upper():
                share_attempted.set()

        task = asyncio.create_task(review.approve_agent(agent_id, None, reviewer_db, reviewer))
        try:
            await asyncio.wait_for(share_attempted.wait(), timeout=5)
            await asyncio.sleep(0.15)
            assert not task.done()
            await author_db.commit()
            with pytest.raises(HTTPException) as blocked:
                await asyncio.wait_for(task, timeout=5)
            assert blocked.value.status_code == 422
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await author_db.rollback()
            await reviewer_db.rollback()
    async with maker() as db:
        version = (await db.execute(select(AgentVersion).where(AgentVersion.agent_id == agent_id))).scalar_one()
        assert version.status == AgentStatus.pending


@pytest.mark.parametrize("decision_route", ["review", "version"])
async def test_public_agent_review_rechecks_skill_status_after_listing_lock_wait(pg_store, monkeypatch, decision_route):
    maker, engine = pg_store
    # If the race is missed, allow the route to finish rather than failing
    # because this narrow fixture intentionally omits inbox/Redis tables.
    monkeypatch.setattr(review.inbox, "on_review_decided", AsyncMock(return_value=1))
    monkeypatch.setattr(review, "invalidate_namespace", AsyncMock())
    monkeypatch.setattr(review, "redis_publish", AsyncMock())
    async with maker() as db:
        author = await ds.user(db)
        reviewer = await ds.user(db, role=UserRole.reviewer)
        listing = await ds.skill(db, author)
        agent = await ds.agent(db, author, status=AgentStatus.pending, components=[("skill", listing.id, listing.name)])
        await db.commit()
        skill_id, skill_version_id, agent_id = listing.id, listing.latest_version_id, agent.id

    async with maker() as writer, maker() as reviewer_db:
        await lock_skill_version(writer, skill_id, skill_version_id)
        version = await writer.get(SkillVersion, skill_version_id)
        version.status = ListingStatus.pending
        version.requires_global_review = True
        await writer.flush()
        share_attempted = asyncio.Event()

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def observe_share(_conn, _cursor, statement, _params, _context, _many):
            if "skill_listings.is_private" in statement and "FOR SHARE" in statement.upper():
                share_attempted.set()

        decision = (
            review.approve_agent(agent_id, None, reviewer_db, reviewer)
            if decision_route == "review"
            else agent_versions._review_agent_version(
                str(agent_id), "3.1.0", AgentVersionReviewRequest(action="approve"), reviewer_db, reviewer
            )
        )
        task = asyncio.create_task(decision)
        try:
            await asyncio.wait_for(share_attempted.wait(), timeout=5)
            await asyncio.sleep(0.15)
            assert not task.done()
            await writer.commit()
            with pytest.raises(HTTPException) as blocked:
                await asyncio.wait_for(task, timeout=5)
            assert blocked.value.status_code == 422
            assert any(
                item["status"] == "pending_public_review" for item in blocked.value.detail["blocking_components"]
            )
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await writer.rollback()
            await reviewer_db.rollback()
    async with maker() as db:
        agent_version = (await db.execute(select(AgentVersion).where(AgentVersion.agent_id == agent_id))).scalar_one()
        assert agent_version.status == AgentStatus.pending


async def test_agent_publication_waits_for_concurrent_skill_privatization(pg_store):
    maker, engine = pg_store
    async with maker() as db:
        author = await ds.user(db)
        listing = await ds.skill(db, author)
        agent = await ds.agent(db, author, status=AgentStatus.pending, components=[("skill", listing.id, listing.name)])
        agent.is_private = True
        await db.commit()
        skill_id, skill_version_id, agent_id = listing.id, listing.latest_version_id, agent.id

    async with maker() as author_db, maker() as visibility_db:
        await lock_skill_version(author_db, skill_id, skill_version_id)
        listing = await author_db.get(SkillListing, skill_id)
        listing.is_private = True
        await author_db.flush()
        share_attempted = asyncio.Event()

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def observe_share(_conn, _cursor, statement, _params, _context, _many):
            if "skill_listings.is_private" in statement and "FOR SHARE" in statement.upper():
                share_attempted.set()

        task = asyncio.create_task(
            registry.update_registry_visibility(
                "agent",
                str(agent_id),
                registry.VisibilityUpdateRequest(visibility="public"),
                SimpleNamespace(state=SimpleNamespace()),
                visibility_db,
                author,
            )
        )
        try:
            await asyncio.wait_for(share_attempted.wait(), timeout=5)
            await asyncio.sleep(0.15)
            assert not task.done()
            await author_db.commit()
            with pytest.raises(HTTPException) as blocked:
                await asyncio.wait_for(task, timeout=5)
            assert blocked.value.status_code == 409
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await author_db.rollback()
            await visibility_db.rollback()
    async with maker() as db:
        agent = await db.get(Agent, agent_id)
        assert agent.is_private


async def test_file_patch_rejects_concurrent_listing_rename(pg_store):
    maker, engine = pg_store
    async with maker() as db:
        owner = await ds.user(db)
        listing = await ds.skill(db, owner, status=ListingStatus.draft)
        await db.commit()
        listing_id, version_id = listing.id, listing.latest_version_id

    async with maker() as editor, maker() as writer:
        listing = (await editor.execute(select(SkillListing).where(SkillListing.id == listing_id))).scalar_one()
        observed = skill_content_revision(listing, listing.latest_version)
        await lock_skill_version(writer, listing_id, version_id)
        changed = (await writer.execute(select(SkillListing).where(SkillListing.id == listing_id))).scalar_one()
        changed.name = "Renamed while saving"
        await writer.flush()
        lock_attempted = asyncio.Event()

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def observe_lock(_conn, _cursor, statement, _params, _context, _many):
            if "skill_listings.latest_version_id" in statement and "FOR UPDATE" in statement.upper():
                lock_attempted.set()

        patch = SkillFileOperations.model_validate(
            {
                "observed_revision": observed,
                "operations": [{"action": "put", "file": {"path": "new.txt", "content": "new"}}],
            }
        )
        task = asyncio.create_task(
            skill_files.patch_skill_files(str(listing_id), version_id, patch, Response(), editor, owner)
        )
        try:
            await asyncio.wait_for(lock_attempted.wait(), timeout=5)
            await asyncio.sleep(0.15)
            assert not task.done(), "file save must wait for the listing rename"
            await writer.commit()
            with pytest.raises(HTTPException) as exc:
                await asyncio.wait_for(task, timeout=5)
            assert exc.value.status_code == 409
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await writer.rollback()
            await editor.rollback()
    async with maker() as db:
        changed = await db.get(SkillListing, listing_id)
        version = await db.get(SkillVersion, version_id)
        assert changed.name == "Renamed while saving"
        assert version.extra_files == []


async def test_file_patch_waits_for_writer_and_rejects_stale_revision(pg_store):
    maker, engine = pg_store
    async with maker() as db:
        owner = await ds.user(db)
        listing = await ds.skill(db, owner, status=ListingStatus.draft)
        await db.commit()
        listing_id, version_id = listing.id, listing.latest_version_id

    async with maker() as editor, maker() as writer:
        listing = (await editor.execute(select(SkillListing).where(SkillListing.id == listing_id))).scalar_one()
        observed = skill_content_revision(listing, listing.latest_version)
        await lock_skill_version(writer, listing_id, version_id)
        version = (await writer.execute(select(SkillVersion).where(SkillVersion.id == version_id))).scalar_one()
        version.extra_files = [{"path": "assets/data.txt", "content": "other edit"}]
        await writer.flush()
        lock_attempted = asyncio.Event()

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def observe_lock(_conn, _cursor, statement, _params, _context, _many):
            if "skill_listings.latest_version_id" in statement and "FOR UPDATE" in statement.upper():
                lock_attempted.set()

        patch = SkillFileOperations.model_validate(
            {
                "observed_revision": observed,
                "operations": [{"action": "put", "file": {"path": "new.txt", "content": "new"}}],
            }
        )
        task = asyncio.create_task(
            skill_files.patch_skill_files(str(listing_id), version_id, patch, Response(), editor, owner)
        )
        try:
            await asyncio.wait_for(lock_attempted.wait(), timeout=5)
            await asyncio.sleep(0.15)
            assert not task.done(), "file patch must wait at the writer's listing lock"
            await writer.commit()
            with pytest.raises(HTTPException) as exc:
                await asyncio.wait_for(task, timeout=5)
            assert exc.value.status_code == 409
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await writer.rollback()
            await editor.rollback()
    async with maker() as db:
        version = (await db.execute(select(SkillVersion).where(SkillVersion.id == version_id))).scalar_one()
        assert version.extra_files == [{"path": "assets/data.txt", "content": "other edit"}]
