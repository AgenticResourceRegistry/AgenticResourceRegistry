# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Claude Code bridge detaches gated updates without injecting model context."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from observal_cli import auto_update_policy, installed_updates, startup_update_apply, startup_update_check
from observal_cli.hooks import claude_updates as bridge

REGISTRY = "https://registry.example"


@pytest.fixture()
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    notices = tmp_path / "notices"
    notices.mkdir(mode=0o700)
    monkeypatch.setattr(startup_update_check, "NOTICE_DIR", notices)
    monkeypatch.setattr(bridge, "STARTED_DIR", tmp_path / "started")
    monkeypatch.setattr(startup_update_apply, "SHUTDOWN_DIR", tmp_path / "shutdown")
    monkeypatch.setattr(auto_update_policy, "active_registry", lambda: REGISTRY)
    monkeypatch.setattr(auto_update_policy, "active_account", lambda: "alice")
    monkeypatch.setattr(auto_update_policy, "policy_status", lambda _registry: {"effective": False})
    for key in (
        "OBSERVAL_ACCESS_TOKEN",
        "OBSERVAL_API_KEY",
        "OBSERVAL_TOKEN",
        "OBSERVAL_SERVER_URL",
        "CLAUDE_CODE_REMOTE",
    ):
        monkeypatch.delenv(key, raising=False)
        monkeypatch.delenv(f"{key}_FILE", raising=False)
    return tmp_path


