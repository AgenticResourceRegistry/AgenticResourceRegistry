# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Old versioned PostgreSQL schemas must not recreate existing enum types."""

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

_MIGRATIONS = Path(__file__).resolve().parent.parent / "observal-server/alembic/versions"


def _migration(filename):
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), _MIGRATIONS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_v1_existing_user_role_and_migration_job_enums_upgrade_without_duplication():
    url = os.environ.get("OBSERVAL_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set OBSERVAL_TEST_POSTGRES_URL to an isolated PostgreSQL test database")
    schema = f"historical_enums_{uuid.uuid4().hex}"
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": f"{schema},public"}})
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TYPE userrole AS ENUM ('super_admin', 'admin', 'reviewer', 'user')"))
            await conn.execute(text("CREATE TABLE users (id uuid PRIMARY KEY)"))

            def apply(sync_conn):
                op = Operations(MigrationContext.configure(sync_conn))
                invites = _migration("007_invites.py")
                invites.op = op
                invites.upgrade()
                assert sync_conn.execute(text("SELECT to_regclass('invites')")).scalar() is not None
                cleanup = _migration("008_remove_invites.py")
                cleanup.op = op
                cleanup.upgrade()
                jobs = _migration("014_migration_jobs.py")
                jobs.op = op
                jobs.upgrade()
                assert sync_conn.execute(text("SELECT to_regclass('migration_jobs')")).scalar() is not None
                enum_names = (
                    sync_conn.execute(
                        text(
                            "SELECT typname FROM pg_type WHERE typname IN "
                            "('userrole', 'migration_operation', 'migration_scope', 'migration_status')"
                        )
                    )
                    .scalars()
                    .all()
                )
                assert set(enum_names) == {"userrole", "migration_operation", "migration_scope", "migration_status"}

            await conn.run_sync(apply)
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()
