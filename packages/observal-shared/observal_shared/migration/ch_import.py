# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Checksummed, chunk-resumable ClickHouse telemetry import."""

from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING

from loguru import logger as optic

from observal_shared.migration.archive import _sha256_file, read_manifest, write_manifest
from observal_shared.migration.ch_export import _ch_query
from observal_shared.migration.connections import ChConnParams, parse_clickhouse_url
from observal_shared.migration.constants import CLICKHOUSE_TABLES, DEFAULT_PROJECT_ID
from observal_shared.migration.exceptions import ChecksumMismatchError, ConnectionFailedError, MigrationError
from observal_shared.migration.results import TelemetryImportResult
from observal_shared.migration.telemetry_manifest import validate_telemetry_manifest

if TYPE_CHECKING:
    from pathlib import Path

    from observal_shared.migration.progress import ProgressReporter

IMPORT_QUERY_SETTINGS = {
    "max_threads": "1",
    "max_insert_threads": "1",
    "max_memory_usage": "350000000",
}


async def _ch_existing_tables(
    http_url: str,
    db: str,
    user: str,
    password: str,
) -> set[str]:
    """Query system.tables to discover which tables exist on target ClickHouse."""
    sql = "SELECT name FROM system.tables WHERE database = {db:String} FORMAT JSON"
    resp = await _ch_query(http_url, db, user, password, sql, extra_params={"param_db": db})
    return {row["name"] for row in resp.json().get("data", [])}


