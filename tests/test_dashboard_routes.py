# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from api.deps import get_current_user, get_db, get_registry_user
from api.routes import dashboard
from models.user import UserRole

AGENT_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
SECOND_AGENT_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
USER_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
SECOND_USER_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
NOW = datetime(2026, 6, 15, 12, 30, tzinfo=UTC)


class _Scalars:
    def __init__(self, values=()):
        self.values = list(values)

    def all(self):
        return self.values


class _Result:
    def __init__(self, rows=(), *, scalars=None):
        self.rows = list(rows)
        self.scalar_values = self.rows if scalars is None else list(scalars)

    def all(self):
        return self.rows

    def scalars(self):
        return _Scalars(self.scalar_values)


def _db(*results, scalar_values=()):
    db = MagicMock()
    db.execute = AsyncMock(side_effect=list(results))
    db.scalar = AsyncMock(side_effect=list(scalar_values))
    return db


def _sql(statement) -> str:
    return " ".join(str(statement.compile(compile_kwargs={"literal_binds": True})).split())


def _user(role: UserRole = UserRole.user, user_id: uuid.UUID = USER_ID):
    return SimpleNamespace(
        id=user_id,
        email="user@example.test",
        username="user-name",
        auth_provider="local",
        role=role,
    )


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


@pytest.mark.parametrize(
    ("range_", "expected"),
    [("24h", 1), ("7d", 7), ("30d", 30), ("90d", 90), (None, 7), ("invalid", 7)],
)
def test_range_days_maps_supported_periods_and_defaults(range_, expected):
    assert dashboard._range_days(range_) == expected


@pytest.mark.asyncio
async def test_ch_json_rewrites_project_adds_final_setting_and_passes_exact_query(monkeypatch):
    response = MagicMock(status_code=200)
    response.json.return_value = {"data": [{"cnt": "3"}], "meta": []}
    query = AsyncMock(return_value=response)
    monkeypatch.setattr(dashboard, "_query", query)
    sql = "SELECT count() AS cnt FROM events FINAL WHERE project_id = '{project_id}'"
    params = {"param_days": "7"}

    result = await dashboard._ch_json(sql, params)

    assert result == [{"cnt": "3"}]
    query.assert_awaited_once_with(
        "SELECT count() AS cnt FROM events FINAL WHERE project_id = 'default' "
        "SETTINGS do_not_merge_across_partitions_select_final = 1 FORMAT JSON",
        params,
    )
    assert sql == "SELECT count() AS cnt FROM events FINAL WHERE project_id = '{project_id}'"
    assert params == {"param_days": "7"}


@pytest.mark.asyncio
async def test_ch_json_preserves_existing_settings_and_maps_failures_to_empty(monkeypatch):
    successful = MagicMock(status_code=200)
    successful.json.return_value = {}
    unavailable = MagicMock(status_code=503)
    query = AsyncMock(side_effect=[successful, unavailable, RuntimeError("clickhouse unavailable")])
    monkeypatch.setattr(dashboard, "_query", query)
    sql = "SELECT 1 FROM events FINAL SETTINGS max_final_threads = 2"

    assert await dashboard._ch_json(sql) == []
    assert await dashboard._ch_json("SELECT unavailable") == []
    assert await dashboard._ch_json("SELECT broken") == []
    assert query.await_args_list == [
        call(f"{sql} FORMAT JSON", None),
        call("SELECT unavailable FORMAT JSON", None),
        call("SELECT broken FORMAT JSON", None),
    ]


