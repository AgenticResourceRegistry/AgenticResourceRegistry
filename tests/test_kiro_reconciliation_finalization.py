# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Explicit Kiro Stop intents complete outside the native hook process tree."""

import json
from pathlib import Path

import pytest

from observal_cli import cmd_reconcile_cli, telemetry_buffer
from observal_cli.harness import ensure_loaded
from observal_cli.hooks import session_push
from observal_cli.sessions import base


@pytest.fixture
def session(tmp_path, monkeypatch):
    """An acknowledged V3 source with positive initial credits and isolated identity."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / ".kiro/sessions/workspace/session/messages.jsonl"
    path.parent.mkdir(parents=True)
    records = [
        {"id": "prompt", "payload": {"type": "user", "content": "synthetic"}},
        {
            "id": "usage",
            "payload": {"type": "usage_summary", "promptTurnSummaries": [{"unit": "credit", "usage": 0.25}]},
        },
    ]
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    path.with_name("session.json").write_text(json.dumps({"workspacePaths": [str(tmp_path)]}))
    config = {"server_url": "http://server", "user_id": "user", "access_token": "dummy"}
    cfg = tmp_path / ".observal/config.json"
    cfg.parent.mkdir()
    cfg.write_text(json.dumps(config))
    monkeypatch.setattr(telemetry_buffer, "DB_PATH", tmp_path / "outbox.db")
    monkeypatch.setattr(base, "_maybe_upload_layer_snapshot", lambda *a, **k: None)
    posts, workers = [], []

    def acknowledge(_url, _token, payload, **kwargs):
        posts.append(payload)
        return {
            "acknowledged_line": payload["start_offset"] + len(payload["lines"]) - 1,
            "acknowledged_offset": payload.get("end_byte_offsets", [payload.get("total_offset", path.stat().st_size)])[
                -1
            ],
        }

    monkeypatch.setattr(base, "post_to_server_ack", acknowledge)
    monkeypatch.setattr(
        cmd_reconcile_cli, "recover_cursor_from_server", lambda source, cfg: base.read_cursor(source.checkpoint_key)
    )
    monkeypatch.setattr(session_push, "_spawn_worker", lambda *a, **k: workers.append(a) or True)
    ensure_loaded()
    return tmp_path, path, config, posts, workers


def stop(home):
    """Capture an explicit native-style Stop without pretending that its child completed."""
    session_push._run_hook(
        {"session_id": "session", "hook_event_name": "Stop"}, harness="kiro", home=home, placement="standalone"
    )


def test_v3_stop_records_intent_without_spawning_cleanup_owned_workers(session):
    """V3 records durable intent rather than scheduling a child known to be killed."""
    home, path, config, posts, workers = session
    stop(home)
    assert not workers
    assert base.read_cursor_state("session")[2] is False
    assert list((home / ".observal/session-delivery").rglob("finalization.pending"))


def test_reconcile_finalizes_late_records_credits_and_clears_intent(session):
    """A foreground reconcile after Kiro exits acknowledges final evidence and retires Stop intent."""
    home, path, config, posts, workers = session
    stop(home)
    with path.open("a") as stream:
        stream.write(
            json.dumps(
                {"id": "reply", "payload": {"type": "assistant", "operationType": "Say", "content": "late reply"}}
            )
            + "\n"
        )
        stream.write(
            json.dumps(
                {
                    "id": "late-usage",
                    "payload": {"type": "usage_summary", "promptTurnSummaries": [{"unit": "credit", "usage": 3}]},
                }
            )
            + "\n"
        )
    result = cmd_reconcile_cli._reconcile_harness("kiro", config, 24, False)
    assert result["pushed"] == 1
    assert posts[-1]["final"] and posts[-1]["total_credits"] == 3.25
    assert base.read_cursor_state("session") == (path.stat().st_size, 4, True)
    assert not list((home / ".observal/session-delivery").rglob("finalization.pending"))
    count = len(posts)
    cmd_reconcile_cli._reconcile_harness("kiro", config, 24, False)
    assert len(posts) == count


def test_reconcile_does_not_finalize_quiet_conversation_without_stop(session):
    """Quiet or permission-paused Kiro sessions are not implicitly final."""
    home, path, config, posts, workers = session
    session_push._run_hook({"session_id": "session", "hook_event_name": "UserPromptSubmit"}, harness="kiro", home=home)
    cmd_reconcile_cli._reconcile_harness("kiro", config, 24, False)
    assert not any(payload.get("final") for payload in posts)
    assert base.read_cursor_state("session")[2] is False


def test_resumed_prompt_cancels_previous_stop_intent(session):
    """A new prompt supersedes unfinished Stop work even when capture evidence is unchanged."""
    home, path, config, posts, workers = session
    stop(home)
    for _ in range(2):
        session_push._run_hook(
            {"session_id": "session", "hook_event_name": "UserPromptSubmit"}, harness="kiro", home=home
        )
    assert not list((home / ".observal/session-delivery").rglob("finalization.pending"))
    posts.clear()
    cmd_reconcile_cli._reconcile_harness("kiro", config, 24, False)
    assert not any(payload.get("final") for payload in posts)


def test_reconcile_finalizes_parent_and_child_under_one_intent(session):
    """Parent intent retires only after all eligible delegated sources acknowledge finalization."""
    home, path, config, posts, workers = session
    child = path.parent / "sub-executions/child.jsonl"
    child.parent.mkdir()
    child.write_text(
        json.dumps({"id": "child-reply", "payload": {"type": "assistant", "content": "child output"}}) + "\n"
    )
    stop(home)
    result = cmd_reconcile_cli._reconcile_harness("kiro", config, 24, False)
    assert result["discovered"] == 2
    assert base.read_cursor_state("session")[2]
    assert base.read_cursor_state("session__sub__child")[2]
    assert not list((home / ".observal/session-delivery").rglob("finalization.pending"))
    final_child = next(payload for payload in posts if payload.get("final") and payload["session_id"] == "child")
    assert final_child["parent_session_id"] == "session"


def test_prompt_written_before_hook_cancels_old_stop_authority(session):
    """Reconcile must detect a resumed source prompt even before its capture hook runs."""
    home, path, config, posts, workers = session
    stop(home)
    with path.open("a") as stream:
        stream.write(
            json.dumps({"id": "new-prompt", "payload": {"type": "user", "content": "permission paused"}}) + "\n"
        )
    posts.clear()
    cmd_reconcile_cli._reconcile_harness("kiro", config, 24, False)
    assert not any(payload.get("final") for payload in posts)
    assert base.read_cursor_state("session")[2] is False


def test_resuming_completed_session_resets_final_cursor(session):
    """A replayed prompt invalidates old final state even before new source bytes arrive."""
    home, path, config, posts, workers = session
    stop(home)
    cmd_reconcile_cli._reconcile_harness("kiro", config, 24, False)
    assert base.read_cursor_state("session")[2]
    session_push._run_hook({"session_id": "session", "hook_event_name": "UserPromptSubmit"}, harness="kiro", home=home)
    assert base.read_cursor_state("session")[2] is False


def test_pending_dry_run_neither_sends_nor_changes_intent(session, monkeypatch):
    """Preview reads Stop authority without taking a write lock or claiming completion."""
    home, path, config, posts, workers = session
    stop(home)
    directory = home / ".observal/session-delivery"
    before = {item.relative_to(directory): item.read_bytes() for item in directory.rglob("*") if item.is_file()}
    monkeypatch.setattr(
        cmd_reconcile_cli, "recover_cursor_from_server", lambda *a: pytest.fail("dry-run checkpoint fetch")
    )
    count = len(posts)
    result = cmd_reconcile_cli._reconcile_harness("kiro", config, 24, True)
    assert result["would_finalize"] == 1
    assert len(posts) == count
    assert before == {item.relative_to(directory): item.read_bytes() for item in directory.rglob("*") if item.is_file()}
    assert base.read_cursor_state("session")[2] is False


def test_rejected_child_delivery_keeps_parent_intent_retryable(session, monkeypatch):
    """A child failing to acknowledge cannot retire its parent's group intent."""
    home, path, config, posts, workers = session
    child = path.parent / "sub-executions/child.jsonl"
    child.parent.mkdir()
    child.write_text(
        json.dumps({"id": "child-reply", "payload": {"type": "assistant", "content": "child output"}}) + "\n"
    )
    stop(home)
    acknowledge = base.post_to_server_ack
    monkeypatch.setattr(
        base,
        "post_to_server_ack",
        lambda url, token, payload, **kw: (
            None if payload["session_id"] == "child" else acknowledge(url, token, payload, **kw)
        ),
    )
    result = cmd_reconcile_cli._reconcile_harness("kiro", config, 24, False)
    assert result["queued"] == 1
    assert list((home / ".observal/session-delivery").rglob("finalization.pending"))
    assert not base.read_cursor_state("session__sub__child")[2]
    monkeypatch.setattr(base, "post_to_server_ack", acknowledge)
    cmd_reconcile_cli._reconcile_harness("kiro", config, 24, False)
    assert base.read_cursor_state("session__sub__child")[2]
    assert not list((home / ".observal/session-delivery").rglob("finalization.pending"))


