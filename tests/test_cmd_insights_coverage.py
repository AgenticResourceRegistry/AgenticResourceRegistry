# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Deterministic behavioral coverage for the insights CLI boundary."""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest
import typer
from typer.main import get_command
from typer.testing import CliRunner

from observal_cli import cmd_insights as insights

AGENT_ID = "c6185803-8c32-4c39-b347-78f8281e306e"
REPORT_ID = "be5aa083-d84a-49e7-8a35-b37b3e687780"
runner = CliRunner()


@dataclass
class FakePanel:
    content: object
    title: str
    border_style: str
    expand: bool


class FakeTable:
    def __init__(self, **options):
        self.options = options
        self.columns: list[tuple[str, dict]] = []
        self.rows: list[tuple[str, ...]] = []

    def add_column(self, name: str, **options) -> None:
        self.columns.append((name, options))

    def add_row(self, *values: str) -> None:
        self.rows.append(values)


class FakeConsole:
    def __init__(self) -> None:
        self.renderables: list[object] = []

    def print(self, renderable: object) -> None:
        self.renderables.append(renderable)


def _blocked(name: str) -> MagicMock:
    def fail(*args, **kwargs):
        raise AssertionError(f"unmocked boundary: {name}, args={args}, kwargs={kwargs}")

    return MagicMock(side_effect=fail)


