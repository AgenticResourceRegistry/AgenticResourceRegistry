# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Native, bounded evidence from every harness; no Registry identity inference."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

from observal_cli.discovery.bounded_walk import AggregateDiscoveryBudget, discovery_budget
from observal_cli.discovery.models import DiscoveryScope
from observal_cli.discovery.serialize import inventory_to_dict
from observal_cli.harness import ensure_loaded, get_adapter

if TYPE_CHECKING:
    from pathlib import Path


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


@pytest.mark.parametrize(
    ("harness", "config", "format"),
    [
        ("claude-code", ".mcp.json", "mcp"),
        ("kiro", ".kiro/settings/mcp.json", "mcp"),
        ("pi", ".pi/mcp.json", "mcp"),
        ("cursor", ".cursor/mcp.json", "mcp"),
        ("antigravity", ".agents/mcp_config.json", "mcp"),
        ("goose", ".agents/skills/example/SKILL.md", "skill"),
        ("opencode", "opencode.json", "opencode"),
        ("codex", ".codex/config.toml", "toml"),
        ("copilot", ".vscode/mcp.json", "copilot"),
        ("copilot-cli", ".mcp.json", "mcp"),
    ],
)
def test_project_adapter_inventory_is_local_and_redacted(
    tmp_path: Path, harness: str, config: str, format: str
) -> None:
    ensure_loaded()
    project = tmp_path / "project"
    secret = "PRIVATE_CREDENTIAL"
    server = {"command": "npx", "args": ["-y", "example-mcp"], "env": {"API_KEY": secret}}
    content = {
        "mcp": json.dumps({"mcpServers": {"example": server}}),
        "copilot": json.dumps({"servers": {"example": server}}),
        "opencode": json.dumps(
            {"mcp": {"example": {"command": ["npx", "-y", "example-mcp"], "env": {"API_KEY": secret}}}}
        ),
        "toml": '[mcp_servers.example]\ncommand = "npx"\nargs = ["-y", "example-mcp"]\n',
        "skill": "---\ndescription: PRIVATE_CREDENTIAL\n---\nBody PRIVATE_CREDENTIAL\n",
    }[format]
    _write(project / config, content)

    result = get_adapter(harness).discover_project(project)
    output = inventory_to_dict(result.evidence, result.diagnostics, home=tmp_path / "home", project_dir=project)

    assert [item["name"] for item in output["inventory"]] == ["example"]
    assert output["inventory"][0]["type"] == ("skill" if harness == "goose" else "mcp")
    assert output["inventory"][0]["harness"] == harness
    assert output["inventory"][0]["scope"] == DiscoveryScope.PROJECT.value
    assert output["inventory"][0]["source"] == f"<project>/{config}"
    assert secret not in json.dumps(output)
    assert str(tmp_path) not in json.dumps(output)
    assert not output["diagnostics"]
    if harness != "goose":
        assert output["inventory"][0]["launch"]["package"] == "example-mcp"
        if harness != "codex":
            assert output["inventory"][0]["launch"]["environment_names"] == ["API_KEY"]


@pytest.mark.parametrize(
    ("harness", "config", "malformed"),
    [
        ("cursor", ".cursor/mcp.json", "{not JSON"),
        ("codex", ".codex/config.toml", '[mcp.servers.unclosed\ncommand = "npx"'),
        ("goose", ".agents/plugins/bad/hooks/hooks.json", "{not JSON"),
    ],
)
def test_malformed_native_config_reports_diagnostic_without_leaking_content(
    tmp_path: Path, harness: str, config: str, malformed: str
) -> None:
    ensure_loaded()
    project = tmp_path / "project"
    _write(project / config, malformed)
    result = get_adapter(harness).discover_project(project)
    output = inventory_to_dict(result.evidence, result.diagnostics, home=tmp_path / "home", project_dir=project)
    assert not output["inventory"]
    assert any(item["code"] == "metadata_malformed" for item in output["diagnostics"])
    assert malformed not in json.dumps(output)
    assert str(tmp_path) not in json.dumps(output)


@pytest.mark.parametrize("scope", ["home", "project"])
def test_codex_mcp_servers_and_legacy_nested_config(tmp_path: Path, scope: str) -> None:
    ensure_loaded()
    base = tmp_path / scope
    path = base / ".codex" / "config.toml"
    _write(path, '[mcp_servers.current]\ncommand = "npx"\nargs = ["current"]\n')
    adapter = get_adapter("codex")
    result = adapter.discover_home(base) if scope == "home" else adapter.discover_project(base)
    assert [item.component.name for item in result.evidence] == ["current"]

    _write(path, '[mcp.servers.old]\ncommand = "npx"\nargs = ["old"]\n')
    result = adapter.discover_home(base) if scope == "home" else adapter.discover_project(base)
    assert [item.component.name for item in result.evidence] == ["old"]


