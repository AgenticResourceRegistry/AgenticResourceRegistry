# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Focused coverage for enterprise settings administration routes."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

import api.deps as deps_module
from api.deps import get_current_user, get_db
from api.routes.admin import enterprise_settings as es
from models.enterprise_config import RESTART_PENDING_KEY, EnterpriseConfig
from models.user import UserRole
from schemas.admin import EnterpriseConfigUpdate
from services.secrets_redactor import REDACTED
from services.security_events import EventType, Severity

ADMIN_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")


def _actor(role: UserRole = UserRole.admin) -> SimpleNamespace:
    return SimpleNamespace(id=ADMIN_ID, email="admin@example.test", role=role)


def _one(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _many(values):
    result = MagicMock()
    result.scalars.return_value.all.return_value = list(values)
    return result


def _db(*results):
    db = MagicMock()
    db.execute = AsyncMock(side_effect=list(results)) if results else AsyncMock()
    db.scalar = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.delete = AsyncMock()
    db.add = MagicMock()
    return db


def _override_db(app: FastAPI, db) -> None:
    async def dependency():
        yield db

    app.dependency_overrides[get_db] = dependency


@pytest.fixture
def route_app() -> FastAPI:
    app = FastAPI()
    app.include_router(es.router)
    return app


@pytest.fixture
def boundaries(monkeypatch):
    invalidate = AsyncMock()
    refresh = AsyncMock()
    emit = AsyncMock()
    monkeypatch.setattr(es.ds, "invalidate", invalidate)
    monkeypatch.setattr(es.ds, "refresh_sync_cache", refresh)
    monkeypatch.setattr(es, "emit_security_event", emit)
    return SimpleNamespace(invalidate=invalidate, refresh=refresh, emit=emit)


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 7, 15, 20, 0, tzinfo=tz)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_value", "existing_keys"),
    [
        (None, []),
        (json.dumps({"keys": ["google.client_id"]}), ["google.client_id"]),
        ("not-json", []),
        (json.dumps([]), []),
    ],
)
async def test_mark_restart_pending_creates_or_repairs_marker(monkeypatch, raw_value, existing_keys):
    marker = None if raw_value is None else SimpleNamespace(value=raw_value)
    db = _db(_one(marker))
    monkeypatch.setattr(es, "datetime", FrozenDateTime)

    await es._mark_restart_pending(db, "oauth.client_secret")

    expected = json.dumps(
        {
            "changed_at": "2026-07-15T20:00:00+00:00",
            "keys": sorted([*existing_keys, "oauth.client_secret"]),
        }
    )
    if marker is None:
        db.add.assert_called_once()
        added = db.add.call_args.args[0]
        assert isinstance(added, EnterpriseConfig)
        assert added.key == RESTART_PENDING_KEY
        assert added.value == expected
    else:
        db.add.assert_not_called()
        assert marker.value == expected


@pytest.mark.asyncio
async def test_diagnostics_reports_database_keys_and_degraded_runtime(monkeypatch):
    import services.crypto as crypto

    db = _db(MagicMock())
    db.scalar.side_effect = [4, 1]
    get_key_manager = MagicMock(return_value=object())
    monkeypatch.setattr(crypto, "get_key_manager", get_key_manager)
    monkeypatch.setattr(es, "settings", SimpleNamespace(JWT_SIGNING_ALGORITHM="ES256"))
    monkeypatch.setattr(es, "validate_runtime_config_async", AsyncMock(return_value=["weak setting"]))
    monkeypatch.setattr(es.ds, "get_bool", AsyncMock(return_value=True))
    monkeypatch.setattr(es.ds, "get", AsyncMock(return_value="oidc-client"))

    response = await es.diagnostics(db=db, current_user=_actor())

    assert response == {
        "status": "degraded",
        "checks": {
            "database": {"status": "ok", "users": 4, "demo_accounts": 1},
            "jwt_keys": {"status": "ok", "algorithm": "ES256"},
            "runtime_config": {
                "status": "misconfigured",
                "sso_only": True,
                "sso_configured": True,
                "issues": ["weak setting"],
            },
        },
    }
    get_key_manager.assert_called_once_with()


@pytest.mark.asyncio
async def test_diagnostics_reports_owned_failures_without_exposing_them(monkeypatch):
    import services.crypto as crypto

    db = _db()
    db.execute.side_effect = RuntimeError("database unavailable")
    get_key_manager = MagicMock(side_effect=RuntimeError("keys unavailable"))
    monkeypatch.setattr(crypto, "get_key_manager", get_key_manager)
    monkeypatch.setattr(es, "settings", SimpleNamespace(JWT_SIGNING_ALGORITHM="ES256"))
    monkeypatch.setattr(es, "validate_runtime_config_async", AsyncMock(return_value=[]))
    monkeypatch.setattr(es.ds, "get_bool", AsyncMock(return_value=False))
    monkeypatch.setattr(es.ds, "get", AsyncMock(return_value=""))

    response = await es.diagnostics(db=db, current_user=_actor())

    assert response == {
        "status": "unhealthy",
        "checks": {
            "database": {"status": "error", "detail": "Database health check failed."},
            "jwt_keys": {"status": "missing", "algorithm": "ES256"},
            "runtime_config": {
                "status": "ok",
                "sso_only": False,
                "sso_configured": False,
                "issues": [],
            },
        },
    }
    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_system_warnings_returns_exact_weak_key_and_demo_warnings(monkeypatch):
    db = _db()
    db.scalar.return_value = 3
    monkeypatch.setattr(es, "settings", SimpleNamespace(SECRET_KEY="short"))

    response = await es.system_warnings(db=db, current_user=_actor())

    assert response == [
        {
            "level": "critical",
            "code": "weak_secret_key",
            "message": "SECRET_KEY is insecure. Set a random string of at least 32 characters.",
        },
        {
            "level": "warning",
            "code": "demo_accounts_active",
            "message": (
                "3 demo account(s) are still active. Remove them or change their passwords before going to production."
            ),
        },
    ]


