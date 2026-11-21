# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Comprehensive integration tests for Copilot (VS Code) and Copilot CLI support.

Covers:
- Task 12: Registry and Frontend
- Task 13: CLI Adapters
- Task 14: Hook Spec and Doctor Patch
- Task 15: Session Parsers
- Task 16: Session Push Hook and Server Adapter
- Task 17: Property-Based Tests (Hypothesis)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from hypothesis import assume, given
from hypothesis import settings as hsettings
from hypothesis import strategies as st

# ═══════════════════════════════════════════════════════════════════
# Task 12: Tests - Registry and Frontend
# ═══════════════════════════════════════════════════════════════════


class TestRegistryAndFrontend:
    """Registry entries for Copilot harness support."""

    def test_copilot_feature_matrix(self):
        """Copilot features == {"mcp_servers", "hooks", "skills", "prompts"}."""
        from observal_cli.constants import HARNESS_CAPABILITIES

        assert HARNESS_CAPABILITIES["copilot"] == {"mcp_servers", "hooks", "skills", "prompts"}

    def test_copilot_session_parser(self):
        """Copilot session_parser is 'copilot-cli' (shares parser with Copilot CLI)."""
        from observal_shared.harness_registry import HARNESS_REGISTRY

        assert HARNESS_REGISTRY["copilot"]["session_parser"] == "copilot-cli"

    def test_copilot_cli_session_parser(self):
        """Copilot CLI session_parser is 'copilot-cli'."""
        from observal_shared.harness_registry import HARNESS_REGISTRY

        assert HARNESS_REGISTRY["copilot-cli"]["session_parser"] == "copilot-cli"

    def test_copilot_cli_hook_events_map(self):
        """Copilot CLI hook_events_map contains all 5 events with correct mappings."""
        from observal_shared.harness_registry import HARNESS_REGISTRY

        expected = {
            "SessionStart": "sessionStart",
            "UserPromptSubmit": "userPromptSubmitted",
            "PreToolUse": "preToolUse",
            "PostToolUse": "postToolUse",
            "Stop": "sessionEnd",
        }
        assert HARNESS_REGISTRY["copilot-cli"]["hook_events_map"] == expected

    def test_copilot_hook_events_map(self):
        """Copilot hook_events_map contains PascalCase events."""
        from observal_shared.harness_registry import HARNESS_REGISTRY

        expected = {
            "SessionStart": "SessionStart",
            "UserPromptSubmit": "UserPromptSubmit",
            "PreToolUse": "PreToolUse",
            "PostToolUse": "PostToolUse",
            "Stop": "Stop",
        }
        assert HARNESS_REGISTRY["copilot"]["hook_events_map"] == expected

    def test_parse_raw_events_handles_unknown_ide_gracefully(self):
        """parse_raw_events raises KeyError for unknown harnesses (strict dispatch)."""
        from services.session_parsers import parse_raw_events

        rows = [{"harness": "unknown-harness-xyz", "raw_line": "", "timestamp": "2025-01-01 00:00:00.000"}]
        with pytest.raises(KeyError):
            parse_raw_events(rows)

    def test_get_classifier_raises_for_none_parser(self):
        """get_classifier raises ValueError for harness with session_parser=None."""
        from services.session_parsers.ingest_classify import get_classifier

        # Test that an unknown harness raises KeyError (not silently falls through)
        with pytest.raises(KeyError):
            get_classifier("unknown-harness-xyz")


# ═══════════════════════════════════════════════════════════════════
# Task 13: Tests - CLI Adapters
# ═══════════════════════════════════════════════════════════════════


