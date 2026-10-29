# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""A smaller finalized source must not make an old offset silently valid again."""

from pathlib import Path

import pytest

from observal_cli import cmd_reconcile_cli, telemetry_buffer
from observal_cli.harness import SessionSource
from observal_cli.hooks import session_push
from observal_cli.sessions import base

CONFIG = {"server_url": "http://server", "user_id": "user", "access_token": "token"}


@pytest.mark.parametrize("invalidate", ["shrink", "revision"])
def test_in_flight_409_does_not_quarantine_invalidated_final(tmp_path: Path, invalidate: str):
    db = tmp_path / "outbox.db"
    source = SessionSource("claude-code", "session", tmp_path / "session.jsonl")
    source.path.write_text('{"type":"system"}\n')
    base.write_cursor(source.checkpoint_key, source.path.stat().st_size, 1, home=tmp_path)
    telemetry_buffer.enqueue(
        {
            "harness": source.harness,
            "session_id": source.session_id,
            "lines": [],
            "start_offset": 1,
            "final": True,
            "total_line_count": 1,
            "total_offset": source.path.stat().st_size,
            "session_hash": base.hash_session_source(source.path)[0],
            "hashed_line_count": 1,
        },
        destination="http://server",
        user_id="user",
        checkpoint_key=source.checkpoint_key,
        db_path=db,
    )
    original = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)[0]
    rejections = []

    def post(_payload, _config):
        if invalidate == "shrink":
            source.path.write_text("")
            base.mark_cursor_source_shrunk(source.checkpoint_key, home=tmp_path)
        else:
            base.write_cursor(source.checkpoint_key, 0, 0, home=tmp_path, preserve_finalized=False)
        raise base.PermanentIngestRejectionError(409)

    assert not base.drain_outbox(CONFIG, home=tmp_path, db_path=db, post=post, rejections=rejections)
    assert not rejections
    pending = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)
    assert len(pending) == 1 and pending[0].id == original.id and pending[0].final
    assert not telemetry_buffer.has_rejections(destination="http://server", user_id="user", db_path=db)
    assert base.cursor_source_shrunk(source.checkpoint_key, home=tmp_path) == (invalidate == "shrink")


@pytest.mark.parametrize("recover", [False, True])
@pytest.mark.parametrize("pending", [False, True])
def test_shrink_then_append_refuses_stale_finalized_cursor(tmp_path: Path, recover: bool, pending: bool):
    path = tmp_path / "session.jsonl"
    first = '{"type":"system"}\n'
    assert len(first.encode()) == 18
    path.write_text(first + " " * 16)
    source = SessionSource("claude-code", "session", path)
    db = tmp_path / "outbox.db"
    base.write_cursor(source.checkpoint_key, 34, 1, finalized=True, home=tmp_path)
    if pending:
        telemetry_buffer.enqueue(
            {
                "harness": source.harness,
                "session_id": source.session_id,
                "lines": [],
                "start_offset": 1,
                "final": True,
                "total_line_count": 1,
                "total_offset": 34,
                "session_hash": base.hash_session_source(path)[0],
                "hashed_line_count": 1,
            },
            destination="http://server",
            user_id="user",
            checkpoint_key=source.checkpoint_key,
            db_path=db,
        )
    path.write_text(first)
    posted = []

    def post(payload, _config):
        posted.append(payload)
        return {"acknowledged_line": 0, "acknowledged_offset": 34, "integrity_ok": True}

    assert not base.drain_session_source(
        source,
        CONFIG,
        hook_event="Stop",
        final=True,
        home=tmp_path,
        db_path=db,
        post=post,
        recover_from_server=recover,
        checkpoint_fetch=lambda *_: {"acknowledged_line": 0, "acknowledged_offset": 34},
    )
    assert not posted
    with path.open("a") as file:
        file.write('{"type":"user"}\n')
    assert path.stat().st_size == 34
    assert not base.drain_session_source(
        source, CONFIG, hook_event="Reconcile", final=True, home=tmp_path, db_path=db, post=post
    )
    assert not posted
    assert base.cursor_source_shrunk(source.checkpoint_key, home=tmp_path)
    if pending:
        telemetry_buffer.enqueue(
            {
                "harness": "claude-code",
                "session_id": "healthy",
                "lines": ['{"n":0}'],
                "start_offset": 0,
                "end_byte_offsets": [8],
            },
            destination="http://server",
            user_id="user",
            db_path=db,
        )
        healthy = []

        def deliver(payload, _config):
            healthy.append(payload["session_id"])
            return {"acknowledged_line": 0, "acknowledged_offset": 8}

        assert not base.drain_outbox(CONFIG, home=tmp_path, db_path=db, post=deliver)
        assert healthy == ["healthy"]
        assert telemetry_buffer.stats(db_path=db)["pending"] == 1