def _returns(boundary: MagicMock, value) -> MagicMock:
    boundary.side_effect = None
    boundary.return_value = value
    return boundary


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    prints: list[tuple[tuple[object, ...], dict]] = []
    json_values: list[object] = []
    json_lines: list[object] = []
    spinner_messages: list[str] = []
    tables: list[FakeTable] = []
    console = FakeConsole()

    client_get = _blocked("client.get")
    client_post = _blocked("client.post")
    resolve = _blocked("client.resolve_registry_reference")
    confirm = _blocked("typer.confirm")
    monkeypatch.setattr(insights.client, "get", client_get)
    monkeypatch.setattr(insights.client, "post", client_post)
    monkeypatch.setattr(insights.client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(insights.typer, "confirm", confirm)

    @contextmanager
    def fake_spinner(message: str):
        spinner_messages.append(message)
        yield

    def fake_table(**options) -> FakeTable:
        table = FakeTable(**options)
        tables.append(table)
        return table

    monkeypatch.setattr(insights, "spinner", fake_spinner)
    monkeypatch.setattr(insights, "Table", fake_table)
    monkeypatch.setattr(insights, "Panel", FakePanel)
    monkeypatch.setattr(insights, "console", console)
    monkeypatch.setattr(insights, "rprint", lambda *values, **options: prints.append((values, options)))
    monkeypatch.setattr(insights, "output_json", json_values.append)
    monkeypatch.setattr(insights, "output_json_line", json_lines.append)
    monkeypatch.setattr(insights, "status_badge", lambda status: f"<{status}>")
    monkeypatch.setattr(insights, "relative_time", lambda value: f"relative:{value}")
    monkeypatch.setattr(insights, "_registry_name_cache", None)

    def messages() -> list[str]:
        return [" ".join(str(value) for value in values) for values, _options in prints]

    return SimpleNamespace(
        client_get=client_get,
        client_post=client_post,
        resolve=resolve,
        confirm=confirm,
        console=console,
        json=json_values,
        json_lines=json_lines,
        messages=messages,
        prints=prints,
        spinners=spinner_messages,
        tables=tables,
    )


def _completed_report(**overrides) -> dict:
    report = {
        "id": REPORT_ID,
        "agent_id": AGENT_ID,
        "status": "completed",
        "period_start": "2026-05-17T00:00:00Z",
        "period_end": "2026-05-31T00:00:00Z",
        "sessions_analyzed": 42,
        "llm_model_used": "analysis-model",
        "narrative": {},
    }
    report.update(overrides)
    return report


def test_list_table_resolves_agent_and_renders_exact_rows(cli):
    reports = [
        {
            "id": "report-one",
            "status": "completed",
            "agent_version": "2.0.0",
            "period_start": "2026-05-01T00:00:00Z",
            "period_end": "2026-05-14T00:00:00Z",
            "sessions_analyzed": 12,
            "completed_at": "2026-05-15T00:00:00Z",
        },
        {
            "id": "report-two",
            "status": "pending",
            "period_start": "2026-05-15T00:00:00Z",
            "period_end": "2026-05-28T00:00:00Z",
        },
    ]
    _returns(cli.resolve, "owner/agent")
    cli.client_get.side_effect = [{"id": AGENT_ID}, reports]

    assert insights.insights_list("@agent", "table") is None

    cli.resolve.assert_called_once_with("agent", "@agent")
    assert cli.client_get.call_args_list == [
        call("/api/v1/agents/owner/agent"),
        call(f"/api/v1/agents/{AGENT_ID}/insights/reports"),
    ]
    assert cli.spinners == ["Fetching insight reports..."]
    assert len(cli.tables) == 1
    table = cli.tables[0]
    assert table.options == {"title": "Insight Reports (2)", "show_lines": False, "padding": (0, 1)}
    assert table.columns == [
        ("#", {"style": "dim", "width": 3}),
        ("Status", {}),
        ("Version", {}),
        ("Period", {}),
        ("Sessions", {"justify": "right"}),
        ("Completed", {}),
    ]
    assert table.rows == [
        ("1", "<completed>", "2.0.0", "2026-05-01 → 2026-05-14", "12", "relative:2026-05-15T00:00:00Z"),
        ("2", "<pending>", "-", "2026-05-15 → 2026-05-28", "0", "relative:None"),
    ]
    assert cli.console.renderables == [table]
    assert cli.messages() == [
        "",
        "[dim]Open latest completed: [cyan]observal ops insights show @agent[/cyan][/dim]",
        "[dim]Open row 1: [cyan]observal ops insights show @agent 1[/cyan][/dim]",
    ]


def test_list_json_and_empty_table_have_distinct_output(cli):
    _returns(cli.resolve, "agent")
    cli.client_get.side_effect = [{"id": AGENT_ID}, [{"id": REPORT_ID}], {"id": AGENT_ID}, []]

    insights.insights_list("agent", "json")
    insights.insights_list("agent", "table")

    assert cli.json == [[{"id": REPORT_ID}]]
    assert cli.messages() == ["[dim]No insight reports found.[/dim]"]
    assert cli.tables == []
    assert cli.console.renderables == []
    assert cli.spinners == ["Fetching insight reports..."]


def test_select_report_defaults_to_first_when_none_completed(cli):
    reports = [{"id": "first", "status": "failed"}, {"id": "second", "status": "running"}]

    assert insights._select_report_id(reports, None) == "first"
    assert insights._select_report_id(reports, "latest") == "first"
    assert insights._select_report_id(reports, "2") == "second"
    assert insights._select_report_id(reports, "SEC") == "second"
    assert cli.messages() == []


@pytest.mark.parametrize(
    ("reports", "report_ref", "exit_code", "message"),
    [
        ([], None, 5, "No insight reports were found"),
        ([{"id": "one"}, {"id": "two"}], "3", 7, "row 3 is out of range"),
        ([{"id": "abc-one"}, {"id": "abc-two"}], "abc", 6, "prefix is ambiguous"),
        ([{"id": "abc-one"}], "missing", 5, "report not found"),
    ],
)
def test_select_report_validation_exits_without_side_effects(cli, reports, report_ref, exit_code, message):
    with pytest.raises(typer.Exit) as raised:
        insights._select_report_id(reports, report_ref)

    assert raised.value.exit_code == exit_code
    assert message.lower() in " ".join(cli.messages()).lower()
    cli.client_get.assert_not_called()
    cli.client_post.assert_not_called()
    cli.confirm.assert_not_called()


def test_show_non_completed_report_renders_status_progress_and_error(cli, monkeypatch):
    report = {
        "status": "failed",
        "progress_phase": "writing_sections",
        "progress_percent": 37,
        "progress_message": "Three sections generated",
        "error_message": "Provider rejected the request",
    }
    resolve_report = MagicMock(return_value=report)
    monkeypatch.setattr(insights, "_resolve_report_for_show", resolve_report)

    assert insights.insights_show("agent", "latest", "table", None) is None

    resolve_report.assert_called_once_with("agent", "latest")
    assert cli.spinners == ["Fetching report..."]
    assert cli.messages() == [
        "  Status: <failed>",
        "  Phase: [cyan]writing sections[/cyan] (37%)",
        "  [dim]Three sections generated[/dim]",
        "  [red]Error:[/red] Provider rejected the request",
    ]
    assert cli.console.renderables == []


def test_show_json_section_returns_only_requested_data(cli, monkeypatch):
    report = _completed_report(narrative={"suggestions": {"features_to_try": []}})
    resolve_report = MagicMock(return_value=report)
    render_section = MagicMock()
    monkeypatch.setattr(insights, "_resolve_report_for_show", resolve_report)
    monkeypatch.setattr(insights, "_render_section", render_section)

    insights.insights_show("agent", "abc", "json", "suggestions")

    assert cli.json == [{"report_id": REPORT_ID, "section": "suggestions", "data": {"features_to_try": []}}]
    assert cli.messages() == []
    render_section.assert_not_called()


def test_show_rejects_unknown_section_with_available_names(cli, monkeypatch):
    report = _completed_report(narrative={"at_a_glance": {}, "suggestions": {}})
    monkeypatch.setattr(insights, "_resolve_report_for_show", MagicMock(return_value=report))

    with pytest.raises(typer.Exit) as raised:
        insights.insights_show("agent", None, "table", "missing")

    assert raised.value.exit_code == 7
    assert cli.messages() == ["[red]Unknown insight report section: missing.[/red]"]


def test_show_completed_header_orders_sections_and_registry_note(cli, monkeypatch):
    registry_match = {"enabled": True, "offered": 2, "reused": 0}
    report = _completed_report(
        agent_version="2.1.0",
        comparison_agent_version="2.0.0",
        metrics={"rich": {"cache_hit_rate_pct": 75, "cache_tokens_saved": 900}},
        narrative={
            "suggestions": {"features_to_try": []},
            "at_a_glance": {"health": "healthy"},
            "registry_match": registry_match,
            "unrecognized": {"narrative": "not rendered"},
        },
    )
    render_section = MagicMock()
    render_note = MagicMock()
    monkeypatch.setattr(insights, "_resolve_report_for_show", MagicMock(return_value=report))
    monkeypatch.setattr(insights, "_render_section", render_section)
    monkeypatch.setattr(insights, "_render_registry_match_note", render_note)

    insights.insights_show("agent", None, "table", None)

    assert cli.messages() == [
        "",
        "  [bold]Insight Report[/bold]  v2.1.0  2026-05-17 → 2026-05-31",
        "  Sessions: 42  Model: analysis-model  Compared to: v2.0.0",
        "  Cache: 75% hit rate, 900 tokens saved",
        "",
    ]
    assert render_section.call_args_list == [
        call("at_a_glance", {"health": "healthy"}),
        call("suggestions", {"features_to_try": []}),
    ]
    render_note.assert_called_once_with(registry_match)


def test_section_renderers_cover_optional_report_content(cli):
    insights._render_section("empty", None)
    assert cli.console.renderables == []

    insights._render_section("legacy_section", {"narrative": "Legacy narrative"})
    insights._render_at_a_glance(
        "At a Glance",
        {
            "health": "unrated",
            "whats_working": "Fast edits",
            "whats_hindering": "Retries",
            "quick_win": "Cache reads",
            "ambitious_workflows": "Release automation",
        },
    )
    before_empty_area = len(cli.console.renderables)
    insights._render_what_they_work_on("Work", {"areas": []})
    assert len(cli.console.renderables) == before_empty_area
    insights._render_usage_patterns(
        "Usage",
        {
            "narrative": "Steady use",
            "session_profile": {"avg_duration_minutes": 8, "avg_tool_calls": 4, "avg_prompts": 3},
            "tool_distribution": [
                {"tool": "Read", "calls": 5, "error_rate": 0.0},
                {"tool": "Edit", "calls": 4, "error_rate": 0.05},
                {"tool": "Bash", "calls": 3, "error_rate": 0.2},
            ],
        },
    )
    insights._render_friction(
        "Friction",
        {
            "intro": "Some friction",
            "categories": [
                {
                    "title": "Retries",
                    "severity": "unexpected",
                    "description": "Commands repeat",
                    "examples": ["retry one"],
                    "impact": "Longer sessions",
                }
            ],
        },
    )
    insights._render_suggestions(
        "Suggestions",
        {
            "config_additions": [{"addition": "Run checks", "why": "Reliability", "where": "prompt"}],
            "features_to_try": [
                {
                    "feature": "Skill",
                    "name": "review",
                    "one_liner": "Review changes",
                    "why_for_you": "Many edits",
                }
            ],
            "usage_patterns": [
                {
                    "title": "Verify",
                    "suggestion": "Run tests",
                    "detail": "Before completion",
                    "copyable_prompt": "Run the tests",
                }
            ],
        },
    )
    insights._render_cost(
        "Cost",
        {
            "summary": "Costs are stable",
            "metrics": {"total_cost_usd": 1.25, "cost_per_session": 0.125, "cache_efficiency_pct": 80},
            "opportunities": [{"title": "Cache", "description": "Reuse context", "estimated_savings": "$3 monthly"}],
        },
    )
    insights._render_regression("Regression", {"has_previous_data": False})
    insights._render_regression(
        "Regression",
        {
            "has_previous_data": True,
            "summary": "Mixed changes",
            "changes": [
                {
                    "metric": "errors",
                    "direction": "degraded",
                    "previous_value": 1,
                    "current_value": 2,
                    "significance": "high",
                },
                {"metric": "latency", "direction": "unexpected", "previous_value": 4, "current_value": 4},
            ],
        },
    )
    insights._render_horizon(
        "Horizon",
        {
            "intro": "Next steps",
            "opportunities": [{"title": "Automate", "whats_possible": "More", "how_to_try": "Pilot"}],
        },
    )
    insights._render_version_comparison(
        "Versions",
        {
            "summary": "Version improved",
            "confidence": "high",
            "changes": [
                {
                    "metric": "success",
                    "direction": "up",
                    "prior_value": 70,
                    "current_value": 90,
                    "attribution": "Prompt update",
                    "evidence": "Ten sessions",
                }
            ],
        },
    )

    panel_text = "\n".join(
        str(renderable.content) for renderable in cli.console.renderables if isinstance(renderable, FakePanel)
    )
    for expected in (
        "Legacy narrative",
        "Retries",
        "Release automation",
        "Avg duration",
        "Some friction",
        "Longer sessions",
        "Costs are stable",
        "$3 monthly",
        "Mixed changes",
        "Next steps",
        "Confidence: high",
        "Attribution: Prompt update",
    ):
        assert expected in panel_text
    assert any(isinstance(renderable, FakeTable) for renderable in cli.console.renderables)
    assert "No previous data for comparison" in "\n".join(cli.messages())
    assert insights._reuse_ref("not a mapping") is None


def test_reuse_renderer_falls_back_to_personal_reason_and_default_type(cli, monkeypatch):
    monkeypatch.setattr(insights, "_registry_name_cache", "Acme")

    insights._render_reuse_feature(
        {"one_liner": "Review every diff", "why_for_you": "Frequent reviews"},
        {"id": "skill-id", "name": "review-skill"},
    )

    assert cli.messages() == [
        "    [green]✔ ALREADY IN ACME[/green]  [dim](skill)[/dim]",
        "      [bold]review-skill[/bold]",
        "      Review every diff",
        "      [dim]Frequent reviews[/dim]",
        "      [cyan]observal registry skill show review-skill --output json[/cyan]",
        "      [dim]Attach to an agent: observal agent add skill skill-id[/dim]",
    ]
    cli.client_get.assert_not_called()


def test_generate_posts_exact_period_versions_and_renders_summary(cli):
    _returns(cli.resolve, "owner/agent")
    cli.client_get.side_effect = [{"available": True}, {"id": AGENT_ID}]
    response = {
        "id": REPORT_ID,
        "status": "pending",
        "agent_version": "2.0.0",
        "comparison_agent_version": "1.9.0",
        "period_start": "2026-05-01T00:00:00Z",
        "period_end": "2026-05-31T00:00:00Z",
        "progress_phase": "queued_for_worker",
        "progress_percent": 5,
    }
    _returns(cli.client_post, response)

    assert insights.insights_generate("@agent", 30, "2.0.0", "1.9.0", "table", False) is None

    assert cli.client_get.call_args_list == [
        call("/api/v1/insights/status"),
        call("/api/v1/agents/owner/agent"),
    ]
    cli.resolve.assert_called_once_with("agent", "@agent")
    cli.client_post.assert_called_once_with(
        f"/api/v1/agents/{AGENT_ID}/insights/reports",
        {"period_days": 30, "agent_version": "2.0.0", "comparison_agent_version": "1.9.0"},
    )
    assert cli.spinners == ["Checking insights configuration...", "Generating insight report..."]
    assert cli.messages() == [
        "[green]✓ Report queued[/green] (status: <pending>)",
        f"  ID: [dim]{REPORT_ID}[/dim]",
        "  Version: v2.0.0",
        "  Compare: v1.9.0",
        "  Period: 2026-05-01 → 2026-05-31",
        "  Phase: queued for worker (5%)",
        "[dim]  Run `observal ops insights show <agent>` when complete.[/dim]",
    ]


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ({"available": False, "reason": "No provider credentials"}, "No provider credentials"),
        ({}, "Insights is not configured."),
    ],
)
def test_generate_unavailable_exits_before_agent_resolution_or_post(cli, status, reason):
    _returns(cli.client_get, status)

    with pytest.raises(typer.Exit) as raised:
        insights.insights_generate("agent", 14, None, None, "table", False)

    assert raised.value.exit_code == 9
    cli.client_get.assert_called_once_with("/api/v1/insights/status")
    cli.resolve.assert_not_called()
    cli.client_post.assert_not_called()
    cli.confirm.assert_not_called()
    assert cli.messages() == [f"[red]Insights is unavailable: {reason}[/red]"]


