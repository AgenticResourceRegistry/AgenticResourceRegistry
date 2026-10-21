# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Terminal recovery of held Codex outbox rows without attributed replay."""

import json
import multiprocessing
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from typer.testing import CliRunner

from observal_cli import cmd_reconcile_cli
from observal_cli import telemetry_buffer as outbox
from observal_cli.harness import SessionSource
from observal_cli.main import app
from observal_cli.sessions.base import drain_outbox, drain_session_source


def _queue(db, *, user="user", session="held", line="secret", start=0):
    body = {
        "harness": "codex",
        "session_id": session,
        "agent_id": "delegated-agent",
        "agent_version": "1",
        "start_offset": start,
        "lines": [line],
        "end_byte_offsets": [start + len(line) + 1],
    }
    row_id = outbox.enqueue(body, destination="http://server", user_id=user, db_path=db)
    return row_id, body


def _hold(db, *, user="user", session="held"):
    item = next(
        row
        for row in outbox.pending(destination="http://server", user_id=user, db_path=db)
        if row.session_id == session
    )
    assert outbox.hold_if_current(item, "identity unverified", db_path=db)
    return item


def _setup(tmp_path, monkeypatch):
    db = tmp_path / "queue.db"
    monkeypatch.setattr(outbox, "DB_PATH", db)
    monkeypatch.setattr(cmd_reconcile_cli, "load_config", lambda: {"server_url": "http://server", "user_id": "user"})
    return db


def test_missing_or_truncated_rollout_never_replays_but_archives_original(tmp_path, monkeypatch):
    db = _setup(tmp_path, monkeypatch)
    from observal_cli.harness import ensure_loaded, get_adapter

    ensure_loaded()
    adapter = get_adapter("codex")
    missing = tmp_path / "missing.jsonl"
    monkeypatch.setattr(
        adapter,
        "resolve_session_source",
        lambda event: adapter._source(tmp_path / "truncated.jsonl") if event["session_id"] == "truncated" else None,
    )
    for session in ("missing", "truncated"):
        path = missing if session == "missing" else tmp_path / "truncated.jsonl"
        if session == "truncated":
            path.write_text("incomplete")
        row_id, body = _queue(db, session=session)
        _hold(db, session=session)
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE session_outbox SET provenance = ? WHERE id = ?", ('{"original":"proof"}', row_id))
        refused = CliRunner().invoke(app, ["reconcile", "--harness", "codex", "--review-unattributed", session])
        assert refused.exit_code != 0
        assert outbox.is_held("http://server", "user", "codex", session, db_path=db)
        result = CliRunner().invoke(
            app, ["reconcile", "--harness", "codex", "--quarantine-held", session, "--yes", "--output", "json"]
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == {"session_id": session, "status": "quarantined", "rows": 1}
        assert "secret" not in result.output
        archive = db.with_suffix(".quarantined.jsonl")
        stored = next(row for row in map(json.loads, archive.read_text().splitlines()) if row["id"] == row_id)
        assert json.loads(stored["payload"]) == body
        assert (stored["provenance"], stored["status"], stored["reason"]) == (
            '{"original":"proof"}',
            "quarantined",
            "identity unverified",
        )
        assert os.stat(archive).st_mode & 0o777 == 0o600
        assert not outbox.is_held("http://server", "user", "codex", session, db_path=db)
    assert not outbox.pending(destination="http://server", user_id="user", db_path=db)


def test_terminal_archive_is_all_or_nothing_and_ack_cannot_delete_held_rows(tmp_path):
    db = tmp_path / "queue.db"
    _queue(db)
    _queue(db, start=1, line="second")
    stale = _hold(db)
    assert not outbox.accept_item(stale.id, db_path=db, revision=stale.revision)
    assert not outbox.acknowledge(
        destination="http://server",
        user_id="user",
        harness="codex",
        session_id="held",
        acknowledged_line=999,
        expected_item=stale,
        db_path=db,
    )
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TRIGGER fail_second BEFORE INSERT ON session_outbox_terminal "
            "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
        )
    with pytest.raises(outbox.OutboxError):
        outbox.quarantine_held_session("http://server", "user", "codex", "held", db_path=db)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM session_outbox_terminal").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM session_outbox").fetchone()[0] == 2
        conn.execute("DROP TRIGGER fail_second")
    assert outbox.is_held("http://server", "user", "codex", "held", db_path=db)
    assert len(db.with_suffix(".quarantined.jsonl").read_text().splitlines()) == 2
    assert outbox.quarantine_held_session("http://server", "user", "codex", "held", db_path=db) == 2
    assert not outbox.acknowledge(
        destination="http://server",
        user_id="user",
        harness="codex",
        session_id="held",
        acknowledged_line=999,
        expected_item=stale,
        db_path=db,
    )
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT archived_rows FROM session_outbox_terminal").fetchone()[0] == 2


