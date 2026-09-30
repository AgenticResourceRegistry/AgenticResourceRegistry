# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for GET /api/v1/sessions/{id}/otlp, the on-demand OpenTelemetry export."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from api.routes import sessions
from services.otel.ids import trace_id_for
from tests.test_sessions_api import AGENT_ID, IDENTITY_SQL_USER, USER_ID, _api_client, _sql, _user

IDENTITY = {"project_id": "project-a", "user_id": str(USER_ID), "harness": "claude-code"}
SUBAGENTS_SQL = (
    "SELECT DISTINCT session_id FROM session_events WHERE parent_session_id = {sid:String} "
    "AND project_id = {pid:String} AND user_id = {uid:String} AND harness = {harness:String} ORDER BY session_id"
)


def _row(session_id: str, offset: int, line: dict, parent_session_id: str | None = None) -> dict:
    return {
        "session_id": session_id,
        "parent_session_id": parent_session_id,
        "user_id": str(USER_ID),
        "agent_id": str(AGENT_ID),
        "agent_version": "1.2.0",
        "line_offset": offset,
        "timestamp": line["timestamp"].replace("T", " ").rstrip("Z"),
        "event_type": "",
        "raw_line": json.dumps(line),
    }


def _stored() -> dict[str, list[dict]]:
    main = [
        {"type": "user", "uuid": "u1", "timestamp": "2026-09-26T10:00:00Z", "message": {"content": "hello"}},
        {
            "type": "assistant",
            "uuid": "a1",
            "timestamp": "2026-09-26T10:00:02Z",
            "message": {
                "id": "msg_1",
                "model": "m1",
                "usage": {"input_tokens": 5, "output_tokens": 2},
                "content": [{"type": "tool_use", "id": "toolu_1", "name": "Agent", "input": {"prompt": "go"}}],
            },
        },
        {
            "type": "user",
            "uuid": "u2",
            "timestamp": "2026-09-26T10:00:09Z",
            "toolUseResult": {"agentId": "agent-1"},
            "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "found it"}]},
        },
    ]
    child = [{"type": "user", "uuid": "c1", "timestamp": "2026-09-26T10:00:03Z", "message": {"content": "sub task"}}]
    return {
        "session": [_row("session", i, line) for i, line in enumerate(main)],
        "agent-1": [_row("agent-1", i, line, "session") for i, line in enumerate(child)],
    }


def _patch(monkeypatch, identity=IDENTITY, subagents=({"session_id": "agent-1"},), stored=None):
    query = AsyncMock(side_effect=[[identity] if identity else [], list(subagents)])
    monkeypatch.setattr(sessions, "_ch_json", query)
    rows_by_session = _stored() if stored is None else stored

    async def rows(key, **_kwargs):
        return rows_by_session.get(key.session_id, [])

    read = AsyncMock(side_effect=rows)
    monkeypatch.setattr("services.clickhouse.query_session_rows", read)
    return query, read


def _spans(body: dict) -> list[dict]:
    return body["resourceSpans"][0]["scopeSpans"][0]["spans"]


def _attrs(span: dict) -> dict:
    return {a["key"]: a["value"] for a in span["attributes"]}


async def test_invisible_session_is_not_found(monkeypatch):
    query, read = _patch(monkeypatch, identity=None)

    async with _api_client(_user()) as client:
        response = await client.get("/api/v1/sessions/session/otlp")

    assert response.status_code == 404
    sql, params = query.await_args_list[0].args
    assert _sql(sql) == _sql(IDENTITY_SQL_USER)
    assert params == {"param_sid": "session", "param_uid": str(USER_ID)}
    read.assert_not_awaited()


async def test_session_without_stored_rows_is_not_found(monkeypatch):
    _patch(monkeypatch, subagents=(), stored={})

    async with _api_client(_user()) as client:
        response = await client.get("/api/v1/sessions/session/otlp")

    assert response.status_code == 404


async def test_harness_without_a_projector_is_unprocessable(monkeypatch):
    _query, read = _patch(monkeypatch, identity={**IDENTITY, "harness": "cursor"})

    async with _api_client(_user()) as client:
        response = await client.get("/api/v1/sessions/session/otlp")

    assert response.status_code == 422
    assert "cursor" in response.json()["detail"]
    read.assert_not_awaited()


