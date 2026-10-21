# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from redis.exceptions import RedisError

from api.deps import get_current_user
from api.routes import sessions
from models.user import UserRole
from services.user_search import UserFilterValues

USER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER_USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
AGENT_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _sql(value) -> str:
    return " ".join(str(value).split())


LIST_SQL_FILTERED = (
    "SELECT session_id, if(first_event_time > '2020-01-01 00:00:00' AND first_event_time < "
    "'2099-01-01 00:00:00',    first_event_time, last_event_time) AS first_event_time, "
    "if(last_event_time < '2099-01-01 00:00:00', last_event_time, first_event_time) AS last_event_time, "
    "(if(harness = 'kiro', greatest(last_event_time, last_source_ingest_at), last_event_time) > now() - INTERVAL "
    "5 MINUTE) AS is_active, prompt_count, 0                   AS api_request_count, tool_result_count, "
    "input_tokens        AS total_input_tokens, output_tokens       AS total_output_tokens, "
    "cache_read_tokens   AS total_cache_read_tokens, cache_write_tokens  AS total_cache_write_tokens, "
    "total_credits, model, harness, agent_id, agent_version, user_id FROM session_stats_agg FINAL "
    "WHERE session_id != '' AND parent_session_id = '' AND prompt_count > 0 AND user_id = {uid:String} "
    "AND if(harness = 'kiro', greatest(last_event_time, last_source_ingest_at), last_event_time) > now() - INTERVAL 365 DAY AND harness = {platform:String} "
    "AND user_id IN ({user_0:String}, {user_1:String}) ORDER BY "
    "if(harness = 'kiro', greatest(last_event_time, last_source_ingest_at), last_event_time) DESC LIMIT 25 OFFSET 10"
)
LIST_SQL_UNFILTERED = (
    "SELECT session_id, if(first_event_time > '2020-01-01 00:00:00' AND first_event_time < "
    "'2099-01-01 00:00:00',    first_event_time, last_event_time) AS first_event_time, "
    "if(last_event_time < '2099-01-01 00:00:00', last_event_time, first_event_time) AS last_event_time, "
    "(if(harness = 'kiro', greatest(last_event_time, last_source_ingest_at), last_event_time) > now() - INTERVAL "
    "5 MINUTE) AS is_active, prompt_count, 0                   AS api_request_count, tool_result_count, "
    "input_tokens        AS total_input_tokens, output_tokens       AS total_output_tokens, "
    "cache_read_tokens   AS total_cache_read_tokens, cache_write_tokens  AS total_cache_write_tokens, "
    "total_credits, model, harness, agent_id, agent_version, user_id FROM session_stats_agg FINAL "
    "WHERE session_id != '' AND parent_session_id = '' AND prompt_count > 0 "
    "ORDER BY if(harness = 'kiro', greatest(last_event_time, last_source_ingest_at), last_event_time) DESC LIMIT 50 OFFSET 0"
)
IDENTITY_SQL_USER = (
    "SELECT project_id, user_id, harness FROM session_events FINAL WHERE session_id = {sid:String} "
    "AND user_id = {uid:String} ORDER BY ingested_at DESC LIMIT 1"
)
IDENTITY_SQL_ADMIN = (
    "SELECT project_id, user_id, harness FROM session_events FINAL WHERE session_id = {sid:String} "
    "ORDER BY ingested_at DESC LIMIT 1"
)
MAIN_SQL = (
    "SELECT line_offset, timestamp, event_type, content_preview, tool_name, tool_id, uuid, parent_uuid, "
    "content_length, harness, agent_id, agent_version, raw_line, raw_line_truncated, credits, ingested_at "
    "FROM session_events FINAL WHERE session_id = {sid:String} AND project_id = {pid:String} "
    "AND user_id = {uid:String} AND harness = {harness:String} AND rendered = 1 ORDER BY line_offset ASC "
    "SETTINGS max_final_threads = 4, do_not_merge_across_partitions_select_final = 1"
)
SUB_SQL = (
    "SELECT session_id, timestamp, event_type, content_preview, tool_name, tool_id, uuid, parent_uuid, "
    "content_length, harness, raw_line, raw_line_truncated, credits, ingested_at, line_offset "
    "FROM session_events FINAL WHERE parent_session_id = {sid:String} AND project_id = {pid:String} "
    "AND user_id = {uid:String} AND harness = {harness:String} AND rendered = 1 "
    "ORDER BY session_id, line_offset ASC SETTINGS max_final_threads = 4, "
    "do_not_merge_across_partitions_select_final = 1"
)
MAIN_SQL_OFFSET = MAIN_SQL.replace(
    "AND rendered = 1 ORDER BY", "AND rendered = 1 AND line_offset > {offset:UInt32} ORDER BY"
)
SUB_SQL_OFFSET = SUB_SQL.replace(
    "AND rendered = 1 ORDER BY", "AND rendered = 1 AND line_offset > {offset:UInt32} ORDER BY"
)
SUMMARY_SQL_USER = (
    "SELECT count() AS total, countIf(toDate(last_event_time) = today()) AS today_sessions FROM (   "
    "SELECT session_id, max(last_event_time) AS last_event_time   FROM session_stats_agg FINAL   "
    "WHERE session_id != '' AND user_id = {uid:String}   GROUP BY session_id )"
)
SUMMARY_SQL_ADMIN = SUMMARY_SQL_USER.replace("AND user_id = {uid:String} ", "")
STATS_SQL = (
    "SELECT count() AS total_sessions, sum(prompt_count) AS total_prompts, 0 AS total_api_requests, "
    "sum(tool_call_count) AS total_tool_calls, sum(event_count) AS total_events FROM (   SELECT session_id, "
    "    sum(prompt_count) AS prompt_count,     sum(tool_call_count) AS tool_call_count,     "
    "sum(event_count) AS event_count   FROM session_stats_agg FINAL   WHERE session_id != ''   "
    "GROUP BY session_id )"
)


