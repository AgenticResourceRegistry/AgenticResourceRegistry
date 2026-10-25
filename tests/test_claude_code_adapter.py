# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Focused coverage for the Claude Code CLI harness adapter."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from observal_cli.harness import NotSupportedError, ScanResult, SessionSource
from observal_cli.harness.base import _check_feature
from observal_cli.harness.claude_code import ClaudeCodeAdapter
from observal_shared.harness_registry import HARNESS_REGISTRY

_FRONTMATTER = "-" * 3


def _write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


def _write_text(path: Path, value: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
    return path


def _markdown(fields: list[str], body: str = "") -> str:
    return "\n".join([_FRONTMATTER, *fields, _FRONTMATTER, body])


def _records(values: list[object]) -> list[dict]:
    return [vars(value) for value in values]


def _empty_result() -> dict[str, list]:
    return {"mcps": [], "skills": [], "hooks": [], "agents": []}


def _install_plugin(
    claude_dir: Path,
    plugin_key: str,
    plugin_dir: Path,
    *,
    enabled: object = True,
) -> None:
    _write_json(claude_dir / "settings.json", {"enabledPlugins": {plugin_key: enabled}})
    _write_json(
        claude_dir / "plugins" / "installed_plugins.json",
        {"plugins": {plugin_key: [{"installPath": str(plugin_dir)}]}},
    )


def test_adapter_metadata_registry_and_capability_gates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    adapter = ClaudeCodeAdapter()
    registry = HARNESS_REGISTRY["claude-code"]

    assert adapter.harness_name == "claude-code"
    assert adapter.home_markers == (".claude",)
    assert adapter.managed_agent_profiles == (
        "user:agents/{name}.md",
        "project:.claude/agents/{name}.md",
    )
    assert adapter.managed_skills == ("user:skills/{name}/SKILL.md",)
    assert adapter.managed_mcp_files == ()
    assert registry["capabilities"] == {"hooks", "mcp_servers", "skills"}
    assert registry["scopes"] == ["project", "user"]
    assert registry["default_scope"] == "project"

    gated = ("scan_home", "scan_project", "get_hook_spec", "generate_hook_config", "detect_hooks")
    for method in gated:
        _check_feature(adapter.harness_name, method)
    assert isinstance(adapter.scan_home(tmp_path), ScanResult)

    monkeypatch.setitem(registry, "capabilities", set())
    for method in gated:
        with pytest.raises(NotSupportedError, match=rf"claude-code does not support {method}"):
            _check_feature(adapter.harness_name, method)


def test_managed_files_include_only_supported_claude_paths():
    lockfile = {
        "harnesses": {
            "claude-code": {
                "agents": [
                    {
                        "name": "reviewer",
                        "components": [
                            {"type": "skill", "name": "audit"},
                            {"type": "mcp", "name": "filesystem"},
                            {"type": "hook", "name": "guard"},
                        ],
                    }
                ],
                "standalone": [
                    {"type": "skill", "name": "standalone"},
                    {"type": "mcp", "name": "remote"},
                ],
            }
        }
    }

    assert ClaudeCodeAdapter().get_observal_managed_files(lockfile) == {
        "user:agents/reviewer.md",
        "project:.claude/agents/reviewer.md",
        "user:skills/audit/SKILL.md",
        "user:skills/standalone/SKILL.md",
    }


def test_scan_home_resolves_default_home_and_normalizes_all_components(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()

    settings_target = tmp_path / "targets" / "settings.json"
    _write_json(
        settings_target,
        {"enabledPlugins": {"suite@market": True, "disabled@market": False}},
    )
    (claude_dir / "settings.json").symlink_to(settings_target)

    plugin_target = tmp_path / "targets" / "suite-plugin"
    plugin_target.mkdir(parents=True)
    plugin_link = tmp_path / "targets" / "suite-link"
    plugin_link.symlink_to(plugin_target, target_is_directory=True)
    disabled_plugin = tmp_path / "targets" / "disabled-plugin"
    disabled_plugin.mkdir()
    _write_json(
        claude_dir / "plugins" / "installed_plugins.json",
        {
            "plugins": {
                "suite@market": [{"installPath": str(plugin_link)}],
                "disabled@market": [{"installPath": str(disabled_plugin)}],
            }
        },
    )

    _write_json(
        plugin_target / ".claude-plugin" / "plugin.json",
        {"description": "Suite plugin description"},
    )
    _write_json(
        plugin_target / ".mcp.json",
        {
            "mcpServers": {
                "local-server": {"command": "node", "args": ["server.js"]},
                "remote-server": {"url": "https://mcp.example.test/api"},
            }
        },
    )
    _write_text(
        plugin_target / "skills" / "plugin-helper" / "SKILL.md",
        _markdown(["name: plugin-helper"], "# Heading\nPlugin helper body"),
    )
    _write_json(
        plugin_target / "hooks" / "hooks.json",
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Write",
                        "hooks": [{"type": "http", "url": "https://hooks.example.test/pre"}],
                    }
                ],
                "Stop": [{"type": "command", "command": "python stop.py"}],
            }
        },
    )
    _write_json(disabled_plugin / ".mcp.json", {"mcpServers": {"ignored": {"command": "ignored"}}})

    _write_text(
        claude_dir / "skills" / "alpha" / "SKILL.md",
        _markdown(["description: Local helper", "task_type: review"], "Local body"),
    )
    _write_text(
        claude_dir / "skills" / "fallback" / "SKILL.md",
        _markdown(["name: fallback"], "# Heading\nFallback body"),
    )
    agent_path = _write_text(
        claude_dir / "agents" / "reviewer.md",
        _markdown(["model: claude-opus"], "# Reviewer\nReview carefully.\n"),
    )

    result = ClaudeCodeAdapter().scan_home()

    assert _records(result.mcps) == [
        {
            "name": "local-server",
            "command": "node",
            "args": ["server.js"],
            "url": None,
            "description": "Suite plugin description",
            "source": "plugin:suite",
        },
        {
            "name": "remote-server",
            "command": None,
            "args": [],
            "url": "https://mcp.example.test/api",
            "description": "Suite plugin description",
            "source": "plugin:suite",
        },
    ]
    assert _records(result.skills) == [
        {
            "name": "suite/plugin-helper",
            "description": "Plugin helper body",
            "source": "plugin:suite",
            "task_type": "general",
        },
        {
            "name": "alpha",
            "description": "Local helper",
            "source": "claude:skills",
            "task_type": "review",
        },
        {
            "name": "fallback",
            "description": "Fallback body",
            "source": "claude:skills",
            "task_type": "general",
        },
    ]
    assert _records(result.hooks) == [
        {
            "name": "suite/PreToolUse",
            "event": "PreToolUse",
            "handler_type": "http",
            "handler_config": {"type": "http", "url": "https://hooks.example.test/pre"},
            "description": "Hook from suite: PreToolUse",
            "source": "plugin:suite",
        },
        {
            "name": "suite/Stop",
            "event": "Stop",
            "handler_type": "command",
            "handler_config": {"type": "command", "command": "python stop.py"},
            "description": "Hook from suite: Stop",
            "source": "plugin:suite",
        },
    ]
    assert _records(result.agents) == [
        {
            "name": "reviewer",
            "description": "Review carefully.",
            "model_name": "claude-opus",
            "prompt": "# Reviewer\nReview carefully.\n",
            "source_file": str(agent_path),
        }
    ]


