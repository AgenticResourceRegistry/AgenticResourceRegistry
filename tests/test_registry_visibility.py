# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for /api/v1/registry visibility enforcement.

Two holes are covered here:

1. GET /api/v1/registry/resolve returned the canonical identity of any listing
   whose latest version was not approved, to anyone. The endpoint resolves with
   ``require_status=approved`` first and falls back to an unfiltered lookup, and
   that fallback had no status gate. Resolution now matches the component detail
   handlers: non-approved items resolve only for owners, co-authors, admins and
   reviewers.

2. PATCH /api/v1/registry/{type}/{id}/visibility only inspected the latest
   version of each public agent before letting a component go team-private.
   Installs can pin any approved version (``POST /agents/{id}/install`` with a
   ``version``), so an older approved version of a public agent kept a dangling
   reference that fails the install-time component scope check. The guard now
   covers every approved version of a public agent.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.deps import get_db, get_registry_user

# api.routes.registry is imported eagerly on purpose: it binds resolve_listing at
# import time, so a first import that happened inside a patch() block would
# capture the mock for the rest of the session.
from api.routes.registry import (
    VisibilityUpdateRequest,
    update_registry_visibility,
)
from api.routes.registry import router as registry_router
from models.agent import Agent, AgentStatus, AgentVersion
from models.agent_component import AgentComponent
from models.base import Base
from models.inbox import InboxItem, InboxItemEvent
from models.mcp import ListingStatus, McpListing, McpValidationResult, McpVersion
from models.review import (
    Review,
    ReviewComment,
    ReviewEvent,
    ReviewReviewerRequest,
    ReviewRevision,
    ReviewState,
    ReviewSubmission,
    ReviewSubscription,
    ReviewThread,
)
from models.team import Team, TeamMembership, TeamRole
from models.user import User, UserRole
from services.review.decisions import open_or_push

# resolve_visible_listing calls the module-global resolve_listing inside
# api.deps, so that is the only seam a patch can intercept.
RESOLVE_SEAM = "api.deps.resolve_listing"

NON_APPROVED = [
    ListingStatus.draft,
    ListingStatus.pending,
    ListingStatus.rejected,
    ListingStatus.archived,
]


def _user(role=UserRole.user, user_id=None):
    return SimpleNamespace(id=user_id or uuid.uuid4(), role=role, email="u@example.com", username="u")


def _component(status, submitted_by=None, co_authors=None):
    listing = MagicMock()
    listing.id = uuid.uuid4()
    listing.namespace = "alice"
    listing.slug = "tool"
    listing.qualified_name = "alice/tool"
    listing.status = status
    listing.submitted_by = submitted_by or uuid.uuid4()
    listing.co_authors = co_authors or []
    # Privacy is a separate axis from status. A bare MagicMock attribute is
    # truthy, which would make every listing look team-private.
    listing.is_private = False
    listing.team_id = None
    return listing


def _agent(status, created_by=None, co_authors=None):
    agent = MagicMock()
    agent.id = uuid.uuid4()
    agent.namespace = "alice"
    agent.slug = "reviewer"
    agent.qualified_name = "alice/reviewer"
    agent.status = status
    agent.created_by = created_by or uuid.uuid4()
    agent.co_authors = co_authors or []
    agent.is_private = False
    agent.team_id = None
    return agent


def _app(user):
    app = FastAPI()
    app.include_router(registry_router)
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[get_registry_user] = lambda: user
    return app


async def _resolve(app, item_type: str, identifier: str = "alice/tool"):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(
            "/api/v1/registry/resolve",
            params={"type": item_type, "identifier": identifier},
        )


# ── /resolve: component branch ───────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("status", NON_APPROVED)
async def test_anonymous_cannot_resolve_non_approved_component(status):
    listing = _component(status)
    with patch(RESOLVE_SEAM, new_callable=AsyncMock) as resolve:
        resolve.side_effect = [None, listing]
        response = await _resolve(_app(None), "mcp")
    assert response.status_code == 404
    assert "alice/tool" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status", NON_APPROVED)
