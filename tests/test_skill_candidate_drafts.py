# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""A saved candidate owns new bytes without moving the approved pointer."""

import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import select

from api.routes import component_versions, skill_files
from models.inbox import InboxItem, InboxItemEvent
from models.mcp import ListingStatus
from models.skill import SkillListing, SkillVersion
from models.user import User, UserRole
from schemas.component_version import VersionPublishRequest
from schemas.skill import SkillCandidateDraftRequest, SkillFolderImportDraftRequest, SkillUpdateRequest
from schemas.skill_resources import SkillFileOperations, SkillVersionRevisionRequest
from services.skill_revisions import skill_content_revision
from tests import discovery_support as ds

FIXTURE = json.loads((Path(__file__).parent / "fixtures/skill_folder_contract.json").read_text())


@pytest.mark.asyncio
async def test_version_summary_does_not_load_or_expose_stored_resources():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(
                db, owner, status=ListingStatus.approved, content=FIXTURE["snapshot"]["skill_md_content"]
            )
            approved = await db.get(SkillVersion, listing.latest_version_id)
            approved.extra_files = FIXTURE["snapshot"]["extra_files"]
            await db.commit()
            listing_id, owner_id = listing.id, owner.id
        async with maker() as db:
            owner = await db.get(User, owner_id)
            data = await component_versions._list_versions(
                str(listing_id), 1, 50, SkillListing, SkillVersion, "skill", db, owner
            )
            assert data["total"] == 1
            assert "extra_files" not in data["items"][0]
            assert "skill_md_content" not in data["items"][0]
            assert "script_content" not in data["items"][0]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_authoritative_approved_base_is_owner_only_and_ignores_pending_pointer():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            stranger = await ds.user(db)
            listing = await ds.skill(
                db, owner, status=ListingStatus.approved, content=FIXTURE["snapshot"]["skill_md_content"]
            )
            approved_id = listing.latest_version_id
            await ds.add_skill_version(
                db, listing, owner, version="1.1.0", status=ListingStatus.pending, set_latest=True
            )
            await db.commit()
            listing_id, owner_id, stranger_id = listing.id, owner.id, stranger.id

        async with maker() as db:
            stranger = await db.get(User, stranger_id)
            with pytest.raises(HTTPException) as forbidden:
                await skill_files.get_skill_approved_base(str(listing_id), Response(), db, stranger)
            assert forbidden.value.status_code == 403
            owner = await db.get(User, owner_id)
            response = Response()
            base = await skill_files.get_skill_approved_base(str(listing_id), response, db, owner)
            assert base["version_id"] == str(approved_id)
            assert base["delivery_mode"] == "registry_direct"
            assert base["import_required"] is False
            assert len(base["revision"]) == 64
            assert response.headers["cache-control"] == "no-store"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["git_fetch", "legacy_direct"])
async def test_atomic_folder_import_preserves_old_release_and_refuses_stale_base(source):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            stranger = await ds.user(db)
            listing = await ds.skill(db, owner, status=ListingStatus.approved)
            old = await db.get(SkillVersion, listing.latest_version_id)
            if source == "git_fetch":
                old.delivery_mode = "git_fetch"
                old.git_url = "https://github.com/example/old-skill.git"
                old.git_ref = "main"
            else:
                old.skill_md_content = "# Old skill without conforming frontmatter\n"
            await db.commit()
            listing_id, old_id, owner_id, stranger_id = listing.id, old.id, owner.id, stranger.id

        async with maker() as db:
            owner = await db.get(User, owner_id)
            stranger = await db.get(User, stranger_id)
            base = await skill_files.get_skill_approved_base(str(listing_id), Response(), db, owner)
            assert base["import_required"] is True
            req = SkillFolderImportDraftRequest(
                base_version_id=old_id,
                observed_base_revision=base["revision"],
                version="1.3.0",
                description="Full local folder",
                **FIXTURE["snapshot"],
            )
            with pytest.raises(HTTPException) as forbidden:
                await skill_files.import_skill_folder_candidate(str(listing_id), req, Response(), db, stranger)
            assert forbidden.value.status_code == 403
            with pytest.raises(HTTPException) as stale:
                await skill_files.import_skill_folder_candidate(
                    str(listing_id),
                    req.model_copy(update={"observed_base_revision": "0" * 64}),
                    Response(),
                    db,
                    owner,
                )
            assert stale.value.status_code == 409
            result = await skill_files.import_skill_folder_candidate(str(listing_id), req, Response(), db, owner)
            await db.refresh(await db.get(SkillListing, listing_id))
            assert (await db.get(SkillListing, listing_id)).latest_version_id == old_id
            original = await db.get(SkillVersion, old_id)
            await db.refresh(original, attribute_names=["skill_md_content"])
            assert original.delivery_mode == ("git_fetch" if source == "git_fetch" else "registry_direct")
            if source == "git_fetch":
                assert original.git_url == "https://github.com/example/old-skill.git"
            else:
                assert original.skill_md_content == "# Old skill without conforming frontmatter\n"
            candidate = await db.get(SkillVersion, result.version_id)
            await db.refresh(candidate, attribute_names=["extra_files"])
            assert candidate.base_version_id == old_id
            assert candidate.base_revision == base["revision"]
            assert candidate.status == ListingStatus.draft
            assert candidate.delivery_mode == "registry_direct"
            assert candidate.git_url is None
            assert candidate.extra_files == [resource.model_dump() for resource in req.extra_files]
            assert len(result.files) == len(req.extra_files) + 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["marked_approved", "approved_prerelease_behind_pending"])
