# SPDX-FileCopyrightText: 2026 ahmadalguydi <ahmadalgaidy@hotmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Behavioral tests for :mod:`observal_cli.cmd_auth`."""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import time
import webbrowser
from contextlib import nullcontext
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, call

import httpx
import pytest
import typer
from typer.testing import CliRunner

import observal_cli.cmd_auth as auth
from observal_cli.errors import CliError, ErrorCategory, ExitCode

if TYPE_CHECKING:
    from pathlib import Path

SERVER_URL = "https://registry.example.test"
ACCESS_TOKEN = "test-access-token-value"
REFRESH_TOKEN = "test-refresh-token-value"
VALID_PASSWORD = "ValidPassword1!"
_MISSING = object()
_HARNESS_DIRS = {
    "_configure_cursor": ".cursor",
    "_configure_kiro": ".kiro",
    "_configure_codex": ".codex",
    "_configure_claude_code": ".claude",
}


def _response(
    status_code: int = 200,
    data: object = _MISSING,
    *,
    text: str | None = None,
) -> httpx.Response:
    request = httpx.Request("GET", SERVER_URL)
    if data is not _MISSING:
        return httpx.Response(status_code, json=data, request=request)
    return httpx.Response(status_code, text=text or "", request=request)


def _responder(result: object) -> MagicMock:
    """Return a mock that raises exceptions and returns ordinary responses."""
    if isinstance(result, Exception):
        return MagicMock(side_effect=result)
    return MagicMock(return_value=result)


def _install_harness(function_name: str, home: Path) -> None:
    directory = _HARNESS_DIRS.get(function_name)
    if directory:
        (home / directory).mkdir(exist_ok=True)
    if function_name == "_configure_copilot":
        (home / ".vscode" / "extensions" / "github.copilot-1.0.0").mkdir(parents=True)
    if function_name == "_configure_opencode":
        binary = home / ".opencode" / "bin" / "opencode"
        binary.parent.mkdir(parents=True)
        binary.write_text("")


def _user(**overrides: str) -> dict[str, str]:
    return {
        "id": "user-123",
        "name": "Ada Lovelace",
        "email": "ada@example.test",
        "role": "admin",
        "username": "ada",
        **overrides,
    }


def _login_payload(**overrides: object) -> dict[str, object]:
    return {
        "access_token": ACCESS_TOKEN,
        "refresh_token": REFRESH_TOKEN,
        "user": _user(),
        **overrides,
    }


def _device_authorization(**overrides: object) -> dict[str, object]:
    return {
        "device_code": "private-device-code",
        "user_code": "ABCD-EFGH",
        "verification_uri": "http://localhost/device",
        "verification_uri_complete": "http://localhost/device?code=ABCD-EFGH",
        "expires_in": 10,
        "interval": 1,
        **overrides,
    }


