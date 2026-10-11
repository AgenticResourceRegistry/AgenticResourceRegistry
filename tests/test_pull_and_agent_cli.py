# SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Shreem Seth <shreemseth26@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the `observal pull` command."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from contextlib import contextmanager
from pathlib import PurePath
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from observal_cli.errors import CliError, ErrorCategory
from observal_cli.main import app as cli_app

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    """Strip ANSI escape codes from text."""
    return _ANSI_RE.sub("", text)


# ── Helpers ──────────────────────────────────────────────────

_FAKE_CONFIG = {"server_url": "http://localhost:8000", "api_key": "test-key"}


@contextmanager
def _patch_config():
    """Patch config access so the CLI doesn't need real credentials."""
    with (
        patch("observal_cli.config.get_or_exit", return_value=_FAKE_CONFIG),
        patch("observal_cli.config.load", return_value=_FAKE_CONFIG),
        patch(
            "observal_cli.cmd_pull.subprocess.run",
            return_value=subprocess.CompletedProcess(["claude"], 0, "", ""),
        ),
    ):
        yield


@pytest.fixture(autouse=True)
def isolated_lockfile(tmp_path, monkeypatch):
    monkeypatch.setattr("observal_cli.lockfile.LOCKFILE_PATH", tmp_path / ".observal/lockfile.json")
    monkeypatch.setattr("observal_cli.lockfile._LOCKFILE_LOCK", tmp_path / ".observal/lockfile.lock")


@contextmanager
def _patch_post(return_value: dict):
    """Patch authenticated writes and public install requests with one mock."""
    request = MagicMock(return_value=return_value)
    with (
        patch("observal_cli.client.post", request),
        patch("observal_cli.client.post_public", request),
    ):
        yield request


# Agent detail with no MCP env vars — used by pull to check for env var prompts
_AGENT_DETAIL_NO_ENV = {
    "id": "abc123",
    "name": "my-agent",
    "mcp_links": [],
    "component_links": [],
}


def _patch_get_agent(detail: dict = _AGENT_DETAIL_NO_ENV):
    """Patch client.get to return a canned agent detail response."""
    return patch("observal_cli.client.get", return_value=detail)


# ── Fixtures for common server responses ─────────────────────


def _cursor_snippet() -> dict:
    return {
        "config_snippet": {
            "agent_profile": {
                "path": ".cursor/agents/my-agent.md",
                "content": "# My Agent\n\nDo the thing.\n",
            },
            "mcp_config": {
                "path": ".cursor/mcp.json",
                "content": {
                    "mcpServers": {
                        "my-server": {
                            "command": "npx",
                            "args": ["-y", "my-server"],
                        }
                    }
                },
            },
        }
    }


def _claude_code_snippet() -> dict:
    return {
        "config_snippet": {
            "agent_profile": {
                "path": ".claude/agents/my-agent.md",
                "content": (
                    "---\n"
                    "name: my-agent\n"
                    'description: "A test agent"\n'
                    "mcpServers:\n"
                    "  - observal-mcp\n"
                    "---\n"
                    "\n"
                    "# Claude Code Agent\n"
                ),
            },
            "mcp_config": {"observal-mcp": {"command": "observal-mcp", "args": ["--agent", "abc"]}},
            "mcp_setup_commands": [["claude", "mcp", "add", "observal-mcp", "--", "observal-mcp", "--agent", "abc"]],
        }
    }


def _kiro_snippet() -> dict:
    return {
        "config_snippet": {
            "agent_profile": {
                "path": "~/.kiro/agents/my-agent.json",
                "content": {
                    "name": "my-agent",
                    "version": "1.0.0",
                    "tools": ["search"],
                },
            }
        }
    }


def _codex_snippet() -> dict:
    return {
        "config_snippet": {
            "agent_profile": {
                "path": ".codex/agents/my-agent.toml",
                "content": 'name = "my-agent"\ndeveloper_instructions = "Rules for Codex."\n',
            }
        }
    }


def _copilot_snippet() -> dict:
    return {
        "config_snippet": {
            "agent_profile": {
                "path": ".github/copilot-instructions.md",
                "content": "# Copilot Instructions\n",
            }
        }
    }


def _pi_snippet() -> dict:
    return {
        "config_snippet": {
            "agent_profile": {
                "path": ".pi/agents/my-agent/AGENTS.md",
                "content": "# Pi Agent\n",
            },
            "skill_components": [
                {
                    "name": "pi-skill",
                    "path": ".pi/agents/my-agent/skills/pi-skill/SKILL.md",
                    "skill_md_content": "# Pi Skill\n",
                }
            ],
        }
    }


def _opencode_snippet() -> dict:
    return {
        "config_snippet": {
            "agent_profile": {
                "path": ".opencode/agents/my-agent.md",
                "content": "# My Agent Rules\n",
            },
            "mcp_config": {
                "path": ".opencode/opencode.json",
                "content": {
                    "mcp": {
                        "my-server": {
                            "type": "local",
                            "command": ["npx", "-y", "my-server"],
                        }
                    }
                },
            },
            "hooks_config": {"path": ".opencode/plugins/observal-plugin.mjs", "content": "export default {};"},
        }
    }


# ═══════════════════════════════════════════════════════════════
# 1. Cursor / VSCode format
# ═══════════════════════════════════════════════════════════════


