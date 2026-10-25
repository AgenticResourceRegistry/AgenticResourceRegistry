# SPDX-FileCopyrightText: 2026 Tanvi Reddy <reddyplayer22@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Focused coverage for admin SSO configuration and diagnostics routes."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

import api.deps as deps
from api.routes import admin_sso as sso
from models.user import UserRole
from schemas.sso_health import make_check
from services.security_events import EventType, Severity

FRONTEND_URL = "https://app.example.test"
CONFIG_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
TOKEN_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
ADMIN_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
SESSION_ID = "diagnostic_session_1"
IDP_CERT = "fake-idp-certificate"
PRIVATE_KEY = "fake-sp-private-key"
ENCRYPTED_KEY = "encrypted-fake-sp-private-key"
SP_CERT = "fake-sp-certificate"
CLIENT_SECRET = "fake-oidc-client-secret"


def _result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _list_result(values):
    result = MagicMock()
    result.scalars.return_value.all.return_value = values
    return result


def _db(*results):
    db = MagicMock()
    db.execute = AsyncMock(side_effect=list(results) if results else [_result(None)])
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.delete = AsyncMock()
    db.add = MagicMock()
    return db


def _user(role=UserRole.admin):
    return SimpleNamespace(
        id=ADMIN_ID,
        email="admin@example.test",
        role=role,
        auth_provider="local",
    )


def _config(**overrides):
    values = {
        "id": CONFIG_ID,
        "idp_entity_id": "https://idp.example.test/entity",
        "idp_sso_url": "https://idp.example.test/sso",
        "idp_slo_url": "https://idp.example.test/slo",
        "idp_x509_cert": IDP_CERT,
        "sp_entity_id": f"{FRONTEND_URL}/api/v1/sso/saml/metadata",
        "sp_acs_url": f"{FRONTEND_URL}/api/v1/sso/saml/acs",
        "sp_private_key_enc": ENCRYPTED_KEY,
        "sp_x509_cert": SP_CERT,
        "external_sp_private_key": None,
        "jit_provisioning": True,
        "default_role": "user",
        "active": True,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 1, 2, tzinfo=UTC),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture(autouse=True)
def disable_rate_limiter(monkeypatch):
    monkeypatch.setattr(sso.limiter, "enabled", False)


@pytest.fixture
def dynamic_settings(monkeypatch):
    state = SimpleNamespace(
        values={"deployment.frontend_url": FRONTEND_URL},
        external=set(),
    )

    def get_sync(key, default=None):
        return state.values.get(key, default)

    def get_sync_bool(key, default=False):
        value = state.values.get(key, default)
        return value if isinstance(value, bool) else str(value).lower() in {"1", "true", "yes"}

    monkeypatch.setattr(sso.ds, "get_sync", get_sync)
    monkeypatch.setattr(sso.ds, "get_sync_bool", get_sync_bool)
    monkeypatch.setattr(sso.ds, "external_setting_keys", lambda: set(state.external))
    monkeypatch.setattr(sso.ds, "is_externally_managed", lambda key: key in state.external)
    monkeypatch.setattr(
        sso.ds,
        "has_external_saml_material",
        lambda: "saml.sp_private_key" in state.external,
    )
    return state


class _RedisStore:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.writes: list[tuple[str, int, str]] = []

    async def setex(self, key: str, ttl: int, value: str):
        self.values[key] = value
        self.writes.append((key, ttl, value))

    async def get(self, key: str):
        return self.values.get(key)


@pytest.fixture
def diagnostic_store(monkeypatch):
    redis = _RedisStore()
    monkeypatch.setattr(sso.sso_diagnostics, "get_redis", lambda: redis)
    monkeypatch.setattr(sso.sso_diagnostics, "new_session_id", lambda: SESSION_ID)
    monkeypatch.setattr(sso.sso_diagnostics, "time", SimpleNamespace(time=lambda: 1_000.0))
    return redis


def _mock_http_client(monkeypatch):
    client = MagicMock(name="health-client")
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(sso.httpx, "AsyncClient", factory)
    return client, factory


async def _http_get_saml_config(user, db):
    app = FastAPI()
    app.include_router(sso.router)

    async def override_db():
        yield db

    app.dependency_overrides[deps.get_db] = override_db
    if user is not None:
        app.dependency_overrides[deps.get_current_user] = lambda: user

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        return await client.get("/api/v1/admin/saml-config")


