# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Focused coverage for the SCIM 2.0 provisioning routes."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from api.deps import get_db
from api.routes import scim
from models.scim_token import ScimToken
from models.user import User, UserRole, live_users
from services.events import UserCreated, UserDeleted
from services.scim_service import (
    SCIM_ERROR_SCHEMA,
    SCIM_LIST_SCHEMA,
    SCIM_PATCH_SCHEMA,
    SCIM_USER_SCHEMA,
    format_scim_error,
    format_scim_list,
    format_scim_user,
    hash_scim_token,
    parse_scim_user,
)
from services.security_events import EventType, Severity

USER_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
OTHER_USER_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
TOKEN_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
CREATED_AT = datetime(2026, 2, 3, 4, 5, 6, tzinfo=UTC)
BASE_URL = "https://scim.example.test/api/v1/scim"
SCIM_MEDIA_TYPE = "application/scim+json"


def _one(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _scalar(value):
    result = MagicMock()
    result.scalar.return_value = value
    return result


def _many(values):
    result = MagicMock()
    result.scalars.return_value.all.return_value = list(values)
    return result


def _db(*results):
    db = MagicMock()
    db.execute = AsyncMock(side_effect=list(results)) if results else AsyncMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.delete = AsyncMock()
    db.refresh = AsyncMock()
    db.add = MagicMock()
    return db


def _user(**overrides):
    values = {
        "id": USER_ID,
        "email": "alice@example.test",
        "username": "alice",
        "name": "Alice Example",
        "role": UserRole.user,
        "password_hash": "stored-password-hash",
        "auth_provider": "scim",
        "created_at": CREATED_AT,
        "deleted_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _resource(user, *, base_url: str = BASE_URL):
    name_parts = (user.name or "").split(" ", 1)
    given = name_parts[0] if name_parts else ""
    family = name_parts[1] if len(name_parts) > 1 else ""
    user_id = str(user.id)
    return {
        "schemas": [SCIM_USER_SCHEMA],
        "id": user_id,
        "userName": user.email,
        "name": {
            "givenName": given,
            "familyName": family,
            "formatted": user.name or "",
        },
        "displayName": user.name or "",
        "emails": [{"value": user.email, "primary": True, "type": "work"}],
        "active": user.auth_provider != "deactivated",
        "meta": {
            "resourceType": "User",
            "created": user.created_at.isoformat() if user.created_at else "",
            "location": f"{base_url}/Users/{user_id}" if base_url else "",
        },
    }


def _assert_statement(actual, expected):
    assert actual.compare(expected)
    assert actual.compile().params == expected.compile().params


def _assert_scim(response, status: int, body: dict):
    assert response.status_code == status
    assert response.headers["content-type"] == SCIM_MEDIA_TYPE
    assert response.json() == body


def _assert_no_write(db):
    db.add.assert_not_called()
    db.flush.assert_not_awaited()
    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()
    db.delete.assert_not_awaited()


def _bind_db(app: FastAPI, db, *, authenticated: bool = True):
    async def db_override():
        yield db

    app.dependency_overrides[get_db] = db_override
    if authenticated:

        async def token_override():
            return SimpleNamespace(id=TOKEN_ID, active=True)

        app.dependency_overrides[scim._verify_scim_token] = token_override


@pytest.fixture
def app():
    route_app = FastAPI()
    route_app.include_router(scim.router)
    return route_app


@pytest.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://scim.example.test",
    ) as http:
        yield http


class TestScimServiceContracts:
    @pytest.mark.parametrize(
        ("resource", "expected"),
        [
            (
                {
                    "userName": "ignored@example.test",
                    "emails": [
                        {"value": "first@example.test"},
                        {"value": " Primary@Example.TEST ", "primary": True},
                    ],
                    "name": {"givenName": "Alice", "familyName": "Example"},
                    "active": False,
                },
                {"email": "primary@example.test", "name": "Alice Example", "active": False},
            ),
            (
                {
                    "userName": "ignored@example.test",
                    "emails": [{"value": " First@Example.TEST "}],
                    "displayName": "Display Name",
                },
                {"email": "first@example.test", "name": "Display Name", "active": True},
            ),
            (
                {"userName": " User@Example.TEST "},
                {"email": "user@example.test", "name": "user@example.test", "active": True},
            ),
        ],
    )
    def test_parse_user_email_name_and_active_precedence(self, resource, expected):
        assert parse_scim_user(resource) == expected

    def test_user_list_error_and_token_serialization_are_exact(self):
        user = _user(name="Alice Family Name")
        resource = format_scim_user(user, BASE_URL)
        assert resource == _resource(user)
        assert resource["name"] == {
            "givenName": "Alice",
            "familyName": "Family Name",
            "formatted": "Alice Family Name",
        }
        assert format_scim_list([resource], total=7, start_index=3) == {
            "schemas": [SCIM_LIST_SCHEMA],
            "totalResults": 7,
            "itemsPerPage": 1,
            "startIndex": 3,
            "Resources": [resource],
        }
        assert format_scim_error(409, "conflict") == {
            "schemas": [SCIM_ERROR_SCHEMA],
            "status": "409",
            "detail": "conflict",
        }
        assert hash_scim_token("secret-token") == hashlib.sha256(b"secret-token").hexdigest()

    def test_user_serialization_handles_empty_name_time_location_and_deactivation(self):
        user = _user(name=None, created_at=None, auth_provider="deactivated")
        assert format_scim_user(user) == {
            **_resource(user, base_url=""),
            "active": False,
        }


class TestBearerAuthentication:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("authorization", [None, "", "Basic secret", "bearer secret"])
    async def test_missing_or_wrong_scheme_is_rejected_without_database_or_security_event(
        self, authorization, monkeypatch
    ):
        db = _db()
        emit = AsyncMock()
        monkeypatch.setattr(scim, "emit_security_event", emit)

        with pytest.raises(HTTPException) as error:
            await scim._verify_scim_token(authorization=authorization, db=db)

        assert error.value.status_code == 401
        assert error.value.detail == "Missing or invalid SCIM bearer token"
        db.execute.assert_not_awaited()
        emit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_token_hash_is_compared_against_active_candidates_until_match(self, monkeypatch):
        presented = "plain-text-secret"
        expected_hash = hash_scim_token(presented)
        first = SimpleNamespace(token_hash=hash_scim_token("other"), id=uuid.uuid4())
        matched = SimpleNamespace(token_hash=expected_hash, id=TOKEN_ID)
        ignored = SimpleNamespace(token_hash=expected_hash, id=uuid.uuid4())
        db = _db(_many([first, matched, ignored]))
        compare = MagicMock(side_effect=hmac.compare_digest)
        emit = AsyncMock()
        monkeypatch.setattr(scim.hmac, "compare_digest", compare)
        monkeypatch.setattr(scim, "emit_security_event", emit)

        result = await scim._verify_scim_token(authorization=f"Bearer  {presented}  ", db=db)

        assert result is matched
        _assert_statement(
            db.execute.await_args.args[0],
            select(ScimToken).where(ScimToken.active.is_(True)),
        )
        assert compare.call_args_list == [
            call(first.token_hash, expected_hash),
            call(matched.token_hash, expected_hash),
        ]
        emit.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("authorization", ["Bearer wrong", "Bearer   "])
    async def test_invalid_token_emits_exact_security_event_and_returns_401(self, authorization, monkeypatch):
        candidate = SimpleNamespace(token_hash=hash_scim_token("valid"))
        db = _db(_many([candidate]))
        emit = AsyncMock()
        monkeypatch.setattr(scim, "emit_security_event", emit)

        with pytest.raises(HTTPException) as error:
            await scim._verify_scim_token(authorization=authorization, db=db)

        assert error.value.status_code == 401
        assert error.value.detail == "Invalid SCIM bearer token"
        event = emit.await_args.args[0]
        assert event.event_type is EventType.API_KEY_REJECTED
        assert event.severity is Severity.WARNING
        assert event.outcome == "failure"
        assert event.detail == "Invalid SCIM bearer token"
        assert event.actor_id == ""
        assert event.target_id == ""

    @pytest.mark.asyncio
    async def test_token_database_failure_is_loud_and_does_not_emit_rejection(self, monkeypatch):
        db = _db()
        db.execute.side_effect = RuntimeError("token store unavailable")
        emit = AsyncMock()
        monkeypatch.setattr(scim, "emit_security_event", emit)

        with pytest.raises(RuntimeError, match="token store unavailable"):
            await scim._verify_scim_token(authorization="Bearer token", db=db)

        emit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_http_authentication_errors_use_current_fastapi_contract(self, app, client):
        _bind_db(app, _db(), authenticated=False)

        response = await client.get("/api/v1/scim/Users")

        assert response.status_code == 401
        assert response.headers["content-type"] == "application/json"
        assert response.json() == {"detail": "Missing or invalid SCIM bearer token"}


class TestUserListing:
    @pytest.mark.asyncio
    async def test_unfiltered_list_clamps_pagination_and_serializes_exact_response(self, app, client):
        users = [_user(), _user(id=OTHER_USER_ID, email="bob@example.test", username="bob", name="Bob Example")]
        db = _db(_scalar(2), _many(users))
        _bind_db(app, db)

        response = await client.get("/api/v1/scim/Users", params={"startIndex": -7, "count": 900})

        _assert_scim(
            response,
            200,
            {
                "schemas": [SCIM_LIST_SCHEMA],
                "totalResults": 2,
                "itemsPerPage": 2,
                "startIndex": 1,
                "Resources": [_resource(user) for user in users],
            },
        )
        base_query = select(User).where(live_users())
        _assert_statement(
            db.execute.await_args_list[0].args[0],
            select(func.count()).select_from(base_query.subquery()),
        )
        _assert_statement(
            db.execute.await_args_list[1].args[0],
            base_query.order_by(User.created_at).offset(0).limit(500),
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("operator", ["eq", "sw", "co", "ne"])
    async def test_username_filter_uses_exact_email_query_and_pagination(self, operator, app, client):
        user = _user()
        db = _db(_scalar(4), _many([user]))
        _bind_db(app, db)
        raw_value = " Alice@Example.TEST "
        normalized = "alice@example.test"

        response = await client.get(
            "/api/v1/scim/Users",
            params={
                "filter": f'userName {operator} "{raw_value}"',
                "startIndex": 3,
                "count": 1,
            },
        )

        _assert_scim(
            response,
            200,
            {
                "schemas": [SCIM_LIST_SCHEMA],
                "totalResults": 4,
                "itemsPerPage": 1,
                "startIndex": 3,
                "Resources": [_resource(user)],
            },
        )
        predicates = {
            "eq": User.email == normalized,
            "sw": User.email.startswith(normalized),
            "co": User.email.contains(normalized),
            "ne": User.email != normalized,
        }
        filtered = select(User).where(live_users()).where(predicates[operator])
        _assert_statement(
            db.execute.await_args_list[0].args[0],
            select(func.count()).select_from(filtered.subquery()),
        )
        _assert_statement(
            db.execute.await_args_list[1].args[0],
            filtered.order_by(User.created_at).offset(2).limit(1),
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("filter_value", "detail"),
        [
            ("userName eq unquoted@example.test", "Invalid filter expression: userName eq unquoted@example.test"),
            ('name.givenName eq "Alice"', "Unsupported filter attribute: name.givenname"),
        ],
    )
    async def test_invalid_or_unsupported_filters_return_exact_scim_error_without_query(
        self, filter_value, detail, app, client
    ):
        db = _db()
        _bind_db(app, db)

        response = await client.get("/api/v1/scim/Users", params={"filter": filter_value})

        _assert_scim(response, 400, format_scim_error(400, detail))
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_route_defensively_rejects_filter_operator_not_supported_by_parser(self, app, client, monkeypatch):
        db = _db()
        _bind_db(app, db)
        monkeypatch.setattr(
            scim,
            "parse_scim_filter",
            lambda _raw: SimpleNamespace(attr="username", op="gt", value="alice@example.test"),
        )

        response = await client.get("/api/v1/scim/Users", params={"filter": 'userName gt "alice"'})

        _assert_scim(response, 400, format_scim_error(400, "Unsupported filter operator: gt"))
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_filter_parser_failure_is_loud_and_does_not_touch_database(self, app, client, monkeypatch):
        db = _db()
        _bind_db(app, db)
        monkeypatch.setattr(scim, "parse_scim_filter", MagicMock(side_effect=RuntimeError("parser failed")))

        response = await client.get("/api/v1/scim/Users", params={"filter": 'userName eq "alice"'})

        assert response.status_code == 500
        assert response.text == "Internal Server Error"
        _assert_no_write(db)
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_list_database_failure_is_loud_and_has_no_mutation(self, app, client):
        db = _db()
        db.execute.side_effect = RuntimeError("database unavailable")
        _bind_db(app, db)

        response = await client.get("/api/v1/scim/Users")

        assert response.status_code == 500
        assert response.text == "Internal Server Error"
        _assert_no_write(db)


class TestUserLookup:
    @pytest.mark.asyncio
    async def test_invalid_id_is_404_without_query(self, app, client):
        db = _db()
        _bind_db(app, db)

        response = await client.get("/api/v1/scim/Users/not-a-uuid")

        _assert_scim(response, 404, format_scim_error(404, "User not found"))
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_user_is_exact_scim_404(self, app, client):
        db = _db(_one(None))
        _bind_db(app, db)

        response = await client.get(f"/api/v1/scim/Users/{USER_ID}")

        _assert_scim(response, 404, format_scim_error(404, "User not found"))
        _assert_statement(db.execute.await_args.args[0], select(User).where(User.id == USER_ID, live_users()))
        _assert_no_write(db)

    @pytest.mark.asyncio
    async def test_get_user_queries_deployment_wide_id_and_serializes_names_and_email(self, app, client):
        user = _user(name="Alice Family Name")
        db = _db(_one(user))
        _bind_db(app, db)

        response = await client.get(f"/api/v1/scim/Users/{USER_ID}")

        _assert_scim(response, 200, _resource(user))
        _assert_statement(db.execute.await_args.args[0], select(User).where(User.id == USER_ID, live_users()))
        _assert_no_write(db)

    @pytest.mark.asyncio
    async def test_lookup_database_failure_is_loud_and_has_no_mutation(self, app, client):
        db = _db()
        db.execute.side_effect = RuntimeError("lookup unavailable")
        _bind_db(app, db)

        response = await client.get(f"/api/v1/scim/Users/{USER_ID}")

        assert response.status_code == 500
        _assert_no_write(db)


class TestUserCreation:
    @pytest.mark.asyncio
    async def test_missing_email_returns_scim_400_without_query_or_mutation(self, app, client, monkeypatch):
        db = _db()
        _bind_db(app, db)
        generate = AsyncMock()
        monkeypatch.setattr(scim, "generate_unique_username", generate)

        response = await client.post("/api/v1/scim/Users", json={"displayName": "No Email"})

        _assert_scim(response, 400, format_scim_error(400, "userName or email is required"))
        db.execute.assert_not_awaited()
        generate.assert_not_awaited()
        _assert_no_write(db)

    @pytest.mark.asyncio
    async def test_existing_email_returns_scim_409_without_username_generation(self, app, client, monkeypatch):
        existing = _user()
        db = _db(_one(existing))
        _bind_db(app, db)
        generate = AsyncMock()
        monkeypatch.setattr(scim, "generate_unique_username", generate)

        response = await client.post("/api/v1/scim/Users", json={"userName": " Alice@Example.TEST "})

        _assert_scim(
            response,
            409,
            format_scim_error(409, "User with email alice@example.test already exists"),
        )
        _assert_statement(
            db.execute.await_args.args[0],
            select(User).where(User.email == "alice@example.test"),
        )
        generate.assert_not_awaited()
        _assert_no_write(db)

    @pytest.mark.asyncio
    async def test_create_orders_lookup_username_flush_commit_audit_and_returns_resource(
        self, app, client, monkeypatch
    ):
        order = []
        db = _db()

        async def execute(statement):
            order.append("lookup")
            _assert_statement(statement, select(User).where(User.email == "alice@example.test"))
            return _one(None)

        async def generate(email, session):
            order.append("username")
            assert email == "alice@example.test"
            assert session is db
            return "alice-generated"

        def add(user):
            order.append("add")
            assert user.id is None

        async def flush():
            order.append("flush")
            created = db.add.call_args.args[0]
            created.id = USER_ID
            created.created_at = CREATED_AT

        async def commit():
            order.append("commit")

        async def emit(event):
            order.append("emit")

        db.execute.side_effect = execute
        db.add.side_effect = add
        db.flush.side_effect = flush
        db.commit.side_effect = commit
        _bind_db(app, db)
        monkeypatch.setattr(scim, "generate_unique_username", AsyncMock(side_effect=generate))
        monkeypatch.setattr(scim.bus, "emit", AsyncMock(side_effect=emit))

        response = await client.post(
            "/api/v1/scim/Users",
            json={
                "userName": "ignored@example.test",
                "emails": [{"value": " Alice@Example.TEST ", "primary": True, "type": "work"}],
                "name": {"givenName": "Alice", "familyName": "Example"},
                "active": True,
            },
        )

        created = db.add.call_args.args[0]
        assert isinstance(created, User)
        assert created.email == "alice@example.test"
        assert created.username == "alice-generated"
        assert created.name == "Alice Example"
        assert created.role is UserRole.user
        assert created.auth_provider == "scim"
        assert created.password_hash is None
        assert order == ["lookup", "username", "add", "flush", "commit", "emit"]
        event = scim.bus.emit.await_args.args[0]
        assert event == UserCreated(user_id=str(USER_ID), email="alice@example.test", role="user")
        _assert_scim(response, 201, _resource(created))
        db.rollback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_flush_integrity_conflict_rolls_back_and_returns_exact_scim_409(self, app, client, monkeypatch):
        db = _db(_one(None))
        db.flush.side_effect = IntegrityError("insert user", {}, Exception("duplicate username or email"))
        _bind_db(app, db)
        monkeypatch.setattr(scim, "generate_unique_username", AsyncMock(return_value="alice"))
        emit = AsyncMock()
        monkeypatch.setattr(scim.bus, "emit", emit)

        response = await client.post("/api/v1/scim/Users", json={"userName": "alice@example.test"})

        _assert_scim(
            response,
            409,
            format_scim_error(409, "User with email alice@example.test already exists"),
        )
        db.add.assert_called_once()
        db.rollback.assert_awaited_once_with()
        db.commit.assert_not_awaited()
        emit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_username_service_failure_is_loud_before_add(self, app, client, monkeypatch):
        db = _db(_one(None))
        _bind_db(app, db)
        monkeypatch.setattr(
            scim,
            "generate_unique_username",
            AsyncMock(side_effect=ValueError("username is already reserved")),
        )

        response = await client.post("/api/v1/scim/Users", json={"userName": "alice@example.test"})

        assert response.status_code == 500
        assert response.text == "Internal Server Error"
        _assert_no_write(db)

    @pytest.mark.asyncio
    async def test_commit_failure_is_loud_and_does_not_emit_created_event(self, app, client, monkeypatch):
        db = _db(_one(None))

        async def flush():
            created = db.add.call_args.args[0]
            created.id = USER_ID
            created.created_at = CREATED_AT

        db.flush.side_effect = flush
        db.commit.side_effect = RuntimeError("commit failed")
        _bind_db(app, db)
        monkeypatch.setattr(scim, "generate_unique_username", AsyncMock(return_value="alice"))
        emit = AsyncMock()
        monkeypatch.setattr(scim.bus, "emit", emit)

        response = await client.post("/api/v1/scim/Users", json={"userName": "alice@example.test"})

        assert response.status_code == 500
        db.flush.assert_awaited_once_with()
        db.commit.assert_awaited_once_with()
        db.rollback.assert_not_awaited()
        emit.assert_not_awaited()


class TestUserReplacement:
    @pytest.mark.asyncio
    async def test_replace_updates_email_name_and_deactivates_password_user(self, app, client):
        user = _user(auth_provider="local")
        db = _db(_one(user))
        _bind_db(app, db)

        response = await client.put(
            f"/api/v1/scim/Users/{USER_ID}",
            json={
                "emails": [{"value": " New@Example.TEST ", "primary": True}],
                "name": {"givenName": "New", "familyName": "Name"},
                "active": False,
            },
        )

        assert user.email == "new@example.test"
        assert user.name == "New Name"
        assert user.password_hash is None
        assert user.auth_provider == "deactivated"
        db.commit.assert_awaited_once_with()
        db.rollback.assert_not_awaited()
        _assert_scim(response, 200, _resource(user))

    @pytest.mark.asyncio
    async def test_replace_reactivates_as_scim_without_restoring_password(self, app, client):
        user = _user(auth_provider="deactivated", password_hash=None)
        db = _db(_one(user))
        _bind_db(app, db)

        response = await client.put(f"/api/v1/scim/Users/{USER_ID}", json={"active": True})

        assert user.email == "alice@example.test"
        assert user.name == "Alice Example"
        assert user.auth_provider == "scim"
        assert user.password_hash is None
        db.commit.assert_awaited_once_with()
        _assert_scim(response, 200, _resource(user))

    @pytest.mark.asyncio
    async def test_replace_missing_user_returns_scim_404_without_mutation(self, app, client):
        db = _db(_one(None))
        _bind_db(app, db)

        response = await client.put(f"/api/v1/scim/Users/{USER_ID}", json={"active": False})

        _assert_scim(response, 404, format_scim_error(404, "User not found"))
        _assert_no_write(db)

    @pytest.mark.asyncio
    async def test_replace_commit_conflict_is_unhandled_and_not_rolled_back(self, app, client):
        user = _user()
        db = _db(_one(user))
        db.commit.side_effect = IntegrityError("update user", {}, Exception("duplicate email"))
        _bind_db(app, db)

        response = await client.put(
            f"/api/v1/scim/Users/{USER_ID}",
            json={"userName": "taken@example.test", "displayName": "Changed"},
        )

        assert response.status_code == 500
        assert user.email == "taken@example.test"
        assert user.name == "Changed"
        db.rollback.assert_not_awaited()


class TestPatchOperation:
    @pytest.mark.parametrize(
        ("op", "path", "value", "error"),
        [
            ("invalid", "displayName", "Name", "Unsupported op: invalid"),
            ("remove", "displayName", None, "Cannot remove required attributes"),
            ("replace", None, "Name", "Unknown path: None"),
            ("replace", "unknown", "Name", "Unknown path: unknown"),
        ],
    )
    def test_invalid_operations_return_exact_error_without_mutation(self, op, path, value, error):
        user = _user()
        before = vars(user).copy()

        assert scim._apply_patch_op(user, op, path, value) == error
        assert vars(user) == before

    @pytest.mark.parametrize("path", ["displayName", "name"])
    def test_display_name_paths_replace_truthy_value_and_ignore_empty(self, path):
        user = _user(name="Old Name")
        assert scim._apply_patch_op(user, "ADD", path, "New Name") is None
        assert user.name == "New Name"
        assert scim._apply_patch_op(user, "replace", path, "") is None
        assert user.name == "New Name"

    @pytest.mark.parametrize(
        ("starting_name", "path", "value", "expected"),
        [
            ("Alice Example", "name.givenName", "Alicia", "Alicia Example"),
            ("Alice", "name.givenName", "Alicia", "Alicia"),
            ("Alice Example", "name.familyName", "Changed", "Alice Changed"),
            (None, "name.familyName", "Changed", "Changed"),
        ],
    )
    def test_structured_name_paths_preserve_the_other_name_part(self, starting_name, path, value, expected):
        user = _user(name=starting_name)

        assert scim._apply_patch_op(user, "replace", path, value) is None
        assert user.name == expected

    @pytest.mark.parametrize(
        "path",
        [
            "userName",
            "emails",
            'emails[type eq "work"].value',
            "emails.value",
        ],
    )
    def test_email_paths_normalize_nonempty_values(self, path):
        user = _user()

        assert scim._apply_patch_op(user, "replace", path, " New@Example.TEST ") is None
        assert user.email == "new@example.test"
        assert scim._apply_patch_op(user, "replace", path, None) is None
        assert user.email == "new@example.test"

    def test_active_path_deactivates_and_clears_password(self):
        user = _user(auth_provider="saml")

        assert scim._apply_patch_op(user, "replace", "active", False) is None
        assert user.auth_provider == "deactivated"
        assert user.password_hash is None

    def test_active_path_reactivates_only_deactivated_users(self):
        deactivated = _user(auth_provider="deactivated", password_hash=None)
        already_active = _user(auth_provider="local")

        assert scim._apply_patch_op(deactivated, "replace", "active", True) is None
        assert deactivated.auth_provider == "scim"
        assert deactivated.password_hash is None
        assert scim._apply_patch_op(already_active, "replace", "active", True) is None
        assert already_active.auth_provider == "local"
        assert already_active.password_hash == "stored-password-hash"

    @pytest.mark.parametrize("value", ["false", "TRUE", 0, 1, None, [], {}])
    def test_active_path_rejects_non_boolean_values_without_mutation(self, value):
        user = _user(auth_provider="saml")
        before = vars(user).copy()

        assert scim._apply_patch_op(user, "replace", "active", value) == "active must be a boolean"
        assert vars(user) == before


class TestUserPatch:
    @pytest.mark.asyncio
    async def test_patch_missing_user_returns_exact_scim_404(self, app, client):
        db = _db(_one(None))
        _bind_db(app, db)

        response = await client.patch(
            f"/api/v1/scim/Users/{USER_ID}",
            json={"schemas": [SCIM_PATCH_SCHEMA], "Operations": [{"op": "replace", "path": "active"}]},
        )

        _assert_scim(response, 404, format_scim_error(404, "User not found"))
        _assert_no_write(db)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("body", "detail"),
        [
            ({}, f"Request must include schema {SCIM_PATCH_SCHEMA}"),
            ({"schemas": [SCIM_PATCH_SCHEMA]}, "No operations provided"),
            ({"schemas": [SCIM_PATCH_SCHEMA], "Operations": []}, "No operations provided"),
        ],
    )
    async def test_patch_request_validation_returns_exact_scim_400_without_commit(self, body, detail, app, client):
        user = _user()
        db = _db(_one(user))
        _bind_db(app, db)

        response = await client.patch(f"/api/v1/scim/Users/{USER_ID}", json=body)

        _assert_scim(response, 400, format_scim_error(400, detail))
        db.commit.assert_not_awaited()
        assert user == _user()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("operation", "detail"),
        [
            ({}, "Unsupported op: "),
            ({"op": "remove", "path": "displayName"}, "Cannot remove required attributes"),
            ({"op": "replace", "path": "unknown", "value": "x"}, "Unknown path: unknown"),
            ({"op": "replace", "path": "active", "value": 1}, "active must be a boolean"),
        ],
    )
    async def test_patch_invalid_operation_returns_exact_scim_400_without_commit(self, operation, detail, app, client):
        user = _user()
        before = vars(user).copy()
        db = _db(_one(user))
        _bind_db(app, db)

        response = await client.patch(
            f"/api/v1/scim/Users/{USER_ID}",
            json={"schemas": [SCIM_PATCH_SCHEMA], "Operations": [operation]},
        )

        _assert_scim(response, 400, format_scim_error(400, detail))
        db.commit.assert_not_awaited()
        assert vars(user) == before

    @pytest.mark.asyncio
    async def test_patch_applies_operations_in_order_then_commits_and_serializes(self, app, client):
        user = _user()
        db = _db(_one(user))
        _bind_db(app, db)

        response = await client.patch(
            f"/api/v1/scim/Users/{USER_ID}",
            json={
                "schemas": [SCIM_PATCH_SCHEMA],
                "Operations": [
                    {"op": "replace", "path": "name.givenName", "value": "Alicia"},
                    {"op": "add", "path": "name.familyName", "value": "Changed"},
                    {"op": "replace", "path": 'emails[type eq "work"].value', "value": "NEW@EXAMPLE.TEST"},
                    {"op": "replace", "path": "active", "value": False},
                ],
            },
        )

        assert user.name == "Alicia Changed"
        assert user.email == "new@example.test"
        assert user.auth_provider == "deactivated"
        assert user.password_hash is None
        db.commit.assert_awaited_once_with()
        _assert_scim(response, 200, _resource(user))

    @pytest.mark.asyncio
    async def test_later_invalid_operation_leaves_earlier_in_memory_mutation_without_commit(self, app, client):
        user = _user()
        db = _db(_one(user))
        _bind_db(app, db)

        response = await client.patch(
            f"/api/v1/scim/Users/{USER_ID}",
            json={
                "schemas": [SCIM_PATCH_SCHEMA],
                "Operations": [
                    {"op": "replace", "path": "displayName", "value": "Partially Changed"},
                    {"op": "replace", "path": "unknown", "value": "x"},
                ],
            },
        )

        _assert_scim(response, 400, format_scim_error(400, "Unknown path: unknown"))
        assert user.name == "Partially Changed"
        db.commit.assert_not_awaited()
        db.rollback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_patch_commit_conflict_is_unhandled_and_not_rolled_back(self, app, client):
        user = _user()
        db = _db(_one(user))
        db.commit.side_effect = IntegrityError("update user", {}, Exception("duplicate email"))
        _bind_db(app, db)

        response = await client.patch(
            f"/api/v1/scim/Users/{USER_ID}",
            json={
                "schemas": [SCIM_PATCH_SCHEMA],
                "Operations": [{"op": "replace", "path": "userName", "value": "taken@example.test"}],
            },
        )

        assert response.status_code == 500
        assert user.email == "taken@example.test"
        db.rollback.assert_not_awaited()


