# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Claude Code harness adapter for agent config generation."""

from __future__ import annotations

from observal_shared.harness_registry import HARNESS_REGISTRY
from services.harness import BaseHarnessAdapter, ConfigContext, McpConfigContext, register_adapter
from services.harness.helpers import (
    _claude_code_hooks_frontmatter_lines,
    _collect_hook_script_files,
    _model_name_to_frontmatter,
)


def _claude_mcp_add_command(name: str, entry: dict, scope: str | None = None) -> list[str]:
    """Build the ``claude mcp add`` argv that registers one MCP entry.

    ``-e`` and ``-H`` accept several values, so they follow the name and URL;
    placed before them they would swallow the name as one more value.
    """
    command = ["claude", "mcp", "add"]
    url = entry.get("url")
    if url:
        transport = "http" if entry.get("type") in ("streamable-http", "http") else "sse"
        command += ["--transport", transport]
    if scope:
        command += ["--scope", scope]
    if url:
        command += [name, url]
        for header, value in (entry.get("headers") or {}).items():
            command += ["-H", f"{header}: {value}"]
        return command
    command.append(name)
    for key, value in (entry.get("env") or {}).items():
        command += ["-e", f"{key}={value}"]
    return [*command, "--", entry.get("command", ""), *entry.get("args", [])]


class ClaudeCodeAdapter(BaseHarnessAdapter):
    """Claude Code harness adapter."""

    @property
    def harness_name(self) -> str:
        return "claude-code"

    def format_hook_install_snippet(self, event: str, handler_type: str, command: str, timeout: int | None) -> dict:
        entry: dict = {"type": handler_type, "command": command}
        if timeout:
            entry["timeout"] = timeout
        return {"hooks": {event: [{"matcher": "*", "hooks": [entry]}]}}

    def hook_install_notes(self) -> list[str]:
        return ["Also works in Cursor via Third Party Hooks (enable in Cursor Settings → Features)."]

    def format_hook_telemetry(self, hook_listing, server_url: str, platform: str) -> dict:
        entry = {
            "type": "http",
            "url": f"{server_url}/api/v1/telemetry/hooks",
            "timeout": 10,
            "allowedEnvVars": ["OBSERVAL_API_KEY"],
        }
        return {"hooks": {str(hook_listing.event): [{"matcher": "*", "hooks": [entry]}]}}

    def skill_hook_extra(self) -> dict:
        return {"allowedEnvVars": ["OBSERVAL_ACCESS_TOKEN"]}

    def skill_frontmatter_extra(self, slash_command: str | None) -> dict:
        if not slash_command:
            return {}
        from schemas.skill_commands import normalize_slash_command

        return {"command": f"/{normalize_slash_command(slash_command)}"}

    def format_mcp_config(self, ctx: McpConfigContext) -> dict:
        entry = ctx.standard_entry()
        if ctx.url:
            return {
                "command": _claude_mcp_add_command(ctx.name, entry),
                "type": "shell_command",
                "claude_settings_snippet": {"env": ctx.server_env} if ctx.server_env else {},
                "mcpServers": {ctx.name: entry},
            }
        return {
            "command": _claude_mcp_add_command(ctx.name, entry),
            "type": "shell_command",
        }

    def default_model_candidate(self, model_name: str | None) -> str | None:
        return model_name

    def format_model(self, model: str, provider: str) -> str:
        lowered = model.lower()
        for alias in ("opus", "sonnet", "haiku"):
            if alias in lowered:
                return alias
        return model

    def format_config(self, ctx: ConfigContext) -> dict:
        safe_name = ctx.safe_name
        options = ctx.options
        mcp_configs = ctx.mcp_configs
        rules_content = ctx.rules_content
        hook_configs = ctx.hook_configs
        skill_configs = ctx.skill_configs
        scope = options.get("scope", HARNESS_REGISTRY["claude-code"]["default_scope"])
        # A user-scope agent runs in every project, so its servers must too. Project
        # scope keeps Claude Code's default "local" scope: this project, this user.
        mcp_scope = "user" if scope == "user" else "local"
        setup_commands = []
        # `claude mcp add` refuses a name that already exists in the scope, so a
        # re-pull first drops the previous registration. The CLI ignores failures
        # here: on a first pull there is nothing to remove.
        reset_commands = []
        claude_mcps = {}
        for name, cfg in mcp_configs.items():
            if cfg.get("url") or cfg.get("type") in ("sse", "streamable-http"):
                # SSE/streamable-http entry: preserve as-is (url, headers, env)
                claude_mcps[name] = cfg
                if not cfg.get("url"):
                    continue
            else:
                claude_mcps[name] = {
                    "command": cfg.get("command", ""),
                    "args": cfg.get("args", []),
                    "env": cfg.get("env", {}),
                }
            reset_commands.append(["claude", "mcp", "remove", "--scope", mcp_scope, name])
            setup_commands.append(_claude_mcp_add_command(name, claude_mcps[name], mcp_scope))

        tools = options.get("tools", "")
        color = options.get("color", "")

        # Model resolution
        if "_resolved_model" in options:
            model_choice = options.get("_resolved_model") or ""
        else:
            model_choice = options.get("model", "")
            if not model_choice or model_choice == "inherit":
                model_choice = _model_name_to_frontmatter(getattr(ctx.agent, "model_name", ""))

        # Build YAML frontmatter
        frontmatter_lines = [
            "---",
            f"name: {safe_name}",
        ]
        agent_desc = getattr(ctx.agent, "description", "") or ""
        if agent_desc:
            frontmatter_lines.append(f'description: "{agent_desc}"')
        if model_choice:
            frontmatter_lines.append(f"model: {model_choice}")
        if tools:
            frontmatter_lines.append(f"tools: {tools}")
        if color:
            frontmatter_lines.append(f"color: {color}")
        if claude_mcps:
            frontmatter_lines.append("mcpServers:")
            for mcp_name in claude_mcps:
                frontmatter_lines.append(f"  - {mcp_name}")
        frontmatter_lines.extend(_claude_code_hooks_frontmatter_lines(custom_hooks=hook_configs))
        frontmatter_lines.append("---")
        agent_content = "\n".join(frontmatter_lines) + "\n\n" + rules_content

        agent_path = HARNESS_REGISTRY["claude-code"]["agent_profile"][scope].format(name=safe_name)

        result: dict = {
            "agent_profile": {"path": agent_path, "content": agent_content},
            "mcp_config": claude_mcps,
            "mcp_reset_commands": reset_commands,
            "mcp_setup_commands": setup_commands,
            "scope": scope,
        }
        if skill_configs:
            result["skill_components"] = skill_configs

        cc_hook_files = _collect_hook_script_files(hook_configs, ctx.hook_listings, "claude-code")
        if cc_hook_files:
            result["hook_files"] = cc_hook_files

        warnings_combined = list(ctx.compatibility_warnings)
        warnings_combined.extend(options.get("_model_warnings") or [])
        if warnings_combined:
            result["_warnings"] = warnings_combined

        return result


register_adapter(ClaudeCodeAdapter())
