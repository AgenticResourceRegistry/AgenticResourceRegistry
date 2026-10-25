# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for deployment-wide data retention purge service."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_response(status_code=200, data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = ""
    if data is not None:
        resp.json.return_value = {"data": data}
    else:
        resp.json.return_value = {"data": []}
    return resp


@pytest.mark.asyncio
async def test_delete_batch_uses_parameterized_query():
    """DELETE batch uses {pid:String} and {cutoff:String} placeholders."""
    with patch("services.clickhouse._query", new_callable=AsyncMock) as mock_query:
        mock_query.return_value = _mock_response()

        from services.retention import _delete_batch

        await _delete_batch("traces", "start_time", "test-project-id", "2026-04-01 00:00:00.000")

        call_args = mock_query.call_args
        sql = call_args.args[0]
        params = call_args.args[1]

        assert "{pid:String}" in sql
        assert "{cutoff:String}" in sql
        assert "param_pid" in params
        assert "param_cutoff" in params
        assert params["param_pid"] == "test-project-id"
        assert params["param_cutoff"] == "2026-04-01 00:00:00.000"
        assert "lightweight_deletes_sync = 1" in sql


@pytest.mark.asyncio
async def test_purge_time_based_correct_columns():
    """Time-based purge uses correct time columns per table."""
    with patch("services.clickhouse._query", new_callable=AsyncMock) as mock_query:
        mock_query.return_value = _mock_response()

        from services.retention import TIME_PURGE_TABLES, _purge_time_based

        await _purge_time_based("pid", "2026-04-27 00:00:00.000", TIME_PURGE_TABLES)

        calls = mock_query.call_args_list
        sqls = [c.args[0] for c in calls]

        assert len(sqls) == 1
        assert "FROM session_events" in sqls[0]
        assert "timestamp" in sqls[0]


@pytest.mark.asyncio
async def test_scoped_session_orphan_cleanup():
    """A surviving session belonging to another user or harness cannot preserve an orphan."""
    with patch("services.clickhouse._query", new_callable=AsyncMock) as mock_query:
        mock_query.return_value = _mock_response()

        from services.retention import _purge_session_orphans

        result = await _purge_session_orphans("test-pid", "2026-04-01 00:00:00.000")

    assert set(result) == {
        "session_stats_agg",
        "component_activity",
        "component_activity_publications",
        "session_capabilities",
    }
    for call in mock_query.call_args_list:
        sql, params = call.args
        assert "(user_id, harness, session_id) NOT IN" in sql
        assert "project_id = {pid:String}" in sql and "project_id = {pid2:String}" in sql
        assert params["param_pid"] == params["param_pid2"] == "test-pid"
        assert "lightweight_deletes_sync = 1" in sql
    assert "used_at < {cutoff:String}" in mock_query.call_args.args[0]


@pytest.mark.asyncio
async def test_count_based_purge_uses_daily_aggregation():
    """Count-based purge queries daily counts, not OFFSET."""
    with (
        patch("services.clickhouse._query", new_callable=AsyncMock) as mock_query,
        patch("services.retention._delete_batch", new=AsyncMock(return_value=1)),
    ):
        mock_query.return_value = _mock_response(
            data=[
                {"day": "2026-05-11", "cnt": "1000"},
                {"day": "2026-05-10", "cnt": "1000"},
                {"day": "2026-05-09", "cnt": "1000"},
            ]
        )
        from services.retention import _purge_count_based

        await _purge_count_based("test-pid", 1500)

        # First call should be the GROUP BY day query
        first_sql = mock_query.call_args_list[0].args[0]
        assert "GROUP BY day" in first_sql
        assert "ORDER BY day DESC" in first_sql
        assert "LIMIT 730" in first_sql
        assert "OFFSET" not in first_sql


def test_scheduled_retention_never_deletes_layer_snapshots():
    """Senders cache "already uploaded" per hash, so a deleted snapshot is never re-sent (the admin purge agrees)."""
    import inspect

    from services import retention

    source = inspect.getsource(retention)
    assert "DELETE FROM layer_" not in source
    assert "_purge_layer_orphans" not in source
