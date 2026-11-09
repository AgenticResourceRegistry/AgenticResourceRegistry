# SPDX-FileCopyrightText: 2026 Shreem Seth <shreemseth26@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Behavioral coverage for canonical session ingestion."""

from __future__ import annotations

import hashlib
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import xxhash

from services import session_ingest
from services.clickhouse.insert import _dumps
from services.secrets_redactor import REDACTED
from tests.test_antigravity_session_parser import USER_PROMPT as ANTIGRAVITY_USER_PROMPT
from tests.test_claude_code_session_parser import (
    ASSISTANT_TEXT as CLAUDE_ASSISTANT_TEXT,
)
from tests.test_claude_code_session_parser import (
    ASSISTANT_TOOL_USE as CLAUDE_TOOL_CALL,
)
from tests.test_claude_code_session_parser import USER_SIMPLE as CLAUDE_USER_PROMPT
from tests.test_goose_session_parser import _TOOL_REQUEST as GOOSE_TOOL_REQUEST
from tests.test_goose_session_parser import _message as goose_message
from tests.test_pi_session_parser import ASSISTANT_THINKING as PI_ASSISTANT_TEXT
from tests.test_pi_session_parser import TOOL_CALL as PI_TOOL_CALL
from tests.test_pi_session_parser import USER_PROMPT as PI_USER_PROMPT

SESSION_ID = "session-1"
PROJECT_ID = "project-1"
USER_ID = "user-1"


@pytest.fixture()
def external_calls(monkeypatch):
    """Replace only the service's external persistence and identity calls."""
    calls = SimpleNamespace(
        dedup=AsyncMock(return_value={}),
        checkpoint=AsyncMock(return_value=(-1, 0)),
        manifest=AsyncMock(return_value=[]),
        records=AsyncMock(return_value=[]),
        insert=AsyncMock(),
        insert_checkpoint=AsyncMock(),
        refresh=AsyncMock(),
        resolve_agent=AsyncMock(side_effect=lambda value: value),
        resolve_version=AsyncMock(side_effect=lambda _agent_id, value: value),
    )
    monkeypatch.setattr(session_ingest, "query_existing_for_dedup", calls.dedup)
    monkeypatch.setattr(session_ingest, "query_session_checkpoint", calls.checkpoint)
    monkeypatch.setattr(session_ingest, "query_session_source_manifest", calls.manifest)
    monkeypatch.setattr(session_ingest, "query_source_records_after", calls.records)
    monkeypatch.setattr(session_ingest, "insert_session_events", calls.insert)
    monkeypatch.setattr(session_ingest, "insert_session_checkpoint", calls.insert_checkpoint)
    monkeypatch.setattr(session_ingest, "refresh_session_summary", calls.refresh)
    monkeypatch.setattr(session_ingest, "_resolve_agent_id", calls.resolve_agent)
    monkeypatch.setattr(session_ingest, "_resolve_agent_version", calls.resolve_version)
    return calls


async def _ingest(lines: list[str] | None, **overrides) -> session_ingest.IngestResult:
    arguments = {
        "session_id": SESSION_ID,
        "project_id": PROJECT_ID,
        "user_id": USER_ID,
        "agent_id": None,
        "agent_version": None,
        "harness": "claude-code",
        "lines": lines,
    }
    arguments.update(overrides)
    return await session_ingest.ingest_session_lines(**arguments)


def _hash_manifest(source_hashes: list[str]) -> str:
    hasher = hashlib.sha256()
    for source_hash in source_hashes:
        hasher.update(source_hash.encode())
        hasher.update(b"\n")
    return hasher.hexdigest()


KIRO_TOOL_CALL = json.dumps(
    {
        "kind": "AssistantMessage",
        "data": {
            "meta": {"timestamp": 1_767_225_600},
            "content": [
                {
                    "kind": "toolUse",
                    "data": {"name": "read", "toolUseId": "kiro-tool", "input": {"path": "README.md"}},
                }
            ],
        },
    }
)
# Kiro IDE transcripts envelope every record as {id, timestamp, payload} and
# dispatch on payload.type, unlike the CLI's flat "kind" records above.
KIRO_IDE_PROMPT = json.dumps(
    {
        "id": "ide-prompt-1",
        "timestamp": "2026-09-26T00:00:00.000Z",
        "payload": {"type": "user", "content": "pull the agent called test"},
    }
)
KIRO_IDE_TOOL_CALL = json.dumps(
    {
        "id": "ide-call-1",
        "timestamp": "2026-09-26T00:00:01.000Z",
        "payload": {
            "type": "tool_call",
            "toolCallId": "toolu_ide_1",
            "toolName": "executeBash",
            "args": {"command": "observal agent list"},
        },
    }
)
KIRO_IDE_REASONING = json.dumps(
    {
        "id": "ide-reason-1",
        "timestamp": "2026-09-26T00:00:02.000Z",
        "payload": {"type": "assistant", "content": "thinking...", "operationType": "Reasoning"},
    }
)
KIRO_IDE_TURN_START = json.dumps(
    {
        "id": "ide-turn-1",
        "timestamp": "2026-09-26T00:00:03.000Z",
        "payload": {"type": "turn_start", "executionId": "exec-1"},
    }
)
CURSOR_TOOL_CALL = json.dumps(
    {
        "role": "assistant",
        "uuid": "cursor-child",
        "parentUuid": "cursor-parent",
        "message": {
            "model": "cursor-model",
            "usage": {"input_tokens": 8, "output_tokens": 3},
            "content": [{"type": "tool_use", "id": "cursor-tool", "name": "read"}],
        },
    }
)
CODEX_USAGE = json.dumps(
    {
        "type": "event_msg",
        "timestamp": "2026-06-01T10:00:09.000Z",
        "payload": {
            "type": "token_count",
            "info": {"last_token_usage": {"input_tokens": 21, "output_tokens": 13, "cached_input_tokens": 5}},
        },
    }
)
COPILOT_CLI_TOOL_CALL = json.dumps(
    {
        "id": "copilot-tool-id",
        "type": "tool.call",
        "ts": "2026-06-01T10:00:10.000Z",
        "data": {"name": "shell"},
    }
)
COPILOT_USAGE = json.dumps(
    {
        "agentId": "copilot-agent",
        "event": {
            "type": "assistant.usage",
            "ts": "2026-06-01T10:00:11.000Z",
            "data": {"inputTokens": 34, "outputTokens": 18, "model": "gpt-test"},
        },
    }
)
GOOSE_TOOL_CALL = json.dumps(
    goose_message(
        "assistant",
        [GOOSE_TOOL_REQUEST],
        metadata={
            "usage": {"inputTokens": 12, "outputTokens": 4},
            "inference": {"resolvedModel": "goose-model"},
        },
    )
)


