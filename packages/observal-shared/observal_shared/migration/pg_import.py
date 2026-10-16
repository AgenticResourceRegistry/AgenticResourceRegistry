# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""FK-safe PostgreSQL import: session_replication_role='replica', ON CONFLICT DO NOTHING."""

from __future__ import annotations

import json
import os
import shutil
import tarfile
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger as optic

from observal_shared.migration.archive import _safe_tar_extract, _sha256_file, read_manifest
from observal_shared.migration.connections import PgConnParams, connect_pg
from observal_shared.migration.constants import CHUNK_SIZE, INSERT_ORDER
from observal_shared.migration.encoding import _build_insert, _coerce_value
from observal_shared.migration.exceptions import ChecksumMismatchError, MigrationError
from observal_shared.migration.results import ImportResult

if TYPE_CHECKING:
    import asyncpg

    from observal_shared.migration.progress import ProgressReporter


async def _get_column_types(conn: asyncpg.Connection, table: str) -> dict[str, str]:
    """Get column name -> PostgreSQL type mapping for a table."""
    rows = await conn.fetch(
        "SELECT column_name, udt_name FROM information_schema.columns WHERE table_name = $1 ORDER BY ordinal_position",
        table,
    )
    return {row["column_name"]: row["udt_name"] for row in rows}


async def _get_notnull_json_defaults(conn: asyncpg.Connection, table: str) -> dict[str, str]:
    """Discover NOT NULL columns with defaults for a table.

    Handles JSON/JSONB columns (empty objects), boolean columns (false fallback),
    and all other NOT NULL columns with explicit column_default values.
    """
    rows = await conn.fetch(
        """
        SELECT column_name, column_default, udt_name
        FROM information_schema.columns
        WHERE table_name = $1
            AND table_schema = 'public'
            AND is_nullable = 'NO'
            AND (udt_name IN ('json', 'jsonb', 'bool') OR column_default IS NOT NULL)
        """,
        table,
    )
    defaults: dict[str, str] = {}
    for row in rows:
        col_name = row["column_name"]
        col_default = row["column_default"]
        udt_name = row["udt_name"]

        if col_default:
            clean = col_default.split("::")[0].strip().strip("'")
            defaults[col_name] = clean
        elif udt_name in ("json", "jsonb"):
            defaults[col_name] = "{}"
        elif udt_name == "bool":
            defaults[col_name] = "false"
    return defaults


async def _flush_batch(
    conn: asyncpg.Connection,
    table: str,
    columns: list[str],
    col_types: dict[str, str],
    batch: list[dict],
    notnull_defaults: dict[str, str] | None = None,
) -> tuple[int, int, list[str]]:
    """Flush a batch of rows to the database. Returns (inserted, skipped, warnings)."""
    import asyncpg as _asyncpg

    if not batch:
        return 0, 0, []

    query = _build_insert(table, columns, col_types)

    inserted = 0
    skipped = 0
    batch_warnings: list[str] = []
    defaulted_cols: set[str] = set()

    for row in batch:
        # Apply NOT NULL defaults for columns that are NULL in the archive
        if notnull_defaults:
            for col, default_val in notnull_defaults.items():
                if col in columns and row.get(col) is None:
                    row[col] = default_val
                    if col not in defaulted_cols:
                        optic.debug("{}: substituting default for NULL in NOT NULL column '{}'", table, col)
                        defaulted_cols.add(col)

        values = [_coerce_value(row.get(col), col_types.get(col, "")) for col in columns]
        try:
            status = await conn.execute(query, *values)
            count = int(status.split()[-1])
            if count > 0:
                inserted += 1
            else:
                skipped += 1
        except _asyncpg.ForeignKeyViolationError as e:
            row_id = row.get("id", "unknown")
            optic.warning("FK violation in {}, row {}: {}", table, row_id, e.constraint_name)
            skipped += 1
        except _asyncpg.UniqueViolationError as e:
            row_id = row.get("id", "unknown")
            msg = f"{table}: unique conflict on row {row_id} ({e.constraint_name})"
            optic.warning("Unique conflict in {}, row {}: {}", table, row_id, e.constraint_name)
            batch_warnings.append(msg)
            skipped += 1

    return inserted, skipped, batch_warnings


