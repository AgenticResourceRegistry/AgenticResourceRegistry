# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 EuanTop <euan@mail.bnu.edu.cn>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the CLI-side harness adapter protocol and registry."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from observal_cli.harness import (
    HookSpec,
    NotSupportedError,
    ScanResult,
    SessionSource,
    ensure_loaded,
    get_adapter,
    get_all_adapters,
)
from observal_shared.harness_registry import HARNESS_REGISTRY


@pytest.fixture(autouse=True)
def _load_adapters():
    """Ensure all adapters are registered before each test."""
    ensure_loaded()


class TestAdapterRegistry:
    """Test adapter registration and lookup."""

    def test_all_registry_adapters_registered(self):
        adapters = get_all_adapters()
        assert set(adapters.keys()) == set(HARNESS_REGISTRY)

    def test_get_adapter_by_canonical_name(self):
        adapter = get_adapter("claude-code")
        assert adapter.harness_name == "claude-code"

    def test_get_adapter_requires_canonical_name(self):
        with pytest.raises(KeyError):
            get_adapter("claude_code")
        with pytest.raises(KeyError):
            get_adapter("copilot_cli")

    def test_get_adapter_unknown_raises_keyerror(self):
        with pytest.raises(KeyError, match="No adapter registered"):
            get_adapter("nonexistent-ide")

    def test_all_adapters_have_required_methods(self):
        required_methods = [
            "scan_home",
            "is_installed",
            "plan_bundled_skill_install",
            "scan_project",
            "get_hook_spec",
            "generate_hook_config",
            "detect_hooks",
            "resolve_session_source",
            "discover_session_sources",
            "related_session_sources",
            "session_extra_fields",
            "session_extra_records",
            "defer_session_delivery",
            "is_session_final",
            "get_observal_managed_files",
        ]
        for name, adapter in get_all_adapters().items():
            for method in required_methods:
                assert hasattr(adapter, method), f"{name} missing {method}"
                assert callable(getattr(adapter, method)), f"{name}.{method} not callable"

    def test_session_source_uses_explicit_cursor_key(self, tmp_path):
        source = SessionSource("claude-code", "session-id", tmp_path / "session.jsonl", cursor_key="subagent")
        assert source.checkpoint_key == "subagent"

    def test_session_source_defaults_checkpoint_to_session_id(self):
        source = SessionSource("claude-code", "session-id")
        assert source.checkpoint_key == "session-id"

    def test_base_adapter_recognizes_common_final_events(self):
        adapter = get_adapter("claude-code")
        assert adapter.is_session_final({"hook_event_name": "Stop"})
        assert adapter.is_session_final({"event": "sessionEnd"})
        assert not adapter.is_session_final({"hook_event_name": "UserPromptSubmit"})

    def test_pi_shares_bundled_skills_with_codex(self, tmp_path):
        plan = get_adapter("pi").plan_bundled_skill_install("observal", tmp_path, frozenset({"codex", "pi"}))

        assert plan.target == tmp_path / ".agents/skills/observal/SKILL.md"
        assert plan.cleanup_candidates == (tmp_path / ".pi/agent/skills/observal/SKILL.md",)

    def test_pi_uses_native_bundled_skills_when_isolated(self, tmp_path):
        plan = get_adapter("pi").plan_bundled_skill_install("observal", tmp_path, frozenset({"pi"}))

        assert plan.target == tmp_path / ".pi/agent/skills/observal/SKILL.md"
        assert plan.reuse_candidates == (tmp_path / ".agents/skills/observal/SKILL.md",)

    def test_pi_detect_hooks_missing_when_neither_install_mode_is_present(self, tmp_path):
        assert get_adapter("pi").detect_hooks(tmp_path) == "missing"

    def test_pi_detect_hooks_recognizes_local_extension_file(self, tmp_path):
        extension = tmp_path / "extensions" / "observal.ts"
        extension.parent.mkdir(parents=True)
        extension.write_text("extension", encoding="utf-8")

        assert get_adapter("pi").detect_hooks(tmp_path) == "installed"


