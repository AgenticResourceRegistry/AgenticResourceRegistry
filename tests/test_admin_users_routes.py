# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Focused coverage for deployment-wide admin user management routes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

import api.deps as deps_module
from api.deps import get_current_user, get_db, require_password_auth
from api.routes.admin import users
from models.user import User, UserRole
from schemas.admin import (
    AdminResetPasswordRequest,
    UserCreateRequest,
    UserDepartmentUpdate,
    UserRoleUpdate,
)
from services.security_events import EventType, Severity

ADMIN_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
TARGET_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
CREATED_AT = datetime(2026, 5, 4, 3, 2, 1, tzinfo=UTC)


class _Rows:
    def __init__(self, values):
        self._values = list(values)

    def all(self):
        return list(self._values)


class _Result:
    def __init__(self, *, one=None, values=()):
        self._one = one
        self._values = list(values)

    def scalar_one_or_none(self):
        return self._one

    def scalars(self):
        return _Rows(self._values)


def _one(value):
    return _Result(one=value)


def _many(values):
    return _Result(values=values)


def _db(*results):
    database = MagicMock()
    database.execute = AsyncMock(side_effect=list(results)) if results else AsyncMock()
    database.scalar = AsyncMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()
    database.refresh = AsyncMock()
    database.delete = AsyncMock()
    database.add = MagicMock()
    return database


def _actor(role: UserRole = UserRole.admin, *, user_id: uuid.UUID = ADMIN_ID):
    return SimpleNamespace(
        id=user_id,
        email="admin@example.test",
        username="admin",
        name="Deployment Admin",
        role=role,
        auth_provider="local",
        department=None,
        created_at=CREATED_AT,
    )


def _target(**overrides):
    values = {
        "id": TARGET_ID,
        "email": "member@example.test",
        "username": "member",
        "name": "Team Member",
        "role": UserRole.user,
        "auth_provider": "local",
        "department": "Engineering",
        "created_at": CREATED_AT,
    }
    values.update(overrides)
    target = SimpleNamespace(**values)
    target.set_password = MagicMock()
    return target


def _assert_statement(actual, expected):
    assert actual.compare(expected)
    assert actual.compile().params == expected.compile().params


def _assert_no_write(database):
    database.add.assert_not_called()
    database.commit.assert_not_awaited()
    database.rollback.assert_not_awaited()
    database.refresh.assert_not_awaited()
    database.delete.assert_not_awaited()


def _override_db(app: FastAPI, database):
    async def dependency():
        yield database

    app.dependency_overrides[get_db] = dependency


@pytest.fixture
def route_app():
    app = FastAPI()
    app.include_router(users.router)
    return app


@pytest.mark.asyncio
async def test_list_users_orders_by_creation_time_and_serializes_exact_response():
    first = _target()
    second = _target(
        id=uuid.UUID("33333333-3333-4333-8333-333333333333"),
        email="reviewer@example.test",
        username=None,
        name="Registry Reviewer",
        role=UserRole.reviewer,
        department=None,
        created_at=None,
    )
    database = _db(_many([first, second]))

    response = await users.list_users(database, _actor())

    assert [item.model_dump() for item in response] == [
        {
            "id": TARGET_ID,
            "email": "member@example.test",
            "username": "member",
            "name": "Team Member",
            "role": "user",
            "department": "Engineering",
            "created_at": CREATED_AT,
        },
        {
            "id": second.id,
            "email": "reviewer@example.test",
            "username": None,
            "name": "Registry Reviewer",
            "role": "reviewer",
            "department": None,
            "created_at": None,
        },
    ]
    _assert_statement(database.execute.await_args.args[0], select(User).order_by(User.created_at.desc()))
    _assert_no_write(database)


@pytest.mark.asyncio
async def test_list_users_database_failure_is_loud_and_read_only():
    database = _db()
    database.execute.side_effect = RuntimeError("user store unavailable")

    with pytest.raises(RuntimeError, match="user store unavailable"):
        await users.list_users(database, _actor())

    _assert_no_write(database)


