# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for query_session_rows, the stored-row reader behind OTLP output."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.clickhouse import query_session_rows
from services.otel.types import SessionKey

KEY = SessionKey(project_id="proj-1", user_id="user-1", harness="claude-code", session_id="sess-1")


def _response(rows: list[dict]) -> MagicMock:
    response = MagicMock()
    response.json.return_value = {"data": rows}
    return response


async def test_reads_one_session_by_primary_key_after_the_watermark():
    rows = [{"line_offset": 3, "raw_line": "{}"}, {"line_offset": "4", "raw_line": "{}"}]
    with patch("services.clickhouse.client._query", new_callable=AsyncMock, return_value=_response(rows)) as query:
        result = await query_session_rows(KEY, after_line=2)

    sql, params = query.call_args.args
    assert "FROM session_events FINAL" in sql
    assert "project_id = {pid:String} AND user_id = {uid:String}" in sql
    assert "harness = {harness:String} AND session_id = {sid:String}" in sql
    assert "line_offset > {after:Int64}" in sql
    assert "ORDER BY line_offset" in sql
    assert "{upto" not in sql
    assert params == {
        "param_pid": "proj-1",
        "param_uid": "user-1",
        "param_harness": "claude-code",
        "param_sid": "sess-1",
        "param_after": "2",
    }
    assert [row["line_offset"] for row in result] == [3, 4]


async def test_upper_bound_is_inclusive_and_defaults_start_before_line_zero():
    with patch("services.clickhouse.client._query", new_callable=AsyncMock, return_value=_response([])) as query:
        await query_session_rows(KEY, up_to_line=9)

    sql, params = query.call_args.args
    assert "line_offset <= {upto:Int64}" in sql
    assert params["param_after"] == "-1"
    assert params["param_upto"] == "9"


async def test_every_stored_row_is_read_with_the_columns_otlp_needs():
    with patch("services.clickhouse.client._query", new_callable=AsyncMock, return_value=_response([])) as query:
        await query_session_rows(KEY)

    sql = query.call_args.args[0]
    # Synthetic and unrendered rows are stored records too.
    assert "is_source_record =" not in sql
    assert "rendered =" not in sql
    columns = sql.split("SELECT ", 1)[1].split(" FROM ", 1)[0]
    for column in (
        "raw_line",
        "raw_line_truncated",
        "line_hash",
        "is_source_record",
        "uuid",
        "parent_uuid",
        "parent_session_id",
        "tool_id",
        "model",
        "input_tokens",
        "cache_write_tokens",
        "ingested_at",
    ):
        assert column in [c.strip() for c in columns.split(",")]


async def test_clickhouse_errors_propagate():
    response = _response([])
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "boom", request=httpx.Request("POST", "http://ch"), response=httpx.Response(500)
    )
    with (
        patch("services.clickhouse.client._query", new_callable=AsyncMock, return_value=response),
        pytest.raises(httpx.HTTPStatusError),
    ):
        await query_session_rows(KEY)
