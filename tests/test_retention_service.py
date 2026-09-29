# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Focused tests for deployment-wide retention purging."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


def _response(status_code=200, data=None):
    response = MagicMock(status_code=status_code, text="error")
    response.json.return_value = {"data": data or []}
    return response


@pytest.mark.asyncio
async def test_delete_batch_reports_clickhouse_failure():
    with patch("services.clickhouse._query", new=AsyncMock(return_value=_response(500))):
        from services.retention import _delete_batch

        assert await _delete_batch("session_events", "timestamp", "default", "2026-01-01") == 0


@pytest.mark.asyncio
async def test_delete_batch_reports_success():
    with patch("services.clickhouse._query", new=AsyncMock(return_value=_response())):
        from services.retention import _delete_batch

        assert await _delete_batch("session_events", "timestamp", "default", "2026-01-01") == 1


@pytest.mark.asyncio
async def test_has_inflight_insights_checks_all_reports():
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = "report-id"
    db.execute.return_value = result
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=db)
    session.__aexit__ = AsyncMock(return_value=False)

    with patch("services.retention.async_session", return_value=session):
        from services.retention import _has_inflight_insights

        assert await _has_inflight_insights() is True


@pytest.mark.asyncio
async def test_purge_time_based_reports_each_table():
    with patch("services.retention._delete_batch", new=AsyncMock(return_value=1)) as delete_batch:
        from services.retention import _purge_time_based

        result = await _purge_time_based("default", "2026-01-01", {"session_events": "timestamp"})

    assert result == {"session_events": 1}
    delete_batch.assert_awaited_once_with("session_events", "timestamp", "default", "2026-01-01")


@pytest.mark.asyncio
async def test_purge_insight_reports_deletes_completed_and_stuck_rows():
    db = AsyncMock()
    completed = MagicMock(rowcount=3)
    stuck = MagicMock(rowcount=1)
    db.execute.side_effect = [completed, stuck]
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=db)
    session.__aexit__ = AsyncMock(return_value=False)

    with patch("services.retention.async_session", return_value=session):
        from services.retention import _purge_insight_reports

        result = await _purge_insight_reports(datetime.now(UTC))

    assert result == 4
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_purge_count_based_does_nothing_under_limit():
    with patch(
        "services.clickhouse._query",
        new=AsyncMock(return_value=_response(data=[{"day": "2026-05-11", "cnt": "2"}])),
    ):
        from services.retention import _purge_count_based

        assert await _purge_count_based("default", 10) == {"deleted": 0, "cutoff": None, "failed": False}


@pytest.mark.asyncio
async def test_purge_count_based_deletes_old_sessions_without_derived_cleanup():
    query = AsyncMock(
        return_value=_response(data=[{"day": "2026-05-11", "cnt": "6"}, {"day": "2026-05-10", "cnt": "6"}])
    )
    with (
        patch("services.clickhouse._query", new=query),
        patch("services.retention._delete_batch", new=AsyncMock(return_value=1)) as source,
        patch("services.retention._purge_source_orphans", new=AsyncMock()) as derived,
    ):
        from services.retention import _purge_count_based

        result = await _purge_count_based("default", 10)
    assert result == {"deleted": 1, "cutoff": "2026-05-10 00:00:00.000", "failed": False}
    source.assert_awaited_once_with("session_events", "timestamp", "default", "2026-05-10 00:00:00.000")
    derived.assert_not_awaited()  # the caller runs derived cleanup on every run


@pytest.mark.asyncio
async def test_count_purge_reports_failed_source_delete():
    with (
        patch(
            "services.clickhouse._query",
            new=AsyncMock(return_value=_response(data=[{"day": "2026-05-11", "cnt": "6"}])),
        ),
        patch("services.retention._delete_batch", new=AsyncMock(return_value=0)),
    ):
        from services.retention import _purge_count_based

        result = await _purge_count_based("default", 1)
    assert result["failed"] is True and result["deleted"] == 0


def _settings(values: dict):
    return (
        patch("services.retention.ds.get_bool", new=AsyncMock(return_value=True)),
        patch(
            "services.retention.ds.get_int",
            new=AsyncMock(side_effect=lambda key, default=0: values.get(key, default)),
        ),
        patch("services.retention._has_inflight_insights", new=AsyncMock(return_value=False)),
    )


@pytest.mark.asyncio
async def test_count_only_retries_derived_cleanup_when_under_limit():
    """A derived delete that failed after a successful source delete is retried
    on the next run even though the source is now under its limit."""
    a, b, c = _settings({"retention.max_trace_count": 100})
    under_limit = {"deleted": 0, "cutoff": None, "failed": False}
    with (
        a,
        b,
        c,
        patch("services.retention._purge_count_based", new=AsyncMock(return_value=under_limit)),
        patch("services.retention._retry_cutoff", new=AsyncMock(return_value="2026-05-01 00:00:00.000")) as retry,
        patch("services.retention._purge_source_orphans", new=AsyncMock(return_value={})) as derived,
    ):
        from services.retention import run_retention_purge

        await run_retention_purge()
    retry.assert_awaited_once()
    derived.assert_awaited_once_with("default", "2026-05-01 00:00:00.000")


