# SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for canonical harness config generation."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
import yaml

from services.harness import generate_agent_config
from services.harness.helpers import (
    _build_rules_content,
    _inject_agent_id,
    _model_name_to_frontmatter,
    _sanitize_name,
)

# ── Helpers ───────────────────────────────────────────────────────


def _make_component(component_type: str = "mcp", component_id: uuid.UUID | None = None) -> MagicMock:
    comp = MagicMock()
    comp.component_type = component_type
    comp.component_id = component_id or uuid.uuid4()
    return comp


def _make_agent(
    name: str = "test-agent",
    description: str = "A test agent",
    prompt: str = "You are helpful.",
    model_name: str = "claude-sonnet-4",
    components: list | None = None,
    external_mcps: list | None = None,
) -> MagicMock:
    agent = MagicMock()
    agent.id = uuid.uuid4()
    agent.name = name
    agent.description = description
    agent.prompt = prompt
    agent.model_name = model_name
    agent.components = components or []
    agent.external_mcps = external_mcps or []
    return agent


# ═══════════════════════════════════════════════════════════════════
# 1. _sanitize_name
# ═══════════════════════════════════════════════════════════════════


class TestSanitizeName:
    def test_passthrough_for_safe_name(self):
        assert _sanitize_name("my-agent_v2") == "my-agent_v2"

    def test_replaces_spaces_and_special_chars(self):
        assert _sanitize_name("my agent!@#v2") == "my-agent---v2"

    def test_empty_string(self):
        assert _sanitize_name("") == ""

    def test_all_special_chars(self):
        result = _sanitize_name("!!!???")
        assert all(c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in result)


# ═══════════════════════════════════════════════════════════════════
# 2. _inject_agent_id
# ═══════════════════════════════════════════════════════════════════


class TestInjectAgentId:
    def test_adds_env_var_to_all_entries(self):
        configs = {
            "server-a": {"command": "npx", "args": [], "env": {}},
            "server-b": {"command": "node", "args": ["s.js"], "env": {"FOO": "bar"}},
        }
        _inject_agent_id(configs, "agent-123")
        assert configs["server-a"]["env"]["OBSERVAL_AGENT_ID"] == "agent-123"
        assert configs["server-b"]["env"]["OBSERVAL_AGENT_ID"] == "agent-123"
        assert configs["server-b"]["env"]["FOO"] == "bar"

    def test_creates_env_if_missing(self):
        configs = {"srv": {"command": "x"}}
        _inject_agent_id(configs, "id-1")
        assert configs["srv"]["env"]["OBSERVAL_AGENT_ID"] == "id-1"

    def test_skips_non_dict_values(self):
        configs = {"meta": "not-a-dict", "srv": {"command": "x", "env": {}}}
        _inject_agent_id(configs, "id-1")
        assert configs["meta"] == "not-a-dict"


# ═══════════════════════════════════════════════════════════════════
# 3. _build_rules_content
# ═══════════════════════════════════════════════════════════════════


class TestBuildRulesContent:
    def test_uses_prompt_when_available(self):
        agent = _make_agent(prompt="Custom prompt", description="Desc")
        result = _build_rules_content(agent)
        assert "Custom prompt" in result
        assert "Desc" not in result

    def test_no_prompt_produces_bare_heading(self):
        agent = _make_agent(prompt="", description="Registry copy. Must not appear.")
        result = _build_rules_content(agent)
        assert "Registry copy" not in result
        assert result == f"# {agent.name}"

    def test_fallback_when_both_empty(self):
        agent = _make_agent(prompt="", description="")
        result = _build_rules_content(agent)
        assert agent.name in result

    def test_includes_component_summaries(self):
        mcp_id = uuid.uuid4()
        skill_id = uuid.uuid4()
        components = [
            _make_component("mcp", mcp_id),
            _make_component("skill", skill_id),
        ]
        names = {str(mcp_id): "my-mcp-server", str(skill_id): "my-skill"}
        agent = _make_agent(components=components)

        result = _build_rules_content(agent, component_names=names)
        assert "## MCP Servers" in result
        assert "**my-mcp-server**" in result
        assert "## Skills" in result
        assert "**my-skill**" in result

    def test_truncates_component_id_when_no_name_provided(self):
        comp_id = uuid.uuid4()
        agent = _make_agent(components=[_make_component("mcp", comp_id)])
        result = _build_rules_content(agent)
        assert str(comp_id)[:8] in result

    def test_groups_multiple_components_by_type(self):
        id1, id2 = uuid.uuid4(), uuid.uuid4()
        components = [_make_component("hook", id1), _make_component("hook", id2)]
        names = {str(id1): "hook-a", str(id2): "hook-b"}
        agent = _make_agent(components=components)
        result = _build_rules_content(agent, component_names=names)
        assert "## Hooks" in result
        assert "**hook-a**" in result
        assert "**hook-b**" in result

    def test_never_returns_empty(self):
        agent = _make_agent(prompt="", description="", components=[])
        result = _build_rules_content(agent)
        assert len(result.strip()) > 0


