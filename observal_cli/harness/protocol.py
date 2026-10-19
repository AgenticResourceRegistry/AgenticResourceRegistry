# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 EuanTop <euan@mail.bnu.edu.cn>
# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""harness adapter protocol definition.

This module defines the HarnessAdapter protocol that all harness adapters must
satisfy, along with the data types used across the adapter interface.

Feature-flag gating: Each protocol method maps to an harness_Registry feature.
The BaseAdapter enforces that methods raise NotSupportedError when the
harness lacks the required feature.

Method → Feature mapping:
    generate_hook_config, detect_hooks, get_hook_spec → "hooks"
    scan_home, scan_project                            → "mcp_servers"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pathlib import Path

    from observal_cli.discovery.models import AdapterDiscoveryResult


# ── Feature → Method mapping ──────────────────────────────────────

METHOD_FEATURE_MAP: dict[str, str] = {
    "generate_hook_config": "hooks",
    "detect_hooks": "hooks",
    "get_hook_spec": "hooks",
    "scan_home": "mcp_servers",
    "scan_project": "mcp_servers",
}


# ── Data Types ────────────────────────────────────────────────────


class DiscoveredMcp:
    """An MCP server discovered during scanning."""

    def __init__(
        self,
        name: str,
        command: str | None,
        args: list[str],
        url: str | None,
        description: str,
        source: str,
    ):
        self.name = name
        self.command = command
        self.args = args
        self.url = url
        self.description = description
        self.source = source

    def display_cmd(self) -> str:
        if self.url:
            return self.url[:60]
        cmd = f"{self.command or '?'} {' '.join(self.args[:3])}"
        return cmd[:60] + "..." if len(cmd) > 60 else cmd


class DiscoveredSkill:
    """A skill discovered during scanning."""

    def __init__(self, name: str, description: str, source: str, task_type: str = "general"):
        self.name = name
        self.description = description
        self.source = source
        self.task_type = task_type


class DiscoveredHook:
    """A hook discovered during scanning."""

    def __init__(
        self,
        name: str,
        event: str,
        handler_type: str,
        handler_config: dict,
        description: str,
        source: str,
    ):
        self.name = name
        self.event = event
        self.handler_type = handler_type
        self.handler_config = handler_config
        self.description = description
        self.source = source


class DiscoveredAgent:
    """An agent discovered during scanning."""

    def __init__(self, name: str, description: str, model_name: str, prompt: str, source_file: str):
        self.name = name
        self.description = description
        self.model_name = model_name
        self.prompt = prompt
        self.source_file = source_file


@dataclass
class ScanResult:
    """Aggregated scan output from an harness adapter."""

    mcps: list[DiscoveredMcp] = field(default_factory=list)
    skills: list[DiscoveredSkill] = field(default_factory=list)
    hooks: list[DiscoveredHook] = field(default_factory=list)
    agents: list[DiscoveredAgent] = field(default_factory=list)


@dataclass
class HookSpec:
    """Hook specification for an harness."""

    events: list[str] = field(default_factory=list)
    format: str = ""  # "command", "http", "plugin"
    markers: list[str] = field(default_factory=list)
    env_vars: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class BundledSkillPlan:
    """Destination and safe reuse or cleanup candidates for one bundled skill."""

    target: Path
    reuse_candidates: tuple[Path, ...] = ()
    cleanup_candidates: tuple[Path, ...] = ()


@dataclass(frozen=True)
class SessionSource:
    """A harness session source resolved from a hook or recent-session scan."""

    harness: str
    session_id: str
    path: Path | None = None
    cwd: str = ""
    cursor_key: str | None = None
    parent_session_id: str | None = None

    @property
    def checkpoint_key(self) -> str:
        """Return the local checkpoint key for this source."""
        return self.cursor_key or self.session_id


