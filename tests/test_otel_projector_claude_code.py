# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Claude Code span projector.

Golden fixtures under ``fixtures/otel/claude_code/`` are real Claude Code
transcripts with every piece of text replaced; IDs, parent links, timestamps,
models, usage, tool names and error flags are as recorded.  Rows are built
from them the way ingest stores them.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import xxhash
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from services.otel.attributes import semconv_input_tokens
from services.otel.ids import span_id, to_unix_nanos, trace_id_for
from services.otel.projectors import get_projector
from services.otel.projectors.claude_code import ClaudeCodeProjector
from services.session_ingest import _extract_usage_tokens, _extract_uuid
from services.session_parsers.ingest_classify import extract_timestamp, get_classifier

if TYPE_CHECKING:
    from services.otel.types import Span

FIXTURES = Path(__file__).parent / "fixtures" / "otel" / "claude_code"
PROJECTOR = ClaudeCodeProjector()
ALL_FIXTURES = sorted(path.stem for path in FIXTURES.glob("*.jsonl"))


# ---------------------------------------------------------------------------
# Rows as ingest stores them
# ---------------------------------------------------------------------------


def _rows(lines: list[dict | str], session_id: str = "sess-1", parent_session_id: str | None = None) -> list[dict]:
    classify, _preview, tool_info = get_classifier("claude-code")
    rows = []
    last_ts = "2026-01-01 00:00:00.000"
    for offset, item in enumerate(lines):
        raw = item if isinstance(item, str) else json.dumps(item)
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = {}
        event_type = (classify(parsed) or "_ignored") if parsed else "_parse_error"
        rendered = int(event_type not in ("_ignored", "_parse_error"))
        last_ts = (extract_timestamp("claude-code", parsed) if parsed else None) or last_ts
        uuid, parent_uuid = _extract_uuid(parsed) if parsed else (None, None)
        usage = _extract_usage_tokens(parsed) if parsed else {}
        tool_name, tool_id = tool_info(parsed) if rendered else (None, None)
        rows.append(
            {
                "session_id": session_id,
                "project_id": "proj-1",
                "user_id": "user-1",
                "harness": "claude-code",
                "agent_id": None,
                "agent_version": None,
                "parent_session_id": parent_session_id,
                "line_offset": offset,
                "line_hash": xxhash.xxh128(raw.encode()).hexdigest(),
                "is_source_record": 1,
                "rendered": rendered,
                "event_type": event_type,
                "timestamp": last_ts,
                "ingested_at": "2026-09-30 00:00:00.000",
                "uuid": uuid,
                "parent_uuid": parent_uuid,
                "tool_name": tool_name,
                "tool_id": tool_id,
                "model": usage.get("model", ""),
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "cache_read_tokens": usage.get("cache_read_tokens", 0),
                "cache_write_tokens": usage.get("cache_write_tokens", 0),
                "credits": 0,
                "raw_line": raw,
                "raw_line_truncated": 0,
            }
        )
    return rows


