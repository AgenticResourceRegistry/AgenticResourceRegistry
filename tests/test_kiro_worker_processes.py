# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Real process coordination and ambiguous HTTP acknowledgements against a local peer."""

import json
import os
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from observal_cli import telemetry_buffer
from observal_cli.hooks import session_push
from observal_cli.sessions import base


@pytest.fixture
def peer():
    """A controlled ingest peer stores source identities before optionally losing the ACK."""
    state = {"posts": [], "stored": set(), "lose_ack": False}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            """Never log request headers or credentials."""

        def do_GET(self):
            """No server checkpoint is available, so workers recover their local cursor."""
            self.send_error(404)

        def do_POST(self):
            """Store idempotent source identities, then acknowledge or sever the connection."""
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path != "/api/v1/ingest/session":
                self.send_error(404)
                return
            with lock:
                state["posts"].append(payload)
                for i, _line in enumerate(payload["lines"]):
                    state["stored"].add((payload["session_id"], payload["start_offset"] + i))
                lose_ack = state["lose_ack"]
            if lose_ack:
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            result = {
                "acknowledged_line": payload["start_offset"] + len(payload["lines"]) - 1,
                "acknowledged_offset": payload.get("total_offset", 0),
            }
            if payload.get("end_byte_offsets"):
                result["acknowledged_offset"] = payload["end_byte_offsets"][-1]
            raw = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def setup_home(home, url):
    """Create an isolated legacy source and dummy credentials for the local peer."""
    source = home / ".kiro/sessions/cli/session.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text('{"kind":"Prompt","data":{"content":[]}}\n')
    source.with_suffix(".json").write_text(json.dumps({"cwd": str(home)}))
    config = home / ".observal/config.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"server_url": url, "user_id": "user", "access_token": "dummy"}))
    return source


def test_accepted_request_with_lost_ack_retries_without_receipt(peer, tmp_path, monkeypatch):
    """HTTP acceptance without an ACK is at-least-once retry, never a successful capture receipt."""
    url, state = peer
    setup_home(tmp_path, url)
    monkeypatch.setattr(telemetry_buffer, "DB_PATH", tmp_path / "outbox.db")
    monkeypatch.setattr(session_push, "_spawn_worker", lambda *a, **k: False)
    state["lose_ack"] = True
    event = {"session_id": "session", "hook_event_name": "UserPromptSubmit"}
    session_push._run_hook(event, harness="kiro", home=tmp_path)
    assert state["stored"] == {("session", 0)}
    assert telemetry_buffer.pending(destination=url, user_id="user")
    assert not list((tmp_path / ".observal/session-delivery").rglob("*.json"))
    state["lose_ack"] = False
    session_push._run_hook(event, harness="kiro", home=tmp_path)
    assert len(state["posts"]) == 2
    assert len(state["stored"]) == 1
    assert not telemetry_buffer.pending(destination=url, user_id="user")
    assert list((tmp_path / ".observal/session-delivery").rglob("*.json"))


