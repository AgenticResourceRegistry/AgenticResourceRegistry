# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Validation helpers for chunked ClickHouse migration manifests."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from observal_shared.migration.constants import CLICKHOUSE_TABLES, TELEMETRY_MANIFEST_VERSION
from observal_shared.migration.exceptions import MigrationError

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CHUNK_BOUNDARY_RE = re.compile(r"^\d{8}T\d{12}$")


def _chunk_id_matches_metadata(
    chunk_id: str,
    table_name: str,
    range_start: datetime,
    range_end: datetime,
    bucket: int,
    shard_count: int,
) -> bool:
    """Match canonical IDs and early v2 IDs that retained sub-millisecond digits."""
    try:
        id_table, id_start, id_end, id_bucket, id_shards = chunk_id.rsplit(":", 4)
    except ValueError:
        return False
    if not _CHUNK_BOUNDARY_RE.fullmatch(id_start) or not _CHUNK_BOUNDARY_RE.fullmatch(id_end):
        return False
    expected_start = range_start.strftime("%Y%m%dT%H%M%S%f")
    expected_end = range_end.strftime("%Y%m%dT%H%M%S%f")
    return (
        id_table == table_name
        and id_start[:18] == expected_start[:18]
        and id_end[:18] == expected_end[:18]
        and id_bucket == str(bucket)
        and id_shards == str(shard_count)
    )


def _legacy_month_chunks(manifest: dict, input_dir: Path) -> dict[str, list[dict]]:
    """Normalize pre-2.0 monthly manifests into bounded import chunks."""
    import pyarrow.parquet as pq

    if manifest.get("phase") != "deep_copy" or manifest.get("phase_status") != "export_complete":
        raise MigrationError("Unsupported legacy telemetry manifest.")
    if not manifest.get("migration_id"):
        raise MigrationError("Telemetry manifest is missing migration_id.")
    cutoff = manifest.get("export_time_cutoff")
    if not isinstance(cutoff, str):
        raise MigrationError("Telemetry manifest is missing export_time_cutoff.")
    try:
        datetime.fromisoformat(cutoff.replace(" ", "T").replace("Z", "+00:00"))
    except ValueError as exc:
        raise MigrationError("Telemetry manifest has an invalid export_time_cutoff.") from exc

    tables = manifest.get("tables")
    if not isinstance(tables, dict):
        raise MigrationError("Telemetry manifest tables must be an object.")
    known = {cfg["name"] for cfg in CLICKHOUSE_TABLES}
    unknown = sorted(set(tables) - known)
    missing = sorted(known - set(tables))
    if unknown:
        raise MigrationError(f"Telemetry manifest contains unknown tables: {', '.join(unknown)}")
    if missing:
        raise MigrationError(f"Telemetry manifest is missing tables: {', '.join(missing)}")

    result: dict[str, list[dict]] = {}
    seen_filenames: set[str] = set()
    for table_name in known:
        table_info = tables[table_name]
        if not isinstance(table_info, dict):
            raise MigrationError(f"Telemetry manifest has invalid metadata for {table_name}.")
        files = table_info.get("files", [])
        checksums = table_info.get("checksum", {})
        if not isinstance(files, list) or not isinstance(checksums, dict):
            raise MigrationError(f"Telemetry manifest has invalid metadata for {table_name}.")

        chunks: list[dict] = []
        row_count = 0
        filename_pattern = re.compile(rf"^{re.escape(table_name)}_(\d{{4}})-(\d{{2}})\.parquet$")
        for filename in files:
            if not isinstance(filename, str) or Path(filename).name != filename or filename in seen_filenames:
                raise MigrationError(f"Telemetry manifest has an unsafe or duplicate filename for {table_name}.")
            match = filename_pattern.fullmatch(filename)
            digest = checksums.get(filename)
            if not match or not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
                raise MigrationError(f"Telemetry manifest has invalid legacy chunk metadata for {filename}.")
            year, month = (int(value) for value in match.groups())
            try:
                range_start = datetime(year, month, 1)
            except ValueError as exc:
                raise MigrationError(f"Telemetry manifest has an invalid month for {filename}.") from exc
            range_end = (
                range_start.replace(year=year + 1, month=1) if month == 12 else range_start.replace(month=month + 1)
            )
            filepath = input_dir / filename
            if not filepath.is_file():
                raise MigrationError(f"Telemetry chunk file is missing: {filename}")
            try:
                file_rows = pq.read_metadata(filepath).num_rows
            except Exception as exc:
                raise MigrationError(f"Telemetry chunk is not valid Parquet: {filename}") from exc
            row_count += file_rows
            seen_filenames.add(filename)
            chunks.append(
                {
                    "chunk_id": f"legacy:{table_name}:{year:04d}{month:02d}",
                    "range_start": range_start.isoformat(sep=" "),
                    "range_end": range_end.isoformat(sep=" "),
                    "bucket": 0,
                    "shard_count": 1,
                    "row_count": file_rows,
                    "file": filename,
                    "size_bytes": filepath.stat().st_size,
                    "sha256": digest,
                    "legacy": True,
                }
            )
        if set(checksums) != set(files):
            raise MigrationError(f"Telemetry manifest checksum list is incomplete for {table_name}.")
        if table_info.get("row_count", 0) != row_count:
            raise MigrationError(f"Telemetry manifest row count disagrees for {table_name}.")
        result[table_name] = chunks
    return result


