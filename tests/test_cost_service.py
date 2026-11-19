# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from services.cost import queries, service
from services.cost.config import CostSettings

M = 1_000_000
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


def usage_row(model: str, *, input=0, output=0, cache_read=0, cache_write=0, **extra) -> dict:
    # ClickHouse returns 64-bit sums as strings.
    return {
        "model": model,
        "input": str(input),
        "output": str(output),
        "cache_read": str(cache_read),
        "cache_write": str(cache_write),
        **extra,
    }


def session_row(session_id, user_id, harness, model, **tokens) -> dict:
    return usage_row(model, session_id=session_id, user_id=user_id, harness=harness, **tokens)


# Current period: s1 uses Sonnet ($12) and Opus ($4), s2 uses Codex ($14), s3 uses a model with no price.
CURRENT_SESSIONS = [
    session_row("s1", "u1", "claude-code", "claude-sonnet-5-5", input=M, output=M),
    session_row("s1", "u1", "claude-code", "claude-opus-5-5", input=M),
    session_row("s2", "u2", "codex", "gpt-5.3-codex", output=M),
    session_row("s3", "u2", "opencode", "mystery-model", input=5 * M),
]
CURRENT_BUCKETS = [
    usage_row("claude-sonnet-5-5", input=M, output=M, bucket="2026-11-01", harness="claude-code"),
    usage_row("claude-opus-5-5", input=M, bucket="2026-11-01", harness="claude-code"),
    usage_row("gpt-5.3-codex", output=M, bucket="2026-11-02", harness="codex"),
    usage_row("mystery-model", input=5 * M, bucket="2026-11-02", harness="opencode"),
]
PREVIOUS_BUCKETS = [usage_row("claude-sonnet-5-5", output=2 * M, bucket="2026-10-01", harness="claude-code")]
MONTH_BUCKETS = [
    usage_row("claude-sonnet-5-5", input=M, output=M, bucket="2026-11-01", harness="claude-code"),
    usage_row("gpt-5.3-codex", output=M, bucket="2026-11-02", harness="codex"),
]
COVERAGE = [
    {"harness": "claude-code", "sessions": "1", "sessions_with_usage": "1", "credits": 0},
    {"harness": "codex", "sessions": "1", "sessions_with_usage": "1", "credits": 0},
    {"harness": "kiro", "sessions": "5", "sessions_with_usage": "0", "credits": 120.0},
    {"harness": "antigravity", "sessions": "2", "sessions_with_usage": "0", "credits": 0},
]


def install(
    monkeypatch,
    *,
    current=None,
    previous=None,
    month=None,
    sessions=None,
    previous_sessions=None,
    prompts=None,
    coverage=None,
    settings=None,
):
    start = NOW - timedelta(days=30)

    async def bucket_usage(range_start, range_end, tz, bucket):
        if range_end == start:
            return PREVIOUS_BUCKETS if previous is None else previous
        if range_start == start:
            return CURRENT_BUCKETS if current is None else current
        return MONTH_BUCKETS if month is None else month

    async def session_usage(range_start, range_end):
        if range_end == start:
            return [] if previous_sessions is None else previous_sessions
        return CURRENT_SESSIONS if sessions is None else sessions

    monkeypatch.setattr(queries, "bucket_usage", bucket_usage)
    monkeypatch.setattr(queries, "session_usage", session_usage)
    monkeypatch.setattr(
        queries, "session_prompts", AsyncMock(return_value=prompts or [{"session_id": "s1", "prompts": "3"}])
    )
    monkeypatch.setattr(queries, "harness_coverage", AsyncMock(return_value=COVERAGE if coverage is None else coverage))
    monkeypatch.setattr(service.cost_config, "load", AsyncMock(return_value=settings or CostSettings()))


@pytest.mark.asyncio
async def test_overview_kpis(monkeypatch):
    install(monkeypatch)

    overview = await service.build_overview("30d", now=NOW)

    kpis = overview.kpis
    assert kpis.spend_usd == 30.0
    assert kpis.prev_spend_usd == 20.0
    assert kpis.change_pct == 50.0
    assert kpis.month_to_date_usd == 26.0
    assert kpis.projected_month_usd == pytest.approx(26 / 15.5 * 30, abs=0.01)
    assert kpis.active_users == 2
    assert kpis.sessions_priced == 2  # s1 used two models but is one session
    assert kpis.cost_per_session_usd == 15.0
    assert kpis.cost_per_active_user_usd == 15.0
    assert kpis.potential_savings_monthly_usd == 0.0
    assert overview.range.bucket == "day"
    assert overview.currency.default == "USD"


@pytest.mark.asyncio
async def test_overview_change_is_null_without_a_previous_period(monkeypatch):
    install(monkeypatch, previous=[])

    kpis = (await service.build_overview("30d", now=NOW)).kpis

    assert kpis.prev_spend_usd is None
    assert kpis.change_pct is None


@pytest.mark.asyncio
async def test_overview_breakdowns_and_coverage(monkeypatch):
    install(monkeypatch)

    overview = await service.build_overview("30d", now=NOW)

    assert [(m.key, m.usd) for m in overview.model_share] == [
        ("gpt-5.3-codex", 14.0),
        ("claude-sonnet-5-5", 12.0),
        ("claude-opus-5-5", 4.0),
    ]
    assert [(h.display_name, h.usd) for h in overview.harnesses] == [("Claude Code", 16.0), ("Codex", 14.0)]
    assert [(p.bucket_start, p.total_usd) for p in overview.series] == [("2026-11-01", 16.0), ("2026-11-02", 14.0)]
    coverage = overview.coverage
    assert (coverage.sessions_total, coverage.sessions_with_usage) == (9, 2)
    assert [(u.model, u.usage_total) for u in coverage.unpriced_models] == [("mystery-model", 5 * M)]
    assert [(n.harness, n.reason, n.sessions, n.credits) for n in coverage.not_priced] == [
        ("kiro", "credits_only", 5, 120.0),
        ("antigravity", "no_usage", 2, 0.0),
    ]


