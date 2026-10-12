# SPDX-FileCopyrightText: 2026 RAWx18 <rawx18.dev@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Goose harness adapters (CLI scanning + server config generation)."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
import yaml

from observal_cli.harness import ensure_loaded, get_adapter
from observal_cli.harness.goose import GooseAdapter
from observal_shared.harness_registry import HARNESS_REGISTRY

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _load_adapters(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GOOSE_PATH_ROOT", raising=False)
    ensure_loaded()


def _write_config(home: Path, extensions: dict, extra: dict | None = None) -> Path:
    config_dir = home / ".config" / "goose"
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "config.yaml"
    path.write_text(yaml.safe_dump({**(extra or {}), "extensions": extensions}))
    return path


# ── Registry ──────────────────────────────────────────────────────────────────


def test_goose_registry_entry_is_complete():
    spec = HARNESS_REGISTRY["goose"]
    required = {
        "display_name",
        "capabilities",
        "scopes",
        "default_scope",
        "agent_profile",
        "mcp_config",
        "mcp_servers_key",
        "skills",
        "hooks",
        "hook_events_map",
        "session_parser",
    }
    assert required <= set(spec)
    assert spec["mcp_servers_key"] == "extensions"
    assert spec["session_parser"] == "goose"
    assert spec["hook_type"] == "plugin"
    assert spec["mcp_config"]["user"] == "~/.config/goose/config.yaml"
    assert spec["skills"]["user"] == "~/.agents/skills/{name}/SKILL.md"
    assert spec["agent_profile"]["user"] == "~/.agents/agents/{name}.md"


def test_goose_adapters_are_registered():
    from services.harness import ensure_loaded as server_loaded
    from services.harness import get_adapter as server_adapter

    server_loaded()
    assert get_adapter("goose").harness_name == "goose"
    assert server_adapter("goose").harness_name == "goose"


# ── Detection ─────────────────────────────────────────────────────────────────


def test_is_installed_requires_a_goose_directory(tmp_path: Path):
    adapter = GooseAdapter()
    assert adapter.is_installed(tmp_path) is False
    (tmp_path / ".config" / "goose").mkdir(parents=True)
    assert adapter.is_installed(tmp_path) is True


def test_is_installed_accepts_the_data_directory(tmp_path: Path):
    (tmp_path / ".local" / "share" / "goose").mkdir(parents=True)
    assert GooseAdapter().is_installed(tmp_path) is True


# ── Scanning ──────────────────────────────────────────────────────────────────


def test_scan_home_empty(tmp_path: Path):
    result = GooseAdapter().scan_home(tmp_path)
    assert result.mcps == []
    assert result.skills == []
    assert result.hooks == []
    assert result.agents == []


def test_scan_home_discovers_stdio_and_streamable_http_extensions(tmp_path: Path):
    _write_config(
        tmp_path,
        {
            "developer": {"type": "builtin", "name": "developer", "enabled": True},
            "computercontroller": {"type": "platform", "name": "computercontroller", "enabled": True},
            "snippet": {"type": "inline_python", "name": "snippet", "code": "print(1)"},
            "filesystem": {
                "type": "stdio",
                "name": "filesystem",
                "cmd": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            },
            "legacy-sse": {"type": "sse", "name": "legacy-sse", "uri": "https://example.com/sse"},
            "remote-tools": {"type": "streamable_http", "name": "remote", "uri": "https://example.com/mcp"},
        },
    )

    mcps = {mcp.name: mcp for mcp in GooseAdapter().scan_home(tmp_path).mcps}

    # builtin, platform and inline_python extensions have no command to record.
    assert set(mcps) == {"filesystem", "legacy-sse", "remote"}
    assert mcps["filesystem"].command == "npx"
    assert mcps["filesystem"].args == ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    assert mcps["remote"].url == "https://example.com/mcp"
    assert mcps["legacy-sse"].url == "https://example.com/sse"


def test_scan_home_survives_malformed_config(tmp_path: Path):
    config_dir = tmp_path / ".config" / "goose"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text("extensions: [unclosed\n")
    assert GooseAdapter().scan_home(tmp_path).mcps == []


def test_scan_home_discovers_skills_agents_and_plugin_hooks(tmp_path: Path):
    skill = tmp_path / ".agents" / "skills" / "helper"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: helper\ndescription: Does helpful things\n---\n")

    agents = tmp_path / ".agents" / "agents"
    agents.mkdir(parents=True)
    (agents / "reviewer.md").write_text(
        "---\nname: code-reviewer\ndescription: Reviews code\nmodel: gpt-5.5\n---\nBe direct."
    )

    hooks = tmp_path / ".agents" / "plugins" / "observal" / "hooks"
    hooks.mkdir(parents=True)
    hooks.joinpath("hooks.json").write_text(
        json.dumps({"hooks": {"SessionEnd": [{"hooks": [{"type": "command", "command": "observal_cli"}]}]}})
    )

    result = GooseAdapter().scan_home(tmp_path)

    assert [skill.name for skill in result.skills] == ["helper"]
    assert result.skills[0].description == "Does helpful things"
    assert [agent.name for agent in result.agents] == ["code-reviewer"]
    assert result.agents[0].model_name == "gpt-5.5"
    assert [(hook.name, hook.event) for hook in result.hooks] == [("observal", "SessionEnd")]


def test_scan_project_discovers_project_agents_dir(tmp_path: Path):
    skill = tmp_path / ".agents" / "skills" / "project-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\ndescription: Project scoped\n---\n")

    result = GooseAdapter().scan_project(tmp_path)

    assert [skill.name for skill in result.skills] == ["project-skill"]
    assert result.mcps == []  # goose only reads extensions from the user config


