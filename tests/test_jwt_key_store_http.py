# SPDX-FileCopyrightText: 2026 SrihariLegend <sriharilegend23@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""HTTP behavior when JWT key storage is unavailable versus invalid tokens."""

from __future__ import annotations

import base64
import io
import json
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import jwt as pyjwt
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from loguru import logger

import services.crypto as crypto
from services.crypto import KeyManager, init_key_manager

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

AuthApp = tuple[FastAPI, KeyManager, Path]


def _replace_header(token: str, **changes: str) -> str:
    header, payload, signature = token.split(".")
    header_data = json.loads(base64.urlsafe_b64decode(header + "=" * (-len(header) % 4)))
    header_data.update(changes)
    encoded_header = base64.urlsafe_b64encode(json.dumps(header_data, separators=(",", ":")).encode()).rstrip(b"=")
    return f"{encoded_header.decode()}.{payload}.{signature}"


@pytest.fixture
def auth_app(tmp_path: Path) -> Iterator[AuthApp]:
    import services.crypto as crypto
    from api.deps import get_db
    from api.ratelimit import limiter
    from main import app

    previous_manager = crypto._key_manager
    previous_overrides = app.dependency_overrides.copy()
    previous_limiter_enabled = limiter.enabled
    limiter.enabled = False
    key_dir = tmp_path / "keys"
    manager = init_key_manager(key_dir=str(key_dir))

    async def empty_database() -> AsyncIterator[None]:
        yield None

    app.dependency_overrides[get_db] = empty_database
    try:
        yield app, manager, key_dir
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        crypto._key_manager = previous_manager
        limiter.enabled = previous_limiter_enabled


async def _get_whoami(app: FastAPI, token: str) -> Response:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/api/v1/auth/whoami", headers={"Authorization": f"Bearer {token}"})


async def _refresh_token(app: FastAPI, token: str) -> Response:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/api/v1/auth/token/refresh", json={"refresh_token": token})


async def _graphql_request(app: FastAPI, token: str) -> Response:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(
            "/api/v1/graphql",
            json={"query": "{ health }"},
            headers={"Authorization": f"Bearer {token}"},
        )


def _access_token(manager: KeyManager) -> str:
    return manager.sign_token(
        {
            "sub": str(uuid.uuid4()),
            "type": "access",
            "exp": int(time.time()) + 60,
        }
    )


def _refresh_jwt(manager: KeyManager) -> str:
    return manager.sign_token(
        {
            "sub": str(uuid.uuid4()),
            "jti": str(uuid.uuid4()),
            "type": "refresh",
            "exp": int(time.time()) + 60,
        }
    )


@pytest.mark.asyncio
async def test_key_store_failure_returns_generic_503_without_secret_material(auth_app: AuthApp) -> None:
    app, manager, key_dir = auth_app
    token = _access_token(manager)
    private_pem = (key_dir / "signing.pem").read_text()
    private_key_payload = "".join(private_pem.splitlines()[1:-1])
    (key_dir / "signing.pem").unlink()
    captured_logs = io.StringIO()
    sink_id = logger.add(captured_logs, level="ERROR")
    try:
        response = await _get_whoami(app, token)
    finally:
        logger.remove(sink_id)

    assert response.status_code == 503
    assert response.json() == {"detail": "Authentication service temporarily unavailable"}
    assert token not in response.text
    assert private_pem not in response.text
    assert private_key_payload not in response.text
    assert token not in captured_logs.getvalue()
    assert private_pem not in captured_logs.getvalue()
    assert private_key_payload not in captured_logs.getvalue()
    assert "PRIVATE KEY" not in captured_logs.getvalue()


@pytest.mark.asyncio
async def test_refresh_key_store_failure_returns_service_unavailable(auth_app: AuthApp) -> None:
    app, manager, key_dir = auth_app
    token = _refresh_jwt(manager)
    (key_dir / "signing.pem").unlink()

    response = await _refresh_token(app, token)

    assert response.status_code == 503
    assert response.json() == {"detail": "Authentication service temporarily unavailable"}
    assert token not in response.text


@pytest.mark.asyncio
async def test_key_directory_listing_failure_returns_service_unavailable(
    auth_app: AuthApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, manager, key_dir = auth_app
    token = _access_token(manager)
    peer = KeyManager(key_dir=str(key_dir))
    peer.initialize()
    peer.rotate_key()
    real_scandir = crypto.os.scandir

    def deny_key_directory_listing(path: str | Path):
        if Path(path) == key_dir:
            raise PermissionError("test-only key directory listing failure")
        return real_scandir(path)

    monkeypatch.setattr(crypto.os, "scandir", deny_key_directory_listing)
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token_response = await client.get("/api/v1/auth/whoami", headers={"Authorization": f"Bearer {token}"})
        jwks_response = await client.get("/api/v1/auth/.well-known/jwks.json")

    responses = (token_response, jwks_response)
    assert [response.status_code for response in responses] == [503, 503]
    for response in responses:
        assert response.json() == {"detail": "Authentication service temporarily unavailable"}


@pytest.mark.asyncio
async def test_graphql_key_store_failure_returns_service_unavailable(auth_app: AuthApp) -> None:
    app, manager, key_dir = auth_app
    token = _access_token(manager)
    (key_dir / "signing.pem").unlink()

    response = await _graphql_request(app, token)

    assert response.status_code == 503
    assert response.json() == {"detail": "Authentication service temporarily unavailable"}
    assert token not in response.text


@pytest.mark.parametrize("invalid_token_kind", ["tampered", "unknown-kid", "algorithm-confusion"])
@pytest.mark.asyncio
async def test_invalid_tokens_remain_authentication_rejections(auth_app: AuthApp, invalid_token_kind: str) -> None:
    app, manager, _key_dir = auth_app
    token = _access_token(manager)
    if invalid_token_kind == "tampered":
        header, payload, signature = token.split(".")
        signature = ("A" if signature[0] != "A" else "B") + signature[1:]
        token = f"{header}.{payload}.{signature}"
    elif invalid_token_kind == "unknown-kid":
        token = pyjwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "type": "access",
                "exp": int(time.time()) + 60,
            },
            manager.get_private_key(),
            algorithm=manager.algorithm,
            headers={"kid": "unknown-key-id"},
        )
    else:
        token = _replace_header(token, alg="RS256")

    response = await _get_whoami(app, token)

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or expired token"}