async def test_non_owner_cannot_resolve_non_approved_component(status):
    listing = _component(status)
    with patch(RESOLVE_SEAM, new_callable=AsyncMock) as resolve:
        resolve.side_effect = [None, listing]
        response = await _resolve(_app(_user()), "mcp")
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("status", NON_APPROVED)
async def test_owner_resolves_own_non_approved_component(status):
    owner = _user()
    listing = _component(status, submitted_by=owner.id)
    with patch(RESOLVE_SEAM, new_callable=AsyncMock) as resolve:
        resolve.side_effect = [None, listing]
        response = await _resolve(_app(owner), "mcp")
    assert response.status_code == 200
    assert response.json()["qualified_name"] == "alice/tool"


@pytest.mark.asyncio
async def test_co_author_resolves_non_approved_component():
    co_author = _user()
    listing = _component(ListingStatus.pending, co_authors=[str(co_author.id)])
    with patch(RESOLVE_SEAM, new_callable=AsyncMock) as resolve:
        resolve.side_effect = [None, listing]
        response = await _resolve(_app(co_author), "mcp")
    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [UserRole.reviewer, UserRole.admin, UserRole.super_admin])
async def test_privileged_roles_resolve_non_approved_component(role):
    listing = _component(ListingStatus.draft)
    with patch(RESOLVE_SEAM, new_callable=AsyncMock) as resolve:
        resolve.side_effect = [None, listing]
        response = await _resolve(_app(_user(role=role)), "mcp")
    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("item_type", ["mcp", "skill", "hook", "prompt", "sandbox"])
async def test_anonymous_resolves_approved_component(item_type):
    listing = _component(ListingStatus.approved)
    with patch(RESOLVE_SEAM, new_callable=AsyncMock) as resolve:
        resolve.return_value = listing
        response = await _resolve(_app(None), item_type)
    assert response.status_code == 200
    assert response.json()["type"] == item_type


@pytest.mark.asyncio
@pytest.mark.parametrize("item_type", ["mcp", "skill", "hook", "prompt", "sandbox"])
async def test_every_component_type_gates_non_approved(item_type):
    listing = _component(ListingStatus.draft)
    with patch(RESOLVE_SEAM, new_callable=AsyncMock) as resolve:
        resolve.side_effect = [None, listing]
        response = await _resolve(_app(None), item_type)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_missing_component_returns_404():
    with patch(RESOLVE_SEAM, new_callable=AsyncMock) as resolve:
        resolve.side_effect = [None, None]
        response = await _resolve(_app(None), "mcp")
    assert response.status_code == 404


# ── /resolve: agent branch ───────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [AgentStatus.draft, AgentStatus.pending, AgentStatus.rejected, AgentStatus.archived],
)
async def test_anonymous_cannot_resolve_non_approved_agent(status):
    """_load_agent skips the status filter on UUID and prefix lookups."""
    agent = _agent(status)
    with patch("api.routes.registry._load_agent", new_callable=AsyncMock) as load:
        load.return_value = agent
        response = await _resolve(_app(None), "agent", identifier=str(agent.id))
    assert response.status_code == 404
    assert "alice/reviewer" not in response.text


@pytest.mark.asyncio
async def test_non_owner_cannot_resolve_non_approved_agent():
    agent = _agent(AgentStatus.draft)
    with patch("api.routes.registry._load_agent", new_callable=AsyncMock) as load:
        load.return_value = agent
        response = await _resolve(_app(_user()), "agent", identifier=str(agent.id))
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_owner_resolves_own_non_approved_agent():
    owner = _user()
    agent = _agent(AgentStatus.draft, created_by=owner.id)
    with patch("api.routes.registry._load_agent", new_callable=AsyncMock) as load:
        load.return_value = agent
        response = await _resolve(_app(owner), "agent", identifier=str(agent.id))
    assert response.status_code == 200
    assert response.json()["qualified_name"] == "alice/reviewer"


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [UserRole.reviewer, UserRole.admin, UserRole.super_admin])
async def test_privileged_roles_resolve_non_approved_agent(role):
    agent = _agent(AgentStatus.pending)
    with patch("api.routes.registry._load_agent", new_callable=AsyncMock) as load:
        load.return_value = agent
        response = await _resolve(_app(_user(role=role)), "agent", identifier=str(agent.id))
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_anonymous_resolves_approved_agent():
    agent = _agent(AgentStatus.approved)
    with patch("api.routes.registry._load_agent", new_callable=AsyncMock) as load:
        load.return_value = agent
        response = await _resolve(_app(None), "agent", identifier=str(agent.id))
    assert response.status_code == 200


