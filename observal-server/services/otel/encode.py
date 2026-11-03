# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Encode projected spans as OTLP requests.

Spans are first written in the OTLP/JSON shape and then parsed into the
protobuf message, the route that is tested against the OpenTelemetry
Collector.  ``to_bytes`` serializes a request for either OTLP/HTTP encoding:
binary protobuf (the default) or OTLP/JSON.

Content (``Span.content`` and ``SpanEvent.content``) is only written when
``include_content`` is set; structure, timing, models, token usage and tool
names are always exported.
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Literal

from services.otel.attributes import to_attributes

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from google.protobuf.message import Message
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    from services.otel.types import Span, SpanEvent

SCOPE_NAME = "observal"
STATUS_CODE_ERROR = 2

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
