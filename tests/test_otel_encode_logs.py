# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""encode_logs: one stored row becomes one OTLP log record, as the design's field table says."""

from __future__ import annotations

import json

from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest

from services.otel.encode import LOG_EVENT_NAME, encode_logs, to_bytes, to_json
from services.otel.ids import trace_id_for
from services.secrets_redactor import redact_secrets

RAW_PROMPT = json.dumps({"type": "user", "message": {"content": "fix the flaky test"}})


def _row(**overrides) -> dict:
    row = {
        "session_id": "sess-1",
        "project_id": "default",
        "user_id": "user-1",
        "harness": "claude-code",
        "agent_id": "agent-1",
        "agent_version": "1.2.0",
        "parent_session_id": None,
        "line_offset": 0,
        "line_hash": "h0",
        "is_source_record": 1,
        "rendered": 1,
        "event_type": "user_prompt",
        "timestamp": "2026-09-30 10:00:00.123",
        "ingested_at": "2026-09-30 10:00:01.000",
        "uuid": "u-0",
        "parent_uuid": None,
        "tool_name": None,
        "tool_id": None,
        "model": "",
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "credits": 0,
        "raw_line": RAW_PROMPT,
        "raw_line_truncated": 0,
    }
    row.update(overrides)
    return row


def _records(request: ExportLogsServiceRequest) -> list[dict]:
    return to_json(request)["resourceLogs"][0]["scopeLogs"][0]["logRecords"]


def _attrs(record: dict) -> dict:
    values = {}
    for item in record["attributes"]:
        value = item["value"]
        values[item["key"]] = next(iter(value.values()))
    return values


def test_one_record_per_row_with_the_stored_line_as_body():
    rows = [_row(line_offset=i, line_hash=f"h{i}", raw_line=f'{{"n":{i}}}') for i in range(3)]

    records = _records(encode_logs(rows, include_content=True))

    assert len(records) == 3
    assert [record["body"]["stringValue"] for record in records] == [row["raw_line"] for row in rows]


def test_attributes_follow_the_design_field_table():
    request = encode_logs([_row(line_offset=7, line_hash="abc", harness="kiro")], include_content=False)
    attrs = _attrs(_records(request)[0])

    assert attrs["event.name"] == LOG_EVENT_NAME
    assert attrs["session.id"] == "sess-1"
    assert attrs["observal.line_offset"] == "7"  # OTLP/JSON writes int64 as a string
    assert attrs["observal.line_hash"] == "abc"
    assert attrs["observal.event_type"] == "user_prompt"
    assert attrs["observal.uuid"] == "u-0"
    assert attrs["user.id"] == "user-1"
    assert attrs["gen_ai.agent.id"] == "agent-1"
    assert attrs["observal.agent.version"] == "1.2.0"
    # Empty and missing columns are left out rather than sent as empty strings.
    assert "observal.parent_uuid" not in attrs
    assert "gen_ai.tool.name" not in attrs
    resource = to_json(request)["resourceLogs"][0]["resource"]
    assert resource["attributes"] == [{"key": "service.name", "value": {"stringValue": "kiro"}}]


def test_timestamps_are_record_time_and_ingest_time_with_a_fallback():
    record, untimed = _records(encode_logs([_row(), _row(line_offset=1, timestamp="")], include_content=False))

    assert record["timeUnixNano"] == "1790762400123000000"
    assert record["observedTimeUnixNano"] == "1790762401000000000"
    assert untimed["timeUnixNano"] == untimed["observedTimeUnixNano"] == "1790762401000000000"


def test_trace_and_span_ids_join_the_spans_and_survive_protobuf():
    rows = [_row(), _row(session_id="sub-1", parent_session_id="sess-1", line_offset=1)]
    request = encode_logs(rows, include_content=False, span_ids={1: "0123456789abcdef"})
    root, child = _records(request)

    # A subagent's records share the root session's trace.
    assert root["traceId"] == child["traceId"] == trace_id_for("sess-1")
    assert _attrs(child)["observal.parent_session.id"] == "sess-1"
    assert "spanId" not in root
    assert child["spanId"] == "0123456789abcdef"

    parsed = ExportLogsServiceRequest.FromString(to_bytes(request, protocol="http/protobuf"))
    binary = parsed.resource_logs[0].scope_logs[0].log_records[1]
    assert binary.trace_id.hex() == trace_id_for("sess-1")
    assert binary.span_id.hex() == "0123456789abcdef"


def test_tokens_are_written_as_stored_never_as_gen_ai_usage():
    attrs = _attrs(
        _records(
            encode_logs(
                [_row(input_tokens="12", output_tokens=30, cache_read_tokens=0, model="claude-sonnet-5")],
                include_content=False,
            )
        )[0]
    )

    assert attrs["observal.usage.input_tokens"] == "12"
    assert attrs["observal.usage.output_tokens"] == "30"
    assert "observal.usage.cache_read_tokens" not in attrs
    assert attrs["gen_ai.response.model"] == "claude-sonnet-5"
    assert not any(key.startswith("gen_ai.usage.") for key in attrs)


def test_flags_are_set_only_when_true():
    plain = _attrs(_records(encode_logs([_row()], include_content=False))[0])
    flagged = _attrs(
        _records(encode_logs([_row(is_source_record=0, raw_line_truncated=1)], include_content=False, replay=True))[0]
    )

    flags = ("observal.record.synthetic", "observal.raw_line_truncated", "observal.forward.replay")
    assert not any(flag in plain for flag in flags)
    assert all(flagged[flag] is True for flag in flags)


def test_content_gate_drops_the_body_in_both_encodings():
    request = encode_logs([_row()], include_content=False)

    assert "body" not in _records(request)[0]
    assert b"flaky" not in to_bytes(request, protocol="http/protobuf")
    assert b"flaky" not in to_bytes(request, protocol="http/json")


def test_secrets_redacted_at_ingest_never_reach_a_payload():
    secret = "sk-ant-api03-" + "A" * 95
    stored = _row(raw_line=redact_secrets(json.dumps({"type": "user", "message": {"content": f"key {secret}"}})))

    request = encode_logs([stored], include_content=True)

    assert secret.encode() not in to_bytes(request, protocol="http/protobuf")
    assert secret.encode() not in to_bytes(request, protocol="http/json")
