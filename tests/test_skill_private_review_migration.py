# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Real PostgreSQL check of private review attribution upgrade and downgrade guard."""

from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models.base import Base
from models.component_bundle import ComponentBundle
from models.mcp import ListingStatus
from tests import discovery_support as ds


def _migration(conn, operation):
    path = (
        Path(__file__).resolve().parents[1] / "observal-server/alembic/versions/033_skill_private_review_provenance.py"
    )
    spec = importlib.util.spec_from_file_location("private_skill_review_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    migration.op = Operations(MigrationContext.configure(conn))
    getattr(migration, operation)()


@pytest.mark.asyncio
async def test_postgres_review_attribution_upgrade_and_refused_populated_downgrade():
    url = os.environ.get("OBSERVAL_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set OBSERVAL_TEST_POSTGRES_URL to an isolated PostgreSQL test database")
    schema = f"skill_review_provenance_{uuid.uuid4().hex}"
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
            listing = await ds.skill(db, owner, status=ListingStatus.approved)
            version_id, owner_id = listing.latest_version_id, owner.id
            await db.commit()
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE skill_versions DROP COLUMN pre_public_reviewed_by"))
            await conn.execute(text("ALTER TABLE skill_versions DROP COLUMN pre_public_reviewed_at"))
            await conn.run_sync(_migration, "upgrade")
            await conn.execute(
                text("UPDATE skill_versions SET pre_public_reviewed_by = :owner WHERE id = :id"),
                {"owner": owner_id, "id": version_id},
            )
            with pytest.raises(RuntimeError, match="private skill review attribution remains"):
                await conn.run_sync(_migration, "downgrade")
            await conn.execute(text("UPDATE skill_versions SET pre_public_reviewed_by = NULL"))
            await conn.run_sync(_migration, "downgrade")
            assert (
                await conn.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns WHERE table_schema = :schema "
                        "AND table_name = 'skill_versions' AND column_name = 'pre_public_reviewed_by'"
                    ),
                    {"schema": schema},
                )
                == 0
            )
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()
