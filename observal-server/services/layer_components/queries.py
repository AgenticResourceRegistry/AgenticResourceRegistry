# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""Current-version, user-scoped published presence queries (no JSON parsing)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import services.clickhouse.client as clickhouse
from services.layer_components import CURRENT_EXTRACTOR_VERSION


def _params(project_id: str, period: tuple[datetime, datetime]) -> dict:
    start, end = period
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError("Presence period must be a nonempty, timezone-aware half-open interval")
    return {
        "param_project_id": project_id,
        "param_extractor_version": CURRENT_EXTRACTOR_VERSION,
        "param_start": start.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        "param_end": end.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
    }


def _identity_params(component_type: str, component_id: str, version_id: str | None) -> dict:
    if component_type not in {"mcp", "skill", "hook"}:
        raise ValueError("Unsupported component type")
    listing = str(UUID(component_id))
    return {
        "param_component_type": component_type,
        "param_component_id": listing,
        "param_component_version_id": str(UUID(version_id)) if version_id else "",
    }


# Select the latest complete attempt at CURRENT_EXTRACTOR_VERSION only. A later
# failed attempt cannot hide an earlier published complete attempt.
_LATEST = """SELECT project_id, user_id, layer_hash, max(extraction_generation) AS generation,
                      argMax(identity_conflict, extraction_generation) AS conflict
           FROM (
               SELECT project_id, user_id, layer_hash, extraction_generation,
                      max(identity_conflict) AS identity_conflict
               FROM layer_component_extractions
               WHERE project_id = {project_id:String} AND extractor_version = {extractor_version:UInt16}
               GROUP BY project_id, user_id, layer_hash, extraction_generation
               HAVING countIf(status = 'complete') > 0 AND countIf(status = 'failed') = 0
           ) GROUP BY project_id, user_id, layer_hash"""


# A later conflicting upload marks the *current* snapshot row as conflicted.
# If re-extraction failed afterwards, an earlier complete mapping must not keep
# producing presence: the current snapshot identity is part of validity.
_CONFLICTED_SNAPSHOTS = """SELECT user_id, hash FROM layer_snapshots FINAL
           WHERE project_id = {project_id:String}
             AND hash IN (SELECT layer_hash FROM layer_components
                          WHERE project_id = {project_id:String} AND component_type = {component_type:String}
                            AND component_id = {component_id:String})
             AND JSONExtractString(content, 'identity_status') = 'identity_conflict'"""

SNAPSHOT_CONFLICT_EXPR = "JSONExtractString(content, 'identity_status') = 'identity_conflict'"


async def _rows(sql: str, params: dict) -> list[dict]:
    response = await clickhouse._query(sql, params)
    response.raise_for_status()
    return response.json().get("data", [])


def presence_params(
    project_id: str,
    component_type: str,
    component_id: str,
    component_version_id: str | None,
    period: tuple[datetime, datetime],
) -> dict:
    """Bound ClickHouse parameters shared by every presence-cohort consumer."""
    return _params(project_id, period) | _identity_params(component_type, component_id, component_version_id)


# Distinct eligible sessions from verified v2, current, non-conflicted mappings.
# Reused as a subquery by the component-activity API so both share one cohort.
PRESENCE_COHORT_SQL = (
    """SELECT DISTINCT s.user_id AS user_id, s.harness AS harness, s.session_id AS session_id,
                       s.layer_hash AS layer_hash, s.last_event_time AS last_event_time
    FROM session_stats_agg AS s FINAL
    INNER JOIN (
        SELECT DISTINCT c.project_id, c.user_id, c.layer_hash
        FROM layer_components AS c FINAL
        INNER JOIN ("""
    + _LATEST
    + """) AS published
          ON c.project_id = published.project_id AND c.user_id = published.user_id
         AND c.layer_hash = published.layer_hash AND c.extraction_generation = published.generation
        WHERE c.project_id = {project_id:String} AND c.extractor_version = {extractor_version:UInt16}
          AND c.hash_schema_version = 2 AND startsWith(c.layer_hash, 'v2_') AND published.conflict = 0
          AND c.component_type = {component_type:String} AND c.component_id = {component_id:String}
          AND ({component_version_id:String} = '' OR c.component_version_id = {component_version_id:String})
          AND c.identity_status = 'resolved' AND c.verification_status = 'verified'
          AND (c.user_id, c.layer_hash) NOT IN ("""
    + _CONFLICTED_SNAPSHOTS
    + """)
    ) AS present
      ON s.project_id = present.project_id AND s.user_id = present.user_id AND s.layer_hash = present.layer_hash
    WHERE s.project_id = {project_id:String} AND s.layer_hash != ''
      AND s.last_event_time >= toDateTime64({start:String}, 3, 'UTC')
      AND s.last_event_time < toDateTime64({end:String}, 3, 'UTC')"""
)


