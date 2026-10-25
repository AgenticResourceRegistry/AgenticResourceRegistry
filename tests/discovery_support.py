# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Shared fixtures for the discovery tests: an in-memory registry with every native kind."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from models.agent import Agent, AgentStatus, AgentVersion
from models.agent_component import AgentComponent
from models.base import Base
from models.discovery_entry import DiscoveryEntry
from models.hook import HookListing, HookVersion
from models.inbox import InboxItem, InboxItemEvent
from models.mcp import ListingStatus, McpListing, McpValidationResult, McpVersion
from models.prompt import PromptListing, PromptVersion
from models.review import (
    Review,
    ReviewComment,
    ReviewEvent,
    ReviewReviewerRequest,
    ReviewRevision,
    ReviewSubmission,
    ReviewSubscription,
    ReviewThread,
)
from models.sandbox import SandboxListing, SandboxVersion
from models.skill import SkillListing, SkillVersion
from models.team import Team, TeamMembership, TeamRole
from models.user import User, UserRole
from services.discovery.projection import ProjectionContext

TABLES = [
    User.__table__,
    Team.__table__,
    TeamMembership.__table__,
    SkillListing.__table__,
    SkillVersion.__table__,
    McpListing.__table__,
    McpVersion.__table__,
    McpValidationResult.__table__,
    HookListing.__table__,
    HookVersion.__table__,
    PromptListing.__table__,
    PromptVersion.__table__,
    SandboxListing.__table__,
    SandboxVersion.__table__,
    Agent.__table__,
    AgentVersion.__table__,
    AgentComponent.__table__,
    DiscoveryEntry.__table__,
    InboxItem.__table__,
    InboxItemEvent.__table__,
    Review.__table__,
    ReviewRevision.__table__,
    ReviewThread.__table__,
    ReviewSubmission.__table__,
    ReviewComment.__table__,
    ReviewReviewerRequest.__table__,
    ReviewSubscription.__table__,
    ReviewEvent.__table__,
]

CTX = ProjectionContext.from_public_url("https://observal.example.com")
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def make_engine(url: str = "sqlite+aiosqlite:///:memory:", **kwargs):
    return create_async_engine(url, **kwargs)


async def create_schema(engine) -> async_sessionmaker:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=TABLES)
    return async_sessionmaker(engine, expire_on_commit=False)


async def user(db: AsyncSession, role: UserRole = UserRole.user, email: str | None = None) -> User:
    row = User(
        id=uuid.uuid4(),
        email=email or f"{uuid.uuid4().hex[:8]}@example.test",
        username=uuid.uuid4().hex[:12],
        name="Test User",
        password_hash="x",
        role=role,
    )
    db.add(row)
    await db.flush()
    return row


async def team_with_member(db: AsyncSession, member: User) -> Team:
    team = Team(
        id=uuid.uuid4(), name=f"team-{uuid.uuid4().hex[:6]}", handle=f"t{uuid.uuid4().hex[:8]}", created_by=member.id
    )
    db.add(team)
    await db.flush()
    db.add(TeamMembership(id=uuid.uuid4(), team_id=team.id, user_id=member.id, role=TeamRole.member))
    await db.flush()
    return team


async def skill(
    db: AsyncSession,
    owner: User,
    *,
    name: str = "Security Review",
    slug: str | None = None,
    description: str = "Reviews pull requests for authentication and authorization vulnerabilities.",
    status: ListingStatus = ListingStatus.approved,
    version: str = "1.2.0",
    is_private: bool = False,
    team_id: uuid.UUID | None = None,
    task_type: str = "code_review",
    harnesses: list[str] | None = None,
    content: str | None = "---\nname: security-review\n---\n# Security Review\nLook for auth bugs.",
    co_authors: list[str] | None = None,
) -> SkillListing:
    listing = SkillListing(
        id=uuid.uuid4(),
        name=name,
        namespace="acme",
        slug=slug or name.lower().replace(" ", "-"),
        owner=owner.email,
        submitted_by=owner.id,
        is_private=is_private,
        team_id=team_id,
        co_authors=co_authors or [],
    )
    db.add(listing)
    await db.flush()
    await add_skill_version(
        db,
        listing,
        owner,
        version=version,
        status=status,
        description=description,
        task_type=task_type,
        harnesses=harnesses,
        content=content,
    )
    return listing


async def add_skill_version(
    db: AsyncSession,
    listing: SkillListing,
    owner: User,
    *,
    version: str,
    status: ListingStatus,
    description: str | None = None,
    task_type: str = "code_review",
    harnesses: list[str] | None = None,
    content: str | None = None,
    set_latest: bool = True,
) -> SkillVersion:
    row = SkillVersion(
        id=uuid.uuid4(),
        listing_id=listing.id,
        version=version,
        description=description or "Reviews pull requests for security problems.",
        status=status,
        task_type=task_type,
        delivery_mode="registry_direct" if content else "git_fetch",
        skill_md_content=content,
        released_by=owner.id,
        released_at=NOW,
        reviewed_at=NOW if status == ListingStatus.approved else None,
        supported_harnesses=harnesses if harnesses is not None else ["claude-code", "kiro", "pi"],
    )
    db.add(row)
    await db.flush()
    if set_latest:
        listing.latest_version_id = row.id
        await db.flush()
    return row


