# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""Owner-facing component activity reads over the published projections.

Every read starts from the shared presence cohort and binds project, user,
harness and session. Activity rows come only from the latest complete,
non-failed generation at the current publication version for that exact
scoped session, filtered by component (and version when given).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import services.clickhouse.client as clickhouse
from config import settings
from observal_shared.harness_registry import HARNESS_REGISTRY
from services.component_activity.coverage import build_coverage
from services.component_activity.projector import MAX_SOURCE_BYTES, MAX_SOURCE_RECORDS, publication_version
from services.layer_components.queries import PRESENCE_COHORT_SQL, presence_coverage, presence_params

MAX_REFERENCES_PER_SESSION = 20

# Latest complete, non-failed generation per scoped session at the current version.
_LATEST_PUBLICATION = """SELECT user_id, harness, session_id, max(projection_generation) AS generation,
           argMax(candidate_count, projection_generation) AS candidate_count,
           argMax(attributed_count, projection_generation) AS attributed_count,
           argMax(collision_count, projection_generation) AS collision_count,
           argMax(unmatched_count, projection_generation) AS unmatched_count,
           argMax(unknown_result_count, projection_generation) AS unknown_result_count
    FROM (
        SELECT user_id, harness, session_id, projection_generation,
               max(candidate_count) AS candidate_count, max(attributed_count) AS attributed_count,
               max(collision_count) AS collision_count, max(unmatched_count) AS unmatched_count,
               max(unknown_result_count) AS unknown_result_count
        FROM component_activity_publications
        WHERE project_id = {project_id:String} AND projection_version = {projection_version:UInt16}
        GROUP BY user_id, harness, session_id, projection_generation
        HAVING countIf(status = 'complete') > 0 AND countIf(status = 'failed') = 0
    ) GROUP BY user_id, harness, session_id"""

_MARKER_STATES = """SELECT user_id, harness, session_id,
           countIf(status = 'failed' AND projection_version = {projection_version:UInt16}) AS failed_markers,
           countIf(status = 'complete' AND projection_version != {projection_version:UInt16}) AS other_complete
    FROM component_activity_publications WHERE project_id = {project_id:String}
    GROUP BY user_id, harness, session_id"""

_COMPONENT_ACTIVITY = (
    """SELECT a.user_id AS user_id, a.harness AS harness, a.session_id AS session_id,
           count() AS calls, countIf(a.result_state = 'success') AS successes,
           countIf(a.result_state = 'error') AS errors, countIf(a.result_state = 'unknown') AS unknowns,
           arraySlice(arraySort(groupArray((a.source_line_offset, a.source_block_key, a.result_state))),
                      1, {max_refs:UInt16}) AS refs
    FROM component_activity AS a FINAL
    INNER JOIN ("""
    + _LATEST_PUBLICATION
    + """) AS latest
      ON a.user_id = latest.user_id AND a.harness = latest.harness AND a.session_id = latest.session_id
     AND a.projection_generation = latest.generation
    WHERE a.project_id = {project_id:String} AND a.projection_version = {projection_version:UInt16}
      AND a.component_type = {component_type:String} AND a.component_id = {component_id:String}
      AND ({component_version_id:String} = '' OR a.component_version_id = {component_version_id:String})
    GROUP BY a.user_id, a.harness, a.session_id"""
)

# Canonical source availability for cohort sessions, mirroring the projector's
# own preconditions (contiguous offsets, retained untruncated raw lines, budget).
# ``empty(raw_line)`` reads only string sizes; raw text never leaves ClickHouse.
_SOURCE_STATES = (
    """SELECT user_id, harness, session_id, count() AS records,
           countIf(empty(raw_line) OR raw_line_truncated = 1) AS unavailable_records,
           countIf(empty(line_hash)) AS invalid_hash_records,
           max(line_offset) AS max_offset, sum(content_length) AS bytes
    FROM session_events FINAL
    WHERE project_id = {project_id:String} AND is_source_record = 1
      AND (user_id, harness, session_id) IN (SELECT user_id, harness, session_id FROM ("""
    + PRESENCE_COHORT_SQL
    + """))
    GROUP BY user_id, harness, session_id"""
)

