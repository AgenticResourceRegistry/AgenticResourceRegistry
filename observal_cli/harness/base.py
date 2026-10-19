# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 EuanTop <euan@mail.bnu.edu.cn>
# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Base adapter with feature-flag gating from harness_Registry.

Methods automatically raise NotSupportedError when the harness lacks
the required feature in its harness_Registry entry. Subclasses override
methods they support; the feature gate runs before the override.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from observal_cli.harness.protocol import (
    METHOD_FEATURE_MAP,
    BundledSkillPlan,
    HeadlessPlan,
    HeadlessRequest,
    HeadlessResult,
    HookSpec,
    NotSupportedError,
    ScanResult,
    SessionSource,
)

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def inline_agent_prompt(request: HeadlessRequest) -> str:
    """Prompt for harnesses that cannot select an agent profile by name."""
    instructions = request.instructions.strip()
    if not instructions:
        return request.message
    return (
        f"You are acting as the agent `{request.agent_name}`. Follow its instructions.\n\n"
        f"<agent-instructions>\n{instructions}\n</agent-instructions>\n\n"
        f"<task>\n{request.message}\n</task>"
    )


def parse_json_result(plan: HeadlessPlan, stdout: str) -> HeadlessResult | None:
    """Parse the single ``{"type": "result", ...}`` object Claude Code and Cursor print in json mode."""
    import json

    lines = [line for line in stdout.strip().splitlines() if line.strip()]
    if not lines:
        return None
    try:
        data = json.loads(lines[-1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "result" not in data:
        return None
    text = str(data.get("result") or "").strip()
    error = (text or "The delegated session reported an error.") if data.get("is_error") else None
    return HeadlessResult(text=text, session_id=data.get("session_id") or plan.session_id, error=error)


def _check_headless(harness_name: str) -> None:
    from observal_shared.harness_registry import HARNESS_REGISTRY

    if not HARNESS_REGISTRY.get(harness_name, {}).get("headless_run"):
        raise NotSupportedError(harness_name, "headless_command")


if TYPE_CHECKING:
    from observal_cli.discovery.models import AdapterDiscoveryResult


def _get_features(harness_name: str) -> set[str]:
    """Look up the feature set for an harness from the registry."""
    from observal_shared.harness_registry import HARNESS_REGISTRY

    spec = HARNESS_REGISTRY.get(harness_name, {})
    return spec.get("capabilities", set())


def _check_feature(harness_name: str, method_name: str) -> None:
    """Raise NotSupportedError if the harness lacks the required feature for a method."""
    required_feature = METHOD_FEATURE_MAP.get(method_name)
    if required_feature is None:
        return  # No feature gate for this method
    features = _get_features(harness_name)
    if required_feature not in features:
        raise NotSupportedError(harness_name, method_name)


class BaseAdapter:
    """Base class providing feature-gated defaults for all protocol methods.

    On each call, checks the harness_Registry feature set. If the harness lacks
    the required feature, raises NotSupportedError before reaching the
    method body. Subclasses override methods they support.
    """

    home_markers: tuple[str, ...] = ()
    # Executable that headless_command launches; delegation checks it is on PATH.
    headless_binary: str | None = None
    # headless_command passes the task on stdin, so its length and cmd.exe quoting never matter.
    headless_task_on_stdin = False
    managed_agent_profiles: tuple[str, ...] = ()
    managed_skills: tuple[str, ...] = ()
    managed_mcp_files: tuple[str, ...] = ()

    @property
    def harness_name(self) -> str:
        raise NotImplementedError("Subclasses must define harness_name")

    def resolve_home_dir(self) -> Path | None:
        """Return the harness home directory, for adapters that resolve their own layout.

        ``None`` means the caller should fall back to its static path table.
        """
        return None

    def scan_home(self, home: Path | None = None) -> ScanResult:
        _check_feature(self.harness_name, "scan_home")
        return ScanResult()

    def scan_project(self, project_dir: Path) -> ScanResult:
        _check_feature(self.harness_name, "scan_project")
        return ScanResult()

    def discover_home(self, home: Path | None = None) -> AdapterDiscoveryResult:
        """No fallback to unbounded legacy scanning in local inventory."""
        from observal_cli.discovery.models import AdapterDiscoveryResult

        return AdapterDiscoveryResult()

    def discover_project(self, project_dir: Path) -> AdapterDiscoveryResult:
        """Adapters explicitly opt in to bounded local inventory."""
        from observal_cli.discovery.models import AdapterDiscoveryResult

        return AdapterDiscoveryResult()

    def get_hook_spec(self) -> HookSpec:
        _check_feature(self.harness_name, "get_hook_spec")
        return HookSpec()

    def generate_hook_config(
        self,
        observal_url: str,
        api_key: str,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        _check_feature(self.harness_name, "generate_hook_config")
        raise NotSupportedError(self.harness_name, "generate_hook_config")

    def detect_hooks(self, config_dir: Path) -> str:
        _check_feature(self.harness_name, "detect_hooks")
        return "none"

    def is_installed(self, home: Path | None = None) -> bool:
        """Return whether this harness has a detectable home config marker."""
        if not self.home_markers:
            return False
        home = home or Path.home()
        for marker in self.home_markers:
            if any(char in marker for char in "*?["):
                if any(path.exists() for path in home.glob(marker)):
                    return True
            elif (home / marker).exists():
                return True
        return False

    def plan_bundled_skill_install(
        self,
        skill_name: str,
        home: Path,
        installed_harnesses: frozenset[str],
    ) -> BundledSkillPlan | None:
        """Plan the registry-defined user-scope skill destination."""
        from observal_shared.harness_registry import HARNESS_REGISTRY

        user_path = (HARNESS_REGISTRY[self.harness_name].get("skills") or {}).get("user")
        if not user_path:
            return None
        resolved = user_path.replace("{name}", skill_name)
        target = home / resolved[2:] if resolved.startswith("~/") else Path(resolved)
        return BundledSkillPlan(target=target)

    def resolve_session_source(self, event: dict[str, Any], home: Path | None = None) -> SessionSource | None:
        """Resolve a hook payload to a session source when the harness supports it."""
        return None

    def discover_session_sources(
        self,
        home: Path | None = None,
        since_hours: int = 168,
    ) -> list[SessionSource]:
        """Return recent session sources; harness adapters opt in as they are migrated."""
        return []

    def resolve_session_agent_identity(
        self,
        session_jsonl: Path | None,
        cwd: str,
    ) -> tuple[str | None, str | None] | None:
        """Resolve a harness-specific session identity, or defer to shared resolution."""
        return None

    def should_capture_session(self, source: SessionSource, home: Path | None = None) -> bool:
        """Return whether a discovered session is collected by configured hooks.

        Collection is independent of agent attribution. Shared hooks may fire
        for ordinary sessions using MCPs and skills; those sessions remain
        unattributed when no registry agent matches.
        """
        return True

    def related_session_sources(self, source: SessionSource, home: Path | None = None) -> list[SessionSource]:
        """Return child sources when a harness stores them separately."""
        return []

    def session_extra_fields(
        self,
        source: SessionSource,
        event: dict[str, Any],
        final: bool,
        home: Path | None = None,
    ) -> dict[str, Any]:
        """Return optional harness-specific ingest fields."""
        return {}

    def session_extra_records(
        self,
        source: SessionSource,
        event: dict[str, Any],
        final: bool,
        home: Path | None = None,
    ) -> tuple[str, ...]:
        """Return optional synthetic records from a hook payload."""
        return ()

    def defer_session_delivery(self) -> bool:
        """Return whether the harness requires detached network delivery."""
        return False

    def aged_recovery_final(self) -> bool:
        """Finalize aged session sources by default."""
        return True

    def reports_sessions_outside_pull(self) -> bool:
        """Session telemetry comes from the hook files a pull writes."""
        return False

    def is_session_final(self, event: dict[str, Any]) -> bool:
        """Recognize common final lifecycle event names."""
        event_name = str(
            event.get("hook_event_name") or event.get("hookEventName") or event.get("event") or event.get("type") or ""
        )
        return event_name.lower() in {"stop", "sessionend", "session_end", "sessionshutdown"}

    def saved_model(self, agent_detail: dict | None) -> str | None:
        if not agent_detail:
            return None
        values = agent_detail.get("models_by_harness")
        candidate = values.get(self.harness_name) if isinstance(values, dict) else None
        return candidate.strip() if isinstance(candidate, str) and candidate.strip() else None

    def apply_install_options(self, options: dict, tools: str | None) -> None:
        return None

    def rewrite_hooks(self, content: dict, agent_id: str) -> dict:
        return content

    def rewrite_agent_profile(self, content: dict, agent_id: str) -> dict:
        return content

    def allow_home_agent_profile(self, is_user_scope: bool) -> bool:
        return is_user_scope

    def persist_active_agent(self, agent_id: str, name: str, version: str | None) -> None:
        return None

    def extract_mcp_servers(self, config: dict) -> dict:
        from observal_shared.harness_registry import HARNESS_REGISTRY

        key = HARNESS_REGISTRY[self.harness_name].get("mcp_servers_key", "mcpServers")
        value = config
        for part in key.split("."):
            value = value.get(part, {}) if isinstance(value, dict) else {}
        if value:
            return value
        for fallback in ("mcpServers", "servers"):
            if isinstance(config.get(fallback), dict):
                return config[fallback]
        return {
            name: entry
            for name, entry in config.items()
            if isinstance(entry, dict) and any(field in entry for field in ("command", "url", "type"))
        }

    def patch_hooks(self, dry_run: bool) -> bool:
        return False

    def cleanup_hooks(self, dry_run: bool) -> bool:
        return False

    def requires_explicit_agent_id(self) -> bool:
        return False

    def headless_command(self, request: HeadlessRequest) -> HeadlessPlan:
        _check_headless(self.harness_name)
        return self._headless_command(request)

    def _headless_command(self, request: HeadlessRequest) -> HeadlessPlan:
        raise NotSupportedError(self.harness_name, "headless_command")

    def parse_headless_output(self, plan: HeadlessPlan, stdout: str) -> HeadlessResult:
        """Plain-text harnesses: the answer is stdout, or the file the harness wrote it to."""
        if plan.output_file is not None and plan.output_file.is_file():
            text = plan.output_file.read_text(encoding="utf-8", errors="replace")
        else:
            text = ANSI_RE.sub("", stdout)
        return HeadlessResult(text=text.strip(), session_id=plan.session_id)

    def get_observal_managed_files(self, lockfile_data: dict, project_dir: str | None = None) -> set[str]:
        """Return layer snapshot display paths managed by Observal for this harness."""
        managed: set[str] = set()
        harness_section = lockfile_data.get("harnesses", {}).get(self.harness_name)
        if harness_section is None:
            harness_section = lockfile_data.get("ides", {}).get(self.harness_name, {})

        for agent in harness_section.get("agents", []):
            agent_name = agent.get("name", "")
            if agent_name:
                managed.update(self._format_managed_paths(self.managed_agent_profiles, agent_name))

            for component in agent.get("components", []):
                managed.update(self._managed_component_files(component.get("type", ""), component.get("name", "")))

        for item in harness_section.get("standalone", []):
            managed.update(self._managed_component_files(item.get("type", ""), item.get("name", "")))

        return managed

    def _managed_component_files(self, component_type: str, component_name: str) -> set[str]:
        if not component_name:
            return set()
        if component_type == "skill":
            return self._format_managed_paths(self.managed_skills, component_name)
        if component_type == "mcp":
            return self._format_managed_paths(self.managed_mcp_files, component_name)
        return set()

    @staticmethod
    def _format_managed_paths(patterns: tuple[str, ...], name: str) -> set[str]:
        return {pattern.format(name=name) for pattern in patterns}
