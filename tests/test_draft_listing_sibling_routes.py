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


@pytest.mark.parametrize(
    ("status", "user", "hidden"),
    [
        (ListingStatus.approved, _user(), False),
        (ListingStatus.draft, _user(), True),
        (ListingStatus.pending, _user(), True),
        (ListingStatus.draft, _user(OWNER_ID), False),
        (ListingStatus.draft, _user(role=UserRole.admin), False),
        (ListingStatus.pending, _user(role=UserRole.reviewer), False),
    ],
)
def test_hidden_unapproved_listing(status, user, hidden):
    assert hidden_unapproved_listing(_listing(status), user) is hidden


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["list", "suggestions"])
async def test_draft_version_routes_404_for_non_owner(monkeypatch, route):
    monkeypatch.setattr(versions, "resolve_visible_listing", AsyncMock(return_value=_listing(ListingStatus.draft)))
    db = MagicMock()
    db.execute = AsyncMock()
    if route == "list":
        call = versions._list_versions("x", 1, 20, McpListing, McpVersion, "mcp", db, _user())
    else:
        call = versions._version_suggestions("x", McpListing, McpVersion, db, _user())
    with pytest.raises(HTTPException) as exc:
        await call
    assert exc.value.status_code == 404
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_draft_feedback_404_for_non_owner():
    db = MagicMock()
    db.scalar = AsyncMock(return_value=_listing(ListingStatus.draft))
    with pytest.raises(HTTPException) as exc:
        await feedback._visible_listing(db, "mcp", LISTING_ID, _user())
    assert exc.value.status_code == 404
    # The owner still sees it.
    assert await feedback._visible_listing(db, "mcp", LISTING_ID, _user(OWNER_ID)) is not None


@pytest.mark.asyncio
async def test_owner_cannot_rate_own_listing():
    db = MagicMock()
    db.scalar = AsyncMock(return_value=_listing(ListingStatus.approved))
    req = SimpleNamespace(listing_type="mcp", listing_id=LISTING_ID, rating=5, comment="ok", anonymous=False)
    with pytest.raises(HTTPException) as exc:
        await feedback.create_feedback(req, db=db, current_user=_user(OWNER_ID))
    assert exc.value.status_code == 403
