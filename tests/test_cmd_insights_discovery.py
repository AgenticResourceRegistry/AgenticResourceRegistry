# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The CLI asks for ARD discovery suggestions and shows them, for Agent and component reports alike."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from observal_cli.cmd_insights import insights_app

runner = CliRunner()
COMPONENT_ID = "11111111-1111-4111-8111-111111111111"
AGENT_ID = "c6185803-8c32-4c39-b347-78f8281e306e"

ASSESSED = {
    "version": 1,
    "state": "assessed",
    "listing_version": "1.0.0",
    "metadata_version_scope": "current_listing",
    "suggestions": [
        {
            "field": "description",
            "illustrative_query": "review login and JWT code",
            "suggested_description": "Reviews login, session and JWT handling code for missing checks.",
            "rationale": "The published excerpt names these checks.",
            "evidence_keys": [],
            "listing_evidence_keys": ["published_skill_excerpt"],
            "relevance_before": 26,
            "relevance_after": 74,
        }
    ],
}


def _show(monkeypatch, report: dict, *args: str):
    monkeypatch.setattr("observal_cli.cmd_insights.client.resolve_registry_reference", lambda *_: COMPONENT_ID)
    monkeypatch.setattr("observal_cli.cmd_insights._resolve_report_for_show", lambda *_: report)
    return runner.invoke(insights_app, ["show", *args])


def _component_report(kind: str, discovery: dict | None) -> dict:
    narrative = {"summary": "Confirmed evidence only", "outcomes": {"state": "insufficient"}, "attention": []}
    if discovery is not None:
        narrative["discovery"] = discovery
    return {
        "id": "report-1",
        "subject_type": "component",
        "component_type": kind,
        "component_name": "team/probe",
        "status": "completed",
        "narrative": narrative,
        "metrics": {"present_sessions": 2},
        "coverage": {"presence": {}, "attribution_state": "observed", "reasons": [], "limitations": []},
    }


def test_discovery_is_opt_in_and_reaches_the_component_request(monkeypatch):
    monkeypatch.setattr("observal_cli.cmd_insights.client.resolve_registry_reference", lambda *_: COMPONENT_ID)
    bodies = []
    monkeypatch.setattr("observal_cli.cmd_insights.client.post", lambda _path, body: bodies.append(body) or {"id": "r"})
    for extra in ([], ["--discovery"]):
        result = runner.invoke(
            insights_app, ["generate", "--component", "skill", "team/probe", "--output", "json", *extra]
        )
        assert result.exit_code == 0, result.output
    assert bodies == [{"period_days": 14}, {"period_days": 14, "include_discovery_optimization": True}]


def test_discovery_reaches_the_agent_request(monkeypatch):
    def get(path, params=None):
        if path == "/api/v1/insights/status":
            return {"available": True}
        return {"id": AGENT_ID, "name": "ultra-pi"}

    bodies = []
    monkeypatch.setattr("observal_cli.config.resolve_alias", lambda value: value)
    monkeypatch.setattr("observal_cli.cmd_insights.client.get", get)
    monkeypatch.setattr("observal_cli.cmd_insights.client.post", lambda _path, body: bodies.append(body) or {"id": "r"})
    result = runner.invoke(insights_app, ["generate", "ultra-pi", "--discovery", "--output", "json"])
    assert result.exit_code == 0, result.output
    assert bodies == [{"period_days": 14, "include_discovery_optimization": True}]


@pytest.mark.parametrize("kind", ["mcp", "skill", "hook"])
def test_every_component_report_shows_the_suggestions_with_their_limits(monkeypatch, kind):
    result = _show(monkeypatch, _component_report(kind, ASSESSED), "--component", kind, "team/probe")
    assert result.exit_code == 0, result.output
    out = " ".join(result.output.split())
    assert "ARD discovery suggestions" in out
    assert "Example search: review login and JWT code" in out
    assert "Suggested description: Reviews login, session and JWT handling code for missing checks." in out
    assert "26 → 74 (not a search position)" in out
    assert "Illustrative searches, not recorded queries" in out and "no ranking is guaranteed" in out


@pytest.mark.parametrize(
    ("discovery", "expected"),
    [
        ({"version": 1, "state": "unavailable"}, "could not be written"),
        ({"version": 1, "state": "unknown"}, "No suggestion passed the accuracy checks"),
        ({"version": 1, "state": "assessed", "suggestions": []}, "No supported description edits were identified"),
    ],
)
def test_each_state_says_what_happened_without_inventing_suggestions(monkeypatch, discovery, expected):
    result = _show(monkeypatch, _component_report("skill", discovery), "--component", "skill", "team/probe")
    assert result.exit_code == 0, result.output
    assert expected in " ".join(result.output.split())
    assert "Example search" not in result.output


