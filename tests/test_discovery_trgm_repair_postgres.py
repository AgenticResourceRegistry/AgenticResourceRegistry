# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""A restored pre-feature database may lack the discovery trigram index."""

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

_REVISION = Path(__file__).resolve().parents[1] / "observal-server/alembic/versions/036_restore_discovery_trgm_index.py"


@pytest.mark.asyncio
@pytest.mark.parametrize("index_state", ["absent", "valid", "wrong"])
async def test_discovery_index_is_added_or_validated_without_dropping_existing_data(index_state):
    url = os.environ.get("OBSERVAL_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set OBSERVAL_TEST_POSTGRES_URL to an isolated PostgreSQL test database")
    schema = f"historical_trgm_{uuid.uuid4().hex}"
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": f"{schema},public"}})
    try:
        async with admin.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as conn:
            await conn.execute(
                text("CREATE TABLE discovery_entries (id uuid PRIMARY KEY, search_document text NOT NULL)")
            )
            await conn.execute(
                text("INSERT INTO discovery_entries (id, search_document) VALUES (:id, 'historical text')"),
                {"id": uuid.uuid4()},
            )
            if index_state == "valid":
                await conn.execute(
                    text(
                        "CREATE INDEX ix_discovery_entries_search_trgm ON discovery_entries "
                        "USING gin (search_document gin_trgm_ops)"
                    )
                )
            elif index_state == "wrong":
                await conn.execute(
                    text("CREATE INDEX ix_discovery_entries_search_trgm ON discovery_entries (search_document)")
                )
            spec = importlib.util.spec_from_file_location("restore_discovery_trgm", _REVISION)
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)

            def upgrade(sync_conn):
                migration.op = Operations(MigrationContext.configure(sync_conn))
                migration.upgrade()
                migration.upgrade()

            if index_state == "wrong":
                with pytest.raises(RuntimeError, match="incompatible"):
                    await conn.run_sync(upgrade)
            else:
                await conn.run_sync(upgrade)
                index = await conn.scalar(
                    text(
                        "SELECT pg_get_indexdef(c.oid) FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid=c.relnamespace "
                        "WHERE n.nspname=current_schema() AND c.relname='ix_discovery_entries_search_trgm'"
                    )
                )
                assert "USING gin" in index and "gin_trgm_ops" in index
            assert await conn.scalar(text("SELECT search_document FROM discovery_entries")) == "historical text"
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()
