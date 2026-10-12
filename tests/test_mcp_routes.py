# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
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
from api.routes import mcp
from models.mcp import ListingStatus, McpDownload, McpListing, McpValidationResult, McpVersion
from models.user import UserRole
from schemas.mcp import (
    ClientAnalysis,
    McpAnalyzeRequest,
    McpDraftRequest,
    McpInstallRequest,
    McpSubmitRequest,
    McpUpdateRequest,
)
from services.teamspace import PublishTarget

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
LISTING_ID = uuid.UUID(int=201)
VERSION_ID = uuid.UUID(int=202)
USER_ID = uuid.UUID(int=203)
OTHER_USER_ID = uuid.UUID(int=204)
TEAM_ID = uuid.UUID(int=205)


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


def _user(*, user_id=USER_ID, role=UserRole.user, username="alice", email="alice@example.test"):
    return SimpleNamespace(id=user_id, role=role, username=username, email=email)


def _db():
    return SimpleNamespace(
        add=Mock(),
        delete=AsyncMock(),
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
    name="Review MCP",
    namespace="alice",
    slug="review-mcp",
    description="Review changes safely",
    version="1.2.3",
    category="developer-tools",
    is_private=False,
    team_id=None,
    transport="stdio",
    framework="typescript",
    docker_image=None,
    command="npx",
    args=None,
    url=None,
    headers=None,
    auto_approve=None,
    environment_variables=None,
    supported_harnesses=None,
    setup_instructions=None,
    changelog="Stable release",
    source_url="https://github.com/acme/review-mcp",
    mcp_validated=True,
    download_count=7,
):
    listing = McpListing(
        id=LISTING_ID,
        name=name,
        namespace=namespace,
        slug=slug,
        category=category,
        owner=namespace,
        submitted_by=submitted_by,
        co_authors=[],
        is_private=is_private,
        team_id=team_id,
    )
    listing.created_at = NOW
    listing.updated_at = NOW
    latest = McpVersion(
        id=VERSION_ID,
        listing_id=listing.id,
        version=version,
        description=description,
        changelog=changelog,
        transport=transport,
        framework=framework,
        docker_image=docker_image,
        command=command,
        args=["-y", "@acme/review"] if args is None else args,
        url=url,
        headers=(
            [{"name": "Authorization", "description": "Bearer token", "required": True}] if headers is None else headers
        ),
        auto_approve=["review"] if auto_approve is None else auto_approve,
        mcp_validated=mcp_validated,
        environment_variables=(
            [{"name": "API_KEY", "description": "Access key", "required": True}]
            if environment_variables is None
            else environment_variables
        ),
        supported_harnesses=["cursor", "pi"] if supported_harnesses is None else supported_harnesses,
        setup_instructions=setup_instructions,
        source_url=source_url,
        status=status,
        rejection_reason=None,
        download_count=download_count,
        released_by=submitted_by,
        released_at=NOW,
        reviewed_by=None,
        reviewed_at=None,
        is_editing=False,
        editing_by=None,
        editing_since=None,
    )
    latest.created_at = NOW
    listing.latest_version = latest
    listing.latest_version_id = latest.id
    listing.validation_results = []
    return listing


def _submit_request(**overrides):
    values = {
        "name": "Review MCP",
        "version": "1.0.0",
        "description": "Review changes",
        "category": "developer-tools",
        "owner": "request-owner",
        "command": "npx",
        "args": ["-y", "@acme/review"],
        "headers": [{"name": "Authorization", "description": "Bearer token", "required": True}],
        "auto_approve": ["review"],
        "framework": "typescript",
        "supported_harnesses": ["cursor", "pi"],
        "environment_variables": [{"name": "API_KEY", "description": "Access key", "required": True}],
        "setup_instructions": "Install Node.js",
        "changelog": "Initial release",
    }
    values.update(overrides)
    return McpSubmitRequest(**values)


def _draft_request(**overrides):
    values = {
        "name": "Review MCP",
        "version": "0.1.0",
        "description": "Review changes",
        "category": "developer-tools",
        "owner": "",
        "command": "npx",
        "args": ["-y", "@acme/review"],
        "headers": [{"name": "Authorization", "description": "Bearer token", "required": True}],
        "auto_approve": ["review"],
        "framework": "typescript",
        "supported_harnesses": ["pi"],
        "environment_variables": [{"name": "API_KEY", "description": "Access key", "required": True}],
        "setup_instructions": "Install Node.js",
        "changelog": "Draft release",
    }
    values.update(overrides)
    return McpDraftRequest(**values)