async def _insert_table(
    conn: asyncpg.Connection,
    table: str,
    jsonl_path: Path,
    col_types: dict[str, str],
    notnull_defaults: dict[str, str] | None = None,
) -> tuple[int, int, list[str]]:
    """Insert rows from a JSONL file into a table. Returns (inserted, skipped, warnings)."""
    inserted = 0
    skipped = 0
    table_warnings: list[str] = []
    batch: list[dict] = []
    columns = sorted(col_types.keys())
    logged_skipped = False
    unscoped_facets = 0
    legacy_agent_reports = 0
    scoped_facets = table == "insight_session_facets" and {"project_id", "user_id", "harness"} <= col_types.keys()

    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)

            # Pre-030 archives contain only Agent reports. Do not guess the
            # subject of a newer or ambiguous row with a null subject_type.
            if table == "insight_reports" and "subject_type" in col_types and row.get("subject_type") is None:
                if (
                    "subject_type" in row
                    or not row.get("agent_id")
                    or row.get("component_id")
                    or row.get("component_type")
                ):
                    raise MigrationError(
                        "insight_reports: cannot identify a report without subject_type as an Agent report"
                    )
                row["subject_type"] = "agent"
                legacy_agent_reports += 1

            # Migration 030 discarded legacy facet cache rows: their project,
            # user and harness cannot be recovered from a session ID alone.
            # Preserve that invariant when importing an older archive.
            if scoped_facets and not all(row.get(col) for col in ("project_id", "user_id", "harness")):
                skipped += 1
                unscoped_facets += 1
                continue

            if not logged_skipped:
                skipped_cols = set(row) - set(columns)
                if skipped_cols:
                    optic.debug(
                        "{}: skipping archive columns not in target: {}",
                        jsonl_path.stem,
                        ", ".join(sorted(skipped_cols)),
                    )
                    logged_skipped = True

            batch.append(row)

            if len(batch) >= CHUNK_SIZE:
                ins, sk, bw = await _flush_batch(conn, table, columns, col_types, batch, notnull_defaults)
                inserted += ins
                skipped += sk
                table_warnings.extend(bw)
                batch = []

    if batch and columns:
        ins, sk, bw = await _flush_batch(conn, table, columns, col_types, batch, notnull_defaults)
        inserted += ins
        skipped += sk
        table_warnings.extend(bw)

    if unscoped_facets:
        table_warnings.append(
            f"insight_session_facets: skipped {unscoped_facets} unscoped legacy cache rows "
            "(missing project_id, user_id or harness; re-extract from telemetry)"
        )
    if legacy_agent_reports:
        table_warnings.append(f"insight_reports: classified {legacy_agent_reports} legacy rows as Agent reports")

    return inserted, skipped, table_warnings


