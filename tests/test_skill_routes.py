# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, call

import pytest
from fastapi import FastAPI, HTTPException, Response
from httpx import ASGITransport, AsyncClient
from sqlalchemy import literal

from api.deps import get_current_user, get_db
from api.routes import skill
from models.mcp import ListingStatus
from models.skill import SkillDownload, SkillListing, SkillVersion
from models.user import UserRole
from schemas.skill import (
    SkillDraftRequest,
    SkillInstallRequest,
    SkillSubmitRequest,
    SkillUpdateRequest,
)
from services.skill_validator import SkillAnalysis, SkillValidationError
from services.teamspace import PublishTarget

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
LISTING_ID = uuid.UUID(int=101)
VERSION_ID = uuid.UUID(int=102)
USER_ID = uuid.UUID(int=103)
OTHER_USER_ID = uuid.UUID(int=104)
TEAM_ID = uuid.UUID(int=105)


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


def _user(*, user_id=USER_ID, role=UserRole.user, username="alice", email="alice@example.test"):
    return SimpleNamespace(id=user_id, role=role, username=username, email=email)


def _db():
    return SimpleNamespace(
        add=Mock(),
        flush=AsyncMock(),
        refresh=AsyncMock(),
        execute=AsyncMock(),
        scalar=AsyncMock(),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )


def _result(value=None, *, rows=None):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalar_one.return_value = value
    result.scalar.return_value = value
    result.first.return_value = value
    result.all.return_value = list(rows or [])
    result.scalars.return_value.all.return_value = list(rows if rows is not None else ([value] if value else []))
    result.scalars.return_value.first.return_value = value
    return result


def _listing(
    *,
    status=ListingStatus.approved,
    submitted_by=OTHER_USER_ID,
    name="Review Skill",
    namespace="alice",
    slug="review-skill",
    description="Review changes safely",
    version="1.2.3",
    is_private=False,
    team_id=None,
    slash_command="review",
    skill_md_content=None,
    download_count=7,
):
    listing = SkillListing(
        id=LISTING_ID,
        name=name,
        namespace=namespace,
        slug=slug,
        owner=namespace,
        submitted_by=submitted_by,
        co_authors=[],
        is_private=is_private,
        team_id=team_id,
    )
    listing.created_at = NOW
    listing.updated_at = NOW
    latest = SkillVersion(
        id=VERSION_ID,
        listing_id=listing.id,
        version=version,
        description=description,
        status=status,
        released_by=submitted_by,
        released_at=NOW,
        reviewed_by=None,
        reviewed_at=None,
        supported_harnesses=["claude-code", "pi"],
        skill_path="skills/review",
        git_url="https://github.com/acme/review",
        git_ref="main",
        skill_md_content=skill_md_content,
        delivery_mode="git_fetch",
        script_content=None,
        script_filename=None,
        validated=True,
        target_agents=["claude-code"],
        task_type="code-review",
        slash_command=slash_command,
        download_count=download_count,
        is_editing=False,
        editing_by=None,
        editing_since=None,
    )
    latest.created_at = NOW
    listing.latest_version = latest
    listing.latest_version_id = latest.id
    return listing


def _submit_request(**overrides):
    values = {
        "name": "Review Skill",
        "version": "1.0.0",
        "description": "Review changes",
        "owner": "alice",
        "task_type": "code-review",
    }
    values.update(overrides)
    return SkillSubmitRequest(**values)


def _draft_request(**overrides):
    values = {
        "name": "Review Skill",
        "version": "0.1.0",
        "description": "Review changes",
        "owner": "",
        "task_type": "code-review",
    }
    values.update(overrides)
    return SkillDraftRequest(**values)


def _target(*, auto_approve=False, team_id=None, visibility="public"):
    return PublishTarget(
        namespace="platform" if team_id else "alice",
        slug="review-skill",
        team_id=team_id,
        visibility=visibility,
        owner="platform" if team_id else "alice",
        auto_approve=auto_approve,
    )


def _http_error(exc, status, detail):
    assert exc.value.status_code == status
    assert exc.value.detail == detail


def _sql(statement):
    return " ".join(str(statement).split())


def _prepare_new_rows(db):
    added = db.add.call_args_list
    current = added[-1].args[0]
    if isinstance(current, SkillListing):
        current.id = LISTING_ID
        current.created_at = NOW
        current.updated_at = NOW
    else:
        current.id = VERSION_ID
        current.created_at = NOW
        current.download_count = 0
        current.validated = bool(current.validated)


def _mock_skill_lock(monkeypatch, listing):
    """Unit-route mock; integration tests exercise the real ordered row locks."""
    monkeypatch.setattr(
        skill, "lock_skill_version", AsyncMock(return_value=(listing.latest_version_id, listing.latest_version))
    )


def _refresh_new_listing(db, listing):
    version = next(entry.args[0] for entry in db.add.call_args_list if isinstance(entry.args[0], SkillVersion))
    listing.latest_version = version
    listing.created_at = NOW
    listing.updated_at = NOW


class TestStoredContentValidation:
    def test_returns_normalized_frontmatter(self):
        analysis = skill._validate_stored_skill_md(
            '---\nname: review\ndescription: Reviews code\ncommand: "/review"\n---\nBody\n'
        )

        assert analysis.frontmatter["name"] == "review"
        assert analysis.slash_command == "review"

    def test_maps_validation_error_to_exact_http_contract(self, monkeypatch):
        monkeypatch.setattr(
            skill,
            "validate_skill_md_content_frontmatter",
            Mock(side_effect=SkillValidationError("Malformed SKILL.md frontmatter")),
        )

        with pytest.raises(HTTPException) as exc:
            skill._validate_stored_skill_md("broken")

        _http_error(exc, 422, "Malformed SKILL.md frontmatter")


