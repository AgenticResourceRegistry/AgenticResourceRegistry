# SPDX-FileCopyrightText: 2026 Shreem Seth <shreemseth26@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""CLI commands for Agent and evidence-backed component Insights reports."""

from __future__ import annotations

from contextlib import nullcontext
from uuid import UUID

import typer
from packaging.version import InvalidVersion, Version
from rich import print as rprint
from rich.panel import Panel
from rich.table import Table

from observal_cli import client
from observal_cli.errors import ErrorCategory, fail
from observal_cli.render import (
    OutputMode,
    console,
    esc,
    output_json,
    output_json_line,
    relative_time,
    spinner,
    status_badge,
)

insights_app = typer.Typer(
    help=(
        "Agent and MCP component insight reports\n\n"
        "Examples:\n"
        "  observal ops insights list alice/my-agent\n"
        "  observal ops insights show alice/my-agent latest\n"
        "  observal ops insights generate alice/my-agent"
    )
)

_registry_name_cache: str | None = None


def _progress(output: OutputMode | str, message: str):
    return nullcontext() if output == "json" else spinner(message)


def _version(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    try:
        return str(Version(value))
    except InvalidVersion:
        fail(
            ErrorCategory.VALIDATION,
            f"Invalid {label}: {value}.",
            operation="Generate agent insight report",
            resource=label,
            remediation="Use a semantic version such as 1.2.3.",
        )


def _registry_name() -> str:
    """White-label name for this registry, for user-facing copy.

    Best effort and cached per process: a cosmetic label must never be the
    reason a report fails to render, so any error falls back to a neutral
    phrase rather than hardcoding "Observal" over someone's branding.
    """
    global _registry_name_cache
    if _registry_name_cache is None:
        try:
            data = client.get("/api/v1/config/public")
            _registry_name_cache = str(data.get("branding_app_name") or "").strip() or "Observal"
        except Exception:
            _registry_name_cache = "your registry"
    return _registry_name_cache


def _resolve_agent_id(agent_id: str) -> str:
    """Resolve an agent UUID, name, or alias to the canonical UUID."""
    resolved = client.resolve_registry_reference("agent", agent_id)
    agent = client.get(f"/api/v1/agents/{resolved}")
    return str(agent.get("id") or resolved)


def _select_report_id(reports: list[dict], report_ref: str | None) -> str:
    if not reports:
        fail(
            ErrorCategory.NOT_FOUND,
            "No insight reports were found for this agent.",
            operation="Show agent insight report",
            resource="agent insight reports",
            remediation="Generate a report first.",
        )

    if not report_ref or report_ref == "latest":
        completed = next((report for report in reports if report.get("status") == "completed"), None)
        return str((completed or reports[0])["id"])

    if report_ref.isdigit():
        index = int(report_ref)
        if 1 <= index <= len(reports):
            return str(reports[index - 1]["id"])
        fail(
            ErrorCategory.VALIDATION,
            f"Insight report row {index} is out of range.",
            operation="Show agent insight report",
            resource="insight report row",
            remediation=f"Choose a row from 1 to {len(reports)}.",
        )

    matches = [report for report in reports if str(report.get("id", "")).lower().startswith(report_ref.lower())]
    if len(matches) == 1:
        return str(matches[0]["id"])
    if matches:
        fail(
            ErrorCategory.CONFLICT,
            f"Insight report prefix is ambiguous: {report_ref}.",
            operation="Show agent insight report",
            resource="insight report",
            remediation="Use a row number from `observal ops insights list <agent>`.",
        )

    fail(
        ErrorCategory.NOT_FOUND,
        f"Insight report not found: {report_ref}.",
        operation="Show agent insight report",
        resource="insight report",
        remediation="Use a row number from `observal ops insights list <agent>`.",
    )


def _component_path(component: tuple[str, str]) -> str:
    kind, ref = component
    if kind != "mcp":
        fail(
            ErrorCategory.VALIDATION,
            "Only MCP component reports are supported.",
            operation="Component insight report",
            resource="component type",
        )
    resolved = client.resolve_registry_reference(kind, ref)
    return f"/api/v1/insights/components/{kind}/{resolved}"


def _resolve_report_for_show(target: str, report_ref: str | None, component: tuple[str, str] | None = None) -> dict:
    if component:
        # An exact historical ID is accessible even when it is no longer in the
        # first report page. The server still rechecks component ownership.
        if report_ref:
            try:
                exact_id = str(UUID(report_ref))
            except ValueError:
                pass
            else:
                resolved_path = _component_path(component)
                report = client.get(f"/api/v1/insights/reports/{exact_id}")
                if report.get("component_id") != resolved_path.rsplit("/", 1)[-1]:
                    fail(
                        ErrorCategory.VALIDATION,
                        "Report does not belong to this component.",
                        operation="Show component insight report",
                    )
                return report
        reports = client.get(f"{_component_path(component)}/reports")
        report_id = _select_report_id(reports, report_ref)
        return client.get(f"/api/v1/insights/reports/{report_id}")
    agent_id = _resolve_agent_id(target)
    reports = client.get(f"/api/v1/agents/{agent_id}/insights/reports")
    report_id = _select_report_id(reports, report_ref)
    return client.get(f"/api/v1/agents/{agent_id}/insights/reports/{report_id}")


@insights_app.command(name="list")
def insights_list(
    agent_id: str = typer.Argument("", help="Agent ID, name, or @alias (omit with --component)"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
    component: tuple[str, str] | None = typer.Option(
        None, "--component", help="Component type and ref, e.g. mcp org/tool"
    ),
    all_reports: bool = typer.Option(False, "--all", help="List all component report pages"),
):
    """List insight reports for an agent.

    Examples:

        observal ops insights list my-agent

        observal ops insights list my-agent --output json
    """
    component = component if isinstance(component, tuple) else None
    all_reports = all_reports if isinstance(all_reports, bool) else False
    if not component and not agent_id:
        raise typer.BadParameter("Provide an agent or --component")
    with _progress(output, "Fetching insight reports..."):
        if component and agent_id:
            fail(ErrorCategory.VALIDATION, "Use an agent or --component, not both.", operation="List insight reports")
        if not component and not agent_id:
            fail(ErrorCategory.VALIDATION, "Provide an agent or --component.", operation="List insight reports")
        if all_reports and not component:
            fail(ErrorCategory.VALIDATION, "--all applies to component reports only.", operation="List insight reports")
        path = (
            f"{_component_path(component)}/reports"
            if component
            else f"/api/v1/agents/{_resolve_agent_id(agent_id)}/insights/reports"
        )
        data = client.get(f"{path}?limit=100" if all_reports else path)
        if all_reports:
            from urllib.parse import urlencode

            while len(data) and len(data) % 100 == 0:
                last = data[-1]
                page = client.get(
                    f"{path}?"
                    + urlencode(
                        {
                            "limit": 100,
                            "before_created_at": last["created_at"],
                            "before_id": last["id"],
                        }
                    )
                )
                data.extend(page)
                if len(page) < 100:
                    break
    if output == "json":
        output_json(data)
        return
    if not data:
        rprint("[dim]No insight reports found.[/dim]")
        return
    table = Table(title=f"Insight Reports ({len(data)})", show_lines=False, padding=(0, 1))
    table.add_column("#", style="dim", width=3)
    if all_reports:
        table.add_column("Report ID")
    table.add_column("Status")
    table.add_column("Version")
    table.add_column("Period")
    table.add_column("Sessions", justify="right")
    table.add_column("Completed")
    for i, r in enumerate(data, 1):
        start = str(r.get("period_start") or "")[:10]
        end = str(r.get("period_end") or "")[:10]
        table.add_row(
            str(i),
            *([esc(r.get("id", ""))] if all_reports else []),
            status_badge(r.get("status", "")),
            esc(r.get("component_version") or "all" if component else r.get("agent_version") or "-"),
            f"{esc(start)} → {esc(end)}",
            str(r.get("sessions_analyzed", 0)),
            relative_time(r.get("completed_at")),
        )
    console.print(table)
    rprint()
    target_hint = f"--component {esc(component[0])} {esc(component[1])}" if component else esc(agent_id)
    rprint(f"[dim]Open latest completed: [cyan]observal ops insights show {target_hint}[/cyan][/dim]")
    rprint(f"[dim]Open row 1: [cyan]observal ops insights show {target_hint} 1[/cyan][/dim]")
    if all_reports:
        rprint(f"[dim]Open an older report: [cyan]observal ops insights show {target_hint} REPORT_ID[/cyan][/dim]")


@insights_app.command(name="show")
def insights_show(
    target: str = typer.Argument("", help="Agent name, agent ID, or @alias (omit with --component)"),
    report_ref: str | None = typer.Argument(None, help="Report row number, report ID prefix, or 'latest'"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
    section: str | None = typer.Option(None, "--section", "-s", help="Show only a specific section"),
    component: tuple[str, str] | None = typer.Option(None, "--component", help="Component type and ref"),
):
    """Show an insight report with pretty-printed narrative.

    Examples:

        observal ops insights show my-agent

        observal ops insights show my-agent 3

        observal ops insights show my-agent --section suggestions
    """
    component = component if isinstance(component, tuple) else None
    if not component and not target:
        raise typer.BadParameter("Provide an agent or --component")
    with _progress(output, "Fetching report..."):
        if component:
            if target and report_ref:
                fail(
                    ErrorCategory.VALIDATION,
                    "Provide one report reference with --component.",
                    operation="Show insight report",
                )
            report_ref = report_ref or target or None
        elif not target:
            fail(ErrorCategory.VALIDATION, "Provide an agent or --component.", operation="Show insight report")
        data = (
            _resolve_report_for_show(target, report_ref, component)
            if component
            else _resolve_report_for_show(target, report_ref)
        )
    if output == "json":
        if section:
            narrative = data.get("narrative") or {}
            if not isinstance(narrative, dict):
                fail(
                    ErrorCategory.UNAVAILABLE,
                    "The insight report narrative has an invalid shape.",
                    operation="Show agent insight report",
                    resource="insight report",
                    remediation="Regenerate the report or check server compatibility.",
                )
            if section not in narrative:
                fail(
                    ErrorCategory.VALIDATION,
                    f"Unknown insight report section: {section}.",
                    operation="Show agent insight report",
                    resource="insight report section",
                    remediation=f"Choose from: {', '.join(narrative.keys())}.",
                )
            output_json({"report_id": data.get("id"), "section": section, "data": narrative[section]})
        else:
            output_json(data)
        return
    if data.get("status") != "completed":
        rprint(f"  Status: {status_badge(data.get('status', 'unknown'))}")
        if data.get("progress_phase"):
            rprint(
                f"  Phase: [cyan]{esc(str(data.get('progress_phase')).replace('_', ' '))}[/cyan] "
                f"({data.get('progress_percent', 0)}%)"
            )
        if data.get("progress_message"):
            rprint(f"  [dim]{esc(data['progress_message'])}[/dim]")
        if data.get("error_message"):
            rprint(f"  [red]Error:[/red] {esc(data['error_message'])}")
        return

    if data.get("subject_type") == "component":
        metrics = data.get("metrics") or {}
        coverage = data.get("coverage") or {}
        rprint(f"[bold]{esc(data.get('component_name') or 'Component')} Insights[/bold]")
        observed = (
            metrics.get("observed_calls", 0) if coverage.get("usage_rate_denominator_sessions", 0) else "not measured"
        )
        rprint(f"  Present sessions: {metrics.get('present_sessions', 0)}  Observed calls: {observed}")
        narrative = data.get("narrative") or {}
        rprint(f"  {esc(narrative.get('summary') or 'No summary available')}")
        analysis = narrative.get("component_analysis") or {}
        if not analysis:
            rprint(
                "  [dim]This report predates evidence-backed analysis; generate a new report to assess sessions.[/dim]"
            )
        elif analysis.get("state") != "assessed" or not analysis.get("findings"):
            rprint("  [dim]No grounded interpretive findings in this sample; this does not mean unused.[/dim]")
        else:
            rprint("\n[bold]What the published calls suggest[/bold]")
            for finding in analysis["findings"]:
                label = finding["kind"]
                rprint(
                    f"  [bold]{esc(label)}[/bold] · {esc(finding['confidence'])} confidence: {esc(finding['insight'])}"
                )
                for ref in finding["evidence_refs"]:
                    excerpt = (analysis.get("evidence") or {}).get(ref)
                    if excerpt:
                        rprint(f"    [dim]{esc(ref)} · {esc(excerpt)}[/dim]")
        if analysis:
            rprint(
                f"  [dim]Sample: {analysis.get('sampled_sessions', 0)} sessions"
                f"{' (truncated)' if analysis.get('truncated') else ''}; not cohort-wide.[/dim]"
            )
        rprint(
            f"  Cohort attribution gaps: {metrics.get('cohort_collision_calls', 0)} collisions, "
            f"{metrics.get('cohort_unmatched_calls', 0)} unmatched calls (not assigned to this MCP)"
        )
        for label, values in (
            ("Versions", metrics.get("version_distribution") or {}),
            ("Harnesses", metrics.get("harness_distribution") or {}),
        ):
            if values:
                rprint(f"  {label}: " + ", ".join(f"{esc(name)} ({count})" for name, count in sorted(values.items())))
        projection = coverage.get("projection") or {}
        rprint(
            f"  Coverage: {projection.get('projection_complete_sessions', 0)} processed; "
            f"state: {esc(coverage.get('attribution_state', 'unknown'))}"
        )
        if coverage.get("reasons"):
            rprint(f"  Gaps: {esc(', '.join(coverage['reasons']))}")
        for limitation in coverage.get("limitations") or []:
            rprint(f"  [dim]{esc(limitation)}[/dim]")
        return

    narrative = data.get("narrative") or {}
    if not isinstance(narrative, dict):
        fail(
            ErrorCategory.UNAVAILABLE,
            "The insight report narrative has an invalid shape.",
            operation="Show agent insight report",
            resource="insight report",
            remediation="Regenerate the report or check server compatibility.",
        )
    registry_match = narrative.get("registry_match")
    if not isinstance(registry_match, dict):
        registry_match = None
    if section:
        if section not in narrative:
            fail(
                ErrorCategory.VALIDATION,
                f"Unknown insight report section: {section}.",
                operation="Show agent insight report",
                resource="insight report section",
                remediation=f"Choose from: {', '.join(narrative.keys())}.",
            )
        _render_section(section, narrative[section])
        if section == "suggestions":
            _render_registry_match_note(registry_match)
        return

    # Header
    start = str(data.get("period_start") or "")[:10]
    end = str(data.get("period_end") or "")[:10]
    rprint()
    version = data.get("agent_version") or "unknown"
    comparison = data.get("comparison_agent_version")
    comparison_text = f"  Compared to: v{esc(comparison)}" if comparison else ""
    rprint(f"  [bold]Insight Report[/bold]  v{esc(version)}  {esc(start)} → {esc(end)}")
    rprint(
        f"  Sessions: {data.get('sessions_analyzed', 0)}  Model: {esc(data.get('llm_model_used', 'unknown'))}"
        f"{comparison_text}"
    )
    rich = (data.get("metrics") or {}).get("rich") or {}
    if rich.get("cache_hit_rate_pct") is not None:
        rprint(f"  Cache: {rich.get('cache_hit_rate_pct')}% hit rate, {rich.get('cache_tokens_saved', 0)} tokens saved")
    rprint()

    # Render sections in logical order
    order = [
        "at_a_glance",
        "what_they_work_on",
        "interaction_style",
        "usage_patterns",
        "what_works",
        "friction_analysis",
        "suggestions",
        "usage_cost_analysis",
        "version_comparison",
        "regression_detection",
        "on_the_horizon",
        "fun_ending",
    ]
    note_rendered = False
    for key in order:
        section_data = narrative.get(key)
        if section_data:
            _render_section(key, section_data)
            if key == "suggestions":
                _render_registry_match_note(registry_match)
                note_rendered = True
    if not note_rendered:
        _render_registry_match_note(registry_match)


# ──────────────────────────────────────────────────────────────────────────────
# Section renderers
# ──────────────────────────────────────────────────────────────────────────────

_SECTION_TITLES = {
    "at_a_glance": "⚡ At a Glance",
    "what_they_work_on": "📂 What You Work On",
    "interaction_style": "💬 Interaction Style",
    "usage_patterns": "📊 Usage Patterns",
    "what_works": "✅ What Works",
    "friction_analysis": "⚠️  Friction Analysis",
    "suggestions": "💡 Suggestions",
    "usage_cost_analysis": "💰 Cost Analysis",
    "version_comparison": "🧪 Version Comparison",
    "regression_detection": "📈 Regression Detection",
    "on_the_horizon": "🔮 On the Horizon",
    "fun_ending": "🎉 Fun Moment",
}

_HEALTH_COLORS = {"healthy": "green", "mixed": "yellow", "concerning": "red"}


def _render_section(name: str, data: dict | str | None):
    if not data:
        return
    title = _SECTION_TITLES.get(name, name.replace("_", " ").title())
    # Every typed renderer indexes into a mapping. Older reports stored some
    # sections as a plain string or list, which would crash on `.get`.
    renderer = _RENDERERS.get(name) if isinstance(data, dict) else None
    if renderer:
        renderer(title, data)
    elif isinstance(data, str):
        console.print(Panel(esc(data), title=f"[bold]{esc(title)}[/bold]", border_style="blue", expand=False))
    elif isinstance(data, dict) and "narrative" in data:
        console.print(
            Panel(esc(data["narrative"]), title=f"[bold]{esc(title)}[/bold]", border_style="blue", expand=False)
        )


def _render_at_a_glance(title: str, data: dict):
    health = data.get("health", "unknown")
    color = _HEALTH_COLORS.get(health, "white")
    lines = [f"[bold]Health:[/bold] [{color}]{esc(health)}[/{color}]", ""]
    if data.get("whats_working"):
        lines += [f"[green]What's working:[/green] {esc(data['whats_working'])}", ""]
    if data.get("whats_hindering"):
        lines += [f"[yellow]What's hindering:[/yellow] {esc(data['whats_hindering'])}", ""]
    if data.get("quick_win"):
        lines += [f"[cyan]Quick win:[/cyan] {esc(data['quick_win'])}", ""]
    if data.get("ambitious_workflows"):
        lines += [f"[magenta]Ambitious workflows:[/magenta] {esc(data['ambitious_workflows'])}"]
    console.print(Panel("\n".join(lines), title=f"[bold]{title}[/bold]", border_style="bright_blue", expand=False))


def _render_what_they_work_on(title: str, data: dict):
    areas = data.get("areas", [])
    if not areas:
        return
    table = Table(title=title, show_lines=False, padding=(0, 1))
    table.add_column("Area", style="bold")
    table.add_column("Sessions", justify="right")
    table.add_column("Description")
    for a in areas:
        table.add_row(esc(a.get("name", "")), esc(a.get("sessions", "")), esc(a.get("description", "")))
    console.print(table)
    rprint()


def _render_interaction_style(title: str, data: dict):
    lines = []
    if data.get("narrative"):
        lines.append(esc(data["narrative"]))
    if data.get("key_pattern"):
        lines += ["", f"[bold]Key pattern:[/bold] [italic]{esc(data['key_pattern'])}[/italic]"]
    if lines:
        console.print(Panel("\n".join(lines), title=f"[bold]{title}[/bold]", border_style="blue", expand=False))


def _render_usage_patterns(title: str, data: dict):
    lines = []
    if data.get("narrative"):
        lines.append(esc(data["narrative"]))
    sp = data.get("session_profile", {})
    if sp:
        lines += [
            "",
            f"  Avg duration: [bold]{esc(sp.get('avg_duration_minutes', '?'))}m[/bold]"
            f"  Tool calls: [bold]{esc(sp.get('avg_tool_calls', '?'))}[/bold]"
            f"  Prompts: [bold]{esc(sp.get('avg_prompts', '?'))}[/bold]",
        ]
    if lines:
        console.print(Panel("\n".join(lines), title=f"[bold]{title}[/bold]", border_style="blue", expand=False))
    tools = data.get("tool_distribution", [])
    if tools:
        t = Table(show_lines=False, padding=(0, 1))
        t.add_column("Tool", style="bold")
        t.add_column("Calls", justify="right")
        t.add_column("Error %", justify="right")
        for tool in tools[:10]:
            err = tool.get("error_rate", 0)
            err_style = "red" if err > 0.1 else "yellow" if err > 0.01 else "green"
            t.add_row(esc(tool.get("tool", "")), esc(tool.get("calls", "")), f"[{err_style}]{err:.1%}[/{err_style}]")
        console.print(t)
        rprint()


def _render_what_works(title: str, data: dict):
    lines = []
    if data.get("intro"):
        lines.append(esc(data["intro"]))
    for s in data.get("strengths", []):
        lines += [
            "",
            f"  [green]●[/green] [bold]{esc(s.get('title', ''))}[/bold]",
            f"    {esc(s.get('description', ''))}",
        ]
    if lines:
        console.print(Panel("\n".join(lines), title=f"[bold]{title}[/bold]", border_style="green", expand=False))


def _render_friction(title: str, data: dict):
    lines = []
    if data.get("intro"):
        lines.append(esc(data["intro"]))
    sev_colors = {"high": "red", "medium": "yellow", "low": "dim"}
    for cat in data.get("categories", []):
        sev = cat.get("severity", "medium")
        color = sev_colors.get(sev, "white")
        lines += ["", f"  [{color}]■ {esc(cat.get('title', ''))}[/{color}] [{color}]({esc(sev)})[/{color}]"]
        if cat.get("description"):
            lines.append(f"    {esc(cat['description'])}")
        for ex in cat.get("examples", []):
            lines.append(f"    [dim]• {esc(ex)}[/dim]")
        if cat.get("impact"):
            lines.append(f"    [dim]Impact: {esc(cat['impact'])}[/dim]")
    if lines:
        console.print(Panel("\n".join(lines), title=f"[bold]{title}[/bold]", border_style="yellow", expand=False))


def _reuse_ref(feature: object) -> dict | None:
    """The validated registry reference on a suggestion, if it has one.

    The server strips this field from any suggestion whose component could
    not be resolved, so its presence is the signal that the component is
    genuinely in the registry and safe to point the user at.
    """
    if not isinstance(feature, dict):
        return None
    ref = feature.get("component_ref")
    return ref if isinstance(ref, dict) else None


def _render_reuse_feature(feature: dict, ref: dict):
    """A suggestion to reuse something the registry already has.

    Given prominence over "build this" suggestions, because installing an
    approved component is cheaper and safer than writing a new one.
    """
    component_type = str(ref.get("type") or "skill")
    name = ref.get("qualified_name") or ref.get("name", "")
    version = ref.get("latest_version") or ""
    version_suffix = f" [dim]v{esc(version)}[/dim]" if version else ""

    rprint(f"    [green]✔ ALREADY IN {esc(_registry_name().upper())}[/green]  [dim]({esc(component_type)})[/dim]")
    rprint(f"      [bold]{esc(name)}[/bold]{version_suffix}")
    if feature.get("one_liner"):
        rprint(f"      {esc(feature['one_liner'])}")
    if feature.get("match_reason"):
        rprint(f"      [dim]Why this fits: {esc(feature['match_reason'])}[/dim]")
    elif feature.get("why_for_you"):
        rprint(f"      [dim]{esc(feature['why_for_you'])}[/dim]")
    rprint(f"      [cyan]observal registry {esc(component_type)} show {esc(name)} --output json[/cyan]")
    rprint(f"      [dim]Attach to an agent: observal agent add {esc(component_type)} {esc(ref.get('id', ''))}[/dim]")


def _render_registry_match_note(summary: dict | None):
    """Say why a report suggested nothing to reuse.

    An empty reuse list is ambiguous on its own: "we searched and nothing
    fit" and "there was nothing to search" look identical but mean opposite
    things to someone judging whether the feature works. Reports generated
    before this existed carry no summary, and stay silent.
    """
    if not isinstance(summary, dict) or (summary.get("reused") or 0) > 0:
        return

    offered = summary.get("offered") or 0
    if summary.get("enabled") is False:
        message = "Reuse suggestions are turned off, so this report only proposes new components."
    elif summary.get("registry_has_components") is False:
        message = (
            f"No components have been published to {_registry_name()} yet, so there was nothing to "
            "reuse. Publish skills, hooks or prompts and future reports will point at them."
        )
    elif offered == 0:
        message = (
            f"Nothing in {_registry_name()} looked close enough to this agent's work to recommend. "
            "More sessions give the match more to go on."
        )
    else:
        message = (
            f"Checked {offered} component{'' if offered == 1 else 's'} already in {_registry_name()}; "
            "none fit this agent's problems closely enough to recommend."
        )
    rprint(f"  [dim]ⓘ {esc(message)}[/dim]")
    rprint()


def _render_suggestions(title: str, data: dict):
    # Config additions
    configs = data.get("config_additions", [])
    if configs:
        rprint(f"\n  [bold]{title} > Config Additions[/bold]")
        for c in configs:
            rprint(f"    [cyan]→[/cyan] {esc(c.get('addition', ''))}")
            rprint(f"      [dim]Why: {esc(c.get('why', ''))} | Where: {esc(c.get('where', ''))}[/dim]")

    # Features to try
    features = [f for f in data.get("features_to_try", []) or [] if isinstance(f, dict)]
    if features:
        rprint(f"\n  [bold]{title} > Features to Try[/bold]")
        # Reuse suggestions lead: "you already own this" is a better answer
        # than "go build this", so it should be the first thing read.
        for f in sorted(features, key=lambda item: not _reuse_ref(item)):
            ref = _reuse_ref(f)
            if ref:
                _render_reuse_feature(f, ref)
                continue
            label = f.get("feature", "")
            rprint(f"    [magenta]{esc(label)}:[/magenta] [bold]{esc(f.get('name', ''))}[/bold]")
            rprint(f"      {esc(f.get('one_liner', ''))}")
            if f.get("why_for_you"):
                rprint(f"      [dim]{esc(f['why_for_you'])}[/dim]")

    # Usage patterns
    patterns = data.get("usage_patterns", [])
    if patterns:
        rprint(f"\n  [bold]{title} > Usage Patterns[/bold]")
        for p in patterns:
            rprint(f"    [cyan]●[/cyan] [bold]{esc(p.get('title', ''))}[/bold]: {esc(p.get('suggestion', ''))}")
            if p.get("detail"):
                rprint(f"      [dim]{esc(p['detail'])}[/dim]")
            if p.get("copyable_prompt"):
                rprint(f"      [green]Try:[/green] {esc(p['copyable_prompt'])}")
    rprint()


def _render_cost(title: str, data: dict):
    lines = []
    if data.get("summary"):
        lines.append(esc(data["summary"]))
    m = data.get("metrics", {})
    if m:
        parts = []
        if m.get("total_cost_usd") is not None:
            parts.append(f"Total: ${m['total_cost_usd']:.2f}")
        if m.get("cost_per_session") is not None:
            parts.append(f"Per session: ${m['cost_per_session']:.3f}")
        if m.get("cache_efficiency_pct") is not None:
            parts.append(f"Cache: {m['cache_efficiency_pct']:.0f}%")
        if parts:
            lines += ["", "  " + "  │  ".join(parts)]
    opps = data.get("opportunities", [])
    for o in opps:
        lines += ["", f"  [yellow]●[/yellow] [bold]{esc(o.get('title', ''))}[/bold]"]
        if o.get("description"):
            lines.append(f"    {esc(o['description'])}")
        if o.get("estimated_savings"):
            lines.append(f"    [green]Savings: {esc(o['estimated_savings'])}[/green]")
    if lines:
        console.print(Panel("\n".join(lines), title=f"[bold]{title}[/bold]", border_style="blue", expand=False))


def _render_regression(title: str, data: dict):
    if not data.get("has_previous_data"):
        rprint(f"  [dim]{title}: No previous data for comparison.[/dim]")
        return
    lines = []
    if data.get("summary"):
        lines.append(esc(data["summary"]))
    for c in data.get("changes", []):
        direction = c.get("direction", "stable")
        icon = {"improved": "[green]↑[/green]", "degraded": "[red]↓[/red]", "stable": "[dim]→[/dim]"}.get(
            direction, "→"
        )
        sig = c.get("significance", "")
        sig_dim = f" [dim]({esc(sig)})[/dim]" if sig else ""
        lines.append(
            f"  {icon} [bold]{esc(c.get('metric', ''))}[/bold]: "
            f"{esc(c.get('previous_value', '?'))} → {esc(c.get('current_value', '?'))}{sig_dim}"
        )
    if lines:
        console.print(Panel("\n".join(lines), title=f"[bold]{title}[/bold]", border_style="blue", expand=False))


def _render_horizon(title: str, data: dict):
    lines = []
    if data.get("intro"):
        lines.append(esc(data["intro"]))
    for o in data.get("opportunities", []):
        lines += ["", f"  [magenta]●[/magenta] [bold]{esc(o.get('title', ''))}[/bold]"]
        if o.get("whats_possible"):
            lines.append(f"    {esc(o['whats_possible'])}")
        if o.get("how_to_try"):
            lines.append(f"    [cyan]Try:[/cyan] {esc(o['how_to_try'])}")
    if lines:
        console.print(Panel("\n".join(lines), title=f"[bold]{title}[/bold]", border_style="magenta", expand=False))


def _render_version_comparison(title: str, data: dict):
    lines = []
    if data.get("summary"):
        lines.append(esc(data["summary"]))
    if data.get("confidence"):
        lines.append(f"[dim]Confidence: {esc(data['confidence'])}[/dim]")
    for change in data.get("changes", [])[:6]:
        lines.append(
            f"\n[bold]{esc(change.get('metric', ''))}[/bold]: {esc(change.get('direction', ''))} "
            f"({esc(change.get('prior_value', '?'))} → {esc(change.get('current_value', '?'))})"
        )
        if change.get("attribution"):
            lines.append(f"[dim]Attribution: {esc(change['attribution'])}[/dim]")
        if change.get("evidence"):
            lines.append(f"[dim]{esc(change['evidence'])}[/dim]")
    if lines:
        console.print(Panel("\n".join(lines), title=f"[bold]{title}[/bold]", border_style="blue", expand=False))


def _render_fun_ending(title: str, data: dict):
    headline = data.get("headline", "")
    detail = data.get("detail", "")
    if headline:
        content = f"[italic]{esc(headline)}[/italic]"
        if detail:
            content += f"\n[dim]{esc(detail)}[/dim]"
        console.print(Panel(content, title=f"[bold]{title}[/bold]", border_style="bright_yellow", expand=False))


_RENDERERS = {
    "at_a_glance": _render_at_a_glance,
    "what_they_work_on": _render_what_they_work_on,
    "interaction_style": _render_interaction_style,
    "usage_patterns": _render_usage_patterns,
    "what_works": _render_what_works,
    "friction_analysis": _render_friction,
    "suggestions": _render_suggestions,
    "usage_cost_analysis": _render_cost,
    "version_comparison": _render_version_comparison,
    "regression_detection": _render_regression,
    "on_the_horizon": _render_horizon,
    "fun_ending": _render_fun_ending,
}


@insights_app.command(name="generate")
def insights_generate(
    agent_id: str = typer.Argument("", help="Agent ID, name, or @alias (omit with --component)"),
    period_days: int = typer.Option(14, "--period", "-p", min=1, max=365, help="Analysis period in days"),
    agent_version: str | None = typer.Option(None, "--version", "-v", help="Agent version to analyze"),
    compare_version: str | None = typer.Option(None, "--compare", help="Baseline agent version for A/B comparison"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
    wait: bool = typer.Option(False, "--wait", help="Poll until the report completes"),
    component: tuple[str, str] | None = typer.Option(None, "--component", help="Component type and ref"),
):
    """Trigger generation of a new insight report.

    Examples:

        observal ops insights generate my-agent

        observal ops insights generate my-agent --period 30
    """
    component = component if isinstance(component, tuple) else None
    if not component and not agent_id:
        raise typer.BadParameter("Provide an agent or --component")
    if (component and agent_id) or (not component and not agent_id):
        fail(ErrorCategory.VALIDATION, "Provide either an agent or --component.", operation="Generate insight report")
    if component and (agent_version or compare_version):
        fail(
            ErrorCategory.VALIDATION,
            "Agent version flags do not apply to component reports.",
            operation="Generate component insight report",
        )
    if component and period_days > 90:
        fail(
            ErrorCategory.VALIDATION,
            "Component reports support at most 90 days.",
            operation="Generate component insight report",
        )
    agent_version = _version(agent_version, "agent version")
    compare_version = _version(compare_version, "comparison version")

    # Evidence-backed component reports degrade to deterministic facts without a configured LLM.
    if not component:
        with _progress(output, "Checking insights configuration..."):
            status = client.get("/api/v1/insights/status")
        if not status.get("available"):
            reason = status.get("reason", "Insights is not configured.")
            fail(
                ErrorCategory.UNAVAILABLE,
                f"Insights is unavailable: {reason}",
                operation="Generate agent insight report",
                resource="insights service",
                remediation="Configure insights.model_sections and insights.api_key, then retry.",
            )

    with _progress(output, "Generating insight report..."):
        resolved = _resolve_agent_id(agent_id) if not component else ""
        component_path = _component_path(component) if component else ""
        body = {"period_days": period_days}
        if agent_version:
            body["agent_version"] = agent_version
        if compare_version:
            body["comparison_agent_version"] = compare_version
        data = client.post(
            f"{component_path}/generate" if component else f"/api/v1/agents/{resolved}/insights/reports", body
        )

    if wait:
        import time

        report_id = str(data.get("id"))
        if output == "json":
            output_json_line({"event": "queued", "report": data})
        for _ in range(120):
            current = client.get(
                f"/api/v1/insights/reports/{report_id}"
                if component
                else f"/api/v1/agents/{resolved}/insights/reports/{report_id}"
            )
            phase = str(current.get("progress_phase") or current.get("status") or "queued").replace("_", " ")
            percent = current.get("progress_percent", 0)
            if output == "json":
                output_json_line({"event": "progress", "report": current})
            else:
                rprint(f"\r  {status_badge(current.get('status', 'pending'))} {esc(phase)} ({percent}%)", end="")
            if current.get("status") in {"completed", "failed"}:
                if output != "json":
                    rprint()
                data = current
                break
            time.sleep(3)
        else:
            fail(
                ErrorCategory.UNAVAILABLE,
                "Timed out waiting for the insight report.",
                operation="Generate agent insight report",
                resource="insight report",
                remediation="Use `observal ops insights show <agent> latest` to check it later.",
            )

        if data.get("status") == "failed":
            fail(
                ErrorCategory.UNAVAILABLE,
                "Insight report generation failed.",
                operation="Generate agent insight report",
                resource="insight report",
                remediation="Inspect the report error and insights provider configuration.",
                detail=str(data.get("error_message") or ""),
            )
        if output == "json":
            return

    if output == "json":
        output_json(data)
        return
    rprint(f"[green]✓ Report queued[/green] (status: {status_badge(data.get('status', 'pending'))})")
    rprint(f"  ID: [dim]{esc(data.get('id', ''))}[/dim]")
    if data.get("agent_version"):
        rprint(f"  Version: v{esc(data.get('agent_version'))}")
    if data.get("comparison_agent_version"):
        rprint(f"  Compare: v{esc(data.get('comparison_agent_version'))}")
    rprint(f"  Period: {str(data.get('period_start', ''))[:10]} → {str(data.get('period_end', ''))[:10]}")
    if data.get("progress_phase"):
        rprint(
            f"  Phase: {esc(str(data.get('progress_phase')).replace('_', ' '))} ({data.get('progress_percent', 0)}%)"
        )
    hint = f"--component {esc(component[0])} {esc(component[1])}" if component else "<agent>"
    rprint(f"[dim]  Run `observal ops insights show {hint}` when complete.[/dim]")