async def presence_cohort(
    project_id: str,
    component_type: str,
    component_id: str,
    component_version_id: str | None,
    period: tuple[datetime, datetime],
) -> list[dict]:
    """Return distinct eligible sessions from verified v2, current mappings."""
    params = presence_params(project_id, component_type, component_id, component_version_id, period)
    sql = PRESENCE_COHORT_SQL + "\n    ORDER BY last_event_time, user_id, harness, session_id FORMAT JSON"
    return await _rows(sql, params)


async def presence_version_distribution(
    project_id: str,
    component_type: str,
    component_id: str,
    component_version_id: str | None,
    period: tuple[datetime, datetime],
) -> dict[str, int]:
    """Count present sessions by the resolved listing version in their published layer.

    A session may have more than one verified version installed. Deduplicate
    occurrences by the full session key and version, not by listing alias.
    """
    params = presence_params(project_id, component_type, component_id, component_version_id, period)
    sql = (
        "SELECT version, count() AS sessions FROM ("
        "SELECT DISTINCT p.user_id, p.harness, p.session_id, "
        "if(c.component_version_id = '' OR c.raw_version = '', 'unknown', c.raw_version) AS version FROM ("
        + PRESENCE_COHORT_SQL
        + ") AS p INNER JOIN layer_components AS c FINAL "
        "ON c.project_id = {project_id:String} AND c.user_id = p.user_id AND c.layer_hash = p.layer_hash "
        "INNER JOIN ("
        + _LATEST
        + ") AS published ON c.project_id = published.project_id AND c.user_id = published.user_id "
        "AND c.layer_hash = published.layer_hash AND c.extraction_generation = published.generation "
        "WHERE c.extractor_version = {extractor_version:UInt16} AND published.conflict = 0 "
        "AND c.component_type = {component_type:String} AND c.component_id = {component_id:String} "
        "AND ({component_version_id:String} = '' OR c.component_version_id = {component_version_id:String}) "
        "AND c.identity_status = 'resolved' AND c.verification_status = 'verified'"
        ") GROUP BY version ORDER BY version FORMAT JSON"
    )
    rows = await _rows(sql, params)
    return {row["version"]: int(row["sessions"]) for row in rows}


