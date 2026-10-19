# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Codex CLI harness adapter."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

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

    @property
    def harness_name(self) -> str:
        return "codex"

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

    def _source(self, path: Path) -> SessionSource:
        """Use only metadata recorded by this rollout, never the hook process cwd."""
        meta = self._session_meta(path)
        return SessionSource(
            self.harness_name,
            self._session_id(path),
            path,
            cwd=meta.get("cwd") if isinstance(meta.get("cwd"), str) else "",
            parent_session_id=self._parent_thread_id(path),
        )

    def resolve_session_source(self, event: dict, home: Path | None = None) -> SessionSource | None:
        root = self._sessions_root(home)
        if not root.is_dir():
            return None
        session_id = str(event.get("session_id") or event.get("thread_id") or "")
        transcript = event.get("transcript_path") or event.get("transcriptPath")
        if not session_id and not transcript:
            return None
        candidates = list(root.rglob("*.jsonl"))
        if session_id:
            candidates = [path for path in candidates if self._session_id(path) == session_id]
        if transcript:
            candidates = [path for path in candidates if str(path.resolve()) == str(Path(transcript).resolve())]
        if not candidates:
            return None
        try:
            path = max(candidates, key=lambda item: item.stat().st_mtime)
        except OSError:
            return None
        return self._source(path)

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
                    sources.append(self._source(path))
            except OSError:
                continue
        return sorted(sources, key=lambda source: source.path.stat().st_mtime if source.path else 0, reverse=True)

    def outbox_provenance(self, source: SessionSource, payload: dict) -> dict | None:
        """Record only verified delegated identity or explicit lack of identity."""
        from observal_cli.delegation.tasks import delegated_agent

        delegated = delegated_agent("", (source.session_id,))
        identity = "unattributed"
        if payload.get("agent_id"):
            if delegated != (payload.get("agent_id"), payload.get("agent_version")):
                return None
            identity = "delegation"
        return {
            "session_id": source.session_id,
            "path": os.path.abspath(source.path) if source.path else "",
            "source_kind": "codex-rollout",
            "identity": identity,
            "agent_id": payload.get("agent_id"),
            "agent_version": payload.get("agent_version"),
        }

    def allows_ambient_agent_identity(self) -> bool:
        return False

    def validate_outbox_provenance(self, item) -> bool:
        """Only a session-bound delegated identity can authorize attributed replay."""
        proof = item.provenance
        if proof is None:
            return not item.payload.get("agent_id") and not item.payload.get("agent_version")
        if not isinstance(proof, dict) or proof.get("session_id") != item.session_id:
            return False
        path = proof.get("path")
        if proof.get("source_kind") != "codex-rollout" or not isinstance(path, str) or not path.endswith(".jsonl"):
            return False
        # Check the lexical Codex location before resolving links: ~/.codex
        # itself may be a symlink to another volume.
        source_path = Path(path)
        if (
            self._session_id(source_path) != item.session_id
            or not any(parent.name == "sessions" and parent.parent.name == ".codex" for parent in source_path.parents)
            or not source_path.is_absolute()
            or os.path.abspath(path) != path
            or item.payload.get("session_id") != item.session_id
            or item.payload.get("harness") != item.harness
        ):
            return False
        try:
            if item.end_offset < 0 or item.end_offset > source_path.stat().st_size:
                return False
            source = self._source(source_path)
            # Read only the queued byte prefix. Later appends cannot invalidate
            # a final batch, and the proof need not scan a growing rollout.
            with source_path.open("rb") as transcript:
                prefix = transcript.read(item.end_offset)
            if len(prefix) != item.end_offset:
                return False
            consumed = prefix
            lines = []
            offsets = []
            position = 0
            for encoded in consumed.splitlines(keepends=True):
                position += len(encoded)
                line = encoded.rstrip(b"\r\n").decode("utf-8", errors="replace")
                if line.strip():
                    lines.append(line)
                    offsets.append(position)
            start = item.start_line
            posted = item.payload.get("lines") or []
            posted_offsets = item.payload.get("end_byte_offsets") or []
            if (
                start < 0
                or start > len(lines)
                or not isinstance(posted, list)
                or lines[start : start + len(posted)] != posted
                or len(posted_offsets) != len(posted)
                or any(offset != offsets[start + index] for index, offset in enumerate(posted_offsets[:-1]))
                or (posted and posted_offsets[-1] != item.end_offset)
                or (posted and item.end_offset < offsets[start + len(posted) - 1])
                or (posted and start + len(posted) < len(offsets) and item.end_offset >= offsets[start + len(posted)])
                or (posted and not consumed.endswith(b"\n"))
            ):
                return False
            if item.payload.get("session_hash") is not None:
                count = item.payload.get("hashed_line_count")
                if not isinstance(count, int) or count != len(lines):
                    return False
                hasher = hashlib.sha256()
                for line in lines[:count]:
                    hasher.update(hashlib.sha256(line.encode("utf-8", errors="replace")).hexdigest().encode())
                    hasher.update(b"\n")
                if item.payload["session_hash"] != hasher.hexdigest():
                    return False
            # A rollout's own metadata must not contradict the path identity.
            first = json.loads(lines[0]) if lines else {}
            meta = first.get("payload") if first.get("type") == "session_meta" else None
            if isinstance(meta, dict) and meta.get("id") and meta["id"] != item.session_id:
                return False
        except (OSError, ValueError, IndexError, TypeError, AttributeError):
            return False
        identity = proof.get("identity")
        if identity == "unattributed":
            return (
                not item.payload.get("agent_id")
                and not item.payload.get("agent_version")
                and not proof.get("agent_id")
                and not proof.get("agent_version")
            )
        if identity != "delegation" or not item.payload.get("agent_id"):
            return False
        from observal_cli.delegation.tasks import delegated_agent

        delegated = delegated_agent("", (item.session_id,))
        return (
            delegated == (item.payload.get("agent_id"), item.payload.get("agent_version"))
            and proof.get("agent_id") == item.payload.get("agent_id")
            and proof.get("agent_version") == item.payload.get("agent_version")
            and source.session_id == item.session_id
        )

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
