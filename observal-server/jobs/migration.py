# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Migration background jobs: run_migration_job and purge_migration_artifacts."""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from datetime import UTC, datetime, timedelta

from loguru import logger as optic
from sqlalchemy import select, update

import services.dynamic_settings as ds
from database import async_session
from models.migration_job import MigrationJob, MigrationOperation, MigrationScope, MigrationStatus
from observal_shared.migration import (
    ChConnParams,
    MigrationError,
    PgConnParams,
    export_ch,
    export_pg,
    import_ch,
    import_pg,
    validate_ch,
    validate_pg,
)
from observal_shared.migration.archive import _safe_tar_extract
from services.security_events import EventType, SecurityEvent, Severity, emit_security_event

MAX_MIGRATION_JOB_TIMEOUT_SECONDS = 86400


def _bounded_migration_timeout(configured_timeout: int) -> int:
    return min(max(configured_timeout, 1), MAX_MIGRATION_JOB_TIMEOUT_SECONDS)


# ── DB-backed progress reporter ──────────────────────────────────────────────


class DbProgressReporter:
    """Writes progress updates to the MigrationJob row, throttled to ~1s."""

    def __init__(self, session_factory, job_id: str):
        self._session_factory = session_factory
        self._job_id = job_id
        self._last_write: float = 0.0
        self._throttle_interval: float = 1.0

    async def update(self, *, phase: str, pct: int, message: str) -> None:
        now = time.monotonic()
        if now - self._last_write < self._throttle_interval:
            return
        self._last_write = now
        try:
            async with self._session_factory() as session:
                await session.execute(
                    update(MigrationJob)
                    .where(MigrationJob.id == self._job_id)
                    .values(
                        progress_phase=phase,
                        progress_pct=pct,
                        progress_message=message,
                        progress_updated_at=datetime.now(UTC),
                    )
                )
                await session.commit()
        except Exception as exc:
            optic.warning("progress_update_failed job_id={} error={}", self._job_id, exc)


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _resolve_pg_conn() -> PgConnParams:
    """Build PgConnParams from the server's own DATABASE_URL."""
    from config import settings

    # Convert async DSN to plain DSN for asyncpg
    dsn = settings.DATABASE_URL
    return PgConnParams(dsn=dsn)


async def _resolve_ch_conn() -> ChConnParams:
    """Build ChConnParams from dynamic settings or boot config."""
    from config import settings

    ch_url = await ds.get("migration.clickhouse_url", default=settings.CLICKHOUSE_URL)
    return ChConnParams(url=ch_url)


def _build_artifact_dir(job_id: str) -> str:
    """Return and create the artifact directory for a job."""
    import pathlib

    artifact_root_default = str(pathlib.Path.home() / ".observal" / "migration_artifacts")
    # Use sync get since we're in a sync context for path building
    # We'll resolve the setting before calling this
    return str(pathlib.Path(artifact_root_default) / job_id)


async def _get_artifact_root() -> str:
    """Get artifact root from env var, dynamic settings, or fallback."""
    import pathlib

    # Docker containers set MIGRATION_ARTIFACT_ROOT to a writable volume path.
    # Local dev falls back to ~/.observal/migration_artifacts/.
    env_root = os.environ.get("MIGRATION_ARTIFACT_ROOT")
    if env_root:
        return env_root

    default = str(pathlib.Path.home() / ".observal" / "migration_artifacts")
    return await ds.get("migration.artifact_root", default=default)


# ── Main job function ────────────────────────────────────────────────────────