@pytest.mark.parametrize(
    ("harness", "line", "expected"),
    [
        (
            "claude-code",
            CLAUDE_TOOL_CALL,
            {
                "event_type": "tool_call",
                "tool_name": "read_file",
                "tool_id": "tool_123",
                "uuid": "126",
                "timestamp": "2026-06-01 10:00:07.000",
            },
        ),
        (
            "kiro",
            KIRO_TOOL_CALL,
            {
                "event_type": "tool_call",
                "tool_name": "read",
                "tool_id": "kiro-tool",
                "timestamp": "2026-01-01 00:00:00.000",
            },
        ),
        (
            "kiro",
            KIRO_IDE_PROMPT,
            {"event_type": "user_prompt", "uuid": "ide-prompt-1"},
        ),
        (
            "kiro",
            KIRO_IDE_TOOL_CALL,
            {
                "event_type": "tool_call",
                "tool_name": "executeBash",
                "tool_id": "toolu_ide_1",
                "uuid": "ide-call-1",
            },
        ),
        (
            "kiro",
            KIRO_IDE_REASONING,
            {"event_type": "thinking"},
        ),
        (
            "kiro",
            KIRO_IDE_TURN_START,
            {"event_type": "meta"},
        ),
        (
            "cursor",
            CURSOR_TOOL_CALL,
            {
                "event_type": "tool_call",
                "tool_name": "read",
                "tool_id": "cursor-tool",
                "uuid": "cursor-child",
                "parent_uuid": "cursor-parent",
                "input_tokens": 8,
                "output_tokens": 3,
                "model": "cursor-model",
            },
        ),
        (
            "pi",
            PI_TOOL_CALL,
            {
                "event_type": "tool_call",
                "tool_name": "read",
                "tool_id": "tooluse_abc123",
                "uuid": "cba220e0",
                "parent_uuid": "075da560",
                "input_tokens": 10,
                "output_tokens": 20,
            },
        ),
        (
            "codex",
            CODEX_USAGE,
            {
                "event_type": "meta",
                "input_tokens": 16,
                "output_tokens": 13,
                "cache_read_tokens": 5,
            },
        ),
        (
            "copilot-cli",
            COPILOT_CLI_TOOL_CALL,
            {
                "event_type": "tool_call",
                "tool_name": "shell",
                "tool_id": "copilot-tool-id",
                "timestamp": "2026-06-01 10:00:10.000",
            },
        ),
        (
            "copilot",
            COPILOT_USAGE,
            {
                "event_type": "usage",
                "input_tokens": 34,
                "output_tokens": 18,
                "model": "gpt-test",
                "timestamp": "2026-06-01 10:00:11.000",
            },
        ),
        (
            "opencode",
            CLAUDE_USER_PROMPT,
            {
                "event_type": "user_prompt",
                "uuid": "123",
                "content_preview": "Hello world",
            },
        ),
        (
            "antigravity",
            ANTIGRAVITY_USER_PROMPT,
            {
                "event_type": "user_prompt",
                "uuid": "0",
                "content_preview": "what files are in this directory",
            },
        ),
        (
            "goose",
            GOOSE_TOOL_CALL,
            {
                "event_type": "tool_call",
                "tool_name": "developer__shell",
                "tool_id": "tool-1",
                "uuid": "msg-1",
                "input_tokens": 12,
                "output_tokens": 4,
                "model": "goose-model",
            },
        ),
    ],
)
async def test_registered_harness_selects_its_parser_and_normalizes_the_write(
    external_calls, harness: str, line: str, expected: dict
):
    result = await _ingest([line], harness=harness, start_offset=4, end_byte_offsets=[123])

    assert result == session_ingest.IngestResult(ingested=1, skipped=0, errors=0)
    external_calls.dedup.assert_awaited_once_with(SESSION_ID, PROJECT_ID, USER_ID, harness, 4, 4)
    rows = external_calls.insert.await_args.args[0]
    assert len(rows) == 1
    row = rows[0]
    assert {key: row[key] for key in expected} == expected
    assert row["harness"] == harness
    assert row["line_offset"] == 4
    assert row["source_end_offset"] == 123
    assert row["is_source_record"] == 1
    assert row["rendered"] == 1
    external_calls.refresh.assert_awaited_once_with(SESSION_ID, PROJECT_ID, USER_ID, harness)


async def test_batch_normalizes_relationships_timestamps_usage_tools_and_result_counts(external_calls):
    assistant = json.loads(CLAUDE_ASSISTANT_TEXT)
    assistant["parentUuid"] = "123"
    assistant_line = json.dumps(assistant, separators=(",", ":"))
    ignored = json.dumps(
        {
            "uuid": "ignored",
            "parentUuid": "126",
            "type": "user",
            "message": {"content": []},
        }
    )
    lines = [CLAUDE_USER_PROMPT, assistant_line, CLAUDE_TOOL_CALL, ignored, "[]", "not-json"]
    end_offsets = [100, 200, 300, 400, 410, 420]

    result = await _ingest(lines, start_offset=10, end_byte_offsets=end_offsets)

    assert result == session_ingest.IngestResult(ingested=3, skipped=1, errors=2)
    rows = external_calls.insert.await_args.args[0]
    assert [(row["line_offset"], row["event_type"], row["rendered"]) for row in rows] == [
        (10, "user_prompt", 1),
        (11, "assistant_text", 1),
        (12, "tool_call", 1),
        (13, "_ignored", 0),
        (14, "_parse_error", 0),
        (15, "_parse_error", 0),
    ]
    assert [row["source_end_offset"] for row in rows] == end_offsets
    assert (rows[1]["uuid"], rows[1]["parent_uuid"]) == ("124", "123")
    expected_usage = {
        "input_tokens": 10,
        "output_tokens": 20,
        "cache_read_tokens": 5,
        "cache_write_tokens": 2,
        "model": "claude-3-5-sonnet-20241022",
    }
    assert {key: rows[1][key] for key in expected_usage} == expected_usage
    assert (rows[2]["tool_name"], rows[2]["tool_id"]) == ("read_file", "tool_123")
    assert rows[3]["timestamp"] == rows[2]["timestamp"]
    assert rows[4]["timestamp"] == rows[2]["timestamp"]
    assert rows[5]["timestamp"] == rows[2]["timestamp"]
    assert sum(row["input_tokens"] for row in rows) == 10
    assert sum(row["output_tokens"] for row in rows) == 20
    for raw_line, row in zip(lines, rows, strict=True):
        encoded = raw_line.encode()
        assert row["line_hash"] == xxhash.xxh128(encoded).hexdigest()
        assert row["source_sha256"] == hashlib.sha256(encoded).hexdigest()
        assert row["content_length"] == len(encoded)
    external_calls.refresh.assert_awaited_once_with(SESSION_ID, PROJECT_ID, USER_ID, "claude-code")


