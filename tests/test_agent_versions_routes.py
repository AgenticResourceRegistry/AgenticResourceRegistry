# SPDX-FileCopyrightText: 2026 Shreem Seth <shreemseth26@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Focused business coverage for the agent version routes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from api.deps import get_current_user, get_db, get_registry_user
from api.routes import agent as agent_routes
from api.routes import agent_versions as routes
from models.agent import Agent, AgentStatus, AgentVersion
from models.agent_component import AgentComponent
from models.user import UserRole
from schemas.agent import (
    AgentVersionCreateRequest,
    ComponentRef,
    ExternalMcp,
    SuccessCriteria,
    SuccessMetric,
)
from services.agent_lock import PinnedListing

NOW = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
USER_ID = uuid.UUID("10000000-0000-0000-0000-000000000001")
OTHER_USER_ID = uuid.UUID("10000000-0000-0000-0000-000000000002")
REVIEWER_ID = uuid.UUID("10000000-0000-0000-0000-000000000003")
TEAM_ID = uuid.UUID("20000000-0000-0000-0000-000000000001")
AGENT_ID = uuid.UUID("30000000-0000-0000-0000-000000000001")
VERSION_ID = uuid.UUID("40000000-0000-0000-0000-000000000001")
LATEST_VERSION_ID = uuid.UUID("40000000-0000-0000-0000-000000000002")
MCP_ID = uuid.UUID("50000000-0000-0000-0000-000000000001")
SKILL_ID = uuid.UUID("60000000-0000-0000-0000-000000000001")
HOOK_ID = uuid.UUID("70000000-0000-0000-0000-000000000001")
PROMPT_ID = uuid.UUID("80000000-0000-0000-0000-000000000001")


class _FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz else NOW.replace(tzinfo=None)


def _user(
    *,
    user_id: uuid.UUID = USER_ID,
    role: UserRole = UserRole.user,
    username: str = "alice",
):
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
    name: str = "",
    resolved_version: str | None = "1.2.3",
    order: int = 0,
    config_override: dict | None = None,
) -> AgentComponent:
    return AgentComponent(
        id=uuid.uuid5(VERSION_ID, f"{component_type}:{component_id}"),
        agent_version_id=version_id,
        component_type=component_type,
        component_id=component_id,
        component_name=name,
        resolved_version=resolved_version,
        order_index=order,
        config_override=config_override,
        created_at=NOW,
    )


def _version(
    version: str = "1.2.3",
    *,
    version_id: uuid.UUID = VERSION_ID,
    status: AgentStatus | str = AgentStatus.pending,
    components: list[AgentComponent] | None = None,
    yaml_snapshot: str | None = "description: current\n",
    harness_configs: dict | None = None,
) -> AgentVersion:
    value = AgentVersion(
        id=version_id,
        agent_id=AGENT_ID,
        version=version,
        description="Reviews pull requests",
        prompt="Review carefully",
        model_name="claude-sonnet-4",
        model_config_json={"temperature": 0},
        models_by_harness={"kiro": "claude-haiku-4"},
        external_mcps=[{"name": "local", "command": "python", "args": ["server.py"]}],
        supported_harnesses=["kiro"],
        required_capabilities=["mcp_servers"],
        inferred_supported_harnesses=["kiro"],
        yaml_snapshot=yaml_snapshot,
        harness_configs=harness_configs,
        status=status,
        is_prerelease=False,
        rejection_reason=None,
        download_count=7,
        released_by=USER_ID,
        released_at=NOW,
        reviewed_by=None,
        reviewed_at=None,
        created_at=NOW,
        success_criteria={"intended_purpose": "Review changes"},
    )
    value.components = list(components or [])
    return value


def _agent(
    *,
    created_by: uuid.UUID = USER_ID,
    co_authors: list[str] | None = None,
    is_private: bool = False,
    team_id: uuid.UUID | None = None,
    latest: AgentVersion | None = None,
) -> Agent:
    agent = Agent(
        id=AGENT_ID,
        name="review-agent",
        namespace="alice",
        slug="review-agent",
        owner="alice",
        is_private=is_private,
        team_id=team_id,
        co_authors=list(co_authors or []),
        category="testing",
        created_by=created_by,
        created_at=NOW,
        updated_at=NOW,
    )
    agent.latest_version = latest
    agent.latest_version_id = latest.id if latest else None
    agent.versions = [latest] if latest else []
    return agent