# ═══════════════════════════════════════════════════════════════════
# 4. generate_agent_config — Claude Code
# ═══════════════════════════════════════════════════════════════════


class TestGenerateClaudeCode:
    def test_path_is_under_agents_not_rules(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "claude-code")
        path = cfg["agent_profile"]["path"]
        assert ".claude/agents/" in path
        assert ".claude/rules/" not in path

    def test_content_starts_with_yaml_frontmatter(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "claude-code")
        content = cfg["agent_profile"]["content"]
        assert content.startswith("---\n")
        # Extract frontmatter
        parts = content.split("---", 2)
        assert len(parts) >= 3
        fm = yaml.safe_load(parts[1])
        assert fm["name"] == "test-agent"
        assert fm["description"] == "A test agent"

    def test_frontmatter_includes_mcp_servers(self):
        ext_mcps = [{"name": "my-ext", "command": "npx", "args": ["-y", "ext"]}]
        agent = _make_agent(external_mcps=ext_mcps)
        cfg = generate_agent_config(agent, "claude-code")
        content = cfg["agent_profile"]["content"]
        parts = content.split("---", 2)
        fm = yaml.safe_load(parts[1])
        assert "mcpServers" in fm
        assert "my-ext" in fm["mcpServers"]

    def test_frontmatter_omits_mcpservers_when_empty(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "claude-code")
        content = cfg["agent_profile"]["content"]
        parts = content.split("---", 2)
        fm = yaml.safe_load(parts[1])
        assert "mcpServers" not in fm or fm.get("mcpServers") is None

    def test_body_contains_rules_content(self):
        agent = _make_agent(prompt="Be very helpful.")
        cfg = generate_agent_config(agent, "claude-code")
        content = cfg["agent_profile"]["content"]
        assert "Be very helpful." in content

    def test_setup_commands_generated_for_external_mcps(self):
        ext_mcps = [{"name": "srv", "command": "npx", "args": ["-y", "srv"]}]
        agent = _make_agent(external_mcps=ext_mcps)
        cfg = generate_agent_config(agent, "claude-code")
        assert "mcp_setup_commands" in cfg
        assert len(cfg["mcp_setup_commands"]) == 1
        cmd = cfg["mcp_setup_commands"][0]
        assert cmd[0:6] == ["claude", "mcp", "add", "--scope", "local", "srv"]

    def test_no_otlp_env_in_config(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "claude-code")
        assert "otlp_env" not in cfg
        assert "claude_settings_snippet" not in cfg

    def test_claude_code_underscore_alias_removed(self):
        agent = _make_agent()
        with pytest.raises(ValueError):
            generate_agent_config(agent, "claude_code")

    def test_model_fallback_from_agent_when_no_option(self):
        agent = _make_agent(model_name="claude-sonnet-4-6-20250725")
        cfg = generate_agent_config(agent, "claude-code")
        content = cfg["agent_profile"]["content"]
        parts = content.split("---", 2)
        fm = yaml.safe_load(parts[1])
        assert fm["model"] == "sonnet"

    def test_model_fallback_from_agent_when_inherit(self):
        agent = _make_agent(model_name="claude-opus-4-6-20250725")
        cfg = generate_agent_config(agent, "claude-code", options={"model": "inherit"})
        content = cfg["agent_profile"]["content"]
        parts = content.split("---", 2)
        fm = yaml.safe_load(parts[1])
        assert fm["model"] == "opus"

    def test_explicit_model_option_overrides_agent(self):
        agent = _make_agent(model_name="claude-sonnet-4-6-20250725")
        cfg = generate_agent_config(agent, "claude-code", options={"model": "haiku"})
        content = cfg["agent_profile"]["content"]
        parts = content.split("---", 2)
        fm = yaml.safe_load(parts[1])
        assert fm["model"] == "haiku"

    def test_no_model_when_agent_has_empty_model_name(self):
        agent = _make_agent(model_name="")
        cfg = generate_agent_config(agent, "claude-code")
        content = cfg["agent_profile"]["content"]
        parts = content.split("---", 2)
        fm = yaml.safe_load(parts[1])
        assert "model" not in fm


