# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Focused coverage for migration background job orchestration."""

from __future__ import annotations

import asyncio
import io
import sys
import tarfile
import uuid
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import jobs.migration as migration
from models.migration_job import MigrationOperation, MigrationScope, MigrationStatus
from observal_shared.migration import ChConnParams, MigrationError, PgConnParams
from observal_shared.migration.results import (
    ChecksumResult,
    ExportResult,
    ImportResult,
    TelemetryExportResult,
    TelemetryImportResult,
    TelemetryValidationResult,
    ValidationResult,
)
from services.security_events import Severity

NOW = datetime(2026, 4, 5, 6, 7, 8, tzinfo=UTC)


class FrozenDateTime(datetime):
    """Return one deterministic instant from ``now``."""

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return NOW.replace(tzinfo=None)
        return NOW


class Result:
    """Small SQLAlchemy result stand-in for scalar queries."""

    def __init__(self, *, one=None, many=()):
        self.one = one
        self.many = list(many)

    def scalar_one_or_none(self):
        return self.one

    def scalars(self):
        return self

    def all(self):
        return self.many


class Session:
    """Async session stand-in that records statements and lifecycle calls."""

    def __init__(self, *results, execute_error: BaseException | None = None, commit_error: BaseException | None = None):
        self.results = deque(results)
        self.execute_error = execute_error
        self.statements = []
        self.commit = AsyncMock(side_effect=commit_error)
        self.entered = 0
        self.exited = 0

    async def __aenter__(self):
        self.entered += 1
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.exited += 1
        return False

    async def execute(self, statement):
        self.statements.append(statement)
        if self.execute_error is not None:
            raise self.execute_error
        return self.results.popleft() if self.results else Result()


class SessionFactory:
    """Return prepared sessions in invocation order."""

    def __init__(self, *sessions: Session):
        self.sessions = deque(sessions)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if not self.sessions:
            raise AssertionError("Unexpected database session")
        return self.sessions.popleft()


class TimeoutContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


def make_job(
    *,
    operation=MigrationOperation.export,
    scope=MigrationScope.postgres,
    artifact_dir: str | None = None,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        operation_type=operation,
        data_scope=scope,
        status=MigrationStatus.queued,
        started_at=None,
        finished_at=None,
        artifact_dir=artifact_dir,
        progress_phase="queued",
        progress_pct=0,
        progress_message="Queued",
        error_message=None,
    )


def statement_values(session: Session) -> dict:
    return session.statements[-1].compile().params


def install_job_boundaries(monkeypatch, factory: SessionFactory, artifact_root: Path):
    """Replace every external boundary used by ``run_migration_job``."""
    pg_conn = PgConnParams(dsn="postgresql://source/db")
    ch_conn = ChConnParams(url="clickhouse://source/observal")
    timeout = MagicMock(side_effect=lambda seconds: TimeoutContext())
    export = AsyncMock()
    import_ = AsyncMock()
    validate = AsyncMock()
    emit = AsyncMock()
    pg_resolver = AsyncMock(return_value=pg_conn)
    ch_resolver = AsyncMock(return_value=ch_conn)
    get_timeout = AsyncMock(return_value=17)

    monkeypatch.setattr(migration, "async_session", factory)
    monkeypatch.setattr(migration, "datetime", FrozenDateTime)
    monkeypatch.setattr(migration, "_get_artifact_root", AsyncMock(return_value=str(artifact_root)))
    monkeypatch.setattr(migration.ds, "get_int", get_timeout)
    monkeypatch.setattr(migration, "_resolve_pg_conn", pg_resolver)
    monkeypatch.setattr(migration, "_resolve_ch_conn", ch_resolver)
    monkeypatch.setattr(migration.asyncio, "timeout", timeout)
    monkeypatch.setattr(migration, "_run_export", export)
    monkeypatch.setattr(migration, "_run_import", import_)
    monkeypatch.setattr(migration, "_run_validate", validate)
    monkeypatch.setattr(migration, "emit_security_event", emit)

    return SimpleNamespace(
        pg_conn=pg_conn,
        ch_conn=ch_conn,
        timeout=timeout,
        export=export,
        import_=import_,
        validate=validate,
        emit=emit,
        pg_resolver=pg_resolver,
        ch_resolver=ch_resolver,
        get_timeout=get_timeout,
    )


def test_migration_timeout_defaults_and_clamps_to_worker_ceiling():
    assert migration._bounded_migration_timeout(0) == 1
    assert migration._bounded_migration_timeout(3600) == 3600
    assert migration._bounded_migration_timeout(172800) == migration.MAX_MIGRATION_JOB_TIMEOUT_SECONDS


def write_tar(path: Path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))