@pytest.mark.asyncio
async def test_derived_cleanup_skipped_when_source_failed_or_unreadable():
    a, b, c = _settings({"retention.max_trace_count": 100})
    with (
        a,
        b,
        c,
        patch(
            "services.retention._purge_count_based",
            new=AsyncMock(return_value={"deleted": 0, "cutoff": "2026-05-10 00:00:00.000", "failed": True}),
        ),
        patch("services.retention._purge_source_orphans", new=AsyncMock()) as derived,
    ):
        from services.retention import run_retention_purge

        await run_retention_purge()
    derived.assert_not_awaited()

    a, b, c = _settings({"retention.max_trace_count": 100})
    with (
        a,
        b,
        c,
        patch(
            "services.retention._purge_count_based",
            new=AsyncMock(return_value={"deleted": 0, "cutoff": None, "failed": False}),
        ),
        patch("services.retention._retry_cutoff", new=AsyncMock(return_value=None)),
        patch("services.retention._purge_source_orphans", new=AsyncMock()) as derived,
    ):
        await run_retention_purge()
    derived.assert_not_awaited()


@pytest.mark.asyncio
async def test_derived_cleanup_failure_does_not_block_report_purge():
    a, b, c = _settings({"retention.trace_days": 14})
    with (
        a,
        b,
        c,
        patch("services.retention._purge_time_based", new=AsyncMock(return_value={"session_events": 1})),
        patch("services.retention._purge_source_orphans", new=AsyncMock(side_effect=RuntimeError("boom"))),
        patch("services.retention._purge_insight_reports", new=AsyncMock(return_value=0)) as reports,
    ):
        from services.retention import run_retention_purge

        await run_retention_purge()
    reports.assert_awaited_once()


@pytest.mark.asyncio
async def test_retry_cutoff_is_older_of_oldest_source_and_grace():
    from services.retention import _retry_cutoff

    now = datetime(2026, 9, 30, tzinfo=UTC)
    with patch(
        "services.clickhouse._query",
        new=AsyncMock(return_value=_response(data=[{"oldest": "2026-09-28 10:00:00.000", "n": "5"}])),
    ):
        assert await _retry_cutoff("default", now) == "2026-09-23 00:00:00.000"  # 7-day grace is older
    with patch(
        "services.clickhouse._query",
        new=AsyncMock(return_value=_response(data=[{"oldest": "2026-08-01 00:00:00.000", "n": "5"}])),
    ):
        assert await _retry_cutoff("default", now) == "2026-08-01 00:00:00.000"
    with patch("services.clickhouse._query", new=AsyncMock(return_value=_response(500))):
        assert await _retry_cutoff("default", now) is None


@pytest.mark.asyncio
async def test_run_retention_purge_skips_when_disabled():
    with patch("services.retention.ds.get_bool", new=AsyncMock(return_value=False)):
        from services.retention import run_retention_purge

        await run_retention_purge()


@pytest.mark.asyncio
async def test_run_retention_purge_uses_default_project_and_settings():
    int_values = {
        "retention.trace_days": 14,
        "retention.score_days": 30,
        "retention.max_trace_count": 5000,
    }
    with (
        patch("services.retention.ds.get_bool", new=AsyncMock(return_value=True)),
        patch(
            "services.retention.ds.get_int",
            new=AsyncMock(side_effect=lambda key, default=0: int_values.get(key, default)),
        ),
        patch("services.retention._has_inflight_insights", new=AsyncMock(return_value=False)),
        patch("services.retention._purge_time_based", new=AsyncMock(return_value={"session_events": 1})) as purge_time,
        patch("services.retention._purge_source_orphans", new=AsyncMock()) as purge_derived,
        patch(
            "services.retention._purge_count_based",
            new=AsyncMock(return_value={"deleted": 1, "cutoff": "2026-01-01 00:00:00.000", "failed": False}),
        ) as purge_count,
        patch("services.retention._purge_insight_reports", new=AsyncMock(return_value=2)) as purge_reports,
    ):
        from services.retention import run_retention_purge

        await run_retention_purge()

    assert purge_time.await_args.args[0] == "default"
    purge_derived.assert_awaited_once()
    purge_count.assert_awaited_once_with("default", 5000)
    purge_reports.assert_awaited_once()


@pytest.mark.asyncio
async def test_report_purge_runs_even_when_source_sessions_have_expired():
    with (
        patch("services.retention.ds.get_bool", new=AsyncMock(return_value=True)),
        patch(
            "services.retention.ds.get_int",
            new=AsyncMock(side_effect=lambda key, default=0: 30 if key == "retention.score_days" else 0),
        ),
        patch("services.retention._has_inflight_insights", new=AsyncMock(return_value=False)),
        patch("services.retention._purge_insight_reports", new=AsyncMock(return_value=1)) as reports,
        patch("services.clickhouse._query", new=AsyncMock()) as query,
    ):
        from services.retention import run_retention_purge

        await run_retention_purge()
    reports.assert_awaited_once()
    query.assert_not_awaited()