# ═══════════════════════════════════════════════════════════════════
# 4b. _model_name_to_frontmatter
# ═══════════════════════════════════════════════════════════════════


class TestModelNameToFrontmatter:
    def test_sonnet_with_date(self):
        assert _model_name_to_frontmatter("claude-sonnet-4-6-20250725") == "sonnet"

    def test_opus_without_date(self):
        assert _model_name_to_frontmatter("claude-opus-4-6") == "opus"

    def test_haiku_with_date(self):
        assert _model_name_to_frontmatter("claude-haiku-4-5-20251001") == "haiku"

    def test_empty(self):
        assert _model_name_to_frontmatter("") == ""

    def test_non_claude_model_passthrough(self):
        assert _model_name_to_frontmatter("gpt-4o") == "gpt-4o"


# ═══════════════════════════════════════════════════════════════════
# 5. generate_agent_config — Cursor / VSCode
# ═══════════════════════════════════════════════════════════════════


class TestGenerateCursorVscode:
    def test_cursor_paths(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "cursor")
        assert cfg["agent_profile"]["path"] == ".cursor/agents/test-agent.md"
        assert cfg["mcp_config"]["path"] == ".cursor/mcp.json"

    def test_agent_content_not_empty(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "cursor")
        assert len(cfg["agent_profile"]["content"]) > 0

    def test_mcp_config_has_mcpservers_key(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "cursor")
        assert "mcpServers" in cfg["mcp_config"]["content"]

    def test_cursor_hooks_config_present(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "cursor")
        assert "hooks_config" in cfg
        hooks = cfg["hooks_config"]["content"]["hooks"]
        assert "beforeSubmitPrompt" in hooks
        assert "stop" in hooks

    def test_cursor_hooks_use_shared_session_push(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "cursor")
        cmd = cfg["hooks_config"]["content"]["hooks"]["beforeSubmitPrompt"][0]["command"]
        assert "hooks.session_push --harness cursor" in cmd

    def test_cursor_hooks_win32_uses_python(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "cursor", platform="win32")
        cmd = cfg["hooks_config"]["content"]["hooks"]["beforeSubmitPrompt"][0]["command"]
        assert cmd.startswith("python -m")
        assert "hooks.session_push --harness cursor" in cmd

    def test_cursor_hooks_unix_uses_python3(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "cursor", platform="linux")
        cmd = cfg["hooks_config"]["content"]["hooks"]["beforeSubmitPrompt"][0]["command"]
        assert cmd.startswith("python3 -m")
        assert "hooks.session_push --harness cursor" in cmd


# ═══════════════════════════════════════════════════════════════════
# 6. generate_agent_config — Kiro
# ═══════════════════════════════════════════════════════════════════


class TestGenerateKiro:
    def test_agent_profile_path(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "kiro")
        assert cfg["agent_profile"]["path"] == "~/.kiro/agents/test-agent.json"

    def test_agent_content_structure(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "kiro")
        content = cfg["agent_profile"]["content"]
        assert content["name"] == "test-agent"
        assert content["description"] == "A test agent"
        assert "You are helpful." in content["prompt"]
        assert "Agent Specialization" in content["prompt"]
        assert content["tools"] == ["*"]
        assert content["includeMcpJson"] is True
        # Kiro IDE 1.0 loads an agent carrying a "hooks" field but never fires
        # those hooks, so telemetry lives in the standalone v1 file instead.
        assert "hooks" not in content

    def test_omits_cli_only_tool_fields(self):
        """Kiro IDE 1.x ProfileLoader drops profiles carrying these.

        Without a "permissions" block, "allowedTools" or "toolsSettings" make
        ProfileLoader reject the profile (reasonCode "cli_only_agent") and the
        agent never reaches the IDE agent picker. Both were always emitted empty
        and were removed from the V3 agent schema.
        """
        agent = _make_agent()
        cfg = generate_agent_config(agent, "kiro")
        content = cfg["agent_profile"]["content"]
        assert "allowedTools" not in content
        assert "toolsSettings" not in content

    def test_hooks_contain_required_events(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "kiro")
        hooks_cfg = cfg["hooks_config"]
        assert hooks_cfg["path"] == "~/.kiro/hooks/observal.json"
        assert hooks_cfg["content"]["version"] == "v1"
        triggers = {h["trigger"] for h in hooks_cfg["content"]["hooks"]}
        assert triggers == {"UserPromptSubmit", "Stop"}

    def test_no_steering_file_generated(self):
        agent = _make_agent(prompt="Do the thing")
        cfg = generate_agent_config(agent, "kiro")
        assert "steering_file" not in cfg

    def test_kiro_agent_profile_truncates_description(self):
        agent = _make_agent(description="x" * 300)
        cfg = generate_agent_config(agent, "kiro")
        assert cfg["agent_profile"]["content"]["description"] == "x" * 200