class TestSubmitSkill:
    @pytest.mark.asyncio
    async def test_registry_direct_persists_frontmatter_metadata_in_transaction_order(self, monkeypatch):
        db = _db()
        events = []
        db.add.side_effect = lambda row: events.append(f"add:{type(row).__name__}")

        async def flush():
            _prepare_new_rows(db)
            events.append("flush")

        async def refresh(row):
            _refresh_new_listing(db, row)
            events.append("refresh")

        db.flush.side_effect = flush
        db.refresh.side_effect = refresh
        target = _target(team_id=TEAM_ID, visibility="team")
        resolve_target = AsyncMock(return_value=target)
        identity_exists = AsyncMock(return_value=False)
        publish = AsyncMock(side_effect=lambda *args, **kwargs: events.append("inbox"))
        commit = AsyncMock(side_effect=lambda *args, **kwargs: events.append("commit"))
        monkeypatch.setattr(skill, "datetime", FrozenDateTime)
        monkeypatch.setattr(skill, "resolve_publish_target", resolve_target)
        monkeypatch.setattr(skill, "identity_exists", identity_exists)
        monkeypatch.setattr(skill.inbox, "on_publish", publish)
        monkeypatch.setattr(skill, "commit_or_name_conflict", commit)
        content = '---\nname: Frontmatter Name\ndescription: Frontmatter description\ncommand: "/review"\n---\nBody\n'
        request = _submit_request(
            name="",
            description="",
            owner="ignored-owner",
            team_id=TEAM_ID,
            visibility="team",
            delivery_mode="registry_direct",
            skill_md_content=content,
            script_content="print('review')",
            script_filename="review.py",
            slash_command=None,
            target_agents=["pi"],
            supported_harnesses=["pi"],
        )

        response = await skill.submit_skill(request, db, _user())

        listing, version = [entry.args[0] for entry in db.add.call_args_list]
        assert isinstance(listing, SkillListing)
        assert isinstance(version, SkillVersion)
        assert (
            listing.id,
            listing.name,
            listing.namespace,
            listing.slug,
            listing.owner,
            listing.submitted_by,
            listing.team_id,
            listing.is_private,
        ) == (
            LISTING_ID,
            "Frontmatter Name",
            "platform",
            "review-skill",
            "platform",
            USER_ID,
            TEAM_ID,
            True,
        )
        assert (
            version.listing_id,
            version.version,
            version.description,
            version.skill_md_content,
            version.delivery_mode,
            version.script_content,
            version.script_filename,
            version.validated,
            version.slash_command,
            version.status,
            version.released_by,
            version.released_at,
            version.reviewed_by,
            version.reviewed_at,
        ) == (
            LISTING_ID,
            "1.0.0",
            "Frontmatter description",
            content,
            "registry_direct",
            "print('review')",
            "review.py",
            True,
            "review",
            ListingStatus.pending,
            USER_ID,
            NOW,
            None,
            None,
        )
        assert listing.latest_version_id == VERSION_ID
        assert response.model_dump() == {
            "id": LISTING_ID,
            "name": "Frontmatter Name",
            "namespace": "platform",
            "slug": "review-skill",
            "qualified_name": "platform/review-skill",
            "version": "1.0.0",
            "description": "Frontmatter description",
            "owner": "platform",
            "team_id": TEAM_ID,
            "visibility": "team",
            "is_private": True,
            "task_type": "code-review",
            "target_agents": ["pi"],
            "supported_harnesses": ["pi"],
            "skill_path": "/",
            "git_url": None,
            "git_ref": None,
            "skill_md_content": content,
            "delivery_mode": "registry_direct",
            "script_content": "print('review')",
            "script_filename": "review.py",
            "validated": True,
            "slash_command": "review",
            "status": ListingStatus.pending,
            "rejection_reason": None,
            "submitted_by": USER_ID,
            "created_at": NOW,
            "updated_at": NOW,
            "download_count": 0,
            "user_permission": None,
            "is_recommended": False,
            "forked_from": None,
            "fork_count": 0,
            "warnings": [],
        }
        assert events == [
            "add:SkillListing",
            "flush",
            "add:SkillVersion",
            "flush",
            "inbox",
            "commit",
            "refresh",
        ]
        resolve_target.assert_awaited_once_with(
            db,
            _user(),
            "Frontmatter Name",
            team_id=TEAM_ID,
            visibility="team",
        )
        identity_exists.assert_awaited_once_with(db, SkillListing, "platform", "review-skill")
        publish.assert_awaited_once_with(
            db,
            listing,
            subject_type="skill",
            actor_id=USER_ID,
            auto_approved=False,
            version="1.0.0",
        )
        commit.assert_awaited_once_with(db, "skill")

    @pytest.mark.asyncio
    async def test_git_validation_discovers_path_and_metadata(self, monkeypatch):
        db = _db()
        db.flush.side_effect = lambda: _prepare_new_rows(db)
        db.refresh.side_effect = lambda row: _refresh_new_listing(db, row)
        content = "---\nname: Git Review\ndescription: From Git\ncommand: /git-review\n---\nBody\n"
        validate = AsyncMock(
            return_value=SkillAnalysis(
                name="Git Review",
                description="From Git",
                slash_command="git-review",
                raw_content=content,
                discovered_path="skills/git-review",
            )
        )
        monkeypatch.setattr(skill, "validate_skill_md", validate)
        monkeypatch.setattr(skill, "resolve_publish_target", AsyncMock(return_value=_target()))
        monkeypatch.setattr(skill, "identity_exists", AsyncMock(return_value=False))
        monkeypatch.setattr(skill.inbox, "on_publish", AsyncMock(return_value=1))
        monkeypatch.setattr(skill, "commit_or_name_conflict", AsyncMock())
        request = _submit_request(
            name="",
            description="",
            git_url="https://github.com/acme/skills",
            git_ref=None,
            skill_path="/",
            slash_command=None,
        )

        response = await skill.submit_skill(request, db, _user())

        version = next(entry.args[0] for entry in db.add.call_args_list if isinstance(entry.args[0], SkillVersion))
        validate.assert_awaited_once_with(
            "https://github.com/acme/skills",
            skill_path="/",
            git_ref="main",
        )
        assert (
            response.name,
            version.description,
            version.skill_path,
            version.skill_md_content,
            version.slash_command,
            version.validated,
        ) == ("Git Review", "From Git", "skills/git-review", content, "git-review", True)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("payload", "detail"),
        [
            (
                _submit_request(delivery_mode="registry_direct", skill_md_content=None),
                "skill_md_content is required for registry_direct delivery",
            ),
            (_submit_request(name=""), "name is required"),
            (_submit_request(description=""), "description is required"),
        ],
    )
    async def test_validation_failures_precede_database_mutation(self, monkeypatch, payload, detail):
        db = _db()
        target = AsyncMock()
        monkeypatch.setattr(skill, "resolve_publish_target", target)

        with pytest.raises(HTTPException) as exc:
            await skill.submit_skill(payload, db, _user())

        _http_error(exc, 422, detail)
        target.assert_not_awaited()
        db.add.assert_not_called()
        db.flush.assert_not_awaited()
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_git_validation_and_command_mismatch_fail_without_mutation(self, monkeypatch):
        db = _db()
        target = AsyncMock()
        monkeypatch.setattr(skill, "resolve_publish_target", target)
        analysis = SkillAnalysis(
            name="Review",
            description="Review",
            slash_command="from-file",
            raw_content="Body",
        )
        validate = AsyncMock(return_value=analysis)
        monkeypatch.setattr(skill, "validate_skill_md", validate)

        with pytest.raises(HTTPException) as mismatch:
            await skill.submit_skill(
                _submit_request(git_url="https://github.com/acme/review", slash_command="requested"),
                db,
                _user(),
            )
        _http_error(mismatch, 422, "slash_command does not match SKILL.md frontmatter command")

        validate.side_effect = SkillValidationError("SKILL.md not found")
        with pytest.raises(HTTPException) as invalid:
            await skill.submit_skill(_submit_request(git_url="https://github.com/acme/missing"), db, _user())
        _http_error(invalid, 422, "SKILL.md not found")
        target.assert_not_awaited()
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_normalized_command_fails_before_target_resolution(self, monkeypatch):
        db = _db()
        target = AsyncMock()
        monkeypatch.setattr(skill, "resolve_publish_target", target)
        monkeypatch.setattr(skill, "normalize_slash_command", Mock(side_effect=ValueError("unsafe")))

        with pytest.raises(HTTPException) as exc:
            await skill.submit_skill(_submit_request(), db, _user())

        _http_error(exc, 422, "Invalid slash_command: unsafe")
        target.assert_not_awaited()
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_identity_conflict_is_exact_and_does_not_add_rows(self, monkeypatch):
        db = _db()
        monkeypatch.setattr(skill, "resolve_publish_target", AsyncMock(return_value=_target()))
        monkeypatch.setattr(skill, "identity_exists", AsyncMock(return_value=True))

        with pytest.raises(HTTPException) as exc:
            await skill.submit_skill(_submit_request(), db, _user())

        _http_error(exc, 409, "Skill 'alice/review-skill' already exists")
        db.add.assert_not_called()
        db.flush.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_inbox_failure_prevents_commit_and_refresh(self, monkeypatch):
        db = _db()
        db.flush.side_effect = lambda: _prepare_new_rows(db)
        monkeypatch.setattr(skill, "resolve_publish_target", AsyncMock(return_value=_target()))
        monkeypatch.setattr(skill, "identity_exists", AsyncMock(return_value=False))
        monkeypatch.setattr(skill.inbox, "on_publish", AsyncMock(side_effect=RuntimeError("inbox unavailable")))
        commit = AsyncMock()
        monkeypatch.setattr(skill, "commit_or_name_conflict", commit)

        with pytest.raises(RuntimeError, match="inbox unavailable"):
            await skill.submit_skill(_submit_request(), db, _user())

        assert db.add.call_count == 2
        commit.assert_not_awaited()
        db.refresh.assert_not_awaited()


