# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Point insight suggestions at components the registry already has.

Three stages, in order:

1. :func:`build_signals` — distil a report's aggregates and facets into a
   search string describing what this agent actually does.
2. :func:`build_catalog` — ask the shared recommender for a small, ranked,
   user-visible shortlist of components the agent is *not* already using.
3. :func:`validate_reuse_suggestions` — after the LLM has written its
   suggestions, drop any reuse that names a component we did not offer or
   that no longer resolves. The model's output is never trusted as a
   registry reference.

Stage 3 is the part that makes this safe to ship: without it a hallucinated
UUID would be rendered to users as a real component.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

from services.registry_recommender import (
    ALL_COMPONENT_TYPES,
    build_signal_query,
    coerce_uuid,
    resolve_components,
    shortlist,
)

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

# Action types the LLM may use to mean "attach something that already exists".
REUSE_ACTIONS = frozenset({"reuse_existing_component", "attach_registry_component"})


@dataclass(frozen=True)
class RegistryScope:
    """Which components an agent may be offered, and which it already has."""

    user_id: uuid.UUID | None = None
    attached_ids: tuple[uuid.UUID, ...] = ()


@dataclass
class CatalogOffer:
    """The shortlist that was actually shown to the model.

    Kept so validation can check the model only referenced what it was given,
    and so the report can tell the reader *why* there were no reuse
    suggestions. "We searched and nothing fit" and "there was nothing to
    search" look identical from an empty list, but mean opposite things to
    someone deciding whether the feature works.
    """

    entries_by_type: dict[str, list[dict]] = field(default_factory=dict)
    offered_ids: set[uuid.UUID] = field(default_factory=set)
    # False when the agent can see no approved components at all. None when
    # we did not need to check (because the shortlist found something).
    registry_has_components: bool | None = None
    # False when an operator turned reuse suggestions off.
    enabled: bool = True

    def __bool__(self) -> bool:
        return bool(self.entries_by_type)

    @property
    def item_count(self) -> int:
        return sum(len(v) for v in self.entries_by_type.values())

    def to_summary(self, reused: int = 0) -> dict:
        """Persisted alongside the narrative so the UI can explain itself."""
        return {
            "enabled": self.enabled,
            "offered": self.item_count,
            "reused": reused,
            "registry_has_components": self.registry_has_components,
        }

    def to_dict(self) -> dict:
        """Serialize the full offer for persistence as analysis payload.

        Unlike :meth:`to_summary` (which records *that* a search happened),
        this captures *what* was offered so downstream consumers can read the
        shortlist without re-running the recommender. ``offered_ids`` is a
        set of UUIDs and is serialized as sorted strings for stable storage.
        """
        return {
            "enabled": self.enabled,
            "registry_has_components": self.registry_has_components,
            "item_count": self.item_count,
            "offered_ids": sorted(str(cid) for cid in self.offered_ids),
            "entries_by_type": self.entries_by_type,
        }


def build_signals(
    agg: dict | None = None,
    facets_summary: dict | None = None,
    agent_config: dict | None = None,
) -> str:
    """Build the search string that drives the shortlist.

    Draws on what the agent *does* (goals, languages, tools) and where it
    *struggles* (friction types, tool error categories), because both are
    reasons to reach for an existing component.
    """
    agg = agg or {}
    facets_summary = facets_summary or {}

    def _labels(items, key: str) -> list[str]:
        """Pull labels out of the assorted shapes aggregates use.

        Facets come back from an LLM, so a field can arrive as the wrong
        shape entirely. Anything unexpected yields no labels rather than
        raising — a bad signal is recoverable, a failed report is not.
        """
        out: list[str] = []
        if isinstance(items, str):
            # Iterating a string would emit one token per character.
            return []
        if isinstance(items, dict):
            items = list(items.items())
        if not isinstance(items, list | tuple):
            return []
        for item in items or []:
            if isinstance(item, dict):
                value = item.get(key) or item.get("name") or item.get("category")
            elif isinstance(item, list | tuple) and item:
                value = item[0]
            else:
                value = item
            if value:
                out.append(str(value))
        return out

    tools = _labels(agg.get("top_tools"), "tool")[:12]
    config = agent_config or {}

    return build_signal_query(
        # What the agent is *for*. Tool names and languages describe mechanics
        # ("Edit", "TypeScript") but carry no domain vocabulary, so an agent
        # doing React work looks identical to one doing terraform work. The
        # agent's own prompt is where words like "React" or "terraform" live,
        # and without it the shortlist starves on anything but tool-name matches.
        _domain_words(config),
        _labels(facets_summary.get("goal_categories"), "category")[:10],
        _labels(facets_summary.get("friction_types"), "type")[:8],
        _labels(agg.get("top_languages"), "language")[:6],
        tools,
        # An MCP tool reads as one opaque token ("mcp__postgres__query"), which
        # matches nothing. The server name inside it is the useful signal — it
        # names the domain the user actually works in.
        _mcp_server_names(tools),
        _labels(agg.get("tool_error_categories"), "category")[:5],
        _labels(facets_summary.get("repeated_instructions"), "instruction")[:5],
        (agent_config or {}).get("category") or "",
        (agent_config or {}).get("configured_mcps") or [],
    )


