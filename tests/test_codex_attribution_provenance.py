# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Session-bound Codex identity and outbox replacement regression checks."""

import json
import sqlite3

import pytest

from observal_cli import telemetry_buffer
from observal_cli.delegation import tasks
from observal_cli.harness import ensure_loaded, get_adapter
from observal_cli.sessions import base


def _rollout(tmp_path, session_id="one"):
    root = tmp_path / ".codex" / "sessions"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{session_id}.jsonl"
    line = json.dumps({"type": "session_meta", "payload": {"id": session_id}})
    path.write_text(line + "\n")
    return path, line


def _queued(path, line, session_id="one", *, final=False):
    payload = {
        "harness": "codex",
        "session_id": session_id,
        "start_offset": 0,
        "lines": [line],
        "end_byte_offsets": [path.stat().st_size],
        "agent_id": None,
        "agent_version": None,
    }
    if final:
        payload.update(final=True, session_hash=base.hash_session_source(path)[0], hashed_line_count=1)
    return payload


@pytest.mark.parametrize("original_ending,new_ending", [("\n", "\r\n"), ("\r\n", "\n")])
def test_same_range_final_promotion_uses_validated_byte_prefix(tmp_path, original_ending, new_ending):
    ensure_loaded()
    path, line = _rollout(tmp_path)
    adapter = get_adapter("codex")
    source = adapter.resolve_session_source({"session_id": "one"}, home=tmp_path)
    db = tmp_path / "outbox.db"
    path.write_bytes((line + original_ending).encode())
    original = _queued(path, line)
    telemetry_buffer.enqueue(
        original,
        destination="http://server",
        user_id="user",
        db_path=db,
        provenance=adapter.outbox_provenance(source, original),
    )
    path.write_bytes((line + new_ending).encode())
    replacement = _queued(path, line, final=True)
    telemetry_buffer.enqueue(
        replacement,
        destination="http://server",
        user_id="user",
        db_path=db,
        provenance=adapter.outbox_provenance(source, replacement),
    )
    item = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)[0]
    assert item.end_offset == replacement["end_byte_offsets"][-1]
    assert adapter.validate_outbox_provenance(item)


def test_symlinked_codex_root_preserves_lexical_provenance(tmp_path):
    ensure_loaded()
    actual = tmp_path / "volume"
    actual.mkdir()
    (actual / "sessions").mkdir()
    (tmp_path / ".codex").symlink_to(actual, target_is_directory=True)
    path = tmp_path / ".codex" / "sessions" / "one.jsonl"
    line = json.dumps({"type": "session_meta", "payload": {"id": "one"}})
    path.write_text(line + "\n")
    adapter = get_adapter("codex")
    source = adapter.resolve_session_source({"session_id": "one"}, home=tmp_path)
    payload = _queued(path, line)
    proof = adapter.outbox_provenance(source, payload)
    assert proof["path"] == str(path)
    assert proof["path"] != str(path.resolve())
    db = tmp_path / "outbox.db"
    telemetry_buffer.enqueue(payload, destination="http://server", user_id="user", db_path=db, provenance=proof)
    posted = []
    assert base.drain_outbox(
        {"server_url": "http://server", "user_id": "user"},
        home=tmp_path,
        db_path=db,
        post=lambda item, cfg: (
            posted.append(item) or {"acknowledged_line": 0, "acknowledged_offset": path.stat().st_size}
        ),
    )
    assert posted == [payload]


def test_final_batch_remains_valid_after_rollout_appends(tmp_path):
    ensure_loaded()
    path, line = _rollout(tmp_path)
    payload = _queued(path, line, final=True)
    adapter = get_adapter("codex")
    source = adapter.resolve_session_source({"session_id": "one"}, home=tmp_path)
    db = tmp_path / "outbox.db"
    telemetry_buffer.enqueue(
        payload,
        destination="http://server",
        user_id="user",
        db_path=db,
        provenance=adapter.outbox_provenance(source, payload),
    )
    with path.open("a") as stream:
        stream.write(json.dumps({"type": "event_msg", "payload": {"message": "later"}}) + "\n")
    posted = []
    assert base.drain_outbox(
        {"server_url": "http://server", "user_id": "user"},
        home=tmp_path,
        db_path=db,
        post=lambda item, cfg: (
            posted.append(item) or {"acknowledged_line": 0, "acknowledged_offset": payload["end_byte_offsets"][0]}
        ),
    )
    assert posted == [payload]
    assert telemetry_buffer.held_sessions(db_path=db) == []


def test_queued_prefix_tampering_is_not_excused_by_append(tmp_path):
    ensure_loaded()
    path, line = _rollout(tmp_path)
    payload = _queued(path, line, final=True)
    adapter = get_adapter("codex")
    source = adapter.resolve_session_source({"session_id": "one"}, home=tmp_path)
    proof = adapter.outbox_provenance(source, payload)
    db = tmp_path / "outbox.db"
    telemetry_buffer.enqueue(payload, destination="http://server", user_id="user", db_path=db, provenance=proof)
    path.write_text(line.replace('"one"', '"two"') + "\n" + line + "\n")
    assert not base.drain_outbox(
        {"server_url": "http://server", "user_id": "user"},
        home=tmp_path,
        db_path=db,
        post=lambda item, cfg: pytest.fail("tampered prefix posted"),
    )
    assert len(telemetry_buffer.held_sessions(db_path=db)) == 1


