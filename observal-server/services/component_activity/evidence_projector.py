# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""User-scoped non-MCP component evidence (skills, hooks), published in immutable generations.

Each evidence type shares the versioned activity rails with MCP calls but never
their meaning: rows carry the type's own ``evidence_kind`` values and
publications its ``evidence_type``, so a projection is complete or failed on
its own, and MCP call totals, coverage and model evidence never read them.

A projection is *complete* when every source line has a v2 layer hash with a
complete mapping for the component type and all extracted facts were
published. Missing source, legacy or unstable hashes, and pending mappings
publish nothing; an identity conflict publishes an explicit zero-row
generation that supersedes any earlier positive one. A harness without a
verified extractor for the type is ``unsupported``: never a session with no
evidence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from loguru import logger as optic

from observal_shared.harness_registry import HARNESS_REGISTRY
from services.layer_components import CURRENT_EXTRACTOR_VERSION
from services.projection_generation import next_projection_generation

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

from .projector import (
    ProjectionRaceError,
    _canonical_row,
    _index_layer,
    _inputs_unchanged,
    _latest_complete,
    _mapping,
    _marker,
    _params,
    _publish_unattributable,
    _published_rows,
    _query,
    _source_revision,
    _source_rows,
    publication_version,
)


@dataclass(frozen=True)
class EvidenceSpec:
    """One evidence type on the shared rails: what to extract, how to match, how to label."""

    evidence_type: str  # publication label; also the component type mapped from the layer
    registry_key: str  # HARNESS_REGISTRY key naming the harness's verified extractor
    extract: Callable[[str, Sequence[Mapping[str, object]]], Any]
    # (extraction, candidates by layer hash, layer hash by source offset) -> match result
    match: Callable[[Any, Mapping[str, Sequence[dict]], Mapping[int, str]], Any]
    unknown_results: Callable[[Any], int]
    matcher_version: int


def _row_time(fact_time: datetime | None, source_row: dict) -> str:
    value = fact_time
    if value is None:
        raw = source_row.get("timestamp")
        try:
            value = datetime.fromisoformat(str(raw).replace(" ", "T").replace("Z", "+00:00"))
        except ValueError:
            value = datetime(1970, 1, 1, tzinfo=UTC)
    value = value if value.tzinfo else value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:23]


async def project_session_evidence(
    spec: EvidenceSpec, project_id: str, user_id: str, harness: str, session_id: str, *, force: bool = False
) -> dict:
    """Project one scoped session's evidence of one type; only an acknowledged attempt is visible."""
    version = publication_version()
    kind = spec.evidence_type
    if not project_id or not user_id or not harness or not session_id:
        raise ValueError(f"{kind} evidence session key must be fully scoped")
    if HARNESS_REGISTRY.get(harness, {}).get(spec.registry_key) is None:
        return {"status": "unsupported", "publication_version": version}
    params = _params(project_id, user_id, harness, session_id)
    source = await _source_rows(params)
    if source is None:
        return {"status": "source_too_large", "publication_version": version}
    revision = _source_revision(source)
    if revision is None:
        return {"status": "pending_source", "publication_version": version}
    extracted = spec.extract(harness, source)
    if extracted.status != "supported":
        return {"status": "unsupported", "publication_version": version}
    unattributed = {
        "candidate_count": len(extracted.evidence),
        "unmatched_count": len(extracted.evidence),
        "unknown_result_count": spec.unknown_results(extracted),
    }
    hashes = [row.get("layer_hash") or "" for row in source]
    if not all(isinstance(value, str) and value.startswith("v2_") for value in hashes):
        return {
            "status": "legacy_layer" if len(set(hashes)) == 1 else "unstable_layer",
            "publication_version": version,
            **unattributed,
        }
    candidates: dict[str, list[dict]] = {}
    generations: dict[str, int] = {}
    for layer_hash in sorted(set(hashes)):
        status, generation, mapped = await _mapping(project_id, user_id, layer_hash, harness, kind)
        if status == "pending_mapping" and await _index_layer(project_id, user_id, layer_hash):
            status, generation, mapped = await _mapping(project_id, user_id, layer_hash, harness, kind)
        if status == "identity_conflict":
            return await _publish_unattributable(params, version, revision, unattributed, layer_hash, kind)
        if status != "complete":
            return {"status": status, "publication_version": version, "layer_hash": layer_hash, **unattributed}
        candidates[layer_hash] = mapped
        generations[layer_hash] = generation

    by_offset = {int(row["line_offset"]): row for row in source}
    matched = spec.match(extracted, candidates, {int(row["line_offset"]): hashes[i] for i, row in enumerate(source)})
    rows = []
    for match in matched.rows:
        source_row = by_offset[match["source_line_offset"]]
        rows.append(
            {
                **match,
                "project_id": project_id,
                "user_id": user_id,
                "harness": harness,
                "session_id": session_id,
                "projection_version": version,
                "source_line_hash": source_row.get("source_sha256") or source_row["line_hash"],
                "layer_hash": hashes[match["source_line_offset"]],
                "event_time": _row_time(match["event_time"], source_row),
                "matcher_version": spec.matcher_version,
                "extractor_version": CURRENT_EXTRACTOR_VERSION,
            }
        )
    counts = {
        "candidate_count": matched.candidate_count,
        # A matcher may publish non-fact rows (hook context); it then reports its own fact count.
        "attributed_count": getattr(matched, "attributed_count", len(rows)),
        "collision_count": matched.collision_count,
        "unmatched_count": matched.unmatched_count,
        "unknown_result_count": matched.unknown_result_count,
    }
    previous = await _latest_complete(params, version, kind)
    if (
        not force
        and previous
        and previous["source_revision"] == revision
        and {key: int(previous[key]) for key in counts} == counts
    ):
        old = await _published_rows(params, version, int(previous["projection_generation"]))

        def ordering(item: dict) -> tuple:
            return item["source_line_offset"], item["source_block_key"]

        if sorted(map(_canonical_row, old), key=ordering) == sorted(
            map(_canonical_row, rows), key=ordering
        ) and await _inputs_unchanged(params, revision, source, generations, project_id, user_id, harness, kind):
            return {
                "status": "already_complete",
                "generation": int(previous["projection_generation"]),
                "publication_version": version,
                **counts,
            }
    generation = await next_projection_generation()
    try:
        if rows:
            await _query(
                "INSERT INTO component_activity "
                "(project_id, user_id, harness, session_id, projection_version, projection_generation, "
                "source_line_offset, source_block_key, source_line_hash, layer_hash, component_type, "
                "component_id, component_version_id, tool_name, tool_use_id, event_time, result_state, "
                "attribution_method, matcher_version, extractor_version, evidence_kind) FORMAT JSONEachRow",
                data="\n".join(json.dumps(row | {"projection_generation": generation}) for row in rows),
            )
        if not await _inputs_unchanged(params, revision, source, generations, project_id, user_id, harness, kind):
            raise ProjectionRaceError(f"Canonical source or published {kind} mapping changed during publication")
        await _marker(params, version, generation, "complete", revision, counts, kind)
    except Exception:
        try:
            await _marker(params, version, generation, "failed", revision, counts, kind)
        except Exception as marker_error:
            optic.warning("failed to publish {} evidence failure: {}", kind, type(marker_error).__name__)
        raise
    return {"status": "complete", "generation": generation, "publication_version": version, **counts}