async def test_session_and_subagents_export_as_one_closed_trace(monkeypatch):
    query, read = _patch(monkeypatch)

    async with _api_client(_user()) as client:
        response = await client.get("/api/v1/sessions/session/otlp")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    sql, params = query.await_args_list[1].args
    assert _sql(sql) == _sql(SUBAGENTS_SQL)
    assert params == {
        "param_sid": "session",
        "param_pid": "project-a",
        "param_uid": str(USER_ID),
        "param_harness": "claude-code",
    }
    assert sorted(c.args[0].session_id for c in read.await_args_list) == ["agent-1", "session"]
    assert {c.args[0].project_id for c in read.await_args_list} == {"project-a"}

    spans = _spans(response.json())
    by_name = {span["name"]: span for span in spans}
    assert set(by_name) == {
        "invoke_agent claude-code",
        "turn 1",
        "chat m1",
        "execute_tool Agent",
        "invoke_agent subagent",
    }
    # Six spans: the subagent's "turn 1" shares a name with the parent's.
    assert response.headers["x-observal-span-count"] == str(len(spans)) == "6"
    assert {span["traceId"] for span in spans} == {trace_id_for("session")}
    assert by_name["invoke_agent subagent"]["parentSpanId"] == by_name["execute_tool Agent"]["spanId"]
    root = _attrs(by_name["invoke_agent claude-code"])
    assert root["user.id"] == {"stringValue": str(USER_ID)}
    assert root["gen_ai.agent.id"] == {"stringValue": str(AGENT_ID)}
    assert root["observal.agent.version"] == {"stringValue": "1.2.0"}
    # Content stays out unless the caller asks for it.
    keys = {key for span in spans for key in _attrs(span)}
    assert not keys & {"input.value", "output.value", "gen_ai.output.messages", "gen_ai.tool.call.result"}


async def test_content_is_included_on_request(monkeypatch):
    _patch(monkeypatch)

    async with _api_client(_user()) as client:
        response = await client.get("/api/v1/sessions/session/otlp?include_content=true")

    spans = _spans(response.json())
    by_name = {span["name"]: span for span in spans}
    turns = {span["parentSpanId"]: span for span in spans if span["name"] == "turn 1"}
    parent_turn = turns[by_name["invoke_agent claude-code"]["spanId"]]
    subagent_turn = turns[by_name["invoke_agent subagent"]["spanId"]]
    assert _attrs(parent_turn)["input.value"] == {"stringValue": "hello"}
    assert _attrs(subagent_turn)["input.value"] == {"stringValue": "sub task"}
    assert _attrs(by_name["execute_tool Agent"])["gen_ai.tool.call.result"] == {"stringValue": "found it"}


async def test_protobuf_encoding(monkeypatch):
    _patch(monkeypatch)

    async with _api_client(_user()) as client:
        response = await client.get("/api/v1/sessions/session/otlp?encoding=protobuf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-protobuf"
    assert response.headers["x-observal-span-count"] == "6"
    spans = ExportTraceServiceRequest.FromString(response.content).resource_spans[0].scope_spans[0].spans
    assert len(spans) == 6
    assert {span.trace_id for span in spans} == {bytes.fromhex(trace_id_for("session"))}


async def test_subagent_reads_are_bounded(monkeypatch):
    subagents = [{"session_id": f"agent-{i}"} for i in range(12)]
    _query, read = _patch(monkeypatch, subagents=subagents)
    rows_by_session = _stored()
    in_flight = peak = 0

    async def rows(key, **_kwargs):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return rows_by_session.get(key.session_id, [])

    read.side_effect = rows

    async with _api_client(_user()) as client:
        response = await client.get("/api/v1/sessions/session/otlp")

    assert response.status_code == 200
    assert read.await_count == 13
    assert peak == sessions._OTLP_EXPORT_READS


async def test_unknown_encoding_is_rejected_before_any_query(monkeypatch):
    query = AsyncMock()
    monkeypatch.setattr(sessions, "_ch_json", query)

    async with _api_client(_user()) as client:
        response = await client.get("/api/v1/sessions/session/otlp?encoding=grpc")

    assert response.status_code == 422
    query.assert_not_awaited()


@pytest.mark.parametrize("encoding", ["json", "protobuf"])
async def test_export_requires_authentication(monkeypatch, encoding):
    from fastapi import HTTPException

    query = AsyncMock()
    monkeypatch.setattr(sessions, "_ch_json", query)

    async with _api_client(auth_error=HTTPException(status_code=401, detail="Missing credentials")) as client:
        response = await client.get(f"/api/v1/sessions/session/otlp?encoding={encoding}")

    assert response.status_code == 401
    query.assert_not_awaited()
