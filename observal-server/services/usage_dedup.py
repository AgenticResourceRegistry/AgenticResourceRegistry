# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Count each model call's token usage once.

Claude Code (and harnesses that write its transcript format) emit one JSONL
line per content block of an API response: thinking, text and each tool call.
Every one of those lines repeats the response's ``message.usage``. Summing
token columns across lines therefore counts a response once per block,
measured at 1.8x to 2.1x on a production deployment.

Lines that belong to one API response share ``message.id`` and
``requestId``. For each such key in a session exactly one row keeps the
token counts; the others are stored with zero tokens. The kept row is the
one with the largest ``output_tokens`` because lines written mid-stream carry
partial output counts (keeping the first line, as some tools do, undercounts).
Input and cache counts are identical across a response's lines.

The raw line is never modified, so the stored counts can always be recomputed
from it. Rows with no response id are marked with ``NO_KEY`` so the backfill
does not revisit them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import orjson
from loguru import logger as optic

if TYPE_CHECKING:
    from collections.abc import Iterable

# Harnesses whose transcripts use the Claude Code message format.
KEYED_HARNESSES = frozenset({"claude-code", "opencode", "cursor", "kiro"})
NO_KEY = "-"
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "cache_write_1h_tokens")


def usage_key(parsed: dict, harness: str) -> str:
    """Identity of the API response a line's usage belongs to, or NO_KEY / ''."""
    if harness not in KEYED_HARNESSES:
        return ""
    message = parsed.get("message")
    message_id = message.get("id") if isinstance(message, dict) else None
    if not isinstance(message_id, str) or not message_id:
        return NO_KEY
    request_id = parsed.get("requestId")
    return f"{message_id}:{request_id}" if isinstance(request_id, str) and request_id else message_id


def _int(value) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def cache_write_1h(parsed: dict, harness: str) -> int:
    """One-hour cache writes, a subset of cache_write_tokens billed at 2x input (vs 1.25x)."""
    message = parsed.get("message")
    usage = message.get("usage") if isinstance(message, dict) else None
    if not isinstance(usage, dict):
        return 0
    if harness == "pi":
        return _int(usage.get("cacheWrite1h"))
    creation = usage.get("cache_creation")
    if isinstance(creation, dict):
        return _int(creation.get("ephemeral_1h_input_tokens"))
    return 0


def has_tokens(row: dict) -> bool:
    return any(_int(row.get(f)) for f in TOKEN_FIELDS)


def winners(rows: Iterable[dict]) -> dict[str, int]:
    """Per usage key, the line_offset of the row that keeps the token counts."""
    best: dict[str, tuple[int, int]] = {}
    for row in rows:
        key = row.get("usage_key") or ""
        if not key or key == NO_KEY:
            continue
        rank = (_int(row.get("output_tokens")), -int(row["line_offset"]))
        current = best.get(key)
        if current is None or rank > (current[0], -current[1]):
            best[key] = (rank[0], int(row["line_offset"]))
    return {key: offset for key, (_, offset) in best.items()}


def zero_tokens(row: dict) -> dict:
    return {**row, **dict.fromkeys(TOKEN_FIELDS, 0)}