def test_concurrent_ack_cannot_steal_held_session_archive(tmp_path):
    db = tmp_path / "queue.db"
    _queue(db)
    stale = _hold(db)
    barrier = threading.Barrier(2)

    def ack():
        barrier.wait()
        return outbox.acknowledge(
            destination="http://server",
            user_id="user",
            harness="codex",
            session_id="held",
            acknowledged_line=999,
            expected_item=stale,
            db_path=db,
        )

    def archive():
        barrier.wait()
        return outbox.quarantine_held_session("http://server", "user", "codex", "held", db_path=db)

    with ThreadPoolExecutor(max_workers=2) as pool:
        ack_future = pool.submit(ack)
        archive_future = pool.submit(archive)
        assert ack_future.result() == 0
        assert archive_future.result() == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT archived_rows FROM session_outbox_terminal").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM session_outbox").fetchone()[0] == 0


def _spawn_test_imports(monkeypatch):
    # spawn.prepare overwrites the child's sys.path with the parent's sys.path;
    # PYTHONPATH alone is insufficient when pytest runs from observal-server.
    checkout = str(Path(__file__).resolve().parents[1])
    monkeypatch.syspath_prepend(checkout)
    monkeypatch.setenv("PYTHONPATH", checkout, prepend=os.pathsep)


def _child_claim(db, entered, resume):
    item = outbox.pending(destination="http://server", user_id="user", db_path=db)[0]
    token = outbox.claim_for_delivery(item, db_path=db)
    assert token
    entered.set()
    assert resume.wait(10)
    outbox.release_delivery_claim(item, token, db_path=db)


def test_claim_serializes_across_processes(tmp_path, monkeypatch):
    db = tmp_path / "queue.db"
    _queue(db)
    _spawn_test_imports(monkeypatch)
    context = multiprocessing.get_context("spawn")
    entered, resume = context.Event(), context.Event()
    item = outbox.pending(destination="http://server", user_id="user", db_path=db)[0]
    child = context.Process(target=_child_claim, args=(db, entered, resume))
    child.start()
    try:
        assert entered.wait(10)
        assert outbox.pending(destination="http://server", user_id="user", db_path=db) == []
        assert outbox.claim_for_delivery(item, db_path=db) is None
        assert outbox.hold_if_current(item, "identity unverified", db_path=db)
        with pytest.raises(outbox.OutboxConflictError, match="in-flight delivery claim"):
            outbox.quarantine_held_session("http://server", "user", "codex", "held", db_path=db)
    finally:
        resume.set()
        child.join(10)
        if child.is_alive():
            child.terminate()
            child.join()
    assert child.exitcode == 0
    assert outbox.quarantine_held_session("http://server", "user", "codex", "held", db_path=db) == 1


def test_paused_post_blocks_terminal_quarantine_across_sqlite_connections(tmp_path, monkeypatch):
    db = tmp_path / "queue.db"
    _queue(db)
    item = outbox.pending(destination="http://server", user_id="user", db_path=db)[0]
    entered = threading.Event()
    resume = threading.Event()
    # Exercise the real drain with the same shared SQLite file used by other processes.
    from observal_cli.harness import ensure_loaded, get_adapter

    ensure_loaded()
    monkeypatch.setattr(get_adapter("codex"), "validate_outbox_provenance", lambda _item: True)

    def post(_payload, _cfg):
        entered.set()
        assert resume.wait(5)
        return None

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(drain_outbox, {"server_url": "http://server", "user_id": "user"}, db_path=db, post=post)
        try:
            assert entered.wait(5)
            with sqlite3.connect(db) as conn:
                assert conn.execute("SELECT COUNT(*) FROM session_outbox_inflight").fetchone()[0] == 1
            assert outbox.pending(destination="http://server", user_id="user", db_path=db) == []
            assert outbox.hold_if_current(item, "identity unverified", db_path=db)
            with pytest.raises(outbox.OutboxConflictError, match="in-flight delivery claim"):
                outbox.quarantine_held_session("http://server", "user", "codex", "held", db_path=db)
            assert not db.with_suffix(".quarantined.jsonl").exists()
        finally:
            resume.set()
        assert not future.result(timeout=5)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM session_outbox_inflight").fetchone()[0] == 0
    assert outbox.quarantine_held_session("http://server", "user", "codex", "held", db_path=db) == 1