# ── PATCH visibility: every approved public agent version ──

_TABLES = [
    Agent.__table__,
    AgentVersion.__table__,
    AgentComponent.__table__,
    McpListing.__table__,
    McpVersion.__table__,
    McpValidationResult.__table__,
    Team.__table__,
    TeamMembership.__table__,
    # Making a team listing public re-enters review, which delivers inbox items
    # to the reviewers in the same transaction.
    InboxItem.__table__,
    InboxItemEvent.__table__,
    Review.__table__,
    ReviewRevision.__table__,
    ReviewEvent.__table__,
    ReviewSubmission.__table__,
    ReviewThread.__table__,
    ReviewComment.__table__,
    ReviewReviewerRequest.__table__,
    ReviewSubscription.__table__,
    User.__table__,
]


@asynccontextmanager
async def _sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=_TABLES)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _seed_component_used_by_older_version(
    sessions,
    *,
    agent_is_private: bool,
    old_status: AgentStatus,
    link_to_latest: bool = False,
):
    """Seed an agent with two versions, only one of which references the component."""
    owner_id = uuid.uuid4()
    team = Team(id=uuid.uuid4(), name="Platform", handle="platform", created_by=owner_id)
    membership = TeamMembership(team_id=team.id, user_id=owner_id, role=TeamRole.owner)
    listing = McpListing(
        id=uuid.uuid4(),
        name="Search",
        namespace="platform",
        slug="search",
        category="general",
        owner="platform",
        submitted_by=owner_id,
        team_id=team.id,
        is_private=False,
        co_authors=[],
    )
    agent = Agent(
        id=uuid.uuid4(),
        name="Reviewer",
        namespace="alice",
        slug="reviewer",
        owner="alice",
        created_by=owner_id,
        is_private=agent_is_private,
        co_authors=[],
    )
    old = AgentVersion(
        id=uuid.uuid4(),
        agent_id=agent.id,
        version="1.0.0",
        model_name="claude-sonnet-4-5",
        released_by=owner_id,
        status=old_status,
    )
    latest = AgentVersion(
        id=uuid.uuid4(),
        agent_id=agent.id,
        version="2.0.0",
        model_name="claude-sonnet-4-5",
        released_by=owner_id,
        status=AgentStatus.approved,
    )
    link = AgentComponent(
        agent_version_id=latest.id if link_to_latest else old.id,
        component_type="mcp",
        component_id=listing.id,
        component_name="Search",
        resolved_version="1.0.0",
    )
    async with sessions() as session:
        session.add_all([team, membership, listing, agent, old, latest])
        await session.flush()
        agent.latest_version_id = latest.id
        session.add(link)
        await session.commit()
    return SimpleNamespace(listing_id=listing.id, user=SimpleNamespace(id=owner_id, role=UserRole.user))


async def _patch_visibility(session, listing_id, user, visibility="team", item_type="mcp"):
    return await update_registry_visibility(
        item_type,
        str(listing_id),
        VisibilityUpdateRequest(visibility=visibility),
        SimpleNamespace(state=SimpleNamespace()),
        session,
        user,
    )


