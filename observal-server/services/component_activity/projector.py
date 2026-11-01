# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""User-scoped canonical-source activity, published in immutable generations."""

from __future__ import annotations

import hashlib
import json

from loguru import logger as optic

import services.clickhouse.client as clickhouse
from services.layer_components import CURRENT_EXTRACTOR_VERSION
from services.layer_components.queries import SNAPSHOT_CONFLICT_EXPR
from services.projection_generation import next_projection_generation


class ProjectionRaceError(RuntimeError):
    """The source or published mapping changed during publication; the attempt failed closed.

    Its message is fixed text with no session data, so it may be logged.
    """


# Every projection or matcher change must invalidate all three evidence types
# together: readers and the full-replay checkpoint use the same UInt16 key.
# 2: activity rows carry evidence_kind and publications evidence_type.
# 3: hook extraction excludes Observal telemetry hooks from unmatched candidates.
PROJECTION_VERSION = 3
# 2: result-link ordering changed. 3: harness-reported server identity (Pi).
MATCHER_VERSION = 3
MAX_SOURCE_RECORDS = 50_000
MAX_SOURCE_BYTES = 64 * 1024 * 1024


def publication_version() -> int:
    """Pack projection, MCP, skill and hook revisions without collisions in UInt16.

    A change to any matcher invalidates every reader's prior publications and
    triggers the bounded full replay. Extending any field beyond 15 needs a
    schema/versioning change rather than silent wraparound. The packing change
    itself invalidates older publications (which used only MCP's version).
    """
    from .hook_matcher import HOOK_MATCHER_VERSION
    from .skill_matcher import SKILL_MATCHER_VERSION

    versions = (PROJECTION_VERSION, MATCHER_VERSION, SKILL_MATCHER_VERSION, HOOK_MATCHER_VERSION)
    if not all(1 <= version <= 15 for version in versions):
        raise ValueError("Activity publication versions must each fit a nonzero UInt4")
    return sum(version << shift for version, shift in zip(versions, (12, 8, 4, 0), strict=True))


async def _query(sql: str, params: dict | None = None, *, data: str | None = None) -> list[dict]:
    response = await clickhouse._query(sql, params, data=data)
    response.raise_for_status()
    return response.json().get("data", []) if "FORMAT JSON" in sql and data is None else []


def _params(project_id: str, user_id: str, harness: str, session_id: str) -> dict:
    return {
        "param_project_id": project_id,
        "param_user_id": user_id,
        "param_harness": harness,
        "param_session_id": session_id,
    }


class SourceSnapshot:
    """Load a session's bounded canonical source once for the three projectors.

    This caches only the extraction input. Final publication checks deliberately
    call ``_source_rows`` again and recheck the mapping against current data.
    """

    def __init__(self, project_id: str, user_id: str, harness: str, session_id: str):
        self.params = _params(project_id, user_id, harness, session_id)
        self._loaded = False
        self._rows: list[dict] | None = None

    async def load(self) -> list[dict] | None:
        if not self._loaded:
            self._rows = await _source_rows(self.params)
            self._loaded = True
        return self._rows


async def _source_rows(params: dict) -> list[dict] | None:
    """Load canonical source rows, or None when they exceed the projection budget.

    A cheap aggregate preflight bounds both record count and stored bytes so an
    oversized (but valid) session never materializes gigabytes in the worker.
    """
    shape = await _query(
        """SELECT count() AS records, sum(content_length) AS bytes
        FROM session_events FINAL
        WHERE project_id = {project_id:String} AND user_id = {user_id:String}
          AND harness = {harness:String} AND session_id = {session_id:String}
          AND is_source_record = 1 FORMAT JSON""",
        params,
    )
    if shape and (
        int(shape[0].get("records") or 0) > MAX_SOURCE_RECORDS or int(shape[0].get("bytes") or 0) > MAX_SOURCE_BYTES
    ):
        return None
    return await _query(
        """SELECT line_offset, line_hash, source_sha256, layer_hash, timestamp, raw_line, raw_line_truncated,
                  is_source_record
        FROM session_events FINAL
        WHERE project_id = {project_id:String} AND user_id = {user_id:String}
          AND harness = {harness:String} AND session_id = {session_id:String}
          AND is_source_record = 1 ORDER BY line_offset
        LIMIT 50001 FORMAT JSON""",
        params,
    )


