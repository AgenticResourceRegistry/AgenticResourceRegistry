# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""A deleted account's shell row can never authenticate or be found."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

USER_ID = uuid.UUID("11111111-2222-4333-8444-555555555555")


def _result(user):
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    return result


def _user(deleted: bool):
    return SimpleNamespace(
        id=USER_ID,
        role=None,
        auth_provider="deleted" if deleted else "local",
        deleted_at=datetime.now(UTC) if deleted else None,
    )


class _Redis:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.setex = AsyncMock(side_effect=lambda key, _ttl, value: self.values.__setitem__(key, value))
        self.delete = AsyncMock(side_effect=lambda key: self.values.pop(key, None))

    async def get(self, key):
        return self.values.get(key)


@pytest.mark.asyncio
@pytest.mark.parametrize("deleted", [False, True])
async def test_access_token_never_authenticates_a_deleted_shell(deleted):
    """Even if revocation never reached Redis, the row's deleted_at blocks it."""
    import api.deps as deps

    db = MagicMock()
    db.execute = AsyncMock(return_value=_result(_user(deleted)))
    with (
        patch.object(deps, "decode_access_token", return_value={"sub": str(USER_ID), "jti": "j"}),
        patch.object(deps, "get_redis", return_value=_Redis()),
        patch.object(deps.ds, "get_sync_bool", return_value=False),
    ):
        user = await deps._authenticate_via_jwt("token", db)
    assert (user is None) is deleted


@pytest.mark.asyncio
async def test_refresh_token_rejects_a_deleted_shell(monkeypatch):
    import api.routes.auth as auth
    from api.ratelimit import limiter

    monkeypatch.setattr(limiter, "enabled", False)
    from schemas.auth import RefreshRequest

    redis = _Redis({"refresh_jti:old": str(USER_ID)})
    monkeypatch.setattr(auth, "get_redis", lambda: redis)
    monkeypatch.setattr(auth, "decode_refresh_token", lambda _t: {"jti": "old", "sub": str(USER_ID)})
    issue = MagicMock()
    monkeypatch.setattr(auth, "create_access_token", issue)
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result(_user(deleted=True)))
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={}, query_params={}, session={})

    with pytest.raises(HTTPException) as raised:
        await auth.refresh_token(request, RefreshRequest(refresh_token="r"), db)
    assert raised.value.status_code == 401
    issue.assert_not_called()


@pytest.mark.asyncio
async def test_revocation_sets_account_wide_key_for_refresh_lifetime(monkeypatch):
    from services import user_deletion

    redis = _Redis({f"must_change_password:{USER_ID}": "1"})
    monkeypatch.setattr("services.redis.get_redis", lambda: redis)
    monkeypatch.setattr("services.dynamic_settings.get_sync_int", lambda _k, default: 7)

    assert await user_deletion.revoke_deleted_user_tokens(USER_ID) is True
    redis.setex.assert_awaited_once_with(f"revoked_user:{USER_ID}", 7 * 86400, "1")
    assert f"must_change_password:{USER_ID}" not in redis.values


@pytest.mark.asyncio
async def test_revocation_failure_is_reported_not_raised(monkeypatch):
    from services import user_deletion

    def broken():
        raise RuntimeError("redis down")

    monkeypatch.setattr("services.redis.get_redis", broken)
    assert await user_deletion.revoke_deleted_user_tokens(USER_ID) is False


@pytest.mark.asyncio
async def test_deleting_an_already_deleted_shell_is_refused():
    from services.user_deletion import delete_user_account

    with pytest.raises(ValueError, match="already deleted"):
        await delete_user_account(MagicMock(), _user(deleted=True))


def test_user_search_excludes_shells():
    from services.user_search import build_user_search_stmt

    assert "users.deleted_at IS NULL" in str(build_user_search_stmt("alice", 10))


def _shell():
    return SimpleNamespace(id=USER_ID, role=None, auth_provider="deleted", deleted_at=datetime.now(UTC))


def test_shell_is_recognised_without_the_marker_column():
    """The lockout does not rest on deleted_at alone (e.g. after a partial restore)."""
    from models.user import is_deleted_account, live_users

    assert is_deleted_account(SimpleNamespace(auth_provider="deleted", deleted_at=None))
    assert is_deleted_account(SimpleNamespace(auth_provider="local", deleted_at=datetime.now(UTC)))
    assert not is_deleted_account(SimpleNamespace(auth_provider="local", deleted_at=None))
    assert not is_deleted_account(MagicMock())  # test doubles are never mistaken for shells
    clause = str(live_users().compile(compile_kwargs={"literal_binds": True}))
    assert "users.deleted_at IS NULL" in clause and "users.auth_provider != 'deleted'" in clause