class TestUserDeletion:
    @pytest.mark.asyncio
    async def test_delete_missing_user_returns_scim_404_without_mutation(self, app, client):
        db = _db(_one(None))
        _bind_db(app, db)

        response = await client.delete(f"/api/v1/scim/Users/{USER_ID}")

        _assert_scim(response, 404, format_scim_error(404, "User not found"))
        _assert_no_write(db)

    @pytest.mark.asyncio
    async def test_delete_scrubs_shell_commits_then_revokes_and_emits(self, app, client, monkeypatch):
        order = []
        user = _user()
        db = _db(_one(user), *[MagicMock() for _ in range(6)])
        db.commit.side_effect = lambda: order.append(("commit", None))

        async def emit(event):
            order.append(("emit", event))

        async def revoke(user_id):
            order.append(("revoke", user_id))
            return True

        monkeypatch.setattr(scim.bus, "emit", AsyncMock(side_effect=emit))
        monkeypatch.setattr(scim, "revoke_deleted_user_tokens", AsyncMock(side_effect=revoke))
        _bind_db(app, db)

        response = await client.delete(f"/api/v1/scim/Users/{USER_ID}")

        assert response.status_code == 204
        assert response.content == b""
        # The row is kept as a shell (never deleted); the event keeps the old address.
        db.delete.assert_not_awaited()
        assert order == [
            ("commit", None),
            ("revoke", USER_ID),
            ("emit", UserDeleted(user_id=str(USER_ID), email="alice@example.test")),
        ]
        assert user.deleted_at is not None and user.name == "Deleted user" and user.password_hash is None
        _assert_statement(db.execute.await_args_list[0].args[0], select(User).where(User.id == USER_ID, live_users()))

    @pytest.mark.asyncio
    async def test_delete_refuses_the_last_admin_without_any_change(self, app, client, monkeypatch):
        """A shell keeps its row, so first-run bootstrap could never replace a deleted last admin."""
        user = _user(role=UserRole.super_admin)
        lock = MagicMock()
        lock.scalars.return_value.all.return_value = [USER_ID]  # the only live admin
        db = _db(_one(user), lock)
        emit = AsyncMock()
        revoke = AsyncMock()
        monkeypatch.setattr(scim.bus, "emit", emit)
        monkeypatch.setattr(scim, "revoke_deleted_user_tokens", revoke)
        _bind_db(app, db)

        response = await client.delete(f"/api/v1/scim/Users/{USER_ID}")

        _assert_scim(response, 400, format_scim_error(400, "Cannot delete the last admin"))
        assert db.execute.await_args_list[1].args[0]._for_update_arg is not None
        assert db.execute.await_count == 2  # nothing deleted after the refusal
        db.rollback.assert_awaited_once()
        db.commit.assert_not_awaited()
        revoke.assert_not_awaited()
        emit.assert_not_awaited()
        assert user.deleted_at is None and user.email == "alice@example.test"

    @pytest.mark.asyncio
    async def test_delete_failure_before_commit_does_not_emit_deleted_event(self, app, client, monkeypatch):
        user = _user()
        db = _db(_one(user), RuntimeError("delete failed"))
        emit = AsyncMock()
        monkeypatch.setattr(scim.bus, "emit", emit)
        _bind_db(app, db)

        response = await client.delete(f"/api/v1/scim/Users/{USER_ID}")

        assert response.status_code == 500
        db.commit.assert_not_awaited()
        emit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_commit_failure_does_not_emit_deleted_event(self, app, client, monkeypatch):
        user = _user()
        db = _db(_one(user), *[MagicMock() for _ in range(6)])
        db.commit.side_effect = RuntimeError("commit failed")
        emit = AsyncMock()
        revoke = AsyncMock()
        monkeypatch.setattr(scim.bus, "emit", emit)
        monkeypatch.setattr(scim, "revoke_deleted_user_tokens", revoke)
        _bind_db(app, db)

        response = await client.delete(f"/api/v1/scim/Users/{USER_ID}")

        assert response.status_code == 500
        db.delete.assert_not_awaited()
        revoke.assert_not_awaited()
        emit.assert_not_awaited()


