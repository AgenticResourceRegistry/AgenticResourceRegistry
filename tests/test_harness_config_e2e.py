# SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Comprehensive end-to-end tests for issue #434: first-class harness support.

Covers Codex CLI, GitHub Copilot (VS Code), and OpenCode across:

1. Server-side config generation (agent_config_generator)
2. CLI pull command (cmd_pull._dict_to_toml, _write_file, full pull flow)
3. Constants (VALID_HARNESSES, HARNESS_CAPABILITIES)
4. harness compatibility warnings
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from observal_cli.cmd_pull import _dict_to_toml, _write_file
from observal_cli.constants import HARNESS_CAPABILITIES, VALID_HARNESSES
from observal_cli.main import app as cli_app
from services.agent_lock import PinnedListing
from services.harness import generate_agent_config
from services.harness.helpers import _check_harness_compatibility

# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════


def _make_component(component_type="mcp", component_id=None):
    comp = MagicMock()
    comp.component_type = component_type
    comp.component_id = component_id or uuid.uuid4()
    return comp


def _make_agent(
    name="test-agent",
    description="A test agent",
    prompt="You are helpful.",
    model_name="claude-sonnet-4",
    components=None,
    external_mcps=None,
):
    agent = MagicMock()
    agent.id = uuid.uuid4()
    agent.name = name
    agent.description = description
    agent.prompt = prompt
    agent.model_name = model_name
    agent.components = components or []
    agent.external_mcps = external_mcps or []
    agent.required_capabilities = []
    return agent


# ═══════════════════════════════════════════════════════════════════
# 1. CONSTANTS — VALID_HARNESSES and HARNESS_CAPABILITIES
# ═══════════════════════════════════════════════════════════════════


class TestConstants:
    def test_codex_in_valid_harnesses(self):
        assert "codex" in VALID_HARNESSES

    def test_copilot_in_valid_harnesses(self):
        assert "copilot" in VALID_HARNESSES

    def test_opencode_in_valid_harnesses(self):
        assert "opencode" in VALID_HARNESSES

    def test_codex_feature_matrix(self):
        assert "codex" in HARNESS_CAPABILITIES
        assert HARNESS_CAPABILITIES["codex"] == {"mcp_servers", "hooks", "skills"}

    def test_copilot_feature_matrix(self):
        assert "copilot" in HARNESS_CAPABILITIES
        assert HARNESS_CAPABILITIES["copilot"] == {"mcp_servers", "hooks", "skills", "prompts"}

    def test_copilot_session_parser(self):
        from observal_shared.harness_registry import HARNESS_REGISTRY

        assert HARNESS_REGISTRY["copilot"]["session_parser"] == "copilot-cli"

    def test_copilot_cli_session_parser(self):
        from observal_shared.harness_registry import HARNESS_REGISTRY

        assert HARNESS_REGISTRY["copilot-cli"]["session_parser"] == "copilot-cli"

    def test_copilot_cli_hook_events_map(self):
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
        from observal_shared.harness_registry import HARNESS_REGISTRY

        expected = {
            "SessionStart": "SessionStart",
            "UserPromptSubmit": "UserPromptSubmit",
            "PreToolUse": "PreToolUse",
            "PostToolUse": "PostToolUse",
            "Stop": "Stop",
        }
        assert HARNESS_REGISTRY["copilot"]["hook_events_map"] == expected

    def test_opencode_feature_matrix(self):
        assert "opencode" in HARNESS_CAPABILITIES
        assert HARNESS_CAPABILITIES["opencode"] == {"hooks", "mcp_servers", "skills"}

    def test_copilot_cli_in_valid_harnesses(self):
        assert "copilot-cli" in VALID_HARNESSES

    def test_copilot_cli_feature_matrix(self):
        assert "copilot-cli" in HARNESS_CAPABILITIES
        assert HARNESS_CAPABILITIES["copilot-cli"] == {"mcp_servers", "hooks", "skills", "prompts"}


# ═══════════════════════════════════════════════════════════════════
# 2. SERVER-SIDE CONFIG GENERATION
# ═══════════════════════════════════════════════════════════════════


class TestGenerateCodexConfig:
    def test_agent_path_is_codex_agent_toml(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "codex")
        assert cfg["agent_profile"]["path"] == ".codex/agents/test-agent.toml"

    def test_mcp_config(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "codex")
        assert cfg["mcp_config"]["path"] == ".codex/config.toml"

    def test_mcp_config_root_key(self):
        ext = [{"name": "my-server", "command": "npx", "args": ["-y", "my-server"]}]
        agent = _make_agent(external_mcps=ext)
        cfg = generate_agent_config(agent, "codex")
        assert "mcp_servers" in cfg["mcp_config"]["content"]
        assert "my-server" in cfg["mcp_config"]["content"]["mcp_servers"]

    def test_mcp_config_entry_has_command_and_args(self):
        ext = [{"name": "my-server", "command": "npx", "args": ["-y", "my-server"]}]
        agent = _make_agent(external_mcps=ext)
        cfg = generate_agent_config(agent, "codex")
        servers = cfg["mcp_config"]["content"]["mcp_servers"]
        assert servers["my-server"]["command"] == "npx"
        assert servers["my-server"]["args"] == ["-y", "my-server"]

    def test_mcp_config_injects_agent_id(self):
        ext = [{"name": "srv", "command": "npx", "args": ["-y", "srv"]}]
        agent = _make_agent(external_mcps=ext)
        cfg = generate_agent_config(agent, "codex")
        servers = cfg["mcp_config"]["content"]["mcp_servers"]
        assert servers["srv"]["env"]["OBSERVAL_AGENT_ID"] == str(agent.id)

    def test_scope_is_project(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "codex")
        assert cfg["scope"] == "project"

    def test_agent_content_not_empty(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "codex")
        assert len(cfg["agent_profile"]["content"]) > 0

    def test_empty_mcp_configs_still_produces_valid_structure(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "codex")
        assert "mcp_config" in cfg
        assert "mcp_servers" in cfg["mcp_config"]["content"]
        assert cfg["mcp_config"]["content"]["mcp_servers"] == {}

    def test_agent_content_uses_agent_prompt(self):
        agent = _make_agent(prompt="Always use Python.")
        cfg = generate_agent_config(agent, "codex")
        assert "Always use Python." in cfg["agent_profile"]["content"]


