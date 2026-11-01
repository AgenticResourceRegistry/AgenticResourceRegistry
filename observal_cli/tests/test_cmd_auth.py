# SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""E2E tests for the ``observal auth`` CLI commands.

Covers (per issue #959):
  * ``observal auth login --server --email --password``
  * ``observal auth whoami``
  * ``observal auth status``

All external services are mocked — no live server is required.
"""

from __future__ import annotations

import json
import sys
from contextlib import ExitStack
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from observal_cli.main import app

runner = CliRunner()


# ── Helpers ────────────────────────────────────────────────────


def _login_response(role: str = "developer") -> dict:
    """Return a canned ``/api/v1/auth/login`` response body."""
    return {
        "access_token": "test-access-token",
        "refresh_token": "test-refresh-token",
        "user": {
            "id": "user-uuid-1",
            "name": "Test User",
            "email": "test@example.com",
            "role": role,
            "username": "testuser",
        },
    }


def _make_response(status: int = 200, json_body: dict | None = None) -> MagicMock:
    """Build a MagicMock ``httpx.Response`` with status + JSON body."""
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_body or {}
    resp.text = ""
    # raise_for_status is a no-op when status < 400
    if status >= 400:
        import httpx

        def _raise() -> None:
            raise httpx.HTTPStatusError("error", request=MagicMock(), response=resp)

        resp.raise_for_status.side_effect = _raise
    else:
        resp.raise_for_status = MagicMock()
    return resp


def _fake_get(url: str, *_args, **_kwargs) -> MagicMock:
    """Route ``httpx.get`` calls used by the login flow."""
    if "/health" in url:
        return _make_response(200, {"initialized": True})
    if "/api/v1/config/public" in url:
        return _make_response(200, {"sso_enabled": False, "saml_enabled": False, "sso_only": False})
    if "/api/v1/config/endpoints" in url:
        return _make_response(
            200,
            {"otlp_http": "http://localhost:4318", "web": "http://localhost:3000"},
        )
    return _make_response(200, {})


def _fake_post_login(url: str, *_args, **_kwargs) -> MagicMock:
    """Route ``httpx.post`` calls used by the login flow."""
    if "/api/v1/auth/login" in url:
        return _make_response(200, _login_response())
    return _make_response(200, {})


def _patch_post_login_hooks(stack: ExitStack) -> MagicMock:
    """Patch the harness-configuration helpers and onboarding step.

    ``observal auth login`` invokes a series of best-effort, side-effecting
    helpers after a successful login (write Claude Code settings, configure
    Kiro hooks, run post-auth onboarding, …).  None of those helpers are
    relevant to the unit under test, so we replace them with no-ops to keep
    the CliRunner sandbox hermetic.

    Returns the ``config.save`` mock so callers can assert on it.
    """
    helper_names = [
        "_post_login_setup",
        "_configure_claude_code",
        "_configure_kiro",
        "_configure_codex",
        "_configure_copilot",
        "_configure_copilot_cli",
        "_configure_opencode",
        "_post_auth_onboarding",
    ]
    for name in helper_names:
        stack.enter_context(patch(f"observal_cli.cmd_auth.{name}"))
    return stack.enter_context(patch("observal_cli.cmd_auth.config.save"))


# ── auth login ─────────────────────────────────────────────────


class TestAuthLogin:
    """``observal auth login --server --email --password``."""

    def test_login_with_credentials_saves_config(self) -> None:
        """Happy path: server reachable, credentials valid, config persisted."""
        with ExitStack() as stack:
            stack.enter_context(patch("observal_cli.cmd_auth.httpx.get", side_effect=_fake_get))
            stack.enter_context(patch("observal_cli.cmd_auth.httpx.post", side_effect=_fake_post_login))
            mock_save = _patch_post_login_hooks(stack)

            result = runner.invoke(
                app,
                [
                    "auth",
                    "login",
                    "--server",
                    "http://localhost:8000",
                    "--email",
                    "test@example.com",
                    "--password",
                    "Sup3rSecret!Pw",
                ],
            )

        assert result.exit_code == 0, result.output
        assert "Logged in" in result.output
        assert "test@example.com" in result.output

        # Config was saved with the credentials we expected.
        assert mock_save.called, "config.save should have been invoked"
        saved_payload = mock_save.call_args[0][0]
        assert saved_payload["server_url"] == "http://localhost:8000"
        assert saved_payload["access_token"] == "test-access-token"
        assert saved_payload["refresh_token"] == "test-refresh-token"
        assert saved_payload["user_id"] == "user-uuid-1"

    def test_login_drops_the_hooks_token_of_a_different_login(self) -> None:
        """Hooks prefer api_key, so one left from another server or user would get every upload refused."""
        from observal_cli import config

        def login(server: str) -> None:
            with ExitStack() as stack:
                stack.enter_context(patch("observal_cli.cmd_auth.httpx.get", side_effect=_fake_get))
                stack.enter_context(patch("observal_cli.cmd_auth.httpx.post", side_effect=_fake_post_login))
                _patch_post_login_hooks(stack)
                stack.enter_context(patch("observal_cli.cmd_auth.config.save", side_effect=config.save))
                args = [
                    "auth",
                    "login",
                    "--server",
                    server,
                    "--email",
                    "test@example.com",
                    "--password",
                    "Sup3rSecret!Pw",
                ]
                result = runner.invoke(app, args)
            assert result.exit_code == 0, result.output

        config.save({"server_url": "http://localhost:8000", "user_id": "user-uuid-1", "api_key": "hooks"})
        login("http://localhost:8000")
        assert config.load_persisted()["api_key"] == "hooks"  # same server and user: still valid
        login("http://other:8000")
        assert "api_key" not in config.load_persisted()

    def test_login_strips_trailing_slash_from_server_url(self) -> None:
        """A trailing slash on ``--server`` must not propagate into the saved URL."""
        with ExitStack() as stack:
            stack.enter_context(patch("observal_cli.cmd_auth.httpx.get", side_effect=_fake_get))
            stack.enter_context(patch("observal_cli.cmd_auth.httpx.post", side_effect=_fake_post_login))
            mock_save = _patch_post_login_hooks(stack)

            result = runner.invoke(
                app,
                [
                    "auth",
                    "login",
                    "--server",
                    "http://localhost:8000/",
                    "--email",
                    "test@example.com",
                    "--password",
                    "Sup3rSecret!Pw",
                ],
            )

        assert result.exit_code == 0, result.output
        saved_payload = mock_save.call_args[0][0]
        assert saved_payload["server_url"] == "http://localhost:8000"

    def test_login_with_invalid_credentials_exits_nonzero(self) -> None:
        """A 401 from the login endpoint should surface a clear error."""
        import httpx

        def fake_post_unauth(url: str, *_a, **_k) -> MagicMock:
            if "/api/v1/auth/login" in url:
                resp = _make_response(401, {"detail": "Invalid email or password"})

                def _raise() -> None:
                    raise httpx.HTTPStatusError("unauthorized", request=MagicMock(), response=resp)

                resp.raise_for_status.side_effect = _raise
                return resp
            return _make_response(200, {})

        with ExitStack() as stack:
            stack.enter_context(patch("observal_cli.cmd_auth.httpx.get", side_effect=_fake_get))
            stack.enter_context(patch("observal_cli.cmd_auth.httpx.post", side_effect=fake_post_unauth))
            stack.enter_context(patch("observal_cli.cmd_auth._post_login_setup"))
            mock_save = stack.enter_context(patch("observal_cli.cmd_auth.config.save"))

            result = runner.invoke(
                app,
                [
                    "auth",
                    "login",
                    "--server",
                    "http://localhost:8000",
                    "--email",
                    "test@example.com",
                    "--password",
                    "wrong-password",
                ],
            )

        assert result.exit_code == 3
        assert "Authentication failed" in result.output
        assert not mock_save.called, "config.save must NOT run on auth failure"

    def test_login_when_server_unreachable_exits_nonzero(self) -> None:
        """If ``/health`` raises ConnectError, exit with a friendly message."""
        import httpx

        def fake_get_unreachable(*_a, **_k):
            raise httpx.ConnectError("connection refused")

        with ExitStack() as stack:
            stack.enter_context(patch("observal_cli.cmd_auth.httpx.get", side_effect=fake_get_unreachable))
            stack.enter_context(patch("observal_cli.cmd_auth.httpx.post"))
            mock_save = stack.enter_context(patch("observal_cli.cmd_auth.config.save"))

            result = runner.invoke(
                app,
                [
                    "auth",
                    "login",
                    "--server",
                    "http://localhost:9999",
                    "--email",
                    "test@example.com",
                    "--password",
                    "Sup3rSecret!Pw",
                ],
            )

        assert result.exit_code == 9
        assert "Cannot reach" in result.output
        assert not mock_save.called


# ── auth whoami ────────────────────────────────────────────────


class TestAuthWhoami:
    """``observal auth whoami``."""

    @pytest.fixture(autouse=True)
    def _logged_in(self):
        cfg = {"server_url": "http://localhost:8000", "access_token": "tok"}
        with patch("observal_cli.cmd_auth.config.get_or_exit", return_value=cfg):
            yield

    def test_whoami_outputs_email_and_role(self) -> None:
        """Default (table) output must contain the user's email and role."""
        user_data = {
            "id": "user-uuid-99",
            "name": "Alice",
            "email": "alice@example.com",
            "role": "admin",
            "username": "alice",
        }
        with patch("observal_cli.cmd_auth.client.get", return_value=user_data) as mock_get:
            result = runner.invoke(app, ["auth", "whoami"])

        assert result.exit_code == 0, result.output
        mock_get.assert_called_once_with("/api/v1/auth/whoami")
        assert "alice@example.com" in result.output
        assert "admin" in result.output

    def test_whoami_json_output(self) -> None:
        """``--output json`` must emit a JSON document with the same fields."""
        user_data = {
            "id": "user-uuid-99",
            "name": "Alice",
            "email": "alice@example.com",
            "role": "admin",
            "username": "alice",
        }
        with patch("observal_cli.cmd_auth.client.get", return_value=user_data):
            result = runner.invoke(app, ["auth", "whoami", "--output", "json"])

        assert result.exit_code == 0, result.output
        assert result.stderr == ""
        assert json.loads(result.stdout) == user_data

    def test_whoami_rejects_removed_plain_output(self) -> None:
        result = runner.invoke(app, ["auth", "whoami", "--output", "plain"])

        assert result.exit_code == 2
        assert "Error" in result.output
        assert "plain" in result.output

    def test_whoami_unset_username_is_handled(self) -> None:
        """A user without a username should still render cleanly."""
        user_data = {
            "id": "user-uuid-99",
            "name": "Bob",
            "email": "bob@example.com",
            "role": "developer",
            # username intentionally omitted
        }
        with patch("observal_cli.cmd_auth.client.get", return_value=user_data):
            result = runner.invoke(app, ["auth", "whoami"])

        assert result.exit_code == 0, result.output
        assert "bob@example.com" in result.output
        assert "developer" in result.output


# ── auth status ────────────────────────────────────────────────


class TestAuthStatus:
    """``observal auth status``."""

    def test_status_reports_ok_when_healthy(self) -> None:
        """A reachable server with stored credentials renders ``ok`` + latency."""
        cfg = {"server_url": "http://localhost:8000", "access_token": "tok"}
        with (
            patch("observal_cli.cmd_auth.config.load", return_value=cfg),
            patch("observal_cli.cmd_auth.client.health", return_value=(True, 42.0)),
        ):
            result = runner.invoke(app, ["auth", "status"])

        assert result.exit_code == 0, result.output
        assert "http://localhost:8000" in result.output
        assert "configured" in result.output
        assert "ok" in result.output

    def test_status_reports_unreachable_when_health_fails(self) -> None:
        """A failing health probe renders ``unreachable``."""
        cfg = {"server_url": "http://localhost:8000", "access_token": "tok"}
        with (
            patch("observal_cli.cmd_auth.config.load", return_value=cfg),
            patch("observal_cli.cmd_auth.client.health", return_value=(False, 0.0)),
        ):
            result = runner.invoke(app, ["auth", "status"])

        assert result.exit_code == 9
        assert "unreachable" in result.output

    def test_status_reports_auth_not_set_when_no_token(self) -> None:
        """When no access token is stored, the status output flags it."""
        cfg = {"server_url": "http://localhost:8000", "access_token": ""}
        with (
            patch("observal_cli.cmd_auth.config.load", return_value=cfg),
            patch("observal_cli.cmd_auth.client.health", return_value=(False, 0.0)),
        ):
            result = runner.invoke(app, ["auth", "status"])

        assert result.exit_code == 3
        assert "not configured" in result.output


class TestAuthJsonOutputs:
    def test_login_json_is_safe_and_skips_setup(self) -> None:
        with ExitStack() as stack:
            stack.enter_context(patch("observal_cli.cmd_auth.httpx.get", side_effect=_fake_get))
            stack.enter_context(patch("observal_cli.cmd_auth.httpx.post", side_effect=_fake_post_login))
            stack.enter_context(patch("observal_cli.cmd_auth.config.load", return_value={}))
            save = stack.enter_context(patch("observal_cli.cmd_auth.config.save"))
            setup = stack.enter_context(patch("observal_cli.cmd_auth._post_login_setup"))
            banner = stack.enter_context(patch("observal_cli.cmd_auth.welcome_banner"))
            spinner = stack.enter_context(patch("observal_cli.cmd_auth.spinner"))

            result = runner.invoke(
                app,
                [
                    "auth",
                    "login",
                    "--server",
                    "http://localhost:8000",
                    "--email",
                    "test@example.com",
                    "--password",
                    "Sup3rSecret!Pw",
                    "--output",
                    "json",
                ],
            )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["authenticated"] is True
        assert payload["user"]["email"] == "test@example.com"
        assert "access_token" not in result.stdout
        assert "refresh_token" not in result.stdout
        assert "Sup3rSecret!Pw" not in result.stdout
        save.assert_called_once()
        setup.assert_not_called()
        banner.assert_not_called()
        spinner.assert_not_called()

    def test_login_json_sso_preserves_stream_events_on_failure(self) -> None:
        def device_flow(*_args, **_kwargs):
            print('{"event":"authorization_required"}')
            from observal_cli.errors import ErrorCategory, fail

            fail(
                ErrorCategory.AUTH,
                "Device authorization expired.",
                operation="Complete device authorization",
            )

        with (
            patch("observal_cli.cmd_auth.httpx.get", side_effect=_fake_get),
            patch("observal_cli.cmd_auth.config.load", return_value={}),
            patch("observal_cli.cmd_auth._ensure_cli_matches_server"),
            patch("observal_cli.cmd_auth._do_device_flow_login", side_effect=device_flow),
        ):
            result = runner.invoke(
                app,
                ["auth", "login", "--server", "http://localhost:8000", "--sso", "--output", "json"],
            )

        assert result.exit_code == 3
        assert json.loads(result.stdout) == {"event": "authorization_required"}
        assert json.loads(result.stderr)["error"]["category"] == "authentication"

    def test_login_json_rejects_incomplete_credentials_without_prompt(self) -> None:
        prompt = MagicMock()
        with (
            patch("observal_cli.cmd_auth.httpx.get", side_effect=_fake_get),
            patch("observal_cli.cmd_auth.config.load", return_value={}),
            patch("observal_cli.cmd_auth.text_input", prompt),
            patch("observal_cli.cmd_auth.password_input", prompt),
        ):
            result = runner.invoke(
                app,
                ["auth", "login", "--server", "http://localhost:8000", "--output", "json"],
            )

        assert result.exit_code == 7
        assert result.stdout == ""
        assert json.loads(result.stderr)["error"]["category"] == "validation"
        prompt.assert_not_called()

    def test_logout_json_reports_remote_and_local_results(self, tmp_path) -> None:
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "server_url": "http://localhost:8000",
                    "access_token": "access",
                    "refresh_token": "refresh",
                }
            )
        )
        response = _make_response()
        response.is_success = True
        with (
            patch("observal_cli.cmd_auth.config.CONFIG_FILE", config_file),
            patch("observal_cli.cmd_auth.config.CONFIG_DIR", tmp_path),
            patch("observal_cli.config.CONFIG_FILE", config_file),
            patch("observal_cli.config.CONFIG_DIR", tmp_path),
            patch("observal_cli.cmd_auth.httpx.post", return_value=response),
        ):
            result = runner.invoke(app, ["auth", "logout", "--output", "json"])

        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == {
            "logged_out": True,
            "config_existed": True,
            "local_tokens_cleared": True,
            "remote_revocation_attempted": True,
            "remote_revoked": True,
        }
        assert json.loads(config_file.read_text()) == {"server_url": "http://localhost:8000"}

    def test_status_json_is_structured(self) -> None:
        telemetry_buffer = ModuleType("observal_cli.telemetry_buffer")
        telemetry_buffer.stats = lambda: {  # type: ignore[attr-defined]
            "total": 2,
            "pending": 1,
            "bytes": 512,
            "oldest_pending": "2026-08-14 12:00:00",
        }
        with (
            patch.dict(sys.modules, {"observal_cli.telemetry_buffer": telemetry_buffer}),
            patch(
                "observal_cli.cmd_auth.config.load",
                return_value={"server_url": "http://localhost:8000", "access_token": "token"},
            ),
            patch("observal_cli.cmd_auth.client.health", return_value=(True, 42.5)),
        ):
            result = runner.invoke(app, ["auth", "status", "--output", "json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["health"] == {"reachable": True, "latency_ms": 42.5}
        assert payload["outbox"]["pending"] == 1

    def test_status_json_failure_uses_stable_error(self) -> None:
        with patch("observal_cli.cmd_auth.config.load", return_value={"server_url": "http://localhost:8000"}):
            result = runner.invoke(app, ["auth", "status", "--output", "json"])

        assert result.exit_code == 3
        assert result.stdout == ""
        error = json.loads(result.stderr)["error"]
        assert error["category"] == "authentication"
        assert error["exit_code"] == 3

    def test_change_password_json_never_prompts(self) -> None:
        password_prompt = MagicMock()
        with (
            patch.dict(
                "os.environ",
                {
                    "OBSERVAL_CURRENT_PASSWORD": "CurrentPassword1!",
                    "OBSERVAL_NEW_PASSWORD": "ValidPassword1!",
                },
                clear=False,
            ),
            patch(
                "observal_cli.cmd_auth.config.load",
                return_value={"server_url": "http://localhost:8000", "access_token": "token"},
            ),
            patch(
                "observal_cli.cmd_auth.client.put",
                return_value={"message": "Password changed"},
            ),
            patch("observal_cli.cmd_auth.password_input", password_prompt),
        ):
            result = runner.invoke(app, ["auth", "change-password", "--output", "json"])

        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == {"message": "Password changed"}
        password_prompt.assert_not_called()
        assert "CurrentPassword1!" not in result.output
        assert "ValidPassword1!" not in result.output

    def test_set_username_json_preserves_server_object(self) -> None:
        user = {"id": "user-1", "username": "alice", "email": "alice@example.com"}
        with (
            patch("observal_cli.cmd_auth.client.put", return_value=user),
            patch("observal_cli.cmd_auth.config.save") as save,
        ):
            result = runner.invoke(app, ["auth", "set-username", "alice", "--output", "json"])

        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == user
        save.assert_called_once_with({"username": "alice"})


class TestConfigContracts:
    def test_show_json_uses_root_contract_without_secrets(self) -> None:
        stored = {
            "server_url": "https://registry.example.test",
            "timeout": 30,
            "access_token": "private-access-token",
            "refresh_token": "private-refresh-token",
        }
        with patch("observal_cli.cmd_auth.config.load", return_value=stored):
            result = runner.invoke(app, ["config", "show", "--output", "json"])

        assert result.exit_code == 0, result.output
        assert result.stderr == ""
        payload = json.loads(result.stdout)
        assert payload["access_token_configured"] is True
        assert payload["refresh_token_configured"] is True
        assert "private-access-token" not in result.stdout
        assert "private-refresh-token" not in result.stdout

    def test_set_invalid_value_uses_json_error_contract(self) -> None:
        result = runner.invoke(app, ["config", "set", "output", "json", "--output", "json"])

        assert result.exit_code == 7
        assert result.stdout == ""
        error = json.loads(result.stderr)["error"]
        assert error["category"] == "validation"
        assert error["resource"] == "output"

    def test_set_permission_failure_uses_permission_exit(self) -> None:
        denied = PermissionError(13, "permission denied", "/root/.observal/config.json")
        with (
            patch("observal_cli.cmd_auth.config.save", side_effect=denied),
            patch("observal_cli.cmd_auth.config.load", return_value={"timeout": 30}),
        ):
            result = runner.invoke(app, ["config", "set", "timeout", "30", "--output", "json"])

        assert result.exit_code == 4
        assert result.stdout == ""
        assert json.loads(result.stderr)["error"]["category"] == "permission"

    def test_aliases_empty_json_keeps_stable_shape(self) -> None:
        with patch("observal_cli.cmd_auth.config.load_aliases", return_value={}):
            result = runner.invoke(app, ["config", "aliases", "--output", "json"])

        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == {"items": [], "total": 0, "page": 1, "page_size": 0}


# ── post-login harness detection ───────────────────────────────


class TestPostLoginHarnessDetection:
    """Best-effort harness setup after login."""

    def test_opencode_detects_off_path_installer_binary(self, tmp_path, monkeypatch) -> None:
        """OpenCode's installer writes ~/.opencode/bin/opencode without always updating PATH."""
        from observal_cli.cmd_auth import _configure_opencode

        opencode_bin = tmp_path / ".opencode" / "bin" / "opencode"
        opencode_bin.parent.mkdir(parents=True)
        opencode_bin.write_text("")

        monkeypatch.setattr("observal_cli.cmd_auth.Path.home", lambda: tmp_path)
        monkeypatch.setattr("observal_cli.cmd_auth.shutil.which", lambda _name: None)

        with (
            patch("observal_cli.cmd_auth.typer.confirm", return_value=True),
            patch("observal_cli.cmd_auth._run_doctor_patch") as patch_doctor,
        ):
            _configure_opencode("http://localhost")

        patch_doctor.assert_called_once_with("opencode")

    def test_doctor_patch_uses_current_flags(self) -> None:
        from observal_cli.cmd_auth import _run_doctor_patch

        completed = MagicMock(returncode=0, stdout="", stderr="")
        with patch("subprocess.run", return_value=completed) as run:
            _run_doctor_patch("cursor")

        assert run.call_args.args[0][-3:] == ["patch", "--harness", "cursor"]


if __name__ == "__main__":  # pragma: no cover - manual debug entry point
    pytest.main([__file__, "-v"])
