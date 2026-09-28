# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""Pi MCP invocation extraction and matching (real-derived vs constructed cases)."""

import json
from datetime import UTC, datetime
from pathlib import Path

from observal_shared.harness_registry import HARNESS_REGISTRY
from services.component_activity.matcher import match_invocations
from services.session_parsers.invocation_types import SourceInvocation
from services.session_parsers.invocations import extract_invocations

_FIXTURES = Path(__file__).parent / "fixtures/component_insights/pi"
_SUPER = "super-phase0-probe"
_TEAM = "component-insights-phase0-phase0-probe"


def _rows(name: str) -> list[dict]:
    return [
        {"line_offset": offset, "is_source_record": 1, "raw_line": raw}
        for offset, raw in enumerate((_FIXTURES / name).read_text().splitlines())
    ]


def _facts(extracted) -> list[tuple]:
    return [
        (call.source_line_offset, call.source_block_key, call.mcp_server, call.tool_name, call.result_state)
        for call in extracted.invocations
    ]


def _source(offset: int, record: dict) -> dict:
    return {"line_offset": offset, "is_source_record": 1, "raw_line": json.dumps(record)}


def _assistant(*blocks: dict) -> dict:
    return {
        "type": "message",
        "timestamp": "2026-01-01T00:00:00Z",
        "message": {"role": "assistant", "content": list(blocks)},
    }


def _call(name: str, call_id: str | None, arguments: dict | None = None) -> dict:
    return {"type": "toolCall", "id": call_id, "name": name, "arguments": arguments or {}}


def _result(call_id: str | None, name: str, details: object, *, is_error: object = False) -> dict:
    message = {"role": "toolResult", "toolCallId": call_id, "toolName": name, "content": [], "isError": is_error}
    if details is not None:
        message["details"] = details
    return {"type": "message", "timestamp": "2026-01-01T00:00:01Z", "message": message}


def _candidate(alias: str, component: str = "c1", **fields: str) -> dict:
    return {
        "local_name": alias,
        "component_id": component,
        "component_version_id": f"{component}-v",
        "identity_status": "resolved",
        "verification_status": "verified",
        **fields,
    }


def test_pi_registry_opts_in_to_its_extractor():
    assert HARNESS_REGISTRY["pi"]["invocation_extractor"] == "pi"


def test_real_derived_proxy_calls_use_adapter_reported_server_and_ignore_search():
    extracted = extract_invocations("pi", _rows("proxy_session.jsonl"))
    assert extracted.status == "supported"
    assert extracted.malformed_source_records == 0
    assert _facts(extracted) == [
        (4, "id:call_fixture01|fc_fixture01", _SUPER, "ping", "success"),
        (6, "id:call_fixture02|fc_fixture02", _TEAM, "ping", "success"),
        (8, "id:call_fixture03|fc_fixture03", _TEAM, "fail", "error"),
    ]  # line 10 is mcp({search}); its result lists servers but is not an invocation
    assert [call.tool_use_id for call in extracted.invocations] == [
        "call_fixture01|fc_fixture01",
        "call_fixture02|fc_fixture02",
        "call_fixture03|fc_fixture03",
    ]
    assert all(call.event_time and call.event_time.tzinfo is UTC for call in extracted.invocations)
    assert "fixture-private-output" not in repr(extracted)
    assert extract_invocations("pi", _rows("proxy_session.jsonl")) == extracted  # stable redelivery


def test_real_derived_direct_tools_and_parallel_proxy_calls_keep_distinct_blocks():
    extracted = extract_invocations("pi", _rows("direct_session.jsonl"))
    assert _facts(extracted) == [
        (4, "id:call_fixture05|fc_fixture05", _SUPER, "ping", "success"),
        (6, "id:call_fixture06|fc_fixture06", _TEAM, "ping", "success"),
        (6, "id:call_fixture07|fc_fixture07", _TEAM, "fail", "error"),
        # A failed direct call reports only {error, server}: the call name stands in.
        (9, "id:call_fixture08|fc_fixture08", _SUPER, "super-phase0-probe_fail", "error"),
    ]


def test_real_derived_sessions_match_each_verified_alias_exactly():
    rows = _rows("direct_session.jsonl")
    extracted = extract_invocations("pi", rows)
    hashes = {row["line_offset"]: "v2_layer" for row in rows}
    matched = match_invocations(
        extracted.invocations, {"v2_layer": [_candidate(_SUPER, "super"), _candidate(_TEAM, "team")]}, hashes
    )
    assert matched.candidate_count == 4
    assert (matched.collision_count, matched.unmatched_count, matched.unknown_result_count) == (0, 0, 0)
    assert [(row["component_id"], row["result_state"]) for row in matched.rows] == [
        ("super", "success"),
        ("team", "success"),
        ("team", "error"),
        ("super", "error"),
    ]
    assert {row["attribution_method"] for row in matched.rows} == {"verified_server"}


def test_server_identity_never_matches_by_prefix_and_duplicate_alias_is_a_collision():
    time = datetime(2026, 1, 1, tzinfo=UTC)
    calls = [
        SourceInvocation(1, "id:a", "ping", "a", time, "success", mcp_server="probe"),
        SourceInvocation(2, "id:b", "ping", "b", time, "success", mcp_server="probe-2"),
        SourceInvocation(3, "id:c", "mcp", "c", time, "unknown", mcp_server=""),
    ]
    hashes = dict.fromkeys((1, 2, 3), "h")
    matched = match_invocations(calls, {"h": [_candidate("probe"), _candidate("probe-2x")]}, hashes)
    assert [row["source_block_key"] for row in matched.rows] == ["id:a"]
    assert (matched.candidate_count, matched.unmatched_count, matched.unknown_result_count) == (3, 2, 1)
    duplicate = match_invocations(calls[:1], {"h": [_candidate("probe", "x"), _candidate("probe", "y")]}, hashes)
    assert (duplicate.rows, duplicate.collision_count) == ((), 1)
    unverified = match_invocations(calls[:1], {"h": [_candidate("probe", verification_status="unverified")]}, hashes)
    assert (unverified.rows, unverified.unmatched_count) == ((), 1)


