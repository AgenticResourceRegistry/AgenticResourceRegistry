# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the 6 new registry types: tool, skill, hook, prompt, sandbox, graphrag."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.deps import get_current_user, get_db, get_registry_user
from models.mcp import ListingStatus
from models.user import User, UserRole

# ── Helpers ──────────────────────────────────────────────


def _user(**kw):
    u = MagicMock(spec=User)
    u.id = kw.get("id", uuid.uuid4())
    u.role = kw.get("role", UserRole.admin)
    u.username = kw.get("username", "testuser")
    u.email = kw.get("email", "test@example.com")
    return u


def _mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.delete = AsyncMock()
    empty = MagicMock()
    empty.all.return_value = []
    db.execute.return_value = empty
    # A review decision delivers inbox items in the same transaction, wrapping
    # each insert in a SAVEPOINT. A bare AsyncMock returns a coroutine from
    # begin_nested(), which is not an async context manager.
    nested = MagicMock()
    nested.__aenter__ = AsyncMock(return_value=None)
    nested.__aexit__ = AsyncMock(return_value=False)
    db.begin_nested = MagicMock(return_value=nested)
    return db


def _app_with(router, user=None, db=None):
    user = user or _user()
    db = db or _mock_db()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_registry_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    return app, db, user


def _listing_mock(model_cls, status=ListingStatus.pending, **extra):
    m = MagicMock()
    m.id = uuid.uuid4()
    m.name = "test-listing"
    m.namespace = "testuser"
    m.slug = "test-listing"
    m.qualified_name = "testuser/test-listing"
    m.version = "1.0.0"
    m.description = "A test listing description that is long enough"
    m.owner = "testowner"
    m.status = status
    m.rejection_reason = None
    m.submitted_by = uuid.uuid4()
    m.supported_harnesses = ["cursor"]
    m.slash_command = None
    m.skill_md_content = None
    m.created_at = datetime.now(UTC)
    m.updated_at = datetime.now(UTC)
    m.latest_version = SimpleNamespace(
        id=uuid.uuid4(),
        version=m.version,
        status=status,
        download_count=0,
        delivery_mode="git_fetch",
        extra_files=[],
        script_filename=None,
        script_content=None,
        requires_global_review=False,
    )
    for k, v in extra.items():
        setattr(m, k, v)
    return m


def _scalar_result(val):
    """Mock db.execute() returning a result whose .scalar_one_or_none() / .scalars().first() returns val."""
    r = MagicMock()
    r.scalar_one_or_none.return_value = val
    r.scalars.return_value.all.return_value = [val] if val else []
    r.scalars.return_value.first.return_value = val
    return r


# ═══════════════════════════════════════════════════════════
# 1. TestModels
# ═══════════════════════════════════════════════════════════


class TestModels:
    """Test that all 6 listing + download + link models have correct table names and reuse ListingStatus."""

    def test_skill_listing_tablename(self):
        from models.skill import SkillListing

        assert SkillListing.__tablename__ == "skill_listings"

    def test_hook_listing_tablename(self):
        from models.hook import HookListing

        assert HookListing.__tablename__ == "hook_listings"

    def test_prompt_listing_tablename(self):
        from models.prompt import PromptListing

        assert PromptListing.__tablename__ == "prompt_listings"

    def test_sandbox_listing_tablename(self):
        from models.sandbox import SandboxListing

        assert SandboxListing.__tablename__ == "sandbox_listings"

    def test_skill_download_tablename(self):
        from models.skill import SkillDownload

        assert SkillDownload.__tablename__ == "skill_downloads"

    def test_hook_download_tablename(self):
        from models.hook import HookDownload

        assert HookDownload.__tablename__ == "hook_downloads"

    def test_prompt_download_tablename(self):
        from models.prompt import PromptDownload

        assert PromptDownload.__tablename__ == "prompt_downloads"

    def test_sandbox_download_tablename(self):
        from models.sandbox import SandboxDownload

        assert SandboxDownload.__tablename__ == "sandbox_downloads"

    def test_listing_status_reused_not_redefined(self):
        """All version models use the same ListingStatus enum from models.mcp."""
        from models.hook import HookVersion
        from models.mcp import ListingStatus as Canonical
        from models.mcp import McpVersion
        from models.prompt import PromptVersion
        from models.sandbox import SandboxVersion
        from models.skill import SkillVersion

        for model in (McpVersion, SkillVersion, HookVersion, PromptVersion, SandboxVersion):
            col = model.__table__.columns["status"]
            assert col.type.enum_class is Canonical

    def test_submission_model_tablename(self):
        from models.submission import Submission

        assert Submission.__tablename__ == "submissions"