def _lines(name: str) -> list[dict]:
    return [json.loads(line) for line in (FIXTURES / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()]


def _fixture_rows(name: str) -> list[dict]:
    if name == "subagent_child":
        return _rows(_lines(name), session_id=_subagent_id(), parent_session_id="sess-subagent-parent")
    return _rows(_lines(name), session_id=f"sess-{name.replace('_', '-')}")


def _subagent_id() -> str:
    return _lines("subagent_child")[0]["agentId"]


def _attrs(span: Span) -> dict:
    return dict(span.attributes)


def _by_kind(spans: list[Span]) -> dict[str, list[Span]]:
    grouped: dict[str, list[Span]] = {}
    for span in spans:
        grouped.setdefault(_attrs(span).get("gen_ai.operation.name") or "turn", []).append(span)
    return grouped


def _originals(lines: list[dict]) -> list[dict]:
    """Lines that aren't replays: a new uuid and a new (type, timestamp, message)."""
    uuids: set[str] = set()
    prints: set[str] = set()
    kept = []
    for line in lines:
        fingerprint = json.dumps([line.get("type"), line.get("timestamp"), line.get("message")], sort_keys=True)
        if line.get("uuid") not in uuids and fingerprint not in prints:
            kept.append(line)
        uuids.add(line.get("uuid"))
        prints.add(fingerprint)
    return kept


def _responses(lines: list[dict]) -> dict[str, list[dict]]:
    """Original assistant lines grouped on message.id."""
    grouped: dict[str, list[dict]] = {}
    for line in _originals(lines):
        if line.get("type") == "assistant":
            grouped.setdefault(line["message"]["id"], []).append(line)
    return grouped


def _tool_results(lines: list[dict]) -> dict[str, tuple[dict, dict]]:
    """tool_use_id -> (result line, result block)."""
    results = {}
    for line in lines:
        content = (line.get("message") or {}).get("content")
        if line.get("type") == "user" and isinstance(content, list):
            for block in content:
                if block.get("type") == "tool_result":
                    results.setdefault(block["tool_use_id"], (line, block))
    return results


def _assert_well_formed(spans: list[Span]) -> None:
    ids = [span.span_id for span in spans]
    assert len(ids) == len(set(ids)), "a span was emitted twice"
    assert len({span.trace_id for span in spans}) <= 1
    for span in spans:
        assert span.end_ns >= span.start_ns


# ---------------------------------------------------------------------------
# Golden fixtures
# ---------------------------------------------------------------------------


def test_claude_code_sessions_dispatch_to_this_projector():
    assert isinstance(get_projector("claude-code"), ClaudeCodeProjector)


@pytest.mark.parametrize("name", [n for n in ALL_FIXTURES if n != "subagent_child"])
def test_fixture_tree_follows_the_transcript_ids(name):
    lines = _lines(name)
    rows = _fixture_rows(name)
    spans = PROJECTOR.project_spans(rows, session_closed=True)
    _assert_well_formed(spans)
    by_kind = _by_kind(spans)
    by_id = {span.span_id: span for span in spans}
    sid = rows[0]["session_id"]

    [session] = by_kind["invoke_agent"]
    assert session.parent_span_id is None
    assert session.trace_id == trace_id_for(sid)
    for turn in by_kind["turn"]:
        assert turn.parent_span_id == session.span_id

    responses = _responses(lines)
    chats = {_attrs(span)["gen_ai.response.id"]: span for span in by_kind["chat"]}
    assert set(chats) == set(responses)
    for message_id, chat in chats.items():
        assert chat.span_id == span_id(sid, f"chat:{message_id}")
        assert by_id[chat.parent_span_id] in by_kind["turn"]
        assert len(chat.record_offsets) == len(responses[message_id])

    tool_uses = {
        block["id"]: message_id
        for message_id, group in responses.items()
        for line in group
        for block in line["message"]["content"]
        if block.get("type") == "tool_use"
    }
    tools = [span for span in by_kind.get("execute_tool", []) if "observal.tool.orphan_result" not in _attrs(span)]
    assert {_attrs(span)["gen_ai.tool.call.id"] for span in tools} == set(tool_uses)
    for tool in tools:
        # Each tool hangs off the response that issued it.
        assert tool.parent_span_id == chats[tool_uses[_attrs(tool)["gen_ai.tool.call.id"]]].span_id


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_usage_is_counted_once_per_response_from_its_last_line(name):
    lines = _lines(name)
    spans = PROJECTOR.project_spans(_fixture_rows(name), session_closed=True)
    chats = {_attrs(s)["gen_ai.response.id"]: _attrs(s) for s in _by_kind(spans)["chat"]}
    for message_id, group in _responses(lines).items():
        usage = group[-1]["message"]["usage"]
        expected_output = usage.get("output_tokens", 0)
        tokens = {
            "input_tokens": usage.get("input_tokens", 0),
            "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
            "cache_write_tokens": usage.get("cache_creation_input_tokens", 0),
        }
        assert chats[message_id].get("gen_ai.usage.output_tokens", 0) == expected_output
        assert chats[message_id].get("gen_ai.usage.input_tokens", 0) == semconv_input_tokens(tokens)


def test_multi_line_response_is_one_chat_span_not_summed():
    lines = _lines("single_turn")
    responses = _responses(lines)
    message_id, group = next((mid, g) for mid, g in responses.items() if len(g) > 1)
    spans = PROJECTOR.project_spans(_fixture_rows("single_turn"), session_closed=True)
    [chat] = [s for s in spans if _attrs(s).get("gen_ai.response.id") == message_id]
    per_line = group[0]["message"]["usage"]["output_tokens"]
    assert _attrs(chat)["gen_ai.usage.output_tokens"] == per_line
    assert len(chat.record_offsets) == len(group)
    # What summing every stored row would report instead.
    assert per_line * len(group) > per_line


def test_parallel_tool_calls_each_close_on_their_own_result():
    lines = _lines("parallel_tools")
    rows = _fixture_rows("parallel_tools")
    spans = PROJECTOR.project_spans(rows, session_closed=True)
    results = _tool_results(lines)
    tools = _by_kind(spans)["execute_tool"]
    siblings = Counter(tool.parent_span_id for tool in tools)
    assert max(siblings.values()) >= 3
    for tool in tools:
        result_line, _block = results[_attrs(tool)["gen_ai.tool.call.id"]]
        assert tool.end_ns == to_unix_nanos(result_line["timestamp"])
        assert len(tool.record_offsets) == 2


def test_failed_tool_call_has_error_status():
    lines = _lines("failed_tool")
    spans = PROJECTOR.project_spans(_fixture_rows("failed_tool"), session_closed=True)
    failed = {tool_id for tool_id, (_line, block) in _tool_results(lines).items() if block.get("is_error")}
    assert failed
    for tool in _by_kind(spans)["execute_tool"]:
        assert tool.error is (_attrs(tool)["gen_ai.tool.call.id"] in failed)


def test_tool_without_result_is_only_sent_at_close_and_marked_incomplete():
    rows = _fixture_rows("interrupted_tool")
    open_spans = PROJECTOR.project_spans(rows, session_closed=False)
    closed_spans = PROJECTOR.project_spans(rows, session_closed=True)
    new = {s.span_id: s for s in closed_spans}.keys() - {s.span_id for s in open_spans}
    incomplete = [s for s in closed_spans if _attrs(s).get("observal.tool.incomplete")]
    assert incomplete
    for tool in incomplete:
        assert tool.span_id in new
        assert tool.start_ns == tool.end_ns
        assert not tool.error


def test_turns_close_when_the_next_prompt_arrives():
    rows = _fixture_rows("three_turns")
    open_turns = [s for s in PROJECTOR.project_spans(rows, session_closed=False) if s.name.startswith("turn ")]
    closed_turns = [s for s in PROJECTOR.project_spans(rows, session_closed=True) if s.name.startswith("turn ")]
    assert [s.name for s in closed_turns] == ["turn 1", "turn 2", "turn 3"]
    assert [s.name for s in open_turns] == ["turn 1", "turn 2"]
    assert all(_attrs(s)["observal.turn.index"] == i for i, s in enumerate(closed_turns, start=1))


def test_subagent_hangs_off_the_agent_tool_span_in_the_parent_trace():
    parent_rows = _fixture_rows("subagent_parent")
    child_rows = _fixture_rows("subagent_child")
    parent = PROJECTOR.project_spans(parent_rows, session_closed=True)
    child = PROJECTOR.project_spans(child_rows, session_closed=True)
    agent_id = _subagent_id()

    [agent_tool] = [s for s in parent if _attrs(s).get("observal.subagent.id") == agent_id]
    assert agent_tool.span_id == span_id("sess-subagent-parent", f"agent:{agent_id}")
    [child_session] = _by_kind(child)["invoke_agent"]
    assert child_session.parent_span_id == agent_tool.span_id
    assert child_session.name == "invoke_agent subagent"
    assert {s.trace_id for s in child} == {trace_id_for("sess-subagent-parent")}
    assert _attrs(child_session)["session.id"] == "sess-subagent-parent"
    assert _attrs(child_session)["observal.parent_session.id"] == "sess-subagent-parent"
    _assert_well_formed(parent + child)


def test_replayed_history_adds_no_spans():
    lines = _lines("replayed_history")
    rows = _fixture_rows("replayed_history")
    spans = PROJECTOR.project_spans(rows, session_closed=True)
    _assert_well_formed(spans)
    assistant = [line for line in lines if line.get("type") == "assistant"]
    assert len(_originals(lines)) < len(lines)
    # Replayed responses map to the chat spans of their originals.
    chats = [s for s in spans if _attrs(s).get("gen_ai.operation.name") == "chat"]
    assert len(chats) == len({line["message"]["id"] for line in assistant})
    covered = [offset for s in chats for offset in s.record_offsets]
    assert len(covered) == len(set(covered)) == sum(1 for line in _originals(lines) if line.get("type") == "assistant")
    record_ids = PROJECTOR.record_span_ids(rows)
    by_message = {_attrs(s)["gen_ai.response.id"]: s.span_id for s in chats}
    for offset, line in enumerate(lines):
        if line.get("type") == "assistant":
            assert record_ids[offset] == by_message[line["message"]["id"]]


def test_queued_prompt_written_late_still_starts_a_turn():
    # Prompts typed while Claude Code is busy are written later, stamped with the time they were queued.
    rows = _rows(
        [
            _user("u1", "2026-09-26T10:00:00Z", "first"),
            _assistant("a1", "2026-09-26T10:00:50Z", "msg_1", [{"type": "text", "text": "done"}]),
            _user("u2", "2026-09-26T10:00:20Z", "queued while busy"),
            _assistant("a2", "2026-09-26T10:01:00Z", "msg_2", [{"type": "text", "text": "ok"}]),
        ]
    )
    spans = PROJECTOR.project_spans(rows, session_closed=True)
    assert [s.name for s in spans if s.name.startswith("turn ")] == ["turn 1", "turn 2"]
    assert len([s for s in spans if s.name.startswith("chat")]) == 2


def test_replay_under_a_new_uuid_is_recognised_by_its_content():
    prompt = _user("u1", "2026-09-26T10:00:00Z", "first")
    answer = _assistant("a1", "2026-09-26T10:00:05Z", "msg_1", [{"type": "text", "text": "done"}])
    rows = _rows(
        [
            prompt,
            answer,
            {**prompt, "uuid": "u1-copy"},
            {**answer, "uuid": "a1-copy"},
            _user("u2", "2026-09-26T10:05:00Z", "next"),
        ]
    )
    spans = PROJECTOR.project_spans(rows, session_closed=True)
    assert [s.name for s in spans if s.name.startswith("turn ")] == ["turn 1", "turn 2"]
    [chat] = [s for s in spans if s.name.startswith("chat")]
    assert chat.record_offsets == (1,)
    assert PROJECTOR.record_span_ids(rows)[3] == chat.span_id


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_every_row_maps_to_a_span_that_is_sent(name):
    rows = _fixture_rows(name)
    record_ids = PROJECTOR.record_span_ids(rows)
    assert set(record_ids) == {row["line_offset"] for row in rows}
    sent = {span.span_id for span in PROJECTOR.project_spans(rows, session_closed=True)}
    assert set(record_ids.values()) <= sent


# ---------------------------------------------------------------------------
# Hand-built edge cases
# ---------------------------------------------------------------------------


def _user(uuid: str, ts: str, content, **extra) -> dict:
    return {"type": "user", "uuid": uuid, "timestamp": ts, "message": {"role": "user", "content": content}, **extra}


def _assistant(uuid: str, ts: str, message_id: str, blocks: list[dict], usage: dict | None = None, **extra) -> dict:
    message = {"role": "assistant", "id": message_id, "model": "claude-sonnet-5", "content": blocks}
    if usage is not None:
        message["usage"] = usage
    return {"type": "assistant", "uuid": uuid, "timestamp": ts, "message": message, **extra}


def _result(uuid: str, ts: str, tool_use_id: str, content: str = "ok", **block_extra) -> dict:
    block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content, **block_extra}
    return _user(uuid, ts, [block])


