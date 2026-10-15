# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the component version API (factory-generated endpoints).

Tests are parametrized over all 5 component types.  They use MagicMock for the
DB session and FastAPI dependency overrides so no real database is needed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.mcp import ListingStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SEMVER_VALID = "1.2.3"
SEMVER_VALID_PRE = "1.2.3-beta.1"
SEMVER_INVALID = "not-a-version"


def _make_version(listing_id, ver=SEMVER_VALID, status=ListingStatus.pending, rejection_reason=None):
    v = MagicMock()
    v.id = uuid.uuid4()
    v.listing_id = listing_id
    v.version = ver
    v.description = "A test version"
    v.changelog = None
    v.status = status
    v.rejection_reason = rejection_reason
    v.download_count = 0
    v.supported_harnesses = []
    v.released_by = uuid.uuid4()
    v.released_at = datetime.now(UTC)
    v.created_at = datetime.now(UTC)
    # MCP/Sandbox extras (present but None for non-MCP types — safe to ignore)
    v.source_url = None
    v.source_ref = None
    v.resolved_sha = None
    return v


# The version routes resolve through api.deps.resolve_visible_listing, which calls
# the module-global resolve_listing inside api.deps. That is the only seam a patch
# can intercept: api.routes.component_versions no longer binds resolve_listing.
RESOLVE_SEAM = "api.deps.resolve_listing"


def _make_listing(owner_id, *, is_private=False, team_id=None):
    listing = MagicMock()
    listing.id = uuid.uuid4()
    listing.name = "test-listing"
    listing.submitted_by = owner_id
    listing.latest_version_id = None
    listing.versions = []
    listing.latest_version = None
    # Set explicitly: an unset MagicMock attribute is truthy, which would make
    # every listing look team-private to check_listing_visibility_async.
    listing.is_private = is_private
    listing.team_id = team_id
    listing.co_authors = []
    return listing


def _make_user(role_value="user"):
    from models.user import UserRole

    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = "test@example.com"
    user.role = UserRole(role_value)
    return user


# ---------------------------------------------------------------------------
# Import the factory directly so tests do not depend on the mounted app
# ---------------------------------------------------------------------------


def _get_factory():
    from api.routes.component_versions import create_version_router

    return create_version_router


# ---------------------------------------------------------------------------
# Semver validation helper tests (unit, no DB)
# ---------------------------------------------------------------------------


def test_semver_pattern_matches_valid():
    from api.routes.component_versions import SEMVER_RE

    assert SEMVER_RE.match(SEMVER_VALID)
    assert SEMVER_RE.match(SEMVER_VALID_PRE)
    assert SEMVER_RE.match("0.0.0")
    assert SEMVER_RE.match("10.20.300")


def test_semver_pattern_rejects_invalid():
    from api.routes.component_versions import SEMVER_RE

    assert not SEMVER_RE.match(SEMVER_INVALID)
    assert not SEMVER_RE.match("1.2")
    assert not SEMVER_RE.match("1.2.3.4")
    assert not SEMVER_RE.match("")
    assert not SEMVER_RE.match("v1.2.3")


# ---------------------------------------------------------------------------
# Shared DB mock helpers
# ---------------------------------------------------------------------------


def _db_with_versions(versions: list):
    """Return an async DB mock that returns *versions* for the versions query."""
    db = AsyncMock()

    # First call: versions query (scalars().all())
    scalars = MagicMock()
    scalars.all.return_value = versions
    versions_result = MagicMock()
    versions_result.scalars.return_value = scalars

    # Second call: count query (.scalar())
    count_result = MagicMock()
    count_result.scalar.return_value = len(versions)

    db.execute = AsyncMock(side_effect=[versions_result, count_result])
    db.commit = AsyncMock()
    return db


def _db_returning_one(obj):
    """Return an async DB mock whose first execute returns *obj* as a scalar."""
    db = AsyncMock()

    scalar_result = MagicMock()
    scalar_result.scalar_one_or_none.return_value = obj

    scalars = MagicMock()
    scalars.first.return_value = obj
    scalars.all.return_value = [] if obj is None else [obj]

    wrapped = MagicMock()
    wrapped.scalars.return_value = scalars
    wrapped.scalar_one_or_none.return_value = obj

    db.execute = AsyncMock(return_value=wrapped)
    db.commit = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# Tests for list_versions endpoint function
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_versions_empty():
    """list_versions returns empty list when no versions exist."""
    from api.routes.component_versions import _list_versions
    from models.mcp import McpListing, McpVersion

    listing_id = str(uuid.uuid4())
    db = _db_with_versions([])

    with patch(RESOLVE_SEAM, new=AsyncMock(return_value=_make_listing(uuid.uuid4()))):
        result = await _list_versions(
            listing_id=listing_id,
            page=1,
            page_size=20,
            listing_model=McpListing,
            version_model=McpVersion,
            component_type="mcp",
            db=db,
            current_user=_make_user(),
        )

    assert result["items"] == []
    assert result["total"] == 0


