# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Project pulls must write MCP configuration where the harness can read it."""

from __future__ import annotations

import json

import pytest

from observal_cli.cmd_pull import write_install_snippet
from observal_cli.errors import CliError
from observal_cli.harness import ensure_loaded, get_adapter


@pytest.fixture(autouse=True)
def load_harnesses():
    ensure_loaded()


@pytest.mark.parametrize("dry_run", [False, True])
def test_goose_project_pull_rejects_user_only_mcp_before_any_write(tmp_path, monkeypatch, dry_run):
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    snippet = {
        "agent_profile": {"path": ".agents/agents/example.md", "content": "# Example"},
        "mcp_config": {"path": "~/.config/goose/config.yaml", "content": {"extensions": {"example": {"cmd": "local"}}}},
    }
    with pytest.raises(CliError) as exc:
        write_install_snippet(
            snippet,
            harness="goose",
            adapter=get_adapter("goose"),
            target_dir=project,
            agent_id="example",
            is_user_scope=False,
            dry_run=dry_run,
        )
    assert "Goose MCP extensions require a user-level config" in exc.value.message
    assert not list(project.iterdir())
    assert not list(home.iterdir())


def test_goose_user_pull_writes_native_home_mcp_path(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    snippet = {
        "mcp_config": {
            "path": "~/.config/goose/config.yaml",
            "content": {"extensions": {"example": {"cmd": "local"}}},
        }
    }
    write_install_snippet(
        snippet,
        harness="goose",
        adapter=get_adapter("goose"),
        target_dir=home,
        agent_id="example",
        is_user_scope=True,
    )
    assert "example" in (home / ".config/goose/config.yaml").read_text()


def test_copilot_cli_project_pull_is_discoverable_and_does_not_touch_home(tmp_path, monkeypatch):
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    snippet = {
        "agent_profile": {"path": ".github/agents/example.agent.md", "content": "# Example"},
        "mcp_config": {
            "path": ".mcp.json",
            "content": {
                "mcpServers": {
                    "example": {"type": "stdio", "command": "echo", "args": ["hello"], "tools": ["*"]},
                }
            },
        },
    }
    write_install_snippet(
        snippet,
        harness="copilot-cli",
        adapter=get_adapter("copilot-cli"),
        target_dir=project,
        agent_id="example",
        is_user_scope=False,
    )
    assert json.loads((project / ".mcp.json").read_text())["mcpServers"]["example"]["command"] == "echo"
    assert not (project / ".copilot/mcp-config.json").exists()
    assert not list(home.iterdir())
    assert any(m.name == "example" for m in get_adapter("copilot-cli").scan_project(project).mcps)
