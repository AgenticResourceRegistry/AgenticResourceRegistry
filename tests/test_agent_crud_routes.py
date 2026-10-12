# SPDX-FileCopyrightText: 2026 Shreem Seth <shreemseth26@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Focused unit coverage for the agent CRUD routes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException, Response
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError

from api.deps import get_current_user, get_db
from api.routes.agent import crud, router
from models.agent import Agent, AgentStatus, AgentVersion
from models.agent_component import AgentComponent
from models.user import UserRole
from schemas.agent import (
    AgentCreateRequest,
    AgentRestoreRequest,
    AgentUpdateRequest,
    ComponentRef,
    ExternalMcp,
    SuccessCriteria,
    SuccessMetric,
)
from services.teamspace import PublishTarget

NOW = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
USER_ID = uuid.UUID("10000000-0000-0000-0000-000000000001")
OTHER_USER_ID = uuid.UUID("10000000-0000-0000-0000-000000000002")
TEAM_ID = uuid.UUID("20000000-0000-0000-0000-000000000001")
AGENT_ID = uuid.UUID("30000000-0000-0000-0000-000000000001")
VERSION_ID = uuid.UUID("40000000-0000-0000-0000-000000000001")
MCP_ID = uuid.UUID("50000000-0000-0000-0000-000000000001")
SKILL_ID = uuid.UUID("60000000-0000-0000-0000-000000000001")
OLD_COMPONENT_ID = uuid.UUID("70000000-0000-0000-0000-000000000001")


class _FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz else NOW.replace(tzinfo=None)


def _user(*, user_id: uuid.UUID = USER_ID, role: UserRole = UserRole.user, username: str = "alice"):
    return SimpleNamespace(
        id=user_id,
        role=role,
        email=f"{username}@example.com",
        username=username,
    )


def _component(
    component_type: str = "mcp",
    component_id: uuid.UUID = MCP_ID,
    *,
    version_id: uuid.UUID = VERSION_ID,
    resolved_version: str = "1.2.3",
    order: int = 0,
    config_override: dict | None = None,
):
    return AgentComponent(
        id=uuid.uuid5(OLD_COMPONENT_ID, f"{component_type}:{component_id}"),
        agent_version_id=version_id,
        component_type=component_type,
        component_id=component_id,
        component_name="",
        resolved_version=resolved_version,
        order_index=order,
        config_override=config_override,
        created_at=NOW,
    )


def _agent(
    *,
    agent_id: uuid.UUID = AGENT_ID,
    created_by: uuid.UUID = USER_ID,
    status: AgentStatus = AgentStatus.approved,
    name: str = "review-agent",
    namespace: str = "alice",
    slug: str = "review-agent",
    is_private: bool = False,
    team_id: uuid.UUID | None = None,
    deleted_at: datetime | None = None,
    latest_version_id: uuid.UUID | None = VERSION_ID,
    components: list[AgentComponent] | None = None,
):
    version = AgentVersion(
        id=VERSION_ID,
        agent_id=agent_id,
        version="1.2.3",
        description="Reviews pull requests",
        prompt="Review carefully",
        model_name="claude-sonnet-4",
        model_config_json={"temperature": 0},
        models_by_harness={"kiro": "claude-haiku-4"},
        external_mcps=[],
        supported_harnesses=["kiro"],
        required_capabilities=["mcp_servers"],
        inferred_supported_harnesses=["kiro"],
        status=status,
        rejection_reason=None,
        download_count=7,
        released_by=created_by,
        released_at=NOW,
        created_at=NOW,
        success_criteria=None,
    )
    version.components = list(components or [])
    agent = Agent(
        id=agent_id,
        name=name,
        namespace=namespace,
        slug=slug,
        owner=namespace,
        is_private=is_private,
        team_id=team_id,
        co_authors=[],
        category="testing",
        created_by=created_by,
        created_at=NOW,
        deleted_at=deleted_at,
        updated_at=NOW,
    )
    agent.latest_version = version
    agent.latest_version_id = latest_version_id
    agent.versions = [version]
    return agent


def _agent_without_version(*, created_by: uuid.UUID = USER_ID):
    return SimpleNamespace(
        id=AGENT_ID,
        name="review-agent",
        namespace="alice",
        slug="review-agent",
        owner="alice",
        category="testing",
        created_by=created_by,
        co_authors=[],
        is_private=False,
        team_id=None,
        version="0.0.0",
        description="",
        prompt="",
        model_name="",
        model_config_json={},
        models_by_harness={},
        supported_harnesses=[],
        external_mcps=[],
        required_capabilities=[],
        inferred_supported_harnesses=[],
        components=[],
        latest_version=None,
        latest_version_id=None,
    )


def _result(*, scalar=None, scalar_rows: list | None = None, rows: list | None = None, first=None):
    result = MagicMock()
    result.scalar_one.return_value = scalar
    result.scalar_one_or_none.return_value = scalar
    result.scalar.return_value = scalar
    result.scalars.return_value.all.return_value = list(scalar_rows or [])
    result.all.return_value = list(rows or [])
    result.first.return_value = first
    return result


def _db(*results):
    db = MagicMock()
    db.execute = AsyncMock(side_effect=list(results))
    db.add = MagicMock()
    db.delete = AsyncMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


def _creation_db(*results):
    db = _db(*results)

    async def assign_database_defaults():
        for call in db.add.call_args_list:
            value = call.args[0]
            if isinstance(value, Agent) and value.id is None:
                value.id = AGENT_ID
            elif isinstance(value, AgentVersion) and value.id is None:
                value.id = VERSION_ID
            elif isinstance(value, AgentComponent) and value.id is None:
                value.id = uuid.uuid5(OLD_COMPONENT_ID, str(len(db.add.call_args_list)))

    db.flush = AsyncMock(side_effect=assign_database_defaults)
    return db