async def import_pg(
    params: PgConnParams,
    archive_path: Path,
    reporter: ProgressReporter,
) -> ImportResult:
    """Import a migration archive into the target database.

    Verifies checksums before loading any data. Uses session_replication_role='replica'
    to disable FK triggers during bulk insert. Raises ChecksumMismatchError if
    verification fails before any data load.
    """
    t0 = time.monotonic()
    warnings: list[str] = []

    staging_dir = Path(tempfile.mkdtemp())
    os.chmod(staging_dir, 0o700)
    try:
        await reporter.update(phase="pg_import", pct=0, message="Extracting archive")

        # Extract archive
        with tarfile.open(archive_path, "r:gz") as tar:
            _safe_tar_extract(tar, staging_dir)

        # Read manifest
        manifest_path = staging_dir / "manifest.json"
        if not manifest_path.exists():
            raise MigrationError("Archive does not contain manifest.json")
        manifest = read_manifest(manifest_path)
        migration_id = manifest["migration_id"]

        await reporter.update(phase="pg_import", pct=5, message="Verifying checksums")

        # Verify checksums BEFORE any DB operations
        failed_checksums: list[str] = []
        for table in INSERT_ORDER:
            jsonl_path = staging_dir / "pg" / f"{table}.jsonl"
            if not jsonl_path.exists():
                if table not in manifest["tables"]:
                    continue
                failed_checksums.append(f"{table} (file missing)")
                continue
            if table not in manifest["tables"]:
                continue
            expected = manifest["tables"][table]["checksum"]
            actual = _sha256_file(jsonl_path)
            if actual != expected:
                failed_checksums.append(table)

        if failed_checksums:
            raise ChecksumMismatchError(
                f"Checksum verification failed for: {', '.join(failed_checksums)}. "
                "Archive may be corrupted or tampered. Re-export from source."
            )

        await reporter.update(phase="pg_import", pct=10, message="Connecting to target database")

        # Connect and verify schema version
        conn = await connect_pg(params)
        try:
            target_version = await conn.fetchval("SELECT version_num FROM alembic_version LIMIT 1")
            source_version = manifest["source_alembic_version"]
            if target_version != source_version:
                optic.info(
                    "Schema version mismatch (non-fatal): archive={}, target={}",
                    source_version,
                    target_version,
                )
                warnings.append(f"Schema version mismatch: archive={source_version}, target={target_version}")

            rows_inserted: dict[str, int] = {}
            rows_skipped: dict[str, int] = {}

            # Discover which tables exist on the target
            existing_tables = {
                row["table_name"]
                for row in await conn.fetch(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
                )
            }

            # Disable all user-defined triggers (including FK constraint triggers)
            await conn.execute("SET session_replication_role = 'replica'")
            try:
                total_tables = len(INSERT_ORDER)
                for idx, table in enumerate(INSERT_ORDER):
                    pct = int((idx / total_tables) * 80) + 15  # 15-95%
                    await reporter.update(phase="pg_import", pct=pct, message=f"Importing {table}")

                    jsonl_path = staging_dir / "pg" / f"{table}.jsonl"

                    # Skip tables that don't exist on target
                    if table not in existing_tables:
                        optic.debug("Skipping {} (table does not exist on target)", table)
                        rows_inserted[table] = 0
                        rows_skipped[table] = 0
                        continue

                    # Skip tables not present in the archive
                    if not jsonl_path.exists() or jsonl_path.stat().st_size == 0:
                        rows_inserted[table] = 0
                        rows_skipped[table] = 0
                        continue

                    # Get column types for proper coercion
                    col_types = await _get_column_types(conn, table)

                    # Get NOT NULL defaults from schema
                    notnull_defaults = await _get_notnull_json_defaults(conn, table)

                    ins, sk, tw = await _insert_table(
                        conn,
                        table,
                        jsonl_path,
                        col_types,
                        notnull_defaults=notnull_defaults,
                    )
                    rows_inserted[table] = ins
                    rows_skipped[table] = sk
                    warnings.extend(tw)
            finally:
                # Always restore default trigger behavior
                await conn.execute("SET session_replication_role = 'origin'")

            await reporter.update(phase="pg_import", pct=96, message="Finishing import")

        finally:
            await conn.close()

        elapsed = time.monotonic() - t0
        await reporter.update(phase="pg_import", pct=100, message="Import complete")

        return ImportResult(
            migration_id=migration_id,
            tables_imported=len(INSERT_ORDER),
            rows_inserted=rows_inserted,
            rows_skipped=rows_skipped,
            duration_seconds=round(elapsed, 2),
            warnings=warnings,
        )

    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)
