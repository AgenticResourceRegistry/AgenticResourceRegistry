# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Read-only savings estimates. Each one is a plain function over already-priced usage."""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field

from schemas.cost import CachingSaving, ReviewUser, ReviewUsersSaving, RouteDownSaving
from services.cost.pricing import PriceBook, Prices, Usage

ROUTE_MAX_PROMPTS = 15  # a "short" session has fewer prompts than this
CACHE_MIN_SPEND_USD = 50.0  # ignore models with little spend
CACHE_GAP = 0.10  # at least 10 points below the org cache hit rate
REVIEW_MIN_SESSIONS = 20
REVIEW_RATIO = 1.5
MIN_MONTHLY_USD = 10.0


def per_month(amount: float, days: int) -> float:
    return round(amount * 30 / days, 2)


@dataclass(frozen=True, slots=True)
class SessionLine:
    """Usage of one model by one session."""

    session_id: str
    user_id: str
    harness: str
    model: str
    usage: Usage
    prices: Prices | None

    @property
    def cost(self) -> float | None:
        return self.prices.cost(self.usage) if self.prices else None


@dataclass(slots=True)
class _RouteTally:
    short_sessions: set[str] = field(default_factory=set)
    all_sessions: set[str] = field(default_factory=set)
    cost: float = 0.0
    saving: float = 0.0


def route_down(lines: list[SessionLine], prompts: dict[str, int], book: PriceBook, days: int) -> list[RouteDownSaving]:
    """Short sessions on a model with a cheaper sibling, repriced at the sibling's list rates."""
    tallies: dict[str, _RouteTally] = defaultdict(_RouteTally)
    for line in lines:
        if not line.prices or not line.prices.route_to:
            continue
        target = book.price_for(line.prices.route_to)
        if target is None:
            continue
        tally = tallies[line.model]
        tally.all_sessions.add(line.session_id)
        if prompts.get(line.session_id, 0) < ROUTE_MAX_PROMPTS:
            current = line.prices.cost(line.usage)
            tally.short_sessions.add(line.session_id)
            tally.cost += current
            tally.saving += current - target.cost(line.usage)

    results = []
    for model, tally in tallies.items():
        monthly = per_month(tally.saving, days)
        prices = book.price_for(model)
        if monthly < MIN_MONTHLY_USD or prices is None or not prices.route_to:
            continue
        results.append(
            RouteDownSaving(
                monthly_usd=monthly,
                model=model,
                model_label=book.label_for(model),
                route_to=prices.route_to,
                route_to_label=book.label_for(prices.route_to),
                sessions=len(tally.short_sessions),
                model_sessions=len(tally.all_sessions),
                max_prompts=ROUTE_MAX_PROMPTS,
                cost_usd=round(tally.cost, 2),
            )
        )
    return sorted(results, key=lambda r: r.monthly_usd, reverse=True)


def caching(model_usage: dict[str, Usage], book: PriceBook, days: int) -> list[CachingSaving]:
    """Models well below the org cache hit rate, repriced as if they matched it."""
    priced = {model: usage for model, usage in model_usage.items() if book.price_for(model)}
    org_hit = sum(priced.values(), Usage()).cache_hit
    if org_hit is None:
        return []

    results = []
    for model, usage in priced.items():
        prices = book.price_for(model)
        hit = usage.cache_hit
        if prices is None or hit is None:
            continue
        if prices.cost(usage) < CACHE_MIN_SPEND_USD or org_hit - hit < CACHE_GAP:
            continue
        extra = min(usage.input, max(0, round(org_hit * usage.input_side) - usage.cache_read))
        monthly = per_month(extra * (prices.input - prices.cache_read) / 1_000_000, days)
        if monthly < MIN_MONTHLY_USD:
            continue
        results.append(
            CachingSaving(
                monthly_usd=monthly,
                model=model,
                model_label=book.label_for(model),
                cache_hit=round(hit, 3),
                org_cache_hit=round(org_hit, 3),
                extra_cached_input=extra,
                input_price=prices.input,
                cache_read_price=prices.cache_read,
            )
        )
    return sorted(results, key=lambda r: r.monthly_usd, reverse=True)


def department_medians(per_session: dict[str, float], members: dict[str, list[str]]) -> dict[str, float]:
    """Median cost per session for each department, over members who had sessions."""
    medians = {}
    for department, user_ids in members.items():
        values = [per_session[user_id] for user_id in user_ids if user_id in per_session]
        if values:
            medians[department] = statistics.median(values)
    return medians


def review_users(
    sessions: dict[str, int],
    per_session: dict[str, float],
    user_departments: dict[str, list[str]],
    medians: dict[str, float],
    usernames: dict[str, str],
) -> ReviewUsersSaving | None:
    """Users far above their department's median cost per session. Never added to the savings total."""
    flagged = []
    for user_id, value in per_session.items():
        if sessions.get(user_id, 0) < REVIEW_MIN_SESSIONS:
            continue
        departments = user_departments.get(user_id, [])
        ratios = [value / medians[d] for d in departments if medians.get(d)]
        if ratios and max(ratios) >= REVIEW_RATIO:
            flagged.append(
                ReviewUser(
                    user_id=user_id,
                    username=usernames.get(user_id, user_id),
                    departments=departments,
                    ratio_to_median=round(max(ratios), 2),
                )
            )
    if not flagged:
        return None
    return ReviewUsersSaving(users=sorted(flagged, key=lambda u: u.ratio_to_median, reverse=True))
