# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the CLI HTTP client and its user-facing failures."""

from __future__ import annotations

import ast
import importlib.metadata
import json
import os
import sys
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import httpx
import pytest
import typer
from click import Group
from typer.testing import CliRunner

from observal_cli import client
from observal_cli.error_context import OPERATION_LABELS, RESOURCE_LABELS
from observal_cli.errors import (
    CliError,
    ErrorCategory,
    ErrorHandlingGroup,
    ExitCode,
    _uses_json_output,
    _uses_machine_output,
    emit_error,
)
from observal_cli.main import app

_MISSING = object()


def _response(
    status: int = 200,
    *,
    data: object = _MISSING,
    text: str | None = None,
    headers: dict[str, str] | None = None,
    url: str = "https://registry.example.test/api/v1/items",
) -> httpx.Response:
    kwargs: dict[str, object] = {
        "headers": headers or {},
        "request": httpx.Request("GET", url),
    }
    if data is not _MISSING:
        kwargs["json"] = data
    elif text is not None:
        kwargs["text"] = text
    return httpx.Response(status, **kwargs)


@pytest.fixture(autouse=True)
def isolated_client(monkeypatch):
    """Block live I/O and make client timing and logging deterministic."""

    def unexpected_http(*_args, **_kwargs):
        raise AssertionError("unexpected live HTTP request")

    for method in ("get", "post", "put", "patch", "delete"):
        monkeypatch.setattr(client.httpx, method, unexpected_http)

    clock = SimpleNamespace(
        monotonic=MagicMock(return_value=100.0),
        sleep=MagicMock(side_effect=AssertionError("unexpected real retry delay")),
    )
    monkeypatch.setattr(client, "time", clock)
    monkeypatch.setattr(client, "optic", MagicMock())
    monkeypatch.setattr(client, "logger", MagicMock())
    monkeypatch.setattr(client, "_version_enforced", False)
    monkeypatch.setattr(client, "_server_version_cache", None)
    return clock


def test_get_timeout_default():
    """Default timeout is 30s."""
    from observal_cli.config import get_timeout

    with (
        patch("observal_cli.config.load", return_value={"timeout": 30}),
        patch.dict("os.environ", {}, clear=True),
    ):
        assert get_timeout() == 30


def test_get_timeout_env_override():
    """OBSERVAL_TIMEOUT env var overrides config."""
    from observal_cli.config import get_timeout

    with patch.dict("os.environ", {"OBSERVAL_TIMEOUT": "60"}):
        assert get_timeout() == 60


def test_get_timeout_config_override():
    """Config file timeout is used when no env var."""
    from observal_cli.config import get_timeout

    with (
        patch("observal_cli.config.load", return_value={"timeout": 45}),
        patch.dict("os.environ", {}, clear=True),
    ):
        assert get_timeout() == 45


def test_handle_error_builds_categorized_failure():
    response = _response(401, data={"detail": "secret authentication detail"})
    error = httpx.HTTPStatusError("", request=response.request, response=response)

    with pytest.raises(CliError) as raised:
        client._handle_error(
            error,
            "/api/v1/test",
            operation="Authenticate test user",
            resource="test account",
        )

    assert raised.value.category is ErrorCategory.AUTH
    assert raised.value.contract_exit_code == ExitCode.AUTH
    assert raised.value.operation == "Authenticate test user"
    assert raised.value.resource == "test account"
    assert "secret authentication detail" not in raised.value.message


def test_handle_error_reads_ard_error_message():
    response = _response(422, data={"errorCode": "INVALID_AGENT_CARD", "message": "Agent Cards must use https."})
    error = httpx.HTTPStatusError("", request=response.request, response=response)

    with pytest.raises(CliError) as raised:
        client._handle_error(error, "/api/v1/ard/imports", operation="Register A2A agent", resource="card")

    assert raised.value.message == "Agent Cards must use https."


def test_handle_error_preserves_request_id_and_http_status():
    response = _response(503, text="Internal error", headers={"X-Request-ID": "request-123"})
    error = httpx.HTTPStatusError("", request=response.request, response=response)

    with pytest.raises(CliError) as raised:
        client._handle_error(error, "/api/v1/agents", operation="List agents", resource="agent registry")

    assert raised.value.category is ErrorCategory.UNAVAILABLE
    assert raised.value.http_status == 503
    assert raised.value.request_id == "request-123"


def test_config_save_sets_permissions(tmp_path):
    """Config save sets 0o600 permissions."""
    from observal_cli import config

    with (
        patch.object(config, "CONFIG_DIR", tmp_path),
        patch.object(config, "CONFIG_FILE", tmp_path / "config.json"),
    ):
        config.save({"server_url": "http://localhost:8000", "api_key": "test"})

        mode = os.stat(tmp_path / "config.json").st_mode & 0o777
        assert mode == 0o600


def test_config_write_permission_failure_is_categorized(monkeypatch):
    from observal_cli import config

    denied = PermissionError(13, "denied", str(config.CONFIG_FILE))
    monkeypatch.setattr(config, "_write_json", MagicMock(side_effect=denied))

    with pytest.raises(CliError) as error:
        config._write_config({"server_url": "http://localhost:8000"})

    assert error.value.category is ErrorCategory.PERMISSION
    assert error.value.operation == "Save CLI configuration"


def test_render_error_helper():
    """render.error() prints formatted error."""
    from observal_cli.render import error, success, warning

    # These should not raise
    error("test error", hint="try this")
    warning("test warning")
    success("test success")


def test_cli_version_header_uses_installed_version(monkeypatch):
    monkeypatch.setattr(importlib.metadata, "version", lambda package: "1.2.3")

    assert client._get_cli_version() == "1.2.3"


def test_cli_version_header_falls_back_for_uninstalled_package(monkeypatch):
    def missing(_package):
        raise importlib.metadata.PackageNotFoundError

    monkeypatch.setattr(importlib.metadata, "version", missing)

    assert client._get_cli_version() == "0.0.0"


