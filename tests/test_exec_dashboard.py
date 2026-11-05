# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import inspect
import json
import uuid
from collections import namedtuple
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from redis.exceptions import RedisError

from api.deps import get_current_user, get_db
from api.routes import exec_dashboard as dashboard
from models.exec_config import ExecDashboardConfig
from models.user import UserRole


class FakeScalars:
    def __init__(self, values=()):
        self.values = list(values)

    def all(self):
        return self.values


class FakeResult:
    def __init__(self, rows=(), *, scalar=None, scalars=None):
        self.rows = list(rows)
        self.scalar = scalar
        self.scalar_values = self.rows if scalars is None else list(scalars)

    def all(self):
        return self.rows

    def scalar_one_or_none(self):
        return self.scalar

    def scalars(self):
        return FakeScalars(self.scalar_values)


def make_db(*results, scalar_values=()):
    db = MagicMock()
    db.execute = AsyncMock(side_effect=results)
    db.scalar = AsyncMock(side_effect=scalar_values)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


def row(**values):
    row_type = namedtuple("Row", values)
    return row_type(**values)


def sql_text(statement) -> str:
    return " ".join(str(statement).split())


def admin_user():
    return SimpleNamespace(
        id=uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        email="admin@example.test",
        role=UserRole.admin,
    )


def ch_mock(monkeypatch, *responses):
    mock = AsyncMock(side_effect=responses)
    monkeypatch.setattr(dashboard, "_ch_json", mock)
    return mock


@pytest.mark.parametrize(
    ("current", "previous", "expected"),
    [(150, 100, 50.0), (50, 100, -50.0), (0, 100, -100.0), (4, 0, 100.0), (0, 0, 0.0)],
)
def test_compute_trend_percent(current, previous, expected):
    assert dashboard.compute_trend_percent(current, previous) == expected


def test_period_bounds_are_contiguous_and_deterministic(monkeypatch):
    now = datetime(2026, 6, 15, 12, 30, tzinfo=UTC)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(dashboard, "dt", FrozenDateTime)

    current_start, current_end, previous_start, previous_end = dashboard._period_bounds("30d")

    assert (current_start, current_end) == (now - timedelta(days=30), now)
    assert (previous_start, previous_end) == (now - timedelta(days=60), now - timedelta(days=30))


@pytest.mark.asyncio
async def test_resolve_user_departments_prefers_groups_then_department_and_unassigned():
    grouped = uuid.UUID("11111111-1111-1111-1111-111111111111")
    departmental = uuid.UUID("22222222-2222-2222-2222-222222222222")
    unassigned = uuid.UUID("33333333-3333-3333-3333-333333333333")
    db = make_db(
        FakeResult([row(group_name="Platform", user_id=grouped), row(group_name="Security", user_id=grouped)]),
        FakeResult([row(id=departmental, department="Product")]),
        FakeResult(scalars=[grouped, departmental, unassigned]),
    )

    result = await dashboard.resolve_user_departments(db)

    assert result == {
        "Platform": [str(grouped)],
        "Security": [str(grouped)],
        "Product": [str(departmental)],
        "Unassigned": [str(unassigned)],
    }
    statements = [sql_text(call.args[0]) for call in db.execute.await_args_list]
    assert "user_groups.group_name" in statements[0]
    assert "users.department IS NOT NULL" in statements[1]
    assert "users.id NOT IN" in statements[1]
    assert statements[2].startswith("SELECT users.id")


@pytest.mark.asyncio
async def test_resolve_user_departments_handles_empty_deployment():
    db = make_db(FakeResult(), FakeResult(), FakeResult(scalars=[]))

    assert await dashboard.resolve_user_departments(db) == {}
    assert "NOT IN" not in sql_text(db.execute.await_args_list[1].args[0])


@pytest.mark.asyncio
async def test_get_exec_config_returns_none_or_serialized_config():
    config_id = uuid.UUID("44444444-4444-4444-4444-444444444444")
    config = SimpleNamespace(
        id=config_id,
        hourly_dev_cost=Decimal("82.50"),
        pre_ai_baselines=None,
        department_budgets={"Platform": 1000},
        target_adoption_pct=85,
        target_adoption_date=date(2026, 12, 31),
    )
    missing_db = make_db(FakeResult(scalar=None))
    existing_db = make_db(FakeResult(scalar=config))

    assert await dashboard.get_exec_config(missing_db, admin_user()) is None
    response = await dashboard.get_exec_config(existing_db, admin_user())

    assert response.model_dump(mode="json") == {
        "id": str(config_id),
        "hourly_dev_cost": 82.5,
        "pre_ai_baselines": {},
        "department_budgets": {"Platform": 1000},
        "target_adoption_pct": 85,
        "target_adoption_date": "2026-12-31",
    }
    assert "LIMIT" in sql_text(existing_db.execute.await_args.args[0])


@pytest.mark.asyncio
async def test_update_exec_config_creates_and_populates_every_field():
    config_id = uuid.UUID("55555555-5555-5555-5555-555555555555")
    db = make_db(FakeResult(scalar=None))

    async def refresh(config):
        config.id = config_id

    db.refresh.side_effect = refresh
    request = dashboard.ExecConfigUpdate(
        hourly_dev_cost=91.25,
        pre_ai_baselines={"reviews": 8},
        department_budgets={"Security": 2500},
        target_adoption_pct=90,
        target_adoption_date="2026-11-30",
    )

    response = await dashboard.update_exec_config(request, db, admin_user())

    created = db.add.call_args.args[0]
    assert isinstance(created, ExecDashboardConfig)
    assert created.target_adoption_date == date(2026, 11, 30)
    assert response.model_dump(mode="json") == {
        "id": str(config_id),
        "hourly_dev_cost": 91.25,
        "pre_ai_baselines": {"reviews": 8},
        "department_budgets": {"Security": 2500},
        "target_adoption_pct": 90,
        "target_adoption_date": "2026-11-30",
    }
    db.commit.assert_awaited_once_with()
    db.refresh.assert_awaited_once_with(created)


