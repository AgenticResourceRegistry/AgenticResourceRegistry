# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Focused contracts and failure coverage for the review routes."""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.orm import aliased

from api.deps import get_db
from api.routes import review
from models.agent import Agent, AgentStatus, AgentVersion
from models.mcp import ListingStatus
from models.skill import SkillVersion
from models.user import UserRole
from schemas.mcp import ReviewActionRequest
from services.security_events import EventType, Severity
from services.teamspace import ReviewScope


@pytest.fixture(autouse=True)
def _lock_service_stub(monkeypatch):
    """These routes run on a mocked session; the lock service has its own tests."""
    import services.agent_lock as agent_lock

    monkeypatch.setattr(agent_lock, "lock_agent_version", AsyncMock(return_value={}))


NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
ACTOR_ID = uuid.UUID(int=1)
SUBMITTER_ID = uuid.UUID(int=2)
TEAM_ID = uuid.UUID(int=3)
OTHER_TEAM_ID = uuid.UUID(int=4)

ADMIN_SCOPE = ReviewScope(is_admin=True, is_global_reviewer=True, team_ids=frozenset())
GLOBAL_SCOPE = ReviewScope(is_admin=False, is_global_reviewer=True, team_ids=frozenset())
TEAM_SCOPE = ReviewScope(is_admin=False, is_global_reviewer=False, team_ids=frozenset({TEAM_ID}))
PUBLIC_TEAM_SCOPE = ReviewScope(
    is_admin=False,
    is_global_reviewer=False,
    team_ids=frozenset({TEAM_ID}),
    public_team_ids=frozenset({TEAM_ID}),
)
EMPTY_SCOPE = ReviewScope(is_admin=False, is_global_reviewer=False, team_ids=frozenset())

_UNSET = object()


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is not None else NOW.replace(tzinfo=None)


def _actor(role=UserRole.admin, *, user_id=ACTOR_ID):
    return SimpleNamespace(
        id=user_id,
        role=role,
        email="reviewer@example.test",
        username="reviewer",
    )


def _db():
    database = MagicMock()
    database.execute = AsyncMock()
    database.flush = AsyncMock()
    database.commit = AsyncMock()
    database.refresh = AsyncMock()
    database.add = MagicMock()
    return database


def _result(*, scalar=_UNSET, scalars=(), rows=()):
    scalar_rows = list(scalars)
    result = MagicMock()
    if scalar is _UNSET:
        scalar = scalar_rows[0] if scalar_rows else None
    result.scalar_one_or_none.return_value = scalar
    result.scalars.return_value.all.return_value = scalar_rows
    result.all.return_value = list(rows)
    return result


def _sql(statement) -> str:
    return " ".join(str(statement).split())


def _pending_skill_ids(statement) -> bool:
    sql = _sql(statement)
    return "FROM skill_versions" in sql and "skill_versions.status" in sql


def _params(statement) -> dict:
    return statement.compile().params


def _bound_values(statement) -> set:
    values = set()
    for value in _params(statement).values():
        values.update(value if isinstance(value, (list, tuple, set)) else [value])
    return values


def _assert_http(exc, status: int, detail) -> None:
    assert exc.value.status_code == status
    assert exc.value.detail == detail


def _component_ref(component_type: str, component_id: uuid.UUID, name=""):
    return SimpleNamespace(component_type=component_type, component_id=component_id, component_name=name)


def _pending_version(
    listing_id: uuid.UUID,
    *,
    version="1.0.0",
    created_at=NOW,
    released_at=NOW,
    released_by=SUBMITTER_ID,
    status=ListingStatus.pending,
    components=None,
    active=False,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        listing_id=listing_id,
        agent_id=listing_id,
        version=version,
        description=f"Description {version}",
        prompt="Review prompt",
        model_name="review-model",
        model_config_json={"temperature": 0},
        external_mcps=[],
        supported_harnesses=["pi"],
        required_capabilities=["mcp_servers"],
        status=status,
        rejection_reason=None,
        released_by=released_by,
        released_at=released_at,
        created_at=created_at,
        components=[] if components is None else components,
        is_editing=active,
        editing_by=uuid.UUID(int=99) if active else None,
        editing_since=datetime.now(UTC) if active else None,
        gaming_flags={"score": 1},
        success_criteria={"purpose": "review"},
        reviewed_by=None,
        reviewed_at=None,
    )


def _orm_listing(
    listing_type: str,
    *,
    status=ListingStatus.pending,
    bundle_id=None,
    is_private=False,
    team_id=None,
    index=0,
):
    listing_id = uuid.UUID(int=100 + index)
    version_id = uuid.UUID(int=200 + index)
    listing_model = review.LISTING_MODELS[listing_type]
    version_model = review.VERSION_MODELS[listing_type]
    listing = listing_model(
        id=listing_id,
        name=f"{listing_type} listing",
        namespace="acme",
        slug=f"{listing_type}-listing",
        owner="acme",
        submitted_by=SUBMITTER_ID,
        is_private=is_private,
        team_id=team_id,
        bundle_id=bundle_id,
        created_at=NOW,
        updated_at=NOW,
    )
    version = version_model(
        id=version_id,
        listing_id=listing_id,
        version="2.0.0",
        description=f"{listing_type} description",
        status=status,
        rejection_reason="old reason",
        released_by=SUBMITTER_ID,
        released_at=NOW,
        created_at=NOW,
        is_editing=False,
        editing_by=None,
        editing_since=None,
    )
    listing.latest_version = version
    listing.latest_version_id = version.id
    listing.versions = [version]
    return listing, version


def _orm_agent(*, latest_status=AgentStatus.approved, team_id=None):
    agent = Agent(
        id=uuid.UUID(int=500),
        name="review agent",
        namespace="acme",
        slug="review-agent",
        owner="acme",
        created_by=SUBMITTER_ID,
        team_id=team_id,
        is_private=False,
        created_at=NOW,
        updated_at=NOW,
    )
    latest = AgentVersion(
        id=uuid.UUID(int=501),
        agent_id=agent.id,
        version="1.0.0",
        description="Current release",
        prompt="Current prompt",
        model_name="review-model",
        status=latest_status,
        released_by=SUBMITTER_ID,
        released_at=NOW,
        created_at=NOW,
    )
    agent.latest_version = latest
    agent.latest_version_id = latest.id
    agent.versions = [latest]
    return agent, latest


@pytest.fixture
def decision_boundaries(monkeypatch):
    scope = AsyncMock(return_value=ADMIN_SCOPE)
    decide = AsyncMock()
    invalidate = AsyncMock()
    publish = MagicMock(return_value="publish-awaitable")
    create_task = MagicMock()
    monkeypatch.setattr(review, "_require_review_scope", scope)
    monkeypatch.setattr(review.inbox, "on_review_decided", decide)
    monkeypatch.setattr(review, "invalidate_namespace", invalidate)
    monkeypatch.setattr(review, "redis_publish", publish)
    monkeypatch.setattr(review.asyncio, "create_task", create_task)
    monkeypatch.setattr(review, "datetime", FrozenDateTime)
    return SimpleNamespace(
        scope=scope,
        decide=decide,
        invalidate=invalidate,
        publish=publish,
        create_task=create_task,
    )


@pytest.mark.asyncio
async def test_require_review_scope_emits_exact_denial_and_propagates_resolution_failures(monkeypatch):
    database = _db()
    actor = _actor(UserRole.user)
    scope = AsyncMock(return_value=EMPTY_SCOPE)
    emit = AsyncMock()
    monkeypatch.setattr(review, "review_scope", scope)
    monkeypatch.setattr(review, "emit_security_event", emit)

    with pytest.raises(HTTPException) as exc:
        await review._require_review_scope(database, actor)

    _assert_http(exc, 403, "Insufficient permissions")
    scope.assert_awaited_once_with(database, actor)
    event = emit.await_args.args[0]
    assert event.event_type is EventType.PERMISSION_DENIED
    assert event.severity is Severity.WARNING
    assert (event.outcome, event.actor_id, event.actor_email, event.actor_role) == (
        "failure",
        str(actor.id),
        actor.email,
        "user",
    )
    assert event.detail == "Review access requires a global review role or a team owner or reviewer seat"

    scope.reset_mock(side_effect=True)
    emit.reset_mock()
    scope.side_effect = RuntimeError("scope unavailable")
    with pytest.raises(RuntimeError, match="scope unavailable"):
        await review._require_review_scope(database, actor)
    emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_require_review_scope_returns_capability_without_security_event(monkeypatch):
    database = _db()
    actor = _actor(UserRole.reviewer)
    resolve = AsyncMock(return_value=GLOBAL_SCOPE)
    emit = AsyncMock()
    monkeypatch.setattr(review, "review_scope", resolve)
    monkeypatch.setattr(review, "emit_security_event", emit)

    assert await review._require_review_scope(database, actor) is GLOBAL_SCOPE
    emit.assert_not_awaited()


@pytest.mark.parametrize(
    ("scope", "team_id", "allowed"),
    [
        (TEAM_SCOPE, None, True),
        (TEAM_SCOPE, TEAM_ID, True),
        (TEAM_SCOPE, OTHER_TEAM_ID, False),
        (GLOBAL_SCOPE, OTHER_TEAM_ID, True),
        (ADMIN_SCOPE, OTHER_TEAM_ID, True),
    ],
)
def test_team_filter_contract(scope, team_id, allowed):
    if allowed:
        review._check_team_filter(team_id, scope)
        return
    with pytest.raises(HTTPException) as exc:
        review._check_team_filter(team_id, scope)
    _assert_http(exc, 403, "You do not review for this teamspace")