def test_offline_final_resume_reconnect_keeps_cursor_non_final(session, monkeypatch):
    """A pre-group outbox drain must not acknowledge superseded finalization."""
    home, path, config, posts, workers = session
    stop(home)
    acknowledge = base.post_to_server_ack
    monkeypatch.setattr(base, "post_to_server_ack", lambda *a, **k: None)
    cmd_reconcile_cli._reconcile_harness("kiro", config, 24, False)
    assert any(item.final for item in telemetry_buffer.pending(destination="http://server", user_id="user"))
    session_push._run_hook({"session_id": "session", "hook_event_name": "UserPromptSubmit"}, harness="kiro", home=home)
    assert not list((home / ".observal/session-delivery").rglob("finalization.pending"))
    assert not base.read_cursor_state("session")[2]
    posts.clear()
    monkeypatch.setattr(base, "post_to_server_ack", acknowledge)
    assert base.drain_outbox(config, home=home)
    cmd_reconcile_cli._reconcile_harness("kiro", config, 24, False)
    assert not any(payload.get("final") for payload in posts)
    assert not base.read_cursor_state("session")[2]


def test_queued_final_records_survive_source_resume_before_hook(session, monkeypatch):
    """Supersession strips final metadata but keeps every offline-captured source record."""
    home, path, config, posts, workers = session
    stop(home)
    late = json.dumps({"id": "late", "payload": {"type": "assistant", "content": "captured offline"}})
    with path.open("a") as stream:
        stream.write(late + "\n")
    acknowledge = base.post_to_server_ack
    monkeypatch.setattr(base, "post_to_server_ack", lambda *a, **k: None)
    cmd_reconcile_cli._reconcile_harness("kiro", config, 24, False)
    with path.open("a") as stream:
        stream.write(json.dumps({"id": "resume", "payload": {"type": "user", "content": "next turn"}}) + "\n")
    posts.clear()
    monkeypatch.setattr(base, "post_to_server_ack", acknowledge)
    assert base.drain_outbox(config, home=home)
    assert any(late in payload["lines"] for payload in posts)
    assert not any(payload.get("final") for payload in posts)
    assert not base.read_cursor_state("session")[2]


