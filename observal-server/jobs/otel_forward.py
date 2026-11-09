# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import httpx
from loguru import logger as optic

SWEEP_LIMIT = 500  # sessions per destination per tick


async def forward_session(
    ctx: dict,
    destination_id: str,
    project_id: str,
    user_id: str,
    harness: str,
    session_id: str,
) -> str:
    """Send one session's unsent records to one destination."""
    from services.otel.destinations import load_destinations
    from services.otel.forwarder import LOGS, ForwardStore, forward_session_logs
    from services.otel.transport import REQUEST_TIMEOUT_SECONDS
    from services.otel.types import SessionKey

    destination = next(
        (d for d in await load_destinations() if d.id == destination_id and d.enabled),
        None,
    )
    if destination is None:
        return "destination_gone"

    key = SessionKey(project_id=project_id, user_id=user_id, harness=harness, session_id=session_id)
    statuses: list[str] = []
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=False) as client:
        for signal in destination.signals:
            if signal != LOGS:
                continue
            result = await forward_session_logs(destination, key, store=ForwardStore(), client=client)
            statuses.append(f"{signal}:{result.status}:{result.records_sent}")
            optic.debug(
                "otlp forward: destination={}, session={}, signal={}, status={}, sent={}",
                destination.id,
                session_id,
                signal,
                result.status,
                result.records_sent,
            )
    return ",".join(statuses)


async def sweep_otlp_forwarding(ctx: dict) -> int:
    """Queue forward jobs for sessions each enabled destination is behind on."""
    from services.clickhouse import query_forward_candidates
    from services.otel.destinations import load_destinations
    from services.otel.forwarder import FORWARD_JOB, LOGS, ForwardStore, job_id

    destinations = [d for d in await load_destinations() if d.enabled]
    if not destinations:
        return 0

    store = ForwardStore()
    pool = ctx["redis"]
    queued = 0
    for destination in destinations:
        if await store.is_paused(destination.id):
            continue
        keys = set()
        for signal in destination.signals:
            if signal != LOGS:
                continue
            keys.update(await query_forward_candidates(destination.id, signal, destination.added_at, SWEEP_LIMIT))
        for key in keys:
            await pool.enqueue_job(
                FORWARD_JOB,
                destination.id,
                key.project_id,
                key.user_id,
                key.harness,
                key.session_id,
                _job_id=job_id(destination.id, key),
            )
        queued += len(keys)
        if keys:
            optic.info("otlp sweep queued {} sessions for destination={}", len(keys), destination.id)
    return queued
