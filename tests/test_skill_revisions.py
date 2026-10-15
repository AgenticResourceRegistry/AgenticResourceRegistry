# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Review revisions hash decoded bytes/modes/metadata, not wire formatting or counts."""

import base64

import pytest
from sqlalchemy import select

from models.mcp import ListingStatus
from models.skill import SkillVersion
from services.skill_revisions import skill_content_revision
from tests import discovery_support as ds


@pytest.mark.asyncio
async def test_revision_changes_with_bytes_mode_and_identity_not_wire_encoding():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(db, owner, status=ListingStatus.draft)
            version = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing.id))).scalar_one()
            version.skill_md_content = "---\nname: example\ndescription: Example skill\n---\n"
            version.extra_files = [
                {"path": "z.txt", "content": "same", "encoding": "utf-8", "executable": False},
                {"path": "a.txt", "content": "other", "encoding": "utf-8", "executable": False},
            ]
            await db.flush()
            original_description = version.description
            before = skill_content_revision(listing, version)
            version.extra_files = list(reversed(version.extra_files))
            assert skill_content_revision(listing, version) == before
            version.extra_files = [
                {"path": "a.txt", "content": "other", "encoding": "utf-8", "executable": False},
                {
                    "path": "z.txt",
                    "content": base64.b64encode(b"same").decode(),
                    "encoding": "base64",
                    "executable": False,
                },
            ]
            assert skill_content_revision(listing, version) == before
            version.download_count = 42
            version.status = ListingStatus.pending
            assert skill_content_revision(listing, version) == before
            version.review_epoch = 0
            assert skill_content_revision(listing, version) == before
            version.review_epoch = 1
            assert skill_content_revision(listing, version) != before
            version.review_epoch = 0
            assert skill_content_revision(listing, version) == before
            version.extra_files = [
                version.extra_files[0],
                {**version.extra_files[1], "executable": True},
            ]
            assert skill_content_revision(listing, version) != before
            version.extra_files = [{**version.extra_files[0]}, {**version.extra_files[1], "executable": False}]
            assert skill_content_revision(listing, version) == before
            version.description = "new meaning"
            assert skill_content_revision(listing, version) != before
            version.description = original_description
            listing.name = "New identity"
            assert skill_content_revision(listing, version) != before
    finally:
        await engine.dispose()
