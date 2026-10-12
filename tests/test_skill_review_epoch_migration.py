# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""A downgraded worker must never forget withdrawn review generations."""

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
from models.skill import SkillVersion
from tests import discovery_support as ds


def _migration():
    path = Path(__file__).resolve().parents[1] / "observal-server/alembic/versions/031_skill_review_epoch.py"
    spec = importlib.util.spec_from_file_location("skill_review_epoch_migration_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_migration(sync_conn, action):
    migration = _migration()
    migration.op = Operations(MigrationContext.configure(sync_conn))
    getattr(migration, action)()


def test_review_generation_migration_is_additive():
    migration = _migration()
    assert migration.down_revision == "030_skill_release_lifecycle"


@pytest.mark.asyncio
async def test_real_postgres_review_generation_survives_upgrade_and_blocks_downgrade():
    url = os.environ.get("OBSERVAL_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set OBSERVAL_TEST_POSTGRES_URL to an isolated PostgreSQL test database")
    schema = f"review_epoch_{uuid.uuid4().hex}"
    admin_engine = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": f"{schema},public"}})
    try:
        async with admin_engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(text(f'CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA "{schema}"'))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=[*ds.TABLES, ComponentBundle.__table__])
            await conn.execute(text("ALTER TABLE skill_versions DROP COLUMN review_epoch"))
            await conn.run_sync(_run_migration, "upgrade")
            default = await conn.scalar(
                text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = 'skill_versions' AND column_name = 'review_epoch'"
                ),
                {"schema": schema},
            )
            assert default == "0"
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(db, owner)
            version = await db.get(SkillVersion, listing.latest_version_id)
            version.review_epoch = 1
            await db.commit()
        async with engine.begin() as conn:
            with pytest.raises(RuntimeError, match="Cannot downgrade"):
                await conn.run_sync(_run_migration, "downgrade")
            stored = await conn.scalar(text("SELECT max(review_epoch) FROM skill_versions"))
            assert stored == 1
            await conn.execute(text("UPDATE skill_versions SET review_epoch = 0"))
            await conn.run_sync(_run_migration, "downgrade")
            remaining = await conn.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = 'skill_versions' AND column_name = 'review_epoch'"
                ),
                {"schema": schema},
            )
            assert remaining == 0
    finally:
        await engine.dispose()
        async with admin_engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
