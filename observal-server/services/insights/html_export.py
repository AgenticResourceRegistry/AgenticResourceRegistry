# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""HTML export for insight reports — self-contained single-file report (V3)."""

from __future__ import annotations

import html
from datetime import datetime


def _esc(text: str | None) -> str:
    """HTML-escape text."""
    return html.escape(str(text)) if text else ""


def _format_cost(val: float | None) -> str:
    if val is None:
        return "$0.00"
    if val < 0.01:
        return f"${val:.4f}"
    return f"${val:.2f}"


def _format_number(val: int | float | None) -> str:
    if val is None:
        return "0"
    if isinstance(val, float):
        if val == int(val):
            return f"{int(val):,}"
        return f"{val:,.1f}"
    return f"{val:,}"


def _format_duration_hours(seconds: float | None) -> str:
    if not seconds:
        return "0h"
    hours = seconds / 3600
    if hours < 1:
        return f"{seconds / 60:.0f}m"
    return f"{hours:.1f}h"


def _severity_color(severity: str) -> str:
    return {"high": "#dc2626", "medium": "#d97706", "low": "#2563eb"}.get(severity, "#6b7280")


def _priority_color(priority: str) -> str:
    return {"high": "#dc2626", "medium": "#d97706", "low": "#16a34a"}.get(priority, "#6b7280")


def _health_badge(health: str) -> str:
    colors = {
        "healthy": ("#16a34a", "#f0fdf4"),
        "mixed": ("#d97706", "#fffbeb"),
        "concerning": ("#dc2626", "#fef2f2"),
    }
    fg, bg = colors.get(health, ("#6b7280", "#f1f5f9"))
    return (
        f'<span class="health-badge" style="background:{bg};color:{fg};'
        f'border:1px solid {fg}30;">{_esc(health).upper()}</span>'
    )


def _render_bar_chart(items: list[tuple[str, float]], color: str = "var(--blue)") -> str:
    """Render a CSS horizontal bar chart from (label, value) pairs."""
    if not items:
        return ""
    max_val = max(v for _, v in items) if items else 1
    if max_val == 0:
        max_val = 1
    rows = ""
    for label, value in items:
        pct = (value / max_val) * 100
        display_val = f"{value:.0f}%" if max_val <= 100 and all(v <= 100 for _, v in items) else _format_number(value)
        # If all values sum roughly to 100 or less and look like percentages, show as %
        rows += f"""<div class="chart-bar-row">
  <span class="chart-label">{_esc(label)}</span>
  <div class="chart-bar-container">
    <div class="chart-bar" style="width:{pct:.1f}%;background:{color}"></div>
  </div>
  <span class="chart-value">{display_val}</span>
</div>"""
    return rows


def _render_pct_bar_chart(items: list[tuple[str, float]], color: str = "var(--blue)") -> str:
    """Render bar chart where values are already percentages (0-100)."""
    if not items:
        return ""
    rows = ""
    for label, value in items:
        rows += f"""<div class="chart-bar-row">
  <span class="chart-label">{_esc(label)}</span>
  <div class="chart-bar-container">
    <div class="chart-bar" style="width:{value:.1f}%;background:{color}"></div>
  </div>
  <span class="chart-value">{value:.0f}%</span>
</div>"""
    return rows


def _render_count_bar_chart(items: list[tuple[str, int | float]], color: str = "var(--blue)") -> str:
    """Render bar chart where values are raw counts."""
    if not items:
        return ""
    max_val = max(v for _, v in items) if items else 1
    if max_val == 0:
        max_val = 1
    rows = ""
    for label, value in items:
        pct = (value / max_val) * 100
        rows += f"""<div class="chart-bar-row">
  <span class="chart-label">{_esc(label)}</span>
  <div class="chart-bar-container">
    <div class="chart-bar" style="width:{pct:.1f}%;background:{color}"></div>
  </div>
  <span class="chart-value">{_format_number(value)}</span>
</div>"""
    return rows