def _source_revision(rows: list[dict]) -> str | None:
    """SHA-256 of sorted canonical (line_offset, line_hash) pairs, not raw text."""
    if not rows or len(rows) > MAX_SOURCE_RECORDS:
        return None
    offsets = [row.get("line_offset") for row in rows]
    if offsets != list(range(len(rows))) or any(
        not isinstance(row.get("line_hash"), str)
        or not row["line_hash"]
        or not isinstance(row.get("raw_line"), str)
        or not row["raw_line"]
        or row.get("raw_line_truncated")
        for row in rows
    ):
        return None
    pairs = [[offset, row["line_hash"]] for offset, row in enumerate(rows)]
    return hashlib.sha256(json.dumps(pairs, separators=(",", ":")).encode()).hexdigest()


async def _mapping(
    project_id: str, user_id: str, layer_hash: str, harness: str, component_type: str = "mcp"
) -> tuple[str, int, list[dict]]:
    params = {
        "param_project_id": project_id,
        "param_user_id": user_id,
        "param_layer_hash": layer_hash,
        "param_extractor_version": CURRENT_EXTRACTOR_VERSION,
        "param_harness": harness,
        "param_component_type": component_type,
    }
    published = await _query(
        """SELECT extraction_generation, max(identity_conflict) AS conflict
        FROM layer_component_extractions
        WHERE project_id = {project_id:String} AND user_id = {user_id:String}
          AND layer_hash = {layer_hash:String} AND extractor_version = {extractor_version:UInt16}
        GROUP BY extraction_generation
        HAVING countIf(status = 'complete') > 0 AND countIf(status = 'failed') = 0
        ORDER BY extraction_generation DESC LIMIT 1 FORMAT JSON""",
        params,
    )
    if not published:
        return "pending_mapping", 0, []
    generation = int(published[0]["extraction_generation"])
    if int(published[0]["conflict"]):
        return "identity_conflict", generation, []
    # The current snapshot identity is part of mapping validity: a later
    # conflicting upload must invalidate an earlier complete extraction even
    # when its re-extraction failed.
    snapshot = await _query(
        """SELECT toUInt8("""
        + SNAPSHOT_CONFLICT_EXPR
        + """) AS conflict FROM layer_snapshots FINAL
        WHERE project_id = {project_id:String} AND user_id = {user_id:String}
          AND hash = {layer_hash:String} LIMIT 1 FORMAT JSON""",
        params,
    )
    if not snapshot:
        return "pending_mapping", 0, []
    if int(snapshot[0]["conflict"]):
        return "identity_conflict", generation, []
    params["param_generation"] = generation
    candidates = await _query(
        """SELECT local_name, scope, component_id, component_version_id, identity_status, verification_status,
               location_sha256, binding_agent, binding_placement
        FROM layer_components FINAL
        WHERE project_id = {project_id:String} AND user_id = {user_id:String}
          AND layer_hash = {layer_hash:String} AND harness = {harness:String}
          AND extractor_version = {extractor_version:UInt16}
          AND extraction_generation = {generation:UInt64} AND hash_schema_version = 2
          AND component_type = {component_type:String} FORMAT JSON""",
        params,
    )
    return "complete", generation, candidates


async def _latest_complete(params: dict, version: int, evidence_type: str = "mcp") -> dict | None:
    rows = await _query(
        """SELECT projection_generation, any(source_revision) AS source_revision,
                  max(candidate_count) AS candidate_count, max(attributed_count) AS attributed_count,
                  max(collision_count) AS collision_count, max(unmatched_count) AS unmatched_count,
                  max(unknown_result_count) AS unknown_result_count
        FROM component_activity_publications
        WHERE project_id = {project_id:String} AND user_id = {user_id:String}
          AND harness = {harness:String} AND session_id = {session_id:String}
          AND projection_version = {projection_version:UInt16} AND evidence_type = {evidence_type:String}
        GROUP BY projection_generation
        HAVING countIf(status = 'complete') > 0 AND countIf(status = 'failed') = 0
        ORDER BY projection_generation DESC LIMIT 1 FORMAT JSON""",
        params | {"param_projection_version": version, "param_evidence_type": evidence_type},
    )
    return rows[0] if rows else None


async def _published_rows(params: dict, version: int, generation: int) -> list[dict]:
    return await _query(
        """SELECT source_line_offset, source_block_key, source_line_hash, layer_hash,
                  component_type, component_id, component_version_id, tool_name, tool_use_id,
                  event_time, result_state, attribution_method, matcher_version, extractor_version, evidence_kind
        FROM component_activity FINAL
        WHERE project_id = {project_id:String} AND user_id = {user_id:String}
          AND harness = {harness:String} AND session_id = {session_id:String}
          AND projection_version = {projection_version:UInt16}
          AND projection_generation = {generation:UInt64} ORDER BY source_line_offset, source_block_key FORMAT JSON""",
        params | {"param_projection_version": version, "param_generation": generation},
    )


