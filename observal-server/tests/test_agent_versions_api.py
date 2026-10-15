# SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the agent versioning API endpoints.

Uses MagicMock for AsyncSession — no real database required.
Calls standalone async functions directly (same pattern as
test_component_versions_api.py).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.agent import AgentStatus

# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------

SEMVER_VALID = "1.2.3"
SEMVER_INVALID = "not-a-version"


def _make_user(role_value: str = "user"):
    from models.user import UserRole

    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = "test@example.com"
    user.username = "testuser"
    user.role = UserRole(role_value)
    return user


def _make_agent(owner_id: uuid.UUID | None = None):
    agent = MagicMock()
    agent.id = uuid.uuid4()
    agent.name = "my-agent"
    agent.created_by = owner_id or uuid.uuid4()
    agent.co_authors = []
    agent.latest_version_id = None
    agent.latest_version = None
    agent.is_private = False
    agent.team_id = None
    return agent


def _make_version(agent_id: uuid.UUID, ver: str = SEMVER_VALID, status: AgentStatus = AgentStatus.pending):
    v = MagicMock()
    v.id = uuid.uuid4()
    v.agent_id = agent_id
    v.version = ver
    v.description = "A test agent version"
    v.prompt = "Do something useful"
    v.model_name = "claude-3-5-sonnet"
    v.model_config_json = {}
    v.external_mcps = []
    v.supported_harnesses = ["claude-code"]
    v.required_capabilities = ["rules"]
    v.inferred_supported_harnesses = ["claude-code"]
    v.yaml_snapshot = None
    v.success_criteria = None
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


def _db_returning_versions(versions: list, total: int | None = None):
    """DB mock that returns *versions* on scalars().all() and *total* on scalar()."""
    db = AsyncMock()
    scalars = MagicMock()
    scalars.all.return_value = versions
    result = MagicMock()
    result.scalars.return_value = scalars
    result.scalar.return_value = total if total is not None else len(versions)
    result.scalar_one_or_none.return_value = versions[0] if versions else None
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


def _db_returning_one(obj):
    """DB mock whose first execute returns *obj* as scalar_one_or_none."""
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = obj
    result.scalars.return_value = MagicMock(all=lambda: [] if obj is None else [obj])
    result.scalar.return_value = 0
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# 1. List versions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_versions_empty():
    """list_agent_versions returns empty items when no versions exist."""
    from api.routes.agent_versions import _list_agent_versions

    agent_id = str(uuid.uuid4())
    agent = _make_agent()
    db = _db_returning_versions([])

    with patch("api.routes.agent_versions._load_agent", new=AsyncMock(return_value=agent)):
        result = await _list_agent_versions(
            agent_id=agent_id,
            page=1,
            page_size=20,
            db=db,
            current_user=_make_user(),
        )

    assert result["items"] == []
    assert result["total"] == 0
    assert result["page"] == 1
    assert result["page_size"] == 20


@pytest.mark.asyncio
async def test_list_versions_with_data():
    """list_agent_versions returns version summaries with pagination metadata."""
    from api.routes.agent_versions import _list_agent_versions

    agent = _make_agent()
    ver = _make_version(agent.id)

    # We need two execute calls: one for versions list, one for count
    db = AsyncMock()
    versions_result = MagicMock()
    versions_scalars = MagicMock()
    versions_scalars.all.return_value = [ver]
    versions_result.scalars.return_value = versions_scalars
    count_result = MagicMock()
    count_result.scalar.return_value = 1
    db.execute = AsyncMock(side_effect=[versions_result, count_result])
    db.commit = AsyncMock()

    with patch("api.routes.agent_versions._load_agent", new=AsyncMock(return_value=agent)):
        result = await _list_agent_versions(
            agent_id=str(agent.id),
            page=1,
            page_size=20,
            db=db,
            current_user=_make_user(),
        )

    assert result["total"] == 1
    assert len(result["items"]) == 1
    assert result["items"][0]["version"] == SEMVER_VALID
    assert "component_count" in result["items"][0]