class TestPullCursor:
    def test_writes_agent_and_mcp(self, tmp_path: Path):
        with _patch_config(), _patch_get_agent(), _patch_post(_cursor_snippet()):
            result = runner.invoke(
                cli_app, ["agent", "pull", "abc123", "--harness", "cursor", "--dir", str(tmp_path), "--no-prompt"]
            )

        assert result.exit_code == 0, result.output

        agent = tmp_path / ".cursor" / "agents" / "my-agent.md"
        assert agent.exists()
        assert "My Agent" in agent.read_text()

        mcp = tmp_path / ".cursor" / "mcp.json"
        assert mcp.exists()
        data = json.loads(mcp.read_text())
        assert "my-server" in data["mcpServers"]
        assert data["mcpServers"]["my-server"]["command"] == "npx"

    def test_output_lists_written_files(self, tmp_path: Path):
        with _patch_config(), _patch_get_agent(), _patch_post(_cursor_snippet()):
            result = runner.invoke(
                cli_app, ["agent", "pull", "abc123", "--harness", "cursor", "--dir", str(tmp_path), "--no-prompt"]
            )

        assert "Pulled cursor config" in result.output
        # Rich may wrap long absolute paths; strip all whitespace for path checks
        flat = result.output.replace("\n", "").replace(" ", "")
        assert "my-agent.md" in flat
        assert "mcp.json" in flat


# ═══════════════════════════════════════════════════════════════
# 2. Claude Code format
# ═══════════════════════════════════════════════════════════════


class TestPullClaudeCode:
    def test_writes_agent_profile(self, tmp_path: Path):
        with _patch_config(), _patch_get_agent(), _patch_post(_claude_code_snippet()):
            result = runner.invoke(
                cli_app, ["agent", "pull", "abc123", "--harness", "claude-code", "--dir", str(tmp_path), "--no-prompt"]
            )

        assert result.exit_code == 0, result.output
        agent_profile = tmp_path / ".claude" / "agents" / "my-agent.md"
        assert agent_profile.exists()
        content = agent_profile.read_text()
        assert content.startswith("---\n")
        assert "name: my-agent" in content
        assert "mcpServers:" in content
        assert "Claude Code Agent" in content

    def test_auto_runs_setup_commands(self, tmp_path: Path):
        with _patch_config(), _patch_get_agent(), _patch_post(_claude_code_snippet()):
            result = runner.invoke(
                cli_app, ["agent", "pull", "abc123", "--harness", "claude-code", "--dir", str(tmp_path), "--no-prompt"]
            )

        assert "Registered MCP servers" in result.output

    def test_dry_run_shows_setup_commands_without_running(self, tmp_path: Path):
        with _patch_config(), _patch_get_agent(), _patch_post(_claude_code_snippet()):
            result = runner.invoke(
                cli_app,
                [
                    "agent",
                    "pull",
                    "abc123",
                    "--harness",
                    "claude-code",
                    "--dir",
                    str(tmp_path),
                    "--dry-run",
                    "--no-prompt",
                ],
            )

        assert "Would run these setup commands" in result.output
        assert "claude mcp add" in result.output

    def test_no_otlp_env_in_output(self, tmp_path: Path):
        """OTLP env vars should not be shown in pull output."""
        with _patch_config(), _patch_get_agent(), _patch_post(_claude_code_snippet()):
            result = runner.invoke(
                cli_app, ["agent", "pull", "abc123", "--harness", "claude-code", "--dir", str(tmp_path), "--no-prompt"]
            )

        assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in result.output
        assert "OTEL_SERVICE_NAME" not in result.output

    def test_mcp_config_without_path_not_written(self, tmp_path: Path):
        """Claude Code mcp_config has no 'path' key — should not write a file for it."""
        with _patch_config(), _patch_get_agent(), _patch_post(_claude_code_snippet()):
            result = runner.invoke(
                cli_app, ["agent", "pull", "abc123", "--harness", "claude-code", "--dir", str(tmp_path), "--no-prompt"]
            )

        assert result.exit_code == 0
        # Only the agent file should be written, not an mcp_config file
        assert (tmp_path / ".claude" / "agents" / "my-agent.md").exists()
        # No .claude/mcp.json should exist — Claude Code uses setup commands instead
        assert not (tmp_path / ".claude" / "mcp.json").exists()

    def test_rewrites_frontmatter_hook_python_path(self, tmp_path: Path, monkeypatch):
        """Frontmatter hooks must use this CLI's interpreter, including paths with spaces."""
        monkeypatch.setattr(sys, "executable", "/tmp/Observal Tools/bin/python3")
        snippet = _claude_code_snippet()
        snippet["config_snippet"]["agent_profile"]["content"] = (
            "---\n"
            "name: my-agent\n"
            "hooks:\n"
            "  UserPromptSubmit:\n"
            "    - hooks:\n"
            "        - type: command\n"
            '          command: "python3 -m observal_cli.hooks.session_push"\n'
            "  Stop:\n"
            "    - hooks:\n"
            "        - type: command\n"
            '          command: "python3 -m observal_cli.hooks.session_push"\n'
            "---\n"
        )
        with _patch_config(), _patch_get_agent(), _patch_post(snippet):
            result = runner.invoke(
                cli_app, ["agent", "pull", "abc123", "--harness", "claude-code", "--dir", str(tmp_path), "--no-prompt"]
            )

        assert result.exit_code == 0, result.output
        content = (tmp_path / ".claude" / "agents" / "my-agent.md").read_text()
        assert '"python3 -m' not in content
        frontmatter, _, _body = content[4:].partition("\n---")
        hooks = yaml.safe_load(frontmatter)["hooks"]
        expected_path = PurePath(sys.executable).as_posix()
        quoted = subprocess.list2cmdline([expected_path]) if sys.platform == "win32" else shlex.quote(expected_path)
        for event in ("UserPromptSubmit", "Stop"):
            command = hooks[event][0]["hooks"][0]["command"]
            assert command == f"{quoted} -m observal_cli.hooks.session_push"

    def test_says_when_agent_reports_sessions(self, tmp_path: Path):
        snippet = _claude_code_snippet()
        snippet["config_snippet"]["agent_profile"]["content"] = (
            "---\n"
            "name: my-agent\n"
            "hooks:\n"
            "  Stop:\n"
            "    - hooks:\n"
            "        - type: command\n"
            '          command: "python3 -m observal_cli.hooks.session_push"\n'
            "---\n"
        )
        with _patch_config(), _patch_get_agent(), _patch_post(snippet):
            result = runner.invoke(
                cli_app, ["agent", "pull", "abc123", "--harness", "claude-code", "--dir", str(tmp_path), "--no-prompt"]
            )

        assert result.exit_code == 0, result.output
        output = _plain(result.output)
        assert "Telemetry:" in output
        assert "session hooks are present" in output
        assert "http://localhost:8000" in output

        with _patch_config(), _patch_get_agent(), _patch_post(snippet):
            dry_run = runner.invoke(
                cli_app,
                [
                    "agent",
                    "pull",
                    "abc123",
                    "--harness",
                    "claude-code",
                    "--dir",
                    str(tmp_path),
                    "--dry-run",
                    "--no-prompt",
                ],
            )
        assert dry_run.exit_code == 0, dry_run.output
        assert "session hooks would be present after this pull" in _plain(dry_run.output)

    def test_no_telemetry_line_without_session_hooks(self, tmp_path: Path):
        with _patch_config(), _patch_get_agent(), _patch_post(_claude_code_snippet()):
            result = runner.invoke(
                cli_app, ["agent", "pull", "abc123", "--harness", "claude-code", "--dir", str(tmp_path), "--no-prompt"]
            )

        assert result.exit_code == 0, result.output
        assert "Telemetry:" not in _plain(result.output)