@pytest.mark.asyncio
async def test_progress_reporter_writes_and_throttles_at_one_second(monkeypatch):
    first = Session()
    second = Session()
    factory = SessionFactory(first, second)
    clock = MagicMock(side_effect=[10.0, 10.5, 11.0])
    monkeypatch.setattr(migration, "time", SimpleNamespace(monotonic=clock))
    monkeypatch.setattr(migration, "datetime", FrozenDateTime)
    reporter = migration.DbProgressReporter(factory, "job-1")

    await reporter.update(phase="pg_export", pct=10, message="Starting")
    await reporter.update(phase="pg_export", pct=20, message="Throttled")
    await reporter.update(phase="pg_export", pct=30, message="Writing")

    assert factory.calls == 2
    assert first.entered == first.exited == second.entered == second.exited == 1
    first.commit.assert_awaited_once_with()
    second.commit.assert_awaited_once_with()
    first_values = statement_values(first)
    second_values = statement_values(second)
    assert first_values["progress_phase"] == "pg_export"
    assert first_values["progress_pct"] == 10
    assert first_values["progress_message"] == "Starting"
    assert first_values["progress_updated_at"] == NOW
    assert second_values["progress_pct"] == 30
    assert second_values["progress_message"] == "Writing"
    assert reporter._last_write == 11.0


@pytest.mark.asyncio
async def test_progress_reporter_swallows_database_failure_and_still_throttles(monkeypatch):
    error = RuntimeError("database unavailable")
    factory = MagicMock(side_effect=error)
    warning = MagicMock()
    monkeypatch.setattr(migration, "time", SimpleNamespace(monotonic=MagicMock(side_effect=[10.0, 10.2])))
    monkeypatch.setattr(migration.optic, "warning", warning)
    reporter = migration.DbProgressReporter(factory, "job-2")

    await reporter.update(phase="pg_import", pct=5, message="Connecting")
    await reporter.update(phase="pg_import", pct=6, message="Waiting")

    factory.assert_called_once_with()
    warning.assert_called_once_with("progress_update_failed job_id={} error={}", "job-2", error)
    assert reporter._last_write == 10.0


@pytest.mark.asyncio
async def test_connection_and_artifact_helpers_honor_configuration(monkeypatch):
    fake_config = ModuleType("config")
    fake_config.settings = SimpleNamespace(
        DATABASE_URL="postgresql+asyncpg://app:secret@db/observal",
        CLICKHOUSE_URL="clickhouse://boot/observal",
    )
    dynamic_get = AsyncMock(return_value="clickhouse://dynamic/observal")
    monkeypatch.setitem(sys.modules, "config", fake_config)
    monkeypatch.setattr(migration.ds, "get", dynamic_get)

    pg_conn = await migration._resolve_pg_conn()
    ch_conn = await migration._resolve_ch_conn()

    assert pg_conn.dsn == fake_config.settings.DATABASE_URL
    assert ch_conn.url == "clickhouse://dynamic/observal"
    dynamic_get.assert_awaited_once_with("migration.clickhouse_url", default="clickhouse://boot/observal")

    artifact_get = AsyncMock(return_value="/settings/artifacts")
    monkeypatch.setattr(migration.ds, "get", artifact_get)
    monkeypatch.setenv("MIGRATION_ARTIFACT_ROOT", "/environment/artifacts")
    assert await migration._get_artifact_root() == "/environment/artifacts"
    artifact_get.assert_not_awaited()

    monkeypatch.delenv("MIGRATION_ARTIFACT_ROOT")
    with patch("pathlib.Path.home", return_value=Path("/users/tester")):
        assert await migration._get_artifact_root() == "/settings/artifacts"
        assert migration._build_artifact_dir("job-3") == "/users/tester/.observal/migration_artifacts/job-3"
    artifact_get.assert_awaited_once_with(
        "migration.artifact_root",
        default="/users/tester/.observal/migration_artifacts",
    )


@pytest.mark.asyncio
async def test_missing_job_closes_lookup_session_and_stops(monkeypatch, tmp_path):
    lookup = Session(Result(one=None))
    factory = SessionFactory(lookup)
    monkeypatch.setattr(migration, "async_session", factory)
    get_root = AsyncMock(return_value=str(tmp_path))
    monkeypatch.setattr(migration, "_get_artifact_root", get_root)

    await migration.run_migration_job({}, str(uuid.uuid4()))

    assert factory.calls == 1
    assert lookup.entered == lookup.exited == 1
    lookup.commit.assert_not_awaited()
    get_root.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_job_id_fails_before_database_lookup(monkeypatch):
    factory = SessionFactory()
    monkeypatch.setattr(migration, "async_session", factory)

    with pytest.raises(ValueError):
        await migration.run_migration_job({}, "not-a-uuid")

    assert factory.calls == 0