def test_client_builds_bearer_and_version_headers(monkeypatch):
    get_config = MagicMock(
        return_value={
            "server_url": "https://registry.example.test/",
            "access_token": "fake-access-token",
        }
    )
    enforce = MagicMock()
    monkeypatch.setattr(client.config, "get_or_exit", get_config)
    monkeypatch.setattr(client, "_get_cli_version", lambda: "2.4.1")
    monkeypatch.setattr(client, "_enforce_version_once", enforce)

    base_url, headers = client._client()

    assert base_url == "https://registry.example.test"
    assert headers == {
        "Authorization": "Bearer fake-access-token",
        "X-Observal-CLI-Version": "2.4.1",
    }
    get_config.assert_called_once_with()
    enforce.assert_called_once_with(base_url)


def test_request_requires_authenticated_configuration(monkeypatch):
    failure = CliError(
        ErrorCategory.AUTH,
        "Authentication is required.",
        operation="Load authenticated CLI configuration",
    )
    get_config = MagicMock(side_effect=failure)
    enforce = MagicMock()
    monkeypatch.setattr(client.config, "get_or_exit", get_config)
    monkeypatch.setattr(client, "_enforce_version_once", enforce)

    with pytest.raises(CliError) as error:
        client.post("/api/v1/items", {"name": "example"})

    assert error.value is failure
    get_config.assert_called_once_with()
    enforce.assert_not_called()


def test_version_enforcement_runs_once(monkeypatch):
    from observal_cli import version_check

    check = MagicMock()
    monkeypatch.setattr(version_check, "check_version_compatibility", check)
    monkeypatch.setattr(sys, "argv", ["observal", "registry", "mcp", "list"])

    client._enforce_version_once("https://one.example.test")
    client._enforce_version_once("https://two.example.test")

    check.assert_called_once_with("https://one.example.test")


@pytest.mark.parametrize(
    "argv",
    [
        ["observal", "self", "upgrade"],
        ["observal", "-v", "server", "status"],
    ],
)
def test_version_enforcement_exempts_recovery_commands(monkeypatch, argv):
    from observal_cli import version_check

    check = MagicMock()
    monkeypatch.setattr(version_check, "check_version_compatibility", check)
    monkeypatch.setattr(sys, "argv", argv)

    client._enforce_version_once("https://registry.example.test")

    check.assert_not_called()
    assert client._version_enforced is True


def test_version_enforcement_without_subcommand_still_checks(monkeypatch):
    from observal_cli import version_check

    check = MagicMock()
    monkeypatch.setattr(version_check, "check_version_compatibility", check)
    monkeypatch.setattr(sys, "argv", ["observal", "-v"])

    client._enforce_version_once("https://registry.example.test")

    check.assert_called_once_with("https://registry.example.test")


@pytest.mark.parametrize(
    ("status", "expected_category", "expected_code"),
    [
        (401, ErrorCategory.AUTH, ExitCode.AUTH),
        (403, ErrorCategory.PERMISSION, ExitCode.PERMISSION),
        (404, ErrorCategory.NOT_FOUND, ExitCode.NOT_FOUND),
        (409, ErrorCategory.CONFLICT, ExitCode.CONFLICT),
        (422, ErrorCategory.VALIDATION, ExitCode.VALIDATION),
        (426, ErrorCategory.VERSION, ExitCode.VERSION),
        (429, ErrorCategory.RATE_LIMIT, ExitCode.RATE_LIMIT),
        (503, ErrorCategory.UNAVAILABLE, ExitCode.UNAVAILABLE),
    ],
)
def test_http_statuses_map_to_stable_categories(status, expected_category, expected_code):
    response = _response(
        status,
        data={"detail": "safe cause"},
        headers={"X-Request-ID": "request-123", "Retry-After": "12"},
    )
    error = httpx.HTTPStatusError("request failed", request=response.request, response=response)

    with pytest.raises(CliError) as raised:
        client._handle_error(
            error,
            "/api/v1/items/id",
            operation="Update item",
            resource="item id",
        )

    assert raised.value.category is expected_category
    assert raised.value.contract_exit_code == expected_code
    assert raised.value.operation == "Update item"
    assert raised.value.resource == "item id"
    assert raised.value.request_id == "request-123"
    assert raised.value.http_status == status
    assert raised.value.remediation


@pytest.mark.parametrize("path", ["/api/v1/teams/by-handle/platform", "/api/v1/insights/report-id"])
def test_not_found_has_generic_remediation_for_non_registry_resources(path):
    response = _response(404, data={"detail": "missing"})
    error = httpx.HTTPStatusError("request failed", request=response.request, response=response)

    with pytest.raises(CliError) as raised:
        client._handle_error(error, path, operation="Show resource", resource="resource")

    assert raised.value.remediation == "Check the identifier and retry."


def test_not_found_has_browse_remediation():
    response = _response(404, data={"detail": "missing"})
    error = httpx.HTTPStatusError("request failed", request=response.request, response=response)

    with pytest.raises(CliError) as raised:
        client._handle_error(error, "/api/v1/sandboxes/id", operation="Show sandbox", resource="sandbox id")

    assert "observal registry sandbox list" in raised.value.remediation


def test_rate_limit_uses_retry_after_header():
    response = _response(429, text="slow down", headers={"Retry-After": "12 seconds"})
    error = httpx.HTTPStatusError("request failed", request=response.request, response=response)

    with pytest.raises(CliError) as raised:
        client._handle_error(error, operation="List items", resource="registry items")

    assert raised.value.category is ErrorCategory.RATE_LIMIT
    assert raised.value.remediation == "Retry in 12 seconds."


def test_connection_failure_is_unavailable_without_token(monkeypatch):
    monkeypatch.setattr(
        client.config,
        "load",
        lambda: {"server_url": "https://registry.example.test", "access_token": "fake-secret-token"},
    )

    with pytest.raises(CliError) as raised:
        client._handle_connect(operation="List items", resource="registry items", detail="connection refused")

    assert raised.value.category is ErrorCategory.UNAVAILABLE
    assert raised.value.contract_exit_code == ExitCode.UNAVAILABLE
    assert "fake-secret-token" not in repr(raised.value)


def test_connection_failure_handles_missing_server(monkeypatch):
    monkeypatch.setattr(client.config, "load", lambda: {})

    with pytest.raises(CliError) as raised:
        client._handle_connect()

    assert raised.value.resource == "server not set"


