# SPDX-FileCopyrightText: 2026 Hussain Alsaibai <hussain.alsaibai@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Cursor hook spec and doctor patch/cleanup tests."""

from __future__ import annotations

import json

from observal_cli.cmd_doctor import _check_cursor, _cleanup_cursor, _patch_cursor
from observal_cli.harness_specs.cursor_hooks_spec import (
    CURSOR_HOOK_EVENTS,
    CURSOR_PERMISSION_EVENTS,
    build_cursor_hooks,
    build_cursor_push_command,
)


def _cursor_home(tmp_path, monkeypatch):
    """Point the doctor at a temporary home that looks like a Cursor install."""
    home = tmp_path / "home"
    (home / ".cursor").mkdir(parents=True)
    monkeypatch.setattr("observal_cli.cmd_doctor.Path.home", lambda: home)
    return home


def test_cursor_hook_events_cover_the_four_session_events():
    assert CURSOR_HOOK_EVENTS == (
        "beforeSubmitPrompt",
        "preToolUse",
        "postToolUse",
        "stop",
    )


def test_cursor_permission_events_are_a_subset_of_hook_events():
    assert set(CURSOR_PERMISSION_EVENTS).issubset(set(CURSOR_HOOK_EVENTS))


def test_cursor_push_command_targets_the_cursor_bridge():
    command = build_cursor_push_command()

    assert "observal_cli.hooks.cursor_session_push" in command


def test_cursor_push_command_carries_the_agent_id():
    command = build_cursor_push_command("agent-42")

    assert "OBSERVAL_AGENT_ID=agent-42" in command


def test_cursor_push_command_is_not_the_raw_shared_hook():
    """The bridge is required: preToolUse is a permission hook in Cursor."""
    assert "hooks.session_push " not in build_cursor_push_command()


def test_build_cursor_hooks_uses_the_flat_cursor_shape():
    hooks = build_cursor_hooks()["hooks"]

    assert set(hooks) == set(CURSOR_HOOK_EVENTS)
    for entries in hooks.values():
        assert len(entries) == 1
        assert entries[0]["type"] == "command"
        assert "hooks.cursor_session_push" in entries[0]["command"]


def test_cursor_patch_installs_every_hook_event(tmp_path, monkeypatch):
    home = _cursor_home(tmp_path, monkeypatch)
    hooks_path = home / ".cursor" / "hooks.json"

    assert _patch_cursor(dry_run=False) is True

    installed = json.loads(hooks_path.read_text())["hooks"]
    assert set(installed) == set(CURSOR_HOOK_EVENTS)
    for entries in installed.values():
        assert any("hooks.cursor_session_push" in entry["command"] for entry in entries)


def test_cursor_patch_is_idempotent(tmp_path, monkeypatch):
    home = _cursor_home(tmp_path, monkeypatch)

    assert _patch_cursor(dry_run=False) is True
    assert _patch_cursor(dry_run=False) is False


def test_cursor_patch_adds_missing_events_to_an_existing_install(tmp_path, monkeypatch):
    home = _cursor_home(tmp_path, monkeypatch)
    hooks_path = home / ".cursor" / "hooks.json"
    hooks_path.write_text(json.dumps({"version": 1, "hooks": {"beforeSubmitPrompt": []}}))

    assert _patch_cursor(dry_run=False) is True

    installed = json.loads(hooks_path.read_text())["hooks"]
    assert set(installed) == set(CURSOR_HOOK_EVENTS)


def test_cursor_patch_preserves_unrelated_hooks(tmp_path, monkeypatch):
    home = _cursor_home(tmp_path, monkeypatch)
    hooks_path = home / ".cursor" / "hooks.json"
    hooks_path.write_text(
        json.dumps(
            {
                "version": 1,
                "hooks": {
                    "afterFileEdit": [{"command": "./format.sh", "type": "command"}],
                },
            }
        )
    )

    assert _patch_cursor(dry_run=False) is True

    installed = json.loads(hooks_path.read_text())["hooks"]
    assert installed["afterFileEdit"] == [{"command": "./format.sh", "type": "command"}]


def test_cursor_check_warns_when_events_are_missing(tmp_path, monkeypatch):
    home = _cursor_home(tmp_path, monkeypatch)
    hooks_path = home / ".cursor" / "hooks.json"
    command = build_cursor_push_command()
    hooks_path.write_text(
        json.dumps(
            {
                "version": 1,
                "hooks": {
                    "beforeSubmitPrompt": [{"command": command, "type": "command"}],
                },
            }
        )
    )
    issues: list[str] = []
    warnings: list[str] = []

    _check_cursor(issues, warnings)

    assert issues == []
    assert any("preToolUse" in warning for warning in warnings)


def test_cursor_check_accepts_a_complete_install(tmp_path, monkeypatch):
    home = _cursor_home(tmp_path, monkeypatch)
    hooks_path = home / ".cursor" / "hooks.json"
    hooks_path.write_text(json.dumps({"version": 1, **build_cursor_hooks()}))
    issues: list[str] = []
    warnings: list[str] = []

    _check_cursor(issues, warnings)

    assert issues == []
    assert warnings == []


def test_cursor_cleanup_removes_only_observal_hooks(tmp_path, monkeypatch):
    home = _cursor_home(tmp_path, monkeypatch)
    hooks_path = home / ".cursor" / "hooks.json"
    hooks_path.write_text(
        json.dumps(
            {
                "version": 1,
                "hooks": {
                    "afterFileEdit": [{"command": "./format.sh", "type": "command"}],
                    **build_cursor_hooks()["hooks"],
                },
            }
        )
    )

    assert _cleanup_cursor(dry_run=False) is True

    remaining = json.loads(hooks_path.read_text())["hooks"]
    assert remaining == {"afterFileEdit": [{"command": "./format.sh", "type": "command"}]}
