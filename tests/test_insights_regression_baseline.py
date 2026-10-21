# SPDX-FileCopyrightText: 2026 Dheirav <dheirav2005@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""REGRESSION (OBS-1487): regression detection must diff separate windows.

Both the generate route and the cron batch linked the latest completed report
for the version as ``previous_report_id``, whatever period it covered. Two
``--period 1`` reports minutes apart analysed the same 19 sessions, so every
metric matched and the section reported "stable / noise".
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models.insight_report import InsightReport, InsightReportStatus

# SQLite hands datetimes back naive, so the fixtures stay naive UTC throughout.
NOW = datetime(2026, 10, 18, 12, 0)
DAY = timedelta(days=1)
AGENT_ID = uuid.uuid4()


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(InsightReport.metadata.create_all, tables=[InsightReport.__table__])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        yield session
    await engine.dispose()


async def _report(db, start: datetime, end: datetime, *, version="1.0.0", status=InsightReportStatus.completed):
    row = InsightReport(
        id=uuid.uuid4(),
        agent_id=AGENT_ID,
        status=status,
        period_start=start,
        period_end=end,
        started_at=end,
        created_at=end,
        agent_version=version,
    )
    db.add(row)
    await db.flush()
    return row


async def _baseline(db, *, start: datetime = NOW - DAY, end: datetime = NOW):
    from services.insights.batch import find_baseline_report

    return await find_baseline_report(db, agent_id=AGENT_ID, agent_version="1.0.0", period_start=start, period_end=end)


@pytest.mark.asyncio
async def test_overlapping_report_minutes_earlier_is_not_a_baseline(db):
    """The reported case: the same one-day window, generated a few minutes apart."""
    await _report(db, NOW - DAY - timedelta(minutes=5), NOW - timedelta(minutes=5))

    assert await _baseline(db) is None


@pytest.mark.asyncio
async def test_scheduler_drift_of_seconds_still_finds_the_previous_window(db):
    """The weekly cron fires a few seconds late or early, so the report from one
    period back can end just after the new window starts. It is still the baseline."""
    drifted = await _report(db, NOW - 2 * DAY + timedelta(seconds=5), NOW - DAY + timedelta(seconds=5))

    assert (await _baseline(db)).id == drifted.id


@pytest.mark.asyncio
async def test_baseline_is_the_immediately_preceding_window_of_the_same_length(db):
    await _report(db, NOW - 3 * DAY, NOW - 2 * DAY)  # older, still non-overlapping
    preceding = await _report(db, NOW - 2 * DAY, NOW - DAY)
    await _report(db, NOW - DAY - timedelta(minutes=5), NOW - timedelta(minutes=5))  # overlaps
    await _report(db, NOW - 15 * DAY, NOW - DAY)  # ends in time, but a 14-day total
    await _report(db, NOW - 2 * DAY, NOW - DAY, version="0.9.0")
    await _report(db, NOW - 2 * DAY, NOW - DAY, status=InsightReportStatus.failed)

    assert (await _baseline(db)).id == preceding.id


@pytest.mark.asyncio
async def test_window_that_ended_more_than_one_period_ago_is_not_a_baseline(db):
    await _report(db, NOW - 4 * DAY, NOW - 3 * DAY)

    assert await _baseline(db) is None


@pytest.mark.asyncio
async def test_generate_route_links_only_a_non_overlapping_baseline():
    from api.routes.insights import generate_insight
    from schemas.insights import GenerateInsightRequest

    agent = SimpleNamespace(id=uuid.uuid4(), name="pr-verify-qa")
    version = SimpleNamespace(id=uuid.uuid4(), version="1.0.0")
    baseline = SimpleNamespace(id=uuid.uuid4())
    added: list = []

    async def flush():
        for row in added:
            row.id, row.created_at = uuid.uuid4(), datetime.now(UTC)
            for column in ("sessions_analyzed", "progress_current", "progress_total", "progress_percent"):
                setattr(row, column, 0)

    no_rows = MagicMock()
    no_rows.scalars.return_value.all.return_value = []
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[MagicMock(), no_rows])
    db.add = MagicMock(side_effect=added.append)
    db.flush = AsyncMock(side_effect=flush)
    find = AsyncMock(return_value=baseline)
    pool = SimpleNamespace(enqueue_job=AsyncMock())

    with (
        patch("api.routes.insights._resolve_insights_agent", new=AsyncMock(return_value=agent)),
        patch("api.routes.insights._resolve_insight_agent_version", new=AsyncMock(return_value=version)),
        patch("api.routes.insights._previous_approved_version", new=AsyncMock(return_value=None)),
        patch("api.routes.insights._count_insight_sessions", new=AsyncMock(return_value=19)),
        patch("api.routes.insights._get_arq_pool", new=AsyncMock(return_value=pool)),
        patch("api.routes.insights.find_baseline_report", new=find),
    ):
        await generate_insight(str(agent.id), GenerateInsightRequest(period_days=1), db, SimpleNamespace(id=None))

    (report,) = added
    kwargs = find.await_args.kwargs
    assert kwargs["period_start"] == report.period_start
    assert kwargs["period_end"] == report.period_end
    assert kwargs["agent_version"] == "1.0.0"
    assert report.previous_report_id == baseline.id


@pytest.mark.asyncio
async def test_cron_batch_links_only_a_non_overlapping_baseline():
    import services.dynamic_settings as ds
    from models.agent import AgentStatus
    from services.insights import batch

    version = SimpleNamespace(id=uuid.uuid4(), version="1.0.0", status=AgentStatus.approved)
    agent = SimpleNamespace(id=uuid.uuid4(), name="pr-verify-qa", latest_version=version)
    agents = MagicMock()
    agents.scalars.return_value.all.return_value = [agent]
    no_recent = MagicMock()
    no_recent.scalar_one_or_none.return_value = None

    added: list = []
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[agents, no_recent])
    db.add = MagicMock(side_effect=added.append)
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=db)
    session.__aexit__ = AsyncMock(return_value=False)
    find = AsyncMock(return_value=None)
    pool = SimpleNamespace(enqueue_job=AsyncMock())
    settings = {"insights.batch_period_days": 14, "insights.min_sessions": 1}

    with (
        patch.object(batch, "_reap_stale_reports", new=AsyncMock(return_value=0)),
        patch.object(batch, "async_session", new=MagicMock(return_value=session)),
        patch.object(batch, "_count_agent_sessions", new=AsyncMock(return_value=19)),
        patch.object(batch, "_get_arq_pool", new=AsyncMock(return_value=pool)),
        patch.object(batch, "find_baseline_report", new=find),
        patch.object(ds, "get_sync_bool", return_value=True),
        patch.object(ds, "get_sync_int", side_effect=lambda key, default: settings.get(key, default)),
    ):
        assert await batch.discover_and_queue_reports() == 1

    (report,) = added
    kwargs = find.await_args.kwargs
    assert kwargs["period_start"] == report.period_start
    assert kwargs["period_end"] == report.period_end
    assert report.period_end - report.period_start == timedelta(days=14)
    assert report.previous_report_id is None