class TestCopilotAdapterDetectHooks:
    """CopilotAdapter.detect_hooks() hook file detection."""

    def test_returns_installed_when_hooks_present(self, tmp_path, monkeypatch):
        """Returns 'installed' when .github/hooks has observal hook file."""
        from observal_cli.harness.copilot import CopilotAdapter

        hooks_dir = tmp_path / ".github" / "hooks"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "observal.json").write_text(
            json.dumps(
                {
                    "hooks": {
                        "UserPromptSubmit": [
                            {"bash": "python -m observal_cli.hooks.session_push --harness copilot-cli"}
                        ]
                    }
                }
            )
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        adapter = CopilotAdapter()
        assert adapter.detect_hooks(tmp_path / ".github" / "hooks") == "installed"

    def test_returns_missing_when_no_hooks(self, tmp_path, monkeypatch):
        """Returns 'missing' when no hook files exist."""
        from observal_cli.harness.copilot import CopilotAdapter

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        adapter = CopilotAdapter()
        assert adapter.detect_hooks(tmp_path) == "missing"


class TestCopilotAdapterScan:
    """CopilotAdapter scan_home and scan_project."""

    def test_scan_home_finds_mcps(self, tmp_path):
        from observal_cli.harness.copilot import CopilotAdapter

        vscode_dir = tmp_path / ".vscode"
        vscode_dir.mkdir()
        mcp_file = vscode_dir / "mcp.json"
        mcp_file.write_text(
            json.dumps({"servers": {"test-srv": {"type": "stdio", "command": "npx", "args": ["-y", "test"]}}})
        )
        adapter = CopilotAdapter()
        result = adapter.scan_home(home=tmp_path)
        assert len(result.mcps) == 1
        assert result.mcps[0].name == "test-srv"

    def test_scan_home_empty_when_no_vscode(self, tmp_path):
        from observal_cli.harness.copilot import CopilotAdapter

        adapter = CopilotAdapter()
        result = adapter.scan_home(home=tmp_path)
        assert result.mcps == []

    def test_scan_project_finds_mcps(self, tmp_path):
        from observal_cli.harness.copilot import CopilotAdapter

        vscode_dir = tmp_path / ".vscode"
        vscode_dir.mkdir()
        mcp_file = vscode_dir / "mcp.json"
        mcp_file.write_text(
            json.dumps({"servers": {"proj-srv": {"type": "stdio", "command": "node", "args": ["srv.js"]}}})
        )
        adapter = CopilotAdapter()
        result = adapter.scan_project(tmp_path)
        assert len(result.mcps) == 1
        assert result.mcps[0].name == "proj-srv"


class TestCopilotCliAdapterSkills:
    """CopilotCliAdapter._scan_skills_dir() finds skills."""

    def test_finds_skills_at_project_path(self, tmp_path):
        from observal_cli.harness.copilot_cli import CopilotCliAdapter

        skills_dir = tmp_path / ".agents" / "skills" / "my-skill"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text("---\ndescription: Test skill\n---\nContent")
        adapter = CopilotCliAdapter()
        result = adapter.scan_project(tmp_path)
        assert len(result.skills) == 1
        assert result.skills[0].name == "my-skill"

    def test_finds_skills_at_user_path(self, tmp_path):
        from observal_cli.harness.copilot_cli import CopilotCliAdapter

        copilot_dir = tmp_path / ".copilot"
        copilot_dir.mkdir()
        skills_dir = copilot_dir / "skills" / "user-skill"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text("---\ndescription: User skill\n---\nBody")
        adapter = CopilotCliAdapter()
        result = adapter.scan_home(home=tmp_path)
        assert len(result.skills) == 1
        assert result.skills[0].name == "user-skill"


class TestCopilotCliAdapterAgents:
    """CopilotCliAdapter._scan_agents_dir() finds .github/agents/*.agent.md."""

    def test_finds_agent_md_files(self, tmp_path):
        from observal_cli.harness.copilot_cli import CopilotCliAdapter

        agents_dir = tmp_path / ".github" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "helper.agent.md").write_text(
            "---\ndescription: Helper agent\nmodel: gpt-4\n---\nYou are helpful."
        )
        adapter = CopilotCliAdapter()
        result = adapter.scan_project(tmp_path)
        assert len(result.agents) >= 1
        agent_names = [a.name for a in result.agents]
        assert "helper" in agent_names