# Enough of the prompt to carry the domain, not so much that boilerplate
# ("you are a helpful assistant...") drowns out the specific words.
_PROMPT_SIGNAL_CHARS = 400


def _domain_words(agent_config: dict) -> list[str]:
    """Domain vocabulary describing what this agent is for."""
    parts: list[str] = []
    excerpt = agent_config.get("system_prompt_excerpt")
    if isinstance(excerpt, str) and excerpt.strip():
        parts.append(excerpt[:_PROMPT_SIGNAL_CHARS])
    for key in ("name", "description", "category"):
        value = agent_config.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value)
    return parts


def _mcp_server_names(tool_names: list[str]) -> list[str]:
    """Pull server names out of ``mcp__<server>__<tool>`` tool identifiers."""
    servers: list[str] = []
    for name in tool_names:
        if not name.startswith("mcp__"):
            continue
        parts = name.split("__")
        if len(parts) >= 2 and parts[1]:
            servers.append(parts[1])
    return servers


async def build_catalog(
    db: AsyncSession,
    scope: RegistryScope,
    signals: str,
) -> CatalogOffer:
    """Shortlist components for the suggestions prompt.

    Returns an empty offer when the feature is disabled or nothing matches;
    callers then simply omit the catalog block from the prompt.
    """
    import services.dynamic_settings as ds

    if not ds.get_sync_bool("insights.registry_match_enabled", True):
        return CatalogOffer(enabled=False)

    try:
        # Operator-supplied caps. A zero or negative value would become
        # LIMIT 0 and make the feature look broken rather than disabled —
        # `registry_match_enabled` is the switch for turning it off.
        per_type = max(1, ds.get_sync_int("insights.registry_match_per_type", 6))
        total = max(1, ds.get_sync_int("insights.registry_match_max_items", 24))

        candidates = await shortlist(
            db,
            signals=signals,
            user_id=scope.user_id,
            exclude_ids=scope.attached_ids,
            per_type_limit=per_type,
            total_limit=total,
        )
    except Exception as e:
        # A recommendation failure must never fail the whole report.
        logger.warning("insight_registry_shortlist_failed", error=str(e))
        return CatalogOffer()

    offer = CatalogOffer()
    for candidate in candidates:
        offer.entries_by_type.setdefault(f"{candidate.component_type}s", []).append(candidate.to_catalog_entry())
        offer.offered_ids.add(candidate.id)

    if not offer:
        # Nothing matched. Find out whether that is because the registry is
        # empty or because nothing was relevant — the report says different
        # things in each case. One cheap unfiltered lookup answers it.
        offer.registry_has_components = await _registry_has_any(db, scope)

    logger.info(
        "insight_registry_shortlist",
        items=offer.item_count,
        types=sorted(offer.entries_by_type),
        signal_chars=len(signals),
        registry_has_components=offer.registry_has_components,
    )
    return offer


async def _registry_has_any(db: AsyncSession, scope: RegistryScope) -> bool:
    """Whether this agent can see any approved component at all."""
    try:
        probe = await shortlist(
            db,
            signals="",
            user_id=scope.user_id,
            exclude_ids=scope.attached_ids,
            per_type_limit=1,
            total_limit=1,
        )
        return bool(probe)
    except Exception as e:
        logger.warning("insight_registry_probe_failed", error=str(e))
        return False


