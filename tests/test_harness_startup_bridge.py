# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Codex, Kiro and Cursor session hooks for the startup updater, and their notice channels."""

from __future__ import annotations

import io
import json
import os
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from observal_cli import auto_update_policy, startup_update_apply, startup_update_check
from observal_cli.harness_specs import startup_update_hooks
from observal_cli.hooks import claude_updates as bridge
from observal_cli.hooks import harness_updates

if TYPE_CHECKING:
    from pathlib import Path

REGISTRY = "https://registry.example"

# Payload shapes captured from real sessions (Codex 0.162.1 exec, Kiro CLI 2.31.1
# with --agent-engine v3) and the Cursor hooks reference.
EVENTS = {
    "codex": {
        "start": {"hook_event_name": "SessionStart", "session_id": "01a1-codex", "cwd": "{cwd}", "source": "startup"},
        "end": {"hook_event_name": "SessionEnd", "session_id": "01a1-codex", "cwd": "{cwd}", "reason": "other"},
    },
    "kiro": {
        "start": {"hook_event_name": "SessionStart", "session_id": "sess_kiro", "cwd": "{cwd}"},
        "end": None,  # Kiro has no session-end event; the admission window bounds the worker.
    },
    "cursor": {
        "start": {"hook_event_name": "sessionStart", "session_id": "cur-1", "workspace_roots": ["{cwd}"]},
        "end": {"hook_event_name": "sessionEnd", "session_id": "cur-1", "reason": "user_close"},
    },
}


def _fill(event: dict, cwd: Path) -> dict:
    return json.loads(json.dumps(event).replace("{cwd}", str(cwd)))


@pytest.fixture()
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    notices = tmp_path / "notices"
    notices.mkdir(mode=0o700)
    monkeypatch.setattr(startup_update_check, "NOTICE_DIR", notices)
    monkeypatch.setattr(bridge, "STARTED_DIR", tmp_path / "started")
    monkeypatch.setattr(startup_update_apply, "SHUTDOWN_DIR", tmp_path / "shutdown")
    monkeypatch.setattr(auto_update_policy, "active_registry", lambda: REGISTRY)
    monkeypatch.setattr(auto_update_policy, "active_account", lambda: "alice")
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


def _sealed_notice(harness: str, session: str) -> Path:
    key = bridge.notice_key(REGISTRY, "alice", session, harness=harness)
    path = startup_update_check.NOTICE_DIR / f"{key}.json"
    startup_update_check._write_json(
        path,
        {
            "schema": 1,
            "registry": REGISTRY,
            "account_id": "alice",
            "session_id": session,
            "harness": harness,
            "effective_in_current_session": "no",
            "items": [{"status": "available", "name": "alice/reviewer", "current_version": "1", "latest_version": "2"}],
        },
        startup_update_check.MAX_NOTICE_BYTES,
    )
    return path


@pytest.mark.parametrize("harness", ["codex", "kiro", "cursor"])
def test_session_start_launches_the_hook_worker_once(
    isolated: Path, monkeypatch: pytest.MonkeyPatch, harness: str
) -> None:
    popen = MagicMock(return_value=SimpleNamespace(pid=7))
    monkeypatch.setattr(bridge.subprocess, "Popen", popen)
    event = _fill(EVENTS[harness]["start"], isolated)
    bridge.handle(event, harness=harness)
    bridge.handle(event, harness=harness)  # resume/compact of the same session is deduped
    popen.assert_called_once()
    argv = popen.call_args.args[0]
    session = event.get("session_id")
    assert argv[3:6] == ["_startup-apply-hook", "--harness", harness]
    assert argv[argv.index("--cwd") + 1] == str(isolated.resolve())
    assert argv[argv.index("--notice-key") + 1] == bridge.notice_key(REGISTRY, "alice", session, harness=harness)
    # The key binds the harness: a Claude key for the same session is different.
    assert bridge.notice_key(REGISTRY, "alice", session, harness=harness) != bridge.notice_key(
        REGISTRY, "alice", session
    )


