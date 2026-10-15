# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Cost intelligence: pricing, ledger, detectors, realized savings, and the dashboard payload."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.deps import get_current_user, get_db
from api.routes import cost_intelligence as routes
from models.cost_opportunity import STATUS_APPROVED, STATUS_IDENTIFIED, STATUS_IMPLEMENTED, CostOpportunity
from models.user import UserRole
from services.cost import ledger, opportunities, realized, service
from services.cost.ledger import CostLine, SessionCost
from services.cost.pricing import (
    PRICE_SOURCE_CREDIT,
    PRICE_SOURCE_OVERRIDE,
    PriceBook,
    PricingConfig,
    TokenUsage,
    normalize_model,
    validate_setting,
)

AGENT = str(uuid.UUID("11111111-1111-1111-1111-111111111111"))
OTHER = str(uuid.UUID("22222222-2222-2222-2222-222222222222"))


def book(**kw) -> PriceBook:
    return PriceBook(PricingConfig.parse(kw.get("overrides", ""), kw.get("multipliers", ""), kw.get("credits", "")))


def session(
    sid: str,
    model: str,
    usage: TokenUsage,
    *,
    agent: str = AGENT,
    version: str = "1.0.0",
    started: str = "2026-10-05 10:00:00",
    context: float = 0.0,
    calls: int = 1,
    cost: float | None = None,
    prs: int = 0,
) -> SessionCost:
    prices = PriceBook().prices_for(model)
    return SessionCost(
        session_id=sid,
        harness="claude-code",
        agent_id=agent,
        agent_version=version,
        user_id="u1",
        started_at=started,
        cost=usage.cost(prices) if cost is None and prices else cost,
        usage=usage,
        first_turn_context=context,
        first_turn_cost=None,
        prs_created=prs,
        tool_errors=0,
        by_model={model: usage},
        calls=calls,
    )


# ── Pricing ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("eu.anthropic.claude-opus-4-6-v1", "claude-opus-4-6"),
        ("global.anthropic.claude-sonnet-4-6", "claude-sonnet-4-6"),
        ("amazon-bedrock/anthropic.claude-sonnet-4-6", "claude-sonnet-4-6"),
        ("claude-sonnet-4-5@20250929", "claude-sonnet-4-5"),
        ("{'providerID': 'anthropic', 'modelID': 'claude-sonnet-4-6'}", "claude-sonnet-4-6"),
        ("claude-opus-4-7", "claude-opus-4-7"),
        ("", ""),
    ],
)
def test_normalize_model_unifies_harness_spellings(raw, expected):
    assert normalize_model(raw) == expected


def test_list_price_matches_provider_reported_cost():
    # Pi records the provider's own USD per call; the price book must agree with it.
    prices = PriceBook().prices_for("gpt-5.5")
    usage = TokenUsage(input=1_019_259, output=41_796, cache_read=9_069_056)
    assert usage.cost(prices) == pytest.approx(10.88, abs=0.01)


def test_regional_bedrock_profiles_carry_the_published_ten_percent_premium():
    b = PriceBook()
    assert b.prices_for("global.anthropic.claude-opus-4-6-v1").input == pytest.approx(5e-6)
    assert b.prices_for("eu.anthropic.claude-opus-4-6-v1").input == pytest.approx(5.5e-6)
    # Models before Claude 4.5 keep their original pricing; a date suffix is not a minor version.
    assert b.prices_for("us.anthropic.claude-sonnet-4-20250514-v1:0").input == pytest.approx(3e-6)


def test_price_corrections_override_upstream_errors():
    p = PriceBook().prices_for("claude-sonnet-5-5")
    assert p.cache_read == pytest.approx(1e-7)
    assert "corrected" in p.adjustments


def test_one_hour_cache_writes_are_priced_at_their_own_rate():
    p = PriceBook().prices_for("claude-opus-4-6")
    five_min = TokenUsage(cache_write=1_000_000)
    one_hour = TokenUsage(cache_write=1_000_000, cache_write_1h=1_000_000)
    assert five_min.cost(p) == pytest.approx(6.25)
    assert one_hour.cost(p) == pytest.approx(10.0)


