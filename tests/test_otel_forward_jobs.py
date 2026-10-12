# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The forward job, the sweep cron, their worker registration and the ClickHouse SQL behind them."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jobs import otel_forward
from services.clickhouse import insert as ch_insert
from services.clickhouse import (
    query_forward_candidates,
    query_forward_state,
    query_session_first_event,
)
from services.otel.destinations import Destination
from services.otel.forwarder import ForwardResult, job_id
from services.otel.types import SessionKey

KEY = SessionKey(project_id="default", user_id="user-1", harness="claude-code", session_id="sess-1")
ADDED = datetime(2026, 9, 1, 12, 30, 15, 250000, tzinfo=UTC)


def _destination(**overrides) -> Destination:
    values = {"id": "collector", "url": "https://otel.example.com", "added_at": ADDED.isoformat()}
    values.update(overrides)
    return Destination(**values)


def _returning(value):
    async def fake():
        return value

    return fake


class FakePool:
    def __init__(self):
        self.jobs: list[tuple] = []

    async def enqueue_job(self, name, *args, _job_id=None):
        self.jobs.append((name, args, _job_id))


# ── forward_session ──────────────────────────────────────────────────────


async def test_job_forwards_logs_for_the_named_destination(monkeypatch):
    calls = []

    async def fake_forward(destination, key, *, store, client):
        calls.append((destination.id, key))
        return ForwardResult("caught_up", 3)

    monkeypatch.setattr("services.otel.destinations.load_destinations", _returning([_destination()]))
    monkeypatch.setattr("services.otel.forwarder.forward_session_logs", fake_forward)

    summary = await otel_forward.forward_session({}, "collector", "default", "user-1", "claude-code", "sess-1")

    assert calls == [("collector", KEY)]
    assert summary == "logs:caught_up:3"


@pytest.mark.parametrize("destinations", [[], [_destination(enabled=False)], [_destination(id="other")]])
async def test_job_for_a_removed_or_disabled_destination_does_nothing(monkeypatch, destinations):
    monkeypatch.setattr("services.otel.destinations.load_destinations", _returning(destinations))
    monkeypatch.setattr("services.otel.forwarder.forward_session_logs", AsyncMock(side_effect=AssertionError))

    assert (
        await otel_forward.forward_session({}, "collector", "default", "user-1", "claude-code", "sess-1")
        == "destination_gone"
    )


# ── sweep_otlp_forwarding ────────────────────────────────────────────────


async def test_sweep_queues_lagging_sessions_for_enabled_unpaused_destinations(monkeypatch):
    other = SessionKey(project_id="default", user_id="user-2", harness="kiro", session_id="sess-2")
    candidates = AsyncMock(return_value=[KEY, other])
    paused = {"paused-one"}
    monkeypatch.setattr(
        "services.otel.destinations.load_destinations",
        _returning([_destination(), _destination(id="off", enabled=False), _destination(id="paused-one")]),
    )
    monkeypatch.setattr("services.clickhouse.query_forward_candidates", candidates)

    async def is_paused(self, destination_id):
        return destination_id in paused

    monkeypatch.setattr("services.otel.forwarder.ForwardStore.is_paused", is_paused)
    pool = FakePool()

    queued = await otel_forward.sweep_otlp_forwarding({"redis": pool})

    assert queued == 2
    candidates.assert_awaited_once_with("collector", "logs", ADDED, otel_forward.SWEEP_LIMIT)
    assert sorted(job for _name, _args, job in pool.jobs) == sorted(
        [job_id("collector", KEY), job_id("collector", other)]
    )


async def test_sweep_without_destinations_touches_nothing(monkeypatch):
    monkeypatch.setattr("services.otel.destinations.load_destinations", _returning([]))

    assert await otel_forward.sweep_otlp_forwarding({}) == 0


def test_worker_registers_the_job_without_kept_results_and_the_sweep_every_minute():
    from worker import WorkerSettings

    (job,) = [f for f in WorkerSettings.functions if getattr(f, "name", None) == "forward_session"]
    assert job.keep_result_s == 0
    assert job.timeout_s == 120
    (sweep,) = [c for c in WorkerSettings.cron_jobs if c.name == "cron:sweep_otlp_forwarding"]
    assert sweep.second == {30}
    assert sweep.unique is True


# ── ClickHouse SQL ───────────────────────────────────────────────────────


def _response(rows: list[dict]) -> MagicMock:
    response = MagicMock()
    response.json.return_value = {"data": rows}
    return response


async def test_candidates_query_handles_missing_state_rows_and_rewinds():
    rows = [{"project_id": "default", "user_id": "user-1", "harness": "claude-code", "session_id": "sess-1"}]
    with patch("services.clickhouse.client._query", new_callable=AsyncMock, return_value=_response(rows)) as query:
        keys = await query_forward_candidates("collector", "logs", ADDED, 50)

    sql, params = query.call_args.args
    assert "join_use_nulls = 1" in sql  # a missing state row must read as NULL, not forwarded_line = 0
    assert "f.forwarded_line IS NULL OR f.forwarded_line != c.acknowledged_line" in sql
    assert "first_event_time >= {added:DateTime64(3)}" in sql
    assert "FROM session_checkpoints AS c FINAL" in sql
    assert params == {
        "param_dest": "collector",
        "param_signal": "logs",
        "param_added": "2026-09-01 12:30:15.250",
        "param_limit": "50",
    }
    assert keys == [KEY]


async def test_state_reads_none_before_the_first_send_and_parses_after():
    with patch("services.clickhouse.client._query", new_callable=AsyncMock, return_value=_response([])):
        assert await query_forward_state("collector", "logs", KEY) is None

    response = _response([{"forwarded_line": "12", "replay_through": "-1", "session_closed": 0}])
    with patch("services.clickhouse.client._query", new_callable=AsyncMock, return_value=response):
        assert await query_forward_state("collector", "logs", KEY) == (12, -1, False)


async def test_first_event_is_read_as_utc():
    response = _response([{"first_event_time": "2026-09-30 10:00:00.000"}])
    with patch("services.clickhouse.client._query", new_callable=AsyncMock, return_value=response):
        assert await query_session_first_event(KEY) == datetime(2026, 9, 30, 10, tzinfo=UTC)


async def test_delivery_insert_failures_are_swallowed(monkeypatch):
    async def failing(*_args, **_kwargs):
        raise RuntimeError("clickhouse down")

    monkeypatch.setattr(ch_insert._client, "_query", failing)

    await ch_insert.insert_forward_deliveries([{"delivery_id": "d", "destination_id": "collector"}])