@pytest.mark.parametrize("harness", ["codex", "cursor"])
def test_session_end_blocks_new_admission(isolated: Path, harness: str) -> None:
    event = _fill(EVENTS[harness]["end"], isolated)
    assert bridge.handle(event, harness=harness) is None
    key = bridge.notice_key(REGISTRY, "alice", event["session_id"], harness=harness)
    assert startup_update_apply.shutdown_marker(key).exists()


def test_cursor_background_agents_are_ignored(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    popen = MagicMock()
    monkeypatch.setattr(bridge.subprocess, "Popen", popen)
    event = {**_fill(EVENTS["cursor"]["start"], isolated), "is_background_agent": True}
    assert bridge.handle(event, harness="cursor") is None
    popen.assert_not_called()


def test_codex_shows_results_as_a_user_message_not_model_context(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bridge.subprocess, "Popen", MagicMock())
    notice = _sealed_notice("codex", "earlier")
    output = bridge.handle(_fill(EVENTS["codex"]["start"], isolated), harness="codex")
    assert output and set(output) == {"systemMessage"}
    assert "Observal Codex update result" in output["systemMessage"] and "alice/reviewer" in output["systemMessage"]
    assert not notice.exists()


@pytest.mark.parametrize("harness", ["kiro", "cursor"])
def test_kiro_and_cursor_hooks_print_nothing_and_leave_notices_for_the_cli(
    isolated: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, harness: str
) -> None:
    """Their hook stdout reaches the model; registry text must never go there."""
    monkeypatch.setattr(bridge.subprocess, "Popen", MagicMock())
    notice = _sealed_notice(harness, "earlier")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_fill(EVENTS[harness]["start"], isolated))))
    harness_updates.main(["--harness", harness])
    assert capsys.readouterr().out == ""
    assert notice.exists()


def test_other_hosts_notices_are_never_consumed(isolated: Path) -> None:
    claude = _sealed_notice("claude-code", "s")
    kiro = _sealed_notice("kiro", "s")
    assert bridge._notices(REGISTRY, "alice", harness="codex") == []
    assert claude.exists() and kiro.exists()


