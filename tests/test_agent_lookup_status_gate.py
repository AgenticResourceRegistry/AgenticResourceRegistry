# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""A name and a UUID must see the same agents.

The UUID branch of _load_agent let admins and reviewers read a pending agent,
but the name branch only admitted approved agents and the caller's own, so
`observal agent show alice/agent` returned 404 where the UUID worked.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from api.routes.agent import helpers
from models.agent import AgentStatus
from models.user import UserRole

OWNER_ID = uuid.uuid4()


def _agent(status=AgentStatus.pending, name="alice/review-agent"):
    return SimpleNamespace(id=uuid.uuid4(), created_by=OWNER_ID, co_authors=[], status=status, qualified_name=name)


def _caller(role):
    return SimpleNamespace(id=uuid.uuid4(), role=role)


def _db(*query_results):
    """Each db.execute returns the next list of agents."""
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=list(rows)))))
            for rows in query_results
        ]
    )
    return db


@pytest.fixture
def by_name(monkeypatch):
    """Force the name branch and leave visibility scoping out of the test."""
    monkeypatch.setattr(helpers, "resolve_prefix_id", AsyncMock(side_effect=HTTPException(404)))
    monkeypatch.setattr(helpers, "apply_visibility_filter", lambda stmt, model, user: stmt)


@pytest.mark.parametrize(
    ("role", "visible"),
    [(UserRole.super_admin, True), (UserRole.admin, True), (UserRole.reviewer, True), (UserRole.user, False)],
)
async def test_pending_agent_by_name_matches_uuid(monkeypatch, role, visible):
    pending, caller = _agent(), _caller(role)

    monkeypatch.setattr(helpers, "resolve_prefix_id", AsyncMock(return_value=pending))
    monkeypatch.setattr(helpers, "check_listing_visibility_async", AsyncMock(return_value=True))
    by_uuid = await helpers._load_agent(_db(), str(pending.id), prefer_user_id=caller.id, current_user=caller)

    monkeypatch.setattr(helpers, "resolve_prefix_id", AsyncMock(side_effect=HTTPException(404)))
    monkeypatch.setattr(helpers, "apply_visibility_filter", lambda stmt, model, user: stmt)
    by_name = await helpers._load_agent(
        _db([], [pending]), "alice/review-agent", prefer_user_id=caller.id, current_user=caller
    )

    assert (by_uuid is pending) is visible
    assert (by_name is pending) is visible


async def test_approved_match_skips_the_fallback(by_name):
    approved = _agent(status=AgentStatus.approved)
    db = _db([approved])

    result = await helpers._load_agent(db, "review-agent", current_user=_caller(UserRole.super_admin))

    assert result is approved
    assert db.execute.await_count == 1


async def test_bare_name_stays_unambiguous_for_admins_when_an_approved_agent_exists(by_name):
    """A pending namesake must not turn a working legacy name into a 409 for an admin."""
    approved = _agent(status=AgentStatus.approved, name="alice/review-agent")
    db = _db([approved], [approved, _agent(name="bob/review-agent")])

    assert await helpers._load_agent(db, "review-agent", current_user=_caller(UserRole.admin)) is approved


async def test_anonymous_caller_never_reaches_pending_agents(by_name):
    db = _db([])

    assert await helpers._load_agent(db, "alice/review-agent", current_user=None) is None
    assert db.execute.await_count == 1