def test_crashed_claim_blocks_quarantine_and_second_sender(tmp_path):
    db = tmp_path / "queue.db"
    _queue(db)
    item = outbox.pending(destination="http://server", user_id="user", db_path=db)[0]
    token = outbox.claim_for_delivery(item, db_path=db)
    assert token
    assert outbox.claim_for_delivery(item, db_path=db) is None
    assert outbox.hold_if_current(item, "identity unverified", db_path=db)
    with pytest.raises(outbox.OutboxConflictError, match="in-flight delivery claim"):
        outbox.quarantine_held_session("http://server", "user", "codex", "held", db_path=db)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT claim_token FROM session_outbox_inflight").fetchone()[0] == token
    assert outbox.stats(db_path=db)["inflight_sessions"] == 1


def test_claimed_oldest_does_not_block_other_sessions_and_drain_reports_incomplete(tmp_path, monkeypatch):
    db = tmp_path / "queue.db"
    _queue(db, session="claimed")
    _queue(db, session="free")
    claimed = outbox.pending(destination="http://server", user_id="user", db_path=db)[0]
    token = outbox.claim_for_delivery(claimed, db_path=db)
    assert token
    from observal_cli.harness import ensure_loaded, get_adapter

    ensure_loaded()
    monkeypatch.setattr(get_adapter("codex"), "validate_outbox_provenance", lambda _item: True)
    posted = []

    def post(payload, _cfg):
        posted.append(payload["session_id"])
        return {"acknowledged_line": 0, "acknowledged_offset": 7}

    cfg = {"server_url": "http://server", "user_id": "user"}
    assert [row.session_id for row in outbox.pending(destination="http://server", user_id="user", db_path=db)] == [
        "free"
    ]
    assert not drain_outbox(cfg, home=tmp_path, db_path=db, post=post)
    assert posted == ["free"]
    assert outbox.stats(db_path=db)["pending"] == 1
    outbox.release_delivery_claim(claimed, token, db_path=db)
    assert drain_outbox(cfg, home=tmp_path, db_path=db, post=post)
    assert posted == ["free", "claimed"]


def test_claim_race_reselects_and_delivers_unclaimed_session(tmp_path, monkeypatch):
    db = tmp_path / "queue.db"
    _queue(db, session="raced")
    _queue(db, session="free")
    from observal_cli.harness import ensure_loaded, get_adapter

    ensure_loaded()
    monkeypatch.setattr(get_adapter("codex"), "validate_outbox_provenance", lambda _item: True)
    original_claim = outbox.claim_for_delivery
    raced = []

    def claim(item, *, db_path=None):
        if item.session_id == "raced" and not raced:
            raced.append((item, original_claim(item, db_path=db_path)))
            return None
        return original_claim(item, db_path=db_path)

    monkeypatch.setattr(outbox, "claim_for_delivery", claim)
    posted = []

    def post(payload, _cfg):
        posted.append(payload["session_id"])
        return {"acknowledged_line": 0, "acknowledged_offset": 7}

    assert not drain_outbox({"server_url": "http://server", "user_id": "user"}, home=tmp_path, db_path=db, post=post)
    assert posted == ["free"]
    assert raced[0][1]
    outbox.release_delivery_claim(*raced[0], db_path=db)


