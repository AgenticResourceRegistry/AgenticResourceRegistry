# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Forward one session's stored rows to one OTLP destination.

ClickHouse is the queue.  Every run reads the destination's watermark and the
session checkpoint and sends what lies between, so a run can stop anywhere (a
crash, a job timeout, an outage) and the next one picks up exactly where the
watermark says:

1. Take a lock per destination, signal and session.  A run that finds it held
   exits; the run holding it re-reads the checkpoint before it finishes.
2. Skip a destination paused after a final failure (a 4xx retrying cannot
   fix).  The pause expires after an hour, or when an admin saves the
   destinations again.
3. On the first run for a session, skip it when it started before the
   destination was added (``start_from: "now"``).
4. Rows at or below the checkpoint never change, except after an integrity
   repair rewinds the checkpoint.  Then the watermark moves back with it, and
   the rows sent before are sent again, marked ``observal.forward.replay``.
5. Send rows in ``(watermark, checkpoint]`` in chunks and move the watermark
   after each 2xx, then re-read the checkpoint and repeat until caught up.

Rows are read back from ``session_events`` after ingest has redacted and
de-duplicated them; nothing here ever sees an ingest request body.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from loguru import logger as optic

from services.otel.encode import encode_logs, to_bytes
from services.otel.transport import Attempt, post_otlp

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    import httpx

    from services.otel.destinations import Destination
    from services.otel.types import Row, SessionKey

LOGS = "logs"
FORWARD_JOB = "forward_session"

WINDOW_LINES = 1000  # rows read from ClickHouse at a time
MAX_CHUNK_RECORDS = 1000
MAX_CHUNK_BYTES = 4 * 1024 * 1024  # before compression
LOCK_TTL_SECONDS = 150  # longer than the job timeout, so a killed job's lock still expires
PAUSE_TTL_SECONDS = 3600


@dataclass(frozen=True)
class ForwardState:
    forwarded_line: int = -1
    replay_through: int = -1
    session_closed: bool = False


@dataclass(frozen=True)
class ForwardResult:
    status: str  # caught_up | locked | paused | excluded | retry_later | failed
    records_sent: int = 0


def job_id(destination_id: str, key: SessionKey) -> str:
    """One queued forward job per destination and session; repeats collapse into it."""
    return f"otlp:{destination_id}:{key.project_id}:{key.user_id}:{key.harness}:{key.session_id}"


def _lock_name(destination_id: str, signal: str, key: SessionKey) -> str:
    return f"otlp:lock:{destination_id}:{signal}:{key.project_id}:{key.user_id}:{key.harness}:{key.session_id}"


def _pause_name(destination_id: str) -> str:
    return f"otlp:paused:{destination_id}"


class ForwardStore:
    """ClickHouse and Redis access for the forwarder; tests swap in a fake."""

    @asynccontextmanager
    async def locked(self, name: str) -> AsyncIterator[bool]:
        from redis.exceptions import LockError

        from services.redis import get_redis

        lock = get_redis().lock(name, timeout=LOCK_TTL_SECONDS, blocking=False)
        acquired = await lock.acquire()
        try:
            yield acquired
        finally:
            if acquired:
                try:
                    await lock.release()
                except LockError:
                    pass  # expired first; the next run takes over

    async def is_paused(self, destination_id: str) -> bool:
        from services.redis import get_redis

        return await get_redis().exists(_pause_name(destination_id)) > 0

    async def pause(self, destination_id: str, status_code: int | None) -> None:
        from services.redis import get_redis

        await get_redis().set(_pause_name(destination_id), str(status_code or ""), ex=PAUSE_TTL_SECONDS)

    async def checkpoint(self, key: SessionKey) -> int:
        from services.clickhouse import query_session_checkpoint

        line, _offset = await query_session_checkpoint(key.session_id, key.project_id, key.user_id, key.harness)
        return line

    async def state(self, destination_id: str, signal: str, key: SessionKey) -> ForwardState | None:
        from services.clickhouse import query_forward_state

        stored = await query_forward_state(destination_id, signal, key)
        if stored is None:
            return None
        forwarded_line, replay_through, session_closed = stored
        return ForwardState(forwarded_line, replay_through, session_closed)

    async def set_state(self, destination_id: str, signal: str, key: SessionKey, state: ForwardState) -> None:
        from services.clickhouse import insert_forward_state

        await insert_forward_state(
            destination_id,
            signal,
            key,
            state.forwarded_line,
            replay_through=state.replay_through,
            session_closed=state.session_closed,
        )

    async def first_event(self, key: SessionKey) -> datetime | None:
        from services.clickhouse import query_session_first_event

        return await query_session_first_event(key)

    async def rows(self, key: SessionKey, *, after_line: int, up_to_line: int) -> list[dict]:
        from services.clickhouse import query_session_rows

        return await query_session_rows(key, after_line=after_line, up_to_line=up_to_line)

    async def record_deliveries(self, records: list[dict]) -> None:
        from services.clickhouse import insert_forward_deliveries

        await insert_forward_deliveries(records)


async def clear_pauses(destination_ids: list[str]) -> None:
    """Lift pauses after an admin saves the destinations (the fix may be in the new value)."""
    if not destination_ids:
        return
    from services.redis import get_redis

    await get_redis().delete(*(_pause_name(destination_id) for destination_id in destination_ids))


def _row_bytes(row: Row) -> int:
    # The stored line dominates a record's size; the rest is a few hundred bytes of attributes.
    return len(str(row.get("raw_line") or "")) + 512


