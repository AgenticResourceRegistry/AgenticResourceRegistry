# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from observal_shared.secrets import MAX_SECRET_BYTES, read_secret_file, resolve_secret


def test_secret_file_resolution_and_trailing_newline(tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("value\n")

    assert resolve_secret("TOKEN", {"TOKEN_FILE": str(secret)}) == "value"
    assert read_secret_file(secret) == "value"


def test_secret_file_rejects_ambiguous_missing_and_oversized_values(tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("value")
    with pytest.raises(ValueError, match="Set only one"):
        resolve_secret("TOKEN", {"TOKEN": "direct", "TOKEN_FILE": str(secret)})
    with pytest.raises(ValueError, match="not a regular file"):
        resolve_secret("TOKEN", {"TOKEN_FILE": str(tmp_path / "missing")})

    secret.write_bytes(b"x" * (MAX_SECRET_BYTES + 1))
    with pytest.raises(ValueError, match="exceeds"):
        read_secret_file(secret)


def test_server_boot_and_cli_tokens_use_secret_files(tmp_path, monkeypatch):
    from config import _secret_overrides
    from observal_cli import config as cli_config

    app_secret = tmp_path / "app-secret"
    app_secret.write_text("a" * 32)
    token = tmp_path / "token"
    token.write_text("cli-token\n")
    git_token = tmp_path / "git-token"
    git_token.write_text("git-token\n")

    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("SECRET_KEY_FILE", str(app_secret))
    monkeypatch.setenv("GIT_CLONE_TOKEN_FILE", str(git_token))
    monkeypatch.setenv("OBSERVAL_TOKEN_FILE", str(token))
    monkeypatch.setattr(cli_config, "CONFIG_FILE", tmp_path / "missing.json")
    # This case exercises explicit *_FILE env vars, not the developer's .env.
    monkeypatch.chdir(tmp_path)

    overrides = _secret_overrides()
    assert overrides["SECRET_KEY"] == "a" * 32
    assert overrides["GIT_CLONE_TOKEN"] == "git-token"
    assert cli_config.load()["access_token"] == "cli-token"


def test_git_clone_token_file_resolves_from_dotenv(tmp_path, monkeypatch):
    from config import _secret_overrides

    token = tmp_path / "git-token"
    token.write_text("dotenv-token\n")
    (tmp_path / ".env").write_text(f"GIT_CLONE_TOKEN_FILE={token}\n")
    monkeypatch.delenv("GIT_CLONE_TOKEN", raising=False)
    monkeypatch.delenv("GIT_CLONE_TOKEN_FILE", raising=False)
    monkeypatch.chdir(tmp_path)

    assert _secret_overrides()["GIT_CLONE_TOKEN"] == "dotenv-token"


@pytest.mark.asyncio
async def test_dynamic_secret_files_stay_external_and_saml_files_are_paired(tmp_path, monkeypatch):
    import services.dynamic_settings as ds

    original_external_settings = dict(ds._external_settings)
    try:
        oauth_secret = tmp_path / "oauth"
        oauth_secret.write_text("oauth-secret")
        saml_key = tmp_path / "saml.key"
        saml_key.write_text("private-key")

        monkeypatch.setenv("OAUTH_CLIENT_SECRET_FILE", str(oauth_secret))
        ds.load_external_settings()
        assert ds.is_externally_managed("oauth.client_secret")
        assert await ds.get("oauth.client_secret") == "oauth-secret"

        monkeypatch.setenv("SAML_SP_PRIVATE_KEY_FILE", str(saml_key))
        with pytest.raises(ValueError, match="must be configured together"):
            ds.load_external_settings()
    finally:
        ds._external_settings.clear()
        ds._external_settings.update(original_external_settings)


@pytest.mark.asyncio
async def test_admin_rejects_writes_to_external_settings(monkeypatch):
    from fastapi import HTTPException

    import services.dynamic_settings as ds
    from api.routes.admin.enterprise_settings import get_setting, upsert_setting
    from schemas.admin import EnterpriseConfigUpdate

    monkeypatch.setattr(ds, "is_externally_managed", lambda key: key == "oauth.client_secret")
    with pytest.raises(HTTPException) as error:
        await upsert_setting(
            "oauth.client_secret",
            EnterpriseConfigUpdate(value="replacement"),
            db=None,
            current_user=None,
        )
    assert error.value.status_code == 409

    with pytest.raises(HTTPException) as error:
        await upsert_setting(
            "saml.sp_private_key",
            EnterpriseConfigUpdate(value="private-key"),
            db=None,
            current_user=None,
        )
    assert error.value.status_code == 409

    with pytest.raises(HTTPException) as error:
        await get_setting("saml.sp_private_key", db=None, current_user=None)
    assert error.value.status_code == 404
    assert "saml.sp_private_key" in ds.SENSITIVE_KEYS


@pytest.mark.asyncio
async def test_worker_loads_external_settings_before_insights(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    import services.dynamic_settings as ds
    import services.insights as insights
    import worker

    load_external = MagicMock()
    load_cache = AsyncMock()
    configure = MagicMock()
    monkeypatch.setattr(ds, "load_external_settings", load_external)
    monkeypatch.setattr(ds, "load_sync_cache", load_cache)
    monkeypatch.setattr(insights, "configure_insights", configure)

    await worker.startup({})

    load_external.assert_called_once_with()
    load_cache.assert_awaited_once_with()
    configure.assert_called_once_with()


def test_server_package_uses_secret_files_and_loopback_defaults():
    root = Path(__file__).resolve().parent.parent
    env_template = (root / "docker/server-package/env.template").read_text()
    compose = (root / "docker/server-package/docker-compose.yml").read_text()
    setup = (root / "docker/server-package/setup.sh").read_text()

    assert "SECRET_KEY_FILE=/run/secrets/secret_key" in env_template
    assert "POSTGRES_PASSWORD_FILE=/run/secrets/postgres_password" in env_template
    assert "OBSERVAL_BIND_ADDRESS=127.0.0.1" in env_template
    assert "${OBSERVAL_BIND_ADDRESS:-127.0.0.1}" in compose
    assert "password_sha256_hex" in setup
    assert "chmod 640" in setup
    assert "OBSERVAL_SECRET_GID" in compose
    assert "openssl rand -hex" in setup
    assert "docker-compose.tls.yml" not in setup
    db_service = compose[compose.index("\n  observal-db:\n") : compose.index("\n  observal-clickhouse:\n")]
    assert "env_file:" not in db_service
    assert "./secrets/postgres:/run/secrets:ro" in db_service
    assert "./secrets:/run/secrets:ro" not in db_service

    clickhouse_service = compose[compose.index("\n  observal-clickhouse:\n") : compose.index("\n  observal-redis:\n")]
    assert "env_file:" not in clickhouse_service
    assert "./secrets/clickhouse:/run/secrets:ro" in clickhouse_service
    assert "cat /run/secrets/clickhouse_password" in clickhouse_service

    observability = (root / "docker/server-package/docker-compose.observability.yml").read_text()
    grafana = observability[observability.index("  observal-grafana:") :]
    assert "env_file:" not in grafana
    assert "./secrets/grafana:/run/secrets:ro" in grafana


def _package_fixture(tmp_path):
    root = Path(__file__).resolve().parent.parent
    install = tmp_path / "install"
    install.mkdir()
    for name in ("setup.sh", "env.template"):
        shutil.copy2(root / "docker/server-package" / name, install / name)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text("#!/bin/sh\nexit 0\n")
    docker.chmod(0o755)
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "OBSERVAL_INSTALL_DIR": str(install)}
    return install, env


def test_server_package_new_install_generates_restricted_secrets(tmp_path):
    install, env = _package_fixture(tmp_path)
    result = subprocess.run(
        ["bash", str(install / "setup.sh")],
        input="\n\ngrafana\n",
        text=True,
        env=env,
        check=True,
        capture_output=True,
    )

    secret = install / "secrets/secret_key"
    assert secret.read_text()
    assert stat.S_IMODE(secret.stat().st_mode) == 0o640
    assert "SECRET_KEY=" not in (install / ".env").read_text()
    assert "OBSERVAL_BIND_ADDRESS=127.0.0.1" in (install / ".env").read_text()
    assert "password_sha256_hex" in (install / "clickhouse/users.d/generated-password.xml").read_text()
    assert "Grafana administrator" in result.stdout
    assert (install / "secrets/grafana/grafana_admin_password").read_text() in result.stdout
    assert "Password file:" in result.stdout


def test_server_package_headless_setup_uses_safe_defaults(tmp_path):
    install, env = _package_fixture(tmp_path)
    result = subprocess.run(
        ["bash", str(install / "setup.sh")],
        input="",
        text=True,
        env=env,
        check=True,
        capture_output=True,
    )

    generated_env = (install / ".env").read_text()
    assert "OBSERVAL_BIND_ADDRESS=127.0.0.1" in generated_env
    assert "FRONTEND_URL=http://localhost:3000" in generated_env
    assert "DEMO_SUPER_ADMIN_EMAIL=super@demo.example" in generated_env
    assert "Password file:" in result.stdout


def test_installer_selects_tty_or_safe_headless_input():
    installer = (Path(__file__).resolve().parent.parent / "install-server.sh").read_text()
    assert "if ( : </dev/tty )" in installer
    assert 'bash "$INSTALL_DIR/setup.sh" </dev/tty' in installer
    assert 'bash "$INSTALL_DIR/setup.sh" </dev/null' in installer


def test_server_package_upgrade_preserves_legacy_public_bind(tmp_path):
    install, env = _package_fixture(tmp_path)
    existing = "SECRET_KEY=existing\nPOSTGRES_PASSWORD=existing\n"
    (install / ".env").write_text(existing)

    subprocess.run(
        ["bash", str(install / "setup.sh")],
        input="n\n",
        text=True,
        env=env,
        check=True,
        capture_output=True,
    )

    updated = (install / ".env").read_text()
    assert existing in updated
    assert "OBSERVAL_BIND_ADDRESS=0.0.0.0" in updated
    assert not (install / "secrets").exists()


def test_server_package_replacement_preserves_existing_credentials(tmp_path):
    install, env = _package_fixture(tmp_path)
    (install / ".env").write_text(
        "SECRET_KEY=existing-secret\n"
        "POSTGRES_PASSWORD=existing-postgres\n"
        "CLICKHOUSE_PASSWORD=existing-clickhouse\n"
        "GRAFANA_ADMIN_PASSWORD=existing-grafana\n"
        "DEMO_SUPER_ADMIN_EMAIL=owner@example.com\n"
    )

    subprocess.run(
        ["bash", str(install / "setup.sh")],
        input="y\n\n\n\n",
        text=True,
        env=env,
        check=True,
        capture_output=True,
    )

    assert (install / "secrets/secret_key").read_text() == "existing-secret"
    assert (install / "secrets/postgres/postgres_password").read_text() == "existing-postgres"
    assert (install / "secrets/clickhouse/clickhouse_password").read_text() == "existing-clickhouse"
    assert (install / "secrets/grafana/clickhouse_password").read_text() == "existing-clickhouse"
    assert (install / "secrets/grafana/grafana_admin_password").read_text() == "existing-grafana"
    assert "existing-postgres" in (install / "secrets/database_url").read_text()
    assert "DEMO_SUPER_ADMIN_EMAIL=owner@example.com" in (install / ".env").read_text()
