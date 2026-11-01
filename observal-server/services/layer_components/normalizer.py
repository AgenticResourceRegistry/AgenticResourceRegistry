# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""Project bounded registry pin claims into stable per-component occurrences."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass

SUPPORTED_TYPES = frozenset({"mcp", "skill", "hook"})
_VERIFICATION_KEY = ("harness", "component_id", "alias", "scope", "parent_agent_id")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_AGENT = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")


@dataclass(frozen=True)
class Occurrence:
    occurrence_key: str
    component_type: str
    source: str
    harness: str
    scope: str
    parent_agent_id: str
    parent_agent_version: str
    raw_listing_id: str
    raw_name: str
    raw_version: str
    qualified_name: str
    local_name: str
    verification_status: str
    # Skills: SHA-256 of the absolute SKILL.md path the verifier hashed.
    # Hooks: SHA-256 of the (event, command) the harness records when the hook runs.
    location_sha256: str = ""
    # Hooks only: the agent whose hook it is ('' for a standalone settings-file hook).
    binding_agent: str = ""
    # Hooks with a binding agent only: ``frontmatter`` (in the agent file, so Claude Code
    # runs it only interactively) or ``gated_settings`` (in settings.json behind the
    # agent gate, so it runs whenever the agent is active). Ignored without an agent.
    binding_placement: str = "frontmatter"


def _text(value: object) -> str:
    return unicodedata.normalize("NFC", value) if isinstance(value, str) else ""


def _drifted_components(drift: dict) -> set[tuple[str, str]] | None:
    """``(harness, component)`` named by each drift entry, or None if any entry names none.

    Verifier entries name the component by ID (``component``) and alias; file-integrity
    entries by name. An entry that cannot be tied to a component, or a non-canonical
    layer that lists none, leaves the whole layer drifted (fail closed).
    """
    entries = drift.get("drifted_files")
    if not isinstance(entries, list) or not entries:
        return None
    named: set[tuple[str, str]] = set()
    for entry in entries:
        harness = _text(entry.get("harness")) if isinstance(entry, dict) else ""
        refs = [_text(entry.get(key)) for key in ("component", "alias")] if harness else []
        refs = [ref for ref in refs if ref]
        if not refs:
            return None
        named.update((harness, ref) for ref in refs)
    return named