# ═══════════════════════════════════════════════════════════════
# 3. Kiro format (agent_profile with ~/  path)
# ═══════════════════════════════════════════════════════════════


class TestPullKiro:
    def test_writes_agent_profile(self, tmp_path: Path):
        with _patch_config(), _patch_get_agent(), _patch_post(_kiro_snippet()):
            result = runner.invoke(
                cli_app,
                [
                    "agent",
                    "pull",
                    "abc123",
                    "--harness",
                    "kiro",
                    "--dir",
                    str(tmp_path),
                    "--no-prompt",
                    "--scope",
                    "project",
                ],
            )

        assert result.exit_code == 0, result.output
        agent = tmp_path / ".kiro" / "agents" / "my-agent.json"
        assert agent.exists()
        data = json.loads(agent.read_text())
        assert data["name"] == "my-agent"
        assert data["tools"] == ["search"]

    @staticmethod
    def _hooked_snippet() -> dict:
        return {
            "config_snippet": {
                "agent_profile": {
                    "path": "~/.kiro/agents/my-agent.json",
                    "content": {
                        "name": "my-agent",
                        "tools": ["search"],
                        "hooks": {
                            "userPromptSubmit": [{"command": "python3 -m observal_cli.hooks.kiro_session_push"}],
                            "stop": [{"command": "python3 -m observal_cli.hooks.kiro_session_push"}],
                        },
                    },
                },
                "hooks_config": {
                    "path": ".kiro/hooks/observal.json",
                    "content": {
                        "version": "v1",
                        "hooks": [
                            {
                                "name": "observal-session-push-stop",
                                "trigger": "Stop",
                                "action": {
                                    "type": "command",
                                    "command": "python3 -m observal_cli.hooks.session_push --harness kiro",
                                },
                            }
                        ],
                    },
                },
            }
        }

    def _pull(self, tmp_path: Path):
        with _patch_config(), _patch_get_agent(), _patch_post(self._hooked_snippet()):
            return runner.invoke(
                cli_app,
                [
                    "agent",
                    "pull",
                    "abc123",
                    "--harness",
                    "kiro",
                    "--dir",
                    str(tmp_path),
                    "--no-prompt",
                    "--scope",
                    "project",
                ],
            )

    def test_strips_cli_only_tool_fields_a_stale_server_still_emits(self, tmp_path: Path):
        """Repair profiles on pull, not just at generation time.

        Kiro IDE 1.x ProfileLoader drops any profile carrying ``allowedTools``
        or ``toolsSettings`` without ``permissions``, so the agent never reaches
        the picker. The server no longer emits them, but a client talking to an
        older server has to fix the profile locally.
        """
        snippet = self._hooked_snippet()
        content = snippet["config_snippet"]["agent_profile"]["content"]
        content["allowedTools"] = []
        content["toolsSettings"] = {}

        with _patch_config(), _patch_get_agent(), _patch_post(snippet):
            result = runner.invoke(
                cli_app,
                [
                    "agent",
                    "pull",
                    "abc123",
                    "--harness",
                    "kiro",
                    "--dir",
                    str(tmp_path),
                    "--no-prompt",
                    "--scope",
                    "project",
                ],
            )

        assert result.exit_code == 0, result.output
        data = json.loads((tmp_path / ".kiro" / "agents" / "my-agent.json").read_text())
        assert "allowedTools" not in data
        assert "toolsSettings" not in data
        assert data["name"] == "my-agent"

    def test_strips_inline_hooks_when_ide_can_read_the_standalone_file(self, tmp_path: Path, monkeypatch):
        """Kiro IDE 1.0 loads inline-hooked agents but never fires the hooks."""
        import observal_cli.harness.kiro as kiro_adapter

        monkeypatch.setattr(kiro_adapter, "use_inline_hooks", lambda *_a, **_k: False)
        result = self._pull(tmp_path)

        assert result.exit_code == 0, result.output
        data = json.loads((tmp_path / ".kiro" / "agents" / "my-agent.json").read_text())
        assert "hooks" not in data

    def test_writes_standalone_v1_hooks_file(self, tmp_path: Path, monkeypatch):
        """Telemetry hooks live in .kiro/hooks/observal.json, read by IDE and CLI 3.x."""
        import observal_cli.harness.kiro as kiro_adapter

        monkeypatch.setattr(kiro_adapter, "use_inline_hooks", lambda *_a, **_k: False)
        result = self._pull(tmp_path)

        assert result.exit_code == 0, result.output
        hooks_file = tmp_path / ".kiro" / "hooks" / "observal.json"
        assert hooks_file.exists()
        data = json.loads(hooks_file.read_text())
        assert data["version"] == "v1"
        triggers = {h["trigger"] for h in data["hooks"]}
        assert triggers == {"UserPromptSubmit", "Stop"}
        for hook in data["hooks"]:
            command = hook["action"]["command"]
            assert sys.executable in command, f"hook missing sys.executable: {command}"
            # Shared across every agent in the scope, so no per-agent id.
            assert "OBSERVAL_AGENT_ID" not in command
            assert not command.startswith("python3 ")

    def test_rewrites_hook_python_path(self, tmp_path: Path, monkeypatch):
        """On legacy Kiro CLI 2.x, inline hooks stay and use sys.executable."""
        import observal_cli.harness.kiro as kiro_adapter

        monkeypatch.setattr(kiro_adapter, "use_inline_hooks", lambda *_a, **_k: True)
        result = self._pull(tmp_path)

        assert result.exit_code == 0, result.output
        agent = tmp_path / ".kiro" / "agents" / "my-agent.json"
        data = json.loads(agent.read_text())
        # All hook commands should use sys.executable and the pulled agent UUID.
        for event, entries in data["hooks"].items():
            for h in entries:
                assert sys.executable in h["command"], f"{event} hook missing sys.executable: {h['command']}"
                assert "OBSERVAL_AGENT_ID=abc123" in h["command"]
                assert "OBSERVAL_AGENT_NAME" not in h["command"]
                assert not h["command"].startswith("python3 ")


