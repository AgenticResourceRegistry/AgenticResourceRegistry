# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Observed Kiro models and durable companion metadata must stay distinct."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from observal_cli import telemetry_buffer
from observal_cli.harness import ensure_loaded, get_adapter
from observal_cli.harness.protocol import SessionSource
from observal_cli.sessions.base import drain_outbox, drain_session_source
from services.session_parsers.kiro import extract_usage, parse_rows
from tests.test_session_ingest import external_calls as external_calls


@pytest.mark.parametrize(
    "model,expected",
    [
        ("qdev::auto", "auto"),
        ("qdev::claude-opus-5.5", "claude-opus-5.5"),
        ("other-model", "other-model"),
        (None, ""),
        ([], ""),
        ("  ", ""),
    ],
)
def test_reasoning_model_is_normalized_without_guessing(model, expected):
    """Normalize valid reasoning identifiers while preserving honest auto and unknown values."""
    native = Path(__file__).parent / "fixtures/kiro/v3-messages.jsonl"
    records = [json.loads(raw) for raw in native.read_text().splitlines()]
    line = next(record for record in records if record.get("payload", {}).get("operationType") == "Reasoning")
    line["payload"]["reasoningModelId"] = model
    assert extract_usage(line)["model"] == expected
    assert extract_usage({"payload": {"type": "tool_call", "reasoningModelId": model}})["model"] == ""


def test_reasoning_model_precedes_payload_model_and_v2_stays_supported():
    """Prefer V3 reasoning evidence while retaining older payload and V2 model fields."""
    assert (
        extract_usage({"payload": {"type": "assistant", "reasoningModelId": "qdev::", "modelId": "known"}})["model"]
        == "known"
    )
    assert parse_rows([{"raw_line": None}])[0]["event_name"] == ""
    assert (
        extract_usage({"payload": {"type": "assistant", "reasoningModelId": "qdev::auto", "modelId": "old"}})["model"]
        == "auto"
    )
    assert (
        extract_usage(
            {
                "kind": "AssistantMessage",
                "data": {"content": [{"kind": "toolUse", "data": {"modelId": "qdev::claude-opus-5.5"}}]},
            }
        )["model"]
        == "claude-opus-5.5"
    )


@pytest.mark.parametrize(
    "value,expected",
    [
        ("auto", "auto"),
        ("qdev::claude-opus-5.5", "qdev::claude-opus-5.5"),
        (None, None),
        ({}, None),
        ("", None),
        ("x" * 513, None),
    ],
)
def test_companion_model_only_transports_a_bounded_identifier(tmp_path, value, expected):
    """Transport only a bounded companion model identifier, never unrelated session data."""
    from observal_cli.sessions.kiro import read_kiro_session_model

    path = tmp_path / "messages.jsonl"
    path.write_text("")
    state = tmp_path / "session.json"
    state.write_text(json.dumps({"modelId": value, "secret": "do not upload"}))
    assert read_kiro_session_model(path) == expected
    state.write_text("{invalid")
    assert read_kiro_session_model(path) is None
    state.unlink()
    assert read_kiro_session_model(path) is None


def test_adapter_metadata_survives_source_removal_and_retry(tmp_path, monkeypatch):
    """Deliver queued model evidence after source files disappear and an initial send fails."""
    transcript = tmp_path / "messages.jsonl"
    transcript.write_text('{"payload":{"type":"user","content":"hello"}}\n')
    transcript.with_name("session.json").write_text(json.dumps({"modelId": "auto", "secret": "private"}))
    ensure_loaded()
    adapter = get_adapter("kiro")
    monkeypatch.setattr("observal_cli.sessions.kiro.read_kiro_credits", lambda *_a, **_kw: None)
    source = SessionSource(harness="kiro", session_id="model-test", path=transcript)
    extra = adapter.session_extra_fields(source, {}, False, home=tmp_path)
    assert extra == {"session_model": "auto"}
    config = {"server_url": "http://server", "user_id": "user"}
    db = tmp_path / "outbox.db"
    assert drain_session_source(
        source, config, hook_event="Recovery", extra_fields=extra, spool_only=True, home=tmp_path, db_path=db
    )
    transcript.unlink()
    transcript.with_name("session.json").unlink()
    pending = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)
    assert pending[0].payload["session_model"] == "auto"
    assert "private" not in json.dumps(pending[0].payload)
    posted = []

    def post(payload, _config):
        """Record the retry payload and return a successful delivery acknowledgement."""
        posted.append(payload)
        return {"acknowledged_line": 0, "acknowledged_offset": 55}

    assert drain_outbox(config, home=tmp_path, db_path=db, post=post)
    assert posted[0]["session_model"] == "auto"
    assert not telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)