@pytest.mark.asyncio
async def test_system_warnings_is_empty_for_strong_key_without_demo_users(monkeypatch):
    db = _db()
    db.scalar.return_value = 0
    monkeypatch.setattr(es, "settings", SimpleNamespace(SECRET_KEY="s" * 32))

    assert await es.system_warnings(db=db, current_user=_actor()) == []


@pytest.mark.asyncio
async def test_settings_schema_returns_dynamic_schema(monkeypatch):
    schema = [{"id": "data", "settings": [{"key": "data.retention_days"}]}]
    settings_schema = MagicMock(return_value=schema)
    monkeypatch.setattr(es.ds, "settings_schema", settings_schema)

    assert await es.settings_schema(_current_user=_actor()) == schema
    settings_schema.assert_called_once_with()


@pytest.mark.asyncio
async def test_settings_http_authentication_and_role_guards(route_app, monkeypatch):
    db = _db(_many([]), _many([]))
    _override_db(route_app, db)
    permission_event = AsyncMock()
    monkeypatch.setattr(deps_module, "emit_security_event", permission_event)

    async with AsyncClient(transport=ASGITransport(app=route_app), base_url="http://test") as client:
        unauthenticated = await client.get("/api/v1/admin/settings")
        route_app.dependency_overrides[get_current_user] = lambda: _actor(UserRole.user)
        forbidden = await client.get("/api/v1/admin/settings")
        route_app.dependency_overrides[get_current_user] = lambda: _actor(UserRole.admin)
        admin = await client.get("/api/v1/admin/settings")
        route_app.dependency_overrides[get_current_user] = lambda: _actor(UserRole.super_admin)
        super_admin = await client.get("/api/v1/admin/settings")

    assert unauthenticated.status_code == 401
    assert unauthenticated.json() == {"detail": "Missing credentials"}
    assert forbidden.status_code == 403
    assert forbidden.json() == {"detail": "Insufficient permissions"}
    assert admin.status_code == 200
    assert admin.json() == []
    assert super_admin.status_code == 200
    assert super_admin.json() == []
    denied = permission_event.await_args.args[0]
    assert denied.event_type is EventType.PERMISSION_DENIED
    assert denied.actor_role == "user"


