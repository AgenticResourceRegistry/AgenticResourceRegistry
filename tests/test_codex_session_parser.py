# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Codex trace-viewer parser's token usage events."""

from __future__ import annotations

import json

import pytest

from services.session_parsers import parse_raw_events


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


def test_user_text_in_response_item_is_prompt_not_injected_context():
    def row(kinds: list[str], texts: list[str]) -> dict:
        record = {
            "type": "response_item",
            "timestamp": "2026-06-01T10:00:09.000Z",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text} for text in texts],
                "internal_chat_message_metadata_passthrough": {"content_item_kinds": kinds},
            },
        }
        return {"raw_line": json.dumps(record), "harness": "codex"}

    events = parse_raw_events(
        [
            row(["agents_md.instructions", "environments.environment_context"], ["Instructions", "Context"]),
            row(["agents_md.instructions", "user.text"], ["More context", "Check the build"]),
            row(["user.text", "user.text"], ["First", "second"]),
            row(["user.text", "user.text"], ["Mismatched metadata"]),
        ]
    )

    assert [event["event_name"] for event in events] == [
        "system",
        "hook_userpromptsubmit",
        "hook_userpromptsubmit",
        "system",
    ]
    assert [event["body"] for event in events[1:3]] == ["Check the build", "First second"]
    assert events[1]["attributes"]["tool_input"] == "Check the build"


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
