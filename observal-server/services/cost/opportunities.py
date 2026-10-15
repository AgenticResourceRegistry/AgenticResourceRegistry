# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Deterministic cost-opportunity detectors.

Each detector reads observed per-session usage and returns candidates whose
dollar value is a formula over that usage. No model is asked for a number.
Every candidate keeps its inputs in ``evidence`` so the estimate can be
recomputed and audited.

Detectors (savings are for the observed window; a 30-day window is a monthly
run rate):

model_routing
    An agent runs some sessions on a premium model while the same agent
    already succeeds on a cheaper model. Saving = premium usage repriced at
    the cheaper model's rates. Marked ``validated`` only when outcome data
    shows the cheaper model doing at least as well.

cache_efficiency
    An agent's prompt-cache hit rate is well below the deployment's own
    observed rate. Saving = the missing cache reads priced at the input minus
    cache-read price difference.

context_overhead
    An agent's first-turn context (system prompt, skills, tool definitions)
    is far larger than the deployment median. Saving = the excess tokens
    carried on every billable call, at the cache-read price.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field

from services.cost.ledger import NO_AGENT, SessionCost
from services.cost.pricing import PriceBook, TokenUsage, display_model, normalize_model

DETECTOR_MODEL_ROUTING = "model_routing"
DETECTOR_CACHE = "cache_efficiency"
DETECTOR_CONTEXT = "context_overhead"

# Minimum evidence before a detector speaks. Below these an estimate would be
# noise, so the detector stays silent rather than guessing.
MIN_SESSIONS_ON_CHEAPER_MODEL = 3
MIN_SESSIONS_FOR_CACHE = 5
MIN_SESSIONS_FOR_CONTEXT = 5
CACHE_GAP_POINTS = 10.0
CONTEXT_RATIO = 1.5
PARITY_TOLERANCE_POINTS = 5.0

SUCCESS_OUTCOMES = {"fully_achieved", "mostly_achieved"}


@dataclass(slots=True)
class Candidate:
    fingerprint: str
    detector: str
    title: str
    agent_id: str
    baseline_version: str
    savings: float
    evidence: dict = field(default_factory=dict)
    # "billed": reduces the bill (pay-per-token usage). "capacity": usage on a
    # subscription, where cheaper work frees plan capacity but the seat fee stays.
    billing_class: str = "billed"


def _current_version(sessions: list[SessionCost]) -> str:
    """The version the agent runs now (its latest session). A fix ships on top of it."""
    if not sessions:
        return ""
    return max(sessions, key=lambda s: s.started_at).agent_version


def _dominant_model(session: SessionCost) -> str:
    return max(session.by_model, key=lambda m: session.by_model[m].total) if session.by_model else ""


def _cost_per_session(sessions: list[SessionCost], model: str, book: PriceBook) -> float | None:
    """Mean cost of the sessions that ran mostly on ``model``."""
    costs = [s.cost for s in sessions if s.cost is not None and normalize_model(_dominant_model(s)) == model]
    return sum(costs) / len(costs) if len(costs) >= MIN_SESSIONS_ON_CHEAPER_MODEL else None


def _success_rate(sessions: list[SessionCost], outcomes: dict[str, str]) -> tuple[float | None, int]:
    judged = [outcomes[s.session_id] for s in sessions if outcomes.get(s.session_id) not in (None, "unclear")]
    if not judged:
        return None, 0
    return 100 * sum(o in SUCCESS_OUTCOMES for o in judged) / len(judged), len(judged)


