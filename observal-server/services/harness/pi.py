# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Pi harness adapter for agent config generation.

Pi is harness-centric: `observal pull` writes AGENTS.md which becomes pi's
entire system prompt, effectively reconfiguring the whole agent runtime.
MCP servers are written to ~/.pi/agent/mcp.json (read by pi-mcp-adapter).
Skills go to .pi/skills/ or ~/.pi/agent/skills/.

Registry command hooks are written to the profile's ``observal-hooks.json``,
which the Observal Pi extension runs on ``tool_call`` and ``tool_result``
(docs/integrations/pi.md, "Registry hooks"). Every hook outside that supported
matrix is reported as a warning and not written, never dropped silently.
"""

from __future__ import annotations

from loguru import logger as optic

from observal_shared.harness_registry import HARNESS_REGISTRY
from services.harness import BaseHarnessAdapter, ConfigContext, register_adapter

# Must match PI_HOOKS_SCHEMA in packages/pi-extension/extensions/observal.ts.
PI_HOOKS_SCHEMA = "observal-pi-hooks/v1"
PI_HOOKS_FILE = "observal-hooks.json"
PI_HOOK_DEFAULT_TIMEOUT = 60
PI_HOOK_MAX_TIMEOUT = 600


def _pi_hook_timeout(handler_config: dict) -> int:
    timeout = handler_config.get("timeout")
    if type(timeout) is int and 1 <= timeout <= PI_HOOK_MAX_TIMEOUT:
        return timeout
    return PI_HOOK_DEFAULT_TIMEOUT


def pi_hook_entries(hook_configs: list[dict], scope: str) -> tuple[list[dict], list[str]]:
    """The hooks the Pi extension can run, and a warning for each one it cannot.

    Supported: user scope, a ``command`` handler with an inline command (no script
    file, no tool filter), on ``PreToolUse`` (Pi ``tool_call``) or ``PostToolUse``
    (Pi ``tool_result``).
    """
    events = HARNESS_REGISTRY["pi"]["hook_events_map"]
    entries: list[dict] = []
    warnings: list[str] = []
    for hook in hook_configs:
        name = hook.get("name") or "hook"
        handler_config = hook.get("handler_config") or {}
        command = handler_config.get("command") if isinstance(handler_config, dict) else None
        reason = None
        if scope != "user":
            reason = "Pi runs registry hooks only from user-scope profiles"
        elif hook.get("event") not in events:
            reason = f"Pi does not support the {hook.get('event') or 'unknown'} hook event"
        elif hook.get("handler_type", "command") != "command":
            reason = "Pi runs only command hooks"
        elif hook.get("script_filename") or hook.get("script_content"):
            reason = "Pi does not install hook scripts yet"
        elif hook.get("tool_filter"):
            reason = "Pi cannot apply a hook tool filter"
        elif not isinstance(command, str) or not command.strip():
            reason = "the hook has no command"
        if reason:
            warnings.append(f"Hook '{name}' was not installed for Pi: {reason}.")
            continue
        entries.append(
            {
                "name": name,
                "event": events[hook["event"]],
                "type": "command",
                "command": command,
                "timeout": _pi_hook_timeout(handler_config),
            }
        )
    return entries, warnings


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
        - ~/.pi/agent/agents/{agent}/observal-hooks.json

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

        # ── Hooks (run by the Observal Pi extension) ──
        warnings: list[str] = []
        if ctx.hook_configs:
            entries, hook_warnings = pi_hook_entries(ctx.hook_configs, scope)
            warnings.extend(hook_warnings)
            if entries:
                result["hooks_config"] = {
                    "path": f"~/.pi/agent/agents/{agent_name}/{PI_HOOKS_FILE}",
                    "content": {"schema": PI_HOOKS_SCHEMA, "agent": agent_name, "hooks": entries},
                }
        if warnings:
            result["_warnings"] = warnings

        return result


register_adapter(PiAdapter())