def _create_request(**overrides):
    values = {
        "name": "review-agent",
        "version": "1.2.3",
        "description": "Reviews pull requests",
        "owner": "alice",
        "prompt": "Review carefully",
        "model_name": "claude-sonnet-4",
    }
    values.update(overrides)
    return AgentCreateRequest(**values)


def _sql(statement) -> str:
    return " ".join(str(statement.compile(compile_kwargs={"literal_binds": True})).split())


@pytest.fixture
def boundaries(monkeypatch):
    import services.agent_lock as agent_lock
    import services.agent_resolver as resolver
    import services.agent_snapshot as snapshot
    import services.registry_telemetry as registry_telemetry

    target = PublishTarget(
        namespace="alice",
        slug="review-agent",
        team_id=None,
        visibility="public",
        owner="alice",
        auto_approve=False,
    )
    publish_target = AsyncMock(return_value=target)
    validate_components = AsyncMock(return_value=[])
    pins: dict = {}

    async def fake_attach(db, agent_version_id, refs, *, previous=(), order_start=0, **_options):
        # Stand in for the lock service: pin each ref to the version in ``pins``.
        links = []
        for offset, ref in enumerate(refs):
            field = (
                ref.get if isinstance(ref, dict) else lambda name, default=None, ref=ref: getattr(ref, name, default)
            )
            key = (field("component_type"), field("component_id"))
            link = AgentComponent(
                agent_version_id=agent_version_id,
                component_type=key[0],
                component_id=key[1],
                component_name="",
                resolved_version=pins.get(key, "1.0.0"),
                order_index=order_start + offset,
                config_override=field("config_override"),
            )
            db.add(link)
            links.append(link)
        return links

    attach = AsyncMock(side_effect=fake_attach)
    lock = AsyncMock(return_value={})
    build_snapshot = AsyncMock(return_value="snapshot: true\n")
    emit = MagicMock()
    invalidate = AsyncMock(return_value=0)
    validate_command = MagicMock()
    clickhouse_insert = AsyncMock()
    names = AsyncMock(return_value={})
    identities = AsyncMock(return_value={})
    statuses = AsyncMock(return_value={})
    validate_mcps = AsyncMock(return_value=[])
    identity_exists = AsyncMock(return_value=False)

    monkeypatch.setattr(crud, "datetime", _FixedDateTime)
    monkeypatch.setattr(crud, "resolve_publish_target", publish_target)
    monkeypatch.setattr(crud, "emit_registry_event", emit)
    monkeypatch.setattr(crud, "invalidate_namespace", invalidate)
    monkeypatch.setattr(crud, "validate_mcp_command", validate_command)
    monkeypatch.setattr(crud, "infer_required_features", MagicMock(return_value=["mcp_servers"]))
    monkeypatch.setattr(crud, "compute_supported_harnesses", MagicMock(return_value=["kiro"]))
    monkeypatch.setattr(crud, "_resolve_component_names", names)
    monkeypatch.setattr(crud, "_resolve_component_identities", identities)
    monkeypatch.setattr(crud, "_resolve_component_statuses", statuses)
    monkeypatch.setattr(crud, "_validate_mcp_ids", validate_mcps)
    monkeypatch.setattr(crud, "identity_exists", identity_exists)
    monkeypatch.setattr(resolver, "validate_component_ids", validate_components)
    monkeypatch.setattr(agent_lock, "attach_pinned_components", attach)
    monkeypatch.setattr(agent_lock, "lock_agent_version", lock)
    monkeypatch.setattr(snapshot, "build_yaml_snapshot", build_snapshot)
    monkeypatch.setattr(registry_telemetry, "insert_audit_log", clickhouse_insert)

    return SimpleNamespace(
        target=target,
        publish_target=publish_target,
        validate_components=validate_components,
        pins=pins,
        attach=attach,
        lock=lock,
        build_snapshot=build_snapshot,
        emit=emit,
        invalidate=invalidate,
        validate_command=validate_command,
        clickhouse_insert=clickhouse_insert,
        names=names,
        identities=identities,
        statuses=statuses,
        validate_mcps=validate_mcps,
        identity_exists=identity_exists,
    )


async def test_create_rejects_empty_description_before_external_work(boundaries):
    db = _db()

    with pytest.raises(HTTPException) as caught:
        await crud.create_agent(_create_request(description=""), db, _user())

    assert (caught.value.status_code, caught.value.detail) == (422, "Description must not be empty")
    boundaries.publish_target.assert_not_awaited()
    db.add.assert_not_called()


async def test_create_reports_component_validation_failures(boundaries):
    error = SimpleNamespace(component_type="skill", component_id=SKILL_ID, reason="not visible")
    boundaries.validate_components.return_value = [error]
    request = _create_request(components=[ComponentRef(component_type="skill", component_id=SKILL_ID)])
    db = _db()

    with pytest.raises(HTTPException) as caught:
        await crud.create_agent(request, db, _user())

    assert caught.value.status_code == 400
    assert caught.value.detail == [{"component_type": "skill", "component_id": str(SKILL_ID), "reason": "not visible"}]
    boundaries.validate_components.assert_awaited_once()
    db.add.assert_not_called()


async def test_create_rejects_unsafe_external_mcp(boundaries):
    boundaries.validate_command.side_effect = ValueError("shell operator is not allowed")
    request = _create_request(external_mcps=[ExternalMcp(name="bad", command="sh", args=["-c", "rm /tmp/x"])])

    with pytest.raises(HTTPException) as caught:
        await crud.create_agent(request, _db(), _user())

    assert caught.value.status_code == 422
    assert caught.value.detail == "Invalid MCP command: shell operator is not allowed"