@pytest.mark.parametrize(
    ("operation", "handler_name"),
    [
        (MigrationOperation.export, "export"),
        (MigrationOperation.import_, "import_"),
        (MigrationOperation.validate, "validate"),
    ],
)
@pytest.mark.asyncio
async def test_job_dispatches_operation_and_persists_success(
    monkeypatch,
    tmp_path,
    operation,
    handler_name,
):
    artifact_dir = None
    if operation != MigrationOperation.export:
        upload_dir = tmp_path / "uploaded"
        upload_dir.mkdir()
        artifact_dir = str(upload_dir)
    job = make_job(operation=operation, scope=MigrationScope.both, artifact_dir=artifact_dir)
    lookup = Session(Result(one=job))
    terminal = Session()
    factory = SessionFactory(lookup, terminal)
    boundaries = install_job_boundaries(monkeypatch, factory, tmp_path)
    handler = getattr(boundaries, handler_name)
    handler.return_value = (
        {"total_rows": 7, "operation": operation.value},
        [{"name": "artifact.tar.gz", "size_bytes": 12, "sha256": "abc", "kind": "archive"}],
        "schema-7",
    )
    queue_client = MagicMock()
    object_storage = MagicMock()
    ctx = {"redis": queue_client, "object_storage": object_storage}

    await migration.run_migration_job(ctx, str(job.id))

    expected_dir = artifact_dir or str(tmp_path / str(job.id))
    assert Path(expected_dir).is_dir()
    assert job.status == MigrationStatus.running
    assert job.started_at == NOW
    assert job.progress_phase == "initializing"
    assert job.progress_message == "Job started"
    lookup.commit.assert_awaited_once_with()
    assert lookup.entered == lookup.exited == terminal.entered == terminal.exited == 1
    terminal.commit.assert_awaited_once_with()

    values = statement_values(terminal)
    assert values["status"] == MigrationStatus.completed
    assert values["finished_at"] == NOW
    assert values["result_json"] == {"total_rows": 7, "operation": operation.value}
    assert values["artifacts_json"][0]["name"] == "artifact.tar.gz"
    assert values["artifact_dir"] == expected_dir
    assert values["schema_version"] == "schema-7"
    assert values["error_message"] is None
    assert values["progress_phase"] == "completed"
    assert values["progress_pct"] == 100
    assert values["progress_message"] == "Completed"

    reporter = handler.await_args.args[-1]
    assert isinstance(reporter, migration.DbProgressReporter)
    assert reporter._job_id == str(job.id)
    handler.assert_awaited_once_with(
        MigrationScope.both,
        boundaries.pg_conn,
        boundaries.ch_conn,
        expected_dir,
        reporter,
    )
    for other_name in {"export", "import_", "validate"} - {handler_name}:
        getattr(boundaries, other_name).assert_not_awaited()
    boundaries.get_timeout.assert_awaited_once_with(
        "migration.job_timeout_seconds",
        default=migration.MAX_MIGRATION_JOB_TIMEOUT_SECONDS,
    )
    boundaries.timeout.assert_called_once_with(17)
    boundaries.pg_resolver.assert_awaited_once_with()
    boundaries.ch_resolver.assert_awaited_once_with()

    boundaries.emit.assert_awaited_once()
    event = boundaries.emit.await_args.args[0]
    assert event.outcome == "success"
    assert event.severity == Severity.INFO
    assert event.target_id == str(job.id)
    assert event.target_type == "migration_job"
    assert event.detail == f"Migration {operation.value} completed (scope=both) total_rows=7"
    assert queue_client.mock_calls == []
    assert object_storage.mock_calls == []


@pytest.mark.parametrize(
    ("error", "expected_message"),
    [
        (MigrationError("migration broke"), "migration broke"),
        (TimeoutError(), "Job timed out after 17 seconds"),
        (RuntimeError("disk full"), "Unexpected error: RuntimeError: disk full"),
    ],
)
@pytest.mark.asyncio
async def test_export_failures_are_persisted_audited_and_cleaned(
    monkeypatch,
    tmp_path,
    error,
    expected_message,
):
    job = make_job()
    lookup = Session(Result(one=job))
    terminal = Session()
    factory = SessionFactory(lookup, terminal)
    boundaries = install_job_boundaries(monkeypatch, factory, tmp_path)
    boundaries.export.side_effect = error

    await migration.run_migration_job({}, str(job.id))

    created_dir = tmp_path / str(job.id)
    assert not created_dir.exists()
    values = statement_values(terminal)
    assert values["status"] == MigrationStatus.failed
    assert values["finished_at"] == NOW
    assert values["result_json"] is None
    assert values["artifacts_json"] is None
    assert values["artifact_dir"] is None
    assert values["schema_version"] is None
    assert values["error_message"] == expected_message
    assert values["progress_phase"] == "failed"
    assert values["progress_pct"] == 0
    assert values["progress_message"] == expected_message
    lookup.commit.assert_awaited_once_with()
    terminal.commit.assert_awaited_once_with()

    event = boundaries.emit.await_args.args[0]
    assert event.outcome == "failure"
    assert event.severity == Severity.WARNING
    assert event.detail == "Migration export failed (scope=postgres)"