@pytest.mark.asyncio
async def test_admin_user_routes_all_enforce_the_admin_role(route_app, monkeypatch):
    database = _db()
    _override_db(route_app, database)
    route_app.dependency_overrides[get_current_user] = lambda: _actor(UserRole.user)
    route_app.dependency_overrides[require_password_auth] = lambda: None
    denied = AsyncMock()
    monkeypatch.setattr(deps_module, "emit_security_event", denied)

    requests = [
        ("GET", "/api/v1/admin/users", None),
        (
            "POST",
            "/api/v1/admin/users",
            {"email": "new@example.test", "name": "New User", "role": "user", "password": "Chosen1!"},
        ),
        ("PUT", f"/api/v1/admin/users/{TARGET_ID}/role", {"role": "reviewer"}),
        ("PUT", f"/api/v1/admin/users/{TARGET_ID}/department", {"department": "Platform"}),
        (
            "POST",
            "/api/v1/admin/users/bulk-department",
            {"entries": [{"email": "member@example.test", "department": "Platform"}]},
        ),
        ("PUT", f"/api/v1/admin/users/{TARGET_ID}/password", {"new_password": "Chosen2!"}),
        ("DELETE", f"/api/v1/admin/users/{TARGET_ID}", None),
    ]

    async with AsyncClient(transport=ASGITransport(app=route_app), base_url="http://test") as client:
        responses = [await client.request(method, path, json=body) for method, path, body in requests]

    assert [(response.status_code, response.json()) for response in responses] == [
        (403, {"detail": "Insufficient permissions"})
    ] * len(requests)
    assert denied.await_count == len(requests)
    assert all(event.args[0].event_type is EventType.PERMISSION_DENIED for event in denied.await_args_list)
    database.execute.assert_not_awaited()
    _assert_no_write(database)


@pytest.mark.asyncio
async def test_list_users_http_authentication_and_privileged_roles(route_app, monkeypatch):
    database = _db()
    database.execute.return_value = _many([])
    _override_db(route_app, database)
    denied = AsyncMock()
    monkeypatch.setattr(deps_module, "emit_security_event", denied)

    async with AsyncClient(transport=ASGITransport(app=route_app), base_url="http://test") as client:
        unauthenticated = await client.get("/api/v1/admin/users")
        route_app.dependency_overrides[get_current_user] = lambda: _actor(UserRole.reviewer)
        reviewer = await client.get("/api/v1/admin/users")
        route_app.dependency_overrides[get_current_user] = lambda: _actor(UserRole.admin)
        admin = await client.get("/api/v1/admin/users")
        route_app.dependency_overrides[get_current_user] = lambda: _actor(UserRole.super_admin)
        super_admin = await client.get("/api/v1/admin/users")

    assert (unauthenticated.status_code, unauthenticated.json()) == (401, {"detail": "Missing credentials"})
    assert (reviewer.status_code, reviewer.json()) == (403, {"detail": "Insufficient permissions"})
    assert (admin.status_code, admin.json()) == (200, [])
    assert (super_admin.status_code, super_admin.json()) == (200, [])
    event = denied.await_args.args[0]
    assert event.actor_role == "reviewer"
    assert event.detail == "Required role: admin, has: reviewer"
    assert database.execute.await_count == 2


@pytest.mark.asyncio
async def test_password_routes_are_blocked_in_sso_only_mode(route_app, monkeypatch):
    database = _db()
    _override_db(route_app, database)
    route_app.dependency_overrides[get_current_user] = lambda: _actor()
    get_bool = AsyncMock(return_value=True)
    monkeypatch.setattr("services.dynamic_settings.get_bool", get_bool)

    async with AsyncClient(transport=ASGITransport(app=route_app), base_url="http://test") as client:
        created = await client.post(
            "/api/v1/admin/users",
            json={"email": "new@example.test", "name": "New User", "role": "user"},
        )
        reset = await client.put(
            f"/api/v1/admin/users/{TARGET_ID}/password",
            json={"generate": True},
        )

    expected = (403, {"detail": "Password authentication is disabled (SSO-only mode)"})
    assert (created.status_code, created.json()) == expected
    assert (reset.status_code, reset.json()) == expected
    assert get_bool.await_args_list == [call("deployment.sso_only"), call("deployment.sso_only")]
    database.execute.assert_not_awaited()
    _assert_no_write(database)


@pytest.mark.asyncio
async def test_create_user_with_supplied_password_orders_mutation_commit_refresh_and_event(monkeypatch):
    order = []
    database = _db()

    async def execute(statement):
        order.append("lookup")
        _assert_statement(statement, select(User).where(User.email == "alice@example.test"))
        return _one(None)

    async def generate(email, session, *, explicit=None):
        order.append("username")
        assert (email, session, explicit) == ("alice@example.test", database, "alice")
        return "alice"

    def set_password(password):
        order.append("password")
        assert password == "Chosen1!"

    def add(user):
        order.append("add")
        assert user.id is None

    async def commit():
        order.append("commit")

    async def refresh(user):
        order.append("refresh")
        user.id = TARGET_ID

    async def emit(event):
        order.append("event")

    database.execute.side_effect = execute
    database.add.side_effect = add
    database.commit.side_effect = commit
    database.refresh.side_effect = refresh
    monkeypatch.setattr(users, "generate_unique_username", AsyncMock(side_effect=generate))
    generated_password = AsyncMock(side_effect=AssertionError("provided passwords must not be replaced"))
    monkeypatch.setattr(users, "_generate_unique_password", generated_password)
    monkeypatch.setattr(users.User, "set_password", MagicMock(side_effect=set_password))
    event_boundary = AsyncMock(side_effect=emit)
    monkeypatch.setattr(users, "emit_security_event", event_boundary)
    request = UserCreateRequest(
        email=" Alice@Example.TEST ",
        name="Alice Example",
        username="alice",
        role="admin",
        password="Chosen1!",
    )

    response = await users.create_user(request, database, _actor())

    assert order == ["lookup", "username", "password", "add", "commit", "refresh", "event"]
    assert response.model_dump() == {
        "id": TARGET_ID,
        "email": "alice@example.test",
        "username": "alice",
        "name": "Alice Example",
        "role": "admin",
        "password": "Chosen1!",
    }
    generated_password.assert_not_awaited()
    database.rollback.assert_not_awaited()
    event = event_boundary.await_args.args[0]
    assert event.event_type is EventType.USER_CREATED
    assert event.severity is Severity.INFO
    assert event.outcome == "success"
    assert event.actor_id == str(ADMIN_ID)
    assert event.actor_email == "admin@example.test"
    assert event.actor_role == "admin"
    assert event.target_id == str(TARGET_ID)
    assert event.target_type == "user"
    assert event.detail == "Created user alice@example.test with role admin"