def test_item_authorization_and_scope_visibility_are_exact():
    public = SimpleNamespace(is_private=False, team_id=TEAM_ID)
    private = SimpleNamespace(is_private=True, team_id=TEAM_ID)
    other_private = SimpleNamespace(is_private=True, team_id=OTHER_TEAM_ID)

    review._authorize_item(public, GLOBAL_SCOPE)
    review._authorize_item(public, PUBLIC_TEAM_SCOPE)
    review._authorize_item(private, TEAM_SCOPE)
    assert review._in_scope(private, TEAM_SCOPE, TEAM_ID) is True
    assert review._in_scope(private, TEAM_SCOPE, OTHER_TEAM_ID) is False
    assert review._in_scope(other_private, TEAM_SCOPE, None) is False

    with pytest.raises(HTTPException) as hidden:
        review._authorize_item(other_private, TEAM_SCOPE)
    _assert_http(hidden, 404, "Submission not found")

    with pytest.raises(HTTPException) as public_denied:
        review._authorize_item(public, TEAM_SCOPE)
    _assert_http(
        public_denied,
        403,
        "Public item is outside your review scope",
    )


@pytest.mark.asyncio
async def test_find_listing_resolves_unique_prefix_and_reports_cross_type_ambiguity(monkeypatch):
    mcp = SimpleNamespace(id=uuid.UUID(int=10))
    skill = SimpleNamespace(id=uuid.UUID(int=11))

    async def resolve(model, identifier, database, *, load_options=None):
        assert load_options == (review._SKILL_REVIEW_LOOKUP_OPTIONS if model is review.SkillListing else None)
        assert identifier == "abcd"
        assert database is db
        if model is review.LISTING_MODELS["mcp"]:
            return mcp
        if model is review.LISTING_MODELS["skill"]:
            return skill
        raise HTTPException(status_code=404, detail="missing")

    db = _db()
    monkeypatch.setattr(review, "resolve_prefix_id", resolve)

    with pytest.raises(HTTPException) as exc:
        await review._find_listing("abcd", db)

    _assert_http(exc, 400, "Prefix 'abcd' matches records across multiple types: mcp, skill")
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_find_listing_propagates_ambiguous_prefix_within_a_type(monkeypatch):
    db = _db()
    resolve = AsyncMock(side_effect=HTTPException(status_code=400, detail="Ambiguous prefix 'abcd'"))
    monkeypatch.setattr(review, "resolve_prefix_id", resolve)

    with pytest.raises(HTTPException) as exc:
        await review._find_listing("abcd", db)

    _assert_http(exc, 400, "Ambiguous prefix 'abcd'")
    assert resolve.await_count == 1
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_find_listing_falls_back_to_exact_name_with_exact_queries(monkeypatch):
    db = _db()
    listing = SimpleNamespace(id=uuid.UUID(int=12), name="named listing")
    monkeypatch.setattr(
        review,
        "resolve_prefix_id",
        AsyncMock(side_effect=HTTPException(status_code=404, detail="missing")),
    )
    db.execute.side_effect = [
        _result(),
        _result(scalar=listing),
    ]

    assert await review._find_listing("named listing", db) == ("skill", listing)
    assert db.execute.await_count == 2
    for index, listing_type in enumerate(("mcp", "skill")):
        statement = db.execute.await_args_list[index].args[0]
        assert f"FROM {review.LISTING_MODELS[listing_type].__tablename__}" in _sql(statement)
        assert set(_params(statement).values()) == {"named listing"}


@pytest.mark.asyncio
@pytest.mark.parametrize("identifier", ["x", "acme/review-tool", "not-a-uuid"])
async def test_find_listing_short_canonical_and_malformed_identifiers_use_name_fallback(monkeypatch, identifier):
    db = _db()

    async def too_short_or_missing(model, value, database, *, load_options=None):
        detail = "Prefix is too short" if value == "x" else "missing"
        status = 400 if value == "x" else 404
        raise HTTPException(status_code=status, detail=detail)

    monkeypatch.setattr(review, "resolve_prefix_id", too_short_or_missing)
    db.execute.return_value = _result()

    assert await review._find_listing(identifier, db) == (None, None)
    assert db.execute.await_count == len(review.LISTING_MODELS)
    for awaited in db.execute.await_args_list:
        assert set(_params(awaited.args[0]).values()) == {identifier}


@pytest.mark.asyncio
async def test_find_listing_returns_unique_uuid_hit_and_propagates_database_failure(monkeypatch):
    db = _db()
    listing = SimpleNamespace(id=uuid.UUID(int=13))
    calls = []

    async def resolve(model, identifier, database, *, load_options=None):
        calls.append(model)
        if model is review.LISTING_MODELS["hook"]:
            return listing
        raise HTTPException(status_code=404, detail="missing")

    monkeypatch.setattr(review, "resolve_prefix_id", resolve)
    assert await review._find_listing(str(listing.id), db) == ("hook", listing)
    assert calls == list(review.LISTING_MODELS.values())

    monkeypatch.setattr(review, "resolve_prefix_id", AsyncMock(side_effect=RuntimeError("database offline")))
    with pytest.raises(RuntimeError, match="database offline"):
        await review._find_listing(str(listing.id), db)


@pytest.mark.asyncio
async def test_component_readiness_reads_the_pinned_release_not_the_latest():
    from sqlalchemy import select

    from models.mcp import McpVersion
    from tests import discovery_support as ds

    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    async with maker() as db:
        owner = await ds.user(db)
        # Pinned to an approved release whose listing has since gained a pending one.
        ready = await ds.mcp(db, owner, name="Ready")
        ready_pin = (await db.execute(select(McpVersion).where(McpVersion.listing_id == ready.id))).scalar_one()
        pending_release = McpVersion(
            id=uuid.uuid4(),
            listing_id=ready.id,
            version="2.0.0",
            description="next",
            status=ListingStatus.pending,
            released_by=owner.id,
            released_at=ds.NOW,
        )
        db.add(pending_release)
        await db.flush()
        ready.latest_version_id = pending_release.id
        # Pinned to a pending release of a listing whose latest release is approved.
        waiting = await ds.skill(db, owner, name="Waiting", version="1.0.0", status=ListingStatus.pending)
        waiting_pin = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == waiting.id))).scalar_one()
        await ds.add_skill_version(db, waiting, owner, version="1.1.0", status=ListingStatus.approved)
        components = [
            SimpleNamespace(
                component_type="mcp",
                component_id=ready.id,
                component_name="",
                resolved_version="1.4.2",
                resolved_version_id=ready_pin.id,
            ),
            SimpleNamespace(
                component_type="skill",
                component_id=waiting.id,
                component_name="",
                resolved_version="1.0.0",
                resolved_version_id=waiting_pin.id,
            ),
            _component_ref("unknown", uuid.UUID(int=24)),
            _component_ref("hook", uuid.UUID(int=23)),
        ]

        ready_flag, blockers = await review._check_agent_components_ready(components, db)

    await engine.dispose()
    assert ready_flag is False
    assert blockers[0] == {
        "component_type": "skill",
        "component_id": str(waiting.id),
        "name": "Waiting",
        "version": "1.0.0",
        "status": "pending",
    }
    assert {(item["component_type"], item["status"]) for item in blockers[1:]} == {
        ("unknown", "missing_listing"),
        ("hook", "missing_listing"),
    }


@pytest.mark.asyncio
async def test_component_readiness_empty_path_skips_the_database():
    db = _db()
    assert await review._check_agent_components_ready([], db) == (True, [])
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_component_queue_serializes_bundle_validation_submitter_and_visibility(monkeypatch):
    db = _db()
    listing_id = uuid.UUID(int=30)
    hidden_id = uuid.UUID(int=31)
    active_id = uuid.UUID(int=32)
    bundle_id = uuid.UUID(int=33)
    newest = _pending_version(listing_id, version="2.0.0", released_at=NOW)
    older = _pending_version(listing_id, version="1.0.0", released_at=NOW - timedelta(days=1))
    hidden_version = _pending_version(hidden_id)
    active_version = _pending_version(active_id, active=True)
    validation = SimpleNamespace(stage="manifest", passed=True, details="ok", run_at=NOW)
    listing = SimpleNamespace(
        id=listing_id,
        name="visible mcp",
        description="listing fallback",
        owner="acme",
        submitted_by=SUBMITTER_ID,
        created_at=NOW - timedelta(days=5),
        bundle_id=bundle_id,
        is_private=False,
        team_id=None,
        validation_results=[validation],
        mcp_validated=True,
    )
    hidden = SimpleNamespace(
        id=hidden_id,
        name="secret mcp",
        owner="secret",
        submitted_by=uuid.UUID(int=34),
        created_at=NOW,
        bundle_id=None,
        is_private=True,
        team_id=OTHER_TEAM_ID,
        validation_results=[],
        mcp_validated=False,
    )
    statements = []

    async def execute(statement):
        statements.append(statement)
        sql = _sql(statement)
        if "FROM mcp_versions" in sql:
            return _result(scalars=[newest, older, active_version, hidden_version])
        if "FROM mcp_listings" in sql:
            return _result(scalars=[listing, hidden])
        if "FROM component_bundles" in sql:
            return _result(rows=[(bundle_id, "review bundle")])
        if "FROM users" in sql:
            return _result(scalars=[SimpleNamespace(id=SUBMITTER_ID, username="submitter", email="fallback@test")])
        raise AssertionError(sql)

    db.execute.side_effect = execute
    listing.is_fork = True
    provenance = AsyncMock(return_value={listing.id: {"available": True, "id": uuid.UUID(int=90)}})
    monkeypatch.setattr(review, "provenance_for_many", provenance)
    actor = _actor()
    items = await review._query_pending_components(db, GLOBAL_SCOPE, "mcp", current_user=actor)
    provenance.assert_awaited_once_with([listing], actor, db)

    assert items == [
        {
            "type": "mcp",
            "id": str(listing_id),
            "name": "visible mcp",
            "description": "Description 2.0.0",
            "version": "2.0.0",
            "owner": "acme",
            "status": "pending",
            "submitted_by": "submitter",
            "created_at": NOW.isoformat(),
            "bundle_id": str(bundle_id),
            "mcp_validated": True,
            "validation_results": [{"stage": "manifest", "passed": True, "details": "ok", "run_at": NOW.isoformat()}],
            "bundle_name": "review bundle",
            "forked_from": {"available": True, "id": uuid.UUID(int=90)},
        }
    ]
    assert "ORDER BY mcp_versions.released_at DESC" in _sql(statements[0])
    assert active_id not in _bound_values(statements[1])
    assert hidden_id in _bound_values(statements[1])


