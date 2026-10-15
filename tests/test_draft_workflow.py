# SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
# SPDX-FileCopyrightText: 2026 Subramania Raja <dhanpraja231@gmail.com>
# SPDX-FileCopyrightText: 2026 Harishankar <harishankar0301@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the draft agent lifecycle (save, update, submit).

Covers creating a draft, updating it, submitting for review,
and error handling when agent is not in draft status.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.deps import get_current_user, get_db
from api.routes.agent import router
from models.agent import AgentStatus
from models.team import Team, TeamRole
from models.user import User, UserRole

# ── Helpers ──────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _lock_service_stub(monkeypatch):
    """These routes run on a mocked session; the lock service has its own tests."""
    import services.agent_lock as agent_lock

    monkeypatch.setattr(agent_lock, "lock_agent_version", AsyncMock(return_value={}))
    monkeypatch.setattr(agent_lock, "attach_pinned_components", AsyncMock(return_value=[]))
    # Synthetic agents cannot be requeued via the mapped-model publication policy.
    monkeypatch.setattr("api.routes.agent.draft.review_publication_to_public", AsyncMock(return_value=False))


@pytest.fixture
def review_submission_stub(monkeypatch):
    async def submit(_db, _subject_type, agent, version, _author_id, *, message=None):
        agent.status = AgentStatus.pending
        version.status = AgentStatus.pending
        return SimpleNamespace(number=42)

    mock_submit = AsyncMock(side_effect=submit)
    monkeypatch.setattr("api.routes.agent.draft.submit_for_review", mock_submit)
    return mock_submit


def _user(**kw):
    u = MagicMock(spec=User)
    u.id = kw.get("id", uuid.uuid4())
    u.role = kw.get("role", UserRole.user)
    u.email = kw.get("email", "test@example.com")
    u.username = kw.get("username", "testuser")
    return u


def _mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.delete = MagicMock()
    db.flush = AsyncMock()
    return db


def _membership(role: TeamRole):
    m = MagicMock()
    m.role = role
    return m


def _team_row(agent, *, private=False, visibility_request_status=None):
    team = MagicMock(spec=Team)
    team.id = agent.team_id
    team.is_private = private
    team.visibility_request_status = visibility_request_status
    return team


def _db_with_membership(membership):
    """Mock db whose only query, the teamspace membership lookup, yields *membership*.

    A bare AsyncMock returns AsyncMock children, so `result.scalar_one_or_none()`
    would hand back an un-awaited coroutine instead of a row. Wire `execute`
    explicitly so the membership lookup behaves like a real AsyncSession.
    """
    db = _mock_db()
    result = MagicMock()
    result.scalar_one_or_none.return_value = membership
    db.execute = AsyncMock(return_value=result)
    return db


def _app_with(user=None, db=None):
    user = user or _user()
    db = db or _mock_db()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    return app, db, user


def _agent_mock(status=AgentStatus.draft, created_by=None, **extra):
    """Return a MagicMock that looks like an Agent ORM instance."""
    m = MagicMock()
    m.id = extra.get("id", uuid.uuid4())
    m.name = extra.get("name", "test-agent")
    m.version = extra.get("version", "1.0.0")
    m.description = extra.get("description", "A test agent")
    m.owner = extra.get("owner", "testowner")
    m.prompt = extra.get("prompt", "Test prompt")
    m.model_name = extra.get("model_name", "claude-sonnet-4")
    m.model_config_json = {}
    m.external_mcps = []
    m.supported_harnesses = []
    m.status = status
    m.rejection_reason = None
    m.download_count = 0
    m.unique_users = 0
    m.git_url = None
    # team_id is the sole privacy axis: None means a personal listing.
    m.team_id = extra.get("team_id")
    m.is_private = extra.get("is_private", False)
    m.created_by = created_by or uuid.uuid4()
    m.created_at = datetime.now(UTC)
    m.deleted_at = None
    m.updated_at = datetime.now(UTC)
    m.components = extra.get("components", [])
    m.success_criteria = None
    # Edit-lock fields on the latest_version mock
    m.latest_version.is_editing = False
    m.latest_version.editing_by = None
    m.latest_version.editing_since = None
    m.latest_version.prompt = extra.get("prompt", "Test prompt")
    m.latest_version.reviewed_by = None
    m.latest_version.reviewed_at = None
    # Make __table__.columns iterable for _agent_to_response
    col_keys = [
        "id",
        "name",
        "version",
        "description",
        "owner",
        "git_url",
        "prompt",
        "model_name",
        "model_config_json",
        "external_mcps",
        "supported_harnesses",
        "status",
        "rejection_reason",
        "download_count",
        "unique_users",
        "created_by",
        "created_at",
        "deleted_at",
        "updated_at",
    ]
    cols = []
    for key in col_keys:
        col = MagicMock()
        col.key = key
        cols.append(col)
    m.__table__ = MagicMock()
    m.__table__.columns = cols
    return m