class TestDiscoveryDocuments:
    @pytest.mark.asyncio
    async def test_discovery_documents_are_public_exact_scim_json(self, client):
        service_provider = await client.get("/api/v1/scim/ServiceProviderConfig")
        schemas = await client.get("/api/v1/scim/Schemas")
        resource_types = await client.get("/api/v1/scim/ResourceTypes")

        _assert_scim(
            service_provider,
            200,
            {
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
                "documentationUri": "https://observal.dev/docs/scim",
                "patch": {"supported": True},
                "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
                "filter": {"supported": True, "maxResults": 100},
                "changePassword": {"supported": False},
                "sort": {"supported": False},
                "etag": {"supported": False},
                "authenticationSchemes": [
                    {
                        "type": "oauthbearertoken",
                        "name": "OAuth Bearer Token",
                        "description": "Authentication via bearer token",
                        "specUri": "https://www.rfc-editor.org/rfc/rfc6750",
                    }
                ],
            },
        )
        _assert_scim(
            schemas,
            200,
            {
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Schema"],
                "totalResults": 1,
                "Resources": [
                    {
                        "id": SCIM_USER_SCHEMA,
                        "name": "User",
                        "description": "SCIM core User schema",
                        "attributes": [
                            {
                                "name": "userName",
                                "type": "string",
                                "multiValued": False,
                                "required": True,
                                "uniqueness": "server",
                            },
                            {
                                "name": "displayName",
                                "type": "string",
                                "multiValued": False,
                                "required": False,
                            },
                            {
                                "name": "name",
                                "type": "complex",
                                "multiValued": False,
                                "required": False,
                                "subAttributes": [
                                    {
                                        "name": "givenName",
                                        "type": "string",
                                        "multiValued": False,
                                        "required": False,
                                    },
                                    {
                                        "name": "familyName",
                                        "type": "string",
                                        "multiValued": False,
                                        "required": False,
                                    },
                                    {
                                        "name": "formatted",
                                        "type": "string",
                                        "multiValued": False,
                                        "required": False,
                                    },
                                ],
                            },
                            {
                                "name": "emails",
                                "type": "complex",
                                "multiValued": True,
                                "required": True,
                                "subAttributes": [
                                    {
                                        "name": "value",
                                        "type": "string",
                                        "multiValued": False,
                                        "required": True,
                                    },
                                    {
                                        "name": "type",
                                        "type": "string",
                                        "multiValued": False,
                                        "required": False,
                                    },
                                    {
                                        "name": "primary",
                                        "type": "boolean",
                                        "multiValued": False,
                                        "required": False,
                                    },
                                ],
                            },
                            {
                                "name": "active",
                                "type": "boolean",
                                "multiValued": False,
                                "required": False,
                            },
                        ],
                        "meta": {
                            "resourceType": "Schema",
                            "location": f"/api/v1/scim/Schemas/{SCIM_USER_SCHEMA}",
                        },
                    }
                ],
            },
        )
        _assert_scim(
            resource_types,
            200,
            {
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
                "totalResults": 1,
                "Resources": [
                    {
                        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
                        "id": "User",
                        "name": "User",
                        "endpoint": "/Users",
                        "schema": SCIM_USER_SCHEMA,
                    }
                ],
            },
        )