@pytest.mark.asyncio
async def test_super_admin_can_create_super_admin_with_generated_password(monkeypatch):
    database = _db(_one(None))

    async def refresh(user):
        user.id = TARGET_ID

    database.refresh.side_effect = refresh
    generate_password = AsyncMock(return_value="Generated1!")
    generate_username = AsyncMock(return_value="root-two")
    set_password = MagicMock()
    emit = AsyncMock()
    monkeypatch.setattr(users, "_generate_unique_password", generate_password)
    monkeypatch.setattr(users, "generate_unique_username", generate_username)
    monkeypatch.setattr(users.User, "set_password", set_password)
    monkeypatch.setattr(users, "emit_security_event", emit)
    request = UserCreateRequest(email="root@example.test", name="Root Two", role="super_admin")

    response = await users.create_user(request, database, _actor(UserRole.super_admin))

    generate_password.assert_awaited_once_with(database)
    generate_username.assert_awaited_once_with("root@example.test", database, explicit=None)
    set_password.assert_called_once_with("Generated1!")
    assert response.password == "Generated1!"
    assert response.role == "super_admin"
    database.commit.assert_awaited_once_with()
    emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_user_duplicate_email_stops_before_role_and_service_boundaries(monkeypatch):
    existing = _target()
    database = _db(_one(existing))
    username = AsyncMock()
    password = AsyncMock()
    emit = AsyncMock()
    monkeypatch.setattr(users, "generate_unique_username", username)
    monkeypatch.setattr(users, "_generate_unique_password", password)
    monkeypatch.setattr(users, "emit_security_event", emit)

    with pytest.raises(HTTPException) as error:
        await users.create_user(
            UserCreateRequest(email=" MEMBER@example.test ", name="Duplicate", role="not-a-role"),
            database,
            _actor(),
        )

    assert (error.value.status_code, error.value.detail) == (409, "Email already registered")
    _assert_statement(database.execute.await_args.args[0], select(User).where(User.email == "member@example.test"))
    username.assert_not_awaited()
    password.assert_not_awaited()
    emit.assert_not_awaited()
    _assert_no_write(database)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "actor_role", "status", "detail"),
    [
        ("operator", UserRole.admin, 422, "Invalid role. Must be one of: ['super_admin', 'admin', 'reviewer', 'user']"),
        ("super_admin", UserRole.admin, 403, "Cannot assign a role higher than your own"),
    ],
)
async def test_create_user_rejects_invalid_or_higher_roles_without_mutation(
    monkeypatch, role, actor_role, status, detail
):
    database = _db(_one(None))
    username = AsyncMock()
    password = AsyncMock()
    monkeypatch.setattr(users, "generate_unique_username", username)
    monkeypatch.setattr(users, "_generate_unique_password", password)

    with pytest.raises(HTTPException) as error:
        await users.create_user(
            UserCreateRequest(email="new@example.test", name="New User", role=role),
            database,
            _actor(actor_role),
        )

    assert (error.value.status_code, error.value.detail) == (status, detail)
    username.assert_not_awaited()
    password.assert_not_awaited()
    _assert_no_write(database)


@pytest.mark.asyncio
async def test_create_user_maps_cross_handle_username_conflict_to_exact_409(monkeypatch):
    database = _db(_one(None))
    generate = AsyncMock(side_effect=ValueError("Handle 'platform' is already taken"))
    monkeypatch.setattr(users, "generate_unique_username", generate)
    monkeypatch.setattr(users, "emit_security_event", AsyncMock())

    with pytest.raises(HTTPException) as error:
        await users.create_user(
            UserCreateRequest(
                email="new@example.test",
                name="New User",
                username="platform",
                role="user",
                password="Chosen1!",
            ),
            database,
            _actor(),
        )

    assert (error.value.status_code, error.value.detail) == (409, "Handle 'platform' is already taken")
    generate.assert_awaited_once_with("new@example.test", database, explicit="platform")
    _assert_no_write(database)


