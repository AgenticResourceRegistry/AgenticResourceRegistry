# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Behavioral contracts for the portable migration CLI."""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from typer.main import get_command
from typer.testing import CliRunner

import observal_cli.cmd_migrate as migrate
from observal_cli.errors import CliError, ErrorCategory
from observal_cli.main import app
from observal_shared.migration import ChecksumMismatchError, ConnectionFailedError, MigrationError, PrerequisiteError
from observal_shared.migration.archive import pack_pg_archive
from observal_shared.migration.results import (
    ChecksumResult,
    ExportResult,
    ImportResult,
    TelemetryExportResult,
    TelemetryImportResult,
    TelemetryValidationResult,
    ValidationResult,
)

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


def export_result(path: Path) -> ExportResult:
    return ExportResult(
        archive_path=str(path),
        migration_id="migration-pg",
        table_counts={"users": 2, "agents": 1},
        checksums={"users": "abc", "agents": "def"},
        duration_seconds=2.25,
        total_rows=3,
    )


def import_result() -> ImportResult:
    return ImportResult(
        migration_id="migration-pg",
        tables_imported=2,
        rows_inserted={"users": 1, "agents": 2},
        rows_skipped={"users": 3, "agents": 4},
        duration_seconds=3.5,
        warnings=["one table used a default"],
    )


def telemetry_export_result(path: Path) -> TelemetryExportResult:
    return TelemetryExportResult(
        output_dir=str(path),
        migration_id="migration-ch",
        table_results={"session_events": {"files": [], "row_count": 2500}},
        total_rows=2500,
        total_size_bytes=1572864,
        duration_seconds=4.75,
    )


def telemetry_import_result() -> TelemetryImportResult:
    return TelemetryImportResult(
        migration_id="migration-ch",
        tables_imported=2,
        tables_skipped=["audit_log"],
        rows_imported={"session_events": 1200, "security_events": 34},
        duration_seconds=5.25,
        warnings=["partition already present"],
    )


@pytest.fixture
def human(monkeypatch: pytest.MonkeyPatch):
    lines: list[str] = []
    spinners: list[str] = []

    @contextmanager
    def spinner(message: str):
        spinners.append(message)
        yield

    monkeypatch.setattr(migrate, "rprint", lambda *values, **_kwargs: lines.append(" ".join(map(str, values))))
    monkeypatch.setattr(migrate, "spinner", spinner)
    return lines, spinners


def test_all_migration_leaves_have_json_output_and_export_uses_file() -> None:
    command = get_command(app).commands["server"].commands["migrate"]
    for leaf in command.commands.values():
        assert any(parameter.name == "output" for parameter in leaf.params)
    export = command.commands["export"]
    assert any(parameter.name == "file" for parameter in export.params)
    assert sum(parameter.name == "output" for parameter in export.params) == 1


def test_pyarrow_dependency_is_categorized(monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as missing:
        missing.setitem(sys.modules, "pyarrow", None)
        with pytest.raises(CliError) as error:
            migrate._require_pyarrow()
    assert error.value.category is ErrorCategory.UNAVAILABLE


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (ChecksumMismatchError("secret detail"), ErrorCategory.VALIDATION),
        (ConnectionFailedError("secret detail"), ErrorCategory.UNAVAILABLE),
        (PrerequisiteError("secret detail"), ErrorCategory.VALIDATION),
        (MigrationError("secret detail"), ErrorCategory.UNAVAILABLE),
    ],
)
def test_migration_errors_are_categorized_without_exposing_domain_text(error, category) -> None:
    with pytest.raises(CliError) as raised:
        migrate._handle_migration_error(error, "Test migration")
    assert raised.value.category is category
    assert "secret detail" not in raised.value.message


def test_null_progress_reporter_is_silent(capsys) -> None:
    asyncio.run(migrate.NullProgressReporter().update(phase="export", pct=50, message="half"))
    assert capsys.readouterr().out == ""


def test_clickhouse_cleartext_warning_is_human_only(human) -> None:
    lines, _ = human
    migrate._warn_clickhouse_cleartext("clickhouse://operator:sensitive@db.example/observal", migrate.OutputMode.table)
    assert "unencrypted HTTP" in "\n".join(lines)
    assert "sensitive" not in "\n".join(lines)
    lines.clear()
    migrate._warn_clickhouse_cleartext("clickhouse://operator:sensitive@db.example/observal", migrate.OutputMode.json)
    assert lines == []