def test_metadata_only_outbox_does_not_require_credits_or_final(tmp_path):
    """Allow durable model-only batches without requiring credits or finalization."""
    db = tmp_path / "outbox.db"
    payload = {"harness": "kiro", "session_id": "s", "start_offset": 0, "lines": [], "session_model": "auto"}
    telemetry_buffer.enqueue(payload, destination="http://server", user_id="u", db_path=db)
    assert (
        telemetry_buffer.pending(destination="http://server", user_id="u", db_path=db)[0].payload["session_model"]
        == "auto"
    )


@pytest.mark.asyncio
async def test_ingest_fallback_is_not_a_source_record_or_counted_event(external_calls):
    """Keep fallback model rows outside rendered/source counts and preserve parent attribution."""
    from services.session_ingest import ingest_session_lines

    result = await ingest_session_lines(
        session_id="s",
        project_id="p",
        user_id="u",
        agent_id=None,
        agent_version=None,
        harness="kiro",
        lines=[],
        session_model="qdev::auto",
        parent_session_id="parent",
    )
    assert result.ingested == result.errors == result.skipped == 0
    row = external_calls.insert.await_args.args[0][0]
    assert row["model"] == "auto"
    assert row["is_source_record"] == row["rendered"] == 0
    assert row["parent_session_id"] == "parent"
    assert row["line_offset"] == 0xFFFFFFFE
    external_calls.refresh.assert_awaited_once()


def test_trace_credits_prefer_observed_model_to_companion_fallback():
    """Use observed or canonical model evidence before the companion fallback in credit traces."""
    fallback = {"raw_line": json.dumps({"kind": "KiroSessionModel", "data": {"modelId": "auto"}})}
    reasoning = {
        "raw_line": json.dumps(
            {
                "payload": {
                    "type": "assistant",
                    "operationType": "Reasoning",
                    "content": "...",
                    "reasoningModelId": "qdev::claude-opus-5.5",
                }
            }
        )
    }
    credit = {"raw_line": json.dumps({"kind": "KiroCredits", "credits": 1.25}), "credits": 1.25}
    assert parse_rows([reasoning, fallback, credit])[-1]["attributes"]["model"] == "claude-opus-5.5"
    assert parse_rows([fallback, credit])[0]["attributes"]["model"] == "auto"
    assert parse_rows([fallback]) == []
    # A cursor can omit the earlier model-bearing source record.
    fallback["session_model"] = "claude-opus-5.5"
    assert parse_rows([fallback, credit])[0]["attributes"]["model"] == "claude-opus-5.5"


@pytest.mark.asyncio
async def test_summary_uses_metadata_only_when_no_observed_model(monkeypatch):
    """Ensure the canonical summary expression prioritizes observed source models."""
    from services.clickhouse import insert

    query = AsyncMock(return_value=Mock(raise_for_status=Mock()))
    monkeypatch.setattr(insert._client, "_query", query)
    await insert.refresh_session_summary("s", "p", "u", "kiro")
    sql = query.await_args.args[0]
    assert "kiro_session_model" in sql
    assert "countIf(rendered = 1 AND model != '')" in sql


def test_api_bounds_model_metadata_and_catalog_has_observed_model():
    """Bound API model metadata and register the reported explicit model identifier."""
    from pydantic import ValidationError

    from api.routes.ingest import SessionIngestRequest
    from observal_shared.harness_models import supported_model_ids

    assert SessionIngestRequest(session_id="s", lines=[], session_model="auto").session_model == "auto"
    with pytest.raises(ValidationError):
        SessionIngestRequest(session_id="s", lines=[], session_model="x" * 513)
    assert "claude-opus-5.5" in supported_model_ids("kiro")


def test_final_requeue_keeps_already_durable_model_when_companion_is_gone(tmp_path):
    """Retain queued model evidence when a later final payload lacks the companion value."""
    db = tmp_path / "outbox.db"
    first = {"harness": "kiro", "session_id": "s", "start_offset": 0, "lines": ["line"], "session_model": "auto"}
    final = {key: value for key, value in first.items() if key != "session_model"} | {"final": True}
    telemetry_buffer.enqueue(first, destination="http://server", user_id="u", db_path=db)
    telemetry_buffer.enqueue(final, destination="http://server", user_id="u", db_path=db)
    pending = telemetry_buffer.pending(destination="http://server", user_id="u", db_path=db)
    assert pending[0].payload["session_model"] == "auto"
    assert pending[0].payload["final"] is True