@pytest.mark.asyncio
async def test_pending_component_queue_empty_team_filter_and_database_failure():
    db = _db()
    db.execute.return_value = _result()
    assert await review._query_pending_components(db, ADMIN_SCOPE, "skill") == []
    assert db.execute.await_count == 1

    active_only = _pending_version(uuid.UUID(int=36), active=True)
    db.execute.reset_mock()
    db.execute.return_value = _result(scalars=[active_only])
    assert await review._query_pending_components(db, ADMIN_SCOPE, "skill") == []
    assert db.execute.await_count == 1

    listing_id = uuid.UUID(int=35)
    version = _pending_version(listing_id)
    listing = SimpleNamespace(
        id=listing_id,
        name="other team",
        owner="other",
        submitted_by=SUBMITTER_ID,
        created_at=NOW,
        bundle_id=None,
        is_private=True,
        team_id=OTHER_TEAM_ID,
    )
    db.execute.reset_mock()
    db.execute.side_effect = [_result(scalars=[version]), _result(scalars=[listing])]
    assert await review._query_pending_components(db, ADMIN_SCOPE, "skill", TEAM_ID) == []

    db.execute.reset_mock(side_effect=True)
    db.execute.side_effect = RuntimeError("component query failed")
    with pytest.raises(RuntimeError, match="component query failed"):
        await review._query_pending_components(db, ADMIN_SCOPE, "hook")


@pytest.mark.asyncio
async def test_pending_agent_queue_groups_newest_hides_locks_and_resolves_authors(monkeypatch):
    db = _db()
    agent_id = uuid.UUID(int=40)
    hidden_id = uuid.UUID(int=41)
    active_id = uuid.UUID(int=42)
    component = _component_ref("mcp", uuid.UUID(int=43))
    newest = _pending_version(agent_id, version="3.0.0", components=[component], created_at=NOW)
    older = _pending_version(agent_id, version="2.0.0", created_at=NOW - timedelta(days=1))
    hidden_version = _pending_version(hidden_id)
    active_version = _pending_version(active_id, active=True)
    agent = SimpleNamespace(
        id=agent_id,
        name="visible agent",
        description="fallback description",
        owner="acme",
        created_by=uuid.UUID(int=44),
        is_private=False,
        team_id=None,
    )
    hidden = SimpleNamespace(
        id=hidden_id,
        name="secret agent",
        description="secret",
        owner="secret",
        created_by=uuid.UUID(int=45),
        is_private=True,
        team_id=OTHER_TEAM_ID,
    )
    statements = []

    async def execute(statement):
        statements.append(statement)
        sql = _sql(statement)
        if "FROM agent_versions" in sql:
            return _result(scalars=[newest, older, active_version, hidden_version])
        if "FROM agents" in sql:
            return _result(scalars=[agent, hidden])
        if "FROM users" in sql:
            return _result(
                rows=[
                    (agent.created_by, "creator"),
                    (newest.released_by, "releaser"),
                ]
            )
        raise AssertionError(sql)

    db.execute.side_effect = execute
    readiness = AsyncMock(return_value=(False, [{"name": "waiting"}]))
    monkeypatch.setattr(review, "_check_agent_components_ready", readiness)

    items = await review._query_pending_agents(db, GLOBAL_SCOPE)

    assert items == [
        {
            "type": "agent",
            "id": str(agent_id),
            "name": "visible agent",
            "description": "Description 3.0.0",
            "version": "3.0.0",
            "owner": "acme",
            "status": "pending",
            "submitted_by": "releaser",
            "created_at": NOW.isoformat(),
            "prompt": "Review prompt",
            "component_count": 1,
            "components_ready": False,
            "blocking_components": [{"name": "waiting"}],
            "gaming_flags": {"score": 1},
            "forked_from": None,
        }
    ]
    readiness.assert_awaited_once_with([component], db)
    assert "ORDER BY agent_versions.created_at DESC" in _sql(statements[0])
    assert active_id not in _bound_values(statements[1])


@pytest.mark.asyncio
async def test_pending_agent_queue_empty_and_database_failure():
    db = _db()
    db.execute.return_value = _result()
    assert await review._query_pending_agents(db, ADMIN_SCOPE) == []
    assert db.execute.await_count == 1

    db.execute.side_effect = RuntimeError("agent query failed")
    with pytest.raises(RuntimeError, match="agent query failed"):
        await review._query_pending_agents(db, ADMIN_SCOPE)


@pytest.mark.asyncio
async def test_list_pending_tabs_default_order_and_team_denial(monkeypatch):
    db = _db()
    actor = _actor()
    scope = AsyncMock(return_value=ADMIN_SCOPE)
    agents = AsyncMock(return_value=[{"name": "agent", "created_at": "2026-03-01T10:00:00+00:00"}])
    components = AsyncMock(return_value=[{"name": "component", "created_at": "2026-03-01T11:00:00+00:00"}])
    monkeypatch.setattr(review, "_require_review_scope", scope)
    monkeypatch.setattr(review, "_query_pending_agents", agents)
    monkeypatch.setattr(review, "_query_pending_components", components)

    assert await review.list_pending(type="skill", tab="agents", team_id=None, db=db, current_user=actor) == [
        {"name": "agent", "created_at": "2026-03-01T10:00:00+00:00"}
    ]
    agents.assert_awaited_once_with(db, ADMIN_SCOPE, None, actor)
    components.assert_not_awaited()

    agents.reset_mock()
    assert await review.list_pending(type="skill", tab="components", team_id=TEAM_ID, db=db, current_user=actor) == [
        {"name": "component", "created_at": "2026-03-01T11:00:00+00:00"}
    ]
    components.assert_awaited_once_with(db, ADMIN_SCOPE, "skill", TEAM_ID, actor)
    agents.assert_not_awaited()

    components.reset_mock()
    result = await review.list_pending(type=None, tab="unexpected", team_id=None, db=db, current_user=actor)
    assert [item["name"] for item in result] == ["component", "agent"]
    agents.assert_awaited_once_with(db, ADMIN_SCOPE, None, actor)
    components.assert_awaited_once_with(db, ADMIN_SCOPE, None, None, actor)

    scope.return_value = TEAM_SCOPE
    agents.reset_mock()
    components.reset_mock()
    with pytest.raises(HTTPException) as exc:
        await review.list_pending(type=None, tab=None, team_id=OTHER_TEAM_ID, db=db, current_user=actor)
    _assert_http(exc, 403, "You do not review for this teamspace")
    agents.assert_not_awaited()
    components.assert_not_awaited()


class _DemoEnum(str, enum.Enum):
    ready = "ready"


def test_safe_serialization_covers_uuid_datetime_enum_and_plain_values():
    value_id = uuid.UUID(int=50)
    assert review._safe_serialize(value_id) == str(value_id)
    assert review._safe_serialize(NOW) == NOW.isoformat()
    assert review._safe_serialize(_DemoEnum.ready) == "ready"
    marker = {"plain": True}
    assert review._safe_serialize(marker) is marker


@pytest.mark.parametrize("listing_type", list(review.LISTING_MODELS))
def test_listing_detail_uses_pending_version_and_serializes_every_declared_field(listing_type):
    listing_id = uuid.UUID(int=60)
    pending = SimpleNamespace(status=ListingStatus.pending, description="pending description", version="2.0.0")
    approved = SimpleNamespace(status=ListingStatus.approved, description="old description", version="1.0.0")
    listing = SimpleNamespace(
        id=listing_id,
        name="detail listing",
        owner="acme",
        status=ListingStatus.approved,
        submitted_by=SUBMITTER_ID,
        created_at=NOW,
        updated_at=NOW + timedelta(hours=1),
        versions=[approved, pending],
    )
    for index, field in enumerate(review._DETAIL_FIELDS[listing_type]):
        setattr(pending, field, uuid.UUID(int=61) if index == 0 else f"pending {field}")
        setattr(listing, field, f"listing {field}")
    fallback_field = review._DETAIL_FIELDS[listing_type][-1]
    setattr(pending, fallback_field, None)
    if fallback_field == "bundle_id":
        setattr(listing, fallback_field, uuid.UUID(int=62))
    if listing_type == "mcp":
        listing.validation_results = [SimpleNamespace(stage="runtime", passed=False, details="failed", run_at=None)]
        listing.mcp_validated = False

    result = review._serialize_listing_detail(listing_type, listing)

    assert result["type"] == listing_type
    assert result["status"] == "pending"
    assert result["description"] == "pending description"
    assert result["version"] == "2.0.0"
    assert result[review._DETAIL_FIELDS[listing_type][0]] == str(uuid.UUID(int=61))
    expected_fallback = str(uuid.UUID(int=62)) if fallback_field == "bundle_id" else f"listing {fallback_field}"
    assert result[fallback_field] == expected_fallback
    assert set(review._DETAIL_FIELDS[listing_type]) <= set(result)
    if listing_type == "mcp":
        assert result["validation_results"] == [
            {"stage": "runtime", "passed": False, "details": "failed", "run_at": None}
        ]