@pytest.mark.asyncio
async def test_update_exec_config_preserves_omitted_fields_and_rejects_bad_dates():
    config = SimpleNamespace(
        id=uuid.uuid4(),
        hourly_dev_cost=75.0,
        pre_ai_baselines={"tasks": 2},
        department_budgets={"Product": 10},
        target_adoption_pct=70,
        target_adoption_date=None,
    )
    db = make_db(FakeResult(scalar=config), FakeResult(scalar=config))

    unchanged = await dashboard.update_exec_config(dashboard.ExecConfigUpdate(), db, admin_user())

    assert unchanged.pre_ai_baselines == {"tasks": 2}
    assert unchanged.target_adoption_pct == 70
    db.add.assert_not_called()

    with pytest.raises(ValueError, match="Invalid isoformat string"):
        await dashboard.update_exec_config(
            dashboard.ExecConfigUpdate(target_adoption_date="not-a-date"),
            db,
            admin_user(),
        )
    assert db.commit.await_count == 1


@pytest.mark.asyncio
async def test_adoption_aggregates_months_current_users_and_departments(monkeypatch):
    db = make_db(scalar_values=[8])
    ch = ch_mock(
        monkeypatch,
        [{"month": "2026-04-01", "active": "2"}, {"month": "2026-05-01", "active": 6}],
        [{"active": "4"}],
    )
    departments = AsyncMock(return_value={"Platform": ["one"], "Product": ["two"], "Unassigned": ["three"]})
    monkeypatch.setattr(dashboard, "resolve_user_departments", departments)

    result = await dashboard.get_adoption(db, admin_user())

    assert result.model_dump() == {
        "monthly": [
            {"month": "2026-04", "adoption_pct": 25.0},
            {"month": "2026-05", "adoption_pct": 75.0},
        ],
        "current_pct": 50.0,
        "total_users": 8,
        "active_users": 4,
        "departments_covered": 2,
    }
    assert "INTERVAL 12 MONTH" in ch.await_args_list[0].args[0]
    assert "toStartOfMonth(now())" in ch.await_args_list[1].args[0]
    assert all("project_id = '{project_id}'" in call.args[0] for call in ch.await_args_list)


@pytest.mark.asyncio
async def test_adoption_handles_no_users_or_clickhouse_rows(monkeypatch):
    db = make_db(scalar_values=[None])
    ch_mock(monkeypatch, [{"month": "2026-05-01", "active": 3}], [])
    monkeypatch.setattr(dashboard, "resolve_user_departments", AsyncMock(return_value={}))

    result = await dashboard.get_adoption(db, admin_user())

    assert result.total_users == 0
    assert result.monthly[0].adoption_pct == 0
    assert result.active_users == 0
    assert result.current_pct == 0


@pytest.mark.asyncio
async def test_agent_counts_combines_postgres_statuses_categories_and_clickhouse(monkeypatch):
    db = make_db(
        FakeResult([("Review", 3), (None, 2)]),
        scalar_values=[7, 4, 2],
    )
    ch = ch_mock(monkeypatch, [{"cnt": "5"}])

    result = await dashboard.get_agent_counts(db, admin_user())

    assert result.model_dump() == {
        "total": 7,
        "active": 5,
        "published": 4,
        "in_development": 2,
        "by_category": [
            {"category": "Review", "count": 3},
            {"category": "Uncategorized", "count": 2},
        ],
    }
    scalar_sql = [sql_text(call.args[0]) for call in db.scalar.await_args_list]
    assert "FROM agents" in scalar_sql[0]
    assert "JOIN agent_versions" in scalar_sql[1]
    assert "JOIN agent_versions" in scalar_sql[2]
    assert "INTERVAL 7 DAY" in ch.await_args.args[0]
    assert "GROUP BY agents.category" in sql_text(db.execute.await_args.args[0])


