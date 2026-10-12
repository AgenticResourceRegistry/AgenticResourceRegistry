# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Assemble the Cost Intelligence dashboard.

Every number here is either measured from telemetry, a deterministic formula
over measured data (with its inputs kept), or explicitly unavailable. The
response carries an ``evidence`` label on each figure:

- ``measured``: priced directly from recorded usage.
- ``estimated``: a detector formula over recorded usage; not yet realized.
- ``unavailable``: the inputs do not exist; ``reason`` says which.
"""

from __future__ import annotations

import calendar
import json
import statistics
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from loguru import logger as optic
from sqlalchemy import select

import services.dynamic_settings as ds
from models.agent import Agent, AgentStatus, AgentVersion
from models.cost_opportunity import (
    STATUS_APPROVED,
    STATUS_DISMISSED,
    STATUS_IDENTIFIED,
    STATUS_IMPLEMENTED,
    CostOpportunity,
)
from models.insight_session_facets import InsightSessionFacets
from services.cost import ledger, opportunities, realized
from services.cost.ledger import NO_AGENT, CostLine, SessionCost
from services.cost.pricing import PriceBook, TokenUsage, display_model, load_price_book, price_book_ref

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

TREND_MONTHS = 6
DETECTION_WINDOW_DAYS = 30
ANOMALY_BASELINE_DAYS = 60
ANOMALY_ROBUST_Z = 3.5
ANOMALY_MIN_USD = 5.0
ANOMALY_MIN_HISTORY_DAYS = 14
CACHE_KEY = "cost:intelligence:v1"
CACHE_TTL_SECONDS = 300
NO_AGENT_LABEL = "No agent"
UNVERSIONED = "unversioned"


# ── Windows ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Windows:
    now: datetime
    month_start: datetime
    prev_month_start: datetime
    prev_mtd_end: datetime  # same elapsed time into the previous month
    trend_start: datetime
    detection_start: datetime


def month_floor(value: datetime) -> datetime:
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def add_months(value: datetime, months: int) -> datetime:
    month = value.month - 1 + months
    year = value.year + month // 12
    month = month % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def windows_for(now: datetime) -> Windows:
    now = now.astimezone(UTC)
    month_start = month_floor(now)
    prev_month_start = add_months(month_start, -1)
    elapsed = now - month_start
    prev_mtd_end = min(prev_month_start + elapsed, month_start)
    return Windows(
        now=now,
        month_start=month_start,
        prev_month_start=prev_month_start,
        prev_mtd_end=prev_mtd_end,
        trend_start=add_months(month_start, -(TREND_MONTHS - 1)),
        detection_start=now - timedelta(days=DETECTION_WINDOW_DAYS),
    )


# ── Small helpers ────────────────────────────────────────────────────────


def _round(value: float | None, places: int = 2) -> float | None:
    return None if value is None else round(value, places)


def _pct_change(current: float, previous: float) -> float | None:
    if previous <= 0:
        return None
    return round(100 * (current - previous) / previous, 1)


def _total(lines: list[CostLine]) -> float:
    return sum(line.cost for line in lines if line.cost is not None)


def _usage_of(lines: list[CostLine]) -> TokenUsage:
    out = TokenUsage()
    for line in lines:
        if line.cost is None:
            continue
        u = line.usage
        out = TokenUsage(
            out.input + u.input, out.output + u.output, out.cache_read + u.cache_read, out.cache_write + u.cache_write
        )
    return out


def _parse_ts(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace(" ", "T").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _sessions_between(sessions: list[SessionCost], start: datetime, end: datetime) -> list[SessionCost]:
    out = []
    for s in sessions:
        started = _parse_ts(s.started_at)
        if started and start <= started < end:
            out.append(s)
    return out


async def _agent_names(db: AsyncSession, ids: set[str]) -> dict[str, str]:
    parsed = []
    for raw in ids:
        try:
            parsed.append(uuid.UUID(raw))
        except (ValueError, TypeError):
            continue
    if not parsed:
        return {}
    rows = (await db.execute(select(Agent.id, Agent.name).where(Agent.id.in_(parsed)))).all()
    return {str(r.id): r.name for r in rows}


def _label(agent_id: str, names: dict[str, str]) -> str:
    if agent_id == NO_AGENT:
        return NO_AGENT_LABEL
    try:
        return names.get(str(uuid.UUID(agent_id)), names.get(agent_id, "Unknown agent"))
    except ValueError:
        return names.get(agent_id, "Unknown agent")


async def criteria_coverage(db: AsyncSession, agent_ids: set[str]) -> dict:
    """How many agents with spend define creator success criteria on their latest version."""
    parsed = []
    for raw in agent_ids:
        try:
            parsed.append(uuid.UUID(raw))
        except (ValueError, TypeError):
            continue
    if not parsed:
        return {"agents_with_spend": 0, "agents_with_criteria": 0}
    rows = (
        await db.execute(
            select(AgentVersion.success_criteria)
            .join(Agent, Agent.latest_version_id == AgentVersion.id)
            .where(Agent.id.in_(parsed))
        )
    ).all()
    defined = sum(
        1 for r in rows if isinstance(r.success_criteria, dict) and r.success_criteria.get("intended_purpose")
    )
    return {"agents_with_spend": len(parsed), "agents_with_criteria": defined}


async def load_outcomes(db: AsyncSession, session_ids: list[str]) -> dict[str, str]:
    """Session outcome from cached insight facets (LLM-assessed, reviewed by humans in reports)."""
    out: dict[str, str] = {}
    for i in range(0, len(session_ids), 500):
        chunk = session_ids[i : i + 500]
        rows = (
            await db.execute(
                select(InsightSessionFacets.session_id, InsightSessionFacets.facets).where(
                    InsightSessionFacets.session_id.in_(chunk)
                )
            )
        ).all()
        for row in rows:
            outcome = (row.facets or {}).get("outcome") if isinstance(row.facets, dict) else None
            if isinstance(outcome, str):
                out[row.session_id] = outcome
    return out


async def _min_savings() -> float:
    return await ds.get_float("cost.opportunity_min_monthly_usd", 1.0)


# ── Opportunity pipeline ─────────────────────────────────────────────────


async def sync_opportunities(
    db: AsyncSession, book: PriceBook, sessions: list[SessionCost], outcomes: dict[str, str], now: datetime
) -> list[CostOpportunity]:
    """Upsert detector results from the trailing window and return all opportunities."""
    candidates = opportunities.detect_all(sessions, book, outcomes, await _min_savings())
    existing = {o.fingerprint: o for o in (await db.execute(select(CostOpportunity))).scalars().all()}
    seen: set[str] = set()
    for c in candidates:
        seen.add(c.fingerprint)
        try:
            agent_uuid = uuid.UUID(c.agent_id) if c.agent_id else None
        except ValueError:
            agent_uuid = None
        row = existing.get(c.fingerprint)
        if row is None:
            row = CostOpportunity(
                fingerprint=c.fingerprint,
                detector=c.detector,
                title=c.title,
                agent_id=agent_uuid,
                baseline_version=c.baseline_version or None,
                status=STATUS_IDENTIFIED,
                estimated_monthly_usd=round(c.savings, 4),
                evidence={**c.evidence, "window_days": DETECTION_WINDOW_DAYS},
                detected_at=now,
                last_seen_at=now,
                still_detected=True,
            )
            db.add(row)
            existing[c.fingerprint] = row
            continue
        row.last_seen_at = now
        row.still_detected = True
        if row.status == STATUS_IDENTIFIED:
            # Only open items track the latest data; approved items keep the
            # estimate that was approved.
            row.title = c.title
            row.estimated_monthly_usd = round(c.savings, 4)
            row.evidence = {**c.evidence, "window_days": DETECTION_WINDOW_DAYS}
            row.baseline_version = c.baseline_version or row.baseline_version
    for fingerprint, row in existing.items():
        if fingerprint not in seen and row.status == STATUS_IDENTIFIED:
            row.still_detected = False
    await auto_implement(db, list(existing.values()), sessions)
    await db.commit()
    return list(existing.values())


async def auto_implement(db: AsyncSession, rows: list[CostOpportunity], sessions: list[SessionCost]) -> None:
    """Mark approved opportunities implemented once a later approved version has sessions."""
    pending = [o for o in rows if o.status == STATUS_APPROVED and o.agent_id and o.approved_at]
    if not pending:
        return
    agent_ids = {o.agent_id for o in pending}
    versions = (
        await db.execute(
            select(AgentVersion.agent_id, AgentVersion.version, AgentVersion.released_at).where(
                AgentVersion.agent_id.in_(agent_ids), AgentVersion.status == AgentStatus.approved
            )
        )
    ).all()
    by_agent: dict[uuid.UUID, list] = defaultdict(list)
    for v in versions:
        by_agent[v.agent_id].append(v)
    used = {(s.agent_id, s.agent_version) for s in sessions}
    for opp in pending:
        released = sorted(
            (v for v in by_agent.get(opp.agent_id, []) if v.released_at and v.released_at > opp.approved_at),
            key=lambda v: v.released_at,
        )
        for v in released:
            if (str(opp.agent_id), v.version) in used:
                opp.status = STATUS_IMPLEMENTED
                opp.implemented_version = v.version
                opp.implemented_at = v.released_at
                break


async def approve(db: AsyncSession, opp: CostOpportunity, user_id: uuid.UUID, now: datetime) -> CostOpportunity:
    book = await load_price_book()
    start = now - timedelta(days=DETECTION_WINDOW_DAYS)
    sessions = await ledger.session_costs(start, now, book)
    opp.baseline = realized.baseline_from_sessions(
        sessions, str(opp.agent_id), opp.baseline_version or "", f"{start.date()}..{now.date()}"
    )
    opp.status = STATUS_APPROVED
    opp.approved_at = now
    opp.approved_by = user_id
    await db.commit()
    await invalidate_cache()
    return opp


def blocker_note(opps: list[CostOpportunity], names: dict[str, str]) -> str | None:
    """One line on what is stuck between stages, built from pipeline state."""
    approved = [o for o in opps if o.status == STATUS_APPROVED]
    identified = [o for o in opps if o.status == STATUS_IDENTIFIED and o.still_detected]
    if approved:
        amount = sum(float(o.estimated_monthly_usd) for o in approved)
        waiting: dict[str, int] = defaultdict(int)
        for o in approved:
            waiting[_label(str(o.agent_id) if o.agent_id else NO_AGENT, names)] += 1
        top = max(waiting, key=lambda a: waiting[a])
        return (
            f"${amount:,.0f}/mo approved but not live: waiting on a new release of {top}"
            + (f" and {len(waiting) - 1} other agent(s)" if len(waiting) > 1 else "")
            + "."
        )
    if identified:
        amount = sum(float(o.estimated_monthly_usd) for o in identified)
        return f"${amount:,.0f}/mo identified and awaiting review."
    return None


# ── Dashboard sections ───────────────────────────────────────────────────


def detect_anomalies(lines: list[CostLine], names: dict[str, str], w: Windows) -> list[dict]:
    """Days this month where an agent's spend broke far above its own trailing baseline."""
    daily: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))
    driver: dict[tuple[str, date], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for line in lines:
        if line.cost is None:
            continue
        daily[line.agent_id][line.day] += line.cost
        driver[(line.agent_id, line.day)][line.model] += line.cost
    flags = []
    for agent_id, series in daily.items():
        first_day = min(series)
        for day, spend in series.items():
            if day < w.month_start.date() or spend < ANOMALY_MIN_USD:
                continue
            # A new agent has no "usual day" yet; its first weeks are not spikes.
            history_days = min((day - first_day).days, ANOMALY_BASELINE_DAYS)
            if history_days < ANOMALY_MIN_HISTORY_DAYS:
                continue
            history = [series.get(day - timedelta(days=i), 0.0) for i in range(1, history_days + 1)]
            median = statistics.median(history)
            mad = statistics.median(abs(v - median) for v in history)
            scale = 1.4826 * mad if mad > 0 else max(median, 1.0) * 0.5
            z = (spend - median) / scale if scale else 0.0
            if z < ANOMALY_ROBUST_Z:
                continue
            models = driver[(agent_id, day)]
            flags.append(
                {
                    "date": day.isoformat(),
                    "agent": _label(agent_id, names),
                    "spend_usd": round(spend, 2),
                    "baseline_daily_usd": round(median, 2),
                    "multiple": round(spend / median, 1) if median > 0 else None,
                    "top_model": display_model(max(models, key=lambda m: models[m])) if models else None,
                }
            )
    return sorted(flags, key=lambda f: -f["spend_usd"])


def _ranked(values: dict[str, float], limit: int = 8) -> list[dict]:
    total = sum(values.values())
    items = sorted(values.items(), key=lambda kv: -kv[1])
    out = [
        {"label": k, "spend_usd": round(v, 2), "share_pct": round(100 * v / total, 1) if total else 0.0}
        for k, v in items[:limit]
    ]
    rest = sum(v for _, v in items[limit:])
    if rest > 0:
        out.append({"label": "Other", "spend_usd": round(rest, 2), "share_pct": round(100 * rest / total, 1)})
    return out


def _unit_economics(sessions: list[SessionCost], outcomes: dict[str, str]) -> dict:
    priced = [s for s in sessions if s.cost is not None]
    spend = sum(s.cost for s in priced)
    judged = [s for s in priced if outcomes.get(s.session_id) not in (None, "unclear")]
    successful = [s for s in judged if outcomes[s.session_id] in opportunities.SUCCESS_OUTCOMES]
    prs = sum(s.prs_created for s in priced)
    judged_spend = sum(s.cost for s in judged)
    return {
        "sessions": len(priced),
        "spend_usd": round(spend, 2),
        "cost_per_session_usd": _round(spend / len(priced) if priced else None, 4),
        "successful_sessions": len(successful),
        "judged_sessions": len(judged),
        "outcome_coverage_pct": round(100 * len(judged) / len(priced), 1) if priced else None,
        # Spend over the judged sessions divided by the successful ones, so
        # failed attempts are paid for by the successes they enabled.
        "cost_per_successful_session_usd": _round(judged_spend / len(successful) if successful else None, 4),
        "prs_created": prs,
        "cost_per_pr_usd": _round(spend / prs if prs else None, 4),
    }


def _insight(
    *,
    realized_rows: list[dict],
    open_rows: list[dict],
    anomalies: list[dict],
    drivers_now: dict[str, float],
    drivers_prev: dict[str, float],
    spend_now: float,
    spend_prev: float,
) -> dict | None:
    """Deterministically pick the single most material finding and phrase it.

    Numbers come from the computed figures; nothing here is generated by a model.
    """
    options: list[tuple[float, dict]] = []
    best_realized = max(
        (r for r in realized_rows if r["realized_mtd_usd"] > 0), key=lambda r: r["realized_mtd_usd"], default=None
    )
    if best_realized:
        options.append(
            (
                best_realized["realized_mtd_usd"] * 2,  # measured beats estimated at equal size
                {
                    "text": f"{best_realized['title']} on {best_realized['agent']} saved "
                    f"${best_realized['realized_mtd_usd']:,.2f} this month, measured across "
                    f"{best_realized['realized_sessions']} sessions.",
                    "subject": best_realized["agent"],
                    "evidence": "measured",
                },
            )
        )
    best_open = max(open_rows, key=lambda r: r["estimated_monthly_usd"], default=None)
    if best_open:
        ev = best_open["evidence"]
        support = ""
        if best_open["detector"] == opportunities.DETECTOR_MODEL_ROUTING:
            support = f"; it already runs {ev.get('target_sessions', 0)} sessions on {ev.get('to_model')}"
            if ev.get("target_success_pct") is not None:
                support += f" with {ev['target_success_pct']:.0f}% judged successful"
        options.append(
            (
                best_open["estimated_monthly_usd"],
                {
                    "text": f"{best_open['agent']}: {best_open['title'][0].lower()}{best_open['title'][1:]} "
                    f"could save about ${best_open['estimated_monthly_usd']:,.2f}/mo{support}.",
                    "subject": best_open["agent"],
                    "evidence": "estimated" if ev.get("validated", True) else "estimated, outcome parity not yet shown",
                },
            )
        )
    if anomalies:
        a = anomalies[0]
        options.append(
            (
                a["spend_usd"] - a["baseline_daily_usd"],
                {
                    "text": f"{a['agent']} spent ${a['spend_usd']:,.2f} on {_human_date(a['date'])}"
                    + (f", {a['multiple']}x its usual day" if a["multiple"] else "")
                    + (f", mostly on {a['top_model']}" if a["top_model"] else "")
                    + ".",
                    "subject": a["agent"],
                    "evidence": "measured",
                },
            )
        )
    if spend_prev > 0 and drivers_now:
        deltas = {k: drivers_now.get(k, 0.0) - drivers_prev.get(k, 0.0) for k in set(drivers_now) | set(drivers_prev)}
        top = max(deltas, key=lambda k: abs(deltas[k]))
        change = _pct_change(spend_now, spend_prev)
        if change is not None and abs(deltas[top]) > 0:
            direction = "up" if spend_now >= spend_prev else "down"
            options.append(
                (
                    abs(deltas[top]),
                    {
                        "text": f"Spend is {direction} {abs(change):.0f}% on the same point last month, "
                        f"driven mostly by {top} ({'+' if deltas[top] >= 0 else '-'}${abs(deltas[top]):,.2f}).",
                        "subject": top,
                        "evidence": "measured",
                    },
                )
            )
    if not options:
        return None
    return max(options, key=lambda o: o[0])[1]


def _human_date(iso_day: str) -> str:
    try:
        day = date.fromisoformat(iso_day)
    except ValueError:
        return iso_day
    return f"{day:%b} {day.day}"


def _opportunity_row(o: CostOpportunity, names: dict[str, str]) -> dict:
    return {
        "id": str(o.id),
        "detector": o.detector,
        "title": o.title,
        "agent": _label(str(o.agent_id) if o.agent_id else NO_AGENT, names),
        "agent_id": str(o.agent_id) if o.agent_id else None,
        "baseline_version": o.baseline_version,
        "status": o.status,
        "estimated_monthly_usd": round(float(o.estimated_monthly_usd or 0), 2),
        "still_detected": o.still_detected,
        "evidence": o.evidence or {},
        "baseline": o.baseline,
        "detected_at": o.detected_at.isoformat() if o.detected_at else None,
        "approved_at": o.approved_at.isoformat() if o.approved_at else None,
        "implemented_at": o.implemented_at.isoformat() if o.implemented_at else None,
        "implemented_version": o.implemented_version,
        "note": o.note,
    }


async def build_cost_intelligence(db: AsyncSession, now: datetime | None = None) -> dict:
    """The full dashboard payload. Raises CostDataUnavailableError if telemetry is unreadable."""
    w = windows_for(now or datetime.now(UTC))
    book = await load_price_book()

    lines = await ledger.daily_cost_lines(w.trend_start - timedelta(days=ANOMALY_BASELINE_DAYS), w.now, book)
    # The 30-day detection window always sits inside the six-month trend window.
    trend_sessions = await ledger.session_costs(min(w.trend_start, w.detection_start), w.now, book)
    earliest = await ledger.earliest_event()
    retention_days = await ds.get_int("data.retention_days", 0)
    detection_sessions = _sessions_between(trend_sessions, w.detection_start, w.now)
    all_ids = sorted({s.session_id for s in trend_sessions} | {s.session_id for s in detection_sessions})
    outcomes = await load_outcomes(db, all_ids)

    opps = await sync_opportunities(db, book, detection_sessions, outcomes, w.now)

    agent_ids = {line.agent_id for line in lines} | {str(o.agent_id) for o in opps if o.agent_id}
    names = await _agent_names(db, agent_ids)

    mtd = [ln for ln in lines if w.month_start.date() <= ln.day <= w.now.date()]
    # Same number of calendar days into the previous month (today counts as a day).
    prev_last_day = min(w.prev_mtd_end.date(), w.month_start.date() - timedelta(days=1))
    prev_mtd = [ln for ln in lines if w.prev_month_start.date() <= ln.day <= prev_last_day]
    spend_mtd = _total(mtd)
    spend_prev_mtd = _total(prev_mtd)
    coverage = ledger.coverage_of(mtd)

    # Pipeline totals are monthly run rates from the trailing 30-day window.
    live = [o for o in opps if o.status != STATUS_DISMISSED and (o.still_detected or o.status != STATUS_IDENTIFIED)]
    identified_rate = sum(float(o.estimated_monthly_usd) for o in live)
    approved_rate = sum(
        float(o.estimated_monthly_usd) for o in live if o.status in (STATUS_APPROVED, STATUS_IMPLEMENTED)
    )
    implemented_rate = sum(float(o.estimated_monthly_usd) for o in live if o.status == STATUS_IMPLEMENTED)

    # Realized savings per implemented opportunity, per month.
    month_starts = [add_months(w.month_start, -i) for i in range(TREND_MONTHS - 1, -1, -1)]
    implemented = [o for o in opps if o.status == STATUS_IMPLEMENTED and o.agent_id]
    realized_rows = []
    realized_by_month: dict[str, float] = defaultdict(float)
    for o in implemented:
        mtd_measure = realized.measure(
            _sessions_between(trend_sessions, w.month_start, w.now),
            agent_id=str(o.agent_id),
            baseline=o.baseline,
            implemented_version=o.implemented_version,
            implemented_at=o.implemented_at,
        )
        for ms in month_starts:
            me = min(add_months(ms, 1), w.now)
            m = realized.measure(
                _sessions_between(trend_sessions, ms, me),
                agent_id=str(o.agent_id),
                baseline=o.baseline,
                implemented_version=o.implemented_version,
                implemented_at=o.implemented_at,
            )
            if m.measurable:
                realized_by_month[ms.strftime("%Y-%m")] += m.amount
        realized_rows.append(
            {
                **_opportunity_row(o, names),
                "realized_mtd_usd": round(mtd_measure.amount, 2),
                "realized_sessions": mtd_measure.sessions,
                "realized_measurable": mtd_measure.measurable,
                "realized_reason": mtd_measure.reason,
            }
        )
    realized_mtd = sum(r["realized_mtd_usd"] for r in realized_rows if r["realized_measurable"])

    # Trend: spend, savings found by running the detectors on each month's own
    # usage, and realized savings measured in that month.
    min_savings = await _min_savings()
    trend = []
    for ms in month_starts:
        me = min(add_months(ms, 1), w.now)
        month_lines = [ln for ln in lines if ms.date() <= ln.day < add_months(ms, 1).date()]
        month_sessions = _sessions_between(trend_sessions, ms, me)
        found = sum(c.savings for c in opportunities.detect_all(month_sessions, book, outcomes, min_savings))
        if earliest is None or earliest >= add_months(ms, 1):
            data = "none"  # nothing stored for this month: unknown, not zero
        elif earliest > ms:
            data = "partial"
        else:
            data = "full"
        trend.append(
            {
                "month": ms.strftime("%Y-%m"),
                "partial": me < add_months(ms, 1),
                "data": data,
                "spend_usd": None if data == "none" else round(_total(month_lines), 2),
                "savings_found_usd": None if data == "none" else round(found, 2),
                "savings_actioned_usd": round(realized_by_month.get(ms.strftime("%Y-%m"), 0.0), 2),
                "sessions": len(month_sessions),
            }
        )

    # Token economics this month.
    usage = _usage_of(mtd)
    tokens = usage.total
    cache_saved = sum(ln.cache_savings for ln in mtd if ln.cost is not None)
    prev_usage = _usage_of(prev_mtd)
    prompt_tokens = usage.input + usage.cache_read + usage.cache_write
    token_tile = {
        "cache_savings_mtd_usd": round(cache_saved, 2),
        "cost_per_million_usd": _round(1e6 * spend_mtd / tokens if tokens else None, 4),
        "cost_per_million_without_cache_usd": _round(1e6 * (spend_mtd + cache_saved) / tokens if tokens else None, 4),
        "cost_per_million_prev_month_usd": _round(
            1e6 * spend_prev_mtd / prev_usage.total if prev_usage.total else None, 4
        ),
        "cache_hit_pct": _round(100 * usage.cache_read / prompt_tokens if prompt_tokens else None, 1),
        "tokens_mtd": int(tokens),
        "token_mix": {
            "input": int(usage.input),
            "output": int(usage.output),
            "cache_read": int(usage.cache_read),
            "cache_write": int(usage.cache_write),
        },
        "evidence": "measured",
        "note": "Savings from the coding tools' own prompt caching, priced against uncached list rates.",
    }

    # Context overhead (what each session carries before doing any work).
    mtd_sessions = _sessions_between(trend_sessions, w.month_start, w.now)
    contexts = [s.first_turn_context for s in mtd_sessions if s.first_turn_context > 0]
    context_cost = 0.0
    for ln in mtd:
        if ln.cost is None or ln.usage.cache_read <= 0:
            continue
        prices = book.prices_for(ln.raw_model)
        if prices:
            context_cost += ln.usage.cache_read * prices.cache_read
    context_opps = [o for o in live if o.detector == opportunities.DETECTOR_CONTEXT]
    context_tile = {
        "context_spend_mtd_usd": round(context_cost, 2),
        "context_share_pct": _round(100 * context_cost / spend_mtd if spend_mtd else None, 1),
        "median_first_turn_tokens": int(statistics.median(contexts)) if contexts else None,
        "sessions_measured": len(contexts),
        "pruning_opportunity_monthly_usd": round(sum(float(o.estimated_monthly_usd) for o in context_opps), 2),
        "agents_over_budget": len(context_opps),
        "evidence": "measured",
        "note": "Context re-read on every call (cache reads). Observal does not store agent memory, so stored-memory "
        "spend is measured as carried context instead.",
    }

    # Drivers.
    by_agent = ledger.sum_by(mtd, lambda ln: _label(ln.agent_id, names))
    by_agent_prev = ledger.sum_by(prev_mtd, lambda ln: _label(ln.agent_id, names))
    by_model = ledger.sum_by(mtd, lambda ln: ln.model)
    by_harness = ledger.sum_by(mtd, lambda ln: ln.harness or "unknown")

    anomalies = detect_anomalies(lines, names, w)
    open_rows = [_opportunity_row(o, names) for o in live if o.status == STATUS_IDENTIFIED and o.still_detected]

    unit_now = _unit_economics(mtd_sessions, outcomes)
    criteria = await criteria_coverage(db, {ln.agent_id for ln in mtd if ln.agent_id != NO_AGENT and ln.cost})
    unit_prev = _unit_economics(_sessions_between(trend_sessions, w.prev_month_start, w.prev_mtd_end), outcomes)

    pipeline = {
        "identified_monthly_usd": round(identified_rate, 2),
        "approved_monthly_usd": round(approved_rate, 2),
        "implemented_monthly_usd": round(implemented_rate, 2),
        # Cumulative like the amounts: approved includes implemented, identified includes both.
        "counts": {
            STATUS_IDENTIFIED: len(live),
            STATUS_APPROVED: sum(1 for o in live if o.status in (STATUS_APPROVED, STATUS_IMPLEMENTED)),
            STATUS_IMPLEMENTED: sum(1 for o in live if o.status == STATUS_IMPLEMENTED),
        },
        "blocker": blocker_note(live, names),
    }

    headline = {
        "spend_mtd_usd": round(spend_mtd, 2),
        "spend_prev_mtd_usd": round(spend_prev_mtd, 2),
        "spend_change_pct": _pct_change(spend_mtd, spend_prev_mtd),
        "savings_identified_monthly_usd": round(identified_rate, 2),
        "savings_identified_pct_of_spend": _round(100 * identified_rate / spend_mtd if spend_mtd else None, 1),
        "savings_actioned_monthly_usd": round(implemented_rate, 2),
        "savings_realized_mtd_usd": round(realized_mtd, 2),
        "realization_rate_pct": _round(100 * implemented_rate / identified_rate if identified_rate else None, 1),
    }

    return {
        "generated_at": w.now.isoformat(),
        "period": {
            "label": w.now.strftime("%B %Y") + " month to date",
            "month_start": w.month_start.isoformat(),
            "compare_start": w.prev_month_start.isoformat(),
            "compare_end": w.prev_mtd_end.isoformat(),
            "detection_window_days": DETECTION_WINDOW_DAYS,
            "earliest_telemetry": earliest.isoformat() if earliest else None,
            "retention_days": retention_days or None,
        },
        "headline": headline,
        "trend": trend,
        "tokens": token_tile,
        "insight": _insight(
            realized_rows=realized_rows,
            open_rows=open_rows,
            anomalies=anomalies,
            drivers_now=by_agent,
            drivers_prev=by_agent_prev,
            spend_now=spend_mtd,
            spend_prev=spend_prev_mtd,
        ),
        "context": context_tile,
        "drivers": {
            "by_agent": _ranked(by_agent),
            "by_model": _ranked(by_model, 6),
            "by_harness": _ranked(by_harness, 6),
        },
        "pipeline": pipeline,
        "opportunities": sorted([_opportunity_row(o, names) for o in live], key=lambda r: -r["estimated_monthly_usd"]),
        "realized": realized_rows,
        "unit_economics": {"current": unit_now, "previous": unit_prev, "criteria": criteria},
        "anomalies": anomalies[:5],
        "coverage": {
            "priced_token_pct": coverage.priced_token_pct,
            "unpriced_models": {
                k: int(v) for k, v in sorted(coverage.unpriced_models.items(), key=lambda kv: -kv[1])[:10]
            },
            "unpriced_credits": {k: round(v, 4) for k, v in coverage.unpriced_credits.items()},
            "price_sources": sorted(coverage.price_sources),
            "price_book_ref": price_book_ref(),
            "customized_pricing": book.config.is_customized,
            "reconciled": False,
            "basis": "Provider list prices (Langfuse-audited) unless overridden in Settings → Cost. Not reconciled to invoices.",
        },
    }


# ── Cache ────────────────────────────────────────────────────────────────


async def cached_cost_intelligence(db: AsyncSession, *, refresh: bool = False) -> dict:
    from services.redis import get_redis

    if not refresh:
        try:
            cached = await get_redis().get(CACHE_KEY)
            if cached:
                return json.loads(cached)
        except Exception:
            optic.debug("cost intelligence cache read skipped")
    payload = await build_cost_intelligence(db)
    try:
        await get_redis().set(CACHE_KEY, json.dumps(payload), ex=CACHE_TTL_SECONDS)
    except Exception:
        optic.debug("cost intelligence cache write skipped")
    return payload


async def invalidate_cache() -> None:
    from services.redis import get_redis

    try:
        await get_redis().delete(CACHE_KEY)
    except Exception:
        optic.debug("cost intelligence cache invalidate skipped")
