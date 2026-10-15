# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Team publishing review rules.

NOTHING auto-approves anymore. Every publish — team-visibility or public, from
any role up to super_admin — enters the review queue as pending, so each
release carries a recorded reviewed_by decision. Team owners and team
reviewers clear their own team queue explicitly (self-approval is allowed);
public listings still clear global review.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from api.deps import get_current_user, get_db
from models.agent import AgentStatus
from models.mcp import ListingStatus
from models.team import TeamRole
from models.user import User, UserRole
from services.teamspace import publish_auto_approves_for_entity, resolve_publish_target


@pytest.fixture(autouse=True)
def _lock_service_stub(monkeypatch):
    """These routes run on a mocked session; the lock service has its own tests."""
    import services.agent_lock as agent_lock

    monkeypatch.setattr(agent_lock, "lock_agent_version", AsyncMock(return_value={}))


# ── Helpers ──────────────────────────────────────────────


def _user(role=UserRole.user, **kw):
    u = MagicMock(spec=User)
    u.id = kw.get("id", uuid.uuid4())
    u.role = role
    u.username = kw.get("username", "testuser")
    u.email = kw.get("email", "test@example.com")
    return u


def _membership(role: TeamRole | None):
    return SimpleNamespace(role=role) if role is not None else None


def _result(value):
    """Stand in for a SQLAlchemy Result whose scalar lookup yields *value*."""
    r = MagicMock()
    r.scalar_one_or_none.return_value = value
    return r


def _mock_db(execute_values=()):
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.flush = AsyncMock()

    # Scripted results first, then empty ones indefinitely. A publish that stays
    # pending now also resolves inbox recipients, and a fixed-length script would
    # turn that extra query into a StopIteration in a test about publish status.
    # The filler answers empty for scalars() too, which _result does not: a
    # registry with no users has no reviewers to deliver to.
    def _empty():
        r = MagicMock()
        r.scalar_one_or_none.return_value = None
        r.scalars.return_value.all.return_value = []
        return r

    remaining = iter([_result(v) for v in execute_values])

    def _next(*_args, **_kwargs):
        return next(remaining, _empty())

    db.execute = AsyncMock(side_effect=_next)
    return db


def _app_with(router, user, db):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    return app


# (label, user role, team role or None, visibility, expected auto approval)
# Every row expects False: team publishing never auto-approves, for anyone.
# The rows are kept distinct so a regression that re-introduces auto-approval
# for one role shows up as that row's failure, not a generic one.
MATRIX = [
    ("team-owner-public", UserRole.user, TeamRole.owner, "public", False),
    ("team-owner-team", UserRole.user, TeamRole.owner, "team", False),
    ("team-reviewer-public", UserRole.user, TeamRole.reviewer, "public", False),
    ("team-reviewer-team", UserRole.user, TeamRole.reviewer, "team", False),
    ("team-member-public", UserRole.user, TeamRole.member, "public", False),
    ("team-member-team", UserRole.user, TeamRole.member, "team", False),
    ("global-reviewer-member-public", UserRole.reviewer, TeamRole.member, "public", False),
    ("global-reviewer-member-team", UserRole.reviewer, TeamRole.member, "team", False),
    ("admin-public", UserRole.admin, None, "public", False),
    ("admin-team", UserRole.admin, None, "team", False),
    ("super-admin-public", UserRole.super_admin, None, "public", False),
    ("super-admin-team", UserRole.super_admin, None, "team", False),
]

MATRIX_PARAMS = [pytest.param(*row[1:], id=row[0]) for row in MATRIX]


# ═══════════════════════════════════════════════════════════
# resolve_publish_target: new listings
# ═══════════════════════════════════════════════════════════