class TestListAndDetail:
    @pytest.fixture(autouse=True)
    def _stub_fork_enrichment(self, monkeypatch):
        # Query-shape tests isolate fork metadata; HTTP fork tests cover its SQL.
        from api.routes.component_forks import RESPONSE_MODELS, SUMMARY_MODELS

        async def serialize(items, kind, _user, _db, *, summary=True):
            schema = SUMMARY_MODELS[kind] if summary else RESPONSE_MODELS[kind]
            return [schema.model_validate(item) for item in items]

        async def attach(response, *_args, **_kwargs):
            return response

        monkeypatch.setattr(skill, "component_responses", serialize)
        monkeypatch.setattr(skill, "attach_fork_info", attach)

    @pytest.mark.asyncio
    async def test_list_builds_all_filters_count_order_and_pagination(self, monkeypatch):
        db = _db()
        listing = _listing()
        db.scalar.return_value = 9
        db.execute.return_value = _result(rows=[listing])
        scope = Mock(side_effect=lambda stmt, model, user, **kwargs: stmt.where(model.team_id == TEAM_ID))
        search = Mock(
            side_effect=[
                (SkillVersion.task_type == "agent-match", None),
                (SkillListing.name == "needle", literal(4)),
            ]
        )
        monkeypatch.setattr(skill, "apply_registry_scope", scope)
        monkeypatch.setattr(skill, "keyword_search", search)
        response = Response()
        user = _user(role=UserRole.admin)

        rows = await skill.list_skills(
            response=response,
            task_type="code-review",
            target_agent="claude",
            harness="pi%_",
            namespace=" Alice ",
            search="needle",
            team_id=TEAM_ID,
            composable_for_team_id=None,
            public_only=False,
            limit=25,
            offset=50,
            db=db,
            current_user=user,
        )

        assert [row.qualified_name for row in rows] == ["alice/review-skill"]
        assert response.headers["X-Total-Count"] == "9"
        assert search.call_count == 2
        target_query, target_fields = search.call_args_list[0].args
        assert target_query == "claude"
        assert len(target_fields) == 1
        assert str(target_fields[0]) == "CAST(skill_versions.target_agents AS VARCHAR)"
        assert search.call_args_list[1].kwargs["name_field"] is SkillListing.name
        scope.assert_called_once_with(
            scope.call_args.args[0],
            SkillListing,
            user,
            team_id=TEAM_ID,
            composable_for_team_id=None,
            public_only=False,
        )
        count_stmt = db.scalar.await_args.args[0]
        data_stmt = db.execute.await_args.args[0]
        count_sql = _sql(count_stmt)
        data_sql = _sql(data_stmt)
        for fragment in (
            "skill_versions.status =",
            "skill_versions.task_type =",
            "CAST(skill_versions.supported_harnesses AS VARCHAR)",
            "skill_listings.namespace =",
            "skill_versions.task_type =",
            "skill_listings.name =",
            "skill_listings.team_id =",
        ):
            assert fragment in count_sql
            assert fragment in data_sql
        assert "SELECT count(*) AS count_1 FROM (SELECT" in count_sql
        assert " DESC, skill_listings.created_at DESC" in data_sql
        assert " LIMIT " in data_sql
        assert " OFFSET " in data_sql
        params = data_stmt.compile().params
        assert {"code-review", '%"pi\\%\\_"%', "alice", "agent-match", "needle", TEAM_ID, 25, 50} <= set(
            params.values()
        )

    @pytest.mark.asyncio
    async def test_my_skills_applies_authorship_visibility_and_order(self, monkeypatch):
        db = _db()
        listing = _listing(submitted_by=USER_ID)
        db.execute.return_value = _result(rows=[listing])
        visibility = Mock(side_effect=lambda stmt, model, user: stmt.where(model.is_private.is_(False)))
        monkeypatch.setattr(skill, "apply_visibility_filter", visibility)
        user = _user()

        rows = await skill.my_skills(db, user)

        assert [row.qualified_name for row in rows] == ["alice/review-skill"]
        visibility.assert_called_once()
        stmt = db.execute.await_args.args[0]
        sql = _sql(stmt)
        assert "skill_listings.submitted_by =" in sql
        assert "skill_listings.is_private IS false" in sql
        assert "ORDER BY skill_listings.created_at DESC" in sql
        assert USER_ID in stmt.compile().params.values()

    @pytest.mark.asyncio
    async def test_approved_detail_resolves_canonical_identity_once(self, monkeypatch):
        db = _db()
        listing = _listing()
        resolve = AsyncMock(return_value=listing)
        monkeypatch.setattr(skill, "resolve_visible_listing", resolve)
        authorized = AsyncMock(return_value=(listing, listing.latest_version))
        monkeypatch.setattr(skill, "_authorized_version", authorized)
        user = _user()

        response = await skill.get_skill("Alice/Review-Skill", db, user)

        assert response.qualified_name == "alice/review-skill"
        assert response.user_permission == "view"
        resolve.assert_awaited_once_with(
            SkillListing,
            "Alice/Review-Skill",
            db,
            user,
            load_options=skill._BODY_FREE_LISTING,
        )
        authorized.assert_awaited_once_with(str(listing.id), listing.latest_version_id, db, user)

    @pytest.mark.asyncio
    async def test_detail_refuses_pointer_changed_during_authorization(self, monkeypatch):
        db = _db()
        listing = _listing()
        monkeypatch.setattr(skill, "resolve_visible_listing", AsyncMock(return_value=listing))
        monkeypatch.setattr(skill, "_authorized_version", AsyncMock(return_value=(listing, listing.latest_version)))
        db.refresh.side_effect = lambda row, **kwargs: setattr(row, "latest_version_id", uuid.uuid4())

        with pytest.raises(HTTPException) as stale:
            await skill.get_skill(str(listing.id), db, _user())
        _http_error(stale, 409, "Skill latest version changed; refresh the listing")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("user", "status", "expected_permission"),
        [
            (_user(), ListingStatus.pending, "owner"),
            (_user(role=UserRole.reviewer), ListingStatus.pending, "view"),
        ],
    )
    async def test_owner_and_reviewer_can_read_unapproved_detail(self, monkeypatch, user, status, expected_permission):
        db = _db()
        listing = _listing(status=status, submitted_by=USER_ID if expected_permission == "owner" else OTHER_USER_ID)
        resolve = AsyncMock(return_value=listing)
        monkeypatch.setattr(skill, "resolve_visible_listing", resolve)
        monkeypatch.setattr(skill, "_authorized_version", AsyncMock(return_value=(listing, listing.latest_version)))

        response = await skill.get_skill("alice/review-skill", db, user)

        assert response.status == status
        assert response.user_permission == expected_permission
        resolve.assert_awaited_once_with(
            SkillListing, "alice/review-skill", db, user, load_options=skill._BODY_FREE_LISTING
        )

    @pytest.mark.asyncio
    async def test_unapproved_nonowner_and_missing_detail_share_404(self, monkeypatch):
        db = _db()
        db.execute.return_value = _result(rows=[])
        listing = _listing(status=ListingStatus.pending)
        resolve = AsyncMock(return_value=listing)
        monkeypatch.setattr(skill, "resolve_visible_listing", resolve)
        monkeypatch.setattr(
            skill, "_authorized_version", AsyncMock(side_effect=HTTPException(404, "Skill version not found"))
        )

        with pytest.raises(HTTPException) as hidden:
            await skill.get_skill(
                "alice/review-skill",
                db,
                _user(),
            )
        _http_error(hidden, 404, "Skill version not found")

        resolve.side_effect = [None, None]
        with pytest.raises(HTTPException) as missing:
            await skill.get_skill("missing", db, _user())
        _http_error(missing, 404, "Listing not found")