# ═══════════════════════════════════════════════════════════════════
# 7. generate_agent_config — Codex
# ═══════════════════════════════════════════════════════════════════


class TestGenerateCodex:
    def test_agent_path(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "codex")
        assert cfg["agent_profile"]["path"] == ".codex/agents/test-agent.toml"

    def test_mcp_config_present(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "codex")
        assert "mcp_config" in cfg
        assert cfg["mcp_config"]["path"] == ".codex/config.toml"
        assert "mcp_servers" in cfg["mcp_config"]["content"]

    def test_mcp_config_empty_servers(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "codex")
        assert cfg["mcp_config"]["content"]["mcp_servers"] == {}

    def test_content_not_empty(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "codex")
        assert len(cfg["agent_profile"]["content"]) > 0


# ═══════════════════════════════════════════════════════════════════
# 8. generate_agent_config — Copilot
# ═══════════════════════════════════════════════════════════════════


class TestGenerateCopilot:
    def test_rules_path(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "copilot")
        assert cfg["agent_profile"]["path"] == ".github/agents/test-agent.agent.md"

    def test_mcp_config_present(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "copilot")
        assert "mcp_config" in cfg
        assert cfg["mcp_config"]["path"] == ".vscode/mcp.json"

    def test_mcp_config_uses_servers_key(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "copilot")
        assert "servers" in cfg["mcp_config"]["content"]

    def test_content_not_empty(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "copilot")
        assert len(cfg["agent_profile"]["content"]) > 0

    def test_external_mcps_get_type_stdio(self):
        ext = [{"name": "my-srv", "command": "npx", "args": ["-y", "my-srv"]}]
        agent = _make_agent(external_mcps=ext)
        cfg = generate_agent_config(agent, "copilot")
        entry = cfg["mcp_config"]["content"]["servers"]["my-srv"]
        assert entry["type"] == "stdio"
        assert entry["command"] == "npx"
        assert entry["args"] == ["-y", "my-srv"]

    def test_env_vars_preserved(self):
        ext = [{"name": "my-srv", "command": "npx", "args": [], "env": {"API_KEY": "secret"}}]
        agent = _make_agent(external_mcps=ext)
        cfg = generate_agent_config(agent, "copilot")
        entry = cfg["mcp_config"]["content"]["servers"]["my-srv"]
        assert entry["env"]["API_KEY"] == "secret"
        assert entry["env"]["OBSERVAL_AGENT_ID"] == str(agent.id)

    def test_scope_is_project(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "copilot")
        assert cfg["scope"] == "project"

    def test_no_mcpservers_key(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "copilot")
        assert "mcpServers" not in cfg["mcp_config"]["content"]


# ═══════════════════════════════════════════════════════════════════
# 10. generate_agent_config — External MCPs
# ═══════════════════════════════════════════════════════════════════


