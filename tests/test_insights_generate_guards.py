# SPDX-FileCopyrightText: 2026 Dheirav <dheirav2005@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""REGRESSION (OBS-1485): generate must reject self-compare and join in-flight runs.

``--version 1.0.0 --compare 1.0.0`` used to create a report whose stored
baseline its own version_comparison section said did not exist, and three
identical requests within two seconds all ran, each paying for the model.
Both entry points (``/insights/agents/{id}/generate`` and
``/agents/{id}/insights/reports``) share ``generate_insight``.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

V1 = SimpleNamespace(id=uuid.uuid4(), version="1.0.0")
V0 = SimpleNamespace(id=uuid.uuid4(), version="0.9.0")


def _scalars(*rows):
    result = MagicMock()
    result.scalars.return_value.all.return_value = list(rows)
    result.scalar_one_or_none.return_value = rows[0] if rows else None
    return result


def _in_flight(agent_id, *, period_days: int, comparison=V0):
    from models.insight_report import InsightReportStatus

    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid.uuid4(),
        agent_id=agent_id,
        agent_version_id=V1.id,
        agent_version=V1.version,
        version_scope="canonical_and_dirty",
        comparison_agent_version_id=comparison.id if comparison else None,
        status=InsightReportStatus.running,
        period_start=now - timedelta(days=period_days),
        period_end=now,
        sessions_analyzed=0,
        created_at=now,
        completed_at=None,
        progress_phase="generating_sections",
        progress_current=7,
        progress_total=9,
        progress_percent=77,
        progress_message="Generating report sections",
        progress_updated_at=now,
    )


def _db(*execute_results):
    added: list = []

    async def flush():
        for row in added:
            row.id = row.id or uuid.uuid4()
            row.created_at = row.created_at or datetime.now(UTC)
            for column in ("sessions_analyzed", "progress_current", "progress_total", "progress_percent"):
                setattr(row, column, getattr(row, column) or 0)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=list(execute_results))
    db.add = MagicMock(side_effect=added.append)
    db.flush = AsyncMock(side_effect=flush)
    db.added = added
    return db


@contextmanager
def _route(agent):
    versions = {None: V1, "1.0.0": V1, "0.9.0": V0}

    async def resolve_version(_agent, _db, requested=None):
        return versions[requested]

    pool = SimpleNamespace(enqueue_job=AsyncMock())
    with (
        patch("api.routes.insights._resolve_insights_agent", new=AsyncMock(return_value=agent)),
        patch("api.routes.insights._resolve_insight_agent_version", new=resolve_version),
        patch("api.routes.insights._previous_approved_version", new=AsyncMock(return_value=V0)),
        patch("api.routes.insights._count_insight_sessions", new=AsyncMock(return_value=19)),
        patch("api.routes.insights._get_arq_pool", new=AsyncMock(return_value=pool)),
    ):
        yield pool


async def _generate(entry: str, agent, req, db):
    if entry == "insights":
        from api.routes.insights import generate_insight

        return await generate_insight(str(agent.id), req, db, SimpleNamespace(id=uuid.uuid4()))
    from api.routes.agent.insights import create_agent_insight_report

    return await create_agent_insight_report(str(agent.id), req, db, SimpleNamespace(id=uuid.uuid4()))


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["insights", "agent"])
async def test_comparing_a_version_with_itself_is_rejected(entry):
    from schemas.insights import GenerateInsightRequest

    agent = SimpleNamespace(id=uuid.uuid4(), name="pr-verify-qa")
    req = GenerateInsightRequest(period_days=1, agent_version="1.0.0", comparison_agent_version="1.0.0")
    db = _db()

    with _route(agent) as pool, pytest.raises(HTTPException) as exc:
        await _generate(entry, agent, req, db)

    assert exc.value.status_code == 422
    assert "1.0.0" in exc.value.detail
    db.add.assert_not_called()
    pool.enqueue_job.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["insights", "agent"])
async def test_repeat_request_returns_the_in_flight_report(entry):
    from schemas.insights import GenerateInsightRequest

    agent = SimpleNamespace(id=uuid.uuid4(), name="pr-verify-qa")
    running = _in_flight(agent.id, period_days=1)
    db = _db(MagicMock(), _scalars(running))

    with _route(agent) as pool:
        result = await _generate(entry, agent, GenerateInsightRequest(period_days=1), db)

    assert result.id == running.id
    assert result.progress_percent == 77
    db.add.assert_not_called()
    pool.enqueue_job.assert_not_awaited()

    lock_sql = str(db.execute.await_args_list[0].args[0])
    assert "pg_advisory_xact_lock" in lock_sql
    lookup_sql = str(db.execute.await_args_list[1].args[0])
    assert "insight_reports.status IN" in lookup_sql
    assert "insight_reports.started_at >=" in lookup_sql
    assert "insight_reports.comparison_agent_version_id IS NOT DISTINCT FROM" in lookup_sql


@pytest.mark.asyncio
async def test_in_flight_report_for_another_period_does_not_block_a_new_run():
    from api.routes.insights import generate_insight
    from schemas.insights import GenerateInsightRequest

    agent = SimpleNamespace(id=uuid.uuid4(), name="pr-verify-qa")
    fourteen_day_run = _in_flight(agent.id, period_days=14)
    db = _db(MagicMock(), _scalars(fourteen_day_run), _scalars())

    with _route(agent) as pool:
        result = await generate_insight(
            str(agent.id), GenerateInsightRequest(period_days=1), db, SimpleNamespace(id=uuid.uuid4())
        )

    assert result.id != fourteen_day_run.id
    assert len(db.added) == 1
    new_report = db.added[0]
    pool.enqueue_job.assert_awaited_once_with(
        "generate_insight_report", str(new_report.id), _job_id=f"insight-report:{new_report.id}"
    )