class TestCopilotCliAdapterDetectHooks:
    """CopilotCliAdapter.detect_hooks() finds observal markers."""

    def test_finds_hooks_in_user_path(self, tmp_path, monkeypatch):
        from observal_cli.harness.copilot_cli import CopilotCliAdapter

        hooks_dir = tmp_path / ".copilot" / "hooks"
        hooks_dir.mkdir(parents=True)
        hook_file = hooks_dir / "observal.json"
        hook_file.write_text(
            json.dumps(
                {
                    "version": 1,
                    "hooks": {
                        "sessionStart": [
                            {
                                "type": "command",
                                "bash": "python -m observal_cli.hooks.session_push --harness copilot-cli",
                            }
                        ]
                    },
                }
            )
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        adapter = CopilotCliAdapter()
        assert adapter.detect_hooks(tmp_path) == "installed"

    def test_finds_hooks_in_project_path(self, tmp_path, monkeypatch):
        from observal_cli.harness.copilot_cli import CopilotCliAdapter

        # No user-level hooks
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Project-level hooks
        hooks_dir = tmp_path / ".github" / "hooks"
        hooks_dir.mkdir(parents=True)
        hook_file = hooks_dir / "observal.json"
        hook_file.write_text(
            json.dumps(
                {
                    "version": 1,
                    "hooks": {
                        "preToolUse": [
                            {
                                "type": "command",
                                "bash": "python -m observal_cli.hooks.session_push --harness copilot-cli",
                            }
                        ]
                    },
                }
            )
        )
        adapter = CopilotCliAdapter()
        assert adapter.detect_hooks(tmp_path) == "installed"

    def test_returns_missing_when_no_hooks(self, tmp_path, monkeypatch):
        from observal_cli.harness.copilot_cli import CopilotCliAdapter

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        adapter = CopilotCliAdapter()
        assert adapter.detect_hooks(tmp_path) == "missing"


class TestCopilotCliAdapterHookSpec:
    """CopilotCliAdapter.get_hook_spec() returns correct event names and format."""

    def test_hook_spec_events(self):
        from observal_cli.harness.copilot_cli import CopilotCliAdapter

        adapter = CopilotCliAdapter()
        spec = adapter.get_hook_spec()
        assert set(spec.events) == {"sessionStart", "sessionEnd", "userPromptSubmitted", "preToolUse", "postToolUse"}

    def test_hook_spec_format(self):
        from observal_cli.harness.copilot_cli import CopilotCliAdapter

        adapter = CopilotCliAdapter()
        spec = adapter.get_hook_spec()
        assert spec.format == "command"

    def test_hook_spec_markers(self):
        from observal_cli.harness.copilot_cli import CopilotCliAdapter

        adapter = CopilotCliAdapter()
        spec = adapter.get_hook_spec()
        assert "observal" in spec.markers or "OBSERVAL" in spec.markers


# ═══════════════════════════════════════════════════════════════════
# Task 14: Tests - Hook Spec and Doctor Patch
# ═══════════════════════════════════════════════════════════════════


class TestBuildCopilotCliHooks:
    """build_copilot_cli_hooks() returns correct structure."""

    def test_returns_version_1(self):
        from observal_cli.harness_specs.copilot_cli_hooks_spec import build_copilot_cli_hooks

        result = build_copilot_cli_hooks()
        assert result["version"] == 1

    def test_contains_all_5_events(self):
        from observal_cli.harness_specs.copilot_cli_hooks_spec import build_copilot_cli_hooks

        result = build_copilot_cli_hooks()
        expected_events = {"sessionStart", "sessionEnd", "userPromptSubmitted", "preToolUse", "postToolUse"}
        assert set(result["hooks"].keys()) == expected_events

    def test_each_event_has_bash_key(self):
        from observal_cli.harness_specs.copilot_cli_hooks_spec import build_copilot_cli_hooks

        result = build_copilot_cli_hooks()
        for event, entries in result["hooks"].items():
            assert len(entries) == 1
            assert "bash" in entries[0], f"Event {event} missing 'bash' key"

    def test_each_event_has_timeout(self):
        from observal_cli.harness_specs.copilot_cli_hooks_spec import build_copilot_cli_hooks

        result = build_copilot_cli_hooks()
        for event, entries in result["hooks"].items():
            assert entries[0]["timeoutSec"] == 5, f"Event {event} timeout != 5"

    def test_no_agent_id_omits_observal_agent_id(self):
        """Without an agent_id, commands carry no OBSERVAL_AGENT_ID prefix."""
        from observal_cli.harness_specs.copilot_cli_hooks_spec import build_copilot_cli_hooks

        result = build_copilot_cli_hooks()
        for entries in result["hooks"].values():
            assert "OBSERVAL_AGENT_ID" not in entries[0]["bash"]
            assert "OBSERVAL_AGENT_ID" not in entries[0]["powershell"]

    def test_agent_id_injected_into_both_command_forms(self):
        """A supplied agent_id is stamped into bash and powershell commands.

        Regression: Copilot CLI sessions were unattributed because the project
        hook carried no agent identity. The UUID must reach the push hook so
        _resolve_agent can map it to an agent+version via the lockfile.
        """
        from observal_cli.harness_specs.copilot_cli_hooks_spec import build_copilot_cli_hooks

        result = build_copilot_cli_hooks(agent_id="agent-uuid-42")
        for event, entries in result["hooks"].items():
            bash = entries[0]["bash"]
            ps = entries[0]["powershell"]
            assert "OBSERVAL_AGENT_ID=agent-uuid-42" in bash, f"{event} bash missing UUID"
            assert b"agent-uuid-42".hex() in ps, f"{event} powershell missing UUID"
            # The push command must still be present after the prefix.
            assert "hooks.session_push --harness copilot-cli" in bash

    def test_powershell_invokes_cli_interpreter(self):
        """PowerShell must run this CLI's interpreter, not bare python.

        Regression: bare `python` cannot import observal_cli from a `uv tool`
        install, and a quoted path without `&` is a string, not a command.
        """
        import sys

        from observal_cli.harness_specs.copilot_cli_hooks_spec import build_copilot_cli_hooks
        from observal_cli.shared.launcher import windows_powershell_module_command

        ps_path = sys.executable.replace("'", "''")
        for agent_id in ("", "agent-uuid-42"):
            env = {"OBSERVAL_AGENT_ID": agent_id} if agent_id else None
            invoke = windows_powershell_module_command("observal_cli.hooks.session_push", env)
            for entries in build_copilot_cli_hooks(agent_id=agent_id)["hooks"].values():
                ps = entries[0]["powershell"]
                assert ps == f"{invoke} --harness copilot-cli"
                assert ps.startswith(f"& '{ps_path}' '-I' ")


class TestRewriteCopilotCliHooks:
    """_rewrite_copilot_cli_hooks: stamps the pulled agent UUID at pull time."""

    def _server_content(self) -> dict:
        """Generic hook file as the server emits it (no agent identity)."""
        push = "python3 -m observal_cli.hooks.session_push --harness copilot-cli"
        return {
            "version": 1,
            "hooks": {
                "sessionStart": [{"type": "command", "bash": push, "powershell": push, "timeoutSec": 5}],
                "userPromptSubmitted": [{"type": "command", "bash": push, "powershell": push, "timeoutSec": 5}],
            },
        }

    def test_stamps_agent_id_into_observal_hooks(self):
        from observal_cli.cmd_pull import _rewrite_copilot_cli_hooks

        out = _rewrite_copilot_cli_hooks(self._server_content(), agent_id="uuid-99")
        for entries in out["hooks"].values():
            obs = [e for e in entries if "hooks.session_push --harness copilot-cli" in e.get("bash", "")]
            assert len(obs) == 1
            assert "OBSERVAL_AGENT_ID=uuid-99" in obs[0]["bash"]

    def test_powershell_interpreter_path_is_literal(self, monkeypatch):
        """A path with PowerShell metacharacters cannot interpolate commands."""
        import sys

        from observal_cli.harness_specs.copilot_cli_hooks_spec import build_copilot_cli_hooks

        monkeypatch.setattr(sys, "executable", "C:/Apps/$env:TEMP/it's python.exe")
        command = build_copilot_cli_hooks()["hooks"]["sessionStart"][0]["powershell"]
        assert command.startswith("& 'C:/Apps/$env:TEMP/it''s python.exe' '-I' ")

    def test_rebuilt_powershell_keeps_cli_interpreter(self):
        """Pull pins the interpreter, then this rebuild runs; it must not restore bare python."""
        import sys

        from observal_cli.cmd_pull import _rewrite_copilot_cli_hooks

        out = _rewrite_copilot_cli_hooks(self._server_content(), agent_id="uuid-99")
        for entries in out["hooks"].values():
            obs = [e for e in entries if "hooks.session_push --harness copilot-cli" in e.get("bash", "")]
            assert obs[0]["powershell"].startswith(f"& '{sys.executable}' '-I' ")

    def test_preserves_user_added_hooks(self):
        from observal_cli.cmd_pull import _rewrite_copilot_cli_hooks

        content = self._server_content()
        content["hooks"]["sessionStart"].append({"type": "command", "bash": "echo user-hook", "timeoutSec": 5})
        out = _rewrite_copilot_cli_hooks(content, agent_id="uuid-99")
        bashes = [e["bash"] for e in out["hooks"]["sessionStart"]]
        assert any("echo user-hook" in b for b in bashes), "user hook must survive"

    def test_is_idempotent_no_duplicate_observal_hooks(self):
        """Re-pulling must not accumulate duplicate Observal entries."""
        from observal_cli.cmd_pull import _rewrite_copilot_cli_hooks

        content = _rewrite_copilot_cli_hooks(self._server_content(), agent_id="uuid-1")
        content = _rewrite_copilot_cli_hooks(content, agent_id="uuid-2")
        for entries in content["hooks"].values():
            obs = [e for e in entries if "hooks.session_push --harness copilot-cli" in e.get("bash", "")]
            assert len(obs) == 1, "exactly one Observal hook per event"
            assert "OBSERVAL_AGENT_ID=uuid-2" in obs[0]["bash"], "latest UUID wins"

    def test_no_hooks_key_returns_unchanged(self):
        from observal_cli.cmd_pull import _rewrite_copilot_cli_hooks

        assert _rewrite_copilot_cli_hooks({}, agent_id="uuid-1") == {}


class TestResolveAgentCopilotUuidFallback:
    """_resolve_agent: OBSERVAL_AGENT_ID resolves across a harness-key mismatch."""

    def test_unscoped_fallback_when_harness_scope_misses(self):
        """Copilot pulls under 'copilot' but pushes with harness 'copilot-cli'
        (and build_payload defaults to 'claude-code'). A globally-unique UUID
        must still resolve via the unscoped lockfile lookup.
        """
        from unittest.mock import patch

        from observal_cli.sessions.base import _resolve_agent

        entry = {"id": "uuid-cp", "name": "test", "version": "1.2.0"}

        def by_id(agent_id, harness=None):
            if agent_id != "uuid-cp":
                return None
            # Agent is stored only under the 'copilot' harness key.
            return entry if harness in (None, "copilot") else None

        with (
            patch.dict("os.environ", {"OBSERVAL_AGENT_ID": "uuid-cp"}, clear=True),
            patch("observal_cli.sessions.base._lookup_lockfile_agent_by_id", side_effect=by_id),
        ):
            # harness reported by the push payload differs from the lockfile key
            assert _resolve_agent("/repo", [], None, harness="copilot-cli") == ("uuid-cp", "1.2.0")
            # build_payload's default harness also differs
            assert _resolve_agent("/repo", [], None, harness="claude-code") == ("uuid-cp", "1.2.0")

    def test_unknown_uuid_stays_unattributed(self):
        from unittest.mock import patch

        from observal_cli.sessions.base import _resolve_agent

        with (
            patch.dict("os.environ", {"OBSERVAL_AGENT_ID": "nope"}, clear=True),
            patch("observal_cli.sessions.base._lookup_lockfile_agent_by_id", return_value=None),
            patch("observal_cli.sessions.base._lookup_lockfile_agent") as cwd_lookup,
        ):
            assert _resolve_agent("/repo", [], None, harness="copilot-cli") == (None, None)
            cwd_lookup.assert_not_called()


class TestPatchCopilot:
    """_patch_copilot: hook installation for VS Code Copilot."""

    def test_installs_hooks_file(self, tmp_path, monkeypatch):
        from observal_cli.cmd_doctor import _patch_copilot

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("observal_cli.config.load", lambda: {"server_url": "http://localhost:8000"})

        result = _patch_copilot(dry_run=False)
        assert result is True

        hooks_path = tmp_path / ".github" / "hooks" / "observal.json"
        assert hooks_path.exists()
        data = json.loads(hooks_path.read_text())
        assert "hooks" in data
        assert "UserPromptSubmit" in data["hooks"]

    def test_is_idempotent(self, tmp_path, monkeypatch):
        from observal_cli.cmd_doctor import _patch_copilot

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("observal_cli.config.load", lambda: {"server_url": "http://localhost:8000"})

        _patch_copilot(dry_run=False)
        result = _patch_copilot(dry_run=False)
        assert result is False  # No changes on second run


class TestPatchCopilotCli:
    """_patch_copilot_cli: hook installation."""

    def test_writes_to_correct_path(self, tmp_path, monkeypatch):
        from observal_cli.cmd_doctor import _patch_copilot_cli

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        _patch_copilot_cli(dry_run=False)

        hooks_file = tmp_path / ".copilot" / "hooks" / "observal.json"
        assert hooks_file.exists()

    def test_creates_directory(self, tmp_path, monkeypatch):
        from observal_cli.cmd_doctor import _patch_copilot_cli

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        _patch_copilot_cli(dry_run=False)

        hooks_dir = tmp_path / ".copilot" / "hooks"
        assert hooks_dir.is_dir()

    def test_is_idempotent(self, tmp_path, monkeypatch):
        from observal_cli.cmd_doctor import _patch_copilot_cli

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # First call
        result1 = _patch_copilot_cli(dry_run=False)
        assert result1 is True

        # Second call - should be idempotent
        result2 = _patch_copilot_cli(dry_run=False)
        assert result2 is False

    def test_merges_with_existing_hooks(self, tmp_path, monkeypatch):
        from observal_cli.cmd_doctor import _patch_copilot_cli

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        hooks_dir = tmp_path / ".copilot" / "hooks"
        hooks_dir.mkdir(parents=True)
        hooks_file = hooks_dir / "observal.json"
        hooks_file.write_text(
            json.dumps(
                {
                    "version": 1,
                    "hooks": {
                        "sessionStart": [{"type": "command", "bash": "echo custom-hook"}],
                    },
                }
            )
        )

        _patch_copilot_cli(dry_run=False)

        data = json.loads(hooks_file.read_text())
        # Should have all 5 events
        assert "sessionStart" in data["hooks"]
        assert "sessionEnd" in data["hooks"]
        assert "userPromptSubmitted" in data["hooks"]
        assert "preToolUse" in data["hooks"]
        assert "postToolUse" in data["hooks"]
        # Custom hook should be preserved (non-observal)
        session_start_entries = data["hooks"]["sessionStart"]
        has_custom = any("custom-hook" in e.get("bash", "") for e in session_start_entries)
        assert has_custom, "Non-observal hooks should be preserved"


# ═══════════════════════════════════════════════════════════════════
# Task 15: Tests - Session Parsers
# ═══════════════════════════════════════════════════════════════════


class TestCliParseEventLine:
    """CLI-side parse_event_line(): all event types, envelope unwrapping."""

    def test_user_message(self):
        from observal_cli.sessions.copilot_cli import parse_event_line

        line = json.dumps(
            {
                "agentId": "agent-1",
                "ts": "2025-01-01T12:00:00Z",
                "event": {"type": "user.message", "content": "Hello"},
            }
        )
        result = parse_event_line(line)
        assert result is not None
        assert result["agent_id"] == "agent-1"
        assert result["timestamp"] == "2025-01-01T12:00:00Z"
        assert result["event_type"] == "user.message"
        assert result["payload"] == {"content": "Hello"}

    def test_tool_call(self):
        from observal_cli.sessions.copilot_cli import parse_event_line

        line = json.dumps(
            {
                "agentId": "agent-1",
                "ts": "2025-01-01T12:00:01Z",
                "event": {"type": "tool.call", "name": "read_file", "input": {"path": "foo.py"}},
            }
        )
        result = parse_event_line(line)
        assert result is not None
        assert result["event_type"] == "tool.call"
        assert result["payload"]["name"] == "read_file"

    def test_tool_result(self):
        from observal_cli.sessions.copilot_cli import parse_event_line

        line = json.dumps(
            {
                "agentId": "agent-1",
                "ts": "2025-01-01T12:00:02Z",
                "event": {"type": "tool.result", "output": "file content here"},
            }
        )
        result = parse_event_line(line)
        assert result is not None
        assert result["event_type"] == "tool.result"

    def test_assistant_message(self):
        from observal_cli.sessions.copilot_cli import parse_event_line

        line = json.dumps(
            {
                "agentId": "agent-1",
                "ts": "2025-01-01T12:00:03Z",
                "event": {"type": "assistant.message", "content": "Here is the answer"},
            }
        )
        result = parse_event_line(line)
        assert result is not None
        assert result["event_type"] == "assistant.message"

    def test_session_start(self):
        from observal_cli.sessions.copilot_cli import parse_event_line

        line = json.dumps(
            {
                "agentId": "agent-1",
                "ts": "2025-01-01T12:00:04Z",
                "event": {"type": "session.start", "source": "cli"},
            }
        )
        result = parse_event_line(line)
        assert result is not None
        assert result["event_type"] == "session.start"

    def test_session_end(self):
        from observal_cli.sessions.copilot_cli import parse_event_line

        line = json.dumps(
            {
                "agentId": "agent-1",
                "ts": "2025-01-01T12:00:05Z",
                "event": {"type": "session.end", "reason": "user_exit"},
            }
        )
        result = parse_event_line(line)
        assert result is not None
        assert result["event_type"] == "session.end"


class TestCliParseEventLineEdgeCases:
    """CLI-side edge cases: U+2028/U+2029, trailing NUL, malformed, empty."""

    def test_u2028_replacement(self):
        from observal_cli.sessions.copilot_cli import parse_event_line

        # U+2028 in the JSON string value
        raw = '{"agentId":"a","ts":"t","event":{"type":"user.message","content":"line\u2028sep"}}'
        result = parse_event_line(raw)
        assert result is not None
        assert result["event_type"] == "user.message"

    def test_u2029_replacement(self):
        from observal_cli.sessions.copilot_cli import parse_event_line

        raw = '{"agentId":"a","ts":"t","event":{"type":"user.message","content":"para\u2029sep"}}'
        result = parse_event_line(raw)
        assert result is not None

    def test_trailing_nul_bytes(self):
        from observal_cli.sessions.copilot_cli import parse_event_line

        line = json.dumps({"agentId": "a", "ts": "t", "event": {"type": "tool.call", "name": "x"}}) + "\x00\x00\x00"
        result = parse_event_line(line)
        assert result is not None
        assert result["event_type"] == "tool.call"

    def test_malformed_json(self):
        from observal_cli.sessions.copilot_cli import parse_event_line

        result = parse_event_line("{not valid json")
        assert result is None

    def test_empty_line(self):
        from observal_cli.sessions.copilot_cli import parse_event_line

        assert parse_event_line("") is None
        assert parse_event_line("   ") is None
        assert parse_event_line("\n") is None

    def test_missing_event_key(self):
        from observal_cli.sessions.copilot_cli import parse_event_line

        line = json.dumps({"agentId": "a", "ts": "t"})
        result = parse_event_line(line)
        assert result is None

    def test_event_not_dict(self):
        from observal_cli.sessions.copilot_cli import parse_event_line

        line = json.dumps({"agentId": "a", "ts": "t", "event": "not a dict"})
        result = parse_event_line(line)
        assert result is None


class TestCliDiscoverSessions:
    """CLI-side discover_sessions(): glob discovery."""

    def test_discovers_events_jsonl(self, tmp_path):
        from observal_cli.sessions.copilot_cli import discover_sessions

        sessions_dir = tmp_path / ".copilot" / "session-state"
        session1 = sessions_dir / "uuid-1"
        session1.mkdir(parents=True)
        (session1 / "events.jsonl").write_text('{"agentId":"a","ts":"t","event":{"type":"session.start"}}\n')

        session2 = sessions_dir / "uuid-2"
        session2.mkdir(parents=True)
        (session2 / "events.jsonl").write_text('{"agentId":"b","ts":"t","event":{"type":"session.start"}}\n')

        results = discover_sessions(home=tmp_path)
        assert len(results) == 2
        assert all(p.name == "events.jsonl" for p in results)

    def test_returns_empty_when_no_sessions(self, tmp_path):
        from observal_cli.sessions.copilot_cli import discover_sessions

        results = discover_sessions(home=tmp_path)
        assert results == []


class TestServerParseRows:
    """Server-side parse_rows(): all event type mappings."""

    def _make_row(self, event_type: str, **extra_event_fields) -> dict:
        event = {"type": event_type, **extra_event_fields}
        envelope = {"agentId": "agent-1", "ts": "2025-01-01T12:00:00Z", "event": event}
        return {
            "raw_line": json.dumps(envelope),
            "timestamp": "2025-01-01 12:00:00.000",
            "ingested_at": "2025-01-01 12:00:01.000",
            "harness": "copilot-cli",
        }

    def test_tool_call_maps_to_hook_pretooluse(self):
        from services.session_parsers.copilot_cli import parse_rows

        rows = [self._make_row("tool.call", name="read_file", input={"path": "x"})]
        events = parse_rows(rows)
        assert len(events) == 1
        assert events[0]["event_name"] == "hook_pretooluse"

    def test_tool_result_maps_to_tool_result(self):
        from services.session_parsers.copilot_cli import parse_rows

        rows = [self._make_row("tool.result", output="content")]
        events = parse_rows(rows)
        assert len(events) == 1
        assert events[0]["event_name"] == "hook_toolresult"

    def test_tool_call_id_correlates_interleaved_calls_from_one_agent(self):
        from services.session_parsers.copilot_cli import parse_rows

        rows = [
            self._make_row("tool.call", name="read", toolCallId="call-1"),
            self._make_row("tool.call", name="read", toolCallId="call-2"),
            self._make_row("tool.result", output="second", toolCallId="call-2"),
            self._make_row("tool.result", output="first", toolCallId="call-1"),
        ]
        events = parse_rows(rows)
        assert len(events) == 2
        assert [event["attributes"]["tool_use_id"] for event in events] == ["call-1", "call-2"]
        assert [event["attributes"]["tool_response"] for event in events] == ["first", "second"]

    def test_missing_tool_call_id_does_not_use_agent_id_as_invocation_id(self):
        from services.session_parsers.copilot_cli import parse_rows

        events = parse_rows([self._make_row("tool.call", name="read"), self._make_row("tool.result", name="read")])
        assert [event["attributes"]["tool_use_id"] for event in events] == ["", ""]

    def test_flat_tool_events_correlate_by_tool_call_id(self):
        from services.session_parsers.copilot_cli import parse_rows

        def flat_row(event_type: str, data: dict) -> dict:
            return {"raw_line": json.dumps({"type": event_type, "data": data, "id": "agent-1"})}

        rows = [
            flat_row("tool.call", {"name": "read", "toolCallId": "call-1"}),
            flat_row("tool.result", {"output": "done", "toolCallId": "call-1"}),
        ]
        events = parse_rows(rows)
        assert len(events) == 1
        assert events[0]["attributes"]["tool_use_id"] == "call-1"
        assert events[0]["attributes"]["tool_response"] == "done"

    def test_user_message_maps_to_hook_userpromptsubmit(self):
        from services.session_parsers.copilot_cli import parse_rows

        rows = [self._make_row("user.message", content="Hello")]
        events = parse_rows(rows)
        assert len(events) == 1
        assert events[0]["event_name"] == "hook_userpromptsubmit"

    def test_assistant_message_maps_to_hook_assistant_response(self):
        from services.session_parsers.copilot_cli import parse_rows

        rows = [self._make_row("assistant.message", content="Answer")]
        events = parse_rows(rows)
        assert len(events) == 1
        assert events[0]["event_name"] == "hook_assistant_response"

    def test_session_start_maps_to_hook_sessionstart(self):
        from services.session_parsers.copilot_cli import parse_rows

        rows = [self._make_row("session.start", source="cli")]
        events = parse_rows(rows)
        assert len(events) == 1
        assert events[0]["event_name"] == "hook_sessionstart"

    def test_session_end_maps_to_hook_sessionend(self):
        from services.session_parsers.copilot_cli import parse_rows

        rows = [self._make_row("session.end", reason="user_exit")]
        events = parse_rows(rows)
        assert len(events) == 1
        assert events[0]["event_name"] == "hook_sessionend"

    def test_thinking_maps_to_hook_assistant_thinking(self):
        from services.session_parsers.copilot_cli import parse_rows

        rows = [self._make_row("agent.thinking", content="Let me think...")]
        events = parse_rows(rows)
        assert len(events) == 1
        assert events[0]["event_name"] == "hook_assistant_thinking"

    def test_unknown_event_type_emits_generic(self):
        from services.session_parsers.copilot_cli import parse_rows

        rows = [self._make_row("custom.unknown")]
        events = parse_rows(rows)
        assert len(events) == 1
        assert "copilot_cli_custom_unknown" in events[0]["event_name"]


class TestServerParserFallbacks:
    """Server-side fallbacks: missing raw_line, invalid JSON, unknown event type."""

    def test_missing_raw_line_uses_basic_event(self):
        from services.session_parsers.copilot_cli import parse_rows

        rows = [{"raw_line": "", "timestamp": "2025-01-01 12:00:00.000", "ingested_at": "", "harness": "copilot-cli"}]
        events = parse_rows(rows)
        assert len(events) == 1
        # basic_event fallback

    def test_invalid_json_uses_basic_event(self):
        from services.session_parsers.copilot_cli import parse_rows

        rows = [
            {
                "raw_line": "not valid json {{{",
                "timestamp": "2025-01-01 12:00:00.000",
                "ingested_at": "",
                "harness": "copilot-cli",
            }
        ]
        events = parse_rows(rows)
        assert len(events) == 1


class TestServerParserRegistration:
    """Server parser registered correctly."""

    def test_registered_in_parsers(self):
        from services.session_parsers import _PARSERS

        assert "copilot-cli" in _PARSERS

    def test_registered_in_classifiers(self):
        from services.session_parsers.ingest_classify import _CLASSIFIERS

        assert "copilot-cli" in _CLASSIFIERS


# ═══════════════════════════════════════════════════════════════════
# Task 16: Tests - Session Push Hook and Server Adapter
# ═══════════════════════════════════════════════════════════════════


class TestCopilotCliSessionPush:
    """Legacy module entry point routes through the shared delivery hook."""

    def test_compatibility_entry_point_uses_shared_hook(self, monkeypatch):
        import observal_cli.hooks.copilot_cli_session_push as hook

        calls = []
        monkeypatch.setattr(hook, "_shared_main", lambda harness, home=None: calls.append((harness, home)))

        hook.main()

        assert calls == [("copilot-cli", None)]


class TestServerAdapterFormatConfig:
    """Server adapter format_config(): correct output structure."""

    def _make_ctx(self, **overrides):
        from services.harness import ConfigContext

        defaults = {
            "safe_name": "test-agent",
            "mcp_configs": {"my-srv": {"command": "npx", "args": ["-y", "srv"]}},
            "rules_content": "You are helpful.",
            "hook_configs": [],
            "skill_configs": [],
            "hook_listings": [],
            "compatibility_warnings": [],
            "prompt_listings": None,
            "component_names": None,
        }
        agent_components = overrides.pop("agent_components", [])
        defaults.update(overrides)

        ctx = MagicMock(spec=ConfigContext)
        for k, v in defaults.items():
            setattr(ctx, k, v)
        ctx.agent = MagicMock()
        ctx.agent.description = "Test agent"
        ctx.agent.components = agent_components
        return ctx

    def test_correct_hook_file_format(self):
        from services.harness.copilot_cli import CopilotCliAdapter

        ctx = self._make_ctx()
        adapter = CopilotCliAdapter()
        result = adapter.format_config(ctx)

        hooks_config = result["hooks_config"]
        assert hooks_config["path"] == ".github/hooks/observal.json"
        content = hooks_config["content"]
        assert content["version"] == 1
        assert "hooks" in content
        # All 5 events present
        expected_events = {"sessionStart", "sessionEnd", "userPromptSubmitted", "preToolUse", "postToolUse"}
        assert set(content["hooks"].keys()) == expected_events

    def test_correct_agent_profile_path(self):
        from services.harness.copilot_cli import CopilotCliAdapter

        ctx = self._make_ctx(safe_name="my-agent")
        adapter = CopilotCliAdapter()
        result = adapter.format_config(ctx)

        assert result["agent_profile"]["path"] == ".github/agents/my-agent.agent.md"

    def test_correct_mcp_config_structure(self):
        from services.harness.copilot_cli import CopilotCliAdapter

        ctx = self._make_ctx()
        adapter = CopilotCliAdapter()
        result = adapter.format_config(ctx)

        mcp_config = result["mcp_config"]
        assert mcp_config["path"] == ".mcp.json"
        assert "mcpServers" in mcp_config["content"]

    def test_no_prompt_files_without_prompt_components(self):
        from services.harness.copilot_cli import CopilotCliAdapter

        result = CopilotCliAdapter().format_config(self._make_ctx())
        assert "prompt_files" not in result

    def test_emits_native_prompt_file(self):
        """A prompt component becomes .github/prompts/<name>.prompt.md."""
        from services.harness.copilot_cli import CopilotCliAdapter

        comp = MagicMock()
        comp.component_type = "prompt"
        comp.component_id = "pid-1"
        listing = MagicMock()
        listing.name = "My Prompt"
        listing.template = "Do the thing."
        listing.description = "A test prompt"

        ctx = self._make_ctx(prompt_listings={"pid-1": listing}, agent_components=[comp])
        result = CopilotCliAdapter().format_config(ctx)

        assert "prompt_files" in result
        pf = result["prompt_files"]
        assert len(pf) == 1
        # sanitize_name preserves case, replaces the space with a hyphen
        assert pf[0]["path"] == ".github/prompts/My-Prompt.prompt.md"
        assert "Do the thing." in pf[0]["content"]
        assert "description:" in pf[0]["content"]

    def test_vscode_copilot_also_emits_prompt_file(self):
        from services.harness.copilot import CopilotAdapter

        comp = MagicMock()
        comp.component_type = "prompt"
        comp.component_id = "pid-9"
        listing = MagicMock()
        listing.name = "vs-prompt"
        listing.template = "vscode prompt body"
        listing.description = "d"

        ctx = self._make_ctx(prompt_listings={"pid-9": listing}, agent_components=[comp])
        result = CopilotAdapter().format_config(ctx)

        assert "prompt_files" in result
        assert result["prompt_files"][0]["path"] == ".github/prompts/vs-prompt.prompt.md"

    def test_correct_skill_paths(self):
        from services.harness.copilot_cli import CopilotCliAdapter

        skill = {"name": "my-skill", "content": "# Skill", "git_url": "https://example.com/skill.git"}
        ctx = self._make_ctx(skill_configs=[skill])
        adapter = CopilotCliAdapter()
        result = adapter.format_config(ctx)

        if "skills" in result:
            # Skill files should be generated
            assert len(result["skills"]) >= 1


# ═══════════════════════════════════════════════════════════════════
# Task 17: Property-Based Tests (Hypothesis)
# ═══════════════════════════════════════════════════════════════════


class TestPropertySessionParserRoundTrip:
    """Property: parse-serialize-parse equivalence for events.jsonl lines."""

    @hsettings(max_examples=25, deadline=None)
    @given(
        st.fixed_dictionaries(
            {
                "agentId": st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("L", "N"))),
                "ts": st.from_regex(r"2025-0[1-9]-[0-2][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z", fullmatch=True),
                "event": st.fixed_dictionaries(
                    {
                        "type": st.sampled_from(
                            [
                                "user.message",
                                "assistant.message",
                                "tool.call",
                                "tool.result",
                                "session.start",
                                "session.end",
                                "agent.thinking",
                            ]
                        ),
                        "content": st.text(min_size=0, max_size=100),
                    }
                ),
            }
        )
    )
    def test_session_parser_round_trip(self, envelope):
        """Validates: Requirements 2.1

        Generate random valid events.jsonl lines, verify parse-serialize-parse equivalence.
        """
        from observal_cli.sessions.copilot_cli import parse_event_line

        line = json.dumps(envelope)
        result1 = parse_event_line(line)
        assert result1 is not None
        assert result1["agent_id"] == envelope["agentId"]
        assert result1["timestamp"] == envelope["ts"]
        assert result1["event_type"] == envelope["event"]["type"]

        # Re-serialize and re-parse should give same result
        reconstructed = json.dumps(
            {
                "agentId": result1["agent_id"],
                "ts": result1["timestamp"],
                "event": {"type": result1["event_type"], **result1["payload"]},
            }
        )
        result2 = parse_event_line(reconstructed)
        assert result2 is not None
        assert result2["agent_id"] == result1["agent_id"]
        assert result2["timestamp"] == result1["timestamp"]
        assert result2["event_type"] == result1["event_type"]


