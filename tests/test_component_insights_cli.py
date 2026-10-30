# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Component Insights CLI calls the owner-authorized component endpoints."""

import json

import httpx
import pytest
from typer.testing import CliRunner

from observal_cli.client import _handle_error
from observal_cli.cmd_insights import insights_app
from observal_cli.errors import CliError, ErrorCategory

runner = CliRunner()
COMPONENT_ID = "11111111-1111-4111-8111-111111111111"


@pytest.mark.parametrize("kind", ["mcp", "skill", "hook"])
def test_activity_preserves_server_coverage_and_uses_resolved_component(monkeypatch, kind):
    monkeypatch.setattr("observal_cli.cmd_insights.client.resolve_registry_reference", lambda *_: COMPONENT_ID)
    expected = {
        "component": {"qualified_name": "team/probe"},
        "present_sessions": 1,
        "coverage": {"presence": {"legacy_hash_sessions": 9}},
    }
    calls = []

    def get(path, params=None):
        calls.append((path, params))
        return expected

    monkeypatch.setattr("observal_cli.cmd_insights.client.get", get)
    result = runner.invoke(
        insights_app, ["activity", "--component", kind, "team/probe", "--period", "30", "--output", "json"]
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == expected
    assert calls == [(f"/api/v1/components/{kind}/{COMPONENT_ID}/activity/summary", {"period_days": 30})]


def test_sessions_passes_cursor_and_keeps_nullable_evidence(monkeypatch):
    monkeypatch.setattr("observal_cli.cmd_insights.client.resolve_registry_reference", lambda *_: COMPONENT_ID)
    expected = {"sessions": [{"session_id": "session-1", "invocations": None}], "next_cursor": "next-page"}
    calls = []

    def get(path, params=None):
        calls.append((path, params))
        return expected

    monkeypatch.setattr("observal_cli.cmd_insights.client.get", get)
    result = runner.invoke(
        insights_app,
        [
            "sessions",
            "--component",
            "skill",
            "team/probe",
            "--period",
            "20",
            "--limit",
            "10",
            "--cursor",
            "first-page",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == expected
    assert calls == [
        (
            f"/api/v1/components/skill/{COMPONENT_ID}/activity/sessions",
            {"period_days": 20, "limit": 10, "cursor": "first-page"},
        )
    ]


def test_empty_presence_generation_error_preserves_coverage_without_raw_json():
    path = f"/api/v1/insights/components/mcp/{COMPONENT_ID}/generate"
    coverage = {
        "presence": {"eligible_sessions": 63, "legacy_hash_sessions": 60, "snapshot_missing_sessions": 1},
        "reasons": ["no_present_sessions"],
    }
    request = httpx.Request("POST", "https://example.test" + path)
    response = httpx.Response(
        422, json={"detail": {"message": "No verified present sessions", "coverage": coverage}}, request=request
    )
    error = httpx.HTTPStatusError("unprocessable", request=request, response=response)

    with pytest.raises(CliError) as failure:
        _handle_error(error, path)

    assert failure.value.category is ErrorCategory.VALIDATION
    assert failure.value.http_status == 422
    assert failure.value.result == {"coverage": coverage}
    assert "legacy hashes: 60" in failure.value.message
    assert "missing snapshots: 1" in failure.value.message
    assert "{" not in failure.value.message
    assert "Legacy Agent traces alone do not prove" in failure.value.remediation


@pytest.mark.parametrize("kind", ["mcp", "skill", "hook"])
def test_version_is_resolved_and_scopes_activity_sessions_and_generation(monkeypatch, kind):
    monkeypatch.setattr("observal_cli.cmd_insights.client.resolve_registry_reference", lambda *_: COMPONENT_ID)
    version_id = "22222222-2222-4222-8222-222222222222"
    version = "1.2.3-rc.1"
    calls = []

    def get(path, params=None):
        calls.append(("get", path, params))
        if "/versions/" in path:
            return {"id": version_id, "version": version}
        if path.endswith("/sessions"):
            return {"sessions": [], "next_cursor": None}
        return {"coverage": {"presence": {}}, "component": {"qualified_name": "team/probe"}}

    def post(path, body):
        calls.append(("post", path, body))
        return {"id": "report-id", "status": "queued"}

    monkeypatch.setattr("observal_cli.cmd_insights.client.get", get)
    monkeypatch.setattr("observal_cli.cmd_insights.client.post", post)
    for args in (
        ["activity", "--component", kind, "team/probe", "--version", version],
        ["sessions", "--component", kind, "team/probe", "--version", version],
        ["generate", "--component", kind, "team/probe", "--version", version],
    ):
        result = runner.invoke(insights_app, [*args, "--output", "json"])
        assert result.exit_code == 0, result.output

    version_path = f"/api/v1/{kind}s/{COMPONENT_ID}/versions/{version}"
    assert calls == [
        ("get", version_path, None),
        (
            "get",
            f"/api/v1/components/{kind}/{COMPONENT_ID}/activity/summary",
            {"period_days": 14, "component_version_id": version_id},
        ),
        ("get", version_path, None),
        (
            "get",
            f"/api/v1/components/{kind}/{COMPONENT_ID}/activity/sessions",
            {"period_days": 14, "limit": 50, "component_version_id": version_id},
        ),
        ("get", version_path, None),
        (
            "post",
            f"/api/v1/insights/components/{kind}/{COMPONENT_ID}/generate",
            {"period_days": 14, "component_version_id": version_id},
        ),
    ]


def test_invalid_component_version_does_not_request_report(monkeypatch):
    monkeypatch.setattr("observal_cli.cmd_insights.client.resolve_registry_reference", lambda *_: COMPONENT_ID)
    monkeypatch.setattr("observal_cli.cmd_insights.client.post", lambda *_: pytest.fail("should not generate"))
    result = runner.invoke(insights_app, ["generate", "--component", "mcp", "team/probe", "--version", "not/a/version"])
    assert result.exit_code == 7


@pytest.mark.parametrize("kind", ["mcp", "skill", "hook"])
def test_show_component_section_supports_facts_and_rejects_unknown_sections(monkeypatch, kind):
    report = {
        "id": "report-1",
        "subject_type": "component",
        "component_type": kind,
        "status": "completed",
        "narrative": {"summary": "Confirmed evidence only"},
        "metrics": {"present_sessions": 2, "invocations": None},
        "coverage": {"presence": {"legacy_hash_sessions": 9}, "attribution_state": "attribution_not_possible"},
    }
    monkeypatch.setattr("observal_cli.cmd_insights._resolve_report_for_show", lambda *_: report)
    for section, expected in (
        ("summary", "Confirmed evidence only"),
        ("metrics", "present_sessions"),
        ("coverage", "legacy_hash_sessions"),
    ):
        result = runner.invoke(
            insights_app, ["show", "--component", kind, "team/probe", "--section", section, "--output", "json"]
        )
        assert result.exit_code == 0, result.output
        assert expected in json.dumps(json.loads(result.output))
        assert json.loads(result.output)["section"] == section

    table = runner.invoke(insights_app, ["show", "--component", kind, "team/probe", "--section", "metrics"])
    assert table.exit_code == 0, table.output
    assert "not recorded" in table.output
    invalid = runner.invoke(insights_app, ["show", "--component", kind, "team/probe", "--section", "not_a_section"])
    assert invalid.exit_code == 7


@pytest.mark.parametrize("kind", ["mcp", "skill", "hook"])
def test_sessions_table_does_not_turn_unknown_into_zero(monkeypatch, kind):
    monkeypatch.setattr("observal_cli.cmd_insights.client.resolve_registry_reference", lambda *_: COMPONENT_ID)
    rows = [
        {"session_id": "unprocessed", "projection_state": "pending", "source_state": "available", "observed_calls": 0},
        {
            "session_id": "lost-source",
            "projection_state": "complete",
            "source_state": "source_missing",
            "observed_calls": 0,
        },
        {
            "session_id": "measured",
            "projection_state": "complete",
            "source_state": "available",
            "usage_measurable": True,
            "observed_calls": 0,
            "confirmed_loads": 0,
            "invocations": None,
            "runs_with_output": 0,
            "silent_runs": None,
            "eligibility": "eligible",
        },
    ]
    monkeypatch.setattr(
        "observal_cli.cmd_insights.client.get", lambda *_args, **_kwargs: {"sessions": rows, "next_cursor": "next"}
    )
    result = runner.invoke(insights_app, ["sessions", "--component", kind, "team/probe"])
    assert result.exit_code == 0, result.output
    assert result.output.count("not measured") == 2
    if kind == "mcp":
        assert "0 calls" in result.output
    else:
        assert "not recorded" in result.output
    assert "--cursor next" in result.output


def test_mcp_complete_collision_is_unknown_in_sessions_table_but_json_keeps_attributed_count(monkeypatch):
    monkeypatch.setattr("observal_cli.cmd_insights.client.resolve_registry_reference", lambda *_: COMPONENT_ID)
    rows = [
        {
            "session_id": "11111111-1111-4111-8111-111111111111",
            "harness": "codex",
            "projection_state": "complete",
            "source_state": "available",
            "usage_measurable": False,
            "observed_calls": 0,
        },
        {
            "session_id": "22222222-2222-4222-8222-222222222222",
            "harness": "codex",
            "projection_state": "complete",
            "source_state": "available",
            "usage_measurable": True,
            "observed_calls": 0,
        },
    ]
    monkeypatch.setattr(
        "observal_cli.cmd_insights.client.get", lambda *_args, **_kwargs: {"sessions": rows, "next_cursor": None}
    )
    command = ["sessions", "--component", "mcp", "team/probe"]
    table = runner.invoke(insights_app, command)
    assert table.exit_code == 0, table.output
    # Rich may shorten the reason in a narrow terminal, but never replace it with a zero.
    assert "not measured" in table.output
    evidence_cells = [line.split("│")[-2].strip() for line in table.output.splitlines() if "│" in line]
    assert any(cell.startswith("not measured") for cell in evidence_cells)
    assert evidence_cells.count("0 calls") == 1
    json_result = runner.invoke(insights_app, [*command, "--output", "json"])
    assert json_result.exit_code == 0, json_result.output
    assert json.loads(json_result.output)["sessions"] == rows


@pytest.mark.parametrize("category,code", [(ErrorCategory.PERMISSION, 4), (ErrorCategory.UNAVAILABLE, 9)])
def test_activity_does_not_hide_permission_or_service_errors(monkeypatch, category, code):
    monkeypatch.setattr("observal_cli.cmd_insights.client.resolve_registry_reference", lambda *_: COMPONENT_ID)

    def deny(*_args, **_kwargs):
        raise CliError(category, "Unavailable", "Component activity")

    monkeypatch.setattr("observal_cli.cmd_insights.client.get", deny)
    result = runner.invoke(insights_app, ["activity", "--component", "hook", "team/probe", "--output", "json"])
    assert result.exit_code == code
    assert not result.stdout.strip()