def test_server_only_checkpoint_shrink_remains_refused_after_append(tmp_path: Path):
    path = tmp_path / "session.jsonl"
    path.write_text('{"type":"system"}\n')
    source = SessionSource("claude-code", "session", path)
    checkpoint = {"acknowledged_line": 0, "acknowledged_offset": 34}
    assert not base.drain_session_source(
        source,
        CONFIG,
        hook_event="Stop",
        final=True,
        recover_from_server=True,
        checkpoint_fetch=lambda *_: checkpoint,
        spool_only=True,
        home=tmp_path,
        db_path=tmp_path / "outbox.db",
    )
    with path.open("a") as file:
        file.write('{"type":"user"}\n')
    assert path.stat().st_size == 34
    assert base.cursor_source_shrunk(source.checkpoint_key, home=tmp_path)
    assert not base.drain_session_source(
        source,
        CONFIG,
        hook_event="Reconcile",
        final=True,
        recover_from_server=True,
        checkpoint_fetch=lambda *_: checkpoint,
        spool_only=True,
        home=tmp_path,
        db_path=tmp_path / "outbox.db",
    )
    assert telemetry_buffer.stats(db_path=tmp_path / "outbox.db")["pending"] == 0


@pytest.mark.parametrize("scanner", ["automatic", "reconcile"])
def test_shrunken_finalized_source_not_skipped_by_scanners(tmp_path: Path, monkeypatch, scanner: str):
    db = tmp_path / "outbox.db"
    monkeypatch.setattr(telemetry_buffer, "DB_PATH", db)
    path = tmp_path / "session.jsonl"
    path.write_text('{"type":"system"}\n' + " " * 16)
    source = SessionSource("claude-code", "session", path)
    base.write_cursor(source.checkpoint_key, 34, 1, finalized=True, home=tmp_path)
    path.write_text('{"type":"system"}\n')
    assert not base.drain_session_source(
        source, CONFIG, hook_event="Stop", final=True, spool_only=True, home=tmp_path, db_path=db
    )
    with path.open("a") as file:
        file.write('{"type":"user"}\n')
    assert path.stat().st_size == 34

    class Adapter:
        def discover_session_sources(self, **_kwargs):
            return [source]

        def related_session_sources(self, *_args, **_kwargs):
            return []

        def should_capture_session(self, *_args, **_kwargs):
            return True

        def aged_recovery_final(self):
            return True

        def session_extra_fields(self, *_args, **_kwargs):
            return {}

        def session_extra_records(self, *_args, **_kwargs):
            return ()

    attempts = []
    original = base.drain_session_source

    def guarded(*args, **kwargs):
        attempts.append(args[0].session_id)
        return original(*args, **kwargs)

    monkeypatch.setattr(session_push, "drain_session_source", guarded)
    monkeypatch.setattr(cmd_reconcile_cli, "drain_session_source", guarded)
    monkeypatch.setattr(base, "get_server_checkpoint", lambda *_: {"acknowledged_line": 0, "acknowledged_offset": 34})
    if scanner == "automatic":
        monkeypatch.setattr(session_push, "ensure_loaded", lambda: None)
        monkeypatch.setattr(session_push, "get_adapter", lambda _: Adapter())
        monkeypatch.setattr(session_push, "load_config", lambda **_: CONFIG)
        session_push._recover_sessions("claude-code", home=tmp_path)
    else:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(cmd_reconcile_cli, "get_adapter", lambda _: Adapter())
        result = cmd_reconcile_cli._reconcile_harness("claude-code", CONFIG, 24, False)
        assert result["up_to_date"] == 0
        assert result["errors"] + result["queued"] == 1
    assert attempts == ["session"] if scanner == "automatic" else attempts == []
    assert base.cursor_source_shrunk(source.checkpoint_key, home=tmp_path)
