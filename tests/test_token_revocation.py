# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""User-wide token revocation must stick: a later login, refresh, or password change never revives old tokens."""

import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import api.deps as deps
import api.routes.admin.users as admin_users
import api.routes.auth as auth
import services.jwt_service as jwt_service
from models.user import User, UserRole
from schemas.admin import AdminResetPasswordRequest
from schemas.auth import ChangePasswordRequest, CodeExchangeRequest, LogoutRequest, RefreshRequest
from services.token_revocation import is_revoked, revoke_user_tokens, token_issued_us

PASSWORD = "OldPassw0rd!xyz"


@pytest.fixture(autouse=True, scope="module")
def _keys(tmp_path_factory):
    from services.crypto import init_key_manager

    init_key_manager(key_dir=str(tmp_path_factory.mktemp("keys")), key_password=None)


class _Redis:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def getdel(self, key):
        return self.values.pop(key, None)

    async def setex(self, key, _ttl, value):
        self.values[key] = value

    async def eval(self, _script, _numkeys, key, value):
        # Mirrors services.token_revocation._RAISE_MARKER: the marker only moves forward.
        try:
            current = int(self.values.get(key))
        except (TypeError, ValueError):
            current = 0
        if int(value) > current:
            self.values[key] = value

    async def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)


@pytest.fixture
def env(monkeypatch):
    redis = _Redis()
    for module in (deps, auth):
        monkeypatch.setattr(module, "get_redis", lambda: redis)
    monkeypatch.setattr("services.redis.get_redis", lambda: redis)
    monkeypatch.setattr(auth, "emit_security_event", AsyncMock())
    monkeypatch.setattr(admin_users, "emit_security_event", AsyncMock())
    user = User(id=uuid.uuid4(), email="victim@example.test", username="victim", name="V", role=UserRole.user)
    user.auth_provider = "local"
    user.created_at = datetime.now(UTC)
    user.set_password(PASSWORD)
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: user))
    db.commit = AsyncMock()
    return SimpleNamespace(user=user, db=db, redis=redis, monkeypatch=monkeypatch)


@contextmanager
def _issued_a_minute_ago(monkeypatch):
    """Tokens created inside this block look like they were issued before the revocation."""
    earlier = datetime.now(UTC) - timedelta(minutes=1)
    with monkeypatch.context() as m:
        m.setattr(jwt_service, "datetime", SimpleNamespace(now=lambda _tz=None: earlier))
        yield


def _request(token=""):
    return SimpleNamespace(headers={"authorization": f"Bearer {token}"}, client=SimpleNamespace(host="1.2.3.4"))


async def _works(env, access_token):
    return await deps._authenticate_via_jwt(access_token, env.db) is not None


async def _refreshes(env, refresh_token):
    try:
        await auth.refresh_token.__wrapped__(_request(), RefreshRequest(refresh_token=refresh_token), env.db)
    except Exception:
        return False
    return True


def test_is_revoked_rules():
    now = time.time_ns() // 1_000  # markers and issue times are epoch microseconds
    assert not is_revoked(None, now - 100)
    assert is_revoked(str(now), now - 1)  # issued 1µs before the revocation
    assert not is_revoked(str(now), now)
    assert not is_revoked(str(now), now + 5)
    assert is_revoked("1", now + 5)  # legacy marker revokes everything
    assert is_revoked("garbage", now + 5)


def test_issue_time_uses_microsecond_claim_and_falls_back_to_iat():
    assert token_issued_us({"iat": 1_700_000_000, "iat_us": 1_700_000_000_123_456}) == 1_700_000_000_123_456
    assert token_issued_us({"iat": 1_700_000_000}) == 1_700_000_000_000_000  # tokens issued before this change
    assert token_issued_us({}) is None


@pytest.mark.asyncio
async def test_token_issued_moments_before_logout_in_the_same_second_is_revoked(env):
    hooks_token = (await auth.create_hooks_token(env.user))["access_token"]
    await revoke_user_tokens(env.redis, env.user.id)
    assert not await _works(env, hooks_token)


