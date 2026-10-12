# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Exercise all five fork routers through HTTP against real ORM rows."""

import uuid
from datetime import UTC, datetime
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.deps import get_current_user, get_db, get_registry_user
from api.routes import component_forks, hook, mcp, prompt, review, sandbox, skill
from models.base import Base
from models.mcp import ListingStatus
from models.user import UserRole
from services import registry_fork
from services.agent_lock import content_digest
from services.teamspace import ReviewScope

CONTENT = {
    "mcp": {"transport": "stdio", "command": "npx", "args": ["example"], "mcp_validated": True},
    "skill": {
        "task_type": "review",
        "delivery_mode": "registry_direct",
        "skill_md_content": "# Review",
        "validated": True,
    },
    "hook": {"event": "PreToolUse", "handler_type": "command", "handler_config": {"command": "echo ok"}},
    "prompt": {"category": "review", "template": "Hello {name}"},
    "sandbox": {"runtime_type": "docker", "image": "python:3", "validated_at": datetime.now(UTC)},
}


def _as_user(user):
    return user


ROUTES = {
    "mcp": (mcp.router, "mcps"),
    "skill": (skill.router, "skills"),
    "hook": (hook.router, "hooks"),
    "prompt": (prompt.router, "prompts"),
    "sandbox": (sandbox.router, "sandboxes"),
}