def test_generate_json_wait_emits_json_lines_until_completion(cli, monkeypatch):
    report = {"id": REPORT_ID, "status": "pending"}
    cli.client_get.side_effect = [
        {"available": True},
        {"id": REPORT_ID, "status": "running", "progress_percent": 50},
        {"id": REPORT_ID, "status": "completed", "progress_percent": 100},
    ]
    _returns(cli.client_post, report)
    resolve_agent = MagicMock(return_value=AGENT_ID)
    sleep = MagicMock()
    monkeypatch.setattr(insights, "_resolve_agent_id", resolve_agent)
    monkeypatch.setattr(time, "sleep", sleep)

    insights.insights_generate("agent", 7, None, None, "json", True)

    resolve_agent.assert_called_once_with("agent")
    cli.client_post.assert_called_once_with(
        f"/api/v1/agents/{AGENT_ID}/insights/reports",
        {"period_days": 7},
    )
    poll_path = f"/api/v1/agents/{AGENT_ID}/insights/reports/{REPORT_ID}"
    assert cli.client_get.call_args_list == [
        call("/api/v1/insights/status"),
        call(poll_path),
        call(poll_path),
    ]
    assert cli.json == []
    assert cli.json_lines == [
        {"event": "queued", "report": report},
        {"event": "progress", "report": {"id": REPORT_ID, "status": "running", "progress_percent": 50}},
        {"event": "progress", "report": {"id": REPORT_ID, "status": "completed", "progress_percent": 100}},
    ]
    assert cli.messages() == []
    sleep.assert_called_once_with(3)


