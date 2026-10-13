# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Sandbox Registry CLI contract tests."""

from __future__ import annotations

import json
from contextlib import nullcontext
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

import observal_cli.cmd_sandbox as sandbox
from observal_cli.errors import CliError, ErrorCategory
from observal_cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(sandbox, "spinner", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(sandbox.config, "load", MagicMock(return_value={"username": "alice"}))
    monkeypatch.setattr(sandbox.config, "save_last_results", MagicMock())


def _item(**overrides) -> dict:
    item = {
        "id": "sandbox-1",
        "name": "runner",
        "namespace": "acme",
        "slug": "runner",
        "qualified_name": "acme/runner",
        "version": "1.0.0",
        "status": "approved",
        "runtime_type": "docker",
        "image": "python:3.13",
        "description": "Python runner",
    }
    item.update(overrides)
    return item


def test_submit_json_is_noninteractive_and_does_not_print_removed_install(monkeypatch):
    response = {"id": "sandbox-1", "name": "runner", "status": "pending"}
    post = MagicMock(return_value=response)
    monkeypatch.setattr(sandbox.client, "post", post)

    result = runner.invoke(
        app,
        [
            "registry",
            "sandbox",
            "submit",
            "--name",
            "runner",
            "--description",
            "Python runner",
            "--runtime-type",
            "docker",
            "--image",
            "python:3.13",
            "--resource-limits",
            '{"memory_mb":512}',
            "--runtime-config",
            "{}",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout) == response
    assert "sandbox install" not in result.stdout


def test_interactive_submit_defaults_optional_json_objects(monkeypatch):
    defaults = []

    def text_input(prompt, default=None):
        defaults.append((prompt, default))
        return (
            default
            if default is not None
            else {"Sandbox name": "runner", "Description": "Runner", "Image": "python"}[prompt]
        )

    monkeypatch.setattr(sandbox, "text_input", text_input)
    monkeypatch.setattr(sandbox, "select_one", lambda *_args, **_kwargs: "docker")
    post = MagicMock(return_value={"id": "sandbox-1", "name": "runner", "status": "pending"})
    monkeypatch.setattr(sandbox.client, "post", post)

    result = runner.invoke(app, ["registry", "sandbox", "submit"])

    assert result.exit_code == 0, result.output
    payload = post.call_args.args[1]
    assert payload["resource_limits"] == {}
    assert payload["runtime_config"] == {}
    assert ("Resource limits (JSON)", "{}") in defaults
    assert ("Runtime config (JSON)", "{}") in defaults


def test_list_table_json_empty_and_typed_cache(monkeypatch):
    hostile = "Clean [/tmp] [bold]literal[/bold]"
    item = _item(name=hostile, slug=hostile)
    get = MagicMock(side_effect=[[item], [item], []])
    save = MagicMock()
    monkeypatch.setattr(sandbox.client, "get", get)
    monkeypatch.setattr(sandbox.config, "save_last_results", save)

    table = runner.invoke(app, ["registry", "sandbox", "list"])
    structured = runner.invoke(app, ["registry", "sandbox", "list", "--output", "json"])
    empty = runner.invoke(app, ["registry", "sandbox", "list", "--output", "json"])

    assert table.exit_code == structured.exit_code == empty.exit_code == 0
    assert hostile in table.output
    assert json.loads(structured.stdout)["items"][0]["id"] == "sandbox-1"
    assert json.loads(empty.stdout) == {"items": [], "total": 0, "page": 1, "page_size": 0}
    assert save.call_args_list[-1].args == ([], "sandbox")


def test_show_table_and_json_escape_registry_values(monkeypatch):
    hostile = "Clean [/tmp] [bold]literal[/bold]"
    item = _item(description=hostile)
    monkeypatch.setattr(sandbox.client, "resolve_registry_reference", MagicMock(return_value="resolved"))
    monkeypatch.setattr(sandbox.client, "get", MagicMock(return_value=item))

    table = runner.invoke(app, ["registry", "sandbox", "show", "acme/runner"])
    structured = runner.invoke(app, ["registry", "sandbox", "show", "acme/runner", "--output", "json"])

    assert table.exit_code == structured.exit_code == 0
    assert hostile in table.output
    assert json.loads(structured.stdout) == item


def test_edit_json_returns_result_and_preserves_conflict(monkeypatch):
    monkeypatch.setattr(sandbox.client, "resolve_registry_reference", MagicMock(return_value="resolved"))
    post = MagicMock(return_value={})
    monkeypatch.setattr(sandbox.client, "post", post)
    monkeypatch.setattr(
        sandbox.client,
        "put",
        MagicMock(return_value={"id": "sandbox-1", "name": "runner", "status": "pending"}),
    )

    result = runner.invoke(
        app,
        ["registry", "sandbox", "edit", "runner", "--description", "Updated", "--output", "json"],
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["status"] == "pending"

    post.side_effect = CliError(
        ErrorCategory.CONFLICT,
        "The sandbox is currently being edited.",
        operation="Edit sandbox",
        resource="sandbox registry",
    )
    conflict = runner.invoke(app, ["registry", "sandbox", "edit", "runner", "--description", "Again"])
    assert conflict.exit_code == 6


@pytest.mark.parametrize(
    "arguments",
    [
        ["list", "--runtime", "unknown"],
        ["submit", "--name", "incomplete"],
        [
            "submit",
            "--name",
            "runner",
            "--description",
            "Runner",
            "--runtime-type",
            "docker",
            "--image",
            "python",
            "--resource-limits",
            "[]",
        ],
        ["edit", "runner"],
        ["edit", "runner", "--runtime-type", "unknown"],
        ["edit", "runner", "--resource-limits", "not-json"],
    ],
)
def test_validation_uses_stable_exit_code(arguments, monkeypatch):
    monkeypatch.setattr(sandbox.client, "resolve_registry_reference", MagicMock(return_value="resolved"))
    get = MagicMock()
    post = MagicMock()
    monkeypatch.setattr(sandbox.client, "get", get)
    monkeypatch.setattr(sandbox.client, "post", post)

    result = runner.invoke(app, ["registry", "sandbox", *arguments])

    assert result.exit_code == 7
    get.assert_not_called()
    post.assert_not_called()


def test_removed_install_command_is_not_registered() -> None:
    result = runner.invoke(app, ["registry", "sandbox", "install", "runner"])

    assert result.exit_code == 2