def test_meta_compaction_and_interruptions_do_not_start_turns():
    rows = _rows(
        [
            _user("u1", "2026-09-26T10:00:00Z", "first prompt"),
            _user("u2", "2026-09-26T10:00:01Z", "<local-command-caveat>x</local-command-caveat>", isMeta=True),
            _user("u3", "2026-09-26T10:00:02Z", "summary", isCompactSummary=True),
            _user("u4", "2026-09-26T10:00:03Z", [{"type": "text", "text": "[Request interrupted by user]"}]),
            {"type": "system", "subtype": "compact_boundary", "uuid": "s1", "timestamp": "2026-09-26T10:00:04Z"},
        ]
    )
    spans = PROJECTOR.project_spans(rows, session_closed=True)
    [turn] = [s for s in spans if s.name.startswith("turn ")]
    assert [e.name for e in turn.events] == [
        "user.meta",
        "compact_summary",
        "user.interrupted",
        "system.compact_boundary",
    ]
    assert turn.end_ns == to_unix_nanos("2026-09-26T10:00:04Z")


def test_orphan_result_becomes_a_zero_length_tool_span():
    rows = _rows([_user("u1", "2026-09-26T10:00:00Z", "go"), _result("u2", "2026-09-26T10:00:05Z", "toolu_x")])
    spans = PROJECTOR.project_spans(rows, session_closed=False)
    [orphan] = spans
    assert _attrs(orphan)["observal.tool.orphan_result"] is True
    assert orphan.start_ns == orphan.end_ns == to_unix_nanos("2026-09-26T10:00:05Z")
    assert orphan.parent_span_id == span_id("sess-1", "turn:0")


