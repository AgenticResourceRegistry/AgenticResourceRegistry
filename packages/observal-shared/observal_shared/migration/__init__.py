# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Shared Migration Service: export, import, and validation for PostgreSQL and ClickHouse.

Public API entry points:
    export_pg   — PostgreSQL snapshot export to .tar.gz archive
    export_ch   — ClickHouse telemetry export to bounded Parquet chunks
    import_pg   — Import PG archive into target database
    import_ch   — Import telemetry Parquet files into target ClickHouse
    validate_pg — Validate PG archive checksums and row counts
    validate_ch — Validate telemetry checksums, row counts, and FK references

This module contains NO typer, NO rich, and NO typer.Exit.
Progress is reported through an injected ProgressReporter protocol.
Errors are raised as plain domain exceptions.
"""

from observal_shared.migration.ch_export import export_ch
from observal_shared.migration.ch_import import import_ch
from observal_shared.migration.connections import ChConnParams, PgConnParams
from observal_shared.migration.constants import DEFAULT_PROJECT_ID
from observal_shared.migration.exceptions import (
    ArtifactValidationError,
    ChecksumMismatchError,
    ConnectionFailedError,
    MigrationError,
    PrerequisiteError,
)
from observal_shared.migration.pg_export import export_pg
from observal_shared.migration.pg_import import import_pg
from observal_shared.migration.progress import NullReporter, ProgressReporter
from observal_shared.migration.results import (
    ChecksumResult,
    ExportResult,
    ImportResult,
    TelemetryExportResult,
    TelemetryImportResult,
    TelemetryValidationResult,
    ValidationResult,
)
from observal_shared.migration.validation import validate_ch, validate_pg

__all__ = [
    "DEFAULT_PROJECT_ID",
    "ArtifactValidationError",
    "ChConnParams",
    "ChecksumMismatchError",
    "ChecksumResult",
    "ConnectionFailedError",
    # Results
    "ExportResult",
    "ImportResult",
    # Exceptions
    "MigrationError",
    "NullReporter",
    # Connection params
    "PgConnParams",
    "PrerequisiteError",
    # Progress
    "ProgressReporter",
    "TelemetryExportResult",
    "TelemetryImportResult",
    "TelemetryValidationResult",
    "ValidationResult",
    "export_ch",
    # Entry points
    "export_pg",
    "import_ch",
    "import_pg",
    "validate_ch",
    "validate_pg",
]