def test_timeout_failure_includes_context(monkeypatch):
    monkeypatch.setattr(client.config, "get_timeout", lambda: 17)

    with pytest.raises(CliError) as raised:
        client._handle_timeout(
            "/api/v1/items",
            operation="List items",
            resource="registry items",
            detail="slow request",
        )

    assert raised.value.category is ErrorCategory.UNAVAILABLE
    assert raised.value.contract_exit_code == ExitCode.UNAVAILABLE
    assert raised.value.message == "The request timed out after 17 seconds."
    assert raised.value.operation == "List items"
    assert raised.value.resource == "registry items"


def test_error_renderer_emits_json_to_stderr(capsys):
    error = CliError(
        ErrorCategory.NOT_FOUND,
        "Item was not found.",
        operation="Show item",
        resource="item 123",
        remediation="List available items.",
        request_id="request-123",
        http_status=404,
        detail="internal detail",
    )

    emit_error(error, json_mode=True, debug=False)

    captured = capsys.readouterr()
    assert captured.out == ""
    payload = json.loads(captured.err)
    assert payload == {
        "error": {
            "category": "not_found",
            "message": "Item was not found.",
            "operation": "Show item",
            "exit_code": 5,
            "resource": "item 123",
            "remediation": "List available items.",
            "request_id": "request-123",
            "http_status": 404,
        }
    }


def test_error_renderer_exposes_detail_only_in_debug(capsys):
    error = CliError(ErrorCategory.UNEXPECTED, "Failed.", operation="Test", detail="internal detail")

    emit_error(error, json_mode=True, debug=True)

    payload = json.loads(capsys.readouterr().err)
    assert payload["error"]["detail"] == "internal detail"


def test_error_renderer_always_exposes_safe_structured_result(capsys):
    error = CliError(
        ErrorCategory.VALIDATION,
        "Input is incomplete.",
        operation="Install item",
        detail="secret internal detail",
        result={"needs_input": True, "inputs": [{"kind": "header", "name": "Authorization"}]},
    )

    emit_error(error, json_mode=True, debug=False)

    payload = json.loads(capsys.readouterr().err)["error"]
    assert payload["result"]["needs_input"] is True
    assert "secret internal detail" not in json.dumps(payload)


def test_every_leaf_command_exposes_json_machine_output():
    from typer.main import get_command

    root = get_command(app)
    leaves = []

    def walk(command):
        if isinstance(command, Group):
            for child in command.commands.values():
                walk(child)
        else:
            leaves.append(command)

    walk(root)
    assert len(leaves) == 232
    for command in leaves:
        output = next((parameter for parameter in command.params if parameter.name == "output"), None)
        assert output is not None, command.name
        choices = {getattr(choice, "value", choice) for choice in getattr(output.type, "choices", ())}
        assert "json" in choices, command.name


def test_json_error_mode_distinguishes_format_from_file_destination():
    from typer.main import get_command

    root = get_command(app)
    assert _uses_json_output(root, ("agent", "show", "reviewer", "--output", "json")) is True
    assert _uses_json_output(root, ("agent", "show", "reviewer", "-ojson")) is True
    assert _uses_json_output(root, ("doctor", "support", "bundle", "--output", "json")) is True
    assert _uses_json_output(root, ("doctor", "support", "bundle", "--file", "json")) is False


def test_machine_output_mode_includes_raw_json_commands_only():
    from typer.main import get_command

    root = get_command(app)
    assert _uses_machine_output(root, ("registry", "mcp", "install", "item", "--harness", "pi", "--raw"))
    assert not _uses_machine_output(root, ("registry", "mcp", "show", "--raw"))


def test_json_error_mode_supports_typer_choice_without_click_inheritance():
    from typer.main import get_command

    root = get_command(app)
    output = next(
        parameter for parameter in root.commands["auth"].commands["login"].params if parameter.name == "output"
    )
    original = output.type
    output.type = SimpleNamespace(choices=("table", "json"))
    try:
        assert _uses_json_output(root, ("auth", "login", "--output", "json")) is True
    finally:
        output.type = original


def test_root_boundary_emits_json_usage_error_to_stderr():
    result = CliRunner().invoke(app, ["agent", "show", "--output", "json"])

    assert result.exit_code == ExitCode.USAGE
    assert result.stdout == ""
    payload = json.loads(result.stderr)
    assert payload["error"]["category"] == "usage"
    assert payload["error"]["operation"] == "Run observal agent show"


def test_package_conflict_uses_structured_json_error_boundary(monkeypatch):
    import observal_cli.main as main

    monkeypatch.setattr(importlib.metadata, "metadata", lambda _name: {"Name": "observal"})
    monkeypatch.setattr(main, "_migrate_legacy_mcp_configs", lambda: None)
    monkeypatch.setattr(main, "_try_lockfile_migration", lambda: None)

    result = CliRunner().invoke(app, ["config", "path", "--output", "json"])

    assert result.exit_code == ExitCode.CONFLICT
    assert result.stdout == ""
    error = json.loads(result.stderr)["error"]
    assert error["category"] == "conflict"
    assert error["operation"] == "Start Observal CLI"


def test_startup_migration_notice_never_contaminates_json_stdout(monkeypatch):
    import observal_cli.main as main

    monkeypatch.setattr(client.config, "migrate_shimmed_mcp_configs", lambda: [Path("/tmp/legacy.json")])
    monkeypatch.setattr(main, "_try_lockfile_migration", lambda: None)

    result = CliRunner().invoke(app, ["config", "path", "--output", "json"])

    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)["path"]
    assert "Migrated" not in result.stdout
    assert result.stderr == ""


def test_startup_migration_failure_is_a_structured_json_error(monkeypatch):
    import observal_cli.main as main

    def fail_migration():
        raise RuntimeError("private migration detail")

    monkeypatch.setattr(client.config, "migrate_shimmed_mcp_configs", fail_migration)
    monkeypatch.setattr(main, "_try_lockfile_migration", lambda: None)

    result = CliRunner().invoke(app, ["config", "path", "--output", "json"])

    assert result.exit_code == ExitCode.UNAVAILABLE
    assert result.stdout == ""
    error = json.loads(result.stderr)["error"]
    assert error["operation"] == "Migrate legacy MCP configuration"
    assert "private migration detail" not in result.stderr


