# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Codex CLI harness adapter for agent config generation."""

from __future__ import annotations

import json

from observal_shared.harness_registry import HARNESS_REGISTRY
from services.harness import BaseHarnessAdapter, ConfigContext, McpConfigContext, register_adapter

_CODEX_SESSION_PUSH_CMD = "python3 -m observal_cli.hooks.codex_session_push"


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _codex_agent_toml(name: str, description: str, instructions: str, model: str | None) -> str:
    lines = [
        f"name = {_toml_string(name)}",
        f"description = {_toml_string(description or name)}",
        f"developer_instructions = {_toml_string(instructions)}",
    ]
    if model:
        lines.append(f"model = {_toml_string(model)}")
    return "\n".join(lines) + "\n"


def _codex_hooks_config(agent_name: str = "") -> dict:
    """Build ~/.codex/hooks.json content for Codex CLI (Claude Code format)."""
    cmd = _CODEX_SESSION_PUSH_CMD
    if agent_name:
        cmd = f"OBSERVAL_AGENT_NAME={agent_name} {cmd}"
    return {
        "hooks": {
            "UserPromptSubmit": [{"matcher": "", "hooks": [{"type": "command", "command": cmd}]}],
            "Stop": [{"matcher": "", "hooks": [{"type": "command", "command": cmd}]}],
        },
    }


class CodexAdapter(BaseHarnessAdapter):
    """Codex CLI harness adapter."""

    @property
    def harness_name(self) -> str:
        return "codex"

    def format_hook_install_snippet(self, event: str, handler_type: str, command: str, timeout: int | None) -> dict:
        return {
            "hooks": {event: {"command": command}},
            "_format": "toml",
            "_note": f"Add to .codex/config.toml under [hooks.{event}]",
        }

    def format_mcp_config(self, ctx: McpConfigContext) -> dict:
        if ctx.url:
            entry: dict = {"url": ctx.url}
            if ctx.headers:
                entry["headers"] = ctx.headers
            if ctx.server_env:
                entry["env"] = ctx.server_env
        else:
            entry = ctx.standard_entry()
        return {"mcp_servers": {ctx.name: entry}}

    def format_config(self, ctx: ConfigContext) -> dict:
        options = ctx.options
        mcp_configs = ctx.mcp_configs
        rules_content = ctx.rules_content

        codex_spec = HARNESS_REGISTRY["codex"]
        codex_scope = options.get("scope") or codex_spec["default_scope"]
        codex_content: dict = {codex_spec.get("mcp_servers_key", "mcp_servers"): mcp_configs}
        codex_model = options.get("_resolved_model")

        result: dict = {
            "agent_profile": {
                "path": codex_spec["agent_profile"][codex_scope].format(name=ctx.safe_name),
                "content": _codex_agent_toml(
                    ctx.safe_name,
                    (ctx.agent.description or ctx.safe_name).replace("\n", " ").strip()[:200],
                    rules_content,
                    codex_model,
                ),
            },
            "mcp_config": {"path": codex_spec["mcp_config"][codex_scope], "content": codex_content},
            "hooks_config": {
                "path": codex_spec["hooks"][codex_scope],
                "content": _codex_hooks_config(agent_name=ctx.safe_name),
            },
            "scope": codex_scope,
        }

        if ctx.skill_configs:
            result["skill_components"] = ctx.skill_configs

        warnings_combined = list(ctx.compatibility_warnings)
        warnings_combined.extend(options.get("_model_warnings") or [])
        if warnings_combined:
            result["_warnings"] = warnings_combined

        return result


register_adapter(CodexAdapter())
