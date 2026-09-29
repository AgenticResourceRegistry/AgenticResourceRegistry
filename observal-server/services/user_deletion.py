# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Delete a user account by reducing its row to an empty shell.

Policy: telemetry belongs to the instance once it has been sent. Deleting an
account does not remove or alter sessions, derived activity, snapshots or
reports; they keep the original user ID and age out only through retention.
Everything the user authored (listings, versions, reviews, co-authorship)
also stays, still referencing the same ID.

The ``users`` row is kept, so every reference keeps resolving, but it is
scrubbed of identifying fields, marked ``deleted_at`` and can never log in.
Rows the database would have cascade-deleted with the user (group, work
profile, recommendation feedback, team membership and join requests, inbox)
are removed, exactly as a hard delete would have done.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from loguru import logger as optic
from sqlalchemy import delete, select

from models.inbox import InboxItem
from models.team import TeamMembership, TeamMembershipRequest
from models.user import DELETED_AUTH_PROVIDER, DELETED_USER_NAME, User, UserRole, is_deleted_account, live_users
from models.user_group import UserGroup
from models.user_profile import RecommendationFeedback, UserWorkProfile

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Tables whose user FK is ON DELETE CASCADE: account-scoped, not records.
_ACCOUNT_ROWS = (UserGroup, UserWorkProfile, RecommendationFeedback, TeamMembership, TeamMembershipRequest, InboxItem)


def _shell_identity(user_id) -> tuple[str, str]:
    """Unique, non-identifying email and username that cannot receive mail."""
    return f"deleted-{user_id.hex}@deleted.invalid", f"deleted-{user_id.hex[:24]}"  # username <= 32 chars


_ADMIN_ROLES = (UserRole.admin, UserRole.super_admin)


class LastAdminError(Exception):
    """Deleting this account would leave the instance with no usable admin."""


async def ensure_not_last_admin(db: AsyncSession, user: User) -> None:
    """Refuse to delete the last live admin, whichever route asks.

    A shell keeps its row, so first-run bootstrap (which requires an empty
    ``users`` table) could never create a replacement. The live admin rows are
    locked, so two concurrent deletions cannot each see the other as the
    remaining admin.
    """
    if user.role not in _ADMIN_ROLES:
        return
    rows = await db.execute(
        select(User.id).where(User.role.in_(_ADMIN_ROLES), live_users()).order_by(User.id).with_for_update()
    )
    if len(rows.scalars().all()) <= 1:
        raise LastAdminError("Cannot delete the last admin")


async def delete_user_account(db: AsyncSession, user: User) -> None:
    """Scrub ``user`` into a deleted shell in the caller's transaction; the caller commits.

    Token revocation is done by ``revoke_deleted_user_tokens`` after commit.
    Authentication also rejects ``deleted_at`` itself, so revocation is
    defence in depth rather than the only barrier.
    """
    if is_deleted_account(user):
        raise ValueError("User is already deleted")
    await ensure_not_last_admin(db, user)
    for model in _ACCOUNT_ROWS:
        await db.execute(delete(model).where(model.user_id == user.id))
    email, username = _shell_identity(user.id)
    user.email = email
    user.username = username
    user.name = DELETED_USER_NAME
    user.password_hash = None
    user.sso_subject_id = None
    user.avatar_url = None
    user.department = None
    user.auth_provider = DELETED_AUTH_PROVIDER
    user.role = UserRole.user
    user.deleted_at = datetime.now(UTC)


async def revoke_deleted_user_tokens(user_id) -> bool:
    """Revoke outstanding access and refresh tokens for a deleted account. Best effort."""
    try:
        import services.dynamic_settings as ds
        from services.redis import get_redis

        redis = get_redis()
        ttl = ds.get_sync_int("jwt.refresh_token_expire_days", 30) * 86400
        await redis.setex(f"revoked_user:{user_id}", ttl, "1")
        await redis.delete(f"must_change_password:{user_id}")
        return True
    except Exception as error:
        optic.warning("could not revoke tokens for deleted user {}: {}", user_id, type(error).__name__)
        return False