@pytest.mark.parametrize("terminal_status", ["completed", "failed"])
def test_generate_wait_polls_progress_until_terminal_status(cli, monkeypatch, terminal_status):
    initial = {
        "id": REPORT_ID,
        "status": "pending",
        "period_start": "2026-05-01T00:00:00Z",
        "period_end": "2026-05-14T00:00:00Z",
    }
    terminal = {
        **initial,
        "status": terminal_status,
        "agent_version": "2.0.0",
        "progress_phase": "writing_report",
        "progress_percent": 100,
    }
    cli.client_get.side_effect = [
        {"available": True},
        {"status": "running", "progress_phase": "collecting_data", "progress_percent": 20},
        {"progress_percent": 40},
        terminal,
    ]
    _returns(cli.client_post, initial)
    monkeypatch.setattr(insights, "_resolve_agent_id", MagicMock(return_value=AGENT_ID))
    sleep = MagicMock()
    monkeypatch.setattr(time, "sleep", sleep)

    if terminal_status == "failed":
        with pytest.raises(typer.Exit) as raised:
            insights.insights_generate("agent", 14, None, None, "table", True)
        assert raised.value.exit_code == 9
    else:
        insights.insights_generate("agent", 14, None, None, "table", True)

    poll_path = f"/api/v1/agents/{AGENT_ID}/insights/reports/{REPORT_ID}"
    assert cli.client_get.call_args_list == [
        call("/api/v1/insights/status"),
        call(poll_path),
        call(poll_path),
        call(poll_path),
    ]
    assert sleep.call_args_list == [call(3), call(3)]
    assert cli.prints[:4] == [
        (("\r  <running> collecting data (20%)",), {"end": ""}),
        (("\r  <pending> queued (40%)",), {"end": ""}),
        ((f"\r  <{terminal_status}> writing report (100%)",), {"end": ""}),
        ((), {}),
    ]
    if terminal_status == "failed":
        assert cli.messages()[4:] == ["[red]Insight report generation failed.[/red]"]
    else:
        assert cli.messages()[4:] == [
            "[green]✓ Report queued[/green] (status: <completed>)",
            f"  ID: [dim]{REPORT_ID}[/dim]",
            "  Version: v2.0.0",
            "  Period: 2026-05-01 → 2026-05-14",
            "  Phase: writing report (100%)",
            "[dim]  Run `observal ops insights show <agent>` when complete.[/dim]",
        ]