@pytest.mark.asyncio
async def test_list_versions_with_data():
    """list_versions returns version data with pagination metadata."""
    from api.routes.component_versions import _list_versions
    from models.mcp import McpListing, McpVersion

    owner_id = uuid.uuid4()
    listing = _make_listing(owner_id)
    ver = _make_version(listing.id)
    listing.versions = [ver]

    listing_id = str(listing.id)
    db = _db_with_versions([ver])

    with patch(RESOLVE_SEAM, new=AsyncMock(return_value=listing)):
        result = await _list_versions(
            listing_id=listing_id,
            page=1,
            page_size=20,
            listing_model=McpListing,
            version_model=McpVersion,
            component_type="mcp",
            db=db,
            current_user=_make_user(),
        )

    assert result["total"] == 1
    assert result["items"][0]["version"] == SEMVER_VALID


# ---------------------------------------------------------------------------
# Tests for get_version endpoint function
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_version_found():
    """get_version returns version detail when found."""
    from api.routes.component_versions import _get_version
    from models.mcp import McpListing, McpVersion

    owner_id = uuid.uuid4()
    listing = _make_listing(owner_id)
    ver = _make_version(listing.id)

    db = _db_returning_one(ver)

    with patch(RESOLVE_SEAM, new=AsyncMock(return_value=listing)):
        result = await _get_version(
            listing_id=str(listing.id),
            version=SEMVER_VALID,
            listing_model=McpListing,
            version_model=McpVersion,
            component_type="mcp",
            db=db,
            current_user=_make_user(),
        )

    assert result["version"] == SEMVER_VALID
    assert result["id"] == str(ver.id)


@pytest.mark.asyncio
async def test_get_version_not_found():
    """get_version raises 404 when version does not exist."""
    from fastapi import HTTPException

    from api.routes.component_versions import _get_version
    from models.mcp import McpListing, McpVersion

    owner_id = uuid.uuid4()
    listing = _make_listing(owner_id)

    db = _db_returning_one(None)

    with (
        patch(RESOLVE_SEAM, new=AsyncMock(return_value=listing)),
        pytest.raises(HTTPException) as exc,
    ):
        await _get_version(
            listing_id=str(listing.id),
            version="9.9.9",
            listing_model=McpListing,
            version_model=McpVersion,
            component_type="mcp",
            db=db,
            current_user=_make_user(),
        )

    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Tests for publish_version endpoint function
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_version_bad_semver():
    """publish_version rejects invalid semver strings with 422."""
    from fastapi import HTTPException

    from api.routes.component_versions import _publish_version
    from models.mcp import McpListing, McpVersion
    from schemas.component_version import VersionPublishRequest

    owner_id = uuid.uuid4()
    listing = _make_listing(owner_id)
    user = _make_user()
    user.id = owner_id

    req = VersionPublishRequest(version=SEMVER_INVALID, description="test", changelog=None, extra=None)

    with (
        patch(RESOLVE_SEAM, new=AsyncMock(return_value=listing)),
        pytest.raises(HTTPException) as exc,
    ):
        await _publish_version(
            listing_id=str(listing.id),
            req=req,
            listing_model=McpListing,
            version_model=McpVersion,
            component_type="mcp",
            db=AsyncMock(),
            current_user=user,
        )

    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_publish_version_not_owner():
    """publish_version returns 403 if user is not the listing owner."""
    from fastapi import HTTPException

    from api.routes.component_versions import _publish_version
    from models.mcp import McpListing, McpVersion
    from schemas.component_version import VersionPublishRequest

    owner_id = uuid.uuid4()
    listing = _make_listing(owner_id)
    other_user = _make_user()
    other_user.id = uuid.uuid4()  # different from owner

    req = VersionPublishRequest(version=SEMVER_VALID, description="test", changelog=None, extra=None)

    with (
        patch(RESOLVE_SEAM, new=AsyncMock(return_value=listing)),
        pytest.raises(HTTPException) as exc,
    ):
        await _publish_version(
            listing_id=str(listing.id),
            req=req,
            listing_model=McpListing,
            version_model=McpVersion,
            component_type="mcp",
            db=AsyncMock(),
            current_user=other_user,
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_publish_version_duplicate_409():
    """publish_version returns 409 if (listing_id, version) already exists."""
    from fastapi import HTTPException

    from api.routes.component_versions import _publish_version
    from models.mcp import McpListing, McpVersion
    from schemas.component_version import VersionPublishRequest

    owner_id = uuid.uuid4()
    listing = _make_listing(owner_id)
    user = _make_user()
    user.id = owner_id

    existing_ver = _make_version(listing.id)

    req = VersionPublishRequest(version=SEMVER_VALID, description="test", changelog=None, extra=None)

    # DB returns an existing version (duplicate)
    db = _db_returning_one(existing_ver)

    with (
        patch(RESOLVE_SEAM, new=AsyncMock(return_value=listing)),
        pytest.raises(HTTPException) as exc,
    ):
        await _publish_version(
            listing_id=str(listing.id),
            req=req,
            listing_model=McpListing,
            version_model=McpVersion,
            component_type="mcp",
            db=db,
            current_user=user,
        )

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_publish_version_happy_path():
    """publish_version creates a new version and returns 201-like response."""
    from api.routes.component_versions import _publish_version
    from models.mcp import McpListing, McpVersion
    from schemas.component_version import VersionPublishRequest

    owner_id = uuid.uuid4()
    listing = _make_listing(owner_id)
    user = _make_user()
    user.id = owner_id

    req = VersionPublishRequest(
        version=SEMVER_VALID, description="New version desc", changelog="Added stuff", extra=None
    )

    # DB returns None for the duplicate check; AsyncSession.add is synchronous.
    db = AsyncMock()
    dup_result = MagicMock()
    dup_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=dup_result)
    db.add = MagicMock()
    db.commit = AsyncMock()

    with (
        patch(RESOLVE_SEAM, new=AsyncMock(return_value=listing)),
        patch("api.routes.component_versions.audit", new=AsyncMock()),
    ):
        result = await _publish_version(
            listing_id=str(listing.id),
            req=req,
            listing_model=McpListing,
            version_model=McpVersion,
            component_type="mcp",
            db=db,
            current_user=user,
        )

    assert result["version"] == SEMVER_VALID
    assert result["status"] == ListingStatus.pending.value
    db.add.assert_called_once()
    db.commit.assert_called_once()


