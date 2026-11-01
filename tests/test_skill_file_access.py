# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Exact version file reads authorize before selecting any content columns."""

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import event, select

from api.routes import component_versions, skill_files
from models.mcp import ListingStatus
from models.skill import SkillVersion
from models.team import TeamMembership, TeamRole
from models.user import UserRole
from services.skill_bundle import MAX_FILE_BYTES
from services.skill_revisions import skill_content_revision
from tests import discovery_support as ds


@pytest.mark.asyncio
async def test_historical_oversized_md_is_readable_but_not_a_new_upload():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(db, owner, status=ListingStatus.approved)
            version = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing.id))).scalar_one()
            version.skill_md_content = "---\nname: historical\ndescription: older skill\n---\n" + "x" * MAX_FILE_BYTES
            await db.commit()
            listing_id, version_id = listing.id, version.id

        async with maker() as db:
            manifest = await skill_files.get_skill_manifest(str(listing_id), version_id, Response(), db, None)
            assert manifest.files[0].path == "SKILL.md"
            assert manifest.files[0].size > MAX_FILE_BYTES
            file = await skill_files.get_skill_file(str(listing_id), version_id, "SKILL.md", db, None)
            assert len(file.body) > MAX_FILE_BYTES
            assert file.headers["Cache-Control"] == "no-store"
            version = await db.get(SkillVersion, version_id)
            version.skill_md_content += "x" * (2 * MAX_FILE_BYTES)
            await db.commit()

        async with maker() as db:
            with pytest.raises(HTTPException) as oversized:
                await component_versions._get_version(
                    str(listing_id), "1.2.0", type(listing), SkillVersion, "skill", db, None
                )
            assert oversized.value.status_code == 409
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_private_pending_skill_files_are_only_readable_by_owner_and_team_reviewer():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            outsider = await ds.user(db)
            team_reviewer = await ds.user(db)
            global_reviewer = await ds.user(db, role=UserRole.reviewer)
            team = await ds.team_with_member(db, owner)
            db.add(TeamMembership(team_id=team.id, user_id=team_reviewer.id, role=TeamRole.reviewer))
            listing = await ds.skill(db, owner, status=ListingStatus.pending, is_private=True, team_id=team.id)
            version = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing.id))).scalar_one()
            version.extra_files = [{"path": "private.txt", "content": "private bytes"}]
            version.content_revision = skill_content_revision(listing, version)
            await db.commit()
            listing_id, version_id = listing.id, version.id

        selected = []

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def capture(_conn, _cursor, statement, _params, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                selected.append(statement.lower())

        for actor in (outsider, global_reviewer):
            selected.clear()
            async with maker() as db:
                with pytest.raises(HTTPException) as exc:
                    await skill_files._authorized_files(str(listing_id), version_id, db, actor)
                assert exc.value.status_code == 404
            assert not any(
                field in sql for sql in selected for field in ("extra_files", "skill_md_content", "script_content")
            )

        for actor in (owner, team_reviewer):
            async with maker() as db:
                _, row, revision, files = await skill_files._authorized_files(str(listing_id), version_id, db, actor)
                assert row.id == version_id
                assert len(revision) == 64
                assert next(f.content for f in files if f.path == "private.txt") == b"private bytes"
                detail = await component_versions._get_version(
                    str(listing_id), row.version, type(listing), SkillVersion, "skill", db, actor
                )
                assert detail["id"] == str(version_id)
                assert detail["revision"] == revision

        async with maker() as db:
            listing = await db.get(type(listing), listing_id)
            version = await db.get(SkillVersion, version_id)
            listing.is_private = False
            version.requires_global_review = True
            version.pre_public_status = "approved"
            version.content_revision = skill_content_revision(listing, version)
            await db.commit()
        async with maker() as db:
            with pytest.raises(HTTPException) as exc:
                await skill_files._authorized_files(str(listing_id), version_id, db, team_reviewer)
            assert exc.value.status_code == 404
        async with maker() as db:
            _, _, _, files = await skill_files._authorized_files(str(listing_id), version_id, db, global_reviewer)
            assert any(f.path == "private.txt" for f in files)
    finally:
        await engine.dispose()