@pytest.mark.asyncio
async def test_settings_http_error_contracts_and_no_mutation(route_app, boundaries, monkeypatch):
    db = _db()
    db.execute.return_value = _one(None)
    _override_db(route_app, db)
    route_app.dependency_overrides[get_current_user] = lambda: _actor()
    monkeypatch.setattr(es.ds, "is_externally_managed", lambda key: key == "oauth.client_secret")

    async with AsyncClient(transport=ASGITransport(app=route_app), base_url="http://test") as client:
        responses = [
            await client.get("/api/v1/admin/settings/saml.sp_private_key"),
            await client.get("/api/v1/admin/settings/unknown.setting"),
            await client.put("/api/v1/admin/settings/saml.sp_private_key", json={"value": "replacement"}),
            await client.put("/api/v1/admin/settings/oauth.client_secret", json={"value": "replacement"}),
            await client.delete("/api/v1/admin/settings/oauth.client_secret"),
            await client.delete("/api/v1/admin/settings/unknown.setting"),
            await client.post("/api/v1/admin/settings/deployment.public_url/revoke"),
            await client.post("/api/v1/admin/settings/oauth.client_secret/revoke"),
            await client.post("/api/v1/admin/settings/insights.api_key/revoke"),
        ]
        malformed = await client.put("/api/v1/admin/settings/data.retention_days", json={"value": 30})

    assert [(response.status_code, response.json()) for response in responses] == [
        (404, {"detail": "Setting not found"}),
        (404, {"detail": "Setting not found"}),
        (409, {"detail": "Setting can only be managed through dedicated files"}),
        (409, {"detail": "Setting is externally managed by a secret file"}),
        (409, {"detail": "Setting is externally managed by a secret file"}),
        (404, {"detail": "Setting not found"}),
        (400, {"detail": "Only sensitive keys can be revoked"}),
        (409, {"detail": "Setting is externally managed by a secret file"}),
        (404, {"detail": "Setting not found or already revoked"}),
    ]
    assert malformed.status_code == 422
    assert malformed.json()["detail"][0]["loc"] == ["body", "value"]
    db.add.assert_not_called()
    db.delete.assert_not_awaited()
    db.commit.assert_not_awaited()
    boundaries.invalidate.assert_not_awaited()
    boundaries.refresh.assert_not_awaited()
    boundaries.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_settings_masks_secrets_and_merges_external_defaults(monkeypatch):
    rows = [
        SimpleNamespace(key=RESTART_PENDING_KEY, value="ignored"),
        SimpleNamespace(key="deployment.public_url", value="https://db.example.test"),
        SimpleNamespace(key="oauth.client_secret", value="enc:old-ciphertext"),
        SimpleNamespace(key="github.client_secret", value=""),
        SimpleNamespace(key="oauth.client_id", value="stale-db-client"),
        SimpleNamespace(key="google.client_secret", value=""),
    ]
    db = _db(_many(rows))
    external = {
        "oauth.client_id",
        "google.client_secret",
        "deployment.frontend_url",
        "insights.api_key",
        "saml.sp_private_key",
        "unknown.external",
    }
    external_values = {
        "oauth.client_id": "file-client",
        "google.client_secret": "file-google-secret",
        "deployment.frontend_url": "https://file.example.test",
        "insights.api_key": "file-insights-secret",
    }
    monkeypatch.setattr(es.ds, "is_externally_managed", external.__contains__)
    monkeypatch.setattr(es.ds, "external_setting_keys", lambda: set(external))
    get_sync = MagicMock(side_effect=lambda key: external_values[key])
    monkeypatch.setattr(es.ds, "get_sync", get_sync)

    response = await es.list_settings(db=db, current_user=_actor())

    assert [item.model_dump() for item in response] == [
        {
            "key": "deployment.public_url",
            "value": "https://db.example.test",
            "is_sensitive": False,
            "is_set": True,
            "is_externally_managed": False,
        },
        {
            "key": "oauth.client_secret",
            "value": REDACTED,
            "is_sensitive": True,
            "is_set": True,
            "is_externally_managed": False,
        },
        {
            "key": "github.client_secret",
            "value": "",
            "is_sensitive": True,
            "is_set": False,
            "is_externally_managed": False,
        },
        {
            "key": "oauth.client_id",
            "value": "file-client",
            "is_sensitive": False,
            "is_set": True,
            "is_externally_managed": True,
        },
        {
            "key": "google.client_secret",
            "value": REDACTED,
            "is_sensitive": True,
            "is_set": True,
            "is_externally_managed": True,
        },
        {
            "key": "deployment.frontend_url",
            "value": "https://file.example.test",
            "is_sensitive": False,
            "is_set": True,
            "is_externally_managed": True,
        },
        {
            "key": "insights.api_key",
            "value": REDACTED,
            "is_sensitive": True,
            "is_set": True,
            "is_externally_managed": True,
        },
    ]
    assert get_sync.call_args_list == [
        call("oauth.client_id"),
        call("google.client_secret"),
        call("deployment.frontend_url"),
    ]


@pytest.mark.asyncio
async def test_get_setting_returns_external_plain_and_masks_all_secret_storage(monkeypatch):
    external = {"deployment.frontend_url"}
    monkeypatch.setattr(es.ds, "is_externally_managed", external.__contains__)
    get_sync = MagicMock(return_value="https://external.example.test")
    monkeypatch.setattr(es.ds, "get_sync", get_sync)
    decrypt = MagicMock(side_effect=AssertionError("route must never decrypt settings"))
    monkeypatch.setattr(es.ds, "decrypt_value", decrypt)

    external_response = await es.get_setting("deployment.frontend_url", db=_db(_one(None)), current_user=_actor())
    secret_response = await es.get_setting(
        "oauth.client_secret",
        db=_db(_one(SimpleNamespace(value="enc:ciphertext"))),
        current_user=_actor(),
    )
    empty_secret_response = await es.get_setting(
        "github.client_secret", db=_db(_one(SimpleNamespace(value=""))), current_user=_actor()
    )
    plain_response = await es.get_setting(
        "data.retention_days", db=_db(_one(SimpleNamespace(value="90"))), current_user=_actor()
    )

    assert external_response.model_dump() == {
        "key": "deployment.frontend_url",
        "value": "https://external.example.test",
        "is_sensitive": False,
        "is_set": True,
        "is_externally_managed": True,
    }
    assert secret_response.value == REDACTED
    assert secret_response.is_set is True
    assert empty_secret_response.value == ""
    assert empty_secret_response.is_set is False
    assert plain_response.value == "90"
    assert plain_response.is_sensitive is False
    get_sync.assert_called_once_with("deployment.frontend_url")
    decrypt.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_retention_setting_normalizes_persists_and_refreshes_cache(boundaries, monkeypatch):
    db = _db(_one(None))
    monkeypatch.setattr(es.ds, "is_externally_managed", lambda _key: False)

    response = await es.upsert_setting(
        "data.retention_days",
        EnterpriseConfigUpdate(value="  45 \n"),
        db=db,
        current_user=_actor(),
    )

    db.add.assert_called_once()
    stored = db.add.call_args.args[0]
    assert stored.key == "data.retention_days"
    assert stored.value == "45"
    db.commit.assert_awaited_once_with()
    db.refresh.assert_awaited_once_with(stored)
    boundaries.invalidate.assert_awaited_once_with("data.retention_days")
    boundaries.refresh.assert_awaited_once_with()
    event = boundaries.emit.await_args.args[0]
    assert event.event_type is EventType.SETTING_CHANGED
    assert event.severity is Severity.WARNING
    assert event.actor_id == str(ADMIN_ID)
    assert event.actor_email == "admin@example.test"
    assert event.actor_role == "admin"
    assert event.target_id == "data.retention_days"
    assert event.target_type == "setting"
    assert response.model_dump() == {
        "key": "data.retention_days",
        "value": "45",
        "is_sensitive": False,
        "is_set": True,
        "is_externally_managed": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["resource.max_query_memory_mb", "custom.unknown_setting"])
