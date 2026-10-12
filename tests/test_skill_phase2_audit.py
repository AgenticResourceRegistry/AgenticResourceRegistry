# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Database-backed regression tests for stale review state and body-free reads."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select

from api.routes import component_versions, review, skill
from models.mcp import ListingStatus
from models.skill import SkillListing, SkillVersion
from models.user import UserRole
from schemas.component_version import VersionPublishRequest, VersionReviewRequest
from schemas.mcp import ReviewActionRequest
from schemas.skill import SkillListingSummary, SkillUpdateRequest
from services.skill_bundle import validate_skill_bundle
from services.skill_validator import SkillValidationError
from services.teamspace import ReviewScope
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
async def test_generic_review_reloads_bytes_after_stale_read(store, monkeypatch):
    _, maker = store
    owner, listing_id = await _pending_skill(maker)
    async with maker() as reviewer, maker() as author:
        stale = (await reviewer.execute(select(SkillListing).where(SkillListing.id == listing_id))).scalar_one()
        assert stale.latest_version.extra_files == []
        row = (await author.execute(select(SkillVersion).where(SkillVersion.listing_id == listing_id))).scalar_one()
        row.extra_files = [RESOURCE]
        await author.commit()

        monkeypatch.setattr(
            review, "_require_review_scope", AsyncMock(return_value=ReviewScope(True, True, frozenset()))
        )
        monkeypatch.setattr(review, "_find_listing", AsyncMock(return_value=("skill", stale)))
        with pytest.raises(HTTPException) as exc:
            await review.approve(str(listing_id), reviewer, owner)
        assert exc.value.status_code == 409
        await reviewer.rollback()
    async with maker() as db:
        row = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing_id))).scalar_one()
        assert row.status == ListingStatus.pending
        assert row.extra_files == [RESOURCE]


@pytest.mark.asyncio
async def test_generic_reject_requires_pending_skill_version(store, monkeypatch):
    _, maker = store
    _, listing_id = await _pending_skill(maker)
    async with maker() as db:
        row = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing_id))).scalar_one()
        row.status = ListingStatus.approved
        await db.commit()
    async with maker() as db:
        listing = (await db.execute(select(SkillListing).where(SkillListing.id == listing_id))).scalar_one()
        actor = await ds.user(db, role=UserRole.admin)
        monkeypatch.setattr(review, "_find_listing", AsyncMock(return_value=("skill", listing)))
        with pytest.raises(HTTPException) as exc:
            await review.reject(str(listing_id), ReviewActionRequest(reason="not pending"), db, actor)
        assert exc.value.status_code == 409
        await db.rollback()
    async with maker() as db:
        row = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing_id))).scalar_one()
        assert row.status == ListingStatus.approved


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["generic", "version"])
async def test_reject_cannot_overwrite_approved_skill_after_stale_read(store, monkeypatch, route):
    _, maker = store
    owner, listing_id = await _pending_skill(maker)
    monkeypatch.setattr(
        component_versions, "review_scope", AsyncMock(return_value=ReviewScope(True, True, frozenset()))
    )
    async with maker() as decision, maker() as approval:
        stale = (await decision.execute(select(SkillListing).where(SkillListing.id == listing_id))).scalar_one()
        approved = (
            await approval.execute(select(SkillVersion).where(SkillVersion.listing_id == listing_id))
        ).scalar_one()
        approved.status = ListingStatus.approved
        await approval.commit()
        with pytest.raises(HTTPException) as exc:
            if route == "generic":
                monkeypatch.setattr(
                    review, "_require_review_scope", AsyncMock(return_value=ReviewScope(True, True, frozenset()))
                )
                monkeypatch.setattr(review, "_find_listing", AsyncMock(return_value=("skill", stale)))
                await review.reject(str(listing_id), ReviewActionRequest(reason="old decision"), decision, owner)
            else:
                await component_versions._review_version(
                    str(listing_id),
                    stale.latest_version.version,
                    VersionReviewRequest(action="reject", reason="old decision"),
                    SkillListing,
                    SkillVersion,
                    "skill",
                    decision,
                    owner,
                )
        assert exc.value.status_code == (409 if route == "generic" else 422)
        await decision.rollback()
    async with maker() as db:
        row = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing_id))).scalar_one()
        assert row.status == ListingStatus.approved


