# SPDX-FileCopyrightText: 2026 RAWx18 <rawx18.dev@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Goose ingest classifier and trace-viewer parser."""

from __future__ import annotations

import json

import pytest

from services.session_ingest import _extract_usage_tokens, _extract_uuid
from services.session_parsers import parse_raw_events
from services.session_parsers.ingest_classify import classify, extract_preview, extract_timestamp, extract_tool_info


def _row(record: dict) -> dict:
    return {
        "raw_line": json.dumps(record),
        "harness": "goose",
        "timestamp": "2026-08-07 12:00:00.000",
        "ingested_at": "2026-08-07 12:00:01.000",
    }


def _message(role: str, content: list, **extra) -> dict:
    return {
        "type": "message",
        "row_id": 1,
        "session_id": "20260807_1",
        "message_id": "msg-1",
        "role": role,
        "timestamp": "2026-08-07T12:00:00.000Z",
        "content": content,
        "metadata": {},
        **extra,
    }


_TOOL_REQUEST = {
    "type": "toolRequest",
    "id": "tool-1",
    "toolCall": {"status": "success", "value": {"name": "developer__shell", "arguments": {"command": "ls"}}},
}


# ── Ingest classification ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({"type": "session", "name": "Fix parser"}, "system"),
        ({"type": "session_end", "usage": {}}, "system"),
        (_message("user", [{"type": "text", "text": "hi"}]), "user_prompt"),
        (_message("assistant", [{"type": "text", "text": "sure"}]), "assistant_text"),
        (_message("assistant", [{"type": "thinking", "thinking": "hmm", "signature": ""}]), "thinking"),
        (_message("assistant", [{"type": "redactedThinking", "data": "x"}]), "thinking"),
        (_message("assistant", [_TOOL_REQUEST]), "tool_call"),
        # A mixed block list resolves on the first significant block, as Claude Code does.
        (
            _message("assistant", [{"type": "thinking", "thinking": "hmm", "signature": ""}, _TOOL_REQUEST]),
            "thinking",
        ),
        (
            _message("assistant", [_TOOL_REQUEST, {"type": "thinking", "thinking": "hmm", "signature": ""}]),
            "tool_call",
        ),
        (
            _message("user", [{"type": "toolResponse", "id": "tool-1", "toolResult": {"status": "success"}}]),
            "tool_result",
        ),
    ],
)
def test_classify_goose_records(record: dict, expected: str):
    assert classify("goose", record) == expected


def test_classify_skips_empty_message_rows():
    assert classify("goose", _message("assistant", [])) is None


def test_preview_covers_every_record_kind():
    assert "Fix parser" in extract_preview("goose", {"type": "session", "name": "Fix parser"}, "system")
    assert extract_preview("goose", {"type": "session_end"}, "system") == "[session end]"
    assert "developer__shell" in extract_preview("goose", _message("assistant", [_TOOL_REQUEST]), "tool_call")
    result = _message(
        "user",
        [
            {
                "type": "toolResponse",
                "id": "tool-1",
                "toolResult": {"status": "success", "value": {"content": [{"type": "text", "text": "README.md"}]}},
            }
        ],
    )
    assert "README.md" in extract_preview("goose", result, "tool_result")


def test_tool_info_reads_the_namespaced_tool_name():
    assert extract_tool_info("goose", _message("assistant", [_TOOL_REQUEST])) == ("developer__shell", "tool-1")
    assert extract_tool_info("goose", _message("user", [{"type": "text", "text": "hi"}])) == (None, None)


def test_timestamp_is_read_from_the_mirrored_record():
    assert extract_timestamp("goose", _message("user", [])) == "2026-08-07 12:00:00.000"
    assert extract_timestamp("goose", {"type": "session"}) is None


def test_usage_and_uuid_extraction():
    record = _message(
        "assistant",
        [{"type": "text", "text": "done"}],
        metadata={
            "usage": {"inputTokens": 120, "outputTokens": 45, "cacheReadTokens": 10, "cacheWriteTokens": 5},
            "inference": {"provider": "anthropic", "requestedModel": "claude-sonnet-4-6", "resolvedModel": "claude-4"},
        },
    )

    assert _extract_usage_tokens(record, "goose") == {
        "input_tokens": 105,
        "output_tokens": 45,
        "cache_read_tokens": 10,
        "cache_write_tokens": 5,
        "model": "claude-4",
    }
    assert _extract_uuid(record, "goose") == ("msg-1", None)
    assert _extract_usage_tokens({"type": "session"}, "goose")["input_tokens"] == 0


