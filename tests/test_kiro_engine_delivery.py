# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Engine signals and real filesystem/SQLite coordination for dual Kiro hooks."""

from __future__ import annotations

import json
import os
import subprocess
import sys

from observal_cli import telemetry_buffer
from observal_cli.harness import ensure_loaded, get_adapter
from observal_cli.harness_specs.kiro_hooks_spec import build_kiro_hooks_file
from observal_cli.hooks import session_push
from observal_cli.sessions import base


def setup_source(home):
    """Create a legacy transcript and companion with cumulative credits in an isolated home."""
    root = home / ".kiro/sessions/cli"
    root.mkdir(parents=True)
    source = root / "session.jsonl"
    source.write_text('{"kind":"Prompt","data":{"content":[{"kind":"text","data":"hello"}]}}\n')
    source.with_suffix(".json").write_text(
        json.dumps(
            {
                "cwd": str(home),
                "session_state": {
                    "conversation_metadata": {
                        "user_turn_metadatas": [{"metering_usage": [{"unit": "credit", "value": 1.0}]}]
                    }
                },
            }
        )
    )
    cfg = home / ".observal/config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"server_url": "http://server", "user_id": "user", "access_token": "token"}))
    return source


def ack(payload):
    """Acknowledge the entire synthetic batch using its actual source byte offsets."""
    return {
        "acknowledged_line": payload["start_offset"] + len(payload["lines"]) - 1,
        "acknowledged_offset": payload.get("end_byte_offsets", [payload.get("total_offset", 0)])[-1],
    }


def test_duplicate_hook_skips_metadata_post_and_worker_but_new_turn_runs(tmp_path, monkeypatch):
    """An acknowledged duplicate is a no-op, including metadata and detached workers."""
    source = setup_source(tmp_path)
    posts = []
    workers = []
    ensure_loaded()
    adapter = get_adapter("kiro")
    monkeypatch.setattr(adapter, "should_capture_session", lambda *a, **k: True)
    monkeypatch.setattr(telemetry_buffer, "DB_PATH", tmp_path / "outbox.db")
    monkeypatch.setattr(base, "_maybe_upload_layer_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(
        base, "post_to_server_ack", lambda _url, _token, payload, **kw: posts.append(payload) or ack(payload)
    )
    monkeypatch.setattr(session_push, "_spawn_worker", lambda *a, **k: workers.append(a))
    event = {"session_id": "session", "hook_event_name": "UserPromptSubmit"}
    session_push._run_hook(event, harness="kiro", home=tmp_path)
    session_push._run_hook(event, harness="kiro", home=tmp_path)
    assert len(posts) == len(workers) == 1
    with source.open("a") as f:
        f.write('{"kind":"AssistantMessage","data":{"content":[]}}\n')
    session_push._run_hook(event, harness="kiro", home=tmp_path)
    assert len(posts) == len(workers) == 2
    base.write_cursor("session", 0, 0, home=tmp_path, preserve_finalized=False)
    session_push._run_hook(event, harness="kiro", home=tmp_path)
    assert len(workers) == 3


