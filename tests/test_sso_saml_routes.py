# SPDX-FileCopyrightText: 2026 Tanvi Reddy <tanvi.reddy330@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Deterministic route tests for the SAML service provider endpoints."""

from __future__ import annotations

import hashlib
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from api.deps import get_db
from api.routes import sso_saml as saml
from models.user import UserRole
from services.security_events import EventType, Severity

FRONTEND_URL = "https://app.example.test"
IDP_URL = "https://idp.example.test/sso"
USER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
TOKEN_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _config(**overrides):
    values = {
        "idp_entity_id": "https://idp.example.test/entity",
        "idp_sso_url": IDP_URL,
        "idp_slo_url": "",
        "idp_x509_cert": "idp-cert",
        "sp_entity_id": f"{FRONTEND_URL}/api/v1/sso/saml/metadata",
        "sp_acs_url": f"{FRONTEND_URL}/api/v1/sso/saml/acs",
        "sp_private_key_enc": "encrypted-key",
        "sp_x509_cert": "sp-cert",
        "external_sp_private_key": None,
        "jit_provisioning": True,
        "default_role": "user",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _user(**overrides):
    values = {
        "id": USER_ID,
        "email": "alice@example.test",
        "username": "alice",
        "name": "Alice Example",
        "role": UserRole.user,
        "auth_provider": "saml",
        "sso_subject_id": "subject-alice",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _auth():
    auth = MagicMock()
    auth.process_response.return_value = None
    auth.get_errors.return_value = []
    auth.get_last_error_reason.return_value = None
    auth.is_authenticated.return_value = True
    auth.get_last_message_id.return_value = "response-123"
    auth.get_last_response_xml.return_value = None
    auth.get_nameid.return_value = "subject-alice"
    return auth


def _request(
    path: str = "/api/v1/sso/saml/login",
    *,
    query: bytes = b"",
    body: bytes = b"",
    content_type: bytes | None = None,
) -> Request:
    headers = [] if content_type is None else [(b"content-type", content_type)]
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST" if body else "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query,
        "headers": headers,
        "client": ("203.0.113.10", 1234),
        "server": ("test", 80),
    }
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


@pytest.fixture(autouse=True)
def reset_dynamic_saml_cache():
    old_cache = saml._dynamic_saml_config_cache
    old_signature = saml._dynamic_saml_config_signature
    saml._dynamic_saml_config_cache = None
    saml._dynamic_saml_config_signature = None
    yield
    saml._dynamic_saml_config_cache = old_cache
    saml._dynamic_saml_config_signature = old_signature


@pytest.fixture
def settings(monkeypatch):
    values = {"deployment.frontend_url": FRONTEND_URL}
    flags = {"external_sp": False, "external_idp": False}

    def get_sync(key, default=""):
        return values.get(key, default)

    def get_sync_bool(key, default=False):
        return values.get(key, default)

    def get_sync_int(key, default=0):
        return int(values.get(key, default))

    monkeypatch.setattr(saml.ds, "get_sync", get_sync)
    monkeypatch.setattr(saml.ds, "get_sync_bool", get_sync_bool)
    monkeypatch.setattr(saml.ds, "get_sync_int", get_sync_int)
    monkeypatch.setattr(saml.ds, "has_external_saml_material", lambda: flags["external_sp"])
    monkeypatch.setattr(
        saml.ds,
        "is_externally_managed",
        lambda key: key == "saml.idp_x509_cert" and flags["external_idp"],
    )
    return SimpleNamespace(values=values, flags=flags)


@pytest.fixture
def db():
    session = MagicMock()
    session.execute = AsyncMock(return_value=_result(None))
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.add = MagicMock()
    return session


@pytest.fixture
def app(db):
    route_app = FastAPI()
    route_app.include_router(saml.router)

    async def override_db():
        yield db

    route_app.dependency_overrides[get_db] = override_db
    return route_app


@pytest.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
        follow_redirects=False,
    ) as http:
        yield http


@pytest.fixture
def acs_env(monkeypatch, db, settings):
    config = _config()
    auth = _auth()
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()
    redis.delete = AsyncMock()
    redis.incr = AsyncMock(return_value=1)
    redis.expire = AsyncMock()
    redis.getdel = AsyncMock()

    existing_user = _user()
    db.execute.return_value = _result(existing_user)

    get_config = AsyncMock(return_value=config)
    decrypt = MagicMock(return_value="sp-private-key")
    build_auth = MagicMock(return_value=auth)
    extract = MagicMock(
        return_value=(
            "alice@example.test",
            {"displayName": ["Alice Example"]},
        )
    )
    issue_tokens = AsyncMock(return_value=("access-token", "refresh-token", 3600))
    emit = AsyncMock()
    username = AsyncMock(return_value="alice")

    sessions = {}

    async def finalize(session_id, *, checks, summary, actor_email):
        sessions[session_id] = {
            "checks": checks,
            "summary": summary,
            "actor_email": actor_email,
        }

    async def get_session(session_id):
        return sessions.get(session_id)

    finalize_mock = AsyncMock(side_effect=finalize)
    get_session_mock = AsyncMock(side_effect=get_session)
    render = MagicMock(side_effect=lambda **kwargs: f"<html>{kwargs['ok']}:{kwargs['session_id']}</html>")

    monkeypatch.setattr(saml, "_get_saml_config", get_config)
    monkeypatch.setattr(saml, "_decrypt_sp_key", decrypt)
    monkeypatch.setattr(saml, "_build_auth", build_auth)
    monkeypatch.setattr(saml, "extract_name_id_and_attrs", extract)
    monkeypatch.setattr(saml, "_issue_tokens", issue_tokens)
    monkeypatch.setattr(saml, "get_redis", lambda: redis)
    monkeypatch.setattr(saml, "emit_security_event", emit)
    monkeypatch.setattr(saml, "generate_unique_username", username)
    monkeypatch.setattr(saml.secrets, "token_urlsafe", lambda _size: "oauth-code")
    monkeypatch.setattr(saml.uuid, "uuid4", lambda: TOKEN_ID)
    monkeypatch.setattr(saml.sso_diagnostics, "new_session_id", lambda: "corr-safe")
    monkeypatch.setattr(saml.sso_diagnostics, "finalize", finalize_mock)
    monkeypatch.setattr(saml.sso_diagnostics, "get_session", get_session_mock)
    monkeypatch.setattr(saml.sso_diagnostics, "render_result_page", render)

    return SimpleNamespace(
        config=config,
        auth=auth,
        redis=redis,
        user=existing_user,
        get_config=get_config,
        decrypt=decrypt,
        build_auth=build_auth,
        extract=extract,
        issue_tokens=issue_tokens,
        emit=emit,
        username=username,
        sessions=sessions,
        finalize=finalize_mock,
        get_session=get_session_mock,
        render=render,
        db=db,
    )


