# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Packaged init must not stamp head when a migration reports failure."""

import os
import shlex
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models.mcp import ListingStatus
from tests import discovery_support as ds


def test_failed_alembic_upgrade_aborts_before_clickhouse_or_stamp(tmp_path):
    source = (Path(__file__).resolve().parents[1] / "docker" / "entrypoint.sh").read_text()
    calls = tmp_path / "calls"
    shim = tmp_path / "python"
    shim.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$1\" >> '{calls}'\n"
        'if [ "$1" = "-c" ]; then echo versioned; exit 0; fi\n'
        'if [ "$1" = "-m" ] && [ "$2" = "alembic" ]; then exit 19; fi\n'
        "exit 0\n"
    )
    shim.chmod(0o755)
    script = tmp_path / "entrypoint.sh"
    script.write_text(source.replace("/app/.venv/bin/python", str(shim)))
    result = subprocess.run(["bash", str(script)], capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert "Initialization complete" not in result.stdout
    assert "no revision was stamped" in result.stdout
    invocations = calls.read_text().splitlines()
    assert len(invocations) == 2
    assert invocations[1] == "-m"
    assert not any("stamp" in line or "clickhouse" in line for line in invocations)


def test_alembic_accepts_escaped_database_url_without_a_connection():
    """URL-escaped credentials must survive ConfigParser during offline migrations."""
    server = Path(__file__).resolve().parents[1] / "observal-server"
    env = {**os.environ, "DATABASE_URL": "postgresql+asyncpg://user:p%40ss%25word@localhost/example"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "v1_baseline", "--sql"],
        cwd=server,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-500:]
    assert "v1_baseline" in result.stdout


