# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Component source sync must not block the event loop (OBS-1395)."""

import asyncio
import uuid
from contextlib import asynccontextmanager
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import services.git_mirror_service as git_mirror
from api.routes.component_source import trigger_sync
from jobs.maintenance import sync_component_sources
from services.git_mirror_service import SyncResult


def _gated_sync(started: Event, release: Event):
    def sync(url, component_type):
        started.set()
        if not release.wait(timeout=5):
            raise AssertionError("the event loop could not release the blocked sync")
        return SyncResult(success=True, commit_sha="a" * 40)

    return sync


def _source():
    return SimpleNamespace(
        id=uuid.uuid4(),
        url="https://example.com/acme/catalog.git",
        component_type="skill",
        sync_status=None,
        sync_error="stale error",
        last_synced_at=None,
    )


async def _assert_sync_does_not_block_loop(coro, started: Event, release: Event) -> None:
    """The loop must progress while sync work waits on a separate thread."""
    task = asyncio.create_task(coro)
    try:
        assert await asyncio.to_thread(started.wait, 5), "sync work did not start"
        await asyncio.sleep(0)
        assert not task.done(), "sync work finished before the loop could release it"
    finally:
        release.set()
        await task


async def test_trigger_sync_keeps_event_loop_responsive():
    source = _source()
    db = AsyncMock()
    db.get.return_value = source

    started, release = Event(), Event()
    with patch("api.routes.component_source.sync_source", _gated_sync(started, release)):
        await _assert_sync_does_not_block_loop(
            trigger_sync(source.id, db=db, current_user=MagicMock()), started, release
        )

    assert source.sync_status == "success"
    assert source.sync_error is None


async def test_sync_component_sources_keeps_event_loop_responsive():
    source = _source()
    db = AsyncMock()
    db.execute.return_value = MagicMock(**{"scalars.return_value.all.return_value": [source]})

    @asynccontextmanager
    async def session():
        yield db

    started, release = Event(), Event()
    with (
        patch.dict("sys.modules", {"database": MagicMock(async_session=session)}),
        patch.object(git_mirror, "sync_source", _gated_sync(started, release)),
    ):
        await _assert_sync_does_not_block_loop(sync_component_sources({}), started, release)

    assert source.sync_status == "success"
    assert source.sync_error is None