def test_constructed_unlinkable_mcp_requests_are_candidates_without_identity():
    rows = [
        _source(
            0,
            _assistant(
                _call("mcp", "missing", {"tool": "ping", "server": "probe"}),
                _call("mcp__probe", "namespace", {"tool": "ping"}),
                _call("probe_ping", "direct-missing"),
                _call("mcp", "search", {"search": "probe"}),
                _call("mcp", "reused", {"tool": "ping"}),
            ),
        ),
        _source(1, _assistant(_call("mcp", "reused", {"tool": "ping"}))),
        _source(2, _result("reused", "mcp", {"mode": "call", "server": "probe", "tool": "ping"})),
        _source(3, _result("search", "mcp", {"mode": "search", "server": "probe"})),
    ]
    facts = _facts(extract_invocations("pi", rows))
    assert facts == [
        (0, "id:missing", "", "mcp", "unknown"),
        (0, "id:namespace", "", "mcp__probe", "unknown"),
        # A direct tool with no result is indistinguishable from any other tool.
        (0, "id:reused", "", "mcp", "unknown"),
        (1, "id:reused", "", "mcp", "unknown"),
    ]


def test_constructed_links_require_order_matching_tool_name_and_explicit_error_flag():
    details = {"mode": "call", "server": "probe", "tool": "ping"}
    rows = [
        _source(0, _result("early", "mcp", details)),
        _source(1, _assistant(_call("mcp", "early", {"tool": "ping"}), _call("mcp", "renamed", {"tool": "ping"}))),
        _source(2, _result("renamed", "other", details)),
        _source(3, _assistant(_call("mcp", "flagless", {"tool": "ping"}), _call("mcp", "bad-server", {"tool": "x"}))),
        _source(4, _result("flagless", "mcp", details, is_error="false")),
        _source(5, _result("bad-server", "mcp", {"mode": "call", "server": "bad server", "tool": "x"})),
        _source(6, _assistant(_call("mcp", "pre-dispatch", {"tool": "missing"}))),
        _source(7, _result("pre-dispatch", "mcp", {"mode": "call", "error": "tool_not_found"}, is_error=True)),
        _source(8, _assistant(_call("mcp", "id with space", {"tool": "ping"}))),
    ]
    facts = _facts(extract_invocations("pi", rows))
    assert facts == [
        (1, "id:early", "", "mcp", "unknown"),  # its only result precedes the call
        (1, "id:renamed", "", "mcp", "unknown"),  # result names another tool
        (3, "id:flagless", "probe", "ping", "unknown"),  # identity without a boolean flag
        (3, "id:bad-server", "", "mcp", "unknown"),  # not an installable alias
        (6, "id:pre-dispatch", "", "mcp", "unknown"),  # resolution failed before a server
        (8, "index:0", "", "mcp", "unknown"),  # invalid id cannot link
    ]


def test_constructed_direct_identity_must_describe_the_called_tool():
    calls = [
        ("probe_ping", {"server": "probe", "tool": "ping"}),  # prefix "server"
        ("ping", {"server": "probe-mcp", "tool": "ping"}),  # prefix "none"
        ("probe_ls_dir", {"server": "probe-mcp", "tool": "ls.dir"}),  # prefix "short", dots sanitized
        ("mcp__probe_ping", {"server": "probe", "tool": "ping"}),  # prefix "mcp"
        ("probe_fail", {"server": "probe", "error": "tool_error"}),  # failed direct call: no tool
        ("deploy", {"server": "probe", "error": "failed"}),  # another tool's error shape
        ("web_search", {"server": "probe", "tool": "ping"}),  # another tool's MCP-shaped details
        ("mcp", {"server": "probe", "tool": "ping"}),  # proxy result must carry mode "call"
    ]
    rows = [_source(0, _assistant(*(_call(name, f"c{index}") for index, (name, _) in enumerate(calls))))]
    rows += [
        _source(index + 1, _result(f"c{index}", name, details, is_error="error" in details))
        for index, (name, details) in enumerate(calls)
    ]
    rows.append(_source(20, _result("orphan", "probe_ping", {"server": "probe", "tool": "ping"})))
    assert _facts(extract_invocations("pi", rows)) == [
        (0, "id:c0", "probe", "ping", "success"),
        (0, "id:c1", "probe-mcp", "ping", "success"),
        (0, "id:c2", "probe-mcp", "ls.dir", "success"),
        (0, "id:c3", "probe", "ping", "success"),
        (0, "id:c4", "probe", "probe_fail", "error"),
        (0, "id:c7", "", "mcp", "unknown"),
        (20, "orphan:0", "", "probe_ping", "unknown"),
    ]


def test_malformed_records_are_counted_and_other_records_are_ignored():
    rows = [
        {"line_offset": 0, "is_source_record": 1, "raw_line": "{not json"},
        _source(1, {"type": "message", "message": "text"}),
        _source(2, {"type": "message", "message": {"role": "assistant", "content": "text"}}),
        _source(3, {"type": "model_change", "provider": "x"}),
        {"line_offset": 4, "is_source_record": 0, "raw_line": "{}"},
        {"line_offset": -1, "is_source_record": 1, "raw_line": "{}"},
    ]
    extracted = extract_invocations("pi", rows)
    assert (extracted.invocations, extracted.malformed_source_records) == ((), 4)