class TestInstallSkill:
    @pytest.fixture(autouse=True)
    def _selected_row(self, monkeypatch):
        async def authorized(listing_id, version_id, db, current_user):
            listing = skill.resolve_visible_listing.return_value
            if version_id == listing.latest_version_id:
                return listing, listing.latest_version
            version = db.execute.return_value.scalar_one_or_none()
            if version is None:
                raise HTTPException(status_code=404, detail="Skill version not found")
            return listing, version

        monkeypatch.setattr(skill, "_authorized_version", authorized)

    @pytest.mark.asyncio
    async def test_anonymous_approved_install_does_not_change_download_metrics(self, monkeypatch):
        db = _db()
        listing = _listing()
        commit = AsyncMock()
        monkeypatch.setattr(skill, "resolve_visible_listing", AsyncMock(return_value=listing))
        monkeypatch.setattr(skill, "commit_or_name_conflict", commit)
        monkeypatch.setattr("api.routes.config.derive_endpoints", AsyncMock(return_value={"api": "https://api.test"}))
        monkeypatch.setattr(
            "services.skill_config_generator.generate_skill_config",
            Mock(return_value={"skill": {"name": "review"}}),
        )

        response = await skill.install_skill(
            "alice/review-skill",
            SkillInstallRequest(harness="pi"),
            MagicMock(),
            db,
            None,
        )

        assert response.config_snippet == {"skill": {"name": "review"}}
        assert listing.latest_version.download_count == 7
        db.add.assert_not_called()
        commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_anonymous_archived_install_is_hidden(self, monkeypatch):
        db = _db()
        listing = _listing(status=ListingStatus.archived)
        monkeypatch.setattr(skill, "resolve_visible_listing", AsyncMock(side_effect=[None, listing]))
        commit = AsyncMock()
        monkeypatch.setattr(skill, "commit_or_name_conflict", commit)

        with pytest.raises(HTTPException) as exc:
            await skill.install_skill(
                "alice/review-skill",
                SkillInstallRequest(harness="pi"),
                MagicMock(),
                db,
                None,
            )

        _http_error(exc, 404, "Listing not found or not approved")
        assert listing.latest_version.download_count == 7
        db.add.assert_not_called()
        commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_archived_version_install_tracks_usage_then_generates_exact_config(self, monkeypatch):
        db = _db()
        listing = _listing(status=ListingStatus.archived)
        override = SkillVersion(
            id=uuid.UUID(int=120),
            listing_id=listing.id,
            version="1.0.0",
            description="Old review",
            status=ListingStatus.approved,
            released_by=OTHER_USER_ID,
            released_at=NOW,
            task_type="code-review",
        )
        db.execute.return_value = _result(override)
        resolve = AsyncMock(return_value=listing)
        events = []
        commit = AsyncMock(side_effect=lambda *args: events.append("commit"))
        derive = AsyncMock(side_effect=lambda request: events.append("derive") or {"api": "https://api.test"})
        generate = Mock(side_effect=lambda *args, **kwargs: events.append("generate") or {"skill": {"ok": True}})
        monkeypatch.setattr(skill, "resolve_visible_listing", resolve)
        monkeypatch.setattr(skill, "commit_or_name_conflict", commit)
        monkeypatch.setattr("api.routes.config.derive_endpoints", derive)
        monkeypatch.setattr("services.skill_config_generator.generate_skill_config", generate)
        request = MagicMock(name="request")
        install = SkillInstallRequest(harness="pi", scope="user", local_name="local-review", version="1.0.0")

        response = await skill.install_skill("alice/review-skill", install, request, db, _user())

        assert response.model_dump() == {
            "listing_id": LISTING_ID,
            "harness": "pi",
            "config_snippet": {"skill": {"ok": True}},
            "warnings": ["Archived skill 'Review Skill' is deprecated and may be removed from future agent pulls."],
            "version": "1.0.0",
            "version_id": override.id,
            "digest": response.digest,
        }
        assert response.digest.startswith("sha256:")
        resolve.assert_awaited_once_with(
            SkillListing, "alice/review-skill", db, _user(), load_options=skill._BODY_FREE_LISTING
        )
        version_stmt = db.execute.await_args.args[0]
        assert _sql(version_stmt).startswith("SELECT skill_versions.id")
        params = version_stmt.compile().params
        assert params["listing_id_1"] == LISTING_ID
        assert params["version_1"] == "1.0.0"
        download = db.add.call_args.args[0]
        assert isinstance(download, SkillDownload)
        assert (download.listing_id, download.user_id, download.harness) == (LISTING_ID, USER_ID, "pi")
        # The download is counted against the version that was installed.
        assert override.download_count == 1
        assert listing.latest_version.download_count == 7
        assert events == ["derive", "generate", "commit"]
        commit.assert_awaited_once_with(db, "skill")
        derive.assert_awaited_once_with(request)
        generate.assert_called_once_with(
            listing,
            "pi",
            server_url="https://api.test",
            scope="user",
            version_override=override,
            local_name="local-review",
        )

    @pytest.mark.asyncio
    async def test_pending_owner_fallback_can_install(self, monkeypatch):
        db = _db()
        listing = _listing(status=ListingStatus.pending, submitted_by=USER_ID)
        resolve = AsyncMock(return_value=listing)
        monkeypatch.setattr(skill, "resolve_visible_listing", resolve)
        monkeypatch.setattr(skill, "commit_or_name_conflict", AsyncMock())
        monkeypatch.setattr("api.routes.config.derive_endpoints", AsyncMock(return_value={"api": "https://api.test"}))
        generate = Mock(return_value={"skill": {"name": "review"}})
        monkeypatch.setattr("services.skill_config_generator.generate_skill_config", generate)

        response = await skill.install_skill(
            "alice/review-skill",
            SkillInstallRequest(harness="claude-code"),
            MagicMock(),
            db,
            _user(),
        )

        assert response.config_snippet == {"skill": {"name": "review"}}
        assert listing.latest_version.download_count == 8
        assert isinstance(db.add.call_args.args[0], SkillDownload)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("found", [None, "nonowner"])
    async def test_missing_or_unapproved_nonowner_install_is_404_without_usage(self, monkeypatch, found):
        db = _db()
        fallback = None if found is None else _listing(status=ListingStatus.pending)
        resolve = AsyncMock(return_value=fallback)
        monkeypatch.setattr(skill, "resolve_visible_listing", resolve)
        commit = AsyncMock()
        monkeypatch.setattr(skill, "commit_or_name_conflict", commit)

        with pytest.raises(HTTPException) as exc:
            await skill.install_skill(
                "alice/review-skill",
                SkillInstallRequest(harness="pi"),
                MagicMock(),
                db,
                _user(),
            )

        _http_error(exc, 404, "Listing not found or not approved")
        db.add.assert_not_called()
        commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_approved_old_version_is_installed_even_when_candidate_is_latest(self, monkeypatch):
        db = _db()
        listing = _listing(status=ListingStatus.pending, submitted_by=OTHER_USER_ID)
        previous = SkillVersion(
            id=uuid.UUID(int=123),
            listing_id=listing.id,
            version="1.0.0",
            description="Previous",
            status=ListingStatus.approved,
            released_by=OTHER_USER_ID,
            released_at=NOW,
            task_type="code-review",
        )
        db.execute.return_value = _result(previous)
        monkeypatch.setattr(skill, "resolve_visible_listing", AsyncMock(return_value=listing))
        monkeypatch.setattr(skill, "commit_or_name_conflict", AsyncMock())
        monkeypatch.setattr("api.routes.config.derive_endpoints", AsyncMock(return_value={"api": "https://api.test"}))
        config = Mock(return_value={"skill": {"version": "1.0.0"}})
        monkeypatch.setattr("services.skill_config_generator.generate_skill_config", config)

        response = await skill.install_skill(
            str(LISTING_ID), SkillInstallRequest(harness="pi", version="1.0.0"), MagicMock(), db, _user()
        )

        assert response.version_id == previous.id
        assert response.warnings == []
        assert previous.download_count == 1
        assert listing.latest_version.download_count == 7
        assert config.call_args.kwargs["version_override"] is previous

    @pytest.mark.asyncio
    async def test_unknown_requested_version_does_not_record_download(self, monkeypatch):
        db = _db()
        listing = _listing()
        db.execute.return_value = _result(None)
        monkeypatch.setattr(skill, "resolve_visible_listing", AsyncMock(return_value=listing))
        commit = AsyncMock()
        monkeypatch.setattr(skill, "commit_or_name_conflict", commit)

        with pytest.raises(HTTPException) as exc:
            await skill.install_skill(
                str(LISTING_ID),
                SkillInstallRequest(harness="pi", version="9.9.9"),
                MagicMock(),
                db,
                _user(),
            )

        _http_error(exc, 404, "Version '9.9.9' not found for this skill")
        db.add.assert_not_called()
        commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_config_failure_does_not_record_usage(self, monkeypatch):
        db = _db()
        listing = _listing()
        monkeypatch.setattr(skill, "resolve_visible_listing", AsyncMock(return_value=listing))
        commit = AsyncMock()
        monkeypatch.setattr(skill, "commit_or_name_conflict", commit)
        monkeypatch.setattr("api.routes.config.derive_endpoints", AsyncMock(return_value={"api": "https://api.test"}))
        generate = Mock(side_effect=ValueError("unsupported harness"))
        monkeypatch.setattr("services.skill_config_generator.generate_skill_config", generate)

        with pytest.raises(ValueError, match="unsupported harness"):
            await skill.install_skill(
                str(LISTING_ID),
                SkillInstallRequest(harness="unknown"),
                MagicMock(),
                db,
                _user(),
            )

        db.add.assert_not_called()
        assert listing.latest_version.download_count == 7
        commit.assert_not_awaited()
        generate.assert_called_once()

    @pytest.mark.asyncio
    async def test_digest_failure_does_not_record_usage(self, monkeypatch):
        db = _db()
        listing = _listing()
        monkeypatch.setattr(skill, "resolve_visible_listing", AsyncMock(return_value=listing))
        commit = AsyncMock()
        monkeypatch.setattr(skill, "commit_or_name_conflict", commit)
        monkeypatch.setattr("api.routes.config.derive_endpoints", AsyncMock(return_value={"api": "https://api.test"}))
        monkeypatch.setattr("services.skill_config_generator.generate_skill_config", Mock(return_value={"skill": {}}))
        monkeypatch.setattr("services.agent_lock.content_digest", Mock(side_effect=ValueError("bad digest")))

        with pytest.raises(ValueError, match="bad digest"):
            await skill.install_skill(str(LISTING_ID), SkillInstallRequest(harness="pi"), MagicMock(), db, _user())

        db.add.assert_not_called()
        assert listing.latest_version.download_count == 7
        commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_commit_failure_does_not_return_generated_config(self, monkeypatch):
        db = _db()
        listing = _listing()
        monkeypatch.setattr(skill, "resolve_visible_listing", AsyncMock(return_value=listing))
        monkeypatch.setattr(skill, "commit_or_name_conflict", AsyncMock(side_effect=RuntimeError("commit failed")))
        derive = AsyncMock(return_value={"api": "https://api.test"})
        generated = Mock(return_value={"skill": {"ok": True}})
        monkeypatch.setattr("api.routes.config.derive_endpoints", derive)
        monkeypatch.setattr("services.skill_config_generator.generate_skill_config", generated)

        with pytest.raises(RuntimeError, match="commit failed"):
            await skill.install_skill(
                str(LISTING_ID),
                SkillInstallRequest(harness="pi"),
                MagicMock(),
                db,
                _user(),
            )

        derive.assert_awaited_once()
        generated.assert_called_once()


