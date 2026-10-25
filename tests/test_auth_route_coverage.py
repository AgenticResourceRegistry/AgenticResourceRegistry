# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Focused route coverage for authentication lifecycle and security boundaries."""

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import jwt as pyjwt
import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError

from api.deps import get_current_user, get_db, require_password_auth
from api.ratelimit import limiter
from api.routes import auth
from models.user import UserRole
from schemas.auth import (
    ChangePasswordRequest,
    InitRequest,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    RevokeRequest,
    TokenRequest,
    UsernameUpdateRequest,
)
from services.security_events import EventType

USER_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _disable_rate_limits():
    enabled = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = enabled


def _user(**overrides):
    values = {
        "id": USER_ID,
        "email": "member@example.test",
        "username": "member",
        "name": "Test Member",
        "role": UserRole.user,
        "avatar_url": None,
        "created_at": NOW,
        "auth_provider": "local",
        "sso_subject_id": None,
        "_groups": ["engineering"],
    }
    values.update(overrides)
    user = SimpleNamespace(**values)
    user.verify_password = MagicMock(return_value=True)
    user.set_password = MagicMock()
    return user


def _result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _db(value=None):
    db = MagicMock()
    db.scalar = AsyncMock(return_value=0)
    db.execute = AsyncMock(return_value=_result(value))
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    db.add = MagicMock()
    db.add_all = MagicMock()
    return db


def _request(*, query=None, session=None, headers=None, host="127.0.0.1"):
    return SimpleNamespace(
        client=SimpleNamespace(host=host) if host else None,
        headers=headers or {"user-agent": "route-test"},
        query_params=query or {},
        session=session if session is not None else {},
    )


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.setex = AsyncMock(side_effect=self._setex)
        self.get = AsyncMock(side_effect=self._get)
        self.getdel = AsyncMock(side_effect=self._getdel)
        self.delete = AsyncMock(side_effect=self._delete)

    async def _setex(self, key, _ttl, value):
        self.values[key] = value

    async def _get(self, key):
        return self.values.get(key)

    async def _getdel(self, key):
        return self.values.pop(key, None)

    async def _delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)


def _route_app(db, user=None):
    app = FastAPI()
    app.include_router(auth.router)

    async def db_override():
        yield db

    async def password_auth_override():
        return None

    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[require_password_auth] = password_auth_override
    if user is not None:

        async def user_override():
            return user

        app.dependency_overrides[get_current_user] = user_override
    return app


class TokenHttpResponse:
    def __init__(self, status_code=200, payload=None, text="response"):
        self.status_code = status_code
        self.payload = payload
        self.text = text

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class TokenHttpClient:
    outcome = None
    calls = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, url, **kwargs):
        type(self).calls.append((url, kwargs))
        if isinstance(type(self).outcome, Exception):
            raise type(self).outcome
        return type(self).outcome


@pytest.fixture(autouse=True)
def _reset_token_http_client():
    TokenHttpClient.outcome = None
    TokenHttpClient.calls = []
    yield
    TokenHttpClient.outcome = None
    TokenHttpClient.calls = []


def _e2e_session(**overrides):
    session = {
        "session_id": "session_123",
        "provider": "oidc",
        "mode": "e2e",
        "checks": [],
        "token_endpoint": "https://idp.example.test/token",
        "jwks_uri": "https://idp.example.test/jwks",
        "issuer": "https://idp.example.test",
        "nonce": "fixed-nonce",
    }
    session.update(overrides)
    return session


def _patch_e2e(monkeypatch, session):
    finalize = AsyncMock()
    monkeypatch.setattr(auth.sso_diagnostics, "get_session", AsyncMock(return_value=session))
    monkeypatch.setattr(auth.sso_diagnostics, "finalize", finalize)
    monkeypatch.setattr(auth.sso_diagnostics, "render_result_page", lambda **_kwargs: "result")
    monkeypatch.setattr(auth.sso_diagnostics, "render_error_page", lambda *_args: "error")
    monkeypatch.setattr(
        auth.ds,
        "get_sync",
        lambda key, default=None: {
            "oauth.client_id": "test-client",
            "oauth.client_secret": "test-credential",
        }.get(key, default),
    )
    monkeypatch.setattr(auth.httpx, "AsyncClient", TokenHttpClient)
    return finalize


@pytest.mark.asyncio
async def test_oauth_configuration_registers_each_provider(monkeypatch):
    class OAuthRegistry:
        def __init__(self):
            self.calls = []

        def register(self, **kwargs):
            self.calls.append(kwargs)
            setattr(self, kwargs["name"], SimpleNamespace())

    registry = OAuthRegistry()
    settings = {
        "oauth.client_id": "oidc-client",
        "oauth.client_secret": "oidc-credential",
        "oauth.server_metadata_url": "https://idp.example.test/discovery",
        "google.client_id": "google-client",
        "google.client_secret": "google-credential",
        "github.client_id": "github-client",
        "github.client_secret": "github-credential",
        "github.allowed_orgs": "example-org",
    }
    previous = auth.oauth
    monkeypatch.setattr(auth, "OAuth", lambda: registry)
    monkeypatch.setattr(auth.ds, "get_sync", lambda key, default=None: settings.get(key, default))
    try:
        auth.configure_oauth_client()
        assert [call["name"] for call in registry.calls] == ["oidc", "google", "github"]
        github = registry.calls[-1]
        assert github["client_kwargs"]["scope"] == "read:user user:email read:org"
        assert auth.is_oidc_configured()
        assert auth.is_google_oauth_configured()
        assert auth.is_github_oauth_configured()
    finally:
        auth.oauth = previous