@pytest.mark.asyncio
@pytest.mark.parametrize("module", ["api.routes.auth", "api.routes.sso_saml"])
async def test_token_issuers_refuse_a_shell_and_keep_its_revocation(monkeypatch, module):
    """No sign-in path may mint tokens for, or clear revoked_user of, a deleted account."""
    import importlib

    routes = importlib.import_module(module)
    redis = _Redis({f"revoked_user:{USER_ID}": "1"})
    monkeypatch.setattr(routes, "get_redis", lambda: redis)
    minted = MagicMock(return_value=("t", 1))
    monkeypatch.setattr(routes, "create_access_token", minted)

    with pytest.raises(HTTPException) as raised:
        await routes._issue_tokens(_shell())
    assert raised.value.status_code == 401
    minted.assert_not_called()
    assert redis.values[f"revoked_user:{USER_ID}"] == "1"
    redis.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_oauth_code_exchange_never_returns_pre_issued_tokens_for_a_shell(monkeypatch):
    import json

    import api.routes.auth as auth
    from schemas.auth import CodeExchangeRequest

    redis = MagicMock()
    redis.getdel = AsyncMock(return_value=json.dumps({"access_token": "a", "user_id": str(USER_ID)}))
    monkeypatch.setattr(auth, "get_redis", lambda: redis)
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result(_shell()))

    with pytest.raises(HTTPException) as raised:
        await auth.exchange_code(CodeExchangeRequest(code="c"), db)
    assert raised.value.status_code == 400


@pytest.mark.asyncio
async def test_device_approval_made_before_deletion_never_yields_tokens(monkeypatch):
    import json

    import api.routes.device_auth as device
    from api.ratelimit import limiter
    from schemas.auth import DeviceTokenRequest

    monkeypatch.setattr(limiter, "enabled", False)
    redis = MagicMock()
    redis.get = AsyncMock(return_value=json.dumps({"status": "approved", "user_id": str(USER_ID)}))
    monkeypatch.setattr(device, "get_redis", lambda: redis)
    issue = AsyncMock()
    monkeypatch.setattr(device, "_issue_tokens", issue)
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result(_shell()))
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={}, query_params={}, session={})

    response = await device.device_token(
        request, DeviceTokenRequest(device_code="d", grant_type=device._DEVICE_GRANT_TYPE), db
    )
    assert response.status_code == 400
    assert json.loads(response.body) == {"error": "expired_token"}
    issue.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("resolver", ["co_authors", "teams"])
async def test_id_based_target_lookups_exclude_shells(resolver):
    """Ownership transfer and team membership by UUID cannot reach a deleted account."""
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result(None))
    if resolver == "co_authors":
        from api.routes.co_authors import _resolve_target_user

        with pytest.raises(HTTPException) as raised:
            await _resolve_target_user(db, user_id=str(USER_ID))
    else:
        from api.routes.teams import _resolve_member
        from schemas.team import TeamMemberUpsertRequest

        with pytest.raises(HTTPException) as raised:
            await _resolve_member(db, TeamMemberUpsertRequest(user_id=USER_ID))
    assert raised.value.status_code == 404
    sql = str(db.execute.await_args.args[0].compile(compile_kwargs={"literal_binds": True}))
    assert "users.deleted_at IS NULL" in sql and "users.auth_provider != 'deleted'" in sql


@pytest.mark.asyncio
async def test_component_report_job_refuses_a_deleted_requester():
    from services.insights.batch import _authorize_component_report_job

    db = MagicMock()
    db.scalar = AsyncMock(return_value=_shell())
    with pytest.raises(ValueError, match="no longer available"):
        await _authorize_component_report_job(db, SimpleNamespace(triggered_by=USER_ID))


@pytest.mark.asyncio
async def test_refresh_rejects_scim_deactivated_account_and_consumes_its_token(monkeypatch):
    """Requests already refuse a deactivated account; refresh must not mint tokens for it."""
    import api.routes.auth as auth
    from api.ratelimit import limiter
    from schemas.auth import RefreshRequest

    monkeypatch.setattr(limiter, "enabled", False)
    redis = _Redis({"refresh_jti:old": str(USER_ID)})
    monkeypatch.setattr(auth, "get_redis", lambda: redis)
    monkeypatch.setattr(auth, "decode_refresh_token", lambda _t: {"jti": "old", "sub": str(USER_ID)})
    issue = MagicMock()
    monkeypatch.setattr(auth, "create_access_token", issue)
    monkeypatch.setattr(auth, "create_refresh_token", issue)
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=_result(SimpleNamespace(id=USER_ID, role=None, auth_provider="deactivated", deleted_at=None))
    )
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={}, query_params={}, session={})

    with pytest.raises(HTTPException) as raised:
        await auth.refresh_token(request, RefreshRequest(refresh_token="r"), db)
    assert (raised.value.status_code, raised.value.detail) == (401, "Account deactivated")
    issue.assert_not_called()
    assert "refresh_jti:old" not in redis.values  # the presented token cannot be retried
