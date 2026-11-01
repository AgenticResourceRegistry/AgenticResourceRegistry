# SPDX-FileCopyrightText: 2026 RAWx18 <rawx18.dev@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Goose harness adapter.

Discovery surfaces, resolved through ``observal_cli.shared.utils`` so that
``GOOSE_PATH_ROOT``, XDG overrides, and Windows layouts are all honoured:

  MCP servers  ``extensions:`` in ``<config-dir>/config.yaml``
  Skills       ``<agents-home>/skills/<name>/SKILL.md`` and ``.agents/skills/...``
  Agents       ``<agents-home>/agents/<name>.md`` and ``.agents/agents/...``
  Hooks        ``<agents-home>/plugins/<plugin>/hooks/hooks.json`` and project plugins

Sessions live in a SQLite database, so ``observal_cli.sessions.goose`` projects
them onto append-only JSONL mirrors that the shared delivery engine consumes.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import yaml

from observal_cli.discovery.adapter_support import RichAdapterScanner
from observal_cli.discovery.models import AdapterDiscoveryResult, DiagnosticCode, DiscoveryScope
from observal_cli.discovery.redact import redact_text, redact_value
from observal_cli.harness import (
    DiscoveredAgent,
    DiscoveredHook,
    DiscoveredMcp,
    DiscoveredSkill,
    HookSpec,
    ScanResult,
    SessionSource,
    register_adapter,
)
from observal_cli.harness.base import BaseAdapter
from observal_cli.shared.utils import (
    first_content_line,
    parse_frontmatter_field,
    resolve_goose_agents_home,
    resolve_goose_config_dir,
    resolve_goose_data_dir,
)

# goose extension types backed by an MCP server Observal can describe. `builtin`,
# `platform` and `frontend` are in-process, and `inline_python` carries code, not a command.
_COMMAND_TYPES = frozenset({"stdio"})
_REMOTE_TYPES = frozenset({"streamable_http", "sse"})

_SESSION_PUSH_MODULE = "observal_cli.hooks.session_push"