def test_waiting_v2_worker_cannot_finalize_resumed_session(session, monkeypatch):
    """A retained worker validates Stop authority after settling, even for its Stop event."""
    home, path, config, posts, workers = session
    v2 = home / ".kiro/sessions/cli/session.jsonl"
    v2.parent.mkdir(parents=True)
    v2.write_text('{"kind":"Prompt","data":{"content":[]}}\n')
    stop(home)
    assert workers

    def settle(_path):
        session_push._run_hook(
            {"session_id": "session", "hook_event_name": "UserPromptSubmit"}, harness="kiro", home=home
        )

    monkeypatch.setattr(session_push, "_wait_until_stable", settle)
    posts.clear()
    session_push._finalize_session("kiro", "session", "", home=home)
    assert not any(payload.get("final") for payload in posts)
    assert not base.read_cursor_state("session")[2]


def test_new_stop_does_not_reauthorize_an_old_queued_generation(session, monkeypatch):
    """Even identical prompt bytes cannot revive a canceled batch under a subsequent Stop."""
    home, path, config, posts, workers = session
    stop(home)
    acknowledge = base.post_to_server_ack
    monkeypatch.setattr(base, "post_to_server_ack", lambda *a, **k: None)
    cmd_reconcile_cli._reconcile_harness("kiro", config, 24, False)
    old = telemetry_buffer.pending(destination="http://server", user_id="user")[0].delivery_context
    session_push._run_hook({"session_id": "session", "hook_event_name": "UserPromptSubmit"}, harness="kiro", home=home)
    stop(home)
    pending = next((home / ".observal/session-delivery").rglob("finalization.pending"))
    assert json.loads(pending.read_text())["generation"] != old["generation"]
    posts.clear()
    monkeypatch.setattr(base, "post_to_server_ack", acknowledge)
    assert base.drain_outbox(config, home=home)
    assert not any(payload.get("final") for payload in posts)
    assert not base.read_cursor_state("session")[2]
    assert pending.exists(), "An old queued batch cannot cancel the newer Stop"
    cmd_reconcile_cli._reconcile_harness("kiro", config, 24, False)
    assert posts[-1]["final"]
    assert base.read_cursor_state("session")[2]