class TestDraftCreationAndUpdates:
    @pytest.mark.asyncio
    async def test_save_draft_uses_owner_fallback_and_frontmatter_command(self, monkeypatch):
        db = _db()
        db.flush.side_effect = lambda: _prepare_new_rows(db)
        db.refresh.side_effect = lambda row: _refresh_new_listing(db, row)
        monkeypatch.setattr(skill, "datetime", FrozenDateTime)
        monkeypatch.setattr(skill, "resolve_publish_target", AsyncMock(return_value=_target()))
        monkeypatch.setattr(skill, "identity_exists", AsyncMock(return_value=False))
        monkeypatch.setattr(skill, "commit_or_name_conflict", AsyncMock())
        content = '---\nname: review\ndescription: Review\ncommand: "/draft-review"\n---\nBody\n'
        request = _draft_request(
            skill_md_content=content,
            slash_command=None,
            delivery_mode="registry_direct",
            script_content="print('x')",
            script_filename="run.py",
            target_agents=["pi"],
            supported_harnesses=["pi"],
        )
        user = _user(username="", email="fallback@example.test")

        response = await skill.save_skill_draft(request, db, user)

        listing, version = [entry.args[0] for entry in db.add.call_args_list]
        assert listing.owner == "fallback@example.test"
        assert (listing.namespace, listing.slug, listing.submitted_by, listing.is_private) == (
            "alice",
            "review-skill",
            USER_ID,
            False,
        )
        assert (
            version.status,
            version.slash_command,
            version.delivery_mode,
            version.script_content,
            version.script_filename,
            version.released_at,
        ) == (ListingStatus.draft, "draft-review", "registry_direct", "print('x')", "run.py", NOW)
        assert response.status == ListingStatus.draft
        assert response.owner == "fallback@example.test"
        skill.commit_or_name_conflict.assert_awaited_once_with(db, "skill")

    @pytest.mark.asyncio
    async def test_save_draft_conflict_and_invalid_content_do_not_mutate_database(self, monkeypatch):
        db = _db()
        target = AsyncMock(return_value=_target())
        identity = AsyncMock(return_value=True)
        monkeypatch.setattr(skill, "resolve_publish_target", target)
        monkeypatch.setattr(skill, "identity_exists", identity)

        with pytest.raises(HTTPException) as conflict:
            await skill.save_skill_draft(_draft_request(), db, _user())
        _http_error(conflict, 409, "Skill 'alice/review-skill' already exists")
        db.add.assert_not_called()

        target.reset_mock()
        identity.reset_mock()
        with pytest.raises(HTTPException) as invalid:
            await skill.save_skill_draft(
                _draft_request(skill_md_content="---\nname: [broken\n---\n"),
                db,
                _user(),
            )
        _http_error(invalid, 422, "Malformed SKILL.md frontmatter")
        target.assert_not_awaited()
        identity.assert_not_awaited()
        db.add.assert_not_called()

    def test_visibility_edits_reject_changes_but_allow_echoed_values(self):
        listing = _listing(is_private=True, team_id=TEAM_ID)

        skill._reject_visibility_edits(listing, SkillUpdateRequest(team_id=TEAM_ID, visibility="team"))

        with pytest.raises(HTTPException) as team_change:
            skill._reject_visibility_edits(listing, SkillUpdateRequest(team_id=uuid.UUID(int=999)))
        _http_error(
            team_change,
            400,
            "team_id cannot be changed here. A listing stays in the teamspace it was created under.",
        )
        with pytest.raises(HTTPException) as visibility_change:
            skill._reject_visibility_edits(listing, SkillUpdateRequest(visibility="public"))
        _http_error(
            visibility_change,
            400,
            f"visibility cannot be changed here. Use PATCH /api/v1/registry/skill/{LISTING_ID}/visibility.",
        )

    @pytest.mark.asyncio
    async def test_update_draft_mutates_version_before_listing_and_releases_expired_lock(self, monkeypatch):
        db = _db()
        listing = _listing(status=ListingStatus.rejected, submitted_by=USER_ID)
        version = listing.latest_version
        version.is_editing = True
        version.editing_by = OTHER_USER_ID
        version.editing_since = datetime.now(UTC) - timedelta(hours=1)
        original_name = listing.name
        _mock_skill_lock(monkeypatch, listing)

        async def flush():
            assert listing.name == original_name
            assert version.version == "2.0.0"
            assert version.slash_command == "new-review"

        db.flush.side_effect = flush
        resolve = AsyncMock(return_value=listing)
        commit = AsyncMock()
        monkeypatch.setattr(skill, "resolve_listing", resolve)
        monkeypatch.setattr(skill, "commit_or_name_conflict", commit)
        content = '---\nname: review\ndescription: New\ncommand: "/new-review"\n---\nBody\n'
        request = SkillUpdateRequest(
            name="Renamed Skill",
            owner="new-owner",
            version="2.0.0",
            description="New description",
            skill_path="skills/new",
            git_url="https://github.com/acme/new",
            git_ref="v2",
            skill_md_content=content,
            delivery_mode="registry_direct",
            script_content="print('new')",
            script_filename="new.py",
            target_agents=["pi"],
            task_type="testing",
            supported_harnesses=["pi"],
        )

        response = await skill.update_skill_draft(str(LISTING_ID), request, db, _user())

        assert resolve.await_args == call(SkillListing, str(LISTING_ID), db, current_user=_user())
        assert (
            listing.name,
            listing.owner,
            version.version,
            version.description,
            version.skill_path,
            version.git_url,
            version.git_ref,
            version.skill_md_content,
            version.delivery_mode,
            version.script_content,
            version.script_filename,
            version.target_agents,
            version.task_type,
            version.supported_harnesses,
            version.slash_command,
        ) == (
            "Renamed Skill",
            "new-owner",
            "2.0.0",
            "New description",
            "skills/new",
            "https://github.com/acme/new",
            "v2",
            content,
            "registry_direct",
            "print('new')",
            "new.py",
            ["pi"],
            "testing",
            ["pi"],
            "new-review",
        )
        assert (version.is_editing, version.editing_by, version.editing_since) == (False, None, None)
        db.flush.assert_awaited_once()
        commit.assert_awaited_once_with(db, "skill")
        assert db.refresh.await_count == 2
        db.refresh.assert_any_await(listing, attribute_names=["submitted_by", "co_authors", "team_id", "is_private"])
        db.refresh.assert_any_await(listing)
        assert (response.name, response.slash_command, response.status) == (
            "Renamed Skill",
            "new-review",
            ListingStatus.rejected,
        )

    @pytest.mark.asyncio
    async def test_update_command_without_new_content_and_explicit_clear(self, monkeypatch):
        db = _db()
        listing = _listing(status=ListingStatus.draft, submitted_by=USER_ID, slash_command="old")
        listing.latest_version.skill_md_content = "# Plain skill"
        _mock_skill_lock(monkeypatch, listing)
        monkeypatch.setattr(skill, "resolve_listing", AsyncMock(return_value=listing))
        monkeypatch.setattr(skill, "commit_or_name_conflict", AsyncMock())

        await skill.update_skill_draft(
            str(LISTING_ID),
            SkillUpdateRequest(slash_command="/new"),
            db,
            _user(),
        )
        assert listing.latest_version.slash_command == "new"

        await skill.update_skill_draft(
            str(LISTING_ID),
            SkillUpdateRequest(slash_command=""),
            db,
            _user(),
        )
        assert listing.latest_version.slash_command is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("mode", "status", "detail"),
        [
            ("missing", ListingStatus.draft, "Listing not found"),
            ("nonowner", ListingStatus.draft, "Not the listing owner"),
            ("approved", ListingStatus.approved, "Only draft, rejected, or pending listings can be edited"),
            ("noversion", ListingStatus.draft, "Listing has no version to update"),
        ],
    )
    async def test_update_rejections_are_exact_and_do_not_flush(self, monkeypatch, mode, status, detail):
        db = _db()
        listing = None if mode == "missing" else _listing(status=status, submitted_by=USER_ID)
        if mode == "noversion":
            listing.latest_version = None
        monkeypatch.setattr(skill, "resolve_listing", AsyncMock(return_value=listing))
        permission = "view" if mode == "nonowner" else "owner"
        monkeypatch.setattr(skill, "get_effective_component_permission", Mock(return_value=permission))

        with pytest.raises(HTTPException) as exc:
            await skill.update_skill_draft(str(LISTING_ID), SkillUpdateRequest(), db, _user())

        expected_status = 404 if mode == "missing" else 403 if mode == "nonowner" else 400
        _http_error(exc, expected_status, detail)
        db.flush.assert_not_awaited()
        db.refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_active_foreign_lock_rejects_before_flush_or_commit(self, monkeypatch):
        db = _db()
        listing = _listing(status=ListingStatus.draft, submitted_by=USER_ID)
        version = listing.latest_version
        version.is_editing = True
        version.editing_by = OTHER_USER_ID
        version.editing_since = datetime.now(UTC)
        _mock_skill_lock(monkeypatch, listing)
        monkeypatch.setattr(skill, "resolve_listing", AsyncMock(return_value=listing))
        commit = AsyncMock()
        monkeypatch.setattr(skill, "commit_or_name_conflict", commit)

        with pytest.raises(HTTPException) as exc:
            await skill.update_skill_draft(str(LISTING_ID), SkillUpdateRequest(), db, _user())

        _http_error(
            exc,
            409,
            "This item is currently being edited by another user. Please try again later.",
        )
        db.flush.assert_not_awaited()
        commit.assert_not_awaited()
        assert (version.is_editing, version.editing_by) == (True, OTHER_USER_ID)