@pytest.mark.asyncio
async def test_overview_stats_aggregates_postgres_and_clickhouse_with_exact_queries(monkeypatch):
    db = _db(scalar_values=[4, 3, 9])
    ch = AsyncMock(side_effect=[[{"cnt": "17"}], [{"cnt": 6}]])
    monkeypatch.setattr(dashboard, "_ch_json", ch)

    result = await dashboard.overview_stats(range_="30d", db=db, current_user=None)

    assert result.model_dump() == {
        "total_mcps": 4,
        "total_agents": 3,
        "total_users": 9,
        "total_tool_calls": 17,
        "total_agent_interactions": 6,
    }
    scalar_sql = [_sql(item.args[0]) for item in db.scalar.call_args_list]
    assert scalar_sql == [
        "SELECT count(mcp_listings.id) AS count_1 FROM mcp_listings JOIN mcp_versions ON "
        "mcp_listings.latest_version_id = mcp_versions.id WHERE mcp_versions.status = 'approved' "
        "AND mcp_listings.is_private = false",
        "SELECT count(agents.id) AS count_1 FROM agents JOIN agent_versions ON "
        "agents.latest_version_id = agent_versions.id WHERE agent_versions.status = 'approved' "
        "AND agents.deleted_at IS NULL AND agents.is_private = false",
        "SELECT count(users.id) AS count_1 FROM users",
    ]
    assert ch.await_args_list == [
        call(
            "SELECT sum(tool_call_count) as cnt FROM session_stats_agg FINAL WHERE last_event_time > "
            "now() - INTERVAL {days:UInt32} DAY",
            {"param_days": "30"},
        ),
        call(
            "SELECT count() as cnt FROM session_stats_agg FINAL WHERE last_event_time > "
            "now() - INTERVAL {days:UInt32} DAY",
            {"param_days": "30"},
        ),
    ]


@pytest.mark.asyncio
async def test_overview_stats_returns_zeroes_for_empty_aggregates(monkeypatch):
    db = _db(scalar_values=[None, 0, None])
    monkeypatch.setattr(dashboard, "_ch_json", AsyncMock(side_effect=[[], []]))

    result = await dashboard.overview_stats(range_=None, db=db, current_user=_user())

    assert result.model_dump() == {
        "total_mcps": 0,
        "total_agents": 0,
        "total_users": 0,
        "total_tool_calls": 0,
        "total_agent_interactions": 0,
    }
    scoped_sql = [_sql(item.args[0]) for item in db.scalar.call_args_list[:2]]
    assert all("team_memberships" in sql for sql in scoped_sql)
    assert all(USER_ID.hex in sql for sql in scoped_sql)


@pytest.mark.asyncio
async def test_overview_stats_exposes_null_clickhouse_aggregate_as_invalid(monkeypatch):
    db = _db(scalar_values=[0, 0, 0])
    monkeypatch.setattr(dashboard, "_ch_json", AsyncMock(side_effect=[[{"cnt": None}], []]))

    with pytest.raises(TypeError):
        await dashboard.overview_stats(range_="7d", db=db, current_user=None)


@pytest.mark.asyncio
async def test_top_mcps_transforms_ordered_rows_without_mutating_them():
    rows = [
        SimpleNamespace(listing_id=AGENT_ID, name="Search", cnt=7),
        SimpleNamespace(listing_id=SECOND_AGENT_ID, name="Review", cnt=3),
    ]
    snapshots = [vars(item).copy() for item in rows]
    db = _db(_Result(rows))

    result = await dashboard.top_mcps(db=db, current_user=None)

    assert [item.model_dump(mode="json") for item in result] == [
        {"id": str(AGENT_ID), "name": "Search", "value": 7.0},
        {"id": str(SECOND_AGENT_ID), "name": "Review", "value": 3.0},
    ]
    assert _sql(db.execute.await_args.args[0]) == (
        "SELECT mcp_downloads.listing_id, count(mcp_downloads.id) AS cnt, mcp_listings.name "
        "FROM mcp_downloads JOIN mcp_listings ON mcp_downloads.listing_id = mcp_listings.id "
        "WHERE mcp_listings.is_private = false GROUP BY mcp_downloads.listing_id, mcp_listings.name "
        "ORDER BY count(mcp_downloads.id) DESC LIMIT 5"
    )
    assert [vars(item) for item in rows] == snapshots