@pytest.mark.parametrize("new_records", [False, True])
def test_source_resume_during_ack_cannot_restore_final_cursor(session, monkeypatch, new_records):
    """A source prompt preceding its hook is checked again at ACK, including metadata-only final."""
    home, path, config, posts, workers = session
    stop(home)
    if new_records:
        with path.open("a") as stream:
            stream.write(json.dumps({"id": "late", "payload": {"type": "assistant", "content": "late"}}) + "\n")
    acknowledge = base.post_to_server_ack

    def resume_at_ack(url, token, payload, **kw):
        if payload.get("final"):
            with path.open("a") as stream:
                stream.write(json.dumps({"id": "resumed", "payload": {"type": "user", "content": "next"}}) + "\n")
        return acknowledge(url, token, payload, **kw)

    monkeypatch.setattr(base, "post_to_server_ack", resume_at_ack)
    result = cmd_reconcile_cli._reconcile_harness("kiro", config, 24, False)
    assert result["finalized"] == 0, "A downgraded ACK is not finalization evidence"
    assert any(payload.get("final") for payload in posts), "Request was authoritative when sent"
    assert not base.read_cursor_state("session")[2]
    assert not list((home / ".observal/session-delivery").rglob("finalization.pending"))
    import hashlib

    final_receipt = hashlib.sha256(b"final").hexdigest() + ".json"
    assert not list((home / ".observal/session-delivery").rglob(final_receipt))
    monkeypatch.setattr(base, "post_to_server_ack", acknowledge)
    stop(home)
    cmd_reconcile_cli._reconcile_harness("kiro", config, 24, False)
    assert base.read_cursor_state("session")[2], "A fresh Stop can still finalize after supersession"


def test_resume_cancels_queued_child_final_and_parent_final_cursor(session, monkeypatch):
    """Partial group ACKs cannot leave either parent or child final after resumption."""
    home, path, config, posts, workers = session
    child = path.parent / "sub-executions/child.jsonl"
    child.parent.mkdir()
    child.write_text(json.dumps({"id": "reply", "payload": {"type": "assistant", "content": "child"}}) + "\n")
    stop(home)
    acknowledge = base.post_to_server_ack
    monkeypatch.setattr(
        base,
        "post_to_server_ack",
        lambda url, token, payload, **kw: (
            None if payload["session_id"] == "child" else acknowledge(url, token, payload, **kw)
        ),
    )
    cmd_reconcile_cli._reconcile_harness("kiro", config, 24, False)
    assert base.read_cursor_state("session")[2]
    assert not base.read_cursor_state("session__sub__child")[2]
    session_push._run_hook({"session_id": "session", "hook_event_name": "UserPromptSubmit"}, harness="kiro", home=home)
    posts.clear()
    monkeypatch.setattr(base, "post_to_server_ack", acknowledge)
    assert base.drain_outbox(config, home=home)
    assert not any(payload.get("final") for payload in posts)
    assert not base.read_cursor_state("session")[2]
    assert not base.read_cursor_state("session__sub__child")[2]


def test_waiting_v2_worker_cannot_adopt_a_newer_stop(session, monkeypatch):
    """An old worker's generation remains superseded even when a new Stop exists after settling."""
    home, path, config, posts, workers = session
    v2 = home / ".kiro/sessions/cli/session.jsonl"
    v2.parent.mkdir(parents=True)
    v2.write_text('{"kind":"Prompt","data":{"content":[]}}\n')
    stop(home)

    def settle(_path):
        session_push._run_hook(
            {"session_id": "session", "hook_event_name": "UserPromptSubmit"}, harness="kiro", home=home
        )
        stop(home)

    monkeypatch.setattr(session_push, "_wait_until_stable", settle)
    posts.clear()
    session_push._finalize_session("kiro", "session", "", home=home)
    assert not any(payload.get("final") for payload in posts)
    assert not base.read_cursor_state("session")[2]
    assert list((home / ".observal/session-delivery").rglob("finalization.pending"))


