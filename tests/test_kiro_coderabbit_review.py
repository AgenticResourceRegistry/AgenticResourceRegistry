# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Regression controls for confirmed #2033 review findings."""

import json
from pathlib import Path

import pytest

from observal_cli import cmd_doctor, telemetry_buffer
from observal_cli.harness import SessionSource, ensure_loaded
from observal_cli.harness_specs.kiro_hooks_spec import build_kiro_hooks_file
from observal_cli.sessions import base, delivery_guard


def test_prompt_fingerprint_does_not_depend_on_locale(tmp_path, monkeypatch):
    """UTF-8 prompt bytes must remain readable under a cp1252 text default."""
    path = tmp_path / "messages.jsonl"
    line = json.dumps({"payload": {"type": "user", "content": "Á"}}, ensure_ascii=False) + "\n"
    path.write_bytes(line.encode("utf-8"))
    source = SessionSource("kiro", "session", path)
    expected = delivery_guard._prompt_fingerprint(source)
    assert expected is not None
    original_open = Path.open

    def locale_open(self, mode="r", *args, **kwargs):
        """Simulate the platform text default while leaving binary reads unchanged."""
        if self == path and "b" not in mode and "encoding" not in kwargs:
            kwargs["encoding"] = "cp1252"
        return original_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", locale_open)
    assert delivery_guard._prompt_fingerprint(source) == expected


def test_unknown_outbox_harness_does_not_block_registered_batches(tmp_path, monkeypatch):
    """A removed harness is quarantined without dropping the following valid batch."""
    db = tmp_path / "outbox.db"
    config = {"server_url": "http://peer", "user_id": "user"}
    for harness, sid in [("removed-harness", "old"), ("claude-code", "new")]:
        telemetry_buffer.enqueue(
            {"harness": harness, "session_id": sid, "start_offset": 0, "lines": ["{}"], "end_byte_offsets": [3]},
            destination=config["server_url"],
            user_id=config["user_id"],
            db_path=db,
        )
    posts, quarantines = [], []
    quarantine = telemetry_buffer.quarantine

    def record_quarantine(item, reason, **kwargs):
        """Record permanent rejection and retain the normal durable quarantine behavior."""
        quarantines.append((item.harness, reason))
        return quarantine(item, reason, **kwargs)

    monkeypatch.setattr(telemetry_buffer, "quarantine", record_quarantine)
    assert base.drain_outbox(
        config,
        db_path=db,
        home=tmp_path,
        post=lambda payload, cfg: posts.append(payload) or {"acknowledged_line": 0, "acknowledged_offset": 3},
    )
    assert [payload["session_id"] for payload in posts] == ["new"]
    assert quarantines[0][0] == "removed-harness"
    assert not telemetry_buffer.pending(destination=config["server_url"], user_id="user", db_path=db)


@pytest.mark.parametrize("engine", ["v2", "v3"])
@pytest.mark.parametrize("agents_directory", [False, True], ids=["no-agents-directory", "empty-agents-directory"])
def test_doctor_warns_for_unusable_standalone_without_agents(tmp_path, monkeypatch, engine, agents_directory):
    """A configured surface lacking complete hook coverage cannot silently look healthy."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("KIRO_HOME", raising=False)
    monkeypatch.setenv("OBSERVAL_KIRO_ENGINE", engine)
    monkeypatch.setenv("OBSERVAL_KIRO_CLI_VERSION", "2.35.0")
    monkeypatch.setenv("OBSERVAL_KIRO_IDE", "0")
    root = tmp_path / ".kiro"
    (root / "hooks").mkdir(parents=True)
    if agents_directory:
        (root / "agents").mkdir()
    data = build_kiro_hooks_file()
    if engine == "v3":
        data["hooks"] = data["hooks"][:1]
    (root / "hooks/observal.json").write_text(json.dumps(data))
    ensure_loaded()
    warnings = []
    cmd_doctor._check_kiro([], warnings)
    assert any("Kiro" in warning and "doctor patch --harness kiro" in warning for warning in warnings)


@pytest.mark.parametrize("agents_directory", [False, True])
def test_doctor_lifecycle_uses_custom_runtime_root(tmp_path, monkeypatch, agents_directory):
    """Patch, health and cleanup agree with discovery without touching the default root."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    custom = tmp_path / "relocated-kiro"
    monkeypatch.setenv("KIRO_HOME", str(custom))
    monkeypatch.setenv("OBSERVAL_KIRO_ENGINE", "v3")
    monkeypatch.setenv("OBSERVAL_KIRO_CLI_VERSION", "2.35.0")
    monkeypatch.setenv("OBSERVAL_KIRO_IDE", "0")
    monkeypatch.setattr("observal_cli.lockfile.read_registry_lockfile", lambda: ("server", {}))
    custom.mkdir()
    if agents_directory:
        (custom / "agents").mkdir()
    (custom / "hooks").mkdir()
    user = {"name": "user-kept", "trigger": "Stop", "action": {"type": "command", "command": "echo kept"}}
    config = build_kiro_hooks_file()
    config["hooks"] = [user]
    file = custom / "hooks/observal.json"
    file.write_text(json.dumps(config))
    default_file = tmp_path / ".kiro/hooks/observal.json"
    default_file.parent.mkdir(parents=True)
    default_file.write_text('{"default_kept":true}')
    original = default_file.read_bytes()
    ensure_loaded()
    assert cmd_doctor._patch_kiro(False)
    assert len(json.loads(file.read_text())["hooks"]) == 4
    warnings = []
    cmd_doctor._check_kiro([], warnings)
    assert not warnings
    assert cmd_doctor._cleanup_kiro(False)
    assert json.loads(file.read_text())["hooks"] == [user]
    assert default_file.read_bytes() == original