def test_lockfile_migration_failure_emits_structured_warning_without_corrupting_stdout(tmp_path, monkeypatch):
    import observal_cli.lockfile as lockfile
    import observal_cli.main as main

    config_dir = tmp_path / ".observal"
    config_dir.mkdir()
    monkeypatch.setattr(main, "_migrate_legacy_mcp_configs", lambda: None)
    monkeypatch.setattr(main, "_sync_bundled_skills", lambda: None)
    monkeypatch.setattr(lockfile, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(lockfile, "LOCKFILE_PATH", config_dir / "lockfile.json")
    monkeypatch.setattr(lockfile, "migrate_agent_markers", MagicMock(side_effect=OSError("private path")))

    result = CliRunner().invoke(app, ["config", "path", "--output", "json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["path"]
    warning = json.loads(result.stderr)["warning"]
    assert warning["category"] == "local_state_migration"
    assert "private path" not in result.stderr


def test_raw_json_validation_uses_machine_error_contract(monkeypatch):
    import observal_cli.main as main

    monkeypatch.setattr(main, "_migrate_legacy_mcp_configs", lambda: None)
    monkeypatch.setattr(main, "_try_lockfile_migration", lambda: None)

    result = CliRunner().invoke(
        app,
        ["registry", "mcp", "install", "alice/search", "--harness", "unknown", "--raw"],
    )

    assert result.exit_code == ExitCode.VALIDATION
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["category"] == "validation"


@pytest.mark.parametrize(
    "argv",
    [
        ["observal", "agent", "list", "--output", "json"],
        ["observal", "registry", "mcp", "install", "item", "--harness", "pi", "--raw"],
    ],
)
def test_update_banner_is_suppressed_for_tty_machine_output(monkeypatch, argv):
    import observal_cli.main as main

    class TTY(StringIO):
        def isatty(self):
            return True

    stdout = TTY()
    stderr = TTY()
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    main._show_update_banner()

    assert stdout.getvalue() == ""
    assert stderr.getvalue() == ""


def test_missing_authentication_uses_stable_json_contract(monkeypatch):
    monkeypatch.setattr(client.config, "load", lambda: {})

    result = CliRunner().invoke(app, ["auth", "whoami", "--output", "json"])

    assert result.exit_code == ExitCode.AUTH
    assert result.stdout == ""
    payload = json.loads(result.stderr)["error"]
    assert payload["category"] == "authentication"
    assert payload["operation"] == "Load authenticated CLI configuration"


def test_command_api_failure_uses_audited_context(monkeypatch):
    response = _response(404, data={"detail": "Agent not found"}, headers={"X-Request-ID": "request-456"})
    status_error = httpx.HTTPStatusError("missing", request=response.request, response=response)
    monkeypatch.setattr(client, "_client", lambda: ("https://registry.example.test", {}))
    monkeypatch.setattr(client, "_request_with_retry", MagicMock(side_effect=status_error))

    result = CliRunner().invoke(app, ["agent", "show", "reviewer", "--output", "json"])

    assert result.exit_code == ExitCode.NOT_FOUND
    assert result.stdout == ""
    payload = json.loads(result.stderr)["error"]
    assert payload["operation"] == "Show agent"
    assert payload["resource"] == "agent registry"
    assert payload["request_id"] == "request-456"


def test_team_visibility_json_failure_uses_audited_context(monkeypatch):
    response = _response(403, data={"detail": "Reviewer role required"}, headers={"X-Request-ID": "request-789"})
    status_error = httpx.HTTPStatusError("forbidden", request=response.request, response=response)
    monkeypatch.setattr(client, "_client", lambda: ("https://registry.example.test", {}))
    monkeypatch.setattr(client, "_request_with_retry", MagicMock(side_effect=status_error))

    result = CliRunner().invoke(app, ["team", "visibility", "list-requests", "--output", "json"])

    assert result.exit_code == ExitCode.PERMISSION
    assert result.stdout == ""
    payload = json.loads(result.stderr)["error"]
    assert payload["category"] == "permission"
    assert payload["operation"] == "List teamspace visibility requests"
    assert payload["resource"] == "teamspaces"
    assert payload["request_id"] == "request-789"
    assert "detail" not in payload


def test_all_cli_api_calls_have_custom_error_context():
    methods = {"get", "get_text", "get_with_headers", "request_json", "post", "put", "patch", "delete"}
    missing = []
    cli_root = Path(__file__).resolve().parents[1] / "observal_cli"
    paths = [*cli_root.glob("cmd_*.py"), cli_root / "lockfile_reconcile.py"]
    for path in paths:
        tree = ast.parse(path.read_text())
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"client", "_client"}
                and node.func.attr in methods
            ):
                continue
            current = node
            while current in parents and not isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                current = parents[current]
            function = current.name if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)) else ""
            if function not in OPERATION_LABELS or path.name not in RESOURCE_LABELS:
                missing.append(f"{path}:{node.lineno}")
    assert missing == []


def test_root_group_enforces_error_contract_for_all_commands():
    from typer.main import get_command

    root = get_command(app)
    assert isinstance(root, ErrorHandlingGroup)

    executable = []

    def walk(command):
        if not isinstance(command, Group) or command.invoke_without_command:
            executable.append(command)
        if isinstance(command, Group):
            for child in command.commands.values():
                walk(child)

    walk(root)
    assert len(executable) == 238


@pytest.mark.parametrize(
    "stored",
    [
        {},
        {"server_url": "https://registry.example.test"},
        {"refresh_token": "fake-refresh-token"},
    ],
)
def test_refresh_requires_server_and_refresh_token(monkeypatch, stored):
    post = MagicMock()
    save = MagicMock()
    monkeypatch.setattr(client.config, "load", lambda: stored)
    monkeypatch.setattr(client.config, "save", save)
    monkeypatch.setattr(client.httpx, "post", post)

    assert client._try_refresh_token() is False
    post.assert_not_called()
    save.assert_not_called()


def test_refresh_saves_rotated_tokens(monkeypatch):
    post = MagicMock(
        return_value=_response(
            200,
            data={
                "access_token": "fake-new-access-token",
                "refresh_token": "fake-new-refresh-token",
            },
        )
    )
    save = MagicMock()
    monkeypatch.setattr(
        client.config,
        "load",
        lambda: {
            "server_url": "https://registry.example.test/",
            "refresh_token": "fake-old-refresh-token",
        },
    )
    monkeypatch.setattr(client.config, "save", save)
    monkeypatch.setattr(client.httpx, "post", post)

    assert client._try_refresh_token() is True
    post.assert_called_once_with(
        "https://registry.example.test/api/v1/auth/token/refresh",
        json={"refresh_token": "fake-old-refresh-token"},
        timeout=10,
    )
    save.assert_called_once_with(
        {
            "access_token": "fake-new-access-token",
            "refresh_token": "fake-new-refresh-token",
        }
    )


