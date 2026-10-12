# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Focused contracts and failure coverage for generic component versions."""

from __future__ import annotations

import importlib
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from api.deps import get_current_user, get_db, get_registry_user
from api.routes import component_versions as versions
from models.hook import HookListing, HookVersion
from models.mcp import ListingStatus, McpListing, McpVersion
from models.prompt import PromptListing, PromptVersion
from models.sandbox import SandboxListing, SandboxVersion
from models.skill import SkillListing, SkillVersion
from models.user import UserRole
from schemas.component_version import VersionPublishRequest, VersionReviewRequest
from services.skill_revisions import skill_content_revision

NOW = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)
LISTING_ID = uuid.UUID(int=1001)
OLD_VERSION_ID = uuid.UUID(int=1002)
NEW_VERSION_ID = uuid.UUID(int=1003)
OWNER_ID = uuid.UUID(int=1004)
COAUTHOR_ID = uuid.UUID(int=1005)
OTHER_ID = uuid.UUID(int=1006)
REVIEWER_ID = uuid.UUID(int=1007)

COMPONENTS = {
    "mcp": (McpListing, McpVersion, "mcps"),
    "skill": (SkillListing, SkillVersion, "skills"),
    "hook": (HookListing, HookVersion, "hooks"),
    "prompt": (PromptListing, PromptVersion, "prompts"),
    "sandbox": (SandboxListing, SandboxVersion, "sandboxes"),
}

LISTING_FIELDS = {
    "mcp": {"category": "developer-tools"},
    "skill": {},
    "hook": {},
    "prompt": {},
    "sandbox": {},
}

VERSION_FIELDS = {
    "mcp": {
        "source_url": "https://github.com/acme/review-mcp",
        "source_ref": "v1",
        "resolved_sha": "a" * 40,
        "transport": "stdio",
        "framework": "python",
        "docker_image": "ghcr.io/acme/review:1",
        "command": "python",
        "args": ["-m", "review"],
        "url": None,
        "headers": [{"name": "Authorization", "required": True}],
        "auto_approve": ["review"],
        "environment_variables": [{"name": "TOKEN", "required": True}],
        "setup_instructions": "Install Python",
    },
    "skill": {
        "skill_path": "skills/review",
        "git_url": "https://github.com/acme/skills",
        "git_ref": "v1",
        "skill_md_content": "# Review\n",
        "delivery_mode": "registry_direct",
        "script_content": "print('review')",
        "script_filename": "review.py",
        "validated": True,
        "target_agents": ["pi"],
        "task_type": "code-review",
        "slash_command": "review",
    },
    "hook": {
        "event": "PreToolUse",
        "execution_mode": "blocking",
        "priority": 10,
        "handler_type": "command",
        "handler_config": {"command": "python guard.py"},
        "scope": "agent",
        "tool_filter": {"tools": ["Bash"]},
        "source_url": "https://github.com/acme/hooks",
        "source_ref": "v1",
        "source_path": "hooks/guard.py",
        "resolved_sha": "b" * 40,
        "script_content": "print('guard')",
        "script_filename": "guard.py",
        "requirements": ["policy>=1"],
    },
    "prompt": {
        "category": "general",
        "template": "Review {{ change }}",
        "variables": [{"name": "change"}],
        "model_hints": {"temperature": 0},
        "tags": ["review"],
    },
    "sandbox": {
        "source_url": "https://github.com/acme/sandboxes",
        "source_ref": "v1",
        "resolved_sha": "c" * 40,
        "runtime_type": "docker",
        "image": "python:3.12-slim",
        "resource_limits": {"memory": "1Gi"},
        "network_policy": "none",
        "entrypoint": "pytest",
        "runtime_config": {"workdir": "/workspace"},
        "sandbox_path": "sandboxes/python",
    },
}

PUBLISH_EXTRAS = {
    "mcp": {"command": "python-new"},
    "skill": {"task_type": "code-review", "slash_command": "/review-v2"},
    "hook": {"event": "PostToolUse", "handler_type": "command"},
    "prompt": {"category": "general", "template": "Review version two"},
    "sandbox": None,
}

UNIQUE_FIELDS = {
    "mcp": ("command", "python-new"),
    "skill": ("task_type", "code-review"),
    "hook": ("event", "PostToolUse"),
    "prompt": ("template", "Review version two"),
    "sandbox": ("image", "python:3.12-slim"),
}

_UNSET = object()


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is not None else NOW.replace(tzinfo=None)


def _user(*, user_id=OTHER_ID, role=UserRole.user):
    return SimpleNamespace(
        id=user_id,
        role=role,
        email=f"{user_id.int}@example.test",
        username=f"user-{user_id.int}",
    )


def _listing(
    component_type: str = "mcp",
    *,
    owner_id=OWNER_ID,
    status=ListingStatus.approved,
    version="1.2.3",
    co_authors=None,
):
    listing_model, version_model, _plural = COMPONENTS[component_type]
    listing = listing_model(
        id=LISTING_ID,
        name=f"Review {component_type}",
        namespace="alice",
        slug=f"review-{component_type}",
        owner="alice",
        submitted_by=owner_id,
        co_authors=list(co_authors or []),
        is_private=False,
        team_id=None,
        created_at=NOW,
        updated_at=NOW,
        **LISTING_FIELDS[component_type],
    )
    current = version_model(
        id=OLD_VERSION_ID,
        listing_id=listing.id,
        version=version,
        description="Current release",
        changelog="Current changelog",
        status=status,
        rejection_reason=None,
        download_count=7,
        supported_harnesses=["pi"],
        released_by=owner_id,
        released_at=NOW,
        reviewed_by=REVIEWER_ID if status == ListingStatus.approved else None,
        reviewed_at=NOW if status == ListingStatus.approved else None,
        created_at=NOW,
        is_editing=False,
        editing_by=None,
        editing_since=None,
        **VERSION_FIELDS[component_type],
    )
    listing.latest_version = current
    listing.latest_version_id = current.id
    listing.versions = [current]
    return listing


def _pending_version(component_type="mcp", *, version="2.0.0", released_by=OWNER_ID):
    _listing_model, version_model, _plural = COMPONENTS[component_type]
    row = version_model(
        id=NEW_VERSION_ID,
        listing_id=LISTING_ID,
        version=version,
        description="Second release",
        changelog="Changed behavior",
        status=ListingStatus.pending,
        rejection_reason="old reason",
        download_count=0,
        supported_harnesses=["pi"],
        released_by=released_by,
        released_at=NOW,
        reviewed_by=None,
        reviewed_at=None,
        created_at=NOW,
        is_editing=False,
        editing_by=None,
        editing_since=None,
        **VERSION_FIELDS[component_type],
    )
    return row


