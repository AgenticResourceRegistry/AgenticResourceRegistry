# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 EuanTop <euan@mail.bnu.edu.cn>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Pi harness adapter for scanning and hook detection."""

from __future__ import annotations

import json
import os
from pathlib import Path

from observal_cli.discovery.adapter_support import RichAdapterScanner
from observal_cli.discovery.models import AdapterDiscoveryResult, DiagnosticCode, DiscoveryScope
from observal_cli.discovery.redact import redact_text, redact_value
from observal_cli.errors import ErrorCategory, fail
from observal_cli.harness import (
    BundledSkillPlan,
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
from observal_cli.shared.utils import extract_mcp_servers, first_content_line, parse_frontmatter_field


class PiAdapter(BaseAdapter):
    """Adapter for Pi.

    Pi is harness-centric: the entirety of pi IS one agent.
    MCP servers are managed via pi-mcp-adapter (reads ~/.pi/agent/mcp.json).
    Hooks are delivered as the bundled observal.ts extension.
    """

    home_markers = (".pi",)
    managed_agent_profiles = ("user:AGENTS.md",)
    headless_binary = "pi"
    headless_task_on_stdin = True
    managed_skills = ("user:skills/{name}/SKILL.md",)

    @property
    def harness_name(self) -> str:
        return "pi"

    def skill_manifest_path(self, scope: str, alias: str) -> str | None:
        """Pi loads ``skills/<name>/SKILL.md`` from its agent dir, or ``.pi/skills`` in a project."""
        if scope == "user":
            return f"user:skills/{alias}/SKILL.md"
        if scope == "project":
            return f"project:.pi/skills/{alias}/SKILL.md"
        return None

    def skill_location(self, scope: str, directory: str | None, alias: str) -> str | None:
        """The ``location`` Pi advertises and reads for an active skill (``skill.filePath``)."""
        if scope == "user":
            return str(Path.home() / ".pi" / "agent" / "skills" / alias / "SKILL.md")
        if scope == "project" and directory:
            return os.path.join(os.path.abspath(directory), ".pi", "skills", alias, "SKILL.md")
        return None

    def skill_shadow_paths(self, scope: str, directory: str | None, alias: str) -> list[Path]:
        """Pi also discovers ``~/.agents/skills`` and a project's ``.agents/skills``; neither is hashed."""
        paths = [Path.home() / ".agents" / "skills" / alias / "SKILL.md"]
        if directory:
            paths.append(Path(directory) / ".agents" / "skills" / alias / "SKILL.md")
        return paths

    def redact_layer_content(self, display_path: str) -> bool:
        """MCP, settings and hooks JSON can hold inline credentials; retain only their hashes."""
        return display_path == "user:settings.json" or display_path.endswith(
            ("mcp.json", "mcp-adapter.json", "observal-hooks.json")
        )

    def bind_pulled_hooks(
        self, snippet: dict, lock_components: list[dict], target_dir: Path, is_user_scope: bool
    ) -> list[str]:
        """Bind each pinned hook to the profile ``observal-hooks.json`` this pull wrote."""
        from observal_cli.cmd_pull import _resolve_path
        from observal_cli.pi_hooks import bind_pulled_hooks

        hooks_cfg = snippet.get("hooks_config")
        if not isinstance(hooks_cfg, dict) or not isinstance(hooks_cfg.get("path"), str):
            for component in lock_components:
                if component.get("type") == "hook":
                    for key in [key for key in component if key.startswith("hook_")]:
                        del component[key]
            return []
        return bind_pulled_hooks(
            _resolve_path(hooks_cfg["path"], target_dir, allow_home=is_user_scope), lock_components
        )

    def verify_hook_binding(self, directory: str | None, component: dict) -> str:
        """The pinned hook against the active ``~/.pi/agent/observal-hooks.json`` (``pi_hooks.hook_status``)."""
        from observal_cli.pi_hooks import hook_status, read_active_hooks

        return hook_status(component, read_active_hooks())

    def read_pulled_mcp(
        self, scope: str, directory: str | None, alias: str, written_config: Path
    ) -> tuple[str, dict | None]:
        """Read the profile file a pull wrote; `/agent` copies it to the active config.

        Session-time verification against the *active* file is the Pi
        extension's job, because only it knows which profile Pi loaded.
        """
        try:
            data = json.loads(written_config.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return "unverified", None
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        if not isinstance(servers, dict):
            return "unverified", None
        entry = servers.get(alias)
        if entry is None:
            return "missing", None
        return ("verified", entry) if isinstance(entry, dict) else ("unverified", None)

    def plan_bundled_skill_install(
        self,
        skill_name: str,
        home: Path,
        installed_harnesses: frozenset[str],
    ) -> BundledSkillPlan:
        """Share bundled skills with Codex instead of creating Pi duplicates."""
        native = home / ".pi" / "agent" / "skills" / skill_name / "SKILL.md"
        shared = home / ".agents" / "skills" / skill_name / "SKILL.md"
        return BundledSkillPlan(
            target=shared if "codex" in installed_harnesses else native,
            reuse_candidates=(shared,),
            cleanup_candidates=(native,),
        )

    # ── Scanning ──────────────────────────────────────────────

    def scan_home(self, home: Path | None = None) -> ScanResult:
        """Discover MCPs from ~/.pi/agent/mcp.json, skills from ~/.pi/agent/skills/."""
        home = home or Path.home()
        pi_dir = home / ".pi" / "agent"
        if not pi_dir.exists():
            return ScanResult()

        mcps = self._scan_mcps(pi_dir / "mcp.json", "pi:global")
        skills = self._scan_skills(pi_dir / "skills")
        return ScanResult(mcps=mcps, skills=skills)

    def scan_project(self, project_dir: Path) -> ScanResult:
        """Discover MCPs from .pi/mcp.json, skills from .pi/skills/."""
        pi_dir = project_dir / ".pi"
        if not pi_dir.exists():
            return ScanResult()

        mcps = self._scan_mcps(pi_dir / "mcp.json", "pi:project")
        skills = self._scan_skills(pi_dir / "skills")
        return ScanResult(mcps=mcps, skills=skills)

    def discover_home(self, home: Path | None = None) -> AdapterDiscoveryResult:
        home = home or Path.home()
        return self._discover_pi_root(home / ".pi" / "agent", DiscoveryScope.USER, home=home)

    def discover_project(self, project_dir: Path) -> AdapterDiscoveryResult:
        return self._discover_pi_root(
            project_dir / ".pi",
            DiscoveryScope.PROJECT,
            project_dir=project_dir,
            agent_file=project_dir / "AGENTS.md",
        )

    def _discover_pi_root(
        self,
        root: Path,
        scope: DiscoveryScope,
        *,
        home: Path | None = None,
        project_dir: Path | None = None,
        agent_file: Path | None = None,
    ) -> AdapterDiscoveryResult:
        # Project AGENTS.md sits one level above .pi, so the approved project
        # root must include both documented sources.
        approved_root = project_dir if project_dir is not None else root
        scanner = RichAdapterScanner(
            harness=self.harness_name,
            scope=scope,
            root=approved_root,
            home=home,
            project_dir=project_dir,
        )
        source = "pi:global" if scope is DiscoveryScope.USER else "pi:project"
        scanner.add_mcp_config(root / "mcp.json", source=source, description_prefix="Pi MCP")
        scanner.add_skills(root / "skills", source="pi:skills", prefix="Pi skill")
        scanner.add_agent_document(agent_file or (root / "AGENTS.md"), name="pi-agent")

        settings_path = root / "settings.json"
        settings = scanner.read_json(settings_path)
        if settings is not None:
            extensions = settings.get("extensions", [])
            if not isinstance(extensions, list):
                scanner.diagnostic(
                    DiagnosticCode.METADATA_MALFORMED,
                    settings_path,
                    "Pi extensions setting must be an array",
                )
            else:
                for index, value in enumerate(extensions):
                    if not isinstance(value, str):
                        continue
                    extension_name = Path(value.removeprefix("+").removeprefix("-")).stem
                    if extension_name == "observal":
                        continue
                    safe_value = redact_text(Path(value.removeprefix("+").removeprefix("-")).name)
                    scanner.add_component(
                        DiscoveredHook(
                            name=redact_text(extension_name or f"extension-{index + 1}"),
                            event="extension",
                            handler_type="extension",
                            handler_config=redact_value({"extension": safe_value}),
                            description=f"Pi extension: {redact_text(extension_name)}",
                            source=source,
                        ),
                        settings_path,
                    )
        return scanner.finish()

    # ── Hook detection ────────────────────────────────────────

    def get_hook_spec(self) -> HookSpec:
        return HookSpec(
            events=[],
            format="extension",
            markers=["observal"],
        )

    def detect_hooks(self, config_dir: Path) -> str:
        """Check for the local Observal extension file."""
        if (config_dir / "extensions" / "observal.ts").is_file():
            return "installed"
        return "missing"

    def reports_sessions_outside_pull(self) -> bool:
        """The Observal Pi extension reports every session, not the pulled files."""
        return self.detect_hooks(_pi_agent_dir()) == "installed"

    def discover_session_sources(
        self,
        home: Path | None = None,
        since_hours: int = 168,
    ) -> list[SessionSource]:
        """Recent Pi sessions: <agent dir>/sessions/--<cwd>--/<timestamp>_<id>.jsonl."""
        import time

        root = _pi_agent_dir(home) / "sessions"
        if not root.is_dir():
            return []
        cutoff = time.time() - since_hours * 3600
        found: list[tuple[float, SessionSource]] = []
        for path in root.glob("*/*.jsonl"):
            try:
                modified_at = path.stat().st_mtime
                if modified_at < cutoff:
                    continue
                with path.open(encoding="utf-8") as file:
                    header = json.loads(file.readline())
            except (OSError, ValueError):
                continue
            if not isinstance(header, dict) or header.get("type") != "session" or not header.get("id"):
                continue
            source = SessionSource(self.harness_name, str(header["id"]), path, cwd=str(header.get("cwd") or ""))
            found.append((modified_at, source))
        return [source for _modified_at, source in sorted(found, key=lambda item: item[0], reverse=True)]

    def _headless_command(self, request: HeadlessRequest) -> HeadlessPlan:
        # Verified against pi 0.87: -p/--print with no message argument reads the
        # prompt from stdin and exits; --session-id uses that exact session id,
        # creating it; --approve trusts the project-local .pi files the install
        # wrote. Pi has no flag to pick an agent profile, so its instructions are
        # inlined into the prompt.
        #
        # --approve also runs the project's own .pi extensions and package installs,
        # and the workspace is a copy of the caller's repository, so it is passed only
        # when Pi already trusts that repository.
        if request.source_dir is None or not _pi_trusts(request.source_dir):
            fail(
                ErrorCategory.PERMISSION,
                f"Pi does not trust {request.source_dir or 'this repository'}, and a delegated Pi agent would run "
                "its project extensions. Open the repository in pi and run /trust, or delegate with another harness.",
                operation="Delegate to agent",
                resource="pi project trust",
            )
        argv = ["pi", "-p", "--approve", "--session-id", request.session_id]
        if request.model:
            argv += ["--model", request.model]
        return HeadlessPlan(argv=argv, stdin=inline_agent_prompt(request), session_id=request.session_id)

    def _scan_mcps(self, mcp_file: Path, source: str) -> list[DiscoveredMcp]:
        if not mcp_file.exists():
            return []
        try:
            data = json.loads(mcp_file.read_text())
            servers = extract_mcp_servers(data)
            return [
                DiscoveredMcp(
                    name=name,
                    command=cfg.get("command"),
                    args=cfg.get("args", []),
                    url=cfg.get("url"),
                    description=f"Pi MCP: {name}",
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
                    source="pi:skills",
                )
            )
        return skills

    def persist_active_agent(self, agent_id: str, name: str, version: str | None) -> None:
        from observal_cli.config import load, save

        config = load()
        config["active_agent"] = {"id": agent_id, "name": name, "version": version}
        save(config)

    def patch_hooks(self, dry_run: bool) -> bool:
        from observal_cli.cmd_doctor import _patch_pi

        return _patch_pi(dry_run)

    def cleanup_hooks(self, dry_run: bool) -> bool:
        from observal_cli.cmd_doctor import _cleanup_pi

        return _cleanup_pi(dry_run)


register_adapter(PiAdapter())


def _pi_agent_dir(home: Path | None = None) -> Path:
    """Pi's agent directory: PI_CODING_AGENT_DIR, else <home>/.pi/agent."""
    import os

    from observal_cli.pi_extension import pi_agent_dir

    env_dir = os.environ.get("PI_CODING_AGENT_DIR") if home is None else None
    return Path(env_dir).expanduser() if env_dir else pi_agent_dir(home)


def _pi_trusts(path: Path) -> bool:
    """Whether Pi already trusts ``path``, resolved as Pi does it.

    The nearest saved decision in trust.json on the path or a parent wins; only
    without one does ``defaultProjectTrust: "always"`` apply.
    """
    agent_dir = _pi_agent_dir()
    try:
        trust = json.loads((agent_dir / "trust.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        trust = {}
    resolved = path.resolve()
    for candidate in (resolved, *resolved.parents):
        decision = trust.get(str(candidate)) if isinstance(trust, dict) else None
        if isinstance(decision, bool):
            return decision
    try:
        settings = json.loads((agent_dir / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(settings, dict) and settings.get("defaultProjectTrust") == "always"
