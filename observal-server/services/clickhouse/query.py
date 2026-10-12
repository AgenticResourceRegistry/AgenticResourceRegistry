# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""ClickHouse query functions for live session telemetry tables."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from loguru import logger as optic

import services.clickhouse.client as _client

if TYPE_CHECKING:
    from services.otel.types import SessionKey


async def query_recent_events(minutes: int = 60) -> dict:
    """Get recent session activity counts from JSONL session tables."""
    minutes = int(minutes)
    try:
        r = await _client._query(
            "SELECT sum(tool_call_count) AS tools, count() AS sessions "
            "FROM session_stats_agg FINAL "
            "WHERE last_event_time > now() - INTERVAL {minutes:UInt32} MINUTE "
            "FORMAT JSON",
            {"param_minutes": str(minutes)},
        )
        r.raise_for_status()
        row = r.json().get("data", [{}])[0]
        return {
            "tool_call_events": int(row.get("tools") or 0),
            "agent_interaction_events": int(row.get("sessions") or 0),
        }
    except Exception as e:
        optic.warning("could not count recent session events: {}", e)
        return {"tool_call_events": 0, "agent_interaction_events": 0}


async def query_session_checkpoint(
    session_id: str,
    project_id: str,
    user_id: str,
    harness: str,
) -> tuple[int, int]:
    """Return the durable contiguous (source line, end byte) checkpoint."""
    sql = (
        "SELECT acknowledged_line, acknowledged_offset FROM session_checkpoints FINAL "
        "WHERE project_id = {pid:String} AND user_id = {uid:String} "
        "AND harness = {harness:String} AND session_id = {sid:String} LIMIT 1 FORMAT JSON"
    )
    params = {
        "param_pid": project_id,
        "param_uid": user_id,
        "param_harness": harness,
        "param_sid": session_id,
    }
    try:
        r = await _client._query(sql, params)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            return -1, 0
        return int(data[0]["acknowledged_line"]), int(data[0].get("acknowledged_offset") or 0)
    except Exception as e:
        optic.error("failed to read checkpoint for session {}: {}", session_id, e)
        raise


async def query_source_records_after(
    session_id: str,
    project_id: str,
    user_id: str,
    harness: str,
    after_line: int,
    limit: int = 5000,
) -> list[tuple[int, int]]:
    """Return ordered source positions after a checkpoint for gap detection."""
    sql = (
        "SELECT line_offset, source_end_offset FROM session_events FINAL "
        "WHERE project_id = {pid:String} AND user_id = {uid:String} "
        "AND harness = {harness:String} AND session_id = {sid:String} "
        "AND is_source_record = 1 AND line_offset > {after:Int64} "
        "ORDER BY line_offset LIMIT {limit:UInt32} FORMAT JSON"
    )
    params = {
        "param_pid": project_id,
        "param_uid": user_id,
        "param_harness": harness,
        "param_sid": session_id,
        "param_after": str(after_line),
        "param_limit": str(limit),
    }
    r = await _client._query(sql, params)
    r.raise_for_status()
    return [(int(row["line_offset"]), int(row.get("source_end_offset") or 0)) for row in r.json().get("data", [])]


async def query_session_source_manifest(
    session_id: str,
    project_id: str,
    user_id: str,
    harness: str,
) -> list[tuple[int, int, str]]:
    """Return canonical source positions for final integrity auditing."""
    sql = (
        "SELECT line_offset, source_end_offset, source_sha256 FROM session_events FINAL "
        "WHERE project_id = {pid:String} AND user_id = {uid:String} "
        "AND harness = {harness:String} AND session_id = {sid:String} "
        "AND is_source_record = 1 ORDER BY line_offset FORMAT JSON"
    )
    params = {
        "param_pid": project_id,
        "param_uid": user_id,
        "param_harness": harness,
        "param_sid": session_id,
    }
    r = await _client._query(sql, params)
    r.raise_for_status()
    return [
        (int(row["line_offset"]), int(row.get("source_end_offset") or 0), str(row.get("source_sha256") or ""))
        for row in r.json().get("data", [])
    ]