def _checks(env, session_id="corr-safe"):
    return env.sessions[session_id]["checks"]


def _check(env, name, session_id="corr-safe"):
    return next(check for check in _checks(env, session_id) if check["name"] == name)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "/"),
        ("", "/"),
        ("dashboard", "/"),
        ("https://evil.example/path", "/"),
        ("//evil.example/path", "/"),
        ("/sessions/abc?tab=trace", "/sessions/abc?tab=trace"),
    ],
)
def test_safe_redirect_path_accepts_only_local_absolute_paths(value, expected):
    assert saml._safe_redirect_path(value) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, (None, "/")),
        ("/dashboard", (None, "/dashboard")),
        ("https://evil.example", (None, "/")),
        ("__e2e:session_1", ("session_1", "/")),
        ("__e2e:session_1:ignored", ("session_1", "/")),
        ("__e2e:bad/id", (None, "/")),
        (f"__e2e:{'a' * 65}", (None, "/")),
    ],
)
def test_parse_relay_state_separates_diagnostics_from_redirects(raw, expected):
    assert saml._parse_relay_state(raw) == expected


def test_error_redirect_uses_controlled_origin_and_sanitized_id(settings):
    response = saml._saml_error_redirect("bad/id?next=https://evil.example")
    assert response.status_code == 302
    assert response.headers["location"] == f"{FRONTEND_URL}/login?sso_error=invalid"


def test_prepare_saml_request_uses_configured_public_origin(settings):
    settings.values["deployment.frontend_url"] = "https://sso.example.test:8443"
    data = saml._prepare_saml_request(_request(query=b"next=%2Fdashboard"))
    assert data == {
        "https": "on",
        "http_host": "sso.example.test:8443",
        "server_port": "8443",
        "script_name": "/api/v1/sso/saml/login",
        "get_data": {"next": "/dashboard"},
        "post_data": {},
    }


@pytest.mark.asyncio
async def test_prepare_saml_request_with_body_includes_form(settings):
    request = _request(
        "/api/v1/sso/saml/acs",
        body=b"SAMLResponse=assertion&RelayState=%2Ftraces",
        content_type=b"application/x-www-form-urlencoded",
    )
    data = await saml._prepare_saml_request_with_body(request)
    assert data["post_data"] == {"SAMLResponse": "assertion", "RelayState": "/traces"}


@pytest.mark.parametrize(
    ("slo_url", "expected_sp_slo"), [("", ""), ("https://idp.example.test/slo", f"{FRONTEND_URL}/api/v1/sso/saml/sls")]
)
def test_build_auth_passes_strict_settings_to_onelogin(monkeypatch, settings, slo_url, expected_sp_slo):
    config = _config(idp_slo_url=slo_url)
    toolkit = MagicMock(return_value="toolkit-auth")
    build_settings = MagicMock(return_value={"strict": True})
    monkeypatch.setattr(saml, "OneLogin_Saml2_Auth", toolkit)
    monkeypatch.setattr(saml, "build_saml_settings", build_settings)

    result = saml._build_auth(config, "private-key", {"post_data": {}})

    assert result == "toolkit-auth"
    assert build_settings.call_args.kwargs["sp_slo_url"] == expected_sp_slo
    assert build_settings.call_args.kwargs["idp_slo_url"] == slo_url
    toolkit.assert_called_once_with({"post_data": {}}, old_settings={"strict": True})


@pytest.mark.asyncio
async def test_get_saml_config_prefers_database_and_applies_external_material(monkeypatch, db, settings):
    stored = _config(idp_x509_cert="stored-idp", sp_x509_cert="stored-sp")
    db.execute.return_value = _result(stored)
    settings.flags.update(external_sp=True, external_idp=True)
    settings.values.update(
        {
            "saml.sp_private_key": "external-private-key",
            "saml.sp_x509_cert": "external-sp-cert",
            "saml.idp_x509_cert": "external-idp-cert",
        }
    )

    resolved = await saml._get_saml_config(db)

    assert resolved is not stored
    assert resolved.external_sp_private_key == "external-private-key"
    assert resolved.sp_x509_cert == "external-sp-cert"
    assert resolved.idp_x509_cert == "external-idp-cert"
    assert resolved.sp_entity_id == stored.sp_entity_id

    settings.flags.update(external_sp=False, external_idp=False)
    assert await saml._get_saml_config(db) is stored


@pytest.mark.asyncio
async def test_get_saml_config_returns_none_when_required_dynamic_settings_are_missing(db, settings):
    assert await saml._get_saml_config(db) is None


