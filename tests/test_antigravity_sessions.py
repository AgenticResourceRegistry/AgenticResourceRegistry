# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for Antigravity CLI session helpers, reconcile, and hook push."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from observal_cli.sessions.antigravity import (
    find_antigravity_jsonl,
    find_sessions_dir,
    resolve_session_id,
)
from observal_cli.shared.utils import (
    resolve_antigravity_config_dir,
    resolve_antigravity_dir,
)

# ── resolve_antigravity_dir ───────────────────────────────────────────────────


class TestResolveAntigravityDir:
    def test_returns_dir_when_exists(self, tmp_path):
        ag_dir = tmp_path / ".gemini" / "antigravity-cli"
        ag_dir.mkdir(parents=True)
        result = resolve_antigravity_dir(tmp_path)
        assert result == ag_dir

    def test_returns_none_when_missing(self, tmp_path):
        result = resolve_antigravity_dir(tmp_path)
        assert result is None

    @patch("observal_cli.shared.utils.resolve_wsl_windows_home")
    def test_falls_back_to_wsl(self, mock_wsl, tmp_path):
        # tmp_path doesn't have .gemini, but WSL home does
        wsl_home = tmp_path / "win_home"
        ag_dir = wsl_home / ".gemini" / "antigravity-cli"
        ag_dir.mkdir(parents=True)
        mock_wsl.return_value = wsl_home
        result = resolve_antigravity_dir(tmp_path)
        assert result == ag_dir

    @patch("observal_cli.shared.utils.resolve_wsl_windows_home")
    def test_wsl_fallback_returns_none_if_no_dir(self, mock_wsl, tmp_path):
        mock_wsl.return_value = None
        result = resolve_antigravity_dir(tmp_path)
        assert result is None


# ── resolve_antigravity_config_dir ────────────────────────────────────────────


class TestResolveAntigravityConfigDir:
    def test_returns_config_dir_when_exists(self, tmp_path):
        config_dir = tmp_path / ".gemini" / "config"
        config_dir.mkdir(parents=True)
        result = resolve_antigravity_config_dir(tmp_path)
        assert result == config_dir

    @patch("observal_cli.shared.utils.resolve_wsl_windows_home", return_value=None)
    def test_returns_none_when_missing(self, _mock_wsl, tmp_path):
        result = resolve_antigravity_config_dir(tmp_path)
        assert result is None

    @patch("observal_cli.shared.utils.resolve_wsl_windows_home")
    def test_wsl_fallback(self, mock_wsl, tmp_path):
        wsl_home = tmp_path / "win_home"
        config_dir = wsl_home / ".gemini" / "config"
        config_dir.mkdir(parents=True)
        mock_wsl.return_value = wsl_home
        result = resolve_antigravity_config_dir(tmp_path)
        assert result == config_dir


# ── find_antigravity_jsonl ────────────────────────────────────────────────────


class TestFindAntigravityJsonl:
    def test_returns_path_when_transcript_exists(self, tmp_path):
        ag_dir = tmp_path / ".gemini" / "antigravity-cli"
        transcript = ag_dir / "brain" / "session-123" / ".system_generated" / "logs" / "transcript.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text('{"step_index": 0}')
        result = find_antigravity_jsonl("session-123", home=tmp_path)
        assert result == transcript

    def test_returns_none_when_no_transcript(self, tmp_path):
        ag_dir = tmp_path / ".gemini" / "antigravity-cli"
        ag_dir.mkdir(parents=True)
        result = find_antigravity_jsonl("session-123", home=tmp_path)
        assert result is None

    def test_returns_none_for_empty_session_id(self, tmp_path):
        result = find_antigravity_jsonl("", home=tmp_path)
        assert result is None

    def test_returns_none_when_ag_dir_missing(self, tmp_path):
        result = find_antigravity_jsonl("session-123", home=tmp_path)
        assert result is None


# ── find_sessions_dir ─────────────────────────────────────────────────────────


class TestFindSessionsDir:
    def test_returns_brain_dir(self, tmp_path):
        ag_dir = tmp_path / ".gemini" / "antigravity-cli"
        brain_dir = ag_dir / "brain"
        brain_dir.mkdir(parents=True)
        result = find_sessions_dir(home=tmp_path)
        assert result == brain_dir

    def test_returns_none_when_ag_dir_missing(self, tmp_path):
        result = find_sessions_dir(home=tmp_path)
        assert result is None


# ── resolve_session_id ────────────────────────────────────────────────────────


class TestResolveSessionId:
    def test_from_event_conversation_id(self, tmp_path):
        event = {"conversationId": "abc-123"}
        assert resolve_session_id(event, home=tmp_path) == "abc-123"

    def test_from_event_conversation_id_underscore(self, tmp_path):
        event = {"conversation_id": "def-456"}
        assert resolve_session_id(event, home=tmp_path) == "def-456"

    def test_from_event_session_id(self, tmp_path):
        event = {"session_id": "ghi-789"}
        assert resolve_session_id(event, home=tmp_path) == "ghi-789"

    def test_fallback_to_cached_file(self, tmp_path):
        observal_dir = tmp_path / ".observal"
        observal_dir.mkdir()
        (observal_dir / ".antigravity-session").write_text(json.dumps({"session_id": "cached-001"}))
        event = {}
        assert resolve_session_id(event, home=tmp_path) == "cached-001"

    def test_returns_empty_when_no_source(self, tmp_path):
        event = {}
        assert resolve_session_id(event, home=tmp_path) == ""

    def test_handles_corrupt_cache_file(self, tmp_path):
        observal_dir = tmp_path / ".observal"
        observal_dir.mkdir()
        (observal_dir / ".antigravity-session").write_text("not valid json")
        event = {}
        assert resolve_session_id(event, home=tmp_path) == ""


