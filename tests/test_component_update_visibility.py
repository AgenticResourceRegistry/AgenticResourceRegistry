# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for visibility and teamspace fields on the component update routes.

PUT /api/v1/{type}/{listing_id}/draft accepts `visibility` and `team_id` because the
five *UpdateRequest schemas declare them. The route must never silently discard them.
Visibility has one authoritative path, PATCH /api/v1/registry/{item_type}/{listing_id}/visibility,
which carries the audit metadata and the guard that blocks privatizing a component an
approved public agent depends on, so the update route rejects a real change and points
at that endpoint. Repeating the values a listing already holds is a no-op and succeeds.

All five component types are covered by the same parametrized cases: no per-type behavior.
"""

from __future__ import annotations

import uuid
from contextlib import ExitStack
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.deps import get_current_user, get_db
from models.mcp import ListingStatus
from models.user import User, UserRole

# ── Helpers ──────────────────────────────────────────────


def _user(role=UserRole.user, user_id=None):
    u = MagicMock(spec=User)
    u.id = user_id or uuid.uuid4()
    u.role = role
    u.email = "test@example.com"
    u.username = "testuser"
    return u


def _mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    db.delete = AsyncMock()
    return db


def _app_with(router, user):
    db = _mock_db()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    return app, db


def _version_mock():
    """A latest_version row that holds no edit lock, so saves are not blocked."""
    v = MagicMock()
    v.id = uuid.uuid4()
    v.is_editing = False
    v.editing_by = None
    v.editing_since = None
    v.description = "original description"
    v.status = ListingStatus.draft
    v.content_revision = None
    v.base_version_id = None
    v.review_epoch = 0
    v.delivery_mode = "git_fetch"
    v.skill_md_content = None
    v.script_content = None
    v.script_filename = None
    v.extra_files = []
    return v


def _listing_mock(submitted_by, *, is_private=False, team_id=None):
    m = MagicMock()
    m.id = uuid.uuid4()
    m.name = "test-listing"
    m.namespace = "testowner"
    m.slug = "test-listing"
    m.qualified_name = "testowner/test-listing"
    m.version = "1.0.0"
    m.description = "A test listing"
    m.owner = "testowner"
    m.status = ListingStatus.draft
    m.rejection_reason = None
    m.forked_from = None
    m.is_fork = False
    m.submitted_by = submitted_by
    m.co_authors = []
    # Visibility is the axis under test: set both halves explicitly. A bare MagicMock
    # attribute is truthy, which would make every listing look team-private.
    m.is_private = is_private
    m.team_id = team_id
    m.visibility = "team" if is_private else "public"
    m.latest_version = _version_mock()
    m.latest_version_id = m.latest_version.id
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
    m.requirements = []
    m.source_url = None
    m.source_ref = None
    m.source_path = None
    m.runtime_type = "docker"
    m.image = "python:3.11"
    m.dockerfile_url = None
    m.resource_limits = {}
    m.runtime_config = {}
    m.network_policy = "none"
    m.allowed_mounts = []
    m.env_vars = []
    m.entrypoint = None
    m.sandbox_path = None
    return m


# ── Endpoint configs for parametrization ─────────────────

# The registry item_type doubles as the route module name (api.routes.<item_type>) and as
# the path segment of the authoritative visibility endpoint.
ENDPOINTS = [
    ("mcp", "/api/v1/mcps"),
    ("skill", "/api/v1/skills"),
    ("hook", "/api/v1/hooks"),
    ("prompt", "/api/v1/prompts"),
    ("sandbox", "/api/v1/sandboxes"),
]


def _get_router(item_type):
    import importlib

    return importlib.import_module(f"api.routes.{item_type}").router


async def _put_draft(item_type, base_path, listing, user, body):
    """PUT the update route with resolve_listing pinned to the given listing."""
    app, db = _app_with(_get_router(item_type), user)
    module = f"api.routes.{item_type}"
    with ExitStack() as stack:
        mock_resolve = stack.enter_context(patch(f"{module}.resolve_listing", new_callable=AsyncMock))
        mock_resolve.return_value = listing
        if item_type == "skill":
            stack.enter_context(
                patch(
                    "api.routes.skill.lock_skill_version",
                    new_callable=AsyncMock,
                    return_value=(listing.latest_version_id, listing.latest_version),
                )
            )
            stack.enter_context(patch("api.routes.skill._recheck_skill_owner", new_callable=AsyncMock))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.put(f"{base_path}/{listing.id}/draft", json=body)
    return r, db


# ── Tests ────────────────────────────────────────────────


@pytest.mark.parametrize("item_type,base_path", ENDPOINTS)
class TestVisibilityChangeRejected:
    """A visibility change is refused and points at the authoritative endpoint."""

    @pytest.mark.asyncio
    async def test_public_to_team_rejected(self, item_type, base_path):
        owner = _user()
        listing = _listing_mock(owner.id)
        r, db = await _put_draft(item_type, base_path, listing, owner, {"visibility": "team"})

        assert r.status_code == 400
        detail = r.json()["detail"]
        assert f"PATCH /api/v1/registry/{item_type}/{listing.id}/visibility" in detail
        assert listing.is_private is False
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_team_to_public_rejected(self, item_type, base_path):
        owner = _user()
        team_id = uuid.uuid4()
        listing = _listing_mock(owner.id, is_private=True, team_id=team_id)
        r, db = await _put_draft(item_type, base_path, listing, owner, {"visibility": "public"})

        assert r.status_code == 400
        assert f"PATCH /api/v1/registry/{item_type}/{listing.id}/visibility" in r.json()["detail"]
        assert listing.is_private is True
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_admin_is_not_exempt(self, item_type, base_path):
        """Role does not open a second visibility path: admins use the same endpoint."""
        admin = _user(role=UserRole.admin)
        listing = _listing_mock(uuid.uuid4())
        r, db = await _put_draft(item_type, base_path, listing, admin, {"visibility": "team"})

        assert r.status_code == 400
        assert listing.is_private is False
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rejected_before_other_fields_are_applied(self, item_type, base_path):
        """A rejected request must not half-apply the rest of the payload."""
        owner = _user()
        listing = _listing_mock(owner.id)
        ver = listing.latest_version
        r, db = await _put_draft(
            item_type,
            base_path,
            listing,
            owner,
            {"visibility": "team", "description": "should not be saved"},
        )

        assert r.status_code == 400
        assert ver.description == "original description"
        db.commit.assert_not_awaited()


@pytest.mark.parametrize("item_type,base_path", ENDPOINTS)
class TestTeamIdChangeRejected:
    """A teamspace move is refused: no endpoint reassigns a listing's teamspace."""

    @pytest.mark.asyncio
    async def test_personal_listing_cannot_join_a_team(self, item_type, base_path):
        owner = _user()
        listing = _listing_mock(owner.id)
        r, db = await _put_draft(item_type, base_path, listing, owner, {"team_id": str(uuid.uuid4())})

        assert r.status_code == 400
        assert "team_id cannot be changed here" in r.json()["detail"]
        assert listing.team_id is None
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_team_listing_cannot_move_to_another_team(self, item_type, base_path):
        owner = _user()
        team_id = uuid.uuid4()
        listing = _listing_mock(owner.id, is_private=True, team_id=team_id)
        r, db = await _put_draft(item_type, base_path, listing, owner, {"team_id": str(uuid.uuid4())})

        assert r.status_code == 400
        assert "team_id cannot be changed here" in r.json()["detail"]
        assert listing.team_id == team_id
        db.commit.assert_not_awaited()