@pytest.mark.asyncio
async def test_create_user_integrity_conflict_rolls_back_and_emits_no_success(monkeypatch):
    database = _db(_one(None))
    database.commit.side_effect = IntegrityError("insert user", {}, RuntimeError("duplicate username"))
    monkeypatch.setattr(users, "generate_unique_username", AsyncMock(return_value="new-user"))
    monkeypatch.setattr(users.User, "set_password", MagicMock())
    emit = AsyncMock()
    monkeypatch.setattr(users, "emit_security_event", emit)

    with pytest.raises(HTTPException) as error:
        await users.create_user(
            UserCreateRequest(
                email="new@example.test",
                name="New User",
                role="user",
                password="Chosen1!",
            ),
            database,
            _actor(),
        )

    assert (error.value.status_code, error.value.detail) == (409, "Email already registered")
    database.add.assert_called_once()
    database.rollback.assert_awaited_once_with()
    database.refresh.assert_not_awaited()
    emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_user_unhandled_commit_failure_is_loud_without_false_success(monkeypatch):
    database = _db(_one(None))
    database.commit.side_effect = RuntimeError("commit unavailable")
    monkeypatch.setattr(users, "generate_unique_username", AsyncMock(return_value="new-user"))
    monkeypatch.setattr(users.User, "set_password", MagicMock())
    emit = AsyncMock()
    monkeypatch.setattr(users, "emit_security_event", emit)

    with pytest.raises(RuntimeError, match="commit unavailable"):
        await users.create_user(
            UserCreateRequest(
                email="new@example.test",
                name="New User",
                role="user",
                password="Chosen1!",
            ),
            database,
            _actor(),
        )

    database.rollback.assert_not_awaited()
    database.refresh.assert_not_awaited()
    emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_user_event_failure_occurs_only_after_persistence(monkeypatch):
    database = _db(_one(None))

    async def refresh(user):
        user.id = TARGET_ID

    database.refresh.side_effect = refresh
    monkeypatch.setattr(users, "generate_unique_username", AsyncMock(return_value="new-user"))
    monkeypatch.setattr(users.User, "set_password", MagicMock())
    monkeypatch.setattr(users, "emit_security_event", AsyncMock(side_effect=RuntimeError("event sink unavailable")))

    with pytest.raises(RuntimeError, match="event sink unavailable"):
        await users.create_user(
            UserCreateRequest(
                email="new@example.test",
                name="New User",
                role="user",
                password="Chosen1!",
            ),
            database,
            _actor(),
        )

    database.commit.assert_awaited_once_with()
    database.refresh.assert_awaited_once()
    database.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_role_commits_refreshes_and_emits_exact_event(monkeypatch):
    target = _target(role=UserRole.reviewer)
    database = _db(_one(target))
    emit = AsyncMock()
    monkeypatch.setattr(users, "emit_security_event", emit)

    response = await users.update_user_role(
        TARGET_ID,
        UserRoleUpdate(role="admin"),
        database,
        _actor(UserRole.super_admin),
    )

    _assert_statement(database.execute.await_args.args[0], select(User).where(User.id == TARGET_ID))
    assert target.role is UserRole.admin
    database.commit.assert_awaited_once_with()
    database.refresh.assert_awaited_once_with(target)
    event = emit.await_args.args[0]
    assert event.event_type is EventType.ROLE_CHANGED
    assert event.severity is Severity.WARNING
    assert event.outcome == "success"
    assert event.actor_role == "super_admin"
    assert event.target_id == str(TARGET_ID)
    assert event.detail == "Role changed from reviewer to admin"
    assert response.model_dump()["role"] == "admin"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("request_role", "user_id", "actor_role", "status", "detail", "query_count"),
    [
        (
            "operator",
            TARGET_ID,
            UserRole.admin,
            422,
            "Invalid role. Must be one of: ['super_admin', 'admin', 'reviewer', 'user']",
            0,
        ),
        ("super_admin", TARGET_ID, UserRole.admin, 403, "Cannot assign a role higher than your own", 0),
        ("reviewer", ADMIN_ID, UserRole.admin, 400, "Cannot change your own role", 0),
        ("user", TARGET_ID, UserRole.admin, 404, "User not found", 1),
    ],
)
async def test_update_role_validation_failures_are_exact_and_side_effect_free(
    monkeypatch, request_role, user_id, actor_role, status, detail, query_count
):
    actor = _actor(actor_role)
    database = _db(_one(None)) if query_count else _db()
    emit = AsyncMock()
    monkeypatch.setattr(users, "emit_security_event", emit)

    with pytest.raises(HTTPException) as error:
        await users.update_user_role(user_id, UserRoleUpdate(role=request_role), database, actor)

    assert (error.value.status_code, error.value.detail) == (status, detail)
    assert database.execute.await_count == query_count
    emit.assert_not_awaited()
    _assert_no_write(database)


