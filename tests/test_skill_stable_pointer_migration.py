# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Historical pointer correction chooses only cleared stable skill releases."""

import importlib.util
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models.base import Base
from models.component_bundle import ComponentBundle
from models.mcp import ListingStatus
from tests import discovery_support as ds

PATH = Path(__file__).resolve().parents[1] / "observal-server/alembic/versions/032_skill_stable_pointer_repair.py"


def _migration():
    spec = importlib.util.spec_from_file_location("stable_pointer_repair_test", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stable_pointer_repair_is_data_only_and_follows_review_epoch():
    migration = _migration()
    migration.op = Mock()
    assert migration.down_revision == "031_skill_review_epoch"
    migration.upgrade()
    sql = migration.op.execute.call_args.args[0]
    assert "AND NOT v.requires_global_review" in sql
    assert "stable.release_rank = 1" in sql
    assert "(v.status = 'approved') DESC" in sql
    assert "current.status = 'archived'" in sql
    assert "IS DISTINCT FROM stable.id" in sql
    migration.downgrade()
    migration.op.drop_column.assert_not_called()


@pytest.mark.asyncio
async def test_real_postgres_repairs_only_highest_cleared_stable_release():
    url = os.environ.get("OBSERVAL_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set OBSERVAL_TEST_POSTGRES_URL to an isolated PostgreSQL test database")
    schema = f"stable_pointer_{uuid.uuid4().hex}"
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
            author = await ds.user(db)
            first = await ds.skill(db, author, version="1.0.0")
            first_id = first.id
            newer = await ds.add_skill_version(db, first, author, version="2.0.0", status=ListingStatus.approved)
            newer.released_at = datetime.now(UTC) - timedelta(days=10)
            archived = await ds.add_skill_version(db, first, author, version="3.0.0", status=ListingStatus.archived)
            archived.released_at = datetime.now(UTC) - timedelta(
                days=1
            )  # Newer than approved: old 030 incorrectly hid it.
            marked = await ds.add_skill_version(db, first, author, version="4.0.0", status=ListingStatus.approved)
            marked.requires_global_review = True
            candidate = await ds.add_skill_version(db, first, author, version="5.0.0", status=ListingStatus.pending)
            first.latest_version_id = candidate.id

            only_prerelease = await ds.skill(db, author, name="Preview Only", version="2.0.0-rc.1")
            only_prerelease_id, original_preview_id = only_prerelease.id, only_prerelease.latest_version_id
            archived_only = await ds.skill(
                db, author, name="Archived Stable", version="1.0.0", status=ListingStatus.archived
            )
            archived_stable_id = archived_only.latest_version_id
            await ds.add_skill_version(db, archived_only, author, version="2.0.0-rc.1", status=ListingStatus.approved)
            await ds.add_skill_version(db, archived_only, author, version="3.0.0", status=ListingStatus.pending)
            archived_listing_id = archived_only.id
            preview_pending = await ds.skill(db, author, name="Preview Pending", version="2.0.0-rc.1")
            only_pending = await ds.add_skill_version(
                db, preview_pending, author, version="3.0.0", status=ListingStatus.pending
            )
            only_pending_listing_id = preview_pending.id
            await db.commit()
            archived_id, approved_newest_id = archived.id, newer.id
        migration = _migration()
        lifecycle_path = PATH.with_name("030_skill_release_lifecycle.py")
        lifecycle_spec = importlib.util.spec_from_file_location("skill_lifecycle_sql_test", lifecycle_path)
        lifecycle = importlib.util.module_from_spec(lifecycle_spec)
        lifecycle_spec.loader.exec_module(lifecycle)
        lifecycle.op = Mock()
        lifecycle.upgrade()
        lifecycle_repair_sql = lifecycle.op.execute.call_args.args[0]

        def run_repair(sync_conn):
            migration.op = Operations(MigrationContext.configure(sync_conn))
            migration.upgrade()

        async with engine.begin() as conn:
            # The fresh test schema already has 030's columns; run its actual
            # data repair statement followed by 032, as historical upgrades do.
            await conn.execute(text(lifecycle_repair_sql))
            after_030 = await conn.scalar(
                text("SELECT latest_version_id FROM skill_listings WHERE id = CAST(:id AS uuid)"), {"id": str(first_id)}
            )
            assert after_030 == approved_newest_id
            assert (
                await conn.scalar(
                    text("SELECT latest_version_id FROM skill_listings WHERE id = CAST(:id AS uuid)"),
                    {"id": str(archived_listing_id)},
                )
                == archived_stable_id
            )  # A newer approved prerelease stays pin-only.
            assert (
                await conn.scalar(
                    text("SELECT latest_version_id FROM skill_listings WHERE id = CAST(:id AS uuid)"),
                    {"id": str(only_pending_listing_id)},
                )
                == only_pending.id
            )  # No stable release exists; retain stale pointer.
            await conn.run_sync(run_repair)
            assert (
                await conn.scalar(
                    text("SELECT latest_version_id FROM skill_listings WHERE id = CAST(:id AS uuid)"),
                    {"id": str(archived_listing_id)},
                )
                == archived_stable_id
            )
            repaired = await conn.scalar(
                text("SELECT latest_version_id FROM skill_listings WHERE id = CAST(:id AS uuid)"), {"id": str(first_id)}
            )
            assert repaired == approved_newest_id  # Archived 3.0 must not hide approved 2.0 from discovery.
            untouched = await conn.scalar(
                text("SELECT latest_version_id FROM skill_listings WHERE id = CAST(:id AS uuid)"),
                {"id": str(only_prerelease_id)},
            )
            assert untouched == original_preview_id
            await conn.run_sync(run_repair)
            assert (
                await conn.scalar(
                    text("SELECT latest_version_id FROM skill_listings WHERE id = CAST(:id AS uuid)"),
                    {"id": str(first_id)},
                )
                == approved_newest_id
            )
            await conn.execute(
                text(
                    "UPDATE skill_listings SET latest_version_id = CAST(:version AS uuid) WHERE id = CAST(:id AS uuid)"
                ),
                {"id": str(first_id), "version": str(archived_id)},
            )
            await conn.run_sync(run_repair)
            assert (
                await conn.scalar(
                    text("SELECT latest_version_id FROM skill_listings WHERE id = CAST(:id AS uuid)"),
                    {"id": str(first_id)},
                )
                == archived_id
            )  # Do not silently unarchive an intentionally archived listing.
    finally:
        await engine.dispose()
        async with admin_engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