def _payload(response) -> dict:
    assert response.media_type == "application/json"
    return json.loads(response.body)


def _user(
    role: UserRole = UserRole.user,
    *,
    user_id: uuid.UUID = USER_ID,
    name: str = "Current User",
    trace_privacy: bool | None = False,
):
    values = {
        "id": user_id,
        "role": role,
        "name": name,
        "email": "current@example.test",
        "auth_provider": "local",
    }
    if trace_privacy is not None:
        values["_trace_privacy"] = trace_privacy
    return SimpleNamespace(**values)


def _result(*, rows=(), scalar=None):
    result = MagicMock()
    result.all.return_value = list(rows)
    result.scalar_one_or_none.return_value = scalar
    return result


def _db(*results, error: Exception | None = None):
    db = MagicMock()
    if error is not None:
        db.execute = AsyncMock(side_effect=error)
    else:
        db.execute = AsyncMock(side_effect=list(results))
    return db


class _SessionContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, traceback):
        return False


def _session_factory(*databases):
    contexts = iter(_SessionContext(db) for db in databases)
    return MagicMock(side_effect=lambda: next(contexts))


@asynccontextmanager
async def _api_client(user=None, *, auth_error: HTTPException | None = None):
    app = FastAPI()
    app.include_router(sessions.router)

    async def _current_user():
        if auth_error is not None:
            raise auth_error
        return user

    app.dependency_overrides[get_current_user] = _current_user
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_ch_json_returns_data_and_passes_exact_query(monkeypatch):
    response = MagicMock(status_code=200)
    response.json.return_value = {"data": [{"session_id": "one"}], "meta": []}
    query = AsyncMock(return_value=response)
    monkeypatch.setattr(sessions, "_query", query)

    result = await sessions._ch_json("SELECT 1", {"param_value": "x"})

    assert result == [{"session_id": "one"}]
    query.assert_awaited_once_with("SELECT 1 FORMAT JSON", {"param_value": "x"})


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["http", "exception"])
async def test_ch_json_maps_clickhouse_failures_to_empty_results(monkeypatch, failure):
    if failure == "http":
        response = MagicMock(status_code=503)
        query = AsyncMock(return_value=response)
    else:
        query = AsyncMock(side_effect=RuntimeError("clickhouse unavailable"))
    monkeypatch.setattr(sessions, "_query", query)

    assert await sessions._ch_json("SELECT broken") == []
    query.assert_awaited_once_with("SELECT broken FORMAT JSON", None)


@pytest.mark.parametrize(
    ("role", "trace_privacy", "is_admin", "has_trace_access"),
    [
        (UserRole.user, False, False, False),
        (UserRole.reviewer, False, False, False),
        (UserRole.admin, False, True, True),
        (UserRole.admin, True, True, False),
        (UserRole.super_admin, True, True, True),
        (UserRole.admin, None, True, True),
    ],
)
def test_admin_trace_access_matrix(role, trace_privacy, is_admin, has_trace_access):
    user = _user(role, trace_privacy=trace_privacy)

    assert sessions._is_admin_user(user) is is_admin
    assert sessions._has_admin_trace_access(user) is has_trace_access


@pytest.mark.asyncio
async def test_list_query_uses_exact_filters_pagination_and_parameters(monkeypatch):
    query = AsyncMock(return_value=[{"session_id": "one"}])
    monkeypatch.setattr(sessions, "_ch_json", query)

    result = await sessions._list_sessions_query(
        platform="kiro",
        user_ids=["u1", "u2"],
        days=365,
        is_admin=False,
        uid="me",
        limit=25,
        offset=10,
        mine=True,
    )

    assert result == [{"session_id": "one"}]
    sql, params = query.await_args.args
    assert _sql(sql) == _sql(LIST_SQL_FILTERED)
    assert params == {
        "param_uid": "me",
        "param_platform": "kiro",
        "param_user_0": "u1",
        "param_user_1": "u2",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("platform", "expected"),
    [("Kiro", "kiro"), ("Pi", "pi"), ("Claude Code", "claude-code"), ("claude-code", "claude-code")],
)
async def test_list_query_accepts_harness_id_or_display_name(monkeypatch, platform, expected):
    query = AsyncMock(return_value=[])
    monkeypatch.setattr(sessions, "_ch_json", query)

    await sessions._list_sessions_query(platform=platform, user_ids=None, days=None, is_admin=True, uid="admin")
    assert query.await_args.args[1]["param_platform"] == expected


def test_harness_id_takes_precedence_over_another_harness_display_name(monkeypatch):
    monkeypatch.setattr(sessions, "_PLATFORM_NAMES", {"first": "second", "second": "Second harness"})
    assert sessions._harness_id("SeCoNd") == "second"


@pytest.mark.asyncio
@pytest.mark.parametrize("days", [None, 0, -3])
async def test_list_query_leaves_admin_unfiltered_for_nonpositive_days(monkeypatch, days):
    query = AsyncMock(return_value=[])
    monkeypatch.setattr(sessions, "_ch_json", query)

    await sessions._list_sessions_query(
        platform=None,
        user_ids=None,
        days=days,
        is_admin=True,
        uid="admin",
    )

    sql, params = query.await_args.args
    assert _sql(sql) == _sql(LIST_SQL_UNFILTERED)
    assert params is None