class TestEditingLocks:
    @pytest.mark.asyncio
    async def test_legacy_pending_versions_require_withdrawal_even_without_stored_revision(self, monkeypatch):
        db = _db()
        listing = _listing(status=ListingStatus.pending, submitted_by=USER_ID)
        _mock_skill_lock(monkeypatch, listing)
        monkeypatch.setattr(skill, "resolve_listing", AsyncMock(return_value=listing))
        commit = AsyncMock()
        monkeypatch.setattr(skill, "commit_or_name_conflict", commit)

        for action in (
            lambda: skill.start_edit_skill(str(LISTING_ID), db, _user()),
            lambda: skill.update_skill_draft(str(LISTING_ID), SkillUpdateRequest(description="new"), db, _user()),
        ):
            with pytest.raises(HTTPException) as blocked:
                await action()
            _http_error(blocked, 409, "Withdraw this pending version before editing its files")
        commit.assert_not_awaited()
        db.flush.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_start_edit_locks_selected_version_row_for_update(self, monkeypatch):
        db = _db()
        listing = _listing(status=ListingStatus.draft, submitted_by=USER_ID)
        locked = listing.latest_version
        db.execute.side_effect = [_result(listing.latest_version_id), _result(locked)]
        monkeypatch.setattr(skill, "resolve_listing", AsyncMock(return_value=listing))
        commit = AsyncMock()
        monkeypatch.setattr(skill, "commit_or_name_conflict", commit)

        response = await skill.start_edit_skill("alice/review-skill", db, _user())

        assert response == {"status": "locked"}
        assert "skill_listings" in _sql(db.execute.await_args_list[0].args[0])
        assert _sql(db.execute.await_args_list[0].args[0]).endswith("FOR UPDATE")
        stmt = db.execute.await_args.args[0]
        assert "WHERE skill_versions.id =" in _sql(stmt)
        assert _sql(stmt).endswith("FOR UPDATE")
        assert stmt.compile().params["id_1"] == VERSION_ID
        assert locked.is_editing is True
        assert locked.editing_by == USER_ID
        assert locked.editing_since is not None
        commit.assert_awaited_once_with(db, "skill")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("mode", "detail", "status"),
        [
            ("missing", "Listing not found", 404),
            ("nonowner", "Not the listing owner", 403),
            ("noversion", "Listing has no version", 400),
            ("approved", "Cannot edit: listing is 'approved'", 400),
        ],
    )
    async def test_start_edit_rejections_do_not_query_or_commit(self, monkeypatch, mode, detail, status):
        db = _db()
        listing = None if mode == "missing" else _listing(submitted_by=USER_ID)
        if mode == "noversion":
            listing.latest_version = None
        if mode == "approved":
            listing.latest_version.status = ListingStatus.approved
        monkeypatch.setattr(skill, "resolve_listing", AsyncMock(return_value=listing))
        monkeypatch.setattr(
            skill,
            "get_effective_component_permission",
            Mock(return_value="view" if mode == "nonowner" else "owner"),
        )
        commit = AsyncMock()
        monkeypatch.setattr(skill, "commit_or_name_conflict", commit)

        with pytest.raises(HTTPException) as exc:
            await skill.start_edit_skill(str(LISTING_ID), db, _user())

        _http_error(exc, status, detail)
        db.execute.assert_not_awaited()
        commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cancel_edit_releases_holder_and_commits(self, monkeypatch):
        db = _db()
        listing = _listing(status=ListingStatus.draft, submitted_by=USER_ID)
        version = listing.latest_version
        version.is_editing = True
        version.editing_by = USER_ID
        version.editing_since = NOW
        _mock_skill_lock(monkeypatch, listing)
        monkeypatch.setattr(skill, "resolve_listing", AsyncMock(return_value=listing))
        commit = AsyncMock()
        monkeypatch.setattr(skill, "commit_or_name_conflict", commit)

        response = await skill.cancel_edit_skill(str(LISTING_ID), db, _user())

        assert response == {"status": "unlocked"}
        assert (version.is_editing, version.editing_by, version.editing_since) == (False, None, None)
        commit.assert_awaited_once_with(db, "skill")

    @pytest.mark.asyncio
    async def test_cancel_edit_enforces_owner_version_and_lock_holder(self, monkeypatch):
        db = _db()
        commit = AsyncMock()
        monkeypatch.setattr(skill, "commit_or_name_conflict", commit)

        monkeypatch.setattr(skill, "resolve_listing", AsyncMock(return_value=None))
        with pytest.raises(HTTPException) as missing:
            await skill.cancel_edit_skill("missing", db, _user())
        _http_error(missing, 404, "Listing not found")

        listing = _listing(submitted_by=USER_ID)
        monkeypatch.setattr(skill, "resolve_listing", AsyncMock(return_value=listing))
        monkeypatch.setattr(skill, "get_effective_component_permission", Mock(return_value="view"))
        with pytest.raises(HTTPException) as forbidden:
            await skill.cancel_edit_skill(str(LISTING_ID), db, _user())
        _http_error(forbidden, 403, "Not the listing owner")

        monkeypatch.setattr(skill, "get_effective_component_permission", Mock(return_value="owner"))
        listing.latest_version = None
        with pytest.raises(HTTPException) as no_version:
            await skill.cancel_edit_skill(str(LISTING_ID), db, _user())
        _http_error(no_version, 400, "Listing has no version")

        listing = _listing(submitted_by=USER_ID)
        listing.latest_version.is_editing = True
        listing.latest_version.editing_by = OTHER_USER_ID
        listing.latest_version.editing_since = NOW
        _mock_skill_lock(monkeypatch, listing)
        monkeypatch.setattr(skill, "resolve_listing", AsyncMock(return_value=listing))
        with pytest.raises(HTTPException) as wrong_holder:
            await skill.cancel_edit_skill(str(LISTING_ID), db, _user())
        _http_error(wrong_holder, 403, "You do not hold the edit lock on this item")
        commit.assert_not_awaited()


