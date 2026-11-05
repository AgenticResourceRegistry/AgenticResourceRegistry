# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Regression coverage for Audit 2 PR1 ingest abuse guardrails."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError


def _info():
    info = MagicMock()
    info.context = {"project_id": "default"}
    return info


def _query_bound(default, name: str):
    if hasattr(default, name):
        return getattr(default, name)
    for item in getattr(default, "metadata", []):
        if hasattr(item, name):
            return getattr(item, name)
    return None


def test_session_ingest_rejects_oversized_batches_and_lines():
    from api.routes.ingest import MAX_SESSION_LINES, MAX_TEXT_LENGTH, SessionIngestRequest

    with pytest.raises(ValidationError):
        SessionIngestRequest(session_id="session-1", lines=["{}"] * (MAX_SESSION_LINES + 1))

    with pytest.raises(ValidationError):
        SessionIngestRequest(session_id="session-1", lines=["x" * (MAX_TEXT_LENGTH + 1)])

    with pytest.raises(ValidationError):
        SessionIngestRequest(session_id="session-1", lines=["{}"], start_offset=-1)


def test_session_ingest_rejects_unbounded_integrity_counts():
    from api.routes.ingest import MAX_SESSION_TOTAL_LINES, SessionIngestRequest

    with pytest.raises(ValidationError):
        SessionIngestRequest(session_id="session-1", lines=[], total_line_count=MAX_SESSION_TOTAL_LINES + 1)

    with pytest.raises(ValidationError):
        SessionIngestRequest(session_id="session-1", lines=[], hashed_line_count=MAX_SESSION_TOTAL_LINES + 1)


def test_alert_history_query_params_are_bounded():
    import inspect

    from api.routes.alert import get_alert_history

    signature = inspect.signature(get_alert_history)
    assert signature.parameters["limit"].default.default == 50
    assert _query_bound(signature.parameters["limit"].default, "le") == 200
    assert signature.parameters["offset"].default.default == 0
    assert _query_bound(signature.parameters["offset"].default, "ge") == 0


def test_mcp_validator_rejects_http_git_by_default():
    import services.mcp_validator as mcp_validator

    with (
        patch.object(mcp_validator, "ALLOWED_SCHEMES", {"https"}),
        patch.object(mcp_validator, "ALLOW_HTTP_GIT", False),
    ):
        err = mcp_validator._validate_git_url("http://github.com/example/repo")

    assert err is not None
    assert "scheme" in err.lower()
    assert "https" in err.lower()


def test_mcp_validator_allows_http_only_with_explicit_opt_in():
    import services.mcp_validator as mcp_validator

    with (
        patch.object(mcp_validator, "ALLOWED_SCHEMES", {"https", "http"}),
        patch.object(mcp_validator, "ALLOW_HTTP_GIT", True),
        patch.object(mcp_validator, "_ssrf_is_private", return_value=False),
    ):
        assert mcp_validator._validate_git_url("http://github.com/example/repo") is None
        assert "MCP_ALLOW_HTTP_GIT" in mcp_validator._git_url_warning("http://github.com/example/repo")


def test_mcp_validator_warning_is_empty_for_https_urls():
    import services.mcp_validator as mcp_validator

    with patch.object(mcp_validator, "ALLOW_HTTP_GIT", True):
        assert mcp_validator._git_url_warning("https://github.com/example/repo") == ""
        assert mcp_validator._git_url_warning("https://gitlab.example.com/x.git") == ""


@pytest.mark.asyncio
async def test_mcp_validator_redacts_clone_token_from_validation_details(monkeypatch):
    import uuid

    import services.mcp_validator as mcp_validator

    token = "super-secret-git-token-1234567890"
    listing = SimpleNamespace(id=uuid.uuid4(), git_url="https://github.com/example/private-repo.git")
    db = MagicMock()
    db.commit = AsyncMock()

    clone_error = RuntimeError(
        f"fatal: Authentication failed for 'https://x-access-token:{token}@github.com/example/private-repo.git/'"
    )
    monkeypatch.setattr(mcp_validator.settings, "GIT_CLONE_TOKEN", token)

    with (
        patch.object(mcp_validator, "_validate_git_url", return_value=None),
        patch.object(mcp_validator, "_async_clone", new=AsyncMock(side_effect=clone_error)),
    ):
        result = await mcp_validator._clone_and_inspect(listing, db, "/tmp/unused")

    assert result is None
    validation_result = db.add.call_args.args[0]
    assert token not in validation_result.details
    assert "Failed to clone repo:" in validation_result.details
    assert "**REDACTED**" in validation_result.details


