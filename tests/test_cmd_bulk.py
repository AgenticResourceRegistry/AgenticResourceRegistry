# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Behavioral coverage for mixed Registry component bulk submission."""

from __future__ import annotations

import json
from unittest.mock import Mock, call

import pytest
from typer.testing import CliRunner

import observal_cli.cmd_bulk as bulk
from observal_cli.errors import CliError, ErrorCategory

runner = CliRunner()


def _write_components(tmp_path, components):
    path = tmp_path / "components.json"
    path.write_text(json.dumps({"components": components}), encoding="utf-8")
    return path


@pytest.fixture
def boundaries(monkeypatch: pytest.MonkeyPatch):
    post = Mock(side_effect=AssertionError("unexpected client.post"))
    get = Mock(side_effect=AssertionError("unexpected client.get"))
    load = Mock(return_value={"username": "alice"})
    monkeypatch.setattr(bulk.client, "post", post)
    monkeypatch.setattr(bulk.client, "get", get)
    monkeypatch.setattr(bulk.client.config, "load", load)
    return post, get, load


def test_dry_run_emits_parseable_json_without_http(tmp_path, boundaries):
    post, get, _load = boundaries
    path = _write_components(
        tmp_path,
        [
            {"type": "skill", "name": "review", "description": "Review", "task_type": "general"},
            {"type": "prompt", "name": "brief", "description": "Brief", "category": "general"},
        ],
    )

    result = runner.invoke(
        bulk.bulk_app,
        ["--from-file", str(path), "--dry-run", "--output", "json"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "total": 2,
        "submitted": 0,
        "skipped": 0,
        "errors": 0,
        "partial": False,
        "dry_run": True,
        "results": [
            {"type": "skill", "name": "review", "status": "planned"},
            {"type": "prompt", "name": "brief", "status": "planned"},
        ],
    }
    post.assert_not_called()
    get.assert_not_called()


def test_execute_submits_mixed_entries_and_reports_conflicts(tmp_path, boundaries):
    post, get, load = boundaries
    path = _write_components(
        tmp_path,
        [
            {
                "type": "skill",
                "name": "review",
                "description": "Review",
                "task_type": "general",
                "owner": "   ",
            },
            {"type": "prompt", "name": "brief", "description": "Brief", "category": "general"},
            {"type": "sandbox", "name": "runner", "description": "Runner", "runtime_type": "docker"},
        ],
    )
    conflict = CliError(
        ErrorCategory.CONFLICT,
        "Prompt already exists.",
        operation="Bulk submit components",
        resource="prompt submission",
        request_id="request-1",
    )
    post.side_effect = [
        {"id": "skill-1", "qualified_name": "alice/review", "status": "pending"},
        conflict,
        {"id": "sandbox-1", "qualified_name": "alice/runner", "status": "pending"},
    ]

    result = runner.invoke(
        bulk.bulk_app,
        ["--from-file", str(path), "--yes", "--output", "json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert {key: payload[key] for key in ("total", "submitted", "skipped", "errors", "partial", "dry_run")} == {
        "total": 3,
        "submitted": 2,
        "skipped": 1,
        "errors": 0,
        "partial": False,
        "dry_run": False,
    }
    assert payload["results"][1]["error"] == {
        "category": "conflict",
        "message": "Prompt already exists.",
        "request_id": "request-1",
    }
    load.assert_called_once_with()
    get.assert_not_called()
    assert post.call_args_list == [
        call(
            "/api/v1/skills/submit",
            json_data={
                "name": "review",
                "description": "Review",
                "task_type": "general",
                "version": "1.0.0",
                "owner": "alice",
            },
            operation="Bulk submit components",
            resource="skill submission",
        ),
        call(
            "/api/v1/prompts/submit",
            json_data={
                "name": "brief",
                "description": "Brief",
                "category": "general",
                "version": "1.0.0",
                "owner": "alice",
            },
            operation="Bulk submit components",
            resource="prompt submission",
        ),
        call(
            "/api/v1/sandboxes/submit",
            json_data={
                "name": "runner",
                "description": "Runner",
                "runtime_type": "docker",
                "version": "1.0.0",
                "owner": "alice",
            },
            operation="Bulk submit components",
            resource="sandbox submission",
        ),
    ]


def test_partial_json_batch_preserves_result_and_exits_nonzero(tmp_path, boundaries, monkeypatch):
    import observal_cli.main as main

    post, _get, _load = boundaries
    path = _write_components(
        tmp_path,
        [
            {"type": "skill", "name": "review", "description": "Review"},
            {"type": "hook", "name": "guard", "description": "Guard"},
        ],
    )
    post.side_effect = [
        {"id": "skill-1", "qualified_name": "alice/review", "status": "pending"},
        CliError(ErrorCategory.VALIDATION, "Invalid hook.", operation="Bulk submit components"),
    ]
    monkeypatch.setattr(main, "_migrate_legacy_mcp_configs", lambda: None)
    monkeypatch.setattr(main, "_try_lockfile_migration", lambda: None)

    result = runner.invoke(
        main.app,
        ["registry", "bulk", "submit", "--from-file", str(path), "--yes", "--output", "json"],
    )

    assert result.exit_code == 11
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["partial"] is True
    assert payload["submitted"] == 1
    assert payload["errors"] == 1
    assert [item["status"] for item in payload["results"]] == ["submitted", "error"]


def test_default_output_renders_preview_and_results(tmp_path, boundaries, monkeypatch: pytest.MonkeyPatch):
    post, _get, _load = boundaries
    rendered: list[object] = []
    monkeypatch.setattr(bulk, "rprint", rendered.append)
    path = _write_components(
        tmp_path,
        [{"type": "hook", "name": "guard", "description": "Guard", "event": "Stop"}],
    )
    post.side_effect = None
    post.return_value = {"id": "hook-1", "status": "pending"}

    result = runner.invoke(bulk.bulk_app, ["--from-file", str(path), "--yes"])

    assert result.exit_code == 0, result.output
    assert rendered[0].title == "Components to submit (1)"
    assert rendered[1].title == "Bulk submission results"
    assert rendered[2] == "[green]1 submitted[/green], [yellow]0 skipped[/yellow], [red]0 errors[/red]"


@pytest.mark.parametrize(
    "components",
    [
        [],
        [{"type": "unknown", "name": "x"}],
        [{"type": "skill"}],
        [{"type": "skill", "name": "same"}, {"type": "skill", "name": "SAME"}],
    ],
)
def test_invalid_files_fail_before_http(tmp_path, boundaries, components):
    post, get, _load = boundaries
    path = _write_components(tmp_path, components)

    result = runner.invoke(bulk.bulk_app, ["--from-file", str(path), "--dry-run"])

    assert result.exit_code == 7
    post.assert_not_called()
    get.assert_not_called()


def test_json_validation_error_keeps_stdout_clean(tmp_path, boundaries, monkeypatch: pytest.MonkeyPatch):
    import observal_cli.main as main

    post, _get, _load = boundaries
    path = _write_components(tmp_path, [])
    monkeypatch.setattr(main, "_migrate_legacy_mcp_configs", lambda: None)
    monkeypatch.setattr(main, "_try_lockfile_migration", lambda: None)

    result = runner.invoke(
        main.app,
        ["registry", "bulk", "submit", "--from-file", str(path), "--dry-run", "--output", "json"],
    )

    assert result.exit_code == 7
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["category"] == "validation"
    post.assert_not_called()


def test_json_execution_requires_confirmation(tmp_path, boundaries, monkeypatch: pytest.MonkeyPatch):
    import observal_cli.main as main

    post, _get, _load = boundaries
    path = _write_components(
        tmp_path,
        [{"type": "skill", "name": "review", "description": "Review", "task_type": "general"}],
    )
    monkeypatch.setattr(main, "_migrate_legacy_mcp_configs", lambda: None)
    monkeypatch.setattr(main, "_try_lockfile_migration", lambda: None)

    result = runner.invoke(
        main.app,
        ["registry", "bulk", "submit", "--from-file", str(path), "--output", "json"],
    )

    assert result.exit_code == 7
    assert result.stdout == ""
    error = json.loads(result.stderr)["error"]
    assert error["category"] == "validation"
    assert "--yes" in error["message"]
    post.assert_not_called()
