# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Database-backed regression tests for stale review state and body-free reads."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select

from api.routes import component_versions, skill
from models.mcp import ListingStatus
from models.skill import SkillListing, SkillVersion
from schemas.component_version import VersionPublishRequest
from schemas.skill import SkillListingSummary, SkillUpdateRequest
from services.skill_bundle import validate_skill_bundle
from services.skill_validator import SkillValidationError
from tests import discovery_support as ds

RESOURCE = {"path": "assets/data.txt", "content": "unreviewed"}


@pytest.fixture
async def store():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        yield engine, maker
    finally:
        await engine.dispose()


async def _pending_skill(maker):
    async with maker() as db:
        owner = await ds.user(db)
        listing = await ds.skill(db, owner, status=ListingStatus.pending)
        await db.commit()
        return owner, listing.id


@pytest.mark.asyncio
async def test_stale_writer_cannot_change_approved_version(store, monkeypatch):
    _, maker = store
    owner, listing_id = await _pending_skill(maker)
    async with maker() as author, maker() as reviewer:
        stale = (await author.execute(select(SkillListing).where(SkillListing.id == listing_id))).scalar_one()
        row = (await reviewer.execute(select(SkillVersion).where(SkillVersion.listing_id == listing_id))).scalar_one()
        row.status = ListingStatus.approved
        await reviewer.commit()
        monkeypatch.setattr(skill, "resolve_listing", AsyncMock(return_value=stale))
        with pytest.raises(HTTPException) as exc:
            await skill.update_skill_draft(str(listing_id), SkillUpdateRequest(extra_files=[RESOURCE]), author, owner)
        assert exc.value.status_code == 409
        await author.rollback()
    async with maker() as db:
        row = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing_id))).scalar_one()
        assert row.status == ListingStatus.approved
        assert row.extra_files == []


@pytest.mark.asyncio
async def test_removed_coauthor_cannot_start_edit_from_stale_listing(store, monkeypatch):
    _, maker = store
    owner, listing_id = await _pending_skill(maker)
    async with maker() as db:
        coauthor = await ds.user(db)
        listing = (await db.execute(select(SkillListing).where(SkillListing.id == listing_id))).scalar_one()
        listing.co_authors = [str(coauthor.id)]
        await db.commit()
    async with maker() as editor, maker() as owner_session:
        stale = (await editor.execute(select(SkillListing).where(SkillListing.id == listing_id))).scalar_one()
        changed = (await owner_session.execute(select(SkillListing).where(SkillListing.id == listing_id))).scalar_one()
        changed.co_authors = []
        await owner_session.commit()
        monkeypatch.setattr(skill, "resolve_listing", AsyncMock(return_value=stale))
        with pytest.raises(HTTPException) as exc:
            await skill.start_edit_skill(str(listing_id), editor, coauthor)
        assert exc.value.status_code == 403
        await editor.rollback()
    async with maker() as db:
        row = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing_id))).scalar_one()
        assert row.is_editing is False


@pytest.mark.asyncio
async def test_version_summary_queries_and_serialization_are_body_free(store):
    engine, maker = store
    owner, listing_id = await _pending_skill(maker)
    async with maker() as db:
        row = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing_id))).scalar_one()
        row.script_filename = "run.sh"
        row.script_content = "SECRET-SCRIPT"
        row.extra_files = [RESOURCE]
        await db.commit()
    statements = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def capture(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement.lower())

    async with maker() as db:
        listing = (
            await db.execute(skill._summary_options(select(SkillListing).where(SkillListing.id == listing_id)))
        ).scalar_one()
        assert SkillListingSummary.model_validate(listing).version
        versions = await component_versions._list_versions(
            str(listing_id), 1, 20, SkillListing, SkillVersion, "skill", db, owner
        )
        fields = versions["items"][0]
        assert "script_content" not in fields
        assert "skill_md_content" not in fields
        assert "extra_files" not in fields
        assert not any(
            body_field in sql
            for sql in statements
            for body_field in ("extra_files", "script_content", "skill_md_content")
        )


