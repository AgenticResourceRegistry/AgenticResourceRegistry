# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""A stored folder must not become installable without the revision that was reviewed."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import func, select

from api.routes import component_versions, review, skill, skill_files
from models.skill import SkillDownload, SkillListing, SkillVersion
from models.user import UserRole
from schemas.component_version import VersionPublishRequest, VersionReviewRequest
from schemas.skill import SkillCandidateDraftRequest, SkillInstallRequest, SkillUpdateRequest
from schemas.skill_resources import SkillVersionRevisionRequest
from services.agent_lock import (
    attach_pinned_components,
    content_digest,
    load_pinned_listings,
    pinned_component_blockers,
)
from services.skill_revisions import skill_content_revision
from tests import discovery_support as ds


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["unbound", "changed_after_review"])
async def test_unreviewed_approved_extra_files_refuse_bundle_and_manifest_without_usage(monkeypatch, tamper):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    async with engine.begin() as conn:
        await conn.run_sync(SkillDownload.__table__.create)
    monkeypatch.setattr("services.dynamic_settings.get_bool", AsyncMock(return_value=True))
    monkeypatch.setattr("api.routes.config.derive_endpoints", AsyncMock(return_value={"api": "https://api.test"}))
    try:
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(db, owner)
            version = await db.get(SkillVersion, listing.latest_version_id)
            if tamper == "changed_after_review":
                version.content_revision = skill_content_revision(listing, version)
            version.extra_files = [{"path": "scripts/unreviewed.sh", "content": "echo unreviewed", "executable": True}]
            listing_id, version_id, owner_id = listing.id, version.id, owner.id
            await db.commit()

        async with maker() as db:
            owner = await db.get(type(owner), owner_id)
            with pytest.raises(HTTPException) as detail_denied:
                await skill.get_skill(str(listing_id), db, owner)
            assert detail_denied.value.status_code == 409
            with pytest.raises(HTTPException) as release_denied:
                await component_versions._get_version(
                    str(listing_id),
                    "1.2.0",
                    SkillListing,
                    SkillVersion,
                    "skill",
                    db,
                    owner,
                )
            assert release_denied.value.status_code == 409
            with pytest.raises(HTTPException) as refused:
                await skill.install_skill(
                    str(listing_id),
                    SkillInstallRequest(harness="pi", supported_features=["skill_extra_files_v1"]),
                    MagicMock(),
                    db,
                    owner,
                )
            assert refused.value.status_code == 409
            await db.rollback()
            owner = await db.get(type(owner), owner_id)
            with pytest.raises(HTTPException) as hidden:
                await skill_files.get_skill_manifest(str(listing_id), version_id, Response(), db, owner)
            assert hidden.value.status_code == 409
            assert await db.scalar(select(func.count()).select_from(SkillDownload)) == 0
            assert (await db.get(SkillVersion, version_id)).download_count == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_generic_publish_refuses_unbound_approved_folder_inheritance(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    monkeypatch.setattr(component_versions.inbox, "on_publish", AsyncMock())
    try:
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(db, owner)
            approved = await db.get(SkillVersion, listing.latest_version_id)
            approved.extra_files = [{"path": "scripts/unreviewed.sh", "content": "echo unreviewed"}]
            listing_id = listing.id
            await db.commit()
            with pytest.raises(HTTPException) as refused:
                await component_versions._publish_version(
                    str(listing_id),
                    VersionPublishRequest(version="2.0.0", description="new release"),
                    type(listing),
                    SkillVersion,
                    "skill",
                    db,
                    owner,
                )
            assert refused.value.status_code == 409
            assert (
                await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing_id))
            ).scalars().all() == [approved]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_draft_update_cannot_overwrite_stale_review_revision():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(db, owner, status=skill.ListingStatus.draft)
            version = await db.get(SkillVersion, listing.latest_version_id)
            version.content_revision = skill_content_revision(listing, version)
            version.extra_files = [{"path": "scripts/unreviewed.sh", "content": "echo unreviewed"}]
            observed = skill_content_revision(listing, version)
            listing_id, version_id, old_revision = listing.id, version.id, version.content_revision
            await db.commit()
            with pytest.raises(HTTPException) as refused:
                await skill.update_skill_draft(
                    str(listing_id),
                    SkillUpdateRequest(observed_revision=observed, description="laundered"),
                    db,
                    owner,
                )
            assert refused.value.status_code == 409
            await db.rollback()
            version = await db.get(SkillVersion, version_id)
            assert version.content_revision == old_revision
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_candidate_cannot_fork_unbound_approved_folder_even_with_computed_observation():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(
                db,
                owner,
                content="---\nname: security-review\ndescription: Review security issues\n---\n# Security Review\n",
            )
            base = await db.get(SkillVersion, listing.latest_version_id)
            base.extra_files = [{"path": "scripts/unreviewed.sh", "content": "echo unreviewed"}]
            revision = skill_content_revision(listing, base)
            listing_id, base_id = listing.id, base.id
            await db.commit()
            with pytest.raises(HTTPException) as refused:
                await skill_files.create_skill_candidate_draft(
                    str(listing_id),
                    SkillCandidateDraftRequest(
                        base_version_id=base_id,
                        observed_base_revision=revision,
                        version="2.0.0",
                        description="Do not launder an unreviewed base",
                    ),
                    Response(),
                    db,
                    owner,
                )
            assert refused.value.status_code == 409
            assert refused.value.detail == "Approved skill base is not a valid folder"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [skill.ListingStatus.draft, skill.ListingStatus.pending])
