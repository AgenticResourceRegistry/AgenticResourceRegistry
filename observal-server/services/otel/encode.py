# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Encode projected spans and stored rows as OTLP requests.

Spans and log records are first written in the OTLP/JSON shape and then
parsed into the protobuf message, the route that is tested against the
OpenTelemetry Collector.  ``to_bytes`` serializes a request for either
OTLP/HTTP encoding: binary protobuf (the default) or OTLP/JSON.

Content (``Span.content``, ``SpanEvent.content`` and a log record's body, the
stored line) is only written when ``include_content`` is set; structure,
timing, models, token usage and tool names are always exported.
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Literal

from services.otel.attributes import to_attributes, to_int
from services.otel.ids import to_unix_nanos, trace_id_for

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from google.protobuf.message import Message
    from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    from services.otel.types import Row, Span, SpanEvent

SCOPE_NAME = "observal"
STATUS_CODE_ERROR = 2
SEVERITY_NUMBER_INFO = 9
LOG_EVENT_NAME = "observal.session.record"

# Stored per-row token columns, written as stored under observal.* names.  A
# harness can repeat one response's usage on every line of it (Claude Code
# does), so these must not be summed as gen_ai.usage.*; spans carry
# gen_ai.usage.* once per response.
_ROW_TOKEN_KEYS = {
    "input_tokens": "observal.usage.input_tokens",
    "output_tokens": "observal.usage.output_tokens",
    "cache_read_tokens": "observal.usage.cache_read_tokens",
    "cache_write_tokens": "observal.usage.cache_write_tokens",
}

OtlpProtocol = Literal["http/protobuf", "http/json"]

# OTLP/JSON writes these as hex; the generic protobuf JSON mapping uses base64.
_ID_KEYS = frozenset({"traceId", "spanId", "parentSpanId"})


def _map_ids(value: object, convert: Callable[[str], str]) -> object:
    if isinstance(value, dict):
        return {
            key: convert(item) if key in _ID_KEYS and isinstance(item, str) and item else _map_ids(item, convert)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_map_ids(item, convert) for item in value]
    return value


def _hex_to_base64(value: str) -> str:
    return base64.b64encode(bytes.fromhex(value)).decode()


def _base64_to_hex(value: str) -> str:
    return base64.b64decode(value).hex()


def _event_json(event: SpanEvent, include_content: bool) -> dict:
    values = event.attributes + (event.content if include_content else ())
    return {"timeUnixNano": str(event.time_ns), "name": event.name, "attributes": to_attributes(values)}


def span_json(span: Span, *, include_content: bool) -> dict:
    """One span in the OTLP/JSON shape (hex IDs, string nanoseconds)."""
    values = span.attributes + (span.content if include_content else ())
    item: dict = {
        "traceId": span.trace_id,
        "spanId": span.span_id,
        "name": span.name,
        "kind": span.kind,
        "startTimeUnixNano": str(span.start_ns),
        "endTimeUnixNano": str(max(span.end_ns, span.start_ns)),
        "attributes": to_attributes(values),
    }
    if span.parent_span_id:
        item["parentSpanId"] = span.parent_span_id
    if span.events:
        item["events"] = [_event_json(event, include_content) for event in span.events]
    if span.error:
        item["status"] = {"code": STATUS_CODE_ERROR}
    return item


def traces_json(
    spans: Iterable[Span],
    *,
    service_name: str,
    include_content: bool,
    resource_attributes: Mapping[str, object] | None = None,
) -> dict:
    """An ``ExportTraceServiceRequest`` in the OTLP/JSON shape."""
    resource = {"service.name": service_name or "observal", **(resource_attributes or {})}
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": to_attributes(resource.items())},
                "scopeSpans": [
                    {
                        "scope": {"name": SCOPE_NAME},
                        "spans": [span_json(span, include_content=include_content) for span in spans],
                    }
                ],
            }
        ]
    }


def encode_spans(
    spans: Sequence[Span],
    *,
    service_name: str,
    include_content: bool,
    resource_attributes: Mapping[str, object] | None = None,
) -> ExportTraceServiceRequest:
    """Build the protobuf ``ExportTraceServiceRequest`` for ``spans``."""
    from google.protobuf.json_format import ParseDict
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    request = traces_json(
        spans,
        service_name=service_name,
        include_content=include_content,
        resource_attributes=resource_attributes,
    )
    return ParseDict(_map_ids(request, _hex_to_base64), ExportTraceServiceRequest())


def _flag(value: object) -> bool:
    return str(value).strip().lower() in ("1", "true")


