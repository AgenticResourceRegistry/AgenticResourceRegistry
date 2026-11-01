# SPDX-FileCopyrightText: 2026 Dheirav <dheirav2005@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""REGRESSION (OBS-1486): the HTML export must not reach the network.

The export linked Google Fonts, so opening a shared report told Google the
viewer's IP and that the report was opened, and the font silently failed on
air-gapped installs. The file has to render from what it contains.
"""

from __future__ import annotations

import re


def _report() -> dict:
    return {
        "id": "report-1",
        "agent_name": "pr-verify-qa",
        "period_start": "2026-10-17T00:00:00Z",
        "period_end": "2026-10-18T00:00:00Z",
        "sessions_analyzed": 2,
        "metrics": {"rich": {"total_sessions": 2, "total_messages": 10, "top_tools": [["bash", 4]]}},
        "narrative": {"at_a_glance": {"health": "good", "whats_working": "Pushes verify."}},
        "facets_summary": {},
    }


def test_export_loads_no_remote_resources():
    from services.insights.html_export import render_report_html

    html = render_report_html(_report())

    assert "fonts.googleapis.com" not in html
    assert "fonts.gstatic.com" not in html
    assert not re.search(r"""(?:href|src)\s*=\s*["']?(?:https?:)?//""", html, re.IGNORECASE)
    assert not re.search(r"@import|url\(\s*[\"']?(?:https?:)?//", html, re.IGNORECASE)


def test_export_uses_a_system_font_stack():
    from services.insights.html_export import render_report_html

    html = render_report_html(_report())

    body_font = re.search(r"body\s*\{[^}]*font-family:\s*([^;]+);", html)
    assert body_font is not None
    assert "Inter" not in body_font.group(1)
    assert body_font.group(1).startswith("system-ui")