@pytest.mark.asyncio
async def test_component_cannot_go_private_for_latest_approved_public_agent_version():
    """The guard the widened query has to keep: the latest version still counts."""
    async with _sessions() as sessions:
        seed = await _seed_component_used_by_older_version(
            sessions,
            agent_is_private=False,
            old_status=AgentStatus.approved,
            link_to_latest=True,
        )
        async with sessions() as session:
            with pytest.raises(HTTPException) as exc:
                await _patch_visibility(session, seed.listing_id, seed.user)
        assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_component_cannot_go_private_for_older_approved_public_agent_version():
    """Only the superseded version references the component, and installs can pin it."""
    async with _sessions() as sessions:
        seed = await _seed_component_used_by_older_version(
            sessions, agent_is_private=False, old_status=AgentStatus.approved
        )
        async with sessions() as session:
            with pytest.raises(HTTPException) as exc:
                await _patch_visibility(session, seed.listing_id, seed.user)
        assert exc.value.status_code == 409
        assert "approved public agent version" in exc.value.detail

        async with sessions() as session:
            listing = await session.get(McpListing, seed.listing_id)
            assert listing.is_private is False


@pytest.mark.asyncio
async def test_component_goes_private_when_older_public_agent_version_is_not_approved():
    async with _sessions() as sessions:
        seed = await _seed_component_used_by_older_version(
            sessions, agent_is_private=False, old_status=AgentStatus.rejected
        )
        async with sessions() as session:
            result = await _patch_visibility(session, seed.listing_id, seed.user)
        assert result["visibility"] == "team"

        async with sessions() as session:
            listing = await session.get(McpListing, seed.listing_id)
            assert listing.is_private is True


@pytest.mark.asyncio
async def test_component_goes_private_when_the_dependent_agent_is_team_private():
    async with _sessions() as sessions:
        seed = await _seed_component_used_by_older_version(
            sessions, agent_is_private=True, old_status=AgentStatus.approved
        )
        async with sessions() as session:
            result = await _patch_visibility(session, seed.listing_id, seed.user)
        assert result["visibility"] == "team"


@pytest.mark.asyncio
async def test_personal_component_moves_to_claimed_private_teamspace():
    owner_id = uuid.uuid4()
    team = Team(
        id=uuid.uuid4(),
        name="Personal",
        handle="alice-private",
        created_by=owner_id,
        is_private=True,
        is_personal=True,
    )
    listing = McpListing(
        id=uuid.uuid4(),
        name="Search",
        namespace="alice",
        slug="search",
        category="general",
        owner="alice",
        submitted_by=owner_id,
        is_private=False,
        co_authors=[],
    )
    async with _sessions() as sessions:
        async with sessions() as session:
            session.add_all(
                [
                    team,
                    TeamMembership(team_id=team.id, user_id=owner_id, role=TeamRole.owner),
                    listing,
                ]
            )
            await session.commit()

        user = SimpleNamespace(id=owner_id, role=UserRole.user)
        async with sessions() as session:
            result = await _patch_visibility(session, listing.id, user)

        assert result["team_id"] == team.id
        assert result["qualified_name"] == "alice-private/search"
        async with sessions() as session:
            moved = await session.get(McpListing, listing.id)
            assert moved.team_id == team.id
            assert moved.namespace == team.handle
            assert moved.owner == team.handle
            assert moved.is_private is True


@pytest.mark.asyncio
async def test_personal_component_requires_active_creator_owner_membership():
    owner_id = uuid.uuid4()
    team = Team(
        id=uuid.uuid4(),
        name="Personal",
        handle="alice-private",
        created_by=owner_id,
        is_private=True,
        is_personal=True,
    )
    listing = McpListing(
        id=uuid.uuid4(),
        name="Search",
        namespace="alice",
        slug="search",
        category="general",
        owner="alice",
        submitted_by=owner_id,
        is_private=False,
        co_authors=[],
    )
    async with _sessions() as sessions:
        async with sessions() as session:
            session.add_all([team, listing])
            await session.commit()

        user = SimpleNamespace(id=owner_id, role=UserRole.user)
        async with sessions() as session:
            with pytest.raises(HTTPException) as exc:
                await _patch_visibility(session, listing.id, user)
        assert exc.value.status_code == 409
        assert "Claim a private personal teamspace" in exc.value.detail


