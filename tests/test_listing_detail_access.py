# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for listing detail endpoint access control.

Verifies that GET /{listing_id} endpoints for all 5 registry types
enforce status-based visibility:
- Unauthenticated: only approved listings visible
- Owner: any status visible
- Admin/reviewer: any status visible
- Non-owner regular user: only approved listings visible
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.deps import get_db, get_registry_user
from models.mcp import ListingStatus
from models.user import User, UserRole

# ── Helpers ──────────────────────────────────────────────


def _user(role=UserRole.user, user_id=None, **kw):
    u = MagicMock(spec=User)
    u.id = user_id or uuid.uuid4()
    u.role = role
    u.email = kw.get("email", "test@example.com")
    u.username = kw.get("username", "testuser")
    return u


def _mock_db(membership=None):
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.delete = AsyncMock()
    # check_listing_visibility_async resolves team membership with db.scalar.
    # A bare AsyncMock returns a truthy sentinel, which would make every caller
    # look like a team member, so the membership row is always explicit here.
    db.scalar = AsyncMock(return_value=membership)
    # Fork-count reads use a grouped execute; this fixture has no fork rows.
    db.execute.return_value.all = MagicMock(return_value=[])
    return db


def _app_with(router, user=None, membership=None):
    db = _mock_db(membership)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    if user is not None:
        app.dependency_overrides[get_registry_user] = lambda: user
    else:
        app.dependency_overrides[get_registry_user] = lambda: None
    return app


def _listing_mock(status=ListingStatus.approved, submitted_by=None, is_private=False, team_id=None):
    m = MagicMock()
    m.id = uuid.uuid4()
    m.latest_version_id = uuid.uuid4()
    m.name = "test-listing"
    m.namespace = "testowner"
    m.slug = "test-listing"
    m.qualified_name = "testowner/test-listing"
    m.version = "1.0.0"
    m.description = "A test listing"
    m.owner = "testowner"
    m.status = status
    m.rejection_reason = None
    m.forked_from = None
    m.is_fork = False
    m.submitted_by = submitted_by or uuid.uuid4()
    m.co_authors = []
    # Privacy is a separate axis from status: set it explicitly so these tests
    # exercise status gating only. A bare MagicMock attribute is truthy, which
    # would silently make every listing look team-private.
    m.is_private = is_private
    m.team_id = team_id
    m.visibility = "team" if is_private else "public"
    m.supported_harnesses = []
    m.created_at = datetime(2025, 1, 1, tzinfo=UTC)
    m.updated_at = datetime(2025, 1, 1, tzinfo=UTC)
    m.category = "general"
    m.git_url = None
    m.command = None
    m.args = None
    m.url = None
    m.headers = None
    m.auto_approve = []
    m.transport = None
    m.framework = None
    m.docker_image = None
    m.mcp_validated = False
    m.changelog = None
    m.setup_instructions = None
    m.environment_variables = []
    m.custom_fields = []
    m.validation_results = []
    m.download_count = 0
    m.unique_users = 0
    m.template = "Hello {{ name }}"
    m.variables = []
    m.model_hints = []
    m.tags = []
    m.task_type = "code-review"
    m.target_agents = []
    m.skill_path = "/"
    m.git_ref = None
    m.skill_md_content = None
    m.delivery_mode = "git_fetch"
    m.script_content = None
    m.script_filename = None
    m.validated = False
    m.slash_command = None
    m.event = "PreToolUse"
    m.execution_mode = "blocking"
    m.priority = 0
    m.handler_type = "command"
    m.handler_config = {}
    m.input_schema = None
    m.output_schema = None
    m.scope = "project"
    m.tool_filter = None
    m.file_pattern = None
    m.runtime_type = "docker"
    m.image = "python:3.11"
    m.dockerfile_url = None
    m.resource_limits = {}
    m.network_policy = "none"
    m.allowed_mounts = []
    m.env_vars = []
    m.entrypoint = None
    return m


@pytest.fixture(autouse=True)
def mock_selected_skill_version(monkeypatch):
    """These listing-scope tests mock the listing lookup, not version-row locks.

    Exact-version authorization is exercised with a real DB in
    test_skill_file_access; this fixture keeps older listing-only mocks focused
    on their intended access boundary.
    """
    from api.routes import skill

    async def selected(_listing_id, version_id, _db, _user):
        row = MagicMock()
        row.id = version_id
        listing = await skill.resolve_visible_listing(
            skill.SkillListing, _listing_id, _db, _user, load_options=skill._BODY_FREE_LISTING
        )
        return listing, row

    monkeypatch.setattr(skill, "_authorized_version", selected)


