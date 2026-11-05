# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""Phase 3 component observability API: auth, bounds, coverage and scoping."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import get_current_user, get_db
from api.routes import component_activity as route
from models.user import UserRole
from services.component_activity import coverage, queries

OWNER_ID = uuid.uuid4()
LISTING_ID = uuid.uuid4()
VERSION_ID = uuid.uuid4()


def _user(role=UserRole.user, user_id=None):
    return SimpleNamespace(id=user_id or uuid.uuid4(), role=role)


def _listing(co_authors=()):
    return SimpleNamespace(
        id=LISTING_ID, submitted_by=OWNER_ID, co_authors=list(co_authors), namespace="team", slug="probe"
    )


_SUMMARY = {
    "present_sessions": 2,
    "present_users": 1,
    "observed_sessions": 1,
    "observed_calls": 3,
    "result_states": {"success": 1, "error": 1, "unknown": 1},
    "harness_distribution": {"claude-code": 2},
    "activation_actions": {"context_sessions": 0, "next_session_sessions": 1},
    "coverage": coverage.build_coverage(
        {"present_sessions": 2, "present_users": 1},
        {"supported_present_sessions": 2, "projection_complete_sessions": 2, "observed_sessions": 1},
        257,
    ),
}


@pytest.fixture
def api(monkeypatch):
    app = FastAPI()
    app.include_router(route.router)
    state = {"user": _user(user_id=OWNER_ID), "listing": _listing(), "version_found": True}
    db = SimpleNamespace(scalar=AsyncMock(side_effect=lambda *_a, **_k: "1.2.3" if state["version_found"] else None))
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: state["user"]
    resolve = AsyncMock(side_effect=lambda *_a, **_k: state["listing"])
    summary = AsyncMock(return_value=_SUMMARY)
    sessions = AsyncMock(return_value=([], None))
    monkeypatch.setattr(route, "resolve_visible_listing", resolve)
    monkeypatch.setattr(queries, "activity_summary", summary)
    monkeypatch.setattr(queries, "activity_sessions", sessions)
    return SimpleNamespace(
        client=TestClient(app), state=state, resolve=resolve, summary=summary, sessions=sessions, db=db
    )


@pytest.mark.parametrize(
    "actor",
    [
        _user(user_id=OWNER_ID),
        _user(role=UserRole.admin),
        _user(role=UserRole.super_admin),
    ],
    ids=["owner", "admin", "super-admin"],
)
def test_owner_and_admin_may_read_summary(api, actor):
    api.state["user"] = actor
    response = api.client.get("/api/v1/components/mcp/team/probe/activity/summary")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["component"]["qualified_name"] == "team/probe"
    assert body["coverage"]["attribution_state"] == "observed"
    assert "last_event_time in [start, end)" in body["time_basis"]
    assert api.resolve.await_args.args[1] == "team/probe"  # namespace/slug resolved by the shared loader


def test_co_author_allowed_viewer_forbidden_private_non_member_not_found(api):
    co_author = _user()
    api.state["listing"] = _listing(co_authors=[co_author.id])
    api.state["user"] = co_author
    assert api.client.get(f"/api/v1/components/mcp/{LISTING_ID}/activity/summary").status_code == 200

    api.state["listing"] = _listing()
    api.state["user"] = _user(role=UserRole.reviewer)
    response = api.client.get(f"/api/v1/components/mcp/{LISTING_ID}/activity/summary")
    assert response.status_code == 403

    api.state["listing"] = None  # resolve_visible_listing hides private listings from non-members
    assert api.client.get(f"/api/v1/components/mcp/{LISTING_ID}/activity/sessions").status_code == 404
    api.summary.assert_awaited_once()  # denied callers never reach a telemetry query
    api.sessions.assert_not_awaited()


def test_unauthenticated_uses_existing_auth_dependency(api):
    def deny():
        from fastapi import HTTPException

        raise HTTPException(status_code=401, detail="Not authenticated")

    api.client.app.dependency_overrides[get_current_user] = deny
    assert api.client.get(f"/api/v1/components/mcp/{LISTING_ID}/activity/summary").status_code == 401
    api.summary.assert_not_awaited()


