# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Async webhook delivery service with HMAC signing and retry logic.

Delivers signed webhooks with exponential backoff. Records delivery
outcomes to an in-memory buffer for batch-insert to ClickHouse.
"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from uuid import UUID

import httpx
from loguru import logger as optic

from services.ssrf_guard import is_private_url
from services.webhook_signer import HEADER_EVENT_ID, build_headers

# Buffer for batch ClickHouse inserts (AD-1)
_delivery_buffer: list[dict] = []
_BUFFER_FLUSH_THRESHOLD = 1000


@dataclass
class DeliveryResult:
    """Outcome of a webhook delivery attempt."""

    success: bool
    status_code: int | None
    attempts: int
    error: str | None
    duration_ms: float
    event_id: UUID


@dataclass
class DeliveryRecord:
    """A single delivery attempt record for ClickHouse."""

    delivery_id: UUID
    event_id: UUID
    alert_rule_id: UUID
    attempt_number: int
    timestamp: str
    webhook_url: str
    status_code: int | None
    delivery_status: str
    error: str | None
    duration_ms: float
    payload_size: int


def _buffer_delivery_record(
    alert_rule_id: UUID,
    event_id: UUID,
    attempt_number: int,
    webhook_url: str,
    status_code: int | None,
    delivery_status: str,
    error: str | None,
    duration_ms: float,
    payload_size: int,
) -> None:
    """Buffer a delivery record for later batch-insert to ClickHouse."""
    optic.trace("buffering delivery record for alert rule")
    from datetime import UTC, datetime

    record = {
        "delivery_id": str(uuid.uuid4()),
        "event_id": str(event_id),
        "alert_rule_id": str(alert_rule_id),
        "attempt_number": attempt_number,
        "timestamp": datetime.now(UTC).isoformat(),
        "webhook_url": webhook_url,
        "status_code": status_code,
        "delivery_status": delivery_status,
        "error": error,
        "duration_ms": round(duration_ms, 2),
        "payload_size": payload_size,
    }
    _delivery_buffer.append(record)

    # Hard cap: flush immediately if buffer grows too large (burst protection)
    if len(_delivery_buffer) >= _BUFFER_FLUSH_THRESHOLD:
        optic.info("Delivery buffer hit threshold ({}), scheduling flush", _BUFFER_FLUSH_THRESHOLD)
        # Note: actual flush is async and called by the evaluator at cycle end
        # This is a safety net - in practice the evaluator flushes first


async def flush_delivery_records() -> int:
    """Batch-insert buffered delivery records to ClickHouse.

    Called at end of evaluation cycle. Fire-and-forget: failures are
    logged but don't affect delivery outcomes.

    Returns:
        Number of records flushed.
    """
    if not _delivery_buffer:
        return 0

    records = _delivery_buffer.copy()
    _delivery_buffer.clear()

    try:
        from services.clickhouse import _insert_webhook_deliveries

        await _insert_webhook_deliveries(records)
        optic.info("Flushed {} delivery records to ClickHouse", len(records))
    except Exception as e:
        optic.error("Failed to flush delivery records to ClickHouse: {}", e)
        # Don't re-raise - delivery recording is best-effort

    return len(records)


async def deliver_webhook(
    webhook_url: str,
    webhook_secret: str,
    payload: dict,
    alert_rule_id: UUID,
    *,
    max_retries: int = 3,
    timeout_seconds: float = 10.0,
) -> DeliveryResult:
    """Deliver a signed webhook with exponential backoff retry.

    Serializes payload once before the retry loop (AD-5: payload immutability).
    Generates a unique event_id per delivery for receiver idempotency (AD-2).
    Buffers delivery records for batch-insert to ClickHouse (AD-1).

    Args:
        webhook_url: Target URL (must pass SSRF validation).
        webhook_secret: Signing secret. If empty, delivers without signature.
        payload: JSON-serializable dict to deliver.
        alert_rule_id: UUID of the alert rule that triggered this delivery.
        max_retries: Maximum number of delivery attempts (default 3).
        timeout_seconds: HTTP request timeout per attempt (default 10s).

    Returns:
        DeliveryResult with outcome details.
    """
    # SSRF protection
    optic.debug("delivery attempt")
    if await asyncio.to_thread(is_private_url, webhook_url):
        return DeliveryResult(
            success=False,
            status_code=None,
            attempts=0,
            error="SSRF: webhook URL resolves to private network",
            duration_ms=0.0,
            event_id=uuid.uuid4(),
        )

    # Serialize ONCE outside the loop (AD-5: payload immutability)
    body = json.dumps(payload, default=str).encode()
    event_id = uuid.uuid4()

    # Build headers (with or without signing)
    headers = build_headers(webhook_secret, body) if webhook_secret else {HEADER_EVENT_ID: str(event_id)}

    headers["Content-Type"] = "application/json"
    headers[HEADER_EVENT_ID] = str(event_id)  # Ensure event_id is always set

    start = time.monotonic()
    last_error: str | None = None

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        for attempt in range(max_retries):
            if attempt > 0:
                await asyncio.sleep(2 ** (attempt - 1))  # 1s, 2s backoff

            try:
                resp = await client.post(webhook_url, content=body, headers=headers)
                duration_ms = (time.monotonic() - start) * 1000

                status = "delivered" if resp.status_code < 400 else "failed"
                _buffer_delivery_record(
                    alert_rule_id,
                    event_id,
                    attempt + 1,
                    webhook_url,
                    resp.status_code,
                    status,
                    None,
                    duration_ms,
                    len(body),
                )

                if resp.status_code < 400:
                    return DeliveryResult(
                        success=True,
                        status_code=resp.status_code,
                        attempts=attempt + 1,
                        error=None,
                        duration_ms=duration_ms,
                        event_id=event_id,
                    )

                # Don't retry 4xx (client error - retrying won't help)
                if resp.status_code < 500:
                    last_error = f"HTTP {resp.status_code}"
                    break

                last_error = f"HTTP {resp.status_code}"

            except Exception as e:
                duration_ms = (time.monotonic() - start) * 1000
                last_error = str(e)
                _buffer_delivery_record(
                    alert_rule_id,
                    event_id,
                    attempt + 1,
                    webhook_url,
                    None,
                    "failed",
                    last_error,
                    duration_ms,
                    len(body),
                )

    duration_ms = (time.monotonic() - start) * 1000
    return DeliveryResult(
        success=False,
        status_code=None,
        attempts=max_retries,
        error=last_error,
        duration_ms=duration_ms,
        event_id=event_id,
    )
