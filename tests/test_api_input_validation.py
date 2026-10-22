# SPDX-FileCopyrightText: 2026 Dheirav <dheirav2005@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Bad client input must come back as a 4xx, never reach the database as a 500."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import exc as sa_exc


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["category=%00", "harness=a%00b", "%00=x"])
async def test_nul_in_query_string_is_rejected_on_get(query):
    from api.middleware.content_type import ContentTypeMiddleware

    app = FastAPI()
    app.add_middleware(ContentTypeMiddleware)

    @app.get("/api/v1/mcps")
    async def mcps():
        return []

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        bad = await client.get("/api/v1/mcps?" + query)
        ok = await client.get("/api/v1/mcps?category=general&search=%2500")
    assert bad.status_code == 422
    assert "NUL" in bad.json()["detail"]
    assert ok.status_code == 200


# ── OBS-1466: field lengths match the VARCHAR columns ─────────────────────────


def _component_request_models():
    from schemas.hook import HookDraftRequest, HookSubmitRequest, HookUpdateRequest
    from schemas.mcp import McpDraftRequest, McpSubmitRequest, McpUpdateRequest
    from schemas.prompt import PromptDraftRequest, PromptSubmitRequest, PromptUpdateRequest
    from schemas.sandbox import SandboxDraftRequest, SandboxSubmitRequest, SandboxUpdateRequest
    from schemas.skill import SkillDraftRequest, SkillSubmitRequest, SkillUpdateRequest

    return [
        McpSubmitRequest,
        McpDraftRequest,
        McpUpdateRequest,
        SkillSubmitRequest,
        SkillDraftRequest,
        SkillUpdateRequest,
        HookSubmitRequest,
        HookDraftRequest,
        HookUpdateRequest,
        PromptSubmitRequest,
        PromptDraftRequest,
        PromptUpdateRequest,
        SandboxSubmitRequest,
        SandboxDraftRequest,
        SandboxUpdateRequest,
    ]


def _field_errors(model, **values):
    from pydantic import ValidationError

    try:
        model(**values)
    except ValidationError as exc:
        return {err["loc"][0]: err["type"] for err in exc.errors()}
    return {}


@pytest.mark.parametrize("model", _component_request_models(), ids=lambda m: m.__name__)
@pytest.mark.parametrize(("field", "limit"), [("name", 255), ("owner", 255), ("version", 50)])
def test_component_requests_bound_name_owner_and_version(model, field, limit):
    errors = _field_errors(model, **{field: "1" * (limit + 1)})
    assert errors.get(field) == "string_too_long"


def test_agent_requests_bound_owner_model_name_and_version():
    from schemas.agent import AgentCreateRequest, AgentUpdateRequest, AgentVersionCreateRequest

    for model in (AgentCreateRequest, AgentUpdateRequest):
        errors = _field_errors(model, owner="o" * 256, model_name="m" * 101, version="1.0." + "1" * 50)
        assert errors.get("owner") == "string_too_long"
        assert errors.get("model_name") == "string_too_long"
        assert errors.get("version") == "string_too_long"
    errors = _field_errors(AgentVersionCreateRequest, model_name="m" * 101, version="1.0." + "1" * 50)
    assert errors.get("model_name") == "string_too_long"
    assert errors.get("version") == "string_too_long"


def test_skill_folder_drafts_bound_owner():
    # The route builds a SkillDraftRequest from this one inside the handler, where
    # an over-long owner would fail as a ValidationError, which is a 500.
    from schemas.skill import SkillFolderDraftRequest

    assert _field_errors(SkillFolderDraftRequest, owner="o" * 256).get("owner") == "string_too_long"


def test_team_requests_bound_name():
    from schemas.team import TeamCreateRequest, TeamUpdateRequest

    assert _field_errors(TeamCreateRequest, name="t" * 256).get("name") == "string_too_long"
    assert _field_errors(TeamUpdateRequest, name="t" * 256).get("name") == "string_too_long"


# ── Safety net: a value PostgreSQL rejects is still the client's 422 ─────────


