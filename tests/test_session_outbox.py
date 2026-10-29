# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from observal_cli import telemetry_buffer as outbox

if TYPE_CHECKING:
    from pathlib import Path


def payload(start: int, lines: list[str], *, final: bool = False) -> dict:
    return {
        "session_id": "session",
        "harness": "claude-code",
        "lines": lines,
        "start_offset": start,
        "end_byte_offsets": [(start + i + 1) * 10 for i in range(len(lines))],
        "final": final,
    }


def test_outbox_survives_reopen_and_acknowledges_only_contiguous_batches(tmp_path: Path):
    db = tmp_path / "outbox.db"
    outbox.enqueue(payload(0, ["zero", "one"]), destination="http://server", user_id="user", db_path=db)
    outbox.enqueue(payload(2, ["two"]), destination="http://server", user_id="user", db_path=db)

    restarted = outbox.pending(destination="http://server", user_id="user", db_path=db)
    assert [(item.start_line, item.end_line) for item in restarted] == [(0, 1), (2, 2)]

    assert (
        outbox.acknowledge(
            destination="http://server",
            user_id="user",
            harness="claude-code",
            session_id="session",
            acknowledged_line=1,
            db_path=db,
        )
        == 1
    )
    assert [item.start_line for item in outbox.pending(destination="http://server", user_id="user", db_path=db)] == [2]


def test_requeue_is_idempotent_and_can_promote_final_payload(tmp_path: Path):
    db = tmp_path / "outbox.db"
    first = outbox.enqueue(payload(0, ["zero"]), destination="http://server", user_id="user", db_path=db)
    final = outbox.enqueue(
        payload(0, ["zero"], final=True),
        destination="http://server/",
        user_id="user",
        checkpoint_key="cursor-key",
        db_path=db,
    )

    assert first == final
    items = outbox.pending(destination="http://server", user_id="user", db_path=db)
    assert len(items) == 1
    assert items[0].final
    assert items[0].checkpoint_key == "cursor-key"
    assert items[0].payload["final"] is True


def test_metadata_only_batch_is_durable_and_replaceable(tmp_path: Path):
    db = tmp_path / "outbox.db"
    first = payload(2, []) | {"total_credits": 1.0}
    latest = payload(2, [], final=True) | {"total_credits": 2.0}

    outbox.enqueue(first, destination="http://server", user_id="user", db_path=db)
    outbox.enqueue(latest, destination="http://server", user_id="user", db_path=db)

    item = outbox.pending(destination="http://server", user_id="user", db_path=db)[0]
    assert item.start_line == 2
    assert item.end_line == 1
    assert item.final
    assert item.payload["total_credits"] == 2.0


def test_source_batches_drain_before_metadata_waiting_on_their_checkpoint(tmp_path: Path):
    db = tmp_path / "outbox.db"
    outbox.enqueue(
        payload(2, []) | {"total_credits": 2.0},
        destination="http://server",
        user_id="user",
        db_path=db,
    )
    outbox.enqueue(payload(0, ["zero", "one"]), destination="http://server", user_id="user", db_path=db)

    items = outbox.pending(destination="http://server", user_id="user", db_path=db)
    assert [(item.start_line, item.end_line) for item in items] == [(0, 1), (2, 1)]

    outbox.acknowledge(
        destination="http://server",
        user_id="user",
        harness="claude-code",
        session_id="session",
        acknowledged_line=1,
        db_path=db,
    )
    remaining = outbox.pending(destination="http://server", user_id="user", db_path=db)
    assert [(item.start_line, item.end_line) for item in remaining] == [(2, 1)]


def test_requeue_rejects_different_content_for_same_source_range(tmp_path: Path):
    db = tmp_path / "outbox.db"
    outbox.enqueue(payload(0, ["original"]), destination="http://server", user_id="user", db_path=db)

    with pytest.raises(outbox.OutboxConflictError):
        outbox.enqueue(payload(0, ["changed"]), destination="http://server", user_id="user", db_path=db)

    assert outbox.pending(destination="http://server", user_id="user", db_path=db)[0].payload["lines"] == ["original"]


def test_spooled_checkpoint_advances_across_pending_ranges(tmp_path: Path):
    db = tmp_path / "outbox.db"
    outbox.enqueue(payload(0, ["zero", "one"]), destination="http://server", user_id="user", db_path=db)
    outbox.enqueue(payload(2, ["two"]), destination="http://server", user_id="user", db_path=db)

    assert outbox.spooled_checkpoint(
        destination="http://server",
        user_id="user",
        harness="claude-code",
        session_id="session",
        checkpoint_key="session",
        line_count=0,
        byte_offset=0,
        db_path=db,
    ) == (30, 3)


def test_failed_attempts_never_become_terminal(tmp_path: Path):
    db = tmp_path / "outbox.db"
    item_id = outbox.enqueue(payload(0, ["zero"]), destination="http://server", user_id="user", db_path=db)

    for _ in range(20):
        outbox.record_attempt(item_id, db_path=db)

    item = outbox.pending(destination="http://server", user_id="user", db_path=db)[0]
    assert item.attempts == 20


def test_capacity_failure_never_evicts_existing_records(tmp_path: Path):
    db = tmp_path / "outbox.db"
    outbox.enqueue(
        payload(0, ["kept"]),
        destination="http://server",
        user_id="user",
        db_path=db,
        max_bytes=128 * 1024,
    )

    with pytest.raises(outbox.OutboxFullError):
        outbox.enqueue(
            payload(1, [json.dumps({"large": "x" * 200_000})]),
            destination="http://server",
            user_id="user",
            db_path=db,
            max_bytes=128 * 1024,
        )

    items = outbox.pending(destination="http://server", user_id="user", db_path=db)
    assert len(items) == 1
    assert items[0].payload["lines"] == ["kept"]


def test_database_is_owner_only_and_stats_track_sync(tmp_path: Path):
    db = tmp_path / "outbox.db"
    outbox.enqueue(payload(0, ["zero"]), destination="http://server", user_id="user", db_path=db)
    assert db.stat().st_mode & 0o777 == 0o600
    assert outbox.stats(db_path=db)["pending"] == 1

    outbox.acknowledge(
        destination="http://server",
        user_id="user",
        harness="claude-code",
        session_id="session",
        acknowledged_line=0,
        db_path=db,
    )
    stats = outbox.stats(db_path=db)
    assert stats["pending"] == 0
    assert stats["last_sync"] is not None