class TestExternalMcps:
    def test_external_mcps_appear_in_cursor_config(self):
        ext = [{"name": "ext-server", "command": "npx", "args": ["-y", "ext"]}]
        agent = _make_agent(external_mcps=ext)
        cfg = generate_agent_config(agent, "cursor")
        servers = cfg["mcp_config"]["content"]["mcpServers"]
        assert "ext-server" in servers

    def test_external_mcp_keeps_original_command(self):
        ext = [{"name": "ext-server", "command": "npx", "args": ["-y", "ext"], "id": "ext-id"}]
        agent = _make_agent(external_mcps=ext)
        cfg = generate_agent_config(agent, "cursor")
        entry = cfg["mcp_config"]["content"]["mcpServers"]["ext-server"]
        assert entry["command"] == "npx"
        assert entry["args"] == ["-y", "ext"]

    def test_external_mcp_string_args_split(self):
        ext = [{"name": "srv", "command": "node", "args": "serve.js --port 3000"}]
        agent = _make_agent(external_mcps=ext)
        cfg = generate_agent_config(agent, "cursor")
        entry = cfg["mcp_config"]["content"]["mcpServers"]["srv"]
        assert "serve.js" in entry["args"]
        assert "--port" in entry["args"]
        assert "3000" in entry["args"]

    def test_external_mcp_env_preserved(self):
        ext = [{"name": "srv", "command": "npx", "args": [], "env": {"API_KEY": "secret"}}]
        agent = _make_agent(external_mcps=ext)
        cfg = generate_agent_config(agent, "cursor")
        entry = cfg["mcp_config"]["content"]["mcpServers"]["srv"]
        assert entry["env"]["API_KEY"] == "secret"

    def test_external_mcp_with_empty_name_skipped(self):
        ext = [{"name": "", "command": "npx", "args": []}]
        agent = _make_agent(external_mcps=ext)
        cfg = generate_agent_config(agent, "cursor")
        assert len(cfg["mcp_config"]["content"]["mcpServers"]) == 0

    def test_agent_id_injected_into_all_mcp_entries(self):
        ext = [
            {"name": "srv-a", "command": "npx", "args": []},
            {"name": "srv-b", "command": "node", "args": ["s.js"]},
        ]
        agent = _make_agent(external_mcps=ext)
        cfg = generate_agent_config(agent, "cursor")
        servers = cfg["mcp_config"]["content"]["mcpServers"]
        for name in ("srv-a", "srv-b"):
            assert servers[name]["env"]["OBSERVAL_AGENT_ID"] == str(agent.id)


# ═══════════════════════════════════════════════════════════════════
# 11. generate_agent_config: MCP listings through the Claude Code adapter
# ═══════════════════════════════════════════════════════════════════


class TestMcpListingClaudeCodeAdapter:
    def test_builds_direct_entry(self):
        comp_id = uuid.uuid4()
        listing = MagicMock()
        listing.name = "my-mcp"
        listing.id = comp_id
        listing.url = None
        listing.command = "npx"
        listing.args = ["-y", "my-mcp"]
        listing.framework = None
        listing.docker_image = None
        listing.auto_approve = None
        listing.environment_variables = []

        agent = _make_agent(components=[_make_component("mcp", comp_id)])
        cfg = generate_agent_config(agent, "claude-code", mcp_listings={comp_id: listing})

        content = cfg["agent_profile"]["content"]
        parts = content.split("---", 2)
        fm = yaml.safe_load(parts[1])
        assert "my-mcp" in fm["mcpServers"]
        assert cfg["mcp_config"]["my-mcp"]["command"] == "npx"
        assert cfg["mcp_config"]["my-mcp"]["args"] == ["-y", "my-mcp"]
        assert cfg["mcp_setup_commands"] == [
            [
                "claude",
                "mcp",
                "add",
                "--scope",
                "local",
                "my-mcp",
                "-e",
                f"OBSERVAL_AGENT_ID={agent.id}",
                "--",
                "npx",
                "-y",
                "my-mcp",
            ]
        ]
        assert cfg["mcp_reset_commands"] == [["claude", "mcp", "remove", "--scope", "local", "my-mcp"]]

    @staticmethod
    def _remote_listing(comp_id: uuid.UUID, transport: str) -> MagicMock:
        listing = MagicMock()
        listing.name = "linear"
        listing.id = comp_id
        listing.url = "https://mcp.linear.app/mcp"
        listing.transport = transport
        listing.command = None
        listing.args = None
        listing.framework = None
        listing.docker_image = None
        listing.auto_approve = None
        listing.environment_variables = []
        return listing

    @pytest.mark.parametrize(
        ("transport", "flag", "scope", "claude_scope"),
        [
            ("streamable-http", "http", "user", "user"),
            ("sse", "sse", "project", "local"),
        ],
    )
    def test_registers_remote_servers(self, transport: str, flag: str, scope: str, claude_scope: str):
        comp_id = uuid.uuid4()
        agent = _make_agent(components=[_make_component("mcp", comp_id)])
        cfg = generate_agent_config(
            agent,
            "claude-code",
            mcp_listings={comp_id: self._remote_listing(comp_id, transport)},
            header_values={str(comp_id): {"Authorization": "Bearer tok", "X-Team": "eng"}},
            options={"scope": scope},
        )

        fm = yaml.safe_load(cfg["agent_profile"]["content"].split("---", 2)[1])
        assert "linear" in fm["mcpServers"]
        # Headers follow the URL: -H takes several values and would swallow the name.
        assert [
            "claude",
            "mcp",
            "add",
            "--transport",
            flag,
            "--scope",
            claude_scope,
            "linear",
            "https://mcp.linear.app/mcp",
            "-H",
            "Authorization: Bearer tok",
            "-H",
            "X-Team: eng",
        ] in cfg["mcp_setup_commands"]
        assert ["claude", "mcp", "remove", "--scope", claude_scope, "linear"] in cfg["mcp_reset_commands"]

    def test_user_scope_registers_stdio_servers_for_every_project(self):
        ext_mcps = [{"name": "srv", "command": "npx", "args": ["-y", "srv"]}]
        agent = _make_agent(external_mcps=ext_mcps)
        cfg = generate_agent_config(agent, "claude-code", options={"scope": "user"})
        assert cfg["mcp_setup_commands"][0][:6] == ["claude", "mcp", "add", "--scope", "user", "srv"]


