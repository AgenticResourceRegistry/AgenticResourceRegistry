# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Opt-in report cleanup proof on the separately isolated Phase 4 Postgres.

Requires an isolated PostgreSQL with Alembic 031 applied at :15433;
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


@pytest.mark.asyncio
async def test_listing_delete_and_rollback_cleanup_component_reports():
    pg = urlparse(_URL or "")
    assert (pg.hostname, pg.port, pg.path) == ("127.0.0.1", 15433, "/observal_phase4_ci")
    connection = await asyncpg.connect(_URL.replace("postgresql+asyncpg://", "postgresql://"))
    listing_id, report_id = uuid.uuid4(), uuid.uuid4()
    try:
        assert await connection.fetchval("SELECT version_num FROM alembic_version") == "031_component_privacy"
        triggers = await connection.fetch(
            "SELECT tgname FROM pg_trigger WHERE tgname IN "
            "('trg_mcp_insight_report_cleanup','trg_skill_insight_report_cleanup','trg_hook_insight_report_cleanup')"
        )
        assert len(triggers) == 3
        await connection.execute("INSERT INTO mcp_listings (id) VALUES ($1)", listing_id)
        await connection.execute(
            "INSERT INTO insight_reports (id,agent_id,subject_type,project_id,component_type,component_id) "
            "VALUES ($1,NULL,'component','default','mcp',$2)",
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
        await connection.close()