# ═══════════════════════════════════════════════════════════════
# 5. Codex format
# ═══════════════════════════════════════════════════════════════


class TestPullCodex:
    def test_writes_custom_agent(self, tmp_path: Path):
        with _patch_config(), _patch_get_agent(), _patch_post(_codex_snippet()):
            result = runner.invoke(
                cli_app, ["agent", "pull", "abc123", "--harness", "codex", "--dir", str(tmp_path), "--no-prompt"]
            )

        assert result.exit_code == 0, result.output
        agent = tmp_path / ".codex" / "agents" / "my-agent.toml"
        assert agent.exists()
        assert "Rules for Codex" in agent.read_text()


# ═══════════════════════════════════════════════════════════════
# 6. Copilot format
# ═══════════════════════════════════════════════════════════════


class TestPullCopilot:
    def test_writes_copilot_instructions(self, tmp_path: Path):
        with _patch_config(), _patch_get_agent(), _patch_post(_copilot_snippet()):
            result = runner.invoke(
                cli_app, ["agent", "pull", "abc123", "--harness", "copilot", "--dir", str(tmp_path), "--no-prompt"]
            )

        assert result.exit_code == 0, result.output
        rules = tmp_path / ".github" / "copilot-instructions.md"
        assert rules.exists()
        assert "Copilot Instructions" in rules.read_text()


# ═══════════════════════════════════════════════════════════════
# 6b. OpenCode format
# ═══════════════════════════════════════════════════════════════


class TestPullPi:
    def test_writes_skill_to_agent_profile_path(self, tmp_path: Path):
        with _patch_config(), _patch_get_agent(), _patch_post(_pi_snippet()):
            result = runner.invoke(
                cli_app,
                [
                    "agent",
                    "pull",
                    "abc123",
                    "--harness",
                    "pi",
                    "--dir",
                    str(tmp_path),
                    "--no-prompt",
                    "--scope",
                    "project",
                ],
            )

        assert result.exit_code == 0, result.output
        skill = tmp_path / ".pi" / "agents" / "my-agent" / "skills" / "pi-skill" / "SKILL.md"
        assert skill.exists()
        assert "Pi Skill" in skill.read_text()


class TestPullOpenCode:
    def test_writes_rules_mcp_and_hooks(self, tmp_path: Path):
        with _patch_config(), _patch_get_agent(), _patch_post(_opencode_snippet()):
            result = runner.invoke(
                cli_app,
                [
                    "agent",
                    "pull",
                    "abc123",
                    "--harness",
                    "opencode",
                    "--dir",
                    str(tmp_path),
                    "--no-prompt",
                    "--scope",
                    "project",
                ],
            )

        assert result.exit_code == 0, result.output

        rules = tmp_path / ".opencode" / "agents" / "my-agent.md"
        assert rules.exists()
        assert "My Agent Rules" in rules.read_text()

        mcp = tmp_path / ".opencode" / "opencode.json"
        assert mcp.exists()
        data = json.loads(mcp.read_text())
        assert "my-server" in data["mcp"]
        assert data["mcp"]["my-server"]["type"] == "local"

        hook = tmp_path / ".opencode" / "plugins" / "observal-plugin.mjs"
        assert hook.exists()
        assert "export default" in hook.read_text()


