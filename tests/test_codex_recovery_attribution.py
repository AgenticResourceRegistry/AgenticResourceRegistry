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


def test_live_subagent_and_recovery_keep_own_identity_and_user_boundary(tmp_path, monkeypatch):
    """A hook's parent marker cannot claim the parent, another user, or a damaged rollout."""
    cfg = config(tmp_path)
    db = tmp_path / "queue.db"
    monkeypatch.setattr(telemetry_buffer, "DB_PATH", db)
    monkeypatch.setattr(base, "recover_cursor_from_server", lambda *a, **kw: (0, 0))
    monkeypatch.setattr(base, "_get_cached_layer_hash", lambda *a: None)
    monkeypatch.setattr(base, "_capabilities_for_session", lambda *a: None)
    monkeypatch.setattr(session_push, "_spawn_worker", lambda *a, **kw: None)
    monkeypatch.setattr(session_push, "read_cursor_state", lambda *a, **kw: (0, 0, False))
    monkeypatch.setenv("OBSERVAL_AGENT_ID", "other-agent")
    monkeypatch.setenv("OBSERVAL_AGENT_NAME", "other-agent")
    project = str(tmp_path / "project")
    monkeypatch.setattr(
        "observal_cli.lockfile.read_registry_lockfile",
        lambda: (
            {},
            {
                "harnesses": {
                    "codex": {
                        "agents": [
                            {
                                "name": "quick-fixer",
                                "id": "agent-uuid",
                                "version": "1.0.0",
                                "scope": "project",
                                "directory": project,
                            }
                        ]
                    }
                }
            },
        ),
    )
    root = tmp_path / ".codex" / "sessions"
    root.mkdir(parents=True)

    def record(session, **meta):
        path = root / f"{session}.jsonl"
        path.write_text(json.dumps({"type": "session_meta", "payload": {"id": session, "cwd": project, **meta}}) + "\n")
        return path

    record("parent", thread_source="user")
    record("child", thread_source="subagent", agent_role="quick-fixer", parent_thread_id="parent")
    record("queued", thread_source="subagent", agent_role="quick-fixer", parent_thread_id="parent")
    damaged = record("damaged", thread_source="subagent", agent_role="quick-fixer")
    observed = []

    def post(payload, _cfg):
        observed.append(payload)
        return {
            "acknowledged_line": payload["start_offset"] + len(payload["lines"]) - 1,
            "acknowledged_offset": payload["end_byte_offsets"][-1],
        }

    monkeypatch.setattr(base, "post_to_server_ack", lambda _url, _token, body, config: post(body, config))
    session_push._run_hook({"session_id": "child", "event": "Stop"}, harness="codex", home=tmp_path)
    session_push._run_hook({"session_id": "parent", "event": "Stop"}, harness="codex", home=tmp_path)
    assert [(p["session_id"], p["agent_id"], p["agent_version"], p["parent_session_id"]) for p in observed] == [
        ("child", "agent-uuid", "1.0.0", "parent"),
        ("parent", None, None, None),
    ]

    # A valid subagent queued for another user cannot be replayed by this user's drain.
    adapter = get_adapter("codex")
    other_source = adapter.resolve_session_source({"session_id": "queued"}, home=tmp_path)
    other_line = other_source.path.read_text().strip()
    other_payload = base.build_payload(
        "queued",
        [other_line],
        0,
        "Stop",
        0,
        cwd=project,
        session_jsonl=other_source.path,
        harness="codex",
        identity_scope="recovery",
    )
    other_payload["harness"] = "codex"
    other_payload["end_byte_offsets"] = [other_source.path.stat().st_size]
    telemetry_buffer.enqueue(
        other_payload,
        destination="http://server",
        user_id="other-user",
        db_path=db,
        provenance=adapter.outbox_provenance(other_source, other_payload),
    )
    # This user's queued recovery still credits a verified subagent.
    recovered = record("recovered", thread_source="subagent", agent_role="quick-fixer")
    old = time.time() - 300
    os.utime(recovered, (old, old))
    # Missing/truncated queued proof must be held before sending, then explicitly quarantined.
    damaged_payload = dict(
        other_payload,
        session_id="damaged",
        lines=[damaged.read_text().strip()],
        end_byte_offsets=[damaged.stat().st_size],
    )
    damaged_source = adapter.resolve_session_source({"session_id": "damaged"}, home=tmp_path)
    telemetry_buffer.enqueue(
        damaged_payload,
        destination="http://server",
        user_id="user",
        db_path=db,
        provenance=adapter.outbox_provenance(damaged_source, damaged_payload),
    )
    damaged.write_text("{truncated")
    assert not base.drain_outbox(cfg, home=tmp_path, db_path=db, post=post)
    assert {hold[3] for hold in telemetry_buffer.held_sessions(db_path=db)} == {"damaged"}
    session_push._recover_sessions("codex", home=tmp_path)
    assert any(
        p["session_id"] == "recovered" and (p["agent_id"], p["agent_version"]) == ("agent-uuid", "1.0.0")
        for p in observed
    )
    assert not any(p["session_id"] in {"queued", "damaged"} for p in observed)
    assert len(telemetry_buffer.pending(destination="http://server", user_id="other-user", db_path=db)) == 1
    assert telemetry_buffer.quarantine_held_session("http://server", "user", "codex", "damaged", db_path=db) == 1
    assert not telemetry_buffer.is_held("http://server", "user", "codex", "damaged", db_path=db)
    assert db.with_suffix(".quarantined.jsonl").exists()


