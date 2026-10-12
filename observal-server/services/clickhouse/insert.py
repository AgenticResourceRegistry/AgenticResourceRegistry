# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""ClickHouse insert functions for live tables."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import orjson
from loguru import logger as optic

import services.clickhouse.client as _client

if TYPE_CHECKING:
    from services.otel.types import SessionKey


def _dumps(obj: dict) -> str:
    return orjson.dumps(obj, default=str).decode()


async def insert_audit_log(events: list[dict]):
    """Batch insert audit log events into ClickHouse."""
    optic.trace("inserting {} audit log events into ClickHouse", len(events))
    if not events:
        return
    lines = []
    for e in events:
        row = {
            "event_id": e["event_id"],
            "timestamp": e.get("timestamp") or _client._normalize_ts(e.get("timestamp")),
            "actor_id": e.get("actor_id", ""),
            "actor_email": e.get("actor_email", ""),
            "actor_role": e.get("actor_role", ""),
            "action": e.get("action", ""),
            "resource_type": e.get("resource_type", ""),
            "resource_id": e.get("resource_id", ""),
            "resource_name": e.get("resource_name", ""),
            "http_method": e.get("http_method", ""),
            "http_path": e.get("http_path", ""),
            "status_code": e.get("status_code", 0),
            "ip_address": e.get("ip_address", ""),
            "user_agent": e.get("user_agent", ""),
            "detail": e.get("detail", ""),
            "sensitivity": e.get("sensitivity", "standard"),
            "request_id": e.get("request_id", ""),
            "outcome": e.get("outcome", ""),
            "duration_ms": e.get("duration_ms", 0.0),
            "chain_hash": e.get("chain_hash", ""),
            "source": e.get("source", "server"),
        }
        lines.append(_dumps(row))
    try:
        r = await _client._query(
            "INSERT INTO audit_log SETTINGS async_insert=0 FORMAT JSONEachRow", data="\n".join(lines)
        )
        r.raise_for_status()
    except Exception as exc:
        optic.error("failed to insert {} audit events into ClickHouse - audit trail has a gap: {}", len(events), exc)


async def _insert_webhook_deliveries(records: list[dict]):
    """Batch insert webhook delivery records into ClickHouse."""
    optic.trace("inserting {} webhook delivery records into ClickHouse", len(records))
    if not records:
        return
    lines = []
    for r in records:
        row = {
            "delivery_id": r["delivery_id"],
            "event_id": r["event_id"],
            "alert_rule_id": r["alert_rule_id"],
            "attempt_number": r["attempt_number"],
            "timestamp": _client._normalize_ts(r["timestamp"]),
            "webhook_url": r["webhook_url"],
            "status_code": r["status_code"],
            "delivery_status": r["delivery_status"],
            "error": r.get("error"),
            "duration_ms": r["duration_ms"],
            "payload_size": r["payload_size"],
        }
        lines.append(_dumps(row))
    sql = (
        "INSERT INTO webhook_deliveries (delivery_id, event_id, alert_rule_id, "
        "attempt_number, timestamp, webhook_url, status_code, delivery_status, "
        "error, duration_ms, payload_size) FORMAT JSONEachRow"
    )
    try:
        r = await _client._query(sql, data="\n".join(lines))
        r.raise_for_status()
    except Exception as exc:
        optic.error("failed to record {} webhook deliveries in ClickHouse: {}", len(records), exc)


async def insert_session_events(rows: list[dict]):
    """Batch insert canonical session source rows into ClickHouse."""
    optic.trace("inserting {} session events into ClickHouse", len(rows))
    if not rows:
        return
    for row in rows:
        row.setdefault("source_end_offset", 0)
        row.setdefault("is_source_record", 1)
        row.setdefault("rendered", 1)
        row.setdefault("raw_line_truncated", 0)
    sql = (
        "INSERT INTO session_events (session_id, project_id, user_id, agent_id, "
        "agent_version, layer_hash, harness, line_offset, source_end_offset, line_hash, source_sha256, "
        "is_source_record, rendered, event_type, timestamp, uuid, parent_uuid, "
        "tool_name, tool_id, content_preview, content_length, raw_line, credits, parent_session_id, "
        "input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, model, raw_line_truncated) FORMAT JSONEachRow"
    )
    try:
        r = await _client._query(
            sql,
            {"wait_for_async_insert": "1"},
            data="\n".join(_dumps(row) for row in rows),
        )
        r.raise_for_status()
    except Exception as e:
        optic.error("failed to insert {} session events - session will appear incomplete: {}", len(rows), e)
        raise