@pytest.mark.asyncio
async def test_top_agents_batches_ratings_and_transforms_nullable_fields():
    rows = [
        SimpleNamespace(
            agent_id=AGENT_ID,
            cnt=8,
            name="Reviewer",
            namespace="alice",
            slug="reviewer",
            description=None,
            owner=None,
            version=None,
        ),
        SimpleNamespace(
            agent_id=SECOND_AGENT_ID,
            cnt=2,
            name="Builder",
            namespace="platform",
            slug="builder",
            description="Builds things",
            owner="Platform",
            version="2.0.0",
        ),
    ]
    db = _db(_Result(rows), _Result([(AGENT_ID, Decimal("4.236"))]))

    result = await dashboard.top_agents(limit=2, db=db, current_user=None)

    assert [item.model_dump(mode="json") for item in result] == [
        {
            "id": str(AGENT_ID),
            "name": "Reviewer",
            "namespace": "alice",
            "slug": "reviewer",
            "qualified_name": "alice/reviewer",
            "description": "",
            "owner": "",
            "created_by_username": None,
            "version": "",
            "download_count": 8,
            "average_rating": 4.24,
        },
        {
            "id": str(SECOND_AGENT_ID),
            "name": "Builder",
            "namespace": "platform",
            "slug": "builder",
            "qualified_name": "platform/builder",
            "description": "Builds things",
            "owner": "Platform",
            "created_by_username": None,
            "version": "2.0.0",
            "download_count": 2,
            "average_rating": None,
        },
    ]
    assert db.execute.await_count == 2
    main_sql = _sql(db.execute.await_args_list[0].args[0])
    assert "agent_versions.status = 'approved'" in main_sql
    assert "agents.deleted_at IS NULL" in main_sql
    assert main_sql.endswith("ORDER BY count(agent_download_records.id) DESC LIMIT 2")
    rating_sql = _sql(db.execute.await_args_list[1].args[0])
    assert rating_sql == (
        f"SELECT feedback.listing_id, avg(feedback.rating) AS avg_1 FROM feedback WHERE "
        f"feedback.listing_id IN ('{AGENT_ID.hex}', '{SECOND_AGENT_ID.hex}') AND "
        "feedback.listing_type = 'agent' GROUP BY feedback.listing_id"
    )


@pytest.mark.asyncio
async def test_agent_leaderboard_filters_period_and_user_and_serializes_rows(monkeypatch):
    monkeypatch.setattr(dashboard, "dt", _FrozenDateTime)
    source = SimpleNamespace(
        agent_id=AGENT_ID,
        cnt=12,
        name="Reviewer",
        namespace="alice",
        slug="reviewer",
        description=None,
        owner=None,
        version=None,
        created_by=USER_ID,
    )
    snapshot = vars(source).copy()
    db = _db(
        _Result([source]),
        _Result([(AGENT_ID, Decimal("4.555"))]),
        _Result([(USER_ID, "alice@example.test", "alice")]),
    )

    result = await dashboard.agent_leaderboard(
        window="30d",
        limit=4,
        user=r"alice%_\team",
        db=db,
        current_user=_user(),
    )

    assert [item.model_dump(mode="json") for item in result] == [
        {
            "id": str(AGENT_ID),
            "name": "Reviewer",
            "namespace": "alice",
            "slug": "reviewer",
            "qualified_name": "alice/reviewer",
            "description": "",
            "owner": "",
            "created_by_username": "alice",
            "version": "",
            "download_count": 12,
            "average_rating": 4.55,
            "created_by_email": "alice@example.test",
        }
    ]
    main = db.execute.await_args_list[0].args[0]
    main_params = main.compile().params
    assert main_params["email_1"] == r"%alice\%\_\\team%"
    assert main_params["installed_at_1"] == NOW - timedelta(days=30)
    assert main_params["param_1"] == 4
    main_sql = _sql(main)
    assert "JOIN users ON agents.created_by = users.id" in main_sql
    assert "agent_download_records.installed_at >= '2026-05-16 12:30:00+00:00'" in main_sql
    assert "ORDER BY count(agent_download_records.id) DESC LIMIT 4" in main_sql
    assert vars(source) == snapshot


@pytest.mark.asyncio
async def test_anonymous_agent_leaderboard_ignores_email_filter():
    db = _db(_Result())

    result = await dashboard.agent_leaderboard(
        window="7d",
        limit=4,
        user="private@example.test",
        db=db,
        current_user=None,
    )

    assert result == []
    sql = _sql(db.execute.await_args_list[0].args[0])
    assert "JOIN users" not in sql
    assert "private@example.test" not in str(db.execute.await_args_list[0].args[0].compile().params)


