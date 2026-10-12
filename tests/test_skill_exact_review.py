# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""A reviewer sees selected UUIDs, never an arbitrary pending listing body."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import select

from api.routes import component_versions, review, skill
from models.mcp import ListingStatus
from models.skill import SkillListing, SkillVersion
from models.team import TeamMembership, TeamRole
from models.user import UserRole
from schemas.component_version import VersionReviewRequest
from schemas.mcp import ReviewActionRequest
from schemas.skill import SkillSubmitRequest
from services.skill_revisions import skill_content_revision
from services.teamspace import ReviewScope, review_scope
from tests import discovery_support as ds

pytestmark = pytest.mark.asyncio


async def test_queue_and_detail_bind_two_pending_skill_versions_by_uuid(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            author = await ds.user(db)
            reviewer = await ds.user(db, role=UserRole.reviewer)
            listing = await ds.skill(db, author, status=ListingStatus.approved)
            first = await ds.add_skill_version(
                db, listing, author, version="1.3.0", status=ListingStatus.pending, set_latest=False
            )
            second = await ds.add_skill_version(
                db, listing, author, version="1.4.0", status=ListingStatus.pending, set_latest=False
            )
            second.extra_files = [{"path": "a.txt", "content": "second version"}]
            second.delivery_mode = "registry_direct"
            second.skill_md_content = "---\nname: second\ndescription: reviewed\n---\n# Second\n"
            second.content_revision = skill_content_revision(listing, second)
            await db.commit()
            listing_id, first_id, second_id = listing.id, first.id, second.id

        async with maker() as db:
            scope = ReviewScope(is_admin=False, is_global_reviewer=True, team_ids=frozenset())
            queued = await review._query_pending_components(db, scope, "skill")
            assert {item["version_id"] for item in queued} == {str(first_id), str(second_id)}
            assert {item["id"] for item in queued} == {str(listing_id)}
            assert len({item["review_key"] for item in queued}) == 2
            response = Response()
            detail = await review.get_skill_version_review(str(listing_id), second_id, response, db, reviewer)
            assert detail["version_id"] == str(second_id)
            assert detail["version"] == "1.4.0"
            assert detail["files"][1]["path"] == "a.txt"
            assert detail["revision"]
            assert response.headers["Cache-Control"] == "no-store"
            with pytest.raises(HTTPException) as ambiguous:
                await review.get_review(str(listing_id), db, reviewer)
            assert ambiguous.value.status_code == 409
            for decide in (
                lambda: review.approve(str(listing_id), db, reviewer),
                lambda: review.reject(str(listing_id), ReviewActionRequest(reason="not ready"), db, reviewer),
                lambda: review.decide_skill_version(
                    str(listing_id), second_id, VersionReviewRequest(action="reject"), db, reviewer
                ),
            ):
                with pytest.raises(HTTPException) as error:
                    await decide()
                assert error.value.status_code == 409
            monkeypatch.setattr("api.routes.component_versions.inbox.on_review_decided", AsyncMock())
            reviewed = await review.decide_skill_version(
                str(listing_id), first_id, VersionReviewRequest(action="approve"), db, reviewer
            )
            assert reviewed["version"] == "1.3.0"
            assert reviewed["new_status"] == "approved"
            monkeypatch.setattr("api.routes.component_versions._ds.get_bool", AsyncMock(return_value=True))
            rejected = await review.decide_skill_version(
                str(listing_id),
                second_id,
                VersionReviewRequest(action="reject", reason="not ready", observed_revision=detail["revision"]),
                db,
                reviewer,
            )
            assert rejected["new_status"] == "rejected"

        async with maker() as db:
            statuses = (
                await db.execute(
                    select(SkillVersion.id, SkillVersion.status).where(SkillVersion.listing_id == listing_id)
                )
            ).all()
            assert dict(statuses)[first_id] == ListingStatus.approved
            assert dict(statuses)[second_id] == ListingStatus.rejected
    finally:
        await engine.dispose()


async def test_new_direct_resource_less_submission_requires_reviewer_observation(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            reviewer = await ds.user(db, role=UserRole.reviewer)
            monkeypatch.setattr(skill.inbox, "on_publish", AsyncMock())
            created = await skill.submit_skill(
                SkillSubmitRequest(
                    name="Fresh Direct Skill",
                    version="1.0.0",
                    description="A direct skill",
                    owner=owner.email,
                    delivery_mode="registry_direct",
                    task_type="general",
                    skill_md_content="---\nname: fresh\ndescription: Direct skill\n---\n# Fresh\n",
                ),
                db,
                owner,
            )
            listing_id = created.id
            version = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing_id))).scalar_one()
            version_id = version.id
            assert version.content_revision is not None
        async with maker() as db:
            for decide in (
                lambda: review.approve(str(listing_id), db, reviewer),
                lambda: review.decide_skill_version(
                    str(listing_id),
                    version_id,
                    VersionReviewRequest(action="approve"),
                    db,
                    reviewer,
                ),
            ):
                with pytest.raises(HTTPException) as blocked:
                    await decide()
                assert blocked.value.status_code == 409
                await db.rollback()
            detail = await review.get_skill_version_review(str(listing_id), version_id, Response(), db, reviewer)
            assert detail["revision"]
            monkeypatch.setattr("api.routes.component_versions.inbox.on_review_decided", AsyncMock())
            approved = await review.decide_skill_version(
                str(listing_id),
                version_id,
                VersionReviewRequest(action="approve", observed_revision=detail["revision"]),
                db,
                reviewer,
            )
            assert approved["new_status"] == "approved"
    finally:
        await engine.dispose()


