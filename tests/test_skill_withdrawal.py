# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Withdrawal invalidates every review observation without losing saved bytes."""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Response

from api.routes import skill, skill_files
from models.mcp import ListingStatus
from models.skill import SkillVersion
from models.user import UserRole
from schemas.skill import SkillUpdateRequest
from schemas.skill_resources import SkillVersionRevisionRequest
from services.skill_revisions import skill_content_revision
from tests import discovery_support as ds

pytestmark = pytest.mark.asyncio


async def test_legacy_metadata_route_cannot_renumber_reserved_revision_draft():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(db, owner, status=ListingStatus.draft)
            version = await db.get(SkillVersion, listing.latest_version_id)
            version.content_revision = skill_content_revision(listing, version)
            await db.commit()
            listing_id, version_id = listing.id, version.id
        async with maker() as db:
            with pytest.raises(HTTPException) as renumber:
                await skill.update_skill_draft(
                    str(listing_id),
                    SkillUpdateRequest(version="8.0.0", observed_revision=version.content_revision),
                    db,
                    owner,
                )
            assert renumber.value.status_code == 409
            await db.rollback()
            row = await db.get(SkillVersion, version_id)
            assert row.version == "1.2.0"
    finally:
        await engine.dispose()


async def test_legacy_resubmit_cannot_auto_approve_marked_draft():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db, role=UserRole.admin)
            listing = await ds.skill(db, owner, status=ListingStatus.draft)
            version = await db.get(SkillVersion, listing.latest_version_id)
            version.requires_global_review = True
            await db.commit()
            listing_id, version_id = listing.id, version.id
        async with maker() as db:
            with pytest.raises(HTTPException) as marked:
                await skill.submit_skill_draft(str(listing_id), db=db, current_user=owner)
            assert marked.value.status_code == 409
            await db.rollback()
            row = await db.get(SkillVersion, version_id)
            assert row.status == ListingStatus.draft and row.requires_global_review
    finally:
        await engine.dispose()


async def test_historical_requeued_version_cannot_be_withdrawn(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(db, owner, status=ListingStatus.pending)
            version = await db.get(SkillVersion, listing.latest_version_id)
            version.pre_public_status = ListingStatus.approved.value
            await db.commit()
            listing_id, version_id = listing.id, version.id
        notice = AsyncMock()
        async with maker() as db:
            with pytest.raises(HTTPException) as historical:
                await skill_files.withdraw_skill_version(
                    str(listing_id),
                    version_id,
                    SkillVersionRevisionRequest(observed_revision="0" * 64),
                    Response(),
                    db,
                    owner,
                )
            assert historical.value.status_code == 409
            notice.assert_not_awaited()
    finally:
        await engine.dispose()