def _target(*, auto_approve=False, team_id=None, visibility="public"):
    return PublishTarget(
        namespace="platform" if team_id else "alice",
        slug="review-mcp",
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
    current = db.add.call_args_list[-1].args[0]
    if isinstance(current, McpListing):
        current.id = LISTING_ID
        current.created_at = NOW
        current.updated_at = NOW
    elif isinstance(current, McpVersion):
        current.id = VERSION_ID
        current.created_at = NOW
        current.download_count = current.download_count or 0
        current.mcp_validated = bool(current.mcp_validated)


def _refresh_new_listing(db, listing):
    version = next(entry.args[0] for entry in db.add.call_args_list if isinstance(entry.args[0], McpVersion))
    listing.latest_version = version
    listing.created_at = NOW
    listing.updated_at = NOW
    listing.validation_results = []


class TestAnalysisAndValidation:
    @pytest.mark.asyncio
    async def test_analyze_delegates_to_validator_and_returns_typed_payload(self, monkeypatch):
        analyze = AsyncMock(
            return_value={
                "name": "review-mcp",
                "description": "Review changes",
                "version": "1.0.0",
                "tools": [{"name": "review"}],
                "environment_variables": [{"name": "TOKEN", "description": "Token", "required": True}],
                "issues": [],
                "error": "",
                "command": "npx",
                "args": ["-y", "review"],
                "framework": "typescript",
                "docker_image": None,
            }
        )
        monkeypatch.setattr(mcp, "analyze_repo", analyze)

        response = await mcp.analyze_mcp(McpAnalyzeRequest(git_url="https://github.com/acme/review"), _user())

        assert response.model_dump() == {
            "name": "review-mcp",
            "description": "Review changes",
            "version": "1.0.0",
            "tools": [{"name": "review"}],
            "environment_variables": [{"name": "TOKEN", "description": "Token", "required": True}],
            "issues": [],
            "error": "",
            "command": "npx",
            "args": ["-y", "review"],
            "framework": "typescript",
            "docker_image": None,
        }
        analyze.assert_awaited_once_with("https://github.com/acme/review")

    @pytest.mark.asyncio
    async def test_analyze_service_failure_is_not_hidden(self, monkeypatch):
        monkeypatch.setattr(mcp, "analyze_repo", AsyncMock(side_effect=RuntimeError("validator unavailable")))

        with pytest.raises(RuntimeError, match="validator unavailable"):
            await mcp.analyze_mcp(McpAnalyzeRequest(git_url="https://github.com/acme/review"), _user())

    @pytest.mark.asyncio
    async def test_client_analysis_replaces_results_and_applies_discovered_config(self, monkeypatch):
        db = _db()
        listing = _listing(
            command=None,
            args=[],
            docker_image=None,
            framework=None,
            mcp_validated=False,
        )
        commit = AsyncMock()
        monkeypatch.setattr(mcp, "commit_or_name_conflict", commit)
        analysis = ClientAnalysis(
            tools=[{"name": "review"}, {"name": "summarize"}],
            issues=["Document permissions"],
            framework="typescript",
            entry_point="src/server.ts",
            command="npx",
            args=["-y", "@acme/review"],
            docker_image="ghcr.io/acme/review:1",
        )

        await mcp._store_client_analysis(listing, analysis, db)

        delete_stmt = db.execute.await_args.args[0]
        assert _sql(delete_stmt).startswith("DELETE FROM mcp_validation_results WHERE")
        assert delete_stmt.compile().params["listing_id_1"] == LISTING_ID
        assert (
            listing.framework,
            listing.command,
            listing.args,
            listing.docker_image,
            listing.mcp_validated,
        ) == (
            "typescript",
            "npx",
            ["-y", "@acme/review"],
            "ghcr.io/acme/review:1",
            True,
        )
        added = [entry.args[0] for entry in db.add.call_args_list]
        assert [(row.stage, row.passed, row.details) for row in added] == [
            ("clone_and_inspect", True, "Client-side analysis: found entry point (typescript)"),
            (
                "manifest_validation",
                False,
                "Client-side analysis: 2 tool(s) found\nIssues:\n- Document permissions",
            ),
        ]
        assert all(isinstance(row, McpValidationResult) and row.listing_id == LISTING_ID for row in added)
        commit.assert_awaited_once_with(db, "listing")

    @pytest.mark.asyncio
    async def test_client_analysis_without_entry_is_recorded_and_preserves_explicit_config(self, monkeypatch):
        db = _db()
        listing = _listing(command="custom", args=["serve"], docker_image="existing:image", mcp_validated=False)
        monkeypatch.setattr(mcp, "commit_or_name_conflict", AsyncMock())

        await mcp._store_client_analysis(
            listing,
            ClientAnalysis(
                tools=[{"name": "review"}],
                command="ignored",
                args=["ignored"],
                docker_image="ignored:image",
            ),
            db,
        )

        assert (listing.command, listing.args, listing.docker_image, listing.mcp_validated) == (
            "custom",
            ["serve"],
            "existing:image",
            True,
        )
        added = [entry.args[0] for entry in db.add.call_args_list]
        assert [(row.stage, row.passed, row.details) for row in added] == [
            (
                "clone_and_inspect",
                False,
                "Client-side analysis: no recognized MCP framework detected",
            ),
            ("manifest_validation", True, "Client-side analysis: 1 tool(s) found"),
        ]

    @pytest.mark.asyncio
    async def test_client_analysis_commit_failure_propagates(self, monkeypatch):
        db = _db()
        commit = AsyncMock(side_effect=RuntimeError("commit failed"))
        monkeypatch.setattr(mcp, "commit_or_name_conflict", commit)

        with pytest.raises(RuntimeError, match="commit failed"):
            await mcp._store_client_analysis(_listing(), ClientAnalysis(entry_point="server.py"), db)

        assert db.add.call_count == 1

    @pytest.mark.asyncio
    async def test_background_validation_uses_private_session(self, monkeypatch):
        db = _db()
        listing = _listing()
        db.execute.return_value = _result(listing)
        validate = AsyncMock()

        class SessionContext:
            async def __aenter__(self):
                return db

            async def __aexit__(self, exc_type, exc, tb):
                return False

        session_factory = Mock(return_value=SessionContext())
        monkeypatch.setattr(mcp, "async_session", session_factory)
        monkeypatch.setattr(mcp, "run_validation", validate)

        await mcp._run_validation_background(str(LISTING_ID))

        session_factory.assert_called_once_with()
        stmt = db.execute.await_args.args[0]
        assert "FROM mcp_listings" in _sql(stmt)
        assert stmt.compile().params["id_1"] == str(LISTING_ID)
        validate.assert_awaited_once_with(listing, db)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("found", [False, True])
    async def test_background_validation_handles_missing_listing_and_validator_failure(self, monkeypatch, found):
        db = _db()
        listing = _listing() if found else None
        db.execute.return_value = _result(listing)
        validate = AsyncMock(side_effect=RuntimeError("clone failed"))

        class SessionContext:
            async def __aenter__(self):
                return db

            async def __aexit__(self, exc_type, exc, tb):
                return False

        monkeypatch.setattr(mcp, "async_session", Mock(return_value=SessionContext()))
        monkeypatch.setattr(mcp, "run_validation", validate)

        await mcp._run_validation_background(str(LISTING_ID))

        if found:
            validate.assert_awaited_once_with(listing, db)
        else:
            validate.assert_not_awaited()


class TestSubmitMcp:
    @pytest.mark.asyncio
    async def test_stdio_team_submit_persists_complete_version_in_transaction_order(self, monkeypatch):
        db = _db()
        db.execute.return_value = _result(None)
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
        target = _target(auto_approve=True, team_id=TEAM_ID, visibility="team")
        resolve_target = AsyncMock(return_value=target)
        publish = AsyncMock(side_effect=lambda *args, **kwargs: events.append("inbox"))
        commit = AsyncMock(side_effect=lambda *args: events.append("commit"))
        monkeypatch.setattr(mcp, "datetime", FrozenDateTime)
        monkeypatch.setattr(mcp, "resolve_publish_target", resolve_target)
        monkeypatch.setattr(mcp.inbox, "on_publish", publish)
        monkeypatch.setattr(mcp, "commit_or_name_conflict", commit)
        background = Mock()
        request = _submit_request(team_id=TEAM_ID, visibility="team")

        response = await mcp.submit_mcp(request, background, db, _user())

        listing, version = [entry.args[0] for entry in db.add.call_args_list]
        assert isinstance(listing, McpListing)
        assert isinstance(version, McpVersion)
        assert (
            listing.id,
            listing.name,
            listing.namespace,
            listing.slug,
            listing.category,
            listing.owner,
            listing.submitted_by,
            listing.team_id,
            listing.is_private,
        ) == (
            LISTING_ID,
            "Review MCP",
            "platform",
            "review-mcp",
            "developer-tools",
            "platform",
            USER_ID,
            TEAM_ID,
            True,
        )
        assert (
            version.listing_id,
            version.version,
            version.description,
            version.transport,
            version.framework,
            version.command,
            version.args,
            version.headers,
            version.auto_approve,
            version.environment_variables,
            version.supported_harnesses,
            version.setup_instructions,
            version.changelog,
            version.source_url,
            version.status,
            version.released_by,
            version.released_at,
            version.reviewed_by,
            version.reviewed_at,
        ) == (
            LISTING_ID,
            "1.0.0",
            "Review changes",
            "stdio",
            "typescript",
            "npx",
            ["-y", "@acme/review"],
            [{"name": "Authorization", "description": "Bearer token", "required": True}],
            ["review"],
            [{"name": "API_KEY", "description": "Access key", "required": True}],
            ["cursor", "pi"],
            "Install Node.js",
            "Initial release",
            None,
            ListingStatus.approved,
            USER_ID,
            NOW,
            USER_ID,
            NOW,
        )
        assert listing.latest_version_id == VERSION_ID
        assert response.model_dump() == {
            "id": LISTING_ID,
            "name": "Review MCP",
            "namespace": "platform",
            "slug": "review-mcp",
            "qualified_name": "platform/review-mcp",
            "version": "1.0.0",
            "git_url": None,
            "description": "Review changes",
            "category": "developer-tools",
            "owner": "platform",
            "team_id": TEAM_ID,
            "visibility": "team",
            "is_private": True,
            "supported_harnesses": ["cursor", "pi"],
            "environment_variables": [{"name": "API_KEY", "description": "Access key", "required": True}],
            "setup_instructions": "Install Node.js",
            "changelog": "Initial release",
            "framework": "typescript",
            "docker_image": None,
            "command": "npx",
            "args": ["-y", "@acme/review"],
            "url": None,
            "headers": [{"name": "Authorization", "description": "Bearer token", "required": True}],
            "auto_approve": ["review"],
            "mcp_validated": False,
            "status": ListingStatus.approved,
            "rejection_reason": None,
            "submitted_by": USER_ID,
            "created_at": NOW,
            "updated_at": NOW,
            "custom_fields": [],
            "validation_results": [],
            "download_count": 0,
            "user_permission": None,
            "is_recommended": False,
            "forked_from": None,
            "fork_count": 0,
            "warnings": [],
        }
        assert events == [
            "add:McpListing",
            "flush",
            "add:McpVersion",
            "flush",
            "inbox",
            "commit",
            "refresh",
        ]
        resolve_target.assert_awaited_once_with(
            db,
            _user(),
            "Review MCP",
            team_id=TEAM_ID,
            visibility="team",
        )
        existing_stmt = db.execute.await_args.args[0]
        assert "mcp_listings.namespace =" in _sql(existing_stmt)
        assert "mcp_listings.slug =" in _sql(existing_stmt)
        assert {"platform", "review-mcp"} <= set(existing_stmt.compile().params.values())
        publish.assert_awaited_once_with(
            db,
            listing,
            subject_type="mcp",
            actor_id=USER_ID,
            auto_approved=True,
            version="1.0.0",
        )
        commit.assert_awaited_once_with(db, "listing")
        background.add_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_remote_submit_infers_sse_and_stays_pending(self, monkeypatch):
        db = _db()
        db.execute.return_value = _result(None)
        db.flush.side_effect = lambda: _prepare_new_rows(db)
        db.refresh.side_effect = lambda row: _refresh_new_listing(db, row)
        monkeypatch.setattr(mcp, "resolve_publish_target", AsyncMock(return_value=_target()))
        monkeypatch.setattr(mcp.inbox, "on_publish", AsyncMock())
        monkeypatch.setattr(mcp, "commit_or_name_conflict", AsyncMock())
        background = Mock()

        response = await mcp.submit_mcp(
            _submit_request(command=None, args=None, url="https://mcp.example.test/events", transport=None),
            background,
            db,
            _user(),
        )

        version = next(entry.args[0] for entry in db.add.call_args_list if isinstance(entry.args[0], McpVersion))
        assert (version.transport, version.url, version.command, version.status) == (
            "sse",
            "https://mcp.example.test/events",
            None,
            ListingStatus.pending,
        )
        assert response.status == ListingStatus.pending
        background.add_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_client_analysis_runs_after_listing_refresh_instead_of_background_task(self, monkeypatch):
        db = _db()
        db.execute.return_value = _result(None)
        db.flush.side_effect = lambda: _prepare_new_rows(db)
        db.refresh.side_effect = lambda row: _refresh_new_listing(db, row)
        monkeypatch.setattr(mcp, "resolve_publish_target", AsyncMock(return_value=_target()))
        monkeypatch.setattr(mcp.inbox, "on_publish", AsyncMock())
        monkeypatch.setattr(mcp, "commit_or_name_conflict", AsyncMock())
        store = AsyncMock()
        monkeypatch.setattr(mcp, "_store_client_analysis", store)
        background = Mock()
        analysis = ClientAnalysis(entry_point="server.py", framework="python")

        await mcp.submit_mcp(_submit_request(client_analysis=analysis), background, db, _user())

        listing = next(entry.args[0] for entry in db.add.call_args_list if isinstance(entry.args[0], McpListing))
        store.assert_awaited_once_with(listing, analysis, db)
        db.refresh.assert_awaited_once_with(listing)
        background.add_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_git_submit_schedules_validation_after_commit_and_refresh(self, monkeypatch):
        db = _db()
        db.execute.return_value = _result(None)
        db.flush.side_effect = lambda: _prepare_new_rows(db)
        db.refresh.side_effect = lambda row: _refresh_new_listing(db, row)
        monkeypatch.setattr(mcp, "resolve_publish_target", AsyncMock(return_value=_target()))
        monkeypatch.setattr(mcp.inbox, "on_publish", AsyncMock())
        monkeypatch.setattr(mcp, "commit_or_name_conflict", AsyncMock())
        background = Mock()

        await mcp.submit_mcp(
            _submit_request(git_url="https://github.com/acme/review", command=None, args=None),
            background,
            db,
            _user(),
        )

        background.add_task.assert_called_once_with(mcp._run_validation_background, str(LISTING_ID))

    @pytest.mark.asyncio
    async def test_owner_can_replace_own_nonapproved_identity_before_recreation(self, monkeypatch):
        db = _db()
        existing = _listing(status=ListingStatus.rejected, submitted_by=USER_ID)
        db.execute.return_value = _result(existing)
        events = []
        db.delete.side_effect = lambda row: events.append("delete")
        db.add.side_effect = lambda row: events.append(f"add:{type(row).__name__}")

        async def flush():
            if db.add.call_args_list:
                _prepare_new_rows(db)
            events.append("flush")

        db.flush.side_effect = flush
        db.refresh.side_effect = lambda row: _refresh_new_listing(db, row)
        monkeypatch.setattr(mcp, "resolve_publish_target", AsyncMock(return_value=_target()))
        monkeypatch.setattr(mcp.inbox, "on_publish", AsyncMock())
        monkeypatch.setattr(mcp, "commit_or_name_conflict", AsyncMock())

        await mcp.submit_mcp(_submit_request(), Mock(), db, _user())

        db.delete.assert_awaited_once_with(existing)
        assert events[:3] == ["delete", "flush", "add:McpListing"]
        assert db.flush.await_count == 3

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("status", "submitted_by"),
        [
            (ListingStatus.approved, USER_ID),
            (ListingStatus.pending, OTHER_USER_ID),
        ],
    )
    async def test_existing_approved_or_foreign_identity_is_exact_conflict_without_mutation(
        self, monkeypatch, status, submitted_by
    ):
        db = _db()
        existing = _listing(status=status, submitted_by=submitted_by)
        db.execute.return_value = _result(existing)
        monkeypatch.setattr(mcp, "resolve_publish_target", AsyncMock(return_value=_target()))

        with pytest.raises(HTTPException) as exc:
            await mcp.submit_mcp(_submit_request(), Mock(), db, _user())

        _http_error(exc, 409, "Approved MCP server 'alice/review-mcp' already exists")
        db.delete.assert_not_awaited()
        db.add.assert_not_called()
        db.flush.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_publish_target_failure_precedes_database_mutation(self, monkeypatch):
        db = _db()
        monkeypatch.setattr(
            mcp,
            "resolve_publish_target",
            AsyncMock(side_effect=HTTPException(status_code=403, detail="Not a team member")),
        )

        with pytest.raises(HTTPException) as exc:
            await mcp.submit_mcp(_submit_request(), Mock(), db, _user())

        _http_error(exc, 403, "Not a team member")
        db.execute.assert_not_awaited()
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_inbox_failure_prevents_commit_refresh_and_validation(self, monkeypatch):
        db = _db()
        db.execute.return_value = _result(None)
        db.flush.side_effect = lambda: _prepare_new_rows(db)
        monkeypatch.setattr(mcp, "resolve_publish_target", AsyncMock(return_value=_target()))
        monkeypatch.setattr(mcp.inbox, "on_publish", AsyncMock(side_effect=RuntimeError("inbox unavailable")))
        commit = AsyncMock()
        monkeypatch.setattr(mcp, "commit_or_name_conflict", commit)
        background = Mock()

        with pytest.raises(RuntimeError, match="inbox unavailable"):
            await mcp.submit_mcp(
                _submit_request(git_url="https://github.com/acme/review"),
                background,
                db,
                _user(),
            )

        assert db.add.call_count == 2
        commit.assert_not_awaited()
        db.refresh.assert_not_awaited()
        background.add_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_commit_failure_prevents_refresh_and_client_analysis(self, monkeypatch):
        db = _db()
        db.execute.return_value = _result(None)
        db.flush.side_effect = lambda: _prepare_new_rows(db)
        monkeypatch.setattr(mcp, "resolve_publish_target", AsyncMock(return_value=_target()))
        monkeypatch.setattr(mcp.inbox, "on_publish", AsyncMock())
        monkeypatch.setattr(mcp, "commit_or_name_conflict", AsyncMock(side_effect=RuntimeError("commit failed")))
        store = AsyncMock()
        monkeypatch.setattr(mcp, "_store_client_analysis", store)

        with pytest.raises(RuntimeError, match="commit failed"):
            await mcp.submit_mcp(
                _submit_request(client_analysis=ClientAnalysis(entry_point="server.py")),
                Mock(),
                db,
                _user(),
            )

        db.refresh.assert_not_awaited()
        store.assert_not_awaited()