async def test_create_rejects_existing_namespace_slug_and_asserts_sql(boundaries):
    db = _db(_result(scalar=AGENT_ID))

    with pytest.raises(HTTPException) as caught:
        await crud.create_agent(_create_request(), db, _user())

    assert caught.value.status_code == 409
    assert caught.value.detail == "Agent 'alice/review-agent' already exists"
    sql = _sql(db.execute.await_args.args[0])
    assert "agents.namespace = 'alice'" in sql
    assert "agents.slug = 'review-agent'" in sql
    assert "agents.deleted_at IS NULL" in sql
    db.add.assert_not_called()


async def test_create_team_agent_with_typed_components_maps_response_and_audit(boundaries, monkeypatch):
    boundaries.target = PublishTarget(
        namespace="platform",
        slug="review-agent",
        team_id=TEAM_ID,
        visibility="team",
        owner="platform",
        auto_approve=True,
    )
    boundaries.publish_target.return_value = boundaries.target
    boundaries.pins.update({("mcp", MCP_ID): "2.0.0", ("skill", SKILL_ID): "3.0.0"})
    skill = SimpleNamespace(id=SKILL_ID)
    db = _creation_db(_result(scalar=None), _result(scalar_rows=[skill]))
    loaded = _agent(
        namespace="platform",
        slug="review-agent",
        is_private=True,
        team_id=TEAM_ID,
        components=[
            _component("mcp", MCP_ID, resolved_version="2.0.0"),
            _component("skill", SKILL_ID, resolved_version="3.0.0", order=1),
        ],
    )
    load = AsyncMock(return_value=loaded)
    monkeypatch.setattr(crud, "_load_agent", load)
    boundaries.names.return_value = {str(MCP_ID): "GitHub", str(SKILL_ID): "Review"}
    criteria = SuccessCriteria(
        intended_purpose="Review changes",
        success_metrics=[SuccessMetric(name="Accuracy", target="95%", measurement="Audit")],
    )
    request = _create_request(
        owner="ignored",
        team_id=TEAM_ID,
        visibility="team",
        mcp_server_ids=[uuid.uuid4()],
        components=[
            ComponentRef(component_type="mcp", component_id=MCP_ID, config_override={"mode": "safe"}),
            ComponentRef(component_type="skill", component_id=SKILL_ID),
        ],
        external_mcps=[ExternalMcp(name="local", command="python", args=["server.py"])],
        success_criteria=criteria,
    )

    response = await crud.create_agent(request, db, _user())

    added = [call.args[0] for call in db.add.call_args_list]
    new_agent = next(value for value in added if isinstance(value, Agent))
    version = next(value for value in added if isinstance(value, AgentVersion))
    links = [value for value in added if isinstance(value, AgentComponent)]
    assert (new_agent.namespace, new_agent.slug, new_agent.owner, new_agent.team_id, new_agent.is_private) == (
        "platform",
        "review-agent",
        "platform",
        TEAM_ID,
        True,
    )
    assert version.status == AgentStatus.approved
    assert version.reviewed_by == USER_ID
    assert version.reviewed_at == NOW
    assert version.success_criteria == criteria.model_dump()
    assert version.required_capabilities == ["mcp_servers"]
    assert version.inferred_supported_harnesses == ["kiro"]
    assert [(link.component_type, link.resolved_version, link.order_index) for link in links] == [
        ("mcp", "2.0.0", 0),
        ("skill", "3.0.0", 1),
    ]
    assert request.mcp_server_ids == []
    boundaries.validate_mcps.assert_awaited_once_with(
        [], db, current_user=ANY, target_team_id=TEAM_ID, enforce_target=True
    )
    boundaries.validate_components.assert_awaited_once()
    boundaries.validate_command.assert_called_once_with("python", ["server.py"])
    boundaries.build_snapshot.assert_awaited_once_with(version, db)
    db.commit.assert_awaited_once()
    load.assert_awaited_once_with(db, str(AGENT_ID), prefer_user_id=USER_ID, current_user=ANY)
    assert response.qualified_name == "platform/review-agent"
    assert [link.component_name for link in response.component_links] == ["GitHub", "Review"]
    boundaries.emit.assert_called_once_with(
        action="agent.create",
        user_id=str(USER_ID),
        user_email="alice@example.com",
        user_role="user",
        agent_id=str(AGENT_ID),
        resource_name="review-agent",
        metadata={"agent_name": "review-agent", "version": "1.2.3", "component_count": "2"},
    )
    boundaries.clickhouse_insert.assert_not_awaited()


async def test_create_legacy_mcp_links_are_version_pinned_and_pending(boundaries, monkeypatch):
    second_mcp = uuid.UUID("50000000-0000-0000-0000-000000000002")
    boundaries.pins.update({("mcp", MCP_ID): "1.0.0", ("mcp", second_mcp): "2.0.0"})
    db = _creation_db(_result(scalar=None))
    monkeypatch.setattr(crud, "_load_agent", AsyncMock(return_value=_agent()))
    request = _create_request(owner="", mcp_server_ids=[MCP_ID, second_mcp])

    await crud.create_agent(request, db, _user())

    added = [call.args[0] for call in db.add.call_args_list]
    new_agent = next(value for value in added if isinstance(value, Agent))
    version = next(value for value in added if isinstance(value, AgentVersion))
    links = [value for value in added if isinstance(value, AgentComponent)]
    assert new_agent.owner == "alice"
    assert version.status == AgentStatus.pending
    assert version.reviewed_by is None
    assert version.reviewed_at is None
    assert [(link.component_id, link.resolved_version, link.order_index) for link in links] == [
        (MCP_ID, "1.0.0", 0),
        (second_mcp, "2.0.0", 1),
    ]
    boundaries.attach.assert_awaited_once_with(
        db,
        version.id,
        [{"component_type": "mcp", "component_id": MCP_ID}, {"component_type": "mcp", "component_id": second_mcp}],
        current_user=_user(),
    )
    boundaries.lock.assert_awaited_once_with(db, new_agent, version)


