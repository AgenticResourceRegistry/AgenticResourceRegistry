# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""The startup installers plan and verify through the normal CLI installers, never a printed command."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from observal_cli import cmd_update

if TYPE_CHECKING:
    from pathlib import Path


def item(tmp_path: Path, kind: str = "agent", scope: str = "user") -> dict:
    root = str(tmp_path) if scope == "project" or kind == "agent" else None
    components = [{"type": "skill", "id": "skill-id", "version": "1.0"}] if kind == "agent" else None
    release = {"components": [{"component_type": "skill", "component_id": "skill-id", "resolved_version": "2.0"}]}
    return {
        "id": f"{kind}-id",
        "name": kind,
        "qualified_name": f"alice/{kind}",
        "type": kind,
        "harness": "pi" if kind != "hook" else "claude-code",
        "scope": scope,
        "directory": root,
        "current_version": "1.0",
        "latest_version": "2.0",
        "outdated": True,
        "status": "outdated",
        "release_verified": True,
        "release": release,
        "requested_version": None,
        "components": components,
        "lock_status": "locked" if kind == "agent" else None,
        "lock_digest": "old-digest" if kind == "agent" else None,
    }


def test_plan_does_not_execute_suggested_command_and_preserves_agent_scope(tmp_path: Path) -> None:
    candidate = item(tmp_path)
    candidate["upgrade_command"] = "sh -c 'touch /tmp/not-from-registry'"
    argv, reason, cwd = cmd_update._plan(candidate)
    assert reason is None and cwd == tmp_path
    assert argv == [
        cmd_update.sys.executable,
        "-m",
        "observal_cli",
        "agent",
        "pull",
        "agent-id",
        "--harness",
        "pi",
        "--scope",
        "user",
        "--dir",
        str(tmp_path),
        "--version",
        "2.0",
        "--strict",
        "--no-prompt",
        "--output",
        "json",
    ]
    assert "sh -c" not in " ".join(argv)


@pytest.mark.parametrize(
    "change",
    [
        {"requested_version": "1.0"},
        {"release_verified": False},
        {"directory": None},
    ],
)
def test_unsafe_agent_is_never_planned(tmp_path: Path, change: dict) -> None:
    candidate = {**item(tmp_path), **change}
    argv, reason, _cwd = cmd_update._plan(candidate)
    assert argv is None and reason


def test_agent_component_changes_are_planned_but_the_result_is_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = item(tmp_path)
    candidate["release"]["components"] = [
        {"component_type": "mcp", "component_id": "new-id", "resolved_version": "2.0"}
    ]
    argv, reason, _ = cmd_update._plan(candidate)
    assert reason is None and argv is not None and argv[3:5] == ["agent", "pull"]
    # Even when the CLI reports success, a stale component lock is not a verified update.
    monkeypatch.setattr(cmd_update, "_entries", lambda _harness: [{**candidate, "current_version": "2.0"}])
    assert cmd_update._verify(candidate) is False
    monkeypatch.setattr(
        cmd_update,
        "_entries",
        lambda _harness: [
            {
                **candidate,
                "current_version": "2.0",
                "components": [{"type": "mcp", "id": "new-id", "version": "2.0"}],
            }
        ],
    )
    assert cmd_update._verify(candidate) is True


def test_agent_lock_with_duplicate_or_missing_pins_is_not_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = item(tmp_path)
    installed = {
        **candidate,
        "current_version": "2.0",
        "components": [
            {"type": "skill", "id": "skill-id", "version": "2.0"},
            {"type": "skill", "id": "skill-id", "version": "2.0"},
        ],
    }
    monkeypatch.setattr(cmd_update, "_entries", lambda _harness: [installed])
    assert cmd_update._verify(candidate) is False
    installed["components"] = []
    assert cmd_update._verify(candidate) is False


def test_only_user_scope_agents_and_skills_have_an_installer(tmp_path: Path) -> None:
    for scope_item in (item(tmp_path, "agent", "project"), item(tmp_path, "skill", "project")):
        argv, reason, _ = cmd_update._plan(scope_item)
        assert argv is None and "outside" in reason
    for kind in ("mcp", "hook"):
        argv, reason, _ = cmd_update._plan(item(tmp_path, kind))
        assert argv is None and "no managed install command" in reason


def test_skill_plan_is_the_exact_normal_installer_in_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    argv, reason, root = cmd_update._plan(item(tmp_path, "skill"))
    assert reason is None and root == tmp_path
    assert argv == [
        cmd_update.sys.executable,
        "-m",
        "observal_cli",
        "registry",
        "skill",
        "install",
        "skill-id",
        "--harness",
        "pi",
        "--version",
        "2.0",
        "--output",
        "json",
        "--scope",
        "user",
    ]


def test_exit_zero_alone_is_not_proof_for_a_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    skill = item(tmp_path, "skill")
    monkeypatch.setattr(cmd_update, "_entries", lambda _harness: [skill])  # still at the old version
    assert cmd_update._verify(skill) is False
    monkeypatch.setattr(cmd_update, "_entries", lambda _harness: [{**skill, "current_version": "2.0"}])
    assert cmd_update._verify(skill) is True
    monkeypatch.setattr(
        cmd_update, "_entries", lambda _harness: [{**skill, "current_version": "2.0", "requested_version": "2.0"}]
    )
    assert cmd_update._verify(skill) is False, "an install that created an explicit pin is not a clean update"