class TestListAndDetail:
    @pytest.fixture(autouse=True)
    def _stub_fork_enrichment(self, monkeypatch):
        # These tests assert the existing list/detail query shapes. Fork metadata
        # and the additional grouped count query are covered by the HTTP fork tests.
        from api.routes.component_forks import RESPONSE_MODELS, SUMMARY_MODELS

        async def serialize(items, kind, _user, _db, *, summary=True):
            schema = SUMMARY_MODELS[kind] if summary else RESPONSE_MODELS[kind]
            return [schema.model_validate(item) for item in items]

        monkeypatch.setattr(mcp, "component_responses", serialize)

    @pytest.mark.asyncio
    async def test_list_builds_filters_count_rank_scope_and_pagination(self, monkeypatch):
        db = _db()
        listing = _listing()
        db.scalar.return_value = 9
        db.execute.return_value = _result(rows=[listing])
        scope = Mock(side_effect=lambda stmt, model, user, **kwargs: stmt.where(model.team_id == TEAM_ID))
        search = Mock(return_value=(McpListing.name == "needle", literal(4)))
        monkeypatch.setattr(mcp, "apply_registry_scope", scope)
        monkeypatch.setattr(mcp, "keyword_search", search)
        response = Response()
        user = _user(role=UserRole.admin)

        rows = await mcp.list_mcps(
            response=response,
            category="developer-tools",
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

        assert [row.qualified_name for row in rows] == ["alice/review-mcp"]
        assert response.headers["X-Total-Count"] == "9"
        search.assert_called_once()
        assert search.call_args.args[0] == "needle"
        assert search.call_args.kwargs["name_field"] is McpListing.name
        searched_fields = search.call_args.args[1]
        assert searched_fields == [
            McpListing.name,
            McpListing.slug,
            McpListing.namespace,
            McpListing.owner,
            McpListing.category,
            McpVersion.description,
            McpVersion.framework,
            McpVersion.setup_instructions,
        ]
        scope.assert_called_once_with(
            scope.call_args.args[0],
            McpListing,
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
            "JOIN mcp_versions",
            "mcp_versions.status =",
            "mcp_listings.category =",
            "mcp_listings.namespace =",
            "mcp_listings.name =",
            "mcp_listings.team_id =",
        ):
            assert fragment in count_sql
            assert fragment in data_sql
        assert "SELECT count(*) AS count_1 FROM (SELECT" in count_sql
        assert " DESC, mcp_listings.created_at DESC" in data_sql
        assert " LIMIT " in data_sql
        assert " OFFSET " in data_sql
        assert {"developer-tools", "alice", "needle", TEAM_ID, 25, 50} <= set(data_stmt.compile().params.values())

    @pytest.mark.asyncio
    async def test_list_without_search_uses_created_order_and_zero_total(self, monkeypatch):
        db = _db()
        db.scalar.return_value = None
        db.execute.return_value = _result(rows=[])
        monkeypatch.setattr(mcp, "apply_registry_scope", Mock(side_effect=lambda stmt, *args, **kwargs: stmt))
        search = Mock()
        monkeypatch.setattr(mcp, "keyword_search", search)
        response = Response()

        rows = await mcp.list_mcps(
            response=response,
            category=None,
            namespace=None,
            search=None,
            team_id=None,
            composable_for_team_id=None,
            public_only=True,
            limit=50,
            offset=0,
            db=db,
            current_user=None,
        )

        assert rows == []
        assert response.headers["X-Total-Count"] == "0"
        search.assert_not_called()
        assert "ORDER BY mcp_listings.created_at DESC" in _sql(db.execute.await_args.args[0])

        search.return_value = (None, None)
        await mcp.list_mcps(
            response=Response(),
            category=None,
            namespace=None,
            search="the",
            team_id=None,
            composable_for_team_id=None,
            public_only=True,
            limit=50,
            offset=0,
            db=db,
            current_user=None,
        )
        search.assert_called_once()

    @pytest.mark.asyncio
    async def test_my_list_applies_authorship_visibility_and_order(self, monkeypatch):
        db = _db()
        listing = _listing(submitted_by=USER_ID)
        db.execute.return_value = _result(rows=[listing])
        visibility = Mock(side_effect=lambda stmt, model, user: stmt.where(model.is_private.is_(False)))
        monkeypatch.setattr(mcp, "apply_visibility_filter", visibility)

        rows = await mcp.my_mcps(db, _user())

        assert [row.qualified_name for row in rows] == ["alice/review-mcp"]
        visibility.assert_called_once()
        stmt = db.execute.await_args.args[0]
        sql = _sql(stmt)
        assert "mcp_listings.submitted_by =" in sql
        assert "mcp_listings.is_private IS false" in sql
        assert "ORDER BY mcp_listings.created_at DESC" in sql
        assert USER_ID in stmt.compile().params.values()

    @pytest.mark.asyncio
    async def test_approved_detail_resolves_canonical_identity_once(self, monkeypatch):
        db = _db()
        listing = _listing()
        resolve = AsyncMock(return_value=listing)
        monkeypatch.setattr(mcp, "resolve_visible_listing", resolve)

        response = await mcp.get_mcp("Alice/Review-MCP", db, _user())

        assert response.qualified_name == "alice/review-mcp"
        assert response.user_permission == "view"
        resolve.assert_awaited_once_with(
            McpListing,
            "Alice/Review-MCP",
            db,
            _user(),
            require_status=ListingStatus.approved,
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("user", "status", "expected_permission"),
        [
            (_user(), ListingStatus.pending, "owner"),
            (_user(role=UserRole.reviewer), ListingStatus.rejected, "view"),
        ],
    )
    async def test_owner_and_reviewer_can_read_unapproved_detail(self, monkeypatch, user, status, expected_permission):
        db = _db()
        listing = _listing(status=status, submitted_by=USER_ID if expected_permission == "owner" else OTHER_USER_ID)
        resolve = AsyncMock(side_effect=[None, listing])
        monkeypatch.setattr(mcp, "resolve_visible_listing", resolve)

        response = await mcp.get_mcp("alice/review-mcp", db, user)

        assert response.status == status
        assert response.user_permission == expected_permission
        assert resolve.await_args_list == [
            call(McpListing, "alice/review-mcp", db, user, require_status=ListingStatus.approved),
            call(McpListing, "alice/review-mcp", db, user),
        ]

    @pytest.mark.asyncio
    async def test_hidden_unapproved_and_missing_detail_share_exact_404(self, monkeypatch):
        db = _db()
        listing = _listing(status=ListingStatus.pending)
        resolve = AsyncMock(side_effect=[None, listing])
        monkeypatch.setattr(mcp, "resolve_visible_listing", resolve)

        with pytest.raises(HTTPException) as hidden:
            await mcp.get_mcp("alice/review-mcp", db, _user())
        _http_error(hidden, 404, "Listing not found")

        resolve.side_effect = [None, None]
        with pytest.raises(HTTPException) as missing:
            await mcp.get_mcp("missing", db, None)
        _http_error(missing, 404, "Listing not found")


class TestInstallMcp:
    @pytest.mark.asyncio
    async def test_archived_remote_install_tracks_usage_then_generates_exact_config(self, monkeypatch):
        db = _db()
        listing = _listing(
            status=ListingStatus.archived,
            command=None,
            args=[],
            url="https://mcp.example.test/events",
            transport="sse",
            setup_instructions="Create a local token",
        )
        resolve = AsyncMock(side_effect=[None, listing])
        events = []
        commit = AsyncMock(side_effect=lambda *args: events.append("commit"))
        derive = AsyncMock(side_effect=lambda request: events.append("derive") or {"api": "https://api.test"})
        generate = Mock(side_effect=lambda *args, **kwargs: events.append("generate") or {"mcpServers": {"local": {}}})
        monkeypatch.setattr(mcp, "resolve_visible_listing", resolve)
        monkeypatch.setattr(mcp, "commit_or_name_conflict", commit)
        monkeypatch.setattr("api.routes.config.derive_endpoints", derive)
        monkeypatch.setattr(mcp, "generate_config", generate)
        request = MagicMock(name="request")
        install = McpInstallRequest(
            harness="cursor",
            local_name="local",
            env_values={"API_KEY": "secret"},
            header_values={"Authorization": "Bearer token"},
        )

        response = await mcp.install_mcp("alice/review-mcp", install, request, db, _user())

        assert response.model_dump() == {
            "listing_id": LISTING_ID,
            "harness": "cursor",
            "config_snippet": {"mcpServers": {"local": {}}},
            "warnings": [
                "Archived MCP 'Review MCP' is deprecated and may be removed from future agent pulls.",
                "MCP 'Review MCP' requires local setup before use:\nCreate a local token",
            ],
            "version": "1.2.3",
            "version_id": listing.latest_version.id,
            "digest": response.digest,
        }
        assert response.digest.startswith("sha256:")
        assert resolve.await_args_list == [
            call(McpListing, "alice/review-mcp", db, _user(), require_status=ListingStatus.approved),
            call(McpListing, "alice/review-mcp", db, _user()),
        ]
        download = db.add.call_args.args[0]
        assert isinstance(download, McpDownload)
        assert (download.listing_id, download.user_id, download.harness) == (LISTING_ID, USER_ID, "cursor")
        assert listing.latest_version.download_count == 8
        assert events == ["commit", "derive", "generate"]
        commit.assert_awaited_once_with(db, "listing")
        derive.assert_awaited_once_with(request)
        generate.assert_called_once_with(
            listing,
            "cursor",
            observal_url="https://api.test",
            env_values={"API_KEY": "secret"},
            header_values={"Authorization": "Bearer token"},
            local_name="local",
        )

    @pytest.mark.asyncio
    async def test_pending_owner_fallback_can_install(self, monkeypatch):
        db = _db()
        listing = _listing(status=ListingStatus.pending, submitted_by=USER_ID)
        monkeypatch.setattr(mcp, "resolve_visible_listing", AsyncMock(side_effect=[None, listing]))
        monkeypatch.setattr(mcp, "commit_or_name_conflict", AsyncMock())
        monkeypatch.setattr("api.routes.config.derive_endpoints", AsyncMock(return_value={"api": "https://api.test"}))
        monkeypatch.setattr(mcp, "generate_config", Mock(return_value={"mcpServers": {"review-mcp": {}}}))

        response = await mcp.install_mcp(
            "alice/review-mcp",
            McpInstallRequest(harness="pi"),
            MagicMock(),
            db,
            _user(),
        )

        assert response.config_snippet == {"mcpServers": {"review-mcp": {}}}
        assert listing.latest_version.download_count == 8
        assert isinstance(db.add.call_args.args[0], McpDownload)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("found", [None, "nonowner"])
    async def test_missing_or_unapproved_nonowner_is_404_without_usage(self, monkeypatch, found):
        db = _db()
        fallback = None if found is None else _listing(status=ListingStatus.pending)
        monkeypatch.setattr(mcp, "resolve_visible_listing", AsyncMock(side_effect=[None, fallback]))
        commit = AsyncMock()
        monkeypatch.setattr(mcp, "commit_or_name_conflict", commit)

        with pytest.raises(HTTPException) as exc:
            await mcp.install_mcp(
                "alice/review-mcp",
                McpInstallRequest(harness="pi"),
                MagicMock(),
                db,
                _user(),
            )

        _http_error(exc, 404, "Listing not found or not approved")
        db.add.assert_not_called()
        commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_install_without_loaded_latest_version_still_records_download(self, monkeypatch):
        db = _db()
        listing = _listing()
        listing.latest_version = None
        monkeypatch.setattr(mcp, "resolve_visible_listing", AsyncMock(return_value=listing))
        monkeypatch.setattr(mcp, "commit_or_name_conflict", AsyncMock())
        monkeypatch.setattr("api.routes.config.derive_endpoints", AsyncMock(return_value={"api": "https://api.test"}))
        monkeypatch.setattr(mcp, "generate_config", Mock(return_value={}))

        response = await mcp.install_mcp(
            str(LISTING_ID),
            McpInstallRequest(harness="pi"),
            MagicMock(),
            db,
            _user(),
        )

        assert response.config_snippet == {}
        assert isinstance(db.add.call_args.args[0], McpDownload)

    @pytest.mark.asyncio
    async def test_config_failure_occurs_after_usage_commit(self, monkeypatch):
        db = _db()
        listing = _listing()
        monkeypatch.setattr(mcp, "resolve_visible_listing", AsyncMock(return_value=listing))
        commit = AsyncMock()
        monkeypatch.setattr(mcp, "commit_or_name_conflict", commit)
        monkeypatch.setattr("api.routes.config.derive_endpoints", AsyncMock(return_value={"api": "https://api.test"}))
        generate = Mock(side_effect=ValueError("unsupported harness"))
        monkeypatch.setattr(mcp, "generate_config", generate)

        with pytest.raises(ValueError, match="unsupported harness"):
            await mcp.install_mcp(
                str(LISTING_ID),
                McpInstallRequest(harness="unknown"),
                MagicMock(),
                db,
                _user(),
            )

        assert isinstance(db.add.call_args.args[0], McpDownload)
        assert listing.latest_version.download_count == 8
        commit.assert_awaited_once_with(db, "listing")
        generate.assert_called_once()

    @pytest.mark.asyncio
    async def test_commit_failure_prevents_endpoint_derivation_and_config_generation(self, monkeypatch):
        db = _db()
        listing = _listing()
        monkeypatch.setattr(mcp, "resolve_visible_listing", AsyncMock(return_value=listing))
        monkeypatch.setattr(mcp, "commit_or_name_conflict", AsyncMock(side_effect=RuntimeError("commit failed")))
        derive = AsyncMock()
        generate = Mock()
        monkeypatch.setattr("api.routes.config.derive_endpoints", derive)
        monkeypatch.setattr(mcp, "generate_config", generate)

        with pytest.raises(RuntimeError, match="commit failed"):
            await mcp.install_mcp(
                str(LISTING_ID),
                McpInstallRequest(harness="pi"),
                MagicMock(),
                db,
                _user(),
            )

        derive.assert_not_awaited()
        generate.assert_not_called()


class TestDraftCreationAndUpdates:
    @pytest.mark.asyncio
    async def test_save_remote_draft_uses_owner_fallback_and_complete_payload(self, monkeypatch):
        db = _db()
        db.flush.side_effect = lambda: _prepare_new_rows(db)
        db.refresh.side_effect = lambda row: _refresh_new_listing(db, row)
        monkeypatch.setattr(mcp, "datetime", FrozenDateTime)
        monkeypatch.setattr(mcp, "resolve_publish_target", AsyncMock(return_value=_target()))
        identity = AsyncMock(return_value=False)
        monkeypatch.setattr(mcp, "identity_exists", identity)
        monkeypatch.setattr(mcp, "commit_or_name_conflict", AsyncMock())
        request = _draft_request(
            owner="",
            command=None,
            args=None,
            url="https://mcp.example.test/events",
            transport=None,
            git_url="https://github.com/acme/review",
        )
        user = _user(username="", email="fallback@example.test")

        response = await mcp.save_mcp_draft(request, db, user)

        listing, version = [entry.args[0] for entry in db.add.call_args_list]
        assert listing.owner == "fallback@example.test"
        assert (listing.namespace, listing.slug, listing.submitted_by, listing.is_private) == (
            "alice",
            "review-mcp",
            USER_ID,
            False,
        )
        assert (
            version.status,
            version.transport,
            version.url,
            version.source_url,
            version.headers,
            version.environment_variables,
            version.released_at,
        ) == (
            ListingStatus.draft,
            "sse",
            "https://mcp.example.test/events",
            "https://github.com/acme/review",
            [{"name": "Authorization", "description": "Bearer token", "required": True}],
            [{"name": "API_KEY", "description": "Access key", "required": True}],
            NOW,
        )
        assert response.status == ListingStatus.draft
        assert response.owner == "fallback@example.test"
        identity.assert_awaited_once_with(db, McpListing, "alice", "review-mcp")
        mcp.commit_or_name_conflict.assert_awaited_once_with(db, "listing")

    @pytest.mark.asyncio
    async def test_save_draft_conflict_is_exact_and_adds_no_rows(self, monkeypatch):
        db = _db()
        monkeypatch.setattr(mcp, "resolve_publish_target", AsyncMock(return_value=_target()))
        monkeypatch.setattr(mcp, "identity_exists", AsyncMock(return_value=True))

        with pytest.raises(HTTPException) as exc:
            await mcp.save_mcp_draft(_draft_request(), db, _user())

        _http_error(exc, 409, "MCP server 'alice/review-mcp' already exists")
        db.add.assert_not_called()
        db.flush.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_save_draft_commit_failure_prevents_refresh(self, monkeypatch):
        db = _db()
        db.flush.side_effect = lambda: _prepare_new_rows(db)
        monkeypatch.setattr(mcp, "resolve_publish_target", AsyncMock(return_value=_target()))
        monkeypatch.setattr(mcp, "identity_exists", AsyncMock(return_value=False))
        monkeypatch.setattr(mcp, "commit_or_name_conflict", AsyncMock(side_effect=RuntimeError("commit failed")))

        with pytest.raises(RuntimeError, match="commit failed"):
            await mcp.save_mcp_draft(_draft_request(), db, _user())

        db.refresh.assert_not_awaited()

    def test_visibility_edits_reject_changes_but_allow_echoed_values(self):
        listing = _listing(is_private=True, team_id=TEAM_ID)

        mcp._reject_visibility_edits(listing, McpUpdateRequest(team_id=TEAM_ID, visibility="team"))

        with pytest.raises(HTTPException) as team_change:
            mcp._reject_visibility_edits(listing, McpUpdateRequest(team_id=uuid.UUID(int=999)))
        _http_error(
            team_change,
            400,
            "team_id cannot be changed here. A listing stays in the teamspace it was created under.",
        )
        with pytest.raises(HTTPException) as visibility_change:
            mcp._reject_visibility_edits(listing, McpUpdateRequest(visibility="public"))
        _http_error(
            visibility_change,
            400,
            f"visibility cannot be changed here. Use PATCH /api/v1/registry/mcp/{LISTING_ID}/visibility.",
        )

    @pytest.mark.asyncio
    async def test_update_mutates_version_before_listing_and_releases_expired_lock(self, monkeypatch):
        db = _db()
        listing = _listing(status=ListingStatus.rejected, submitted_by=USER_ID)
        version = listing.latest_version
        version.is_editing = True
        version.editing_by = OTHER_USER_ID
        version.editing_since = datetime.now(UTC) - timedelta(hours=1)
        original_name = listing.name

        async def flush():
            assert listing.name == original_name
            assert version.version == "2.0.0"
            assert version.source_url == "https://github.com/acme/new"

        db.flush.side_effect = flush
        resolve = AsyncMock(return_value=listing)
        commit = AsyncMock()
        monkeypatch.setattr(mcp, "resolve_listing", resolve)
        monkeypatch.setattr(mcp, "commit_or_name_conflict", commit)
        request = McpUpdateRequest(
            name="Renamed MCP",
            category="search",
            owner="new-owner",
            version="2.0.0",
            description="New description",
            framework="python",
            docker_image="ghcr.io/acme/new:2",
            command="python",
            args=["-m", "review"],
            url="https://mcp.example.test/new",
            auto_approve=["new-review"],
            transport="streamable-http",
            supported_harnesses=["pi"],
            setup_instructions="Run setup",
            changelog="Second release",
            git_url="https://github.com/acme/new",
            headers=[{"name": "X-Token", "description": "Token", "required": False}],
            environment_variables=[{"name": "TOKEN", "description": "Token", "required": False}],
        )

        response = await mcp.update_mcp_draft(str(LISTING_ID), request, db, _user())

        assert resolve.await_args == call(McpListing, str(LISTING_ID), db, current_user=_user())
        assert (
            listing.name,
            listing.category,
            listing.owner,
            version.version,
            version.description,
            version.framework,
            version.docker_image,
            version.command,
            version.args,
            version.url,
            version.auto_approve,
            version.transport,
            version.supported_harnesses,
            version.setup_instructions,
            version.changelog,
            version.source_url,
            version.headers,
            version.environment_variables,
        ) == (
            "Renamed MCP",
            "search",
            "new-owner",
            "2.0.0",
            "New description",
            "python",
            "ghcr.io/acme/new:2",
            "python",
            ["-m", "review"],
            "https://mcp.example.test/new",
            ["new-review"],
            "streamable-http",
            ["pi"],
            "Run setup",
            "Second release",
            "https://github.com/acme/new",
            [{"name": "X-Token", "description": "Token", "required": False}],
            [{"name": "TOKEN", "description": "Token", "required": False}],
        )
        assert (version.is_editing, version.editing_by, version.editing_since) == (False, None, None)
        db.flush.assert_awaited_once()
        commit.assert_awaited_once_with(db, "listing")
        db.refresh.assert_awaited_once_with(listing)
        assert (response.name, response.version, response.status) == (
            "Renamed MCP",
            "2.0.0",
            ListingStatus.rejected,
        )

        db.flush.reset_mock()
        db.flush.side_effect = None
        db.refresh.reset_mock()
        commit.reset_mock()
        response = await mcp.update_mcp_draft(
            str(LISTING_ID),
            McpUpdateRequest(description="Third description"),
            db,
            _user(),
        )
        assert response.description == "Third description"
        assert (listing.name, listing.category, listing.owner) == ("Renamed MCP", "search", "new-owner")

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
    async def test_update_rejections_are_exact_without_flush(self, monkeypatch, mode, status, detail):
        db = _db()
        listing = None if mode == "missing" else _listing(status=status, submitted_by=USER_ID)
        if mode == "noversion":
            listing.latest_version = None
        monkeypatch.setattr(mcp, "resolve_listing", AsyncMock(return_value=listing))
        monkeypatch.setattr(
            mcp,
            "get_effective_component_permission",
            Mock(return_value="view" if mode == "nonowner" else "owner"),
        )

        with pytest.raises(HTTPException) as exc:
            await mcp.update_mcp_draft(str(LISTING_ID), McpUpdateRequest(), db, _user())

        expected_status = 404 if mode == "missing" else 403 if mode == "nonowner" else 400
        _http_error(exc, expected_status, detail)
        db.flush.assert_not_awaited()
        db.refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_visibility_rejection_precedes_version_mutation(self, monkeypatch):
        db = _db()
        listing = _listing(status=ListingStatus.draft, submitted_by=USER_ID)
        original_description = listing.description
        monkeypatch.setattr(mcp, "resolve_listing", AsyncMock(return_value=listing))

        with pytest.raises(HTTPException) as exc:
            await mcp.update_mcp_draft(
                str(LISTING_ID),
                McpUpdateRequest(visibility="team", description="must not persist"),
                db,
                _user(),
            )

        assert exc.value.status_code == 400
        assert listing.description == original_description
        db.flush.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_active_foreign_lock_rejects_without_flush_or_commit(self, monkeypatch):
        db = _db()
        listing = _listing(status=ListingStatus.pending, submitted_by=USER_ID)
        version = listing.latest_version
        version.is_editing = True
        version.editing_by = OTHER_USER_ID
        version.editing_since = datetime.now(UTC)
        monkeypatch.setattr(mcp, "resolve_listing", AsyncMock(return_value=listing))
        commit = AsyncMock()
        monkeypatch.setattr(mcp, "commit_or_name_conflict", commit)

        with pytest.raises(HTTPException) as exc:
            await mcp.update_mcp_draft(str(LISTING_ID), McpUpdateRequest(), db, _user())

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
    async def test_start_edit_locks_selected_version_row_for_update(self, monkeypatch):
        db = _db()
        listing = _listing(status=ListingStatus.pending, submitted_by=USER_ID)
        locked = listing.latest_version
        db.execute.return_value = _result(locked)
        monkeypatch.setattr(mcp, "resolve_listing", AsyncMock(return_value=listing))
        commit = AsyncMock()
        monkeypatch.setattr(mcp, "commit_or_name_conflict", commit)

        response = await mcp.start_edit_mcp("alice/review-mcp", db, _user())

        assert response == {"status": "locked"}
        stmt = db.execute.await_args.args[0]
        assert "WHERE mcp_versions.id =" in _sql(stmt)
        assert _sql(stmt).endswith("FOR UPDATE")
        assert stmt.compile().params["id_1"] == VERSION_ID
        assert locked.is_editing is True
        assert locked.editing_by == USER_ID
        assert locked.editing_since is not None
        commit.assert_awaited_once_with(db, "listing")

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
        listing = None if mode == "missing" else _listing(status=ListingStatus.pending, submitted_by=USER_ID)
        if mode == "noversion":
            listing.latest_version = None
        if mode == "approved":
            listing.latest_version.status = ListingStatus.approved
        monkeypatch.setattr(mcp, "resolve_listing", AsyncMock(return_value=listing))
        monkeypatch.setattr(
            mcp,
            "get_effective_component_permission",
            Mock(return_value="view" if mode == "nonowner" else "owner"),
        )
        commit = AsyncMock()
        monkeypatch.setattr(mcp, "commit_or_name_conflict", commit)

        with pytest.raises(HTTPException) as exc:
            await mcp.start_edit_mcp(str(LISTING_ID), db, _user())

        _http_error(exc, status, detail)
        db.execute.assert_not_awaited()
        commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_edit_lock_service_failure_prevents_commit(self, monkeypatch):
        db = _db()
        listing = _listing(status=ListingStatus.draft, submitted_by=USER_ID)
        db.execute.return_value = _result(listing.latest_version)
        monkeypatch.setattr(mcp, "resolve_listing", AsyncMock(return_value=listing))
        monkeypatch.setattr(mcp, "acquire_edit_lock", Mock(side_effect=HTTPException(status_code=409, detail="locked")))
        commit = AsyncMock()
        monkeypatch.setattr(mcp, "commit_or_name_conflict", commit)

        with pytest.raises(HTTPException) as exc:
            await mcp.start_edit_mcp(str(LISTING_ID), db, _user())

        _http_error(exc, 409, "locked")
        commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cancel_edit_releases_holder_and_commits(self, monkeypatch):
        db = _db()
        listing = _listing(status=ListingStatus.draft, submitted_by=USER_ID)
        version = listing.latest_version
        version.is_editing = True
        version.editing_by = USER_ID
        version.editing_since = NOW
        monkeypatch.setattr(mcp, "resolve_listing", AsyncMock(return_value=listing))
        commit = AsyncMock()
        monkeypatch.setattr(mcp, "commit_or_name_conflict", commit)

        response = await mcp.cancel_edit_mcp(str(LISTING_ID), db, _user())

        assert response == {"status": "unlocked"}
        assert (version.is_editing, version.editing_by, version.editing_since) == (False, None, None)
        commit.assert_awaited_once_with(db, "listing")

    @pytest.mark.asyncio
    async def test_cancel_edit_enforces_owner_version_and_lock_holder(self, monkeypatch):
        db = _db()
        commit = AsyncMock()
        monkeypatch.setattr(mcp, "commit_or_name_conflict", commit)

        monkeypatch.setattr(mcp, "resolve_listing", AsyncMock(return_value=None))
        with pytest.raises(HTTPException) as missing:
            await mcp.cancel_edit_mcp("missing", db, _user())
        _http_error(missing, 404, "Listing not found")

        listing = _listing(submitted_by=USER_ID)
        monkeypatch.setattr(mcp, "resolve_listing", AsyncMock(return_value=listing))
        monkeypatch.setattr(mcp, "get_effective_component_permission", Mock(return_value="view"))
        with pytest.raises(HTTPException) as forbidden:
            await mcp.cancel_edit_mcp(str(LISTING_ID), db, _user())
        _http_error(forbidden, 403, "Not the listing owner")

        monkeypatch.setattr(mcp, "get_effective_component_permission", Mock(return_value="owner"))
        listing.latest_version = None
        with pytest.raises(HTTPException) as no_version:
            await mcp.cancel_edit_mcp(str(LISTING_ID), db, _user())
        _http_error(no_version, 400, "Listing has no version")

        listing = _listing(submitted_by=USER_ID)
        listing.latest_version.is_editing = True
        listing.latest_version.editing_by = OTHER_USER_ID
        listing.latest_version.editing_since = NOW
        monkeypatch.setattr(mcp, "resolve_listing", AsyncMock(return_value=listing))
        with pytest.raises(HTTPException) as wrong_holder:
            await mcp.cancel_edit_mcp(str(LISTING_ID), db, _user())
        _http_error(wrong_holder, 403, "You do not hold the edit lock on this item")
        commit.assert_not_awaited()


class TestSubmitDraftAndLifecycle:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("auto_approved", [False, True])
    async def test_submit_draft_notifies_and_sets_review_state_in_order(self, monkeypatch, auto_approved):
        db = _db()
        listing = _listing(status=ListingStatus.rejected, submitted_by=USER_ID)
        events = []
        monkeypatch.setattr(mcp, "datetime", FrozenDateTime)
        monkeypatch.setattr(mcp, "resolve_listing", AsyncMock(return_value=listing))
        decide = AsyncMock(return_value=auto_approved)
        publish = AsyncMock(side_effect=lambda *args, **kwargs: events.append("inbox"))
        commit = AsyncMock(side_effect=lambda *args: events.append("commit"))
        monkeypatch.setattr(mcp, "publish_auto_approves_for_entity", decide)
        monkeypatch.setattr(mcp.inbox, "on_publish", publish)
        monkeypatch.setattr(mcp, "commit_or_name_conflict", commit)
        db.refresh.side_effect = lambda row: events.append("refresh")

        response = await mcp.submit_mcp_draft("alice/review-mcp", db, _user())

        expected_status = ListingStatus.approved if auto_approved else ListingStatus.pending
        assert listing.status == expected_status
        if auto_approved:
            assert listing.latest_version.reviewed_by == USER_ID
            assert listing.latest_version.reviewed_at == NOW
        else:
            assert listing.latest_version.reviewed_by is None
        assert response.status == expected_status
        assert events == ["inbox", "commit", "refresh"]
        decide.assert_awaited_once_with(listing, _user(), db)
        publish.assert_awaited_once_with(
            db,
            listing,
            subject_type="mcp",
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
            ("nodescription", ListingStatus.draft, "Description is required before submitting", 400),
            ("nosource", ListingStatus.draft, "At least one of git_url, command, or url is required", 400),
        ],
    )
    async def test_submit_draft_rejections_do_not_publish_or_commit(self, monkeypatch, mode, status, detail, code):
        db = _db()
        listing = None if mode == "missing" else _listing(status=status, submitted_by=USER_ID)
        if mode == "nodescription":
            listing.latest_version.description = ""
        if mode == "nosource":
            listing.latest_version.source_url = None
            listing.latest_version.command = None
            listing.latest_version.url = None
        monkeypatch.setattr(mcp, "resolve_listing", AsyncMock(return_value=listing))
        monkeypatch.setattr(
            mcp,
            "get_effective_component_permission",
            Mock(return_value="view" if mode == "nonowner" else "owner"),
        )
        decide = AsyncMock()
        publish = AsyncMock()
        commit = AsyncMock()
        monkeypatch.setattr(mcp, "publish_auto_approves_for_entity", decide)
        monkeypatch.setattr(mcp.inbox, "on_publish", publish)
        monkeypatch.setattr(mcp, "commit_or_name_conflict", commit)

        with pytest.raises(HTTPException) as exc:
            await mcp.submit_mcp_draft(str(LISTING_ID), db, _user())

        _http_error(exc, code, detail)
        decide.assert_not_awaited()
        publish.assert_not_awaited()
        commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_publish_policy_failure_preserves_rejected_state(self, monkeypatch):
        db = _db()
        listing = _listing(status=ListingStatus.rejected, submitted_by=USER_ID)
        monkeypatch.setattr(mcp, "resolve_listing", AsyncMock(return_value=listing))
        monkeypatch.setattr(
            mcp,
            "publish_auto_approves_for_entity",
            AsyncMock(side_effect=RuntimeError("policy unavailable")),
        )

        with pytest.raises(RuntimeError, match="policy unavailable"):
            await mcp.submit_mcp_draft(str(LISTING_ID), db, _user())

        assert listing.status == ListingStatus.rejected

    @pytest.mark.asyncio
    async def test_archive_and_unarchive_delegate_exact_boundaries(self, monkeypatch):
        db = _db()
        user = _user()
        archive = AsyncMock(return_value={"status": "archived"})
        unarchive = AsyncMock(return_value={"status": "approved"})
        monkeypatch.setattr(mcp, "archive_listing", archive)
        monkeypatch.setattr(mcp, "unarchive_listing", unarchive)

        assert await mcp.archive_mcp("alice/review-mcp", db, user) == {"status": "archived"}
        assert await mcp.unarchive_mcp("alice/review-mcp", db, user) == {"status": "approved"}
        archive.assert_awaited_once_with(McpListing, "alice/review-mcp", db, user, "listing")
        unarchive.assert_awaited_once_with(McpListing, "alice/review-mcp", db, user, "listing")