def test_extract_mcp_servers_ignores_unrelated_config_sections():
    adapter = GooseAdapter()
    config = {"active_provider": "anthropic", "providers": {"anthropic": {"enabled": True}}}
    assert adapter.extract_mcp_servers(config) == {}
    assert adapter.extract_mcp_servers({"extensions": {"a": {}}}) == {"a": {}}


# ── Hooks ─────────────────────────────────────────────────────────────────────


def test_detect_hooks_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Without this the user-scope fallback would read the developer's real plugin.
    monkeypatch.setenv("GOOSE_PATH_ROOT", str(tmp_path))
    assert GooseAdapter().detect_hooks(tmp_path) == "missing"


def test_detect_hooks_installed_and_partial(tmp_path: Path):
    from observal_cli.harness_specs.goose_hooks_spec import GOOSE_HOOK_EVENTS, build_hooks

    hooks_dir = tmp_path / "plugins" / "observal" / "hooks"
    hooks_dir.mkdir(parents=True)
    hooks_json = hooks_dir / "hooks.json"

    hooks_json.write_text(json.dumps(build_hooks()))
    assert GooseAdapter().detect_hooks(tmp_path) == "installed"

    partial = build_hooks()
    partial["hooks"].pop(GOOSE_HOOK_EVENTS[-1])
    hooks_json.write_text(json.dumps(partial))
    assert GooseAdapter().detect_hooks(tmp_path) == "partial"

    hooks_json.write_text("{ not json")
    assert GooseAdapter().detect_hooks(tmp_path) == "missing"


def test_rewrite_hooks_repoints_pulled_commands_at_the_local_interpreter():
    from observal_cli.harness_specs.goose_hooks_spec import hook_command

    pulled = {
        "hooks": {
            "Stop": [
                {
                    "hooks": [
                        {"type": "command", "command": "python3 -m observal_cli.hooks.session_push --harness goose"}
                    ]
                },
                {"hooks": [{"type": "command", "command": "/usr/local/bin/my-hook.sh"}]},
            ]
        }
    }

    rewritten = GooseAdapter().rewrite_hooks(pulled, agent_id="a1")["hooks"]["Stop"]

    assert rewritten[0]["hooks"][0]["command"] == hook_command()
    assert rewritten[1]["hooks"][0]["command"] == "/usr/local/bin/my-hook.sh"