def test_concurrent_capture_and_final_workers_complete_once(peer, tmp_path, monkeypatch):
    """Independent capture/final interpreters complete the final cursor without duplicate final POSTs."""
    url, state = peer
    source = setup_home(tmp_path, url)
    monkeypatch.setattr(telemetry_buffer, "DB_PATH", tmp_path / ".observal/telemetry_buffer.db")
    monkeypatch.setattr(session_push, "_spawn_worker", lambda *a, **k: False)
    session_push._run_hook({"session_id": "session", "hook_event_name": "Stop"}, harness="kiro", home=tmp_path)
    root = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "USERPROFILE": str(tmp_path),
        "PYTHONPATH": str(root) + os.pathsep + str(root / "packages/observal-shared"),
    }
    args = [sys.executable, "-P", "-m", "observal_cli.hooks.session_push", "--harness", "kiro"]
    final_args = [*args, "--finalize-session", "session", "--cwd", str(tmp_path)]
    processes = [
        subprocess.Popen(final_args, env=env, cwd=tmp_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(2)
    ]
    capture = subprocess.Popen(
        args, env=env, cwd=tmp_path, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    processes.append(capture)
    try:
        capture.communicate(json.dumps({"session_id": "session", "hook_event_name": "Stop"}).encode(), timeout=30)
        for process in processes:
            assert process.wait(timeout=30) == 0
        assert base.read_cursor_state("session", home=tmp_path) == (source.stat().st_size, 1, True)
        assert state["stored"] == {("session", 0)}
        assert sum(bool(payload.get("final")) for payload in state["posts"]) == 1
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()


def test_killed_final_worker_preserves_stop_intent_and_capture_replay(peer, tmp_path, monkeypatch):
    """SIGKILL during final settling leaves explicit intent and permits retry without recapture."""
    import signal
    import time

    url, state = peer
    source = setup_home(tmp_path, url)
    monkeypatch.setattr(telemetry_buffer, "DB_PATH", tmp_path / "outbox.db")
    scheduled = []
    monkeypatch.setattr(session_push, "_spawn_worker", lambda *a, **k: scheduled.append(a) or True)
    event = {"session_id": "session", "hook_event_name": "Stop"}
    session_push._run_hook(event, harness="kiro", home=tmp_path)
    pending = list((tmp_path / ".observal/session-delivery").rglob("finalization.pending"))
    assert len(pending) == 1
    root = Path(__file__).resolve().parents[1]
    ready = tmp_path / "settling"
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "USERPROFILE": str(tmp_path),
        "PYTHONPATH": str(root) + os.pathsep + str(root / "packages/observal-shared"),
    }
    script = """import sys,time
from pathlib import Path
from observal_cli.hooks import session_push
home=Path(sys.argv[1])
def settle(path):
    (home/'settling').touch()
    time.sleep(120)
session_push._wait_until_stable=settle
sys.argv=['session_push','--harness','kiro','--finalize-session','session','--cwd',str(home)]
session_push.cli_main()
"""
    process = subprocess.Popen(
        [sys.executable, "-P", "-c", script, str(tmp_path)],
        env=env,
        cwd=tmp_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 20
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "Worker never entered settling"
        process.kill()
        exit_status = process.wait(timeout=10)
        if sys.platform != "win32":
            assert exit_status == -signal.SIGKILL
        assert base.read_cursor_state("session", home=tmp_path) == (source.stat().st_size, 1, False)
        assert pending[0].exists()
        session_push._run_hook(event, harness="kiro", home=tmp_path)
        assert len(scheduled) == 2
        assert len(state["posts"]) == 1
        assert pending[0].exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_real_waiting_v2_worker_exits_without_finalizing_after_resume(peer, tmp_path, monkeypatch):
    """A separate interpreter waiting on source settling cannot bypass prompt cancellation."""
    import time

    url, state = peer
    setup_home(tmp_path, url)
    monkeypatch.setattr(telemetry_buffer, "DB_PATH", tmp_path / ".observal/telemetry_buffer.db")
    monkeypatch.setattr(session_push, "_spawn_worker", lambda *a, **k: False)
    session_push._run_hook({"session_id": "session", "hook_event_name": "Stop"}, harness="kiro", home=tmp_path)
    root = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "USERPROFILE": str(tmp_path),
        "KIRO_HOME": str(tmp_path / ".kiro"),
        "PYTHONPATH": str(root) + os.pathsep + str(root / "packages/observal-shared"),
    }
    script = """import sys,time
from pathlib import Path
from observal_cli.hooks import session_push
home=Path(sys.argv[1])
def settle(path):
    (home/'settling').touch()
    deadline=time.monotonic()+20
    while not (home/'release').exists():
        if time.monotonic()>deadline: raise RuntimeError('release timeout')
        time.sleep(.02)
session_push._wait_until_stable=settle
session_push._finalize_session('kiro','session',str(home),home=home)
"""
    process = subprocess.Popen(
        [sys.executable, "-P", "-c", script, str(tmp_path)],
        env=env,
        cwd=tmp_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 20
        while not (tmp_path / "settling").exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert (tmp_path / "settling").exists()
        session_push._run_hook(
            {"session_id": "session", "hook_event_name": "UserPromptSubmit"}, harness="kiro", home=tmp_path
        )
        (tmp_path / "release").touch()
        assert process.wait(timeout=20) == 0
        assert not any(payload.get("final") for payload in state["posts"])
        assert not base.read_cursor_state("session", home=tmp_path)[2]
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
