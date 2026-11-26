# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Opt-in proof that each API response's tokens are counted once, on a disposable database only.

Claude Code repeats an API response's usage on every transcript line of it. This
runs the real migration runner and ingest path: rows stored before the fix are
corrected by migration 012, and new pushes keep a response's tokens on one row,
including a response split across two pushes. The database is dropped and
recreated, so both URLs must name the dedicated database on localhost:

    CLICKHOUSE_URL=clickhouse://default:@127.0.0.1:18124/observal_response_tokens_ci
    OBSERVAL_CH_RESPONSE_TOKENS_URL=http://127.0.0.1:18124/observal_response_tokens_ci
"""

from __future__ import annotations

import json
import os
import shutil
from urllib.parse import urlparse

import pytest
from tests.test_claude_code_session_parser import ASSISTANT_TEXT, USER_SIMPLE, USER_TOOL_RESULT
from tests.test_session_ingest import BILLED, TEXT_BLOCK, THINKING_BLOCK, TOOL_USE_BLOCK, _response_line

from services import session_ingest
from services.clickhouse import client, insert, migrations

URL = os.getenv("OBSERVAL_CH_RESPONSE_TOKENS_URL")
pytestmark = pytest.mark.skipif(not URL, reason="opt-in: disposable ClickHouse proof of per-response token counts")
DATABASE = "observal_response_tokens_ci"
PROJECT = "default"
USER = "proof"
HARNESS = "claude-code"
TOKENS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")

# Written before the fix: msg_a spans three lines (the first with a partial
# streaming count), msg_b one line, msg_d two lines of which the second was
# stored as invalid JSON (see _legacy_insert), and ASSISTANT_TEXT has no message id.
LEGACY_LINES = [
    USER_SIMPLE,
    _response_line(THINKING_BLOCK, "msg_a", "req_a", output_tokens=5),
    _response_line(TEXT_BLOCK, "msg_a", "req_a"),
    _response_line(TOOL_USE_BLOCK, "msg_a", "req_a"),
    USER_TOOL_RESULT,
    _response_line(TEXT_BLOCK, "msg_b", "req_b", output_tokens=40),
    _response_line(TEXT_BLOCK, "msg_d", "req_d", output_tokens=60),
    _response_line(TOOL_USE_BLOCK, "msg_d", "req_d", output_tokens=60),
    ASSISTANT_TEXT,
]
# msg_a + msg_b + msg_d + ASSISTANT_TEXT (10 / 20 / 5 / 2), each counted once.
LEGACY_BILLED = {
    "input_tokens": 3 + 3 + 3 + 10,
    "output_tokens": 344 + 40 + 60 + 20,
    "cache_read_tokens": 1200 + 1200 + 1200 + 5,
    "cache_write_tokens": 16743 + 16743 + 16743 + 2,
}
# Secret redaction can swallow a closing quote inside the content, leaving invalid JSON.
CORRUPTED_LINE = 7


async def _rows(sql: str, params: dict | None = None) -> list[dict]:
    response = await client._query(sql + " FORMAT JSONEachRow", params)
    assert response.status_code == 200, response.text
    return [json.loads(line) for line in response.text.splitlines() if line]


async def _summary(session_id: str) -> dict[str, int]:
    rows = await _rows(
        "SELECT input_tokens, output_tokens, cache_read_tokens, cache_write_tokens "
        "FROM session_stats_agg FINAL WHERE session_id = {sid:String}",
        {"param_sid": session_id},
    )
    assert len(rows) == 1
    return {column: int(rows[0][column]) for column in TOKENS}


async def _events(session_id: str, response_key: bool = True) -> list[dict]:
    return await _rows(
        "SELECT line_offset, raw_line, toString(ingested_at) AS ingested_at, "
        + ("response_key, " if response_key else "")
        + "input_tokens, output_tokens, cache_read_tokens, cache_write_tokens "
        "FROM session_events FINAL WHERE session_id = {sid:String} ORDER BY line_offset",
        {"param_sid": session_id},
    )


async def _push(session_id: str, lines: list[str], start_offset: int = 0) -> None:
    """One session push as the ingest route makes it: write the lines, then advance the checkpoint."""
    await session_ingest.ingest_session_lines(
        session_id, PROJECT, USER, None, None, harness=HARNESS, lines=lines, start_offset=start_offset
    )
    await session_ingest.advance_session_checkpoint(session_id, PROJECT, USER, HARNESS)


async def _legacy_insert(rows: list[dict]) -> None:
    """The pre-012 write: no response_key column, every line keeps its usage."""
    for row in rows:
        row.pop("response_key", None)
        if row["line_offset"] == CORRUPTED_LINE:
            row["raw_line"] = row["raw_line"].replace('README.md"', "README.md = **REDACTED**")
            with pytest.raises(json.JSONDecodeError):
                json.loads(row["raw_line"])
    columns = insert._SESSION_EVENT_COLUMNS.replace(", response_key", "")
    response = await client._query(
        f"INSERT INTO session_events ({columns}) FORMAT JSONEachRow",
        {"wait_for_async_insert": "1"},
        data="\n".join(insert._dumps(row) for row in rows),
    )
    assert response.status_code == 200, response.text


async def _count_every_line(*_args) -> list[int]:
    return []


async def test_migration_corrects_stored_sessions_and_ingest_counts_each_response_once(monkeypatch, tmp_path):
    parsed = urlparse(URL or "")
    assert parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
    assert parsed.path == f"/{DATABASE}" and client.CLICKHOUSE_DB == DATABASE
    response = await migrations._query_server(f"DROP DATABASE IF EXISTS {DATABASE}")
    assert response.status_code == 200, response.text

    # The schema as it was before this fix, holding a session ingested the old way.
    real_migrations = migrations.MIGRATIONS_DIR
    for path in real_migrations.glob("*.sql"):
        if path.stem < "012":
            shutil.copy(path, tmp_path / path.name)
    monkeypatch.setattr(migrations, "MIGRATIONS_DIR", tmp_path)
    await migrations.run_clickhouse_migrations()
    with monkeypatch.context() as legacy:
        legacy.setattr(session_ingest, "_count_each_response_once", _count_every_line)
        legacy.setattr(session_ingest, "insert_session_events", _legacy_insert)
        await _push("legacy", LEGACY_LINES)
    before = await _events("legacy", response_key=False)
    inflated = await _summary("legacy")
    assert inflated["output_tokens"] == 5 + 344 + 344 + 40 + 60 + 60 + 20
    assert inflated["cache_write_tokens"] == 3 * 16743 + 16743 + 2 * 16743 + 2

    # Migration 012 corrects the stored rows and rebuilds the summary.
    monkeypatch.setattr(migrations, "MIGRATIONS_DIR", real_migrations)
    await migrations.run_clickhouse_migrations()
    after = await _events("legacy")
    assert await _summary("legacy") == LEGACY_BILLED
    assert [row["line_offset"] for row in after] == [row["line_offset"] for row in before]
    assert [row["raw_line"] for row in after] == [row["raw_line"] for row in before]
    assert [(row["response_key"], int(row["output_tokens"])) for row in after] == [
        ("", 0),
        ("msg_a:req_a", 0),
        ("msg_a:req_a", 344),
        ("msg_a:req_a", 0),
        ("", 0),
        ("msg_b:req_b", 40),
        ("msg_d:req_d", 60),
        ("msg_d:req_d", 0),
        ("", 20),
    ]
    rewritten = [index for index, row in enumerate(after) if row["response_key"]]
    untouched = [index for index, row in enumerate(after) if not row["response_key"]]
    assert all(after[i]["ingested_at"] > before[i]["ingested_at"] for i in rewritten)
    assert all(after[i]["ingested_at"] == before[i]["ingested_at"] for i in untouched)
    await migrations.run_clickhouse_migrations()
    assert await _summary("legacy") == LEGACY_BILLED

    # A response split across two pushes; the later line reports more output.
    await _push("live", [_response_line(THINKING_BLOCK, "msg_c", "req_c", output_tokens=5)])
    assert (await _summary("live"))["output_tokens"] == 5
    second_push = [_response_line(TEXT_BLOCK, "msg_c", "req_c"), _response_line(TOOL_USE_BLOCK, "msg_c", "req_c")]
    await _push("live", second_push, start_offset=1)
    assert await _summary("live") == BILLED
    await _push("live", second_push, start_offset=1)
    await _push("live", [_response_line(TEXT_BLOCK, "msg_c", "req_c")], start_offset=3)
    assert await _summary("live") == BILLED
    assert [int(row["output_tokens"]) for row in await _events("live")] == [0, 344, 0, 0]
