# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Private team review is never sufficient to publish any skill release globally."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from models.base import Base
from models.inbox import InboxItem, InboxKind, InboxState
from models.mcp import ListingStatus
from models.review import Review, ReviewState
from models.skill import SkillListing, SkillVersion
from models.team import TeamMembership, TeamRole
from models.user import UserRole
from services import teamspace
from services.review.cutover import submit_for_review
from services.review.decisions import gate, submit_verdict
from services.review.requests import request_lifecycle_status
from tests import discovery_support as ds


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["tracked", "extras", "empty_script", "base_only", "epoch"])
async def test_rollout_gate_catches_untracked_bundles_and_empty_scripts(mode):
    engine = ds.make_engine()
    try:
        sessions = await ds.create_schema(engine)
        async with sessions() as db:
            owner = await ds.user(db)
            listing = await ds.skill(db, owner)
            version = await db.get(SkillVersion, listing.latest_version_id)
            if mode == "tracked":
                version.content_revision = "a" * 64
            elif mode == "extras":
                version.extra_files = [{"path": "helper.txt", "content": "test"}]
            elif mode == "base_only":
                version.base_revision = "a" * 64
            elif mode == "epoch":
                version.review_epoch = 1
            else:
                version.script_filename = "empty.sh"
                version.script_content = ""
            await db.flush()
            assert await teamspace.skill_transition_needs_rollout_gate(db, listing.id)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("actor_role", [UserRole.user, UserRole.reviewer, UserRole.admin])
