# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""OTLP/HTTP delivery to one destination, with retries.

- The body is gzip-compressed; ``Content-Type`` follows the destination's
  protocol (protobuf or OTLP/JSON).
- 5xx, 408, 429 and connection errors are retried up to 5 times, backing off
  1, 2, 4 and 8 s, or for the server's ``Retry-After`` (capped at 60 s).
- Any other non-2xx status is final: retrying cannot fix a bad URL, a
  rejected credential or a malformed request.  Redirects are not followed, so
  a redirect cannot lead past the SSRF check.
- A 2xx can still report rejected records (OTLP partial success); the count is
  returned so it can be recorded.

Errors are short strings (``HTTP 503``, an exception class name), never the
response body or anything from the request.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from services.otel.destinations import Destination

MAX_ATTEMPTS = 5
_BACKOFF_SECONDS = (1.0, 2.0, 4.0, 8.0)
MAX_RETRY_AFTER_SECONDS = 60.0
REQUEST_TIMEOUT_SECONDS = 10.0

_CONTENT_TYPES = {"http/protobuf": "application/x-protobuf", "http/json": "application/json"}


@dataclass(frozen=True)
class Attempt:
    """One POST, as recorded in ``otlp_forward_deliveries``."""

    attempt: int
    status: str  # delivered | retry | failed
    status_code: int | None
    rejected: int
    payload_bytes: int
    duration_ms: float
    error: str | None


@dataclass(frozen=True)
class SendResult:
    delivered: bool
    final: bool  # failed for a reason retrying will not fix
    status_code: int | None
    rejected: int
    attempts: int
    error: str | None


def _retryable(status_code: int) -> bool:
    return status_code >= 500 or status_code in (408, 429)


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    fallback = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS)) - 1]
    header = response.headers.get("retry-after", "").strip() if response is not None else ""
    if not header:
        return fallback
    try:
        seconds = float(header)
    except ValueError:
        try:
            when = parsedate_to_datetime(header)
        except (TypeError, ValueError):
            return fallback
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - datetime.now(UTC)).total_seconds()
    return min(max(seconds, 0.0), MAX_RETRY_AFTER_SECONDS)


def _rejected_count(response: httpx.Response, signal: str) -> int:
    """Rejected records from an OTLP partial-success response, or 0."""
    if not response.content:
        return 0
    try:
        if "json" in response.headers.get("content-type", ""):
            partial = json.loads(response.content).get("partialSuccess") or {}
            value = partial.get("rejectedLogRecords" if signal == "logs" else "rejectedSpans", 0)
            return int(value or 0)
        if signal == "logs":
            from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceResponse

            return ExportLogsServiceResponse.FromString(response.content).partial_success.rejected_log_records
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse

        return ExportTraceServiceResponse.FromString(response.content).partial_success.rejected_spans
    except Exception:
        return 0


async def post_otlp(
    client: httpx.AsyncClient,
    destination: Destination,
    signal: str,
    body: bytes,
    *,
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
    on_attempt: Callable[[Attempt], Awaitable[None]] | None = None,
) -> SendResult:
    """POST one encoded OTLP request to ``destination``, retrying what can be retried."""
    from services.ssrf_guard import is_private_url

    url = destination.endpoint(signal)
    payload = gzip.compress(body)

    async def record(attempt: Attempt) -> None:
        if on_attempt is not None:
            await on_attempt(attempt)

    # Checked at send time as well as on save: DNS can change after a destination is saved.
    if await asyncio.to_thread(is_private_url, url):
        error = "url resolves to a private or internal address"
        await record(Attempt(1, "failed", None, 0, len(payload), 0.0, error))
        return SendResult(delivered=False, final=True, status_code=None, rejected=0, attempts=1, error=error)

    headers = {
        **destination.headers,
        "Content-Type": _CONTENT_TYPES[destination.protocol],
        "Content-Encoding": "gzip",
    }
    status_code: int | None = None
    error: str | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        started = time.monotonic()
        response: httpx.Response | None = None
        try:
            response = await client.post(url, content=payload, headers=headers)
        except httpx.HTTPError as exc:
            status_code, error = None, type(exc).__name__
        else:
            status_code = response.status_code
            error = None if 200 <= status_code < 300 else f"HTTP {status_code}"
        duration_ms = (time.monotonic() - started) * 1000

        if status_code is not None and 200 <= status_code < 300:
            rejected = _rejected_count(response, signal)  # type: ignore[arg-type]
            await record(Attempt(attempt, "delivered", status_code, rejected, len(payload), duration_ms, None))
            return SendResult(True, False, status_code, rejected, attempt, None)

        if status_code is not None and not _retryable(status_code):
            await record(Attempt(attempt, "failed", status_code, 0, len(payload), duration_ms, error))
            return SendResult(False, True, status_code, 0, attempt, error)

        last = attempt == MAX_ATTEMPTS
        await record(Attempt(attempt, "failed" if last else "retry", status_code, 0, len(payload), duration_ms, error))
        if not last:
            await sleep(_retry_delay(response, attempt))

    return SendResult(False, False, status_code, 0, MAX_ATTEMPTS, error)
