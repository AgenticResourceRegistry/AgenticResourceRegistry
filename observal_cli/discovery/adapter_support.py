# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Shared rich-discovery parsing helpers for harness adapters."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from observal_cli.discovery.bounded_walk import AggregateDiscoveryBudget, BoundedWalker, WalkLimits
from observal_cli.discovery.models import (
    AdapterDiscoveryResult,
    DiagnosticCode,
    DiscoveredComponent,
    DiscoveryEvidence,
    DiscoveryScope,
)
from observal_cli.discovery.normalize import normalize_mcp_definition
from observal_cli.discovery.redact import redact_arguments, redact_text, redact_value, sanitize_url
from observal_cli.discovery.serialize import privacy_safe_path
from observal_cli.harness import DiscoveredAgent, DiscoveredHook, DiscoveredMcp, DiscoveredSkill
from observal_cli.shared.utils import extract_body, extract_mcp_servers, first_content_line, parse_frontmatter_field

if TYPE_CHECKING:
    from pathlib import Path


def _description_line(content: str) -> str:
    description = first_content_line(content)
    if description:
        return description
    if content.startswith("---"):
        return ""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:200]
    return ""


class RichAdapterScanner:
    """Build per-file evidence while keeping raw configuration inside adapters."""

    def __init__(
        self,
        *,
        harness: str,
        scope: DiscoveryScope,
        root: Path,
        home: Path | None = None,
        project_dir: Path | None = None,
        limits: WalkLimits | None = None,
        budget: AggregateDiscoveryBudget | None = None,
        deadline: float | None = None,
    ) -> None:
        self.harness = harness
        self.scope = scope
        self.root = root
        self.home = home
        self.project_dir = project_dir
        self.walker = BoundedWalker(root, provider=harness, limits=limits, budget=budget, deadline=deadline)
        self.result = AdapterDiscoveryResult()

    def diagnostic(self, code: DiagnosticCode, path: Path, message: str) -> None:
        self.walker.diagnostic(code, message, source=path)

    def read_json(self, path: Path) -> Mapping[str, Any] | None:
        content = self.walker.read_text(path)
        if content is None:
            return None
        try:
            value = json.loads(content)
        except json.JSONDecodeError:
            self.diagnostic(DiagnosticCode.METADATA_MALFORMED, path, "malformed JSON discovery metadata")
            return None
        if not isinstance(value, Mapping):
            self.diagnostic(DiagnosticCode.METADATA_MALFORMED, path, "discovery metadata must be a JSON object")
            return None
        return value

    def add_component(self, component: DiscoveredComponent, path: Path, *, launch=None) -> None:
        if self.walker.stopped:
            return
        budget = self.walker.budget
        if budget.evidence >= budget.max_evidence:
            self.walker.limit(DiagnosticCode.EVIDENCE_LIMIT_REACHED, "aggregate discovery evidence limit reached")
            self.walker.halt()
            return
        budget.evidence += 1
        self.result.evidence.append(
            DiscoveryEvidence(
                component=component,
                scope=self.scope,
                harness=self.harness,
                source_path=path,
                display_path=privacy_safe_path(path, home=self.home, project_dir=self.project_dir),
                launch=launch,
            )
        )

    def add_mcp_config(
        self,
        path: Path,
        *,
        source: str,
        description_prefix: str,
        description: str | None = None,
    ) -> None:
        data = self.read_json(path)
        if data is None:
            return
        try:
            # An explicit mcpServers value is passed through so add_mcps reports a wrong type.
            servers = data["mcpServers"] if "mcpServers" in data else extract_mcp_servers(dict(data))
        except (AttributeError, TypeError):
            self.diagnostic(DiagnosticCode.METADATA_MALFORMED, path, "MCP configuration has an unsupported shape")
            return
        self.add_mcps(
            servers,
            path,
            source=source,
            description_prefix=description_prefix,
            description=description,
        )

    def add_mcps(
        self,
        servers: object,
        path: Path,
        *,
        source: str,
        description_prefix: str,
        description: str | None = None,
    ) -> None:
        if not isinstance(servers, Mapping):
            self.diagnostic(DiagnosticCode.METADATA_MALFORMED, path, "MCP server collection must be an object")
            return
        for raw_name in sorted(servers, key=lambda value: str(value).casefold()):
            config = servers[raw_name]
            if not isinstance(config, Mapping):
                self.diagnostic(DiagnosticCode.METADATA_MALFORMED, path, "MCP server entry must be an object")
                continue
            name = redact_text(str(raw_name))
            command_value = config.get("command")
            command = redact_text(str(command_value)) if command_value is not None else None
            raw_args = config.get("args", [])
            if not isinstance(raw_args, list):
                self.diagnostic(DiagnosticCode.METADATA_MALFORMED, path, "MCP arguments must be an array")
                continue
            args, _ = redact_arguments([str(item) for item in raw_args])
            url_value = config.get("url")
            url = sanitize_url(str(url_value)) if url_value is not None else None
            normalized = normalize_mcp_definition(
                config,
                source_root=self.root,
                working_dir=path.parent,
            )
            component = DiscoveredMcp(
                name=name,
                command=command,
                args=list(args),
                url=url,
                description=redact_text(description or f"{description_prefix}: {name}"),
                source=source,
            )
            if normalized.reason in {
                "malformed_environment",
                "ambiguous_environment_metadata",
                "malformed_headers",
                "ambiguous_header_metadata",
                "malformed_transport",
                "ambiguous_transport",
            }:
                self.diagnostic(
                    DiagnosticCode.METADATA_MALFORMED,
                    path,
                    f"MCP server {name!r} has malformed launch metadata",
                )
            elif not normalized.complete:
                self.diagnostic(
                    DiagnosticCode.UNSUPPORTED_LAUNCH,
                    path,
                    f"MCP server {name!r} has an unsupported or incomplete launch",
                )
            self.add_component(component, path, launch=normalized.launch if normalized.complete else None)

    def add_skills(self, directory: Path, *, source: str, prefix: str = "Skill") -> None:
        for path in self.walker.files(directory, name="SKILL.md"):
            content = self.walker.read_text(path)
            if content is None:
                continue
            name = redact_text(path.parent.name)
            description = parse_frontmatter_field(content, "description") or _description_line(content)
            task_type = parse_frontmatter_field(content, "task_type") or "general"
            component = DiscoveredSkill(
                name=name,
                description=redact_text(description or f"{prefix}: {name}"),
                source=source,
                task_type=redact_text(task_type),
            )
            self.add_component(component, path)

    def add_markdown_agents(self, directory: Path, *, source_prefix: str, recursive: bool = False) -> None:
        paths = self.walker.files(directory, suffix=".md")
        for path in paths:
            if not recursive and path.parent.resolve(strict=False) != directory.resolve(strict=False):
                continue
            content = self.walker.read_text(path)
            if content is None:
                continue
            name = redact_text(path.stem)
            description = parse_frontmatter_field(content, "description") or _description_line(content)
            model = parse_frontmatter_field(content, "model") or ""
            component = DiscoveredAgent(
                name=name,
                description=redact_text(description or f"{source_prefix}: {name}"),
                model_name=redact_text(model),
                prompt=redact_text(extract_body(content)),
                source_file=str(path),
            )
            self.add_component(component, path)

    def add_agent_document(self, path: Path, *, name: str) -> None:
        content = self.walker.read_text(path)
        if content is None or not content.strip():
            return
        component = DiscoveredAgent(
            name=redact_text(name),
            description=redact_text(_description_line(content) or f"Agent: {name}"),
            model_name="",
            prompt=redact_text(content),
            source_file=str(path),
        )
        self.add_component(component, path)

    def add_hooks_mapping(self, path: Path, hooks: object, *, name_prefix: str, source: str) -> None:
        if not isinstance(hooks, Mapping):
            self.diagnostic(DiagnosticCode.METADATA_MALFORMED, path, "hook collection must be an object")
            return
        for event_value in sorted(hooks, key=lambda value: str(value).casefold()):
            entries = hooks[event_value]
            if not isinstance(entries, list):
                self.diagnostic(DiagnosticCode.METADATA_MALFORMED, path, "hook event handlers must be an array")
                continue
            handler: Mapping[str, Any] = {}
            if entries and isinstance(entries[0], Mapping):
                first = entries[0]
                inner = first.get("hooks", [first])
                if isinstance(inner, list) and inner and isinstance(inner[0], Mapping):
                    handler = inner[0]
            event = redact_text(str(event_value))
            safe_handler = redact_value(handler)
            handler_type = str(safe_handler.get("type", "command")) if isinstance(safe_handler, dict) else "command"
            self.add_component(
                DiscoveredHook(
                    name=f"{name_prefix}/{event}",
                    event=event,
                    handler_type=redact_text(handler_type),
                    handler_config=safe_handler if isinstance(safe_handler, dict) else {},
                    description=f"Hook from {name_prefix}: {event}",
                    source=source,
                ),
                path,
            )

    def finish(self) -> AdapterDiscoveryResult:
        self.result.diagnostics.extend(self.walker.diagnostics)
        self.result.evidence.sort(
            key=lambda item: (
                item.display_path or "",
                str(getattr(item.component, "name", "")).casefold(),
                type(item.component).__name__,
            )
        )
        self.result.diagnostics.sort(key=lambda item: (item.source or "", item.code.value, item.message))
        return self.result
