# SPDX-FileCopyrightText: 2026 RAWx18 <rawx18.dev@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Goose session delivery through the shared acknowledged-delivery pipeline."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

from observal_cli import telemetry_buffer
from observal_cli.hooks import session_push
from observal_cli.sessions import base
from observal_cli.sessions import goose as goose_sessions

if TYPE_CHECKING:
    from pathlib import Path

_DDL = (
    """
    CREATE TABLE sessions (
        id TEXT PRIMARY KEY, name TEXT DEFAULT '', description TEXT DEFAULT '',
        session_type TEXT DEFAULT 'user', working_dir TEXT NOT NULL,
        created_at TIMESTAMP, updated_at TIMESTAMP,
        accumulated_input_tokens INTEGER, accumulated_output_tokens INTEGER,
        accumulated_total_tokens INTEGER, accumulated_cost REAL,
        provider_name TEXT, model_config_json TEXT, goose_mode TEXT DEFAULT 'auto',
        schedule_id TEXT, parent_session_id TEXT
    )
    """,
    """
    CREATE TABLE messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT, session_id TEXT NOT NULL,
        role TEXT NOT NULL, content_json TEXT NOT NULL, created_timestamp INTEGER NOT NULL,
        metadata_json TEXT
    )
    """,
)


def _seed_goose(home: Path, session_id: str = "20260807_1") -> None:
    db = home / ".local" / "share" / "goose" / "sessions" / "sessions.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as connection:
        for statement in _DDL:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO sessions (id, name, working_dir, created_at, updated_at) VALUES (?,?,?,?,?)",
            (session_id, "Fix parser", "/project", "2026-08-07 11:59:00", "2026-08-07 12:00:00"),
        )
        connection.execute(
            "INSERT INTO messages (message_id, session_id, role, content_json, created_timestamp, metadata_json) "
            "VALUES (?,?,?,?,?,?)",
            ("msg-1", session_id, "user", json.dumps([{"type": "text", "text": "hi"}]), 1_785_000_000, "{}"),
        )


def _configure(home: Path, monkeypatch, tmp_path: Path):
    config_dir = home / ".observal"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text(
        json.dumps({"server_url": "http://server", "access_token": "token", "user_id": "user"})
    )
    db = tmp_path / "outbox.db"
    monkeypatch.setattr(telemetry_buffer, "DB_PATH", db)
    monkeypatch.setattr(base, "_resolve_agent", lambda *_a, **_k: (None, None))
    monkeypatch.setattr(base, "_get_cached_layer_hash", lambda *_a, **_k: None)
    return db


def test_hook_spools_goose_records_into_the_shared_outbox(tmp_path: Path, monkeypatch):
    _seed_goose(tmp_path)
    db = _configure(tmp_path, monkeypatch, tmp_path)
    monkeypatch.setattr(base, "post_to_server_ack", lambda *_a, **_k: None)
    monkeypatch.setattr(session_push, "_spawn_worker", lambda *_a, **_k: None)

    session_push._run_hook(
        {"event": "UserPromptSubmit", "session_id": "20260807_1", "working_dir": "/project"},
        harness="goose",
        home=tmp_path,
    )

    item = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)[0]
    assert item.harness == "goose"
    assert item.payload["hook_event"] == "UserPromptSubmit"
    types = [json.loads(line)["type"] for line in item.payload["lines"]]
    assert types == ["session", "message"]


def test_hook_never_posts_inline_and_hands_delivery_to_a_worker(tmp_path: Path, monkeypatch):
    """goose awaits hooks inside its agent loop, so the hook must only spool."""
    _seed_goose(tmp_path)
    db = _configure(tmp_path, monkeypatch, tmp_path)
    posts: list = []
    workers: list[tuple] = []
    monkeypatch.setattr(base, "post_to_server_ack", lambda *a, **k: posts.append(a) or None)
    monkeypatch.setattr(session_push, "_spawn_worker", lambda *args, harness: workers.append((args, harness)))

    session_push._run_hook(
        {"event": "Stop", "session_id": "20260807_1", "working_dir": "/project"},
        harness="goose",
        home=tmp_path,
    )

    assert posts == []
    assert ("--drain-outbox",) in [args for args, _harness in workers]
    assert telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)