def test_api_error_response_has_error_status():
    rows = _rows(
        [
            _user("u1", "2026-09-26T10:00:00Z", "go"),
            _assistant(
                "a1", "2026-09-26T10:00:01Z", "msg_err", [{"type": "text", "text": "API Error"}], isApiErrorMessage=True
            ),
        ]
    )
    [chat] = [s for s in PROJECTOR.project_spans(rows, session_closed=True) if s.name.startswith("chat")]
    assert chat.error


def test_usage_takes_the_last_line_when_lines_differ():
    rows = _rows(
        [
            _user("u1", "2026-09-26T10:00:00Z", "go"),
            _assistant("a1", "2026-09-26T10:00:01Z", "msg_1", [{"type": "text", "text": "a"}], {"output_tokens": 1}),
            _assistant("a2", "2026-09-26T10:00:02Z", "msg_1", [{"type": "text", "text": "b"}], {"output_tokens": 40}),
        ]
    )
    [chat] = [s for s in PROJECTOR.project_spans(rows, session_closed=True) if s.name.startswith("chat")]
    assert _attrs(chat)["gen_ai.usage.output_tokens"] == 40
    assert chat.start_ns == to_unix_nanos("2026-09-26T10:00:00Z")
    assert chat.end_ns == to_unix_nanos("2026-09-26T10:00:02Z")