async def validate_reuse_suggestions(
    narrative: dict,
    offer: CatalogOffer,
    db: AsyncSession,
    scope: RegistryScope,
) -> dict:
    """Ground every reuse suggestion against the registry.

    A ``features_to_try`` entry claiming to reuse an existing component is
    kept only when its id was in the shortlist we offered *and* still
    resolves to an approved, visible listing. Anything else is rewritten
    into a plain suggestion with the bogus reference stripped, so the user
    still sees the idea but never a fake component link.

    Returns the narrative (mutated in place) for convenience.
    """
    suggestions = narrative.get("suggestions")
    if not isinstance(suggestions, dict):
        return narrative

    features = suggestions.get("features_to_try")
    if not isinstance(features, list):
        return narrative

    # Collect the ids the model claims to reuse.
    wanted: list[tuple[int, uuid.UUID]] = []
    for idx, feature in enumerate(features):
        if not isinstance(feature, dict):
            continue
        action = str(feature.get("action_type") or "").lower()
        if action not in REUSE_ACTIONS:
            continue
        component_id = coerce_uuid(feature.get("existing_component_id"))
        if component_id is not None:
            wanted.append((idx, component_id))

    if not wanted:
        return narrative

    # Only ids we actually offered are eligible. This is what stops a
    # plausible-looking but invented UUID from reaching the UI.
    eligible = [(idx, cid) for idx, cid in wanted if cid in offer.offered_ids]

    resolved = {}
    if eligible:
        refs = [(t, cid) for _, cid in eligible for t in ALL_COMPONENT_TYPES]
        resolved = await resolve_components(db, refs, user_id=scope.user_id)

    resolved_by_id: dict[uuid.UUID, object] = {}
    for (_component_type, component_id), component in resolved.items():
        resolved_by_id.setdefault(component_id, component)

    dropped = 0
    for idx, component_id in wanted:
        feature = features[idx]
        component = resolved_by_id.get(component_id)
        if component is None:
            dropped += 1
            feature["action_type"] = "create_new_skill" if _looks_like_skill(feature) else "no_action"
            feature["existing_component_id"] = None
            feature["component_ref"] = None
            continue
        # Attach the resolved reference so UI/HTML/CLI can render a real
        # link and the correct version without re-querying.
        feature["existing_component_id"] = str(component.id)
        feature["component_ref"] = component.to_ref()

    if dropped:
        logger.warning(
            "insight_reuse_suggestions_rejected",
            dropped=dropped,
            offered=len(offer.offered_ids),
            claimed=len(wanted),
        )

    return narrative


def _looks_like_skill(feature: dict) -> bool:
    label = f"{feature.get('feature') or ''}".lower()
    return "skill" in label


def count_reused(narrative: dict) -> int:
    """How many suggestions survived validation with a real component."""
    suggestions = narrative.get("suggestions")
    if not isinstance(suggestions, dict):
        return 0
    features = suggestions.get("features_to_try")
    if not isinstance(features, list):
        return 0
    return sum(1 for f in features if isinstance(f, dict) and f.get("component_ref"))


def catalog_block(offer: CatalogOffer) -> str:
    """Render the shortlist for inclusion in the suggestions prompt."""
    if not offer:
        return ""
    return (
        "\n\n## Reusable Components Already In This Registry\n"
        "These are approved components this agent is NOT currently using. "
        "When one of them solves an observed problem, prefer reusing it over "
        "inventing something new. Copy `id` verbatim into "
        "`existing_component_id` — never invent an id.\n" + json.dumps(offer.entries_by_type, indent=2)
    )


def offered_summary(offer: CatalogOffer) -> list[str]:
    """Short human-readable list of what was offered (for logs/debugging)."""
    names: list[str] = []
    for entries in offer.entries_by_type.values():
        names.extend(str(e.get("qualified_name") or e.get("name") or "") for e in entries)
    return [n for n in names if n]


__all__ = [
    "CatalogOffer",
    "RegistryScope",
    "build_catalog",
    "build_signals",
    "catalog_block",
    "count_reused",
    "offered_summary",
    "validate_reuse_suggestions",
]