def _request(component_type="mcp", *, version="2.0.0", supported_harnesses=None, extra=_UNSET):
    if extra is _UNSET:
        extra = PUBLISH_EXTRAS[component_type]
    return VersionPublishRequest(
        version=version,
        description="Second release",
        changelog="Changed behavior",
        supported_harnesses=["pi"] if supported_harnesses is None else supported_harnesses,
        extra=extra,
    )


def _db():
    return SimpleNamespace(
        add=Mock(),
        execute=AsyncMock(),
        refresh=AsyncMock(),
        flush=AsyncMock(),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )


def _result(*, scalar=_UNSET, scalar_value=_UNSET, scalars=(), rows=()):
    scalar_rows = list(scalars)
    result = MagicMock()
    if scalar is _UNSET:
        scalar = scalar_rows[0] if scalar_rows else None
    if scalar_value is _UNSET:
        scalar_value = scalar
    result.scalar_one_or_none.return_value = scalar
    result.scalar.return_value = scalar_value
    result.scalars.return_value.all.return_value = scalar_rows
    result.all.return_value = list(rows)
    return result


def _sql(statement) -> str:
    return " ".join(str(statement).split())


def _bound_values(statement) -> set:
    values = set()
    for value in statement.compile().params.values():
        if isinstance(value, (list, tuple, set)):
            values.update(value)
        else:
            values.add(value)
    return values


def _assert_http(exc, status, detail):
    assert exc.value.status_code == status
    assert exc.value.detail == detail


def _set_generated_defaults(db, component_type):
    _listing_model, version_model, _plural = COMPONENTS[component_type]
    row = next(entry.args[0] for entry in db.add.call_args_list if isinstance(entry.args[0], version_model))
    row.id = NEW_VERSION_ID
    row.created_at = NOW
    row.download_count = 0
    row.rejection_reason = None
    return row


@pytest.mark.parametrize(
    "value",
    ["0.0.0", "1.2.3", "10.20.300", "1.2.3-beta.1", "1.2.3-rc.2"],
)
def test_semver_pattern_accepts_the_documented_forms(value):
    assert versions.SEMVER_RE.fullmatch(value)


@pytest.mark.parametrize(
    "value",
    ["", "1", "1.2", "1.2.3.4", "v1.2.3", "1.2.3-", "1.2.3+build", "1.2.3-beta_1"],
)
def test_semver_pattern_rejects_malformed_forms(value):
    assert versions.SEMVER_RE.fullmatch(value) is None


def test_semver_parser_compares_numeric_core_and_ignores_prerelease_label():
    assert versions._parse_semver("10.2.30") == (10, 2, 30)
    assert versions._parse_semver("2.0.0-beta.1") == (2, 0, 0)


@pytest.mark.asyncio
async def test_legacy_audit_hook_has_no_side_effects():
    assert await versions.audit("publish", object(), resource_id=LISTING_ID) is None


def test_version_serialization_is_exact_for_mcp_fields():
    row = _listing("mcp").latest_version

    assert versions._version_to_dict(row, "mcp") == {
        "id": str(OLD_VERSION_ID),
        "listing_id": str(LISTING_ID),
        "version": "1.2.3",
        "description": "Current release",
        "changelog": "Current changelog",
        "status": "approved",
        "rejection_reason": None,
        "download_count": 7,
        "supported_harnesses": ["pi"],
        "released_by": str(OWNER_ID),
        "released_at": NOW,
        "created_at": NOW,
        "source_url": "https://github.com/acme/review-mcp",
        "source_ref": "v1",
        "resolved_sha": "a" * 40,
        "transport": "stdio",
        "framework": "python",
        "docker_image": "ghcr.io/acme/review:1",
        "command": "python",
        "args": ["-m", "review"],
        "url": None,
        "headers": [{"name": "Authorization", "required": True}],
        "auto_approve": ["review"],
        "environment_variables": [{"name": "TOKEN", "required": True}],
        "setup_instructions": "Install Python",
    }


@pytest.mark.parametrize("component_type", COMPONENTS)
def test_version_serialization_uses_each_types_real_version_model(component_type):
    row = _listing(component_type).latest_version
    field, expected = UNIQUE_FIELDS[component_type]
    if component_type == "mcp":
        expected = "python"
    if component_type == "hook":
        expected = "PreToolUse"
    if component_type == "prompt":
        expected = "Review {{ change }}"

    payload = versions._version_to_dict(row, component_type)

    assert payload[field] == expected
    assert payload["status"] == "approved"


def test_version_serialization_supports_legacy_string_status_and_missing_extras():
    row = SimpleNamespace(
        id=OLD_VERSION_ID,
        listing_id=LISTING_ID,
        version="0.9.0",
        description="Legacy",
        changelog=None,
        status="legacy",
        rejection_reason=None,
        download_count=1,
        supported_harnesses=[],
        released_by=OWNER_ID,
        released_at=NOW,
        created_at=NOW,
        task_type="review",
    )

    payload = versions._version_to_dict(row, "skill")

    assert payload == {
        "id": str(OLD_VERSION_ID),
        "listing_id": str(LISTING_ID),
        "version": "0.9.0",
        "description": "Legacy",
        "changelog": None,
        "status": "legacy",
        "rejection_reason": None,
        "download_count": 1,
        "supported_harnesses": [],
        "released_by": str(OWNER_ID),
        "released_at": NOW,
        "created_at": NOW,
        "task_type": "review",
    }


