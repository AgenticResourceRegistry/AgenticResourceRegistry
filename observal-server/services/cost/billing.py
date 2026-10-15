# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""How each harness is paid for, and what that means for billed spend.

Token usage priced at provider rates is *usage value*. It equals the bill only
for pay-per-token (metered) access: API keys, Bedrock, Vertex, Azure. A
subscription (Claude Pro/Max, Kiro plans) costs a flat seat fee; its token
usage is still worth knowing (it shows what the same work would cost
metered) but it is not money spent.

Sources, in order of precedence:

1. ``cost.billing`` (admin-configured), per harness::

       {"claude-code": {"mode": "subscription", "seats": 4, "seat_usd_month": 100},
        "kiro": {"mode": "subscription", "seats": 3, "seat_usd_month": 20,
                 "plan_credits": 1000, "overage_usd_per_credit": 0.04},
        "pi": {"mode": "metered"}}

2. Evidence in the telemetry: a Bedrock inference profile or Azure deployment in
   the model id means a cloud bill (metered); provider credits mean a Kiro plan.

3. Otherwise the harness is ``unconfigured``. Its usage value is reported, and
   counted in spend as an explicit, labelled assumption, never silently.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field

from loguru import logger as optic

MODE_METERED = "metered"
MODE_SUBSCRIPTION = "subscription"
MODE_UNCONFIGURED = "unconfigured"

SOURCE_CONFIGURED = "configured"
SOURCE_DETECTED = "detected"
SOURCE_NONE = "none"

_CLOUD_MODEL_RE = re.compile(
    r"^(?:(?:amazon-)?bedrock/|azure/|vertex(?:_ai)?/)|^(?:eu|us|global|jp|apac|au|ca|us-gov)\.anthropic\.", re.I
)


@dataclass(slots=True)
class HarnessBilling:
    mode: str = MODE_UNCONFIGURED
    seats: int = 0
    seat_usd_month: float = 0.0
    plan_credits: float = 0.0  # included credits per seat per month
    overage_usd_per_credit: float = 0.0


def parse(raw: str) -> dict[str, HarnessBilling]:
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        optic.warning("cost.billing is not valid JSON")
        return {}
    out = {}
    for harness, cfg in (data or {}).items():
        if not isinstance(cfg, dict):
            continue
        try:
            out[str(harness)] = HarnessBilling(
                mode=cfg.get("mode", MODE_UNCONFIGURED),
                seats=int(cfg.get("seats", 0) or 0),
                seat_usd_month=float(cfg.get("seat_usd_month", 0) or 0),
                plan_credits=float(cfg.get("plan_credits", 0) or 0),
                overage_usd_per_credit=float(cfg.get("overage_usd_per_credit", 0) or 0),
            )
        except (TypeError, ValueError):
            optic.warning("ignoring invalid billing config for harness={}", harness)
    return out


def validate(raw: str) -> None:
    """Raise ValueError with a user-facing message if ``cost.billing`` is invalid."""
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("cost.billing must be a JSON object keyed by harness")
    for harness, cfg in data.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"Billing for {harness} must be an object")
        if cfg.get("mode") not in (MODE_METERED, MODE_SUBSCRIPTION):
            raise ValueError(f"Billing mode for {harness} must be 'metered' or 'subscription'")
        for key in ("seats", "seat_usd_month", "plan_credits", "overage_usd_per_credit"):
            value = cfg.get(key, 0)
            if not isinstance(value, int | float) or value < 0:
                raise ValueError(f"{harness}.{key} must be a non-negative number")
        unknown = set(cfg) - {"mode", "seats", "seat_usd_month", "plan_credits", "overage_usd_per_credit"}
        if unknown:
            raise ValueError(f"Unknown billing field(s) for {harness}: {', '.join(sorted(unknown))}")