def test_paused_post_refuses_both_review_paths_without_changing_attribution(tmp_path, monkeypatch):
    db = tmp_path / "queue.db"
    row_id, original = _queue(db)
    item = _hold(db)
    from observal_cli.harness import ensure_loaded, get_adapter

    ensure_loaded()
    adapter = get_adapter("codex")
    monkeypatch.setattr(adapter, "validate_outbox_provenance", lambda _item: True)
    monkeypatch.setattr(adapter, "outbox_provenance", lambda _source, _payload: {"verified": True})
    entered, resume = threading.Event(), threading.Event()

    def post(_payload, _cfg):
        entered.set()
        assert resume.wait(5)
        return None

    # A sender may already be posting when the operator places a hold.
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM session_outbox_holds")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(drain_outbox, {"server_url": "http://server", "user_id": "user"}, db_path=db, post=post)
        try:
            assert entered.wait(5)
            assert outbox.hold_if_current(item, "identity unverified", db_path=db)
            corrected = dict(original, agent_id=None, agent_version=None)
            with pytest.raises(outbox.OutboxConflictError, match="in-flight delivery claim"):
                outbox.review_hold(
                    "http://server", "user", "codex", "held", {row_id: (corrected, {"verified": True})}, db_path=db
                )
            source = SessionSource("codex", "held", tmp_path / "held.jsonl")
            with pytest.raises(outbox.OutboxConflictError, match="in-flight delivery claim"):
                outbox.review_unattributed("http://server", "user", "codex", "held", source, db_path=db)
            with sqlite3.connect(db) as conn:
                payload, revision = conn.execute(
                    "SELECT payload, revision FROM session_outbox WHERE id = ?", (row_id,)
                ).fetchone()
                assert json.loads(payload) == original
                assert revision == item.revision
                assert conn.execute("SELECT COUNT(*) FROM session_outbox_holds").fetchone()[0] == 1
        finally:
            resume.set()
        assert not future.result(timeout=5)


def _crash_during_archive(db, after_fsync):
    if after_fsync:
        original = outbox._archive_rows

        def crash_after_archive(path, rows):
            original(path, rows)
            os._exit(43)

        outbox._archive_rows = crash_after_archive
    else:
        original_write = os.write

        def crash_during_write(fd, data):
            original_write(fd, data[:11])
            os._exit(42)

        outbox.os.write = crash_during_write
    outbox.quarantine_held_session("http://server", "user", "codex", "second", db_path=db)


def test_crash_during_archive_append_refuses_retry_and_preserves_hold(tmp_path, monkeypatch):
    db = tmp_path / "queue.db"
    _queue(db, session="first")
    _hold(db, session="first")
    outbox.quarantine_held_session("http://server", "user", "codex", "first", db_path=db)
    archive = db.with_suffix(".quarantined.jsonl")
    prefix = archive.read_bytes()
    _queue(db, session="second")
    _hold(db, session="second")
    _spawn_test_imports(monkeypatch)
    child = multiprocessing.get_context("spawn").Process(target=_crash_during_archive, args=(db, False))
    child.start()
    child.join(10)
    assert child.exitcode == 42
    corrupt = archive.read_bytes()
    assert corrupt.startswith(prefix)
    assert len(corrupt) == len(prefix) + 11
    with pytest.raises(outbox.OutboxError, match="manual repair"):
        outbox.quarantine_held_session("http://server", "user", "codex", "second", db_path=db)
    assert archive.read_bytes() == corrupt
    assert outbox.is_held("http://server", "user", "codex", "second", db_path=db)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM session_outbox WHERE session_id = 'second'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM session_outbox_terminal").fetchone()[0] == 1


def test_crash_after_archive_fsync_can_retry_with_readable_duplicates(tmp_path, monkeypatch):
    db = tmp_path / "queue.db"
    _queue(db, session="second")
    _hold(db, session="second")
    _spawn_test_imports(monkeypatch)
    child = multiprocessing.get_context("spawn").Process(target=_crash_during_archive, args=(db, True))
    child.start()
    child.join(10)
    assert child.exitcode == 43
    assert outbox.is_held("http://server", "user", "codex", "second", db_path=db)
    assert outbox.quarantine_held_session("http://server", "user", "codex", "second", db_path=db) == 1
    archive = db.with_suffix(".quarantined.jsonl")
    assert archive.read_bytes().endswith(b"\n")
    assert [row["session_id"] for row in map(json.loads, archive.read_text().splitlines())] == ["second", "second"]


@pytest.mark.parametrize("existing", [b"{}\nnot-json\n", b"{}\n{}", b"{}\n\n", b"{}\n\xff\n"])
def test_malformed_existing_archive_refuses_append(tmp_path, existing):
    db = tmp_path / "queue.db"
    _queue(db)
    _hold(db)
    archive = db.with_suffix(".quarantined.jsonl")
    archive.write_bytes(existing)
    with pytest.raises(outbox.OutboxError, match="manual repair"):
        outbox.quarantine_held_session("http://server", "user", "codex", "held", db_path=db)
    assert archive.read_bytes() == existing
    assert outbox.is_held("http://server", "user", "codex", "held", db_path=db)
    assert len(outbox.pending(destination="http://server", user_id="user", db_path=db)) == 0
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM session_outbox").fetchone()[0] == 1