def test_cache_savings_subtract_the_write_premium():
    p = PriceBook().prices_for("claude-opus-4-6")
    usage = TokenUsage(cache_read=1_000_000, cache_write=1_000_000, cache_write_1h=1_000_000)
    # reads save 4.50/M; one-hour writes cost 5.00/M more than plain input
    assert usage.cache_savings(p) == pytest.approx(4.5 - 5.0)


def test_override_beats_list_price_and_multiplier_scales_it():
    b = book(
        overrides='{"claude-opus-4-6": {"input": 4, "output": 20, "cache_read": 0.4, "cache_write": 5}}',
        multipliers='{"^eu\\\\.anthropic\\\\.": 1.1}',
    )
    eu = b.prices_for("eu.anthropic.claude-opus-4-6-v1")
    plain = b.prices_for("claude-opus-4-6")
    assert plain.source == PRICE_SOURCE_OVERRIDE
    assert plain.input == pytest.approx(4e-6)
    # regional premium x1.1, then the configured multiplier x1.1
    assert eu.input == pytest.approx(4e-6 * 1.1 * 1.1)


def test_placeholder_and_unknown_models_are_unpriced_not_free():
    b = PriceBook()
    assert b.prices_for("<synthetic>") is None
    assert b.prices_for("Kiro Auto") is None


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("cost.price_overrides", "[1]", "JSON object"),
        ("cost.price_overrides", '{"m": {"inputs": 1}}', "Unknown price bucket"),
        ("cost.price_overrides", '{"m": {"input": -1}}', "non-negative"),
        ("cost.price_multipliers", '{"(": 1.1}', "Invalid regex"),
        ("cost.price_multipliers", '{"^eu": 0}', "positive"),
        ("cost.credit_usd", '{"kiro": "a"}', "positive"),
        ("cost.opportunity_min_monthly_usd", "-2", "negative"),
    ],
)
def test_validate_setting_rejects_bad_input(key, value, message):
    with pytest.raises(ValueError, match=message):
        validate_setting(key, value)


def test_validate_setting_accepts_good_input():
    assert validate_setting("cost.credit_usd", '{"kiro": 0.04}') == '{"kiro": 0.04}'
    assert validate_setting("cost.price_overrides", "") == ""


# ── Ledger ───────────────────────────────────────────────────────────────


def test_credits_stay_unpriced_without_a_rate_and_price_with_one():
    row = {"day": "2026-10-02", "harness": "kiro", "model": "Kiro Auto", "credits": 2.5}
    unpriced = ledger.price_line(row, book())
    priced = ledger.price_line(row, book(credits='{"kiro": 0.04}'))
    assert unpriced.cost is None
    assert priced.cost == pytest.approx(0.1)
    assert priced.price_source == PRICE_SOURCE_CREDIT


def test_coverage_reports_unpriced_usage_instead_of_counting_it_as_zero():
    lines = [
        ledger.price_line(
            {"day": "2026-10-02", "harness": "claude-code", "model": "claude-sonnet-4-6", "output": 1000}, book()
        ),
        ledger.price_line(
            {"day": "2026-10-02", "harness": "claude-code", "model": "mystery-model", "output": 500}, book()
        ),
        ledger.price_line({"day": "2026-10-02", "harness": "kiro", "model": "", "credits": 1.5}, book()),
    ]
    cov = ledger.coverage_of(lines)
    assert cov.priced_cost == pytest.approx(0.015)
    assert cov.unpriced_models == {"mystery-model": 500}
    assert cov.unpriced_credits == {"kiro": 1.5}
    assert cov.priced_token_pct == pytest.approx(66.7)


@pytest.mark.asyncio
async def test_ch_rows_raises_instead_of_returning_empty(monkeypatch):
    import services.clickhouse as ch

    monkeypatch.setattr(ch, "_query", AsyncMock(return_value=SimpleNamespace(status_code=500, json=dict)))
    with pytest.raises(ledger.CostDataUnavailableError):
        await ledger.ch_rows("SELECT 1")


