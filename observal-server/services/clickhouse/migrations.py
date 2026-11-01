# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Versioned ClickHouse SQL migrations."""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger as optic

import services.clickhouse.client as _client

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "clickhouse" / "migrations"
MIGRATIONS_TABLE = "clickhouse_schema_migrations"
BASELINE_VERSION = "001_baseline"
BASELINE_NAME = f"{BASELINE_VERSION}.sql"
BASELINE_TABLES = frozenset(
    {
        "audit_log",
        "layer_snapshots",
        "security_events",
        "session_events",
        "session_stats_agg",
        "webhook_deliveries",
    }
)


def _strip_sql_comments(sql: str) -> str:
    return "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith(("#", "--")))


def _split_sql(sql: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False

    for char in _strip_sql_comments(sql):
        current.append(char)
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == ";":
            stmt = "".join(current).strip().rstrip(";").strip()
            if stmt:
                statements.append(stmt)
            current = []

    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


async def _query_server(sql: str):
    client = _client._get_client()
    return await client.post(
        _client.CLICKHOUSE_HTTP,
        content=sql,
        headers=_client.credential_headers(),
    )


async def _ensure_database() -> None:
    resp = await _query_server(f"CREATE DATABASE IF NOT EXISTS {_client.CLICKHOUSE_DB}")
    if resp.status_code >= 400:
        raise RuntimeError(f"ClickHouse database setup failed: {_response_text(resp)[:200]}")


async def _ensure_migrations_table() -> None:
    resp = await _client._query(
        f"""CREATE TABLE IF NOT EXISTS {MIGRATIONS_TABLE} (
            version String,
            name String,
            applied_at DateTime64(3, 'UTC') DEFAULT now()
        ) ENGINE = MergeTree()
        ORDER BY version"""
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"ClickHouse migration table setup failed: {_response_text(resp)[:200]}")


def _response_text(resp) -> str:
    text = getattr(resp, "text", "")
    return text if isinstance(text, str) else ""


async def _applied_versions() -> set[str]:
    resp = await _client._query(f"SELECT version FROM {MIGRATIONS_TABLE} FORMAT JSONEachRow")
    text = _response_text(resp)
    if resp.status_code >= 400:
        raise RuntimeError(f"ClickHouse migration lookup failed: {text[:200]}")
    if not text.strip():
        return set()
    return {json.loads(line)["version"] for line in text.splitlines() if line.strip()}


async def _record_applied(version: str, name: str) -> None:
    resp = await _client._query(
        f"INSERT INTO {MIGRATIONS_TABLE} (version, name) VALUES ({{version:String}}, {{name:String}})",
        {"param_version": version, "param_name": name},
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"ClickHouse migration record failed: {_response_text(resp)[:200]}")


async def _existing_tables() -> set[str]:
    names = ", ".join(f"'{table}'" for table in sorted(BASELINE_TABLES))
    resp = await _client._query(
        f"SELECT name FROM system.tables WHERE database = currentDatabase() AND name IN ({names}) FORMAT JSONEachRow"
    )
    text = _response_text(resp)
    if resp.status_code >= 400:
        raise RuntimeError(f"ClickHouse table lookup failed: {text[:200]}")
    if not text.strip():
        return set()
    return {json.loads(line)["name"] for line in text.splitlines() if line.strip()}


async def _stamp_baseline_if_present(applied: set[str]) -> set[str]:
    if applied or not (MIGRATIONS_DIR / BASELINE_NAME).exists():
        return applied

    existing = await _existing_tables()
    if not BASELINE_TABLES.issubset(existing):
        return applied

    optic.info("stamping existing ClickHouse baseline as applied")
    await _record_applied(BASELINE_VERSION, BASELINE_NAME)
    return {BASELINE_VERSION}


async def _run_file(path: Path) -> None:
    version = path.stem
    statements = _split_sql(path.read_text())
    optic.info("applying ClickHouse migration {} ({} statements)", path.name, len(statements))
    for stmt in statements:
        resp = await _client._query(stmt, {"wait_for_async_insert": "1"})
        if resp.status_code >= 400:
            raise RuntimeError(f"ClickHouse migration {path.name} failed: {_response_text(resp)[:200]}")
    await _record_applied(version, path.name)


async def run_clickhouse_migrations() -> None:
    """Apply pending ClickHouse migrations from ``observal-server/clickhouse/migrations``."""
    await _ensure_database()
    if not await _client.clickhouse_health():
        raise RuntimeError(f"ClickHouse unreachable at {_client.CLICKHOUSE_HTTP}")

    await _ensure_migrations_table()
    applied = await _stamp_baseline_if_present(await _applied_versions())
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.stem in applied:
            continue
        await _run_file(path)
    optic.info("ClickHouse migrations complete")


if __name__ == "__main__":
    import asyncio

    asyncio.run(run_clickhouse_migrations())