def test_listing_detail_falls_back_to_listing_without_versions():
    listing = SimpleNamespace(
        id=uuid.UUID(int=63),
        name="legacy",
        description=None,
        version=None,
        owner=None,
        status=ListingStatus.rejected,
        submitted_by=SUBMITTER_ID,
        created_at=NOW,
        updated_at=None,
    )
    result = review._serialize_listing_detail("unknown", listing)
    assert result == {
        "type": "unknown",
        "id": str(listing.id),
        "name": "legacy",
        "description": "",
        "version": "",
        "owner": "",
        "status": "rejected",
        "submitted_by": str(SUBMITTER_ID),
        "created_at": NOW.isoformat(),
        "updated_at": None,
    }


@pytest.mark.asyncio
async def test_get_review_component_authorizes_serializes_and_resolves_submitter(monkeypatch):
    db = _db()
    actor = _actor()
    listing = SimpleNamespace(
        id=uuid.UUID(int=70),
        name="component detail",
        description="detail",
        version="1.0.0",
        owner="acme",
        status=ListingStatus.pending,
        submitted_by=SUBMITTER_ID,
        created_at=NOW,
        updated_at=NOW,
        versions=[],
        is_private=False,
        team_id=None,
        validation_results=[],
        mcp_validated=False,
    )
    monkeypatch.setattr(review, "_require_review_scope", AsyncMock(return_value=GLOBAL_SCOPE))
    monkeypatch.setattr(review, "_find_listing", AsyncMock(return_value=("mcp", listing)))
    provenance = AsyncMock(return_value={"available": False})
    monkeypatch.setattr(review, "provenance_for", provenance)
    db.execute.return_value = _result(
        scalar=SimpleNamespace(id=SUBMITTER_ID, username="component-author", email="author@example.test")
    )

    result = await review.get_review(str(listing.id), db, actor)

    assert result["name"] == "component detail"
    assert result["forked_from"] == {"available": False}
    provenance.assert_awaited_once_with(listing, actor, db)
    assert result["submitted_by"] == "component-author"
    statement = db.execute.await_args.args[0]
    assert "FROM users" in _sql(statement)
    assert set(_params(statement).values()) == {SUBMITTER_ID}

    listing.is_private = True
    listing.team_id = OTHER_TEAM_ID
    db.execute.reset_mock()
    with pytest.raises(HTTPException) as hidden:
        await review.get_review(str(listing.id), db, actor)
    _assert_http(hidden, 404, "Listing not found")
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_review_agent_serializes_pending_release_and_expands_components(monkeypatch):
    db = _db()
    actor = _actor()
    agent_id = uuid.UUID(int=71)
    mcp_id = uuid.UUID(int=72)
    prompt_id = uuid.UUID(int=73)
    missing_id = uuid.UUID(int=74)
    components = [
        _component_ref("mcp", mcp_id, "stale mcp"),
        _component_ref("prompt", prompt_id),
        _component_ref("unknown", uuid.UUID(int=75), "inline unknown"),
        _component_ref("skill", missing_id, "missing skill"),
    ]
    pending = _pending_version(agent_id, version="2.0.0", components=components)
    agent = SimpleNamespace(
        id=agent_id,
        name="agent detail",
        description="agent fallback",
        version="1.0.0",
        owner="acme",
        status=AgentStatus.approved,
        created_by=uuid.UUID(int=76),
        created_at=NOW,
        updated_at=NOW,
        git_url=None,
        versions=[pending],
        latest_version=SimpleNamespace(version="1.0.0", status=AgentStatus.approved),
        components=[],
        is_private=False,
        team_id=None,
    )
    mcp_listing = SimpleNamespace(name="resolved mcp", description="mcp description")
    prompt_listing = SimpleNamespace(name="resolved prompt", template="Do review", category="general")
    author = SimpleNamespace(id=SUBMITTER_ID, username=None, email="release-author@example.test")
    monkeypatch.setattr(review, "_require_review_scope", AsyncMock(return_value=GLOBAL_SCOPE))
    monkeypatch.setattr(review, "_find_listing", AsyncMock(return_value=(None, None)))
    readiness = AsyncMock(return_value=(False, [{"name": "blocked"}]))
    monkeypatch.setattr(review, "_check_agent_components_ready", readiness)
    db.execute.side_effect = [
        _result(scalar=agent),
        _result(scalar=mcp_listing),
        _result(scalar=prompt_listing),
        _result(),
        _result(scalar=author),
    ]

    result = await review.get_review(str(agent_id), db, actor)

    assert result == {
        "type": "agent",
        "id": str(agent_id),
        "name": "agent detail",
        "description": "Description 2.0.0",
        "version": "2.0.0",
        "owner": "acme",
        "status": "pending",
        "submitted_by": "release-author@example.test",
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        "git_url": None,
        "prompt": "Review prompt",
        "model_name": "review-model",
        "model_config_json": {"temperature": 0},
        "external_mcps": [],
        "supported_harnesses": ["pi"],
        "required_capabilities": ["mcp_servers"],
        "rejection_reason": None,
        "component_count": 4,
        "components_ready": False,
        "component_blockers": [{"name": "blocked"}],
        "gaming_flags": {"score": 1},
        "success_criteria": {"purpose": "review"},
        "forked_from": None,
        "components": [
            {
                "component_type": "mcp",
                "component_id": str(mcp_id),
                "name": "resolved mcp",
                "description": "mcp description",
            },
            {
                "component_type": "prompt",
                "component_id": str(prompt_id),
                "name": "resolved prompt",
                "template": "Do review",
                "category": "general",
            },
            {
                "component_type": "unknown",
                "component_id": str(uuid.UUID(int=75)),
                "name": "inline unknown",
            },
            {"component_type": "skill", "component_id": str(missing_id), "name": "missing skill"},
        ],
    }
    readiness.assert_awaited_once_with(components, db)
    assert db.execute.await_count == 5


@pytest.mark.asyncio
async def test_get_review_agent_missing_hidden_and_malformed_contracts(monkeypatch):
    db = _db()
    actor = _actor()
    monkeypatch.setattr(review, "_require_review_scope", AsyncMock(return_value=GLOBAL_SCOPE))
    monkeypatch.setattr(review, "_find_listing", AsyncMock(return_value=(None, None)))

    with pytest.raises(HTTPException) as malformed:
        await review.get_review("not-a-uuid", db, actor)
    _assert_http(malformed, 404, "Listing not found")
    db.execute.assert_not_awaited()

    db.execute.return_value = _result()
    with pytest.raises(HTTPException) as missing:
        await review.get_review(str(uuid.UUID(int=77)), db, actor)
    _assert_http(missing, 404, "Listing not found")

    hidden_agent = SimpleNamespace(is_private=True, team_id=OTHER_TEAM_ID)
    db.execute.return_value = _result(scalar=hidden_agent)
    with pytest.raises(HTTPException) as hidden:
        await review.get_review(str(uuid.UUID(int=78)), db, actor)
    _assert_http(hidden, 404, "Listing not found")


@pytest.mark.asyncio
async def test_get_review_keeps_unparseable_submitter_without_extra_failure(monkeypatch):
    db = _db()
    listing = SimpleNamespace(
        id=uuid.UUID(int=79),
        name="legacy submitter",
        description="",
        version="",
        owner="",
        status=ListingStatus.pending,
        submitted_by="legacy-user",
        created_at=NOW,
        updated_at=None,
        versions=[],
        is_private=False,
        team_id=None,
    )
    monkeypatch.setattr(review, "_require_review_scope", AsyncMock(return_value=GLOBAL_SCOPE))
    monkeypatch.setattr(review, "_find_listing", AsyncMock(return_value=("skill", listing)))

    result = await review.get_review(str(listing.id), db, _actor())

    assert result["submitted_by"] == "legacy-user"
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("listing_type", list(review.LISTING_MODELS))
async def test_approve_each_component_type_updates_version_then_notifies_and_commits(
    monkeypatch,
    decision_boundaries,
    listing_type,
):
    db = _db()
    actor = _actor(user_id=SUBMITTER_ID)
    listing, version = _orm_listing(listing_type, team_id=TEAM_ID)
    if listing_type == "skill":
        monkeypatch.setattr(review, "lock_skill_version", AsyncMock(return_value=(version.id, version)))
        # Real-DB tests cover release ordering; this fixture verifies the
        # notification and commit sequence without mocking SQL query results.
        monkeypatch.setattr(review, "should_promote_skill_version", AsyncMock(return_value=True))
    events = []
    decision_boundaries.scope.return_value = PUBLIC_TEAM_SCOPE
    monkeypatch.setattr(review, "_find_listing", AsyncMock(return_value=(listing_type, listing)))
    db.flush.side_effect = lambda: events.append("flush")

    def record_execute(statement):
        if listing_type == "skill" and _pending_skill_ids(statement):
            return _result(scalars=[version.id])
        events.append("update")
        return _result()

    db.execute.side_effect = record_execute
    decision_boundaries.decide.side_effect = lambda *args, **kwargs: events.append("inbox")
    db.commit.side_effect = lambda: events.append("commit")
    db.refresh.side_effect = lambda row, **kwargs: events.append("auth" if kwargs else "refresh")
    decision_boundaries.invalidate.side_effect = lambda namespace: events.append("cache")
    decision_boundaries.create_task.side_effect = lambda awaitable: events.append("publish")

    result = await review.approve(str(listing.id), db, actor)

    assert result == {
        "type": listing_type,
        "id": str(listing.id),
        "name": listing.name,
        "status": "approved",
    }
    assert version.status is ListingStatus.approved
    assert version.rejection_reason is None
    assert version.reviewed_by == actor.id
    assert version.reviewed_at == NOW
    assert events == (["auth"] if listing_type == "skill" else []) + [
        "flush",
        "update",
        "inbox",
        "commit",
        "refresh",
        "cache",
        "publish",
    ]
    statement = db.execute.await_args.args[0]
    assert f"UPDATE {review.LISTING_MODELS[listing_type].__tablename__}" in _sql(statement)
    assert _params(statement) == {
        "updated_at": None,
        "latest_version_id": version.id,
        "id_1": listing.id,
    }
    decision_boundaries.decide.assert_awaited_once_with(
        db,
        listing,
        subject_type=listing_type,
        approved=True,
        actor_id=actor.id,
        version="2.0.0",
        submitter_id=SUBMITTER_ID,
    )
    decision_boundaries.invalidate.assert_awaited_once_with("dashboard")
    decision_boundaries.publish.assert_called_once_with(
        "reviews:updated",
        {"listing_id": str(listing.id), "action": "approved"},
    )
    decision_boundaries.create_task.assert_called_once_with("publish-awaitable")