def test_uuid_falls_back_to_the_session_id_without_a_message_id():
    """Pre-v7 goose databases have no ``message_id`` column."""
    record = _message("assistant", [{"type": "text", "text": "done"}], message_id=None)

    assert _extract_uuid(record, "goose") == ("20260807_1", None)


@pytest.mark.parametrize(
    "metadata",
    [
        {"usage": "not-a-dict"},
        {"usage": {"inputTokens": "many"}},
        {"usage": {"inputTokens": None}},
        {"inference": []},
        [],
    ],
)
def test_usage_extraction_never_raises_on_malformed_metadata(metadata):
    usage = _extract_usage_tokens(_message("assistant", [], metadata=metadata), "goose")
    assert usage["input_tokens"] == 0
    assert usage["model"] == ""


# ── Trace viewer parsing ──────────────────────────────────────────────────────


def test_parse_rows_emits_session_boundaries():
    events = parse_raw_events(
        [
            _row(
                {
                    "type": "session",
                    "session_id": "20260807_1",
                    "name": "Fix parser",
                    "working_dir": "/project",
                    "provider": "anthropic",
                    "model": "claude-sonnet-4-6",
                    "timestamp": "2026-08-07T11:59:00.000Z",
                }
            ),
            _row({"type": "session_end", "usage": {"inputTokens": 120, "totalTokens": 165}}),
        ]
    )

    assert [event["event_name"] for event in events] == ["hook_sessionstart", "hook_stop"]
    assert events[0]["attributes"]["working_dir"] == "/project"
    assert events[0]["attributes"]["model"] == "claude-sonnet-4-6"
    assert events[1]["attributes"]["total_tokens"] == "165"


def test_parse_rows_merges_tool_results_into_their_request():
    events = parse_raw_events(
        [
            _row(_message("assistant", [_TOOL_REQUEST])),
            _row(
                _message(
                    "user",
                    [
                        {
                            "type": "toolResponse",
                            "id": "tool-1",
                            "toolResult": {
                                "status": "success",
                                "value": {"content": [{"type": "text", "text": "README.md"}], "isError": False},
                            },
                        }
                    ],
                )
            ),
        ]
    )

    assert len(events) == 1
    assert events[0]["event_name"] == "hook_posttooluse"
    assert events[0]["attributes"]["tool_name"] == "developer__shell"
    assert json.loads(events[0]["attributes"]["tool_input"]) == {"command": "ls"}
    assert events[0]["attributes"]["tool_response"] == "README.md"


def test_parse_rows_flags_failed_tool_calls():
    events = parse_raw_events(
        [
            _row(_message("assistant", [_TOOL_REQUEST])),
            _row(
                _message(
                    "user",
                    [
                        {
                            "type": "toolResponse",
                            "id": "tool-1",
                            "toolResult": {
                                "status": "success",
                                "value": {"content": [{"type": "text", "text": "boom"}], "isError": True},
                            },
                        }
                    ],
                )
            ),
        ]
    )

    assert events[0]["attributes"]["tool_status"] == "error"
    assert events[0]["attributes"]["tool_response"] == "boom"


def test_parse_rows_handles_legacy_tool_result_shape():
    events = parse_raw_events(
        [
            _row(_message("assistant", [_TOOL_REQUEST])),
            _row(
                _message(
                    "user",
                    [
                        {
                            "type": "toolResponse",
                            "id": "tool-1",
                            "toolResult": {"status": "success", "value": [{"type": "text", "text": "legacy"}]},
                        }
                    ],
                )
            ),
        ]
    )

    assert events[0]["attributes"]["tool_response"] == "legacy"


def test_parse_rows_surfaces_tool_call_errors():
    events = parse_raw_events(
        [
            _row(
                _message(
                    "assistant",
                    [{"type": "toolRequest", "id": "tool-2", "toolCall": {"status": "error", "error": "bad args"}}],
                )
            )
        ]
    )

    assert events[0]["attributes"]["tool_status"] == "error"
    assert events[0]["attributes"]["tool_response"] == "bad args"


