# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Authorization matrix for the insights routes.

Insight reports are derived from an agent's private session telemetry, so every
insights route needs two independent gates:

1. visibility (`check_listing_visibility_async` / `apply_visibility_filter`),
   which answers "does this agent exist for you" and produces 404.
2. permission (`_require_agent_edit_access`), which answers "do you own it" and
   produces 403. Only the creator, a co-author, or an admin passes.

Team membership feeds gate 1 only. A team member who is not the creator, a
co-author, or an admin therefore resolves a team-private agent and is refused
with 403. A global reviewer is privileged for gate 1 and refused by gate 2 for
the same reason: reviewing a registry submission never requires reading the
session transcripts of everyone who ran the agent.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _result(value):
    """Mock a SQLAlchemy Result whose scalar_one_or_none yields *value*."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalars.return_value.all.return_value = [value] if value is not None else []
    return result


def _user(role=None, user_id: uuid.UUID | None = None):
    from models.user import UserRole

    return SimpleNamespace(id=user_id or uuid.uuid4(), role=role or UserRole.user)


def _agent(*, created_by: uuid.UUID, is_private: bool = False, team_id=None, co_authors=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="ultra-pi",
        created_by=created_by,
        is_private=is_private,
        team_id=team_id,
        co_authors=co_authors or [],
    )


def _report(agent_id: uuid.UUID):
    from models.insight_report import InsightReportStatus

    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid.uuid4(),
        agent_id=agent_id,
        agent_version_id=None,
        agent_version="1.0.0",
        version_scope="canonical_and_dirty",
        comparison_agent_version_id=None,
        comparison_agent_version=None,
        triggered_by=None,
        status=InsightReportStatus.completed,
        period_start=now,
        period_end=now,
        metrics={},
        narrative={},
        sessions_analyzed=3,
        llm_model_used=None,
        error_message=None,
        started_at=now,
        completed_at=now,
        created_at=now,
    )


def _agent_db(agent, *, membership=None):
    """DB mock for routes that resolve an agent by id."""
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_result(agent))
    db.scalar = AsyncMock(return_value=membership)
    return db


def _report_db(report, agent, *, membership=None):
    """DB mock for report routes: they load the report, then its agent."""
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_result(report), _result(agent)])
    db.scalar = AsyncMock(return_value=membership)
    return db


# Routes reached through _resolve_insights_agent.
AGENT_ROUTES = ("agent_session_count", "generate_insight", "list_reports", "clear_agent_reports")

# Routes reached through _authorize_report_agent.
REPORT_ROUTES = ("get_report", "export_report_html", "delete_report", "apply_report_suggestions")


async def _call_agent_route(name: str, agent_id: str, db, user):
    import api.routes.insights as insights

    if name == "agent_session_count":
        return await insights.agent_session_count(agent_id, None, db, user)
    if name == "generate_insight":
        return await insights.generate_insight(agent_id, None, db, user)
    return await getattr(insights, name)(agent_id, db, user)


async def _call_report_route(name: str, report_id: str, db, user):
    import api.routes.insights as insights

    if name == "apply_report_suggestions":
        with (
            patch("services.dynamic_settings.get_bool", new=AsyncMock(return_value=True)),
            patch("services.insights.self_learn.apply_insight_suggestions", new=AsyncMock(return_value={})),
        ):
            return await insights.apply_report_suggestions(report_id, None, db, user)
    if name == "export_report_html":
        with patch("api.routes.insights.render_report_html", return_value="<html></html>"):
            return await insights.export_report_html(report_id, db, user)
    return await getattr(insights, name)(report_id, db, user)


# ---------------------------------------------------------------------------
# _require_agent_edit_access: the helper the regression neutered
# ---------------------------------------------------------------------------


def test_effective_agent_permission_never_returns_none():
    """The regression compared against 'none', which this helper never returns.

    Any insights gate written as `== "none"` is therefore dead code. This test
    pins the helper's real return set so that shape cannot come back unnoticed.
    """
    from api.deps import get_effective_agent_permission
    from models.user import UserRole

    creator_id = uuid.uuid4()
    agent = _agent(created_by=creator_id)

    assert get_effective_agent_permission(agent, _user(user_id=creator_id)) == "owner"
    assert get_effective_agent_permission(agent, _user(role=UserRole.admin)) == "owner"
    assert get_effective_agent_permission(agent, _user(role=UserRole.reviewer)) == "view"
    assert get_effective_agent_permission(agent, _user()) == "view"


@pytest.mark.parametrize("role_name", ["user", "reviewer"])
def test_require_agent_edit_access_rejects_non_owners(role_name):
    """A stranger and a global reviewer are both refused, whatever the agent."""
    from api.routes.insights import _require_agent_edit_access
    from models.user import UserRole

    agent = _agent(created_by=uuid.uuid4())

    with pytest.raises(HTTPException) as exc:
        _require_agent_edit_access(agent, _user(role=getattr(UserRole, role_name)))

    assert exc.value.status_code == 403
    assert exc.value.detail == "Insufficient permissions for this agent"


def test_require_agent_edit_access_allows_creator_co_author_and_admin():
    from api.routes.insights import _require_agent_edit_access
    from models.user import UserRole

    creator_id = uuid.uuid4()
    co_author_id = uuid.uuid4()
    agent = _agent(created_by=creator_id, co_authors=[co_author_id])

    _require_agent_edit_access(agent, _user(user_id=creator_id))
    _require_agent_edit_access(agent, _user(user_id=co_author_id))
    _require_agent_edit_access(agent, _user(role=UserRole.admin))
    _require_agent_edit_access(agent, _user(role=UserRole.super_admin))


# ---------------------------------------------------------------------------
# Agent-scoped routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("route", AGENT_ROUTES)
async def test_agent_routes_reject_unrelated_user_on_public_agent(route):
    """REGRESSION: any authenticated user could read insights for any public agent."""
    agent = _agent(created_by=uuid.uuid4(), is_private=False)
    stranger = _user()

    with pytest.raises(HTTPException) as exc:
        await _call_agent_route(route, str(agent.id), _agent_db(agent), stranger)

    assert exc.value.status_code == 403
    assert exc.value.detail == "Insufficient permissions for this agent"


@pytest.mark.asyncio
@pytest.mark.parametrize("route", AGENT_ROUTES)
async def test_agent_routes_hide_team_private_agent_from_non_member(route):
    """Visibility, not permission: an outsider must not learn the agent exists."""
    agent = _agent(created_by=uuid.uuid4(), is_private=True, team_id=uuid.uuid4())
    outsider = _user()

    with pytest.raises(HTTPException) as exc:
        await _call_agent_route(route, str(agent.id), _agent_db(agent, membership=None), outsider)

    assert exc.value.status_code == 404
    assert exc.value.detail == "Agent not found"


@pytest.mark.asyncio
@pytest.mark.parametrize("route", AGENT_ROUTES)
async def test_agent_routes_reject_team_member_who_does_not_own_the_agent(route):
    """Documented answer: a member sees the agent (no 404) but cannot read insights."""
    agent = _agent(created_by=uuid.uuid4(), is_private=True, team_id=uuid.uuid4())
    member = _user()
    db = _agent_db(agent, membership=uuid.uuid4())

    with pytest.raises(HTTPException) as exc:
        await _call_agent_route(route, str(agent.id), db, member)

    assert exc.value.status_code == 403
    db.scalar.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", ["creator", "co_author", "admin"])
async def test_list_reports_allows_owners_and_admins(actor):
    from api.routes.insights import list_reports
    from models.user import UserRole

    creator_id = uuid.uuid4()
    co_author_id = uuid.uuid4()
    agent = _agent(created_by=creator_id, co_authors=[co_author_id])
    users = {
        "creator": _user(user_id=creator_id),
        "co_author": _user(user_id=co_author_id),
        "admin": _user(role=UserRole.admin),
    }

    empty_reports = MagicMock()
    empty_reports.scalars.return_value.all.return_value = []
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_result(agent), empty_reports])
    db.scalar = AsyncMock(return_value=None)

    assert await list_reports(str(agent.id), db, users[actor]) == []


# ---------------------------------------------------------------------------
# Report-scoped routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("route", REPORT_ROUTES)
async def test_report_routes_reject_unrelated_user_on_public_agent(route):
    """REGRESSION: read, export, delete and apply must all gate on ownership.

    delete and apply additionally sit behind require_role(admin) at the routing
    layer; the in-function gate is asserted here as defense in depth so a future
    change to the role dependency cannot silently open them.
    """
    agent = _agent(created_by=uuid.uuid4(), is_private=False)
    report = _report(agent.id)
    stranger = _user()

    with pytest.raises(HTTPException) as exc:
        await _call_report_route(route, str(report.id), _report_db(report, agent), stranger)

    assert exc.value.status_code == 403
    assert exc.value.detail == "Insufficient permissions for this agent"


@pytest.mark.asyncio
@pytest.mark.parametrize("route", REPORT_ROUTES)
async def test_report_routes_hide_team_private_agent_from_non_member(route):
    agent = _agent(created_by=uuid.uuid4(), is_private=True, team_id=uuid.uuid4())
    report = _report(agent.id)
    outsider = _user()

    with pytest.raises(HTTPException) as exc:
        await _call_report_route(route, str(report.id), _report_db(report, agent, membership=None), outsider)

    assert exc.value.status_code == 404
    assert exc.value.detail == "Report not found"


@pytest.mark.asyncio
@pytest.mark.parametrize("route", REPORT_ROUTES)
async def test_report_routes_reject_team_member_who_does_not_own_the_agent(route):
    agent = _agent(created_by=uuid.uuid4(), is_private=True, team_id=uuid.uuid4())
    report = _report(agent.id)
    db = _report_db(report, agent, membership=uuid.uuid4())

    with pytest.raises(HTTPException) as exc:
        await _call_report_route(route, str(report.id), db, _user())

    assert exc.value.status_code == 403
    db.scalar.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("route", REPORT_ROUTES)
async def test_report_routes_fail_loudly_when_the_agent_row_is_missing(route):
    """A report whose agent row is gone is unreachable, never an unchecked pass.

    delete and apply previously skipped every check in that case because the
    guard read `if agent and ...`.
    """
    agent_id = uuid.uuid4()
    report = _report(agent_id)

    with pytest.raises(HTTPException) as exc:
        await _call_report_route(route, str(report.id), _report_db(report, None), _user())

    assert exc.value.status_code == 404
    assert exc.value.detail == "Report not found"


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["get_report", "export_report_html"])
@pytest.mark.parametrize("actor", ["creator", "co_author", "admin"])
async def test_report_routes_allow_owners_and_admins(route, actor):
    from models.user import UserRole

    creator_id = uuid.uuid4()
    co_author_id = uuid.uuid4()
    agent = _agent(created_by=creator_id, co_authors=[co_author_id])
    report = _report(agent.id)
    users = {
        "creator": _user(user_id=creator_id),
        "co_author": _user(user_id=co_author_id),
        "admin": _user(role=UserRole.admin),
    }

    response = await _call_report_route(route, str(report.id), _report_db(report, agent), users[actor])

    assert response is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["delete_report", "apply_report_suggestions"])
async def test_admin_only_report_routes_allow_admins(route):
    from models.user import UserRole

    agent = _agent(created_by=uuid.uuid4())
    report = _report(agent.id)

    result = await _call_report_route(route, str(report.id), _report_db(report, agent), _user(role=UserRole.admin))

    assert result is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("route", REPORT_ROUTES)
async def test_report_routes_reject_global_reviewer(route):
    """Reviewers are privileged for review status but not for session telemetry.

    The agent here is PUBLIC, so gate 1 (visibility) admits the reviewer and the
    403 comes from gate 2 (ownership) alone. That is what proves the two gates are
    independent. A team-private agent would prove nothing, because gate 1 already
    rejects a global reviewer who is not in the team.
    """
    from models.user import UserRole

    agent = _agent(created_by=uuid.uuid4(), is_private=False)
    report = _report(agent.id)

    with pytest.raises(HTTPException) as exc:
        await _call_report_route(route, str(report.id), _report_db(report, agent), _user(role=UserRole.reviewer))

    assert exc.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("route", REPORT_ROUTES)
async def test_report_routes_hide_a_team_private_agent_from_a_global_reviewer(route):
    """Gate 1 answers first, and it no longer admits a global reviewer.

    Team-private content is reviewed inside its own teamspace, so an outside
    reviewer gets "not found" rather than the 403 that would confirm the report
    exists.
    """
    from models.user import UserRole

    agent = _agent(created_by=uuid.uuid4(), is_private=True, team_id=uuid.uuid4())
    report = _report(agent.id)

    with pytest.raises(HTTPException) as exc:
        await _call_report_route(route, str(report.id), _report_db(report, agent), _user(role=UserRole.reviewer))

    assert exc.value.status_code == 404


def test_no_route_bypasses_the_permission_gate():
    """Every agent-touching route in insights.py routes through one of the gates.

    A new handler that loads an agent without calling _resolve_insights_agent or
    _authorize_report_agent fails here rather than shipping unguarded.
    """
    import ast
    import inspect

    import api.routes.insights as insights

    source = inspect.getsource(insights)
    tree = ast.parse(source)
    gates = {
        "_resolve_insights_agent",
        "_authorize_report_agent",
        "_require_agent_edit_access",
        "_authorize_report",
        "_authorize_component",
    }
    exempt = {"insights_status"} | gates

    unguarded = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef) or node.name.startswith("_"):
            continue
        if node.name in exempt:
            continue
        called = {
            child.func.id
            for child in ast.walk(node)
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
        }
        if not called & gates:
            unguarded.append(node.name)

    assert unguarded == [], f"insights routes missing an authorization gate: {unguarded}"
