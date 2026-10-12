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


def test_regional_bedrock_profiles_resolve_to_the_same_list_price():
    assert PriceBook().prices_for("eu.anthropic.claude-opus-4-6-v1").input == pytest.approx(5e-6)


def test_override_beats_list_price_and_multiplier_scales_it():
    b = book(
        overrides='{"claude-opus-4-6": {"input": 4, "output": 20, "cache_read": 0.4, "cache_write": 5}}',
        multipliers='{"^eu\\\\.anthropic\\\\.": 1.1}',
    )
    eu = b.prices_for("eu.anthropic.claude-opus-4-6-v1")
    plain = b.prices_for("claude-opus-4-6")
    assert plain.source == PRICE_SOURCE_OVERRIDE
    assert plain.input == pytest.approx(4e-6)
    assert eu.input == pytest.approx(4.4e-6)


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
    cached = TokenUsage(input=10_000, output=1_000, cache_read=990_000)
    uncached = TokenUsage(input=500_000, output=1_000, cache_read=500_000)
    sessions = [
        *[session(f"good{i}", "claude-sonnet-4-6", cached, agent=OTHER) for i in range(10)],
        *[session(f"bad{i}", "claude-sonnet-4-6", uncached) for i in range(5)],
    ]
    [candidate] = opportunities.detect_cache_efficiency(sessions, PriceBook())
    assert candidate.agent_id == AGENT
    assert candidate.evidence["agent_hit_pct"] == pytest.approx(50.0)
    assert candidate.savings > 0


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


def test_realized_savings_compare_new_version_sessions_with_frozen_baseline():
    after = [session(f"n{i}", "claude-sonnet-4-6", TokenUsage(), version="1.1.0", cost=1.0) for i in range(4)]
    baseline = {"cost_per_session": 3.0, "sessions": 10}
    result = realized.measure(
        after, agent_id=AGENT, baseline=baseline, implemented_version="1.1.0", implemented_at=None
    )
    assert result.measurable
    assert result.amount == pytest.approx(8.0)
    assert result.sessions == 4


def test_realized_savings_keep_regressions_negative():
    after = [session("n", "claude-sonnet-4-6", TokenUsage(), version="1.1.0", cost=5.0)]
    result = realized.measure(
        after,
        agent_id=AGENT,
        baseline={"cost_per_session": 3.0, "sessions": 5},
        implemented_version="1.1.0",
        implemented_at=None,
    )
    assert result.amount == pytest.approx(-2.0)


def test_realized_savings_refuse_a_thin_baseline():
    result = realized.measure(
        [],
        agent_id=AGENT,
        baseline={"cost_per_session": 3.0, "sessions": 2},
        implemented_version="1.1.0",
        implemented_at=None,
    )
    assert not result.measurable
    assert "fewer than" in result.reason


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
                "claude-opus-4-6",
                TokenUsage(output=200_000, cache_read=5_000_000),
                started="2026-10-03 10:00:00",
                prs=1,
            )
            for i in range(4)
        ],
        *[
            session(
                f"s{i}",
                "claude-sonnet-4-6",
                TokenUsage(output=100_000, cache_read=2_000_000),
                started="2026-10-01 09:00:00",
            )
            for i in range(3)
        ],
    ]
    monkeypatch.setattr(service, "load_price_book", AsyncMock(return_value=PriceBook()))
    monkeypatch.setattr(ledger, "daily_cost_lines", AsyncMock(return_value=lines))
    monkeypatch.setattr(ledger, "session_costs", AsyncMock(return_value=sessions))
    monkeypatch.setattr(
        service, "load_outcomes", AsyncMock(return_value={"o0": "fully_achieved", "s0": "mostly_achieved"})
    )
    monkeypatch.setattr(service, "_min_savings", AsyncMock(return_value=1.0))
    monkeypatch.setattr(ledger, "earliest_event", AsyncMock(return_value=datetime(2026, 6, 10, tzinfo=UTC)))
    monkeypatch.setattr(service.ds, "get_int", AsyncMock(return_value=120))

    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    # sync_opportunities: existing rows; _agent_names: id/name rows
    db.execute = AsyncMock(
        side_effect=[
            _Result(scalars=[]),  # existing opportunities
            _Result(rows=[SimpleNamespace(id=uuid.UUID(AGENT), name="ultra-pi")]),  # agent names
            _Result(rows=[SimpleNamespace(success_criteria={"intended_purpose": "Ship features"})]),  # criteria
        ]
    )

    payload = await service.build_cost_intelligence(db, now=now)

    head = payload["headline"]
    assert head["spend_mtd_usd"] == pytest.approx(lines[0].cost, rel=1e-6)
    assert head["savings_identified_monthly_usd"] > 0
    assert head["realization_rate_pct"] == 0.0
    assert payload["drivers"]["by_agent"][0]["label"] == "ultra-pi"
    assert payload["coverage"]["unpriced_credits"] == {"kiro": 3.0}
    assert payload["tokens"]["cache_hit_pct"] == 100.0
    assert payload["pipeline"]["counts"][STATUS_IDENTIFIED] >= 1
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
    assert payload["unit_economics"]["current"]["outcome_coverage_pct"] == pytest.approx(28.6)
    assert payload["insight"] is not None
    assert payload["unit_economics"]["criteria"] == {"agents_with_spend": 1, "agents_with_criteria": 1}
    db.add.assert_called()


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