# ── Detectors ────────────────────────────────────────────────────────────


def _routing_sessions():
    opus = TokenUsage(input=1_000, output=200_000, cache_read=5_000_000, cache_write=100_000)
    sonnet = TokenUsage(input=1_000, output=100_000, cache_read=2_000_000, cache_write=50_000)
    return [
        *[session(f"o{i}", "claude-opus-4-6", opus) for i in range(4)],
        *[session(f"s{i}", "claude-sonnet-4-6", sonnet) for i in range(3)],
    ]


def test_model_routing_reprices_premium_usage_at_the_cheaper_model():
    sessions = _routing_sessions()
    [candidate] = opportunities.detect_model_routing(sessions, PriceBook())
    opus_usage = TokenUsage(input=4_000, output=800_000, cache_read=20_000_000, cache_write=400_000)
    b = PriceBook()
    expected = opus_usage.cost(b.prices_for("claude-opus-4-6")) - opus_usage.cost(b.prices_for("claude-sonnet-4-6"))
    assert candidate.savings == pytest.approx(expected)
    assert candidate.evidence["from_model"] == "claude-opus-4-6"
    assert candidate.evidence["to_model"] == "claude-sonnet-4-6"
    assert candidate.evidence["validated"] is False  # no outcome data yet


def test_model_routing_is_validated_only_with_outcome_parity():
    sessions = _routing_sessions()
    good = {s.session_id: "fully_achieved" for s in sessions}
    worse = {**good, **{f"s{i}": "not_achieved" for i in range(3)}}
    assert opportunities.detect_model_routing(sessions, PriceBook(), good)[0].evidence["validated"] is True
    assert opportunities.detect_model_routing(sessions, PriceBook(), worse)[0].evidence["validated"] is False


def test_model_routing_needs_the_agent_to_already_use_the_cheaper_model():
    sessions = _routing_sessions()[:6]  # only two sonnet sessions
    assert opportunities.detect_model_routing(sessions, PriceBook()) == []


def test_unattributed_sessions_never_produce_agent_opportunities():
    sessions = [s for s in _routing_sessions()]
    for s in sessions:
        s.agent_id = ledger.NO_AGENT
    assert opportunities.detect_all(sessions, PriceBook()) == []


def test_cache_efficiency_targets_the_deployments_own_hit_rate():
    cached = TokenUsage(input=10_000, output=1_000, cache_read=990_000, cache_write=10_000)
    uncached = TokenUsage(input=500_000, output=1_000, cache_read=500_000)
    best = TokenUsage(input=100_000, output=1_000, cache_read=900_000)
    sessions = [
        *[session(f"good{i}", "claude-sonnet-4-6", cached, agent=OTHER) for i in range(10)],
        *[session(f"bad{i}", "claude-sonnet-4-6", uncached) for i in range(3)],
        *[session(f"best{i}", "claude-sonnet-4-6", best) for i in range(3)],
    ]
    [candidate] = opportunities.detect_cache_efficiency(sessions, PriceBook())
    assert candidate.agent_id == AGENT
    # Target never exceeds what the agent's own better sessions reach, nor the deployment rate.
    assert candidate.evidence["agent_best_quartile_hit_pct"] == pytest.approx(90.0)
    assert candidate.evidence["target_hit_pct"] == min(90.0, candidate.evidence["deployment_hit_pct"])
    assert candidate.savings > 0


def test_cache_detector_stays_silent_when_the_agent_cannot_do_better():
    cached = TokenUsage(input=10_000, output=1_000, cache_read=990_000)
    uniform = TokenUsage(input=500_000, output=1_000, cache_read=500_000)
    sessions = [
        *[session(f"good{i}", "claude-sonnet-4-6", cached, agent=OTHER) for i in range(10)],
        *[session(f"bad{i}", "claude-sonnet-4-6", uniform) for i in range(6)],
    ]
    assert opportunities.detect_cache_efficiency(sessions, PriceBook()) == []