def test_generate_wait_exhaustion_is_categorized(cli, monkeypatch):
    initial = {
        "id": REPORT_ID,
        "status": "pending",
        "period_start": "2026-05-01T00:00:00Z",
        "period_end": "2026-05-14T00:00:00Z",
    }
    poll_path = f"/api/v1/agents/{AGENT_ID}/insights/reports/{REPORT_ID}"

    def get(path: str):
        if path == "/api/v1/insights/status":
            return {"available": True}
        if path == poll_path:
            return {"status": "running", "progress_phase": "generating", "progress_percent": 50}
        raise AssertionError(f"unexpected path: {path}")

    cli.client_get.side_effect = get
    _returns(cli.client_post, initial)
    monkeypatch.setattr(insights, "_resolve_agent_id", MagicMock(return_value=AGENT_ID))
    sleep = MagicMock()
    monkeypatch.setattr(time, "sleep", sleep)

    with pytest.raises(typer.Exit) as raised:
        insights.insights_generate("agent", 14, None, None, "table", True)

    assert raised.value.exit_code == 9
    assert cli.client_get.call_args_list == [call("/api/v1/insights/status"), *[call(poll_path)] * 120]
    assert sleep.call_args_list == [call(3)] * 120
    assert "timed out" in cli.messages()[-1].lower()