# ═══════════════════════════════════════════════════════════════════
# 12. generate_agent_config — name sanitization in output
# ═══════════════════════════════════════════════════════════════════


class TestNameSanitizationInOutput:
    def test_special_chars_sanitized_in_claude_code_path(self):
        agent = _make_agent(name="my agent v2!")
        cfg = generate_agent_config(agent, "claude-code")
        path = cfg["agent_profile"]["path"]
        assert " " not in path
        assert "!" not in path

    def test_special_chars_sanitized_in_kiro_path(self):
        agent = _make_agent(name="my agent v2!")
        cfg = generate_agent_config(agent, "kiro")
        path = cfg["agent_profile"]["path"]
        assert " " not in path


# ═══════════════════════════════════════════════════════════════════
# Windows platform-aware Kiro config generation
# ═══════════════════════════════════════════════════════════════════

UNIX_FORBIDDEN_IN_WIN32 = ["cat |", "sed '", "$PPID", "$TERM", "$SHELL", "python3"]


class TestGenerateKiroWin32:
    """Fix checking: Windows Kiro configs must not contain Unix-only syntax."""

    def _all_hook_commands(self, cfg: dict) -> list[str]:
        """Extract all hook command strings from a Kiro v1 hooks file."""
        return [h["action"]["command"] for h in cfg["hooks_config"]["content"]["hooks"]]

    def test_win32_hooks_contain_no_unix_syntax(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "kiro", platform="win32")
        cmds = self._all_hook_commands(cfg)
        assert cmds, "Expected at least one hook command"
        for cmd in cmds:
            for forbidden in UNIX_FORBIDDEN_IN_WIN32:
                assert forbidden not in cmd, f"Found Unix-only '{forbidden}' in win32 hook: {cmd}"

    def test_win32_hooks_use_python_not_python3(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "kiro", platform="win32")
        cmds = self._all_hook_commands(cfg)
        for cmd in cmds:
            assert "python3" not in cmd
            assert "python " in cmd or "python -m" in cmd

    def test_win32_hooks_use_session_push(self):
        agent = _make_agent(name="my-cool-agent")
        cfg = generate_agent_config(agent, "kiro", platform="win32")
        cmds = self._all_hook_commands(cfg)
        for cmd in cmds:
            assert "hooks.session_push --harness kiro" in cmd
            # One hooks file per scope serves every agent, so it carries no
            # per-agent id; the CLI attributes from session metadata instead.
            assert "OBSERVAL_AGENT_ID" not in cmd
            assert "OBSERVAL_AGENT_NAME" not in cmd

    def test_hooks_omit_model_flag(self):
        """Model is detected from Kiro SQLite at runtime, not baked into hook commands."""
        agent = _make_agent(model_name="claude-sonnet-4")
        cfg = generate_agent_config(agent, "kiro", platform="win32")
        cmds = self._all_hook_commands(cfg)
        for cmd in cmds:
            assert "--model" not in cmd

    def test_win32_has_session_push_events_only(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "kiro", platform="win32")
        triggers = {h["trigger"] for h in cfg["hooks_config"]["content"]["hooks"]}
        assert triggers == {"UserPromptSubmit", "Stop"}

    def test_win32_agent_profile_path_unchanged(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "kiro", platform="win32")
        assert cfg["agent_profile"]["path"] == "~/.kiro/agents/test-agent.json"