# One row per present session: projection state and this component's activity.
_SESSIONS = (
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
                   p.generation > 0, 'complete',
                   m.failed_markers > 0, 'failed',
                   m.other_complete > 0, 'stale',
                   s.records = 0, 'source_missing',
                   s.records > {max_source_records:UInt32} OR s.bytes > {max_source_bytes:UInt64}, 'source_too_large',
                   s.unavailable_records > 0, 'source_unavailable',
                   s.max_offset + 1 != s.records, 'source_incomplete',
                   'pending') AS projection_state,
           p.candidate_count AS candidate_count, p.attributed_count AS attributed_count,
           p.collision_count AS collision_count, p.unmatched_count AS unmatched_count,
           p.unknown_result_count AS unknown_result_count,
           a.calls AS calls, a.successes AS successes, a.errors AS errors, a.unknowns AS unknowns,
           a.refs AS refs
    FROM ("""
    + PRESENCE_COHORT_SQL
    + """) AS c
    LEFT JOIN ("""
    + _LATEST_PUBLICATION
    + """) AS p ON c.user_id = p.user_id AND c.harness = p.harness AND c.session_id = p.session_id
    LEFT JOIN ("""
    + _MARKER_STATES
    + """) AS m ON c.user_id = m.user_id AND c.harness = m.harness AND c.session_id = m.session_id
    LEFT JOIN ("""
    + _SOURCE_STATES
    + """) AS s ON c.user_id = s.user_id AND c.harness = s.harness AND c.session_id = s.session_id
    LEFT JOIN ("""
    + _COMPONENT_ACTIVITY
    + """) AS a ON c.user_id = a.user_id AND c.harness = a.harness AND c.session_id = a.session_id"""
)

_SUMMARY = (
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
           sumIf(candidate_count, projection_state = 'complete') AS candidate_calls,
           sumIf(attributed_count, projection_state = 'complete') AS attributed_calls,
           sumIf(collision_count, projection_state = 'complete') AS collision_calls,
           sumIf(unmatched_count, projection_state = 'complete') AS unmatched_calls,
           sumIf(unknown_result_count, projection_state = 'complete') AS unknown_result_calls,
           countIf(projection_state = 'complete' AND calls > 0) AS observed_sessions,
           sumIf(calls, projection_state = 'complete') AS observed_calls,
           sumIf(successes, projection_state = 'complete') AS successes,
           sumIf(errors, projection_state = 'complete') AS errors,
           sumIf(unknowns, projection_state = 'complete') AS unknowns
    FROM ("""
    + _SESSIONS
    + """) FORMAT JSON"""
)

_HARNESSES = (
    """SELECT harness, count() AS sessions FROM ("""
    + PRESENCE_COHORT_SQL
    + """) GROUP BY harness ORDER BY harness FORMAT JSON"""
)

# Activation/configuration actions for present sessions only; never usage.
_ACTIVATIONS = (
    """SELECT countDistinctIf((user_id, harness, session_id), mode = 'context') AS context_sessions,
           countDistinctIf((user_id, harness, session_id), mode = 'next-session') AS next_session_sessions
    FROM session_capabilities FINAL
    WHERE project_id = {project_id:String} AND kind = {component_type:String}
      AND component_id = {component_id:String}
      AND ({component_version_id:String} = '' OR version = {component_version:String})
      AND (user_id, harness, session_id) IN (SELECT user_id, harness, session_id FROM ("""
    + PRESENCE_COHORT_SQL
    + """)) FORMAT JSON"""
)


def supported_harnesses() -> list[str]:
    """Harnesses whose registry entry declares a verified invocation extractor."""
    return sorted(name for name, entry in HARNESS_REGISTRY.items() if entry.get("invocation_extractor"))


def _clickhouse_array(values: list[str]) -> str:
    # Registry keys are server-controlled, but quote defensively anyway.
    return "[" + ",".join("'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'" for value in values) + "]"


def _activity_params(
    project_id: str,
    component_type: str,
    component_id: str,
    component_version_id: str | None,
    period: tuple[datetime, datetime],
    component_version: str | None = None,
) -> dict:
    return presence_params(project_id, component_type, component_id, component_version_id, period) | {
        "param_component_version": component_version or "",
        "param_projection_version": publication_version(),
        "param_supported": _clickhouse_array(supported_harnesses()),
        "param_max_refs": MAX_REFERENCES_PER_SESSION,
        "param_max_source_records": MAX_SOURCE_RECORDS,
        "param_max_source_bytes": MAX_SOURCE_BYTES,
    }


async def _rows(sql: str, params: dict) -> list[dict]:
    response = await clickhouse._query(sql, params)
    response.raise_for_status()
    return response.json().get("data", [])


async def activity_summary(
    project_id: str,
    component_type: str,
    component_id: str,
    component_version_id: str | None,
    period: tuple[datetime, datetime],
    *,
    component_version: str | None = None,
) -> dict:
    """Exact cohort summary plus the shared coverage block."""
    if component_version_id and not component_version:
        raise ValueError("A version-scoped summary requires a verified version label")
    params = _activity_params(project_id, component_type, component_id, component_version_id, period, component_version)
    presence, aggregate_rows, harnesses, activation_rows = await asyncio.gather(
        presence_coverage(project_id, component_type, component_id, component_version_id, period),
        _rows(_SUMMARY, params),
        _rows(_HARNESSES, params),
        _rows(_ACTIVATIONS, params),
    )
    aggregate = (aggregate_rows or [{}])[0]
    activations = (activation_rows or [{}])[0]
    coverage = build_coverage(presence, aggregate, params["param_projection_version"])
    return {
        "present_sessions": coverage.presence.present_sessions,
        "present_users": coverage.presence.present_users,
        "observed_sessions": coverage.observed_sessions,
        "observed_calls": int(aggregate.get("observed_calls") or 0),
        "result_states": {
            "success": int(aggregate.get("successes") or 0),
            "error": int(aggregate.get("errors") or 0),
            "unknown": int(aggregate.get("unknowns") or 0),
        },
        "harness_distribution": {row["harness"]: int(row["sessions"]) for row in harnesses},
        "activation_actions": {
            "context_sessions": int(activations.get("context_sessions") or 0),
            "next_session_sessions": int(activations.get("next_session_sessions") or 0),
            "scope": "component_version" if component_version_id else "component",
        },
        "coverage": coverage,
    }


