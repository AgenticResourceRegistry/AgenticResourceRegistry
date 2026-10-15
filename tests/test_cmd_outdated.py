# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Contract tests for the outdated command."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
import typer
from click import unstyle
from typer.testing import CliRunner

from observal_cli import cmd_outdated, installed_updates
from observal_cli.cmd_outdated import register_outdated
from observal_cli.errors import CliError, ErrorCategory, ErrorHandlingGroup, ExitCode

_AGENT_ID = "11111111-1111-4111-8111-111111111111"
_MCP_ID = "22222222-2222-4222-8222-222222222222"


@pytest.fixture()
def cli() -> typer.Typer:
    app = typer.Typer(name="observal", cls=ErrorHandlingGroup)

    @app.callback()
    def root() -> None:
        pass

    register_outdated(app)
    return app


def _agent(*, version: str = "1.0.0", name: str = "reviewer") -> dict:
    return {
        "entry_type": "agent",
        "id": _AGENT_ID,
        "name": name,
        "namespace": "acme",
        "slug": "reviewer",
        "qualified_name": "acme/reviewer",
        "version": version,
        "harness": "claude-code",
    }


def _mcp(*, version: str = "1.0.0") -> dict:
    return {
        "entry_type": "standalone",
        "type": "mcp",
        "id": _MCP_ID,
        "name": "filesystem",
        "namespace": "acme",
        "slug": "filesystem",
        "qualified_name": "acme/filesystem",
        "version": version,
        "harness": "pi",
    }


def _set_entries(monkeypatch: pytest.MonkeyPatch, entries: list[dict]) -> None:
    from observal_cli import lockfile

    monkeypatch.setattr(lockfile, "get_all_entries", lambda harness=None: entries)


def _error(category: ErrorCategory, *, request_id: str | None = None) -> CliError:
    return CliError(
        category,
        "Registry request failed.",
        operation="Check installed versions",
        resource="agent acme/reviewer",
        remediation="Retry the request.",
        request_id=request_id,
        http_status=404 if category is ErrorCategory.NOT_FOUND else None,
    )


def test_help_has_canonical_examples(cli: typer.Typer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "120")
    result = CliRunner().invoke(cli, ["outdated", "--help"])
    output = unstyle(result.output)

    assert result.exit_code == 0
    assert "observal outdated" in output
    assert "observal outdated --harness claude-code" in output
    assert "observal outdated --output json --no-report" in output


def test_invalid_output_mode_is_a_usage_error(cli: typer.Typer) -> None:
    result = CliRunner().invoke(cli, ["outdated", "--output", "yaml"])

    assert result.exit_code == ExitCode.USAGE