def _chunks(rows: list[Row], replay_through: int) -> list[tuple[list[Row], bool]]:
    """Split rows into sendable chunks; a chunk is all replayed rows or all new ones."""
    chunks: list[tuple[list[Row], bool]] = []
    current: list[Row] = []
    current_bytes = 0
    current_replay = False
    for row in rows:
        replay = int(row["line_offset"]) <= replay_through
        size = _row_bytes(row)
        if current and (
            replay != current_replay or len(current) >= MAX_CHUNK_RECORDS or current_bytes + size > MAX_CHUNK_BYTES
        ):
            chunks.append((current, current_replay))
            current, current_bytes = [], 0
        if not current:
            current_replay = replay
        current.append(row)
        current_bytes += size
    if current:
        chunks.append((current, current_replay))
    return chunks


def _delivery_record(destination_id: str, signal: str, key: SessionKey, records: int, attempt: Attempt) -> dict:
    return {
        "delivery_id": str(uuid.uuid4()),
        "destination_id": destination_id,
        "signal": signal,
        "session_id": key.session_id,
        "attempt": attempt.attempt,
        "status_code": attempt.status_code,
        "status": attempt.status,
        "records": records,
        "rejected": attempt.rejected,
        "payload_bytes": attempt.payload_bytes,
        "duration_ms": round(attempt.duration_ms, 3),
        "error": attempt.error,
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
    }


async def forward_session_logs(
    destination: Destination,
    key: SessionKey,
    *,
    store: ForwardStore,
    client: httpx.AsyncClient,
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
) -> ForwardResult:
    """Send one session's unsent rows to ``destination`` as OTLP log records."""
    signal = LOGS
    async with store.locked(_lock_name(destination.id, signal, key)) as acquired:
        if not acquired:
            return ForwardResult("locked")
        if await store.is_paused(destination.id):
            return ForwardResult("paused")

        sent = 0
        while True:
            checkpoint = await store.checkpoint(key)
            state = await store.state(destination.id, signal, key)
            if state is None:
                if checkpoint < 0:
                    return ForwardResult("caught_up", sent)
                first_event = await store.first_event(key)
                if destination.added_at and first_event and first_event < destination.added_at:
                    return ForwardResult("excluded")
                state = ForwardState()

            if state.forwarded_line > checkpoint:
                # An integrity repair rewound the checkpoint; everything above it may be replaced.
                optic.info(
                    "otlp forward rewind: destination={}, session={}, from={}, to={}",
                    destination.id,
                    key.session_id,
                    state.forwarded_line,
                    checkpoint,
                )
                state = ForwardState(
                    forwarded_line=checkpoint,
                    replay_through=max(state.forwarded_line, state.replay_through),
                    session_closed=state.session_closed,
                )
                await store.set_state(destination.id, signal, key, state)

            if state.forwarded_line >= checkpoint:
                return ForwardResult("caught_up", sent)

            up_to = min(checkpoint, state.forwarded_line + WINDOW_LINES)
            rows = await store.rows(key, after_line=state.forwarded_line, up_to_line=up_to)
            if not rows:
                optic.warning(
                    "otlp forward found no rows below the checkpoint: destination={}, session={}, lines {}..{}",
                    destination.id,
                    key.session_id,
                    state.forwarded_line + 1,
                    up_to,
                )
                return ForwardResult("retry_later", sent)

            for chunk, replay in _chunks(rows, state.replay_through):
                body = to_bytes(
                    encode_logs(chunk, include_content=destination.include_content, replay=replay),
                    protocol=destination.protocol,
                )
                attempts: list[dict] = []

                async def on_attempt(attempt: Attempt, size: int = len(chunk), log: list[dict] = attempts) -> None:
                    log.append(_delivery_record(destination.id, signal, key, size, attempt))

                result = await post_otlp(client, destination, signal, body, sleep=sleep, on_attempt=on_attempt)
                await store.record_deliveries(attempts)
                if not result.delivered:
                    optic.warning(
                        "otlp forward failed: destination={}, session={}, status={}, error={}, final={}",
                        destination.id,
                        key.session_id,
                        result.status_code,
                        result.error,
                        result.final,
                    )
                    if result.final:
                        await store.pause(destination.id, result.status_code)
                        return ForwardResult("failed", sent)
                    return ForwardResult("retry_later", sent)
                if result.rejected:
                    optic.warning(
                        "otlp destination rejected records: destination={}, session={}, rejected={}",
                        destination.id,
                        key.session_id,
                        result.rejected,
                    )

                state = ForwardState(
                    forwarded_line=int(chunk[-1]["line_offset"]),
                    replay_through=state.replay_through,
                    session_closed=state.session_closed,
                )
                await store.set_state(destination.id, signal, key, state)
                sent += len(chunk)


async def enqueue_forwarding(key: SessionKey, *, pool=None) -> int:
    """Queue a forward job for each enabled destination; returns how many were queued."""
    from services.otel.destinations import load_destinations

    destinations = [destination for destination in await load_destinations() if destination.enabled]
    if not destinations:
        return 0
    if pool is None:
        from services.redis import _get_arq_pool

        pool = await _get_arq_pool()
    for destination in destinations:
        await pool.enqueue_job(
            FORWARD_JOB,
            destination.id,
            key.project_id,
            key.user_id,
            key.harness,
            key.session_id,
            _job_id=job_id(destination.id, key),
        )
    return len(destinations)
