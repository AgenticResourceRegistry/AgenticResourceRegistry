# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Checksum verify, row-count compare, FK reference checks for migration artifacts."""

from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from observal_shared.migration.archive import _safe_tar_extract, _sha256_file, read_manifest, write_manifest
from observal_shared.migration.ch_export import (
    EXPORT_QUERY_SETTINGS,
    TelemetryChunk,
    _build_ch_count_query,
    _ch_query,
    _chunk_params,
    _parse_ch_datetime,
    _read_count,
)
from observal_shared.migration.connections import ChConnParams, PgConnParams, connect_pg, parse_clickhouse_url
from observal_shared.migration.constants import _UUID_RE, CLICKHOUSE_TABLES, INSERT_ORDER
from observal_shared.migration.exceptions import MigrationError
from observal_shared.migration.results import ChecksumResult, TelemetryValidationResult, ValidationResult
from observal_shared.migration.telemetry_manifest import validate_telemetry_manifest

if TYPE_CHECKING:
    import asyncpg

    from observal_shared.migration.progress import ProgressReporter


async def _validate_fk_references(
    parquet_dir: Path,
    manifest: dict,
    conn: asyncpg.Connection,
) -> dict[str, list[str] | bool]:
    """Read FK columns from Parquet files and check against PostgreSQL."""
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    fk_values: dict[str, set[str]] = {
        "agent_id": set(),
        "user_id": set(),
        "actor_id": set(),
    }

    for table_cfg in CLICKHOUSE_TABLES:
        table_name = table_cfg["name"]
        fk_cols = table_cfg["fk_cols"]
        files = manifest["tables"].get(table_name, {}).get("files", [])
        for filename in files:
            filepath = parquet_dir / filename
            if not filepath.exists():
                continue
            cols_to_read = [c for c in fk_cols if c in fk_values]
            if not cols_to_read:
                continue
            parquet = pq.ParquetFile(filepath)
            for batch in parquet.iter_batches(batch_size=65_536, columns=cols_to_read):
                for col in cols_to_read:
                    if col in batch.schema.names:
                        unique = pc.unique(batch.column(batch.schema.get_field_index(col)))
                        for val in unique.to_pylist():
                            if val is not None and val != "":
                                fk_values[col].add(str(val))

    # Audit events identify users through actor_id.
    fk_values["user_id"] |= fk_values.pop("actor_id", set())

    # Filter to valid UUIDs only
    for key in list(fk_values):
        fk_values[key] = {v.lower() for v in fk_values[key] if _UUID_RE.match(v)}

    # Check against PostgreSQL
    orphaned: dict[str, list[str] | bool] = {}
    for fk_col, pg_table in [("agent_id", "agents"), ("user_id", "users")]:
        ids = fk_values.get(fk_col, set())
        if not ids:
            orphaned[f"orphaned_{fk_col}s"] = []
            orphaned[f"orphaned_{fk_col}s_truncated"] = False
            continue
        existing = set()
        id_list = list(ids)
        # Batch in chunks of 1000 to avoid query size limits
        for i in range(0, len(id_list), 1000):
            batch = id_list[i : i + 1000]
            rows = await conn.fetch(
                f'SELECT id::text FROM "{pg_table}" WHERE id = ANY($1::uuid[])',
                batch,
            )
            existing.update(row["id"] for row in rows)
        missing = sorted(ids - existing)
        orphaned[f"orphaned_{fk_col}s"] = missing[:10_000]
        orphaned[f"orphaned_{fk_col}s_truncated"] = len(missing) > 10_000
    return orphaned


async def validate_pg(
    params: PgConnParams | None,
    archive_path: Path,
    reporter: ProgressReporter,
) -> ValidationResult:
    """Validate archive checksums and optionally compare against a database.

    Raises ChecksumMismatchError if pre-import validation is desired and fails.
    For standalone validation, returns the result with archive_valid=False instead.
    """
    staging_dir = Path(tempfile.mkdtemp())
    os.chmod(staging_dir, 0o700)
    try:
        await reporter.update(phase="validate", pct=0, message="Extracting archive")

        with tarfile.open(archive_path, "r:gz") as tar:
            _safe_tar_extract(tar, staging_dir)

        manifest_path = staging_dir / "manifest.json"
        if not manifest_path.exists():
            raise MigrationError("Archive does not contain manifest.json")
        manifest = read_manifest(manifest_path)

        await reporter.update(phase="validate", pct=20, message="Verifying checksums")

        # Verify checksums
        checksum_results: list[ChecksumResult] = []
        for table in INSERT_ORDER:
            if table not in manifest["tables"]:
                continue
            jsonl_path = staging_dir / "pg" / f"{table}.jsonl"
            expected = manifest["tables"][table]["checksum"]
            if not jsonl_path.exists():
                checksum_results.append(ChecksumResult(table, expected, "", False))
                continue
            actual = _sha256_file(jsonl_path)
            checksum_results.append(ChecksumResult(table, expected, actual, actual == expected))

        all_ok = all(r.passed for r in checksum_results)

        # Optional cross-database validation
        cross_db_results: dict[str, tuple[int, int]] | None = None
        if params:
            await reporter.update(phase="validate", pct=50, message="Comparing row counts against database")
            conn = await connect_pg(params)
            try:
                existing_tables = {
                    row["table_name"]
                    for row in await conn.fetch(
                        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
                    )
                }
                cross_db_results = {}
                for table in INSERT_ORDER:
                    if table not in manifest["tables"]:
                        continue
                    archive_count = manifest["tables"][table]["row_count"]
                    if table not in existing_tables:
                        cross_db_results[table] = (archive_count, -1)
                        continue
                    db_count = await conn.fetchval(f'SELECT count(*) FROM "{table}"')
                    cross_db_results[table] = (archive_count, db_count)
            finally:
                await conn.close()

        await reporter.update(phase="validate", pct=100, message="Validation complete")

        return ValidationResult(
            archive_valid=all_ok,
            checksum_results=checksum_results,
            cross_db_results=cross_db_results,
        )

    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