async def run_migration_job(ctx: dict, job_id: str) -> None:
    """Run an export/import/validate MigrationJob to completion, updating progress."""
    optic.info("migration_job_started job_id={}", job_id)

    import uuid

    uid = uuid.UUID(job_id)

    # Load the job row
    async with async_session() as session:
        job = (await session.execute(select(MigrationJob).where(MigrationJob.id == uid))).scalar_one_or_none()
        if not job:
            optic.error("migration_job_not_found job_id={}", job_id)
            return

        # Set status=running + started_at
        job.status = MigrationStatus.running
        job.started_at = datetime.now(UTC)
        job.progress_phase = "initializing"
        job.progress_message = "Job started"
        await session.commit()

        operation_type = job.operation_type
        data_scope = job.data_scope
        artifact_dir = job.artifact_dir

    # Create artifact dir
    artifact_root = await _get_artifact_root()
    if not artifact_dir:
        artifact_dir = os.path.join(artifact_root, job_id)

    # Track whether we created the artifact dir (for cleanup on failure)
    artifact_dir_created_by_us = not os.path.isdir(artifact_dir)
    os.makedirs(artifact_dir, mode=0o700, exist_ok=True)

    # Build progress reporter
    reporter = DbProgressReporter(async_session, job_id)

    # Keep the inner timeout aligned with arq's function-specific ceiling.
    configured_timeout = await ds.get_int(
        "migration.job_timeout_seconds",
        default=MAX_MIGRATION_JOB_TIMEOUT_SECONDS,
    )
    timeout_seconds = _bounded_migration_timeout(configured_timeout)

    # Resolve connections
    pg_conn = await _resolve_pg_conn()
    ch_conn = await _resolve_ch_conn()

    result_json = None
    artifacts_json = None
    schema_version = None
    error_message = None
    final_status = MigrationStatus.completed

    try:
        async with asyncio.timeout(timeout_seconds):
            if operation_type == MigrationOperation.export:
                result_json, artifacts_json, schema_version = await _run_export(
                    data_scope, pg_conn, ch_conn, artifact_dir, reporter
                )
            elif operation_type == MigrationOperation.import_:
                result_json, artifacts_json, schema_version = await _run_import(
                    data_scope, pg_conn, ch_conn, artifact_dir, reporter
                )
            elif operation_type == MigrationOperation.validate:
                result_json, artifacts_json, schema_version = await _run_validate(
                    data_scope, pg_conn, ch_conn, artifact_dir, reporter
                )
            else:
                raise MigrationError(f"Unknown operation type: {operation_type}")

    except MigrationError as exc:
        optic.error("migration_job_failed job_id={} error={}", job_id, str(exc))
        error_message = str(exc)
        final_status = MigrationStatus.failed
    except TimeoutError:
        optic.error("migration_job_timeout job_id={} timeout={}s", job_id, timeout_seconds)
        error_message = f"Job timed out after {timeout_seconds} seconds"
        final_status = MigrationStatus.failed
    except Exception as exc:
        optic.error("migration_job_unexpected_error job_id={} error={}", job_id, str(exc))
        error_message = f"Unexpected error: {type(exc).__name__}: {exc}"
        final_status = MigrationStatus.failed

    # Clean up artifact dir on failure for export jobs (no user-uploaded data to preserve)
    if (
        final_status == MigrationStatus.failed
        and operation_type == MigrationOperation.export
        and artifact_dir_created_by_us
        and os.path.isdir(artifact_dir)
    ):
        shutil.rmtree(artifact_dir, ignore_errors=True)
        artifact_dir = None

    # Write terminal state
    async with async_session() as session:
        await session.execute(
            update(MigrationJob)
            .where(MigrationJob.id == uid)
            .values(
                status=final_status,
                finished_at=datetime.now(UTC),
                result_json=result_json,
                artifacts_json=artifacts_json,
                artifact_dir=artifact_dir,
                schema_version=schema_version,
                error_message=error_message,
                progress_phase="completed" if final_status == MigrationStatus.completed else "failed",
                progress_pct=100 if final_status == MigrationStatus.completed else 0,
                progress_message="Completed" if final_status == MigrationStatus.completed else error_message,
            )
        )
        await session.commit()

    # Emit terminal audit event
    detail = f"Migration {operation_type.value} {final_status.value} (scope={data_scope.value})"
    if result_json and "total_rows" in result_json:
        detail += f" total_rows={result_json['total_rows']}"

    await emit_security_event(
        SecurityEvent(
            event_type=EventType.SETTING_CHANGED,
            severity=Severity.WARNING if final_status == MigrationStatus.failed else Severity.INFO,
            outcome="failure" if final_status == MigrationStatus.failed else "success",
            actor_id="system",
            target_id=job_id,
            target_type="migration_job",
            detail=detail,
        )
    )

    optic.info("migration_job_finished job_id={} status={}", job_id, final_status.value)


# ── Operation dispatchers ────────────────────────────────────────────────────


