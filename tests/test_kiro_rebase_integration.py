# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Preserve upstream Kiro cleanup and model metadata across the #2033 rebase."""

import json

import pytest

from observal_cli import cmd_doctor, lockfile, telemetry_buffer
from observal_cli.harness import SessionSource, ensure_loaded, get_adapter
from observal_cli.sessions import base
from observal_shared.harness_registry import HARNESS_REGISTRY


@pytest.mark.parametrize(
    "metadata", [{"total_credits": 0.75}, {"session_model": "auto"}, {"total_credits": 0.75, "session_model": "auto"}]
)
def test_late_metadata_preserves_final_authority_and_model(tmp_path, metadata):
    """Late capture updates metadata without replacing a queued final's snapshot or authority."""
    db = tmp_path / "outbox.db"
    context = {"path": str(tmp_path / "messages.jsonl"), "generation": "stop-generation"}
    final = {
        "harness": "kiro",
        "session_id": "session",
        "start_offset": 1,
        "lines": [],
        "final": True,
        "snapshot": {"source": "final"},
        "session_model": "auto",
    }
    telemetry_buffer.enqueue(final, destination="http://peer", user_id="user", db_path=db, delivery_context=context)
    capture = {"harness": "kiro", "session_id": "session", "start_offset": 1, "lines": [], "final": False, **metadata}
    telemetry_buffer.enqueue(
        capture, destination="http://peer", user_id="user", db_path=db, delivery_context={"path": None}
    )
    [item] = telemetry_buffer.pending(destination="http://peer", user_id="user", db_path=db)
    assert item.final and item.payload["final"]
    assert item.delivery_context == context
    assert item.payload["snapshot"] == final["snapshot"]
    assert item.payload["session_model"] == "auto"
    if "total_credits" in metadata:
        assert item.payload["total_credits"] == metadata["total_credits"]


def test_session_end_coalesces_pending_model_with_fresh_credits_before_sending(tmp_path):
    """Do not spend the teardown send window on superseded model-only metadata."""
    ensure_loaded()
    path = tmp_path / "messages.jsonl"
    path.write_text("")
    db = tmp_path / "outbox.db"
    config = {"server_url": "http://peer", "user_id": "user"}
    telemetry_buffer.enqueue(
        {
            "harness": "kiro",
            "session_id": "session",
            "start_offset": 0,
            "lines": [],
            "session_model": "auto",
            "hook_event": "Stop",
        },
        destination=config["server_url"],
        user_id=config["user_id"],
        db_path=db,
    )
    posts = []

    def one_send_window(payload, _config):
        """The native teardown may interrupt a second request; keep its data durable."""
        posts.append(dict(payload))
        return {"acknowledged_line": -1, "acknowledged_offset": 0} if len(posts) == 1 else None

    assert base.drain_session_source(
        SessionSource("kiro", "session", path),
        config,
        hook_event="SessionEnd",
        extra_fields={"session_model": "auto", "total_credits": 0.125},
        home=tmp_path,
        db_path=db,
        post=one_send_window,
    )
    assert len(posts) == 1
    assert posts[0]["session_model"] == "auto" and posts[0]["total_credits"] == 0.125
    assert posts[0]["hook_event"] == "SessionEnd" and not posts[0].get("final")
    assert not telemetry_buffer.pending(destination=config["server_url"], user_id=config["user_id"], db_path=db)


def test_session_end_capture_first_keeps_queued_source_records_in_order(tmp_path):
    """Capturing fresh teardown metadata must not replace or reorder unacknowledged records."""
    ensure_loaded()
    first = json.dumps({"payload": {"type": "user", "content": "hello"}})
    tail = json.dumps({"payload": {"type": "usage_summary"}})
    path = tmp_path / "messages.jsonl"
    path.write_text(first + "\n" + tail + "\n")
    db = tmp_path / "outbox.db"
    config = {"server_url": "http://peer", "user_id": "user"}
    telemetry_buffer.enqueue(
        {
            "harness": "kiro",
            "session_id": "session",
            "start_offset": 0,
            "lines": [first],
            "end_byte_offsets": [len((first + "\n").encode())],
            "session_model": "auto",
        },
        destination=config["server_url"],
        user_id=config["user_id"],
        db_path=db,
    )
    posts = []

    def ack(payload, _config):
        """Acknowledge each durable batch at its actual source boundary."""
        posts.append(dict(payload))
        return {
            "acknowledged_line": payload["start_offset"] + len(payload["lines"]) - 1,
            "acknowledged_offset": payload["end_byte_offsets"][-1],
        }

    assert base.drain_session_source(
        SessionSource("kiro", "session", path),
        config,
        hook_event="SessionEnd",
        extra_fields={"session_model": "auto", "total_credits": 0.125},
        home=tmp_path,
        db_path=db,
        post=ack,
    )
    assert [payload["start_offset"] for payload in posts] == [0, 1]
    assert [line for payload in posts for line in payload["lines"]] == [first, tail]
    assert posts[-1]["total_credits"] == 0.125
    assert base.read_cursor_state("session", home=tmp_path) == (path.stat().st_size, 2, False)
    assert not telemetry_buffer.pending(destination=config["server_url"], user_id=config["user_id"], db_path=db)


