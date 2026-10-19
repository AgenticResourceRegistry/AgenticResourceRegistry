# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Codex trace-viewer parser and ingest classifier."""

from __future__ import annotations

import json

import pytest

from services.session_parsers import parse_raw_events
from services.session_parsers.ingest_classify import _classify_codex, _preview_codex


def _token_count_row(info: object) -> dict:
    record = {
        "timestamp": "2026-06-01T10:00:09.000Z",
        "type": "event_msg",
        "payload": {"type": "token_count", "info": info, "rate_limits": {"model": "gpt-5-codex"}},
    }
    return {
        "raw_line": json.dumps(record),
        "harness": "codex",
        "timestamp": "2026-06-01 10:00:09.000",
        "ingested_at": "2026-06-01 10:00:10.000",
    }


def test_token_count_reports_input_without_cached_tokens():
    """Codex input_tokens includes cached_input_tokens; the trace view shows them separately."""
    usage = {
        "input_tokens": 12017,
        "cached_input_tokens": 11520,
        "output_tokens": 55,
        "reasoning_output_tokens": 0,
        "total_tokens": 12072,
    }
    events = parse_raw_events([_token_count_row({"last_token_usage": usage})])

    assert [event["event_name"] for event in events] == ["hook_token_usage"]
    assert events[0]["attributes"] == {
        "input_tokens": "497",
        "output_tokens": "55",
        "cache_read_tokens": "11520",
        "model": "gpt-5-codex",
    }


def test_token_count_falls_back_to_total_usage():
    events = parse_raw_events(
        [_token_count_row({"total_token_usage": {"input_tokens": 90, "cached_input_tokens": 40, "output_tokens": 5}})]
    )

    assert events[0]["attributes"]["input_tokens"] == "50"
    assert events[0]["attributes"]["cache_read_tokens"] == "40"


@pytest.mark.parametrize(
    ("info", "input_tokens", "cache_read_tokens"),
    [
        (None, "0", "0"),
        ({"last_token_usage": {"input_tokens": 5, "cached_input_tokens": 9}}, "0", "9"),
        ({"last_token_usage": {"input_tokens": "many", "cached_input_tokens": [1]}}, "0", "0"),
        ({"last_token_usage": {"input_tokens": 25, "cached_input_tokens": -3, "output_tokens": "many"}}, "25", "0"),
    ],
)
def test_token_count_tolerates_malformed_usage(info, input_tokens: str, cache_read_tokens: str):
    events = parse_raw_events([_token_count_row(info)])

    assert events[0]["attributes"]["input_tokens"] == input_tokens
    assert events[0]["attributes"]["cache_read_tokens"] == cache_read_tokens
    assert events[0]["attributes"]["output_tokens"] == "0"


def _user_message(*texts: str) -> dict:
    return {
        "timestamp": "2026-10-18T09:00:00.000Z",
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": text} for text in texts],
        },
    }


# User turns as Codex 0.163 writes them for `codex exec`: only response_item messages.
ENVIRONMENT = _user_message("<environment_context>\n  <cwd>/home/me/repo</cwd>\n</environment_context>")
AGENTS_MD = _user_message("# AGENTS.md instructions for /home/me/repo\n\n<INSTRUCTIONS>be terse</INSTRUCTIONS>")
PROMPT = _user_message("run the tests before saying it's done")
ECHO = {
    "timestamp": "2026-10-18T09:00:00.001Z",
    "type": "event_msg",
    "payload": {"type": "user_message", "message": "run the tests before saying it's done"},
}
DEVELOPER = {
    "type": "response_item",
    "payload": {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "<skills>"}]},
}


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (PROMPT, "user_prompt"),
        (_user_message("<environment_context>...</environment_context>", "fix the parser"), "user_prompt"),
        (ENVIRONMENT, "system"),
        (AGENTS_MD, "system"),
        (_user_message("   "), "system"),
        (ECHO, "system"),
        (DEVELOPER, "system"),
    ],
)
def test_classify_codex_user_messages(line: dict, expected: str):
    assert _classify_codex(line) == expected


def test_trace_view_shows_each_typed_prompt_once():
    rows = [{"raw_line": json.dumps(line), "harness": "codex"} for line in (ENVIRONMENT, AGENTS_MD, PROMPT, ECHO)]

    prompts = [event for event in parse_raw_events(rows) if event["event_name"] == "hook_userpromptsubmit"]

    assert [event["attributes"]["tool_input"] for event in prompts] == ["run the tests before saying it's done"]


def test_prompt_preview_skips_injected_context_blocks():
    line = _user_message("<environment_context>...</environment_context>", "fix the parser")

    assert _preview_codex(line, "user_prompt") == "fix the parser"
    assert _preview_codex(ENVIRONMENT, "system").startswith("<environment_context>")