@pytest.mark.parametrize("operation", [MigrationOperation.export, MigrationOperation.import_])
@pytest.mark.asyncio
async def test_failure_preserves_preexisting_and_uploaded_artifact_directories(
    monkeypatch,
    tmp_path,
    operation,
):
    artifact_dir = tmp_path / operation.value
    artifact_dir.mkdir()
    marker = artifact_dir / "uploaded.tar.gz"
    marker.write_bytes(b"uploaded")
    job = make_job(operation=operation, artifact_dir=str(artifact_dir))
    lookup = Session(Result(one=job))
    terminal = Session()
    factory = SessionFactory(lookup, terminal)
    boundaries = install_job_boundaries(monkeypatch, factory, tmp_path)
    getattr(boundaries, "export" if operation == MigrationOperation.export else "import_").side_effect = MigrationError(
        "invalid archive"
    )

    await migration.run_migration_job({}, str(job.id))

    assert marker.read_bytes() == b"uploaded"
    assert statement_values(terminal)["artifact_dir"] == str(artifact_dir)


@pytest.mark.asyncio
async def test_unknown_operation_is_a_persisted_failure(monkeypatch, tmp_path):
    operation = SimpleNamespace(value="unknown")
    job = make_job(operation=operation)
    lookup = Session(Result(one=job))
    terminal = Session()
    factory = SessionFactory(lookup, terminal)
    boundaries = install_job_boundaries(monkeypatch, factory, tmp_path)

    await migration.run_migration_job({}, str(job.id))

    values = statement_values(terminal)
    assert values["status"] == MigrationStatus.failed
    assert values["error_message"] == f"Unknown operation type: {operation}"
    boundaries.export.assert_not_awaited()
    boundaries.import_.assert_not_awaited()
    boundaries.validate.assert_not_awaited()
    assert boundaries.emit.await_args.args[0].detail == "Migration unknown failed (scope=postgres)"


@pytest.mark.asyncio
async def test_cancellation_propagates_and_a_queue_retry_can_complete(monkeypatch, tmp_path):
    job = make_job()
    first_lookup = Session(Result(one=job))
    retry_lookup = Session(Result(one=job))
    terminal = Session()
    factory = SessionFactory(first_lookup, retry_lookup, terminal)
    boundaries = install_job_boundaries(monkeypatch, factory, tmp_path)
    boundaries.export.side_effect = [asyncio.CancelledError(), ({"total_rows": 1}, None, None)]
    queue_client = MagicMock()
    object_storage = MagicMock()
    ctx = {"redis": queue_client, "object_storage": object_storage}

    with pytest.raises(asyncio.CancelledError):
        await migration.run_migration_job(ctx, str(job.id))

    artifact_dir = tmp_path / str(job.id)
    assert artifact_dir.is_dir()
    assert job.status == MigrationStatus.running
    assert factory.calls == 1
    boundaries.emit.assert_not_awaited()

    await migration.run_migration_job(ctx, str(job.id))

    assert factory.calls == 3
    assert boundaries.export.await_count == 2
    assert statement_values(terminal)["status"] == MigrationStatus.completed
    boundaries.emit.assert_awaited_once()
    assert queue_client.mock_calls == []
    assert object_storage.mock_calls == []


@pytest.mark.asyncio
async def test_connection_resolution_failure_propagates_for_queue_retry(monkeypatch, tmp_path):
    job = make_job()
    lookup = Session(Result(one=job))
    factory = SessionFactory(lookup)
    boundaries = install_job_boundaries(monkeypatch, factory, tmp_path)
    boundaries.pg_resolver.side_effect = ConnectionError("postgres unavailable")

    with pytest.raises(ConnectionError, match="postgres unavailable"):
        await migration.run_migration_job({}, str(job.id))

    assert job.status == MigrationStatus.running
    lookup.commit.assert_awaited_once_with()
    assert (tmp_path / str(job.id)).is_dir()
    boundaries.ch_resolver.assert_not_awaited()
    boundaries.export.assert_not_awaited()
    boundaries.emit.assert_not_awaited()
    assert factory.calls == 1


