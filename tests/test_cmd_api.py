# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Behavioral coverage for the authenticated API escape hatch."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
import typer
from typer.testing import CliRunner

import observal_cli.cmd_api as api

runner = CliRunner()
api_app = typer.Typer()
api_app.command("api")(api.api_request)


@pytest.fixture
def api_call(monkeypatch: pytest.MonkeyPatch):
    boundary = Mock(side_effect=AssertionError("unexpected client.request_json"))
    monkeypatch.setattr(api.client, "request_json", boundary)
    return boundary


def _allow(boundary: Mock, response):
    boundary.side_effect = None
    boundary.return_value = response
    return boundary


def test_get_preserves_raw_api_array_json(api_call):
    _allow(api_call, [{"id": "team-1"}])

    result = runner.invoke(api_app, ["GET", "/api/v1/teams", "--param", "limit=10", "--output", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [{"id": "team-1"}]
    api_call.assert_called_once_with(
        "GET",
        "/api/v1/teams",
        params=[("limit", "10")],
        json_data=None,
        operation="Call Observal API",
        resource="Observal API endpoint",
    )


def test_repeated_query_parameters_preserve_order_and_values(api_call):
    _allow(api_call, {"ok": True})

    result = runner.invoke(
        api_app,
        ["GET", "/api/v1/items", "--param", "tag=a", "--param", "tag=b", "--output", "json"],
    )

    assert result.exit_code == 0
    assert api_call.call_args.kwargs["params"] == [("tag", "a"), ("tag", "b")]


def test_post_reads_json_file_and_returns_direct_object(tmp_path, api_call):
    body = tmp_path / "team.json"
    body.write_text('{"name":"Platform"}', encoding="utf-8")
    _allow(api_call, {"id": "team-1", "name": "Platform"})

    result = runner.invoke(
        api_app,
        ["POST", "/api/v1/teams", "--from-file", str(body), "--output", "json"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"id": "team-1", "name": "Platform"}
    api_call.assert_called_once_with(
        "POST",
        "/api/v1/teams",
        params=None,
        json_data={"name": "Platform"},
        operation="Call Observal API",
        resource="Observal API endpoint",
    )


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ('[{"name":"one"}]', [{"name": "one"}]),
        ('"plain"', "plain"),
        ("42", 42),
        ("true", True),
    ],
)
def test_post_accepts_any_non_null_json_file_value(tmp_path, api_call, content, expected):
    body = tmp_path / "body.json"
    body.write_text(content, encoding="utf-8")
    _allow(api_call, {"ok": True})

    result = runner.invoke(api_app, ["POST", "/api/v1/items", "--from-file", str(body), "--output", "json"])

    assert result.exit_code == 0
    assert api_call.call_args.kwargs["json_data"] == expected


def test_client_retry_layer_preserves_explicit_json_null(monkeypatch):
    response = Mock(status_code=200, headers={})
    post = Mock(return_value=response)
    monkeypatch.setattr(api.client.httpx, "post", post)
    monkeypatch.setattr(api.client.config, "get_timeout", lambda: 17)

    assert (
        api.client._request_with_retry(
            "post",
            "https://registry.example.test/api/v1/items",
            {},
            json=None,
            send_json=True,
        )
        is response
    )
    post.assert_called_once_with(
        "https://registry.example.test/api/v1/items",
        headers={"Content-Type": "application/json"},
        timeout=17,
        content=b"null",
    )


def test_post_can_send_explicit_json_null(api_call):
    _allow(api_call, {"ok": True})

    result = runner.invoke(api_app, ["POST", "/api/v1/items", "--output", "json"], input="null")

    assert result.exit_code == 0
    api_call.assert_called_once_with(
        "POST",
        "/api/v1/items",
        params=None,
        json_data=None,
        send_json=True,
        operation="Call Observal API",
        resource="Observal API endpoint",
    )


def test_patch_reads_json_stdin(api_call):
    _allow(api_call, {"visibility": "private"})

    result = runner.invoke(
        api_app,
        ["PATCH", "/api/v1/teams/team-1/visibility", "--output", "json"],
        input='{"visibility":"private"}',
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"visibility": "private"}
    api_call.assert_called_once_with(
        "PATCH",
        "/api/v1/teams/team-1/visibility",
        params=None,
        json_data={"visibility": "private"},
        operation="Call Observal API",
        resource="Observal API endpoint",
    )


def test_default_output_renders_generic_table(api_call, monkeypatch: pytest.MonkeyPatch):
    rendered: list[object] = []
    monkeypatch.setattr(api, "rprint", rendered.append)

    api._render_response({"id": "team-1", "members": []})

    table = rendered[0]
    assert table.title == "API response"
    assert [column.header for column in table.columns] == ["field", "value"]
    assert table.row_count == 2
    api_call.assert_not_called()


@pytest.mark.parametrize(
    "arguments",
    [
        ["TRACE", "/api/v1/teams"],
        ["GET", "https://evil.example/api/v1/teams"],
        ["GET", "/api/v1/../health"],
        ["GET", "/api/v1/%252e%252e/health"],
        ["GET", "/api/v1/teams%253Fadmin=true"],
        ["GET", "/api/v1/teams", "--param", "broken"],
        ["GET", "C:/Program Files/Git/api/v1/../health"],
        ["GET", "C:/Program Files/Git/health"],
    ],
)
def test_invalid_method_path_and_params_fail_before_http(arguments, api_call):
    result = runner.invoke(api_app, arguments)

    assert result.exit_code == 7
    api_call.assert_not_called()


@pytest.mark.parametrize(
    "path",
    [
        "api/v1/mcps/abc",
        "C:/Program Files/Git/api/v1/mcps/abc",
        "C:\\Program Files\\Git\\api\\v1\\mcps\\abc",
    ],
)
def test_git_bash_converted_and_slashless_paths_reach_the_endpoint(path, api_call):
    _allow(api_call, {"id": "abc"})

    result = runner.invoke(api_app, ["GET", path, "--output", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"id": "abc"}
    assert api_call.call_args.args == ("GET", "/api/v1/mcps/abc")
    assert ("Git Bash" in result.stderr) is path.startswith("C:")


def test_json_validation_error_keeps_stdout_clean(api_call, monkeypatch: pytest.MonkeyPatch):
    import observal_cli.main as main

    monkeypatch.setattr(main, "_migrate_legacy_mcp_configs", lambda: None)
    monkeypatch.setattr(main, "_try_lockfile_migration", lambda: None)

    result = runner.invoke(main.app, ["api", "TRACE", "/api/v1/teams", "--output", "json"])

    assert result.exit_code == 7
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["category"] == "validation"
    api_call.assert_not_called()


def test_get_rejects_request_body(tmp_path, api_call):
    body = tmp_path / "body.json"
    body.write_text("{}", encoding="utf-8")

    result = runner.invoke(api_app, ["GET", "/api/v1/teams", "--from-file", str(body)])

    assert result.exit_code == 7
    api_call.assert_not_called()


def test_get_rejects_explicit_null_body(api_call):
    result = runner.invoke(api_app, ["GET", "/api/v1/teams"], input="null")

    assert result.exit_code == 7
    api_call.assert_not_called()