def test_plugin_install_path_precedes_cache_and_first_installed_entry_wins(tmp_path: Path):
    claude_dir = tmp_path / ".claude"
    installed_first = tmp_path / "installed-first"
    installed_second = tmp_path / "installed-second"
    cached = claude_dir / "plugins" / "cache" / "market" / "suite" / "9.0"
    for directory, server in (
        (installed_first, "from-first"),
        (installed_second, "from-second"),
        (cached, "from-cache"),
    ):
        _write_json(directory / ".mcp.json", {"mcpServers": {server: {"command": server}}})

    _write_json(claude_dir / "settings.json", {"enabledPlugins": {"suite@market": True}})
    _write_json(
        claude_dir / "plugins" / "installed_plugins.json",
        {
            "plugins": {
                "suite@market": [
                    {"installPath": str(installed_first)},
                    {"installPath": str(installed_second)},
                ]
            }
        },
    )

    result = ClaudeCodeAdapter().scan_home(tmp_path)

    assert [mcp.name for mcp in result.mcps] == ["from-first"]
    assert result.mcps[0].source == "plugin:suite"


def test_plugin_cache_uses_newest_version_and_plain_plugin_layout(tmp_path: Path):
    claude_dir = tmp_path / ".claude"
    _write_json(
        claude_dir / "settings.json",
        {
            "enabledPlugins": {
                "cached@market": True,
                "plain": True,
                "missing@market": True,
                "not-directory@market": True,
                "disabled@market": False,
            }
        },
    )
    old = claude_dir / "plugins" / "cache" / "market" / "cached" / "1.0"
    newest = claude_dir / "plugins" / "cache" / "market" / "cached" / "2.0"
    plain = claude_dir / "plugins" / "cache" / "plain" / "plain" / "1.0"
    _write_json(old / ".mcp.json", {"mcpServers": {"old": {"command": "old"}}})
    _write_json(newest / ".mcp.json", {"latest": {"command": "new"}})
    _write_json(plain / ".mcp.json", {"mcpServers": {"plain": {"command": "plain"}}})
    version_file = claude_dir / "plugins" / "cache" / "market" / "not-directory" / "1.0"
    _write_text(version_file, "not a plugin directory")
    now = time.time()
    os.utime(old, (now - 20, now - 20))
    os.utime(newest, (now - 10, now - 10))

    result = ClaudeCodeAdapter().scan_home(tmp_path)
    records = {record["name"]: record for record in _records(result.mcps)}

    assert records == {
        "latest": {
            "name": "latest",
            "command": "new",
            "args": [],
            "url": None,
            "description": "Plugin: cached",
            "source": "plugin:cached",
        },
        "plain": {
            "name": "plain",
            "command": "plain",
            "args": [],
            "url": None,
            "description": "Plugin: plain",
            "source": "plugin:plain",
        },
    }


