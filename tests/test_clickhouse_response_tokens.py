# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""ClickHouse helpers that keep one API response's tokens on a single session row."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock

import pytest

from services.clickhouse import client, insert, query

TOKEN_COLUMNS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def _response(payload: dict | None = None) -> Mock:
    return Mock(raise_for_status=Mock(), json=Mock(return_value=payload or {}))


def _columns(sql: str) -> list[str]:
    inserted = sql[sql.index("(") + 1 : sql.index(")")]
    return [column.strip() for column in inserted.split(",")]


async def test_clear_reinserts_the_stored_rows_with_zero_tokens(monkeypatch):
    ch_query = AsyncMock(return_value=_response())
    monkeypatch.setattr(insert._client, "_query", ch_query)

    await insert.clear_session_event_tokens("session-1", "project-1", "user-1", "claude-code", [7, 4, 7])

    sql, params = ch_query.await_args.args
    columns = _columns(sql)
    projection = sql[sql.index("SELECT") + len("SELECT") : sql.index("FROM")].split(",")
    assert len(projection) == len(columns)
    for column, selected in zip(columns, projection, strict=True):
        expected = f"0 AS {column}" if column in TOKEN_COLUMNS else column
        assert selected.strip() == expected
    assert "ingested_at" not in columns
    assert {"raw_line", "line_hash", "source_sha256", "response_key"} <= set(columns)
    assert "FROM session_events FINAL" in sql
    assert "is_source_record = 1" in sql
    assert params == {
        "param_pid": "project-1",
        "param_uid": "user-1",
        "param_harness": "claude-code",
        "param_sid": "session-1",
        "param_offsets": "[4,7]",
        "insert_deduplicate": "0",
        "wait_for_async_insert": "1",
    }


async def test_clear_without_offsets_does_not_touch_clickhouse(monkeypatch):
    ch_query = AsyncMock()
    monkeypatch.setattr(insert._client, "_query", ch_query)

    await insert.clear_session_event_tokens("session-1", "project-1", "user-1", "claude-code", [])

    ch_query.assert_not_awaited()


async def test_insert_writes_the_response_key_column(monkeypatch):
    ch_query = AsyncMock(return_value=_response())
    monkeypatch.setattr(insert._client, "_query", ch_query)

    await insert.insert_session_events([{"session_id": "session-1", "line_offset": 0}])

    sql = ch_query.await_args.args[0]
    assert "response_key" in _columns(sql)
    written = json.loads(ch_query.await_args.kwargs["data"])
    assert written["response_key"] == ""


async def test_response_token_rows_are_read_for_the_given_responses(monkeypatch):
    rows = [
        {"line_offset": 4, "response_key": "msg_1:req_1", "output_tokens": 344},
        {"line_offset": "9", "response_key": "msg_'2", "output_tokens": "12"},
    ]
    ch_query = AsyncMock(return_value=_response({"data": rows}))
    monkeypatch.setattr(query._client, "_query", ch_query)

    result = await query.query_response_token_rows(
        "session-1", "project-1", "user-1", "claude-code", ["msg_1:req_1", "msg_'2", "msg_1:req_1"]
    )

    assert result == [(4, "msg_1:req_1", 344), (9, "msg_'2", 12)]
    sql, params = ch_query.await_args.args
    assert "FROM session_events FINAL" in sql
    assert "rendered = 1" in sql
    assert "has({keys:Array(String)}, response_key)" in sql
    assert params["param_keys"] == "['msg_\\'2','msg_1:req_1']"
    assert (params["param_sid"], params["param_harness"]) == ("session-1", "claude-code")


async def test_response_token_rows_skip_clickhouse_without_responses(monkeypatch):
    ch_query = AsyncMock()
    monkeypatch.setattr(query._client, "_query", ch_query)

    assert await query.query_response_token_rows("session-1", "project-1", "user-1", "claude-code", []) == []
    ch_query.assert_not_awaited()


async def test_response_token_lookup_failure_is_raised(monkeypatch):
    monkeypatch.setattr(query._client, "_query", AsyncMock(side_effect=RuntimeError("clickhouse down")))

    with pytest.raises(RuntimeError, match="clickhouse down"):
        await query.query_response_token_rows("session-1", "project-1", "user-1", "claude-code", ["msg_1"])


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([], "[]"),
        (["plain"], "['plain']"),
        (["it's", "back\\slash"], "['it\\'s','back\\\\slash']"),
    ],
)
def test_string_array_param_quotes_each_element(values: list[str], expected: str):
    assert client.string_array_param(values) == expected