def _request(**overrides) -> AgentVersionCreateRequest:
    values = {
        "version": "2.0.0",
        "description": "Second release",
        "prompt": "Review carefully",
        "model_name": "claude-sonnet-4",
    }
    values.update(overrides)
    return AgentVersionCreateRequest(**values)


def _result(*, one=None, rows: list | None = None, scalar=None):
    result = MagicMock()
    result.scalar_one.return_value = one
    result.scalar_one_or_none.return_value = one
    result.scalar.return_value = scalar
    result.scalars.return_value.all.return_value = list(rows or [])
    result.all.return_value = list(rows or [])
    return result


def _db(*results):
    db = MagicMock()
    remaining = list(results)
    statements = []

    async def execute(statement):
        statements.append(statement)
        if not remaining:
            raise AssertionError(f"Unexpected SQL statement: {statement}")
        return remaining.pop(0)

    db.execute = AsyncMock(side_effect=execute)
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.statements = statements
    return db


def _creation_db(*results):
    db = _db(*results)

    async def assign_database_defaults():
        for added in (call.args[0] for call in db.add.call_args_list):
            if isinstance(added, AgentVersion):
                if added.id is None:
                    added.id = VERSION_ID
                if added.created_at is None:
                    added.created_at = NOW

    db.flush = AsyncMock(side_effect=assign_database_defaults)
    return db


def _sql(statement) -> str:
    return " ".join(str(statement.compile(compile_kwargs={"literal_binds": True})).split())


@pytest.fixture
def boundaries(monkeypatch):
    import services.agent_lock as agent_lock
    import services.agent_snapshot as snapshot
    import services.clickhouse as clickhouse
    import services.model_resolver as model_resolver

    load = AsyncMock()
    validate_components = AsyncMock(return_value=[])
    pins: dict = {}

    async def fake_attach(db, agent_version_id, refs, *, order_start=0, **_options):
        # Stand in for the lock service: pin each ref to the version in ``pins``.
        links = []
        for offset, ref in enumerate(refs):
            link = AgentComponent(
                agent_version_id=agent_version_id,
                component_type=ref.component_type,
                component_id=ref.component_id,
                component_name="",
                resolved_version=pins.get((ref.component_type, ref.component_id), "1.0.0"),
                order_index=order_start + offset,
                config_override=ref.config_override,
            )
            db.add(link)
            links.append(link)
        return links

    attach = AsyncMock(side_effect=fake_attach)
    lock = AsyncMock(return_value={})
    pinned = AsyncMock(return_value={})
    infer = MagicMock(return_value=[])
    compute = MagicMock(return_value=[])
    generate = MagicMock(return_value={"files": {}})
    build_snapshot = AsyncMock(return_value="snapshot: generated\n")
    resolve_model = AsyncMock(return_value=("resolved-model", []))
    publish = AsyncMock(return_value=SimpleNamespace(number=42))
    legacy_audit = AsyncMock()
    clickhouse_insert = AsyncMock()
    names = AsyncMock(return_value={})
    statuses = AsyncMock(return_value={})

    monkeypatch.setattr(routes, "datetime", _FixedDateTime)
    monkeypatch.setattr(routes, "_load_agent", load)
    monkeypatch.setattr(routes, "validate_component_ids", validate_components)
    monkeypatch.setattr(agent_lock, "attach_pinned_components", attach)
    monkeypatch.setattr(agent_lock, "lock_agent_version", lock)
    monkeypatch.setattr(agent_lock, "pinned_versions", pinned)
    monkeypatch.setattr(routes, "infer_required_features", infer)
    monkeypatch.setattr(routes, "compute_supported_harnesses", compute)
    monkeypatch.setattr(routes, "generate_agent_config", generate)
    monkeypatch.setattr(routes, "audit", legacy_audit)
    monkeypatch.setattr(snapshot, "build_yaml_snapshot", build_snapshot)
    monkeypatch.setattr(model_resolver, "resolve_model_for_harness", resolve_model)
    monkeypatch.setattr(routes, "submit_for_review", publish)
    monkeypatch.setattr(clickhouse, "insert_audit_log", clickhouse_insert)
    monkeypatch.setattr(agent_routes, "_resolve_component_names", names)
    monkeypatch.setattr(agent_routes, "_resolve_component_statuses", statuses)

    return SimpleNamespace(
        load=load,
        validate_components=validate_components,
        pins=pins,
        attach=attach,
        lock=lock,
        pinned=pinned,
        infer=infer,
        compute=compute,
        generate=generate,
        build_snapshot=build_snapshot,
        resolve_model=resolve_model,
        publish=publish,
        legacy_audit=legacy_audit,
        clickhouse_insert=clickhouse_insert,
        names=names,
        statuses=statuses,
    )