def dedupe_rows(rows: list[dict], existing: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
    """Apply the one-row-per-response rule.

    ``rows`` are the batch about to be inserted; ``existing`` are already
    stored token rows of the same session that share a key with the batch.
    Returns ``(rows, existing_to_zero)``: the batch with losers zeroed, and
    stored rows that lost to a better line in this batch and must be
    re-inserted with zero tokens.
    """
    existing = existing or []
    keep = winners([*existing, *[r for r in rows if has_tokens(r)]])
    out = []
    for row in rows:
        key = row.get("usage_key") or ""
        if key and key != NO_KEY and has_tokens(row) and keep.get(key) != int(row["line_offset"]):
            row = zero_tokens(row)
        out.append(row)
    batch_offsets = {int(r["line_offset"]) for r in rows}
    to_zero = [
        zero_tokens(r)
        for r in existing
        if int(r["line_offset"]) not in batch_offsets
        and has_tokens(r)
        and keep.get(r.get("usage_key") or "") != int(r["line_offset"])
    ]
    return out, to_zero


# ── ClickHouse helpers ───────────────────────────────────────────────────

_EVENT_COLUMNS = (
    "session_id, project_id, user_id, agent_id, agent_version, layer_hash, harness, line_offset, "
    "source_end_offset, line_hash, source_sha256, is_source_record, rendered, event_type, timestamp, uuid, "
    "parent_uuid, tool_name, tool_id, content_preview, content_length, raw_line, credits, parent_session_id, "
    "input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cache_write_1h_tokens, model, "
    "raw_line_truncated, usage_key"
)


def ch_string_array(values: Iterable[str]) -> str:
    """A ClickHouse Array(String) query parameter: single-quoted, backslash-escaped."""
    escaped = (v.replace("\\", "\\\\").replace("'", "\\'") for v in values)
    return "[" + ",".join(f"'{v}'" for v in escaped) + "]"


def _session_params(session_id: str, project_id: str, user_id: str, harness: str) -> dict[str, str]:
    return {"param_sid": session_id, "param_pid": project_id, "param_uid": user_id, "param_harness": harness}


_SESSION_WHERE = (
    "project_id = {pid:String} AND user_id = {uid:String} AND harness = {harness:String} AND session_id = {sid:String}"
)


async def _rows(sql: str, params: dict[str, str]) -> list[dict]:
    from services.clickhouse import _query

    resp = await _query(f"{sql} FORMAT JSON", params)
    resp.raise_for_status()
    return resp.json().get("data", [])


async def existing_keyed_rows(
    session_id: str, project_id: str, user_id: str, harness: str, keys: set[str]
) -> list[dict]:
    """Stored token rows of a session whose usage key is in ``keys``."""
    if not keys:
        return []
    params = _session_params(session_id, project_id, user_id, harness)
    params["param_keys"] = ch_string_array(sorted(keys))
    return await _rows(
        f"SELECT {_EVENT_COLUMNS} FROM session_events FINAL WHERE {_SESSION_WHERE} "
        "AND usage_key IN {keys:Array(String)} "
        "AND (input_tokens + output_tokens + cache_read_tokens + cache_write_tokens) > 0",
        params,
    )


async def dedupe_batch(
    rows: list[dict], *, session_id: str, project_id: str, user_id: str, harness: str
) -> tuple[list[dict], list[dict]]:
    """Ingestion hook: dedupe a batch against itself and the session's stored rows."""
    if harness not in KEYED_HARNESSES:
        return rows, []
    keys = {r["usage_key"] for r in rows if r.get("usage_key") not in ("", NO_KEY) and has_tokens(r)}
    existing = await existing_keyed_rows(session_id, project_id, user_id, harness, keys)
    return dedupe_rows(rows, existing)


async def backfill_session(session_id: str, project_id: str, user_id: str, harness: str) -> int:
    """Key and dedupe one stored session. Returns how many rows were rewritten.

    Reads only that session's token rows, so memory stays bounded however large
    the table is. Idempotent: a processed session has no unkeyed token rows.
    """
    from services.clickhouse import insert_session_events, refresh_session_summary

    params = _session_params(session_id, project_id, user_id, harness)
    rows = await _rows(
        f"SELECT {_EVENT_COLUMNS} FROM session_events FINAL WHERE {_SESSION_WHERE} "
        "AND (input_tokens + output_tokens + cache_read_tokens + cache_write_tokens) > 0",
        params,
    )
    if not rows:
        return 0
    updated: list[dict] = []
    for row in rows:
        try:
            parsed = orjson.loads(row.get("raw_line") or "{}")
        except orjson.JSONDecodeError:
            parsed = {}
        if not isinstance(parsed, dict):
            parsed = {}
        key = usage_key(parsed, harness) or NO_KEY
        hour = cache_write_1h(parsed, harness)
        updated.append(
            {**row, "usage_key": key, "cache_write_1h_tokens": hour or _int(row.get("cache_write_1h_tokens"))}
        )
    keep = winners(updated)
    rewritten = []
    for before, row in zip(rows, updated, strict=True):
        key = row["usage_key"]
        if key != NO_KEY and keep.get(key) != int(row["line_offset"]):
            row = zero_tokens(row)
        changed = any(str(before.get(f)) != str(row.get(f)) for f in (*TOKEN_FIELDS, "usage_key"))
        if changed:
            rewritten.append(row)
    if rewritten:
        await insert_session_events(rewritten)
        await refresh_session_summary(session_id, project_id, user_id, harness)
        optic.info("usage dedup backfill session={} rewrote {} rows", session_id, len(rewritten))
    return len(rewritten)


async def pending_sessions(limit: int, exclude: set[str] | None = None) -> list[dict]:
    """Sessions in keyed harnesses that still have unkeyed token rows.

    Reads with FINAL so rows already rewritten are not seen through their older,
    not-yet-merged versions. Only small columns are read (never raw_line), which
    keeps FINAL cheap.
    """
    rows = await _rows(
        "SELECT DISTINCT session_id, project_id, user_id, harness FROM session_events FINAL "
        "WHERE harness IN {harnesses:Array(String)} AND usage_key = '' "
        "AND (input_tokens + output_tokens + cache_read_tokens + cache_write_tokens) > 0 "
        "LIMIT {lim:UInt32}",
        {
            "param_harnesses": ch_string_array(sorted(KEYED_HARNESSES)),
            "param_lim": str(limit + len(exclude or ())),
        },
    )
    exclude = exclude or set()
    return [r for r in rows if r["session_id"] not in exclude][:limit]