def test_context_overhead_flags_agents_far_above_the_median():
    u = TokenUsage(input=1_000, output=1_000, cache_read=100_000)
    third = str(uuid.uuid4())
    sessions = [
        *[session(f"a{i}", "claude-sonnet-4-6", u, agent=AGENT, context=90_000, calls=10) for i in range(5)],
        *[session(f"b{i}", "claude-sonnet-4-6", u, agent=OTHER, context=20_000, calls=10) for i in range(5)],
        *[session(f"c{i}", "claude-sonnet-4-6", u, agent=third, context=22_000, calls=10) for i in range(5)],
    ]
    [candidate] = opportunities.detect_context_overhead(sessions, PriceBook())
    assert candidate.agent_id == AGENT
    excess = 90_000 - 22_000
    assert candidate.savings == pytest.approx(5 * excess * 10 * PriceBook().prices_for("claude-sonnet-4-6").cache_read)


# ── Realized savings ─────────────────────────────────────────────────────


def _baseline(costs, outcomes=None):
    return {
        "cost_per_session": sum(costs) / len(costs),
        "sessions": len(costs),
        "costs": costs,
        "outcomes": outcomes or [None] * len(costs),
        "version": "1.0.0",
    }


def test_realized_savings_compare_new_version_sessions_with_frozen_baseline():
    after = [session(f"n{i}", "claude-sonnet-4-6", TokenUsage(), version="1.1.0", cost=1.0) for i in range(12)]
    result = realized.measure(
        after, agent_id=AGENT, baseline=_baseline([3.0] * 12), implemented_version="1.1.0", implemented_at=None
    )
    assert result.measurable
    assert result.amount == pytest.approx(24.0)
    assert result.low == pytest.approx(24.0) and result.high == pytest.approx(24.0)
    assert result.normalized is False


def test_realized_savings_use_cost_per_success_when_outcomes_allow():
    after = [session(f"n{i}", "claude-sonnet-4-6", TokenUsage(), version="1.1.0", cost=1.0) for i in range(10)]
    # New version: cheaper sessions but only half succeed; baseline: all succeed at 1.5.
    outcomes = {f"n{i}": ("fully_achieved" if i % 2 else "not_achieved") for i in range(10)}
    result = realized.measure(
        after,
        agent_id=AGENT,
        baseline=_baseline([1.5] * 10, [1] * 10),
        implemented_version="1.1.0",
        implemented_at=None,
        outcomes=outcomes,
    )
    assert result.normalized is True
    # 1.5 per success before vs 2.0 per success after, over 5 successes: a regression.
    assert result.amount == pytest.approx(-2.5)


def test_realized_savings_keep_regressions_negative():
    after = [session(f"n{i}", "claude-sonnet-4-6", TokenUsage(), version="1.1.0", cost=5.0) for i in range(10)]
    result = realized.measure(
        after, agent_id=AGENT, baseline=_baseline([3.0] * 10), implemented_version="1.1.0", implemented_at=None
    )
    assert result.amount == pytest.approx(-20.0)


def test_realized_savings_refuse_thin_samples():
    thin_base = realized.measure(
        [], agent_id=AGENT, baseline=_baseline([3.0] * 5), implemented_version="1.1.0", implemented_at=None
    )
    assert not thin_base.measurable and "needs 10" in thin_base.reason
    after = [session("n", "claude-sonnet-4-6", TokenUsage(), version="1.1.0", cost=1.0)]
    thin_new = realized.measure(
        after, agent_id=AGENT, baseline=_baseline([3.0] * 10), implemented_version="1.1.0", implemented_at=None
    )
    assert not thin_new.measurable and "1 sessions" in thin_new.reason


# ── Service helpers ──────────────────────────────────────────────────────


def test_previous_month_window_clamps_short_months():
    w = service.windows_for(datetime(2026, 3, 31, 12, tzinfo=UTC))
    assert w.prev_month_start == datetime(2026, 2, 1, tzinfo=UTC)
    assert w.prev_mtd_end == datetime(2026, 3, 1, tzinfo=UTC)
    assert w.trend_start == datetime(2025, 10, 1, tzinfo=UTC)