def test_summary_and_detail_serialize_version_owned_fields():
    components = [
        _component(name="GitHub"),
        _component("skill", SKILL_ID, name="", resolved_version=None, order=1),
    ]
    version = _version(components=components)

    summary = routes._version_to_summary(version)
    detail = routes._version_to_detail(version)

    assert summary == {
        "id": str(VERSION_ID),
        "agent_id": str(AGENT_ID),
        "version": "1.2.3",
        "description": "Reviews pull requests",
        "status": "pending",
        "is_prerelease": False,
        "download_count": 7,
        "supported_harnesses": ["kiro"],
        "released_by": str(USER_ID),
        "released_at": NOW,
        "created_at": NOW,
        "rejection_reason": None,
        "component_count": 2,
    }
    assert detail["prompt"] == "Review carefully"
    assert detail["models_by_harness"] == {"kiro": "claude-haiku-4"}
    assert detail["success_criteria"] == {"intended_purpose": "Review changes"}
    assert detail["components"] == [
        {
            "component_type": "mcp",
            "component_id": str(MCP_ID),
            "name": "GitHub",
            "resolved_version": "1.2.3",
        },
        {
            "component_type": "skill",
            "component_id": str(SKILL_ID),
            "name": "",
            "resolved_version": "",
        },
    ]
    assert "is_editing" not in detail

    version.status = "approved"
    version.models_by_harness = None
    version.components = []
    assert routes._version_to_summary(version)["status"] == "approved"
    assert routes._version_to_summary(version)["component_count"] == 0
    assert routes._version_to_detail(version)["models_by_harness"] == {}


async def test_legacy_audit_shim_is_a_noop_and_never_reaches_clickhouse(monkeypatch):
    import services.clickhouse as clickhouse

    insert = AsyncMock()
    monkeypatch.setattr(clickhouse, "insert_audit_log", insert)

    assert await routes.audit(_user(), "agent.version.publish") is None
    insert.assert_not_awaited()


async def test_load_agent_delegates_with_the_callers_visibility(monkeypatch):
    delegated = AsyncMock(return_value=_agent())
    monkeypatch.setattr(agent_routes, "_load_agent", delegated)
    user = _user()
    db = _db()

    result = await routes._load_agent(db, "alice/review-agent", user)

    assert result.id == AGENT_ID
    delegated.assert_awaited_once_with(db, "alice/review-agent", current_user=user)


async def _call_read_helper(name: str, db, user):
    if name == "list":
        return await routes._list_agent_versions(str(AGENT_ID), 1, 20, db, user)
    if name == "detail":
        return await routes._get_agent_version(str(AGENT_ID), "1.2.3", db, user)
    if name == "harness":
        return await routes._get_agent_harness_config(str(AGENT_ID), "1.2.3", "kiro", db, user)
    return await routes._get_version_diff(str(AGENT_ID), "1.0.0", "1.2.3", db, user)


@pytest.mark.parametrize("name", ["list", "detail", "harness", "diff"])
async def test_read_helpers_report_missing_agent(name, boundaries):
    boundaries.load.return_value = None

    with pytest.raises(HTTPException) as caught:
        await _call_read_helper(name, _db(), _user())

    assert (caught.value.status_code, caught.value.detail) == (404, "Agent not found")


@pytest.mark.parametrize("name", ["list", "detail", "harness", "diff"])
async def test_read_helpers_reject_no_effective_permission(name, boundaries, monkeypatch):
    boundaries.load.return_value = _agent()
    monkeypatch.setattr(routes, "get_effective_agent_permission", MagicMock(return_value="none"))

    with pytest.raises(HTTPException) as caught:
        await _call_read_helper(name, _db(), _user())

    assert (caught.value.status_code, caught.value.detail) == (
        403,
        "Insufficient permissions to view this agent",
    )


