# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Saved skill drafts merge disjoint files but never silently resolve overlaps."""

import base64
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response

from api.routes import skill_files
from models.mcp import ListingStatus
from models.skill import SkillListing, SkillVersion
from models.user import User
from schemas.skill import SkillCandidateDraftRequest, SkillUpdateRequest
from schemas.skill_resources import SkillDraftRebaseRequest, SkillFileOperations
from services.skill_bundle import validate_skill_bundle
from services.skill_rebase import merge_skill_draft
from services.skill_revisions import skill_content_revision
from tests import discovery_support as ds


def test_rebase_grandfathers_unchanged_historical_frontmatter_name():
    md = "---\nname: UPPER\ndescription: Legacy name\n---\n# Old skill\n"
    shared = dict(
        delivery_mode="registry_direct",
        skill_md_content=md,
        script_filename=None,
        script_content=None,
        description="Old release",
        target_agents=[],
        task_type="general",
        supported_harnesses=["pi"],
        extra_files=[],
    )
    base = SimpleNamespace(**shared)
    draft = SimpleNamespace(**(shared | {"target_agents": ["pi"]}))
    current = SimpleNamespace(**(shared | {"description": "Newer release"}))
    edit, metadata, conflicts = merge_skill_draft(base, draft, current)
    assert conflicts == {"paths": [], "metadata": []}
    assert edit.skill_md_content == md
    assert {file.path for file in edit.files} == {"SKILL.md"}
    assert metadata["target_agents"] == ["pi"] and metadata["description"] == "Newer release"