@dataclass(frozen=True)
class HeadlessRequest:
    """One delegated task for a harness to run non-interactively (ADR 0002).

    The agent has already been materialized into ``workdir`` with project
    scope, so harnesses that select agents by name find it there. Harnesses
    without an agent flag get ``instructions`` inlined into the prompt.
    """

    agent_name: str
    instructions: str
    message: str
    workdir: Path
    scratch_dir: Path
    session_id: str
    mcp_servers: dict = field(default_factory=dict)
    model: str | None = None
    # The caller's own directory, for harnesses that gate project files on a trust decision made there.
    source_dir: Path | None = None


@dataclass(frozen=True)
class HeadlessPlan:
    """How to launch the child process, and where its answer will be."""

    argv: list[str]
    stdin: str | None = None
    output_file: Path | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class HeadlessResult:
    text: str
    session_id: str | None = None
    error: str | None = None


class NotSupportedError(Exception):
    """Raised when an harness does not support a requested operation."""

    def __init__(self, harness_name: str, method_name: str):
        self.harness_name = harness_name
        self.method_name = method_name
        super().__init__(f"{harness_name} does not support {method_name}")


# ── Protocol ──────────────────────────────────────────────────────


@runtime_checkable
class HarnessAdapter(Protocol):
    """Protocol defining the interface for CLI-side harness adapters.

    Each harness adapter implements this protocol to handle scanning,
    hook specs, and config file operations specific to that harness.
    """

    @property
    def harness_name(self) -> str:
        """Canonical harness name (e.g. 'claude-code', 'cursor')."""
        ...

    def scan_home(self, home: Path | None = None) -> ScanResult:
        """Scan the user's home directory for this harness's configuration.

        Args:
            home: Override home directory (defaults to Path.home()).

        Returns:
            ScanResult with discovered MCPs, skills, hooks, and agents.

        Raises:
            NotSupportedError: If this harness does not have mcp_servers feature.
        """
        ...

    def is_installed(self, home: Path | None = None) -> bool:
        """Return whether this harness has a detectable home config marker.

        Args:
            home: Override home directory (defaults to Path.home()).
        """
        ...

    def plan_bundled_skill_install(
        self,
        skill_name: str,
        home: Path,
        installed_harnesses: frozenset[str],
    ) -> BundledSkillPlan | None:
        """Return the destination and migration paths for one bundled skill."""
        ...

    def scan_project(self, project_dir: Path) -> ScanResult:
        """Scan a project directory for this harness's configuration.

        Args:
            project_dir: The project root to scan.

        Returns:
            ScanResult with discovered MCPs, skills, hooks, and agents.

        Raises:
            NotSupportedError: If this harness does not have mcp_servers feature.
        """
        ...

    def discover_home(self, home: Path | None = None) -> AdapterDiscoveryResult:
        """Return rich user-scope evidence and expected-data diagnostics."""
        ...

    def discover_project(self, project_dir: Path) -> AdapterDiscoveryResult:
        """Return rich project-scope evidence and expected-data diagnostics."""
        ...

    def get_hook_spec(self) -> HookSpec:
        """Return the hook specification for this harness.

        Returns:
            HookSpec describing available hooks, or empty HookSpec
            if this harness does not support hooks.

        Raises:
            NotSupportedError: If this harness does not have hooks feature.
        """
        ...

    def generate_hook_config(
        self,
        observal_url: str,
        api_key: str,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        """Generate hook configuration for telemetry collection.

        Args:
            observal_url: The Observal server URL.
            api_key: User's API key.
            agent_id: Optional agent ID to tag telemetry.

        Returns:
            Dict representing the hook config to write.

        Raises:
            NotSupportedError: If this harness does not have hooks feature.
        """
        ...

    def detect_hooks(self, config_dir: Path) -> str:
        """Detect whether Observal hooks are already installed.

        Args:
            config_dir: harness-specific config directory to check.

        Returns:
            Status string: "installed", "partial", "missing", or "none".

        Raises:
            NotSupportedError: If this harness does not have hooks feature.
        """
        ...

    def resolve_session_source(self, event: dict[str, Any], home: Path | None = None) -> SessionSource | None:
        """Resolve a harness hook payload to its local session source."""
        ...

    def discover_session_sources(
        self,
        home: Path | None = None,
        since_hours: int = 168,
    ) -> list[SessionSource]:
        """Discover recently modified local session sources."""
        ...

    def should_capture_session(self, source: SessionSource, home: Path | None = None) -> bool:
        """Return whether a discovered session is in scope for telemetry."""
        ...

    def related_session_sources(self, source: SessionSource, home: Path | None = None) -> list[SessionSource]:
        """Return child/subagent sources associated with a resolved session."""
        ...

    def session_extra_fields(
        self,
        source: SessionSource,
        event: dict[str, Any],
        final: bool,
        home: Path | None = None,
    ) -> dict[str, Any]:
        """Return harness-specific ingest metadata for a session wake-up."""
        ...

    def session_extra_records(
        self,
        source: SessionSource,
        event: dict[str, Any],
        final: bool,
        home: Path | None = None,
    ) -> tuple[str, ...]:
        """Return synthetic source records supplied by a harness hook."""
        ...

    def defer_session_delivery(self) -> bool:
        """Return whether network drain must run outside the hook process."""
        ...

    def aged_recovery_final(self) -> bool:
        """Return whether aged session recovery should finalize the source."""
        ...

    def reports_sessions_outside_pull(self) -> bool:
        """Return whether sessions are reported by telemetry a pull does not write.

        Harnesses whose telemetry is an installed extension rather than
        written hook files report sessions for any pulled agent.
        """
        ...

    def is_session_final(self, event: dict[str, Any]) -> bool:
        """Return whether a hook payload marks its session final."""
        ...

    def saved_model(self, agent_detail: dict | None) -> str | None:
        """Return the saved model for this harness."""
        ...

    def apply_install_options(self, options: dict, tools: str | None) -> None:
        """Apply harness-specific CLI install options."""
        ...

    def rewrite_hooks(self, content: dict, agent_id: str) -> dict:
        """Rewrite downloaded hook configuration before writing it."""
        ...

    def rewrite_agent_profile(self, content: dict, agent_id: str) -> dict:
        """Rewrite a downloaded structured agent profile before writing it."""
        ...

    def allow_home_agent_profile(self, is_user_scope: bool) -> bool:
        """Return whether an agent profile may resolve under the home directory."""
        ...

    def persist_active_agent(self, agent_id: str, name: str, version: str | None) -> None:
        """Persist harness-specific global agent attribution after pull."""
        ...

    def extract_mcp_servers(self, config: dict) -> dict:
        """Extract MCP server entries from a harness configuration."""
        ...

    def patch_hooks(self, dry_run: bool) -> bool:
        """Install this harness's telemetry hooks."""
        ...

    def cleanup_hooks(self, dry_run: bool) -> bool:
        """Remove this harness's telemetry hooks."""
        ...

    def requires_explicit_agent_id(self) -> bool:
        """Return whether sessions must not fall back to name or cwd attribution."""
        ...

    def headless_command(self, request: HeadlessRequest) -> HeadlessPlan:
        """Build the argv that runs a materialized agent on one task and exits.

        Raises:
            NotSupportedError: If the harness has no verified headless mode
                (``headless_run`` runtime fact).
        """
        ...

    def parse_headless_output(self, plan: HeadlessPlan, stdout: str) -> HeadlessResult:
        """Extract the child's final answer (and session id, when known) from its output."""
        ...

    def get_observal_managed_files(self, lockfile_data: dict, project_dir: str | None = None) -> set[str]:
        """Return layer snapshot display paths managed by Observal for this harness.

        Args:
            lockfile_data: Parsed Observal lockfile content.
            project_dir: Optional project directory for project-scoped installs.

        Returns:
            Display paths that correspond to Observal-installed files.
        """
        ...