@pytest.mark.asyncio
async def test_admin_mine_filter_is_user_scoped(monkeypatch):
    query = AsyncMock(return_value=[])
    monkeypatch.setattr(sessions, "_ch_json", query)

    await sessions._list_sessions_query(
        platform=None,
        user_ids=[],
        days=None,
        is_admin=True,
        uid="admin-id",
        mine=True,
    )

    sql, params = query.await_args.args
    assert _sql(sql) == _sql(
        LIST_SQL_UNFILTERED.replace("prompt_count > 0 ORDER BY", "prompt_count > 0 AND user_id = {uid:String} ORDER BY")
    )
    assert params == {"param_uid": "admin-id"}


@pytest.mark.asyncio
async def test_list_sessions_resolves_filters_names_and_transforms_rows(monkeypatch):
    filtered_ids = [str(OTHER_USER_ID)]
    rows = [
        {
            "session_id": "known",
            "user_id": str(OTHER_USER_ID),
            "harness": "kiro",
            "is_active": "1",
            "agent_id": str(AGENT_ID),
            "agent_version": "2.1.0",
        },
        {
            "session_id": "malformed-identities",
            "user_id": "not-a-uuid",
            "harness": "new-harness",
            "is_active": 0,
            "agent_id": "not-an-agent-uuid",
            "agent_version": "",
        },
        {
            "session_id": "defaults",
            "user_id": "",
            "harness": "",
            "is_active": "0",
            "agent_id": "",
            "agent_version": None,
        },
    ]
    list_query = AsyncMock(return_value=rows)
    resolve_filter = AsyncMock(return_value=UserFilterValues(ids=filtered_ids, emails=["ignored@example.test"]))
    filter_db = _db()
    user_db = _db(_result(rows=[(OTHER_USER_ID, "Other User")]))
    agent_db = _db(_result(rows=[(AGENT_ID, "Canonical Agent")]))
    session_factory = _session_factory(filter_db, user_db, agent_db)
    monkeypatch.setattr(sessions, "_list_sessions_query", list_query)
    monkeypatch.setattr(sessions, "resolve_user_filter_values", resolve_filter)
    monkeypatch.setattr(sessions, "async_session", session_factory)
    current_user = _user()

    response = await sessions.list_sessions(
        status=None,
        platform="kiro",
        user=" other ",
        days=999,
        limit=17,
        offset=4,
        mine=True,
        current_user=current_user,
    )

    assert response == [
        {
            "session_id": "known",
            "user_id": str(OTHER_USER_ID),
            "is_active": True,
            "agent_id": str(AGENT_ID),
            "agent_version": "2.1.0",
            "user_name": "Other User",
            "platform": "Kiro",
            "service_name": "kiro",
            "agent_name": "Canonical Agent",
        },
        {
            "session_id": "malformed-identities",
            "user_id": "not-a-uuid",
            "is_active": False,
            "agent_id": "not-an-agent-uuid",
            "agent_version": None,
            "user_name": "Current User",
            "platform": "New Harness",
            "service_name": "new-harness",
            "agent_name": None,
        },
        {
            "session_id": "defaults",
            "user_id": "",
            "is_active": False,
            "agent_id": None,
            "agent_version": None,
            "user_name": "Current User",
            "platform": "Claude Code",
            "service_name": "",
            "agent_name": None,
        },
    ]
    resolve_filter.assert_awaited_once_with(filter_db, " other ")
    list_query.assert_awaited_once_with(
        platform="kiro",
        user_ids=filtered_ids,
        days=365,
        is_admin=False,
        uid=str(USER_ID),
        limit=17,
        offset=4,
        mine=True,
    )
    assert session_factory.call_count == 3
    user_db.execute.assert_awaited_once()
    agent_db.execute.assert_awaited_once()
    user_statement = user_db.execute.await_args.args[0]
    agent_statement = agent_db.execute.await_args.args[0]
    assert _sql(user_statement) == "SELECT users.id, users.name FROM users WHERE users.id IN (__[POSTCOMPILE_id_1])"
    assert user_statement.compile().params == {"id_1": [OTHER_USER_ID]}
    assert _sql(agent_statement) == (
        "SELECT agents.id, agents.name FROM agents WHERE agents.id IN (__[POSTCOMPILE_id_1])"
    )
    assert agent_statement.compile().params == {"id_1": [AGENT_ID]}


@pytest.mark.asyncio
async def test_list_sessions_returns_early_when_user_filter_has_no_matches(monkeypatch):
    filter_db = _db()
    session_factory = _session_factory(filter_db)
    resolver = AsyncMock(return_value=UserFilterValues(ids=[], emails=["nobody@example.test"]))
    list_query = AsyncMock()
    monkeypatch.setattr(sessions, "async_session", session_factory)
    monkeypatch.setattr(sessions, "resolve_user_filter_values", resolver)
    monkeypatch.setattr(sessions, "_list_sessions_query", list_query)

    result = await sessions.list_sessions(
        status=None,
        platform=None,
        user="nobody",
        days=None,
        limit=50,
        offset=0,
        mine=False,
        current_user=_user(),
    )

    assert result == []
    resolver.assert_awaited_once_with(filter_db, "nobody")
    list_query.assert_not_awaited()
    assert session_factory.call_count == 1


