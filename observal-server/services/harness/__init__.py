# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Harness adapter protocol, context, and registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ConfigContext:
    """Shared pre-computed data passed to each harness adapter."""

    agent: Any
    safe_name: str
    harness: str
    observal_url: str
    mcp_configs: dict = field(default_factory=dict)
    rules_content: str = ""
    skill_configs: list = field(default_factory=list)
    hook_configs: list = field(default_factory=list)
    options: dict = field(default_factory=dict)
    platform: str = ""
    compatibility_warnings: list = field(default_factory=list)
    mcp_listings: dict | None = None
    hook_listings: dict | None = None
    skill_listings: dict | None = None
    sandbox_listings: dict | None = None
    prompt_listings: dict | None = None
    component_names: dict | None = None


@dataclass
class McpConfigContext:
    """Normalized MCP data formatted by a harness adapter."""

    name: str
    command: str
    args: list[str]
    server_env: dict[str, str]
    headers: dict[str, str]
    transport: str
    url: str | None
    auto_approve: list[str]

    def standard_entry(self) -> dict:
        """Return the common MCP entry used by JSON-based harnesses."""
        if self.url:
            entry: dict = {"type": self.transport or "sse", "url": self.url}
            if self.headers:
                entry["headers"] = self.headers
            if self.server_env:
                entry["env"] = self.server_env
            if self.auto_approve:
                entry.update({"autoApprove": self.auto_approve, "disabled": False})
            return entry
        entry = {"command": self.command, "args": self.args, "env": self.server_env}
        if self.auto_approve:
            entry.update({"autoApprove": self.auto_approve, "disabled": False})
        return entry


@runtime_checkable
class HarnessAdapter(Protocol):
    """Protocol defining harness-specific configuration behavior."""

    @property
    def harness_name(self) -> str: ...

    def format_config(self, ctx: ConfigContext) -> dict: ...

    def format_mcp_config(self, ctx: McpConfigContext) -> dict: ...

    def agent_mcp_entry(self, ctx: McpConfigContext) -> dict | None: ...

    def format_model(self, model: str, provider: str) -> str: ...

    def default_model_candidate(self, model_name: str | None) -> str | None: ...

    def format_hook_install_snippet(self, event: str, handler_type: str, command: str, timeout: int | None) -> dict: ...

    def hook_install_notes(self) -> list[str]: ...

    def format_hook_telemetry(self, hook_listing: Any, server_url: str, platform: str) -> dict: ...

    def skill_hook_extra(self) -> dict: ...

    def skill_frontmatter_extra(self, slash_command: str | None) -> dict: ...

    def format_hook_component(self, command: str) -> dict: ...

    def emits_prompt_files(self) -> bool: ...


class BaseHarnessAdapter:
    """Shared defaults for harness adapters."""

    def format_mcp_config(self, ctx: McpConfigContext) -> dict:
        return {"mcpServers": {ctx.name: ctx.standard_entry()}}

    def agent_mcp_entry(self, ctx: McpConfigContext) -> dict:
        return ctx.standard_entry()

    def format_model(self, model: str, provider: str) -> str:
        return model

    def default_model_candidate(self, model_name: str | None) -> str | None:
        return None

    def format_hook_install_snippet(self, event: str, handler_type: str, command: str, timeout: int | None) -> dict:
        entry: dict = {"command": command}
        if timeout:
            entry["timeout"] = timeout
        return {"hooks": {event: [entry]}}

    def hook_install_notes(self) -> list[str]:
        return []

    def format_hook_telemetry(self, hook_listing: Any, server_url: str, platform: str) -> dict:
        return {"comment": f"harness '{self.harness_name}' requires manual hook setup. See Observal docs."}

    def skill_hook_extra(self) -> dict:
        return {}

    def skill_frontmatter_extra(self, slash_command: str | None) -> dict:
        return {}

    def format_hook_component(self, command: str) -> dict:
        return {"command": command}

    def emits_prompt_files(self) -> bool:
        return False


# ── Adapter Registry ──────────────────────────────────────────────

_ADAPTER_REGISTRY: dict[str, HarnessAdapter] = {}


def register_adapter(adapter: HarnessAdapter) -> None:
    """Register a harness adapter instance."""
    _ADAPTER_REGISTRY[adapter.harness_name] = adapter


def get_adapter(harness: str) -> HarnessAdapter | None:
    """Look up an adapter by harness name. Returns None if not registered."""
    return _ADAPTER_REGISTRY.get(harness)


def get_all_adapters() -> dict[str, HarnessAdapter]:
    """Return all registered adapters."""
    return dict(_ADAPTER_REGISTRY)


