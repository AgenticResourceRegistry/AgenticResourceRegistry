# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Codex insights must read rollout content, including previously misclassified rows."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.insights.scope import SessionKey
from services.insights.session_meta_extractor import extract_session_meta


def item(payload, kind="response_item"):
    """Construct a rollout record without private session content."""
    return {"type": kind, "timestamp": "2026-11-05T10:00:00Z", "payload": payload}


def message(role, text):
    """Use Codex's input/output text block shapes."""
    return item(
        {
            "type": "message",
            "role": role,
            "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}],
        }
    )


async def build(monkeypatch, entries):
    """Query constructed old system-classified rows through the real transcript path."""
    from services.insights import transcript

    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {
            "data": [
                {"line_offset": i, "event_type": "system", "raw_line": json.dumps(e)} for i, e in enumerate(entries)
            ]
        },
    )
    monkeypatch.setattr(transcript, "get_query", lambda: AsyncMock(return_value=response))
    return await transcript.build_session_transcript(SessionKey("default", "user", "codex", "session"))


@pytest.mark.asyncio
@pytest.mark.parametrize("echo_first", [False, True])
async def test_transcript_recovers_old_prompts_and_replies_without_echoes(monkeypatch, echo_first):
    """Recover semantic messages without trusting old event_type or duplicating echoes."""
    prompt = message("user", "Fix the failing test")
    prompt_echo = item({"type": "user_message", "message": "Fix the failing test"}, "event_msg")
    reply = message("assistant", "The tests now pass")
    reply_echo = item({"type": "agent_message", "message": "The tests now pass"}, "event_msg")
    pairs = [(prompt_echo, prompt), (reply_echo, reply)] if echo_first else [(prompt, prompt_echo), (reply, reply_echo)]
    entries = [
        message("user", "# AGENTS.md instructions for /repo\nprivate instructions"),
        *(e for pair in pairs for e in pair),
        item({"type": "reasoning", "summary": [{"text": "hidden reasoning"}]}),
    ]
    assert await build(monkeypatch, entries) == "[User]: Fix the failing test\n[Assistant]: The tests now pass"
    meta = extract_session_meta("session", [json.dumps(e) for e in entries], "codex")
    assert (meta["user_message_count"], meta["assistant_message_count"]) == (1, 1)


@pytest.mark.asyncio
async def test_event_only_messages_are_preserved_and_measured(monkeypatch):
    """Event-message-only sessions retain user prompts and assistant replies."""
    entries = [
        item({"type": "user_message", "message": "Run tests"}, "event_msg"),
        item({"type": "agent_message", "message": "Two tests passed"}, "event_msg"),
    ]
    assert await build(monkeypatch, entries) == "[User]: Run tests\n[Assistant]: Two tests passed"
    meta = extract_session_meta("session", [json.dumps(e) for e in entries], "codex")
    assert (meta["user_message_count"], meta["assistant_message_count"]) == (1, 1)
    assert meta["first_prompt"] == "Run tests"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output",
    [
        "Process exited with code 1\nPermission denied",
        {"exit_code": 1, "output": "Permission denied"},
        {"error": "Permission denied"},
    ],
)
@pytest.mark.parametrize("custom", [False, True])
async def test_tool_input_and_error_are_visible_and_measured(monkeypatch, output, custom):
    """Function and freeform calls correlate errors to their tool without losing input."""
    call = {"type": "custom_tool_call" if custom else "function_call", "name": "exec_command", "call_id": "c1"}
    call["input" if custom else "arguments"] = "pytest tests/" if custom else json.dumps({"cmd": "pytest tests/"})
    result = {
        "type": "custom_tool_call_output" if custom else "function_call_output",
        "call_id": "c1",
        "output": output,
    }
    entries = [item(call), item(result)]
    text = await build(monkeypatch, entries)
    assert "[Tool: exec_command] pytest tests/" in text
    assert "[Tool Result: exec_command] ERROR:" in text and "Permission denied" in text
    assert extract_session_meta("session", [json.dumps(e) for e in entries], "codex")["tool_errors"] == 1