async def validate_ch(
    ch_params: ChConnParams | None,
    pg_params: PgConnParams | None,
    input_dir: Path,
    reporter: ProgressReporter,
) -> TelemetryValidationResult:
    """Validate chunk integrity and optionally compare bounded target counts/FKs."""
    import httpx as _httpx
    import pyarrow.parquet as pq

    manifest_path = input_dir / "telemetry_manifest.json"
    if not manifest_path.exists():
        raise MigrationError("Telemetry manifest not found.")
    manifest = read_manifest(manifest_path)
    chunks_by_table = validate_telemetry_manifest(manifest, input_dir)

    await reporter.update(phase="validate", pct=0, message="Verifying telemetry chunks")
    checksum_results: dict[str, bool] = {}
    for chunks in chunks_by_table.values():
        for chunk in chunks:
            filename = chunk.get("file")
            if not filename:
                continue
            filepath = input_dir / filename
            passed = filepath.is_file()
            if passed:
                passed = filepath.stat().st_size == chunk["size_bytes"]
            if passed:
                passed = _sha256_file(filepath) == chunk["sha256"]
            if passed:
                try:
                    passed = pq.read_metadata(filepath).num_rows == chunk["row_count"]
                except Exception:
                    passed = False
            checksum_results[filename] = passed

    checksums_valid = all(checksum_results.values()) if checksum_results else True

    row_count_results: dict[str, tuple[int, int]] | None = None
    if ch_params:
        await reporter.update(phase="validate", pct=40, message="Comparing bounded telemetry row counts")
        http_url, db, user, password = parse_clickhouse_url(ch_params.url)
        try:
            async with _httpx.AsyncClient(timeout=_httpx.Timeout(30.0, connect=10.0)) as health_client:
                resp = await health_client.post(
                    http_url,
                    content="SELECT 1",
                    auth=(user, password),
                    params={"database": db},
                )
                resp.raise_for_status()
        except (_httpx.HTTPStatusError, _httpx.RequestError) as exc:
            raise MigrationError(f"ClickHouse health check failed: {type(exc).__name__}") from exc

        cutoff = manifest["export_time_cutoff"]
        row_count_results = {}
        async with _httpx.AsyncClient(timeout=_httpx.Timeout(300.0, connect=10.0)) as http_client:
            existing_sql = "SELECT name FROM system.tables WHERE database = {db:String} FORMAT JSON"
            existing_resp = await _ch_query(
                http_url,
                db,
                user,
                password,
                existing_sql,
                http_client=http_client,
                extra_params={"param_db": db},
            )
            existing = {row["name"] for row in existing_resp.json().get("data", [])}

            for table_cfg in CLICKHOUSE_TABLES:
                table_name = table_cfg["name"]
                manifest_count = manifest["tables"].get(table_name, {}).get("row_count", 0)
                if table_name not in existing:
                    row_count_results[table_name] = (manifest_count, -1)
                    continue

                target_count = 0
                for item in chunks_by_table[table_name]:
                    chunk = TelemetryChunk(
                        table=table_name,
                        start=_parse_ch_datetime(item["range_start"]),
                        end=_parse_ch_datetime(item["range_end"]),
                        bucket=item["bucket"],
                        shard_count=item["shard_count"],
                    )
                    resp = await _ch_query(
                        http_url,
                        db,
                        user,
                        password,
                        _build_ch_count_query(table_cfg, chunk),
                        http_client=http_client,
                        extra_params={**_chunk_params(chunk, cutoff), **EXPORT_QUERY_SETTINGS},
                    )
                    target_count += _read_count(resp)
                row_count_results[table_name] = (manifest_count, target_count)

    fk_results: dict[str, list[str]] | None = None
    if pg_params:
        await reporter.update(phase="validate", pct=70, message="Validating FK references")
        conn = await connect_pg(pg_params)
        try:
            fk_results = await _validate_fk_references(input_dir, manifest, conn)
            manifest["fk_validation"] = {**fk_results, "validated_at": datetime.now(UTC).isoformat()}
            write_manifest(manifest_path, manifest)
        finally:
            await conn.close()

    await reporter.update(phase="validate", pct=100, message="Telemetry validation complete")
    return TelemetryValidationResult(
        checksums_valid=checksums_valid,
        checksum_results=checksum_results,
        fk_results=fk_results,
        row_count_results=row_count_results,
    )