def _draft_request_body(**overrides) -> dict:
    """Build a minimal AgentCreateRequest body for the draft endpoint."""
    body = {
        "name": "my-draft-agent",
        "version": "1.0.0",
        "description": "Draft agent",
        "owner": "testowner",
        "prompt": "Do things",
        "model_name": "claude-sonnet-4",
    }
    body.update(overrides)
    return body


def _empty_result():
    r = MagicMock()
    r.scalars.return_value.all.return_value = []
    r.scalar_one_or_none.return_value = None
    return r


def _component_mock(component_type="mcp", component_id=None):
    """Return a MagicMock that looks like an AgentComponent row."""
    c = MagicMock()
    c.component_type = component_type
    c.component_id = component_id or uuid.uuid4()
    c.resolved_version = "1.0.0"
    c.order_index = 0
    c.config_override = None
    return c


def _scalar_result(value):
    r = MagicMock()
    r.scalar_one_or_none.return_value = value
    return r


def _rows_result(rows):
    r = MagicMock()
    r.all.return_value = rows
    return r


def _approved_listing(name="public-mcp"):
    from models.mcp import ListingStatus

    listing = MagicMock()
    listing.status = ListingStatus.approved
    listing.name = name
    return listing


def _sequenced_db(*results):
    """Mock db whose successive `execute` calls yield *results* in order.

    Anything beyond the listed calls raises StopIteration, so an unexpected
    extra query fails the test instead of silently returning a MagicMock.
    """
    db = _mock_db()
    db.execute = AsyncMock(side_effect=list(results))
    return db


def _result_with_agent(agent):
    """Return a mock result that yields the agent via scalar_one_or_none."""
    r = MagicMock()
    r.scalar_one_or_none.return_value = agent
    r.scalars.return_value.all.return_value = [agent]
    return r


# ═══════════════════════════════════════════════════════════
# save_draft (POST /api/v1/agents/draft)
# ═══════════════════════════════════════════════════════════


class TestDraftSave:
    """Test creating a new draft agent."""

    @pytest.mark.asyncio
    @patch("services.agent_snapshot.build_yaml_snapshot", new=AsyncMock(return_value="snapshot"))
    @patch("api.routes.agent.draft._load_agent")
    async def test_creates_agent_with_draft_status(self, mock_load):
        """POST /agents/draft creates an agent in draft status."""
        user = _user()
        app, db, _ = _app_with(user=user)
        agent = _agent_mock(status=AgentStatus.draft, created_by=user.id)
        mock_load.return_value = agent

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/agents/draft",
                json=_draft_request_body(),
            )

        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "draft"
        db.commit.assert_awaited()

    @pytest.mark.asyncio
    @patch("services.agent_snapshot.build_yaml_snapshot", new=AsyncMock(return_value="snapshot"))
    @patch("api.routes.agent.draft._load_agent")
    async def test_response_includes_agent_fields(self, mock_load):
        """Draft response includes id, name, and status fields."""
        user = _user()
        app, db, _ = _app_with(user=user)
        agent = _agent_mock(status=AgentStatus.draft, name="my-draft-agent", created_by=user.id)
        mock_load.return_value = agent

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/agents/draft",
                json=_draft_request_body(),
            )

        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "my-draft-agent"
        assert "id" in data


# ═══════════════════════════════════════════════════════════
# update_draft (PUT /api/v1/agents/{id}/draft)
# ═══════════════════════════════════════════════════════════


