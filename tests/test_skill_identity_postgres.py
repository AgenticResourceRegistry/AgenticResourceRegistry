# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Listing and version revisions are atomically rebound under PostgreSQL contention."""

from __future__ import annotations

import asyncio
import os
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Request, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.routes import co_authors, registry, skill_files
from models.base import Base
from models.component_bundle import ComponentBundle
from models.skill import SkillListing, SkillVersion
from models.team import TeamMembership, TeamRole
from models.user import User
from services.skill_revisions import skill_content_revision
from tests import discovery_support as ds


@pytest.mark.asyncio
@pytest.mark.parametrize("writer_first", [True, False])
@pytest.mark.parametrize("operation", ["visibility", "transfer"])
async def test_tracked_visibility_and_manifest_lock_order(monkeypatch, writer_first, operation):
    url = os.environ.get("OBSERVAL_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set OBSERVAL_TEST_POSTGRES_URL to an isolated PostgreSQL test database")
    schema = f"skill_identity_{uuid.uuid4().hex}"
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": f"{schema},public"}})
    monkeypatch.setattr("services.dynamic_settings.get_bool", AsyncMock(return_value=True))
    monkeypatch.setattr("services.inbox.sources.on_review_requested", AsyncMock(return_value=0))
    monkeypatch.setattr("services.inbox.sources.on_review_withdrawn", AsyncMock(return_value=0))
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(text(f'CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA "{schema}"'))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=[*ds.TABLES, ComponentBundle.__table__])
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            owner = await ds.user(db)
            successor = await ds.user(db)
            team = await ds.team_with_member(db, owner)
            team.is_private = False
            membership = (
                await db.execute(select(TeamMembership).where(TeamMembership.team_id == team.id))
            ).scalar_one()
            membership.role = TeamRole.owner
            listing = await ds.skill(
                db,
                owner,
                is_private=True,
                team_id=team.id,
                content="---\nname: security-review\n---\n# Security Review\n",
            )
            row = await db.get(SkillVersion, listing.latest_version_id)
            row.content_revision = skill_content_revision(listing, row)
            listing_id, version_id, owner_id, successor_id, before = (
                listing.id,
                row.id,
                owner.id,
                successor.id,
                row.content_revision,
            )
            await db.commit()

        first_locked = asyncio.Event()
        release_first = asyncio.Event()
        second_started = asyncio.Event()
        second_locked = asyncio.Event()
        seen = []

        async def writer():
            async with sessions() as db:
                owner = await db.get(User, owner_id)
                if writer_first:
                    await db.execute(select(SkillListing.id).where(SkillListing.id == listing_id).with_for_update())
                    first_locked.set()
                    await asyncio.wait_for(release_first.wait(), timeout=10)
                else:
                    second_started.set()
                if operation == "visibility":
                    await registry.update_registry_visibility(
                        "skill",
                        str(listing_id),
                        registry.VisibilityUpdateRequest(visibility="public"),
                        Request({"type": "http"}),
                        db,
                        owner,
                    )
                else:
                    successor = await db.get(User, successor_id)
                    await co_authors.transfer_ownership(
                        "skills",
                        str(listing_id),
                        co_authors.TransferOwnershipRequest(username=successor.username),
                        db,
                        owner,
                    )
                if not writer_first:
                    second_locked.set()

        async def reader():
            async with sessions() as db:
                owner = await db.get(User, owner_id)
                if not writer_first:
                    # A reader's listing share lock protects the observation
                    # until its transaction finishes, even when a writer waits.
                    await db.execute(
                        select(SkillListing.id).where(SkillListing.id == listing_id).with_for_update(read=True)
                    )
                    first_locked.set()
                else:
                    second_started.set()
                try:
                    manifest = await skill_files.get_skill_manifest(str(listing_id), version_id, Response(), db, owner)
                    seen.append(manifest.revision)
                except HTTPException as exc:
                    if operation != "transfer" or not writer_first or exc.status_code not in (403, 404):
                        raise
                    seen.append("denied")
                if not writer_first:
                    await asyncio.wait_for(release_first.wait(), timeout=10)
                    await db.commit()
                else:
                    second_locked.set()

        first = asyncio.create_task(writer() if writer_first else reader())
        await asyncio.wait_for(first_locked.wait(), timeout=10)
        second = asyncio.create_task(reader() if writer_first else writer())
        await asyncio.wait_for(second_started.wait(), timeout=10)
        await asyncio.sleep(0.1)
        assert not second_locked.is_set()
        release_first.set()
        await asyncio.wait_for(asyncio.gather(first, second), timeout=25)
        async with sessions() as db:
            listing = await db.get(SkillListing, listing_id)
            version = await db.get(SkillVersion, version_id)
            assert not listing.is_private
            assert version.content_revision != before
            assert version.content_revision == skill_content_revision(listing, version)
            if operation == "transfer":
                assert listing.submitted_by == successor_id
                assert version.requires_global_review and version.status.value == "pending"
                assert seen == ["denied" if writer_first else before]
            else:
                assert seen == [version.content_revision if writer_first else before]
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()