def _rewrite_project_id(parquet_path: Path, target_project_id: str) -> Path:
    """Rewrite project_id in bounded record batches instead of loading a whole file."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    parquet = pq.ParquetFile(parquet_path)
    schema = parquet.schema_arrow
    if "project_id" not in schema.names:
        return parquet_path

    column_index = schema.names.index("project_id")
    temporary = parquet_path.with_suffix(".tmp.parquet")
    writer = pq.ParquetWriter(temporary, schema, compression="zstd")
    try:
        for batch in parquet.iter_batches(batch_size=65_536):
            project_ids = pa.array([target_project_id] * batch.num_rows, type=schema.field(column_index).type)
            writer.write_batch(batch.set_column(column_index, schema.field(column_index), project_ids))
    except Exception:
        writer.close()
        temporary.unlink(missing_ok=True)
        raise
    else:
        writer.close()
    temporary.chmod(0o600)
    return temporary


async def _ch_import(
    http_url: str,
    db: str,
    user: str,
    password: str,
    table: str,
    parquet_path: Path,
    *,
    deduplication_token: str,
) -> None:
    """Stream one bounded Parquet chunk into ClickHouse."""
    import httpx as _httpx

    params = {
        "database": db,
        "query": f"INSERT INTO {table} FORMAT Parquet",
        "insert_deduplication_token": deduplication_token,
        **IMPORT_QUERY_SETTINGS,
    }

    async def _file_stream():
        with open(parquet_path, "rb") as file:
            while chunk := file.read(65_536):
                yield chunk

    try:
        async with _httpx.AsyncClient(timeout=_httpx.Timeout(600.0, connect=10.0)) as client:
            resp = await client.post(http_url, content=_file_stream(), auth=(user, password), params=params)
            if resp.is_error:
                detail = (await resp.aread()).decode("utf-8", errors="replace")[:500]
                optic.error("ClickHouse returned HTTP {}", resp.status_code)
                raise MigrationError(f"ClickHouse returned HTTP {resp.status_code}: {detail}")
    except _httpx.RequestError as exc:
        optic.error("ClickHouse unreachable: {}", exc)
        raise ConnectionFailedError(f"ClickHouse unreachable: {type(exc).__name__}") from exc


def _load_import_state(state_path: Path, migration_id: str) -> dict[str, dict]:
    if not state_path.exists():
        return {}
    state = read_manifest(state_path)
    if state.get("migration_id") != migration_id:
        raise MigrationError("Telemetry import state belongs to a different migration.")
    completed = state.get("completed_chunks", {})
    if not isinstance(completed, dict):
        raise MigrationError("Telemetry import state has invalid completed chunk metadata.")
    return completed


def _write_import_state(state_path: Path, migration_id: str, completed: dict[str, dict]) -> None:
    write_manifest(
        state_path,
        {
            "schema_version": "2.0",
            "migration_id": migration_id,
            "completed_chunks": completed,
        },
    )


def _deduplication_token(migration_id: str, chunk_id: str, checksum: str) -> str:
    return hashlib.sha256(f"{migration_id}:{chunk_id}:{checksum}".encode()).hexdigest()


def _summary_rebuild_chunks(event_chunks: list[dict]) -> list[dict]:
    """Return bounded hash shards for rebuilding imported session summaries."""
    if event_chunks and event_chunks[0].get("legacy"):
        return [
            {
                "chunk_id": f"legacy-summary:{bucket}:64",
                "bucket": bucket,
                "shard_count": 64,
            }
            for bucket in range(64)
        ]
    return event_chunks


async def _rebuild_session_stats_chunk(
    http_url: str,
    db: str,
    user: str,
    password: str,
    chunk: dict,
) -> None:
    """Write one complete summary per session after all event chunks are present."""
    sql = (
        "INSERT INTO session_stats_agg "
        "SELECT 'default' AS project_id, session_id, "
        "coalesce(anyIf(agent_id, agent_id IS NOT NULL AND agent_id != ''), '') AS agent_id, "
        "coalesce(anyIf(agent_version, agent_version IS NOT NULL AND agent_version != ''), '') AS agent_version, "
        "user_id, coalesce(anyIf(parent_session_id, parent_session_id IS NOT NULL), '') AS parent_session_id, "
        "harness, coalesce(anyIf(layer_hash, layer_hash IS NOT NULL AND layer_hash != ''), '') AS layer_hash, "
        "minIf(timestamp, rendered = 1 AND timestamp > '1971-01-01 00:00:00' "
        "AND timestamp < '2099-01-01 00:00:00') AS first_event_time, "
        "maxIf(timestamp, rendered = 1 AND timestamp > '1971-01-01 00:00:00' "
        "AND timestamp < '2099-01-01 00:00:00') AS last_event_time, "
        "countIf(rendered = 1) AS event_count, "
        "countIf(rendered = 1 AND event_type = 'user_prompt') AS prompt_count, "
        "countIf(rendered = 1 AND event_type = 'tool_call') AS tool_call_count, "
        "countIf(rendered = 1 AND event_type = 'tool_result') AS tool_result_count, "
        "sumIf(input_tokens, rendered = 1) AS input_tokens, "
        "sumIf(output_tokens, rendered = 1) AS output_tokens, "
        "sumIf(cache_read_tokens, rendered = 1) AS cache_read_tokens, "
        "sumIf(cache_write_tokens, rendered = 1) AS cache_write_tokens, "
        "max(credits) AS total_credits, anyLastIf(model, rendered = 1 AND model != '') AS model, "
        "toUInt64(toUnixTimestamp64Milli(now64(3))) + 1 AS summary_version, now64(3) AS updated_at "
        "FROM session_events FINAL "
        "WHERE project_id = 'default' "
        "AND sipHash64(project_id, user_id, harness, session_id) % 64 = {bucket:UInt32} % 64 "
        "AND sipHash64(project_id, user_id, harness, session_id) % {shard_count:UInt32} = {bucket:UInt32} "
        "GROUP BY project_id, session_id, user_id, harness"
    )
    await _ch_query(
        http_url,
        db,
        user,
        password,
        sql,
        extra_params={
            "param_bucket": str(chunk["bucket"]),
            "param_shard_count": str(chunk["shard_count"]),
            **IMPORT_QUERY_SETTINGS,
        },
    )


async def import_ch(
    params: ChConnParams,
    input_dir: Path,
    reporter: ProgressReporter,
) -> TelemetryImportResult:
    """Verify and import telemetry one resumable chunk at a time."""
    import httpx as _httpx

    t0 = time.monotonic()
    warnings: list[str] = []
    manifest_path = input_dir / "telemetry_manifest.json"
    if not manifest_path.exists():
        raise MigrationError("Telemetry manifest not found in input directory.")
    manifest = read_manifest(manifest_path)
    chunks_by_table = validate_telemetry_manifest(manifest, input_dir)
    migration_id = manifest["migration_id"]

    await reporter.update(phase="ch_import", pct=0, message="Verifying telemetry chunks")
    import pyarrow.parquet as pq

    failed: list[str] = []
    for chunks in chunks_by_table.values():
        for chunk in chunks:
            filename = chunk.get("file")
            if not filename:
                continue
            filepath = input_dir / filename
            if not filepath.is_file():
                failed.append(f"{filename} (missing)")
                continue
            if filepath.stat().st_size != chunk["size_bytes"] or _sha256_file(filepath) != chunk["sha256"]:
                failed.append(filename)
                continue
            try:
                if pq.read_metadata(filepath).num_rows != chunk["row_count"]:
                    failed.append(f"{filename} (row count)")
            except Exception:
                failed.append(f"{filename} (invalid Parquet)")
    if failed:
        raise ChecksumMismatchError(f"Checksum verification failed for: {', '.join(failed)}")

    http_url, db, user, password = parse_clickhouse_url(params.url)
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
        raise ConnectionFailedError(f"ClickHouse health check failed: {type(exc).__name__}") from exc

    await reporter.update(phase="ch_import", pct=5, message="Connected to ClickHouse")
    existing = await _ch_existing_tables(http_url, db, user, password)
    state_path = input_dir / ".import_state.json"
    completed = _load_import_state(state_path, migration_id)

    all_file_chunks = [
        chunk for table_chunks in chunks_by_table.values() for chunk in table_chunks if chunk.get("file") is not None
    ]
    total_chunks = len(all_file_chunks)
    processed_chunks = 0
    rows_imported: dict[str, int] = {}
    tables_skipped: list[str] = []

    for table_cfg in CLICKHOUSE_TABLES:
        table_name = table_cfg["name"]
        chunks = chunks_by_table[table_name]
        file_chunks = [chunk for chunk in chunks if chunk.get("file")]
        rows_imported[table_name] = 0
        if not file_chunks:
            continue
        if table_name not in existing:
            tables_skipped.append(table_name)
            warnings.append(f"{table_name}: table does not exist on target")
            processed_chunks += len(file_chunks)
            continue

        resumed_rows = 0
        for chunk in file_chunks:
            processed_chunks += 1
            chunk_id = chunk["chunk_id"]
            filename = chunk["file"]
            checksum = chunk["sha256"]
            prior = completed.get(chunk_id)
            if prior and prior.get("sha256") == checksum and prior.get("filename") == filename:
                resumed_rows += chunk["row_count"]
                continue

            pct = 10 + int((processed_chunks / max(total_chunks, 1)) * 85)
            await reporter.update(
                phase="ch_import",
                pct=pct,
                message=f"Importing {table_name} chunk {processed_chunks}/{total_chunks}",
            )
            filepath = input_dir / filename
            import_path = _rewrite_project_id(filepath, DEFAULT_PROJECT_ID)
            try:
                await _ch_import(
                    http_url,
                    db,
                    user,
                    password,
                    table_name,
                    import_path,
                    deduplication_token=_deduplication_token(migration_id, chunk_id, checksum),
                )
            except MigrationError as exc:
                raise MigrationError(f"Failed to import telemetry chunk {chunk_id}: {exc}") from exc
            finally:
                if import_path != filepath:
                    import_path.unlink(missing_ok=True)

            rows_imported[table_name] += chunk["row_count"]
            completed[chunk_id] = {
                "filename": filename,
                "sha256": checksum,
                "rows": chunk["row_count"],
            }
            _write_import_state(state_path, migration_id, completed)

        if resumed_rows:
            warnings.append(f"{table_name}: resumed {resumed_rows} rows from completed chunks")
        optic.info(
            "{}: {} rows imported, {} rows resumed",
            table_name,
            rows_imported[table_name],
            resumed_rows,
        )

    # session_stats_mv evaluates each INSERT block independently. Rebuild one
    # canonical summary per session after both event and exported summary chunks
    # are present so a partial materialized-view row cannot win by version.
    event_chunks = chunks_by_table["session_events"]
    if any(chunk.get("file") for chunk in event_chunks) and {"session_events", "session_stats_agg"}.issubset(existing):
        await reporter.update(phase="ch_import", pct=96, message="Rebuilding complete session summaries")
        for chunk in _summary_rebuild_chunks(event_chunks):
            summary_state_id = f"session-summary:{chunk['chunk_id']}"
            if completed.get(summary_state_id, {}).get("source_manifest") == migration_id:
                continue
            try:
                await _rebuild_session_stats_chunk(http_url, db, user, password, chunk)
            except MigrationError as exc:
                raise MigrationError(f"Failed to rebuild session summaries for {chunk['chunk_id']}: {exc}") from exc
            completed[summary_state_id] = {"source_manifest": migration_id}
            _write_import_state(state_path, migration_id, completed)

    elapsed = time.monotonic() - t0
    await reporter.update(phase="ch_import", pct=100, message="Telemetry import complete")
    return TelemetryImportResult(
        migration_id=migration_id,
        tables_imported=sum(1 for value in rows_imported.values() if value > 0),
        tables_skipped=tables_skipped,
        rows_imported=rows_imported,
        duration_seconds=round(elapsed, 2),
        warnings=warnings,
    )