@pytest.mark.asyncio
async def test_export_both_packages_postgres_and_telemetry_artifacts(monkeypatch, tmp_path):
    pg_conn = PgConnParams("postgresql://source/db")
    ch_conn = ChConnParams("clickhouse://source/observal")
    reporter = MagicMock()
    export_pg = AsyncMock()
    export_ch = AsyncMock()

    async def fake_export_pg(params, output_path, progress):
        assert params == pg_conn
        assert progress is reporter
        output_path.write_bytes(b"postgres archive")
        output_path.with_name("pg_export.manifest.json").write_text("{}", encoding="utf-8")
        return ExportResult(
            archive_path=str(output_path),
            migration_id="migration-1",
            table_counts={"users": 2},
            checksums={"users": "checksum"},
            duration_seconds=1.0,
            total_rows=2,
        )

    async def fake_export_ch(params, manifest_path, output_dir, progress):
        assert params == ch_conn
        assert manifest_path == tmp_path / "pg_export.manifest.json"
        assert progress is reporter
        output_dir.mkdir()
        (output_dir / "telemetry_manifest.json").write_text("{}", encoding="utf-8")
        (output_dir / "session_events.parquet").write_bytes(b"parquet")
        return TelemetryExportResult(
            output_dir=str(output_dir),
            migration_id="migration-1",
            table_results={
                "session_events": {"files": ["session_events.parquet", "missing.parquet"]},
            },
            total_rows=3,
            total_size_bytes=7,
            duration_seconds=2.0,
        )

    export_pg.side_effect = fake_export_pg
    export_ch.side_effect = fake_export_ch
    monkeypatch.setattr(migration, "export_pg", export_pg)
    monkeypatch.setattr(migration, "export_ch", export_ch)
    from observal_shared.migration import archive as archive_service

    hash_file = MagicMock(side_effect=lambda path: f"hash:{path.name}")
    monkeypatch.setattr(archive_service, "_sha256_file", hash_file)

    result, artifacts, schema_version = await migration._run_export(
        MigrationScope.both,
        pg_conn,
        ch_conn,
        str(tmp_path),
        reporter,
    )

    assert result == {
        "table_counts": {"users": 2},
        "total_rows": 2,
        "archive_size_bytes": len(b"postgres archive"),
        "telemetry_size_bytes": 7,
        "schema_version_diff": None,
    }
    assert schema_version is None
    assert [artifact["name"] for artifact in artifacts] == ["pg_export.tar.gz", "telemetry_export.tar.gz"]
    assert artifacts[0]["sha256"] == "hash:pg_export.tar.gz"
    assert artifacts[1]["sha256"] == "hash:telemetry_export.tar.gz"
    with tarfile.open(tmp_path / "telemetry_export.tar.gz", "r:gz") as archive:
        assert archive.getnames() == ["telemetry_manifest.json", "session_events.parquet"]
    export_pg.assert_awaited_once()
    export_ch.assert_awaited_once()
    assert hash_file.call_count == 2


@pytest.mark.asyncio
async def test_clickhouse_export_uses_fallback_manifest_and_default_result_fields(monkeypatch, tmp_path):
    fallback = tmp_path / "migration_manifest.json"
    fallback.write_text("{}", encoding="utf-8")
    export_pg = AsyncMock()

    async def fake_export_ch(params, manifest_path, output_dir, reporter):
        assert manifest_path == fallback
        output_dir.mkdir()
        return TelemetryExportResult(
            output_dir=str(output_dir),
            migration_id="migration-2",
            table_results={},
            total_rows=0,
            total_size_bytes=0,
            duration_seconds=1.0,
        )

    export_ch = AsyncMock(side_effect=fake_export_ch)
    monkeypatch.setattr(migration, "export_pg", export_pg)
    monkeypatch.setattr(migration, "export_ch", export_ch)
    from observal_shared.migration import archive as archive_service

    monkeypatch.setattr(archive_service, "_sha256_file", MagicMock(return_value="telemetry-hash"))

    result, artifacts, schema_version = await migration._run_export(
        MigrationScope.clickhouse,
        PgConnParams("postgresql://source/db"),
        ChConnParams("clickhouse://source/observal"),
        str(tmp_path),
        MagicMock(),
    )

    export_pg.assert_not_awaited()
    export_ch.assert_awaited_once()
    assert result == {
        "telemetry_size_bytes": 0,
        "archive_size_bytes": None,
        "schema_version_diff": None,
    }
    assert artifacts[0]["name"] == "telemetry_export.tar.gz"
    assert artifacts[0]["sha256"] == "telemetry-hash"
    assert schema_version is None


@pytest.mark.asyncio
async def test_clickhouse_export_without_packed_output_has_no_artifact(monkeypatch, tmp_path):
    export_ch = AsyncMock(
        return_value=TelemetryExportResult(
            output_dir=str(tmp_path / "telemetry"),
            migration_id="migration-missing",
            table_results={},
            total_rows=1,
            total_size_bytes=9,
            duration_seconds=1.0,
        )
    )
    archive_context = MagicMock()
    archive_context.__enter__.return_value = MagicMock()
    archive_context.__exit__.return_value = False
    open_archive = MagicMock(return_value=archive_context)
    monkeypatch.setattr(migration, "export_ch", export_ch)
    monkeypatch.setattr(tarfile, "open", open_archive)

    result, artifacts, schema_version = await migration._run_export(
        MigrationScope.clickhouse,
        PgConnParams("postgresql://source/db"),
        ChConnParams("clickhouse://source/observal"),
        str(tmp_path),
        MagicMock(),
    )

    open_archive.assert_called_once_with(tmp_path / "telemetry_export.tar.gz", "w:gz")
    assert result["telemetry_size_bytes"] == 9
    assert artifacts is None
    assert schema_version is None


@pytest.mark.asyncio
async def test_postgres_export_without_output_has_no_artifact(monkeypatch, tmp_path):
    export_pg = AsyncMock(
        return_value=ExportResult(
            archive_path=str(tmp_path / "pg_export.tar.gz"),
            migration_id="migration-3",
            table_counts={},
            checksums={},
            duration_seconds=1.0,
            total_rows=0,
        )
    )
    monkeypatch.setattr(migration, "export_pg", export_pg)

    result, artifacts, schema_version = await migration._run_export(
        MigrationScope.postgres,
        PgConnParams("postgresql://source/db"),
        ChConnParams("clickhouse://source/observal"),
        str(tmp_path),
        MagicMock(),
    )

    assert result["archive_size_bytes"] is None
    assert result["telemetry_size_bytes"] is None
    assert artifacts is None
    assert schema_version is None