def test_version_must_belong_to_listing_and_is_forwarded(api):
    url = f"/api/v1/components/mcp/{LISTING_ID}/activity/summary"
    assert api.client.get(url, params={"component_version_id": "not-a-uuid"}).status_code == 422
    api.state["version_found"] = False
    assert api.client.get(url, params={"component_version_id": str(uuid.uuid4())}).status_code == 404
    api.summary.assert_not_awaited()
    api.state["version_found"] = True
    assert api.client.get(url, params={"component_version_id": str(VERSION_ID)}).status_code == 200
    assert api.summary.await_args.args[3] == str(VERSION_ID)
    assert api.summary.await_args.kwargs["component_version"] == "1.2.3"


def test_unknown_component_type_is_rejected_before_any_query(api):
    for endpoint in ("summary", "sessions"):
        response = api.client.get(f"/api/v1/components/prompt/{LISTING_ID}/activity/{endpoint}")
        assert response.status_code == 422
    api.summary.assert_not_awaited()


def _hook_summary(aggregate: dict | None = None) -> dict:
    from services.component_activity.hook_queries import build_hook_coverage

    aggregate = aggregate or {
        "supported_present_sessions": 3,
        "projection_complete_sessions": 3,
        "eligible_sessions": 2,
        "headless_sessions": 1,
        "observed_sessions": 1,
    }
    coverage = build_hook_coverage({"present_sessions": 3, "present_users": 1}, aggregate, 515)
    return {
        "present_sessions": 3,
        "present_users": 1,
        "eligible_sessions": coverage.eligibility.eligible_sessions,
        "sessions_with_recorded_run": coverage.observed_sessions,
        "runs_with_output": 1,
        "silent_runs": None,
        "failures": 1,
        "blocks": 0,
        "harness_distribution": {"claude-code": 3},
        "version_distribution": {},
        "activation_actions": {"context_sessions": 0, "next_session_sessions": 0, "scope": "component"},
        "coverage": coverage,
    }


def test_hook_reads_hook_evidence_never_mcp_or_skill_fields(api, monkeypatch):
    from services.component_activity import hook_queries

    summary = AsyncMock(return_value=_hook_summary())
    sessions = AsyncMock(return_value=([], None))
    monkeypatch.setattr(hook_queries, "hook_activity_summary", summary)
    monkeypatch.setattr(hook_queries, "hook_activity_sessions", sessions)
    response = api.client.get(f"/api/v1/components/hook/{LISTING_ID}/activity/summary")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["eligible_sessions"], body["sessions_with_recorded_run"]) == (2, 1)
    assert body["coverage"]["eligibility"]["headless_sessions"] == 1
    assert "agent_hook_headless_sessions" in body["coverage"]["reasons"]
    for other in ("observed_calls", "result_states", "loaded_sessions", "invocations"):
        assert other not in body
    api.summary.assert_not_awaited()
    assert api.client.get(f"/api/v1/components/hook/{LISTING_ID}/activity/sessions").status_code == 200
    sessions.assert_awaited_once()
    api.state["user"] = _user(role=UserRole.reviewer)
    assert api.client.get(f"/api/v1/components/hook/{LISTING_ID}/activity/summary").status_code == 403


def test_hook_denominator_counts_only_sessions_where_the_hook_could_run():
    from services.component_activity.hook_queries import build_hook_coverage

    coverage = build_hook_coverage(
        {"present_sessions": 4},
        {
            "projection_complete_sessions": 4,
            "eligible_sessions": 1,
            "headless_sessions": 2,
            "agent_inactive_sessions": 1,
            "observed_sessions": 0,
        },
        515,
    )
    assert coverage.usage_rate_denominator_sessions == 1
    assert coverage.attribution_state == "no_recorded_runs"
    none_could_run = build_hook_coverage({"present_sessions": 2}, {"headless_sessions": 2}, 515)
    assert none_could_run.attribution_state == "attribution_not_possible" and none_could_run.usage_rate is None
    assert any(item.startswith("effect_not_observed") for item in coverage.limitations)