class TestSubmitDraftAndLifecycle:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("auto_approved", [False, True])
    async def test_submit_draft_validates_content_notifies_and_sets_review_state(self, monkeypatch, auto_approved):
        db = _db()
        listing = _listing(
            status=ListingStatus.rejected,
            submitted_by=USER_ID,
            skill_md_content='---\nname: review\ndescription: Review\ncommand: "/submit-review"\n---\nBody\n',
            slash_command=None,
        )
        events = []
        _mock_skill_lock(monkeypatch, listing)
        monkeypatch.setattr(skill, "datetime", FrozenDateTime)
        monkeypatch.setattr(skill, "resolve_listing", AsyncMock(return_value=listing))
        decide = AsyncMock(return_value=auto_approved)
        publish = AsyncMock(side_effect=lambda *args, **kwargs: events.append("inbox"))
        commit = AsyncMock(side_effect=lambda *args: events.append("commit"))
        monkeypatch.setattr(skill, "publish_auto_approves_for_entity", decide)
        monkeypatch.setattr(skill.inbox, "on_publish", publish)
        monkeypatch.setattr(skill, "commit_or_name_conflict", commit)
        db.refresh.side_effect = lambda row, **kwargs: events.append("auth" if kwargs else "refresh")

        response = await skill.submit_skill_draft("alice/review-skill", db, _user())

        expected_status = ListingStatus.approved if auto_approved else ListingStatus.pending
        assert listing.status == expected_status
        assert listing.latest_version.slash_command == "submit-review"
        if auto_approved:
            assert listing.latest_version.reviewed_by == USER_ID
            assert listing.latest_version.reviewed_at == NOW
        else:
            assert listing.latest_version.reviewed_by is None
        assert response.status == expected_status
        assert events == ["auth", "inbox", "commit", "refresh"]
        decide.assert_awaited_once_with(listing, _user(), db)
        publish.assert_awaited_once_with(
            db,
            listing,
            subject_type="skill",
            actor_id=USER_ID,
            auto_approved=auto_approved,
            version="1.2.3",
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("mode", "status", "detail", "code"),
        [
            ("missing", ListingStatus.draft, "Listing not found", 404),
            ("nonowner", ListingStatus.draft, "Not the listing owner", 403),
            ("pending", ListingStatus.pending, "Listing is not a draft", 400),
            ("noversion", ListingStatus.draft, "Listing has no version", 400),
            ("nodescription", ListingStatus.draft, "Description is required before submitting", 400),
        ],
    )
    async def test_submit_draft_rejections_do_not_publish_or_commit(self, monkeypatch, mode, status, detail, code):
        db = _db()
        listing = None if mode == "missing" else _listing(status=status, submitted_by=USER_ID)
        if mode == "noversion":
            listing.latest_version = None
        if mode == "nodescription":
            listing.latest_version.description = ""
            _mock_skill_lock(monkeypatch, listing)
        monkeypatch.setattr(skill, "resolve_listing", AsyncMock(return_value=listing))
        monkeypatch.setattr(
            skill,
            "get_effective_component_permission",
            Mock(return_value="view" if mode == "nonowner" else "owner"),
        )
        publish = AsyncMock()
        commit = AsyncMock()
        monkeypatch.setattr(skill.inbox, "on_publish", publish)
        monkeypatch.setattr(skill, "commit_or_name_conflict", commit)

        with pytest.raises(HTTPException) as exc:
            await skill.submit_skill_draft(str(LISTING_ID), db, _user())

        _http_error(exc, code, detail)
        publish.assert_not_awaited()
        commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_submit_draft_invalid_stored_content_preserves_rejected_state(self, monkeypatch):
        db = _db()
        listing = _listing(
            status=ListingStatus.rejected,
            submitted_by=USER_ID,
            skill_md_content="---\nname: [broken\n---\n",
        )
        _mock_skill_lock(monkeypatch, listing)
        monkeypatch.setattr(skill, "resolve_listing", AsyncMock(return_value=listing))
        decide = AsyncMock()
        monkeypatch.setattr(skill, "publish_auto_approves_for_entity", decide)

        with pytest.raises(HTTPException) as exc:
            await skill.submit_skill_draft(str(LISTING_ID), db, _user())

        _http_error(exc, 422, "Malformed SKILL.md frontmatter")
        assert listing.status == ListingStatus.rejected
        decide.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_archive_and_unarchive_delegate_exact_boundaries(self, monkeypatch):
        db = _db()
        user = _user()
        archive = AsyncMock(return_value={"status": "archived"})
        unarchive = AsyncMock(return_value={"status": "approved"})
        monkeypatch.setattr(skill, "archive_listing", archive)
        monkeypatch.setattr(skill, "unarchive_listing", unarchive)

        assert await skill.archive_skill("alice/review-skill", db, user) == {"status": "archived"}
        assert await skill.unarchive_skill("alice/review-skill", db, user) == {"status": "approved"}
        archive.assert_awaited_once_with(SkillListing, "alice/review-skill", db, user, "skill")
        unarchive.assert_awaited_once_with(SkillListing, "alice/review-skill", db, user, "skill")