def test_cli_shows_kiro_and_cursor_notices_on_an_interactive_terminal_only(
    isolated: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from observal_cli import main as cli_main

    kiro, cursor, codex = _sealed_notice("kiro", "a"), _sealed_notice("cursor", "b"), _sealed_notice("codex", "c")
    monkeypatch.setattr(cli_main.sys, "argv", ["observal", "agent", "list", "--output", "json"])
    monkeypatch.setattr(cli_main.sys.stderr, "isatty", lambda: True, raising=False)
    cli_main._show_startup_update_notices()
    assert capsys.readouterr().err == "" and kiro.exists()  # never on JSON output
    monkeypatch.setattr(cli_main.sys, "argv", ["observal", "agent", "list"])
    monkeypatch.setattr(cli_main.sys.stderr, "isatty", lambda: False, raising=False)
    cli_main._show_startup_update_notices()
    assert capsys.readouterr().err == "" and kiro.exists()  # never when not a terminal
    monkeypatch.setattr(cli_main.sys.stderr, "isatty", lambda: True, raising=False)
    cli_main._show_startup_update_notices()
    err = capsys.readouterr().err
    assert "Observal Kiro update result" in err and "Observal Cursor update result" in err
    assert not kiro.exists() and not cursor.exists()
    assert codex.exists(), "Codex has its own hook channel"


def test_unknown_harness_argument_does_nothing(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    harness_updates.main(["--harness", "claude-code"])
    harness_updates.main([])
    assert capsys.readouterr().out == ""


# ── doctor patch installation ─────────────────────────────────


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    for directory in (".codex", ".cursor", ".kiro"):
        (tmp_path / directory).mkdir()
    return tmp_path


def test_codex_hooks_are_added_beside_user_and_telemetry_hooks(home: Path) -> None:
    hooks = home / ".codex/hooks.json"
    user = {"matcher": "startup", "hooks": [{"type": "command", "command": "/usr/bin/mine"}]}
    telemetry = {
        "matcher": "",
        "hooks": [{"type": "command", "command": "py -m observal_cli.hooks.session_push --harness codex"}],
    }
    hooks.write_text(json.dumps({"hooks": {"SessionStart": [user], "Stop": [telemetry]}}))
    assert startup_update_hooks.patch("codex", dry_run=False) is True
    data = json.loads(hooks.read_text())
    assert data["hooks"]["Stop"] == [telemetry]
    assert data["hooks"]["SessionStart"][0] == user
    assert data["hooks"]["SessionStart"][1]["matcher"] == "startup|resume"
    assert "harness_updates --harness codex" in data["hooks"]["SessionEnd"][0]["hooks"][0]["command"]
    assert startup_update_hooks.patch("codex", dry_run=False) is False  # idempotent
    assert startup_update_hooks.cleanup("codex", dry_run=False) is True
    assert json.loads(hooks.read_text())["hooks"] == {"SessionStart": [user], "Stop": [telemetry]}


def test_cursor_hooks_survive_an_agent_pull_merge(home: Path) -> None:
    from observal_cli.cmd_pull import _render_file

    hooks = home / ".cursor/hooks.json"
    hooks.write_text(json.dumps({"version": 1, "hooks": {}}))
    startup_update_hooks.patch("cursor", dry_run=False)
    # An agent pull merges only these two events (services/harness/helpers._cursor_hooks_config).
    pulled = {"version": 1, "hooks": {"beforeSubmitPrompt": [{"command": "x"}], "stop": [{"command": "x"}]}}
    merged = json.loads(_render_file(hooks, pulled, merge_mcp=True)[0])
    assert "harness_updates --harness cursor" in merged["hooks"]["sessionStart"][0]["command"]
    assert merged["hooks"]["sessionEnd"]


def test_kiro_hooks_get_their_own_v1_file(home: Path) -> None:
    observal = home / ".kiro/hooks/observal.json"
    observal.parent.mkdir()
    observal.write_text('{"version": "v1", "hooks": []}')
    assert startup_update_hooks.patch("kiro", dry_run=False) is True
    data = json.loads((home / ".kiro/hooks/observal-updates.json").read_text())
    assert data["version"] == "v1" and data["hooks"][0]["trigger"] == "SessionStart"
    assert "harness_updates --harness kiro" in data["hooks"][0]["action"]["command"]
    assert observal.read_text() == '{"version": "v1", "hooks": []}'  # an agent pull rewrites this one whole
    assert startup_update_hooks.cleanup("kiro", dry_run=False) is True
    assert not (home / ".kiro/hooks/observal-updates.json").exists()


def test_unparseable_hook_configs_are_never_overwritten(home: Path) -> None:
    hooks = home / ".codex/hooks.json"
    hooks.write_text("{not json")
    assert startup_update_hooks.patch("codex", dry_run=False) is False
    assert hooks.read_text() == "{not json"


def test_missing_harness_directories_are_left_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert not any(startup_update_hooks.patch(h, dry_run=False) for h in ("codex", "cursor", "kiro"))
    assert not {".codex", ".cursor", ".kiro"} & set(os.listdir(tmp_path))


def test_skill_plan_roots_match_the_installer_destinations() -> None:
    from observal_cli import automatic_skill_plan
    from observal_cli.cmd_skill import _user_skill_dest
    from observal_cli.startup_harnesses import SKILL_HARNESSES

    assert set(automatic_skill_plan.SKILL_ROOTS) == SKILL_HARNESSES
    for harness in SKILL_HARNESSES:
        assert automatic_skill_plan.destination("review", harness) == _user_skill_dest(harness, "review")


def test_identical_cli_notices_from_several_sessions_print_once(
    isolated: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from observal_cli import main as cli_main

    first, second = _sealed_notice("kiro", "one"), _sealed_notice("kiro", "two")
    monkeypatch.setattr(cli_main.sys, "argv", ["observal", "agent", "list"])
    monkeypatch.setattr(cli_main.sys.stderr, "isatty", lambda: True, raising=False)
    cli_main._show_startup_update_notices()
    assert capsys.readouterr().err.count("Observal Kiro update result") == 1
    assert not first.exists() and not second.exists()


def test_codex_patch_tells_the_user_to_trust_the_new_hooks(home: Path, capsys: pytest.CaptureFixture) -> None:
    startup_update_hooks.patch("codex", dry_run=False)
    assert "/hooks" in capsys.readouterr().out
