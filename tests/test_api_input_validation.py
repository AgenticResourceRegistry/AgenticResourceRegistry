# SPDX-FileCopyrightText: 2026 Dheirav <dheirav2005@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Bad client input must come back as a 4xx, never reach the database as a 500."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


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
