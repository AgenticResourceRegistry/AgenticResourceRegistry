# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Agent fork route authorization, count/list symmetry and response contract."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.deps import get_current_user, get_db, get_registry_user
from api.routes.agent import fork as routes
from api.routes.agent import router
from models.agent import Agent, AgentStatus, AgentVersion
from models.base import Base
from models.user import UserRole
from schemas.fork import ForkRequest
from services.registry_fork import ForkResult


@pytest.fixture(autouse=True)
def _forking_on(monkeypatch):
    monkeypatch.setattr(routes.dynamic_settings, "get_bool", AsyncMock(return_value=True))


def _agent(name="Source", status=AgentStatus.approved):
    user_id = uuid.uuid4()
    agent = Agent(
        id=uuid.uuid4(),
        name=name,
        namespace="alice",
        slug=name.lower(),
        owner="alice",
        created_by=user_id,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        is_private=False,
        deleted_at=None,
        co_authors=[],
    )
    version = AgentVersion(
        id=uuid.uuid4(),
        agent_id=agent.id,
        version="1.2.3",
        description="Description",
        prompt="Prompt",
        model_name="example-model",
        model_config_json={},
        models_by_harness={},
        external_mcps=[],
        supported_harnesses=[],
        required_capabilities=[],
        inferred_supported_harnesses=[],
        status=status,
        released_by=user_id,
        created_at=datetime.now(UTC),
    )
    version.components = []
    agent.latest_version = version
    agent.latest_version_id = version.id
    agent.versions = [version]
    return agent


@pytest.mark.asyncio
async def test_post_fork_returns_draft_and_emits_audit_after_commit(monkeypatch):
    source = _agent()
    child = _agent("Variant", AgentStatus.draft)
    child.forked_from_id = source.id
    child.forked_from_version_id = source.latest_version.id
    child.forked_from_ref = "alice/source@1.2.3"
    child.forked_at = datetime.now(UTC)
    load = AsyncMock(side_effect=[source, child])
    monkeypatch.setattr(routes, "_load_agent", load)
    service = AsyncMock(return_value=ForkResult(child, child.latest_version, ["warning"]))
    monkeypatch.setattr(routes, "fork_agent", service)
    committed = AsyncMock()
    monkeypatch.setattr(routes, "commit_or_name_conflict", committed)
    monkeypatch.setattr(routes, "provenance_for", AsyncMock(return_value={"available": False}))
    audit = MagicMock()
    monkeypatch.setattr(routes, "emit_registry_event", audit)
    user = SimpleNamespace(
        id=uuid.uuid4(), username="alice", email="alice@example.com", role=SimpleNamespace(value="user")
    )
    db = MagicMock()

    response = await routes.create_agent_fork(str(source.id), ForkRequest(name="Variant"), db=db, current_user=user)
    assert response.status == AgentStatus.draft
    assert response.forked_from.available is False
    assert response.warnings == ["warning"]
    committed.assert_awaited_once_with(db, "agent")
    audit.assert_called_once()
    assert audit.call_args.kwargs["metadata"]["source_id"] == str(source.id)
    assert service.await_args.kwargs["current_user"] is user
    assert load.await_count == 2


@pytest.mark.asyncio
async def test_post_fork_hides_public_pending_source(monkeypatch):
    source = _agent(status=AgentStatus.pending)
    monkeypatch.setattr(routes, "_load_agent", AsyncMock(return_value=source))
    monkeypatch.setattr(routes, "get_effective_agent_permission", lambda *_: "view")
    monkeypatch.setattr(routes, "may_view_unapproved", lambda *_: False)
    service = AsyncMock()
    monkeypatch.setattr(routes, "fork_agent", service)
    with pytest.raises(HTTPException) as error:
        await routes.create_agent_fork(
            str(source.id),
            ForkRequest(),
            db=MagicMock(),
            current_user=SimpleNamespace(id=uuid.uuid4()),
        )
    assert error.value.status_code == 404
    service.assert_not_awaited()


@pytest.mark.asyncio
async def test_public_forks_list_and_count_share_approval_predicate(monkeypatch):
    source = _agent()
    child = _agent("Variant")
    child.forked_from_id = source.id
    db = MagicMock()
    db.scalar = AsyncMock(return_value=1)
    result = MagicMock()
    result.scalars.return_value.all.return_value = [child]
    db.execute = AsyncMock(return_value=result)
    monkeypatch.setattr(routes, "_load_agent", AsyncMock(return_value=source))
    monkeypatch.setattr(routes, "agent_fork_counts", AsyncMock(return_value={child.id: 0}))
    monkeypatch.setattr(routes, "provenance_for_many", AsyncMock(return_value={child.id: {"available": False}}))

    response = await routes.list_agent_forks(str(source.id), limit=10, offset=0, db=db, current_user=None)
    assert response["total"] == len(response["items"]) == 1
    assert response["items"][0].forked_from.available is False
    for statement in (db.scalar.await_args.args[0], db.execute.await_args.args[0]):
        sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
        assert "agent_versions.status = 'approved'" in sql
        assert "agents.is_private IS false" in sql
        assert "agents.forked_from_id" in sql


