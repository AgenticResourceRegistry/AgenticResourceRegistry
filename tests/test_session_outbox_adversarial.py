# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Regressions for in-flight mutations and bounded outbox delivery."""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from typing import TYPE_CHECKING

import pytest

from observal_cli import telemetry_buffer as outbox
from observal_cli.harness import SessionSource
from observal_cli.sessions import base

if TYPE_CHECKING:
    from pathlib import Path


CFG = {"server_url": "http://server", "user_id": "user", "access_token": "token"}


def batch(session: str, start: int, *, final: bool = False) -> dict:
    return {
        "harness": "claude-code",
        "session_id": session,
        "start_offset": start,
        "lines": ["record"],
        "end_byte_offsets": [(start + 1) * 7],
        "final": final,
    }


@pytest.mark.parametrize("reject", [False, True])
def test_inflight_promotion_survives_old_ack_or_rejection(tmp_path: Path, reject: bool):
    db = tmp_path / "outbox.db"
    outbox.enqueue(batch("s", 0), destination="http://server", user_id="user", db_path=db)
    calls = []

    def post(payload, _config):
        calls.append(payload["final"])
        if len(calls) == 1:
            outbox.enqueue(batch("s", 0, final=True), destination="http://server", user_id="user", db_path=db)
            if reject:
                raise base.PermanentIngestRejectionError(422)
        return {"acknowledged_line": 0, "acknowledged_offset": 7}

    assert base.drain_outbox(CFG, home=tmp_path, db_path=db, post=post)
    assert calls == [False, True]
    assert outbox.stats(db_path=db)["pending"] == 0
    assert not outbox.has_rejections(destination="http://server", user_id="user", db_path=db)
    assert base.read_cursor_state("s", home=tmp_path) == (7, 1, True)


@pytest.mark.parametrize("bad_offset", [999, 3])
def test_repair_offset_beyond_posted_source_retains_batch_and_cursor(tmp_path: Path, bad_offset: int):
    db = tmp_path / "outbox.db"
    outbox.enqueue(batch("s", 0, final=True), destination="http://server", user_id="user", db_path=db)
    assert not base.drain_outbox(
        CFG,
        home=tmp_path,
        db_path=db,
        post=lambda *_: {"acknowledged_line": -1, "repair_from_line": 0, "acknowledged_offset": bad_offset},
    )
    assert base.read_cursor("s", home=tmp_path) == (0, 0)
    assert outbox.pending(destination="http://server", user_id="user", db_path=db)[0].final