@pytest.mark.asyncio
async def test_list_versions_builds_exact_visibility_pagination_and_count_queries(monkeypatch):
    listing = _listing("mcp")
    row = listing.latest_version
    db = _db()
    db.execute.side_effect = [_result(scalars=[row]), _result(scalar_value=1)]
    actor = _user()
    resolve = AsyncMock(return_value=listing)
    monkeypatch.setattr(versions, "resolve_visible_listing", resolve)

    result = await versions._list_versions(
        "Alice/Review-MCP",
        3,
        5,
        McpListing,
        McpVersion,
        "mcp",
        db,
        actor,
    )

    assert result == {
        "items": [versions._version_to_dict(row, "mcp")],
        "total": 1,
        "page": 3,
        "page_size": 5,
    }
    resolve.assert_awaited_once_with(McpListing, "Alice/Review-MCP", db, actor)
    data_stmt, count_stmt = [entry.args[0] for entry in db.execute.await_args_list]
    data_sql = _sql(data_stmt)
    count_sql = _sql(count_stmt)
    assert "mcp_versions.listing_id =" in data_sql
    assert "mcp_versions.status =" in data_sql
    assert "ORDER BY mcp_versions.released_at DESC" in data_sql
    assert " LIMIT " in data_sql and " OFFSET " in data_sql
    assert {LISTING_ID, ListingStatus.approved, 5, 10} <= _bound_values(data_stmt)
    assert "count(mcp_versions.id)" in count_sql
    assert "mcp_versions.status =" in count_sql
    assert {LISTING_ID, ListingStatus.approved} == _bound_values(count_stmt)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("access", "expects_status_filter"),
    [("owner", False), ("coauthor", False), ("reviewer", False), ("admin", False), ("other", True)],
)
async def test_list_version_visibility_matches_effective_permissions(monkeypatch, access, expects_status_filter):
    co_authors = [str(COAUTHOR_ID)] if access == "coauthor" else []
    listing = _listing("skill", co_authors=co_authors)
    if access == "owner":
        actor = _user(user_id=OWNER_ID)
    elif access == "coauthor":
        actor = _user(user_id=COAUTHOR_ID)
    elif access == "reviewer":
        actor = _user(user_id=REVIEWER_ID, role=UserRole.reviewer)
    elif access == "admin":
        actor = _user(role=UserRole.admin)
    else:
        actor = _user()
    db = _db()
    db.execute.side_effect = [_result(), _result(scalar_value=0)]
    monkeypatch.setattr(versions, "resolve_visible_listing", AsyncMock(return_value=listing))

    result = await versions._list_versions(str(LISTING_ID), 1, 20, SkillListing, SkillVersion, "skill", db, actor)

    assert result == {"items": [], "total": 0, "page": 1, "page_size": 20}
    for awaited in db.execute.await_args_list:
        statement = _sql(awaited.args[0])
        assert ("skill_versions.status IN" in statement) is expects_status_filter
        assert ("skill_versions.requires_global_review IS false" in statement) is expects_status_filter


@pytest.mark.asyncio
async def test_list_versions_missing_listing_and_database_failures_do_not_continue(monkeypatch):
    actor = _user()
    db = _db()
    resolve = AsyncMock(return_value=None)
    monkeypatch.setattr(versions, "resolve_visible_listing", resolve)

    with pytest.raises(HTTPException) as missing:
        await versions._list_versions(str(LISTING_ID), 1, 20, McpListing, McpVersion, "mcp", db, actor)
    _assert_http(missing, 404, "Listing not found")
    db.execute.assert_not_awaited()

    resolve.return_value = _listing("mcp")
    db.execute.side_effect = RuntimeError("version query failed")
    with pytest.raises(RuntimeError, match="version query failed"):
        await versions._list_versions(str(LISTING_ID), 1, 20, McpListing, McpVersion, "mcp", db, actor)
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_get_version_builds_exact_approved_query_and_response(monkeypatch):
    listing = _listing("prompt")
    row = listing.latest_version
    db = _db()
    db.execute.return_value = _result(scalar=row)
    actor = _user()
    resolve = AsyncMock(return_value=listing)
    monkeypatch.setattr(versions, "resolve_visible_listing", resolve)

    result = await versions._get_version(
        "Alice/Review-Prompt",
        "1.2.3",
        PromptListing,
        PromptVersion,
        "prompt",
        db,
        actor,
    )

    assert result == versions._version_to_dict(row, "prompt")
    resolve.assert_awaited_once_with(PromptListing, "Alice/Review-Prompt", db, actor)
    statement = db.execute.await_args.args[0]
    sql = _sql(statement)
    assert "prompt_versions.listing_id =" in sql
    assert "prompt_versions.version =" in sql
    assert "prompt_versions.status =" in sql
    assert {LISTING_ID, "1.2.3", ListingStatus.approved} == _bound_values(statement)


@pytest.mark.asyncio
async def test_get_version_owner_can_read_pending_without_status_filter(monkeypatch):
    listing = _listing("hook", owner_id=OWNER_ID, status=ListingStatus.pending)
    row = listing.latest_version
    db = _db()
    db.execute.return_value = _result(scalar=row)
    actor = _user(user_id=OWNER_ID)
    monkeypatch.setattr(versions, "resolve_visible_listing", AsyncMock(return_value=listing))

    result = await versions._get_version(str(LISTING_ID), "1.2.3", HookListing, HookVersion, "hook", db, actor)

    assert result["status"] == "pending"
    assert "hook_versions.status =" not in _sql(db.execute.await_args.args[0])


@pytest.mark.asyncio
async def test_get_version_not_found_and_database_failure_are_exact(monkeypatch):
    listing = _listing("mcp")
    actor = _user()
    db = _db()
    monkeypatch.setattr(versions, "resolve_visible_listing", AsyncMock(return_value=listing))
    db.execute.return_value = _result()

    with pytest.raises(HTTPException) as missing:
        await versions._get_version(str(LISTING_ID), "9.9.9", McpListing, McpVersion, "mcp", db, actor)
    _assert_http(missing, 404, "Version not found")

    db.execute.side_effect = RuntimeError("detail query failed")
    with pytest.raises(RuntimeError, match="detail query failed"):
        await versions._get_version(str(LISTING_ID), "1.2.3", McpListing, McpVersion, "mcp", db, actor)


