# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the self-learn apply path when reusing existing registry components.

The reuse path writes AgentComponent rows directly, so the things that matter
are: the pinned version is real, the component name is populated, another
user's private components are unreachable, and harness capability inference
is refreshed so pull-time warnings survive.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from models.agent import Agent, AgentStatus, AgentVersion
from models.agent_component import AgentComponent
from models.base import Base
from models.hook import HookListing, HookVersion
from models.insight_report import InsightReport, InsightReportStatus
from models.mcp import ListingStatus, McpListing, McpVersion
from models.skill import SkillListing, SkillVersion
from models.user import User
from services.insights.self_learn import apply_insight_suggestions
from services.skill_revisions import skill_content_revision


@pytest.fixture()
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session
        await session.rollback()
    await engine.dispose()


async def _user(db: AsyncSession, email: str) -> User:
    user = User(email=email, username=email.split("@")[0], name=email)
    db.add(user)
    await db.flush()
    return user


async def _agent(db: AsyncSession, owner: User) -> Agent:
    # `description` and `status` are delegating properties on Agent, not
    # columns — they live on the version.
    agent = Agent(
        name="test-agent",
        namespace="test",
        slug="test-agent",
        owner="tester",
        created_by=owner.id,
    )
    db.add(agent)
    await db.flush()
    version = AgentVersion(
        agent_id=agent.id,
        version="1.4.0",
        description="Base",
        prompt="You are a test agent.",
        model_name="claude-sonnet-4",
        supported_harnesses=["claude-code", "cursor"],
        status=AgentStatus.approved,
        released_by=owner.id,
        released_at=datetime.now(UTC),
    )
    db.add(version)
    await db.flush()
    agent.latest_version_id = version.id
    await db.flush()
    return agent


async def _skill(
    db: AsyncSession,
    *,
    name: str,
    submitter: User,
    version: str = "3.2.0",
    status: ListingStatus = ListingStatus.approved,
    is_private: bool = False,
    slash_command: str | None = None,
) -> SkillListing:
    listing = SkillListing(
        name=name,
        namespace="test",
        slug=name,
        owner="tester",
        submitted_by=submitter.id,
        is_private=is_private,
    )
    db.add(listing)
    await db.flush()
    ver = SkillVersion(
        listing_id=listing.id,
        version=version,
        description=f"{name} does useful things",
        status=status,
        task_type="general",
        delivery_mode="registry_direct",
        skill_md_content=f"---\nname: {name}\ndescription: {name} skill\n---\n",
        slash_command=slash_command,
        released_by=submitter.id,
        released_at=datetime.now(UTC),
    )
    db.add(ver)
    await db.flush()
    listing.latest_version_id = ver.id
    ver.content_revision = skill_content_revision(listing, ver)
    await db.flush()
    return listing


async def _mcp(db: AsyncSession, *, name: str, submitter: User, version: str = "2.0.0") -> McpListing:
    listing = McpListing(
        name=name,
        namespace="test",
        slug=name,
        owner="tester",
        category="databases",
        submitted_by=submitter.id,
    )
    db.add(listing)
    await db.flush()
    ver = McpVersion(
        listing_id=listing.id,
        version=version,
        description=f"{name} database access",
        status=ListingStatus.approved,
        released_by=submitter.id,
        released_at=datetime.now(UTC),
    )
    db.add(ver)
    await db.flush()
    listing.latest_version_id = ver.id
    await db.flush()
    return listing


async def _report(db: AsyncSession, agent: Agent, features: list[dict]) -> InsightReport:
    now = datetime.now(UTC)
    report = InsightReport(
        agent_id=agent.id,
        status=InsightReportStatus.completed,
        period_start=now,
        period_end=now,
        started_at=now,
        created_at=now,
        narrative={"suggestions": {"features_to_try": features}},
    )
    db.add(report)
    await db.flush()
    return report


async def _components_of(db: AsyncSession, version_id: uuid.UUID) -> list[AgentComponent]:
    rows = await db.execute(select(AgentComponent).where(AgentComponent.agent_version_id == version_id))
    return list(rows.scalars().all())