def test_common_password_and_avatar_validation_edges(monkeypatch):
    monkeypatch.setattr(auth, "_COMMON_WEAK_PASSWORDS", {"common1!"})
    with pytest.raises(HTTPException, match="too common"):
        auth._validate_password_strength("Common1!")

    oversized_data_url = "x" * (auth._MAX_AVATAR_DATA_URL_LEN + 1)
    too_much_binary = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * auth._MAX_AVATAR_BYTES).decode()
    invalid_values = [
        oversized_data_url,
        "https://example.test/avatar.png",
        "data:image/gif;base64,R0lGODlh",
        "data:image/png;base64,a",
        f"data:image/png;base64,{too_much_binary}",
        f"data:image/png;base64,{base64.b64encode(b'not-png').decode()}",
        f"data:image/webp;base64,{base64.b64encode(b'RIFFxxxxNOPE').decode()}",
    ]
    for value in invalid_values:
        with pytest.raises(HTTPException) as exc:
            auth._validate_avatar_data_url(value)
        assert exc.value.status_code == 422

    webp = base64.b64encode(b"RIFFxxxxWEBPpayload").decode()
    auth._validate_avatar_data_url(f"data:image/webp;base64,{webp}")


@pytest.mark.asyncio
async def test_init_and_bootstrap_rules(monkeypatch):
    monkeypatch.setattr(auth, "generate_unique_username", AsyncMock(return_value="admin-user"))
    monkeypatch.setattr(auth, "_issue_tokens", AsyncMock(return_value=("access", "refresh", 3600)))
    set_password = MagicMock()
    monkeypatch.setattr(auth.User, "set_password", set_password)

    initialized = _db()
    initialized.scalar.return_value = 1
    with pytest.raises(HTTPException) as exc:
        await auth.init_admin(InitRequest(email="admin@example.com", name="Admin"), initialized)
    assert exc.value.status_code == 400

    invalid_name = _db()
    auth.generate_unique_username.side_effect = ValueError("username unavailable")
    with pytest.raises(HTTPException) as exc:
        await auth.init_admin(InitRequest(email="admin@example.com", name="Admin"), invalid_name)
    assert exc.value.status_code == 409

    auth.generate_unique_username.side_effect = None
    conflict = _db()
    conflict.commit.side_effect = IntegrityError("insert", {}, Exception("duplicate"))
    with pytest.raises(HTTPException) as exc:
        await auth.init_admin(InitRequest(email="admin@example.com", name="Admin"), conflict)
    assert exc.value.status_code == 409
    conflict.rollback.assert_awaited_once()

    fresh = _db()

    async def refresh_admin(user):
        user.id = USER_ID
        user.created_at = NOW
        user.avatar_url = None

    fresh.refresh.side_effect = refresh_admin
    response = await auth.init_admin(
        InitRequest(email="admin@example.com", name="Admin", password="Valid1!Password"), fresh
    )
    assert response.user.role == UserRole.admin
    set_password.assert_called_once()

    remote = _request(host="198.51.100.7")
    with pytest.raises(HTTPException) as exc:
        await auth.bootstrap(remote, _db())
    assert exc.value.status_code == 403

    already_bootstrapped = _db()
    already_bootstrapped.scalar.return_value = 2
    with pytest.raises(HTTPException) as exc:
        await auth.bootstrap(_request(), already_bootstrapped)
    assert exc.value.status_code == 400

    bootstrap_conflict = _db()
    bootstrap_conflict.commit.side_effect = IntegrityError("insert", {}, Exception("duplicate"))
    with pytest.raises(HTTPException) as exc:
        await auth.bootstrap(_request(), bootstrap_conflict)
    assert exc.value.status_code == 409
    bootstrap_conflict.rollback.assert_awaited_once()

    bootstrap_db = _db()
    bootstrap_db.refresh.side_effect = refresh_admin
    response = await auth.bootstrap(_request(), bootstrap_db)
    assert response.user.email == "admin@localhost"
    assert response.user.role == UserRole.admin


@pytest.mark.asyncio
async def test_bootstrap_remote_http_response():
    app = _route_app(_db())
    transport = ASGITransport(app=app, client=("198.51.100.7", 443), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="https://test") as client:
        response = await client.post("/api/v1/auth/bootstrap")
    assert response.status_code == 403
    assert response.json() == {"detail": "Bootstrap is only available from localhost"}


@pytest.mark.asyncio
async def test_registration_conflict_and_audit(monkeypatch):
    monkeypatch.setattr(auth.ds, "get_bool", AsyncMock(return_value=True))
    monkeypatch.setattr(auth, "generate_unique_username", AsyncMock(return_value="new-member"))
    monkeypatch.setattr(auth.User, "set_password", MagicMock())
    monkeypatch.setattr(auth, "_issue_tokens", AsyncMock(return_value=("access", "refresh", 3600)))
    emit = AsyncMock()
    monkeypatch.setattr(auth, "emit_security_event", emit)

    conflict = _db()
    conflict.commit.side_effect = IntegrityError("insert", {}, Exception("duplicate"))
    request_data = RegisterRequest(
        email="new@example.com",
        name="New Member",
        username="new-member",
        password="Valid1!Password",
    )
    auth.generate_unique_username.side_effect = ValueError("username unavailable")
    with pytest.raises(HTTPException) as exc:
        await auth.register(_request(), request_data, _db())
    assert exc.value.status_code == 409

    auth.generate_unique_username.side_effect = None
    with pytest.raises(HTTPException) as exc:
        await auth.register(_request(), request_data, conflict)
    assert exc.value.status_code == 409
    conflict.rollback.assert_awaited_once()

    created = _db()

    async def refresh_user(user):
        user.id = USER_ID
        user.created_at = NOW
        user.avatar_url = None

    created.refresh.side_effect = refresh_user
    response = await auth.register(_request(host="203.0.113.9"), request_data, created)
    assert response.user.role == UserRole.user
    event = emit.await_args.args[0]
    assert event.event_type == EventType.REGISTRATION
    assert event.source_ip == "203.0.113.9"
    assert event.outcome == "success"


@pytest.mark.asyncio
async def test_login_success_failure_and_password_flag(monkeypatch):
    emit = AsyncMock()
    monkeypatch.setattr(auth, "emit_security_event", emit)
    monkeypatch.setattr(auth, "_issue_tokens", AsyncMock(return_value=("access", "refresh", 3600)))
    redis = FakeRedis()
    redis.values[f"must_change_password:{USER_ID}"] = "1"
    monkeypatch.setattr(auth, "get_redis", lambda: redis)

    user = _user()
    result = await auth.login(_request(), LoginRequest(email="member", password="Valid1!Password"), _db(user))
    assert result["must_change_password"] is True
    assert emit.await_args.args[0].event_type == EventType.LOGIN_SUCCESS

    emit.reset_mock()
    with pytest.raises(HTTPException) as exc:
        await auth.login(_request(), LoginRequest(email="missing", password="Valid1!Password"), _db())
    assert exc.value.status_code == 401
    assert emit.await_args.args[0].event_type == EventType.LOGIN_FAILURE

    redis.get.side_effect = RedisError("unavailable")
    result = await auth.login(_request(), LoginRequest(email="member", password="Valid1!Password"), _db(user))
    assert result["must_change_password"] is False