# ═══════════════════════════════════════════════════════════
# 2. TestSchemas
# ═══════════════════════════════════════════════════════════


class TestSchemas:
    """Validate pydantic schemas for all 6 types."""

    # ── SubmitRequest valid ──

    def test_skill_submit_valid(self):
        from schemas.skill import SkillSubmitRequest

        r = SkillSubmitRequest(name="s", version="1.0", description="desc", owner="o", task_type="code-review")
        assert r.skill_path == "/"

    def test_hook_submit_valid(self):
        from schemas.hook import HookSubmitRequest

        r = HookSubmitRequest(
            name="h", version="1.0", description="desc", owner="o", event="PreToolUse", handler_type="command"
        )
        assert r.execution_mode == "async"
        assert r.priority == 100

    def test_prompt_submit_valid(self):
        from schemas.prompt import PromptSubmitRequest

        r = PromptSubmitRequest(
            name="p", version="1.0", description="desc", owner="o", category="general", template="Hello {{ name }}"
        )
        assert r.variables == []

    def test_sandbox_submit_valid(self):
        from schemas.sandbox import SandboxSubmitRequest

        r = SandboxSubmitRequest(
            name="sb", version="1.0", description="desc", owner="o", runtime_type="docker", image="python:3.11"
        )
        assert r.network_policy == "none"

    # ── SubmitRequest missing required fields ──

    def test_hook_submit_missing_event(self):
        from schemas.hook import HookSubmitRequest

        with pytest.raises(ValueError):
            HookSubmitRequest(name="h", version="1.0", description="d", owner="o", handler_type="command")

    def test_sandbox_submit_missing_image(self):
        from schemas.sandbox import SandboxSubmitRequest

        with pytest.raises(ValueError):
            SandboxSubmitRequest(name="sb", version="1.0", description="d", owner="o", runtime_type="docker")

    # ── ListingResponse from_attributes ──

    def _ns(self, **kw):
        """SimpleNamespace works with pydantic from_attributes (MagicMock.name conflicts)."""
        from types import SimpleNamespace

        return SimpleNamespace(**kw)

    def test_prompt_response_from_attrs(self):
        from schemas.prompt import PromptListingResponse

        obj = self._ns(
            id=uuid.uuid4(),
            name="p",
            namespace="testuser",
            slug="p",
            qualified_name="testuser/p",
            version="1.0",
            description="d",
            owner="o",
            category="c",
            template="hi",
            variables=[],
            tags=[],
            supported_harnesses=[],
            status=ListingStatus.approved,
            rejection_reason=None,
            submitted_by=uuid.uuid4(),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        r = PromptListingResponse.model_validate(obj, from_attributes=True)
        assert r.status == ListingStatus.approved

    # ── Prompt render schemas ──

    def test_prompt_render_request(self):
        from schemas.prompt import PromptRenderRequest

        r = PromptRenderRequest(variables={"name": "world"})
        assert r.variables["name"] == "world"

    def test_prompt_render_response(self):
        from schemas.prompt import PromptRenderResponse

        r = PromptRenderResponse(listing_id=uuid.uuid4(), rendered="Hello world")
        assert "world" in r.rendered


# ═══════════════════════════════════════════════════════════
# 3. TestRoutes
# ═══════════════════════════════════════════════════════════


class TestSkillRoutes:
    @pytest.mark.asyncio
    async def test_submit_calls_db_add_and_commit(self):
        from api.routes.skill import router

        app, db, user = _app_with(router)

        def _refresh(obj):
            obj.id = uuid.uuid4()
            obj.created_at = datetime.now(UTC)
            obj.updated_at = datetime.now(UTC)

        db.refresh = AsyncMock(side_effect=_refresh)
        db.execute = AsyncMock(return_value=_scalar_result(None))

        from models.skill import SkillListing

        _orig_init = SkillListing.__init__

        def _patched_init(self, **kwargs):
            kwargs.pop("archive_url", None)
            _orig_init(self, **kwargs)

        with patch.object(SkillListing, "__init__", _patched_init):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.post(
                    "/api/v1/skills/submit",
                    json={"name": "s", "version": "1.0", "description": "d", "owner": "o", "task_type": "code-review"},
                )
        assert r.status_code == 200
        assert db.add.call_count == 2  # listing + version

    @pytest.mark.asyncio
    async def test_registry_direct_uses_yaml_frontmatter_for_command(self):
        from api.routes.skill import router
        from models.skill import SkillListing, SkillVersion

        app, db, user = _app_with(router)

        def _refresh(obj):
            obj.id = uuid.uuid4()
            obj.created_at = datetime.now(UTC)
            obj.updated_at = datetime.now(UTC)

        db.refresh = AsyncMock(side_effect=_refresh)
        db.execute = AsyncMock(return_value=_scalar_result(None))

        _orig_init = SkillListing.__init__

        def _patched_init(self, **kwargs):
            kwargs.pop("archive_url", None)
            _orig_init(self, **kwargs)

        skill_md = '---\nname: s\ndescription: d\ncommand: "/review"\n---\n\nBody\n'
        with patch.object(SkillListing, "__init__", _patched_init):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.post(
                    "/api/v1/skills/submit",
                    json={
                        "name": "s",
                        "version": "1.0",
                        "description": "d",
                        "owner": "o",
                        "task_type": "code-review",
                        "delivery_mode": "registry_direct",
                        "skill_md_content": skill_md,
                    },
                )

        assert r.status_code == 200
        version = next(call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], SkillVersion))
        assert version.slash_command == "review"

    @pytest.mark.asyncio
    async def test_draft_update_empty_slash_command_clears_even_with_existing_frontmatter_command(self):
        from api.routes import skill as skill_routes
        from schemas.skill import SkillUpdateRequest

        user = _user()
        db = _mock_db()
        version = MagicMock()
        version.skill_md_content = "---\nname: review\ndescription: d\ncommand: /review\n---\n"
        version.slash_command = "review"
        version.is_editing = False
        version.status = ListingStatus.draft
        version.content_revision = None
        version.base_version_id = None
        version.review_epoch = 0
        version.delivery_mode = "registry_direct"
        version.script_content = None
        version.script_filename = None
        version.extra_files = []
        version.editing_by = None
        version.editing_since = None
        listing = _listing_mock(object, status=ListingStatus.draft, latest_version=version)
        listing.latest_version_id = version.id

        with (
            patch.object(skill_routes, "resolve_listing", AsyncMock(return_value=listing)),
            patch.object(skill_routes, "lock_skill_version", AsyncMock(return_value=(version.id, version))),
            patch.object(skill_routes, "_recheck_skill_owner", AsyncMock()),
            patch.object(skill_routes, "get_effective_component_permission", return_value="owner"),
            patch.object(skill_routes.SkillListingResponse, "model_validate", return_value=listing),
        ):
            await skill_routes.update_skill_draft(
                listing_id=str(listing.id),
                req=SkillUpdateRequest(slash_command=""),
                db=db,
                current_user=user,
            )

        assert version.slash_command is None

    @pytest.mark.asyncio
    async def test_get_missing_returns_404(self):
        from api.routes.skill import router

        app, db, _ = _app_with(router)
        db.execute = AsyncMock(return_value=_scalar_result(None))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get(f"/api/v1/skills/{uuid.uuid4()}")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_install_approved_returns_config(self):
        from api.routes.skill import router

        app, db, user = _app_with(router)
        listing = _listing_mock(None, status=ListingStatus.approved)
        db.execute = AsyncMock(return_value=_scalar_result(listing))
        with patch(
            "api.routes.skill._selected_skill_release",
            new_callable=AsyncMock,
            return_value=(listing, listing.latest_version),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.post(f"/api/v1/skills/{listing.id}/install", json={"harness": "cursor"})
        assert r.status_code == 200
        assert "config_snippet" in r.json()


class TestHookRoutes:
    @pytest.mark.asyncio
    async def test_submit_calls_db_add_and_commit(self):
        from api.routes.hook import router

        app, db, user = _app_with(router)

        def _refresh(obj):
            obj.id = uuid.uuid4()
            obj.created_at = datetime.now(UTC)
            obj.updated_at = datetime.now(UTC)

        db.refresh = AsyncMock(side_effect=_refresh)
        db.execute = AsyncMock(return_value=_scalar_result(None))

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/hooks/submit",
                json={
                    "name": "h",
                    "version": "1.0",
                    "description": "d",
                    "owner": "o",
                    "event": "PreToolUse",
                    "handler_type": "command",
                },
            )
        assert r.status_code == 200
        assert db.add.call_count == 2  # listing + version

    @pytest.mark.asyncio
    async def test_get_missing_returns_404(self):
        from api.routes.hook import router

        app, db, _ = _app_with(router)
        db.execute = AsyncMock(return_value=_scalar_result(None))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get(f"/api/v1/hooks/{uuid.uuid4()}")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_install_approved_returns_config(self):
        from api.routes.hook import router

        app, db, user = _app_with(router)
        listing = _listing_mock(None, status=ListingStatus.approved)
        db.execute = AsyncMock(return_value=_scalar_result(listing))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(f"/api/v1/hooks/{listing.id}/install", json={"harness": "cursor"})
        assert r.status_code == 200
        assert "config_snippet" in r.json()


