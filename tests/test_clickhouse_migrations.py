# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def _resp(text: str = "", status_code: int = 200):
    return SimpleNamespace(status_code=status_code, text=text)


_BASELINE_TABLE_ROWS = "".join(
    f'{{"name":"{name}"}}\n'
    for name in [
        "audit_log",
        "layer_snapshots",
        "security_events",
        "session_events",
        "session_stats_agg",
        "webhook_deliveries",
    ]
)


def test_production_activity_migration_matches_pinned_phase0_ddl():
    from services.clickhouse.migrations import _split_sql

    root = Path(__file__).resolve().parents[1]
    production = _split_sql((root / "observal-server/clickhouse/migrations/007_component_activity.sql").read_text())
    pinned = _split_sql(
        (root / "tests/fixtures/component_insights/clickhouse/projection_tables.sql")
        .read_text()
        .replace("{prefix}", "")
    )[2:]
    assert len(production) == 2
    assert production == pinned
    activity_key = production[0].split("ORDER BY (")[1]
    assert "project_id, user_id, harness, session_id, projection_version" in activity_key
    assert "projection_generation, source_line_offset, source_block_key" in activity_key
    assert "component_id" not in activity_key
    assert "source_revision" in production[1] and "unknown_result_count" in production[1]


def test_split_sql_strips_spdx_and_keeps_quoted_semicolon():
    from services.clickhouse.migrations import _split_sql

    sql = """# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
SELECT 'a;b';
SELECT 2;
"""

    assert _split_sql(sql) == ["SELECT 'a;b'", "SELECT 2"]


@pytest.mark.asyncio
async def test_run_clickhouse_migrations_skips_applied_files(tmp_path, monkeypatch):
    from services.clickhouse import migrations

    (tmp_path / "001_done.sql").write_text("SELECT 1;\n")
    (tmp_path / "002_new.sql").write_text("SELECT 2;\nSELECT 3;\n")

    calls: list[tuple[str, dict | None]] = []

    async def fake_query_server(_sql: str):
        return _resp()

    async def fake_query(sql: str, params: dict | None = None, **_kwargs):
        calls.append((sql, params))
        if sql.startswith("SELECT version"):
            return _resp('{"version":"001_done"}\n')
        return _resp()

    monkeypatch.setattr(migrations, "MIGRATIONS_DIR", tmp_path)
    monkeypatch.setattr(migrations, "_query_server", fake_query_server)
    monkeypatch.setattr(migrations._client, "clickhouse_health", AsyncMock(return_value=True))
    monkeypatch.setattr(migrations._client, "_query", fake_query)

    await migrations.run_clickhouse_migrations()

    executed_sql = [sql for sql, _params in calls]
    assert "SELECT 1" not in executed_sql
    assert "SELECT 2" in executed_sql
    assert "SELECT 3" in executed_sql
    assert any(sql.startswith("INSERT INTO clickhouse_schema_migrations") for sql in executed_sql)


@pytest.mark.asyncio
async def test_run_clickhouse_migrations_stamps_existing_baseline(tmp_path, monkeypatch):
    from services.clickhouse import migrations

    (tmp_path / "001_baseline.sql").write_text("SELECT 1;\n")
    (tmp_path / "002_new.sql").write_text("SELECT 2;\n")

    calls: list[tuple[str, dict | None]] = []

    async def fake_query_server(_sql: str):
        return _resp()

    async def fake_query(sql: str, params: dict | None = None, **_kwargs):
        calls.append((sql, params))
        if sql.startswith("SELECT version"):
            return _resp()
        if "FROM system.tables" in sql:
            return _resp(_BASELINE_TABLE_ROWS)
        return _resp()

    monkeypatch.setattr(migrations, "MIGRATIONS_DIR", tmp_path)
    monkeypatch.setattr(migrations, "_query_server", fake_query_server)
    monkeypatch.setattr(migrations._client, "clickhouse_health", AsyncMock(return_value=True))
    monkeypatch.setattr(migrations._client, "_query", fake_query)

    await migrations.run_clickhouse_migrations()

    executed_sql = [sql for sql, _params in calls]
    assert "SELECT 1" not in executed_sql
    assert "SELECT 2" in executed_sql
    inserts = [params for sql, params in calls if sql.startswith("INSERT INTO clickhouse_schema_migrations")]
    assert {params["param_version"] for params in inserts if params} == {"001_baseline", "002_new"}