@pytest.mark.asyncio
async def test_oauth_login_stores_only_safe_return_paths(monkeypatch):
    client = SimpleNamespace(authorize_redirect=AsyncMock(return_value=SimpleNamespace(status_code=307)))
    monkeypatch.setattr(auth.ds, "get_sync", lambda _key, default=None: "https://app.example.test/")
    request = _request()

    response = await auth._start_oauth_flow(
        request,
        client,
        callback_path="/api/v1/auth/oauth/callback",
        next_path="/dashboard",
    )
    assert response.status_code == 307
    assert request.session["oauth_next"] == "/dashboard"

    monkeypatch.setattr(auth, "oauth", SimpleNamespace(oidc=None))
    with pytest.raises(HTTPException) as exc:
        await auth.oauth_login(_request())
    assert exc.value.status_code == 500

    monkeypatch.setattr(auth, "oauth", SimpleNamespace(oidc=client))
    response = await auth.oauth_login(_request(), next="/settings")
    assert response.status_code == 307


@pytest.mark.asyncio
async def test_sso_provisioning_race_groups_and_failure(monkeypatch):
    monkeypatch.setattr(auth, "generate_unique_username", AsyncMock(return_value="sso-user"))

    race_db = _db()
    concurrent = _user(auth_provider="oidc", sso_subject_id="existing-subject")
    race_db.execute.side_effect = [_result(None), _result(concurrent), _result(None)]
    race_db.flush.side_effect = IntegrityError("insert", {}, Exception("duplicate"))
    provisioned = await auth._provision_sso_user(
        race_db,
        email="sso@example.test",
        name="SSO User",
        groups=["dev", "ops"],
        provider="oidc",
        subject_id="subject-1",
    )
    assert provisioned is concurrent
    race_db.rollback.assert_awaited_once()
    assert len(race_db.add_all.call_args.args[0]) == 2

    missing_db = _db()
    missing_db.execute.side_effect = [_result(None), _result(None)]
    missing_db.flush.side_effect = IntegrityError("insert", {}, Exception("duplicate"))
    with pytest.raises(HTTPException) as exc:
        await auth._provision_sso_user(
            missing_db,
            email="sso@example.test",
            name="SSO User",
            groups=None,
            provider="oidc",
            subject_id=None,
        )
    assert exc.value.status_code == 500

    groups_db = _db(_user(auth_provider="oidc"))
    groups_db.execute.side_effect = [
        _result(_user(auth_provider="oidc")),
        RuntimeError("group store unavailable"),
    ]
    returned = await auth._provision_sso_user(
        groups_db,
        email="sso@example.test",
        name="SSO User",
        groups=["dev"],
        provider="oidc",
        subject_id="subject-2",
    )
    assert returned.auth_provider == "oidc"


@pytest.mark.asyncio
async def test_complete_sso_login_rejects_redis_and_preserves_safe_next(monkeypatch):
    user = _user()
    db = _db(user)
    monkeypatch.setattr(auth, "_issue_tokens", AsyncMock(return_value=("access", "refresh", 3600)))
    monkeypatch.setattr(auth.ds, "get_sync", lambda _key, default=None: "https://app.example.test")
    emit = AsyncMock()
    monkeypatch.setattr(auth, "emit_security_event", emit)

    broken = FakeRedis()
    broken.setex.side_effect = RedisError("unavailable")
    monkeypatch.setattr(auth, "get_redis", lambda: broken)
    with pytest.raises(HTTPException) as exc:
        await auth._complete_sso_login(_request(), db, user, None)
    assert exc.value.status_code == 503

    redis = FakeRedis()
    monkeypatch.setattr(auth, "get_redis", lambda: redis)
    request = _request(session={"oauth_next": "/admin/users?tab=active"})
    response = await auth._complete_sso_login(request, db, user, ["dev"])
    assert response.status_code == 307
    assert "next=%2Fadmin%2Fusers%3Ftab%3Dactive" in response.headers["location"]
    assert emit.await_args.args[0].event_type == EventType.SSO_SUCCESS