async def test_lone_surrogate_does_not_abort_other_session_lines(external_calls):
    malformed = '{"type":"user","message":"\ud800"}'
    valid = CLAUDE_USER_PROMPT

    result = await _ingest([malformed, valid])

    assert result == session_ingest.IngestResult(ingested=1, skipped=0, errors=1)
    rows = external_calls.insert.await_args.args[0]
    assert [row["event_type"] for row in rows] == ["_parse_error", "user_prompt"]
    assert rows[0]["content_length"] == len(malformed.encode("utf-8", errors="replace"))
    assert rows[0]["source_sha256"] == hashlib.sha256(malformed.encode("utf-8", errors="replace")).hexdigest()
    assert rows[0]["raw_line"] == malformed.encode("utf-8", errors="replace").decode("utf-8")
    assert '"event_type":"_parse_error"' in _dumps(rows[0])
    external_calls.refresh.assert_awaited_once()


async def test_malformed_normalization_isolated_without_leaking_source(external_calls, caplog, monkeypatch):
    secret = "PRIVATE_TRANSCRIPT_VALUE"
    broken = json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": secret}}})
    result = await _ingest([broken, CLAUDE_USER_PROMPT], end_byte_offsets=[42, 100])
    assert result == session_ingest.IngestResult(ingested=1, skipped=0, errors=1)
    rows = external_calls.insert.await_args.args[0]
    assert rows[0]["event_type"] == "_parse_error"
    assert rows[0]["rendered"] == 0
    assert rows[0]["source_end_offset"] == 42
    assert rows[0]["source_sha256"] == hashlib.sha256(broken.encode()).hexdigest()
    assert rows[0]["raw_line"] == broken
    assert rows[1]["line_offset"] == 1
    assert secret not in caplog.text

    external_calls.insert.reset_mock()
    external_calls.insert.side_effect = RuntimeError("storage unavailable")
    with pytest.raises(RuntimeError, match="storage unavailable"):
        await _ingest([broken])


@pytest.mark.parametrize(
    ("harness", "bad", "good", "expected_timestamp"),
    [
        (
            "claude-code",
            {"type": "user", "timestamp": "not-a-date", "message": {"content": "bad"}},
            {"type": "user", "timestamp": "2026-06-01T12:00:01+02:00", "message": {"content": "ok"}},
            "2026-06-01 10:00:01.000",
        ),
        (
            "pi",
            {"type": "message", "timestamp": "2026-99-99", "id": "bad"},
            {"type": "message", "timestamp": "2026-06-01T10:00:01Z", "id": "good"},
            "2026-06-01 10:00:01.000",
        ),
        (
            "kiro",
            {"id": "bad", "timestamp": "garbage", "payload": {"type": "user", "content": "bad"}},
            {"id": "good", "timestamp": "2026-06-01T10:00:01Z", "payload": {"type": "user", "content": "ok"}},
            "2026-06-01 10:00:01.000",
        ),
    ],
)
async def test_bad_timestamp_is_source_error_without_poisoning_neighbor(
    external_calls, harness, bad, good, expected_timestamp
):
    lines = [json.dumps(bad), json.dumps(good)]
    result = await _ingest(lines, harness=harness, end_byte_offsets=[31, 72])
    assert result == session_ingest.IngestResult(ingested=1, skipped=0, errors=1)
    failed, valid = external_calls.insert.await_args.args[0]
    assert (failed["event_type"], failed["line_offset"], failed["source_end_offset"]) == ("_parse_error", 0, 31)
    assert failed["line_hash"] == xxhash.xxh128(lines[0].encode()).hexdigest()
    assert failed["source_sha256"] == hashlib.sha256(lines[0].encode()).hexdigest()
    assert valid["timestamp"] == expected_timestamp


@pytest.mark.parametrize(
    ("field", "value"),
    [("uuid", {"invalid": "object"}), ("parentUuid", 99), ("model", ["not a string"]), ("model", 0)],
)
async def test_bad_string_columns_are_isolated(external_calls, field, value):
    broken = {"type": "assistant", "message": {"content": "ok", "usage": {"input_tokens": 1}}}
    if field == "model":
        broken["message"]["model"] = value
    else:
        broken[field] = value
    result = await _ingest([json.dumps(broken), CLAUDE_USER_PROMPT])
    assert result.errors == 1 and result.ingested == 1
    rows = external_calls.insert.await_args.args[0]
    assert rows[0]["event_type"] == "_parse_error"
    assert rows[1]["event_type"] == "user_prompt"


async def test_kiro_bad_model_isolated_from_valid_neighbor(external_calls):
    bad = {"id": "bad", "timestamp": "2026-06-01T10:00:00Z", "payload": {"type": "assistant", "modelId": []}}
    result = await _ingest([json.dumps(bad), KIRO_IDE_PROMPT], harness="kiro")
    assert result.errors == 1 and result.ingested == 1
    failed, valid = external_calls.insert.await_args.args[0]
    assert failed["event_type"] == "_parse_error"
    assert valid["event_type"] == "user_prompt"


async def test_non_string_tool_fields_do_not_poison_valid_neighbor(external_calls):
    bad = {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": {"invalid": "tool"}, "id": 123}]},
    }
    result = await _ingest([json.dumps(bad), CLAUDE_TOOL_CALL], end_byte_offsets=[25, 60])
    assert result == session_ingest.IngestResult(ingested=1, skipped=0, errors=1)
    failed, valid = external_calls.insert.await_args.args[0]
    assert failed["event_type"] == "_parse_error" and failed["source_end_offset"] == 25
    assert valid["tool_name"] == "read_file"


@pytest.mark.parametrize("overrides", [{"start_offset": 2**32}, {"end_byte_offsets": [2**64]}])
async def test_out_of_range_request_offsets_rejected_before_insert(external_calls, overrides):
    with pytest.raises(ValueError, match="offset outside ClickHouse"):
        await _ingest([CLAUDE_USER_PROMPT], **overrides)
    external_calls.insert.assert_not_awaited()


async def test_malformed_source_retains_only_redacted_raw_line(external_calls):
    secret = "sk-proj-abc123def456ghi789jkl012mno345"
    line = json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": f"OPENAI_API_KEY={secret}"}}})
    await _ingest([line])
    row = external_calls.insert.await_args.args[0][0]
    assert row["event_type"] == "_parse_error"
    assert secret not in row["raw_line"]
    assert REDACTED in row["raw_line"]


