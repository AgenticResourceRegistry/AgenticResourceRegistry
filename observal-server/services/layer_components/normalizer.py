# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""Project bounded registry pin claims into stable per-component occurrences."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass

SUPPORTED_TYPES = frozenset({"mcp", "skill", "hook"})
_VERIFICATION_KEY = ("harness", "component_id", "alias", "scope", "parent_agent_id")


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


def _text(value: object) -> str:
    return unicodedata.normalize("NFC", value) if isinstance(value, str) else ""


def normalize_snapshot(pinned_versions: object, drift: object) -> list[Occurrence]:
    """Never inspect file contents; legacy pin claims remain diagnostic only."""
    if not isinstance(pinned_versions, dict):
        return []
    drift = drift if isinstance(drift, dict) else {}
    is_v2 = pinned_versions.get("schema_version") == 2
    globally_drifted = drift.get("is_canonical") is False
    verifications = drift.get("mcp_verifications")
    verifications = verifications if isinstance(verifications, list) else []
    # Index once (first record wins) so normalization stays linear in input size.
    verification_index: dict[tuple, object] = {}
    for item in verifications:
        if isinstance(item, dict):
            try:
                key = tuple(item.get(field) for field in _VERIFICATION_KEY)
                status = item.get("status")
                verification_index.setdefault(key, status if isinstance(status, str) else None)
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
        if kind == "mcp" and key in verification_index:
            status = verification_index[key]
            verification = (
                "verified"
                if is_v2 and not globally_drifted and raw_id and alias and status == "verified"
                else ("drifted" if status in {"drifted", "missing"} or globally_drifted else "unverified")
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
    alias_claims: dict[tuple[str, str, str], set[tuple[str, str]]] = defaultdict(set)
    for record in records:
        if record["component_type"] == "mcp" and record["local_name"]:
            alias_claims[record["harness"], record["scope"], record["local_name"]].add(
                (record["raw_listing_id"], record["raw_version"])
            )
    for record in records:
        if (
            record["component_type"] == "mcp"
            and len(alias_claims.get((record["harness"], record["scope"], record["local_name"]), ())) > 1
        ):
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