def test_session_start_only_spawns_detached_check_and_returns_immediately(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    popen = MagicMock(return_value=SimpleNamespace(pid=42))
    monkeypatch.setattr(bridge.subprocess, "Popen", popen)
    event = {"hook_event_name": "SessionStart", "session_id": "s-1", "cwd": str(isolated)}
    assert bridge.handle(event) is None
    assert bridge.handle(event) is None  # resume/compact of the same session is deduped
    popen.assert_called_once()
    args, kwargs = popen.call_args
    assert args[0] == [
        bridge.sys.executable,
        "-m",
        "observal_cli",
        "_startup-apply-claude",
        "--cwd",
        str(isolated),
        "--session-id",
        "s-1",
        "--notice-key",
        bridge.notice_key(REGISTRY, "alice", "s-1"),
    ]
    assert kwargs["stdin"] == kwargs["stdout"] == kwargs["stderr"] == bridge.subprocess.DEVNULL
    assert kwargs["start_new_session"] is True
    assert len(list(bridge.STARTED_DIR.iterdir())) == 1
    bridge.handle({"hook_event_name": "SessionEnd", "session_id": "s-1"})
    assert startup_update_apply.shutdown_marker(bridge.notice_key(REGISTRY, "alice", "s-1")).exists()


def test_completed_notice_is_user_visible_and_cannot_consume_pi_or_pending(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = bridge.notice_key(REGISTRY, "alice", "s-1")
    notice = startup_update_check.NOTICE_DIR / f"{key}.json"
    startup_update_check._write_json(
        notice,
        {
            "schema": 1,
            "registry": REGISTRY,
            "account_id": "alice",
            "session_id": "s-1",
            "harness": "claude-code",
            "effective_in_current_session": "unknown",
            "items": [
                {
                    "status": "available",
                    "name": "alice/review\u001b[31m",
                    "current_version": "1.0",
                    "latest_version": "2.0",
                }
            ],
        },
        startup_update_check.MAX_NOTICE_BYTES,
    )
    pi = startup_update_check.NOTICE_DIR / ("a" * 64 + ".json")
    startup_update_check._write_json(pi, {"schema": 1, "harness": "pi", "items": []}, 1000)
    (startup_update_check.NOTICE_DIR / f"{key}.pending").write_text("not a result")
    output = bridge.handle({"hook_event_name": "UserPromptSubmit", "session_id": "s-1"})
    assert output and "alice/review" in output["systemMessage"]
    assert "\u001b" not in output["systemMessage"] and "do not confirm which profile" in output["systemMessage"]
    assert "additionalContext" not in output
    assert not notice.exists() and pi.exists()
    assert (startup_update_check.NOTICE_DIR / f"{key}.pending").exists()
    assert bridge.handle({"hook_event_name": "UserPromptSubmit", "session_id": "s-1"}) is None


def test_updated_notice_requires_durable_completion_seal(isolated: Path) -> None:
    key = bridge.notice_key(REGISTRY, "alice", "s-1")
    notice = startup_update_check.NOTICE_DIR / f"{key}.json"
    result = {
        "schema": 1,
        "registry": REGISTRY,
        "account_id": "alice",
        "session_id": "s-1",
        "harness": "claude-code",
        "journaled": True,
        "outcome_final": True,
        "effective_in_current_session": "no",
        "items": [{"status": "updated", "name": "alice/review", "current_version": "1.0", "latest_version": "2.0"}],
    }
    startup_update_check._write_json(notice, result, startup_update_check.MAX_NOTICE_BYTES)
    # A prior session's saved update can be active already in this new session
    # if the agent was selected at launch. Never claim it is inactive here.
    event = {"hook_event_name": "UserPromptSubmit", "session_id": "next-session"}
    assert bridge.handle(event) is None and notice.exists()
    startup_update_check._write_json(
        notice.with_suffix(".complete"),
        {"schema": 1, "state": "complete", "registry": REGISTRY, "account_id": "alice", "session_id": "s-1"},
        startup_update_check.MAX_NOTICE_BYTES,
    )
    output = bridge.handle(event)
    assert output and "installed on disk" in output["systemMessage"]
    assert "do not confirm which profile" in output["systemMessage"]
    assert "not active in this session" not in output["systemMessage"]
    assert "additionalContext" not in output
    assert not notice.exists() and not notice.with_suffix(".complete").exists()


def test_journaled_notice_without_final_flag_never_touches_another_seal(isolated: Path) -> None:
    """A malformed journaled record must not reuse an earlier notice's seal or crash the hook."""

    def write(session: str, mtime: int, **extra: object) -> Path:
        key = bridge.notice_key(REGISTRY, "alice", session)
        notice = startup_update_check.NOTICE_DIR / f"{key}.json"
        record = {
            "schema": 1,
            "registry": REGISTRY,
            "account_id": "alice",
            "session_id": session,
            "harness": "claude-code",
            "journaled": True,
            "effective_in_current_session": "no",
            "items": [{"status": "updated", "name": "alice/review", "current_version": "1", "latest_version": "2"}],
            **extra,
        }
        startup_update_check._write_json(notice, record, startup_update_check.MAX_NOTICE_BYTES)
        os.utime(notice, (mtime, mtime))
        return notice

    import time as _time

    now = int(_time.time())
    sealed = write("s-1", now - 20, outcome_final=True)
    seal = sealed.with_suffix(".complete")
    startup_update_check._write_json(
        seal,
        {"schema": 1, "state": "complete", "registry": REGISTRY, "account_id": "alice", "session_id": "s-1"},
        startup_update_check.MAX_NOTICE_BYTES,
    )
    malformed = write("s-2", now - 10)  # journaled, but no outcome_final
    ack: list[Path] = []
    output = bridge.handle({"hook_event_name": "UserPromptSubmit", "session_id": "s-3"}, ack=ack)
    assert output and "installed on disk" in output["systemMessage"]
    assert sorted(ack) == sorted([sealed, seal])  # only s-1's own pair is acknowledged
    assert malformed.exists()


def test_identity_permissions_env_overrides_and_foreign_results_fail_closed(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    popen = MagicMock()
    monkeypatch.setattr(bridge.subprocess, "Popen", popen)
    event = {"hook_event_name": "SessionStart", "session_id": "s-1", "cwd": str(isolated)}
    for invalid in (None, {}, {**event, "session_id": ""}, {**event, "session_id": "x" * 257}):
        assert bridge.handle(invalid) is None
    monkeypatch.setenv("OBSERVAL_ACCESS_TOKEN", "foreign")
    assert bridge.handle(event) is None
    monkeypatch.delenv("OBSERVAL_ACCESS_TOKEN")
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    assert bridge.handle(event) is None
    monkeypatch.delenv("CLAUDE_CODE_REMOTE")
    key = bridge.notice_key(REGISTRY, "alice", "s-1")
    result = startup_update_check.NOTICE_DIR / f"{key}.json"
    startup_update_check._write_json(result, {"schema": 1, "harness": "claude-code", "items": []}, 1000)
    result.chmod(0o644)
    assert bridge.handle({"hook_event_name": "UserPromptSubmit", "session_id": "s-1"}) is None
    assert result.exists()
    result.chmod(0o600)
    result.unlink()
    result.symlink_to(startup_update_check.NOTICE_DIR / "other.json")
    assert bridge.handle({"hook_event_name": "UserPromptSubmit", "session_id": "s-1"}) is None
    assert result.is_symlink()
    popen.assert_not_called()


@pytest.mark.parametrize("unfrozen", [False, True])
def test_frozen_worker_skips_apply_and_unfrozen_delegates_guarded_preflight(
    isolated: Path, monkeypatch: pytest.MonkeyPatch, unfrozen: bool
) -> None:
    from observal_cli import cmd_update

    installer = MagicMock(return_value={"status": "skipped", "reason": "unknown pin intent"})
    monkeypatch.setattr(cmd_update, "apply_startup_pi_agent", installer)
    monkeypatch.setattr(auto_update_policy, "policy_status", lambda _registry: {"effective": unfrozen})
    entry = {
        "id": "agent-id",
        "type": "agent",
        "scope": "user",
        "harness": "claude-code",
        "directory": str(isolated),
        "current_version": "1.0.0",
        "qualified_name": "alice/review",
    }
    seen = []
    monkeypatch.setattr(
        installed_updates, "inventory_for_context", lambda harness, cwd: seen.append((harness, cwd)) or [entry]
    )
    finding = {
        **entry,
        "outdated": True,
        "latest_version": "2.0.0",
        "release_verified": True,
        "release": {"description": "Author notes"},
    }
    monkeypatch.setattr(startup_update_check, "_cached_or_compare", lambda *_args: [finding])
    monkeypatch.setattr(installed_updates, "compare", lambda *_args, **_kwargs: [finding])
    monkeypatch.setattr(startup_update_check, "CACHE_DIR", isolated / "cache")
    key = bridge.notice_key(REGISTRY, "alice", "s-1")
    startup_update_apply.apply_claude(str(isolated), "s-1", key)
    payload = json.loads((startup_update_check.NOTICE_DIR / f"{key}.json").read_text())
    assert seen == [("claude-code", str(isolated))]
    assert payload["harness"] == "claude-code"
    assert payload["items"][0]["status"] == ("skipped" if unfrozen else "available")
    assert "unknown" in payload["items"][0]["reason"] if unfrozen else "frozen" in payload["items"][0]["reason"]
    assert "--harness claude-code" in payload["items"][0]["manual_command"]
    assert payload["effective_in_current_session"] == "no"
    with pytest.raises(ValueError, match="notice key"):
        startup_update_apply.apply_claude(str(isolated), "s-1", "a" * 64)
    assert not (startup_update_check.NOTICE_DIR / ("a" * 64 + ".json")).exists()
    if unfrozen:
        installer.assert_called_once()
        assert installer.call_args.kwargs["harness"] == "claude-code"
    else:
        installer.assert_not_called()


@pytest.mark.skipif(os.getenv("OBSERVAL_RUN_LIVE_CLAUDE") != "1", reason="explicit live Claude Code opt-in")
def test_disposable_claude_code_accepts_system_message(tmp_path: Path) -> None:
    """Real host hook protocol with fake credentials; not a visible authenticated TUI test."""
    import shutil
    import subprocess
    import sys
    import time

    if not shutil.which("claude"):
        pytest.skip("Claude Code CLI is not installed")
    home = tmp_path / "home"
    home.mkdir()
    config = home / ".observal" / "config.json"
    config.parent.mkdir(mode=0o700)
    config.write_text(json.dumps({"server_url": "http://127.0.0.1:9", "user_id": "alice", "access_token": "fake"}))
    repo = Path(__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "CLAUDE_", "OBSERVAL_"))}
    env.update(
        {
            "HOME": str(home),
            "CLAUDE_CONFIG_DIR": str(home / ".claude"),
            "ANTHROPIC_API_KEY": "invalid-test-key",
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:9",
            "PYTHONPATH": os.pathsep.join([str(repo), str(repo / "packages/observal-shared")]),
        }
    )
    patch = subprocess.run(
        [
            str(Path(sys.executable).parent / "observal"),
            "doctor",
            "patch",
            "--harness",
            "claude-code",
            "--output",
            "json",
        ],
        cwd=home,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert patch.returncode == 0, patch.stdout + patch.stderr
    # A synthetic, sealed prior success must survive until the host receives
    # the SessionStart hook response. This does not authenticate the host.
    prior = "earlier-session"
    key = bridge.notice_key("http://127.0.0.1:9", "alice", prior)
    notice_dir = home / ".observal/update-notices"
    notice_dir.mkdir(mode=0o700)
    notice = notice_dir / f"{key}.json"
    notice.write_text(
        json.dumps(
            {
                "schema": 1,
                "registry": "http://127.0.0.1:9",
                "account_id": "alice",
                "session_id": prior,
                "harness": "claude-code",
                "journaled": True,
                "outcome_final": True,
                "effective_in_current_session": "no",
                "items": [
                    {"status": "updated", "name": "alice/reviewer", "current_version": "1.0", "latest_version": "2.0"}
                ],
            }
        )
    )
    notice.chmod(0o600)
    seal = notice.with_suffix(".complete")
    seal.write_text(
        json.dumps(
            {
                "schema": 1,
                "state": "complete",
                "registry": "http://127.0.0.1:9",
                "account_id": "alice",
                "session_id": prior,
            }
        )
    )
    seal.chmod(0o600)
    events = home / "host-events.jsonl"
    output = events.open("w")
    proc = subprocess.Popen(
        [
            "claude",
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-hook-events",
            "--max-budget-usd",
            "0.000001",
            "hello",
        ],
        cwd=home,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=output,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    output.close()
    try:
        started = home / ".observal/claude-update-started"
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if started.exists() and list(started.glob("*")):
                break
            time.sleep(0.1)
        else:
            pytest.fail("Installed Claude Code SessionStart hook did not launch a worker")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not list((home / ".observal/update-notices").glob("*.json")):
            time.sleep(0.1)
        deadline = time.monotonic() + 10
        responses = []
        while time.monotonic() < deadline:
            responses = [
                record
                for line in events.read_text().splitlines()
                if line.startswith("{")
                for record in [json.loads(line)]
                if record.get("subtype") == "hook_response" and record.get("hook_event") == "SessionStart"
            ]
            if any("Observal Claude Code update result" in response.get("output", "") for response in responses):
                break
            time.sleep(0.1)
        else:
            pytest.fail("Claude Code did not accept the user-facing SessionStart systemMessage")
        response = next(
            response for response in responses if "Observal Claude Code update result" in response["output"]
        )
        delivered = json.loads(response["output"])
        assert "alice/reviewer" in delivered["systemMessage"]
        assert "installed on disk" in delivered["systemMessage"]
        assert "additionalContext" not in delivered
        assert not notice.exists() and not seal.exists()  # acknowledged after hook stdout was flushed
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not list(notice_dir.glob("*.json")):
            time.sleep(0.1)
        notices = list(notice_dir.glob("*.json"))
        assert len(notices) == 1
        assert json.loads(notices[0].read_text())["harness"] == "claude-code"
    finally:
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