class TestDraftUpdate:
    """Test updating a draft agent."""

    @pytest.mark.asyncio
    @patch("services.agent_snapshot.build_yaml_snapshot", new=AsyncMock(return_value="snapshot"))
    @patch("api.routes.agent.draft._load_agent")
    async def test_updates_draft_fields(self, mock_load):
        """PUT /agents/{id}/draft updates the draft agent."""
        user = _user()
        app, db, _ = _app_with(user=user)
        agent = _agent_mock(status=AgentStatus.draft, created_by=user.id)
        mock_load.return_value = agent

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.put(
                f"/api/v1/agents/{agent.id}/draft",
                json={"description": "Updated draft description"},
            )

        assert r.status_code == 200
        db.commit.assert_awaited()

    @pytest.mark.asyncio
    @patch("api.routes.agent.draft._load_agent")
    async def test_rejects_update_on_non_draft(self, mock_load):
        """Updating an approved agent returns 400."""
        user = _user()
        app, db, _ = _app_with(user=user)
        agent = _agent_mock(status=AgentStatus.approved, created_by=user.id)
        mock_load.return_value = agent

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.put(
                f"/api/v1/agents/{agent.id}/draft",
                json={"description": "Nope"},
            )

        assert r.status_code == 400

    @pytest.mark.asyncio
    @patch("api.routes.agent.draft._load_agent")
    async def test_rejects_update_by_non_owner(self, mock_load):
        """Non-owner cannot update a draft agent (403)."""
        user = _user()
        app, db, _ = _app_with(user=user)
        agent = _agent_mock(status=AgentStatus.draft, created_by=uuid.uuid4())
        mock_load.return_value = agent

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.put(
                f"/api/v1/agents/{agent.id}/draft",
                json={"description": "Not my agent"},
            )

        assert r.status_code == 403


# ═══════════════════════════════════════════════════════════
# update_draft visibility changes (Feature 3 composition policy)
# ═══════════════════════════════════════════════════════════


