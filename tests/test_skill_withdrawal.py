# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Withdrawal invalidates every review observation without losing saved bytes."""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Response

from api.routes import review, skill, skill_files
from models.mcp import ListingStatus
from models.skill import SkillVersion
from models.user import UserRole
from schemas.component_version import VersionReviewRequest
from schemas.skill import SkillUpdateRequest
from schemas.skill_resources import SkillVersionRevisionRequest
from services.skill_revisions import skill_content_revision
from tests import discovery_support as ds

pytestmark = pytest.mark.asyncio


async def test_withdraw_and_resubmit_requires_fresh_review_even_when_bytes_do_not_change(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            reviewer = await ds.user(db, role=UserRole.reviewer)
            listing = await ds.skill(db, owner, status=ListingStatus.pending)
            version = await db.get(SkillVersion, listing.latest_version_id)
            listing_id, version_id = listing.id, version.id
            original = skill_content_revision(listing, version)
            original_md = version.skill_md_content
            await db.commit()

        withdrawn_notice = AsyncMock()
        requested_notice = AsyncMock()
        monkeypatch.setattr(skill_files.inbox, "on_review_withdrawn", withdrawn_notice)
        monkeypatch.setattr(skill_files.inbox, "on_review_requested", requested_notice)
        async with maker() as db:
            with pytest.raises(HTTPException) as stale:
                await skill_files.withdraw_skill_version(
                    str(listing_id),
                    version_id,
                    SkillVersionRevisionRequest(observed_revision="0" * 64),
                    Response(),
                    db,
                    owner,
                )
            assert stale.value.status_code == 409
            await db.rollback()
            withdrawn = await skill_files.withdraw_skill_version(
                str(listing_id),
                version_id,
                SkillVersionRevisionRequest(observed_revision=original),
                Response(),
                db,
                owner,
            )
            assert withdrawn.revision != original
            assert withdrawn.files[0].path == "SKILL.md"
            withdrawn_notice.assert_awaited_once()
            row = await db.get(SkillVersion, version_id)
            assert row.status == ListingStatus.draft
            assert row.skill_md_content == original_md
            assert row.review_epoch == 1
            with pytest.raises(HTTPException) as legacy_submit:
                await skill.submit_skill_draft(str(listing_id), db, owner)
            assert legacy_submit.value.status_code == 409
            await db.rollback()
            with pytest.raises(HTTPException) as old_action:
                await review.decide_skill_version(
                    str(listing_id),
                    version_id,
                    VersionReviewRequest(action="approve", observed_revision=original),
                    db,
                    reviewer,
                )
            assert old_action.value.status_code == 422
            await db.rollback()
            with pytest.raises(HTTPException) as stale_submit:
                await skill_files.submit_skill_version_draft(
                    str(listing_id),
                    version_id,
                    SkillVersionRevisionRequest(observed_revision=original),
                    Response(),
                    db,
                    owner,
                )
            assert stale_submit.value.status_code == 409
            await db.rollback()
            resubmitted = await skill_files.submit_skill_version_draft(
                str(listing_id),
                version_id,
                SkillVersionRevisionRequest(observed_revision=withdrawn.revision),
                Response(),
                db,
                owner,
            )
            assert resubmitted.revision == withdrawn.revision
            requested_notice.assert_awaited_once()
            with pytest.raises(HTTPException) as old_review:
                await review.decide_skill_version(
                    str(listing_id),
                    version_id,
                    VersionReviewRequest(action="approve", observed_revision=original),
                    db,
                    reviewer,
                )
            assert old_review.value.status_code == 409
            await db.rollback()
            with pytest.raises(HTTPException) as generic:
                await review.approve(str(listing_id), db, reviewer)
            assert generic.value.status_code == 409
        async with maker() as db:
            row = await db.get(SkillVersion, version_id)
            assert row.status == ListingStatus.pending and row.review_epoch == 1
    finally:
        await engine.dispose()


async def test_rejected_version_resubmission_invalidates_previous_review_observation(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            reviewer = await ds.user(db, role=UserRole.reviewer)
            listing = await ds.skill(db, owner, status=ListingStatus.rejected)
            version = await db.get(SkillVersion, listing.latest_version_id)
            prior = skill_content_revision(listing, version)
            version.content_revision = prior
            await db.commit()
            listing_id, version_id = listing.id, version.id
        monkeypatch.setattr(skill_files.inbox, "on_review_requested", AsyncMock())
        async with maker() as db:
            submitted = await skill_files.submit_skill_version_draft(
                str(listing_id),
                version_id,
                SkillVersionRevisionRequest(observed_revision=prior),
                Response(),
                db,
                owner,
            )
            assert submitted.revision != prior
            version = await db.get(SkillVersion, version_id)
            assert version.review_epoch == 1 and version.status == ListingStatus.pending
            with pytest.raises(HTTPException) as old_review:
                await review.decide_skill_version(
                    str(listing_id),
                    version_id,
                    VersionReviewRequest(action="approve", observed_revision=prior),
                    db,
                    reviewer,
                )
            assert old_review.value.status_code == 409
            await db.rollback()
        async with maker() as db:
            version = await db.get(SkillVersion, version_id)
            assert version.review_epoch == 1 and version.status == ListingStatus.pending
    finally:
        await engine.dispose()


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
                await skill.submit_skill_draft(str(listing_id), db, owner)
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
        monkeypatch.setattr(skill_files.inbox, "on_review_withdrawn", notice)
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
