# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Registry model catalog CLI contract tests."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from typer.testing import CliRunner

from observal_cli import cmd_models
from observal_cli.main import app

runner = CliRunner()


def _catalog(models: list[dict]) -> dict:
    return {"models": models, "source": "harness-registry", "degraded": False}


def test_direct_and_list_json_return_catalog_metadata(monkeypatch) -> None:
    catalog = _catalog(
        [
            {
                "harness": "pi",
                "model_id": "anthropic/claude-sonnet-4-6",
                "kind": "exact",
                "display_name": "Claude Sonnet 4.6",
            }
        ]
    )
    fetch = MagicMock(return_value=catalog)
    monkeypatch.setattr(cmd_models.model_catalog, "fetch_catalog", fetch)

    direct = runner.invoke(app, ["registry", "models", "--harness", "pi", "--output", "json"])
    explicit = runner.invoke(app, ["registry", "models", "list", "--harness", "pi", "--output", "json"])

    assert direct.exit_code == explicit.exit_code == 0
    assert json.loads(direct.stdout) == catalog
    assert json.loads(explicit.stdout) == catalog
    assert fetch.call_count == 2


def test_table_and_empty_json_are_deterministic(monkeypatch) -> None:
    row = {
        "harness": "pi",
        "model_id": "model[0]",
        "kind": "exact",
        "display_name": "Model [bold] literal",
    }
    monkeypatch.setattr(
        cmd_models.model_catalog,
        "fetch_catalog",
        MagicMock(side_effect=[_catalog([row]), _catalog([])]),
    )

    table = runner.invoke(app, ["registry", "models"])
    empty = runner.invoke(app, ["registry", "models", "list", "--output", "json"])

    assert table.exit_code == empty.exit_code == 0
    assert "model[0]" in table.output
    assert "Model [bold] literal" in table.output
    assert json.loads(empty.stdout) == _catalog([])


def test_invalid_harness_is_a_usage_error_with_clean_json() -> None:
    result = runner.invoke(
        app,
        ["registry", "models", "list", "--harness", "unknown", "--output", "json"],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["category"] == "usage"
