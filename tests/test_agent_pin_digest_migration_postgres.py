# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""A real PostgreSQL migration accepts versioned folder pins without truncation."""

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

_MIGRATION = Path(__file__).resolve().parent.parent / "observal-server/alembic/versions/039_widen_agent_pin_digest.py"


@pytest.mark.asyncio
async def test_widened_agent_pin_digest_preserves_old_pins_and_refuses_destructive_downgrade():
    url = os.environ.get("OBSERVAL_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set OBSERVAL_TEST_POSTGRES_URL to an isolated PostgreSQL test database")
    schema = f"agent_pin_digest_{uuid.uuid4().hex}"
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": f"{schema},public"}})
    old_digest = "sha256:" + "a" * 64
    folder_digest = "observal-content-v2:sha256:" + "b" * 64
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE agent_components (id uuid PRIMARY KEY, resolved_digest varchar(80))"))
            await conn.execute(
                text("INSERT INTO agent_components (id, resolved_digest) VALUES (:id, :digest)"),
                {"id": uuid.uuid4(), "digest": old_digest},
            )
            spec = importlib.util.spec_from_file_location("widen_agent_pin_digest", _MIGRATION)
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)

            def apply(sync_conn):
                migration.op = Operations(MigrationContext.configure(sync_conn))
                migration.upgrade()

            await conn.run_sync(apply)
            assert (
                await conn.scalar(
                    text(
                        "SELECT character_maximum_length FROM information_schema.columns "
                        "WHERE table_schema=current_schema() AND table_name='agent_components' "
                        "AND column_name='resolved_digest'"
                    )
                )
                == 128
            )
            assert await conn.scalar(text("SELECT resolved_digest FROM agent_components")) == old_digest
            await conn.execute(
                text("INSERT INTO agent_components (id, resolved_digest) VALUES (:id, :digest)"),
                {"id": uuid.uuid4(), "digest": folder_digest},
            )
            assert (
                await conn.execute(text("SELECT resolved_digest FROM agent_components ORDER BY resolved_digest"))
            ).scalars().all() == [folder_digest, old_digest]

            def refuse_downgrade(sync_conn):
                migration.op = Operations(MigrationContext.configure(sync_conn))
                migration.downgrade()

            with pytest.raises(RuntimeError, match="Cannot narrow Agent pin digests"):
                await conn.run_sync(refuse_downgrade)
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()