class TestGenerateCopilotConfig:
    def test_rules_path(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "copilot")
        assert cfg["agent_profile"]["path"] == ".github/agents/test-agent.agent.md"

    def test_mcp_config(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "copilot")
        assert cfg["mcp_config"]["path"] == ".vscode/mcp.json"

    def test_mcp_config_root_key_is_servers(self):
        ext = [{"name": "my-server", "command": "npx", "args": ["-y", "my-server"]}]
        agent = _make_agent(external_mcps=ext)
        cfg = generate_agent_config(agent, "copilot")
        assert "servers" in cfg["mcp_config"]["content"]
        assert "my-server" in cfg["mcp_config"]["content"]["servers"]

    def test_copilot_entries_have_type_stdio(self):
        ext = [{"name": "my-server", "command": "npx", "args": ["-y", "my-server"]}]
        agent = _make_agent(external_mcps=ext)
        cfg = generate_agent_config(agent, "copilot")
        entry = cfg["mcp_config"]["content"]["servers"]["my-server"]
        assert entry["type"] == "stdio"
        assert entry["command"] == "npx"
        assert isinstance(entry["args"], list)

    def test_copilot_preserves_env_vars(self):
        ext = [{"name": "my-server", "command": "npx", "args": [], "env": {"API_KEY": "secret"}}]
        agent = _make_agent(external_mcps=ext)
        cfg = generate_agent_config(agent, "copilot")
        entry = cfg["mcp_config"]["content"]["servers"]["my-server"]
        assert entry["env"]["API_KEY"] == "secret"
        assert entry["env"]["OBSERVAL_AGENT_ID"] == str(agent.id)

    def test_scope_is_project(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "copilot")
        assert cfg["scope"] == "project"

    def test_rules_content_not_empty(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "copilot")
        assert len(cfg["agent_profile"]["content"]) > 0

    def test_empty_mcp_configs_still_produces_valid_structure(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "copilot")
        assert "mcp_config" in cfg
        assert "servers" in cfg["mcp_config"]["content"]

    def test_multiple_mcps_all_get_type_stdio(self):
        ext = [
            {"name": "srv-a", "command": "npx", "args": ["-y", "a"]},
            {"name": "srv-b", "command": "node", "args": ["b.js"]},
        ]
        agent = _make_agent(external_mcps=ext)
        cfg = generate_agent_config(agent, "copilot")
        servers = cfg["mcp_config"]["content"]["servers"]
        assert servers["srv-a"]["type"] == "stdio"
        assert servers["srv-b"]["type"] == "stdio"


class TestGenerateCopilotCliConfig:
    def test_direct_pinned_skill_is_delivered_even_with_historical_git_coordinates(self):
        listing_id = uuid.uuid4()
        component = _make_component("skill", listing_id)
        listing = SimpleNamespace(
            id=listing_id,
            name="Security Review",
            namespace="acme",
            slug="security-review",
            delivery_mode="git_fetch",
            git_url="https://example.test/old.git",
            git_ref="main",
            skill_md_content="OLD GIT",
            description="Older listing",
        )
        selected = SimpleNamespace(
            delivery_mode="registry_direct",
            git_url="https://example.test/obsolete.git",
            git_ref="old",
            skill_md_content="---\nname: security-review\ndescription: Release\n---\nPinned body\n",
            description="Pinned release",
            task_type="general",
            slash_command=None,
            script_content=None,
            script_filename=None,
            skill_path="/",
        )
        agent = _make_agent(components=[component])

        config = generate_agent_config(
            agent, "copilot-cli", skill_listings={listing_id: PinnedListing(listing, selected)}
        )
        assert len(config["skill_components"]) == 1
        skill = config["skill_components"][0]
        assert skill["name"] == "security-review"
        assert skill["delivery_mode"] == "registry_direct"
        assert skill["git_url"] is None
        assert skill["git_ref"] is None
        assert skill["skill_md_content"] == selected.skill_md_content

    def test_rules_path(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "copilot-cli")
        assert cfg["agent_profile"]["path"] == ".github/agents/test-agent.agent.md"

    def test_mcp_config(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "copilot-cli")
        assert cfg["mcp_config"]["path"] == "~/.copilot/mcp-config.json"

    def test_mcp_config_root_key_is_mcp_servers(self):
        ext = [{"name": "my-server", "command": "npx", "args": ["-y", "my-server"]}]
        agent = _make_agent(external_mcps=ext)
        cfg = generate_agent_config(agent, "copilot-cli")
        assert "mcpServers" in cfg["mcp_config"]["content"]
        assert "servers" not in cfg["mcp_config"]["content"]
        assert "my-server" in cfg["mcp_config"]["content"]["mcpServers"]

    def test_copilot_cli_entries_have_type_stdio(self):
        ext = [{"name": "my-server", "command": "npx", "args": ["-y", "my-server"]}]
        agent = _make_agent(external_mcps=ext)
        cfg = generate_agent_config(agent, "copilot-cli")
        entry = cfg["mcp_config"]["content"]["mcpServers"]["my-server"]
        assert entry["type"] == "stdio"
        assert entry["command"] == "npx"
        assert isinstance(entry["args"], list)
        assert entry["tools"] == ["*"]

    def test_copilot_cli_preserves_env_vars(self):
        ext = [{"name": "my-server", "command": "npx", "args": [], "env": {"API_KEY": "secret"}}]
        agent = _make_agent(external_mcps=ext)
        cfg = generate_agent_config(agent, "copilot-cli")
        entry = cfg["mcp_config"]["content"]["mcpServers"]["my-server"]
        assert entry["env"]["API_KEY"] == "secret"
        assert entry["env"]["OBSERVAL_AGENT_ID"] == str(agent.id)

    def test_scope_is_project(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "copilot-cli")
        assert cfg["scope"] == "project"

    def test_rules_content_not_empty(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "copilot-cli")
        assert len(cfg["agent_profile"]["content"]) > 0

    def test_empty_mcp_configs_still_produces_valid_structure(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "copilot-cli")
        assert "mcp_config" in cfg
        assert "mcpServers" in cfg["mcp_config"]["content"]

    def test_multiple_mcps_all_get_type_stdio(self):
        ext = [
            {"name": "srv-a", "command": "npx", "args": ["-y", "a"]},
            {"name": "srv-b", "command": "node", "args": ["b.js"]},
        ]
        agent = _make_agent(external_mcps=ext)
        cfg = generate_agent_config(agent, "copilot-cli")
        servers = cfg["mcp_config"]["content"]["mcpServers"]
        assert servers["srv-a"]["type"] == "stdio"
        assert servers["srv-b"]["type"] == "stdio"
        assert servers["srv-a"]["tools"] == ["*"]
        assert servers["srv-b"]["tools"] == ["*"]