@pytest.mark.asyncio
async def test_agent_leaderboard_backfills_zero_download_agents_and_missing_creators():
    extra = SimpleNamespace(
        id=SECOND_AGENT_ID,
        name="New Agent",
        namespace="platform",
        slug="new-agent",
        qualified_name="platform/new-agent",
        description=None,
        owner=None,
        version=None,
        created_by=SECOND_USER_ID,
    )
    snapshot = vars(extra).copy()
    db = _db(
        _Result(),
        _Result(scalars=[extra]),
        _Result([(SECOND_USER_ID, "owner@example.test", None)]),
    )

    result = await dashboard.agent_leaderboard(
        window="all",
        limit=2,
        user="owner",
        db=db,
        current_user=_user(),
    )

    assert [item.model_dump(mode="json") for item in result] == [
        {
            "id": str(SECOND_AGENT_ID),
            "name": "New Agent",
            "namespace": "platform",
            "slug": "new-agent",
            "qualified_name": "platform/new-agent",
            "description": "",
            "owner": "",
            "created_by_username": None,
            "version": "",
            "download_count": 0,
            "average_rating": None,
            "created_by_email": "owner@example.test",
        }
    ]
    assert db.execute.await_count == 3
    extra_sql = _sql(db.execute.await_args_list[1].args[0])
    assert "agent_versions.status = 'approved'" in extra_sql
    assert "agents.deleted_at IS NULL" in extra_sql
    assert "JOIN users ON agents.created_by = users.id" in extra_sql
    assert "ORDER BY agents.created_at DESC LIMIT 2" in extra_sql
    assert "agent_download_records.installed_at" not in extra_sql
    assert vars(extra) == snapshot


@pytest.mark.asyncio
async def test_component_leaderboard_aggregates_all_types_ratings_and_emails(monkeypatch):
    monkeypatch.setattr(dashboard, "dt", _FrozenDateTime)
    component_ids = [uuid.UUID(f"00000000-0000-4000-8000-{index:012d}") for index in range(1, 6)]
    labels = ["mcp", "skill", "hook", "prompt", "sandbox"]
    rows = [
        SimpleNamespace(
            component_id=component_id,
            cnt=count,
            name=label.title(),
            namespace="platform",
            slug=f"{label}-item",
            description=None if label == "hook" else f"{label} description",
            submitted_by=None if label == "sandbox" else USER_ID,
        )
        for component_id, label, count in zip(component_ids, labels, [5, 5, 3, 2, 1], strict=True)
    ]
    snapshots = [vars(item).copy() for item in rows]
    feedback = [
        SimpleNamespace(listing_id=component_ids[0], avg_rating=Decimal("4.236"), total_reviews=1),
        SimpleNamespace(listing_id=component_ids[1], avg_rating=Decimal("4.5"), total_reviews=3),
        SimpleNamespace(listing_id=component_ids[2], avg_rating=None, total_reviews=2),
    ]
    db = _db(
        *[_Result([item]) for item in rows],
        _Result(feedback),
        _Result([(USER_ID, "creator@example.test")]),
    )

    result = await dashboard.component_leaderboard(
        window="24h",
        limit=5,
        user=r"creator%_\team",
        db=db,
        current_user=_user(),
    )

    assert [(item.component_type, item.download_count, item.total_reviews) for item in result] == [
        ("skill", 5, 3),
        ("mcp", 5, 1),
        ("hook", 3, 2),
        ("prompt", 2, 0),
        ("sandbox", 1, 0),
    ]
    assert result[0].model_dump(mode="json") == {
        "id": str(component_ids[1]),
        "name": "Skill",
        "namespace": "platform",
        "slug": "skill-item",
        "qualified_name": "platform/skill-item",
        "component_type": "skill",
        "description": "skill description",
        "download_count": 5,
        "created_by_email": "creator@example.test",
        "average_rating": 4.5,
        "total_reviews": 3,
    }
    assert result[2].description == ""
    assert result[2].average_rating is None
    assert db.execute.await_count == 7
    tables = ["mcp_listings", "skill_listings", "hook_listings", "prompt_listings", "sandbox_listings"]
    for index, (label, table) in enumerate(zip(labels, tables, strict=True)):
        statement = db.execute.await_args_list[index].args[0]
        sql = _sql(statement)
        params = statement.compile().params
        assert f"agent_components.component_type = '{label}'" in sql
        assert f"{table}.is_private = false" in sql
        assert "agents.is_private = false" in sql
        assert "agent_download_records.installed_at >= '2026-06-14 12:30:00+00:00'" in sql
        assert params["email_1"] == r"%creator\%\_\\team%"
        assert sql.endswith("DESC LIMIT 5")
    feedback_sql = _sql(db.execute.await_args_list[5].args[0])
    assert "avg(feedback.rating) AS avg_rating" in feedback_sql
    assert "count(feedback.id) AS total_reviews" in feedback_sql
    assert [vars(item) for item in rows] == snapshots