async def _run_export(
    data_scope: MigrationScope,
    pg_conn: PgConnParams,
    ch_conn: ChConnParams,
    artifact_dir: str,
    reporter: DbProgressReporter,
) -> tuple[dict | None, list | None, str | None]:
    """Dispatch export operations based on scope."""
    from pathlib import Path

    from observal_shared.migration.archive import _sha256_file

    result: dict = {}
    artifacts: list = []
    schema_version = None

    if data_scope in (MigrationScope.postgres, MigrationScope.both):
        output_path = Path(artifact_dir) / "pg_export.tar.gz"
        export_result = await export_pg(
            pg_conn,
            output_path,
            reporter,
        )
        result["table_counts"] = export_result.table_counts
        result["total_rows"] = export_result.total_rows
        archive_size = output_path.stat().st_size if output_path.exists() else None
        result["archive_size_bytes"] = archive_size

        if output_path.exists():
            archive_hash = _sha256_file(output_path)
            artifacts.append(
                {"name": output_path.name, "size_bytes": archive_size, "sha256": archive_hash, "kind": "archive"}
            )

    if data_scope in (MigrationScope.clickhouse, MigrationScope.both):
        # Phase 2 CH export requires a Phase 1 manifest
        manifest_path = Path(artifact_dir) / "pg_export.manifest.json"
        if not manifest_path.exists():
            # Fall back to looking in the archive staging area
            manifest_path = Path(artifact_dir) / "migration_manifest.json"
        ch_output_dir = Path(artifact_dir) / "telemetry"
        ch_result = await export_ch(
            ch_conn,
            manifest_path,
            ch_output_dir,
            reporter,
        )
        result["telemetry_size_bytes"] = ch_result.total_size_bytes

        # Pack all telemetry Parquet files + manifest into a single tar.gz
        telemetry_archive_path = Path(artifact_dir) / "telemetry_export.tar.gz"
        import tarfile as _tarfile

        with _tarfile.open(telemetry_archive_path, "w:gz") as tar:
            telemetry_manifest_path = ch_output_dir / "telemetry_manifest.json"
            if telemetry_manifest_path.exists():
                tar.add(str(telemetry_manifest_path), arcname="telemetry_manifest.json")
            for _table_name, table_info in ch_result.table_results.items():
                for filename in table_info.get("files", []):
                    filepath = ch_output_dir / filename
                    if filepath.exists():
                        tar.add(str(filepath), arcname=filename)

        if telemetry_archive_path.exists() and telemetry_archive_path.stat().st_size > 0:
            archive_hash = _sha256_file(telemetry_archive_path)
            artifacts.append(
                {
                    "name": telemetry_archive_path.name,
                    "size_bytes": telemetry_archive_path.stat().st_size,
                    "sha256": archive_hash,
                    "kind": "archive",
                }
            )

    result.setdefault("telemetry_size_bytes", None)
    result.setdefault("archive_size_bytes", None)
    result.setdefault("schema_version_diff", None)

    return result, artifacts or None, schema_version


async def _run_import(
    data_scope: MigrationScope,
    pg_conn: PgConnParams,
    ch_conn: ChConnParams,
    artifact_dir: str,
    reporter: DbProgressReporter,
) -> tuple[dict | None, list | None, str | None]:
    """Dispatch import operations based on scope."""
    from pathlib import Path

    result: dict = {"rows_inserted": {}, "rows_skipped": {}, "tables_skipped": []}
    artifacts: list = []
    schema_version = None
    artifact_path = Path(artifact_dir)

    if data_scope in (MigrationScope.postgres, MigrationScope.both):
        # Find the PG archive file (exclude telemetry archives)
        archive_candidates = [
            f
            for f in (list(artifact_path.glob("*.tar.gz")) + list(artifact_path.glob("*.tgz")))
            if not f.name.startswith("telemetry")
        ]
        if not archive_candidates:
            raise MigrationError("No PostgreSQL .tar.gz archive found in artifact directory")
        archive_file = archive_candidates[0]

        import_result = await import_pg(
            pg_conn,
            archive_file,
            reporter,
        )
        result["rows_inserted"] = import_result.rows_inserted
        result["rows_skipped"] = import_result.rows_skipped
        result["tables_skipped"] = []
        if import_result.warnings:
            result["warnings"] = import_result.warnings
        schema_version = None

    if data_scope in (MigrationScope.clickhouse, MigrationScope.both):
        # Extract telemetry archive if present (from the new tar.gz format)
        import tarfile as _tarfile

        telemetry_archives = [
            f for f in artifact_path.iterdir() if f.name.startswith("telemetry") and f.suffix == ".gz"
        ]
        if telemetry_archives and not (artifact_path / "telemetry").is_dir():
            extract_dir = artifact_path / "telemetry"
            extract_dir.mkdir(exist_ok=True)
            with _tarfile.open(telemetry_archives[0], "r:gz") as tar:
                _safe_tar_extract(tar, extract_dir)

        # Telemetry files may be in a subdirectory or the root
        telemetry_dir = artifact_path / "telemetry" if (artifact_path / "telemetry").is_dir() else artifact_path

        ch_result = await import_ch(
            ch_conn,
            telemetry_dir,
            reporter,
        )
        # Merge CH import results
        for table, count in (ch_result.rows_imported or {}).items():
            result["rows_inserted"][table] = result["rows_inserted"].get(table, 0) + count
        result["tables_skipped"].extend(ch_result.tables_skipped)

    result["total_rows"] = sum(result["rows_inserted"].values()) + sum(result["rows_skipped"].values())
    result.setdefault("schema_version_diff", None)

    return result, artifacts or None, schema_version