def _line(day: date, cost: float, agent: str = AGENT, model: str = "claude-opus-4-6") -> CostLine:
    return CostLine(
        day=day,
        harness="pi",
        raw_model=model,
        model=model,
        agent_id=agent,
        agent_version="1.0.0",
        sessions=1,
        usage=TokenUsage(),
        credits=0,
        cost=cost,
        cache_savings=0,
        price_source="list_price",
    )


def test_anomaly_flags_a_day_far_above_the_agents_own_baseline():
    w = service.windows_for(datetime(2026, 10, 20, tzinfo=UTC))
    lines = [_line(date(2026, 10, 20) - timedelta(days=i), 2.0 + (i % 3) * 0.1) for i in range(1, 61)]
    lines.append(_line(date(2026, 10, 15), 40.0))
    flags = service.detect_anomalies(lines, {AGENT: "ultra-pi"}, w)
    assert flags[0]["date"] == "2026-10-15"
    assert flags[0]["agent"] == "ultra-pi"


def test_a_new_agent_is_not_a_spike():
    w = service.windows_for(datetime(2026, 10, 20, tzinfo=UTC))
    lines = [_line(date(2026, 10, 12), 1.0), _line(date(2026, 10, 15), 40.0)]
    assert service.detect_anomalies(lines, {}, w) == []


def test_display_model_reads_like_a_product_name():
    from services.cost.pricing import display_model

    assert display_model("eu.anthropic.claude-opus-4-6-v1") == "Claude Opus 4.6"
    assert display_model("claude-sonnet-5") == "Claude Sonnet 5"
    assert display_model("claude-haiku-4-5-20251001") == "Claude Haiku 4.5"
    assert display_model("gpt-5.5") == "GPT-5.5"


def test_steady_spend_raises_no_anomaly():
    w = service.windows_for(datetime(2026, 10, 20, tzinfo=UTC))
    lines = [_line(date(2026, 10, 20) - timedelta(days=i), 10.0) for i in range(0, 61)]
    assert service.detect_anomalies(lines, {}, w) == []


def test_insight_prefers_measured_savings_over_an_equal_estimate():
    insight = service._insight(
        realized_rows=[
            {"title": "Route opus to sonnet", "agent": "a", "realized_mtd_usd": 50.0, "realized_sessions": 9}
        ],
        open_rows=[
            {
                "title": "Route x to y",
                "agent": "b",
                "estimated_monthly_usd": 60.0,
                "detector": "model_routing",
                "evidence": {},
            }
        ],
        anomalies=[],
        drivers_now={},
        drivers_prev={},
        spend_now=0,
        spend_prev=0,
    )
    assert insight["evidence"] == "measured"
    assert "$50.00" in insight["text"]


def test_blocker_note_names_the_agent_waiting_on_release():
    opp = CostOpportunity(
        status=STATUS_APPROVED, estimated_monthly_usd=13, agent_id=uuid.UUID(AGENT), still_detected=True
    )
    assert service.blocker_note([opp], {AGENT: "Test-Writing Agent"}) == (
        "$13/mo approved but not live: waiting on a new release of Test-Writing Agent."
    )


# ── Full payload ─────────────────────────────────────────────────────────


class _Scalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _Result:
    def __init__(self, rows=(), scalars=()):
        self._rows = list(rows)
        self._scalars = list(scalars)

    def all(self):
        return self._rows

    def scalars(self):
        return _Scalars(self._scalars)


def _config(**kw):
    from services.cost import billing as billing_mod

    return service.CostConfig(
        book=PriceBook(),
        billing=billing_mod.parse(kw.get("billing", "")),
        tz=kw.get("tz", "UTC"),
        min_savings=1.0,
    )