# ═══════════════════════════════════════════════════════════════
# 7. MCP merge behaviour
# ═══════════════════════════════════════════════════════════════


class TestPullMcpMerge:
    def test_merge_preserves_existing_servers(self, tmp_path: Path):
        """Pre-existing mcpServers should not be overwritten."""
        mcp_path = tmp_path / ".cursor" / "mcp.json"
        mcp_path.parent.mkdir(parents=True)
        mcp_path.write_text(
            json.dumps({"mcpServers": {"existing-server": {"command": "old-cmd", "args": ["--old"]}}}, indent=2)
        )

        with _patch_config(), _patch_get_agent(), _patch_post(_cursor_snippet()):
            result = runner.invoke(
                cli_app, ["agent", "pull", "abc123", "--harness", "cursor", "--dir", str(tmp_path), "--no-prompt"]
            )

        assert result.exit_code == 0, result.output
        data = json.loads(mcp_path.read_text())
        # Both servers must be present
        assert "existing-server" in data["mcpServers"]
        assert data["mcpServers"]["existing-server"]["command"] == "old-cmd"
        assert "my-server" in data["mcpServers"]
        assert data["mcpServers"]["my-server"]["command"] == "npx"

    def test_merge_overwrites_same_named_server(self, tmp_path: Path):
        """If the incoming server has the same name, it should update."""
        mcp_path = tmp_path / ".cursor" / "mcp.json"
        mcp_path.parent.mkdir(parents=True)
        mcp_path.write_text(json.dumps({"mcpServers": {"my-server": {"command": "old", "args": []}}}, indent=2))

        with _patch_config(), _patch_get_agent(), _patch_post(_cursor_snippet()):
            result = runner.invoke(
                cli_app, ["agent", "pull", "abc123", "--harness", "cursor", "--dir", str(tmp_path), "--no-prompt"]
            )

        assert result.exit_code == 0, result.output
        data = json.loads(mcp_path.read_text())
        assert data["mcpServers"]["my-server"]["command"] == "npx"

    def test_merge_status_reported(self, tmp_path: Path):
        """Output should say 'merged' when existing mcp.json was merged."""
        mcp_path = tmp_path / ".cursor" / "mcp.json"
        mcp_path.parent.mkdir(parents=True)
        mcp_path.write_text(json.dumps({"mcpServers": {}}, indent=2))

        with _patch_config(), _patch_get_agent(), _patch_post(_cursor_snippet()):
            result = runner.invoke(
                cli_app, ["agent", "pull", "abc123", "--harness", "cursor", "--dir", str(tmp_path), "--no-prompt"]
            )

        assert "merged" in result.output


# ═══════════════════════════════════════════════════════════════
# 8. Dry-run
# ═══════════════════════════════════════════════════════════════


class TestPullDryRun:
    def test_dry_run_does_not_write_files(self, tmp_path: Path):
        with _patch_config(), _patch_get_agent(), _patch_post(_cursor_snippet()):
            result = runner.invoke(
                cli_app,
                ["agent", "pull", "abc123", "--harness", "cursor", "--dir", str(tmp_path), "--dry-run", "--no-prompt"],
            )

        assert result.exit_code == 0, result.output
        assert "Dry run" in result.output
        assert "would write" in result.output

        # No files should exist
        assert not (tmp_path / ".cursor" / "agents" / "my-agent.md").exists()
        assert not (tmp_path / ".cursor" / "mcp.json").exists()

    def test_dry_run_still_shows_paths(self, tmp_path: Path):
        with _patch_config(), _patch_get_agent(), _patch_post(_cursor_snippet()):
            result = runner.invoke(
                cli_app,
                ["agent", "pull", "abc123", "--harness", "cursor", "--dir", str(tmp_path), "--dry-run", "--no-prompt"],
            )

        # Rich may wrap long absolute paths; strip all whitespace for path checks
        flat = result.output.replace("\n", "").replace(" ", "")
        assert "my-agent.md" in flat
        assert "mcp.json" in flat

    def test_dry_run_kiro(self, tmp_path: Path):
        with _patch_config(), _patch_get_agent(), _patch_post(_kiro_snippet()):
            result = runner.invoke(
                cli_app,
                ["agent", "pull", "abc123", "--harness", "kiro", "--dir", str(tmp_path), "--dry-run", "--no-prompt"],
            )

        assert result.exit_code == 0
        assert "would write" in result.output
        assert not (tmp_path / ".kiro" / "agents" / "my-agent.json").exists()


# ═══════════════════════════════════════════════════════════════
# 9. Edge cases and argument validation
# ═══════════════════════════════════════════════════════════════