async def test_upsert_accepts_resource_map_and_unknown_keys_without_implicit_apply(key, boundaries, monkeypatch):
    import services.clickhouse as clickhouse

    db = _db(_one(None))
    apply_resources = AsyncMock()
    monkeypatch.setattr(clickhouse, "apply_resource_settings", apply_resources)
    monkeypatch.setattr(es.ds, "is_externally_managed", lambda _key: False)

    response = await es.upsert_setting(key, EnterpriseConfigUpdate(value=" 300 "), db=db, current_user=_actor())

    stored = db.add.call_args.args[0]
    assert (stored.key, stored.value) == (key, "300")
    assert response.value == "300"
    boundaries.invalidate.assert_awaited_once_with(key)
    boundaries.refresh.assert_awaited_once_with()
    apply_resources.assert_not_awaited()


@pytest.mark.asyncio
async def test_sensitive_upsert_reencrypts_at_boundary_and_never_exposes_ciphertext(boundaries, monkeypatch):
    cfg = SimpleNamespace(key="oauth.client_secret", value="enc:old-ciphertext")
    marker = SimpleNamespace(value=json.dumps({"keys": ["oauth.client_id"]}))
    db = _db(_one(cfg), _one(marker))
    encrypt = MagicMock(return_value="enc:new-ciphertext")
    decrypt = MagicMock(side_effect=AssertionError("route must not decrypt old ciphertext"))
    monkeypatch.setattr(es.ds, "is_externally_managed", lambda _key: False)
    monkeypatch.setattr(es.ds, "encrypt_value", encrypt)
    monkeypatch.setattr(es.ds, "decrypt_value", decrypt)
    monkeypatch.setattr(es, "datetime", FrozenDateTime)

    response = await es.upsert_setting(
        "oauth.client_secret",
        EnterpriseConfigUpdate(value="  rotated-secret  "),
        db=db,
        current_user=_actor(),
    )

    encrypt.assert_called_once_with("rotated-secret")
    decrypt.assert_not_called()
    assert cfg.value == "enc:new-ciphertext"
    assert json.loads(marker.value) == {
        "changed_at": "2026-07-15T20:00:00+00:00",
        "keys": ["oauth.client_id", "oauth.client_secret"],
    }
    assert response.value == REDACTED
    assert response.is_sensitive is True
    assert response.is_set is True
    assert "new-ciphertext" not in response.model_dump_json()
    db.commit.assert_awaited_once_with()
    boundaries.invalidate.assert_awaited_once_with("oauth.client_secret")


@pytest.mark.asyncio
async def test_encryption_failure_is_loud_and_has_no_database_or_cache_mutation(boundaries, monkeypatch):
    db = _db()
    monkeypatch.setattr(es.ds, "is_externally_managed", lambda _key: False)
    monkeypatch.setattr(es.ds, "encrypt_value", MagicMock(side_effect=RuntimeError("key rotation unavailable")))

    with pytest.raises(RuntimeError, match="key rotation unavailable"):
        await es.upsert_setting(
            "oauth.client_secret",
            EnterpriseConfigUpdate(value="secret"),
            db=db,
            current_user=_actor(),
        )

    db.execute.assert_not_awaited()
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
    boundaries.invalidate.assert_not_awaited()
    boundaries.refresh.assert_not_awaited()
    boundaries.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_unchanged_restart_setting_does_not_mark_restart(boundaries, monkeypatch):
    cfg = SimpleNamespace(key="oauth.client_id", value="same-client")
    db = _db(_one(cfg))
    monkeypatch.setattr(es.ds, "is_externally_managed", lambda _key: False)

    response = await es.upsert_setting(
        "oauth.client_id", EnterpriseConfigUpdate(value="same-client"), db=db, current_user=_actor()
    )

    assert response.value == "same-client"
    assert db.execute.await_count == 1
    db.add.assert_not_called()
    db.commit.assert_awaited_once_with()
    boundaries.invalidate.assert_awaited_once_with("oauth.client_id")


