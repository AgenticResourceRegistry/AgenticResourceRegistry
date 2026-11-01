# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Per-kind projection adapters.

Each adapter turns one native (listing, version) pair into the neutral
``Projected`` shape the index stores and the artifact endpoint serves. This is
the only place that knows native column names; everything downstream works on
``Projected``.

Capabilities and representative queries are *derived* here from native
metadata and are flagged as such. Publisher-authored queries are a later
addition; derived text must never be presented as a publisher claim.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from models.agent import Agent, AgentVersion
from models.discovery_entry import DiscoveryKind
from models.hook import HookListing, HookVersion
from models.mcp import McpListing, McpVersion
from models.prompt import PromptListing, PromptVersion
from models.sandbox import SandboxListing, SandboxVersion
from models.skill import SkillListing, SkillVersion
from services.skill_bundle import needs_bundle_delivery

MAX_QUERIES = 5
MAX_CAPABILITIES = 24
MAX_DESCRIPTION_QUERY_LEN = 120


@dataclass(slots=True)
class Artifact:
    """Bytes served by the permanent artifact endpoint, with their media type."""

    content: bytes
    content_type: str

    @property
    def digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.content).hexdigest()

    @property
    def digest_header(self) -> str:
        """RFC 3230 ``Digest`` header value (base64 of the raw digest)."""
        return "sha-256=" + base64.b64encode(hashlib.sha256(self.content).digest()).decode("ascii")


@dataclass(slots=True)
class Projected:
    display_name: str
    description: str
    capabilities: list[str]
    representative_queries: list[str]
    tags: list[str]
    supported_harnesses: list[str]
    artifact: Artifact
    activatable: bool = True
    extra: dict[str, Any] = field(default_factory=dict)  # additional obs:* terms


# ── Helpers ──────────────────────────────────────────────────────────────


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _first_sentence(text: str) -> str:
    text = _clean(text)
    if not text:
        return ""
    sentence = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    return sentence[:MAX_DESCRIPTION_QUERY_LEN].rstrip(" ,;:.")


def _humanize(token: str | None) -> str:
    return _clean((token or "").replace("_", " ").replace("-", " ")).lower()