def test_malformed_installed_registry_falls_back_to_plugin_cache(tmp_path: Path):
    claude_dir = tmp_path / ".claude"
    _write_json(claude_dir / "settings.json", {"enabledPlugins": {"cached@market": True}})
    _write_text(claude_dir / "plugins" / "installed_plugins.json", "{ malformed")
    cached = claude_dir / "plugins" / "cache" / "market" / "cached" / "1.0"
    _write_json(cached / ".mcp.json", {"mcpServers": {"fallback": {"command": "node"}}})

    assert [mcp.name for mcp in ClaudeCodeAdapter().scan_home(tmp_path).mcps] == ["fallback"]


def test_scan_project_follows_config_symlink_and_preserves_server_order(tmp_path: Path):
    target = tmp_path / "config-target.json"
    _write_json(
        target,
        {
            "mcpServers": {
                "stdio": {"command": "python", "args": ["server.py"]},
                "remote": {"url": "https://project.example.test/mcp"},
            }
        },
    )
    project = tmp_path / "project"
    project.mkdir()
    (project / ".mcp.json").symlink_to(target)
    adapter = ClaudeCodeAdapter()

    assert _records(adapter.scan_project(project).mcps) == [
        {
            "name": "stdio",
            "command": "python",
            "args": ["server.py"],
            "url": None,
            "description": "Claude Code project MCP: stdio",
            "source": "claude-code:project",
        },
        {
            "name": "remote",
            "command": None,
            "args": [],
            "url": "https://project.example.test/mcp",
            "description": "Claude Code project MCP: remote",
            "source": "claude-code:project",
        },
    ]

    _write_json(target, {"bare": {"command": "uvx", "args": ["bare-server"]}})
    assert _records(adapter.scan_project(project).mcps) == [
        {
            "name": "bare",
            "command": "uvx",
            "args": ["bare-server"],
            "url": None,
            "description": "Claude Code project MCP: bare",
            "source": "claude-code:project",
        }
    ]