def _skill_summary(state: str = "observed") -> dict:
    from services.component_activity.skill_queries import build_skill_coverage

    aggregate = {"projection_complete_sessions": 2, "observed_sessions": 1, "supported_present_sessions": 2}
    coverage = build_skill_coverage({"present_sessions": 2, "present_users": 1}, aggregate, 515)
    assert coverage.attribution_state == state
    return {
        "present_sessions": 2,
        "present_users": 1,
        "available_sessions": 2,
        "loaded_sessions": 1,
        "confirmed_loads": 1,
        "load_attempts": 1,
        "invoked_sessions": 0,
        "invocations": 0,
        "harness_distribution": {"pi": 2},
        "version_distribution": {},
        "activation_actions": {"context_sessions": 0, "next_session_sessions": 0, "scope": "component"},
        "coverage": coverage,
    }


def test_skill_reads_skill_evidence_never_mcp_fields(api, monkeypatch):
    """Skills have their own queries, fields and coverage; owner checks are the same."""
    from services.component_activity import skill_queries

    skill_summary = AsyncMock(return_value=_skill_summary() | {"invoked_sessions": None, "invocations": None})
    skill_sessions = AsyncMock(return_value=([], None))
    monkeypatch.setattr(skill_queries, "skill_activity_summary", skill_summary)
    monkeypatch.setattr(skill_queries, "skill_activity_sessions", skill_sessions)
    response = api.client.get(f"/api/v1/components/skill/{LISTING_ID}/activity/summary")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["loaded_sessions"], body["confirmed_loads"], body["load_attempts"]) == (1, 1, 1)
    assert (body["invoked_sessions"], body["invocations"]) == (None, None), "unknown, not zero"
    for mcp_only in ("observed_calls", "result_states"):
        assert mcp_only not in body
    assert "calls" not in body["coverage"] and "evidence" in body["coverage"]
    assert any(item.startswith("entered_context_not_helped") for item in body["coverage"]["limitations"])
    api.summary.assert_not_awaited()  # the MCP query is never consulted for a skill
    assert api.client.get(f"/api/v1/components/skill/{LISTING_ID}/activity/sessions").status_code == 200
    api.sessions.assert_not_awaited()
    skill_sessions.assert_awaited_once()
    # Same visibility and ownership gate as MCP: a reviewer who is not an owner is refused.
    api.state["user"] = _user(role=UserRole.reviewer)
    assert api.client.get(f"/api/v1/components/skill/{LISTING_ID}/activity/summary").status_code == 403


def test_skill_coverage_excludes_unsupported_and_incomplete_from_the_denominator():
    from services.component_activity.skill_queries import build_skill_coverage

    coverage = build_skill_coverage(
        {"present_sessions": 3, "present_users": 2},
        {
            "supported_present_sessions": 2,
            "unsupported_present_sessions": 1,
            "projection_complete_sessions": 1,
            "projection_pending_sessions": 1,
            "observed_sessions": 0,
            "unknown_load_results": 1,
        },
        515,
    )
    assert coverage.usage_rate_denominator_sessions == 1
    assert coverage.attribution_state == "no_observed_skill_use"
    assert {"unsupported_harness_sessions", "projection_pending_sessions", "unconfirmed_load_attempts"} <= set(
        coverage.reasons
    )
    none_complete = build_skill_coverage({"present_sessions": 1}, {"unsupported_present_sessions": 1}, 515)
    assert none_complete.attribution_state == "attribution_not_possible"
    assert none_complete.usage_rate is None


