# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""Shared D§7 coverage contract for component observability and Insights.

Pure functions only: callers supply exact counts from the presence and
activity queries. A usage-rate denominator only counts present sessions on a
supported harness that have a complete, current activity publication, so an
unmapped/unsupported/pending session is never read as "no use".
"""

from __future__ import annotations

from schemas.component_activity import (
    ActivityCoverage,
    CallCoverage,
    PresenceCoverage,
    ProjectionCoverage,
)


def _int(values: dict, key: str) -> int:
    return int(values.get(key) or 0)


def build_coverage(presence: dict, activity: dict, publication_version: int) -> ActivityCoverage:
    """Combine presence-stage and activity-stage counts into one coverage block.

    ``activity`` holds per-cohort aggregates from the activity query:
    supported/unsupported present sessions, per-state projection counts,
    session-level call counters and ``observed_sessions``.
    """
    presence_block = PresenceCoverage(**{key: _int(presence, key) for key in PresenceCoverage.model_fields})
    projection = ProjectionCoverage(
        **{key: _int(activity, key) for key in ProjectionCoverage.model_fields if key != "publication_version"},
        publication_version=publication_version,
    )
    calls = CallCoverage(**{key: _int(activity, key) for key in CallCoverage.model_fields})
    denominator = projection.projection_complete_sessions
    observed = _int(activity, "observed_sessions")

    reasons: list[str] = []
    if presence_block.present_sessions == 0:
        reasons.append("no_present_sessions")
    if projection.unsupported_present_sessions:
        reasons.append("unsupported_harness_sessions")
    if projection.projection_pending_sessions:
        reasons.append("projection_pending_sessions")
    if projection.projection_failed_sessions:
        reasons.append("projection_failed_sessions")
    if projection.projection_stale_sessions:
        reasons.append("projection_stale_sessions")
    for field in (
        "source_missing_sessions",
        "source_unavailable_sessions",
        "source_incomplete_sessions",
        "source_too_large_sessions",
    ):
        if getattr(projection, field):
            reasons.append(field)
    if calls.collision_calls or calls.unmatched_calls:
        reasons.append("unattributed_calls")

    if observed:
        state = "observed"
    elif denominator:
        state = "no_observed_calls"
    else:
        state = "attribution_not_possible"
    return ActivityCoverage(
        presence=presence_block,
        projection=projection,
        calls=calls,
        usage_rate_denominator_sessions=denominator,
        observed_sessions=observed,
        usage_rate=(observed / denominator) if denominator else None,
        attribution_state=state,
        reasons=reasons,
    )
