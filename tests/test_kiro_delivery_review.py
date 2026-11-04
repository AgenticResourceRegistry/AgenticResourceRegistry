# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Regression evidence for the #2021 auditor's delivery-health findings."""

import json
from pathlib import Path

import pytest

from observal_cli import telemetry_buffer
from observal_cli.harness import ensure_loaded, get_adapter
from observal_cli.hooks import session_push
from observal_cli.sessions import base


@pytest.fixture
def captured_stop(tmp_path, monkeypatch):
    """Successfully acknowledge capture while keeping worker execution under test control."""
    path = tmp_path / ".kiro/sessions/cli/session.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"kind":"Prompt","data":{"content":[]}}\n')
    config = tmp_path / ".observal/config.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"server_url": "http://server", "user_id": "user", "access_token": "token"}))
    ensure_loaded()
    adapter = get_adapter("kiro")
    monkeypatch.setattr(adapter, "should_capture_session", lambda *a, **k: True)
    monkeypatch.setattr(telemetry_buffer, "DB_PATH", tmp_path / "outbox.db")
    monkeypatch.setattr(base, "_maybe_upload_layer_snapshot", lambda *a, **k: None)
    posts = []

    def acknowledge(_url, _token, payload, **kwargs):
        """Acknowledge synthetic batches at their actual source byte boundaries."""
        posts.append(payload)
        return {
            "acknowledged_line": payload["start_offset"] + len(payload["lines"]) - 1,
            "acknowledged_offset": payload.get("end_byte_offsets", [path.stat().st_size])[-1],
        }

    monkeypatch.setattr(base, "post_to_server_ack", acknowledge)
    return {"session_id": "session", "hook_event_name": "Stop"}, path, posts


def test_successful_capture_retries_failed_final_worker(captured_stop, tmp_path, monkeypatch):
    """A capture receipt cannot prevent an unchanged Stop replay from retrying its finalizer."""
    event, path, posts = captured_stop
    workers = []
    monkeypatch.setattr(session_push, "_spawn_worker", lambda *a, **k: workers.append(a) or False)
    session_push._run_hook(event, harness="kiro", home=tmp_path)
    session_push._run_hook(event, harness="kiro", home=tmp_path)
    assert len(posts) == 1
    assert len(workers) == 2
    assert base.read_cursor_state("session", home=tmp_path) == (path.stat().st_size, 1, False)


def test_successful_capture_retries_child_that_exited(captured_stop, tmp_path, monkeypatch):
    """Popen success is not finalization completion, even when launch succeeded twice."""
    event, _path, posts = captured_stop
    workers = []
    monkeypatch.setattr(session_push, "_spawn_worker", lambda *a, **k: workers.append(a) or True)
    session_push._run_hook(event, harness="kiro", home=tmp_path)
    session_push._run_hook(event, harness="kiro", home=tmp_path)
    assert len(posts) == 1
    assert len(workers) == 2


def test_failed_worker_intent_survives_and_aged_recovery_finalizes(captured_stop, tmp_path, monkeypatch):
    """An explicit Stop persists retry intent, unlike a merely quiet Kiro conversation."""
    import os
    import time

    event, path, posts = captured_stop
    monkeypatch.setattr(session_push, "_spawn_worker", lambda *a, **k: False)
    monkeypatch.setattr(
        base,
        "recover_cursor_from_server",
        lambda source, cfg, **k: base.read_cursor(source.checkpoint_key, home=tmp_path),
    )
    adapter = get_adapter("kiro")
    source = adapter.resolve_session_source(event, home=tmp_path)
    session_push._run_hook(event, harness="kiro", home=tmp_path)
    assert adapter.session_finalization_pending(source, base.load_config(tmp_path), home=tmp_path)
    assert not adapter.aged_recovery_final()
    os.utime(path, (time.time() - 300, time.time() - 300))
    session_push._recover_sessions("kiro", home=tmp_path)
    assert posts[-1]["final"] is True
    assert base.read_cursor_state("session", home=tmp_path)[2]
    assert not adapter.session_finalization_pending(source, base.load_config(tmp_path), home=tmp_path)


