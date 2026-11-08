# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Skill review metadata follows the exact submitted or authorized version."""

import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import Response
from sqlalchemy import select

from api.routes import skill, skill_files
from models.mcp import ListingStatus
from models.review import Review, ReviewRevision, ReviewState
from models.skill import SkillVersion
from models.user import User
from schemas.skill import SkillFolderDraftRequest, SkillUpdateRequest
from schemas.skill_resources import SkillVersionRevisionRequest
from services import dynamic_settings
from tests import discovery_support as ds

FIXTURE = json.loads((Path(__file__).parent / "fixtures/skill_folder_contract.json").read_text())


@pytest.fixture(autouse=True)
def _mock_external_settings(monkeypatch):
    monkeypatch.setattr(
        dynamic_settings,
        "get",
        AsyncMock(
            side_effect=lambda key, default=None: (
                default if default is not None else dynamic_settings.DEFAULTS.get(key, "")
            )
        ),
    )
    monkeypatch.setattr("services.redis.get_redis", Mock(return_value=AsyncMock()))


@pytest.mark.asyncio
@pytest.mark.parametrize("with_resources", [False, True])
async def test_folder_submit_and_show_return_open_review_metadata(monkeypatch, with_resources):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    monkeypatch.setattr(skill_files._ds, "get_bool", AsyncMock(return_value=with_resources))
    try:
        async with maker() as db:
            owner = await ds.user(db)
            data = FIXTURE["author_create"]["body"] | FIXTURE["snapshot"]
            if not with_resources:
                data["extra_files"] = []
            draft = await skill.create_skill_folder_draft(SkillFolderDraftRequest.model_validate(data), db, owner)
            assert draft.review_number is None
            assert await db.scalar(select(Review)) is None
            owner_id = owner.id
            submitted = await skill_files.submit_skill_version_draft(
                str(draft.listing_id),
                draft.version_id,
                SkillVersionRevisionRequest(observed_revision=draft.revision),
                Response(),
                db,
                owner,
            )
            review = await db.scalar(select(Review).where(Review.version_id == draft.version_id))
            assert review.state == ReviewState.open
            assert submitted.review_number == review.number
            assert submitted.review_url == f"/review/{review.number}"
            assert submitted.review_revision == 1
            assert len(submitted.revision) == 64

        async with maker() as db:
            owner = await db.get(User, owner_id)
            shown = await skill.get_skill(str(draft.listing_id), db, owner)
            assert shown.status == ListingStatus.pending
            assert shown.review_number == submitted.review_number
            assert shown.review_url == submitted.review_url
            assert shown.review_revision == 1

            # Withdraw before editing a pending folder, then reopen the same
            # review. Its numeric head must advance instead of being hardcoded.
            withdrawn = await skill_files.withdraw_skill_version(
                str(draft.listing_id),
                draft.version_id,
                SkillVersionRevisionRequest(observed_revision=submitted.revision),
                Response(),
                db,
                owner,
            )
            await skill.update_skill_draft(
                str(draft.listing_id),
                SkillUpdateRequest(observed_revision=withdrawn.revision, description="Updated review candidate"),
                db,
                owner,
            )
            version = await db.get(SkillVersion, draft.version_id)
            resubmitted = await skill_files.submit_skill_version_draft(
                str(draft.listing_id),
                draft.version_id,
                SkillVersionRevisionRequest(observed_revision=version.content_revision),
                Response(),
                db,
                owner,
            )
            assert resubmitted.review_number == submitted.review_number
            assert resubmitted.review_url == submitted.review_url
            assert resubmitted.review_revision == 2

        async with maker() as db:
            owner = await db.get(User, owner_id)
            shown = await skill.get_skill(str(draft.listing_id), db, owner)
            assert shown.review_number == submitted.review_number
            assert shown.review_revision == 2
    finally:
        await engine.dispose()


async def _review(db, listing, version, owner, state=ReviewState.open):
    review = Review(
        id=uuid.uuid4(),
        number=42,
        subject_type="skill",
        subject_id=listing.id,
        version_id=version.id,
        version=version.version,
        title="Skill review",
        state=state,
        opened_by=owner.id,
    )
    db.add(review)
    await db.flush()
    revision = ReviewRevision(
        id=uuid.uuid4(), review_id=review.id, number=3, files={}, content_hash="a" * 64, created_by=owner.id
    )
    db.add(revision)
    await db.flush()
    review.head_revision_id = revision.id
    await db.commit()
    return review


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [None, ReviewState.closed, ReviewState.published])
async def test_show_without_active_review_keeps_metadata_null(state):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(db, owner, content=None)
            version = await db.get(SkillVersion, listing.latest_version_id)
            if state is not None:
                await _review(db, listing, version, owner, state)
            shown = await skill.get_skill(str(listing.id), db, owner)
            assert shown.review_number is None
            assert shown.review_url is None
            assert shown.review_revision is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_show_does_not_attach_pending_review_to_authorized_approved_fallback():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            reader = await ds.user(db)
            listing = await ds.skill(db, owner, content=None)
            pending = await ds.add_skill_version(
                db, listing, owner, version="2.0.0", status=ListingStatus.pending, content=None
            )
            review = await _review(db, listing, pending, owner)
            shown = await skill.get_skill(str(listing.id), db, reader)
            assert shown.version == "1.2.0"
            assert shown.status == ListingStatus.approved
            assert shown.review_number is None
            assert shown.review_url is None
            assert shown.review_revision is None
            shown_owner = await skill.get_skill(str(listing.id), db, owner)
            assert shown_owner.version == "2.0.0"
            assert shown_owner.review_number == review.number
            assert shown_owner.review_revision == 3
    finally:
        await engine.dispose()
