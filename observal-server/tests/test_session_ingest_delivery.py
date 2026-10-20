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


@pytest.mark.asyncio
async def test_verified_trailing_bytes_reconcile_checkpoint_before_final_ack(monkeypatch):
    line = '{"type":"system"}'
    source_digest = hashlib.sha256(line.encode()).hexdigest()
    final_hash = hashlib.sha256(f"{source_digest}\n".encode()).hexdigest()
    monkeypatch.setattr(
        session_ingest, "query_session_source_manifest", AsyncMock(return_value=[(0, 18, source_digest)])
    )
    monkeypatch.setattr(
        session_ingest,
        "ingest_session_lines",
        AsyncMock(return_value=session_ingest.IngestResult(ingested=0, skipped=1, errors=0)),
    )
    monkeypatch.setattr(session_ingest, "advance_session_checkpoint", AsyncMock(return_value=(0, 18)))
    monkeypatch.setattr(clickhouse, "query_session_error_offsets", AsyncMock(return_value=[]))
    checkpoint = AsyncMock()
    monkeypatch.setattr(clickhouse, "insert_session_checkpoint", checkpoint)
    user = MagicMock(id="user")
    req = SessionIngestRequest(
        session_id="session",
        harness="claude-code",
        lines=[line],
        start_offset=0,
        end_byte_offsets=[19],
        final=True,
        total_line_count=1,
        total_offset=19,
        session_hash=final_hash,
        hashed_line_count=1,
    )
    response = await ingest_route.ingest_session.__wrapped__(req, MagicMock(), user)
    assert response.integrity_ok is True
    assert response.repair_from_line is None
    assert response.acknowledged_offset == 19
    checkpoint.assert_awaited_once_with("session", ingest_route.DEFAULT_PROJECT_ID, "user", "claude-code", 0, 19)

    # A shorter file with the same authenticated records can also reconcile.
    shrunk = await session_ingest.check_session_integrity(
        "session",
        "project",
        "user",
        "claude-code",
        1,
        17,
        acknowledged_line=0,
        acknowledged_offset=18,
        expected_hash=final_hash,
        hashed_line_count=1,
    )
    assert shrunk.ok and shrunk.reconciled_offset and shrunk.repair_from_line is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("harness", "manifest_offsets", "expected_offset"),
    [
        ("claude-code", [18], 21),  # blank bytes appended after the record
        ("claude-code", [21], 18),  # blank bytes removed after the record
        ("kiro", [18, 18], 21),  # synthetic prompt follows the hashed file record
        ("kiro", [21, 21], 18),
        ("cursor", [18, 21], 18),  # file prefix replayed; suffix retained at former end
        ("cursor", [21, 18], 21),
    ],
)
async def test_offset_only_final_reconciles_both_directions(monkeypatch, harness, manifest_offsets, expected_offset):
    digest = hashlib.sha256(b'{"type":"system"}').hexdigest()
    final_hash = hashlib.sha256(f"{digest}\n".encode()).hexdigest()
    manifest = [(index, offset, digest if index == 0 else "synthetic") for index, offset in enumerate(manifest_offsets)]
    monkeypatch.setattr(session_ingest, "query_session_source_manifest", AsyncMock(return_value=manifest))
    monkeypatch.setattr(
        session_ingest,
        "ingest_session_lines",
        AsyncMock(return_value=session_ingest.IngestResult(ingested=0, skipped=1, errors=0)),
    )
    monkeypatch.setattr(
        session_ingest, "advance_session_checkpoint", AsyncMock(return_value=(len(manifest) - 1, manifest_offsets[-1]))
    )
    monkeypatch.setattr(clickhouse, "query_session_error_offsets", AsyncMock(return_value=[]))
    checkpoint = AsyncMock()
    monkeypatch.setattr(clickhouse, "insert_session_checkpoint", checkpoint)
    req = SessionIngestRequest(
        session_id="session",
        harness=harness,
        lines=[],
        final=True,
        total_line_count=len(manifest),
        total_offset=expected_offset,
        session_hash=final_hash,
        hashed_line_count=1,
    )
    response = await ingest_route.ingest_session.__wrapped__(req, MagicMock(), MagicMock(id="user"))
    assert response.integrity_ok is True
    assert response.repair_from_line is None
    assert response.acknowledged_line == len(manifest) - 1
    assert response.acknowledged_offset == expected_offset
    checkpoint.assert_awaited_once_with(
        "session", ingest_route.DEFAULT_PROJECT_ID, "user", harness, len(manifest) - 1, expected_offset
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("manifest", "hash_count", "expected_hash"),
    [
        ([], 1, "match"),
        ([(0, 18, "file")], 1, "match"),  # missing synthetic suffix
        ([(0, 18, "file"), (2, 18, "synthetic")], 1, "match"),  # gap
        ([(0, 18, "file"), (1, 18, "synthetic"), (2, 18, "extra")], 1, "match"),
        ([(0, 18, "file"), (1, 19, "synthetic")], 1, "match"),  # suffix has wrong byte accounting
        ([(0, 18, "changed"), (1, 18, "synthetic")], 1, "match"),
        ([(0, 18, "file"), (1, 18, "synthetic")], 1, None),  # no hash to authenticate source
        ([(0, 18, "file"), (1, 18, "synthetic")], 0, "match"),
        ([(0, 18, "file"), (1, 19, "synthetic"), (2, 20, "synthetic")], 1, "match"),
    ],
)
async def test_offset_reconciliation_rejects_unverified_manifest(monkeypatch, manifest, hash_count, expected_hash):
    monkeypatch.setattr(session_ingest, "query_session_source_manifest", AsyncMock(return_value=manifest))
    digest = hashlib.sha256(b"file\n").hexdigest() if expected_hash is not None else None
    result = await session_ingest.check_session_integrity(
        "session",
        "project",
        "user",
        "kiro",
        2,
        21,
        acknowledged_line=1,
        acknowledged_offset=18,
        expected_hash=digest,
        hashed_line_count=hash_count,
    )
    assert not result.ok
    assert not result.reconciled_offset


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("manifest", "hash_count", "expected_offset", "acknowledged_offset", "valid_hash"),
    [
        ([(0, 18, "file"), (1, 20, "a"), (2, 21, "b")], 1, 18, 21, True),  # split former ends
        ([(0, 18, "file"), (2, 21, "a"), (3, 21, "b")], 1, 18, 21, True),  # gap
        ([(0, 18, "changed"), (1, 21, "a"), (2, 21, "b")], 1, 18, 21, True),
        ([(0, 18, "file"), (1, 21, "a"), (2, 21, "b")], 4, 18, 21, True),
        ([(0, 18, "file"), (1, 21, "a"), (2, 21, "b")], -1, 18, 21, True),
        ([(0, 18, "file"), (1, 21, "a"), (2, 21, "b")], 1, 2**64, 21, True),
        ([(0, 18, "file"), (1, 21, "a"), (2, 21, "b")], 1, 18, 2**64, True),
        ([(0, 18, "file"), (1, 21, "a"), (2, 21, "b")], 1, 18, 21, False),
    ],
)
async def test_former_suffix_offset_requires_authenticated_contiguous_bounded_manifest(
    monkeypatch, manifest, hash_count, expected_offset, acknowledged_offset, valid_hash
):
    monkeypatch.setattr(session_ingest, "query_session_source_manifest", AsyncMock(return_value=manifest))
    result = await session_ingest.check_session_integrity(
        "session",
        "project",
        "user",
        "cursor",
        3,
        expected_offset,
        acknowledged_line=2,
        acknowledged_offset=acknowledged_offset,
        expected_hash=hashlib.sha256(b"file\n").hexdigest() if valid_hash else None,
        hashed_line_count=hash_count,
    )
    assert not result.ok and not result.reconciled_offset


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "offsets,expected_offset,accepted",
    [
        ([18, 18], 21, True),
        ([21, 21], 18, True),
        ([18, 19], 21, False),
        ([18], 21, False),
        ([18, 18, 18], 21, False),
    ],
)
async def test_synthetic_only_empty_file_hash_reconciles_offset(monkeypatch, offsets, expected_offset, accepted):
    manifest = [(index, offset, "synthetic") for index, offset in enumerate(offsets)]
    monkeypatch.setattr(session_ingest, "query_session_source_manifest", AsyncMock(return_value=manifest))
    result = await session_ingest.check_session_integrity(
        "session",
        "project",
        "user",
        "cursor",
        2,
        expected_offset,
        acknowledged_line=1,
        acknowledged_offset=offsets[-1],
        expected_hash=hashlib.sha256(b"").hexdigest(),
        hashed_line_count=0,
    )
    assert result.ok is accepted
    assert result.reconciled_offset is accepted


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("total_offset", "expected_ok", "expected_checkpoint"),
    [(0, True, 0), (None, True, None)],
)
async def test_synthetic_only_explicit_zero_rebases_but_omitted_keeps_legacy_checkpoint(
    monkeypatch, total_offset, expected_ok, expected_checkpoint
):
    manifest = [(0, 18, "synthetic")]
    monkeypatch.setattr(session_ingest, "query_session_source_manifest", AsyncMock(return_value=manifest))
    monkeypatch.setattr(
        session_ingest, "ingest_session_lines", AsyncMock(return_value=session_ingest.IngestResult(0, 0, 0))
    )
    monkeypatch.setattr(session_ingest, "advance_session_checkpoint", AsyncMock(return_value=(0, 18)))
    monkeypatch.setattr(clickhouse, "query_session_error_offsets", AsyncMock(return_value=[]))
    checkpoint = AsyncMock()
    monkeypatch.setattr(clickhouse, "insert_session_checkpoint", checkpoint)
    values = dict(
        session_id="session",
        harness="cursor",
        lines=[],
        final=True,
        total_line_count=1,
        session_hash=hashlib.sha256(b"").hexdigest(),
        hashed_line_count=0,
    )
    if total_offset is not None:
        values["total_offset"] = total_offset
    req = SessionIngestRequest(**values)
    response = await ingest_route.ingest_session.__wrapped__(req, MagicMock(), MagicMock(id="user"))
    assert response.integrity_ok is expected_ok
    assert response.acknowledged_offset == (18 if expected_checkpoint is None else expected_checkpoint)
    if expected_checkpoint is None:
        checkpoint.assert_not_awaited()
    else:
        checkpoint.assert_awaited_once_with(
            "session", ingest_route.DEFAULT_PROJECT_ID, "user", "cursor", 0, expected_checkpoint
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("manifest", "expected_hash"),
    [
        ([(0, 18, "synthetic")], None),
        ([], hashlib.sha256(b"").hexdigest()),
        ([(1, 18, "synthetic")], hashlib.sha256(b"").hexdigest()),
        ([(0, 18, "synthetic"), (2, 18, "synthetic")], hashlib.sha256(b"").hexdigest()),
    ],
)
async def test_synthetic_only_zero_rejects_unverified_or_gapped_manifest(monkeypatch, manifest, expected_hash):
    monkeypatch.setattr(session_ingest, "query_session_source_manifest", AsyncMock(return_value=manifest))
    result = await session_ingest.check_session_integrity(
        "session",
        "project",
        "user",
        "cursor",
        2 if len(manifest) == 2 else 1,
        0,
        acknowledged_line=1 if len(manifest) == 2 else 0,
        acknowledged_offset=18,
        expected_hash=expected_hash,
        hashed_line_count=0,
    )
    assert not result.ok and not result.reconciled_offset


@pytest.mark.asyncio
async def test_synthetic_only_rejects_noncanonical_empty_hash(monkeypatch):
    monkeypatch.setattr(session_ingest, "query_session_source_manifest", AsyncMock(return_value=[(0, 18, "synthetic")]))
    result = await session_ingest.check_session_integrity(
        "session",
        "project",
        "user",
        "cursor",
        1,
        21,
        acknowledged_line=0,
        acknowledged_offset=18,
        expected_hash="not-empty-hash",
        hashed_line_count=0,
    )
    assert not result.ok and not result.reconciled_offset
    assert result.repair_from_line == 0


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