def test_unknown_type_and_period_and_limit_bounds(api):
    assert api.client.get(f"/api/v1/components/prompt/{LISTING_ID}/activity/summary").status_code == 422
    base = f"/api/v1/components/mcp/{LISTING_ID}/activity"
    for days in (0, 91):
        assert api.client.get(f"{base}/summary", params={"period_days": days}).status_code == 422
    for limit in (0, 101):
        assert api.client.get(f"{base}/sessions", params={"limit": limit}).status_code == 422
    assert api.client.get(f"{base}/sessions", params={"cursor": "%%%"}).status_code == 422
    assert api.client.get(f"{base}/summary", params={"period_days": 90}).status_code == 200
    assert api.client.get(f"{base}/sessions", params={"limit": 100}).status_code == 200
    api.resolve.assert_awaited()


def test_cursor_round_trip_pins_window_end_and_rejects_tampering(api):
    end = datetime.now(UTC).replace(microsecond=0)
    scope = (route.DEFAULT_PROJECT_ID, str(OWNER_ID), "mcp", str(LISTING_ID), "", "7")
    row = {"user_id": "u", "harness": "claude-code", "session_id": "s"}
    cursor = queries.encode_cursor(row, end, scope=scope)
    pinned, key = queries.decode_cursor(cursor, scope=scope)
    assert pinned == end and key == ["u", "claude-code", "s"]
    response = api.client.get(
        f"/api/v1/components/mcp/{LISTING_ID}/activity/sessions", params={"cursor": cursor, "period_days": 7}
    )
    assert response.status_code == 200
    kwargs, args = api.sessions.await_args.kwargs, api.sessions.await_args.args
    assert kwargs["cursor"] == key and args[4] == (end - timedelta(days=7), end)
    assert kwargs["cursor_scope"] == scope
    for bad in ("", "e30", cursor[:-3] + "AAA"):
        with pytest.raises(ValueError):
            queries.decode_cursor(bad, scope=scope)
    for altered in (
        ("other-project", *scope[1:]),
        (scope[0], str(uuid.uuid4()), *scope[2:]),
        (*scope[:3], str(uuid.uuid4()), *scope[4:]),
        (*scope[:4], str(uuid.uuid4()), scope[5]),
        (*scope[:5], "90"),
    ):
        with pytest.raises(ValueError):
            queries.decode_cursor(cursor, scope=altered)
    for old_end in (datetime(2021, 1, 1, tzinfo=UTC), datetime(1, 1, 1, tzinfo=UTC)):
        expired = queries.encode_cursor(row, old_end, scope=scope)
        with pytest.raises(ValueError):
            queries.decode_cursor(expired, scope=scope)
        assert (
            api.client.get(
                f"/api/v1/components/mcp/{LISTING_ID}/activity/sessions", params={"cursor": expired, "period_days": 7}
            ).status_code
            == 422
        )
    future = queries.encode_cursor(row, end + timedelta(days=1), scope=scope)
    with pytest.raises(ValueError):
        queries.decode_cursor(future, scope=scope)


def test_coverage_distinguishes_no_observed_calls_from_attribution_not_possible():
    processed = coverage.build_coverage(
        {"present_sessions": 3}, {"supported_present_sessions": 3, "projection_complete_sessions": 2}, 257
    )
    assert processed.attribution_state == "no_observed_calls" and processed.usage_rate == 0
    assert processed.usage_rate_denominator_sessions == 2
    pending = coverage.build_coverage(
        {"present_sessions": 3},
        {"supported_present_sessions": 1, "unsupported_present_sessions": 2, "projection_pending_sessions": 1},
        257,
    )
    assert pending.attribution_state == "attribution_not_possible" and pending.usage_rate is None
    assert {"unsupported_harness_sessions", "projection_pending_sessions"} <= set(pending.reasons)
    empty = coverage.build_coverage({}, {}, 257)
    assert empty.attribution_state == "attribution_not_possible" and "no_present_sessions" in empty.reasons
    observed = coverage.build_coverage(
        {"present_sessions": 4}, {"projection_complete_sessions": 4, "observed_sessions": 1}, 257
    )
    assert observed.attribution_state == "observed" and observed.usage_rate == 0.25
    assert any("sender_cached_hash_not_proven_stable" in item for item in observed.limitations)
    assert any("historical_final_push_unproven" in item for item in observed.limitations)