class TestGenerateOpenCodeConfig:
    def test_rules_path(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "opencode")
        assert cfg["agent_profile"]["path"] == "~/.config/opencode/agents/test-agent.md"

    def test_rules_path_project_scope(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "opencode", options={"scope": "project"})
        assert cfg["agent_profile"]["path"] == ".opencode/agents/test-agent.md"

    def test_mcp_config(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "opencode")
        assert cfg["mcp_config"]["path"] == "~/.config/opencode/opencode.json"

    def test_plugin_not_in_agent_config(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "opencode")

        assert "hooks_config" not in cfg

    def test_mcp_config_root_key_is_mcp(self):
        ext = [{"name": "my-server", "command": "npx", "args": ["-y", "my-server"]}]
        agent = _make_agent(external_mcps=ext)
        cfg = generate_agent_config(agent, "opencode")
        assert "mcp" in cfg["mcp_config"]["content"]
        assert "my-server" in cfg["mcp_config"]["content"]["mcp"]
        # OpenCode rewrites a project config without "$schema" whenever it starts.
        assert cfg["mcp_config"]["content"]["$schema"] == "https://opencode.ai/config.json"

    def test_opencode_entries_have_type_local(self):
        ext = [{"name": "my-server", "command": "npx", "args": ["-y", "my-server"]}]
        agent = _make_agent(external_mcps=ext)
        cfg = generate_agent_config(agent, "opencode")
        entry = cfg["mcp_config"]["content"]["mcp"]["my-server"]
        assert entry["type"] == "local"
        assert isinstance(entry["command"], list)
        assert entry["command"][0] == "npx"

    def test_opencode_command_is_flat_array(self):
        ext = [{"name": "my-server", "command": "npx", "args": ["-y", "my-server"]}]
        agent = _make_agent(external_mcps=ext)
        cfg = generate_agent_config(agent, "opencode")
        entry = cfg["mcp_config"]["content"]["mcp"]["my-server"]
        assert entry["command"] == ["npx", "-y", "my-server"]

    def test_opencode_preserves_env_vars(self):
        ext = [{"name": "my-server", "command": "npx", "args": [], "env": {"FOO": "bar"}}]
        agent = _make_agent(external_mcps=ext)
        cfg = generate_agent_config(agent, "opencode")
        entry = cfg["mcp_config"]["content"]["mcp"]["my-server"]
        assert entry["environment"]["FOO"] == "bar"
        assert entry["environment"]["OBSERVAL_AGENT_ID"] == str(agent.id)

    def test_scope_is_user(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "opencode")
        assert cfg["scope"] == "user"

    def test_rules_content_not_empty(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "opencode")
        assert len(cfg["agent_profile"]["content"]) > 0

    def test_empty_mcp_configs_still_produces_valid_structure(self):
        agent = _make_agent()
        cfg = generate_agent_config(agent, "opencode")
        assert "mcp_config" in cfg
        assert "mcp" in cfg["mcp_config"]["content"]

    def test_no_separate_args_field(self):
        ext = [{"name": "my-server", "command": "npx", "args": ["-y", "my-server"]}]
        agent = _make_agent(external_mcps=ext)
        cfg = generate_agent_config(agent, "opencode")
        entry = cfg["mcp_config"]["content"]["mcp"]["my-server"]
        assert "args" not in entry, "OpenCode entries should not have separate 'args' key"
        assert isinstance(entry["command"], list), "OpenCode 'command' should be a flat array"

    def test_hooks_plugin_is_valid_javascript_source(self):
        from observal_shared.opencode_plugin_source import OPENCODE_PLUGIN_SOURCE

        plugin = OPENCODE_PLUGIN_SOURCE
        assert "fetch(`${config.server_url}/api/v1/ingest/session`" in plugin
        assert "from loguru import logger" not in plugin
        assert "export const ObservalPlugin" in plugin
        assert '"session.created"' in plugin
        assert '"session.idle"' in plugin
        assert '"message.updated"' in plugin
        assert '"session.next.agent.switched"' in plugin
        assert 'harness: "opencode"' in plugin
        assert "LOCKFILE_PATH" in plugin
        assert "agent_version: pending.agentVersion" in plugin
        assert "client.app.log" in plugin


# ═══════════════════════════════════════════════════════════════════
# 3. harness COMPATIBILITY WARNINGS
# ═══════════════════════════════════════════════════════════════════


class TestIdeCompatibilityWarnings:
    def test_codex_no_longer_warns_on_mcp_requirement(self):
        agent = _make_agent()
        agent.required_capabilities = ["mcp_servers"]
        warnings = _check_harness_compatibility(agent, "codex")
        assert len(warnings) == 0

    def test_copilot_no_longer_warns_on_mcp_requirement(self):
        agent = _make_agent()
        agent.required_capabilities = ["mcp_servers"]
        warnings = _check_harness_compatibility(agent, "copilot")
        assert len(warnings) == 0

    def test_opencode_warns_on_unsupported_features(self):
        agent = _make_agent()
        agent.required_capabilities = ["skills", "hooks"]
        warnings = _check_harness_compatibility(agent, "opencode")
        # opencode now supports both hooks and skills
        assert len(warnings) == 0

    def test_no_warnings_for_supported_features(self):
        agent = _make_agent()
        agent.required_capabilities = []
        for harness in ("codex", "copilot", "opencode"):
            if harness in HARNESS_CAPABILITIES:
                warnings = _check_harness_compatibility(agent, harness)
                assert len(warnings) == 0, f"Unexpected warnings for {harness}: {warnings}"

    def test_copilot_supports_mcp_servers(self):
        agent = _make_agent()
        agent.required_capabilities = ["mcp_servers"]
        warnings = _check_harness_compatibility(agent, "copilot")
        assert len(warnings) == 0

    def test_copilot_cli_supports_hooks(self):
        agent = _make_agent()
        agent.required_capabilities = ["mcp_servers", "hooks"]
        warnings = _check_harness_compatibility(agent, "copilot-cli")
        assert len(warnings) == 0

    def test_codex_warns_on_unsupported_features(self):
        agent = _make_agent()
        agent.required_capabilities = ["skills", "hooks"]
        warnings = _check_harness_compatibility(agent, "codex")
        # Codex now supports hooks and skills
        assert len(warnings) == 0

    def test_codex_warns_on_skills_requirement(self):
        agent = _make_agent()
        agent.required_capabilities = ["skills"]
        warnings = _check_harness_compatibility(agent, "codex")
        # Codex now supports skills
        assert len(warnings) == 0


