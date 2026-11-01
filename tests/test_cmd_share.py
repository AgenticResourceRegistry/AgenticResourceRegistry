# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from observal_cli import cmd_share
from observal_cli.main import app

runner = CliRunner()
TOKEN = "A" * 43


def test_discovery_combines_harnesses_and_stays_in_repository(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    child = root / "service"
    child.mkdir()
    outside = tmp_path / "repo-other"
    outside.mkdir()
    entries = [
        {
            "entry_type": "agent",
            "scope": "project",
            "directory": str(root),
            "id": "agent-1",
            "version": "1.2.3",
            "qualified_name": "team/reviewer",
            "harness": "pi",
        },
        {
            "entry_type": "agent",
            "scope": "project",
            "directory": str(child),
            "id": "agent-1",
            "version": "1.2.3",
            "qualified_name": "team/reviewer",
            "harness": "kiro",
        },
        {
            "entry_type": "agent",
            "scope": "project",
            "directory": str(outside),
            "id": "agent-2",
            "version": "1.0.0",
            "qualified_name": "team/outside",
            "harness": "cursor",
        },
        {
            "entry_type": "agent",
            "scope": "user",
            "directory": str(root),
            "id": "agent-3",
            "version": "1.0.0",
            "qualified_name": "team/global",
            "harness": "claude-code",
        },
    ]
    monkeypatch.setattr(cmd_share, "_repository_root", lambda _directory: root)
    monkeypatch.setattr("observal_cli.lockfile.get_all_entries", lambda: entries)

    resolved, agents = cmd_share.discover_repository_agents(str(root))

    assert resolved == root
    assert agents == [
        {
            "agent_id": "agent-1",
            "version": "1.2.3",
            "qualified_name": "team/reviewer",
            "installed_in": ["pi", "kiro"],
        }
    ]


def test_share_link_parser_accepts_only_configured_origin(monkeypatch):
    monkeypatch.setattr(
        cmd_share.config,
        "load",
        lambda: {"server_url": "https://api.example.test", "web_url": "https://app.example.test"},
    )

    assert cmd_share.parse_share_token(TOKEN) == TOKEN
    assert cmd_share.parse_share_token(f"https://app.example.test/shares/agents/{TOKEN}") == TOKEN
    assert cmd_share.parse_share_token(f"https://api.example.test/api/v1/agent-shares/{TOKEN}") == TOKEN

    with pytest.raises(ValueError):
        cmd_share.parse_share_token(f"https://evil.example/shares/agents/{TOKEN}")
    with pytest.raises(ValueError):
        cmd_share.parse_share_token(f"https://app.example.test/shares/agents/{TOKEN}?next=https://evil.example")
    with pytest.raises(ValueError):
        cmd_share.parse_share_token("https://app.example.test/shares/agents/../../etc/passwd")


def test_share_default_creates_expiring_manifest(tmp_path, monkeypatch):
    candidates = [
        {
            "agent_id": "11111111-1111-4111-8111-111111111111",
            "version": "2.1.0",
            "qualified_name": "team/reviewer",
            "installed_in": ["pi", "kiro"],
        }
    ]
    monkeypatch.setattr(cmd_share, "discover_repository_agents", lambda _directory: (Path(tmp_path), candidates))
    post = MagicMock(
        return_value={
            "token": TOKEN,
            "url": f"https://app.example.test/shares/agents/{TOKEN}",
            "created_at": "2026-01-01T00:00:00Z",
            "expires_at": "2026-01-08T00:00:00Z",
            "item_count": 1,
        }
    )
    monkeypatch.setattr(cmd_share.client, "post", post)

    result = runner.invoke(app, ["share", "--all", "--expires-days", "7", "--output", "json"])

    assert result.exit_code == 0
    post.assert_called_once()
    assert post.call_args.kwargs["json_data"] == {
        "title": None,
        "expires_in_days": 7,
        "items": [{"agent_id": candidates[0]["agent_id"], "version": "2.1.0"}],
    }


def test_share_open_pulls_with_argument_array_not_shell(monkeypatch, tmp_path):
    monkeypatch.setattr(
        cmd_share.client,
        "get",
        MagicMock(
            return_value={
                "title": "Review set",
                "created_by_username": "alice",
                "expires_at": "2026-01-08T00:00:00Z",
                "unavailable_count": 0,
                "items": [
                    {
                        "agent_id": "11111111-1111-4111-8111-111111111111",
                        "qualified_name": "team/reviewer",
                        "version": "2.1.0",
                    }
                ],
            }
        ),
    )
    run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr(cmd_share.subprocess, "run", run)
    from observal_cli.shared import launcher

    monkeypatch.setattr(launcher, "importable_in_isolation", lambda: True)

    result = runner.invoke(
        app,
        ["share", "open", TOKEN, "--yes", "--harness", "pi", "--dir", str(tmp_path)],
    )

    assert result.exit_code == 0
    command = run.call_args.args[0]
    # -I keeps the project directory and any inherited PYTHONPATH off sys.path.
    assert command[:5] == [sys.executable, "-I", "-m", "observal_cli", "agent"]
    assert command[5:7] == ["pull", "11111111-1111-4111-8111-111111111111"]
    assert set(run.call_args.kwargs) == {"check", "env"} and run.call_args.kwargs["check"] is False


def test_share_expiry_is_bounded_before_network_call():
    result = runner.invoke(app, ["share", "--all", "--expires-days", "31"])
    assert result.exit_code != 0


def test_repository_root_rejects_non_git_directory(tmp_path):
    import typer

    with pytest.raises(typer.BadParameter):
        cmd_share._repository_root(str(tmp_path))