@pytest.mark.asyncio
async def test_every_operation_hides_a_missing_listing_without_database_mutation(monkeypatch):
    resolve = AsyncMock(return_value=None)
    monkeypatch.setattr(versions, "resolve_visible_listing", resolve)
    actor = _user(user_id=OWNER_ID, role=UserRole.reviewer)
    request = _request("mcp")
    review_request = VersionReviewRequest(action="approve")

    operations = [
        lambda db: versions._list_versions("missing", 1, 20, McpListing, McpVersion, "mcp", db, actor),
        lambda db: versions._get_version("missing", "1.0.0", McpListing, McpVersion, "mcp", db, actor),
        lambda db: versions._publish_version("missing", request, McpListing, McpVersion, "mcp", db, actor),
        lambda db: versions._version_suggestions("missing", McpListing, McpVersion, db, actor),
        lambda db: versions._review_version(
            "missing", "1.0.0", review_request, McpListing, McpVersion, "mcp", db, actor
        ),
    ]

    for operation in operations:
        db = _db()
        with pytest.raises(HTTPException) as exc:
            await operation(db)
        _assert_http(exc, 404, "Listing not found")
        db.execute.assert_not_awaited()
        db.add.assert_not_called()
        db.flush.assert_not_awaited()
        db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_rejects_invalid_semver_before_resolution_or_mutation(monkeypatch):
    resolve = AsyncMock()
    validate = Mock()
    inbox = AsyncMock()
    monkeypatch.setattr(versions, "resolve_visible_listing", resolve)
    monkeypatch.setattr(versions, "validate_and_extract", validate)
    monkeypatch.setattr(versions.inbox, "on_publish", inbox)
    db = _db()

    with pytest.raises(HTTPException) as exc:
        await versions._publish_version(
            str(LISTING_ID),
            _request("mcp", version="not-semver"),
            McpListing,
            McpVersion,
            "mcp",
            db,
            _user(user_id=OWNER_ID),
        )

    _assert_http(exc, 422, "Invalid semver string: 'not-semver'")
    resolve.assert_not_awaited()
    validate.assert_not_called()
    db.execute.assert_not_awaited()
    db.add.assert_not_called()
    inbox.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("component_type", COMPONENTS)
async def test_publish_uses_real_model_pair_snapshots_content_and_orders_transaction(monkeypatch, component_type):
    listing_model, version_model, _plural = COMPONENTS[component_type]
    listing = _listing(component_type, owner_id=OWNER_ID)
    actor = _user(user_id=OWNER_ID)
    db = _db()
    db.execute.return_value = _result()
    events = []
    db.add.side_effect = lambda row: events.append("add")

    async def flush():
        _set_generated_defaults(db, component_type)
        events.append("flush")

    db.flush.side_effect = flush
    db.commit.side_effect = lambda: events.append("commit")
    resolve = AsyncMock(return_value=listing)
    notify = AsyncMock(side_effect=lambda *args, **kwargs: events.append("inbox"))
    monkeypatch.setattr(versions, "resolve_visible_listing", resolve)
    monkeypatch.setattr(versions.inbox, "on_publish", notify)
    monkeypatch.setattr(versions, "datetime", FrozenDateTime)
    if component_type == "skill":
        listing.latest_version.review_epoch = 3  # Successors must not inherit withdrawn review cycles.
        listing.latest_version.content_revision = skill_content_revision(listing, listing.latest_version)
        monkeypatch.setattr(
            versions, "lock_skill_version", AsyncMock(return_value=(OLD_VERSION_ID, listing.latest_version))
        )
        monkeypatch.setattr(versions, "check_listing_visibility_async", AsyncMock(return_value=True))

    result = await versions._publish_version(
        "Alice/Review-Item",
        _request(component_type),
        listing_model,
        version_model,
        component_type,
        db,
        actor,
    )

    created = db.add.call_args.args[0]
    assert isinstance(created, version_model)
    assert (
        created.id,
        created.listing_id,
        created.version,
        created.description,
        created.changelog,
        created.status,
        created.released_by,
        created.released_at,
    ) == (
        NEW_VERSION_ID,
        LISTING_ID,
        "2.0.0",
        "Second release",
        "Changed behavior",
        ListingStatus.pending,
        OWNER_ID,
        NOW,
    )
    unique_field, expected = UNIQUE_FIELDS[component_type]
    assert getattr(created, unique_field) == expected
    if component_type == "mcp":
        assert created.args == ["-m", "review"]
        assert created.transport == "stdio"
    elif component_type == "skill":
        assert created.review_epoch is None  # Database supplies the new generation's default 0.
        assert created.skill_md_content == "# Review\n"
        assert created.script_content == "print('review')"
        assert created.git_url == "https://github.com/acme/skills"
        assert created.slash_command == "review-v2"
        assert created.base_version_id == listing.latest_version.id
        assert created.base_revision == skill_content_revision(listing, listing.latest_version)
        assert created.content_revision == skill_content_revision(listing, created)
        assert created.content_revision != created.base_revision
    elif component_type == "hook":
        assert created.script_content == "print('guard')"
    elif component_type == "sandbox":
        assert created.runtime_config == {"workdir": "/workspace"}
        assert created.source_url == "https://github.com/acme/sandboxes"
    assert result["id"] == str(NEW_VERSION_ID)
    assert result["status"] == "pending"
    assert result[unique_field] == expected
    assert listing.latest_version_id == OLD_VERSION_ID
    assert events == ["add", "flush", "inbox", "commit"]
    resolve.assert_awaited_once_with(listing_model, "Alice/Review-Item", db, actor)
    notify.assert_awaited_once_with(
        db,
        listing,
        subject_type=component_type,
        actor_id=OWNER_ID,
        auto_approved=False,
        version="2.0.0",
    )
    duplicate_stmt = db.execute.await_args.args[0]
    assert f"FROM {version_model.__tablename__}" in _sql(duplicate_stmt)
    assert {LISTING_ID, "2.0.0"} == _bound_values(duplicate_stmt)


@pytest.mark.asyncio
@pytest.mark.parametrize("component_type", COMPONENTS)
async def test_publish_snapshots_complete_metadata_when_optional_fields_are_omitted(monkeypatch, component_type):
    listing_model, version_model, _plural = COMPONENTS[component_type]
    listing = _listing(component_type, owner_id=OWNER_ID)
    listing.latest_version.supported_harnesses = ["claude-code"]
    db = _db()
    db.execute.return_value = _result()
    db.flush.side_effect = lambda: _set_generated_defaults(db, component_type)
    monkeypatch.setattr(versions, "resolve_visible_listing", AsyncMock(return_value=listing))
    monkeypatch.setattr(versions.inbox, "on_publish", AsyncMock())
    if component_type == "skill":
        monkeypatch.setattr(
            versions, "lock_skill_version", AsyncMock(return_value=(OLD_VERSION_ID, listing.latest_version))
        )
        monkeypatch.setattr(versions, "check_listing_visibility_async", AsyncMock(return_value=True))
    request = VersionPublishRequest(version="2.0.0", description="Second release")

    await versions._publish_version(
        str(LISTING_ID),
        request,
        listing_model,
        version_model,
        component_type,
        db,
        _user(user_id=OWNER_ID),
    )

    created = db.add.call_args.args[0]
    for column in version_model.__table__.columns:
        if column.name not in versions._VERSION_MANAGED_FIELDS:
            expected = getattr(listing.latest_version, column.name)
            if component_type == "skill" and column.name == "extra_files" and expected is None:
                expected = []  # Transient fixtures lack the database's non-null JSON default.
            assert getattr(created, column.name) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("access", ["coauthor", "admin"])