def assert_outbox_lock_available(db, tmp_path):
    """A separate interpreter must acquire the database lock while a send is active."""
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    script = """import sys
from pathlib import Path
from observal_cli.lockfile import _exclusive_lock
with _exclusive_lock(Path(sys.argv[1])):
    print('available', flush=True)
"""
    result = subprocess.run(
        [sys.executable, "-P", "-c", script, str(delivery_guard._outbox_lock_path(db))],
        env={**os.environ, "PYTHONPATH": str(root) + os.pathsep + str(root / "packages/observal-shared")},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    assert result.stdout.strip() == "available"


def test_network_send_does_not_hold_outbox_wide_lock(tmp_path):
    """Transport claims serialize a session, not unrelated hooks using the same database."""
    db = tmp_path / "outbox.db"
    config = {"server_url": "http://peer", "user_id": "user"}
    telemetry_buffer.enqueue(
        {
            "harness": "claude-code",
            "session_id": "session",
            "start_offset": 0,
            "lines": ["{}"],
            "end_byte_offsets": [3],
        },
        destination=config["server_url"],
        user_id=config["user_id"],
        db_path=db,
    )

    def send(payload, cfg):
        assert_outbox_lock_available(db, tmp_path)
        return {"acknowledged_line": 0, "acknowledged_offset": 3}

    assert base.drain_outbox(config, home=tmp_path, db_path=db, post=send)


def test_lifecycle_claim_does_not_hold_outbox_wide_lock(tmp_path, monkeypatch):
    """A Kiro lifecycle claim retains session authority without locking all HTTP drainage."""
    db = tmp_path / "outbox.db"
    monkeypatch.setattr(telemetry_buffer, "DB_PATH", db)
    path = tmp_path / "messages.jsonl"
    path.write_text("{}\n")
    source = SessionSource("kiro", "session", path)
    with delivery_guard.kiro_delivery_guard(
        source, {}, {"server_url": "http://peer", "user_id": "user"}, home=tmp_path
    ):
        assert_outbox_lock_available(db, tmp_path)


def test_busy_transport_claim_returns_without_resending(tmp_path):
    """A competing drain cannot send a row currently owned by another thread."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    db = tmp_path / "outbox.db"
    config = {"server_url": "http://peer", "user_id": "user"}
    telemetry_buffer.enqueue(
        {
            "harness": "claude-code",
            "session_id": "session",
            "start_offset": 0,
            "lines": ["{}"],
            "end_byte_offsets": [3],
        },
        destination=config["server_url"],
        user_id=config["user_id"],
        db_path=db,
    )
    sending, release = Event(), Event()

    def slow_send(payload, cfg):
        sending.set()
        assert release.wait(10)
        return {"acknowledged_line": 0, "acknowledged_offset": 3}

    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(base.drain_outbox, config, home=tmp_path, db_path=db, post=slow_send)
        try:
            assert sending.wait(5)
            competitor = pool.submit(
                base.drain_outbox,
                config,
                home=tmp_path,
                db_path=db,
                post=lambda *args: pytest.fail("Duplicate send"),
            )
            assert competitor.result(timeout=2) is False
        finally:
            release.set()
        assert owner.result(timeout=5)


def test_busy_other_session_keeps_kiro_stop_durable(tmp_path, monkeypatch):
    """An unrelated in-flight batch cannot stall a V3 hook or discard its Stop intent."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from observal_cli.hooks import session_push

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("KIRO_HOME", raising=False)
    db = tmp_path / "outbox.db"
    monkeypatch.setattr(telemetry_buffer, "DB_PATH", db)
    monkeypatch.setattr(base, "_maybe_upload_layer_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(session_push, "_spawn_worker", lambda *a, **k: False)
    config = {"server_url": "http://peer", "user_id": "user", "access_token": "dummy"}
    cfg = tmp_path / ".observal/config.json"
    cfg.parent.mkdir()
    cfg.write_text(json.dumps(config))
    path = tmp_path / ".kiro/sessions/workspace/session/messages.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"payload": {"type": "user", "content": "pending"}}) + "\n")
    path.with_name("session.json").write_text(json.dumps({"workspacePaths": [str(tmp_path)]}))
    telemetry_buffer.enqueue(
        {"harness": "claude-code", "session_id": "other", "start_offset": 0, "lines": ["{}"], "end_byte_offsets": [3]},
        destination=config["server_url"],
        user_id=config["user_id"],
        db_path=db,
    )
    sending, release = Event(), Event()

    def slow_send(payload, cfg):
        sending.set()
        assert release.wait(10)
        return {
            "acknowledged_line": payload["start_offset"] + len(payload["lines"]) - 1,
            "acknowledged_offset": payload.get("end_byte_offsets", [0])[-1],
        }

    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(base.drain_outbox, config, home=tmp_path, db_path=db, post=slow_send)
        try:
            assert sending.wait(5)
            hook = pool.submit(
                session_push._run_hook,
                {"session_id": "session", "hook_event_name": "Stop"},
                harness="kiro",
                home=tmp_path,
            )
            hook.result(timeout=2)
            assert list((tmp_path / ".observal/session-delivery").rglob("finalization.pending"))
            assert any(
                item.session_id == "session"
                for item in telemetry_buffer.pending(
                    destination=config["server_url"], user_id=config["user_id"], db_path=db
                )
            )
        finally:
            release.set()
        assert owner.result(timeout=5)


def test_stale_kiro_candidate_does_not_revoke_completed_cursor(tmp_path, monkeypatch):
    """Refreshing an accepted candidate must precede any final-authority side effects."""
    monkeypatch.setattr(telemetry_buffer, "DB_PATH", tmp_path / "outbox.db")
    config = {"server_url": "http://peer", "user_id": "user"}
    telemetry_buffer.enqueue(
        {
            "harness": "kiro",
            "session_id": "session",
            "start_offset": 0,
            "lines": ["{}"],
            "end_byte_offsets": [3],
            "final": True,
        },
        destination=config["server_url"],
        user_id=config["user_id"],
    )
    (item,) = telemetry_buffer.pending(destination=config["server_url"], user_id=config["user_id"])
    telemetry_buffer.accept_item(item.id)
    base.write_cursor("session", 3, 1, finalized=True, home=tmp_path)
    with delivery_guard.kiro_outbox_guard(item, config, home=tmp_path) as delivery:
        assert delivery.item is None
    assert base.read_cursor_state("session", home=tmp_path) == (3, 1, True)


@pytest.mark.parametrize("output", ["table", "json"])
def test_multi_target_patch_reports_target_errors_without_json_pollution(monkeypatch, capsys, output):
    """Human errors identify their target; machine errors keep those details structured."""
    from observal_cli.errors import CliError, ErrorCategory

    def patch(target, *args):
        """Simulate one failed adapter followed by a successful unchanged adapter."""
        if target == "kiro":
            raise CliError(ErrorCategory.VALIDATION, "Malformed hook configuration", operation="Doctor patch")
        return False

    monkeypatch.setattr(cmd_doctor, "_adapter_change", patch)
    with pytest.raises(CliError) as error:
        cmd_doctor._patch_targets(["kiro", "claude-code"], dry_run=True, output=output)
    text = capsys.readouterr().out
    if output == "table":
        assert "kiro" in text and "Malformed hook configuration" in text
    else:
        assert "Malformed hook configuration" not in text
    assert error.value.result["targets"][0]["error"] == "Malformed hook configuration"


@pytest.mark.parametrize("child", [False, True], ids=["parent", "delegated-child"])
def test_refreshed_final_candidate_uses_current_authority_source(tmp_path, monkeypatch, child):
    """An unscoped candidate upgraded by Stop must claim and validate the refreshed parent."""
    from concurrent.futures import ThreadPoolExecutor

    from observal_cli.lockfile import _exclusive_lock

    monkeypatch.setattr(telemetry_buffer, "DB_PATH", tmp_path / "outbox.db")
    parent_path = tmp_path / "messages.jsonl"
    parent_path.write_text(json.dumps({"payload": {"type": "user", "content": "pending"}}) + "\n")
    parent = SessionSource("kiro", "parent", parent_path)
    target = parent
    related = []
    if child:
        child_path = tmp_path / "child.jsonl"
        child_path.write_text("{}\n")
        target = SessionSource("kiro", "child", child_path, parent_session_id="parent")
        related = [target]
    config = {"server_url": "http://peer", "user_id": "user"}
    capture = {"harness": "kiro", "session_id": target.session_id, "start_offset": 1, "lines": [], "total_credits": 0.5}
    kwargs = {
        "destination": config["server_url"],
        "user_id": config["user_id"],
        "checkpoint_key": target.checkpoint_key,
    }
    telemetry_buffer.enqueue(capture, **kwargs)
    (candidate,) = telemetry_buffer.pending(destination=config["server_url"], user_id=config["user_id"])
    finalizable = [parent, *related]
    with delivery_guard.kiro_delivery_guard(
        parent,
        {"hook_event_name": "Stop"},
        config,
        final_requested=True,
        finalizable=finalizable,
        related=related,
        home=tmp_path,
    ):
        pass
    with delivery_guard.kiro_delivery_guard(
        parent,
        {"hook_event_name": "Reconcile"},
        config,
        final=True,
        require_final_intent=True,
        finalizable=finalizable,
        related=related,
        home=tmp_path,
    ):
        context = delivery_guard.kiro_outbox_context(target)
        telemetry_buffer.enqueue(
            {**capture, "final": True, "total_offset": target.path.stat().st_size, "total_line_count": 1},
            delivery_context=context,
            **kwargs,
        )
    assert candidate.delivery_context is None
    parent_dir = delivery_guard._session_directory(parent, config, tmp_path)

    def competing_claim():
        """Observe parent claim ownership in a different thread, avoiding same-thread reentry."""
        with _exclusive_lock(parent_dir.parent / (parent_dir.name + ".lock"), blocking=False) as acquired:
            return acquired

    with delivery_guard.kiro_outbox_guard(candidate, config, home=tmp_path) as delivery:
        assert delivery.item.final and delivery.final_allowed()
        assert (parent_dir / "finalization.pending").exists()
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(competing_claim).result(timeout=2) is False


@pytest.mark.parametrize("metadata_only", [False, True])
def test_capture_credit_update_preserves_queued_final_envelope(tmp_path, metadata_only):
    """New capture credits merge into final evidence without losing its Stop generation."""
    payload = {
        "harness": "kiro",
        "session_id": "session",
        "start_offset": 0,
        "lines": [] if metadata_only else ["{}"],
        "end_byte_offsets": [] if metadata_only else [3],
        "final": True,
        "total_offset": 3,
        "total_line_count": 1,
        "session_hash": "final-hash",
        "hashed_line_count": 1,
        "total_credits": 1,
    }
    context = {"generation": "queued-stop", "checkpoint_key": "session", "source_path": "/synthetic/messages.jsonl"}
    kwargs = {"destination": "http://peer", "user_id": "user", "db_path": tmp_path / "outbox.db"}
    row_id = telemetry_buffer.enqueue(payload, delivery_context=context, **kwargs)
    capture = {
        key: value
        for key, value in payload.items()
        if key not in {"final", "total_offset", "total_line_count", "session_hash", "hashed_line_count"}
    }
    capture["total_credits"] = 2.5
    assert telemetry_buffer.enqueue(capture, **kwargs) == row_id
    (item,) = telemetry_buffer.pending(**kwargs)
    assert item.final and item.payload["final"]
    assert item.delivery_context == context
    assert item.payload == {**payload, "total_credits": 2.5}
    assert item.end_offset == 3
