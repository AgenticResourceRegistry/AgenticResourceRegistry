# SPDX-FileCopyrightText: 2026 Sarthak Bobde <bobdesarthak@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Cursor session parser against a fixture of Cursor transcript lines."""

from __future__ import annotations

import json
from pathlib import Path

from services.session_parsers import parse_raw_events
from services.session_parsers.cursor import parse_rows

FIXTURE = Path(__file__).parent / "fixtures" / "cursor_session.jsonl"


def _rows() -> list[dict]:
    return [
        {
            "raw_line": line,
            "harness": "cursor",
            "timestamp": "2026-11-13 10:02:00.000",
            "ingested_at": "2026-11-13 10:02:01.000",
        }
        for line in FIXTURE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_fixture_parses_into_events_without_raw_line_fallthrough() -> None:
    rows = _rows()
    assert 5 <= len(rows) <= 10
    assert all(json.loads(row["raw_line"])["role"] in {"user", "assistant"} for row in rows)

    events = parse_rows(rows)

    assert [event["event_name"] for event in events] == [
        "hook_userpromptsubmit",
        "hook_assistant_response",
        "hook_posttooluse",
        "hook_posttooluse",
        "hook_assistant_response",
        "hook_userpromptsubmit",
        "hook_posttooluse",
    ]
    assert {event["service_name"] for event in events} == {"cursor"}


def test_cursor_xml_tags_are_stripped_from_user_text() -> None:
    prompts = [event for event in parse_rows(_rows()) if event["event_name"] == "hook_userpromptsubmit"]

    assert [event["attributes"]["tool_input"] for event in prompts] == [
        "Fix the failing test in utils.py",
        "src/utils.py\nNow run the tests",
    ]
    for event in prompts:
        text = event["attributes"]["tool_input"]
        for tag in ("timestamp", "user_query", "attached_files"):
            assert f"<{tag}>" not in text
            assert f"</{tag}>" not in text
        assert event["body"] == text[:100]


def test_tool_results_are_paired_with_their_tool_use() -> None:
    tool_events = [event for event in parse_rows(_rows()) if event["event_name"] == "hook_posttooluse"]

    paired = {
        event["attributes"]["tool_use_id"]: (
            event["attributes"]["tool_name"],
            json.loads(event["attributes"]["tool_input"]),
            event["attributes"]["tool_response"],
        )
        for event in tool_events
    }
    assert paired == {
        "toolu_cursor_01": ("Read", {"path": "src/utils.py"}, "def add(a, b):\n    return a - b"),
        "toolu_cursor_02": (
            "StrReplace",
            {"path": "src/utils.py", "old_string": "return a - b", "new_string": "return a + b"},
            "The file src/utils.py has been updated.",
        ),
        "toolu_cursor_03": ("Shell", {"command": "pytest tests/test_utils.py"}, "3 passed in 0.12s"),
    }


def test_cursor_rows_dispatch_to_the_cursor_parser() -> None:
    rows = _rows()

    assert parse_raw_events(rows) == parse_rows(rows)