async def test_list_versions_filters_unapproved_rows_and_paginates(boundaries):
    agent = _agent(created_by=OTHER_USER_ID)
    approved = _version(status=AgentStatus.approved)
    boundaries.load.return_value = agent
    db = _db(_result(rows=[approved]), _result(scalar=3))

    response = await routes._list_agent_versions(str(AGENT_ID), 2, 10, db, _user())

    assert response["items"][0]["version"] == "1.2.3"
    assert response["total"] == 3
    assert response["page"] == 2
    list_sql, count_sql = map(_sql, db.statements)
    assert f"agent_versions.agent_id = '{AGENT_ID.hex}'" in list_sql
    assert "agent_versions.status = 'approved'" in list_sql
    assert "ORDER BY agent_versions.created_at DESC" in list_sql
    assert "LIMIT 10 OFFSET 10" in list_sql
    assert f"agent_versions.agent_id = '{AGENT_ID.hex}'" in count_sql


async def test_list_versions_owner_can_see_pending_rows(boundaries):
    agent = _agent()
    pending = _version()
    boundaries.load.return_value = agent
    db = _db(_result(rows=[pending]), _result(scalar=1))

    response = await routes._list_agent_versions(str(AGENT_ID), 1, 20, db, _user())

    assert response["items"][0]["status"] == "pending"
    assert "agent_versions.status = 'approved'" not in _sql(db.statements[0])


async def test_get_version_resolves_missing_names_and_component_statuses(boundaries):
    components = [
        _component(),
        _component("skill", SKILL_ID, name="Stored name", order=1),
    ]
    version = _version(components=components)
    agent = _agent(latest=version)
    boundaries.load.return_value = agent
    boundaries.names.return_value = {str(MCP_ID): "GitHub", str(SKILL_ID): "Ignored"}
    boundaries.statuses.return_value = {str(MCP_ID): "approved", str(SKILL_ID): "archived"}
    db = _db(_result(one=version))

    detail = await routes._get_agent_version(str(AGENT_ID), "1.2.3", db, _user())

    assert detail["components"] == [
        {
            "component_type": "mcp",
            "component_id": str(MCP_ID),
            "name": "GitHub",
            "resolved_version": "1.2.3",
            "status": "approved",
        },
        {
            "component_type": "skill",
            "component_id": str(SKILL_ID),
            "name": "Stored name",
            "resolved_version": "1.2.3",
            "status": "archived",
        },
    ]
    boundaries.names.assert_awaited_once_with(components, db)
    boundaries.statuses.assert_awaited_once_with(components, db)
    assert "agent_versions.status = 'approved'" not in _sql(db.statements[0])


async def test_get_version_hides_nonapproved_version_from_regular_viewer(boundaries):
    boundaries.load.return_value = _agent(created_by=OTHER_USER_ID)
    db = _db(_result(one=None))

    with pytest.raises(HTTPException) as caught:
        await routes._get_agent_version(str(AGENT_ID), "2.0.0", db, _user())

    assert (caught.value.status_code, caught.value.detail) == (404, "Version not found")
    sql = _sql(db.statements[0])
    assert "agent_versions.status = 'approved'" in sql
    assert "agent_versions.version = '2.0.0'" in sql
    boundaries.names.assert_not_awaited()


async def test_create_semver_validation_happens_before_database_work(boundaries):
    with pytest.raises(ValidationError):
        _request(version="01.2.3")

    request = MagicMock(version="v2")
    with pytest.raises(HTTPException) as caught:
        await routes._create_agent_version(str(AGENT_ID), request, _db(), _user())

    assert caught.value.status_code == 422
    assert caught.value.detail == "Invalid semver string: 'v2'"
    boundaries.load.assert_not_awaited()


async def test_create_reports_missing_agent_and_nonowner(boundaries):
    request = _request()
    boundaries.load.return_value = None
    with pytest.raises(HTTPException) as missing:
        await routes._create_agent_version(str(AGENT_ID), request, _db(), _user())
    assert (missing.value.status_code, missing.value.detail) == (404, "Agent not found")

    boundaries.load.return_value = _agent(created_by=OTHER_USER_ID)
    with pytest.raises(HTTPException) as forbidden:
        await routes._create_agent_version(str(AGENT_ID), request, _db(), _user())
    assert (forbidden.value.status_code, forbidden.value.detail) == (
        403,
        "Not authorized to release versions",
    )


