# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for ownership transfer of personal and teamspace listings."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from api.routes.co_authors import TransferOwnershipRequest, _get_entity_for_transfer, transfer_ownership
from models.team import TeamRole
from models.user import UserRole

ENTITY_TYPES = ["agents", "mcps", "skills", "hooks", "prompts", "sandboxes"]


def _stub_db():
    """Minimal AsyncSession stand-in for these ownership tests.

    ``execute`` answers empty rather than being absent: moving a listing out of
    a teamspace republishes it, which re-enters review, and the inbox then
    resolves reviewer recipients. A registry with no users has no reviewers, so
    empty is the truthful answer and keeps these tests about ownership.
    """
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    result.scalar_one_or_none.return_value = None
    return SimpleNamespace(
        commit=AsyncMock(),
        refresh=AsyncMock(),
        execute=AsyncMock(return_value=result),
        scalar=AsyncMock(return_value=None),
    )


class _Listing(SimpleNamespace):
    @property
    def qualified_name(self):
        return f"{self.namespace}/{self.slug}"


def _listing(entity_type, owner_id, *, team_id=None, is_private=False):
    owner_field = "created_by" if entity_type == "agents" else "submitted_by"
    return _Listing(
        id=uuid.uuid4(),
        owner="platform",
        namespace="platform" if team_id else "alice",
        slug="tool",
        co_authors=[],
        team_id=team_id,
        is_private=is_private,
        **{owner_field: owner_id},
    )


def _target_user():
    return SimpleNamespace(
        id=uuid.uuid4(),
        username="bob",
        email="bob@example.com",
        auth_provider="local",
    )


async def _transfer(entity_type, listing, current_user, target_user, db):
    with (
        patch("api.routes.co_authors._get_entity_for_transfer", new=AsyncMock(return_value=listing)),
        patch("api.routes.co_authors._resolve_target_user", new=AsyncMock(return_value=target_user)),
        patch("api.routes.co_authors.identity_exists", new=AsyncMock(return_value=False)),
        patch("api.routes.co_authors.review_publication_to_public", new=AsyncMock(return_value=True)),
        patch(
            "api.routes.co_authors.team_membership",
            new=AsyncMock(return_value=SimpleNamespace(role=TeamRole.owner)),
        ),
    ):
        return await transfer_ownership(
            entity_type,
            listing.qualified_name,
            TransferOwnershipRequest(username="bob"),
            db,
            current_user,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("entity_type", ENTITY_TYPES)
@pytest.mark.parametrize("is_private", [False, True])
async def test_transfer_of_team_listing_needs_a_teamspace_owner(entity_type, is_private):
    """A plain member cannot walk a listing out of the teamspace."""
    current_user = SimpleNamespace(id=uuid.uuid4())
    team_id = uuid.uuid4()
    listing = _listing(entity_type, current_user.id, team_id=team_id, is_private=is_private)
    db = _stub_db()

    with (
        patch("api.routes.co_authors.resolve_listing", new=AsyncMock(return_value=listing)),
        patch(
            "api.routes.co_authors.team_membership",
            new=AsyncMock(return_value=SimpleNamespace(role=TeamRole.member)),
        ),
        patch("api.routes.co_authors.is_admin", return_value=False),
        pytest.raises(HTTPException) as exc,
    ):
        await _get_entity_for_transfer(entity_type, listing.qualified_name, current_user, db)

    assert exc.value.status_code == 403
    # Nothing is rehomed, republished, or committed.
    assert listing.team_id == team_id
    assert listing.is_private is is_private
    assert listing.namespace == "platform"
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("entity_type", ENTITY_TYPES)
@pytest.mark.parametrize("is_private", [False, True])
async def test_teamspace_owner_transfer_detaches_the_listing(entity_type, is_private):
    """Transfer is the way OUT of a teamspace.

    Refusing outright used to be a dead end: deleting a teamspace requires
    emptying it, emptying it requires transferring, and transferring refused every
    team-owned listing. Leaving the teamspace also drops team visibility, because
    team-private requires a teamspace.
    """
    current_user = SimpleNamespace(id=uuid.uuid4(), role=UserRole.user)
    listing = _listing(entity_type, current_user.id, team_id=uuid.uuid4(), is_private=is_private)
    db = _stub_db()

    await _transfer(entity_type, listing, current_user, _target_user(), db)

    assert listing.team_id is None
    assert listing.is_private is False
    db.commit.assert_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("entity_type", ENTITY_TYPES)
async def test_transfer_of_personal_listing_still_works(entity_type):
    current_user = SimpleNamespace(id=uuid.uuid4())
    listing = _listing(entity_type, current_user.id)
    target_user = _target_user()
    db = _stub_db()

    response = await _transfer(entity_type, listing, current_user, target_user, db)

    assert response.qualified_name == "bob/tool"
    assert listing.team_id is None
    assert listing.is_private is False
    owner_field = "created_by" if entity_type == "agents" else "submitted_by"
    assert getattr(listing, owner_field) == target_user.id
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("team_owned", [False, True])
async def test_transfer_refuses_tracked_skill_identity_change(team_owned):
    actor = SimpleNamespace(id=uuid.uuid4(), role=UserRole.user)
    listing = _listing("skills", actor.id, team_id=uuid.uuid4() if team_owned else None, is_private=team_owned)
    original = (listing.team_id, listing.namespace, listing.is_private)
    db = _stub_db()
    db.scalar.return_value = uuid.uuid4()
    with pytest.raises(HTTPException) as exc:
        await _transfer("skills", listing, actor, _target_user(), db)
    assert exc.value.status_code == 409
    assert (listing.team_id, listing.namespace, listing.is_private) == original
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_team_check_runs_before_the_target_user_is_resolved():
    """A refused transfer must not disclose whether the target account exists."""
    current_user = SimpleNamespace(id=uuid.uuid4())
    listing = _listing("mcps", current_user.id, team_id=uuid.uuid4())
    resolve_target = AsyncMock(side_effect=AssertionError("target user resolved before the teamspace check"))
    db = _stub_db()

    with (
        patch(
            "api.routes.co_authors._get_entity_for_transfer",
            new=AsyncMock(side_effect=HTTPException(status_code=403, detail="Not authorized")),
        ),
        patch("api.routes.co_authors._resolve_target_user", new=resolve_target),
        pytest.raises(HTTPException) as exc,
    ):
        await transfer_ownership(
            "mcps",
            listing.qualified_name,
            TransferOwnershipRequest(username="bob"),
            db,
            current_user,
        )

    assert exc.value.status_code == 403
    resolve_target.assert_not_awaited()