def test_cli_reports_manual_archive_repair_without_deleting_hold(tmp_path, monkeypatch):
    db = _setup(tmp_path, monkeypatch)
    _queue(db)
    _hold(db)
    archive = db.with_suffix(".quarantined.jsonl")
    archive.write_bytes(b'{"broken":')
    result = CliRunner().invoke(
        app, ["reconcile", "--harness", "codex", "--quarantine-held", "held", "--yes", "--output", "json"]
    )
    assert result.exit_code != 0
    assert "manual repair" in result.output
    assert archive.read_bytes() == b'{"broken":'
    assert outbox.is_held("http://server", "user", "codex", "held", db_path=db)


def test_partial_archive_write_is_truncated_before_retry(tmp_path, monkeypatch):
    db = tmp_path / "queue.db"
    _queue(db, session="first")
    _hold(db, session="first")
    assert outbox.quarantine_held_session("http://server", "user", "codex", "first", db_path=db) == 1
    archive = db.with_suffix(".quarantined.jsonl")
    before = archive.read_bytes()
    _queue(db, session="second")
    _hold(db, session="second")
    real_write = os.write
    calls = 0

    def disk_full(fd, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(fd, data[:11])
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as patch:
        patch.setattr(outbox.os, "write", disk_full)
        with pytest.raises(outbox.OutboxError, match="cannot durably append"):
            outbox.quarantine_held_session("http://server", "user", "codex", "second", db_path=db)
    assert archive.read_bytes() == before
    assert outbox.is_held("http://server", "user", "codex", "second", db_path=db)
    assert outbox.quarantine_held_session("http://server", "user", "codex", "second", db_path=db) == 1
    assert [row["session_id"] for row in map(json.loads, archive.read_text().splitlines())] == ["first", "second"]


def test_dry_run_never_opens_outbox_or_creates_database(tmp_path, monkeypatch):
    from observal_cli.harness import get_adapter

    db = tmp_path / "absent" / "queue.db"
    monkeypatch.setattr(outbox, "DB_PATH", db)
    monkeypatch.setattr(cmd_reconcile_cli, "load_config", lambda: {"server_url": "http://server", "user_id": "user"})
    monkeypatch.setattr(outbox, "_connect", lambda *args, **kwargs: pytest.fail("dry-run opened outbox"))
    adapter = get_adapter("codex")
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("{}\n")
    monkeypatch.setattr(
        adapter, "discover_session_sources", lambda **kwargs: [SessionSource("codex", "session", transcript)]
    )
    monkeypatch.setattr(adapter, "related_session_sources", lambda source: [])
    result = CliRunner().invoke(app, ["reconcile", "--harness", "codex", "--dry-run", "--output", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["summary"]["would_push"] == 1
    assert not db.exists()


def test_quarantine_scopes_to_current_user_and_validates_session(tmp_path):
    db = tmp_path / "queue.db"
    _queue(db)
    _hold(db)
    _queue(db, user="other")
    _hold(db, user="other")
    for args in (
        ("http://server", "user", "codex", ""),
        ("http://server", "user", "kiro", "held"),
        ("http://server", "user", "codex", "unknown"),
    ):
        with pytest.raises(outbox.OutboxConflictError):
            outbox.quarantine_held_session(*args, db_path=db)
    assert outbox.quarantine_held_session("http://server", "user", "codex", "held", db_path=db) == 1
    assert outbox.is_held("http://server", "other", "codex", "held", db_path=db)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT user_id FROM session_outbox_terminal").fetchall() == [("user",)]
        assert conn.execute("SELECT user_id FROM session_outbox").fetchall() == [("other",)]


def test_cli_requires_confirmation_and_rejects_conflicting_modes(tmp_path, monkeypatch):
    db = _setup(tmp_path, monkeypatch)
    _queue(db)
    _hold(db)
    runner = CliRunner()
    base = ["reconcile", "--harness", "codex", "--quarantine-held", "held"]
    for extra in (["--review-unattributed", "held"], ["--list-held"], ["--dry-run"], ["--output", "json"]):
        result = runner.invoke(app, base + extra)
        assert result.exit_code != 0, result.output
    assert runner.invoke(app, base, input="n\n").exit_code == 0
    assert outbox.is_held("http://server", "user", "codex", "held", db_path=db)
    assert runner.invoke(app, [*base, "--yes"]).exit_code == 0
    assert not outbox.is_held("http://server", "user", "codex", "held", db_path=db)
    assert runner.invoke(app, [*base, "--yes"]).exit_code != 0
    assert runner.invoke(app, ["reconcile", "--harness", "codex", "--quarantine-held", "", "--yes"]).exit_code != 0
    assert runner.invoke(app, ["reconcile", "--yes"]).exit_code != 0


def test_legacy_terminal_migration_keeps_replay_barrier_and_original(tmp_path):
    db = tmp_path / "queue.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE session_outbox_terminal (destination TEXT, user_id TEXT, harness TEXT, "
            "session_id TEXT, status TEXT, reason TEXT, quarantined_at TEXT, payload TEXT, provenance TEXT)"
        )
        conn.execute(
            "INSERT INTO session_outbox_terminal VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("http://server", "user", "codex", "held", "quarantined", "unverified", "now", "secret", "proof"),
        )
    assert outbox.is_terminal("http://server", "user", "codex", "held", db_path=db)
    assert outbox.stats(db_path=db)["archived_rows"] == 1
    assert json.loads(db.with_suffix(".quarantined.jsonl").read_text().splitlines()[0])["payload"] == "secret"
    with pytest.raises(outbox.OutboxConflictError):
        _queue(db)


def test_legacy_migration_refuses_malformed_archive_and_keeps_rows(tmp_path):
    db = tmp_path / "queue.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE session_outbox_terminal (destination TEXT, user_id TEXT, harness TEXT, "
            "session_id TEXT, status TEXT, reason TEXT, quarantined_at TEXT, payload TEXT, provenance TEXT)"
        )
        conn.execute(
            "INSERT INTO session_outbox_terminal VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("http://server", "user", "codex", "held", "quarantined", "unverified", "now", "secret", "proof"),
        )
    archive = db.with_suffix(".quarantined.jsonl")
    archive.write_bytes(b'{"old":true}\n{"partial":')
    before = archive.read_bytes()
    with pytest.raises(outbox.OutboxError, match="manual repair"):
        outbox.is_terminal("http://server", "user", "codex", "held", db_path=db)
    assert archive.read_bytes() == before
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT payload FROM session_outbox_terminal").fetchall() == [("secret",)]


def test_restored_rollout_never_requeues_or_claims_delivery(tmp_path):
    db = tmp_path / "queue.db"
    _queue(db)
    _hold(db)
    outbox.quarantine_held_session("http://server", "user", "codex", "held", db_path=db)
    restored = tmp_path / "held.jsonl"
    restored.write_text("restored record\n")
    source = SessionSource("codex", "held", restored)
    cfg = {"server_url": "http://server", "user_id": "user"}
    posted = []
    assert not drain_session_source(
        source,
        cfg,
        hook_event="Reconcile",
        recover_from_server=True,
        final=True,
        home=tmp_path,
        db_path=db,
        checkpoint_fetch=lambda *_: {"acknowledged_line": -1},
        post=lambda body, _: posted.append(body),
    )
    assert not posted
    assert not (tmp_path / ".observal" / "sync_state.json").exists()
    assert not outbox.session_delivered("http://server", "user", "codex", "held", db_path=db)
    with pytest.raises(outbox.OutboxConflictError, match="terminally quarantined"):
        _queue(db)
    assert outbox.stats(db_path=db)["terminal_sessions"] == 1


def test_many_archived_rows_do_not_exhaust_small_outbox(tmp_path):
    db = tmp_path / "queue.db"
    for index in range(16):
        _queue(db, session=f"held-{index}", line="x" * 5000)
        _hold(db, session=f"held-{index}")
        outbox.quarantine_held_session("http://server", "user", "codex", f"held-{index}", db_path=db)
    assert db.with_suffix(".quarantined.jsonl").stat().st_size > 16 * 5000
    assert outbox.stats(db_path=db)["archived_rows"] == 16
    outbox.enqueue(
        {"harness": "codex", "session_id": "fresh", "start_offset": 0, "lines": ["ok"]},
        destination="http://server",
        user_id="user",
        db_path=db,
        max_bytes=65536,
    )


@pytest.mark.parametrize("primitive", ["O_NOFOLLOW", "O_DIRECTORY", "fchmod"])
def test_missing_archive_primitive_keeps_held_rows(tmp_path, monkeypatch, primitive):
    db = tmp_path / "queue.db"
    _queue(db)
    _hold(db)
    archive = db.with_suffix(".quarantined.jsonl")
    archive.write_text('{"existing":true}\n')
    with monkeypatch.context() as patch:
        patch.delattr(outbox.os, primitive, raising=False)
        with pytest.raises(outbox.OutboxError, match=primitive):
            outbox.quarantine_held_session("http://server", "user", "codex", "held", db_path=db)
    assert archive.read_text() == '{"existing":true}\n'
    assert outbox.is_held("http://server", "user", "codex", "held", db_path=db)
    assert outbox.stats(db_path=db)["pending"] == 1


def test_windows_archive_fails_closed_before_writing(tmp_path, monkeypatch):
    db = tmp_path / "queue.db"
    _queue(db)
    _hold(db)
    archive = db.with_suffix(".quarantined.jsonl")
    monkeypatch.setattr(outbox, "_archive_os_name", lambda: "nt")
    with pytest.raises(outbox.OutboxError, match=r"unsupported on Windows.*supported POSIX host"):
        outbox.quarantine_held_session("http://server", "user", "codex", "held", db_path=db)
    assert not archive.exists()
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM session_outbox").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM session_outbox_holds").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM session_outbox_terminal").fetchone()[0] == 0