@pytest.fixture(autouse=True)
def _isolate_side_effects(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(auth, "spinner", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(auth, "welcome_banner", MagicMock())
    monkeypatch.setattr(auth.config, "CONFIG_FILE", tmp_path / "config.json")


@pytest.fixture
def printed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    messages: list[str] = []

    def record(*values: object, **_kwargs: object) -> None:
        messages.append(" ".join(str(value) for value in values))

    monkeypatch.setattr(auth, "rprint", record)
    return messages


@pytest.fixture(scope="module")
def config_cli() -> typer.Typer:
    root = typer.Typer()
    local_config_app = typer.Typer()
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(auth, "config_app", local_config_app)
        auth.register_config(root)
    return root


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def device_runtime(monkeypatch: pytest.MonkeyPatch) -> tuple[_Clock, MagicMock]:
    clock = _Clock()
    browser_open = MagicMock(return_value=True)
    monkeypatch.setattr(time, "monotonic", clock.monotonic)
    monkeypatch.setattr(time, "sleep", clock.sleep)
    monkeypatch.setattr(platform, "system", lambda: "Other")
    monkeypatch.setattr(webbrowser, "open", browser_open)
    return clock, browser_open


@pytest.mark.parametrize(
    ("password", "missing"),
    [
        ("Short1!", ["At least 12 characters"]),
        ("longpassword1!", ["One uppercase letter"]),
        ("Longpassword!", ["One number"]),
        ("Longpassword1", ["One special character"]),
        (
            "short",
            ["At least 12 characters", "One uppercase letter", "One number", "One special character"],
        ),
        (VALID_PASSWORD, []),
    ],
)
def test_validate_password_reports_exact_requirements(password: str, missing: list[str]) -> None:
    assert auth._validate_password(password) == missing


def test_prompt_password_retries_without_echoing_password(
    monkeypatch: pytest.MonkeyPatch,
    printed: list[str],
) -> None:
    password_input = MagicMock(side_effect=["weak", VALID_PASSWORD])
    monkeypatch.setattr(auth, "password_input", password_input)

    assert auth._prompt_password("Choose password") == VALID_PASSWORD

    assert password_input.call_args_list == [call("Choose password"), call("Choose password")]
    output = "\n".join(printed)
    assert "Password does not meet requirements" in output
    assert "At least 12 characters" in output
    assert "weak" not in output
    assert VALID_PASSWORD not in output


@pytest.mark.parametrize("cli_version", ["0.0.0"])
def test_version_check_skips_uninstalled_cli(
    cli_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import observal_cli.version_check as version_check

    get = MagicMock()
    monkeypatch.setattr(version_check, "get_current_version", lambda: cli_version)
    monkeypatch.setattr(auth.httpx, "get", get)

    auth._ensure_cli_matches_server(SERVER_URL)

    get.assert_not_called()


@pytest.mark.parametrize(
    "server_response",
    [
        RuntimeError("offline"),
        _response(200, {}),
        _response(200, {"server_version": "dev"}),
        _response(200, {"server_version": "invalid version"}),
        _response(200, {"server_version": "1.2.3"}),
    ],
)
def test_version_check_allows_unavailable_or_compatible_server(
    server_response: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import observal_cli.version_check as version_check

    monkeypatch.setattr(version_check, "get_current_version", lambda: "1.2.3")
    get = _responder(server_response)
    monkeypatch.setattr(auth.httpx, "get", get)

    auth._ensure_cli_matches_server(SERVER_URL)


@pytest.mark.parametrize(
    ("cli_version", "server_version", "expected_text", "upgrade_result"),
    [
        ("2.0.0", "1.9.0", "self downgrade", None),
        ("1.9.0", "2.0.0", "install-observal 2.0.0", "install-observal 2.0.0"),
    ],
)
def test_version_check_blocks_mismatch_with_correct_remediation(
    cli_version: str,
    server_version: str,
    expected_text: str,
    upgrade_result: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import observal_cli.install_detector as install_detector
    import observal_cli.version_check as version_check

    monkeypatch.setattr(version_check, "get_current_version", lambda: cli_version)
    monkeypatch.setattr(auth.httpx, "get", lambda *_args, **_kwargs: _response(200, {"server_version": server_version}))
    upgrade = MagicMock(return_value=upgrade_result)
    monkeypatch.setattr(install_detector, "upgrade_command", upgrade)

    with pytest.raises(CliError) as exc_info:
        auth._ensure_cli_matches_server(SERVER_URL)

    assert exc_info.value.exit_code == ExitCode.VERSION
    assert exc_info.value.category is ErrorCategory.VERSION
    assert expected_text in (exc_info.value.remediation or "")
    if upgrade_result is None:
        upgrade.assert_not_called()
    else:
        upgrade.assert_called_once_with(server_version)


def test_version_check_dev_server_gives_source_install_command(monkeypatch: pytest.MonkeyPatch) -> None:
    import observal_cli.version_check as version_check

    monkeypatch.setattr(version_check, "get_current_version", lambda: "1.13.1")
    monkeypatch.setattr(auth.httpx, "get", lambda *_args, **_kwargs: _response(200, {"server_version": "1.15.0-dev.0"}))

    with pytest.raises(CliError) as exc_info:
        auth._ensure_cli_matches_server(SERVER_URL)

    assert exc_info.value.category is ErrorCategory.VERSION
    assert "git+https://github.com/AgenticResourceRegistry/AgenticResourceRegistry@main" in (
        exc_info.value.remediation or ""
    )
    assert "observal-cli==1.15.0-dev.0" not in (exc_info.value.remediation or "")


def _prepare_login(
    monkeypatch: pytest.MonkeyPatch,
    *,
    initialized: bool = True,
    public: dict[str, object] | None = None,
    public_status: int = 200,
    previous_server: str = "",
) -> tuple[MagicMock, MagicMock, MagicMock]:
    import observal_cli.lockfile as lockfile

    responses = [
        _response(200, {"initialized": initialized}),
        _response(public_status, public or {}),
    ]
    get = MagicMock(side_effect=responses)
    ensure_version = MagicMock()
    migrate = MagicMock()
    monkeypatch.setattr(auth.httpx, "get", get)
    monkeypatch.setattr(auth, "_ensure_cli_matches_server", ensure_version)
    monkeypatch.setattr(auth.config, "load", lambda: {"server_url": previous_server})
    monkeypatch.setattr(lockfile, "migrate_lockfile_v1", migrate)
    return get, ensure_version, migrate


@pytest.mark.parametrize(
    ("error", "category", "message"),
    [
        (httpx.ConnectError("connection refused"), ErrorCategory.UNAVAILABLE, "Cannot reach"),
        (RuntimeError("bad health response"), ErrorCategory.UNEXPECTED, "health check failed"),
    ],
)
def test_login_stops_when_health_check_fails(
    error: Exception,
    category: ErrorCategory,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth.config, "load", lambda: {})
    monkeypatch.setattr(auth.httpx, "get", MagicMock(side_effect=error))

    with pytest.raises(CliError) as exc_info:
        auth.login(SERVER_URL, "ada@example.test", VALID_PASSWORD, None, False, False)

    assert exc_info.value.category is category
    assert message in exc_info.value.message


def test_login_initializes_fresh_server_and_persists_only_returned_tokens(
    monkeypatch: pytest.MonkeyPatch,
    printed: list[str],
) -> None:
    _, ensure_version, migrate = _prepare_login(
        monkeypatch,
        initialized=False,
        previous_server="https://old.example.test",
    )
    text_input = MagicMock(side_effect=["ada@example.test", "Ada"])
    password_input = MagicMock(return_value=VALID_PASSWORD)
    post = MagicMock(return_value=_response(200, _login_payload()))
    save = MagicMock()
    setup = MagicMock()
    monkeypatch.setattr(auth, "text_input", text_input)
    monkeypatch.setattr(auth, "_prompt_password", MagicMock(return_value=VALID_PASSWORD))
    monkeypatch.setattr(auth, "password_input", password_input)
    monkeypatch.setattr(auth.httpx, "post", post)
    monkeypatch.setattr(auth, "_fetch_endpoints", lambda _url: {"web": "https://app.example.test"})
    monkeypatch.setattr(auth.config, "save", save)
    monkeypatch.setattr(auth, "_post_login_setup", setup)

    auth.login(f"{SERVER_URL}/", None, None, None, False, False)

    ensure_version.assert_called_once_with(SERVER_URL)
    migrate.assert_called_once_with("https://old.example.test")
    post.assert_called_once_with(
        f"{SERVER_URL}/api/v1/auth/init",
        json={"email": "ada@example.test", "name": "Ada", "password": VALID_PASSWORD},
        timeout=30,
    )
    save.assert_called_once_with(
        {
            "server_url": SERVER_URL,
            "access_token": ACCESS_TOKEN,
            "refresh_token": REFRESH_TOKEN,
            "user_id": "user-123",
            "user_name": "Ada Lovelace",
            "username": "ada",
            "web_url": "https://app.example.test",
        }
    )
    setup.assert_called_once_with()
    output = "\n".join(printed)
    assert "Logged in as Ada Lovelace" in output
    assert VALID_PASSWORD not in output
    assert ACCESS_TOKEN not in output
    assert REFRESH_TOKEN not in output


def test_login_fresh_server_rejects_mismatched_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    printed: list[str],
) -> None:
    _prepare_login(monkeypatch, initialized=False)
    post = MagicMock()
    monkeypatch.setattr(auth, "_prompt_password", lambda _prompt: VALID_PASSWORD)
    monkeypatch.setattr(auth, "password_input", lambda _prompt: "DifferentPassword2!")
    monkeypatch.setattr(auth.httpx, "post", post)

    with pytest.raises(typer.Exit):
        auth.login(SERVER_URL, "ada@example.test", None, "Ada", False, False)

    post.assert_not_called()
    assert "Passwords do not match" in "\n".join(printed)


@pytest.mark.parametrize(
    ("status", "body", "category"),
    [
        (400, "Already Initialized by another request", ErrorCategory.CONFLICT),
        (500, "database unavailable", ErrorCategory.UNAVAILABLE),
    ],
)
def test_login_handles_admin_initialization_failures(
    status: int,
    body: str,
    category: ErrorCategory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_login(monkeypatch, initialized=False)
    monkeypatch.setattr(auth.httpx, "post", lambda *_args, **_kwargs: _response(status, text=body))
    save = MagicMock()
    monkeypatch.setattr(auth.config, "save", save)

    with pytest.raises(CliError) as exc_info:
        auth.login(SERVER_URL, "ada@example.test", VALID_PASSWORD, "Ada", False, False)

    save.assert_not_called()
    assert exc_info.value.category is category


def test_human_login_prompts_with_blank_localhost_default(monkeypatch: pytest.MonkeyPatch) -> None:
    stale = "http://localhost:8000"
    selected = "http://localhost"
    monkeypatch.setattr(auth.config, "load", lambda: {"server_url": stale})
    monkeypatch.setattr("observal_cli.lockfile.migrate_lockfile_v1", MagicMock())
    monkeypatch.setattr(
        auth.httpx,
        "get",
        MagicMock(side_effect=[_response(200, {"initialized": True}), _response(200, {})]),
    )
    monkeypatch.setattr(auth, "_ensure_cli_matches_server", MagicMock())
    prompt = MagicMock(return_value="")
    monkeypatch.setattr(auth, "text_input", prompt)
    password_login = MagicMock()
    monkeypatch.setattr(auth, "_do_password_login", password_login)

    auth.login(None, "ada", VALID_PASSWORD, None, False, False)

    prompt.assert_called_once_with("Server URL (leave blank for http://localhost)", default="")
    password_login.assert_called_once_with(
        selected, "ada", VALID_PASSWORD, output=auth.OutputMode.table, run_setup=True
    )


def test_json_login_recovers_stale_local_port_without_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    stale = "http://localhost:8000"
    monkeypatch.setattr(auth.config, "load", lambda: {"server_url": stale})
    monkeypatch.setattr("observal_cli.lockfile.migrate_lockfile_v1", MagicMock())
    monkeypatch.setattr(
        auth.httpx,
        "get",
        MagicMock(
            side_effect=[
                httpx.ConnectError("stale port"),
                _response(200, {"initialized": True}),
                _response(200, {}),
            ]
        ),
    )
    monkeypatch.setattr(auth, "_ensure_cli_matches_server", MagicMock())
    prompt = MagicMock(side_effect=AssertionError("JSON mode must not prompt"))
    monkeypatch.setattr(auth, "text_input", prompt)
    password_login = MagicMock()
    monkeypatch.setattr(auth, "_do_password_login", password_login)

    auth.login(None, "ada", VALID_PASSWORD, None, False, False, output=auth.OutputMode.json)

    prompt.assert_not_called()
    password_login.assert_called_once_with(
        "http://localhost", "ada", VALID_PASSWORD, output=auth.OutputMode.json, run_setup=False
    )


def test_login_with_credentials_routes_to_password_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, migrate = _prepare_login(
        monkeypatch,
        public={"sso_enabled": False, "saml_enabled": False, "sso_only": False},
        previous_server=SERVER_URL,
    )
    password_login = MagicMock()
    monkeypatch.setattr(auth, "_do_password_login", password_login)

    auth.login(f"{SERVER_URL}/", "ada", VALID_PASSWORD, None, False, False)

    password_login.assert_called_once_with(
        SERVER_URL, "ada", VALID_PASSWORD, output=auth.OutputMode.table, run_setup=True
    )
    migrate.assert_called_once_with(SERVER_URL)


@pytest.mark.parametrize(
    ("choice", "public", "expected_direct", "expected_provider"),
    [
        ("2", {}, False, None),
        ("3", {"sso_enabled": True}, True, "oidc"),
        ("3", {"saml_enabled": True}, True, "saml"),
        ("4", {"sso_enabled": True, "saml_enabled": True}, True, "saml"),
        ("3", {"google_sso_enabled": True}, True, "google"),
        ("4", {"sso_enabled": True, "google_sso_enabled": True}, True, "google"),
        ("5", {"sso_enabled": True, "github_sso_enabled": True, "saml_enabled": True}, True, "saml"),
    ],
)
def test_login_method_menu_routes_browser_flows(
    choice: str,
    public: dict[str, object],
    expected_direct: bool,
    expected_provider: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_login(monkeypatch, public=public)
    monkeypatch.setattr(auth, "quick_choice", lambda _prompt, _valid: choice)
    device_login = MagicMock()
    monkeypatch.setattr(auth, "_do_device_flow_login", device_login)

    auth.login(SERVER_URL, None, None, None, False, False)

    device_login.assert_called_once_with(
        SERVER_URL,
        direct_sso=expected_direct,
        provider=expected_provider,
        output=auth.OutputMode.table,
        run_setup=True,
    )


@pytest.mark.parametrize(
    ("sso", "saml", "provider_option", "provider"),
    [
        (False, True, None, "saml"),
        (False, False, "google", "google"),
        (True, False, "GitHub", "github"),
    ],
)
def test_login_explicit_provider_bypasses_prompts(
    sso: bool,
    saml: bool,
    provider_option: str | None,
    provider: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_login(
        monkeypatch,
        public={"sso_enabled": True, "google_sso_enabled": True, "github_sso_enabled": True, "saml_enabled": True},
    )
    quick_choice = MagicMock()
    device_login = MagicMock()
    monkeypatch.setattr(auth, "quick_choice", quick_choice)
    monkeypatch.setattr(auth, "_do_device_flow_login", device_login)

    auth.login(SERVER_URL, None, None, None, sso, saml, provider=provider_option)

    quick_choice.assert_not_called()
    device_login.assert_called_once_with(
        SERVER_URL,
        direct_sso=True,
        provider=provider,
        output=auth.OutputMode.table,
        run_setup=True,
    )


def test_login_sso_only_server_forces_browser_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_login(monkeypatch, public={"sso_only": True, "sso_enabled": True})
    device_login = MagicMock()
    monkeypatch.setattr(auth, "_do_device_flow_login", device_login)

    auth.login(SERVER_URL, None, None, None, False, False)

    device_login.assert_called_once_with(
        SERVER_URL,
        direct_sso=True,
        provider="oidc",
        output=auth.OutputMode.table,
        run_setup=True,
    )


@pytest.mark.parametrize(
    ("public", "answer", "expected_provider"),
    [
        ({"sso_enabled": True, "google_sso_enabled": True}, "2", "google"),
        ({"sso_enabled": True, "google_sso_enabled": True, "saml_enabled": True}, "1", "oidc"),
        ({"sso_only": True, "sso_enabled": True, "github_sso_enabled": True}, "2", "github"),
    ],
)
def test_bare_sso_asks_which_provider_when_several_are_enabled(
    public: dict[str, object],
    answer: str,
    expected_provider: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_login(monkeypatch, public=public)
    quick_choice = MagicMock(return_value=answer)
    device_login = MagicMock()
    monkeypatch.setattr(auth, "quick_choice", quick_choice)
    monkeypatch.setattr(auth, "_do_device_flow_login", device_login)
    monkeypatch.setattr(auth.sys, "stdin", SimpleNamespace(isatty=lambda: True))

    auth.login(SERVER_URL, None, None, None, not public.get("sso_only"), False)

    prompt, valid = quick_choice.call_args.args
    assert prompt == "SSO provider"
    assert len(valid) == sum(1 for key, value in public.items() if key != "sso_only" and value)
    device_login.assert_called_once_with(
        SERVER_URL,
        direct_sso=True,
        provider=expected_provider,
        output=auth.OutputMode.table,
        run_setup=True,
    )


def test_bare_sso_uses_the_only_enabled_provider_without_asking(monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_login(monkeypatch, public={"google_sso_enabled": True})
    quick_choice = MagicMock()
    device_login = MagicMock()
    monkeypatch.setattr(auth, "quick_choice", quick_choice)
    monkeypatch.setattr(auth, "_do_device_flow_login", device_login)

    auth.login(SERVER_URL, None, None, None, True, False)

    quick_choice.assert_not_called()
    assert device_login.call_args.kwargs["provider"] == "google"


def test_bare_sso_without_terminal_keeps_server_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_login(monkeypatch, public={"sso_enabled": True, "google_sso_enabled": True})
    quick_choice = MagicMock(side_effect=AssertionError("non-interactive stdin must not prompt"))
    device_login = MagicMock()
    monkeypatch.setattr(auth, "quick_choice", quick_choice)
    monkeypatch.setattr(auth, "_do_device_flow_login", device_login)
    monkeypatch.setattr(auth.sys, "stdin", SimpleNamespace(isatty=lambda: False))

    auth.login(SERVER_URL, None, None, None, True, False)

    assert device_login.call_args.kwargs["provider"] is None


@pytest.mark.parametrize("provider", ["google", "okta"])
def test_login_rejects_saml_with_a_different_provider(provider: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_login(monkeypatch, public={"sso_enabled": True, "google_sso_enabled": True, "saml_enabled": True})
    device_login = MagicMock()
    monkeypatch.setattr(auth, "_do_device_flow_login", device_login)

    with pytest.raises(CliError) as exc_info:
        auth.login(SERVER_URL, None, None, None, False, True, provider=provider)

    assert exc_info.value.category is ErrorCategory.VALIDATION
    assert "--saml and --provider" in exc_info.value.message
    device_login.assert_not_called()


def test_login_accepts_saml_with_matching_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_login(monkeypatch, public={"saml_enabled": True})
    device_login = MagicMock()
    monkeypatch.setattr(auth, "_do_device_flow_login", device_login)

    auth.login(SERVER_URL, None, None, None, False, True, provider="SAML")

    assert device_login.call_args.kwargs["provider"] == "saml"


def test_json_bare_sso_keeps_server_default_without_prompting(monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_login(monkeypatch, public={"sso_enabled": True, "google_sso_enabled": True})
    quick_choice = MagicMock(side_effect=AssertionError("JSON mode must not prompt"))
    device_login = MagicMock()
    monkeypatch.setattr(auth, "quick_choice", quick_choice)
    monkeypatch.setattr(auth, "_do_device_flow_login", device_login)

    auth.login(SERVER_URL, None, None, None, True, False, output=auth.OutputMode.json)

    device_login.assert_called_once_with(
        SERVER_URL,
        direct_sso=True,
        provider=None,
        output=auth.OutputMode.json,
        run_setup=False,
    )


def test_json_provider_implies_sso(monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_login(monkeypatch, public={"sso_enabled": True, "google_sso_enabled": True})
    device_login = MagicMock()
    monkeypatch.setattr(auth, "_do_device_flow_login", device_login)

    auth.login(SERVER_URL, None, None, None, False, False, output=auth.OutputMode.json, provider="google")

    device_login.assert_called_once_with(
        SERVER_URL,
        direct_sso=True,
        provider="google",
        output=auth.OutputMode.json,
        run_setup=False,
    )


@pytest.mark.parametrize(
    ("provider", "message"),
    [
        ("google", "Google is not configured"),
        ("okta", "Unknown SSO provider: okta"),
    ],
)
def test_login_rejects_unusable_explicit_provider(
    provider: str,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_login(monkeypatch, public={"sso_enabled": True})
    device_login = MagicMock()
    monkeypatch.setattr(auth, "_do_device_flow_login", device_login)

    with pytest.raises(CliError) as exc_info:
        auth.login(SERVER_URL, None, None, None, True, False, provider=provider)

    assert exc_info.value.category is ErrorCategory.VALIDATION
    assert message in exc_info.value.message
    device_login.assert_not_called()


def test_quick_choice_restores_terminal_before_printing_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    import termios
    import tty

    import rich

    from observal_cli import prompts

    events: list[object] = []
    stdin = SimpleNamespace(
        isatty=lambda: True,
        fileno=lambda: 7,
        read=MagicMock(return_value="1"),
    )
    monkeypatch.setattr(prompts.sys, "stdin", stdin)
    monkeypatch.setattr(termios, "tcgetattr", lambda _fd: ["saved"])
    monkeypatch.setattr(tty, "setraw", lambda fd: events.append(("raw", fd)))
    monkeypatch.setattr(
        termios,
        "tcsetattr",
        lambda fd, when, settings: events.append(("restore", fd, when, settings)),
    )
    monkeypatch.setattr(rich, "print", lambda *values, **kwargs: events.append(("print", values, kwargs)))

    assert prompts.quick_choice("Login method", ["1", "2"]) == "1"

    assert events[0] == ("print", ("  Login method: ",), {"end": "", "flush": True})
    assert events[1] == ("raw", 7)
    assert events[2][0] == "restore"
    assert events[3] == ("print", ("1",), {})


def test_quick_choice_windows_uses_native_keypress(monkeypatch: pytest.MonkeyPatch) -> None:
    import rich

    from observal_cli import prompts

    keys = iter(["x", "2"])
    msvcrt = ModuleType("msvcrt")
    msvcrt.getwch = lambda: next(keys)
    monkeypatch.setitem(sys.modules, "msvcrt", msvcrt)
    monkeypatch.setattr(prompts.sys, "platform", "win32")
    monkeypatch.setattr(prompts.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    printed: list[str] = []
    monkeypatch.setattr(rich, "print", lambda value, **_kwargs: printed.append(value))

    assert prompts.quick_choice("Login method", ["1", "2"]) == "2"
    assert printed == ["  Login method: ", "2"]


def test_login_password_menu_prompts_for_identifier_and_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_login(monkeypatch)
    monkeypatch.setattr(auth, "quick_choice", lambda _prompt, _valid: "1")
    monkeypatch.setattr(auth, "text_input", lambda _prompt: "ada")
    monkeypatch.setattr(auth, "password_input", lambda _prompt: VALID_PASSWORD)
    password_login = MagicMock()
    monkeypatch.setattr(auth, "_do_password_login", password_login)

    auth.login(SERVER_URL, None, None, None, False, False)

    password_login.assert_called_once_with(
        SERVER_URL, "ada", VALID_PASSWORD, output=auth.OutputMode.table, run_setup=True
    )


def test_login_ignores_unavailable_public_config(monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_login(monkeypatch, public_status=503)
    monkeypatch.setattr(auth, "quick_choice", lambda _prompt, _valid: "1")
    monkeypatch.setattr(auth, "text_input", lambda _prompt: "ada")
    monkeypatch.setattr(auth, "password_input", lambda _prompt: VALID_PASSWORD)
    password_login = MagicMock()
    monkeypatch.setattr(auth, "_do_password_login", password_login)

    auth.login(SERVER_URL, None, None, None, False, False)

    password_login.assert_called_once_with(
        SERVER_URL, "ada", VALID_PASSWORD, output=auth.OutputMode.table, run_setup=True
    )


def test_login_rejects_unavailable_explicit_saml(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_login(monkeypatch, public={"saml_enabled": False})
    password_login = MagicMock()
    monkeypatch.setattr(auth, "_do_password_login", password_login)

    with pytest.raises(CliError) as exc_info:
        auth.login(SERVER_URL, "ada", VALID_PASSWORD, None, False, True)

    assert exc_info.value.category is ErrorCategory.VALIDATION
    password_login.assert_not_called()


def test_login_sso_only_rejects_unavailable_explicit_saml(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_login(monkeypatch, public={"sso_only": True, "saml_enabled": False})
    choice = MagicMock(return_value="1")
    device_login = MagicMock()
    monkeypatch.setattr(auth, "quick_choice", choice)
    monkeypatch.setattr(auth, "_do_device_flow_login", device_login)

    with pytest.raises(CliError) as exc_info:
        auth.login(SERVER_URL, None, None, None, False, True)

    assert exc_info.value.category is ErrorCategory.VALIDATION
    choice.assert_not_called()
    device_login.assert_not_called()


def test_logout_revokes_remote_session_then_removes_every_local_token(
    monkeypatch: pytest.MonkeyPatch,
    printed: list[str],
) -> None:
    auth.config.CONFIG_FILE.write_text(
        json.dumps(
            {
                "server_url": f"{SERVER_URL}/",
                "access_token": ACCESS_TOKEN,
                "refresh_token": REFRESH_TOKEN,
                "api_key": "legacy-secret",
                "output": "json",
            }
        )
    )
    post = MagicMock(return_value=_response())
    monkeypatch.setattr(auth.httpx, "post", post)

    auth.logout()

    post.assert_called_once_with(
        f"{SERVER_URL}/api/v1/auth/logout",
        json={"refresh_token": REFRESH_TOKEN},
        headers={"Authorization": f"Bearer {ACCESS_TOKEN}"},
        timeout=5,
    )
    assert json.loads(auth.config.CONFIG_FILE.read_text()) == {"server_url": f"{SERVER_URL}/"}
    output = "\n".join(printed)
    assert "Logged out" in output
    assert ACCESS_TOKEN not in output
    assert REFRESH_TOKEN not in output
    assert "legacy-secret" not in output


def test_logout_cleans_local_tokens_when_revocation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth.config.CONFIG_FILE.write_text(
        json.dumps({"server_url": SERVER_URL, "access_token": ACCESS_TOKEN, "refresh_token": REFRESH_TOKEN})
    )
    monkeypatch.setattr(auth.httpx, "post", MagicMock(side_effect=httpx.ConnectError("offline")))

    auth.logout()

    assert json.loads(auth.config.CONFIG_FILE.read_text()) == {"server_url": SERVER_URL}


def test_logout_without_config_does_not_contact_server(
    monkeypatch: pytest.MonkeyPatch,
    printed: list[str],
) -> None:
    post = MagicMock()
    monkeypatch.setattr(auth.httpx, "post", post)

    auth.logout()

    post.assert_not_called()
    assert "No config to clear" in "\n".join(printed)


def test_whoami_renders_profile_panel(monkeypatch: pytest.MonkeyPatch) -> None:
    user = _user()
    get = MagicMock(return_value=user)
    panel = object()
    kv_panel = MagicMock(return_value=panel)
    console_print = MagicMock()
    monkeypatch.setattr(auth.client, "get", get)
    monkeypatch.setattr(auth.config, "get_or_exit", MagicMock(return_value={"access_token": "token"}))
    monkeypatch.setattr(auth, "status_badge", lambda role: f"badge:{role}")
    monkeypatch.setattr(auth, "kv_panel", kv_panel)
    monkeypatch.setattr(auth.console, "print", console_print)

    auth.whoami("table")

    get.assert_called_once_with("/api/v1/auth/whoami")
    kv_panel.assert_called_once_with(
        "Ada Lovelace",
        [
            ("Username", "@ada"),
            ("Email", "ada@example.test"),
            ("Role", "badge:admin"),
            ("ID", "[dim]user-123[/dim]"),
        ],
    )
    console_print.assert_called_once_with(panel)


def test_whoami_json_delegates_to_safe_json_renderer(monkeypatch: pytest.MonkeyPatch) -> None:
    user = _user(username="")
    output_json = MagicMock()
    spinner = MagicMock()
    monkeypatch.setattr(auth.client, "get", lambda _path: user)
    monkeypatch.setattr(auth.config, "get_or_exit", MagicMock(return_value={"access_token": "token"}))
    monkeypatch.setattr(auth, "output_json", output_json)
    monkeypatch.setattr(auth, "spinner", spinner)

    auth.whoami("json")

    output_json.assert_called_once_with(user)
    spinner.assert_not_called()


@pytest.mark.parametrize(
    ("ok", "latency", "expected"),
    [
        (True, 42.0, "[green]ok"),
        (True, 200.0, "[yellow]ok"),
        (True, 500.0, "[yellow]ok"),
        (True, 1000.0, "[red]ok"),
        (True, 1500.0, "[red]ok"),
    ],
)
def test_status_reports_health_and_auth_state(
    ok: bool,
    latency: float,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
    printed: list[str],
) -> None:
    telemetry_buffer = ModuleType("observal_cli.telemetry_buffer")
    telemetry_buffer.stats = lambda: {"total": 0}  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "observal_cli.telemetry_buffer", telemetry_buffer)
    monkeypatch.setattr(auth.config, "load", lambda: {"server_url": SERVER_URL, "access_token": ACCESS_TOKEN})
    monkeypatch.setattr(auth.client, "health", lambda: (ok, latency))

    auth.status()

    output = "\n".join(printed)
    assert SERVER_URL in output
    assert "configured" in output
    assert expected in output


def test_status_returns_unavailable_when_server_is_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth.config, "load", lambda: {"server_url": SERVER_URL, "access_token": ACCESS_TOKEN})
    monkeypatch.setattr(auth.client, "health", lambda: (False, 0.0))

    with pytest.raises(CliError) as exc_info:
        auth.status()

    assert exc_info.value.category is ErrorCategory.UNAVAILABLE
    assert exc_info.value.exit_code == ExitCode.UNAVAILABLE


def test_status_returns_auth_when_credentials_are_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth.config, "load", lambda: {"server_url": SERVER_URL})

    with pytest.raises(CliError) as exc_info:
        auth.status()

    assert exc_info.value.category is ErrorCategory.AUTH
    assert exc_info.value.exit_code == ExitCode.AUTH


def test_status_reports_pending_outbox(
    monkeypatch: pytest.MonkeyPatch,
    printed: list[str],
) -> None:
    telemetry_buffer = ModuleType("observal_cli.telemetry_buffer")
    telemetry_buffer.stats = lambda: {  # type: ignore[attr-defined]
        "total": 3,
        "pending": 2,
        "bytes": 2048,
        "oldest_pending": "2026-01-02 03:04:05",
    }
    monkeypatch.setitem(sys.modules, "observal_cli.telemetry_buffer", telemetry_buffer)
    monkeypatch.setattr(auth.config, "load", lambda: {"server_url": SERVER_URL, "access_token": ACCESS_TOKEN})
    monkeypatch.setattr(auth.client, "health", lambda: (True, 42.0))

    auth.status()

    output = "\n".join(printed)
    assert "2 pending" in output
    assert "2.0 KiB" in output
    assert "2026-01-02 03:04:05 UTC" in output


def test_status_reports_broken_outbox_stats(
    monkeypatch: pytest.MonkeyPatch,
    printed: list[str],
) -> None:
    telemetry_buffer = ModuleType("observal_cli.telemetry_buffer")

    def broken_stats() -> dict[str, object]:
        raise RuntimeError("corrupt outbox")

    telemetry_buffer.stats = broken_stats  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "observal_cli.telemetry_buffer", telemetry_buffer)
    monkeypatch.setattr(auth.config, "load", lambda: {"server_url": SERVER_URL, "access_token": ACCESS_TOKEN})
    monkeypatch.setattr(auth.client, "health", lambda: (True, 42.0))

    auth.status()

    assert "status unavailable" in "\n".join(printed)


def test_change_password_requires_saved_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password_input = MagicMock()
    monkeypatch.setattr(auth.config, "load", lambda: {})
    monkeypatch.setattr(auth, "password_input", password_input)

    with pytest.raises(CliError) as exc_info:
        auth.change_password()

    password_input.assert_not_called()
    assert exc_info.value.category is ErrorCategory.AUTH
    assert "authenticated session" in exc_info.value.message


def test_change_password_sends_current_and_validated_password_with_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
    printed: list[str],
) -> None:
    current_password = "CurrentPassword1!"
    put = MagicMock(return_value={"message": "Password changed"})
    monkeypatch.setattr(auth.config, "load", lambda: {"server_url": SERVER_URL, "access_token": ACCESS_TOKEN})
    monkeypatch.setattr(auth, "password_input", MagicMock(side_effect=[current_password, VALID_PASSWORD]))
    monkeypatch.setattr(auth, "_prompt_password", lambda _prompt: VALID_PASSWORD)
    monkeypatch.setattr(auth.client, "put", put)

    auth.change_password()

    put.assert_called_once_with(
        "/api/v1/auth/profile/password",
        {"current_password": current_password, "new_password": VALID_PASSWORD},
    )
    output = "\n".join(printed)
    assert "Password changed successfully" in output
    assert current_password not in output
    assert VALID_PASSWORD not in output
    assert ACCESS_TOKEN not in output


def test_change_password_keeps_this_cli_signed_in_without_printing_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """The server signs out every session, so the CLI must swap its one-time code for the fresh pair."""
    auth.config.save(
        {"server_url": SERVER_URL, "access_token": ACCESS_TOKEN, "refresh_token": "old", "api_key": "hooks"}
    )
    secrets = {"OBSERVAL_CURRENT_PASSWORD": "CurrentPassword1!", "OBSERVAL_NEW_PASSWORD": VALID_PASSWORD}
    monkeypatch.setattr(auth, "_secret", lambda name, **_kwargs: secrets[name])
    monkeypatch.setattr(
        auth.client,
        "put",
        MagicMock(return_value={"message": "Password changed", "exchange_code": "one-time-code"}),
    )
    exchange = MagicMock(return_value={"access_token": "fresh-access", "refresh_token": "fresh-refresh"})
    monkeypatch.setattr(auth.client, "post", exchange)
    emitted: list[dict] = []
    monkeypatch.setattr(auth, "output_json", emitted.append)

    auth.change_password(output=auth.OutputMode.json)

    saved = json.loads(auth.config.CONFIG_FILE.read_text())
    assert (saved["access_token"], saved["refresh_token"]) == ("fresh-access", "fresh-refresh")
    assert "api_key" not in saved  # the revoked hooks token would make every hook upload fail
    assert exchange.call_args.args == ("/api/v1/auth/exchange", {"code": "one-time-code"})
    assert emitted == [{"message": "Password changed"}]  # neither the code nor tokens are printed


def test_change_password_rejects_mismatched_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    put = MagicMock()
    monkeypatch.setattr(auth.config, "load", lambda: {"server_url": SERVER_URL, "access_token": ACCESS_TOKEN})
    monkeypatch.setattr(auth, "password_input", MagicMock(side_effect=["CurrentPassword1!", "DifferentPassword2!"]))
    monkeypatch.setattr(auth, "_prompt_password", lambda _prompt: VALID_PASSWORD)
    monkeypatch.setattr(auth.httpx, "put", put)

    with pytest.raises(typer.Exit):
        auth.change_password()

    put.assert_not_called()


@pytest.mark.parametrize(
    ("category", "message"),
    [
        (ErrorCategory.VALIDATION, "Current password is incorrect"),
        (ErrorCategory.UNAVAILABLE, "The server returned HTTP 500"),
    ],
)
def test_change_password_preserves_client_errors_without_secrets(
    category: ErrorCategory,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = CliError(category, message, operation="Change password", resource="user account")
    monkeypatch.setattr(auth.config, "load", lambda: {"server_url": SERVER_URL, "access_token": ACCESS_TOKEN})
    monkeypatch.setattr(auth, "password_input", MagicMock(side_effect=["CurrentPassword1!", VALID_PASSWORD]))
    monkeypatch.setattr(auth, "_prompt_password", lambda _prompt: VALID_PASSWORD)
    monkeypatch.setattr(auth.client, "put", MagicMock(side_effect=error))

    with pytest.raises(CliError) as exc_info:
        auth.change_password()

    assert exc_info.value is error
    assert ACCESS_TOKEN not in exc_info.value.message
    assert VALID_PASSWORD not in exc_info.value.message


def test_set_username_validates_before_request(
    monkeypatch: pytest.MonkeyPatch,
    printed: list[str],
) -> None:
    put = MagicMock()
    monkeypatch.setattr(auth.client, "put", put)

    with pytest.raises(CliError) as exc_info:
        auth.set_username("Invalid Namespace")

    put.assert_not_called()
    assert exc_info.value.category is ErrorCategory.VALIDATION
    assert auth.NAMESPACE_RULE_TEXT in "\n".join(printed)


def test_set_username_updates_profile(monkeypatch: pytest.MonkeyPatch, printed: list[str]) -> None:
    put = MagicMock(return_value={"username": "ada.dev"})
    save = MagicMock()
    monkeypatch.setattr(auth.client, "put", put)
    monkeypatch.setattr(auth.config, "save", save)

    auth.set_username("ada.dev")

    put.assert_called_once_with("/api/v1/auth/profile/username", {"username": "ada.dev"})
    save.assert_called_once_with({"username": "ada.dev"})
    assert "@ada.dev" in "\n".join(printed)


def test_set_username_preserves_client_error(monkeypatch: pytest.MonkeyPatch) -> None:
    error = CliError(
        ErrorCategory.CONFLICT,
        "Username already taken.",
        operation="Update username",
        resource="user account",
        request_id="request-123",
    )
    monkeypatch.setattr(auth.client, "put", MagicMock(side_effect=error))

    with pytest.raises(CliError) as exc_info:
        auth.set_username("ada.dev")

    assert exc_info.value is error
    assert exc_info.value.request_id == "request-123"


@pytest.mark.parametrize(("package_result", "expected"), [("1.2.3", "1.2.3"), (RuntimeError("missing"), "dev")])
def test_version_callback_has_installed_and_development_fallbacks(
    package_result: str | Exception,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
    printed: list[str],
) -> None:
    import importlib.metadata

    version = MagicMock(
        side_effect=package_result if isinstance(package_result, Exception) else None,
        return_value=package_result if isinstance(package_result, str) else None,
    )
    monkeypatch.setattr(importlib.metadata, "version", version)

    auth.version_callback()

    assert expected in "\n".join(printed)


def test_fetch_endpoints_returns_discovery_document(monkeypatch: pytest.MonkeyPatch) -> None:
    get = MagicMock(return_value=_response(200, {"api": SERVER_URL, "web": "https://app.example.test"}))
    monkeypatch.setattr(auth.httpx, "get", get)

    assert auth._fetch_endpoints(f"{SERVER_URL}/") == {
        "api": SERVER_URL,
        "web": "https://app.example.test",
    }
    get.assert_called_once_with(f"{SERVER_URL}/api/v1/config/endpoints", timeout=5)


def test_fetch_endpoints_fails_closed_for_404(monkeypatch: pytest.MonkeyPatch) -> None:
    get = MagicMock(return_value=_response(404, {}))
    monkeypatch.setattr(auth.httpx, "get", get)

    assert auth._fetch_endpoints(SERVER_URL) == {}


def test_fetch_endpoints_fails_closed_when_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    get = MagicMock(side_effect=RuntimeError("offline"))
    monkeypatch.setattr(auth.httpx, "get", get)

    assert auth._fetch_endpoints(SERVER_URL) == {}


def test_password_login_saves_tokens_and_profile(monkeypatch: pytest.MonkeyPatch, printed: list[str]) -> None:
    response = MagicMock()
    response.json.return_value = _login_payload()
    post = MagicMock(return_value=response)
    save = MagicMock()
    fetch_endpoints = MagicMock(return_value={"web": "https://app.example.test"})
    setup = MagicMock()
    monkeypatch.setattr(auth.httpx, "post", post)
    monkeypatch.setattr(auth, "_fetch_endpoints", fetch_endpoints)
    monkeypatch.setattr(auth.config, "save", save)
    monkeypatch.setattr(auth, "_post_login_setup", setup)

    auth._do_password_login(SERVER_URL, "ada", VALID_PASSWORD)

    post.assert_called_once_with(
        f"{SERVER_URL}/api/v1/auth/login",
        json={"email": "ada", "password": VALID_PASSWORD},
        timeout=30,
    )
    response.raise_for_status.assert_called_once_with()
    fetch_endpoints.assert_called_once_with(SERVER_URL)
    save.assert_called_once_with(
        {
            "server_url": SERVER_URL,
            "access_token": ACCESS_TOKEN,
            "refresh_token": REFRESH_TOKEN,
            "user_id": "user-123",
            "user_name": "Ada Lovelace",
            "username": "ada",
            "web_url": "https://app.example.test",
        }
    )
    setup.assert_called_once_with()
    output = "\n".join(printed)
    assert "Logged in as Ada Lovelace" in output
    assert VALID_PASSWORD not in output
    assert ACCESS_TOKEN not in output


def test_password_login_completes_mandatory_password_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    login_response = _response(200, _login_payload(must_change_password=True))
    changed_response = _response(200, {})
    post = MagicMock(return_value=login_response)
    put = MagicMock(return_value=changed_response)
    save = MagicMock()
    monkeypatch.setattr(auth.httpx, "post", post)
    monkeypatch.setattr(auth.httpx, "put", put)
    monkeypatch.setattr(auth, "password_input", MagicMock(side_effect=[VALID_PASSWORD, VALID_PASSWORD]))
    monkeypatch.setattr(auth, "_fetch_endpoints", lambda _url: {})
    monkeypatch.setattr(auth.config, "save", save)
    monkeypatch.setattr(auth, "_post_login_setup", MagicMock())

    auth._do_password_login(SERVER_URL, "ada", "Temporary1!")

    put.assert_called_once_with(
        f"{SERVER_URL}/api/v1/auth/profile/password",
        json={"current_password": "Temporary1!", "new_password": VALID_PASSWORD},
        headers={"Authorization": f"Bearer {ACCESS_TOKEN}"},
        timeout=30,
    )
    assert "web_url" not in save.call_args.args[0]


def test_password_login_rejects_mismatched_mandatory_password_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        auth.httpx, "post", lambda *_args, **_kwargs: _response(200, _login_payload(must_change_password=True))
    )
    put = MagicMock()
    save = MagicMock()
    monkeypatch.setattr(auth.httpx, "put", put)
    monkeypatch.setattr(auth, "password_input", MagicMock(side_effect=[VALID_PASSWORD, "DifferentPassword2!"]))
    monkeypatch.setattr(auth.config, "save", save)

    with pytest.raises(CliError) as exc_info:
        auth._do_password_login(SERVER_URL, "ada", "Temporary1!")

    put.assert_not_called()
    save.assert_not_called()
    assert exc_info.value.category is ErrorCategory.VALIDATION
    assert "do not match" in exc_info.value.message


def test_password_login_rejects_weak_noninteractive_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        auth.httpx, "post", lambda *_args, **_kwargs: _response(200, _login_payload(must_change_password=True))
    )
    monkeypatch.setenv("OBSERVAL_NEW_PASSWORD", "Short1!")
    put = MagicMock()
    monkeypatch.setattr(auth.httpx, "put", put)

    with pytest.raises(CliError) as exc_info:
        auth._do_password_login(SERVER_URL, "ada", "Temporary1!")

    put.assert_not_called()
    assert exc_info.value.category is ErrorCategory.VALIDATION
    assert "security requirements" in exc_info.value.message


@pytest.mark.parametrize(
    ("response", "category", "message"),
    [
        (_response(401, {"detail": "Invalid credentials"}), ErrorCategory.AUTH, "Authentication failed"),
        (_response(502, text="bad gateway"), ErrorCategory.UNAVAILABLE, "HTTP 502"),
    ],
)
def test_password_login_categorizes_http_errors_without_saving(
    response: httpx.Response,
    category: ErrorCategory,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save = MagicMock()
    monkeypatch.setattr(auth.httpx, "post", lambda *_args, **_kwargs: response)
    monkeypatch.setattr(auth.config, "save", save)

    with pytest.raises(CliError) as exc_info:
        auth._do_password_login(SERVER_URL, "ada", VALID_PASSWORD)

    save.assert_not_called()
    assert exc_info.value.category is category
    assert message in exc_info.value.message
    assert VALID_PASSWORD not in exc_info.value.message


def test_device_flow_rewrites_local_verification_url_and_saves_authorized_session(
    monkeypatch: pytest.MonkeyPatch,
    printed: list[str],
    device_runtime: tuple[_Clock, MagicMock],
) -> None:
    clock, browser_open = device_runtime
    authorize = _response(200, _device_authorization())
    token = _response(200, _login_payload())
    post = MagicMock(side_effect=[authorize, token])
    save = MagicMock()
    setup = MagicMock()
    monkeypatch.setattr(auth.httpx, "post", post)
    monkeypatch.setattr(auth, "_fetch_endpoints", lambda _url: {"web": "https://app.example.test"})
    monkeypatch.setattr(auth.config, "save", save)
    monkeypatch.setattr(auth, "_post_login_setup", setup)

    auth._do_device_flow_login(SERVER_URL, direct_sso=True, provider="oidc")

    assert post.call_args_list == [
        call(
            f"{SERVER_URL}/api/v1/auth/device/authorize",
            json={"sso": True, "provider": "oidc"},
            timeout=10,
        ),
        call(
            f"{SERVER_URL}/api/v1/auth/device/token",
            json={
                "device_code": "private-device-code",
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            timeout=10,
        ),
    ]
    browser_open.assert_called_once_with(f"{SERVER_URL}/device?code=ABCD-EFGH")
    assert clock.sleeps == [1]
    save.assert_called_once_with(
        {
            "server_url": SERVER_URL,
            "access_token": ACCESS_TOKEN,
            "refresh_token": REFRESH_TOKEN,
            "user_id": "user-123",
            "user_name": "Ada Lovelace",
            "username": "ada",
            "web_url": "https://app.example.test",
        }
    )
    setup.assert_called_once_with()
    output = "\n".join(printed)
    assert "ABCD-EFGH" in output
    assert "private-device-code" not in output
    assert ACCESS_TOKEN not in output
    assert REFRESH_TOKEN not in output


def test_device_flow_json_emits_events_and_skips_setup(
    monkeypatch: pytest.MonkeyPatch,
    device_runtime: tuple[_Clock, MagicMock],
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        auth.httpx,
        "post",
        MagicMock(
            side_effect=[
                _response(200, _device_authorization()),
                _response(200, _login_payload()),
            ]
        ),
    )
    monkeypatch.setattr(auth, "_fetch_endpoints", lambda _url: {})
    monkeypatch.setattr(auth.config, "save", MagicMock())
    setup = MagicMock()
    monkeypatch.setattr(auth, "_post_login_setup", setup)

    auth._do_device_flow_login(SERVER_URL, direct_sso=True, provider="oidc", output="json", run_setup=False)

    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[0]["event"] == "authorization_required"
    assert events[0]["verification_uri"] == f"{SERVER_URL}/device"
    assert events[1]["event"] == "authenticated"
    assert events[1]["user"]["email"] == "ada@example.test"
    assert "private-device-code" not in str(events)
    assert ACCESS_TOKEN not in str(events)
    assert REFRESH_TOKEN not in str(events)
    setup.assert_not_called()


def test_device_flow_keeps_local_url_for_local_server(
    monkeypatch: pytest.MonkeyPatch,
    device_runtime: tuple[_Clock, MagicMock],
) -> None:
    _, browser_open = device_runtime
    local_server = "http://localhost:8080"
    post = MagicMock(
        side_effect=[
            _response(200, _device_authorization()),
            _response(400, {"error": "access_denied"}),
        ]
    )
    monkeypatch.setattr(auth.httpx, "post", post)

    with pytest.raises(typer.Exit):
        auth._do_device_flow_login(local_server)

    browser_open.assert_called_once_with("http://localhost/device?code=ABCD-EFGH")


def test_device_flow_categorizes_authorization_request_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth.httpx, "post", lambda *_args, **_kwargs: _response(503, text="unavailable"))

    with pytest.raises(CliError) as exc_info:
        auth._do_device_flow_login(SERVER_URL)

    assert exc_info.value.category is ErrorCategory.UNAVAILABLE
    assert exc_info.value.http_status == 503


@pytest.mark.parametrize(
    ("error", "category", "expected"),
    [
        ("expired_token", ErrorCategory.AUTH, "authorization code expired"),
        ("access_denied", ErrorCategory.PERMISSION, "authorization was denied"),
        ("server_error", ErrorCategory.VALIDATION, "HTTP 400"),
    ],
)
def test_device_flow_stops_on_terminal_poll_error(
    error: str,
    category: ErrorCategory,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
    device_runtime: tuple[_Clock, MagicMock],
) -> None:
    monkeypatch.setattr(
        auth.httpx,
        "post",
        MagicMock(
            side_effect=[
                _response(200, _device_authorization()),
                _response(400, {"error": error}),
            ]
        ),
    )

    with pytest.raises(CliError) as exc_info:
        auth._do_device_flow_login(SERVER_URL)

    assert exc_info.value.category is category
    assert expected in exc_info.value.message


def test_device_flow_polls_pending_until_success(
    monkeypatch: pytest.MonkeyPatch,
    device_runtime: tuple[_Clock, MagicMock],
) -> None:
    save = MagicMock()
    monkeypatch.setattr(
        auth.httpx,
        "post",
        MagicMock(
            side_effect=[
                _response(200, _device_authorization()),
                _response(428, {"error": "authorization_pending"}),
                _response(200, _login_payload()),
            ]
        ),
    )
    monkeypatch.setattr(auth, "_fetch_endpoints", lambda _url: {})
    monkeypatch.setattr(auth.config, "save", save)
    monkeypatch.setattr(auth, "_post_login_setup", MagicMock())

    auth._do_device_flow_login(SERVER_URL)

    assert save.call_count == 1
    assert device_runtime[0].sleeps == [1, 1]


def test_device_flow_retries_network_errors_until_timeout(
    monkeypatch: pytest.MonkeyPatch,
    device_runtime: tuple[_Clock, MagicMock],
) -> None:
    request = httpx.Request("POST", SERVER_URL)
    monkeypatch.setattr(
        auth.httpx,
        "post",
        MagicMock(
            side_effect=[
                _response(200, _device_authorization(expires_in=2)),
                httpx.ConnectError("offline", request=request),
                httpx.ConnectError("offline", request=request),
            ]
        ),
    )

    with pytest.raises(CliError) as exc_info:
        auth._do_device_flow_login(SERVER_URL)

    assert device_runtime[0].sleeps == [1, 1]
    assert exc_info.value.category is ErrorCategory.UNAVAILABLE
    assert "timed out" in exc_info.value.message


@pytest.mark.parametrize(
    ("system_name", "wsl_result", "expected_program"),
    [
        ("Darwin", None, "open"),
        ("Linux", 0, "powershell.exe"),
        ("Linux", 1, "xdg-open"),
    ],
)
def test_device_flow_uses_platform_browser_launcher(
    system_name: str,
    wsl_result: int | None,
    expected_program: str,
    monkeypatch: pytest.MonkeyPatch,
    device_runtime: tuple[_Clock, MagicMock],
) -> None:
    monkeypatch.setattr(platform, "system", lambda: system_name)
    popen = MagicMock()
    monkeypatch.setattr(subprocess, "Popen", popen)
    if wsl_result is not None:
        monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=wsl_result))
    monkeypatch.setattr(
        auth.httpx,
        "post",
        MagicMock(
            side_effect=[
                _response(200, _device_authorization()),
                _response(400, {"error": "expired_token"}),
            ]
        ),
    )

    with pytest.raises(typer.Exit):
        auth._do_device_flow_login(SERVER_URL)

    assert popen.call_args.args[0][0] == expected_program
    launched = " ".join(popen.call_args.args[0])
    assert f"{SERVER_URL}/device?code=ABCD-EFGH" in launched


def test_device_flow_uses_xdg_open_when_wslpath_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    device_runtime: tuple[_Clock, MagicMock],
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(subprocess, "run", MagicMock(side_effect=FileNotFoundError("wslpath")))
    popen = MagicMock()
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(
        auth.httpx,
        "post",
        MagicMock(
            side_effect=[
                _response(200, _device_authorization()),
                _response(400, {"error": "expired_token"}),
            ]
        ),
    )

    with pytest.raises(typer.Exit):
        auth._do_device_flow_login(SERVER_URL)

    assert popen.call_args.args[0][0] == "xdg-open"


def test_device_flow_browser_failure_keeps_manual_flow_available(
    monkeypatch: pytest.MonkeyPatch,
    printed: list[str],
    device_runtime: tuple[_Clock, MagicMock],
) -> None:
    device_runtime[1].side_effect = RuntimeError("no browser")
    monkeypatch.setattr(
        auth.httpx,
        "post",
        MagicMock(
            side_effect=[
                _response(200, _device_authorization()),
                _response(400, {"error": "access_denied"}),
            ]
        ),
    )

    with pytest.raises(typer.Exit):
        auth._do_device_flow_login(SERVER_URL)

    assert "Please open the URL manually" in "\n".join(printed)


def test_config_show_json_never_exposes_token_fragments(
    config_cli: typer.Typer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored = {
        "server_url": SERVER_URL,
        "access_token": ACCESS_TOKEN,
        "refresh_token": REFRESH_TOKEN,
        "api_key": "hooks-secret",
        "timeout": 30,
    }
    monkeypatch.setattr(auth.config, "load", lambda: stored)

    result = CliRunner().invoke(config_cli, ["config", "show", "--output", "json"])

    assert result.exit_code == 0, result.output
    rendered = json.loads(result.output)
    assert rendered == {
        "server_url": SERVER_URL,
        "timeout": 30,
        "access_token_configured": True,
        "refresh_token_configured": True,
        "hooks_token_configured": True,
    }
    assert ACCESS_TOKEN not in result.output
    assert REFRESH_TOKEN not in result.output
    assert "hooks-secret" not in result.output


def test_config_show_defaults_to_table(
    config_cli: typer.Typer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    print_table = MagicMock()
    monkeypatch.setattr(auth.config, "load", lambda: {"server_url": SERVER_URL, "timeout": 30})
    monkeypatch.setattr(auth.console, "print", print_table)

    result = CliRunner().invoke(config_cli, ["config", "show"])

    assert result.exit_code == 0, result.output
    print_table.assert_called_once()
    assert isinstance(print_table.call_args.args[0], auth.Table)


@pytest.mark.parametrize(
    ("key", "value", "saved_value"),
    [
        ("update_check", "YES", True),
        ("update_check", "off", False),
        ("timeout", "60", 60),
        ("update_check_interval", "120", 120),
        (
            "update_check_repo",
            "AgenticResourceRegistry/AgenticResourceRegistry",
            "AgenticResourceRegistry/AgenticResourceRegistry",
        ),
    ],
)
def test_config_set_normalizes_supported_values(
    key: str,
    value: str,
    saved_value: object,
    config_cli: typer.Typer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save = MagicMock()
    monkeypatch.setattr(auth.config, "save", save)
    monkeypatch.setattr(auth.config, "load", lambda: {key: saved_value})

    result = CliRunner().invoke(config_cli, ["config", "set", key, value, "--output", "json"])

    assert result.exit_code == 0, result.output
    save.assert_called_once_with({key: saved_value})
    assert json.loads(result.output) == {
        "key": key,
        "value": saved_value,
        "persisted": True,
        "effective": saved_value,
    }


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("output", "json"),
        ("color", "false"),
        ("access_token", "secret"),
        ("unknown", "value"),
        ("update_check", "maybe"),
        ("timeout", "zero"),
        ("timeout", "0"),
        ("update_check_interval", "59"),
        ("server_url", "registry.example.test"),
        ("server_url", "https://user:password@registry.example.test"),
        ("update_check_repo", "missing-slash"),
    ],
)
def test_config_set_rejects_unsupported_or_invalid_values(
    key: str,
    value: str,
    config_cli: typer.Typer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save = MagicMock()
    monkeypatch.setattr(auth.config, "save", save)

    result = CliRunner().invoke(config_cli, ["config", "set", key, value])

    assert result.exit_code == ExitCode.VALIDATION
    save.assert_not_called()


def test_config_set_server_normalizes_and_migrates_previous_lockfile(
    config_cli: typer.Typer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import observal_cli.lockfile as lockfile

    migrate = MagicMock()
    save = MagicMock()
    monkeypatch.setattr(auth.config, "load_persisted", lambda: {"server_url": "https://old.example.test"})
    monkeypatch.setattr(auth.config, "load", lambda: {"server_url": SERVER_URL})
    monkeypatch.setattr(auth.config, "save", save)
    monkeypatch.setattr(lockfile, "migrate_lockfile_v1", migrate)

    result = CliRunner().invoke(config_cli, ["config", "set", "server_url", f"{SERVER_URL}/"])

    assert result.exit_code == 0, result.output
    migrate.assert_called_once_with("https://old.example.test")
    save.assert_called_once_with({"server_url": SERVER_URL})


def test_config_set_server_categorizes_lockfile_migration_failure(
    config_cli: typer.Typer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import observal_cli.lockfile as lockfile

    save = MagicMock()
    monkeypatch.setattr(auth.config, "load_persisted", lambda: {"server_url": "https://old.example.test"})
    monkeypatch.setattr(lockfile, "migrate_lockfile_v1", MagicMock(side_effect=RuntimeError("bad lockfile")))
    monkeypatch.setattr(auth.config, "save", save)

    result = CliRunner().invoke(config_cli, ["config", "set", "server_url", SERVER_URL])

    assert result.exit_code == ExitCode.VALIDATION
    save.assert_not_called()


def test_config_path_supports_bare_and_json_output(config_cli: typer.Typer) -> None:
    plain = CliRunner().invoke(config_cli, ["config", "path"])
    structured = CliRunner().invoke(config_cli, ["config", "path", "--output", "json"])

    assert plain.exit_code == structured.exit_code == 0
    assert plain.output.strip() == str(auth.config.CONFIG_FILE)
    assert json.loads(structured.output) == {
        "path": str(auth.config.CONFIG_FILE),
        "exists": auth.config.CONFIG_FILE.exists(),
    }


def test_config_alias_sets_and_removes_mapping(
    config_cli: typer.Typer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aliases = {"old": "old-id"}
    save_aliases = MagicMock()
    monkeypatch.setattr(auth.config, "load_aliases", lambda: dict(aliases))
    monkeypatch.setattr(auth.config, "save_aliases", save_aliases)

    set_result = CliRunner().invoke(
        config_cli,
        ["config", "alias", "agent", "alice/agent", "--output", "json"],
    )
    remove_result = CliRunner().invoke(config_cli, ["config", "alias", "old", "--output", "json"])

    assert set_result.exit_code == remove_result.exit_code == 0
    assert save_aliases.call_args_list == [
        call({"old": "old-id", "agent": "alice/agent"}),
        call({}),
    ]
    assert json.loads(set_result.output) == {
        "action": "set",
        "alias": "agent",
        "target": "alice/agent",
        "changed": True,
    }
    assert json.loads(remove_result.output) == {
        "action": "removed",
        "alias": "old",
        "target": "old-id",
        "changed": True,
    }


def test_config_alias_missing_removal_is_idempotent(
    config_cli: typer.Typer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_aliases = MagicMock()
    monkeypatch.setattr(auth.config, "load_aliases", lambda: {})
    monkeypatch.setattr(auth.config, "save_aliases", save_aliases)

    result = CliRunner().invoke(config_cli, ["config", "alias", "missing", "--output", "json"])

    assert result.exit_code == 0, result.output
    save_aliases.assert_not_called()
    assert json.loads(result.output) == {
        "action": "removed",
        "alias": "missing",
        "target": None,
        "changed": False,
    }


@pytest.mark.parametrize("name", ["@alias", "1", "has space", "has/slash", "a" * 65])
def test_config_alias_rejects_invalid_names(
    name: str,
    config_cli: typer.Typer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_aliases = MagicMock()
    monkeypatch.setattr(auth.config, "save_aliases", save_aliases)

    result = CliRunner().invoke(config_cli, ["config", "alias", name, "target"])

    assert result.exit_code == ExitCode.VALIDATION
    save_aliases.assert_not_called()


@pytest.mark.parametrize("aliases", [{}, {"zeta": "2", "alpha": "1"}])
def test_config_aliases_has_stable_json_shape(
    aliases: dict[str, str],
    config_cli: typer.Typer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth.config, "load_aliases", lambda: aliases)

    result = CliRunner().invoke(config_cli, ["config", "aliases", "--output", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "items": [{"alias": name, "target": target} for name, target in sorted(aliases.items())],
        "total": len(aliases),
        "page": 1,
        "page_size": len(aliases),
    }


def test_config_aliases_defaults_to_table(
    config_cli: typer.Typer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    print_table = MagicMock()
    monkeypatch.setattr(auth.config, "load_aliases", lambda: {"alpha": "alice/agent"})
    monkeypatch.setattr(auth.console, "print", print_table)

    result = CliRunner().invoke(config_cli, ["config", "aliases"])

    assert result.exit_code == 0, result.output
    print_table.assert_called_once()
    assert isinstance(print_table.call_args.args[0], auth.Table)


def test_config_storage_is_atomic_private_and_rejects_malformed_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "config.json"
    aliases_file = tmp_path / "aliases.json"
    monkeypatch.setattr(auth.config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(auth.config, "CONFIG_FILE", config_file)
    monkeypatch.setattr(auth.config, "ALIASES_FILE", aliases_file)

    auth.config.save({"server_url": SERVER_URL, "output": "json", "color": True})
    auth.config.save_aliases({"agent": "alice/agent"})

    assert json.loads(config_file.read_text()) == {"server_url": SERVER_URL}
    assert json.loads(aliases_file.read_text()) == {"agent": "alice/agent"}
    assert config_file.stat().st_mode & 0o777 == 0o600
    assert aliases_file.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.glob(".*.json.*")) == []

    aliases_file.write_text("not json")
    with pytest.raises(CliError) as exc_info:
        auth.config.load_aliases()
    assert exc_info.value.category is ErrorCategory.VALIDATION


def test_post_login_setup_installs_skills_snapshots_and_runs_doctor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import observal_cli.cmd_doctor as cmd_doctor

    monkeypatch.setenv("HOME", str(tmp_path))
    setup_order: list[str] = []
    install = MagicMock(side_effect=lambda: setup_order.append("skills"))
    pi_extension = MagicMock(side_effect=lambda: setup_order.append("pi"))
    snapshot = MagicMock(side_effect=lambda: setup_order.append("snapshot"))
    doctor = MagicMock(side_effect=lambda **_kwargs: setup_order.append("doctor"))
    monkeypatch.setattr(auth, "_install_observal_skill", install)
    monkeypatch.setattr(auth, "_install_or_check_pi_extension", pi_extension)
    monkeypatch.setattr(auth, "_generate_initial_layer_snapshot", snapshot)
    monkeypatch.setattr(cmd_doctor, "doctor", doctor)

    auth._post_login_setup()

    install.assert_called_once_with()
    pi_extension.assert_called_once_with()
    snapshot.assert_called_once_with()
    assert doctor.call_args.kwargs["yes"] is False
    assert doctor.call_args.kwargs["ctx"].invoked_subcommand is None
    assert setup_order == ["skills", "pi", "snapshot", "doctor"]


@pytest.mark.parametrize("error", [typer.Exit(1), RuntimeError("doctor unavailable")])
def test_post_login_setup_contains_doctor_failures(
    error: Exception,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    printed: list[str],
) -> None:
    import observal_cli.cmd_doctor as cmd_doctor

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(auth, "_install_observal_skill", MagicMock())
    monkeypatch.setattr(auth, "_install_or_check_pi_extension", MagicMock())
    monkeypatch.setattr(auth, "_generate_initial_layer_snapshot", MagicMock())
    monkeypatch.setattr(cmd_doctor, "doctor", MagicMock(side_effect=error))

    auth._post_login_setup()

    if type(error) is RuntimeError:
        assert "Could not run doctor" in "\n".join(printed)
        assert "manually" in "\n".join(printed)


def test_post_auth_onboarding_scans_detected_harnesses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    printed: list[str],
) -> None:
    import observal_cli.harness as harness

    for directory in (".claude", ".kiro", ".cursor"):
        (tmp_path / directory).mkdir()
    results = {
        "claude-code": SimpleNamespace(agents=[object()], mcps=[]),
        "kiro": SimpleNamespace(agents=[object(), object()], mcps=[object(), object()]),
    }

    def get_adapter(name: str) -> SimpleNamespace:
        if name == "cursor":
            raise KeyError(name)
        return SimpleNamespace(scan_home=MagicMock(return_value=results[name]))

    ensure_loaded = MagicMock()
    monkeypatch.setattr(auth.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(harness, "ensure_loaded", ensure_loaded)
    monkeypatch.setattr(harness, "get_adapter", get_adapter)

    auth._post_auth_onboarding()

    assert ensure_loaded.call_count == 3
    output = "\n".join(printed)
    assert "Detected local harness configs" in output
    assert "1 agent found" in output
    assert "2 agents, 2 MCPs found" in output
    assert "all-harnesses" in output


def test_post_auth_onboarding_is_silent_when_none_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    printed: list[str],
) -> None:
    monkeypatch.setattr(auth.Path, "home", lambda: tmp_path)

    auth._post_auth_onboarding()

    assert printed == []


def test_post_auth_onboarding_contains_detection_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth.Path, "home", MagicMock(side_effect=OSError("home unavailable")))

    auth._post_auth_onboarding()


def test_snapshot_generation_is_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    import observal_cli.layer as layer

    snapshot = MagicMock(side_effect=[None, RuntimeError("scan failed")])
    monkeypatch.setattr(layer, "ensure_local_snapshot", snapshot)

    auth._generate_initial_layer_snapshot()
    auth._generate_initial_layer_snapshot()

    assert snapshot.call_count == 2


def test_install_observal_skill_delegates_to_installer(monkeypatch: pytest.MonkeyPatch) -> None:
    import observal_cli.skill_installer as skill_installer

    install = MagicMock()
    monkeypatch.setattr(skill_installer, "install_observal_skill", install)

    auth._install_observal_skill()

    install.assert_called_once_with()


def test_pi_extension_setup_is_silent_when_pi_not_detected(monkeypatch: pytest.MonkeyPatch, printed: list[str]) -> None:
    import observal_cli.pi_extension as pi_extension

    monkeypatch.setattr(pi_extension, "check_status", lambda: pi_extension.PiExtensionStatus(pi_extension.NOT_DETECTED))
    install_or_refresh = MagicMock(return_value=pi_extension.PiExtensionResult(True))
    monkeypatch.setattr(pi_extension, "install_or_refresh", install_or_refresh)

    auth._install_or_check_pi_extension()

    install_or_refresh.assert_not_called()
    assert printed == []


def test_pi_extension_setup_installs_when_missing(monkeypatch: pytest.MonkeyPatch, printed: list[str]) -> None:
    import observal_cli.pi_extension as pi_extension

    status = pi_extension.PiExtensionStatus(pi_extension.NOT_INSTALLED, "not installed", action="install")
    monkeypatch.setattr(pi_extension, "check_status", lambda: status)
    install_or_refresh = MagicMock(return_value=pi_extension.PiExtensionResult(True))
    monkeypatch.setattr(pi_extension, "install_or_refresh", install_or_refresh)

    auth._install_or_check_pi_extension()

    install_or_refresh.assert_called_once_with(dry_run=False, status=status)
    assert any("Installed the Pi telemetry extension" in message for message in printed)


def test_pi_extension_setup_refreshes_and_prompts_reload_when_stale(
    monkeypatch: pytest.MonkeyPatch, printed: list[str]
) -> None:
    import observal_cli.pi_extension as pi_extension

    status = pi_extension.PiExtensionStatus(pi_extension.STALE, "stale", action="refresh")
    monkeypatch.setattr(pi_extension, "check_status", lambda: status)
    monkeypatch.setattr(
        pi_extension, "install_or_refresh", MagicMock(return_value=pi_extension.PiExtensionResult(True))
    )

    auth._install_or_check_pi_extension()

    output = "\n".join(printed)
    assert "Updated the Pi telemetry extension" in output
    assert "reload" in output.lower()


def test_pi_extension_setup_adopts_silently(monkeypatch: pytest.MonkeyPatch, printed: list[str]) -> None:
    import observal_cli.pi_extension as pi_extension

    status = pi_extension.PiExtensionStatus(pi_extension.CURRENT, action="adopt")
    monkeypatch.setattr(pi_extension, "check_status", lambda: status)
    install_or_refresh = MagicMock(return_value=pi_extension.PiExtensionResult(True))
    monkeypatch.setattr(pi_extension, "install_or_refresh", install_or_refresh)

    auth._install_or_check_pi_extension()

    install_or_refresh.assert_called_once_with(dry_run=False, status=status)
    assert printed == []


def test_pi_extension_setup_migrates_pre_manifest_install(monkeypatch: pytest.MonkeyPatch, printed: list[str]) -> None:
    from pathlib import Path  # module-level import is TYPE_CHECKING-only

    import observal_cli.pi_extension as pi_extension

    status = pi_extension.PiExtensionStatus(pi_extension.MIGRATABLE, "older CLI", action="migrate")
    monkeypatch.setattr(pi_extension, "check_status", lambda: status)
    backup = Path("/home/u/.pi/agent/extensions/observal.ts.bak")
    install_or_refresh = MagicMock(return_value=pi_extension.PiExtensionResult(True, "migrate", backup))
    monkeypatch.setattr(pi_extension, "install_or_refresh", install_or_refresh)

    auth._install_or_check_pi_extension()

    install_or_refresh.assert_called_once_with(dry_run=False, status=status)
    output = "\n".join(printed)
    assert "Migrated the Pi telemetry extension" in output
    assert "observal.ts.bak" in output
    assert "reload" in output.lower()


def test_pi_extension_setup_never_blocks_login_on_failure(monkeypatch: pytest.MonkeyPatch, printed: list[str]) -> None:
    import observal_cli.pi_extension as pi_extension

    monkeypatch.setattr(pi_extension, "check_status", MagicMock(side_effect=OSError("settings.json unreadable")))

    auth._install_or_check_pi_extension()  # must not raise

    assert any("Could not check the Pi telemetry extension" in message for message in printed)


def test_run_doctor_patch_uses_isolated_subprocess_environment(
    monkeypatch: pytest.MonkeyPatch,
    printed: list[str],
) -> None:
    completed = SimpleNamespace(returncode=1, stdout="doctor output\n", stderr="doctor warning\n")
    run = MagicMock(return_value=completed)
    monkeypatch.setattr(subprocess, "run", run)
    from observal_cli.shared import launcher

    monkeypatch.setattr(launcher, "importable_in_isolation", lambda: True)

    auth._run_doctor_patch("cursor")

    command = run.call_args.args[0]
    # -I keeps the working directory and any inherited PYTHONPATH off sys.path.
    assert command[:6] == [sys.executable, "-I", "-X", "utf8", "-m", "observal_cli.main"]
    assert command[-1] == "cursor"
    assert run.call_args.kwargs["capture_output"] is True
    assert run.call_args.kwargs["timeout"] == 30
    assert run.call_args.kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
    assert printed == ["doctor output", "[yellow]doctor warning[/yellow]"]


def test_run_doctor_patch_reports_subprocess_failure(
    monkeypatch: pytest.MonkeyPatch,
    printed: list[str],
) -> None:
    monkeypatch.setattr(subprocess, "run", MagicMock(side_effect=OSError("cannot execute")))

    auth._run_doctor_patch("cursor")

    output = "\n".join(printed)
    assert "cannot execute" in output
    assert "manually" in output


@pytest.mark.parametrize(
    ("function_name", "directory", "harness_name"),
    [
        ("_configure_cursor", ".cursor", "cursor"),
        ("_configure_kiro", ".kiro", "kiro"),
        ("_configure_codex", ".codex", "codex"),
    ],
)
def test_basic_harness_configurators_patch_detected_installation(
    function_name: str,
    directory: str,
    harness_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / directory).mkdir()
    patch_doctor = MagicMock()
    monkeypatch.setattr(auth.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(auth.shutil, "which", lambda _name: None)
    monkeypatch.setattr(auth.typer, "confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(auth, "_run_doctor_patch", patch_doctor)

    getattr(auth, function_name)(SERVER_URL)

    patch_doctor.assert_called_once_with(harness_name)


@pytest.mark.parametrize(
    "function_name",
    [
        "_configure_cursor",
        "_configure_kiro",
        "_configure_codex",
        "_configure_copilot",
        "_configure_copilot_cli",
        "_configure_opencode",
        "_configure_claude_code",
    ],
)
def test_harness_configurators_respect_decline(
    function_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_harness(function_name, tmp_path)

    patch_doctor = MagicMock()
    monkeypatch.setattr(auth.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        auth.shutil,
        "which",
        lambda name: "/bin/copilot" if function_name == "_configure_copilot_cli" and name == "copilot" else None,
    )
    monkeypatch.setattr(auth.typer, "confirm", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(auth, "_run_doctor_patch", patch_doctor)

    function = getattr(auth, function_name)
    if function_name == "_configure_claude_code":
        function(SERVER_URL, ACCESS_TOKEN)
    else:
        function(SERVER_URL)

    patch_doctor.assert_not_called()


@pytest.mark.parametrize(
    "function_name",
    [
        "_configure_cursor",
        "_configure_kiro",
        "_configure_codex",
        "_configure_copilot",
        "_configure_copilot_cli",
        "_configure_opencode",
        "_configure_claude_code",
    ],
)
def test_harness_configurators_skip_missing_installation(
    function_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    confirm = MagicMock()
    monkeypatch.setattr(auth.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(auth.shutil, "which", lambda _name: None)
    monkeypatch.setattr(auth.typer, "confirm", confirm)

    function = getattr(auth, function_name)
    if function_name == "_configure_claude_code":
        function(SERVER_URL, ACCESS_TOKEN)
    else:
        function(SERVER_URL)

    confirm.assert_not_called()


@pytest.mark.parametrize("function_name", ["_configure_cursor", "_configure_kiro", "_configure_codex"])
def test_basic_harness_configurators_report_detection_errors(
    function_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    printed: list[str],
) -> None:
    directory = {"_configure_cursor": ".cursor", "_configure_kiro": ".kiro", "_configure_codex": ".codex"}[
        function_name
    ]
    (tmp_path / directory).mkdir()
    monkeypatch.setattr(auth.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(auth.typer, "confirm", MagicMock(side_effect=RuntimeError("prompt failed")))

    getattr(auth, function_name)(SERVER_URL)

    output = "\n".join(printed)
    assert "prompt failed" in output
    assert "manually" in output


def test_configure_copilot_requires_actual_extension(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extensions = tmp_path / ".vscode" / "extensions"
    extensions.mkdir(parents=True)
    (extensions / "github.copilot-1.0.0").mkdir()
    patch_doctor = MagicMock()
    monkeypatch.setattr(auth.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(auth.typer, "confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(auth, "_run_doctor_patch", patch_doctor)

    auth._configure_copilot(SERVER_URL)

    patch_doctor.assert_called_once_with("copilot")


def test_configure_copilot_skips_vscode_without_extension(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".vscode" / "extensions").mkdir(parents=True)
    confirm = MagicMock()
    monkeypatch.setattr(auth.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(auth.typer, "confirm", confirm)

    auth._configure_copilot(SERVER_URL)

    confirm.assert_not_called()


def test_configure_copilot_cli_uses_binary_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_doctor = MagicMock()
    monkeypatch.setattr(auth.shutil, "which", lambda name: "/bin/copilot" if name == "copilot" else None)
    monkeypatch.setattr(auth.typer, "confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(auth, "_run_doctor_patch", patch_doctor)

    auth._configure_copilot_cli(SERVER_URL)

    patch_doctor.assert_called_once_with("copilot-cli")


def test_configure_opencode_detects_off_path_binary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = tmp_path / ".opencode" / "bin" / "opencode"
    binary.parent.mkdir(parents=True)
    binary.write_text("")
    patch_doctor = MagicMock()
    monkeypatch.setattr(auth.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(auth.shutil, "which", lambda _name: None)
    monkeypatch.setattr(auth.typer, "confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(auth, "_run_doctor_patch", patch_doctor)

    auth._configure_opencode(SERVER_URL)

    patch_doctor.assert_called_once_with("opencode")


def test_configure_claude_code_stores_hooks_token_before_patching(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".claude").mkdir()
    save = MagicMock()
    patch_doctor = MagicMock()
    monkeypatch.setattr(auth.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(auth.shutil, "which", lambda _name: None)
    monkeypatch.setattr(auth.typer, "confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(auth, "_fetch_hooks_token", lambda _url, _token: "hooks-token")
    monkeypatch.setattr(auth.config, "load", lambda: {"server_url": SERVER_URL})
    monkeypatch.setattr(auth.config, "save", save)
    monkeypatch.setattr(auth, "_run_doctor_patch", patch_doctor)

    auth._configure_claude_code(SERVER_URL, ACCESS_TOKEN)

    save.assert_called_once_with({"server_url": SERVER_URL, "api_key": "hooks-token"})
    patch_doctor.assert_called_once_with("claude-code")


@pytest.mark.parametrize("function_name", ["_configure_copilot", "_configure_copilot_cli", "_configure_opencode"])
def test_silent_harness_configurators_contain_prompt_errors(
    function_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_harness(function_name, tmp_path)
    monkeypatch.setattr(auth.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        auth.shutil,
        "which",
        lambda name: "/bin/copilot" if function_name == "_configure_copilot_cli" and name == "copilot" else None,
    )
    monkeypatch.setattr(auth.typer, "confirm", MagicMock(side_effect=RuntimeError("prompt failed")))

    getattr(auth, function_name)(SERVER_URL)


def test_configure_claude_code_reports_prompt_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    printed: list[str],
) -> None:
    (tmp_path / ".claude").mkdir()
    monkeypatch.setattr(auth.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(auth.typer, "confirm", MagicMock(side_effect=RuntimeError("prompt failed")))

    auth._configure_claude_code(SERVER_URL, ACCESS_TOKEN)

    output = "\n".join(printed)
    assert "prompt failed" in output
    assert "manually" in output


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (_response(200, {"access_token": "hooks-token"}), "hooks-token"),
        (_response(200, {}), ACCESS_TOKEN),
        (_response(503, {}), ACCESS_TOKEN),
        (httpx.ConnectError("offline"), ACCESS_TOKEN),
    ],
)
def test_fetch_hooks_token_uses_authenticated_endpoint_with_safe_fallback(
    result: object,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post = _responder(result)
    monkeypatch.setattr(auth.httpx, "post", post)

    assert auth._fetch_hooks_token(f"{SERVER_URL}/", ACCESS_TOKEN) == expected
    post.assert_called_once_with(
        f"{SERVER_URL}/api/v1/auth/hooks-token",
        headers={"Authorization": f"Bearer {ACCESS_TOKEN}"},
        timeout=10,
    )
