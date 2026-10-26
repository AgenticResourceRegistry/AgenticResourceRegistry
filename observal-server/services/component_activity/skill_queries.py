# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Owner-facing skill evidence reads over the published projections.

Mirrors the MCP activity reads but never shares their meaning: rows are
``evidence_kind LIKE 'skill_%'`` from the latest complete, non-failed
``evidence_type = 'skill'`` publication of each scoped session. Every read
starts from the shared verified-presence cohort and binds project, user,
harness and session. MCP call totals, coverage and model evidence never read
skill rows, and a complete MCP publication says nothing about skill coverage.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from observal_shared.harness_registry import HARNESS_REGISTRY
from schemas.component_activity import (
    SKILL_AVAILABILITY_LIMITATION,
    SKILL_INVOCATIONS_LIMITATION,
    SKILL_LOADS_LIMITATION,
    PresenceCoverage,
    ProjectionCoverage,
    SkillActivityCoverage,
    SkillEvidenceCoverage,
)
from services.component_activity.projector import MAX_SOURCE_BYTES, MAX_SOURCE_RECORDS, publication_version
from services.component_activity.queries import (
    _ACTIVATIONS,
    _HARNESSES,
    CursorScope,
    _clickhouse_array,
    _rows,
    cohort_session_joins_sql,
    encode_cursor,
    latest_publication_sql,
    marker_states_sql,
    projection_state_sql,
)
from services.layer_components.queries import (
    _LATEST as _LATEST_LAYER_COMPONENTS,
)
from services.layer_components.queries import (
    presence_coverage,
    presence_params,
    presence_version_distribution,
)
from services.session_parsers.skill_evidence import kind_recorded

if TYPE_CHECKING:
    from datetime import datetime


_LATEST_SKILL_PUBLICATION = latest_publication_sql("skill")
_SKILL_MARKER_STATES = marker_states_sql("skill")
_SOURCE_STATE, _PROJECTION_STATE = projection_state_sql()

_SKILL_EVIDENCE = (
    """SELECT a.user_id AS user_id, a.harness AS harness, a.session_id AS session_id,
           countIf(a.evidence_kind = 'skill_available') AS available_facts,
           countIf(a.evidence_kind = 'skill_load' AND a.result_state = 'success') AS confirmed_loads,
           countIf(a.evidence_kind = 'skill_load' AND a.result_state != 'success') AS load_attempts,
           countIf(a.evidence_kind = 'skill_invoked') AS invocations
    FROM component_activity AS a FINAL
    INNER JOIN ("""
    + _LATEST_SKILL_PUBLICATION
    + """) AS latest
      ON a.user_id = latest.user_id AND a.harness = latest.harness AND a.session_id = latest.session_id
     AND a.projection_generation = latest.generation
    WHERE a.project_id = {project_id:String} AND a.projection_version = {projection_version:UInt16}
      AND a.evidence_kind IN ('skill_available', 'skill_load', 'skill_invoked')
      AND a.component_type = 'skill' AND a.component_id = {component_id:String}
      AND ({component_version_id:String} = '' OR a.component_version_id = {component_version_id:String})
    GROUP BY a.user_id, a.harness, a.session_id"""
)

# Presence identifies a listing, not the scope of the install that made it
# present. A project-only extractor cannot turn a user-scope (or mixed-scope)
# skill into a measured zero. Select only verified, published mappings for
# this exact listing/version, with one row per layer/harness.
_SKILL_SCOPES = (
    """SELECT c.user_id, c.harness, c.layer_hash,
           countIf(c.scope = 'project') AS project_installs,
           countIf(c.scope != 'project') AS other_installs
    FROM layer_components AS c FINAL
    INNER JOIN ("""
    + _LATEST_LAYER_COMPONENTS
    + """) AS published
      ON c.project_id = published.project_id AND c.user_id = published.user_id
     AND c.layer_hash = published.layer_hash AND c.extraction_generation = published.generation
    WHERE c.project_id = {project_id:String} AND c.extractor_version = {extractor_version:UInt16}
      AND c.hash_schema_version = 2 AND startsWith(c.layer_hash, 'v2_') AND published.conflict = 0
      AND c.component_type = 'skill' AND c.component_id = {component_id:String}
      AND ({component_version_id:String} = '' OR c.component_version_id = {component_version_id:String})
      AND c.identity_status = 'resolved' AND c.verification_status = 'verified'
    GROUP BY c.user_id, c.harness, c.layer_hash"""
)