@pytest.mark.asyncio
async def test_list_sessions_propagates_user_filter_database_failure(monkeypatch):
    filter_db = _db()
    resolver = AsyncMock(side_effect=RuntimeError("postgres unavailable"))
    list_query = AsyncMock()
    monkeypatch.setattr(sessions, "async_session", _session_factory(filter_db))
    monkeypatch.setattr(sessions, "resolve_user_filter_values", resolver)
    monkeypatch.setattr(sessions, "_list_sessions_query", list_query)

    with pytest.raises(RuntimeError, match="postgres unavailable"):
        await sessions.list_sessions(
            status=None,
            platform=None,
            user="alice",
            days=None,
            limit=50,
            offset=0,
            mine=False,
            current_user=_user(),
        )

    list_query.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_sessions_name_lookup_failures_use_safe_fallbacks(monkeypatch):
    rows = [
        {
            "session_id": "one",
            "user_id": str(OTHER_USER_ID),
            "harness": "cursor",
            "is_active": 0,
            "agent_id": str(AGENT_ID),
            "agent_version": "latest",
        }
    ]
    user_db = _db(error=RuntimeError("user lookup failed"))
    agent_db = _db(error=RuntimeError("agent lookup failed"))
    monkeypatch.setattr(sessions, "_list_sessions_query", AsyncMock(return_value=rows))
    monkeypatch.setattr(sessions, "async_session", _session_factory(user_db, agent_db))

    result = await sessions.list_sessions(
        status=None,
        platform=None,
        user=None,
        days=None,
        limit=50,
        offset=0,
        mine=False,
        current_user=_user(name="Fallback"),
    )

    assert result == [
        {
            "session_id": "one",
            "user_id": str(OTHER_USER_ID),
            "is_active": False,
            "agent_id": str(AGENT_ID),
            "agent_version": "latest",
            "user_name": "Fallback",
            "platform": "Cursor",
            "service_name": "cursor",
            "agent_name": None,
        }
    ]
    user_db.execute.assert_awaited_once()
    agent_db.execute.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "expected"), [("active", ["active"]), ("complete", ["active", "idle"])])
async def test_list_sessions_status_filter_only_removes_inactive_rows(monkeypatch, status, expected):
    rows = [
        {"session_id": "active", "user_id": "", "harness": "opencode", "is_active": 1},
        {"session_id": "idle", "user_id": "", "harness": "codex", "is_active": 0},
    ]
    monkeypatch.setattr(sessions, "_list_sessions_query", AsyncMock(return_value=rows))
    session_factory = MagicMock(side_effect=AssertionError("database should not be opened"))
    monkeypatch.setattr(sessions, "async_session", session_factory)

    result = await sessions.list_sessions(
        status=status,
        platform=None,
        user=None,
        days=None,
        limit=50,
        offset=0,
        mine=False,
        current_user=_user(),
    )

    assert [row["session_id"] for row in result] == expected
    assert [row["platform"] for row in rows] == ["OpenCode", "Codex"]
    session_factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user", "expected_sql", "expected_params"),
    [
        (_user(), SUMMARY_SQL_USER, {"param_uid": str(USER_ID)}),
        (_user(UserRole.admin), SUMMARY_SQL_ADMIN, None),
        (_user(UserRole.admin, trace_privacy=True), SUMMARY_SQL_USER, {"param_uid": str(USER_ID)}),
        (_user(UserRole.super_admin, trace_privacy=True), SUMMARY_SQL_ADMIN, None),
    ],
)
async def test_sessions_summary_applies_trace_visibility_and_transforms_counts(
    monkeypatch, user, expected_sql, expected_params
):
    query = AsyncMock(return_value=[{"total": "12", "today_sessions": 3}])
    monkeypatch.setattr(sessions, "_ch_json", query)

    result = await sessions.sessions_summary(user)

    assert result == {"total_sessions": 12, "today_sessions": 3}
    sql, params = query.await_args.args
    assert _sql(sql) == _sql(expected_sql)
    assert params == expected_params


@pytest.mark.asyncio
async def test_sessions_summary_empty_result_returns_zeroes(monkeypatch):
    query = AsyncMock(return_value=[])
    monkeypatch.setattr(sessions, "_ch_json", query)

    assert await sessions.sessions_summary(_user()) == {"total_sessions": 0, "today_sessions": 0}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        (
            [
                {
                    "total_sessions": "7",
                    "total_prompts": 8,
                    "total_api_requests": "0",
                    "total_tool_calls": "9",
                    "total_events": 10,
                }
            ],
            {
                "total_sessions": 7,
                "total_prompts": 8,
                "total_api_requests": 0,
                "total_tool_calls": 9,
                "total_events": 10,
            },
        ),
        (
            [],
            {
                "total_sessions": 0,
                "total_prompts": 0,
                "total_api_requests": 0,
                "total_tool_calls": 0,
                "total_events": 0,
            },
        ),
    ],
)
async def test_sessions_stats_uses_exact_aggregate_and_transforms_counts(monkeypatch, rows, expected):
    query = AsyncMock(return_value=rows)
    monkeypatch.setattr(sessions, "_ch_json", query)

    result = await sessions.sessions_stats.__wrapped__(current_user=_user(UserRole.admin))

    assert result == expected
    assert _sql(query.await_args.args[0]) == _sql(STATS_SQL)