@pytest.mark.asyncio
async def test_oidc_callback_happy_paths(monkeypatch):
    token = {
        "userinfo": {
            "email": " Member@Example.Test ",
            "name": "Member",
            "sub": "subject-1",
            "groups": ["dev", "ops"],
        }
    }
    oidc = SimpleNamespace(authorize_access_token=AsyncMock(return_value=token))
    monkeypatch.setattr(auth, "oauth", SimpleNamespace(oidc=oidc))
    monkeypatch.setattr(auth.ds, "get_sync", lambda _key, default=None: "https://app.example.test")
    monkeypatch.setattr(auth, "_issue_tokens", AsyncMock(return_value=("access", "refresh", 3600)))
    monkeypatch.setattr(auth.secrets, "token_urlsafe", lambda _size: "one-time-code")
    redis = FakeRedis()
    monkeypatch.setattr(auth, "get_redis", lambda: redis)
    emit = AsyncMock()
    monkeypatch.setattr(auth, "emit_security_event", emit)

    existing = _user()
    db = _db(existing)
    response = await auth.oauth_callback(_request(session={"oauth_next": "/dashboard"}), db)
    assert response.headers["location"].endswith("?code=one-time-code&next=%2Fdashboard")
    assert existing.auth_provider == "oidc"
    assert existing.sso_subject_id == "subject-1"
    assert len(db.add_all.call_args.args[0]) == 2
    assert emit.await_args.args[0].event_type == EventType.SSO_SUCCESS

    oidc.authorize_access_token.return_value = {"userinfo": {"email": "new@example.test", "sub": 42, "groups": None}}
    new_db = _db()
    monkeypatch.setattr(auth, "generate_unique_username", AsyncMock(return_value="new-user"))
    response = await auth.oauth_callback(_request(), new_db)
    assert response.status_code == 307
    new_db.add.assert_called_once()
    added = new_db.add.call_args.args[0]
    assert added.name == "SSO User"
    assert added.sso_subject_id is None

    oidc.authorize_access_token.return_value = {
        "userinfo": {"email": "unexpected@example.test", "groups": "unexpected"}
    }
    response = await auth.oauth_callback(_request(), _db(_user(auth_provider="oidc")))
    assert response.status_code == 307

    oidc.authorize_access_token.return_value = {
        "userinfo": {"email": "race@example.test", "name": "Race", "groups": ["dev"]}
    }
    concurrent = _user(auth_provider="oidc")
    race_db = _db()
    race_db.execute.side_effect = [_result(None), _result(concurrent), RuntimeError("group store unavailable")]
    race_db.flush.side_effect = IntegrityError("insert", {}, Exception("duplicate"))
    response = await auth.oauth_callback(_request(), race_db)
    assert response.status_code == 307
    race_db.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "token"),
    [
        ("exchange", RuntimeError("invalid_client")),
        ("exchange", RuntimeError("state mismatch")),
        ("exchange", RuntimeError("invalid_grant")),
        ("userinfo", {}),
        ("email", {"userinfo": {"name": "No Email"}}),
    ],
)
async def test_oidc_callback_early_failures(monkeypatch, case, token):
    authorize = AsyncMock(side_effect=token) if isinstance(token, Exception) else AsyncMock(return_value=token)
    monkeypatch.setattr(auth, "oauth", SimpleNamespace(oidc=SimpleNamespace(authorize_access_token=authorize)))
    monkeypatch.setattr(auth.ds, "get_sync", lambda _key, default=None: "https://app.example.test")
    persist = AsyncMock(return_value="correlation_1")
    monkeypatch.setattr(auth, "_persist_oidc_failure", persist)
    monkeypatch.setattr(auth, "emit_security_event", AsyncMock())

    response = await auth.oauth_callback(_request(), _db())
    assert response.status_code == 307
    assert response.headers["location"].endswith("/login?sso_error=correlation_1")
    assert persist.await_count == 1
    assert case in {"exchange", "userinfo", "email"}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["lookup", "create", "race", "tokens", "auth_service", "handoff"])
async def test_oidc_callback_storage_failures(monkeypatch, failure):
    token = {"userinfo": {"email": "sso@example.test", "name": "SSO", "sub": "sub"}}
    oidc = SimpleNamespace(authorize_access_token=AsyncMock(return_value=token))
    monkeypatch.setattr(auth, "oauth", SimpleNamespace(oidc=oidc))
    monkeypatch.setattr(auth.ds, "get_sync", lambda _key, default=None: "https://app.example.test")
    monkeypatch.setattr(auth, "generate_unique_username", AsyncMock(return_value="sso-user"))
    monkeypatch.setattr(auth, "emit_security_event", AsyncMock())
    persist = AsyncMock(return_value="correlation_2")
    monkeypatch.setattr(auth, "_persist_oidc_failure", persist)
    redis = FakeRedis()
    monkeypatch.setattr(auth, "get_redis", lambda: redis)
    db = _db(_user(auth_provider="oidc"))
    monkeypatch.setattr(auth, "_issue_tokens", AsyncMock(return_value=("access", "refresh", 3600)))

    if failure == "lookup":
        db.execute.side_effect = RuntimeError("database unavailable")
    elif failure == "create":
        db.execute.return_value = _result(None)
        db.flush.side_effect = RuntimeError("insert unavailable")
    elif failure == "race":
        db.execute.side_effect = [_result(None), _result(None)]
        db.flush.side_effect = IntegrityError("insert", {}, Exception("duplicate"))
    elif failure == "tokens":
        auth._issue_tokens.side_effect = RuntimeError("signing unavailable")
    elif failure == "auth_service":
        auth._issue_tokens.side_effect = HTTPException(status_code=503, detail="Auth service unavailable")
    else:
        redis.setex.side_effect = RedisError("unavailable")

    if failure == "auth_service":
        with pytest.raises(HTTPException) as exc:
            await auth.oauth_callback(_request(), db)
        assert exc.value.status_code == 503
        return

    response = await auth.oauth_callback(_request(), db)
    assert response.status_code == 307
    assert "sso_error=correlation_2" in response.headers["location"]
    persist.assert_awaited_once()


@pytest.mark.asyncio
async def test_oidc_failure_persistence_and_redirect_sanitization(monkeypatch):
    monkeypatch.setattr(auth.sso_diagnostics, "new_session_id", lambda: "safe_id")
    finalize = AsyncMock()
    monkeypatch.setattr(auth.sso_diagnostics, "finalize", finalize)
    correlation_id = await auth._persist_oidc_failure([], "failed", None)
    assert correlation_id == "safe_id"
    finalize.assert_awaited_once()

    finalize.side_effect = RuntimeError("diagnostics unavailable")
    assert await auth._persist_oidc_failure([], "failed", None) == "safe_id"

    monkeypatch.setattr(auth.sso_diagnostics, "is_safe_session_id", lambda _value: False)
    response = auth._oidc_error_redirect("https://app.example.test/", "unsafe/value")
    assert response.headers["location"] == "https://app.example.test/login?sso_error=invalid"


@pytest.mark.asyncio
async def test_oidc_e2e_dispatch_and_structural_failures(monkeypatch):
    handle_callback = auth._handle_oidc_e2e_callback
    dispatch = AsyncMock(return_value=SimpleNamespace(status_code=299))
    monkeypatch.setattr(auth, "_handle_oidc_e2e_callback", dispatch)
    monkeypatch.setattr(auth.ds, "get_sync", lambda _key, default=None: "https://app.example.test")
    response = await auth.oauth_callback(_request(query={"state": "__e2e:session_123"}), _db())
    assert response.status_code == 299
    dispatch.assert_awaited_once()

    monkeypatch.setattr(auth.sso_diagnostics, "render_error_page", lambda *_args: "error")
    malformed = await handle_callback(_request(query={}), _db(), "https://app.example.test", "__e2e:bad/value")
    assert malformed.status_code == 400

    monkeypatch.setattr(auth.sso_diagnostics, "get_session", AsyncMock(return_value=None))
    expired = await handle_callback(_request(query={}), _db(), "https://app.example.test", "__e2e:session_123")
    assert expired.status_code == 404