_SKILL_SESSIONS = (
    """SELECT c.user_id AS user_id, c.harness AS harness, c.session_id AS session_id,
           (NOT has({project_only:Array(String)}, c.harness)
            OR (scope_map.project_installs > 0 AND scope_map.other_installs = 0)) AS scope_measurable,
           c.last_event_time AS last_event_time,
           has({supported:Array(String)}, c.harness) AS supported,
           """
    + _SOURCE_STATE
    + """ AS source_state,
           """
    + _PROJECTION_STATE
    + """ AS projection_state,
           p.candidate_count AS candidate_count, p.attributed_count AS attributed_count,
           p.collision_count AS collision_count, p.unmatched_count AS unmatched_count,
           p.unknown_result_count AS unknown_result_count,
           e.available_facts AS available_facts, e.confirmed_loads AS confirmed_loads,
           e.load_attempts AS load_attempts, e.invocations AS invocations
"""
    + cohort_session_joins_sql(_LATEST_SKILL_PUBLICATION, _SKILL_MARKER_STATES, _SKILL_EVIDENCE, "e")
    + """ LEFT JOIN ("""
    + _SKILL_SCOPES
    + """) AS scope_map ON c.user_id = scope_map.user_id AND c.harness = scope_map.harness
                       AND c.layer_hash = scope_map.layer_hash"""
)

_SKILL_SUMMARY = (
    """SELECT countIf(supported) AS supported_present_sessions,
           countIf(NOT supported) AS unsupported_present_sessions,
           countIf(projection_state = 'complete') AS projection_complete_sessions,
           countIf(projection_state = 'complete' AND has({use_capable:Array(String)}, harness)
                   AND scope_measurable) AS usage_measurable_sessions,
           countIf(projection_state = 'complete' AND has({project_only:Array(String)}, harness)
                   AND NOT scope_measurable) AS scope_unmeasurable_sessions,
           countIf(has({available_capable:Array(String)}, harness) AND scope_measurable)
               AS available_scope_present_sessions,
           countIf(has({load_capable:Array(String)}, harness) AND scope_measurable)
               AS load_scope_present_sessions,
           countIf(has({invoked_capable:Array(String)}, harness) AND scope_measurable)
               AS invoked_scope_present_sessions,
           countIf(projection_state = 'complete' AND has({available_capable:Array(String)}, harness)
                   AND scope_measurable) AS available_measurable_sessions,
           countIf(projection_state = 'complete' AND has({load_capable:Array(String)}, harness)
                   AND scope_measurable) AS load_measurable_sessions,
           countIf(projection_state = 'complete' AND has({invoked_capable:Array(String)}, harness)
                   AND scope_measurable) AS invoked_measurable_sessions,
           countIf(projection_state = 'pending') AS projection_pending_sessions,
           countIf(projection_state = 'failed') AS projection_failed_sessions,
           countIf(projection_state = 'stale') AS projection_stale_sessions,
           countIf(source_state = 'source_missing') AS source_missing_sessions,
           countIf(source_state = 'source_unavailable') AS source_unavailable_sessions,
           countIf(source_state = 'source_incomplete') AS source_incomplete_sessions,
           countIf(source_state = 'source_too_large') AS source_too_large_sessions,
           sumIf(candidate_count, projection_state = 'complete') AS candidate_facts,
           sumIf(attributed_count, projection_state = 'complete') AS attributed_facts,
           sumIf(collision_count, projection_state = 'complete') AS collision_facts,
           sumIf(unmatched_count, projection_state = 'complete') AS unmatched_facts,
           sumIf(unknown_result_count, projection_state = 'complete') AS unknown_load_results,
           countIf(projection_state = 'complete' AND scope_measurable AND available_facts > 0)
               AS available_sessions,
           countIf(projection_state = 'complete' AND scope_measurable AND confirmed_loads > 0)
               AS loaded_sessions,
           sumIf(confirmed_loads, projection_state = 'complete' AND scope_measurable) AS total_confirmed_loads,
           sumIf(load_attempts, projection_state = 'complete' AND scope_measurable) AS total_load_attempts,
           countIf(projection_state = 'complete' AND scope_measurable AND invocations > 0)
               AS invoked_sessions,
           sumIf(invocations, projection_state = 'complete' AND scope_measurable) AS total_invocations,
           countIf(projection_state = 'complete' AND scope_measurable
                   AND (confirmed_loads > 0 OR invocations > 0)) AS observed_sessions
    FROM ("""
    + _SKILL_SESSIONS
    + """) FORMAT JSON"""
)