@pytest.mark.asyncio
async def test_personal_agent_moves_to_claimed_private_teamspace():
    owner_id = uuid.uuid4()
    team = Team(
        id=uuid.uuid4(),
        name="Personal",
        handle="alice-private",
        created_by=owner_id,
        is_private=True,
        is_personal=True,
    )
    agent = Agent(
        id=uuid.uuid4(),
        name="Reviewer",
        namespace="alice",
        slug="reviewer",
        owner="alice",
        created_by=owner_id,
        is_private=False,
        co_authors=[],
    )
    async with _sessions() as sessions:
        async with sessions() as session:
            session.add_all(
                [
                    team,
                    TeamMembership(team_id=team.id, user_id=owner_id, role=TeamRole.owner),
                    agent,
                ]
            )
            await session.commit()

        user = SimpleNamespace(id=owner_id, role=UserRole.user)
        async with sessions() as session:
            result = await _patch_visibility(session, agent.id, user, item_type="agent")

        assert result["team_id"] == team.id
        assert result["qualified_name"] == "alice-private/reviewer"
        async with sessions() as session:
            moved = await session.get(Agent, agent.id)
            assert moved.team_id == team.id
            assert moved.namespace == team.handle
            assert moved.owner == team.handle
            assert moved.is_private is True


@pytest.mark.asyncio
async def test_item_in_private_teamspace_cannot_become_public():
    owner_id = uuid.uuid4()
    team = Team(
        id=uuid.uuid4(),
        name="Private",
        handle="private-team",
        created_by=owner_id,
        is_private=True,
    )
    listing = McpListing(
        id=uuid.uuid4(),
        name="Search",
        namespace=team.handle,
        slug="search",
        category="general",
        owner=team.handle,
        submitted_by=owner_id,
        team_id=team.id,
        is_private=True,
        co_authors=[],
    )
    async with _sessions() as sessions:
        async with sessions() as session:
            session.add_all(
                [
                    team,
                    TeamMembership(team_id=team.id, user_id=owner_id, role=TeamRole.owner),
                    listing,
                ]
            )
            await session.commit()

        user = SimpleNamespace(id=owner_id, role=UserRole.user)
        async with sessions() as session:
            with pytest.raises(HTTPException) as exc:
                await _patch_visibility(session, listing.id, user, visibility="public")
        assert exc.value.status_code == 409
        assert "Private teamspaces" in exc.value.detail


@pytest.mark.asyncio
async def test_personal_item_needs_a_claimed_private_teamspace():
    owner_id = uuid.uuid4()
    listing = McpListing(
        id=uuid.uuid4(),
        name="Search",
        namespace="alice",
        slug="search",
        category="general",
        owner="alice",
        submitted_by=owner_id,
        is_private=False,
        co_authors=[],
    )
    async with _sessions() as sessions:
        async with sessions() as session:
            session.add(listing)
            await session.commit()

        user = SimpleNamespace(id=owner_id, role=UserRole.user)
        async with sessions() as session:
            with pytest.raises(HTTPException) as exc:
                await _patch_visibility(session, listing.id, user)
        assert exc.value.status_code == 409
        assert "Claim a private personal teamspace" in exc.value.detail


@pytest.mark.asyncio
async def test_component_returns_to_public_without_the_dependency_check():
    async with _sessions() as sessions:
        seed = await _seed_component_used_by_older_version(
            sessions, agent_is_private=False, old_status=AgentStatus.approved
        )
        async with sessions() as session:
            result = await _patch_visibility(session, seed.listing_id, seed.user, visibility="public")
        assert result["visibility"] == "public"