class TestPullEdgeCases:
    def test_missing_ide_flag_fails(self):
        """--harness is required; omitting it should exit non-zero."""
        with _patch_config():
            result = runner.invoke(cli_app, ["agent", "pull", "abc123"])
        assert result.exit_code != 0

    def test_missing_agent_id_fails(self):
        """Agent ID is a required argument."""
        with _patch_config():
            result = runner.invoke(cli_app, ["agent", "pull", "--harness", "cursor"])
        assert result.exit_code != 0

    def test_empty_snippet_exits(self, tmp_path: Path):
        """An empty config_snippet from the server should exit non-zero."""
        with _patch_config(), _patch_get_agent(), _patch_post({"config_snippet": {}}):
            result = runner.invoke(
                cli_app, ["agent", "pull", "abc123", "--harness", "cursor", "--dir", str(tmp_path), "--no-prompt"]
            )
        assert result.exit_code != 0
        assert "empty agent configuration" in result.output.lower()

    def test_resolve_alias_is_called(self, tmp_path: Path):
        """The command should call config.resolve_alias for the agent_id."""
        with (
            _patch_config(),
            _patch_get_agent(),
            _patch_post(_codex_snippet()),
            patch("observal_cli.config.resolve_alias", return_value="real-uuid") as mock_resolve,
        ):
            result = runner.invoke(
                cli_app, ["agent", "pull", "@myagent", "--harness", "codex", "--dir", str(tmp_path), "--no-prompt"]
            )

        assert result.exit_code == 0, result.output
        mock_resolve.assert_called_once_with("@myagent")


# ═══════════════════════════════════════════════════════════════
# 10. Env var prompting during pull
# ═══════════════════════════════════════════════════════════════


_AGENT_WITH_MCP = {
    "id": "agent-uuid",
    "name": "my-agent",
    "mcp_links": [{"mcp_listing_id": "mcp-uuid-1", "mcp_name": "my-mcp", "order": 0}],
    "component_links": [],
}

_MCP_LISTING_WITH_ENV = {
    "id": "mcp-uuid-1",
    "name": "my-mcp",
    "environment_variables": [
        {"name": "API_KEY", "description": "Your API key", "required": True},
        {"name": "REGION", "description": "AWS region", "required": False},
    ],
}


class TestPullEnvVarPrompting:
    def test_prompts_for_required_env_vars(self, tmp_path: Path):
        """Pull should prompt for MCP env vars and send them to install."""

        def mock_get(path, **kwargs):
            if "/mcps/" in path:
                return _MCP_LISTING_WITH_ENV
            return _AGENT_WITH_MCP

        with (
            _patch_config(),
            patch("observal_cli.client.get", side_effect=mock_get),
            _patch_post(_cursor_snippet()) as mock_post,
        ):
            result = runner.invoke(
                cli_app,
                ["agent", "pull", "agent-uuid", "--harness", "cursor", "--dir", str(tmp_path)],
                input="sk-test-123\nus-east-1\n",
            )

        assert result.exit_code == 0, result.output
        assert "requires 1 environment variable" in result.output
        assert "API_KEY" in result.output

        # Verify env_values were sent to the install endpoint
        call_args = mock_post.call_args
        payload = call_args[0][1]
        assert "env_values" in payload
        assert payload["env_values"]["mcp-uuid-1"]["API_KEY"] == "sk-test-123"

    def test_no_prompts_when_no_env_vars(self, tmp_path: Path):
        """Pull should not prompt when agent MCPs have no env vars."""
        agent_no_env = {
            "id": "agent-uuid",
            "name": "my-agent",
            "mcp_links": [{"mcp_listing_id": "mcp-uuid-2", "mcp_name": "simple-mcp", "order": 0}],
            "component_links": [],
        }
        mcp_no_env = {"id": "mcp-uuid-2", "name": "simple-mcp", "environment_variables": []}

        def mock_get(path, **kwargs):
            if "/mcps/" in path:
                return mcp_no_env
            return agent_no_env

        with _patch_config(), patch("observal_cli.client.get", side_effect=mock_get), _patch_post(_cursor_snippet()):
            result = runner.invoke(
                cli_app,
                ["agent", "pull", "agent-uuid", "--harness", "cursor", "--dir", str(tmp_path), "--no-prompt"],
            )

        assert result.exit_code == 0, result.output
        assert "environment variable" not in result.output


# ═══════════════════════════════════════════════════════════════
# 11. Help text
# ═══════════════════════════════════════════════════════════════


class TestPullHelp:
    def test_help_flag(self):
        result = runner.invoke(cli_app, ["agent", "pull", "--help"])
        assert result.exit_code == 0
        out = _plain(result.output)
        assert "Fetch agent config" in out
        assert "--harness" in out
        assert "--dir" in out
        assert "--dry-run" in out


# ═══════════════════════════════════════════════════════════════
# Agent authoring CLI tests
# ═══════════════════════════════════════════════════════════════


def _make_agent_yaml(tmp_path: Path, **overrides) -> Path:
    """Write a minimal observal-agent.yaml and return its path."""
    data = {
        "name": "test-agent",
        "version": "1.0.0",
        "description": "A test agent",
        "owner": "test-team",
        "model_name": "claude-sonnet-4",
        "prompt": "You are helpful.",
        "components": [],
    }
    data.update(overrides)
    yaml_path = tmp_path / "observal-agent.yaml"
    yaml_path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
    return yaml_path


def _patch_get(return_value):
    return patch("observal_cli.client.get", return_value=return_value)


def _patch_put(return_value):
    return patch("observal_cli.client.put", return_value=return_value)


