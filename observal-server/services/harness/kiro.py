# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Kiro harness adapter for agent config generation."""

from __future__ import annotations

import re

from observal_shared.harness_registry import HARNESS_REGISTRY
from services.harness import BaseHarnessAdapter, ConfigContext, register_adapter
from services.harness.helpers import (
    _KIRO_V1_MATCHER_TRIGGERS,
    _KIRO_V1_TRIGGER_MAP,
    _collect_hook_script_files,
    _wrap_kiro_prompt,
)

_KIRO_MODEL_RE = re.compile(r"^(claude-[a-z]+-\d{1,3})-(\d{1,3})(-\d{8})?$")


class KiroAdapter(BaseHarnessAdapter):
    """Kiro harness adapter."""

    @property
    def harness_name(self) -> str:
        return "kiro"

    @staticmethod
    def _v1_hook_file(name: str, trigger: str, command: str, timeout: int | None = None) -> dict:
        """Wrap a single command in the standalone v1 hooks-file schema.

        Kiro IDE 1.0 / CLI 3.0 read ``.kiro/hooks/*.json`` files in this shape;
        the legacy ``{"hooks": {event: [...]}}`` inline shape is CLI 2.x only and
        makes the IDE hide the agent it is attached to.
        """
        entry: dict = {
            "name": name,
            "trigger": trigger,
            "action": {"type": "command", "command": command},
        }
        if timeout:
            entry["timeout"] = timeout
        return {"version": "v1", "hooks": [entry]}

    def format_hook_install_snippet(self, event: str, handler_type: str, command: str, timeout: int | None) -> dict:
        trigger = _KIRO_V1_TRIGGER_MAP.get(event, event)
        return self._v1_hook_file(f"observal-{trigger.lower()}", trigger, command, timeout)

    def format_hook_telemetry(self, hook_listing, server_url: str, platform: str) -> dict:
        raw_event = str(hook_listing.event)
        trigger = _KIRO_V1_TRIGGER_MAP.get(raw_event, raw_event)
        python = "python" if platform == "win32" else "python3"
        module = "kiro_stop_hook" if trigger == "Stop" else "kiro_hook"
        command = f"{python} -m observal_cli.hooks.{module} --url {server_url}/api/v1/telemetry/hooks"
        return self._v1_hook_file(f"observal-telemetry-{trigger.lower()}", trigger, command)

    def format_model(self, model: str, provider: str) -> str:
        match = _KIRO_MODEL_RE.match(model)
        if not match:
            return model
        return f"{match.group(1)}.{match.group(2)}{match.group(3) or ''}"

    def format_config(self, ctx: ConfigContext) -> dict:
        safe_name = ctx.safe_name
        options = ctx.options
        platform = ctx.platform
        mcp_configs = ctx.mcp_configs
        hook_configs = ctx.hook_configs
        skill_configs = ctx.skill_configs

        # Telemetry via JSONL session push. The UUID is resolved through the local lockfile at push time.
        #
        # Hooks go into the standalone v1 file (.kiro/hooks/observal.json), NOT
        # inline in the agent profile: Kiro IDE 1.0 loads an inline-hooked agent
        # fine but never fires those hooks, so such agents were visible and
        # silent. The v1 file is read by IDE 1.0 and CLI 3.0 alike. The CLI
        # re-adds inline hooks locally when it detects a legacy CLI 2.x with no
        # IDE installed.
        # No OBSERVAL_AGENT_ID: there is one hooks file per scope and Kiro runs
        # it for every session whatever agent is active, so a per-agent id here
        # would just make each pulled agent rewrite the previous one's copy. The
        # CLI resolves the active agent from the session companion metadata.
        if platform == "win32":
            push_cmd = "python -m observal_cli.hooks.session_push --harness kiro"
        else:
            push_cmd = "python3 -m observal_cli.hooks.session_push --harness kiro"
        v1_hooks: list[dict] = [
            {
                "name": f"observal-session-push-{trigger.lower()}",
                "trigger": trigger,
                "action": {"type": "command", "command": push_cmd},
            }
            for trigger in ("UserPromptSubmit", "Stop")
        ]

        kiro_spec = HARNESS_REGISTRY["kiro"]
        kiro_scope = options.get("scope", kiro_spec["default_scope"])

        # Determine scope-aware hooks dir
        hooks_dir = "~/.kiro/hooks" if kiro_scope == "user" else ".kiro/hooks"

        # Merge custom hook components
        for index, hc in enumerate(hook_configs):
            event = hc.get("event")
            if not event:
                continue
            trigger = _KIRO_V1_TRIGGER_MAP.get(event, event)
            handler_type = hc.get("handler_type", "command")
            handler_config = hc.get("handler_config", {})
            if handler_type == "command":
                cmd = handler_config.get("command", "")
                script_filename = hc.get("script_filename")
                if not cmd and script_filename:
                    cmd = f"{hooks_dir}/{script_filename}"
                elif not cmd:
                    continue
                elif script_filename:
                    cmd = f"{hooks_dir}/{script_filename}"
            elif handler_type == "http":
                url = handler_config.get("url", "")
                if not url:
                    continue
                cmd = f"curl -s -X POST -H 'Content-Type: application/json' -d @- {url}"
            else:
                continue
            entry: dict = {
                "name": hc.get("name") or f"{safe_name}-{trigger.lower()}-{index}",
                "trigger": trigger,
                "action": {"type": "command", "command": cmd},
            }
            # v1 matchers are regexes and are only evaluated for some triggers;
            # the legacy "*" wildcard means "always fire", i.e. no matcher.
            matcher = handler_config.get("matcher")
            if matcher and matcher != "*" and trigger in _KIRO_V1_MATCHER_TRIGGERS:
                entry["matcher"] = matcher
            v1_hooks.append(entry)

        agent_path = kiro_spec["agent_profile"][kiro_scope].format(name=safe_name)
        kiro_model = options.get("_resolved_model", None)

        content = {
            "name": safe_name,
            "description": (ctx.agent.description or safe_name).replace("\n", " ").strip()[:200],
            "prompt": _wrap_kiro_prompt(ctx.agent.prompt, safe_name),
            "mcpServers": mcp_configs,
            "tools": ["*"],
            "toolAliases": {},
            # No "allowedTools"/"toolsSettings": Kiro IDE 1.x drops any JSON agent
            # profile carrying those CLI-only fields unless "permissions" is also
            # present (ProfileLoader -> reasonCode "cli_only_agent"), which made
            # every pulled agent invisible in the IDE agent picker. Both fields
            # were removed in the V3 agent schema anyway; permissions replaces them.
            "resources": [
                "file://AGENTS.md",
                "file://README.md",
                "skill://.kiro/skills/**/SKILL.md",
                "skill://~/.kiro/skills/**/SKILL.md",
            ],
            "includeMcpJson": True,
            "model": kiro_model,
        }
        result: dict = {
            "agent_profile": {"path": agent_path, "content": content},
            "hooks_config": {
                "path": f"{hooks_dir}/observal.json",
                "content": {"version": "v1", "hooks": v1_hooks},
            },
            "scope": kiro_scope,
        }

        if skill_configs:
            result["skill_components"] = skill_configs

        kiro_hook_files = _collect_hook_script_files(hook_configs, ctx.hook_listings, "kiro")
        if kiro_hook_files:
            # Fix paths for user scope
            if kiro_scope == "user":
                for hf in kiro_hook_files:
                    hf["path"] = hf["path"].replace(".kiro/hooks", "~/.kiro/hooks", 1)
            result["hook_files"] = kiro_hook_files

        warnings_combined = list(ctx.compatibility_warnings)
        warnings_combined.extend(options.get("_model_warnings") or [])
        if warnings_combined:
            result["_warnings"] = warnings_combined

        return result


# Auto-register on import
register_adapter(KiroAdapter())