# ── Making a team-private listing public re-enters review ───────────────────
#
# Team owners and team reviewers approve their own team-visibility publications,
# because only their own teamspace can see the result. Without a review step on
# the visibility transition that self-approval becomes a two-step route into the
# public catalog: publish as team visibility, self-approve, then flip the same
# approved row to public and reach every user without a reviewer ever seeing it.
# The web registry UI exposes the flip as a single toggle, so the bypass is one
# click, not a theoretical API sequence.


async def _seed_team_private_component(sessions, *, status=ListingStatus.approved):
    """Seed an approved team-private MCP owned by a plain user who owns the team."""
    owner_id = uuid.uuid4()
    team = Team(id=uuid.uuid4(), name="Acme", handle="acme", created_by=owner_id)
    membership = TeamMembership(team_id=team.id, user_id=owner_id, role=TeamRole.owner)
    listing = McpListing(
        id=uuid.uuid4(),
        name="Internal",
        namespace="acme",
        slug="internal",
        category="general",
        owner="acme",
        submitted_by=owner_id,
        team_id=team.id,
        is_private=True,
        co_authors=[],
    )
    version = McpVersion(
        id=uuid.uuid4(),
        listing_id=listing.id,
        version="1.0.0",
        description="internal tool",
        released_by=owner_id,
        released_at=datetime(2026, 1, 1, tzinfo=UTC),
        status=status,
        reviewed_by=owner_id,
        reviewed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    async with sessions() as session:
        # Insert the listing before the version: McpListing.versions and
        # McpListing.latest_version point at each other, so flushing both in one
        # unit of work makes SQLAlchemy see a circular dependency.
        session.add_all([team, membership, listing])
        await session.flush()
        session.add(version)
        await session.flush()
        listing.latest_version_id = version.id
        await session.commit()
    return SimpleNamespace(
        listing_id=listing.id,
        team_id=team.id,
        owner=SimpleNamespace(id=owner_id, role=UserRole.user),
    )


@pytest.mark.asyncio
async def test_team_owner_making_a_listing_public_sends_it_back_to_review():
    """The bypass: a plain user who owns the team must not self-publish publicly."""
    async with _sessions() as sessions:
        seed = await _seed_team_private_component(sessions)
        async with sessions() as session:
            result = await _patch_visibility(session, seed.listing_id, seed.owner, visibility="public")

        assert result["visibility"] == "public"
        assert result["returned_to_review"] is True
        assert result["status"] == ListingStatus.pending.value

        async with sessions() as session:
            listing = await session.get(McpListing, seed.listing_id)
            version = await session.get(McpVersion, listing.latest_version_id)
            assert version.status == ListingStatus.pending
            # The previous team-level approval must not be presented as a global one.
            assert version.reviewed_by is None
            assert version.reviewed_at is None


@pytest.mark.asyncio
async def test_public_visibility_requeues_older_approved_version_even_with_pending_latest():
    async with _sessions() as sessions:
        seed = await _seed_team_private_component(sessions)
        async with sessions() as db:
            listing = await db.get(McpListing, seed.listing_id)
            older_id = listing.latest_version_id
            newer = McpVersion(
                listing_id=listing.id,
                version="1.1.0",
                description="not yet reviewed",
                released_by=seed.owner.id,
                released_at=datetime.now(UTC),
                status=ListingStatus.pending,
            )
            db.add(newer)
            await db.flush()
            listing.latest_version_id = newer.id
            await db.commit()
        async with sessions() as db:
            result = await _patch_visibility(db, seed.listing_id, seed.owner, visibility="public")
        assert result["returned_to_review"] is True
        async with sessions() as db:
            assert (await db.get(McpVersion, older_id)).status == ListingStatus.pending
            assert (await db.get(McpVersion, newer.id)).status == ListingStatus.pending
            assert await db.scalar(select(Review).where(Review.version_id == older_id)) is not None


@pytest.mark.asyncio
async def test_public_visibility_reopens_published_review_without_counting_private_approvals():
    async with _sessions() as sessions:
        seed = await _seed_team_private_component(sessions)
        async with sessions() as db:
            listing = await db.get(McpListing, seed.listing_id)
            version = await db.get(McpVersion, listing.latest_version_id)
            review = await open_or_push(db, "mcp", listing, version, seed.owner.id, backfill=True)
            review.state = ReviewState.published
            review.published_at = datetime.now(UTC)
            original_number = review.number
            db.add(
                ReviewSubmission(
                    review_id=review.id,
                    revision_id=review.head_revision_id,
                    reviewer_id=seed.owner.id,
                    state="submitted",
                    verdict="approve",
                    submitted_at=datetime.now(UTC),
                )
            )
            await db.commit()

        async with sessions() as db:
            result = await _patch_visibility(db, seed.listing_id, seed.owner, visibility="public")
        assert result["returned_to_review"] is True
        async with sessions() as db:
            review = await db.scalar(select(Review).where(Review.number == original_number))
            assert review.state == ReviewState.open
            assert review.is_private is False
            assert (await db.get(ReviewRevision, review.head_revision_id)).number == 2
            assert (
                await db.scalar(select(ReviewSubmission).where(ReviewSubmission.review_id == review.id))
            ).state == "dismissed"
            version = await db.get(McpVersion, review.version_id)
            assert version.status == ListingStatus.pending


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [UserRole.admin, UserRole.super_admin])
async def test_privileged_actor_making_a_listing_public_keeps_it_approved(role):
    """An admin already holds the authority the queue represents."""
    async with _sessions() as sessions:
        seed = await _seed_team_private_component(sessions)
        actor = SimpleNamespace(id=uuid.uuid4(), role=role)
        async with sessions() as session:
            result = await _patch_visibility(session, seed.listing_id, actor, visibility="public")

        assert result["visibility"] == "public"
        assert result["returned_to_review"] is False
        assert result["status"] == ListingStatus.approved.value


