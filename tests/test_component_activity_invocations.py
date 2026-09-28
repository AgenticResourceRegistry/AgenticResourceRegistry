# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""Phase 2.1 source invocation extraction (real-derived vs constructed cases)."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from observal_shared.harness_registry import HARNESS_REGISTRY
from services.session_parsers.invocations import extract_invocations

_FIXTURE = Path(__file__).parent / "fixtures/component_insights/claude_code/session.jsonl"
_OFFSETS = (21, 22, 23, 24, 33, 34, 35, 36, 38, 39)


def _source(offset: int, record: dict, **fields: object) -> dict:
    return {"line_offset": offset, "is_source_record": 1, "raw_line": json.dumps(record), **fields}


def _call(name: str, call_id: str | None) -> dict:
    return {"type": "tool_use", "id": call_id, "name": name, "input": {}}


def test_sanitized_real_derived_parallel_calls_keep_source_identity_and_only_explicit_error():
    rows = [
        {"line_offset": offset, "is_source_record": 1, "raw_line": raw}
        for offset, raw in zip(_OFFSETS, _FIXTURE.read_text().splitlines(), strict=True)
    ]
    extracted = extract_invocations("claude-code", rows)
    assert extracted.status == "supported"
    assert extracted.malformed_source_records == 0
    assert [
        (call.source_line_offset, call.source_block_key, call.tool_name, call.result_state)
        for call in extracted.invocations
    ] == [
        (23, "id:toolu_fixture_search", "ToolSearch", "unknown"),
        (33, "id:toolu_fixture_first", "mcp__component-insights-phase0-phase0-probe__ping", "unknown"),
        (34, "id:toolu_fixture_second", "mcp__super-phase0-probe__ping", "unknown"),
        (38, "id:toolu_fixture_error", "mcp__super-phase0-probe__fail", "error"),
    ]
    assert len({c.source_block_key for c in extracted.invocations if c.source_line_offset in (33, 34)}) == 2
    assert all(
        c.event_time == datetime(2026, 1, 1, 0, 0, c.source_line_offset, tzinfo=UTC) for c in extracted.invocations
    )
    assert [c.tool_use_id for c in extracted.invocations] == [
        "toolu_fixture_search",
        "toolu_fixture_first",
        "toolu_fixture_second",
        "toolu_fixture_error",
    ]
    assert extract_invocations("claude-code", rows) == extracted  # stable redelivery


def test_constructed_multiblock_and_text_first_not_collapsed_to_summary_or_rendered_row():
    calls = [_call(f"mcp__fixture__{i}", f"constructed-{i}") for i in range(3)]
    rows = [
        _source(7, {"type": "assistant", "message": {"content": [{"type": "text"}, *calls]}}),
        _source(8, {"type": "assistant", "message": {"content": [{"type": "text"}, calls[0]]}}),
        _source(9, {"type": "assistant", "message": {"content": [calls[1]]}}, is_source_record=0),
        _source(10, {"type": "assistant", "message": {"content": [calls[2]]}}, is_source_record=0, rendered=1),
    ]
    extracted = extract_invocations("claude-code", rows)
    assert [(c.source_line_offset, c.source_block_key) for c in extracted.invocations] == [
        (7, "id:constructed-0"),
        (7, "id:constructed-1"),
        (7, "id:constructed-2"),
        (8, "id:constructed-0"),
    ]
    assert all(c.result_state == "unknown" for c in extracted.invocations)


def test_constructed_result_link_requires_unique_id_single_result_and_explicit_boolean_flag():
    calls = [_call("mcp__fixture__one", "unique"), _call("mcp__fixture__two", "ambiguous")]
    rows = [_source(1, {"type": "assistant", "message": {"content": calls}})]
    rows.extend(
        _source(2 + index, {"type": "user", "message": {"content": [block]}})
        for index, block in enumerate(
            [
                {"type": "tool_result", "tool_use_id": "unique", "is_error": False},
                {"type": "tool_result", "tool_use_id": "ambiguous", "is_error": True},
                {"type": "tool_result", "tool_use_id": "ambiguous", "is_error": False},
                {"type": "tool_result", "tool_use_id": "orphan", "is_error": True},
            ]
        )
    )
    assert [c.result_state for c in extract_invocations("claude-code", rows).invocations] == ["success", "unknown"]
    rows[1]["raw_line"] = json.dumps(
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "unique"}]}}
    )
    assert extract_invocations("claude-code", rows).invocations[0].result_state == "unknown"
    rows[1]["raw_line"] = json.dumps(
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "unique", "is_error": "true"}]}}
    )
    assert extract_invocations("claude-code", rows).invocations[0].result_state == "unknown"