@pytest.mark.asyncio
async def test_anonymous_version_count_only_includes_approved_versions():
    """Anonymous pagination totals use the same approved-only filter as the rows."""
    from api.routes.agent_versions import _list_agent_versions

    agent = _make_agent()
    db = _db_returning_versions([], total=0)

    with patch("api.routes.agent_versions._load_agent", new=AsyncMock(return_value=agent)):
        await _list_agent_versions(
            agent_id=str(agent.id),
            page=1,
            page_size=20,
            db=db,
            current_user=None,
        )

    list_statement = db.execute.await_args_list[0].args[0]
    count_statement = db.execute.await_args_list[1].args[0]
    assert "agent_versions.status" in str(list_statement)
    assert "agent_versions.status" in str(count_statement)


@pytest.mark.asyncio
async def test_list_versions_pagination():
    """list_agent_versions respects page/page_size parameters."""
    from api.routes.agent_versions import _list_agent_versions

    agent = _make_agent()

    db = AsyncMock()
    versions_result = MagicMock()
    versions_scalars = MagicMock()
    versions_scalars.all.return_value = []
    versions_result.scalars.return_value = versions_scalars
    count_result = MagicMock()
    count_result.scalar.return_value = 42
    db.execute = AsyncMock(side_effect=[versions_result, count_result])
    db.commit = AsyncMock()

    with patch("api.routes.agent_versions._load_agent", new=AsyncMock(return_value=agent)):
        result = await _list_agent_versions(
            agent_id=str(agent.id),
            page=3,
            page_size=5,
            db=db,
            current_user=_make_user(),
        )

    assert result["page"] == 3
    assert result["page_size"] == 5
    assert result["total"] == 42


@pytest.mark.asyncio
async def test_list_versions_agent_not_found():
    """list_agent_versions raises 404 when agent doesn't exist."""
    from fastapi import HTTPException

    from api.routes.agent_versions import _list_agent_versions

    with (
        patch("api.routes.agent_versions._load_agent", new=AsyncMock(return_value=None)),
        pytest.raises(HTTPException) as exc,
    ):
        await _list_agent_versions(
            agent_id="nonexistent",
            page=1,
            page_size=20,
            db=AsyncMock(),
            current_user=_make_user(),
        )

    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# 2. Get specific version
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_version_found():
    """get_agent_version returns full version detail when found."""
    from api.routes.agent_versions import _get_agent_version

    agent = _make_agent()
    ver = _make_version(agent.id)
    db = _db_returning_one(ver)

    with patch("api.routes.agent_versions._load_agent", new=AsyncMock(return_value=agent)):
        result = await _get_agent_version(
            agent_id=str(agent.id),
            version=SEMVER_VALID,
            db=db,
            current_user=_make_user(),
        )

    assert result["version"] == SEMVER_VALID
    assert result["id"] == str(ver.id)
    assert "prompt" in result
    assert "model_name" in result
    assert "yaml_snapshot" in result


@pytest.mark.asyncio
async def test_get_version_not_found():
    """get_agent_version raises 404 when version doesn't exist."""
    from fastapi import HTTPException

    from api.routes.agent_versions import _get_agent_version

    agent = _make_agent()
    db = _db_returning_one(None)

    with (
        patch("api.routes.agent_versions._load_agent", new=AsyncMock(return_value=agent)),
        pytest.raises(HTTPException) as exc,
    ):
        await _get_agent_version(
            agent_id=str(agent.id),
            version="9.9.9",
            db=db,
            current_user=_make_user(),
        )

    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# 3. Create version
# ---------------------------------------------------------------------------


@pytest.fixture
def create_release_dependencies():
    # Pinning, model resolution, notifications and locks have dedicated tests.
    # These route tests assert that the interactions occur without relying on
    # an AsyncMock session to emulate PostgreSQL joins or nested transactions.
    with (
        patch("services.agent_lock.attach_pinned_components", new=AsyncMock(return_value=[])) as pins,
        patch("services.agent_lock.lock_agent_version", new=AsyncMock()) as lock,
        patch(
            "services.model_resolver.resolve_model_for_harness", new=AsyncMock(return_value=("claude-3-5-sonnet", []))
        ),
        patch("api.routes.agent_versions.inbox.on_publish", new=AsyncMock(return_value=0)) as notification,
    ):
        yield pins, lock, notification