@pytest.mark.asyncio
async def test_saml_config_read_masks_material_and_reports_sources(dynamic_settings):
    dynamic_settings.values.update(
        {
            "saml.idp_entity_id": "https://dynamic-idp.example.test/entity",
            "saml.idp_sso_url": "https://dynamic-idp.example.test/sso",
            "saml.idp_slo_url": "https://dynamic-idp.example.test/slo",
            "saml.sp_entity_id": "https://sp.example.test/metadata",
            "saml.sp_acs_url": "https://sp.example.test/acs",
            "saml.jit_provisioning": False,
            "saml.default_role": "reviewer",
            "saml.idp_x509_cert": IDP_CERT,
            "saml.sp_private_key": PRIVATE_KEY,
        }
    )
    dynamic_settings.external.update({"saml.idp_x509_cert", "saml.sp_private_key", "saml.sp_x509_cert"})
    dynamic_db = _db(_result(None))

    response = await sso.get_saml_config(db=dynamic_db, current_user=_user())

    assert response == {
        "configured": True,
        "source": "dynamic",
        "idp_entity_id": "https://dynamic-idp.example.test/entity",
        "idp_sso_url": "https://dynamic-idp.example.test/sso",
        "idp_slo_url": "https://dynamic-idp.example.test/slo",
        "sp_entity_id": "https://sp.example.test/metadata",
        "sp_acs_url": "https://sp.example.test/acs",
        "jit_provisioning": False,
        "default_role": "reviewer",
        "has_idp_cert": True,
        "has_sp_key": True,
        "externally_managed": ["saml.idp_x509_cert", "saml.sp_private_key", "saml.sp_x509_cert"],
    }
    assert IDP_CERT not in json.dumps(response)
    assert PRIVATE_KEY not in json.dumps(response)

    stored = _config(idp_x509_cert="", sp_private_key_enc="")
    database_db = _db(_result(stored))
    database_response = await sso.get_saml_config(db=database_db, current_user=_user())

    assert database_response["source"] == "database+files"
    assert database_response["has_idp_cert"] is True
    assert database_response["has_sp_key"] is True
    assert database_response["created_at"] == "2026-01-01T00:00:00+00:00"
    assert database_response["updated_at"] == "2026-01-02T00:00:00+00:00"
    statement = str(database_db.execute.await_args.args[0]).lower()
    assert "saml_configs.active is true" in statement


@pytest.mark.asyncio
async def test_saml_config_read_reports_unconfigured_without_leaking_defaults(dynamic_settings):
    response = await sso.get_saml_config(db=_db(_result(None)), current_user=_user())

    assert response["configured"] is False
    assert response["source"] == "none"
    assert response["idp_entity_id"] is None
    assert response["has_idp_cert"] is False
    assert response["has_sp_key"] is False


@pytest.mark.parametrize(
    ("external", "body", "detail"),
    [
        (
            {"saml.idp_x509_cert"},
            {"idp_entity_id": "entity", "idp_sso_url": "https://idp.example.test/sso", "idp_x509_cert": IDP_CERT},
            "SAML IdP certificate is externally managed by a secret file",
        ),
        (
            {"saml.sp_private_key"},
            {
                "idp_entity_id": "entity",
                "idp_sso_url": "https://idp.example.test/sso",
                "idp_x509_cert": IDP_CERT,
                "regenerate_sp_key": True,
            },
            "SAML SP key and certificate are externally managed by secret files",
        ),
    ],
)
@pytest.mark.asyncio
async def test_saml_update_rejects_external_file_managed_material(dynamic_settings, external, body, detail):
    dynamic_settings.external.update(external)
    db = _db()

    with pytest.raises(HTTPException) as error:
        await sso.upsert_saml_config(body, db=db, current_user=_user())

    assert error.value.status_code == 409
    assert error.value.detail == detail
    db.execute.assert_not_awaited()


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"idp_entity_id": "entity", "idp_sso_url": "https://idp.example.test/sso"},
        {"idp_entity_id": "entity", "idp_x509_cert": IDP_CERT},
    ],
)
@pytest.mark.asyncio
async def test_saml_update_validates_required_fields(dynamic_settings, body):
    with pytest.raises(HTTPException) as error:
        await sso.upsert_saml_config(body, db=_db(), current_user=_user())

    assert error.value.status_code == 422
    assert error.value.detail == "idp_entity_id, idp_sso_url, and idp_x509_cert are required"