@pytest.mark.asyncio
async def test_oidc_e2e_authorization_and_configuration_failures(monkeypatch):
    session = _e2e_session()
    finalize = _patch_e2e(monkeypatch, session)

    response = await auth._handle_oidc_e2e_callback(
        _request(query={"error": "access_denied", "error_description": "cancelled"}),
        _db(),
        "https://app.example.test",
        "__e2e:session_123",
    )
    assert response.status_code == 200
    assert finalize.await_args.kwargs["summary"] == "IdP error: access_denied"

    response = await auth._handle_oidc_e2e_callback(
        _request(query={}), _db(), "https://app.example.test", "__e2e:session_123"
    )
    assert response.status_code == 200
    assert finalize.await_args.kwargs["summary"] == "No code in callback"

    monkeypatch.setattr(auth.ds, "get_sync", lambda _key, default=None: None)
    response = await auth._handle_oidc_e2e_callback(
        _request(query={"code": "code"}), _db(), "https://app.example.test", "__e2e:session_123"
    )
    assert finalize.await_args.kwargs["summary"] == "OIDC creds missing"

    _patch_e2e(monkeypatch, _e2e_session(token_endpoint=None))
    response = await auth._handle_oidc_e2e_callback(
        _request(query={"code": "code"}), _db(), "https://app.example.test", "__e2e:session_123"
    )
    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [
        httpx.TimeoutException("timeout"),
        RuntimeError("network unavailable"),
        TokenHttpResponse(401, {"error": "invalid_client", "error_description": "rejected"}),
        TokenHttpResponse(400, {"error": "invalid_grant"}),
        TokenHttpResponse(400, {"error": "redirect_uri_mismatch"}),
        TokenHttpResponse(500, ValueError("not json"), "not json"),
    ],
)
async def test_oidc_e2e_token_exchange_failures(monkeypatch, outcome):
    finalize = _patch_e2e(monkeypatch, _e2e_session())
    TokenHttpClient.outcome = outcome
    response = await auth._handle_oidc_e2e_callback(
        _request(query={"code": "code"}), _db(), "https://app.example.test", "__e2e:session_123"
    )
    assert response.status_code == 200
    assert "Token" in finalize.await_args.kwargs["summary"] or "token" in finalize.await_args.kwargs["summary"]


@pytest.mark.asyncio
async def test_oidc_e2e_token_payload_failures(monkeypatch):
    finalize = _patch_e2e(monkeypatch, _e2e_session())

    TokenHttpClient.outcome = TokenHttpResponse(200, ValueError("not json"))
    await auth._handle_oidc_e2e_callback(
        _request(query={"code": "code"}), _db(), "https://app.example.test", "__e2e:session_123"
    )
    assert finalize.await_args.kwargs["summary"] == "Non-JSON token response"

    TokenHttpClient.outcome = TokenHttpResponse(200, {})
    await auth._handle_oidc_e2e_callback(
        _request(query={"code": "code"}), _db(), "https://app.example.test", "__e2e:session_123"
    )
    assert finalize.await_args.kwargs["summary"] == "No id_token"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "summary"),
    [
        (pyjwt.InvalidAudienceError("audience"), "Audience mismatch"),
        (pyjwt.ExpiredSignatureError("expired"), "ID token expired"),
        (pyjwt.InvalidIssuerError("issuer"), "Issuer mismatch"),
        (pyjwt.InvalidTokenError("invalid"), "ID token invalid"),
        (RuntimeError("validation unavailable"), "ID token validation error"),
    ],
)
async def test_oidc_e2e_signature_failures(monkeypatch, error, summary):
    finalize = _patch_e2e(monkeypatch, _e2e_session())
    TokenHttpClient.outcome = TokenHttpResponse(200, {"refresh_token": "refresh", "id_token": "id-token"})
    monkeypatch.setattr(auth, "_decode_id_token_with_jwks", MagicMock(side_effect=error))

    response = await auth._handle_oidc_e2e_callback(
        _request(query={"code": "code"}), _db(), "https://app.example.test", "__e2e:session_123"
    )
    assert response.status_code == 200
    assert finalize.await_args.kwargs["summary"] == summary


@pytest.mark.asyncio
async def test_oidc_e2e_claim_validation_and_read_only_success(monkeypatch):
    finalize = _patch_e2e(monkeypatch, _e2e_session())
    TokenHttpClient.outcome = TokenHttpResponse(200, {"refresh_token": "refresh", "id_token": "id-token"})
    claims = {
        "email": " Actor@Example.Test ",
        "name": "Actor",
        "groups": ["dev"],
        "nonce": "fixed-nonce",
    }
    decode = MagicMock(return_value=claims)
    monkeypatch.setattr(auth, "_decode_id_token_with_jwks", decode)
    db = _db(_user())

    response = await auth._handle_oidc_e2e_callback(
        _request(query={"code": "code"}), db, "https://app.example.test", "__e2e:session_123"
    )
    assert response.status_code == 200
    assert finalize.await_args.kwargs["summary"] == "OIDC end-to-end test completed"
    assert finalize.await_args.kwargs["actor_email"] == "actor@example.test"
    db.commit.assert_not_awaited()
    db.add.assert_not_called()

    decode.return_value = {**claims, "nonce": "wrong"}
    await auth._handle_oidc_e2e_callback(
        _request(query={"code": "code"}), db, "https://app.example.test", "__e2e:session_123"
    )
    assert finalize.await_args.kwargs["summary"] == "Nonce mismatch"

    decode.return_value = {"nonce": "fixed-nonce", "sub": "subject"}
    await auth._handle_oidc_e2e_callback(
        _request(query={"code": "code"}), db, "https://app.example.test", "__e2e:session_123"
    )
    assert finalize.await_args.kwargs["summary"] == "No email claim"


