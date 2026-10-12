# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Optional real PostgreSQL upgrade, historical pointer repair and downgrade test."""

import importlib.util
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models.base import Base
from models.component_bundle import ComponentBundle
from models.mcp import ListingStatus
from models.skill import SkillVersion
from tests import discovery_support as ds

pytestmark = pytest.mark.asyncio


def _run_migration(sync_conn, action):
    path = Path(__file__).resolve().parents[1] / "observal-server/alembic/versions/030_skill_release_lifecycle.py"
    spec = importlib.util.spec_from_file_location("skill_release_real_pg", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    migration.op = Operations(MigrationContext.configure(sync_conn))
    getattr(migration, action)()


async def test_real_postgres_repairs_pending_pointer_and_refuses_marked_downgrade():
    url = os.environ.get("OBSERVAL_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set OBSERVAL_TEST_POSTGRES_URL to an isolated PostgreSQL test database")
    schema = f"phase2_migration_{uuid.uuid4().hex}"
    admin_engine = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": f"{schema},public"}})
    try:
        async with admin_engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(text(f'CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA "{schema}"'))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=[*ds.TABLES, ComponentBundle.__table__])
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(db, owner, status=ListingStatus.approved)
            approved_id = listing.latest_version_id
            candidate = SkillVersion(
                listing_id=listing.id,
                version="1.3.0",
                description="unreviewed",
                status=ListingStatus.pending,
                task_type="general",
                released_by=owner.id,
                released_at=datetime.now(UTC),
            )
            db.add(candidate)
            await db.flush()
            listing.latest_version_id = candidate.id
            await db.commit()
            listing_id = listing.id

        async with engine.begin() as conn:
            for column in (
                "base_version_id",
                "base_revision",
                "content_revision",
                "requires_global_review",
                "pre_public_status",
            ):
                await conn.execute(text(f"ALTER TABLE skill_versions DROP COLUMN {column}"))
            await conn.run_sync(_run_migration, "upgrade")
            restored = await conn.scalar(
                text("SELECT latest_version_id FROM skill_listings WHERE id = CAST(:id AS uuid)"),
                {"id": str(listing_id)},
            )
            assert restored == approved_id
            await conn.execute(
                text("UPDATE skill_versions SET requires_global_review = true WHERE id = CAST(:id AS uuid)"),
                {"id": str(approved_id)},
            )
            with pytest.raises(RuntimeError, match="Cannot downgrade"):
                await conn.run_sync(_run_migration, "downgrade")
            await conn.execute(text("UPDATE skill_versions SET requires_global_review = false"))
            await conn.run_sync(_run_migration, "downgrade")
            remaining = await conn.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns WHERE table_schema = :schema "
                    "AND table_name = 'skill_versions' AND column_name = 'requires_global_review'"
                ),
                {"schema": schema},
            )
            assert remaining == 0
    finally:
        await engine.dispose()
        async with admin_engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
