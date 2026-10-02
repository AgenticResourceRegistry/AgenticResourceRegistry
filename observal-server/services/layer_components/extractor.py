# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""Allocate, insert and publish one user-scoped layer extraction generation."""

from __future__ import annotations

import json

from loguru import logger as optic

import services.clickhouse.client as clickhouse
from database import async_session
from services.layer_components import CURRENT_EXTRACTOR_VERSION
from services.layer_components.normalizer import normalize_snapshot, occurrence_row
from services.layer_components.resolver import resolve_occurrences
from services.projection_generation import next_projection_generation


async def _query(sql: str, params: dict | None = None, *, data: str | None = None) -> list[dict]:
    result = await clickhouse._query(sql, params, data=data)
    result.raise_for_status()
    return result.json().get("data", []) if "FORMAT JSON" in sql and data is None else []


async def _snapshot(project_id: str, user_id: str, layer_hash: str) -> tuple[dict | None, bool]:
    rows = await _query(
        """SELECT content FROM layer_snapshots
        WHERE project_id = {project_id:String} AND user_id = {user_id:String}
          AND hash = {layer_hash:String} LIMIT 1000 FORMAT JSON""",
        {"param_project_id": project_id, "param_user_id": user_id, "param_layer_hash": layer_hash},
    )
    if not rows:
        return None, False
    snapshots = []
    for row in rows:
        try:
            content = json.loads(row["content"])
            if not isinstance(content, dict):
                return None, True
            snapshots.append(content)
        except (KeyError, ValueError, TypeError):
            return None, True
    conflict = len(rows) >= 1000 or len({json.dumps(value, sort_keys=True) for value in snapshots}) > 1
    conflict |= any(value.get("identity_status") == "identity_conflict" for value in snapshots)
    return snapshots[0], conflict


async def _latest_complete(project_id: str, user_id: str, layer_hash: str) -> dict | None:
    rows = await _query(
        """SELECT extraction_generation, max(identity_conflict) AS identity_conflict
        FROM layer_component_extractions
        WHERE project_id = {project_id:String} AND user_id = {user_id:String}
          AND layer_hash = {layer_hash:String} AND extractor_version = {extractor_version:UInt16}
        GROUP BY extraction_generation
        HAVING countIf(status = 'complete') > 0 AND countIf(status = 'failed') = 0
        ORDER BY extraction_generation DESC LIMIT 1 FORMAT JSON""",
        {
            "param_project_id": project_id,
            "param_user_id": user_id,
            "param_layer_hash": layer_hash,
            "param_extractor_version": CURRENT_EXTRACTOR_VERSION,
        },
    )
    return rows[0] if rows else None


async def _marker(
    project_id: str,
    user_id: str,
    layer_hash: str,
    generation: int,
    *,
    status: str,
    count: int,
    diagnostics: int,
    conflict: bool,
) -> None:
    await _query(
        "INSERT INTO layer_component_extractions "
        "(project_id, user_id, layer_hash, extractor_version, extraction_generation, status, "
        "identity_conflict, occurrence_count, diagnostic_count) FORMAT JSONEachRow",
        data=json.dumps(
            {
                "project_id": project_id,
                "user_id": user_id,
                "layer_hash": layer_hash,
                "extractor_version": CURRENT_EXTRACTOR_VERSION,
                "extraction_generation": generation,
                "status": status,
                "identity_conflict": int(conflict),
                "occurrence_count": count,
                "diagnostic_count": diagnostics,
            }
        ),
    )


async def ensure_layer_components(project_id: str, user_id: str, layer_hash: str, force: bool = False) -> dict:
    """Index a snapshot once per current extractor version, or re-resolve on force.

    A complete marker is inserted only after all rows have been acknowledged.
    Failed attempts get a failed marker and never mask the last complete one.
    """
    snapshot, conflict = await _snapshot(project_id, user_id, layer_hash)
    if snapshot is None:
        return {"status": "missing", "occurrences": 0}
    previous = await _latest_complete(project_id, user_id, layer_hash)
    if not force and previous and bool(previous["identity_conflict"]) == conflict:
        return {"status": "already_complete", "occurrences": 0}
    generation = await next_projection_generation()
    count = 0
    diagnostics = 0
    try:
        occurrences = normalize_snapshot(snapshot.get("pinned_versions"), snapshot.get("drift"))
        resolved = []
        if occurrences:
            async with async_session() as db:
                resolved = await resolve_occurrences(db, occurrences)
        rows = []
        for item in resolved:
            occurrence = item.occurrence
            status = "identity_conflict" if conflict else item.identity_status
            verification = "unverified" if conflict else occurrence.verification_status
            diagnostics += int(status != "resolved" or verification != "verified")
            rows.append(
                {
                    "project_id": project_id,
                    "user_id": user_id,
                    "layer_hash": layer_hash,
                    "hash_schema_version": 2 if layer_hash.startswith("v2_") else 1,
                    "extractor_version": CURRENT_EXTRACTOR_VERSION,
                    "extraction_generation": generation,
                    **occurrence_row(occurrence),
                    "component_id": item.component_id,
                    "component_version_id": item.component_version_id,
                    "identity_status": status,
                    "verification_status": verification,
                }
            )
        count = len(rows)
        if rows:
            await _query(
                "INSERT INTO layer_components "
                "(project_id, user_id, layer_hash, hash_schema_version, extractor_version, extraction_generation, "
                "occurrence_key, component_type, source, harness, scope, parent_agent_id, parent_agent_version, "
                "raw_listing_id, raw_name, raw_version, qualified_name, local_name, component_id, "
                "component_version_id, identity_status, verification_status, location_sha256, binding_agent, "
                "binding_placement) "
                "FORMAT JSONEachRow",
                data="\n".join(json.dumps(row) for row in rows),
            )
        await _marker(
            project_id,
            user_id,
            layer_hash,
            generation,
            status="complete",
            count=count,
            diagnostics=diagnostics,
            conflict=conflict,
        )
    except Exception:
        try:
            await _marker(
                project_id,
                user_id,
                layer_hash,
                generation,
                status="failed",
                count=count,
                diagnostics=diagnostics,
                conflict=conflict,
            )
        except Exception as marker_error:
            optic.warning("failed to publish layer extraction failure: {}", type(marker_error).__name__)
        raise
    return {
        "status": "complete",
        "occurrences": count,
        "diagnostics": diagnostics,
        "generation": generation,
        "identity_conflict": conflict,
    }
