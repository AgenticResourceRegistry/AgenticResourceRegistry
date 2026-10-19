# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Who receives an item.

Every resolver here is a thin wrapper over authorization that already exists.
No call site writes its own recipient query: one place to read, one place to
audit, and one place that has to change when the rules do.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from models.team import TeamMembership, TeamRole
from models.user import User, UserRole

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

# Global roles that may review the public catalog. Mirrors
# ``teamspace.is_global_reviewer``.
_GLOBAL_REVIEWER_ROLES = (UserRole.reviewer, UserRole.admin, UserRole.super_admin)
_ADMIN_ROLES = (UserRole.admin, UserRole.super_admin)


async def _user_ids(db: AsyncSession, stmt) -> list[uuid.UUID]:
    return list((await db.execute(stmt)).scalars().all())


async def global_reviewers(db: AsyncSession) -> list[uuid.UUID]:
    """Global reviewer, admin, and super-admin recipients."""
    return await _user_ids(db, select(User.id).where(User.role.in_(_GLOBAL_REVIEWER_ROLES)))


async def team_owners(db: AsyncSession, team_id: uuid.UUID) -> list[uuid.UUID]:
    """Owners of one teamspace — join requests and transfer offers land here."""
    return await _user_ids(
        db,
        select(TeamMembership.user_id).where(
            TeamMembership.team_id == team_id,
            TeamMembership.role == TeamRole.owner,
        ),
    )


async def admins(db: AsyncSession) -> list[uuid.UUID]:
    """Admins and super_admins, for system notices."""
    return await _user_ids(db, select(User.id).where(User.role.in_(_ADMIN_ROLES)))


def submitter_of(entity) -> list[uuid.UUID]:
    """The user a decision is addressed to.

    Listings carry ``submitted_by``; agent versions carry ``released_by``. An
    agent listing itself falls back to ``created_by``.
    """
    for attr in ("submitted_by", "released_by", "created_by"):
        value = getattr(entity, attr, None)
        if value is not None:
            return [value]
    return []