def test_hook_spec_uses_documented_goose_events():
    spec = GooseAdapter().get_hook_spec()
    assert spec.format == "plugin"
    assert set(spec.events) == {"SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"}


def test_generated_hook_rules_omit_matcher_and_never_write_stdout():
    from observal_cli.harness_specs.goose_hooks_spec import build_hooks

    hooks = build_hooks()["hooks"]
    for rules in hooks.values():
        assert len(rules) == 1
        assert "matcher" not in rules[0]  # a bare glob matcher would be skipped by goose
        handler = rules[0]["hooks"][0]
        assert handler["type"] == "command"
        assert "--harness goose" in handler["command"]
        assert "--json-response" not in handler["command"]


def test_hook_command_is_posix_shell_safe(monkeypatch: pytest.MonkeyPatch):
    """goose runs every hook through ``sh -c``, including on Windows."""
    import shlex

    from observal_cli.harness_specs import goose_hooks_spec

    monkeypatch.setattr(goose_hooks_spec.sys, "executable", "/opt/py 3.14/bin/python")
    command = goose_hooks_spec.hook_command()

    assert shlex.split(command)[:3] == ["/opt/py 3.14/bin/python", "-m", "observal_cli.hooks.session_push"]
    assert "&&" not in command  # cmd.exe syntax would never parse under sh -c


# ── Layer attribution ─────────────────────────────────────────────────────────


def test_managed_files_for_layer_source_attribution():
    lockfile = {
        "harnesses": {
            "goose": {
                "agents": [{"name": "agent-one", "components": [{"type": "skill", "name": "helper"}]}],
            }
        }
    }
    assert GooseAdapter().get_observal_managed_files(lockfile) == {
        "user:agents/agent-one.md",
        "project:.agents/agents/agent-one.md",
        "user:skills/helper/SKILL.md",
        "project:.agents/skills/helper/SKILL.md",
    }


def test_layer_scan_globs_registered():
    from observal_cli.layer import HARNESS_LAYER_CONFIGS

    config = HARNESS_LAYER_CONFIGS["goose"]
    user_bases = {base for base, _patterns in config["user"]}
    assert user_bases == {"~/.agents", "~/.config/goose"}
    assert any("config.yaml" in patterns for _base, patterns in config["user"])


# ── Server-side config generation ─────────────────────────────────────────────


def _agent(description: str = "A goose test agent") -> MagicMock:
    agent = MagicMock()
    agent.id = uuid.uuid4()
    agent.name = "goose-agent"
    agent.description = description
    agent.prompt = "You are helpful."
    agent.model_name = "gpt-5.5"
    agent.components = []
    agent.external_mcps = []
    return agent


def _generate(**options):
    from services.harness import generate_agent_config

    return generate_agent_config(_agent(), "goose", options=options)


def test_server_config_writes_agent_markdown_with_frontmatter():
    result = _generate(scope="user", _resolved_model="gpt-5.5")

    profile = result["agent_profile"]
    assert profile["path"] == "~/.agents/agents/goose-agent.md"
    header, body = profile["content"].split("---\n", 2)[1:]
    frontmatter = yaml.safe_load(header)
    assert frontmatter["name"] == "goose-agent"
    assert frontmatter["model"] == "gpt-5.5"
    assert "You are helpful." in body


def test_server_config_agent_markdown_escapes_yaml_hostile_descriptions():
    from services.harness import generate_agent_config

    agent = _agent(description='Reviews: "code" & stuff #1')
    content = generate_agent_config(agent, "goose", options={"scope": "user"})["agent_profile"]["content"]
    frontmatter = yaml.safe_load(content.split("---\n", 2)[1])
    assert frontmatter["description"] == 'Reviews: "code" & stuff #1'