@pytest.mark.asyncio
async def test_version_review_reloads_bytes_after_stale_read(store, monkeypatch):
    _, maker = store
    owner, listing_id = await _pending_skill(maker)
    monkeypatch.setattr(
        component_versions, "review_scope", AsyncMock(return_value=ReviewScope(True, True, frozenset()))
    )
    async with maker() as reviewer, maker() as author:
        stale = (await reviewer.execute(select(SkillVersion).where(SkillVersion.listing_id == listing_id))).scalar_one()
        row = (await author.execute(select(SkillVersion).where(SkillVersion.listing_id == listing_id))).scalar_one()
        row.extra_files = [RESOURCE]
        await author.commit()
        assert stale.extra_files == []
        with pytest.raises(HTTPException) as exc:
            await component_versions._review_version(
                str(listing_id),
                stale.version,
                VersionReviewRequest(action="approve"),
                SkillListing,
                SkillVersion,
                "skill",
                reviewer,
                owner,
            )
        assert exc.value.status_code == 409
        await reviewer.rollback()
    async with maker() as db:
        row = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing_id))).scalar_one()
        assert row.status == ListingStatus.pending


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["version", "generic"])
async def test_stale_approval_does_not_revert_newer_latest_version(store, monkeypatch, route):
    _, maker = store
    owner, listing_id = await _pending_skill(maker)
    monkeypatch.setattr(
        component_versions, "review_scope", AsyncMock(return_value=ReviewScope(True, True, frozenset()))
    )
    async with maker() as db:
        listing = (await db.execute(select(SkillListing).where(SkillListing.id == listing_id))).scalar_one()
        first = listing.latest_version
        first.status = ListingStatus.approved
        await db.flush()
        older = await ds.add_skill_version(
            db, listing, owner, version="2.0.0", status=ListingStatus.pending, set_latest=False
        )
        newer = await ds.add_skill_version(
            db, listing, owner, version="3.0.0", status=ListingStatus.pending, set_latest=False
        )
        await db.commit()
    monkeypatch.setattr(component_versions.inbox, "on_review_decided", AsyncMock())
    async with maker() as reviewer, maker() as author:
        stale = (await reviewer.execute(select(SkillListing).where(SkillListing.id == listing_id))).scalar_one()
        assert stale.latest_version_id == first.id
        fresh = (await author.execute(select(SkillListing).where(SkillListing.id == listing_id))).scalar_one()
        new_version = (await author.execute(select(SkillVersion).where(SkillVersion.id == newer.id))).scalar_one()
        new_version.status = ListingStatus.approved
        fresh.latest_version_id = new_version.id
        await author.commit()

        if route == "version":
            with pytest.raises(HTTPException) as stale_release:
                await component_versions._review_version(
                    str(listing_id),
                    older.version,
                    VersionReviewRequest(action="approve"),
                    SkillListing,
                    SkillVersion,
                    "skill",
                    reviewer,
                    owner,
                )
            assert stale_release.value.status_code == 409
        else:
            monkeypatch.setattr(
                review, "_require_review_scope", AsyncMock(return_value=ReviewScope(True, True, frozenset()))
            )
            monkeypatch.setattr(review, "_find_listing", AsyncMock(return_value=("skill", stale)))
            monkeypatch.setattr(review.inbox, "on_review_decided", AsyncMock())
            monkeypatch.setattr(review, "invalidate_namespace", AsyncMock())
            monkeypatch.setattr(review, "redis_publish", AsyncMock())
            with pytest.raises(HTTPException) as ambiguous:
                await review.approve(str(listing_id), reviewer, owner)
            assert ambiguous.value.status_code == 409
    async with maker() as db:
        listing = (await db.execute(select(SkillListing).where(SkillListing.id == listing_id))).scalar_one()
        assert listing.latest_version_id == newer.id
        older_row = (await db.execute(select(SkillVersion).where(SkillVersion.id == older.id))).scalar_one()
        assert older_row.status == ListingStatus.pending


@pytest.mark.asyncio
async def test_version_decision_denies_global_reviewer_with_only_private_member_seat(store):
    _, maker = store
    async with maker() as db:
        author = await ds.user(db)
        outsider = await ds.user(db, role=UserRole.reviewer)
        team = await ds.team_with_member(db, outsider)  # Member, not owner or team reviewer.
        listing = await ds.skill(db, author, status=ListingStatus.pending, is_private=True, team_id=team.id)
        await db.commit()
        listing_id = listing.id
    async with maker() as db:
        with pytest.raises(HTTPException) as denied:
            await component_versions._review_version(
                str(listing_id),
                "1.2.0",
                VersionReviewRequest(action="approve"),
                SkillListing,
                SkillVersion,
                "skill",
                db,
                outsider,
            )
        assert denied.value.status_code == 404
        await db.rollback()
    async with maker() as db:
        version = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing_id))).scalar_one()
        assert version.status == ListingStatus.pending


@pytest.mark.asyncio
async def test_generic_review_rechecks_private_visibility_after_stale_read(store, monkeypatch):
    _, maker = store
    _, listing_id = await _pending_skill(maker)
    async with maker() as db:
        reviewer = await ds.user(db, role=UserRole.reviewer)
        await db.commit()
    async with maker() as decision, maker() as author:
        stale = (await decision.execute(select(SkillListing).where(SkillListing.id == listing_id))).scalar_one()
        changed = (await author.execute(select(SkillListing).where(SkillListing.id == listing_id))).scalar_one()
        changed.is_private = True
        await author.commit()
        monkeypatch.setattr(review, "_find_listing", AsyncMock(return_value=("skill", stale)))
        with pytest.raises(HTTPException) as exc:
            await review.approve(str(listing_id), decision, reviewer)
        assert exc.value.status_code == 404
        await decision.rollback()
    async with maker() as db:
        row = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing_id))).scalar_one()
        assert row.status == ListingStatus.pending