@pytest.mark.asyncio
async def test_build_cost_intelligence_assembles_every_layer(monkeypatch):
    now = datetime(2026, 10, 20, 12, tzinfo=UTC)
    lines = [
        ledger.price_line(
            {
                "day": "2026-10-03",
                "harness": "pi",
                "model": "eu.anthropic.claude-opus-4-6-v1",
                "agent_id": AGENT,
                "agent_version": "1.0.0",
                "sessions": 4,
                "output": 800_000,
                "cache_read": 20_000_000,
            },
            PriceBook(),
        ),
        ledger.price_line(
            {
                "day": "2026-10-03",
                "harness": "claude-code",
                "model": "claude-sonnet-4-6",
                "agent_id": "",
                "sessions": 2,
                "output": 100_000,
            },
            PriceBook(),
        ),
        ledger.price_line(
            {
                "day": "2026-09-05",
                "harness": "pi",
                "model": "claude-sonnet-4-6",
                "agent_id": AGENT,
                "agent_version": "1.0.0",
                "sessions": 3,
                "output": 300_000,
                "cache_read": 6_000_000,
            },
            PriceBook(),
        ),
        ledger.price_line({"day": "2026-10-04", "harness": "kiro", "model": "", "credits": 3}, PriceBook()),
    ]
    sessions = [
        *[
            session(
                f"o{i}",
                "eu.anthropic.claude-opus-4-6-v1",
                TokenUsage(output=200_000, cache_read=5_000_000),
                started="2026-10-03 10:00:00",
                prs=1,
            )
            for i in range(4)
        ],
        *[
            session(
                f"s{i}",
                "eu.anthropic.claude-sonnet-4-6",
                TokenUsage(output=100_000, cache_read=2_000_000),
                started="2026-10-01 09:00:00",
            )
            for i in range(3)
        ],
    ]
    for sc in sessions:
        sc.harness = "pi"
    opp = CostOpportunity(
        id=uuid.uuid4(),
        fingerprint="model_routing:x",
        detector="model_routing",
        title="Route Claude Opus 4.6 work to Claude Sonnet 4.6",
        agent_id=uuid.UUID(AGENT),
        baseline_version="1.0.0",
        status=STATUS_IDENTIFIED,
        estimated_monthly_usd=40,
        evidence={"billing_class": "billed", "validated": False, "target_sessions": 3, "to_model": "claude-sonnet-4-6"},
        still_detected=True,
        detected_at=now,
        last_seen_at=now,
    )
    capacity = CostOpportunity(
        id=uuid.uuid4(),
        fingerprint="cache_efficiency:y:capacity",
        detector="cache_efficiency",
        title="Raise prompt-cache hit rate",
        agent_id=None,
        status=STATUS_IDENTIFIED,
        estimated_monthly_usd=12,
        evidence={"billing_class": "capacity"},
        still_detected=True,
        detected_at=now,
        last_seen_at=now,
    )
    monkeypatch.setattr(
        service,
        "load_config",
        AsyncMock(
            return_value=_config(billing='{"claude-code": {"mode": "subscription", "seats": 2, "seat_usd_month": 100}}')
        ),
    )
    monkeypatch.setattr(ledger, "daily_cost_lines", AsyncMock(return_value=lines))
    monkeypatch.setattr(ledger, "session_costs", AsyncMock(return_value=sessions))
    monkeypatch.setattr(ledger, "earliest_event", AsyncMock(return_value=datetime(2026, 6, 10, tzinfo=UTC)))
    monkeypatch.setattr(service.ds, "get_int", AsyncMock(return_value=120))
    monkeypatch.setattr(
        service, "load_outcomes", AsyncMock(return_value={"o0": "fully_achieved", "s0": "mostly_achieved"})
    )

    db = MagicMock()
    db.add = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _Result(scalars=[opp, capacity]),  # opportunities (read-only)
            _Result(rows=[SimpleNamespace(id=uuid.UUID(AGENT), name="ultra-pi")]),  # agent names
            _Result(rows=[SimpleNamespace(success_criteria={"intended_purpose": "Ship features"})]),  # criteria
        ]
    )

    payload = await service.build_cost_intelligence(db, now=now)

    head = payload["headline"]
    pi_cost = lines[0].cost
    # Billed: Bedrock usage is detected as metered; Claude Code is a configured
    # subscription (2 seats x $100); unconfigured Kiro credits have no rate.
    assert head["spend_mtd_usd"] == pytest.approx(pi_cost + 200, rel=1e-6)
    assert head["usage_value_mtd_usd"] == pytest.approx(pi_cost + lines[1].cost, rel=1e-6)
    modes = {h["harness"]: (h["mode"], h["source"]) for h in payload["billing"]["harnesses"]}
    assert modes["pi"] == ("metered", "detected")
    assert modes["claude-code"] == ("subscription", "configured")
    assert modes["kiro"] == ("subscription", "detected")
    # Capacity savings (subscription usage) never inflate the headline.
    assert head["savings_identified_monthly_usd"] == pytest.approx(40)
    assert payload["pipeline"]["capacity_monthly_usd"] == pytest.approx(12)
    assert head["realization_rate_pct"] == 0.0
    assert payload["drivers"]["by_agent"][0]["label"] == "ultra-pi"
    assert payload["coverage"]["unpriced_credits"] == {"kiro": 3.0}
    assert payload["pipeline"]["counts"][STATUS_IDENTIFIED] == 1
    assert payload["pipeline"]["blocker"].endswith("awaiting review.")
    assert [t["month"] for t in payload["trend"]] == ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]
    assert payload["trend"][-1]["partial"] is True
    # Months before the oldest stored telemetry are unknown, not $0.
    assert payload["trend"][0]["data"] == "none"
    assert payload["trend"][0]["spend_usd"] is None
    assert payload["trend"][1]["data"] == "partial"
    assert payload["trend"][2]["data"] == "full"
    assert payload["period"]["retention_days"] == 120
    assert payload["unit_economics"]["current"]["prs_created"] == 4
    assert payload["unit_economics"]["criteria"] == {"agents_with_spend": 1, "agents_with_criteria": 1}
    assert payload["insight"] is not None
    db.add.assert_not_called()  # the dashboard read never writes