@pytest.mark.asyncio
async def test_create_version_happy_path(create_release_dependencies):
    """create_agent_version creates a new AgentVersion and returns its data."""
    from api.routes.agent_versions import _create_agent_version
    from schemas.agent import AgentVersionCreateRequest

    owner_id = uuid.uuid4()
    agent = _make_agent(owner_id)
    user = _make_user()
    user.id = owner_id

    req = AgentVersionCreateRequest(
        version=SEMVER_VALID,
        description="First proper release",
        prompt="You are helpful",
        model_name="claude-3-5-sonnet",
        model_config_json={},
        external_mcps=[],
        supported_harnesses=["claude-code"],
        components=[],
    )

    # DB: dup check returns None, pending count returns 0
    db = AsyncMock()
    dup_result = MagicMock()
    dup_result.scalar_one_or_none.return_value = None
    count_result = MagicMock()
    count_result.scalar.return_value = 0
    db.execute = AsyncMock(side_effect=[dup_result, count_result])
    db.commit = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    with (
        patch("api.routes.agent_versions._load_agent", new=AsyncMock(return_value=agent)),
        patch("api.routes.agent_versions.validate_component_ids", new=AsyncMock(return_value=[])),
        patch("api.routes.agent_versions.infer_required_features", return_value=["rules"]),
        patch("api.routes.agent_versions.compute_supported_harnesses", return_value=["claude-code"]),
        patch("api.routes.agent_versions.generate_agent_config", return_value={"mcpServers": {}}),
        patch("api.routes.agent_versions.audit", new=AsyncMock()),
        patch("services.agent_snapshot.build_yaml_snapshot", new=AsyncMock(return_value="snapshot")),
    ):
        result = await _create_agent_version(
            agent_id=str(agent.id),
            req=req,
            db=db,
            current_user=user,
        )

    assert result["version"] == SEMVER_VALID
    assert result["status"] == AgentStatus.pending.value
    db.add.assert_called()
    db.commit.assert_called_once()
    for dependency in create_release_dependencies:
        dependency.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_version_bad_semver():
    """create_agent_version raises 422 for an invalid semver string."""
    from fastapi import HTTPException

    from api.routes.agent_versions import _create_agent_version

    # AgentVersionCreateRequest validates semver inline via field_validator,
    # so we need to mock that validation passes and let the route reject it.
    # Easier: pass a MagicMock request with invalid version directly.
    req = MagicMock()
    req.version = SEMVER_INVALID

    owner_id = uuid.uuid4()
    agent = _make_agent(owner_id)
    user = _make_user()
    user.id = owner_id

    with (
        patch("api.routes.agent_versions._load_agent", new=AsyncMock(return_value=agent)),
        patch("api.routes.agent_versions.validate_semver", return_value=False),
        pytest.raises(HTTPException) as exc,
    ):
        await _create_agent_version(
            agent_id=str(agent.id),
            req=req,
            db=AsyncMock(),
            current_user=user,
        )

    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_create_version_duplicate_409():
    """create_agent_version raises 409 if the version already exists."""
    from fastapi import HTTPException

    from api.routes.agent_versions import _create_agent_version
    from schemas.agent import AgentVersionCreateRequest

    owner_id = uuid.uuid4()
    agent = _make_agent(owner_id)
    user = _make_user()
    user.id = owner_id

    req = AgentVersionCreateRequest(
        version=SEMVER_VALID,
        model_name="claude-3-5-sonnet",
    )

    # DB returns an existing version (duplicate)
    existing_ver = _make_version(agent.id)
    db = _db_returning_one(existing_ver)

    with (
        patch("api.routes.agent_versions._load_agent", new=AsyncMock(return_value=agent)),
        pytest.raises(HTTPException) as exc,
    ):
        await _create_agent_version(
            agent_id=str(agent.id),
            req=req,
            db=db,
            current_user=user,
        )

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_create_version_not_owner_403():
    """create_agent_version raises 403 if the caller is not the agent owner."""
    from fastapi import HTTPException

    from api.routes.agent_versions import _create_agent_version
    from schemas.agent import AgentVersionCreateRequest

    owner_id = uuid.uuid4()
    agent = _make_agent(owner_id)
    other_user = _make_user()
    other_user.id = uuid.uuid4()  # different from owner

    req = AgentVersionCreateRequest(
        version=SEMVER_VALID,
        model_name="claude-3-5-sonnet",
    )

    with (
        patch("api.routes.agent_versions._load_agent", new=AsyncMock(return_value=agent)),
        pytest.raises(HTTPException) as exc,
    ):
        await _create_agent_version(
            agent_id=str(agent.id),
            req=req,
            db=AsyncMock(),
            current_user=other_user,
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_create_version_co_author_allowed(create_release_dependencies):
    """create_agent_version succeeds for a co-maintainer (not just owner)."""
    from api.routes.agent_versions import _create_agent_version
    from schemas.agent import AgentVersionCreateRequest

    owner_id = uuid.uuid4()
    co_author_id = uuid.uuid4()
    agent = _make_agent(owner_id)
    agent.co_authors = [str(co_author_id)]

    co_user = _make_user()
    co_user.id = co_author_id

    req = AgentVersionCreateRequest(
        version=SEMVER_VALID,
        description="Co-maintainer release",
        prompt="You are helpful",
        model_name="claude-3-5-sonnet",
    )

    # DB: dup check returns None, pending count returns 0, flush/commit succeed
    db = AsyncMock()
    dup_result = MagicMock()
    dup_result.scalar_one_or_none.return_value = None
    count_result = MagicMock()
    count_result.scalar.return_value = 0
    db.execute = AsyncMock(side_effect=[dup_result, count_result])
    db.commit = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    with (
        patch("api.routes.agent_versions._load_agent", new=AsyncMock(return_value=agent)),
        patch("api.routes.agent_versions.validate_component_ids", new=AsyncMock(return_value=[])),
        patch("api.routes.agent_versions.infer_required_features", return_value=[]),
        patch("api.routes.agent_versions.compute_supported_harnesses", return_value=[]),
        patch("api.routes.agent_versions.generate_agent_config", return_value={}),
        patch("api.routes.agent_versions.audit", new=AsyncMock()),
        patch("services.agent_snapshot.build_yaml_snapshot", new=AsyncMock(return_value="snapshot")),
    ):
        result = await _create_agent_version(
            agent_id=str(agent.id),
            req=req,
            db=db,
            current_user=co_user,
        )

    assert result["version"] == SEMVER_VALID
    assert result["status"] == "pending"


@pytest.mark.asyncio
async def test_create_version_warns_multiple_pending(create_release_dependencies):
    """create_agent_version includes warning when other pending versions exist."""
    from api.routes.agent_versions import _create_agent_version
    from schemas.agent import AgentVersionCreateRequest

    owner_id = uuid.uuid4()
    agent = _make_agent(owner_id)
    user = _make_user()
    user.id = owner_id

    req = AgentVersionCreateRequest(
        version=SEMVER_VALID,
        description="Another release",
        prompt="You are helpful",
        model_name="claude-3-5-sonnet",
    )

    # DB: dup check returns None, pending count returns 2
    db = AsyncMock()
    dup_result = MagicMock()
    dup_result.scalar_one_or_none.return_value = None
    count_result = MagicMock()
    count_result.scalar.return_value = 2
    db.execute = AsyncMock(side_effect=[dup_result, count_result])
    db.commit = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    with (
        patch("api.routes.agent_versions._load_agent", new=AsyncMock(return_value=agent)),
        patch("api.routes.agent_versions.validate_component_ids", new=AsyncMock(return_value=[])),
        patch("api.routes.agent_versions.infer_required_features", return_value=[]),
        patch("api.routes.agent_versions.compute_supported_harnesses", return_value=[]),
        patch("api.routes.agent_versions.generate_agent_config", return_value={}),
        patch("api.routes.agent_versions.audit", new=AsyncMock()),
        patch("services.agent_snapshot.build_yaml_snapshot", new=AsyncMock(return_value="snapshot")),
    ):
        result = await _create_agent_version(
            agent_id=str(agent.id),
            req=req,
            db=db,
            current_user=user,
        )

    assert "warnings" in result
    assert "2 pending" in result["warnings"][0]


# ---------------------------------------------------------------------------
# 4. Review version
# ---------------------------------------------------------------------------


@pytest.fixture
def review_notification():
    # The inbox service's nested transaction is tested independently.
    with patch("api.routes.agent_versions.inbox.on_review_decided", new=AsyncMock(return_value=0)) as notify:
        yield notify


@pytest.mark.asyncio
async def test_review_version_approve_updates_latest(review_notification):
    """Approving a pending version sets agent.latest_version_id."""
    from api.routes.agent_versions import _review_agent_version
    from schemas.agent import AgentVersionReviewRequest

    owner_id = uuid.uuid4()
    agent = _make_agent(owner_id)
    ver = _make_version(agent.id, status=AgentStatus.pending)
    agent.latest_version = ver
    agent.latest_version_id = ver.id

    reviewer = _make_user("reviewer")
    req = AgentVersionReviewRequest(action="approve", reason=None)

    db = AsyncMock()
    ver_result = MagicMock()
    ver_result.scalar_one_or_none.return_value = ver
    db.execute = AsyncMock(return_value=ver_result)
    db.commit = AsyncMock()

    with (
        patch("api.routes.agent_versions._load_agent", new=AsyncMock(return_value=agent)),
        patch("api.routes.agent_versions.audit", new=AsyncMock()),
    ):
        result = await _review_agent_version(
            agent_id=str(agent.id),
            version=SEMVER_VALID,
            req=req,
            db=db,
            current_user=reviewer,
        )

    assert result["new_status"] == AgentStatus.approved.value
    review_notification.assert_awaited_once()
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_review_version_reject_stores_reason(review_notification):
    """Rejecting a pending version stores the rejection reason."""
    from api.routes.agent_versions import _review_agent_version
    from schemas.agent import AgentVersionReviewRequest

    owner_id = uuid.uuid4()
    agent = _make_agent(owner_id)
    ver = _make_version(agent.id, status=AgentStatus.pending)

    reviewer = _make_user("reviewer")
    req = AgentVersionReviewRequest(action="reject", reason="Prompt violates policy")

    db = AsyncMock()
    ver_result = MagicMock()
    ver_result.scalar_one_or_none.return_value = ver
    db.execute = AsyncMock(return_value=ver_result)
    db.commit = AsyncMock()

    with (
        patch("api.routes.agent_versions._load_agent", new=AsyncMock(return_value=agent)),
        patch("api.routes.agent_versions.audit", new=AsyncMock()),
    ):
        result = await _review_agent_version(
            agent_id=str(agent.id),
            version=SEMVER_VALID,
            req=req,
            db=db,
            current_user=reviewer,
        )

    assert result["new_status"] == AgentStatus.rejected.value
    assert ver.rejection_reason == "Prompt violates policy"
    review_notification.assert_awaited_once()
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_review_version_non_pending_422():
    """Reviewing a non-pending version raises 422."""
    from fastapi import HTTPException

    from api.routes.agent_versions import _review_agent_version
    from schemas.agent import AgentVersionReviewRequest

    owner_id = uuid.uuid4()
    agent = _make_agent(owner_id)
    ver = _make_version(agent.id, status=AgentStatus.approved)  # already approved

    reviewer = _make_user("reviewer")
    req = AgentVersionReviewRequest(action="approve", reason=None)

    db = AsyncMock()
    ver_result = MagicMock()
    ver_result.scalar_one_or_none.return_value = ver
    db.execute = AsyncMock(return_value=ver_result)

    with (
        patch("api.routes.agent_versions._load_agent", new=AsyncMock(return_value=agent)),
        pytest.raises(HTTPException) as exc,
    ):
        await _review_agent_version(
            agent_id=str(agent.id),
            version=SEMVER_VALID,
            req=req,
            db=db,
            current_user=reviewer,
        )

    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_review_version_not_found_404():
    """Reviewing a non-existent version raises 404."""
    from fastapi import HTTPException

    from api.routes.agent_versions import _review_agent_version
    from schemas.agent import AgentVersionReviewRequest

    agent = _make_agent()
    reviewer = _make_user("reviewer")
    req = AgentVersionReviewRequest(action="approve", reason=None)

    db = _db_returning_one(None)

    with (
        patch("api.routes.agent_versions._load_agent", new=AsyncMock(return_value=agent)),
        pytest.raises(HTTPException) as exc,
    ):
        await _review_agent_version(
            agent_id=str(agent.id),
            version="9.9.9",
            req=req,
            db=db,
            current_user=reviewer,
        )

    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# 5. harness config endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_harness_config_from_cache():
    """get_agent_harness_config returns cached harness configs when pre-generated."""
    from api.routes.agent_versions import _get_agent_harness_config

    agent = _make_agent()
    ver = _make_version(agent.id)
    ver.harness_configs = {"claude-code": {"mcpServers": {}}}
    db = _db_returning_one(ver)

    with patch("api.routes.agent_versions._load_agent", new=AsyncMock(return_value=agent)):
        result = await _get_agent_harness_config(
            agent_id=str(agent.id),
            version=SEMVER_VALID,
            harness="claude-code",
            db=db,
            current_user=_make_user(),
        )

    assert result == {"mcpServers": {}}


@pytest.mark.asyncio
async def test_get_harness_config_404_when_not_cached():
    """get_agent_harness_config raises 404 when harness config is not pre-generated."""
    from fastapi import HTTPException

    from api.routes.agent_versions import _get_agent_harness_config

    agent = _make_agent()
    ver = _make_version(agent.id)
    ver.harness_configs = {}  # no cached config for requested harness
    db = _db_returning_one(ver)

    with (
        patch("api.routes.agent_versions._load_agent", new=AsyncMock(return_value=agent)),
        pytest.raises(HTTPException) as exc,
    ):
        await _get_agent_harness_config(
            agent_id=str(agent.id),
            version=SEMVER_VALID,
            harness="claude-code",
            db=db,
            current_user=_make_user(),
        )

    assert exc.value.status_code == 404
    assert "claude-code" in exc.value.detail


# ---------------------------------------------------------------------------
# 6. YAML diff endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_diff_versions_returns_yaml_diff():
    """get_version_diff returns unified diff and component_changes."""
    from api.routes.agent_versions import _get_version_diff

    agent = _make_agent()
    ver1 = _make_version(agent.id, ver="1.0.0")
    ver1.yaml_snapshot = "prompt: hello\nmodel: claude\n"
    ver1.components = []
    ver2 = _make_version(agent.id, ver="1.1.0")
    ver2.yaml_snapshot = "prompt: hello world\nmodel: claude\n"
    ver2.components = []

    db = AsyncMock()
    calls = [0]
    statements = []

    async def _execute(stmt):
        calls[0] += 1
        statements.append(str(stmt))
        mock = MagicMock()
        mock.scalar_one_or_none.return_value = ver1 if calls[0] == 1 else ver2
        return mock

    db.execute = _execute

    with patch("api.routes.agent_versions._load_agent", new=AsyncMock(return_value=agent)):
        result = await _get_version_diff(
            agent_id=str(agent.id),
            v1="1.0.0",
            v2="1.1.0",
            db=db,
            current_user=_make_user(),
        )

    assert result["agent_id"] == str(agent.id)
    assert result["version_a"] == "1.0.0"
    assert result["version_b"] == "1.1.0"
    assert isinstance(result["yaml_diff"], str)
    assert len(result["yaml_diff"]) > 0
    assert isinstance(result["component_changes"], list)
    assert all("agent_versions.status" in statement for statement in statements)


@pytest.mark.asyncio
async def test_diff_versions_structural_when_no_snapshot():
    """get_version_diff falls back to structural diff when yaml_snapshot is None."""
    from api.routes.agent_versions import _get_version_diff

    agent = _make_agent()
    ver1 = _make_version(agent.id, ver="1.0.0")
    ver1.yaml_snapshot = None
    ver1.description = "Old description"
    ver1.prompt = "Old prompt"
    ver1.model_name = "claude-3-haiku"
    ver1.model_config_json = {}
    ver1.supported_harnesses = ["claude-code"]
    ver1.external_mcps = []
    ver1.components = []

    ver2 = _make_version(agent.id, ver="1.1.0")
    ver2.yaml_snapshot = None
    ver2.description = "New description"
    ver2.prompt = "New prompt"
    ver2.model_name = "claude-3-5-sonnet"
    ver2.model_config_json = {}
    ver2.supported_harnesses = ["claude-code"]
    ver2.external_mcps = []
    ver2.components = []

    calls = [0]

    async def _execute(stmt):
        calls[0] += 1
        mock = MagicMock()
        mock.scalar_one_or_none.return_value = ver1 if calls[0] == 1 else ver2
        return mock

    db = AsyncMock()
    db.execute = _execute

    with patch("api.routes.agent_versions._load_agent", new=AsyncMock(return_value=agent)):
        result = await _get_version_diff(
            agent_id=str(agent.id),
            v1="1.0.0",
            v2="1.1.0",
            db=db,
            current_user=_make_user(),
        )

    assert result["agent_id"] == str(agent.id)
    assert result["version_a"] == "1.0.0"
    assert result["version_b"] == "1.1.0"
    assert isinstance(result["yaml_diff"], str)
    assert isinstance(result["component_changes"], list)