@pytest.mark.asyncio
async def test_update_role_allows_self_noop_and_records_current_contract(monkeypatch):
    actor = _actor()
    target = _target(
        id=actor.id,
        email=actor.email,
        username=actor.username,
        name=actor.name,
        role=actor.role,
        department=None,
    )
    database = _db(_one(target))
    emit = AsyncMock()
    monkeypatch.setattr(users, "emit_security_event", emit)

    response = await users.update_user_role(actor.id, UserRoleUpdate(role="admin"), database, actor)

    assert response.role == "admin"
    database.commit.assert_awaited_once_with()
    assert emit.await_args.args[0].detail == "Role changed from admin to admin"


@pytest.mark.asyncio
async def test_update_role_commit_failure_stops_refresh_and_event(monkeypatch):
    target = _target(role=UserRole.user)
    database = _db(_one(target))
    database.commit.side_effect = RuntimeError("role commit failed")
    emit = AsyncMock()
    monkeypatch.setattr(users, "emit_security_event", emit)

    with pytest.raises(RuntimeError, match="role commit failed"):
        await users.update_user_role(TARGET_ID, UserRoleUpdate(role="reviewer"), database, _actor())

    assert target.role is UserRole.reviewer
    database.refresh.assert_not_awaited()
    database.rollback.assert_not_awaited()
    emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_department_persists_nullable_value_and_returns_exact_user():
    target = _target()
    database = _db(_one(target))

    response = await users.update_user_department(
        TARGET_ID,
        UserDepartmentUpdate(department=None),
        database,
        _actor(),
    )

    _assert_statement(database.execute.await_args.args[0], select(User).where(User.id == TARGET_ID))
    assert target.department is None
    database.commit.assert_awaited_once_with()
    database.refresh.assert_awaited_once_with(target)
    assert response.model_dump()["department"] is None