async def query_existing_for_dedup(
    session_id: str,
    project_id: str,
    user_id: str,
    harness: str,
    min_offset: int,
    max_offset: int,
) -> dict[int, str]:
    """Return existing source line hashes by stable source index."""
    _t0 = time.perf_counter()
    if min_offset > max_offset:
        return {}
    sql = (
        "SELECT line_offset, line_hash FROM session_events FINAL "
        "WHERE project_id = {pid:String} AND user_id = {uid:String} "
        "AND harness = {harness:String} AND session_id = {sid:String} AND is_source_record = 1 "
        "AND line_offset >= {min_off:UInt32} AND line_offset <= {max_off:UInt32} FORMAT JSON"
    )
    params = {
        "param_pid": project_id,
        "param_uid": user_id,
        "param_harness": harness,
        "param_sid": session_id,
        "param_min_off": str(min_offset),
        "param_max_off": str(max_offset),
    }
    try:
        r = await _client._query(sql, params)
        r.raise_for_status()
        existing = {int(row["line_offset"]): str(row.get("line_hash") or "") for row in r.json().get("data", [])}
        _elapsed = (time.perf_counter() - _t0) * 1000
        optic.trace(
            "dedup check for session {}: {} offsets in range [{}, {}] ({:.0f}ms)",
            session_id,
            len(existing),
            min_offset,
            max_offset,
            _elapsed,
        )
        return existing
    except Exception as e:
        optic.error("dedup query failed for session {}: {}", session_id, e)
        raise


# Every column the OTLP log and span output reads.
_SESSION_ROW_COLUMNS = (
    "session_id, project_id, user_id, harness, agent_id, agent_version, parent_session_id, "
    "line_offset, line_hash, is_source_record, rendered, event_type, timestamp, ingested_at, "
    "uuid, parent_uuid, tool_name, tool_id, model, "
    "input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, credits, "
    "raw_line, raw_line_truncated"
)


async def query_session_rows(
    key: SessionKey,
    *,
    after_line: int = -1,
    up_to_line: int | None = None,
) -> list[dict]:
    """Return one session's stored rows in ``(after_line, up_to_line]``, ordered by ``line_offset``.

    Reads by primary key and includes every stored row: synthetic rows
    (``is_source_record = 0``) and rows the UI does not render.
    """
    upper = "AND line_offset <= {upto:Int64} " if up_to_line is not None else ""
    sql = (
        "SELECT " + _SESSION_ROW_COLUMNS + " FROM session_events FINAL "
        "WHERE project_id = {pid:String} AND user_id = {uid:String} "
        "AND harness = {harness:String} AND session_id = {sid:String} "
        "AND line_offset > {after:Int64} " + upper + "ORDER BY line_offset "
        "SETTINGS max_final_threads = 4, do_not_merge_across_partitions_select_final = 1 "
        "FORMAT JSON"
    )
    params = {
        "param_pid": key.project_id,
        "param_uid": key.user_id,
        "param_harness": key.harness,
        "param_sid": key.session_id,
        "param_after": str(after_line),
    }
    if up_to_line is not None:
        params["param_upto"] = str(up_to_line)
    r = await _client._query(sql, params)
    r.raise_for_status()
    rows = r.json().get("data", [])
    for row in rows:
        row["line_offset"] = int(row["line_offset"])
    return rows


def _key_params(key: SessionKey) -> dict[str, str]:
    return {
        "param_pid": key.project_id,
        "param_uid": key.user_id,
        "param_harness": key.harness,
        "param_sid": key.session_id,
    }


def _ch_time(value: datetime | None) -> str:
    """A UTC ``DateTime64(3)`` literal; ``None`` is the epoch, which every session is after."""
    if value is None:
        return "1970-01-01 00:00:00.000"
    if value.tzinfo is not None:
        value = value.astimezone(UTC)
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


async def query_session_first_event(key: SessionKey) -> datetime | None:
    """When a session's first record happened, from its summary row."""
    sql = (
        "SELECT first_event_time FROM session_stats_agg FINAL "
        "WHERE project_id = {pid:String} AND user_id = {uid:String} "
        "AND harness = {harness:String} AND session_id = {sid:String} LIMIT 1 FORMAT JSON"
    )
    r = await _client._query(sql, _key_params(key))
    r.raise_for_status()
    data = r.json().get("data", [])
    if not data:
        return None
    return datetime.fromisoformat(str(data[0]["first_event_time"])).replace(tzinfo=UTC)


