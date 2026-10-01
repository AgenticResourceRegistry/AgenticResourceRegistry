# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Hook activity reads: recorded runs and whether the hook could run, never MCP calls.

Reads only ``evidence_type = 'hook'`` publications and ``hook_*`` rows. The
denominator is processed present sessions where the hook could run
(``eligible``): agent-scoped hooks whose agent did not run, or ran headless,
are excluded rather than counted as no use. Recorded runs are a lower bound
where silent successes leave no record.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from observal_shared.harness_registry import HARNESS_REGISTRY
from schemas.component_activity import (
    HOOK_SILENT_LIMITATION,
    HookActivityCoverage,
    HookEligibility,
    HookEvidenceCoverage,
    PresenceCoverage,
    ProjectionCoverage,
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
from services.component_activity.skill_queries import latest_publication_sql, marker_states_sql, projection_state_sql
from services.layer_components.queries import (
    PRESENCE_COHORT_SQL,
    presence_coverage,
    presence_params,
    presence_version_distribution,
)
from services.session_parsers.hook_evidence import hook_extractor

if TYPE_CHECKING:
    from datetime import datetime

_LATEST = latest_publication_sql("hook")
_MARKERS = marker_states_sql("hook")
_SOURCE_STATE, _PROJECTION_STATE = projection_state_sql()

_HOOK_EVIDENCE = (
    """SELECT a.user_id AS user_id, a.harness AS harness, a.session_id AS session_id,
           countIf(a.evidence_kind = 'hook_ran_with_output') AS runs_with_output,
           countIf(a.evidence_kind = 'hook_failed') AS failures,
           countIf(a.evidence_kind = 'hook_blocked') AS blocks,
           countIf(a.evidence_kind = 'hook_context_eligible') AS ctx_eligible,
           countIf(a.evidence_kind = 'hook_context_headless') AS ctx_headless,
           countIf(a.evidence_kind = 'hook_context_agent_inactive') AS ctx_agent_inactive,
           countIf(a.evidence_kind = 'hook_context_mode_unknown') AS ctx_mode_unknown
    FROM component_activity AS a FINAL
    INNER JOIN ("""
    + _LATEST
    + """) AS latest
      ON a.user_id = latest.user_id AND a.harness = latest.harness AND a.session_id = latest.session_id
     AND a.projection_generation = latest.generation
    WHERE a.project_id = {project_id:String} AND a.projection_version = {projection_version:UInt16}
      AND startsWith(a.evidence_kind, 'hook_')
      AND a.component_type = 'hook' AND a.component_id = {component_id:String}
      AND ({component_version_id:String} = '' OR a.component_version_id = {component_version_id:String})
    GROUP BY a.user_id, a.harness, a.session_id"""
)

_HOOK_SESSIONS = (
    """SELECT c.user_id AS user_id, c.harness AS harness, c.session_id AS session_id,
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
           e.runs_with_output AS runs_with_output, e.failures AS failures, e.blocks AS blocks,
           multiIf(e.ctx_eligible > 0 OR e.runs_with_output + e.failures + e.blocks > 0, 'eligible',
                   e.ctx_headless > 0, 'headless',
                   e.ctx_mode_unknown > 0, 'mode_unknown',
                   e.ctx_agent_inactive > 0, 'agent_inactive',
                   'not_processed') AS eligibility
    FROM ("""
    + PRESENCE_COHORT_SQL
    + """) AS c
    LEFT JOIN ("""
    + _LATEST
    + """) AS p ON c.user_id = p.user_id AND c.harness = p.harness AND c.session_id = p.session_id
    LEFT JOIN ("""
    + _MARKERS
    + """) AS m ON c.user_id = m.user_id AND c.harness = m.harness AND c.session_id = m.session_id
    LEFT JOIN ("""
    + _SOURCE_STATES
    + """) AS s ON c.user_id = s.user_id AND c.harness = s.harness AND c.session_id = s.session_id
    LEFT JOIN ("""
    + _HOOK_EVIDENCE
    + """) AS e ON c.user_id = e.user_id AND c.harness = e.harness AND c.session_id = e.session_id"""
)

_HOOK_SUMMARY = (
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
           sumIf(candidate_count, projection_state = 'complete') AS candidate_runs,
           sumIf(attributed_count, projection_state = 'complete') AS attributed_runs,
           sumIf(collision_count, projection_state = 'complete') AS collision_runs,
           sumIf(unmatched_count, projection_state = 'complete') AS unmatched_runs,
           countIf(projection_state = 'complete' AND eligibility = 'eligible') AS eligible_sessions,
           countIf(projection_state = 'complete' AND eligibility = 'headless') AS headless_sessions,
           countIf(projection_state = 'complete' AND eligibility = 'agent_inactive') AS agent_inactive_sessions,
           countIf(projection_state = 'complete' AND eligibility = 'mode_unknown') AS mode_unknown_sessions,
           countIf(projection_state = 'complete' AND runs_with_output + failures + blocks > 0) AS observed_sessions,
           sumIf(runs_with_output, projection_state = 'complete') AS total_runs_with_output,
           sumIf(failures, projection_state = 'complete') AS total_failures,
           sumIf(blocks, projection_state = 'complete') AS total_blocks
    FROM ("""
    + _HOOK_SESSIONS
    + """) FORMAT JSON"""
)


def hook_supported_harnesses() -> list[str]:
    """Harnesses whose registry entry declares a verified hook evidence extractor."""
    return sorted(name for name, entry in HARNESS_REGISTRY.items() if entry.get("hook_evidence_extractor"))


def _params(
    project_id: str,
    component_id: str,
    component_version_id: str | None,
    period: tuple[datetime, datetime],
    component_version: str | None = None,
) -> dict:
    return presence_params(project_id, "hook", component_id, component_version_id, period) | {
        "param_component_version": component_version or "",
        "param_projection_version": publication_version(),
        "param_supported": _clickhouse_array(hook_supported_harnesses()),
        "param_max_source_records": MAX_SOURCE_RECORDS,
        "param_max_source_bytes": MAX_SOURCE_BYTES,
    }


def _int(values: dict, key: str) -> int:
    return int(values.get(key) or 0)


def build_hook_coverage(presence: dict, aggregate: dict, version: int) -> HookActivityCoverage:
    presence_block = PresenceCoverage(**{key: _int(presence, key) for key in PresenceCoverage.model_fields})
    projection = ProjectionCoverage(
        **{key: _int(aggregate, key) for key in ProjectionCoverage.model_fields if key != "publication_version"},
        publication_version=version,
    )
    eligibility = HookEligibility(**{key: _int(aggregate, key) for key in HookEligibility.model_fields})
    evidence = HookEvidenceCoverage(**{key: _int(aggregate, key) for key in HookEvidenceCoverage.model_fields})
    denominator = eligibility.eligible_sessions
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
    if eligibility.headless_sessions:
        reasons.append("agent_hook_headless_sessions")
    if eligibility.mode_unknown_sessions:
        reasons.append("session_mode_unknown")
    if evidence.collision_runs or evidence.unmatched_runs:
        reasons.append("unattributed_hook_runs")
    state = "observed" if observed else "no_recorded_runs" if denominator else "attribution_not_possible"
    return HookActivityCoverage(
        presence=presence_block,
        projection=projection,
        eligibility=eligibility,
        evidence=evidence,
        usage_rate_denominator_sessions=denominator,
        observed_sessions=observed,
        usage_rate=(observed / denominator) if denominator else None,
        attribution_state=state,
        reasons=reasons,
    )


def _silent_successes_unrecorded(harnesses) -> bool:
    return any(not getattr(hook_extractor(harness), "records_silent_success", True) for harness in harnesses)


async def hook_activity_summary(
    project_id: str,
    component_id: str,
    component_version_id: str | None,
    period: tuple[datetime, datetime],
    *,
    component_version: str | None = None,
) -> dict:
    """Exact cohort summary of hook evidence plus its own coverage block."""
    if component_version_id and not component_version:
        raise ValueError("A version-scoped summary requires a verified version label")
    params = _params(project_id, component_id, component_version_id, period, component_version)
    presence, aggregate_rows, harnesses, activation_rows, versions = await asyncio.gather(
        presence_coverage(project_id, "hook", component_id, component_version_id, period),
        _rows(_HOOK_SUMMARY, params),
        _rows(_HARNESSES, params),
        _rows(_ACTIVATIONS, params),
        presence_version_distribution(project_id, "hook", component_id, component_version_id, period),
    )
    aggregate = (aggregate_rows or [{}])[0]
    activations = (activation_rows or [{}])[0]
    coverage = build_hook_coverage(presence, aggregate, params["param_projection_version"])
    distribution = {row["harness"]: int(row["sessions"]) for row in harnesses}
    if _silent_successes_unrecorded(h for h in distribution if hook_extractor(h)):
        coverage.limitations.insert(1, HOOK_SILENT_LIMITATION)
    return {
        "present_sessions": coverage.presence.present_sessions,
        "present_users": coverage.presence.present_users,
        "eligible_sessions": coverage.eligibility.eligible_sessions,
        "sessions_with_recorded_run": coverage.observed_sessions,
        "runs_with_output": _int(aggregate, "total_runs_with_output"),
        "failures": _int(aggregate, "total_failures"),
        "blocks": _int(aggregate, "total_blocks"),
        "harness_distribution": distribution,
        "version_distribution": versions,
        "activation_actions": {
            "context_sessions": _int(activations, "context_sessions"),
            "next_session_sessions": _int(activations, "next_session_sessions"),
            "scope": "component_version" if component_version_id else "component",
        },
        "coverage": coverage,
    }


async def hook_activity_sessions(
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
        "SELECT * FROM (" + _HOOK_SESSIONS + ") " + where + " ORDER BY user_id, harness, session_id "
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
                "eligibility": row["eligibility"] if complete else "not_processed",
                "runs_with_output": _int(row, "runs_with_output") if complete else 0,
                "failures": _int(row, "failures") if complete else 0,
                "blocks": _int(row, "blocks") if complete else 0,
            }
        )
    next_cursor = encode_cursor(page[-1], period[1], scope=cursor_scope) if len(rows) > limit and page else None
    return sessions, next_cursor
