# SPDX-FileCopyrightText: 2026 Santhiya Manivannan <70739919+sanraj2000@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for html_export.py — self-contained HTML report rendering."""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("DEPLOYMENT_MODE") != "enterprise",
    reason="Insights html_export tests require DEPLOYMENT_MODE=enterprise",
)


def _minimal_report(**overrides) -> dict:
    base = {
        "id": "rpt-001",
        "agent_id": "agent-123",
        "agent_name": "My Agent",
        "status": "completed",
        "period_start": "2026-01-01",
        "period_end": "2026-01-31",
        "sessions_analyzed": 10,
        "metrics": {},
        "narrative": {},
        "facets_summary": {},
    }
    base.update(overrides)
    return base


class TestHelperFunctions:
    def test_esc_escapes_html_entities(self):
        from services.insights.html_export import _esc

        assert _esc("<script>alert('xss')</script>") == "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;"

    def test_esc_none_returns_empty(self):
        from services.insights.html_export import _esc

        assert _esc(None) == ""

    def test_format_cost_none_returns_zero(self):
        from services.insights.html_export import _format_cost

        assert _format_cost(None) == "$0.00"

    def test_format_cost_small_value(self):
        from services.insights.html_export import _format_cost

        assert _format_cost(0.0023) == "$0.0023"

    def test_format_cost_normal_value(self):
        from services.insights.html_export import _format_cost

        assert _format_cost(3.5) == "$3.50"

    def test_format_number_none(self):
        from services.insights.html_export import _format_number

        assert _format_number(None) == "0"

    def test_format_number_large_int(self):
        from services.insights.html_export import _format_number

        assert _format_number(1_234_567) == "1,234,567"

    def test_format_number_float_whole(self):
        from services.insights.html_export import _format_number

        assert _format_number(5.0) == "5"

    def test_format_number_float_fraction(self):
        from services.insights.html_export import _format_number

        assert _format_number(5.5) == "5.5"

    def test_format_duration_zero(self):
        from services.insights.html_export import _format_duration_hours

        assert _format_duration_hours(0) == "0h"

    def test_format_duration_minutes(self):
        from services.insights.html_export import _format_duration_hours

        assert _format_duration_hours(1800) == "30m"

    def test_format_duration_hours(self):
        from services.insights.html_export import _format_duration_hours

        assert _format_duration_hours(7200) == "2.0h"

    def test_severity_color_high(self):
        from services.insights.html_export import _severity_color

        assert _severity_color("high") == "#dc2626"

    def test_severity_color_unknown(self):
        from services.insights.html_export import _severity_color

        # Unknown severity returns gray
        assert _severity_color("critical") == "#6b7280"

    def test_priority_color_low(self):
        from services.insights.html_export import _priority_color

        assert _priority_color("low") == "#16a34a"

    def test_health_badge_healthy_contains_green(self):
        from services.insights.html_export import _health_badge

        badge = _health_badge("healthy")
        assert "#16a34a" in badge
        assert "HEALTHY" in badge

    def test_health_badge_concerning_contains_red(self):
        from services.insights.html_export import _health_badge

        badge = _health_badge("concerning")
        assert "#dc2626" in badge

    def test_render_bar_chart_empty_returns_empty(self):
        from services.insights.html_export import _render_bar_chart

        assert _render_bar_chart([]) == ""

    def test_render_bar_chart_produces_html(self):
        from services.insights.html_export import _render_bar_chart

        html = _render_bar_chart([("bash", 10), ("edit", 5)])
        assert "bash" in html
        assert "chart-bar" in html

    def test_render_count_bar_chart_scales_to_max(self):
        from services.insights.html_export import _render_count_bar_chart

        html = _render_count_bar_chart([("a", 100), ("b", 50)])
        # 'a' should have width 100%, 'b' should have width 50%
        assert "100.0%" in html
        assert "50.0%" in html

    def test_render_pct_bar_chart_uses_values_as_pct(self):
        from services.insights.html_export import _render_pct_bar_chart

        html = _render_pct_bar_chart([("TypeScript", 75.0)])
        assert "75.0%" in html


