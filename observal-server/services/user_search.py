# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Shared user search and identity filter helpers."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import case, desc, func, literal, or_, select

from api.sanitize import escape_like
from models.user import User, live_users

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy.ext.asyncio import AsyncSession

_MIN_SIMILARITY = 0.18


@dataclass(frozen=True)
class UserSearchMatch:
    user: User
    score: float


@dataclass(frozen=True)
class UserFilterValues:
    ids: list[str]
    emails: list[str]


def _norm(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _search_score(query: str):
    q = _norm(query).lstrip("@")
    escaped = escape_like(q)
    exact = literal(q)
    prefix = f"{escaped}%"
    contains = f"%{escaped}%"
    username = func.coalesce(User.username, "")

    similarity = func.greatest(
        func.similarity(func.lower(User.name), q),
        func.similarity(func.lower(User.email), q),
        func.similarity(func.lower(username), q),
    )
    return (
        case((func.lower(username) == exact, 100), else_=0)
        + case((func.lower(User.email) == exact, 98), else_=0)
        + case((func.lower(User.name) == exact, 96), else_=0)
        + case((User.username.ilike(prefix, escape="\\"), 30), else_=0)
        + case((User.email.ilike(prefix, escape="\\"), 28), else_=0)
        + case((User.name.ilike(prefix, escape="\\"), 26), else_=0)
        + case((User.name.ilike(contains, escape="\\"), 10), else_=0)
        + similarity * 74
    )


def build_user_search_stmt(query: str, limit: int = 10):
    q = _norm(query).lstrip("@")
    escaped = escape_like(q)
    prefix = f"{escaped}%"
    contains = f"%{escaped}%"
    username = func.coalesce(User.username, "")
    score = _search_score(q).label("score")
    similarity = func.greatest(
        func.similarity(func.lower(User.name), q),
        func.similarity(func.lower(User.email), q),
        func.similarity(func.lower(username), q),
    )
    return (
        select(User, score)
        .where(
            live_users(),
            or_(
                User.username.ilike(prefix, escape="\\"),
                User.email.ilike(prefix, escape="\\"),
                User.name.ilike(contains, escape="\\"),
                User.username.op("%")(q),
                User.email.op("%")(q),
                User.name.op("%")(q),
                similarity >= _MIN_SIMILARITY,
            ),
        )
        .order_by(desc(score), User.name, User.email)
        .limit(max(1, min(limit, 50)))
    )


async def search_users(db: AsyncSession, query: str, limit: int = 10) -> list[UserSearchMatch]:
    q = _norm(query).lstrip("@")
    if len(q) < 2:
        return []

    result = await db.execute(build_user_search_stmt(q, limit))
    return [UserSearchMatch(user=user, score=float(score_value or 0)) for user, score_value in result.all()]


async def resolve_user_filter_values(db: AsyncSession, query: str, limit: int = 25) -> UserFilterValues:
    q = _norm(query)
    ids: list[str] = []
    emails: list[str] = []

    try:
        ids.append(str(uuid.UUID(q)))
    except ValueError:
        pass

    if "@" in q and not q.startswith("@"):
        emails.append(q)

    for match in await search_users(db, q, limit=limit):
        ids.append(str(match.user.id))
        emails.append(match.user.email)

    return UserFilterValues(ids=_dedupe(ids), emails=_dedupe(emails))


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def clickhouse_in_condition(column: str, values: list[str], prefix: str, params: dict[str, str]) -> str | None:
    if not values:
        return None
    placeholders = []
    for idx, value in enumerate(values):
        name = f"{prefix}_{idx}"
        placeholders.append(f"{{{name}:String}}")
        params[f"param_{name}"] = value
    return f"{column} IN ({', '.join(placeholders)})"


def clickhouse_user_conditions(
    *,
    id_column: str,
    email_column: str,
    values: UserFilterValues,
    prefix: str,
    params: dict[str, str],
) -> list[str]:
    conditions = []
    id_condition = clickhouse_in_condition(id_column, values.ids, f"{prefix}_id", params)
    email_condition = clickhouse_in_condition(email_column, values.emails, f"{prefix}_email", params)
    if id_condition:
        conditions.append(id_condition)
    if email_condition:
        conditions.append(email_condition)
    return conditions