async def test_coauthor_and_admin_have_owner_level_publish_permission(monkeypatch, access):
    listing = _listing("mcp", co_authors=[str(COAUTHOR_ID)])
    actor = _user(user_id=COAUTHOR_ID) if access == "coauthor" else _user(role=UserRole.admin)
    db = _db()
    db.execute.return_value = _result()
    db.flush.side_effect = lambda: _set_generated_defaults(db, "mcp")
    monkeypatch.setattr(versions, "resolve_visible_listing", AsyncMock(return_value=listing))
    notify = AsyncMock()
    monkeypatch.setattr(versions.inbox, "on_publish", notify)

    result = await versions._publish_version(str(LISTING_ID), _request("mcp"), McpListing, McpVersion, "mcp", db, actor)

    assert result["released_by"] == str(actor.id)
    notify.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [UserRole.user, UserRole.reviewer])
async def test_publish_denies_nonowners_before_duplicate_query(monkeypatch, role):
    listing = _listing("mcp")
    actor = _user(role=role)
    db = _db()
    notify = AsyncMock()
    monkeypatch.setattr(versions, "resolve_visible_listing", AsyncMock(return_value=listing))
    monkeypatch.setattr(versions.inbox, "on_publish", notify)

    with pytest.raises(HTTPException) as exc:
        await versions._publish_version(str(LISTING_ID), _request("mcp"), McpListing, McpVersion, "mcp", db, actor)

    _assert_http(exc, 403, "Only the listing owner can publish versions")
    db.execute.assert_not_awaited()
    db.add.assert_not_called()
    notify.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_duplicate_is_exact_conflict_without_mutation(monkeypatch):
    listing = _listing("mcp")
    existing = _pending_version("mcp")
    db = _db()
    db.execute.return_value = _result(scalar=existing)
    notify = AsyncMock()
    validate = Mock()
    monkeypatch.setattr(versions, "resolve_visible_listing", AsyncMock(return_value=listing))
    monkeypatch.setattr(versions, "validate_and_extract", validate)
    monkeypatch.setattr(versions.inbox, "on_publish", notify)

    with pytest.raises(HTTPException) as exc:
        await versions._publish_version(
            "alice/review-mcp",
            _request("mcp"),
            McpListing,
            McpVersion,
            "mcp",
            db,
            _user(user_id=OWNER_ID),
        )

    _assert_http(exc, 409, "Version '2.0.0' already exists for this listing")
    statement = db.execute.await_args.args[0]
    assert {LISTING_ID, "2.0.0"} == _bound_values(statement)
    validate.assert_not_called()
    db.add.assert_not_called()
    db.flush.assert_not_awaited()
    notify.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["query", "validation", "flush", "inbox", "commit"])
async def test_publish_boundary_failures_stop_later_work(monkeypatch, boundary):
    listing = _listing("mcp")
    actor = _user(user_id=OWNER_ID)
    db = _db()
    events = []
    db.execute.return_value = _result()
    db.add.side_effect = lambda row: events.append("add")
    db.flush.side_effect = lambda: events.append("flush")
    db.commit.side_effect = lambda: events.append("commit")
    validate = Mock(return_value={})
    notify = AsyncMock(side_effect=lambda *args, **kwargs: events.append("inbox"))
    monkeypatch.setattr(versions, "resolve_visible_listing", AsyncMock(return_value=listing))
    monkeypatch.setattr(versions, "validate_and_extract", validate)
    monkeypatch.setattr(versions.inbox, "on_publish", notify)
    if boundary == "query":
        db.execute.side_effect = RuntimeError("query failed")
    elif boundary == "validation":
        validate.side_effect = RuntimeError("validation failed")
    elif boundary == "flush":
        db.flush.side_effect = RuntimeError("flush failed")
    elif boundary == "inbox":
        notify.side_effect = RuntimeError("inbox failed")
    else:
        db.commit.side_effect = RuntimeError("commit failed")

    with pytest.raises(RuntimeError, match=f"{boundary} failed"):
        await versions._publish_version(str(LISTING_ID), _request("mcp"), McpListing, McpVersion, "mcp", db, actor)

    if boundary == "query":
        validate.assert_not_called()
        db.add.assert_not_called()
    if boundary in {"query", "validation"}:
        db.flush.assert_not_awaited()
    if boundary in {"query", "validation", "flush"}:
        notify.assert_not_awaited()
    if boundary in {"query", "validation", "flush", "inbox"}:
        db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_version_suggestions_use_highest_valid_version_including_pending(monkeypatch):
    listing = _listing("mcp", version="1.9.9")
    actor = _user(user_id=OWNER_ID)
    db = _db()
    db.execute.return_value = _result(rows=[("garbage",), ("1.10.0",), ("1.2.99",), ("0.9.0",)])
    resolve = AsyncMock(return_value=listing)
    monkeypatch.setattr(versions, "resolve_visible_listing", resolve)

    result = await versions._version_suggestions("Alice/Review-MCP", McpListing, McpVersion, db, actor)

    assert result == {
        "current": "1.10.0",
        "suggestions": {"patch": "1.10.1", "minor": "1.11.0", "major": "2.0.0"},
    }
    resolve.assert_awaited_once_with(McpListing, "Alice/Review-MCP", db, actor)
    statement = db.execute.await_args.args[0]
    assert "SELECT mcp_versions.version" in _sql(statement)
    assert "mcp_versions.listing_id =" in _sql(statement)
    assert "ORDER BY mcp_versions.released_at DESC" in _sql(statement)
    assert _bound_values(statement) == {LISTING_ID}


@pytest.mark.asyncio
async def test_version_suggestions_start_at_zero_without_a_latest_release(monkeypatch):
    listing = _listing("skill")
    listing.latest_version = None
    listing.latest_version_id = None
    db = _db()
    db.execute.return_value = _result(rows=[])
    monkeypatch.setattr(versions, "resolve_visible_listing", AsyncMock(return_value=listing))

    result = await versions._version_suggestions(
        str(LISTING_ID), SkillListing, SkillVersion, db, _user(user_id=OWNER_ID)
    )

    assert result == {
        "current": "0.0.0",
        "suggestions": {"patch": "0.0.1", "minor": "0.1.0", "major": "1.0.0"},
    }