def test_missing_malformed_and_unreadable_scan_configs_fail_soft(tmp_path: Path):
    adapter = ClaudeCodeAdapter()
    assert vars(adapter.scan_home(tmp_path)) == _empty_result()
    assert vars(adapter.scan_project(tmp_path)) == _empty_result()

    claude_dir = tmp_path / ".claude"
    local_skill = claude_dir / "skills" / "ignored" / "SKILL.md"
    _write_text(local_skill, "ignored without settings")
    assert vars(adapter.scan_home(tmp_path)) == _empty_result()

    _write_text(claude_dir / "settings.json", "{ malformed")
    assert vars(adapter.scan_home(tmp_path)) == _empty_result()
    (claude_dir / "settings.json").unlink()
    (claude_dir / "settings.json").mkdir()
    assert vars(adapter.scan_home(tmp_path)) == _empty_result()

    project_config = tmp_path / ".mcp.json"
    project_config.mkdir()
    assert vars(adapter.scan_project(tmp_path)) == _empty_result()


@pytest.mark.parametrize(
    ("settings", "error"),
    [
        ([], AttributeError),
        ({"enabledPlugins": []}, AttributeError),
    ],
)
def test_unsupported_settings_shapes_fail_loudly(tmp_path: Path, settings: object, error: type[Exception]):
    _write_json(tmp_path / ".claude" / "settings.json", settings)

    with pytest.raises(error):
        ClaudeCodeAdapter().scan_home(tmp_path)


@pytest.mark.parametrize(
    ("config", "error"),
    [
        ([], TypeError),
        ({"mcpServers": {"unsupported": "string"}}, AttributeError),
    ],
)
def test_unsupported_project_mcp_shapes_fail_loudly(tmp_path: Path, config: object, error: type[Exception]):
    _write_json(tmp_path / ".mcp.json", config)

    with pytest.raises(error):
        ClaudeCodeAdapter().scan_project(tmp_path)


@pytest.mark.parametrize(
    ("component", "content", "error"),
    [
        ("installed", [], AttributeError),
        ("metadata", [], AttributeError),
        ("mcp", [], TypeError),
        ("hooks", [], AttributeError),
    ],
)
def test_unsupported_plugin_json_shapes_fail_loudly(
    tmp_path: Path,
    component: str,
    content: object,
    error: type[Exception],
):
    claude_dir = tmp_path / ".claude"
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    _install_plugin(claude_dir, "suite@market", plugin_dir)

    if component == "installed":
        _write_json(claude_dir / "plugins" / "installed_plugins.json", content)
    elif component == "metadata":
        _write_json(plugin_dir / ".claude-plugin" / "plugin.json", content)
    elif component == "mcp":
        _write_json(plugin_dir / ".mcp.json", content)
    else:
        _write_json(plugin_dir / "hooks.json", content)

    with pytest.raises(error):
        ClaudeCodeAdapter().scan_home(tmp_path)