@pytest.mark.asyncio
async def test_insights_api_key_update_removes_legacy_credentials_and_invalidates_each_cache(boundaries, monkeypatch):
    db = _db(_one(None), MagicMock())
    encrypt = MagicMock(return_value="enc:insights-key")
    monkeypatch.setattr(es.ds, "is_externally_managed", lambda _key: False)
    monkeypatch.setattr(es.ds, "encrypt_value", encrypt)

    response = await es.upsert_setting(
        "insights.api_key",
        EnterpriseConfigUpdate(value="  provider-key "),
        db=db,
        current_user=_actor(),
    )

    deprecated = [
        "insights.aws_region",
        "insights.aws_access_key_id",
        "insights.aws_secret_access_key",
        "insights.aws_session_token",
        "insights.model_url",
        "insights.model_api_key",
    ]
    encrypt.assert_called_once_with("provider-key")
    assert db.execute.await_count == 2
    delete_statement = db.execute.await_args_list[1].args[0]
    values = next(value for value in delete_statement.compile().params.values() if isinstance(value, list))
    assert values == deprecated
    assert db.commit.await_count == 2
    assert boundaries.invalidate.await_args_list == [
        call("insights.api_key"),
        *[call(key) for key in deprecated],
    ]
    assert boundaries.refresh.await_count == 2
    assert response.value == REDACTED


@pytest.mark.asyncio
async def test_clearing_insights_api_key_does_not_delete_legacy_rows(boundaries, monkeypatch):
    db = _db(_one(None))
    monkeypatch.setattr(es.ds, "is_externally_managed", lambda _key: False)
    monkeypatch.setattr(es.ds, "encrypt_value", MagicMock(return_value=""))

    response = await es.upsert_setting(
        "insights.api_key", EnterpriseConfigUpdate(value="   "), db=db, current_user=_actor()
    )

    assert db.execute.await_count == 1
    db.commit.assert_awaited_once_with()
    boundaries.invalidate.assert_awaited_once_with("insights.api_key")
    boundaries.refresh.assert_awaited_once_with()
    assert response.value == REDACTED


@pytest.mark.asyncio
async def test_usage_reporting_settings_require_super_admin(boundaries):
    db = _db()

    with pytest.raises(HTTPException) as error:
        await es.upsert_setting(
            "usage_ping.frequency",
            EnterpriseConfigUpdate(value="daily"),
            db=db,
            current_user=_actor(UserRole.admin),
        )

    assert error.value.status_code == 403
    assert error.value.detail == "Usage reporting can only be changed by a super administrator"
    db.execute.assert_not_awaited()
    boundaries.invalidate.assert_not_awaited()


@pytest.mark.asyncio
async def test_usage_reporting_frequency_rejects_unknown_values(boundaries, monkeypatch):
    db = _db()
    monkeypatch.setattr(es.ds, "is_externally_managed", lambda _key: False)

    with pytest.raises(HTTPException) as error:
        await es.upsert_setting(
            "usage_ping.frequency",
            EnterpriseConfigUpdate(value="hourly"),
            db=db,
            current_user=_actor(UserRole.super_admin),
        )

    assert error.value.status_code == 422
    assert error.value.detail == "Usage reporting frequency must be every_6_hours, daily, or weekly"
    db.execute.assert_not_awaited()
    boundaries.invalidate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["every_6_hours", "daily", "weekly"])
