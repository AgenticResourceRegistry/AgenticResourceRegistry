# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for observal pull / observal agent pull fixes (Issue #667).

Covers:
1. AgentResponse includes `latest_approved_version` and `latest_version` fields.
2. POST /api/v1/agents/{id}/install returns 400 (not 500) when no approved version exists.
3. POST /api/v1/agents/{id}/install returns 200 when an approved version exists.

Uses the same MagicMock/AsyncMock pattern as test_agent_versions_api.py — no real DB.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.agent import AgentStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(role_value: str = "user"):
    from models.user import UserRole

    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = "test@example.com"
    user.username = "testuser"
    user.role = UserRole(role_value)
    return user


def _make_version(
    agent_id: uuid.UUID,
    ver: str = "1.0.0",
    status: AgentStatus = AgentStatus.approved,
):
    v = MagicMock()
    v.id = uuid.uuid4()
    v.agent_id = agent_id
    v.version = ver
    v.description = "A test agent"
    v.prompt = "Do something"
    v.model_name = "claude-3-5-sonnet"
    v.model_config_json = {}
    v.external_mcps = []
    v.supported_harnesses = ["claude-code"]
    v.required_capabilities = []
    v.inferred_supported_harnesses = ["claude-code"]
    v.yaml_snapshot = None
    v.harness_configs = None
    v.status = status
    v.is_prerelease = False
    v.rejection_reason = None
    v.download_count = 0
    v.released_by = uuid.uuid4()
    v.released_at = datetime.now(UTC)
    v.reviewed_by = None
    v.reviewed_at = None
    v.created_at = datetime.now(UTC)
    v.components = []
    return v


def _make_agent(owner_id: uuid.UUID | None = None, *, with_approved_version: bool = True):
    """Build a mock Agent.  If with_approved_version=True, attach an approved version."""

    owner_id = owner_id or uuid.uuid4()
    agent = MagicMock()
    agent.id = uuid.uuid4()
    agent.name = "ruffchecker"
    agent.namespace = "testuser"
    agent.slug = "ruffchecker"
    agent.qualified_name = "testuser/ruffchecker"
    agent.owner = "testuser"
    agent.created_by = owner_id
    agent.co_authors = []
    agent.success_criteria = None

    if with_approved_version:
        ver = _make_version(agent.id)
        agent.latest_version_id = ver.id
        agent.latest_version = ver
        agent.versions = [ver]
        # Compat properties
        agent.version = ver.version
        agent.description = ver.description
        agent.prompt = ver.prompt
        agent.model_name = ver.model_name
        agent.model_config_json = ver.model_config_json
        agent.external_mcps = ver.external_mcps
        agent.supported_harnesses = ver.supported_harnesses
        agent.required_capabilities = ver.required_capabilities
        agent.inferred_supported_harnesses = ver.inferred_supported_harnesses
        agent.status = AgentStatus.approved
        agent.rejection_reason = None
        agent.components = []
    else:
        agent.latest_version_id = None
        agent.latest_version = None
        agent.versions = []
        agent.version = "0.0.0"
        agent.description = ""
        agent.prompt = ""
        agent.model_name = ""
        agent.model_config_json = {}
        agent.external_mcps = []
        agent.supported_harnesses = []
        agent.required_capabilities = []
        agent.inferred_supported_harnesses = []
        agent.status = AgentStatus.draft
        agent.rejection_reason = None
        agent.components = []

    return agent


# ---------------------------------------------------------------------------
# Sub-issue 2: AgentResponse must include latest_approved_version + latest_version
# ---------------------------------------------------------------------------


def test_agent_response_schema_has_latest_approved_version_field():
    """AgentResponse schema must declare latest_approved_version as an optional str field."""
    from schemas.agent import AgentResponse

    fields = AgentResponse.model_fields
    assert "latest_approved_version" in fields, (
        "AgentResponse must include latest_approved_version field so CLI can determine pull version"
    )
    # Field should be optional (default None)
    assert fields["latest_approved_version"].default is None