@pytest.mark.asyncio
async def test_activity_sql_binds_scoped_key_current_version_and_component(monkeypatch):
    captured = []

    async def fake_query(sql, params):
        captured.append((sql, params))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": []})

    monkeypatch.setattr(queries.clickhouse, "_query", fake_query)
    monkeypatch.setattr(queries, "presence_coverage", AsyncMock(return_value={}))
    monkeypatch.setattr(queries, "presence_version_distribution", AsyncMock(return_value={}))
    period = (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC))
    await queries.activity_summary("proj", "mcp", str(LISTING_ID), None, period)
    scope = ("proj", "u", "mcp", str(LISTING_ID), "", "14")
    await queries.activity_sessions(
        "proj", "mcp", str(LISTING_ID), None, period, limit=5, cursor=None, cursor_scope=scope
    )
    for sql, params in captured:
        assert "'proj'" not in sql and str(LISTING_ID) not in sql  # parameterized only
        assert params["param_project_id"] == "proj"
    summary_sql = captured[0][0]
    for join in (
        "c.user_id = p.user_id AND c.harness = p.harness AND c.session_id = p.session_id",
        "c.user_id = a.user_id AND c.harness = a.harness AND c.session_id = a.session_id",
        "a.projection_generation = latest.generation",
    ):
        assert join in summary_sql
    assert "a.component_id = {component_id:String}" in summary_sql
    assert "countIf(status = 'failed') = 0" in summary_sql
    assert captured[0][1]["param_projection_version"] == queries.publication_version()
    assert captured[0][1]["param_supported"] == "['claude-code','pi']"
    page_sql, page_params = captured[-1]
    # Source availability reads only sizes/flags server-side; raw text is never selected.
    assert "empty(raw_line)" in page_sql
    assert "raw_line," not in page_sql and "raw_line AS" not in page_sql and page_params["param_limit"] == 6
    assert "s.user_id" in page_sql and "'source_too_large'" in page_sql
    assert "countIf(source_state = 'source_unavailable')" in summary_sql
    assert "s.revision != p.source_revision, 'stale'" in summary_sql
    assert "arraySort(groupArray(50001)((line_offset, line_hash)))" in summary_sql
    # Keep nested source-revision expressions balanced: malformed SQL used to
    # make both summary and sessions endpoints return HTTP 500 on ClickHouse.
    assert queries._SOURCE_STATES.count("(") == queries._SOURCE_STATES.count(")")
    assert summary_sql.count("(") == summary_sql.count(")")
    assert page_sql.count("(") == page_sql.count(")")
    assert summary_sql.index("s.unavailable_records > 0, 'source_unavailable'") < summary_sql.index(
        "p.generation > 0, 'complete'"
    )
    assert "ORDER BY user_id, harness, session_id" in page_sql
    assert "toDateTime64({after_time" not in page_sql
    assert any("session_capabilities FINAL" in sql for sql, _ in captured)
    with pytest.raises(ValueError):
        await queries.activity_sessions(
            "proj", "mcp", str(LISTING_ID), None, period, limit=101, cursor=None, cursor_scope=scope
        )


@pytest.mark.asyncio
async def test_version_scoped_activation_filters_verified_version_label(monkeypatch):
    captured = []

    async def fake_rows(sql, params):
        captured.append((sql, params))
        return [] if "GROUP BY harness ORDER BY" in sql else [{}]

    monkeypatch.setattr(queries, "_rows", fake_rows)
    monkeypatch.setattr(queries, "presence_coverage", AsyncMock(return_value={}))
    monkeypatch.setattr(queries, "presence_version_distribution", AsyncMock(return_value={}))
    period = (datetime.now(UTC) - timedelta(days=1), datetime.now(UTC))
    with pytest.raises(ValueError, match="verified version label"):
        await queries.activity_summary("p", "mcp", str(LISTING_ID), str(VERSION_ID), period)
    summary = await queries.activity_summary(
        "p", "mcp", str(LISTING_ID), str(VERSION_ID), period, component_version="1.2.3"
    )
    assert summary["activation_actions"]["scope"] == "component_version"
    activation_sql, params = next((sql, params) for sql, params in captured if "session_capabilities" in sql)
    assert "version = {component_version:String}" in activation_sql
    assert params["param_component_version"] == "1.2.3"