@pytest.mark.asyncio
async def test_version_suggestion_database_failure_propagates(monkeypatch):
    db = _db()
    db.execute.side_effect = RuntimeError("suggestion query failed")
    monkeypatch.setattr(versions, "resolve_visible_listing", AsyncMock(return_value=_listing("mcp")))

    with pytest.raises(RuntimeError, match="suggestion query failed"):
        await versions._version_suggestions(str(LISTING_ID), McpListing, McpVersion, db, _user(user_id=OWNER_ID))


@pytest.mark.asyncio
async def test_review_approve_newer_version_updates_latest_then_notifies_and_commits(monkeypatch):
    listing = _listing("mcp", version="1.9.0")
    pending = _pending_version("mcp", version="2.0.0")
    actor = _user(user_id=REVIEWER_ID, role=UserRole.reviewer)
    db = _db()
    db.execute.return_value = _result(scalar=pending)
    events = []
    notify = AsyncMock(side_effect=lambda *args, **kwargs: events.append("inbox"))
    db.commit.side_effect = lambda: events.append("commit")
    resolve = AsyncMock(return_value=listing)
    monkeypatch.setattr(versions, "resolve_visible_listing", resolve)
    monkeypatch.setattr(versions.inbox, "on_review_decided", notify)
    monkeypatch.setattr(versions, "datetime", FrozenDateTime)

    result = await versions._review_version(
        "Alice/Review-MCP",
        "2.0.0",
        VersionReviewRequest(action="approve"),
        McpListing,
        McpVersion,
        "mcp",
        db,
        actor,
    )

    assert result == {"version": "2.0.0", "new_status": "approved", "reason": None}
    assert pending.status is ListingStatus.approved
    assert pending.rejection_reason is None
    assert pending.reviewed_by == REVIEWER_ID
    assert pending.reviewed_at == NOW
    assert listing.latest_version_id == NEW_VERSION_ID
    assert events == ["inbox", "commit"]
    resolve.assert_awaited_once_with(McpListing, "Alice/Review-MCP", db, actor)
    statement = db.execute.await_args.args[0]
    assert "mcp_versions.status =" not in _sql(statement)
    assert {LISTING_ID, "2.0.0"} == _bound_values(statement)
    notify.assert_awaited_once_with(
        db,
        listing,
        subject_type="mcp",
        approved=True,
        actor_id=REVIEWER_ID,
        version="2.0.0",
        reason=None,
        submitter_id=OWNER_ID,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("current", "incoming", "updates_latest"),
    [("2.0.0", "1.9.9", False), ("2.0.0", "2.0.0", True)],
)
async def test_review_approval_uses_numeric_semver_for_latest_relationship(
    monkeypatch, current, incoming, updates_latest
):
    listing = _listing("mcp", version=current)
    original_latest_id = listing.latest_version_id
    pending = _pending_version("mcp", version=incoming)
    db = _db()
    db.execute.return_value = _result(scalar=pending)
    monkeypatch.setattr(versions, "resolve_visible_listing", AsyncMock(return_value=listing))
    monkeypatch.setattr(versions.inbox, "on_review_decided", AsyncMock())

    await versions._review_version(
        str(LISTING_ID),
        incoming,
        VersionReviewRequest(action="approve"),
        McpListing,
        McpVersion,
        "mcp",
        db,
        _user(user_id=REVIEWER_ID, role=UserRole.reviewer),
    )

    expected = NEW_VERSION_ID if updates_latest else original_latest_id
    assert listing.latest_version_id == expected


@pytest.mark.asyncio
async def test_review_approval_sets_latest_when_listing_has_no_current_release(monkeypatch):
    listing = _listing("sandbox")
    listing.latest_version = None
    listing.latest_version_id = None
    pending = _pending_version("sandbox", version="1.0.0")
    db = _db()
    db.execute.return_value = _result(scalar=pending)
    monkeypatch.setattr(versions, "resolve_visible_listing", AsyncMock(return_value=listing))
    monkeypatch.setattr(versions.inbox, "on_review_decided", AsyncMock())

    await versions._review_version(
        str(LISTING_ID),
        "1.0.0",
        VersionReviewRequest(action="approve"),
        SandboxListing,
        SandboxVersion,
        "sandbox",
        db,
        _user(user_id=REVIEWER_ID, role=UserRole.reviewer),
    )

    assert listing.latest_version_id == NEW_VERSION_ID


