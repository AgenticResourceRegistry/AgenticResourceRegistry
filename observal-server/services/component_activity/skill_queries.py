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
    PresenceCoverage,
    ProjectionCoverage,
    SkillActivityCoverage,
    SkillEvidenceCoverage,
)
from services.component_activity.projector import MAX_SOURCE_BYTES, MAX_SOURCE_RECORDS, publication_version
from services.component_activity.queries import (
    _ACTIVATIONS,
    _HARNESSES,
    _SOURCE_STATES,
    CursorScope,
    _clickhouse_array,
    _rows,
    encode_cursor,
)
from services.layer_components.queries import (
    PRESENCE_COHORT_SQL,
    presence_coverage,
    presence_params,
    presence_version_distribution,
)
from services.session_parsers.skill_evidence import kind_recorded

if TYPE_CHECKING:
    from datetime import datetime


def latest_publication_sql(evidence_type: str) -> str:
    """Latest complete, never-failed publication per scoped session for one evidence type."""
    if evidence_type not in ("skill", "hook"):
        raise ValueError("unknown evidence type")
    return (
        """SELECT user_id, harness, session_id, max(projection_generation) AS generation,
           argMax(source_revision, projection_generation) AS source_revision,
           argMax(candidate_count, projection_generation) AS candidate_count,
           argMax(attributed_count, projection_generation) AS attributed_count,
           argMax(collision_count, projection_generation) AS collision_count,
           argMax(unmatched_count, projection_generation) AS unmatched_count,
           argMax(unknown_result_count, projection_generation) AS unknown_result_count
    FROM (
        SELECT user_id, harness, session_id, projection_generation,
               any(source_revision) AS source_revision, max(candidate_count) AS candidate_count,
               max(attributed_count) AS attributed_count, max(collision_count) AS collision_count,
               max(unmatched_count) AS unmatched_count, max(unknown_result_count) AS unknown_result_count
        FROM component_activity_publications
        WHERE project_id = {project_id:String} AND projection_version = {projection_version:UInt16}
          AND evidence_type = '"""
        + evidence_type
        + """'
        GROUP BY user_id, harness, session_id, projection_generation
        HAVING countIf(status = 'complete') > 0 AND countIf(status = 'failed') = 0
    ) GROUP BY user_id, harness, session_id"""
    )


def marker_states_sql(evidence_type: str) -> str:
    """Failed current markers and complete markers of another version, per scoped session."""
    if evidence_type not in ("skill", "hook"):
        raise ValueError("unknown evidence type")
    return (
        """SELECT user_id, harness, session_id,
           countIf(status = 'failed' AND projection_version = {projection_version:UInt16}) AS failed_markers,
           countIf(status = 'complete' AND projection_version != {projection_version:UInt16}) AS other_complete
    FROM component_activity_publications WHERE project_id = {project_id:String}
      AND evidence_type = '"""
        + evidence_type
        + """'
    GROUP BY user_id, harness, session_id"""
    )


def projection_state_sql(supported: str = "{supported:Array(String)}") -> tuple[str, str]:
    """(source_state, projection_state) expressions over c (cohort), s (source), p (latest), m (markers)."""
    source = f"""multiIf(NOT has({supported}, c.harness), 'unsupported',
                   s.records = 0, 'source_missing',
                   s.records > {{max_source_records:UInt32}} OR s.bytes > {{max_source_bytes:UInt64}}, 'source_too_large',
                   s.unavailable_records > 0, 'source_unavailable',
                   s.max_offset + 1 != s.records OR s.invalid_hash_records > 0, 'source_incomplete',
                   'available')"""
    projection = f"""multiIf(NOT has({supported}, c.harness), 'unsupported',
                   s.records = 0, 'source_missing',
                   s.records > {{max_source_records:UInt32}} OR s.bytes > {{max_source_bytes:UInt64}}, 'source_too_large',
                   s.unavailable_records > 0, 'source_unavailable',
                   s.max_offset + 1 != s.records OR s.invalid_hash_records > 0, 'source_incomplete',
                   p.generation > 0 AND s.revision != p.source_revision, 'stale',
                   p.generation > 0, 'complete',
                   m.failed_markers > 0, 'failed',
                   m.other_complete > 0, 'stale',
                   'pending')"""
    return source, projection


_LATEST_SKILL_PUBLICATION = latest_publication_sql("skill")
_SKILL_MARKER_STATES = marker_states_sql("skill")

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