@pytest.mark.asyncio
@pytest.mark.parametrize("session_id", [str(AGENT_ID), "malformed';SELECT"])
async def test_session_detail_empty_identity_preserves_identifier_and_user_isolation(monkeypatch, session_id):
    query = AsyncMock(return_value=[])
    monkeypatch.setattr(sessions, "_ch_json", query)

    result = _payload(await sessions.get_session(session_id, current_user=_user()))

    assert result == {"session_id": session_id, "harness": "", "events": []}
    sql, params = query.await_args.args
    assert _sql(sql) == _sql(IDENTITY_SQL_USER)
    assert params == {"param_sid": session_id, "param_uid": str(USER_ID)}


@pytest.mark.asyncio
async def test_admin_session_detail_uses_canonical_identity_for_both_event_queries(monkeypatch):
    identity = {"project_id": "project-a", "user_id": str(OTHER_USER_ID), "harness": "cursor"}
    query = AsyncMock(side_effect=[[identity], [], []])
    monkeypatch.setattr(sessions, "_ch_json", query)

    result = _payload(await sessions.get_session("shared-id", after_offset=None, current_user=_user(UserRole.admin)))

    params = {
        "param_sid": "shared-id",
        "param_pid": "project-a",
        "param_uid": str(OTHER_USER_ID),
        "param_harness": "cursor",
    }
    assert result == {"session_id": "shared-id", "service_name": "", "events": [], "traces": []}
    assert [(_sql(item.args[0]), item.args[1]) for item in query.await_args_list] == [
        (_sql(IDENTITY_SQL_ADMIN), {"param_sid": "shared-id"}),
        (_sql(MAIN_SQL), params),
        (_sql(SUB_SQL), params),
    ]


@pytest.mark.asyncio
async def test_incremental_session_detail_uses_offset_for_parent_and_subagents(monkeypatch):
    identity = {"project_id": "project-a", "user_id": str(USER_ID), "harness": "claude-code"}
    query = AsyncMock(side_effect=[[identity], [], []])
    monkeypatch.setattr(sessions, "_ch_json", query)

    result = _payload(await sessions.get_session("session", after_offset=7, current_user=_user()))

    params = {
        "param_sid": "session",
        "param_pid": "project-a",
        "param_uid": str(USER_ID),
        "param_harness": "claude-code",
        "param_offset": "7",
    }
    assert result == {"session_id": "session", "events": [], "max_offset": 7}
    assert [(_sql(item.args[0]), item.args[1]) for item in query.await_args_list] == [
        (_sql(IDENTITY_SQL_USER), {"param_sid": "session", "param_uid": str(USER_ID)}),
        (_sql(MAIN_SQL_OFFSET), params),
        (_sql(SUB_SQL_OFFSET), params),
    ]


@pytest.mark.asyncio
async def test_session_detail_parses_events_subagents_and_agent_name(monkeypatch):
    identity = {"project_id": "project-a", "user_id": str(USER_ID), "harness": "kiro"}
    parent_rows = [
        {
            "line_offset": "2",
            "harness": "kiro",
            "agent_id": "",
            "agent_version": "",
            "raw_line": "parent one",
        },
        {
            "line_offset": 9,
            "harness": "kiro",
            "agent_id": str(AGENT_ID),
            "agent_version": "3.0.0",
            "raw_line": "parent two",
        },
    ]
    child_b = {
        "session_id": "child-b",
        "line_offset": 1,
        "parent_uuid": "spawn-b",
        "harness": "kiro",
        "raw_line": "child b",
    }
    child_a = {
        "session_id": "child-a",
        "line_offset": 3,
        "parent_uuid": "spawn-a",
        "harness": "kiro",
        "raw_line": "child a",
    }
    query = AsyncMock(side_effect=[[identity], parent_rows, [child_b, child_a]])
    parser = MagicMock(side_effect=[[{"event_name": "parent"}], [{"event_name": "a"}], [{"event_name": "b"}]])
    agent_db = _db(_result(scalar="Canonical Agent"))
    session_factory = _session_factory(agent_db)
    monkeypatch.setattr(sessions, "_ch_json", query)
    monkeypatch.setattr("services.session_parsers.parse_raw_events", parser)
    monkeypatch.setattr(sessions, "async_session", session_factory)

    result = _payload(await sessions.get_session("session", after_offset=None, current_user=_user()))

    assert result == {
        "session_id": "session",
        "service_name": "kiro",
        "agent_id": str(AGENT_ID),
        "agent_name": "Canonical Agent",
        "agent_version": "3.0.0",
        "events": [{"event_name": "parent"}],
        "traces": [],
        "subagent_sessions": [
            {"session_id": "child-a", "spawned_by": "spawn-a", "events": [{"event_name": "a"}]},
            {"session_id": "child-b", "spawned_by": "spawn-b", "events": [{"event_name": "b"}]},
        ],
        "max_offset": 9,
    }
    params = {
        "param_sid": "session",
        "param_pid": "project-a",
        "param_uid": str(USER_ID),
        "param_harness": "kiro",
    }
    assert [(_sql(item.args[0]), item.args[1]) for item in query.await_args_list] == [
        (_sql(IDENTITY_SQL_USER), {"param_sid": "session", "param_uid": str(USER_ID)}),
        (_sql(MAIN_SQL), params),
        (_sql(SUB_SQL), params),
    ]
    assert parser.call_args_list == [call(parent_rows), call([child_a]), call([child_b])]
    agent_db.execute.assert_awaited_once()
    statement = agent_db.execute.await_args.args[0]
    assert _sql(statement) == "SELECT agents.name FROM agents WHERE agents.id = :id_1"
    assert statement.compile().params == {"id_1": AGENT_ID}
    assert session_factory.call_count == 1


