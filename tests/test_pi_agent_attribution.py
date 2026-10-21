# SPDX-FileCopyrightText: 2026 Dheirav <dheirav2005@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Pi's machine-wide active agent comes only from user-scope pulls (OBS-1453).

The Observal Pi extension tags every Pi session with ``active_agent`` from
~/.observal/config.json, so a project-scope pull that set it mis-tagged sessions
in unrelated directories.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from observal_cli import config, lockfile
from observal_cli.harness.pi import PiAdapter

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def observal_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".observal"
    home.mkdir()
    monkeypatch.setattr(config, "CONFIG_DIR", home)
    monkeypatch.setattr(config, "CONFIG_FILE", home / "config.json")
    monkeypatch.setattr(lockfile, "LOCKFILE_PATH", home / "lockfile.json")
    monkeypatch.setattr(lockfile, "_LOCKFILE_LOCK", home / "lockfile.lock")
    for name in ("OBSERVAL_SERVER_URL", "OBSERVAL_ACCESS_TOKEN", "OBSERVAL_API_KEY", "OBSERVAL_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    (home / "config.json").write_text(json.dumps({"server_url": "http://localhost:8000"}), encoding="utf-8")
    return home


def _pull(scope: str, agent_id: str, name: str, directory: Path) -> None:
    """What a pull records before cmd_pull asks the adapter to persist the active agent."""
    lockfile.upsert_agent("pi", name=name, agent_id=agent_id, version="1.0.0", scope=scope, directory=str(directory))
    PiAdapter().persist_active_agent(agent_id, name, "1.0.0", is_user_scope=scope == "user")


def _active_agent(home: Path) -> dict | None:
    return json.loads((home / "config.json").read_text(encoding="utf-8")).get("active_agent")


def test_project_scope_pull_sets_no_machine_wide_active_agent(observal_home: Path, tmp_path: Path) -> None:
    _pull("project", "agent-x", "pr-verify-qa", tmp_path / "project-a")

    assert _active_agent(observal_home) is None


def test_project_scope_pull_keeps_the_user_scope_active_agent(observal_home: Path, tmp_path: Path) -> None:
    _pull("user", "agent-y", "everyday", tmp_path)
    _pull("project", "agent-x", "pr-verify-qa", tmp_path / "project-a")

    assert _active_agent(observal_home) == {"id": "agent-y", "name": "everyday", "version": "1.0.0"}


def test_project_scope_pull_of_an_agent_also_installed_at_user_scope_keeps_the_active_agent(
    observal_home: Path, tmp_path: Path
) -> None:
    _pull("user", "agent-x", "pr-verify-qa", tmp_path)
    _pull("user", "agent-y", "everyday", tmp_path)
    _pull("project", "agent-x", "pr-verify-qa", tmp_path / "project-a")

    assert _active_agent(observal_home) == {"id": "agent-y", "name": "everyday", "version": "1.0.0"}


def test_user_scope_pull_sets_the_machine_wide_active_agent(observal_home: Path, tmp_path: Path) -> None:
    _pull("user", "agent-y", "everyday", tmp_path)

    assert _active_agent(observal_home) == {"id": "agent-y", "name": "everyday", "version": "1.0.0"}