@dataclass(slots=True)
class HarnessSpend:
    harness: str
    mode: str
    source: str
    evidence: str
    usage_value_usd: float = 0.0  # tokens at provider rates
    metered_usd: float = 0.0  # pay-per-token money spent
    subscription_usd: float = 0.0  # seat fees for the month
    overage_usd: float = 0.0  # credits beyond the plan
    seats: int = 0
    credits: float = 0.0

    @property
    def billed_usd(self) -> float:
        return self.metered_usd + self.subscription_usd + self.overage_usd


@dataclass(slots=True)
class BillingSummary:
    harnesses: list[HarnessSpend] = field(default_factory=list)

    @property
    def billed_usd(self) -> float:
        return sum(h.billed_usd for h in self.harnesses)

    @property
    def usage_value_usd(self) -> float:
        return sum(h.usage_value_usd for h in self.harnesses)

    @property
    def unclassified_usd(self) -> float:
        return sum(h.usage_value_usd for h in self.harnesses if h.mode == MODE_UNCONFIGURED)

    @property
    def complete(self) -> bool:
        return all(h.mode != MODE_UNCONFIGURED for h in self.harnesses)


def classify(
    harness: str, raw_models: set[str], credits: float, config: dict[str, HarnessBilling]
) -> tuple[str, str, str]:
    """(mode, source, evidence) for one harness."""
    cfg = config.get(harness)
    if cfg and cfg.mode in (MODE_METERED, MODE_SUBSCRIPTION):
        return cfg.mode, SOURCE_CONFIGURED, "Set in Settings -> Cost"
    models = {m for m in raw_models if m}
    if models and all(_CLOUD_MODEL_RE.search(m) for m in models):
        return MODE_METERED, SOURCE_DETECTED, "Cloud provider endpoint in model id (Bedrock, Vertex or Azure)"
    if credits > 0 and not models:
        return MODE_SUBSCRIPTION, SOURCE_DETECTED, "Usage reported in plan credits"
    return MODE_UNCONFIGURED, SOURCE_NONE, "No billing signal in telemetry; set the billing mode in Settings -> Cost"


def summarize(
    usage_by_harness: dict[str, float],
    models_by_harness: dict[str, set[str]],
    credits_by_user: dict[tuple[str, str], float],
    config: dict[str, HarnessBilling],
    include_seats: bool = True,
) -> BillingSummary:
    """Billed spend per harness for one period.

    ``credits_by_user`` maps (harness, user) to credits used in the period;
    overage is each user's credits beyond the plan allowance.
    """
    harnesses = set(usage_by_harness) | {h for h, _ in credits_by_user} | set(config)
    credits_total: dict[str, float] = defaultdict(float)
    for (h, _user), credits in credits_by_user.items():
        credits_total[h] += credits
    out = BillingSummary()
    for harness in sorted(harnesses):
        mode, source, evidence = classify(
            harness, models_by_harness.get(harness, set()), credits_total.get(harness, 0.0), config
        )
        cfg = config.get(harness, HarnessBilling())
        spend = HarnessSpend(
            harness=harness,
            mode=mode,
            source=source,
            evidence=evidence,
            usage_value_usd=usage_by_harness.get(harness, 0.0),
            credits=credits_total.get(harness, 0.0),
            seats=cfg.seats,
        )
        if mode == MODE_METERED:
            spend.metered_usd = spend.usage_value_usd
        elif mode == MODE_SUBSCRIPTION:
            spend.subscription_usd = cfg.seats * cfg.seat_usd_month if include_seats else 0.0
            if cfg.plan_credits > 0 and cfg.overage_usd_per_credit > 0:
                spend.overage_usd = sum(
                    max(credits - cfg.plan_credits, 0.0) * cfg.overage_usd_per_credit
                    for (h, _user), credits in credits_by_user.items()
                    if h == harness
                )
        else:
            # Unconfigured: counted as metered so spend is not understated, and
            # surfaced as an assumption the admin should confirm. Credits priced
            # with cost.credit_usd are already inside the usage value.
            spend.metered_usd = spend.usage_value_usd
        if harness not in usage_by_harness and spend.billed_usd == 0 and mode != MODE_SUBSCRIPTION:
            continue
        out.harnesses.append(spend)
    return out
