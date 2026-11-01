# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Historical nullable read-model fields remain safe to search and serialize."""

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

from services.discovery.search import search_entries
from services.discovery.serialize import entry_document, search_result_item

_REVISION = Path(__file__).resolve().parents[1] / "observal-server/alembic/versions/027_discovery_entries.py"


@pytest.mark.asyncio
async def test_historical_nullable_discovery_row_remains_readable():
    url = os.environ.get("OBSERVAL_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set OBSERVAL_TEST_POSTGRES_URL to an isolated PostgreSQL test database")
    schema = f"nullable_discovery_{uuid.uuid4().hex}"
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": f"{schema},public"}})
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as conn:
            spec = importlib.util.spec_from_file_location("historical_discovery", _REVISION)
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)

            def create_historical_table(sync_conn):
                migration.op = Operations(MigrationContext.configure(sync_conn))
                migration.upgrade()

            await conn.run_sync(create_historical_table)
            await conn.execute(
                text(
                    "INSERT INTO discovery_entries (id, ard_identifier, kind, media_type, display_name, "
                    "version, artifact_url, source_kind, publisher_domain, visibility, lifecycle_status, "
                    "search_document) VALUES (:id, :identifier, 'skill', 'text/markdown', 'Legacy Skill', "
                    "'1.0.0', 'https://example.test/skill', 'local', 'example.test', 'public', "
                    "'approved', 'legacy skill')"
                ),
                {"id": uuid.uuid4(), "identifier": "urn:observal:skill:legacy"},
            )
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            page = await search_entries(db, text="legacy skill", public_search_enabled=True)
            assert page.total == 1
            ranked = page.results[0]
            entry = ranked.entry
            assert entry.capabilities is None
            assert entry.raw_entry is None
            assert entry.last_seen_at is None
            assert search_result_item(ranked, source="local")["capabilities"] == []
            assert entry_document(entry) == {}
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()
