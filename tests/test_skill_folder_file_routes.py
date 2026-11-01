# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Version-scoped file batches preserve bytes and reject stale or partial saves."""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException, Response
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.deps import get_current_user, get_db, get_registry_user
from api.routes import skill, skill_files
from models.mcp import ListingStatus
from models.skill import SkillVersion
from schemas.skill_resources import SkillFileOperations
from services.skill_revisions import skill_content_revision
from tests import discovery_support as ds

FIXTURE = json.loads((Path(__file__).parent / "fixtures/skill_folder_contract.json").read_text())


@pytest.mark.asyncio
async def test_http_file_batch_and_authoritative_replace_never_inherit_omitted_files():
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
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                created = await client.post(
                    FIXTURE["author_create"]["path"], json=FIXTURE["author_create"]["body"] | FIXTURE["snapshot"]
                )
                assert created.status_code == 200, created.text
                listing_id, version_id = created.json()["listing_id"], created.json()["version_id"]
                url = f"/api/v1/skills/{listing_id}/versions/{version_id}/files"
                patch = await client.patch(
                    url, json=FIXTURE["author_patch"]["body"] | {"observed_revision": created.json()["revision"]}
                )
                assert patch.status_code == 200, patch.text
                assert patch.headers["Cache-Control"] == "no-store"
                assert len(patch.json()["files"]) == 9
                paths = {entry["path"] for entry in patch.json()["files"]}
                assert "templates/second.txt" in paths
                assert "templates/two.txt" not in paths and "templates/three.txt" not in paths
                file = await client.get(url + "/templates/one.txt")
                assert file.json()["content"] == "replaced"
                stale = await client.patch(
                    url, json=FIXTURE["author_patch"]["body"] | {"observed_revision": created.json()["revision"]}
                )
                assert stale.status_code == 409
                invalid = await client.patch(
                    url,
                    json={
                        "observed_revision": patch.json()["revision"],
                        "operations": [{"action": "delete", "path": "SKILL.md"}],
                    },
                )
                assert invalid.status_code == 422
                assert (await client.get(url + "/templates/one.txt")).json()["content"] == "replaced"
                replaced = await client.put(
                    url,
                    json={
                        "observed_revision": patch.json()["revision"],
                        "skill_md_content": FIXTURE["snapshot"]["skill_md_content"],
                        "extra_files": [],
                    },
                )
                assert replaced.status_code == 200, replaced.text
                assert [entry["path"] for entry in replaced.json()["files"]] == ["SKILL.md"]
                assert (await client.get(url + "/templates/one.txt")).status_code == 404
                assert (await client.get(url.removesuffix("/files") + "/manifest")).json() == replaced.json()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("extras", [[], [{"path": "bin/data.bin", "content": "AP8=", "encoding": "base64"}]])
async def test_full_replacement_converts_git_draft_to_direct_without_clone(extras):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(db, owner, status=ListingStatus.draft)
            version = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing.id))).scalar_one()
            version.delivery_mode = "git_fetch"
            version.git_url = "https://example.test/source.git"
            version.git_ref = "main"
            version.skill_md_content = None
            await db.flush()
            revision = skill_content_revision(listing, version)
            version.content_revision = revision
            await db.commit()
            result = await skill_files.replace_skill_files(
                str(listing.id),
                version.id,
                skill_files.SkillSnapshotReplace(
                    observed_revision=revision,
                    skill_md_content=FIXTURE["snapshot"]["skill_md_content"],
                    extra_files=extras,
                ),
                Response(),
                db,
                owner,
            )
            assert len(result.files) == 1 + len(extras)
            assert version.delivery_mode == "registry_direct"
            assert version.git_url is None and version.git_ref is None
            fresh = await skill_files.get_skill_manifest(str(listing.id), version.id, Response(), db, owner)
            assert fresh.revision == result.revision
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_version_scoped_file_save_refuses_another_authors_active_lock():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(db, owner, status=ListingStatus.draft)
            version = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing.id))).scalar_one()
            version.is_editing = True
            version.editing_by = uuid.uuid4()
            version.editing_since = datetime.now(UTC)
            await db.flush()
            revision = skill_content_revision(listing, version)
            await db.commit()
            with pytest.raises(HTTPException) as blocked:
                await skill_files.patch_skill_files(
                    str(listing.id),
                    version.id,
                    SkillFileOperations.model_validate(
                        {
                            "observed_revision": revision,
                            "operations": [{"action": "put", "file": {"path": "a.txt", "content": "new"}}],
                        }
                    ),
                    Response(),
                    db,
                    owner,
                )
            assert blocked.value.status_code == 409
            assert version.extra_files == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_patch_migrates_touched_legacy_script_and_refuses_pending_edit():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(db, owner, status=ListingStatus.draft)
            version = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing.id))).scalar_one()
            version.script_filename = "old.sh"
            version.script_content = "echo old\n"
            await db.flush()
            revision = skill_content_revision(listing, version)
            version.content_revision = revision
            await db.commit()
            updated = await skill_files.patch_skill_files(
                str(listing.id),
                version.id,
                SkillFileOperations.model_validate(
                    {
                        "observed_revision": revision,
                        "operations": [{"action": "rename", "path": "scripts/old.sh", "new_path": "scripts/new.sh"}],
                    }
                ),
                Response(),
                db,
                owner,
            )
            assert [file.path for file in updated.files] == ["SKILL.md", "scripts/new.sh"]
            assert updated.files[-1].mode == "0755"
            assert version.script_filename is None and version.script_content is None
            assert version.extra_files[0]["path"] == "scripts/new.sh"
            assert version.content_revision == updated.revision
            version.status = ListingStatus.pending
            await db.commit()
            with pytest.raises(HTTPException) as refused:
                await skill_files.patch_skill_files(
                    str(listing.id),
                    version.id,
                    SkillFileOperations.model_validate(
                        {
                            "observed_revision": updated.revision,
                            "operations": [{"action": "put", "file": {"path": "a.txt", "content": "new"}}],
                        }
                    ),
                    Response(),
                    db,
                    owner,
                )
            assert refused.value.status_code == 409
    finally:
        await engine.dispose()