def test_response_stays_open_across_interleaved_tool_results():
    rows = _rows(
        [
            _user("u1", "2026-09-26T10:00:00Z", "go"),
            _assistant(
                "a1", "2026-09-26T10:00:01Z", "msg_1", [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}]
            ),
            _result("u2", "2026-09-26T10:00:02Z", "t1"),
            _assistant(
                "a2", "2026-09-26T10:00:03Z", "msg_1", [{"type": "tool_use", "id": "t2", "name": "Read", "input": {}}]
            ),
            _result("u3", "2026-09-26T10:00:04Z", "t2", is_error=True),
        ]
    )
    spans = PROJECTOR.project_spans(rows, session_closed=False)
    assert [s.name for s in spans] == ["execute_tool Read", "execute_tool Read"]
    assert [s.error for s in spans] == [False, True]
    [chat] = [s for s in PROJECTOR.project_spans(rows, session_closed=True) if s.name.startswith("chat")]
    assert chat.record_offsets == (1, 3)
    assert {s.parent_span_id for s in spans} == {chat.span_id}


def test_main_session_ignores_old_style_sidechain_records():
    rows = _rows(
        [
            _user("u1", "2026-09-26T10:00:00Z", "go"),
            _user("u2", "2026-09-26T10:00:01Z", "subagent prompt", isSidechain=True),
            _assistant("a1", "2026-09-26T10:00:02Z", "msg_side", [{"type": "text", "text": "x"}], isSidechain=True),
            {
                "type": "system",
                "subtype": "informational",
                "uuid": "s1",
                "timestamp": "2026-09-26T10:00:03Z",
                "isSidechain": True,
            },
        ]
    )
    spans = PROJECTOR.project_spans(rows, session_closed=True)
    assert [s.name for s in spans] == ["turn 1", "invoke_agent claude-code"]
    assert all(not s.events for s in spans)