@pytest.mark.asyncio
async def test_marker_never_moves_backwards(env):
    key = f"revoked_user:{env.user.id}"
    later = str(time.time_ns() // 1_000 + 3_600_000_000)  # written by a replica whose clock runs ahead
    env.redis.values[key] = later
    await revoke_user_tokens(env.redis, env.user.id)
    assert env.redis.values[key] == later


@pytest.mark.asyncio
async def test_refresh_racing_a_logout_hands_out_no_tokens(env):
    _, refresh, _ = await auth._issue_tokens(env.user)

    async def lookup_then_logout(_stmt):
        await revoke_user_tokens(env.redis, env.user.id)  # logout lands during the user lookup
        return MagicMock(scalar_one_or_none=lambda: env.user)

    env.db.execute = AsyncMock(side_effect=lookup_then_logout)
    assert not await _refreshes(env, refresh)
    assert not [key for key in env.redis.values if key.startswith("refresh_jti:")]


@pytest.mark.asyncio
async def test_logout_everywhere_survives_the_next_login(env):
    with _issued_a_minute_ago(env.monkeypatch):
        laptop, _, _ = await auth._issue_tokens(env.user)
        stolen_access, stolen_refresh, _ = await auth._issue_tokens(env.user)
        hooks_token = (await auth.create_hooks_token(env.user))["access_token"]

    await auth.logout(_request(laptop), LogoutRequest(), current_user=env.user)
    assert not await _works(env, stolen_access)
    assert not await _works(env, hooks_token)
    assert not await _refreshes(env, stolen_refresh)

    new_access, new_refresh, _ = await auth._issue_tokens(env.user)  # log in on a new laptop
    assert await _works(env, new_access)
    assert await _refreshes(env, new_refresh)
    assert not await _works(env, stolen_access)
    assert not await _works(env, hooks_token)


@pytest.mark.asyncio
async def test_legacy_marker_blocks_until_login_then_only_new_tokens_work(env):
    with _issued_a_minute_ago(env.monkeypatch):
        old_access, _, _ = await auth._issue_tokens(env.user)
    env.redis.values[f"revoked_user:{env.user.id}"] = "1"  # written by a server before this change
    assert not await _works(env, old_access)

    new_access, _, _ = await auth._issue_tokens(env.user)
    assert await _works(env, new_access)
    assert not await _works(env, old_access)


@pytest.mark.asyncio
async def test_password_change_signs_out_other_sessions_but_keeps_this_one(env):
    with _issued_a_minute_ago(env.monkeypatch):
        mine, _, _ = await auth._issue_tokens(env.user)
        stolen_access, stolen_refresh, _ = await auth._issue_tokens(env.user)

    changed = await auth.change_password.__wrapped__(
        _request(mine),
        ChangePasswordRequest(current_password=PASSWORD, new_password="N3wPassw0rd!xyzQ"),
        db=env.db,
        current_user=env.user,
    )

    session = await auth.exchange_code(CodeExchangeRequest(code=changed["exchange_code"]), env.db)

    assert not await _works(env, stolen_access)
    assert not await _refreshes(env, stolen_refresh)
    assert await _works(env, session.access_token)
    assert await _refreshes(env, session.refresh_token)


@pytest.mark.asyncio
async def test_admin_password_reset_signs_out_every_session(env):
    with _issued_a_minute_ago(env.monkeypatch):
        stolen_access, stolen_refresh, _ = await auth._issue_tokens(env.user)
    admin = User(id=uuid.uuid4(), email="admin@example.test", username="admin", name="A", role=UserRole.admin)

    await admin_users.reset_user_password(
        env.user.id, AdminResetPasswordRequest(new_password="Adm1nSet!Passw0rd"), db=env.db, current_user=admin
    )

    assert not await _works(env, stolen_access)
    assert not await _refreshes(env, stolen_refresh)