@pytest.mark.parametrize(
    "outcome",
    [
        _response(401, data={"detail": "expired"}),
        _response(200, data={"access_token": "missing-rotated-refresh"}),
        _response(200, text="not-json", headers={"content-type": "text/plain"}),
        httpx.ConnectError("refresh unavailable"),
    ],
)
def test_refresh_failures_do_not_overwrite_tokens(monkeypatch, outcome):
    post = MagicMock()
    if isinstance(outcome, Exception):
        post.side_effect = outcome
    else:
        post.return_value = outcome
    save = MagicMock()
    monkeypatch.setattr(
        client.config,
        "load",
        lambda: {
            "server_url": "https://registry.example.test",
            "refresh_token": "fake-old-refresh-token",
        },
    )
    monkeypatch.setattr(client.config, "save", save)
    monkeypatch.setattr(client.httpx, "post", post)

    assert client._try_refresh_token() is False
    save.assert_not_called()


@pytest.mark.parametrize("status", [429, 503, 504])
def test_transient_status_retries_once_then_succeeds(monkeypatch, isolated_client, status):
    responses = [_response(status, data={"detail": "retry"}), _response(200, data={"ok": True})]
    get = MagicMock(side_effect=responses)
    isolated_client.sleep.side_effect = None
    monkeypatch.setattr(client.config, "get_timeout", lambda: 19)
    monkeypatch.setattr(client.httpx, "get", get)

    result = client._request_with_retry(
        "get",
        "https://registry.example.test/api/v1/items",
        {"Authorization": "Bearer fake-access-token"},
        params={"page": 2},
    )

    assert result is responses[1]
    assert get.call_count == 2
    assert all(item.kwargs["timeout"] == 19 for item in get.call_args_list)
    assert all(item.kwargs["params"] == {"page": 2} for item in get.call_args_list)
    isolated_client.sleep.assert_called_once_with(0.5)


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_mutations_never_retry_transient_responses(monkeypatch, isolated_client, method):
    response = _response(503, data={"detail": "unknown mutation state"})
    request = MagicMock(return_value=response)
    monkeypatch.setattr(client.config, "get_timeout", lambda: 19)
    monkeypatch.setattr(client.httpx, method, request)

    with pytest.raises(httpx.HTTPStatusError):
        client._request_with_retry(method, "https://registry.example.test/api/v1/items", {})

    request.assert_called_once()
    isolated_client.sleep.assert_not_called()


def test_retry_honors_header_and_stops_after_three_attempts(monkeypatch, isolated_client):
    responses = [
        _response(429, data={"detail": "retry"}, headers={"Retry-After": "2.5"}),
        _response(503, data={"detail": "retry"}),
        _response(504, data={"detail": "failed"}),
    ]
    get = MagicMock(side_effect=responses)
    isolated_client.sleep.side_effect = None
    monkeypatch.setattr(client.config, "get_timeout", lambda: 30)
    monkeypatch.setattr(client.httpx, "get", get)

    with pytest.raises(httpx.HTTPStatusError) as error:
        client._request_with_retry("get", "https://registry.example.test/api/v1/items", {})

    assert error.value.response is responses[-1]
    assert get.call_count == 3
    assert isolated_client.sleep.call_args_list == [call(2.5), call(1.0)]


def test_unauthorized_request_refreshes_and_retries_once(monkeypatch):
    responses = iter([_response(401, data={"detail": "expired"}), _response(200, data={"ok": True})])
    requests = []

    def get(url, **kwargs):
        requests.append((url, {**kwargs, "headers": dict(kwargs["headers"])}))
        return next(responses)

    refresh = MagicMock(return_value=True)
    monkeypatch.setattr(client.config, "get_timeout", lambda: 21)
    monkeypatch.setattr(client.config, "load", lambda: {"access_token": "fake-new-access-token"})
    monkeypatch.setattr(client, "_try_refresh_token", refresh)
    monkeypatch.setattr(client.httpx, "get", get)
    headers = {"Authorization": "Bearer fake-old-access-token"}

    response = client._request_with_retry("get", "https://registry.example.test/api/v1/items", headers)

    assert response.status_code == 200
    assert len(requests) == 2
    assert requests[0][1]["headers"]["Authorization"] == "Bearer fake-old-access-token"
    assert requests[1][1]["headers"]["Authorization"] == "Bearer fake-new-access-token"
    assert requests[0][1]["timeout"] == requests[1][1]["timeout"] == 21
    assert headers["Authorization"] == "Bearer fake-new-access-token"
    refresh.assert_called_once_with()


def test_unauthorized_request_never_refreshes_twice(monkeypatch):
    responses = [_response(401, data={"detail": "expired"}), _response(401, data={"detail": "still expired"})]
    get = MagicMock(side_effect=responses)
    refresh = MagicMock(return_value=True)
    monkeypatch.setattr(client.config, "get_timeout", lambda: 30)
    monkeypatch.setattr(client.config, "load", lambda: {"access_token": "fake-new-access-token"})
    monkeypatch.setattr(client, "_try_refresh_token", refresh)
    monkeypatch.setattr(client.httpx, "get", get)

    with pytest.raises(httpx.HTTPStatusError) as error:
        client._request_with_retry(
            "get",
            "https://registry.example.test/api/v1/items",
            {"Authorization": "Bearer fake-old-access-token"},
        )

    assert error.value.response is responses[1]
    assert get.call_count == 2
    refresh.assert_called_once_with()


def test_unauthorized_request_does_not_retry_when_refresh_fails(monkeypatch):
    response = _response(401, data={"detail": "expired"})
    get = MagicMock(return_value=response)
    refresh = MagicMock(return_value=False)
    monkeypatch.setattr(client.config, "get_timeout", lambda: 30)
    monkeypatch.setattr(client, "_try_refresh_token", refresh)
    monkeypatch.setattr(client.httpx, "get", get)

    with pytest.raises(httpx.HTTPStatusError):
        client._request_with_retry("get", "https://registry.example.test/api/v1/items", {})

    get.assert_called_once()
    refresh.assert_called_once_with()


