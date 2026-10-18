# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Server-side owner search before pagination, after team visibility checks.

The public catalogue only contains reviewed releases. Owner drafts are never
looked up by searching the public catalogue or by trusting `submitted_by` alone.
"""

from sqlalchemy import func, or_, select


def owner_page_statement(
    listing_model,
    version_model,
    user,
    visibility_filter,
    *,
    search: str | None = None,
    status=None,
    namespace=None,
    team_id=None,
    conditions=(),
):
    stmt = (
        select(listing_model)
        .join(version_model, listing_model.latest_version_id == version_model.id)
        .where(listing_model.submitted_by == user.id)
    )
    stmt = visibility_filter(stmt, listing_model, user)
    if status is not None:
        stmt = stmt.where(version_model.status == status)
    if namespace is not None:
        stmt = stmt.where(listing_model.namespace == namespace)
    if team_id is not None:
        stmt = stmt.where(listing_model.team_id == team_id)
    if conditions:
        stmt = stmt.where(*conditions)
    if search and search.strip():
        # Escape SQL LIKE wildcards: text search is literal, not an accidental
        # wildcard query across all of an owner's private drafts.
        term = search.strip()[:200].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        term = f"%{term}%"
        stmt = stmt.where(
            or_(
                listing_model.name.ilike(term, escape="\\"),
                listing_model.slug.ilike(term, escape="\\"),
                version_model.description.ilike(term, escape="\\"),
            )
        )
    return stmt


async def owner_page(
    db,
    listing_model,
    version_model,
    user,
    visibility_filter,
    *,
    search=None,
    status=None,
    namespace=None,
    team_id=None,
    conditions=(),
    limit=None,
    offset=0,
    options=None,
):
    stmt = owner_page_statement(
        listing_model,
        version_model,
        user,
        visibility_filter,
        search=search,
        status=status,
        namespace=namespace,
        team_id=team_id,
        conditions=conditions,
    )
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    # Created timestamps can tie during bulk imports; the ID stabilizes page boundaries.
    stmt = stmt.order_by(listing_model.created_at.desc(), listing_model.id.desc())
    if offset:
        stmt = stmt.offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)
    if options is not None:
        stmt = options(stmt)
    rows = await db.execute(stmt)
    return rows.scalars().all(), total or 0