async def test_super_admin_can_set_usage_reporting_frequency(value, boundaries, monkeypatch):
    db = _db(_one(None))
    monkeypatch.setattr(es.ds, "is_externally_managed", lambda _key: False)

    response = await es.upsert_setting(
        "usage_ping.frequency",
        EnterpriseConfigUpdate(value=value),
        db=db,
        current_user=_actor(UserRole.super_admin),
    )

    stored = db.add.call_args.args[0]
    assert (stored.key, stored.value) == ("usage_ping.frequency", value)
    assert response.value == value
    boundaries.invalidate.assert_awaited_once_with("usage_ping.frequency")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "value", "detail"),
    [
        ("branding.app_name", "<b>Unsafe</b>", "App name must not contain HTML tags"),
        (
            "branding.logo",
            "data:image/png;base64,bm90LXBuZw==",
            "File content does not match declared type image/png",
        ),
        (
            "branding.wordmark",
            "https://example.test/logo.png",
            "Logo must be a base64 data URL (data:image/...;base64,...)",
        ),
    ],
)
async def test_branding_validation_rejects_before_database_mutation(key, value, detail, boundaries, monkeypatch):
    db = _db()
    monkeypatch.setattr(es.ds, "is_externally_managed", lambda _key: False)

    with pytest.raises(HTTPException) as error:
        await es.upsert_setting(key, EnterpriseConfigUpdate(value=value), db=db, current_user=_actor())

    assert error.value.status_code == 422
    assert error.value.detail == detail
    db.execute.assert_not_awaited()
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
    boundaries.invalidate.assert_not_awaited()
    boundaries.refresh.assert_not_awaited()
    boundaries.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_commit_failure_prevents_refresh_and_audit(boundaries, monkeypatch):
    db = _db(_one(None))
    db.commit.side_effect = RuntimeError("commit failed")
    monkeypatch.setattr(es.ds, "is_externally_managed", lambda _key: False)

    with pytest.raises(RuntimeError, match="commit failed"):
        await es.upsert_setting(
            "deployment.public_url",
            EnterpriseConfigUpdate(value="https://example.test"),
            db=db,
            current_user=_actor(),
        )

    db.refresh.assert_not_awaited()
    boundaries.invalidate.assert_not_awaited()
    boundaries.refresh.assert_not_awaited()
    boundaries.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_restart_setting_persists_marker_and_refreshes_cache(boundaries, monkeypatch):
    cfg = SimpleNamespace(key="oauth.client_id", value="client")
    db = _db(_one(cfg), _one(None))
    monkeypatch.setattr(es.ds, "is_externally_managed", lambda _key: False)
    monkeypatch.setattr(es, "datetime", FrozenDateTime)

    response = await es.delete_setting("oauth.client_id", db=db, current_user=_actor())

    assert response == {"deleted": "oauth.client_id"}
    db.delete.assert_awaited_once_with(cfg)
    marker = db.add.call_args.args[0]
    assert marker.key == RESTART_PENDING_KEY
    assert json.loads(marker.value) == {
        "changed_at": "2026-07-15T20:00:00+00:00",
        "keys": ["oauth.client_id"],
    }
    db.commit.assert_awaited_once_with()
    boundaries.invalidate.assert_awaited_once_with("oauth.client_id")
    boundaries.refresh.assert_awaited_once_with()
    boundaries.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_revoke_sensitive_restart_setting_deletes_and_emits_critical_event(boundaries, monkeypatch):
    cfg = SimpleNamespace(key="oauth.client_secret", value="enc:ciphertext")
    db = _db(_one(cfg), _one(None))
    monkeypatch.setattr(es.ds, "is_externally_managed", lambda _key: False)

    response = await es.revoke_setting("oauth.client_secret", db=db, current_user=_actor())

    assert response == {
        "revoked": "oauth.client_secret",
        "message": "Secret has been permanently deleted",
    }
    db.delete.assert_awaited_once_with(cfg)
    db.commit.assert_awaited_once_with()
    boundaries.invalidate.assert_awaited_once_with("oauth.client_secret")
    boundaries.refresh.assert_awaited_once_with()
    event = boundaries.emit.await_args.args[0]
    assert event.event_type is EventType.SETTING_CHANGED
    assert event.severity is Severity.CRITICAL
    assert event.outcome == "success"
    assert event.target_id == "oauth.client_secret"
    assert event.target_type == "sensitive_setting"
    assert event.detail == "Sensitive setting revoked: oauth.client_secret"


@pytest.mark.asyncio
async def test_delete_and_revoke_nonrestart_settings_skip_restart_marker(boundaries, monkeypatch):
    monkeypatch.setattr(es.ds, "is_externally_managed", lambda _key: False)
    delete_cfg = SimpleNamespace(key="data.retention_days", value="90")
    revoke_cfg = SimpleNamespace(key="insights.api_key", value="enc:key")
    delete_db = _db(_one(delete_cfg))
    revoke_db = _db(_one(revoke_cfg))

    deleted = await es.delete_setting("data.retention_days", db=delete_db, current_user=_actor())
    revoked = await es.revoke_setting("insights.api_key", db=revoke_db, current_user=_actor())

    assert deleted == {"deleted": "data.retention_days"}
    assert revoked["revoked"] == "insights.api_key"
    delete_db.add.assert_not_called()
    revoke_db.add.assert_not_called()
    assert delete_db.execute.await_count == 1
    assert revoke_db.execute.await_count == 1
    assert boundaries.invalidate.await_args_list == [
        call("data.retention_days"),
        call("insights.api_key"),
    ]
    assert boundaries.refresh.await_count == 2
    boundaries.emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_resources_passes_all_overrides_but_reports_only_supported_keys(boundaries, monkeypatch):
    import services.clickhouse as clickhouse

    rows = [
        SimpleNamespace(key="resource.max_query_memory_mb", value="300"),
        SimpleNamespace(key="resource.unknown", value="9"),
    ]
    db = _db(_many(rows))
    apply = AsyncMock()
    monkeypatch.setattr(clickhouse, "apply_resource_settings", apply)

    response = await es.apply_resources(current_user=_actor(), db=db)

    expected = {"resource.max_query_memory_mb": "300", "resource.unknown": "9"}
    apply.assert_awaited_once_with(overrides=expected)
    assert response == {
        "applied": {"resource.max_query_memory_mb": "300"},
        "message": "ClickHouse resource settings applied",
    }
    event = boundaries.emit.await_args.args[0]
    assert event.event_type is EventType.SETTING_CHANGED
    assert event.severity is Severity.WARNING
    assert event.target_id == "resource_settings"
    assert event.target_type == "setting"
    assert event.detail == ("Applied resource settings: ['resource.max_query_memory_mb', 'resource.unknown']")