def validate_telemetry_manifest(manifest: dict, input_dir: Path | None = None) -> dict[str, list[dict]]:
    """Validate manifest structure and return chunks grouped by known table."""
    if manifest.get("schema_version") is None and input_dir is not None:
        return _legacy_month_chunks(manifest, input_dir)
    if manifest.get("schema_version") != TELEMETRY_MANIFEST_VERSION:
        raise MigrationError(
            f"Unsupported telemetry manifest schema {manifest.get('schema_version')!r}; "
            "create a new telemetry export with this Observal version."
        )
    if not manifest.get("migration_id"):
        raise MigrationError("Telemetry manifest is missing migration_id.")
    if manifest.get("phase_status") != "export_complete":
        raise MigrationError("Telemetry manifest does not describe a completed export.")

    tables = manifest.get("tables")
    if not isinstance(tables, dict):
        raise MigrationError("Telemetry manifest tables must be an object.")

    known = {cfg["name"] for cfg in CLICKHOUSE_TABLES}
    unknown = sorted(set(tables) - known)
    if unknown:
        raise MigrationError(f"Telemetry manifest contains unknown tables: {', '.join(unknown)}")
    missing = sorted(known - set(tables))
    if missing:
        raise MigrationError(f"Telemetry manifest is missing tables: {', '.join(missing)}")
    cutoff = manifest.get("export_time_cutoff")
    if not isinstance(cutoff, str):
        raise MigrationError("Telemetry manifest is missing export_time_cutoff.")
    try:
        datetime.fromisoformat(cutoff.replace(" ", "T").replace("Z", "+00:00"))
    except ValueError as exc:
        raise MigrationError("Telemetry manifest has an invalid export_time_cutoff.") from exc

    seen_chunk_ids: set[str] = set()
    seen_filenames: set[str] = set()
    result: dict[str, list[dict]] = {}

    for table_name in known:
        table_info = tables.get(table_name, {})
        chunks = table_info.get("chunks", [])
        files = table_info.get("files", [])
        checksums = table_info.get("checksum", {})
        if not isinstance(chunks, list) or not isinstance(files, list) or not isinstance(checksums, dict):
            raise MigrationError(f"Telemetry manifest has invalid metadata for {table_name}.")

        chunk_files: list[str] = []
        chunk_rows = 0
        for chunk in chunks:
            if not isinstance(chunk, dict):
                raise MigrationError(f"Telemetry manifest has an invalid chunk for {table_name}.")
            chunk_id = chunk.get("chunk_id")
            if not isinstance(chunk_id, str) or not chunk_id:
                raise MigrationError(f"Telemetry chunk for {table_name} is missing chunk_id.")
            if chunk_id in seen_chunk_ids:
                raise MigrationError(f"Telemetry manifest contains duplicate chunk ID: {chunk_id}")
            seen_chunk_ids.add(chunk_id)

            row_count = chunk.get("row_count")
            if not isinstance(row_count, int) or isinstance(row_count, bool) or row_count < 0:
                raise MigrationError(f"Telemetry chunk {chunk_id} has an invalid row count.")
            chunk_rows += row_count

            bucket = chunk.get("bucket")
            shard_count = chunk.get("shard_count")
            if (
                not isinstance(bucket, int)
                or isinstance(bucket, bool)
                or not isinstance(shard_count, int)
                or isinstance(shard_count, bool)
                or shard_count < 1
                or shard_count & (shard_count - 1)
                or not 0 <= bucket < shard_count
            ):
                raise MigrationError(f"Telemetry chunk {chunk_id} has invalid shard metadata.")
            try:
                range_start = datetime.fromisoformat(str(chunk["range_start"]).replace(" ", "T"))
                range_end = datetime.fromisoformat(str(chunk["range_end"]).replace(" ", "T"))
            except (KeyError, ValueError) as exc:
                raise MigrationError(f"Telemetry chunk {chunk_id} has an invalid time range.") from exc
            if range_start >= range_end:
                raise MigrationError(f"Telemetry chunk {chunk_id} has an empty or reversed time range.")
            if not _chunk_id_matches_metadata(chunk_id, table_name, range_start, range_end, bucket, shard_count):
                raise MigrationError(f"Telemetry chunk ID does not match its boundaries: {chunk_id}")

            filename = chunk.get("file")
            digest = chunk.get("sha256")
            size_bytes = chunk.get("size_bytes")
            if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 0:
                raise MigrationError(f"Telemetry chunk {chunk_id} has an invalid file size.")

            if row_count == 0:
                if filename is not None or digest is not None or size_bytes != 0:
                    raise MigrationError(f"Empty telemetry chunk {chunk_id} must not reference a file.")
                continue

            if size_bytes == 0:
                raise MigrationError(f"Telemetry chunk {chunk_id} has an invalid empty file.")
            if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith(".parquet"):
                raise MigrationError(f"Telemetry chunk {chunk_id} has an unsafe filename.")
            if filename in seen_filenames:
                raise MigrationError(f"Telemetry manifest contains duplicate filename: {filename}")
            seen_filenames.add(filename)
            chunk_files.append(filename)
            if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
                raise MigrationError(f"Telemetry chunk {chunk_id} has an invalid SHA-256 digest.")
            if checksums.get(filename) != digest:
                raise MigrationError(f"Telemetry chunk checksum metadata disagrees for {filename}.")

        if files != chunk_files:
            raise MigrationError(f"Telemetry manifest file list is incomplete or out of order for {table_name}.")
        if set(checksums) != set(chunk_files):
            raise MigrationError(f"Telemetry manifest checksum list is incomplete for {table_name}.")
        if table_info.get("row_count", 0) != chunk_rows:
            raise MigrationError(f"Telemetry manifest row count disagrees for {table_name}.")
        result[table_name] = chunks

    return result
