# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Builds the Cost Intelligence overview and details responses."""

from __future__ import annotations

import asyncio
import calendar
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from models.user import User
from observal_shared.harness_registry import get_harness_display_names
from schemas import cost as s
from services.cost import config as cost_config
from services.cost import queries, savings
from services.cost.pricing import PriceBook, Usage, build_price_book
from services.cost.savings import SessionLine
from services.departments import resolve_user_departments

if TYPE_CHECKING:
    from zoneinfo import ZoneInfo

    from sqlalchemy.ext.asyncio import AsyncSession

    from services.cost.config import CostSettings

RANGE_DAYS = {"7d": 7, "30d": 30, "90d": 90}
TOP_MODELS = 5
TOP_PEOPLE = 10
TOP_MODEL_USERS = 3
UNASSIGNED = "Unassigned"


# ── helpers ───────────────────────────────────────────────────────────────────


def _usage(row: dict) -> Usage:
    return Usage(int(row["input"]), int(row["output"]), int(row["cache_read"]), int(row["cache_write"]))


def _counts(usage: Usage):
    """Usage as the response model."""
    return s.TokenCounts(
        input=usage.input, output=usage.output, cache_read=usage.cache_read, cache_write=usage.cache_write
    )


def _money(value: float) -> float:
    return round(value, 2)


def _costs(by_type: dict[str, float]):
    """Cost by token type as the response model."""
    return s.TokenCosts(
        input=_money(by_type["input"]),
        output=_money(by_type["output"]),
        cache_read=_money(by_type["cache_read"]),
        cache_write=_money(by_type["cache_write"]),
    )


def _pct_change(current: float, previous: float | None) -> float | None:
    if not previous:
        return None
    return round((current - previous) / previous * 100, 1)


def _rounded(value: float | None, places: int = 3) -> float | None:
    return round(value, places) if value is not None else None


def _month_start(now: datetime, tz: ZoneInfo) -> datetime:
    local = now.astimezone(tz)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def _projection(month_to_date: float, now: datetime, tz: ZoneInfo) -> float | None:
    elapsed_days = (now - _month_start(now, tz)).total_seconds() / 86400
    if elapsed_days < 1:
        return None  # too early in the month to extrapolate
    local = now.astimezone(tz)
    days_in_month = calendar.monthrange(local.year, local.month)[1]
    return _money(month_to_date / elapsed_days * days_in_month)


def _priced_total(rows: list[dict], book: PriceBook) -> float:
    total = 0.0
    for row in rows:
        prices = book.price_for(row["model"])
        if prices:
            total += prices.cost(_usage(row))
    return total


def _session_lines(rows: list[dict], book: PriceBook) -> list[SessionLine]:
    return [
        SessionLine(r["session_id"], r["user_id"], r["harness"], r["model"], _usage(r), book.price_for(r["model"]))
        for r in rows
    ]


def _model_usage(lines: list[SessionLine]) -> dict[str, Usage]:
    usage: dict[str, Usage] = defaultdict(Usage)
    for line in lines:
        usage[line.model] = usage[line.model] + line.usage
    return usage


def _common(range_key: str, settings: CostSettings, now: datetime):
    days = RANGE_DAYS[range_key]
    bucket = "week" if days == 90 else "day"
    start = now - timedelta(days=days)
    range_ = s.CostRange(
        key=range_key,
        start=start.isoformat(),
        end=now.isoformat(),
        days=days,
        timezone=settings.timezone.key,
        bucket=bucket,
    )
    currency = s.CostCurrency(default=settings.currency, rates=settings.exchange_rates, as_of=settings.rates_as_of)
    return days, bucket, start, range_, currency


def _counted_savings(lines: list[SessionLine], prompts: dict[str, int], book: PriceBook, days: int) -> list:
    return [*savings.route_down(lines, prompts, book, days), *savings.caching(_model_usage(lines), book, days)]


# ── overview ──────────────────────────────────────────────────────────────────