def test_agent_response_schema_has_latest_version_field():
    """AgentResponse schema must declare latest_version as an optional str field."""
    from schemas.agent import AgentResponse

    fields = AgentResponse.model_fields
    assert "latest_version" in fields, (
        "AgentResponse must include latest_version field so CLI can determine pull version"
    )
    assert fields["latest_version"].default is None


def test_agent_to_response_populates_latest_approved_version():
    """_agent_to_response must set latest_approved_version from agent.versions."""
    from api.routes.agent.helpers import _agent_to_response

    agent = _make_agent(with_approved_version=True)
    # Ensure the approved version is in agent.versions list
    approved_ver = agent.latest_version
    approved_ver.status = AgentStatus.approved
    agent.versions = [approved_ver]

    resp = _agent_to_response(
        agent,
        created_by_email="test@example.com",
        created_by_username="testuser",
    )

    assert resp.latest_approved_version == approved_ver.version


def test_agent_to_response_latest_approved_version_none_when_no_approved():
    """_agent_to_response sets latest_approved_version=None when no approved version."""
    from api.routes.agent.helpers import _agent_to_response

    agent = _make_agent(with_approved_version=False)
    # Add a pending version (not approved)
    pending_ver = _make_version(agent.id, status=AgentStatus.pending)
    agent.versions = [pending_ver]
    agent.latest_version = pending_ver
    agent.latest_version_id = pending_ver.id
    agent.version = pending_ver.version
    agent.description = pending_ver.description
    agent.prompt = pending_ver.prompt
    agent.model_name = pending_ver.model_name
    agent.model_config_json = pending_ver.model_config_json
    agent.external_mcps = pending_ver.external_mcps
    agent.supported_harnesses = pending_ver.supported_harnesses
    agent.required_capabilities = pending_ver.required_capabilities
    agent.inferred_supported_harnesses = pending_ver.inferred_supported_harnesses
    agent.status = AgentStatus.pending
    agent.rejection_reason = None
    agent.components = []

    resp = _agent_to_response(
        agent,
        created_by_email="test@example.com",
        created_by_username="testuser",
    )

    assert resp.latest_approved_version is None


def test_agent_to_response_populates_latest_version_string():
    """_agent_to_response sets latest_version to agent.version string."""
    from api.routes.agent.helpers import _agent_to_response

    agent = _make_agent(with_approved_version=True)

    resp = _agent_to_response(
        agent,
        created_by_email="test@example.com",
        created_by_username="testuser",
    )

    assert resp.latest_version == agent.version