def test_windows_cli_reports_unsupported_quarantine(tmp_path, monkeypatch):
    db = _setup(tmp_path, monkeypatch)
    _queue(db)
    _hold(db)
    monkeypatch.setattr(outbox, "_archive_os_name", lambda: "nt")
    result = CliRunner().invoke(app, ["reconcile", "--harness", "codex", "--quarantine-held", "held", "--yes"])
    assert result.exit_code != 0
    assert "unsupported on Windows" in result.output
    assert "supported POSIX host" in result.output
    assert outbox.is_held("http://server", "user", "codex", "held", db_path=db)


def test_windows_legacy_migration_keeps_original_rows(tmp_path, monkeypatch):
    db = tmp_path / "queue.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE session_outbox_terminal (destination TEXT, user_id TEXT, harness TEXT, "
            "session_id TEXT, status TEXT, reason TEXT, quarantined_at TEXT, payload TEXT, provenance TEXT)"
        )
        conn.execute(
            "INSERT INTO session_outbox_terminal VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("http://server", "user", "codex", "held", "quarantined", "unverified", "now", "secret", "proof"),
        )
    monkeypatch.setattr(outbox, "_archive_os_name", lambda: "nt")
    with pytest.raises(outbox.OutboxError, match="unsupported on Windows"):
        outbox.is_terminal("http://server", "user", "codex", "held", db_path=db)
    assert not db.with_suffix(".quarantined.jsonl").exists()
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT payload FROM session_outbox_terminal").fetchall() == [("secret",)]


