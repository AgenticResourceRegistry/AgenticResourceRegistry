# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Regression coverage for Codex recovery identity and durable outbox holds."""

from __future__ import annotations

import json
import os
import time

import pytest

from observal_cli import telemetry_buffer
from observal_cli.harness import ensure_loaded, get_adapter
from observal_cli.hooks import session_push
from observal_cli.sessions import base


def rollout(home, session_id, cwd=None):
    root = home / ".codex" / "sessions"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{session_id}.jsonl"
    records = []
    if cwd is not None:
        records.append({"type": "session_meta", "payload": {"cwd": cwd}})
    records.append({"type": "event_msg", "payload": {"type": "user_message", "message": "hi"}})
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    old = time.time() - 300
    os.utime(path, (old, old))
    return path


def config(home):
    cfg = {"server_url": "http://server", "user_id": "user", "access_token": "token"}
    directory = home / ".observal"
    directory.mkdir(exist_ok=True)
    (directory / "config.json").write_text(json.dumps(cfg))
    return cfg


def test_discovered_cwd_and_missing_or_wrong_hook_id(tmp_path):
    ensure_loaded()
    adapter = get_adapter("codex")
    first = rollout(tmp_path, "first", "/tmp/unrelated")
    second = rollout(tmp_path, "second", "/work/project")
    missing = rollout(tmp_path, "third")
    sources = {source.session_id: source for source in adapter.discover_session_sources(home=tmp_path)}
    assert {key: source.cwd for key, source in sources.items()} == {
        "first": "/tmp/unrelated",
        "second": "/work/project",
        "third": "",
    }
    assert adapter.resolve_session_source({"session_id": "not-found", "cwd": "/work/project"}, home=tmp_path) is None
    assert adapter.resolve_session_source({"cwd": "/work/project"}, home=tmp_path) is None
    assert adapter.resolve_session_source({"transcript_path": str(first)}, home=tmp_path).session_id == "first"
    assert adapter.resolve_session_source({"transcript_path": str(second)}, home=tmp_path).cwd == "/work/project"
    assert adapter.resolve_session_source({"session_id": "third"}, home=tmp_path).path == missing


def test_recovery_ignores_both_ambient_markers_and_delivers_unattributed(tmp_path, monkeypatch):
    config(tmp_path)
    db = tmp_path / "queue.db"
    monkeypatch.setattr(telemetry_buffer, "DB_PATH", db)
    monkeypatch.setattr(base, "recover_cursor_from_server", lambda _source, _config, **kw: (0, 0))
    monkeypatch.setattr(session_push, "read_cursor_state", lambda *a, **kw: (0, 0, False))
    monkeypatch.setattr(base, "_get_cached_layer_hash", lambda *a: None)
    monkeypatch.setattr(base, "_capabilities_for_session", lambda *a: None)
    monkeypatch.setenv("OBSERVAL_AGENT_ID", "active-id")
    monkeypatch.setenv("OBSERVAL_AGENT_NAME", "active-name")
    rollout(tmp_path, "first", "/tmp/unrelated")
    rollout(tmp_path, "second", "/work/pinned-agent")
    rollout(tmp_path, "third")
    observed = []

    def post(payload, _cfg):
        observed.append(payload)
        return {
            "acknowledged_line": payload["start_offset"] + len(payload["lines"]) - 1,
            "acknowledged_offset": payload.get("end_byte_offsets", [payload.get("total_offset", 0)])[-1],
        }

    monkeypatch.setattr(base, "post_to_server_ack", lambda _dest, _token, payload, config: post(payload, config))
    session_push._recover_sessions("codex", home=tmp_path)
    assert {payload["session_id"] for payload in observed} == {"first", "second", "third"}
    assert all(payload["agent_id"] is None and payload["agent_version"] is None for payload in observed)


