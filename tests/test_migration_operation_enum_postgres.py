# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Historical import jobs must survive enum-label normalization at head."""

from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from models.migration_job import MigrationJob, MigrationOperation

_MIGRATION = (
    Path(__file__).resolve().parent.parent / "observal-server/alembic/versions/034_align_migration_operation_enum.py"
)


@pytest.mark.asyncio
@pytest.mark.parametrize("old_label", ["import", "import_"])
async def test_import_job_enum_works_after_upgrading_historical_or_fresh_schema(old_label):
    url = os.environ.get("OBSERVAL_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set OBSERVAL_TEST_POSTGRES_URL to an isolated PostgreSQL test database")
    schema = f"migration_operation_{uuid.uuid4().hex}"
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": f"{schema},public"}})
    old_id, new_id = uuid.uuid4(), uuid.uuid4()
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as conn:
            await conn.execute(text(f"CREATE TYPE migration_operation AS ENUM ('export', '{old_label}', 'validate')"))
            await conn.execute(
                text("CREATE TABLE migration_jobs (id uuid PRIMARY KEY, operation_type migration_operation NOT NULL)")
            )
            await conn.execute(
                text("INSERT INTO migration_jobs (id, operation_type) VALUES (:id, :operation)"),
                {"id": old_id, "operation": old_label},
            )
            spec = importlib.util.spec_from_file_location("align_migration_operation_enum", _MIGRATION)
            revision = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(revision)

            def upgrade(sync_conn):
                revision.op = Operations(MigrationContext.configure(sync_conn))
                revision.upgrade()
                # Also check a repeated invocation: already-normalized schemas
                # must not change enum identity or destroy historical rows.
                revision.upgrade()

            await conn.run_sync(upgrade)
            processor = MigrationJob.__table__.c.operation_type.type.bind_processor(engine.dialect)
            assert processor(MigrationOperation.import_) == "import_"
            await conn.execute(
                text("INSERT INTO migration_jobs (id, operation_type) VALUES (:id, :operation)"),
                {"id": new_id, "operation": processor(MigrationOperation.import_)},
            )
            found = (
                (await conn.execute(text("SELECT operation_type::text FROM migration_jobs ORDER BY id")))
                .scalars()
                .all()
            )
            assert found == ["import_", "import_"]
            labels = (
                (
                    await conn.execute(
                        text(
                            "SELECT e.enumlabel FROM pg_enum e "
                            "JOIN pg_type t ON e.enumtypid=t.oid "
                            "JOIN pg_namespace n ON n.oid=t.typnamespace "
                            "WHERE t.typname='migration_operation' AND n.nspname=current_schema() ORDER BY e.enumsortorder"
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert labels == ["export", "import_", "validate"]
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()
