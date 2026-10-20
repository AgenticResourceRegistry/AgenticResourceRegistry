# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest
import xxhash
from pydantic import ValidationError

from api.routes import ingest as ingest_route
from api.routes.ingest import SessionIngestRequest
from services import clickhouse, session_ingest
from services.clickhouse import insert as clickhouse_insert


@pytest.mark.asyncio
async def test_canonical_insert_waits_for_clickhouse_commit(monkeypatch):
    response = MagicMock()
    response.raise_for_status.return_value = None
    query = AsyncMock(return_value=response)
    monkeypatch.setattr(clickhouse_insert._client, "_query", query)

    await clickhouse_insert.insert_session_events([{"session_id": "session"}])

    assert query.await_args.args[1]["wait_for_async_insert"] == "1"


@pytest.mark.asyncio
async def test_ingest_retains_ignored_and_malformed_source_positions(monkeypatch):
    inserted: list[dict] = []

    async def capture(rows):
        inserted.extend(rows)

    monkeypatch.setattr(session_ingest, "query_existing_for_dedup", AsyncMock(return_value={}))
    monkeypatch.setattr(session_ingest, "insert_session_events", capture)
    monkeypatch.setattr(session_ingest, "refresh_session_summary", AsyncMock())

    ignored = '{"type":"user","message":{"content":[]}}'
    result = await session_ingest.ingest_session_lines(
        session_id="session",
        project_id="project",
        user_id="user",
        agent_id=None,
        agent_version=None,
        harness="claude-code",
        lines=[ignored, "not-json"],
        start_offset=3,
        end_byte_offsets=[50, 59],
    )

    assert result == session_ingest.IngestResult(ingested=0, skipped=1, errors=1)
    assert [(row["line_offset"], row["event_type"], row["rendered"]) for row in inserted] == [
        (3, "_ignored", 0),
        (4, "_parse_error", 0),
    ]
    assert [row["source_end_offset"] for row in inserted] == [50, 59]
    assert all(row["is_source_record"] == 1 for row in inserted)


@pytest.mark.asyncio
async def test_dedup_uses_source_index_not_content_hash(monkeypatch):
    line = '{"type":"system","content":"same"}'
    digest = xxhash.xxh128(line.encode()).hexdigest()
    inserted: list[dict] = []

    monkeypatch.setattr(session_ingest, "query_existing_for_dedup", AsyncMock(return_value={0: digest}))
    monkeypatch.setattr(session_ingest, "query_session_checkpoint", AsyncMock(return_value=(0, 0)))
    monkeypatch.setattr(
        session_ingest, "insert_session_events", AsyncMock(side_effect=lambda rows: inserted.extend(rows))
    )
    monkeypatch.setattr(session_ingest, "refresh_session_summary", AsyncMock())

    result = await session_ingest.ingest_session_lines(
        session_id="session",
        project_id="project",
        user_id="user",
        agent_id=None,
        agent_version=None,
        harness="claude-code",
        lines=[line, line],
    )

    assert result.skipped == 1
    assert [row["line_offset"] for row in inserted] == [1]


@pytest.mark.asyncio
async def test_conflicting_content_at_same_source_index_is_rejected(monkeypatch):
    monkeypatch.setattr(session_ingest, "query_existing_for_dedup", AsyncMock(return_value={7: "different"}))
    monkeypatch.setattr(session_ingest, "query_session_checkpoint", AsyncMock(return_value=(7, 0)))
    insert = AsyncMock()
    monkeypatch.setattr(session_ingest, "insert_session_events", insert)

    with pytest.raises(session_ingest.SessionRecordConflictError) as exc:
        await session_ingest.ingest_session_lines(
            session_id="session",
            project_id="project",
            user_id="user",
            agent_id=None,
            agent_version=None,
            harness="claude-code",
            lines=['{"type":"system"}'],
            start_offset=7,
        )

    assert exc.value.offsets == [7]
    insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_conflict_after_audited_checkpoint_is_replaced(monkeypatch):
    monkeypatch.setattr(session_ingest, "query_existing_for_dedup", AsyncMock(return_value={7: "different"}))
    monkeypatch.setattr(session_ingest, "query_session_checkpoint", AsyncMock(return_value=(6, 70)))
    inserted = AsyncMock()
    monkeypatch.setattr(session_ingest, "insert_session_events", inserted)
    monkeypatch.setattr(session_ingest, "refresh_session_summary", AsyncMock())

    await session_ingest.ingest_session_lines(
        session_id="session",
        project_id="project",
        user_id="user",
        agent_id=None,
        agent_version=None,
        harness="claude-code",
        lines=['{"type":"system"}'],
        start_offset=7,
    )

    assert inserted.await_args.args[0][0]["line_offset"] == 7