@pytest.mark.asyncio
async def test_explicit_rebase_requires_new_semver_and_preserves_disjoint_files():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            author = await ds.user(db)
            listing = await ds.skill(db, author, status=ListingStatus.approved)
            base = await db.get(SkillVersion, listing.latest_version_id)
            base.skill_md_content = "---\nname: example\ndescription: Example\n---\n# Skill\n"
            base.extra_files = [{"path": "scripts/empty.sh", "content": "", "executable": True}]
            await db.flush()
            base_revision = skill_content_revision(listing, base)
            base.content_revision = base_revision
            listing_id, base_id, author_id = listing.id, base.id, author.id
            created = await skill_files.create_skill_candidate_draft(
                str(listing.id),
                SkillCandidateDraftRequest(
                    base_version_id=base.id,
                    observed_base_revision=base_revision,
                    version="1.3.0",
                    description=base.description,
                ),
                Response(),
                db,
                author,
            )
            draft_id = created.version_id
            edited = await skill_files.patch_skill_files(
                str(listing.id),
                draft_id,
                SkillFileOperations.model_validate(
                    {
                        "observed_revision": created.revision,
                        "operations": [
                            {"action": "put", "file": {"path": "templates/author.txt", "content": "author"}}
                        ],
                    }
                ),
                Response(),
                db,
                author,
            )
            assert edited.revision != created.revision
            newest = await ds.add_skill_version(
                db,
                listing,
                author,
                version="1.4.0",
                status=ListingStatus.approved,
            )
            newest.delivery_mode = "registry_direct"
            newest.skill_md_content = base.skill_md_content
            newest.extra_files = [
                *base.extra_files,
                {
                    "path": "templates/teammate.bin",
                    "encoding": "base64",
                    "content": base64.b64encode(b"\x00\xff").decode(),
                    "executable": False,
                },
            ]
            newest.description = "Team metadata"
            await db.flush()
            newest_revision = skill_content_revision(listing, newest)
            newest.content_revision = newest_revision
            await db.commit()
            current_id = newest.id

        async with maker() as db:
            author = await db.get(User, author_id)
            req = SkillDraftRebaseRequest(
                observed_revision=edited.revision,
                current_version_id=current_id,
                observed_current_revision=newest_revision,
            )
            with pytest.raises(HTTPException, match="Choose a new stable release number"):
                await skill_files.rebase_skill_draft(str(listing_id), draft_id, req, Response(), db, author)
            await db.rollback()
            author = await db.get(User, author_id)
            with pytest.raises(HTTPException, match="Current approved release changed"):
                await skill_files.rebase_skill_draft(
                    str(listing_id),
                    draft_id,
                    req.model_copy(update={"observed_current_revision": "0" * 64, "new_version": "1.5.0"}),
                    Response(),
                    db,
                    author,
                )
            await db.rollback()
            author = await db.get(User, author_id)
            rebased = await skill_files.rebase_skill_draft(
                str(listing_id),
                draft_id,
                req.model_copy(update={"new_version": "1.5.0"}),
                Response(),
                db,
                author,
            )
            assert rebased.revision != edited.revision
            assert {item.path for item in rebased.files} == {
                "SKILL.md",
                "scripts/empty.sh",
                "templates/author.txt",
                "templates/teammate.bin",
            }
            assert next(item for item in rebased.files if item.path == "scripts/empty.sh").mode == "0755"
            listing = await db.get(SkillListing, listing_id)
            draft = await db.get(SkillVersion, draft_id)
            base = await db.get(SkillVersion, base_id)
            assert listing.latest_version_id == current_id
            assert draft.base_version_id == current_id
            assert draft.base_revision == newest_revision
            assert draft.description == "Team metadata"
            assert draft.version == "1.5.0"
            assert draft.status == ListingStatus.draft
            assert base.extra_files == [{"path": "scripts/empty.sh", "content": "", "executable": True}]
            files = validate_skill_bundle(
                delivery_mode=draft.delivery_mode,
                skill_md_content=draft.skill_md_content,
                script_content=draft.script_content,
                script_filename=draft.script_filename,
                extra_files=draft.extra_files,
            )
            assert next(file for file in files if file.path == "templates/teammate.bin").content == b"\x00\xff"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rebase_refuses_overlapping_file_and_metadata_changes_without_mutating_saved_draft():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(db, owner, status=ListingStatus.approved)
            base = await db.get(SkillVersion, listing.latest_version_id)
            base.skill_md_content = "---\nname: example\ndescription: Example\n---\n# Base\n"
            await db.flush()
            created = await skill_files.create_skill_candidate_draft(
                str(listing.id),
                SkillCandidateDraftRequest(
                    base_version_id=base.id,
                    observed_base_revision=skill_content_revision(listing, base),
                    version="1.6.0",
                    description=base.description,
                ),
                Response(),
                db,
                owner,
            )
            edited = await skill_files.update_skill_version_draft(
                str(listing.id),
                created.version_id,
                SkillUpdateRequest(observed_revision=created.revision, description="Author changed metadata"),
                Response(),
                db,
                owner,
            )
            edited = await skill_files.patch_skill_files(
                str(listing.id),
                created.version_id,
                SkillFileOperations.model_validate(
                    {
                        "observed_revision": edited.revision,
                        "operations": [{"action": "put", "file": {"path": "same.txt", "content": "author"}}],
                    }
                ),
                Response(),
                db,
                owner,
            )
            newest = await ds.add_skill_version(db, listing, owner, version="1.5.0", status=ListingStatus.approved)
            newest.delivery_mode = "registry_direct"
            newest.skill_md_content = base.skill_md_content
            newest.extra_files = [{"path": "same.txt", "content": "teammate"}]
            newest.description = "Team changed metadata"
            await db.flush()
            latest_revision = skill_content_revision(listing, newest)
            newest.content_revision = latest_revision
            await db.commit()
            with pytest.raises(HTTPException) as conflict:
                await skill_files.rebase_skill_draft(
                    str(listing.id),
                    created.version_id,
                    SkillDraftRebaseRequest(
                        observed_revision=edited.revision,
                        current_version_id=newest.id,
                        observed_current_revision=latest_revision,
                    ),
                    Response(),
                    db,
                    owner,
                )
            assert conflict.value.status_code == 409
            assert conflict.value.detail["paths"] == ["same.txt"]
            assert conflict.value.detail["metadata"] == ["description"]
            assert (await db.get(SkillVersion, created.version_id)).base_version_id == base.id
            assert (await db.get(SkillVersion, created.version_id)).content_revision == edited.revision
    finally:
        await engine.dispose()