@pytest.mark.asyncio
async def test_anonymous_component_leaderboard_hides_email_and_ignores_filter():
    component_id = uuid.UUID("00000000-0000-4000-8000-000000000099")
    row = SimpleNamespace(
        component_id=component_id,
        cnt=1,
        name="Public MCP",
        namespace="public",
        slug="public-mcp",
        description="Public component",
        submitted_by=USER_ID,
    )
    db = _db(
        _Result([row]),
        _Result(),
        _Result(),
        _Result(),
        _Result(),
        _Result(),
    )

    result = await dashboard.component_leaderboard(
        window="7d",
        limit=1,
        user="private@example.test",
        db=db,
        current_user=None,
    )

    assert len(result) == 1
    assert result[0].created_by_email == ""
    assert db.execute.await_count == 6
    for awaited in db.execute.await_args_list[:5]:
        statement = awaited.args[0]
        assert "JOIN users" not in _sql(statement)
        assert "private@example.test" not in str(statement.compile().params)


@pytest.mark.asyncio
async def test_component_leaderboard_backfills_deduplicates_and_stops_at_limit():
    first_id = uuid.UUID("10000000-0000-4000-8000-000000000001")
    second_id = uuid.UUID("10000000-0000-4000-8000-000000000002")
    third_id = uuid.UUID("10000000-0000-4000-8000-000000000003")
    first = SimpleNamespace(
        id=first_id,
        name="First",
        namespace="alice",
        slug="first",
        description=None,
        submitted_by=USER_ID,
    )
    duplicate = SimpleNamespace(**vars(first))
    second = SimpleNamespace(
        id=second_id,
        name="Second",
        namespace="alice",
        slug="second",
        description="Second item",
        submitted_by=SECOND_USER_ID,
    )
    third = SimpleNamespace(
        id=third_id,
        name="Third",
        namespace="alice",
        slug="third",
        description="Third item",
        submitted_by=None,
    )
    extras = [first, duplicate, second, third]
    snapshots = [vars(item).copy() for item in extras]
    db = _db(
        _Result(),
        _Result(),
        _Result(),
        _Result(),
        _Result(),
        _Result(extras),
        _Result([(USER_ID, "first@example.test"), (SECOND_USER_ID, "second@example.test")]),
    )

    result = await dashboard.component_leaderboard(
        window="all",
        limit=3,
        user=None,
        db=db,
        current_user=_user(),
    )

    assert [item.id for item in result] == [first_id, second_id, third_id]
    assert [item.download_count for item in result] == [0, 0, 0]
    assert [item.created_by_email for item in result] == ["first@example.test", "second@example.test", ""]
    assert [item.component_type for item in result] == ["mcp", "mcp", "mcp"]
    assert result[0].description == ""
    assert db.execute.await_count == 7
    extra_sql = _sql(db.execute.await_args_list[5].args[0])
    assert "mcp_versions.status = 'approved'" in extra_sql
    assert "mcp_listings.is_private = false" in extra_sql
    assert "ORDER BY mcp_listings.created_at DESC LIMIT 3" in extra_sql
    assert [vars(item) for item in extras] == snapshots


