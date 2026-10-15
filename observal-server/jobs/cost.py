# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Cost background jobs: token usage de-duplication backfill and opportunity detection."""

from __future__ import annotations

import time

from loguru import logger as optic

BACKFILL_BATCH = 200
BACKFILL_BUDGET_SECONDS = 240


async def backfill_usage_dedup(ctx: dict) -> dict:
    """Key and de-duplicate stored token rows, a bounded number of sessions per run.

    Runs on a schedule until no session has unkeyed token rows, then becomes a
    cheap no-op. Each session is read and rewritten on its own, so memory use
    does not grow with the table.
    """
    from services import usage_dedup

    started = time.monotonic()
    sessions = rows = failed = 0
    seen: set[str] = set()  # never revisit a session within one run
    while time.monotonic() - started < BACKFILL_BUDGET_SECONDS:
        pending = await usage_dedup.pending_sessions(BACKFILL_BATCH, exclude=seen)
        if not pending:
            break
        progressed = False
        for s in pending:
            if time.monotonic() - started >= BACKFILL_BUDGET_SECONDS:
                break
            seen.add(s["session_id"])
            try:
                rows += await usage_dedup.backfill_session(s["session_id"], s["project_id"], s["user_id"], s["harness"])
                sessions += 1
                progressed = True
            except Exception:
                failed += 1
                optic.exception("usage dedup backfill failed session={}", s.get("session_id"))
        if not progressed:
            break
    if sessions or failed:
        optic.info("usage dedup backfill: sessions={} rows_rewritten={} failed={}", sessions, rows, failed)
    return {"sessions": sessions, "rows_rewritten": rows, "failed": failed}


async def refresh_cost_intelligence(ctx: dict) -> dict:
    """Detect opportunities, mark shipped ones implemented, and warm the dashboard cache."""
    from database import async_session
    from services.cost import service

    async with async_session() as db:
        result = await service.refresh(db)
    return result
