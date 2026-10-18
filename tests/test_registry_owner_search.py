# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The draft owner index must scope before counting and paginating."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from api.deps import apply_visibility_filter
from models.hook import HookListing, HookVersion
from models.mcp import McpListing, McpVersion
from models.prompt import PromptListing, PromptVersion
from models.sandbox import SandboxListing, SandboxVersion
from models.skill import SkillListing, SkillVersion
from models.user import UserRole
from services.registry_owner_search import owner_page, owner_page_statement


def _sql(stmt):
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


@pytest.mark.parametrize(
    ("listing", "version"),
    [
        (McpListing, McpVersion),
        (SkillListing, SkillVersion),
        (HookListing, HookVersion),
        (PromptListing, PromptVersion),
        (SandboxListing, SandboxVersion),
    ],
)
def test_owner_search_scopes_visibility_before_count(listing, version):
    user = SimpleNamespace(id="00000000-0000-0000-0000-000000000001")

    def restrict(stmt, model, _user):
        return stmt.where(model.is_private.is_(False))

    stmt = owner_page_statement(listing, version, user, restrict, search="review%_")
    sql = _sql(stmt)
    assert "submitted_by" in sql
    assert "is_private IS false" in sql
    assert "description" in sql
    assert any(r"review\%\_" in value for value in stmt.compile().params.values() if isinstance(value, str))


@pytest.mark.parametrize(
    ("listing", "version"),
    [
        (McpListing, McpVersion),
        (SkillListing, SkillVersion),
        (HookListing, HookVersion),
        (PromptListing, PromptVersion),
        (SandboxListing, SandboxVersion),
    ],
)
def test_removed_team_submitter_must_still_have_membership(listing, version):
    user = SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001"), role=UserRole.user)
    sql = _sql(owner_page_statement(listing, version, user, apply_visibility_filter))
    assert "submitted_by" in sql
    assert "EXISTS (SELECT team_memberships.id" in sql
    assert "team_memberships.user_id" in sql
    assert "team_memberships.team_id" in sql
    assert "team_id IS NULL" in sql  # Personal drafts still belong to the submitter.


@pytest.mark.asyncio
async def test_owner_page_counts_filtered_query_before_limit():
    db = MagicMock()
    db.scalar = AsyncMock(return_value=64)
    row_result = MagicMock()
    row_result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=row_result)
    user = SimpleNamespace(id="00000000-0000-0000-0000-000000000001")

    items, total = await owner_page(
        db,
        McpListing,
        McpVersion,
        user,
        lambda stmt, _model, _user: stmt,
        search="needle",
        limit=20,
        offset=40,
    )

    assert items == []
    assert total == 64
    count_sql = _sql(db.scalar.await_args.args[0])
    page_sql = _sql(db.execute.await_args.args[0])
    assert "needle" in count_sql and " LIMIT " not in count_sql
    assert "needle" in page_sql and "LIMIT 20 OFFSET 40" in page_sql
    assert "created_at DESC" in page_sql and "id DESC" in page_sql

    await owner_page(db, McpListing, McpVersion, user, lambda stmt, _model, _user: stmt, search="needle", offset=40)
    assert "OFFSET 40" in _sql(db.execute.await_args.args[0])