def render_report_html(report: dict) -> str:
    """Render a complete V3 insight report as a self-contained HTML document.

    Args:
        report: Full report dict with keys: id, agent_id, agent_name, status,
                period_start, period_end, metrics, narrative, facets_summary,
                sessions_analyzed, regressions, etc.

    Returns:
        Complete HTML string for the report.
    """
    metrics = report.get("metrics") or {}
    narrative = report.get("narrative") or {}
    facets = report.get("facets_summary") or {}
    agent_name = report.get("agent_name") or report.get("agent_id", "Agent")
    period_start = report.get("period_start", "")
    period_end = report.get("period_end", "")
    sessions_analyzed = report.get("sessions_analyzed", 0)
    report_id = report.get("id", "")
    agent_version = report.get("agent_version") or ""
    comparison_agent_version = report.get("comparison_agent_version") or ""

    # Format dates
    if isinstance(period_start, datetime):
        period_start = period_start.strftime("%Y-%m-%d")
    elif isinstance(period_start, str) and "T" in period_start:
        period_start = period_start.split("T")[0]

    if isinstance(period_end, datetime):
        period_end = period_end.strftime("%Y-%m-%d")
    elif isinstance(period_end, str) and "T" in period_end:
        period_end = period_end.split("T")[0]

    # Extract narrative sections
    at_a_glance = narrative.get("at_a_glance") or {}
    what_they_work_on = narrative.get("what_they_work_on") or {}
    usage_patterns = narrative.get("usage_patterns") or {}
    what_works = narrative.get("what_works") or {}
    friction = narrative.get("friction_analysis") or {}
    suggestions = narrative.get("suggestions") or {}
    usage_cost = narrative.get("usage_cost_analysis") or {}
    regression = narrative.get("regression_detection") or {}
    version_comparison = narrative.get("version_comparison") or {}
    fun_ending = narrative.get("fun_ending") or {}

    # Metrics sub-dicts
    overview = metrics.get("overview") or {}
    tokens = metrics.get("tokens") or {}
    metrics.get("credits") or {}
    cost = metrics.get("cost") or {}
    duration = metrics.get("duration") or {}
    tools_list = metrics.get("tools") or []
    metrics.get("tool_errors") or {}
    git = metrics.get("git") or {}
    languages = metrics.get("languages") or {}
    time_of_day = metrics.get("time_of_day") or {}
    metrics.get("interruptions") or {}
    metrics.get("multi_session") or {}
    metrics.get("subagents") or {}
    rich = metrics.get("rich") or {}
    interaction_style = narrative.get("interaction_style") or {}

    sections_html = []

    # ══════════════════════════════════════════════════════════════════════════
    # AT A GLANCE
    # ══════════════════════════════════════════════════════════════════════════
    if at_a_glance:
        health = at_a_glance.get("health", "mixed")
        sections_html.append(f"""
<section class="at-a-glance-section">
  <div class="at-a-glance-card">
    <div class="glance-header">
      <h2>At a Glance</h2>
      {_health_badge(health)}
    </div>
    <div class="glance-grid">
      <div class="glance-item glance-good">
        <div class="glance-icon">&#10003;</div>
        <div>
          <h4>What's Working</h4>
          <p>{_esc(at_a_glance.get("whats_working", ""))}</p>
        </div>
      </div>
      <div class="glance-item glance-bad">
        <div class="glance-icon">&#9888;</div>
        <div>
          <h4>What's Hindering</h4>
          <p>{_esc(at_a_glance.get("whats_hindering", ""))}</p>
        </div>
      </div>
      <div class="glance-item glance-action">
        <div class="glance-icon">&#9889;</div>
        <div>
          <h4>Quick Win</h4>
          <p>{_esc(at_a_glance.get("quick_win", ""))}</p>
        </div>
      </div>
      <div class="glance-item glance-ambitious">
        <div class="glance-icon">&#127942;</div>
        <div>
          <h4>Ambitious Workflows</h4>
          <p>{_esc(at_a_glance.get("ambitious_workflows", ""))}</p>
        </div>
      </div>
    </div>
  </div>
</section>""")

    # ══════════════════════════════════════════════════════════════════════════
    # STATS ROW
    # ══════════════════════════════════════════════════════════════════════════
    # Prefer rich metrics (from raw transcript analysis) over ClickHouse metrics
    total_sessions = overview.get("total_sessions", sessions_analyzed)
    avg_dur = duration.get("avg_duration_seconds", 0)
    active_hours = rich.get("active_hours", 0) or ((avg_dur * total_sessions) / 3600 if avg_dur else 0)
    days_active = rich.get("days_active", 0)
    total_messages = rich.get("total_messages", 0)
    commits = rich.get("git_commits", 0) or git.get("commits", 0)
    git_pushes = rich.get("git_pushes", 0) or git.get("pushes", 0)
    lines_added = rich.get("lines_added", 0) or git.get("lines_added", 0)
    lines_removed = rich.get("lines_removed", 0) or git.get("lines_removed", 0)
    files_modified = rich.get("files_modified", 0) or git.get("files_modified", 0)
    total_cost = rich.get("total_cost_usd", 0) or cost.get("total_cost_usd", 0)
    tool_errors_total = rich.get("tool_errors", 0)
    interruptions_total = rich.get("interruptions", 0)
    subagent_sessions = rich.get("subagent_sessions", 0)
    rich_top_tools = rich.get("top_tools", [])
    rich_top_langs = rich.get("top_languages", [])
    rich_error_cats = rich.get("tool_error_categories", {})
    cache_hit_rate = rich.get("cache_hit_rate_pct")
    cache_tokens_saved = rich.get("cache_tokens_saved") or rich.get("total_cache_read_tokens", 0)
    canonical_dirty = rich.get("canonical_dirty_summary") or {}

    def _fmt_tokens(n: int | float) -> str:
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.0f}k"
        return _format_number(n)

    # Build stats row with all the rich data (matching pi /insights)
    stat_items = [
        ("Sessions", _format_number(total_sessions), f"{days_active} active days" if days_active else ""),
        (
            "Messages",
            _format_number(total_messages),
            f"{total_messages / max(total_sessions, 1):.1f} per session" if total_messages else "",
        ),
        (
            "Active Time",
            f"{active_hours:.1f}h" if active_hours >= 1 else f"{active_hours * 60:.0f}m",
            "",
        ),
        (
            "Total Cost",
            _format_cost(total_cost),
            f"{_format_cost(total_cost / max(total_sessions, 1))}/session" if total_sessions else "",
        ),
        ("Lines Added", _fmt_tokens(lines_added), ""),
        ("Lines Removed", _fmt_tokens(lines_removed), ""),
        ("Git Commits", _format_number(commits), f"{git_pushes} pushes" if git_pushes else ""),
        ("Files Modified", _fmt_tokens(files_modified), ""),
        ("Tool Errors", _format_number(tool_errors_total), ""),
        ("Interruptions", _format_number(interruptions_total), ""),
    ]
    if cache_hit_rate is not None:
        stat_items.append(
            ("Cache Efficiency", f"{float(cache_hit_rate):.1f}%", f"{_fmt_tokens(cache_tokens_saved)} tokens saved")
        )
    if subagent_sessions:
        stat_items.append(("Subagent Sessions", _format_number(subagent_sessions), ""))

    stat_cells = ""
    for label, value, sub in stat_items:
        sub_html = f'<span class="stat-sub">{_esc(sub)}</span>' if sub else ""
        stat_cells += f"""<div class="stat-item">
      <span class="stat-value">{_esc(value)}</span>
      <span class="stat-label">{_esc(label)}</span>
      {sub_html}
    </div>\n"""

    sections_html.append(f"""
<section class="stats-row-section">
  <div class="stats-row">
    {stat_cells}
  </div>
</section>""")

    if canonical_dirty:
        sections_html.append(f"""
<section class="content-section">
  <h2>Canonical vs Dirty Installs</h2>
  <div class="stats-row">
    <div class="stat-item"><span class="stat-value">{_format_number(canonical_dirty.get("canonical_sessions", 0))}</span><span class="stat-label">Canonical Sessions</span></div>
    <div class="stat-item"><span class="stat-value">{_format_number(canonical_dirty.get("dirty_sessions", 0))}</span><span class="stat-label">Dirty Sessions</span></div>
    <div class="stat-item"><span class="stat-value">{_format_number(canonical_dirty.get("canonical_users", 0))}</span><span class="stat-label">Canonical Users</span></div>
    <div class="stat-item"><span class="stat-value">{_format_number(canonical_dirty.get("dirty_users", 0))}</span><span class="stat-label">Dirty Users</span></div>
  </div>
</section>""")

    # ══════════════════════════════════════════════════════════════════════════
    # WHAT THEY WORK ON
    # ══════════════════════════════════════════════════════════════════════════
    areas = what_they_work_on.get("areas", [])
    if areas:
        area_cards = ""
        for area in areas:
            if isinstance(area, dict):
                area_cards += f"""
    <div class="area-card">
      <div class="area-header">
        <h4>{_esc(area.get("name", ""))}</h4>
        <span class="area-count">{area.get("sessions", 0)} sessions</span>
      </div>
      <p>{_esc(area.get("description", ""))}</p>
    </div>"""
        sections_html.append(f"""
<section>
  <h2>What They Work On</h2>
  <div class="areas-grid">{area_cards}
  </div>
</section>""")

    # ══════════════════════════════════════════════════════════════════════════
    # CHARTS
    # ══════════════════════════════════════════════════════════════════════════
    charts_html_parts = []

    # 1. Goal Categories
    goal_cats = facets.get("goal_categories", [])
    if goal_cats:
        items = [(name, count) for name, count in goal_cats[:8]]
        charts_html_parts.append(f"""
    <div class="chart-panel">
      <h3>Goal Categories</h3>
      {_render_count_bar_chart(items, "var(--blue)")}
    </div>""")

    # 2. Tool Distribution
    tool_dist = usage_patterns.get("tool_distribution", [])
    if tool_dist and isinstance(tool_dist, list):
        items = [(t.get("tool", t.get("name", "")), t.get("calls", t.get("invocations", 0))) for t in tool_dist[:8]]
        charts_html_parts.append(f"""
    <div class="chart-panel">
      <h3>Tool Distribution</h3>
      {_render_count_bar_chart(items, "var(--purple)")}
    </div>""")
    elif tools_list:
        items = [(t.get("name", ""), t.get("invocations", 0)) for t in tools_list[:8]]
        charts_html_parts.append(f"""
    <div class="chart-panel">
      <h3>Tool Distribution</h3>
      {_render_count_bar_chart(items, "var(--purple)")}
    </div>""")

    # 3. Languages
    if languages:
        lang_items = sorted(languages.items(), key=lambda x: -x[1])[:8]
        # Values might be fractions (0.42) or percentages (42)
        if lang_items and lang_items[0][1] <= 1.0:
            pct_items = [(name, val * 100) for name, val in lang_items]
        else:
            pct_items = [(name, val) for name, val in lang_items]
        charts_html_parts.append(f"""
    <div class="chart-panel">
      <h3>Languages</h3>
      {_render_pct_bar_chart(pct_items, "var(--green)")}
    </div>""")

    # 4. Outcomes
    outcomes = facets.get("outcomes") or {}
    if outcomes:
        outcome_items = sorted(outcomes.items(), key=lambda x: -x[1])[:6]
        charts_html_parts.append(f"""
    <div class="chart-panel">
      <h3>Outcomes</h3>
      {_render_count_bar_chart(outcome_items, "var(--amber)")}
    </div>""")

    # 5. Satisfaction
    satisfaction = facets.get("satisfaction") or {}
    if satisfaction:
        sat_items = sorted(satisfaction.items(), key=lambda x: -x[1])[:6]
        charts_html_parts.append(f"""
    <div class="chart-panel">
      <h3>Satisfaction</h3>
      {_render_count_bar_chart(sat_items, "#8b5cf6")}
    </div>""")

    # 6. Friction Types
    friction_types = facets.get("friction_types", [])
    if friction_types:
        items = [(name, count) for name, count in friction_types[:8]]
        charts_html_parts.append(f"""
    <div class="chart-panel">
      <h3>Friction Types</h3>
      {_render_count_bar_chart(items, "var(--red)")}
    </div>""")

    # 7. Tool Error Categories (from rich)
    if rich_error_cats:
        error_items = sorted(rich_error_cats.items(), key=lambda x: -x[1])[:8]
        charts_html_parts.append(f"""
    <div class="chart-panel">
      <h3>Tool Errors</h3>
      {_render_count_bar_chart(error_items, "var(--red)")}
    </div>""")

    # 8. Top Tools (from rich)
    if rich_top_tools:
        tool_items = [(name, count) for name, count in rich_top_tools[:10]]
        charts_html_parts.append(f"""
    <div class="chart-panel">
      <h3>Top Tools</h3>
      {_render_count_bar_chart(tool_items, "var(--purple)")}
    </div>""")

    # 9. Languages (from rich)
    if rich_top_langs:
        lang_items = [(name, count) for name, count in rich_top_langs[:10]]
        charts_html_parts.append(f"""
    <div class="chart-panel">
      <h3>Languages</h3>
      {_render_count_bar_chart(lang_items, "var(--green)")}
    </div>""")

    if charts_html_parts:
        sections_html.append(f"""
<section>
  <h2>Charts</h2>
  <div class="charts-grid">{"".join(charts_html_parts)}
  </div>
</section>""")

    # ══════════════════════════════════════════════════════════════════════════
    # USAGE PATTERNS
    # ══════════════════════════════════════════════════════════════════════════
    if usage_patterns:
        usage_narrative = usage_patterns.get("narrative", "")
        top_tasks = usage_patterns.get("top_tasks", [])
        session_profile = usage_patterns.get("session_profile") or {}

        top_tasks_html = ""
        if top_tasks and isinstance(top_tasks, list):
            tasks_items = []
            for t in top_tasks[:6]:
                if isinstance(t, dict):
                    name = _esc(t.get("name", ""))
                    count = t.get("count", "")
                    desc = _esc(t.get("description", ""))
                    tasks_items.append(f"<li><strong>{name}</strong> ({count}) — {desc}</li>")
                else:
                    tasks_items.append(f"<li>{_esc(str(t))}</li>")
            tasks_list = "".join(tasks_items)
            top_tasks_html = f'<div class="top-tasks"><h4>Top Tasks</h4><ul>{tasks_list}</ul></div>'

        profile_html = ""
        if session_profile:
            profile_html = f"""
      <div class="session-profile-card">
        <h4>Typical Session Profile</h4>
        <div class="profile-stats">
          <div class="profile-stat"><span class="profile-val">{session_profile.get("avg_duration_minutes", "?")}m</span><span class="profile-lbl">Duration</span></div>
          <div class="profile-stat"><span class="profile-val">{session_profile.get("avg_tool_calls", "?")}</span><span class="profile-lbl">Tool Calls</span></div>
          <div class="profile-stat"><span class="profile-val">{session_profile.get("avg_prompts", "?")}</span><span class="profile-lbl">Prompts</span></div>
          <div class="profile-stat"><span class="profile-val">{_esc(session_profile.get("session_type", "?"))}</span><span class="profile-lbl">Type</span></div>
        </div>
      </div>"""

        # Time-of-day heatmap
        hourly = time_of_day.get("hourly_counts") or {}
        heatmap_html = ""
        if hourly:
            max_hourly = max(hourly.values()) if hourly.values() else 1
            cells = ""
            for h in range(24):
                count = hourly.get(h, hourly.get(str(h), 0))
                intensity = count / max_hourly if max_hourly else 0
                opacity = 0.1 + (intensity * 0.9)
                label = f"{h:02d}:00"
                cells += (
                    f'<div class="heatmap-cell" style="opacity:{opacity}" title="{label}: {count} sessions">{h}</div>'
                )
            heatmap_html = f"""
      <div class="heatmap-section">
        <h4>Activity by Hour</h4>
        <div class="heatmap-row">{cells}</div>
        <div class="heatmap-legend"><span>Less</span><span>More</span></div>
      </div>"""

        sections_html.append(f"""
<section>
  <h2>Usage Patterns</h2>
  <p class="narrative">{_esc(usage_narrative)}</p>
  {top_tasks_html}
  {profile_html}
  {heatmap_html}
</section>""")

    # ══════════════════════════════════════════════════════════════════════════
    # INTERACTION STYLE (V4: the most personal section)
    # ══════════════════════════════════════════════════════════════════════════
    if interaction_style:
        style_narrative = interaction_style.get("narrative", "")
        key_pattern = interaction_style.get("key_pattern", "")
        if style_narrative:
            key_pattern_html = ""
            if key_pattern:
                key_pattern_html = f"""
      <div style="margin-top:16px;padding:14px 18px;background:var(--accent-bg);border:1px solid var(--accent-border);border-radius:var(--radius-sm);font-size:14px;font-style:italic;color:var(--accent)">
        \"{_esc(key_pattern)}\"
      </div>"""
            sections_html.append(f"""
<section>
  <h2>Interaction Style</h2>
  <div style="line-height:1.8;color:var(--text-secondary);font-size:14px">
    {_esc(style_narrative).replace(chr(10) + chr(10), "</p><p>").replace(chr(10), "<br>")}
  </div>
  {key_pattern_html}
</section>""")

    # ══════════════════════════════════════════════════════════════════════════
    # WHAT'S WORKING
    # ══════════════════════════════════════════════════════════════════════════
    if what_works:
        strengths = what_works.get("strengths", [])
        if strengths:
            strength_cards = ""
            for s in strengths:
                if isinstance(s, dict):
                    strength_cards += f"""
        <div class="strength-card">
          <h4>{_esc(s.get("title", ""))}</h4>
          <p>{_esc(s.get("description", ""))}</p>
        </div>"""
            sections_html.append(f"""
<section class="whats-working-section">
  <h2>What's Working</h2>
  <p class="section-intro">{_esc(what_works.get("intro", ""))}</p>
  <div class="strengths-grid">{strength_cards}
  </div>
</section>""")

    # ══════════════════════════════════════════════════════════════════════════
    # WHERE THINGS GO WRONG
    # ══════════════════════════════════════════════════════════════════════════
    if friction:
        categories = friction.get("categories", [])
        if categories:
            friction_cards = ""
            for c in categories:
                if isinstance(c, dict):
                    sev = c.get("severity", "low")
                    evidence = c.get("evidence", "")
                    evidence_html = f'<code class="evidence">{_esc(evidence)}</code>' if evidence else ""
                    friction_cards += f"""
        <div class="friction-card" style="border-left:4px solid {_severity_color(sev)}">
          <div class="friction-header">
            <h4>{_esc(c.get("title", ""))}</h4>
            <span class="severity-badge" style="background:{_severity_color(sev)};color:white;">{_esc(sev).upper()}</span>
          </div>
          <p>{_esc(c.get("description", ""))}</p>
          {evidence_html}
          <p class="impact"><strong>Impact:</strong> {_esc(c.get("impact", ""))}</p>
        </div>"""
            sections_html.append(f"""
<section>
  <h2>Where Things Go Wrong</h2>
  <p class="section-intro">{_esc(friction.get("intro", ""))}</p>
  <div class="friction-list">{friction_cards}
  </div>
</section>""")

    # ══════════════════════════════════════════════════════════════════════════
    # SUGGESTIONS (with copy buttons)
    # ══════════════════════════════════════════════════════════════════════════
    if suggestions:
        sugg_parts = []

        # V4: Config Additions (with checkboxes, like pi /insights)
        config_additions = suggestions.get("config_additions", [])
        if config_additions:
            cfg_cards = ""
            for _idx, c in enumerate(config_additions):
                if isinstance(c, dict):
                    addition = c.get("addition", "")
                    where = c.get("where", "system_prompt")
                    why = c.get("why", "")
                    addition_attr = html.escape(addition, quote=True) if addition else ""
                    cfg_cards += f"""
        <div class="suggestion-card" style="margin-bottom:10px">
          <div style="display:flex;align-items:flex-start;gap:10px">
            <input type="checkbox" class="cfg-check" checked data-addition="{addition_attr}" style="margin-top:3px;accent-color:var(--accent);width:15px;height:15px;flex-shrink:0">
            <div>
              <span class="meta-badge" style="margin-bottom:6px;display:inline-block">{_esc(where)}</span>
              <h4 style="font-size:14px;margin-bottom:4px">{_esc(addition)}</h4>
              <p class="suggestion-why"><em>Why: {_esc(why)}</em></p>
            </div>
          </div>
        </div>"""
            sugg_parts.append(f"""
      <h3 style="font-size:15px;font-weight:600;margin-bottom:12px">Config Additions</h3>
      <p style="font-size:12px;color:var(--text-muted);margin-bottom:16px">Select the ones you want, then copy them all at once.</p>
      <div id="config-list">{cfg_cards}</div>
      <button class="copy-btn" style="margin-top:12px;padding:8px 18px;font-size:12px" onclick="
        var checks=document.querySelectorAll('.cfg-check:checked');
        var lines=['# Agent config additions (generated by Observal Insights)',''];
        for(var ch of checks){{lines.push(ch.dataset.addition);lines.push('')}}
        navigator.clipboard.writeText(lines.join(String.fromCharCode(10)));
        this.textContent='Copied!';setTimeout(()=>this.textContent='Copy All Selected',1500)
      ">Copy All Selected</button>""")

        # V4: Features to Try (with code examples)
        features = suggestions.get("features_to_try", [])
        if features:
            feat_cards = ""
            for f in features:
                if isinstance(f, dict):
                    example = f.get("example", "")
                    example_attr = html.escape(example, quote=True) if example else ""
                    ref = f.get("component_ref") if isinstance(f.get("component_ref"), dict) else None
                    # A validated reuse suggestion shows the real component
                    # instead of generated content there is nothing to copy from.
                    reuse_html = ""
                    if ref:
                        reuse_html = (
                            '<div style="margin-top:10px;padding:10px 12px;border:1px solid var(--border);'
                            'border-radius:var(--radius-xs);background:var(--bg-alt)">'
                            '<div style="font-size:11px;text-transform:uppercase;letter-spacing:.04em;'
                            'color:var(--text-muted);margin-bottom:4px">Already in your registry</div>'
                            f'<div style="font-size:13px;font-weight:600">{_esc(ref.get("qualified_name") or ref.get("name", ""))}'
                            f'<span style="font-family:monospace;font-weight:400;color:var(--text-muted);margin-left:8px">'
                            f"v{_esc(ref.get('latest_version', ''))}</span></div></div>"
                        )
                    match_reason = f.get("match_reason") or ""
                    match_html = (
                        f'<p style="font-size:12px;color:var(--text-muted);margin-top:8px;font-style:italic">'
                        f"Why this fits: {_esc(match_reason)}</p>"
                        if match_reason
                        else ""
                    )
                    badge = "Reuse existing" if ref else f.get("feature", "")
                    example_html = (
                        f'<pre style="background:var(--bg-alt);border:1px solid var(--border);border-radius:var(--radius-xs);padding:12px 14px;font-family:monospace;font-size:12px;color:var(--text-secondary);white-space:pre-wrap;word-break:break-all;margin-top:10px">{_esc(example)}</pre><button class="copy-btn" style="margin-top:6px" onclick="navigator.clipboard.writeText(this.getAttribute(&quot;data-text&quot;)).then(()=>{{this.textContent=&quot;Copied!&quot;;setTimeout(()=>this.textContent=&quot;Copy&quot;,1500)}})" data-text="{example_attr}">Copy</button>'
                        if example and not ref
                        else ""
                    )
                    feat_cards += f"""
        <div class="suggestion-card">
          <span class="meta-badge" style="margin-bottom:8px;display:inline-block">{_esc(badge)}</span>
          <h4 style="font-size:14px;margin-bottom:4px">{_esc(f.get("one_liner", ""))}</h4>
          <p style="font-size:13px;color:var(--text-secondary);margin-top:6px">{_esc(f.get("why_for_you", ""))}</p>
          {reuse_html}
          {match_html}
          {example_html}
        </div>"""
            sugg_parts.append(f"""
      <h3 style="font-size:15px;font-weight:600;margin:24px 0 12px">Features to Try</h3>
      <div class="areas-grid">{feat_cards}</div>""")

        # V4: Usage Patterns (with copyable prompts)
        patterns = suggestions.get("usage_patterns", [])
        if patterns:
            pat_cards = ""
            for p in patterns:
                if isinstance(p, dict):
                    prompt = p.get("copyable_prompt", "")
                    prompt_attr = html.escape(prompt, quote=True) if prompt else ""
                    pat_cards += f"""
        <div class="suggestion-card">
          <h4 style="font-size:14px;margin-bottom:4px">{_esc(p.get("title", ""))}</h4>
          <p style="font-size:13px;color:var(--text-secondary);margin-top:6px">{_esc(p.get("suggestion", ""))}</p>
          <p style="font-size:12px;color:var(--text-muted);margin-top:8px">{_esc(p.get("detail", ""))}</p>
          {f'<pre style="background:var(--bg-alt);border:1px solid var(--border);border-radius:var(--radius-xs);padding:12px 14px;font-family:monospace;font-size:12px;color:var(--text-secondary);white-space:pre-wrap;word-break:break-all;margin-top:10px">{_esc(prompt)}</pre><button class="copy-btn" style="margin-top:6px" onclick="navigator.clipboard.writeText(this.getAttribute(&quot;data-text&quot;)).then(()=>{{this.textContent=&quot;Copied!&quot;;setTimeout(()=>this.textContent=&quot;Copy&quot;,1500)}})" data-text="{prompt_attr}">Copy</button>' if prompt else ""}
        </div>"""
            sugg_parts.append(f"""
      <h3 style="font-size:15px;font-weight:600;margin:24px 0 12px">Usage Patterns</h3>
      <div style="display:flex;flex-direction:column;gap:12px">{pat_cards}</div>""")

        # V3 fallback: items array (backward compatibility)
        items = suggestions.get("items", [])
        if items and not config_additions and not features and not patterns:
            suggestion_cards = ""
            for idx, item in enumerate(items, 1):
                if isinstance(item, dict):
                    priority = item.get("priority", "medium")
                    action_text = item.get("action", "")
                    action_attr = html.escape(action_text, quote=True) if action_text else ""
                    suggestion_cards += f"""
        <div class="suggestion-card">
          <div class="suggestion-header">
            <span class="suggestion-num">#{idx}</span>
            <h4>{_esc(item.get("title", ""))}</h4>
            <span class="priority-badge" style="background:{_priority_color(priority)};color:white;">{_esc(priority).upper()}</span>
          </div>
          <div class="suggestion-action">
            <div class="action-row">
              <span class="action-text">{_esc(action_text)}</span>
              <button class="copy-btn" onclick="navigator.clipboard.writeText(this.getAttribute('data-text')).then(()=>{{this.textContent='Copied!';setTimeout(()=>this.textContent='Copy',1500)}})" data-text="{action_attr}">Copy</button>
            </div>
          </div>
          <p class="suggestion-why"><em>{_esc(item.get("why", ""))}</em></p>
        </div>"""
            sugg_parts.append(f"""<div class="suggestions-list">{suggestion_cards}</div>""")

        if sugg_parts:
            sections_html.append(f"""
<section>
  <h2>Suggestions</h2>
  {"".join(sugg_parts)}
</section>""")

    # ══════════════════════════════════════════════════════════════════════════
    # REPEATED INSTRUCTIONS
    # ══════════════════════════════════════════════════════════════════════════
    repeated_instructions = facets.get("repeated_instructions", [])
    if repeated_instructions:
        rows = ""
        for item in repeated_instructions:
            if isinstance(item, dict):
                rows += f"""
          <tr>
            <td class="instruction-cell">{_esc(item.get("instruction", ""))}</td>
            <td class="freq-cell">{item.get("frequency", 0)}</td>
          </tr>"""
        sections_html.append(f"""
<section>
  <h2>Repeated Instructions</h2>
  <p class="section-intro">Instructions that appear across multiple sessions, indicating habits or persistent needs.</p>
  <table class="repeated-table">
    <thead><tr><th>Instruction</th><th>Frequency</th></tr></thead>
    <tbody>{rows}
    </tbody>
  </table>
</section>""")

    # ══════════════════════════════════════════════════════════════════════════
    # USAGE & COST ANALYSIS
    # ══════════════════════════════════════════════════════════════════════════
    if usage_cost or cost:
        cost_summary = usage_cost.get("summary", "")
        usage_cost.get("metrics") or {}
        model_breakdown = usage_cost.get("model_breakdown") or cost.get("cost_by_model") or {}
        opportunities = usage_cost.get("opportunities") or []

        model_rows = ""
        if model_breakdown:
            if isinstance(model_breakdown, dict):
                sorted_models = sorted(
                    model_breakdown.items(), key=lambda x: -(x[1] if isinstance(x[1], (int, float)) else 0)
                )
                for model, model_cost in sorted_models[:6]:
                    if isinstance(model_cost, (int, float)):
                        model_rows += f"<tr><td><code>{_esc(model)}</code></td><td>{_format_cost(model_cost)}</td></tr>"
            elif isinstance(model_breakdown, list):
                for item in model_breakdown[:6]:
                    if isinstance(item, dict):
                        model_name = item.get("model", "unknown")
                        model_cost = item.get("cost_usd", item.get("total_cost_usd", 0))
                        model_rows += (
                            f"<tr><td><code>{_esc(model_name)}</code></td><td>{_format_cost(model_cost)}</td></tr>"
                        )

        opp_html = ""
        if opportunities and isinstance(opportunities, list):
            for opp in opportunities:
                if isinstance(opp, str):
                    opp_html += f"<li>{_esc(opp)}</li>"
                elif isinstance(opp, dict):
                    opp_html += (
                        f"<li><strong>{_esc(opp.get('title', ''))}</strong>: {_esc(opp.get('description', ''))}</li>"
                    )

        cache_eff = cost.get("cache_efficiency_ratio", 0)
        if isinstance(cache_eff, (int, float)):
            cache_pct = round(float(cache_eff) * 100, 1) if cache_eff <= 1 else round(float(cache_eff), 1)
        else:
            cache_pct = 0

        sections_html.append(f"""
<section>
  <h2>Usage &amp; Cost Analysis</h2>
  {f'<p class="narrative">{_esc(cost_summary)}</p>' if cost_summary else ""}
  <div class="cost-grid">
    <div class="cost-card">
      <span class="cost-val">{_format_cost(cost.get("total_cost_usd"))}</span>
      <span class="cost-lbl">Total Cost</span>
    </div>
    <div class="cost-card">
      <span class="cost-val">{_format_cost(cost.get("avg_cost_per_session"))}</span>
      <span class="cost-lbl">Per Session</span>
    </div>
    <div class="cost-card">
      <span class="cost-val">{cache_pct}%</span>
      <span class="cost-lbl">Cache Hit Rate</span>
    </div>
    <div class="cost-card">
      <span class="cost-val">{_format_number(tokens.get("total_tokens", 0))}</span>
      <span class="cost-lbl">Total Tokens</span>
    </div>
  </div>
  {f'<div class="model-breakdown"><h4>Cost by Model</h4><table><thead><tr><th>Model</th><th>Cost</th></tr></thead><tbody>{model_rows}</tbody></table></div>' if model_rows else ""}
  {f'<div class="cost-opportunities"><h4>Optimization Opportunities</h4><ul>{opp_html}</ul></div>' if opp_html else ""}
</section>""")

    # ══════════════════════════════════════════════════════════════════════════
    # REGRESSION FLAGS
    # ══════════════════════════════════════════════════════════════════════════
    regressions_list = report.get("regressions") or []
    has_regression_narrative = regression and regression.get("has_previous_data")

    if has_regression_narrative or regressions_list:
        changes = regression.get("changes", []) if regression else []
        if not changes:
            changes = regressions_list

        if changes:
            change_rows = ""
            for ch in changes:
                if isinstance(ch, dict):
                    direction = ch.get("direction", "stable")
                    arrow = (
                        "&#8593;" if direction == "improved" else "&#8595;" if direction == "degraded" else "&#8594;"
                    )
                    color = (
                        "var(--green)"
                        if direction == "improved"
                        else "var(--red)"
                        if direction == "degraded"
                        else "var(--text-muted)"
                    )
                    mag = ch.get("magnitude_pct", 0)
                    change_rows += f"""
          <tr>
            <td>{_esc(ch.get("metric", ""))}</td>
            <td style="color:{color};font-weight:600;">{arrow} {_esc(direction)}</td>
            <td>{_esc(str(ch.get("previous_value", "")))}</td>
            <td>{_esc(str(ch.get("current_value", "")))}</td>
            <td>{mag:.1f}%</td>
            <td>{_esc(ch.get("significance", ""))}</td>
          </tr>"""
            sections_html.append(f"""
<section class="regression-section">
  <h2>Regression Flags</h2>
  <p class="narrative">{_esc(regression.get("summary", "Period-over-period changes detected."))}</p>
  <table class="regression-table">
    <thead><tr><th>Metric</th><th>Direction</th><th>Previous</th><th>Current</th><th>Change</th><th>Significance</th></tr></thead>
    <tbody>{change_rows}
    </tbody>
  </table>
</section>""")

    # ══════════════════════════════════════════════════════════════════════════
    # VERSION COMPARISON
    # ══════════════════════════════════════════════════════════════════════════
    if version_comparison:
        changes_html = ""
        for change in version_comparison.get("changes", [])[:8]:
            changes_html += f"""
    <div class="insight-card">
      <h4>{_esc(change.get("metric", "Change"))}: {_esc(change.get("direction", ""))}</h4>
      <p>{_esc(change.get("prior_value", "?"))} &rarr; {_esc(change.get("current_value", "?"))}</p>
      <p class="muted">Attribution: {_esc(change.get("attribution", "unknown"))} &middot; Risk: {_esc(change.get("risk", "none"))}</p>
      <p>{_esc(change.get("evidence", ""))}</p>
    </div>"""
        sections_html.append(f"""
<section class="content-section">
  <h2>Version Comparison</h2>
  <p>{_esc(version_comparison.get("summary", ""))}</p>
  <p class="muted">Confidence: {_esc(version_comparison.get("confidence", ""))}</p>
  <div class="insights-list">{changes_html}</div>
</section>""")

    # ══════════════════════════════════════════════════════════════════════════
    # FUN ENDING
    # ══════════════════════════════════════════════════════════════════════════
    if fun_ending and fun_ending.get("headline"):
        sections_html.append(f"""
<section class="fun-ending-section">
  <div class="fun-card">
    <h3>{_esc(fun_ending.get("headline", ""))}</h3>
    <p>{_esc(fun_ending.get("detail", ""))}</p>
  </div>
</section>""")

    # ══════════════════════════════════════════════════════════════════════════
    # ASSEMBLE DOCUMENT
    # ══════════════════════════════════════════════════════════════════════════
    body_content = "\n".join(sections_html)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    version_bits = []
    if agent_version:
        version_bits.append(f"Version v{_esc(agent_version)}")
    if comparison_agent_version:
        version_bits.append(f"Compared to v{_esc(comparison_agent_version)}")
    version_text = " &nbsp;&middot;&nbsp; ".join(version_bits)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Observal Agent Insights &mdash; {_esc(agent_name)} &mdash; {_esc(period_start)} to {_esc(period_end)}</title>
  <style>
    :root {{
      --bg: #fafaf9;
      --bg-alt: #f5f5f4;
      --card-bg: #ffffff;
      --text: #1c1917;
      --text-secondary: #44403c;
      --text-muted: #78716c;
      --border: #e7e5e4;
      --border-light: #f5f5f4;
      --green: #15803d;
      --green-bg: #f0fdf4;
      --green-border: #bbf7d0;
      --red: #b91c1c;
      --red-bg: #fef2f2;
      --red-border: #fecaca;
      --amber: #b45309;
      --amber-bg: #fffbeb;
      --amber-border: #fde68a;
      --blue: #1d4ed8;
      --blue-bg: #eff6ff;
      --blue-border: #bfdbfe;
      --accent: #c2410c;
      --accent-bg: #fff7ed;
      --accent-border: #fed7aa;
      --accent-light: #ea580c;
      --shadow-sm: 0 1px 2px rgba(28,25,23,0.03);
      --shadow: 0 1px 3px rgba(28,25,23,0.04), 0 1px 2px rgba(28,25,23,0.03);
      --shadow-md: 0 4px 6px -1px rgba(28,25,23,0.05), 0 2px 4px -2px rgba(28,25,23,0.03);
      --radius: 16px;
      --radius-sm: 10px;
      --radius-xs: 6px;
    }}

    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #1c1917;
        --bg-alt: #292524;
        --card-bg: #292524;
        --text: #fafaf9;
        --text-secondary: #d6d3d1;
        --text-muted: #a8a29e;
        --border: #44403c;
        --border-light: #292524;
        --green-bg: #052e16;
        --green-border: #166534;
        --red-bg: #450a0a;
        --red-border: #991b1b;
        --amber-bg: #451a03;
        --amber-border: #92400e;
        --blue-bg: #1e3a5f;
        --blue-border: #1d4ed8;
        --accent: #ea580c;
        --accent-bg: #431407;
        --accent-border: #9a3412;
        --accent-light: #fb923c;
        --shadow-sm: 0 1px 2px rgba(0,0,0,0.3);
        --shadow: 0 1px 3px rgba(0,0,0,0.4);
        --shadow-md: 0 4px 6px rgba(0,0,0,0.4);
      }}
    }}

    * {{ margin: 0; padding: 0; box-sizing: border-box; }}

    body {{
      font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.6;
      padding: 56px 24px;
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
      font-size: 15px;
    }}

    .container {{
      max-width: 960px;
      margin: 0 auto;
    }}

    /* ─── Header ─── */
    header {{
      text-align: center;
      margin-bottom: 48px;
      padding: 32px;
      background: var(--card-bg);
      border-radius: var(--radius);
      border: 1px solid var(--border);
      box-shadow: var(--shadow-md);
    }}

    .brand {{
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 2.5px;
      text-transform: uppercase;
      color: var(--accent);
      margin-bottom: 16px;
    }}

    header h1 {{
      font-size: 24px;
      font-weight: 600;
      margin-bottom: 8px;
      color: var(--text);
      letter-spacing: -0.02em;
    }}

    header .subtitle {{
      color: var(--text-muted);
      font-size: 14px;
      line-height: 1.5;
    }}

    /* ─── Sections ─── */
    section {{
      background: var(--card-bg);
      border-radius: var(--radius);
      padding: 32px;
      margin-bottom: 20px;
      border: 1px solid var(--border);
      box-shadow: var(--shadow);
    }}

    section h2 {{
      font-size: 17px;
      font-weight: 600;
      margin-bottom: 20px;
      color: var(--text);
      letter-spacing: -0.01em;
    }}

    .narrative {{
      color: var(--text-secondary);
      margin-bottom: 20px;
      white-space: pre-wrap;
      font-size: 14px;
      line-height: 1.7;
    }}

    .section-intro {{
      color: var(--text-muted);
      margin-bottom: 20px;
      font-size: 14px;
    }}

    /* ─── At a Glance ─── */
    .at-a-glance-section {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-top: 3px solid var(--accent);
    }}

    .glance-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 20px;
    }}

    .glance-header h2 {{ margin-bottom: 0; }}

    .health-badge {{
      font-size: 11px;
      font-weight: 700;
      padding: 5px 14px;
      border-radius: 20px;
      letter-spacing: 0.5px;
    }}

    .glance-grid {{
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 16px;
    }}

    .glance-item {{
      display: flex;
      gap: 12px;
      padding: 16px;
      border-radius: var(--radius-sm);
      align-items: flex-start;
    }}

    .glance-icon {{
      font-size: 20px;
      flex-shrink: 0;
      width: 32px;
      height: 32px;
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: 50%;
    }}

    .glance-item h4 {{
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin-bottom: 4px;
    }}

    .glance-item p {{
      font-size: 13px;
      color: var(--text-secondary);
      line-height: 1.5;
    }}

    .glance-good {{ background: var(--green-bg); border: 1px solid var(--green-border); }}
    .glance-good h4 {{ color: var(--green); }}
    .glance-good .glance-icon {{ background: var(--green-border); color: var(--green); }}

    .glance-bad {{ background: var(--red-bg); border: 1px solid var(--red-border); }}
    .glance-bad h4 {{ color: var(--red); }}
    .glance-bad .glance-icon {{ background: var(--red-border); color: var(--red); }}

    .glance-action {{ background: var(--blue-bg); border: 1px solid var(--blue-border); }}
    .glance-action h4 {{ color: var(--blue); }}
    .glance-action .glance-icon {{ background: var(--blue-border); color: var(--blue); }}

    .glance-ambitious {{ background: var(--accent-bg); border: 1px solid var(--accent-border); }}
    .glance-ambitious h4 {{ color: var(--accent); }}
    .glance-ambitious .glance-icon {{ background: var(--accent-border); color: var(--accent); }}

    /* ─── Stats Row ─── */
    .stats-row-section {{
      background: var(--card-bg);
      padding: 20px 28px;
    }}

    .stats-row {{
      display: grid;
      grid-template-columns: repeat(6, 1fr);
      gap: 8px;
    }}

    .stat-item {{
      text-align: center;
      padding: 16px 8px;
      border-radius: var(--radius-sm);
      background: var(--bg-alt);
      border: 1px solid var(--border);
    }}

    .stat-value {{
      display: block;
      font-size: 22px;
      font-weight: 700;
      color: var(--text);
      line-height: 1.2;
    }}

    .stat-value.stat-positive {{ font-size: 14px; color: var(--green); }}
    .stat-value.stat-negative {{ font-size: 14px; color: var(--red); }}

    .stat-label {{
      display: block;
      font-size: 11px;
      color: var(--text-muted);
      margin-top: 4px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      font-weight: 600;
    }}

    .stat-sub {{
      display: block;
      font-size: 10px;
      color: var(--text-muted);
      margin-top: 2px;
    }}

    /* ─── Areas ─── */
    .areas-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 12px;
    }}

    .area-card {{
      padding: 16px;
      border-radius: var(--radius-sm);
      background: var(--bg-alt);
      border: 1px solid var(--border);
    }}

    .area-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 8px;
    }}

    .area-header h4 {{
      font-size: 14px;
      font-weight: 600;
    }}

    .area-count {{
      font-size: 12px;
      color: var(--text-muted);
      background: var(--card-bg);
      padding: 2px 8px;
      border-radius: 10px;
      border: 1px solid var(--border);
    }}

    .area-card p {{
      font-size: 13px;
      color: var(--text-muted);
    }}

    /* ─── Charts ─── */
    .charts-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
      gap: 20px;
    }}

    .chart-panel {{
      background: var(--bg-alt);
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      padding: 20px;
    }}

    .chart-panel h3 {{
      font-size: 13px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--text-muted);
      margin-bottom: 16px;
    }}

    .chart-bar-row {{
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 8px;
    }}

    .chart-label {{
      font-size: 12px;
      min-width: 90px;
      max-width: 90px;
      color: var(--text-secondary);
      font-weight: 500;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}

    .chart-bar-container {{
      flex: 1;
      height: 22px;
      background: var(--border-light);
      border-radius: 4px;
      overflow: hidden;
    }}

    .chart-bar {{
      height: 100%;
      border-radius: 4px;
      transition: width 0.3s ease;
      min-width: 2px;
    }}

    .chart-value {{
      font-size: 12px;
      font-weight: 700;
      min-width: 45px;
      text-align: right;
      color: var(--text);
    }}

    /* ─── Usage Patterns ─── */
    .top-tasks {{ margin: 16px 0; }}
    .top-tasks h4 {{ font-size: 14px; font-weight: 600; margin-bottom: 8px; }}
    .top-tasks ul {{ padding-left: 20px; }}
    .top-tasks li {{ font-size: 13px; color: var(--text-secondary); margin-bottom: 4px; }}

    .session-profile-card {{
      background: var(--bg-alt);
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      padding: 16px;
      margin-top: 16px;
    }}

    .session-profile-card h4 {{
      font-size: 13px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--text-muted);
      margin-bottom: 12px;
    }}

    .profile-stats {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
    }}

    .profile-stat {{
      text-align: center;
    }}

    .profile-val {{
      display: block;
      font-size: 20px;
      font-weight: 700;
      color: var(--accent);
    }}

    .profile-lbl {{
      display: block;
      font-size: 11px;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.3px;
      margin-top: 2px;
    }}

    /* ─── Heatmap ─── */
    .heatmap-section {{ margin-top: 20px; }}
    .heatmap-section h4 {{ font-size: 14px; font-weight: 600; margin-bottom: 10px; }}

    .heatmap-row {{
      display: grid;
      grid-template-columns: repeat(24, 1fr);
      gap: 3px;
    }}

    .heatmap-cell {{
      aspect-ratio: 1;
      background: var(--accent);
      border-radius: 4px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 9px;
      color: white;
      font-weight: 600;
    }}

    .heatmap-legend {{
      display: flex;
      justify-content: space-between;
      font-size: 10px;
      color: var(--text-muted);
      margin-top: 6px;
      padding: 0 2px;
    }}

    /* ─── Strengths ─── */
    .strengths-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 12px;
    }}

    .strength-card {{
      background: var(--green-bg);
      border: 1px solid var(--green-border);
      padding: 16px;
      border-radius: var(--radius-sm);
    }}

    .strength-card h4 {{
      color: var(--green);
      font-size: 14px;
      margin-bottom: 6px;
    }}

    .strength-card p {{
      font-size: 13px;
      color: var(--text-secondary);
    }}

    /* ─── Friction ─── */
    .friction-list {{
      display: flex;
      flex-direction: column;
      gap: 14px;
    }}

    .friction-card {{
      padding: 18px;
      border-radius: var(--radius-sm);
      background: var(--bg-alt);
      border: 1px solid var(--border);
    }}

    .friction-header {{
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 10px;
    }}

    .friction-header h4 {{ flex: 1; font-size: 15px; }}

    .severity-badge, .priority-badge {{
      font-size: 10px;
      padding: 3px 10px;
      border-radius: 12px;
      font-weight: 700;
      letter-spacing: 0.5px;
    }}

    .friction-card p {{
      font-size: 13px;
      color: var(--text-secondary);
      margin-bottom: 6px;
    }}

    .evidence {{
      display: block;
      background: var(--bg-alt);
      color: var(--text-secondary);
      padding: 10px 14px;
      border-radius: var(--radius-xs);
      border: 1px solid var(--border);
      font-size: 12px;
      margin: 10px 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: 'SF Mono', 'Fira Code', 'Menlo', monospace;
    }}

    .impact {{
      color: var(--text-muted);
      font-size: 12px;
      margin-top: 8px;
    }}

    /* ─── Suggestions ─── */
    .suggestions-list {{
      display: flex;
      flex-direction: column;
      gap: 14px;
    }}

    .suggestion-card {{
      padding: 18px;
      border-radius: var(--radius-sm);
      background: var(--bg-alt);
      border: 1px solid var(--border);
    }}

    .suggestion-header {{
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 12px;
    }}

    .suggestion-num {{
      font-size: 18px;
      font-weight: 700;
      color: var(--accent);
      min-width: 36px;
    }}

    .suggestion-header h4 {{ flex: 1; font-size: 15px; }}

    .suggestion-action {{
      background: var(--card-bg);
      padding: 12px 14px;
      border-radius: var(--radius-xs);
      border: 1px solid var(--border);
      margin-bottom: 10px;
    }}

    .action-row {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }}

    .action-text {{
      font-size: 13px;
      color: var(--text-secondary);
      flex: 1;
    }}

    .copy-btn {{
      background: var(--accent);
      color: white;
      border: none;
      padding: 5px 12px;
      border-radius: var(--radius-xs);
      font-size: 11px;
      font-weight: 600;
      cursor: pointer;
      flex-shrink: 0;
      transition: background 0.2s;
    }}

    .copy-btn:hover {{ background: #9a3412; }}
    .copy-btn:active {{ background: #7c2d12; }}

    .suggestion-why {{
      font-size: 13px;
      color: var(--text-muted);
    }}

    .suggestion-meta {{
      display: flex;
      gap: 8px;
      margin-top: 10px;
      flex-wrap: wrap;
    }}

    .meta-badge {{
      font-size: 10px;
      padding: 3px 8px;
      border-radius: 10px;
      background: var(--bg);
      border: 1px solid var(--border);
      color: var(--text-muted);
      font-weight: 600;
    }}

    /* ─── Repeated Instructions Table ─── */
    .repeated-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}

    .repeated-table thead {{
      background: var(--bg-alt);
    }}

    .repeated-table th {{
      padding: 10px 14px;
      text-align: left;
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--text-muted);
      border-bottom: 2px solid var(--border);
    }}

    .repeated-table td {{
      padding: 12px 14px;
      border-bottom: 1px solid var(--border);
    }}

    .instruction-cell {{
      color: var(--text-secondary);
    }}

    .freq-cell {{
      font-weight: 700;
      color: var(--purple);
      width: 100px;
      text-align: center;
    }}

    /* ─── Cost Analysis ─── */
    .cost-grid {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
      margin-bottom: 20px;
    }}

    .cost-card {{
      text-align: center;
      padding: 16px;
      border-radius: var(--radius-sm);
      background: var(--bg-alt);
      border: 1px solid var(--border);
    }}

    .cost-val {{
      display: block;
      font-size: 22px;
      font-weight: 700;
      color: var(--text);
    }}

    .cost-lbl {{
      display: block;
      font-size: 11px;
      color: var(--text-muted);
      margin-top: 4px;
      text-transform: uppercase;
      letter-spacing: 0.3px;
      font-weight: 600;
    }}

    .model-breakdown {{
      margin-top: 16px;
    }}

    .model-breakdown h4 {{
      font-size: 14px;
      font-weight: 600;
      margin-bottom: 8px;
    }}

    .cost-opportunities {{
      margin-top: 16px;
      background: var(--amber-bg);
      border: 1px solid var(--amber-border);
      border-radius: var(--radius-sm);
      padding: 16px;
    }}

    .cost-opportunities h4 {{
      font-size: 13px;
      font-weight: 700;
      color: var(--amber);
      margin-bottom: 8px;
    }}

    .cost-opportunities ul {{
      padding-left: 18px;
    }}

    .cost-opportunities li {{
      font-size: 13px;
      color: var(--text-secondary);
      margin-bottom: 4px;
    }}

    /* ─── Tables (generic) ─── */
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}

    thead {{ background: var(--bg-alt); }}

    th, td {{
      padding: 10px 14px;
      text-align: left;
      border-bottom: 1px solid var(--border);
    }}

    th {{
      font-weight: 700;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--text-muted);
    }}

    code {{
      background: var(--bg-alt);
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 12px;
      font-family: 'SF Mono', 'Fira Code', monospace;
    }}

    /* ─── Regression ─── */
    .regression-section {{
      border-left: 4px solid var(--amber);
    }}

    .regression-table th, .regression-table td {{
      font-size: 12px;
      padding: 8px 10px;
    }}

    /* ─── Fun Ending ─── */
    .fun-ending-section {{
      background: var(--accent-bg);
      border: 1px solid var(--accent-border);
    }}

    .fun-card {{
      text-align: center;
      padding: 28px;
    }}

    .fun-card h3 {{
      font-size: 18px;
      font-weight: 600;
      color: var(--accent);
      margin-bottom: 10px;
    }}

    .fun-card p {{
      font-size: 14px;
      color: var(--text-secondary);
      max-width: 600px;
      margin: 0 auto;
    }}

    /* ─── Footer ─── */
    footer {{
      text-align: center;
      color: var(--text-muted);
      font-size: 12px;
      margin-top: 56px;
      padding: 24px 0;
      border-top: 1px solid var(--border);
    }}

    footer .footer-brand {{
      font-weight: 600;
      letter-spacing: 2px;
      text-transform: uppercase;
      color: var(--accent);
      font-size: 10px;
      margin-bottom: 4px;
    }}

    /* ─── Responsive ─── */
    @media (max-width: 768px) {{
      body {{ padding: 24px 12px; }}
      section {{ padding: 20px 16px; }}

      .stats-row {{ grid-template-columns: repeat(3, 1fr); }}
      .glance-grid {{ grid-template-columns: 1fr; }}
      .charts-grid {{ grid-template-columns: 1fr; }}
      .strengths-grid {{ grid-template-columns: 1fr; }}
      .areas-grid {{ grid-template-columns: 1fr; }}
      .cost-grid {{ grid-template-columns: repeat(2, 1fr); }}
      .profile-stats {{ grid-template-columns: repeat(2, 1fr); }}
      .heatmap-row {{ grid-template-columns: repeat(12, 1fr); }}
      .heatmap-cell:nth-child(n+13) {{ display: none; }}
    }}

    @media (max-width: 480px) {{
      .stats-row {{ grid-template-columns: repeat(2, 1fr); }}
      .stat-value {{ font-size: 18px; }}
      header h1 {{ font-size: 20px; }}
    }}

    /* ─── Print ─── */
    @media print {{
      body {{ padding: 20px; background: white; }}
      section {{ box-shadow: none; break-inside: avoid; border: 1px solid #ddd; }}
      .copy-btn {{ display: none; }}
      header {{ box-shadow: none; }}
      .at-a-glance-section {{ background: white; }}
      .fun-ending-section {{ background: white; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="brand">OBSERVAL AGENT INSIGHTS</div>
      <h1>{_esc(agent_name)}</h1>
      <p class="subtitle">
        {version_text + " &nbsp;&middot;&nbsp;" if version_text else ""}
        Period: {_esc(period_start)} &mdash; {_esc(period_end)} &nbsp;&middot;&nbsp;
        {sessions_analyzed} sessions analyzed &nbsp;&middot;&nbsp;
        Report {_esc(str(report_id)[:8])}
      </p>
    </header>

    {body_content}

    <footer>
      <div class="footer-brand">OBSERVAL</div>
      <p>Generated {now_str}</p>
    </footer>
  </div>
</body>
</html>"""
