# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""A final audit cannot be retired after its local source becomes unsafe."""

import json
from pathlib import Path

import pytest

from observal_cli import telemetry_buffer
from observal_cli.harness import SessionSource
from observal_cli.sessions import base

CONFIG = {"server_url": "http://server", "user_id": "user", "access_token": "token"}


def _pending_final(tmp_path: Path) -> tuple[SessionSource, Path]:
    source = SessionSource("claude-code", "session", tmp_path / "session.jsonl")
    source.path.write_text('{"type":"system"}\n')
    db = tmp_path / "outbox.db"
    assert base.drain_session_source(
        source, CONFIG, hook_event="Stop", final=True, spool_only=True, home=tmp_path, db_path=db
    )
    return source, db


def _ack(payload: dict, _config: dict) -> dict:
    return {
        "acknowledged_line": payload.get("total_line_count", payload["start_offset"] + len(payload["lines"])) - 1,
        "integrity_ok": True,
    }


def _assert_final_pending(source: SessionSource, db: Path):
    pending = [
        item
        for item in telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)
        if item.checkpoint_key == source.checkpoint_key
    ]
    assert len(pending) == 1 and pending[0].final


def test_relocated_source_fails_before_post_or_spool_and_keeps_old_final(tmp_path: Path):
    source, db = _pending_final(tmp_path)
    old_path = source.path
    old_path.write_text('{"type":"system","padding":"' + "x" * 4096 + '"}\n')
    new_path = tmp_path / "new.jsonl"
    new_path.write_text('{"type":"system","replacement":true}\n')
    relocated = SessionSource("claude-code", source.session_id, new_path)
    posted = []

    def post(payload, config):
        posted.append(payload["session_id"])
        if payload["session_id"] == source.session_id:
            new_path.unlink()
        return _ack(payload, config)

    telemetry_buffer.enqueue(
        {"harness": "claude-code", "session_id": "healthy", "lines": ['{"n":0}'], "start_offset": 0},
        destination="http://server",
        user_id="user",
        db_path=db,
    )
    assert not base.drain_session_source(
        relocated, CONFIG, hook_event="Stop", final=True, home=tmp_path, db_path=db, post=post
    )
    assert base.cursor_source_shrunk(source.checkpoint_key, home=tmp_path)
    assert old_path.is_file() and new_path.is_file()
    assert posted == []
    assert json.loads((tmp_path / ".observal" / "sync_state.json").read_text())[source.checkpoint_key][
        "source_path"
    ] == str(old_path.resolve())
    _assert_final_pending(source, db)

    assert not base.drain_outbox(CONFIG, home=tmp_path, db_path=db, post=post)
    assert posted == ["healthy"]
    _assert_final_pending(source, db)


def test_relocation_during_post_invalidates_inflight_final_ack(tmp_path: Path):
    source, db = _pending_final(tmp_path)
    new_path = tmp_path / "new.jsonl"
    new_path.write_text('{"type":"new"}\n')
    relocated = SessionSource("claude-code", source.session_id, new_path)

    def post(payload, _config):
        assert not base.drain_session_source(
            relocated, CONFIG, hook_event="Stop", final=True, spool_only=True, home=tmp_path, db_path=db
        )
        return _ack(payload, _config)

    assert not base.drain_outbox(CONFIG, home=tmp_path, db_path=db, post=post)
    assert base.cursor_source_shrunk(source.checkpoint_key, home=tmp_path)
    _assert_final_pending(source, db)


def test_relocation_during_predrain_blocks_further_spooling(tmp_path: Path):
    source, db = _pending_final(tmp_path)
    source.path.write_text(source.path.read_text() + '{"type":"later"}\n')
    new_path = tmp_path / "new.jsonl"
    new_path.write_text('{"type":"replacement"}\n')
    relocated = SessionSource("claude-code", source.session_id, new_path)

    def post(payload, config):
        assert not base.drain_session_source(
            relocated, CONFIG, hook_event="Stop", final=True, spool_only=True, home=tmp_path, db_path=db
        )
        return _ack(payload, config)

    assert not base.drain_session_source(
        source, CONFIG, hook_event="Stop", final=True, home=tmp_path, db_path=db, post=post
    )
    _assert_final_pending(source, db)
    assert base.cursor_source_shrunk(source.checkpoint_key, home=tmp_path)


def test_marker_set_between_initial_check_and_snapshot_blocks_ack(tmp_path: Path, monkeypatch):
    source, db = _pending_final(tmp_path)
    original = base.cursor_source_shrunk
    called = False

    def marker_between_check_and_snapshot(key, home=None):
        nonlocal called
        if not called:
            called = True
            base.mark_cursor_source_shrunk(key, home=home)
            return False
        return original(key, home=home)

    monkeypatch.setattr(base, "cursor_source_shrunk", marker_between_check_and_snapshot)
    assert not base.drain_outbox(CONFIG, home=tmp_path, db_path=db, post=_ack)
    _assert_final_pending(source, db)


