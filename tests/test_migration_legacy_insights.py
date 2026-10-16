# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Import older insight data without inventing scope or report subjects."""

import json
from unittest.mock import AsyncMock

import pytest

from observal_shared.migration.exceptions import MigrationError
from observal_shared.migration.pg_import import _insert_table


@pytest.mark.asyncio
async def test_import_skips_unscoped_legacy_facets_but_keeps_scoped_rows(tmp_path):
    archive = tmp_path / "insight_session_facets.jsonl"
    legacy = {"id": "00000000-0000-0000-0000-000000000001", "session_id": "shared"}
    null_scope = {**legacy, "id": "00000000-0000-0000-0000-000000000002", "project_id": None}
    scoped = {
        **legacy,
        "id": "00000000-0000-0000-0000-000000000003",
        "project_id": "project-a",
        "user_id": "user-a",
        "harness": "pi",
    }
    archive.write_text("\n".join(json.dumps(row) for row in (legacy, null_scope, scoped)) + "\n")
    conn = AsyncMock()
    conn.execute.return_value = "INSERT 0 1"
    columns = {
        "id": "uuid",
        "session_id": "varchar",
        "project_id": "varchar",
        "user_id": "varchar",
        "harness": "varchar",
    }

    inserted, skipped, warnings = await _insert_table(conn, "insight_session_facets", archive, columns)

    assert (inserted, skipped) == (1, 2)
    assert len(warnings) == 1
    assert "skipped 2 unscoped legacy cache rows" in warnings[0]
    conn.execute.assert_awaited_once()
    assert "project-a" in conn.execute.await_args.args


@pytest.mark.asyncio
async def test_import_legacy_facets_to_legacy_schema_does_not_drop_rows(tmp_path):
    archive = tmp_path / "insight_session_facets.jsonl"
    archive.write_text(json.dumps({"id": "00000000-0000-0000-0000-000000000001"}) + "\n")
    conn = AsyncMock()
    conn.execute.return_value = "INSERT 0 1"

    inserted, skipped, warnings = await _insert_table(conn, "insight_session_facets", archive, {"id": "uuid"})

    assert (inserted, skipped, warnings) == (1, 0, [])
    conn.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_import_classifies_only_legacy_agent_reports(tmp_path):
    archive = tmp_path / "insight_reports.jsonl"
    legacy = {"id": "00000000-0000-0000-0000-000000000001", "agent_id": "00000000-0000-0000-0000-000000000002"}
    component = {
        "id": "00000000-0000-0000-0000-000000000003",
        "agent_id": None,
        "subject_type": "component",
        "component_type": "mcp",
        "component_id": "00000000-0000-0000-0000-000000000004",
    }
    archive.write_text("\n".join(json.dumps(row) for row in (legacy, component)) + "\n")
    conn = AsyncMock()
    conn.execute.return_value = "INSERT 0 1"
    columns = {
        "id": "uuid",
        "agent_id": "uuid",
        "subject_type": "varchar",
        "component_type": "varchar",
        "component_id": "uuid",
    }

    inserted, skipped, warnings = await _insert_table(conn, "insight_reports", archive, columns)

    assert (inserted, skipped) == (2, 0)
    assert warnings == ["insight_reports: classified 1 legacy rows as Agent reports"]
    assert conn.execute.await_count == 2
    assert "agent" in conn.execute.await_args_list[0].args
    assert "component" in conn.execute.await_args_list[1].args


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row",
    [
        {"agent_id": None},
        {"agent_id": "00000000-0000-0000-0000-000000000002", "subject_type": None},
        {"agent_id": "00000000-0000-0000-0000-000000000002", "component_type": "mcp"},
    ],
)
async def test_import_does_not_guess_ambiguous_report_subject(tmp_path, row):
    archive = tmp_path / "insight_reports.jsonl"
    archive.write_text(json.dumps({"id": "00000000-0000-0000-0000-000000000001", **row}) + "\n")
    conn = AsyncMock()

    with pytest.raises(MigrationError, match="cannot identify a report without subject_type"):
        await _insert_table(
            conn, "insight_reports", archive, {"id": "uuid", "agent_id": "uuid", "subject_type": "varchar"}
        )

    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_import_agent_reports_to_legacy_schema_does_not_add_subject(tmp_path):
    archive = tmp_path / "insight_reports.jsonl"
    archive.write_text(json.dumps({"id": "00000000-0000-0000-0000-000000000001"}) + "\n")
    conn = AsyncMock()
    conn.execute.return_value = "INSERT 0 1"

    inserted, skipped, warnings = await _insert_table(conn, "insight_reports", archive, {"id": "uuid"})

    assert (inserted, skipped, warnings) == (1, 0, [])
    conn.execute.assert_awaited_once()