@pytest.mark.asyncio
async def test_overview_groups_models_beyond_the_top_five_as_other(monkeypatch):
    models = [
        "claude-sonnet-5-5",
        "gpt-5.3-codex",
        "claude-opus-5-5",
        "gemini-3.1-pro-preview",
        "gpt-5.4-mini",
        "claude-haiku-5-5",
        "gemini-2.5-flash",
    ]
    sessions = [session_row(f"s{i}", "u1", "claude-code", m, output=(10 - i) * M) for i, m in enumerate(models)]
    buckets = [
        usage_row(m, output=(10 - i) * M, bucket="2026-11-01", harness="claude-code") for i, m in enumerate(models)
    ]
    install(monkeypatch, sessions=sessions, current=buckets)

    overview = await service.build_overview("30d", now=NOW)

    assert len(overview.model_share) == 6
    assert overview.model_share[-1].key == "other"
    assert overview.model_share[-1].models == 2
    top_and_other = {m.key for m in overview.model_share}
    assert {entry.key for entry in overview.series[0].by_model} <= top_and_other
    assert sum(m.share for m in overview.model_share) == pytest.approx(1.0, abs=0.01)


@pytest.mark.asyncio
async def test_ninety_days_uses_weekly_buckets(monkeypatch):
    install(monkeypatch)
    calls = []

    async def bucket_usage(range_start, range_end, tz, bucket):
        calls.append(bucket)
        return []

    monkeypatch.setattr(queries, "bucket_usage", bucket_usage)

    overview = await service.build_overview("90d", now=NOW)

    assert overview.range.bucket == "week"
    assert calls[0] == "week"


def test_month_start_and_projection_respect_the_timezone():
    kolkata = ZoneInfo("Asia/Kolkata")
    # 20:00 UTC on Oct 31 is already 01:30 on Nov 1 in Kolkata.
    late = datetime(2026, 10, 31, 20, 0, tzinfo=UTC)
    assert service._month_start(late, kolkata) == datetime(2026, 10, 31, 18, 30, tzinfo=UTC)
    assert service._projection(50.0, late, kolkata) is None  # first hours of the month
    assert service._projection(30.0, datetime(2026, 11, 16, 0, 0, tzinfo=UTC), ZoneInfo("UTC")) == 60.0


@pytest.mark.asyncio
async def test_details_departments_models_and_totals(monkeypatch):
    sessions = [
        session_row("s1", "u1", "claude-code", "claude-sonnet-5-5", input=M, output=M),
        session_row("s1", "u1", "claude-code", "claude-opus-5-5", input=M),
        session_row("s2", "u2", "codex", "gpt-5.3-codex", output=M),
        session_row("s4", "u3", "claude-code", "claude-sonnet-5-5", input=2 * M),
    ]
    previous = [session_row("p1", "u1", "claude-code", "claude-sonnet-5-5", output=800_000)]
    install(monkeypatch, sessions=sessions, previous_sessions=previous)
    monkeypatch.setattr(
        service,
        "resolve_user_departments",
        AsyncMock(return_value={"Platform": ["u1"], "Security": ["u1"], "Product": ["u2"]}),
    )
    monkeypatch.setattr(service, "_usernames", AsyncMock(return_value={"u1": "ana", "u2": "bo"}))

    details = await service.build_details(db=None, range_key="30d", now=NOW)

    by_name = {d.name: d for d in details.departments}
    assert set(by_name) == {"Platform", "Security", "Product", "Unassigned"}
    assert by_name["Platform"].usd == by_name["Security"].usd == 16.0
    assert by_name["Platform"].change_pct == 100.0
    assert by_name["Unassigned"].people[0].username == "u3"  # unknown to Postgres
    ana = by_name["Platform"].people[0]
    assert (ana.username, ana.sessions, ana.top_model, ana.top_model_share) == ("ana", 1, "claude-sonnet-5-5", 0.75)
    assert details.multi_department_users == 1

    sonnet = details.models[0]
    assert (sonnet.model, sonnet.usd, sonnet.sessions) == ("claude-sonnet-5-5", 16.0, 2)
    assert sonnet.usage.input == 3 * M
    assert sonnet.cost_usd.input == 6.0
    assert [(u.username, u.usd) for u in sonnet.top_users] == [("ana", 12.0), ("u3", 4.0)]
    assert sum(h.share for h in sonnet.harnesses) == pytest.approx(1.0)
    assert details.totals.usd == 34.0
    assert details.totals.sessions == 3


@pytest.mark.asyncio
async def test_details_keeps_the_top_ten_people_and_summarizes_the_rest(monkeypatch):
    users = [f"u{i}" for i in range(12)]
    sessions = [session_row(f"s{i}", u, "codex", "gpt-5.3-codex", output=(i + 1) * M) for i, u in enumerate(users)]
    install(monkeypatch, sessions=sessions)
    monkeypatch.setattr(service, "resolve_user_departments", AsyncMock(return_value={"Eng": users}))
    monkeypatch.setattr(service, "_usernames", AsyncMock(return_value={}))

    [eng] = (await service.build_details(db=None, range_key="30d", now=NOW)).departments

    assert len(eng.people) == 10
    assert eng.people[0].user_id == "u11"
    assert (eng.rest.users, eng.rest.sessions, eng.rest.usd) == (2, 2, 14.0 + 28.0)
    assert eng.users == 12