@pytest.mark.parametrize(
    ("database_message", "converted"),
    [("uq_agents_active_namespace_slug", True), ("network write failed", False)],
)
async def test_create_rolls_back_integrity_failures(boundaries, monkeypatch, database_message, converted):
    db = _creation_db(_result(scalar=None))
    failure = IntegrityError("insert", {}, RuntimeError(database_message))
    db.commit.side_effect = failure
    load = AsyncMock()
    monkeypatch.setattr(crud, "_load_agent", load)

    if converted:
        with pytest.raises(HTTPException) as caught:
            await crud.create_agent(_create_request(), db, _user())
        assert caught.value.status_code == 409
        assert "alice/review-agent" in caught.value.detail
    else:
        with pytest.raises(IntegrityError) as caught:
            await crud.create_agent(_create_request(), db, _user())
        assert caught.value is failure

    db.rollback.assert_awaited_once()
    load.assert_not_awaited()
    boundaries.emit.assert_not_called()


async def test_list_agents_builds_scoped_search_queries_and_maps_batch_data(boundaries):
    agent = _agent()
    db = _db(
        _result(scalar=1),
        _result(scalar_rows=[agent]),
        _result(rows=[(AGENT_ID, 4.126)]),
        _result(rows=[]),  # public fork counts
        _result(rows=[(USER_ID, "creator@example.com", "creator")]),
    )
    response = Response()

    result = await crud.list_agents(
        response=response,
        search="review",
        namespace=" Alice ",
        category="testing",
        team_id=TEAM_ID,
        composable_for_team_id=None,
        public_only=False,
        limit=25,
        offset=5,
        db=db,
        current_user=_user(),
    )

    assert response.headers["X-Total-Count"] == "1"
    assert len(result) == 1
    assert result[0].qualified_name == "alice/review-agent"
    assert result[0].average_rating == 4.13
    assert result[0].created_by_email == "creator@example.com"
    assert result[0].created_by_username == "creator"
    assert result[0].component_count == 0
    count_sql = _sql(db.execute.await_args_list[0].args[0])
    list_sql = _sql(db.execute.await_args_list[1].args[0])
    for sql in (count_sql, list_sql):
        assert "agent_versions.status = 'approved'" in sql
        assert "agents.deleted_at IS NULL" in sql
        assert "agents.namespace = 'alice'" in sql
        assert "agents.category = 'testing'" in sql
        assert f"agents.team_id = '{TEAM_ID.hex}'" in sql
        assert "lower(agents.name) LIKE lower('%review%')" in sql
    assert "ORDER BY" in list_sql
    assert "LIMIT 25 OFFSET 5" in list_sql


async def test_list_agents_empty_page_skips_batch_queries(boundaries):
    db = _db(_result(scalar=0), _result(scalar_rows=[]))
    response = Response()

    result = await crud.list_agents(
        response=response,
        search=None,
        namespace=None,
        category=None,
        team_id=None,
        composable_for_team_id=TEAM_ID,
        public_only=True,
        limit=50,
        offset=0,
        db=db,
        current_user=_user(role=UserRole.admin),
    )

    assert result == []
    assert response.headers["X-Total-Count"] == "0"
    assert db.execute.await_count == 2
    sql = _sql(db.execute.await_args_list[1].args[0])
    assert "agents.is_private = false" in sql
    assert "agents.team_id =" not in sql


async def test_my_agents_filters_owner_and_maps_rating(boundaries):
    agent = _agent()
    db = _db(_result(scalar_rows=[agent]), _result(rows=[(AGENT_ID, 3.555)]), _result(rows=[]))

    result = await crud.my_agents(db=db, current_user=_user())

    assert len(result) == 1
    assert result[0].average_rating == 3.56
    assert result[0].created_by_email == "alice@example.com"
    assert result[0].created_by_username == "alice"
    sql = _sql(db.execute.await_args_list[0].args[0])
    assert f"agents.created_by = '{USER_ID.hex}'" in sql
    assert "agents.deleted_at IS NULL" in sql
    assert "ORDER BY agents.created_at DESC" in sql


async def test_archived_agents_maps_creators_and_ratings(boundaries):
    agent = _agent(status=AgentStatus.archived)
    db = _db(
        _result(scalar_rows=[agent]),
        _result(rows=[(AGENT_ID, 2.0)]),
        _result(rows=[(USER_ID, "archiver@example.com", None)]),
    )

    result = await crud.archived_agents(db=db, current_user=_user(role=UserRole.admin))

    assert result[0].status == AgentStatus.archived
    assert result[0].average_rating == 2.0
    assert result[0].created_by_email == "archiver@example.com"
    sql = _sql(db.execute.await_args_list[0].args[0])
    assert "agent_versions.status = 'archived'" in sql
    assert "agents.deleted_at IS NULL" in sql


@pytest.mark.parametrize(("role", "owner_filter"), [(UserRole.user, True), (UserRole.admin, False)])
async def test_deleted_agents_scopes_regular_users_but_not_admins(boundaries, role, owner_filter):
    deleted = _agent(deleted_at=NOW)
    first_result = _result(scalar_rows=[deleted]) if owner_filter else _result(scalar_rows=[])
    results = [first_result]
    if owner_filter:
        results.extend(
            [
                _result(rows=[(AGENT_ID, 4.0)]),
                _result(rows=[(USER_ID, "deleted@example.com", "alice")]),
            ]
        )
    db = _db(*results)

    response = await crud.deleted_agents(db=db, current_user=_user(role=role))

    sql = _sql(db.execute.await_args_list[0].args[0])
    assert "agents.deleted_at IS NOT NULL" in sql
    assert (f"agents.created_by = '{USER_ID.hex}'" in sql) is owner_filter
    if owner_filter:
        assert response[0].deleted_at == NOW
        assert response[0].average_rating == 4.0
        assert response[0].created_by_email == "deleted@example.com"
    else:
        assert response == []


