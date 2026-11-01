# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 RAWx18 <rawx18.dev@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Goose harness adapter for agent config generation.

Emits, per install:
- a custom agent markdown file (``.agents/agents/<name>.md``)
- MCP servers as goose ``extensions`` merged into ``config.yaml``
- skill components installed through the shared skill installer
- an Observal goose plugin (``plugin.json`` + ``hooks/hooks.json``) carrying the
  session telemetry hooks and any registry hook components
"""

from __future__ import annotations

import json

import yaml
from loguru import logger as optic

from observal_shared.harness_registry import HARNESS_REGISTRY
from services.harness import BaseHarnessAdapter, ConfigContext, McpConfigContext, register_adapter
from services.harness.helpers import (
    _collect_hook_script_files,
    _merge_hook_components_into_config,
)

_GOOSE_SESSION_PUSH_CMD = "python3 -m observal_cli.hooks.session_push --harness goose"
# Mirrors observal_cli.harness_specs.goose_hooks_spec; the CLI cannot be imported here.
_GOOSE_HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd")
_HOOK_TIMEOUT_SECONDS = 30
_DEFAULT_EXTENSION_TIMEOUT = 300
_PLUGIN_DIR = ".agents/plugins/observal"
# goose runs plugin hooks through `sh -c` with PLUGIN_ROOT set to the plugin
# directory, so a script is found whatever directory the session started in.
_PLUGIN_SCRIPT_COMMAND = '"${{PLUGIN_ROOT}}/scripts/{filename}"'
_PLUGIN_MANIFEST = {
    "name": "observal",
    "version": "1.0.0",
    "description": "Observal session telemetry and hook components for goose",
}


def _goose_hooks_config(platform: str = "") -> dict:
    """Build the Observal plugin ``hooks/hooks.json`` content.

    Rules omit ``matcher`` so they run for every event of that type; goose
    treats ``matcher`` as a regular expression, not a glob.
    """
    command = (
        "python -m observal_cli.hooks.session_push --harness goose" if platform == "win32" else _GOOSE_SESSION_PUSH_CMD
    )
    return {
        "hooks": {
            event: [{"hooks": [{"type": "command", "command": command, "timeout": _HOOK_TIMEOUT_SECONDS}]}]
            for event in _GOOSE_HOOK_EVENTS
        }
    }


def _agent_markdown(name: str, description: str, model: str | None, body: str) -> str:
    """Render a goose custom agent file: YAML frontmatter plus instructions."""
    frontmatter: dict = {"name": name, "description": description}
    if model:
        frontmatter["model"] = model
    rendered = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return f"---\n{rendered}---\n\n{body}"


class GooseAdapter(BaseHarnessAdapter):
    """Goose harness adapter."""

    @property
    def harness_name(self) -> str:
        return "goose"

    def agent_mcp_entry(self, ctx: McpConfigContext) -> dict:
        """Return a goose ``extensions`` entry for one MCP server."""
        if ctx.url:
            entry: dict = {
                "type": "streamable_http",
                "name": ctx.name,
                "enabled": True,
                "uri": ctx.url,
                "headers": ctx.headers or {},
            }
        else:
            entry = {
                "type": "stdio",
                "name": ctx.name,
                "enabled": True,
                "cmd": ctx.command,
                "args": list(ctx.args),
            }
        entry["envs"] = dict(ctx.server_env or {})
        entry["env_keys"] = []
        entry["timeout"] = _DEFAULT_EXTENSION_TIMEOUT
        return entry

    def format_mcp_config(self, ctx: McpConfigContext) -> dict:
        return {"extensions": {ctx.name: self.agent_mcp_entry(ctx)}}

    def format_hook_component(self, command: str) -> dict:
        return {"hooks": [{"type": "command", "command": command}]}

    def format_hook_install_snippet(self, event: str, handler_type: str, command: str, timeout: int | None) -> dict:
        entry: dict = {"type": "command", "command": command}
        if timeout:
            entry["timeout"] = timeout
        return {
            "hooks": {event: [{"hooks": [entry]}]},
            "_note": f"Add to {_PLUGIN_DIR}/hooks/hooks.json",
        }

    def format_hook_telemetry(self, hook_listing, server_url: str, platform: str) -> dict:
        return _goose_hooks_config(platform)

    def format_config(self, ctx: ConfigContext) -> dict:
        optic.debug("format_config: agent={}", ctx.safe_name)
        spec = HARNESS_REGISTRY["goose"]
        options = ctx.options
        scope = options.get("scope", spec["default_scope"])
        description = (getattr(ctx.agent, "description", "") or ctx.safe_name).replace("\n", " ").strip()[:200]

        result: dict = {
            "agent_profile": {
                "path": spec["agent_profile"][scope].format(name=ctx.safe_name),
                "content": _agent_markdown(
                    ctx.safe_name,
                    description,
                    options.get("_resolved_model"),
                    ctx.rules_content,
                ),
            },
            "scope": scope,
        }

        extensions = {name: self._as_extension(name, entry) for name, entry in ctx.mcp_configs.items()}
        if extensions:
            result["mcp_config"] = {
                "path": spec["mcp_config"]["user"],
                "content": {spec["mcp_servers_key"]: extensions},
            }

        hooks_content = _goose_hooks_config(ctx.platform)
        _merge_hook_components_into_config(hooks_content, ctx.hook_configs, "goose", _PLUGIN_SCRIPT_COMMAND)
        hooks_path = spec["hooks"][scope]
        result["hooks_config"] = {
            "path": hooks_path,
            "content": hooks_content,
            "merge": True,
        }

        hook_files = [
            {
                "path": hooks_path.replace("hooks/hooks.json", "plugin.json"),
                "content": json.dumps(_PLUGIN_MANIFEST, indent=2) + "\n",
            }
        ]
        # Scripts live inside the plugin, so they follow it to the home directory.
        for script in _collect_hook_script_files(ctx.hook_configs, ctx.hook_listings, "goose"):
            if scope == "user":
                script["path"] = f"~/{script['path']}"
            hook_files.append(script)
        result["hook_files"] = hook_files

        if ctx.skill_configs:
            result["skill_components"] = ctx.skill_configs

        warnings = list(ctx.compatibility_warnings)
        warnings.extend(options.get("_model_warnings") or [])
        if warnings:
            result["_warnings"] = warnings

        return result

    @staticmethod
    def _as_extension(name: str, entry: dict) -> dict:
        """Normalise external and sandbox MCP entries into goose extension shape."""
        if entry.get("type") in ("stdio", "streamable_http"):
            return entry
        extension: dict = {
            "type": "stdio",
            "name": name,
            "enabled": True,
            "cmd": entry.get("command"),
            "args": list(entry.get("args") or []),
            "envs": dict(entry.get("env") or {}),
            "env_keys": [],
            "timeout": _DEFAULT_EXTENSION_TIMEOUT,
        }
        if entry.get("url"):
            extension.update({"type": "streamable_http", "uri": entry["url"], "headers": entry.get("headers") or {}})
            extension.pop("cmd", None)
            extension.pop("args", None)
        return extension


register_adapter(GooseAdapter())
