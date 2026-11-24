# SPDX-FileCopyrightText: 2026 Olivia Cruz <olivia.cruz150@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Cursor JSONL session parser.

Covers:
A normal user and assistant session rows producing normalized trace events.
Empty raw_line rows and empty input lists.
Malformed JSON lines, unknown roles, and Cursor XML tag stripping edge cases.
"""

from __future__ import annotations

import json
from pathlib import Path

from services.session_parsers.cursor import parse_rows

_FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fuzz" / "corpus" / "session_jsonl_fuzzer" / "cursor.jsonl"


def _row(
    raw_line: str,
    *,
    harness: str = "cursor",
    timestamp: str = "2026-08-07 12:00:00.000",
    ingested_at: str = "2026-08-07 12:00:01.000",
    **extra: object,
) -> dict:
    """Build a session parser input row."""
    return {
        "raw_line": raw_line,
        "harness": harness,
        "timestamp": timestamp,
        "ingested_at": ingested_at,
        **extra,
    }


def _user_line(text: str, *, timestamp: str = "2026-08-07T12:00:00.000Z") -> str:
    """Serialize a Cursor user message JSONL record."""
    return json.dumps(
        {
            "role": "user",
            "timestamp": timestamp,
            "message": {"content": [{"type": "text", "text": text}]},
        }
    )


def _assistant_line(text: str, *, timestamp: str = "2026-08-07T12:00:05.000Z") -> str:
    """Serialize a Cursor assistant message JSONL record."""
    return json.dumps(
        {
            "role": "assistant",
            "timestamp": timestamp,
            "message": {"content": [{"type": "text", "text": text}]},
        }
    )


class TestCursorSessionParser:
    """Behavioral tests for Cursor session JSONL row parsing."""

    def test_empty_rows_list_returns_no_events(self) -> None:
        events = parse_rows([])

        assert events == []

    def test_empty_raw_line_falls_back_to_basic_event(self) -> None:
        rows = [_row("", content_preview="empty row")]

        events = parse_rows(rows)

        assert len(events) == 1
        assert events[0]["body"] == "empty row"

    def test_normal_user_and_assistant_session_emits_normalized_events(self) -> None:
        rows = [
            _row(_user_line("Explain this helper")),
            _row(_assistant_line("It normalizes rows.")),
        ]

        events = parse_rows(rows)

        assert [event["event_name"] for event in events] == [
            "hook_userpromptsubmit",
            "hook_assistant_response",
        ]
        assert "Explain this helper" in str(events[0]["attributes"]), events[0]["attributes"]
        assert "It normalizes rows." in str(events[1]["attributes"]), events[1]["attributes"]

    def test_strips_cursor_xml_tags_from_user_text_blocks(self) -> None:
        tagged_text = "<user_query>Fix the bug</user_query>"
        rows = [_row(_user_line(tagged_text))]

        events = parse_rows(rows)

        assert len(events) == 1
        assert events[0]["event_name"] == "hook_userpromptsubmit"
        rendered_attrs = str(events[0]["attributes"])
        assert "<user_query>" not in rendered_attrs, rendered_attrs
        assert "Fix the bug" in rendered_attrs, rendered_attrs

    def test_malformed_json_line_falls_back_to_basic_event(self) -> None:
        rows = [_row("{not valid json", content_preview="malformed fallback")]

        events = parse_rows(rows)

        assert len(events) == 1
        assert events[0]["body"] == "malformed fallback"

    def test_unknown_role_falls_back_to_basic_event(self) -> None:
        rows = [_row(json.dumps({"role": "system_note", "content": "hi"}), content_preview="unknown role")]

        events = parse_rows(rows)

        assert len(events) == 1
        assert events[0]["body"] == "unknown role"

    def test_fuzzer_corpus_fixture_parses_without_error_when_present(self) -> None:
        if not _FIXTURE_PATH.exists():
            return
        lines = [line for line in _FIXTURE_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not lines:
            return
        rows = [_row(line) for line in lines[:5]]

        events = parse_rows(rows)

        assert len(events) == len(rows)