def test_truncation_during_post_without_scanner_retains_final_and_delivers_other_sessions(tmp_path: Path):
    source, db = _pending_final(tmp_path)
    posted = []

    def post(payload, config):
        posted.append(payload["session_id"])
        if payload["session_id"] == source.session_id:
            source.path.write_text("")
        return _ack(payload, config)

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
    assert not base.drain_outbox(CONFIG, home=tmp_path, db_path=db, post=post)
    assert posted == [source.session_id, "healthy"]
    assert base.cursor_source_shrunk(source.checkpoint_key, home=tmp_path)
    _assert_final_pending(source, db)
    source.path.write_text('{"type":"system"}\n')
    assert not base.drain_outbox(CONFIG, home=tmp_path, db_path=db, post=post)
    assert posted == [source.session_id, "healthy"]
    _assert_final_pending(source, db)


@pytest.mark.parametrize("bad_path", [None, "relative.jsonl", 42, "missing", "directory"])
def test_known_invalid_or_missing_path_fails_closed(tmp_path: Path, bad_path):
    source, db = _pending_final(tmp_path)
    state_file = tmp_path / ".observal" / "sync_state.json"
    state = json.loads(state_file.read_text())
    state[source.checkpoint_key]["source_path"] = (
        str(tmp_path / "missing.jsonl")
        if bad_path == "missing"
        else str(tmp_path)
        if bad_path == "directory"
        else bad_path
    )
    state_file.write_text(json.dumps(state))
    base.write_cursor(source.checkpoint_key, 0, 0, home=tmp_path)
    assert not base.drain_outbox(CONFIG, home=tmp_path, db_path=db, post=_ack)
    assert base.cursor_source_shrunk(source.checkpoint_key, home=tmp_path)
    _assert_final_pending(source, db)


def test_legacy_final_without_path_still_checks_cursor_marker(tmp_path: Path):
    db = tmp_path / "outbox.db"
    base.write_cursor("legacy", 0, 0, home=tmp_path)
    telemetry_buffer.enqueue(
        {
            "harness": "claude-code",
            "session_id": "legacy",
            "lines": [],
            "start_offset": 0,
            "final": True,
            "total_offset": 0,
        },
        destination="http://server",
        user_id="user",
        db_path=db,
    )
    assert base.drain_outbox(CONFIG, home=tmp_path, db_path=db, post=_ack)
    assert not base.cursor_source_shrunk("legacy", home=tmp_path)


@pytest.mark.parametrize("metadata_only", [False, True])
def test_predrain_truncated_bound_final_blocks_post_with_zero_cursor_and_allows_neighbor(
    tmp_path: Path, metadata_only: bool
):
    source, db = _pending_final(tmp_path)
    if metadata_only:
        # Replace the queued record-bearing final with a metadata-only audit.
        item = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)[0]
        assert telemetry_buffer.accept_item(item, db_path=db)
        base.write_cursor(source.checkpoint_key, source.path.stat().st_size, 1, home=tmp_path)
        assert base.drain_session_source(
            source, CONFIG, hook_event="Stop", final=True, spool_only=True, home=tmp_path, db_path=db
        )
    base.write_cursor(source.checkpoint_key, 0, 0, home=tmp_path)
    source.path.write_text("")
    telemetry_buffer.enqueue(
        {"harness": "claude-code", "session_id": "healthy", "lines": ['{"n":0}'], "start_offset": 0},
        destination="http://server",
        user_id="user",
        db_path=db,
    )
    posted = []

    def post(payload, config):
        posted.append(payload["session_id"])
        return _ack(payload, config)

    assert not base.drain_outbox(CONFIG, home=tmp_path, db_path=db, post=post)
    assert posted == ["healthy"]
    assert base.cursor_source_shrunk(source.checkpoint_key, home=tmp_path)
    _assert_final_pending(source, db)
    assert base.read_cursor(source.checkpoint_key, home=tmp_path) == (0, 0)
    source.path.write_text('{"type":"system"}\n')
    assert not base.drain_outbox(CONFIG, home=tmp_path, db_path=db, post=post)
    assert posted == ["healthy"]


def test_local_source_path_survives_cursor_writes_and_is_not_sent(tmp_path: Path):
    source, db = _pending_final(tmp_path)
    state_file = tmp_path / ".observal" / "sync_state.json"
    base.write_cursor(source.checkpoint_key, 0, 0, home=tmp_path)
    assert json.loads(state_file.read_text())[source.checkpoint_key]["source_path"] == str(source.path.resolve())
    pending = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)
    assert "source_path" not in pending[0].payload
    assert str(source.path.resolve()) not in json.dumps(pending[0].payload)
