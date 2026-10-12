# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Measure realized savings for implemented opportunities.

When an opportunity is approved we freeze the baseline: the agent's cost per
session on the version it was detected on, over the 30 days before approval.
Once a later version ships, every session on that version is compared with
the baseline:

    realized = sum (baseline cost per session - actual session cost)

Negative values stay negative: a change that made sessions more expensive
shows as negative savings. This compares cost only; outcome parity is
reported separately in the opportunity's evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.cost.ledger import SessionCost

MIN_BASELINE_SESSIONS = 3


def baseline_from_sessions(sessions: list[SessionCost], agent_id: str, version: str, window: str) -> dict:
    """Cost-per-session reference for ``agent_id`` on ``version``."""
    costs = [s.cost for s in sessions if s.agent_id == agent_id and s.agent_version == version and s.cost is not None]
    if not costs:
        return {"cost_per_session": None, "sessions": 0, "window": window, "version": version}
    return {
        "cost_per_session": sum(costs) / len(costs),
        "sessions": len(costs),
        "window": window,
        "version": version,
    }


@dataclass(slots=True)
class Realized:
    amount: float
    sessions: int
    measurable: bool
    reason: str | None = None


def _parse_ts(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace(" ", "T").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def measure(
    sessions: list[SessionCost],
    *,
    agent_id: str,
    baseline: dict | None,
    implemented_version: str | None,
    implemented_at: datetime | None,
) -> Realized:
    if not baseline or baseline.get("cost_per_session") is None:
        return Realized(0.0, 0, False, "no baseline sessions on the original version")
    if int(baseline.get("sessions") or 0) < MIN_BASELINE_SESSIONS:
        return Realized(0.0, 0, False, f"baseline has fewer than {MIN_BASELINE_SESSIONS} sessions")
    if not implemented_version:
        return Realized(0.0, 0, False, "not implemented yet")
    reference = float(baseline["cost_per_session"])
    amount = 0.0
    counted = 0
    for s in sessions:
        if s.agent_id != agent_id or s.agent_version != implemented_version or s.cost is None:
            continue
        started = _parse_ts(s.started_at)
        if implemented_at and started and started < implemented_at:
            continue
        amount += reference - s.cost
        counted += 1
    if not counted:
        return Realized(0.0, 0, False, f"no sessions on {implemented_version} in this period yet")
    return Realized(amount, counted, True)