# ---------------------------------------------------------------------------
# Tests for review_version endpoint function
# ---------------------------------------------------------------------------


@pytest.fixture
def review_notification():
    # The inbox delivery service has its own integration tests; a generic
    # AsyncMock session cannot represent its nested SQLAlchemy transaction.
    with patch("services.inbox.sources.on_review_decided", new=AsyncMock(return_value=0)) as notify:
        yield notify


@pytest.mark.asyncio
async def test_review_version_approve_updates_latest(review_notification):
    """Approving a pending version sets listing.latest_version_id."""
    from api.routes.component_versions import _review_version
    from models.mcp import McpListing, McpVersion
    from schemas.component_version import VersionReviewRequest

    owner_id = uuid.uuid4()
    listing = _make_listing(owner_id)
    ver = _make_version(listing.id, status=ListingStatus.pending)
    listing.latest_version = ver

    reviewer = _make_user("reviewer")
    req = VersionReviewRequest(action="approve", reason=None)

    db = AsyncMock()
    ver_result = MagicMock()
    ver_result.scalar_one_or_none.return_value = ver
    db.execute = AsyncMock(return_value=ver_result)
    db.commit = AsyncMock()

    with (
        patch(RESOLVE_SEAM, new=AsyncMock(return_value=listing)),
        patch("api.routes.component_versions.audit", new=AsyncMock()),
    ):
        result = await _review_version(
            listing_id=str(listing.id),
            version=SEMVER_VALID,
            req=req,
            listing_model=McpListing,
            version_model=McpVersion,
            component_type="mcp",
            db=db,
            current_user=reviewer,
        )

    assert result["new_status"] == ListingStatus.approved.value
    assert listing.latest_version_id == ver.id
    review_notification.assert_awaited_once()
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_review_version_reject_stores_reason(review_notification):
    """Rejecting a pending version stores the rejection reason."""
    from api.routes.component_versions import _review_version
    from models.mcp import McpListing, McpVersion
    from schemas.component_version import VersionReviewRequest

    owner_id = uuid.uuid4()
    listing = _make_listing(owner_id)
    ver = _make_version(listing.id, status=ListingStatus.pending)

    reviewer = _make_user("reviewer")
    req = VersionReviewRequest(action="reject", reason="Not acceptable")

    db = AsyncMock()
    ver_result = MagicMock()
    ver_result.scalar_one_or_none.return_value = ver
    db.execute = AsyncMock(return_value=ver_result)
    db.commit = AsyncMock()

    with (
        patch(RESOLVE_SEAM, new=AsyncMock(return_value=listing)),
        patch("api.routes.component_versions.audit", new=AsyncMock()),
    ):
        result = await _review_version(
            listing_id=str(listing.id),
            version=SEMVER_VALID,
            req=req,
            listing_model=McpListing,
            version_model=McpVersion,
            component_type="mcp",
            db=db,
            current_user=reviewer,
        )

    assert result["new_status"] == ListingStatus.rejected.value
    assert ver.rejection_reason == "Not acceptable"
    review_notification.assert_awaited_once()
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_review_version_non_pending_422():
    """Reviewing a non-pending version raises 422."""
    from fastapi import HTTPException

    from api.routes.component_versions import _review_version
    from models.mcp import McpListing, McpVersion
    from schemas.component_version import VersionReviewRequest

    owner_id = uuid.uuid4()
    listing = _make_listing(owner_id)
    ver = _make_version(listing.id, status=ListingStatus.approved)  # already approved

    reviewer = _make_user("reviewer")
    req = VersionReviewRequest(action="approve", reason=None)

    db = AsyncMock()
    ver_result = MagicMock()
    ver_result.scalar_one_or_none.return_value = ver
    db.execute = AsyncMock(return_value=ver_result)

    with (
        patch(RESOLVE_SEAM, new=AsyncMock(return_value=listing)),
        pytest.raises(HTTPException) as exc,
    ):
        await _review_version(
            listing_id=str(listing.id),
            version=SEMVER_VALID,
            req=req,
            listing_model=McpListing,
            version_model=McpVersion,
            component_type="mcp",
            db=db,
            current_user=reviewer,
        )

    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_review_version_not_found_404():
    """Reviewing a non-existent version raises 404."""
    from fastapi import HTTPException

    from api.routes.component_versions import _review_version
    from models.mcp import McpListing, McpVersion
    from schemas.component_version import VersionReviewRequest

    owner_id = uuid.uuid4()
    listing = _make_listing(owner_id)

    reviewer = _make_user("reviewer")
    req = VersionReviewRequest(action="approve", reason=None)

    db = AsyncMock()
    ver_result = MagicMock()
    ver_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=ver_result)

    with (
        patch(RESOLVE_SEAM, new=AsyncMock(return_value=listing)),
        pytest.raises(HTTPException) as exc,
    ):
        await _review_version(
            listing_id=str(listing.id),
            version="9.9.9",
            req=req,
            listing_model=McpListing,
            version_model=McpVersion,
            component_type="mcp",
            db=db,
            current_user=reviewer,
        )

    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Factory creates router for each component type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "component_type,listing_cls,version_cls",
    [
        ("mcp", "models.mcp.McpListing", "models.mcp.McpVersion"),
        ("skill", "models.skill.SkillListing", "models.skill.SkillVersion"),
        ("hook", "models.hook.HookListing", "models.hook.HookVersion"),
        ("prompt", "models.prompt.PromptListing", "models.prompt.PromptVersion"),
        ("sandbox", "models.sandbox.SandboxListing", "models.sandbox.SandboxVersion"),
    ],
)
def test_factory_creates_router(component_type, listing_cls, version_cls):
    """create_version_router returns an APIRouter for each component type."""
    import importlib

    from fastapi import APIRouter

    from api.routes.component_versions import create_version_router

    mod_path, cls_name = listing_cls.rsplit(".", 1)
    listing_model = getattr(importlib.import_module(mod_path), cls_name)

    mod_path, cls_name = version_cls.rsplit(".", 1)
    version_model = getattr(importlib.import_module(mod_path), cls_name)

    router = create_version_router(component_type, listing_model, version_model)
    assert isinstance(router, APIRouter)

    # Should have 4 routes (list, get, publish, review)
    routes = router.routes
    assert len(routes) == 5


def test_factory_route_paths():
    """Factory routes match expected URL patterns."""
    from api.routes.component_versions import create_version_router
    from models.mcp import McpListing, McpVersion

    router = create_version_router("mcp", McpListing, McpVersion)
    paths = {r.path for r in router.routes}

    assert "/{listing_id}/versions" in paths
    assert "/{listing_id}/versions/{version}" in paths
