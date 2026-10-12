# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""All component versions fork as independent drafts with identical content."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from models.mcp import ListingStatus
from models.user import User
from services import registry_fork as forks
from services.agent_lock import content_digest

CONTENT = {
    "mcp": {
        "transport": "stdio",
        "command": "npx",
        "args": ["example"],
        "mcp_validated": True,
        "tools_schema": {"tools": []},
    },
    "skill": {
        "task_type": "review",
        "delivery_mode": "registry_direct",
        "skill_md_content": "# Review",
        "validated": True,
    },
    "hook": {"event": "PreToolUse", "handler_type": "command", "handler_config": {"command": "echo ok"}},
    "prompt": {"category": "review", "template": "Hello {name}", "variables": ["name"]},
    "sandbox": {"runtime_type": "docker", "image": "python:3", "validated_at": datetime.now(UTC)},
}


@pytest.mark.parametrize("kind", list(forks.COMPONENT_MODELS))
@pytest.mark.asyncio
async def test_component_fork_copies_content_resets_review_state_and_keeps_digest(kind, monkeypatch):
    listing_model, version_model = forks.COMPONENT_MODELS[kind]
    user = SimpleNamespace(id=uuid.uuid4(), username="alice")
    source = listing_model(
        id=uuid.uuid4(),
        name="Original",
        namespace="bob",
        slug="original",
        owner="bob",
        submitted_by=uuid.uuid4(),
        is_private=False,
    )
    base = version_model(
        id=uuid.uuid4(),
        listing_id=source.id,
        version="2.0.0",
        status=ListingStatus.approved,
        description="Documentation",
        changelog="old",
        released_by=source.submitted_by,
        released_at=datetime.now(UTC),
        reviewed_by=uuid.uuid4(),
        reviewed_at=datetime.now(UTC),
        download_count=17,
        supported_harnesses=["kiro"],
        **CONTENT[kind],
    )
    source.latest_version = base
    source.versions = [base]
    monkeypatch.setattr(forks, "_check_rate_limit", AsyncMock())
    monkeypatch.setattr(forks, "identity_exists", AsyncMock(return_value=False))
    monkeypatch.setattr(
        forks,
        "resolve_publish_target",
        AsyncMock(
            return_value=SimpleNamespace(
                namespace="alice", slug="copy", owner="alice", team_id=None, visibility="public"
            )
        ),
    )
    db = MagicMock()
    db.flush = AsyncMock(
        side_effect=lambda: [
            setattr(call.args[0], "id", call.args[0].id or uuid.uuid4()) for call in db.add.call_args_list
        ]
    )
    result = await forks.fork_component(db, kind, source, forks.ForkRequestSpec(name="Copy"), current_user=user)
    fork, draft = result.entity, result.version
    assert fork.id != source.id and fork.forked_from_id == source.id and fork.forked_from_version_id == base.id
    assert fork.bundle_id is None and fork.unique_agents == 0 and fork.submitted_by == user.id
    assert draft.id != base.id and draft.status == ListingStatus.draft and draft.released_by == user.id
    assert draft.changelog is None and draft.reviewed_by is None and draft.rejection_reason is None
    assert draft.download_count is None or draft.download_count == 0  # ORM default fills on insert
    assert content_digest(kind, base) == content_digest(kind, draft)
    if kind == "mcp":
        assert draft.mcp_validated is False and draft.tools_schema == base.tools_schema
    elif kind == "skill":
        assert draft.validated is False
    elif kind == "sandbox":
        assert draft.validated_at is None
    assert source.latest_version.status == ListingStatus.approved


@pytest.mark.parametrize("kind", list(forks.COMPONENT_MODELS))
@pytest.mark.asyncio
async def test_component_fork_rejects_pending_source_even_with_approved_older_version(kind, monkeypatch):
    source = SimpleNamespace(status=ListingStatus.pending)
    monkeypatch.setattr(forks, "_check_rate_limit", AsyncMock())
    with pytest.raises(HTTPException) as error:
        await forks.fork_component(
            MagicMock(), kind, source, forks.ForkRequestSpec(), current_user=SimpleNamespace(id=uuid.uuid4())
        )
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_concurrent_identity_insert_maps_to_conflict_not_server_error(monkeypatch):
    """The unique index, not the pre-check, decides a same-name race; it must surface as 409."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from models.base import Base

    listing_model, version_model = forks.COMPONENT_MODELS["skill"]
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            # A real row: the conflict path must reload the rolled-back actor.
            user = User(id=uuid.uuid4(), username="alice", email="alice@example.com", name="Alice")
            source = listing_model(
                name="Original", namespace="bob", slug="original", owner="bob", submitted_by=uuid.uuid4()
            )
            winner = listing_model(name="Copy", namespace="alice", slug="copy", owner="alice", submitted_by=user.id)
            db.add_all([user, source, winner])
            await db.flush()
            base = version_model(
                listing_id=source.id,
                version="2.0.0",
                status=ListingStatus.approved,
                description="Documentation",
                released_by=source.submitted_by,
                released_at=datetime.now(UTC),
                supported_harnesses=["kiro"],
                **CONTENT["skill"],
            )
            db.add(base)
            await db.commit()
            await db.refresh(source, ["versions", "latest_version"])
            source.latest_version_id = base.id
            await db.commit()
            await db.refresh(source, ["versions", "latest_version"])
            monkeypatch.setattr(forks, "_check_rate_limit", AsyncMock())
            # Simulate the other request committing after this request's pre-check.
            monkeypatch.setattr(forks, "identity_exists", AsyncMock(return_value=False))
            target = SimpleNamespace(namespace="alice", slug="copy", owner="alice", team_id=None, visibility="public")
            monkeypatch.setattr(forks, "resolve_publish_target", AsyncMock(return_value=target))
            with pytest.raises(HTTPException) as error:
                await forks.fork_component(db, "skill", source, forks.ForkRequestSpec(name="Copy"), current_user=user)
            assert error.value.status_code == 409
            assert "alice/copy" in error.value.detail
            # Attributes must be loaded, not expired by the rollback (audit middleware reads them).
            assert "id" in user.__dict__
    finally:
        await engine.dispose()