@pytest.mark.asyncio
async def test_get_saml_config_generates_and_caches_dynamic_key_pair(monkeypatch, db, settings):
    settings.values.update(
        {
            "saml.idp_entity_id": "https://idp.example.test/entity",
            "saml.idp_sso_url": IDP_URL,
            "saml.idp_slo_url": "https://idp.example.test/slo",
            "saml.idp_x509_cert": "idp-cert",
            "saml.jit_provisioning": False,
            "saml.default_role": "reviewer",
            "saml.sp_key_encryption_password": "encryption-password",
        }
    )
    generate = MagicMock(return_value=("generated-key", "generated-cert"))
    encrypt = MagicMock(return_value="encrypted-generated-key")
    monkeypatch.setattr(saml, "generate_sp_key_pair", generate)
    monkeypatch.setattr(saml, "encrypt_private_key", encrypt)

    first = await saml._get_saml_config(db)
    second = await saml._get_saml_config(db)

    assert second is first
    assert first.sp_entity_id == f"{FRONTEND_URL}/api/v1/sso/saml/metadata"
    assert first.sp_acs_url == f"{FRONTEND_URL}/api/v1/sso/saml/acs"
    assert first.sp_private_key_enc == "encrypted-generated-key"
    assert first.sp_x509_cert == "generated-cert"
    assert first.jit_provisioning is False
    assert first.default_role == "reviewer"
    generate.assert_called_once_with(common_name=first.sp_entity_id)
    encrypt.assert_called_once_with("generated-key", "encryption-password")


@pytest.mark.asyncio
async def test_get_saml_config_warns_when_generated_key_is_not_encrypted(monkeypatch, db, settings):
    settings.values.update(
        {
            "saml.idp_entity_id": "https://idp.example.test/entity",
            "saml.idp_sso_url": IDP_URL,
            "saml.sp_key_encryption_password": "",
        }
    )
    warning = MagicMock()
    monkeypatch.setattr(saml.optic, "warning", warning)
    monkeypatch.setattr(saml, "generate_sp_key_pair", MagicMock(return_value=("key", "cert")))
    monkeypatch.setattr(saml, "encrypt_private_key", MagicMock(return_value="key"))

    config = await saml._get_saml_config(db)

    assert config.sp_private_key_enc == "key"
    warning.assert_called_once()
    assert "stored unencrypted" in warning.call_args.args[0]


@pytest.mark.asyncio
async def test_get_saml_config_rechecks_cache_after_waiting_for_lock(monkeypatch, db, settings):
    settings.values.update(
        {
            "saml.idp_entity_id": "https://idp.example.test/entity",
            "saml.idp_sso_url": IDP_URL,
        }
    )
    cached = object()

    class CacheFilledByFirstRequest:
        async def __aenter__(self):
            saml._dynamic_saml_config_cache = cached
            saml._dynamic_saml_config_signature = saml._dynamic_saml_signature()

        async def __aexit__(self, *_exc):
            return False

    monkeypatch.setattr(saml, "_dynamic_saml_config_lock", CacheFilledByFirstRequest())

    assert await saml._get_saml_config(db) is cached


@pytest.mark.asyncio
async def test_get_saml_config_uses_external_key_pair_without_generating(monkeypatch, db, settings):
    settings.values.update(
        {
            "saml.idp_entity_id": "https://idp.example.test/entity",
            "saml.idp_sso_url": IDP_URL,
            "saml.sp_entity_id": "https://sp.example.test/metadata",
            "saml.sp_acs_url": "https://sp.example.test/acs",
            "saml.sp_private_key": "external-key",
            "saml.sp_x509_cert": "external-cert",
        }
    )
    settings.flags["external_sp"] = True
    generate = MagicMock()
    monkeypatch.setattr(saml, "generate_sp_key_pair", generate)

    config = await saml._get_saml_config(db)

    assert config.external_sp_private_key == "external-key"
    assert config.sp_private_key_enc == ""
    assert config.sp_x509_cert == "external-cert"
    generate.assert_not_called()


def test_decrypt_sp_key_prefers_external_material(monkeypatch, settings):
    decrypt = MagicMock(return_value="decrypted")
    monkeypatch.setattr(saml, "decrypt_private_key", decrypt)

    assert saml._decrypt_sp_key(_config(external_sp_private_key="external-key")) == "external-key"
    assert saml._decrypt_sp_key(_config()) == "decrypted"
    decrypt.assert_called_once_with("encrypted-key", "")


@pytest.mark.asyncio
async def test_issue_tokens_persists_refresh_session_and_clears_user_revocation(monkeypatch, settings):
    settings.values["jwt.refresh_token_expire_days"] = 7
    redis = MagicMock()
    redis.setex = AsyncMock()
    redis.delete = AsyncMock()
    access = MagicMock(return_value=("access", 900))
    refresh = MagicMock(return_value=("refresh", "refresh-jti"))
    monkeypatch.setattr(saml, "create_access_token", access)
    monkeypatch.setattr(saml, "create_refresh_token", refresh)
    monkeypatch.setattr(saml, "get_redis", lambda: redis)
    user = _user(role=UserRole.reviewer)

    assert await saml._issue_tokens(user) == ("access", "refresh", 900)
    access.assert_called_once_with(USER_ID, UserRole.reviewer)
    refresh.assert_called_once_with(USER_ID, UserRole.reviewer)
    redis.setex.assert_awaited_once_with("refresh_jti:refresh-jti", 7 * 86400, str(USER_ID))
    redis.delete.assert_awaited_once_with(f"revoked_user:{USER_ID}")


@pytest.mark.parametrize(
    ("error", "message"),
    [
        ("idp_cert malformed", "IdP certificate missing or malformed."),
        ("sp_cert malformed", "SP key or certificate invalid."),
        ("idp_sso_url invalid", "IdP SSO URL missing or invalid."),
        ("unexpected", "SAML configuration error."),
    ],
)
@pytest.mark.asyncio
async def test_saml_check_suite_reports_toolkit_configuration_failures(monkeypatch, settings, error, message):
    monkeypatch.setattr(saml, "_build_auth", MagicMock(side_effect=ValueError(error)))

    checks = await saml._run_saml_check_suite(_config(), "key", FRONTEND_URL, MagicMock())

    assert checks == [
        {
            "name": "onelogin_build",
            "label": "OneLogin SAML settings load",
            "status": "fail",
            "message": message,
            "hint": "Open the admin SAML page for full diagnostics.",
        }
    ]


