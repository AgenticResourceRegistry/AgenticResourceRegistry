# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the shared OTLP package: IDs, attributes and span encoding."""

from __future__ import annotations

import json

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from services.otel import projectors
from services.otel.attributes import MAX_CONTENT_CHARS, messages, pairs, semconv_input_tokens
from services.otel.encode import STATUS_CODE_ERROR, encode_spans, to_bytes, traces_json
from services.otel.ids import span_id, to_unix_nanos, trace_id_for
from services.otel.types import SPAN_KIND_CLIENT, SPAN_KIND_INTERNAL, Span, SpanEvent

SECOND = 1_000_000_000
T0 = to_unix_nanos("2026-09-26 10:00:00.000")
TRACE = trace_id_for("sess-1")
ROOT = span_id("sess-1", "session")


def _spans() -> list[Span]:
    root = Span(
        trace_id=TRACE,
        span_id=ROOT,
        parent_span_id=None,
        name="invoke_agent claude-code",
        kind=SPAN_KIND_INTERNAL,
        start_ns=T0,
        end_ns=T0 + 10 * SECOND,
        attributes=pairs({"gen_ai.operation.name": "invoke_agent", "session.id": "sess-1"}),
        content=(("input.value", "List the files"),),
        events=(
            SpanEvent(
                name="compaction",
                time_ns=T0 + SECOND,
                attributes=(("observal.event.type", "system"),),
                content=(("observal.event.body", "context compacted"),),
            ),
        ),
    )
    chat = Span(
        trace_id=TRACE,
        span_id=span_id("sess-1", "msg_1"),
        parent_span_id=ROOT,
        name="chat claude-sonnet-5",
        kind=SPAN_KIND_CLIENT,
        start_ns=T0 + SECOND,
        end_ns=T0 + 2 * SECOND,
        attributes=pairs(
            {
                "gen_ai.operation.name": "chat",
                "gen_ai.response.model": "claude-sonnet-5",
                "gen_ai.usage.input_tokens": 150,
                "gen_ai.response.finish_reasons": ["tool_use"],
            }
        ),
        content=(("gen_ai.output.messages", messages("assistant", [{"type": "text", "content": "Listing."}])),),
        record_offsets=(1,),
    )
    tool = Span(
        trace_id=TRACE,
        span_id=span_id("sess-1", "toolu_1"),
        parent_span_id=chat.span_id,
        name="execute_tool Bash",
        kind=SPAN_KIND_INTERNAL,
        start_ns=T0 + 2 * SECOND,
        end_ns=T0 + 5 * SECOND,
        attributes=pairs({"gen_ai.tool.name": "Bash", "gen_ai.tool.call.id": "toolu_1"}),
        content=(("gen_ai.tool.call.arguments", '{"command": "ls"}'), ("gen_ai.tool.call.result", "denied")),
        error=True,
        record_offsets=(1, 2),
    )
    return [root, chat, tool]


def _attrs(items) -> dict:
    return {item.key: item.value for item in items}


# ---------------------------------------------------------------------------
# IDs and timestamps
# ---------------------------------------------------------------------------