@pytest.mark.parametrize("marker", ["OBSERVAL_AGENT_ID", "OBSERVAL_AGENT_NAME"])
def test_live_hook_and_finalizer_ignore_static_agent_marker(tmp_path, monkeypatch, marker):
    cfg = config(tmp_path)
    rollout(tmp_path, "live", "/work/project")
    monkeypatch.setenv(marker, "active-agent")
    monkeypatch.delenv("OBSERVAL_AGENT_NAME" if marker == "OBSERVAL_AGENT_ID" else "OBSERVAL_AGENT_ID", raising=False)
    monkeypatch.setattr(telemetry_buffer, "DB_PATH", tmp_path / "queue.db")
    monkeypatch.setattr(base, "_get_cached_layer_hash", lambda *a: None)
    monkeypatch.setattr(base, "_capabilities_for_session", lambda *a: None)
    monkeypatch.setattr(base, "recover_cursor_from_server", lambda *a, **kw: (0, 0))
    monkeypatch.setattr(session_push, "_wait_until_stable", lambda *a: None)
    monkeypatch.setattr(session_push, "_spawn_worker", lambda *a, **kw: None)
    observed = []

    def post(_dest, _token, payload, config):
        observed.append(payload)
        return {
            "acknowledged_line": payload["start_offset"] + len(payload["lines"]) - 1,
            "acknowledged_offset": payload.get("end_byte_offsets", [payload.get("total_offset", 0)])[-1],
        }

    monkeypatch.setattr(base, "post_to_server_ack", post)
    session_push._run_hook({"session_id": "live", "event": "Stop"}, harness="codex", home=tmp_path)
    session_push._finalize_session("codex", "live", "/other", home=tmp_path)
    assert observed and all(item["agent_id"] is None and item["agent_version"] is None for item in observed)
    assert cfg["user_id"] == "user"


def test_public_reconcile_passes_recovery_identity_scope(tmp_path, monkeypatch):
    from observal_cli import cmd_reconcile_cli

    ensure_loaded()
    path = rollout(tmp_path, "reconciled", "/work/pinned-agent")
    source = get_adapter("codex").resolve_session_source({"session_id": "reconciled"}, home=tmp_path)
    monkeypatch.setenv("OBSERVAL_AGENT_ID", "active-id")
    monkeypatch.setenv("OBSERVAL_AGENT_NAME", "active-name")
    adapter = get_adapter("codex")
    monkeypatch.setattr(adapter, "discover_session_sources", lambda **kw: [source])
    monkeypatch.setattr(adapter, "should_capture_session", lambda *a, **kw: True)
    monkeypatch.setattr(cmd_reconcile_cli, "read_cursor_state", lambda *a: (0, 0, False))
    monkeypatch.setattr(cmd_reconcile_cli, "recover_cursor_from_server", lambda *a: (0, 0))
    observed = []
    monkeypatch.setattr(cmd_reconcile_cli, "drain_session_source", lambda *a, **kw: observed.append(kw) or True)
    cmd_reconcile_cli._reconcile_harness("codex", config(tmp_path), 24, False)
    assert path.exists() and observed[0]["identity_scope"] == "recovery"
    monkeypatch.setattr(base, "_lookup_lockfile_agent", lambda *a, **kw: pytest.fail("directory fallback used"))
    assert base._resolve_agent(
        source.cwd,
        [],
        source.path,
        harness="codex",
        session_ids=(source.session_id,),
        identity_scope=observed[0]["identity_scope"],
    ) == (None, None)


def test_missing_hook_id_schedules_sanitized_recovery(tmp_path, monkeypatch):
    rollout(tmp_path, "first")
    spawned = []
    monkeypatch.setattr(session_push.subprocess, "Popen", lambda argv, **kw: spawned.append((argv, kw)))
    monkeypatch.setenv("OBSERVAL_AGENT_ID", "active-id")
    monkeypatch.setenv("OBSERVAL_AGENT_NAME", "active-name")
    session_push._run_hook({"cwd": "/work"}, harness="codex", home=tmp_path)
    session_push._run_hook({"session_id": "miss"}, harness="codex", home=tmp_path)
    assert len(spawned) == 2
    assert all("OBSERVAL_AGENT_ID" not in kw["env"] and "OBSERVAL_AGENT_NAME" not in kw["env"] for _, kw in spawned)
    assert all("--recover" in argv for argv, _ in spawned)