async def test_unbound_saved_folder_cannot_be_edited_or_withdrawn_into_valid_draft(monkeypatch, status):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    monkeypatch.setattr(skill_files.inbox, "on_review_withdrawn", AsyncMock())
    try:
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(db, owner, status=status)
            version = await db.get(SkillVersion, listing.latest_version_id)
            version.extra_files = [{"path": "scripts/unreviewed.sh", "content": "echo unreviewed"}]
            revision = skill_content_revision(listing, version)
            listing_id, version_id = listing.id, version.id
            await db.commit()
            with pytest.raises(HTTPException) as refused:
                if status == skill.ListingStatus.draft:
                    await skill_files._editable_version(str(listing_id), version_id, revision, db, owner)
                else:
                    await skill_files.withdraw_skill_version(
                        str(listing_id),
                        version_id,
                        SkillVersionRevisionRequest(observed_revision=revision),
                        Response(),
                        db,
                        owner,
                    )
            assert refused.value.status_code == 409
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reviewer_cannot_approve_unbound_folder_with_self_computed_observation(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    monkeypatch.setattr("api.routes.component_versions._ds.get_bool", AsyncMock(return_value=True))
    monkeypatch.setattr("api.routes.component_versions.inbox.on_review_decided", AsyncMock())
    try:
        async with maker() as db:
            author = await ds.user(db)
            reviewer = await ds.user(db, role=UserRole.reviewer)
            listing = await ds.skill(db, author, status=skill.ListingStatus.pending)
            version = await db.get(SkillVersion, listing.latest_version_id)
            version.extra_files = [{"path": "scripts/unreviewed.sh", "content": "echo unreviewed"}]
            revision = skill_content_revision(listing, version)
            listing_id, version_id = listing.id, version.id
            await db.commit()
            with pytest.raises(HTTPException) as refused:
                await review.decide_skill_version(
                    str(listing_id),
                    version_id,
                    VersionReviewRequest(action="approve", observed_revision=revision),
                    db,
                    reviewer,
                )
            assert refused.value.status_code == 409
            assert (await db.get(SkillVersion, version_id)).status == skill.ListingStatus.pending
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_new_agent_pin_cannot_approve_unbound_resource_folder():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(db, owner)
            version = await db.get(SkillVersion, listing.latest_version_id)
            version.extra_files = [{"path": "scripts/unreviewed.sh", "content": "echo unreviewed"}]
            await db.commit()
            with pytest.raises(HTTPException) as refused:
                await attach_pinned_components(
                    db,
                    listing.id,
                    [{"component_type": "skill", "component_id": listing.id}],
                    current_user=owner,
                    require_approved=True,
                )
            assert refused.value.status_code == 400
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_exact_agent_pin_refuses_folder_with_correct_digest_but_missing_review_revision():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(db, owner)
            version = await db.get(SkillVersion, listing.latest_version_id)
            version.extra_files = [{"path": "scripts/run.sh", "content": "echo reviewed", "executable": True}]
            version.content_revision = skill_content_revision(listing, version)
            await db.commit()
            pin = SimpleNamespace(
                component_type="skill",
                component_id=listing.id,
                component_name=listing.name,
                resolved_version_id=version.id,
                resolved_version=version.version,
                resolved_digest=content_digest("skill", version),
            )
            version.content_revision = None  # digests do not include this field
            await db.commit()
            blockers = await pinned_component_blockers(db, [pin])
            assert blockers[0]["status"] == "invalid_review_revision"
            with pytest.raises(HTTPException) as refused:
                await load_pinned_listings(db, [pin], {"skill": {listing.id: listing}})
            assert refused.value.status_code == 409
    finally:
        await engine.dispose()