@pytest.mark.asyncio
async def test_repeated_turns_and_successful_tool_output_are_not_false_errors(monkeypatch):
    """Repeated typed prompts are distinct; successful stdout mentioning errors is not failure."""
    entries = [
        message("user", "Run tests"),
        item({"type": "function_call", "name": "exec_command", "call_id": "c1", "arguments": '{"cmd":"pytest"}'}),
        item({"type": "function_call_output", "call_id": "c1", "output": "Process exited with code 0\n0 errors"}),
        message("assistant", "Done"),
        message("user", "Run tests"),
        message("assistant", "Done"),
    ]
    text = await build(monkeypatch, entries)
    assert text.count("[User]: Run tests") == 2
    assert text.count("[Assistant]: Done") == 2
    assert "ERROR:" not in text
    assert extract_session_meta("session", [json.dumps(e) for e in entries], "codex")["tool_errors"] == 0


@pytest.mark.asyncio
async def test_store_upgrades_existing_scoped_codex_facet(monkeypatch):
    """Upgrade the unique existing row rather than inserting a duplicate for version two."""
    from models.insight_session_facets import InsightSessionFacets
    from services.insights import facets

    row = SimpleNamespace(facets={"old": True}, facet_version=1)
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: row)),
        flush=AsyncMock(),
        add=lambda _: pytest.fail("must upgrade existing row"),
    )
    monkeypatch.setattr(facets, "get_facets_model", lambda: InsightSessionFacets)
    await facets.store_facets(SessionKey("default", "user", "codex", "session"), "", {"new": True}, db)
    assert row.facet_version == 2 and row.facets == {"new": True}


def test_only_codex_facets_are_invalidated():
    """A Codex transcript fix must not discard cached work for unchanged harnesses."""
    from services.insights.facets import facet_version

    assert facet_version("codex") == 2
    assert facet_version("kiro") == facet_version("pi") == 1


@pytest.mark.asyncio
async def test_cached_codex_version_one_is_not_reused(monkeypatch):
    """Stale Codex facets are excluded while an unchanged harness cache stays eligible."""
    from models.insight_session_facets import InsightSessionFacets
    from services.insights import facets

    codex = SessionKey("default", "user", "codex", "session")
    control = SessionKey("default", "user", "pi", "session")
    rows = [
        SimpleNamespace(
            project_id=k.project_id,
            user_id=k.user_id,
            harness=k.harness,
            session_id=k.session_id,
            facet_version=1,
            facets={"old": True},
        )
        for k in [codex, control]
    ]
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows)))
    )
    monkeypatch.setattr(facets, "get_facets_model", lambda: InsightSessionFacets)
    assert await facets.load_cached_facets_batch([codex, control], db) == {control: {"old": True}}
    params = db.execute.await_args.args[0].compile().params
    assert [(*codex.as_tuple(), 2), (*control.as_tuple(), 1)] in params.values()


@pytest.mark.asyncio
async def test_facet_lookups_are_bounded(monkeypatch):
    """Keep five-column scoped lookups below the asyncpg bind parameter limit."""
    from models.insight_session_facets import InsightSessionFacets
    from services.insights import facets

    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [])))
    )
    monkeypatch.setattr(facets, "get_facets_model", lambda: InsightSessionFacets)
    keys = [SessionKey("default", "user", "codex", str(i)) for i in range(1001)]
    assert await facets.load_cached_facets_batch(keys, db) == {}
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_malformed_rollout_rows_do_not_leak_context_or_crash(monkeypatch):
    """Skip malformed records and hidden reasoning without dropping the valid reply."""
    entries = [
        [],
        None,
        {"type": "event_msg", "payload": {"type": []}},
        {"type": "response_item", "payload": []},
        item({"type": "reasoning", "encrypted_content": "sealed"}),
        message("assistant", "Visible"),
    ]
    assert await build(monkeypatch, entries) == "[Assistant]: Visible"