class TestResolvePublishTargetAutoApprove:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("user_role", "team_role", "visibility", "expected"), MATRIX_PARAMS)
    async def test_matrix(self, user_role, team_role, visibility, expected):
        team_id = uuid.uuid4()
        db = _mock_db([_membership(team_role)])
        db.get = AsyncMock(return_value=SimpleNamespace(id=team_id, handle="platform-tools", is_private=False))

        target = await resolve_publish_target(
            db, _user(user_role), "Internal Tool", team_id=team_id, visibility=visibility
        )

        assert target.namespace == "platform-tools"
        assert target.visibility == visibility
        assert target.auto_approve is expected

    @pytest.mark.asyncio
    async def test_private_team_rejects_public_publish(self):
        team_id = uuid.uuid4()
        db = _mock_db([_membership(TeamRole.owner)])
        db.get = AsyncMock(return_value=SimpleNamespace(id=team_id, handle="private-tools", is_private=True))

        with pytest.raises(HTTPException) as exc:
            await resolve_publish_target(db, _user(), "Internal Tool", team_id=team_id, visibility="public")
        assert exc.value.status_code == 409
        assert "team-private" in exc.value.detail

    @pytest.mark.asyncio
    async def test_pending_public_team_rejects_every_publish(self):
        team_id = uuid.uuid4()
        db = _mock_db()
        db.get = AsyncMock(
            return_value=SimpleNamespace(
                id=team_id,
                handle="pending-tools",
                is_private=True,
                visibility_request_status="pending",
            )
        )

        with pytest.raises(HTTPException) as exc:
            await resolve_publish_target(db, _user(), "Internal Tool", team_id=team_id, visibility="team")
        assert exc.value.status_code == 409
        assert "locked" in exc.value.detail

    @pytest.mark.asyncio
    @pytest.mark.parametrize("team_role", [None, TeamRole.member, TeamRole.reviewer])
    async def test_personal_publish_requires_active_creator_owner(self, team_role):
        team_id = uuid.uuid4()
        creator = _user()
        db = _mock_db([_membership(team_role)])
        db.get = AsyncMock(
            return_value=SimpleNamespace(
                id=team_id,
                handle="personal-tools",
                is_private=True,
                is_personal=True,
                created_by=creator.id,
            )
        )

        with pytest.raises(HTTPException) as exc:
            await resolve_publish_target(db, creator, "Internal Tool", team_id=team_id, visibility="team")
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_personal_creator_owner_may_publish(self):
        team_id = uuid.uuid4()
        creator = _user()
        db = _mock_db([_membership(TeamRole.owner)])
        db.get = AsyncMock(
            return_value=SimpleNamespace(
                id=team_id,
                handle="personal-tools",
                is_private=True,
                is_personal=True,
                created_by=creator.id,
            )
        )

        target = await resolve_publish_target(db, creator, "Internal Tool", team_id=team_id, visibility="team")
        assert target.namespace == "personal-tools"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("visibility", ["public", "team"])
    async def test_global_reviewer_cannot_publish_into_a_team_they_are_not_in(self, visibility):
        """Publishing cross-team is an admin capability, not a reviewer one.

        A global reviewer cannot read another team's private listings, so allowing
        one to publish here would create a listing its own author can no longer see.
        """
        team_id = uuid.uuid4()
        db = _mock_db([_membership(None)])
        db.get = AsyncMock(return_value=SimpleNamespace(id=team_id, handle="platform-tools", is_private=False))

        with pytest.raises(HTTPException) as exc:
            await resolve_publish_target(
                db, _user(UserRole.reviewer), "Internal Tool", team_id=team_id, visibility=visibility
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.parametrize("role", [UserRole.admin, UserRole.super_admin])
    @pytest.mark.parametrize("private", [False, True])
    async def test_admin_may_publish_into_a_team_they_are_not_in(self, role, private):
        """Admins keep the cross-team capability because they can still read the result."""
        team_id = uuid.uuid4()
        db = _mock_db([_membership(None)])
        db.get = AsyncMock(return_value=SimpleNamespace(id=team_id, handle="platform-tools", is_private=private))

        target = await resolve_publish_target(db, _user(role), "Internal Tool", team_id=team_id, visibility="team")
        # They can publish cross-team, but even admins do not skip review.
        assert (target.namespace, target.visibility, target.auto_approve) == ("platform-tools", "team", False)

    @pytest.mark.asyncio
    async def test_personal_public_listing_never_auto_approves(self):
        target = await resolve_publish_target(_mock_db(), _user(UserRole.super_admin), "Internal Tool")
        assert (target.team_id, target.visibility, target.auto_approve) == (None, "public", False)


# ═══════════════════════════════════════════════════════════
# publish_auto_approves_for_entity: saved drafts
# ═══════════════════════════════════════════════════════════


class TestEntityAutoApprove:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("user_role", "team_role", "visibility", "expected"), MATRIX_PARAMS)
    async def test_matrix(self, user_role, team_role, visibility, expected):
        entity = SimpleNamespace(team_id=uuid.uuid4(), is_private=visibility == "team")
        db = _mock_db([_membership(team_role)])

        assert await publish_auto_approves_for_entity(entity, _user(user_role), db) is expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize("visibility", ["public", "team"])
    async def test_non_member_never_auto_approves(self, visibility):
        entity = SimpleNamespace(team_id=uuid.uuid4(), is_private=visibility == "team")
        assert await publish_auto_approves_for_entity(entity, _user(), _mock_db([None])) is False

    @pytest.mark.asyncio
    async def test_personal_listing_skips_membership_lookup(self):
        db = _mock_db()
        entity = SimpleNamespace(team_id=None, is_private=False)
        assert await publish_auto_approves_for_entity(entity, _user(UserRole.super_admin), db) is False
        db.execute.assert_not_awaited()


# ═══════════════════════════════════════════════════════════
# Route level: the auto-approval decision becomes a status
# ═══════════════════════════════════════════════════════════