_SKILL_SESSIONS = (
    """SELECT c.user_id AS user_id, c.harness AS harness, c.session_id AS session_id,
           c.last_event_time AS last_event_time,
           has({supported:Array(String)}, c.harness) AS supported,
           multiIf(NOT has({supported:Array(String)}, c.harness), 'unsupported',
                   s.records = 0, 'source_missing',
                   s.records > {max_source_records:UInt32} OR s.bytes > {max_source_bytes:UInt64}, 'source_too_large',
                   s.unavailable_records > 0, 'source_unavailable',
                   s.max_offset + 1 != s.records OR s.invalid_hash_records > 0, 'source_incomplete',
                   'available') AS source_state,
           multiIf(NOT has({supported:Array(String)}, c.harness), 'unsupported',
                   s.records = 0, 'source_missing',
                   s.records > {max_source_records:UInt32} OR s.bytes > {max_source_bytes:UInt64}, 'source_too_large',
                   s.unavailable_records > 0, 'source_unavailable',
                   s.max_offset + 1 != s.records OR s.invalid_hash_records > 0, 'source_incomplete',
                   p.generation > 0 AND s.revision != p.source_revision, 'stale',
                   p.generation > 0, 'complete',
                   m.failed_markers > 0, 'failed',
                   m.other_complete > 0, 'stale',
                   'pending') AS projection_state,
           p.candidate_count AS candidate_count, p.attributed_count AS attributed_count,
           p.collision_count AS collision_count, p.unmatched_count AS unmatched_count,
           p.unknown_result_count AS unknown_result_count,
           e.available_facts AS available_facts, e.confirmed_loads AS confirmed_loads,
           e.load_attempts AS load_attempts, e.invocations AS invocations
    FROM ("""
    + PRESENCE_COHORT_SQL
    + """) AS c
    LEFT JOIN ("""
    + _LATEST_SKILL_PUBLICATION
    + """) AS p ON c.user_id = p.user_id AND c.harness = p.harness AND c.session_id = p.session_id
    LEFT JOIN ("""
    + _SKILL_MARKER_STATES
    + """) AS m ON c.user_id = m.user_id AND c.harness = m.harness AND c.session_id = m.session_id
    LEFT JOIN ("""
    + _SOURCE_STATES
    + """) AS s ON c.user_id = s.user_id AND c.harness = s.harness AND c.session_id = s.session_id
    LEFT JOIN ("""
    + _SKILL_EVIDENCE
    + """) AS e ON c.user_id = e.user_id AND c.harness = e.harness AND c.session_id = e.session_id"""
)

_SKILL_SUMMARY = (
    """SELECT countIf(supported) AS supported_present_sessions,
           countIf(NOT supported) AS unsupported_present_sessions,
           countIf(projection_state = 'complete') AS projection_complete_sessions,
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
           countIf(projection_state = 'complete' AND available_facts > 0) AS available_sessions,
           countIf(projection_state = 'complete' AND confirmed_loads > 0) AS loaded_sessions,
           sumIf(confirmed_loads, projection_state = 'complete') AS total_confirmed_loads,
           sumIf(load_attempts, projection_state = 'complete') AS total_load_attempts,
           countIf(projection_state = 'complete' AND invocations > 0) AS invoked_sessions,
           sumIf(invocations, projection_state = 'complete') AS total_invocations,
           countIf(projection_state = 'complete' AND (confirmed_loads > 0 OR invocations > 0)) AS observed_sessions
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
    denominator = projection.projection_complete_sessions
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
    for kind, limitation in (("available", SKILL_AVAILABILITY_LIMITATION), ("invoked", SKILL_INVOCATIONS_LIMITATION)):
        recording = [kind_recorded(harness, kind) for harness in distribution]
        recorded[kind] = any(recording)
        if not recorded[kind]:
            coverage.limitations.append(limitation)
        elif not all(recording):
            coverage.reasons.append(f"{kind}_not_recorded_on_some_harnesses")
    return {
        "present_sessions": coverage.presence.present_sessions,
        "present_users": coverage.presence.present_users,
        "available_sessions": _int(aggregate, "available_sessions") if recorded["available"] else None,
        "loaded_sessions": _int(aggregate, "loaded_sessions"),
        "confirmed_loads": _int(aggregate, "total_confirmed_loads"),
        "load_attempts": _int(aggregate, "total_load_attempts"),
        "invoked_sessions": _int(aggregate, "invoked_sessions") if recorded["invoked"] else None,
        "invocations": _int(aggregate, "total_invocations") if recorded["invoked"] else None,
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
        sessions.append(
            {
                "user_id": row["user_id"],
                "harness": row["harness"],
                "session_id": row["session_id"],
                "last_event_time": str(row["last_event_time"]),
                "projection_state": row["projection_state"],
                "source_state": row["source_state"],
                "available": (complete and _int(row, "available_facts") > 0)
                if kind_recorded(row["harness"], "available")
                else None,
                "confirmed_loads": _int(row, "confirmed_loads") if complete else 0,
                "load_attempts": _int(row, "load_attempts") if complete else 0,
                "invocations": (_int(row, "invocations") if complete else 0)
                if kind_recorded(row["harness"], "invoked")
                else None,
            }
        )
    next_cursor = encode_cursor(page[-1], period[1], scope=cursor_scope) if len(rows) > limit and page else None
    return sessions, next_cursor