@pytest.mark.asyncio
async def test_apply_resources_failure_is_not_hidden_and_emits_no_success(boundaries, monkeypatch):
    import services.clickhouse as clickhouse

    db = _db(_many([SimpleNamespace(key="resource.max_query_memory_mb", value="300")]))
    monkeypatch.setattr(
        clickhouse,
        "apply_resource_settings",
        AsyncMock(side_effect=RuntimeError("ClickHouse unavailable")),
    )

    with pytest.raises(RuntimeError, match="ClickHouse unavailable"):
        await es.apply_resources(current_user=_actor(), db=db)

    boundaries.emit.assert_not_awaited()


_PURGED_TABLES = [
    "session_events",
    "session_stats_agg",
    "session_capabilities",
    "component_activity",
    "component_activity_publications",
]


def _ch_ok(status_code: int = 200):
    return MagicMock(status_code=status_code)


@pytest.mark.asyncio
async def test_purge_waits_for_every_clickhouse_delete_and_reports_success(boundaries, monkeypatch):
    import services.clickhouse.client as clickhouse_client

    query = AsyncMock(return_value=_ch_ok())
    monkeypatch.setattr(clickhouse_client, "_query", query)
    db = _db(*[MagicMock(rowcount=value) for value in (5, 4, 3, 2)])

    response = await es.purge_traces_and_insights(db=db, current_user=_actor())

    assert query.await_args_list == [
        call(
            f"ALTER TABLE {table} DELETE WHERE project_id = {{project_id:String}}",
            {"param_project_id": "default", "mutations_sync": "2"},
            timeout=es._PURGE_MUTATION_TIMEOUT,
        )
        for table in _PURGED_TABLES
    ]
    # Snapshots AND their component mappings are retained, so later sessions on
    # an already-uploaded hash keep verified attribution without a backfill.
    for kept in ("layer_snapshots", "layer_components", "layer_component_extractions", "session_checkpoints"):
        assert all(kept not in item.args[0] for item in query.await_args_list)
    statements = [item.args[0] for item in db.execute.await_args_list]
    assert [statement.table.name for statement in statements] == [
        "insight_reports",
        "insight_session_facets",
        "insight_session_meta",
        "insight_meta_cache",
    ]
    report_sql = str(statements[0].compile(compile_kwargs={"literal_binds": True}))
    facet_sql = str(statements[1].compile(compile_kwargs={"literal_binds": True}))
    # Component reports/facets have no agent_id: they must match by project.
    assert "insight_reports.subject_type = 'component'" in report_sql
    assert "insight_reports.project_id = 'default'" in report_sql
    assert "insight_session_facets.project_id = 'default'" in facet_sql
    db.commit.assert_awaited_once_with()
    assert response.model_dump() == {
        "project_id": "default",
        "clickhouse_tables": _PURGED_TABLES,
        "deleted_reports": 5,
        "deleted_facets": 4,
        "deleted_session_meta": 3,
        "deleted_meta_cache": 2,
    }
    event = boundaries.emit.await_args.args[0]
    assert event.outcome == "success"
    assert event.severity is Severity.CRITICAL
    assert event.target_id == "danger.purge_traces_insights"
    assert event.target_type == "danger_zone"


@pytest.mark.parametrize(
    "failure",
    [RuntimeError("mutation unavailable"), _ch_ok(500)],
    ids=["raised", "http-error-response"],
)
@pytest.mark.asyncio
async def test_purge_clickhouse_failure_is_reported_not_success(boundaries, monkeypatch, failure):
    """An HTTP error response is returned, not raised, by the ClickHouse client;
    both it and an exception must fail the action rather than report success."""
    import services.clickhouse.client as clickhouse_client

    effects = [_ch_ok() for _ in _PURGED_TABLES]
    effects[1] = failure
    query = AsyncMock(side_effect=effects)
    monkeypatch.setattr(clickhouse_client, "_query", query)
    db = _db(*[MagicMock(rowcount=1) for _ in range(4)])

    with pytest.raises(HTTPException) as raised:
        await es.purge_traces_and_insights(db=db, current_user=_actor())

    assert raised.value.status_code == 502
    worst = "Purge incomplete; run it again. Insight reports were removed. Failed: " + ", ".join(_PURGED_TABLES)
    assert len(worst) < 200
    assert "session_stats_agg" in raised.value.detail and "run it again" in raised.value.detail
    # One failing table does not stop the rest, and PostgreSQL insight data is still removed.
    assert query.await_count == len(_PURGED_TABLES)
    db.commit.assert_awaited_once_with()
    event = boundaries.emit.await_args.args[0]
    assert event.outcome == "failure"
    assert "session_stats_agg" in event.detail


@pytest.mark.asyncio
async def test_purge_database_failure_propagates_without_commit_or_success_event(boundaries, monkeypatch):
    import services.clickhouse.client as clickhouse_client

    query = AsyncMock(return_value=_ch_ok())
    monkeypatch.setattr(clickhouse_client, "_query", query)
    db = _db()
    db.execute.side_effect = RuntimeError("postgres unavailable")

    with pytest.raises(RuntimeError, match="postgres unavailable"):
        await es.purge_traces_and_insights(db=db, current_user=_actor())

    assert query.await_count == len(_PURGED_TABLES)
    db.commit.assert_not_awaited()
    boundaries.emit.assert_not_awaited()