async def _new_version(db: AsyncSession, agent: Agent) -> AgentVersion:
    rows = await db.execute(
        select(AgentVersion).where(
            AgentVersion.agent_id == agent.id,
            AgentVersion.status == AgentStatus.pending,
        )
    )
    return rows.scalars().one()


# ── version pinning ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reused_skill_is_pinned_to_its_actual_version(db: AsyncSession):
    owner = await _user(db, "owner@example.com")
    agent = await _agent(db, owner)
    skill = await _skill(db, name="pr-review", submitter=owner, version="3.2.0")
    report = await _report(
        db,
        agent,
        [
            {
                "action_type": "reuse_existing_component",
                "feature": "Skill",
                "name": "pr-review",
                "existing_component_id": str(skill.id),
                "one_liner": "Reviews PRs",
                "why_for_you": "You review PRs often",
            }
        ],
    )

    applied = await apply_insight_suggestions(str(report.id), db, owner.id)

    version = await _new_version(db, agent)
    components = await _components_of(db, version.id)
    linked = [c for c in components if c.component_id == skill.id]
    assert len(linked) == 1
    # The bug this guards: a hardcoded "1.0.0" pin that does not exist.
    assert linked[0].resolved_version == "3.2.0"
    assert linked[0].component_name == "pr-review"
    assert applied["linked_existing"][0]["version"] == "3.2.0"


@pytest.mark.asyncio
async def test_reusing_an_already_attached_component_does_not_duplicate_it(db: AsyncSession):
    """A component the agent already carries must not be linked twice.

    The insights shortlist excludes attached components, but the
    existing-skill fallback does not, and an agent can gain a component
    between report generation and apply. Two AgentComponent rows for one
    component -- at differing resolved_versions -- break agent pulls.
    """
    owner = await _user(db, "owner@example.com")
    agent = await _agent(db, owner)
    skill = await _skill(db, name="pr-review", submitter=owner, version="3.2.0")

    # The agent already carries this component, pinned at an older version.
    db.add(
        AgentComponent(
            agent_version_id=agent.latest_version_id,
            component_type="skill",
            component_id=skill.id,
            component_name="pr-review",
            resolved_version="2.0.0",
            order_index=0,
        )
    )
    await db.flush()

    report = await _report(
        db,
        agent,
        [
            {
                "action_type": "reuse_existing_component",
                "feature": "Skill",
                "name": "pr-review",
                "existing_component_id": str(skill.id),
                "one_liner": "Reviews PRs",
                "why_for_you": "You review PRs often",
            }
        ],
    )

    applied = await apply_insight_suggestions(str(report.id), db, owner.id)
    assert applied is not None

    version = await _new_version(db, agent)
    linked = [c for c in await _components_of(db, version.id) if c.component_id == skill.id]
    assert len(linked) == 1, f"component linked {len(linked)} times"
    # The carried row wins: it is the pin the agent was already using.
    assert linked[0].resolved_version == "2.0.0"


@pytest.mark.asyncio
async def test_created_skill_keeps_component_name(db: AsyncSession):
    owner = await _user(db, "owner@example.com")
    agent = await _agent(db, owner)
    report = await _report(
        db,
        agent,
        [
            {
                "action_type": "create_new_skill",
                "feature": "Skill",
                "name": "scope-guard",
                "one_liner": "Keeps edits in scope",
                "why_for_you": "You often over-edit",
                "example": "1. Read the diff\n2. Reject out-of-scope files",
            }
        ],
    )

    await apply_insight_suggestions(str(report.id), db, owner.id)

    version = await _new_version(db, agent)
    created = [c for c in await _components_of(db, version.id) if c.component_type == "skill"]
    assert len(created) == 1
    assert created[0].resolved_version == "1.0.0"
    # Empty names degrade the next insight report into showing UUID stubs.
    assert created[0].component_name != ""