def test_an_unrequested_report_does_not_mention_discovery(monkeypatch):
    result = _show(monkeypatch, _component_report("skill", None), "--component", "skill", "team/probe")
    assert result.exit_code == 0, result.output
    assert "discovery" not in result.output.lower()


def test_model_text_cannot_inject_terminal_markup(monkeypatch):
    hostile = {
        **ASSESSED,
        "suggestions": [
            {**ASSESSED["suggestions"][0], "rationale": "[bold red]boom[/bold red] [link=http://x]y[/link]"}
        ],
    }
    result = _show(monkeypatch, _component_report("skill", hostile), "--component", "skill", "team/probe")
    assert result.exit_code == 0, result.output
    assert "[bold red]boom[/bold red]" in result.output


def test_one_component_section_and_json_carry_the_same_data(monkeypatch):
    report = _component_report("skill", ASSESSED)
    as_text = _show(monkeypatch, report, "--component", "skill", "team/probe", "--section", "discovery")
    assert as_text.exit_code == 0, as_text.output
    assert "Example search: review login and JWT code" in " ".join(as_text.output.split())
    as_json = _show(monkeypatch, report, "--component", "skill", "team/probe", "--section", "discovery", "-o", "json")
    assert json.loads(as_json.output)["data"] == ASSESSED


def test_agent_report_shows_the_section_in_its_place(monkeypatch):
    report = {
        "id": "report-1",
        "status": "completed",
        "agent_version": "1.0.0",
        "period_start": "2026-10-01",
        "period_end": "2026-10-14",
        "sessions_analyzed": 5,
        "llm_model_used": "test/model",
        "metrics": {},
        "narrative": {"discovery": ASSESSED},
    }
    monkeypatch.setattr("observal_cli.config.resolve_alias", lambda value: value)
    monkeypatch.setattr("observal_cli.cmd_insights._resolve_report_for_show", lambda *_: report)
    result = runner.invoke(insights_app, ["show", "ultra-pi"])
    assert result.exit_code == 0, result.output
    assert "Example search: review login and JWT code" in " ".join(result.output.split())


def _agent_report(narrative: dict, *, requested: bool) -> dict:
    return {
        "id": "report-1",
        "status": "completed",
        "agent_version": "1.0.0",
        "period_start": "2026-10-01",
        "period_end": "2026-10-14",
        "sessions_analyzed": 5,
        "llm_model_used": "test/model",
        "metrics": {},
        "narrative": narrative,
        "discovery_optimization_requested": requested,
    }


def test_agent_report_puts_the_section_in_a_panel_like_the_others(monkeypatch):
    panels = []

    class RecordingPanel:
        def __init__(self, content, title="", **_):
            panels.append((title, content))

        def __rich__(self):
            return ""

    monkeypatch.setattr("observal_cli.cmd_insights.Panel", RecordingPanel)
    monkeypatch.setattr("observal_cli.config.resolve_alias", lambda value: value)
    monkeypatch.setattr(
        "observal_cli.cmd_insights._resolve_report_for_show",
        lambda *_: _agent_report({"discovery": ASSESSED}, requested=True),
    )
    result = runner.invoke(insights_app, ["show", "ultra-pi"])
    assert result.exit_code == 0, result.output
    assert any("ARD discovery suggestions" in title and "Example search" in content for title, content in panels), (
        panels
    )


@pytest.mark.parametrize("output", ["table", "json"])
@pytest.mark.parametrize("subject", ["agent", "component"])
def test_asking_for_discovery_on_an_unrequested_report_says_why(monkeypatch, subject, output):
    if subject == "agent":
        report = _agent_report({"suggestions": {}}, requested=False)
        args = ["ultra-pi"]
        monkeypatch.setattr("observal_cli.config.resolve_alias", lambda value: value)
    else:
        report = {**_component_report("skill", None), "discovery_optimization_requested": False}
        args = ["--component", "skill", "team/probe"]
    result = _show(monkeypatch, report, *args, "--section", "discovery", "-o", output)
    assert result.exit_code != 0
    assert "generated without ARD discovery suggestions" in " ".join(result.output.split())
    assert "Unknown" not in result.output


def test_the_unrequested_section_error_names_the_flag_and_spares_requested_reports():
    from observal_cli.cmd_insights import _fail_if_discovery_not_requested
    from observal_cli.errors import CliError

    with pytest.raises(CliError) as failure:
        _fail_if_discovery_not_requested({"narrative": {}, "discovery_optimization_requested": False}, "discovery")
    assert "--discovery" in failure.value.remediation
    # Requested, or already holding the section, or asking for something else: no early failure.
    _fail_if_discovery_not_requested({"narrative": {}, "discovery_optimization_requested": True}, "discovery")
    _fail_if_discovery_not_requested({"narrative": {"discovery": ASSESSED}}, "discovery")
    _fail_if_discovery_not_requested({"narrative": {}}, "suggestions")
