#!/bin/bash
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Swathi Saravanan <ss4522@cornell.edu>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

# The v1 Alembic baseline is intentionally empty: new installations use the
# current models to create the final empty schema. Running *historical* ADD
# COLUMN migrations against that new schema fails. Crucially, an unversioned
# database WITH tables is not a new installation and must never be stamped.
DB_STATE=$(/app/.venv/bin/python -c "
import asyncio
from sqlalchemy import text
from database import engine

async def check():
    try:
        async with engine.connect() as conn:
            tables = set((await conn.execute(text(
                'SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = current_schema()'
            ))).scalars())
            if not tables:
                return 'fresh'
            if 'alembic_version' not in tables:
                return 'unversioned'
            revision = await conn.scalar(text('SELECT version_num FROM alembic_version LIMIT 1'))
            if not revision or len(tables - {'alembic_version'}) == 0:
                return 'unversioned'
            return 'versioned'
    finally:
        await engine.dispose()

print(asyncio.run(check()))
")

case "$DB_STATE" in
fresh)
    echo "Creating schema for an empty database..."
    /app/.venv/bin/python -c "
import asyncio
from sqlalchemy import text
from database import engine
from models import Base

async def init():
    try:
        async with engine.begin() as conn:
            await conn.execute(text('CREATE EXTENSION IF NOT EXISTS pg_trgm'))
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()

asyncio.run(init())
"
    # No migration data exists on a genuinely empty database. Stamp ONLY after
    # the transactional model schema creation succeeded; never as an upgrade
    # failure fallback. An interruption before stamp leaves unversioned tables
    # and the next init refuses instead of guessing the schema is complete.
    echo "Recording freshly created schema revision..."
    /app/.venv/bin/python -m alembic stamp head
    ;;
versioned)
    echo "Running database migrations..."
    if ! /app/.venv/bin/python -m alembic upgrade head; then
        echo "ERROR: alembic upgrade failed. Initialization stopped; no revision was stamped."
        exit 1
    fi
    ;;
*)
    echo "ERROR: unversioned or incomplete schema: manual inspection required; refusing to stamp."
    exit 1
    ;;
esac

echo "Running ClickHouse migrations..."
/app/.venv/bin/python -m services.clickhouse.migrations

echo "Initialization complete."