def test_rejected_source_spools_append_before_rotation(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(base, "_resolve_agent", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(base, "_get_cached_layer_hash", lambda *_args, **_kwargs: None)
    db = tmp_path / "outbox.db"
    path = tmp_path / "session.jsonl"
    path.write_text('{"n":0}\n')
    source = SessionSource("claude-code", "s", path)

    def reject(*_args):
        raise base.PermanentIngestRejectionError(422)

    assert not base.drain_session_source(
        source, CFG, hook_event="UserPromptSubmit", home=tmp_path, db_path=db, post=reject
    )
    path.write_text('{"n":0}\n{"n":1}\n')
    assert not base.drain_session_source(
        source, CFG, hook_event="Stop", final=True, home=tmp_path, db_path=db, post=reject
    )
    path.unlink()  # The source is gone, but its newly appended line is durable.
    restored = outbox.recover_quarantined(1, db_path=db)
    assert restored > 0
    pending = outbox.pending(destination="http://server", user_id="user", db_path=db)
    assert [(item.start_line, item.payload["lines"]) for item in pending] == [(0, ['{"n":0}']), (1, ['{"n":1}'])]
    assert pending[-1].final


def test_durable_error_offsets_logged_without_transcript_on_retry(tmp_path: Path):
    db = tmp_path / "outbox.db"
    payload = batch("s", 0)
    payload["lines"] = ["SECRET_SOURCE_RECORD"]
    outbox.enqueue(payload, destination="http://server", user_id="user", db_path=db)
    response = {"acknowledged_line": 0, "acknowledged_offset": 7, "error_offsets": [0] * 15 + ["SECRET_SOURCE_RECORD"]}
    assert base.drain_outbox(CFG, home=tmp_path, db_path=db, post=lambda *_: response)
    text = (tmp_path / ".observal" / "sync.log").read_text()
    assert "source normalization error(s)" in text
    assert "SECRET_SOURCE_RECORD" not in text
    assert "[0, 0, 0, 0, 0, 0, 0, 0, 0, 0]" in text


def test_archive_interruption_keeps_active_batch_and_future_entries_readable(tmp_path: Path, monkeypatch):
    db = tmp_path / "outbox.db"
    outbox.enqueue(batch("s", 0), destination="http://server", user_id="user", db_path=db)
    item = outbox.pending(destination="http://server", user_id="user", db_path=db)[0]
    replace = outbox.os.replace

    def interrupted(*_args):
        raise OSError("interrupted rename")

    monkeypatch.setattr(outbox.os, "replace", interrupted)
    with pytest.raises(OSError, match="interrupted rename"):
        outbox.quarantine(item, "422", db_path=db)
    assert outbox.pending(destination="http://server", user_id="user", db_path=db)[0].payload == item.payload
    assert list(db.with_suffix(".rejected.d").iterdir()) == []
    monkeypatch.setattr(outbox.os, "replace", replace)
    outbox.quarantine(item, "422", db_path=db)
    outbox.enqueue(batch("another", 0), destination="http://server", user_id="user", db_path=db)
    next_item = outbox.pending(destination="http://server", user_id="user", db_path=db)[0]
    outbox.quarantine(next_item, "422", db_path=db)
    assert outbox.recover_quarantined(item.id, db_path=db) > 0
    assert outbox.recover_quarantined(next_item.id, db_path=db) > 0


def test_interruption_after_archive_publish_keeps_original_until_retry(tmp_path: Path, monkeypatch):
    db = tmp_path / "outbox.db"
    outbox.enqueue(batch("s", 0), destination="http://server", user_id="user", db_path=db)
    item = outbox.pending(destination="http://server", user_id="user", db_path=db)[0]
    connect = outbox._connect

    def interrupted(*_args, **_kwargs):
        raise outbox.OutboxError("interrupted DB transaction")

    monkeypatch.setattr(outbox, "_connect", interrupted)
    with pytest.raises(outbox.OutboxError, match="interrupted DB transaction"):
        outbox.quarantine(item, "422", db_path=db)
    monkeypatch.setattr(outbox, "_connect", connect)
    assert outbox.pending(destination="http://server", user_id="user", db_path=db)[0].payload == item.payload
    assert len(list(db.with_suffix(".rejected.d").glob("*.json"))) == 1
    outbox.quarantine(item, "422", db_path=db)
    assert len(list(db.with_suffix(".rejected.d").glob("*.json"))) == 2
    assert outbox.recover_quarantined(item.id, db_path=db) > 0


@pytest.mark.parametrize("stale_first", [True, False])
def test_quarantine_recovers_only_cas_archive_in_either_publication_order(tmp_path: Path, monkeypatch, stale_first):
    db = tmp_path / "outbox.db"
    outbox.enqueue(batch("s", 0), destination="http://server", user_id="user", db_path=db)
    stale = outbox.pending(destination="http://server", user_id="user", db_path=db)[0]
    publish = outbox._archive_entry
    if stale_first:

        def publish_during_promotion(record, path):
            orphan = publish(record, path)
            outbox.enqueue(batch("s", 0, final=True), destination="http://server", user_id="user", db_path=db)
            return orphan

        monkeypatch.setattr(outbox, "_archive_entry", publish_during_promotion)
        assert outbox.quarantine(stale, "422", db_path=db) is None
        monkeypatch.setattr(outbox, "_archive_entry", publish)
        final = outbox.pending(destination="http://server", user_id="user", db_path=db)[0]
        selected = outbox.quarantine(final, "422", db_path=db)
    else:
        outbox.enqueue(batch("s", 0, final=True), destination="http://server", user_id="user", db_path=db)
        final = outbox.pending(destination="http://server", user_id="user", db_path=db)[0]
        selected = outbox.quarantine(final, "422", db_path=db)
        # The stale publisher completes *after* the successful CAS publication.
        assert outbox.quarantine(stale, "422", db_path=db) is None
    assert selected is not None
    assert len(list(db.with_suffix(".rejected.d").glob("*.json"))) == 2
    with sqlite3.connect(db) as conn:
        ref = conn.execute("SELECT archive_ref FROM session_outbox_rejected WHERE item_id = ?", (stale.id,)).fetchone()[
            0
        ]
    assert ref == selected.name
    assert outbox.spooled_checkpoint(
        destination="http://server",
        user_id="user",
        harness="claude-code",
        session_id="s",
        checkpoint_key="s",
        line_count=0,
        byte_offset=0,
        db_path=db,
    ) == (7, 1)
    restored = outbox.recover_quarantined(stale.id, db_path=db)
    assert restored > 0
    assert outbox.pending(destination="http://server", user_id="user", db_path=db)[0].final


def test_missing_selected_archive_fails_closed_despite_matching_orphan(tmp_path: Path):
    db = tmp_path / "outbox.db"
    outbox.enqueue(batch("s", 0), destination="http://server", user_id="user", db_path=db)
    item = outbox.pending(destination="http://server", user_id="user", db_path=db)[0]
    selected = outbox.quarantine(item, "422", db_path=db)
    assert selected is not None
    outbox._archive_entry(json.loads(selected.read_text()), db)
    selected.unlink()
    with pytest.raises(outbox.OutboxError, match="archive missing"):
        outbox.recover_quarantined(item.id, db_path=db)
    assert outbox.has_rejections(destination="http://server", user_id="user", db_path=db)


def test_torn_legacy_tail_does_not_block_recovery_or_spooling(tmp_path: Path):
    db = tmp_path / "outbox.db"
    outbox.enqueue(batch("s", 0), destination="http://server", user_id="user", db_path=db)
    item = outbox.pending(destination="http://server", user_id="user", db_path=db)[0]
    archive = outbox.quarantine(item, "422", db_path=db)
    legacy = db.with_suffix(".rejected.jsonl")
    legacy.write_bytes((json.dumps(json.loads(archive.read_text())) + "\n").encode() + b'{"item_id":')
    archive.unlink()  # Model a pre-upgrade archive and a torn final append.
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE session_outbox_rejected SET archive_ref = 'legacy:0' WHERE item_id = ?", (item.id,))
    assert outbox.spooled_checkpoint(
        destination="http://server",
        user_id="user",
        harness="claude-code",
        session_id="s",
        checkpoint_key="s",
        line_count=0,
        byte_offset=0,
        db_path=db,
    ) == (7, 1)
    assert outbox.recover_quarantined(item.id, db_path=db) > 0
    outbox.enqueue(batch("later", 0), destination="http://server", user_id="user", db_path=db)
    later = next(
        i for i in outbox.pending(destination="http://server", user_id="user", db_path=db) if i.session_id == "later"
    )
    outbox.quarantine(later, "422", db_path=db)
    assert outbox.recover_quarantined(later.id, db_path=db) > 0
    assert legacy.read_bytes().endswith(b'{"item_id":')


def test_corrupt_legacy_middle_preserves_later_valid_entries(tmp_path: Path):
    db = tmp_path / "outbox.db"
    records = []
    for index in range(2):
        outbox.enqueue(batch(f"s{index}", 0), destination="http://server", user_id="user", db_path=db)
        item = outbox.pending(destination="http://server", user_id="user", db_path=db)[0]
        archived = outbox.quarantine(item, "422", db_path=db)
        records.append((item.id, archived.read_bytes()))
        archived.unlink()
    db.with_suffix(".rejected.jsonl").write_bytes(records[0][1] + b"\n{partial\n" + records[1][1] + b"\n")
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE session_outbox_rejected SET archive_ref = ? WHERE item_id = ?", ("legacy:0", records[0][0])
        )
        conn.execute(
            "UPDATE session_outbox_rejected SET archive_ref = ? WHERE item_id = ?",
            (f"legacy:{len(records[0][1]) + len(b'{partial') + 2}", records[1][0]),
        )
    assert outbox.recover_quarantined(records[1][0], db_path=db) > 0
    assert outbox.recover_quarantined(records[0][0], db_path=db) > 0


def test_legacy_archive_without_id_blocks_gap_and_recovers(tmp_path: Path):
    db = tmp_path / "outbox.db"
    legacy = db.with_suffix(".rejected.jsonl")
    legacy.write_text(
        json.dumps(
            {
                "destination": "http://server",
                "user_id": "user",
                "harness": "claude-code",
                "session_id": "s",
                "checkpoint_key": "s",
                "start_line": 0,
                "end_line": 0,
                "payload": batch("s", 0),
            }
        )
        + "\n{broken\n"
        + json.dumps(
            {
                "destination": "http://server",
                "user_id": "user",
                "harness": "claude-code",
                "session_id": "other-rejected",
                "checkpoint_key": "other-rejected",
                "start_line": 0,
                "end_line": 0,
                "payload": batch("other-rejected", 0),
            }
        )
        + "\n"
    )
    original = legacy.read_bytes()
    outbox.enqueue(batch("s", 1), destination="http://server", user_id="user", db_path=db)
    outbox.enqueue(batch("other", 0), destination="http://server", user_id="user", db_path=db)
    assert outbox.stats(db_path=db)["failed"] == 2
    assert [i.session_id for i in outbox.pending(destination="http://server", user_id="user", db_path=db)] == ["other"]
    assert not base.drain_outbox(
        CFG,
        home=tmp_path,
        db_path=db,
        post=lambda payload, _: {"acknowledged_line": payload["start_offset"], "acknowledged_offset": 7},
    )
    assert outbox.stats(db_path=db)["pending"] == 1
    assert outbox.recover_quarantined(-1, db_path=db) > 0
    assert [i.start_line for i in outbox.pending(destination="http://server", user_id="user", db_path=db)] == [0, 1]
    assert outbox.stats(db_path=db)["failed"] == 1
    assert (
        base.drain_outbox(
            CFG,
            home=tmp_path,
            db_path=db,
            post=lambda payload, _: {
                "acknowledged_line": payload["start_offset"],
                "acknowledged_offset": payload["end_byte_offsets"][-1],
            },
        )
        is False
    )  # The unrelated legacy rejection remains blocked.
    assert outbox.stats(db_path=db)["failed"] == 1
    assert not outbox.is_rejected(
        destination="http://server", user_id="user", harness="claude-code", session_id="s", db_path=db
    )
    with pytest.raises(outbox.OutboxError, match="already recovered"):
        outbox.recover_quarantined(-1, db_path=db)
    assert legacy.read_bytes() == original


def _legacy_entry(session: str) -> str:
    return (
        json.dumps(
            {
                "destination": "http://server",
                "user_id": "user",
                "harness": "claude-code",
                "session_id": session,
                "checkpoint_key": session,
                "start_line": 0,
                "end_line": 0,
                "payload": batch(session, 0),
            }
        )
        + "\n"
    )


def test_legacy_import_runs_once_per_db_across_repeated_connections(tmp_path: Path, monkeypatch):
    db = tmp_path / "outbox.db"
    db.with_suffix(".rejected.jsonl").write_text(_legacy_entry("s"))
    records = outbox._legacy_records
    scans = 0

    def counting(path):
        nonlocal scans
        scans += 1
        yield from records(path)

    monkeypatch.setattr(outbox, "_legacy_records", counting)
    for _ in range(4):
        assert outbox.stats(db_path=db)["failed"] == 1
        assert outbox.is_rejected(
            destination="http://server", user_id="user", harness="claude-code", session_id="s", db_path=db
        )
    assert scans == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT id FROM session_outbox_legacy_import").fetchall() == [(1,)]


@pytest.mark.parametrize("initial", ["missing", "empty", "imported"])
def test_old_worker_append_after_marker_blocks_deleted_active_gap(tmp_path: Path, initial: str):
    db = tmp_path / "outbox.db"
    legacy = db.with_suffix(".rejected.jsonl")
    if initial != "missing":
        legacy.write_text(_legacy_entry("first") if initial == "imported" else "")
    outbox.enqueue(batch("gap", 1), destination="http://server", user_id="user", db_path=db)
    assert outbox.stats(db_path=db)["failed"] == (1 if initial == "imported" else 0)
    with legacy.open("a") as archive:
        archive.write(_legacy_entry("gap"))
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM session_outbox WHERE session_id = 'gap'")
    outbox.enqueue(batch("gap", 1), destination="http://server", user_id="user", db_path=db)

    def reopen():
        return outbox.is_rejected(
            destination="http://server", user_id="user", harness="claude-code", session_id="gap", db_path=db
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(pool.map(lambda _: reopen(), range(4)))
    assert outbox.pending(destination="http://server", user_id="user", db_path=db) == []
    assert outbox.stats(db_path=db)["failed"] == (2 if initial == "imported" else 1)


def test_id_only_import_marker_rechecks_archive_and_upgrades(tmp_path: Path):
    db = tmp_path / "outbox.db"
    legacy = db.with_suffix(".rejected.jsonl")
    outbox.stats(db_path=db)
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE session_outbox_legacy_import")
        conn.execute("CREATE TABLE session_outbox_legacy_import (id INTEGER PRIMARY KEY CHECK (id = 1))")
        conn.execute("INSERT INTO session_outbox_legacy_import VALUES (1)")
    legacy.write_text(_legacy_entry("gap"))
    assert outbox.is_rejected(
        destination="http://server", user_id="user", harness="claude-code", session_id="gap", db_path=db
    )
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT size FROM session_outbox_legacy_import").fetchone() == (legacy.stat().st_size,)


def test_append_during_scan_rolls_back_marker_and_retries(tmp_path: Path, monkeypatch):
    db = tmp_path / "outbox.db"
    legacy = db.with_suffix(".rejected.jsonl")
    legacy.write_text(_legacy_entry("first"))
    records = outbox._legacy_records

    def appending(path):
        yield from records(path)
        with legacy.open("a") as archive:
            archive.write(_legacy_entry("late"))

    monkeypatch.setattr(outbox, "_legacy_records", appending)
    with pytest.raises(outbox.OutboxError, match="changed during import"):
        outbox.stats(db_path=db)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM session_outbox_rejected").fetchone() == (0,)
        assert conn.execute("SELECT COUNT(*) FROM session_outbox_legacy_import").fetchone() == (0,)
    monkeypatch.setattr(outbox, "_legacy_records", records)
    assert outbox.stats(db_path=db)["failed"] == 2


@pytest.mark.parametrize(("field", "value"), [("end_line", 17), ("item_id", None), ("item_id", 3), ("item_id", "-1")])
def test_invalid_identifiable_legacy_row_fails_closed(tmp_path: Path, field: str, value: object):
    db = tmp_path / "outbox.db"
    record = json.loads(_legacy_entry("s"))
    record[field] = value
    db.with_suffix(".rejected.jsonl").write_text(json.dumps(record) + "\n")
    with pytest.raises(outbox.OutboxError, match="invalid legacy quarantine record"):
        outbox.pending(destination="http://server", user_id="user", db_path=db)


def test_interrupted_legacy_import_rolls_back_rows_and_retries(tmp_path: Path, monkeypatch):
    db = tmp_path / "outbox.db"
    db.with_suffix(".rejected.jsonl").write_text(_legacy_entry("s") + _legacy_entry("later"))
    records = outbox._legacy_records

    def interrupted(path):
        for record in records(path):
            yield record
            raise OSError("interrupted scan")

    monkeypatch.setattr(outbox, "_legacy_records", interrupted)
    with pytest.raises(OSError, match="interrupted scan"):
        outbox.stats(db_path=db)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM session_outbox_rejected").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM session_outbox_legacy_import").fetchone()[0] == 0
    monkeypatch.setattr(outbox, "_legacy_records", records)
    assert outbox.stats(db_path=db)["failed"] == 2
    assert outbox.stats(db_path=db)["failed"] == 2
    assert outbox.recover_quarantined(-1, db_path=db) > 0


def test_concurrent_legacy_import_scans_once(tmp_path: Path, monkeypatch):
    db = tmp_path / "outbox.db"
    db.with_suffix(".rejected.jsonl").write_text(_legacy_entry("s"))
    records = outbox._legacy_records
    barrier = Barrier(2)
    lock = Lock()
    scans = 0

    def counting(path):
        nonlocal scans
        with lock:
            scans += 1
        yield from records(path)

    monkeypatch.setattr(outbox, "_legacy_records", counting)

    def open_db():
        barrier.wait(timeout=5)
        return outbox.stats(db_path=db)["failed"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(open_db) for _ in range(2)]
        assert [future.result(timeout=10) for future in futures] == [1, 1]
    assert scans == 1


def test_concurrent_legacy_schema_upgrade_is_serialized(tmp_path: Path):
    db = tmp_path / "outbox.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE session_outbox_legacy_import (id INTEGER PRIMARY KEY)")
    barrier = Barrier(4)

    def open_db():
        barrier.wait(timeout=5)
        return outbox.stats(db_path=db)["failed"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(open_db) for _ in range(4)]
        assert [future.result(timeout=10) for future in futures] == [0] * 4
    with sqlite3.connect(db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(session_outbox_legacy_import)")}
        assert {"device", "inode", "size", "mtime_ns", "ctime_ns"} <= columns


def test_ack_started_before_repair_cannot_overwrite_rewind(tmp_path: Path):
    db = tmp_path / "outbox.db"
    outbox.enqueue(batch("s", 0), destination="http://server", user_id="user", db_path=db)
    base.write_cursor("s", 7, 1, home=tmp_path)
    calls = 0

    def post(payload, _config):
        nonlocal calls
        calls += 1
        if calls == 1:
            assert not base.drain_outbox(
                CFG,
                home=tmp_path,
                db_path=db,
                post=lambda *_: {"acknowledged_line": -1, "repair_from_line": 0, "acknowledged_offset": 0},
            )
            return {"acknowledged_line": 0, "acknowledged_offset": 7}
        return None

    assert not base.drain_outbox(CFG, home=tmp_path, db_path=db, post=post)
    assert base.read_cursor("s", home=tmp_path) == (0, 0)
    # Repair deliberately removes its posted batch to allow source re-spooling.
    assert outbox.stats(db_path=db)["pending"] == 0


def test_stale_ack_retains_batch_after_cursor_rewind(tmp_path: Path):
    db = tmp_path / "outbox.db"
    outbox.enqueue(batch("s", 0), destination="http://server", user_id="user", db_path=db)
    base.write_cursor("s", 7, 1, home=tmp_path)

    def post(_payload, _config):
        base.write_cursor("s", 0, 0, home=tmp_path, preserve_finalized=False)
        return {"acknowledged_line": 0, "acknowledged_offset": 7}

    assert not base.drain_outbox(CFG, home=tmp_path, db_path=db, post=post)
    assert base.read_cursor("s", home=tmp_path) == (0, 0)
    assert outbox.stats(db_path=db)["pending"] == 1


def test_metadata_final_gets_turn_amid_sustained_source_load(tmp_path: Path):
    db = tmp_path / "outbox.db"
    for index in range(110):
        outbox.enqueue(batch("busy", index), destination="http://server", user_id="user", db_path=db)
    outbox.enqueue(
        {
            "harness": "claude-code",
            "session_id": "done",
            "start_offset": 0,
            "lines": [],
            "final": True,
            "total_offset": 0,
        },
        destination="http://server",
        user_id="user",
        db_path=db,
    )
    posted = []

    def post(payload, _config):
        posted.append(payload["session_id"])
        return {
            "acknowledged_line": payload["start_offset"] + len(payload["lines"]) - 1,
            "acknowledged_offset": (payload.get("end_byte_offsets") or [0])[-1],
        }

    assert not base.drain_outbox(CFG, home=tmp_path, db_path=db, post=post)
    assert "done" in posted[:3]
    assert len(posted) == 50
    assert outbox.stats(db_path=db)["pending"] == 61
