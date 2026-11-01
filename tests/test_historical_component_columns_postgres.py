# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""A v1-upgraded DB can read hook/sandbox columns present on fresh installs."""

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

_MIGRATION = (
    Path(__file__).resolve().parent.parent / "observal-server/alembic/versions/035_restore_component_columns.py"
)


@pytest.mark.asyncio
async def test_historical_hook_and_sandbox_tables_gain_required_nullable_columns():
    url = os.environ.get("OBSERVAL_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set OBSERVAL_TEST_POSTGRES_URL to an isolated PostgreSQL test database")
    schema = f"historical_columns_{uuid.uuid4().hex}"
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": f"{schema},public"}})
    expected = {
        "hook_versions": {
            "source_path": "character varying(500)",
            "resolved_sha": "character varying(40)",
            "script_content": "text",
            "script_filename": "character varying(255)",
            "requirements": "json",
            "source_url": "character varying(500)",
            "source_ref": "character varying(255)",
        },
        "sandbox_versions": {"sandbox_path": "character varying(500)", "validated_at": "timestamp with time zone"},
    }
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as conn:
            for table in expected:
                await conn.execute(text(f"CREATE TABLE {table} (id uuid PRIMARY KEY)"))
                await conn.execute(text(f"INSERT INTO {table} (id) VALUES (:id)"), {"id": uuid.uuid4()})
            # Simulate columns that were created correctly by a partial earlier
            # operator patch: the additive migration must not overwrite them.
            await conn.execute(text("ALTER TABLE hook_versions ADD COLUMN source_url varchar(500)"))
            await conn.execute(text("UPDATE hook_versions SET source_url='https://example.test/source'"))
            spec = importlib.util.spec_from_file_location("restore_component_columns", _MIGRATION)
            revision = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(revision)

            def apply(sync_conn):
                revision.op = Operations(MigrationContext.configure(sync_conn))
                revision.upgrade()
                revision.upgrade()

            await conn.run_sync(apply)
            for table, columns in expected.items():
                actual = dict(
                    (
                        await conn.execute(
                            text(
                                "SELECT column_name, data_type || CASE WHEN character_maximum_length IS NOT NULL "
                                "THEN '(' || character_maximum_length || ')' ELSE '' END "
                                "FROM information_schema.columns WHERE table_schema=current_schema() "
                                "AND table_name=:table"
                            ),
                            {"table": table},
                        )
                    ).all()
                )
                for name, datatype in columns.items():
                    assert actual[name] == datatype
            assert await conn.scalar(text("SELECT source_url FROM hook_versions")) == "https://example.test/source"
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()