@pytest.mark.asyncio
async def test_saml_check_suite_runs_all_reachability_and_certificate_checks(monkeypatch, settings):
    settings.values["saml.idp_metadata_url"] = "https://idp.example.test/metadata"
    auth = MagicMock()
    auth.login.return_value = IDP_URL
    monkeypatch.setattr(saml, "_build_auth", MagicMock(return_value=auth))
    monkeypatch.setattr(saml, "get_idp_metadata_xml", AsyncMock(return_value=None))
    monkeypatch.setattr(
        saml,
        "check_idp_sso_url_reachable",
        AsyncMock(return_value={"name": "sso", "status": "pass"}),
    )
    monkeypatch.setattr(
        saml,
        "check_idp_slo_url_reachable",
        AsyncMock(return_value={"name": "slo", "status": "skip"}),
    )
    sync_checks = [
        "check_idp_cert_against_metadata",
        "check_cert_expiry",
        "check_sp_host_consistency",
        "check_sp_cert_key_match",
        "check_nameid_format",
    ]
    for name in sync_checks:
        monkeypatch.setattr(saml, name, MagicMock(return_value={"name": name, "status": "pass"}))

    checks = await saml._run_saml_check_suite(
        _config(idp_slo_url="https://idp.example.test/slo"),
        "key",
        FRONTEND_URL,
        MagicMock(),
    )

    assert checks[0]["name"] == "onelogin_build"
    assert checks[1]["name"] == "authn_request"
    assert any(check["name"] == "idp_metadata_reachable" and check["status"] == "fail" for check in checks)
    assert {check["name"] for check in checks} >= {"sso", "slo", *sync_checks}
    auth.login.assert_called_once_with(return_to="/")


@pytest.mark.asyncio
async def test_saml_health_probe_handles_absent_config_and_key_failure(monkeypatch, db, settings):
    get_config = AsyncMock(return_value=None)
    monkeypatch.setattr(saml, "_get_saml_config", get_config)
    assert await saml.saml_health_probe(db) is None

    get_config.return_value = _config()
    monkeypatch.setattr(saml, "_decrypt_sp_key", MagicMock(side_effect=ValueError("wrong password")))
    monkeypatch.setattr(saml, "time", SimpleNamespace(monotonic=MagicMock(side_effect=[10.0, 10.004])))
    result = await saml.saml_health_probe(db)
    assert result == {
        "ok": False,
        "checks": [
            {
                "name": "sp_key_decrypt",
                "label": "SP private key decrypts",
                "status": "fail",
                "message": "SP private key could not be decrypted.",
                "hint": "Check saml.sp_key_encryption_password.",
            }
        ],
        "latency_ms": 4,
    }