class GooseAdapter(BaseAdapter):
    """Adapter for goose (CLI and Desktop share the same on-disk state)."""

    home_markers = (".config/goose", ".local/share/goose")
    managed_agent_profiles = ("user:agents/{name}.md", "project:.agents/agents/{name}.md")
    managed_skills = ("user:skills/{name}/SKILL.md", "project:.agents/skills/{name}/SKILL.md")

    @property
    def harness_name(self) -> str:
        return "goose"

    def is_installed(self, home: Path | None = None) -> bool:
        """Detect goose from its config or data directory on any platform."""
        if super().is_installed(home):
            return True
        if home is not None and home != Path.home():
            return False
        return resolve_goose_config_dir().is_dir() or resolve_goose_data_dir().is_dir()

    # ── Sessions ──────────────────────────────────────────────────────

    def resolve_session_source(self, event: dict[str, Any], home: Path | None = None) -> SessionSource | None:
        from observal_cli.sessions.goose import export_session, resolve_session_id

        session_id = resolve_session_id(event, home=home)
        if not session_id:
            return None
        finalize = self.is_session_final(event) or bool(event.get("_observal_lookup_only"))
        mirrored = export_session(session_id, home=home, finalize=finalize)
        if mirrored is None:
            return None
        # goose omits working_dir on SessionStart, UserPromptSubmit and SessionEnd.
        cwd = str(event.get("working_dir") or event.get("cwd") or "") or mirrored.working_dir
        return SessionSource(self.harness_name, session_id, mirrored.path, cwd=cwd)

    def discover_session_sources(
        self,
        home: Path | None = None,
        since_hours: int = 168,
    ) -> list[SessionSource]:
        from observal_cli.sessions.goose import export_session, list_recent_sessions, mirror_path

        cutoff = time.time() - since_hours * 3600
        sources: list[SessionSource] = []
        for session in list_recent_sessions(cutoff, home=home):
            path = mirror_path(session.session_id, home)
            # Re-reading the database is only worth it when goose wrote after the last export.
            if not path.is_file() or path.stat().st_mtime <= session.updated_epoch:
                mirrored = export_session(session.session_id, home=home)
                if mirrored is None:
                    continue
                path = mirrored.path
            sources.append(
                SessionSource(
                    self.harness_name,
                    session.session_id,
                    path,
                    cwd=session.working_dir,
                    parent_session_id=session.parent_session_id,
                )
            )
        return sources

    def related_session_sources(self, source: SessionSource, home: Path | None = None) -> list[SessionSource]:
        """Return delegated (subagent) sessions goose linked to this one."""
        from observal_cli.sessions.goose import export_session, read_child_session_ids

        children: list[SessionSource] = []
        for child_id, working_dir in read_child_session_ids(source.session_id, home=home):
            mirrored = export_session(child_id, home=home)
            if mirrored is None:
                continue
            children.append(
                SessionSource(
                    self.harness_name,
                    child_id,
                    mirrored.path,
                    cwd=working_dir or mirrored.working_dir or source.cwd,
                    parent_session_id=source.session_id,
                )
            )
        return children

    def defer_session_delivery(self) -> bool:
        """goose awaits hooks inside its agent loop, so never block them on network IO."""
        return True

    def is_session_final(self, event: dict[str, Any]) -> bool:
        """Only ``SessionEnd`` closes a goose session; ``Stop`` ends a single turn."""
        return str(event.get("event") or event.get("hook_event_name") or "").lower() == "sessionend"

    # ── Scanning ──────────────────────────────────────────────────────

    def resolve_home_dir(self) -> Path | None:
        """Return the config dir, or the data dir when only that one exists.

        Goose honours ``GOOSE_PATH_ROOT``, XDG and ``%APPDATA%``, so scanning must
        not assume ``~/.config/goose``.
        """
        config_dir = resolve_goose_config_dir()
        if config_dir.is_dir():
            return config_dir
        data_dir = resolve_goose_data_dir()
        return data_dir if data_dir.is_dir() else config_dir

    def scan_home(self, home: Path | None = None) -> ScanResult:
        """Discover MCPs, skills, agents, and hooks from the user's goose setup."""
        config_dir = resolve_goose_config_dir(home)
        agents_root = resolve_goose_agents_home(home)
        if not config_dir.is_dir() and not agents_root.is_dir():
            return ScanResult()
        return ScanResult(
            mcps=self._scan_mcps(config_dir / "config.yaml", "goose:global"),
            skills=self._scan_skills(agents_root / "skills", "goose:skills"),
            hooks=self._scan_plugins(agents_root / "plugins", "goose:plugins"),
            agents=self._scan_agents(agents_root / "agents", "goose:agents"),
        )

    def scan_project(self, project_dir: Path) -> ScanResult:
        """Discover project-scoped skills, agents, and hook plugins under ``.agents/``."""
        agents_root = project_dir / ".agents"
        if not agents_root.is_dir():
            return ScanResult()
        return ScanResult(
            skills=self._scan_skills(agents_root / "skills", "goose:project"),
            hooks=self._scan_plugins(agents_root / "plugins", "goose:project"),
            agents=self._scan_agents(agents_root / "agents", "goose:project"),
        )

    def discover_home(self, home: Path | None = None) -> AdapterDiscoveryResult:
        home = home or Path.home()
        config_dir = resolve_goose_config_dir(home)
        agents_root = resolve_goose_agents_home(home)
        config_scanner = RichAdapterScanner(
            harness=self.harness_name,
            scope=DiscoveryScope.USER,
            root=config_dir,
            home=home,
        )
        self._discover_goose_config(config_scanner, config_dir / "config.yaml", "goose:global")
        result = config_scanner.finish()
        agents_scanner = RichAdapterScanner(
            harness=self.harness_name,
            scope=DiscoveryScope.USER,
            root=agents_root,
            home=home,
        )
        self._discover_goose_components(agents_scanner, agents_root, "goose:global")
        extra = agents_scanner.finish()
        result.evidence.extend(extra.evidence)
        result.diagnostics.extend(extra.diagnostics)
        return result

    def discover_project(self, project_dir: Path) -> AdapterDiscoveryResult:
        root = project_dir / ".agents"
        scanner = RichAdapterScanner(
            harness=self.harness_name,
            scope=DiscoveryScope.PROJECT,
            root=root,
            project_dir=project_dir,
        )
        self._discover_goose_components(scanner, root, "goose:project")
        return scanner.finish()

    def _discover_goose_config(self, scanner: RichAdapterScanner, config_path: Path, source: str) -> None:
        content = scanner.walker.read_text(config_path)
        if content is None:
            return
        try:
            data = yaml.safe_load(content) or {}
        except yaml.YAMLError:
            scanner.diagnostic(DiagnosticCode.METADATA_MALFORMED, config_path, "malformed YAML discovery metadata")
            return
        extensions = data.get("extensions", {}) if isinstance(data, dict) else {}
        servers: dict[str, dict[str, Any]] = {}
        if isinstance(extensions, dict):
            for raw_name, entry in extensions.items():
                if not isinstance(entry, dict):
                    continue
                entry_type = str(entry.get("type") or "")
                if entry_type not in _COMMAND_TYPES | _REMOTE_TYPES:
                    continue
                name = str(entry.get("name") or raw_name)
                server: dict[str, Any] = {
                    "command": entry.get("cmd") or entry.get("command"),
                    "args": entry.get("args") or [],
                    "url": entry.get("uri") or entry.get("url"),
                }
                if entry_type in _REMOTE_TYPES:
                    server["transport"] = entry_type.replace("_", "-")
                servers[name] = server
        scanner.add_mcps(servers, config_path, source=source, description_prefix="Goose extension")

    def _discover_goose_components(self, scanner: RichAdapterScanner, root: Path, source: str) -> None:
        scanner.add_skills(root / "skills", source=source, prefix="Goose skill")
        scanner.add_markdown_agents(root / "agents", source_prefix="Goose agent")
        for hooks_path in scanner.walker.files(root / "plugins", name="hooks.json"):
            if hooks_path.parent.name != "hooks":
                continue
            data = scanner.read_json(hooks_path)
            declared = data.get("hooks", {}) if data is not None else {}
            if not isinstance(declared, dict):
                continue
            plugin_name = redact_text(hooks_path.parent.parent.name)
            for event, rules in declared.items():
                if not isinstance(rules, list):
                    continue
                for rule in rules:
                    if not isinstance(rule, dict):
                        continue
                    handlers = rule.get("hooks", [])
                    if not isinstance(handlers, list):
                        continue
                    for handler in handlers:
                        safe_handler = redact_value(handler)
                        if not isinstance(safe_handler, dict):
                            continue
                        scanner.add_component(
                            DiscoveredHook(
                                name=plugin_name,
                                event=redact_text(str(event)),
                                handler_type=redact_text(str(safe_handler.get("type", "command"))),
                                handler_config=safe_handler,
                                description=f"Goose plugin hook: {plugin_name} ({redact_text(str(event))})",
                                source=source,
                            ),
                            hooks_path,
                        )

    def extract_mcp_servers(self, config: dict) -> dict:
        """Return goose's ``extensions`` map, which is its MCP server registry."""
        extensions = config.get("extensions")
        return extensions if isinstance(extensions, dict) else {}

    # ── Hooks ─────────────────────────────────────────────────────────

    def get_hook_spec(self) -> HookSpec:
        from observal_cli.harness_specs.goose_hooks_spec import GOOSE_HOOK_EVENTS

        return HookSpec(
            events=list(GOOSE_HOOK_EVENTS),
            format="plugin",
            markers=["observal", "OBSERVAL"],
        )

    def generate_hook_config(
        self,
        observal_url: str,
        api_key: str,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        from observal_cli.harness_specs.goose_hooks_spec import build_hooks

        return build_hooks()

    def detect_hooks(self, config_dir: Path) -> str:
        """Report whether the Observal goose hook plugin is installed.

        *config_dir* is treated as an optional ``.agents`` root so callers (and
        tests) can point at a sandbox; the real user plugin location is used as
        the fallback.
        """
        from observal_cli.harness_specs.goose_hooks_spec import GOOSE_HOOK_EVENTS, PLUGIN_NAME, hooks_file

        candidates = [config_dir / "plugins" / PLUGIN_NAME / "hooks" / "hooks.json", hooks_file()]
        path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if path is None:
            return "missing"
        try:
            hooks = json.loads(path.read_text()).get("hooks", {})
        except (json.JSONDecodeError, OSError, AttributeError):
            return "missing"
        installed = sum(1 for event in GOOSE_HOOK_EVENTS if self._event_has_observal_hook(hooks.get(event)))
        if installed == len(GOOSE_HOOK_EVENTS):
            return "installed"
        return "partial" if installed else "missing"

    def patch_hooks(self, dry_run: bool) -> bool:
        from observal_cli.cmd_doctor import _patch_goose

        return _patch_goose(dry_run)

    def rewrite_hooks(self, content: dict, agent_id: str) -> dict:
        """Point pulled session-push hooks at this machine's interpreter.

        The server cannot know the local interpreter path, whether it needs
        quoting, or whether ``observal_cli`` is importable without a
        ``PYTHONPATH``, so the command is replaced with the one ``doctor``
        installs and compares against.
        """
        from observal_cli.harness_specs.goose_hooks_spec import hook_command

        hooks = content.get("hooks")
        if not isinstance(hooks, dict):
            return content
        command = hook_command()
        for rules in hooks.values():
            if not isinstance(rules, list):
                continue
            for rule in rules:
                if not isinstance(rule, dict):
                    continue
                for handler in rule.get("hooks", []):
                    if isinstance(handler, dict) and _SESSION_PUSH_MODULE in str(handler.get("command", "")):
                        handler["command"] = command
        return content

    def cleanup_hooks(self, dry_run: bool) -> bool:
        from observal_cli.cmd_doctor import _cleanup_goose

        return _cleanup_goose(dry_run)

    # ── Private helpers ───────────────────────────────────────────────

    @staticmethod
    def _event_has_observal_hook(rules: Any) -> bool:
        from observal_cli.shared.utils import is_observal_hook_entry

        if not isinstance(rules, list):
            return False
        return any(
            is_observal_hook_entry(handler)
            for rule in rules
            if isinstance(rule, dict)
            for handler in rule.get("hooks", [])
            if isinstance(handler, dict)
        )

    def _scan_mcps(self, config_file: Path, source: str) -> list[DiscoveredMcp]:
        """Read goose ``extensions`` and keep the ones backed by an MCP server."""
        if not config_file.is_file():
            return []
        try:
            config = yaml.safe_load(config_file.read_text()) or {}
        except (yaml.YAMLError, OSError, UnicodeDecodeError):
            return []
        if not isinstance(config, dict):
            return []
        mcps: list[DiscoveredMcp] = []
        for name, entry in self.extract_mcp_servers(config).items():
            if not isinstance(entry, dict):
                continue
            entry_type = str(entry.get("type") or "")
            if entry_type not in _COMMAND_TYPES and entry_type not in _REMOTE_TYPES:
                continue  # builtin, platform, frontend and inline_python have no command to record
            args = entry.get("args") or []
            mcps.append(
                DiscoveredMcp(
                    name=str(entry.get("name") or name),
                    command=entry.get("cmd") or entry.get("command"),
                    args=list(args) if isinstance(args, list) else [],
                    url=entry.get("uri") or entry.get("url"),
                    description=f"Goose extension: {entry.get('display_name') or name}",
                    source=source,
                )
            )
        return mcps

    def _scan_skills(self, skills_dir: Path, source: str) -> list[DiscoveredSkill]:
        if not skills_dir.is_dir():
            return []
        skills: list[DiscoveredSkill] = []
        for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
            description = ""
            try:
                content = skill_md.read_text(errors="ignore")
                description = parse_frontmatter_field(content, "description") or first_content_line(content)
            except OSError:
                pass
            skills.append(
                DiscoveredSkill(
                    name=skill_md.parent.name,
                    description=description or f"Skill: {skill_md.parent.name}",
                    source=source,
                )
            )
        return skills

    def _scan_agents(self, agents_dir: Path, source: str) -> list[DiscoveredAgent]:
        if not agents_dir.is_dir():
            return []
        agents: list[DiscoveredAgent] = []
        for agent_md in sorted(agents_dir.glob("*.md")):
            content = ""
            try:
                content = agent_md.read_text(errors="ignore")
            except OSError:
                pass
            name = parse_frontmatter_field(content, "name") or agent_md.stem
            agents.append(
                DiscoveredAgent(
                    name=name,
                    description=parse_frontmatter_field(content, "description") or f"Agent: {name}",
                    model_name=parse_frontmatter_field(content, "model") or "",
                    prompt=content[:500],
                    source_file=str(agent_md),
                )
            )
        return agents

    def _scan_plugins(self, plugins_dir: Path, source: str) -> list[DiscoveredHook]:
        """Discover hook rules declared by goose plugins."""
        if not plugins_dir.is_dir():
            return []
        hooks: list[DiscoveredHook] = []
        for hooks_json in sorted(plugins_dir.glob("*/hooks/hooks.json")):
            plugin_name = hooks_json.parent.parent.name
            try:
                declared = json.loads(hooks_json.read_text()).get("hooks", {})
            except (json.JSONDecodeError, OSError, AttributeError):
                continue
            if not isinstance(declared, dict):
                continue
            for event, rules in declared.items():
                if not isinstance(rules, list):
                    continue
                for rule in rules:
                    if not isinstance(rule, dict):
                        continue
                    for handler in rule.get("hooks", []):
                        if not isinstance(handler, dict):
                            continue
                        hooks.append(
                            DiscoveredHook(
                                name=plugin_name,
                                event=event,
                                handler_type=handler.get("type", "command"),
                                handler_config=handler,
                                description=f"Goose plugin hook: {plugin_name} ({event})",
                                source=source,
                            )
                        )
        return hooks


register_adapter(GooseAdapter())
