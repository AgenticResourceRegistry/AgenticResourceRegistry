# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Bounded, resumable Parquet export from ClickHouse."""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from loguru import logger as optic

from observal_shared.migration.archive import _is_empty_parquet, _sha256_file, read_manifest, write_manifest
from observal_shared.migration.connections import ChConnParams, parse_clickhouse_url
from observal_shared.migration.constants import (
    CLICKHOUSE_TABLES,
    EPOCH_SENTINELS,
    TELEMETRY_MANIFEST_VERSION,
    TableCfg,
)
from observal_shared.migration.exceptions import ConnectionFailedError, MigrationError, PrerequisiteError
from observal_shared.migration.results import TelemetryExportResult

if TYPE_CHECKING:
    from pathlib import Path

    import httpx

    from observal_shared.migration.progress import ProgressReporter

TARGET_CHUNK_ROWS = 250_000
MIN_CHUNK_WINDOW = timedelta(hours=1)
MAX_SHARD_COUNT = 65_536
EXPORT_QUERY_SETTINGS = {
    "max_threads": "1",
    "max_final_threads": "1",
    "do_not_merge_across_partitions_select_final": "1",
}
_RETRYABLE_CHUNK_ERROR_MARKERS = (
    "memory limit",
    "memory_limit_exceeded",
    "cannot allocate memory",
    "attempt to allocate chunk",
    "readtimeout",
)