@pytest.mark.asyncio
async def test_review_rejection_records_reason_and_never_changes_latest(monkeypatch):
    listing = _listing("prompt")
    pending = _pending_version("prompt")
    original_latest_id = listing.latest_version_id
    actor = _user(user_id=REVIEWER_ID, role=UserRole.reviewer)
    db = _db()
    db.execute.return_value = _result(scalar=pending)
    notify = AsyncMock()
    monkeypatch.setattr(versions, "resolve_visible_listing", AsyncMock(return_value=listing))
    monkeypatch.setattr(versions.inbox, "on_review_decided", notify)
    monkeypatch.setattr(versions, "datetime", FrozenDateTime)

    result = await versions._review_version(
        str(LISTING_ID),
        "2.0.0",
        VersionReviewRequest(action="reject", reason="Needs documentation"),
        PromptListing,
        PromptVersion,
        "prompt",
        db,
        actor,
    )

    assert result == {
        "version": "2.0.0",
        "new_status": "rejected",
        "reason": "Needs documentation",
    }
    assert pending.status is ListingStatus.rejected
    assert pending.rejection_reason == "Needs documentation"
    assert pending.reviewed_by == REVIEWER_ID
    assert pending.reviewed_at == NOW
    assert listing.latest_version_id == original_latest_id
    notify.assert_awaited_once_with(
        db,
        listing,
        subject_type="prompt",
        approved=False,
        actor_id=REVIEWER_ID,
        version="2.0.0",
        reason="Needs documentation",
        submitter_id=OWNER_ID,
    )
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [ListingStatus.draft, ListingStatus.approved, ListingStatus.rejected, ListingStatus.archived],
)
async def test_review_only_accepts_pending_versions_without_side_effects(monkeypatch, status):
    listing = _listing("mcp")
    row = _pending_version("mcp")
    row.status = status
    db = _db()
    db.execute.return_value = _result(scalar=row)
    notify = AsyncMock()
    monkeypatch.setattr(versions, "resolve_visible_listing", AsyncMock(return_value=listing))
    monkeypatch.setattr(versions.inbox, "on_review_decided", notify)

    with pytest.raises(HTTPException) as exc:
        await versions._review_version(
            str(LISTING_ID),
            "2.0.0",
            VersionReviewRequest(action="approve"),
            McpListing,
            McpVersion,
            "mcp",
            db,
            _user(user_id=REVIEWER_ID, role=UserRole.reviewer),
        )

    _assert_http(exc, 422, f"Version is {status.value!r}, only pending versions can be reviewed")
    notify.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_review_hidden_or_missing_version_returns_404_without_side_effects(monkeypatch):
    listing = _listing("mcp")
    actor = _user()
    db = _db()
    db.execute.return_value = _result()
    notify = AsyncMock()
    monkeypatch.setattr(versions, "resolve_visible_listing", AsyncMock(return_value=listing))
    monkeypatch.setattr(versions.inbox, "on_review_decided", notify)

    with pytest.raises(HTTPException) as exc:
        await versions._review_version(
            str(LISTING_ID),
            "2.0.0",
            VersionReviewRequest(action="approve"),
            McpListing,
            McpVersion,
            "mcp",
            db,
            actor,
        )

    _assert_http(exc, 404, "Version not found")
    statement = db.execute.await_args.args[0]
    assert "mcp_versions.status =" in _sql(statement)
    assert ListingStatus.approved in _bound_values(statement)
    notify.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_review_currently_does_not_gate_an_active_edit_lock(monkeypatch):
    listing = _listing("hook")
    pending = _pending_version("hook")
    pending.is_editing = True
    pending.editing_by = OWNER_ID
    pending.editing_since = NOW
    db = _db()
    db.execute.return_value = _result(scalar=pending)
    monkeypatch.setattr(versions, "resolve_visible_listing", AsyncMock(return_value=listing))
    monkeypatch.setattr(versions.inbox, "on_review_decided", AsyncMock())

    result = await versions._review_version(
        str(LISTING_ID),
        "2.0.0",
        VersionReviewRequest(action="approve"),
        HookListing,
        HookVersion,
        "hook",
        db,
        _user(user_id=REVIEWER_ID, role=UserRole.reviewer),
    )

    assert result["new_status"] == "approved"
    assert pending.is_editing is True
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["query", "inbox", "commit"])
async def test_review_boundary_failures_stop_later_work(monkeypatch, boundary):
    listing = _listing("mcp")
    pending = _pending_version("mcp")
    db = _db()
    db.execute.return_value = _result(scalar=pending)
    notify = AsyncMock()
    monkeypatch.setattr(versions, "resolve_visible_listing", AsyncMock(return_value=listing))
    monkeypatch.setattr(versions.inbox, "on_review_decided", notify)
    if boundary == "query":
        db.execute.side_effect = RuntimeError("query failed")
    elif boundary == "inbox":
        notify.side_effect = RuntimeError("inbox failed")
    else:
        db.commit.side_effect = RuntimeError("commit failed")

    with pytest.raises(RuntimeError, match=f"{boundary} failed"):
        await versions._review_version(
            str(LISTING_ID),
            "2.0.0",
            VersionReviewRequest(action="approve"),
            McpListing,
            McpVersion,
            "mcp",
            db,
            _user(user_id=REVIEWER_ID, role=UserRole.reviewer),
        )

    if boundary == "query":
        notify.assert_not_awaited()
    if boundary in {"query", "inbox"}:
        db.commit.assert_not_awaited()


@pytest.mark.parametrize("component_type", COMPONENTS)
def test_factory_exposes_the_five_exact_route_contracts(component_type):
    listing_model, version_model, _plural = COMPONENTS[component_type]
    router = versions.create_version_router(component_type, listing_model, version_model)

    contracts = {(next(iter(route.methods)), route.path) for route in router.routes}

    assert contracts == {
        ("GET", "/{listing_id}/versions"),
        ("POST", "/{listing_id}/versions"),
        ("GET", "/{listing_id}/versions/{version}"),
        ("POST", "/{listing_id}/versions/{version}/review"),
        ("GET", "/{listing_id}/version-suggestions"),
    }
    assert {tag for route in router.routes for tag in route.tags} == {f"{component_type}-versions"}


def _generic_app(component_type="mcp", *, actor=None):
    listing_model, version_model, plural = COMPONENTS[component_type]
    db = _db()
    app = FastAPI()
    app.include_router(
        versions.create_version_router(component_type, listing_model, version_model),
        prefix=f"/api/v1/{plural}",
    )
    app.dependency_overrides[get_db] = lambda: db
    if actor is not None:
        app.dependency_overrides[get_current_user] = lambda: actor
        app.dependency_overrides[get_registry_user] = lambda: actor
    return app, db


