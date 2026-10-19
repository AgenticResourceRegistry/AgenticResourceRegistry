# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Session-bound Codex identity and outbox replacement regression checks."""

import json

from observal_cli import telemetry_buffer
from observal_cli.delegation import tasks
from observal_cli.harness import ensure_loaded, get_adapter
from observal_cli.sessions import base


def test_delegated_identity_survives_recovery_without_ambient_markers(tmp_path, monkeypatch):
    ensure_loaded()
    root = tmp_path / ".codex" / "sessions"
    root.mkdir(parents=True)
    path = root / "delegated.jsonl"
    path.write_text(json.dumps({"type": "session_meta", "payload": {"cwd": "/child"}}) + "\n")
    adapter = get_adapter("codex")
    source = adapter.resolve_session_source({"session_id": "delegated"}, home=tmp_path)
    monkeypatch.setattr(tasks, "delegated_agent", lambda cwd, ids: ("child-id", "2") if "delegated" in ids else None)
    monkeypatch.setenv("OBSERVAL_AGENT_ID", "bystander")
    monkeypatch.setenv("OBSERVAL_AGENT_NAME", "bystander")
    agent, version = base._resolve_agent(
        source.cwd, [], path, harness="codex", session_ids=(source.session_id,), identity_scope="recovery"
    )
    assert (agent, version) == ("child-id", "2")
    payload = {
        "agent_id": agent,
        "agent_version": version,
        "session_id": "delegated",
        "harness": "codex",
        "lines": [path.read_text().strip()],
        "end_byte_offsets": [path.stat().st_size],
    }
    proof = adapter.outbox_provenance(source, payload)
    item = telemetry_buffer.OutboxItem(
        1,
        "http://server",
        "user",
        "codex",
        "delegated",
        "delegated",
        0,
        0,
        path.stat().st_size,
        False,
        payload,
        0,
        proof,
    )
    assert adapter.validate_outbox_provenance(item)
    assert not adapter.validate_outbox_provenance(
        telemetry_buffer.OutboxItem(
            1, "http://server", "user", "codex", "other", "other", 0, 0, 1, False, payload, 0, proof
        )
    )


def test_replacement_during_http_post_keeps_corrected_batch(tmp_path):
    ensure_loaded()
    db = tmp_path / "outbox.db"
    config = {"server_url": "http://server", "user_id": "user"}
    root = tmp_path / ".codex" / "sessions"
    root.mkdir(parents=True)
    path = root / "one.jsonl"
    line = json.dumps({"type": "event_msg", "payload": {"type": "user_message"}})
    path.write_text(line + "\n")
    original = {
        "harness": "codex",
        "session_id": "one",
        "agent_id": None,
        "agent_version": None,
        "start_offset": 0,
        "lines": [line],
        "end_byte_offsets": [path.stat().st_size],
    }
    telemetry_buffer.enqueue(original, destination="http://server", user_id="user", db_path=db)
    replacement = dict(original, final=True)
    proof = {
        "session_id": "one",
        "path": str(path.resolve()),
        "source_kind": "codex-rollout",
        "identity": "unattributed",
        "agent_id": None,
        "agent_version": None,
    }

    def post(_payload, _cfg):
        telemetry_buffer.enqueue(replacement, destination="http://server", user_id="user", db_path=db, provenance=proof)
        return {"acknowledged_line": 0, "acknowledged_offset": path.stat().st_size}

    assert not base.drain_outbox(config, home=tmp_path, db_path=db, post=post)
    remaining = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)
    assert len(remaining) == 1 and remaining[0].payload["final"] is True
    assert remaining[0].provenance == proof


def test_same_range_replacement_does_not_hold_stale_snapshot(tmp_path):
    db = tmp_path / "outbox.db"
    root = tmp_path / ".codex" / "sessions"
    root.mkdir(parents=True)
    path = root / "one.jsonl"
    line = json.dumps({"type": "event_msg", "payload": {"type": "user_message"}})
    path.write_text(line + "\n")
    legacy = {
        "harness": "codex",
        "session_id": "one",
        "agent_id": "old",
        "agent_version": "1",
        "start_offset": 0,
        "lines": [line],
        "end_byte_offsets": [path.stat().st_size],
    }
    telemetry_buffer.enqueue(legacy, destination="http://server", user_id="user", db_path=db)
    snapshot = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)[0]
    replacement = dict(legacy, agent_id=None, agent_version=None, final=True)
    proof = {
        "session_id": "one",
        "path": str(path.resolve()),
        "source_kind": "codex-rollout",
        "identity": "unattributed",
        "agent_id": None,
        "agent_version": None,
    }
    telemetry_buffer.enqueue(replacement, destination="http://server", user_id="user", db_path=db, provenance=proof)
    assert not telemetry_buffer.hold_if_current(snapshot, "stale", db_path=db)
    assert telemetry_buffer.held_sessions(db_path=db) == []
    latest = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)[0]
    assert latest.revision > snapshot.revision
    assert latest.payload["agent_id"] is None and latest.provenance == proof