@pytest.mark.parametrize(
    ("error", "model", "expected"),
    [
        (
            "model identifier is invalid",
            "bedrock/us.anthropic.claude",
            (
                "Model ID is not available in your region. Ensure the Base URL region matches where the model is "
                "enabled. Cross-region models use prefixes like us./eu./apac. "
                "(e.g., bedrock/us.anthropic.claude-sonnet-4-6-v1)."
            ),
        ),
        ("model_not_found", "openai/missing", "Model ID not recognized. Verify the format: provider/model-name"),
        ("authentication failed", "anthropic/claude", "Invalid API key. Get one at console.anthropic.com"),
        ("401", "bedrock/model", "Bearer token may be expired. Regenerate in AWS Console."),
        ("invalid api key", "openai/gpt", "Invalid API key. Get one at platform.openai.com/api-keys"),
        ("forbidden", "gemini/model", "Invalid API key. Get one at aistudio.google.com/apikey"),
        ("auth rejected", "custom/model", "Authentication failed. Verify your API key."),
        (
            "connection timed out",
            "custom/model",
            "Could not reach endpoint. Check your Base URL and network connectivity.",
        ),
        ("provider does not exist", "custom/model", "Model ID not recognized. Verify the format: provider/model-name"),
        ("429 rate limit", "custom/model", "Rate limited by provider. The key is valid, try again in a moment."),
        (
            "access denied",
            "bedrock/model",
            "Model access not enabled. Enable the model in your AWS Bedrock console for this region.",
        ),
        ("unexpected", "custom/model", "Connection test failed. Check your settings and try again."),
    ],
)
def test_connection_error_hints(error, model, expected):
    assert es._get_connection_error_hint(error, model) == expected


@pytest.mark.asyncio
async def test_connection_without_model_returns_actionable_response(monkeypatch):
    get = AsyncMock(return_value="")
    completion = AsyncMock(side_effect=AssertionError("provider must not be called"))
    monkeypatch.setattr(es.ds, "get", get)
    monkeypatch.setattr(es.litellm, "acompletion", completion)

    response = await es.test_insights_connection(es._TestConnectionRequest(), current_user=_actor())

    assert response.model_dump() == {
        "success": False,
        "model": None,
        "latency_ms": None,
        "error": "No model configured",
        "hint": "Set the Sections Model first, or provide a model in the request.",
    }
    assert get.await_args_list == [
        call("insights.api_key"),
        call("insights.api_base"),
        call("insights.api_version"),
        call("insights.aws_region"),
        call("insights.model_sections"),
    ]
    completion.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "optional"),
    [
        (
            "azure/deployment",
            {
                "api_key": "provider-key",
                "api_base": "https://provider.example.test",
                "api_version": "2026-01-01",
            },
        ),
        (
            "bedrock/us.anthropic.claude",
            {
                "api_key": "provider-key",
                "api_base": "https://provider.example.test",
                "aws_region_name": "us-east-1",
            },
        ),
    ],
)
async def test_connection_success_builds_provider_specific_request(monkeypatch, model, optional):
    values = {
        "insights.api_key": "provider-key",
        "insights.api_base": "https://provider.example.test",
        "insights.api_version": "2026-01-01",
        "insights.aws_region": "us-east-1",
    }
    get = AsyncMock(side_effect=lambda key: values[key])
    completion = AsyncMock(return_value=object())
    clock = MagicMock(side_effect=[1.0, 1.125])
    monkeypatch.setattr(es.ds, "get", get)
    monkeypatch.setattr(es.litellm, "acompletion", completion)
    monkeypatch.setattr(es.time, "time", clock)

    response = await es.test_insights_connection(es._TestConnectionRequest(model=model), current_user=_actor())

    expected = {
        "model": model,
        "messages": [{"role": "user", "content": "Say hello in exactly one word."}],
        "max_tokens": 2048,
        "timeout": 15,
        "drop_params": True,
        **optional,
    }
    completion.assert_awaited_once_with(**expected)
    assert response.model_dump() == {
        "success": True,
        "model": model,
        "latency_ms": 125,
        "error": None,
        "hint": None,
    }


@pytest.mark.asyncio
async def test_connection_failure_is_truncated_and_mapped_to_hint(monkeypatch):
    values = {
        "insights.api_key": "",
        "insights.api_base": "",
        "insights.api_version": "",
        "insights.aws_region": "",
    }
    error = "Invalid API key: " + "x" * 240
    monkeypatch.setattr(es.ds, "get", AsyncMock(side_effect=lambda key: values[key]))
    monkeypatch.setattr(es.litellm, "acompletion", AsyncMock(side_effect=RuntimeError(error)))
    monkeypatch.setattr(es.time, "time", MagicMock(return_value=1.0))

    response = await es.test_insights_connection(
        es._TestConnectionRequest(model="anthropic/claude"), current_user=_actor()
    )

    assert response.model_dump() == {
        "success": False,
        "model": "anthropic/claude",
        "latency_ms": None,
        "error": error[:200],
        "hint": "Invalid API key. Get one at console.anthropic.com",
    }
