# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tracked skill identity transitions never strand, replay or leak reviewed folders."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException, Request, Response
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.deps import get_current_user, get_db
from api.routes import co_authors, registry, review, skill, skill_files
from models.mcp import ListingStatus
from models.skill import SkillListing, SkillVersion
from models.team import TeamMembership, TeamRole
from models.user import User, UserRole
from schemas.component_version import VersionReviewRequest
from schemas.skill import SkillFolderDraftRequest
from schemas.skill_resources import SkillVersionRevisionRequest
from services.skill_revisions import skill_content_revision
from tests import discovery_support as ds

_MD = "---\nname: security-review\ndescription: Reviews security changes\n---\n# Review\n"
_DRAFT = json.loads((Path(__file__).parent / "fixtures/skill_folder_contract.json").read_text())["author_create"][
    "body"
]


@pytest.mark.asyncio
async def test_tracked_folder_visibility_rebinds_all_revisions_and_reversal_cannot_replay(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    monkeypatch.setattr("services.dynamic_settings.get_bool", AsyncMock(return_value=True))
    monkeypatch.setattr("services.inbox.sources.on_review_requested", AsyncMock(return_value=0))
    monkeypatch.setattr("services.inbox.sources.on_review_withdrawn", AsyncMock(return_value=0))
    try:
        async with maker() as db:
            owner = await ds.user(db)
            team = await ds.team_with_member(db, owner)
            team.is_private = False
            membership = (
                await db.execute(select(TeamMembership).where(TeamMembership.team_id == team.id))
            ).scalar_one()
            membership.role = TeamRole.owner
            manifest = await skill.create_skill_folder_draft(
                SkillFolderDraftRequest.model_validate(
                    _DRAFT
                    | {
                        "name": "Security Review",
                        "skill_md_content": _MD,
                        "team_id": team.id,
                        "visibility": "team",
                        "extra_files": [],
                    }
                ),
                db,
                owner,
            )
            first = manifest.revision
            listing_id, version_id, owner_id = manifest.listing_id, manifest.version_id, owner.id

        async with maker() as db:
            owner = await db.get(User, owner_id)
            result = await registry.update_registry_visibility(
                "skill",
                str(listing_id),
                registry.VisibilityUpdateRequest(visibility="public"),
                Request({"type": "http"}),
                db,
                owner,
            )
            assert result["visibility"] == "public"
        async with maker() as db:
            owner = await db.get(User, owner_id)
            public = await skill_files.get_skill_manifest(str(listing_id), version_id, Response(), db, owner)
            assert public.revision != first
            with pytest.raises(HTTPException) as stale:
                await skill_files.submit_skill_version_draft(
                    str(listing_id),
                    version_id,
                    SkillVersionRevisionRequest(observed_revision=first),
                    Response(),
                    db,
                    owner,
                )
            assert stale.value.status_code == 409
            await db.rollback()
        async with maker() as db:
            owner = await db.get(User, owner_id)
            submitted = await skill_files.submit_skill_version_draft(
                str(listing_id),
                version_id,
                SkillVersionRevisionRequest(observed_revision=public.revision),
                Response(),
                db,
                owner,
            )
            assert submitted.revision == public.revision
        async with maker() as db:
            owner = await db.get(User, owner_id)
            result = await registry.update_registry_visibility(
                "skill",
                str(listing_id),
                registry.VisibilityUpdateRequest(visibility="team"),
                Request({"type": "http"}),
                db,
                owner,
            )
            assert result["visibility"] == "team"
        async with maker() as db:
            owner = await db.get(User, owner_id)
            restored = await skill_files.get_skill_manifest(str(listing_id), version_id, Response(), db, owner)
            assert restored.revision not in (first, public.revision)
            version = await db.get(SkillVersion, version_id)
            listing = await db.get(SkillListing, listing_id)
            assert version.content_revision == skill_content_revision(listing, version) == restored.revision
            assert version.status == ListingStatus.pending
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_tracked_public_transfer_rebinds_review_and_revokes_previous_owner(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    monkeypatch.setattr("services.dynamic_settings.get_bool", AsyncMock(return_value=True))
    try:
        async with maker() as db:
            owner = await ds.user(db)
            successor = await ds.user(db)
            manifest = await skill.create_skill_folder_draft(
                SkillFolderDraftRequest.model_validate(
                    _DRAFT | {"name": "Security Review", "skill_md_content": _MD, "extra_files": []}
                ),
                db,
                owner,
            )
            old_revision = manifest.revision
            listing_id, version_id, old_owner_id, new_owner_id = (
                manifest.listing_id,
                manifest.version_id,
                owner.id,
                successor.id,
            )

        async with maker() as db:
            owner = await db.get(User, old_owner_id)
            successor = await db.get(User, new_owner_id)
            result = await co_authors.transfer_ownership(
                "skills", str(listing_id), co_authors.TransferOwnershipRequest(username=successor.username), db, owner
            )
            assert result.owner_id == str(successor.id)
            assert result.qualified_name == f"{successor.username}/security-review"
        async with maker() as db:
            owner = await db.get(User, old_owner_id)
            successor = await db.get(User, new_owner_id)
            listing = await db.get(SkillListing, listing_id)
            version = await db.get(SkillVersion, version_id)
            assert listing.submitted_by == successor.id
            assert version.content_revision != old_revision
            assert version.content_revision == skill_content_revision(listing, version)
            current = await skill_files.get_skill_manifest(str(listing_id), version_id, Response(), db, successor)
            assert current.revision == version.content_revision
            with pytest.raises(HTTPException) as hidden:
                await skill_files.get_skill_manifest(str(listing_id), version_id, Response(), db, owner)
            assert hidden.value.status_code in (403, 404)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_public_tracked_release_transfer_requeues_global_review(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    monkeypatch.setattr("services.dynamic_settings.get_bool", AsyncMock(return_value=True))
    try:
        async with maker() as db:
            owner = await ds.user(db)
            successor = await ds.user(db)
            listing = await ds.skill(db, owner, content=_MD)
            release = await db.get(SkillVersion, listing.latest_version_id)
            release.content_revision = skill_content_revision(listing, release)
            listing_id, version_id = listing.id, release.id
            await db.commit()
            result = await co_authors.transfer_ownership(
                "skills", str(listing_id), co_authors.TransferOwnershipRequest(username=successor.username), db, owner
            )
            assert result.owner_id == str(successor.id)
        async with maker() as db:
            listing = await db.get(SkillListing, listing_id)
            release = await db.get(SkillVersion, version_id)
            assert release.status == ListingStatus.pending
            assert release.requires_global_review
            assert release.content_revision == skill_content_revision(listing, release)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_all_version_rebind_preserves_draft_base_and_private_review_provenance(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    monkeypatch.setattr("services.dynamic_settings.get_bool", AsyncMock(return_value=True))
    monkeypatch.setattr("services.inbox.sources.on_review_requested", AsyncMock(return_value=0))
    monkeypatch.setattr("services.inbox.sources.on_review_withdrawn", AsyncMock(return_value=0))
    try:
        async with maker() as db:
            owner = await ds.user(db)
            team = await ds.team_with_member(db, owner)
            team.is_private = False
            membership = (
                await db.execute(select(TeamMembership).where(TeamMembership.team_id == team.id))
            ).scalar_one()
            membership.role = TeamRole.owner
            listing = await ds.skill(db, owner, is_private=True, team_id=team.id, content=_MD)
            base = await db.get(SkillVersion, listing.latest_version_id)
            base.reviewed_by = owner.id
            base.content_revision = skill_content_revision(listing, base)
            draft = await ds.add_skill_version(
                db, listing, owner, version="2.0.0", status=ListingStatus.draft, content=_MD, set_latest=False
            )
            draft.base_version_id = base.id
            draft.base_revision = base.content_revision
            draft.content_revision = skill_content_revision(listing, draft)
            old_base_revision, old_draft_revision = base.content_revision, draft.content_revision
            listing_id, base_id, draft_id, owner_id = listing.id, base.id, draft.id, owner.id
            await db.commit()

        async with maker() as db:
            owner = await db.get(User, owner_id)
            await registry.update_registry_visibility(
                "skill",
                str(listing_id),
                registry.VisibilityUpdateRequest(visibility="public"),
                Request({"type": "http"}),
                db,
                owner,
            )
        async with maker() as db:
            listing = await db.get(SkillListing, listing_id)
            base = await db.get(SkillVersion, base_id)
            draft = await db.get(SkillVersion, draft_id)
            assert (base.status, base.pre_public_status) == (ListingStatus.pending, "approved")
            assert base.requires_global_review and draft.requires_global_review
            assert draft.status == ListingStatus.draft
            assert base.content_revision != old_base_revision
            assert draft.content_revision != old_draft_revision
            assert draft.base_revision == base.content_revision == skill_content_revision(listing, base)
            assert draft.content_revision == skill_content_revision(listing, draft)

        async with maker() as db:
            owner = await db.get(User, owner_id)
            await registry.update_registry_visibility(
                "skill",
                str(listing_id),
                registry.VisibilityUpdateRequest(visibility="team"),
                Request({"type": "http"}),
                db,
                owner,
            )
        async with maker() as db:
            listing = await db.get(SkillListing, listing_id)
            base = await db.get(SkillVersion, base_id)
            draft = await db.get(SkillVersion, draft_id)
            assert base.status == ListingStatus.approved
            assert base.reviewed_by == owner_id
            assert not base.requires_global_review and not draft.requires_global_review
            assert draft.status == ListingStatus.draft
            assert draft.base_revision == skill_content_revision(listing, base)
            assert draft.content_revision == skill_content_revision(listing, draft)
            assert base.content_revision != old_base_revision
            assert draft.content_revision != old_draft_revision
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_transferred_public_release_needs_fresh_global_exact_decision(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    monkeypatch.setattr("services.dynamic_settings.get_bool", AsyncMock(return_value=True))
    monkeypatch.setattr("services.inbox.sources.on_review_requested", AsyncMock(return_value=0))
    monkeypatch.setattr("api.routes.component_versions.inbox.on_review_decided", AsyncMock(return_value=0))
    try:
        async with maker() as db:
            owner = await ds.user(db)
            successor = await ds.user(db)
            reviewer = await ds.user(db, role=UserRole.reviewer)
            listing = await ds.skill(db, owner, content=_MD)
            release = await db.get(SkillVersion, listing.latest_version_id)
            release.content_revision = skill_content_revision(listing, release)
            previous = release.content_revision
            listing_id, version_id, reviewer_id = listing.id, release.id, reviewer.id
            await db.commit()
            await co_authors.transfer_ownership(
                "skills", str(listing_id), co_authors.TransferOwnershipRequest(username=successor.username), db, owner
            )
        async with maker() as db:
            reviewer = await db.get(User, reviewer_id)
            detail = await review.get_skill_version_review(str(listing_id), version_id, Response(), db, reviewer)
            assert detail["revision"] != previous
            with pytest.raises(HTTPException) as stale:
                await review.decide_skill_version(
                    str(listing_id),
                    version_id,
                    VersionReviewRequest(action="approve", observed_revision=previous),
                    db,
                    reviewer,
                )
            assert stale.value.status_code == 409
            await db.rollback()
        async with maker() as db:
            reviewer = await db.get(User, reviewer_id)
            decision = await review.decide_skill_version(
                str(listing_id),
                version_id,
                VersionReviewRequest(action="approve", observed_revision=detail["revision"]),
                db,
                reviewer,
            )
            assert decision["new_status"] == "approved"
        async with maker() as db:
            version = await db.get(SkillVersion, version_id)
            assert version.status == ListingStatus.approved
            assert not version.requires_global_review
            assert version.pre_public_status is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_team_owned_tracked_release_transfer_reviews_final_public_identity(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    monkeypatch.setattr("services.dynamic_settings.get_bool", AsyncMock(return_value=True))
    requested = AsyncMock(return_value=0)
    monkeypatch.setattr("services.inbox.sources.on_review_requested", requested)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            successor = await ds.user(db)
            team = await ds.team_with_member(db, owner)
            membership = (
                await db.execute(select(TeamMembership).where(TeamMembership.team_id == team.id))
            ).scalar_one()
            membership.role = TeamRole.owner
            listing = await ds.skill(db, owner, is_private=True, team_id=team.id, content=_MD)
            version = await db.get(SkillVersion, listing.latest_version_id)
            version.content_revision = skill_content_revision(listing, version)
            listing_id, version_id = listing.id, version.id
            await db.commit()
            result = await co_authors.transfer_ownership(
                "skills", str(listing_id), co_authors.TransferOwnershipRequest(username=successor.username), db, owner
            )
            assert result.qualified_name == f"{successor.username}/security-review"
        async with maker() as db:
            listing = await db.get(SkillListing, listing_id)
            version = await db.get(SkillVersion, version_id)
            assert not listing.is_private and listing.team_id is None
            assert (version.status, version.pre_public_status) == (ListingStatus.pending, "approved")
            assert version.requires_global_review
            assert version.content_revision == skill_content_revision(listing, version)
            assert requested.await_args.kwargs["global_only"] is True
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_corrupted_tracked_revision_refuses_transition_without_changing_identity(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    monkeypatch.setattr("services.dynamic_settings.get_bool", AsyncMock(return_value=True))
    try:
        async with maker() as db:
            owner = await ds.user(db)
            successor = await ds.user(db)
            listing = await ds.skill(db, owner, content=_MD)
            version = await db.get(SkillVersion, listing.latest_version_id)
            version.content_revision = "0" * 64
            listing_id, owner_id = listing.id, owner.id
            await db.commit()
        async with maker() as db:
            owner = await db.get(User, owner_id)
            with pytest.raises(HTTPException) as invalid:
                await co_authors.transfer_ownership(
                    "skills",
                    str(listing_id),
                    co_authors.TransferOwnershipRequest(username=successor.username),
                    db,
                    owner,
                )
            assert invalid.value.status_code == 409
            await db.rollback()
        async with maker() as db:
            listing = await db.get(SkillListing, listing_id)
            version = await db.get(SkillVersion, listing.latest_version_id)
            assert listing.submitted_by == owner_id
            assert version.status == ListingStatus.approved
            assert version.content_revision == "0" * 64
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_transfer_refuses_unbound_resource_bearing_release(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    monkeypatch.setattr("services.dynamic_settings.get_bool", AsyncMock(return_value=True))
    try:
        async with maker() as db:
            owner = await ds.user(db)
            successor = await ds.user(db)
            manifest = await skill.create_skill_folder_draft(
                SkillFolderDraftRequest.model_validate(_DRAFT | {"name": "Security Review", "skill_md_content": _MD}),
                db,
                owner,
            )
            version = await db.get(SkillVersion, manifest.version_id)
            assert version.extra_files
            version.content_revision = None  # corrupt a persisted whole-folder observation
            listing_id, version_id, owner_id = manifest.listing_id, version.id, owner.id
            await db.commit()
        async with maker() as db:
            owner = await db.get(User, owner_id)
            with pytest.raises(HTTPException) as invalid:
                await co_authors.transfer_ownership(
                    "skills",
                    str(listing_id),
                    co_authors.TransferOwnershipRequest(username=successor.username),
                    db,
                    owner,
                )
            assert invalid.value.status_code == 409
            await db.rollback()
        async with maker() as db:
            listing = await db.get(SkillListing, listing_id)
            version = await db.get(SkillVersion, version_id)
            assert listing.submitted_by == owner_id
            assert version.content_revision is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_transfer_refuses_unresolved_old_team_approval_provenance(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    monkeypatch.setattr("services.dynamic_settings.get_bool", AsyncMock(return_value=True))
    monkeypatch.setattr("services.inbox.sources.on_review_requested", AsyncMock(return_value=0))
    monkeypatch.setattr("services.inbox.sources.on_review_withdrawn", AsyncMock(return_value=0))
    try:
        async with maker() as db:
            owner = await ds.user(db)
            successor = await ds.user(db)
            team = await ds.team_with_member(db, owner)
            team.is_private = False
            membership = (
                await db.execute(select(TeamMembership).where(TeamMembership.team_id == team.id))
            ).scalar_one()
            membership.role = TeamRole.owner
            listing = await ds.skill(db, owner, is_private=True, team_id=team.id, content=_MD)
            version = await db.get(SkillVersion, listing.latest_version_id)
            version.content_revision = skill_content_revision(listing, version)
            listing_id, version_id, owner_id, successor_name = listing.id, version.id, owner.id, successor.username
            await db.commit()
        async with maker() as db:
            owner = await db.get(User, owner_id)
            await registry.update_registry_visibility(
                "skill",
                str(listing_id),
                registry.VisibilityUpdateRequest(visibility="public"),
                Request({"type": "http"}),
                db,
                owner,
            )
        async with maker() as db:
            owner = await db.get(User, owner_id)
            with pytest.raises(HTTPException) as unresolved:
                await co_authors.transfer_ownership(
                    "skills", str(listing_id), co_authors.TransferOwnershipRequest(username=successor_name), db, owner
                )
            assert unresolved.value.status_code == 409
            await db.rollback()
        async with maker() as db:
            listing = await db.get(SkillListing, listing_id)
            version = await db.get(SkillVersion, version_id)
            assert listing.submitted_by == owner_id
            assert version.pre_public_status == "approved" and version.requires_global_review
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_http_visibility_and_transfer_require_gate_and_preserve_manifest(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    enabled = False

    async def current_gate(*_args):
        return enabled

    monkeypatch.setattr("services.dynamic_settings.get_bool", current_gate)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            successor = await ds.user(db)
            team = await ds.team_with_member(db, owner)
            team.is_private = False
            membership = (
                await db.execute(select(TeamMembership).where(TeamMembership.team_id == team.id))
            ).scalar_one()
            membership.role = TeamRole.owner
            manifest = await skill.create_skill_folder_draft(
                SkillFolderDraftRequest.model_validate(
                    _DRAFT
                    | {
                        "name": "Security Review",
                        "skill_md_content": _MD,
                        "team_id": team.id,
                        "visibility": "team",
                        "extra_files": [],
                    }
                ),
                db,
                owner,
            )
            listing_id, version_id, successor_name = manifest.listing_id, manifest.version_id, successor.username
            app = FastAPI()
            app.include_router(registry.router)
            app.include_router(co_authors.router)
            app.dependency_overrides[get_db] = lambda: db
            app.dependency_overrides[get_current_user] = lambda: owner
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                flip = f"/api/v1/registry/skill/{listing_id}/visibility"
                transfer = f"/api/v1/skills/{listing_id}/transfer-ownership"
                assert (await client.patch(flip, json={"visibility": "public"})).status_code == 409
                await db.rollback()
                await db.refresh(owner)
                assert (await client.post(transfer, json={"username": successor_name})).status_code == 409
                await db.rollback()
                await db.refresh(owner)
                enabled = True
                changed = await client.patch(flip, json={"visibility": "public"})
                assert changed.status_code == 200, changed.text
                assert changed.json()["visibility"] == "public"
                moved = await client.post(transfer, json={"username": successor_name})
                assert moved.status_code == 200, moved.text
                assert moved.json()["qualified_name"] == f"{successor_name}/security-review"
        async with maker() as db:
            listing = await db.get(SkillListing, listing_id)
            version = await db.get(SkillVersion, version_id)
            assert listing.namespace == successor_name
            assert version.content_revision != manifest.revision
            assert version.content_revision == skill_content_revision(listing, version)
    finally:
        await engine.dispose()