class TestPromptRoutes:
    @pytest.mark.asyncio
    async def test_submit_calls_db_add_and_commit(self):
        from api.routes.prompt import router

        app, db, user = _app_with(router)

        def _refresh(obj):
            obj.id = uuid.uuid4()
            obj.created_at = datetime.now(UTC)
            obj.updated_at = datetime.now(UTC)

        db.refresh = AsyncMock(side_effect=_refresh)
        db.execute = AsyncMock(return_value=_scalar_result(None))

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/prompts/submit",
                json={
                    "name": "p",
                    "version": "1.0",
                    "description": "d",
                    "owner": "o",
                    "category": "general",
                    "template": "Hello {{ name }}",
                },
            )
        assert r.status_code == 200
        assert db.add.call_count == 2  # listing + version

    @pytest.mark.asyncio
    async def test_get_missing_returns_404(self):
        from api.routes.prompt import router

        app, db, _ = _app_with(router)
        db.execute = AsyncMock(return_value=_scalar_result(None))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get(f"/api/v1/prompts/{uuid.uuid4()}")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_install_route_removed(self):
        from api.routes.prompt import router

        app, db, user = _app_with(router)
        listing = _listing_mock(None, status=ListingStatus.approved)
        db.execute = AsyncMock(return_value=_scalar_result(listing))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(f"/api/v1/prompts/{listing.id}/install")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_render_substitutes_variables(self):
        from api.routes.prompt import router

        app, db, user = _app_with(router)
        listing = _listing_mock(
            None, status=ListingStatus.approved, template="Hello {{ name }}, welcome to {{ place }}"
        )
        db.execute = AsyncMock(return_value=_scalar_result(listing))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                f"/api/v1/prompts/{listing.id}/render",
                json={"variables": {"name": "Alice", "place": "Wonderland"}},
            )
        assert r.status_code == 200
        assert r.json()["rendered"] == "Hello Alice, welcome to Wonderland"

    @pytest.mark.asyncio
    async def test_render_missing_returns_404(self):
        from api.routes.prompt import router

        app, db, _ = _app_with(router)
        db.execute = AsyncMock(return_value=_scalar_result(None))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(f"/api/v1/prompts/{uuid.uuid4()}/render", json={"variables": {}})
        assert r.status_code == 404