async def _run_validate(
    data_scope: MigrationScope,
    pg_conn: PgConnParams,
    ch_conn: ChConnParams,
    artifact_dir: str,
    reporter: DbProgressReporter,
) -> tuple[dict | None, list | None, str | None]:
    """Dispatch validation operations based on scope."""
    from pathlib import Path

    result: dict = {
        "checksums_valid": True,
        "checksum_details": {},
        "row_count_comparison": None,
        "orphaned_fk_refs": None,
        "schema_version_diff": None,
    }
    schema_version = None
    artifact_path = Path(artifact_dir)

    if data_scope in (MigrationScope.postgres, MigrationScope.both):
        # Find the PG archive file (exclude telemetry archives)
        archive_candidates = [
            f
            for f in (list(artifact_path.glob("*.tar.gz")) + list(artifact_path.glob("*.tgz")))
            if not f.name.startswith("telemetry")
        ]
        if not archive_candidates:
            raise MigrationError("No PostgreSQL .tar.gz archive found in artifact directory for validation")
        archive_file = archive_candidates[0]

        val_result = await validate_pg(
            pg_conn,
            archive_file,
            reporter,
        )
        result["checksums_valid"] = result["checksums_valid"] and val_result.archive_valid
        result["checksum_details"] = {cr.table_name: cr.passed for cr in val_result.checksum_results}
        if val_result.cross_db_results:
            result["row_count_comparison"] = {
                table: list(counts) for table, counts in val_result.cross_db_results.items()
            }

    if data_scope in (MigrationScope.clickhouse, MigrationScope.both):
        # Extract telemetry archive if present (from the new tar.gz format)
        import tarfile as _tarfile

        telemetry_archives = [
            f for f in artifact_path.iterdir() if f.name.startswith("telemetry") and f.suffix == ".gz"
        ]
        if telemetry_archives and not (artifact_path / "telemetry").is_dir():
            extract_dir = artifact_path / "telemetry"
            extract_dir.mkdir(exist_ok=True)
            with _tarfile.open(telemetry_archives[0], "r:gz") as tar:
                _safe_tar_extract(tar, extract_dir)

        # Telemetry files may be in a subdirectory or the root
        telemetry_dir = artifact_path / "telemetry" if (artifact_path / "telemetry").is_dir() else artifact_path

        ch_val = await validate_ch(
            ch_conn,
            pg_conn,
            telemetry_dir,
            reporter,
        )
        result["checksums_valid"] = result["checksums_valid"] and ch_val.checksums_valid
        result["checksum_details"].update(ch_val.checksum_results or {})
        if ch_val.row_count_results:
            comparisons = result["row_count_comparison"] or {}
            comparisons.update({table: list(counts) for table, counts in ch_val.row_count_results.items()})
            result["row_count_comparison"] = comparisons
        result["orphaned_fk_refs"] = ch_val.fk_results

    return result, None, schema_version


# ── Artifact purge cron ──────────────────────────────────────────────────────


async def purge_migration_artifacts(ctx: dict) -> None:
    """Cron job: delete artifact directories older than Artifact_TTL."""
    optic.debug("purge_migration_artifacts")

    ttl_hours = await ds.get_int("migration.artifact_ttl_hours", default=24)
    cutoff = datetime.now(UTC) - timedelta(hours=ttl_hours)

    async with async_session() as session:
        stmt = select(MigrationJob).where(
            MigrationJob.finished_at.isnot(None),
            MigrationJob.finished_at < cutoff,
            MigrationJob.artifact_dir.isnot(None),
        )
        jobs = (await session.execute(stmt)).scalars().all()

        purged = 0
        for job in jobs:
            if job.artifact_dir and os.path.isdir(job.artifact_dir):
                try:
                    shutil.rmtree(job.artifact_dir)
                    optic.info("purged_migration_artifacts job_id={} dir={}", job.id, job.artifact_dir)
                except Exception as exc:
                    optic.warning("purge_failed job_id={} error={}", job.id, exc)
                    # Only clear the reference if the directory was fully removed
                    if os.path.isdir(job.artifact_dir):
                        continue

            job.artifact_dir = None
            job.artifacts_json = None
            purged += 1

        if purged > 0:
            await session.commit()
            optic.info("purge_migration_artifacts_complete count={}", purged)