class _PgError(Exception):
    def __init__(self, sqlstate):
        super().__init__("db said no")
        self.sqlstate = sqlstate


def _client_encode_error():
    # What asyncpg's adapter raises when its own DataError (a ValueError) fires while encoding a parameter.
    orig = Exception("invalid input for query argument $1")
    orig.__cause__ = ValueError("invalid UUID 'not-a-uuid'")
    return orig


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("make_exc", "expected"),
    [
        (lambda: sa_exc.DBAPIError("SELECT 1", {"v": "x"}, _PgError("22001")), 422),
        (lambda: sa_exc.DataError("SELECT 1", {}, _PgError(None)), 422),
        (lambda: sa_exc.InterfaceError("SELECT 1", {}, _client_encode_error()), 422),
        (lambda: sa_exc.IntegrityError("INSERT", {}, _PgError("23505")), 500),
        (lambda: sa_exc.OperationalError("SELECT 1", {}, _PgError("08006")), 500),
    ],
)
async def test_db_value_errors_become_422_and_other_db_errors_stay_500(make_exc, expected):
    from middleware import db_value_error_handler

    app = FastAPI()
    app.add_exception_handler(sa_exc.DBAPIError, db_value_error_handler)

    @app.get("/boom")
    async def boom():
        raise make_exc()

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/boom")
    assert resp.status_code == expected
    if expected == 422:
        # The statement and its parameters never reach the client.
        assert resp.json() == {"detail": "A request value is not valid for its field"}


# ── OBS-1471: one semver rule for component versions ─────────────────────────


@pytest.mark.parametrize("model", _component_request_models(), ids=lambda m: m.__name__)
@pytest.mark.parametrize("version", ["", "-1.0.0", "1 .0.0", "v1.0.0", "1.0", "01.0.0", "1.0.0 ", "1.0.0\n"])
def test_component_requests_reject_non_semver_versions(model, version):
    assert _field_errors(model, version=version).get("version") == "value_error"


@pytest.mark.parametrize("model", _component_request_models(), ids=lambda m: m.__name__)
@pytest.mark.parametrize("version", ["0.1.0", "1.2.3", "2.0.0-beta.1"])
def test_component_requests_accept_semver_versions(model, version):
    assert "version" not in _field_errors(model, version=version)


@pytest.mark.parametrize("version", ["1.0", "01.0.0", "1.0.0\n", "1.0." + "1" * 50])
def test_version_publish_requests_share_the_component_version_rule(version):
    from schemas.component_version import VersionPublishRequest

    assert "version" in _field_errors(VersionPublishRequest, version=version, description="d")
    assert VersionPublishRequest(version="1.2.0-rc.1", description="d").version == "1.2.0-rc.1"


def test_agent_component_refs_share_the_component_version_rule():
    import uuid

    from schemas.agent import ComponentRef

    ref = {"component_type": "mcp", "component_id": str(uuid.uuid4())}
    assert ComponentRef(**ref, version="1.2.0-rc.1").version == "1.2.0-rc.1"
    assert _field_errors(ComponentRef, **ref, version="v1").get("version") == "value_error"


# ── OBS-1468: a non-string agent name is a type error, not a crash ───────────


@pytest.mark.parametrize("field", ["name", "version"])
@pytest.mark.parametrize("value", [123, 1.5, ["x"], {"a": 1}])
def test_agent_update_rejects_non_string_name_and_version(field, value):
    from schemas.agent import AgentUpdateRequest

    assert _field_errors(AgentUpdateRequest, **{field: value}).get(field) == "string_type"


@pytest.mark.parametrize("value", [123, ["x"]])
def test_agent_restore_rejects_non_string_name(value):
    from schemas.agent import AgentRestoreRequest

    assert _field_errors(AgentRestoreRequest, name=value).get("name") == "string_type"


# ── OBS-1469: a name that cannot become a slug is a 422 ──────────────────────