@pytest.mark.asyncio
async def test_five_component_forks_from_approved_releases(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(registry_fork, "_check_rate_limit", AsyncMock())
    monkeypatch.setattr(component_forks, "emit_registry_event", MagicMock())
    monkeypatch.setattr(component_forks.dynamic_settings, "get_bool", AsyncMock(return_value=True))
    try:
        async with sessions() as db:
            actor = SimpleNamespace(id=uuid.uuid4(), username="alice", email="alice@example.com", role=UserRole.user)
            app = FastAPI()
            for router, _path in ROUTES.values():
                app.include_router(router)

            async def session():
                yield db

            app.dependency_overrides[get_db] = session
            app.dependency_overrides[get_current_user] = lambda: actor
            app.dependency_overrides[get_registry_user] = lambda: actor
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                for kind, (_router, path) in ROUTES.items():
                    listing_model, version_model = registry_fork.COMPONENT_MODELS[kind]
                    source = listing_model(
                        name="Source",
                        namespace="bob",
                        slug="source",
                        owner="bob",
                        submitted_by=actor.id,
                        is_private=False,
                        **({"category": "tools"} if kind == "mcp" else {}),
                    )
                    db.add(source)
                    await db.flush()
                    base = version_model(
                        listing_id=source.id,
                        version="2.0.0",
                        description="Documentation",
                        status=ListingStatus.approved,
                        released_by=actor.id,
                        released_at=datetime.now(UTC),
                        supported_harnesses=["kiro"],
                        **CONTENT[kind],
                    )
                    db.add(base)
                    await db.flush()
                    source.latest_version_id = base.id
                    await db.commit()
                    url = f"/api/v1/{path}/{source.id}"
                    component_forks.dynamic_settings.get_bool.return_value = False
                    assert (await client.post(url + "/fork", json={})).status_code == 403
                    assert (await client.get(url + "/forks")).status_code == 403
                    assert (await client.get(url + "/fork-diff")).status_code == 403
                    component_forks.dynamic_settings.get_bool.return_value = True
                    missing_version = await client.post(url + "/fork", json={"name": "Other", "version": "9.9.9"})
                    assert missing_version.status_code == 404, (kind, missing_version.text)
                    base.status = ListingStatus.pending
                    await db.commit()
                    pending = await client.post(url + "/fork", json={"name": "Other"})
                    assert pending.status_code == 409, (kind, pending.text)
                    base.status = ListingStatus.approved
                    await db.commit()
                    created = await client.post(url + "/fork", json={"name": "Variant"})
                    assert created.status_code == 200, (kind, created.text)
                    data = created.json()
                    assert data["status"] == "draft" and data["forked_from"]["id"] == str(source.id)
                    fork = await db.get(listing_model, uuid.UUID(data["id"]))
                    draft = await db.get(version_model, fork.latest_version_id)
                    assert content_digest(kind, base) == content_digest(kind, draft)
                    same = await client.get(f"/api/v1/{path}/{fork.id}/fork-diff")
                    assert same.status_code == 200, (kind, same.text)
                    assert same.json()["unchanged"] is True and same.json()["diff"] == ""
                    if kind == "skill":
                        draft.script_content = "private-script-not-for-public-diff"
                        await db.commit()
                        script_change = await client.get(f"/api/v1/skills/{fork.id}/fork-diff")
                        assert script_change.status_code == 200 and script_change.json()["unchanged"] is False
                        assert "additional_install_content_digest" in script_change.json()["diff"]
                        assert "private-script-not-for-public-diff" not in script_change.text
                        draft.script_content = None
                        await db.commit()
                    own = await client.get(f"/api/v1/{path}/my")
                    assert own.status_code == 200 and any(row["id"] == data["id"] for row in own.json())
                    assert next(row for row in own.json() if row["id"] == data["id"])["forked_from"]["id"] == str(
                        source.id
                    )
                    if kind == "skill":
                        edited = await client.put(
                            f"/api/v1/skills/{fork.id}/draft", json={"description": "Fork customized"}
                        )
                        assert edited.status_code == 200, edited.text
                        assert edited.json()["forked_from"]["id"] == str(source.id)
                        changed = await client.get(f"/api/v1/skills/{fork.id}/fork-diff?version=2.0.0")
                        assert changed.status_code == 200 and changed.json()["unchanged"] is False
                        assert "Fork customized" in changed.json()["diff"]
                        monkeypatch.setattr(skill, "publish_auto_approves_for_entity", AsyncMock(return_value=False))
                        monkeypatch.setattr(skill.inbox, "on_publish", AsyncMock())
                        submitted = await client.post(f"/api/v1/skills/{fork.id}/submit")
                        assert submitted.status_code == 200, submitted.text
                        assert submitted.json()["status"] == "pending"
                        assert submitted.json()["forked_from"]["id"] == str(source.id)
                        monkeypatch.setattr(
                            "api.routes.config.derive_endpoints", AsyncMock(return_value={"api": "https://api.test"})
                        )
                        monkeypatch.setattr(
                            "services.skill_config_generator.generate_skill_config",
                            MagicMock(return_value={"skill": "ok"}),
                        )
                        installed = await client.post(f"/api/v1/skills/{fork.id}/install", json={"harness": "pi"})
                        assert installed.status_code == 200, installed.text
                        stranger = SimpleNamespace(
                            id=uuid.uuid4(), username="eve", email="eve@example.com", role=UserRole.user
                        )
                        app.dependency_overrides[get_registry_user] = partial(_as_user, stranger)
                        forbidden = await client.post(f"/api/v1/skills/{fork.id}/install", json={"harness": "pi"})
                        assert forbidden.status_code == 404, forbidden.text
                        app.dependency_overrides[get_registry_user] = lambda: actor
                    if kind == "mcp":
                        edited = await client.put(
                            f"/api/v1/mcps/{fork.id}/draft", json={"description": "Fork customized"}
                        )
                        assert edited.status_code == 200, edited.text
                        assert edited.json()["forked_from"]["id"] == str(source.id)
                        monkeypatch.setattr(mcp, "publish_auto_approves_for_entity", AsyncMock(return_value=False))
                        monkeypatch.setattr(mcp.inbox, "on_publish", AsyncMock())
                        submitted = await client.post(f"/api/v1/mcps/{fork.id}/submit")
                        assert submitted.status_code == 200, submitted.text
                        assert submitted.json()["status"] == "pending"
                        assert submitted.json()["forked_from"]["id"] == str(source.id)
                    if kind in {"skill", "mcp"}:
                        reviewer = SimpleNamespace(id=uuid.uuid4(), role=UserRole.admin)
                        scope = ReviewScope(is_admin=True, is_global_reviewer=True, team_ids=frozenset())
                        queue = await review._query_pending_components(db, scope, kind, current_user=reviewer)
                        assert next(row for row in queue if row["id"] == str(fork.id))["forked_from"]["id"] == source.id
                        review_detail = await review.get_review(str(fork.id), db, reviewer)
                        assert review_detail["forked_from"]["id"] == source.id
                    before = await client.get(url + "/forks")
                    assert before.status_code == 200 and before.json()["total"] == 0
                    draft.status = ListingStatus.approved
                    await db.commit()
                    after = await client.get(url + "/forks")
                    assert after.status_code == 200 and after.json()["total"] == 1
                    assert after.json()["items"][0]["id"] == data["id"]
                    detail = await client.get(url)
                    assert detail.status_code == 200 and detail.json()["fork_count"] == 1
                    collection = await client.get(f"/api/v1/{path}")
                    assert collection.status_code == 200
                    assert next(row for row in collection.json() if row["id"] == str(source.id))["fork_count"] == 1
                    fork.is_private = True
                    await db.commit()
                    hidden = await client.get(url + "/forks")
                    assert hidden.status_code == 200 and hidden.json()["total"] == 0
                    fork.is_private = False
                    source.is_private = True
                    await db.commit()
                    blocked = await client.post(url + "/fork", json={"name": "Public copy"})
                    assert blocked.status_code == 409, (kind, blocked.text)
                    outsider = SimpleNamespace(
                        id=uuid.uuid4(), username="eve", email="eve@example.com", role=UserRole.user
                    )
                    app.dependency_overrides[get_registry_user] = partial(_as_user, outsider)
                    redacted = await client.get(f"/api/v1/{path}/{fork.id}")
                    assert redacted.status_code == 200, (kind, redacted.text)
                    assert redacted.json()["forked_from"] == {
                        "available": False,
                        "id": None,
                        "type": None,
                        "namespace": None,
                        "slug": None,
                        "qualified_name": None,
                        "version": None,
                        "forked_at": redacted.json()["forked_from"]["forked_at"],
                    }
                    assert "forked_from_ref" not in redacted.text
                    private_diff = await client.get(f"/api/v1/{path}/{fork.id}/fork-diff")
                    assert private_diff.status_code == 404
                    assert "bob/source" not in private_diff.text
                    assert (await client.get(url)).status_code == 404
                    app.dependency_overrides[get_registry_user] = lambda: actor
    finally:
        await engine.dispose()