async def test_create_rejects_duplicate_version_with_agent_scoped_sql(boundaries):
    boundaries.load.return_value = _agent()
    existing = _version(version="2.0.0")
    db = _db(_result(one=existing))

    with pytest.raises(HTTPException) as caught:
        await routes._create_agent_version(str(AGENT_ID), _request(), db, _user())

    assert caught.value.status_code == 409
    assert caught.value.detail == "Version '2.0.0' already exists for this agent"
    sql = _sql(db.statements[0])
    assert f"agent_versions.agent_id = '{AGENT_ID.hex}'" in sql
    assert "agent_versions.version = '2.0.0'" in sql
    db.add.assert_not_called()


async def test_create_surfaces_component_validation_errors_for_target_team(boundaries):
    agent = _agent(is_private=True, team_id=TEAM_ID)
    boundaries.load.return_value = agent
    error = SimpleNamespace(component_type="skill", component_id=SKILL_ID, reason="not approved")
    boundaries.validate_components.return_value = [error]
    request = _request(components=[ComponentRef(component_type="skill", component_id=SKILL_ID)])
    db = _db(_result(one=None))

    with pytest.raises(HTTPException) as caught:
        await routes._create_agent_version(str(AGENT_ID), request, db, _user())

    assert caught.value.status_code == 400
    assert caught.value.detail == [{"component_type": "skill", "component_id": str(SKILL_ID), "reason": "not approved"}]
    boundaries.validate_components.assert_awaited_once_with(
        [{"component_type": "skill", "component_id": SKILL_ID}],
        db,
        current_user=_user(),
        target_team_id=TEAM_ID,
        enforce_target=True,
    )
    boundaries.attach.assert_not_awaited()
    db.add.assert_not_called()


