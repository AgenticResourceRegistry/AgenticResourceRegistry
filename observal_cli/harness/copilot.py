# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""GitHub Copilot harness adapter."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from observal_cli.discovery.adapter_support import RichAdapterScanner
from observal_cli.discovery.models import AdapterDiscoveryResult, DiscoveryScope
from observal_cli.harness import (
    DiscoveredMcp,
    HookSpec,
    ScanResult,
    SessionSource,
    register_adapter,
)
from observal_cli.harness.base import BaseAdapter


def _vscode_user_settings_path(home: Path | None = None) -> Path:
    """Return the platform-specific path to VS Code user settings.json."""
    if home is None:
        home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Code" / "User" / "settings.json"
    elif sys.platform == "win32":
        appdata = Path.home() / "AppData" / "Roaming"
        return appdata / "Code" / "User" / "settings.json"
    else:
        # Linux and other Unix
        return home / ".config" / "Code" / "User" / "settings.json"


class CopilotAdapter(BaseAdapter):
    """Adapter for GitHub Copilot (VS Code based)."""

    home_markers = (".vscode/extensions/github.copilot-*", ".vscode/extensions/github.copilot-chat-*")
    managed_agent_profiles = ("project:.github/agents/{name}.agent.md",)

    @property
    def harness_name(self) -> str:
        return "copilot"

    def resolve_session_source(self, event: dict, home: Path | None = None) -> SessionSource | None:
        from observal_cli.sessions.copilot_cli import append_vscode_hook_event, vscode_hook_source_path

        session_id = str(event.get("session_id") or event.get("sessionId") or "")
        if not session_id:
            return None
        path = (
            vscode_hook_source_path(session_id, home=home)
            if event.get("_observal_lookup_only")
            else append_vscode_hook_event(event, session_id, home=home)
        )
        if not path.is_file():
            return None
        return SessionSource(
            self.harness_name,
            session_id,
            path,
            cwd=str(event.get("cwd") or ""),
        )

    def discover_session_sources(
        self,
        home: Path | None = None,
        since_hours: int = 168,
    ) -> list[SessionSource]:
        from observal_cli.sessions.copilot_cli import discover_vscode_hook_sources

        cutoff = time.time() - since_hours * 3600
        sources: list[SessionSource] = []
        for session_id, path in discover_vscode_hook_sources(home=home):
            try:
                if path.stat().st_mtime >= cutoff:
                    sources.append(SessionSource(self.harness_name, session_id, path))
            except OSError:
                continue
        return sorted(sources, key=lambda source: source.path.stat().st_mtime if source.path else 0, reverse=True)

    def defer_session_delivery(self) -> bool:
        return True

    def is_session_final(self, event: dict) -> bool:
        from observal_cli.sessions.copilot_cli import resolve_hook_event

        return resolve_hook_event(event) in {"Stop", "SessionEnd"}

    def get_hook_spec(self) -> HookSpec:
        return HookSpec(
            events=["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"],
            format="command",
            markers=["observal", "OBSERVAL"],
        )

    def generate_hook_config(
        self,
        observal_url: str,
        api_key: str,
        agent_id: str | None = None,
    ) -> dict:
        from observal_cli.harness_specs.copilot_hooks_spec import build_copilot_hooks

        return build_copilot_hooks()

    def scan_home(self, home: Path | None = None) -> ScanResult:
        home = home or Path.home()
        vscode_dir = home / ".vscode"
        if not vscode_dir.exists():
            return ScanResult()
        mcp_file = vscode_dir / "mcp.json"
        if not mcp_file.exists():
            return ScanResult()
        try:
            data = json.loads(mcp_file.read_text())
            servers = data.get("servers", data.get("mcpServers", {}))
            mcps = []
            for srv_name, srv_config in servers.items():
                if isinstance(srv_config, dict):
                    mcps.append(
                        DiscoveredMcp(
                            name=srv_name,
                            command=srv_config.get("command"),
                            args=srv_config.get("args", []),
                            url=srv_config.get("url"),
                            description=f"Copilot MCP: {srv_name}",
                            source="copilot:global",
                        )
                    )
            return ScanResult(mcps=mcps)
        except (json.JSONDecodeError, OSError):
            return ScanResult()

    def scan_project(self, project_dir: Path) -> ScanResult:
        mcp_file = project_dir / ".vscode" / "mcp.json"
        if not mcp_file.exists():
            return ScanResult()
        try:
            data = json.loads(mcp_file.read_text())
            servers = data.get("servers", data.get("mcpServers", {}))
            mcps = []
            for name, cfg in servers.items():
                if isinstance(cfg, dict):
                    mcps.append(
                        DiscoveredMcp(
                            name=name,
                            command=cfg.get("command"),
                            args=cfg.get("args", []),
                            url=cfg.get("url"),
                            description=f"Copilot project MCP: {name}",
                            source="copilot:project",
                        )
                    )
            return ScanResult(mcps=mcps)
        except (json.JSONDecodeError, OSError):
            return ScanResult()

    def discover_home(self, home: Path | None = None) -> AdapterDiscoveryResult:
        home = home or Path.home()
        return self._discover_copilot_root(home / ".vscode", DiscoveryScope.USER, home=home)

    def discover_project(self, project_dir: Path) -> AdapterDiscoveryResult:
        return self._discover_copilot_root(
            project_dir / ".vscode",
            DiscoveryScope.PROJECT,
            project_dir=project_dir,
        )

    def _discover_copilot_root(
        self,
        root: Path,
        scope: DiscoveryScope,
        *,
        home: Path | None = None,
        project_dir: Path | None = None,
    ) -> AdapterDiscoveryResult:
        scanner = RichAdapterScanner(
            harness=self.harness_name,
            scope=scope,
            root=root,
            home=home,
            project_dir=project_dir,
        )
        config_path = root / "mcp.json"
        data = scanner.read_json(config_path)
        if data is not None:
            scanner.add_mcps(
                data.get("servers", data.get("mcpServers", {})),
                config_path,
                source=f"copilot:{'global' if scope is DiscoveryScope.USER else 'project'}",
                description_prefix="Copilot MCP",
            )
        return scanner.finish()

    def detect_hooks(self, config_dir: Path) -> str:
        """Detect telemetry hooks for Copilot.

        Checks for hook files at .github/hooks/*.json or ~/.copilot/hooks/*.json
        with observal markers.

        Returns "installed" when hooks are configured, "missing" otherwise.
        """
        # Check for hook files in the project
        hooks_dir = config_dir.parent / ".github" / "hooks" if config_dir.name == ".vscode" else config_dir
        if hooks_dir.is_dir():
            for hook_file in hooks_dir.glob("*.json"):
                try:
                    data = json.loads(hook_file.read_text())
                    hooks = data.get("hooks", {})
                    for entries in hooks.values():
                        if isinstance(entries, list):
                            for entry in entries:
                                if isinstance(entry, dict):
                                    cmd = entry.get("command", entry.get("bash", ""))
                                    if "observal" in cmd or "session_push" in cmd:
                                        return "installed"
                except (json.JSONDecodeError, OSError):
                    continue

        # Check user-level hooks
        user_hooks_dir = Path.home() / ".copilot" / "hooks"
        if user_hooks_dir.is_dir():
            for hook_file in user_hooks_dir.glob("*.json"):
                try:
                    data = json.loads(hook_file.read_text())
                    hooks = data.get("hooks", {})
                    for entries in hooks.values():
                        if isinstance(entries, list):
                            for entry in entries:
                                if isinstance(entry, dict):
                                    cmd = entry.get("command", entry.get("bash", ""))
                                    if "observal" in cmd or "session_push" in cmd:
                                        return "installed"
                except (json.JSONDecodeError, OSError):
                    continue

        return "missing"

    def rewrite_hooks(self, content: dict, agent_id: str) -> dict:
        from observal_cli.cmd_pull import _rewrite_copilot_cli_hooks

        return _rewrite_copilot_cli_hooks(content, agent_id=agent_id)

    def patch_hooks(self, dry_run: bool) -> bool:
        from observal_cli.cmd_doctor import _patch_copilot

        return _patch_copilot(dry_run)

    def cleanup_hooks(self, dry_run: bool) -> bool:
        from observal_cli.cmd_doctor import _cleanup_copilot

        return _cleanup_copilot(dry_run)


register_adapter(CopilotAdapter())
