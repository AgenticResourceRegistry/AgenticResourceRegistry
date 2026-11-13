# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Automatic capture must carry identity/credits before any manual reconciliation."""

import json
from pathlib import Path

import pytest

from observal_cli import lockfile, telemetry_buffer
from observal_cli.harness import ensure_loaded, get_adapter
from observal_cli.harness_specs.kiro_hooks_spec import build_kiro_hooks_file
from observal_cli.hooks import session_push
from observal_cli.sessions import base
from observal_cli.sessions.kiro import read_kiro_ide_session_agent


def record(kind, **fields):
    """Construct a native-shaped record with no private conversation content."""
    return json.dumps({"payload": {"type": kind, **fields}}) + "\n"


@pytest.fixture
def automatic_source(tmp_path, monkeypatch):
    """Run the real capture/outbox path with isolated files and mocked HTTP ACKs."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("KIRO_HOME", str(tmp_path / ".kiro"))
    path = tmp_path / ".kiro/sessions/workspace/session/messages.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(record("user", content="run tests"))
    companion = path.parent / "session.json"
    companion.write_text(
        json.dumps({"id": "session", "agentMode": "selected-alias", "workspacePaths": [str(tmp_path)]})
    )
    config = tmp_path / ".observal/config.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"server_url": "http://server", "user_id": "user", "access_token": "token"}))
    monkeypatch.setattr(telemetry_buffer, "DB_PATH", tmp_path / "outbox.db")
    ensure_loaded()
    adapter = get_adapter("kiro")
    monkeypatch.setattr(adapter, "should_capture_session", lambda *a, **k: True)
    # More than one installed agent; using the sole/latest cwd entry would be wrong.
    selected = {"id": "selected-id", "version": "1.2.3", "registry_status": "registry_backed"}
    other = {"id": "other-id", "version": "9.9.9", "registry_status": "registry_backed"}
    monkeypatch.setattr(
        lockfile, "get_agent_by_name", lambda name, **kw: {"selected-alias": selected, "other-alias": other}.get(name)
    )
    monkeypatch.setattr(base, "_maybe_upload_layer_snapshot", lambda *a, **k: None)
    posts = []

    def ack(url, token, payload, **kwargs):
        """Acknowledge records and metadata through the existing durable transport."""
        posts.append(dict(payload))
        return {
            "acknowledged_line": payload["start_offset"] + len(payload["lines"]) - 1,
            "acknowledged_offset": (payload.get("end_byte_offsets") or [payload.get("total_offset", 0)])[-1],
        }

    monkeypatch.setattr(base, "post_to_server_ack", ack)
    workers = []
    monkeypatch.setattr(session_push, "_spawn_worker", lambda *a, **k: workers.append(a))
    return path, companion, adapter, posts, workers


def test_first_automatic_capture_uses_companion_identity(automatic_source):
    """A fresh transcript has no session_start or profile-hook identity to rely on."""
    path, companion, adapter, posts, workers = automatic_source
    session_push._run_hook(
        {"session_id": "session", "cwd": str(path.parents[4]), "hook_event_name": "UserPromptSubmit"},
        harness="kiro",
        placement="standalone",
    )
    assert posts and posts[0]["agent_id"] == "selected-id"
    assert posts[0]["agent_version"] == "1.2.3"
    assert not any(p.get("final") for p in posts)
    assert not workers


@pytest.mark.parametrize("mode", ["vibe", "kiro_default", "unknown", None, 17])
def test_unresolvable_companion_does_not_guess_from_cwd(automatic_source, mode):
    """Shared hook identity is native session evidence, not the latest installed agent."""
    path, companion, adapter, posts, workers = automatic_source
    companion.write_text(json.dumps({"id": "session", "agentMode": mode}))
    session_push._run_hook(
        {"session_id": "session", "hook_event_name": "UserPromptSubmit"}, harness="kiro", placement="standalone"
    )
    assert posts and posts[0].get("agent_id") is None


def test_wrong_session_companion_is_not_trusted(automatic_source):
    """Companion identity cannot be borrowed from another native session."""
    path, companion, adapter, posts, workers = automatic_source
    companion.write_text('{"id":"other-session","agentMode":"selected-alias"}')
    assert read_kiro_ide_session_agent(path) is None


def test_conflicting_identity_stays_unattributed(automatic_source):
    """Disagreement between native sources must not guess which registry agent ran."""
    path, companion, adapter, posts, workers = automatic_source
    path.write_text(record("session_start", agentType="other-alias"))
    assert read_kiro_ide_session_agent(path) is None


def test_session_end_captures_late_credits_without_finalizing(automatic_source):
    """Late usage capture must not invent or replace Stop authority."""
    path, companion, adapter, posts, workers = automatic_source
    event = {"session_id": "session", "hook_event_name": "Stop"}
    session_push._run_hook(event, harness="kiro", placement="standalone")
    pending = next((Path.home() / ".observal/session-delivery").rglob("finalization.pending"))
    before = pending.read_bytes()
    with path.open("a") as stream:
        stream.write(record("usage_summary", promptTurnSummaries=[{"unit": "credit", "usage": 0.125}]))
    session_push._run_hook({**event, "hook_event_name": "SessionEnd"}, harness="kiro", placement="standalone")
    assert posts[-1]["agent_id"] == "selected-id" and posts[-1]["total_credits"] == 0.125
    assert not any(p.get("final") or "session_hash" in p for p in posts)
    assert pending.read_bytes() == before
    assert base.read_cursor_state("session")[2] is False
    assert not workers


def test_session_end_without_stop_never_creates_authority(automatic_source):
    """Exiting a quiet/failed session is metadata capture, not proof of completed work."""
    path, companion, adapter, posts, workers = automatic_source
    path.write_text(record("usage_summary", promptTurnSummaries=[{"unit": "credit", "usage": 0.125}]))
    session_push._run_hook(
        {"session_id": "session", "hook_event_name": "SessionEnd"}, harness="kiro", placement="standalone"
    )
    assert not list((Path.home() / ".observal/session-delivery").rglob("finalization.pending"))
    assert posts[-1]["total_credits"] == 0.125 and not posts[-1].get("final")
    assert not workers


def test_metadata_event_is_installed_without_duplicate_workspace_route(automatic_source, monkeypatch):
    """SessionEnd is auxiliary capture and must be deduplicated across scopes too."""
    path, companion, adapter, posts, workers = automatic_source
    root = Path.home() / ".kiro"
    hooks = root / "hooks/observal.json"
    hooks.parent.mkdir()
    hooks.write_text(json.dumps(build_kiro_hooks_file()))
    assert "SessionEnd" in {hook["trigger"] for hook in json.loads(hooks.read_text())["hooks"]}
    project_hooks = Path.home() / "project/.kiro/hooks/observal.json"
    monkeypatch.setenv("OBSERVAL_KIRO_ENGINE", "v3")
    updated = adapter.rewrite_hooks(build_kiro_hooks_file(), "selected-id", path=project_hooks)
    assert updated["hooks"] == []


def test_failed_session_end_capture_retries_without_finality(automatic_source, monkeypatch):
    """A late metadata transport failure is durable and retry does not finalize the source."""
    path, companion, adapter, posts, workers = automatic_source
    path.write_text(record("usage_summary", promptTurnSummaries=[{"unit": "credit", "usage": 0.125}]))
    ack = base.post_to_server_ack
    monkeypatch.setattr(base, "post_to_server_ack", lambda *a, **k: None)
    event = {"session_id": "session", "hook_event_name": "SessionEnd"}
    session_push._run_hook(event, harness="kiro", placement="standalone")
    assert telemetry_buffer.pending(destination="http://server", user_id="user")
    monkeypatch.setattr(base, "post_to_server_ack", ack)
    session_push._run_hook(event, harness="kiro", placement="standalone")
    assert not telemetry_buffer.pending(destination="http://server", user_id="user")
    assert posts[-1]["agent_id"] == "selected-id" and posts[-1]["total_credits"] == 0.125
    assert not posts[-1].get("final") and base.read_cursor_state("session")[2] is False
    assert not list((Path.home() / ".observal/session-delivery").rglob("finalization.pending"))


def test_repeated_session_end_does_not_duplicate_metadata(automatic_source):
    """An acknowledged unchanged teardown remains a no-op on duplicate invocation."""
    path, companion, adapter, posts, workers = automatic_source
    path.write_text(record("usage_summary", promptTurnSummaries=[{"unit": "credit", "usage": 0.125}]))
    event = {"session_id": "session", "hook_event_name": "SessionEnd"}
    session_push._run_hook(event, harness="kiro", placement="standalone")
    count = len(posts)
    session_push._run_hook(event, harness="kiro", placement="standalone")
    assert len(posts) == count


@pytest.mark.parametrize(
    "content",
    ["{", "[" * 10000 + "0" + "]" * 10000, " " * (1024 * 1024 + 1)],
    ids=["malformed", "deep-json", "oversized"],
)
def test_bad_companion_cannot_abort_automatic_record_capture(automatic_source, content):
    """Invalid identity metadata is ignored without dropping the actual transcript."""
    path, companion, adapter, posts, workers = automatic_source
    companion.write_text(content)
    session_push._run_hook(
        {"session_id": "session", "hook_event_name": "UserPromptSubmit"}, harness="kiro", placement="standalone"
    )
    assert posts and posts[0].get("agent_id") is None
    assert posts[0]["lines"]
