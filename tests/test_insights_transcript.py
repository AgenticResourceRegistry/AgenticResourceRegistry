# SPDX-FileCopyrightText: 2026 Santhiya Manivannan <70739919+sanraj2000@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for transcript.py — session transcript builder from ClickHouse events.

Covers: _format_rows, _format_event, extraction helpers, build_session_transcript
(short/long/summarized paths).
"""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("DEPLOYMENT_MODE") != "enterprise",
    reason="Insights transcript tests require DEPLOYMENT_MODE=enterprise",
)


def _row(event_type: str, raw_line: dict, tool_name: str = "") -> dict:
    return {
        "line_offset": 0,
        "event_type": event_type,
        "tool_name": tool_name,
        "raw_line": json.dumps(raw_line),
    }


class TestFormatEvent:
    def test_user_prompt_string_content(self):
        from services.insights.transcript import _format_event

        parsed = {"message": {"content": "fix the bug"}}
        line = _format_event("user_prompt", "", parsed)
        assert line == "[User]: fix the bug"

    def test_user_prompt_content_list(self):
        from services.insights.transcript import _format_event

        parsed = {"message": {"content": [{"type": "text", "text": "hello world"}]}}
        line = _format_event("user_prompt", "", parsed)
        assert line == "[User]: hello world"

    def test_user_prompt_truncated_to_max_chars(self):
        from services.insights.transcript import MAX_PROMPT_CHARS, _format_event

        long_text = "a" * (MAX_PROMPT_CHARS + 100)
        parsed = {"message": {"content": long_text}}
        line = _format_event("user_prompt", "", parsed)
        assert len(line) <= len("[User]: ") + MAX_PROMPT_CHARS

    def test_assistant_text_string(self):
        from services.insights.transcript import _format_event

        parsed = {"message": {"content": "I will fix it"}}
        line = _format_event("assistant_text", "", parsed)
        assert line.startswith("[Assistant]:")
        assert "I will fix it" in line

    def test_tool_call_with_tool_name_field(self):
        from services.insights.transcript import _format_event

        parsed = {"input": {"command": "ls -la /repo"}}
        line = _format_event("tool_call", "bash", parsed)
        assert "[Tool: bash]" in line
        assert "ls -la /repo" in line

    def test_tool_call_extracts_name_from_parsed(self):
        from services.insights.transcript import _format_event

        parsed = {"name": "edit", "input": {"path": "/app/main.py"}}
        line = _format_event("tool_call", "", parsed)
        assert "[Tool: edit]" in line
        assert "file: /app/main.py" in line

    def test_tool_result_error_included(self):
        from services.insights.transcript import _format_event

        parsed = {"is_error": True, "content": "command failed"}
        line = _format_event("tool_result", "bash", parsed)
        assert "ERROR" in line
        assert "command failed" in line

    def test_tool_result_non_error_returns_empty(self):
        from services.insights.transcript import _format_event

        parsed = {"is_error": False, "content": "ok"}
        line = _format_event("tool_result", "bash", parsed)
        assert line == ""

    def test_unknown_event_type_returns_empty(self):
        from services.insights.transcript import _format_event

        line = _format_event("session_start", "", {})
        assert line == ""


class TestExtractHelpers:
    def test_extract_text_from_string_content(self):
        from services.insights.transcript import _extract_text

        assert _extract_text({"message": {"content": "hello"}}) == "hello"

    def test_extract_text_from_list_content(self):
        from services.insights.transcript import _extract_text

        parsed = {"message": {"content": [{"type": "text", "text": "part1"}, {"type": "text", "text": "part2"}]}}
        assert _extract_text(parsed) == "part1 part2"

    def test_extract_text_ignores_non_text_blocks(self):
        from services.insights.transcript import _extract_text

        parsed = {
            "message": {
                "content": [
                    {"type": "tool_use", "name": "bash"},
                    {"type": "text", "text": "only this"},
                ]
            }
        }
        assert _extract_text(parsed) == "only this"

    def test_extract_tool_name_from_direct_field(self):
        from services.insights.transcript import _extract_tool_name

        assert _extract_tool_name({"name": "edit"}) == "edit"

    def test_extract_tool_name_from_content_array(self):
        from services.insights.transcript import _extract_tool_name

        parsed = {"message": {"content": [{"type": "tool_use", "name": "write", "input": {}}]}}
        assert _extract_tool_name(parsed) == "write"

    def test_extract_tool_name_returns_empty_when_absent(self):
        from services.insights.transcript import _extract_tool_name

        assert _extract_tool_name({}) == ""

    def test_extract_tool_input_command(self):
        from services.insights.transcript import _extract_tool_input

        assert _extract_tool_input({"input": {"command": "echo hi"}}) == "echo hi"

    def test_extract_tool_input_file_path(self):
        from services.insights.transcript import _extract_tool_input

        assert _extract_tool_input({"input": {"path": "/a/b.py"}}) == "file: /a/b.py"

    def test_extract_tool_input_from_content_array(self):
        from services.insights.transcript import _extract_tool_input

        parsed = {"message": {"content": [{"type": "tool_use", "input": {"command": "ls"}}]}}
        assert _extract_tool_input(parsed) == "ls"

    def test_extract_tool_result_string(self):
        from services.insights.transcript import _extract_tool_result

        assert _extract_tool_result({"content": "output text"}) == "output text"

    def test_extract_tool_result_list_of_blocks(self):
        from services.insights.transcript import _extract_tool_result

        parsed = {"content": [{"type": "text", "text": "line1"}, {"type": "text", "text": "line2"}]}
        assert _extract_tool_result(parsed) == "line1 line2"


class TestFormatRows:
    def test_skips_rows_with_empty_raw_line(self):
        from services.insights.transcript import _format_rows

        rows = [{"line_offset": 0, "event_type": "user_prompt", "tool_name": "", "raw_line": ""}]
        result = _format_rows(rows)
        assert result == ""

    def test_skips_rows_with_invalid_json(self):
        from services.insights.transcript import _format_rows

        rows = [{"line_offset": 0, "event_type": "user_prompt", "tool_name": "", "raw_line": "{bad json"}]
        result = _format_rows(rows)
        assert result == ""

    def test_formats_multiple_events(self):
        from services.insights.transcript import _format_rows

        rows = [
            _row("user_prompt", {"message": {"content": "hello"}}),
            _row("tool_call", {"name": "bash", "input": {"command": "ls"}}, tool_name="bash"),
        ]
        result = _format_rows(rows)
        assert "[User]: hello" in result
        assert "[Tool: bash]" in result


class TestBuildSessionTranscript:
    @pytest.mark.asyncio
    async def test_returns_empty_when_query_fails(self, monkeypatch):
        from services.insights import transcript

        async def failing_query(_sql, _params):
            raise RuntimeError("ClickHouse unavailable")

        monkeypatch.setattr(transcript, "get_query", lambda: failing_query)
        result = await transcript.build_session_transcript("s1")
        assert result == ""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_rows(self, monkeypatch):
        from services.insights import transcript

        class EmptyResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"data": []}

        monkeypatch.setattr(transcript, "get_query", lambda: AsyncMock(return_value=EmptyResponse()))
        result = await transcript.build_session_transcript("s1")
        assert result == ""

    @pytest.mark.asyncio
    async def test_returns_short_transcript_directly(self, monkeypatch):
        from services.insights import transcript

        rows = [_row("user_prompt", {"message": {"content": "quick question"}})]

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {"data": rows}

        monkeypatch.setattr(transcript, "get_query", lambda: AsyncMock(return_value=Response()))
        result = await transcript.build_session_transcript("s1")
        assert "[User]: quick question" in result

    @pytest.mark.asyncio
    async def test_summarizes_long_transcript(self, monkeypatch):
        """Transcripts exceeding MAX_TRANSCRIPT_CHARS are summarized via LLM."""
        import services.dynamic_settings as ds
        from services.insights import transcript

        # Generate rows that produce a long transcript
        big_content = "x" * 600
        rows = [
            {
                "line_offset": i,
                "event_type": "user_prompt",
                "tool_name": "",
                "raw_line": json.dumps({"message": {"content": big_content}}),
            }
            for i in range(70)
        ]

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {"data": rows}

        summaries: list[str] = []

        async def call_model(prompt: str, **kwargs) -> dict:
            summaries.append(prompt)
            return {"summary": "chunk summarized"}

        monkeypatch.setattr(transcript, "get_query", lambda: AsyncMock(return_value=Response()))
        monkeypatch.setattr(transcript, "get_call_model", lambda: call_model)
        monkeypatch.setattr(ds, "get", AsyncMock(return_value=None))

        result = await transcript.build_session_transcript("session-abc123")
        assert summaries  # model was called to summarize
        assert "[Long session summarized]" in result
        assert "chunk summarized" in result