@pytest.mark.asyncio
async def test_update_department_missing_user_and_commit_failure_contracts():
    missing_db = _db(_one(None))
    with pytest.raises(HTTPException) as error:
        await users.update_user_department(
            TARGET_ID,
            UserDepartmentUpdate(department="Platform"),
            missing_db,
            _actor(),
        )
    assert (error.value.status_code, error.value.detail) == (404, "User not found")
    _assert_no_write(missing_db)

    target = _target()
    failed_db = _db(_one(target))
    failed_db.commit.side_effect = RuntimeError("department commit failed")
    with pytest.raises(RuntimeError, match="department commit failed"):
        await users.update_user_department(
            TARGET_ID,
            UserDepartmentUpdate(department="Platform"),
            failed_db,
            _actor(),
        )
    assert target.department == "Platform"
    failed_db.refresh.assert_not_awaited()
    failed_db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_bulk_departments_normalizes_lookups_strips_values_and_reports_original_missing_email():
    first = _target(email="alice@example.test", department=None)
    second = _target(
        id=uuid.UUID("33333333-3333-4333-8333-333333333333"),
        email="bob@example.test",
        department=None,
    )
    database = _db(_one(first), _one(None), _one(second))
    request = users.BulkDepartmentRequest(
        entries=[
            users.BulkDepartmentEntry(email=" Alice@Example.TEST ", department=" Platform "),
            users.BulkDepartmentEntry(email=" Missing@Example.TEST ", department=" Product "),
            users.BulkDepartmentEntry(email="BOB@example.test", department=" Security "),
        ]
    )

    response = await users.bulk_update_departments(request, database, _actor())

    assert response.model_dump() == {"updated": 2, "not_found": [" Missing@Example.TEST "]}
    assert first.department == "Platform"
    assert second.department == "Security"
    expected_emails = ["alice@example.test", "missing@example.test", "bob@example.test"]
    for executed, email in zip(database.execute.await_args_list, expected_emails, strict=True):
        _assert_statement(executed.args[0], select(User).where(User.email == email))
    database.commit.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_bulk_departments_empty_request_still_commits_once():
    database = _db()

    response = await users.bulk_update_departments(users.BulkDepartmentRequest(entries=[]), database, _actor())

    assert response.model_dump() == {"updated": 0, "not_found": []}
    database.execute.assert_not_awaited()
    database.commit.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_bulk_departments_query_and_commit_failures_are_loud():
    first = _target(department=None)
    query_failure = _db(_one(first))
    query_failure.execute.side_effect = [_one(first), RuntimeError("lookup failed")]
    request = users.BulkDepartmentRequest(
        entries=[
            users.BulkDepartmentEntry(email="member@example.test", department="Platform"),
            users.BulkDepartmentEntry(email="other@example.test", department="Product"),
        ]
    )

    with pytest.raises(RuntimeError, match="lookup failed"):
        await users.bulk_update_departments(request, query_failure, _actor())
    assert first.department == "Platform"
    query_failure.commit.assert_not_awaited()

    target = _target(department=None)
    commit_failure = _db(_one(target))
    commit_failure.commit.side_effect = RuntimeError("bulk commit failed")
    with pytest.raises(RuntimeError, match="bulk commit failed"):
        await users.bulk_update_departments(
            users.BulkDepartmentRequest(entries=[users.BulkDepartmentEntry(email=target.email, department="Platform")]),
            commit_failure,
            _actor(),
        )
    assert target.department == "Platform"
    commit_failure.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_generated_password_reset_orders_commit_gate_and_exact_security_event(monkeypatch):
    order = []
    target = _target()
    database = _db()

    async def execute(statement):
        order.append("lookup")
        _assert_statement(statement, select(User).where(User.id == TARGET_ID))
        return _one(target)

    async def generate(session):
        order.append("generate")
        assert session is database
        return "Temporary1!"

    def set_password(password):
        order.append("password")
        assert password == "Temporary1!"

    async def commit():
        order.append("commit")

    redis = SimpleNamespace(setex=AsyncMock(side_effect=lambda *_args: order.append("gate")))

    async def emit(_event):
        order.append("event")

    database.execute.side_effect = execute
    database.commit.side_effect = commit
    target.set_password.side_effect = set_password
    monkeypatch.setattr(users, "_generate_unique_password", AsyncMock(side_effect=generate))
    monkeypatch.setattr("services.redis.get_redis", lambda: redis)
    event_boundary = AsyncMock(side_effect=emit)
    monkeypatch.setattr(users, "emit_security_event", event_boundary)

    response = await users.reset_user_password(
        TARGET_ID,
        AdminResetPasswordRequest(generate=True),
        database,
        _actor(),
    )

    assert order == ["lookup", "generate", "password", "commit", "gate", "event"]
    assert response == {
        "message": "Password reset for member@example.test",
        "generated_password": "Temporary1!",
        "must_change_password": "true",
    }
    redis.setex.assert_awaited_once_with(f"must_change_password:{TARGET_ID}", 86400, "1")
    event = event_boundary.await_args.args[0]
    assert event.event_type is EventType.ADMIN_PASSWORD_RESET
    assert event.severity is Severity.WARNING
    assert event.outcome == "success"
    assert event.actor_id == str(ADMIN_ID)
    assert event.target_id == str(TARGET_ID)
    assert event.target_type == "user"
    assert event.detail == "Password reset for member@example.test"


@pytest.mark.asyncio
async def test_supplied_password_reset_skips_generation_but_still_sets_change_gate(monkeypatch):
    target = _target()
    database = _db(_one(target))
    generate = AsyncMock()
    redis = SimpleNamespace(setex=AsyncMock())
    monkeypatch.setattr(users, "_generate_unique_password", generate)
    monkeypatch.setattr("services.redis.get_redis", lambda: redis)
    monkeypatch.setattr(users, "emit_security_event", AsyncMock())

    response = await users.reset_user_password(
        TARGET_ID,
        AdminResetPasswordRequest(new_password="Chosen2!"),
        database,
        _actor(),
    )

    generate.assert_not_awaited()
    target.set_password.assert_called_once_with("Chosen2!")
    database.commit.assert_awaited_once_with()
    redis.setex.assert_awaited_once_with(f"must_change_password:{TARGET_ID}", 86400, "1")
    assert response == {"message": "Password reset for member@example.test"}


@pytest.mark.asyncio
async def test_generate_flag_takes_precedence_over_supplied_reset_password(monkeypatch):
    target = _target()
    database = _db(_one(target))
    monkeypatch.setattr(users, "_generate_unique_password", AsyncMock(return_value="Generated2!"))
    monkeypatch.setattr("services.redis.get_redis", lambda: SimpleNamespace(setex=AsyncMock()))
    monkeypatch.setattr(users, "emit_security_event", AsyncMock())

    response = await users.reset_user_password(
        TARGET_ID,
        AdminResetPasswordRequest(new_password="Ignored1!", generate=True),
        database,
        _actor(),
    )

    target.set_password.assert_called_once_with("Generated2!")
    assert response["generated_password"] == "Generated2!"