def test_result_before_call_cannot_establish_outcome():
    call = _source(2, {"type": "assistant", "message": {"content": [_call("mcp__fixture__search", "call")]}})
    result = _source(
        1, {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "call", "is_error": False}]}}
    )
    assert extract_invocations("claude-code", [result, call]).invocations[0].result_state == "unknown"
    result["line_offset"] = 2
    assert extract_invocations("claude-code", [call, result]).invocations[0].result_state == "unknown"
    result["line_offset"] = 3
    assert extract_invocations("claude-code", [call, result]).invocations[0].result_state == "success"


def test_constructed_missing_or_duplicated_ids_use_block_indices_and_unknown_results():
    rows = [
        _source(
            3,
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text"},
                        _call("mcp__fixture__one", None),
                        _call("mcp__fixture__two", "reused"),
                        _call("mcp__fixture__three", "reused"),
                        _call("mcp__fixture__four", "bad id"),
                    ]
                },
            },
        ),
        _source(
            4,
            {
                "type": "user",
                "message": {"content": [{"type": "tool_result", "tool_use_id": "reused", "is_error": True}]},
            },
        ),
        _source(5, {"type": "assistant", "message": {"content": [_call("mcp__fixture__five", "global-reused")]}}),
        _source(6, {"type": "assistant", "message": {"content": [_call("mcp__fixture__six", "global-reused")]}}),
        _source(
            7,
            {
                "type": "user",
                "message": {"content": [{"type": "tool_result", "tool_use_id": "global-reused", "is_error": True}]},
            },
        ),
    ]
    extracted = extract_invocations("claude-code", rows).invocations
    assert [(call.source_line_offset, call.source_block_key) for call in extracted] == [
        (3, "index:1"),
        (3, "index:2"),
        (3, "index:3"),
        (3, "index:4"),
        (5, "id:global-reused"),
        (6, "id:global-reused"),
    ]
    assert [call.tool_use_id for call in extracted[:4]] == ["", "reused", "reused", ""]
    assert all(call.result_state == "unknown" for call in extracted)


def test_malformed_source_and_missing_time_are_explicit_not_derived_from_rendered_summary():
    rows = [
        {"is_source_record": 1, "line_offset": 1, "raw_line": "not-json"},
        {"is_source_record": 1, "line_offset": -1, "raw_line": "{}"},
        _source(4, {"type": "assistant", "message": {"content": {"type": "tool_use"}}}),
        _source(
            2,
            {"type": "assistant", "message": {"content": [_call("mcp__fixture__one", "one")]}},
            timestamp="2026-01-01 00:00:02",
        ),
        _source(3, {"type": "assistant", "message": {"content": [_call("mcp__fixture__two", "two")]}}),
    ]
    extracted = extract_invocations("claude-code", rows)
    assert extracted.malformed_source_records == 3
    assert [call.event_time for call in extracted.invocations] == [datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC), None]


@pytest.mark.parametrize("harness", sorted(set(HARNESS_REGISTRY) - {"claude-code", "pi"}))
def test_unverified_harness_extractor_is_unsupported_not_zero_use(harness):
    assert extract_invocations(harness, []).status == "unsupported"
    assert extract_invocations(harness, []).invocations == ()


def test_unknown_or_unimplemented_registry_extractor_fails_closed(monkeypatch):
    with pytest.raises(KeyError):
        extract_invocations("not-a-harness", [])
    monkeypatch.setitem(HARNESS_REGISTRY["pi"], "invocation_extractor", "not-implemented")
    with pytest.raises(KeyError):
        extract_invocations("pi", [])