@pytest.mark.asyncio
async def test_trends_merges_days_sorts_and_uses_exact_period(monkeypatch):
    monkeypatch.setattr(dashboard, "dt", _FrozenDateTime)
    db = _db(
        _Result(
            [
                SimpleNamespace(day=datetime(2026, 6, 10, tzinfo=UTC), cnt=2),
                SimpleNamespace(day=datetime(2026, 6, 12, tzinfo=UTC), cnt=4),
            ]
        ),
        _Result(
            [
                SimpleNamespace(day=datetime(2026, 6, 11, tzinfo=UTC), cnt=3),
                SimpleNamespace(day=datetime(2026, 6, 12, tzinfo=UTC), cnt=1),
            ]
        ),
    )

    result = await dashboard.trends(range_="7d", db=db, current_user=_user(UserRole.admin))

    assert [item.model_dump() for item in result] == [
        {"date": "2026-06-10", "submissions": 2, "users": 0},
        {"date": "2026-06-11", "submissions": 0, "users": 3},
        {"date": "2026-06-12", "submissions": 4, "users": 1},
    ]
    assert [_sql(item.args[0]) for item in db.execute.await_args_list] == [
        "SELECT date_trunc('day', mcp_listings.created_at) AS day, count(mcp_listings.id) AS cnt "
        "FROM mcp_listings WHERE mcp_listings.created_at >= '2026-06-08 12:30:00+00:00' "
        "GROUP BY date_trunc('day', mcp_listings.created_at) ORDER BY day",
        "SELECT date_trunc('day', users.created_at) AS day, count(users.id) AS cnt FROM users "
        "WHERE users.created_at >= '2026-06-08 12:30:00+00:00' GROUP BY date_trunc('day', users.created_at) "
        "ORDER BY day",
    ]


@pytest.mark.asyncio
async def test_trends_rejects_null_aggregate_days():
    db = _db(_Result([SimpleNamespace(day=None, cnt=1)]), _Result())

    with pytest.raises(AttributeError):
        await dashboard.trends(range_="7d", db=db, current_user=_user(UserRole.admin))


@pytest.mark.asyncio
async def test_placeholder_dashboard_endpoints_return_exact_empty_payloads():
    admin = _user(UserRole.admin)

    tokens = await dashboard.token_stats(range_="90d")
    harnesses = await dashboard.harness_usage(admin)
    sandboxes = await dashboard.sandbox_metrics(admin)
    graphrag = await dashboard.graphrag_metrics(admin)
    latency = await dashboard.latency_heatmap(admin)
    traces = await dashboard.unannotated_traces(admin)

    assert tokens.model_dump() == {
        "total_input": 0,
        "total_output": 0,
        "total_tokens": 0,
        "avg_per_trace": 0.0,
        "by_agent": [],
        "by_mcp": [],
        "over_time": [],
    }
    assert harnesses.model_dump() == {"harnesses": []}
    assert sandboxes.model_dump() == {
        "total_runs": 0,
        "oom_count": 0,
        "oom_rate": 0.0,
        "timeout_count": 0,
        "timeout_rate": 0.0,
        "avg_exit_code": None,
        "recent_runs": [],
        "cpu_over_time": [],
        "memory_over_time": [],
    }
    assert graphrag.model_dump() == {
        "total_queries": 0,
        "avg_entities": None,
        "avg_relationships": None,
        "avg_relevance_score": None,
        "avg_embedding_latency_ms": None,
        "relevance_distribution": [],
        "recent_queries": [],
    }
    assert latency == []
    assert traces == []


def test_dashboard_route_role_contract_is_explicit():
    routes = {route.path: route for route in dashboard.router.routes if isinstance(route, APIRoute)}
    admin_paths = {
        "/api/v1/overview/trends",
        "/api/v1/dashboard/tokens",
        "/api/v1/dashboard/harness-usage",
        "/api/v1/dashboard/sandbox-metrics",
        "/api/v1/dashboard/graphrag-metrics",
        "/api/v1/dashboard/latency-heatmap",
        "/api/v1/dashboard/unannotated-traces",
    }

    user_paths = {"/api/v1/overview/stats"}
    public_scoped_paths = {
        "/api/v1/overview/top-mcps",
        "/api/v1/overview/top-agents",
        "/api/v1/overview/leaderboard",
        "/api/v1/overview/component-leaderboard",
    }

    assert admin_paths | user_paths | public_scoped_paths <= routes.keys()
    for path in admin_paths:
        dependencies = [item for item in routes[path].dependant.dependencies if item.name == "current_user"]
        assert len(dependencies) == 1, path
        assert inspect.getclosurevars(dependencies[0].call).nonlocals["min_role"] == UserRole.admin
    for path in user_paths:
        dependencies = [item for item in routes[path].dependant.dependencies if item.name == "current_user"]
        assert len(dependencies) == 1, path
        assert inspect.getclosurevars(dependencies[0].call).nonlocals["min_role"] == UserRole.user
    for path in public_scoped_paths:
        dependencies = [item for item in routes[path].dependant.dependencies if item.name == "current_user"]
        assert [item.call for item in dependencies] == [get_registry_user], path