@pytest.mark.asyncio
async def test_saml_create_generates_encrypts_persists_and_audits(monkeypatch, dynamic_settings):
    dynamic_settings.values["saml.sp_key_encryption_password"] = "fake-encryption-password"
    db = _db(_result(None))

    async def refresh(config):
        config.id = CONFIG_ID

    db.refresh.side_effect = refresh
    generate = MagicMock(return_value=(PRIVATE_KEY, SP_CERT))
    encrypt = MagicMock(return_value=ENCRYPTED_KEY)
    emit = AsyncMock()
    monkeypatch.setattr(sso, "generate_sp_key_pair", generate)
    monkeypatch.setattr(sso, "encrypt_private_key", encrypt)
    monkeypatch.setattr(sso, "emit_security_event", emit)

    response = await sso.upsert_saml_config(
        {
            "idp_entity_id": "https://idp.example.test/entity",
            "idp_sso_url": "https://idp.example.test/sso",
            "idp_x509_cert": IDP_CERT,
            "active": False,
        },
        db=db,
        current_user=_user(),
    )

    expected_entity = f"{FRONTEND_URL}/api/v1/sso/saml/metadata"
    expected_acs = f"{FRONTEND_URL}/api/v1/sso/saml/acs"
    generate.assert_called_once_with(common_name=expected_entity)
    encrypt.assert_called_once_with(PRIVATE_KEY, "fake-encryption-password")
    config = db.add.call_args.args[0]
    assert config.idp_x509_cert == IDP_CERT
    assert config.sp_private_key_enc == ENCRYPTED_KEY
    assert config.sp_x509_cert == SP_CERT
    assert config.sp_entity_id == expected_entity
    assert config.sp_acs_url == expected_acs
    assert config.jit_provisioning is True
    assert config.default_role == "user"
    assert config.active is False
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(config)
    assert PRIVATE_KEY not in json.dumps(response)
    assert IDP_CERT not in json.dumps(response)
    assert response["message"] == "SAML configuration saved"

    event = emit.await_args.args[0]
    assert event.event_type == EventType.SETTING_CHANGED
    assert event.severity == Severity.WARNING
    assert event.actor_id == str(ADMIN_ID)
    assert event.actor_role == "admin"
    assert event.target_id == str(CONFIG_ID)
    assert event.target_type == "saml_config"
    assert event.detail == "SAML configuration updated"


@pytest.mark.asyncio
async def test_saml_update_preserves_key_and_applies_activation_fields(monkeypatch, dynamic_settings):
    existing = _config(idp_slo_url=None, default_role="reviewer")
    original_key = existing.sp_private_key_enc
    original_cert = existing.sp_x509_cert
    db = _db(_result(existing))
    generate = MagicMock()
    emit = AsyncMock()
    monkeypatch.setattr(sso, "generate_sp_key_pair", generate)
    monkeypatch.setattr(sso, "emit_security_event", emit)

    response = await sso.upsert_saml_config(
        {
            "idp_entity_id": "https://new-idp.example.test/entity",
            "idp_sso_url": "https://new-idp.example.test/sso",
            "idp_x509_cert": "replacement-fake-certificate",
            "jit_provisioning": False,
            "default_role": "user",
            "active": False,
        },
        db=db,
        current_user=_user(),
    )

    assert existing.idp_slo_url == ""
    assert existing.sp_private_key_enc == original_key
    assert existing.sp_x509_cert == original_cert
    assert existing.jit_provisioning is False
    assert existing.default_role == "user"
    assert existing.active is False
    assert response["active"] is False
    generate.assert_not_called()
    db.add.assert_not_called()
    db.commit.assert_awaited_once()
    emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_saml_update_regenerates_encrypted_key(monkeypatch, dynamic_settings):
    dynamic_settings.values["saml.sp_key_encryption_password"] = "fake-encryption-password"
    existing = _config()
    db = _db(_result(existing))
    generate = MagicMock(return_value=("replacement-fake-private-key", "replacement-fake-sp-cert"))
    encrypt = MagicMock(return_value="replacement-encrypted-key")
    monkeypatch.setattr(sso, "generate_sp_key_pair", generate)
    monkeypatch.setattr(sso, "encrypt_private_key", encrypt)
    monkeypatch.setattr(sso, "emit_security_event", AsyncMock())

    await sso.upsert_saml_config(
        {
            "idp_entity_id": existing.idp_entity_id,
            "idp_sso_url": existing.idp_sso_url,
            "idp_x509_cert": IDP_CERT,
            "sp_entity_id": "https://custom-sp.example.test/metadata",
            "sp_acs_url": "https://custom-sp.example.test/acs",
            "regenerate_sp_key": True,
        },
        db=db,
        current_user=_user(),
    )

    generate.assert_called_once_with(common_name="https://custom-sp.example.test/metadata")
    encrypt.assert_called_once_with("replacement-fake-private-key", "fake-encryption-password")
    assert existing.sp_private_key_enc == "replacement-encrypted-key"
    assert existing.sp_x509_cert == "replacement-fake-sp-cert"