def log_record_json(
    row: Row,
    *,
    include_content: bool,
    span_id: str | None = None,
    replay: bool = False,
) -> dict:
    """One stored row as an OTLP/JSON ``LogRecord``.

    The body is the stored line, unchanged (ingest has already redacted it),
    and only when ``include_content`` is set.  ``observal.line_offset`` and
    ``observal.line_hash`` identify the record, so a destination can drop
    copies that a retry or an integrity replay sends again.
    """
    session_id = str(row.get("session_id") or "")
    parent_session_id = str(row.get("parent_session_id") or "") or None
    time_ns = to_unix_nanos(row.get("timestamp"))
    observed_ns = to_unix_nanos(row.get("ingested_at"))

    attributes: dict[str, object] = {
        "event.name": LOG_EVENT_NAME,
        "session.id": session_id,
        "observal.line_offset": to_int(row.get("line_offset")),
        "observal.line_hash": row.get("line_hash"),
        "observal.event_type": row.get("event_type"),
        "observal.uuid": row.get("uuid"),
        "observal.parent_uuid": row.get("parent_uuid"),
        "observal.parent_session.id": parent_session_id,
        "gen_ai.tool.name": row.get("tool_name"),
        "gen_ai.tool.call.id": row.get("tool_id"),
        "gen_ai.response.model": row.get("model"),
        "user.id": row.get("user_id"),
        "gen_ai.agent.id": row.get("agent_id"),
        "observal.agent.version": row.get("agent_version"),
    }
    for column, key in _ROW_TOKEN_KEYS.items():
        count = to_int(row.get(column))
        if count:
            attributes[key] = count
    if _flag(row.get("raw_line_truncated")):
        attributes["observal.raw_line_truncated"] = True
    if not _flag(row.get("is_source_record", 1)):
        attributes["observal.record.synthetic"] = True
    if replay:
        attributes["observal.forward.replay"] = True

    record: dict = {
        "timeUnixNano": str(time_ns or observed_ns or 0),
        "observedTimeUnixNano": str(observed_ns or time_ns or 0),
        "severityNumber": SEVERITY_NUMBER_INFO,
        "attributes": to_attributes(attributes.items()),
        "traceId": trace_id_for(parent_session_id or session_id),
    }
    if span_id:
        record["spanId"] = span_id
    if include_content:
        record["body"] = {"stringValue": str(row.get("raw_line") or "")}
    return record


def logs_json(
    rows: Iterable[Row],
    *,
    service_name: str,
    include_content: bool,
    span_ids: Mapping[int, str] | None = None,
    replay: bool = False,
) -> dict:
    """An ``ExportLogsServiceRequest`` in the OTLP/JSON shape, one record per row."""
    span_ids = span_ids or {}
    records = [
        log_record_json(
            row,
            include_content=include_content,
            span_id=span_ids.get(to_int(row.get("line_offset"))),
            replay=replay,
        )
        for row in rows
    ]
    return {
        "resourceLogs": [
            {
                "resource": {"attributes": to_attributes([("service.name", service_name or "observal")])},
                "scopeLogs": [{"scope": {"name": SCOPE_NAME}, "logRecords": records}],
            }
        ]
    }


def encode_logs(
    rows: Sequence[Row],
    *,
    include_content: bool,
    span_ids: Mapping[int, str] | None = None,
    replay: bool = False,
) -> ExportLogsServiceRequest:
    """Build the protobuf ``ExportLogsServiceRequest`` for one session's stored rows."""
    from google.protobuf.json_format import ParseDict
    from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest

    service_name = str(rows[0].get("harness") or "") if rows else ""
    request = logs_json(
        rows,
        service_name=service_name,
        include_content=include_content,
        span_ids=span_ids,
        replay=replay,
    )
    return ParseDict(_map_ids(request, _hex_to_base64), ExportLogsServiceRequest())


def to_json(request: Message) -> dict:
    """A protobuf OTLP request in the OTLP/JSON shape (hex IDs, integer enums)."""
    from google.protobuf.json_format import MessageToDict

    return _map_ids(MessageToDict(request, use_integers_for_enums=True), _base64_to_hex)  # type: ignore[return-value]


def to_bytes(request: Message, *, protocol: OtlpProtocol) -> bytes:
    """Serialize an OTLP request for OTLP/HTTP with the given encoding."""
    if protocol == "http/protobuf":
        return request.SerializeToString()
    if protocol == "http/json":
        return json.dumps(to_json(request), ensure_ascii=False, separators=(",", ":")).encode()
    raise ValueError(f"unsupported OTLP protocol: {protocol}")