def _dedupe(values: list[str], limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean(value)
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
        if len(out) >= limit:
            break
    return out


def _queries(*candidates: str) -> list[str]:
    """Build 2-5 representative queries, always including a description-derived one."""
    return _dedupe([c for c in candidates if c], MAX_QUERIES)


def _json_artifact(payload: dict[str, Any], content_type: str) -> Artifact:
    body = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return Artifact(content=body, content_type=content_type)


def _env_names(values: list | None) -> list[str]:
    """Environment variable *names* only. Values are never part of an artifact."""
    names: list[str] = []
    for item in values or []:
        name = item.get("name") or item.get("key") if isinstance(item, dict) else str(item).split("=", 1)[0]
        if name:
            names.append(str(name))
    return names


# ── Adapters ─────────────────────────────────────────────────────────────


def project_skill(listing: SkillListing, version: SkillVersion) -> Projected:
    description = _clean(version.description)
    task = _humanize(version.task_type)
    capabilities = _dedupe(
        [task, version.slash_command or "", *(f"harness:{h}" for h in version.supported_harnesses or [])],
        MAX_CAPABILITIES,
    )
    queries = _queries(
        _first_sentence(description),
        f"help me with {task}" if task else "",
        f"use the {listing.name} skill",
    )
    if version.skill_md_content:
        body = version.skill_md_content
    else:
        # No inline content: publish a minimal skill document that points at the source.
        # Every value is JSON-quoted (valid YAML scalars) so a newline or colon in
        # a source URL or ref cannot add front-matter keys or break the document.
        body = (
            "---\n"
            f"name: {json.dumps(listing.slug)}\n"
            f"description: {json.dumps(description)}\n"
            f"source: {json.dumps(version.git_url or '')}\n"
            f"ref: {json.dumps(version.git_ref or '')}\n"
            f"path: {json.dumps(version.skill_path or '/')}\n"
            "---\n\n"
            f"# {_clean(listing.name)}\n\n{description}\n"
        )
    return Projected(
        display_name=listing.name,
        description=description,
        capabilities=capabilities,
        representative_queries=queries,
        tags=_dedupe(["skill", task, listing.namespace], 12),
        supported_harnesses=list(version.supported_harnesses or []),
        artifact=Artifact(body.encode("utf-8"), "text/markdown; charset=utf-8"),
        activatable=not needs_bundle_delivery(version),
        extra={
            "obs:taskType": version.task_type,
            "obs:deliveryMode": version.delivery_mode,
            "obs:artifactScope": "preview" if needs_bundle_delivery(version) else "complete",
        },
    )


def _mcp_tool_names(tools_schema: dict | list | None) -> list[tuple[str, str]]:
    """Return (name, description) pairs from whatever shape tools_schema took."""
    tools: list[Any]
    if isinstance(tools_schema, dict):
        tools = tools_schema.get("tools") or tools_schema.get("items") or []
        if not tools and all(isinstance(v, dict) for v in tools_schema.values()):
            tools = [{"name": k, **v} for k, v in tools_schema.items()]
    elif isinstance(tools_schema, list):
        tools = tools_schema
    else:
        tools = []
    out: list[tuple[str, str]] = []
    for tool in tools:
        if isinstance(tool, dict) and tool.get("name"):
            out.append((str(tool["name"]), _clean(str(tool.get("description") or ""))))
        elif isinstance(tool, str):
            out.append((tool, ""))
    return out


def project_mcp(listing: McpListing, version: McpVersion) -> Projected:
    description = _clean(version.description)
    tools = _mcp_tool_names(version.tools_schema)
    capabilities = _dedupe([name for name, _ in tools] or [_humanize(listing.category)], MAX_CAPABILITIES)
    tool_queries = [desc for _, desc in tools if desc][:2]
    queries = _queries(
        _first_sentence(description),
        *(_first_sentence(q) for q in tool_queries),
        f"connect to {listing.name}",
    )
    card: dict[str, Any] = {
        "name": listing.name,
        "description": description,
        "version": version.version,
        "transport": version.transport,
        "command": version.command,
        "args": list(version.args or []),
        "url": version.url,
        "environmentVariables": _env_names(version.environment_variables),
        "tools": [{"name": n, "description": d} for n, d in tools],
        "sourceUrl": version.source_url,
    }
    return Projected(
        display_name=listing.name,
        description=description,
        capabilities=capabilities,
        representative_queries=queries,
        tags=_dedupe(["mcp", _humanize(listing.category), listing.namespace], 12),
        supported_harnesses=list(version.supported_harnesses or []),
        artifact=_json_artifact(card, "application/mcp-server-card+json"),
        extra={
            "obs:category": listing.category,
            "obs:transport": version.transport,
            "obs:requiresCredentials": bool(_env_names(version.environment_variables)),
        },
    )


def project_prompt(listing: PromptListing, version: PromptVersion) -> Projected:
    description = _clean(version.description)
    variables = [str(v.get("name") if isinstance(v, dict) else v) for v in (version.variables or [])]
    capabilities = _dedupe([_humanize(version.category), *(f"variable:{v}" for v in variables)], MAX_CAPABILITIES)
    queries = _queries(
        _first_sentence(description), f"prompt for {_humanize(version.category)}", f"render {listing.name}"
    )
    payload = {
        "name": listing.name,
        "description": description,
        "version": version.version,
        "category": version.category,
        "template": version.template,
        "variables": list(version.variables or []),
        "modelHints": version.model_hints or {},
        "tags": list(version.tags or []),
    }
    return Projected(
        display_name=listing.name,
        description=description,
        capabilities=capabilities,
        representative_queries=queries,
        tags=_dedupe(["prompt", _humanize(version.category), *(version.tags or []), listing.namespace], 12),
        supported_harnesses=list(version.supported_harnesses or []),
        artifact=_json_artifact(payload, "application/vnd.observal.prompt+json"),
        extra={"obs:category": version.category, "obs:variables": variables},
    )


def project_hook(listing: HookListing, version: HookVersion) -> Projected:
    description = _clean(version.description)
    capabilities = _dedupe([f"event:{version.event}", f"handler:{version.handler_type}"], MAX_CAPABILITIES)
    queries = _queries(
        _first_sentence(description),
        f"run something on {_humanize(version.event)}",
        f"install the {listing.name} hook",
    )
    payload = {
        "name": listing.name,
        "description": description,
        "version": version.version,
        "event": version.event,
        "executionMode": version.execution_mode,
        "priority": version.priority,
        "handlerType": version.handler_type,
        "handlerConfig": version.handler_config or {},
        "scope": version.scope,
        "toolFilter": version.tool_filter,
        "scriptFilename": version.script_filename,
        "scriptContent": version.script_content,
        "requirements": list(version.requirements or []),
        "source": {"url": version.source_url, "ref": version.source_ref, "path": version.source_path},
        "resolvedSha": version.resolved_sha,
    }
    return Projected(
        display_name=listing.name,
        description=description,
        capabilities=capabilities,
        representative_queries=queries,
        tags=_dedupe(["hook", _humanize(version.event), listing.namespace], 12),
        supported_harnesses=list(version.supported_harnesses or []),
        artifact=_json_artifact(payload, "application/vnd.observal.hook+json"),
        # Hooks change lifecycle behaviour outside the conversation: explicit install only.
        activatable=False,
        extra={"obs:event": version.event, "obs:handlerType": version.handler_type},
    )


def project_sandbox(listing: SandboxListing, version: SandboxVersion) -> Projected:
    description = _clean(version.description)
    capabilities = _dedupe(
        [f"runtime:{version.runtime_type}", f"network:{version.network_policy}", version.image.split(":")[0]],
        MAX_CAPABILITIES,
    )
    queries = _queries(
        _first_sentence(description),
        f"run code in an isolated {_humanize(version.runtime_type)} environment",
        f"use the {listing.name} sandbox",
    )
    payload = {
        "name": listing.name,
        "description": description,
        "version": version.version,
        "runtimeType": version.runtime_type,
        "image": version.image,
        "resourceLimits": version.resource_limits or {},
        "networkPolicy": version.network_policy,
        "entrypoint": version.entrypoint,
        "runtimeConfig": version.runtime_config or {},
        "source": {"url": version.source_url, "ref": version.source_ref, "path": version.sandbox_path},
        "resolvedSha": version.resolved_sha,
    }
    return Projected(
        display_name=listing.name,
        description=description,
        capabilities=capabilities,
        representative_queries=queries,
        tags=_dedupe(["sandbox", _humanize(version.runtime_type), listing.namespace], 12),
        supported_harnesses=list(version.supported_harnesses or []),
        artifact=_json_artifact(payload, "application/vnd.observal.sandbox+json"),
        extra={"obs:runtimeType": version.runtime_type, "obs:networkPolicy": version.network_policy},
    )


def project_agent(agent: Agent, version: AgentVersion) -> Projected:
    description = _clean(version.description)
    components = sorted(version.components or [], key=lambda c: (c.order_index, c.component_name))
    component_names = [c.component_name for c in components if c.component_name]
    capabilities = _dedupe(
        [
            *(f"{c.component_type}:{c.component_name}" for c in components if c.component_name),
            *version.required_capabilities,
        ],
        MAX_CAPABILITIES,
    )
    queries = _queries(_first_sentence(description), f"use the {agent.name} agent", _first_sentence(version.prompt))
    harnesses = list(version.supported_harnesses or version.inferred_supported_harnesses or [])
    payload = {
        "name": agent.name,
        "qualifiedName": f"{agent.namespace}/{agent.slug}",
        "description": description,
        "version": version.version,
        "prompt": version.prompt,
        "model": version.model_name,
        "modelsByHarness": version.models_by_harness or {},
        "components": [
            {
                "type": c.component_type,
                "name": c.component_name,
                "id": str(c.component_id),
                "version": c.resolved_version,
            }
            for c in components
        ],
        "externalMcps": [
            {k: v for k, v in (m or {}).items() if k != "env"} | {"env": _env_names((m or {}).get("env"))}
            for m in (version.external_mcps or [])
            if isinstance(m, dict)
        ],
        "supportedHarnesses": harnesses,
        "requiredCapabilities": list(version.required_capabilities or []),
        "yamlSnapshot": version.yaml_snapshot,
    }
    return Projected(
        display_name=agent.name,
        description=description,
        capabilities=capabilities,
        representative_queries=queries,
        tags=_dedupe(["agent", *component_names, agent.namespace], 12),
        supported_harnesses=harnesses,
        artifact=_json_artifact(payload, "application/vnd.observal.agent+json"),
        extra={"obs:componentCount": len(components), "obs:model": version.model_name},
    )


# ── Registry ─────────────────────────────────────────────────────────────

Adapter = Callable[[Any, Any], Projected]

ADAPTERS: dict[DiscoveryKind, Adapter] = {
    DiscoveryKind.skill: project_skill,
    DiscoveryKind.mcp: project_mcp,
    DiscoveryKind.prompt: project_prompt,
    DiscoveryKind.hook: project_hook,
    DiscoveryKind.sandbox: project_sandbox,
    DiscoveryKind.agent: project_agent,
}

# (listing model, version model, FK column on the version pointing at the listing)
NATIVE_MODELS: dict[DiscoveryKind, tuple[type, type, str]] = {
    DiscoveryKind.skill: (SkillListing, SkillVersion, "listing_id"),
    DiscoveryKind.mcp: (McpListing, McpVersion, "listing_id"),
    DiscoveryKind.prompt: (PromptListing, PromptVersion, "listing_id"),
    DiscoveryKind.hook: (HookListing, HookVersion, "listing_id"),
    DiscoveryKind.sandbox: (SandboxListing, SandboxVersion, "listing_id"),
    DiscoveryKind.agent: (Agent, AgentVersion, "agent_id"),
}

NATIVE_KINDS: tuple[DiscoveryKind, ...] = tuple(NATIVE_MODELS)