def test_subagent_transcript_stored_without_its_parent_still_projects():
    # All sidechain records, as in a subagent file pushed without parent_session_id.
    rows = _rows(
        [
            _user("u1", "2026-09-26T10:00:00Z", "subagent prompt", isSidechain=True),
            _assistant("a1", "2026-09-26T10:00:02Z", "msg_1", [{"type": "text", "text": "x"}], isSidechain=True),
        ]
    )
    spans = PROJECTOR.project_spans(rows, session_closed=True)
    assert [s.name for s in spans] == ["chat claude-sonnet-5", "turn 1", "invoke_agent claude-code"]


def test_cut_line_falls_back_to_the_stored_tool_columns():
    call = _assistant(
        "a1", "2026-09-26T10:00:02Z", "msg_1", [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]
    )
    rows = _rows(
        [
            _user("u1", "2026-09-26T10:00:00Z", "go"),
            _assistant("a0", "2026-09-26T10:00:01Z", "msg_1", [{"type": "text", "text": "running"}]),
            call,
            _result("u2", "2026-09-26T10:00:05Z", "t1"),
        ]
    )
    rows[2]["raw_line"] = rows[2]["raw_line"][:40]
    rows[2]["raw_line_truncated"] = 1
    [tool] = [s for s in PROJECTOR.project_spans(rows, session_closed=False) if s.name.startswith("execute_tool")]
    assert tool.name == "execute_tool Bash"
    assert tool.start_ns == to_unix_nanos("2026-09-26 10:00:02.000")
    assert tool.end_ns == to_unix_nanos("2026-09-26T10:00:05Z")


def test_empty_session_projects_nothing():
    assert PROJECTOR.project_spans([], session_closed=True) == []
    assert PROJECTOR.record_span_ids([]) == {}


# ---------------------------------------------------------------------------
# Properties the forwarder relies on
# ---------------------------------------------------------------------------

_SESSIONS = {name: _fixture_rows(name) for name in ALL_FIXTURES}
_PROPERTY_SETTINGS = settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@st.composite
def _prefixes(draw):
    name = draw(st.sampled_from(ALL_FIXTURES))
    rows = _SESSIONS[name]
    i = draw(st.integers(min_value=0, max_value=len(rows)))
    j = draw(st.integers(min_value=i, max_value=len(rows)))
    return rows, i, j


@st.composite
def _batches(draw):
    name = draw(st.sampled_from(ALL_FIXTURES))
    rows = _SESSIONS[name]
    cuts = draw(st.lists(st.integers(min_value=1, max_value=len(rows)), max_size=8, unique=True))
    return rows, sorted(cuts)


@_PROPERTY_SETTINGS
@given(_prefixes())
def test_projection_is_monotonic(case):
    rows, i, j = case
    shorter = PROJECTOR.project_spans(rows[:i], session_closed=False)
    for session_closed in (False, True):
        longer = {s.span_id: s for s in PROJECTOR.project_spans(rows[:j], session_closed=session_closed)}
        for span in shorter:
            assert longer.get(span.span_id) == span


@_PROPERTY_SETTINGS
@given(_prefixes())
def test_record_span_ids_never_change_once_assigned(case):
    rows, i, j = case
    shorter = PROJECTOR.record_span_ids(rows[:i])
    longer = PROJECTOR.record_span_ids(rows[:j])
    assert {offset: longer[offset] for offset in shorter} == dict(shorter)


@_PROPERTY_SETTINGS
@given(_batches())
def test_spans_do_not_depend_on_batching(case):
    """The forwarder's diff: each batch sends project(new) minus project(old), then the close."""
    rows, cuts = case
    sent: dict[str, Span] = {}
    watermark = 0
    for cut in [*cuts, len(rows)]:
        before = {s.span_id for s in PROJECTOR.project_spans(rows[:watermark], session_closed=False)}
        closing = cut == len(rows) and cut == [*cuts, len(rows)][-1]
        for span in PROJECTOR.project_spans(rows[:cut], session_closed=False):
            if span.span_id not in before:
                assert span.span_id not in sent
                sent[span.span_id] = span
        watermark = cut
        if closing:
            already = set(sent)
            for span in PROJECTOR.project_spans(rows, session_closed=True):
                if span.span_id not in already:
                    sent[span.span_id] = span
    expected = {s.span_id: s for s in PROJECTOR.project_spans(rows, session_closed=True)}
    assert sent == expected