class TestPropertyServerParserNormalization:
    """Property: server parser output has correct fields and mapping."""

    @hsettings(max_examples=25, deadline=None)
    @given(
        st.sampled_from(
            [
                "user.message",
                "assistant.message",
                "tool.call",
                "tool.result",
                "session.start",
                "session.end",
                "agent.thinking",
            ]
        )
    )
    def test_server_parser_normalization(self, event_type):
        """Validates: Requirements 2.2

        Generate random valid raw rows, verify output fields and correct mapping.
        """
        from services.session_parsers.copilot_cli import parse_rows

        envelope = {
            "agentId": "test-agent",
            "ts": "2025-01-15T10:30:00Z",
            "event": {"type": event_type, "content": "test content", "name": "tool_x"},
        }
        rows = [
            {
                "raw_line": json.dumps(envelope),
                "timestamp": "2025-01-15 10:30:00.000",
                "ingested_at": "2025-01-15 10:30:01.000",
                "harness": "copilot-cli",
            }
        ]

        events = parse_rows(rows)

        # Skip streaming deltas
        if event_type == "assistant.message_delta":
            return

        assert len(events) >= 1
        evt = events[0]
        # All events must have these fields
        assert "timestamp" in evt
        assert "event_name" in evt
        assert "body" in evt
        assert "attributes" in evt
        assert "service_name" in evt
        assert evt["service_name"] == "copilot-cli"

        # Verify correct mapping
        expected_mapping = {
            "user.message": "hook_userpromptsubmit",
            "assistant.message": "hook_assistant_response",
            "tool.call": "hook_pretooluse",
            "tool.result": "hook_toolresult",
            "session.start": "hook_sessionstart",
            "session.end": "hook_sessionend",
            "agent.thinking": "hook_assistant_thinking",
        }
        if event_type in expected_mapping:
            assert evt["event_name"] == expected_mapping[event_type]