def _publisher():
    import uuid
    from types import SimpleNamespace

    from models.user import UserRole

    return SimpleNamespace(id=uuid.uuid4(), role=UserRole.user, username="alice", email="a@example.com")


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["中文", "🚀", "ééé", "!!!", "___", "...", "", "   ", "draft"])
@pytest.mark.parametrize("in_team", [False, True])
async def test_publish_target_rejects_names_without_a_slug(name, in_team):
    import uuid
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from fastapi import HTTPException

    from services.teamspace import resolve_publish_target

    db = MagicMock()
    db.get = AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4(), handle="tools", is_private=False))
    db.execute = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await resolve_publish_target(db, _publisher(), name, team_id=uuid.uuid4() if in_team else None)
    assert exc.value.status_code == 422
    # Rejected before any team lookup, so the error is about the name and nothing else.
    db.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_target_still_slugs_ordinary_names():
    from unittest.mock import MagicMock

    from services.teamspace import resolve_publish_target

    target = await resolve_publish_target(MagicMock(), _publisher(), "My Tool")
    assert (target.namespace, target.slug) == ("alice", "my-tool")


@pytest.mark.asyncio
async def test_publish_target_reports_unusable_username_as_422():
    from unittest.mock import MagicMock

    from fastapi import HTTPException

    from services.teamspace import resolve_publish_target

    user = _publisher()
    user.username = None
    with pytest.raises(HTTPException) as exc:
        await resolve_publish_target(MagicMock(), user, "My Tool")
    assert exc.value.status_code == 422


@pytest.mark.parametrize("name", ["", "   "])
def test_team_requests_reject_blank_names(name):
    from schemas.team import TeamCreateRequest, TeamUpdateRequest

    assert _field_errors(TeamCreateRequest, name=name).get("name") == "string_too_short"
    assert _field_errors(TeamUpdateRequest, name=name).get("name") == "string_too_short"


# ── OBS-1470: out-of-range numbers and ids fail validation, not the query ────


def _route_client(router):
    import uuid
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from api.deps import get_current_user, get_db, get_registry_user
    from models.user import UserRole

    user = SimpleNamespace(id=uuid.uuid4(), role=UserRole.super_admin, username="root", email="r@example.com")
    db = MagicMock(name="db")
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_registry_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test"), db


def _routers():
    from api.routes import dashboard, exec_dashboard, inbox, insights
    from api.routes.admin import router as admin_router
    from api.routes.agent import router as agent_router

    return {
        "inbox": inbox.router,
        "dashboard": dashboard.router,
        "exec": exec_dashboard.router,
        "insights": insights.router,
        "admin": admin_router,
        "agent": agent_router,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("router", "path"),
    [
        ("inbox", "/api/v1/inbox?page=99999999999999999999"),
        ("inbox", "/api/v1/inbox?page=100001"),
        ("dashboard", "/api/v1/overview/top-agents?limit=-1"),
        ("dashboard", "/api/v1/overview/top-agents?limit=0"),
        ("exec", "/api/v1/exec/top-agents?limit=-1"),
        ("exec", "/api/v1/exec/developer-breakdown?limit=0"),
        ("admin", "/api/v1/admin/retention/preview?days=99999999"),
        ("insights", "/api/v1/insights/reports/not-a-uuid"),
        ("insights", "/api/v1/insights/reports/not-a-uuid/export/html"),
        ("agent", f"/api/v1/agents/{'0' * 8}-0000-0000-0000-{'0' * 12}/insights/reports/not-a-uuid"),
    ],
)
async def test_out_of_range_parameters_are_rejected_before_the_database(router, path):
    client, db = _route_client(_routers()[router])
    async with client:
        resp = await client.get(path)
    assert resp.status_code == 422, resp.text
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_insights_report_mutations_reject_non_uuid_ids():
    client, db = _route_client(_routers()["insights"])
    async with client:
        deleted = await client.delete("/api/v1/insights/reports/not-a-uuid")
        applied = await client.post("/api/v1/insights/reports/not-a-uuid/apply", json={})
    assert (deleted.status_code, applied.status_code) == (422, 422)
    db.execute.assert_not_called()