def test_unversioned_existing_schema_refuses_without_upgrade(tmp_path):
    source = (Path(__file__).resolve().parents[1] / "docker" / "entrypoint.sh").read_text()
    calls = tmp_path / "calls"
    shim = tmp_path / "python"
    shim.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$1\" >> '{calls}'\n"
        'if [ "$1" = "-c" ]; then echo unversioned; exit 0; fi\n'
        "exit 0\n"
    )
    shim.chmod(0o755)
    script = tmp_path / "entrypoint.sh"
    script.write_text(source.replace("/app/.venv/bin/python", str(shim)))
    result = subprocess.run(["bash", str(script)], capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert "refusing to stamp" in result.stdout
    assert calls.read_text().splitlines() == ["-c"]


@pytest.mark.asyncio
async def test_actual_packaged_init_from_empty_postgres_creates_revision_and_restarts(tmp_path):
    configured = os.environ.get("OBSERVAL_TEST_POSTGRES_URL")
    if not configured:
        pytest.skip("Set OBSERVAL_TEST_POSTGRES_URL to a disposable PostgreSQL database")
    url = make_url(configured)
    database = "skill_fresh_" + uuid.uuid4().hex[:12]
    admin = create_async_engine(url.set(database=url.database), isolation_level="AUTOCOMMIT")
    fresh_url = url.set(database=database)
    source = (Path(__file__).resolve().parents[1] / "docker" / "entrypoint.sh").read_text()
    shim = tmp_path / "python"
    shim.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-m" ] && [ "$2" = "services.clickhouse.migrations" ]; then exit 0; fi\n'
        f'exec {shlex.quote(sys.executable)} "$@"\n'
    )
    shim.chmod(0o755)
    script = tmp_path / "entrypoint.sh"
    script.write_text(source.replace("/app/.venv/bin/python", str(shim)))
    env = {**os.environ, "DATABASE_URL": fresh_url.render_as_string(hide_password=False)}
    server = str(Path(__file__).resolve().parents[1] / "observal-server")
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{database}"'))
    try:
        for _ in range(2):  # The second run must treat it as a versioned installation.
            result = subprocess.run(
                ["bash", str(script)], cwd=server, env=env, capture_output=True, text=True, check=False
            )
            assert result.returncode == 0, result.stderr[-1000:] + result.stdout[-1000:]
            assert "Initialization complete" in result.stdout
        fresh = create_async_engine(fresh_url)
        try:
            async with fresh.connect() as conn:
                assert await conn.scalar(text("SELECT version_num FROM alembic_version")) == "040_mcp_webhook_syncs"
                assert await conn.scalar(text("SELECT COUNT(*) FROM skill_versions")) == 0
                assert await conn.scalar(text("SELECT COUNT(*) FROM agent_share_manifests")) == 0
                assert (
                    await conn.scalar(
                        text(
                            "SELECT COUNT(*) FROM pg_indexes WHERE tablename = 'discovery_entries' "
                            "AND indexname = 'ix_discovery_entries_search_trgm' AND indexdef LIKE '%gin_trgm_ops%'"
                        )
                    )
                    == 1
                )
                assert (
                    await conn.scalar(
                        text(
                            "SELECT COUNT(*) FROM pg_constraint WHERE conname = 'fk_skill_versions_pre_public_reviewed_by'"
                        )
                    )
                    == 1
                )
                indexes = dict(
                    (
                        await conn.execute(
                            text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = current_schema()")
                        )
                    ).all()
                )
                for name in ("ix_users_email_trgm", "ix_users_name_trgm", "ix_users_username_trgm"):
                    assert "gin_trgm_ops" in indexes[name]
                for name in (
                    "uq_exporter_configs_type",
                    "uq_saml_configs_singleton",
                    "uq_exec_dashboard_config_singleton",
                ):
                    assert "UNIQUE INDEX" in indexes[name]
            # Simulate an installation stamped by the earlier skill-only branch:
            # Alembic cannot revisit its newly inserted 029/030 parents, so
            # 037/038 must restore missing flags and share tables before use.
            recommended_tables = (
                "agents",
                "mcp_listings",
                "skill_listings",
                "hook_listings",
                "prompt_listings",
                "sandbox_listings",
            )
            async with fresh.begin() as conn:
                await conn.execute(text("DROP TABLE agent_share_items"))
                await conn.execute(text("UPDATE alembic_version SET version_num = '037_recommended_compat'"))
            partial = subprocess.run(
                ["bash", str(script)], cwd=server, env=env, capture_output=True, text=True, check=False
            )
            assert partial.returncode == 1
            assert "Partial agent share schema" in partial.stderr
            assert "Running ClickHouse migrations" not in partial.stdout
            async with fresh.begin() as conn:
                assert await conn.scalar(text("SELECT version_num FROM alembic_version")) == "037_recommended_compat"
                for table in recommended_tables:
                    await conn.execute(text(f"ALTER TABLE {table} DROP COLUMN is_recommended"))
                await conn.execute(text("DROP TABLE agent_share_manifests"))
                await conn.execute(text("UPDATE alembic_version SET version_num = '036_discovery_trgm'"))
            repaired = subprocess.run(
                ["bash", str(script)], cwd=server, env=env, capture_output=True, text=True, check=False
            )
            assert repaired.returncode == 0, repaired.stderr[-1000:] + repaired.stdout[-1000:]
            async with fresh.connect() as conn:
                assert await conn.scalar(text("SELECT version_num FROM alembic_version")) == "040_mcp_webhook_syncs"
                assert await conn.scalar(text("SELECT COUNT(*) FROM agent_share_items")) == 0
                for table in recommended_tables:
                    assert (
                        await conn.scalar(
                            text(
                                "SELECT COUNT(*) FROM information_schema.columns "
                                "WHERE table_name = :table AND column_name = 'is_recommended' "
                                "AND data_type = 'boolean' AND is_nullable = 'NO' AND column_default = 'false'"
                            ),
                            {"table": table},
                        )
                        == 1
                    )
            # A freshly stamped schema must really support the empty-data
            # downgrade guard and then the packaged versioned upgrade path.
            downgraded = subprocess.run(
                [sys.executable, "-m", "alembic", "downgrade", "032_skill_stable_pointer_repair"],
                cwd=server,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            assert downgraded.returncode == 0, downgraded.stderr[-1000:]
            async with fresh.connect() as conn:
                assert (
                    await conn.scalar(text("SELECT version_num FROM alembic_version"))
                    == "032_skill_stable_pointer_repair"
                )
            upgraded = subprocess.run(
                ["bash", str(script)], cwd=server, env=env, capture_output=True, text=True, check=False
            )
            assert upgraded.returncode == 0, upgraded.stderr[-1000:] + upgraded.stdout[-1000:]
            assert "Running database migrations" in upgraded.stdout
            async with fresh.connect() as conn:
                assert await conn.scalar(text("SELECT version_num FROM alembic_version")) == "040_mcp_webhook_syncs"
                assert (
                    await conn.scalar(
                        text(
                            "SELECT COUNT(*) FROM information_schema.columns "
                            "WHERE table_name = 'skill_versions' AND column_name = 'pre_public_reviewed_by'"
                        )
                    )
                    == 1
                )
            # A populated database must refuse every destructive skill migration.
            # A failed multi-step Alembic downgrade rolls the prior DDL back.
            async with async_sessionmaker(fresh, expire_on_commit=False)() as db:
                owner = await ds.user(db)
                listing = await ds.skill(db, owner, status=ListingStatus.approved)
                version_id, owner_id = listing.latest_version_id, owner.id
                await db.commit()
            async with fresh.begin() as conn:
                await conn.execute(
                    text(
                        "UPDATE skill_versions SET pre_public_reviewed_by = :owner, review_epoch = 1, "
                        "base_revision = :base, extra_files = CAST(:files AS json) WHERE id = :id"
                    ),
                    {
                        "owner": owner_id,
                        "base": "a" * 64,
                        "files": '[{"path":"note.txt","content":"x"}]',
                        "id": version_id,
                    },
                )
            guards = (
                ("private skill review attribution remains", "pre_public_reviewed_by = NULL"),
                ("withdrawn skill review revisions remain", "review_epoch = 0"),
                ("skill drafts or global public review provenance remain", "base_revision = NULL"),
                ("skill_versions.extra_files contains resources", "extra_files = '[]'::json"),
            )
            for refusal, clear in guards:
                blocked = subprocess.run(
                    [sys.executable, "-m", "alembic", "downgrade", "028_agent_component_pins"],
                    cwd=server,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                assert blocked.returncode != 0
                assert refusal in blocked.stderr, blocked.stderr[-1500:]
                async with fresh.begin() as conn:
                    assert await conn.scalar(text("SELECT version_num FROM alembic_version")) == "040_mcp_webhook_syncs"
                    await conn.execute(text(f"UPDATE skill_versions SET {clear} WHERE id = :id"), {"id": version_id})
            cleared = subprocess.run(
                [sys.executable, "-m", "alembic", "downgrade", "028_agent_component_pins"],
                cwd=server,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            assert cleared.returncode == 0, cleared.stderr[-1500:]
            restored = subprocess.run(
                ["bash", str(script)],
                cwd=server,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            assert restored.returncode == 0, restored.stderr[-1500:]
            # Deliberately corrupt only the disposable revision: the migration
            # must fail on the existing column rather than pretending head was
            # reached, and ClickHouse must never run after that failure.
            async with fresh.begin() as conn:
                await conn.execute(text("UPDATE alembic_version SET version_num = '032_skill_stable_pointer_repair'"))
            failed = subprocess.run(
                ["bash", str(script)], cwd=server, env=env, capture_output=True, text=True, check=False
            )
            assert failed.returncode == 1
            assert "alembic upgrade failed" in failed.stdout
            assert "Running ClickHouse migrations" not in failed.stdout
            async with fresh.connect() as conn:
                assert (
                    await conn.scalar(text("SELECT version_num FROM alembic_version"))
                    == "032_skill_stable_pointer_repair"
                )
            async with fresh.begin() as conn:
                await conn.execute(text("DELETE FROM alembic_version"))
            interrupted = subprocess.run(
                ["bash", str(script)], cwd=server, env=env, capture_output=True, text=True, check=False
            )
            assert interrupted.returncode == 1
            assert "unversioned or incomplete schema" in interrupted.stdout
            async with fresh.connect() as conn:
                assert await conn.scalar(text("SELECT COUNT(*) FROM alembic_version")) == 0
        finally:
            await fresh.dispose()
    finally:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE "{database}" WITH (FORCE)'))
        await admin.dispose()