@pytest.mark.asyncio
async def test_unknown_stored_harness_keeps_generic_transcript(monkeypatch):
    """Historical unregistered harness values retain the existing generic formatter."""
    from services.insights import transcript
    from services.session_parsers import parse_raw_events, transcript_rows

    rows = [{"event_type": "assistant_text", "raw_line": '{"message":{"content":"Historical reply"}}'}]
    assert transcript_rows("retired-harness", rows) is rows
    response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": rows})
    monkeypatch.setattr(transcript, "get_query", lambda: AsyncMock(return_value=response))
    result = await transcript.build_session_transcript(SessionKey("default", "user", "retired-harness", "session"))
    assert result == "[Assistant]: Historical reply"
    # The telemetry read parser remains strict; only the insight formatter falls back.
    with pytest.raises(KeyError):
        parse_raw_events([{"harness": "retired-harness", "raw_line": "{}"}])


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("[" * 10000 + "0" + "]" * 10000, False),
        ('{"exit_code":' + "9" * 5000 + "}", True),
        ("Process exited with code " + "0" * 5000, False),
        ("Process exited with code -" + "0" * 5000, False),
        ("Process exited with code -0001", True),
        ("Process exited with code \u0660\u0660\u0660", False),
        ("Process exited with code -\u0660\u0660\u0660", False),
        ("Process exited with code \u0660\u0661", True),
    ],
    ids=[
        "deep-json",
        "oversized-nonzero",
        "oversized-zero",
        "oversized-negative-zero",
        "negative-one",
        "unicode-zero",
        "unicode-negative-zero",
        "unicode-one",
    ],
)
def test_untrusted_tool_output_does_not_crash_and_preserves_zero_semantics(output, expected):
    """JSON depth and integer limits cannot turn tool output into a session failure."""
    from services.insights.transcript import _format_rows

    entries = [
        item({"type": "function_call", "name": "exec_command", "call_id": "c1", "arguments": '{"cmd":"pytest"}'}),
        item({"type": "function_call_output", "call_id": "c1", "output": output}),
    ]
    text = _format_rows([{"raw_line": json.dumps(e)} for e in entries], harness="codex")
    assert ("ERROR:" in text) is expected
    assert extract_session_meta("session", [json.dumps(e) for e in entries], "codex")["tool_errors"] == int(expected)


@pytest.mark.parametrize("tool", ["exec_command", "mcp__inspect"])
def test_deeply_nested_tool_arguments_do_not_abort_insights(tool):
    """Embedded input JSON needs the same depth guard as embedded tool output."""
    from services.insights.transcript import _format_rows

    entry = item({"type": "function_call", "name": tool, "call_id": "c1", "arguments": "[" * 10000 + "0" + "]" * 10000})
    assert f"[Tool: {tool}]" in _format_rows([{"raw_line": json.dumps(entry)}], harness="codex")
    assert extract_session_meta("session", [json.dumps(entry)], "codex")["tool_counts"] == {tool: 1}


@pytest.mark.parametrize(
    "boundary",
    [
        item({"model": "test-model"}, "turn_context"),
        item({"type": "task_started"}, "event_msg"),
        item({"type": "task_complete"}, "event_msg"),
    ],
    ids=["turn-context", "task-started", "task-complete"],
)
@pytest.mark.parametrize("echo_first", [False, True])
def test_explicit_turn_boundary_preserves_repeated_prompt(boundary, echo_first):
    """Equal text across a turn boundary is a new prompt, not a cross-format echo."""
    from services.insights.transcript import _format_rows

    response = message("user", "Run tests")
    event = item({"type": "user_message", "message": "Run tests"}, "event_msg")
    first, second = (event, response) if echo_first else (response, event)
    entries = [first, boundary, second]
    assert (
        _format_rows([{"raw_line": json.dumps(e)} for e in entries], harness="codex")
        == "[User]: Run tests\n[User]: Run tests"
    )
    assert extract_session_meta("session", [json.dumps(e) for e in entries], "codex")["user_message_count"] == 2


@pytest.mark.parametrize("echo_first", [False, True])
def test_token_metadata_does_not_break_echo_matching(echo_first):
    """Usage metadata between paired message representations does not duplicate a prompt."""
    from services.insights.transcript import _format_rows

    response = message("user", "Run tests")
    event = item({"type": "user_message", "message": "Run tests"}, "event_msg")
    first, second = (event, response) if echo_first else (response, event)
    entries = [first, item({"type": "token_count", "info": {}}, "event_msg"), second]
    assert _format_rows([{"raw_line": json.dumps(e)} for e in entries], harness="codex") == "[User]: Run tests"
    assert extract_session_meta("session", [json.dumps(e) for e in entries], "codex")["user_message_count"] == 1