class TestConfigGeneration:
    def test_stdio_config_preserves_command_args_and_environment(self):
        listing = _listing(
            args=["-y", "@acme/review", "$REGION"],
            environment_variables=[
                {"name": "API_KEY", "description": "Key", "required": True},
                {"name": "REGION", "description": "Region", "required": True},
            ],
        )

        config = mcp.generate_config(
            listing,
            "cursor",
            env_values={"API_KEY": "secret", "REGION": "eu-west"},
        )

        assert config == {
            "mcpServers": {
                "review-mcp": {
                    "command": "npx",
                    "args": ["-y", "@acme/review", "eu-west"],
                    "env": {"API_KEY": "secret", "REGION": "eu-west"},
                    "autoApprove": ["review"],
                    "disabled": False,
                }
            }
        }

    def test_remote_config_preserves_transport_headers_and_environment(self):
        listing = _listing(
            command=None,
            args=[],
            url="https://mcp.example.test/events",
            transport="streamable-http",
            environment_variables=[{"name": "REGION", "description": "Region", "required": True}],
        )

        config = mcp.generate_config(
            listing,
            "opencode",
            env_values={"REGION": "eu-west"},
            header_values={"Authorization": "Bearer token"},
            local_name="Local Review",
        )

        assert config == {
            "mcp": {
                "Local-Review": {
                    "type": "remote",
                    "url": "https://mcp.example.test/events",
                    "headers": {"Authorization": "Bearer token"},
                    "env": {"REGION": "eu-west"},
                }
            }
        }