def test_ids_are_deterministic_hex_of_the_otlp_sizes():
    assert trace_id_for("sess-1") == TRACE
    assert len(TRACE) == 32 and int(TRACE, 16) >= 0
    assert span_id("sess-1", "session") == ROOT
    assert len(ROOT) == 16
    assert span_id("sess-1", "a") != span_id("sess-2", "a") != span_id("sess-1", "b")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-09-26 10:00:00.000", T0),
        ("2026-09-26T10:00:00Z", T0),
        ("2026-09-26T10:00:00.000+00:00", T0),
        ("2026-09-26T15:30:00.000+05:30", T0),
        ("2026-09-26T10:00:00.123456789Z", T0 + 123_456_000),
        (T0 // 1_000_000, T0),
        (str(T0 // 1_000_000_000), T0),
        ("1970-01-01 00:00:00.000", None),
        ("not a time", None),
        ("", None),
        (None, None),
        (0, None),
    ],
)
def test_to_unix_nanos(value, expected):
    assert to_unix_nanos(value) == expected


# ---------------------------------------------------------------------------
# Attributes
# ---------------------------------------------------------------------------


def test_pairs_drop_empty_values_and_freeze_lists():
    assert pairs({"a": None, "b": "", "c": 0, "d": ["x"], "e": False}) == (("c", 0), ("d", ("x",)), ("e", False))


def test_messages_clip_each_part_and_stay_valid_json():
    long = "x" * (MAX_CONTENT_CHARS + 10)
    [message] = json.loads(messages("user", [{"type": "text", "content": long}], "stop"))
    assert message["role"] == "user"
    assert message["finish_reason"] == "stop"
    assert message["parts"][0]["content"].endswith("...[truncated]")
    assert len(message["parts"][0]["content"]) == MAX_CONTENT_CHARS + len("...[truncated]")
    assert messages("user", []) is None


def test_semconv_input_tokens_add_cache_back():
    assert semconv_input_tokens({"input_tokens": 100, "cache_read_tokens": 50, "cache_write_tokens": 7}) == 157
    assert semconv_input_tokens({"input_tokens": 3, "cache_creation_tokens": 4, "cache_write_tokens": 9}) == 7


# ---------------------------------------------------------------------------
# Span model
# ---------------------------------------------------------------------------


def test_the_same_span_projected_twice_compares_equal_and_hashes():
    first, second = _spans(), _spans()
    assert first == second
    assert {hash(span) for span in first} == {hash(span) for span in second}


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------


def test_protobuf_and_json_carry_the_same_ids_names_and_times():
    spans = _spans()
    request = encode_spans(spans, service_name="claude-code", include_content=True)

    decoded = ExportTraceServiceRequest.FromString(to_bytes(request, protocol="http/protobuf"))
    as_json = json.loads(to_bytes(request, protocol="http/json"))

    [proto_resource] = decoded.resource_spans
    [proto_scope] = proto_resource.scope_spans
    [json_resource] = as_json["resourceSpans"]
    [json_scope] = json_resource["scopeSpans"]
    assert _attrs(proto_resource.resource.attributes)["service.name"].string_value == "claude-code"
    assert len(proto_scope.spans) == len(json_scope["spans"]) == len(spans)
    for span, proto_span, json_span in zip(spans, proto_scope.spans, json_scope["spans"], strict=True):
        assert proto_span.trace_id.hex() == json_span["traceId"] == span.trace_id
        assert proto_span.span_id.hex() == json_span["spanId"] == span.span_id
        assert proto_span.parent_span_id.hex() == json_span.get("parentSpanId", "") == (span.parent_span_id or "")
        assert proto_span.name == json_span["name"] == span.name
        assert proto_span.kind == json_span["kind"] == span.kind
        assert proto_span.start_time_unix_nano == int(json_span["startTimeUnixNano"]) == span.start_ns
        assert proto_span.end_time_unix_nano == int(json_span["endTimeUnixNano"]) == span.end_ns


def test_json_bytes_match_the_otlp_json_shape_built_directly():
    spans = _spans()
    request = encode_spans(spans, service_name="claude-code", include_content=True)
    direct = traces_json(spans, service_name="claude-code", include_content=True)

    [span] = [
        s
        for s in json.loads(to_bytes(request, protocol="http/json"))["resourceSpans"][0]["scopeSpans"][0]["spans"]
        if s["name"] == "execute_tool Bash"
    ]
    [expected] = [s for s in direct["resourceSpans"][0]["scopeSpans"][0]["spans"] if s["name"] == "execute_tool Bash"]
    assert span["status"] == {"code": STATUS_CODE_ERROR} == expected["status"]
    assert span["attributes"] == expected["attributes"]


def test_attribute_values_keep_their_types():
    request = encode_spans(_spans(), service_name="claude-code", include_content=False)
    chat = next(s for s in request.resource_spans[0].scope_spans[0].spans if s.name.startswith("chat"))
    attrs = _attrs(chat.attributes)
    assert attrs["gen_ai.usage.input_tokens"].int_value == 150
    assert [v.string_value for v in attrs["gen_ai.response.finish_reasons"].array_value.values] == ["tool_use"]


@pytest.mark.parametrize("protocol", ["http/protobuf", "http/json"])
def test_content_is_left_out_unless_requested(protocol):
    content_keys = {
        "input.value",
        "gen_ai.output.messages",
        "gen_ai.tool.call.arguments",
        "gen_ai.tool.call.result",
        "observal.event.body",
    }

    def keys(include_content: bool) -> set[str]:
        request = encode_spans(_spans(), service_name="claude-code", include_content=include_content)
        found: set[str] = set()
        for span in (
            ExportTraceServiceRequest.FromString(to_bytes(request, protocol="http/protobuf"))
            .resource_spans[0]
            .scope_spans[0]
            .spans
        ):
            found |= {a.key for a in span.attributes}
            for event in span.events:
                found |= {a.key for a in event.attributes}
        payload = to_bytes(request, protocol=protocol)
        assert (b"List the files" in payload) is include_content
        assert (b"context compacted" in payload) is include_content
        return found

    assert content_keys <= keys(True)
    without = keys(False)
    assert not content_keys & without
    assert {"gen_ai.tool.name", "gen_ai.usage.input_tokens", "observal.event.type"} <= without


def test_end_is_never_before_start():
    [span] = _spans()[:1]
    backwards = Span(**{**span.__dict__, "end_ns": span.start_ns - SECOND})
    [encoded] = traces_json([backwards], service_name="x", include_content=False)["resourceSpans"][0]["scopeSpans"][0][
        "spans"
    ]
    assert encoded["endTimeUnixNano"] == encoded["startTimeUnixNano"]


def test_resource_attributes_are_added_after_service_name():
    request = encode_spans(
        [], service_name="antigravity", include_content=False, resource_attributes={"observal.structure": "sequence"}
    )
    attrs = _attrs(request.resource_spans[0].resource.attributes)
    assert attrs["service.name"].string_value == "antigravity"
    assert attrs["observal.structure"].string_value == "sequence"


def test_unknown_protocol_is_rejected():
    request = encode_spans(_spans(), service_name="claude-code", include_content=False)
    with pytest.raises(ValueError, match="grpc"):
        to_bytes(request, protocol="grpc")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Projector dispatch
# ---------------------------------------------------------------------------


def test_harness_without_projector_or_unknown_harness_gets_none():
    assert projectors.get_projector("cursor") is None
    assert projectors.get_projector("no-such-harness") is None


def test_projector_dispatch_uses_the_session_parser_key(monkeypatch):
    sentinel = object()
    monkeypatch.setitem(projectors._PROJECTORS, "copilot-cli", sentinel)
    # Copilot shares the Copilot CLI session parser, so it shares the projector.
    assert projectors.get_projector("copilot") is sentinel
    assert projectors.get_projector("copilot-cli") is sentinel