_STORED_FACTS = (
    "source_line_offset",
    "source_block_key",
    "source_line_hash",
    "layer_hash",
    "component_type",
    "component_id",
    "component_version_id",
    "tool_name",
    "tool_use_id",
    "result_state",
    "attribution_method",
    "matcher_version",
    "extractor_version",
    "evidence_kind",
)


def _canonical_row(row: dict) -> dict:
    """Compare only the same safe facts returned by _published_rows."""
    facts = {key: row[key] for key in _STORED_FACTS}
    for field in ("source_line_offset", "matcher_version", "extractor_version"):
        facts[field] = int(facts[field])
    return facts | {"event_time": str(row["event_time"])[:23]}


def _source_facts(rows: list[dict]) -> list[tuple]:
    """Compare layer bindings as well as source revision before publication."""
    return [
        (
            row.get("line_offset"),
            row.get("line_hash"),
            row.get("layer_hash"),
            row.get("source_sha256"),
            row.get("raw_line"),
        )
        for row in rows
    ]


async def _inputs_unchanged(
    params: dict,
    revision: str,
    source_rows: list[dict],
    generations: dict[str, int],
    project_id: str,
    user_id: str,
    harness: str,
    component_type: str = "mcp",
) -> bool:
    current_rows = await _source_rows(params)
    if current_rows is None:
        return False
    if _source_revision(current_rows) != revision or _source_facts(current_rows) != _source_facts(source_rows):
        return False
    for layer_hash, pinned_generation in generations.items():
        status, current_generation, _ = await _mapping(project_id, user_id, layer_hash, harness, component_type)
        if status != "complete" or current_generation != pinned_generation:
            return False
    return True


async def _marker(
    params: dict,
    version: int,
    generation: int,
    status: str,
    revision: str,
    counts: dict,
    evidence_type: str = "mcp",
) -> None:
    await _query(
        "INSERT INTO component_activity_publications "
        "(project_id, user_id, harness, session_id, projection_version, projection_generation, "
        "status, source_revision, candidate_count, attributed_count, collision_count, unmatched_count, "
        "unknown_result_count, evidence_type) FORMAT JSONEachRow",
        data=json.dumps(
            {
                "project_id": params["param_project_id"],
                "user_id": params["param_user_id"],
                "harness": params["param_harness"],
                "session_id": params["param_session_id"],
                "projection_version": version,
                "projection_generation": generation,
                "status": status,
                "source_revision": revision,
                "evidence_type": evidence_type,
                **counts,
            }
        ),
    )


async def _publish_unattributable(
    params: dict,
    version: int,
    revision: str,
    source_rows: list[dict],
    unattributed: dict,
    layer_hash: str,
    evidence_type: str = "mcp",
) -> dict:
    counts = {"attributed_count": 0, "collision_count": 0, **unattributed}
    generation = await next_projection_generation()
    try:
        # A conflict publishes a complete zero-row generation. It needs the same
        # fresh-source check as a normal complete marker, plus a fresh conflict
        # check: otherwise a repaired source/mapping could be hidden by this zero.
        current = await _source_rows(params)
        if (
            current is None
            or _source_revision(current) != revision
            or _source_facts(current) != _source_facts(source_rows)
            or (
                await _mapping(
                    params["param_project_id"],
                    params["param_user_id"],
                    layer_hash,
                    params["param_harness"],
                    evidence_type,
                )
            )[0]
            != "identity_conflict"
        ):
            raise ProjectionRaceError("Canonical source or conflicted mapping changed during publication")
        await _marker(params, version, generation, "complete", revision, counts, evidence_type)
    except Exception:
        try:
            await _marker(params, version, generation, "failed", revision, counts, evidence_type)
        except Exception as marker_error:
            optic.warning("failed to publish activity failure: {}", type(marker_error).__name__)
        raise
    return {
        "status": "identity_conflict",
        "generation": generation,
        "publication_version": version,
        "layer_hash": layer_hash,
        **counts,
    }


async def _index_layer(project_id: str, user_id: str, layer_hash: str) -> bool:
    """Index a snapshot the upload request could not see yet; True if a mapping may now exist.

    ClickHouse async inserts make a snapshot invisible to the upload request's
    own extraction attempt, which then finds no snapshot and publishes nothing.
    Without this, a session's mapping would wait for the nightly layer backfill.
    """
    try:
        from services.layer_components.extractor import ensure_layer_components

        result = await ensure_layer_components(project_id, user_id, layer_hash)
    except Exception as error:
        optic.warning("activity layer indexing failed: {}", type(error).__name__)
        return False
    return result.get("status") == "complete"
