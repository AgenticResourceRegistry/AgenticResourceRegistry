# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""ClickHouse reads for Cost Intelligence. Every value is a query parameter; nothing is interpolated."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from loguru import logger as optic

from observal_shared.migration.constants import DEFAULT_PROJECT_ID
from services.clickhouse import _query

Bucket = Literal["day", "week"]

_USAGE_SUM = "input_tokens + output_tokens + cache_read_tokens + cache_write_tokens"
_WHERE = (
    "project_id = {pid:String} AND rendered = 1 "
    "AND timestamp >= {start:DateTime64(3)} AND timestamp < {end:DateTime64(3)}"
)
_SUMS = (
    "sum(input_tokens) AS input, sum(output_tokens) AS output, "
    "sum(cache_read_tokens) AS cache_read, sum(cache_write_tokens) AS cache_write"
)
_SETTINGS = " SETTINGS do_not_merge_across_partitions_select_final = 1"
# Picked from a Literal, never from user input.
_BUCKET_SQL = {
    "day": "toDate(timestamp, {tz:String})",
    "week": "toMonday(toDate(timestamp, {tz:String}))",
}


class CostDataUnavailableError(RuntimeError):
    """ClickHouse could not answer. The API returns 503 rather than zeros."""


def _ts(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _params(start: datetime, end: datetime, **extra: str) -> dict[str, str]:
    params = {"param_pid": DEFAULT_PROJECT_ID, "param_start": _ts(start), "param_end": _ts(end)}
    params.update({f"param_{name}": value for name, value in extra.items()})
    return params


async def _rows(sql: str, params: dict[str, str]) -> list[dict]:
    try:
        response = await _query(f"{sql}{_SETTINGS} FORMAT JSON", params)
    except Exception as exc:
        optic.warning("cost query failed: error={}", type(exc).__name__)
        raise CostDataUnavailableError from exc
    if response.status_code != 200:
        optic.warning("cost query failed: status={} body={}", response.status_code, response.text[:200])
        raise CostDataUnavailableError
    return response.json().get("data", [])


async def bucket_usage(start: datetime, end: datetime, tz: str, bucket: Bucket) -> list[dict]:
    """Usage per (bucket, harness, model). Feeds the daily chart, the previous period and month to date."""
    sql = (
        f"SELECT {_BUCKET_SQL[bucket]} AS bucket, harness, model, {_SUMS} "
        f"FROM session_events FINAL WHERE {_WHERE} AND ({_USAGE_SUM}) > 0 "
        "GROUP BY bucket, harness, model"
    )
    return await _rows(sql, _params(start, end, tz=tz))


async def session_usage(start: datetime, end: datetime) -> list[dict]:
    """Usage per (session, user, harness, model). Everything per user, department and model."""
    sql = (
        f"SELECT session_id, user_id, harness, model, {_SUMS} "
        f"FROM session_events FINAL WHERE {_WHERE} AND ({_USAGE_SUM}) > 0 "
        "GROUP BY session_id, user_id, harness, model"
    )
    return await _rows(sql, _params(start, end))


async def session_prompts(start: datetime, end: datetime) -> list[dict]:
    """User prompts per session, for the short-session routing estimate."""
    sql = (
        "SELECT session_id, countIf(event_type = 'user_prompt') AS prompts "
        f"FROM session_events FINAL WHERE {_WHERE} GROUP BY session_id"
    )
    return await _rows(sql, _params(start, end))


async def harness_coverage(start: datetime, end: datetime) -> list[dict]:
    """Per harness: all sessions, sessions with usage, and credits (Kiro stores a running total, so max)."""
    sql = (
        "SELECT harness, count() AS sessions, countIf(usage_total > 0) AS sessions_with_usage, "
        "sum(credits) AS credits FROM ("
        f"  SELECT harness, session_id, sum({_USAGE_SUM}) AS usage_total, max(credits) AS credits "
        f"  FROM session_events FINAL WHERE {_WHERE} GROUP BY harness, session_id"
        ") GROUP BY harness"
    )
    return await _rows(sql, _params(start, end))
