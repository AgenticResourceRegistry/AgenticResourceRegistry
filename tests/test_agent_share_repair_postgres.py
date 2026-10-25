# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The compatibility revision must not stamp incomplete existing share tables."""

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

_VERSIONS = Path(__file__).resolve().parents[1] / "observal-server/alembic/versions"


def _load(revision):
    spec = importlib.util.spec_from_file_location(revision, _VERSIONS / f"{revision}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _upgrade(sync_conn, migration):
    migration.op = Operations(MigrationContext.configure(sync_conn))
    migration.upgrade()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("damage", "expected"),
    [
        ("ALTER TABLE agent_share_manifests DROP COLUMN title", "missing agent_share_manifests.title"),
        ("ALTER TABLE agent_share_items DROP COLUMN position CASCADE", "missing agent_share_items.position"),
        ("ALTER TABLE agent_share_items DROP CONSTRAINT uq_agent_share_item_position", "unique"),
        ("ALTER TABLE agent_share_items DROP CONSTRAINT agent_share_items_agent_id_fkey", "foreign key"),
        ("DROP INDEX ix_agent_share_items_agent_id", "index"),
        ("DROP INDEX ix_agent_share_manifests_expires_at", "index"),
        (
            "ALTER TABLE agent_share_manifests ALTER COLUMN title TYPE varchar(121)",
            "incompatible agent_share_manifests.title",
        ),
        ("ALTER TABLE agent_share_manifests DROP CONSTRAINT agent_share_manifests_token_hash_key", "unique"),
    ],
)
async def test_existing_incomplete_pair_refuses_upgrade_without_losing_rows(damage, expected):
    url = os.environ.get("OBSERVAL_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set OBSERVAL_TEST_POSTGRES_URL to an isolated PostgreSQL test database")
    schema = f"share_repair_{uuid.uuid4().hex}"
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE users (id uuid PRIMARY KEY)"))
            await conn.execute(text("CREATE TABLE agents (id uuid PRIMARY KEY)"))
            await conn.execute(text("CREATE TABLE agent_versions (id uuid PRIMARY KEY)"))
            upstream = _load("030_agent_share_manifests")
            await conn.run_sync(lambda sync: _upgrade(sync, upstream))
            ids = {name: uuid.uuid4() for name in ("user", "agent", "version", "manifest", "item")}
            for table, key in (("users", "user"), ("agents", "agent"), ("agent_versions", "version")):
                await conn.execute(text(f"INSERT INTO {table} (id) VALUES (:id)"), {"id": ids[key]})
            await conn.execute(
                text(
                    "INSERT INTO agent_share_manifests "
                    "(id, token_hash, created_by, created_at, expires_at) "
                    "VALUES (:manifest, :hash, :user, now(), now() + interval '1 day')"
                ),
                {**ids, "hash": "a" * 64},
            )
            await conn.execute(
                text(
                    "INSERT INTO agent_share_items (id, manifest_id, agent_id, agent_version_id, position) "
                    "VALUES (:item, :manifest, :agent, :version, 0)"
                ),
                ids,
            )
            await conn.execute(text(damage))
            repair = _load("038_repair_agent_share_manifests")
            with pytest.raises(RuntimeError, match=expected):
                await conn.run_sync(lambda sync: _upgrade(sync, repair))
            assert await conn.scalar(text("SELECT token_hash FROM agent_share_manifests")) == "a" * 64
            assert await conn.scalar(text("SELECT id FROM agent_share_items")) == ids["item"]
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("already_upstream", [False, True])
async def test_repair_creates_missing_pair_or_preserves_complete_upstream_tables(already_upstream):
    url = os.environ.get("OBSERVAL_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set OBSERVAL_TEST_POSTGRES_URL to an isolated PostgreSQL test database")
    schema = f"share_repair_{uuid.uuid4().hex}"
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as conn:
            for table in ("users", "agents", "agent_versions"):
                await conn.execute(text(f"CREATE TABLE {table} (id uuid PRIMARY KEY)"))
            if already_upstream:
                await conn.run_sync(lambda sync: _upgrade(sync, _load("030_agent_share_manifests")))
                user = uuid.uuid4()
                manifest = uuid.uuid4()
                await conn.execute(text("INSERT INTO users (id) VALUES (:id)"), {"id": user})
                await conn.execute(
                    text(
                        "INSERT INTO agent_share_manifests "
                        "(id, token_hash, created_by, created_at, expires_at) "
                        "VALUES (:id, :hash, :user, now(), now() + interval '1 day')"
                    ),
                    {"id": manifest, "hash": "b" * 64, "user": user},
                )
            repair = _load("038_repair_agent_share_manifests")
            await conn.run_sync(lambda sync: _upgrade(sync, repair))
            await conn.run_sync(lambda sync: _upgrade(sync, repair))
            assert await conn.scalar(text("SELECT count(*) FROM agent_share_manifests")) == int(already_upstream)
            assert await conn.scalar(text("SELECT count(*) FROM agent_share_items")) == 0
            if already_upstream:
                assert await conn.scalar(text("SELECT token_hash FROM agent_share_manifests")) == "b" * 64
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()