def test_server_config_emits_goose_extension_entries():
    from services.harness import McpConfigContext, ensure_loaded, get_adapter

    ensure_loaded()
    adapter = get_adapter("goose")

    stdio = adapter.agent_mcp_entry(
        McpConfigContext(
            name="fs",
            command="npx",
            args=["-y", "server"],
            server_env={"TOKEN": "x"},
            headers={},
            transport="stdio",
            url=None,
            auto_approve=[],
        )
    )
    assert stdio == {
        "type": "stdio",
        "name": "fs",
        "enabled": True,
        "cmd": "npx",
        "args": ["-y", "server"],
        "envs": {"TOKEN": "x"},
        "env_keys": [],
        "timeout": 300,
    }

    remote = adapter.agent_mcp_entry(
        McpConfigContext(
            name="remote",
            command="",
            args=[],
            server_env={},
            headers={"Authorization": "Bearer x"},
            transport="streamable_http",
            url="https://example.com/mcp",
            auto_approve=[],
        )
    )
    assert remote["type"] == "streamable_http"
    assert remote["uri"] == "https://example.com/mcp"
    assert remote["headers"] == {"Authorization": "Bearer x"}


def test_server_config_normalises_external_mcp_entries():
    from services.harness import generate_agent_config

    agent = _agent()
    agent.external_mcps = [{"name": "extra", "command": "node", "args": ["server.js"]}]
    result = generate_agent_config(agent, "goose", options={"scope": "user"})

    extensions = result["mcp_config"]["content"]["extensions"]
    assert result["mcp_config"]["path"] == "~/.config/goose/config.yaml"
    assert extensions["extra"]["type"] == "stdio"
    assert extensions["extra"]["cmd"] == "node"
    assert extensions["extra"]["envs"]["OBSERVAL_AGENT_ID"] == str(agent.id)


def test_server_config_emits_plugin_manifest_and_telemetry_hooks():
    result = _generate(scope="user")

    hooks = result["hooks_config"]
    assert hooks["path"] == "~/.agents/plugins/observal/hooks/hooks.json"
    assert hooks["merge"] is True
    assert set(hooks["content"]["hooks"]) == {"SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"}

    manifest = next(f for f in result["hook_files"] if f["path"].endswith("plugin.json"))
    assert manifest["path"] == "~/.agents/plugins/observal/plugin.json"
    assert json.loads(manifest["content"])["name"] == "observal"


@pytest.mark.parametrize(("scope", "plugin_dir"), [("user", "~/.agents"), ("project", ".agents")])
def test_server_config_runs_hook_scripts_from_the_plugin_root(scope: str, plugin_dir: str):
    from services.harness import generate_agent_config

    hook_id = uuid.uuid4()
    agent = _agent()
    agent.components = [SimpleNamespace(component_type="hook", component_id=hook_id)]
    hook = SimpleNamespace(
        name="nudge",
        slug="nudge",
        namespace="acme",
        event="Stop",
        handler_type="command",
        handler_config={"command": "nudge.sh"},
        script_filename="nudge.sh",
        script_content="#!/bin/sh\n",
    )
    result = generate_agent_config(agent, "goose", options={"scope": scope}, hook_listings={hook_id: hook})

    # The plugin's hooks.json and the script it runs land in the same plugin directory.
    script = next(f for f in result["hook_files"] if f["path"].endswith("nudge.sh"))
    assert script["path"] == f"{plugin_dir}/plugins/observal/scripts/nudge.sh"
    assert result["hooks_config"]["path"] == f"{plugin_dir}/plugins/observal/hooks/hooks.json"
    commands = [h["command"] for rule in result["hooks_config"]["content"]["hooks"]["Stop"] for h in rule["hooks"]]
    assert '"${PLUGIN_ROOT}/scripts/nudge.sh"' in commands


def test_server_config_omits_mcp_section_when_agent_has_no_servers():
    assert "mcp_config" not in _generate(scope="user")


def test_hook_script_filename_with_path_segments_is_rejected():
    from services.harness.helpers import _collect_hook_script_files

    with pytest.raises(ValueError):
        _collect_hook_script_files([{"script_content": "x", "script_filename": "../../.ssh/k"}], None, "goose")
