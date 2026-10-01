# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Claude Code harness adapter."""

from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from observal_cli.discovery.adapter_support import RichAdapterScanner
from observal_cli.discovery.models import AdapterDiscoveryResult, DiagnosticCode, DiscoveryScope
from observal_cli.discovery.redact import redact_text
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
from observal_cli.harness.base import BaseAdapter, parse_json_result
from observal_cli.harness.protocol import HeadlessPlan, HeadlessRequest, HeadlessResult
from observal_cli.shared.utils import (
    _OBSERVAL_HOOK_MARKERS,
    extract_body,
    extract_mcp_servers,
    first_content_line,
    is_observal_agent_hook_group,
    parse_frontmatter_field,
)
from observal_shared.agent_model import is_auto_model

if TYPE_CHECKING:
    from observal_cli.discovery.bounded_walk import AggregateDiscoveryBudget


class ClaudeCodeAdapter(BaseAdapter):
    """Adapter for Claude Code (Anthropic)."""

    home_markers = (".claude",)
    headless_binary = "claude"
    headless_task_on_stdin = True
    caller_env_markers = ("CLAUDECODE",)
    # Exits 1 only when there is no credential at all; an API key, Bedrock or Vertex counts as one.
    headless_ready_argv = ("claude", "auth", "status")
    headless_not_ready_hint = (
        "Claude Code is not logged in. Run `claude auth login` (or set ANTHROPIC_API_KEY), then delegate again."
    )
    managed_agent_profiles = ("user:agents/{name}.md", "project:.claude/agents/{name}.md")
    managed_skills = ("user:skills/{name}/SKILL.md",)

    @property
    def harness_name(self) -> str:
        return "claude-code"

    def resolve_session_source(self, event: dict[str, Any], home: Path | None = None) -> SessionSource | None:
        from observal_cli.sessions.claude_code import (
            find_jsonl_file,
            get_parent_session_id,
            project_key_from_cwd,
        )

        session_id = str(event.get("session_id") or "")
        cwd = str(event.get("cwd") or "")
        if not session_id:
            return None
        path = find_jsonl_file(session_id, project_key_from_cwd(cwd), home=home)
        if path is None:
            return None
        parent_session_id = get_parent_session_id(path)
        cursor_key = None
        if parent_session_id:
            cursor_key = f"{parent_session_id}__sub__{path.stem.removeprefix('agent-')}"
        return SessionSource(
            harness=self.harness_name,
            session_id=path.stem.removeprefix("agent-") if parent_session_id else session_id,
            path=path,
            cwd=cwd,
            cursor_key=cursor_key,
            parent_session_id=parent_session_id,
        )

    def discover_session_sources(
        self,
        home: Path | None = None,
        since_hours: int = 168,
    ) -> list[SessionSource]:
        from observal_cli.sessions.claude_code import find_sessions_dir

        cutoff = time.time() - since_hours * 3600
        root = find_sessions_dir(home)
        if not root.is_dir():
            return []
        sources: list[SessionSource] = []
        for path in root.glob("*/*.jsonl"):
            if self._recent(path, cutoff):
                sources.append(SessionSource(self.harness_name, path.stem, path))
        for path in root.glob("*/*/subagents/*.jsonl"):
            if not self._recent(path, cutoff):
                continue
            parent_session_id = path.parts[-3]
            subagent_id = path.stem.removeprefix("agent-")
            sources.append(
                SessionSource(
                    self.harness_name,
                    subagent_id,
                    path,
                    cursor_key=f"{parent_session_id}__sub__{subagent_id}",
                    parent_session_id=parent_session_id,
                )
            )
        return sorted(sources, key=lambda source: str(source.path))

    def attributes_by_directory(self) -> bool:
        # A session runs as an agent only through `--agent` or the `agent` setting,
        # which the transcript records; a pulled agent in the folder proves nothing.
        return False

    def session_subagent_name(self, session_jsonl: Path | None) -> str | None:
        # Claude Code writes agent-<id>.meta.json beside each subagent transcript.
        if session_jsonl is None or session_jsonl.parent.name != "subagents":
            return None
        try:
            meta = json.loads(session_jsonl.with_suffix(".meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        agent_type = meta.get("agentType") if isinstance(meta, dict) else None
        return agent_type if isinstance(agent_type, str) and agent_type else None

    def related_session_sources(self, source: SessionSource, home: Path | None = None) -> list[SessionSource]:
        if source.path is None or source.parent_session_id is not None:
            return []
        subagents_dir = source.path.parent / source.session_id / "subagents"
        if not subagents_dir.is_dir():
            return []
        related: list[SessionSource] = []
        for path in sorted(subagents_dir.glob("agent-*.jsonl")):
            subagent_id = path.stem.removeprefix("agent-")
            related.append(
                SessionSource(
                    self.harness_name,
                    subagent_id,
                    path,
                    cwd=source.cwd,
                    cursor_key=f"{source.session_id}__sub__{subagent_id}",
                    parent_session_id=source.session_id,
                )
            )
        return related

    @staticmethod
    def _recent(path: Path, cutoff: float) -> bool:
        try:
            return path.stat().st_mtime >= cutoff
        except OSError:
            return False

    def scan_home(self, home: Path | None = None) -> ScanResult:
        home = home or Path.home()
        claude_dir = home / ".claude"
        if not claude_dir.exists():
            return ScanResult()
        return self._scan_claude_dir(claude_dir)

    def prepare_mcp_setup_command(self, command: list[str], scope: str) -> list[str]:
        if not command or command[0] != "claude":
            return command
        # npm installs a .cmd launcher on Windows. CreateProcess cannot execute
        # its extensionless POSIX shim, even though `where claude` finds it.
        if sys.platform == "win32":
            import shutil

            native = shutil.which("claude.cmd")
            if native:
                command = [native, *command[1:]]
        flags = command[: command.index("--")] if "--" in command else command
        if command[1:3] == ["mcp", "add"] and "--scope" not in flags:
            # Server-generated commands already select Claude's native local/user
            # scope. Ignore flags after `--` (they belong to the MCP process).
            return [*command[:3], "--scope", "local" if scope == "project" else scope, *command[3:]]
        return command

    def mcp_manifest_path(self, scope: str) -> str | None:
        return {"project": "project:.mcp.json", "user": "user:.claude.json"}.get(scope)

    def _installed_mcp_entry(
        self, scope: str, directory: str | None, alias: str
    ) -> tuple[str, dict | None, str | None]:
        """Claude local scope lives under projects[realpath] in ~/.claude.json.

        A separate project-wide .mcp.json can also exist; local takes precedence.
        Never fall through a malformed local registration to a different entry.
        """
        if scope not in {"project", "user"} or (scope == "project" and not directory):
            return "unverified", None, None
        user_path = Path.home() / ".claude.json"
        try:
            user_data = json.loads(user_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            user_data = None
        except (OSError, UnicodeError, json.JSONDecodeError):
            return "unverified", None, "user:.claude.json"
        if user_data is not None and not isinstance(user_data, dict):
            return "unverified", None, "user:.claude.json"
        if scope == "user":
            data, display = user_data, "user:.claude.json"
        else:
            projects = user_data.get("projects", {}) if user_data else {}
            if not isinstance(projects, dict):
                return "unverified", None, "user:.claude.json"
            project = projects.get(str(Path(directory).resolve()))
            if project is not None:
                if not isinstance(project, dict):
                    return "unverified", None, "user:.claude.json"
                local_servers = project.get("mcpServers", {})
                if not isinstance(local_servers, dict):
                    return "unverified", None, "user:.claude.json"
                if alias in local_servers:
                    entry = local_servers[alias]
                    return (
                        ("verified", entry, "user:.claude.json")
                        if isinstance(entry, dict)
                        else ("unverified", None, "user:.claude.json")
                    )
            display = "project:.mcp.json"
            try:
                data = json.loads((Path(directory) / ".mcp.json").read_text(encoding="utf-8"))
            except FileNotFoundError:
                return "missing", None, display
            except (OSError, UnicodeError, json.JSONDecodeError):
                return "unverified", None, display
        if not isinstance(data, dict) or not isinstance(data.get("mcpServers"), dict):
            return "unverified", None, display
        entry = data["mcpServers"].get(alias)
        if entry is None:
            return "missing", None, display
        return ("verified", entry, display) if isinstance(entry, dict) else ("unverified", None, display)

    def installed_mcp_manifest_path(self, scope: str, directory: str | None, alias: str) -> str | None:
        return self._installed_mcp_entry(scope, directory, alias)[2]

    def redact_layer_content(self, display_path: str) -> bool:
        return display_path in {"user:.claude.json", "project:.mcp.json"}

    def standalone_hook_binding(
        self, config_path: str, config_snippet: dict, written: list[tuple[Path, Path]]
    ) -> dict | None:
        """Bind a ``hook install`` that wrote exactly one command hook into a settings file."""
        import hashlib

        from observal_cli.layer import skill_file_fingerprint

        if config_path.startswith("~/.claude/"):
            config = f"user:{config_path[len('~/.claude/') :]}"
        elif config_path and not config_path.startswith(("~", "/")):
            config = f"project:{config_path}"
        else:
            return None
        commands = [
            (event, hook.get("command"))
            for event, groups in (config_snippet.get("hooks") or {}).items()
            if isinstance(groups, list)
            for group in groups
            if isinstance(group, dict)
            for hook in group.get("hooks") or []
            if isinstance(hook, dict) and hook.get("type", "command") == "command"
        ]
        if len(commands) != 1 or not isinstance(commands[0][1], str) or not commands[0][1]:
            return None
        event, command = commands[0]
        scripts = [
            (path, rel) for path, rel in written if command == rel.as_posix() or command.endswith(rel.as_posix())
        ]
        if scripts:
            fingerprint = skill_file_fingerprint(scripts[0][0])
            if not fingerprint:
                return None
            script = f"project:{scripts[0][1].as_posix()}"
        else:
            fingerprint, script = f"sha256-{hashlib.sha256(command.encode()).hexdigest()}", ""
        return {
            "hook_event": event,
            "hook_command": command,
            "hook_agent": "",
            "hook_config": config,
            "hook_script": script,
            "hook_integrity": fingerprint,
        }

    def _hook_file(self, display: str, directory: str | None) -> Path | None:
        if display.startswith("user:"):
            return Path.home() / ".claude" / display[len("user:") :]
        if display.startswith("project:") and directory:
            return Path(directory) / display[len("project:") :]
        return None

    @staticmethod
    def _hook_entries(path: Path) -> Counter[tuple[str, str]] | None:
        """How many times each (event, command) is configured in a settings file or agent frontmatter.

        Counts, not a set: two identical entries are two hooks that Claude Code's
        records cannot tell apart. None when the file cannot be read or parsed.
        """
        import yaml

        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None
        try:
            if path.suffix == ".md":
                if not text.startswith("---"):
                    return Counter()
                data = yaml.safe_load(text.split("---", 2)[1]) or {}
            else:
                data = json.loads(text)
        except (ValueError, yaml.YAMLError, IndexError):
            return None
        hooks = data.get("hooks") if isinstance(data, dict) else None
        return Counter(
            (event, hook["command"])
            for event, groups in (hooks.items() if isinstance(hooks, dict) else ())
            if isinstance(groups, list)
            for group in groups
            if isinstance(group, dict)
            for hook in group.get("hooks") or []
            if isinstance(hook, dict) and isinstance(hook.get("command"), str)
        )

    def _hook_locations(self, directory: str | None) -> list[Path]:
        """Every place Claude Code reads command hooks from (settings and agent frontmatter)."""
        base = Path.home() / ".claude"
        paths = [base / "settings.json", *sorted((base / "agents").glob("*.md"))]
        if directory:
            project = Path(directory) / ".claude"
            paths += [
                project / "settings.json",
                project / "settings.local.json",
                *sorted((project / "agents").glob("*.md")),
            ]
        return paths

    def verify_hook_binding(self, directory: str | None, component: dict) -> str:
        """The recorded hook is configured exactly once where it was written, its script is
        unchanged, and no other hook location configures the same event and command (which
        Claude Code's records could not tell apart). A hook location that exists but cannot be
        read or parsed cannot rule out a duplicate, so it also leaves the hook unverified.
        """
        from observal_cli.layer import skill_file_fingerprint

        event, command = component.get("hook_event"), component.get("hook_command")
        config = self._hook_file(str(component.get("hook_config") or ""), directory)
        if not event or not command or config is None or not component.get("hook_integrity"):
            return "unverified"
        entries = self._hook_entries(config)
        if entries is None:
            return "unverified"
        if entries[event, command] == 0:
            return "drifted"
        if entries[event, command] > 1:
            return "unverified"  # duplicated in the same file: a recorded run names neither copy
        if component.get("hook_placement") == "gated_settings":
            gated = self._gated_hook_state(directory, config, component)
            if gated != "verified":
                return gated
        script = component.get("hook_script") or ""
        if script:
            path = self._hook_file(script, directory)
            fingerprint = skill_file_fingerprint(path) if path else None
            if fingerprint is None:
                return "unverified"
            if fingerprint != component["hook_integrity"]:
                return "drifted"
        for other in self._hook_locations(directory):
            if not other.exists() or other.resolve() == config.resolve():
                continue
            found = self._hook_entries(other)
            if found is None or found[event, command]:
                return "unverified"
        return "verified"

    def _gated_hook_state(self, directory: str | None, config: Path, component: dict) -> str:
        """Extra checks for an agent hook placed in settings.json behind its agent gate.

        Its Observal-owned group must be there exactly once and unedited (any change
        to matcher, timeout or options is drift), and the agent file must no longer
        carry the original hook: a copy there would run as well in interactive
        sessions, and its runs would name a different command.
        """
        from observal_cli.agent_hooks import agent_hook_metadata, is_edited

        event, command = component["hook_event"], component["hook_command"]
        original = component.get("hook_original_command")
        agent = component.get("hook_agent")
        profile = self._hook_file(str(component.get("hook_agent_profile") or ""), directory)
        if not isinstance(original, str) or not original or not agent or profile is None:
            return "unverified"
        try:
            data = json.loads(config.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return "unverified"
        hooks = data.get("hooks") if isinstance(data, dict) else None
        owned = [
            group
            for group in (hooks.get(event) if isinstance(hooks, dict) else None) or []
            if (meta := agent_hook_metadata(group)) is not None
            and meta.get("agent") == agent
            and meta.get("component_id") == component.get("id")
        ]
        if not owned:
            return "unverified"  # the group was handed to the user (no _observal key) or moved
        if len(owned) > 1:
            return "unverified"
        if is_edited(event, owned[0]):
            return "drifted"
        if not any(isinstance(hook, dict) and hook.get("command") == command for hook in owned[0].get("hooks") or []):
            return "drifted"
        if profile.exists():
            entries = self._hook_entries(profile)
            if entries is None or entries[event, original]:
                return "unverified"
        return "verified"

    def skill_manifest_path(self, scope: str, alias: str) -> str | None:
        """Claude Code loads ``~/.claude/skills/<name>`` (personal) or the project's ``.claude/skills``."""
        if scope == "user":
            return f"user:skills/{alias}/SKILL.md"
        if scope == "project":
            return f"project:.claude/skills/{alias}/SKILL.md"
        return None

    def skill_shadow_paths(self, scope: str, directory: str | None, alias: str) -> list[Path]:
        """An enterprise skill of the same name runs instead of a personal or project one (unhashed)."""
        managed = (
            Path("/Library/Application Support/ClaudeCode")
            if sys.platform == "darwin"
            else Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "ClaudeCode"
            if sys.platform == "win32"
            else Path("/etc/claude-code")
        )
        return [managed / ".claude" / "skills" / alias / "SKILL.md"]

    def skill_location(self, scope: str, directory: str | None, alias: str) -> str | None:
        """``<base dir>/SKILL.md`` as Claude Code names it in the skill's expansion record."""
        if scope == "user":
            return str(Path.home() / ".claude" / "skills" / alias / "SKILL.md")
        if scope == "project" and directory:
            return os.path.join(os.path.abspath(directory), ".claude", "skills", alias, "SKILL.md")
        return None

    def read_installed_mcp(self, scope: str, directory: str | None, alias: str) -> tuple[str, dict | None]:
        """Read the effective MCP key without exporting the shared settings document."""
        status, entry, _ = self._installed_mcp_entry(scope, directory, alias)
        return status, entry

    def scan_project(self, project_dir: Path) -> ScanResult:
        # Claude Code uses .mcp.json at project root
        mcp_file = project_dir / ".mcp.json"
        if not mcp_file.exists():
            return ScanResult()
        try:
            data = json.loads(mcp_file.read_text())
            servers = extract_mcp_servers(data)
            mcps = []
            for name, cfg in servers.items():
                mcps.append(
                    DiscoveredMcp(
                        name=name,
                        command=cfg.get("command"),
                        args=cfg.get("args", []),
                        url=cfg.get("url"),
                        description=f"Claude Code project MCP: {name}",
                        source="claude-code:project",
                    )
                )
            return ScanResult(mcps=mcps)
        except (json.JSONDecodeError, OSError):
            return ScanResult()

    def discover_home(self, home: Path | None = None) -> AdapterDiscoveryResult:
        home = home or Path.home()
        return self._discover_claude_home(home / ".claude", home)

    def discover_project(self, project_dir: Path) -> AdapterDiscoveryResult:
        scanner = RichAdapterScanner(
            harness=self.harness_name,
            scope=DiscoveryScope.PROJECT,
            root=project_dir,
            project_dir=project_dir,
        )
        scanner.add_mcp_config(
            project_dir / ".mcp.json",
            source="claude-code:project",
            description_prefix="Claude Code project MCP",
        )
        settings_path = project_dir / ".claude" / "settings.json"
        settings = scanner.read_json(settings_path)
        if settings is not None:
            scanner.add_hooks_mapping(
                settings_path,
                settings.get("hooks", {}),
                name_prefix="claude-code:project",
                source="claude-code:project",
            )
        scanner.add_skills(project_dir / ".claude" / "skills", source="claude:skills", prefix="Skill")
        scanner.add_markdown_agents(project_dir / ".claude" / "agents", source_prefix="Agent")
        return scanner.finish()

    def get_hook_spec(self) -> HookSpec:
        return HookSpec(
            events=[
                "PreToolUse",
                "PostToolUse",
                "Notification",
                "Stop",
                "SubagentStop",
            ],
            format="command",
            markers=["observal", "OBSERVAL"],
        )

    def generate_hook_config(
        self,
        observal_url: str,
        api_key: str,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        from observal_cli.harness_specs.claude_code_hooks_spec import get_desired_hooks

        return get_desired_hooks()

    def detect_hooks(self, config_dir: Path) -> str:
        settings = config_dir / "settings.json"
        if not settings.exists():
            return "missing"
        try:
            data = json.loads(settings.read_text())
        except (json.JSONDecodeError, OSError):
            return "missing"
        hooks = data.get("hooks", {})
        if not hooks:
            return "missing"
        installed_events = set()
        has_observal_hook = False
        for event, groups in hooks.items():
            if not isinstance(groups, list):
                continue
            for group in groups:
                if is_observal_agent_hook_group(group):
                    continue  # an agent's gated hook, not telemetry
                for hook in group.get("hooks", []):
                    command, url = hook.get("command", ""), hook.get("url", "")
                    if any(marker in command or marker in url for marker in _OBSERVAL_HOOK_MARKERS):
                        has_observal_hook = True
                    if (
                        event in ("UserPromptSubmit", "Stop")
                        and hook.get("type") == "command"
                        and command.endswith(" -m observal_cli.hooks.session_push --harness claude-code")
                    ):
                        installed_events.add(event)
        return "installed" if len(installed_events) == 2 else ("partial" if has_observal_hook else "missing")

    # ── Private scanning helpers ──────────────────────────────────

    def _scan_claude_dir(self, claude_dir: Path) -> ScanResult:
        """Scan ~/.claude for all component types."""
        mcps: list[DiscoveredMcp] = []
        skills: list[DiscoveredSkill] = []
        hooks: list[DiscoveredHook] = []
        agents: list[DiscoveredAgent] = []

        settings_file = claude_dir / "settings.json"
        if not settings_file.exists():
            return ScanResult()

        try:
            settings = json.loads(settings_file.read_text())
        except (json.JSONDecodeError, OSError):
            return ScanResult()

        enabled_plugins = settings.get("enabledPlugins", {})
        active_plugins = {name for name, enabled in enabled_plugins.items() if enabled}

        # Load installed_plugins.json to get install paths
        installed_file = claude_dir / "plugins" / "installed_plugins.json"
        plugin_paths: dict[str, Path] = {}
        if installed_file.exists():
            try:
                installed = json.loads(installed_file.read_text())
                for plugin_key, entries in installed.get("plugins", {}).items():
                    if plugin_key in active_plugins and entries:
                        install_path = entries[0].get("installPath")
                        if install_path:
                            plugin_paths[plugin_key] = Path(install_path)
            except (json.JSONDecodeError, OSError):
                pass

        # Fallback: scan plugin cache directly
        cache_dir = claude_dir / "plugins" / "cache"
        if cache_dir.exists():
            for plugin_key in active_plugins:
                if plugin_key in plugin_paths:
                    continue
                parts = plugin_key.split("@", 1)
                name = parts[0]
                marketplace = parts[1] if len(parts) > 1 else ""
                market_dir = cache_dir / marketplace / name if marketplace else cache_dir / name / name
                if market_dir.exists():
                    versions = sorted(market_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
                    if versions:
                        plugin_paths[plugin_key] = versions[0]

        for plugin_key, plugin_dir in plugin_paths.items():
            if not plugin_dir.is_dir():
                continue

            plugin_name = plugin_key.split("@")[0]
            plugin_desc = f"Plugin: {plugin_name}"
            plugin_json = plugin_dir / ".claude-plugin" / "plugin.json"
            if plugin_json.exists():
                try:
                    meta = json.loads(plugin_json.read_text())
                    plugin_desc = meta.get("description", plugin_desc)
                except (json.JSONDecodeError, OSError):
                    pass

            mcp_file = plugin_dir / ".mcp.json"
            if mcp_file.exists():
                try:
                    mcp_data = json.loads(mcp_file.read_text())
                    servers = extract_mcp_servers(mcp_data)
                    for srv_name, srv_config in servers.items():
                        mcps.append(
                            DiscoveredMcp(
                                name=srv_name,
                                command=srv_config.get("command"),
                                args=srv_config.get("args", []),
                                url=srv_config.get("url"),
                                description=plugin_desc,
                                source=f"plugin:{plugin_name}",
                            )
                        )
                except (json.JSONDecodeError, OSError):
                    pass

            for skill_md in plugin_dir.rglob("SKILL.md"):
                skill_name_part = skill_md.parent.name
                full_name = f"{plugin_name}/{skill_name_part}"
                desc = ""
                try:
                    content = skill_md.read_text()
                    desc = parse_frontmatter_field(content, "description") or ""
                    if not desc:
                        desc = first_content_line(content)
                except OSError:
                    pass
                skills.append(
                    DiscoveredSkill(
                        name=full_name,
                        description=desc or f"Skill from {plugin_name}",
                        source=f"plugin:{plugin_name}",
                    )
                )

            for hooks_file in plugin_dir.rglob("hooks.json"):
                try:
                    hooks_data = json.loads(hooks_file.read_text())
                    hook_events = hooks_data.get("hooks", {})
                    for event_name, event_hooks in hook_events.items():
                        hook_full_name = f"{plugin_name}/{event_name}"
                        handler_type = "command"
                        handler_config = {}
                        if isinstance(event_hooks, list) and event_hooks:
                            first = event_hooks[0]
                            if isinstance(first, dict):
                                inner = first.get("hooks", [first])
                                if inner and isinstance(inner[0], dict):
                                    handler_type = inner[0].get("type", "command")
                                    handler_config = inner[0]
                        hooks.append(
                            DiscoveredHook(
                                name=hook_full_name,
                                event=event_name,
                                handler_type=handler_type,
                                handler_config=handler_config,
                                description=f"Hook from {plugin_name}: {event_name}",
                                source=f"plugin:{plugin_name}",
                            )
                        )
                except (json.JSONDecodeError, OSError):
                    pass

        # Skills from ~/.claude/skills/
        skills_dir = claude_dir / "skills"
        if skills_dir.is_dir():
            for skill_md in sorted(skills_dir.rglob("SKILL.md")):
                skill_name = skill_md.parent.name
                desc = ""
                task_type = "general"
                try:
                    content = skill_md.read_text()
                    desc = parse_frontmatter_field(content, "description") or ""
                    task_type = parse_frontmatter_field(content, "task_type") or "general"
                    if not desc:
                        desc = first_content_line(content)
                except OSError:
                    pass
                skills.append(
                    DiscoveredSkill(
                        name=skill_name,
                        description=desc or f"Skill: {skill_name}",
                        source="claude:skills",
                        task_type=task_type,
                    )
                )

        # Agents from ~/.claude/agents/
        agents_dir = claude_dir / "agents"
        if agents_dir.is_dir():
            for agent_md in sorted(agents_dir.glob("*.md")):
                try:
                    content = agent_md.read_text()
                    name = agent_md.stem
                    model = parse_frontmatter_field(content, "model") or ""
                    desc = first_content_line(content)
                    prompt_body = extract_body(content)
                    agents.append(
                        DiscoveredAgent(
                            name=name,
                            description=desc or f"Agent: {name}",
                            model_name=model,
                            prompt=prompt_body,
                            source_file=str(agent_md),
                        )
                    )
                except OSError:
                    pass

        return ScanResult(mcps=mcps, skills=skills, hooks=hooks, agents=agents)

    def _discover_claude_home(self, claude_dir: Path, home: Path) -> AdapterDiscoveryResult:
        scanner = RichAdapterScanner(
            harness=self.harness_name,
            scope=DiscoveryScope.USER,
            root=claude_dir,
            home=home,
        )
        budget = scanner.walker.budget
        deadline = scanner.walker.deadline
        settings_path = claude_dir / "settings.json"
        settings = scanner.read_json(settings_path)
        active_plugins: set[str] = set()
        if settings is not None:
            scanner.add_mcps(
                settings.get("mcpServers", {}),
                settings_path,
                source="claude-code:global",
                description_prefix="Claude Code global MCP",
            )
            scanner.add_hooks_mapping(
                settings_path,
                settings.get("hooks", {}),
                name_prefix="claude-code",
                source="claude-code:global",
            )
            enabled = settings.get("enabledPlugins", {})
            if isinstance(enabled, Mapping):
                active_plugins = {str(name) for name, value in enabled.items() if value is True}
            else:
                scanner.diagnostic(
                    DiagnosticCode.METADATA_MALFORMED,
                    settings_path,
                    "Claude enabledPlugins must be an object",
                )

        # User skills and agents are independent of settings.json.
        scanner.add_skills(claude_dir / "skills", source="claude:skills", prefix="Skill")
        scanner.add_markdown_agents(claude_dir / "agents", source_prefix="Agent")

        plugin_paths: dict[str, Path] = {}
        installed_path = claude_dir / "plugins" / "installed_plugins.json"
        installed = scanner.read_json(installed_path) if active_plugins else None
        if installed is not None:
            plugins = installed.get("plugins", {})
            if not isinstance(plugins, Mapping):
                scanner.diagnostic(
                    DiagnosticCode.METADATA_MALFORMED,
                    installed_path,
                    "Claude installed plugin registry must contain an object",
                )
            else:
                for plugin_key in sorted(active_plugins, key=str.casefold):
                    entries = plugins.get(plugin_key)
                    if entries is None:
                        continue
                    if not isinstance(entries, list) or not entries or not isinstance(entries[0], Mapping):
                        scanner.diagnostic(
                            DiagnosticCode.METADATA_MALFORMED,
                            installed_path,
                            "Claude installed plugin entry must be a non-empty array of objects",
                        )
                        continue
                    install_path = entries[0].get("installPath")
                    if not isinstance(install_path, str) or not install_path.strip():
                        scanner.diagnostic(
                            DiagnosticCode.METADATA_MALFORMED,
                            installed_path,
                            "Claude installed plugin entry is missing installPath",
                        )
                        continue
                    candidate = Path(install_path).expanduser()
                    try:
                        resolved = candidate.resolve(strict=True)
                    except (OSError, RuntimeError):
                        scanner.diagnostic(
                            DiagnosticCode.PERMISSION_DENIED,
                            installed_path,
                            "Claude plugin install root is missing or unreadable",
                        )
                        continue
                    if not resolved.is_dir():
                        scanner.diagnostic(
                            DiagnosticCode.METADATA_MALFORMED,
                            installed_path,
                            "Claude plugin install root is not a directory",
                        )
                        continue
                    plugin_paths[plugin_key] = resolved

        for plugin_key in sorted(active_plugins, key=str.casefold):
            if plugin_key not in plugin_paths:
                cached = self._cached_plugin_root(claude_dir, plugin_key, scanner)
                if cached is not None:
                    plugin_paths[plugin_key] = cached

        result = scanner.finish()
        aggregate_limit_codes = {
            DiagnosticCode.APPROVED_ROOT_LIMIT_REACHED,
            DiagnosticCode.COLLECTION_FILE_LIMIT_REACHED,
            DiagnosticCode.COLLECTION_ENTRY_LIMIT_REACHED,
            DiagnosticCode.EVIDENCE_LIMIT_REACHED,
        }
        for plugin_key in sorted(plugin_paths, key=str.casefold):
            if (
                budget.emitted_limits.intersection(aggregate_limit_codes)
                or budget.roots >= budget.max_roots
                or budget.files >= budget.max_files
                or budget.entries >= budget.max_entries
                or budget.evidence >= budget.max_evidence
            ):
                break
            plugin_result = self._discover_claude_plugin(
                plugin_key,
                plugin_paths[plugin_key],
                home=home,
                budget=budget,
                deadline=deadline,
            )
            result.evidence.extend(plugin_result.evidence)
            result.diagnostics.extend(plugin_result.diagnostics)
        result.evidence.sort(
            key=lambda item: (
                item.display_path or "",
                str(getattr(item.component, "name", "")).casefold(),
                type(item.component).__name__,
            )
        )
        result.diagnostics.sort(key=lambda item: (item.source or "", item.code.value, item.message))
        return result

    def _cached_plugin_root(
        self,
        claude_dir: Path,
        plugin_key: str,
        scanner: RichAdapterScanner,
    ) -> Path | None:
        name, separator, marketplace = plugin_key.partition("@")
        market_dir = (
            claude_dir / "plugins" / "cache" / marketplace / name
            if separator
            else claude_dir / "plugins" / "cache" / name / name
        )
        if not market_dir.is_dir():
            return None
        versions = scanner.walker.child_directories(market_dir)
        if not versions:
            return None
        try:
            versions.sort(key=lambda path: (-path.stat().st_mtime, path.name.casefold()))
        except OSError:
            scanner.diagnostic(
                DiagnosticCode.PERMISSION_DENIED,
                market_dir,
                "unable to inspect Claude plugin cache metadata",
            )
            return None
        return versions[0].resolve(strict=False) if versions else None

    def _discover_claude_plugin(
        self,
        plugin_key: str,
        plugin_dir: Path,
        *,
        home: Path,
        budget: AggregateDiscoveryBudget,
        deadline: float,
    ) -> AdapterDiscoveryResult:
        scanner = RichAdapterScanner(
            harness=self.harness_name,
            scope=DiscoveryScope.USER,
            root=plugin_dir,
            home=home,
            budget=budget,
            deadline=deadline,
        )
        plugin_name = redact_text(plugin_key.split("@", 1)[0])
        description = f"Plugin: {plugin_name}"
        metadata_path = plugin_dir / ".claude-plugin" / "plugin.json"
        metadata = scanner.read_json(metadata_path)
        if metadata is not None and isinstance(metadata.get("description"), str):
            description = redact_text(metadata["description"])
        scanner.add_mcp_config(
            plugin_dir / ".mcp.json",
            source=f"plugin:{plugin_name}",
            description_prefix="Plugin MCP",
            description=description,
        )
        for skill_path in scanner.walker.files(plugin_dir, name="SKILL.md"):
            content = scanner.walker.read_text(skill_path)
            if content is None:
                continue
            skill_name = redact_text(skill_path.parent.name)
            skill_description = parse_frontmatter_field(content, "description") or first_content_line(content)
            scanner.add_component(
                DiscoveredSkill(
                    name=f"{plugin_name}/{skill_name}",
                    description=redact_text(skill_description or f"Skill from {plugin_name}"),
                    source=f"plugin:{plugin_name}",
                ),
                skill_path,
            )
        for hooks_path in scanner.walker.files(plugin_dir, name="hooks.json"):
            hooks_data = scanner.read_json(hooks_path)
            if hooks_data is not None:
                scanner.add_hooks_mapping(
                    hooks_path,
                    hooks_data.get("hooks", {}),
                    name_prefix=plugin_name,
                    source=f"plugin:{plugin_name}",
                )
        return scanner.finish()

    def saved_model(self, agent_detail: dict | None) -> str | None:
        saved = super().saved_model(agent_detail)
        if saved or not agent_detail:
            return saved
        legacy = agent_detail.get("model_name")
        # "auto" (or blank) leaves the choice to Claude Code's own default.
        return None if is_auto_model(legacy) else legacy.strip()

    def apply_install_options(self, options: dict, tools: str | None) -> None:
        if tools:
            options["tools"] = tools

    def patch_hooks(self, dry_run: bool) -> bool:
        from observal_cli.cmd_doctor import _patch_claude_code

        return _patch_claude_code(dry_run)

    def cleanup_hooks(self, dry_run: bool) -> bool:
        from observal_cli.cmd_doctor import _cleanup_claude_code

        return _cleanup_claude_code(dry_run)

    # ── Headless delegation ──────────────────────────────────

    def _headless_command(self, request: HeadlessRequest) -> HeadlessPlan:
        # claude --help: -p/--print, --agent, --permission-mode, --mcp-config,
        # --strict-mcp-config, --session-id. The prompt goes on stdin so a task
        # that starts with "-" is never read as a flag. Edits are accepted (they
        # land in the throwaway worktree); anything else that asks is denied in
        # print mode, except the agent's own reviewed MCP servers.
        mcp_path = request.scratch_dir / "mcp.json"
        mcp_path.write_text(json.dumps({"mcpServers": request.mcp_servers}, indent=2), encoding="utf-8")
        argv = [
            "claude",
            "-p",
            "--agent",
            request.agent_name,
            "--output-format",
            "json",
            "--permission-mode",
            "acceptEdits",
            "--session-id",
            request.session_id,
            "--mcp-config",
            str(mcp_path),
            "--strict-mcp-config",
        ]
        if request.mcp_servers:
            argv += ["--allowedTools", ",".join(f"mcp__{name}" for name in request.mcp_servers)]
        if request.model:
            argv += ["--model", request.model]
        return HeadlessPlan(argv=argv, stdin=request.message, session_id=request.session_id)

    def parse_headless_output(self, plan: HeadlessPlan, stdout: str) -> HeadlessResult:
        return parse_json_result(plan, stdout) or super().parse_headless_output(plan, stdout)


register_adapter(ClaudeCodeAdapter())