def detect_model_routing(
    sessions: list[SessionCost], book: PriceBook, outcomes: dict[str, str] | None = None
) -> list[Candidate]:
    outcomes = outcomes or {}
    by_agent: dict[str, list[SessionCost]] = defaultdict(list)
    for s in sessions:
        if s.agent_id != NO_AGENT:
            by_agent[s.agent_id].append(s)

    out: list[Candidate] = []
    for agent_id, agent_sessions in by_agent.items():
        usage_by_model: dict[str, TokenUsage] = defaultdict(TokenUsage)
        sessions_by_model: dict[str, list[SessionCost]] = defaultdict(list)
        for s in agent_sessions:
            for raw, usage in s.by_model.items():
                model = normalize_model(raw)
                u = usage_by_model[model]
                usage_by_model[model] = TokenUsage(
                    u.input + usage.input,
                    u.output + usage.output,
                    u.cache_read + usage.cache_read,
                    u.cache_write + usage.cache_write,
                )
                sessions_by_model[model].append(s)
        priced = {m: book.prices_for(m) for m in usage_by_model}
        priced = {m: p for m, p in priced.items() if p is not None}
        # Cheaper models this agent already ran enough times to count as viable.
        viable = [m for m in priced if len(sessions_by_model[m]) >= MIN_SESSIONS_ON_CHEAPER_MODEL]
        for premium, premium_prices in priced.items():
            premium_usage = usage_by_model[premium]
            premium_cost = premium_usage.cost(premium_prices)
            cheaper = [m for m in viable if m != premium and premium_usage.cost(priced[m]) < premium_cost * 0.95]
            if not cheaper:
                continue
            target = min(cheaper, key=lambda m: (premium_usage.cost(priced[m]), -len(sessions_by_model[m])))
            repriced = premium_usage.cost(priced[target])
            # Repricing assumes the cheaper model uses the same tokens and turns.
            # Models differ (newer tokenizers alone add ~30%), so also compare what
            # the agent's sessions actually cost on each model, and take the more
            # conservative of the two.
            premium_cps = _cost_per_session(sessions_by_model[premium], premium, book)
            target_cps = _cost_per_session(sessions_by_model[target], target, book)
            observed = (
                (premium_cps - target_cps) * len(sessions_by_model[premium])
                if premium_cps is not None and target_cps is not None
                else None
            )
            savings = min(premium_cost - repriced, observed) if observed is not None else premium_cost - repriced
            if savings <= 0:
                continue
            premium_rate, premium_judged = _success_rate(sessions_by_model[premium], outcomes)
            target_rate, target_judged = _success_rate(sessions_by_model[target], outcomes)
            validated = (
                premium_rate is not None
                and target_rate is not None
                and target_rate >= premium_rate - PARITY_TOLERANCE_POINTS
            )
            out.append(
                Candidate(
                    fingerprint=f"{DETECTOR_MODEL_ROUTING}:{agent_id}:{premium}:{target}",
                    detector=DETECTOR_MODEL_ROUTING,
                    title=f"Route {display_model(premium)} work to {display_model(target)}",
                    agent_id=agent_id,
                    baseline_version=_current_version(agent_sessions),
                    savings=savings,
                    evidence={
                        "formula": "min(premium usage repriced at target rates, premium sessions x observed cost-per-session gap)",
                        "from_model": premium,
                        "to_model": target,
                        "premium_cost_usd": round(premium_cost, 4),
                        "repriced_cost_usd": round(repriced, 4),
                        "premium_cost_per_session_usd": None if premium_cps is None else round(premium_cps, 4),
                        "target_cost_per_session_usd": None if target_cps is None else round(target_cps, 4),
                        "estimate_basis": "observed"
                        if observed is not None and observed < premium_cost - repriced
                        else "repriced",
                        "premium_sessions": len(sessions_by_model[premium]),
                        "target_sessions": len(sessions_by_model[target]),
                        "premium_success_pct": None if premium_rate is None else round(premium_rate, 1),
                        "target_success_pct": None if target_rate is None else round(target_rate, 1),
                        "outcomes_judged": premium_judged + target_judged,
                        "validated": validated,
                    },
                )
            )
    return out


def _hit_rate(usage: TokenUsage) -> float | None:
    denominator = usage.input + usage.cache_read + usage.cache_write
    return 100 * usage.cache_read / denominator if denominator else None