@pytest.mark.asyncio
async def test_prerelease_approval_does_not_replace_stable_latest(store, monkeypatch):
    _, maker = store
    owner, listing_id = await _pending_skill(maker)
    monkeypatch.setattr(
        component_versions, "review_scope", AsyncMock(return_value=ReviewScope(True, True, frozenset()))
    )
    async with maker() as db:
        listing = (await db.execute(select(SkillListing).where(SkillListing.id == listing_id))).scalar_one()
        stable = await ds.add_skill_version(db, listing, owner, version="2.0.0", status=ListingStatus.approved)
        candidate = await ds.add_skill_version(
            db, listing, owner, version="2.0.0-rc.1", status=ListingStatus.pending, set_latest=False
        )
        await db.commit()
    monkeypatch.setattr(component_versions.inbox, "on_review_decided", AsyncMock())
    async with maker() as db:
        await component_versions._review_version(
            str(listing_id),
            candidate.version,
            VersionReviewRequest(action="approve"),
            SkillListing,
            SkillVersion,
            "skill",
            db,
            owner,
        )
    async with maker() as db:
        listing = (await db.execute(select(SkillListing).where(SkillListing.id == listing_id))).scalar_one()
        assert listing.latest_version_id == stable.id


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
async def test_review_queue_does_not_fetch_out_of_scope_bundle_bytes(store):
    engine, maker = store
    _, listing_id = await _pending_skill(maker)
    async with maker() as db:
        listing = (await db.execute(select(SkillListing).where(SkillListing.id == listing_id))).scalar_one()
        listing.is_private = True
        version = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing_id))).scalar_one()
        version.extra_files = [RESOURCE]
        await db.commit()
    statements = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def capture(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement.lower())

    async with maker() as db:
        assert (
            await review._query_pending_components(db, ReviewScope(False, True, frozenset()), type_filter="skill") == []
        )
    assert statements
    assert not any("extra_files" in sql or "script_content" in sql or "skill_md_content" in sql for sql in statements)


@pytest.mark.asyncio
@pytest.mark.parametrize("identifier", ["uuid", "name"])
async def test_review_lookup_does_not_read_private_skill_bodies_before_authorization(store, monkeypatch, identifier):
    engine, maker = store
    owner, listing_id = await _pending_skill(maker)
    async with maker() as db:
        listing = (await db.execute(select(SkillListing).where(SkillListing.id == listing_id))).scalar_one()
        team = await ds.team_with_member(db, owner)
        listing.team_id = team.id
        listing.is_private = True
        version = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing_id))).scalar_one()
        version.extra_files = [RESOURCE]
        version.skill_md_content = "# PRIVATE SKILL"
        version.script_content = "PRIVATE SCRIPT"
        name = listing.name
        await db.commit()

    requested_id = str(listing_id) if identifier == "uuid" else name
    statements = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def capture(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement.lower())

    monkeypatch.setattr(review, "_require_review_scope", AsyncMock(return_value=ReviewScope(False, True, frozenset())))
    async with maker() as db:
        with pytest.raises(HTTPException) as exc:
            await review.get_review(requested_id, db, owner)
        assert exc.value.status_code == 404
    assert statements
    assert not any(
        body_field in sql for sql in statements for body_field in ("extra_files", "script_content", "skill_md_content")
    )

    statements.clear()
    monkeypatch.setattr(
        review, "_require_review_scope", AsyncMock(return_value=ReviewScope(False, False, frozenset({team.id})))
    )
    async with maker() as db:
        detail = await review.get_review(requested_id, db, owner)
    assert detail["id"] == str(listing_id)
    assert detail["version"] == "1.2.0"
    assert not any(
        body_field in sql for sql in statements for body_field in ("extra_files", "script_content", "skill_md_content")
    )


@pytest.mark.asyncio
async def test_bundle_review_lookup_does_not_read_private_skill_bodies(store):
    engine, maker = store
    owner, listing_id = await _pending_skill(maker)
    bundle_id = uuid.uuid4()
    async with maker() as db:
        listing = (await db.execute(select(SkillListing).where(SkillListing.id == listing_id))).scalar_one()
        listing.bundle_id = bundle_id
        listing.is_private = True
        version = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing_id))).scalar_one()
        version.extra_files = [RESOURCE]
        await db.commit()
    statements = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def capture(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement.lower())

    async with maker() as db:
        with pytest.raises(HTTPException) as exc:
            await review._bundle_listings(bundle_id, db, ReviewScope(False, True, frozenset()))
        assert exc.value.status_code == 404
    assert statements
    assert not any(
        body_field in sql for sql in statements for body_field in ("extra_files", "script_content", "skill_md_content")
    )


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

    monkeypatch.setattr(component_versions.inbox, "on_publish", AsyncMock())
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
    monkeypatch.setattr(component_versions.inbox, "on_publish", AsyncMock())
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
