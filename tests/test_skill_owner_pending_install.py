# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Legacy owners may explicitly install older pending resource-less skill rows."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from api.routes import skill
from models.mcp import ListingStatus
from models.skill import SkillDownload, SkillVersion
from schemas.skill import SkillInstallRequest
from tests import discovery_support as ds


@pytest.mark.asyncio
async def test_explicit_older_pending_release_is_owner_only(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    async with engine.begin() as conn:
        await conn.run_sync(SkillDownload.__table__.create)
    try:
        async with maker() as db:
            author = await ds.user(db)
            reader = await ds.user(db)
            listing = await ds.skill(db, author, status=ListingStatus.pending, version="1.0.0", content=None)
            old_id = listing.latest_version_id
            await ds.add_skill_version(db, listing, author, version="2.0.0", status=ListingStatus.pending, content=None)
            await db.commit()
            listing_id, author_id, reader_id = listing.id, author.id, reader.id
        monkeypatch.setattr("api.routes.config.derive_endpoints", AsyncMock(return_value={"api": "https://api.test"}))
        monkeypatch.setattr(
            "services.skill_config_generator.generate_skill_config", MagicMock(return_value={"skill": {}})
        )
        async with maker() as db:
            author = await db.get(type(author), author_id)
            installed = await skill.install_skill(
                str(listing_id), SkillInstallRequest(harness="pi", version="1.0.0"), MagicMock(), db, author
            )
            assert installed.version_id == old_id
            old = (await db.execute(select(SkillVersion).where(SkillVersion.id == old_id))).scalar_one()
            assert old.download_count == 1
        async with maker() as db:
            reader = await db.get(type(reader), reader_id)
            with pytest.raises(HTTPException) as blocked:
                await skill.install_skill(
                    str(listing_id), SkillInstallRequest(harness="pi", version="1.0.0"), MagicMock(), db, reader
                )
            assert blocked.value.status_code == 404
    finally:
        await engine.dispose()
