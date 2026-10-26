# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Codex CLI harness adapter."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from observal_cli.discovery.adapter_support import RichAdapterScanner
from observal_cli.discovery.models import AdapterDiscoveryResult, DiagnosticCode, DiscoveryScope
from observal_cli.harness import (
    DiscoveredMcp,
    HookSpec,
    ScanResult,
    SessionSource,
    register_adapter,
)
from observal_cli.harness.base import BaseAdapter, inline_agent_prompt
from observal_cli.harness.protocol import HeadlessPlan, HeadlessRequest
from observal_cli.shared.utils import _OBSERVAL_HOOK_MARKERS

if TYPE_CHECKING:
    from .codex_mcp_binding import McpConfigUndo


def _load_toml(path: Path) -> dict:
    """Load a TOML file with graceful fallback across parser implementations."""
    try:
        import tomllib as toml

        return toml.loads(path.read_text())
    except ImportError:
        pass
    try:
        import tomli as toml  # type: ignore[no-redef]

        return toml.loads(path.read_text())
    except ImportError:
        pass
    try:
        import toml  # type: ignore[no-redef]

        return toml.loads(path.read_text())  # type: ignore[arg-type]
    except ImportError:
        return {}


class CodexAdapter(BaseAdapter):
    """Adapter for Codex CLI (OpenAI)."""

    home_markers = (".codex",)
    headless_binary = "codex"
    caller_env_markers = ("CODEX_THREAD_ID",)
    managed_agent_profiles = ("user:agents/{name}.toml", "project:.codex/agents/{name}.toml")
    managed_mcp_files = ("user:config.toml",)
    tracked_mcp_scopes = ("project", "user")

    @property
    def harness_name(self) -> str:
        return "codex"

    def redact_layer_content(self, display_path: str) -> bool:
        # Codex keeps MCP credentials beside other settings; upload hashes only.
        return display_path in {"user:config.toml", "project:.codex/config.toml"}

    def mcp_manifest_path(self, scope: str) -> str | None:
        return {"user": "user:config.toml", "project": "project:.codex/config.toml"}.get(scope)

    def configure_tracked_mcp(self, scope: str, directory: str, alias: str, snippet: dict) -> McpConfigUndo:
        from observal_shared.harness_registry import HARNESS_REGISTRY

        from .codex_mcp_binding import (
            codex_http_install_entry,
            install_toml_mcp,
            native_mcp_version,
            stdio_install_entry,
        )

        tested = HARNESS_REGISTRY["codex"]["mcp_native_tested_versions"]
        if native_mcp_version("codex") not in {f"codex-cli {version}" for version in tested}:
            raise ValueError("Tracked Codex install requires a tested MCP runtime")
        if self.read_installed_mcp(scope, directory, alias)[0] != "missing":
            raise ValueError("Codex MCP alias is untrusted, configured or ambiguous")
        servers = snippet.get("mcp_servers") if isinstance(snippet, dict) else None
        entry = servers.get(alias) if isinstance(servers, dict) else None
        verified = (
            codex_http_install_entry(snippet, alias)
            if isinstance(entry, dict) and "url" in entry
            else stdio_install_entry(snippet, "mcp_servers", alias)
        )
        root = Path(directory).resolve() / ".codex" if scope == "project" else Path.home() / ".codex"
        return install_toml_mcp(root / "config.toml", alias, verified)

    def read_installed_mcp(self, scope: str, directory: str | None, alias: str) -> tuple[str, dict | None]:
        import tomllib

        from .codex_mcp_binding import codex_http_install_entry, read_toml_mcp

        if scope not in {"user", "project"} or not directory:
            return "unverified", None
        home = Path.home() / ".codex"
        if Path(os.environ.get("CODEX_HOME", home)).resolve() != home.resolve():
            return "unverified", None  # The v2 layer hashes only canonical home paths.
        project = Path(directory).resolve()
        user_path = home / "config.toml"
        project_path = project / ".codex/config.toml"
        primary, other = (project_path, user_path) if scope == "project" else (user_path, project_path)
        status, entry = read_toml_mcp(primary, alias)
        shadow, _ = read_toml_mcp(other, alias)
        if shadow != "missing":
            return "unverified", None
        if status == "verified" and entry is not None and "url" in entry:
            try:
                codex_http_install_entry({"mcp_servers": {alias: entry}}, alias)
            except ValueError:
                return "unverified", None
        if scope == "project":
            # Tested Codex CLI ignores untrusted project MCP settings.
            try:
                user = tomllib.loads(user_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError):
                return "unverified", None
            projects = user.get("projects")
            trust = projects.get(str(project)) if isinstance(projects, dict) else None
            if not isinstance(trust, dict) or trust.get("trust_level") != "trusted":
                return "unverified", None
        return status, entry

    @staticmethod
    def _session_id(path: Path) -> str:
        match = re.search(r"([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})$", path.stem)
        return match.group(1) if match else path.stem

    def session_worker_environment(self) -> dict[str, str]:
        """Keep Codex's relative home anchored to the hook's working directory."""
        codex_home = os.environ.get("CODEX_HOME")
        if codex_home and not Path(codex_home).expanduser().is_absolute():
            return {"CODEX_HOME": str(Path(codex_home).expanduser().resolve())}
        return {}

    @staticmethod
    def _sessions_root(home: Path | None) -> Path:
        # Explicit home is used by isolated consumers/tests. Production hooks pass None.
        codex_home = (
            (home / ".codex")
            if home is not None
            else Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
        )
        return (codex_home / "sessions").resolve()

    @staticmethod
    def _session_meta(path: Path) -> dict:
        """Return the payload of the rollout's first line, the ``session_meta`` record."""
        try:
            with path.open(encoding="utf-8") as rollout:
                record = json.loads(rollout.readline())
        except (OSError, ValueError):
            return {}
        if not isinstance(record, dict) or record.get("type") != "session_meta":
            return {}
        payload = record.get("payload")
        return payload if isinstance(payload, dict) else {}

    @classmethod
    def _parent_thread_id(cls, path: Path) -> str | None:
        parent = cls._session_meta(path).get("parent_thread_id")
        return parent if isinstance(parent, str) and parent else None

    def attributes_by_directory(self) -> bool:
        # Pulled agents run as subagents, which write their own rollouts; the
        # parent session in that folder is the user's own Codex session.
        return False

    def session_subagent_name(self, session_jsonl: Path | None) -> str | None:
        if session_jsonl is None:
            return None
        meta = self._session_meta(session_jsonl)
        role = meta.get("agent_role")
        if meta.get("thread_source") != "subagent" or not isinstance(role, str) or not role:
            return None
        return role

    def resolve_session_source(self, event: dict, home: Path | None = None) -> SessionSource | None:
        root = self._sessions_root(home)
        if not root.is_dir():
            return None
        session_id = str(event.get("session_id") or event.get("thread_id") or "")
        candidates = list(root.rglob("*.jsonl"))
        if session_id:
            candidates = [path for path in candidates if self._session_id(path) == session_id]
        if not candidates:
            return None
        try:
            path = max(candidates, key=lambda item: item.stat().st_mtime)
        except OSError:
            return None
        return SessionSource(
            self.harness_name,
            self._session_id(path),
            path,
            cwd=str(event.get("cwd") or ""),
            parent_session_id=self._parent_thread_id(path),
        )

    def discover_session_sources(
        self,
        home: Path | None = None,
        since_hours: int = 168,
    ) -> list[SessionSource]:
        root = self._sessions_root(home)
        if not root.is_dir():
            return []
        cutoff = time.time() - since_hours * 3600
        sources: list[SessionSource] = []
        for path in root.rglob("*.jsonl"):
            try:
                modified_at = path.stat().st_mtime
                if modified_at >= cutoff:
                    sources.append(
                        SessionSource(
                            self.harness_name,
                            self._session_id(path),
                            path,
                            parent_session_id=self._parent_thread_id(path),
                        )
                    )
            except OSError:
                continue
        return sorted(sources, key=lambda source: source.path.stat().st_mtime if source.path else 0, reverse=True)

    def scan_home(self, home: Path | None = None) -> ScanResult:
        home = home or Path.home()
        codex_dir = home / ".codex"
        if not codex_dir.exists():
            return ScanResult()
        config_file = codex_dir / "config.toml"
        if not config_file.exists():
            return ScanResult()
        try:
            data = _load_toml(config_file)
            servers = data.get("mcp", {}).get("servers", {})
            mcps = []
            for srv_name, srv_config in servers.items():
                if isinstance(srv_config, dict):
                    mcps.append(
                        DiscoveredMcp(
                            name=srv_name,
                            command=srv_config.get("command"),
                            args=srv_config.get("args", []),
                            url=srv_config.get("url"),
                            description=f"Codex MCP: {srv_name}",
                            source="codex:global",
                        )
                    )
            return ScanResult(mcps=mcps)
        except Exception:
            return ScanResult()

    def scan_project(self, project_dir: Path) -> ScanResult:
        config_file = project_dir / ".codex" / "config.toml"
        if not config_file.exists():
            return ScanResult()
        try:
            data = _load_toml(config_file)
            servers = data.get("mcp", {}).get("servers", {})
            mcps = []
            for srv_name, srv_config in servers.items():
                if isinstance(srv_config, dict):
                    mcps.append(
                        DiscoveredMcp(
                            name=srv_name,
                            command=srv_config.get("command"),
                            args=srv_config.get("args", []),
                            url=srv_config.get("url"),
                            description=f"Codex project MCP: {srv_name}",
                            source="codex:project",
                        )
                    )
            return ScanResult(mcps=mcps)
        except Exception:
            return ScanResult()

    def discover_home(self, home: Path | None = None) -> AdapterDiscoveryResult:
        home = home or Path.home()
        return self._discover_codex_root(home / ".codex", DiscoveryScope.USER, home=home)

    def discover_project(self, project_dir: Path) -> AdapterDiscoveryResult:
        return self._discover_codex_root(
            project_dir / ".codex",
            DiscoveryScope.PROJECT,
            project_dir=project_dir,
        )

    def _discover_codex_root(
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
        config_path = root / "config.toml"
        content = scanner.walker.read_text(config_path)
        if content is not None:
            try:
                import tomllib

                data = tomllib.loads(content)
            except (tomllib.TOMLDecodeError, UnicodeError):
                scanner.diagnostic(DiagnosticCode.METADATA_MALFORMED, config_path, "malformed TOML discovery metadata")
            else:
                servers = data.get("mcp_servers")
                if servers is None:
                    mcp = data.get("mcp", {})
                    servers = mcp.get("servers", {}) if isinstance(mcp, dict) else {}
                scanner.add_mcps(
                    servers,
                    config_path,
                    source=f"codex:{'global' if scope is DiscoveryScope.USER else 'project'}",
                    description_prefix="Codex MCP",
                )
        return scanner.finish()

    def get_hook_spec(self) -> HookSpec:
        return HookSpec(
            events=["UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"],
            format="command",
            markers=["observal", "OBSERVAL", "session_push --harness codex"],
        )

    def generate_hook_config(
        self,
        observal_url: str,
        api_key: str,
        agent_id: str | None = None,
    ) -> dict:
        from observal_cli.harness_specs.codex_hooks_spec import build_codex_hooks

        return build_codex_hooks()

    def detect_hooks(self, config_dir: Path) -> str:
        """Check ~/.codex/hooks.json for Observal markers."""
        home = Path.home()
        hooks_file = home / ".codex" / "hooks.json"
        if not hooks_file.exists():
            return "missing"
        try:
            data = json.loads(hooks_file.read_text())
        except (json.JSONDecodeError, OSError):
            return "missing"
        hooks = data.get("hooks", {})
        if not hooks:
            return "missing"
        for _evt, groups in hooks.items():
            if not isinstance(groups, list):
                continue
            for group in groups:
                if not isinstance(group, dict):
                    continue
                for h in group.get("hooks", []):
                    if isinstance(h, dict):
                        cmd = h.get("command", "")
                        if any(m in cmd for m in _OBSERVAL_HOOK_MARKERS):
                            return "installed"
        return "missing"

    def extract_mcp_servers(self, config: dict) -> dict:
        mcp = config.get("mcp", {})
        if isinstance(mcp, dict) and isinstance(mcp.get("servers"), dict):
            return mcp["servers"]
        return super().extract_mcp_servers(config)

    def patch_hooks(self, dry_run: bool) -> bool:
        from observal_cli.cmd_doctor import _patch_codex

        return _patch_codex(dry_run)

    def cleanup_hooks(self, dry_run: bool) -> bool:
        from observal_cli.cmd_doctor import _cleanup_codex

        return _cleanup_codex(dry_run)

    # ── Headless delegation ──────────────────────────────────

    def _headless_command(self, request: HeadlessRequest) -> HeadlessPlan:
        # Codex CLI reference: exec, --cd, --sandbox workspace-write,
        # --skip-git-repo-check, --output-last-message. exec has no flag to pick
        # a custom agent, so its instructions are inlined into the prompt.
        output = request.scratch_dir / "last-message.txt"
        argv = [
            "codex",
            "exec",
            "--cd",
            str(request.workdir),
            "--sandbox",
            "workspace-write",
            "--skip-git-repo-check",
            "--output-last-message",
            str(output),
        ]
        if request.model:
            argv += ["--model", request.model]
        return HeadlessPlan(argv=[*argv, "--", inline_agent_prompt(request)], output_file=output)


register_adapter(CodexAdapter())