async def build_overview(range_key: str, now: datetime | None = None) -> s.CostOverview:
    settings = await cost_config.load()
    book = build_price_book(settings.price_overrides)
    now = now or datetime.now(UTC)
    days, bucket, start, range_, currency = _common(range_key, settings, now)
    tz = settings.timezone.key

    current, previous, month, session_rows, prompt_rows, coverage_rows = await asyncio.gather(
        queries.bucket_usage(start, now, tz, bucket),
        queries.bucket_usage(start - timedelta(days=days), start, tz, "day"),
        queries.bucket_usage(_month_start(now, settings.timezone), now, tz, "day"),
        queries.session_usage(start, now),
        queries.session_prompts(start, now),
        queries.harness_coverage(start, now),
    )

    lines = _session_lines(session_rows, book)
    prompts = {r["session_id"]: int(r["prompts"]) for r in prompt_rows}
    priced = [line for line in lines if line.cost]
    spend = sum(line.cost for line in priced)
    prev_spend = _priced_total(previous, book) if previous else None
    month_to_date = _priced_total(month, book)
    active_users = len({line.user_id for line in priced})
    sessions_priced = len({line.session_id for line in priced})
    potential = sum(item.monthly_usd for item in _counted_savings(lines, prompts, book, days))

    # Top five models by spend, the rest as "other". Every chart colors models in this order.
    model_cost: dict[str, float] = defaultdict(float)
    for line in priced:
        model_cost[line.model] += line.cost
    ranked = sorted(model_cost, key=model_cost.get, reverse=True)
    top, other = ranked[:TOP_MODELS], ranked[TOP_MODELS:]

    model_share = [
        s.ModelShare(
            key=m, label=book.label_for(m), usd=_money(model_cost[m]), share=round(model_cost[m] / spend, 3), models=1
        )
        for m in top
    ]
    if other:
        other_usd = sum(model_cost[m] for m in other)
        model_share.append(
            s.ModelShare(
                key="other", label="Other", usd=_money(other_usd), share=round(other_usd / spend, 3), models=len(other)
            )
        )

    series_map: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in current:
        prices = book.price_for(row["model"])
        if prices:
            name = row["model"] if row["model"] in top else "other"
            series_map[str(row["bucket"])][name] += prices.cost(_usage(row))
    series = [
        s.SeriesPoint(
            bucket_start=bucket_start,
            total_usd=_money(sum(amounts.values())),
            by_model=[s.ModelAmount(key=name, usd=_money(usd)) for name, usd in amounts.items()],
        )
        for bucket_start, amounts in sorted(series_map.items())
    ]

    names = get_harness_display_names()
    harness_cost: dict[str, float] = defaultdict(float)
    for line in priced:
        harness_cost[line.harness] += line.cost
    harnesses = [
        s.HarnessSpend(harness=h, display_name=names.get(h, h), usd=_money(usd), share=round(usd / spend, 3))
        for h, usd in sorted(harness_cost.items(), key=lambda kv: kv[1], reverse=True)
    ]

    unpriced_usage: dict[str, int] = defaultdict(int)
    for line in lines:
        if line.prices is None:
            unpriced_usage[line.model or "(no model)"] += line.usage.total
    not_priced = []
    for row in coverage_rows:
        credits, with_usage = float(row["credits"]), int(row["sessions_with_usage"])
        if credits > 0 or with_usage == 0:
            not_priced.append(
                s.NotPricedHarness(
                    harness=row["harness"],
                    display_name=names.get(row["harness"], row["harness"]),
                    sessions=int(row["sessions"]),
                    credits=credits,
                    reason="credits_only" if credits > 0 else "no_usage",
                )
            )

    return s.CostOverview(
        range=range_,
        currency=currency,
        kpis=s.CostKpis(
            spend_usd=_money(spend),
            prev_spend_usd=_money(prev_spend) if prev_spend is not None else None,
            change_pct=_pct_change(spend, prev_spend),
            month_to_date_usd=_money(month_to_date),
            projected_month_usd=_projection(month_to_date, now, settings.timezone),
            active_users=active_users,
            cost_per_active_user_usd=_money(spend / active_users) if active_users else None,
            sessions_priced=sessions_priced,
            cost_per_session_usd=_money(spend / sessions_priced) if sessions_priced else None,
            potential_savings_monthly_usd=_money(potential),
        ),
        coverage=s.CostCoverage(
            sessions_total=sum(int(r["sessions"]) for r in coverage_rows),
            sessions_with_usage=sum(int(r["sessions_with_usage"]) for r in coverage_rows),
            unpriced_models=[s.UnpricedModel(model=m, usage_total=n) for m, n in sorted(unpriced_usage.items())],
            not_priced=not_priced,
        ),
        series=series,
        model_share=model_share,
        harnesses=harnesses,
    )