async def test_create_resolves_components_builds_snapshot_and_reports_conflicts(boundaries):
    agent = _agent(created_by=OTHER_USER_ID, co_authors=[str(USER_ID)])
    boundaries.load.return_value = agent
    boundaries.pins.update({("mcp", MCP_ID): "4.1.0", ("skill", SKILL_ID): "3.2.0"})
    boundaries.infer.return_value = ["mcp_servers", "skills"]
    boundaries.compute.return_value = ["kiro", "claude-code"]
    skill_listing = SimpleNamespace(id=SKILL_ID, name="Reviewer")
    mcp_listing = SimpleNamespace(id=MCP_ID, name="GitHub")
    db = _creation_db(
        _result(one=None),
        _result(scalar=2),
        _result(rows=[skill_listing]),
        _result(rows=[mcp_listing]),
    )
    criteria = SuccessCriteria(
        intended_purpose="Review changes",
        success_metrics=[SuccessMetric(name="Accuracy", target="95%", measurement="Audit")],
    )
    request = _request(
        model_config_json={"temperature": 0.2},
        models_by_harness={"kiro": "claude-haiku-4"},
        external_mcps=[ExternalMcp(name="local", command="python", args=["server.py"])],
        supported_harnesses=["kiro", "codex"],
        components=[
            ComponentRef(component_type="mcp", component_id=MCP_ID, config_override={"safe": True}),
            ComponentRef(component_type="skill", component_id=SKILL_ID),
        ],
        yaml_snapshot="caller supplied and ignored",
        is_prerelease=True,
        success_criteria=criteria,
    )

    async def resolve_model(harness, **kwargs):
        if harness == "codex":
            raise RuntimeError("catalog unavailable")
        return "claude-haiku-4", ["normalized alias"]

    boundaries.resolve_model.side_effect = resolve_model
    boundaries.generate.return_value = {"agent_profile": {"content": "review"}}
    pinned_mcp = SimpleNamespace(version="4.1.0", command="npx")
    boundaries.pinned.return_value = {("mcp", MCP_ID): pinned_mcp}

    response = await routes._create_agent_version(str(AGENT_ID), request, db, _user())

    added = [call.args[0] for call in db.add.call_args_list]
    version = next(item for item in added if isinstance(item, AgentVersion))
    links = [item for item in added if isinstance(item, AgentComponent)]
    assert version.id == VERSION_ID
    assert version.status == AgentStatus.pending
    assert version.released_at == NOW
    assert version.model_config_json == {"temperature": 0.2}
    assert version.models_by_harness == {"kiro": "claude-haiku-4"}
    assert version.external_mcps == [
        {"name": "local", "command": "python", "args": ["server.py"], "env": {}, "url": None}
    ]
    assert version.success_criteria == criteria.model_dump()
    assert version.is_prerelease is True
    assert version.required_capabilities == ["mcp_servers", "skills"]
    assert version.inferred_supported_harnesses == ["kiro", "claude-code"]
    assert version.yaml_snapshot == "snapshot: generated\n"
    assert version.harness_configs == {"kiro": {"agent_profile": {"content": "review"}}}
    assert [(link.component_type, link.component_id, link.resolved_version, link.order_index) for link in links] == [
        ("mcp", MCP_ID, "4.1.0", 0),
        ("skill", SKILL_ID, "3.2.0", 1),
    ]
    assert links[0].config_override == {"safe": True}
    boundaries.validate_components.assert_awaited_once()
    boundaries.attach.assert_awaited_once_with(
        db,
        VERSION_ID,
        request.components,
        previous=[],
        refresh=False,
        require_approved=True,
        current_user=_user(),
    )
    boundaries.lock.assert_awaited_once_with(db, agent, version)
    proxy = boundaries.infer.call_args.args[0]
    assert proxy.components == request.components
    assert proxy.external_mcps == version.external_mcps
    assert boundaries.infer.call_args.kwargs == {"skill_listings": {SKILL_ID: skill_listing}}
    boundaries.compute.assert_called_once_with(["mcp_servers", "skills"])
    boundaries.build_snapshot.assert_awaited_once_with(version, db)
    assert db.flush.await_count == 2
    generate_call = boundaries.generate.call_args
    config_agent = generate_call.args[0]
    assert generate_call.args[1] == "kiro"
    assert config_agent.id == agent.id
    assert config_agent.name == agent.name
    assert config_agent.version == version.version
    assert config_agent.components == links
    # Stored release configs come from the pinned MCP release, not the listing's latest.
    boundaries.pinned.assert_awaited_once_with(db, links)
    generated_mcp = generate_call.kwargs["mcp_listings"][MCP_ID]
    assert isinstance(generated_mcp, PinnedListing)
    assert (generated_mcp.listing, generated_mcp.pinned_version) == (mcp_listing, pinned_mcp)
    assert generate_call.kwargs["options"] == {
        "_resolved_model": "claude-haiku-4",
        "_model_warnings": ["normalized alias"],
    }
    boundaries.publish.assert_awaited_once_with(db, "agent", agent, version, USER_ID, message=None)
    assert response["review_number"] == 42 and response["review_url"] == "/review/42"
    db.commit.assert_awaited_once()
    assert response["id"] == str(VERSION_ID)
    assert response["status"] == "pending"
    assert response["created_at"] == NOW
    assert response["warnings"] == [
        "This agent already has 2 pending version(s)",
        "harness config generation failed for: codex. These will 404 until regenerated.",
    ]
    duplicate_sql, pending_sql, skill_sql, mcp_sql = map(_sql, db.statements)
    assert "agent_versions.version = '2.0.0'" in duplicate_sql
    assert "agent_versions.status = 'pending'" in pending_sql
    assert f"skill_listings.id IN ('{SKILL_ID.hex}')" in skill_sql
    assert f"mcp_listings.id IN ('{MCP_ID.hex}')" in mcp_sql
    boundaries.legacy_audit.assert_not_awaited()
    boundaries.clickhouse_insert.assert_not_awaited()


async def test_create_draft_skips_queue_and_has_no_warning(boundaries):
    agent = _agent()
    boundaries.load.return_value = agent
    db = _creation_db(_result(one=None), _result(scalar=None))

    response = await routes._create_agent_version(
        str(AGENT_ID),
        _request(save_as_draft=True, supported_harnesses=[]),
        db,
        _user(),
    )

    version = next(call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], AgentVersion))
    assert version.status == AgentStatus.draft
    assert response["status"] == "draft"
    assert "warnings" not in response
    boundaries.validate_components.assert_not_awaited()
    boundaries.attach.assert_awaited_once_with(
        db, VERSION_ID, [], previous=[], refresh=False, require_approved=True, current_user=_user()
    )
    boundaries.generate.assert_not_called()
    boundaries.publish.assert_not_awaited()
    assert len(db.statements) == 2