class TestDraftVisibilityChange:
    """A visibility flip revalidates the attached components and checks team role."""

    @staticmethod
    def _team_agent(user, *, is_private, components):
        return _agent_mock(
            status=AgentStatus.draft,
            created_by=user.id,
            team_id=uuid.uuid4(),
            is_private=is_private,
            components=components,
        )

    @pytest.mark.asyncio
    @patch("services.agent_snapshot.build_yaml_snapshot", new=AsyncMock(return_value="snapshot"))
    @patch("api.routes.agent.draft._load_agent")
    async def test_team_to_public_rejects_private_component(self, mock_load):
        """Going public with a team-private component attached is a 409 naming it."""
        user = _user()
        component = _component_mock()
        agent = self._team_agent(user, is_private=True, components=[component])
        db = _sequenced_db(
            _scalar_result(_team_row(agent)),  # lock owning team
            _scalar_result(_membership(TeamRole.owner)),  # team role gate
            _scalar_result(None),  # component is outside the public scope
            _rows_result([(component.component_id, "secret-mcp")]),  # name lookup
        )
        app, db, _ = _app_with(user=user, db=db)
        mock_load.return_value = agent

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.put(f"/api/v1/agents/{agent.id}/draft", json={"visibility": "public"})

        assert r.status_code == 409
        assert "secret-mcp" in r.json()["detail"]
        # The flip must not be applied when the composition check fails.
        assert agent.is_private is True
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("services.agent_snapshot.build_yaml_snapshot", new=AsyncMock(return_value="snapshot"))
    @patch("api.routes.agent.draft._load_agent")
    async def test_team_to_public_allows_public_components(self, mock_load):
        """Going public succeeds when every attached component is public."""
        user = _user()
        component = _component_mock()
        agent = self._team_agent(user, is_private=True, components=[component])
        db = _sequenced_db(
            _scalar_result(_team_row(agent)),
            _scalar_result(_membership(TeamRole.owner)),
            _scalar_result(_approved_listing()),
        )
        app, db, _ = _app_with(user=user, db=db)
        mock_load.return_value = agent

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.put(f"/api/v1/agents/{agent.id}/draft", json={"visibility": "public"})

        assert r.status_code == 200
        assert agent.is_private is False
        assert r.json()["visibility"] == "public"
        db.commit.assert_awaited()

    @pytest.mark.asyncio
    @patch("api.routes.agent.draft._load_agent")
    async def test_private_team_cannot_make_draft_public(self, mock_load):
        user = _user()
        agent = self._team_agent(user, is_private=True, components=[])
        db = _sequenced_db(_scalar_result(_team_row(agent, private=True)))
        app, db, _ = _app_with(user=user, db=db)
        mock_load.return_value = agent

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.put(f"/api/v1/agents/{agent.id}/draft", json={"visibility": "public"})

        assert response.status_code == 409
        assert "Private teamspaces" in response.json()["detail"]
        assert agent.is_private is True
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("api.routes.agent.draft._load_agent")
    async def test_public_to_team_requires_teamspace(self, mock_load):
        """Team visibility without a teamspace is a 422, with no component queries."""
        user = _user()
        app, db, _ = _app_with(user=user)
        agent = _agent_mock(status=AgentStatus.draft, created_by=user.id, is_private=False)
        mock_load.return_value = agent

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.put(f"/api/v1/agents/{agent.id}/draft", json={"visibility": "team"})

        assert r.status_code == 422
        assert r.json()["detail"] == "Team visibility requires a teamspace"
        assert agent.is_private is False
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("services.agent_snapshot.build_yaml_snapshot", new=AsyncMock(return_value="snapshot"))
    @patch("api.routes.agent.draft._load_agent")
    async def test_public_to_team_allowed_for_team_owner(self, mock_load):
        """A team owner may take a public team agent private."""
        user = _user()
        component = _component_mock()
        agent = self._team_agent(user, is_private=False, components=[component])
        db = _sequenced_db(
            _scalar_result(_team_row(agent)),
            _scalar_result(_membership(TeamRole.owner)),
            _scalar_result(_approved_listing()),
        )
        app, db, _ = _app_with(user=user, db=db)
        mock_load.return_value = agent

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.put(f"/api/v1/agents/{agent.id}/draft", json={"visibility": "team"})

        assert r.status_code == 200
        assert agent.is_private is True
        assert r.json()["visibility"] == "team"

    @pytest.mark.asyncio
    @patch("api.routes.agent.draft._load_agent")
    async def test_plain_team_member_cannot_flip_visibility(self, mock_load):
        """A plain team member is refused before any component work happens."""
        user = _user()
        agent = self._team_agent(user, is_private=True, components=[_component_mock()])
        db = _sequenced_db(
            _scalar_result(_team_row(agent)),
            _scalar_result(_membership(TeamRole.member)),
        )
        app, db, _ = _app_with(user=user, db=db)
        mock_load.return_value = agent

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.put(f"/api/v1/agents/{agent.id}/draft", json={"visibility": "public"})

        assert r.status_code == 403
        assert r.json()["detail"] == "Only team owners and reviewers can change visibility"
        assert agent.is_private is True

    @pytest.mark.asyncio
    @patch("services.agent_snapshot.build_yaml_snapshot", new=AsyncMock(return_value="snapshot"))
    @patch("api.routes.agent.draft._load_agent")
    async def test_unchanged_visibility_skips_revalidation(self, mock_load):
        """Echoing the current visibility back is a no-op, not a team-role check."""
        user = _user()
        agent = self._team_agent(user, is_private=True, components=[_component_mock()])
        db = _sequenced_db()  # any query at all would raise StopIteration
        app, db, _ = _app_with(user=user, db=db)
        mock_load.return_value = agent

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.put(f"/api/v1/agents/{agent.id}/draft", json={"visibility": "team"})

        assert r.status_code == 200
        assert agent.is_private is True

    @pytest.mark.asyncio
    @patch("services.agent_snapshot.build_yaml_snapshot", new=AsyncMock(return_value="snapshot"))
    @patch("api.routes.agent.draft._load_agent")
    async def test_resent_components_are_validated_against_new_target(self, mock_load):
        """When components are resent, they are checked against the new visibility."""
        user = _user()
        agent = self._team_agent(user, is_private=True, components=[_component_mock()])
        new_component_id = uuid.uuid4()
        db = _sequenced_db(
            _scalar_result(_team_row(agent)),
            _scalar_result(_membership(TeamRole.owner)),
            _scalar_result(None),  # the resent component is not public
        )
        app, db, _ = _app_with(user=user, db=db)
        mock_load.return_value = agent

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.put(
                f"/api/v1/agents/{agent.id}/draft",
                json={
                    "visibility": "public",
                    "components": [{"component_type": "mcp", "component_id": str(new_component_id)}],
                },
            )

        assert r.status_code == 400
        assert r.json()["detail"][0]["component_id"] == str(new_component_id)
        db.commit.assert_not_awaited()


