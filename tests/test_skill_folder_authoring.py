# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Saved initial direct drafts require a full, valid tree and return a manifest."""

import json
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import quote

import pytest
from fastapi import FastAPI, HTTPException, Request, Response
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.deps import get_current_user, get_db, get_registry_user
from api.routes import component_versions, registry, skill, skill_files
from models.mcp import ListingStatus
from models.skill import SkillListing, SkillVersion
from models.team import TeamMembership, TeamRole
from models.user import User, UserRole
from schemas.skill import SkillDraftRequest, SkillFolderDraftRequest, SkillUpdateRequest
from schemas.skill_resources import SkillVersionRevisionRequest
from services.skill_revisions import skill_content_revision, verified_skill_revision
from tests import discovery_support as ds

FIXTURE = json.loads((Path(__file__).parent / "fixtures/skill_folder_contract.json").read_text())


@pytest.mark.asyncio
async def test_create_initial_folder_draft_saves_exact_tree_without_git_or_legacy_script():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            data = FIXTURE["author_create"]["body"] | FIXTURE["snapshot"]
            req = SkillFolderDraftRequest.model_validate(data)
            manifest = await skill.create_skill_folder_draft(req, db, owner)
            assert len(manifest.files) == 10
            assert manifest.files[0].path == "SKILL.md"
            assert next(f.mode for f in manifest.files if f.path == "scripts/empty.sh") == "0755"
            assert next(f.size for f in manifest.files if f.path == "assets/logo.bin") == 2
            assert next(f.size for f in manifest.files if f.path == "scripts/empty.sh") == 0

        async with maker() as db:
            listing = (
                await db.execute(select(SkillListing).where(SkillListing.id == manifest.listing_id))
            ).scalar_one()
            version = (
                await db.execute(select(SkillVersion).where(SkillVersion.id == manifest.version_id))
            ).scalar_one()
            assert version.status == ListingStatus.draft
            assert listing.latest_version_id == version.id
            assert version.delivery_mode == "registry_direct"
            assert version.skill_md_content == data["skill_md_content"]
            assert version.extra_files == [f.model_dump() for f in req.extra_files]
            assert version.git_url is None and version.git_ref is None
            assert version.script_filename is None and version.script_content is None
            assert version.content_revision == manifest.revision == skill_content_revision(listing, version)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_draft_routes_bind_revision_when_folder_delivery_is_added():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            data = FIXTURE["author_create"]["body"] | FIXTURE["snapshot"]
            direct = await skill.save_skill_draft(
                SkillDraftRequest.model_validate(
                    data | {"name": "direct-revision", "delivery_mode": "registry_direct"}
                ),
                db,
                owner,
            )
            direct_listing = await db.get(SkillListing, direct.id)
            assert verified_skill_revision(direct_listing, direct_listing.latest_version)

            git = await skill.save_skill_draft(
                SkillDraftRequest.model_validate(
                    {"name": "git-to-direct", "git_url": "https://example.test/skill.git"}
                ),
                db,
                owner,
            )
            git_listing = await db.get(SkillListing, git.id)
            assert git_listing.latest_version.content_revision is None
            await skill.update_skill_draft(
                str(git.id),
                SkillUpdateRequest(
                    delivery_mode="registry_direct",
                    skill_md_content=data["skill_md_content"],
                    extra_files=data["extra_files"],
                    git_url="",
                    git_ref="",
                ),
                db,
                owner,
            )
            await db.refresh(git_listing)
            assert verified_skill_revision(git_listing, git_listing.latest_version)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_draft_edit_cannot_strip_frontmatter_from_saved_folder():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            req = SkillFolderDraftRequest.model_validate(FIXTURE["author_create"]["body"] | FIXTURE["snapshot"])
            manifest = await skill.create_skill_folder_draft(req, db, owner)
            stripped = "---\nname: sample\n---\n# Sample\n"
            with pytest.raises(HTTPException) as exc:
                await skill.update_skill_draft(
                    str(manifest.listing_id),
                    SkillUpdateRequest(observed_revision=manifest.revision, skill_md_content=stripped),
                    db,
                    owner,
                )
            assert exc.value.status_code == 422
            await db.rollback()

        async with maker() as db:
            version = await db.get(SkillVersion, manifest.version_id)
            assert version.skill_md_content == req.skill_md_content
            assert version.content_revision == manifest.revision
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_folder_draft_http_route_returns_content_free_manifest():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            app = FastAPI()
            app.include_router(skill.router)
            app.dependency_overrides[get_db] = lambda: db
            app.dependency_overrides[get_current_user] = lambda: owner
            app.dependency_overrides[get_registry_user] = lambda: owner
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    FIXTURE["author_create"]["path"], json=FIXTURE["author_create"]["body"] | FIXTURE["snapshot"]
                )
                assert response.status_code == 200, response.text
                payload = response.json()
                assert payload["revision"] and len(payload["revision"]) == 64
                assert len(payload["files"]) == 10
                assert "content" not in payload["files"][0]
                base = f"/api/v1/skills/{payload['listing_id']}/versions/{payload['version_id']}"
                manifest = await client.get(base + "/manifest")
                assert manifest.status_code == 200, manifest.text
                assert manifest.json() == payload
                assert manifest.headers["Cache-Control"] == "no-store"
                assert manifest.headers["X-Content-Type-Options"] == "nosniff"
                file = await client.get(base + "/files/scripts/one.sh")
                assert file.json()["content"] == "#!/bin/sh\necho one\n"
                assert file.headers["Content-Type"] == "application/json"
                binary = await client.get(base + "/files/assets/logo.bin")
                assert binary.status_code == 200
                assert binary.content == b"\x00\xff"
                assert binary.headers["Content-Type"] == "application/octet-stream"
                assert (
                    binary.headers["Content-Disposition"]
                    == "attachment; filename=\"logo.bin\"; filename*=UTF-8''logo.bin"
                )
                assert binary.headers["Cache-Control"] == "no-store"
                assert binary.headers["X-Content-Type-Options"] == "nosniff"
                media = app.openapi()["paths"]["/api/v1/skills/{listing_id}/versions/{version_id}/files/{file_path}"][
                    "get"
                ]["responses"]["200"]["content"]
                assert set(media) == {"application/json", "application/octet-stream"}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_binary_file_with_unicode_filename_downloads_without_invalid_header():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            app = FastAPI()
            app.include_router(skill.router)
            app.dependency_overrides[get_db] = lambda: db
            app.dependency_overrides[get_registry_user] = lambda: owner
            app.dependency_overrides[get_current_user] = lambda: owner
            name = "图标.bin"
            data = FIXTURE["author_create"]["body"] | FIXTURE["snapshot"]
            data["extra_files"] = [
                *data["extra_files"],
                {"path": f"assets/{name}", "content": "AP8=", "encoding": "base64"},
            ]
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                created = await client.post(FIXTURE["author_create"]["path"], json=data)
                assert created.status_code == 200, created.text
                result = await client.get(
                    f"/api/v1/skills/{created.json()['listing_id']}/versions/{created.json()['version_id']}"
                    f"/files/assets/{quote(name)}"
                )
                assert result.status_code == 200, result.text
                assert result.content == b"\x00\xff"
                disposition = result.headers["content-disposition"]
                assert disposition.startswith('attachment; filename="__.bin"; ')
                assert disposition.endswith(f"filename*=UTF-8''{quote(name, safe='')}")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_saved_folder_edit_requires_revision_and_preserves_readability():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            reviewer = await ds.user(db, role=UserRole.reviewer)
            req = SkillFolderDraftRequest.model_validate(FIXTURE["author_create"]["body"] | FIXTURE["snapshot"])
            manifest = await skill.create_skill_folder_draft(req, db, owner)
            owner_id, reviewer_id = owner.id, reviewer.id
            with pytest.raises(HTTPException) as stale:
                await skill.update_skill_draft(
                    str(manifest.listing_id), SkillUpdateRequest(description="edited"), db, owner
                )
            assert stale.value.status_code == 409
            await db.rollback()

        async with maker() as db:
            owner = await db.get(User, owner_id)
            listing_id = str(manifest.listing_id)
            await skill.update_skill_draft(
                listing_id,
                SkillUpdateRequest(description="edited", observed_revision=manifest.revision),
                db,
                owner,
            )
        async with maker() as db:
            reviewer = await db.get(User, reviewer_id)
            updated = await skill_files.get_skill_manifest(listing_id, manifest.version_id, Response(), db, owner)
            assert updated.revision != manifest.revision
            assert len(updated.files) == 10
            with pytest.raises(HTTPException) as stale:
                await skill.update_skill_draft(
                    listing_id,
                    SkillUpdateRequest(description="stale", observed_revision=manifest.revision),
                    db,
                    owner,
                )
            assert stale.value.status_code == 409
            await db.rollback()

        async with maker() as db:
            owner = await db.get(User, owner_id)
            reviewer = await db.get(User, reviewer_id)
            for read in (
                lambda: component_versions._get_version(
                    listing_id, "1.1.0", SkillListing, SkillVersion, "skill", db, reviewer
                ),
                lambda: skill.get_skill(listing_id, db, reviewer),
            ):
                with pytest.raises(HTTPException) as hidden:
                    await read()
                assert hidden.value.status_code == 404
            owner_detail = await component_versions._get_version(
                listing_id, "1.1.0", SkillListing, SkillVersion, "skill", db, owner
            )
            assert owner_detail["extra_files"]
            assert owner_detail["description"] == "edited"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_tracked_draft_visibility_flip_refuses_without_stranding_manifest():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            team = await ds.team_with_member(db, owner)
            membership = (
                await db.execute(select(TeamMembership).where(TeamMembership.team_id == team.id))
            ).scalar_one()
            membership.role = TeamRole.owner
            team.is_private = False
            data = FIXTURE["author_create"]["body"] | FIXTURE["snapshot"]
            data.update({"team_id": team.id, "visibility": "team"})
            manifest = await skill.create_skill_folder_draft(SkillFolderDraftRequest.model_validate(data), db, owner)
            owner_id = owner.id
            with pytest.raises(HTTPException) as exc:
                await registry.update_registry_visibility(
                    "skill",
                    str(manifest.listing_id),
                    registry.VisibilityUpdateRequest(visibility="public"),
                    Request({"type": "http"}),
                    db,
                    owner,
                )
            assert exc.value.status_code == 409
            await db.rollback()
        async with maker() as db:
            owner = await db.get(User, owner_id)
            listing = await db.get(SkillListing, manifest.listing_id)
            assert listing.is_private
            current = await skill_files.get_skill_manifest(str(listing.id), manifest.version_id, Response(), db, owner)
            assert current.revision == manifest.revision
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_initial_resource_bearing_folder_refuses_submission_before_rollout(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            data = FIXTURE["author_create"]["body"] | FIXTURE["snapshot"]
            manifest = await skill.create_skill_folder_draft(SkillFolderDraftRequest.model_validate(data), db, owner)
            owner_id = owner.id
        monkeypatch.setattr(skill_files._ds, "get_sync_bool", lambda *_args: True)  # stale process cache must not win
        monkeypatch.setattr(skill_files._ds, "get_bool", AsyncMock(return_value=False))
        async with maker() as db:
            owner = await db.get(User, owner_id)
            with pytest.raises(HTTPException) as exc:
                await skill_files.submit_skill_version_draft(
                    str(manifest.listing_id),
                    manifest.version_id,
                    SkillVersionRevisionRequest(observed_revision=manifest.revision),
                    Response(),
                    db,
                    owner,
                )
            assert exc.value.status_code == 409
            await db.rollback()
        async with maker() as db:
            version = await db.get(SkillVersion, manifest.version_id)
            assert version.status == ListingStatus.draft
            assert version.content_revision == manifest.revision
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_submission_recomputes_revision_after_slash_command_normalization(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            data = FIXTURE["author_create"]["body"] | FIXTURE["snapshot"]
            data["skill_md_content"] = data["skill_md_content"].replace(
                "description: Example skill", "description: Example skill\ncommand: run"
            )
            manifest = await skill.create_skill_folder_draft(SkillFolderDraftRequest.model_validate(data), db, owner)
            await skill.update_skill_draft(
                str(manifest.listing_id),
                SkillUpdateRequest(slash_command=None, observed_revision=manifest.revision),
                db,
                owner,
            )
        monkeypatch.setattr(skill_files.inbox, "on_review_requested", AsyncMock())
        monkeypatch.setattr(skill_files._ds, "get_bool", AsyncMock(return_value=True))
        async with maker() as db:
            owner = await db.get(User, owner.id)
            version = await db.get(SkillVersion, manifest.version_id)
            submitted = await skill_files.submit_skill_version_draft(
                str(manifest.listing_id),
                manifest.version_id,
                SkillVersionRevisionRequest(observed_revision=version.content_revision),
                Response(),
                db,
                owner,
            )
            assert submitted.revision == version.content_revision
        async with maker() as db:
            owner = await db.get(User, owner.id)
            current = await skill_files.get_skill_manifest(
                str(manifest.listing_id), manifest.version_id, Response(), db, owner
            )
            version = await db.get(SkillVersion, manifest.version_id)
            assert version.status == ListingStatus.pending
            assert version.slash_command == "run"
            assert current.revision == version.content_revision
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("md", ["# No frontmatter\n", "---\nname: x\n---\n", "---\ndescription: valid\n---\n"])
async def test_folder_draft_rejects_missing_required_frontmatter_without_persisting(md):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            req = SkillFolderDraftRequest.model_validate(FIXTURE["author_create"]["body"] | {"skill_md_content": md})
            with pytest.raises(HTTPException) as exc:
                await skill.create_skill_folder_draft(req, db, owner)
            assert exc.value.status_code == 422
            assert (await db.execute(select(SkillListing))).scalars().all() == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["Bad_Name", "a--b", "-lead", "trail-", "x" * 65])
async def test_folder_draft_rejects_invalid_agent_skills_name_without_persisting(name):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            md = f"---\nname: {name}\ndescription: valid\n---\n"
            req = SkillFolderDraftRequest.model_validate(FIXTURE["author_create"]["body"] | {"skill_md_content": md})
            with pytest.raises(HTTPException) as exc:
                await skill.create_skill_folder_draft(req, db, owner)
            assert exc.value.status_code == 422
            assert (await db.execute(select(SkillListing))).scalars().all() == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_submission_refuses_folder_whose_name_cannot_install(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            data = FIXTURE["author_create"]["body"] | FIXTURE["snapshot"]
            manifest = await skill.create_skill_folder_draft(SkillFolderDraftRequest.model_validate(data), db, owner)
            version = await db.get(SkillVersion, manifest.version_id)
            listing = await db.get(SkillListing, manifest.listing_id)
            version.skill_md_content = "---\nname: Bad_Name\ndescription: valid\n---\n"  # e.g. a pre-fix draft
            version.content_revision = manifest.revision = skill_content_revision(listing, version)
            await db.commit()
            monkeypatch.setattr(skill_files._ds, "get_bool", AsyncMock(return_value=True))
            with pytest.raises(HTTPException) as exc:
                await skill_files.submit_skill_version_draft(
                    str(manifest.listing_id),
                    manifest.version_id,
                    SkillVersionRevisionRequest(observed_revision=manifest.revision),
                    Response(),
                    db,
                    owner,
                )
            assert exc.value.status_code == 422
            await db.rollback()
            assert (await db.get(SkillVersion, manifest.version_id)).status == ListingStatus.draft
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_approval_refuses_folder_whose_name_cannot_install(monkeypatch):
    """Any route that produced the version (generic publish, pre-fix drafts) hits this last gate."""
    from schemas.component_version import VersionReviewRequest

    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            admin = await ds.user(db, role=UserRole.admin, email="reviewer@example.com")
            data = FIXTURE["author_create"]["body"] | FIXTURE["snapshot"]
            manifest = await skill.create_skill_folder_draft(SkillFolderDraftRequest.model_validate(data), db, owner)
            version = await db.get(SkillVersion, manifest.version_id)
            listing = await db.get(SkillListing, manifest.listing_id)
            version.skill_md_content = "---\nname: Bad_Name\ndescription: valid\n---\n"
            version.status = ListingStatus.pending
            version.content_revision = skill_content_revision(listing, version)
            await db.commit()
            monkeypatch.setattr(component_versions._ds, "get_bool", AsyncMock(return_value=True))
            with pytest.raises(HTTPException) as exc:
                await component_versions._review_version(
                    str(listing.id),
                    "",
                    VersionReviewRequest(action="approve", observed_revision=version.content_revision),
                    SkillListing,
                    SkillVersion,
                    "skill",
                    db,
                    admin,
                    selected_id=version.id,
                )
            assert exc.value.status_code == 422
            await db.rollback()
            assert (await db.get(SkillVersion, manifest.version_id)).status == ListingStatus.pending
    finally:
        await engine.dispose()