async def test_exact_resource_folder_approval_requires_reviewed_bytes(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            author = await ds.user(db)
            reviewer = await ds.user(db, role=UserRole.reviewer)
            listing = await ds.skill(db, author, status=ListingStatus.pending, version="1.0.0")
            row = await db.get(SkillVersion, listing.latest_version_id)
            row.extra_files = [{"path": "scripts/run.sh", "content": "echo one", "executable": True}]
            row.content_revision = skill_content_revision(listing, row)
            await db.commit()
            listing_id, version_id = listing.id, row.id
        monkeypatch.setattr("api.routes.component_versions.inbox.on_review_decided", AsyncMock())
        async with maker() as db:
            detail = await review.get_skill_version_review(str(listing_id), version_id, Response(), db, reviewer)
            assert {entry["path"] for entry in detail["files"]} == {"SKILL.md", "scripts/run.sh"}
            with pytest.raises(HTTPException) as versionless:
                await review.approve(str(listing_id), db, reviewer)
            assert versionless.value.status_code == 409
            with pytest.raises(HTTPException) as stale:
                await review.decide_skill_version(
                    str(listing_id),
                    version_id,
                    VersionReviewRequest(action="approve", observed_revision="0" * 64),
                    db,
                    reviewer,
                )
            assert stale.value.status_code == 409
            with pytest.raises(HTTPException, match="disabled until fleet rollout"):
                await review.decide_skill_version(
                    str(listing_id),
                    version_id,
                    VersionReviewRequest(action="approve", observed_revision=detail["revision"]),
                    db,
                    reviewer,
                )
            monkeypatch.setattr("api.routes.component_versions._ds.get_bool", AsyncMock(return_value=True))
            approved = await review.decide_skill_version(
                str(listing_id),
                version_id,
                VersionReviewRequest(action="approve", observed_revision=detail["revision"]),
                db,
                reviewer,
            )
            assert approved["new_status"] == "approved"
            assert (await db.get(SkillVersion, version_id)).status == ListingStatus.approved
    finally:
        await engine.dispose()


async def test_exact_decision_refuses_active_editor_and_older_stable_release():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            reviewer = await ds.user(db, role=UserRole.reviewer)
            listing = await ds.skill(db, owner, status=ListingStatus.approved, version="1.0.0")
            newer = await ds.add_skill_version(
                db, listing, owner, version="2.0.0", status=ListingStatus.approved, set_latest=False
            )
            older = await ds.add_skill_version(
                db, listing, owner, version="1.5.0", status=ListingStatus.pending, set_latest=False
            )
            older.is_editing = True
            older.editing_by = owner.id
            older.editing_since = datetime.now(UTC)
            await db.commit()
            listing_id, older_id, newest_id = listing.id, older.id, newer.id
            pointer_id = listing.latest_version_id

        async with maker() as db:
            with pytest.raises(HTTPException) as locked:
                await review.decide_skill_version(
                    str(listing_id), older_id, VersionReviewRequest(action="approve"), db, reviewer
                )
            assert locked.value.status_code == 409
            await db.rollback()
            older = await db.get(SkillVersion, older_id)
            older.is_editing = False
            older.editing_since = None
            older.editing_by = None
            await db.commit()
            for decide in (
                lambda: review.decide_skill_version(
                    str(listing_id), older_id, VersionReviewRequest(action="approve"), db, reviewer
                ),
                lambda: review.approve(str(listing_id), db, reviewer),
            ):
                with pytest.raises(HTTPException) as outdated:
                    await decide()
                assert outdated.value.status_code == 409
                await db.rollback()
        async with maker() as db:
            row = await db.get(SkillVersion, older_id)
            approved = await db.get(SkillVersion, newest_id)
            listing = await db.get(type(listing), listing_id)
            assert row.status == ListingStatus.pending
            assert approved.status == ListingStatus.approved
            assert listing.latest_version_id == pointer_id
    finally:
        await engine.dispose()


async def test_git_to_direct_review_requires_explicit_missing_base_file_acknowledgment(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            author = await ds.user(db)
            reviewer = await ds.user(db, role=UserRole.reviewer)
            listing = await ds.skill(db, author, status=ListingStatus.approved)
            base = await db.get(SkillVersion, listing.latest_version_id)
            base.delivery_mode = "git_fetch"
            base.git_url = "https://github.com/example/old-skill.git"
            base.git_ref = "main"
            candidate = await ds.add_skill_version(
                db, listing, author, version="1.3.0", status=ListingStatus.pending, set_latest=False
            )
            candidate.delivery_mode = "registry_direct"
            candidate.skill_md_content = "---\nname: new-skill\ndescription: New folder\n---\n# Skill\n"
            candidate.base_version_id = base.id
            candidate.base_revision = skill_content_revision(listing, base)
            candidate.content_revision = skill_content_revision(listing, candidate)
            await db.commit()
            listing_id, candidate_id, revision = listing.id, candidate.id, candidate.content_revision
        async with maker() as db:
            detail = await review.get_skill_version_review(str(listing_id), candidate_id, Response(), db, reviewer)
            assert detail["base_delivery_mode"] == "git_fetch"
            assert detail["base_git_url"] == "https://github.com/example/old-skill.git"
            assert detail["base_git_ref"] == "main"
            assert detail["base_version_id"]
            with pytest.raises(HTTPException) as blocked:
                await review.decide_skill_version(
                    str(listing_id),
                    candidate_id,
                    VersionReviewRequest(action="approve", observed_revision=revision),
                    db,
                    reviewer,
                )
            assert blocked.value.status_code == 409
            assert "Git base files are not stored" in blocked.value.detail
            await db.rollback()
            monkeypatch.setattr("api.routes.component_versions.inbox.on_review_decided", AsyncMock())
            approved = await review.decide_skill_version(
                str(listing_id),
                candidate_id,
                VersionReviewRequest(action="approve", observed_revision=revision, git_base_acknowledged=True),
                db,
                reviewer,
            )
            assert approved["new_status"] == "approved"
    finally:
        await engine.dispose()


async def test_candidate_version_decision_requires_matching_observed_revision(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            author = await ds.user(db)
            reviewer = await ds.user(db, role=UserRole.reviewer)
            listing = await ds.skill(db, author, status=ListingStatus.approved)
            base_id = listing.latest_version_id
            candidate = await ds.add_skill_version(
                db, listing, author, version="1.3.0", status=ListingStatus.pending, set_latest=False
            )
            candidate.base_version_id = base_id
            candidate.base_revision = skill_content_revision(listing, await db.get(SkillVersion, base_id))
            candidate.content_revision = skill_content_revision(listing, candidate)
            await db.commit()
            listing_id, candidate_id, revision = listing.id, candidate.id, candidate.content_revision

        async with maker() as db:
            detail = await review.get_skill_version_review(str(listing_id), candidate_id, Response(), db, reviewer)
            assert detail["base_version_id"] == str(base_id)
            for legacy_decision in (
                lambda: review.approve(str(listing_id), db, reviewer),
                lambda: review.reject(str(listing_id), ReviewActionRequest(reason="stale"), db, reviewer),
            ):
                with pytest.raises(HTTPException) as blocked:
                    await legacy_decision()
                assert blocked.value.status_code == 409
                await db.rollback()
            for observed in (None, "0" * 64):
                with pytest.raises(HTTPException) as exc:
                    await review.decide_skill_version(
                        str(listing_id),
                        candidate_id,
                        VersionReviewRequest(action="approve", observed_revision=observed),
                        db,
                        reviewer,
                    )
                assert exc.value.status_code == 409
                await db.rollback()
            monkeypatch.setattr("api.routes.component_versions.inbox.on_review_decided", AsyncMock())
            decided = await review.decide_skill_version(
                str(listing_id),
                candidate_id,
                VersionReviewRequest(action="approve", observed_revision=revision),
                db,
                reviewer,
            )
            assert decided["new_status"] == "approved"
            listing = await db.get(type(listing), listing_id)
            assert listing.latest_version_id == candidate_id
    finally:
        await engine.dispose()


async def test_public_version_list_includes_archived_unmarked_but_hides_marked_rows():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(db, owner, status=ListingStatus.approved)
            archived = await ds.add_skill_version(
                db, listing, owner, version="1.1.0", status=ListingStatus.archived, set_latest=False
            )
            marked = await ds.add_skill_version(
                db, listing, owner, version="1.0.0", status=ListingStatus.archived, set_latest=False
            )
            marked.requires_global_review = True
            await db.commit()
            listing_id, archived_id = listing.id, archived.id
        async with maker() as db:
            result = await component_versions._list_versions(
                str(listing_id), 1, 20, SkillListing, SkillVersion, "skill", db, None
            )
            assert {item["id"] for item in result["items"]} == {str(listing.latest_version_id), str(archived_id)}
            assert result["total"] == 2
    finally:
        await engine.dispose()


async def test_archived_base_candidate_can_be_reviewed_when_pointer_stays_unchanged(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            author = await ds.user(db)
            reviewer = await ds.user(db, role=UserRole.reviewer)
            listing = await ds.skill(db, author, status=ListingStatus.archived)
            base_id = listing.latest_version_id
            candidate = await ds.add_skill_version(
                db, listing, author, version="1.3.0", status=ListingStatus.pending, set_latest=False
            )
            candidate.base_version_id = base_id
            candidate.base_revision = skill_content_revision(listing, await db.get(SkillVersion, base_id))
            candidate.content_revision = skill_content_revision(listing, candidate)
            await db.commit()
            listing_id, candidate_id, revision = listing.id, candidate.id, candidate.content_revision
        monkeypatch.setattr("api.routes.component_versions.inbox.on_review_decided", AsyncMock())
        async with maker() as db:
            decided = await review.decide_skill_version(
                str(listing_id),
                candidate_id,
                VersionReviewRequest(action="approve", observed_revision=revision),
                db,
                reviewer,
            )
            assert decided["new_status"] == "approved"
    finally:
        await engine.dispose()


async def test_review_refuses_candidate_after_approved_base_advances():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            author = await ds.user(db)
            reviewer = await ds.user(db, role=UserRole.reviewer)
            listing = await ds.skill(db, author, status=ListingStatus.approved)
            old_id = listing.latest_version_id
            candidate = await ds.add_skill_version(
                db, listing, author, version="3.0.0", status=ListingStatus.pending, set_latest=False
            )
            candidate.base_version_id = old_id
            candidate.base_revision = skill_content_revision(listing, await db.get(SkillVersion, old_id))
            candidate.content_revision = skill_content_revision(listing, candidate)
            revision, candidate_id, listing_id = candidate.content_revision, candidate.id, listing.id
            newer = await ds.add_skill_version(
                db, listing, author, version="2.0.0", status=ListingStatus.approved, set_latest=True
            )
            await db.commit()
            newer_id = newer.id
        async with maker() as db:
            with pytest.raises(HTTPException) as stale:
                await review.decide_skill_version(
                    str(listing_id),
                    candidate_id,
                    VersionReviewRequest(action="approve", observed_revision=revision),
                    db,
                    reviewer,
                )
            assert stale.value.status_code == 409
            await db.rollback()
            row = await db.get(SkillVersion, candidate_id)
            listing = await db.get(type(listing), listing_id)
            assert row.status == ListingStatus.pending
            assert listing.latest_version_id == newer_id
    finally:
        await engine.dispose()


async def test_team_private_reviewer_can_decide_exact_version_without_global_role(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            author = await ds.user(db)
            team_reviewer = await ds.user(db)
            team = await ds.team_with_member(db, author)
            db.add(TeamMembership(team_id=team.id, user_id=team_reviewer.id, role=TeamRole.reviewer))
            listing = await ds.skill(db, author, status=ListingStatus.pending, is_private=True, team_id=team.id)
            version_id, listing_id = listing.latest_version_id, listing.id
            await db.commit()

        async with maker() as db:
            detail = await review.get_skill_version_review(str(listing_id), version_id, Response(), db, team_reviewer)
            assert detail["version_id"] == str(version_id)
            assert detail["revision"]
            monkeypatch.setattr("api.routes.component_versions.inbox.on_review_decided", AsyncMock())
            decided = await review.decide_skill_version(
                str(listing_id),
                version_id,
                VersionReviewRequest(action="approve", observed_revision=detail["revision"]),
                db,
                team_reviewer,
            )
            assert decided["new_status"] == "approved"
    finally:
        await engine.dispose()


async def test_public_re_review_marker_is_hidden_from_team_reviewers_and_never_cleared():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            author = await ds.user(db)
            team_reviewer = await ds.user(db)
            global_reviewer = await ds.user(db, role=UserRole.reviewer)
            admin = await ds.user(db, role=UserRole.admin)
            team = await ds.team_with_member(db, author)
            team.is_private = False
            db.add(TeamMembership(team_id=team.id, user_id=team_reviewer.id, role=TeamRole.reviewer))
            listing = await ds.skill(db, author, status=ListingStatus.pending, team_id=team.id)
            version = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing.id))).scalar_one()
            version.requires_global_review = True
            await db.commit()
            listing_id, version_id = listing.id, version.id

        async with maker() as db:
            team_scope = await review_scope(db, team_reviewer)
            global_scope = await review_scope(db, global_reviewer)
            assert await review._query_pending_components(db, team_scope, "skill") == []
            global_items = await review._query_pending_components(db, global_scope, "skill")
            assert [item["version_id"] for item in global_items] == [str(version_id)]
            with pytest.raises(HTTPException) as hidden:
                await review.get_skill_version_review(str(listing_id), version_id, Response(), db, team_reviewer)
            assert hidden.value.status_code == 404
            with pytest.raises(HTTPException) as denied:
                await review.decide_skill_version(
                    str(listing_id), version_id, VersionReviewRequest(action="approve"), db, team_reviewer
                )
            assert denied.value.status_code == 404
            with pytest.raises(HTTPException) as blocked:
                await review.decide_skill_version(
                    str(listing_id), version_id, VersionReviewRequest(action="approve"), db, global_reviewer
                )
            assert blocked.value.status_code == 409

        async with maker() as db:
            listing = await db.get(type(listing), listing_id)
            team = await db.get(type(team), team.id)
            listing.is_private = True
            team.is_private = True
            await db.commit()
        async with maker() as db:
            admin_scope = await review_scope(db, admin)
            admin_items = await review._query_pending_components(db, admin_scope, "skill")
            assert [item["version_id"] for item in admin_items] == [str(version_id)]
            detail = await review.get_skill_version_review(str(listing_id), version_id, Response(), db, admin)
            assert detail["version_id"] == str(version_id)
            global_scope = await review_scope(db, global_reviewer)
            assert await review._query_pending_components(db, global_scope, "skill") == []
            row = await db.get(SkillVersion, version_id)
            assert row.status == ListingStatus.pending and row.requires_global_review
    finally:
        await engine.dispose()


async def test_historical_global_re_review_clears_older_archived_out_of_order(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            author = await ds.user(db)
            global_reviewer = await ds.user(db, role=UserRole.reviewer)
            team = await ds.team_with_member(db, author)
            team.is_private = False
            listing = await ds.skill(db, author, status=ListingStatus.pending, team_id=team.id, version="2.0.0")
            newer = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing.id))).scalar_one()
            newer.requires_global_review = True
            newer.pre_public_status = "approved"
            older = await ds.add_skill_version(
                db, listing, author, version="1.0.0", status=ListingStatus.pending, set_latest=False
            )
            older.requires_global_review = True
            older.pre_public_status = "archived"
            await db.commit()
            listing_id, older_id, newer_id = listing.id, older.id, newer.id

        monkeypatch.setattr("api.routes.component_versions.inbox.on_review_decided", AsyncMock())
        async with maker() as db:
            detail = await review.get_skill_version_review(str(listing_id), older_id, Response(), db, global_reviewer)
            with pytest.raises(HTTPException) as missing:
                await review.decide_skill_version(
                    str(listing_id), older_id, VersionReviewRequest(action="approve"), db, global_reviewer
                )
            assert missing.value.status_code == 409
            with pytest.raises(HTTPException) as stale:
                await review.decide_skill_version(
                    str(listing_id),
                    older_id,
                    VersionReviewRequest(action="approve", observed_revision="0" * 64),
                    db,
                    global_reviewer,
                )
            assert stale.value.status_code == 409
            decision = await review.decide_skill_version(
                str(listing_id),
                older_id,
                VersionReviewRequest(action="approve", observed_revision=detail["revision"]),
                db,
                global_reviewer,
            )
            assert decision["new_status"] == "archived"
            older = await db.get(SkillVersion, older_id)
            listing = await db.get(SkillListing, listing_id)
            assert not older.requires_global_review and older.pre_public_status is None
            assert older.reviewed_by == global_reviewer.id
            assert listing.latest_version_id == older_id
            assert (await db.get(SkillVersion, newer_id)).requires_global_review
    finally:
        await engine.dispose()


