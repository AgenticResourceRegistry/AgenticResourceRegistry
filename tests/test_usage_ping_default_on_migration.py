# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Migration 045 returns usage reporting to its defaults on upgraded installations."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


@pytest.fixture
def migration():
    path = Path(__file__).parents[1] / "observal-server/alembic/versions/045_usage_ping_default_on.py"
    spec = importlib.util.spec_from_file_location("usage_ping_default_on_migration", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _upgrade(migration, rows: dict[str, str]) -> dict[str, str]:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE enterprise_config (key VARCHAR(255) UNIQUE NOT NULL, value TEXT NOT NULL)"))
        for key, value in rows.items():
            conn.execute(sa.text("INSERT INTO enterprise_config (key, value) VALUES (:k, :v)"), {"k": key, "v": value})
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        return {key: value for key, value in conn.execute(sa.text("SELECT key, value FROM enterprise_config"))}


def test_upgrade_clears_stored_opt_out_and_frequency(migration):
    remaining = _upgrade(
        migration,
        {
            "usage_ping.enabled": "false",
            "usage_ping.frequency": "weekly",
            "usage_ping.company_name": "Acme",
            "deployment.public_url": "https://observal.acme.example",
        },
    )
    assert remaining == {"usage_ping.company_name": "Acme", "deployment.public_url": "https://observal.acme.example"}


def test_upgrade_is_a_noop_without_stored_rows(migration):
    assert _upgrade(migration, {"auth.sso_only": "false"}) == {"auth.sso_only": "false"}


def test_reset_keys_fall_back_to_enabled_every_six_hours(migration):
    from services.dynamic_settings import DEFAULTS

    assert {key: DEFAULTS[key] for key in migration.RESET_KEYS} == {
        "usage_ping.enabled": "true",
        "usage_ping.frequency": "every_6_hours",
    }


def _stub_settings_storage(monkeypatch: pytest.MonkeyPatch, stored: dict[str, str]) -> None:
    """Back the real dynamic-settings lookup with an empty cache and an in-memory enterprise_config."""
    import database
    import services.redis

    class Cache:
        async def get(self, _key):
            return None

        async def set(self, *_args, **_kwargs):
            return None

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def execute(self, statement):
            key = statement.whereclause.right.value
            return MagicMock(scalar_one_or_none=MagicMock(return_value=stored.get(key)))

    monkeypatch.setattr(services.redis, "get_redis", lambda: Cache())
    monkeypatch.setattr(database, "async_session", Session)


@pytest.mark.asyncio
async def test_missing_rows_resolve_to_enabled_every_six_hours(monkeypatch: pytest.MonkeyPatch):
    import services.dynamic_settings as ds
    from services import usage_ping

    _stub_settings_storage(monkeypatch, {})

    assert await ds.get_bool("usage_ping.enabled") is True
    assert await usage_ping._usage_ping_frequency() == "every_6_hours"


@pytest.mark.asyncio
async def test_stored_opt_out_wins_until_the_migration_removes_it(monkeypatch: pytest.MonkeyPatch):
    import services.dynamic_settings as ds
    from services import usage_ping

    _stub_settings_storage(monkeypatch, {"usage_ping.enabled": "false", "usage_ping.frequency": "weekly"})

    assert await ds.get_bool("usage_ping.enabled") is False
    assert await usage_ping._usage_ping_frequency() == "weekly"