def test_generate_wait_keyboard_interrupt_propagates_without_success(cli, monkeypatch):
    initial = {"id": REPORT_ID, "status": "pending"}
    cli.client_get.side_effect = [{"available": True}, {"status": "running", "progress_percent": 10}]
    _returns(cli.client_post, initial)
    monkeypatch.setattr(insights, "_resolve_agent_id", MagicMock(return_value=AGENT_ID))
    sleep = MagicMock(side_effect=KeyboardInterrupt)
    monkeypatch.setattr(time, "sleep", sleep)

    with pytest.raises(KeyboardInterrupt):
        insights.insights_generate("agent", 14, None, None, "table", True)

    sleep.assert_called_once_with(3)
    assert cli.messages() == ["\r  <running> running (10%)"]
    assert not any("Report queued" in message for message in cli.messages())


@pytest.mark.parametrize("command", ["list", "show", "generate"])
def test_client_http_exit_propagates_without_command_success(cli, monkeypatch, command):
    failure = typer.Exit(1)
    if command == "list":
        monkeypatch.setattr(insights, "_resolve_agent_id", MagicMock(return_value=AGENT_ID))
        cli.client_get.side_effect = failure
        invoke = partial(insights.insights_list, "agent", "table")
    elif command == "show":
        monkeypatch.setattr(insights, "_resolve_report_for_show", MagicMock(side_effect=failure))
        invoke = partial(insights.insights_show, "agent", None, "table", None)
    else:
        cli.client_get.side_effect = failure
        invoke = partial(insights.insights_generate, "agent", 14, None, None, "table", False)

    with pytest.raises(typer.Exit) as raised:
        invoke()

    assert raised.value is failure
    assert cli.messages() == []
    assert cli.json == []
    assert cli.console.renderables == []
    cli.client_post.assert_not_called()


def test_malformed_responses_fail_loudly_at_the_owning_boundary(cli, monkeypatch):
    monkeypatch.setattr(insights, "_resolve_agent_id", MagicMock(return_value=AGENT_ID))
    _returns(
        cli.client_get,
        [{"status": "completed", "period_start": None, "period_end": "2026-05-01T00:00:00Z"}],
    )
    insights.insights_list("agent", "table")
    assert len(cli.console.renderables) == 1

    cli.client_get.reset_mock()
    monkeypatch.setattr(
        insights,
        "_resolve_report_for_show",
        MagicMock(return_value=_completed_report(narrative=["invalid"])),
    )
    with pytest.raises(typer.Exit) as raised:
        insights.insights_show("agent", None, "table", None)
    assert raised.value.exit_code == 9

    cli.client_get.reset_mock()
    _returns(cli.client_get, None)
    with pytest.raises(AttributeError):
        insights.insights_generate("agent", 14, None, None, "table", False)
    cli.client_post.assert_not_called()


def test_poll_timeout_error_propagates_without_sleep_or_final_output(cli, monkeypatch):
    failure = TimeoutError("poll timed out")
    cli.client_get.side_effect = [{"available": True}, failure]
    _returns(cli.client_post, {"id": REPORT_ID, "status": "pending"})
    monkeypatch.setattr(insights, "_resolve_agent_id", MagicMock(return_value=AGENT_ID))
    sleep = MagicMock()
    monkeypatch.setattr(time, "sleep", sleep)

    with pytest.raises(TimeoutError) as raised:
        insights.insights_generate("agent", 14, None, None, "table", True)

    assert raised.value is failure
    sleep.assert_not_called()
    assert cli.messages() == []


@pytest.mark.parametrize(
    "arguments",
    [
        ["generate", "agent", "--period", "not-an-integer"],
        ["list"],
        ["show"],
    ],
)
def test_typer_validation_rejects_bad_arguments_before_any_side_effect(cli, arguments):
    result = runner.invoke(insights.insights_app, arguments)

    assert result.exit_code == 2
    cli.client_get.assert_not_called()
    cli.client_post.assert_not_called()
    cli.resolve.assert_not_called()
    cli.confirm.assert_not_called()


def test_insights_app_registers_only_current_commands():
    assert sorted(get_command(insights.insights_app).commands) == ["activity", "generate", "list", "sessions", "show"]