@pytest.mark.asyncio
async def test_saml_delete_handles_missing_and_audits_success(monkeypatch, dynamic_settings):
    with pytest.raises(HTTPException) as error:
        await sso.delete_saml_config(db=_db(_result(None)), current_user=_user())
    assert error.value.status_code == 404

    config = _config()
    db = _db(_result(config))
    emit = AsyncMock()
    monkeypatch.setattr(sso, "emit_security_event", emit)

    response = await sso.delete_saml_config(db=db, current_user=_user())

    assert response == {"deleted": str(CONFIG_ID)}
    db.delete.assert_awaited_once_with(config)
    db.commit.assert_awaited_once()
    event = emit.await_args.args[0]
    assert event.severity == Severity.WARNING
    assert event.target_id == str(CONFIG_ID)
    assert event.detail == "SAML configuration deleted"


@pytest.mark.parametrize(
    ("values", "error"),
    [
        ({}, "oauth.client_id or oauth.client_secret not configured"),
        (
            {"oauth.client_id": "client", "oauth.client_secret": CLIENT_SECRET},
            "oauth.server_metadata_url not configured",
        ),
    ],
)
@pytest.mark.asyncio
async def test_validate_oidc_configuration_gates(dynamic_settings, values, error):
    dynamic_settings.values.update(values)

    response = await sso.validate_oidc(current_user=_user())

    assert response["success"] is False
    assert response["error"] == error
    assert response["checks"] == []
    assert CLIENT_SECRET not in json.dumps(response)


@pytest.mark.parametrize("passes", [True, False])
@pytest.mark.asyncio
async def test_validate_oidc_returns_deterministic_health_report(monkeypatch, dynamic_settings, passes):
    metadata_url = "https://idp.example.test/.well-known/openid-configuration"
    dynamic_settings.values.update(
        {
            "oauth.client_id": "client-id",
            "oauth.client_secret": CLIENT_SECRET,
            "oauth.server_metadata_url": metadata_url,
            "deployment.frontend_url": f"{FRONTEND_URL}/",
        }
    )
    checks = [
        make_check(
            "discovery_doc",
            "OIDC discovery",
            "pass" if passes else "fail",
            None if passes else "Discovery failed safely.",
            None if passes else "Check the configured URL.",
        )
    ]
    run_checks = AsyncMock(return_value=(checks, {"issuer": "https://idp.example.test"}))
    monkeypatch.setattr(sso, "run_oidc_checks", run_checks)
    monkeypatch.setattr(sso, "time", SimpleNamespace(monotonic=MagicMock(side_effect=[10.0, 10.012])))

    response = await sso.validate_oidc(current_user=_user())

    assert response["success"] is passes
    assert response["issuer"] == "https://idp.example.test"
    assert response["checks"] == checks
    assert response["latency_ms"] == 12
    if passes:
        assert "error" not in response
    else:
        assert response["error"] == "Discovery failed safely."
        assert response["hint"] == "Check the configured URL."
    run_checks.assert_awaited_once_with(
        metadata_url,
        "client-id",
        CLIENT_SECRET,
        f"{FRONTEND_URL}/api/v1/auth/oauth/callback",
    )
    assert CLIENT_SECRET not in json.dumps(response)


@pytest.mark.asyncio
async def test_runtime_and_required_field_checks_report_login_gates(monkeypatch):
    validator = AsyncMock(side_effect=[[], ["frontend origin invalid", "encryption password missing"]])
    monkeypatch.setattr(sso, "validate_runtime_config_async", validator)

    passed = await sso._runtime_config_check()
    failed = await sso._runtime_config_check()

    assert passed["status"] == "pass"
    assert failed["status"] == "fail"
    assert failed["message"] == "frontend origin invalid; encryption password missing"
    assert "503" in failed["hint"]

    missing = sso._required_field_check(
        _config(idp_entity_id="", sp_private_key_enc="", external_sp_private_key="external-fake-key")
    )
    assert missing["status"] == "fail"
    assert missing["message"] == "Missing: IdP Entity ID."
    assert (
        sso._required_field_check(_config(sp_private_key_enc="", external_sp_private_key="external-fake-key")) is None
    )
    assert sso._first_failure([make_check("ok", "Healthy", "pass")]) == (None, None)