# ── private visibility isolation ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_private_component_from_another_user_is_not_attached(db: AsyncSession):
    owner = await _user(db, "owner@example.com")
    outsider = await _user(db, "outsider@example.com")
    agent = await _agent(db, owner)
    foreign = await _skill(
        db,
        name="their-secret",
        submitter=outsider,
        is_private=True,
    )
    report = await _report(
        db,
        agent,
        [
            {
                "action_type": "reuse_existing_component",
                "feature": "Skill",
                "existing_component_id": str(foreign.id),
                "one_liner": "Secret",
                "why_for_you": "no",
            }
        ],
    )

    applied = await apply_insight_suggestions(str(report.id), db, owner.id)

    assert applied["linked_existing"] == []
    assert applied["agent_version"] is None


@pytest.mark.asyncio
async def test_personal_private_component_is_attachable(db: AsyncSession):
    owner = await _user(db, "owner@example.com")
    agent = await _agent(db, owner)
    internal = await _skill(db, name="internal-tool", submitter=owner, is_private=True, version="1.7.2")
    report = await _report(
        db,
        agent,
        [
            {
                "action_type": "reuse_existing_component",
                "feature": "Skill",
                "existing_component_id": str(internal.id),
                "one_liner": "Internal",
                "why_for_you": "yours",
            }
        ],
    )

    applied = await apply_insight_suggestions(str(report.id), db, owner.id)

    assert applied["linked_existing"][0]["id"] == str(internal.id)
    assert applied["linked_existing"][0]["version"] == "1.7.2"


# ── validation of untrusted ids ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_hallucinated_component_id_is_dropped(db: AsyncSession):
    owner = await _user(db, "owner@example.com")
    agent = await _agent(db, owner)
    report = await _report(
        db,
        agent,
        [
            {
                "action_type": "reuse_existing_component",
                "feature": "Skill",
                "existing_component_id": str(uuid.uuid4()),
                "one_liner": "Imaginary",
                "why_for_you": "nope",
            }
        ],
    )

    applied = await apply_insight_suggestions(str(report.id), db, owner.id)

    assert applied["linked_existing"] == []


@pytest.mark.asyncio
async def test_non_uuid_component_id_is_dropped(db: AsyncSession):
    owner = await _user(db, "owner@example.com")
    agent = await _agent(db, owner)
    report = await _report(
        db,
        agent,
        [
            {
                "action_type": "reuse_existing_component",
                "feature": "Skill",
                "existing_component_id": "registry/some-skill",
                "one_liner": "Bad id",
                "why_for_you": "nope",
            }
        ],
    )

    applied = await apply_insight_suggestions(str(report.id), db, owner.id)

    assert applied["linked_existing"] == []


@pytest.mark.asyncio
async def test_unapproved_component_is_not_attached(db: AsyncSession):
    owner = await _user(db, "owner@example.com")
    agent = await _agent(db, owner)
    pending = await _skill(db, name="not-ready", submitter=owner, status=ListingStatus.pending)
    report = await _report(
        db,
        agent,
        [
            {
                "action_type": "reuse_existing_component",
                "feature": "Skill",
                "existing_component_id": str(pending.id),
                "one_liner": "Pending",
                "why_for_you": "nope",
            }
        ],
    )

    applied = await apply_insight_suggestions(str(report.id), db, owner.id)

    assert applied["linked_existing"] == []


@pytest.mark.asyncio
async def test_mislabelled_type_still_resolves_by_id(db: AsyncSession):
    """The declared type is a hint; the id is authoritative."""
    owner = await _user(db, "owner@example.com")
    agent = await _agent(db, owner)
    listing = HookListing(name="scope-guard", namespace="test", slug="scope-guard", owner="t", submitted_by=owner.id)
    db.add(listing)
    await db.flush()
    ver = HookVersion(
        listing_id=listing.id,
        version="4.1.0",
        description="Guards scope",
        status=ListingStatus.approved,
        event="PreToolUse",
        handler_type="script",
        released_by=owner.id,
        released_at=datetime.now(UTC),
    )
    db.add(ver)
    await db.flush()
    listing.latest_version_id = ver.id
    await db.flush()

    report = await _report(
        db,
        agent,
        [
            {
                "action_type": "reuse_existing_component",
                "feature": "Skill",  # wrong label on purpose
                "existing_component_id": str(listing.id),
                "one_liner": "Guards scope",
                "why_for_you": "you over-edit",
            }
        ],
    )

    applied = await apply_insight_suggestions(str(report.id), db, owner.id)

    assert applied["linked_existing"][0]["type"] == "hook"
    assert applied["linked_existing"][0]["version"] == "4.1.0"