@pytest.mark.asyncio
async def test_import_both_extracts_telemetry_and_merges_row_counts(monkeypatch, tmp_path):
    pg_archive = tmp_path / "snapshot.tgz"
    write_tar(pg_archive, {"manifest.json": b"{}"})
    telemetry_archive = tmp_path / "telemetry_export.tar.gz"
    write_tar(
        telemetry_archive,
        {
            "telemetry_manifest.json": b"{}",
            "session_events.parquet": b"parquet",
        },
    )
    pg_result = ImportResult(
        migration_id="migration-4",
        tables_imported=1,
        rows_inserted={"users": 3},
        rows_skipped={"users": 1},
        duration_seconds=1.0,
    )
    ch_result = TelemetryImportResult(
        migration_id="migration-4",
        tables_imported=1,
        tables_skipped=["audit_log"],
        rows_imported={"session_events": 4},
        duration_seconds=1.0,
    )
    import_pg = AsyncMock(return_value=pg_result)
    import_ch = AsyncMock(return_value=ch_result)
    monkeypatch.setattr(migration, "import_pg", import_pg)
    monkeypatch.setattr(migration, "import_ch", import_ch)
    pg_conn = PgConnParams("postgresql://target/db")
    ch_conn = ChConnParams("clickhouse://target/observal")
    reporter = MagicMock()

    result, artifacts, schema_version = await migration._run_import(
        MigrationScope.both,
        pg_conn,
        ch_conn,
        str(tmp_path),
        reporter,
    )

    import_pg.assert_awaited_once_with(pg_conn, pg_archive, reporter)
    telemetry_dir = tmp_path / "telemetry"
    import_ch.assert_awaited_once_with(ch_conn, telemetry_dir, reporter)
    assert (telemetry_dir / "telemetry_manifest.json").read_bytes() == b"{}"
    assert (telemetry_dir / "session_events.parquet").read_bytes() == b"parquet"
    assert result == {
        "rows_inserted": {"users": 3, "session_events": 4},
        "rows_skipped": {"users": 1},
        "tables_skipped": ["audit_log"],
        "total_rows": 8,
        "schema_version_diff": None,
    }
    assert artifacts is None
    assert schema_version is None


@pytest.mark.asyncio
async def test_postgres_import_does_not_call_clickhouse(monkeypatch, tmp_path):
    pg_archive = tmp_path / "snapshot.tar.gz"
    write_tar(pg_archive, {"manifest.json": b"{}"})
    import_pg = AsyncMock(
        return_value=ImportResult(
            migration_id="migration-pg",
            tables_imported=1,
            rows_inserted={"users": 2},
            rows_skipped={"users": 1},
            duration_seconds=1.0,
        )
    )
    import_ch = AsyncMock()
    monkeypatch.setattr(migration, "import_pg", import_pg)
    monkeypatch.setattr(migration, "import_ch", import_ch)
    pg_conn = PgConnParams("postgresql://target/db")
    reporter = MagicMock()

    result, artifacts, schema_version = await migration._run_import(
        MigrationScope.postgres,
        pg_conn,
        ChConnParams("clickhouse://target/observal"),
        str(tmp_path),
        reporter,
    )

    import_pg.assert_awaited_once_with(pg_conn, pg_archive, reporter)
    import_ch.assert_not_awaited()
    assert result["total_rows"] == 3
    assert artifacts is None
    assert schema_version is None


@pytest.mark.asyncio
async def test_clickhouse_import_uses_artifact_root_without_telemetry_archive(monkeypatch, tmp_path):
    (tmp_path / "telemetry_manifest.json").write_text("{}", encoding="utf-8")
    import_pg = AsyncMock()
    import_ch = AsyncMock(
        return_value=TelemetryImportResult(
            migration_id="migration-5",
            tables_imported=0,
            tables_skipped=[],
            rows_imported=None,
            duration_seconds=1.0,
        )
    )
    monkeypatch.setattr(migration, "import_pg", import_pg)
    monkeypatch.setattr(migration, "import_ch", import_ch)
    ch_conn = ChConnParams("clickhouse://target/observal")
    reporter = MagicMock()

    result, artifacts, schema_version = await migration._run_import(
        MigrationScope.clickhouse,
        PgConnParams("postgresql://target/db"),
        ch_conn,
        str(tmp_path),
        reporter,
    )

    import_pg.assert_not_awaited()
    import_ch.assert_awaited_once_with(ch_conn, tmp_path, reporter)
    assert result["total_rows"] == 0
    assert result["rows_inserted"] == {}
    assert artifacts is None
    assert schema_version is None