# ═══════════════════════════════════════════════════════════
# update_draft fields that must never be silently dropped
# ═══════════════════════════════════════════════════════════


class TestDraftUpdateFieldHandling:
    """Every accepted request field is either applied or refused explicitly."""

    @pytest.mark.asyncio
    @patch("api.routes.agent.draft._load_agent")
    async def test_rejects_teamspace_move(self, mock_load):
        """A different team_id is refused instead of being ignored."""
        user = _user()
        app, db, _ = _app_with(user=user)
        agent = _agent_mock(status=AgentStatus.draft, created_by=user.id, team_id=uuid.uuid4(), is_private=True)
        mock_load.return_value = agent

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.put(f"/api/v1/agents/{agent.id}/draft", json={"team_id": str(uuid.uuid4())})

        assert r.status_code == 422
        assert "Teamspace cannot be changed here" in r.json()["detail"]
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("services.agent_snapshot.build_yaml_snapshot", new=AsyncMock(return_value="snapshot"))
    @patch("api.routes.agent.draft._load_agent")
    async def test_accepts_unchanged_teamspace(self, mock_load):
        """Resending the agent's own team_id is a no-op, not an error."""
        user = _user()
        team_id = uuid.uuid4()
        app, db, _ = _app_with(user=user)
        agent = _agent_mock(status=AgentStatus.draft, created_by=user.id, team_id=team_id, is_private=True)
        mock_load.return_value = agent

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.put(f"/api/v1/agents/{agent.id}/draft", json={"team_id": str(team_id)})

        assert r.status_code == 200
        assert agent.team_id == team_id

    @pytest.mark.asyncio
    @patch("api.routes.agent.draft._load_agent")
    async def test_rejects_legacy_mcp_server_ids(self, mock_load):
        """The draft route never wired mcp_server_ids up, so it refuses them."""
        user = _user()
        app, db, _ = _app_with(user=user)
        agent = _agent_mock(status=AgentStatus.draft, created_by=user.id)
        mock_load.return_value = agent

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.put(f"/api/v1/agents/{agent.id}/draft", json={"mcp_server_ids": [str(uuid.uuid4())]})

        assert r.status_code == 422
        assert "components" in r.json()["detail"]
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("services.agent_snapshot.build_yaml_snapshot", new=AsyncMock(return_value="snapshot"))
    @patch("api.routes.agent.draft._load_agent")
    async def test_applies_category(self, mock_load):
        """category reaches the agent row instead of being dropped."""
        user = _user()
        app, db, _ = _app_with(user=user)
        agent = _agent_mock(status=AgentStatus.draft, created_by=user.id)
        mock_load.return_value = agent

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.put(f"/api/v1/agents/{agent.id}/draft", json={"category": "devops"})

        assert r.status_code == 200
        assert agent.category == "devops"

    @pytest.mark.asyncio
    @patch("services.agent_snapshot.build_yaml_snapshot", new=AsyncMock(return_value="snapshot"))
    @patch("api.routes.agent.draft._load_agent")
    async def test_applies_version_bump_type(self, mock_load):
        """version_bump_type bumps the draft version instead of being dropped."""
        user = _user()
        app, db, _ = _app_with(user=user)
        agent = _agent_mock(status=AgentStatus.draft, created_by=user.id, version="1.2.3")
        mock_load.return_value = agent

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.put(f"/api/v1/agents/{agent.id}/draft", json={"version_bump_type": "minor"})

        assert r.status_code == 200
        assert agent.latest_version.version == "1.3.0"


# ═══════════════════════════════════════════════════════════
# submit_draft (POST /api/v1/agents/{id}/submit)
# ═══════════════════════════════════════════════════════════