def test_component_read_errors_are_isolated_with_exact_fallbacks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    claude_dir = tmp_path / ".claude"
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    _install_plugin(claude_dir, "suite@market", plugin_dir)
    metadata = _write_json(plugin_dir / ".claude-plugin" / "plugin.json", {"description": "hidden"})
    plugin_mcp = _write_json(plugin_dir / ".mcp.json", {"mcpServers": {"hidden": {"command": "node"}}})
    plugin_skill = _write_text(plugin_dir / "skills" / "broken" / "SKILL.md", "hidden")
    plugin_hooks = _write_json(plugin_dir / "hooks.json", {"hooks": {"Stop": []}})
    local_skill = _write_text(claude_dir / "skills" / "broken" / "SKILL.md", "hidden")
    local_agent = _write_text(claude_dir / "agents" / "broken.md", "hidden")
    unreadable = {metadata, plugin_mcp, plugin_skill, plugin_hooks, local_skill, local_agent}
    original_read_text = Path.read_text

    def raise_for_components(path: Path, *args, **kwargs):
        if path in unreadable:
            raise OSError("unreadable")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", raise_for_components)

    result = ClaudeCodeAdapter().scan_home(tmp_path)

    assert result.mcps == []
    assert _records(result.skills) == [
        {
            "name": "suite/broken",
            "description": "Skill from suite",
            "source": "plugin:suite",
            "task_type": "general",
        },
        {
            "name": "broken",
            "description": "Skill: broken",
            "source": "claude:skills",
            "task_type": "general",
        },
    ]
    assert result.hooks == []
    assert result.agents == []