@pytest.mark.asyncio
async def test_factory_handlers_delegate_every_argument_and_return_exact_payloads(monkeypatch):
    actor = _user(user_id=REVIEWER_ID, role=UserRole.reviewer)
    app, db = _generic_app(actor=actor)
    list_call = AsyncMock(return_value={"kind": "list"})
    detail_call = AsyncMock(return_value={"kind": "detail"})
    publish_call = AsyncMock(return_value={"kind": "publish"})
    review_call = AsyncMock(return_value={"kind": "review"})
    suggestions_call = AsyncMock(return_value={"kind": "suggestions"})
    monkeypatch.setattr(versions, "_list_versions", list_call)
    monkeypatch.setattr(versions, "_get_version", detail_call)
    monkeypatch.setattr(versions, "_publish_version", publish_call)
    monkeypatch.setattr(versions, "_review_version", review_call)
    monkeypatch.setattr(versions, "_version_suggestions", suggestions_call)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get(f"/api/v1/mcps/{LISTING_ID}/versions?page=2&page_size=7")
        detailed = await client.get(f"/api/v1/mcps/{LISTING_ID}/versions/1.2.3")
        published = await client.post(
            f"/api/v1/mcps/{LISTING_ID}/versions",
            json={"version": "2.0.0", "description": "Second", "extra": {"command": "python"}},
        )
        reviewed = await client.post(
            f"/api/v1/mcps/{LISTING_ID}/versions/2.0.0/review",
            json={"action": "reject", "reason": "policy"},
        )
        suggested = await client.get(f"/api/v1/mcps/{LISTING_ID}/version-suggestions")

    assert [response.json() for response in (listed, detailed, published, reviewed, suggested)] == [
        {"kind": "list"},
        {"kind": "detail"},
        {"kind": "publish"},
        {"kind": "review"},
        {"kind": "suggestions"},
    ]
    list_call.assert_awaited_once_with(
        listing_id=str(LISTING_ID),
        page=2,
        page_size=7,
        listing_model=McpListing,
        version_model=McpVersion,
        component_type="mcp",
        db=db,
        current_user=actor,
    )
    detail_call.assert_awaited_once_with(
        listing_id=str(LISTING_ID),
        version="1.2.3",
        listing_model=McpListing,
        version_model=McpVersion,
        component_type="mcp",
        db=db,
        current_user=actor,
    )
    publish_kwargs = publish_call.await_args.kwargs
    assert publish_kwargs == {
        "listing_id": str(LISTING_ID),
        "req": publish_kwargs["req"],
        "listing_model": McpListing,
        "version_model": McpVersion,
        "component_type": "mcp",
        "db": db,
        "current_user": actor,
    }
    assert publish_kwargs["req"].model_dump() == {
        "version": "2.0.0",
        "description": "Second",
        "changelog": None,
        "supported_harnesses": [],
        "extra": {"command": "python"},
    }
    review_kwargs = review_call.await_args.kwargs
    assert review_kwargs == {
        "listing_id": str(LISTING_ID),
        "version": "2.0.0",
        "req": review_kwargs["req"],
        "listing_model": McpListing,
        "version_model": McpVersion,
        "component_type": "mcp",
        "db": db,
        "current_user": actor,
    }
    assert review_kwargs["req"].model_dump() == {
        "action": "reject",
        "reason": "policy",
        "observed_revision": None,
        "git_base_acknowledged": False,
    }
    suggestions_call.assert_awaited_once_with(
        listing_id=str(LISTING_ID),
        listing_model=McpListing,
        version_model=McpVersion,
        db=db,
        current_user=actor,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("component_type", COMPONENTS)
async def test_real_component_routers_wire_publish_to_their_model_pair(monkeypatch, component_type):
    listing_model, version_model, plural = COMPONENTS[component_type]
    module = importlib.import_module(f"api.routes.{component_type}")
    app = FastAPI()
    app.include_router(module.router)
    db = _db()
    actor = _user(user_id=OWNER_ID)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: actor
    publish = AsyncMock(return_value={"version": "2.0.0", "status": "pending"})
    monkeypatch.setattr(versions, "_publish_version", publish)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/{plural}/{LISTING_ID}/versions",
            json={"version": "2.0.0", "description": "Second release"},
        )

    assert response.status_code == 200
    assert response.json() == {"version": "2.0.0", "status": "pending"}
    kwargs = publish.await_args.kwargs
    assert kwargs == {
        "listing_id": str(LISTING_ID),
        "req": kwargs["req"],
        "listing_model": listing_model,
        "version_model": version_model,
        "component_type": component_type,
        "db": db,
        "current_user": actor,
    }


@pytest.mark.asyncio
async def test_fastapi_request_validation_contracts_prevent_handler_calls(monkeypatch):
    actor = _user(user_id=REVIEWER_ID, role=UserRole.reviewer)
    app, _db_instance = _generic_app(actor=actor)
    list_call = AsyncMock()
    publish_call = AsyncMock()
    review_call = AsyncMock()
    monkeypatch.setattr(versions, "_list_versions", list_call)
    monkeypatch.setattr(versions, "_publish_version", publish_call)
    monkeypatch.setattr(versions, "_review_version", review_call)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        bad_page = await client.get(f"/api/v1/mcps/{LISTING_ID}/versions?page=0")
        bad_page_size = await client.get(f"/api/v1/mcps/{LISTING_ID}/versions?page_size=101")
        missing_description = await client.post(f"/api/v1/mcps/{LISTING_ID}/versions", json={"version": "2.0.0"})
        invalid_extra = await client.post(
            f"/api/v1/mcps/{LISTING_ID}/versions",
            json={"version": "2.0.0", "description": "Second", "extra": []},
        )
        invalid_action = await client.post(f"/api/v1/mcps/{LISTING_ID}/versions/2.0.0/review", json={"action": "ship"})

    assert bad_page.status_code == 422
    assert bad_page.json()["detail"] == [
        {
            "type": "greater_than_equal",
            "loc": ["query", "page"],
            "msg": "Input should be greater than or equal to 1",
            "input": "0",
            "ctx": {"ge": 1},
        }
    ]
    assert bad_page_size.status_code == 422
    assert bad_page_size.json()["detail"] == [
        {
            "type": "less_than_equal",
            "loc": ["query", "page_size"],
            "msg": "Input should be less than or equal to 100",
            "input": "101",
            "ctx": {"le": 100},
        }
    ]
    assert missing_description.status_code == 422
    assert missing_description.json()["detail"] == [
        {
            "type": "missing",
            "loc": ["body", "description"],
            "msg": "Field required",
            "input": {"version": "2.0.0"},
        }
    ]
    assert invalid_extra.status_code == 422
    assert invalid_extra.json()["detail"] == [
        {
            "type": "dict_type",
            "loc": ["body", "extra"],
            "msg": "Input should be a valid dictionary",
            "input": [],
        }
    ]
    assert invalid_action.status_code == 422
    assert invalid_action.json()["detail"] == [
        {
            "type": "literal_error",
            "loc": ["body", "action"],
            "msg": "Input should be 'approve' or 'reject'",
            "input": "ship",
            "ctx": {"expected": "'approve' or 'reject'"},
        }
    ]
    list_call.assert_not_awaited()
    publish_call.assert_not_awaited()
    review_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_router_authentication_and_reviewer_role_fail_before_handlers(monkeypatch):
    unauthenticated_app, _db_instance = _generic_app()
    list_call = AsyncMock()
    monkeypatch.setattr(versions, "_list_versions", list_call)

    async with AsyncClient(transport=ASGITransport(app=unauthenticated_app), base_url="http://test") as client:
        unauthenticated = await client.get(f"/api/v1/mcps/{LISTING_ID}/versions")

    assert unauthenticated.status_code == 401
    assert unauthenticated.json() == {"detail": "Authentication required"}
    list_call.assert_not_awaited()

    user_app, _db_instance = _generic_app(actor=_user(user_id=OWNER_ID))
    review_call = AsyncMock()
    security_event = AsyncMock()
    monkeypatch.setattr(versions, "_review_version", review_call)
    monkeypatch.setattr("api.deps.emit_security_event", security_event)
    async with AsyncClient(transport=ASGITransport(app=user_app), base_url="http://test") as client:
        forbidden = await client.post(f"/api/v1/mcps/{LISTING_ID}/versions/2.0.0/review", json={"action": "approve"})

    assert forbidden.status_code == 403
    assert forbidden.json() == {"detail": "Insufficient permissions"}
    review_call.assert_not_awaited()
    security_event.assert_awaited_once()