class TestSandboxRoutes:
    @pytest.mark.asyncio
    async def test_submit_calls_db_add_and_commit(self):
        from api.routes.sandbox import router

        app, db, user = _app_with(router)

        def _refresh(obj):
            obj.id = uuid.uuid4()
            obj.created_at = datetime.now(UTC)
            obj.updated_at = datetime.now(UTC)

        db.refresh = AsyncMock(side_effect=_refresh)
        db.execute = AsyncMock(return_value=_scalar_result(None))

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/sandboxes/submit",
                json={
                    "name": "sb",
                    "version": "1.0",
                    "description": "d",
                    "owner": "o",
                    "runtime_type": "docker",
                    "image": "python:3.11",
                },
            )
        assert r.status_code == 200
        assert db.add.call_count == 2  # listing + version

    @pytest.mark.asyncio
    async def test_get_missing_returns_404(self):
        from api.routes.sandbox import router

        app, db, _ = _app_with(router)
        db.execute = AsyncMock(return_value=_scalar_result(None))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get(f"/api/v1/sandboxes/{uuid.uuid4()}")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_install_route_removed(self):
        from api.routes.sandbox import router

        app, db, user = _app_with(router)
        listing = _listing_mock(None, status=ListingStatus.approved)
        db.execute = AsyncMock(return_value=_scalar_result(listing))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(f"/api/v1/sandboxes/{listing.id}/install", json={"harness": "cursor"})
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════
# 4. TestUnifiedReview
# ═══════════════════════════════════════════════════════════


