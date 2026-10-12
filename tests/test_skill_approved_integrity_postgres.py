# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Real PostgreSQL must also refuse approved folders without their reviewed bytes."""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.routes import skill_files
from models.base import Base
from models.component_bundle import ComponentBundle
from models.skill import SkillVersion
from schemas.skill import SkillCandidateDraftRequest
from services.agent_lock import content_digest, load_pinned_listings, pinned_component_blockers
from services.skill_revisions import skill_content_revision
from tests import discovery_support as ds


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["missing_revision", "changed_bytes"])
async def test_approved_folder_cannot_be_reviewed_or_pinned_after_corruption(corruption):
    url = os.environ.get("OBSERVAL_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set OBSERVAL_TEST_POSTGRES_URL to an isolated PostgreSQL test database")
    schema = f"skill_integrity_{uuid.uuid4().hex}"
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": f"{schema},public"}})
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(text(f'CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA "{schema}"'))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=[*ds.TABLES, ComponentBundle.__table__])
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            owner = await ds.user(db)
            listing = await ds.skill(
                db,
                owner,
                content="---\nname: security-review\ndescription: Review security issues\n---\n# Security Review\n",
            )
            version = await db.get(SkillVersion, listing.latest_version_id)
            version.extra_files = [{"path": "scripts/run.sh", "content": "echo reviewed", "executable": True}]
            version.content_revision = skill_content_revision(listing, version)
            listing_id, version_id, owner_id = listing.id, version.id, owner.id
            await db.commit()
        async with sessions() as db:
            listing = await db.get(type(listing), listing_id)
            version = await db.get(SkillVersion, version_id)
            if corruption == "missing_revision":
                version.content_revision = None
            else:
                version.extra_files = [{"path": "scripts/run.sh", "content": "echo replaced", "executable": True}]
            await db.commit()
        async with sessions() as db:
            owner = await db.get(type(owner), owner_id)
            listing = await db.get(type(listing), listing_id)
            version = await db.get(SkillVersion, version_id)
            with pytest.raises(HTTPException) as manifest:
                await skill_files.get_skill_manifest(str(listing_id), version_id, Response(), db, owner)
            assert manifest.value.status_code == 409
            with pytest.raises(HTTPException) as fork:
                await skill_files.create_skill_candidate_draft(
                    str(listing_id),
                    SkillCandidateDraftRequest(
                        base_version_id=version_id,
                        observed_base_revision=skill_content_revision(listing, version),
                        version="2.0.0",
                        description="Would inherit invalid base",
                    ),
                    Response(),
                    db,
                    owner,
                )
            assert fork.value.status_code == 409
            component = SimpleNamespace(
                component_type="skill",
                component_id=listing_id,
                component_name=listing.name,
                resolved_version_id=version_id,
                resolved_version=version.version,
                resolved_digest=content_digest("skill", version),
            )
            assert (await pinned_component_blockers(db, [component]))[0]["status"] == "invalid_review_revision"
            with pytest.raises(HTTPException) as install:
                await load_pinned_listings(db, [component], {"skill": {listing_id: listing}})
            assert install.value.status_code == 409
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()
