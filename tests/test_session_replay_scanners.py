# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Persisted final-audit holds survive a crash before the cursor rewind."""

import os
import time
from pathlib import Path

import pytest

from observal_cli import cmd_reconcile_cli, telemetry_buffer
from observal_cli.harness import SessionSource
from observal_cli.hooks import session_push
from observal_cli.sessions import base


@pytest.mark.parametrize("scanner", ["automatic", "reconcile"])
def test_scanner_replays_held_final_with_finalized_cursor(tmp_path: Path, monkeypatch, scanner: str):
    config = {"server_url": "http://server", "user_id": "user", "access_token": "token"}
    db = tmp_path / "outbox.db"
    monkeypatch.setattr(telemetry_buffer, "DB_PATH", db)
    path = tmp_path / "session.jsonl"
    path.write_text('{"n":0}\n')
    source = SessionSource("claude-code", "session", path)
    assert base.drain_session_source(source, config, hook_event="Stop", final=True, spool_only=True, home=tmp_path)
    item = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)[0]
    # Simulate interruption after the SQLite hold commit but before cursor rewind.
    base.write_cursor(source.checkpoint_key, path.stat().st_size, 1, finalized=True, home=tmp_path)
    telemetry_buffer.set_replay_hold(item, db_path=db)
    # A hold must override the usual age filter, not just the cursor filter.
    sent = []

    def post(payload, _config):
        sent.append(payload)
        return {"acknowledged_line": 0, "acknowledged_offset": path.stat().st_size, "integrity_ok": True}

    monkeypatch.setattr(base, "post_to_server_ack", lambda _url, _token, payload, **_kw: post(payload, config))
    monkeypatch.setattr(
        base, "get_server_checkpoint", lambda *_args: pytest.fail("held replay must not fetch checkpoint")
    )

    class Adapter:
        def discover_session_sources(self, **_kwargs):
            return [source]

        def related_session_sources(self, _source, **_kwargs):
            return []

        def should_capture_session(self, _source, **_kwargs):
            return True

        def aged_recovery_final(self):
            return True

        def session_extra_fields(self, *_args, **_kwargs):
            return {}

        def session_extra_records(self, *_args, **_kwargs):
            return ()

    if scanner == "automatic":
        monkeypatch.setattr(session_push, "ensure_loaded", lambda: None)
        monkeypatch.setattr(session_push, "get_adapter", lambda _name: Adapter())
        monkeypatch.setattr(session_push, "load_config", lambda **_kw: config)
        session_push._recover_sessions("claude-code", home=tmp_path)
    else:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(cmd_reconcile_cli, "get_adapter", lambda _name: Adapter())
        result = cmd_reconcile_cli._reconcile_harness("claude-code", config, 24, False)
        assert result["pushed"] + result["finalized"] == 1

    assert len(sent) == 1
    assert sent[0]["lines"] == ['{"n":0}']
    assert sent[0]["final"] is True
    assert base.read_cursor_state(source.checkpoint_key, home=tmp_path) == (path.stat().st_size, 1, True)
    assert not telemetry_buffer.replay_held(
        destination="http://server", user_id="user", harness="claude-code", session_id="session", db_path=db
    )
    assert telemetry_buffer.stats(db_path=db)["pending"] == 0