# ---------------------------------------------------------------------------
# Sub-issue 1: install endpoint must return 400 (not 500) when no approved version
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_agent_not_approved_returns_404():
    """A non-approved agent is not installable, and must not be distinguishable.

    The status gate runs before the no-version branch, so an agent without a
    published version reports 404 rather than confirming it exists. Owning the
    agent does not change that: install is gated on approval, not on permission.
    """
    from fastapi import HTTPException

    from api.routes.agent.install import install_agent
    from schemas.agent import AgentInstallRequest

    agent = _make_agent(with_approved_version=False)
    user = _make_user()
    user.id = agent.created_by

    req = AgentInstallRequest(harness="claude-code")
    request = MagicMock()
    request.url = MagicMock()
    request.url.scheme = "http"
    request.url.hostname = "localhost"

    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=lambda: []))))
    db.commit = AsyncMock()

    with (
        patch("api.routes.agent.install._load_agent", new=AsyncMock(return_value=agent)),
        patch("api.routes.agent.install.get_effective_agent_permission", return_value="owner"),
        patch("api.routes.agent.install._ds.get_sync_bool", return_value=False),
        patch("services.download_tracker.record_agent_download", new=AsyncMock()),
        pytest.raises(HTTPException) as exc,
    ):
        await install_agent(
            agent_id=str(agent.id),
            req=req,
            request=request,
            db=db,
            current_user=user,
        )

    assert exc.value.status_code == 404
    assert "not approved" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_install_agent_no_version_returns_400_for_draft_install():
    """With security.allow_draft_install on, the creator reaches the no-version branch.

    That setting is the only way past the approval gate, so it is the only way the
    400 is reachable. The creator already knows the agent exists, so naming the
    real problem leaks nothing.
    """
    from fastapi import HTTPException

    from api.routes.agent.install import install_agent
    from schemas.agent import AgentInstallRequest

    agent = _make_agent(with_approved_version=False)
    user = _make_user()
    user.id = agent.created_by

    req = AgentInstallRequest(harness="claude-code")
    request = MagicMock()
    request.url = MagicMock()
    request.url.scheme = "http"
    request.url.hostname = "localhost"

    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=lambda: []))))
    db.commit = AsyncMock()

    with (
        patch("api.routes.agent.install._load_agent", new=AsyncMock(return_value=agent)),
        patch("api.routes.agent.install.get_effective_agent_permission", return_value="owner"),
        patch("api.routes.agent.install._ds.get_sync_bool", return_value=True),
        patch("services.download_tracker.record_agent_download", new=AsyncMock()),
        pytest.raises(HTTPException) as exc,
    ):
        await install_agent(
            agent_id=str(agent.id),
            req=req,
            request=request,
            db=db,
            current_user=user,
        )

    assert exc.value.status_code == 400
    assert "version" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_install_agent_with_approved_version_succeeds():
    """install_agent returns 200 with config_snippet when agent has an approved version."""
    from api.routes.agent.install import install_agent
    from schemas.agent import AgentInstallRequest

    user = _make_user()
    agent = _make_agent(owner_id=user.id, with_approved_version=True)

    req = AgentInstallRequest(harness="claude-code")
    request = MagicMock()
    request.url = MagicMock()
    request.url.scheme = "http"
    request.url.hostname = "localhost"
    request.headers = {}
    request.client = MagicMock()
    request.client.host = "127.0.0.1"

    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=lambda: []))))
    db.commit = AsyncMock()

    fake_config = {"mcpServers": {}, "rules": "# ruffchecker\n"}

    with (
        patch("api.routes.agent.install._load_agent", new=AsyncMock(return_value=agent)),
        patch("api.routes.agent.install.get_effective_agent_permission", return_value="owner"),
        patch("api.routes.agent.install.generate_agent_config", return_value=fake_config),
        patch("api.routes.agent.install.emit_registry_event"),
        patch("api.routes.config.derive_endpoints", return_value={"api": "http://localhost:8000", "otlp_http": ""}),
        patch("services.download_tracker.record_agent_download", new=AsyncMock()),
    ):
        result = await install_agent(
            agent_id=str(agent.id),
            req=req,
            request=request,
            db=db,
            current_user=user,
        )

    assert result.agent_id == agent.id
    assert result.harness == "claude-code"
    assert isinstance(result.config_snippet, dict)


@pytest.mark.asyncio
async def test_anonymous_agent_install_does_not_record_download_metrics():
    """Guests can install approved agents without mutating download metrics."""
    from api.routes.agent.install import install_agent
    from schemas.agent import AgentInstallRequest

    agent = _make_agent(with_approved_version=True)
    request = MagicMock()
    request.url = MagicMock()
    request.url.scheme = "http"
    request.url.hostname = "localhost"
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=lambda: []))))
    db.commit = AsyncMock()
    record_download = AsyncMock()

    with (
        patch("api.routes.agent.install._load_agent", new=AsyncMock(return_value=agent)),
        patch("api.routes.agent.install.get_effective_agent_permission", return_value="view"),
        patch("api.routes.agent.install.generate_agent_config", return_value={"mcpServers": {}}),
        patch("api.routes.config.derive_endpoints", return_value={"api": "http://localhost:8000", "otlp_http": ""}),
        patch("services.download_tracker.record_agent_download", new=record_download),
    ):
        result = await install_agent(
            agent_id=str(agent.id),
            req=AgentInstallRequest(harness="claude-code"),
            request=request,
            db=db,
            current_user=None,
        )

    assert result.agent_id == agent.id
    record_download.assert_not_awaited()
    db.commit.assert_not_awaited()