def test_component_cli_list_show_generate_use_owner_scoped_routes(cli):
    _returns(cli.resolve, "component-id")
    _returns(
        cli.client_get,
        [
            {
                "id": REPORT_ID,
                "status": "completed",
                "component_version": None,
                "period_start": "2026-05-01",
                "period_end": "2026-05-14",
            }
        ],
    )
    listed = runner.invoke(insights.insights_app, ["list", "--component", "mcp", "team/tool", "--output", "json"])
    assert listed.exit_code == 0, listed.output
    cli.client_get.assert_called_with("/api/v1/insights/components/mcp/component-id/reports")

    cli.client_get.reset_mock()
    cli.client_get.side_effect = [
        [{"id": REPORT_ID, "status": "completed"}],
        {"id": REPORT_ID, "subject_type": "component", "status": "completed", "coverage": {}},
    ]
    shown = runner.invoke(insights.insights_app, ["show", "--component", "mcp", "team/tool", "--output", "json"])
    assert shown.exit_code == 0, shown.output
    assert cli.client_get.call_args_list[-1] == call(f"/api/v1/insights/reports/{REPORT_ID}")

    cli.client_get.reset_mock()
    cli.client_get.side_effect = [
        [{"id": REPORT_ID, "status": "completed"}],
        {
            "id": REPORT_ID,
            "subject_type": "component",
            "status": "completed",
            "coverage": {},
            "narrative": {
                "summary": "Observed call",
                "component_analysis": {
                    "state": "assessed",
                    "sampled_sessions": 1,
                    "truncated": False,
                    "findings": [
                        {
                            "kind": "workflow",
                            "insight": "Used to find docs",
                            "confidence": "low",
                            "evidence_refs": ["s0-call0"],
                        }
                    ],
                    "evidence": {"s0-call0": "search (result: unknown)"},
                },
            },
        },
    ]
    plain = runner.invoke(insights.insights_app, ["show", "--component", "mcp", "team/tool"])
    assert plain.exit_code == 0, plain.output
    assert "What the published calls suggest" in " ".join(cli.messages())
    assert "search (result: unknown)" in " ".join(cli.messages())

    cli.client_get.reset_mock()
    cli.client_get.side_effect = _blocked("component generate should not check LLM status").side_effect
    _returns(cli.client_post, {"id": REPORT_ID, "status": "pending"})
    queued = runner.invoke(
        insights.insights_app, ["generate", "--component", "mcp", "team/tool", "--period", "14", "--output", "json"]
    )
    assert queued.exit_code == 0, queued.output
    cli.client_get.assert_not_called()
    cli.client_post.assert_called_once_with(
        "/api/v1/insights/components/mcp/component-id/generate", {"period_days": 14}
    )


def test_component_cli_all_pages_and_exact_historical_id(cli):
    _returns(cli.resolve, "component-id")
    page = [{"id": REPORT_ID, "created_at": "2026-05-17T00:00:00Z", "status": "completed"}] * 100
    cli.client_get.side_effect = [page, [{"id": "older", "created_at": "2026-05-16T00:00:00Z"}]]
    listed = runner.invoke(
        insights.insights_app,
        [
            "list",
            "--component",
            "mcp",
            "team/tool",
            "--all",
            "--output",
            "json",
        ],
    )
    assert listed.exit_code == 0, listed.output
    assert len(cli.json[-1]) == 101
    assert "before_created_at=2026-05-17T00%3A00%3A00Z" in cli.client_get.call_args_list[-1].args[0]
    cli.client_get.reset_mock()
    cli.client_get.side_effect = [{"id": REPORT_ID, "component_id": "component-id", "status": "completed"}]
    shown = runner.invoke(
        insights.insights_app,
        [
            "show",
            REPORT_ID,
            "--component",
            "mcp",
            "team/tool",
            "--output",
            "json",
        ],
    )
    assert shown.exit_code == 0, shown.output
    cli.client_get.assert_called_once_with(f"/api/v1/insights/reports/{REPORT_ID}")


def _skill_report(**metrics) -> dict:
    """Shape of a real skill report (services.insights.component_report._generate_skill_content)."""
    base = {
        "present_sessions": 6,
        "loaded_sessions": 2,
        "confirmed_loads": 2,
        "load_attempts": 0,
        "invoked_sessions": 2,
        "invocations": 2,
        "available_sessions": 1,
        "harness_distribution": {"claude-code": 5, "pi": 1},
        "version_distribution": {"unknown": 6},
    }
    return {
        "id": REPORT_ID,
        "subject_type": "component",
        "component_type": "skill",
        "component_name": "team/review",
        "status": "completed",
        "metrics": base | metrics,
        "narrative": {"summary": "The skill's instructions entered context in processed present sessions."},
        "coverage": {
            "attribution_state": "observed",
            "presence": {"present_sessions": 6},
            "projection": {"projection_complete_sessions": 6},
            "evidence": {"collision_facts": 0, "unmatched_facts": 1},
            "usage_rate_denominator_sessions": 6,
            "reasons": ["available_not_recorded_on_some_harnesses", "invoked_not_recorded_on_some_harnesses"],
            "limitations": ["entered_context_not_helped: a load shows context, not help"],
        },
    }