@pytest.mark.asyncio
async def test_facets_purge_matches_full_session_identity_and_does_not_delete_on_query_failure():
    from services.retention import _purge_session_facets

    old = SimpleNamespace(id=uuid4(), user_id="alice", harness="claude-code", session_id="same")
    retained = SimpleNamespace(id=uuid4(), user_id="bob", harness="claude-code", session_id="same")
    another_harness = SimpleNamespace(id=uuid4(), user_id="alice", harness="pi", session_id="same")
    rows = [old, retained, another_harness]
    db = AsyncMock()
    page = MagicMock()
    page.all.return_value = rows
    empty = MagicMock()
    empty.all.return_value = []
    removed = MagicMock(rowcount=2)
    db.execute.side_effect = [page, removed, empty]
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=db)
    session.__aexit__ = AsyncMock(return_value=False)
    response = _response(data=[{"user_id": "bob", "harness": "claude-code", "session_id": "same"}])
    with (
        patch("services.retention.async_session", return_value=session),
        patch("services.clickhouse._query", new=AsyncMock(return_value=response)) as query,
    ):
        assert await _purge_session_facets("default") == 2
    sql, params = query.await_args.args
    assert "project_id = {pid:String}" in sql and "harness = {h0:String}" in sql
    assert "user_id = {u0:String}" in sql and "session_id = {s0:String}" in sql
    assert params["param_u0"] == "alice" and params["param_u1"] == "bob"
    assert params["param_h2"] == "pi" and params["param_pid"] == "default"
    db.commit.assert_awaited_once()

    db.execute.reset_mock(side_effect=True)
    db.execute.side_effect = [page]
    unavailable = _response(500)
    unavailable.raise_for_status.side_effect = RuntimeError("ClickHouse unavailable")
    with (
        patch("services.retention.async_session", return_value=session),
        patch("services.clickhouse._query", new=AsyncMock(return_value=unavailable)),
        pytest.raises(RuntimeError, match="ClickHouse unavailable"),
    ):
        await _purge_session_facets("default")
    assert db.execute.await_count == 1


def _session(db):
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=db)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


@pytest.mark.asyncio
async def test_legacy_session_meta_keeps_any_retained_bare_session_id():
    """insight_session_meta has no user/harness/project; any retained source
    event with that bare session ID, under any scope, keeps the row."""
    from services.retention import _purge_legacy_insight_meta

    kept = SimpleNamespace(id=uuid4(), session_id="shared")
    gone = SimpleNamespace(id=uuid4(), session_id="expired")
    page, empty, removed = MagicMock(), MagicMock(), MagicMock(rowcount=1)
    page.all.return_value = [kept, gone]
    empty.all.return_value = []
    old_id, fresh_id, junk_id = uuid4(), uuid4(), uuid4()
    caches = MagicMock()
    caches.all.return_value = [
        SimpleNamespace(id=old_id, period_start="2026-04-01T00:00:00Z"),
        SimpleNamespace(id=fresh_id, period_start="2026-09-20T00:00:00+00:00"),
        SimpleNamespace(id=junk_id, period_start="not-a-date"),
    ]
    cache_removed = MagicMock(rowcount=2)
    db = AsyncMock()
    db.execute.side_effect = [page, removed, empty, caches, cache_removed]
    response = _response(data=[{"session_id": "shared"}])
    with (
        patch("services.retention.async_session", return_value=_session(db)),
        patch("services.clickhouse._query", new=AsyncMock(return_value=response)) as query,
    ):
        result = await _purge_legacy_insight_meta("2026-09-01 00:00:00.000")
    assert result == {"insight_session_meta": 1, "insight_meta_cache": 2}
    sql, params = query.await_args.args
    assert "project_id" not in sql and "user_id" not in sql  # deliberately unscoped: conservative
    assert params["param_ids"] == "['expired','shared']"
    meta_delete = db.execute.await_args_list[1].args[0]
    assert gone.id.hex in str(meta_delete.compile(compile_kwargs={"literal_binds": True}))
    assert kept.id.hex not in str(meta_delete.compile(compile_kwargs={"literal_binds": True}))
    cache_delete = str(db.execute.await_args_list[4].args[0].compile(compile_kwargs={"literal_binds": True}))
    assert old_id.hex in cache_delete and junk_id.hex in cache_delete and fresh_id.hex not in cache_delete


@pytest.mark.asyncio
async def test_legacy_session_meta_never_deletes_when_source_unreadable():
    from services.retention import _purge_legacy_insight_meta

    page = MagicMock()
    page.all.return_value = [SimpleNamespace(id=uuid4(), session_id="x")]
    db = AsyncMock()
    db.execute.side_effect = [page]
    unavailable = _response(500)
    unavailable.raise_for_status.side_effect = RuntimeError("ClickHouse unavailable")
    with (
        patch("services.retention.async_session", return_value=_session(db)),
        patch("services.clickhouse._query", new=AsyncMock(return_value=unavailable)),
        pytest.raises(RuntimeError, match="ClickHouse unavailable"),
    ):
        await _purge_legacy_insight_meta("2026-09-01 00:00:00.000")
    assert db.execute.await_count == 1