async def _dashboard_app(user=None):
    app = FastAPI()
    app.include_router(dashboard.router)
    database = _db(_Result(), _Result(), scalar_values=[0, 0, 0])

    async def database_override():
        yield database

    app.dependency_overrides[get_db] = database_override

    async def registry_user_override():
        return user

    app.dependency_overrides[get_registry_user] = registry_user_override
    if user is not None:

        async def user_override():
            return user

        app.dependency_overrides[get_current_user] = user_override
    return app, database


@pytest.mark.asyncio
async def test_admin_dashboard_routes_reject_missing_credentials_and_non_admins(monkeypatch):
    paths = [
        "/api/v1/overview/trends",
        "/api/v1/dashboard/tokens",
        "/api/v1/dashboard/harness-usage",
        "/api/v1/dashboard/sandbox-metrics",
        "/api/v1/dashboard/graphrag-metrics",
        "/api/v1/dashboard/latency-heatmap",
        "/api/v1/dashboard/unannotated-traces",
    ]
    anonymous_app, anonymous_db = await _dashboard_app()
    async with AsyncClient(transport=ASGITransport(app=anonymous_app), base_url="http://test") as client:
        anonymous = [await client.get(path) for path in paths]
    assert [(response.status_code, response.json()) for response in anonymous] == [
        (401, {"detail": "Missing credentials"})
    ] * len(paths)
    anonymous_db.execute.assert_not_awaited()

    reviewer_app, reviewer_db = await _dashboard_app(_user(UserRole.reviewer))
    emit = AsyncMock()
    monkeypatch.setattr("api.deps.emit_security_event", emit)
    async with AsyncClient(transport=ASGITransport(app=reviewer_app), base_url="http://test") as client:
        forbidden = [await client.get(path) for path in paths]
    assert [(response.status_code, response.json()) for response in forbidden] == [
        (403, {"detail": "Insufficient permissions"})
    ] * len(paths)
    assert emit.await_count == len(paths)
    reviewer_db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_dashboard_query_validation_does_not_touch_database():
    app, db = await _dashboard_app()
    # Validate leaderboard query params with the Leaderboard enabled; while off the routes return 404.
    app.dependency_overrides[dashboard.require_leaderboard_enabled] = lambda: None

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        invalid = [
            await client.get("/api/v1/overview/top-agents", params={"limit": 51}),
            await client.get("/api/v1/overview/leaderboard", params={"window": "1d"}),
            await client.get("/api/v1/overview/leaderboard", params={"limit": 51}),
            await client.get("/api/v1/overview/component-leaderboard", params={"window": "1d"}),
            await client.get("/api/v1/overview/component-leaderboard", params={"limit": 51}),
        ]

    assert [response.status_code for response in invalid] == [422, 422, 422, 422, 422]
    assert [response.json()["detail"][0]["loc"][-1] for response in invalid] == [
        "limit",
        "window",
        "limit",
        "window",
        "limit",
    ]
    db.execute.assert_not_awaited()
    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_postgres_failures_propagate_without_partial_dashboard_results(monkeypatch):
    db = _db()
    db.execute.side_effect = RuntimeError("postgres unavailable")

    with pytest.raises(RuntimeError, match="postgres unavailable"):
        await dashboard.top_mcps(db=db, current_user=None)

    scalar_db = _db()
    scalar_db.scalar.side_effect = RuntimeError("postgres unavailable")
    monkeypatch.setattr(dashboard, "_ch_json", AsyncMock(side_effect=[[], []]))
    with pytest.raises(RuntimeError, match="postgres unavailable"):
        await dashboard.overview_stats(range_="7d", db=scalar_db, current_user=None)