@pytest.mark.asyncio
async def test_validate_saml_handles_absent_incomplete_and_undecryptable_config(monkeypatch, dynamic_settings):
    get_config = AsyncMock(return_value=None)
    monkeypatch.setattr(sso, "_get_saml_config", get_config)
    missing = await sso.validate_saml(db=_db(), current_user=_user())
    assert missing["error"] == "SAML is not configured"

    get_config.return_value = _config(sp_acs_url="")
    incomplete = await sso.validate_saml(db=_db(), current_user=_user())
    assert incomplete["error"] == "Missing: SP ACS URL."
    assert incomplete["checks"][0]["name"] == "required_fields"

    leaked_detail = "fake decryption detail that must stay private"
    get_config.return_value = _config()
    monkeypatch.setattr(sso, "_decrypt_sp_key", MagicMock(side_effect=ValueError(leaked_detail)))
    failed = await sso.validate_saml(db=_db(), current_user=_user())
    assert failed["error"] == "Failed to decrypt SP private key"
    assert leaked_detail not in json.dumps(failed)
    assert failed["checks"][0]["name"] == "sp_key_decrypt"


@pytest.mark.parametrize("passes", [True, False])
@pytest.mark.asyncio
async def test_validate_saml_runs_runtime_and_protocol_health(monkeypatch, dynamic_settings, passes):
    config = _config()
    suite = [
        make_check(
            "idp_reachable",
            "IdP reachable",
            "pass" if passes else "fail",
            None if passes else "IdP did not respond.",
            None if passes else "Check IdP networking.",
        )
    ]
    monkeypatch.setattr(sso, "_get_saml_config", AsyncMock(return_value=config))
    monkeypatch.setattr(sso, "_decrypt_sp_key", MagicMock(return_value=PRIVATE_KEY))
    monkeypatch.setattr(sso, "_run_saml_check_suite", AsyncMock(return_value=suite))
    monkeypatch.setattr(
        sso,
        "_runtime_config_check",
        AsyncMock(return_value=make_check("runtime_config", "Runtime config", "pass")),
    )
    monkeypatch.setattr(sso, "time", SimpleNamespace(monotonic=MagicMock(side_effect=[20.0, 20.009])))
    client, factory = _mock_http_client(monkeypatch)

    response = await sso.validate_saml(db=_db(), current_user=_user())

    assert response["success"] is passes
    assert response["idp_entity_id"] == config.idp_entity_id
    assert response["latency_ms"] == 9
    assert [check["name"] for check in response["checks"][:2]] == ["runtime_config", "sp_key_decrypt"]
    if passes:
        assert "error" not in response
    else:
        assert response["error"] == "IdP did not respond."
        assert response["hint"] == "Check IdP networking."
    factory.assert_called_once_with(timeout=10.0, follow_redirects=False)
    sso._run_saml_check_suite.assert_awaited_once_with(config, PRIVATE_KEY, FRONTEND_URL, client)


@pytest.mark.asyncio
async def test_oidc_e2e_start_reports_preflight_failures_without_staging(monkeypatch, dynamic_settings):
    dynamic_settings.values.update(
        {
            "oauth.client_id": "client-id",
            "oauth.client_secret": CLIENT_SECRET,
            "oauth.server_metadata_url": "https://idp.example.test/discovery",
        }
    )
    failure = make_check("authorization_endpoint", "Authorization", "fail", "Redirect rejected.", "Fix redirect URI.")
    monkeypatch.setattr(sso, "run_oidc_checks", AsyncMock(return_value=([failure], {})))
    create = AsyncMock()
    monkeypatch.setattr(sso.sso_diagnostics, "create_session", create)

    response = await sso.e2e_oidc_start(request=MagicMock(), current_user=_user())

    assert response["success"] is False
    assert response["error"] == "Redirect rejected."
    assert response["hint"] == "Fix redirect URI."
    create.assert_not_awaited()


@pytest.mark.parametrize(
    ("settings", "metadata", "error"),
    [
        ({}, None, "OIDC is not configured on the server"),
        (
            {
                "oauth.client_id": "client-id",
                "oauth.server_metadata_url": "https://idp.example.test/discovery",
            },
            None,
            "OIDC discovery document missing despite passing probes",
        ),
        (
            {
                "oauth.client_id": "client-id",
                "oauth.server_metadata_url": "https://idp.example.test/discovery",
            },
            {"issuer": "https://idp.example.test"},
            "Discovery document is missing authorization_endpoint",
        ),
    ],
)
@pytest.mark.asyncio
async def test_oidc_e2e_start_handles_configuration_and_discovery_gates(
    monkeypatch,
    dynamic_settings,
    settings,
    metadata,
    error,
):
    dynamic_settings.values.update(settings)
    checks = [make_check("discovery", "Discovery", "pass")]
    run_checks = AsyncMock(return_value=(checks, metadata))
    monkeypatch.setattr(sso, "run_oidc_checks", run_checks)

    response = await sso.e2e_oidc_start(request=MagicMock(), current_user=_user())

    assert response["success"] is False
    assert response["error"] == error
    if not settings:
        run_checks.assert_not_awaited()


