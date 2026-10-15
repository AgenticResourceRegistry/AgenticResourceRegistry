# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Cost ledger: priced facts built from per-call token telemetry.

Every billable model call is one ``session_events`` row with its model and
token buckets, so cost is computed per (day, harness, model, agent, version)
from those rows. ``session_stats_agg`` is not used for money: it keeps only
the last model a session used, which misprices sessions that switch models.

Queries raise :class:`CostDataUnavailableError` on failure. A ClickHouse
outage must surface as an error state, never as zero spend.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from loguru import logger as optic

from observal_shared.migration.constants import DEFAULT_PROJECT_ID
from services.cost.pricing import PRICE_SOURCE_CREDIT, PriceBook, TokenUsage, normalize_model

NO_AGENT = ""


class CostDataUnavailableError(RuntimeError):
    """Telemetry could not be read; callers must report an error, not zero."""


async def ch_rows(sql: str, params: dict[str, str] | None = None) -> list[dict]:
    """Run a read query and return rows, raising instead of returning []."""
    from services.clickhouse import _query

    try:
        resp = await _query(f"{sql} FORMAT JSON", params)
    except Exception as exc:  # network errors after the client's own retries
        raise CostDataUnavailableError("ClickHouse is unreachable") from exc
    if resp.status_code != 200:
        optic.warning("cost ledger query failed status={}", resp.status_code)
        raise CostDataUnavailableError(f"ClickHouse query failed ({resp.status_code})")
    return resp.json().get("data", [])