class TestUnifiedReview:
    @pytest.mark.asyncio
    async def test_list_pending_returns_empty(self):
        from api.routes.review import router

        app, db, _ = _app_with(router)
        empty = MagicMock()
        empty.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=empty)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get("/api/v1/review")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_list_pending_requires_admin(self):
        from api.routes.review import router

        user = _user(role=UserRole.user)
        app, db, _ = _app_with(router, user=user)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get("/api/v1/review")
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_approve_not_found(self):
        from api.routes.review import router

        app, db, _ = _app_with(router)
        db.execute = AsyncMock(return_value=_scalar_result(None))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(f"/api/v1/review/{uuid.uuid4()}/approve")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_reject_not_found(self):
        from api.routes.review import router

        app, db, _ = _app_with(router)
        db.execute = AsyncMock(return_value=_scalar_result(None))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(f"/api/v1/review/{uuid.uuid4()}/reject", json={"reason": "bad"})
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_approve_changes_status(self):
        from api.routes.review import router

        app, db, _ = _app_with(router)
        listing = _listing_mock(None, status=ListingStatus.pending)
        # First query finds the listing; everything after — including the inbox
        # queries a decision now runs — answers empty rather than exhausting.
        script = iter([_scalar_result(listing)])
        db.execute = AsyncMock(side_effect=lambda *a, **k: next(script, _scalar_result(None)))
        db.refresh = AsyncMock(side_effect=lambda obj: None)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(f"/api/v1/review/{listing.id}/approve")
        assert r.status_code == 200
        assert listing.status == ListingStatus.approved

    @pytest.mark.asyncio
    async def test_reject_sets_reason(self):
        from api.routes.review import router

        app, db, _ = _app_with(router)
        listing = _listing_mock(None, status=ListingStatus.pending)
        script = iter([_scalar_result(listing)])
        db.execute = AsyncMock(side_effect=lambda *a, **k: next(script, _scalar_result(None)))
        db.refresh = AsyncMock(side_effect=lambda obj: None)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(f"/api/v1/review/{listing.id}/reject", json={"reason": "incomplete"})
        assert r.status_code == 200
        assert listing.status == ListingStatus.rejected
        assert listing.rejection_reason == "incomplete"

    def test_listing_models_dict_has_all_types(self):
        from api.routes.review import LISTING_MODELS

        for t in ("mcp", "skill", "hook", "prompt", "sandbox"):
            assert t in LISTING_MODELS