@pytest.mark.asyncio
async def test_oidc_e2e_start_persists_redis_session_and_returns_safe_status(
    monkeypatch,
    dynamic_settings,
    diagnostic_store,
):
    metadata_url = "https://idp.example.test/discovery"
    authorization_endpoint = "https://idp.example.test/authorize"
    dynamic_settings.values.update(
        {
            "oauth.client_id": "client-id",
            "oauth.client_secret": CLIENT_SECRET,
            "oauth.server_metadata_url": metadata_url,
            "deployment.frontend_url": f"{FRONTEND_URL}/",
        }
    )
    checks = [make_check("discovery", "Discovery", "pass")]
    metadata = {
        "authorization_endpoint": authorization_endpoint,
        "token_endpoint": "https://idp.example.test/token",
        "jwks_uri": "https://idp.example.test/jwks",
        "issuer": "https://idp.example.test",
    }
    monkeypatch.setattr(sso, "run_oidc_checks", AsyncMock(return_value=(checks, metadata)))
    monkeypatch.setattr(sso, "secrets", SimpleNamespace(token_urlsafe=lambda _size: "fake-diagnostic-nonce"))

    response = await sso.e2e_oidc_start(request=MagicMock(), current_user=_user())

    assert response["success"] is True
    assert response["session_id"] == SESSION_ID
    assert response["issuer"] == metadata["issuer"]
    query = parse_qs(urlparse(response["login_url"]).query)
    assert query == {
        "response_type": ["code"],
        "client_id": ["client-id"],
        "redirect_uri": [f"{FRONTEND_URL}/api/v1/auth/oauth/callback"],
        "scope": ["openid email profile groups"],
        "state": [f"__e2e:{SESSION_ID}"],
        "nonce": ["fake-diagnostic-nonce"],
    }
    assert CLIENT_SECRET not in json.dumps(response)

    redis_key = f"sso_diag:{SESSION_ID}"
    stored = json.loads(diagnostic_store.values[redis_key])
    assert stored["nonce"] == "fake-diagnostic-nonce"
    assert stored["finished_at"] is None
    assert stored["ok"] is None
    assert diagnostic_store.writes

    public = await sso.e2e_status(SESSION_ID, current_user=_user())
    assert public["session_id"] == SESSION_ID
    assert "nonce" not in public
    assert "token_endpoint" not in public
    assert CLIENT_SECRET not in json.dumps(public)


@pytest.mark.asyncio
async def test_saml_e2e_start_handles_config_field_and_key_gates(monkeypatch, dynamic_settings):
    get_config = AsyncMock(return_value=None)
    monkeypatch.setattr(sso, "_get_saml_config", get_config)

    missing = await sso.e2e_saml_start(request=MagicMock(), db=_db(), current_user=_user())
    assert missing["error"] == "SAML is not configured"

    get_config.return_value = _config(idp_x509_cert="")
    incomplete = await sso.e2e_saml_start(request=MagicMock(), db=_db(), current_user=_user())
    assert incomplete["error"] == "Missing: IdP X.509 certificate."

    leaked_detail = "fake private decryption failure"
    get_config.return_value = _config()
    monkeypatch.setattr(sso, "_decrypt_sp_key", MagicMock(side_effect=ValueError(leaked_detail)))
    decrypt_failure = await sso.e2e_saml_start(request=MagicMock(), db=_db(), current_user=_user())
    assert decrypt_failure["error"] == "Failed to decrypt SP private key"
    assert leaked_detail not in json.dumps(decrypt_failure)