@pytest.mark.asyncio
async def test_approve_legacy_listing_and_active_lock_contracts(monkeypatch, decision_boundaries):
    db = _db()
    listing, version = _orm_listing("mcp")
    listing.versions = []
    monkeypatch.setattr(review, "_find_listing", AsyncMock(return_value=("mcp", listing)))
    db.execute.return_value = _result()

    result = await review.approve(str(listing.id), db, _actor())

    assert result["status"] == "approved"
    assert listing.status is ListingStatus.approved
    assert listing.rejection_reason is None
    db.flush.assert_not_awaited()

    locked, locked_version = _orm_listing("mcp", index=1)
    locked_version.is_editing = True
    locked_version.editing_by = uuid.UUID(int=900)
    locked_version.editing_since = datetime.now(UTC)
    monkeypatch.setattr(review, "_find_listing", AsyncMock(return_value=("mcp", locked)))
    decision_boundaries.decide.reset_mock()
    db.commit.reset_mock()
    with pytest.raises(HTTPException) as pending_lock:
        await review.approve(str(locked.id), db, _actor())
    _assert_http(pending_lock, 409, "Cannot approve: the owner is currently editing this item")
    assert locked_version.status is ListingStatus.pending
    decision_boundaries.decide.assert_not_awaited()
    db.commit.assert_not_awaited()

    locked.versions = []
    with pytest.raises(HTTPException) as legacy_lock:
        await review.approve(str(locked.id), db, _actor())
    _assert_http(legacy_lock, 409, "Cannot approve: the owner is currently editing this item")


@pytest.mark.asyncio
async def test_approve_not_found_and_authorization_fail_before_mutation(monkeypatch, decision_boundaries):
    db = _db()
    monkeypatch.setattr(review, "_find_listing", AsyncMock(return_value=(None, None)))
    with pytest.raises(HTTPException) as missing:
        await review.approve("missing", db, _actor())
    _assert_http(missing, 404, "Listing not found")

    listing, version = _orm_listing("skill")
    decision_boundaries.scope.return_value = TEAM_SCOPE
    monkeypatch.setattr(review, "_find_listing", AsyncMock(return_value=("skill", listing)))
    with pytest.raises(HTTPException) as forbidden:
        await review.approve(str(listing.id), db, _actor(UserRole.user))
    _assert_http(
        forbidden,
        403,
        "Public item is outside your review scope",
    )
    assert version.status is ListingStatus.pending
    db.flush.assert_not_awaited()
    decision_boundaries.decide.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["flush", "inbox", "commit", "cache"])
async def test_approve_boundary_failures_stop_later_side_effects(
    monkeypatch,
    decision_boundaries,
    boundary,
):
    db = _db()
    listing, _version = _orm_listing("hook")
    monkeypatch.setattr(review, "_find_listing", AsyncMock(return_value=("hook", listing)))
    if boundary == "flush":
        db.flush.side_effect = RuntimeError("flush failed")
    elif boundary == "inbox":
        decision_boundaries.decide.side_effect = RuntimeError("inbox failed")
    elif boundary == "commit":
        db.commit.side_effect = RuntimeError("commit failed")
    else:
        decision_boundaries.invalidate.side_effect = RuntimeError("cache failed")

    with pytest.raises(RuntimeError, match=f"{boundary} failed"):
        await review.approve(str(listing.id), db, _actor())

    if boundary == "flush":
        db.execute.assert_not_awaited()
        decision_boundaries.decide.assert_not_awaited()
    if boundary in {"flush", "inbox"}:
        db.commit.assert_not_awaited()
    if boundary in {"flush", "inbox", "commit"}:
        db.refresh.assert_not_awaited()
        decision_boundaries.invalidate.assert_not_awaited()
    decision_boundaries.create_task.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("listing_type", list(review.LISTING_MODELS))
async def test_reject_each_component_type_records_reason_notifies_and_cascades(
    monkeypatch,
    decision_boundaries,
    listing_type,
):
    db = _db()
    actor = _actor(user_id=SUBMITTER_ID)
    listing, version = _orm_listing(listing_type, team_id=TEAM_ID)
    if listing_type == "skill":
        monkeypatch.setattr(review, "lock_skill_version", AsyncMock(return_value=(version.id, version)))
    events = []
    decision_boundaries.scope.return_value = PUBLIC_TEAM_SCOPE
    monkeypatch.setattr(review, "_find_listing", AsyncMock(return_value=(listing_type, listing)))
    decision_boundaries.decide.side_effect = lambda *args, **kwargs: events.append("inbox")
    db.commit.side_effect = lambda: events.append("commit")
    cascade = AsyncMock(side_effect=lambda *args: events.append("cascade"))
    monkeypatch.setattr("services.insights.self_learn.handle_component_rejection", cascade)
    db.refresh.side_effect = lambda row, **kwargs: events.append("auth" if kwargs else "refresh")
    decision_boundaries.invalidate.side_effect = lambda namespace: events.append("cache")
    decision_boundaries.create_task.side_effect = lambda awaitable: events.append("publish")
    request = ReviewActionRequest(reason="needs changes")
    if listing_type == "skill":
        db.execute.side_effect = lambda statement: (
            _result(scalars=[version.id]) if _pending_skill_ids(statement) else _result()
        )

    result = await review.reject(str(listing.id), request, db, actor)

    assert result == {
        "type": listing_type,
        "id": str(listing.id),
        "name": listing.name,
        "status": "rejected",
    }
    assert version.status is ListingStatus.rejected
    assert version.rejection_reason == "needs changes"
    assert version.reviewed_by == actor.id
    assert version.reviewed_at == NOW
    assert events == (["auth"] if listing_type == "skill" else []) + [
        "inbox",
        "commit",
        "cascade",
        "commit",
        "refresh",
        "cache",
        "publish",
    ]
    decision_boundaries.decide.assert_awaited_once_with(
        db,
        listing,
        subject_type=listing_type,
        approved=False,
        actor_id=actor.id,
        version="2.0.0",
        reason="needs changes",
        submitter_id=SUBMITTER_ID,
    )
    cascade.assert_awaited_once_with(listing_type, listing.id, db)
    assert db.commit.await_count == 2


@pytest.mark.asyncio
async def test_reject_legacy_lock_not_found_and_cascade_failure_contracts(monkeypatch, decision_boundaries):
    db = _db()
    listing, version = _orm_listing("sandbox")
    listing.versions = []
    monkeypatch.setattr(review, "_find_listing", AsyncMock(return_value=("sandbox", listing)))
    cascade = AsyncMock(side_effect=RuntimeError("cascade unavailable"))
    monkeypatch.setattr("services.insights.self_learn.handle_component_rejection", cascade)

    result = await review.reject(
        str(listing.id),
        ReviewActionRequest(reason=None),
        db,
        _actor(),
    )
    assert result["status"] == "rejected"
    assert listing.rejection_reason is None
    assert db.commit.await_count == 1

    locked, locked_version = _orm_listing("sandbox", index=1)
    locked_version.is_editing = True
    locked_version.editing_by = uuid.UUID(int=901)
    locked_version.editing_since = datetime.now(UTC)
    monkeypatch.setattr(review, "_find_listing", AsyncMock(return_value=("sandbox", locked)))
    decision_boundaries.decide.reset_mock()
    db.commit.reset_mock()
    with pytest.raises(HTTPException) as pending_lock:
        await review.reject(str(locked.id), ReviewActionRequest(reason="no"), db, _actor())
    _assert_http(pending_lock, 409, "Cannot reject: the owner is currently editing this item")
    assert locked_version.status is ListingStatus.pending
    db.commit.assert_not_awaited()

    locked.versions = []
    with pytest.raises(HTTPException) as legacy_lock:
        await review.reject(str(locked.id), ReviewActionRequest(reason="no"), db, _actor())
    _assert_http(legacy_lock, 409, "Cannot reject: the owner is currently editing this item")

    monkeypatch.setattr(review, "_find_listing", AsyncMock(return_value=(None, None)))
    with pytest.raises(HTTPException) as missing:
        await review.reject("missing", ReviewActionRequest(reason="no"), db, _actor())
    _assert_http(missing, 404, "Listing not found")


@pytest.mark.asyncio
async def test_reject_inbox_and_commit_failures_do_not_run_later_services(monkeypatch, decision_boundaries):
    db = _db()
    listing, _version = _orm_listing("prompt")
    monkeypatch.setattr(review, "_find_listing", AsyncMock(return_value=("prompt", listing)))
    decision_boundaries.decide.side_effect = RuntimeError("inbox failed")

    with pytest.raises(RuntimeError, match="inbox failed"):
        await review.reject(str(listing.id), ReviewActionRequest(reason="bad"), db, _actor())
    db.commit.assert_not_awaited()
    db.refresh.assert_not_awaited()

    decision_boundaries.decide.side_effect = None
    db.commit.side_effect = RuntimeError("commit failed")
    with pytest.raises(RuntimeError, match="commit failed"):
        await review.reject(str(listing.id), ReviewActionRequest(reason="bad"), db, _actor())
    db.refresh.assert_not_awaited()
    decision_boundaries.invalidate.assert_not_awaited()