@pytest.mark.asyncio
async def test_saml_health_probe_uses_bounded_non_redirecting_client(monkeypatch, db, settings):
    monkeypatch.setattr(saml, "_get_saml_config", AsyncMock(return_value=_config()))
    monkeypatch.setattr(saml, "_decrypt_sp_key", MagicMock(return_value="key"))
    monkeypatch.setattr(
        saml,
        "_run_saml_check_suite",
        AsyncMock(return_value=[{"name": "suite", "status": "pass"}]),
    )
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client_factory = MagicMock(return_value=client)
    monkeypatch.setattr(saml.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(saml, "time", SimpleNamespace(monotonic=MagicMock(side_effect=[20.0, 20.012])))

    result = await saml.saml_health_probe(db)

    assert result["ok"] is True
    assert result["latency_ms"] == 12
    assert [check["name"] for check in result["checks"]] == ["sp_key_decrypt", "suite"]
    client_factory.assert_called_once_with(timeout=10.0, follow_redirects=False)


@pytest.mark.asyncio
async def test_login_returns_404_when_saml_is_not_configured(monkeypatch, client, settings):
    monkeypatch.setattr(saml, "_get_saml_config", AsyncMock(return_value=None))
    response = await client.get("/api/v1/sso/saml/login")
    assert response.status_code == 404
    assert response.json()["detail"] == "SAML SSO is not configured"


@pytest.mark.parametrize(
    ("next_path", "expected_relay"),
    [
        (None, "/"),
        ("/sessions/abc", "/sessions/abc"),
        ("https://evil.example/phish", "/"),
        ("//evil.example/phish", "/"),
    ],
)
@pytest.mark.asyncio
async def test_login_redirects_to_idp_with_sanitized_relay_state(
    monkeypatch,
    client,
    settings,
    next_path,
    expected_relay,
):
    auth = MagicMock()
    auth.login.return_value = f"{IDP_URL}?request=signed"
    monkeypatch.setattr(saml, "_get_saml_config", AsyncMock(return_value=_config()))
    monkeypatch.setattr(saml, "_decrypt_sp_key", MagicMock(return_value="key"))
    monkeypatch.setattr(saml, "_build_auth", MagicMock(return_value=auth))

    response = await client.get("/api/v1/sso/saml/login", params={} if next_path is None else {"next": next_path})

    assert response.status_code == 302
    assert response.headers["location"].startswith(IDP_URL)
    auth.login.assert_called_once_with(return_to=expected_relay)


@pytest.mark.asyncio
async def test_login_uses_valid_diagnostics_relay_state(monkeypatch, client, settings):
    auth = MagicMock()
    auth.login.return_value = IDP_URL
    monkeypatch.setattr(saml, "_get_saml_config", AsyncMock(return_value=_config()))
    monkeypatch.setattr(saml, "_decrypt_sp_key", MagicMock(return_value="key"))
    monkeypatch.setattr(saml, "_build_auth", MagicMock(return_value=auth))

    response = await client.get("/api/v1/sso/saml/login", params={"e2e": "session_123"})

    assert response.status_code == 302
    auth.login.assert_called_once_with(return_to="__e2e:session_123")


@pytest.mark.parametrize("session_id", ["bad/id", "bad id", "a" * 65])
@pytest.mark.asyncio
async def test_login_rejects_invalid_diagnostics_session_ids(monkeypatch, client, settings, session_id):
    build_auth = MagicMock()
    monkeypatch.setattr(saml, "_get_saml_config", AsyncMock(return_value=_config()))
    monkeypatch.setattr(saml, "_decrypt_sp_key", MagicMock(return_value="key"))
    monkeypatch.setattr(saml, "_build_auth", build_auth)

    response = await client.get("/api/v1/sso/saml/login", params={"e2e": session_id})

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid e2e session id"
    build_auth.assert_not_called()


@pytest.mark.asyncio
async def test_metadata_is_public_xml_and_reports_toolkit_validation_errors(monkeypatch, client, settings):
    toolkit_settings = MagicMock()
    toolkit_settings.get_sp_metadata.return_value = "<EntityDescriptor/>"
    toolkit_settings.validate_metadata.return_value = []
    auth = MagicMock()
    auth.get_settings.return_value = toolkit_settings
    monkeypatch.setattr(saml, "_get_saml_config", AsyncMock(return_value=_config()))
    monkeypatch.setattr(saml, "_decrypt_sp_key", MagicMock(return_value="key"))
    monkeypatch.setattr(saml, "_build_auth", MagicMock(return_value=auth))

    response = await client.get("/api/v1/sso/saml/metadata")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/xml"
    assert response.text == "<EntityDescriptor/>"

    toolkit_settings.validate_metadata.return_value = ["invalid_sp_cert", "invalid_acs"]
    response = await client.get("/api/v1/sso/saml/metadata")
    assert response.status_code == 500
    assert response.json()["detail"] == "SP metadata validation error: invalid_sp_cert, invalid_acs"

    monkeypatch.setattr(saml, "_get_saml_config", AsyncMock(return_value=None))
    response = await client.get("/api/v1/sso/saml/metadata")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_acs_without_configuration_records_diagnostics_and_redirects(monkeypatch, client, acs_env):
    acs_env.get_config.return_value = None

    response = await client.post("/api/v1/sso/saml/acs", data={"SAMLResponse": "assertion"})

    assert response.status_code == 302
    assert response.headers["location"] == f"{FRONTEND_URL}/login?sso_error=corr-safe"
    assert _check(acs_env, "saml_configured")["status"] == "fail"
    assert acs_env.sessions["corr-safe"]["summary"] == "SAML not configured"
    acs_env.build_auth.assert_not_called()


@pytest.mark.asyncio
async def test_acs_e2e_failure_returns_result_page_without_session_tokens(client, acs_env):
    acs_env.get_config.return_value = None

    response = await client.post(
        "/api/v1/sso/saml/acs",
        data={"SAMLResponse": "assertion", "RelayState": "__e2e:diag_123"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.text == "<html>False:diag_123</html>"
    assert acs_env.sessions["diag_123"]["summary"] == "SAML not configured"
    acs_env.issue_tokens.assert_not_awaited()
    acs_env.db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_acs_continues_redirect_when_diagnostics_storage_fails(client, acs_env):
    acs_env.get_config.return_value = None
    acs_env.finalize.side_effect = RuntimeError("redis unavailable")

    response = await client.post("/api/v1/sso/saml/acs", data={"SAMLResponse": "assertion"})

    assert response.status_code == 302
    assert response.headers["location"] == f"{FRONTEND_URL}/login?sso_error=corr-safe"


@pytest.mark.asyncio
async def test_acs_rejects_sp_key_decryption_failure(client, acs_env):
    acs_env.decrypt.side_effect = ValueError("bad key password")

    response = await client.post("/api/v1/sso/saml/acs", data={"SAMLResponse": "assertion"})

    assert response.status_code == 302
    assert _check(acs_env, "sp_key_decrypt")["message"] == "bad key password"
    assert acs_env.sessions["corr-safe"]["summary"] == "SP key decrypt failed"
    acs_env.build_auth.assert_not_called()


@pytest.mark.asyncio
async def test_acs_handles_malformed_response_from_toolkit(client, acs_env):
    acs_env.auth.process_response.side_effect = ValueError("malformed assertion XML")

    response = await client.post("/api/v1/sso/saml/acs", data={"SAMLResponse": "not-xml"})

    assert response.status_code == 302
    assert _check(acs_env, "process_response")["status"] == "fail"
    assert acs_env.sessions["corr-safe"]["summary"] == "process_response crashed"
    acs_env.redis.get.assert_not_awaited()


@pytest.mark.parametrize(
    ("reason", "hint_fragment"),
    [
        ("Signature validation failed", "rotated its cert"),
        ("Audience mismatch", "entityID"),
        ("Assertion expired at NotOnOrAfter", "Clock skew"),
        ("Destination mismatch", "ACS URL"),
        ("Certificate rejected", "IdP cert"),
    ],
)
@pytest.mark.asyncio
async def test_acs_rejects_toolkit_signature_and_condition_errors(client, acs_env, reason, hint_fragment):
    acs_env.auth.get_errors.return_value = ["invalid_response"]
    acs_env.auth.get_last_error_reason.return_value = reason

    response = await client.post(
        "/api/v1/sso/saml/acs",
        data={"SAMLResponse": "assertion"},
        headers={"user-agent": "test-browser"},
    )

    assert response.status_code == 302
    check = _check(acs_env, "process_response")
    assert check["message"] == reason
    assert hint_fragment in check["hint"]
    event = acs_env.emit.await_args.args[0]
    assert event.event_type == EventType.SSO_FAILURE
    assert event.severity == Severity.WARNING
    assert event.outcome == "failure"
    assert event.source_ip == "127.0.0.1"
    assert event.user_agent == "test-browser"
    assert reason in event.detail


@pytest.mark.asyncio
async def test_acs_rejects_response_that_did_not_authenticate_subject(client, acs_env):
    acs_env.auth.is_authenticated.return_value = False

    response = await client.post("/api/v1/sso/saml/acs", data={"SAMLResponse": "assertion"})

    assert response.status_code == 302
    assert _check(acs_env, "is_authenticated")["status"] == "fail"
    acs_env.redis.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_acs_requires_stable_response_identifier(client, acs_env):
    acs_env.auth.get_last_message_id.return_value = None
    acs_env.auth.get_last_response_xml.return_value = None

    response = await client.post("/api/v1/sso/saml/acs", data={"SAMLResponse": "assertion"})

    assert response.status_code == 302
    assert _check(acs_env, "replay_protection")["status"] == "fail"
    assert acs_env.sessions["corr-safe"]["summary"] == "no response id"


@pytest.mark.asyncio
async def test_acs_hashes_response_xml_when_toolkit_omits_message_id(client, acs_env):
    acs_env.auth.get_last_message_id.return_value = None
    acs_env.auth.get_last_response_xml.return_value = "<Response ID='fallback'/>"
    expected_id = hashlib.sha256(b"<Response ID='fallback'/>").hexdigest()

    response = await client.post("/api/v1/sso/saml/acs", data={"SAMLResponse": "assertion"})

    assert response.status_code == 302
    acs_env.redis.get.assert_awaited_once_with(f"saml_assertion:{expected_id}")
    acs_env.redis.setex.assert_any_await(f"saml_assertion:{expected_id}", 300, "1")


@pytest.mark.asyncio
async def test_acs_rejects_replayed_assertion_and_emits_failure_event(client, acs_env):
    acs_env.redis.get.return_value = "1"

    response = await client.post("/api/v1/sso/saml/acs", data={"SAMLResponse": "assertion"})

    assert response.status_code == 302
    assert _check(acs_env, "replay_protection")["message"] == "This assertion was already processed."
    event = acs_env.emit.await_args.args[0]
    assert event.event_type == EventType.SSO_FAILURE
    assert event.detail == "SAML assertion replay detected"
    acs_env.db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_acs_fails_closed_when_replay_redis_is_unavailable(client, acs_env):
    acs_env.redis.get.side_effect = RuntimeError("redis down")

    response = await client.post("/api/v1/sso/saml/acs", data={"SAMLResponse": "assertion"})

    assert response.status_code == 302
    check = _check(acs_env, "replay_protection")
    assert check["status"] == "fail"
    assert "Redis unavailable" in check["message"]
    acs_env.db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_acs_reports_attribute_extraction_failure(client, acs_env):
    acs_env.extract.side_effect = ValueError("unsupported NameID")

    response = await client.post("/api/v1/sso/saml/acs", data={"SAMLResponse": "assertion"})

    assert response.status_code == 302
    assert _check(acs_env, "nameid_extract")["message"] == "unsupported NameID"
    acs_env.db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_acs_rejects_assertion_without_email_and_lists_available_attributes(client, acs_env):
    acs_env.extract.return_value = ("", {"groups": ["developers"], "department": ["engineering"]})

    response = await client.post("/api/v1/sso/saml/acs", data={"SAMLResponse": "assertion"})

    assert response.status_code == 302
    check = _check(acs_env, "nameid_extract")
    assert check["status"] == "fail"
    assert "department, groups" in check["message"]


@pytest.mark.parametrize("lookup", [None, _user(), RuntimeError("database unavailable")])
@pytest.mark.asyncio
async def test_acs_e2e_mode_is_read_only_and_records_user_lookup(client, acs_env, lookup):
    if isinstance(lookup, Exception):
        acs_env.db.execute.side_effect = lookup
        expected_status = "fail"
        expected_ok = False
    else:
        acs_env.db.execute.return_value = _result(lookup)
        expected_status = "skip" if lookup is None else "pass"
        expected_ok = True

    response = await client.post(
        "/api/v1/sso/saml/acs",
        data={"SAMLResponse": "assertion", "RelayState": "__e2e:diag_session"},
    )

    assert response.status_code == 200
    assert response.text == f"<html>{expected_ok}:diag_session</html>"
    assert _check(acs_env, "user_lookup", "diag_session")["status"] == expected_status
    assert _check(acs_env, "e2e_complete", "diag_session")["status"] == "pass"
    acs_env.issue_tokens.assert_not_awaited()
    acs_env.db.add.assert_not_called()
    acs_env.db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_acs_records_missing_display_name_without_trusting_group_claims(client, acs_env):
    acs_env.extract.return_value = (
        "alice@example.test",
        {"groups": ["super_admin", "admins"]},
    )

    response = await client.post(
        "/api/v1/sso/saml/acs",
        data={"SAMLResponse": "assertion", "RelayState": "__e2e:no_display_name"},
    )

    assert response.status_code == 200
    assert _check(acs_env, "name_attribute", "no_display_name")["status"] == "skip"
    acs_env.issue_tokens.assert_not_awaited()


@pytest.mark.asyncio
async def test_acs_redirects_when_user_lookup_fails(client, acs_env):
    acs_env.db.execute.side_effect = RuntimeError("database unavailable")

    response = await client.post("/api/v1/sso/saml/acs", data={"SAMLResponse": "assertion"})

    assert response.status_code == 302
    assert _check(acs_env, "user_lookup")["message"] == "database unavailable"
    assert acs_env.sessions["corr-safe"]["actor_email"] == "alice@example.test"


@pytest.mark.asyncio
async def test_acs_rejects_unknown_user_when_jit_is_disabled(client, acs_env):
    acs_env.db.execute.return_value = _result(None)
    acs_env.config.jit_provisioning = False

    response = await client.post("/api/v1/sso/saml/acs", data={"SAMLResponse": "assertion"})

    assert response.status_code == 302
    assert _check(acs_env, "jit_provisioning")["status"] == "fail"
    event = acs_env.emit.await_args.args[0]
    assert event.event_type == EventType.SSO_FAILURE
    assert event.actor_email == "alice@example.test"
    assert event.detail == "JIT provisioning disabled; user does not exist"
    acs_env.db.add.assert_not_called()


@pytest.mark.parametrize(
    ("configured_role", "expected_role"),
    [("reviewer", UserRole.reviewer), ("idp-admin-group", UserRole.user)],
)
@pytest.mark.asyncio
async def test_acs_jit_provisions_with_configured_role_not_untrusted_group_claims(
    client,
    acs_env,
    configured_role,
    expected_role,
):
    acs_env.db.execute.return_value = _result(None)
    acs_env.config.default_role = configured_role
    acs_env.extract.return_value = (
        "new-user@example.test",
        {"displayName": ["New User"], "groups": ["super_admin", "admins"]},
    )
    acs_env.auth.get_nameid.return_value = "idp-subject-42"

    async def assign_id():
        acs_env.db.add.call_args.args[0].id = USER_ID

    acs_env.db.flush.side_effect = assign_id

    response = await client.post("/api/v1/sso/saml/acs", data={"SAMLResponse": "assertion"})

    assert response.status_code == 302
    created = acs_env.db.add.call_args.args[0]
    assert created.email == "new-user@example.test"
    assert created.username == "alice"
    assert created.name == "New User"
    assert created.auth_provider == "saml"
    assert created.sso_subject_id == "idp-subject-42"
    assert created.role == expected_role
    assert created.role != UserRole.super_admin
    acs_env.db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_acs_reports_jit_database_failure(client, acs_env):
    acs_env.db.execute.return_value = _result(None)
    acs_env.db.flush.side_effect = RuntimeError("unique constraint")

    response = await client.post("/api/v1/sso/saml/acs", data={"SAMLResponse": "assertion"})

    assert response.status_code == 302
    assert _check(acs_env, "jit_provisioning")["message"] == "unique constraint"
    acs_env.db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_acs_rejects_deactivated_existing_user(client, acs_env):
    acs_env.user.auth_provider = "deactivated"

    response = await client.post("/api/v1/sso/saml/acs", data={"SAMLResponse": "assertion"})

    assert response.status_code == 302
    assert _check(acs_env, "account_active")["status"] == "fail"
    event = acs_env.emit.await_args.args[0]
    assert event.event_type == EventType.SSO_FAILURE
    assert event.detail == "Deactivated user attempted SAML login"
    acs_env.issue_tokens.assert_not_awaited()


@pytest.mark.parametrize("failure", ["tokens", "commit"])
@pytest.mark.asyncio
async def test_acs_reports_token_or_commit_failure(client, acs_env, failure):
    if failure == "tokens":
        acs_env.issue_tokens.side_effect = RuntimeError("signing failed")
        expected = "signing failed"
    else:
        acs_env.db.commit.side_effect = RuntimeError("database commit failed")
        expected = "database commit failed"

    response = await client.post("/api/v1/sso/saml/acs", data={"SAMLResponse": "assertion"})

    assert response.status_code == 302
    assert _check(acs_env, "issue_tokens")["message"] == expected
    acs_env.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_acs_existing_local_user_success_updates_profile_and_persists_one_time_state(client, acs_env):
    acs_env.user.auth_provider = "local"
    acs_env.user.sso_subject_id = None
    acs_env.user.name = "SSO User"
    acs_env.auth.get_nameid.return_value = "idp-subject-new"

    response = await client.post(
        "/api/v1/sso/saml/acs",
        data={"SAMLResponse": "assertion", "RelayState": "/sessions/abc"},
        headers={"user-agent": "browser/1.0"},
    )

    assert response.status_code == 302
    assert response.headers["location"] == (f"{FRONTEND_URL}/login?saml_token={TOKEN_ID}&next=/sessions/abc")
    assert acs_env.user.auth_provider == "saml"
    assert acs_env.user.sso_subject_id == "idp-subject-new"
    assert acs_env.user.name == "Alice Example"
    acs_env.db.commit.assert_awaited_once()

    calls = acs_env.redis.setex.await_args_list
    assert calls[0].args == ("saml_assertion:response-123", 300, "1")
    assert calls[1].args[:2] == ("oauth_code:oauth-code", 120)
    oauth_payload = json.loads(calls[1].args[2])
    assert oauth_payload == {
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "expires_in": 3600,
        "user_id": str(USER_ID),
        "role": "user",
    }
    assert calls[2].args[:2] == (f"saml_login:{TOKEN_ID}", 120)
    login_payload = json.loads(calls[2].args[2])
    assert login_payload["email"] == "alice@example.test"
    assert login_payload["username"] == "alice"
    assert "access-token" not in response.headers["location"]

    event = acs_env.emit.await_args.args[0]
    assert event.event_type == EventType.SSO_SUCCESS
    assert event.severity == Severity.INFO
    assert event.outcome == "success"
    assert event.actor_id == str(USER_ID)
    assert event.actor_email == "alice@example.test"
    assert event.user_agent == "browser/1.0"


@pytest.mark.asyncio
async def test_acs_sanitizes_hostile_relay_state_on_success(client, acs_env):
    response = await client.post(
        "/api/v1/sso/saml/acs",
        data={"SAMLResponse": "assertion", "RelayState": "https://evil.example/phish"},
    )

    assert response.status_code == 302
    assert response.headers["location"] == f"{FRONTEND_URL}/login?saml_token={TOKEN_ID}"
    assert "evil.example" not in response.headers["location"]


@pytest.mark.asyncio
async def test_acs_surfaces_post_commit_redis_failure_without_emitting_success(client, acs_env):
    acs_env.redis.setex.side_effect = [None, RuntimeError("redis unavailable")]

    response = await client.post("/api/v1/sso/saml/acs", data={"SAMLResponse": "assertion"})

    assert response.status_code == 500
    acs_env.db.commit.assert_awaited_once()
    acs_env.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_exchange_is_public_single_use_and_resets_rate_state(monkeypatch, client, settings):
    redis = MagicMock()
    redis.incr = AsyncMock(return_value=1)
    redis.expire = AsyncMock()
    redis.getdel = AsyncMock(
        return_value=json.dumps(
            {
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 600,
                "user_id": str(USER_ID),
                "role": "reviewer",
                "name": "Alice",
                "email": "alice@example.test",
                "username": "alice",
            }
        )
    )
    redis.delete = AsyncMock()
    monkeypatch.setattr(saml, "get_redis", lambda: redis)

    response = await client.post("/api/v1/sso/saml/exchange", params={"token_id": "one-time-token"})

    assert response.status_code == 200
    assert response.json() == {
        "access_token": "access",
        "refresh_token": "refresh",
        "expires_in": 600,
        "user": {
            "id": str(USER_ID),
            "role": "reviewer",
            "name": "Alice",
            "email": "alice@example.test",
            "username": "alice",
        },
    }
    redis.expire.assert_awaited_once_with("saml_exchange_rate:127.0.0.1", 60)
    redis.getdel.assert_awaited_once_with("saml_login:one-time-token")
    redis.delete.assert_awaited_once_with("saml_exchange_rate:127.0.0.1")


@pytest.mark.parametrize(
    ("attempts", "stored", "status", "detail", "expires"),
    [
        (1, None, 400, "Invalid or expired SAML token", True),
        (2, None, 400, "Invalid or expired SAML token", False),
        (6, "unused", 429, "Too many attempts", False),
    ],
)
@pytest.mark.asyncio
async def test_exchange_rate_limits_and_rejects_missing_tokens(
    monkeypatch,
    client,
    settings,
    attempts,
    stored,
    status,
    detail,
    expires,
):
    redis = MagicMock()
    redis.incr = AsyncMock(return_value=attempts)
    redis.expire = AsyncMock()
    redis.getdel = AsyncMock(return_value=stored)
    redis.delete = AsyncMock()
    monkeypatch.setattr(saml, "get_redis", lambda: redis)

    response = await client.post("/api/v1/sso/saml/exchange", params={"token_id": "bad-token"})

    assert response.status_code == status
    assert response.json()["detail"] == detail
    assert redis.expire.await_count == int(expires)
    if attempts > 5:
        redis.getdel.assert_not_awaited()


@pytest.mark.asyncio
async def test_exchange_redis_failure_is_not_silently_accepted(monkeypatch, client, settings):
    redis = MagicMock()
    redis.incr = AsyncMock(side_effect=RuntimeError("redis unavailable"))
    monkeypatch.setattr(saml, "get_redis", lambda: redis)

    response = await client.post("/api/v1/sso/saml/exchange", params={"token_id": "token"})
    assert response.status_code == 500


@pytest.mark.parametrize("config", [None, _config(idp_slo_url="")])
@pytest.mark.asyncio
async def test_logout_without_slo_returns_to_controlled_login(monkeypatch, client, settings, config):
    monkeypatch.setattr(saml, "_get_saml_config", AsyncMock(return_value=config))

    response = await client.get("/api/v1/sso/saml/logout")

    assert response.status_code == 302
    assert response.headers["location"] == f"{FRONTEND_URL}/login"


@pytest.mark.asyncio
async def test_logout_redirects_to_configured_idp_slo(monkeypatch, client, settings):
    auth = MagicMock()
    auth.logout.return_value = "https://idp.example.test/slo?SAMLRequest=signed"
    monkeypatch.setattr(
        saml,
        "_get_saml_config",
        AsyncMock(return_value=_config(idp_slo_url="https://idp.example.test/slo")),
    )
    monkeypatch.setattr(saml, "_decrypt_sp_key", MagicMock(return_value="key"))
    monkeypatch.setattr(saml, "_build_auth", MagicMock(return_value=auth))

    response = await client.get("/api/v1/sso/saml/logout")

    assert response.status_code == 302
    assert response.headers["location"].startswith("https://idp.example.test/slo")
    auth.logout.assert_called_once_with(return_to=f"{FRONTEND_URL}/login")


@pytest.mark.parametrize(
    ("toolkit_url", "errors", "expected"),
    [
        (f"{FRONTEND_URL}/signed-out", [], f"{FRONTEND_URL}/signed-out"),
        ("https://evil.example/steal", ["invalid_logout_response"], f"{FRONTEND_URL}/login"),
        (None, [], f"{FRONTEND_URL}/login"),
    ],
)
@pytest.mark.asyncio
async def test_sls_processes_logout_and_allows_only_frontend_redirects(
    monkeypatch,
    client,
    settings,
    toolkit_url,
    errors,
    expected,
):
    auth = MagicMock()
    auth.process_slo.return_value = toolkit_url
    auth.get_errors.return_value = errors
    monkeypatch.setattr(saml, "_get_saml_config", AsyncMock(return_value=_config()))
    monkeypatch.setattr(saml, "_decrypt_sp_key", MagicMock(return_value="key"))
    monkeypatch.setattr(saml, "_build_auth", MagicMock(return_value=auth))

    response = await client.get("/api/v1/sso/saml/sls", params={"SAMLResponse": "logout-response"})

    assert response.status_code == 302
    assert response.headers["location"] == expected
    callback = auth.process_slo.call_args.kwargs["delete_session_cb"]
    assert callback() is None


@pytest.mark.asyncio
async def test_sls_without_configuration_redirects_to_login(monkeypatch, client, settings):
    monkeypatch.setattr(saml, "_get_saml_config", AsyncMock(return_value=None))
    response = await client.get("/api/v1/sso/saml/sls")
    assert response.status_code == 302
    assert response.headers["location"] == f"{FRONTEND_URL}/login"


@pytest.mark.asyncio
async def test_protocol_routes_enforce_http_methods_not_bearer_auth(client, settings):
    acs_get = await client.get("/api/v1/sso/saml/acs")
    exchange_get = await client.get("/api/v1/sso/saml/exchange", params={"token_id": "token"})
    assert acs_get.status_code == 405
    assert exchange_get.status_code == 405
    assert acs_get.status_code not in {401, 403}
    assert exchange_get.status_code not in {401, 403}


def test_error_redirect_preserves_only_a_safe_generated_id(settings):
    response = saml._saml_error_redirect("safe_id")
    parsed = urlparse(response.headers["location"])
    assert parsed.netloc == "app.example.test"
    assert parse_qs(parsed.query) == {"sso_error": ["safe_id"]}
