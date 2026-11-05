# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Shared Registry CLI contract tests."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from observal_cli import client, config
from observal_cli.errors import CliError, ErrorCategory, ExitCode
from observal_cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate_boundaries(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "ALIASES_FILE", tmp_path / "aliases.json")
    monkeypatch.setattr(config, "LAST_RESULTS_FILE", tmp_path / "last_results.json")
    monkeypatch.setattr(client, "resolve_registry_reference", MagicMock(return_value="resolved-id"))


def test_row_references_are_scoped_and_empty_lists_clear_them() -> None:
    config.save_last_results([{"id": "skill-id", "name": "review"}], "skill")
    assert config.resolve_alias("1", expected_type="skill") == "skill-id"

    with pytest.raises(CliError) as wrong_type:
        config.resolve_alias("1", expected_type="mcp")
    assert wrong_type.value.category is ErrorCategory.NOT_FOUND

    config.save_last_results([], "skill")
    with pytest.raises(CliError) as missing:
        config.resolve_alias("1", expected_type="skill")
    assert missing.value.category is ErrorCategory.NOT_FOUND


def test_malformed_row_cache_is_a_validation_error() -> None:
    config.LAST_RESULTS_FILE.write_text('{"ids": "broken", "names": {}}')

    with pytest.raises(CliError) as raised:
        config.load_last_results()

    assert raised.value.category is ErrorCategory.VALIDATION


@pytest.mark.parametrize(
    ("component", "collection"),
    [("mcp", "mcps"), ("skill", "skills"), ("hook", "hooks"), ("prompt", "prompts"), ("sandbox", "sandboxes")],
)
def test_co_author_list_uses_real_api_path_and_json(component, collection, monkeypatch) -> None:
    get = MagicMock(return_value=[{"id": "user-id", "email": "dev@example.test"}])
    monkeypatch.setattr(client, "get", get)

    result = runner.invoke(
        app,
        ["registry", component, "co-authors", "list", "acme/item", "--output", "json"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["items"][0]["id"] == "user-id"
    get.assert_called_once_with(f"/api/v1/{collection}/resolved-id/co-authors")
    client.resolve_registry_reference.assert_called_once_with(collection, "acme/item")


def test_co_author_mutations_return_json(monkeypatch) -> None:
    post = MagicMock(return_value={"id": "user-id", "email": "dev@example.test"})
    delete = MagicMock(return_value={"detail": "Co-author removed"})
    monkeypatch.setattr(client, "post", post)
    monkeypatch.setattr(client, "delete", delete)

    added = runner.invoke(
        app,
        ["registry", "mcp", "co-authors", "add", "acme/item", "dev@example.test", "--output", "json"],
    )
    removed = runner.invoke(
        app,
        [
            "registry",
            "mcp",
            "co-authors",
            "remove",
            "acme/item",
            "22222222-2222-2222-2222-222222222222",
            "--output",
            "json",
        ],
    )

    assert json.loads(added.stdout)["id"] == "user-id"
    assert json.loads(removed.stdout) == {"detail": "Co-author removed"}


@pytest.mark.parametrize("action,status", [("archive", "archived"), ("unarchive", "approved")])
def test_component_lifecycle_returns_json(action, status, monkeypatch) -> None:
    patch = MagicMock(return_value={"id": "item-id", "name": "item", "status": status})
    monkeypatch.setattr(client, "patch", patch)

    result = runner.invoke(
        app,
        ["registry", "prompt", action, "acme/item", "--yes", "--output", "json"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == status
    patch.assert_called_once_with(f"/api/v1/prompts/resolved-id/{action}")


def test_json_lifecycle_requires_confirmation_bypass(monkeypatch) -> None:
    patch = MagicMock()
    monkeypatch.setattr(client, "patch", patch)

    result = runner.invoke(app, ["registry", "sandbox", "archive", "acme/item", "--output", "json"])

    assert result.exit_code == ExitCode.VALIDATION
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["category"] == "validation"
    patch.assert_not_called()


def test_transfer_owner_returns_json(monkeypatch) -> None:
    post = MagicMock(return_value={"id": "item-id", "owner": "bob", "qualified_name": "bob/item"})
    monkeypatch.setattr(client, "post", post)

    result = runner.invoke(
        app,
        ["registry", "skill", "transfer-owner", "acme/item", "bob", "--yes", "--output", "json"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["qualified_name"] == "bob/item"
    post.assert_called_once_with(
        "/api/v1/skills/resolved-id/transfer-ownership",
        json_data={"username": "bob"},
    )