# ── details ───────────────────────────────────────────────────────────────────


async def _usernames(db: AsyncSession, user_ids: set[str]) -> dict[str, str]:
    valid = []
    for user_id in user_ids:
        try:
            valid.append(uuid.UUID(user_id))
        except ValueError:
            continue
    if not valid:
        return {}
    rows = (await db.execute(select(User.id, User.username).where(User.id.in_(valid)))).all()
    return {str(r.id): r.username for r in rows}


async def build_details(db: AsyncSession, range_key: str, now: datetime | None = None) -> s.CostDetails:
    settings = await cost_config.load()
    book = build_price_book(settings.price_overrides)
    now = now or datetime.now(UTC)
    days, _bucket, start, range_, currency = _common(range_key, settings, now)

    current_rows, previous_rows, prompt_rows = await asyncio.gather(
        queries.session_usage(start, now),
        queries.session_usage(start - timedelta(days=days), start),
        queries.session_prompts(start, now),
    )
    lines = _session_lines(current_rows, book)
    previous = _session_lines(previous_rows, book)
    prompts = {r["session_id"]: int(r["prompts"]) for r in prompt_rows}

    # department -> members, and user -> departments (a user can be in several SCIM groups)
    members = await resolve_user_departments(db)
    user_departments: dict[str, list[str]] = defaultdict(list)
    for department, user_ids in members.items():
        for user_id in user_ids:
            user_departments[user_id].append(department)
    for line in lines:  # users seen in ClickHouse but unknown to Postgres
        if line.user_id not in user_departments:
            user_departments[line.user_id] = [UNASSIGNED]
            members.setdefault(UNASSIGNED, []).append(line.user_id)
    usernames = await _usernames(db, {line.user_id for line in lines})

    user_sessions: dict[str, set[str]] = defaultdict(set)
    user_cost: dict[str, float] = defaultdict(float)
    user_model_cost: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    user_usage: dict[str, Usage] = defaultdict(Usage)
    for line in lines:
        if line.cost is None:
            continue
        user_sessions[line.user_id].add(line.session_id)
        user_cost[line.user_id] += line.cost
        user_model_cost[line.user_id][line.model] += line.cost
        user_usage[line.user_id] = user_usage[line.user_id] + line.usage
    prev_user_cost: dict[str, float] = defaultdict(float)
    for line in previous:
        if line.cost is not None:
            prev_user_cost[line.user_id] += line.cost

    session_counts = {u: len(ids) for u, ids in user_sessions.items()}
    per_session = {u: user_cost[u] / session_counts[u] for u in session_counts}
    medians = savings.department_medians(per_session, members)

    departments = []
    for name, user_ids in members.items():
        active = [u for u in dict.fromkeys(user_ids) if u in user_cost]
        if not active:
            continue
        usd = sum(user_cost[u] for u in active)
        prev = sum(prev_user_cost[u] for u in dict.fromkeys(user_ids)) or None
        median = medians.get(name)
        people = []
        for user_id in sorted(active, key=user_cost.get, reverse=True):
            top_model = max(user_model_cost[user_id], key=user_model_cost[user_id].get)
            ratio = per_session[user_id] / median if median else None
            people.append(
                s.PersonSpend(
                    user_id=user_id,
                    username=usernames.get(user_id, user_id),
                    sessions=session_counts[user_id],
                    usd=_money(user_cost[user_id]),
                    per_session_usd=_money(per_session[user_id]),
                    top_model=top_model,
                    top_model_label=book.label_for(top_model),
                    top_model_share=round(user_model_cost[user_id][top_model] / user_cost[user_id], 3),
                    cache_hit=_rounded(user_usage[user_id].cache_hit),
                    ratio_to_median=_rounded(ratio, 2),
                    flagged=bool(
                        ratio
                        and ratio >= savings.REVIEW_RATIO
                        and session_counts[user_id] >= savings.REVIEW_MIN_SESSIONS
                    ),
                )
            )
        rest = people[TOP_PEOPLE:]
        departments.append(
            s.DepartmentSpend(
                name=name,
                users=len(active),
                sessions=sum(session_counts[u] for u in active),
                usd=_money(usd),
                prev_usd=_money(prev) if prev else None,
                change_pct=_pct_change(usd, prev),
                per_user_usd=_money(usd / len(active)),
                median_per_session_usd=_money(median) if median else None,
                people=people[:TOP_PEOPLE],
                rest=s.RestOfDepartment(
                    users=len(rest), sessions=sum(p.sessions for p in rest), usd=_money(sum(p.usd for p in rest))
                )
                if rest
                else None,
            )
        )
    departments.sort(key=lambda d: d.usd, reverse=True)

    names = get_harness_display_names()
    spend = sum(user_cost.values())
    model_lines: dict[str, list[SessionLine]] = defaultdict(list)
    for line in lines:
        model_lines[line.model].append(line)
    models = []
    for model, items in model_lines.items():
        usage = sum((line.usage for line in items), Usage())
        prices = book.price_for(model)
        usd = prices.cost(usage) if prices else None
        harness_cost: dict[str, float] = defaultdict(float)
        user_model: dict[str, float] = defaultdict(float)
        for line in items:
            if line.cost is not None:
                harness_cost[line.harness] += line.cost
                user_model[line.user_id] += line.cost
        top_users = sorted(user_model, key=user_model.get, reverse=True)[:TOP_MODEL_USERS]
        models.append(
            s.ModelBreakdown(
                model=model,
                label=book.label_for(model),
                provider=prices.provider if prices else "",
                priced=prices is not None,
                sessions=len({line.session_id for line in items}),
                usage=_counts(usage),
                cost_usd=_costs(prices.cost_by_type(usage)) if prices else None,
                cache_hit=_rounded(usage.cache_hit),
                usd=_money(usd) if usd is not None else None,
                share=round(usd / spend, 3) if usd is not None and spend else None,
                harnesses=[
                    s.ModelHarness(harness=h, display_name=names.get(h, h), usd=_money(v), share=round(v / usd, 3))
                    for h, v in sorted(harness_cost.items(), key=lambda kv: kv[1], reverse=True)
                ]
                if usd
                else [],
                top_users=[
                    s.ModelUser(
                        user_id=u,
                        username=usernames.get(u, u),
                        departments=user_departments.get(u, [UNASSIGNED]),
                        usd=_money(user_model[u]),
                    )
                    for u in top_users
                ],
            )
        )
    models.sort(key=lambda m: (m.usd is None, -(m.usd or 0)))

    priced_lines = [line for line in lines if line.prices]
    total_usage = sum((line.usage for line in priced_lines), Usage())
    total_by_type: dict[str, float] = defaultdict(float)
    for line in priced_lines:
        for field_name, value in line.prices.cost_by_type(line.usage).items():
            total_by_type[field_name] += value

    review = savings.review_users(session_counts, per_session, user_departments, medians, usernames)
    return s.CostDetails(
        range=range_,
        currency=currency,
        savings=[*_counted_savings(lines, prompts, book, days), *([review] if review else [])],
        departments=departments,
        multi_department_users=sum(1 for u in user_cost if len(user_departments.get(u, [])) > 1),
        models=models,
        totals=s.CostTotals(
            sessions=len({line.session_id for line in priced_lines}),
            usage=_counts(total_usage),
            cost_usd=_costs(total_by_type),
            cache_hit=_rounded(total_usage.cache_hit),
            usd=_money(spend),
        ),
    )
