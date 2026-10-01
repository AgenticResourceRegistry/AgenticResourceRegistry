# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Opt-in report cleanup proof on the separately isolated Phase 4 Postgres.

Requires an isolated PostgreSQL at the current Alembic head at :15433;
never runs against the sample/production databases.
"""

from __future__ import annotations

import os
import uuid
from urllib.parse import urlparse

import asyncpg
import pytest

_URL = os.getenv("OBSERVAL_PG_PHASE4_URL")
pytestmark = pytest.mark.skipif(not _URL, reason="opt-in isolated Phase 4 PostgreSQL proof")


def _alembic_head() -> str:
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    server = Path(__file__).resolve().parents[2] / "observal-server"
    config = Config(str(server / "alembic.ini"))
    config.set_main_option("script_location", str(server / "alembic"))
    return ScriptDirectory.from_config(config).get_current_head()


@pytest.mark.asyncio
async def test_listing_delete_and_rollback_cleanup_component_reports():
    pg = urlparse(_URL or "")
    assert (pg.hostname, pg.port, pg.path) == ("127.0.0.1", 15433, "/observal_phase4_ci")
    connection = await asyncpg.connect(_URL.replace("postgresql+asyncpg://", "postgresql://"))
    listing_id, report_id, owner_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    tag = owner_id.hex[:12]
    try:
        assert await connection.fetchval("SELECT version_num FROM alembic_version") == _alembic_head()
        triggers = await connection.fetch(
            "SELECT tgname FROM pg_trigger WHERE tgname IN "
            "('trg_mcp_insight_report_cleanup','trg_skill_insight_report_cleanup','trg_hook_insight_report_cleanup')"
        )
        assert len(triggers) == 3
        # Complete rows for the current schema (every NOT NULL column without a default).
        await connection.execute(
            "INSERT INTO users (id,email,username,name,role,created_at) VALUES ($1,$2,$3,'Owner','user',now())",
            owner_id,
            f"owner-{tag}@example.test",
            f"owner{tag}",
        )
        await connection.execute(
            "INSERT INTO mcp_listings (id,name,namespace,slug,category,owner,is_private,submitted_by,co_authors,"
            "unique_agents,created_at,updated_at) VALUES ($1,'probe','ns',$2,'tools',$3,false,$4,'[]',0,now(),now())",
            listing_id,
            f"probe-{tag}",
            f"owner-{tag}@example.test",
            owner_id,
        )
        await connection.execute(
            "INSERT INTO insight_reports (id,agent_id,subject_type,project_id,component_type,component_id,status,"
            "period_start,period_end,sessions_analyzed,started_at,created_at,report_version,progress_current,"
            "progress_total,progress_percent) VALUES ($1,NULL,'component','default','mcp',$2,'pending',now(),now(),"
            "0,now(),now(),4,0,0,0)",
            report_id,
            listing_id,
        )
        transaction = connection.transaction()
        await transaction.start()
        await connection.execute("DELETE FROM mcp_listings WHERE id=$1", listing_id)
        assert not await connection.fetchval("SELECT 1 FROM insight_reports WHERE id=$1", report_id)
        await transaction.rollback()
        assert await connection.fetchval("SELECT 1 FROM insight_reports WHERE id=$1", report_id)
        await connection.execute("DELETE FROM mcp_listings WHERE id=$1", listing_id)
        assert not await connection.fetchval("SELECT 1 FROM insight_reports WHERE id=$1", report_id)
    finally:
        await connection.execute("DELETE FROM insight_reports WHERE id=$1", report_id)
        await connection.execute("DELETE FROM mcp_listings WHERE id=$1", listing_id)
        await connection.execute("DELETE FROM users WHERE id=$1", owner_id)
        await connection.close()