async def test_archived_endpoint_requires_admin_role(boundaries, monkeypatch):
    import api.deps as deps

    app = FastAPI()
    app.include_router(router)
    db = _db()
    app.dependency_overrides[get_current_user] = lambda: _user()
    app.dependency_overrides[get_db] = lambda: db
    security_event = AsyncMock()
    monkeypatch.setattr(deps, "emit_security_event", security_event)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/agents/archived")

    assert response.status_code == 403
    assert response.json()["detail"] == "Insufficient permissions"
    security_event.assert_awaited_once()
    db.execute.assert_not_awaited()


async def test_get_agent_resolves_qualified_name_and_maps_components(boundaries, monkeypatch):
    agent = _agent(components=[_component()])
    load = AsyncMock(return_value=agent)
    monkeypatch.setattr(crud, "_load_agent", load)
    boundaries.names.return_value = {str(MCP_ID): "GitHub"}
    boundaries.identities.return_value = {str(MCP_ID): ("acme", "github")}
    boundaries.statuses.return_value = {str(MCP_ID): "archived"}
    db = _db(_result(first=("creator@example.com", "creator")), _result(rows=[]))

    result = await crud.get_agent("alice/review-agent", db=db, current_user=_user())

    load.assert_awaited_once_with(
        db,
        "alice/review-agent",
        prefer_user_id=USER_ID,
        current_user=ANY,
    )
    assert result.user_permission == "owner"
    assert result.created_by_email == "creator@example.com"
    assert result.created_by_username == "creator"
    assert result.latest_approved_version == "1.2.3"
    assert result.component_links[0].component_name == "GitHub"
    assert result.component_links[0].namespace == "acme"
    assert result.component_links[0].slug == "github"
    assert result.component_links[0].qualified_name == "acme/github"
    assert result.component_links[0].status == "archived"
    assert result.mcp_links[0].mcp_name == "GitHub"
    assert "users.id" in _sql(db.execute.await_args_list[0].args[0])


@pytest.mark.parametrize(("loaded", "permission", "status_code"), [(None, "view", 404), (_agent(), "none", 403)])
async def test_get_agent_hides_missing_or_forbidden_agents(boundaries, monkeypatch, loaded, permission, status_code):
    monkeypatch.setattr(crud, "_load_agent", AsyncMock(return_value=loaded))
    monkeypatch.setattr(crud, "get_effective_agent_permission", MagicMock(return_value=permission))

    with pytest.raises(HTTPException) as caught:
        await crud.get_agent(str(AGENT_ID), db=_db(), current_user=_user())

    assert caught.value.status_code == status_code
    boundaries.names.assert_not_awaited()


async def test_version_suggestions_uses_highest_valid_version(boundaries, monkeypatch):
    agent = _agent()
    monkeypatch.setattr(crud, "_load_agent", AsyncMock(return_value=agent))
    db = _db(_result(rows=[("1.3.0",), ("not-semver",), ("2.0.0",), ("1.9.9",)]))

    result = await crud.version_suggestions(str(AGENT_ID), db=db, current_user=_user())

    assert result == {
        "current": "2.0.0",
        "suggestions": {"patch": "2.0.1", "minor": "2.1.0", "major": "3.0.0"},
    }
    sql = _sql(db.execute.await_args.args[0])
    assert f"agent_versions.agent_id = '{AGENT_ID.hex}'" in sql
    assert "ORDER BY agent_versions.created_at DESC" in sql


@pytest.mark.parametrize(("loaded", "permission", "status_code"), [(None, "view", 404), (_agent(), "none", 403)])
async def test_version_suggestions_enforces_detail_access(boundaries, monkeypatch, loaded, permission, status_code):
    monkeypatch.setattr(crud, "_load_agent", AsyncMock(return_value=loaded))
    monkeypatch.setattr(crud, "get_effective_agent_permission", MagicMock(return_value=permission))

    with pytest.raises(HTTPException) as caught:
        await crud.version_suggestions(str(AGENT_ID), db=_db(), current_user=_user())

    assert caught.value.status_code == status_code


@pytest.mark.parametrize(
    ("agent", "actor", "payload", "status_code", "detail"),
    [
        (None, _user(), {}, 404, "Agent not found"),
        (_agent(created_by=OTHER_USER_ID), _user(), {}, 403, "Not the agent owner or editor"),
        (
            _agent(is_private=True, team_id=TEAM_ID),
            _user(),
            {"visibility": "public"},
            422,
            "Visibility cannot be changed here",
        ),
        (
            _agent(is_private=True, team_id=TEAM_ID),
            _user(),
            {"team_id": uuid.UUID("20000000-0000-0000-0000-000000000002")},
            422,
            "Teamspace cannot be changed here",
        ),
    ],
)
async def test_update_rejects_missing_unauthorized_or_publish_target_changes(
    boundaries, monkeypatch, agent, actor, payload, status_code, detail
):
    monkeypatch.setattr(crud, "_load_agent", AsyncMock(return_value=agent))
    db = _db()

    with pytest.raises(HTTPException) as caught:
        await crud.update_agent(str(AGENT_ID), AgentUpdateRequest(**payload), db=db, current_user=actor)

    assert caught.value.status_code == status_code
    assert detail in caught.value.detail
    db.commit.assert_not_awaited()


async def test_admin_can_update_another_agents_identity_fields_without_snapshot(boundaries, monkeypatch):
    agent = _agent(created_by=OTHER_USER_ID)
    load = AsyncMock(side_effect=[agent, agent])
    monkeypatch.setattr(crud, "_load_agent", load)
    db = _db(_result(scalar=None))
    request = AgentUpdateRequest(
        name="renamed-agent",
        category="security",
        owner="platform",
        visibility="public",
    )

    result = await crud.update_agent(str(AGENT_ID), request, db=db, current_user=_user(role=UserRole.admin))

    assert (agent.name, agent.category, agent.owner) == ("renamed-agent", "security", "platform")
    assert result.name == "renamed-agent"
    assert result.slug == "review-agent"
    sql = _sql(db.execute.await_args_list[0].args[0])
    assert "agents.name = 'renamed-agent'" in sql
    assert "agents.deleted_at IS NULL" in sql
    assert f"agents.id != '{AGENT_ID.hex}'" in sql
    db.flush.assert_not_awaited()
    boundaries.build_snapshot.assert_not_awaited()
    db.commit.assert_awaited_once()
    boundaries.emit.assert_called_once_with(
        action="agent.update",
        user_id=str(USER_ID),
        user_email="alice@example.com",
        user_role="admin",
        agent_id=str(AGENT_ID),
        resource_name="renamed-agent",
    )