class TestRenderReportHtml:
    def test_returns_html_string(self):
        from services.insights.html_export import render_report_html

        html = render_report_html(_minimal_report())
        assert isinstance(html, str)
        assert html.startswith("<!DOCTYPE html>") or html.startswith("<!")

    def test_contains_agent_name(self):
        from services.insights.html_export import render_report_html

        html = render_report_html(_minimal_report(agent_name="SuperAgent"))
        assert "SuperAgent" in html

    def test_contains_period_dates(self):
        from services.insights.html_export import render_report_html

        html = render_report_html(_minimal_report())
        assert "2026-01-01" in html
        assert "2026-01-31" in html

    def test_handles_iso_datetime_period_start(self):
        from services.insights.html_export import render_report_html

        html = render_report_html(_minimal_report(period_start="2026-01-01T00:00:00Z"))
        assert "2026-01-01" in html

    def test_at_a_glance_section_rendered(self):
        from services.insights.html_export import render_report_html

        report = _minimal_report(
            narrative={
                "at_a_glance": {
                    "health": "healthy",
                    "whats_working": "bash usage is excellent",
                    "whats_hindering": "some tool errors",
                    "quick_win": "add a scope guard hook",
                    "ambitious_workflows": "parallel subagents",
                }
            }
        )
        html = render_report_html(report)
        assert "bash usage is excellent" in html
        assert "scope guard hook" in html
        assert "HEALTHY" in html

    def test_xss_in_agent_name_is_escaped(self):
        from services.insights.html_export import render_report_html

        html = render_report_html(_minimal_report(agent_name="<script>alert(1)</script>"))
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_goal_categories_chart_rendered(self):
        from services.insights.html_export import render_report_html

        report = _minimal_report(
            facets_summary={
                "goal_categories": [("fix_bug", 10), ("implement_feature", 5)],
            }
        )
        html = render_report_html(report)
        assert "fix_bug" in html
        assert "Goal Categories" in html

    def test_friction_section_rendered(self):
        from services.insights.html_export import render_report_html

        report = _minimal_report(
            narrative={
                "friction_analysis": {
                    "intro": "You have persistent friction.",
                    "categories": [
                        {
                            "title": "Wrong Approach",
                            "severity": "high",
                            "description": "Agent went off-track",
                            "examples": ["example 1"],
                            "evidence": "3 out of 10 sessions",
                            "impact": "wasted 2 hours",
                        }
                    ],
                }
            }
        )
        html = render_report_html(report)
        assert "persistent friction" in html
        assert "Wrong Approach" in html

    def test_suggestions_rendered(self):
        from services.insights.html_export import render_report_html

        report = _minimal_report(
            narrative={
                "suggestions": {
                    "config_additions": [
                        {
                            "action_type": "modify_prompt",
                            "addition": "Always show diffs before editing",
                            "why": "User repeatedly asks for this",
                            "where": "system_prompt",
                            "confidence": "high",
                            "risk": "low",
                        }
                    ],
                    "features_to_try": [],
                    "usage_patterns": [],
                }
            }
        )
        html = render_report_html(report)
        assert "Always show diffs before editing" in html

    def test_rich_metrics_rendered_in_stats_row(self):
        from services.insights.html_export import render_report_html

        report = _minimal_report(
            metrics={
                "rich": {
                    "total_messages": 500,
                    "git_commits": 42,
                    "lines_added": 10_000,
                    "active_hours": 8.5,
                    "days_active": 15,
                }
            }
        )
        html = render_report_html(report)
        assert "500" in html
        assert "42" in html

    def test_empty_narrative_renders_without_crash(self):
        from services.insights.html_export import render_report_html

        # Should not raise even with completely empty data
        html = render_report_html(_minimal_report())
        assert len(html) > 100

    def test_fun_ending_rendered_when_present(self):
        from services.insights.html_export import render_report_html

        report = _minimal_report(
            narrative={
                "fun_ending": {
                    "headline": "You accidentally fixed two bugs at once",
                    "detail": "Session 42 was a surprise",
                }
            }
        )
        html = render_report_html(report)
        assert "accidentally fixed two bugs" in html