@pytest.mark.asyncio
async def test_evidence_sample_prioritizes_published_calls_and_is_bounded(monkeypatch):
    captured = []

    async def fake_rows(sql, params):
        captured.append((sql, params))
        return [
            {
                "user_id": "alice",
                "harness": "claude-code",
                "session_id": f"s{i}",
                "last_event_time": "2026-01-01 00:00:00",
                "projection_state": "complete",
                "source_state": "available",
                "calls": 1,
                "refs": [[1, "id:call", "unknown", "source-hash"]],
            }
            for i in range(3)
        ]

    monkeypatch.setattr(queries, "_rows", fake_rows)
    now = datetime.now(UTC)
    sessions, more = await queries.activity_evidence_sample(
        "p", "mcp", str(LISTING_ID), None, (now - timedelta(days=1), now), limit=2
    )
    sql, params = captured[0]
    assert "_SESSIONS" not in sql and "a.projection_generation = latest.generation" in sql
    assert "ORDER BY (source_state = 'available' AND projection_state = 'complete' AND calls > 0) DESC" in sql
    assert params["param_project_id"] == "p" and params["param_limit"] == 3
    assert more and len(sessions) == 2
    assert sessions[0]["source_references"][0]["_source_line_hash"] == "source-hash"


@pytest.mark.asyncio
async def test_session_page_hides_non_complete_counts_and_emits_cursor_only_when_more(monkeypatch):
    rows = [
        {
            "user_id": "u",
            "harness": "claude-code",
            "session_id": f"s{i}",
            "last_event_time": f"2026-01-01 00:00:0{i}.000",
            "projection_state": state,
            "source_state": "available",
            "calls": 2,
            "successes": 1,
            "errors": 1,
            "unknowns": 0,
            "refs": [[4, "id:a", "success", "hash-a"], [5, "id:b", "error", "hash-b"]],
        }
        for i, state in enumerate(["complete", "pending", "complete"])
    ]
    monkeypatch.setattr(queries, "_rows", AsyncMock(return_value=rows))
    end = datetime.now(UTC)
    period = (end - timedelta(days=14), end)
    scope = ("p", "u", "mcp", str(LISTING_ID), "", "14")
    sessions, cursor = await queries.activity_sessions(
        "p", "mcp", str(LISTING_ID), None, period, limit=2, cursor=None, cursor_scope=scope
    )
    assert [s["session_id"] for s in sessions] == ["s0", "s1"]
    assert sessions[0]["observed_calls"] == 2 and sessions[0]["source_references"][0]["source_block_key"] == "id:a"
    from schemas.component_activity import ActivitySession

    public = ActivitySession.model_validate(sessions[0]).model_dump()
    assert "_source_line_hash" not in public["source_references"][0]
    assert sessions[1]["observed_calls"] == 0 and sessions[1]["source_references"] == []
    assert queries.decode_cursor(cursor, scope=scope)[1] == ["u", "claude-code", "s1"]


@pytest.mark.asyncio
async def test_presence_coverage_counts_in_clickhouse_without_materializing_cohort(monkeypatch):
    from services.layer_components import queries as presence_queries

    sqls = []

    async def fake_rows(sql, _params):
        sqls.append(sql)
        if "uniqExact(user_id)" in sql:
            return [{"present_sessions": 1_000_000, "present_users": 200_000}]
        return [{"eligible_sessions": 1_000_002}]

    monkeypatch.setattr(presence_queries, "_rows", fake_rows)
    monkeypatch.setattr(
        presence_queries, "presence_cohort", AsyncMock(side_effect=AssertionError("cohort must not be materialized"))
    )
    period = (datetime.now(UTC) - timedelta(days=1), datetime.now(UTC))
    result = await presence_queries.presence_coverage("project", "mcp", str(LISTING_ID), None, period)
    assert (result["present_sessions"], result["present_users"]) == (1_000_000, 200_000)
    assert len(sqls) == 2 and "FROM (" in sqls[1]