def test_unreadable_installed_registry_uses_cache_but_unreadable_cache_metadata_is_soft(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    claude_dir = tmp_path / ".claude"
    _write_json(claude_dir / "settings.json", {"enabledPlugins": {"cached@market": True}})
    installed = _write_json(claude_dir / "plugins" / "installed_plugins.json", {"plugins": {}})
    cached = claude_dir / "plugins" / "cache" / "market" / "cached" / "1.0"
    metadata = _write_json(cached / ".claude-plugin" / "plugin.json", {"description": "hidden"})
    _write_json(cached / ".mcp.json", {"mcpServers": {"visible": {"command": "node"}}})
    original_read_text = Path.read_text

    def raise_for_registry_and_metadata(path: Path, *args, **kwargs):
        if path in {installed, metadata}:
            raise OSError("unreadable")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", raise_for_registry_and_metadata)

    record = _records(ClaudeCodeAdapter().scan_home(tmp_path).mcps)
    assert record == [
        {
            "name": "visible",
            "command": "node",
            "args": [],
            "url": None,
            "description": "Plugin: cached",
            "source": "plugin:cached",
        }
    ]


def test_unreadable_cache_version_metadata_fails_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    claude_dir = tmp_path / ".claude"
    _write_json(claude_dir / "settings.json", {"enabledPlugins": {"cached@market": True}})
    version = claude_dir / "plugins" / "cache" / "market" / "cached" / "1.0"
    version.mkdir(parents=True)
    original_stat = Path.stat

    def raise_for_version(path: Path, *args, **kwargs):
        if path == version:
            raise PermissionError("unreadable metadata")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", raise_for_version)

    with pytest.raises(PermissionError, match="unreadable metadata"):
        ClaudeCodeAdapter().scan_home(tmp_path)


def test_empty_skills_and_agents_use_exact_defaults_and_sorted_order(tmp_path: Path):
    claude_dir = tmp_path / ".claude"
    _write_json(claude_dir / "settings.json", {})
    _write_text(claude_dir / "skills" / "zeta" / "SKILL.md", "")
    _write_text(claude_dir / "skills" / "alpha" / "SKILL.md", _markdown([], "Alpha body"))
    first_agent = _write_text(claude_dir / "agents" / "a.md", "")
    second_agent = _write_text(claude_dir / "agents" / "z.md", _markdown([], "Agent body"))

    result = ClaudeCodeAdapter().scan_home(tmp_path)

    assert _records(result.skills) == [
        {
            "name": "alpha",
            "description": "Alpha body",
            "source": "claude:skills",
            "task_type": "general",
        },
        {
            "name": "zeta",
            "description": "Skill: zeta",
            "source": "claude:skills",
            "task_type": "general",
        },
    ]
    assert _records(result.agents) == [
        {
            "name": "a",
            "description": "Agent: a",
            "model_name": "",
            "prompt": "",
            "source_file": str(first_agent),
        },
        {
            "name": "z",
            "description": "Agent body",
            "model_name": "",
            "prompt": f"{_FRONTMATTER}\n{_FRONTMATTER}\nAgent body",
            "source_file": str(second_agent),
        },
    ]


@pytest.mark.parametrize(
    ("managed_groups", "expected"), [(0, "missing"), (1, "partial"), (2, "partial"), (3, "partial")]
)
def test_detect_hooks_counts_managed_groups_and_ignores_unmanaged_entries(
    tmp_path: Path,
    managed_groups: int,
    expected: str,
):
    groups = [{"hooks": [{"type": "command", "command": "foreign"}]}]
    groups.extend(
        {"hooks": [{"type": "command", "command": "python -m observal_cli.hooks.session_push"}]}
        for _ in range(managed_groups)
    )
    _write_json(
        tmp_path / "settings.json",
        {"hooks": {"ignored": "not a list", "Stop": groups}},
    )

    assert ClaudeCodeAdapter().detect_hooks(tmp_path) == expected


def test_detect_hooks_requires_both_session_events(tmp_path: Path):
    from observal_cli.harness_specs.claude_code_hooks_spec import get_desired_hooks

    _write_json(tmp_path / "settings.json", {"hooks": get_desired_hooks()})
    assert ClaudeCodeAdapter().detect_hooks(tmp_path) == "installed"


@pytest.mark.parametrize(
    ("hook_type", "command"),
    [
        ("command", "python -m observal_cli.hooks.session_push --harness codex"),
        ("http", "python -m observal_cli.hooks.session_push --harness claude-code"),
    ],
)
def test_detect_hooks_rejects_other_harnesses_and_non_command_hooks(tmp_path: Path, hook_type: str, command: str):
    _write_json(
        tmp_path / "settings.json",
        {
            "hooks": {
                event: [{"hooks": [{"type": hook_type, "command": command}]}] for event in ("UserPromptSubmit", "Stop")
            }
        },
    )
    assert ClaudeCodeAdapter().detect_hooks(tmp_path) == "partial"


def test_detect_hooks_accepts_managed_url_and_fails_soft_on_missing_or_malformed_settings(tmp_path: Path):
    adapter = ClaudeCodeAdapter()
    assert adapter.detect_hooks(tmp_path) == "missing"

    settings = tmp_path / "settings.json"
    settings.write_text("{ malformed")
    assert adapter.detect_hooks(tmp_path) == "missing"
    settings.unlink()
    settings.mkdir()
    assert adapter.detect_hooks(tmp_path) == "missing"
    settings.rmdir()

    _write_json(
        settings,
        {"hooks": {"Stop": [{"hooks": [{"type": "http", "url": "/api/v1/telemetry/hooks"}]}]}},
    )
    assert adapter.detect_hooks(tmp_path) == "partial"


@pytest.mark.parametrize(
    "settings",
    [
        [],
        {"hooks": {"Stop": ["unsupported group"]}},
        {"hooks": {"Stop": [{"hooks": ["unsupported handler"]}]}},
    ],
)
def test_detect_hooks_unsupported_shapes_fail_loudly(tmp_path: Path, settings: object):
    _write_json(tmp_path / "settings.json", settings)

    with pytest.raises(AttributeError):
        ClaudeCodeAdapter().detect_hooks(tmp_path)


def test_hook_spec_generation_and_doctor_delegation_are_exact(monkeypatch: pytest.MonkeyPatch):
    from observal_cli import cmd_doctor
    from observal_cli.harness_specs import claude_code_hooks_spec

    adapter = ClaudeCodeAdapter()
    generated = {"Stop": [{"hooks": []}]}
    generate = Mock(return_value=generated)
    patch = Mock(return_value=True)
    cleanup = Mock(return_value=False)
    monkeypatch.setattr(claude_code_hooks_spec, "get_desired_hooks", generate)
    monkeypatch.setattr(cmd_doctor, "_patch_claude_code", patch)
    monkeypatch.setattr(cmd_doctor, "_cleanup_claude_code", cleanup)

    assert vars(adapter.get_hook_spec()) == {
        "events": ["PreToolUse", "PostToolUse", "Notification", "Stop", "SubagentStop"],
        "format": "command",
        "markers": ["observal", "OBSERVAL"],
        "env_vars": {},
    }
    assert adapter.generate_hook_config("https://server.example.test", "secret", "agent-id") is generated
    assert adapter.patch_hooks(dry_run=True) is True
    assert adapter.cleanup_hooks(dry_run=False) is False
    generate.assert_called_once_with()
    patch.assert_called_once_with(True)
    cleanup.assert_called_once_with(False)


def test_saved_model_and_install_options_precedence():
    adapter = ClaudeCodeAdapter()

    assert adapter.saved_model(None) is None
    assert adapter.saved_model({}) is None
    assert adapter.saved_model({"models_by_harness": {"claude-code": " sonnet "}, "model_name": "opus"}) == "sonnet"
    assert adapter.saved_model({"models_by_harness": {}, "model_name": " opus "}) == "opus"
    assert adapter.saved_model({"models_by_harness": {}, "model_name": " "}) is None
    assert adapter.saved_model({"models_by_harness": {}, "model_name": 7}) is None

    options = {"model": "sonnet", "tools": "existing"}
    adapter.apply_install_options(options, None)
    assert options == {"model": "sonnet", "tools": "existing"}
    adapter.apply_install_options(options, "Read,Write")
    assert options == {"model": "sonnet", "tools": "Read,Write"}


def test_resolve_session_source_uses_primary_fallback_default_home_and_subagent_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    project = tmp_path / ".claude" / "projects" / "-work-project"
    parent = _write_text(project / "parent.jsonl", "{}\n")
    child = _write_text(project / "parent" / "subagents" / "agent-child.jsonl", "{}\n")
    adapter = ClaudeCodeAdapter()

    assert adapter.resolve_session_source({}, home=tmp_path) is None
    assert adapter.resolve_session_source({"session_id": "missing", "cwd": "/work/project"}, home=tmp_path) is None
    assert adapter.resolve_session_source({"session_id": None}, home=tmp_path) is None

    primary = adapter.resolve_session_source({"session_id": "parent", "cwd": "/work/project"})
    assert primary == SessionSource("claude-code", "parent", parent, cwd="/work/project")

    fallback = adapter.resolve_session_source(
        {"session_id": "parent", "cwd": "/different/project"},
        home=tmp_path,
    )
    assert fallback == SessionSource("claude-code", "parent", parent, cwd="/different/project")

    subagent = adapter.resolve_session_source(
        {"session_id": "agent-child", "cwd": "/work/project"},
        home=tmp_path,
    )
    assert subagent == SessionSource(
        "claude-code",
        "child",
        child,
        cwd="/work/project",
        cursor_key="parent__sub__child",
        parent_session_id="parent",
    )


def test_discover_session_sources_filters_age_errors_and_sorts_exactly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = tmp_path / ".claude" / "projects"
    parent_b = _write_text(root / "project-b" / "b.jsonl", "{}\n")
    parent_a = _write_text(root / "project-a" / "a.jsonl", "{}\n")
    child = _write_text(root / "project-a" / "parent" / "subagents" / "agent-child.jsonl", "{}\n")
    old_parent = _write_text(root / "project-a" / "old.jsonl", "{}\n")
    old_child = _write_text(root / "project-a" / "old" / "subagents" / "agent-old.jsonl", "{}\n")
    unreadable = _write_text(root / "project-c" / "unreadable.jsonl", "{}\n")
    target = _write_text(tmp_path / "linked-target.jsonl", "{}\n")
    linked = root / "project-a" / "linked.jsonl"
    linked.symlink_to(target)
    now = time.time()
    for path in (old_parent, old_child):
        os.utime(path, (now - 7200, now - 7200))
    monkeypatch.setattr(time, "time", lambda: now)
    original_stat = Path.stat

    def raise_for_unreadable(path: Path, *args, **kwargs):
        if path == unreadable:
            raise PermissionError("unreadable")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", raise_for_unreadable)
    adapter = ClaudeCodeAdapter()

    sources = adapter.discover_session_sources(home=tmp_path, since_hours=1)
    expected = [
        SessionSource("claude-code", "a", parent_a),
        SessionSource("claude-code", "child", child, cursor_key="parent__sub__child", parent_session_id="parent"),
        SessionSource("claude-code", "linked", linked),
        SessionSource("claude-code", "b", parent_b),
    ]
    assert sources == sorted(expected, key=lambda source: str(source.path))
    assert adapter.discover_session_sources(home=tmp_path / "missing", since_hours=1) == []
    assert adapter._recent(unreadable, 0) is False


def test_related_session_sources_preserve_parent_cwd_order_and_symlink_path(tmp_path: Path):
    parent = _write_text(tmp_path / "project" / "parent.jsonl", "{}\n")
    subagents = parent.parent / "parent" / "subagents"
    child_z = _write_text(subagents / "agent-z.jsonl", "{}\n")
    target = _write_text(tmp_path / "child-a-target.jsonl", "{}\n")
    child_a = subagents / "agent-a.jsonl"
    child_a.symlink_to(target)
    _write_text(subagents / "ignored.jsonl", "{}\n")
    adapter = ClaudeCodeAdapter()
    source = SessionSource("claude-code", "parent", parent, cwd="/work")

    assert adapter.related_session_sources(SessionSource("claude-code", "none")) == []
    assert (
        adapter.related_session_sources(SessionSource("claude-code", "child", parent, parent_session_id="parent")) == []
    )
    assert adapter.related_session_sources(SessionSource("claude-code", "absent", parent)) == []
    assert adapter.related_session_sources(source) == [
        SessionSource(
            "claude-code",
            "a",
            child_a,
            cwd="/work",
            cursor_key="parent__sub__a",
            parent_session_id="parent",
        ),
        SessionSource(
            "claude-code",
            "z",
            child_z,
            cwd="/work",
            cursor_key="parent__sub__z",
            parent_session_id="parent",
        ),
    ]


def test_scan_command_deduplicates_home_before_project_with_stable_scope_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from observal_cli.main import app

    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    claude_dir = home / ".claude"
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    _install_plugin(claude_dir, "suite@market", plugin_dir)
    _write_json(
        plugin_dir / ".mcp.json",
        {
            "mcpServers": {
                "shared": {"command": "home-command", "args": ["home.js"]},
                "home-only": {"command": "home-only"},
            }
        },
    )
    _write_json(
        project / ".mcp.json",
        {
            "mcpServers": {
                "shared": {"command": "project-command", "args": ["project.js"]},
                "project-only": {"command": "project-only"},
            }
        },
    )
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.chdir(project)

    result = CliRunner().invoke(app, ["scan", "-i", "claude-code", "-o", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "harnesses": [{"name": "claude-code", "hooks": "missing"}],
        "mcps": [
            {
                "name": "shared",
                "command": "home-command",
                "args": ["home.js"],
                "url": None,
                "description": "Plugin: suite",
                "source": "plugin:suite",
            },
            {
                "name": "home-only",
                "command": "home-only",
                "args": [],
                "url": None,
                "description": "Plugin: suite",
                "source": "plugin:suite",
            },
            {
                "name": "project-only",
                "command": "project-only",
                "args": [],
                "url": None,
                "description": "Claude Code project MCP: project-only",
                "source": "claude-code:project",
            },
        ],
        "skills": [],
        "hooks": [],
        "agents": [],
    }
