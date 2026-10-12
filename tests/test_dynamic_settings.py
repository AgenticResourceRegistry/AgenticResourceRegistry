# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Focused coverage for the DB-backed dynamic settings service."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import services.dynamic_settings as ds
from models.enterprise_config import EnterpriseConfig
from models.team_invite import TeamInvite


@pytest.fixture(autouse=True)
def _isolate_module_state():
    external = dict(ds._external_settings)
    sync_cache = dict(ds._sync_cache)
    sync_cache_loaded = ds._sync_cache_loaded
    ds._external_settings.clear()
    ds._sync_cache.clear()
    ds._sync_cache_loaded = False
    yield
    ds._external_settings.clear()
    ds._external_settings.update(external)
    ds._sync_cache = sync_cache
    ds._sync_cache_loaded = sync_cache_loaded


@pytest.fixture
async def db_factory(monkeypatch):
    import database

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(EnterpriseConfig.__table__.create)
        await connection.run_sync(TeamInvite.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session", factory)
    yield factory
    await engine.dispose()


def _redis_client(monkeypatch, *, cached=None):
    import services.redis as redis_service

    redis = SimpleNamespace(
        get=AsyncMock(return_value=cached),
        set=AsyncMock(),
        delete=AsyncMock(),
        scan=AsyncMock(return_value=(0, [])),
    )
    monkeypatch.setattr(redis_service, "get_redis", lambda: redis)
    return redis


def _ciphertext(secret: str, value: str) -> str:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return "enc:" + Fernet(key).encrypt(value.encode()).decode()


@pytest.mark.asyncio
async def test_get_prefers_external_value_without_touching_cache_or_database(monkeypatch):
    import services.redis as redis_service

    ds._external_settings["oauth.client_secret"] = "file-secret"
    read_db = AsyncMock(side_effect=AssertionError("database should not be read"))
    monkeypatch.setattr(ds, "_read_from_db", read_db)
    monkeypatch.setattr(
        redis_service,
        "get_redis",
        MagicMock(side_effect=AssertionError("Redis should not be read")),
    )

    assert await ds.get("oauth.client_secret") == "file-secret"
    read_db.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_cache_hit_uses_namespaced_key_and_skips_database(monkeypatch):
    redis = _redis_client(monkeypatch, cached="cached-value")
    read_db = AsyncMock(side_effect=AssertionError("database should not be read"))
    monkeypatch.setattr(ds, "_read_from_db", read_db)

    value = await ds.get("insights.model_sections")

    assert value == "cached-value"
    redis.get.assert_awaited_once_with("settings:insights.model_sections")
    redis.set.assert_not_awaited()
    read_db.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_cache_miss_reads_database_and_caches_plain_string_with_ttl(monkeypatch):
    redis = _redis_client(monkeypatch)
    read_db = AsyncMock(return_value="db-value")
    monkeypatch.setattr(ds, "_read_from_db", read_db)

    value = await ds.get("misc.default_harness")

    assert value == "db-value"
    read_db.assert_awaited_once_with("misc.default_harness")
    redis.set.assert_awaited_once_with("settings:misc.default_harness", "db-value", ex=30)


@pytest.mark.asyncio
async def test_get_fails_open_to_database_when_redis_read_and_write_fail(monkeypatch):
    redis = _redis_client(monkeypatch)
    redis.get.side_effect = ConnectionError("cache read unavailable")
    redis.set.side_effect = ConnectionError("cache write unavailable")
    read_db = AsyncMock(return_value="durable-value")
    monkeypatch.setattr(ds, "_read_from_db", read_db)

    assert await ds.get("deployment.public_url") == "durable-value"
    read_db.assert_awaited_once_with("deployment.public_url")
    redis.set.assert_awaited_once_with("settings:deployment.public_url", "durable-value", ex=30)


@pytest.mark.asyncio
async def test_get_database_miss_resolves_call_default_service_default_and_unknown(monkeypatch):
    redis = _redis_client(monkeypatch)
    monkeypatch.setattr(ds, "_read_from_db", AsyncMock(return_value=None))

    assert await ds.get("insights.batch_period_days", default="21") == "21"
    assert await ds.get("insights.batch_period_days") == "14"
    assert await ds.get("unknown.setting") == ""
    redis.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_does_not_use_direct_environment_as_runtime_fallback(monkeypatch):
    _redis_client(monkeypatch)
    monkeypatch.setenv("OAUTH_CLIENT_ID", "environment-client")
    monkeypatch.setattr(ds, "_read_from_db", AsyncMock(return_value=None))

    assert await ds.get("oauth.client_id") == ""


@pytest.mark.asyncio
async def test_concurrent_reads_keep_cache_and_database_results_isolated(monkeypatch):
    redis = _redis_client(monkeypatch)
    cached = {"settings:concurrent.one": "one"}
    database_values = {"concurrent.two": "two", "concurrent.three": "three"}

    async def read_cache(key):
        await asyncio.sleep(0)
        return cached.get(key)

    async def read_database(key):
        await asyncio.sleep(0)
        return database_values[key]

    redis.get.side_effect = read_cache
    read_db = AsyncMock(side_effect=read_database)
    monkeypatch.setattr(ds, "_read_from_db", read_db)

    values = await asyncio.gather(
        ds.get("concurrent.one"),
        ds.get("concurrent.two"),
        ds.get("concurrent.three"),
    )

    assert values == ["one", "two", "three"]
    assert {call.args[0] for call in redis.get.await_args_list} == {
        "settings:concurrent.one",
        "settings:concurrent.two",
        "settings:concurrent.three",
    }
    assert {call.args[0] for call in read_db.await_args_list} == set(database_values)
    assert {(call.args[0], call.args[1], call.kwargs["ex"]) for call in redis.set.await_args_list} == {
        ("settings:concurrent.two", "two", 30),
        ("settings:concurrent.three", "three", 30),
    }


@pytest.mark.asyncio
async def test_typed_async_getters_handle_values_defaults_and_invalid_data(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(ds, "optic", logger)
    monkeypatch.setitem(ds.DEFAULTS, "broken.int", "not-an-int")
    monkeypatch.setitem(ds.DEFAULTS, "broken.float", "not-a-float")
    get = AsyncMock(
        side_effect=[
            "42",
            "",
            "",
            "",
            "invalid-int-secret",
            "invalid-int-secret",
            "invalid-int-secret",
            "2.5",
            "",
            "",
            "",
            "invalid-float-secret",
            "invalid-float-secret",
            "invalid-float-secret",
        ]
    )
    monkeypatch.setattr(ds, "get", get)

    assert await ds.get_int("value") == 42
    assert await ds.get_int("value", 8) == 8
    assert await ds.get_int("insights.batch_period_days") == 14
    assert await ds.get_int("broken.int") == 0
    assert await ds.get_int("value", 9) == 9
    assert await ds.get_int("insights.batch_period_days") == 14
    assert await ds.get_int("broken.int") == 0

    assert await ds.get_float("value") == 2.5
    assert await ds.get_float("value", 1.25) == 1.25
    assert await ds.get_float("resource.redis_socket_timeout") == 2.0
    assert await ds.get_float("broken.float") == 0.0
    assert await ds.get_float("value", 3.5) == 3.5
    assert await ds.get_float("resource.redis_socket_timeout") == 2.0
    assert await ds.get_float("broken.float") == 0.0

    assert logger.warning.call_count == 6
    logged = repr(logger.warning.call_args_list)
    assert "invalid-int-secret" not in logged
    assert "invalid-float-secret" not in logged
    assert "invalid integer dynamic setting key={}" in logged
    assert "invalid float dynamic setting key={}" in logged


@pytest.mark.asyncio
async def test_bool_and_list_getters_normalize_supported_values(monkeypatch):
    monkeypatch.setattr(
        ds,
        "get",
        AsyncMock(side_effect=["", "", "YES", "off", " alpha, beta ,, gamma ", "", "one | two || three"]),
    )

    assert await ds.get_bool("missing", default=True) is True
    assert await ds.get_bool("insights.batch_enabled") is True
    assert await ds.get_bool("feature") is True
    assert await ds.get_bool("feature") is False
    assert await ds.get_list("items") == ["alpha", "beta", "gamma"]
    assert await ds.get_list("items") == []
    assert await ds.get_list("items", separator="|") == ["one", "two", "three"]


def test_sync_getters_use_loaded_values_and_defensive_defaults(monkeypatch):
    ds._sync_cache.update(
        {
            "cached.text": "value",
            "cached.int": "12",
            "cached.invalid_int": "bad",
            "cached.true": "1",
            "cached.false": "disabled",
            "empty.invalid_default": "",
            "empty.bool_default": "",
        }
    )
    monkeypatch.setitem(ds.DEFAULTS, "empty.invalid_default", "bad")
    monkeypatch.setitem(ds.DEFAULTS, "empty.bool_default", "yes")
    monkeypatch.setitem(ds.DEFAULTS, "broken.sync.int", "bad")

    assert ds.get_sync("cached.text") == "value"
    assert ds.get_sync("missing", "override") == "override"
    assert ds.get_sync("insights.batch_period_days") == "14"
    assert ds.get_sync("unknown") == ""

    assert ds.get_sync_int("cached.int") == 12
    assert ds.get_sync_int("missing", 7) == 7
    assert ds.get_sync_int("insights.batch_period_days") == 14
    assert ds.get_sync_int("empty.invalid_default") == 0
    assert ds.get_sync_int("broken.sync.int") == 0
    assert ds.get_sync_int("cached.invalid_int", 6) == 6
    assert ds.get_sync_int("cached.invalid_int") == 0

    assert ds.get_sync_bool("missing", True) is True
    assert ds.get_sync_bool("insights.batch_enabled") is True
    assert ds.get_sync_bool("empty.bool_default") is True
    assert ds.get_sync_bool("cached.true") is True
    assert ds.get_sync_bool("cached.false") is False


@pytest.mark.asyncio
async def test_read_helpers_query_database_and_decrypt_encrypted_rows(db_factory, monkeypatch):
    monkeypatch.setattr("config.settings.SECRET_KEY", "dynamic-settings-read-key")
    encrypted = ds.encrypt_value("top-secret")
    async with db_factory() as session:
        session.add_all(
            [
                EnterpriseConfig(key="plain.setting", value="plain-value"),
                EnterpriseConfig(key="oauth.client_secret", value=encrypted),
            ]
        )
        await session.commit()

    assert await ds._read_from_db("plain.setting") == "plain-value"
    assert await ds._read_from_db("oauth.client_secret") == "top-secret"
    assert await ds._read_from_db("missing.setting") is None
    assert await ds._read_all_from_db() == {
        "plain.setting": "plain-value",
        "oauth.client_secret": "top-secret",
    }


@pytest.mark.asyncio
async def test_database_read_failures_return_absence_without_logging_values(monkeypatch):
    import database

    logger = MagicMock()
    monkeypatch.setattr(ds, "optic", logger)
    monkeypatch.setattr(
        database,
        "async_session",
        MagicMock(side_effect=RuntimeError("database failed around super-secret-value")),
    )

    assert await ds._read_from_db("oauth.client_secret") is None
    assert await ds._read_all_from_db() == {}
    assert logger.warning.call_args_list == [
        (("dynamic settings database read failed for key={}", "oauth.client_secret"), {}),
        (("dynamic settings database read-all failed",), {}),
    ]
    assert "super-secret-value" not in repr(logger.warning.call_args_list)


@pytest.mark.asyncio
async def test_get_all_and_section_merge_defaults_database_and_external(monkeypatch):
    monkeypatch.setattr(
        ds,
        "_read_all_from_db",
        AsyncMock(
            return_value={
                "insights.batch_period_days": "20",
                "insights.custom": "database",
                "other.value": "outside",
            }
        ),
    )
    ds._external_settings.update(
        {
            "insights.batch_period_days": "30",
            "oauth.client_secret": "external-secret",
        }
    )

    all_settings = await ds.get_all()
    section = await ds.get_section("insights")

    assert all_settings["insights.batch_period_days"] == "30"
    assert all_settings["insights.custom"] == "database"
    assert all_settings["oauth.client_secret"] == "external-secret"
    assert all_settings["auth.self_registration_enabled"] == "false"
    assert section["insights.batch_period_days"] == "30"
    assert section["insights.custom"] == "database"
    assert "other.value" not in section


@pytest.mark.asyncio
async def test_load_and_refresh_sync_cache_replace_stale_values(monkeypatch):
    read_all = AsyncMock(side_effect=[{"custom.old": "first"}, {"custom.new": "second"}])
    monkeypatch.setattr(ds, "_read_all_from_db", read_all)
    ds._external_settings["oauth.client_secret"] = "external-secret"
    ds._sync_cache["stale"] = "remove-me"

    await ds.load_sync_cache()

    assert ds._sync_cache_loaded is True
    assert ds._sync_cache["custom.old"] == "first"
    assert ds._sync_cache["oauth.client_secret"] == "external-secret"
    assert ds._sync_cache["insights.batch_period_days"] == "14"
    assert "stale" not in ds._sync_cache

    await ds.refresh_sync_cache()

    assert ds._sync_cache["custom.new"] == "second"
    assert "custom.old" not in ds._sync_cache
    assert read_all.await_count == 2


@pytest.mark.asyncio
async def test_load_sync_cache_failure_still_initializes_defaults_and_external(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(ds, "optic", logger)
    monkeypatch.setattr(ds, "_read_all_from_db", AsyncMock(side_effect=RuntimeError("secret database detail")))
    ds._external_settings["oauth.client_secret"] = "external-secret"

    await ds.load_sync_cache()

    assert ds._sync_cache_loaded is True
    assert ds._sync_cache["insights.batch_period_days"] == "14"
    assert ds._sync_cache["oauth.client_secret"] == "external-secret"
    logger.warning.assert_called_once_with("dynamic_settings_cache_load_failed")
    assert "secret database detail" not in repr(logger.mock_calls)


@pytest.mark.asyncio
async def test_invalidate_deletes_exact_cache_key_and_ignores_redis_failure(monkeypatch):
    redis = _redis_client(monkeypatch)

    await ds.invalidate("oauth.client_id")
    redis.delete.assert_awaited_once_with("settings:oauth.client_id")

    redis.delete.side_effect = ConnectionError("offline")
    await ds.invalidate("oauth.client_secret")
    assert redis.delete.await_count == 2


@pytest.mark.asyncio
async def test_invalidate_all_scans_each_page_and_deletes_matching_keys(monkeypatch):
    redis = _redis_client(monkeypatch)
    redis.scan.side_effect = [
        (9, ["settings:first", "settings:second"]),
        (4, []),
        (0, ["settings:third"]),
    ]

    await ds.invalidate_all()

    assert redis.scan.await_args_list == [
        ((0,), {"match": "settings:*", "count": 100}),
        ((9,), {"match": "settings:*", "count": 100}),
        ((4,), {"match": "settings:*", "count": 100}),
    ]
    assert redis.delete.await_args_list == [
        (("settings:first", "settings:second"), {}),
        (("settings:third",), {}),
    ]


@pytest.mark.asyncio
async def test_invalidate_all_is_best_effort_when_redis_is_unavailable(monkeypatch):
    redis = _redis_client(monkeypatch)
    redis.scan.side_effect = ConnectionError("offline")

    await ds.invalidate_all()

    redis.scan.assert_awaited_once_with(0, match="settings:*", count=100)
    redis.delete.assert_not_awaited()


def test_environment_values_give_process_environment_precedence(monkeypatch):
    import dotenv

    monkeypatch.setattr(dotenv, "dotenv_values", lambda _path: {"SHARED": "file", "FILE_ONLY": "yes"})
    monkeypatch.setenv("SHARED", "process")
    monkeypatch.setenv("PROCESS_ONLY", "yes")

    values = ds._environment_values()

    assert values["SHARED"] == "process"
    assert values["FILE_ONLY"] == "yes"
    assert values["PROCESS_ONLY"] == "yes"


def test_load_external_settings_loads_only_file_backed_values_and_saml_pair(tmp_path, monkeypatch):
    oauth_secret = tmp_path / "oauth-secret"
    saml_key = tmp_path / "saml-key"
    saml_cert = tmp_path / "saml-cert"
    oauth_secret.write_text("oauth-file-value\n")
    saml_key.write_text("private-key")
    saml_cert.write_text("public-cert")
    monkeypatch.setattr(
        ds,
        "_environment_values",
        lambda: {
            "OAUTH_CLIENT_ID": "direct-value-is-not-external",
            "OAUTH_CLIENT_SECRET_FILE": str(oauth_secret),
            "SAML_SP_PRIVATE_KEY_FILE": str(saml_key),
            "SAML_SP_X509_CERT_FILE": str(saml_cert),
        },
    )

    ds.load_external_settings()

    assert ds.external_setting_keys() == {
        "oauth.client_secret",
        "saml.sp_private_key",
        "saml.sp_x509_cert",
    }
    assert ds.is_externally_managed("oauth.client_secret") is True
    assert ds.is_externally_managed("oauth.client_id") is False
    assert ds.has_external_saml_material() is True
    assert ds._external_settings["oauth.client_secret"] == "oauth-file-value"
    keys = ds.external_setting_keys()
    keys.clear()
    assert ds.external_setting_keys()


def test_load_external_settings_rejects_partial_saml_pair_without_losing_previous_state(tmp_path, monkeypatch):
    saml_key = tmp_path / "saml-key"
    saml_key.write_text("private-key")
    ds._external_settings["oauth.client_secret"] = "existing"
    monkeypatch.setattr(
        ds,
        "_environment_values",
        lambda: {"SAML_SP_PRIVATE_KEY_FILE": str(saml_key)},
    )

    with pytest.raises(ValueError, match="must be configured together"):
        ds.load_external_settings()

    assert ds._external_settings == {"oauth.client_secret": "existing"}


def test_load_external_settings_with_no_files_clears_stale_values(monkeypatch):
    ds._external_settings["oauth.client_secret"] = "stale"
    monkeypatch.setattr(ds, "_environment_values", lambda: {"OAUTH_CLIENT_SECRET": "direct"})

    ds.load_external_settings()

    assert ds.external_setting_keys() == set()
    assert ds.has_external_saml_material() is False


@pytest.mark.asyncio
async def test_import_sso_environment_persists_updates_encrypts_secrets_and_refreshes(db_factory, monkeypatch):
    async with db_factory() as session:
        session.add_all(
            [
                EnterpriseConfig(key="oauth.server_metadata_url", value=""),
                EnterpriseConfig(key="oauth.client_id", value="database-client"),
            ]
        )
        await session.commit()

    ds._external_settings["github.client_id"] = "file-client"
    monkeypatch.setattr(
        ds,
        "_environment_values",
        lambda: {
            "OAUTH_SERVER_METADATA_URL": " https://idp.example/.well-known/openid-configuration ",
            "OAUTH_CLIENT_ID": "environment-client",
            "OAUTH_CLIENT_SECRET": "environment-secret",
            "GITHUB_OAUTH_CLIENT_ID": "ignored-because-external",
            "SSO_ONLY": "   ",
        },
    )
    invalidate_all = AsyncMock()
    refresh = AsyncMock()
    monkeypatch.setattr(ds, "invalidate_all", invalidate_all)
    monkeypatch.setattr(ds, "refresh_sync_cache", refresh)

    assert await ds.import_sso_env_once() == 2

    async with db_factory() as session:
        result = await session.execute(select(EnterpriseConfig))
        rows = {row.key: row.value for row in result.scalars().all()}
    assert rows["oauth.server_metadata_url"] == "https://idp.example/.well-known/openid-configuration"
    assert rows["oauth.client_id"] == "database-client"
    assert rows["oauth.client_secret"].startswith("enc:")
    assert ds.decrypt_value(rows["oauth.client_secret"]) == "environment-secret"
    assert "github.client_id" not in rows
    invalidate_all.assert_awaited_once_with()
    refresh.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_import_sso_environment_without_eligible_values_does_not_refresh(db_factory, monkeypatch):
    async with db_factory() as session:
        session.add(EnterpriseConfig(key="oauth.client_id", value="database-client"))
        await session.commit()
    monkeypatch.setattr(ds, "_environment_values", lambda: {"OAUTH_CLIENT_ID": "environment-client"})
    invalidate_all = AsyncMock()
    refresh = AsyncMock()
    monkeypatch.setattr(ds, "invalidate_all", invalidate_all)
    monkeypatch.setattr(ds, "refresh_sync_cache", refresh)

    assert await ds.import_sso_env_once() == 0
    invalidate_all.assert_not_awaited()
    refresh.assert_not_awaited()


def test_settings_schema_labels_restart_and_external_metadata():
    ds._external_settings["oauth.client_secret"] = "secret"

    schema = ds.settings_schema()
    sso = next(section for section in schema if section["id"] == "sso")
    secret = next(setting for setting in sso["settings"] if setting["key"] == "oauth.client_secret")

    assert ds._setting_label("example.api_url") == "API URL"
    assert ds._setting_label("example.idp_x509_cert") == "IdP X.509 Cert"
    assert ds._setting_label("example.db_cache_ttl") == "DB Cache TTL"
    assert secret == {
        "key": "oauth.client_secret",
        "label": "Client Secret",
        "subtitle": "",
        "default": "",
        "requires_feature": None,
        "restart_required": True,
        "is_externally_managed": True,
    }
    assert all("keys" in section and "settings" in section for section in schema)
    registry = next(section for section in schema if section["id"] == "registry")
    rollout = next(
        setting for setting in registry["settings"] if setting["key"] == "registry.skill_folder_delivery_enabled"
    )
    assert rollout["default"] == "false"
    assert "draining all API/worker processes" in rollout["subtitle"]
    assert "mixed-version rollout" in rollout["subtitle"]


def test_registry_fork_settings_have_defaults_and_appear_in_admin_schema():
    expected = {
        "registry.fork.enabled": "true",
        "registry.fork.max_per_user_per_hour": "30",
    }
    registry = next(section for section in ds.settings_schema() if section["id"] == "registry")
    settings = {setting["key"]: setting for setting in registry["settings"]}
    for key, default in expected.items():
        assert ds.DEFAULTS[key] == default
        assert settings[key]["default"] == default
        assert settings[key]["restart_required"] is False
    # Source-owner fork notices are not implemented; no inert admin control is offered.
    assert "registry.fork.notify_source_owner" not in ds.DEFAULTS
    assert "registry.fork.notify_source_owner" not in settings


def test_mask_value_only_reveals_sensitive_suffix():
    assert ds.mask_value("deployment.public_url", "https://example.test") == "https://example.test"
    assert ds.mask_value("oauth.client_secret", "") == "••••••••"
    assert ds.mask_value("oauth.client_secret", "tiny") == "••••••••"
    assert ds.mask_value("oauth.client_secret", "long-secret-value") == "••••••alue"


def test_encryption_round_trip_passthrough_and_safe_failure_logging(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(ds, "optic", logger)
    monkeypatch.setattr("config.settings.SECRET_KEY", "current-encryption-key")
    monkeypatch.setattr("config.settings.OLD_SECRET_KEY", None)
    monkeypatch.delenv("OLD_SECRET_KEY", raising=False)
    monkeypatch.delenv("OLD_SECRET_KEY_FILE", raising=False)

    encrypted = ds.encrypt_value("private-value")

    assert encrypted.startswith("enc:")
    assert ds.decrypt_value(encrypted) == "private-value"
    assert ds.encrypt_value("") == ""
    assert ds.decrypt_value("") == ""
    assert ds.decrypt_value("plain") == "plain"
    assert ds.decrypt_value("enc:not-valid-ciphertext-private-value") == ""
    logger.error.assert_called_once_with("dynamic settings decrypt failed with current and old keys")
    assert "private-value" not in repr(logger.mock_calls)


@pytest.mark.asyncio
async def test_reencrypt_on_key_rotation_persists_only_values_using_old_key(db_factory, monkeypatch):
    old_secret = "old-rotation-key"
    new_secret = "new-rotation-key"
    old_value = _ciphertext(old_secret, "rotate-me")
    current_value = _ciphertext(new_secret, "already-current")
    corrupt_value = "enc:not-a-valid-token"
    invite_value = _ciphertext(old_secret, "invite-token")
    invite_id = uuid.uuid4()
    monkeypatch.setattr("config.settings.SECRET_KEY", new_secret)
    monkeypatch.setenv("OLD_SECRET_KEY", old_secret)
    monkeypatch.delenv("OLD_SECRET_KEY_FILE", raising=False)

    async with db_factory() as session:
        session.add_all(
            [
                EnterpriseConfig(key="oauth.client_secret", value=old_value),
                EnterpriseConfig(key="google.client_secret", value=current_value),
                EnterpriseConfig(key="github.client_secret", value="legacy-plaintext"),
                EnterpriseConfig(key="insights.api_key", value=corrupt_value),
                EnterpriseConfig(key="saml.idp_x509_cert", value=""),
                TeamInvite(
                    id=invite_id,
                    token_hash="a" * 64,
                    token_encrypted=invite_value,
                    name="Rotated invite",
                    team_id=uuid.uuid4(),
                    invited_by=None,
                    max_uses=1,
                    use_count=0,
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                ),
            ]
        )
        await session.commit()

    assert await ds.reencrypt_on_key_rotation() == 2

    async with db_factory() as session:
        result = await session.execute(select(EnterpriseConfig))
        rows = {row.key: row.value for row in result.scalars().all()}
        invite = await session.get(TeamInvite, invite_id)
    assert rows["oauth.client_secret"] != old_value
    assert ds.decrypt_value(rows["oauth.client_secret"]) == "rotate-me"
    assert rows["google.client_secret"] == current_value
    assert rows["github.client_secret"] == "legacy-plaintext"
    assert rows["insights.api_key"] == corrupt_value
    assert invite.token_encrypted != invite_value
    assert ds.decrypt_value(invite.token_encrypted) == "invite-token"
    assert rows["saml.idp_x509_cert"] == ""


@pytest.mark.asyncio
async def test_reencrypt_with_no_eligible_rows_is_a_noop(db_factory, monkeypatch):
    monkeypatch.setattr("config.settings.SECRET_KEY", "current-key")
    monkeypatch.setenv("OLD_SECRET_KEY", "old-key")
    monkeypatch.delenv("OLD_SECRET_KEY_FILE", raising=False)

    assert await ds.reencrypt_on_key_rotation() == 0

    async with db_factory() as session:
        assert (await session.execute(select(EnterpriseConfig))).scalars().all() == []


@pytest.mark.asyncio
async def test_reencrypt_without_old_key_skips_database(monkeypatch):
    import database

    monkeypatch.setattr(ds, "_old_secret_key", lambda: None)
    session_factory = MagicMock(side_effect=AssertionError("database should not be opened"))
    monkeypatch.setattr(database, "async_session", session_factory)

    assert await ds.reencrypt_on_key_rotation() == 0
    session_factory.assert_not_called()


@pytest.mark.asyncio
async def test_reencrypt_database_failure_logs_without_exception_detail(monkeypatch):
    import database

    logger = MagicMock()
    monkeypatch.setattr(ds, "optic", logger)
    monkeypatch.setattr(ds, "_old_secret_key", lambda: "old-key")
    monkeypatch.setattr(
        database,
        "async_session",
        MagicMock(side_effect=RuntimeError("failure included a private-value")),
    )

    assert await ds.reencrypt_on_key_rotation() == 0
    logger.exception.assert_called_once_with("dynamic settings re-encryption failed")
    assert "private-value" not in repr(logger.mock_calls)