def test_antigravity_same_root_finishes_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from observal_cli.harness.antigravity import AntigravityAdapter

    home = tmp_path / "home"
    root = home / ".gemini"
    _write(root / "mcp_config.json", "{bad JSON")
    _write(root / "agents" / "helper" / "agent.json", json.dumps({"name": "helper"}))
    monkeypatch.setattr("observal_cli.harness.antigravity.resolve_antigravity_config_dir", lambda _home: root)
    monkeypatch.setattr(AntigravityAdapter, "_resolve_ag_dir", lambda _self, _home=None: root)

    result = AntigravityAdapter().discover_home(home)
    assert [item.component.name for item in result.evidence] == ["helper"]
    assert [item.code.value for item in result.diagnostics] == ["metadata_malformed"]


def test_agent_prompt_body_is_never_in_inventory_output(tmp_path: Path) -> None:
    ensure_loaded()
    project = tmp_path / "project"
    _write(project / ".cursor" / "agents" / "reviewer.md", "Prompt: PRIVATE_CREDENTIAL\nDo not print this")
    result = get_adapter("cursor").discover_project(project)
    output = inventory_to_dict(result.evidence, result.diagnostics, home=tmp_path / "home", project_dir=project)
    assert [(item["type"], item["name"]) for item in output["inventory"]] == [("agent", "reviewer")]
    assert "PRIVATE_CREDENTIAL" not in json.dumps(output)
    assert "Do not print this" not in json.dumps(output)


def test_home_and_project_evidence_remain_separate(tmp_path: Path) -> None:
    ensure_loaded()
    home = tmp_path / "home"
    project = tmp_path / "project"
    server = json.dumps({"mcpServers": {"example": {"command": "npx", "args": ["example"]}}})
    _write(home / ".cursor" / "mcp.json", server)
    _write(project / ".cursor" / "mcp.json", server)

    adapter = get_adapter("cursor")
    evidence = adapter.discover_home(home).evidence + adapter.discover_project(project).evidence
    output = inventory_to_dict(evidence, [], home=home, project_dir=project)
    assert [item["scope"] for item in output["inventory"]] == ["project", "user"]
    assert [item["source"] for item in output["inventory"]] == ["<project>/.cursor/mcp.json", "~/.cursor/mcp.json"]


def test_claude_does_not_scan_plugin_after_aggregate_entries_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from observal_cli.harness.claude_code import ClaudeCodeAdapter

    home = tmp_path / "home"
    claude = home / ".claude"
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    _write(claude / "settings.json", json.dumps({"enabledPlugins": {"suite@market": True}}))
    _write(
        claude / "plugins" / "installed_plugins.json",
        json.dumps(
            {
                "plugins": {"suite@market": [{"installPath": str(plugin)}]},
            }
        ),
    )
    (claude / "skills" / "empty").mkdir(parents=True)
    discover_plugin = Mock(side_effect=AssertionError("aggregate entry limit should stop plugin scanning"))
    monkeypatch.setattr(ClaudeCodeAdapter, "_discover_claude_plugin", discover_plugin)
    budget = AggregateDiscoveryBudget(max_entries=1)

    with discovery_budget(budget):
        ClaudeCodeAdapter().discover_home(home)

    assert budget.entries == 1
    discover_plugin.assert_not_called()


def test_claude_plugin_uses_only_approved_roots(tmp_path: Path) -> None:
    ensure_loaded()
    home = tmp_path / "home"
    claude = home / ".claude"
    plugin = tmp_path / "approved-plugin"
    _write(claude / "settings.json", json.dumps({"enabledPlugins": {"suite@market": True}}))
    _write(
        claude / "plugins" / "installed_plugins.json",
        json.dumps({"plugins": {"suite@market": [{"installPath": str(plugin)}]}}),
    )
    _write(
        plugin / ".mcp.json",
        json.dumps(
            {
                "mcpServers": {
                    "plugin-server": {"url": "https://user:password@example.test/mcp?token=PRIVATE_CREDENTIAL"}
                }
            }
        ),
    )
    _write(plugin / "skills" / "helper" / "SKILL.md", "Plugin skill")

    result = get_adapter("claude-code").discover_home(home)
    output = inventory_to_dict(result.evidence, result.diagnostics, home=home, project_dir=tmp_path / "project")
    names = [item["name"] for item in output["inventory"]]
    assert "plugin-server" in names
    assert "suite/helper" in names
    assert output["inventory"][names.index("plugin-server")]["launch"]["url"] == "https://example.test/mcp"
    assert "PRIVATE_CREDENTIAL" not in json.dumps(output)
    assert str(tmp_path) not in json.dumps(output)


@pytest.mark.parametrize("value", ["[]", '"x"', "null"])
def test_wrongly_typed_mcp_servers_reports_diagnostic(tmp_path: Path, value: str) -> None:
    ensure_loaded()
    project = tmp_path / "project"
    _write(project / ".cursor/mcp.json", f'{{"mcpServers": {value}}}')
    result = get_adapter("cursor").discover_project(project)
    output = inventory_to_dict(result.evidence, result.diagnostics, home=tmp_path / "home", project_dir=project)
    assert not output["inventory"]
    assert any(item["code"] == "metadata_malformed" for item in output["diagnostics"])