def test_parse_rows_emits_prompts_thinking_and_responses_with_tokens():
    events = parse_raw_events(
        [
            _row(_message("user", [{"type": "text", "text": "why?"}])),
            _row(
                _message(
                    "assistant",
                    [
                        {"type": "thinking", "thinking": "because", "signature": ""},
                        {"type": "text", "text": "here you go"},
                    ],
                    metadata={"usage": {"inputTokens": 10, "outputTokens": 3}},
                )
            ),
        ]
    )

    assert [event["event_name"] for event in events] == [
        "hook_userpromptsubmit",
        "hook_assistant_thinking",
        "hook_assistant_response",
    ]
    assert events[2]["attributes"]["input_tokens"] == "10"


def test_parse_rows_reports_usage_for_a_tool_only_turn():
    events = parse_raw_events([_row(_message("assistant", [_TOOL_REQUEST], metadata={"usage": {"outputTokens": 7}}))])

    assert [event["event_name"] for event in events] == ["hook_posttooluse", "hook_token_usage"]
    assert events[1]["attributes"]["output_tokens"] == "7"


def test_parse_rows_reports_input_tokens_without_cache():
    """Goose counts cache reads and writes inside inputTokens; the trace view shows them separately."""
    usage = {"inputTokens": 6010, "outputTokens": 50, "cacheReadTokens": 5000, "cacheWriteTokens": 1000}
    events = parse_raw_events([_row(_message("assistant", [_TOOL_REQUEST], metadata={"usage": usage}))])

    attributes = events[1]["attributes"]
    assert attributes["input_tokens"] == "10"
    assert attributes["cache_read_tokens"] == "5000"
    assert attributes["cache_creation_tokens"] == "1000"


def test_parse_rows_ignores_malformed_token_attributes():
    usage = {"inputTokens": 8, "outputTokens": "many", "cacheReadTokens": -4, "cacheWriteTokens": [2]}
    events = parse_raw_events([_row(_message("assistant", [_TOOL_REQUEST], metadata={"usage": usage}))])

    attributes = events[1]["attributes"]
    assert attributes["input_tokens"] == "8"
    assert "output_tokens" not in attributes
    assert "cache_read_tokens" not in attributes
    assert "cache_creation_tokens" not in attributes


def test_parse_rows_emits_orphan_tool_results_standalone():
    events = parse_raw_events(
        [
            _row(
                _message(
                    "user",
                    [
                        {
                            "type": "toolResponse",
                            "id": "unknown",
                            "toolResult": {"status": "success", "value": {"content": [{"type": "text", "text": "x"}]}},
                        }
                    ],
                )
            )
        ]
    )

    assert events[0]["event_name"] == "hook_posttooluse"
    assert events[0]["attributes"]["tool_use_id"] == "unknown"


def test_parse_rows_falls_back_for_unparseable_lines():
    events = parse_raw_events(
        [{"raw_line": "{not json", "harness": "goose", "event_type": "system", "content_preview": "oops"}]
    )

    assert events[0]["body"] == "oops"


@pytest.mark.parametrize(
    "record",
    [
        [1, 2, 3],
        "a string",
        {"type": "message", "role": "assistant", "content": "not-a-list"},
        {"type": "message", "role": "assistant", "content": [None, 7, "text", {}]},
        {"type": "message", "role": None, "content": [{"type": "toolRequest", "toolCall": []}]},
        {"type": "message", "content": [{"type": "toolResponse", "toolResult": {"value": 5}}]},
        {"type": "session_end", "usage": "nope"},
        {"type": "unknown-future-record"},
        {},
    ],
)
def test_malformed_records_never_crash_the_pipeline(record):
    """Goose data is machine-generated, but a corrupt mirror must never break ingest."""
    parsed = record if isinstance(record, dict) else {"type": "message", "content": record}
    event_type = classify("goose", parsed)
    extract_preview("goose", parsed, event_type or "")
    extract_tool_info("goose", parsed)
    extract_timestamp("goose", parsed)
    _extract_usage_tokens(parsed, "goose")
    assert isinstance(parse_raw_events([_row(record)]), list)