@pytest.mark.asyncio
async def test_session_detail_without_agent_uses_default_harness_and_no_database(monkeypatch):
    identity = {"project_id": "project-a", "user_id": str(USER_ID), "harness": "claude-code"}
    rows = [{"line_offset": 0, "raw_line": "event"}]
    query = AsyncMock(side_effect=[[identity], rows, []])
    parser = MagicMock(return_value=[{"event_name": "event"}])
    session_factory = MagicMock(side_effect=AssertionError("database should not be opened"))
    monkeypatch.setattr(sessions, "_ch_json", query)
    monkeypatch.setattr("services.session_parsers.parse_raw_events", parser)
    monkeypatch.setattr(sessions, "async_session", session_factory)

    result = _payload(await sessions.get_session("session", after_offset=None, current_user=_user()))

    assert result["service_name"] == "claude-code"
    assert result["agent_id"] is None
    assert result["agent_name"] is None
    assert result["agent_version"] is None
    assert result["events"] == [{"event_name": "event"}]
    assert result["subagent_sessions"] == []
    assert result["max_offset"] == 0
    parser.assert_called_once_with(rows)
    session_factory.assert_not_called()


@pytest.mark.asyncio
async def test_session_detail_encodes_integers_beyond_64_bits_from_transcripts(monkeypatch):
    identity = {"project_id": "project-a", "user_id": str(USER_ID), "harness": "claude-code"}
    huge = 2**70
    query = AsyncMock(side_effect=[[identity], [{"line_offset": 0, "raw_line": "event"}], []])
    monkeypatch.setattr(sessions, "_ch_json", query)
    monkeypatch.setattr(
        "services.session_parsers.parse_raw_events",
        MagicMock(return_value=[{"event_name": "attachment", "attributes": {"attachment_type": huge}}]),
    )

    result = _payload(await sessions.get_session("session", after_offset=None, current_user=_user()))

    assert result["events"] == [{"event_name": "attachment", "attributes": {"attachment_type": huge}}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("agent_id", "db_error"),
    [("not-a-uuid", None), (str(AGENT_ID), RuntimeError("postgres unavailable"))],
)
async def test_session_detail_agent_resolution_failures_do_not_hide_events(monkeypatch, agent_id, db_error):
    identity = {"project_id": "project-a", "user_id": str(USER_ID), "harness": "cursor"}
    rows = [{"line_offset": 4, "harness": "cursor", "agent_id": agent_id, "raw_line": "event"}]
    query = AsyncMock(side_effect=[[identity], rows, []])
    parser = MagicMock(return_value=[{"event_name": "event"}])
    agent_db = _db(error=db_error) if db_error else _db()
    monkeypatch.setattr(sessions, "_ch_json", query)
    monkeypatch.setattr("services.session_parsers.parse_raw_events", parser)
    monkeypatch.setattr(sessions, "async_session", _session_factory(agent_db))

    result = _payload(await sessions.get_session("session", after_offset=None, current_user=_user()))

    assert result["agent_id"] == agent_id
    assert result["agent_name"] is None
    assert result["events"] == [{"event_name": "event"}]
    if db_error:
        agent_db.execute.assert_awaited_once()
    else:
        agent_db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_session_parser_failure_propagates(monkeypatch):
    identity = {"project_id": "project-a", "user_id": str(USER_ID), "harness": "cursor"}
    rows = [{"line_offset": 0, "harness": "cursor", "raw_line": "bad"}]
    monkeypatch.setattr(sessions, "_ch_json", AsyncMock(side_effect=[[identity], rows, []]))
    monkeypatch.setattr("services.session_parsers.parse_raw_events", MagicMock(side_effect=KeyError("parser")))

    with pytest.raises(KeyError, match="parser"):
        await sessions.get_session("session", after_offset=None, current_user=_user())


@pytest.mark.asyncio
async def test_bind_session_agent_checks_owner_and_sets_expiring_binding(monkeypatch):
    ownership = AsyncMock(return_value=[{"1": 1}])
    redis = MagicMock()
    redis.set = AsyncMock(return_value=True)
    get_redis = MagicMock(return_value=redis)
    monkeypatch.setattr(sessions, "_ch_json", ownership)
    monkeypatch.setattr("services.redis.get_redis", get_redis)

    result = await sessions.bind_session_agent("session", agent_name="alice/reviewer", current_user=_user())

    assert result == {"session_id": "session", "agent_name": "alice/reviewer", "bound": True}
    ownership.assert_awaited_once_with(
        "SELECT 1 FROM session_events WHERE session_id = {sid:String} AND user_id = {uid:String} LIMIT 1",
        {"param_sid": "session", "param_uid": str(USER_ID)},
    )
    get_redis.assert_called_once_with()
    redis.set.assert_awaited_once_with("session_agent:session", "alice/reviewer", ex=86400)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [UserRole.admin, UserRole.super_admin])
async def test_admin_binding_skips_ownership_query(monkeypatch, role):
    ownership = AsyncMock()
    redis = MagicMock()
    redis.set = AsyncMock(return_value=True)
    monkeypatch.setattr(sessions, "_ch_json", ownership)
    monkeypatch.setattr("services.redis.get_redis", MagicMock(return_value=redis))

    result = await sessions.bind_session_agent("session", agent_name="agent", current_user=_user(role))

    assert result["bound"] is True
    ownership.assert_not_awaited()
    redis.set.assert_awaited_once_with("session_agent:session", "agent", ex=86400)


