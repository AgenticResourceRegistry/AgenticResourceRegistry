# SPDX-FileCopyrightText: 2026 RAWx18 <rawx18.dev@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Kiro trace-viewer parser, in both the CLI and IDE layouts.

The viewer renders content from an event's ``attributes`` and never from
``body``, and it only has renderers for the ``hook_*`` vocabulary. An event
reaching it with an ingest-classifier name (``assistant_text``, ``thinking``)
is the ``basic_event`` fallback and draws as a blank row, so these tests assert
the attribute payload rather than just the event name.
"""

from __future__ import annotations

import json

import pytest

from services.session_parsers import parse_raw_events


def _ide_row(payload: dict, record_id: str = "r1") -> dict:
    return {
        "raw_line": json.dumps({"id": record_id, "timestamp": "2026-09-26T00:00:00Z", "payload": payload}),
        "harness": "kiro",
        "timestamp": "2026-09-26 00:00:00.000",
        "ingested_at": "2026-09-26 00:00:01.000",
    }


def _cli_row(record: dict) -> dict:
    return {
        "raw_line": json.dumps(record),
        "harness": "kiro",
        "timestamp": "2026-09-26 00:00:00.000",
        "ingested_at": "2026-09-26 00:00:01.000",
    }


def test_ide_credits_show_model_observed_in_assistant_payload():
    assistant = _ide_row({"type": "assistant", "content": "hello", "modelId": "claude-opus-5.5"})
    credits = _cli_row({"kind": "KiroCredits", "credits": 1.25, "data": {}})
    credits["credits"] = 1.25
    events = parse_raw_events([assistant, credits])
    assert events[-1]["attributes"]["model"] == "claude-opus-5.5"


def test_cli_credits_show_observed_model_not_a_guessed_auto_model():
    assistant = _cli_row(
        {
            "kind": "AssistantMessage",
            "data": {
                "content": [
                    {"kind": "toolUse", "data": {"modelId": "claude-opus-5.5", "name": "read", "toolUseId": "t1"}}
                ]
            },
        }
    )
    credits = _cli_row({"kind": "KiroCredits", "data": {}, "credits": 1.25})
    credits["credits"] = 1.25
    events = parse_raw_events([assistant, credits])
    assert events[-1]["attributes"]["model"] == "claude-opus-5.5"
    assert "model" not in parse_raw_events([credits])[0]["attributes"]


# ── IDE: text must reach the viewer ───────────────────────────────


def test_ide_user_prompt_carries_its_text_in_attributes():
    events = parse_raw_events([_ide_row({"type": "user", "content": "whats up"})])

    assert len(events) == 1
    assert events[0]["event_name"] == "hook_userpromptsubmit"
    assert events[0]["attributes"]["tool_input"] == "whats up"


def test_ide_assistant_text_carries_its_text_in_attributes():
    events = parse_raw_events([_ide_row({"type": "assistant", "content": "Pika pika!", "operationType": "Say"})])

    assert len(events) == 1
    assert events[0]["event_name"] == "hook_assistant_response"
    assert events[0]["attributes"]["tool_response"] == "Pika pika!"


def test_ide_reasoning_is_separated_from_visible_output():
    events = parse_raw_events(
        [_ide_row({"type": "assistant", "content": "weighing options", "operationType": "Reasoning"})]
    )

    assert events[0]["event_name"] == "hook_assistant_thinking"
    assert events[0]["attributes"]["tool_response"] == "weighing options"


def test_ide_elided_reasoning_is_not_rendered():
    """Kiro writes a literal "..." placeholder and seals the real trace.

    Rendering these produced a run of "Thinking ..." rows with nothing in them,
    which no other harness shows and which the Kiro CLI layout never produces.
    """
    rows = [
        _ide_row(
            {
                "type": "assistant",
                "content": "...",
                "operationType": "Reasoning",
                "reasoningSignature": "c2VhbGVk",
            }
        )
    ]

    assert parse_raw_events(rows) == []


def test_ide_empty_assistant_filler_is_dropped():
    assert parse_raw_events([_ide_row({"type": "assistant", "content": "   ", "operationType": "Say"})]) == []


def test_ide_events_match_the_shape_the_cli_path_produces():
    """The CLI hook path is the known-good oracle; the IDE must match it."""
    cli = parse_raw_events(
        [
            _cli_row({"kind": "Prompt", "data": {"content": [{"kind": "text", "data": "hi"}]}}),
            _cli_row({"kind": "AssistantMessage", "data": {"content": [{"kind": "text", "data": "hello"}]}}),
        ]
    )
    ide = parse_raw_events(
        [
            _ide_row({"type": "user", "content": "hi"}),
            _ide_row({"type": "assistant", "content": "hello", "operationType": "Say"}),
        ]
    )

    assert [
        (e["event_name"], e["attributes"].get("tool_input"), e["attributes"].get("tool_response")) for e in ide
    ] == [(e["event_name"], e["attributes"].get("tool_input"), e["attributes"].get("tool_response")) for e in cli]


# ── IDE: tools ────────────────────────────────────────────────────


def test_ide_tool_call_and_result_merge_into_one_event():
    events = parse_raw_events(
        [
            _ide_row({"type": "tool_call", "toolCallId": "t1", "toolName": "read_file", "args": {"path": "a.py"}}),
            _ide_row({"type": "tool_result", "toolCallId": "t1", "content": "file body", "durationMs": 12}),
        ]
    )

    assert len(events) == 1
    attributes = events[0]["attributes"]
    assert events[0]["event_name"] == "hook_posttooluse"
    assert attributes["tool_name"] == "read_file"
    assert json.loads(attributes["tool_input"]) == {"path": "a.py"}
    assert attributes["tool_response"] == "file body"
    assert attributes["duration_ms"] == "12"
    assert attributes["success"] == "true"


def test_ide_failed_tool_result_is_marked():
    events = parse_raw_events(
        [
            _ide_row({"type": "tool_call", "toolCallId": "t1", "toolName": "read_file", "args": {}}),
            _ide_row({"type": "tool_result", "toolCallId": "t1", "content": "no such path", "success": False}),
        ]
    )

    assert events[0]["attributes"]["tool_status"] == "error"
    assert events[0]["attributes"]["success"] == "false"


def test_ide_orphan_tool_result_is_skipped():
    """A result whose call was never seen has nothing to merge into."""
    assert parse_raw_events([_ide_row({"type": "tool_result", "toolCallId": "gone", "content": "x"})]) == []


# ── IDE: noise and unknowns ───────────────────────────────────────


def test_ide_lifecycle_payloads_do_not_become_blank_rows():
    rows = [
        _ide_row({"type": name})
        for name in ("session_start", "turn_start", "turn_end", "ContextualHookInvoked", "session_metadata")
    ]

    assert parse_raw_events(rows) == []


def test_ide_usage_summary_does_not_duplicate_the_session_credit_total():
    """Credits are written once as a synthetic row summing exactly these."""
    rows = [_ide_row({"type": "usage_summary", "promptTurnSummaries": [{"usage": 0.5, "unit": "credit"}]})]

    assert parse_raw_events(rows) == []


def test_ide_unknown_payload_is_surfaced_rather_than_dropped():
    events = parse_raw_events([_ide_row({"type": "brand_new_thing"})])

    assert len(events) == 1
    assert events[0]["attributes"]["payload_type"] == "brand_new_thing"


# ── The CLI layout must keep working ──────────────────────────────


def test_cli_records_are_unaffected_by_the_ide_branch():
    events = parse_raw_events(
        [
            _cli_row({"kind": "Prompt", "data": {"content": [{"kind": "text", "data": "run it"}]}}),
            _cli_row(
                {
                    "kind": "AssistantMessage",
                    "data": {
                        "content": [
                            {"kind": "toolUse", "data": {"toolUseId": "u1", "name": "bash", "input": {"cmd": "ls"}}}
                        ]
                    },
                }
            ),
            _cli_row(
                {
                    "kind": "ToolResults",
                    "data": {
                        "content": [
                            {
                                "kind": "toolResult",
                                "data": {
                                    "toolUseId": "u1",
                                    "status": "success",
                                    "content": [{"kind": "text", "data": "out"}],
                                },
                            }
                        ]
                    },
                }
            ),
        ]
    )

    assert [e["event_name"] for e in events] == ["hook_userpromptsubmit", "hook_posttooluse"]
    assert events[1]["attributes"]["tool_response"] == "out"


# ── Ingest timestamps ─────────────────────────────────────────────


def test_ide_timestamps_are_normalised_to_utc():
    """The value goes straight into a DateTime64 column.

    A non-UTC offset sent verbatim can reject the whole insert batch, and an
    unparseable value must become a source error rather than poison the batch.
    """
    from services.session_parsers.ingest_classify import extract_timestamp

    def ts(raw):
        return extract_timestamp("kiro", {"payload": {"type": "user"}, "timestamp": raw})

    assert ts("2026-09-25T18:59:14.327Z") == "2026-09-25 18:59:14.327"
    assert ts("2026-09-25T18:59:14.327+00:00") == "2026-09-25 18:59:14.327"
    assert ts("2026-09-25T18:59:14.327+05:30") == "2026-09-25 13:29:14.327"
    assert ts("2026-09-25T18:59:14.327") == "2026-09-25 18:59:14.327"
    with pytest.raises(ValueError, match="invalid Kiro IDE timestamp"):
        ts("not-a-timestamp")
    assert ts("") is None


def test_cli_timestamp_extraction_is_unchanged():
    from services.session_parsers.ingest_classify import extract_timestamp

    parsed = {"kind": "Prompt", "data": {"meta": {"timestamp": 1758825554}}}

    assert extract_timestamp("kiro", parsed) == "2025-09-25 18:39:14.000"