def test_debug_request_url_redacts_userinfo(monkeypatch):
    url = "https://fake-user:fake-password@registry.example.test:8443/api/v1/items"
    get = MagicMock(return_value=_response(200, data={"ok": True}, url=url))
    monkeypatch.setattr(client.config, "get_timeout", lambda: 30)
    monkeypatch.setattr(client.httpx, "get", get)

    client._request_with_retry("get", url, {})

    debug_calls = repr(client.optic.debug.call_args_list)
    assert "https://registry.example.test/api/v1/items" in debug_calls
    assert "fake-user" not in debug_calls
    assert "fake-password" not in debug_calls
    assert "8443" not in debug_calls
    assert get.call_args.args[0] == url


@pytest.mark.parametrize("method", ["get", "post", "put", "patch", "delete"])
def test_http_wrappers_construct_authenticated_requests(monkeypatch, method):
    response = _response(200, data={"method": method})
    transport = MagicMock(return_value=response)
    enforce = MagicMock()
    monkeypatch.setattr(
        client.config,
        "get_or_exit",
        lambda **_kwargs: {
            "server_url": "https://registry.example.test/",
            "access_token": "fake-access-token",
        },
    )
    monkeypatch.setattr(client.config, "get_timeout", lambda: 13)
    monkeypatch.setattr(client, "_get_cli_version", lambda: "3.2.1")
    monkeypatch.setattr(client, "_enforce_version_once", enforce)
    monkeypatch.setattr(client.httpx, method, transport)

    path = "/api/v1/items/id"
    if method == "get":
        result = client.get(path, params={"page": 4})
        payload = {"params": {"page": 4}}
    elif method == "delete":
        result = client.delete(path)
        payload = {}
    else:
        result = getattr(client, method)(path, json_data={"name": "example"})
        payload = {"json": {"name": "example"}}

    assert result == {"method": method}
    transport.assert_called_once_with(
        f"https://registry.example.test{path}",
        headers={
            "Authorization": "Bearer fake-access-token",
            "X-Observal-CLI-Version": "3.2.1",
        },
        timeout=13,
        **payload,
    )
    enforce.assert_called_once_with("https://registry.example.test")


def test_get_text_returns_validated_raw_response(monkeypatch):
    response = _response(200, text="a,b\n1,2\n", headers={"Content-Type": "text/csv; charset=utf-8"})
    request = MagicMock(return_value=response)
    monkeypatch.setattr(
        client,
        "_client",
        lambda: ("https://registry.example.test", {"Authorization": "Bearer fake-access-token"}),
    )
    monkeypatch.setattr(client, "_request_with_retry", request)

    result = client.get_text("/api/v1/admin/audit-log/export", content_type="text/csv")

    assert result == "a,b\n1,2\n"
    request.assert_called_once_with(
        "get",
        "https://registry.example.test/api/v1/admin/audit-log/export",
        {"Authorization": "Bearer fake-access-token"},
    )


def test_get_text_rejects_unexpected_content_type(monkeypatch):
    response = _response(200, data={"detail": "not csv"}, headers={"Content-Type": "application/json"})
    monkeypatch.setattr(client, "_client", lambda: ("https://registry.example.test", {}))
    monkeypatch.setattr(client, "_request_with_retry", MagicMock(return_value=response))

    with pytest.raises(CliError) as raised:
        client.get_text(
            "/api/v1/admin/audit-log/export",
            content_type="text/csv",
            operation="Export audit log",
            resource="audit log",
        )

    assert raised.value.category is ErrorCategory.UNAVAILABLE
    assert raised.value.contract_exit_code == ExitCode.UNAVAILABLE
    assert raised.value.operation == "Export audit log"
    assert raised.value.resource == "audit log"


def test_get_with_headers_normalizes_pagination_headers(monkeypatch):
    response = _response(
        200,
        data={"items": [1, 2]},
        headers={"X-Total-Count": "42", "X-Next-Page": "3"},
    )
    request = MagicMock(return_value=response)
    monkeypatch.setattr(
        client,
        "_client",
        lambda: ("https://registry.example.test", {"Authorization": "Bearer fake-access-token"}),
    )
    monkeypatch.setattr(client, "_request_with_retry", request)

    data, headers = client.get_with_headers("/api/v1/items", params={"page": 2})

    assert data == {"items": [1, 2]}
    assert headers["x-total-count"] == "42"
    assert headers["x-next-page"] == "3"
    assert "X-Total-Count" not in headers
    request.assert_called_once_with(
        "get",
        "https://registry.example.test/api/v1/items",
        {"Authorization": "Bearer fake-access-token"},
        params={"page": 2},
    )


def test_request_json_forwards_method_query_and_body(monkeypatch):
    response = _response(200, data=[{"id": "one"}])
    request = MagicMock(return_value=response)
    monkeypatch.setattr(client, "_client", lambda: ("https://registry.example.test", {}))
    monkeypatch.setattr(client, "_request_with_retry", request)

    result = client.request_json(
        "PATCH",
        "/api/v1/items/id",
        params={"notify": "true"},
        json_data={"name": "updated"},
        operation="Call Observal API",
        resource="API endpoint",
    )

    assert result == [{"id": "one"}]
    request.assert_called_once_with(
        "patch",
        "https://registry.example.test/api/v1/items/id",
        {},
        params={"notify": "true"},
        json={"name": "updated"},
    )


@pytest.mark.parametrize(
    ("method", "response"),
    [
        ("post", _response(204)),
        ("post", _response(200)),
        ("delete", _response(204)),
        ("delete", _response(200)),
    ],
)
def test_empty_post_and_delete_responses_return_empty_dict(monkeypatch, method, response):
    monkeypatch.setattr(client, "_client", lambda: ("https://registry.example.test", {}))
    monkeypatch.setattr(client, "_request_with_retry", MagicMock(return_value=response))

    if method == "post":
        result = client.post("/api/v1/items", json_data={"name": "example"})
    else:
        result = client.delete("/api/v1/items/id")

    assert result == {}


def _invoke_wrapper(name: str):
    if name in {"get", "get_text", "get_with_headers"}:
        return getattr(client, name)("/api/v1/items", params={"page": 1})
    if name == "delete":
        return client.delete("/api/v1/items/id")
    return getattr(client, name)("/api/v1/items/id", json_data={"name": "example"})