class TestManagedLayerFiles:
    """Test adapter-owned layer source attribution."""

    @staticmethod
    def _lockfile_for(harness_name: str) -> dict:
        return {
            "ides": {
                harness_name: {
                    "agents": [
                        {
                            "name": "agent-one",
                            "components": [
                                {"type": "skill", "name": "skill-one"},
                                {"type": "mcp", "name": "mcp-one"},
                            ],
                        }
                    ],
                    "standalone": [
                        {"type": "skill", "name": "standalone-skill"},
                        {"type": "mcp", "name": "standalone-mcp"},
                    ],
                }
            }
        }

    @pytest.mark.parametrize(
        ("harness_name", "expected"),
        [
            (
                "claude-code",
                {
                    "user:agents/agent-one.md",
                    "project:.claude/agents/agent-one.md",
                    "user:skills/skill-one/SKILL.md",
                    "user:skills/standalone-skill/SKILL.md",
                },
            ),
            (
                "cursor",
                {
                    "user:agents/agent-one.md",
                    "project:.cursor/agents/agent-one.md",
                    "user:rules/skill-one.mdc",
                    "user:skills/skill-one/SKILL.md",
                    "user:rules/standalone-skill.mdc",
                    "user:skills/standalone-skill/SKILL.md",
                },
            ),
            (
                "kiro",
                {
                    "user:agents/agent-one.json",
                    "project:.kiro/agents/agent-one.json",
                    "user:skills/skill-one/SKILL.md",
                    "user:skills/standalone-skill/SKILL.md",
                },
            ),
            (
                "codex",
                {"user:agents/agent-one.toml", "project:.codex/agents/agent-one.toml", "user:config.toml"},
            ),
            (
                "copilot",
                {"project:.github/agents/agent-one.agent.md"},
            ),
            (
                "copilot-cli",
                {
                    "project:.github/agents/agent-one.agent.md",
                    "project:.agents/skills/skill-one/SKILL.md",
                    "user:skills/skill-one/SKILL.md",
                    "project:.agents/skills/standalone-skill/SKILL.md",
                    "user:skills/standalone-skill/SKILL.md",
                },
            ),
            (
                "opencode",
                {
                    "user:agents/agent-one.md",
                    "project:.opencode/agents/agent-one.md",
                    "user:skills/skill-one/SKILL.md",
                    "project:.opencode/skills/skill-one/SKILL.md",
                    "user:skills/standalone-skill/SKILL.md",
                    "project:.opencode/skills/standalone-skill/SKILL.md",
                },
            ),
            (
                "pi",
                {
                    "user:AGENTS.md",
                    "user:skills/skill-one/SKILL.md",
                    "user:skills/standalone-skill/SKILL.md",
                },
            ),
        ],
    )
    def test_adapter_managed_files_match_existing_layer_paths(self, harness_name, expected):
        adapter = get_adapter(harness_name)
        assert adapter.get_observal_managed_files(self._lockfile_for(harness_name)) == expected

    def test_layer_managed_files_delegates_to_adapter(self):
        from observal_cli.layer import _get_observal_managed_files

        lockfile = self._lockfile_for("codex")
        assert _get_observal_managed_files(lockfile, "codex", None) == {
            "user:agents/agent-one.toml",
            "project:.codex/agents/agent-one.toml",
            "user:config.toml",
        }

    def test_managed_files_accept_current_harnesses_lockfile_key(self):
        adapter = get_adapter("pi")
        legacy = self._lockfile_for("pi")
        lockfile = {"harnesses": legacy["ides"]}

        assert adapter.get_observal_managed_files(lockfile) == {
            "user:AGENTS.md",
            "user:skills/skill-one/SKILL.md",
            "user:skills/standalone-skill/SKILL.md",
        }


class TestActiveIdeDetection:
    """Test adapter-owned active harness detection."""

    @pytest.mark.parametrize(
        ("harness_name", "marker"),
        [
            ("claude-code", ".claude"),
            ("cursor", ".cursor"),
            ("kiro", ".kiro"),
            ("codex", ".codex"),
            ("copilot", ".vscode/extensions/github.copilot-1.0.0"),
            ("copilot-cli", ".copilot"),
            ("opencode", ".config/opencode"),
            ("antigravity", ".gemini/antigravity-cli"),
            ("pi", ".pi/agent"),
        ],
    )
    def test_adapter_is_installed_uses_home_markers(self, harness_name, marker, tmp_path):
        adapter = get_adapter(harness_name)
        assert adapter.is_installed(tmp_path) is False
        (tmp_path / Path(marker)).mkdir(parents=True)
        assert adapter.is_installed(tmp_path) is True

    def test_layer_detect_active_harnesses_delegates_to_adapters(self, tmp_path, monkeypatch):
        from observal_cli.layer import _detect_active_harnesses

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        (tmp_path / ".cursor").mkdir()
        (tmp_path / ".codex").mkdir()
        (tmp_path / ".pi" / "agent").mkdir(parents=True)

        assert _detect_active_harnesses() == ["cursor", "codex", "pi"]

    def test_pi_layer_manifest_includes_isolated_agent_profiles(self, tmp_path, monkeypatch):
        from observal_cli.layer import build_layer_manifest

        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr("observal_cli.config.load", lambda: {"server_url": "http://localhost:80"})
        monkeypatch.setattr("observal_cli.lockfile.LOCKFILE_PATH", tmp_path / ".observal/lockfile.json")
        pi_home = tmp_path / ".pi" / "agent"
        (pi_home / "agents" / "my-agent" / "skills" / "pi-skill").mkdir(parents=True)
        (pi_home / "agents" / "my-agent" / "AGENTS.md").write_text("# Agent")
        (pi_home / "agents" / "my-agent" / "mcp.json").write_text("{}")
        (pi_home / "agents" / "my-agent" / "skills" / "pi-skill" / "SKILL.md").write_text("# Skill")

        paths = {entry["path"] for entry in build_layer_manifest("pi")}

        assert "user:agents/my-agent/AGENTS.md" in paths
        assert "user:agents/my-agent/mcp.json" in paths
        assert "user:agents/my-agent/skills/pi-skill/SKILL.md" in paths