# ═══════════════════════════════════════════════════════════════════
# 6. PULL — _dict_to_toml
# ═══════════════════════════════════════════════════════════════════


class TestDictToToml:
    def test_basic_section(self):
        d = {"mcp_servers": {"my-srv": {"command": "npx", "args": ["-y", "my-srv"]}}}
        toml = _dict_to_toml(d)
        assert "[mcp_servers.my-srv]" in toml
        assert 'command = "npx"' in toml
        assert 'args = ["-y", "my-srv"]' in toml

    def test_env_vars(self):
        d = {"mcp_servers": {"my-srv": {"command": "npx", "env": {"K": "V"}}}}
        toml = _dict_to_toml(d)
        assert "env.K" in toml or "env" in toml

    def test_multiple_servers(self):
        d = {"mcp_servers": {"srv-a": {"command": "echo"}, "srv-b": {"command": "cat"}}}
        toml = _dict_to_toml(d)
        assert "[mcp_servers.srv-a]" in toml
        assert "[mcp_servers.srv-b]" in toml

    def test_empty_servers(self):
        d = {"mcp_servers": {}}
        toml = _dict_to_toml(d)
        assert toml.strip() == ""

    def test_boolean_values(self):
        d = {"mcp_servers": {"my-srv": {"command": "npx", "autoApprove": True}}}
        toml = _dict_to_toml(d)
        assert "autoApprove = true" in toml

    def test_string_values_with_quotes(self):
        d = {"mcp_servers": {"my-srv": {"command": 'echo "hello"'}}}
        toml = _dict_to_toml(d)
        assert "my-srv" in toml


# ═══════════════════════════════════════════════════════════════════
# 7. PULL — _write_file
# ═══════════════════════════════════════════════════════════════════