async def test_re_review_of_older_release_does_not_demote_cleared_newer_pointer(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            reviewer = await ds.user(db, role=UserRole.reviewer)
            listing = await ds.skill(db, owner, version="2.0.0")
            newer_id = listing.latest_version_id
            older = await ds.add_skill_version(
                db, listing, owner, version="1.0.0", status=ListingStatus.pending, set_latest=False
            )
            older.requires_global_review = True
            older.pre_public_status = "approved"
            await db.commit()
            listing_id, older_id = listing.id, older.id
        monkeypatch.setattr("api.routes.component_versions.inbox.on_review_decided", AsyncMock())
        async with maker() as db:
            detail = await review.get_skill_version_review(str(listing_id), older_id, Response(), db, reviewer)
            await review.decide_skill_version(
                str(listing_id),
                older_id,
                VersionReviewRequest(action="approve", observed_revision=detail["revision"]),
                db,
                reviewer,
            )
            assert (await db.get(SkillListing, listing_id)).latest_version_id == newer_id
            assert (await db.get(SkillVersion, older_id)).status == ListingStatus.approved
    finally:
        await engine.dispose()


async def test_resource_bearing_rejection_is_not_a_versionless_review_escape():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            author = await ds.user(db)
            reviewer = await ds.user(db, role=UserRole.reviewer)
            listing = await ds.skill(db, author, status=ListingStatus.pending)
            version = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing.id))).scalar_one()
            version.extra_files = [{"path": "private.txt", "content": "not reviewed"}]
            await db.commit()
            listing_id, version_id = listing.id, version.id

        async with maker() as db:
            with pytest.raises(HTTPException) as error:
                await review.reject(str(listing_id), ReviewActionRequest(reason="wrong bytes"), db, reviewer)
            assert error.value.status_code == 409
            await db.rollback()
        async with maker() as db:
            version = await db.get(SkillVersion, version_id)
            assert version.status == ListingStatus.pending
            assert version.reviewed_by is None
    finally:
        await engine.dispose()