@pytest.mark.asyncio
async def test_oidc_e2e_unverified_diagnostics_paths(monkeypatch):
    finalize = _patch_e2e(monkeypatch, _e2e_session(jwks_uri=None, nonce=None))
    TokenHttpClient.outcome = TokenHttpResponse(200, {"id_token": "id-token"})
    monkeypatch.setattr(auth.pyjwt, "decode", MagicMock(return_value={"email": "actor@example.test"}))
    db = _db()

    response = await auth._handle_oidc_e2e_callback(
        _request(query={"code": "code"}), db, "https://app.example.test", "__e2e:session_123"
    )
    assert response.status_code == 200
    assert finalize.await_args.kwargs["summary"] == "OIDC end-to-end test completed"

    db.execute.side_effect = RuntimeError("lookup unavailable")
    auth.pyjwt.decode.return_value = {"email": "actor@example.test", "groups": "unexpected"}
    await auth._handle_oidc_e2e_callback(
        _request(query={"code": "code"}), db, "https://app.example.test", "__e2e:session_123"
    )
    assert finalize.await_args.kwargs["summary"] == "OIDC end-to-end test completed"

    auth.pyjwt.decode.side_effect = pyjwt.InvalidTokenError("malformed")
    await auth._handle_oidc_e2e_callback(
        _request(query={"code": "code"}), db, "https://app.example.test", "__e2e:session_123"
    )
    assert finalize.await_args.kwargs["summary"] == "Unverifiable id_token"


def test_decode_id_token_uses_jwks_and_strict_claim_checks(monkeypatch):
    key = SimpleNamespace(key="public-key")
    jwks = MagicMock()
    jwks.get_signing_key_from_jwt.return_value = key
    jwks_class = MagicMock(return_value=jwks)
    decode = MagicMock(return_value={"sub": "subject"})
    monkeypatch.setattr(auth, "PyJWKClient", jwks_class)
    monkeypatch.setattr(auth.pyjwt, "decode", decode)

    claims = auth._decode_id_token_with_jwks(
        "id-token", "https://idp.example.test/jwks", "test-client", "https://idp.example.test"
    )
    assert claims == {"sub": "subject"}
    assert decode.call_args.kwargs["options"]["verify_signature"] is True
    assert decode.call_args.kwargs["options"]["verify_aud"] is True
    assert decode.call_args.kwargs["options"]["verify_exp"] is True


@pytest.mark.asyncio
async def test_sso_diagnostics_http_responses(monkeypatch):
    app = _route_app(_db())
    monkeypatch.setattr(auth.sso_diagnostics, "get_session", AsyncMock(return_value=None))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        invalid = await client.get("/api/v1/auth/sso/diagnostics/bad.value")
        missing = await client.get("/api/v1/auth/sso/diagnostics/safe_id")
        monkeypatch.setattr(
            auth.sso_diagnostics,
            "get_session",
            AsyncMock(return_value={"session_id": "safe_id", "checks": [], "summary": "sanitized"}),
        )
        found = await client.get("/api/v1/auth/sso/diagnostics/safe_id")
    assert invalid.status_code == 400
    assert missing.status_code == 404
    assert found.status_code == 200
    assert found.json()["summary"] == "sanitized"