class TestGenerateKiroPreservation:
    """Preservation checking: Unix Kiro configs must be identical with or without platform."""

    def test_no_platform_matches_linux(self):
        agent = _make_agent()
        cfg_default = generate_agent_config(agent, "kiro")
        cfg_linux = generate_agent_config(agent, "kiro", platform="linux")
        assert cfg_default == cfg_linux

    def test_darwin_matches_default(self):
        agent = _make_agent()
        cfg_default = generate_agent_config(agent, "kiro")
        cfg_darwin = generate_agent_config(agent, "kiro", platform="darwin")
        assert cfg_default == cfg_darwin

    def test_empty_platform_matches_default(self):
        agent = _make_agent()
        cfg_default = generate_agent_config(agent, "kiro")
        cfg_empty = generate_agent_config(agent, "kiro", platform="")
        assert cfg_default == cfg_empty

    def test_unix_hooks_use_kiro_session_push(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "kiro", platform="linux")
        hooks = cfg["hooks_config"]["content"]["hooks"]
        cmd = next(h for h in hooks if h["trigger"] == "UserPromptSubmit")["action"]["command"]
        assert "python3 -m observal_cli.hooks.session_push --harness kiro" in cmd
        assert "OBSERVAL_AGENT_ID" not in cmd
        assert "OBSERVAL_AGENT_NAME" not in cmd
        assert "cat |" not in cmd
        assert "sed " not in cmd
        assert "curl" not in cmd

    def test_non_kiro_cursor_harnesses_unaffected_by_platform(self):
        agent = _make_agent()
        for harness in ["claude-code", "codex", "copilot", "opencode"]:
            cfg_default = generate_agent_config(agent, harness)
            cfg_win32 = generate_agent_config(agent, harness, platform="win32")
            assert cfg_default == cfg_win32, f"{harness} config changed with platform=win32"


class TestHookConfigGeneratorWin32:
    """Fix checking: hook_config_generator Windows output has no Unix syntax."""

    def test_win32_kiro_hook_no_unix_syntax(self):
        from services.hook_config_generator import generate_hook_telemetry_config

        listing = MagicMock()
        listing.event = "UserPromptSubmit"
        cfg = generate_hook_telemetry_config(listing, "kiro", platform="win32")
        cmd = cfg["hooks"][0]["action"]["command"]
        for forbidden in UNIX_FORBIDDEN_IN_WIN32:
            assert forbidden not in cmd, f"Found Unix-only '{forbidden}' in win32 hook: {cmd}"
        assert "python " in cmd or "python -m" in cmd

    def test_win32_kiro_stop_hook_no_unix_syntax(self):
        from services.hook_config_generator import generate_hook_telemetry_config

        listing = MagicMock()
        listing.event = "Stop"
        cfg = generate_hook_telemetry_config(listing, "kiro", platform="win32")
        cmd = cfg["hooks"][0]["action"]["command"]
        for forbidden in UNIX_FORBIDDEN_IN_WIN32:
            assert forbidden not in cmd, f"Found Unix-only '{forbidden}' in win32 hook: {cmd}"
        assert "python " in cmd or "python -m" in cmd

    def test_unix_kiro_hook_preserved(self):
        from services.hook_config_generator import generate_hook_telemetry_config

        listing = MagicMock()
        listing.event = "UserPromptSubmit"
        cfg_default = generate_hook_telemetry_config(listing, "kiro")
        cfg_linux = generate_hook_telemetry_config(listing, "kiro", platform="linux")
        assert cfg_default == cfg_linux

    def test_unix_kiro_stop_hook_preserved(self):
        from services.hook_config_generator import generate_hook_telemetry_config

        listing = MagicMock()
        listing.event = "Stop"
        cfg_default = generate_hook_telemetry_config(listing, "kiro")
        cfg_linux = generate_hook_telemetry_config(listing, "kiro", platform="linux")
        assert cfg_default == cfg_linux

    def test_non_kiro_harnesses_unaffected(self):
        from services.hook_config_generator import generate_hook_telemetry_config

        listing = MagicMock()
        listing.event = "PreToolUse"
        cfg_default = generate_hook_telemetry_config(listing, "claude-code")
        cfg_win32 = generate_hook_telemetry_config(listing, "claude-code", platform="win32")
        assert cfg_default == cfg_win32