# ── Endpoint configs for parametrization ─────────────────

ENDPOINTS = [
    ("mcp", "/api/v1/mcps"),
    ("prompt", "/api/v1/prompts"),
    ("skill", "/api/v1/skills"),
    ("hook", "/api/v1/hooks"),
    ("sandbox", "/api/v1/sandboxes"),
]

# The route modules resolve listings through api.deps.resolve_visible_listing,
# which calls the module-global resolve_listing inside api.deps. That is the only
# seam a patch can intercept; patching api.routes.<type>.resolve_listing rebinds a
# name the detail handlers never call.
RESOLVE_SEAM = "api.deps.resolve_listing"


def _get_router(route_type):
    if route_type == "mcp":
        from api.routes.mcp import router
    elif route_type == "prompt":
        from api.routes.prompt import router
    elif route_type == "skill":
        from api.routes.skill import router
    elif route_type == "hook":
        from api.routes.hook import router
    elif route_type == "sandbox":
        from api.routes.sandbox import router
    else:
        raise ValueError(f"Unknown route type: {route_type}")
    return router


# ── Tests ────────────────────────────────────────────────


@pytest.mark.parametrize("route_type,base_path", ENDPOINTS)
class TestUnauthenticatedAccess:
    """Unauthenticated users can only see approved listings."""

    @pytest.mark.asyncio
    async def test_sees_approved(self, route_type, base_path):
        router = _get_router(route_type)
        listing = _listing_mock(status=ListingStatus.approved)
        app = _app_with(router, user=None)

        with patch(RESOLVE_SEAM, new_callable=AsyncMock) as mock_resolve:
            mock_resolve.return_value = listing
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.get(f"{base_path}/{listing.id}")
            assert r.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "status",
        [ListingStatus.draft, ListingStatus.pending, ListingStatus.rejected, ListingStatus.archived],
    )
    async def test_blocked_from_non_approved(self, route_type, base_path, status):
        router = _get_router(route_type)
        listing = _listing_mock(status=status)
        app = _app_with(router, user=None)

        with patch(RESOLVE_SEAM, new_callable=AsyncMock) as mock_resolve:
            mock_resolve.side_effect = [None, listing]
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.get(f"{base_path}/{listing.id}")
            assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_nonexistent_returns_404(self, route_type, base_path):
        router = _get_router(route_type)
        app = _app_with(router, user=None)

        with patch(RESOLVE_SEAM, new_callable=AsyncMock) as mock_resolve:
            mock_resolve.side_effect = [None, None]
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.get(f"{base_path}/{uuid.uuid4()}")
            assert r.status_code == 404


@pytest.mark.parametrize("route_type,base_path", ENDPOINTS)
class TestOwnerAccess:
    """Listing owners can see their own listings in any status."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "status",
        [ListingStatus.draft, ListingStatus.pending, ListingStatus.rejected],
    )
    async def test_owner_sees_own_non_approved(self, route_type, base_path, status):
        owner = _user()
        router = _get_router(route_type)
        listing = _listing_mock(status=status, submitted_by=owner.id)
        app = _app_with(router, user=owner)

        with patch(RESOLVE_SEAM, new_callable=AsyncMock) as mock_resolve:
            if route_type == "skill":
                mock_resolve.return_value = listing
            else:
                mock_resolve.side_effect = [None, listing]
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.get(f"{base_path}/{listing.id}")
            assert r.status_code == 200


@pytest.mark.parametrize("route_type,base_path", ENDPOINTS)
class TestNonOwnerRegularUser:
    """Non-owner regular users cannot see non-approved listings."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "status",
        [ListingStatus.draft, ListingStatus.pending],
    )
    async def test_blocked_from_others_non_approved(self, route_type, base_path, status):
        other_user = _user()
        router = _get_router(route_type)
        listing = _listing_mock(status=status)
        app = _app_with(router, user=other_user)

        with patch(RESOLVE_SEAM, new_callable=AsyncMock) as mock_resolve:
            mock_resolve.side_effect = [None, listing]
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.get(f"{base_path}/{listing.id}")
            assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_sees_approved(self, route_type, base_path):
        other_user = _user()
        router = _get_router(route_type)
        listing = _listing_mock(status=ListingStatus.approved)
        app = _app_with(router, user=other_user)

        with patch(RESOLVE_SEAM, new_callable=AsyncMock) as mock_resolve:
            mock_resolve.return_value = listing
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.get(f"{base_path}/{listing.id}")
            assert r.status_code == 200