async def insert_session_checkpoint(
    session_id: str,
    project_id: str,
    user_id: str,
    harness: str,
    acknowledged_line: int,
    acknowledged_offset: int,
) -> None:
    """Insert a replaceable checkpoint, including audit rewinds."""
    row = {
        "session_id": session_id,
        "project_id": project_id,
        "user_id": user_id,
        "harness": harness,
        "acknowledged_line": acknowledged_line,
        "acknowledged_offset": acknowledged_offset,
        "checkpoint_version": time.time_ns(),
    }
    r = await _client._query(
        "INSERT INTO session_checkpoints (session_id, project_id, user_id, harness, acknowledged_line, "
        "acknowledged_offset, checkpoint_version) FORMAT JSONEachRow",
        {"wait_for_async_insert": "1"},
        data=_dumps(row),
    )
    r.raise_for_status()


async def insert_forward_state(
    destination_id: str,
    signal: str,
    key: SessionKey,
    forwarded_line: int,
    *,
    replay_through: int = -1,
    session_closed: bool = False,
) -> None:
    """Record how far a destination has acknowledged a session; the newest row wins, rewinds included."""
    row = {
        "destination_id": destination_id,
        "signal": signal,
        "project_id": key.project_id,
        "user_id": key.user_id,
        "harness": key.harness,
        "session_id": key.session_id,
        "forwarded_line": forwarded_line,
        "replay_through": replay_through,
        "session_closed": 1 if session_closed else 0,
        "version": time.time_ns(),
    }
    r = await _client._query(
        "INSERT INTO otlp_forward_state (destination_id, signal, project_id, user_id, harness, session_id, "
        "forwarded_line, replay_through, session_closed, version) FORMAT JSONEachRow",
        {"wait_for_async_insert": "1"},
        data=_dumps(row),
    )
    r.raise_for_status()


async def insert_forward_deliveries(records: list[dict]) -> None:
    """Record OTLP delivery attempts. Best effort: a failure here never fails a delivery."""
    if not records:
        return
    columns = (
        "delivery_id",
        "destination_id",
        "signal",
        "session_id",
        "attempt",
        "status_code",
        "status",
        "records",
        "rejected",
        "payload_bytes",
        "duration_ms",
        "error",
        "timestamp",
    )
    data = "\n".join(_dumps({column: record.get(column) for column in columns}) for record in records)
    try:
        r = await _client._query(
            f"INSERT INTO otlp_forward_deliveries ({', '.join(columns)}) FORMAT JSONEachRow", data=data
        )
        r.raise_for_status()
    except Exception as exc:
        optic.error("failed to record {} otlp delivery attempts in ClickHouse: {}", len(records), exc)