def test_empty_results_have_stable_table_and_json_output(
    cli: typer.Typer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_entries(monkeypatch, [])
    get = MagicMock()
    post = MagicMock()
    monkeypatch.setattr(cmd_outdated.client, "get", get)
    monkeypatch.setattr(cmd_outdated.client, "post", post)

    table = CliRunner().invoke(cli, ["outdated"])
    structured = CliRunner().invoke(cli, ["outdated", "--output", "json"])

    assert table.exit_code == 0
    assert "No installed agents or standalone components" in table.output
    assert json.loads(structured.stdout) == {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 0,
        "summary": {"total": 0, "outdated": 0, "current": 0, "missing": 0, "unknown": 0},
        "report": {
            "requested": True,
            "attempted": False,
            "succeeded": None,
            "created": 0,
            "superseded": 0,
            "error": None,
        },
    }
    get.assert_not_called()
    post.assert_not_called()


@pytest.mark.parametrize(
    "registries",
    [
        {},
        {
            "https://other.example.test": {
                "server_url": "https://other.example.test",
                "harnesses": {"kiro": {"agents": [{"id": "other"}], "standalone": []}},
            }
        },
    ],
)
def test_outdated_ignores_empty_or_inactive_registries(
    registries: dict,
    cli: typer.Typer,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from observal_cli import config, lockfile

    path = tmp_path / "lockfile.json"
    path.write_text(json.dumps({"lock_version": 2, "registries": registries}))
    monkeypatch.setattr(lockfile, "LOCKFILE_PATH", path)
    monkeypatch.setattr(config, "load", lambda: {"server_url": "https://active.example.test"})
    get = MagicMock()
    post = MagicMock()
    monkeypatch.setattr(cmd_outdated.client, "get", get)
    monkeypatch.setattr(cmd_outdated.client, "post", post)

    result = CliRunner().invoke(cli, ["outdated", "--output", "json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["items"] == []
    get.assert_not_called()
    post.assert_not_called()


def test_json_reports_current_outdated_and_inbox_state(
    cli: typer.Typer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_entries(monkeypatch, [_agent(), _mcp()])

    def get(path: str, **_kwargs):
        if path == f"/api/v1/agents/{_AGENT_ID}":
            return {"latest_approved_version": "2.0.0", "namespace": "acme", "slug": "reviewer"}
        return {"version": "1.0.0", "namespace": "acme", "slug": "filesystem"}

    post = MagicMock(return_value={"created": 1, "superseded": 2})
    monkeypatch.setattr(cmd_outdated.client, "get", get)
    monkeypatch.setattr(cmd_outdated.client, "post", post)

    result = CliRunner().invoke(cli, ["outdated", "--output", "json"])

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert [item["status"] for item in payload["items"]] == ["outdated", "current"]
    assert payload["items"][0]["upgrade_command"] == (
        "observal agent pull acme/reviewer --harness claude-code --no-prompt --upgrade"
    )
    assert payload["items"][1]["upgrade_command"] is None
    assert payload["summary"] == {"total": 2, "outdated": 1, "current": 1, "missing": 0, "unknown": 0}
    assert payload["report"] == {
        "requested": True,
        "attempted": True,
        "succeeded": True,
        "created": 1,
        "superseded": 2,
        "error": None,
    }
    reported = post.call_args.args[1]["items"]
    assert reported == [
        {
            "type": "agent",
            "component_id": _AGENT_ID,
            "name": "reviewer",
            "namespace": "acme",
            "slug": "reviewer",
            "current_version": "1.0.0",
            "latest_version": "2.0.0",
            "harness": "claude-code",
        }
    ]


def test_table_lists_every_status_and_escapes_registry_names(
    cli: typer.Typer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unsafe_agent = _agent(name="[bold]reviewer[/bold]")
    unsafe_agent.update({"qualified_name": "[bold]reviewer[/bold]", "namespace": None, "slug": None})
    _set_entries(monkeypatch, [unsafe_agent, _mcp()])

    def get(path: str, **_kwargs):
        if path.startswith("/api/v1/agents"):
            return {"latest_approved_version": "2.0.0"}
        raise _error(ErrorCategory.NOT_FOUND)

    monkeypatch.setattr(cmd_outdated.client, "get", get)
    monkeypatch.setattr(cmd_outdated.client, "post", lambda *_args, **_kwargs: {"created": 0, "superseded": 0})

    result = CliRunner().invoke(cli, ["outdated"])

    assert result.exit_code == 0, result.output
    assert "[bold]reviewer[/bold]" in result.output
    assert "acme/filesystem" in result.output
    assert "outdated" in result.output
    assert "missing" in result.output
    assert "All installed items are up to date" not in result.output
    assert "observal agent pull" in result.output


def test_not_found_is_an_item_status_with_request_context(
    cli: typer.Typer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_entries(monkeypatch, [_agent()])
    monkeypatch.setattr(
        cmd_outdated.client,
        "get",
        MagicMock(side_effect=_error(ErrorCategory.NOT_FOUND, request_id="request-123")),
    )

    result = CliRunner().invoke(cli, ["outdated", "--output", "json", "--no-report"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["items"][0]["status"] == "missing"
    assert payload["items"][0]["error"]["category"] == "not_found"
    assert payload["items"][0]["error"]["request_id"] == "request-123"
    assert payload["summary"]["missing"] == 1


@pytest.mark.parametrize(
    ("category", "exit_code"),
    [
        (ErrorCategory.AUTH, ExitCode.AUTH),
        (ErrorCategory.PERMISSION, ExitCode.PERMISSION),
        (ErrorCategory.RATE_LIMIT, ExitCode.RATE_LIMIT),
        (ErrorCategory.UNAVAILABLE, ExitCode.UNAVAILABLE),
        (ErrorCategory.VERSION, ExitCode.VERSION),
    ],
)
def test_registry_failures_keep_their_category_and_exit_code(
    category: ErrorCategory,
    exit_code: ExitCode,
    cli: typer.Typer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_entries(monkeypatch, [_agent()])
    monkeypatch.setattr(cmd_outdated.client, "get", MagicMock(side_effect=_error(category)))

    result = CliRunner().invoke(cli, ["outdated", "--output", "json", "--no-report"])

    assert result.exit_code == exit_code
    assert result.stdout == ""
    payload = json.loads(result.stderr)["error"]
    assert payload["category"] == category.value
    assert payload["operation"] == "Check installed versions"
    assert payload["resource"] == "agent acme/reviewer"
    assert payload["remediation"] == "Retry the request."


def test_invalid_harness_is_a_validation_error(
    cli: typer.Typer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from observal_cli import lockfile

    get_entries = MagicMock()
    monkeypatch.setattr(lockfile, "get_all_entries", get_entries)

    result = CliRunner().invoke(cli, ["outdated", "--harness", "typo", "--output", "json"])

    assert result.exit_code == ExitCode.VALIDATION
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["resource"] == "harness filter"
    get_entries.assert_not_called()


@pytest.mark.parametrize(
    "entry",
    [
        "not-an-object",
        {"entry_type": "agent", "version": "1.0.0"},
        {"entry_type": "agent", "id": _AGENT_ID},
        {"entry_type": "standalone", "type": "prompt", "id": _MCP_ID, "version": "1.0.0"},
        {**_agent(), "harness": "legacy"},
        _agent(version="not a version"),
    ],
)
def test_invalid_lockfile_entries_are_validation_errors(
    entry: object,
    cli: typer.Typer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from observal_cli import lockfile

    monkeypatch.setattr(lockfile, "get_all_entries", lambda harness=None: [entry])

    result = CliRunner().invoke(cli, ["outdated", "--output", "json"])

    assert result.exit_code == ExitCode.VALIDATION
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["category"] == "validation"


def test_nonempty_lockfile_without_active_registry_requires_authentication(
    cli: typer.Typer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from observal_cli import lockfile

    monkeypatch.setattr(lockfile, "get_all_entries", MagicMock(side_effect=ValueError("missing server")))

    result = CliRunner().invoke(cli, ["outdated", "--output", "json"])

    assert result.exit_code == ExitCode.AUTH
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["category"] == "authentication"


def test_malformed_and_unreadable_lockfiles_are_categorized(
    cli: typer.Typer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from observal_cli import lockfile

    monkeypatch.setattr(
        lockfile,
        "get_all_entries",
        MagicMock(side_effect=RuntimeError("malformed lockfile")),
    )
    malformed = CliRunner().invoke(cli, ["outdated", "--output", "json"])

    def unreadable(*_args, **_kwargs):
        try:
            raise PermissionError("denied")
        except PermissionError as error:
            raise RuntimeError("cannot read") from error

    monkeypatch.setattr(lockfile, "get_all_entries", unreadable)
    denied = CliRunner().invoke(cli, ["outdated", "--output", "json"])

    assert malformed.exit_code == ExitCode.VALIDATION
    assert json.loads(malformed.stderr)["error"]["category"] == "validation"
    assert denied.exit_code == ExitCode.PERMISSION
    assert json.loads(denied.stderr)["error"]["category"] == "permission"


def test_invalid_registry_version_is_unavailable(
    cli: typer.Typer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_entries(monkeypatch, [_agent()])
    monkeypatch.setattr(cmd_outdated.client, "get", lambda *_args, **_kwargs: {"version": "not a version"})

    result = CliRunner().invoke(cli, ["outdated", "--output", "json", "--no-report"])

    assert result.exit_code == ExitCode.UNAVAILABLE
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["category"] == "unavailable"


def test_version_comparison_handles_prereleases_and_downgrades() -> None:
    assert installed_updates.version_newer("2.0.0", "1.9.9") is True
    assert installed_updates.version_newer("2.0.0rc1", "2.0.0") is False
    assert installed_updates.version_newer("1.9.9", "2.0.0") is False


def test_report_failure_is_visible_but_does_not_hide_results(
    cli: typer.Typer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_entries(monkeypatch, [_agent()])
    monkeypatch.setattr(
        cmd_outdated.client,
        "get",
        lambda *_args, **_kwargs: {"latest_approved_version": "2.0.0", "namespace": "acme", "slug": "reviewer"},
    )
    monkeypatch.setattr(
        cmd_outdated.client,
        "post",
        MagicMock(side_effect=_error(ErrorCategory.NOT_FOUND)),
    )

    result = CliRunner().invoke(cli, ["outdated", "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["summary"]["outdated"] == 1
    assert payload["report"]["attempted"] is True
    assert payload["report"]["succeeded"] is False
    assert payload["report"]["error"]["category"] == "not_found"


@pytest.mark.parametrize(
    "response",
    [None, [], {"created": "1", "superseded": 0}, {"created": 0, "superseded": -1}],
)
def test_malformed_inbox_report_response_is_best_effort(response, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cmd_outdated.client, "post", MagicMock(return_value=response))

    item = _agent() | {"type": "agent", "latest_version": "2.0.0", "current_version": "1.0.0"}
    status = cmd_outdated._report_to_inbox([item])

    assert status["attempted"] is True
    assert status["succeeded"] is False
    assert status["error"]["category"] == "unavailable"


def test_unexpected_report_failure_is_not_suppressed(
    cli: typer.Typer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_entries(monkeypatch, [_agent()])
    monkeypatch.setattr(
        cmd_outdated.client,
        "get",
        lambda *_args, **_kwargs: {"latest_approved_version": "2.0.0", "namespace": "acme", "slug": "reviewer"},
    )
    monkeypatch.setattr(cmd_outdated.client, "post", MagicMock(side_effect=ValueError("broken response")))

    result = CliRunner().invoke(cli, ["outdated", "--output", "json"])

    assert result.exit_code == ExitCode.UNEXPECTED
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["category"] == "unexpected"


def test_no_report_prevents_inbox_write(
    cli: typer.Typer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_entries(monkeypatch, [_agent()])
    monkeypatch.setattr(
        cmd_outdated.client,
        "get",
        lambda *_args, **_kwargs: {"latest_approved_version": "2.0.0", "namespace": "acme", "slug": "reviewer"},
    )
    post = MagicMock()
    monkeypatch.setattr(cmd_outdated.client, "post", post)

    result = CliRunner().invoke(cli, ["outdated", "--output", "json", "--no-report"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["report"] == {
        "requested": False,
        "attempted": False,
        "succeeded": None,
        "created": 0,
        "superseded": 0,
        "error": None,
    }
    post.assert_not_called()


@pytest.mark.parametrize(
    ("item_type", "expected"),
    [
        ("agent", "observal agent pull acme/tool --harness pi --no-prompt --upgrade"),
        ("mcp", "observal registry mcp install acme/tool --harness pi --no-prompt"),
        ("skill", "observal registry skill install acme/tool --harness pi"),
        ("hook", "observal registry hook install acme/tool --harness pi"),
    ],
)
def test_upgrade_commands_are_type_specific(item_type: str, expected: str) -> None:
    assert (
        installed_updates.upgrade_command({"type": item_type, "qualified_name": "acme/tool", "harness": "pi"})
        == expected
    )


def test_unversioned_entry_is_reported_as_unknown_instead_of_aborting(
    cli: typer.Typer, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Older CLIs recorded no version for skills installed without --version.
    skill = {**_mcp(), "type": "skill", "version": None, "qualified_name": "acme/review", "slug": "review"}
    _set_entries(monkeypatch, [skill, _agent()])
    get = MagicMock(side_effect=[{"version": "1.2.0"}, {"latest_approved_version": "1.0.0"}])
    monkeypatch.setattr(cmd_outdated.client, "get", get)

    result = CliRunner().invoke(cli, ["outdated", "--output", "json", "--no-report"])

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    unknown, current = payload["items"]
    assert (unknown["status"], unknown["current_version"], unknown["outdated"]) == ("unknown", None, False)
    assert unknown["upgrade_command"] == "observal registry skill install acme/review --harness pi"
    assert current["status"] == "current"
    assert payload["summary"]["unknown"] == 1


def test_invalid_registry_version_is_unavailable_even_for_an_unversioned_entry(
    cli: typer.Typer, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill = {**_mcp(), "type": "skill", "version": None, "qualified_name": "acme/review", "slug": "review"}
    _set_entries(monkeypatch, [skill])
    monkeypatch.setattr(cmd_outdated.client, "get", lambda *_args, **_kwargs: {"version": "not a version"})

    result = CliRunner().invoke(cli, ["outdated", "--output", "json", "--no-report"])

    assert result.exit_code == ExitCode.UNAVAILABLE
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["category"] == "unavailable"
