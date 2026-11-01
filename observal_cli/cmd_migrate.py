# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Portable PostgreSQL and ClickHouse migration commands.

This module provides the CLI commands for data migration. All core logic is
delegated to the shared `observal_shared.migration` package. This module handles
only CLI-specific concerns: human and JSON output, categorized errors, and progress reporting.
"""

from __future__ import annotations

import asyncio
import tarfile
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich import print as rprint
from rich.markup import escape

from observal_cli.errors import ErrorCategory, fail
from observal_cli.render import OutputMode, output_json, spinner

# ── Shared service imports ───────────────────────────────
from observal_shared.migration import (
    ChConnParams,
    ChecksumMismatchError,
    ConnectionFailedError,
    ExportResult,
    ImportResult,
    MigrationError,
    PgConnParams,
    PrerequisiteError,
    TelemetryExportResult,
    TelemetryImportResult,
    TelemetryValidationResult,
    ValidationResult,
    export_ch,
    export_pg,
    import_ch,
    import_pg,
    validate_ch,
    validate_pg,
)
from observal_shared.migration.connections import parse_clickhouse_url
from observal_shared.migration.constants import _UUID_RE  # noqa: F401, re-exported for backward compat

# ── RichProgressReporter ─────────────────────────────────


class RichProgressReporter:
    """CLI progress reporter that uses rich console output.

    Satisfies the ProgressReporter protocol defined in observal_shared.migration.progress.
    """

    def __init__(self) -> None:
        self._last_phase: str | None = None

    async def update(self, *, phase: str, pct: int, message: str) -> None:
        """Report progress via rich console output."""
        if phase != self._last_phase:
            if self._last_phase is not None:
                rprint()  # Blank line between phases
            self._last_phase = phase
        rprint(f"  [dim][{pct:3d}%][/dim] {escape(message)}")


class NullProgressReporter:
    """Discard migration progress in finite JSON mode."""

    async def update(self, *, phase: str, pct: int, message: str) -> None:
        return None


# ── CLI-specific helpers ─────────────────────────────────


def _is_json(output: OutputMode) -> bool:
    return output == "json"


def _reporter(output: OutputMode) -> RichProgressReporter | NullProgressReporter:
    return NullProgressReporter() if _is_json(output) else RichProgressReporter()


def _progress(output: OutputMode, message: str):
    return nullcontext() if _is_json(output) else spinner(message)


def _require_pyarrow() -> None:
    """pyarrow is an optional dependency; tell the user how to install it."""
    try:
        import pyarrow  # noqa: F401
    except ImportError as error:
        fail(
            ErrorCategory.UNAVAILABLE,
            "Migration support is not installed.",
            operation="Load migration tools",
            resource="pyarrow",
            remediation="Install the CLI migrate extra and retry.",
            detail=repr(error),
        )


def _handle_migration_error(error: MigrationError, operation: str) -> None:
    """Convert a migration domain failure to the CLI error contract."""
    if isinstance(error, ChecksumMismatchError):
        category = ErrorCategory.VALIDATION
        message = "Migration checksum verification failed."
        remediation = "Re-export the source data and retry with the new artifact."
    elif isinstance(error, ConnectionFailedError):
        category = ErrorCategory.UNAVAILABLE
        message = "The migration database connection failed."
        remediation = "Check database availability and credentials, then retry."
    elif isinstance(error, PrerequisiteError):
        category = ErrorCategory.VALIDATION
        message = "A migration prerequisite is missing or invalid."
        remediation = "Complete and validate the previous migration phase, then retry."
    else:
        category = ErrorCategory.UNAVAILABLE
        message = "The migration operation failed."
        remediation = "Check the source artifact and database health, then retry."
    fail(
        category,
        message,
        operation=operation,
        resource="migration data",
        remediation=remediation,
        detail=type(error).__name__,
    )


def _warn_clickhouse_cleartext(url: str, output: OutputMode) -> None:
    """Emit a warning when using unencrypted HTTP transport with credentials."""
    try:
        http_url, _db, _user, password = parse_clickhouse_url(url)
    except ValueError as error:
        fail(
            ErrorCategory.VALIDATION,
            "The ClickHouse connection URL is invalid.",
            operation="Validate migration connection",
            resource="ClickHouse URL",
            remediation="Provide a clickhouse:// or clickhouses:// URL and retry.",
            detail=type(error).__name__,
        )
    if not _is_json(output) and http_url.startswith("http://") and password:
        rprint(
            "[yellow]⚠  ClickHouse credentials will be sent over unencrypted HTTP.[/yellow]\n"
            "[yellow]   Use clickhouses:// (TLS) for production environments.[/yellow]"
        )


# ── Typer app ────────────────────────────────────────────

migrate_app = typer.Typer(
    help=(
        "Portable PostgreSQL and ClickHouse migration tools\n\n"
        "Examples:\n"
        "  observal server migrate export --db-url postgresql://localhost/observal --file backup.tar.gz\n"
        "  observal server migrate validate --archive backup.tar.gz --output json\n"
        "  observal server migrate export-telemetry "
        "--clickhouse-url clickhouses://localhost/observal "
        "--manifest ./migration_manifest.json --output-dir ./telemetry-export"
    )
)


# ── Export command ───────────────────────────────────────


@migrate_app.command("export")
def export_cmd(
    db_url: str = typer.Option(
        ..., "--db-url", envvar="DATABASE_URL", show_envvar=True, help="Source PostgreSQL connection string"
    ),
    file: Annotated[str | None, typer.Option("--file", "-f", help="Destination archive path")] = None,
    output: Annotated[
        OutputMode, typer.Option("--output", "-o", help="Output format: table or json")
    ] = OutputMode.table,
) -> None:
    """Export all PostgreSQL registry data to a portable archive.

    Connects to the source database, reads all tables in a consistent
    REPEATABLE READ snapshot, and writes JSONL files packed into a
    checksummed .tar.gz archive.

    The archive includes a manifest with SHA-256 checksums and the source
    Alembic migration version for compatibility verification on import.

    Examples:
        observal server migrate export --db-url postgresql://localhost/observal --file backup.tar.gz
        observal server migrate export --db-url postgresql://localhost/observal --file backup.tar.gz --output json
    """
    _require_pyarrow()
    output_path = Path(file or f"observal-export-{datetime.now(UTC):%Y%m%d-%H%M%S}.tar.gz").expanduser()
    if output_path.exists():
        fail(
            ErrorCategory.CONFLICT,
            "The migration archive already exists.",
            operation="Export PostgreSQL registry",
            resource=str(output_path),
            remediation="Choose a new --file destination.",
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not _is_json(output):
        rprint(f"[bold]Exporting to:[/bold] {escape(str(output_path))}")
    try:
        with _progress(output, "Connecting to source database..."):
            result: ExportResult = asyncio.run(export_pg(PgConnParams(dsn=db_url), output_path, _reporter(output)))
    except MigrationError as error:
        _handle_migration_error(error, "Export PostgreSQL registry")

    output_path.chmod(0o600)
    sidecar = output_path.parent / f"{output_path.name.removesuffix('.tar.gz').removesuffix('.tgz')}.manifest.json"
    if not sidecar.is_file():
        fail(
            ErrorCategory.UNAVAILABLE,
            "The PostgreSQL export did not produce its migration manifest.",
            operation="Export PostgreSQL registry",
            resource=str(sidecar),
            remediation="Remove the archive and retry the export.",
        )
    sidecar.chmod(0o600)
    payload = {
        "archive": str(output_path),
        "manifest": str(sidecar) if sidecar.exists() else None,
        "migration_id": result.migration_id,
        "table_counts": result.table_counts,
        "total_rows": result.total_rows,
        "size_bytes": output_path.stat().st_size,
        "duration_seconds": result.duration_seconds,
    }
    if _is_json(output):
        output_json(payload)
        return
    rprint("\n[bold green]✓ Export complete[/bold green]")
    rprint(f"  Archive:    {escape(result.archive_path)}")
    rprint(f"  Migration:  {escape(result.migration_id)}")
    rprint(f"  Tables:     {len(result.table_counts)}")
    rprint(f"  Rows:       {result.total_rows:,}")
    rprint(f"  Size:       {payload['size_bytes'] / (1024 * 1024):.1f} MB")
    rprint(f"  Duration:   {result.duration_seconds:.1f}s")
    rprint("\n[yellow]Archive may contain sensitive registry data. Store it securely.[/yellow]")


# ── Import command ───────────────────────────────────────


@migrate_app.command("import")
def import_cmd(
    db_url: str = typer.Option(
        ..., "--db-url", envvar="TARGET_DATABASE_URL", show_envvar=True, help="Target PostgreSQL connection string"
    ),
    archive: str = typer.Option(..., "--archive", "-a", help="Path to .tar.gz archive"),
    output: Annotated[
        OutputMode, typer.Option("--output", "-o", help="Output format: table or json")
    ] = OutputMode.table,
) -> None:
    """Import a migration archive into the target database.

    Verifies checksums before inserting any data. Uses ON CONFLICT DO NOTHING
    for idempotent imports: existing rows are skipped, not overwritten.

    Examples:
        observal server migrate import --db-url postgresql://localhost/observal --archive backup.tar.gz
        observal server migrate import --db-url postgresql://localhost/observal --archive backup.tar.gz --output json
    """
    _require_pyarrow()
    archive_path = Path(archive).expanduser()
    if not archive_path.is_file():
        fail(
            ErrorCategory.NOT_FOUND,
            "The PostgreSQL migration archive was not found.",
            operation="Import PostgreSQL registry",
            resource=str(archive_path),
            remediation="Provide an existing archive and retry.",
        )
    if not tarfile.is_tarfile(archive_path):
        fail(
            ErrorCategory.VALIDATION,
            "The PostgreSQL migration archive is invalid.",
            operation="Import PostgreSQL registry",
            resource=str(archive_path),
            remediation="Provide a validated export archive and retry.",
        )
    if not _is_json(output):
        rprint(f"[bold]Importing from:[/bold] {escape(str(archive_path))}")
    try:
        with _progress(output, "Importing..."):
            result: ImportResult = asyncio.run(import_pg(PgConnParams(dsn=db_url), archive_path, _reporter(output)))
    except MigrationError as error:
        _handle_migration_error(error, "Import PostgreSQL registry")

    payload = {
        "migration_id": result.migration_id,
        "tables_imported": result.tables_imported,
        "rows_inserted": result.rows_inserted,
        "rows_skipped": result.rows_skipped,
        "total_inserted": sum(result.rows_inserted.values()),
        "total_skipped": sum(result.rows_skipped.values()),
        "duration_seconds": result.duration_seconds,
        "warnings": result.warnings,
    }
    if _is_json(output):
        output_json(payload)
        return
    rprint("\n[bold green]✓ Import complete[/bold green]")
    rprint(f"  Migration:  {escape(result.migration_id)}")
    rprint(f"  Tables:     {result.tables_imported}")
    rprint(f"  Inserted:   {payload['total_inserted']:,}")
    rprint(f"  Skipped:    {payload['total_skipped']:,}")
    rprint(f"  Duration:   {result.duration_seconds:.1f}s")
    if result.warnings:
        rprint("\n[yellow]Warnings:[/yellow]")
        for warning in result.warnings:
            rprint(f"  [yellow]⚠[/yellow]  {escape(warning)}")


# ── Validate command ─────────────────────────────────────


@migrate_app.command("validate")
def validate_cmd(
    archive: str = typer.Option(..., "--archive", "-a", help="Path to .tar.gz archive"),
    db_url: str | None = typer.Option(
        None,
        "--db-url",
        envvar="TARGET_DATABASE_URL",
        show_envvar=True,
        help="Optional database for cross-validation",
    ),
    output: Annotated[
        OutputMode, typer.Option("--output", "-o", help="Output format: table or json")
    ] = OutputMode.table,
) -> None:
    """Validate archive integrity and optionally compare against a database.

    Checks SHA-256 checksums for every table file in the archive. If --db-url
    is provided, also compares row counts between the archive and the live
    database to detect drift or partial imports.

    Examples:
        observal server migrate validate --archive backup.tar.gz
        observal server migrate validate --archive backup.tar.gz --output json
    """
    _require_pyarrow()
    archive_path = Path(archive).expanduser()
    if not archive_path.is_file():
        fail(
            ErrorCategory.NOT_FOUND,
            "The PostgreSQL migration archive was not found.",
            operation="Validate PostgreSQL migration",
            resource=str(archive_path),
            remediation="Provide an existing archive and retry.",
        )
    if not tarfile.is_tarfile(archive_path):
        fail(
            ErrorCategory.VALIDATION,
            "The PostgreSQL migration archive is invalid.",
            operation="Validate PostgreSQL migration",
            resource=str(archive_path),
            remediation="Provide an export archive and retry.",
        )
    try:
        with _progress(output, "Validating archive..."):
            result: ValidationResult = asyncio.run(
                validate_pg(PgConnParams(dsn=db_url) if db_url else None, archive_path, _reporter(output))
            )
    except MigrationError as error:
        _handle_migration_error(error, "Validate PostgreSQL migration")

    checksum_results = [
        {
            "table": item.table_name,
            "expected_checksum": item.expected_checksum,
            "actual_checksum": item.actual_checksum,
            "passed": item.passed,
        }
        for item in result.checksum_results
    ]
    if not result.archive_valid:
        fail(
            ErrorCategory.VALIDATION,
            "PostgreSQL migration checksum validation failed.",
            operation="Validate PostgreSQL migration",
            resource=str(archive_path),
            remediation="Discard the archive and export it again.",
        )
    comparisons = {
        table: {"archive_rows": counts[0], "database_rows": counts[1], "matches": counts[0] == counts[1]}
        for table, counts in (result.cross_db_results or {}).items()
    }
    payload = {
        "archive": str(archive_path),
        "valid": True,
        "checksums": checksum_results,
        "row_counts": comparisons,
        "row_count_mismatches": sum(not item["matches"] for item in comparisons.values()),
    }
    if _is_json(output):
        output_json(payload)
        return
    rprint("\n[bold]Checksum verification:[/bold]")
    for item in checksum_results:
        rprint(f"  {'[green]✓[/green]' if item['passed'] else '[red]✗[/red]'} {escape(str(item['table']))}")
    rprint("\n[green]✓ All checksums valid[/green]")
    if comparisons:
        rprint("\n[bold]Row count comparison:[/bold]")
        for table, item in comparisons.items():
            status = "[green]✓[/green]" if item["matches"] else "[yellow]≠[/yellow]"
            rprint(f"  {status} {escape(table)}: archive={item['archive_rows']}, db={item['database_rows']}")


# ── Export telemetry command ─────────────────────────────


@migrate_app.command("export-telemetry")
def export_telemetry_cmd(
    clickhouse_url: str = typer.Option(
        ..., "--clickhouse-url", envvar="CLICKHOUSE_URL", show_envvar=True, help="Source ClickHouse connection string"
    ),
    manifest: str = typer.Option(..., "--manifest", help="Path to Phase 1 migration_manifest.json"),
    output_dir: str = typer.Option(..., "--output-dir", help="New directory for exported Parquet files"),
    output: Annotated[
        OutputMode, typer.Option("--output", "-o", help="Output format: table or json")
    ] = OutputMode.table,
) -> None:
    """Export ClickHouse telemetry data to Parquet files.

    Phase 2 of migration: exports session, audit, security, and webhook telemetry
    tables as bounded, resumable Parquet chunks. Requires a completed Phase 1 export
    (the migration_manifest.json produced by 'observal server migrate export').

    Uses a time cutoff recorded at export start for consistency. The output
    directory must not already exist.

    Examples:
        observal server migrate export-telemetry --clickhouse-url clickhouses://localhost/observal --manifest ./migration_manifest.json --output-dir ./telemetry-export
    """
    _require_pyarrow()
    destination = Path(output_dir).expanduser()
    if destination.exists():
        fail(
            ErrorCategory.CONFLICT,
            "The telemetry export directory already exists.",
            operation="Export ClickHouse telemetry",
            resource=str(destination),
            remediation="Choose a new --output-dir so failed exports can be cleaned safely.",
        )
    _warn_clickhouse_cleartext(clickhouse_url, output)
    if not _is_json(output):
        rprint(f"[bold]Exporting telemetry to:[/bold] {escape(str(destination))}")
    try:
        result: TelemetryExportResult = asyncio.run(
            export_ch(ChConnParams(url=clickhouse_url), Path(manifest).expanduser(), destination, _reporter(output))
        )
    except MigrationError as error:
        _handle_migration_error(error, "Export ClickHouse telemetry")

    payload = {
        "directory": result.output_dir,
        "migration_id": result.migration_id,
        "tables": result.table_results,
        "total_rows": result.total_rows,
        "size_bytes": result.total_size_bytes,
        "duration_seconds": result.duration_seconds,
    }
    if _is_json(output):
        output_json(payload)
        return
    rprint("\n[bold green]✓ Telemetry export complete[/bold green]")
    rprint(f"  Directory:  {escape(result.output_dir)}")
    rprint(f"  Migration:  {escape(result.migration_id)}")
    rprint(f"  Rows:       {result.total_rows:,}")
    rprint(f"  Size:       {result.total_size_bytes / (1024 * 1024):.1f} MB")
    rprint(f"  Duration:   {result.duration_seconds:.1f}s")
    rprint("\n[yellow]Parquet files may contain sensitive trace data. Store them securely.[/yellow]")


# ── Import telemetry command ─────────────────────────────


@migrate_app.command("import-telemetry")
def import_telemetry_cmd(
    clickhouse_url: str = typer.Option(
        ...,
        "--clickhouse-url",
        envvar="TARGET_CLICKHOUSE_URL",
        show_envvar=True,
        help="Target ClickHouse connection string",
    ),
    input_dir: str = typer.Option(..., "--input-dir", help="Directory containing Parquet files"),
    output: Annotated[
        OutputMode, typer.Option("--output", "-o", help="Output format: table or json")
    ] = OutputMode.table,
) -> None:
    """Import Parquet telemetry files into target ClickHouse.

    Phase 2 import: loads bounded Parquet chunks into the target ClickHouse.
    Verifies checksums before importing and records completed chunk IDs and
    checksums so interrupted imports can resume without skipping unrelated data.

    Examples:
        observal server migrate import-telemetry --clickhouse-url clickhouses://localhost/observal --input-dir ./telemetry-export
        observal server migrate import-telemetry --clickhouse-url clickhouses://localhost/observal --input-dir ./telemetry-export --output json
    """
    _require_pyarrow()
    _warn_clickhouse_cleartext(clickhouse_url, output)
    input_path = Path(input_dir).expanduser()
    if not input_path.is_dir():
        fail(
            ErrorCategory.NOT_FOUND,
            "The telemetry migration directory was not found.",
            operation="Import ClickHouse telemetry",
            resource=str(input_path),
            remediation="Provide an existing telemetry export directory.",
        )
    if not _is_json(output):
        rprint(f"[bold]Importing telemetry from:[/bold] {escape(str(input_path))}")
    try:
        result: TelemetryImportResult = asyncio.run(
            import_ch(ChConnParams(url=clickhouse_url), input_path, _reporter(output))
        )
    except MigrationError as error:
        _handle_migration_error(error, "Import ClickHouse telemetry")

    payload = {
        "migration_id": result.migration_id,
        "tables_imported": result.tables_imported,
        "tables_skipped": result.tables_skipped,
        "rows_imported": result.rows_imported,
        "total_rows": sum(result.rows_imported.values()),
        "duration_seconds": result.duration_seconds,
        "warnings": result.warnings,
    }
    if _is_json(output):
        output_json(payload)
        return
    rprint("\n[bold green]✓ Telemetry import complete[/bold green]")
    rprint(f"  Migration:  {escape(result.migration_id)}")
    rprint(f"  Tables:     {result.tables_imported}")
    rprint(f"  Rows:       {payload['total_rows']:,}")
    rprint(f"  Duration:   {result.duration_seconds:.1f}s")
    if result.tables_skipped:
        rprint(f"  Skipped:    {', '.join(map(escape, result.tables_skipped))}")
    if result.warnings:
        rprint("\n[yellow]Warnings:[/yellow]")
        for warning in result.warnings:
            rprint(f"  [yellow]⚠[/yellow]  {escape(warning)}")


# ── Validate telemetry command ───────────────────────────


@migrate_app.command("validate-telemetry")
def validate_telemetry_cmd(
    input_dir: str = typer.Option(..., "--input-dir", help="Directory containing Parquet files"),
    clickhouse_url: str | None = typer.Option(
        None,
        "--clickhouse-url",
        envvar="TARGET_CLICKHOUSE_URL",
        show_envvar=True,
        help="Target ClickHouse for row count comparison",
    ),
    target_db_url: str | None = typer.Option(
        None,
        "--target-db-url",
        envvar="TARGET_DATABASE_URL",
        show_envvar=True,
        help="Target PostgreSQL for FK validation",
    ),
    output: Annotated[
        OutputMode, typer.Option("--output", "-o", help="Output format: table or json")
    ] = OutputMode.table,
) -> None:
    """Validate telemetry Parquet files and optionally check FK references.

    Verifies SHA-256 checksums for all Parquet files in the export directory.
    Optionally compares row counts against a live ClickHouse instance and
    checks foreign key references (agent_id, mcp_id, user_id) against
    PostgreSQL to detect orphaned telemetry records.

    Examples:
        observal server migrate validate-telemetry --input-dir ./telemetry-export
        observal server migrate validate-telemetry --input-dir ./telemetry-export --output json
    """
    _require_pyarrow()
    input_path = Path(input_dir).expanduser()
    if not input_path.is_dir():
        fail(
            ErrorCategory.NOT_FOUND,
            "The telemetry migration directory was not found.",
            operation="Validate ClickHouse telemetry",
            resource=str(input_path),
            remediation="Provide an existing telemetry export directory.",
        )
    if clickhouse_url:
        _warn_clickhouse_cleartext(clickhouse_url, output)
    if not _is_json(output):
        rprint(f"[bold]Validating telemetry in:[/bold] {escape(str(input_path))}")
    try:
        result: TelemetryValidationResult = asyncio.run(
            validate_ch(
                ChConnParams(url=clickhouse_url) if clickhouse_url else None,
                PgConnParams(dsn=target_db_url) if target_db_url else None,
                input_path,
                _reporter(output),
            )
        )
    except MigrationError as error:
        _handle_migration_error(error, "Validate ClickHouse telemetry")

    if not result.checksums_valid:
        fail(
            ErrorCategory.VALIDATION,
            "ClickHouse telemetry checksum validation failed.",
            operation="Validate ClickHouse telemetry",
            resource=str(input_path),
            remediation="Discard the export and create it again.",
        )
    row_counts = {
        table: {"manifest_rows": counts[0], "database_rows": counts[1], "matches": counts[0] == counts[1]}
        for table, counts in (result.row_count_results or {}).items()
    }
    orphan_groups = {
        key: value
        for key, value in (result.fk_results or {}).items()
        if not key.endswith("_truncated") and isinstance(value, list) and value
    }
    payload = {
        "directory": str(input_path),
        "valid": True,
        "checksums": result.checksum_results,
        "row_counts": row_counts,
        "row_count_mismatches": sum(not item["matches"] for item in row_counts.values()),
        "foreign_keys": result.fk_results or {},
        "orphan_groups": len(orphan_groups),
    }
    if _is_json(output):
        output_json(payload)
        return
    rprint("\n[bold]Checksum verification:[/bold]")
    for filename, passed in result.checksum_results.items():
        status = "[green]✓[/green]" if passed else "[red]✗[/red]"
        rprint(f"  {status} {escape(filename)}")
    rprint("\n[green]✓ All checksums valid[/green]")
    if row_counts:
        rprint("\n[bold]Row count comparison:[/bold]")
        for table, item in row_counts.items():
            status = "[green]✓[/green]" if item["matches"] else "[yellow]≠[/yellow]"
            rprint(f"  {status} {escape(table)}: manifest={item['manifest_rows']}, db={item['database_rows']}")
    if result.fk_results:
        rprint("\n[bold]FK validation:[/bold]")
        for key, value in result.fk_results.items():
            if not key.endswith("_truncated") and isinstance(value, list):
                rprint(
                    f"  {'[yellow]⚠[/yellow]' if value else '[green]✓[/green]'} {escape(key)}: {len(value)} orphaned"
                )