def test_skill_component_reports_use_skill_routes_and_never_mcp_wording(cli):
    _returns(cli.resolve, "skill-id")
    cli.client_get.side_effect = [[{"id": REPORT_ID, "status": "completed"}], _skill_report()]
    shown = runner.invoke(insights.insights_app, ["show", "--component", "skill", "team/review"])
    assert shown.exit_code == 0, shown.output
    assert cli.client_get.call_args_list[0] == call("/api/v1/insights/components/skill/skill-id/reports")
    text = " ".join(cli.messages())
    assert "Sessions with a confirmed load: 2" in text
    assert "Sessions where offered: 1 (only harnesses that record it)" in text
    assert "entered_context_not_helped" in text
    for mcp_only in ("Observed calls", "What the published calls suggest", "collisions,"):
        assert mcp_only not in text

    cli.client_get.reset_mock()
    cli.client_get.side_effect = [
        [{"id": REPORT_ID, "status": "completed"}],
        _skill_report(available_sessions=None, invoked_sessions=None, invocations=None),
    ]
    runner.invoke(insights.insights_app, ["show", "--component", "skill", "team/review"])
    text = " ".join(cli.messages())
    assert "Sessions where offered: not recorded" in text and "Sessions with an invocation: not recorded" in text

    cli.client_get.side_effect = _blocked("skill generate should not check LLM status").side_effect
    _returns(cli.client_post, {"id": REPORT_ID, "status": "pending"})
    queued = runner.invoke(insights.insights_app, ["generate", "--component", "skill", "team/review", "-o", "json"])
    assert queued.exit_code == 0, queued.output
    cli.client_post.assert_called_once_with("/api/v1/insights/components/skill/skill-id/generate", {"period_days": 14})


def test_unsupported_component_reports_are_refused_before_any_request(cli):
    result = runner.invoke(insights.insights_app, ["list", "--component", "prompt", "team/p"])
    assert result.exit_code != 0
    cli.resolve.assert_not_called()
    cli.client_get.assert_not_called()


def _hook_report(**metrics) -> dict:
    """Shape of a real hook report (services.insights.component_report._generate_hook_content)."""
    return {
        "id": REPORT_ID,
        "subject_type": "component",
        "component_type": "hook",
        "component_name": "team/lint",
        "status": "completed",
        "metrics": {
            "present_sessions": 3,
            "eligible_sessions": 1,
            "sessions_with_recorded_run": 1,
            "runs_with_output": 0,
            "failures": 1,
            "blocks": 0,
            "harness_distribution": {"claude-code": 3},
            "version_distribution": {},
        }
        | metrics,
        "narrative": {"summary": "The hook has recorded runs in sessions where it could run."},
        "coverage": {
            "attribution_state": "observed",
            "presence": {"present_sessions": 3},
            "projection": {"projection_complete_sessions": 3},
            "eligibility": {"eligible_sessions": 1, "headless_sessions": 1, "agent_inactive_sessions": 1},
            "evidence": {"collision_runs": 0, "unmatched_runs": 0},
            "usage_rate_denominator_sessions": 1,
            "reasons": ["agent_hook_headless_sessions"],
            "limitations": ["silent_success_unrecorded: recorded runs are a lower bound"],
        },
    }


def test_hook_component_reports_show_lower_bounds_and_sessions_that_could_not_run(cli):
    _returns(cli.resolve, "hook-id")
    cli.client_get.side_effect = [[{"id": REPORT_ID, "status": "completed"}], _hook_report()]
    shown = runner.invoke(insights.insights_app, ["show", "--component", "hook", "team/lint"])
    assert shown.exit_code == 0, shown.output
    assert cli.client_get.call_args_list[0] == call("/api/v1/insights/components/hook/hook-id/reports")
    text = " ".join(cli.messages())
    assert "Sessions with a recorded run: 1 (a lower bound)" in text
    assert "0 with output, 1 failed, 0 blocked" in text
    assert "1 headless (agent hooks do not run), 1 agent not active" in text
    for other in ("Observed calls", "confirmed load", "invocation"):
        assert other not in text


def test_hook_reports_count_silent_runs_where_every_run_is_recorded(cli):
    """Regression: Pi's silent runs were dropped, and every hook report claimed a lower bound."""
    report = _hook_report(silent_runs=4, harness_distribution={"pi": 3})
    report["coverage"] |= {"limitations": [], "reasons": []}
    _returns(cli.resolve, "hook-id")
    cli.client_get.side_effect = [[{"id": REPORT_ID, "status": "completed"}], report]
    shown = runner.invoke(insights.insights_app, ["show", "--component", "hook", "team/lint"])
    assert shown.exit_code == 0, shown.output
    text = " ".join(cli.messages())
    assert "Sessions with a recorded run: 1" in text
    assert "(a lower bound)" not in text
    assert "0 with output, 4 silent, 1 failed, 0 blocked" in text
