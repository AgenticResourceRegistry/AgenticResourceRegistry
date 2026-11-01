# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 EuanTop <euan@mail.bnu.edu.cn>
# SPDX-License-Identifier: Apache-2.0

"""CLI-side harness adapter protocol, registry, and orchestrator.

Each harness has a self-contained adapter module implementing the HarnessAdapter
protocol. Adapters auto-register via module-level register_adapter() calls
when their module is imported (triggered by load_all).
"""

from __future__ import annotations

from observal_cli.harness.protocol import (
    METHOD_FEATURE_MAP,
    BundledSkillPlan,
    DiscoveredAgent,
    DiscoveredHook,
    DiscoveredMcp,
    DiscoveredSkill,
    HarnessAdapter,
    HookSpec,
    NotSupportedError,
    ScanResult,
    SessionSource,
)
from observal_shared.harness_registry import HARNESS_REGISTRY

__all__ = [
    "METHOD_FEATURE_MAP",
    "BundledSkillPlan",
    "DiscoveredAgent",
    "DiscoveredHook",
    "DiscoveredMcp",
    "DiscoveredSkill",
    "HarnessAdapter",
    "HookSpec",
    "NotSupportedError",
    "ScanResult",
    "SessionSource",
    "ensure_loaded",
    "get_adapter",
    "get_all_adapters",
    "register_adapter",
]


# ── Adapter Registry ──────────────────────────────────────────────

_ADAPTER_REGISTRY: dict[str, HarnessAdapter] = {}


def register_adapter(adapter: HarnessAdapter) -> None:
    """Register an harness adapter instance.

    Raises RuntimeError if the adapter is missing required protocol methods.
    """
    required = (
        "harness_name",
        "scan_home",
        "is_installed",
        "plan_bundled_skill_install",
        "scan_project",
        "get_hook_spec",
        "generate_hook_config",
        "detect_hooks",
        "resolve_session_source",
        "session_worker_environment",
        "discover_session_sources",
        "should_capture_session",
        "related_session_sources",
        "session_extra_fields",
        "session_extra_records",
        "defer_session_delivery",
        "is_session_final",
        "get_observal_managed_files",
    )
    for method in required:
        if not hasattr(adapter, method) or not callable(getattr(adapter, method, None)):
            if method == "harness_name":
                continue  # property, not callable
            raise RuntimeError(f"Adapter {type(adapter).__name__} missing required method: {method}")
    _ADAPTER_REGISTRY[adapter.harness_name] = adapter


def get_adapter(harness: str) -> HarnessAdapter:
    """Look up an adapter by harness name."""
    adapter = _ADAPTER_REGISTRY.get(harness)
    if adapter is None:
        registered = sorted(_ADAPTER_REGISTRY.keys())
        raise KeyError(f"No adapter registered for harness '{harness}'. Available: {', '.join(registered)}")
    return adapter


def get_all_adapters() -> dict[str, HarnessAdapter]:
    """Return all registered adapters."""
    return dict(_ADAPTER_REGISTRY)


def ensure_loaded() -> None:
    """Ensure all adapter modules have been imported and registered."""
    if len(_ADAPTER_REGISTRY) < len(HARNESS_REGISTRY):
        import observal_cli.harness.load_all  # noqa: F401