def detect_cache_efficiency(sessions: list[SessionCost], book: PriceBook) -> list[Candidate]:
    total = TokenUsage()
    by_agent: dict[str, list[SessionCost]] = defaultdict(list)
    for s in sessions:
        total = total + s.usage
        if s.agent_id != NO_AGENT:
            by_agent[s.agent_id].append(s)
    deployment_rate = _hit_rate(total)
    if deployment_rate is None:
        return []
    # How many times, on average, a cache write is read back. Writing a prefix
    # costs a premium; that premium is spread over the reads it serves.
    reads_per_write = total.cache_read / total.cache_write if total.cache_write else 1.0

    out = []
    for agent_id, agent_sessions in by_agent.items():
        if len(agent_sessions) < MIN_SESSIONS_FOR_CACHE:
            continue
        session_rates = sorted(r for r in (_hit_rate(s.usage) for s in agent_sessions) if r is not None)
        if not session_rates:
            continue
        # Target what this agent's own better sessions already achieve, never
        # more than the deployment rate: short sessions cannot cache like long ones.
        own_best = session_rates[int(0.75 * (len(session_rates) - 1))]
        target = min(deployment_rate, own_best)
        savings = 0.0
        agent_usage = TokenUsage()
        for s in agent_sessions:
            for raw, usage in s.by_model.items():
                prices = book.prices_for(raw)
                if prices is None or prices.cache_read >= prices.input:
                    continue  # model without a cache discount: nothing to gain
                agent_usage = agent_usage + usage
                prompt_tokens = usage.input + usage.cache_read + usage.cache_write
                gained = min(max(target / 100 * prompt_tokens - usage.cache_read, 0.0), usage.input)
                write_premium = max(prices.cache_write - prices.input, 0.0) / max(reads_per_write, 1.0)
                savings += gained * (prices.input - prices.cache_read - write_premium)
        rate = _hit_rate(agent_usage)
        if rate is None or rate > target - CACHE_GAP_POINTS or savings <= 0:
            continue
        out.append(
            Candidate(
                fingerprint=f"{DETECTOR_CACHE}:{agent_id}",
                detector=DETECTOR_CACHE,
                title=f"Raise prompt-cache hit rate from {rate:.0f}% to {target:.0f}%",
                agent_id=agent_id,
                baseline_version=_current_version(agent_sessions),
                savings=savings,
                evidence={
                    "formula": "uncached input that could be cached x (input - cache_read price - write premium / reads per write)",
                    "agent_hit_pct": round(rate, 1),
                    "target_hit_pct": round(target, 1),
                    "agent_best_quartile_hit_pct": round(own_best, 1),
                    "deployment_hit_pct": round(deployment_rate, 1),
                    "reads_per_write": round(reads_per_write, 1),
                    "sessions": len(agent_sessions),
                },
            )
        )
    return out


def detect_context_overhead(sessions: list[SessionCost], book: PriceBook) -> list[Candidate]:
    by_agent: dict[str, list[SessionCost]] = defaultdict(list)
    for s in sessions:
        if s.agent_id != NO_AGENT and s.first_turn_context > 0:
            by_agent[s.agent_id].append(s)
    medians = {
        a: statistics.median(s.first_turn_context for s in ss)
        for a, ss in by_agent.items()
        if len(ss) >= MIN_SESSIONS_FOR_CONTEXT
    }
    if len(medians) < 2:
        return []  # a deployment median of one agent compares it with itself
    deployment_median = statistics.median(medians.values())

    out = []
    for agent_id, median in medians.items():
        if median < deployment_median * CONTEXT_RATIO:
            continue
        excess = median - deployment_median
        savings = 0.0
        for s in by_agent[agent_id]:
            if not s.by_model:
                continue
            raw = max(s.by_model, key=lambda m: s.by_model[m].total)
            prices = book.prices_for(raw)
            if prices is not None:
                savings += excess * max(s.calls, 1) * prices.cache_read
        if savings <= 0:
            continue
        out.append(
            Candidate(
                fingerprint=f"{DETECTOR_CONTEXT}:{agent_id}",
                detector=DETECTOR_CONTEXT,
                title=f"Trim fixed context from {median / 1000:.0f}K to ~{deployment_median / 1000:.0f}K tokens",
                agent_id=agent_id,
                baseline_version=_current_version(by_agent[agent_id]),
                savings=savings,
                evidence={
                    "formula": "(agent median first-turn context - deployment median) x billable calls x cache-read price",
                    "agent_median_context_tokens": round(median),
                    "deployment_median_context_tokens": round(deployment_median),
                    "sessions": len(by_agent[agent_id]),
                },
            )
        )
    return out


def detect_all(
    sessions: list[SessionCost], book: PriceBook, outcomes: dict[str, str] | None = None, min_savings: float = 0.0
) -> list[Candidate]:
    candidates = [
        *detect_model_routing(sessions, book, outcomes),
        *detect_cache_efficiency(sessions, book),
        *detect_context_overhead(sessions, book),
    ]
    return sorted((c for c in candidates if c.savings >= min_savings), key=lambda c: -c.savings)