@pytest.mark.asyncio
async def test_saml_e2e_start_stops_on_preflight_failure(monkeypatch, dynamic_settings):
    failure = make_check("runtime_config", "Runtime config", "fail", "Login gate is closed.", "Fix config.")
    monkeypatch.setattr(sso, "_get_saml_config", AsyncMock(return_value=_config()))
    monkeypatch.setattr(sso, "_decrypt_sp_key", MagicMock(return_value=PRIVATE_KEY))
    monkeypatch.setattr(sso, "_run_saml_check_suite", AsyncMock(return_value=[]))
    monkeypatch.setattr(sso, "_runtime_config_check", AsyncMock(return_value=failure))
    create = AsyncMock()
    monkeypatch.setattr(sso.sso_diagnostics, "create_session", create)
    _mock_http_client(monkeypatch)

    response = await sso.e2e_saml_start(request=MagicMock(), db=_db(), current_user=_user())

    assert response["success"] is False
    assert response["error"] == "Login gate is closed."
    assert response["hint"] == "Fix config."
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_saml_e2e_start_persists_session_and_builds_controlled_login_url(
    monkeypatch,
    dynamic_settings,
    diagnostic_store,
):
    config = _config()
    protocol_checks = [make_check("idp_reachable", "IdP reachable", "pass")]
    monkeypatch.setattr(sso, "_get_saml_config", AsyncMock(return_value=config))
    monkeypatch.setattr(sso, "_decrypt_sp_key", MagicMock(return_value=PRIVATE_KEY))
    monkeypatch.setattr(sso, "_run_saml_check_suite", AsyncMock(return_value=protocol_checks))
    monkeypatch.setattr(
        sso,
        "_runtime_config_check",
        AsyncMock(return_value=make_check("runtime_config", "Runtime config", "pass")),
    )
    client, factory = _mock_http_client(monkeypatch)
    dynamic_settings.values["deployment.frontend_url"] = f"{FRONTEND_URL}/"

    response = await sso.e2e_saml_start(request=MagicMock(), db=_db(), current_user=_user())

    assert response["success"] is True
    assert response["session_id"] == SESSION_ID
    assert response["login_url"] == f"{FRONTEND_URL}/api/v1/sso/saml/login?e2e={SESSION_ID}"
    assert response["idp_entity_id"] == config.idp_entity_id
    factory.assert_called_once_with(timeout=10.0, follow_redirects=False)
    sso._run_saml_check_suite.assert_awaited_once_with(config, PRIVATE_KEY, f"{FRONTEND_URL}/", client)

    stored = json.loads(diagnostic_store.values[f"sso_diag:{SESSION_ID}"])
    assert stored["finished_at"] is None
    assert stored["ok"] is None
    assert [check["name"] for check in stored["checks"]] == [
        "runtime_config",
        "sp_key_decrypt",
        "idp_reachable",
        "e2e_started",
    ]


@pytest.mark.parametrize("provider", ["oidc", "saml"])
@pytest.mark.asyncio
async def test_e2e_start_tolerates_session_expiry_before_enrichment(
    monkeypatch,
    dynamic_settings,
    provider,
):
    create = AsyncMock(return_value=(SESSION_ID, {}))
    finalize = AsyncMock()
    get_session = AsyncMock(return_value=None)
    save_session = AsyncMock()
    monkeypatch.setattr(sso.sso_diagnostics, "create_session", create)
    monkeypatch.setattr(sso.sso_diagnostics, "finalize", finalize)
    monkeypatch.setattr(sso.sso_diagnostics, "get_session", get_session)
    monkeypatch.setattr(sso.sso_diagnostics, "save_session", save_session)

    if provider == "oidc":
        dynamic_settings.values.update(
            {
                "oauth.client_id": "client-id",
                "oauth.client_secret": CLIENT_SECRET,
                "oauth.server_metadata_url": "https://idp.example.test/discovery",
            }
        )
        metadata = {
            "authorization_endpoint": "https://idp.example.test/authorize",
            "issuer": "https://idp.example.test",
        }
        monkeypatch.setattr(
            sso,
            "run_oidc_checks",
            AsyncMock(return_value=([make_check("discovery", "Discovery", "pass")], metadata)),
        )
        monkeypatch.setattr(sso, "secrets", SimpleNamespace(token_urlsafe=lambda _size: "fake-nonce"))
        response = await sso.e2e_oidc_start(request=MagicMock(), current_user=_user())
    else:
        monkeypatch.setattr(sso, "_get_saml_config", AsyncMock(return_value=_config()))
        monkeypatch.setattr(sso, "_decrypt_sp_key", MagicMock(return_value=PRIVATE_KEY))
        monkeypatch.setattr(sso, "_run_saml_check_suite", AsyncMock(return_value=[]))
        monkeypatch.setattr(
            sso,
            "_runtime_config_check",
            AsyncMock(return_value=make_check("runtime_config", "Runtime config", "pass")),
        )
        _mock_http_client(monkeypatch)
        response = await sso.e2e_saml_start(request=MagicMock(), db=_db(), current_user=_user())

    assert response["success"] is True
    finalize.assert_awaited_once()
    get_session.assert_awaited_once_with(SESSION_ID)
    save_session.assert_not_awaited()


@pytest.mark.parametrize("session_id", ["", "bad/id", "bad id", "a" * 65])
@pytest.mark.asyncio
async def test_e2e_status_rejects_invalid_ids(session_id):
    with pytest.raises(HTTPException) as error:
        await sso.e2e_status(session_id, current_user=_user())
    assert error.value.status_code == 400
    assert error.value.detail == "Invalid session id"