def _ts(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _f(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


@dataclass(slots=True)
class CostLine:
    """One priced group of model calls."""

    day: date
    harness: str
    raw_model: str
    model: str
    agent_id: str
    agent_version: str
    sessions: int
    usage: TokenUsage
    credits: float
    cost: float | None  # None: could not be priced
    cache_savings: float
    price_source: str | None


@dataclass(slots=True)
class Coverage:
    priced_cost: float = 0.0
    priced_tokens: float = 0.0
    unpriced_tokens: float = 0.0
    unpriced_models: dict[str, float] = field(default_factory=dict)
    unpriced_credits: dict[str, float] = field(default_factory=dict)
    price_sources: set[str] = field(default_factory=set)

    @property
    def priced_token_pct(self) -> float | None:
        total = self.priced_tokens + self.unpriced_tokens
        return round(100 * self.priced_tokens / total, 1) if total else None


def price_line(row: dict, book: PriceBook) -> CostLine:
    usage = TokenUsage(
        input=_f(row.get("input")),
        output=_f(row.get("output")),
        cache_read=_f(row.get("cache_read")),
        cache_write=_f(row.get("cache_write")),
        cache_write_1h=_f(row.get("cache_write_1h")),
    )
    # "credit_total", not "credits": an alias named like the column would shadow it in WHERE.
    credits = _f(row.get("credit_total", row.get("credits")))
    harness = row.get("harness") or ""
    raw_model = row.get("model") or ""
    cost: float | None = None
    cache_savings = 0.0
    source: str | None = None
    if usage.total > 0:
        prices = book.prices_for(raw_model)
        if prices is not None:
            cost = usage.cost(prices)
            cache_savings = usage.cache_savings(prices)
            source = prices.source
    elif credits > 0:
        rate = book.credit_rate(harness)
        if rate is not None:
            cost = credits * rate
            source = PRICE_SOURCE_CREDIT
    else:
        cost = 0.0  # no billable usage at all (tool results, prompts)
    day_raw = str(row.get("day") or "")[:10]
    return CostLine(
        day=date.fromisoformat(day_raw) if day_raw else date.min,
        harness=harness,
        raw_model=raw_model,
        model=normalize_model(raw_model) or ("(credits)" if credits > 0 else "(unknown)"),
        agent_id=row.get("agent_id") or NO_AGENT,
        agent_version=row.get("agent_version") or "",
        sessions=int(_f(row.get("sessions"))),
        usage=usage,
        credits=credits,
        cost=cost,
        cache_savings=cache_savings,
        price_source=source,
    )


def coverage_of(lines: list[CostLine]) -> Coverage:
    cov = Coverage()
    for line in lines:
        if line.cost is None:
            if line.usage.total > 0:
                cov.unpriced_tokens += line.usage.total
                key = line.raw_model or "(no model)"
                cov.unpriced_models[key] = cov.unpriced_models.get(key, 0) + line.usage.total
            if line.credits > 0:
                cov.unpriced_credits[line.harness] = cov.unpriced_credits.get(line.harness, 0) + line.credits
            continue
        cov.priced_cost += line.cost
        cov.priced_tokens += line.usage.total
        if line.price_source:
            cov.price_sources.add(line.price_source)
    return cov


async def daily_cost_lines(start: datetime, end: datetime, book: PriceBook, tz: str = "UTC") -> list[CostLine]:
    """Priced (day, harness, model, agent, version) lines for [start, end)."""
    rows = await ch_rows(
        "SELECT toDate(timestamp, {tz:String}) AS day, harness, model, "
        "ifNull(agent_id, '') AS agent_id, ifNull(agent_version, '') AS agent_version, "
        "uniqExact(session_id) AS sessions, "
        "sum(input_tokens) AS input, sum(output_tokens) AS output, "
        "sum(cache_read_tokens) AS cache_read, sum(cache_write_tokens) AS cache_write, "
        "sum(cache_write_1h_tokens) AS cache_write_1h, sum(credits) AS credit_total "
        "FROM session_events FINAL "
        "WHERE project_id = {pid:String} AND rendered = 1 "
        "AND timestamp >= {start:DateTime64(3)} AND timestamp < {end:DateTime64(3)} "
        "AND (input_tokens + output_tokens + cache_read_tokens + cache_write_tokens > 0 OR credits > 0) "
        "GROUP BY day, harness, model, agent_id, agent_version "
        "SETTINGS do_not_merge_across_partitions_select_final = 1",
        {"param_pid": DEFAULT_PROJECT_ID, "param_start": _ts(start), "param_end": _ts(end), "param_tz": tz},
    )
    return [price_line(r, book) for r in rows]


@dataclass(slots=True)
class SessionCost:
    session_id: str
    harness: str
    agent_id: str
    agent_version: str
    user_id: str
    started_at: str
    cost: float | None
    usage: TokenUsage
    first_turn_context: float  # tokens sent with the first billable call
    first_turn_cost: float | None
    prs_created: int  # PR-creation tool calls (attempts, not confirmed merges)
    tool_errors: int
    by_model: dict[str, TokenUsage]
    calls: int = 0
    credits: float = 0.0


async def session_costs(start: datetime, end: datetime, book: PriceBook) -> list[SessionCost]:
    """Per-session cost, context size, PR and error counts for sessions started in [start, end)."""
    params = {"param_pid": DEFAULT_PROJECT_ID, "param_start": _ts(start), "param_end": _ts(end)}
    usage_rows = await ch_rows(
        "SELECT session_id, harness, model, any(ifNull(agent_id, '')) AS agent_id, "
        "any(ifNull(agent_version, '')) AS agent_version, any(user_id) AS user_id, "
        "min(timestamp) AS started_at, "
        "sum(input_tokens) AS input, sum(output_tokens) AS output, "
        "sum(cache_read_tokens) AS cache_read, sum(cache_write_tokens) AS cache_write, "
        "sum(cache_write_1h_tokens) AS cache_write_1h, sum(credits) AS credit_total, "
        "countIf(input_tokens + output_tokens + cache_read_tokens + cache_write_tokens > 0) AS calls "
        "FROM session_events FINAL "
        "WHERE project_id = {pid:String} AND rendered = 1 "
        "AND session_id IN (SELECT session_id FROM session_events "
        "  WHERE project_id = {pid:String} GROUP BY session_id "
        "  HAVING min(timestamp) >= {start:DateTime64(3)} AND min(timestamp) < {end:DateTime64(3)}) "
        "GROUP BY session_id, harness, model "
        "SETTINGS do_not_merge_across_partitions_select_final = 1",
        params,
    )
    shape_rows = await ch_rows(
        "SELECT session_id, "
        "argMinIf(input_tokens + cache_read_tokens + cache_write_tokens, timestamp, "
        "  input_tokens + cache_read_tokens + cache_write_tokens > 0) AS first_context, "
        "argMinIf(model, timestamp, input_tokens + cache_read_tokens + cache_write_tokens > 0) AS first_model, "
        "countIf(event_type = 'tool_call' AND (positionCaseInsensitive(raw_line, 'gh pr create') > 0 "
        "  OR positionCaseInsensitive(ifNull(tool_name, ''), 'create_pull_request') > 0)) AS prs, "
        "countIf(event_type = 'tool_result' AND (positionCaseInsensitive(raw_line, '\"is_error\":true') > 0 "
        "  OR positionCaseInsensitive(raw_line, '\"isError\":true') > 0)) AS tool_errors "
        "FROM session_events FINAL "
        "WHERE project_id = {pid:String} AND rendered = 1 "
        "AND session_id IN (SELECT session_id FROM session_events "
        "  WHERE project_id = {pid:String} GROUP BY session_id "
        "  HAVING min(timestamp) >= {start:DateTime64(3)} AND min(timestamp) < {end:DateTime64(3)}) "
        "GROUP BY session_id "
        "SETTINGS do_not_merge_across_partitions_select_final = 1",
        params,
    )
    shapes = {r["session_id"]: r for r in shape_rows}

    sessions: dict[str, SessionCost] = {}
    for row in usage_rows:
        sid = row["session_id"]
        line = price_line({**row, "day": str(row.get("started_at", ""))[:10]}, book)
        sc = sessions.get(sid)
        if sc is None:
            shape = shapes.get(sid, {})
            first_context = _f(shape.get("first_context"))
            first_prices = book.prices_for(shape.get("first_model") or "")
            sc = SessionCost(
                session_id=sid,
                harness=row.get("harness") or "",
                agent_id=row.get("agent_id") or NO_AGENT,
                agent_version=row.get("agent_version") or "",
                user_id=row.get("user_id") or "",
                started_at=str(row.get("started_at") or ""),
                cost=0.0,
                usage=TokenUsage(),
                first_turn_context=first_context,
                # Context is usually served from cache after the first turn, so
                # the cache-read price is the conservative per-turn carrying cost.
                first_turn_cost=first_context * first_prices.cache_read if first_prices else None,
                prs_created=int(_f(shape.get("prs"))),
                tool_errors=int(_f(shape.get("tool_errors"))),
                by_model={},
            )
            sessions[sid] = sc
        if not sc.agent_id and line.agent_id:
            sc.agent_id, sc.agent_version = line.agent_id, line.agent_version
        sc.usage = sc.usage + line.usage
        sc.calls += int(_f(row.get("calls")))
        sc.credits += line.credits
        if line.usage.total > 0:
            sc.by_model[line.raw_model] = line.usage
        if line.cost is None:
            if line.usage.total > 0 or line.credits > 0:
                sc.cost = None
        elif sc.cost is not None:
            sc.cost += line.cost
    return list(sessions.values())


async def earliest_event() -> datetime | None:
    """Oldest telemetry still stored. Retention TTLs expire older rows, so months
    before this are unknown, not zero."""
    rows = await ch_rows(
        "SELECT min(timestamp) AS earliest, count() AS n FROM session_events "
        "WHERE project_id = {pid:String} AND timestamp > '1971-01-01'",
        {"param_pid": DEFAULT_PROJECT_ID},
    )
    if not rows or not int(_f(rows[0].get("n"))):
        return None
    raw = str(rows[0].get("earliest") or "")
    try:
        parsed = datetime.fromisoformat(raw.replace(" ", "T"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def sum_by(lines: list[CostLine], key) -> dict:
    out: dict = defaultdict(float)
    for line in lines:
        if line.cost is not None:
            out[key(line)] += line.cost
    return dict(out)