async def presence_coverage(
    project_id: str,
    component_type: str,
    component_id: str,
    component_version_id: str | None,
    period: tuple[datetime, datetime],
) -> dict:
    """Report exact presence-stage coverage; unknown is never absence.

    Occurrence diagnostic counters cover all components in a session's layer,
    not just the selected listing. Only present_sessions/present_users are
    target-specific; never use a layer-level verified count as target presence.
    """
    params = _params(project_id, period) | _identity_params(component_type, component_id, component_version_id)
    sql = (
        """SELECT count() AS eligible_sessions,
        countIf(s.layer_hash = '') AS missing_hash_sessions,
        countIf(s.layer_hash != '' AND NOT startsWith(s.layer_hash, 'v2_')) AS legacy_hash_sessions,
        countIf(startsWith(s.layer_hash, 'v2_')) AS v2_sessions,
        countIf(startsWith(s.layer_hash, 'v2_') AND snap.has_snapshot = 0) AS snapshot_missing_sessions,
        countIf(startsWith(s.layer_hash, 'v2_') AND snap.has_snapshot = 1 AND p.has_mapping = 0
                AND attempts.failed_attempts = 0 AND attempts.older_complete = 0) AS mapping_pending_sessions,
        countIf(startsWith(s.layer_hash, 'v2_') AND snap.has_snapshot = 1 AND p.has_mapping = 0
                AND attempts.failed_attempts > 0) AS mapping_failed_sessions,
        countIf(startsWith(s.layer_hash, 'v2_') AND p.has_mapping = 0
                AND attempts.older_complete > 0) AS stale_extractor_sessions,
        countIf(p.conflict = 1 OR snap.snapshot_conflict = 1) AS identity_conflict_sessions,
        countIf(p.has_mapping = 1 AND p.conflict = 0 AND snap.snapshot_conflict = 0) AS mapping_complete_sessions,
        countIf(d.unresolved > 0) AS unresolved_identity_sessions,
        countIf(d.ambiguous > 0) AS ambiguous_identity_sessions,
        countIf(d.unverified > 0) AS unverified_presence_sessions,
        countIf(d.drifted > 0) AS drifted_presence_sessions,
        countIf(d.verified > 0 AND snap.snapshot_conflict = 0) AS verified_presence_sessions
    FROM session_stats_agg AS s FINAL
    LEFT JOIN (SELECT project_id, user_id, hash AS layer_hash, 1 AS has_snapshot,
                      toUInt8("""
        + SNAPSHOT_CONFLICT_EXPR
        + """) AS snapshot_conflict
               FROM layer_snapshots FINAL WHERE project_id = {project_id:String}) AS snap
      ON s.project_id = snap.project_id AND s.user_id = snap.user_id AND s.layer_hash = snap.layer_hash
    LEFT JOIN (SELECT project_id, user_id, layer_hash, generation, conflict, 1 AS has_mapping
               FROM ("""
        + _LATEST
        + """)) AS p
      ON s.project_id = p.project_id AND s.user_id = p.user_id AND s.layer_hash = p.layer_hash
    LEFT JOIN (SELECT project_id, user_id, layer_hash,
                      countIf(status = 'failed' AND extractor_version = {extractor_version:UInt16}) AS failed_attempts,
                      countIf(status = 'complete' AND extractor_version != {extractor_version:UInt16}) AS older_complete
               FROM layer_component_extractions WHERE project_id = {project_id:String}
               GROUP BY project_id, user_id, layer_hash) AS attempts
      ON s.project_id = attempts.project_id AND s.user_id = attempts.user_id AND s.layer_hash = attempts.layer_hash
    LEFT JOIN (SELECT c.project_id, c.user_id, c.layer_hash, c.extraction_generation,
                      countIf(c.identity_status = 'unresolved') AS unresolved,
                      countIf(c.identity_status = 'ambiguous') AS ambiguous,
                      countIf(c.verification_status = 'unverified') AS unverified,
                      countIf(c.verification_status IN ('drifted', 'missing')) AS drifted,
                      countIf(c.identity_status = 'resolved' AND c.verification_status = 'verified') AS verified
               FROM layer_components AS c FINAL
               WHERE c.project_id = {project_id:String} AND c.extractor_version = {extractor_version:UInt16}
               GROUP BY c.project_id, c.user_id, c.layer_hash, c.extraction_generation) AS d
      ON s.project_id = d.project_id AND s.user_id = d.user_id AND s.layer_hash = d.layer_hash
     AND p.generation = d.extraction_generation
    WHERE s.project_id = {project_id:String}
      AND s.last_event_time >= toDateTime64({start:String}, 3, 'UTC')
      AND s.last_event_time < toDateTime64({end:String}, 3, 'UTC') FORMAT JSON"""
    )
    rows = await _rows(sql, params)
    coverage = rows[0] if rows else {}
    # Count in ClickHouse rather than transferring every matching session to
    # Python; a 90-day window can contain an unbounded number of sessions.
    cohort = await _rows(
        "SELECT count() AS present_sessions, uniqExact(user_id) AS present_users FROM ("
        + PRESENCE_COHORT_SQL
        + ") FORMAT JSON",
        params,
    )
    counts = cohort[0] if cohort else {}
    return {
        **{key: int(value) for key, value in coverage.items()},
        "present_sessions": int(counts.get("present_sessions") or 0),
        "present_users": int(counts.get("present_users") or 0),
        "current_extractor_version": CURRENT_EXTRACTOR_VERSION,
    }