@pytest.mark.asyncio
async def test_checkpoint_stops_at_first_gap(monkeypatch):
    monkeypatch.setattr(session_ingest, "query_session_checkpoint", AsyncMock(return_value=(-1, 0)))
    monkeypatch.setattr(
        session_ingest,
        "query_source_records_after",
        AsyncMock(return_value=[(0, 10), (2, 30)]),
    )
    insert = AsyncMock()
    monkeypatch.setattr(session_ingest, "insert_session_checkpoint", insert)

    checkpoint = await session_ingest.advance_session_checkpoint("session", "project", "user", "claude-code")

    assert checkpoint == (0, 10)
    insert.assert_awaited_once_with("session", "project", "user", "claude-code", 0, 10)


@pytest.mark.asyncio
async def test_checkpoint_advances_across_repaired_range(monkeypatch):
    monkeypatch.setattr(session_ingest, "query_session_checkpoint", AsyncMock(return_value=(0, 10)))
    monkeypatch.setattr(
        session_ingest,
        "query_source_records_after",
        AsyncMock(return_value=[(1, 20), (2, 30)]),
    )
    monkeypatch.setattr(session_ingest, "insert_session_checkpoint", AsyncMock())

    assert await session_ingest.advance_session_checkpoint("session", "project", "user", "claude-code") == (2, 30)


@pytest.mark.asyncio
async def test_integrity_uses_line_and_byte_checkpoints(monkeypatch):
    monkeypatch.setattr(session_ingest, "query_session_checkpoint", AsyncMock(return_value=(2, 30)))

    result = await session_ingest.check_session_integrity("session", "project", "user", "claude-code", 3, 30)

    assert result.ok
    assert result.expected_line == 2


@pytest.mark.asyncio
async def test_final_hash_audit_identifies_first_missing_range(monkeypatch):
    lines = ["zero", "one", "two"]
    source_hashes = [hashlib.sha256(line.encode()).hexdigest() for line in lines]
    expected_hash = hashlib.sha256("".join(f"{digest}\n" for digest in source_hashes).encode()).hexdigest()
    monkeypatch.setattr(
        session_ingest,
        "query_session_source_manifest",
        AsyncMock(return_value=[(0, 5, source_hashes[0]), (2, 14, source_hashes[2])]),
    )

    result = await session_ingest.check_session_integrity(
        "session",
        "project",
        "user",
        "claude-code",
        3,
        14,
        acknowledged_line=2,
        acknowledged_offset=14,
        expected_hash=expected_hash,
        hashed_line_count=3,
    )

    assert not result.ok
    assert result.repair_from_line == 1
    assert result.repair_offset == 5


def test_request_requires_one_ordered_byte_checkpoint_per_line():
    with pytest.raises(ValidationError):
        SessionIngestRequest(session_id="session", lines=["a", "b"], end_byte_offsets=[1])
    with pytest.raises(ValidationError):
        SessionIngestRequest(session_id="session", lines=["a", "b"], end_byte_offsets=[2, 1])

    request = SessionIngestRequest(session_id="session", lines=["a", "b"], end_byte_offsets=[1, 2])
    assert request.start_offset == 0


@pytest.mark.asyncio
async def test_ingest_route_returns_contiguous_acknowledgement(monkeypatch):
    user = MagicMock()
    user.id = "user"
    monkeypatch.setattr(ingest_route, "DEFAULT_PROJECT_ID", "project")
    monkeypatch.setattr(
        session_ingest,
        "ingest_session_lines",
        AsyncMock(return_value=session_ingest.IngestResult(ingested=0, skipped=2, errors=0)),
    )
    monkeypatch.setattr(session_ingest, "advance_session_checkpoint", AsyncMock(return_value=(4, 120)))
    monkeypatch.setattr(clickhouse, "query_session_error_offsets", AsyncMock(return_value=[]))

    response = await ingest_route.ingest_session.__wrapped__(
        SessionIngestRequest(
            session_id="session",
            harness="claude-code",
            lines=['{"type":"system"}', '{"type":"system"}'],
            start_offset=3,
            end_byte_offsets=[100, 120],
        ),
        MagicMock(),
        user,
    )

    assert response.acknowledged_line == 4
    assert response.acknowledged_offset == 120