@pytest.mark.asyncio
async def test_postgres_import_requires_an_archive(monkeypatch, tmp_path):
    import_pg = AsyncMock()
    monkeypatch.setattr(migration, "import_pg", import_pg)

    with pytest.raises(MigrationError, match="No PostgreSQL"):
        await migration._run_import(
            MigrationScope.postgres,
            PgConnParams("postgresql://target/db"),
            ChConnParams("clickhouse://target/observal"),
            str(tmp_path),
            MagicMock(),
        )

    import_pg.assert_not_awaited()


@pytest.mark.asyncio
async def test_validate_both_extracts_telemetry_and_combines_results(monkeypatch, tmp_path):
    pg_archive = tmp_path / "snapshot.tar.gz"
    write_tar(pg_archive, {"manifest.json": b"{}"})
    write_tar(
        tmp_path / "telemetry_export.tar.gz",
        {"telemetry_manifest.json": b"{}", "events.parquet": b"parquet"},
    )
    pg_result = ValidationResult(
        archive_valid=True,
        checksum_results=[ChecksumResult("users", "abc", "abc", True)],
        cross_db_results={"users": (2, 3)},
    )
    ch_result = TelemetryValidationResult(
        checksums_valid=False,
        checksum_results={"events.parquet": False},
        fk_results={"orphaned_agent_ids": ["agent-1"]},
        row_count_results={"session_events": (4, 5)},
    )
    validate_pg = AsyncMock(return_value=pg_result)
    validate_ch = AsyncMock(return_value=ch_result)
    monkeypatch.setattr(migration, "validate_pg", validate_pg)
    monkeypatch.setattr(migration, "validate_ch", validate_ch)
    pg_conn = PgConnParams("postgresql://target/db")
    ch_conn = ChConnParams("clickhouse://target/observal")
    reporter = MagicMock()

    result, artifacts, schema_version = await migration._run_validate(
        MigrationScope.both,
        pg_conn,
        ch_conn,
        str(tmp_path),
        reporter,
    )

    validate_pg.assert_awaited_once_with(pg_conn, pg_archive, reporter)
    telemetry_dir = tmp_path / "telemetry"
    validate_ch.assert_awaited_once_with(ch_conn, pg_conn, telemetry_dir, reporter)
    assert (telemetry_dir / "events.parquet").read_bytes() == b"parquet"
    assert result == {
        "checksums_valid": False,
        "checksum_details": {"users": True, "events.parquet": False},
        "row_count_comparison": {"users": [2, 3], "session_events": [4, 5]},
        "orphaned_fk_refs": {"orphaned_agent_ids": ["agent-1"]},
        "schema_version_diff": None,
    }
    assert artifacts is None
    assert schema_version is None


@pytest.mark.asyncio
async def test_postgres_validation_without_comparison_does_not_call_clickhouse(monkeypatch, tmp_path):
    pg_archive = tmp_path / "snapshot.tar.gz"
    write_tar(pg_archive, {"manifest.json": b"{}"})
    validate_pg = AsyncMock(
        return_value=ValidationResult(
            archive_valid=True,
            checksum_results=[],
            cross_db_results=None,
        )
    )
    validate_ch = AsyncMock()
    monkeypatch.setattr(migration, "validate_pg", validate_pg)
    monkeypatch.setattr(migration, "validate_ch", validate_ch)
    pg_conn = PgConnParams("postgresql://target/db")
    reporter = MagicMock()

    result, artifacts, schema_version = await migration._run_validate(
        MigrationScope.postgres,
        pg_conn,
        ChConnParams("clickhouse://target/observal"),
        str(tmp_path),
        reporter,
    )

    validate_pg.assert_awaited_once_with(pg_conn, pg_archive, reporter)
    validate_ch.assert_not_awaited()
    assert result["checksums_valid"] is True
    assert result["row_count_comparison"] is None
    assert artifacts is None
    assert schema_version is None


@pytest.mark.asyncio
async def test_clickhouse_validation_uses_root_and_accepts_empty_details(monkeypatch, tmp_path):
    (tmp_path / "telemetry_manifest.json").write_text("{}", encoding="utf-8")
    validate_pg = AsyncMock()
    validate_ch = AsyncMock(
        return_value=TelemetryValidationResult(
            checksums_valid=True,
            checksum_results=None,
            fk_results=None,
            row_count_results=None,
        )
    )
    monkeypatch.setattr(migration, "validate_pg", validate_pg)
    monkeypatch.setattr(migration, "validate_ch", validate_ch)
    pg_conn = PgConnParams("postgresql://target/db")
    ch_conn = ChConnParams("clickhouse://target/observal")
    reporter = MagicMock()

    result, artifacts, schema_version = await migration._run_validate(
        MigrationScope.clickhouse,
        pg_conn,
        ch_conn,
        str(tmp_path),
        reporter,
    )

    validate_pg.assert_not_awaited()
    validate_ch.assert_awaited_once_with(ch_conn, pg_conn, tmp_path, reporter)
    assert result["checksums_valid"] is True
    assert result["checksum_details"] == {}
    assert result["orphaned_fk_refs"] is None
    assert artifacts is None
    assert schema_version is None