@pytest.mark.asyncio
async def test_publish_inherits_reviewed_base_not_unapproved_latest_pointer(store, monkeypatch):
    _, maker = store
    owner, listing_id = await _pending_skill(maker)
    async with maker() as db:
        listing = (await db.execute(select(SkillListing).where(SkillListing.id == listing_id))).scalar_one()
        approved = listing.latest_version
        approved.status = ListingStatus.approved
        approved.skill_md_content = "# approved bytes\n"
        candidate = SkillVersion(
            listing_id=listing_id,
            version="1.3.0",
            description="unreviewed",
            status=ListingStatus.pending,
            released_by=owner.id,
            released_at=datetime.now(UTC),
            task_type="general",
            delivery_mode="registry_direct",
            skill_md_content="# unreviewed bytes\n",
            extra_files=[RESOURCE],
        )
        db.add(candidate)
        await db.flush()
        listing.latest_version_id = candidate.id  # Historical pointer anomaly repaired by migration 030.
        await db.commit()
        approved_id = approved.id
        candidate_id = candidate.id
    async with maker() as db:
        await component_versions._publish_version(
            str(listing_id),
            VersionPublishRequest(version="1.4.0", description="fresh release"),
            SkillListing,
            SkillVersion,
            "skill",
            db,
            owner,
        )
    async with maker() as db:
        listing = (await db.execute(select(SkillListing).where(SkillListing.id == listing_id))).scalar_one()
        published = (
            await db.execute(
                select(SkillVersion).where(SkillVersion.listing_id == listing_id, SkillVersion.version == "1.4.0")
            )
        ).scalar_one()
        assert listing.latest_version_id == candidate_id  # Publishing pending never advances the public pointer.
        assert published.skill_md_content == "# approved bytes\n"
        assert published.extra_files == []
        assert published.base_version_id == approved_id
        assert not published.requires_global_review
        assert published.pre_public_status is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override",
    [
        None,
        {"script_filename": "legacy.sh"},
        {"delivery_mode": "git_fetch"},
        {"script_filename": "new.sh"},
        {"extra_files": [RESOURCE]},
    ],
)
async def test_publish_legacy_git_script_metadata_only_when_inherited(store, monkeypatch, override):
    _, maker = store
    owner, listing_id = await _pending_skill(maker)
    async with maker() as db:
        old = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing_id))).scalar_one()
        old.delivery_mode = "git_fetch"
        old.skill_md_content = None
        old.git_url = "https://example.com/legacy.git"
        old.script_filename = "legacy.sh"
        old.script_content = None
        await db.commit()
    async with maker() as db:
        req = VersionPublishRequest(
            version="1.3.0",
            description="Updated description",
            extra=override,
        )
        if override is not None:
            with pytest.raises(HTTPException) as exc:
                await component_versions._publish_version(
                    str(listing_id), req, SkillListing, SkillVersion, "skill", db, owner
                )
            assert exc.value.status_code == 422
            await db.rollback()
        else:
            await component_versions._publish_version(
                str(listing_id), req, SkillListing, SkillVersion, "skill", db, owner
            )
    async with maker() as db:
        rows = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing_id))).scalars().all()
        assert len(rows) == (1 if override is not None else 2)
        if override is None:
            new = next(row for row in rows if row.version == "1.3.0")
            assert new.delivery_mode == "git_fetch"
            assert new.script_filename == "legacy.sh"
            assert new.script_content is None


def test_unchanged_legacy_git_script_metadata_is_grandfathered():
    assert (
        validate_skill_bundle(
            delivery_mode="git_fetch",
            skill_md_content=None,
            script_filename="legacy.sh",
            script_content=None,
            extra_files=[],
            enforce_limits=False,
        )
        == ()
    )
    with pytest.raises(SkillValidationError, match="script_content and script_filename"):
        validate_skill_bundle(
            delivery_mode="git_fetch",
            skill_md_content=None,
            script_filename="legacy.sh",
            script_content=None,
            extra_files=[],
        )
