# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Component Insights CLI calls the owner-authorized component endpoints."""

import json

import pytest
from typer.testing import CliRunner

from observal_cli.cmd_insights import insights_app

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
