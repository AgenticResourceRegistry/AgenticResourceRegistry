# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Standalone installation chooses the persisted release before applying visibility/status gates."""

import base64
import hashlib
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from api.routes import skill
from models.mcp import ListingStatus
from models.skill import SkillDownload, SkillVersion
from schemas.skill import SkillInstallRequest
from services.skill_revisions import skill_content_revision
from tests import discovery_support as ds


@pytest.mark.asyncio
async def test_opted_in_standalone_bundle_declares_all_files_and_denies_old_client(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    async with engine.begin() as conn:
        await conn.run_sync(SkillDownload.__table__.create)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(db, owner)
            version = await db.get(SkillVersion, listing.latest_version_id)
            version.extra_files = [
                {"path": "scripts/run.sh", "content": "echo hi", "executable": True},
                {"path": "scripts/empty.sh", "content": "", "executable": True},
                {"path": "assets/image.bin", "content": "AP8=", "encoding": "base64"},
            ]
            version.content_revision = skill_content_revision(listing, version)
            await db.commit()
            listing_id, version_id, owner_id = listing.id, version.id, owner.id
        monkeypatch.setattr("api.routes.config.derive_endpoints", AsyncMock(return_value={"api": "https://api.test"}))
        async with maker() as db:
            owner = await db.get(type(owner), owner_id)
            with pytest.raises(HTTPException) as missing:
                await skill.install_skill(str(listing_id), SkillInstallRequest(harness="pi"), MagicMock(), db, owner)
            assert missing.value.status_code == 409
            assert "skill_extra_files_v1" in missing.value.detail
            with pytest.raises(HTTPException) as unsupported:
                await skill.install_skill(
                    str(listing_id),
                    SkillInstallRequest(harness="kiro", supported_features=["skill_extra_files_v1"]),
                    MagicMock(),
                    db,
                    owner,
                )
            assert unsupported.value.status_code == 409
            with pytest.raises(HTTPException, match="disabled until fleet rollout"):
                await skill.install_skill(
                    str(listing_id),
                    SkillInstallRequest(harness="pi", supported_features=["skill_extra_files_v1"]),
                    MagicMock(),
                    db,
                    owner,
                )
            version = await db.get(SkillVersion, version_id)
            assert version.download_count == 0
            monkeypatch.setattr("services.dynamic_settings.get_bool", AsyncMock(return_value=True))
            with pytest.raises(HTTPException) as wrong_scope:
                await skill.install_skill(
                    str(listing_id),
                    SkillInstallRequest(harness="pi", scope="unknown", supported_features=["skill_extra_files_v1"]),
                    MagicMock(),
                    db,
                    owner,
                )
            assert wrong_scope.value.status_code == 409
            assert wrong_scope.value.detail == "Harness does not support complete skill folders in this scope"
            assert (await db.get(SkillVersion, version_id)).download_count == 0
            preview = await skill.install_skill(
                str(listing_id),
                SkillInstallRequest(harness="pi", supported_features=["skill_extra_files_v1"], preview=True),
                MagicMock(),
                db,
                owner,
            )
            assert preview.bundle is not None and preview.bundle.version_id == version_id
            assert (await db.get(SkillVersion, version_id)).download_count == 0
            assert (await db.execute(select(SkillDownload))).scalars().all() == []
            response = await skill.install_skill(
                str(listing_id),
                SkillInstallRequest(harness="pi", supported_features=["skill_extra_files_v1"]),
                MagicMock(),
                db,
                owner,
            )
            assert response.bundle is not None and response.bundle.version_id == version_id
            assert response.digest == response.bundle.digest
            assert response.bundle.skill_file_path.endswith("/SKILL.md")
            assert response.digest.startswith("observal-content-v2:")
            files = {item.path: item for item in response.bundle.files}
            assert set(files) == {"SKILL.md", "scripts/run.sh", "scripts/empty.sh", "assets/image.bin"}
            for item in files.values():
                content = base64.b64decode(item.content, validate=True)
                assert len(content) == item.size
                assert hashlib.sha256(content).hexdigest() == item.sha256
                assert item.version_id == version_id
            assert files["scripts/run.sh"].mode == "0755"
            assert files["scripts/empty.sh"].size == 0
            assert base64.b64decode(files["assets/image.bin"].content) == b"\x00\xff"
            assert "skills" not in response.config_snippet
            assert "skill_md_content" not in response.config_snippet["skill"]
            assert (await db.get(SkillVersion, version_id)).download_count == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("declared_name", ["portable-name", "café"])
async def test_complete_standalone_folder_uses_frontmatter_name_not_slug_or_alias(monkeypatch, declared_name):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    async with engine.begin() as conn:
        await conn.run_sync(SkillDownload.__table__.create)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(db, owner, slug="registry-alias")
            version = await db.get(SkillVersion, listing.latest_version_id)
            version.skill_md_content = f"---\nname: {declared_name}\ndescription: Test skill\n---\n# Body\n"
            version.extra_files = [{"path": "references/guide.md", "content": "Guide\n"}]
            version.content_revision = skill_content_revision(listing, version)
            await db.commit()
            listing_id, version_id, owner_id = listing.id, version.id, owner.id
        monkeypatch.setattr("api.routes.config.derive_endpoints", AsyncMock(return_value={"api": "https://api.test"}))
        monkeypatch.setattr("services.dynamic_settings.get_bool", AsyncMock(return_value=True))
        async with maker() as db:
            owner = await db.get(type(owner), owner_id)
            with pytest.raises(HTTPException) as alias:
                await skill.install_skill(
                    str(listing_id),
                    SkillInstallRequest(
                        harness="pi", local_name="local-alias", supported_features=["skill_extra_files_v1"]
                    ),
                    MagicMock(),
                    db,
                    owner,
                )
            assert alias.value.status_code == 409
            assert (await db.get(SkillVersion, version_id)).download_count == 0
            response = await skill.install_skill(
                str(listing_id),
                SkillInstallRequest(harness="pi", supported_features=["skill_extra_files_v1"]),
                MagicMock(),
                db,
                owner,
            )
            assert response.bundle.skill_file_path == f".pi/skills/{declared_name}/SKILL.md"
            assert response.config_snippet["skill"]["name"] == declared_name
            assert [file.path for file in response.bundle.files] == ["SKILL.md", "references/guide.md"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_opted_in_historical_resource_less_skill_keeps_its_local_alias(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    async with engine.begin() as conn:
        await conn.run_sync(SkillDownload.__table__.create)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(db, owner, slug="registry-alias")  # Old SKILL.md has no description.
            version = await db.get(SkillVersion, listing.latest_version_id)
            version.content_revision = skill_content_revision(listing, version)
            await db.commit()
            listing_id, owner_id = listing.id, owner.id
        monkeypatch.setattr("api.routes.config.derive_endpoints", AsyncMock(return_value={"api": "https://api.test"}))
        async with maker() as db:
            owner = await db.get(type(owner), owner_id)
            response = await skill.install_skill(
                str(listing_id),
                SkillInstallRequest(harness="pi", local_name="old-local", supported_features=["skill_extra_files_v1"]),
                MagicMock(),
                db,
                owner,
            )
            assert response.bundle.skill_file_path == ".pi/skills/old-local/SKILL.md"
            assert response.config_snippet["skill"]["name"] == "old-local"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sixty_file_server_response_is_complete_and_selected(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    async with engine.begin() as conn:
        await conn.run_sync(SkillDownload.__table__.create)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(db, owner)
            version = await db.get(SkillVersion, listing.latest_version_id)
            version.extra_files = [{"path": f"templates/item-{n:02d}.txt", "content": str(n)} for n in range(60)]
            version.content_revision = skill_content_revision(listing, version)
            await db.commit()
            listing_id, version_id, owner_id = listing.id, version.id, owner.id
        monkeypatch.setattr("services.dynamic_settings.get_bool", AsyncMock(return_value=True))
        with patch("api.routes.config.derive_endpoints", AsyncMock(return_value={"api": "https://api.test"})):
            async with maker() as db:
                owner = await db.get(type(owner), owner_id)
                response = await skill.install_skill(
                    str(listing_id),
                    SkillInstallRequest(harness="pi", supported_features=["skill_extra_files_v1"]),
                    MagicMock(),
                    db,
                    owner,
                )
                assert response.bundle.version_id == version_id
                assert len(response.bundle.files) == 61
                assert response.bundle.files[-1].path == "templates/item-59.txt"
                assert base64.b64decode(response.bundle.files[-1].content) == b"59"
    finally:
        await engine.dispose()


def test_near_cap_complete_folder_manifest_preserves_bytes_and_modes():
    import time
    from types import SimpleNamespace

    from services.skill_bundle import complete_skill_folder

    skill_md = "---\nname: near-cap\ndescription: Near cap\n---\n# Data\n" + "x" * (2 * 1024 * 1024 - 1024)
    binary = b"\x00\xff" * (1024 * 1024 - 1024)
    row = SimpleNamespace(
        id=uuid.uuid4(),
        version="1.0.0",
        description="Near cap",
        delivery_mode="registry_direct",
        skill_md_content=skill_md,
        script_filename=None,
        script_content=None,
        extra_files=[
            {
                "path": "assets/data.bin",
                "encoding": "base64",
                "content": base64.b64encode(binary).decode(),
                "executable": True,
            }
        ],
    )
    started = time.perf_counter()
    folder = complete_skill_folder(uuid.uuid4(), row, skill_file_path=".pi/skills/near-cap/SKILL.md")
    elapsed = time.perf_counter() - started
    assert len(folder.files) == 2
    for file, expected in zip(folder.files, (skill_md.encode(), binary), strict=True):
        assert base64.b64decode(file.content) == expected
        assert file.sha256 == hashlib.sha256(expected).hexdigest()
    assert folder.files[1].mode == "0755"
    print(f"near-cap folder encoding: {elapsed:.3f}s for {sum(len(f.content) for f in folder.files)} base64 bytes")


@pytest.mark.asyncio
async def test_public_user_can_select_old_approved_release_behind_pending_pointer(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    async with engine.begin() as conn:
        await conn.run_sync(SkillDownload.__table__.create)
    try:
        async with maker() as db:
            author = await ds.user(db)
            reader = await ds.user(db)
            listing = await ds.skill(db, author, content=None)
            approved_id = listing.latest_version_id
            candidate = await ds.add_skill_version(
                db,
                listing,
                author,
                version="2.0.0",
                status=ListingStatus.pending,
                content=None,
                description="Unreviewed private candidate notes",
            )
            await db.commit()
            listing_id, reader_id = listing.id, reader.id
            assert candidate.id != approved_id

        monkeypatch.setattr("api.routes.config.derive_endpoints", AsyncMock(return_value={"api": "https://api.test"}))
        selected_config = MagicMock(return_value={"skill": {"version": "1.2.0"}})
        monkeypatch.setattr("services.skill_config_generator.generate_skill_config", selected_config)
        async with maker() as db:
            reader = await db.get(type(reader), reader_id)
            detail = await skill.get_skill(str(listing_id), db, reader)
            assert detail.version == "1.2.0"
            assert detail.status == ListingStatus.approved
            assert "Unreviewed" not in detail.description
            response = await skill.install_skill(
                str(listing_id), SkillInstallRequest(harness="pi", version="1.2.0"), MagicMock(), db, reader
            )
            assert response.version_id == approved_id
            assert response.version == "1.2.0"
            assert selected_config.call_args.kwargs["version_override"].id == approved_id
            approved = (await db.execute(select(SkillVersion).where(SkillVersion.id == approved_id))).scalar_one()
            pending = (await db.execute(select(SkillVersion).where(SkillVersion.id == candidate.id))).scalar_one()
            assert approved.download_count == 1
            assert pending.download_count == 0

        async with maker() as db:
            reader = await db.get(type(reader), reader_id)
            default = await skill.install_skill(
                str(listing_id), SkillInstallRequest(harness="pi"), MagicMock(), db, reader
            )
            assert default.version_id == approved_id
            with pytest.raises(HTTPException) as refused:
                await skill.install_skill(
                    str(listing_id), SkillInstallRequest(harness="pi", version="2.0.0"), MagicMock(), db, reader
                )
            assert refused.value.status_code == 404
            approved = await db.get(SkillVersion, approved_id)
            pending = await db.get(SkillVersion, candidate.id)
            assert approved.download_count == 2
            assert pending.download_count == 0
    finally:
        await engine.dispose()