# ── antigravity_session_push ──────────────────────────────────────────────────


class TestAntigravitySessionPush:
    def test_hook_response_stop_event(self):
        from observal_cli.hooks.antigravity_session_push import _hook_response

        assert _hook_response("stop") == {"decision": ""}
        assert _hook_response("Stop") == {"decision": ""}
        assert _hook_response("session_end") == {"decision": ""}

    def test_hook_response_preinvocation(self):
        from observal_cli.hooks.antigravity_session_push import _hook_response

        assert _hook_response("preinvocation") == {}
        assert _hook_response("PreInvocation") == {}
        assert _hook_response("") == {}

    def test_resolve_path_no_conversion_on_unix_path(self):
        from observal_cli.hooks.antigravity_session_push import _resolve_path_for_platform

        assert _resolve_path_for_platform("/home/user/file.txt") == "/home/user/file.txt"
        assert _resolve_path_for_platform("") == ""

    @patch("os.name", "nt")
    def test_resolve_path_no_conversion_on_windows(self):
        from observal_cli.hooks.antigravity_session_push import _resolve_path_for_platform

        assert _resolve_path_for_platform("C:\\Users\\test\\file.txt") == "C:\\Users\\test\\file.txt"

    def test_resolve_path_windows_to_wsl_manual_fallback(self):
        from observal_cli.hooks.antigravity_session_push import _resolve_path_for_platform

        with patch("os.name", "posix"), patch("subprocess.run", side_effect=FileNotFoundError):
            result = _resolve_path_for_platform("C:\\Users\\test\\file.txt")
            assert result == "/mnt/c/Users/test/file.txt"

    def test_main_writes_json_stdout(self, tmp_path, monkeypatch, capsys):
        """main() always writes valid JSON to stdout, even with no config."""
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO("{}"))
        from observal_cli.hooks.antigravity_session_push import main

        main(home=tmp_path)
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert isinstance(result, dict)


# ── Server harness adapter ────────────────────────────────────────────────────────


class TestAntigravityServerAdapter:
    def test_format_config_basic(self):
        import sys

        sys.path.insert(0, str(Path(__file__).parent.parent / "observal-server"))
        from services.harness import ConfigContext
        from services.harness.antigravity import AntigravityAdapter

        adapter = AntigravityAdapter()
        assert adapter.harness_name == "antigravity"

        ctx = ConfigContext(
            agent=None,
            safe_name="test-agent",
            harness="antigravity",
            observal_url="http://localhost:8000",
            mcp_configs={"my-mcp": {"command": "node", "args": ["server.js"]}},
            rules_content="# Test rules",
            skill_configs=[{"name": "my-skill", "content": "---\ndescription: test\n---\n"}],
            options={"scope": "project"},
        )
        result = adapter.format_config(ctx)
        assert result["scope"] == "project"
        assert result["agent_profile"]["path"] == ".agents/agents/test-agent/agent.json"
        assert result["agent_profile"]["content"]["system_prompt"] == "# Test rules"
        assert "mcp_config" in result
        assert result["mcp_config"]["content"]["mcpServers"]["my-mcp"]["command"] == "node"
        assert len(result["skills"]) == 1

    def test_format_config_user_scope(self):
        import sys

        sys.path.insert(0, str(Path(__file__).parent.parent / "observal-server"))
        from services.harness import ConfigContext
        from services.harness.antigravity import AntigravityAdapter

        adapter = AntigravityAdapter()
        ctx = ConfigContext(
            agent=None,
            safe_name="test-agent",
            harness="antigravity",
            observal_url="http://localhost:8000",
            options={"scope": "user"},
        )
        result = adapter.format_config(ctx)
        assert result["scope"] == "user"
        assert result["agent_profile"]["path"] == "~/.gemini/antigravity-cli/agents/test-agent/agent.json"

    def test_format_config_with_model(self):
        import sys

        sys.path.insert(0, str(Path(__file__).parent.parent / "observal-server"))
        from services.harness import ConfigContext
        from services.harness.antigravity import AntigravityAdapter

        adapter = AntigravityAdapter()
        ctx = ConfigContext(
            agent=None,
            safe_name="test-agent",
            harness="antigravity",
            observal_url="http://localhost:8000",
            mcp_configs={},
            options={"_resolved_model": "gemini-2.5-pro"},
        )
        result = adapter.format_config(ctx)
        assert "mcp_config" not in result

    def test_format_config_warnings(self):
        import sys

        sys.path.insert(0, str(Path(__file__).parent.parent / "observal-server"))
        from services.harness import ConfigContext
        from services.harness.antigravity import AntigravityAdapter

        adapter = AntigravityAdapter()
        ctx = ConfigContext(
            agent=None,
            safe_name="test-agent",
            harness="antigravity",
            observal_url="http://localhost:8000",
            mcp_configs={},
            compatibility_warnings=["Some warning"],
            options={"_model_warnings": ["Model warning"]},
        )
        result = adapter.format_config(ctx)
        assert "Some warning" in result["_warnings"]
        assert "Model warning" in result["_warnings"]