# ── OBS-1463 item 3: the Bearer scheme is case-insensitive ───────────────────


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Bearer abc", "abc"),
        ("bearer abc", "abc"),
        ("BEARER  abc ", "abc"),
        ("Bearer ", ""),
        ("Basic abc", None),
        ("Bearerabc", None),
        ("", None),
        (None, None),
        (123, None),
    ],
)
def test_bearer_token_parses_scheme_case_insensitively(header, expected):
    from services.auth_header import bearer_token

    assert bearer_token(header) == expected


@pytest.mark.asyncio
async def test_lowercase_bearer_scheme_authenticates(monkeypatch):
    import uuid
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from fastapi import Depends

    import api.deps as deps

    user = SimpleNamespace(id=uuid.uuid4(), auth_provider="local", must_change_password=False)
    authenticate = AsyncMock(return_value=user)
    monkeypatch.setattr(deps, "_authenticate_via_jwt", authenticate)
    monkeypatch.setattr(deps, "_enforce_password_change", AsyncMock())

    app = FastAPI()
    app.dependency_overrides[deps.get_db] = lambda: None

    @app.get("/me")
    async def me(current=Depends(deps.get_current_user)):
        return {"id": str(current.id)}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/me", headers={"Authorization": "bearer tok123"})
    assert resp.status_code == 200
    authenticate.assert_awaited_once_with("tok123", None)


def test_cache_key_treats_lowercase_bearer_as_an_identity():
    from types import SimpleNamespace

    from services.cache import _request_key_builder

    def request(auth):
        return SimpleNamespace(url=SimpleNamespace(path="/x"), query_params=None, headers={"authorization": auth})

    anon = _request_key_builder(lambda: None, request=request(""))
    assert _request_key_builder(lambda: None, request=request("bearer a")) != anon
    assert _request_key_builder(lambda: None, request=request("bearer a")) != _request_key_builder(
        lambda: None, request=request("bearer b")
    )


# ── OBS-1463 item 6: agent harnesses are checked against the registry ────────


def test_agent_requests_validate_supported_harnesses():
    import uuid

    from schemas.agent import AgentCreateRequest, AgentUpdateRequest, AgentVersionCreateRequest

    prompt_ref = [{"component_type": "prompt", "component_id": str(uuid.uuid4())}]
    create = {"name": "agt", "version": "1.0.0", "owner": "o", "components": prompt_ref}
    for model, base in (
        (AgentCreateRequest, create),
        (AgentUpdateRequest, {}),
        (AgentVersionCreateRequest, {"version": "1.0.0"}),
    ):
        assert _field_errors(model, **base, supported_harnesses=["nope"]).get("supported_harnesses") == "value_error"
        assert model(**base, supported_harnesses=["claude_code"]).supported_harnesses == ["claude-code"]
    # Omitting the field on a partial update keeps the stored list.
    assert AgentUpdateRequest().supported_harnesses is None


@pytest.mark.asyncio
async def test_bulk_agents_report_bad_versions_and_harnesses_per_item():
    from unittest.mock import AsyncMock, MagicMock

    from api.routes.bulk import bulk_create_agents
    from schemas.bulk import BulkAgentRequest

    request = BulkAgentRequest(
        dry_run=True,
        agents=[
            {"name": "bad-version", "version": "1.0\n"},
            {"name": "bad-harness", "supported_harnesses": ["claude_code", "vscode"]},
        ],
    )
    result = await bulk_create_agents(request, db=AsyncMock(), current_user=MagicMock())

    assert [(item.name, item.status) for item in result.results] == [
        ("bad-version", "error"),
        ("bad-harness", "error"),
    ]
    assert "version" in result.results[0].error.lower()
    assert "vscode" in result.results[1].error
    assert "claude" not in result.results[1].error


def test_bulk_agent_harnesses_are_normalized_like_agent_create():
    from api.routes.bulk import _definition_problem
    from schemas.bulk import BulkAgentItem

    item = BulkAgentItem(name="agt", supported_harnesses=["claude_code", "pi"])
    assert _definition_problem(item) is None
    assert item.supported_harnesses == ["claude-code", "pi"]