@pytest.mark.asyncio
async def test_approve_agent_newest_release_supersedes_older_and_notifies_each_author(
    monkeypatch,
    decision_boundaries,
):
    db = _db()
    actor = _actor(user_id=SUBMITTER_ID)
    agent, current = _orm_agent(team_id=TEAM_ID)
    decision_boundaries.scope.return_value = PUBLIC_TEAM_SCOPE
    newest = _pending_version(
        agent.id,
        version="2.0.0",
        status=AgentStatus.pending,
        released_by=uuid.UUID(int=510),
    )
    older = _pending_version(
        agent.id,
        version="1.5.0",
        status=AgentStatus.pending,
        released_by=uuid.UUID(int=511),
    )
    db.execute.side_effect = [_result(scalar=agent), _result(scalars=[newest, older])]
    readiness = AsyncMock(return_value=(True, []))
    monkeypatch.setattr(review, "_check_agent_components_ready", readiness)
    events = []
    db.flush.side_effect = lambda: events.append("flush")
    decision_boundaries.decide.side_effect = lambda *args, **kwargs: events.append("inbox")
    db.commit.side_effect = lambda: events.append("commit")
    decision_boundaries.invalidate.side_effect = lambda namespace: events.append("cache")
    decision_boundaries.create_task.side_effect = lambda awaitable: events.append("publish")

    result = await review.approve_agent(
        agent.id,
        review.AgentApproveRequest(category="testing"),
        db,
        actor,
    )

    assert result == {"id": str(agent.id), "name": agent.name, "status": "approved", "version": "2.0.0"}
    assert newest.status is AgentStatus.approved
    assert newest.rejection_reason is None
    assert older.status is AgentStatus.rejected
    assert older.rejection_reason == "Superseded by newer version"
    assert newest.reviewed_by == older.reviewed_by == actor.id
    assert newest.reviewed_at == older.reviewed_at == NOW
    assert agent.latest_version_id == newest.id
    assert agent.category == "testing"
    assert events == ["flush", "inbox", "inbox", "commit", "cache", "publish"]
    readiness.assert_awaited_once_with(newest.components, db, require_public_skills=True)
    assert decision_boundaries.decide.await_args_list == [
        call(
            db,
            agent,
            subject_type="agent",
            approved=True,
            actor_id=actor.id,
            version="2.0.0",
            submitter_id=newest.released_by,
        ),
        call(
            db,
            agent,
            subject_type="agent",
            approved=False,
            actor_id=actor.id,
            version="1.5.0",
            reason="Superseded by newer version",
            submitter_id=older.released_by,
        ),
    ]
    assert current.status is AgentStatus.approved


@pytest.mark.asyncio
async def test_approve_agent_not_found_no_pending_lock_and_readiness_fail_without_commit(
    monkeypatch,
    decision_boundaries,
):
    db = _db()
    actor = _actor()
    db.execute.return_value = _result()
    with pytest.raises(HTTPException) as missing:
        await review.approve_agent(uuid.UUID(int=520), None, db, actor)
    _assert_http(missing, 404, "Agent not found")

    agent, _current = _orm_agent()
    db.execute.side_effect = [_result(scalar=agent), _result()]
    with pytest.raises(HTTPException) as no_pending:
        await review.approve_agent(agent.id, None, db, actor)
    _assert_http(no_pending, 400, "Agent has no pending versions (latest is 'approved')")

    locked = _pending_version(agent.id, status=AgentStatus.pending, active=True)
    db.execute.side_effect = [_result(scalar=agent), _result(scalars=[locked])]
    with pytest.raises(HTTPException) as lock:
        await review.approve_agent(agent.id, None, db, actor)
    _assert_http(lock, 409, "Cannot approve: the owner is currently editing this agent")
    assert locked.status is AgentStatus.pending

    pending = _pending_version(agent.id, status=AgentStatus.pending)
    blocker = [{"component_type": "mcp", "status": "pending"}]
    db.execute.side_effect = [_result(scalar=agent), _result(scalars=[pending])]
    readiness = AsyncMock(return_value=(False, blocker))
    monkeypatch.setattr(review, "_check_agent_components_ready", readiness)
    with pytest.raises(HTTPException) as blocked:
        await review.approve_agent(agent.id, None, db, actor)
    _assert_http(
        blocked,
        422,
        {
            "message": "Cannot approve: some components are not approved yet",
            "blocking_components": blocker,
        },
    )
    assert pending.status is AgentStatus.pending
    db.flush.assert_not_awaited()
    decision_boundaries.decide.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_approve_agent_inbox_failure_stops_commit(monkeypatch, decision_boundaries):
    db = _db()
    agent, _current = _orm_agent()
    pending = _pending_version(agent.id, status=AgentStatus.pending)
    db.execute.side_effect = [_result(scalar=agent), _result(scalars=[pending])]
    monkeypatch.setattr(review, "_check_agent_components_ready", AsyncMock(return_value=(True, [])))
    decision_boundaries.decide.side_effect = RuntimeError("agent inbox failed")

    with pytest.raises(RuntimeError, match="agent inbox failed"):
        await review.approve_agent(agent.id, None, db, _actor())

    db.flush.assert_awaited_once()
    db.commit.assert_not_awaited()
    decision_boundaries.invalidate.assert_not_awaited()


@pytest.mark.asyncio
async def test_reject_agent_rejects_every_pending_version_and_notifies_each_author(
    monkeypatch,
    decision_boundaries,
):
    db = _db()
    actor = _actor(user_id=SUBMITTER_ID)
    agent, _current = _orm_agent(team_id=TEAM_ID)
    decision_boundaries.scope.return_value = PUBLIC_TEAM_SCOPE
    newest = _pending_version(
        agent.id,
        version="2.0.0",
        status=AgentStatus.pending,
        released_by=uuid.UUID(int=530),
    )
    older = _pending_version(
        agent.id,
        version="1.5.0",
        status=AgentStatus.pending,
        released_by=uuid.UUID(int=531),
    )
    db.execute.side_effect = [_result(scalar=agent), _result(scalars=[newest, older])]
    events = []
    db.flush.side_effect = lambda: events.append("flush")
    decision_boundaries.decide.side_effect = lambda *args, **kwargs: events.append("inbox")
    db.commit.side_effect = lambda: events.append("commit")
    decision_boundaries.invalidate.side_effect = lambda namespace: events.append("cache")
    decision_boundaries.create_task.side_effect = lambda awaitable: events.append("publish")

    result = await review.reject_agent(
        agent.id,
        review.AgentRejectRequest(reason="policy"),
        db,
        actor,
    )

    assert result == {"id": str(agent.id), "name": agent.name, "status": "rejected", "version": "2.0.0"}
    assert events == ["flush", "inbox", "inbox", "commit", "cache", "publish"]
    for version in (newest, older):
        assert version.status is AgentStatus.rejected
        assert version.rejection_reason == "policy"
        assert version.reviewed_by == actor.id
        assert version.reviewed_at == NOW
    assert decision_boundaries.decide.await_args_list == [
        call(
            db,
            agent,
            subject_type="agent",
            approved=False,
            actor_id=actor.id,
            version="2.0.0",
            reason="policy",
            submitter_id=newest.released_by,
        ),
        call(
            db,
            agent,
            subject_type="agent",
            approved=False,
            actor_id=actor.id,
            version="1.5.0",
            reason="policy",
            submitter_id=older.released_by,
        ),
    ]


@pytest.mark.asyncio
async def test_reject_agent_not_found_no_pending_and_lock_contracts(decision_boundaries):
    db = _db()
    actor = _actor()
    db.execute.return_value = _result()
    with pytest.raises(HTTPException) as missing:
        await review.reject_agent(uuid.UUID(int=540), review.AgentRejectRequest(reason="no"), db, actor)
    _assert_http(missing, 404, "Agent not found")

    agent, _current = _orm_agent()
    db.execute.side_effect = [_result(scalar=agent), _result()]
    with pytest.raises(HTTPException) as no_pending:
        await review.reject_agent(agent.id, review.AgentRejectRequest(reason="no"), db, actor)
    _assert_http(no_pending, 400, "Agent has no pending versions (latest is 'approved')")

    locked = _pending_version(agent.id, status=AgentStatus.pending, active=True)
    db.execute.side_effect = [_result(scalar=agent), _result(scalars=[locked])]
    with pytest.raises(HTTPException) as lock:
        await review.reject_agent(agent.id, review.AgentRejectRequest(reason="no"), db, actor)
    _assert_http(lock, 409, "Cannot reject: the owner is currently editing this agent")
    assert locked.status is AgentStatus.pending
    db.flush.assert_not_awaited()
    decision_boundaries.decide.assert_not_awaited()
    db.commit.assert_not_awaited()


def test_listing_type_lookup_is_exact_with_documented_default():
    for listing_type in review.LISTING_MODELS:
        listing, _version = _orm_listing(listing_type)
        assert review._listing_type_of(listing) == listing_type
    assert review._listing_type_of(SimpleNamespace()) == "mcp"