async def query_forward_state(destination_id: str, signal: str, key: SessionKey) -> tuple[int, int, bool] | None:
    """A destination's watermark for one session, ``(forwarded_line, replay_through, session_closed)``.

    ``None`` means nothing has been sent for this session yet.
    """
    sql = (
        "SELECT forwarded_line, replay_through, session_closed FROM otlp_forward_state FINAL "
        "WHERE destination_id = {dest:String} AND signal = {signal:String} "
        "AND project_id = {pid:String} AND user_id = {uid:String} "
        "AND harness = {harness:String} AND session_id = {sid:String} LIMIT 1 FORMAT JSON"
    )
    params = {"param_dest": destination_id, "param_signal": signal, **_key_params(key)}
    r = await _client._query(sql, params)
    r.raise_for_status()
    data = r.json().get("data", [])
    if not data:
        return None
    row = data[0]
    return int(row["forwarded_line"]), int(row["replay_through"]), bool(int(row["session_closed"]))


# Sessions whose checkpoint and a destination's watermark differ, restricted to
# sessions that started after the destination was added.  ClickHouse fills a
# missing LEFT JOIN row with defaults rather than NULL unless join_use_nulls is
# set, and a default forwarded_line of 0 would read as "line 0 already sent".
_FORWARD_PENDING_FROM = (
    "FROM session_checkpoints AS c FINAL "
    "INNER JOIN (SELECT project_id, user_id, harness, session_id FROM session_stats_agg FINAL "
    "WHERE first_event_time >= {added:DateTime64(3)}) AS s "
    "ON c.project_id = s.project_id AND c.user_id = s.user_id "
    "AND c.harness = s.harness AND c.session_id = s.session_id "
    "LEFT JOIN (SELECT project_id, user_id, harness, session_id, forwarded_line FROM otlp_forward_state FINAL "
    "WHERE destination_id = {dest:String} AND signal = {signal:String}) AS f "
    "ON c.project_id = f.project_id AND c.user_id = f.user_id "
    "AND c.harness = f.harness AND c.session_id = f.session_id "
    "WHERE c.acknowledged_line >= 0 "
    "AND (f.forwarded_line IS NULL OR f.forwarded_line != c.acknowledged_line) "
)


async def query_forward_candidates(
    destination_id: str,
    signal: str,
    added_at: datetime | None,
    limit: int = 500,
) -> list[SessionKey]:
    """Sessions a destination is behind on (or ahead of, after an integrity rewind)."""
    from services.otel.types import SessionKey

    sql = (
        "SELECT c.project_id AS project_id, c.user_id AS user_id, c.harness AS harness, "
        "c.session_id AS session_id " + _FORWARD_PENDING_FROM + "LIMIT {limit:UInt32} "
        "SETTINGS join_use_nulls = 1 FORMAT JSON"
    )
    params = {
        "param_dest": destination_id,
        "param_signal": signal,
        "param_added": _ch_time(added_at),
        "param_limit": str(limit),
    }
    r = await _client._query(sql, params)
    r.raise_for_status()
    return [
        SessionKey(
            project_id=row["project_id"],
            user_id=row["user_id"],
            harness=row["harness"],
            session_id=row["session_id"],
        )
        for row in r.json().get("data", [])
    ]


async def query_forward_lag(destination_id: str, signal: str, added_at: datetime | None) -> dict[str, int]:
    """How far a destination is behind: sessions with unsent lines, and how many lines."""
    sql = (
        "SELECT count() AS sessions_behind, "
        "sum(greatest(c.acknowledged_line - coalesce(f.forwarded_line, -1), 0)) AS lines_behind "
        + _FORWARD_PENDING_FROM
        + "SETTINGS join_use_nulls = 1 FORMAT JSON"
    )
    params = {"param_dest": destination_id, "param_signal": signal, "param_added": _ch_time(added_at)}
    r = await _client._query(sql, params)
    r.raise_for_status()
    data = r.json().get("data", [])
    row = data[0] if data else {}
    return {
        "sessions_behind": int(row.get("sessions_behind") or 0),
        "lines_behind": int(row.get("lines_behind") or 0),
    }


async def query_last_forward_delivery(destination_id: str, signal: str) -> dict | None:
    """The most recent delivery attempt for a destination and signal."""
    sql = (
        "SELECT status, status_code, rejected, error, timestamp FROM otlp_forward_deliveries "
        "WHERE destination_id = {dest:String} AND signal = {signal:String} "
        "ORDER BY timestamp DESC LIMIT 1 FORMAT JSON"
    )
    r = await _client._query(sql, {"param_dest": destination_id, "param_signal": signal})
    r.raise_for_status()
    data = r.json().get("data", [])
    return data[0] if data else None
