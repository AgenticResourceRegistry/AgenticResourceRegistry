# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError
from starlette.requests import Request

from api.routes.agent_shares import (
    _load_manifest,
    _token_hash,
    create_agent_share,
    get_agent_share,
    revoke_agent_share,
)
from models.agent_share import AgentShareManifest
from models.user import UserRole
from schemas.agent_share import AgentShareCreateRequest

TOKEN = "A" * 43


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [], "client": ("127.0.0.1", 1)})


def _user():
    return SimpleNamespace(
        id=uuid.uuid4(),
        username="alice",
        email="alice@example.test",
        role=UserRole.user,
    )


def test_share_schema_enforces_expiry_and_unique_versions():
    agent_id = uuid.uuid4()
    with pytest.raises(ValidationError):
        AgentShareCreateRequest(
            expires_in_days=31,
            items=[{"agent_id": agent_id, "version": "1.0.0"}],
        )
    with pytest.raises(ValidationError):
        AgentShareCreateRequest(
            items=[
                {"agent_id": agent_id, "version": "1.0.0"},
                {"agent_id": agent_id, "version": "1.0.0"},
            ]
        )


def test_public_token_is_stored_as_a_one_way_hash():
    assert _token_hash(TOKEN) != TOKEN
    assert len(_token_hash(TOKEN)) == 64
    assert _token_hash(TOKEN) == _token_hash(TOKEN)


@pytest.mark.asyncio
async def test_invalid_token_is_rejected_before_database_lookup():
    db = AsyncMock()
    with pytest.raises(HTTPException) as error:
        await _load_manifest("../../injected", db)
    assert error.value.status_code == 404
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_hashes_token_and_pins_approved_version():
    current_user = _user()
    agent = SimpleNamespace(id=uuid.uuid4())
    version = SimpleNamespace(id=uuid.uuid4(), version="1.2.3")
    result = MagicMock()
    result.scalar_one_or_none.return_value = version
    db = AsyncMock()
    db.execute.return_value = result
    db.add = MagicMock()

    request = AgentShareCreateRequest(
        expires_in_days=7,
        items=[{"agent_id": agent.id, "version": version.version}],
    )
    with (
        patch("api.routes.agent_shares._load_agent", new=AsyncMock(return_value=agent)),
        patch("api.routes.agent_shares.secrets.token_urlsafe", return_value=TOKEN),
        patch("api.routes.agent_shares.ds.get_sync", return_value="https://app.example.test"),
    ):
        response = await create_agent_share.__wrapped__(request, _request(), db, current_user)

    manifest = next(call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], AgentShareManifest))
    assert manifest.token_hash == _token_hash(TOKEN)
    assert TOKEN not in manifest.token_hash
    assert response.url == f"https://app.example.test/shares/agents/{TOKEN}"
    assert response.expires_at - response.created_at == timedelta(days=7)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_recipient_cannot_enumerate_inaccessible_manifest_items():
    current_user = _user()
    manifest = SimpleNamespace(
        title=None,
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=1),
        created_by=uuid.uuid4(),
        items=[SimpleNamespace(agent_id=uuid.uuid4(), agent_version_id=uuid.uuid4(), position=0)],
    )
    creator = SimpleNamespace(username="creator")
    db = AsyncMock()
    db.get.return_value = creator
    http_response = Response()
    with (
        patch("api.routes.agent_shares._load_manifest", new=AsyncMock(return_value=manifest)),
        patch("api.routes.agent_shares._load_agent", new=AsyncMock(return_value=None)),
    ):
        response = await get_agent_share.__wrapped__(TOKEN, _request(), http_response, db, current_user)

    assert response.created_by_username == "creator"
    assert response.items == []
    assert http_response.headers["cache-control"] == "no-store"
    assert response.unavailable_count == 1


def _manifest(created_by, *, expires_in=timedelta(days=1), revoked=False, items=()):
    now = datetime.now(UTC)
    return SimpleNamespace(
        created_by=created_by,
        title=None,
        created_at=now,
        expires_at=now + expires_in,
        revoked_at=now if revoked else None,
        items=list(items),
    )


def _db_returning(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    db = AsyncMock()
    db.execute.return_value = result
    return db


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{"expires_in": timedelta(seconds=-1)}, {"revoked": True}])
async def test_expired_or_revoked_share_is_gone(kwargs):
    db = _db_returning(_manifest(uuid.uuid4(), **kwargs))
    with pytest.raises(HTTPException) as error:
        await _load_manifest(TOKEN, db)
    assert error.value.status_code == 410


@pytest.mark.asyncio
async def test_unknown_share_is_not_found():
    with pytest.raises(HTTPException) as error:
        await _load_manifest(TOKEN, _db_returning(None))
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_revoke_authorization():
    owner = _user()
    manifest = _manifest(owner.id)

    stranger = _user()
    with patch("api.routes.agent_shares._load_manifest", new=AsyncMock(return_value=manifest)):
        with pytest.raises(HTTPException) as error:
            await revoke_agent_share.__wrapped__(TOKEN, _request(), AsyncMock(), stranger)
        assert error.value.status_code == 403

        admin = _user()
        admin.role = UserRole.admin
        assert (await revoke_agent_share.__wrapped__(TOKEN, _request(), AsyncMock(), admin)).revoked
        assert manifest.revoked_at is not None


@pytest.mark.asyncio
async def test_revoke_hides_expired_state_from_non_creator():
    manifest = _manifest(uuid.uuid4(), expires_in=timedelta(seconds=-1))
    with (
        patch("api.routes.agent_shares._load_manifest", new=AsyncMock(return_value=manifest)),
        pytest.raises(HTTPException) as error,
    ):
        await revoke_agent_share.__wrapped__(TOKEN, _request(), AsyncMock(), _user())
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_get_skips_items_without_approved_version():
    manifest = _manifest(
        uuid.uuid4(),
        items=[SimpleNamespace(agent_id=uuid.uuid4(), agent_version_id=uuid.uuid4(), position=0)],
    )
    db = _db_returning(None)
    db.get.return_value = SimpleNamespace(username="creator")
    with (
        patch("api.routes.agent_shares._load_manifest", new=AsyncMock(return_value=manifest)),
        patch("api.routes.agent_shares._load_agent", new=AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4()))),
    ):
        response = await get_agent_share.__wrapped__(TOKEN, _request(), Response(), db, _user())
    assert response.items == []
    assert response.unavailable_count == 1


@pytest.mark.asyncio
async def test_create_rejects_invisible_agent():
    request = AgentShareCreateRequest(items=[{"agent_id": uuid.uuid4(), "version": "1.0.0"}])
    db = AsyncMock()
    with (
        patch("api.routes.agent_shares._load_agent", new=AsyncMock(return_value=None)),
        pytest.raises(HTTPException) as error,
    ):
        await create_agent_share.__wrapped__(request, _request(), db, _user())
    assert error.value.status_code == 404
    db.commit.assert_not_awaited()