def test_mcp_validator_env_var_controls_allowed_schemes_at_import():
    """Ensure the env-var -> ALLOWED_SCHEMES wiring is exercised, not just patched."""
    import importlib
    import os

    import services.mcp_validator as mcp_validator

    original_env = os.environ.get("MCP_ALLOW_HTTP_GIT")
    try:
        os.environ["MCP_ALLOW_HTTP_GIT"] = "true"
        importlib.reload(mcp_validator)
        assert mcp_validator.ALLOW_HTTP_GIT is True
        assert "http" in mcp_validator.ALLOWED_SCHEMES
        assert "https" in mcp_validator.ALLOWED_SCHEMES

        os.environ["MCP_ALLOW_HTTP_GIT"] = "false"
        importlib.reload(mcp_validator)
        assert mcp_validator.ALLOW_HTTP_GIT is False
        assert "http" not in mcp_validator.ALLOWED_SCHEMES
        assert {"https"} == mcp_validator.ALLOWED_SCHEMES
    finally:
        if original_env is None:
            os.environ.pop("MCP_ALLOW_HTTP_GIT", None)
        else:
            os.environ["MCP_ALLOW_HTTP_GIT"] = original_env
        importlib.reload(mcp_validator)


def test_session_ingest_rejects_oversized_short_string_fields():
    from api.routes.ingest import MAX_SHORT_STRING_LENGTH, SessionIngestRequest

    long_value = "x" * (MAX_SHORT_STRING_LENGTH + 1)

    with pytest.raises(ValidationError):
        SessionIngestRequest(session_id=long_value, lines=[])

    with pytest.raises(ValidationError):
        SessionIngestRequest(session_id="s", harness=long_value, lines=[])

    with pytest.raises(ValidationError):
        SessionIngestRequest(session_id="s", agent_id=long_value, lines=[])

    with pytest.raises(ValidationError):
        SessionIngestRequest(session_id="s", parent_session_id=long_value, lines=[])


def test_alert_history_endpoint_rejects_oversized_limit_via_query_validation():
    """Behavior-level guard: FastAPI must reject limit=300 through query validation."""
    import uuid

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api import deps as deps_module
    from api.routes import alert as alert_module
    from models.user import UserRole

    class _FakeDB:
        async def get(self, *args, **kwargs):
            return None

    async def _current_user():
        user = MagicMock()
        user.id = uuid.uuid4()
        user.email = "tester@example.com"
        user.role = UserRole.user
        return user

    async def _get_db():
        yield _FakeDB()

    app = FastAPI()
    app.dependency_overrides[deps_module.get_current_user] = _current_user
    app.dependency_overrides[alert_module.get_db] = _get_db
    app.include_router(alert_module.router)

    client = TestClient(app, raise_server_exceptions=False)
    alert_id = "00000000-0000-0000-0000-000000000000"

    # limit above bound -> 422
    resp = client.get(f"/api/v1/alerts/{alert_id}/history?limit=300")
    assert resp.status_code == 422
    assert "limit" in resp.text

    # negative offset -> 422
    resp = client.get(f"/api/v1/alerts/{alert_id}/history?limit=50&offset=-1")
    assert resp.status_code == 422
    assert "offset" in resp.text

    # limit at the maximum passes validation and then misses in the fake DB.
    resp = client.get(f"/api/v1/alerts/{alert_id}/history?limit=200&offset=0")
    assert resp.status_code == 404


def test_rate_limit_key_prefers_validated_identity_then_ip():
    from api.ratelimit import _get_rate_limit_key

    user_request = MagicMock()
    user_request.state = SimpleNamespace(current_user=SimpleNamespace(id="user-1"))
    user_request.headers = {}
    assert _get_rate_limit_key(user_request) == "user:user-1"

    ip_request = MagicMock()
    ip_request.state = SimpleNamespace()
    ip_request.headers = {}
    ip_request.client = MagicMock()
    ip_request.client.host = "203.0.113.10"
    with patch("api.ratelimit.ds") as mock_ds:
        mock_ds.get_sync.return_value = ""
        assert _get_rate_limit_key(ip_request) == "ip:203.0.113.10"
        ip_request.headers = {"authorization": "Bearer secret-token"}
        assert _get_rate_limit_key(ip_request) == "ip:203.0.113.10"


@pytest.mark.asyncio
async def test_get_current_user_stores_identity_for_rate_limit_key():
    import uuid

    from api.deps import get_current_user
    from models.user import UserRole

    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = "tester@example.com"
    user.role = UserRole.user
    user.auth_provider = "password"

    request = MagicMock()
    request.url.path = "/api/v1/ingest/session"
    request.state = SimpleNamespace()

    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)

    with (
        patch("api.deps._authenticate_via_jwt", AsyncMock(return_value=user)),
        patch("api.deps.get_redis", return_value=redis),
    ):
        result = await get_current_user(request, authorization="Bearer access-token", db=MagicMock())

    assert result is user
    assert request.state.current_user is user