@pytest.mark.parametrize(
    ("harness", "record"),
    [
        ("claude-code", {"type": "assistant", "message": {"usage": {"input_tokens": 1e308}}}),
        ("cursor", {"type": "assistant", "message": {"usage": {"output_tokens": 2_147_483_648}}}),
        ("opencode", {"type": "assistant", "message": {"usage": {"cache_read_input_tokens": 2_147_483_648}}}),
        ("pi", {"type": "message", "message": {"usage": {"cacheWrite": 1e308}}}),
        ("codex", {"type": "event_msg", "payload": {"info": {"last_token_usage": {"input_tokens": 1e308}}}}),
        ("goose", {"type": "message", "metadata": {"usage": {"outputTokens": 1e308}}}),
        ("copilot-cli", {"type": "assistant.message", "data": {"inputTokens": 1e308}}),
        ("copilot", {"type": "assistant.message", "data": {"cacheReadTokens": 1e308, "inputTokens": 1}}),
    ],
)
async def test_token_overflow_isolated_from_following_source(external_calls, harness, record):
    with pytest.raises((ValueError, OverflowError)):
        session_ingest._extract_usage_tokens(record, harness)
    result = await _ingest([json.dumps(record), "{}"], harness=harness)
    rows = external_calls.insert.await_args.args[0]
    assert result.errors >= 1
    assert rows[0]["event_type"] == "_parse_error"
    assert rows[0]["input_tokens"] == rows[0]["output_tokens"] == 0
    assert len(rows) == 2
    assert rows[1]["line_offset"] == 1


def test_clickhouse_int32_token_boundary_is_accepted():
    maximum = 2_147_483_647
    usage = session_ingest._extract_usage_tokens(
        {
            "message": {
                "usage": {
                    "input_tokens": maximum,
                    "output_tokens": maximum,
                    "cache_read_input_tokens": maximum,
                    "cache_creation_input_tokens": maximum,
                }
            }
        },
        "claude-code",
    )
    assert all(
        usage[key] == maximum for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
    )


async def test_redactor_runtime_failure_does_not_persist_or_acknowledge(external_calls, monkeypatch):
    def fail(_text):
        raise RuntimeError("redactor unavailable")

    monkeypatch.setattr(session_ingest, "redact_secrets", fail)
    with pytest.raises(RuntimeError, match="redactor unavailable"):
        await _ingest([CLAUDE_USER_PROMPT])
    external_calls.insert.assert_not_awaited()


async def test_lone_surrogate_content_length_uses_hash_encoding(external_calls):
    line = '{"type":"user","message":{"content":"\ud800"}}'
    await _ingest([line])
    row = external_calls.insert.await_args.args[0][0]
    encoded = line.encode("utf-8", errors="replace")
    assert row["content_length"] == len(encoded)
    assert row["source_sha256"] == hashlib.sha256(encoded).hexdigest()
    assert row["line_hash"] == xxhash.xxh128(encoded).hexdigest()


async def test_ingest_api_reports_durable_error_offsets_on_response_lost_retry(monkeypatch):
    from api.routes import ingest
    from services import clickhouse

    line = "not json"
    monkeypatch.setattr(clickhouse, "query_session_error_offsets", AsyncMock(return_value=[4]))
    monkeypatch.setattr(
        session_ingest, "ingest_session_lines", AsyncMock(return_value=session_ingest.IngestResult(0, 1, 0))
    )
    monkeypatch.setattr(session_ingest, "advance_session_checkpoint", AsyncMock(return_value=(4, 42)))
    response = await ingest.ingest_session.__wrapped__(
        ingest.SessionIngestRequest(session_id=SESSION_ID, lines=[line], start_offset=4),
        SimpleNamespace(),
        current_user=SimpleNamespace(id=USER_ID),
    )
    assert response.errors == 1
    assert response.error_offsets == [4]
    assert response.acknowledged_line == 4
    clickhouse.query_session_error_offsets.assert_awaited_once_with(
        SESSION_ID,
        ingest.DEFAULT_PROJECT_ID,
        USER_ID,
        "claude-code",
        4,
        4,
    )


async def test_identity_metadata_and_source_accounting_survive_redaction(external_calls):
    canonical_agent = "8a11c861-45a9-4416-bc4f-d9d1586419f2"
    external_calls.resolve_agent.side_effect = None
    external_calls.resolve_agent.return_value = canonical_agent
    external_calls.resolve_version.side_effect = None
    external_calls.resolve_version.return_value = "2.3.0"
    secret = "sk-proj-abc123def456ghi789jkl012mno345"
    line = json.dumps(
        {
            "uuid": "child-event",
            "parentUuid": "parent-event",
            "type": "user",
            "message": {"content": f"OPENAI_API_KEY={secret}"},
            "timestamp": "2026-06-01T12:00:00.000Z",
        },
        separators=(",", ":"),
    )

    result = await _ingest(
        [line],
        agent_id="friendly-agent",
        agent_version="latest",
        layer_hash="layer-hash",
        parent_session_id="parent-session",
    )

    assert result.ingested == 1
    external_calls.resolve_agent.assert_awaited_once_with("friendly-agent")
    external_calls.resolve_version.assert_awaited_once_with(canonical_agent, "latest")
    row = external_calls.insert.await_args.args[0][0]
    expected_identity = {
        "session_id": SESSION_ID,
        "project_id": PROJECT_ID,
        "user_id": USER_ID,
        "agent_id": canonical_agent,
        "agent_version": "2.3.0",
        "layer_hash": "layer-hash",
        "parent_session_id": "parent-session",
        "uuid": "child-event",
        "parent_uuid": "parent-event",
    }
    assert {key: row[key] for key in expected_identity} == expected_identity
    assert secret not in row["content_preview"]
    assert secret not in row["raw_line"]
    assert REDACTED in row["content_preview"]
    assert REDACTED in row["raw_line"]
    assert row["content_length"] == len(line.encode())
    assert row["source_sha256"] == hashlib.sha256(line.encode()).hexdigest()


async def test_kiro_cli_model_id_is_persisted_on_assistant_row(external_calls):
    line = json.dumps(
        {
            "kind": "AssistantMessage",
            "data": {
                "content": [
                    {"kind": "text", "data": {"text": "hello", "modelId": "claude-opus-5.5"}},
                    {"kind": "toolUse", "data": {"toolUseId": "t1", "name": "read", "modelId": "claude-opus-5.5"}},
                ]
            },
        }
    )
    await _ingest([line], harness="kiro")

    row = external_calls.insert.await_args.args[0][0]
    assert row["model"] == "claude-opus-5.5"
    assert row["input_tokens"] == row["output_tokens"] == 0
    external_calls.refresh.assert_awaited_once_with(SESSION_ID, PROJECT_ID, USER_ID, "kiro")