@pytest.mark.asyncio
async def test_refresh_retries_when_a_concurrent_refresh_wins_the_insert(monkeypatch):
    from sqlalchemy.exc import IntegrityError

    calls = {"n": 0}

    async def flaky(db, candidates, now):
        calls["n"] += 1
        if calls["n"] == 1:
            raise IntegrityError("insert", {}, Exception("duplicate fingerprint"))
        return []

    monkeypatch.setattr(service, "_upsert", flaky)
    db = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    rows = await service.sync_opportunities(db, [], [], datetime(2026, 10, 1, tzinfo=UTC))
    assert rows == []
    assert calls["n"] == 2
    db.rollback.assert_awaited_once()


def test_month_windows_follow_the_configured_timezone():
    w = service.windows_for(datetime(2026, 10, 31, 20, tzinfo=UTC), "Asia/Kolkata")
    # 20:00 UTC on Oct 31 is already Nov 1 in India.
    assert w.local_day(w.month_start) == date(2026, 11, 1)
    assert w.month_start == datetime(2026, 10, 31, 18, 30, tzinfo=UTC)


def test_subscription_usage_produces_capacity_not_billed_savings():
    sessions = _routing_sessions()
    for sc in sessions:
        sc.harness = "claude-code"
    billed = service.detect_by_billing(sessions, _config(), {})
    capacity = service.detect_by_billing(
        sessions, _config(billing='{"claude-code": {"mode": "subscription", "seats": 1, "seat_usd_month": 100}}'), {}
    )
    assert [c.billing_class for c in billed] == ["billed"]
    assert [c.billing_class for c in capacity] == ["capacity"]
    assert capacity[0].fingerprint.endswith(":capacity")


@pytest.mark.asyncio
async def test_auto_implement_requires_sessions_on_a_later_release():
    approved_at = datetime(2026, 10, 1, tzinfo=UTC)
    opp = CostOpportunity(status=STATUS_APPROVED, agent_id=uuid.UUID(AGENT), approved_at=approved_at)
    v = SimpleNamespace(agent_id=uuid.UUID(AGENT), version="1.1.0", released_at=approved_at + timedelta(days=2))
    db = MagicMock()
    db.execute = AsyncMock(return_value=_Result(rows=[v]))

    await service.auto_implement(db, [opp], [])
    assert opp.status == STATUS_APPROVED

    await service.auto_implement(db, [opp], [session("x", "claude-sonnet-4-6", TokenUsage(), version="1.1.0")])
    assert opp.status == STATUS_IMPLEMENTED
    assert opp.implemented_version == "1.1.0"


