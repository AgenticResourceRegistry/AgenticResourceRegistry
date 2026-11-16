# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Opt-in real auth regression: disposable PostgreSQL/Redis, no workers.

Set OBSERVAL_AUDIT_INTEGRATION=1 with isolated DATABASE_URL/REDIS_URL/JWT_KEY_DIR
and isolated/unreachable CLICKHOUSE_URL. Enable pg_trgm before schema creation.
"""

import os
import uuid

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("OBSERVAL_AUDIT_INTEGRATION") != "1", reason="requires disposable PostgreSQL and Redis"
)


@pytest.mark.asyncio
async def test_admin_mutations_respect_target_role():
    from app_factory import create_app
    from config import settings
    from database import async_session, engine
    from models import Base
    from models.user import User, UserRole, is_deleted_account
    from services.crypto import init_key_manager
    from services.redis import close

    password = "Boundary-Initial-Password9!"
    changed = "Boundary-Changed-Password9!"
    init_key_manager(settings.JWT_KEY_DIR)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        users = []
        async with async_session() as db:
            for role in (UserRole.admin, UserRole.super_admin, UserRole.super_admin):
                suffix = uuid.uuid4().hex[:10]
                user = User(
                    email=f"boundary-{suffix}@test.invalid", username=f"boundary-{suffix}", name="Test", role=role
                )
                user.set_password(password)
                db.add(user)
                users.append(user)
            await db.commit()
        admin, target, peer = users
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()),
            base_url="http://boundary.test",
            headers={"User-Agent": "boundary-integration"},
        ) as client:

            async def login(user, credential=password):
                response = await client.post("/api/v1/auth/login", json={"email": user.email, "password": credential})
                assert response.status_code == 200, response.text
                return {"Authorization": f"Bearer {response.json()['access_token']}"}

            headers = await login(admin)
            for method, suffix, payload in (
                ("PUT", "/password", {"generate": True}),
                ("DELETE", "", None),
                ("PUT", "/role", {"role": "user"}),
            ):
                response = await client.request(
                    method, f"/api/v1/admin/users/{target.id}{suffix}", json=payload, headers=headers
                )
                assert response.status_code == 403, response.text
            headers = await login(target)  # Denial preserved the original credential.
            reset = await client.put(
                f"/api/v1/admin/users/{peer.id}/password", json={"new_password": changed}, headers=headers
            )
            assert reset.status_code == 200, reset.text
            await login(peer, changed)
            deleted = await client.delete(f"/api/v1/admin/users/{peer.id}", headers=headers)
            assert deleted.status_code == 204, deleted.text
        async with async_session() as db:
            row = await db.get(User, target.id)
            assert row.deleted_at is None and row.role is UserRole.super_admin
            shell = await db.get(User, peer.id)
            assert is_deleted_account(shell) and shell.password_hash is None
    finally:
        try:
            await engine.dispose()
        finally:
            await close()
