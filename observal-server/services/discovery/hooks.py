# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Keep the discovery index current without touching every lifecycle route.

Approve, reject, archive, publish, transfer, edit and delete all end the same
way: a flush that writes a listing or version row. A session-level listener
watches those flushes, remembers which resources changed, and after the
commit schedules a reprojection of exactly those resources in a fresh
session. Routes stay unaware of discovery, and a new lifecycle path added
later is covered automatically.

The periodic full reprojection in ``jobs.maintenance`` is the safety net for
anything this misses (a crash between commit and reprojection, for example).
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Callable, Iterable
from itertools import chain
from typing import Any

from loguru import logger as optic
from sqlalchemy import event
from sqlalchemy.orm import Session

from models.agent import Agent, AgentVersion
from models.discovery_entry import DiscoveryEntry, DiscoveryKind
from models.hook import HookListing, HookVersion
from models.mcp import McpListing, McpVersion
from models.prompt import PromptListing, PromptVersion
from models.sandbox import SandboxListing, SandboxVersion
from models.skill import SkillListing, SkillVersion

DirtySet = set[tuple[DiscoveryKind, uuid.UUID]]
Scheduler = Callable[[DirtySet], Any]

_INFO_KEY = "discovery_dirty"

# model -> (kind, attribute holding the listing id)
_TRACKED: dict[type, tuple[DiscoveryKind, str]] = {
    SkillListing: (DiscoveryKind.skill, "id"),
    SkillVersion: (DiscoveryKind.skill, "listing_id"),
    McpListing: (DiscoveryKind.mcp, "id"),
    McpVersion: (DiscoveryKind.mcp, "listing_id"),
    HookListing: (DiscoveryKind.hook, "id"),
    HookVersion: (DiscoveryKind.hook, "listing_id"),
    PromptListing: (DiscoveryKind.prompt, "id"),
    PromptVersion: (DiscoveryKind.prompt, "listing_id"),
    SandboxListing: (DiscoveryKind.sandbox, "id"),
    SandboxVersion: (DiscoveryKind.sandbox, "listing_id"),
    Agent: (DiscoveryKind.agent, "id"),
    AgentVersion: (DiscoveryKind.agent, "agent_id"),
}

_installed = False
_scheduler: Scheduler | None = None
_background: set[asyncio.Task] = set()


def collect_dirty(objects: Iterable[Any]) -> DirtySet:
    """Resource keys for every tracked object in ``objects``."""
    dirty: DirtySet = set()
    for obj in objects:
        if isinstance(obj, DiscoveryEntry):
            continue  # our own writes must not retrigger us
        spec = _TRACKED.get(type(obj))
        if spec is None:
            continue
        kind, attr = spec
        entity_id = getattr(obj, attr, None)
        if entity_id is not None:
            dirty.add((kind, entity_id))
    return dirty


def _after_flush(session: Session, _flush_context) -> None:
    changed = collect_dirty(chain(session.new, session.dirty, session.deleted))
    if changed:
        session.info.setdefault(_INFO_KEY, set()).update(changed)


def _after_commit(session: Session) -> None:
    # SQLAlchemy also fires after_commit when a SAVEPOINT is released
    # (begin_nested), while the outer transaction is still open. Scheduling
    # then would reproject before the rows are visible to another connection,
    # so wait for the outermost commit.
    if session.in_nested_transaction():
        return
    dirty: DirtySet | None = session.info.pop(_INFO_KEY, None)
    if dirty:
        schedule(dirty)


def _after_rollback(session: Session) -> None:
    session.info.pop(_INFO_KEY, None)


async def reproject(dirty: DirtySet) -> None:
    """Reproject the given resources in a fresh session. Errors are logged, never raised."""
    from database import async_session
    from services.discovery.projection import project_entity, resolve_context

    try:
        async with async_session() as db:
            ctx = await resolve_context(db)
            for kind, entity_id in sorted(dirty, key=lambda item: (item[0].value, str(item[1]))):
                # SAVEPOINT per resource so one failure only discards its own work.
                try:
                    async with db.begin_nested():
                        await project_entity(db, kind, entity_id, ctx=ctx)
                except Exception:
                    optic.exception("discovery reprojection failed kind={} id={}", kind.value, entity_id)
            await db.commit()
        optic.debug("discovery reprojected {} resource(s)", len(dirty))
    except Exception:
        optic.exception("discovery reprojection batch failed count={}", len(dirty))


def _default_scheduler(dirty: DirtySet) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        optic.debug("discovery: no running loop, skipping reprojection of {} resource(s)", len(dirty))
        return
    task = loop.create_task(reproject(dirty))
    _background.add(task)
    task.add_done_callback(_background.discard)


def schedule(dirty: DirtySet) -> None:
    """Hand a dirty set to the configured scheduler (tests swap this out)."""
    (_scheduler or _default_scheduler)(dirty)


def set_scheduler(scheduler: Scheduler | None) -> None:
    global _scheduler
    _scheduler = scheduler


def install() -> None:
    """Register the session listeners once per process."""
    global _installed
    if _installed:
        return
    event.listen(Session, "after_flush", _after_flush)
    event.listen(Session, "after_commit", _after_commit)
    event.listen(Session, "after_rollback", _after_rollback)
    _installed = True
    optic.debug("discovery reprojection hooks installed")


def uninstall() -> None:
    global _installed
    if not _installed:
        return
    for name, fn in (
        ("after_flush", _after_flush),
        ("after_commit", _after_commit),
        ("after_rollback", _after_rollback),
    ):
        with contextlib.suppress(Exception):
            event.remove(Session, name, fn)
    _installed = False


async def drain() -> None:
    """Wait for in-flight background reprojections (used by tests and shutdown)."""
    if _background:
        await asyncio.gather(*list(_background), return_exceptions=True)


__all__ = [
    "DirtySet",
    "collect_dirty",
    "drain",
    "install",
    "reproject",
    "schedule",
    "set_scheduler",
    "uninstall",
]