# ── Routes ───────────────────────────────────────────────────────────────


def _app(db=None):
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid.uuid4(), role=UserRole.admin)
    app.dependency_overrides[get_db] = lambda: db or MagicMock()
    return app


@pytest.mark.asyncio
async def test_intelligence_returns_503_when_telemetry_is_unreadable(monkeypatch):
    monkeypatch.setattr(
        service, "cached_cost_intelligence", AsyncMock(side_effect=ledger.CostDataUnavailableError("down"))
    )
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as client:
        resp = await client.get("/api/v1/exec/cost/intelligence")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_implemented_requires_an_approved_opportunity():
    opp = CostOpportunity(id=uuid.uuid4(), status=STATUS_IDENTIFIED, agent_id=uuid.UUID(AGENT))
    db = MagicMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: opp))
    async with AsyncClient(transport=ASGITransport(app=_app(db)), base_url="http://t") as client:
        resp = await client.post(f"/api/v1/exec/cost/opportunities/{opp.id}/implemented", json={"version": "1.1.0"})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_non_admins_cannot_read_cost_intelligence(monkeypatch):
    monkeypatch.setattr("api.deps.emit_security_event", AsyncMock())
    app = _app()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=uuid.uuid4(), email="user@example.test", role=UserRole.user
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get("/api/v1/exec/cost/intelligence")
    assert resp.status_code == 403


def _seat_config(**cfg):
    import json as _json

    return _config(billing=_json.dumps({"claude-code": {"mode": "subscription", **cfg}}))


def test_plan_fit_flags_seats_that_cost_more_than_their_usage():
    sessions = [
        session(f"u{i}", "claude-sonnet-4-6", TokenUsage(output=1_000_000), cost=c) for i, c in enumerate([150.0, 20.0])
    ]
    for i, sc in enumerate(sessions):
        sc.harness, sc.user_id = "claude-code", f"user{i}"
    [c] = service.detect_plan_fit(sessions, _seat_config(seats=3, seat_usd_month=100), 30)
    # user0 uses more than a seat; user1 uses $20 of a $100 seat; one seat is idle.
    assert c.savings == pytest.approx(80 + 100)
    assert c.evidence["underused_users"] == 1 and c.evidence["idle_seats"] == 1


def test_plan_fit_never_exceeds_the_seat_bill_when_users_outnumber_seats():
    sessions = [session(f"u{i}", "claude-sonnet-4-6", TokenUsage(output=1), cost=5.0) for i in range(6)]
    for i, sc in enumerate(sessions):
        sc.harness, sc.user_id = "claude-code", f"user{i}"
    [c] = service.detect_plan_fit(sessions, _seat_config(seats=3, seat_usd_month=100), 30)
    assert c.savings <= 300
    assert c.evidence["users_without_seat"] == 3


def test_plan_fit_skips_harnesses_whose_usage_cannot_be_priced():
    sc = session("k", "Kiro Auto", TokenUsage(), cost=None)
    sc.harness = "kiro"
    import json as _json

    cfg = _config(billing=_json.dumps({"kiro": {"mode": "subscription", "seats": 2, "seat_usd_month": 20}}))
    assert service.detect_plan_fit([sc], cfg, 30) == []


def test_blocker_note_separates_releases_from_seat_changes():
    agent_opp = CostOpportunity(
        status=STATUS_APPROVED, estimated_monthly_usd=10, agent_id=uuid.UUID(AGENT), still_detected=True
    )
    seat_opp = CostOpportunity(
        status=STATUS_APPROVED,
        estimated_monthly_usd=5,
        agent_id=None,
        evidence={"harness": "claude-code"},
        still_detected=True,
    )
    note = service.blocker_note([agent_opp, seat_opp], {AGENT: "ultra-pi"})
    assert (
        note == "$15/mo approved but not live: waiting on a new release of ultra-pi and a seat change for claude-code."
    )
