# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Infer harness feature requirements and compatible harnesses from agent components."""

from __future__ import annotations

from loguru import logger as optic

from schemas.constants import HARNESS_CAPABILITIES


def infer_required_features(
    agent,
    skill_listings: dict | None = None,
) -> list[str]:
    """Determine which harness features an agent requires based on its components.

    Args:
        agent: Agent model instance with `components` and `external_mcps`.
        skill_listings: optional ``{component_id: SkillListing}`` map used
            to inspect ``slash_command`` and ``delivery_mode`` on skill components.

    Returns:
        Sorted list of required feature strings (e.g. ``["mcp_servers", "rules"]``).
    """
    optic.trace("agent={}, skill_listings={}", agent, skill_listings)
    features: set[str] = set()
    skill_listings = skill_listings or {}

    for comp in getattr(agent, "components", []):
        if comp.component_type == "mcp":
            features.add("mcp_servers")
        elif comp.component_type == "hook":
            features.add("hooks")
        elif comp.component_type == "skill":
            listing = skill_listings.get(comp.component_id)
            # A slash command needs native skill support, and so does a complete
            # skill folder: the install refuses harnesses without a skills directory.
            if listing and (
                getattr(listing, "slash_command", None) or getattr(listing, "delivery_mode", None) == "registry_direct"
            ):
                features.add("skills")

    if getattr(agent, "external_mcps", None):
        features.add("mcp_servers")

    return sorted(features)


def compute_supported_harnesses(required_features: list[str]) -> list[str]:
    """Return sorted harness names that support all *required_features*."""
    optic.trace("required_features={}", required_features)
    required = set(required_features)
    return sorted(harness for harness, capabilities in HARNESS_CAPABILITIES.items() if required.issubset(capabilities))


class NoCompatibleHarnessError(ValueError):
    """None of the declared harnesses can run the agent's components."""


def unsupported_harness_reason(harness: str, required_features: list[str]) -> str:
    """Say why *harness* cannot run an agent, e.g. ``"cursor has no skills support"``."""
    if harness not in HARNESS_CAPABILITIES:
        return f"{harness} is not a known harness"
    missing = sorted(set(required_features) - HARNESS_CAPABILITIES[harness])
    return f"{harness} has no {', '.join(missing)} support"


def split_supported_harnesses(declared: list[str], compatible: list[str]) -> tuple[list[str], list[str]]:
    """Split *declared* harnesses into those in *compatible* and those that aren't.

    Declared order is kept. An empty declaration stays empty: it places no limit on harnesses.
    """
    allowed = set(compatible)
    return [h for h in declared if h in allowed], [h for h in declared if h not in allowed]


def apply_harness_inference(version, agent, skill_listings: dict | None = None) -> list[str]:
    """Infer *version*'s required features and drop declared harnesses that cannot run them.

    Sets ``required_capabilities``, ``inferred_supported_harnesses`` and the narrowed
    ``supported_harnesses`` on *version* (an AgentVersion, or an Agent proxying its latest
    version). *agent* supplies ``components`` and ``external_mcps`` for inference.

    Returns a warning per dropped harness. Raises NoCompatibleHarnessError, leaving
    ``supported_harnesses`` unchanged, when no declared harness remains.
    """
    required = infer_required_features(agent, skill_listings=skill_listings)
    version.required_capabilities = required
    version.inferred_supported_harnesses = compute_supported_harnesses(required)
    kept, dropped = split_supported_harnesses(
        list(version.supported_harnesses or []), version.inferred_supported_harnesses
    )
    reasons = [unsupported_harness_reason(harness, required) for harness in dropped]
    if dropped and not kept:
        raise NoCompatibleHarnessError(
            "None of the declared harnesses can run this agent's components: "
            f"{'; '.join(reasons)}. Declare one of: {', '.join(version.inferred_supported_harnesses)}."
        )
    if dropped:
        optic.debug("dropped unsupported harnesses={} required={}", dropped, required)
        version.supported_harnesses = kept
    return [f"{reason}, so it was left out of supported harnesses." for reason in reasons]
