# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Unvalidated Authorization headers must not create new rate-limit buckets."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Depends, FastAPI, Header, Request
from fastapi.testclient import TestClient
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request as StarletteRequest

from api.deps import get_db, require_password_auth
from api.ratelimit import _get_rate_limit_key
from api.ratelimit import limiter as auth_limiter
from api.routes import auth


def _request(token=None, user=None, legacy_user=None, xff=None):
    headers = []
    if token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    request = StarletteRequest(
        {
            "type": "http",
            "method": "POST",
            "path": "/login",
            "headers": headers,
            "client": ("203.0.113.10", 1234),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )
    if user is not None:
        request.state.current_user = SimpleNamespace(id=user)
    if legacy_user is not None:
        request.state.user = SimpleNamespace(id=legacy_user)
    return request


def test_only_validated_current_user_can_override_ip():
    with patch("api.ratelimit.ds.get_sync", return_value=""):
        assert _get_rate_limit_key(_request()) == "ip:203.0.113.10"
        assert _get_rate_limit_key(_request(token="bogus", legacy_user="untrusted")) == "ip:203.0.113.10"
        assert _get_rate_limit_key(_request(token="another", xff="198.51.100.9")) == "ip:203.0.113.10"
        assert _get_rate_limit_key(_request(token="bogus", user="verified")) == "user:verified"


@pytest.mark.parametrize(
    "tokens",
    [
        [None] * 6,
        [f"bogus-{i}" for i in range(6)],
        ["same-bogus"] * 6,
    ],
    ids=["no-header", "rotating-bogus-bearers", "same-bogus-bearer"],
)
def test_login_asgi_limiter_groups_unvalidated_requests_by_ip(tokens):
    # A real FastAPI route with SlowAPI's decorator and exception handler. The
    # in-memory limiter is isolated per test, not the production Redis limiter.
    limiter = Limiter(key_func=_get_rate_limit_key, storage_uri="memory://")
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    @app.post("/login")
    @limiter.limit("5/minute")
    async def login(request: Request):
        return {"ok": True}

    with patch("api.ratelimit.ds.get_sync", return_value=""), TestClient(app) as client:
        for i, token in enumerate(tokens):
            headers = {} if token is None else {"Authorization": f"Bearer {token}"}
            assert client.post("/login", headers=headers).status_code == (429 if i == 5 else 200)


@pytest.mark.parametrize(
    "tokens",
    [[f"bogus-{i}" for i in range(6)], [None] * 6],
    ids=["rotating-bogus-bearers", "no-header"],
)
def test_real_login_limits_failed_attempts_by_ip(tokens, monkeypatch):
    # auth.login is already decorated with the production limiter. Swap only its
    # storage/strategy, and restore all global limiter state after this test.
    storage = MemoryStorage()
    monkeypatch.setattr(auth_limiter, "_storage", storage)
    monkeypatch.setattr(auth_limiter, "_limiter", FixedWindowRateLimiter(storage))
    monkeypatch.setattr(auth_limiter, "enabled", True)
    emit_event = AsyncMock()
    monkeypatch.setattr(auth, "emit_security_event", emit_event)

    db = MagicMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None))
    app = FastAPI()
    app.include_router(auth.router)
    app.state.limiter = auth_limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.dependency_overrides[require_password_auth] = lambda: None
    app.dependency_overrides[get_db] = lambda: db

    with patch("api.ratelimit.ds.get_sync", return_value=""), TestClient(app) as client:
        for i, token in enumerate(tokens):
            headers = {} if token is None else {"Authorization": f"Bearer {token}"}
            response = client.post(
                "/api/v1/auth/login",
                json={"email": "absent@example.com", "password": "WrongPassword1!"},
                headers=headers,
            )
            assert response.status_code == (429 if i == 5 else 401)
    assert db.execute.await_count == 5
    assert emit_event.await_count == 5


def test_validated_user_has_separate_asgi_bucket():
    limiter = Limiter(key_func=_get_rate_limit_key, storage_uri="memory://")
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    async def mocked_credentials(request: Request, authorization: str | None = Header(None)):
        # Simulate authentication setting current_user only after validation.
        if authorization == "Bearer valid":
            request.state.current_user = SimpleNamespace(id="verified-user")

    @app.post("/login", dependencies=[Depends(mocked_credentials)])
    @limiter.limit("5/minute")
    async def login(request: Request):
        return {"ok": True}

    with patch("api.ratelimit.ds.get_sync", return_value=""), TestClient(app) as client:
        for i in range(6):
            assert client.post("/login", headers={"Authorization": f"Bearer invalid-{i}"}).status_code == (
                429 if i == 5 else 200
            )
        assert client.post("/login", headers={"Authorization": "Bearer valid"}).status_code == 200
        assert client.post("/login").status_code == 429
        for _ in range(4):
            assert client.post("/login", headers={"Authorization": "Bearer valid"}).status_code == 200
        assert client.post("/login", headers={"Authorization": "Bearer valid"}).status_code == 429