@pytest.mark.asyncio
async def test_feature_flag_blocks_source_lookup_and_forks_list(monkeypatch):
    monkeypatch.setattr(routes.dynamic_settings, "get_bool", AsyncMock(return_value=False))
    load = AsyncMock()
    monkeypatch.setattr(routes, "_load_agent", load)
    for operation in (
        routes.create_agent_fork(
            "unknown", ForkRequest(), db=MagicMock(), current_user=SimpleNamespace(id=uuid.uuid4())
        ),
        routes.list_agent_forks("unknown", limit=10, offset=0, db=MagicMock(), current_user=None),
        routes.agent_fork_diff("unknown", version=None, db=MagicMock(), current_user=None),
    ):
        with pytest.raises(HTTPException) as error:
            await operation
        assert error.value.status_code == 403
    load.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_and_public_forks_list_over_asgi_with_real_database(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            source = _agent()
            db.add(source)
            await db.commit()
            user = SimpleNamespace(
                id=uuid.uuid4(),
                username="alice",
                email="alice@example.com",
                role=UserRole.user,
            )
            app = FastAPI()
            app.include_router(router)

            async def session():
                yield db

            app.dependency_overrides[get_db] = session
            app.dependency_overrides[get_current_user] = lambda: user
            app.dependency_overrides[get_registry_user] = lambda: user
            monkeypatch.setattr("services.registry_fork._check_rate_limit", AsyncMock())
            monkeypatch.setattr(routes, "emit_registry_event", MagicMock())
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                created = await client.post(f"/api/v1/agents/{source.id}/fork", json={"name": "Variant"})
                assert created.status_code == 200, created.text
                payload = created.json()
                assert payload["status"] == "draft"
                assert payload["forked_from"]["id"] == str(source.id)
                assert payload["forked_from"]["version"] == "1.2.3"
                before = await client.get(f"/api/v1/agents/{source.id}/forks")
                assert before.json()["total"] == 0
                # A snapshot frozen by an older builder must not read as an edit.
                source.latest_version.yaml_snapshot = "# legacy\nversion: 1.2.3\nprompt: Prompt\n"
                await db.commit()
                unchanged = await client.get(f"/api/v1/agents/{payload['id']}/fork-diff")
                assert unchanged.status_code == 200, unchanged.text
                assert unchanged.json()["unchanged"] is True
                assert unchanged.json()["diff"] == ""
                outsider = SimpleNamespace(id=uuid.uuid4(), role=UserRole.user)
                app.dependency_overrides[get_registry_user] = lambda: outsider
                assert (await client.get(f"/api/v1/agents/{payload['id']}/fork-diff")).status_code == 404
                app.dependency_overrides[get_registry_user] = lambda: user
                child = await db.get(Agent, uuid.UUID(payload["id"]))
                child.latest_version.status = AgentStatus.approved
                await db.commit()
                after = await client.get(f"/api/v1/agents/{source.id}/forks")
                assert after.status_code == 200, after.text
                assert after.json()["total"] == 1
                assert len(after.json()["items"]) == 1
                assert (await client.get(f"/api/v1/agents/{source.id}")).json()["fork_count"] == 1
                child.latest_version.prompt = "An edited fork prompt"
                child.latest_version.yaml_snapshot = None
                await db.commit()
                changed = await client.get(f"/api/v1/agents/{child.id}/fork-diff?version=1.2.3")
                assert changed.status_code == 200, changed.text
                assert "+prompt: An edited fork prompt" in changed.json()["diff"]
                assert changed.json()["unchanged"] is False
                source.is_private = True
                await db.commit()
                hidden = await client.get(f"/api/v1/agents/{child.id}/fork-diff")
                assert hidden.status_code == 404
                assert "alice/source" not in hidden.text
                source.is_private = False
                child.is_private = True
                await db.commit()
                assert (await client.get(f"/api/v1/agents/{source.id}/forks")).json()["total"] == 0
                assert (await client.get(f"/api/v1/agents/{source.id}")).json()["fork_count"] == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_anonymous_post_fork_requires_authentication():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: MagicMock()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/agents/{uuid.uuid4()}/fork", json={})
    assert response.status_code == 401