async def test_publication_marks_every_version_even_for_global_actor(monkeypatch, actor_role):
    engine = ds.make_engine()
    try:
        sessions = await ds.create_schema(engine)
        async with sessions() as db:
            owner = await ds.user(db, role=actor_role)
            team = await ds.team_with_member(db, owner)
            listing = await ds.skill(db, owner, is_private=True, team_id=team.id, version="1.0.0")
            old = await ds.add_skill_version(
                db, listing, owner, version="0.9.0", status=ListingStatus.archived, set_latest=False
            )
            pending = await ds.add_skill_version(
                db, listing, owner, version="1.1.0", status=ListingStatus.pending, set_latest=False
            )
            draft = await ds.add_skill_version(
                db, listing, owner, version="1.2.0", status=ListingStatus.draft, set_latest=False
            )
            rejected = await ds.add_skill_version(
                db, listing, owner, version="1.3.0", status=ListingStatus.rejected, set_latest=False
            )
            approved_id = listing.latest_version_id
            approved = await db.get(SkillVersion, approved_id)
            approved.reviewed_by = owner.id
            old.reviewed_by = owner.id
            listing.is_private = False
            assert await teamspace.review_publication_to_public(listing, owner, db, was_private=True)
            await db.commit()
            listing_id = listing.id
            version_ids = {"old": old.id, "pending": pending.id, "draft": draft.id, "rejected": rejected.id}

        async with sessions() as db:
            versions = {name: await db.get(SkillVersion, id_) for name, id_ in version_ids.items()}
            current = await db.get(SkillVersion, approved_id)
            assert all(row.requires_global_review for row in [*versions.values(), current])
            assert (current.status, current.pre_public_status) == (ListingStatus.pending, "approved")
            assert current.pre_public_reviewed_by == owner.id
            assert current.reviewed_by is None
            assert (versions["old"].status, versions["old"].pre_public_status) == (ListingStatus.pending, "archived")
            assert versions["old"].pre_public_reviewed_by == owner.id
            assert (versions["pending"].status, versions["pending"].pre_public_status) == (ListingStatus.pending, None)
            assert versions["draft"].status == ListingStatus.draft
            assert versions["rejected"].status == ListingStatus.rejected
            assert (await db.get(SkillListing, listing_id)).latest_version_id == approved_id
        async with sessions() as db:
            reviews = (await db.execute(select(Review).where(Review.subject_id == listing_id))).scalars().all()
            # Every pending (or requeued) release has an open global review; drafts and rejections have none.
            assert {r.version for r in reviews} == {"0.9.0", "1.0.0", "1.1.0"}
            assert all(r.state == ReviewState.open and not r.is_private for r in reviews)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_flip_resolves_team_notice_and_routes_each_round_to_correct_audience():
    engine = ds.make_engine()
    try:
        async with engine.begin() as conn:
            await conn.run_sync(
                Base.metadata.create_all,
                tables=[*ds.TABLES],
            )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            owner = await ds.user(db)
            reviewer = await ds.user(db, role=UserRole.reviewer)
            team_reviewer = await ds.user(db)
            team = await ds.team_with_member(db, owner)
            db.add(TeamMembership(team_id=team.id, user_id=team_reviewer.id, role=TeamRole.reviewer))
            membership = (
                await db.execute(
                    select(TeamMembership).where(TeamMembership.team_id == team.id, TeamMembership.user_id == owner.id)
                )
            ).scalar_one()
            membership.role = TeamRole.owner
            listing = await ds.skill(db, owner, is_private=True, team_id=team.id, status=ListingStatus.pending)
            await submit_for_review(
                db, "skill", listing, await db.get(SkillVersion, listing.latest_version_id), owner.id
            )
            await db.flush()
            review = await db.scalar(select(Review).where(Review.subject_id == listing.id))
            request = await submit_verdict(db, review, team_reviewer, "request_changes", body="Team-only concern")
            assert (await gate(db, review)).outstanding_requests == 1
            listing.is_private = False
            await teamspace.review_publication_to_public(listing, owner, db, was_private=True)
            assert request.state == "dismissed"
            assert request_lifecycle_status(review, request, {request.id: "withdrawn"}) == "invalidated"
            assert (await gate(db, review)).outstanding_requests == 0
            await db.commit()
            rows = (
                (await db.execute(select(InboxItem).where(InboxItem.kind == InboxKind.review_requested)))
                .scalars()
                .all()
            )
            by_user = {item.user_id: item for item in rows}
            assert by_user[team_reviewer.id].state == InboxState.done
            assert by_user[reviewer.id].state == InboxState.open
            assert not by_user[reviewer.id].is_private_subject
            listing.is_private = True
            await teamspace.review_publication_to_public(listing, owner, db, was_private=False)
            await db.commit()
            assert by_user[team_reviewer.id].state == InboxState.open
            assert by_user[team_reviewer.id].is_private_subject
            assert by_user[reviewer.id].state == InboxState.done
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reversal_restores_old_approvals_but_keeps_cleared_new_release(monkeypatch):
    engine = ds.make_engine()
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=[*ds.TABLES])
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            owner = await ds.user(db)
            team = await ds.team_with_member(db, owner)
            listing = await ds.skill(db, owner, is_private=True, team_id=team.id, version="1.0.0")
            old = await ds.add_skill_version(
                db, listing, owner, version="0.9.0", status=ListingStatus.archived, set_latest=False
            )
            candidate = await ds.add_skill_version(
                db, listing, owner, version="2.0.0", status=ListingStatus.pending, set_latest=False
            )
            await db.refresh(old)
            old.reviewed_by = owner.id
            old_reviewed_at = old.reviewed_at
            listing.is_private = False
            assert await teamspace.review_publication_to_public(listing, owner, db, was_private=True)
            old.status = ListingStatus.rejected  # A global rejection rejects publication, not team approval.
            old.rejection_reason = "Not yet suitable for the public registry"
            candidate.status = ListingStatus.approved  # A genuinely public-reviewed release survives reversal.
            candidate.requires_global_review = False
            listing.latest_version_id = candidate.id
            await db.flush()
            listing.is_private = True
            assert not await teamspace.review_publication_to_public(listing, owner, db, was_private=False)
            await db.commit()
            listing_id, old_id, candidate_id = listing.id, old.id, candidate.id

        async with sessions() as db:
            versions = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing_id))).scalars()
            by_version = {row.version: row for row in versions}
            assert by_version["1.0.0"].status == ListingStatus.approved
            assert by_version["0.9.0"].status == ListingStatus.archived
            assert by_version["0.9.0"].rejection_reason is None
            assert by_version["0.9.0"].reviewed_by == owner.id
            assert by_version["0.9.0"].reviewed_at == old_reviewed_at
            assert by_version["0.9.0"].pre_public_reviewed_by is None
            assert not any(row.requires_global_review or row.pre_public_status for row in by_version.values())
            assert (await db.get(SkillListing, listing_id)).latest_version_id == candidate_id
            assert by_version["0.9.0"].id == old_id
        async with sessions() as db:
            reviews = {r.version: r for r in (await db.execute(select(Review))).scalars().all()}
            # A reversed publication restores the team approval's published review.
            assert reviews["0.9.0"].state == ReviewState.published
            assert reviews["0.9.0"].is_private
    finally:
        await engine.dispose()