async def test_kiro_model_extractor_ignores_malformed_blocks_and_does_not_invent_auto(external_calls):
    line = json.dumps({"kind": "Prompt", "data": {"content": [{"kind": "text", "data": "hello"}]}})
    await _ingest([line], harness="kiro")
    assert external_calls.insert.await_args.args[0][0]["model"] == ""


async def test_kiro_tool_lines_keep_prompt_time_in_event_timeline(external_calls):
    prompt = json.dumps(
        {"kind": "Prompt", "data": {"meta": {"timestamp": 1_767_225_600}, "content": [{"kind": "text", "data": "hi"}]}}
    )
    tool = json.dumps(
        {
            "kind": "AssistantMessage",
            "data": {"content": [{"kind": "toolUse", "data": {"name": "read", "toolUseId": "t1"}}]},
        }
    )
    await _ingest([prompt, tool], harness="kiro")

    rows = external_calls.insert.await_args.args[0]
    assert rows[0]["timestamp"] == rows[1]["timestamp"] == "2026-01-01 00:00:00.000"
    assert all(row["is_source_record"] == 1 for row in rows)


async def test_empty_kiro_input_writes_only_credit_metadata(external_calls):
    result = await _ingest([], harness="kiro", total_credits=1.25, agent_id="agent", agent_version="1.0.0")

    assert result == session_ingest.IngestResult(ingested=0, skipped=0, errors=0)
    external_calls.dedup.assert_not_awaited()
    rows = external_calls.insert.await_args.args[0]
    assert len(rows) == 1
    expected_metadata = {
        "event_type": "kiro_credits",
        "credits": 1.25,
        "is_source_record": 0,
        "rendered": 1,
        "agent_id": "agent",
        "agent_version": "1.0.0",
    }
    assert {key: rows[0][key] for key in expected_metadata} == expected_metadata
    external_calls.refresh.assert_awaited_once_with(SESSION_ID, PROJECT_ID, USER_ID, "kiro")


@pytest.mark.parametrize("lines", [None, []])
async def test_empty_input_without_metadata_does_not_write(external_calls, lines):
    assert await _ingest(lines) == session_ingest.IngestResult(ingested=0, skipped=0, errors=0)
    external_calls.dedup.assert_not_awaited()
    external_calls.insert.assert_not_awaited()
    external_calls.refresh.assert_not_awaited()


async def test_source_and_synthetic_rows_use_separate_clickhouse_writes(external_calls):
    await _ingest([KIRO_TOOL_CALL], harness="kiro", total_credits=2.5)

    assert external_calls.insert.await_count == 2
    source_rows = external_calls.insert.await_args_list[0].args[0]
    extra_rows = external_calls.insert.await_args_list[1].args[0]
    assert source_rows[0]["is_source_record"] == 1
    assert source_rows[0]["event_type"] == "tool_call"
    assert extra_rows[0]["is_source_record"] == 0
    assert extra_rows[0]["event_type"] == "kiro_credits"
    external_calls.refresh.assert_awaited_once()


async def test_exact_retry_is_skipped_by_source_position(external_calls):
    line = CLAUDE_USER_PROMPT
    digest = xxhash.xxh128(line.encode()).hexdigest()
    external_calls.dedup.return_value = {7: digest}
    external_calls.checkpoint.return_value = (7, 70)

    result = await _ingest([line], start_offset=7)

    assert result == session_ingest.IngestResult(ingested=0, skipped=1, errors=0)
    external_calls.insert.assert_not_awaited()
    external_calls.refresh.assert_not_awaited()


async def test_conflicting_retry_at_checkpoint_fails_before_any_write(external_calls):
    external_calls.dedup.return_value = {7: "old-hash", 8: "another-old-hash"}
    external_calls.checkpoint.return_value = (8, 80)

    with pytest.raises(session_ingest.SessionRecordConflictError) as error:
        await _ingest([CLAUDE_USER_PROMPT, CLAUDE_ASSISTANT_TEXT], start_offset=7)

    assert error.value.offsets == [7, 8]
    assert str(error.value) == "session source content changed at line(s): 7, 8"
    external_calls.insert.assert_not_awaited()
    external_calls.refresh.assert_not_awaited()


async def test_retry_after_checkpoint_rewrites_the_unacknowledged_range(external_calls):
    first_hash = xxhash.xxh128(CLAUDE_USER_PROMPT.encode()).hexdigest()
    external_calls.dedup.return_value = {7: first_hash, 8: "stale-hash"}
    external_calls.checkpoint.return_value = (6, 60)

    result = await _ingest([CLAUDE_USER_PROMPT, CLAUDE_ASSISTANT_TEXT], start_offset=7)

    assert result == session_ingest.IngestResult(ingested=2, skipped=0, errors=0)
    assert [row["line_offset"] for row in external_calls.insert.await_args.args[0]] == [7, 8]
    external_calls.refresh.assert_awaited_once()


async def test_invalid_byte_offsets_and_unknown_harness_fail_without_writes(external_calls):
    with pytest.raises(ValueError, match="one value per source line"):
        await _ingest([CLAUDE_USER_PROMPT], end_byte_offsets=[])

    external_calls.dedup.assert_not_awaited()
    with pytest.raises(KeyError):
        await _ingest([CLAUDE_USER_PROMPT], harness="unregistered")

    external_calls.insert.assert_not_awaited()
    external_calls.refresh.assert_not_awaited()


@pytest.mark.parametrize("failing_call", ["dedup", "insert", "refresh"])
async def test_clickhouse_failures_propagate_and_stop_later_writes(external_calls, failing_call: str):
    getattr(external_calls, failing_call).side_effect = RuntimeError(f"{failing_call} unavailable")

    with pytest.raises(RuntimeError, match=f"{failing_call} unavailable"):
        await _ingest([CLAUDE_USER_PROMPT])

    if failing_call == "dedup":
        external_calls.insert.assert_not_awaited()
        external_calls.refresh.assert_not_awaited()
    elif failing_call == "insert":
        external_calls.refresh.assert_not_awaited()
    else:
        external_calls.insert.assert_awaited_once()