@pytest.mark.usefixtures("review_submission_stub")
class TestDraftSubmit:
    """Test submitting a personal draft for review."""

    @pytest.mark.asyncio
    @patch("services.agent_snapshot.build_yaml_snapshot", new=AsyncMock(return_value="snapshot"))
    @patch("api.routes.agent.draft.emit_registry_event")
    @patch("api.routes.agent.draft._resolve_component_names")
    @patch("api.routes.agent.draft._load_agent")
    async def test_transitions_to_pending(self, mock_load, mock_resolve, mock_emit):
        """POST /agents/{id}/submit transitions a personal draft to pending status."""
        user = _user()
        app, db, _ = _app_with(user=user)
        agent = _agent_mock(status=AgentStatus.draft, created_by=user.id, components=[])
        mock_load.return_value = agent
        mock_resolve.return_value = {}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(f"/api/v1/agents/{agent.id}/submit")

        assert r.status_code == 200
        assert agent.status == AgentStatus.pending
        db.commit.assert_awaited()

    @pytest.mark.asyncio
    @patch("services.agent_snapshot.build_yaml_snapshot", new=AsyncMock(return_value="snapshot"))
    @patch("api.routes.agent.draft.emit_registry_event")
    @patch("api.routes.agent.draft._resolve_component_names")
    @patch("api.routes.agent.draft._load_agent")
    async def test_submit_response_includes_status(self, mock_load, mock_resolve, mock_emit):
        """Submit response body includes the new pending status."""
        user = _user()
        app, db, _ = _app_with(user=user)
        agent = _agent_mock(status=AgentStatus.draft, created_by=user.id, components=[])
        # After status change, _load_agent is called again; return the same agent
        mock_load.return_value = agent
        mock_resolve.return_value = {}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(f"/api/v1/agents/{agent.id}/submit")

        data = r.json()
        assert data["status"] == "pending"

    @pytest.mark.asyncio
    @patch("services.agent_snapshot.build_yaml_snapshot", new=AsyncMock(return_value="snapshot"))
    @patch("api.routes.agent.draft.emit_registry_event")
    @patch("api.routes.agent.draft._resolve_component_names")
    @patch("api.routes.agent.draft._load_agent")
    async def test_personal_draft_never_auto_approves(self, mock_load, mock_resolve, mock_emit):
        """A draft with no teamspace always goes to review, never straight to approved."""
        user = _user()
        app, db, _ = _app_with(user=user)
        agent = _agent_mock(status=AgentStatus.draft, created_by=user.id, components=[])
        mock_load.return_value = agent
        mock_resolve.return_value = {}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(f"/api/v1/agents/{agent.id}/submit")

        assert r.status_code == 200
        assert agent.status == AgentStatus.pending
        assert agent.latest_version.reviewed_by is None
        assert agent.latest_version.reviewed_at is None
        # No teamspace means no membership lookup is needed at all.
        db.execute.assert_not_awaited()


# ═══════════════════════════════════════════════════════════
# Submit a team-owned draft (Feature 3 publishing rules)
# ═══════════════════════════════════════════════════════════