def test_provenance_validation_does_not_hold_sqlite_writer_lock(tmp_path, monkeypatch):
    ensure_loaded()
    path, line = _rollout(tmp_path)
    payload = _queued(path, line)
    adapter = get_adapter("codex")
    source = adapter.resolve_session_source({"session_id": "one"}, home=tmp_path)
    db = tmp_path / "outbox.db"
    telemetry_buffer._connect(db).close()
    original = adapter.validate_outbox_provenance
    observed = []

    def validating(item):
        with sqlite3.connect(db, timeout=0) as other:
            other.execute("BEGIN IMMEDIATE")
            observed.append(True)
        return original(item)

    monkeypatch.setattr(adapter, "validate_outbox_provenance", validating)
    telemetry_buffer.enqueue(
        payload,
        destination="http://server",
        user_id="user",
        db_path=db,
        provenance=adapter.outbox_provenance(source, payload),
    )
    assert observed == [True]
    telemetry_buffer.hold_if_current(
        telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)[0], "review", db_path=db
    )
    with telemetry_buffer._connect(db) as conn:
        row = conn.execute("SELECT id FROM session_outbox").fetchone()
    corrections = {row[0]: (payload, adapter.outbox_provenance(source, payload))}
    telemetry_buffer.review_hold("http://server", "user", "codex", "one", corrections, db_path=db)
    assert observed == [True, True]


def test_review_rechecks_revision_after_unlocked_validation(tmp_path, monkeypatch):
    ensure_loaded()
    path, line = _rollout(tmp_path)
    payload = _queued(path, line)
    adapter = get_adapter("codex")
    source = adapter.resolve_session_source({"session_id": "one"}, home=tmp_path)
    proof = adapter.outbox_provenance(source, payload)
    db = tmp_path / "outbox.db"
    row_id = telemetry_buffer.enqueue(payload, destination="http://server", user_id="user", db_path=db)
    item = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)[0]
    assert telemetry_buffer.hold_if_current(item, "review", db_path=db)
    original = adapter.validate_outbox_provenance

    def racing_validator(candidate):
        with sqlite3.connect(db, timeout=0) as other:
            other.execute("UPDATE session_outbox SET revision = revision + 1 WHERE id = ?", (row_id,))
        return original(candidate)

    monkeypatch.setattr(adapter, "validate_outbox_provenance", racing_validator)
    with pytest.raises(telemetry_buffer.OutboxConflictError, match="changed during review"):
        telemetry_buffer.review_hold("http://server", "user", "codex", "one", {row_id: (payload, proof)}, db_path=db)
    assert len(telemetry_buffer.held_sessions(db_path=db)) == 1


def test_long_rollout_validator_reads_only_consumed_prefix(tmp_path, monkeypatch):
    ensure_loaded()
    path, line = _rollout(tmp_path)
    payload = _queued(path, line, final=True)
    source = get_adapter("codex").resolve_session_source({"session_id": "one"}, home=tmp_path)
    proof = get_adapter("codex").outbox_provenance(source, payload)
    with path.open("a") as stream:
        for index in range(10000):
            stream.write(json.dumps({"type": "event_msg", "payload": {"message": index}}) + "\n")
    original_open = type(path).open
    reads = []

    def tracked_open(self, *args, **kwargs):
        handle = original_open(self, *args, **kwargs)
        if self != path or args[:1] != ("rb",):
            return handle

        class Reader:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                handle.close()

            def read(self, size=-1):
                reads.append(size)
                return handle.read(size)

        return Reader()

    monkeypatch.setattr(type(path), "open", tracked_open)
    item = telemetry_buffer.OutboxItem(
        0,
        "http://server",
        "user",
        "codex",
        "one",
        "one",
        0,
        0,
        payload["end_byte_offsets"][0],
        True,
        payload,
        0,
        proof,
    )
    assert get_adapter("codex").validate_outbox_provenance(item)
    assert reads == [item.end_offset]


def test_held_other_session_does_not_block_clean_finalization(tmp_path):
    ensure_loaded()
    held_path, held_line = _rollout(tmp_path, "held")
    clean_path, _ = _rollout(tmp_path, "clean")
    db = tmp_path / "outbox.db"
    bad = _queued(held_path, held_line, "held")
    bad["agent_id"] = "unverified"
    telemetry_buffer.enqueue(bad, destination="http://server", user_id="user", db_path=db)
    config = {"server_url": "http://server", "user_id": "user"}
    posted = []

    def post(item, cfg):
        posted.append(item)
        return {
            "acknowledged_line": item["start_offset"] + len(item["lines"]) - 1,
            "acknowledged_offset": item.get("end_byte_offsets", [item.get("total_offset")])[-1],
        }

    source = get_adapter("codex").resolve_session_source({"session_id": "clean"}, home=tmp_path)
    assert base.drain_session_source(
        source, config, hook_event="Stop", final=True, home=tmp_path, db_path=db, post=post
    )
    assert {item["session_id"] for item in posted} == {"clean"}
    # A metadata-only finalization must also ignore the other session's hold.
    assert base.drain_session_source(
        source, config, hook_event="Stop", final=True, home=tmp_path, db_path=db, post=post
    )
    assert base.read_cursor_state(source.checkpoint_key, home=tmp_path)[2] is True
    assert {hold[3] for hold in telemetry_buffer.held_sessions(db_path=db)} == {"held"}


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
