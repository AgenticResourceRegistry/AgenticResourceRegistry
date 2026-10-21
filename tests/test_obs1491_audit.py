# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Isolated reproductions of attribution, proof, acknowledgement, and hold review."""

import json

import pytest
from typer.testing import CliRunner

from observal_cli import telemetry_buffer as outbox
from observal_cli.delegation import tasks
from observal_cli.harness import ensure_loaded, get_adapter
from observal_cli.sessions import base


def rollout(tmp_path, session_id, lines):
    root = tmp_path / ".codex" / "sessions"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{session_id}.jsonl"
    path.write_text("".join(line + "\n" for line in lines))
    return path


def payload(session_id, lines, agent_id=None, version=None):
    return {
        "harness": "codex",
        "session_id": session_id,
        "agent_id": agent_id,
        "agent_version": version,
        "lines": lines,
        "start_offset": 0,
        "end_byte_offsets": [sum(len(line.encode()) + 1 for line in lines)],
    }


def test_workspace_delegation_never_claims_unindexed_codex_sibling(tmp_path, monkeypatch):
    ensure_loaded()
    monkeypatch.setattr(tasks, "STORE_DIR", tmp_path / "tasks")
    task = tasks.new_task(message="child", observal={"agentId": "child", "version": "1"})
    workspace = tmp_path / "observal-task-child"
    tasks.record_workspace(task, workspace)
    tasks.record_child_session(task, "actual")
    tasks.save(task)
    for session in ("actual", "sibling"):
        path = rollout(tmp_path, session, [json.dumps({"type": "session_meta", "payload": {"cwd": str(workspace)}})])
        agent = base._resolve_agent(str(workspace), [], path, harness="codex", session_ids=(session,))
        assert agent == (("child", "1") if session == "actual" else (None, None))


def test_forged_codex_proof_requires_actual_source_and_session_declaration(tmp_path, monkeypatch):
    ensure_loaded()
    monkeypatch.setattr(tasks, "STORE_DIR", tmp_path / "tasks")
    adapter = get_adapter("codex")
    line = json.dumps({"type": "session_meta", "payload": {"cwd": "/child", "id": "actual"}})
    source_path = rollout(tmp_path, "actual", [line])
    source = adapter.resolve_session_source({"session_id": "actual"}, home=tmp_path)
    task = tasks.new_task(message="child", observal={"agentId": "child", "version": "1"})
    tasks.record_child_session(task, "actual")
    tasks.save(task)
    body = payload("actual", [line], "child", "1")
    proof = adapter.outbox_provenance(source, body)
    item = outbox.OutboxItem(
        1, "http://server", "user", "codex", "actual", "actual", 0, 0, source_path.stat().st_size, False, body, 0, proof
    )
    assert adapter.validate_outbox_provenance(item)
    fake = dict(proof, path=str(source_path.parent / "missing" / "actual.jsonl"))
    assert not adapter.validate_outbox_provenance(outbox.OutboxItem(**{**vars(item), "provenance": fake}))
    with pytest.raises(outbox.OutboxConflictError):
        outbox.enqueue(
            body, destination="http://server", user_id="user", db_path=tmp_path / "queue.db", provenance=fake
        )
    source_path.write_text(line.replace('"actual"', '"other"') + "\n")
    assert not adapter.validate_outbox_provenance(item)
    source_path.write_text(line + "\n")
    tasks.record_child_session(task, "other")
    tasks.save(task)
    assert not adapter.validate_outbox_provenance(item)


