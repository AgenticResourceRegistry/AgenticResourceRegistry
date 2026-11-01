# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Stub test file for frontend component tests (10.4).

Frontend component tests for the Migration_Panel require React Testing Library
and vitest, and are run separately via the web/ package test infrastructure:

    cd web && pnpm test

Components to test:
- MigrateButton renders only for super_admin and is distinct from ExportDropdown
- ExportDropdown is preserved and unchanged
- Dialog tabs (Export / Import / Validate)
- Active job state transitions: form → progress → result
- Export form shows only postgres/both, disabled clickhouse with tooltip
- Import form submits artifacts without target identity controls

Requirements: 1.1, 1.2, 1.3, 1.4, 3.9, 4.8, 6.3, 6.6, 7.7
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ROOT / "web/src/pages/admin/dashboard/components/migrate-form-fields.tsx"
IMPORT_FORM = ROOT / "web/src/pages/admin/dashboard/components/migrate-import-form.tsx"
VALIDATE_FORM = ROOT / "web/src/pages/admin/dashboard/components/migrate-validate-form.tsx"


class TestMigrationArtifactPicker:
    """Source-level contracts for the migration artifact picker."""

    def test_picker_adds_sequential_selections_and_can_remove_files(self):
        source = FIELDS.read_text(encoding="utf-8")

        assert "const next = [...files]" in source
        assert 'event.target.value = ""' in source
        assert "removeFile" in source
        assert "Selected migration artifacts" in source

    def test_both_scope_requires_registry_and_telemetry(self):
        source = FIELDS.read_text(encoding="utf-8")

        assert "Select both the PostgreSQL registry archive and the telemetry archive." in source
        assert "artifactSelectionError" in source

    def test_import_and_validate_disable_submission_until_selection_is_valid(self):
        for path in (IMPORT_FORM, VALIDATE_FORM):
            source = path.read_text(encoding="utf-8")
            assert "artifactSelectionError(files, scope)" in source
            assert "Boolean(selectionError)" in source
