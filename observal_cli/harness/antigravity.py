# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Antigravity CLI harness adapter."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from observal_cli.discovery.adapter_support import RichAdapterScanner
from observal_cli.discovery.models import AdapterDiscoveryResult, DiscoveryScope
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
from observal_cli.harness.base import BaseAdapter, inline_agent_prompt
from observal_cli.harness.protocol import HeadlessPlan, HeadlessRequest
from observal_cli.shared.utils import (
    extract_mcp_servers,
    first_content_line,
    parse_frontmatter_field,
    resolve_antigravity_config_dir,
)


class AntigravityAdapter(BaseAdapter):
    """Adapter for Antigravity CLI."""

    home_markers = (".gemini/antigravity-cli", ".gemini/config")
    headless_binary = "agy"
    managed_agent_profiles = ()

    @property
    def harness_name(self) -> str:
        return "antigravity"

    def resolve_session_source(self, event: dict, home: Path | None = None) -> SessionSource | None:
        from observal_cli.sessions.antigravity import (
            find_antigravity_jsonl,
            remember_session,
            resolve_hook_event,
            resolve_session_id,
            resolve_transcript_path,
        )

        session_id = resolve_session_id(event, home=home)
        if not session_id:
            return None
        hook_event = resolve_hook_event(event, home=home)
        remember_session(session_id, hook_event, home=home)
        transcript = str(event.get("transcriptPath") or event.get("transcript_path") or "")
        path = Path(resolve_transcript_path(transcript)) if transcript else None
        if path is None or not path.is_file():
            path = find_antigravity_jsonl(session_id, home=home)
        if path is None:
            return None
        workspace_paths = event.get("workspacePaths") or []
        cwd = str(workspace_paths[0]) if workspace_paths else str(event.get("cwd") or "")
        return SessionSource(self.harness_name, session_id, path, cwd=cwd)

    def discover_session_sources(
        self,
        home: Path | None = None,
        since_hours: int = 168,
    ) -> list[SessionSource]:
        from observal_cli.sessions.antigravity import find_sessions_dir

        brain = find_sessions_dir(home=home)
        if brain is None or not brain.is_dir():
            return []
        cutoff = time.time() - since_hours * 3600
        sources: list[SessionSource] = []
        for session_dir in brain.iterdir():
            path = session_dir / ".system_generated" / "logs" / "transcript.jsonl"
            try:
                if path.is_file() and path.stat().st_mtime >= cutoff:
                    sources.append(SessionSource(self.harness_name, session_dir.name, path))
            except OSError:
                continue
        return sorted(sources, key=lambda source: source.path.stat().st_mtime if source.path else 0, reverse=True)

    def session_extra_fields(
        self,
        source: SessionSource,
        event: dict,
        final: bool,
        home: Path | None = None,
    ) -> dict[str, Any]:
        from observal_cli.sessions.antigravity import resolve_hook_event

        return {"hook_event": resolve_hook_event(event, home=home)}

    def defer_session_delivery(self) -> bool:
        return True

    def is_session_final(self, event: dict) -> bool:
        from observal_cli.sessions.antigravity import resolve_hook_event

        return resolve_hook_event(event).lower() in {"stop", "sessionend", "session_end"}

    # -- Scanning ----------------------------------------------------------

    def _resolve_ag_dir(self, home: Path | None = None) -> Path | None:
        """Return the antigravity-cli config dir, with WSL Windows path fallback."""
        from observal_cli.shared.utils import resolve_antigravity_dir

        return resolve_antigravity_dir(home)

    def scan_home(self, home: Path | None = None) -> ScanResult:
        """Discover MCPs, skills, hooks, agents from ~/.gemini/antigravity-cli/."""
        ag_dir = self._resolve_ag_dir(home)
        config_dir = resolve_antigravity_config_dir(home)
        if ag_dir is None and config_dir is None:
            return ScanResult()
        if ag_dir is None:
            ag_dir = config_dir
        if config_dir is None:
            config_dir = ag_dir
        mcps = self._scan_mcps(config_dir / "mcp_config.json", "antigravity:global")
        skills = self._scan_skills(config_dir / "skills")
        hooks = self._scan_hooks(config_dir / "hooks.json")
        agents = self._scan_agents(ag_dir / "agents")
        return ScanResult(mcps=mcps, skills=skills, hooks=hooks, agents=agents)

    def scan_project(self, project_dir: Path) -> ScanResult:
        """Discover MCPs, skills from .agents/ in a project."""
        ag_dir = project_dir / ".agents"
        if not ag_dir.exists():
            return ScanResult()

        mcps = self._scan_mcps(ag_dir / "mcp_config.json", "antigravity:project")
        skills = self._scan_skills(ag_dir / "skills")
        return ScanResult(mcps=mcps, skills=skills)

    def discover_home(self, home: Path | None = None) -> AdapterDiscoveryResult:
        home = home or Path.home()
        config_dir = resolve_antigravity_config_dir(home)
        agent_dir = self._resolve_ag_dir(home)
        if config_dir is None and agent_dir is None:
            return AdapterDiscoveryResult()
        config_dir = config_dir or agent_dir
        agent_dir = agent_dir or config_dir
        assert config_dir is not None and agent_dir is not None
        scanner = RichAdapterScanner(
            harness=self.harness_name,
            scope=DiscoveryScope.USER,
            root=config_dir,
            home=home,
        )
        self._discover_antigravity_config(scanner, config_dir, "antigravity:global")
        same_root = agent_dir.resolve(strict=False) == config_dir.resolve(strict=False)
        if same_root:
            self._discover_antigravity_agents(scanner, agent_dir / "agents", "antigravity:global")
        result = scanner.finish()
        if not same_root:
            agent_scanner = RichAdapterScanner(
                harness=self.harness_name,
                scope=DiscoveryScope.USER,
                root=agent_dir,
                home=home,
            )
            self._discover_antigravity_agents(agent_scanner, agent_dir / "agents", "antigravity:global")
            extra = agent_scanner.finish()
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
        self._discover_antigravity_config(scanner, root, "antigravity:project")
        return scanner.finish()

    def _discover_antigravity_config(self, scanner: RichAdapterScanner, root: Path, source: str) -> None:
        mcp_path = root / "mcp_config.json"
        mcp_data = scanner.read_json(mcp_path)
        if mcp_data is not None:
            scanner.add_mcps(
                extract_mcp_servers(dict(mcp_data), "antigravity"),
                mcp_path,
                source=source,
                description_prefix="Antigravity MCP",
            )
        scanner.add_skills(root / "skills", source="antigravity:skills", prefix="Antigravity skill")
        hooks_path = root / "hooks.json"
        hooks_data = scanner.read_json(hooks_path)
        if hooks_data is not None:
            for hook_name, hook_def in hooks_data.items():
                if not isinstance(hook_def, dict):
                    continue
                for event, entries in hook_def.items():
                    if event == "enabled" or not isinstance(entries, list):
                        continue
                    scanner.add_hooks_mapping(
                        hooks_path,
                        {event: entries},
                        name_prefix=redact_text(str(hook_name)),
                        source="antigravity:hooks",
                    )

    def _discover_antigravity_agents(self, scanner: RichAdapterScanner, agents_dir: Path, source: str) -> None:
        for agent_path in scanner.walker.files(agents_dir, name="agent.json"):
            data = scanner.read_json(agent_path)
            if data is None:
                continue
            name = redact_text(str(data.get("name") or agent_path.parent.name))
            prompt = redact_text(str(data.get("system_prompt") or ""))
            scanner.add_component(
                DiscoveredAgent(
                    name=name,
                    description=redact_text(str(data.get("description") or f"Agent: {name}")),
                    model_name=redact_text(str(data.get("model") or "")),
                    prompt=prompt,
                    source_file=str(agent_path),
                ),
                agent_path,
            )

    # -- Hook detection ----------------------------------------------------

    def get_hook_spec(self) -> HookSpec:
        return HookSpec(
            events=["PreToolUse", "PostToolUse", "ToolError", "Stop", "SessionStart", "PreTurn", "PostTurn"],
            format="command",
            markers=["observal", "OBSERVAL"],
        )

    def generate_hook_config(
        self,
        observal_url: str,
        api_key: str,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        from observal_cli.harness_specs.antigravity_hooks_spec import build_antigravity_hooks

        return build_antigravity_hooks()

    def detect_hooks(self, config_dir: Path) -> str:
        """Check if Observal hooks are installed in ~/.gemini/config/hooks.json."""
        from observal_cli.harness_specs.antigravity_hooks_spec import _OBSERVAL_HOOK_NAME
        from observal_cli.shared.utils import resolve_antigravity_config_dir

        # Use config_dir directly if it has hooks.json (e.g. in tests), else resolve real path
        if (config_dir / "hooks.json").exists():
            hooks_file = config_dir / "hooks.json"
        else:
            resolved = resolve_antigravity_config_dir()
            hooks_file = (resolved / "hooks.json") if resolved else (config_dir / "hooks.json")
        if not hooks_file.exists():
            return "missing"
        try:
            data = json.loads(hooks_file.read_text())
        except (json.JSONDecodeError, OSError):
            return "missing"
        return "installed" if _OBSERVAL_HOOK_NAME in data else "missing"

    # -- Private helpers ---------------------------------------------------

    def _scan_mcps(self, mcp_file: Path, source: str) -> list[DiscoveredMcp]:
        if not mcp_file.exists():
            return []
        try:
            data = json.loads(mcp_file.read_text())
            servers = extract_mcp_servers(data, "antigravity")
            return [
                DiscoveredMcp(
                    name=name,
                    command=cfg.get("command"),
                    args=cfg.get("args", []),
                    url=cfg.get("serverUrl") or cfg.get("url"),
                    description=f"Antigravity MCP: {name}",
                    source=source,
                )
                for name, cfg in servers.items()
                if isinstance(cfg, dict)
            ]
        except (json.JSONDecodeError, OSError):
            return []

    def _scan_skills(self, skills_dir: Path) -> list[DiscoveredSkill]:
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
                    source="antigravity:skills",
                )
            )
        return skills

    def _scan_hooks(self, settings_file: Path) -> list[DiscoveredHook]:
        if not settings_file.exists():
            return []
        try:
            data = json.loads(settings_file.read_text())
            hooks: list[DiscoveredHook] = []
            for hook_name, hook_def in data.items():
                if not isinstance(hook_def, dict):
                    continue
                for event, entries in hook_def.items():
                    if event == "enabled" or not isinstance(entries, list):
                        continue
                    for entry in entries:
                        if not isinstance(entry, dict):
                            continue
                        handlers = entry.get("hooks") if isinstance(entry.get("hooks"), list) else [entry]
                        for handler in handlers:
                            if isinstance(handler, dict):
                                hooks.append(
                                    DiscoveredHook(
                                        name=hook_name,
                                        event=event,
                                        handler_type="command",
                                        handler_config=handler,
                                        description=f"Hook: {event}",
                                        source="antigravity:hooks",
                                    )
                                )
            return hooks
        except (json.JSONDecodeError, OSError):
            return []

    def _scan_agents(self, agents_dir: Path) -> list[DiscoveredAgent]:
        if not agents_dir.is_dir():
            return []
        agents = []
        for agent_profile in sorted(agents_dir.glob("*/agent.json")):
            name = agent_profile.parent.name
            content = ""
            desc = ""
            model = ""
            prompt = ""
            try:
                content = agent_profile.read_text()
                data = json.loads(content)
                name = data.get("name") or name
                desc = data.get("description") or ""
                model = data.get("model") or ""
                prompt = data.get("system_prompt") or ""
            except (json.JSONDecodeError, OSError):
                pass
            agents.append(
                DiscoveredAgent(
                    name=name,
                    description=desc or f"Agent: {name}",
                    model_name=model,
                    prompt=(prompt or content)[:500],
                    source_file=str(agent_profile),
                )
            )
        return agents

    def patch_hooks(self, dry_run: bool) -> bool:
        from observal_cli.cmd_doctor import _patch_antigravity

        return _patch_antigravity(dry_run)

    # ── Headless delegation ──────────────────────────────────

    def _headless_command(self, request: HeadlessRequest) -> HeadlessPlan:
        # agy --help: --print <prompt>, --print-timeout, --sandbox. No agent
        # flag, so the agent's instructions are inlined into the prompt.
        argv = ["agy", f"--print={inline_agent_prompt(request)}", "--print-timeout", "30m", "--sandbox"]
        if request.model:
            argv += ["--model", request.model]
        return HeadlessPlan(argv=argv)


register_adapter(AntigravityAdapter())