@pytest.mark.asyncio
async def test_global_reviewer_outside_the_team_cannot_flip_a_team_private_listing():
    """A global reviewer cannot read the listing, so it cannot flip it either.

    Team-private content is reviewed inside its own teamspace. A reviewer who is
    not in the team gets the same answer as any other outsider: the listing does
    not exist. Publishing it into the public catalog stays a team owner or team
    reviewer action, followed by the global review queue.
    """
    async with _sessions() as sessions:
        seed = await _seed_team_private_component(sessions)
        reviewer = SimpleNamespace(id=uuid.uuid4(), role=UserRole.reviewer)
        async with sessions() as session:
            with pytest.raises(HTTPException) as exc:
                await _patch_visibility(session, seed.listing_id, reviewer, visibility="public")

        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_restricting_a_public_listing_to_the_team_does_not_re_enter_review():
    """Narrowing visibility is not a publication, so it needs no review."""
    async with _sessions() as sessions:
        seed = await _seed_team_private_component(sessions)
        async with sessions() as session:
            await _patch_visibility(session, seed.listing_id, seed.owner, visibility="public")
        # Approve it globally, then narrow it back to the team.
        async with sessions() as session:
            listing = await session.get(McpListing, seed.listing_id)
            version = await session.get(McpVersion, listing.latest_version_id)
            version.status = ListingStatus.approved
            await session.commit()
        async with sessions() as session:
            result = await _patch_visibility(session, seed.listing_id, seed.owner, visibility="team")

        assert result["visibility"] == "team"
        assert result["returned_to_review"] is False
        assert result["status"] == ListingStatus.approved.value


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [ListingStatus.draft, ListingStatus.pending, ListingStatus.rejected])
async def test_never_approved_listing_is_not_moved_when_it_becomes_public(status):
    """A listing that was never approved has no approval to revoke."""
    async with _sessions() as sessions:
        seed = await _seed_team_private_component(sessions, status=status)
        async with sessions() as session:
            result = await _patch_visibility(session, seed.listing_id, seed.owner, visibility="public")

        assert result["returned_to_review"] is False
        assert result["status"] == status.value