@pytest.mark.parametrize(
    ("harness", "parsed", "expected"),
    [
        (
            "claude-code",
            json.loads(CLAUDE_ASSISTANT_TEXT),
            {
                "input_tokens": 10,
                "output_tokens": 20,
                "cache_read_tokens": 5,
                "cache_write_tokens": 2,
                "model": "claude-3-5-sonnet-20241022",
            },
        ),
        (
            "pi",
            json.loads(PI_ASSISTANT_TEXT),
            {
                "input_tokens": 3,
                "output_tokens": 4,
                "cache_read_tokens": 100,
                "cache_write_tokens": 200,
                "model": "eu.anthropic.claude-opus-4-6-v1",
            },
        ),
        (
            "codex",
            json.loads(CODEX_USAGE),
            {
                "input_tokens": 16,
                "output_tokens": 13,
                "cache_read_tokens": 5,
                "cache_write_tokens": 0,
                "model": "",
            },
        ),
        (
            "copilot-cli",
            {"data": {"inputTokens": 7, "outputTokens": 2, "cacheReadTokens": 1, "model": "flat"}},
            {
                "input_tokens": 6,
                "output_tokens": 2,
                "cache_read_tokens": 1,
                "cache_write_tokens": 0,
                "model": "flat",
            },
        ),
        (
            "copilot",
            json.loads(COPILOT_USAGE),
            {
                "input_tokens": 34,
                "output_tokens": 18,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "model": "gpt-test",
            },
        ),
        (
            "goose",
            json.loads(GOOSE_TOOL_CALL),
            {
                "input_tokens": 12,
                "output_tokens": 4,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "model": "goose-model",
            },
        ),
        (
            "antigravity",
            {},
            {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "model": "",
            },
        ),
        (
            "future-claude-compatible",
            json.loads(CLAUDE_ASSISTANT_TEXT),
            {
                "input_tokens": 10,
                "output_tokens": 20,
                "cache_read_tokens": 5,
                "cache_write_tokens": 2,
                "model": "claude-3-5-sonnet-20241022",
            },
        ),
    ],
)
def test_usage_extraction_is_harness_specific(harness: str, parsed: dict, expected: dict):
    assert session_ingest._extract_usage_tokens(parsed, harness) == expected


@pytest.mark.parametrize(
    ("parsed", "expected"),
    [
        ({"payload": "invalid"}, (0, 0, 0, 0, "")),
        ({"payload": {"info": "invalid"}}, (0, 0, 0, 0, "")),
        (
            {"payload": {"info": {"total_token_usage": {"input_tokens": 9, "output_tokens": 6}}}},
            (9, 6, 0, 0, ""),
        ),
        ({"payload": {"info": {"last_token_usage": ["not a mapping"]}}}, (0, 0, 0, 0, "")),
        (
            {
                "payload": {
                    "info": {
                        "last_token_usage": "invalid",
                        "total_token_usage": {"input_tokens": 12, "cached_input_tokens": 4},
                    }
                }
            },
            (8, 0, 4, 0, ""),
        ),
        (
            {
                "payload": {
                    "info": {
                        "last_token_usage": {
                            "input_tokens": 42,
                            "cached_input_tokens": -2,
                            "output_tokens": float("inf"),
                        }
                    }
                }
            },
            (42, 0, 0, 0, ""),
        ),
    ],
)
def test_codex_usage_tolerates_payload_variants(parsed: dict, expected: tuple):
    usage = session_ingest._extract_usage_tokens(parsed, "codex")
    assert tuple(usage.values()) == expected


@pytest.mark.parametrize("metadata", [[], {"usage": "invalid"}, {"usage": {"inputTokens": "many"}}, {"inference": []}])
def test_goose_usage_tolerates_malformed_metadata(metadata):
    parsed = goose_message("assistant", [], metadata=metadata)
    assert session_ingest._extract_usage_tokens(parsed, "goose") == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "model": "",
    }


def test_copilot_usage_without_token_fields_is_zeroed():
    assert session_ingest._extract_usage_tokens({"data": {"model": "ignored"}}, "copilot-cli") == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "model": "",
    }


@pytest.mark.parametrize(
    ("harness", "parsed", "expected"),
    [
        (
            "copilot-cli",
            {
                "data": {
                    "inputTokens": "42",
                    "cacheReadTokens": "many",
                    "cacheWriteTokens": -3,
                    "outputTokens": float("inf"),
                }
            },
            (42, 0, 0, 0),
        ),
        (
            "copilot",
            {"event": {"data": {"inputTokens": "many", "cacheReadTokens": 7, "outputTokens": 2}}},
            (0, 2, 7, 0),
        ),
    ],
)
def test_copilot_usage_tolerates_malformed_counts(harness: str, parsed: dict, expected: tuple[int, ...]):
    usage = session_ingest._extract_usage_tokens(parsed, harness)
    assert (
        tuple(usage[key] for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"))
        == expected
    )


@pytest.mark.parametrize(
    ("harness", "parsed", "expected"),
    [
        (
            "codex",
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {
                            "input_tokens": 12017,
                            "cached_input_tokens": 11520,
                            "output_tokens": 55,
                            "reasoning_output_tokens": 0,
                            "total_tokens": 12072,
                        }
                    },
                },
            },
            (497, 55, 11520, 0),
        ),
        # A cache count above the input total never produces negative input.
        (
            "codex",
            {"payload": {"info": {"last_token_usage": {"input_tokens": 5, "cached_input_tokens": 9}}}},
            (0, 0, 9, 0),
        ),
        (
            "copilot-cli",
            {"data": {"inputTokens": 45102, "outputTokens": 2101, "cacheReadTokens": 32512, "cacheWriteTokens": 0}},
            (12590, 2101, 32512, 0),
        ),
        (
            "copilot",
            {
                "event": {
                    "type": "assistant.usage",
                    "data": {"inputTokens": 900, "outputTokens": 10, "cacheReadTokens": 600, "cacheWriteTokens": 200},
                }
            },
            (100, 10, 600, 200),
        ),
        (
            "goose",
            goose_message(
                "assistant",
                [],
                metadata={
                    "usage": {
                        "inputTokens": 6010,
                        "outputTokens": 50,
                        "cacheReadTokens": 5000,
                        "cacheWriteTokens": 1000,
                    }
                },
            ),
            (10, 50, 5000, 1000),
        ),
    ],
)
def test_cache_inclusive_input_is_stored_without_cache(harness: str, parsed: dict, expected: tuple):
    """Observal stores input_tokens excluding cache reads and writes for every harness."""
    usage = session_ingest._extract_usage_tokens(parsed, harness)
    assert (
        usage["input_tokens"],
        usage["output_tokens"],
        usage["cache_read_tokens"],
        usage["cache_write_tokens"],
    ) == expected


