# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Canonical registry namespace and slug helpers."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from sqlalchemy import select, union_all, update

# The namespace charset is shared with the CLI, which enforces it client-side.
from observal_shared.namespace_rules import NAMESPACE_RULE_TEXT, is_valid_namespace

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
RESERVED_NAMESPACES = frozenset({"admin", "api", "auth", "registry", "root", "system", "teams", "users"})
RESERVED_SLUGS = frozenset(
    {"archive", "draft", "fork", "forks", "install", "resolve", "restore", "submit", "unarchive", "versions"}
)


def validate_namespace(handle: str, *, allow_reserved: bool = False) -> str:
    value = handle.strip().lower()
    if not is_valid_namespace(value):
        raise ValueError(NAMESPACE_RULE_TEXT)
    if not allow_reserved and value in RESERVED_NAMESPACES:
        raise ValueError(f"Namespace '{value}' is reserved")
    return value


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", value.strip().lower()).strip("-_")
    if not slug:
        raise ValueError("Name must contain at least one letter or number")
    if not slug[0].isalnum():
        slug = f"item-{slug}"
    slug = slug[:64].rstrip("-_")
    return validate_slug(slug)


def validate_slug(slug: str, *, allow_reserved: bool = False) -> str:
    value = slug.strip().lower()
    if not SLUG_RE.fullmatch(value):
        raise ValueError(
            "Slug must be at most 64 characters, start with a letter or number, "
            "and contain only lowercase letters, numbers, hyphens, and underscores"
        )
    if not allow_reserved and value in RESERVED_SLUGS:
        raise ValueError(f"Slug '{value}' is reserved")
    return value


def namespace_for_user(user) -> str:
    if not user.username:
        raise ValueError("A username is required before publishing registry items")
    if not is_valid_namespace(user.username):
        # Usernames predating namespace validation (spaces, underscores, ``..``)
        # were kept verbatim by migration 016, so they only fail here, on the write
        # path; case is normalised rather than rejected. Name the offender and the
        # way out, since the bare rule reads as a dead end and set_username lets
        # exactly these users rename despite the publish lock.
        raise ValueError(
            f"Your username '{user.username}' cannot be used as a registry namespace. "
            f"{NAMESPACE_RULE_TEXT}. "
            "Pick a valid username first: `observal auth set-username <name>`, or Account "
            "settings in the web UI."
        )
    # Existing deployments may already contain a now-reserved username. It remains
    # usable so migration does not silently rename login identities.
    return validate_namespace(user.username, allow_reserved=True)


def identity_for_user(user, name: str) -> tuple[str, str]:
    return namespace_for_user(user), slugify(name)


def qualified_name(namespace: str, slug: str) -> str:
    return f"{namespace}/{slug}"


def _namespace_slug_parts(identifier: str) -> tuple[str, str] | None:
    value = identifier.strip().lower()
    if value.count("/") != 1:
        return None
    namespace, slug = value.split("/", 1)
    try:
        return validate_namespace(namespace, allow_reserved=True), validate_slug(slug, allow_reserved=True)
    except ValueError:
        return None


async def user_has_listings(db: AsyncSession, user_id: uuid.UUID) -> bool:
    """Return whether a user owns any listing, including deleted agents."""
    from models.agent import Agent
    from models.hook import HookListing
    from models.mcp import McpListing
    from models.prompt import PromptListing
    from models.sandbox import SandboxListing
    from models.skill import SkillListing

    ownership = union_all(
        select(Agent.id.label("id")).where(Agent.created_by == user_id),
        select(McpListing.id.label("id")).where(McpListing.submitted_by == user_id),
        select(SkillListing.id.label("id")).where(SkillListing.submitted_by == user_id),
        select(HookListing.id.label("id")).where(HookListing.submitted_by == user_id),
        select(PromptListing.id.label("id")).where(PromptListing.submitted_by == user_id),
        select(SandboxListing.id.label("id")).where(SandboxListing.submitted_by == user_id),
    ).subquery()
    return (await db.execute(select(ownership.c.id).limit(1))).first() is not None


async def rename_namespace(db: AsyncSession, old: str, new: str) -> int:
    """Re-point every listing in ``old`` to ``new``. Returns the row count.

    A namespace is always a username, and usernames are unique, so every row in
    ``old`` belongs to the single user who holds it — matching on namespace alone
    leaves no listing stranded in a namespace nobody owns. Does not commit.
    """
    from models.agent import Agent
    from models.hook import HookListing
    from models.mcp import McpListing
    from models.prompt import PromptListing
    from models.sandbox import SandboxListing
    from models.skill import SkillListing

    moved = 0
    for model in (Agent, McpListing, SkillListing, HookListing, PromptListing, SandboxListing):
        result = await db.execute(update(model).where(model.namespace == old).values(namespace=new))
        moved += result.rowcount or 0
    return moved


async def identity_exists(
    db: AsyncSession,
    model,
    namespace: str,
    slug: str,
    *,
    exclude_id: uuid.UUID | None = None,
    active_only: bool = True,
) -> bool:
    stmt = select(model.id).where(model.namespace == namespace, model.slug == slug)
    if exclude_id is not None:
        stmt = stmt.where(model.id != exclude_id)
    if active_only and hasattr(model, "deleted_at"):
        stmt = stmt.where(model.deleted_at.is_(None))
    return (await db.execute(stmt.limit(1))).scalar_one_or_none() is not None