def test_archive_publish_is_atomic_on_pack_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = tmp_path / "manifest.json"
    phase = tmp_path / "migration_manifest.json"
    pg_dir = tmp_path / "pg"
    pg_dir.mkdir()
    manifest.write_text("{}")
    phase.write_text("{}")
    (pg_dir / "users.jsonl").write_text("{}\n")
    archive = MagicMock()
    archive.__enter__.return_value = archive
    archive.add.side_effect = OSError("disk full")
    monkeypatch.setattr("observal_shared.migration.archive.tarfile.open", MagicMock(return_value=archive))
    output = tmp_path / "registry.tar.gz"

    with pytest.raises(OSError, match="disk full"):
        pack_pg_archive(
            output_path=output,
            staging_dir=tmp_path,
            manifest_path=manifest,
            migration_manifest_path=phase,
            insert_order=["users"],
            pg_dir=pg_dir,
        )

    assert not output.exists()
    assert not list(tmp_path.glob(".registry.tar.gz.*"))


def test_postgres_export_json_is_private_and_finite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destination = tmp_path / "registry.tar.gz"

    async def export(_params, path, reporter):
        assert isinstance(reporter, migrate.NullProgressReporter)
        path.write_bytes(b"archive")
        path.with_name("registry.manifest.json").write_text("{}")
        return export_result(path)

    monkeypatch.setattr(migrate, "export_pg", AsyncMock(side_effect=export))

    result = runner.invoke(
        app,
        [
            "server",
            "migrate",
            "export",
            "--db-url",
            "postgresql://operator:secret@source/observal",
            "--file",
            str(destination),
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["archive"] == str(destination)
    assert payload["total_rows"] == 3
    assert "secret" not in result.stdout
    assert destination.stat().st_mode & 0o777 == 0o600
    assert destination.with_name("registry.manifest.json").stat().st_mode & 0o777 == 0o600


def test_postgres_export_rejects_existing_destination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destination = tmp_path / "registry.tar.gz"
    destination.write_bytes(b"keep")
    export = AsyncMock()
    monkeypatch.setattr(migrate, "export_pg", export)

    result = runner.invoke(
        app,
        [
            "server",
            "migrate",
            "export",
            "--db-url",
            "postgresql://source/observal",
            "--file",
            str(destination),
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 6
    assert result.stdout == ""
    assert destination.read_bytes() == b"keep"
    export.assert_not_awaited()


def test_postgres_import_json_reports_accounting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / "registry.tar.gz"
    archive.touch()
    monkeypatch.setattr(migrate.tarfile, "is_tarfile", lambda _path: True)
    operation = AsyncMock(return_value=import_result())
    monkeypatch.setattr(migrate, "import_pg", operation)

    result = runner.invoke(
        app,
        [
            "server",
            "migrate",
            "import",
            "--db-url",
            "postgresql://target/observal",
            "--archive",
            str(archive),
            "--output",
            "json",
        ],
    )

    payload = json.loads(result.stdout)
    assert payload["total_inserted"] == 3
    assert payload["total_skipped"] == 7
    assert isinstance(operation.await_args.args[2], migrate.NullProgressReporter)


@pytest.mark.parametrize("leaf", ["import", "validate"])
def test_postgres_archive_commands_categorize_missing_source(leaf: str, tmp_path: Path) -> None:
    arguments = ["server", "migrate", leaf, "--archive", str(tmp_path / "missing.tar.gz"), "--output", "json"]
    if leaf == "import":
        arguments.extend(["--db-url", "postgresql://target/observal"])

    result = runner.invoke(app, arguments)

    assert result.exit_code == 5
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["category"] == "not_found"


def test_postgres_validation_json_reports_mismatches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / "registry.tar.gz"
    archive.touch()
    monkeypatch.setattr(migrate.tarfile, "is_tarfile", lambda _path: True)
    monkeypatch.setattr(
        migrate,
        "validate_pg",
        AsyncMock(
            return_value=ValidationResult(
                archive_valid=True,
                checksum_results=[ChecksumResult("users", "abc", "abc", True)],
                cross_db_results={"users": (2, 3)},
            )
        ),
    )

    result = runner.invoke(
        app,
        ["server", "migrate", "validate", "--archive", str(archive), "--output", "json"],
    )

    payload = json.loads(result.stdout)
    assert payload["valid"] is True
    assert payload["row_count_mismatches"] == 1


def test_postgres_validation_rejects_bad_checksums(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / "registry.tar.gz"
    archive.touch()
    monkeypatch.setattr(migrate.tarfile, "is_tarfile", lambda _path: True)
    monkeypatch.setattr(
        migrate,
        "validate_pg",
        AsyncMock(
            return_value=ValidationResult(
                archive_valid=False,
                checksum_results=[ChecksumResult("users", "abc", "def", False)],
                cross_db_results=None,
            )
        ),
    )

    result = runner.invoke(
        app,
        ["server", "migrate", "validate", "--archive", str(archive), "--output", "json"],
    )

    assert result.exit_code == 7
    assert result.stdout == ""


def test_clickhouse_url_requires_hostname(tmp_path: Path) -> None:
    source = tmp_path / "telemetry"
    source.mkdir()

    result = runner.invoke(
        app,
        [
            "server",
            "migrate",
            "import-telemetry",
            "--clickhouse-url",
            "clickhouse://",
            "--input-dir",
            str(source),
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 7
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["category"] == "validation"


def test_telemetry_export_requires_new_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destination = tmp_path / "telemetry"
    destination.mkdir()
    operation = AsyncMock()
    monkeypatch.setattr(migrate, "export_ch", operation)

    result = runner.invoke(
        app,
        [
            "server",
            "migrate",
            "export-telemetry",
            "--clickhouse-url",
            "clickhouses://source/observal",
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--output-dir",
            str(destination),
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 6
    operation.assert_not_awaited()


def test_telemetry_export_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destination = tmp_path / "telemetry"
    operation = AsyncMock(return_value=telemetry_export_result(destination))
    monkeypatch.setattr(migrate, "export_ch", operation)

    result = runner.invoke(
        app,
        [
            "server",
            "migrate",
            "export-telemetry",
            "--clickhouse-url",
            "clickhouses://source/observal",
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--output-dir",
            str(destination),
            "--output",
            "json",
        ],
    )

    assert json.loads(result.stdout)["total_rows"] == 2500
    assert isinstance(operation.await_args.args[3], migrate.NullProgressReporter)


def test_telemetry_import_and_validation_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "telemetry"
    source.mkdir()
    monkeypatch.setattr(migrate, "import_ch", AsyncMock(return_value=telemetry_import_result()))
    monkeypatch.setattr(
        migrate,
        "validate_ch",
        AsyncMock(
            return_value=TelemetryValidationResult(
                checksums_valid=True,
                checksum_results={"events.parquet": True},
                row_count_results={"session_events": (10, 9)},
                fk_results={"orphaned_agent_ids": ["agent"]},
            )
        ),
    )

    imported = runner.invoke(
        app,
        [
            "server",
            "migrate",
            "import-telemetry",
            "--clickhouse-url",
            "clickhouses://target/observal",
            "--input-dir",
            str(source),
            "--output",
            "json",
        ],
    )
    validated = runner.invoke(
        app,
        ["server", "migrate", "validate-telemetry", "--input-dir", str(source), "--output", "json"],
    )

    assert json.loads(imported.stdout)["total_rows"] == 1234
    validation = json.loads(validated.stdout)
    assert validation["row_count_mismatches"] == 1
    assert validation["orphan_groups"] == 1


def test_all_migration_services_use_categorized_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / "registry.tar.gz"
    archive.touch()
    source = tmp_path / "telemetry"
    source.mkdir()
    monkeypatch.setattr(migrate.tarfile, "is_tarfile", lambda _path: True)
    monkeypatch.setattr(migrate, "import_pg", AsyncMock(side_effect=MigrationError("internal secret")))

    result = runner.invoke(
        app,
        [
            "server",
            "migrate",
            "import",
            "--db-url",
            "postgresql://operator:sensitive@target/observal",
            "--archive",
            str(archive),
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 9
    assert result.stdout == ""
    assert "sensitive" not in result.stderr
    assert "internal secret" not in result.stderr
