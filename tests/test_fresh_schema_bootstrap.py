# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Fresh and versioned entrypoint paths must never stamp an unverified schema."""

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import schema_bootstrap

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
async def test_bootstrap_installs_sequence_search_index_and_all_listing_cleanup_triggers():
    connection = SimpleNamespace(execute=AsyncMock())

    await schema_bootstrap.bootstrap_fresh_schema_objects(connection)

    statements = [str(call.args[0]) for call in connection.execute.await_args_list]
    assert len(statements) == 6
    assert "CREATE SEQUENCE IF NOT EXISTS projection_generation_seq" in statements[0]
    assert "CREATE INDEX IF NOT EXISTS ix_discovery_entries_search_trgm" in statements[1]
    assert "ON discovery_entries USING gin (search_document gin_trgm_ops)" in statements[1]
    assert "CREATE OR REPLACE FUNCTION insight_report_cleanup_on_listing_delete" in statements[2]
    for kind, statement in zip(("mcp", "skill", "hook"), statements[3:], strict=True):
        assert f"trg_{kind}_insight_report_cleanup" in statement
        assert f"AFTER DELETE ON {kind}_listings" in statement
        assert "IF NOT EXISTS" in statement


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answers", "expected"),
    [
        ([False, False], "fresh"),
        ([False, True], "unversioned"),
        ([True, None], "unversioned"),
        ([True, "049_component_privacy"], "existing"),
    ],
)
async def test_schema_state_requires_an_empty_database_or_a_recorded_revision(answers, expected):
    connection = SimpleNamespace(scalar=AsyncMock(side_effect=answers))
    assert await schema_bootstrap.schema_state(connection) == expected
    queries = [str(call.args[0]) for call in connection.scalar.await_args_list]
    assert "pg_catalog.pg_tables" in queries[0] and "current_schema()" in queries[0]
    assert ("SELECT version_num FROM alembic_version" in queries[1]) == bool(answers[0])
    if not answers[0]:
        for catalog in ("pg_class", "pg_type", "pg_proc"):
            assert f"pg_catalog.{catalog}" in queries[1]
        assert "pg_catalog.pg_depend" in queries[1]  # Ignore preinstalled extension objects.


@pytest.mark.asyncio
async def test_init_checks_state_before_any_ddl(monkeypatch):
    connection = SimpleNamespace(execute=AsyncMock(), run_sync=AsyncMock())
    state = AsyncMock(return_value="unversioned")
    monkeypatch.setattr(schema_bootstrap, "schema_state", state)
    with pytest.raises(RuntimeError, match="nonempty/unversioned"):
        await schema_bootstrap.initialize_fresh_schema(connection)
    connection.execute.assert_not_awaited()
    connection.run_sync.assert_not_awaited()

    state.return_value = "fresh"
    bootstrap = AsyncMock()
    monkeypatch.setattr(schema_bootstrap, "bootstrap_fresh_schema_objects", bootstrap)
    await schema_bootstrap.initialize_fresh_schema(connection)
    assert "CREATE EXTENSION IF NOT EXISTS pg_trgm" in str(connection.execute.await_args.args[0])
    connection.run_sync.assert_awaited_once_with(schema_bootstrap.Base.metadata.create_all)
    bootstrap.assert_awaited_once_with(connection)


def _entrypoint_with_stub(
    tmp_path: Path, state: str, *, fail: str = ""
) -> tuple[subprocess.CompletedProcess, list[str]]:
    """Run the real shell branches with a fake Python command, without touching a DB."""
    executable = tmp_path / "python-stub"
    executable.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$BOOTSTRAP_CALL_LOG"\n'
        'if [ "$1 $2 $3" = "-m services.schema_bootstrap state" ]; then\n'
        '  printf "%s\\n" "$BOOTSTRAP_STATE"\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$1 $2 $3" = "-m services.schema_bootstrap init" ] && '
        '[ "$BOOTSTRAP_FAIL" = "init" ]; then exit 21; fi\n'
        'if [ "$1 $2 $3 $4" = "-m alembic upgrade head" ] && '
        '[ "$BOOTSTRAP_FAIL" = "upgrade" ]; then exit 22; fi\n'
    )
    executable.chmod(0o700)
    script = tmp_path / "entrypoint.sh"
    source = (ROOT / "docker" / "entrypoint.sh").read_text()
    script.write_text(source.replace("/app/.venv/bin/python", str(executable)))
    log = tmp_path / "commands.log"
    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        check=False,
        env=os.environ | {"BOOTSTRAP_CALL_LOG": str(log), "BOOTSTRAP_STATE": state, "BOOTSTRAP_FAIL": fail},
    )
    return result, log.read_text().splitlines()


def test_entrypoint_fresh_path_initializes_then_stamps_before_clickhouse(tmp_path):
    result, calls = _entrypoint_with_stub(tmp_path, "fresh")
    assert result.returncode == 0, result.stderr
    assert calls == [
        "-m services.schema_bootstrap state",
        "-m services.schema_bootstrap init",
        "-m alembic stamp head",
        "-m jobs.review_backfill",
        "-m services.clickhouse.migrations",
    ]


def test_entrypoint_existing_path_upgrades_without_creating_or_stamping(tmp_path):
    result, calls = _entrypoint_with_stub(tmp_path, "existing")
    assert result.returncode == 0, result.stderr
    assert calls == [
        "-m services.schema_bootstrap state",
        "-m alembic upgrade head",
        "-m jobs.review_backfill",
        "-m services.clickhouse.migrations",
    ]


def test_entrypoint_does_not_stamp_when_existing_upgrade_fails(tmp_path):
    result, calls = _entrypoint_with_stub(tmp_path, "existing", fail="upgrade")
    assert result.returncode != 0
    assert calls == ["-m services.schema_bootstrap state", "-m alembic upgrade head"]


def test_entrypoint_refuses_unversioned_database_without_ddl(tmp_path):
    result, calls = _entrypoint_with_stub(tmp_path, "unversioned")
    assert result.returncode != 0
    assert calls == ["-m services.schema_bootstrap state"]
    assert "refusing to stamp" in result.stderr


def test_entrypoint_does_not_stamp_if_fresh_init_fails(tmp_path):
    result, calls = _entrypoint_with_stub(tmp_path, "fresh", fail="init")
    assert result.returncode != 0
    assert calls == ["-m services.schema_bootstrap state", "-m services.schema_bootstrap init"]


_PG_URL = os.environ.get("OBSERVAL_TEST_POSTGRES_URL")


@pytest.mark.skipif(not _PG_URL, reason="requires a disposable PostgreSQL (OBSERVAL_TEST_POSTGRES_URL)")
@pytest.mark.asyncio
async def test_extension_owned_relations_do_not_make_a_schema_unversioned():
    """A preinstalled extension that ships views (pg_buffercache) must not turn an empty schema away."""
    import uuid

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    assert _PG_URL
    schema = f"ext_{uuid.uuid4().hex[:12]}"
    engine = create_async_engine(_PG_URL)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            await connection.execute(text(f'SET search_path TO "{schema}"'))
            await connection.execute(text(f'CREATE EXTENSION pg_buffercache SCHEMA "{schema}"'))
            assert await schema_bootstrap.schema_state(connection) == "fresh"
            await connection.execute(text("CREATE TABLE unrelated (id integer)"))
            assert await schema_bootstrap.schema_state(connection) == "unversioned"
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    finally:
        await engine.dispose()