@pytest.mark.parametrize("route_type,base_path", ENDPOINTS)
class TestPrivilegedAccess:
    """Admins and reviewers can see any listing in any status."""

    @pytest.mark.asyncio
    async def test_reviewer_sees_pending(self, route_type, base_path):
        reviewer = _user(role=UserRole.reviewer)
        router = _get_router(route_type)
        listing = _listing_mock(status=ListingStatus.pending)
        app = _app_with(router, user=reviewer)

        with patch(RESOLVE_SEAM, new_callable=AsyncMock) as mock_resolve:
            if route_type == "skill":
                mock_resolve.return_value = listing
            else:
                mock_resolve.side_effect = [None, listing]
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.get(f"{base_path}/{listing.id}")
            assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_admin_sees_draft(self, route_type, base_path):
        admin = _user(role=UserRole.admin)
        router = _get_router(route_type)
        listing = _listing_mock(status=ListingStatus.draft)
        app = _app_with(router, user=admin)

        with patch(RESOLVE_SEAM, new_callable=AsyncMock) as mock_resolve:
            if route_type == "skill":
                mock_resolve.return_value = listing
            else:
                mock_resolve.side_effect = [None, listing]
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.get(f"{base_path}/{listing.id}")
            assert r.status_code == 200


# ── Privacy is a different axis from status ──────────────
#
# Status decides whether an item is ready to be shown; privacy decides who the
# item belongs to. The detail routes run both gates, and only the status one
# admits a global reviewer. A reviewer reviews the PUBLIC catalog, so a
# team-private listing has to answer the membership question for them exactly as
# it does for any other user.


@pytest.mark.parametrize("route_type,base_path", ENDPOINTS)
class TestTeamPrivateAccess:
    """A team-private listing is readable through membership or an admin role only."""

    @staticmethod
    async def _get(route_type, base_path, user, *, membership, status=ListingStatus.approved):
        router = _get_router(route_type)
        listing = _listing_mock(status=status, is_private=True, team_id=uuid.uuid4())
        app = _app_with(router, user=user, membership=membership)

        with patch(RESOLVE_SEAM, new_callable=AsyncMock) as mock_resolve:
            mock_resolve.return_value = listing
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                return await ac.get(f"{base_path}/{listing.id}")

    @pytest.mark.asyncio
    async def test_global_reviewer_outside_the_team_is_denied(self, route_type, base_path):
        reviewer = _user(role=UserRole.reviewer)

        r = await self._get(route_type, base_path, reviewer, membership=None)

        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_global_reviewer_outside_the_team_is_denied_for_pending_too(self, route_type, base_path):
        """The status gate must not hand a reviewer a private item it cannot own.

        ``may_view_unapproved`` says yes to a reviewer for any pending item, so the
        404 here proves the privacy gate runs first and independently.
        """
        reviewer = _user(role=UserRole.reviewer)

        r = await self._get(route_type, base_path, reviewer, membership=None, status=ListingStatus.pending)

        assert r.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.parametrize("role", [UserRole.admin, UserRole.super_admin])
    async def test_admins_still_read_it(self, route_type, base_path, role):
        r = await self._get(route_type, base_path, _user(role=role), membership=None)

        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_team_member_reads_it(self, route_type, base_path):
        r = await self._get(route_type, base_path, _user(), membership=uuid.uuid4())

        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_reviewer_who_is_a_team_member_reads_it(self, route_type, base_path):
        """Membership admits the reviewer; the global role never did."""
        reviewer = _user(role=UserRole.reviewer)

        r = await self._get(route_type, base_path, reviewer, membership=uuid.uuid4())

        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_reviewer_still_reads_a_pending_public_listing(self, route_type, base_path):
        """The mirror case: narrowing privacy must leave the review queue working."""
        reviewer = _user(role=UserRole.reviewer)
        router = _get_router(route_type)
        listing = _listing_mock(status=ListingStatus.pending)
        app = _app_with(router, user=reviewer, membership=None)

        with patch(RESOLVE_SEAM, new_callable=AsyncMock) as mock_resolve:
            if route_type == "skill":
                mock_resolve.return_value = listing
            else:
                mock_resolve.side_effect = [None, listing]
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.get(f"{base_path}/{listing.id}")

        assert r.status_code == 200