CursorScope = tuple[str, str, str, str, str, str]  # project, requester, type, listing, version, period_days


def encode_cursor(row: dict, period_end: datetime, *, scope: CursorScope) -> str:
    """Sign a pinned window and immutable scoped session key (not a transcript ID)."""
    payload = json.dumps(
        [period_end.astimezone(UTC).isoformat(), *scope, row["user_id"], row["harness"], row["session_id"]],
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(settings.SECRET_KEY.encode(), payload, hashlib.sha256).hexdigest().encode()
    return base64.urlsafe_b64encode(payload + b"." + signature).decode().rstrip("=")


def decode_cursor(cursor: str, *, scope: CursorScope) -> tuple[datetime, list[str]]:
    """Verify scope and bounded window; return (pinned end, immutable key)."""
    if not cursor or len(cursor) > 2048:
        raise ValueError("malformed cursor")
    try:
        raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        payload, signature = raw.rsplit(b".", 1)
        expected = hmac.new(settings.SECRET_KEY.encode(), payload, hashlib.sha256).hexdigest().encode()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid cursor signature")
        key = json.loads(payload)
        if not isinstance(key, list) or len(key) != 10 or not all(isinstance(part, str) for part in key):
            raise ValueError("invalid cursor key")
        if tuple(key[1:7]) != scope or not all(key[7:]):
            raise ValueError("cursor scope mismatch")
        period_end = datetime.fromisoformat(key[0])
        if period_end.tzinfo is None or period_end.utcoffset() != timedelta(0):
            raise ValueError("invalid cursor time")
        now = datetime.now(UTC)
        if not now - timedelta(days=90, minutes=5) <= period_end <= now + timedelta(minutes=5):
            raise ValueError("cursor window out of range")
        return period_end, key[7:]
    except (binascii.Error, ValueError, UnicodeDecodeError) as error:
        raise ValueError("malformed cursor") from error


async def activity_sessions(
    project_id: str,
    component_type: str,
    component_id: str,
    component_version_id: str | None,
    period: tuple[datetime, datetime],
    *,
    limit: int,
    cursor: list[str] | None,
    cursor_scope: CursorScope,
) -> tuple[list[dict], str | None]:
    """Page by immutable scoped session identity; changing last_event_time cannot repeat a row.

    The cohort remains a live view: newly arriving sessions can join the pinned
    window. This is keyset pagination, not a point-in-time database snapshot.
    """
    if not 1 <= limit <= 100:
        raise ValueError("limit out of bounds")
    if cursor_scope[0] != project_id:
        raise ValueError("cursor project mismatch")
    params = _activity_params(project_id, component_type, component_id, component_version_id, period)
    params["param_limit"] = limit + 1
    where = ""
    if cursor is not None:
        where = "WHERE (user_id, harness, session_id) > ({after_user:String}, {after_harness:String}, {after_session:String})"
        params.update(param_after_user=cursor[0], param_after_harness=cursor[1], param_after_session=cursor[2])
    sql = (
        "SELECT * FROM ("
        + _SESSIONS
        + f") {where} ORDER BY user_id, harness, session_id LIMIT {{limit:UInt16}} FORMAT JSON"
    )
    rows = await _rows(sql, params)
    page, more = rows[:limit], len(rows) > limit
    sessions = []
    for row in page:
        complete = row["projection_state"] == "complete"
        refs = row.get("refs") or [] if complete else []
        calls = int(row.get("calls") or 0) if complete else 0
        sessions.append(
            {
                "user_id": row["user_id"],
                "harness": row["harness"],
                "session_id": row["session_id"],
                "last_event_time": str(row["last_event_time"]),
                "projection_state": row["projection_state"],
                "source_state": row["source_state"],
                "observed_calls": calls,
                "result_states": {
                    "success": int(row.get("successes") or 0) if complete else 0,
                    "error": int(row.get("errors") or 0) if complete else 0,
                    "unknown": int(row.get("unknowns") or 0) if complete else 0,
                },
                "source_references": [
                    {"source_line_offset": int(ref[0]), "source_block_key": ref[1], "result_state": ref[2]}
                    for ref in refs
                ],
                "source_references_truncated": calls > len(refs),
            }
        )
    return sessions, (encode_cursor(page[-1], period[1], scope=cursor_scope) if more and page else None)