@pytest.mark.asyncio
async def test_postgres_validation_requires_an_archive(monkeypatch, tmp_path):
    validate_pg = AsyncMock()
    monkeypatch.setattr(migration, "validate_pg", validate_pg)

    with pytest.raises(MigrationError, match="for validation"):
        await migration._run_validate(
            MigrationScope.postgres,
            PgConnParams("postgresql://target/db"),
            ChConnParams("clickhouse://target/observal"),
            str(tmp_path),
            MagicMock(),
        )

    validate_pg.assert_not_awaited()


@pytest.mark.asyncio
async def test_purge_clears_removed_and_missing_artifacts_but_keeps_failed_removal(monkeypatch):
    normal = SimpleNamespace(id="normal", artifact_dir="/normal", artifacts_json=[{"name": "a"}])
    missing = SimpleNamespace(id="missing", artifact_dir="/missing", artifacts_json=[{"name": "b"}])
    stubborn = SimpleNamespace(id="stubborn", artifact_dir="/stubborn", artifacts_json=[{"name": "c"}])
    vanished = SimpleNamespace(id="vanished", artifact_dir="/vanished", artifacts_json=[{"name": "d"}])
    session = Session(Result(many=[normal, missing, stubborn, vanished]))
    factory = SessionFactory(session)
    existing = {"/normal", "/stubborn", "/vanished"}
    removals = []

    def isdir(path):
        return path in existing

    def rmtree(path):
        removals.append(path)
        if path == "/stubborn":
            raise OSError("busy")
        existing.remove(path)
        if path == "/vanished":
            raise OSError("removed despite error")

    get_ttl = AsyncMock(return_value=6)
    warning = MagicMock()
    monkeypatch.setattr(migration, "async_session", factory)
    monkeypatch.setattr(migration, "datetime", FrozenDateTime)
    monkeypatch.setattr(migration.ds, "get_int", get_ttl)
    monkeypatch.setattr(migration.os.path, "isdir", isdir)
    monkeypatch.setattr(migration.shutil, "rmtree", rmtree)
    monkeypatch.setattr(migration.optic, "warning", warning)
    object_storage = MagicMock()

    await migration.purge_migration_artifacts({"object_storage": object_storage})

    get_ttl.assert_awaited_once_with("migration.artifact_ttl_hours", default=24)
    assert session.entered == session.exited == 1
    session.commit.assert_awaited_once_with()
    assert removals == ["/normal", "/stubborn", "/vanished"]
    assert normal.artifact_dir is normal.artifacts_json is None
    assert missing.artifact_dir is missing.artifacts_json is None
    assert vanished.artifact_dir is vanished.artifacts_json is None
    assert stubborn.artifact_dir == "/stubborn"
    assert stubborn.artifacts_json == [{"name": "c"}]
    assert warning.call_count == 2
    assert object_storage.mock_calls == []
    cutoff = NOW - timedelta(hours=6)
    assert cutoff in session.statements[0].compile().params.values()


@pytest.mark.asyncio
async def test_purge_without_eligible_jobs_does_not_commit(monkeypatch):
    session = Session(Result(many=[]))
    factory = SessionFactory(session)
    monkeypatch.setattr(migration, "async_session", factory)
    monkeypatch.setattr(migration, "datetime", FrozenDateTime)
    monkeypatch.setattr(migration.ds, "get_int", AsyncMock(return_value=24))

    await migration.purge_migration_artifacts({})

    session.commit.assert_not_awaited()
    assert session.entered == session.exited == 1


def test_migration_upload_tempdir_uses_persistent_artifact_volume(tmp_path):
    import tempfile

    from services.migration_uploads import configure_migration_upload_tempdir

    artifact_root = tmp_path / "migration_artifacts"
    original_tempdir = tempfile.tempdir
    try:
        upload_tempdir = configure_migration_upload_tempdir(artifact_root)
        assert upload_tempdir == tmp_path / "migration_upload_tmp"
        assert upload_tempdir.is_dir()
        assert upload_tempdir.stat().st_mode & 0o777 == 0o700
        assert tempfile.tempdir == str(upload_tempdir)
    finally:
        tempfile.tempdir = original_tempdir


def test_migration_upload_tempdir_survives_read_only_filesystem(tmp_path, monkeypatch):
    import errno
    import tempfile
    from pathlib import Path

    from services.migration_uploads import configure_migration_upload_tempdir

    def read_only(*_args, **_kwargs):
        raise OSError(errno.EROFS, "Read-only file system")

    monkeypatch.setattr(Path, "mkdir", read_only)
    original_tempdir = tempfile.tempdir
    try:
        assert configure_migration_upload_tempdir(tmp_path / "migration_artifacts") is None
        assert tempfile.tempdir == original_tempdir
    finally:
        tempfile.tempdir = original_tempdir


def test_api_image_keeps_migration_artifacts_on_the_data_volume():
    from pathlib import Path

    dockerfile = (Path(__file__).parents[1] / "docker" / "Dockerfile.api").read_text()
    assert "ENV MIGRATION_ARTIFACT_ROOT=/data/migration_artifacts" in dockerfile