async def test_one_shot_publish_never_inherits_uncleared_or_ambiguous_history(monkeypatch, case):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    notify = AsyncMock()
    monkeypatch.setattr(component_versions.inbox, "on_publish", notify)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(
                db,
                owner,
                status=ListingStatus.approved,
                version="1.2.0-rc.1" if case == "approved_prerelease_behind_pending" else "1.2.0",
            )
            if case == "marked_approved":
                (await db.get(SkillVersion, listing.latest_version_id)).requires_global_review = True
            else:
                await ds.add_skill_version(
                    db, listing, owner, version="1.3.0", status=ListingStatus.pending, set_latest=True
                )
            await db.commit()
            with pytest.raises(HTTPException) as blocked:
                await component_versions._publish_version(
                    str(listing.id),
                    VersionPublishRequest(version="1.4.0", description="Unchecked successor"),
                    SkillListing,
                    SkillVersion,
                    "skill",
                    db,
                    owner,
                )
            assert blocked.value.status_code == 409
            assert "No cleared stable approved skill base" in str(blocked.value.detail)
            assert (
                await db.scalar(
                    select(SkillVersion.id).where(
                        SkillVersion.listing_id == listing.id, SkillVersion.version == "1.4.0"
                    )
                )
                is None
            )
            notify.assert_not_awaited()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_draft_and_one_shot_publish_inherit_highest_stable_not_later_backfill(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    monkeypatch.setattr(component_versions.inbox, "on_publish", AsyncMock())
    try:
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(
                db, owner, status=ListingStatus.approved, content=FIXTURE["snapshot"]["skill_md_content"]
            )
            approved = await db.get(SkillVersion, listing.latest_version_id)
            old = await ds.add_skill_version(
                db,
                listing,
                owner,
                version="1.1.0",
                status=ListingStatus.approved,
                content="---\nname: old\ndescription: old\n---\n# Old\n",
                set_latest=False,
            )
            old.released_at = ds.NOW + timedelta(days=2)
            await ds.add_skill_version(
                db,
                listing,
                owner,
                version="1.4.0",
                status=ListingStatus.pending,
                content="---\nname: pending\ndescription: unreviewed\n---\n# Pending\n",
            )
            base_revision = skill_content_revision(listing, approved)
            await db.commit()
            listing_id, approved_id, owner_id = listing.id, approved.id, owner.id

        async with maker() as db:
            owner = await db.get(User, owner_id)
            draft = await skill_files.create_skill_candidate_draft(
                str(listing_id),
                SkillCandidateDraftRequest(
                    base_version_id=approved_id,
                    observed_base_revision=base_revision,
                    version="1.5.0",
                    description="Saved on current approved base",
                ),
                Response(),
                db,
                owner,
            )
            saved = await db.get(SkillVersion, draft.version_id)
            assert saved.base_version_id == approved_id
            assert saved.skill_md_content == FIXTURE["snapshot"]["skill_md_content"]
            published = await component_versions._publish_version(
                str(listing_id),
                VersionPublishRequest(version="1.6.0", description="One shot"),
                SkillListing,
                SkillVersion,
                "skill",
                db,
                owner,
            )
            assert published["version"] == "1.6.0"
            released = (
                await db.execute(
                    select(SkillVersion).where(SkillVersion.listing_id == listing_id, SkillVersion.version == "1.6.0")
                )
            ).scalar_one()
            assert released.base_version_id == approved_id
            assert released.skill_md_content == FIXTURE["snapshot"]["skill_md_content"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rejected_git_version_is_editable_and_resubmittable_behind_approved_pointer():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    async with engine.begin() as conn:
        await conn.run_sync(InboxItem.__table__.create)
        await conn.run_sync(InboxItemEvent.__table__.create)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            await ds.user(db, role=UserRole.reviewer)
            listing = await ds.skill(db, owner, status=ListingStatus.approved)
            approved_id = listing.latest_version_id
            rejected = await ds.add_skill_version(
                db, listing, owner, version="1.3.0", status=ListingStatus.rejected, set_latest=False
            )
            rejected.git_url = "https://github.com/example/skill.git"
            # Historic Git metadata can have one orphaned inline script field;
            # changing unrelated content must not silently rewrite it.
            rejected.script_content = "#!/bin/sh\nexit 0\n"
            await db.commit()
            listing_id, rejected_id, owner_id = listing.id, rejected.id, owner.id

        async with maker() as db:
            owner = await db.get(User, owner_id)
            detail = await component_versions._get_version(
                str(listing_id), "1.3.0", SkillListing, SkillVersion, "skill", db, owner
            )
            assert len(detail["revision"]) == 64
            edited = await skill_files.update_skill_version_draft(
                str(listing_id),
                rejected_id,
                SkillUpdateRequest(
                    observed_revision=detail["revision"],
                    description="Corrected Git release",
                    skill_md_content="---\nname: corrected\ndescription: reviewed\n---\n# Corrected\n",
                ),
                Response(),
                db,
                owner,
            )
            assert edited.version_id == rejected_id
            assert edited.revision != detail["revision"]
            assert edited.files == []
            assert (await db.get(SkillVersion, rejected_id)).script_content == "#!/bin/sh\nexit 0\n"
            submitted = await skill_files.submit_skill_version_draft(
                str(listing_id),
                rejected_id,
                SkillVersionRevisionRequest(observed_revision=edited.revision),
                Response(),
                db,
                owner,
            )
            assert submitted.version_id == rejected_id
            listing = await db.get(SkillListing, listing_id)
            assert listing.latest_version_id == approved_id
            version = await db.get(SkillVersion, rejected_id)
            assert version.status == ListingStatus.pending
            notices = (await db.execute(select(InboxItem).where(InboxItem.subject_id == listing_id))).scalars().all()
            assert len(notices) == 1
            assert notices[0].dedupe_key.endswith(":v1.3.0")
            with pytest.raises(HTTPException) as pending:
                await skill_files.update_skill_version_draft(
                    str(listing_id),
                    rejected_id,
                    SkillUpdateRequest(observed_revision=submitted.revision, description="Unreviewed edit"),
                    Response(),
                    db,
                    owner,
                )
            assert pending.value.status_code == 409
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rejected_git_without_source_cannot_enter_review_queue():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(db, owner, status=ListingStatus.approved)
            candidate = await ds.add_skill_version(
                db, listing, owner, version="1.3.0", status=ListingStatus.rejected, set_latest=False
            )
            revision = skill_content_revision(listing, candidate)
            await db.commit()
            with pytest.raises(HTTPException) as exc:
                await skill_files.submit_skill_version_draft(
                    str(listing.id),
                    candidate.id,
                    SkillVersionRevisionRequest(observed_revision=revision),
                    Response(),
                    db,
                    owner,
                )
            assert exc.value.status_code == 422
            assert candidate.status == ListingStatus.rejected
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_draft_fork_preserves_approved_pointer_and_supports_isolated_file_edits():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(db, owner, status=ListingStatus.approved)
            base = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing.id))).scalar_one()
            base.skill_md_content = FIXTURE["snapshot"]["skill_md_content"]
            base.extra_files = [FIXTURE["snapshot"]["extra_files"][0]]
            await db.flush()
            base_revision = skill_content_revision(listing, base)
            base.content_revision = base_revision
            listing_id, base_id, owner_id = listing.id, base.id, owner.id
            base_extras = [item.copy() for item in base.extra_files]
            await db.commit()
            request = SkillCandidateDraftRequest(
                base_version_id=base.id,
                observed_base_revision=base_revision,
                version="1.3.0",
                description="Saved draft",
            )
            created = await skill_files.create_skill_candidate_draft(str(listing.id), request, Response(), db, owner)
            assert created.version_id != base.id
            assert len(created.files) == 2
            assert listing.latest_version_id == base.id
            with pytest.raises(HTTPException) as duplicate:
                await skill_files.create_skill_candidate_draft(str(listing.id), request, Response(), db, owner)
            assert duplicate.value.status_code == 409
            await db.rollback()

        async with maker() as db:
            listing = await db.get(SkillListing, listing_id)
            owner = await db.get(type(owner), owner_id)
            candidate = await db.get(SkillVersion, created.version_id)
            assert listing.latest_version_id == base_id
            assert listing.status == ListingStatus.approved
            assert candidate.status == ListingStatus.draft
            assert candidate.base_version_id == base_id
            assert candidate.base_revision == base_revision
            assert candidate.content_revision == created.revision
            assert candidate.extra_files == base_extras
            with pytest.raises(HTTPException) as coordinates:
                await skill_files.update_skill_version_draft(
                    str(listing_id),
                    candidate.id,
                    SkillUpdateRequest(observed_revision=created.revision, git_url="https://example.com/other.git"),
                    Response(),
                    db,
                    owner,
                )
            assert coordinates.value.status_code == 422
            lock = await skill_files.start_skill_version_edit(
                str(listing_id),
                candidate.id,
                SkillVersionRevisionRequest(observed_revision=created.revision),
                db,
                owner,
            )
            assert lock == {"status": "locked"}
            assert candidate.is_editing is True
            edited = await skill_files.patch_skill_files(
                str(listing.id),
                candidate.id,
                SkillFileOperations.model_validate(
                    {
                        "observed_revision": created.revision,
                        "operations": [{"action": "put", "file": {"path": "new.txt", "content": "candidate only"}}],
                    }
                ),
                Response(),
                db,
                owner,
            )
            assert len(edited.files) == 3
            assert listing.latest_version_id == base_id
            unchanged_base = await db.get(SkillVersion, base_id)
            assert len(unchanged_base.extra_files) == 1
            assert len(candidate.extra_files) == 2
            assert candidate.is_editing is False
            with pytest.raises(HTTPException) as stale:
                await skill_files.start_skill_version_edit(
                    str(listing_id),
                    candidate.id,
                    SkillVersionRevisionRequest(observed_revision=created.revision),
                    db,
                    owner,
                )
            assert stale.value.status_code == 409
            with pytest.raises(HTTPException) as gated:
                await skill_files.submit_skill_version_draft(
                    str(listing.id),
                    candidate.id,
                    SkillVersionRevisionRequest(observed_revision=edited.revision),
                    Response(),
                    db,
                    owner,
                )
            assert gated.value.status_code == 409
            assert candidate.status == ListingStatus.draft
            assert listing.latest_version_id == base_id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_candidate_rejects_stale_base_or_release_not_above_current():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(db, owner, status=ListingStatus.approved)
            base = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing.id))).scalar_one()
            base.skill_md_content = FIXTURE["snapshot"]["skill_md_content"]
            await db.flush()
            revision = skill_content_revision(listing, base)
            listing_id, base_id, owner_id = listing.id, base.id, owner.id
            await db.commit()
            for version, observed in (("1.1.0", revision), ("1.3.0", "a" * 64)):
                with pytest.raises(HTTPException) as exc:
                    await skill_files.create_skill_candidate_draft(
                        str(listing_id),
                        SkillCandidateDraftRequest(
                            base_version_id=base_id,
                            observed_base_revision=observed,
                            version=version,
                            description="Candidate",
                        ),
                        Response(),
                        db,
                        owner,
                    )
                assert exc.value.status_code == 409
                await db.rollback()
                owner = await db.get(type(owner), owner_id)
            assert [
                row.id
                for row in (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing_id)))
                .scalars()
                .all()
            ] == [base_id]
    finally:
        await engine.dispose()