def test_hook_recovers_the_working_dir_goose_omits(tmp_path: Path, monkeypatch):
    """SessionStart, UserPromptSubmit and SessionEnd carry no working_dir."""
    _seed_goose(tmp_path)
    _configure(tmp_path, monkeypatch, tmp_path)
    monkeypatch.setattr(session_push, "_spawn_worker", lambda *_a, **_k: None)

    from observal_cli.harness.goose import GooseAdapter

    source = GooseAdapter().resolve_session_source(
        {"event": "SessionEnd", "session_id": "20260807_1"},
        home=tmp_path,
    )

    assert source is not None
    assert source.cwd == "/project"


def test_session_end_finalises_and_advances_the_cursor(tmp_path: Path, monkeypatch):
    _seed_goose(tmp_path)
    db = _configure(tmp_path, monkeypatch, tmp_path)
    posted: list[dict] = []

    def _ack(payload, _config):
        posted.append(payload)
        return {"acknowledged_line": len(payload["lines"]) - 1, "acknowledged_offset": payload["total_offset"]}

    mirrored = goose_sessions.export_session("20260807_1", home=tmp_path, finalize=True)
    assert base.drain_session_source(
        _source(mirrored.path),
        {"server_url": "http://server", "access_token": "token", "user_id": "user"},
        hook_event="SessionEnd",
        final=True,
        home=tmp_path,
        db_path=db,
        post=_ack,
        checkpoint_fetch=lambda *_a, **_k: None,
    )

    assert posted[0]["final"] is True
    assert posted[0]["session_hash"]
    offset, line_count, finalized = base.read_cursor_state("20260807_1", home=tmp_path, destination="http://server")
    assert finalized is True
    assert line_count == 3  # session + message + session_end
    assert offset == mirrored.path.stat().st_size


def test_second_drain_sends_only_new_goose_rows(tmp_path: Path, monkeypatch):
    _seed_goose(tmp_path)
    db = _configure(tmp_path, monkeypatch, tmp_path)
    config = {"server_url": "http://server", "access_token": "token", "user_id": "user"}
    posted: list[dict] = []

    def _ack(payload, _config):
        posted.append(payload)
        return {"acknowledged_line": payload["start_offset"] + len(payload["lines"]) - 1, "acknowledged_offset": 0}

    first = goose_sessions.export_session("20260807_1", home=tmp_path)
    base.drain_session_source(_source(first.path), config, hook_event="Stop", home=tmp_path, db_path=db, post=_ack)

    with sqlite3.connect(tmp_path / ".local" / "share" / "goose" / "sessions" / "sessions.db") as connection:
        connection.execute(
            "INSERT INTO messages (message_id, session_id, role, content_json, created_timestamp, metadata_json) "
            "VALUES (?,?,?,?,?,?)",
            ("msg-2", "20260807_1", "assistant", json.dumps([{"type": "text", "text": "yo"}]), 1_785_000_001, "{}"),
        )
    second = goose_sessions.export_session("20260807_1", home=tmp_path)
    base.drain_session_source(_source(second.path), config, hook_event="Stop", home=tmp_path, db_path=db, post=_ack)

    assert [len(payload["lines"]) for payload in posted] == [2, 1]
    assert json.loads(posted[1]["lines"][0])["message_id"] == "msg-2"


def test_reconcile_discovers_goose_sessions_without_touching_the_database(tmp_path: Path, monkeypatch):
    from observal_cli.harness.goose import GooseAdapter

    _seed_goose(tmp_path)
    db_file = tmp_path / ".local" / "share" / "goose" / "sessions" / "sessions.db"
    before = db_file.read_bytes()

    sources = GooseAdapter().discover_session_sources(home=tmp_path, since_hours=24 * 365 * 100)

    assert [source.session_id for source in sources] == ["20260807_1"]
    assert sources[0].path.is_file()
    assert db_file.read_bytes() == before


def _source(path: Path):
    from observal_cli.harness import SessionSource

    return SessionSource("goose", "20260807_1", path, cwd="/project")