async def mcp(
    db: AsyncSession,
    owner: User,
    *,
    name: str = "GitHub",
    description: str = "Read pull requests, issues and file contents from GitHub repositories.",
    status: ListingStatus = ListingStatus.approved,
    tools: list[dict] | None = None,
) -> McpListing:
    listing = McpListing(
        id=uuid.uuid4(),
        name=name,
        namespace="acme",
        slug=name.lower(),
        category="developer-tools",
        owner=owner.email,
        submitted_by=owner.id,
    )
    db.add(listing)
    await db.flush()
    version = McpVersion(
        id=uuid.uuid4(),
        listing_id=listing.id,
        version="1.4.2",
        description=description,
        status=status,
        transport="stdio",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-github"],
        environment_variables=[{"name": "GITHUB_TOKEN", "value": "SHOULD-NEVER-LEAK"}],
        tools_schema={
            "tools": tools
            or [
                {"name": "list_pull_requests", "description": "List pull requests in a repository."},
                {"name": "get_file_contents", "description": "Read a file from a repository."},
            ]
        },
        supported_harnesses=["claude-code", "cursor", "kiro"],
        released_by=owner.id,
        released_at=NOW,
    )
    db.add(version)
    await db.flush()
    listing.latest_version_id = version.id
    await db.flush()
    return listing


async def prompt(db: AsyncSession, owner: User, *, name: str = "Release Notes") -> PromptListing:
    listing = PromptListing(
        id=uuid.uuid4(), name=name, namespace="acme", slug="release-notes", owner=owner.email, submitted_by=owner.id
    )
    db.add(listing)
    await db.flush()
    version = PromptVersion(
        id=uuid.uuid4(),
        listing_id=listing.id,
        version="0.3.0",
        description="Turns a list of merged pull requests into customer-facing release notes.",
        status=ListingStatus.approved,
        category="documentation",
        template="Write release notes for: {{changes}}",
        variables=[{"name": "changes", "required": True}],
        tags=["release", "changelog"],
        supported_harnesses=["copilot-cli"],
        released_by=owner.id,
        released_at=NOW,
    )
    db.add(version)
    await db.flush()
    listing.latest_version_id = version.id
    await db.flush()
    return listing


async def hook(db: AsyncSession, owner: User, *, name: str = "Lint on Save") -> HookListing:
    listing = HookListing(
        id=uuid.uuid4(), name=name, namespace="acme", slug="lint-on-save", owner=owner.email, submitted_by=owner.id
    )
    db.add(listing)
    await db.flush()
    version = HookVersion(
        id=uuid.uuid4(),
        listing_id=listing.id,
        version="2.0.0",
        description="Runs the project linter after every file edit.",
        status=ListingStatus.approved,
        event="PostToolUse",
        handler_type="command",
        handler_config={"command": "npm run lint"},
        supported_harnesses=["claude-code", "kiro"],
        released_by=owner.id,
        released_at=NOW,
    )
    db.add(version)
    await db.flush()
    listing.latest_version_id = version.id
    await db.flush()
    return listing


async def sandbox(db: AsyncSession, owner: User, *, name: str = "Python Runner") -> SandboxListing:
    listing = SandboxListing(
        id=uuid.uuid4(), name=name, namespace="acme", slug="python-runner", owner=owner.email, submitted_by=owner.id
    )
    db.add(listing)
    await db.flush()
    version = SandboxVersion(
        id=uuid.uuid4(),
        listing_id=listing.id,
        version="1.0.0",
        description="Runs untrusted Python in an isolated container with no network access.",
        status=ListingStatus.approved,
        runtime_type="docker",
        image="python:3.12-slim",
        network_policy="none",
        supported_harnesses=["claude-code"],
        released_by=owner.id,
        released_at=NOW,
    )
    db.add(version)
    await db.flush()
    listing.latest_version_id = version.id
    await db.flush()
    return listing


async def agent(
    db: AsyncSession,
    owner: User,
    *,
    name: str = "PR Reviewer",
    slug: str = "pr-reviewer",
    status: AgentStatus = AgentStatus.approved,
    components: list[tuple[str, uuid.UUID, str]] | None = None,
    deleted: bool = False,
) -> Agent:
    row = Agent(
        id=uuid.uuid4(),
        name=name,
        namespace="acme",
        slug=slug,
        owner=owner.email,
        created_by=owner.id,
        co_authors=[],
        deleted_at=NOW if deleted else None,
    )
    db.add(row)
    await db.flush()
    version = AgentVersion(
        id=uuid.uuid4(),
        agent_id=row.id,
        version="3.1.0",
        description="Reviews pull requests end to end using GitHub and the security review skill.",
        prompt="You are a meticulous reviewer.",
        model_name="claude-sonnet-4",
        supported_harnesses=["claude-code", "kiro"],
        status=status,
        released_by=owner.id,
        released_at=NOW,
    )
    db.add(version)
    await db.flush()
    for order, (ctype, cid, cname) in enumerate(components or []):
        db.add(
            AgentComponent(
                id=uuid.uuid4(),
                agent_version_id=version.id,
                component_type=ctype,
                component_id=cid,
                component_name=cname,
                resolved_version="1.0.0",
                order_index=order,
            )
        )
    await db.flush()
    row.latest_version_id = version.id
    await db.flush()
    return row