@pytest.mark.parametrize("harness", sorted(name for name in HARNESS_REGISTRY if name != "kiro"))
def test_other_harnesses_keep_default_final_delivery_without_kiro_authority(tmp_path, harness):
    """Shared adapter contracts must not apply Kiro's Stop-only policy to other harnesses."""
    ensure_loaded()
    adapter = get_adapter(harness)
    path = tmp_path / "session.jsonl"
    path.write_text("{}\n")
    source = SessionSource(harness, "session", path)
    config = {"server_url": "http://peer", "user_id": "user"}
    db = tmp_path / "outbox.db"
    assert adapter.accepts_session_hook(source, {}, placement="inline", home=tmp_path)
    assert not adapter.reconcile_only_finalization(source)
    assert adapter.session_reconciliation_final(source, config, home=tmp_path)
    assert not adapter.session_finalization_pending(source, config, home=tmp_path)
    assert adapter.session_finalization_generation(source, config, home=tmp_path) is None
    assert adapter.session_outbox_context(source, config, home=tmp_path) is None
    with adapter.session_delivery_guard(source, {}, config, final=True, home=tmp_path) as attempt:
        assert attempt.proceed
    telemetry_buffer.enqueue(
        {
            "harness": harness,
            "session_id": "session",
            "start_offset": 0,
            "lines": ["{}"],
            "end_byte_offsets": [3],
            "final": True,
        },
        destination=config["server_url"],
        user_id=config["user_id"],
        db_path=db,
    )
    posts = []

    def ack(payload, _config):
        """Acknowledge the normal final batch without introducing a Kiro generation."""
        posts.append(dict(payload))
        return {"acknowledged_line": 0, "acknowledged_offset": 3}

    assert base.drain_outbox(config, home=tmp_path, db_path=db, post=ack)
    assert posts[0]["final"] and "delivery_context" not in posts[0]
    assert base.read_cursor_state("session", home=tmp_path) == (3, 1, True)
    assert not list((tmp_path / ".observal/session-delivery").rglob("finalization.pending"))


@pytest.mark.parametrize("harness", ["copilot", "copilot-cli", "goose"])
def test_other_hook_rewriters_accept_scope_without_changing_content(tmp_path, harness):
    """The protocol's path argument must not change non-Kiro hook localization behavior."""
    ensure_loaded()
    adapter = get_adapter(harness)
    content = {"hooks": {"sessionStart": [{"command": "python -m observal_cli.hooks.session_push"}]}}
    before = json.loads(json.dumps(content))
    plain = adapter.rewrite_hooks(json.loads(json.dumps(content)), "agent-id")
    scoped = adapter.rewrite_hooks(json.loads(json.dumps(content)), "agent-id", path=tmp_path / "hooks.json")
    assert scoped == plain and content == before


def test_cleanup_custom_root_and_locked_project_without_default_profiles(tmp_path, monkeypatch):
    """Custom-root cleanup must still visit exact locked projects when user profiles are absent."""
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("KIRO_HOME", str(runtime))
    project = tmp_path / "project"
    profile = project / ".kiro/agents/locked.json"
    profile.parent.mkdir(parents=True)
    owned = {"command": "python -m observal_cli.hooks.session_push --harness kiro"}
    foreign = {"command": "echo user-hook"}
    profile.write_text(json.dumps({"hooks": {"stop": [owned, foreign]}}))
    unrelated = profile.with_name("untracked.json")
    unrelated.write_bytes(profile.read_bytes())
    before = unrelated.read_bytes()
    registry = {
        "harnesses": {"kiro": {"agents": [{"scope": "project", "directory": str(project), "local_name": "locked"}]}}
    }
    monkeypatch.setattr(lockfile, "read_registry_lockfile", lambda: ({}, registry))
    assert cmd_doctor._cleanup_kiro(dry_run=False)
    assert json.loads(profile.read_text())["hooks"]["stop"] == [foreign]
    assert unrelated.read_bytes() == before
    assert not cmd_doctor._cleanup_kiro(dry_run=False)
