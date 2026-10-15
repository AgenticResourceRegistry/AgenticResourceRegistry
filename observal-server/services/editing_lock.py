# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from fastapi import HTTPException

if TYPE_CHECKING:
    import uuid
from loguru import logger as optic

LOCK_TTL_MINUTES = 30


def _is_lock_expired(editing_since: datetime | None) -> bool:
    optic.trace("checking editing lock age")
    if not editing_since:
        return True
    # SQLite-backed tests may return a naive timestamp for a timezone-aware
    # column; stored lock times originate from UTC in acquire_edit_lock.
    stored = editing_since.replace(tzinfo=UTC) if editing_since.tzinfo is None else editing_since
    return datetime.now(UTC) - stored > timedelta(minutes=LOCK_TTL_MINUTES)


def acquire_edit_lock(
    version,
    user_id: uuid.UUID,
) -> None:
    """Set is_editing on a version row. Raises 409 if already locked by another user."""
    optic.trace("acquiring editing lock for version")
    if version.is_editing and not _is_lock_expired(version.editing_since) and version.editing_by != user_id:
        raise HTTPException(
            status_code=409,
            detail="This item is currently being edited by another user. Please try again later.",
        )
    version.is_editing = True
    version.editing_since = datetime.now(UTC)
    version.editing_by = user_id


def release_edit_lock(
    version,
    user_id: uuid.UUID,
    *,
    force: bool = False,
) -> None:
    """Clear is_editing on a version row. Only the lock holder (or force) can release."""
    optic.trace("releasing editing lock (force={})", force)
    if not version.is_editing:
        return
    if not force and version.editing_by != user_id:
        raise HTTPException(status_code=403, detail="You do not hold the edit lock on this item")
    version.is_editing = False
    version.editing_since = None
    version.editing_by = None


def is_actively_editing(version) -> bool:
    """Return True if the version is locked and the lock has not expired."""
    optic.trace("checking if version is locked for editing")
    if not getattr(version, "is_editing", False):
        return False
    editing_by = getattr(version, "editing_by", None)
    if editing_by is None:
        return False
    editing_since = getattr(version, "editing_since", None)
    if not isinstance(editing_since, datetime):
        return False
    return not _is_lock_expired(editing_since)
