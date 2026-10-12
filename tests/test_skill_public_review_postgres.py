# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Visibility and a concurrent status change serialize over the listing lock."""

from __future__ import annotations

import asyncio
import os
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.routes._skill_lock import lock_skill_version
from models.base import Base
from models.component_bundle import ComponentBundle
from models.mcp import ListingStatus
from models.skill import SkillListing, SkillVersion
from services.teamspace import review_publication_to_public, skill_transition_needs_rollout_gate
from tests import discovery_support as ds


@pytest.mark.asyncio
@pytest.mark.parametrize("publisher_first", [True, False])
async def test_publication_serializes_with_skill_review_status(monkeypatch, publisher_first):
    url = os.environ.get("OBSERVAL_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set OBSERVAL_TEST_POSTGRES_URL to an isolated PostgreSQL test database")
    schema = f"skill_visibility_{uuid.uuid4().hex}"
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": f"{schema},public"}})
    monkeypatch.setattr("services.inbox.sources.on_review_requested", AsyncMock(return_value=1))
    monkeypatch.setattr("services.inbox.sources.on_review_withdrawn", AsyncMock(return_value=1))
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(text(f'CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA "{schema}"'))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=[*ds.TABLES, ComponentBundle.__table__])
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            owner = await ds.user(db)
            team = await ds.team_with_member(db, owner)
            listing = await ds.skill(db, owner, is_private=True, team_id=team.id, status=ListingStatus.pending)
            listing_id, version_id, owner_id = listing.id, listing.latest_version_id, owner.id
            assert not await skill_transition_needs_rollout_gate(db, listing_id)
            version = await db.get(SkillVersion, version_id)
            version.extra_files = [{"path": "untracked.txt", "content": "private"}]
            await db.flush()
            assert await skill_transition_needs_rollout_gate(db, listing_id)
            version.extra_files = []
            await db.commit()

        first_locked = asyncio.Event()
        allow_first_commit = asyncio.Event()
        second_started = asyncio.Event()
        second_locked = asyncio.Event()

        async def publication():
            async with sessions() as db:
                await db.execute(select(SkillListing.id).where(SkillListing.id == listing_id).with_for_update())
                if publisher_first:
                    first_locked.set()
                    await asyncio.wait_for(allow_first_commit.wait(), timeout=10)
                else:
                    second_locked.set()
                listing = await db.get(SkillListing, listing_id)
                listing.is_private = False
                await review_publication_to_public(listing, type("Actor", (), {"id": owner_id})(), db, was_private=True)
                await db.commit()

        async def decision():
            async with sessions() as db:
                if publisher_first:
                    second_started.set()
                _pointer, version = await lock_skill_version(db, listing_id, version_id)
                if not publisher_first:
                    first_locked.set()
                    await asyncio.wait_for(allow_first_commit.wait(), timeout=10)
                else:
                    second_locked.set()
                # An exact reviewer cannot clear a row observed only after
                # global re-review became mandatory.
                if version.status == ListingStatus.pending and not version.requires_global_review:
                    version.status = ListingStatus.approved
                await db.commit()

        first = asyncio.create_task(publication() if publisher_first else decision())
        await asyncio.wait_for(first_locked.wait(), timeout=10)
        second = asyncio.create_task(decision() if publisher_first else publication())
        if publisher_first:
            await asyncio.wait_for(second_started.wait(), timeout=10)
        await asyncio.sleep(0.1)
        assert not second_locked.is_set()
        allow_first_commit.set()
        await asyncio.wait_for(asyncio.gather(first, second), timeout=20)
        async with sessions() as db:
            listing = await db.get(SkillListing, listing_id)
            version = await db.get(SkillVersion, version_id)
            assert not listing.is_private
            assert version.requires_global_review
            assert version.status == ListingStatus.pending
            assert version.pre_public_status == ("approved" if not publisher_first else None)
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()
