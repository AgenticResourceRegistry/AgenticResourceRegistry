# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Component fork CLI contract: help, target, structured output and safe provenance."""

import json
from unittest.mock import Mock

import httpx
import pytest
from click.utils import strip_ansi
from typer.main import get_command
from typer.testing import CliRunner

from observal_cli import cmd_fork
from observal_cli.main import app
from observal_cli.tests.test_cmd_component_submit_flags import _assert_example_parses, _command_tree, _help_examples

runner = CliRunner()
KINDS = ("mcp", "skill", "hook", "prompt", "sandbox")


@pytest.fixture(autouse=True)
def _local_cli(monkeypatch):
    monkeypatch.setattr("observal_cli.main._try_lockfile_migration", lambda: None)
    monkeypatch.setattr("observal_cli.main._migrate_legacy_mcp_configs", lambda: None)


@pytest.mark.parametrize("kind", KINDS)
def test_component_fork_help_and_json_contract(monkeypatch, kind):
    path = ("registry", kind, cmd_fork.FORK_COMMANDS.get(kind, "fork"))
    help_result = runner.invoke(app, [*path, "--help"])
    assert help_result.exit_code == 0, help_result.output
    assert f"observal registry {kind} {cmd_fork.FORK_COMMANDS.get(kind, 'fork')}" in help_result.output
    assert "--new-version" in strip_ansi(help_result.output)
    resolve = Mock(return_value="source-uuid")
    post = Mock(
        return_value={
            "id": "fork-uuid",
            "qualified_name": "alice/copy",
            "status": "draft",
            "forked_from": {"available": True, "qualified_name": "acme/original", "version": "1.2.0"},
        }
    )
    monkeypatch.setattr(cmd_fork.client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(cmd_fork.client, "post", post)
    target = Mock(
        side_effect=lambda payload, team, visibility: payload.update({"team_id": "team-uuid", "visibility": visibility})
    )
    monkeypatch.setattr(cmd_fork.client, "add_publish_target", target)
    result = runner.invoke(
        app,
        [
            *path,
            "acme/original",
            "--name",
            "Copy",
            "--version",
            "1.2.0",
            "--new-version",
            "0.1.0",
            "--team",
            "payments",
            "--visibility",
            "team",
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == post.return_value
    resolve.assert_called_once_with(kind, "acme/original")
    target.assert_called_once()
    assert post.call_args.args[0] == f"/api/v1/{cmd_fork._PLURALS[kind]}/source-uuid/fork"
    assert post.call_args.kwargs["json_data"] == {
        "name": "Copy",
        "version": "1.2.0",
        "new_version": "0.1.0",
        "team_id": "team-uuid",
        "visibility": "team",
    }
    table = runner.invoke(app, [*path, "acme/original"])
    assert table.exit_code == 0, table.output
    assert "alice/copy" in table.stdout
    assert f"registry {kind} edit" in table.stdout
    assert "acme/original@1.2.0" in table.stdout
    assert post.call_args.kwargs["json_data"] == {}


@pytest.mark.parametrize("kind", (*KINDS, "agent"))
def test_show_table_and_json_include_only_visibility_checked_provenance(monkeypatch, kind):
    item = {
        "id": "fork-uuid",
        "name": "copy",
        "slug": "copy",
        "namespace": "alice",
        "qualified_name": "alice/copy",
        "version": "1.2.0",
        "status": "approved",
        "forked_from": {"available": False},
        "fork_count": 2,
        "created_at": None,
        "supported_harnesses": [],
        "model_name": "gpt-4o",
        "handler_config": {},
    }
    monkeypatch.setattr(cmd_fork.client, "resolve_registry_reference", Mock(return_value="fork-uuid"))
    monkeypatch.setattr(cmd_fork.client, "get", Mock(return_value=item))
    path = ["agent"] if kind == "agent" else ["registry", kind]
    table = runner.invoke(app, [*path, "show", "alice/copy"])
    assert table.exit_code == 0, (kind, table.output)
    assert "Source unavailable" in table.stdout
    assert "Forks" in table.stdout
    machine = runner.invoke(app, [*path, "show", "alice/copy", "--output", "json"])
    assert machine.exit_code == 0, (kind, machine.output)
    assert json.loads(machine.stdout)["forked_from"] == {"available": False}


def test_six_fork_help_screens_have_parsable_canonical_examples():
    targets = {
        "observal agent fork",
        *(f"observal registry {kind} {cmd_fork.FORK_COMMANDS.get(kind, 'fork')}" for kind in KINDS),
    }
    for path, command in _command_tree(get_command(app)):
        if path not in targets:
            continue
        examples = _help_examples(command.help or "")
        assert 1 <= len(examples) <= 3, path
        assert all(line.startswith(path + " ") for line in examples)
        for example in examples:
            _assert_example_parses(command, path, example)
        targets.remove(path)
    assert not targets


def test_inaccessible_source_never_uses_saved_ref():
    item = {"forked_from": {"available": False, "forked_from_ref": "secret/name@9.0.0"}, "fork_count": 0}
    text = str(cmd_fork.fork_detail_rows(item))
    assert "Source unavailable" in text
    assert "secret/name" not in text
    assert "('Forks', '0')" in text


@pytest.mark.parametrize(
    "status,exit_code,category", [(404, 5, "not_found"), (409, 6, "conflict"), (429, 8, "rate_limit")]
)
def test_api_failures_keep_context_request_id_and_clean_json_stdout(monkeypatch, status, exit_code, category):
    monkeypatch.setattr(cmd_fork.client, "resolve_registry_reference", Mock(return_value="source-uuid"))
    monkeypatch.setattr(cmd_fork.client, "_client", lambda: ("https://registry.example", {}))
    request = httpx.Request("POST", "https://registry.example/api/v1/skills/source-uuid/fork")
    response = httpx.Response(
        status, request=request, headers={"X-Request-ID": "request-42"}, json={"detail": "No approved release"}
    )
    error = httpx.HTTPStatusError("request rejected", request=request, response=response)
    monkeypatch.setattr(cmd_fork.client, "_request_with_retry", Mock(side_effect=error))
    result = runner.invoke(app, ["registry", "skill", "fork-listing", "acme/original", "--output", "json"])
    assert result.exit_code == exit_code
    assert result.stdout == ""
    payload = json.loads(result.stderr)["error"]
    assert payload["category"] == category and payload["exit_code"] == exit_code
    assert payload["operation"] == "Fork registry component"
    assert payload["resource"] == "registry component"
    assert payload["request_id"] == "request-42" and payload["http_status"] == status
    assert payload.get("remediation")


def test_invalid_output_is_usage_error_without_post(monkeypatch):
    post = Mock()
    monkeypatch.setattr(cmd_fork.client, "post", post)
    result = runner.invoke(app, ["registry", "skill", "fork-listing", "a/b", "--output", "xml"])
    assert result.exit_code == 2
    post.assert_not_called()
