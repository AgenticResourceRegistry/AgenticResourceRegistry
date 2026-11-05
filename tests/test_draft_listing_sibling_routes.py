# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""OBS-1467 / OBS-1465: versions, version-suggestions and feedback must hide a
non-approved listing from non-owners exactly as ``GET /{type}/{id}`` does."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from api.deps import hidden_unapproved_listing
from api.routes import component_versions as versions
from api.routes import feedback
from models.mcp import ListingStatus, McpListing, McpVersion
from models.user import UserRole

OWNER_ID = uuid.uuid4()
OTHER_ID = uuid.uuid4()
LISTING_ID = uuid.uuid4()


def _user(user_id=OTHER_ID, role=UserRole.user):
    return SimpleNamespace(id=user_id, role=role)


def _listing(status):
    return SimpleNamespace(id=LISTING_ID, submitted_by=OWNER_ID, co_authors=[], status=status, is_private=False)


def _db():
    scope = SimpleNamespace(is_admin=False, is_global_reviewer=False, team_ids=frozenset(), public_team_ids=frozenset())
    db = MagicMock()
    db.execute = AsyncMock()
    return db, scope


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "user", "hidden"),
    [
        (ListingStatus.approved, _user(), False),
        (ListingStatus.draft, _user(), True),
        (ListingStatus.pending, _user(), True),
        (ListingStatus.draft, None, True),
        (ListingStatus.draft, _user(OWNER_ID), False),
        (ListingStatus.draft, _user(role=UserRole.admin), False),
        (ListingStatus.pending, _user(role=UserRole.reviewer), False),
    ],
)
async def test_hidden_unapproved_listing(monkeypatch, status, user, hidden):
    db, scope = _db()
    monkeypatch.setattr("services.teamspace.review_scope", AsyncMock(return_value=scope))
    assert await hidden_unapproved_listing(_listing(status), user, db) is hidden


@pytest.mark.asyncio
async def test_team_reviewer_keeps_access_to_pending_team_listing(monkeypatch):
    team_id = uuid.uuid4()
    listing = _listing(ListingStatus.pending)
    listing.team_id = team_id
    listing.is_private = True
    db, scope = _db()
    scope.team_ids = frozenset({team_id})
    monkeypatch.setattr("services.teamspace.review_scope", AsyncMock(return_value=scope))
    assert await hidden_unapproved_listing(listing, _user(), db) is False
    # A reviewer of a different team does not.
    scope.team_ids = frozenset({uuid.uuid4()})
    assert await hidden_unapproved_listing(listing, _user(), db) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["list", "suggestions"])
async def test_draft_version_routes_404_for_non_owner(monkeypatch, route):
    monkeypatch.setattr(versions, "resolve_visible_listing", AsyncMock(return_value=_listing(ListingStatus.draft)))
    db, scope = _db()
    monkeypatch.setattr("services.teamspace.review_scope", AsyncMock(return_value=scope))
    if route == "list":
        call = versions._list_versions("x", 1, 20, McpListing, McpVersion, "mcp", db, _user())
    else:
        call = versions._version_suggestions("x", McpListing, McpVersion, db, _user())
    with pytest.raises(HTTPException) as exc:
        await call
    assert exc.value.status_code == 404
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_draft_feedback_404_for_non_owner(monkeypatch):
    db, scope = _db()
    monkeypatch.setattr("services.teamspace.review_scope", AsyncMock(return_value=scope))
    db.scalar = AsyncMock(return_value=_listing(ListingStatus.draft))
    with pytest.raises(HTTPException) as exc:
        await feedback._visible_listing(db, "mcp", LISTING_ID, _user())
    assert exc.value.status_code == 404
    # The owner still sees it.
    assert await feedback._visible_listing(db, "mcp", LISTING_ID, _user(OWNER_ID)) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("author", ["owner", "coauthor"])
async def test_author_cannot_rate_own_listing(author):
    db = MagicMock()
    listing = _listing(ListingStatus.approved)
    coauthor_id = uuid.uuid4()
    listing.co_authors = [str(coauthor_id)]
    db.scalar = AsyncMock(return_value=listing)
    req = SimpleNamespace(listing_type="mcp", listing_id=LISTING_ID, rating=5, comment="ok", anonymous=False)
    with pytest.raises(HTTPException) as exc:
        await feedback.create_feedback(req, db=db, current_user=_user(OWNER_ID if author == "owner" else coauthor_id))
    assert exc.value.status_code == 403