# ═══════════════════════════════════════════════════════════
# 5. TestFeedbackExtension
# ═══════════════════════════════════════════════════════════


class TestFeedbackExtension:
    """Verify the feedback schema accepts all 6 new listing types."""

    @pytest.mark.parametrize("lt", ["mcp", "agent", "skill", "hook", "prompt", "sandbox"])
    def test_feedback_schema_accepts_new_types(self, lt):
        from schemas.feedback import FeedbackCreateRequest

        req = FeedbackCreateRequest(listing_id=uuid.uuid4(), listing_type=lt, rating=4)
        assert req.listing_type == lt

    def test_feedback_schema_rejects_invalid_type(self):
        from schemas.feedback import FeedbackCreateRequest

        with pytest.raises(ValueError):
            FeedbackCreateRequest(listing_id=uuid.uuid4(), listing_type="invalid", rating=4)

    def test_feedback_schema_rejects_rating_out_of_range(self):
        from schemas.feedback import FeedbackCreateRequest

        with pytest.raises(ValueError):
            FeedbackCreateRequest(listing_id=uuid.uuid4(), listing_type="tool", rating=6)

    def test_feedback_schema_accepts_mcp_and_agent(self):
        from schemas.feedback import FeedbackCreateRequest

        for lt in ("mcp", "agent"):
            req = FeedbackCreateRequest(listing_id=uuid.uuid4(), listing_type=lt, rating=3)
            assert req.listing_type == lt


# ═══════════════════════════════════════════════════════════
# 6. TestParameterizedSearch
# ═══════════════════════════════════════════════════════════


def _list_endpoint_cases():
    """Every registry list route with its listing table, type filter, and expected filter value."""
    from api.routes.hook import list_hooks
    from api.routes.mcp import list_mcps
    from api.routes.prompt import list_prompts
    from api.routes.sandbox import list_sandboxes
    from api.routes.skill import list_skills

    return [
        (list_mcps, "mcp_listings", {"category": "developer-tools"}, "developer-tools"),
        (list_skills, "skill_listings", {"task_type": "testing", "target_agent": None, "harness": None}, "testing"),
        (list_hooks, "hook_listings", {"event": "Stop", "scope": None}, "Stop"),
        (list_prompts, "prompt_listings", {"category": "testing"}, "testing"),
        (list_sandboxes, "sandbox_listings", {"runtime_type": "docker"}, "docker"),
    ]


async def _run_list(list_items, filters, *, current_user, namespace="Alice"):
    """Call a registry list route directly and return the SELECT it executed.

    The routes carry no response cache, so every argument is passed explicitly:
    unset FastAPI ``Query`` defaults are sentinel objects, not ``None``.
    """
    from fastapi import Response

    db = _mock_db()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db.execute.return_value = result
    db.scalar.return_value = 0
    await list_items(
        response=Response(),
        namespace=namespace,
        search=None,
        team_id=None,
        composable_for_team_id=None,
        public_only=False,
        limit=50,
        offset=0,
        db=db,
        current_user=current_user,
        **filters,
    )
    return db.execute.await_args.args[0]