class TestSkillSubmitStatus:
    """POST /skills/submit resolves a publish target, so the status follows the matrix."""

    @staticmethod
    async def _submit(user, team_role, visibility):
        from api.routes.skill import router
        from models.skill import SkillListing, SkillVersion

        team_id = uuid.uuid4()
        # membership lookup, then the namespace/slug collision check.
        db = _mock_db([_membership(team_role), None])
        db.get = AsyncMock(return_value=SimpleNamespace(id=team_id, handle="platform-tools", is_private=False))

        def _refresh(obj):
            obj.id = uuid.uuid4()
            obj.created_at = datetime.now(UTC)
            obj.updated_at = datetime.now(UTC)

        db.refresh = AsyncMock(side_effect=_refresh)
        app = _app_with(router, user, db)

        with patch("api.routes.skill.submit_for_review", new=AsyncMock(return_value=SimpleNamespace(number=42))):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.post(
                    "/api/v1/skills/submit",
                    json={
                        "name": "s",
                        "version": "1.0",
                        "description": "d",
                        "owner": "o",
                        "task_type": "code-review",
                        "team_id": str(team_id),
                        "visibility": visibility,
                    },
                )

        assert r.status_code == 200, r.text
        listing = next(c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], SkillListing))
        version = next(c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], SkillVersion))
        return listing, version

    @pytest.mark.asyncio
    async def test_team_owner_public_stays_pending(self):
        listing, version = await self._submit(_user(), TeamRole.owner, "public")
        assert listing.is_private is False
        assert version.status == ListingStatus.pending
        assert version.reviewed_by is None
        assert version.reviewed_at is None

    @pytest.mark.asyncio
    async def test_team_owner_team_stays_pending(self):
        """A team owner's own team-visibility publish waits for an explicit
        (possibly self-) approval instead of skipping review."""
        listing, version = await self._submit(_user(), TeamRole.owner, "team")
        assert listing.is_private is True
        assert version.status == ListingStatus.pending
        assert version.reviewed_by is None

    @pytest.mark.asyncio
    async def test_team_reviewer_public_stays_pending(self):
        _listing, version = await self._submit(_user(), TeamRole.reviewer, "public")
        assert version.status == ListingStatus.pending

    @pytest.mark.asyncio
    async def test_global_reviewer_public_stays_pending(self):
        _listing, version = await self._submit(_user(UserRole.reviewer), TeamRole.member, "public")
        assert version.status == ListingStatus.pending
        assert version.reviewed_by is None


class TestAgentSubmitStatus:
    """POST /agents/{id}/submit reads the saved agent's own visibility."""

    @staticmethod
    def _agent(is_private):
        m = MagicMock()
        m.id = uuid.uuid4()
        m.name = "test-agent"
        m.status = AgentStatus.draft
        m.team_id = uuid.uuid4()
        m.is_private = is_private
        m.components = []
        m.description = "A test agent"
        m.owner = "testowner"
        m.rejection_reason = None
        m.deleted_at = None
        m.latest_version.prompt = "Test prompt"
        m.latest_version.reviewed_by = None
        m.latest_version.reviewed_at = None
        return m

    @staticmethod
    async def _submit(user, team_role, is_private):
        from api.routes.agent import draft as draft_routes

        agent = TestAgentSubmitStatus._agent(is_private)
        agent.created_by = user.id
        db = _mock_db([_membership(team_role)])

        async def submitted(_db, _kind, subject, version, _actor, **_kwargs):
            subject.status = AgentStatus.pending
            version.status = AgentStatus.pending
            return SimpleNamespace(number=42)

        with (
            patch("services.agent_snapshot.build_yaml_snapshot", new=AsyncMock(return_value="snapshot")),
            patch.object(draft_routes, "emit_registry_event"),
            patch.object(draft_routes, "_resolve_component_names", new=AsyncMock(return_value={})),
            patch.object(draft_routes, "_load_agent", new=AsyncMock(return_value=agent)),
            patch.object(draft_routes, "_agent_to_response", new=MagicMock(return_value=MagicMock())),
            patch("services.agent_lock.lock_agent_version", new=AsyncMock()),
            patch.object(draft_routes, "submit_for_review", new=AsyncMock(side_effect=submitted)),
        ):
            await draft_routes.submit_draft(str(agent.id), db=db, current_user=user)

        return agent

    @pytest.mark.asyncio
    async def test_team_owner_public_agent_stays_pending(self):
        agent = await self._submit(_user(), TeamRole.owner, is_private=False)
        assert agent.status == AgentStatus.pending
        assert agent.latest_version.reviewed_by is None

    @pytest.mark.asyncio
    async def test_team_owner_team_agent_stays_pending(self):
        agent = await self._submit(_user(), TeamRole.owner, is_private=True)
        assert agent.status == AgentStatus.pending
        assert agent.latest_version.reviewed_by is None

    @pytest.mark.asyncio
    async def test_team_reviewer_public_agent_stays_pending(self):
        agent = await self._submit(_user(), TeamRole.reviewer, is_private=False)
        assert agent.status == AgentStatus.pending

    @pytest.mark.asyncio
    async def test_team_member_team_agent_stays_pending(self):
        agent = await self._submit(_user(), TeamRole.member, is_private=True)
        assert agent.status == AgentStatus.pending

    @pytest.mark.asyncio
    async def test_admin_public_agent_stays_pending(self):
        agent = await self._submit(_user(UserRole.admin), None, is_private=False)
        assert agent.status == AgentStatus.pending