@pytest.mark.parametrize(
    ("harness", "parsed", "expected"),
    [
        ("claude-code", {"uuid": "child", "parentUuid": "parent"}, ("child", "parent")),
        ("pi", json.loads(PI_USER_PROMPT), ("e67e72b6", "8bc16d27")),
        ("antigravity", {"step_index": 0}, ("0", None)),
        ("antigravity", {}, (None, None)),
        (
            "goose",
            {"message_id": "message", "session_id": "session", "parent_session_id": "parent"},
            ("message", "parent"),
        ),
        ("goose", {"session_id": "session"}, ("session", None)),
        ("future-claude-compatible", {"uuid": "fallback", "parentUuid": "root"}, ("fallback", "root")),
    ],
)
def test_uuid_extraction_preserves_event_relationships(harness: str, parsed: dict, expected: tuple):
    assert session_ingest._extract_uuid(parsed, harness) == expected


@pytest.mark.parametrize("value", [None, "", "9b05668a-d10f-47e4-96b2-6afe46a15fc1"])
async def test_agent_id_fast_paths_do_not_use_redis(monkeypatch, value):
    def fail_redis():
        raise AssertionError("redis should not be used")

    monkeypatch.setattr("services.redis.get_redis", fail_redis)
    assert await session_ingest._resolve_agent_id(value) == value


@pytest.mark.parametrize(
    ("cached", "expected"),
    [
        ("9b05668a-d10f-47e4-96b2-6afe46a15fc1", "9b05668a-d10f-47e4-96b2-6afe46a15fc1"),
        ("__none__", "friendly-agent"),
    ],
)
async def test_agent_id_uses_positive_and_negative_cache_entries(monkeypatch, cached: str, expected: str):
    redis = SimpleNamespace(get=AsyncMock(return_value=cached), setex=AsyncMock())
    monkeypatch.setattr("services.redis.get_redis", lambda: redis)

    assert await session_ingest._resolve_agent_id("friendly-agent") == expected
    redis.get.assert_awaited_once_with("agent_name_resolve:friendly-agent")
    redis.setex.assert_not_awaited()


class _FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return SimpleNamespace(all=lambda: [] if self.value is None else [self.value])


class _FakeSession:
    def __init__(self, value=None, error: Exception | None = None):
        self.execute = AsyncMock(return_value=_FakeResult(value), side_effect=error)

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False


@pytest.mark.parametrize(
    ("database_value", "expected", "cached"),
    [
        (uuid.UUID("142d7672-cfbe-4a6d-831e-326e6332d76c"), "142d7672-cfbe-4a6d-831e-326e6332d76c", None),
        (None, "friendly-agent", "__none__"),
    ],
)
async def test_agent_id_resolves_database_name_and_caches_outcome(
    monkeypatch, database_value, expected: str, cached: str | None
):
    redis = SimpleNamespace(get=AsyncMock(return_value=None), setex=AsyncMock())
    database = _FakeSession(database_value)
    monkeypatch.setattr("services.redis.get_redis", lambda: redis)
    monkeypatch.setattr("database.async_session", lambda: database)

    assert await session_ingest._resolve_agent_id("friendly-agent") == expected
    database.execute.assert_awaited_once()
    redis.setex.assert_awaited_once_with(
        "agent_name_resolve:friendly-agent",
        300,
        cached or expected,
    )


async def test_agent_id_fails_open_when_cache_and_database_are_unavailable(monkeypatch):
    redis = SimpleNamespace(
        get=AsyncMock(side_effect=RuntimeError("cache read failed")),
        setex=AsyncMock(side_effect=RuntimeError("cache write failed")),
    )
    monkeypatch.setattr("services.redis.get_redis", lambda: redis)
    monkeypatch.setattr("database.async_session", lambda: _FakeSession(error=RuntimeError("database failed")))

    assert await session_ingest._resolve_agent_id("friendly-agent") == "friendly-agent"
    redis.setex.assert_awaited_once_with("agent_name_resolve:friendly-agent", 300, "__none__")


@pytest.mark.parametrize(
    ("agent_id", "version"),
    [
        (None, "latest"),
        ("friendly-agent", "latest"),
        ("9b05668a-d10f-47e4-96b2-6afe46a15fc1", "1.2.3"),
    ],
)
async def test_agent_version_fast_paths_do_not_use_redis(monkeypatch, agent_id, version):
    def fail_redis():
        raise AssertionError("redis should not be used")

    monkeypatch.setattr("services.redis.get_redis", fail_redis)
    assert await session_ingest._resolve_agent_version(agent_id, version) == version


@pytest.mark.parametrize(("cached", "expected"), [("2.0.0", "2.0.0"), ("__none__", "latest")])
async def test_agent_version_uses_positive_and_negative_cache_entries(monkeypatch, cached: str, expected: str):
    agent_id = "9b05668a-d10f-47e4-96b2-6afe46a15fc1"
    redis = SimpleNamespace(get=AsyncMock(return_value=cached), setex=AsyncMock())
    monkeypatch.setattr("services.redis.get_redis", lambda: redis)

    assert await session_ingest._resolve_agent_version(agent_id, "latest") == expected
    redis.get.assert_awaited_once_with(f"agent_version_resolve:{agent_id}:latest")
    redis.setex.assert_not_awaited()


@pytest.mark.parametrize(
    ("database_value", "expected", "cached"), [("3.1.4", "3.1.4", "3.1.4"), (None, "latest", "__none__")]
)
async def test_agent_version_resolves_latest_and_caches_outcome(
    monkeypatch, database_value, expected: str, cached: str
):
    agent_id = "9b05668a-d10f-47e4-96b2-6afe46a15fc1"
    redis = SimpleNamespace(get=AsyncMock(return_value=None), setex=AsyncMock())
    database = _FakeSession(database_value)
    monkeypatch.setattr("services.redis.get_redis", lambda: redis)
    monkeypatch.setattr("database.async_session", lambda: database)

    assert await session_ingest._resolve_agent_version(agent_id, "latest") == expected
    database.execute.assert_awaited_once()
    redis.setex.assert_awaited_once_with(f"agent_version_resolve:{agent_id}:latest", 300, cached)


async def test_agent_version_fails_open_when_cache_and_database_are_unavailable(monkeypatch):
    agent_id = "9b05668a-d10f-47e4-96b2-6afe46a15fc1"
    redis = SimpleNamespace(
        get=AsyncMock(side_effect=RuntimeError("cache read failed")),
        setex=AsyncMock(side_effect=RuntimeError("cache write failed")),
    )
    monkeypatch.setattr("services.redis.get_redis", lambda: redis)
    monkeypatch.setattr("database.async_session", lambda: _FakeSession(error=RuntimeError("database failed")))

    assert await session_ingest._resolve_agent_version(agent_id, "latest") == "latest"
    redis.setex.assert_awaited_once_with(f"agent_version_resolve:{agent_id}:latest", 300, "__none__")