@pytest.mark.asyncio
async def test_e2e_status_returns_safe_not_found(monkeypatch):
    monkeypatch.setattr(sso.sso_diagnostics, "get_session", AsyncMock(return_value=None))

    with pytest.raises(HTTPException) as error:
        await sso.e2e_status("missing_session", current_user=_user())

    assert error.value.status_code == 404
    assert error.value.detail == "Session not found or expired"


@pytest.mark.asyncio
async def test_scim_token_list_masks_hash_and_handles_missing_timestamp(dynamic_settings):
    tokens = [
        SimpleNamespace(
            id=TOKEN_ID,
            description="Primary provisioning token",
            active=True,
            created_at=datetime(2026, 2, 1, tzinfo=UTC),
            token_hash="abcdef1234567890",
        ),
        SimpleNamespace(
            id=uuid.UUID("44444444-4444-4444-4444-444444444444"),
            description="Pending token",
            active=False,
            created_at=None,
            token_hash="1234567890abcdef",
        ),
    ]

    response = await sso.list_scim_tokens(db=_db(_list_result(tokens)), current_user=_user())

    assert response[0]["token_prefix"] == "abcdef12..."
    assert response[0]["created_at"] == "2026-02-01T00:00:00+00:00"
    assert response[1]["created_at"] is None
    assert all("token" not in item for item in response)
    assert "abcdef1234567890" not in json.dumps(response)


@pytest.mark.asyncio
async def test_scim_token_create_hashes_persists_and_audits(monkeypatch, dynamic_settings):
    raw_token = "fake-one-time-scim-token"
    db = _db()

    async def refresh(token):
        token.id = TOKEN_ID

    db.refresh.side_effect = refresh
    emit = AsyncMock()
    monkeypatch.setattr(sso, "secrets", SimpleNamespace(token_urlsafe=lambda _size: raw_token))
    monkeypatch.setattr(sso, "emit_security_event", emit)

    response = await sso.create_scim_token(
        {"description": "Identity provider provisioning"},
        db=db,
        current_user=_user(),
    )

    stored = db.add.call_args.args[0]
    assert stored.token_hash == hashlib.sha256(raw_token.encode()).hexdigest()
    assert raw_token not in stored.token_hash
    assert stored.description == "Identity provider provisioning"
    assert stored.active is True
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(stored)
    assert response["token"] == raw_token
    assert response["message"] == "Save this token now. It will not be shown again."

    event = emit.await_args.args[0]
    assert event.event_type == EventType.SETTING_CHANGED
    assert event.severity == Severity.INFO
    assert event.target_id == str(TOKEN_ID)
    assert event.target_type == "scim_token"
    assert event.detail == "SCIM token created"


@pytest.mark.asyncio
async def test_scim_token_revoke_validates_id_and_audits(monkeypatch, dynamic_settings):
    invalid_db = _db()
    with pytest.raises(HTTPException) as error:
        await sso.revoke_scim_token("not-a-uuid", db=invalid_db, current_user=_user())
    assert error.value.status_code == 404
    invalid_db.execute.assert_not_awaited()

    with pytest.raises(HTTPException) as error:
        await sso.revoke_scim_token(str(TOKEN_ID), db=_db(_result(None)), current_user=_user())
    assert error.value.status_code == 404

    token = SimpleNamespace(id=TOKEN_ID, active=True)
    db = _db(_result(token))
    emit = AsyncMock()
    monkeypatch.setattr(sso, "emit_security_event", emit)

    response = await sso.revoke_scim_token(str(TOKEN_ID), db=db, current_user=_user())

    assert response == {"revoked": str(TOKEN_ID)}
    assert token.active is False
    db.commit.assert_awaited_once()
    event = emit.await_args.args[0]
    assert event.severity == Severity.WARNING
    assert event.target_id == str(TOKEN_ID)
    assert event.detail == "SCIM token revoked"


@pytest.mark.parametrize(
    ("role", "status"),
    [
        (UserRole.super_admin, 200),
        (UserRole.admin, 200),
        (UserRole.reviewer, 403),
        (UserRole.user, 403),
    ],
)
@pytest.mark.asyncio
async def test_admin_sso_routes_enforce_role_hierarchy(monkeypatch, dynamic_settings, role, status):
    denied = AsyncMock()
    monkeypatch.setattr(deps, "emit_security_event", denied)

    response = await _http_get_saml_config(_user(role), _db(_result(None)))

    assert response.status_code == status
    if status == 403:
        assert response.json()["detail"] == "Insufficient permissions"
        event = denied.await_args.args[0]
        assert event.event_type == EventType.PERMISSION_DENIED
        assert event.actor_role == role.value
    else:
        denied.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_sso_routes_require_authentication(dynamic_settings):
    response = await _http_get_saml_config(None, _db(_result(None)))

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing credentials"