async def test_update_rejects_duplicate_active_name(boundaries, monkeypatch):
    agent = _agent()
    monkeypatch.setattr(crud, "_load_agent", AsyncMock(return_value=agent))
    db = _db(_result(scalar=uuid.uuid4()))

    with pytest.raises(HTTPException) as caught:
        await crud.update_agent(str(AGENT_ID), AgentUpdateRequest(name="existing-agent"), db=db, current_user=_user())

    assert caught.value.status_code == 409
    assert caught.value.detail == "An active agent named 'existing-agent' already exists."
    db.commit.assert_not_awaited()


async def test_update_typed_components_refreshes_features_snapshot_and_response(boundaries, monkeypatch):
    old_mcp = _component()
    old_skill = _component("skill", SKILL_ID, order=1)
    agent = _agent(components=[old_mcp, old_skill], status=AgentStatus.pending)
    load = AsyncMock(side_effect=[agent, agent])
    monkeypatch.setattr(crud, "_load_agent", load)
    boundaries.pins.update({("mcp", MCP_ID): "4.0.0", ("skill", SKILL_ID): "5.0.0"})
    current_mcp = _component(resolved_version="4.0.0")
    current_skill = _component("skill", SKILL_ID, resolved_version="5.0.0", order=1)
    skill_listing = SimpleNamespace(id=SKILL_ID)
    db = _db(
        _result(scalar_rows=[old_mcp, old_skill]),
        _result(scalar_rows=[current_mcp, current_skill]),
        _result(scalar_rows=[skill_listing]),
    )
    criteria = SuccessCriteria(intended_purpose="Review safely")
    request = AgentUpdateRequest(
        version_bump_type="minor",
        description="Updated description",
        prompt="Updated prompt",
        model_name="claude-opus-4",
        model_config_json={"temperature": 1},
        models_by_harness={"kiro": "claude-opus-4"},
        supported_harnesses=["kiro", "codex"],
        external_mcps=[ExternalMcp(name="local", command="python", args=["main.py"])],
        components=[
            ComponentRef(component_type="mcp", component_id=MCP_ID, config_override={"safe": True}),
            ComponentRef(component_type="skill", component_id=SKILL_ID),
        ],
        success_criteria=criteria,
    )

    response = await crud.update_agent(str(AGENT_ID), request, db=db, current_user=_user())

    assert agent.version == "1.3.0"
    assert agent.description == "Updated description"
    assert agent.prompt == "Updated prompt"
    assert agent.model_name == "claude-opus-4"
    assert agent.model_config_json == {"temperature": 1}
    assert agent.models_by_harness == {"kiro": "claude-opus-4"}
    assert agent.supported_harnesses == ["kiro", "codex"]
    assert agent.external_mcps == [{"name": "local", "command": "python", "args": ["main.py"], "env": {}, "url": None}]
    assert agent.latest_version.success_criteria == criteria.model_dump()
    assert agent.required_capabilities == ["mcp_servers"]
    assert agent.inferred_supported_harnesses == ["kiro"]
    assert db.delete.await_args_list[0].args[0] is old_mcp
    assert db.delete.await_args_list[1].args[0] is old_skill
    added = [call.args[0] for call in db.add.call_args_list]
    assert [(item.component_type, item.resolved_version, item.order_index) for item in added] == [
        ("mcp", "4.0.0", 0),
        ("skill", "5.0.0", 1),
    ]
    assert added[0].config_override == {"safe": True}
    boundaries.validate_components.assert_awaited_once()
    boundaries.validate_command.assert_called_once_with("python", ["main.py"])
    boundaries.build_snapshot.assert_awaited_once_with(agent.latest_version, db)
    assert agent.latest_version.yaml_snapshot == "snapshot: true\n"
    assert db.flush.await_count == 2
    assert response.version == "1.3.0"


@pytest.mark.parametrize("status", [AgentStatus.approved, AgentStatus.archived])
@pytest.mark.parametrize(
    "payload",
    [
        {"prompt": "Rewritten"},
        {"version_bump_type": "minor"},
        {"components": [{"component_type": "mcp", "component_id": MCP_ID}]},
        {"success_criteria": None},
    ],
)
async def test_update_refuses_to_edit_an_approved_version_in_place(boundaries, monkeypatch, status, payload):
    agent = _agent(status=status)
    monkeypatch.setattr(crud, "_load_agent", AsyncMock(return_value=agent))
    db = _db()

    with pytest.raises(HTTPException) as caught:
        await crud.update_agent(str(AGENT_ID), AgentUpdateRequest(**payload), db=db, current_user=_user())

    assert caught.value.status_code == 409
    assert "observal agent release alice/review-agent" in caught.value.detail
    assert agent.latest_version.version == "1.2.3"
    assert agent.latest_version.prompt == "Review carefully"
    db.execute.assert_not_awaited()
    boundaries.attach.assert_not_awaited()


async def test_update_still_edits_identity_fields_of_an_approved_agent(boundaries, monkeypatch):
    agent = _agent()
    monkeypatch.setattr(crud, "_load_agent", AsyncMock(side_effect=[agent, agent]))
    db = _db(_result(scalar=None))

    await crud.update_agent(str(AGENT_ID), AgentUpdateRequest(category="security"), db=db, current_user=_user())

    assert agent.category == "security"
    db.commit.assert_awaited_once()