def test_hold_all_ranges_before_post_and_review_atomically(tmp_path):
    ensure_loaded()
    db = tmp_path / "queue.db"
    cfg = config(tmp_path)
    root = tmp_path / ".codex" / "sessions"
    root.mkdir(parents=True)
    path = root / "held.jsonl"
    lines = [json.dumps({"type": "event_msg", "payload": {"message": f"line-{i}"}}) for i in range(2)]
    path.write_text("".join(line + "\n" for line in lines))

    def payload(session, start, agent=None):
        return {
            "harness": "codex",
            "session_id": session,
            "agent_id": agent,
            "agent_version": "1" if agent else None,
            "start_offset": start,
            "lines": [lines[start]],
            "end_byte_offsets": [sum(len(line) + 1 for line in lines[: start + 1])],
        }

    a = payload("held", 0, "wrong")
    b = payload("held", 1, "wrong")
    clean = payload("clean", 0)
    for item in (a, b, clean):
        telemetry_buffer.enqueue(item, destination="http://server", user_id="user", db_path=db)
    posted = []

    def post(item, _cfg):
        posted.append(item["session_id"])
        return {"acknowledged_line": item["start_offset"], "acknowledged_offset": item["end_byte_offsets"][-1]}

    assert not base.drain_outbox(cfg, home=tmp_path, db_path=db, post=post)
    assert posted == ["clean"]
    assert len(telemetry_buffer.held_sessions(db_path=db)) == 1
    assert telemetry_buffer.stats(db_path=db)["pending"] == 2
    with pytest.raises(telemetry_buffer.OutboxConflictError):
        telemetry_buffer.enqueue(a, destination="http://server", user_id="user", db_path=db)
    assert not telemetry_buffer.hold_if_current(
        telemetry_buffer.OutboxItem(999, "http://server", "user", "codex", "held", "held", 0, 0, 1, False, a, 0),
        "stale",
        db_path=db,
    )
    with telemetry_buffer._connect(db) as conn:
        rows = conn.execute(
            "SELECT id, payload FROM session_outbox WHERE session_id = 'held' ORDER BY start_line"
        ).fetchall()
    corrections = {}
    for row_id, raw in rows:
        corrected = json.loads(raw)
        corrected["agent_id"] = corrected["agent_version"] = None
        corrections[row_id] = (
            corrected,
            {
                "session_id": "held",
                "path": str(path.resolve()),
                "source_kind": "codex-rollout",
                "identity": "unattributed",
            },
        )
    with pytest.raises(telemetry_buffer.OutboxConflictError):
        telemetry_buffer.review_hold(
            "http://server", "user", "codex", "held", {rows[0][0]: corrections[rows[0][0]]}, db_path=db
        )
    tampered = dict(corrections)
    tampered_payload = dict(corrections[rows[0][0]][0], hook_event="Forged")
    tampered[rows[0][0]] = tampered_payload, corrections[rows[0][0]][1]
    with pytest.raises(telemetry_buffer.OutboxConflictError, match="only agent identity"):
        telemetry_buffer.review_hold("http://server", "user", "codex", "held", tampered, db_path=db)
    telemetry_buffer.review_hold("http://server", "user", "codex", "held", corrections, db_path=db)
    assert base.drain_outbox(cfg, home=tmp_path, db_path=db, post=post)
    assert posted == ["clean", "held", "held"]
    assert telemetry_buffer.stats(db_path=db)["pending"] == 0