@pytest.mark.parametrize("parent_finalized", [False, True])
@pytest.mark.parametrize("hold_user", ["user", "other-user"])
def test_kiro_recovery_replays_held_child_when_parent_is_unchanged(
    tmp_path: Path, monkeypatch, parent_finalized: bool, hold_user: str
):
    config = {"server_url": "http://server", "user_id": "user", "access_token": "token"}
    db = tmp_path / "outbox.db"
    monkeypatch.setattr(telemetry_buffer, "DB_PATH", db)
    parent_path = tmp_path / "parent.jsonl"
    child_path = tmp_path / "sub-executions" / "child.jsonl"
    child_path.parent.mkdir()
    parent_path.write_text('{"parent":0}\n')
    child_path.write_text('{"child":0}\n')
    parent = SessionSource("kiro", "parent", parent_path)
    child = SessionSource("kiro", "child", child_path, cursor_key="parent__sub__child", parent_session_id="parent")
    base.write_cursor(parent.checkpoint_key, parent_path.stat().st_size, 1, finalized=parent_finalized, home=tmp_path)
    if parent_finalized:
        old = time.time() - 300
        os.utime(parent_path, (old, old))
    child_config = {**config, "user_id": hold_user}
    assert base.drain_session_source(child, child_config, hook_event="Stop", final=True, spool_only=True, home=tmp_path)
    item = telemetry_buffer.pending(destination="http://server", user_id=hold_user, db_path=db)[0]
    base.write_cursor(child.checkpoint_key, child_path.stat().st_size, 1, finalized=True, home=tmp_path)
    telemetry_buffer.set_replay_hold(item, db_path=db)
    sent = []

    def post(_url, _token, payload, **_kwargs):
        sent.append(payload)
        return {"acknowledged_line": 0, "acknowledged_offset": child_path.stat().st_size, "integrity_ok": True}

    monkeypatch.setattr(base, "post_to_server_ack", post)
    monkeypatch.setattr(base, "get_server_checkpoint", lambda *_args: pytest.fail("held child fetched checkpoint"))

    class Adapter:
        def discover_session_sources(self, **_kwargs):
            return [parent]

        def related_session_sources(self, _source, **_kwargs):
            return [child]

        def should_capture_session(self, _source, **_kwargs):
            return True

        def aged_recovery_final(self):
            return False

        def session_extra_fields(self, *_args, **_kwargs):
            return {}

        def session_extra_records(self, *_args, **_kwargs):
            return ()

    monkeypatch.setattr(session_push, "ensure_loaded", lambda: None)
    monkeypatch.setattr(session_push, "get_adapter", lambda _name: Adapter())
    monkeypatch.setattr(session_push, "load_config", lambda **_kw: config)
    session_push._recover_sessions("kiro", home=tmp_path)
    assert len(sent) == (1 if hold_user == "user" else 0)
    if sent:
        assert sent[0]["session_id"] == "child"
        assert sent[0]["final"] is True
    assert telemetry_buffer.replay_held(
        destination="http://server", user_id=hold_user, harness="kiro", session_id="child", db_path=db
    ) is (hold_user != "user")


def test_kiro_recovery_finalizes_held_child_when_parent_needs_recovery(tmp_path: Path, monkeypatch):
    config = {"server_url": "http://server", "user_id": "user", "access_token": "token"}
    db = tmp_path / "outbox.db"
    monkeypatch.setattr(telemetry_buffer, "DB_PATH", db)
    path = tmp_path / "parent.jsonl"
    path.write_text('{"parent":0}\n')
    old = time.time() - 300
    os.utime(path, (old, old))
    parent = SessionSource("kiro", "parent", path)
    child = SessionSource("kiro", "child", path, cursor_key="parent__sub__child", parent_session_id="parent")
    telemetry_buffer.enqueue(
        {"harness": "kiro", "session_id": "child", "start_offset": 0, "lines": ['{"parent":0}']},
        destination="http://server",
        user_id="user",
        db_path=db,
    )
    item = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)[0]
    telemetry_buffer.set_replay_hold(item, db_path=db)

    class Adapter:
        def discover_session_sources(self, **_kwargs):
            return [parent]

        def related_session_sources(self, _source, **_kwargs):
            return [child]

        def should_capture_session(self, _source, **_kwargs):
            return True

        def aged_recovery_final(self):
            return False

        def session_extra_fields(self, *_args, **_kwargs):
            return {}

        def session_extra_records(self, *_args, **_kwargs):
            return ()

    calls = []
    monkeypatch.setattr(session_push, "ensure_loaded", lambda: None)
    monkeypatch.setattr(session_push, "get_adapter", lambda _name: Adapter())
    monkeypatch.setattr(session_push, "load_config", lambda **_kw: config)
    monkeypatch.setattr(session_push, "drain_outbox", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        session_push, "drain_session_source", lambda source, _config, **kwargs: calls.append((source, kwargs))
    )
    session_push._recover_sessions("kiro", home=tmp_path)
    assert [(source.session_id, kwargs["final"]) for source, kwargs in calls] == [("parent", False), ("child", True)]