def test_legacy_outbox_final_without_generation_keeps_records_but_not_finality(session):
    """Migrating an old SQLite outbox cannot invent Stop authority for its final batches."""
    import sqlite3

    home, path, config, posts, workers = session
    stop(home)
    payload = {
        "harness": "kiro",
        "session_id": "session",
        "start_offset": 2,
        "lines": ['{"id":"legacy"}'],
        "end_byte_offsets": [path.stat().st_size + 16],
        "final": True,
        "total_offset": path.stat().st_size + 16,
        "total_line_count": 3,
        "session_hash": "old",
        "hashed_line_count": 3,
    }
    telemetry_buffer.enqueue(payload, destination="http://server", user_id="user")
    with sqlite3.connect(telemetry_buffer.DB_PATH) as conn:
        conn.execute("ALTER TABLE session_outbox DROP COLUMN delivery_context")
    posts.clear()
    assert base.drain_outbox(config, home=home)
    assert posts[-1]["lines"] == payload["lines"]
    assert not posts[-1].get("final")
    assert not any(
        key in posts[-1] for key in ("session_hash", "total_line_count", "hashed_line_count", "total_offset")
    )
    assert not base.read_cursor_state("session")[2]


@pytest.mark.parametrize("gap_path", [False, True], ids=["contiguous-ack", "permanent-gap-ack"])
@pytest.mark.parametrize("resume_before_sample", [False, True], ids=["inter-check-transition", "already-superseded"])
def test_standalone_ack_uses_one_coherent_authority_sample(session, monkeypatch, gap_path, resume_before_sample):
    """Never combine final=True from one authority sample with preserve=False from another."""
    from contextlib import contextmanager
    from dataclasses import replace

    from observal_cli.harness import get_adapter

    home, path, config, posts, workers = session
    stop(home)
    with path.open("a") as stream:
        stream.write(json.dumps({"id": "late", "payload": {"type": "assistant", "content": "queued"}}) + "\n")
    monkeypatch.setattr(base, "post_to_server_ack", lambda *a, **k: None)
    cmd_reconcile_cli._reconcile_harness("kiro", config, 24, False)
    (item,) = telemetry_buffer.pending(destination="http://server", user_id="user")
    assert item.final and item.start_line == 2
    # Also verify that a rejected authority sample cannot preserve an older final flag.
    offset, lines = base.read_cursor("session")
    base.write_cursor("session", offset, lines, finalized=True)
    adapter = get_adapter("kiro")
    guard = adapter.session_outbox_guard
    samples, writes = [], []

    def append_resume():
        with path.open("a") as stream:
            stream.write(json.dumps({"id": "resumed", "payload": {"type": "user", "content": "next"}}) + "\n")

    @contextmanager
    def instrumented_guard(*args, **kwargs):
        with guard(*args, **kwargs) as delivery:

            def sample():
                if not samples and resume_before_sample:
                    append_resume()
                allowed = delivery.final_allowed()
                samples.append(allowed)
                if len(samples) == 1 and not resume_before_sample:
                    # A second read sees False: the original code wrote (True, False).
                    # One sampled decision does not promise atomicity with transcript writers.
                    append_resume()
                return allowed

            yield replace(delivery, final_allowed=sample)

    write = base.write_cursor

    def capture_write(*args, **kwargs):
        if "finalized" in kwargs:
            writes.append((kwargs["finalized"], kwargs["preserve_finalized"]))
        return write(*args, **kwargs)

    monkeypatch.setattr(adapter, "session_outbox_guard", instrumented_guard)
    monkeypatch.setattr(base, "write_cursor", capture_write)
    acknowledgement = {
        "acknowledged_line": 0 if gap_path else item.end_line,
        "acknowledged_offset": item.end_offset,
        "ingested": len(item.payload["lines"]),
    }
    # Standalone/pre-group drain, deliberately without a lifecycle guard to repair the flag.
    assert base.drain_outbox(config, home=home, post=lambda *a: acknowledgement)
    assert len(writes) == 1
    assert writes[0][0] == writes[0][1], "Contradictory finality arguments restored a superseded final cursor"
    assert samples == [not resume_before_sample]
    assert base.read_cursor_state("session") == (item.end_offset, item.end_line + 1, samples[0])
    if resume_before_sample:
        assert not list((home / ".observal/session-delivery").rglob("finalization.pending"))
    assert not telemetry_buffer.pending(destination="http://server", user_id="user")