@pytest.mark.asyncio
async def test_bundle_loader_queries_every_type_and_authorizes_before_return(monkeypatch):
    db = _db()
    bundle_id = uuid.UUID(int=600)
    listings = [
        _orm_listing(listing_type, bundle_id=bundle_id, index=index)[0]
        for index, listing_type in enumerate(review.LISTING_MODELS)
    ]
    db.execute.side_effect = [_result(scalars=[listing]) for listing in listings]
    authorize = MagicMock()
    monkeypatch.setattr(review, "_authorize_item", authorize)

    assert await review._bundle_listings(bundle_id, db, ADMIN_SCOPE) == listings
    assert db.execute.await_count == len(review.LISTING_MODELS)
    assert authorize.call_args_list == [call(listing, ADMIN_SCOPE) for listing in listings]
    for awaited, model in zip(db.execute.await_args_list, review.LISTING_MODELS.values(), strict=True):
        statement = awaited.args[0]
        assert f"FROM {model.__tablename__}" in _sql(statement)
        assert set(_params(statement).values()) == {bundle_id}


@pytest.mark.asyncio
async def test_bundle_loader_scope_failure_stops_before_any_endpoint_mutation():
    db = _db()
    bundle_id = uuid.UUID(int=610)
    public, public_version = _orm_listing("mcp", bundle_id=bundle_id)
    private, private_version = _orm_listing(
        "skill",
        bundle_id=bundle_id,
        is_private=True,
        team_id=OTHER_TEAM_ID,
        index=1,
    )
    db.execute.side_effect = [_result(scalars=[public]), _result(scalars=[private])]

    with pytest.raises(HTTPException) as exc:
        await review._bundle_listings(bundle_id, db, GLOBAL_SCOPE)

    _assert_http(exc, 404, "Submission not found")
    assert public_version.status is ListingStatus.pending
    assert private_version.status is ListingStatus.pending


@pytest.mark.asyncio
async def test_bundle_skill_listing_order_is_canonical():
    db = _db()
    first, _ = _orm_listing("skill", index=1)
    second, _ = _orm_listing("skill", index=2)
    db.execute.side_effect = [
        _result(scalars=[second, first]) if model is review.SkillListing else _result()
        for model in review.LISTING_MODELS.values()
    ]
    listings = await review._bundle_listings(uuid.UUID(int=999), db, ADMIN_SCOPE)
    assert [row.id for row in listings] == sorted((first.id, second.id))


@pytest.mark.asyncio
async def test_approve_bundle_decides_every_listing_type_in_one_commit(
    monkeypatch,
    decision_boundaries,
):
    db = _db()
    actor = _actor()
    bundle_id = uuid.UUID(int=620)
    bundle = SimpleNamespace(id=bundle_id, name="all components")
    listings = [
        _orm_listing(listing_type, bundle_id=bundle_id, index=index)[0]
        for index, listing_type in enumerate(review.LISTING_MODELS)
    ]
    db.execute.return_value = _result(scalar=bundle)
    monkeypatch.setattr(review, "_bundle_listings", AsyncMock(return_value=listings))
    skill_listing = next(row for row in listings if isinstance(row, review.SkillListing))
    db.execute.side_effect = lambda statement: (
        _result(scalars=[skill_listing.latest_version_id]) if _pending_skill_ids(statement) else _result(scalar=bundle)
    )
    monkeypatch.setattr(
        review,
        "lock_skill_version",
        AsyncMock(return_value=(skill_listing.latest_version_id, skill_listing.latest_version)),
    )

    result = await review.approve_bundle(bundle_id, db, actor)

    assert result == {"bundle_id": str(bundle_id), "name": "all components", "approved_count": 5}
    assert all(listing.status is ListingStatus.approved for listing in listings)
    assert decision_boundaries.decide.await_count == 5
    assert [awaited.kwargs["subject_type"] for awaited in decision_boundaries.decide.await_args_list] == list(
        review.LISTING_MODELS
    )
    for awaited, listing in zip(decision_boundaries.decide.await_args_list, listings, strict=True):
        assert awaited.args == (db, listing)
        assert awaited.kwargs == {
            "subject_type": review._listing_type_of(listing),
            "approved": True,
            "actor_id": actor.id,
            "version": "2.0.0",
            "submitter_id": SUBMITTER_ID,
        }
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_batch_routes_refuse_mcp_pending_update_behind_approved_pointer(monkeypatch, decision_boundaries):
    db = _db()
    actor = _actor()
    mcp, approved = _orm_listing("mcp", status=ListingStatus.approved)
    pending = type(approved)(id=uuid.uuid4(), listing_id=mcp.id, version="3.0.0", status=ListingStatus.pending)
    mcp.versions = [approved, pending]
    bundle_id = uuid.UUID(int=625)
    db.execute.return_value = _result(scalar=SimpleNamespace(id=bundle_id, name="stale MCP"))
    monkeypatch.setattr(review, "_bundle_listings", AsyncMock(return_value=[mcp]))
    monkeypatch.setattr(review, "_find_listing", AsyncMock(return_value=("mcp", mcp)))

    for action in (
        lambda: review.approve_bundle(bundle_id, db, actor),
        lambda: review.approve_mcp_with_skills(str(mcp.id), review.McpBulkApproveRequest(skill_ids=[]), db, actor),
    ):
        with pytest.raises(HTTPException) as blocked:
            await action()
        assert blocked.value.status_code == 409
    assert pending.status == ListingStatus.pending
    db.commit.assert_not_awaited()
    decision_boundaries.decide.assert_not_awaited()


@pytest.mark.asyncio
async def test_reject_bundle_decides_every_listing_type_with_shared_reason(
    monkeypatch,
    decision_boundaries,
):
    db = _db()
    actor = _actor()
    bundle_id = uuid.UUID(int=630)
    bundle = SimpleNamespace(id=bundle_id, name="all components")
    listings = [
        _orm_listing(listing_type, bundle_id=bundle_id, index=index)[0]
        for index, listing_type in enumerate(review.LISTING_MODELS)
    ]
    db.execute.return_value = _result(scalar=bundle)
    monkeypatch.setattr(review, "_bundle_listings", AsyncMock(return_value=listings))

    skill_listing = next(row for row in listings if isinstance(row, review.SkillListing))
    db.execute.side_effect = lambda statement: (
        _result(scalars=[skill_listing.latest_version_id]) if _pending_skill_ids(statement) else _result(scalar=bundle)
    )
    monkeypatch.setattr(
        review,
        "lock_skill_version",
        AsyncMock(return_value=(skill_listing.latest_version_id, skill_listing.latest_version)),
    )
    result = await review.reject_bundle(
        bundle_id,
        ReviewActionRequest(reason="bundle policy"),
        db,
        actor,
    )

    assert result == {"bundle_id": str(bundle_id), "name": "all components", "rejected_count": 5}
    assert all(listing.status is ListingStatus.rejected for listing in listings)
    assert all(listing.rejection_reason == "bundle policy" for listing in listings)
    assert [awaited.kwargs["subject_type"] for awaited in decision_boundaries.decide.await_args_list] == list(
        review.LISTING_MODELS
    )
    assert all(awaited.kwargs["reason"] == "bundle policy" for awaited in decision_boundaries.decide.await_args_list)
    assert all("submitter_id" not in awaited.kwargs for awaited in decision_boundaries.decide.await_args_list)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["approve", "reject"])
async def test_bundle_not_found_and_first_active_lock_have_no_mutation(
    monkeypatch,
    decision_boundaries,
    action,
):
    db = _db()
    bundle_id = uuid.UUID(int=640)
    function = review.approve_bundle if action == "approve" else review.reject_bundle
    args = (
        (bundle_id, db, _actor())
        if action == "approve"
        else (
            bundle_id,
            ReviewActionRequest(reason="no"),
            db,
            _actor(),
        )
    )
    db.execute.return_value = _result()
    with pytest.raises(HTTPException) as missing:
        await function(*args)
    _assert_http(missing, 404, "Bundle not found")

    bundle = SimpleNamespace(id=bundle_id, name="locked")
    locked, version = _orm_listing("mcp", bundle_id=bundle_id)
    version.is_editing = True
    version.editing_by = uuid.UUID(int=902)
    version.editing_since = datetime.now(UTC)
    db.execute.return_value = _result(scalar=bundle)
    monkeypatch.setattr(review, "_bundle_listings", AsyncMock(return_value=[locked]))
    detail = f"Cannot {action}: '{locked.name}' is currently being edited by its owner"
    with pytest.raises(HTTPException) as lock:
        await function(*args)
    _assert_http(lock, 409, detail)
    assert version.status is ListingStatus.pending
    decision_boundaries.decide.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_bundle_inbox_failure_prevents_commit(monkeypatch, decision_boundaries):
    db = _db()
    bundle_id = uuid.UUID(int=650)
    bundle = SimpleNamespace(id=bundle_id, name="failure")
    listing, _version = _orm_listing("hook", bundle_id=bundle_id)
    db.execute.return_value = _result(scalar=bundle)
    monkeypatch.setattr(review, "_bundle_listings", AsyncMock(return_value=[listing]))
    decision_boundaries.decide.side_effect = RuntimeError("bundle inbox failed")

    with pytest.raises(RuntimeError, match="bundle inbox failed"):
        await review.approve_bundle(bundle_id, db, _actor())

    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_related_skills_filters_visibility_serializes_and_escapes_search(monkeypatch):
    db = _db()
    actor = _actor(UserRole.reviewer)
    mcp = SimpleNamespace(
        id=uuid.UUID(int=700),
        name="mcp%_name",
        is_private=False,
        team_id=None,
    )
    visible = SimpleNamespace(
        id=uuid.UUID(int=701),
        name="visible skill",
        version="1.2.0",
        description="uses the mcp",
        task_type="code-review",
        target_agents=["pi"],
        mcp_server_config={"name": mcp.name},
        status=ListingStatus.pending,
        submitted_by=SUBMITTER_ID,
        created_at=NOW,
        is_private=False,
        team_id=None,
    )
    hidden = SimpleNamespace(
        id=uuid.UUID(int=702),
        name="hidden skill",
        version="1.0.0",
        description="hidden",
        task_type="general",
        target_agents=[],
        mcp_server_config={"id": str(mcp.id)},
        status=ListingStatus.pending,
        submitted_by=uuid.UUID(int=703),
        created_at=NOW,
        is_private=True,
        team_id=OTHER_TEAM_ID,
    )
    monkeypatch.setattr(review, "_require_review_scope", AsyncMock(return_value=GLOBAL_SCOPE))
    monkeypatch.setattr(review, "_find_listing", AsyncMock(return_value=("mcp", mcp)))
    query_version = aliased(SkillVersion)
    query_version.mcp_server_config = query_version.target_agents
    monkeypatch.setattr(review, "SkillVersion", query_version)
    db.execute.side_effect = [
        _result(scalars=[visible, hidden]),
        _result(scalars=[SimpleNamespace(id=SUBMITTER_ID, username=None, email="skill-author@example.test")]),
    ]

    result = await review.get_related_skills(str(mcp.id), db, actor)

    assert result == {
        "skills": [
            {
                "id": str(visible.id),
                "type": "skill",
                "name": "visible skill",
                "version": "1.2.0",
                "description": "uses the mcp",
                "task_type": "code-review",
                "target_agents": ["pi"],
                "mcp_server_config": {"name": mcp.name},
                "status": "pending",
                "submitted_by": "skill-author@example.test",
                "created_at": NOW.isoformat(),
            }
        ]
    }
    statement = db.execute.await_args_list[0].args[0]
    sql = _sql(statement)
    assert "skill_versions_1.status" in sql
    assert "skill_versions_1.target_agents IS NOT NULL" in sql
    assert "ORDER BY skill_listings.created_at DESC" in sql
    values = [str(value) for value in _params(statement).values()]
    assert any("mcp\\%\\_name" in value for value in values)
    assert any(str(mcp.id) in value for value in values)