class TestWriteFile:
    def test_creates_json_file(self, tmp_path):
        p = tmp_path / "config.json"
        content = {"mcpServers": {"srv": {"command": "npx"}}}
        status = _write_file(p, content)
        assert status == "created"
        data = json.loads(p.read_text())
        assert "mcpServers" in data

    def test_creates_toml_file(self, tmp_path):
        p = tmp_path / "config.toml"
        content = {"mcp_servers": {"srv": {"command": "npx"}}}
        status = _write_file(p, content)
        assert status == "created"
        assert "[mcp_servers.srv]" in p.read_text()

    def test_updates_existing_json(self, tmp_path):
        p = tmp_path / "mcp.json"
        p.write_text(json.dumps({"mcpServers": {"old": {"command": "old"}}}))
        content = {"mcpServers": {"new": {"command": "new"}}}
        status = _write_file(p, content, merge_mcp=True)
        assert status == "merged"
        data = json.loads(p.read_text())
        assert "old" in data["mcpServers"]
        assert "new" in data["mcpServers"]

    def test_merges_toml(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text("[mcp_servers.old]\ncommand = 'echo'\n")
        content = {"mcp_servers": {"new": {"command": "npx"}}}
        status = _write_file(p, content, merge_mcp=True)
        assert status == "merged"
        merged = p.read_text()
        assert "[mcp_servers.old]" in merged
        assert "npx" in merged

    def test_creates_parent_directories(self, tmp_path):
        p = tmp_path / ".codex" / "config.toml"
        content = {"mcp_servers": {"srv": {"command": "npx"}}}
        _write_file(p, content)
        assert p.exists()

    def test_non_merge_creates_new_file(self, tmp_path):
        p = tmp_path / "new.json"
        content = {"servers": {"srv": {"command": "npx"}}}
        status = _write_file(p, content, merge_mcp=False)
        assert status == "created"

    def test_write_string_content(self, tmp_path):
        p = tmp_path / "AGENTS.md"
        status = _write_file(p, "# Rules\n\nBe helpful.")
        assert status == "created"
        assert "Be helpful" in p.read_text()

    def test_update_status_for_existing_file(self, tmp_path):
        p = tmp_path / "AGENTS.md"
        p.write_text("old content")
        status = _write_file(p, "new content")
        assert status == "updated"

    def test_merges_yaml_and_preserves_unrelated_config(self, tmp_path):
        import yaml

        p = tmp_path / "config.yaml"
        p.write_text(
            yaml.safe_dump(
                {
                    "active_provider": "anthropic",
                    "GOOSE_MODE": "smart_approve",
                    "extensions": {"developer": {"type": "builtin", "enabled": True}},
                }
            )
        )

        status = _write_file(p, {"extensions": {"fs": {"type": "stdio", "cmd": "npx"}}}, merge_mcp=True)

        assert status == "merged"
        data = yaml.safe_load(p.read_text())
        assert data["active_provider"] == "anthropic"
        assert data["GOOSE_MODE"] == "smart_approve"
        assert set(data["extensions"]) == {"developer", "fs"}

    def test_creates_yaml_when_absent(self, tmp_path):
        import yaml

        p = tmp_path / ".config" / "goose" / "config.yaml"
        status = _write_file(p, {"extensions": {"fs": {"type": "stdio"}}}, merge_mcp=True)
        assert status == "created"
        assert yaml.safe_load(p.read_text())["extensions"]["fs"]["type"] == "stdio"

    def test_leaves_unparseable_yaml_untouched(self, tmp_path):
        p = tmp_path / "config.yaml"
        p.write_text("extensions: [unclosed\n")

        with pytest.raises(ValueError, match="unreadable YAML"):
            _write_file(p, {"extensions": {"fs": {}}}, merge_mcp=True)

        assert p.read_text() == "extensions: [unclosed\n"


# ═══════════════════════════════════════════════════════════════════
# 8. PULL — Full CLI flow (E2E per harness)
# ═══════════════════════════════════════════════════════════════════

runner = CliRunner()

_FAKE_CONFIG = {"server_url": "http://localhost:8000", "api_key": "test-key"}


@contextmanager
def _patch_config():
    with (
        patch("observal_cli.config.get_or_exit", return_value=_FAKE_CONFIG),
        patch("observal_cli.config.load", return_value=_FAKE_CONFIG),
    ):
        yield


@pytest.fixture(autouse=True)
def isolated_lockfile(tmp_path, monkeypatch):
    state = tmp_path / ".observal"
    monkeypatch.setattr("observal_cli.lockfile.LOCKFILE_PATH", state / "lockfile.json")
    monkeypatch.setattr("observal_cli.lockfile._LOCKFILE_LOCK", state / "lockfile.lock")
    monkeypatch.setattr("observal_cli.config.CONFIG_DIR", state)
    monkeypatch.setattr("observal_cli.config.CONFIG_FILE", state / "config.json")
    monkeypatch.setattr("observal_cli.config.ALIASES_FILE", state / "aliases.json")
    monkeypatch.setattr("observal_cli.config.LAST_RESULTS_FILE", state / "last_results.json")


def _patch_post(return_value):
    return patch("observal_cli.client.post_public", return_value=return_value)


_AGENT_DETAIL_NO_ENV = {
    "id": "abc123",
    "name": "my-agent",
    "mcp_links": [],
    "component_links": [],
}


def _patch_get_agent(detail=None):
    detail = detail or _AGENT_DETAIL_NO_ENV
    return patch("observal_cli.client.get", return_value=detail)


class TestPullCodex:
    def test_writes_custom_agent_and_mcp_toml(self, tmp_path):
        snippet = {
            "config_snippet": {
                "agent_profile": {
                    "path": ".codex/agents/test-agent.toml",
                    "content": 'name = "test-agent"\ndeveloper_instructions = "Be precise."\n',
                },
                "mcp_config": {
                    "path": ".codex/config.toml",
                    "content": {
                        "mcp_servers": {
                            "my-server": {
                                "command": "npx",
                                "args": ["-y", "my-server"],
                            }
                        }
                    },
                },
                "scope": "project",
            }
        }
        with _patch_config(), _patch_get_agent(), _patch_post(snippet):
            result = runner.invoke(
                cli_app, ["agent", "pull", "abc123", "--harness", "codex", "--dir", str(tmp_path), "--no-prompt"]
            )
        assert result.exit_code == 0, result.output
        agent = tmp_path / ".codex" / "agents" / "test-agent.toml"
        assert agent.exists()
        assert "Be precise" in agent.read_text()
        config = tmp_path / ".codex" / "config.toml"
        assert config.exists()
        content = config.read_text()
        assert "mcp_servers" in content
        assert "npx" in content

    def test_writes_only_agent_when_no_mcp(self, tmp_path):
        snippet = {
            "config_snippet": {
                "agent_profile": {
                    "path": ".codex/agents/simple-agent.toml",
                    "content": 'name = "simple-agent"\ndeveloper_instructions = "Simple Agent"\n',
                },
            }
        }
        with _patch_config(), _patch_get_agent(), _patch_post(snippet):
            result = runner.invoke(
                cli_app, ["agent", "pull", "abc123", "--harness", "codex", "--dir", str(tmp_path), "--no-prompt"]
            )
        assert result.exit_code == 0, result.output
        assert (tmp_path / ".codex" / "agents" / "simple-agent.toml").exists()


class TestPullCopilot:
    def test_writes_copilot_instructions_and_mcp(self, tmp_path):
        snippet = {
            "config_snippet": {
                "agent_profile": {
                    "path": ".github/copilot-instructions.md",
                    "content": "# Copilot Rules\n\nUse Python.\n",
                },
                "mcp_config": {
                    "path": ".vscode/mcp.json",
                    "content": {
                        "servers": {
                            "my-server": {
                                "type": "stdio",
                                "command": "npx",
                                "args": ["-y", "my-server"],
                            }
                        }
                    },
                },
            }
        }
        with _patch_config(), _patch_get_agent(), _patch_post(snippet):
            result = runner.invoke(
                cli_app, ["agent", "pull", "abc123", "--harness", "copilot", "--dir", str(tmp_path), "--no-prompt"]
            )
        assert result.exit_code == 0, result.output
        rules = tmp_path / ".github" / "copilot-instructions.md"
        assert rules.exists()
        assert "Copilot Rules" in rules.read_text()
        mcp = tmp_path / ".vscode" / "mcp.json"
        assert mcp.exists()
        data = json.loads(mcp.read_text())
        assert "servers" in data
        assert data["servers"]["my-server"]["type"] == "stdio"


class TestPullOpenCode:
    def test_writes_agent_profile_and_opencode_json(self, tmp_path):
        snippet = {
            "config_snippet": {
                "agent_profile": {
                    "path": ".opencode/agents/test-agent.md",
                    "content": "# OpenCode Rules\n\nBe concise.\n",
                },
                "mcp_config": {
                    "path": "~/.config/opencode/opencode.json",
                    "content": {
                        "mcp": {
                            "my-server": {
                                "type": "local",
                                "command": ["npx", "-y", "my-server"],
                            }
                        }
                    },
                },
                "scope": "project",
            }
        }
        with _patch_config(), _patch_get_agent(), _patch_post(snippet):
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
        rules = tmp_path / ".opencode" / "agents" / "test-agent.md"
        assert rules.exists()
        config = tmp_path / ".config" / "opencode" / "opencode.json"
        assert config.exists()
        data = json.loads(config.read_text())
        assert "mcp" in data
        assert "my-server" in data["mcp"]
        assert data["mcp"]["my-server"]["type"] == "local"

    def test_writes_only_rules_when_no_mcp(self, tmp_path):
        snippet = {
            "config_snippet": {
                "agent_profile": {
                    "path": ".opencode/agents/test-agent.md",
                    "content": "# Simple Agent\n",
                },
            }
        }
        with _patch_config(), _patch_get_agent(), _patch_post(snippet):
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
        assert (tmp_path / ".opencode" / "agents" / "test-agent.md").exists()


class TestPullDryRunAllIdes:
    @pytest.mark.parametrize("harness", ["codex", "copilot", "opencode"])
    def test_dry_run_does_not_write_files(self, tmp_path, harness):
        path_map = {
            "codex": ".codex/agents/test-agent.toml",
            "copilot": ".github/copilot-instructions.md",
            "opencode": ".opencode/agents/test-agent.md",
        }
        rules_path = path_map[harness]
        file_key = "agent_profile"
        snippet = {
            "config_snippet": {
                file_key: {
                    "path": rules_path,
                    "content": "# Test\n",
                },
            }
        }
        with _patch_config(), _patch_get_agent(), _patch_post(snippet):
            result = runner.invoke(
                cli_app,
                ["agent", "pull", "abc123", "--harness", harness, "--dir", str(tmp_path), "--dry-run", "--no-prompt"],
            )

        assert result.exit_code == 0, result.output
        assert "Dry run" in result.output
        assert not (tmp_path / rules_path).exists()


# ═══════════════════════════════════════════════════════════════════
# 9. CROSS-CUTTING — format correctness per harness spec
# ═══════════════════════════════════════════════════════════════════


class TestCodexTomlFormat:
    def test_toml_output_has_correct_structure(self):
        mcp_configs = {
            "my-server": {
                "command": "npx",
                "args": ["-y", "my-server"],
                "env": {"OBSERVAL_AGENT_ID": "agent-123"},
            }
        }
        toml = _dict_to_toml({"mcp_servers": mcp_configs})
        assert "[mcp_servers.my-server]" in toml
        assert 'command = "npx"' in toml
        assert "OBSERVAL_AGENT_ID" in toml

    def test_toml_entry_with_no_env(self):
        mcp_configs = {"bare-srv": {"command": "echo", "args": []}}
        toml = _dict_to_toml({"mcp_servers": mcp_configs})
        assert "[mcp_servers.bare-srv]" in toml
        assert 'command = "echo"' in toml


class TestCopilotJsonFormat:
    def test_copilot_config_uses_type_stdio(self):
        agent = _make_agent(external_mcps=[{"name": "my-srv", "command": "npx", "args": ["-y", "my-srv"]}])
        cfg = generate_agent_config(agent, "copilot")
        servers = cfg["mcp_config"]["content"]["servers"]
        entry = servers["my-srv"]
        assert entry["type"] == "stdio"
        assert entry["command"] == "npx"
        assert isinstance(entry["args"], list)
        assert entry["args"] == ["-y", "my-srv"]

    def test_copilot_config_has_no_mcpservers_key(self):
        agent = _make_agent(external_mcps=[{"name": "my-srv", "command": "npx", "args": []}])
        cfg = generate_agent_config(agent, "copilot")
        assert "mcpServers" not in cfg["mcp_config"]["content"]
        assert "servers" in cfg["mcp_config"]["content"]


class TestOpenCodeJsonFormat:
    def test_opencode_command_is_flat_array(self):
        agent = _make_agent(external_mcps=[{"name": "my-srv", "command": "npx", "args": ["-y", "my-srv"]}])
        cfg = generate_agent_config(agent, "opencode")
        entry = cfg["mcp_config"]["content"]["mcp"]["my-srv"]
        assert isinstance(entry["command"], list)
        assert entry["command"][0] == "npx"
        assert entry["command"] == ["npx", "-y", "my-srv"]

    def test_opencode_has_no_args_key(self):
        agent = _make_agent(external_mcps=[{"name": "my-srv", "command": "npx", "args": ["-y", "my-srv"]}])
        cfg = generate_agent_config(agent, "opencode")
        entry = cfg["mcp_config"]["content"]["mcp"]["my-srv"]
        assert "args" not in entry

    def test_opencode_type_is_local(self):
        agent = _make_agent(external_mcps=[{"name": "my-srv", "command": "npx", "args": []}])
        cfg = generate_agent_config(agent, "opencode")
        entry = cfg["mcp_config"]["content"]["mcp"]["my-srv"]
        assert entry["type"] == "local"


# ═══════════════════════════════════════════════════════════════════
# 10. DOCTOR — Copilot harness config checks
# ═══════════════════════════════════════════════════════════════════


class TestConfigGeneratorCopilotCli:
    def _make_listing(self, name="my-mcp", listing_id="abc-123", **kw):
        from unittest.mock import MagicMock

        listing = MagicMock()
        listing.name = name
        listing.id = listing_id
        listing.docker_image = kw.get("docker_image")
        listing.framework = kw.get("framework")
        listing.environment_variables = kw.get("environment_variables", [])
        listing.command = kw.get("command", "npx")
        listing.args = kw.get("args", ["-y", name])
        listing.url = kw.get("url")
        listing.transport = kw.get("transport")
        listing.auto_approve = kw.get("auto_approve")
        return listing

    def test_copilot_cli_stdio_has_type_stdio(self):
        from services.config_generator import generate_config

        cfg = generate_config(self._make_listing(), "copilot-cli")
        server = cfg["mcpServers"]["my-mcp"]
        assert server["type"] == "stdio"
        assert server["command"] == "npx"
        assert server["args"] == ["-y", "my-mcp"]
        assert server["tools"] == ["*"]

    def test_copilot_cli_sse_has_type_sse(self):
        from services.config_generator import generate_config

        cfg = generate_config(self._make_listing(url="http://localhost:3000/sse", transport="sse"), "copilot-cli")
        server = cfg["mcpServers"]["my-mcp"]
        assert server["type"] == "sse"
        assert server["url"] == "http://localhost:3000/sse"
        assert server["tools"] == ["*"]

    def test_copilot_cli_env_vars_preserved(self):
        from services.config_generator import generate_config

        cfg = generate_config(
            self._make_listing(environment_variables=[{"name": "API_KEY", "required": True}]),
            "copilot-cli",
            env_values={"API_KEY": "secret"},
        )
        server = cfg["mcpServers"]["my-mcp"]
        assert server["env"]["API_KEY"] == "secret"


# ═══════════════════════════════════════════════════════════════════
# 11. PROFILE — Copilot file map entries
# ═══════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════
# 12. CONFIG GENERATOR — Copilot explicit branch
# ═══════════════════════════════════════════════════════════════════


class TestConfigGeneratorCopilot:
    def _make_listing(self, name="my-mcp", listing_id="abc-123", **kw):
        from unittest.mock import MagicMock

        listing = MagicMock()
        listing.name = name
        listing.id = listing_id
        listing.docker_image = kw.get("docker_image")
        listing.framework = kw.get("framework")
        listing.environment_variables = kw.get("environment_variables", [])
        listing.command = kw.get("command", "npx")
        listing.args = kw.get("args", ["-y", name])
        listing.url = kw.get("url")
        listing.transport = kw.get("transport")
        listing.auto_approve = kw.get("auto_approve")
        return listing

    def test_copilot_stdio_has_type_stdio(self):
        from services.config_generator import generate_config

        cfg = generate_config(self._make_listing(), "copilot")
        server = cfg["mcpServers"]["my-mcp"]
        assert server["type"] == "stdio"
        assert server["command"] == "npx"
        assert server["args"] == ["-y", "my-mcp"]

    def test_copilot_sse_has_type_sse(self):
        from services.config_generator import generate_config

        cfg = generate_config(self._make_listing(url="http://localhost:3000/sse", transport="sse"), "copilot")
        server = cfg["mcpServers"]["my-mcp"]
        assert server["type"] == "sse"
        assert server["url"] == "http://localhost:3000/sse"

    def test_copilot_env_vars_preserved(self):
        from services.config_generator import generate_config

        cfg = generate_config(
            self._make_listing(environment_variables=[{"name": "API_KEY", "required": True}]),
            "copilot",
            env_values={"API_KEY": "secret"},
        )
        server = cfg["mcpServers"]["my-mcp"]
        assert server["env"]["API_KEY"] == "secret"


# ═══════════════════════════════════════════════════════════════════
# 13. DOCTOR — OpenCode harness config checks
# ═══════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════
# 15. CONFIG GENERATOR — OpenCode explicit branch
# ═══════════════════════════════════════════════════════════════════


class TestConfigGeneratorOpenCode:
    def _make_listing(self, name="my-mcp", listing_id="abc-123", **kw):
        from unittest.mock import MagicMock

        listing = MagicMock()
        listing.name = name
        listing.id = listing_id
        listing.docker_image = kw.get("docker_image")
        listing.framework = kw.get("framework")
        listing.environment_variables = kw.get("environment_variables", [])
        listing.command = kw.get("command", "npx")
        listing.args = kw.get("args", ["-y", name])
        listing.url = kw.get("url")
        listing.transport = kw.get("transport")
        listing.auto_approve = kw.get("auto_approve")
        return listing

    def test_opencode_stdio_uses_mcp_key(self):
        from services.config_generator import generate_config

        cfg = generate_config(self._make_listing(), "opencode")
        assert "mcp" in cfg
        assert "mcpServers" not in cfg

    def test_opencode_stdio_type_is_local(self):
        from services.config_generator import generate_config

        cfg = generate_config(self._make_listing(), "opencode")
        entry = cfg["mcp"]["my-mcp"]
        assert entry["type"] == "local"

    def test_opencode_stdio_command_is_flat_array(self):
        from services.config_generator import generate_config

        cfg = generate_config(self._make_listing(), "opencode")
        entry = cfg["mcp"]["my-mcp"]
        assert isinstance(entry["command"], list)
        assert entry["command"][0] == "npx"
        assert entry["command"] == ["npx", "-y", "my-mcp"]

    def test_opencode_stdio_has_no_args_key(self):
        from services.config_generator import generate_config

        cfg = generate_config(self._make_listing(), "opencode")
        entry = cfg["mcp"]["my-mcp"]
        assert "args" not in entry

    def test_opencode_sse_uses_mcp_key(self):
        from services.config_generator import generate_config

        cfg = generate_config(self._make_listing(url="http://localhost:3000/sse", transport="sse"), "opencode")
        assert "mcp" in cfg
        assert "mcpServers" not in cfg

    def test_opencode_sse_type_is_remote(self):
        from services.config_generator import generate_config

        cfg = generate_config(self._make_listing(url="http://localhost:3000/sse", transport="sse"), "opencode")
        entry = cfg["mcp"]["my-mcp"]
        assert entry["type"] == "remote"
        assert entry["url"] == "http://localhost:3000/sse"

    def test_opencode_sse_has_headers(self):
        from services.config_generator import generate_config

        cfg = generate_config(
            self._make_listing(url="http://localhost:3000/sse", transport="sse"),
            "opencode",
            header_values={"Authorization": "Bearer test"},
        )
        entry = cfg["mcp"]["my-mcp"]
        assert "headers" in entry

    def test_opencode_env_vars_preserved(self):
        from services.config_generator import generate_config

        cfg = generate_config(
            self._make_listing(environment_variables=[{"name": "API_KEY", "required": True}]),
            "opencode",
            env_values={"API_KEY": "secret"},
        )
        entry = cfg["mcp"]["my-mcp"]
        assert entry["environment"]["API_KEY"] == "secret"


# ═══════════════════════════════════════════════════════════════════
# 16. PULL — OpenCode scope awareness
# ═══════════════════════════════════════════════════════════════════


class TestPullOpenCodeScope:
    def test_opencode_in_scope_aware_harnesses(self):
        from observal_cli.cmd_pull import _SCOPE_AWARE_HARNESSES

        assert "opencode" in _SCOPE_AWARE_HARNESSES

    def test_opencode_scope_labels(self):
        from observal_cli.cmd_pull import _SCOPE_AWARE_HARNESSES

        project_label, user_label = _SCOPE_AWARE_HARNESSES["opencode"]
        assert "project" in project_label
        assert "user" in user_label
        assert "opencode" in user_label


# ═══════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════
# BUG FIX REGRESSION TESTS
# ═══════════════════════════════════════════════════════════════════


class TestConfigGeneratorCodexFormat:
    """Bug #4: config_generator should return mcp_servers for Codex."""

    def _make_listing(self, name="my-mcp", framework="typescript", url=None, command=None, args=None):
        import uuid
        from unittest.mock import MagicMock

        listing = MagicMock()
        listing.id = uuid.uuid4()
        listing.name = name
        listing.framework = framework
        listing.docker_image = None
        listing.url = url
        listing.transport = None
        listing.auto_approve = None
        listing.environment_variables = []
        listing.command = command
        listing.args = args
        listing.headers = []
        return listing

    def test_stdio_codex_uses_mcp_servers_key(self):
        from services.config_generator import generate_config

        listing = self._make_listing(command="npx", args=["-y", "my-mcp"])
        cfg = generate_config(listing, "codex")
        # Must use "mcp_servers" not "mcpServers"
        assert "mcp_servers" in cfg
        assert "mcpServers" not in cfg

    def test_stdio_codex_keeps_original_command(self):
        from services.config_generator import generate_config

        listing = self._make_listing(command="npx", args=["-y", "my-mcp"])
        cfg = generate_config(listing, "codex")
        servers = cfg["mcp_servers"]
        assert len(servers) == 1
        entry = next(iter(servers.values()))
        assert entry["command"] == "npx"

    def test_sse_codex_uses_mcp_servers_key(self):
        from services.config_generator import generate_config

        listing = self._make_listing(url="https://example.com/mcp")
        listing.transport = "sse"
        cfg = generate_config(listing, "codex")
        assert "mcp_servers" in cfg
        assert "mcpServers" not in cfg

    def test_sse_codex_entry_has_url(self):
        from services.config_generator import generate_config

        listing = self._make_listing(url="https://example.com/mcp")
        listing.transport = "sse"
        cfg = generate_config(listing, "codex")
        entry = next(iter(cfg["mcp_servers"].values()))
        assert entry["url"] == "https://example.com/mcp"

    def test_mcp_servers_toml_renders_correctly(self):
        """The mcp_servers dict should produce valid Codex TOML."""
        from observal_cli.cmd_pull import _dict_to_toml

        servers = {"mcp_servers": {"my-server": {"command": "npx", "args": ["-y", "my-server"]}}}
        toml = _dict_to_toml(servers)
        assert "[mcp_servers.my-server]" in toml
        assert 'command = "npx"' in toml


class TestCodexInstallCliPathHint:
    """Bug #6: install command should show the correct path hint for Codex."""

    def test_harness_config_paths_includes_codex(self):
        """The MCP install command's path hint dict must include codex."""
        import inspect

        # Read the cmd_mcp.py file and verify codex is in harness_config_paths
        import observal_cli.cmd_mcp as cmd_mcp_module

        # Find the harness_config_paths dict in the install function's source
        source = inspect.getsource(cmd_mcp_module)
        assert '"codex": "~/.codex/config.toml"' in source or "'codex': '~/.codex/config.toml'" in source


class TestMcpAuthHeadersInAgentPull:
    """Verify that MCP header_values propagate through generate_agent_config."""

    def _make_sse_listing(self, listing_id):
        listing = MagicMock()
        listing.id = listing_id
        listing.name = "auth-mcp"
        listing.url = "https://api.example.com/mcp"
        listing.transport = "sse"
        listing.command = None
        listing.args = None
        listing.framework = None
        listing.docker_image = None
        listing.auto_approve = None
        listing.environment_variables = []
        return listing

    def test_header_values_included_in_cursor_config(self):
        """header_values should appear in mcpServers config for Cursor."""
        listing_id = uuid.uuid4()
        agent = _make_agent(
            components=[_make_component("mcp", listing_id)],
        )
        listing = self._make_sse_listing(listing_id)
        header_vals = {str(listing_id): {"Authorization": "Bearer sk-test-123"}}

        config = generate_agent_config(
            agent,
            "cursor",
            mcp_listings={listing_id: listing},
            header_values=header_vals,
        )

        mcp_content = config["mcp_config"]["content"]
        servers = mcp_content.get("mcpServers", {})
        assert "auth-mcp" in servers
        entry = servers["auth-mcp"]
        assert entry.get("headers") == {"Authorization": "Bearer sk-test-123"}

    def test_header_values_included_in_claude_code_config(self):
        """header_values should appear in mcpServers config for Claude Code."""
        listing_id = uuid.uuid4()
        agent = _make_agent(
            components=[_make_component("mcp", listing_id)],
        )
        listing = self._make_sse_listing(listing_id)
        header_vals = {str(listing_id): {"Authorization": "Bearer sk-test-456"}}

        config = generate_agent_config(
            agent,
            "claude-code",
            mcp_listings={listing_id: listing},
            header_values=header_vals,
        )

        # Claude Code SSE MCPs go in mcp_config directly
        mcp_servers = config["mcp_config"]
        assert "auth-mcp" in mcp_servers
        entry = mcp_servers["auth-mcp"]
        assert entry.get("headers") == {"Authorization": "Bearer sk-test-456"}
        assert entry.get("type") == "sse"
        assert entry.get("url") == "https://api.example.com/mcp"

    def test_no_headers_when_header_values_empty(self):
        """No headers key emitted when header_values is empty."""
        listing_id = uuid.uuid4()
        agent = _make_agent(
            components=[_make_component("mcp", listing_id)],
        )
        listing = self._make_sse_listing(listing_id)

        config = generate_agent_config(
            agent,
            "cursor",
            mcp_listings={listing_id: listing},
            header_values={},
        )

        mcp_content = config["mcp_config"]["content"]
        servers = mcp_content.get("mcpServers", {})
        assert "auth-mcp" in servers
        entry = servers["auth-mcp"]
        assert "headers" not in entry


@pytest.mark.parametrize(
    "harness,expected_snippet",
    [
        (
            "claude-code",
            {
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "*", "hooks": [{"type": "command", "command": "python guard.py", "timeout": 10}]}
                    ]
                }
            },
        ),
        ("cursor", {"version": 1, "hooks": {"preToolUse": [{"command": "python guard.py"}]}}),
        (
            # Kiro uses the standalone v1 hooks-file schema (IDE 1.0 / CLI 3.0);
            # the legacy inline shape is hidden by the IDE.
            "kiro",
            {
                "version": "v1",
                "hooks": [
                    {
                        "name": "observal-pretooluse",
                        "trigger": "PreToolUse",
                        "action": {"type": "command", "command": "python guard.py"},
                        "timeout": 10,
                    }
                ],
            },
        ),
        ("copilot", {"hooks": {"PreToolUse": [{"command": "python guard.py"}]}}),
        (
            "codex",
            {
                "hooks": {"PreToolUse": {"command": "python guard.py"}},
                "_format": "toml",
                "_note": "Add to .codex/config.toml under [hooks.PreToolUse]",
            },
        ),
    ],
)
def test_hook_install_format_is_owned_by_adapter(harness, expected_snippet):
    from services.hook_install_generator import generate_hook_install_config

    listing = MagicMock(
        event="PreToolUse",
        handler_type="command",
        handler_config={"command": "python guard.py", "timeout": 10},
        script_content=None,
        script_filename=None,
        source_url=None,
        source_path=None,
        source_ref=None,
        resolved_sha=None,
        requirements=[],
    )
    listing.name = "guard"

    result = generate_hook_install_config(listing, harness)
    assert result["config_snippet"] == expected_snippet