@pytest.mark.asyncio
async def test_reset_password_missing_user_or_password_has_no_mutation(monkeypatch):
    emit = AsyncMock()
    monkeypatch.setattr(users, "emit_security_event", emit)

    missing_db = _db(_one(None))
    with pytest.raises(HTTPException) as missing:
        await users.reset_user_password(
            TARGET_ID,
            AdminResetPasswordRequest(generate=True),
            missing_db,
            _actor(),
        )
    assert (missing.value.status_code, missing.value.detail) == (404, "User not found")
    _assert_no_write(missing_db)

    target = _target()
    empty_db = _db(_one(target))
    with pytest.raises(HTTPException) as empty:
        await users.reset_user_password(
            TARGET_ID,
            AdminResetPasswordRequest(),
            empty_db,
            _actor(),
        )
    assert (empty.value.status_code, empty.value.detail) == (422, "Provide new_password or set generate=true")
    target.set_password.assert_not_called()
    _assert_no_write(empty_db)
    emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_reset_password_commit_failure_prevents_gate_and_event(monkeypatch):
    target = _target()
    database = _db(_one(target))
    database.commit.side_effect = RuntimeError("password commit failed")
    redis = SimpleNamespace(setex=AsyncMock())
    emit = AsyncMock()
    monkeypatch.setattr("services.redis.get_redis", lambda: redis)
    monkeypatch.setattr(users, "emit_security_event", emit)

    with pytest.raises(RuntimeError, match="password commit failed"):
        await users.reset_user_password(
            TARGET_ID,
            AdminResetPasswordRequest(new_password="Chosen2!"),
            database,
            _actor(),
        )

    target.set_password.assert_called_once_with("Chosen2!")
    database.rollback.assert_not_awaited()
    redis.setex.assert_not_awaited()
    emit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RedisError("redis down"), RuntimeError("unexpected redis client failure")])
async def test_reset_password_swallows_password_gate_failures_but_still_emits(monkeypatch, failure):
    target = _target()
    database = _db(_one(target))
    redis = SimpleNamespace(setex=AsyncMock(side_effect=failure))
    emit = AsyncMock()
    monkeypatch.setattr("services.redis.get_redis", lambda: redis)
    monkeypatch.setattr(users, "emit_security_event", emit)

    response = await users.reset_user_password(
        TARGET_ID,
        AdminResetPasswordRequest(new_password="Chosen2!"),
        database,
        _actor(),
    )

    assert response == {"message": "Password reset for member@example.test"}
    database.commit.assert_awaited_once_with()
    emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_reset_password_event_failure_is_after_password_persistence(monkeypatch):
    target = _target()
    database = _db(_one(target))
    redis = SimpleNamespace(setex=AsyncMock())
    monkeypatch.setattr("services.redis.get_redis", lambda: redis)
    monkeypatch.setattr(users, "emit_security_event", AsyncMock(side_effect=RuntimeError("event failed")))

    with pytest.raises(RuntimeError, match="event failed"):
        await users.reset_user_password(
            TARGET_ID,
            AdminResetPasswordRequest(new_password="Chosen2!"),
            database,
            _actor(),
        )

    database.commit.assert_awaited_once_with()
    redis.setex.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_role", "admin_count"),
    [
        (UserRole.user, None),
        (UserRole.admin, 2),
        (UserRole.super_admin, None),
    ],
)
async def test_delete_user_success_orders_event_before_delete_and_commit(monkeypatch, target_role, admin_count):
    order = []
    target = _target(role=target_role)
    database = _db(_one(target))
    if target.role in (UserRole.admin, UserRole.super_admin):
        database.scalar.return_value = admin_count

    async def emit(_event):
        order.append("event")

    async def delete(deleted):
        order.append("delete")
        assert deleted is target

    async def commit():
        order.append("commit")

    event_boundary = AsyncMock(side_effect=emit)
    monkeypatch.setattr(users, "emit_security_event", event_boundary)
    database.delete.side_effect = delete
    database.commit.side_effect = commit

    assert await users.delete_user(TARGET_ID, database, _actor()) is None

    _assert_statement(database.execute.await_args.args[0], select(User).where(User.id == TARGET_ID))
    assert order == ["event", "delete", "commit"]
    if target.role in (UserRole.admin, UserRole.super_admin):
        expected = select(func.count()).select_from(User).where(User.role.in_([UserRole.admin, UserRole.super_admin]))
        _assert_statement(database.scalar.await_args.args[0], expected)
    else:
        database.scalar.assert_not_awaited()
    event = event_boundary.await_args.args[0]
    assert event.event_type is EventType.USER_DELETED
    assert event.severity is Severity.WARNING
    assert event.outcome == "success"
    assert event.actor_id == str(ADMIN_ID)
    assert event.target_id == str(TARGET_ID)
    assert event.target_type == "user"
    assert event.detail == "Deleted user member@example.test"