def test_failed_hook_can_retry_same_payload(tmp_path, monkeypatch):
    """A failed send leaves the outbox intact and cannot create a successful hook receipt."""
    setup_source(tmp_path)
    ensure_loaded()
    adapter = get_adapter("kiro")
    monkeypatch.setattr(adapter, "should_capture_session", lambda *a, **k: True)
    monkeypatch.setattr(telemetry_buffer, "DB_PATH", tmp_path / "outbox.db")
    monkeypatch.setattr(base, "_maybe_upload_layer_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(session_push, "_spawn_worker", lambda *a, **k: None)
    monkeypatch.setattr(base, "post_to_server_ack", lambda *a, **k: None)
    event = {"session_id": "session", "hook_event_name": "Stop"}
    session_push._run_hook(event, harness="kiro", home=tmp_path)
    assert telemetry_buffer.pending(destination="http://server", user_id="user")
    posted = []
    monkeypatch.setattr(
        base, "post_to_server_ack", lambda _url, _token, payload, **kw: posted.append(payload) or ack(payload)
    )
    session_push._run_hook(event, harness="kiro", home=tmp_path)
    assert not telemetry_buffer.pending(destination="http://server", user_id="user")
    assert sum(len(p["lines"]) for p in posted) == 1


def test_concurrent_outbox_drainers_send_pending_item_once(tmp_path):
    """Independent interpreters cannot both claim the same pending HTTP delivery."""
    db = tmp_path / "outbox.db"
    log = tmp_path / "posts.log"
    telemetry_buffer.enqueue(
        {"harness": "kiro", "session_id": "s", "start_offset": 0, "lines": ["{}"], "end_byte_offsets": [3]},
        destination="http://server",
        user_id="user",
        db_path=db,
    )
    script = """import time,sys
from pathlib import Path
from observal_cli.sessions import base
while not Path(sys.argv[4]).exists(): time.sleep(.01)
def post(payload,config):
    with open(sys.argv[3], 'a') as f: f.write('post\\n')
    time.sleep(.15)
    return {'acknowledged_line':0,'acknowledged_offset':3}
base.drain_outbox({'server_url':'http://server','user_id':'user'},db_path=Path(sys.argv[1]),home=Path(sys.argv[2]),post=post)
"""
    ready = tmp_path / "ready"
    processes = [
        subprocess.Popen([sys.executable, "-c", script, str(db), str(tmp_path), str(log), str(ready)], env=os.environ)
        for _ in range(2)
    ]
    try:
        ready.touch()
        for process in processes:
            assert process.wait(timeout=60) == 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
    assert log.read_text().splitlines() == ["post"]


def test_runtime_placement_uses_transcript_not_saved_engine(tmp_path, monkeypatch):
    """V3 prefers standalone, V2 overrides keep inline, and inline-only V3 never goes silent."""
    from observal_cli.harness.protocol import SessionSource

    ensure_loaded()
    adapter = get_adapter("kiro")
    project = tmp_path / "project"
    project.mkdir()
    v3 = project / "messages.jsonl"
    v3.write_text("{}\n")
    legacy = project / "session.jsonl"
    legacy.write_text("{}\n")
    source = SessionSource("kiro", "session", v3, str(project))
    monkeypatch.setenv("OBSERVAL_KIRO_ENGINE", "v2")
    assert adapter.accepts_session_hook(source, {"hook_event_name": "Stop"}, "inline", tmp_path)
    hooks = project / ".kiro/hooks"
    hooks.mkdir(parents=True)
    (hooks / "observal.json").write_text(json.dumps(build_kiro_hooks_file()))
    assert not adapter.accepts_session_hook(source, {"hook_event_name": "Stop"}, "inline", tmp_path)
    assert adapter.accepts_session_hook(source, {}, "standalone", tmp_path)
    assert adapter.accepts_session_hook(source, {}, "", tmp_path)
    monkeypatch.setenv("OBSERVAL_KIRO_ENGINE", "v3")
    assert adapter.accepts_session_hook(SessionSource("kiro", "v2", legacy), {}, "inline", tmp_path)


def test_receipts_allow_related_updates_and_isolate_destination(tmp_path):
    """Related writes and destination changes invalidate receipts without exposing source content."""
    from observal_cli.harness.protocol import SessionSource
    from observal_cli.sessions.delivery_guard import kiro_delivery_guard

    parent = tmp_path / "messages.jsonl"
    child = tmp_path / "child.jsonl"
    parent.write_text("{}\n")
    child.write_text("{}\n")
    source = SessionSource("kiro", "parent", parent)
    related = [SessionSource("kiro", "child", child, parent_session_id="parent")]
    event = {"hook_event_name": "Stop", "prompt": "private synthetic text"}
    config = {"server_url": "http://one", "user_id": "user"}
    with kiro_delivery_guard(source, event, config, related=related, home=tmp_path) as attempt:
        assert attempt.proceed
        attempt.delivered = True
    with kiro_delivery_guard(source, event, config, related=related, home=tmp_path) as attempt:
        assert not attempt.proceed
    child.write_text("{}\n{}\n")
    with kiro_delivery_guard(source, event, config, related=related, home=tmp_path) as attempt:
        assert attempt.proceed
        attempt.delivered = True
    with kiro_delivery_guard(
        source, event, {**config, "server_url": "http://two"}, related=related, home=tmp_path
    ) as attempt:
        assert attempt.proceed
    assert all(
        "private synthetic text" not in path.read_text()
        for path in (tmp_path / ".observal/session-delivery").rglob("*.json")
    )


def test_crashed_capture_does_not_acknowledge_receipt(tmp_path):
    """An interrupted capture releases its OS lock and leaves the next attempt eligible."""
    from observal_cli.harness.protocol import SessionSource
    from observal_cli.sessions.delivery_guard import kiro_delivery_guard

    path = tmp_path / "messages.jsonl"
    path.write_text("{}\n")
    source = SessionSource("kiro", "session", path)
    config = {"server_url": "http://one", "user_id": "user"}
    try:
        with kiro_delivery_guard(source, {}, config, home=tmp_path) as attempt:
            attempt.delivered = True
            raise RuntimeError("synthetic crash before receipt commit")
    except RuntimeError:
        pass
    with kiro_delivery_guard(source, {}, config, home=tmp_path) as attempt:
        assert attempt.proceed
