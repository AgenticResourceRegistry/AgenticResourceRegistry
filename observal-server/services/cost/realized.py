# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Measure realized savings for implemented opportunities.

Approval freezes a baseline: the per-session costs (and judged outcomes) of the
agent's sessions on the version the opportunity was detected on, over the 30
days before approval. Once a later version ships, its sessions are compared
with that baseline.

The comparison unit is cost per *successful* session when at least half of
both cohorts have a judged outcome, so a version that simply gets easier or
shorter tasks is not credited with a saving it did not cause. Otherwise it
falls back to cost per session and says so (``normalized=False``).

    realized = (baseline cost per unit - new cost per unit) x new units

A 90% bootstrap interval is reported with the central figure. Negative values
stay negative: a change that made work more expensive is a negative saving.
Below the minimum sample sizes nothing is claimed.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.cost.ledger import SessionCost

MIN_BASELINE_SESSIONS = 10
MIN_NEW_SESSIONS = 10
MIN_OUTCOME_COVERAGE = 0.5
MAX_BASELINE_SAMPLES = 500
BOOTSTRAP_ROUNDS = 1000
SUCCESS_OUTCOMES = {"fully_achieved", "mostly_achieved"}


def _judged(outcome: str | None) -> bool:
    return outcome is not None and outcome != "unclear"


def baseline_from_sessions(
    sessions: list[SessionCost], agent_id: str, version: str, window: str, outcomes: dict[str, str] | None = None
) -> dict:
    """Frozen reference sample for ``agent_id`` on ``version``."""
    outcomes = outcomes or {}
    rows = [s for s in sessions if s.agent_id == agent_id and s.agent_version == version and s.cost is not None]
    rows = rows[-MAX_BASELINE_SAMPLES:]
    costs = [round(float(s.cost), 6) for s in rows]
    return {
        "cost_per_session": sum(costs) / len(costs) if costs else None,
        "sessions": len(costs),
        "costs": costs,
        # Outcome per sampled session: 1 success, 0 judged failure, None not judged.
        "outcomes": [
            (1 if outcomes[s.session_id] in SUCCESS_OUTCOMES else 0) if _judged(outcomes.get(s.session_id)) else None
            for s in rows
        ],
        "window": window,
        "version": version,
    }


@dataclass(slots=True)
class Realized:
    amount: float
    sessions: int
    measurable: bool
    reason: str | None = None
    low: float | None = None
    high: float | None = None
    normalized: bool = False  # True: cost per successful session; False: cost per session


def _parse_ts(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace(" ", "T").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _cost_per_unit(costs: list[float], outcomes: list[int | None], normalized: bool) -> float | None:
    if not costs:
        return None
    if not normalized:
        return sum(costs) / len(costs)
    judged = [(c, o) for c, o in zip(costs, outcomes, strict=True) if o is not None]
    successes = sum(o for _, o in judged)
    return sum(c for c, _ in judged) / successes if successes else None


def _saving(base: tuple[list, list], new: tuple[list, list], normalized: bool) -> float | None:
    base_unit = _cost_per_unit(*base, normalized)
    new_unit = _cost_per_unit(*new, normalized)
    if base_unit is None or new_unit is None:
        return None
    new_costs, new_outcomes = new
    units = sum(o for o in new_outcomes if o is not None) if normalized else len(new_costs)
    return (base_unit - new_unit) * units


def measure(
    sessions: list[SessionCost],
    *,
    agent_id: str,
    baseline: dict | None,
    implemented_version: str | None,
    implemented_at: datetime | None,
    outcomes: dict[str, str] | None = None,
    with_interval: bool = True,
) -> Realized:
    outcomes = outcomes or {}
    if not baseline or not baseline.get("costs"):
        if baseline and baseline.get("cost_per_session") is not None and not baseline.get("costs"):
            return Realized(0.0, 0, False, "baseline was frozen before samples were recorded; re-approve to measure")
        return Realized(0.0, 0, False, "no baseline sessions on the original version")
    base_costs = [float(c) for c in baseline["costs"]]
    base_outcomes = list(baseline.get("outcomes") or [None] * len(base_costs))
    if len(base_costs) < MIN_BASELINE_SESSIONS:
        return Realized(0.0, 0, False, f"baseline has {len(base_costs)} sessions; needs {MIN_BASELINE_SESSIONS}")
    if not implemented_version:
        return Realized(0.0, 0, False, "not implemented yet")

    new_rows = []
    for s in sessions:
        if s.agent_id != agent_id or s.agent_version != implemented_version or s.cost is None:
            continue
        started = _parse_ts(s.started_at)
        if implemented_at and started and started < implemented_at:
            continue
        new_rows.append(s)
    if len(new_rows) < MIN_NEW_SESSIONS:
        return Realized(
            0.0,
            len(new_rows),
            False,
            f"{len(new_rows)} sessions on {implemented_version} so far; needs {MIN_NEW_SESSIONS}",
        )
    new_costs = [float(s.cost) for s in new_rows]
    new_outcomes = [
        (1 if outcomes[s.session_id] in SUCCESS_OUTCOMES else 0) if _judged(outcomes.get(s.session_id)) else None
        for s in new_rows
    ]

    def coverage(values: list[int | None]) -> float:
        return sum(v is not None for v in values) / len(values) if values else 0.0

    normalized = coverage(base_outcomes) >= MIN_OUTCOME_COVERAGE and coverage(new_outcomes) >= MIN_OUTCOME_COVERAGE
    central = _saving((base_costs, base_outcomes), (new_costs, new_outcomes), normalized)
    if central is None:
        normalized = False
        central = _saving((base_costs, base_outcomes), (new_costs, new_outcomes), False)

    if not with_interval:
        return Realized(float(central or 0.0), len(new_rows), True, None, None, None, normalized)
    # Deterministic bootstrap: resample both cohorts, recompute the saving.
    rng = random.Random(f"{agent_id}:{implemented_version}:{len(new_costs)}")
    draws = []
    base_pairs = list(zip(base_costs, base_outcomes, strict=True))
    new_pairs = list(zip(new_costs, new_outcomes, strict=True))
    for _ in range(BOOTSTRAP_ROUNDS):
        b = [base_pairs[rng.randrange(len(base_pairs))] for _ in base_pairs]
        n = [new_pairs[rng.randrange(len(new_pairs))] for _ in new_pairs]
        value = _saving(([c for c, _ in b], [o for _, o in b]), ([c for c, _ in n], [o for _, o in n]), normalized)
        if value is not None:
            draws.append(value)
    draws.sort()
    low = draws[int(0.05 * (len(draws) - 1))] if draws else None
    high = draws[int(0.95 * (len(draws) - 1))] if draws else None
    return Realized(float(central or 0.0), len(new_rows), True, None, low, high, normalized)