def _inline_sql(statement) -> str:
    """Compile a statement to a single-line SQL string with bind values inlined."""
    return " ".join(str(statement.compile(compile_kwargs={"literal_binds": True})).split())


def _membership_predicate(table: str, user) -> str:
    """The correlated EXISTS that gates team-private listings on the caller's own membership."""
    return (
        f"{table}.is_private = true AND {table}.team_id IS NOT NULL "
        "AND (EXISTS (SELECT team_memberships.id FROM team_memberships "
        f"WHERE team_memberships.team_id = {table}.team_id "
        f"AND team_memberships.user_id = '{user.id.hex}'))"
    )


@pytest.mark.asyncio
async def test_component_lists_apply_namespace_and_type_filters():
    for list_items, _table, filters, expected in _list_endpoint_cases():
        statement = await _run_list(list_items, filters, current_user=None)
        sql = str(statement)
        params = set(statement.compile().params.values())
        assert ".namespace" in sql
        assert {"alice", expected}.issubset(params)


@pytest.mark.asyncio
async def test_component_lists_hide_team_private_listings_from_anonymous_callers():
    for list_items, table, filters, _expected in _list_endpoint_cases():
        sql = _inline_sql(await _run_list(list_items, filters, current_user=None))
        assert f"{table}.is_private = false" in sql
        assert f"{table}.is_private = true" not in sql
        assert "team_memberships" not in sql


@pytest.mark.asyncio
async def test_component_lists_gate_team_private_listings_on_caller_membership():
    """A team-private listing is reachable only through a membership row for the caller.

    The same predicate both excludes non-members and includes members, and it is
    bound to the calling user's id, so no other user's rows can leak in.
    """
    caller = _user(role=UserRole.user)
    outsider = _user(role=UserRole.user)
    for list_items, table, filters, _expected in _list_endpoint_cases():
        sql = _inline_sql(await _run_list(list_items, filters, current_user=caller))
        assert _membership_predicate(table, caller) in sql
        assert outsider.id.hex not in sql


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [UserRole.admin, UserRole.super_admin])
async def test_component_lists_do_not_restrict_visibility_for_admins(role):
    admin = _user(role=role)
    for list_items, table, filters, _expected in _list_endpoint_cases():
        sql = _inline_sql(await _run_list(list_items, filters, current_user=admin))
        assert f"{table}.is_private =" not in sql
        assert "team_memberships" not in sql


@pytest.mark.asyncio
async def test_component_lists_gate_a_global_reviewer_on_membership_like_anyone_else():
    """The global reviewer role reviews the public catalog, not other teams' rows.

    Team-private items are reviewed inside their own teamspace, so a reviewer gets
    the same membership predicate as a plain user and only admins skip it.
    """
    reviewer = _user(role=UserRole.reviewer)
    for list_items, table, filters, _expected in _list_endpoint_cases():
        sql = _inline_sql(await _run_list(list_items, filters, current_user=reviewer))
        assert _membership_predicate(table, reviewer) in sql


# ═══════════════════════════════════════════════════════════
# 7. TestCLICommands
# ═══════════════════════════════════════════════════════════


class TestCLICommands:
    """Verify CLI command groups exist with expected subcommands."""

    def _get_command_names(self, typer_app):
        """Extract registered command names from a Typer app."""
        info = typer_app.registered_commands
        return [c.name or c.callback.__name__ for c in info]

    def test_skill_app_exists(self):
        from observal_cli.cmd_skill import skill_app

        assert skill_app is not None

    def test_skill_app_has_subcommands(self):
        from observal_cli.cmd_skill import skill_app

        names = self._get_command_names(skill_app)
        for cmd in ("submit", "list", "show", "install"):
            assert cmd in names, f"skill missing '{cmd}' subcommand"