class TestAgentInit:
    def test_creates_yaml_with_correct_fields(self, tmp_path: Path):
        """Interactive prompts produce a valid YAML file."""
        inputs = "my-agent\n1.0.0\nA cool agent\nclaude-sonnet-4\nDo helpful things\n"
        with patch("observal_cli.config.load", return_value={"username": "my-team"}):
            result = runner.invoke(
                cli_app,
                ["agent", "init", "--dir", str(tmp_path)],
                input=inputs,
            )
        assert result.exit_code == 0, result.output
        assert "Created" in result.output

        yaml_path = tmp_path / "observal-agent.yaml"
        assert yaml_path.exists()

        data = yaml.safe_load(yaml_path.read_text())
        assert data["name"] == "my-agent"
        assert data["version"] == "1.0.0"
        assert data["description"] == "A cool agent"
        assert data["owner"] == "my-team"
        assert data["model_name"] == "claude-sonnet-4"
        assert data["prompt"] == "Do helpful things"
        assert data["components"] == []

    def test_aborts_if_file_exists_and_user_declines(self, tmp_path: Path):
        """If YAML already exists and user says no, exit code != 0."""
        _make_agent_yaml(tmp_path)
        # "n" to decline overwrite
        result = runner.invoke(
            cli_app,
            ["agent", "init", "--dir", str(tmp_path)],
            input="n\n",
        )
        assert result.exit_code != 0
        assert "Aborted" in result.output

    def test_overwrites_if_user_confirms(self, tmp_path: Path):
        """If YAML exists and user says yes, proceed with new data."""
        _make_agent_yaml(tmp_path)
        inputs = "y\nnew-agent\n2.0.0\nNew desc\nnew-team\nclaude-sonnet-4\nNew prompt\n"
        result = runner.invoke(
            cli_app,
            ["agent", "init", "--dir", str(tmp_path)],
            input=inputs,
        )
        assert result.exit_code == 0, result.output
        data = yaml.safe_load((tmp_path / "observal-agent.yaml").read_text())
        assert data["name"] == "new-agent"