# ── MCP handling ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_existing_mcp_can_be_reused(db: AsyncSession):
    owner = await _user(db, "owner@example.com")
    agent = await _agent(db, owner)
    mcp = await _mcp(db, name="postgres-mcp", submitter=owner, version="2.0.0")
    report = await _report(
        db,
        agent,
        [
            {
                "action_type": "reuse_existing_component",
                "feature": "MCP server",
                "existing_component_id": str(mcp.id),
                "one_liner": "Postgres access",
                "why_for_you": "You query databases constantly",
            }
        ],
    )

    applied = await apply_insight_suggestions(str(report.id), db, owner.id)

    assert applied["linked_existing"][0]["type"] == "mcp"
    version = await _new_version(db, agent)
    linked = [c for c in await _components_of(db, version.id) if c.component_type == "mcp"]
    assert linked[0].resolved_version == "2.0.0"


@pytest.mark.asyncio
async def test_new_mcp_is_never_created(db: AsyncSession):
    owner = await _user(db, "owner@example.com")
    agent = await _agent(db, owner)
    report = await _report(
        db,
        agent,
        [
            {
                "action_type": "create_new_skill",
                "feature": "MCP server",
                "name": "sketchy-mcp",
                "one_liner": "Runs arbitrary commands",
                "why_for_you": "no",
                "example": "npx some-server",
            }
        ],
    )

    applied = await apply_insight_suggestions(str(report.id), db, owner.id)

    assert applied["skills"] == []
    assert applied["hooks"] == []
    assert applied["agent_version"] is None


# ── capability inference ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_capabilities_are_recomputed_for_linked_components(db: AsyncSession):
    owner = await _user(db, "owner@example.com")
    agent = await _agent(db, owner)
    skill = await _skill(db, name="slash-skill", submitter=owner, slash_command="/review")
    report = await _report(
        db,
        agent,
        [
            {
                "action_type": "reuse_existing_component",
                "feature": "Skill",
                "existing_component_id": str(skill.id),
                "one_liner": "Slash command skill",
                "why_for_you": "you review a lot",
            }
        ],
    )

    await apply_insight_suggestions(str(report.id), db, owner.id)

    version = await _new_version(db, agent)
    assert "skills" in version.required_capabilities
    # cursor has no `skills` capability, so it must drop out of the inferred set.
    assert "cursor" not in version.inferred_supported_harnesses
    assert "claude-code" in version.inferred_supported_harnesses


@pytest.mark.asyncio
async def test_mcp_reuse_does_not_shrink_harness_support(db: AsyncSession):
    """Every harness supports MCP, so attaching one must not narrow the set."""
    owner = await _user(db, "owner@example.com")
    agent = await _agent(db, owner)
    mcp = await _mcp(db, name="pg-mcp", submitter=owner)
    report = await _report(
        db,
        agent,
        [
            {
                "action_type": "reuse_existing_component",
                "feature": "MCP server",
                "existing_component_id": str(mcp.id),
                "one_liner": "Postgres",
                "why_for_you": "databases",
            }
        ],
    )

    await apply_insight_suggestions(str(report.id), db, owner.id)

    version = await _new_version(db, agent)
    assert version.required_capabilities == ["mcp_servers"]
    assert "cursor" in version.inferred_supported_harnesses
    assert "claude-code" in version.inferred_supported_harnesses
