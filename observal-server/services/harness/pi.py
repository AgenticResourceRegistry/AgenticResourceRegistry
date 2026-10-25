# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Pi harness adapter for agent config generation.

Pi is harness-centric: `observal pull` writes AGENTS.md which becomes pi's
entire system prompt, effectively reconfiguring the whole agent runtime.
MCP servers are written to ~/.pi/agent/mcp.json (read by pi-mcp-adapter).
Skills go to .pi/skills/ or ~/.pi/agent/skills/.
"""

from __future__ import annotations

from loguru import logger as optic

from observal_shared.harness_registry import HARNESS_REGISTRY
from services.harness import BaseHarnessAdapter, ConfigContext, register_adapter


class PiAdapter(BaseHarnessAdapter):
    """Pi harness adapter - harness-centric config generation."""

    @property
    def harness_name(self) -> str:
        return "pi"

    def format_config(self, ctx: ConfigContext) -> dict:
        """Format config for Pi.

        Pi uses isolated profile directories for agents:
        - ~/.pi/agent/agents/{agent}/AGENTS.md
        - ~/.pi/agent/agents/{agent}/mcp.json
        - ~/.pi/agent/agents/{agent}/skills/{name}/SKILL.md

        Project scope uses the same layout under .pi/agents/{agent}/.
        """
        optic.debug("PiAdapter.format_config: agent={}", ctx.safe_name)
        options = ctx.options
        scope = options.get("scope", HARNESS_REGISTRY["pi"]["default_scope"])
        agent_name = ctx.safe_name

        result: dict = {}

        def _rewrite_path(p: str) -> str:
            # Rewrite ~/.pi/agent/... to ~/.pi/agent/agents/{agent_name}/...
            if p.startswith("~/.pi/agent/"):
                return p.replace("~/.pi/agent/", f"~/.pi/agent/agents/{agent_name}/", 1)
            elif p.startswith(".pi/"):
                return p.replace(".pi/", f".pi/agents/{agent_name}/", 1)
            elif "/" not in p:
                # Project root files (AGENTS.md) are loaded by every pi session in
                # the repo and may be the user's own; keep them in the agent's profile.
                return f".pi/agents/{agent_name}/{p}"
            return p

        # ── Rules / Agent file (AGENTS.md) ──
        if ctx.rules_content:
            rules_spec = HARNESS_REGISTRY["pi"]["agent_profile"]
            rules_path = rules_spec.get(scope, rules_spec.get("user", "AGENTS.md"))
            result["agent_profile"] = {
                "path": _rewrite_path(rules_path),
                "content": ctx.rules_content,
            }

        # ── MCP config (for pi-mcp-adapter) ──
        if ctx.mcp_configs:
            mcp_path_spec = HARNESS_REGISTRY["pi"]["mcp_config"]
            mcp_path = mcp_path_spec.get(scope, mcp_path_spec.get("user"))
            if mcp_path:
                result["mcp_config"] = {
                    "path": _rewrite_path(mcp_path),
                    "content": {"mcpServers": ctx.mcp_configs},
                }

        # ── Skills ──
        if ctx.skill_configs:
            skill_path_spec = HARNESS_REGISTRY["pi"]["skills"]
            skill_path = skill_path_spec.get(scope, skill_path_spec.get("user"))
            rewritten_skills = []
            for skill in ctx.skill_configs:
                skill_copy = dict(skill)
                name = skill_copy.get("name")
                if skill_path and name:
                    skill_copy["path"] = _rewrite_path(skill_path.format(name=name))
                rewritten_skills.append(skill_copy)
            result["skill_components"] = rewritten_skills

        return result


register_adapter(PiAdapter())