@pytest.mark.parametrize("item_type,base_path", ENDPOINTS)
class TestUnchangedUpdatesSucceed:
    """Edits that do not move visibility still save, including echoed current values."""

    @pytest.mark.asyncio
    async def test_plain_update_succeeds(self, item_type, base_path):
        owner = _user()
        listing = _listing_mock(owner.id)
        ver = listing.latest_version
        r, db = await _put_draft(item_type, base_path, listing, owner, {"description": "updated description"})

        assert r.status_code == 200, r.text
        assert ver.description == "updated description"
        db.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_echoed_public_visibility_is_a_noop(self, item_type, base_path):
        """The web edit form resends the listing's current visibility on every save."""
        owner = _user()
        listing = _listing_mock(owner.id)
        ver = listing.latest_version
        r, db = await _put_draft(
            item_type,
            base_path,
            listing,
            owner,
            {"visibility": "public", "description": "updated description"},
        )

        assert r.status_code == 200, r.text
        assert ver.description == "updated description"
        assert listing.is_private is False
        db.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_echoed_team_visibility_and_team_id_is_a_noop(self, item_type, base_path):
        owner = _user()
        team_id = uuid.uuid4()
        listing = _listing_mock(owner.id, is_private=True, team_id=team_id)
        ver = listing.latest_version
        r, db = await _put_draft(
            item_type,
            base_path,
            listing,
            owner,
            {"visibility": "team", "team_id": str(team_id), "description": "updated description"},
        )

        assert r.status_code == 200, r.text
        assert ver.description == "updated description"
        assert listing.is_private is True
        assert listing.team_id == team_id
        db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_all_types_share_one_rejection_message():
    """The five routes must not diverge: same wording, same status, per type only in the URL."""
    messages = set()
    for item_type, base_path in ENDPOINTS:
        owner = _user()
        listing = _listing_mock(owner.id)
        r, _db = await _put_draft(item_type, base_path, listing, owner, {"visibility": "team"})
        assert r.status_code == 400
        messages.add(r.json()["detail"].replace(f"/registry/{item_type}/{listing.id}/", "/registry/TYPE/ID/"))
    assert len(messages) == 1, messages