def normalize_snapshot(pinned_versions: object, drift: object) -> list[Occurrence]:
    """Never inspect file contents; legacy pin claims remain diagnostic only."""
    if not isinstance(pinned_versions, dict):
        return []
    drift = drift if isinstance(drift, dict) else {}
    is_v2 = pinned_versions.get("schema_version") == 2
    # A non-canonical layer drifts only the components its drift entries name; a
    # component another component's drift does not name keeps its own verification.
    drifted_components = _drifted_components(drift) if drift.get("is_canonical") is False else set()
    globally_drifted = drifted_components is None
    # Index once (first record wins) so normalization stays linear in input size.
    # MCP and skill results are kept apart: one kind never verifies the other.
    verification_index: dict[str, dict[tuple, object]] = {"mcp": {}, "skill": {}, "hook": {}}
    locations: dict[tuple[str, tuple], str] = {}
    hook_agents: dict[tuple, str] = {}
    hook_placements: dict[tuple, str] = {}
    for kind, field_name in (
        ("mcp", "mcp_verifications"),
        ("skill", "skill_verifications"),
        ("hook", "hook_verifications"),
    ):
        verifications = drift.get(field_name)
        for item in verifications if isinstance(verifications, list) else []:
            if isinstance(item, dict):
                try:
                    key = tuple(item.get(field) for field in _VERIFICATION_KEY)
                    status = item.get("status")
                    if key in verification_index[kind]:
                        continue
                    verification_index[kind][key] = status if isinstance(status, str) else None
                    location = item.get("location_sha256")
                    if kind in ("skill", "hook") and isinstance(location, str) and _SHA256.fullmatch(location):
                        locations[kind, key] = location
                    agent = item.get("hook_agent")
                    if kind == "hook" and isinstance(agent, str) and _AGENT.fullmatch(agent):
                        hook_agents[key] = agent
                        if item.get("hook_placement") == "gated_settings":
                            hook_placements[key] = "gated_settings"
                except TypeError:
                    continue
    records: list[dict[str, str]] = []

    def append(
        pin: object, *, source: str, harness: str, scope: str, parent_id: str = "", parent_version: str = ""
    ) -> None:
        if not isinstance(pin, dict) or pin.get("type") not in SUPPORTED_TYPES:
            return
        kind = pin["type"]
        alias = _text(pin.get("local_name"))
        raw_id = _text(pin.get("id"))
        item_scope = _text(pin.get("scope")) or scope
        verification = "unverified"
        key = (harness, raw_id, alias, item_scope, parent_id)
        location = locations.get((kind, key), "")
        binding_agent = hook_agents.get(key, "") if kind == "hook" else ""
        binding_placement = hook_placements.get(key, "frontmatter") if binding_agent else "frontmatter"
        named_drifted = globally_drifted or any(
            (harness, ref) in drifted_components for ref in (raw_id, alias, _text(pin.get("name"))) if ref
        )
        if kind in verification_index and key in verification_index[kind]:
            status = verification_index[kind][key]
            verification = (
                "verified"
                if is_v2 and not named_drifted and raw_id and alias and status == "verified"
                else ("drifted" if status in {"drifted", "missing"} or named_drifted else "unverified")
            )
        records.append(
            {
                "component_type": kind,
                "source": source,
                "harness": harness,
                "scope": item_scope,
                "parent_agent_id": parent_id,
                "parent_agent_version": parent_version,
                "raw_listing_id": raw_id,
                "raw_name": _text(pin.get("name")),
                "raw_version": _text(pin.get("version")),
                "qualified_name": _text(pin.get("qualified_name")),
                "local_name": alias,
                "verification_status": verification,
                "location_sha256": location,
                "binding_agent": binding_agent,
                "binding_placement": binding_placement,
            }
        )

    agents = pinned_versions.get("agents")
    for agent in agents[:128] if isinstance(agents, list) else []:
        if not isinstance(agent, dict):
            continue
        harness = _text(agent.get("harness"))
        scope = _text(agent.get("scope")) or "project"
        components = agent.get("components")
        for pin in components[:128] if isinstance(components, list) else []:
            append(
                pin,
                source="agent",
                harness=harness,
                scope=scope,
                parent_id=_text(agent.get("id")),
                parent_version=_text(agent.get("version")),
            )
    standalone = pinned_versions.get("standalone")
    for pin in standalone[:512] if isinstance(standalone, list) else []:
        if isinstance(pin, dict):
            append(pin, source="standalone", harness=_text(pin.get("harness")), scope="project")

    # One installed alias cannot prove two different registry identities in the
    # same harness and scope. A duplicated claim about the *same* identity is fine.
    alias_claims: dict[tuple[str, str, str, str], set[tuple[str, str]]] = defaultdict(set)
    for record in records:
        if record["component_type"] in ("mcp", "skill") and record["local_name"]:
            alias_claims[record["component_type"], record["harness"], record["scope"], record["local_name"]].add(
                (record["raw_listing_id"], record["raw_version"])
            )
    for record in records:
        claim = (record["component_type"], record["harness"], record["scope"], record["local_name"])
        if record["component_type"] in ("mcp", "skill") and len(alias_claims.get(claim, ())) > 1:
            record["verification_status"] = "unverified"

    # The published identity is derived from immutable pin fields, not from the
    # verification result: a re-resolution must preserve the occurrence key.
    # Equal pin tuples retain distinct zero-based ordinals regardless of order.
    key_fields = (
        "source",
        "component_type",
        "raw_listing_id",
        "raw_version",
        "harness",
        "scope",
        "local_name",
        "parent_agent_id",
        "parent_agent_version",
        "qualified_name",
        "raw_name",
    )
    counters: dict[tuple[str, ...], int] = defaultdict(int)
    occurrences: list[Occurrence] = []
    for record in sorted(records, key=lambda row: tuple(row[field] for field in key_fields)):
        identity = tuple(record[field] for field in key_fields)
        ordinal = counters[identity]
        counters[identity] += 1
        digest = hashlib.sha256(
            json.dumps([*identity, ordinal], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        occurrences.append(Occurrence(occurrence_key=digest, **record))
    return occurrences


def occurrence_row(occurrence: Occurrence) -> dict[str, str]:
    return asdict(occurrence)