def test_completed_finalizer_replay_neither_recaptures_nor_reposts(captured_stop, tmp_path, monkeypatch):
    """Final cursors and receipts suppress duplicate final work, not retry scheduling."""
    event, path, posts = captured_stop
    monkeypatch.setattr(session_push, "_spawn_worker", lambda *a, **k: True)
    monkeypatch.setattr(session_push, "_wait_until_stable", lambda *a: None)
    monkeypatch.setattr(
        base,
        "recover_cursor_from_server",
        lambda source, cfg, **k: base.read_cursor(source.checkpoint_key, home=tmp_path),
    )
    session_push._run_hook(event, harness="kiro", home=tmp_path)
    session_push._finalize_session("kiro", "session", str(tmp_path), home=tmp_path)
    assert base.read_cursor_state("session", home=tmp_path)[2]
    assert len(posts) == 2
    session_push._run_hook(event, harness="kiro", home=tmp_path)
    session_push._finalize_session("kiro", "session", str(tmp_path), home=tmp_path)
    assert len(posts) == 2


def test_killed_process_releases_lock_without_success_receipt(tmp_path):
    """Kill a real interpreter inside capture; the OS lock releases and replay remains eligible."""
    import os
    import subprocess
    import sys

    from observal_cli.harness.protocol import SessionSource
    from observal_cli.sessions.delivery_guard import kiro_delivery_guard

    path = tmp_path / "messages.jsonl"
    path.write_text("{}\n")
    script = """import sys,time
from pathlib import Path
from observal_cli.sessions.delivery_guard import kiro_delivery_guard
from observal_cli.harness.protocol import SessionSource
home=Path(sys.argv[1]);source=SessionSource('kiro','session',home/'messages.jsonl')
with kiro_delivery_guard(source,{}, {'server_url':'http://server','user_id':'user'},home=home) as attempt:
    attempt.delivered=True
    print('locked',flush=True)
    time.sleep(120)
"""
    root = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "PYTHONPATH": str(root) + os.pathsep + str(root / "packages/observal-shared"),
    }
    process = subprocess.Popen(
        [sys.executable, "-P", "-c", script, str(tmp_path)],
        env=env,
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout.readline().strip() == "locked"
        process.kill()
        process.wait(timeout=10)
        with kiro_delivery_guard(
            SessionSource("kiro", "session", path),
            {},
            {"server_url": "http://server", "user_id": "user"},
            home=tmp_path,
        ) as attempt:
            assert attempt.proceed
        assert not list((tmp_path / ".observal/session-delivery").rglob("*.json"))
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()


def test_pending_metadata_finalization_is_not_hidden_by_old_final_cursor(captured_stop, tmp_path, monkeypatch):
    """A past final cursor cannot hide a new Stop intent carrying delayed companion credits."""
    import os
    import time

    event, path, posts = captured_stop
    monkeypatch.setattr(session_push, "_spawn_worker", lambda *a, **k: False)
    monkeypatch.setattr(session_push, "_wait_until_stable", lambda *a: None)
    monkeypatch.setattr(
        base,
        "recover_cursor_from_server",
        lambda source, cfg, **k: base.read_cursor(source.checkpoint_key, home=tmp_path),
    )
    session_push._run_hook(event, harness="kiro", home=tmp_path)
    session_push._finalize_session("kiro", "session", str(tmp_path), home=tmp_path)
    assert base.read_cursor_state("session", home=tmp_path)[2]
    path.with_suffix(".json").write_text(
        json.dumps(
            {
                "session_state": {
                    "conversation_metadata": {
                        "user_turn_metadatas": [{"metering_usage": [{"unit": "credit", "value": 3.0}]}]
                    }
                }
            }
        )
    )
    session_push._run_hook(event, harness="kiro", home=tmp_path)
    # Capture metadata acknowledges successfully, but the failed child leaves explicit final intent.
    assert base.read_cursor_state("session", home=tmp_path)[2]
    os.utime(path, (time.time() - 300, time.time() - 300))
    session_push._recover_sessions("kiro", home=tmp_path)
    assert posts[-1]["final"] and posts[-1]["total_credits"] == 3.0
    assert not list((tmp_path / ".observal/session-delivery").rglob("finalization.pending"))
