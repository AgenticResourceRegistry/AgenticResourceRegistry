# SPDX-FileCopyrightText: 2026 Subramania Raja <dhanpraja231@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Shreem Seth <shreemseth26@gmail.com>
# SPDX-FileCopyrightText: 2026 SrihariLegend <sriharilegend23@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Session listing and detail endpoints - backed by session_events table.

Reads from the session_events ClickHouse table populated by the
/api/v1/ingest/session endpoint.  Uses session parsers to transform
raw JSONL rows into frontend-friendly event dicts.
"""

import asyncio
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi_cache.decorator import cache
from loguru import logger as optic
from sqlalchemy import select

import services.dynamic_settings as ds
from api.deps import require_role
from database import async_session
from models.user import User, UserRole
from observal_shared.harness_registry import get_harness_display_names
from services.clickhouse import _query
from services.user_search import clickhouse_in_condition, resolve_user_filter_values

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])

_PLATFORM_NAMES = get_harness_display_names()


def _harness_id(value: str) -> str:
    """Accept both canonical ids and display labels from bookmarked trace searches."""
    normalized = value.strip().casefold()
    for harness in _PLATFORM_NAMES:
        if normalized == harness.casefold():
            return harness
    for harness, label in _PLATFORM_NAMES.items():
        if normalized == label.casefold():
            return harness
    return value


async def _ch_json(sql: str, params: dict | None = None) -> list[dict]:
    optic.trace("sql={}, params={}", sql, params)
    try:
        r = await _query(f"{sql} FORMAT JSON", params)
        if r.status_code == 200:
            return r.json().get("data", [])
    except Exception as e:
        optic.warning("clickhouse_query_failed: {}", e)
    return []


def _is_admin_user(user: User) -> bool:
    optic.trace("user_id={}", user.id)
    return user.role in (UserRole.admin, UserRole.super_admin)


def _resolved_agent_name(agent_id: str, names: dict[str, str], *, queried: bool) -> str | None:
    """Return a display name for an agent id, or None when it cannot be judged.

    A session can outlive the agent it was attributed to: the agent may be
    deleted, or the local lockfile may hold an id from a different registry.
    Returning None there renders as a row with an agent id and a blank name,
    which reads as a bug. Name the state instead.

    ``queried`` must be True only when Postgres was actually asked about this
    id and answered. A malformed id was never queried and a failed query proves
    nothing, so both keep returning None rather than claiming the agent is gone.
    """
    name = names.get(agent_id)
    if name is not None:
        return name
    if queried:
        return f"unknown agent ({agent_id[:8]})"
    return None


def _has_admin_trace_access(user: User) -> bool:
    """Check if user has admin-level trace access."""
    optic.trace("user_id={}", user.id)
    if not _is_admin_user(user):
        return False
    if user.role == UserRole.super_admin:
        return True
    return not getattr(user, "_trace_privacy", False)


@router.get("")
async def list_sessions(
    status: str | None = Query(None),
    platform: str | None = Query(None),
    user: str | None = Query(None, description="Filter by user name, username, or email"),
    days: int | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    mine: bool = Query(False),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("status={}, platform={}, user={}", status, platform, user)
    is_admin = _has_admin_trace_access(current_user)
    uid_str = str(current_user.id)
    capped_days = min(days, 365) if days is not None and days > 0 else days

    optic.debug(
        "list_sessions: user={}, is_admin={}, platform={}, days={}, mine={}",
        uid_str,
        is_admin,
        platform,
        capped_days,
        mine,
    )

    user_ids: list[str] | None = None
    if user:
        async with async_session() as db:
            values = await resolve_user_filter_values(db, user)
        user_ids = values.ids
        if not user_ids:
            return []

    rows = await _list_sessions_query(
        platform=platform,
        user_ids=user_ids,
        days=capped_days,
        is_admin=is_admin,
        uid=uid_str,
        limit=limit,
        offset=offset,
        mine=mine,
    )

    # Resolve user display names from PostgreSQL
    uid_to_name: dict[str, str] = {}
    unresolved_ids: set[str] = set()
    for row in rows:
        uid = row.get("user_id", "")
        if uid:
            unresolved_ids.add(uid)

    if unresolved_ids:
        try:
            uuid_ids = []
            for uid in unresolved_ids:
                try:
                    uuid_ids.append(_uuid.UUID(uid))
                except ValueError:
                    pass
            if uuid_ids:
                async with async_session() as db:
                    result = await db.execute(select(User.id, User.name).where(User.id.in_(uuid_ids)))
                    for u_id, u_name in result.all():
                        uid_to_name[str(u_id)] = u_name
        except Exception:
            optic.opt(exception=True).warning("User name resolution failed")

    # Resolve agent names from PostgreSQL
    from models.agent import Agent

    agent_id_to_name: dict[str, str] = {}
    agent_ids_to_resolve: set[str] = set()
    for row in rows:
        aid = row.get("agent_id") or ""
        if aid:
            agent_ids_to_resolve.add(aid)

    # Ids this request actually asked Postgres about. Only those can be called
    # missing: a malformed id was never queried, and a failed query proves
    # nothing about whether the agent exists.
    queried_agent_ids: set[str] = set()
    agent_lookup_succeeded = False

    if agent_ids_to_resolve:
        try:
            agent_uuids = []
            # A session can store a valid but non-canonical id - uppercase, or
            # without hyphens. Those query correctly, but the row comes back
            # canonical, so keying results by str(row_id) alone would miss the
            # stored form and label a live agent unknown.
            #
            # Every stored form is kept, not just one: two sessions on the same
            # page can spell the same agent differently, and mapping each
            # canonical id to a single form would resolve one session and leave
            # the other unresolved.
            canonical_to_stored: dict[str, list[str]] = {}
            for aid in agent_ids_to_resolve:
                try:
                    parsed = _uuid.UUID(aid)
                except ValueError:
                    continue
                agent_uuids.append(parsed)
                canonical_to_stored.setdefault(str(parsed), []).append(aid)
                queried_agent_ids.add(aid)
            if agent_uuids:
                async with async_session() as db:
                    result = await db.execute(select(Agent.id, Agent.name).where(Agent.id.in_(agent_uuids)))
                    for a_id, a_name in result.all():
                        canonical = str(a_id)
                        for stored in canonical_to_stored.get(canonical, [canonical]):
                            agent_id_to_name[stored] = a_name
                agent_lookup_succeeded = True
        except Exception:
            optic.opt(exception=True).warning("Agent name resolution failed")

    for row in rows:
        uid = row.get("user_id", "")
        row["user_name"] = uid_to_name.get(uid, current_user.name)
        harness = row.pop("harness", "") or ""
        row["platform"] = _PLATFORM_NAMES.get(harness, harness.replace("-", " ").title() if harness else "Claude Code")
        row["service_name"] = harness
        row["is_active"] = bool(int(row.get("is_active", 0)))
        agent_id = row.get("agent_id") or None
        row["agent_id"] = agent_id if agent_id else None
        row["agent_name"] = (
            _resolved_agent_name(
                agent_id,
                agent_id_to_name,
                queried=agent_lookup_succeeded and agent_id in queried_agent_ids,
            )
            if agent_id
            else None
        )
        row["agent_version"] = row.get("agent_version") or None

    if status == "active":
        rows = [r for r in rows if r["is_active"]]
    return rows


async def _list_sessions_query(
    *,
    platform: str | None,
    user_ids: list[str] | None,
    days: int | None,
    is_admin: bool,
    uid: str,
    limit: int = 50,
    offset: int = 0,
    mine: bool = False,
) -> list[dict]:
    """Session list from session_stats_agg FINAL.

    session_stats_agg is an AggregatingMergeTree table fed by session_stats_mv.
    FINAL merges parts at read time; at ~1 row per session this is fast and
    avoids illegal nested-aggregation errors with SimpleAggregateFunction columns.
    """
    optic.trace("platform={}, days={}, is_admin={}", platform, days, is_admin)
    where_parts = ["session_id != ''", "parent_session_id = ''", "prompt_count > 0"]
    params: dict[str, str] = {}

    if mine or not is_admin:
        where_parts.append("user_id = {uid:String}")
        params["param_uid"] = uid
    # Kiro CLI records have prompt timestamps only. Its source-record arrival
    # time captures tool activity in a long turn without rewriting the trace
    # timeline. Other harnesses continue to use event time as before.
    activity_time = "if(harness = 'kiro', greatest(last_event_time, last_source_ingest_at), last_event_time)"
    if days is not None and days > 0:
        where_parts.append(f"{activity_time} > now() - INTERVAL {int(days)} DAY")
    if platform:
        where_parts.append("harness = {platform:String}")
        params["param_platform"] = _harness_id(platform)
    if user_ids:
        condition = clickhouse_in_condition("user_id", user_ids, "user", params)
        if condition:
            where_parts.append(condition)

    where_clause = "WHERE " + " AND ".join(where_parts) + " "

    return await _ch_json(
        "SELECT "
        "session_id, "
        "if(first_event_time > '2020-01-01 00:00:00' AND first_event_time < '2099-01-01 00:00:00', "
        "   first_event_time, last_event_time) AS first_event_time, "
        "if(last_event_time < '2099-01-01 00:00:00', last_event_time, first_event_time) AS last_event_time, "
        f"({activity_time} > now() - INTERVAL 5 MINUTE) AS is_active, "
        "prompt_count, "
        "0                   AS api_request_count, "
        "tool_result_count, "
        "input_tokens        AS total_input_tokens, "
        "output_tokens       AS total_output_tokens, "
        "cache_read_tokens   AS total_cache_read_tokens, "
        "cache_write_tokens  AS total_cache_write_tokens, "
        "total_credits, "
        "model, "
        "harness, "
        "agent_id, "
        "agent_version, "
        "user_id "
        "FROM session_stats_agg FINAL " + where_clause + f"ORDER BY {activity_time} DESC "
        f"LIMIT {int(limit)} OFFSET {int(offset)}",
        params or None,
    )


@router.get("/summary")
async def sessions_summary(
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("user_id={}", current_user.id)
    is_admin = _has_admin_trace_access(current_user)
    user_filter = ""
    params: dict[str, str] = {}
    if not is_admin:
        user_filter = "AND user_id = {uid:String} "
        params["param_uid"] = str(current_user.id)

    # Use pre-aggregated session_stats_agg - avoids a full session_events FINAL scan.
    # AggregatingMergeTree + GROUP BY merges partial aggregates at read time; no FINAL needed.
    rows = await _ch_json(
        "SELECT "
        "count() AS total, "
        "countIf(toDate(last_event_time) = today()) AS today_sessions "
        "FROM ( "
        "  SELECT session_id, max(last_event_time) AS last_event_time "
        "  FROM session_stats_agg FINAL "
        "  WHERE session_id != '' " + user_filter + "  GROUP BY session_id "
        ")",
        params or None,
    )
    row = rows[0] if rows else {}
    return {
        "total_sessions": int(row.get("total", 0)),
        "today_sessions": int(row.get("today_sessions", 0)),
    }


@router.get("/stats")
@cache(expire=ds.get_sync_int("data.cache_ttl_default", 30), namespace="otel")
async def sessions_stats(current_user: User = Depends(require_role(UserRole.admin))):
    # Use pre-aggregated session_stats_agg - avoids a full session_events FINAL scan.
    # prompt_count / tool_call_count in the MV correspond to 'user_prompt' / 'tool_call'
    # event types (legacy otel names 'user' / 'tool_use' are not present in V3 events).
    optic.trace("user_id={}", current_user.id)
    rows = await _ch_json(
        "SELECT "
        "count() AS total_sessions, "
        "sum(prompt_count) AS total_prompts, "
        "0 AS total_api_requests, "
        "sum(tool_call_count) AS total_tool_calls, "
        "sum(event_count) AS total_events "
        "FROM ( "
        "  SELECT session_id, "
        "    sum(prompt_count) AS prompt_count, "
        "    sum(tool_call_count) AS tool_call_count, "
        "    sum(event_count) AS event_count "
        "  FROM session_stats_agg FINAL "
        "  WHERE session_id != '' "
        "  GROUP BY session_id "
        ")"
    )
    row = rows[0] if rows else {}
    return {
        "total_sessions": int(row.get("total_sessions", 0)),
        "total_prompts": int(row.get("total_prompts", 0)),
        "total_api_requests": int(row.get("total_api_requests", 0)),
        "total_tool_calls": int(row.get("total_tool_calls", 0)),
        "total_events": int(row.get("total_events", 0)),
    }


@router.get("/{session_id}")
async def get_session(
    session_id: str,
    after_offset: int | None = Query(
        None, ge=0, description="Return only events after this line_offset (incremental fetch)"
    ),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("session_id={}, after_offset={}", session_id, after_offset)
    is_admin = _has_admin_trace_access(current_user)
    identity_params: dict[str, str] = {"param_sid": session_id}
    identity_user_filter = ""
    if not is_admin:
        identity_params["param_uid"] = str(current_user.id)
        identity_user_filter = "AND user_id = {uid:String} "
    identity_rows = await _ch_json(
        "SELECT project_id, user_id, harness FROM session_events FINAL "
        "WHERE session_id = {sid:String} " + identity_user_filter + "ORDER BY ingested_at DESC LIMIT 1",
        identity_params,
    )
    if not identity_rows:
        return {"session_id": session_id, "harness": "", "events": []}

    identity = identity_rows[0]
    params: dict[str, str] = {
        "param_sid": session_id,
        "param_pid": str(identity["project_id"]),
        "param_uid": str(identity["user_id"]),
        "param_harness": str(identity["harness"]),
    }
    identity_filter = "project_id = {pid:String} AND user_id = {uid:String} AND harness = {harness:String} "

    # Build offset filter for incremental fetches
    _offset_filter = ""
    if after_offset is not None:
        _offset_filter = "AND line_offset > {offset:UInt32} "
        params["param_offset"] = str(after_offset)

    # Fan out both FINAL scans in parallel - wall time ≈ max(t1, t2) not t1+t2.
    # Stable identity partitioning lets ClickHouse run FINAL per partition.
    _main_sql = (
        "SELECT "
        "line_offset, timestamp, event_type, content_preview, tool_name, tool_id, "
        "uuid, parent_uuid, content_length, harness, agent_id, agent_version, raw_line, raw_line_truncated, "
        "credits, ingested_at "
        "FROM session_events FINAL "
        "WHERE session_id = {sid:String} AND "
        + identity_filter
        + "AND rendered = 1 "
        + _offset_filter
        + "ORDER BY line_offset ASC "
        "SETTINGS max_final_threads = 4, do_not_merge_across_partitions_select_final = 1"
    )
    _sub_params = dict(params)
    _sub_offset_filter = ""
    if after_offset is not None:
        _sub_offset_filter = "AND line_offset > {offset:UInt32} "
    _sub_sql = (
        "SELECT session_id, timestamp, event_type, content_preview, "
        "tool_name, tool_id, uuid, parent_uuid, content_length, harness, "
        "raw_line, raw_line_truncated, credits, ingested_at, line_offset "
        "FROM session_events FINAL "
        "WHERE parent_session_id = {sid:String} AND "
        + identity_filter
        + "AND rendered = 1 "
        + _sub_offset_filter
        + "ORDER BY session_id, line_offset ASC "
        "SETTINGS max_final_threads = 4, do_not_merge_across_partitions_select_final = 1"
    )
    rows, sub_rows_all = await asyncio.gather(
        _ch_json(_main_sql, params),
        _ch_json(_sub_sql, _sub_params),
    )

    if not rows:
        if after_offset is not None:
            # Incremental fetch with no new data
            return {"session_id": session_id, "events": [], "max_offset": after_offset}
        return {"session_id": session_id, "service_name": "", "events": [], "traces": []}

    harness = rows[0].get("harness", "claude-code")
    agent_id = next((r.get("agent_id") for r in rows if r.get("agent_id")), None)
    agent_version = next((r.get("agent_version") for r in rows if r.get("agent_version")), None)
    agent_name = None
    if agent_id:
        try:
            agent_uuid = _uuid.UUID(agent_id)
        except ValueError:
            agent_uuid = None
        if agent_uuid is not None:
            try:
                from models.agent import Agent

                async with async_session() as db:
                    result = await db.execute(select(Agent.name).where(Agent.id == agent_uuid))
                    resolved = result.scalar_one_or_none()
                agent_name = _resolved_agent_name(
                    agent_id,
                    {agent_id: resolved} if resolved is not None else {},
                    queried=True,
                )
            except Exception:
                optic.opt(exception=True).warning("Agent name resolution failed")

    # Track max line_offset for incremental fetch cursor
    max_offset = max(int(r.get("line_offset", 0)) for r in rows) if rows else (after_offset or 0)

    # Parse raw events through the session parser for rich rendering
    from services.session_parsers import parse_raw_events

    events = parse_raw_events(rows)

    # sub_rows_all was fetched concurrently above alongside the main query.
    # Each subagent's first row carries parent_uuid pointing at the Agent
    # tool_call in the parent that spawned it - the frontend uses this to
    # nest the subagent inline at the right position.
    subagent_sessions = []
    if sub_rows_all:
        # Group by session_id
        from itertools import groupby

        sub_rows_all.sort(key=lambda r: r["session_id"])
        for sub_sid, sub_rows in groupby(sub_rows_all, key=lambda r: r["session_id"]):
            sub_rows_list = list(sub_rows)
            # first row's parent_uuid links to the Agent tool_call in the parent
            spawned_by = sub_rows_list[0].get("parent_uuid") if sub_rows_list else None
            sub_events = parse_raw_events(sub_rows_list)
            subagent_sessions.append(
                {
                    "session_id": sub_sid,
                    "spawned_by": spawned_by,
                    "events": sub_events,
                }
            )
    return {
        "session_id": session_id,
        "service_name": harness,
        "agent_id": agent_id,
        "agent_name": agent_name,
        "agent_version": agent_version,
        "events": events,
        "traces": [],
        "subagent_sessions": subagent_sessions,
        "max_offset": max_offset,
    }


@router.post("/{session_id}/bind-agent")
async def bind_session_agent(
    session_id: str,
    agent_name: str = Query(..., description="Agent name to bind to this session"),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Explicitly bind a session to an agent name."""
    optic.trace("session_id={}, agent_name={}", session_id, agent_name)
    is_admin = _is_admin_user(current_user)
    if not is_admin:
        params = {"param_sid": session_id, "param_uid": str(current_user.id)}
        ownership = await _ch_json(
            "SELECT 1 FROM session_events WHERE session_id = {sid:String} AND user_id = {uid:String} LIMIT 1",
            params,
        )
        if not ownership:
            raise HTTPException(status_code=404, detail="Session not found or access denied")

    from redis.exceptions import RedisError

    from services.redis import get_redis

    try:
        redis = get_redis()
        await redis.set(f"session_agent:{session_id}", agent_name, ex=86400)
    except RedisError:
        return {"session_id": session_id, "agent_name": agent_name, "bound": False, "error": "Redis unavailable"}
    return {"session_id": session_id, "agent_name": agent_name, "bound": True}