def ensure_loaded() -> None:
    """Import every adapter exactly once."""
    from observal_shared.harness_registry import HARNESS_REGISTRY

    if len(_ADAPTER_REGISTRY) < len(HARNESS_REGISTRY):
        import services.harness.load_all  # noqa: F401


# ── Orchestrator ──────────────────────────────────────────────────


def generate_agent_config(
    agent: Any,
    harness: str,
    observal_url: str = "http://localhost:8000",
    mcp_listings: dict | None = None,
    component_names: dict | None = None,
    env_values: dict | None = None,
    header_values: dict | None = None,
    options: dict | None = None,
    platform: str = "",
    skill_listings: dict | None = None,
    hook_listings: dict | None = None,
    prompt_listings: dict | None = None,
    sandbox_listings: dict | None = None,
    skill_folder_names: dict | None = None,
) -> dict:
    """Generate harness-specific config for an agent.

    This is the single entry point for all harness config generation.
    It builds a shared ConfigContext, resolves the adapter, and delegates.
    """
    ensure_loaded()
    from services.harness.helpers import (
        _build_delegation_mcp_entry,
        _build_hook_configs,
        _build_mcp_configs,
        _build_rules_content,
        _build_sandbox_mcp_entry,
        _build_skill_configs,
        _check_harness_compatibility,
        _sanitize_name,
    )
    from services.shared.utils import registry_item_slug

    adapter = get_adapter(harness)
    if adapter is None:
        raise ValueError(f"No adapter registered for harness: {harness!r}")

    options = options or {}
    safe_name = _sanitize_name(options.get("local_name") or registry_item_slug(agent))
    mcp_configs = _build_mcp_configs(
        agent, harness, observal_url, mcp_listings=mcp_listings, env_values=env_values, header_values=header_values
    )

    if sandbox_listings:
        sandbox_mcp = _build_sandbox_mcp_entry(sandbox_listings, harness)
        if sandbox_mcp:
            mcp_configs.update(sandbox_mcp)

    if options.get("_delegation"):
        mcp_configs.update(_build_delegation_mcp_entry(harness))

    # Harnesses with first-class prompt files keep the agent body to a name list.
    emit_prompt_files = adapter.emits_prompt_files()
    rules_content = _build_rules_content(
        agent,
        component_names,
        None if emit_prompt_files else prompt_listings,
        sandbox_listings,
    )
    skill_configs = _build_skill_configs(agent, skill_listings, folder_names=skill_folder_names)
    hook_configs = _build_hook_configs(agent, hook_listings)
    compatibility_warnings = _check_harness_compatibility(agent, harness)

    ctx = ConfigContext(
        agent=agent,
        safe_name=safe_name,
        harness=harness,
        observal_url=observal_url,
        mcp_configs=mcp_configs,
        rules_content=rules_content,
        skill_configs=skill_configs,
        hook_configs=hook_configs,
        options=options,
        platform=platform,
        compatibility_warnings=compatibility_warnings,
        mcp_listings=mcp_listings,
        hook_listings=hook_listings,
        skill_listings=skill_listings,
        sandbox_listings=sandbox_listings,
        prompt_listings=prompt_listings,
        component_names=component_names,
    )
    return adapter.format_config(ctx)


async def generate_all_harness_configs(
    agent_version: Any,
    agent: Any,
    target_harnesses: list[str] | None = None,
    observal_url: str = "http://localhost:8000",
    mcp_listings: dict | None = None,
    skill_listings: dict | None = None,
    hook_listings: dict | None = None,
    component_names: dict | None = None,
    env_values: dict | None = None,
) -> dict[str, dict[str, str]]:
    """Generate harness config files for all target harnesses from an AgentVersion."""
    import json as _json

    from observal_shared.harness_registry import HARNESS_REGISTRY

    harnesses = target_harnesses or agent_version.supported_harnesses or list(HARNESS_REGISTRY.keys())
    result = {}

    for harness in harnesses:
        if harness not in HARNESS_REGISTRY:
            continue
        config = generate_agent_config(
            agent=agent,
            harness=harness,
            observal_url=observal_url,
            mcp_listings=mcp_listings,
            skill_listings=skill_listings,
            hook_listings=hook_listings,
            component_names=component_names,
            env_values=env_values,
        )

        files = {}
        if "agent_profile" in config:
            af = config["agent_profile"]
            content = af["content"]
            files[af["path"]] = _json.dumps(content, indent=2) if isinstance(content, dict) else content
        if "mcp_config" in config:
            mc = config["mcp_config"]
            if isinstance(mc, dict) and "path" in mc:
                content = mc["content"]
                files[mc["path"]] = _json.dumps(content, indent=2) if isinstance(content, dict) else content
        if "skills" in config:
            for sf in config["skills"]:
                files[sf["path"]] = sf["content"]

        if files:
            result[harness] = {"files": files}

    return result
