# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""Opt-in production 007 migration-runner proof on an isolated database only.

Never point this at the existing sample (`observal`) or run the migration
runner on the sample. The dedicated `observal_phase22_ci` database in the
isolated :18123 proof container is intentionally retained after the test.
"""

from __future__ import annotations

import json
import os
import uuid
from urllib.parse import urlparse

import pytest

from services.clickhouse import client, migrations

URL = os.getenv("OBSERVAL_CH_PHASE22_URL")
pytestmark = pytest.mark.skipif(not URL, reason="opt-in: isolated Phase 2.2 ClickHouse migration proof")
DATABASE = "observal_phase22_ci"


async def _rows(sql: str) -> list[dict]:
    response = await client._query(sql + " FORMAT JSONEachRow")
    assert response.status_code == 200
    return [json.loads(line) for line in response.text.splitlines() if line]


@pytest.mark.asyncio
async def test_production_runner_applies_activity_tables_and_is_idempotent():
    parsed = urlparse(URL or "")
    assert parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and parsed.port == 18123
    assert parsed.path == f"/{DATABASE}" and not parsed.query and not parsed.fragment
    assert client.CLICKHOUSE_HTTP == "http://127.0.0.1:18123"
    assert client.CLICKHOUSE_DB == DATABASE and client.CLICKHOUSE_USER == "proof"
    assert migrations.MIGRATIONS_DIR.is_dir()

    try:
        await migrations.run_clickhouse_migrations()
        versions = await _rows("SELECT version FROM clickhouse_schema_migrations")
        assert {item["version"] for item in versions} >= {"006_layer_components", "007_component_activity"}
        assert sum(item["version"] == "007_component_activity" for item in versions) == 1

        tables = await _rows(
            "SELECT name, engine, sorting_key FROM system.tables "
            f"WHERE database = '{DATABASE}' AND name IN ('component_activity', 'component_activity_publications')"
        )
        assert {row["name"] for row in tables} == {"component_activity", "component_activity_publications"}
        activity = next(table for table in tables if table["name"] == "component_activity")
        publications = next(table for table in tables if table["name"] == "component_activity_publications")
        assert activity["engine"] == "ReplacingMergeTree"
        assert activity["sorting_key"] == (
            "project_id, user_id, harness, session_id, projection_version, "
            "projection_generation, source_line_offset, source_block_key"
        )
        assert "component_id" not in activity["sorting_key"]
        assert publications["engine"] == "MergeTree"
        assert publications["sorting_key"] == (
            "project_id, user_id, harness, session_id, projection_version, projection_generation, status"
        )

        columns = await _rows(
            "SELECT table, name, type, default_expression FROM system.columns "
            f"WHERE database = '{DATABASE}' AND table IN ('component_activity', 'component_activity_publications')"
        )
        actual = {(item["table"], item["name"]): item for item in columns}
        activity_fields = {
            "project_id",
            "user_id",
            "harness",
            "session_id",
            "projection_version",
            "projection_generation",
            "source_line_offset",
            "source_block_key",
            "source_line_hash",
            "layer_hash",
            "component_type",
            "component_id",
            "component_version_id",
            "tool_name",
            "tool_use_id",
            "event_time",
            "result_state",
            "attribution_method",
            "matcher_version",
            "extractor_version",
            "row_revision",
        }
        publication_fields = {
            "project_id",
            "user_id",
            "harness",
            "session_id",
            "projection_version",
            "projection_generation",
            "status",
            "source_revision",
            "candidate_count",
            "attributed_count",
            "collision_count",
            "unmatched_count",
            "unknown_result_count",
            "attempted_at",
        }
        # 008 adds one column to each table; the 007 sorting keys above are unchanged,
        # and existing rows default to MCP calls and MCP publications.
        activity_fields.add("evidence_kind")
        publication_fields.add("evidence_type")
        assert {name for table, name in actual if table == "component_activity"} == activity_fields
        assert {name for table, name in actual if table == "component_activity_publications"} == publication_fields
        assert actual[("component_activity", "evidence_kind")]["default_expression"] == "'call'"
        assert actual[("component_activity_publications", "evidence_type")]["default_expression"] == "'mcp'"
        assert actual[("component_activity", "row_revision")]["type"] == "UInt64"
        assert actual[("component_activity", "row_revision")]["default_expression"] == "1"
        assert actual[("component_activity_publications", "source_revision")]["default_expression"] == "''"
        assert actual[("component_activity_publications", "candidate_count")]["type"] == "UInt32"
        assert actual[("component_activity_publications", "attempted_at")]["type"] == "DateTime64(3, 'UTC')"

        # A source session with no candidate calls still gets a complete
        # publication marker without manufacturing an activity row.
        project = "phase22-" + uuid.uuid4().hex
        response = await client._query(
            "INSERT INTO component_activity_publications "
            "(project_id, user_id, harness, session_id, projection_version, projection_generation, status) "
            "VALUES ({project:String}, 'proof', 'claude-code', 'zero-call', 1, 1, 'complete')",
            {"param_project": project},
        )
        assert response.status_code == 200
        marker = await _rows(
            "SELECT status, source_revision, candidate_count, attributed_count, collision_count, "
            "unmatched_count, unknown_result_count FROM component_activity_publications "
            f"WHERE project_id = '{project}'"
        )
        assert len(marker) == 1 and marker[0]["status"] == "complete" and marker[0]["source_revision"] == ""
        assert all(
            int(marker[0][field]) == 0
            for field in (
                "candidate_count",
                "attributed_count",
                "collision_count",
                "unmatched_count",
                "unknown_result_count",
            )
        )
        assert (
            int((await _rows(f"SELECT count() AS n FROM component_activity WHERE project_id = '{project}'"))[0]["n"])
            == 0
        )

        await migrations.run_clickhouse_migrations()
        versions = await _rows("SELECT version FROM clickhouse_schema_migrations")
        assert sum(item["version"] == "007_component_activity" for item in versions) == 1
    finally:
        if client._client is not None:
            await client._client.aclose()
            client._client = None