async def refresh_session_summary(session_id: str, project_id: str, user_id: str, harness: str) -> None:
    """Replace one session summary from canonical deduplicated rows."""
    sql = (
        "INSERT INTO session_stats_agg "
        "SELECT project_id, session_id, "
        "coalesce(anyIf(agent_id, agent_id IS NOT NULL AND agent_id != ''), '') AS agent_id, "
        "coalesce(anyIf(agent_version, agent_version IS NOT NULL AND agent_version != ''), '') AS agent_version, "
        "user_id, coalesce(anyIf(parent_session_id, parent_session_id IS NOT NULL), '') AS parent_session_id, "
        "harness, coalesce(anyIf(layer_hash, layer_hash IS NOT NULL AND layer_hash != ''), '') AS layer_hash, "
        "minIf(timestamp, rendered = 1 AND timestamp > '1971-01-01 00:00:00' AND timestamp < '2099-01-01 00:00:00') AS first_event_time, "
        "maxIf(timestamp, rendered = 1 AND timestamp > '1971-01-01 00:00:00' AND timestamp < '2099-01-01 00:00:00') AS last_event_time, "
        "countIf(rendered = 1) AS event_count, countIf(rendered = 1 AND event_type = 'user_prompt') AS prompt_count, "
        "countIf(rendered = 1 AND event_type = 'tool_call') AS tool_call_count, "
        "countIf(rendered = 1 AND event_type = 'tool_result') AS tool_result_count, "
        "sumIf(input_tokens, rendered = 1) AS input_tokens, sumIf(output_tokens, rendered = 1) AS output_tokens, "
        "sumIf(cache_read_tokens, rendered = 1) AS cache_read_tokens, "
        "sumIf(cache_write_tokens, rendered = 1) AS cache_write_tokens, max(credits) AS total_credits, "
        "anyLastIf(model, rendered = 1 AND model != '') AS model, "
        "toUInt64(toUnixTimestamp64Milli(now64(3))) AS summary_version, now64(3) AS updated_at "
        "FROM session_events FINAL WHERE project_id = {pid:String} AND user_id = {uid:String} "
        "AND harness = {harness:String} AND session_id = {sid:String} "
        "GROUP BY project_id, session_id, user_id, harness"
    )
    params = {
        "param_pid": project_id,
        "param_uid": user_id,
        "param_harness": harness,
        "param_sid": session_id,
    }
    params["wait_for_async_insert"] = "1"
    r = await _client._query(sql, params)
    r.raise_for_status()


async def insert_session_capabilities(
    *, session_id: str, project_id: str, user_id: str, harness: str, uses: list[dict]
) -> None:
    """Record which discovery resources a session used (from the CLI capability lock).

    Best effort: attribution is evidence, so a ClickHouse hiccup is logged and
    never fails the ingest that carried it.
    """
    optic.trace("inserting {} capability uses for session {}", len(uses), session_id)
    if not uses:
        return
    lines = []
    for use in uses:
        used_at = _client._normalize_ts(use.get("used_at")) or _client._normalize_ts(
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        )
        lines.append(
            _dumps(
                {
                    "project_id": project_id,
                    "user_id": user_id,
                    "harness": harness,
                    "session_id": session_id,
                    "identifier": use.get("identifier") or "",
                    "kind": use.get("kind") or "",
                    "component_id": use.get("component_id") or "",
                    "native_ref": use.get("native_ref") or "",
                    "version": use.get("version") or "",
                    "digest": use.get("digest") or "",
                    "mode": use.get("mode") or "context",
                    "source": use.get("source") or "unknown",
                    "confidence": use.get("confidence") or "window",
                    "used_at": used_at,
                }
            )
        )
    sql = (
        "INSERT INTO session_capabilities (project_id, user_id, harness, session_id, identifier, kind, "
        "component_id, native_ref, version, digest, mode, source, confidence, used_at) FORMAT JSONEachRow"
    )
    try:
        r = await _client._query(sql, data="\n".join(lines))
        r.raise_for_status()
    except Exception as exc:
        optic.error("failed to insert {} capability uses for session {}: {}", len(uses), session_id, exc)


async def insert_layer_snapshot(row: dict):
    """Insert a single layer snapshot row into ClickHouse."""
    optic.trace("inserting layer snapshot: hash={}", row.get("hash", "?"))
    sql = (
        "INSERT INTO layer_snapshots (hash, project_id, user_id, harness, content, "
        "file_count, total_size, lockfile_hash) FORMAT JSONEachRow"
    )
    try:
        r = await _client._query(sql, data=_dumps(row))
        r.raise_for_status()
    except Exception as e:
        optic.error("failed to insert layer snapshot: {}", e)
        raise