# ── Legacy bare names resolve against the caller, not against anonymous ─────
#
# resolve_listing filters a bare-name collision through the caller's visibility
# before it builds the 409, so it needs the caller. A call site that omits it
# evaluates the collision as anonymous: the team-private half of the collision
# drops out, one candidate is left, and the name resolves silently to the public
# row instead of raising. These go through update_registry_visibility, a real
# call site, rather than through resolve_listing directly, because the omission
# lives at the call sites and not in the helper.


async def _seed_colliding_bare_name(sessions):
    """Seed a public alice/tool and a team-private platform/tool, both on slug 'tool'."""
    alice_id = uuid.uuid4()
    bob_id = uuid.uuid4()
    team = Team(id=uuid.uuid4(), name="Platform", handle="platform", created_by=bob_id)
    membership = TeamMembership(team_id=team.id, user_id=bob_id, role=TeamRole.owner)
    public = McpListing(
        id=uuid.uuid4(),
        name="Tool",
        namespace="alice",
        slug="tool",
        category="general",
        owner="alice",
        submitted_by=alice_id,
        team_id=None,
        is_private=False,
        co_authors=[],
    )
    private = McpListing(
        id=uuid.uuid4(),
        name="Tool",
        namespace="platform",
        slug="tool",
        category="general",
        owner="platform",
        submitted_by=bob_id,
        team_id=team.id,
        is_private=True,
        co_authors=[],
    )
    async with sessions() as session:
        session.add_all([team, membership, public, private])
        await session.flush()
        for listing in (public, private):
            version = McpVersion(
                id=uuid.uuid4(),
                listing_id=listing.id,
                version="1.0.0",
                description="tool",
                released_by=listing.submitted_by,
                released_at=datetime(2026, 1, 1, tzinfo=UTC),
                status=ListingStatus.approved,
            )
            session.add(version)
            await session.flush()
            listing.latest_version_id = version.id
        await session.commit()
    return SimpleNamespace(
        member=SimpleNamespace(id=bob_id, role=UserRole.user),
        non_member=SimpleNamespace(id=alice_id, role=UserRole.user),
        public_name=public.qualified_name,
        private_name=private.qualified_name,
    )


@pytest.mark.asyncio
async def test_bare_name_colliding_with_a_visible_team_listing_is_ambiguous_for_a_member():
    """Bob can see both rows, so 'tool' is ambiguous and must not pick one for him."""
    async with _sessions() as sessions:
        seed = await _seed_colliding_bare_name(sessions)
        async with sessions() as session:
            with pytest.raises(HTTPException) as exc:
                await _patch_visibility(session, "tool", seed.member, visibility="public")

        assert exc.value.status_code == 409
        assert seed.public_name in exc.value.detail
        assert seed.private_name in exc.value.detail


@pytest.mark.asyncio
async def test_same_bare_name_resolves_to_the_public_row_for_a_non_member():
    """Alice sees one candidate, so the name is unambiguous and the other stays secret."""
    async with _sessions() as sessions:
        seed = await _seed_colliding_bare_name(sessions)
        async with sessions() as session:
            result = await _patch_visibility(session, "tool", seed.non_member, visibility="public")

        assert result["qualified_name"] == seed.public_name
        assert seed.private_name not in str(result)


@pytest.mark.asyncio
async def test_public_listing_is_not_visible_to_anonymous_callers_while_pending():
    """The flipped listing leaves the approved catalog until a reviewer accepts it."""
    from sqlalchemy import select

    from api.deps import apply_visibility_filter

    async with _sessions() as sessions:
        seed = await _seed_team_private_component(sessions)
        async with sessions() as session:
            await _patch_visibility(session, seed.listing_id, seed.owner, visibility="public")

        async with sessions() as session:
            stmt = (
                select(McpListing)
                .join(McpVersion, McpListing.latest_version_id == McpVersion.id)
                .where(McpVersion.status == ListingStatus.approved)
            )
            rows = (await session.execute(apply_visibility_filter(stmt, McpListing, None))).scalars().all()
            assert [r.id for r in rows] == []
