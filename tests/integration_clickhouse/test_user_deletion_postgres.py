# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Opt-in proof of account deletion as a shell on a disposable PostgreSQL.

Set OBSERVAL_PG_USER_DELETION_URL and DATABASE_URL to the same local
``user_deletion_proof`` database. The test creates the current schema there;
never point it at a development or sample database.
"""

import os
import uuid
from unittest.mock import AsyncMock, patch
from urllib.parse import urlparse

import pytest

_URL = os.getenv("OBSERVAL_PG_USER_DELETION_URL")
pytestmark = pytest.mark.skipif(not _URL, reason="requires disposable user_deletion_proof PostgreSQL")


@pytest.mark.asyncio
async def test_deleted_user_becomes_shell_and_references_keep_resolving():
    assert _URL
    target = urlparse(_URL)
    assert target.hostname == "127.0.0.1" and target.path == "/user_deletion_proof"
    assert os.getenv("DATABASE_URL") == _URL

    from sqlalchemy import select, text
    from sqlalchemy.exc import IntegrityError

    import api.deps as deps
    import models  # noqa: F401  (registers every table)
    from api.routes.admin import users as admin_users
    from database import async_session, engine
    from models.base import Base
    from models.feedback import Feedback
    from models.inbox import InboxItem, InboxKind
    from models.user import User, UserRole, live_users
    from models.user_group import UserGroup
    from services.user_search import search_users

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.create_all)

    suffix = uuid.uuid4().hex[:8]
    admin = User(email=f"admin-{suffix}@example.test", username=f"admin{suffix}", name="Admin", role=UserRole.admin)
    person = User(email=f"alice-{suffix}@example.test", username=f"alice{suffix}", name="Alice Example")
    person.set_password("correct horse")
    async with async_session() as db:
        db.add_all([admin, person])
        await db.flush()
        # An authored record with a required, non-cascading FK to users...
        db.add(Feedback(listing_id=uuid.uuid4(), listing_type="mcp", user_id=person.id, rating=5))
        # ...and account-scoped rows that used to cascade with the user.
        db.add(UserGroup(user_id=person.id, group_name="engineering"))
        db.add(
            InboxItem(
                user_id=person.id,
                kind=next(iter(InboxKind)),
                title="t",
                subject_type="mcp",
                dedupe_key=f"k-{suffix}",
                payload={},
            )
        )
        await db.commit()
        admin_id, person_id = admin.id, person.id

    # The previous behaviour: a hard delete of anyone who authored a record fails.
    async with async_session() as db:
        with pytest.raises(IntegrityError):
            await db.execute(text("DELETE FROM users WHERE id = :id"), {"id": person_id})
            await db.commit()
        await db.rollback()

    async with async_session() as db:
        acting = (await db.execute(select(User).where(User.id == admin_id))).scalar_one()
        with (
            patch.object(admin_users, "emit_security_event", AsyncMock()),
            patch.object(admin_users, "revoke_deleted_user_tokens", AsyncMock(return_value=True)),
        ):
            await admin_users.delete_user(person_id, db, acting)

    async with async_session() as db:
        shell = (await db.execute(select(User).where(User.id == person_id))).scalar_one()
        assert shell.deleted_at is not None and shell.name == "Deleted user"
        assert shell.email == f"deleted-{person_id.hex}@deleted.invalid"
        assert shell.password_hash is None and not shell.verify_password("correct horse")
        # The authored record still resolves to the (shell) account.
        feedback = (await db.execute(select(Feedback).where(Feedback.user_id == person_id))).scalars().all()
        assert len(feedback) == 1
        # Account-scoped rows are gone, as a hard delete's cascade would have done.
        groups = (await db.execute(select(UserGroup).where(UserGroup.user_id == person_id))).scalars().all()
        inbox = (await db.execute(select(InboxItem).where(InboxItem.user_id == person_id))).scalars().all()
        assert groups == [] and inbox == []
        # Shells are invisible to listings and search.
        live = (await db.execute(select(User.id).where(live_users()))).scalars().all()
        assert person_id not in live and admin_id in live
        assert all(match.user.id != person_id for match in await search_users(db, "deleted", 50))

        # A still-valid access token for the shell authenticates as nobody.
        with (
            patch.object(deps, "decode_access_token", return_value={"sub": str(person_id), "jti": "j"}),
            patch.object(deps, "get_redis") as redis,
        ):
            redis.return_value.get = AsyncMock(return_value=None)
            assert await deps._authenticate_via_jwt("token", db) is None

        # Deleting the shell again is a 404, not a second scrub.
        acting = (await db.execute(select(User).where(User.id == admin_id))).scalar_one()
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as again:
            await admin_users.delete_user(person_id, db, acting)
        assert again.value.status_code == 404
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_deletions_can_never_remove_the_last_two_admins():
    """Both admins deleted at once in separate transactions: exactly one succeeds."""
    assert _URL and os.getenv("DATABASE_URL") == _URL
    import asyncio

    from sqlalchemy import func, select, text

    import models  # noqa: F401
    from database import async_session, engine
    from models.base import Base
    from models.user import User, UserRole, live_users
    from services.user_deletion import LastAdminError, delete_user_account

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.create_all)
        # Isolate from the other test's rows: only these two admins are live.
        await conn.execute(text("UPDATE users SET role = 'user' WHERE role IN ('admin', 'super_admin')"))
    suffix = uuid.uuid4().hex[:8]
    admins = [
        User(email=f"a{n}-{suffix}@example.test", username=f"a{n}{suffix}", name="Admin", role=UserRole.admin)
        for n in (1, 2)
    ]
    async with async_session() as db:
        db.add_all(admins)
        await db.commit()
    first_locked = asyncio.Event()

    async def delete(user_id, *, hold: bool):
        async with async_session() as db:
            user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
            try:
                await delete_user_account(db, user)
            except LastAdminError:
                await db.rollback()
                return "refused"
            if hold:
                first_locked.set()
                await asyncio.sleep(1.0)  # keep the admin rows locked while the other tries
            await db.commit()
            return "deleted"

    async def second():
        await first_locked.wait()
        return await delete(admins[1].id, hold=False)

    results = await asyncio.gather(delete(admins[0].id, hold=True), second())
    assert sorted(results) == ["deleted", "refused"]
    async with async_session() as db:
        live_admins = await db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role.in_([UserRole.admin, UserRole.super_admin]), live_users())
        )
    assert live_admins == 1
    await engine.dispose()