async def test_update_typed_components_reports_validation_errors(boundaries, monkeypatch):
    agent = _agent(status=AgentStatus.pending)
    monkeypatch.setattr(crud, "_load_agent", AsyncMock(return_value=agent))
    boundaries.validate_components.return_value = [
        SimpleNamespace(component_type="skill", component_id=SKILL_ID, reason="not approved")
    ]

    with pytest.raises(HTTPException) as caught:
        await crud.update_agent(
            str(AGENT_ID),
            AgentUpdateRequest(components=[ComponentRef(component_type="skill", component_id=SKILL_ID)]),
            db=_db(),
            current_user=_user(),
        )

    assert caught.value.status_code == 400
    assert caught.value.detail[0]["reason"] == "not approved"


async def test_update_legacy_mcp_links_only_replaces_mcp_components(boundaries, monkeypatch):
    old = _component()
    current = _component(resolved_version="9.0.0")
    boundaries.pins[("mcp", MCP_ID)] = "9.0.0"
    agent = _agent(components=[old], status=AgentStatus.pending)
    monkeypatch.setattr(crud, "_load_agent", AsyncMock(side_effect=[agent, agent]))
    db = _db(_result(scalar_rows=[old]), _result(scalar_rows=[current]))

    await crud.update_agent(str(AGENT_ID), AgentUpdateRequest(mcp_server_ids=[MCP_ID]), db=db, current_user=_user())

    boundaries.validate_mcps.assert_awaited_once_with(
        [MCP_ID], db, current_user=ANY, target_team_id=None, enforce_target=True
    )
    db.delete.assert_awaited_once_with(old)
    boundaries.attach.assert_awaited_once_with(
        db,
        agent.latest_version.id,
        [{"component_type": "mcp", "component_id": MCP_ID}],
        previous=[old],
        current_user=_user(),
    )
    replacement = db.add.call_args.args[0]
    assert (replacement.component_type, replacement.component_id, replacement.resolved_version) == (
        "mcp",
        MCP_ID,
        "9.0.0",
    )
    old_query = _sql(db.execute.await_args_list[0].args[0])
    assert "agent_components.component_type = 'mcp'" in old_query
    assert db.flush.await_count == 2
    boundaries.build_snapshot.assert_awaited_once()


@pytest.mark.parametrize(
    ("payload", "detail"),
    [
        ({"success_criteria": {"intended_purpose": "Review"}}, "Agent has no version to update"),
        (
            {"components": [{"component_type": "mcp", "component_id": MCP_ID}]},
            "Agent has no version to update components on",
        ),
        ({"mcp_server_ids": [MCP_ID]}, "Agent has no version to update components on"),
        ({"external_mcps": []}, "Agent has no version to update features on"),
    ],
)
async def test_update_requires_a_version_for_version_owned_fields(boundaries, monkeypatch, payload, detail):
    agent = _agent_without_version()
    monkeypatch.setattr(crud, "_load_agent", AsyncMock(return_value=agent))

    with pytest.raises(HTTPException) as caught:
        await crud.update_agent(str(AGENT_ID), AgentUpdateRequest(**payload), db=_db(), current_user=_user())

    assert (caught.value.status_code, caught.value.detail) == (400, detail)


async def test_update_rejects_unsafe_external_mcp(boundaries, monkeypatch):
    monkeypatch.setattr(crud, "_load_agent", AsyncMock(return_value=_agent(status=AgentStatus.pending)))
    boundaries.validate_command.side_effect = ValueError("unsafe")

    with pytest.raises(HTTPException) as caught:
        await crud.update_agent(
            str(AGENT_ID),
            AgentUpdateRequest(external_mcps=[ExternalMcp(name="bad", command="sh", args=["-c", "x"])]),
            db=_db(),
            current_user=_user(),
        )

    assert (caught.value.status_code, caught.value.detail) == (422, "Invalid MCP command: unsafe")


async def test_update_commit_failure_has_no_audit_or_response_reload(boundaries, monkeypatch):
    agent = _agent(status=AgentStatus.pending)
    load = AsyncMock(return_value=agent)
    monkeypatch.setattr(crud, "_load_agent", load)
    db = _db()
    db.commit.side_effect = RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        await crud.update_agent(str(AGENT_ID), AgentUpdateRequest(description="changed"), db=db, current_user=_user())

    assert load.await_count == 1
    boundaries.emit.assert_not_called()


@pytest.mark.parametrize(
    ("loaded", "actor", "status_code"), [(None, _user(), 404), (_agent(), _user(user_id=OTHER_USER_ID), 403)]
)
async def test_delete_rejects_missing_or_non_owner(boundaries, monkeypatch, loaded, actor, status_code):
    monkeypatch.setattr(crud, "_load_agent", AsyncMock(return_value=loaded))
    db = _db()

    with pytest.raises(HTTPException) as caught:
        await crud.delete_agent(str(AGENT_ID), db=db, current_user=actor)

    assert caught.value.status_code == status_code
    db.commit.assert_not_awaited()
    boundaries.invalidate.assert_not_awaited()


async def test_delete_soft_deletes_identity_without_cascading_versions(boundaries, monkeypatch):
    component = _component()
    agent = _agent(components=[component])
    monkeypatch.setattr(crud, "_load_agent", AsyncMock(return_value=agent))
    db = _db()

    result = await crud.delete_agent(str(AGENT_ID), db=db, current_user=_user())

    assert agent.deleted_at == NOW
    assert result == {
        "deleted": str(AGENT_ID),
        "name": "review-agent",
        "deleted_at": agent.deleted_at.isoformat(),
    }
    assert agent.latest_version.id == VERSION_ID
    assert agent.components == [component]
    db.delete.assert_not_awaited()
    db.commit.assert_awaited_once()
    boundaries.invalidate.assert_awaited_once_with("dashboard")
    boundaries.emit.assert_called_once_with(
        action="agent.delete",
        user_id=str(USER_ID),
        user_email="alice@example.com",
        user_role="user",
        agent_id=str(AGENT_ID),
        resource_name="review-agent",
    )