@pytest.mark.asyncio
async def test_denied_binding_returns_404_without_mutation(monkeypatch):
    ownership = AsyncMock(return_value=[])
    get_redis = MagicMock(side_effect=AssertionError("redis must not be touched"))
    monkeypatch.setattr(sessions, "_ch_json", ownership)
    monkeypatch.setattr("services.redis.get_redis", get_redis)

    with pytest.raises(HTTPException) as exc:
        await sessions.bind_session_agent("session", agent_name="agent", current_user=_user())

    assert exc.value.status_code == 404
    assert exc.value.detail == "Session not found or access denied"
    get_redis.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_at", ["get", "set"])
async def test_binding_reports_redis_unavailability(monkeypatch, failure_at):
    redis = MagicMock()
    redis.set = AsyncMock(side_effect=RedisError("redis unavailable") if failure_at == "set" else None)
    get_redis = MagicMock(
        side_effect=RedisError("redis unavailable") if failure_at == "get" else None,
        return_value=redis,
    )
    monkeypatch.setattr(sessions, "_ch_json", AsyncMock())
    monkeypatch.setattr("services.redis.get_redis", get_redis)

    result = await sessions.bind_session_agent("session", agent_name="agent", current_user=_user(UserRole.admin))

    assert result == {
        "session_id": "session",
        "agent_name": "agent",
        "bound": False,
        "error": "Redis unavailable",
    }


@pytest.mark.asyncio
async def test_empty_agent_binding_is_persisted_without_delete(monkeypatch):
    redis = MagicMock()
    redis.set = AsyncMock(return_value=True)
    redis.delete = AsyncMock()
    monkeypatch.setattr("services.redis.get_redis", MagicMock(return_value=redis))

    result = await sessions.bind_session_agent("session", agent_name="", current_user=_user(UserRole.admin))

    assert result == {"session_id": "session", "agent_name": "", "bound": True}
    redis.set.assert_awaited_once_with("session_agent:session", "", ex=86400)
    redis.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_unexpected_binding_service_failure_propagates(monkeypatch):
    redis = MagicMock()
    redis.set = AsyncMock(side_effect=RuntimeError("unexpected redis failure"))
    monkeypatch.setattr("services.redis.get_redis", MagicMock(return_value=redis))

    with pytest.raises(RuntimeError, match="unexpected redis failure"):
        await sessions.bind_session_agent("session", agent_name="agent", current_user=_user(UserRole.admin))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/v1/sessions"),
        ("get", "/api/v1/sessions/summary"),
        ("get", "/api/v1/sessions/stats"),
        ("get", "/api/v1/sessions/session-id"),
        ("post", "/api/v1/sessions/session-id/bind-agent?agent_name=agent"),
    ],
)
async def test_session_routes_require_authentication(monkeypatch, method, path):
    query = AsyncMock()
    get_redis = MagicMock()
    monkeypatch.setattr(sessions, "_ch_json", query)
    monkeypatch.setattr("services.redis.get_redis", get_redis)

    async with _api_client(auth_error=HTTPException(status_code=401, detail="Missing credentials")) as client:
        response = await client.request(method, path)

    assert response.status_code == 401
    assert response.json() == {"detail": "Missing credentials"}
    query.assert_not_awaited()
    get_redis.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [UserRole.user, UserRole.reviewer])
async def test_sessions_stats_requires_admin_role(monkeypatch, role):
    query = AsyncMock()
    monkeypatch.setattr(sessions, "_ch_json", query)

    async with _api_client(_user(role)) as client:
        response = await client.get("/api/v1/sessions/stats")

    assert response.status_code == 403
    assert response.json() == {"detail": "Insufficient permissions"}
    query.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "location", "error_type"),
    [
        ("/api/v1/sessions?limit=0", ["query", "limit"], "greater_than_equal"),
        ("/api/v1/sessions?limit=201", ["query", "limit"], "less_than_equal"),
        ("/api/v1/sessions?offset=-1", ["query", "offset"], "greater_than_equal"),
        ("/api/v1/sessions/session?after_offset=-1", ["query", "after_offset"], "greater_than_equal"),
        ("/api/v1/sessions/session/bind-agent", ["query", "agent_name"], "missing"),
    ],
)
async def test_session_query_validation_rejects_invalid_requests(monkeypatch, path, location, error_type):
    query = AsyncMock()
    monkeypatch.setattr(sessions, "_ch_json", query)

    async with _api_client(_user()) as client:
        method = "post" if path.endswith("bind-agent") else "get"
        response = await client.request(method, path)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert [(item["loc"], item["type"]) for item in detail] == [(location, error_type)]
    query.assert_not_awaited()


# ── Agent ids that outlive their agent ─────────────────────────────
#
# A session keeps the agent id it was attributed to. The agent can later be
# deleted, or the id can come from a lockfile pointing at a different registry,
# leaving a row with an id and nothing to call it.


def test_resolved_agent_name_returns_the_known_name():
    assert sessions._resolved_agent_name("abc", {"abc": "reviewer"}, queried=True) == "reviewer"


def test_resolved_agent_name_labels_an_agent_that_no_longer_exists():
    """Naming the state beats rendering an id with a blank name."""
    missing = "506fdb3f-d08a-4a60-8d75-7bee17233cd9"

    assert sessions._resolved_agent_name(missing, {}, queried=True) == "unknown agent (506fdb3f)"