def skill_supported_harnesses() -> list[str]:
    """Harnesses whose registry entry declares a verified skill evidence extractor."""
    return sorted(name for name, entry in HARNESS_REGISTRY.items() if entry.get("skill_evidence_extractor"))


def _params(
    project_id: str,
    component_id: str,
    component_version_id: str | None,
    period: tuple[datetime, datetime],
    component_version: str | None = None,
) -> dict:
    return presence_params(project_id, "skill", component_id, component_version_id, period) | {
        "param_component_version": component_version or "",
        "param_projection_version": publication_version(),
        "param_supported": _clickhouse_array(skill_supported_harnesses()),
        "param_use_capable": _clickhouse_array(
            [
                name
                for name in skill_supported_harnesses()
                if kind_recorded(name, "load") or kind_recorded(name, "invoked")
            ]
        ),
        "param_project_only": _clickhouse_array(
            [
                name
                for name in skill_supported_harnesses()
                if any(kind_recorded(name, kind, "project") for kind in ("available", "load", "invoked"))
                and not any(kind_recorded(name, kind, "user") for kind in ("available", "load", "invoked"))
            ]
        ),
        **{
            f"param_{kind}_capable": _clickhouse_array(
                [name for name in skill_supported_harnesses() if kind_recorded(name, kind)]
            )
            for kind in ("available", "load", "invoked")
        },
        "param_max_source_records": MAX_SOURCE_RECORDS,
        "param_max_source_bytes": MAX_SOURCE_BYTES,
    }


def _int(values: dict, key: str) -> int:
    return int(values.get(key) or 0)


def build_skill_coverage(presence: dict, aggregate: dict, version: int) -> SkillActivityCoverage:
    presence_block = PresenceCoverage(**{key: _int(presence, key) for key in PresenceCoverage.model_fields})
    projection = ProjectionCoverage(
        **{key: _int(aggregate, key) for key in ProjectionCoverage.model_fields if key != "publication_version"},
        publication_version=version,
    )
    evidence = SkillEvidenceCoverage(**{key: _int(aggregate, key) for key in SkillEvidenceCoverage.model_fields})
    # A complete availability-only projection is not a denominator for use.
    denominator = (
        _int(aggregate, "usage_measurable_sessions")
        if "usage_measurable_sessions" in aggregate
        else (projection.projection_complete_sessions)
    )
    observed = _int(aggregate, "observed_sessions")
    reasons: list[str] = []
    if presence_block.present_sessions == 0:
        reasons.append("no_present_sessions")
    if projection.unsupported_present_sessions:
        reasons.append("unsupported_harness_sessions")
    for field in (
        "projection_pending_sessions",
        "projection_failed_sessions",
        "projection_stale_sessions",
        "source_missing_sessions",
        "source_unavailable_sessions",
        "source_incomplete_sessions",
        "source_too_large_sessions",
    ):
        if getattr(projection, field):
            reasons.append(field)
    if evidence.collision_facts or evidence.unmatched_facts:
        reasons.append("unattributed_skill_facts")
    if evidence.unknown_load_results:
        reasons.append("unconfirmed_load_attempts")
    scope_unmeasurable = _int(aggregate, "scope_unmeasurable_sessions")
    if scope_unmeasurable:
        reasons.append("skill_scope_not_recorded")
    if projection.projection_complete_sessions > denominator + scope_unmeasurable:
        reasons.append("availability_only_harness_sessions")
    state = "observed" if observed else "no_observed_skill_use" if denominator else "attribution_not_possible"
    return SkillActivityCoverage(
        presence=presence_block,
        projection=projection,
        evidence=evidence,
        usage_rate_denominator_sessions=denominator,
        observed_sessions=observed,
        usage_rate=(observed / denominator) if denominator else None,
        attribution_state=state,
        reasons=reasons,
    )


