# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""post_otlp: compression, retries, Retry-After, final failures and partial success."""

from __future__ import annotations

import gzip
import json

import httpx
import pytest
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceResponse

import services.ssrf_guard as ssrf_guard
from services.otel.destinations import Destination
from services.otel.transport import MAX_ATTEMPTS, MAX_RETRY_AFTER_SECONDS, post_otlp

BODY = b"\x0a\x00otlp-request"


def _destination(**overrides) -> Destination:
    values = {"id": "collector", "url": "https://otel.example.com", "headers": {"Authorization": "Bearer t"}}
    values.update(overrides)
    return Destination(**values)


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    monkeypatch.setattr(ssrf_guard, "is_private_url", lambda url: "internal" in url)


class Recorder:
    def __init__(self, *responses: httpx.Response | Exception):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []
        self.attempts: list = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    async def on_attempt(self, attempt) -> None:
        self.attempts.append(attempt)

    async def post(self, destination: Destination | None = None, signal: str = "logs"):
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.handler)) as client:
            return await post_otlp(
                client,
                destination or _destination(),
                signal,
                BODY,
                sleep=self.sleep,
                on_attempt=self.on_attempt,
            )


@pytest.mark.parametrize(
    ("protocol", "content_type"),
    [("http/protobuf", "application/x-protobuf"), ("http/json", "application/json")],
)
async def test_posts_gzipped_to_the_signal_path_with_destination_headers(protocol, content_type):
    recorder = Recorder(httpx.Response(200))
    # Destination headers are sent, but cannot override the encoding headers.
    headers = {"Authorization": "Bearer t", "Content-Type": "text/plain", "Content-Encoding": "identity"}

    result = await recorder.post(_destination(protocol=protocol, headers=headers))

    (request,) = recorder.requests
    assert str(request.url) == "https://otel.example.com/v1/logs"
    assert request.headers["content-type"] == content_type
    assert request.headers["content-encoding"] == "gzip"
    assert request.headers["authorization"] == "Bearer t"
    assert gzip.decompress(request.content) == BODY
    assert result.delivered and not result.final and result.attempts == 1
    assert [a.status for a in recorder.attempts] == ["delivered"]


@pytest.mark.parametrize("status", [500, 408, 429])
async def test_retryable_statuses_are_retried_with_backoff(status):
    recorder = Recorder(httpx.Response(status), httpx.Response(status), httpx.Response(200))

    result = await recorder.post()

    assert result.delivered and result.attempts == 3
    assert recorder.sleeps == [1.0, 2.0]
    assert [a.status for a in recorder.attempts] == ["retry", "retry", "delivered"]


async def test_connection_errors_are_retried():
    recorder = Recorder(httpx.ConnectError("refused"), httpx.Response(200))

    result = await recorder.post()

    assert result.delivered
    assert recorder.attempts[0].error == "ConnectError"
    assert recorder.attempts[0].status_code is None


async def test_gives_up_after_max_attempts_without_marking_it_final():
    recorder = Recorder(*[httpx.Response(503)] * MAX_ATTEMPTS)

    result = await recorder.post()

    assert not result.delivered and not result.final
    assert result.attempts == MAX_ATTEMPTS
    assert recorder.sleeps == [1.0, 2.0, 4.0, 8.0]
    assert recorder.attempts[-1].status == "failed"
    assert result.error == "HTTP 503"


async def test_retry_after_is_honoured_capped_and_ignored_when_unreadable():
    recorder = Recorder(
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(503, headers={"Retry-After": "3600"}),
        httpx.Response(503, headers={"Retry-After": "soon"}),
        httpx.Response(200),
    )

    await recorder.post()

    assert recorder.sleeps == [7.0, MAX_RETRY_AFTER_SECONDS, 4.0]


@pytest.mark.parametrize("status", [400, 401, 301])
async def test_other_statuses_are_final_and_not_retried(status):
    recorder = Recorder(httpx.Response(status, text="echo of the request: Bearer t"))

    result = await recorder.post()

    assert not result.delivered and result.final
    assert len(recorder.requests) == 1
    assert recorder.sleeps == []
    assert result.error == f"HTTP {status}"  # never the response body


async def test_private_destination_is_refused_at_send_time_without_a_request():
    recorder = Recorder()

    result = await recorder.post(_destination(url="https://otel.internal"))

    assert result.final and not result.delivered
    assert recorder.requests == []
    assert recorder.attempts[0].status == "failed"


async def test_partial_success_is_read_from_protobuf_responses():
    response = ExportLogsServiceResponse()
    response.partial_success.rejected_log_records = 4
    recorder = Recorder(
        httpx.Response(200, content=response.SerializeToString(), headers={"Content-Type": "application/x-protobuf"})
    )

    result = await recorder.post()

    assert result.delivered and result.rejected == 4
    assert recorder.attempts[0].rejected == 4


async def test_partial_success_is_read_from_json_responses():
    recorder = Recorder(
        httpx.Response(
            200,
            content=json.dumps({"partialSuccess": {"rejectedSpans": "2"}}).encode(),
            headers={"Content-Type": "application/json"},
        )
    )

    result = await recorder.post(signal="traces")

    assert result.rejected == 2


async def test_unreadable_success_body_counts_as_nothing_rejected():
    recorder = Recorder(httpx.Response(200, content=b"\xff\xfe not protobuf"))

    result = await recorder.post()

    assert result.delivered and result.rejected == 0