class TestAdapterProtocol:
    """Test that each adapter satisfies the protocol interface."""

    @pytest.mark.parametrize(
        "harness_name",
        [
            "claude-code",
            "cursor",
            "kiro",
            "codex",
            "copilot",
            "copilot-cli",
            "opencode",
        ],
    )
    def test_scan_home_returns_scan_result(self, harness_name, tmp_path):
        adapter = get_adapter(harness_name)
        result = adapter.scan_home(home=tmp_path)
        assert isinstance(result, ScanResult)
        assert isinstance(result.mcps, list)
        assert isinstance(result.skills, list)
        assert isinstance(result.hooks, list)
        assert isinstance(result.agents, list)

    @pytest.mark.parametrize(
        "harness_name",
        [
            "claude-code",
            "cursor",
            "kiro",
            "codex",
            "copilot",
            "copilot-cli",
            "opencode",
        ],
    )
    def test_scan_project_returns_scan_result(self, harness_name, tmp_path):
        adapter = get_adapter(harness_name)
        result = adapter.scan_project(tmp_path)
        assert isinstance(result, ScanResult)

    @pytest.mark.parametrize(
        "harness_name",
        [
            "claude-code",
            "cursor",
            "kiro",
            # copilot (VS Code) intentionally omitted: uses OTel export, not hooks
            "copilot-cli",
            "opencode",
        ],
    )
    def test_get_hook_spec_returns_hook_spec(self, harness_name):
        adapter = get_adapter(harness_name)
        spec = adapter.get_hook_spec()
        assert isinstance(spec, HookSpec)

    def test_copilot_get_hook_spec(self):
        """Copilot VS Code supports hooks with PascalCase events."""
        adapter = get_adapter("copilot")
        spec = adapter.get_hook_spec()
        assert isinstance(spec, HookSpec)
        assert "SessionStart" in spec.events
        assert "UserPromptSubmit" in spec.events
        assert "Stop" in spec.events

    @pytest.mark.parametrize(
        "harness_name",
        [
            "claude-code",
            "cursor",
            "kiro",
            "copilot",
            "copilot-cli",
            "opencode",
        ],
    )
    def test_detect_hooks_returns_string(self, harness_name, tmp_path):
        adapter = get_adapter(harness_name)
        result = adapter.detect_hooks(tmp_path)
        assert result in ("installed", "partial", "none", "missing")