@dataclass(frozen=True)
class TelemetryChunk:
    """One deterministic, non-overlapping unit of a telemetry export."""

    table: str
    start: datetime
    end: datetime
    bucket: int
    shard_count: int

    @property
    def chunk_id(self) -> str:
        # ClickHouse DateTime64(3), query parameters, and manifest boundaries all
        # use millisecond precision. Canonicalize IDs to the same precision.
        start = self.start.replace(microsecond=(self.start.microsecond // 1000) * 1000).strftime("%Y%m%dT%H%M%S%f")
        end = self.end.replace(microsecond=(self.end.microsecond // 1000) * 1000).strftime("%Y%m%dT%H%M%S%f")
        return f"{self.table}:{start}:{end}:{self.bucket}:{self.shard_count}"

    @property
    def filename(self) -> str:
        start = self.start.strftime("%Y%m%dT%H%M%S")
        end = self.end.strftime("%Y%m%dT%H%M%S")
        return f"{self.table}_{start}_{end}_b{self.bucket:05d}-of-{self.shard_count:05d}.parquet"


def _safe_error_detail(content: bytes, limit: int = 500) -> str:
    """Decode and bound a ClickHouse HTTP error without exposing request credentials."""
    return content.decode("utf-8", errors="replace").replace("\x00", "")[:limit]


async def _ch_query(
    http_url: str,
    db: str,
    user: str,
    password: str,
    sql: str,
    *,
    stream_to: Path | None = None,
    http_client: httpx.AsyncClient | None = None,
    extra_params: dict[str, str] | None = None,
) -> httpx.Response:
    """Execute a ClickHouse query via HTTP and optionally stream it atomically."""
    import httpx as _httpx

    params: dict[str, str] = {"database": db}
    if extra_params:
        params.update(extra_params)
    owns_client = http_client is None
    if owns_client:
        http_client = _httpx.AsyncClient(timeout=_httpx.Timeout(300.0, connect=10.0))
    try:
        if stream_to:
            tmp = stream_to.with_suffix(stream_to.suffix + ".tmp")
            try:
                async with http_client.stream(
                    "POST", http_url, content=sql, auth=(user, password), params=params
                ) as resp:
                    if resp.is_error:
                        detail = _safe_error_detail(await resp.aread())
                        optic.error("ClickHouse returned HTTP {}", resp.status_code)
                        raise MigrationError(f"ClickHouse returned HTTP {resp.status_code}: {detail}")
                    with open(tmp, "wb") as file:
                        async for chunk in resp.aiter_bytes(chunk_size=65_536):
                            file.write(chunk)
                        file.flush()
                        os.fsync(file.fileno())
                tmp.chmod(0o600)
                os.replace(tmp, stream_to)
                return resp
            except Exception:
                tmp.unlink(missing_ok=True)
                raise

        resp = await http_client.post(http_url, content=sql, auth=(user, password), params=params)
        if resp.is_error:
            detail = _safe_error_detail(await resp.aread())
            optic.error("ClickHouse returned HTTP {}", resp.status_code)
            raise MigrationError(f"ClickHouse returned HTTP {resp.status_code}: {detail}")
        return resp
    except _httpx.RequestError as exc:
        optic.error("ClickHouse unreachable: {}", exc)
        raise ConnectionFailedError(f"ClickHouse unreachable: {type(exc).__name__}") from exc
    finally:
        if owns_client:
            await http_client.aclose()


def _chunk_params(chunk: TelemetryChunk, cutoff: str) -> dict[str, str]:
    return {
        "param_chunk_start": _format_ch_datetime(chunk.start),
        "param_chunk_end": _format_ch_datetime(chunk.end),
        "param_cutoff": cutoff,
        "param_bucket": str(chunk.bucket),
        "param_shard_count": str(chunk.shard_count),
        **EXPORT_QUERY_SETTINGS,
    }


def _chunk_predicate(table_cfg: TableCfg) -> str:
    time_col = table_cfg["time_col"]
    shard_expr = table_cfg["shard_expr"]
    predicates = [
        f"{time_col} >= {{chunk_start:String}}",
        f"{time_col} < {{chunk_end:String}}",
        f"{time_col} < {{cutoff:String}}",
        f"({shard_expr}) % {{shard_count:UInt32}} = {{bucket:UInt32}}",
    ]
    physical_shards = table_cfg["physical_shards"]
    if physical_shards > 1:
        predicates.append(f"({shard_expr}) % {physical_shards} = {{bucket:UInt32}} % {physical_shards}")
    return " AND ".join(predicates)


def _build_ch_export_query(table_cfg: TableCfg, chunk: TelemetryChunk) -> str:
    """Build a bounded Parquet query for one deterministic chunk."""
    final = " FINAL" if table_cfg["engine"] == "replacing" else ""
    return f"SELECT * FROM {table_cfg['name']}{final} WHERE {_chunk_predicate(table_cfg)} FORMAT Parquet"


def _build_ch_count_query(table_cfg: TableCfg, chunk: TelemetryChunk) -> str:
    """Build a bounded count query using exactly the export predicate."""
    final = " FINAL" if table_cfg["engine"] == "replacing" else ""
    return f"SELECT count() AS cnt FROM {table_cfg['name']}{final} WHERE {_chunk_predicate(table_cfg)} FORMAT JSON"


def _read_count(resp: httpx.Response) -> int:
    """Parse a count query response."""
    return int(resp.json().get("data", [{}])[0].get("cnt", 0))


def _build_ch_time_range_query(table_cfg: TableCfg) -> str:
    """Discover the physical time range without an unbounded FINAL operation."""
    time_col = table_cfg["time_col"]
    return (
        f"SELECT min({time_col}) AS min_t, max({time_col}) AS max_t "
        f"FROM {table_cfg['name']} WHERE {time_col} < {{cutoff:String}} FORMAT JSON"
    )


def _format_ch_datetime(value: datetime) -> str:
    normalized = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return normalized.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _parse_ch_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace(" ", "T").replace("Z", "+00:00"))
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _month_windows(min_dt: datetime, max_dt: datetime, cutoff: datetime) -> list[tuple[datetime, datetime]]:
    """Return non-overlapping UTC month windows clipped to the export cutoff."""
    if min_dt >= cutoff:
        return []
    current = min_dt.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    final = min(max_dt, cutoff)
    windows: list[tuple[datetime, datetime]] = []
    while current <= final and current < cutoff:
        if current.month == 12:
            next_month = current.replace(year=current.year + 1, month=1)
        else:
            next_month = current.replace(month=current.month + 1)
        end = min(next_month, cutoff)
        if current < end:
            windows.append((current, end))
        current = next_month
    return windows


def _table_windows(
    table_cfg: TableCfg,
    min_dt: datetime,
    max_dt: datetime,
    cutoff: datetime,
) -> list[tuple[datetime, datetime]]:
    """Plan initial ranges, keeping all events for one session in one range."""
    if table_cfg["preserve_shard_group"]:
        final_end = min(max_dt + timedelta(milliseconds=1), cutoff)
        return [(min_dt, final_end)] if min_dt < final_end else []
    return _month_windows(min_dt, max_dt, cutoff)


def _split_chunk(
    chunk: TelemetryChunk,
    *,
    prefer_hash: bool,
    allow_time: bool = True,
) -> tuple[TelemetryChunk, TelemetryChunk] | None:
    """Split a chunk without overlap, preferring the dimension tied to the failure."""
    can_split_hash = chunk.shard_count < MAX_SHARD_COUNT
    can_split_time = allow_time and chunk.end - chunk.start > MIN_CHUNK_WINDOW

    if prefer_hash and can_split_hash:
        shards = chunk.shard_count * 2
        return (
            TelemetryChunk(chunk.table, chunk.start, chunk.end, chunk.bucket, shards),
            TelemetryChunk(chunk.table, chunk.start, chunk.end, chunk.bucket + chunk.shard_count, shards),
        )
    if can_split_time:
        midpoint = chunk.start + (chunk.end - chunk.start) / 2
        return (
            TelemetryChunk(chunk.table, chunk.start, midpoint, chunk.bucket, chunk.shard_count),
            TelemetryChunk(chunk.table, midpoint, chunk.end, chunk.bucket, chunk.shard_count),
        )
    if can_split_hash:
        shards = chunk.shard_count * 2
        return (
            TelemetryChunk(chunk.table, chunk.start, chunk.end, chunk.bucket, shards),
            TelemetryChunk(chunk.table, chunk.start, chunk.end, chunk.bucket + chunk.shard_count, shards),
        )
    return None


def _is_retryable_chunk_error(exc: MigrationError) -> bool:
    detail = str(exc).lower()
    return any(marker in detail for marker in _RETRYABLE_CHUNK_ERROR_MARKERS)


def _parquet_row_count(path: Path) -> int:
    import pyarrow.parquet as pq

    return pq.read_metadata(path).num_rows


def _chunk_manifest_entry(chunk: TelemetryChunk, *, row_count: int, path: Path | None) -> dict:
    entry = {
        "chunk_id": chunk.chunk_id,
        "range_start": _format_ch_datetime(chunk.start),
        "range_end": _format_ch_datetime(chunk.end),
        "bucket": chunk.bucket,
        "shard_count": chunk.shard_count,
        "row_count": row_count,
        "file": path.name if path else None,
        "size_bytes": path.stat().st_size if path else 0,
        "sha256": _sha256_file(path) if path else None,
    }
    return entry


async def _export_candidate(
    *,
    table_cfg: TableCfg,
    chunk: TelemetryChunk,
    cutoff: str,
    output_dir: Path,
    connection: tuple[str, str, str, str],
    http_client: httpx.AsyncClient,
    reporter: ProgressReporter,
    pct: int,
) -> list[dict]:
    """Plan and export a candidate, recursively splitting oversized chunks."""
    http_url, db, user, password = connection
    params = _chunk_params(chunk, cutoff)
    count_sql = _build_ch_count_query(table_cfg, chunk)

    try:
        count_resp = await _ch_query(
            http_url,
            db,
            user,
            password,
            count_sql,
            http_client=http_client,
            extra_params=params,
        )
        count = _read_count(count_resp)
    except MigrationError as exc:
        children = (
            _split_chunk(
                chunk,
                prefer_hash=True,
                allow_time=not table_cfg["preserve_shard_group"],
            )
            if _is_retryable_chunk_error(exc)
            else None
        )
        if not children:
            raise MigrationError(f"Failed to plan telemetry chunk {chunk.chunk_id}: {exc}") from exc
        optic.warning("Splitting telemetry chunk after count memory error: {}", chunk.chunk_id)
        results: list[dict] = []
        for child in children:
            results.extend(
                await _export_candidate(
                    table_cfg=table_cfg,
                    chunk=child,
                    cutoff=cutoff,
                    output_dir=output_dir,
                    connection=connection,
                    http_client=http_client,
                    reporter=reporter,
                    pct=pct,
                )
            )
        return results

    if count > TARGET_CHUNK_ROWS:
        children = _split_chunk(
            chunk,
            prefer_hash=table_cfg["preserve_shard_group"],
            allow_time=not table_cfg["preserve_shard_group"],
        )
        if children:
            results = []
            for child in children:
                results.extend(
                    await _export_candidate(
                        table_cfg=table_cfg,
                        chunk=child,
                        cutoff=cutoff,
                        output_dir=output_dir,
                        connection=connection,
                        http_client=http_client,
                        reporter=reporter,
                        pct=pct,
                    )
                )
            return results

    if count == 0:
        return [_chunk_manifest_entry(chunk, row_count=0, path=None)]

    path = output_dir / chunk.filename
    await reporter.update(
        phase="ch_export",
        pct=pct,
        message=f"Exporting {table_cfg['name']} chunk ({count:,} rows)",
    )
    try:
        await _ch_query(
            http_url,
            db,
            user,
            password,
            _build_ch_export_query(table_cfg, chunk),
            stream_to=path,
            http_client=http_client,
            extra_params=params,
        )
    except MigrationError as exc:
        children = (
            _split_chunk(
                chunk,
                prefer_hash=True,
                allow_time=not table_cfg["preserve_shard_group"],
            )
            if _is_retryable_chunk_error(exc)
            else None
        )
        if not children:
            raise MigrationError(f"Failed to export telemetry chunk {chunk.chunk_id}: {exc}") from exc
        optic.warning("Splitting telemetry chunk after export memory error: {}", chunk.chunk_id)
        results = []
        for child in children:
            results.extend(
                await _export_candidate(
                    table_cfg=table_cfg,
                    chunk=child,
                    cutoff=cutoff,
                    output_dir=output_dir,
                    connection=connection,
                    http_client=http_client,
                    reporter=reporter,
                    pct=pct,
                )
            )
        return results

    if _is_empty_parquet(path):
        path.unlink(missing_ok=True)
        return [_chunk_manifest_entry(chunk, row_count=0, path=None)]

    actual_rows = _parquet_row_count(path)
    if actual_rows != count:
        optic.warning(
            "Telemetry chunk row count changed during export: chunk={} planned={} actual={}",
            chunk.chunk_id,
            count,
            actual_rows,
        )
    return [_chunk_manifest_entry(chunk, row_count=actual_rows, path=path)]


async def export_ch(
    params: ChConnParams,
    manifest_path: Path,
    output_dir: Path,
    reporter: ProgressReporter,
) -> TelemetryExportResult:
    """Export ClickHouse telemetry into bounded, checksummed Parquet chunks."""
    import httpx as _httpx

    t0 = time.monotonic()
    if not manifest_path.exists():
        raise PrerequisiteError(f"Phase 1 manifest not found: {manifest_path}")
    p1_manifest = read_manifest(manifest_path)
    if not p1_manifest.get("phase1_completed_at"):
        raise PrerequisiteError("Phase 1 has not completed. Run PG export first.")
    migration_id = p1_manifest["migration_id"]

    cutoff_dt = datetime.now(UTC)
    export_time_cutoff = _format_ch_datetime(cutoff_dt)
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

    await reporter.update(phase="ch_export", pct=0, message="Connected to ClickHouse")

    if output_dir.exists() and any(output_dir.iterdir()):
        raise MigrationError(f"Output directory is not empty: {output_dir}")
    dir_existed = output_dir.exists()
    os.makedirs(output_dir, mode=0o700, exist_ok=True)
    os.chmod(output_dir, 0o700)

    try:
        table_meta: dict[str, dict] = {}
        total_rows = 0
        total_size = 0
        total_tables = len(CLICKHOUSE_TABLES)
        connection = (http_url, db, user, password)

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
            source_tables = {row["name"] for row in existing_resp.json().get("data", [])}

            for table_index, table_cfg in enumerate(CLICKHOUSE_TABLES):
                table_name = table_cfg["name"]
                pct = int((table_index / total_tables) * 90) + 5
                empty_meta = {"files": [], "row_count": 0, "checksum": {}, "time_range": None, "chunks": []}

                if table_name not in source_tables:
                    table_meta[table_name] = empty_meta
                    optic.debug("{}: table not found on source (skipped)", table_name)
                    await reporter.update(phase="ch_export", pct=pct, message=f"Skipping {table_name} (not found)")
                    continue

                await reporter.update(phase="ch_export", pct=pct, message=f"Planning bounded chunks for {table_name}")
                tr_resp = await _ch_query(
                    http_url,
                    db,
                    user,
                    password,
                    _build_ch_time_range_query(table_cfg),
                    http_client=http_client,
                    extra_params={"param_cutoff": export_time_cutoff, **EXPORT_QUERY_SETTINGS},
                )
                tr_data = tr_resp.json().get("data", [{}])[0]
                min_t = tr_data.get("min_t")
                max_t = tr_data.get("max_t")
                if min_t in EPOCH_SENTINELS or max_t in EPOCH_SENTINELS:
                    table_meta[table_name] = empty_meta
                    optic.debug("{}: empty", table_name)
                    continue

                min_dt = _parse_ch_datetime(min_t)
                max_dt = _parse_ch_datetime(max_t)
                chunks: list[dict] = []
                for window_start, window_end in _table_windows(table_cfg, min_dt, max_dt, cutoff_dt):
                    for bucket in range(table_cfg["base_shards"]):
                        candidate = TelemetryChunk(
                            table_name,
                            window_start,
                            window_end,
                            bucket,
                            table_cfg["base_shards"],
                        )
                        chunks.extend(
                            await _export_candidate(
                                table_cfg=table_cfg,
                                chunk=candidate,
                                cutoff=export_time_cutoff,
                                output_dir=output_dir,
                                connection=connection,
                                http_client=http_client,
                                reporter=reporter,
                                pct=pct,
                            )
                        )

                files = [entry["file"] for entry in chunks if entry["file"]]
                checksums = {entry["file"]: entry["sha256"] for entry in chunks if entry["file"]}
                table_row_count = sum(entry["row_count"] for entry in chunks)
                table_size = sum(entry["size_bytes"] for entry in chunks)
                total_rows += table_row_count
                total_size += table_size
                table_meta[table_name] = {
                    "files": files,
                    "row_count": table_row_count,
                    "checksum": checksums,
                    "time_range": {"min": str(min_t), "max": str(max_t)} if files else None,
                    "chunks": chunks,
                }
                optic.info("{}: {} rows in {} chunk file(s)", table_name, table_row_count, len(files))

        await reporter.update(phase="ch_export", pct=95, message="Writing telemetry manifest")
        telemetry_manifest = {
            "schema_version": TELEMETRY_MANIFEST_VERSION,
            "migration_id": migration_id,
            "phase": "deep_copy",
            "phase_status": "export_complete",
            "export_completed_at": datetime.now(UTC).isoformat(),
            "export_time_cutoff": export_time_cutoff,
            "source_clickhouse_url_hash": hashlib.sha256(params.url.encode()).hexdigest(),
            "chunking": {
                "target_rows": TARGET_CHUNK_ROWS,
                "minimum_window_seconds": int(MIN_CHUNK_WINDOW.total_seconds()),
                "maximum_shards": MAX_SHARD_COUNT,
            },
            "tables": table_meta,
            "fk_validation": {
                "orphaned_agent_ids": [],
                "orphaned_agent_ids_truncated": False,
                "orphaned_user_ids": [],
                "orphaned_user_ids_truncated": False,
                "validated_at": None,
            },
        }
        write_manifest(output_dir / "telemetry_manifest.json", telemetry_manifest)

        elapsed = time.monotonic() - t0
        await reporter.update(phase="ch_export", pct=100, message="Telemetry export complete")
        return TelemetryExportResult(
            output_dir=str(output_dir),
            migration_id=migration_id,
            table_results=table_meta,
            total_rows=total_rows,
            total_size_bytes=total_size,
            duration_seconds=round(elapsed, 2),
        )
    except Exception:
        if not dir_existed and output_dir.exists():
            shutil.rmtree(output_dir, ignore_errors=True)
        raise