class TestAgentAdd:
    def test_adds_component_to_yaml(self, tmp_path: Path):
        """A valid component is appended to the components list."""
        _make_agent_yaml(tmp_path)
        result = runner.invoke(
            cli_app,
            ["agent", "add", "mcp", "11111111-1111-1111-1111-111111111111", "--dir", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert "Added" in result.output

        data = yaml.safe_load((tmp_path / "observal-agent.yaml").read_text())
        assert len(data["components"]) == 1
        assert data["components"][0]["component_type"] == "mcp"
        assert data["components"][0]["component_id"] == "11111111-1111-1111-1111-111111111111"

    def test_rejects_invalid_component_type(self, tmp_path: Path):
        """Invalid component_type exits non-zero."""
        _make_agent_yaml(tmp_path)
        result = runner.invoke(
            cli_app,
            ["agent", "add", "widget", "some-id", "--dir", str(tmp_path)],
        )
        assert result.exit_code != 0
        assert "Unknown agent component type" in result.output

    def test_prevents_duplicate_component(self, tmp_path: Path):
        """Adding the same component twice exits non-zero."""
        _make_agent_yaml(
            tmp_path,
            components=[
                {"component_type": "skill", "component_id": "22222222-2222-2222-2222-222222222222"},
            ],
        )
        result = runner.invoke(
            cli_app,
            ["agent", "add", "skill", "22222222-2222-2222-2222-222222222222", "--dir", str(tmp_path)],
        )
        assert result.exit_code != 0
        assert "already exists" in result.output

    def test_fails_if_no_yaml(self, tmp_path: Path):
        """Fails if observal-agent.yaml does not exist."""
        result = runner.invoke(
            cli_app,
            ["agent", "add", "mcp", "33333333-3333-3333-3333-333333333333", "--dir", str(tmp_path)],
        )
        assert result.exit_code != 0
        assert "not found" in result.output


_TEAM_UUID = "6f3c4b1a-9d2e-4f5a-8c7b-1e2d3f4a5b6c"


def _validate_call(mock_post_fn):
    """Return the payload the CLI sent to the agent composition validator."""
    for call in mock_post_fn.call_args_list:
        if call[0][0] == "/api/v1/agents/validate":
            return call[0][1]
    raise AssertionError(f"composition validation was never called: {mock_post_fn.call_args_list}")


class TestAgentBuild:
    def test_validates_components_against_server(self, tmp_path: Path):
        """Components are validated via GET calls, then scope-checked on the server."""
        components = [
            {"component_type": "mcp", "component_id": "id-1"},
            {"component_type": "skill", "component_id": "id-2"},
        ]
        _make_agent_yaml(tmp_path, components=components)

        def mock_get(path, **kwargs):
            return {"id": "id-1", "name": "test"}

        with (
            _patch_config(),
            patch("observal_cli.client.get", side_effect=mock_get),
            _patch_post({"valid": True, "issues": []}) as mock_post_fn,
        ):
            result = runner.invoke(
                cli_app,
                ["agent", "build", "--dir", str(tmp_path)],
            )
        assert result.exit_code == 0, result.output
        assert "All components valid" in result.output

        payload = _validate_call(mock_post_fn)
        assert payload["components"] == components
        assert payload["visibility"] == "public"
        assert "team_id" not in payload

    def test_sends_publish_target_to_composition_validation(self, tmp_path: Path):
        """--team/--visibility are resolved and sent to the composition validator."""
        _make_agent_yaml(
            tmp_path,
            components=[{"component_type": "skill", "component_id": "id-1"}],
        )

        def mock_get(path, **kwargs):
            if path == "/api/v1/teams/all":
                return [{"id": _TEAM_UUID, "handle": "platform"}]
            return {"id": "id-1", "name": "test"}

        with (
            _patch_config(),
            patch("observal_cli.client.get", side_effect=mock_get),
            _patch_post({"valid": True, "issues": []}) as mock_post_fn,
        ):
            result = runner.invoke(
                cli_app,
                ["agent", "build", "--dir", str(tmp_path), "--team", "@platform", "--visibility", "team"],
            )
        assert result.exit_code == 0, result.output

        payload = _validate_call(mock_post_fn)
        assert payload["visibility"] == "team"
        assert payload["team_id"] == _TEAM_UUID

    def test_reports_invalid_components(self, tmp_path: Path):
        """Components that fail GET show as errors."""
        _make_agent_yaml(
            tmp_path,
            components=[
                {"component_type": "mcp", "component_id": "bad-id"},
            ],
        )

        def mock_get(path, **kwargs):
            raise CliError(
                ErrorCategory.NOT_FOUND,
                "Component not found.",
                "Validate agent",
                resource=path,
            )

        with (
            _patch_config(),
            patch("observal_cli.client.get", side_effect=mock_get),
            _patch_post({"valid": True, "issues": []}),
        ):
            result = runner.invoke(
                cli_app,
                ["agent", "build", "--dir", str(tmp_path)],
            )
        assert result.exit_code != 0
        assert "validation failed" in result.output
        assert "mcp:bad-id" in result.output

    def test_reports_server_scope_issues(self, tmp_path: Path):
        """A scope issue from the server fails the build and is shown to the user."""
        _make_agent_yaml(
            tmp_path,
            components=[{"component_type": "skill", "component_id": "id-1"}],
        )

        def mock_get(path, **kwargs):
            return {"id": "id-1", "name": "test"}

        scope_result = {
            "valid": False,
            "issues": [
                {
                    "severity": "error",
                    "component_type": "skill",
                    "component_id": "id-1",
                    "message": "Component belongs to another teamspace",
                }
            ],
        }
        with (
            _patch_config(),
            patch("observal_cli.client.get", side_effect=mock_get),
            _patch_post(scope_result),
        ):
            result = runner.invoke(
                cli_app,
                ["agent", "build", "--dir", str(tmp_path)],
            )
        assert result.exit_code != 0
        assert "validation failed" in result.output
        assert "Component belongs to another teamspace" in result.output

    def test_fails_if_no_yaml(self, tmp_path: Path):
        """Fails if observal-agent.yaml does not exist."""
        result = runner.invoke(
            cli_app,
            ["agent", "build", "--dir", str(tmp_path)],
        )
        assert result.exit_code != 0
        assert "not found" in result.output


class TestAgentPublish:
    def test_creates_agent_via_post(self, tmp_path: Path):
        """publish sends correct payload via POST and reports the pending status."""
        _make_agent_yaml(tmp_path)
        mock_result = {
            "id": "new-agent-uuid",
            "name": "test-agent",
            "qualified_name": "tester/test-agent",
            "status": "pending",
        }

        with _patch_config(), _patch_post(mock_result) as mock_post_fn:
            result = runner.invoke(
                cli_app,
                ["agent", "publish", "--dir", str(tmp_path)],
            )
        assert result.exit_code == 0, result.output
        assert "Agent submitted!" in result.output
        assert "new-agent-uuid" in result.output
        assert "observal agent pull tester/test-agent" in result.output
        # A public publish stays in the review queue, so the CLI must say so.
        assert "review is required before publication" in result.output

        # Verify the POST payload
        call_args = mock_post_fn.call_args
        payload = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("json_data")
        assert payload["name"] == "test-agent"
        assert payload["version"] == "1.0.0"
        assert payload["model_name"] == "claude-sonnet-4"
        assert payload["visibility"] == "public"

    def test_team_publish_reports_auto_approval(self, tmp_path: Path):
        """A team publish auto-approves, so no review warning is printed."""
        _make_agent_yaml(tmp_path)
        mock_result = {
            "id": "new-agent-uuid",
            "name": "test-agent",
            "qualified_name": "platform/test-agent",
            "status": "approved",
        }

        with (
            _patch_config(),
            _patch_get([{"id": _TEAM_UUID, "handle": "platform"}]),
            _patch_post(mock_result) as mock_post_fn,
        ):
            result = runner.invoke(
                cli_app,
                ["agent", "publish", "--dir", str(tmp_path), "--team", "@platform", "--visibility", "team"],
            )
        assert result.exit_code == 0, result.output
        assert "Agent submitted!" in result.output
        assert "observal agent pull platform/test-agent" in result.output
        assert "must approve" not in result.output

        payload = mock_post_fn.call_args[0][1]
        assert payload["visibility"] == "team"
        assert payload["team_id"] == _TEAM_UUID

    def test_updates_existing_agent_with_update_flag(self, tmp_path: Path):
        """--update finds agent by name then PUTs."""
        _make_agent_yaml(tmp_path)
        search_results = [{"id": "existing-uuid", "name": "test-agent"}]
        put_result = {"id": "existing-uuid", "name": "test-agent"}

        with _patch_config(), _patch_get(search_results) as mock_get_fn, _patch_put(put_result) as mock_put_fn:
            result = runner.invoke(
                cli_app,
                ["agent", "publish", "--dir", str(tmp_path), "--update"],
            )

        assert result.exit_code == 0, result.output
        assert "Agent updated" in result.output
        assert "existing-uuid" in result.output

        # Verify GET was called with search param
        mock_get_fn.assert_called_once()
        get_call = mock_get_fn.call_args
        assert get_call[1].get("params", {}).get("search") == "test-agent"

        # Verify PUT was called with correct endpoint
        mock_put_fn.assert_called_once()
        put_call = mock_put_fn.call_args
        assert "existing-uuid" in put_call[0][0]

    def test_fails_if_no_yaml(self, tmp_path: Path):
        """Fails if observal-agent.yaml does not exist."""
        with _patch_config():
            result = runner.invoke(
                cli_app,
                ["agent", "publish", "--dir", str(tmp_path)],
            )
        assert result.exit_code != 0
        assert "not found" in result.output