async def skill_activity_summary(
    project_id: str,
    component_id: str,
    component_version_id: str | None,
    period: tuple[datetime, datetime],
    *,
    component_version: str | None = None,
) -> dict:
    """Exact cohort summary of skill evidence plus its own coverage block."""
    if component_version_id and not component_version:
        raise ValueError("A version-scoped summary requires a verified version label")
    params = _params(project_id, component_id, component_version_id, period, component_version)
    presence, aggregate_rows, harnesses, activation_rows, versions = await asyncio.gather(
        presence_coverage(project_id, "skill", component_id, component_version_id, period),
        _rows(_SKILL_SUMMARY, params),
        _rows(_HARNESSES, params),
        _rows(_ACTIVATIONS, params),
        presence_version_distribution(project_id, "skill", component_id, component_version_id, period),
    )
    aggregate = (aggregate_rows or [{}])[0]
    activations = (activation_rows or [{}])[0]
    coverage = build_skill_coverage(presence, aggregate, params["param_projection_version"])
    distribution = {row["harness"]: int(row["sessions"]) for row in harnesses}
    # A kind is measurable only on harnesses that record it; elsewhere it is
    # unknown, never a measured zero. A mixed cohort's count is partial.
    recorded: dict[str, bool] = {}
    for kind, limitation in (
        ("available", SKILL_AVAILABILITY_LIMITATION),
        ("load", SKILL_LOADS_LIMITATION),
        ("invoked", SKILL_INVOCATIONS_LIMITATION),
    ):
        recording = [kind_recorded(harness, kind) for harness in distribution]
        recorded[kind] = _int(aggregate, f"{kind}_measurable_sessions") > 0
        if not _int(aggregate, f"{kind}_scope_present_sessions"):
            coverage.limitations.append(limitation)
        elif recorded[kind] and not all(recording):
            coverage.reasons.append(f"{kind}_not_recorded_on_some_harnesses")
        elif recorded[kind] and _int(aggregate, "scope_unmeasurable_sessions"):
            coverage.reasons.append(f"{kind}_not_recorded_on_some_scopes")
    projected = coverage.projection.projection_complete_sessions > 0
    return {
        "present_sessions": coverage.presence.present_sessions,
        "present_users": coverage.presence.present_users,
        "available_sessions": _int(aggregate, "available_sessions") if projected and recorded["available"] else None,
        "loaded_sessions": _int(aggregate, "loaded_sessions") if projected and recorded["load"] else None,
        "confirmed_loads": _int(aggregate, "total_confirmed_loads") if projected and recorded["load"] else None,
        "load_attempts": _int(aggregate, "total_load_attempts") if projected and recorded["load"] else None,
        "invoked_sessions": _int(aggregate, "invoked_sessions") if projected and recorded["invoked"] else None,
        "invocations": _int(aggregate, "total_invocations") if projected and recorded["invoked"] else None,
        "harness_distribution": distribution,
        "version_distribution": versions,
        "activation_actions": {
            "context_sessions": _int(activations, "context_sessions"),
            "next_session_sessions": _int(activations, "next_session_sessions"),
            "scope": "component_version" if component_version_id else "component",
        },
        "coverage": coverage,
    }


async def skill_activity_sessions(
    project_id: str,
    component_id: str,
    component_version_id: str | None,
    period: tuple[datetime, datetime],
    *,
    limit: int,
    cursor: list[str] | None,
    cursor_scope: CursorScope,
) -> tuple[list[dict], str | None]:
    """Keyset page by immutable scoped session identity, like the MCP session list."""
    if not 1 <= limit <= 100:
        raise ValueError("limit out of bounds")
    if cursor_scope[0] != project_id:
        raise ValueError("cursor project mismatch")
    params = _params(project_id, component_id, component_version_id, period)
    params["param_limit"] = limit + 1
    where = ""
    if cursor is not None:
        where = "WHERE (user_id, harness, session_id) > ({after_user:String}, {after_harness:String}, {after_session:String})"
        params.update(param_after_user=cursor[0], param_after_harness=cursor[1], param_after_session=cursor[2])
    rows = await _rows(
        "SELECT * FROM (" + _SKILL_SESSIONS + f") {where} ORDER BY user_id, harness, session_id "
        "LIMIT {limit:UInt16} FORMAT JSON",
        params,
    )
    page = rows[:limit]
    sessions = []
    for row in page:
        complete = row["projection_state"] == "complete"
        measurable = complete and bool(row["scope_measurable"])
        sessions.append(
            {
                "user_id": row["user_id"],
                "harness": row["harness"],
                "session_id": row["session_id"],
                "last_event_time": str(row["last_event_time"]),
                "projection_state": row["projection_state"],
                "source_state": row["source_state"],
                "available": (_int(row, "available_facts") > 0)
                if measurable and kind_recorded(row["harness"], "available")
                else None,
                "confirmed_loads": _int(row, "confirmed_loads")
                if measurable and kind_recorded(row["harness"], "load")
                else None,
                "load_attempts": _int(row, "load_attempts")
                if measurable and kind_recorded(row["harness"], "load")
                else None,
                "invocations": _int(row, "invocations")
                if measurable and kind_recorded(row["harness"], "invoked")
                else None,
            }
        )
    next_cursor = encode_cursor(page[-1], period[1], scope=cursor_scope) if len(rows) > limit and page else None
    return sessions, next_cursor