class TestRouteContracts:
    @pytest.mark.asyncio
    async def test_protected_submit_requires_bearer_authentication(self):
        app = FastAPI()
        app.include_router(skill.router)
        app.dependency_overrides[get_db] = _db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/skills/submit",
                json={
                    "name": "Review",
                    "version": "1.0.0",
                    "description": "Review",
                    "owner": "alice",
                    "task_type": "code-review",
                },
            )

        assert response.status_code == 401
        assert response.json() == {"detail": "Missing credentials"}

    @pytest.mark.asyncio
    async def test_skill_router_wires_version_publish_to_skill_models(self, monkeypatch):
        from api.routes import component_versions

        app = FastAPI()
        app.include_router(skill.router)
        db = _db()
        user = _user()
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: user
        publish = AsyncMock(return_value={"version": "2.0.0", "status": "pending"})
        monkeypatch.setattr(component_versions, "_publish_version", publish)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/skills/{LISTING_ID}/versions",
                json={
                    "version": "2.0.0",
                    "description": "Second release",
                    "supported_harnesses": ["pi"],
                    "extra": {"task_type": "code-review", "slash_command": "/review-v2"},
                },
            )

        assert response.status_code == 200
        assert response.json() == {"version": "2.0.0", "status": "pending"}
        kwargs = publish.await_args.kwargs
        assert kwargs == {
            "listing_id": str(LISTING_ID),
            "req": kwargs["req"],
            "listing_model": SkillListing,
            "version_model": SkillVersion,
            "component_type": "skill",
            "db": db,
            "current_user": user,
        }
        assert kwargs["req"].model_dump() == {
            "version": "2.0.0",
            "description": "Second release",
            "changelog": None,
            "supported_harnesses": ["pi"],
            "extra": {"task_type": "code-review", "slash_command": "/review-v2"},
        }
