# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""A reviewer sees selected UUIDs, never an arbitrary pending listing body."""

import pytest

from api.routes import component_versions
from models.mcp import ListingStatus
from models.skill import SkillListing, SkillVersion
from tests import discovery_support as ds

pytestmark = pytest.mark.asyncio


async def test_public_version_list_includes_archived_unmarked_but_hides_marked_rows():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(db, owner, status=ListingStatus.approved)
            archived = await ds.add_skill_version(
                db, listing, owner, version="1.1.0", status=ListingStatus.archived, set_latest=False
            )
            marked = await ds.add_skill_version(
                db, listing, owner, version="1.0.0", status=ListingStatus.archived, set_latest=False
            )
            marked.requires_global_review = True
            await db.commit()
            listing_id, archived_id = listing.id, archived.id
        async with maker() as db:
            result = await component_versions._list_versions(
                str(listing_id), 1, 20, SkillListing, SkillVersion, "skill", db, None
            )
            assert {item["id"] for item in result["items"]} == {str(listing.latest_version_id), str(archived_id)}
            assert result["total"] == 2
    finally:
        await engine.dispose()