@pytest.mark.asyncio
async def test_page_key_does_not_repeat_session_whose_last_event_time_advances(monkeypatch):
    scope = ("p", "owner", "mcp", str(LISTING_ID), "", "1")
    period = (datetime.now(UTC) - timedelta(days=1), datetime.now(UTC))
    records = [
        {
            "user_id": "u",
            "harness": "claude-code",
            "session_id": sid,
            "last_event_time": "2026-01-01 00:00:00.000",
            "projection_state": "pending",
            "source_state": "available",
        }
        for sid in ("s0", "s1", "s2")
    ]

    async def fake_rows(sql, params):
        assert "ORDER BY user_id, harness, session_id" in sql
        after = params.get("param_after_session", "")
        return [row for row in records if row["session_id"] > after][: params["param_limit"]]

    monkeypatch.setattr(queries, "_rows", fake_rows)
    first, cursor = await queries.activity_sessions(
        "p", "mcp", str(LISTING_ID), None, period, limit=1, cursor=None, cursor_scope=scope
    )
    assert [item["session_id"] for item in first] == ["s0"]
    records[0]["last_event_time"] = "2026-01-02 00:00:00.000"
    _, key = queries.decode_cursor(cursor, scope=scope)
    second, _ = await queries.activity_sessions(
        "p", "mcp", str(LISTING_ID), None, period, limit=1, cursor=key, cursor_scope=scope
    )
    assert [item["session_id"] for item in second] == ["s1"]


def test_source_availability_reasons_are_distinct_and_never_no_observed_calls():
    from services.component_activity.coverage import build_coverage

    presence = {"present_sessions": 4, "present_users": 1}
    activity = {
        "supported_present_sessions": 4,
        "source_missing_sessions": 1,
        "source_unavailable_sessions": 1,
        "source_incomplete_sessions": 1,
        "source_too_large_sessions": 1,
    }
    coverage = build_coverage(presence, activity, 257)
    assert coverage.attribution_state == "attribution_not_possible"
    assert coverage.usage_rate is None and coverage.usage_rate_denominator_sessions == 0
    for reason in (
        "source_missing_sessions",
        "source_unavailable_sessions",
        "source_incomplete_sessions",
        "source_too_large_sessions",
    ):
        assert reason in coverage.reasons


@pytest.mark.asyncio
async def test_version_distribution_reads_only_published_present_scoped_sessions(monkeypatch):
    from services.layer_components import queries as presence_queries

    calls = []

    async def rows(sql, params):
        calls.append((sql, params))
        return [{"version": "1.0.0", "sessions": 2}]

    monkeypatch.setattr(presence_queries, "_rows", rows)
    period = (datetime.now(UTC) - timedelta(days=1), datetime.now(UTC))
    distribution = await presence_queries.presence_version_distribution(
        "scoped-project", "mcp", str(LISTING_ID), str(VERSION_ID), period
    )
    assert distribution == {"1.0.0": 2}
    sql, params = calls[0]
    assert "SELECT DISTINCT p.user_id, p.harness, p.session_id" in sql
    assert "c.user_id = p.user_id AND c.layer_hash = p.layer_hash AND c.harness = p.harness" in sql
    assert "s.harness = present.harness" in sql
    assert "c.extraction_generation = published.generation" in sql
    assert "published.conflict = 0" in sql
    assert "verification_status = 'verified'" in sql
    assert "{component_version_id:String}" in sql
    assert params["param_project_id"] == "scoped-project"
    assert params["param_component_version_id"] == str(VERSION_ID)