async def test_create_snapshot_failure_aborts_before_notification_or_commit(boundaries):
    boundaries.load.return_value = _agent()
    boundaries.build_snapshot.side_effect = RuntimeError("snapshot failed")
    db = _creation_db(_result(one=None), _result(scalar=0))

    with pytest.raises(RuntimeError, match="snapshot failed"):
        await routes._create_agent_version(str(AGENT_ID), _request(), db, _user())

    boundaries.publish.assert_not_awaited()
    db.commit.assert_not_awaited()
    boundaries.legacy_audit.assert_not_awaited()


async def test_create_commit_failure_propagates_without_audit(boundaries):
    boundaries.load.return_value = _agent()
    db = _creation_db(_result(one=None), _result(scalar=0))
    db.commit.side_effect = RuntimeError("commit failed")

    with pytest.raises(RuntimeError, match="commit failed"):
        await routes._create_agent_version(str(AGENT_ID), _request(), db, _user())

    boundaries.publish.assert_awaited_once()
    boundaries.legacy_audit.assert_not_awaited()
    boundaries.clickhouse_insert.assert_not_awaited()


async def test_harness_config_serves_only_pregenerated_visible_config(boundaries):
    version = _version(status=AgentStatus.approved, harness_configs={"kiro": {"mcpServers": {}}})
    boundaries.load.return_value = _agent(created_by=OTHER_USER_ID)
    db = _db(_result(one=version))

    response = await routes._get_agent_harness_config(str(AGENT_ID), "1.2.3", "kiro", db, _user())

    assert response == {"mcpServers": {}}
    sql = _sql(db.statements[0])
    assert "agent_versions.status = 'approved'" in sql
    assert "agent_versions.version = '1.2.3'" in sql


async def test_harness_config_reports_missing_version_and_available_configs(boundaries):
    boundaries.load.return_value = _agent()
    missing_db = _db(_result(one=None))
    with pytest.raises(HTTPException) as missing:
        await routes._get_agent_harness_config(str(AGENT_ID), "9.0.0", "kiro", missing_db, _user())
    assert (missing.value.status_code, missing.value.detail) == (404, "Version not found")

    version = _version(harness_configs={"kiro": {"files": {}}})
    unavailable_db = _db(_result(one=version))
    with pytest.raises(HTTPException) as unavailable:
        await routes._get_agent_harness_config(str(AGENT_ID), "1.2.3", "codex", unavailable_db, _user())
    assert unavailable.value.status_code == 404
    assert unavailable.value.detail == "harness 'codex' not supported by this agent version. Available: ['kiro']"


async def test_version_diff_uses_snapshots_and_reports_component_changes(boundaries):
    old_components = [
        _component(resolved_version="1.0.0", name="GitHub"),
        _component("skill", SKILL_ID, resolved_version="1.0.0", name="Review", order=1),
        _component("prompt", PROMPT_ID, resolved_version="1.0.0", name="Policy", order=2),
    ]
    new_components = [
        _component(version_id=LATEST_VERSION_ID, resolved_version="2.0.0", name="GitHub"),
        _component(
            "hook",
            HOOK_ID,
            version_id=LATEST_VERSION_ID,
            resolved_version="3.0.0",
            name="Guard",
            order=1,
        ),
        _component(
            "prompt",
            PROMPT_ID,
            version_id=LATEST_VERSION_ID,
            resolved_version="1.0.0",
            name="Policy",
            order=2,
        ),
    ]
    old = _version("1.0.0", components=old_components, yaml_snapshot="prompt: old\n")
    new = _version(
        "2.0.0",
        version_id=LATEST_VERSION_ID,
        components=new_components,
        yaml_snapshot=None,
    )
    boundaries.load.return_value = _agent(created_by=OTHER_USER_ID)
    boundaries.build_snapshot.return_value = "prompt: new\n"
    db = _db(_result(one=old), _result(one=new))

    response = await routes._get_version_diff(str(AGENT_ID), "1.0.0", "2.0.0", db, _user())

    assert response["agent_id"] == str(AGENT_ID)
    assert response["version_a"] == "1.0.0"
    assert response["version_b"] == "2.0.0"
    assert f"{'-' * 3} v1.0.0" in response["yaml_diff"]
    assert f"{'+' * 3} v2.0.0" in response["yaml_diff"]
    assert response["component_changes"] == [
        {"type": "mcp", "name": "GitHub", "change": "updated", "from": "1.0.0", "to": "2.0.0"},
        {"type": "hook", "name": "Guard", "change": "added", "version": "3.0.0"},
        {"type": "skill", "name": "Review", "change": "removed", "version": "1.0.0"},
    ]
    boundaries.build_snapshot.assert_awaited_once_with(new, db)
    for statement in db.statements:
        sql = _sql(statement)
        assert "agent_versions.status = 'approved'" in sql
        assert f"agent_versions.agent_id = '{AGENT_ID.hex}'" in sql