@pytest.mark.parametrize("unsafe", ["symlink", "hardlink"])
def test_unsafe_archive_path_keeps_held_rows(tmp_path, unsafe):
    db = tmp_path / "queue.db"
    _queue(db)
    _hold(db)
    target = tmp_path / "target"
    target.write_text("unchanged")
    archive = db.with_suffix(".quarantined.jsonl")
    if unsafe == "symlink":
        archive.symlink_to(target)
    else:
        os.link(target, archive)
    with pytest.raises(outbox.OutboxError):
        outbox.quarantine_held_session("http://server", "user", "codex", "held", db_path=db)
    assert target.read_text() == "unchanged"
    assert outbox.is_held("http://server", "user", "codex", "held", db_path=db)
    assert outbox.stats(db_path=db)["pending"] == 1


def test_archive_unlinked_and_relinked_during_fsync_keeps_held_rows(tmp_path, monkeypatch):
    db = tmp_path / "queue.db"
    _queue(db)
    _hold(db)
    archive = db.with_suffix(".quarantined.jsonl")
    archive.write_text('{"existing":true}\n')
    original_inode = archive.stat().st_ino
    real_fsync = os.fsync
    replaced = False

    def replace_after_fsync(fd):
        nonlocal replaced
        real_fsync(fd)
        if not replaced and os.fstat(fd).st_ino == original_inode:
            replaced = True
            archive.unlink()
            archive.write_text("replacement\n")

    with monkeypatch.context() as patch:
        patch.setattr(outbox.os, "fsync", replace_after_fsync)
        with pytest.raises(outbox.OutboxError, match="archive path"):
            outbox.quarantine_held_session("http://server", "user", "codex", "held", db_path=db)
    assert replaced
    assert archive.read_text() == "replacement\n"
    assert archive.stat().st_ino != original_inode
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM session_outbox_terminal").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM session_outbox").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM session_outbox_holds").fetchone()[0] == 1