def test_resolved_agent_name_stays_silent_when_the_id_was_never_queried():
    """A malformed id or a failed lookup proves nothing about the agent."""
    assert sessions._resolved_agent_name("not-a-uuid", {}, queried=False) is None


@pytest.mark.asyncio
async def test_session_list_labels_deleted_agents_but_not_unqueryable_ones(monkeypatch):
    """One row's agent is genuinely gone; the other's id was never queryable."""
    deleted_id = str(AGENT_ID)
    rows = [
        {
            "session_id": "deleted-agent",
            "user_id": str(USER_ID),
            "is_active": 0,
            "agent_id": deleted_id,
            "agent_version": "1.0.0",
            "harness": "kiro",
        },
        {
            "session_id": "malformed-agent",
            "user_id": str(USER_ID),
            "is_active": 0,
            "agent_id": "not-an-agent-uuid",
            "agent_version": None,
            "harness": "kiro",
        },
    ]
    user_db = _db(_result(rows=[(USER_ID, "Current User")]))
    agent_db = _db(_result())  # queried successfully, returned no agents
    monkeypatch.setattr(sessions, "_list_sessions_query", AsyncMock(return_value=rows))
    monkeypatch.setattr(sessions, "resolve_user_filter_values", AsyncMock(return_value=None))
    monkeypatch.setattr(sessions, "async_session", _session_factory(user_db, agent_db))

    result = await sessions.list_sessions(
        status=None,
        platform=None,
        user=None,
        days=None,
        limit=50,
        offset=0,
        mine=False,
        current_user=_user(),
    )

    by_id = {row["session_id"]: row for row in result}
    assert by_id["deleted-agent"]["agent_name"] == f"unknown agent ({deleted_id[:8]})"
    assert by_id["malformed-agent"]["agent_name"] is None


@pytest.mark.asyncio
async def test_session_detail_labels_a_deleted_agent(monkeypatch):
    identity = {"project_id": "project-a", "user_id": str(USER_ID), "harness": "kiro"}
    rows = [{"line_offset": 1, "harness": "kiro", "agent_id": str(AGENT_ID), "raw_line": "event"}]
    query = AsyncMock(side_effect=[[identity], rows, []])
    scalar = MagicMock()
    scalar.scalar_one_or_none = MagicMock(return_value=None)
    monkeypatch.setattr(sessions, "_ch_json", query)
    monkeypatch.setattr("services.session_parsers.parse_raw_events", MagicMock(return_value=[]))
    monkeypatch.setattr(sessions, "async_session", _session_factory(_db(scalar)))

    result = _payload(await sessions.get_session("session", after_offset=None, current_user=_user()))

    assert result["agent_id"] == str(AGENT_ID)
    assert result["agent_name"] == f"unknown agent ({str(AGENT_ID)[:8]})"


@pytest.mark.asyncio
async def test_session_list_resolves_a_non_canonical_agent_id(monkeypatch):
    """A session can store a valid id in uppercase or without hyphens.

    It queries correctly, but the row returns canonical, so keying results by
    the returned id alone missed the original and labelled a live agent
    unknown - worse than returning nothing.
    """
    stored = str(AGENT_ID).replace("-", "").upper()
    rows = [
        {
            "session_id": "noncanonical",
            "user_id": str(USER_ID),
            "is_active": 0,
            "agent_id": stored,
            "agent_version": "1.0.0",
            "harness": "kiro",
        }
    ]
    user_db = _db(_result(rows=[(USER_ID, "Current User")]))
    agent_db = _db(_result(rows=[(AGENT_ID, "Canonical Agent")]))
    monkeypatch.setattr(sessions, "_list_sessions_query", AsyncMock(return_value=rows))
    monkeypatch.setattr(sessions, "resolve_user_filter_values", AsyncMock(return_value=None))
    monkeypatch.setattr(sessions, "async_session", _session_factory(user_db, agent_db))

    result = await sessions.list_sessions(
        status=None,
        platform=None,
        user=None,
        days=None,
        limit=50,
        offset=0,
        mine=False,
        current_user=_user(),
    )

    assert result[0]["agent_name"] == "Canonical Agent"


@pytest.mark.asyncio
async def test_session_list_resolves_every_stored_form_of_one_agent(monkeypatch):
    """Two sessions on one page can spell the same agent id differently.

    Mapping each canonical id to a single stored form resolved one session and
    left the other labelled unknown.
    """
    canonical = str(AGENT_ID)
    shouty = canonical.replace("-", "").upper()
    rows = [
        {
            "session_id": form,
            "user_id": str(USER_ID),
            "is_active": 0,
            "agent_id": form,
            "agent_version": "1.0.0",
            "harness": "kiro",
        }
        for form in (canonical, shouty)
    ]
    user_db = _db(_result(rows=[(USER_ID, "Current User")]))
    agent_db = _db(_result(rows=[(AGENT_ID, "Canonical Agent")]))
    monkeypatch.setattr(sessions, "_list_sessions_query", AsyncMock(return_value=rows))
    monkeypatch.setattr(sessions, "resolve_user_filter_values", AsyncMock(return_value=None))
    monkeypatch.setattr(sessions, "async_session", _session_factory(user_db, agent_db))

    result = await sessions.list_sessions(
        status=None,
        platform=None,
        user=None,
        days=None,
        limit=50,
        offset=0,
        mine=False,
        current_user=_user(),
    )

    assert {row["session_id"]: row["agent_name"] for row in result} == {
        canonical: "Canonical Agent",
        shouty: "Canonical Agent",
    }