def test_high_ack_does_not_delete_unposted_or_concurrently_replaced_rows(tmp_path):
    db = tmp_path / "queue.db"
    first = {"harness": "claude-code", "session_id": "one", "start_offset": 0, "lines": ["a"], "end_byte_offsets": [2]}
    second = dict(first, start_offset=1, lines=["b"], end_byte_offsets=[4])
    for row in (first, second):
        outbox.enqueue(row, destination="http://server", user_id="user", db_path=db)
    posted, other = outbox.pending(destination="http://server", user_id="user", db_path=db)
    assert (
        outbox.acknowledge(
            destination="http://server",
            user_id="user",
            harness="claude-code",
            session_id="one",
            acknowledged_line=99,
            db_path=db,
            expected_item=posted,
        )
        == 1
    )
    assert [row.id for row in outbox.pending(destination="http://server", user_id="user", db_path=db)] == [other.id]
    outbox.enqueue(dict(second, final=True), destination="http://server", user_id="user", db_path=db)
    assert not outbox.acknowledge(
        destination="http://server",
        user_id="user",
        harness="claude-code",
        session_id="one",
        acknowledged_line=99,
        db_path=db,
        expected_item=other,
    )
    assert outbox.pending(destination="http://server", user_id="user", db_path=db)[0].final


def test_high_server_ack_holds_unverified_later_codex_row(tmp_path):
    db = tmp_path / "queue.db"
    first_line = json.dumps({"type": "event_msg", "payload": {"message": "first"}})
    second_line = json.dumps({"type": "event_msg", "payload": {"message": "second"}})
    source_path = rollout(tmp_path, "one", [first_line, second_line])
    first = payload("one", [first_line])
    second = {
        **payload("one", [second_line], "forged", "1"),
        "start_offset": 1,
        "end_byte_offsets": [source_path.stat().st_size],
    }
    for row in (first, second):
        outbox.enqueue(row, destination="http://server", user_id="user", db_path=db)
    posted = []

    def post(body, _cfg):
        posted.append(body)
        return {"acknowledged_line": 999, "acknowledged_offset": 99999}

    assert not base.drain_outbox(
        {"server_url": "http://server", "user_id": "user"}, home=tmp_path, db_path=db, post=post
    )
    assert len(posted) == 1
    assert outbox.stats(db_path=db)["pending"] == 1
    assert outbox.held_sessions(db_path=db)[0][3] == "one"


def test_cli_lists_and_reviews_only_verified_held_session(tmp_path, monkeypatch):
    from observal_cli import cmd_reconcile_cli

    ensure_loaded()
    db = tmp_path / "queue.db"
    monkeypatch.setattr(outbox, "DB_PATH", db)
    monkeypatch.setattr(cmd_reconcile_cli, "load_config", lambda: {"server_url": "http://server", "user_id": "user"})
    adapter = get_adapter("codex")
    line = json.dumps({"type": "event_msg", "payload": {"type": "user_message"}})
    path = rollout(tmp_path, "held", [line])
    monkeypatch.setattr(
        adapter,
        "resolve_session_source",
        lambda event: adapter._source(path, path.parent) if event.get("session_id") == "held" else None,
    )
    body = payload("held", [line], "wrong", "1")
    outbox.enqueue(body, destination="http://server", user_id="user", db_path=db)
    item = outbox.pending(destination="http://server", user_id="user", db_path=db)[0]
    assert outbox.hold_if_current(item, "unverified", db_path=db)
    from observal_cli.main import app

    runner = CliRunner()
    listed = runner.invoke(app, ["reconcile", "--list-held", "--output", "json"])
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.output)["held_sessions"][0]["session_id"] == "held"
    path.unlink()
    refused = runner.invoke(app, ["reconcile", "--harness", "codex", "--review-unattributed", "held"])
    assert refused.exit_code != 0 and outbox.is_held("http://server", "user", "codex", "held", db_path=db)
    rollout(tmp_path, "held", [line])
    reviewed = runner.invoke(app, ["reconcile", "--harness", "codex", "--review-unattributed", "held"])
    assert reviewed.exit_code == 0, reviewed.output
    assert not outbox.held_sessions(db_path=db)
    assert outbox.pending(destination="http://server", user_id="user", db_path=db)[0].payload["agent_id"] is None
