# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The shipped recovery recipe must inspect local completion without changing it."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from observal_cli import telemetry_buffer
from observal_cli.harness import ensure_loaded, get_adapter
from observal_cli.sessions import base, delivery_guard


@pytest.fixture
def recovery_source(tmp_path, monkeypatch):
    """Isolate a native-layout parent source and the user's authentication state."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("KIRO_HOME", str(home / ".kiro"))
    path = home / ".kiro/sessions/workspace/session/messages.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"payload": {"type": "user", "content": "private-prompt"}}) + "\n")
    path.with_name("session.json").write_text(json.dumps({"workspacePaths": [str(tmp_path)]}))
    cfg = {"server_url": "http://peer", "user_id": "user", "access_token": "private-token"}
    config = home / ".observal/config.json"
    config.parent.mkdir()
    config.write_text(json.dumps(cfg))
    ensure_loaded()
    adapter = get_adapter("kiro")
    source = adapter.resolve_session_source({"session_id": "session"})
    return home, path, cfg, adapter, source


def snapshot(home):
    """Track every state file's contents and mtime, including migration/lock side effects."""
    return {str(p.relative_to(home)): (p.read_bytes(), p.stat().st_mtime_ns) for p in home.rglob("*") if p.is_file()}


@pytest.mark.parametrize("with_state,with_wal", [(False, False), (True, False), (True, True)])
def test_documented_completion_inspection_is_read_only(recovery_source, with_state, with_wal, request):
    """Run the actual skill recipe with delivery, cursor writes, and DB migration forbidden."""
    home, path, cfg, adapter, source = recovery_source
    if with_state:
        base.write_cursor(source.checkpoint_key, path.stat().st_size, 1, home=home)
        with delivery_guard.kiro_delivery_guard(
            source,
            {"hook_event_name": "Stop"},
            cfg,
            final_requested=True,
            finalizable=[source],
            home=home,
        ):
            pass
        if with_wal:
            writer = telemetry_buffer._connect(home / ".observal/telemetry_buffer.db")
            request.addfinalizer(writer.close)
        telemetry_buffer.enqueue(
            {
                "harness": "kiro",
                "session_id": "session",
                "start_offset": 1,
                "lines": ["private-queued-record"],
                "final": True,
            },
            destination=cfg["server_url"],
            user_id=cfg["user_id"],
            db_path=home / ".observal/telemetry_buffer.db",
        )
    if with_wal:
        assert (home / ".observal/telemetry_buffer.db-wal").stat().st_size > 0
    root = Path(__file__).resolve().parents[1]
    guide = root / "observal_cli/skills/observal-advanced/references/recovery-workflows.md"
    marker = "PYTHONDONTWRITEBYTECODE=1 python - 'SESSION_ID' <<'PY'\n"
    recipe = guide.read_text().split(marker, 1)[1].split("\nPY\n", 1)[0]
    forbid = """from observal_cli import telemetry_buffer
from observal_cli.sessions import base, delivery_guard
def forbidden(*args, **kwargs):
    raise AssertionError("Inspection attempted mutation or delivery")
telemetry_buffer._connect = forbidden
base.write_cursor = forbidden
base.drain_outbox = forbidden
base.post_to_server_ack = forbidden
delivery_guard._transport_claim = forbidden
"""
    before = snapshot(home)
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "KIRO_HOME": str(home / ".kiro"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(root) + os.pathsep + str(root / "packages/observal-shared"),
    }
    result = subprocess.run(
        [sys.executable, "-c", forbid + recipe, "session"],
        env=env,
        cwd=home,
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    data = json.loads(result.stdout)
    assert snapshot(home) == before
    assert not any(secret in result.stdout for secret in ("private-token", "private-prompt", "private-queued-record"))
    assert data["cursors"][0]["valid"] is with_state
    assert data["cursors"][0]["finalized"] is False
    assert data["stop_intent_present"] is with_state
    assert data["matching_stop_intent"] is with_state
    assert data["outbox_exists"] is with_state
    if with_state:
        assert data["outbox"] == [{"session_id": "session", "pending_batches": 1, "pending_final_batches": 1}]
    else:
        assert data["outbox"] == []


def test_pending_stop_does_not_bypass_transcript_discovery_window(recovery_source):
    """A fresh Stop intent cannot make a two-day-old transcript appear in --since 24."""
    home, path, cfg, adapter, source = recovery_source
    with delivery_guard.kiro_delivery_guard(
        source,
        {"hook_event_name": "Stop"},
        cfg,
        final_requested=True,
        finalizable=[source],
        home=home,
    ):
        pass
    age = time.time() - 48 * 3600
    os.utime(path, (age, age))
    assert adapter.session_finalization_pending(source, cfg, home=home)
    assert adapter.discover_session_sources(since_hours=24) == []
    assert [s.session_id for s in adapter.discover_session_sources(since_hours=168)] == ["session"]
