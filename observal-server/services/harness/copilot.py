# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Copilot (VS Code) harness adapter for agent config generation.

Copilot in VS Code does not support hooks. This adapter generates:
- .github/agents/{name}.agent.md (agent file with YAML frontmatter)
- .vscode/mcp.json (MCP server config with "servers" key)
"""

from __future__ import annotations

from observal_shared.harness_registry import HARNESS_REGISTRY
from services.harness import BaseHarnessAdapter, ConfigContext, McpConfigContext, register_adapter
from services.harness.helpers import _generate_prompt_files, _generate_skill


class CopilotAdapter(BaseHarnessAdapter):
    """GitHub Copilot (VS Code) harness adapter."""

    @property
    def harness_name(self) -> str:
        return "copilot"

    def format_hook_install_snippet(self, event: str, handler_type: str, command: str, timeout: int | None) -> dict:
        return {"hooks": {event: [{"command": command}]}}

    def format_hook_component(self, command: str) -> dict:
        return {"type": "command", "command": command}

    def emits_prompt_files(self) -> bool:
        return True

    def format_mcp_config(self, ctx: McpConfigContext) -> dict:
        entry = ctx.standard_entry() if ctx.url else {"type": "stdio", **ctx.standard_entry()}
        return {"mcpServers": {ctx.name: entry}}

    def format_config(self, ctx: ConfigContext) -> dict:
        safe_name = ctx.safe_name
        mcp_configs = ctx.mcp_configs
        rules_content = ctx.rules_content

        copilot_configs = {}
        for name, config in mcp_configs.items():
            entry = dict(config)
            entry["type"] = config.get("type", "sse") if config.get("url") else "stdio"
            copilot_configs[name] = entry

        copilot_spec = HARNESS_REGISTRY["copilot"]

        agent_desc = getattr(ctx.agent, "description", "") or safe_name
        frontmatter_lines = [
            "---",
            f"name: {safe_name}",
            f'description: "{agent_desc}"',
            "target: vscode",
            "tools: ['*']",
            "---",
        ]
        agent_content = "\n".join(frontmatter_lines) + "\n\n" + rules_content

        result: dict = {
            "agent_profile": {
                "path": f".github/agents/{safe_name}.agent.md",
                "content": agent_content,
            },
            "mcp_config": {
                "path": copilot_spec["mcp_config"]["project"],
                "content": {copilot_spec["mcp_servers_key"]: copilot_configs},
            },
            "scope": copilot_spec["default_scope"],
        }
        # Direct skills must be emitted so complete-folder preflight can bind
        # the adapter's destination to the selected, reviewed release.
        direct_skills = [skill for skill in ctx.skill_configs if skill.get("delivery_mode") == "registry_direct"]
        skills = [_generate_skill(skill, "copilot", "project") for skill in direct_skills]
        skills = [file for file in skills if file]
        if skills:
            result["skills"] = skills
            result["skill_components"] = direct_skills
        # Native Copilot prompt files (.github/prompts/*.prompt.md)
        prompt_files = _generate_prompt_files(ctx.prompt_listings, ctx.agent, ctx.component_names)
        if prompt_files:
            result["prompt_files"] = prompt_files
        if ctx.compatibility_warnings:
            result["_warnings"] = ctx.compatibility_warnings

        return result


register_adapter(CopilotAdapter())