def test_archive_replaced_after_append_before_delete_keeps_held_rows(tmp_path, monkeypatch):
    db = tmp_path / "queue.db"
    _queue(db)
    _hold(db)
    archive = db.with_suffix(".quarantined.jsonl")
    real_archive_rows = outbox._archive_rows

    def replace_after_append(path, rows):
        identity = real_archive_rows(path, rows)
        path.unlink()
        path.write_text("replacement\n")
        return identity

    monkeypatch.setattr(outbox, "_archive_rows", replace_after_append)
    with pytest.raises(outbox.OutboxError, match="archive path"):
        outbox.quarantine_held_session("http://server", "user", "codex", "held", db_path=db)
    assert archive.read_text() == "replacement\n"
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM session_outbox_terminal").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM session_outbox").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM session_outbox_holds").fetchone()[0] == 1


def test_archive_replaced_before_commit_rolls_back_deletes(tmp_path, monkeypatch):
    db = tmp_path / "queue.db"
    _queue(db)
    _hold(db)
    archive = db.with_suffix(".quarantined.jsonl")
    real_check = outbox._assert_archive_identity
    checks = 0

    def replace_before_commit(path, identity):
        nonlocal checks
        checks += 1
        if checks == 3:
            path.unlink()
            path.write_text("replacement\n")
        real_check(path, identity)

    monkeypatch.setattr(outbox, "_assert_archive_identity", replace_before_commit)
    with pytest.raises(outbox.OutboxError, match="archive path"):
        outbox.quarantine_held_session("http://server", "user", "codex", "held", db_path=db)
    assert checks == 3
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM session_outbox_terminal").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM session_outbox").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM session_outbox_holds").fetchone()[0] == 1


def test_archive_append_failure_and_concurrent_enqueue_preserve_hold(tmp_path, monkeypatch):
    db = tmp_path / "queue.db"
    _queue(db)
    _hold(db)
    original = outbox._archive_rows

    def fail(*args):
        raise outbox.OutboxError("injected append failure")

    monkeypatch.setattr(outbox, "_archive_rows", fail)
    with pytest.raises(outbox.OutboxError):
        outbox.quarantine_held_session("http://server", "user", "codex", "held", db_path=db)
    monkeypatch.setattr(outbox, "_archive_rows", original)
    assert outbox.is_held("http://server", "user", "codex", "held", db_path=db)
    barrier = threading.Barrier(2)

    def enqueue_again():
        barrier.wait()
        with pytest.raises(outbox.OutboxConflictError):
            _queue(db)

    def archive():
        barrier.wait()
        return outbox.quarantine_held_session("http://server", "user", "codex", "held", db_path=db)

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(enqueue_again), pool.submit(archive)
        a.result()
        assert b.result() == 1


def test_wrong_delegated_identity_cannot_be_released_by_missing_rollout(tmp_path, monkeypatch):
    db = _setup(tmp_path, monkeypatch)
    _queue(db, line="delegated child transcript")
    _hold(db)
    from observal_cli.harness import ensure_loaded, get_adapter

    ensure_loaded()
    adapter = get_adapter("codex")
    # A different delegated session must not serve as proof for the held ID.
    other = tmp_path / "other.jsonl"
    other.write_text("delegated child transcript\n")
    monkeypatch.setattr(adapter, "resolve_session_source", lambda _event: adapter._source(other))
    result = CliRunner().invoke(app, ["reconcile", "--harness", "codex", "--review-unattributed", "held"])
    assert result.exit_code != 0
    assert outbox.is_held("http://server", "user", "codex", "held", db_path=db)
    assert outbox.quarantine_held_session("http://server", "user", "codex", "held", db_path=db) == 1
    assert (
        json.loads(json.loads(db.with_suffix(".quarantined.jsonl").read_text().splitlines()[0])["payload"])["agent_id"]
        == "delegated-agent"
    )
