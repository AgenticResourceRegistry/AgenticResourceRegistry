# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""ClickHouse query functions for live session telemetry tables."""

import time

from loguru import logger as optic

import services.clickhouse.client as _client


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


async def query_session_error_offsets(
    session_id: str, project_id: str, user_id: str, harness: str, min_offset: int, max_offset: int
) -> list[int]:
    """Read durable source errors for a requested range, including after response-lost retries."""
    if min_offset > max_offset:
        return []
    sql = (
        "SELECT line_offset FROM session_events FINAL "
        "WHERE project_id = {pid:String} AND user_id = {uid:String} "
        "AND harness = {harness:String} AND session_id = {sid:String} AND is_source_record = 1 "
        "AND event_type = '_parse_error' "
        "AND line_offset >= {min_off:UInt32} AND line_offset <= {max_off:UInt32} "
        "ORDER BY line_offset FORMAT JSON"
    )
    params = {
        "param_pid": project_id,
        "param_uid": user_id,
        "param_harness": harness,
        "param_sid": session_id,
        "param_min_off": str(min_offset),
        "param_max_off": str(max_offset),
    }
    response = await _client._query(sql, params)
    response.raise_for_status()
    return [int(row["line_offset"]) for row in response.json().get("data", [])]


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