class TestClaudeCodeAdapter:
    """Tests specific to the Claude Code adapter (full implementation)."""

    def test_hook_spec_has_events(self):
        adapter = get_adapter("claude-code")
        spec = adapter.get_hook_spec()
        assert "PreToolUse" in spec.events
        assert "Stop" in spec.events
        assert spec.format == "command"

    def test_generate_hook_config_returns_dict(self):
        adapter = get_adapter("claude-code")
        config = adapter.generate_hook_config(
            observal_url="http://localhost:8000",
            api_key="test-key",
        )
        assert isinstance(config, dict)

    def test_scan_home_with_settings(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        # Claude Code discovers MCPs via plugin system
        settings = {"enabledPlugins": {"test-plugin@marketplace": True}}
        (claude_dir / "settings.json").write_text(json.dumps(settings))

        # Create plugin with MCP
        plugin_dir = claude_dir / "plugins" / "cache" / "marketplace" / "test-plugin" / "1.0.0"
        plugin_dir.mkdir(parents=True)
        mcp_data = {"mcpServers": {"test-server": {"command": "node", "args": ["server.js"]}}}
        (plugin_dir / ".mcp.json").write_text(json.dumps(mcp_data))

        adapter = get_adapter("claude-code")
        result = adapter.scan_home(home=tmp_path)
        assert isinstance(result, ScanResult)
        assert any(m.name == "test-server" for m in result.mcps)


class TestKiroAdapter:
    """Tests specific to the Kiro adapter (full implementation)."""

    def test_hook_spec_has_events(self):
        adapter = get_adapter("kiro")
        spec = adapter.get_hook_spec()
        assert len(spec.events) > 0
        assert spec.format == "http"

    def test_generate_hook_config_returns_dict(self):
        adapter = get_adapter("kiro")
        config = adapter.generate_hook_config(
            observal_url="http://localhost:8000",
            api_key="test-key",
        )
        assert isinstance(config, dict)

    def test_scan_home_with_mcp(self, tmp_path):
        kiro_dir = tmp_path / ".kiro"
        (kiro_dir / "settings").mkdir(parents=True)
        mcp_data = {"mcpServers": {"my-mcp": {"command": "python", "args": ["-m", "mcp"]}}}
        (kiro_dir / "settings" / "mcp.json").write_text(json.dumps(mcp_data))

        adapter = get_adapter("kiro")
        result = adapter.scan_home(home=tmp_path)
        assert any(m.name == "my-mcp" for m in result.mcps)


class TestFeatureGating:
    """Test that harness registry capabilities gate method access."""

    def test_codex_has_hooks_allows_get_hook_spec(self):
        adapter = get_adapter("codex")
        spec = adapter.get_hook_spec()
        assert isinstance(spec, HookSpec)
        assert "UserPromptSubmit" in spec.events

    def test_codex_has_hooks_allows_detect_hooks(self):
        import tempfile
        from pathlib import Path

        adapter = get_adapter("codex")
        result = adapter.detect_hooks(Path(tempfile.mkdtemp()))
        assert result in ("installed", "missing")

    def test_codex_has_mcp_servers_allows_scan_home(self):
        import tempfile
        from pathlib import Path

        adapter = get_adapter("codex")
        # Should not raise, codex has mcp_servers feature
        result = adapter.scan_home(home=Path(tempfile.mkdtemp()))
        assert isinstance(result, ScanResult)

    def test_claude_code_has_all_features_no_gating(self):
        adapter = get_adapter("claude-code")
        # All methods should work without raising
        spec = adapter.get_hook_spec()
        assert len(spec.events) > 0


class TestOpenCodeAdapter:
    """OpenCode path resolution, discovery normalization, and hook behavior."""

    @staticmethod
    def _records(items):
        return [vars(item) for item in items]

    def test_adapter_owned_metadata_and_capability_gates(self, tmp_path, monkeypatch):
        from observal_cli.harness.base import _check_feature
        from observal_cli.harness.opencode import OpenCodeAdapter

        adapter = OpenCodeAdapter()
        spec = HARNESS_REGISTRY["opencode"]

        assert adapter.harness_name == "opencode"
        assert adapter.home_markers == (".config/opencode",)
        assert adapter.managed_agent_profiles == (
            "user:agents/{name}.md",
            "project:.opencode/agents/{name}.md",
        )
        assert adapter.managed_skills == (
            "user:skills/{name}/SKILL.md",
            "project:.opencode/skills/{name}/SKILL.md",
        )
        assert spec["capabilities"] == {"hooks", "mcp_servers", "skills"}

        gated_methods = ("scan_home", "scan_project", "get_hook_spec", "generate_hook_config", "detect_hooks")
        for method in gated_methods:
            _check_feature(adapter.harness_name, method)
        assert isinstance(adapter.scan_home(tmp_path), ScanResult)

        monkeypatch.setitem(spec, "capabilities", set())
        for method in gated_methods:
            with pytest.raises(NotSupportedError, match=rf"opencode does not support {method}"):
                _check_feature(adapter.harness_name, method)

    def test_jsonc_comments_are_removed_without_changing_strings(self):
        from observal_cli.harness.opencode import _strip_jsonc_comments

        parsed = json.loads(
            _strip_jsonc_comments(
                """
                {
                  // line comment
                  "url": "https://example.test/a//b",
                  "literal": "/* keep this */",
                  /* block comment */
                  "command": ["node", "server.js"]
                }
                """
            )
        )

        assert parsed == {
            "url": "https://example.test/a//b",
            "literal": "/* keep this */",
            "command": ["node", "server.js"],
        }

    def test_scan_home_resolves_default_home_and_normalizes_all_components(self, tmp_path, monkeypatch):
        import observal_cli.harness.opencode as opencode_module

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        root = tmp_path / ".config" / "opencode"
        root.mkdir(parents=True)
        (root / "opencode.jsonc").write_text(
            """
            {
              // OpenCode accepts comments in its fallback config.
              "mcp": {
                "local": {
                  "type": "local",
                  "command": ["npx", "-y", "local-package"],
                  "environment": {"TOKEN": "not-disclosed"},
                  "enabled": true
                },
                "disabled": {
                  "type": "local",
                  "command": "uvx",
                  "args": ["disabled-package"],
                  "enabled": false
                },
                "remote": {
                  "type": "remote",
                  "url": "https://mcp.example.test/api",
                  "headers": {"Authorization": "not-disclosed"}
                },
                "empty-command": {"command": []},
                "unsupported": "ignore me"
              }
            }
            """
        )
        skill = root / "skills" / "helper"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\ndescription: Global helper\n---\n")
        (root / "skills" / "README.md").write_text("not a skill")
        (root / "skills" / "missing-file").mkdir()
        agent = root / "agents" / "reviewer.md"
        agent.parent.mkdir()
        agent.write_text("---\ndescription: Reviews code\n---\nReview carefully.")
        (agent.parent / "ignored.txt").write_text("ignore")
        (agent.parent / "directory.md").mkdir()
        plugin = root / "plugins" / "audit.mjs"
        plugin.parent.mkdir()
        plugin.write_text("export const audit = {}")
        (plugin.parent / "ignored.txt").write_text("ignore")
        (plugin.parent / "directory.ts").mkdir()

        result = opencode_module.OpenCodeAdapter().scan_home()

        assert self._records(result.mcps) == [
            {
                "name": "local",
                "command": "npx",
                "args": ["-y", "local-package"],
                "url": None,
                "description": "OpenCode MCP: local",
                "source": "opencode:global",
            },
            {
                "name": "disabled",
                "command": "uvx",
                "args": ["disabled-package"],
                "url": None,
                "description": "OpenCode MCP: disabled",
                "source": "opencode:global",
            },
            {
                "name": "remote",
                "command": None,
                "args": [],
                "url": "https://mcp.example.test/api",
                "description": "OpenCode MCP: remote",
                "source": "opencode:global",
            },
            {
                "name": "empty-command",
                "command": None,
                "args": [],
                "url": None,
                "description": "OpenCode MCP: empty-command",
                "source": "opencode:global",
            },
        ]
        assert self._records(result.skills) == [
            {
                "name": "helper",
                "description": "Global helper",
                "source": "opencode:global",
                "task_type": "general",
            }
        ]
        assert self._records(result.agents) == [
            {
                "name": "reviewer",
                "description": "Reviews code",
                "model_name": "",
                "prompt": "---\ndescription: Reviews code\n---\nReview carefully.",
                "source_file": str(agent),
            }
        ]
        assert self._records(result.hooks) == [
            {
                "name": "audit",
                "event": "plugin",
                "handler_type": "plugin",
                "handler_config": {},
                "description": "OpenCode plugin hook: audit",
                "source": "opencode:global",
            }
        ]
        assert all("environment" not in record and "headers" not in record for record in self._records(result.mcps))

    def test_scan_project_uses_root_config_and_project_component_directory(self, tmp_path, monkeypatch):
        import observal_cli.harness.opencode as opencode_module

        (tmp_path / "opencode.json").write_text(
            json.dumps({"mcp": {"project-server": {"command": "python", "args": ["server.py"]}}})
        )
        root = tmp_path / ".opencode"
        ignored_config = root / "opencode.json"
        ignored_config.parent.mkdir()
        ignored_config.write_text(json.dumps({"mcp": {"ignored": {"command": "ignored"}}}))
        skill = root / "skills" / "project-helper"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\ndescription: Project helper\n---\n")
        agent = root / "agents" / "builder.md"
        agent.parent.mkdir()
        agent.write_text("---\ndescription: Builds projects\n---\n")
        plugin = root / "plugins" / "project-plugin.js"
        plugin.parent.mkdir()
        plugin.write_text("export default {}")

        result = opencode_module.OpenCodeAdapter().scan_project(tmp_path)

        assert self._records(result.mcps) == [
            {
                "name": "project-server",
                "command": "python",
                "args": ["server.py"],
                "url": None,
                "description": "OpenCode MCP: project-server",
                "source": "opencode:project",
            }
        ]
        assert self._records(result.skills) == [
            {
                "name": "project-helper",
                "description": "Project helper",
                "source": "opencode:project",
                "task_type": "general",
            }
        ]
        assert self._records(result.agents) == [
            {
                "name": "builder",
                "description": "Builds projects",
                "model_name": "",
                "prompt": "---\ndescription: Builds projects\n---\n",
                "source_file": str(agent),
            }
        ]
        assert [(hook.name, hook.source) for hook in result.hooks] == [("project-plugin", "opencode:project")]

    def test_scan_json_deduplicates_with_global_scope_precedence(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner

        from observal_cli.main import app

        home = tmp_path / "home"
        global_root = home / ".config" / "opencode"
        global_root.mkdir(parents=True)
        (global_root / "opencode.json").write_text(
            json.dumps({"mcp": {"shared": {"command": ["global-command", "global.js"]}}})
        )
        project = tmp_path / "project"
        project.mkdir()
        (project / "opencode.json").write_text(
            json.dumps({"mcp": {"shared": {"command": ["project-command", "project.js"]}}})
        )
        monkeypatch.setattr(Path, "home", lambda: home)
        monkeypatch.chdir(project)

        result = CliRunner().invoke(app, ["scan", "-i", "opencode", "-o", "json"])

        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == {
            "harnesses": [{"name": "opencode", "hooks": "missing"}],
            "mcps": [
                {
                    "name": "shared",
                    "command": "global-command",
                    "args": ["global.js"],
                    "url": None,
                    "description": "OpenCode MCP: shared",
                    "source": "opencode:global",
                }
            ],
            "skills": [],
            "hooks": [],
            "agents": [],
        }

    def test_missing_home_and_project_paths_return_empty_results(self, tmp_path):
        from observal_cli.harness.opencode import OpenCodeAdapter

        adapter = OpenCodeAdapter()

        assert self._records(adapter.scan_home(tmp_path).mcps) == []
        assert vars(adapter.scan_project(tmp_path)) == {"mcps": [], "skills": [], "hooks": [], "agents": []}
        missing = tmp_path / "missing"
        assert adapter._scan_skills_dir(missing, "scope") == []
        assert adapter._scan_agents_dir(missing, "scope") == []
        assert adapter._scan_plugins_dir(missing, "scope") == []

        (tmp_path / ".config" / "opencode").mkdir(parents=True)
        assert vars(adapter.scan_home(tmp_path)) == {"mcps": [], "skills": [], "hooks": [], "agents": []}
        project = tmp_path / "empty-project"
        (project / ".opencode").mkdir(parents=True)
        assert vars(adapter.scan_project(project)) == {"mcps": [], "skills": [], "hooks": [], "agents": []}

    def test_global_json_has_precedence_over_jsonc_without_fallback_on_errors(self, tmp_path):
        from observal_cli.harness.opencode import OpenCodeAdapter

        root = tmp_path / ".config" / "opencode"
        root.mkdir(parents=True)
        primary = root / "opencode.json"
        primary.write_text(json.dumps({"mcp": {"primary": {"command": "primary"}}}))
        (root / "opencode.jsonc").write_text('{"mcp": {"fallback": {"command": "fallback"}}}')
        adapter = OpenCodeAdapter()

        assert [item.name for item in adapter.scan_home(tmp_path).mcps] == ["primary"]

        primary.write_text("{ malformed")
        assert adapter.scan_home(tmp_path).mcps == []

    @pytest.mark.parametrize(
        "content",
        [
            "{ malformed",
            json.dumps({"other": {"command": "not an MCP section"}}),
            json.dumps({"mcp": {"string": "ignored", "number": 1, "null": None}}),
        ],
    )
    def test_malformed_or_unsupported_server_entries_fail_soft(self, tmp_path, content):
        from observal_cli.harness.opencode import OpenCodeAdapter

        config = tmp_path / "opencode.json"
        config.write_text(content)

        assert OpenCodeAdapter().scan_project(tmp_path).mcps == []

    @pytest.mark.parametrize("content", ["[]", '{"mcp": []}'])
    def test_unsupported_non_object_config_fails_loudly(self, tmp_path, content):
        from observal_cli.harness.opencode import OpenCodeAdapter

        (tmp_path / "opencode.json").write_text(content)

        with pytest.raises(AttributeError):
            OpenCodeAdapter().scan_project(tmp_path)

    def test_config_read_filesystem_error_fails_soft(self, tmp_path):
        from observal_cli.harness.opencode import OpenCodeAdapter

        (tmp_path / "opencode.json").mkdir()

        assert OpenCodeAdapter().scan_project(tmp_path).mcps == []

    def test_component_and_config_symlinks_are_followed(self, tmp_path, monkeypatch):
        import observal_cli.harness.opencode as opencode_module

        root = tmp_path / ".config" / "opencode"
        root.mkdir(parents=True)
        targets = tmp_path / "targets"
        targets.mkdir()
        config_target = targets / "config.json"
        config_target.write_text(json.dumps({"mcp": {"linked": {"command": ["node", "linked.js"]}}}))
        (root / "opencode.json").symlink_to(config_target)

        skill_target = targets / "skill"
        skill_target.mkdir()
        (skill_target / "SKILL.md").write_text("---\ndescription: Linked skill\n---\n")
        skills = root / "skills"
        skills.mkdir()
        (skills / "linked-skill").symlink_to(skill_target, target_is_directory=True)
        (skills / "broken-skill").symlink_to(targets / "missing-skill", target_is_directory=True)

        agent_target = targets / "agent.md"
        agent_target.write_text("---\ndescription: Linked agent\n---\n")
        agents = root / "agents"
        agents.mkdir()
        (agents / "linked-agent.md").symlink_to(agent_target)
        (agents / "broken-agent.md").symlink_to(targets / "missing-agent.md")

        plugin_target = targets / "plugin.mjs"
        plugin_target.write_text("export const ObservalPlugin = {}")
        plugins = root / "plugins"
        plugins.mkdir()
        (plugins / "linked-plugin.mjs").symlink_to(plugin_target)
        (plugins / "broken-plugin.mjs").symlink_to(targets / "missing-plugin.mjs")

        adapter = opencode_module.OpenCodeAdapter()
        result = adapter.scan_home(tmp_path)

        assert [(item.name, item.command, item.args) for item in result.mcps] == [("linked", "node", ["linked.js"])]
        assert [(item.name, item.description) for item in result.skills] == [("linked-skill", "Linked skill")]
        assert [(item.name, item.description) for item in result.agents] == [("linked-agent", "Linked agent")]
        assert [item.name for item in result.hooks] == ["linked-plugin"]
        assert adapter.detect_hooks(root) == "installed"

    def test_agent_discovery_returns_the_shared_record_contract(self, tmp_path):
        from observal_cli.harness.opencode import OpenCodeAdapter

        agent = tmp_path / ".opencode" / "agents" / "reviewer.md"
        agent.parent.mkdir(parents=True)
        content = "---\ndescription: Reviews code\nmodel: claude-sonnet\n---\nReview carefully."
        agent.write_text(content)

        assert self._records(OpenCodeAdapter().scan_project(tmp_path).agents) == [
            {
                "name": "reviewer",
                "description": "Reviews code",
                "model_name": "claude-sonnet",
                "prompt": content,
                "source_file": str(agent),
            }
        ]

    def test_component_read_errors_are_isolated(self, tmp_path, monkeypatch):
        import observal_cli.harness.opencode as opencode_module

        root = tmp_path / ".config" / "opencode"
        config = root / "opencode.json"
        skill = root / "skills" / "broken" / "SKILL.md"
        agent = root / "agents" / "broken.md"
        plugin = root / "plugins" / "broken.ts"
        for path in (config, skill, agent, plugin):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}")

        original_read_text = Path.read_text

        def raise_for_components(path, *args, **kwargs):
            if path in {config, skill, agent, plugin}:
                raise OSError("unreadable")
            return original_read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", raise_for_components)
        adapter = opencode_module.OpenCodeAdapter()

        assert adapter._parse_opencode_config(config, "scope").mcps == []
        assert adapter._scan_skills_dir(skill.parent.parent, "scope") == []
        assert adapter._scan_agents_dir(agent.parent, "scope") == []
        assert [hook.name for hook in adapter._scan_plugins_dir(plugin.parent, "scope")] == ["broken"]
        assert adapter.detect_hooks(root) == "missing"

        monkeypatch.setattr(opencode_module, "DiscoveredHook", Mock(side_effect=OSError("metadata error")))
        assert adapter._scan_plugins_dir(plugin.parent, "scope") == []

    def test_plugin_discovery_keeps_duplicate_stems_and_supported_suffixes(self, tmp_path):
        from observal_cli.harness.opencode import OpenCodeAdapter

        plugins = tmp_path / "plugins"
        plugins.mkdir()
        for name in ("same.ts", "same.js", "alpha.mjs", "ignored.py"):
            (plugins / name).write_text("export default {}")

        records = self._records(OpenCodeAdapter()._scan_plugins_dir(plugins, "opencode:test"))

        assert [record["name"] for record in records] == ["alpha", "same", "same"]
        assert all(record["source"] == "opencode:test" for record in records)

    @pytest.mark.parametrize(
        ("filename", "content"),
        [
            ("plugin.ts", "export const ObservalPlugin = {}"),
            ("plugin.js", "const plugin = 'OBSERVAL telemetry'"),
            ("plugin.mjs", "const plugin = 'observal telemetry'"),
        ],
    )
    def test_detect_hooks_accepts_supported_plugin_markers(self, tmp_path, filename, content):
        from observal_cli.harness.opencode import OpenCodeAdapter

        plugins = tmp_path / "plugins"
        plugins.mkdir()
        (plugins / filename).write_text(content)

        assert OpenCodeAdapter().detect_hooks(tmp_path) == "installed"

    def test_detect_hooks_ignores_foreign_and_unsupported_files(self, tmp_path):
        from observal_cli.harness.opencode import OpenCodeAdapter

        plugins = tmp_path / "plugins"
        plugins.mkdir()
        (plugins / "foreign.ts").write_text("export default {}")
        (plugins / "marker.txt").write_text("ObservalPlugin")
        (plugins / "nested.js").mkdir()

        assert OpenCodeAdapter().detect_hooks(tmp_path) == "missing"
        assert OpenCodeAdapter().detect_hooks(tmp_path / "absent") == "missing"

    def test_detect_hooks_fails_loudly_when_plugins_path_is_not_a_directory(self, tmp_path):
        from observal_cli.harness.opencode import OpenCodeAdapter

        (tmp_path / "plugins").write_text("not a directory")

        with pytest.raises(NotADirectoryError):
            OpenCodeAdapter().detect_hooks(tmp_path)

    @pytest.mark.parametrize(
        ("content", "field", "expected"),
        [
            ("No frontmatter", "description", None),
            ("---\ndescription: missing close", "description", None),
            ("---\n  description: nested\ndescription: top level\n---\n", "description", "top level"),
            ('---\ndescription: "quoted value"\n---\n', "description", "quoted value"),
            ("---\ndescription: 'single quoted'\n---\n", "description", "single quoted"),
            ("---\ndescription:\n---\n", "description", ""),
            ("---\ndescription: \"mismatched'\n---\n", "description", "\"mismatched'"),
            ("---\nmodel: test-model\n---\n", "model", "test-model"),
            ("---\nmodel: test-model\n---\n", "description", None),
        ],
    )
    def test_frontmatter_field_parsing(self, content, field, expected):
        from observal_cli.harness.opencode import OpenCodeAdapter

        assert OpenCodeAdapter()._extract_frontmatter_field(content, field) == expected

    def test_hook_spec_and_generated_config_are_exact(self):
        from observal_cli.harness.opencode import OpenCodeAdapter

        adapter = OpenCodeAdapter()

        assert vars(adapter.get_hook_spec()) == {
            "events": [
                "session.created",
                "session.idle",
                "message.updated",
                "tool.execute.before",
                "tool.execute.after",
            ],
            "format": "plugin",
            "markers": ["observal", "Observal", "ObservalPlugin"],
            "env_vars": {},
        }
        assert adapter.generate_hook_config("https://observal.example.test", "secret", "agent-id") == {
            "hook_type": "plugin",
            "install_method": "file",
            "plugin_path": ".opencode/plugins/observal-plugin.ts",
            "global_plugin_path": "~/.config/opencode/plugins/observal-plugin.ts",
            "events": ["session.created", "session.idle", "message.updated"],
        }

    def test_patch_and_cleanup_delegate_to_doctor(self, monkeypatch):
        from observal_cli import cmd_doctor
        from observal_cli.harness.opencode import OpenCodeAdapter

        patch = Mock(return_value=True)
        cleanup = Mock(return_value=False)
        monkeypatch.setattr(cmd_doctor, "_patch_opencode", patch)
        monkeypatch.setattr(cmd_doctor, "_cleanup_opencode", cleanup)
        adapter = OpenCodeAdapter()

        assert adapter.patch_hooks(dry_run=True) is True
        assert adapter.cleanup_hooks(dry_run=False) is False
        patch.assert_called_once_with(True)
        cleanup.assert_called_once_with(False)
