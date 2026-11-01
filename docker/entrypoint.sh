#!/bin/bash
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Swathi Saravanan <ss4522@cornell.edu>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

echo "Checking PostgreSQL schema state..."
DB_STATE=$(/app/.venv/bin/python -m services.schema_bootstrap state)
case "$DB_STATE" in
    fresh)
        echo "Fresh database detected: creating current schema and migration-only objects..."
        /app/.venv/bin/python -m services.schema_bootstrap init
        echo "Stamping current schema version..."
        /app/.venv/bin/python -m alembic stamp head
        ;;
    existing)
        echo "Running database migrations on the versioned database..."
        if ! /app/.venv/bin/python -m alembic upgrade head; then
            echo "ERROR: alembic upgrade failed; no revision was stamped." >&2
            exit 1
        fi
        ;;
    *)
        echo "ERROR: refusing to stamp an unversioned or partially initialized database." >&2
        echo "Verify its schema and migration history manually before retrying." >&2
        exit 1
        ;;
esac

echo "Backfilling pending version reviews before serving traffic..."
/app/.venv/bin/python -m jobs.review_backfill

echo "Running ClickHouse migrations..."
/app/.venv/bin/python -m services.clickhouse.migrations

echo "Initialization complete."
