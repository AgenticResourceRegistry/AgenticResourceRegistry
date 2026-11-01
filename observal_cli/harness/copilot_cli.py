# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""GitHub Copilot CLI harness adapter."""

from __future__ import annotations

import json
import time
from pathlib import Path

from observal_cli.discovery.adapter_support import RichAdapterScanner
from observal_cli.discovery.models import AdapterDiscoveryResult, DiscoveryScope
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
from observal_cli.harness.protocol import HeadlessPlan, HeadlessRequest
from observal_cli.shared.utils import (
    _OBSERVAL_HOOK_MARKERS,
    first_content_line,
    parse_frontmatter_field,
)


class CopilotCliAdapter(BaseAdapter):
    """Adapter for GitHub Copilot CLI."""

    home_markers = (".copilot",)
    headless_binary = "copilot"
    managed_agent_profiles = ("project:.github/agents/{name}.agent.md",)
    managed_skills = ("project:.agents/skills/{name}/SKILL.md", "user:skills/{name}/SKILL.md")

    @property
    def harness_name(self) -> str:
        return "copilot-cli"

    def resolve_session_source(self, event: dict, home: Path | None = None) -> SessionSource | None:
        from observal_cli.sessions.copilot_cli import find_active_session, find_session_jsonl

        session_id = str(event.get("sessionId") or event.get("session_id") or "")
        transcript = str(event.get("transcriptPath") or event.get("transcript_path") or "")
        path = Path(transcript) if transcript else None
        if path is not None and not path.is_file():
            path = None
        if path is None and session_id:
            path = find_session_jsonl(session_id, home=home)
        cwd = str(event.get("cwd") or "")
        if path is None and not session_id:
            path = find_active_session(cwd, home=home)
        if path is None:
            return None
        if not session_id:
            session_id = path.parent.name
        return SessionSource(self.harness_name, session_id, path, cwd=cwd)

    def discover_session_sources(
        self,
        home: Path | None = None,
        since_hours: int = 168,
    ) -> list[SessionSource]:
        from observal_cli.sessions.copilot_cli import discover_sessions

        cutoff = time.time() - since_hours * 3600
        sources: list[SessionSource] = []
        for path in discover_sessions(home=home):
            try:
                if path.stat().st_mtime >= cutoff:
                    sources.append(SessionSource(self.harness_name, path.parent.name, path))
            except OSError:
                continue
        return sorted(sources, key=lambda source: source.path.stat().st_mtime if source.path else 0, reverse=True)

    def defer_session_delivery(self) -> bool:
        return True

    def is_session_final(self, event: dict) -> bool:
        from observal_cli.sessions.copilot_cli import resolve_hook_event

        return resolve_hook_event(event) in {"Stop", "SessionEnd"}

    def scan_home(self, home: Path | None = None) -> ScanResult:
        home = home or Path.home()
        copilot_dir = home / ".copilot"
        if not copilot_dir.exists():
            return ScanResult()

        mcps = self._scan_mcps_file(copilot_dir / "mcp-config.json", "copilot-cli:global")
        skills = self._scan_skills_dir(copilot_dir / "skills")
        hooks = self._scan_hooks_dir(copilot_dir / "hooks")

        return ScanResult(mcps=mcps, skills=skills, hooks=hooks)

    def scan_project(self, project_dir: Path) -> ScanResult:
        mcps = self._scan_mcps_file(project_dir / ".mcp.json", "copilot-cli:project")
        skills = self._scan_skills_dir(project_dir / ".agents" / "skills")
        agents = self._scan_agents_dir(project_dir / ".github" / "agents")
        hooks = self._scan_hooks_dir(project_dir / ".github" / "hooks")

        return ScanResult(mcps=mcps, skills=skills, agents=agents, hooks=hooks)

    def discover_home(self, home: Path | None = None) -> AdapterDiscoveryResult:
        home = home or Path.home()
        root = home / ".copilot"
        scanner = RichAdapterScanner(
            harness=self.harness_name,
            scope=DiscoveryScope.USER,
            root=root,
            home=home,
        )
        self._discover_copilot_cli(scanner, root, project=False)
        return scanner.finish()

    def discover_project(self, project_dir: Path) -> AdapterDiscoveryResult:
        scanner = RichAdapterScanner(
            harness=self.harness_name,
            scope=DiscoveryScope.PROJECT,
            root=project_dir,
            project_dir=project_dir,
        )
        self._discover_copilot_cli(scanner, project_dir, project=True)
        return scanner.finish()

    def _discover_copilot_cli(self, scanner: RichAdapterScanner, root: Path, *, project: bool) -> None:
        mcp_path = root / ".mcp.json" if project else root / "mcp-config.json"
        mcp_data = scanner.read_json(mcp_path)
        if mcp_data is not None:
            scanner.add_mcps(
                mcp_data.get("mcpServers", {}),
                mcp_path,
                source=f"copilot-cli:{'project' if project else 'global'}",
                description_prefix="Copilot CLI MCP",
            )
        skills_dir = root / ".agents" / "skills" if project else root / "skills"
        scanner.add_skills(skills_dir, source="copilot-cli:skills", prefix="Copilot CLI skill")
        if project:
            scanner.add_markdown_agents(root / ".github" / "agents", source_prefix="Copilot CLI agent")
        hooks_dir = root / ".github" / "hooks" if project else root / "hooks"
        for hook_path in scanner.walker.files(hooks_dir, suffix=".json"):
            data = scanner.read_json(hook_path)
            if data is not None:
                scanner.add_hooks_mapping(
                    hook_path,
                    data.get("hooks", {}),
                    name_prefix=hook_path.stem,
                    source="copilot-cli:hooks",
                )

    def get_hook_spec(self) -> HookSpec:
        return HookSpec(
            events=["sessionStart", "sessionEnd", "userPromptSubmitted", "preToolUse", "postToolUse"],
            format="command",
            markers=["observal", "OBSERVAL", "session_push --harness copilot-cli"],
        )

    def generate_hook_config(
        self,
        observal_url: str,
        api_key: str,
        agent_id: str | None = None,
    ) -> dict:
        from observal_cli.harness_specs.copilot_cli_hooks_spec import build_copilot_cli_hooks

        return build_copilot_cli_hooks(agent_id=agent_id or "")

    def detect_hooks(self, config_dir: Path) -> str:
        """Check for Observal hooks in both project and user-level hook dirs."""
        # Check user-level hooks
        home = Path.home()
        user_hooks_dir = home / ".copilot" / "hooks"
        if self._has_observal_hooks(user_hooks_dir):
            return "installed"

        # Check project-level hooks (config_dir might be the project root)
        project_hooks_dir = config_dir / ".github" / "hooks"
        if self._has_observal_hooks(project_hooks_dir):
            return "installed"

        # Legacy: check config.json format
        config = config_dir / "config.json"
        if config.exists():
            try:
                data = json.loads(config.read_text())
                hooks = data.get("hooks", {})
                for _evt, entries in hooks.items():
                    if isinstance(entries, list):
                        for h in entries:
                            if isinstance(h, dict) and any(
                                m in h.get("bash", "") or m in h.get("command", "") for m in _OBSERVAL_HOOK_MARKERS
                            ):
                                return "installed"
            except (json.JSONDecodeError, OSError):
                pass

        return "missing"

    # ── Private helpers ───────────────────────────────────────

    def _scan_mcps_file(self, mcp_file: Path, source: str) -> list[DiscoveredMcp]:
        if not mcp_file.exists():
            return []
        try:
            data = json.loads(mcp_file.read_text())
            servers = data.get("mcpServers", {})
            mcps = []
            for srv_name, srv_config in servers.items():
                if isinstance(srv_config, dict):
                    mcps.append(
                        DiscoveredMcp(
                            name=srv_name,
                            command=srv_config.get("command"),
                            args=srv_config.get("args", []),
                            url=srv_config.get("url"),
                            description=f"Copilot CLI MCP: {srv_name}",
                            source=source,
                        )
                    )
            return mcps
        except (json.JSONDecodeError, OSError):
            return []

    def _scan_skills_dir(self, skills_dir: Path) -> list[DiscoveredSkill]:
        """Scan a skills directory for SKILL.md files."""
        if not skills_dir.is_dir():
            return []
        skills = []
        for skill_md in sorted(skills_dir.rglob("SKILL.md")):
            name = skill_md.parent.name
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
                    name=name,
                    description=desc or f"Skill: {name}",
                    source="copilot-cli:skills",
                )
            )
        return skills

    def _scan_agents_dir(self, agents_dir: Path) -> list[DiscoveredAgent]:
        """Scan .github/agents/ for .agent.md files."""
        if not agents_dir.is_dir():
            return []
        agents = []
        for agent_md in sorted(agents_dir.glob("*.agent.md")):
            try:
                content = agent_md.read_text()
                name = agent_md.stem.removesuffix(".agent")
                desc = parse_frontmatter_field(content, "description") or ""
                model = parse_frontmatter_field(content, "model") or ""
                if not desc:
                    desc = first_content_line(content)
                agents.append(
                    DiscoveredAgent(
                        name=name,
                        description=desc or f"Agent: {name}",
                        model_name=model,
                        prompt="",
                        source_file=str(agent_md),
                    )
                )
            except OSError:
                pass
        # Also scan plain .md files (VS Code picks these up too)
        for agent_md in sorted(agents_dir.glob("*.md")):
            if agent_md.name.endswith(".agent.md"):
                continue  # already handled above
            try:
                content = agent_md.read_text()
                name = agent_md.stem
                desc = parse_frontmatter_field(content, "description") or ""
                model = parse_frontmatter_field(content, "model") or ""
                if not desc:
                    desc = first_content_line(content)
                agents.append(
                    DiscoveredAgent(
                        name=name,
                        description=desc or f"Agent: {name}",
                        model_name=model,
                        prompt="",
                        source_file=str(agent_md),
                    )
                )
            except OSError:
                pass
        return agents

    def _scan_hooks_dir(self, hooks_dir: Path) -> list[DiscoveredHook]:
        """Scan a hooks directory for .json hook files."""
        if not hooks_dir.is_dir():
            return []
        hooks = []
        for hook_file in sorted(hooks_dir.glob("*.json")):
            try:
                data = json.loads(hook_file.read_text())
                hook_events = data.get("hooks", {})
                for event_name, entries in hook_events.items():
                    if not isinstance(entries, list):
                        continue
                    for entry in entries:
                        if not isinstance(entry, dict):
                            continue
                        hooks.append(
                            DiscoveredHook(
                                name=f"{hook_file.stem}/{event_name}",
                                event=event_name,
                                handler_type="command",
                                handler_config=entry,
                                description=f"Hook from {hook_file.stem}: {event_name}",
                                source="copilot-cli:hooks",
                            )
                        )
            except (json.JSONDecodeError, OSError):
                pass
        return hooks

    def _has_observal_hooks(self, hooks_dir: Path) -> bool:
        """Check if any hook file in the directory contains Observal markers."""
        if not hooks_dir.is_dir():
            return False
        for hook_file in hooks_dir.glob("*.json"):
            try:
                data = json.loads(hook_file.read_text())
                hook_events = data.get("hooks", {})
                for _evt, entries in hook_events.items():
                    if isinstance(entries, list):
                        for h in entries:
                            if isinstance(h, dict):
                                cmd = h.get("bash", "") + h.get("command", "")
                                if any(m in cmd for m in _OBSERVAL_HOOK_MARKERS):
                                    return True
            except (json.JSONDecodeError, OSError):
                pass
        return False

    def rewrite_hooks(self, content: dict, agent_id: str) -> dict:
        from observal_cli.cmd_pull import _rewrite_copilot_cli_hooks

        return _rewrite_copilot_cli_hooks(content, agent_id=agent_id)

    def patch_hooks(self, dry_run: bool) -> bool:
        from observal_cli.cmd_doctor import _patch_copilot_cli

        return _patch_copilot_cli(dry_run)

    def cleanup_hooks(self, dry_run: bool) -> bool:
        from observal_cli.cmd_doctor import _cleanup_copilot_cli

        return _cleanup_copilot_cli(dry_run)

    # ── Headless delegation ──────────────────────────────────

    def _headless_command(self, request: HeadlessRequest) -> HeadlessPlan:
        # GitHub Copilot CLI: --prompt runs one prompt, --agent selects the
        # custom agent, --allow-tool/--deny-tool gate tools in programmatic mode.
        # Its MCP config lives in the home directory, so the agent's servers are
        # passed on the command line instead of written there.
        argv = [
            "copilot",
            f"--prompt={request.message}",
            "--agent",
            request.agent_name,
            "--allow-tool",
            "write",
            "--deny-tool",
            "shell",
        ]
        if request.mcp_servers:
            argv += ["--additional-mcp-config", json.dumps({"mcpServers": request.mcp_servers})]
            for name in request.mcp_servers:
                argv += ["--allow-tool", name]
        if request.model:
            argv += ["--model", request.model]
        return HeadlessPlan(argv=argv)


register_adapter(CopilotCliAdapter())