@pytest.mark.parametrize("wrapper", ["get", "get_text", "get_with_headers", "post", "put", "patch", "delete"])
@pytest.mark.parametrize(
    ("failure", "expected_category"),
    [
        ("status", ErrorCategory.VALIDATION),
        ("timeout", ErrorCategory.UNAVAILABLE),
        ("connection", ErrorCategory.UNAVAILABLE),
    ],
)
def test_wrappers_convert_transport_failures(monkeypatch, wrapper, failure, expected_category):
    response = _response(400, data={"detail": "invalid"})
    status_error = httpx.HTTPStatusError("invalid", request=response.request, response=response)
    exception = {
        "status": status_error,
        "timeout": httpx.ReadTimeout("slow request"),
        "connection": httpx.ConnectError("connection refused"),
    }[failure]
    monkeypatch.setattr(client, "_client", lambda: ("https://registry.example.test", {}))
    monkeypatch.setattr(client, "_request_with_retry", MagicMock(side_effect=exception))

    with pytest.raises(CliError) as raised:
        _invoke_wrapper(wrapper)

    assert raised.value.category is expected_category
    assert raised.value.operation
    assert raised.value.resource


@pytest.mark.parametrize(
    ("item_type", "expected_type"),
    [
        ("agents", "agent"),
        ("mcps", "mcp"),
        ("skills", "skill"),
        ("hooks", "hook"),
        ("prompts", "prompt"),
        ("sandboxes", "sandbox"),
        ("custom", "custom"),
    ],
)
def test_registry_reference_resolves_qualified_alias(monkeypatch, item_type, expected_type):
    resolve_alias = MagicMock(return_value="owner/item")
    get = MagicMock(return_value={"id": 123})
    monkeypatch.setattr(client.config, "resolve_alias", resolve_alias)
    monkeypatch.setattr(client, "get", get)

    assert client.resolve_registry_reference(item_type, "@favorite") == "123"
    resolve_alias.assert_called_once_with("@favorite")
    get.assert_called_once_with(
        "/api/v1/registry/resolve",
        params={"type": expected_type, "identifier": "owner/item"},
    )


@pytest.mark.parametrize("resolved", ["item", "00000000-0000-0000-0000-000000000123"])
def test_registry_reference_preserves_bare_and_uuid_aliases(monkeypatch, resolved):
    monkeypatch.setattr(client.config, "resolve_alias", lambda _reference: resolved)
    get = MagicMock()
    monkeypatch.setattr(client, "get", get)

    assert client.resolve_registry_reference("agent", "input") == resolved
    get.assert_not_called()


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ({"qualified_name": "owner/item", "name": "Item"}, "owner/item"),
        ({"name": "Legacy"}, "Legacy"),
        ({}, ""),
    ],
)
def test_canonical_name_prefers_qualified_identity(item, expected):
    assert client.canonical_name(item) == expected


def test_team_uuid_is_returned_without_lookup(monkeypatch):
    team_id = "11111111-1111-1111-1111-111111111111"
    get = MagicMock()
    monkeypatch.setattr(client, "get", get)

    assert client.resolve_team_id(f"  {team_id.upper()}  ") == team_id
    get.assert_not_called()


@pytest.mark.parametrize(
    ("reference", "encoded_handle"),
    [
        ("  @PLATFORM-tools ", "platform-tools"),
        ("@../admin/secrets", "..%2Fadmin%2Fsecrets"),
    ],
)
def test_team_handle_lookup_is_normalized_and_path_safe(monkeypatch, reference, encoded_handle):
    headers = {"Authorization": "Bearer test-token"}
    request = MagicMock(return_value=_response(200, data={"id": 42}))
    fallback = MagicMock()
    monkeypatch.setattr(client, "_client", lambda: ("https://registry.example.test", headers))
    monkeypatch.setattr(client, "_request_with_retry", request)
    monkeypatch.setattr(client, "get", fallback)

    assert client.resolve_team_id(reference) == "42"
    request.assert_called_once_with(
        "get",
        f"https://registry.example.test/api/v1/teams/by-handle/{encoded_handle}",
        headers,
    )
    fallback.assert_not_called()


def test_unknown_team_handle_is_a_parameter_error(monkeypatch):
    headers = {"Authorization": "Bearer test-token"}
    request = MagicMock(side_effect=httpx.ConnectError("lookup unavailable"))
    fallback = MagicMock(return_value=[{"id": "team-1", "handle": "other"}])
    monkeypatch.setattr(client, "_client", lambda: ("https://registry.example.test", headers))
    monkeypatch.setattr(client, "_request_with_retry", request)
    monkeypatch.setattr(client, "get", fallback)

    with pytest.raises(typer.BadParameter) as error:
        client.resolve_team_id("@missing")

    request.assert_called_once()
    fallback.assert_called_once_with("/api/v1/teams/all")
    assert "No teamspace with handle '@missing'" in str(error.value)
    assert error.value.param_hint == "team"


def test_publish_target_defaults_to_public():
    payload = {"name": "Example"}

    client.add_publish_target(payload, team=None, visibility=None)

    assert payload == {"name": "Example", "visibility": "public"}


def test_publish_target_resolves_team_for_team_visibility(monkeypatch):
    resolve = MagicMock(return_value="team-id")
    monkeypatch.setattr(client, "resolve_team_id", resolve)
    payload = {}

    client.add_publish_target(payload, team="@platform", visibility=" TEAM ")

    assert payload == {"visibility": "team", "team_id": "team-id"}
    resolve.assert_called_once_with("@platform")


def test_public_publish_target_can_retain_team_namespace(monkeypatch):
    monkeypatch.setattr(client, "resolve_team_id", lambda _team: "team-id")
    payload = {}

    client.add_publish_target(payload, team="platform", visibility="PUBLIC")

    assert payload == {"visibility": "public", "team_id": "team-id"}