async def test_checkpoint_advances_across_full_pages_and_ignores_stale_rows(external_calls):
    first_page = [(line, line * 10) for line in range(5_000)]
    second_page = [(4_999, 49_990), (5_000, 50_000)]
    external_calls.records.side_effect = [first_page, second_page]

    checkpoint = await session_ingest.advance_session_checkpoint(SESSION_ID, PROJECT_ID, USER_ID, "claude-code")

    assert checkpoint == (5_000, 50_000)
    assert external_calls.records.await_count == 2
    external_calls.insert_checkpoint.assert_awaited_once_with(
        SESSION_ID,
        PROJECT_ID,
        USER_ID,
        "claude-code",
        5_000,
        50_000,
    )


@pytest.mark.parametrize(
    ("initial", "records", "expected"),
    [
        ((3, 30), [], (3, 30)),
        ((0, 10), [(2, 30)], (0, 10)),
        ((-1, 0), [(0, 10), (2, 30)], (0, 10)),
    ],
)
async def test_checkpoint_stops_without_records_or_at_first_gap(external_calls, initial, records, expected):
    external_calls.checkpoint.return_value = initial
    external_calls.records.return_value = records

    assert await session_ingest.advance_session_checkpoint(SESSION_ID, PROJECT_ID, USER_ID, "pi") == expected
    external_calls.insert_checkpoint.assert_awaited_once_with(
        SESSION_ID,
        PROJECT_ID,
        USER_ID,
        "pi",
        *expected,
    )


async def test_integrity_reads_checkpoint_and_accepts_matching_line_and_offset(external_calls):
    external_calls.checkpoint.return_value = (2, 30)

    result = await session_ingest.check_session_integrity(
        SESSION_ID,
        PROJECT_ID,
        USER_ID,
        "claude-code",
        expected_line_count=3,
        expected_offset=30,
    )

    assert result == session_ingest.IntegrityResult(
        ok=True,
        acknowledged_line=2,
        acknowledged_offset=30,
        expected_line=2,
        expected_offset=30,
    )
    external_calls.manifest.assert_not_awaited()


async def test_integrity_hashes_only_requested_lines_and_accepts_omitted_offset(external_calls):
    hashes = ["source-zero", "source-one"]
    external_calls.manifest.return_value = [(0, 10, hashes[0]), (1, 20, hashes[1]), (2, 30, "not-hashed")]

    result = await session_ingest.check_session_integrity(
        SESSION_ID,
        PROJECT_ID,
        USER_ID,
        "claude-code",
        expected_line_count=3,
        expected_offset=None,
        acknowledged_line=2,
        acknowledged_offset=999,
        expected_hash=_hash_manifest(hashes),
        hashed_line_count=2,
    )

    assert result.ok
    assert result.server_hash == _hash_manifest(hashes)
    assert result.repair_from_line is None
    external_calls.checkpoint.assert_not_awaited()


async def test_integrity_reports_first_missing_line_and_previous_byte_offset(external_calls):
    hashes = ["source-zero", "source-one", "source-two"]
    external_calls.manifest.return_value = [(0, 10, hashes[0]), (2, 30, hashes[2])]

    result = await session_ingest.check_session_integrity(
        SESSION_ID,
        PROJECT_ID,
        USER_ID,
        "claude-code",
        expected_line_count=3,
        expected_offset=30,
        acknowledged_line=2,
        acknowledged_offset=30,
        expected_hash=_hash_manifest(hashes),
    )

    assert not result.ok
    assert result.repair_from_line == 1
    assert result.repair_offset == 10
    assert result.server_hash != _hash_manifest(hashes)


async def test_integrity_reports_a_trailing_missing_line(external_calls):
    hashes = ["source-zero", "source-one"]
    external_calls.manifest.return_value = [(0, 10, hashes[0])]

    result = await session_ingest.check_session_integrity(
        SESSION_ID,
        PROJECT_ID,
        USER_ID,
        "claude-code",
        expected_line_count=2,
        expected_offset=20,
        acknowledged_line=1,
        acknowledged_offset=20,
        expected_hash=_hash_manifest(hashes),
    )

    assert not result.ok
    assert result.repair_from_line == 1
    assert result.repair_offset == 10


async def test_integrity_rewinds_to_zero_for_content_hash_mismatch(external_calls):
    external_calls.manifest.return_value = [(0, 10, "stored")]

    result = await session_ingest.check_session_integrity(
        SESSION_ID,
        PROJECT_ID,
        USER_ID,
        "claude-code",
        expected_line_count=1,
        expected_offset=11,
        acknowledged_line=0,
        acknowledged_offset=10,
        expected_hash=_hash_manifest(["expected"]),
    )

    assert not result.ok
    assert result.repair_from_line == 0
    assert result.repair_offset == 0
    assert result.expected_line == 0
    assert result.expected_offset == 11


async def test_agent_name_resolves_only_when_one_live_agent_has_it(monkeypatch):
    """Two namespaces may share a name; guessing would credit sessions to the wrong team's agent."""
    from datetime import UTC, datetime

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from models.agent import Agent
    from models.base import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    def agent(name, namespace, *, deleted=False):
        return Agent(
            id=uuid.uuid4(),
            name=name,
            namespace=namespace,
            slug=name,
            owner=namespace,
            created_by=uuid.uuid4(),
            deleted_at=datetime.now(UTC) if deleted else None,
        )

    shared_a, shared_b = agent("reviewer", "team-a"), agent("reviewer", "team-b")
    recreated, replaced = agent("linter", "team-a"), agent("linter", "team-a-old", deleted=True)
    unique = agent("planner", "team-a")
    async with sessions() as db:
        db.add_all([shared_a, shared_b, recreated, replaced, unique])
        await db.commit()

    redis = SimpleNamespace(get=AsyncMock(return_value=None), setex=AsyncMock())
    monkeypatch.setattr("services.redis.get_redis", lambda: redis)
    monkeypatch.setattr("database.async_session", sessions)
    try:
        assert await session_ingest._resolve_agent_id("reviewer") == "reviewer"  # ambiguous: left as the name
        assert await session_ingest._resolve_agent_id("linter") == str(recreated.id)  # deleted twin ignored
        assert await session_ingest._resolve_agent_id("planner") == str(unique.id)
    finally:
        await engine.dispose()