@pytest.mark.asyncio
async def test_related_skills_missing_wrong_type_hidden_and_database_failure(monkeypatch):
    db = _db()
    actor = _actor()
    monkeypatch.setattr(review, "_require_review_scope", AsyncMock(return_value=GLOBAL_SCOPE))
    find = AsyncMock(return_value=(None, None))
    monkeypatch.setattr(review, "_find_listing", find)
    assert await review.get_related_skills("missing", db, actor) == {"skills": []}
    db.execute.assert_not_awaited()

    find.return_value = ("skill", SimpleNamespace())
    assert await review.get_related_skills("wrong", db, actor) == {"skills": []}

    hidden = SimpleNamespace(id=uuid.UUID(int=710), name="hidden", is_private=True, team_id=OTHER_TEAM_ID)
    find.return_value = ("mcp", hidden)
    with pytest.raises(HTTPException) as hidden_exc:
        await review.get_related_skills(str(hidden.id), db, actor)
    _assert_http(hidden_exc, 404, "Listing not found")

    public = SimpleNamespace(id=uuid.UUID(int=711), name="public", is_private=False, team_id=None)
    find.return_value = ("mcp", public)
    query_version = aliased(SkillVersion)
    query_version.mcp_server_config = query_version.target_agents
    monkeypatch.setattr(review, "SkillVersion", query_version)
    db.execute.side_effect = RuntimeError("related query failed")
    with pytest.raises(RuntimeError, match="related query failed"):
        await review.get_related_skills(str(public.id), db, actor)


@pytest.mark.asyncio
async def test_bulk_approve_refuses_malformed_missing_duplicate_and_nonpending_skills(monkeypatch, decision_boundaries):
    db = _db()
    actor = _actor()
    mcp, _ = _orm_listing("mcp")
    pending, _ = _orm_listing("skill", index=1)
    approved, _ = _orm_listing("skill", status=ListingStatus.approved, index=2)
    monkeypatch.setattr(review, "_find_listing", AsyncMock(return_value=("mcp", mcp)))
    cases = [
        (["malformed"], None, 400),
        ([str(uuid.UUID(int=720))], None, 404),
        ([str(pending.id), str(pending.id)], None, 400),
        ([str(approved.id)], approved, 409),
    ]
    for ids, selected, code in cases:
        db.execute.return_value = _result(scalar=selected)
        with pytest.raises(HTTPException) as refused:
            await review.approve_mcp_with_skills(str(mcp.id), review.McpBulkApproveRequest(skill_ids=ids), db, actor)
        assert refused.value.status_code == code
        assert mcp.status is ListingStatus.pending
        assert pending.status is ListingStatus.pending
        assert approved.status is ListingStatus.approved
        decision_boundaries.decide.assert_not_awaited()
        db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_bulk_skill_locks_use_canonical_order(monkeypatch, decision_boundaries):
    db = _db()
    mcp, _ = _orm_listing("mcp")
    first, _ = _orm_listing("skill", index=1)
    second, _ = _orm_listing("skill", index=2)
    by_id = {str(row.id): row for row in (first, second)}
    ordered = sorted((first.id, second.id))
    listing_results = iter(_result(scalar=by_id[str(uid)]) for uid in ordered)
    db.execute.side_effect = lambda statement: (
        _result(
            scalars=[
                by_id[
                    str(next(value for value in _bound_values(statement) if isinstance(value, uuid.UUID)))
                ].latest_version_id
            ]
        )
        if _pending_skill_ids(statement)
        else next(listing_results)
    )
    monkeypatch.setattr(review, "_find_listing", AsyncMock(return_value=("mcp", mcp)))
    lock = AsyncMock(
        side_effect=lambda _db, _listing_id, version_id: (version_id, by_id[str(_listing_id)].latest_version)
    )
    monkeypatch.setattr(review, "lock_skill_version", lock)

    await review.approve_mcp_with_skills(
        str(mcp.id), review.McpBulkApproveRequest(skill_ids=[str(ordered[1]), str(ordered[0])]), db, _actor()
    )
    assert [call.args[1] for call in lock.await_args_list] == ordered


@pytest.mark.asyncio
async def test_bulk_approve_missing_wrong_type_and_scope_fail_before_commit(monkeypatch, decision_boundaries):
    db = _db()
    actor = _actor(UserRole.user)
    find = AsyncMock(return_value=(None, None))
    monkeypatch.setattr(review, "_find_listing", find)
    request = review.McpBulkApproveRequest()

    with pytest.raises(HTTPException) as missing:
        await review.approve_mcp_with_skills("missing", request, db, actor)
    _assert_http(missing, 404, "Listing not found")

    wrong, wrong_version = _orm_listing("skill")
    find.return_value = ("skill", wrong)
    with pytest.raises(HTTPException) as wrong_type:
        await review.approve_mcp_with_skills(str(wrong.id), request, db, actor)
    _assert_http(wrong_type, 400, "Only MCP listings support bulk skill approve")
    assert wrong_version.status is ListingStatus.pending

    mcp, mcp_version = _orm_listing("mcp")
    find.return_value = ("mcp", mcp)
    decision_boundaries.scope.return_value = TEAM_SCOPE
    with pytest.raises(HTTPException) as denied:
        await review.approve_mcp_with_skills(str(mcp.id), request, db, actor)
    _assert_http(
        denied,
        403,
        "Public item is outside your review scope",
    )
    assert mcp_version.status is ListingStatus.pending
    decision_boundaries.decide.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_bulk_approve_skill_scope_or_database_failure_does_not_commit(monkeypatch, decision_boundaries):
    db = _db()
    actor = _actor(UserRole.user)
    mcp, _mcp_version = _orm_listing("mcp", is_private=True, team_id=TEAM_ID)
    skill, skill_version = _orm_listing("skill", index=1)
    decision_boundaries.scope.return_value = TEAM_SCOPE
    monkeypatch.setattr(review, "_find_listing", AsyncMock(return_value=("mcp", mcp)))
    db.execute.return_value = _result(scalar=skill)

    with pytest.raises(HTTPException) as denied:
        await review.approve_mcp_with_skills(
            str(mcp.id),
            review.McpBulkApproveRequest(skill_ids=[str(skill.id)]),
            db,
            actor,
        )
    _assert_http(
        denied,
        403,
        "Public item is outside your review scope",
    )
    assert skill_version.status is ListingStatus.pending
    db.commit.assert_not_awaited()

    mcp.status = ListingStatus.pending
    decision_boundaries.decide.reset_mock()
    db.execute.side_effect = RuntimeError("skill lookup failed")
    with pytest.raises(RuntimeError, match="skill lookup failed"):
        await review.approve_mcp_with_skills(
            str(mcp.id),
            review.McpBulkApproveRequest(skill_ids=[str(skill.id)]),
            db,
            actor,
        )
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_review_router_requires_bearer_authentication_before_database_access():
    db = _db()
    app = FastAPI()
    app.include_router(review.router)
    app.dependency_overrides[get_db] = lambda: db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/review")

    assert response.status_code == 401
    assert response.json() == {"detail": "Missing credentials"}
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_review_scope_dependency_runs_before_queue_database_failure(monkeypatch):
    db = _db()
    actor = _actor()
    scope = AsyncMock(return_value=ADMIN_SCOPE)
    agents = AsyncMock(side_effect=RuntimeError("queue service failed"))
    components = AsyncMock()
    monkeypatch.setattr(review, "_require_review_scope", scope)
    monkeypatch.setattr(review, "_query_pending_agents", agents)
    monkeypatch.setattr(review, "_query_pending_components", components)

    with pytest.raises(RuntimeError, match="queue service failed"):
        await review.list_pending(type=None, tab=None, team_id=None, db=db, current_user=actor)

    scope.assert_awaited_once_with(db, actor)
    agents.assert_awaited_once_with(db, ADMIN_SCOPE, None, actor)
    components.assert_not_awaited()