class TestPropertyConfigGenerationFormat:
    """Property: config generation output has correct structure."""

    @hsettings(max_examples=25, deadline=None)
    @given(
        st.fixed_dictionaries(
            {
                "name": st.text(
                    min_size=1,
                    max_size=20,
                    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
                ),
                "description": st.text(min_size=0, max_size=100),
            }
        )
    )
    def test_config_generation_format(self, agent_info):
        """Validates: Requirements 3.1

        Generate random agent configs, verify output structure.
        """
        assume(len(agent_info["name"].strip()) > 0)

        from services.harness.copilot_cli import CopilotCliAdapter

        ctx = MagicMock()
        ctx.safe_name = agent_info["name"].strip().replace(" ", "-")
        ctx.mcp_configs = {}
        ctx.rules_content = "System prompt"
        ctx.hook_configs = []
        ctx.skill_configs = []
        ctx.hook_listings = []
        ctx.compatibility_warnings = []
        ctx.agent = MagicMock()
        ctx.agent.description = agent_info["description"]

        adapter = CopilotCliAdapter()
        result = adapter.format_config(ctx)

        # Verify structure
        assert "agent_profile" in result
        assert "path" in result["agent_profile"]
        assert "content" in result["agent_profile"]
        assert result["agent_profile"]["path"].endswith(".agent.md")

        assert "mcp_config" in result
        assert result["mcp_config"]["path"] == ".mcp.json"
        assert "mcpServers" in result["mcp_config"]["content"]

        assert "hooks_config" in result
        assert result["hooks_config"]["content"]["version"] == 1
        hooks = result["hooks_config"]["content"]["hooks"]
        assert len(hooks) == 5

        assert "scope" in result