@pytest.mark.asyncio
async def test_usage_by_category_resolves_agents_aggregates_and_builds_period_queries(monkeypatch):
    platform_id = uuid.UUID("11111111-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    uncategorized_id = uuid.UUID("22222222-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    current = [
        {"agent_id": str(platform_id), "cnt": "8"},
        {"agent_id": str(uncategorized_id), "cnt": 2},
        {"agent_id": "invalid", "cnt": 5},
    ]
    previous = [
        {"agent_id": str(platform_id), "cnt": 4},
        {"agent_id": str(uncategorized_id), "cnt": 4},
        {"agent_id": "invalid", "cnt": 5},
    ]
    db = make_db(
        FakeResult(
            [
                row(id=platform_id, category="Platform"),
                row(id=uncategorized_id, category=None),
            ]
        )
    )
    ch = ch_mock(monkeypatch, current, previous)

    result = await dashboard.get_usage_by_category("30d", db, admin_user())

    assert [item.model_dump() for item in result] == [
        {"category": "Platform", "sessions": 8, "growth_pct": 100.0},
        {"category": "Uncategorized", "sessions": 7, "growth_pct": -22.2},
    ]
    assert ch.await_args_list[0].args[1] == {"param_days": "30"}
    assert ch.await_args_list[1].args[1] == {"param_days": "30", "param_days2": "60"}
    assert "first_event_time < now()" in ch.await_args_list[1].args[0]
    assert "agents.id IN" in sql_text(db.execute.await_args.args[0])


@pytest.mark.asyncio
async def test_usage_by_category_empty_period_does_not_touch_postgres(monkeypatch):
    db = make_db()
    ch_mock(monkeypatch, [], [])

    assert await dashboard.get_usage_by_category(None, db, admin_user()) == []
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_platform_coverage_serializes_clickhouse_numbers(monkeypatch):
    ch = ch_mock(
        monkeypatch,
        [
            {"harness": "kiro", "users": "4", "sessions": "12"},
            {"harness": "pi", "users": 2, "sessions": 5},
        ],
    )

    result = await dashboard.get_platform_coverage(admin_user())

    assert [item.model_dump() for item in result] == [
        {"platform": "kiro", "users": 4, "sessions": 12},
        {"platform": "pi", "users": 2, "sessions": 5},
    ]
    assert "GROUP BY harness ORDER BY sessions DESC" in ch.await_args.args[0]


@pytest.mark.asyncio
async def test_platform_scores_rank_by_sessions_and_leave_unavailable_metrics_null(monkeypatch):
    ch_mock(
        monkeypatch,
        [
            {"harness": "kiro", "sessions": "20", "users": "7", "avg_latency_ms": "125.5"},
            {"harness": "pi", "sessions": 10, "users": 3, "avg_latency_ms": None},
            {"harness": "cursor", "sessions": 0, "users": 1},
        ],
    )

    result = await dashboard.get_platforms(admin_user())

    assert [item.model_dump() for item in result] == [
        {
            "platform": "kiro",
            "composite_score": 100.0,
            "sessions": 20,
            "avg_cost": 0.0,
            "avg_latency_ms": 125.5,
            "success_rate": None,
            "error_rate": None,
            "users": 7,
        },
        {
            "platform": "pi",
            "composite_score": 50.0,
            "sessions": 10,
            "avg_cost": 0.0,
            "avg_latency_ms": 0.0,
            "success_rate": None,
            "error_rate": None,
            "users": 3,
        },
        {
            "platform": "cursor",
            "composite_score": 0.0,
            "sessions": 0,
            "avg_cost": 0.0,
            "avg_latency_ms": 0.0,
            "success_rate": None,
            "error_rate": None,
            "users": 1,
        },
    ]


@pytest.mark.asyncio
async def test_platform_scores_handle_empty_clickhouse(monkeypatch):
    ch_mock(monkeypatch, [])

    assert await dashboard.get_platforms(admin_user()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rows", "current", "baseline", "multiplier"),
    [
        (
            [
                {"week": "2026-01-05", "traces": 10},
                {"week": "2026-01-12", "traces": 20},
                {"week": "2026-01-19", "traces": 30},
                {"week": "2026-01-26", "traces": 40},
                {"week": "2026-02-02", "traces": 50},
            ],
            35.0,
            25.0,
            1.4,
        ),
        ([{"week": "2026-01-05", "traces": 12}, {"week": "2026-01-12", "traces": 18}], 18.0, 12.0, 1.5),
        ([], 0.0, 0.0, 0.0),
        ([{"week": "2026-01-05", "traces": 0}], 0.0, 0.0, 0.0),
    ],
)
async def test_velocity_computes_baseline_for_available_history(monkeypatch, rows, current, baseline, multiplier):
    ch_mock(monkeypatch, rows)

    result = await dashboard.get_velocity(admin_user())

    assert result.current_weekly_avg == current
    assert result.baseline_weekly_avg == baseline
    assert result.multiplier == multiplier
    assert [point.week for point in result.weekly] == [str(item["week"])[:10] for item in rows]


@pytest.mark.asyncio
async def test_top_agents_scores_postgres_and_clickhouse_data_and_applies_limit(monkeypatch):
    first = uuid.UUID("11111111-1111-4111-8111-111111111111")
    second = uuid.UUID("22222222-2222-4222-8222-222222222222")
    third = uuid.UUID("33333333-3333-4333-8333-333333333333")
    db = make_db(
        FakeResult([row(agent_id=first, downloads=10), row(agent_id=second, downloads=5)]),
        FakeResult([row(listing_id=first, avg_rating=4), row(listing_id=second, avg_rating=5)]),
        FakeResult(
            [
                row(id=first, name="Reviewer", category="Review"),
                row(id=second, name="Builder", category=None),
                row(id=third, name="Search", category="Research"),
            ]
        ),
    )
    ch = ch_mock(
        monkeypatch,
        [
            {"agent_id": str(first), "sessions": 20},
            {"agent_id": str(third), "sessions": 40},
            {"agent_id": "invalid", "sessions": 1},
        ],
        [
            {"agent_id": str(first), "week": "2026-01-01", "cnt": 3},
            {"agent_id": str(first), "week": "2026-01-08", "cnt": 5},
            {"agent_id": str(third), "week": "2026-01-08", "cnt": 8},
        ],
    )

    result = await dashboard.get_top_agents(2, db, admin_user())

    assert [item.model_dump() for item in result] == [
        {
            "id": str(first),
            "name": "Reviewer",
            "category": "Review",
            "composite_score": 74.0,
            "sessions": 20,
            "downloads": 10,
            "avg_rating": 4.0,
            "weekly_trend": [3, 5],
        },
        {
            "id": str(second),
            "name": "Builder",
            "category": "Uncategorized",
            "composite_score": 45.0,
            "sessions": 0,
            "downloads": 5,
            "avg_rating": 5.0,
            "weekly_trend": [],
        },
    ]
    assert "agent_download_records" in sql_text(db.execute.await_args_list[0].args[0])
    assert "feedback.listing_type" in sql_text(db.execute.await_args_list[1].args[0])
    assert "INTERVAL 30 DAY" in ch.await_args_list[0].args[0]
    assert "INTERVAL 6 WEEK" in ch.await_args_list[1].args[0]


@pytest.mark.asyncio
async def test_top_agents_returns_empty_without_downloads_or_sessions(monkeypatch):
    db = make_db(FakeResult(), FakeResult())
    ch_mock(monkeypatch, [], [])

    assert await dashboard.get_top_agents(10, db, admin_user()) == []
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_departments_aggregate_agent_ownership_and_user_sessions(monkeypatch):
    first = "11111111-1111-1111-1111-111111111111"
    second = "22222222-2222-2222-2222-222222222222"
    third = "33333333-3333-3333-3333-333333333333"
    monkeypatch.setattr(
        dashboard,
        "resolve_user_departments",
        AsyncMock(return_value={"Engineering": [first, second], "Empty": [], "Unassigned": [third]}),
    )
    db = make_db(FakeResult([(uuid.UUID(first), 2), (uuid.UUID(third), 1)]))
    ch = ch_mock(
        monkeypatch,
        [{"user_id": first, "sessions": "3"}, {"user_id": third, "sessions": 2}, {"user_id": "other", "sessions": 99}],
    )

    result = await dashboard.get_departments("7d", db, admin_user())

    assert [item.model_dump() for item in result.departments] == [
        {
            "department": "Empty",
            "user_count": 0,
            "agent_count": 0,
            "utilization_pct": 0.0,
            "sessions_per_user": 0.0,
        },
        {
            "department": "Engineering",
            "user_count": 2,
            "agent_count": 2,
            "utilization_pct": 50.0,
            "sessions_per_user": 1.5,
        },
        {
            "department": "Unassigned",
            "user_count": 1,
            "agent_count": 1,
            "utilization_pct": 100.0,
            "sessions_per_user": 2.0,
        },
    ]
    assert "GROUP BY agents.created_by" in sql_text(db.execute.await_args.args[0])
    assert ch.await_args.args[1] == {"param_days": "7"}


@pytest.mark.asyncio
async def test_departments_return_empty_without_users(monkeypatch):
    db = make_db()
    ch = ch_mock(monkeypatch)
    monkeypatch.setattr(dashboard, "resolve_user_departments", AsyncMock(return_value={}))

    result = await dashboard.get_departments(None, db, admin_user())

    assert result.departments == []
    db.execute.assert_not_awaited()
    ch.assert_not_awaited()


@pytest.mark.asyncio
async def test_department_tokens_aggregate_current_and_previous_periods(monkeypatch):
    first = "11111111-1111-1111-1111-111111111111"
    second = "22222222-2222-2222-2222-222222222222"
    monkeypatch.setattr(
        dashboard,
        "resolve_user_departments",
        AsyncMock(return_value={"Empty": [], "Engineering": [first, second]}),
    )
    ch = ch_mock(
        monkeypatch,
        [
            {"user_id": first, "tokens": "100", "traces": "4"},
            {"user_id": second, "tokens": 50, "traces": 1},
        ],
        [{"user_id": first, "tokens": 50}, {"user_id": second, "tokens": 100}],
    )

    result = await dashboard.get_dept_tokens("30d", make_db(), admin_user())

    assert [item.model_dump() for item in result] == [
        {
            "department": "Empty",
            "tokens_used": 0,
            "cost_per_task": 0.0,
            "sessions_per_user": 0.0,
            "trend_pct": 0.0,
        },
        {
            "department": "Engineering",
            "tokens_used": 150,
            "cost_per_task": 0.0,
            "sessions_per_user": 2.5,
            "trend_pct": 0.0,
        },
    ]
    assert ch.await_args_list[0].args[1] == {"param_days": "30"}
    assert ch.await_args_list[1].args[1] == {"param_days": "30", "param_days2": "60"}


@pytest.mark.asyncio
async def test_department_tokens_return_empty_without_departments(monkeypatch):
    monkeypatch.setattr(dashboard, "resolve_user_departments", AsyncMock(return_value={}))
    ch = ch_mock(monkeypatch)

    assert await dashboard.get_dept_tokens(None, make_db(), admin_user()) == []
    ch.assert_not_awaited()


@pytest.mark.asyncio
async def test_cost_summary_distinguishes_unconfigured_and_configured(monkeypatch):
    unconfigured_db = make_db(FakeResult(scalar=None))
    configured_db = make_db(FakeResult(scalar=SimpleNamespace(id=uuid.uuid4())))
    ch = ch_mock(monkeypatch, [{"month": "2026-04-01"}, {"month": "2026-05-01T00:00:00Z"}])

    unconfigured = await dashboard.get_cost_summary("90d", unconfigured_db, admin_user())
    configured = await dashboard.get_cost_summary("90d", configured_db, admin_user())

    assert unconfigured.model_dump() == {
        "monthly_savings": 0.0,
        "cost_reduction_pct": 0.0,
        "projected_annual_savings": 0.0,
        "cost_per_task": 0.0,
        "monthly_trend": [],
        "by_category": [],
        "configured": False,
    }
    assert configured.model_dump() == {
        "monthly_savings": 0.0,
        "cost_reduction_pct": 0.0,
        "projected_annual_savings": 0.0,
        "cost_per_task": 0.0,
        "monthly_trend": [
            {"month": "2026-04", "ai_spend": 0.0, "savings": 0.0},
            {"month": "2026-05", "ai_spend": 0.0, "savings": 0.0},
        ],
        "by_category": [],
        "configured": True,
    }
    ch.assert_awaited_once()
    assert "INTERVAL 12 MONTH" in ch.await_args.args[0]


@pytest.mark.asyncio
async def test_roi_projections_report_cost_telemetry_as_unavailable():
    result = await dashboard.get_roi_projections(admin_user())

    assert result.model_dump() == {
        "projections": [],
        "growth_rate_pct": 0.0,
        "time_to_breakeven_months": None,
        "total_invested": 0.0,
        "total_saved": 0.0,
        "roi_multiple": 0.0,
    }


@pytest.mark.asyncio
async def test_strategic_insights_derive_models_departments_platforms_and_power_users(monkeypatch):
    users = [f"user-{index}" for index in range(1, 9)]
    monkeypatch.setattr(
        dashboard,
        "resolve_user_departments",
        AsyncMock(
            return_value={
                "High": [users[6]],
                "Low": users[0:4],
                "Moderate": users[4:6],
                "Unassigned": [users[7]],
            }
        ),
    )
    ch = ch_mock(
        monkeypatch,
        [
            {"model": "popular", "sessions": 10, "avg_tokens": 1000},
            {"model": "long", "sessions": 8, "avg_tokens": "6001"},
            {"model": "general", "sessions": 6, "avg_tokens": 2000},
        ],
        [
            {"model": "popular", "successes": 8, "total": 10},
            {"model": "long", "successes": 0, "total": 0},
            {"model": "general", "successes": 3, "total": 6},
        ],
        [
            {"user_id": users[0], "sessions": 4},
            {"user_id": users[4], "sessions": 2},
            {"user_id": users[6], "sessions": 7},
        ],
        [
            {"harness": "kiro", "avg_time_ms": 125, "sessions": 10, "completed": 8},
            {"harness": "pi", "avg_time_ms": None, "sessions": 0, "completed": 0},
        ],
        [
            {"user_id": users[0], "sessions": 5, "value": 50},
            {"user_id": users[1], "sessions": 4, "value": 20},
            {"user_id": users[2], "sessions": 3, "value": 10},
            {"user_id": users[3], "sessions": 2, "value": 10},
            {"user_id": users[4], "sessions": 1, "value": 10},
        ],
        [{"simple": 3, "total": 4}],
    )

    result = await dashboard.get_strategic_insights(make_db(), admin_user())

    assert [item.model_dump() for item in result.model_comparison] == [
        {
            "model": "popular",
            "sessions": 10,
            "avg_cost": 0.0,
            "avg_tokens": 1000,
            "success_rate": 80.0,
            "best_at": "Most popular, proven reliability",
        },
        {
            "model": "long",
            "sessions": 8,
            "avg_cost": 0.0,
            "avg_tokens": 6001,
            "success_rate": 0.0,
            "best_at": "Complex/long-context tasks",
        },
        {
            "model": "general",
            "sessions": 6,
            "avg_cost": 0.0,
            "avg_tokens": 2000,
            "success_rate": 50.0,
            "best_at": "General purpose",
        },
    ]
    assert [item.model_dump() for item in result.department_gaps] == [
        {
            "department": "Low",
            "adoption_pct": 25.0,
            "sessions": 4,
            "opportunity": "3 users not using AI \u2014 potential for automation",
        },
        {
            "department": "Moderate",
            "adoption_pct": 50.0,
            "sessions": 2,
            "opportunity": "Moderate adoption, room for deeper integration",
        },
        {
            "department": "High",
            "adoption_pct": 100.0,
            "sessions": 7,
            "opportunity": "High adoption \u2014 focus on optimization",
        },
    ]
    assert [item.model_dump() for item in result.platform_comparison] == [
        {"platform": "kiro", "avg_task_time_ms": 125.0, "sessions": 10, "success_rate": 80.0},
        {"platform": "pi", "avg_task_time_ms": 0.0, "sessions": 0, "success_rate": 0.0},
    ]
    assert result.quick_wins == []
    assert result.power_user_pct == 20
    assert result.power_user_value_pct == 50
    assert result.total_active_users == 5
    assert result.automatable_pct == 75
    assert ch.await_count == 6
    assert all("project_id = '{project_id}'" in call.args[0] for call in ch.await_args_list)


@pytest.mark.asyncio
async def test_strategic_insights_handle_empty_telemetry(monkeypatch):
    monkeypatch.setattr(dashboard, "resolve_user_departments", AsyncMock(return_value={}))
    ch_mock(monkeypatch, [], [], [], [], [], [])

    result = await dashboard.get_strategic_insights(make_db(), admin_user())

    assert result.model_comparison == []
    assert result.department_gaps == []
    assert result.platform_comparison == []
    assert result.power_user_value_pct == 0
    assert result.total_active_users == 0
    assert result.automatable_pct == 0


@pytest.mark.asyncio
async def test_developer_breakdown_ranks_activity_and_resolves_group_departments(monkeypatch):
    first = uuid.UUID("11111111-1111-4111-8111-111111111111")
    second = uuid.UUID("22222222-2222-4222-8222-222222222222")
    db = make_db(
        FakeResult(
            [
                row(id=first, name="Alice", department="Fallback"),
                row(id=second, name="Bob", department=None),
            ]
        ),
        scalar_values=[4],
    )
    ch_mock(
        monkeypatch,
        [
            {"user_id": str(first), "sessions": 10, "tokens": 100},
            {"user_id": "invalid", "sessions": 5, "tokens": 50},
            {"user_id": str(second), "sessions": 0, "tokens": 0},
        ],
    )
    monkeypatch.setattr(
        dashboard,
        "resolve_user_departments",
        AsyncMock(return_value={"Engineering": [str(first)], "Product": [str(second)]}),
    )

    result = await dashboard.get_developer_breakdown(3, db, admin_user())

    assert result.total_developers == 4
    assert result.active_developers == 3
    assert result.top_20_value_pct == 66.7
    assert [item.model_dump() for item in result.developers] == [
        {
            "user_id": str(first),
            "name": "Alice",
            "department": "Engineering",
            "sessions": 10,
            "tokens_consumed": 100,
            "cost": 0.0,
            "percentile": 100,
        },
        {
            "user_id": "invalid",
            "name": "Unknown",
            "department": "Unassigned",
            "sessions": 5,
            "tokens_consumed": 50,
            "cost": 0.0,
            "percentile": 67,
        },
        {
            "user_id": str(second),
            "name": "Bob",
            "department": "Product",
            "sessions": 0,
            "tokens_consumed": 0,
            "cost": 0.0,
            "percentile": 34,
        },
    ]
    assert "users.id IN" in sql_text(db.execute.await_args.args[0])


@pytest.mark.asyncio
async def test_developer_breakdown_handles_no_active_users(monkeypatch):
    db = make_db(scalar_values=[None])
    ch_mock(monkeypatch, [])
    departments = AsyncMock(return_value={})
    monkeypatch.setattr(dashboard, "resolve_user_departments", departments)

    result = await dashboard.get_developer_breakdown(20, db, admin_user())

    assert result.model_dump() == {
        "total_developers": 0,
        "active_developers": 0,
        "top_20_value_pct": 0.0,
        "developers": [],
    }
    db.execute.assert_not_awaited()
    departments.assert_awaited_once_with(db)


@pytest.mark.asyncio
async def test_inactivity_alerts_filter_recent_activity_and_resolve_names(monkeypatch):
    agent_id = uuid.UUID("11111111-1111-4111-8111-111111111111")
    recent_agent_id = uuid.UUID("22222222-2222-4222-8222-222222222222")
    missing_agent_id = uuid.UUID("33333333-3333-4333-8333-333333333333")
    user_id = uuid.UUID("44444444-4444-4444-8444-444444444444")
    recent_user_id = uuid.UUID("55555555-5555-4555-8555-555555555555")
    db = make_db(
        FakeResult([row(id=agent_id, name="Reviewer", category=None)]),
        FakeResult([row(id=user_id, name="Alice")]),
    )
    ch = ch_mock(
        monkeypatch,
        [
            {"agent_id": str(agent_id), "sessions": 7},
            {"agent_id": str(recent_agent_id), "sessions": 9},
            {"agent_id": str(missing_agent_id), "sessions": 8},
            {"agent_id": "invalid", "sessions": 6},
        ],
        [{"agent_id": str(recent_agent_id)}],
        [
            {"user_id": str(user_id), "sessions": 6},
            {"user_id": str(recent_user_id), "sessions": 5},
            {"user_id": "invalid", "sessions": 8},
        ],
        [{"user_id": str(recent_user_id)}],
    )
    monkeypatch.setattr(
        dashboard,
        "resolve_user_departments",
        AsyncMock(return_value={"Engineering": [str(user_id)]}),
    )

    result = await dashboard.get_inactivity_alerts(db, admin_user())

    assert [item.model_dump() for item in result.inactive_agents] == [
        {
            "id": str(agent_id),
            "name": "Reviewer",
            "category": "Uncategorized",
            "last_session_days_ago": 14,
            "previous_sessions": 7,
        }
    ]
    assert [item.model_dump() for item in result.inactive_users] == [
        {
            "user_id": str(user_id),
            "name": "Alice",
            "department": "Engineering",
            "last_session_days_ago": 14,
            "previous_sessions": 6,
        },
        {
            "user_id": "invalid",
            "name": "Unknown",
            "department": "Unassigned",
            "last_session_days_ago": 14,
            "previous_sessions": 8,
        },
    ]
    assert ch.await_count == 4
    assert "INTERVAL 28 DAY" in ch.await_args_list[0].args[0]
    assert "INTERVAL 14 DAY" in ch.await_args_list[1].args[0]


@pytest.mark.asyncio
async def test_time_to_value_calculates_milestones_sorts_and_ignores_bad_dates(monkeypatch):
    first = uuid.UUID("11111111-1111-4111-8111-111111111111")
    second = uuid.UUID("22222222-2222-4222-8222-222222222222")
    third = uuid.UUID("33333333-3333-4333-8333-333333333333")
    fourth = uuid.UUID("44444444-4444-4444-8444-444444444444")
    db = make_db(
        FakeResult(
            [
                row(id=first, name="First", category=None, created_at=datetime(2026, 1, 1)),
                row(id=second, name="Second", category="Build", created_at=datetime(2026, 1, 10, tzinfo=UTC)),
                row(id=third, name="Third", category="Search", created_at=datetime(2026, 1, 2)),
                row(id=fourth, name="Fourth", category=None, created_at=None),
            ]
        )
    )
    ch = ch_mock(
        monkeypatch,
        [
            {"agent_id": str(first), "first_session": "2026-01-01", "total_sessions": 120},
            {"agent_id": str(second), "first_session": "2026-01-02", "total_sessions": 115},
            {"agent_id": str(third), "first_session": "2026-01-03", "total_sessions": 110},
            {"agent_id": str(fourth), "first_session": "2026-01-04", "total_sessions": 50},
        ],
        [
            {"agent_id": str(first), "start_time": "2026-01-11T00:00:00Z"},
            {"agent_id": str(second), "start_time": "2026-01-05T00:00:00+00:00"},
            {"agent_id": str(third), "start_time": "invalid"},
        ],
    )

    result = await dashboard.get_time_to_value(db, admin_user())

    assert result.avg_days_to_100 == 5.0
    assert [item.model_dump() for item in result.agents] == [
        {
            "id": str(first),
            "name": "First",
            "category": "Uncategorized",
            "created_at": "2026-01-01",
            "days_to_100": 10,
            "current_sessions": 120,
        },
        {
            "id": str(second),
            "name": "Second",
            "category": "Build",
            "created_at": "2026-01-10",
            "days_to_100": 0,
            "current_sessions": 115,
        },
        {
            "id": str(third),
            "name": "Third",
            "category": "Search",
            "created_at": "2026-01-02",
            "days_to_100": None,
            "current_sessions": 110,
        },
        {
            "id": str(fourth),
            "name": "Fourth",
            "category": "Uncategorized",
            "created_at": "",
            "days_to_100": None,
            "current_sessions": 50,
        },
    ]
    assert "row_number() OVER" in ch.await_args_list[1].args[0]
    milestone_params = ch.await_args_list[1].args[1]
    assert str(first) in milestone_params["param_aids"]
    assert str(second) in milestone_params["param_aids"]


@pytest.mark.asyncio
async def test_time_to_value_reports_agents_that_have_not_reached_the_milestone(monkeypatch):
    agent_id = uuid.UUID("55555555-5555-4555-8555-555555555555")
    db = make_db(
        FakeResult(
            [row(id=agent_id, name="Nearly There", category="Build", created_at=datetime(2026, 1, 1, tzinfo=UTC))]
        )
    )
    ch = ch_mock(
        monkeypatch,
        [{"agent_id": str(agent_id), "first_session": "2026-01-02", "total_sessions": 99}],
    )

    result = await dashboard.get_time_to_value(db, admin_user())

    assert [item.model_dump() for item in result.agents] == [
        {
            "id": str(agent_id),
            "name": "Nearly There",
            "category": "Build",
            "created_at": "2026-01-01",
            "days_to_100": None,
            "current_sessions": 99,
        }
    ]
    assert result.avg_days_to_100 is None
    ch.assert_awaited_once()


@pytest.mark.asyncio
async def test_time_to_value_returns_early_without_agents(monkeypatch):
    db = make_db(FakeResult())
    ch = ch_mock(monkeypatch)

    result = await dashboard.get_time_to_value(db, admin_user())

    assert result.agents == []
    assert result.avg_days_to_100 is None
    ch.assert_not_awaited()


def ai_payload(**updates):
    payload = {
        "quick_wins": [{"title": "Automate review"}],
        "adoption_gaps": [{"department": "Security"}],
        "platform_insight": {"title": "Kiro"},
        "model_insight": {"title": "Model A"},
        "automation_opportunity": {"title": "Simple tasks"},
        "usage_pattern": {"title": "Morning"},
        "generated": True,
        "generated_at": "2026-06-01T00:00:00+00:00",
    }
    payload.update(updates)
    return payload


@pytest.mark.asyncio
async def test_get_ai_insights_returns_empty_or_valid_cached_report(monkeypatch):
    redis = SimpleNamespace(get=AsyncMock(side_effect=[None, json.dumps(ai_payload()).encode()]))
    monkeypatch.setattr(dashboard, "get_redis", lambda: redis)

    empty = await dashboard.get_ai_insights(admin_user())
    cached = await dashboard.get_ai_insights(admin_user())

    assert empty.generated is False
    assert empty.generated_at is None
    assert empty.platform_insight["title"] == "No cached report"
    assert cached.model_dump(mode="json") == ai_payload()
    assert dashboard._ai_insights_cache_key(admin_user()) == "exec.ai_insights"


@pytest.mark.asyncio
@pytest.mark.parametrize("cached", ["{broken", json.dumps({"generated": True})])
async def test_get_ai_insights_rejects_invalid_cached_reports(monkeypatch, cached):
    redis = SimpleNamespace(get=AsyncMock(return_value=cached))
    monkeypatch.setattr(dashboard, "get_redis", lambda: redis)

    with pytest.raises(HTTPException) as error:
        await dashboard.get_ai_insights(admin_user())

    assert error.value.status_code == 500
    assert error.value.detail == "Cached executive AI insights report is invalid"


@pytest.mark.asyncio
async def test_get_ai_insights_reports_redis_failure(monkeypatch):
    redis = SimpleNamespace(get=AsyncMock(side_effect=RedisError("offline")))
    monkeypatch.setattr(dashboard, "get_redis", lambda: redis)

    with pytest.raises(HTTPException) as error:
        await dashboard.get_ai_insights(admin_user())

    assert error.value.status_code == 503
    assert error.value.detail == "Executive AI insights cache is unavailable"


@pytest.mark.asyncio
async def test_generate_ai_insights_builds_business_metrics_and_caches_response(monkeypatch):
    first = "11111111-1111-1111-1111-111111111111"
    second = "22222222-2222-2222-2222-222222222222"
    db = make_db(scalar_values=[10])
    ch = ch_mock(
        monkeypatch,
        [{"active": 3}],
        [{"model": "model-a", "sessions": "8", "avg_tokens": "1250.5"}],
        [{"harness": "kiro", "sessions": "6", "users": "3", "avg_task_seconds": "12.5"}],
        [{"user_id": first, "sessions": 4}, {"user_id": second, "sessions": 0}],
        [{"simple": 3, "total": 5}],
        [
            {"user_id": first, "sessions": 4},
            {"user_id": second, "sessions": 2},
            {"user_id": "third", "sessions": 1},
        ],
    )
    monkeypatch.setattr(
        dashboard,
        "resolve_user_departments",
        AsyncMock(return_value={"Engineering": [first, second], "Unassigned": ["third"]}),
    )
    generated = {
        "quick_wins": [{"title": "Win"}],
        "adoption_gaps": [{"department": "Engineering"}],
        "platform_insight": {"title": "Platform"},
        "model_insight": {"title": "Model"},
        "automation_opportunity": {"title": "Automation"},
        "usage_pattern": {"title": "Usage"},
    }
    generate = AsyncMock(return_value=generated)
    monkeypatch.setattr("services.strategic_insights.generate_strategic_insights", generate)
    redis = SimpleNamespace(set=AsyncMock())
    monkeypatch.setattr(dashboard, "get_redis", lambda: redis)

    response = await dashboard.generate_ai_insights(db, admin_user())

    assert response.generated is True
    assert response.generated_at is not None
    assert response.quick_wins == [{"title": "Win"}]
    metrics = generate.await_args.args[0]
    assert metrics == {
        "adoption": {"total_users": 10, "active_users": 3, "adoption_pct": 30.0},
        "model_comparison": [{"model": "model-a", "sessions": 8, "avg_cost": 0.0, "avg_tokens": 1250}],
        "platform_comparison": [{"platform": "kiro", "sessions": 6, "users": 3, "avg_task_seconds": 12.5}],
        "department_gaps": [
            {
                "department": "Engineering",
                "users": 2,
                "active_users": 1,
                "adoption_pct": 50.0,
                "sessions": 4,
            }
        ],
        "quick_win_candidates": [],
        "automatable": {"simple_sessions": 3, "total_sessions": 5, "automatable_pct": 60.0},
        "developer_breakdown": {
            "total_active": 3,
            "total_sessions": 7,
            "total_cost": 0.0,
            "top_20_sessions": 4,
        },
    }
    redis.set.assert_awaited_once()
    cache_key, cached_json = redis.set.await_args.args
    assert cache_key == "exec.ai_insights"
    assert json.loads(cached_json)["generated"] is True
    assert ch.await_count == 6
    assert all("project_id = '{project_id}'" in call.args[0] for call in ch.await_args_list)


@pytest.mark.asyncio
async def test_generate_ai_insights_rejects_missing_model_result(monkeypatch):
    db = make_db(scalar_values=[None])
    ch_mock(monkeypatch, [], [], [], [], [], [])
    monkeypatch.setattr(dashboard, "resolve_user_departments", AsyncMock(return_value={}))
    monkeypatch.setattr("services.strategic_insights.generate_strategic_insights", AsyncMock(return_value=None))
    redis = SimpleNamespace(set=AsyncMock())
    monkeypatch.setattr(dashboard, "get_redis", lambda: redis)

    with pytest.raises(HTTPException) as error:
        await dashboard.generate_ai_insights(db, admin_user())

    assert error.value.status_code == 503
    assert error.value.detail == "Insights model is not configured or failed to generate a report"
    redis.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_generate_ai_insights_reports_cache_write_failure(monkeypatch):
    db = make_db(scalar_values=[1])
    ch_mock(monkeypatch, [], [], [], [], [], [])
    monkeypatch.setattr(dashboard, "resolve_user_departments", AsyncMock(return_value={}))
    monkeypatch.setattr(
        "services.strategic_insights.generate_strategic_insights",
        AsyncMock(return_value={"quick_wins": []}),
    )
    redis = SimpleNamespace(set=AsyncMock(side_effect=RedisError("offline")))
    monkeypatch.setattr(dashboard, "get_redis", lambda: redis)

    with pytest.raises(HTTPException) as error:
        await dashboard.generate_ai_insights(db, admin_user())

    assert error.value.status_code == 503
    assert error.value.detail == "Executive AI insights cache is unavailable"


@pytest.mark.asyncio
async def test_postgres_and_clickhouse_failures_are_not_silently_hidden(monkeypatch):
    db = make_db()
    db.execute.side_effect = RuntimeError("postgres unavailable")

    with pytest.raises(RuntimeError, match="postgres unavailable"):
        await dashboard.get_exec_config(db, admin_user())

    monkeypatch.setattr(dashboard, "_ch_json", AsyncMock(side_effect=RuntimeError("clickhouse unavailable")))
    with pytest.raises(RuntimeError, match="clickhouse unavailable"):
        await dashboard.get_platform_coverage(admin_user())


def test_every_exec_route_requires_admin_role():
    routes = [route for route in dashboard.router.routes if isinstance(route, APIRoute)]

    assert routes
    for route in routes:
        current_user_dependencies = [
            dependency for dependency in route.dependant.dependencies if dependency.name == "current_user"
        ]
        assert len(current_user_dependencies) == 1, route.path
        closure = inspect.getclosurevars(current_user_dependencies[0].call)
        assert closure.nonlocals["min_role"] == UserRole.admin, route.path


async def make_auth_app(user=None):
    app = FastAPI()
    app.include_router(dashboard.router)

    async def database_override():
        yield make_db(FakeResult(scalar=None), scalar_values=[0, 0, 0])

    app.dependency_overrides[get_db] = database_override
    if user is not None:

        async def user_override():
            return user

        app.dependency_overrides[get_current_user] = user_override
    return app


@pytest.mark.asyncio
async def test_exec_routes_reject_missing_credentials_and_non_admins(monkeypatch):
    unauthenticated = await make_auth_app()
    async with AsyncClient(transport=ASGITransport(app=unauthenticated), base_url="http://test") as client:
        response = await client.get("/api/v1/exec/roi-projections")
    assert response.status_code == 401
    assert response.json() == {"detail": "Missing credentials"}

    reviewer = SimpleNamespace(id=uuid.uuid4(), email="reviewer@example.test", role=UserRole.reviewer)
    forbidden = await make_auth_app(reviewer)
    emit = AsyncMock()
    monkeypatch.setattr("api.deps.emit_security_event", emit)
    async with AsyncClient(transport=ASGITransport(app=forbidden), base_url="http://test") as client:
        response = await client.get("/api/v1/exec/roi-projections")
    assert response.status_code == 403
    assert response.json() == {"detail": "Insufficient permissions"}
    emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_exec_route_serialization_and_query_validation():
    app = await make_auth_app(admin_user())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/exec/roi-projections")
        top_agents = await client.get("/api/v1/exec/top-agents", params={"limit": 51})
        developers = await client.get("/api/v1/exec/developer-breakdown", params={"limit": 101})
        config = await client.put("/api/v1/exec/config", json={"hourly_dev_cost": "not-a-number"})

    assert response.status_code == 200
    assert response.json() == {
        "projections": [],
        "growth_rate_pct": 0.0,
        "time_to_breakeven_months": None,
        "total_invested": 0.0,
        "total_saved": 0.0,
        "roi_multiple": 0.0,
    }
    assert top_agents.status_code == 422
    assert top_agents.json()["detail"][0]["loc"][-1] == "limit"
    assert developers.status_code == 422
    assert developers.json()["detail"][0]["loc"][-1] == "limit"
    assert config.status_code == 422
    assert config.json()["detail"][0]["loc"][-1] == "hourly_dev_cost"