class TestRouteContracts:
    @pytest.mark.asyncio
    async def test_protected_submit_requires_bearer_authentication(self):
        app = FastAPI()
        app.include_router(mcp.router)
        app.dependency_overrides[get_db] = _db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/mcps/submit",
                json={
                    "name": "Review MCP",
                    "version": "1.0.0",
                    "description": "Review changes",
                    "category": "developer-tools",
                    "owner": "alice",
                    "command": "npx",
                },
            )

        assert response.status_code == 401
        assert response.json() == {"detail": "Missing credentials"}

    @pytest.mark.asyncio
    async def test_router_wires_version_publish_to_mcp_models(self, monkeypatch):
        from api.routes import component_versions

        app = FastAPI()
        app.include_router(mcp.router)
        db = _db()
        user = _user()
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: user
        publish = AsyncMock(return_value={"version": "2.0.0", "status": "pending"})
        monkeypatch.setattr(component_versions, "_publish_version", publish)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/mcps/{LISTING_ID}/versions",
                json={
                    "version": "2.0.0",
                    "description": "Second release",
                    "supported_harnesses": ["pi"],
                    "extra": {
                        "transport": "stdio",
                        "command": "python",
                        "args": ["-m", "review"],
                    },
                },
            )

        assert response.status_code == 200
        assert response.json() == {"version": "2.0.0", "status": "pending"}
        kwargs = publish.await_args.kwargs
        assert kwargs == {
            "listing_id": str(LISTING_ID),
            "req": kwargs["req"],
            "listing_model": McpListing,
            "version_model": McpVersion,
            "component_type": "mcp",
            "db": db,
            "current_user": user,
        }
        assert kwargs["req"].model_dump() == {
            "version": "2.0.0",
            "description": "Second release",
            "changelog": None,
            "supported_harnesses": ["pi"],
            "extra": {
                "transport": "stdio",
                "command": "python",
                "args": ["-m", "review"],
            },
        }
