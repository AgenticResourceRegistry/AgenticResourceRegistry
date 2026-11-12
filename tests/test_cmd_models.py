# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Registry model catalog CLI contract tests."""

from __future__ import annotations

import json
import re
from unittest.mock import MagicMock, call

from typer.testing import CliRunner

from observal_cli import cmd_models, model_catalog
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


def test_harness_option_is_passed_to_fetch_catalog(monkeypatch) -> None:
    fetch = MagicMock(return_value=_catalog([]))
    monkeypatch.setattr(cmd_models.model_catalog, "fetch_catalog", fetch)

    runner.invoke(app, ["registry", "models", "--harness", "pi", "--output", "json"])
    runner.invoke(app, ["registry", "models", "list", "--harness", "pi", "--output", "json"])
    runner.invoke(app, ["registry", "models", "list", "--output", "json"])

    assert fetch.call_args_list == [call(harness="pi"), call(harness="pi"), call(harness=None)]


def test_table_footer_and_missing_optional_fields(monkeypatch) -> None:
    catalog = {
        "models": [
            {"harness": "pi", "model_id": "with-fields", "kind": "alias", "display_name": "Shown Name"},
            {"harness": "pi", "model_id": "bare-row"},
        ],
        "source": "test-source",
        "degraded": False,
    }
    fetch = MagicMock(return_value=catalog)
    monkeypatch.setattr(cmd_models.model_catalog, "fetch_catalog", fetch)

    result = runner.invoke(app, ["registry", "models"])

    assert result.exit_code == 0
    fetch.assert_called_once_with(harness=None)
    assert "source: test-source, count: 2" in result.output
    bare_line = next(line for line in result.output.splitlines() if "bare-row" in line)
    cells = [cell.strip() for cell in re.split(r"[│┃|]", bare_line)[1:-1]]
    # kind falls back to "exact"; display_name falls back to an empty cell.
    assert cells == ["pi", "bare-row", "exact", ""]


def test_empty_table_renders_with_zero_count(monkeypatch) -> None:
    fetch = MagicMock(return_value=_catalog([]))
    monkeypatch.setattr(cmd_models.model_catalog, "fetch_catalog", fetch)

    result = runner.invoke(app, ["registry", "models", "list"])

    assert result.exit_code == 0
    fetch.assert_called_once_with(harness=None)
    assert "model_id" in result.output
    assert "count: 0" in result.output


def test_real_catalog_returns_only_the_requested_harness() -> None:
    harness = next(h for h in model_catalog.valid_harnesses() if model_catalog.fetch_catalog(harness=h)["models"])

    result = runner.invoke(app, ["registry", "models", "list", "--harness", harness, "--output", "json"])

    assert result.exit_code == 0
    rows = json.loads(result.stdout)["models"]
    assert rows
    assert {row["harness"] for row in rows} == {harness}