def test_detached_codex_workers_drop_stale_parent_markers(tmp_path, monkeypatch):
    ensure_loaded()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEX_HOME", "codex-home")
    monkeypatch.setenv("OBSERVAL_AGENT_ID", "stale-parent")
    monkeypatch.setenv("OBSERVAL_AGENT_NAME", "stale-parent")
    spawned = []
    # The launcher probes importability with subprocess.run; do not replace its
    # Popen until that probe has completed.
    from observal_cli.shared.launcher import importable_in_isolation

    importable_in_isolation()
    monkeypatch.setattr(session_push.subprocess, "Popen", lambda argv, **kw: spawned.append(kw["env"]))
    for args in (("--recover",), ("--drain-outbox",), ("--finalize-session", "child")):
        session_push._spawn_worker(*args, harness="codex")
    assert len(spawned) == 3
    assert all(
        env["CODEX_HOME"] == str(tmp_path / "codex-home")
        and "OBSERVAL_AGENT_ID" not in env
        and "OBSERVAL_AGENT_NAME" not in env
        for env in spawned
    )
    assert os.environ["OBSERVAL_AGENT_ID"] == "stale-parent"


def test_child_role_does_not_inherit_parent_delegation_or_other_project(tmp_path, monkeypatch):
    ensure_loaded()
    project = tmp_path / "project"
    other = tmp_path / "other"
    monkeypatch.setattr(
        "observal_cli.lockfile.read_registry_lockfile",
        lambda: (
            {},
            {
                "harnesses": {
                    "codex": {
                        "agents": [
                            {
                                "name": "quick-fixer",
                                "id": "agent-uuid",
                                "version": "1",
                                "scope": "project",
                                "directory": str(project),
                            }
                        ]
                    }
                }
            },
        ),
    )
    monkeypatch.setattr(
        "observal_cli.delegation.tasks.delegated_agent",
        lambda cwd, session_ids: ("parent-agent", "2") if "parent" in session_ids else None,
    )
    path = rollout(tmp_path, "child", str(other))
    path.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "id": "child",
                    "cwd": str(other),
                    "thread_source": "subagent",
                    "agent_role": "quick-fixer",
                    "parent_thread_id": "parent",
                },
            }
        )
        + "\n"
    )
    source = get_adapter("codex").resolve_session_source({"session_id": "child"}, home=tmp_path)
    assert base._resolve_agent(
        source.cwd,
        [],
        source.path,
        harness="codex",
        session_ids=("child", source.parent_session_id),
        identity_scope="recovery",
    ) == (None, None)
    assert get_adapter("codex").outbox_provenance(source, {"agent_id": "parent-agent", "agent_version": "2"}) is None


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
    from observal_cli.shared.launcher import importable_in_isolation

    importable_in_isolation()
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
                "sessions_root": str(root),
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