async def test_version_diff_reports_each_missing_side(boundaries):
    boundaries.load.return_value = _agent()
    first_missing = _db(_result(one=None))
    with pytest.raises(HTTPException) as v1_missing:
        await routes._get_version_diff(str(AGENT_ID), "1.0.0", "2.0.0", first_missing, _user())
    assert (v1_missing.value.status_code, v1_missing.value.detail) == (404, "Version '1.0.0' not found")

    old = _version("1.0.0")
    second_missing = _db(_result(one=old), _result(one=None))
    with pytest.raises(HTTPException) as v2_missing:
        await routes._get_version_diff(str(AGENT_ID), "1.0.0", "2.0.0", second_missing, _user())
    assert (v2_missing.value.status_code, v2_missing.value.detail) == (404, "Version '2.0.0' not found")


def _route_app(user, db) -> FastAPI:
    app = FastAPI()
    app.include_router(routes.agent_version_router, prefix="/api/v1/agents")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_registry_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    return app


async def test_route_handlers_delegate_and_json_serialize_owned_responses(monkeypatch):
    user = _user(user_id=REVIEWER_ID, role=UserRole.reviewer, username="reviewer")
    db = _db()
    payload = {"id": VERSION_ID, "at": NOW}
    list_versions = AsyncMock(return_value=payload)
    get_version = AsyncMock(return_value=payload)
    create_version = AsyncMock(return_value=payload)
    get_config = AsyncMock(return_value=payload)
    get_diff = AsyncMock(return_value=payload)
    monkeypatch.setattr(routes, "_list_agent_versions", list_versions)
    monkeypatch.setattr(routes, "_get_agent_version", get_version)
    monkeypatch.setattr(routes, "_create_agent_version", create_version)
    monkeypatch.setattr(routes, "_get_agent_harness_config", get_config)
    monkeypatch.setattr(routes, "_get_version_diff", get_diff)
    app = _route_app(user, db)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        responses = [
            await client.get(f"/api/v1/agents/{AGENT_ID}/versions", params={"page": 2, "page_size": 5}),
            await client.get(f"/api/v1/agents/{AGENT_ID}/versions/2.0.0"),
            await client.post(
                f"/api/v1/agents/{AGENT_ID}/versions",
                json={"version": "2.0.0", "model_name": "claude-sonnet-4"},
            ),
            await client.get(f"/api/v1/agents/{AGENT_ID}/versions/2.0.0/harness/kiro"),
            await client.get(f"/api/v1/agents/{AGENT_ID}/versions/1.0.0/diff/2.0.0"),
        ]

    assert [response.status_code for response in responses] == [200] * 5
    for response in responses:
        assert response.json()["id"] == str(VERSION_ID)
        assert response.json()["at"].startswith("2026-04-21T12:00:00")
    list_versions.assert_awaited_once_with(
        agent_id=str(AGENT_ID),
        page=2,
        page_size=5,
        db=db,
        current_user=user,
    )
    get_version.assert_awaited_once_with(agent_id=str(AGENT_ID), version="2.0.0", db=db, current_user=user)
    create_call = create_version.await_args.kwargs
    assert create_call["agent_id"] == str(AGENT_ID)
    assert create_call["req"].version == "2.0.0"
    assert create_call["db"] is db
    assert create_call["current_user"] is user
    get_config.assert_awaited_once_with(
        agent_id=str(AGENT_ID), version="2.0.0", harness="kiro", db=db, current_user=user
    )
    get_diff.assert_awaited_once_with(agent_id=str(AGENT_ID), v1="1.0.0", v2="2.0.0", db=db, current_user=user)


async def test_old_agent_review_action_is_removed():
    app = _route_app(_user(), _db())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/agents/{AGENT_ID}/versions/2.0.0/review", json={"action": "approve"})
    assert response.status_code == 404