@pytest.mark.asyncio
async def test_exchange_code_rejects_corrupt_and_missing_users(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(auth, "get_redis", lambda: redis)

    redis.values["oauth_code:corrupt"] = "not-json"
    with pytest.raises(HTTPException) as exc:
        await auth.exchange_code(SimpleNamespace(code="corrupt"), _db())
    assert exc.value.status_code == 400

    redis.values["oauth_code:claims"] = json.dumps({"refresh_token": "refresh"})
    with pytest.raises(HTTPException) as exc:
        await auth.exchange_code(SimpleNamespace(code="claims"), _db())
    assert exc.value.status_code == 400

    redis.values["oauth_code:user"] = json.dumps(
        {"access_token": "access", "refresh_token": "refresh", "expires_in": 3600, "user_id": str(USER_ID)}
    )
    with pytest.raises(HTTPException) as exc:
        await auth.exchange_code(SimpleNamespace(code="user"), _db())
    assert exc.value.status_code == 400

    redis.getdel.side_effect = RedisError("unavailable")
    with pytest.raises(HTTPException) as exc:
        await auth.exchange_code(SimpleNamespace(code="redis"), _db())
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_logout_revokes_access_refresh_and_emits_audit(monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    redis = FakeRedis()
    monkeypatch.setattr(auth, "get_redis", lambda: redis)
    monkeypatch.setattr(auth, "datetime", FrozenDateTime)
    monkeypatch.setattr(
        auth,
        "decode_access_token",
        lambda _token: {"jti": "access-jti", "exp": int((NOW + timedelta(minutes=5)).timestamp())},
    )
    monkeypatch.setattr(auth, "decode_refresh_token", lambda _token: {"jti": "refresh-jti"})
    emit = AsyncMock()
    monkeypatch.setattr(auth, "emit_security_event", emit)

    response = await auth.logout(
        _request(headers={"authorization": "Bearer access-token", "user-agent": "route-test"}),
        LogoutRequest(refresh_token="refresh-token"),
        _user(),
    )
    assert response == {"detail": "Logged out"}
    setex_keys = [call.args[0] for call in redis.setex.await_args_list]
    assert setex_keys == ["revoked_jti:access-jti", f"revoked_user:{USER_ID}"]
    redis.delete.assert_awaited_once_with("refresh_jti:refresh-jti")
    event = emit.await_args.args[0]
    assert event.event_type == EventType.LOGOUT
    assert event.outcome == "success"

    monkeypatch.setattr(auth, "decode_access_token", MagicMock(side_effect=ValueError("malformed")))
    monkeypatch.setattr(auth, "decode_refresh_token", MagicMock(side_effect=ValueError("malformed")))
    response = await auth.logout(_request(headers={}), LogoutRequest(refresh_token="malformed-refresh"), _user())
    assert response == {"detail": "Logged out"}

    monkeypatch.setattr(auth, "decode_access_token", lambda _token: {})
    monkeypatch.setattr(auth, "decode_refresh_token", lambda _token: {"jti": "refresh-jti"})
    redis.delete.side_effect = RedisError("unavailable")
    response = await auth.logout(_request(headers={}), LogoutRequest(refresh_token="refresh"), _user())
    assert response == {"detail": "Logged out"}

    redis.setex.side_effect = RedisError("unavailable")
    with pytest.raises(HTTPException) as exc:
        await auth.logout(_request(headers={}), LogoutRequest(), _user())
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_password_token_endpoint_success_and_failure(monkeypatch):
    emit = AsyncMock()
    monkeypatch.setattr(auth, "emit_security_event", emit)
    monkeypatch.setattr(auth, "_issue_tokens", AsyncMock(return_value=("access", "refresh", 3600)))

    with pytest.raises(HTTPException) as exc:
        await auth.issue_token(_request(), TokenRequest(email="missing", password="Valid1!Password"), _db())
    assert exc.value.status_code == 401
    assert emit.await_args.args[0].event_type == EventType.LOGIN_FAILURE

    emit.reset_mock()
    response = await auth.issue_token(
        _request(host="203.0.113.10"),
        TokenRequest(email="member@example.com", password="Valid1!Password"),
        _db(_user()),
    )
    assert response.token_type == "bearer"
    event = emit.await_args.args[0]
    assert event.event_type == EventType.LOGIN_SUCCESS
    assert event.source_ip == "203.0.113.10"


@pytest.mark.asyncio
async def test_refresh_rotation_and_failure_modes(monkeypatch):
    redis = FakeRedis()
    redis.values["refresh_jti:old-jti"] = str(USER_ID)
    monkeypatch.setattr(auth, "get_redis", lambda: redis)
    monkeypatch.setattr(
        auth,
        "decode_refresh_token",
        lambda _token: {"jti": "old-jti", "sub": str(USER_ID), "groups": ["dev"]},
    )
    access = MagicMock(return_value=("access", 3600))
    refresh = MagicMock(return_value=("refresh", "new-jti"))
    monkeypatch.setattr(auth, "create_access_token", access)
    monkeypatch.setattr(auth, "create_refresh_token", refresh)
    monkeypatch.setattr(auth.ds, "get_sync_int", lambda _key, default: 7)

    response = await auth.refresh_token(_request(), RefreshRequest(refresh_token="refresh"), _db(_user()))
    assert response.expires_in == 3600
    assert "refresh_jti:old-jti" not in redis.values
    assert redis.values["refresh_jti:new-jti"] == str(USER_ID)
    assert access.call_args.kwargs["groups"] == ["dev"]

    monkeypatch.setattr(auth, "decode_refresh_token", lambda _token: {})
    with pytest.raises(HTTPException) as exc:
        await auth.refresh_token(_request(), RefreshRequest(refresh_token="refresh"), _db())
    assert exc.value.status_code == 401

    monkeypatch.setattr(auth, "decode_refresh_token", MagicMock(side_effect=pyjwt.InvalidTokenError("invalid")))
    with pytest.raises(HTTPException) as exc:
        await auth.refresh_token(_request(), RefreshRequest(refresh_token="refresh"), _db())
    assert exc.value.status_code == 401

    monkeypatch.setattr(
        auth,
        "decode_refresh_token",
        lambda _token: {"jti": "revoked-jti", "sub": str(USER_ID)},
    )
    with pytest.raises(HTTPException) as exc:
        await auth.refresh_token(_request(), RefreshRequest(refresh_token="refresh"), _db())
    assert exc.value.status_code == 401

    redis.values["refresh_jti:revoked-jti"] = str(USER_ID)
    with pytest.raises(HTTPException) as exc:
        await auth.refresh_token(_request(), RefreshRequest(refresh_token="refresh"), _db())
    assert exc.value.detail == "User no longer exists"


@pytest.mark.asyncio
async def test_refresh_fails_closed_when_rotated_token_cannot_be_stored(monkeypatch):
    redis = FakeRedis()
    redis.values["refresh_jti:old-jti"] = str(USER_ID)
    redis.delete.side_effect = RedisError("unavailable")
    redis.setex.side_effect = RedisError("unavailable")
    monkeypatch.setattr(auth, "get_redis", lambda: redis)
    monkeypatch.setattr(
        auth,
        "decode_refresh_token",
        lambda _token: {"jti": "old-jti", "sub": str(USER_ID), "groups": []},
    )
    monkeypatch.setattr(auth, "create_access_token", lambda *_args, **_kwargs: ("access", 3600))
    monkeypatch.setattr(auth, "create_refresh_token", lambda *_args, **_kwargs: ("refresh", "new-jti"))

    with pytest.raises(HTTPException) as exc:
        await auth.refresh_token(_request(), RefreshRequest(refresh_token="refresh"), _db(_user()))
    assert exc.value.status_code == 503
    assert exc.value.detail == "Auth service temporarily unavailable"


@pytest.mark.asyncio
async def test_refresh_revocation_validates_claims_and_redis(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(auth, "get_redis", lambda: redis)
    monkeypatch.setattr(auth, "decode_refresh_token", lambda _token: {"jti": "refresh-jti"})
    response = await auth.revoke_token(_request(), RevokeRequest(refresh_token="refresh"))
    assert response == {"detail": "Token revoked"}
    redis.delete.assert_awaited_once_with("refresh_jti:refresh-jti")

    monkeypatch.setattr(auth, "decode_refresh_token", lambda _token: {})
    with pytest.raises(HTTPException) as exc:
        await auth.revoke_token(_request(), RevokeRequest(refresh_token="refresh"))
    assert exc.value.status_code == 401

    monkeypatch.setattr(auth, "decode_refresh_token", MagicMock(side_effect=pyjwt.InvalidTokenError("invalid")))
    with pytest.raises(HTTPException) as exc:
        await auth.revoke_token(_request(), RevokeRequest(refresh_token="refresh"))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_change_password_checks_current_password_and_clears_gate(monkeypatch):
    user = _user()
    user.verify_password.return_value = False
    with pytest.raises(HTTPException) as exc:
        await auth.change_password(
            _request(),
            ChangePasswordRequest(current_password="current", new_password="Valid2!Password"),
            _db(),
            user,
        )
    assert exc.value.status_code == 400

    user.verify_password.return_value = True
    db = _db()
    redis = FakeRedis()
    monkeypatch.setattr(auth, "get_redis", lambda: redis)
    response = await auth.change_password(
        _request(),
        ChangePasswordRequest(current_password="current", new_password="Valid2!Password"),
        db,
        user,
    )
    assert response == {"message": "Password changed"}
    user.set_password.assert_called_once()
    db.commit.assert_awaited_once()
    redis.delete.assert_awaited_once_with(f"must_change_password:{USER_ID}")

    redis.delete.side_effect = RedisError("unavailable")
    response = await auth.change_password(
        _request(),
        ChangePasswordRequest(current_password="current", new_password="Valid3!Password"),
        _db(),
        user,
    )
    assert response == {"message": "Password changed"}


@pytest.mark.asyncio
async def test_username_update_rules_and_conflicts(monkeypatch):
    user = _user()
    same = await auth.set_username(UsernameUpdateRequest(username="member"), _db(), user)
    assert same is user

    duplicate_db = _db(_user(id=uuid.UUID("22222222-2222-4222-8222-222222222222")))
    with pytest.raises(HTTPException) as exc:
        await auth.set_username(UsernameUpdateRequest(username="other-user"), duplicate_db, user)
    assert exc.value.status_code == 409

    monkeypatch.setattr(auth, "user_has_listings", AsyncMock(return_value=True))
    with pytest.raises(HTTPException) as exc:
        await auth.set_username(UsernameUpdateRequest(username="new-member"), _db(), user)
    assert exc.value.detail == "Username cannot change after publishing a registry item"

    monkeypatch.setattr(auth, "user_has_listings", AsyncMock(return_value=False))
    reserve = AsyncMock(side_effect=ValueError("Handle already reserved"))
    monkeypatch.setattr(auth, "reserve_team_handle", reserve)
    with pytest.raises(HTTPException) as exc:
        await auth.set_username(UsernameUpdateRequest(username="new-member"), _db(), user)
    assert exc.value.status_code == 409

    monkeypatch.setattr(auth, "reserve_team_handle", AsyncMock())
    db = _db()
    updated = await auth.set_username(UsernameUpdateRequest(username="new-member"), db, user)
    assert updated.username == "new-member"
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(user)

    legacy = _user(username="Legacy Handle")
    rename = AsyncMock()
    monkeypatch.setattr(auth, "rename_namespace", rename)
    conflict = _db()
    conflict.commit.side_effect = IntegrityError("update", {}, Exception("duplicate"))
    with pytest.raises(HTTPException) as exc:
        await auth.set_username(UsernameUpdateRequest(username="legacy-handle"), conflict, legacy)
    assert exc.value.status_code == 409
    rename.assert_awaited_once_with(conflict, "Legacy Handle", "legacy-handle")
    conflict.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_hooks_token_and_avatar_profile_updates(monkeypatch):
    user = _user()
    create = MagicMock(return_value=("hooks-token", 7200))
    emit = AsyncMock()
    monkeypatch.setattr(auth, "create_access_token", create)
    monkeypatch.setattr(auth, "emit_security_event", emit)
    monkeypatch.setattr(auth.ds, "get_sync_int", lambda _key, default: 120)

    response = await auth.create_hooks_token(user)
    assert response["expires_in"] == 7200
    assert create.call_args.kwargs["expires_in_minutes"] == 120
    assert create.call_args.kwargs["groups"] == ["engineering"]
    assert emit.await_args.args[0].event_type == EventType.API_KEY_CREATED

    db = _db()
    missing_request = SimpleNamespace(json=AsyncMock(return_value={}))
    with pytest.raises(HTTPException) as exc:
        await auth.upload_avatar(missing_request, db, user)
    assert exc.value.status_code == 422

    png = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\npayload").decode()
    upload_request = SimpleNamespace(json=AsyncMock(return_value={"avatar_url": png}))
    uploaded = await auth.upload_avatar(upload_request, db, user)
    assert uploaded.avatar_url == png
    db.commit.assert_awaited_once()

    db.commit.reset_mock()
    deleted = await auth.delete_avatar(db, user)
    assert deleted.avatar_url is None
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_profile_and_whoami_http_authorization_failures():
    app = _route_app(_db())
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        whoami = await client.get("/api/v1/auth/whoami")
        username = await client.put("/api/v1/auth/profile/username", json={"username": "new-member"})
        avatar = await client.delete("/api/v1/auth/profile/avatar")
    assert whoami.status_code == 401
    assert username.status_code == 401
    assert avatar.status_code == 401


@pytest.mark.asyncio
async def test_google_and_github_network_failures_emit_security_events(monkeypatch):
    emit = AsyncMock()
    monkeypatch.setattr(auth, "emit_security_event", emit)

    google = SimpleNamespace(authorize_access_token=AsyncMock(side_effect=RuntimeError("provider rejected request")))
    monkeypatch.setattr(auth, "oauth", SimpleNamespace(google=google, github=None))
    with pytest.raises(HTTPException) as exc:
        await auth.google_oauth_callback(_request(), _db())
    assert exc.value.status_code == 400
    assert emit.await_args.args[0].event_type == EventType.SSO_FAILURE

    github = SimpleNamespace(
        authorize_access_token=AsyncMock(return_value={}), get=AsyncMock(side_effect=OSError("down"))
    )
    monkeypatch.setattr(auth, "oauth", SimpleNamespace(google=None, github=github))
    with pytest.raises(HTTPException) as exc:
        await auth.github_oauth_callback(_request(), _db())
    assert exc.value.status_code == 502

    github.authorize_access_token.side_effect = RuntimeError("provider rejected request")
    with pytest.raises(HTTPException) as exc:
        await auth.github_oauth_callback(_request(), _db())
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_github_email_and_membership_fail_closed(monkeypatch):
    response = MagicMock(status_code=200)
    response.json.return_value = {"unexpected": "shape"}
    client = SimpleNamespace(get=AsyncMock(return_value=response))
    with pytest.raises(HTTPException) as exc:
        await auth._github_pick_verified_email(client, {})
    assert exc.value.status_code == 400

    client.get.side_effect = [OSError("down"), MagicMock(status_code=404)]
    member = await auth._github_check_org_membership(client, {}, {"first-org", "second-org"})
    assert member is None