async def test_delete_commit_failure_does_not_invalidate_or_audit(boundaries, monkeypatch):
    monkeypatch.setattr(crud, "_load_agent", AsyncMock(return_value=_agent()))
    db = _db()
    db.commit.side_effect = RuntimeError("commit failed")

    with pytest.raises(RuntimeError, match="commit failed"):
        await crud.delete_agent(str(AGENT_ID), db=db, current_user=_user())

    boundaries.invalidate.assert_not_awaited()
    boundaries.emit.assert_not_called()


@pytest.mark.parametrize(
    ("loaded", "actor", "status_code"),
    [
        (None, _user(), 404),
        (_agent(deleted_at=None), _user(), 404),
        (_agent(created_by=OTHER_USER_ID, deleted_at=NOW), _user(), 403),
    ],
)
async def test_restore_rejects_non_deleted_or_non_owner(boundaries, monkeypatch, loaded, actor, status_code):
    monkeypatch.setattr(crud, "_load_agent", AsyncMock(return_value=loaded))
    db = _db()

    with pytest.raises(HTTPException) as caught:
        await crud.restore_deleted_agent(str(AGENT_ID), None, db=db, current_user=actor)

    assert caught.value.status_code == status_code
    db.commit.assert_not_awaited()


async def test_restore_detects_active_identity_collision(boundaries, monkeypatch):
    agent = _agent(deleted_at=NOW)
    monkeypatch.setattr(crud, "_load_agent", AsyncMock(return_value=agent))
    boundaries.identity_exists.return_value = True

    with pytest.raises(HTTPException) as caught:
        await crud.restore_deleted_agent(
            str(AGENT_ID), AgentRestoreRequest(name="renamed-agent"), db=_db(), current_user=_user()
        )

    assert caught.value.status_code == 409
    assert caught.value.detail == "Agent 'alice/renamed-agent' already exists. Restore with a new name."


@pytest.mark.parametrize(
    ("restore_request", "expected_name", "expected_slug"),
    [
        (None, "review-agent", "review-agent"),
        (AgentRestoreRequest(name="renamed-agent"), "renamed-agent", "renamed-agent"),
    ],
)
async def test_restore_clears_tombstone_and_can_rename(
    boundaries, monkeypatch, restore_request, expected_name, expected_slug
):
    agent = _agent(deleted_at=NOW)
    monkeypatch.setattr(crud, "_load_agent", AsyncMock(return_value=agent))
    db = _db()

    result = await crud.restore_deleted_agent(str(AGENT_ID), restore_request, db=db, current_user=_user())

    assert (agent.name, agent.slug, agent.deleted_at) == (expected_name, expected_slug, None)
    assert result == {"id": str(AGENT_ID), "name": expected_name, "status": AgentStatus.approved}
    boundaries.identity_exists.assert_awaited_once_with(db, Agent, "alice", expected_slug, exclude_id=AGENT_ID)
    boundaries.invalidate.assert_awaited_once_with("dashboard")
    boundaries.emit.assert_called_once_with(
        action="agent.restore",
        user_id=str(USER_ID),
        user_email="alice@example.com",
        user_role="user",
        agent_id=str(AGENT_ID),
        resource_name=expected_name,
    )


@pytest.mark.parametrize(
    ("route", "required_status", "action", "returned_status"),
    [
        (crud.archive_agent, AgentStatus.approved, "agent.archive", "archived"),
        (crud.unarchive_agent, AgentStatus.archived, "agent.unarchive", "approved"),
    ],
)
async def test_archive_transitions_latest_version_with_sql_and_audit(
    boundaries, monkeypatch, route, required_status, action, returned_status
):
    agent = _agent(status=required_status)
    monkeypatch.setattr(crud, "_load_agent", AsyncMock(return_value=agent))
    db = _db(_result())

    result = await route(str(AGENT_ID), db=db, current_user=_user())

    assert result == {"id": str(AGENT_ID), "name": "review-agent", "status": returned_status}
    sql = _sql(db.execute.await_args.args[0])
    assert "UPDATE agent_versions SET status=" in sql
    assert f"agent_versions.id = '{VERSION_ID.hex}'" in sql
    assert f"status='{returned_status}'" in sql
    db.commit.assert_awaited_once()
    boundaries.emit.assert_called_once_with(
        action=action,
        user_id=str(USER_ID),
        user_email="alice@example.com",
        user_role="user",
        agent_id=str(AGENT_ID),
        resource_name="review-agent",
    )


@pytest.mark.parametrize(
    ("route", "required_status", "wrong_status"),
    [
        (crud.archive_agent, AgentStatus.approved, AgentStatus.pending),
        (crud.unarchive_agent, AgentStatus.archived, AgentStatus.approved),
    ],
)
@pytest.mark.parametrize("case", ["missing", "unauthorized", "wrong_status", "no_version"])
async def test_archive_and_unarchive_authorization_and_state_guards(
    boundaries, monkeypatch, route, required_status, wrong_status, case
):
    actor = _user()
    if case == "missing":
        agent = None
        expected = 404
    elif case == "unauthorized":
        agent = _agent(created_by=OTHER_USER_ID, status=required_status)
        expected = 403
    elif case == "wrong_status":
        agent = _agent(status=wrong_status)
        expected = 400
    else:
        agent = _agent(status=required_status, latest_version_id=None)
        expected = 400
    monkeypatch.setattr(crud, "_load_agent", AsyncMock(return_value=agent))
    db = _db()

    with pytest.raises(HTTPException) as caught:
        await route(str(AGENT_ID), db=db, current_user=actor)

    assert caught.value.status_code == expected
    db.commit.assert_not_awaited()
    boundaries.emit.assert_not_called()