@pytest.mark.asyncio
async def test_delete_user_self_missing_and_last_admin_guards_are_no_mutation(monkeypatch):
    emit = AsyncMock()
    monkeypatch.setattr(users, "emit_security_event", emit)

    self_db = _db()
    with pytest.raises(HTTPException) as self_error:
        await users.delete_user(ADMIN_ID, self_db, _actor())
    assert (self_error.value.status_code, self_error.value.detail) == (400, "Cannot delete yourself")
    self_db.execute.assert_not_awaited()
    _assert_no_write(self_db)

    missing_db = _db(_one(None))
    with pytest.raises(HTTPException) as missing:
        await users.delete_user(TARGET_ID, missing_db, _actor())
    assert (missing.value.status_code, missing.value.detail) == (404, "User not found")
    _assert_no_write(missing_db)

    last = _target(role=UserRole.super_admin)
    last_db = _db(_one(last))
    last_db.scalar.return_value = 1
    with pytest.raises(HTTPException) as last_error:
        await users.delete_user(TARGET_ID, last_db, _actor())
    assert (last_error.value.status_code, last_error.value.detail) == (400, "Cannot delete the last admin")
    last_db.delete.assert_not_awaited()
    last_db.commit.assert_not_awaited()
    emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_admin_count_failure_is_loud_before_event_or_delete(monkeypatch):
    target = _target(role=UserRole.admin)
    database = _db(_one(target))
    database.scalar.side_effect = RuntimeError("count unavailable")
    emit = AsyncMock()
    monkeypatch.setattr(users, "emit_security_event", emit)

    with pytest.raises(RuntimeError, match="count unavailable"):
        await users.delete_user(TARGET_ID, database, _actor())

    emit.assert_not_awaited()
    database.delete.assert_not_awaited()
    database.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_event_failure_prevents_database_delete(monkeypatch):
    target = _target()
    database = _db(_one(target))
    monkeypatch.setattr(users, "emit_security_event", AsyncMock(side_effect=RuntimeError("event unavailable")))

    with pytest.raises(RuntimeError, match="event unavailable"):
        await users.delete_user(TARGET_ID, database, _actor())

    database.delete.assert_not_awaited()
    database.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_team_owner_constraint_failure_occurs_after_success_event(monkeypatch):
    target = _target()
    database = _db(_one(target))
    database.commit.side_effect = IntegrityError("delete user", {}, RuntimeError("teams.created_by foreign key"))
    emit = AsyncMock()
    monkeypatch.setattr(users, "emit_security_event", emit)

    with pytest.raises(IntegrityError):
        await users.delete_user(TARGET_ID, database, _actor())

    emit.assert_awaited_once()
    database.delete.assert_awaited_once_with(target)
    database.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_database_delete_failure_occurs_after_success_event(monkeypatch):
    target = _target()
    database = _db(_one(target))
    database.delete.side_effect = RuntimeError("delete unavailable")
    emit = AsyncMock()
    monkeypatch.setattr(users, "emit_security_event", emit)

    with pytest.raises(RuntimeError, match="delete unavailable"):
        await users.delete_user(TARGET_ID, database, _actor())

    emit.assert_awaited_once()
    database.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_http_success_is_an_empty_204(route_app, monkeypatch):
    target = _target()
    database = _db(_one(target))
    _override_db(route_app, database)
    route_app.dependency_overrides[get_current_user] = lambda: _actor()
    monkeypatch.setattr(users, "emit_security_event", AsyncMock())

    async with AsyncClient(transport=ASGITransport(app=route_app), base_url="http://test") as client:
        response = await client.delete(f"/api/v1/admin/users/{TARGET_ID}")

    assert response.status_code == 204
    assert response.content == b""
    database.delete.assert_awaited_once_with(target)
    database.commit.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_http_request_validation_is_exact_and_precedes_route_mutation(route_app):
    database = _db()
    _override_db(route_app, database)
    route_app.dependency_overrides[get_current_user] = lambda: _actor()
    route_app.dependency_overrides[require_password_auth] = lambda: None

    async with AsyncClient(transport=ASGITransport(app=route_app), base_url="http://test") as client:
        invalid_id = await client.put("/api/v1/admin/users/not-a-uuid/role", json={"role": "user"})
        missing_role = await client.put(f"/api/v1/admin/users/{TARGET_ID}/role", json={})
        invalid_username = await client.post(
            "/api/v1/admin/users",
            json={"email": "new@example.test", "name": "New User", "username": "not valid"},
        )

    assert invalid_id.status_code == 422
    assert invalid_id.json()["detail"][0]["loc"] == ["path", "user_id"]
    assert missing_role.status_code == 422
    assert missing_role.json()["detail"][0]["loc"] == ["body", "role"]
    assert invalid_username.status_code == 422
    assert invalid_username.json()["detail"][0]["loc"] == ["body", "username"]
    database.execute.assert_not_awaited()
    _assert_no_write(database)
