# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Component source sync must not block the event loop (OBS-1395)."""

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import services.git_mirror_service as git_mirror
from api.routes.component_source import trigger_sync
from jobs.maintenance import sync_component_sources
from services.git_mirror_service import SyncResult

SYNC_SECONDS = 0.5
MAX_STALL = 0.2


def _slow_sync(url, component_type):
    time.sleep(SYNC_SECONDS)
    return SyncResult(success=True, commit_sha="a" * 40)


def _source():
    return SimpleNamespace(
        id=uuid.uuid4(),
        url="https://example.com/acme/catalog.git",
        component_type="skill",
        sync_status=None,
        sync_error="stale error",
        last_synced_at=None,
    )


async def _max_stall(coro) -> float:
    """Run coro while a ticker measures the longest gap the event loop went without running it."""
    worst = 0.0
    done = asyncio.Event()

    async def ticker():
        nonlocal worst
        last = time.perf_counter()
        while not done.is_set():
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            worst = max(worst, now - last)
            last = now

    tick = asyncio.create_task(ticker())
    await asyncio.sleep(0)  # let the ticker take its first timestamp before coro can block
    try:
        await coro
    finally:
        done.set()
        await tick
    return worst


async def test_trigger_sync_keeps_event_loop_responsive():
    source = _source()
    db = AsyncMock()
    db.get.return_value = source

    with patch("api.routes.component_source.sync_source", _slow_sync):
        stall = await _max_stall(trigger_sync(source.id, db=db, current_user=MagicMock()))

    assert stall < MAX_STALL
    assert source.sync_status == "success"
    assert source.sync_error is None


async def test_sync_component_sources_keeps_event_loop_responsive():
    source = _source()
    db = AsyncMock()
    db.execute.return_value = MagicMock(**{"scalars.return_value.all.return_value": [source]})

    @asynccontextmanager
    async def session():
        yield db

    with (
        patch.dict("sys.modules", {"database": MagicMock(async_session=session)}),
        patch.object(git_mirror, "sync_source", _slow_sync),
    ):
        stall = await _max_stall(sync_component_sources({}))

    assert stall < MAX_STALL
    assert source.sync_status == "success"
    assert source.sync_error is None