@pytest.mark.parametrize(
    ("team", "visibility", "message", "hint"),
    [
        (None, "team", "requires", "team"),
        (None, "private", "visibility must be", "visibility"),
    ],
)
def test_publish_target_rejects_invalid_combinations(team, visibility, message, hint):
    with pytest.raises(typer.BadParameter) as error:
        client.add_publish_target({}, team=team, visibility=visibility)

    assert message in str(error.value)
    assert error.value.param_hint == hint


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ({}, False),
        ({"server_url": "https://registry.example.test"}, False),
        ({"access_token": "fake-access-token"}, False),
    ],
)
def test_registered_agent_policy_requires_configuration(monkeypatch, stored, expected):
    get = MagicMock()
    monkeypatch.setattr(client.config, "load", lambda: stored)
    monkeypatch.setattr(client.httpx, "get", get)

    assert client.get_registered_agents_only() is expected
    get.assert_not_called()


def test_registered_agent_policy_uses_bearer_header(monkeypatch):
    get = MagicMock(return_value=_response(200, data={"registered_agents_only": True}))
    monkeypatch.setattr(
        client.config,
        "load",
        lambda: {
            "server_url": "https://registry.example.test/",
            "access_token": "fake-access-token",
        },
    )
    monkeypatch.setattr(client.httpx, "get", get)

    assert client.get_registered_agents_only() is True
    get.assert_called_once_with(
        "https://registry.example.test/api/v1/admin/registered-agents-only",
        headers={"Authorization": "Bearer fake-access-token"},
        timeout=5,
    )


@pytest.mark.parametrize(
    "outcome",
    [
        _response(200, data={}),
        _response(503, data={"detail": "unavailable"}),
        httpx.ConnectError("unavailable"),
    ],
)
def test_registered_agent_policy_defaults_to_disabled_on_error(monkeypatch, outcome):
    get = MagicMock()
    if isinstance(outcome, Exception):
        get.side_effect = outcome
    else:
        get.return_value = outcome
    monkeypatch.setattr(
        client.config,
        "load",
        lambda: {"server_url": "https://registry.example.test", "access_token": "fake-access-token"},
    )
    monkeypatch.setattr(client.httpx, "get", get)

    assert client.get_registered_agents_only() is False


@pytest.mark.parametrize(
    ("function_name", "path"),
    [
        ("get_registered_agent_names", "/api/v1/agents"),
        ("get_registered_mcp_names", "/api/v1/mcp"),
    ],
)
def test_registered_name_helpers_filter_empty_names(monkeypatch, function_name, path):
    get = MagicMock(
        return_value=_response(
            200,
            data=[{"name": "alpha"}, {"name": ""}, {}, {"name": "beta"}],
        )
    )
    monkeypatch.setattr(
        client.config,
        "load",
        lambda: {"server_url": "https://registry.example.test/", "access_token": "fake-access-token"},
    )
    monkeypatch.setattr(client.httpx, "get", get)

    assert getattr(client, function_name)() == {"alpha", "beta"}
    get.assert_called_once_with(
        f"https://registry.example.test{path}",
        headers={"Authorization": "Bearer fake-access-token"},
        timeout=5,
    )


@pytest.mark.parametrize("function_name", ["get_registered_agent_names", "get_registered_mcp_names"])
@pytest.mark.parametrize("failure", ["missing-config", "bad-status", "connection"])
def test_registered_name_helpers_fail_open(monkeypatch, function_name, failure):
    if failure == "missing-config":
        stored = {}
        get = MagicMock()
    elif failure == "bad-status":
        stored = {"server_url": "https://registry.example.test", "access_token": "fake-access-token"}
        get = MagicMock(return_value=_response(503, data={"detail": "unavailable"}))
    else:
        stored = {"server_url": "https://registry.example.test", "access_token": "fake-access-token"}
        get = MagicMock(side_effect=httpx.ConnectError("unavailable"))
    monkeypatch.setattr(client.config, "load", lambda: stored)
    monkeypatch.setattr(client.httpx, "get", get)

    assert getattr(client, function_name)() == set()
    if failure == "missing-config":
        get.assert_not_called()


def test_health_without_server_skips_transport(monkeypatch):
    get = MagicMock()
    monkeypatch.setattr(client.config, "load", lambda: {})
    monkeypatch.setattr(client.httpx, "get", get)

    assert client.health() == (False, 0)
    get.assert_not_called()


@pytest.mark.parametrize(("status", "expected"), [(200, True), (503, False)])
def test_health_reports_status_and_latency(monkeypatch, isolated_client, status, expected):
    get = MagicMock(return_value=_response(status, data={"status": "ok"}))
    isolated_client.monotonic.side_effect = [10.0, 10.125]
    monkeypatch.setattr(client.config, "load", lambda: {"server_url": "https://registry.example.test/"})
    monkeypatch.setattr(client.httpx, "get", get)

    assert client.health() == (expected, 125.0)
    get.assert_called_once_with("https://registry.example.test/health", timeout=5)


def test_health_connection_failure_returns_zero_latency(monkeypatch):
    monkeypatch.setattr(client.config, "load", lambda: {"server_url": "https://registry.example.test"})
    monkeypatch.setattr(client.httpx, "get", MagicMock(side_effect=httpx.ConnectError("unavailable")))

    assert client.health() == (False, 0)


def test_server_supports_fetches_and_caches_effective_version(monkeypatch):
    from observal_cli import features

    get = MagicMock(return_value={"server_version": "2.0.0"})
    available = MagicMock(return_value=True)
    monkeypatch.setattr(client, "get", get)
    monkeypatch.setattr(client, "_get_cli_version", lambda: "1.5.0")
    monkeypatch.setattr(features, "is_available", available)

    assert client.server_supports("example-feature") is True
    assert client.server_supports("second-feature") is True

    get.assert_called_once_with("/api/v1/config/version")
    assert available.call_args_list == [
        call("example-feature", "1.5.0"),
        call("second-feature", "1.5.0"),
    ]


def test_server_supports_returns_false_when_version_lookup_fails(monkeypatch):
    monkeypatch.setattr(client, "get", MagicMock(side_effect=typer.Exit(1)))

    assert client.server_supports("example-feature") is False


def test_server_supports_uses_server_string_when_version_parsing_fails(monkeypatch):
    from observal_cli import features

    available = MagicMock(return_value=False)
    monkeypatch.setattr(client, "_server_version_cache", "development")
    monkeypatch.setattr(client, "_get_cli_version", lambda: "also-development")
    monkeypatch.setattr(features, "is_available", available)

    assert client.server_supports("example-feature") is False
    available.assert_called_once_with("example-feature", "development")