@pytest.mark.usefixtures("review_submission_stub")
class TestTeamDraftSubmit:
    """Team publishing never auto-approves: every submit waits for an explicit
    (possibly self-) approval, so each release records a reviewed_by decision."""

    @staticmethod
    def _submit_team_draft(user, membership):
        agent = _agent_mock(
            status=AgentStatus.draft,
            created_by=user.id,
            components=[],
            team_id=uuid.uuid4(),
            is_private=True,
        )
        db = _db_with_membership(membership)
        return agent, db

    @pytest.mark.asyncio
    @patch("services.agent_snapshot.build_yaml_snapshot", new=AsyncMock(return_value="snapshot"))
    @patch("api.routes.agent.draft.emit_registry_event")
    @patch("api.routes.agent.draft._resolve_component_names")
    @patch("api.routes.agent.draft._load_agent")
    async def test_team_owner_goes_to_pending(self, mock_load, mock_resolve, mock_emit):
        """A team owner's own submit waits for an explicit (self-) approval."""
        user = _user()
        agent, db = self._submit_team_draft(user, _membership(TeamRole.owner))
        app, db, _ = _app_with(user=user, db=db)
        mock_load.return_value = agent
        mock_resolve.return_value = {}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(f"/api/v1/agents/{agent.id}/submit")

        assert r.status_code == 200
        assert r.json()["status"] == "pending"
        assert agent.status == AgentStatus.pending
        assert agent.latest_version.reviewed_by is None
        assert agent.latest_version.reviewed_at is None

    @pytest.mark.asyncio
    @patch("services.agent_snapshot.build_yaml_snapshot", new=AsyncMock(return_value="snapshot"))
    @patch("api.routes.agent.draft.emit_registry_event")
    @patch("api.routes.agent.draft._resolve_component_names")
    @patch("api.routes.agent.draft._load_agent")
    async def test_team_reviewer_goes_to_pending(self, mock_load, mock_resolve, mock_emit):
        """A team reviewer's submit also enters the queue instead of skipping it."""
        user = _user()
        agent, db = self._submit_team_draft(user, _membership(TeamRole.reviewer))
        app, db, _ = _app_with(user=user, db=db)
        mock_load.return_value = agent
        mock_resolve.return_value = {}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(f"/api/v1/agents/{agent.id}/submit")

        assert r.status_code == 200
        assert r.json()["status"] == "pending"
        assert agent.status == AgentStatus.pending
        assert agent.latest_version.reviewed_by is None
        assert agent.latest_version.reviewed_at is None

    @pytest.mark.asyncio
    @patch("services.agent_snapshot.build_yaml_snapshot", new=AsyncMock(return_value="snapshot"))
    @patch("api.routes.agent.draft.emit_registry_event")
    @patch("api.routes.agent.draft._resolve_component_names")
    @patch("api.routes.agent.draft._load_agent")
    async def test_plain_team_member_goes_to_pending(self, mock_load, mock_resolve, mock_emit):
        """A plain team member cannot self-publish; the draft waits for review."""
        user = _user()
        agent, db = self._submit_team_draft(user, _membership(TeamRole.member))
        app, db, _ = _app_with(user=user, db=db)
        mock_load.return_value = agent
        mock_resolve.return_value = {}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(f"/api/v1/agents/{agent.id}/submit")

        assert r.status_code == 200
        assert r.json()["status"] == "pending"
        assert agent.status == AgentStatus.pending
        assert agent.latest_version.reviewed_by is None
        assert agent.latest_version.reviewed_at is None

    @pytest.mark.asyncio
    @patch("services.agent_snapshot.build_yaml_snapshot", new=AsyncMock(return_value="snapshot"))
    @patch("api.routes.agent.draft.emit_registry_event")
    @patch("api.routes.agent.draft._resolve_component_names")
    @patch("api.routes.agent.draft._load_agent")
    async def test_global_reviewer_goes_to_pending_too(self, mock_load, mock_resolve, mock_emit):
        """Even a global reviewer's team submit enters the queue; approval is a
        separate recorded action."""
        user = _user(role=UserRole.reviewer)
        agent, db = self._submit_team_draft(user, None)
        app, db, _ = _app_with(user=user, db=db)
        mock_load.return_value = agent
        mock_resolve.return_value = {}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(f"/api/v1/agents/{agent.id}/submit")

        assert r.status_code == 200
        assert r.json()["status"] == "pending"
        assert agent.status == AgentStatus.pending
        assert agent.latest_version.reviewed_by is None


# ═══════════════════════════════════════════════════════════
# Submit non-draft (error cases)
# ═══════════════════════════════════════════════════════════


class TestDraftSubmitNotDraft:
    """Test submitting a non-draft agent returns an error."""

    @pytest.mark.asyncio
    @patch("services.agent_snapshot.build_yaml_snapshot", new=AsyncMock(return_value="snapshot"))
    @patch("api.routes.agent.draft._load_agent")
    async def test_submit_pending_creates_new_revision(self, mock_load, review_submission_stub):
        """Pending submissions can be resubmitted as a new review revision."""
        user = _user()
        app, db, _ = _app_with(user=user)
        agent = _agent_mock(status=AgentStatus.pending, created_by=user.id)
        mock_load.return_value = agent

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(f"/api/v1/agents/{agent.id}/submit")

        assert r.status_code == 200
        assert r.json()["review_number"] == 42
        review_submission_stub.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("api.routes.agent.draft._load_agent")
    async def test_submit_active_returns_400(self, mock_load):
        """Submitting an active agent returns 400."""
        user = _user()
        app, db, _ = _app_with(user=user)
        agent = _agent_mock(status=AgentStatus.approved, created_by=user.id)
        mock_load.return_value = agent

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(f"/api/v1/agents/{agent.id}/submit")

        assert r.status_code == 400

    @pytest.mark.asyncio
    @patch("api.routes.agent.draft._load_agent")
    async def test_submit_not_found_returns_404(self, mock_load):
        """Submitting a nonexistent agent returns 404."""
        user = _user()
        app, db, _ = _app_with(user=user)
        mock_load.return_value = None

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(f"/api/v1/agents/{uuid.uuid4()}/submit")

        assert r.status_code == 404
