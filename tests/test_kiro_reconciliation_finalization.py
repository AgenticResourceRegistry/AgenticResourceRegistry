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
