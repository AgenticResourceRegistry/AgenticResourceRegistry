# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Skill-specific publication rules that live in the PR-style review engine."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models import Base, ReviewState
from models.mcp import ListingStatus
from models.skill import SkillListing, SkillVersion
from models.user import User, UserRole
from services.review.decisions import gate, open_or_push, publish, submit_verdict, withdraw
from services.review.files import render_component
from services.review.policy import ApprovalPolicy


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


async def people(db):
    users = {
        "author": User(id=uuid.uuid4(), username="author", email="a@x.test", name="A", role=UserRole.user),
        "reviewer": User(id=uuid.uuid4(), username="reviewer", email="r@x.test", name="R", role=UserRole.reviewer),
        "admin": User(id=uuid.uuid4(), username="boss", email="s@x.test", name="S", role=UserRole.super_admin),
    }
    db.add_all(users.values())
    await db.flush()
    return users


async def skill(db, users, *, version="1.0.0", status=ListingStatus.pending, listing=None, **fields):
    if listing is None:
        listing = SkillListing(
            name="s",
            namespace="tests",
            slug=f"s{uuid.uuid4().hex[:6]}",
            owner="author",
            submitted_by=users["author"].id,
        )
        db.add(listing)
        await db.flush()
    row = SkillVersion(
        listing_id=listing.id,
        version=version,
        description="d",
        released_by=users["author"].id,
        released_at=datetime.now(UTC),
        task_type="other",
        skill_md_content="---\nname: s\ndescription: d\n---\n# Body\n",
        delivery_mode="registry_direct",
        status=status,
        **fields,
    )
    db.add(row)
    await db.flush()
    return listing, row


async def approve(db, review, users):
    await submit_verdict(db, review, users["reviewer"], "approve", policy=ApprovalPolicy())


@pytest.mark.asyncio
async def test_resource_files_are_in_snapshot_and_change_its_hash(db):
    users = await people(db)
    _, row = await skill(db, users, extra_files=[{"path": "refs/a.md", "content": "one", "encoding": "utf-8"}])
    before = render_component("skill", row)
    assert before["resources/refs/a.md"]["content"] == "one\n"
    row.extra_files = [{"path": "refs/a.md", "content": "one", "encoding": "utf-8", "executable": True}]
    assert render_component("skill", row) != before  # The executable bit alone is a reviewed change.
    row.extra_files = [{"path": "bin/x", "content": "AAEC", "encoding": "base64"}]
    assert "sha256" in render_component("skill", row)["resources/bin/x"]["content"]


@pytest.mark.asyncio
async def test_bundle_skill_is_never_auto_published_but_override_publishes(db, monkeypatch):
    users = await people(db)
    listing, row = await skill(db, users, extra_files=[{"path": "a.txt", "content": "x", "encoding": "utf-8"}])
    review = await open_or_push(db, "skill", listing, row, users["author"].id)
    monkeypatch.setattr("services.review.decisions._ds.get_bool", AsyncMock(return_value=False))
    auto = ApprovalPolicy(required_approvals={**ApprovalPolicy().required_approvals, "skill": 0}, auto_publish=True)
    result = await gate(db, review, policy=auto)
    assert "skill_folder_rollout" in result.requirements and not result.clear
    # Unattended publishing never skips an open item.
    with pytest.raises(HTTPException) as blocked:
        await publish(db, review, None, auto=True, policy=auto)
    assert blocked.value.status_code == 409 and "skill_folder_rollout" in blocked.value.detail
    monkeypatch.setattr("services.review.decisions._ds.get_bool", AsyncMock(return_value=True))
    await approve(db, review, users)
    await db.refresh(review)
    assert review.state != ReviewState.published  # Even with zero approvals required, a human publishes.
    # A super admin override publishes past every open item.
    await publish(db, review, users["admin"], override_reason="urgent", policy=auto)
    assert review.state == ReviewState.published


@pytest.mark.asyncio
async def test_stale_or_older_release_does_not_take_the_latest_pointer(db):
    users = await people(db)
    listing, newer = await skill(db, users, version="2.0.0", status=ListingStatus.approved)
    listing.latest_version_id = newer.id
    _, older = await skill(db, users, version="1.5.0", listing=listing)
    review = await open_or_push(db, "skill", listing, older, users["author"].id)
    await approve(db, review, users)
    with pytest.raises(HTTPException) as stale:
        await publish(db, review, users["reviewer"])
    assert stale.value.status_code == 409
    assert listing.latest_version_id == newer.id


@pytest.mark.asyncio
async def test_prerelease_publishes_without_replacing_stable_latest(db):
    users = await people(db)
    listing, stable = await skill(db, users, version="2.0.0", status=ListingStatus.approved)
    listing.latest_version_id = stable.id
    _, pre = await skill(db, users, version="1.9.0-rc.1", listing=listing)
    review = await open_or_push(db, "skill", listing, pre, users["author"].id)
    await approve(db, review, users)
    await publish(db, review, users["reviewer"])
    assert pre.status == ListingStatus.approved and listing.latest_version_id == stable.id


@pytest.mark.asyncio
async def test_moved_approved_base_blocks_even_override(db):
    users = await people(db)
    listing, base = await skill(db, users, version="1.0.0", status=ListingStatus.approved)
    _, moved = await skill(db, users, version="1.1.0", status=ListingStatus.approved, listing=listing)
    listing.latest_version_id = moved.id
    _, candidate = await skill(db, users, version="1.2.0", listing=listing, base_version_id=base.id)
    review = await open_or_push(db, "skill", listing, candidate, users["author"].id)
    await approve(db, review, users)
    result = await gate(db, review)
    assert result.hard == ("approved_base_changed",) and not result.ready
    for publisher, reason in ((users["reviewer"], None), (users["admin"], "urgent")):
        with pytest.raises(HTTPException) as blocked:
            await publish(db, review, publisher, override_reason=reason)
        assert blocked.value.status_code == 409 and "approved_base_changed" in blocked.value.detail
    assert review.state != ReviewState.published


@pytest.mark.asyncio
async def test_public_re_review_needs_global_reviewer_restores_status_and_cannot_be_withdrawn(db):
    users = await people(db)
    listing, old = await skill(db, users, version="1.0.0", status=ListingStatus.archived)
    listing.latest_version_id = old.id
    review = await open_or_push(db, "skill", listing, old, users["author"].id, backfill=True)
    old.status = ListingStatus.pending
    old.requires_global_review = True
    old.pre_public_status = "archived"
    with pytest.raises(HTTPException) as withdrawn:
        await withdraw(db, review, users["author"])
    assert withdrawn.value.status_code == 409
    team_only = User(id=uuid.uuid4(), username="tm", email="t@x.test", name="T", role=UserRole.user)
    db.add(team_only)
    await db.flush()
    with pytest.raises(HTTPException):
        await submit_verdict(db, review, team_only, "approve")
    await approve(db, review, users)
    await publish(db, review, users["reviewer"])
    assert old.status == ListingStatus.archived  # Re-review restores the pre-public state, never "approved".
    assert not old.requires_global_review and old.pre_public_status is None


@pytest.mark.asyncio
async def test_team_public_policy_can_only_tighten_the_organization_policy():
    from services.review.policy import parse_policy, policy_for

    team = {
        "required_approvals": {"mcp": 0},
        "self_approval": "counted",
        "auto_publish": True,
        "public": {"required_approvals": {"mcp": 2}, "self_approval": "counted", "auto_publish": True},
    }
    review = type("R", (), {"team_id": uuid.uuid4(), "is_private": False})()
    org = {"required_approvals": {"mcp": 1}, "auto_publish": False}
    effective = await policy_for(review, org_raw=org, team_raw=team)
    assert effective.required_approvals["mcp"] == 2  # public override applies, floor is the org's 1
    assert effective.self_approval == "not_counted" and effective.auto_publish is False  # org says no
    relaxed = await policy_for(review, org_raw={"self_approval": "counted", "auto_publish": True}, team_raw=team)
    assert relaxed.self_approval == "counted" and relaxed.auto_publish is True
    private = await policy_for(
        type("R", (), {"team_id": review.team_id, "is_private": True})(), org_raw=org, team_raw=team
    )
    assert private.required_approvals["mcp"] == 0 and private.auto_publish  # private items use the team's own rules
    with pytest.raises(ValueError):
        parse_policy({"public": {}})  # Only a teamspace policy may carry one.
    with pytest.raises(ValueError):
        parse_policy({"required_approvals": {"mcp": 6}})


@pytest.mark.asyncio
async def test_zero_approvals_with_auto_publish_publishes_on_submit(db):
    from services.review.cutover import submit_for_review
    from services.review.policy import ApprovalPolicy

    users = await people(db)
    listing, row = await skill(db, users)
    auto = ApprovalPolicy(required_approvals=dict.fromkeys(ApprovalPolicy().required_approvals, 0), auto_publish=True)
    from services.review import decisions

    original = decisions.policy_for
    decisions.policy_for = AsyncMock(return_value=auto)
    try:
        review = await submit_for_review(db, "skill", listing, row, users["author"].id)
    finally:
        decisions.policy_for = original
    assert review.state == ReviewState.published and row.status == ListingStatus.approved
    await db.refresh(listing)
    assert listing.latest_version_id == row.id


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["mcp", "hook", "prompt", "sandbox"])
async def test_auto_publish_policy_works_for_every_non_skill_type(db, kind):
    """Skill-only rules (bundle delivery) must not break auto-publish for other resource types."""
    from models.hook import HookListing, HookVersion
    from models.mcp import McpListing, McpVersion
    from models.prompt import PromptListing, PromptVersion
    from models.sandbox import SandboxListing, SandboxVersion
    from services.review import decisions
    from services.review.cutover import submit_for_review
    from services.review.policy import ApprovalPolicy

    listing_cls, version_cls, fields = {
        "mcp": (McpListing, McpVersion, dict(command="npx", mcp_validated=True)),
        "hook": (HookListing, HookVersion, dict(event="PreToolUse", handler_type="command")),
        "prompt": (PromptListing, PromptVersion, dict(template="hello", category="general")),
        "sandbox": (SandboxListing, SandboxVersion, dict(runtime_type="docker", image="python:3.12")),
    }[kind]
    users = await people(db)
    listing = listing_cls(
        name="x",
        namespace="tests",
        slug=f"x{uuid.uuid4().hex[:6]}",
        owner="a",
        submitted_by=users["author"].id,
        **({"category": "databases"} if kind == "mcp" else {}),
    )
    db.add(listing)
    await db.flush()
    version = version_cls(
        listing_id=listing.id,
        version="1.0.0",
        description="d",
        released_by=users["author"].id,
        released_at=datetime.now(UTC),
        status=ListingStatus.pending,
        **fields,
    )
    db.add(version)
    await db.flush()
    auto = ApprovalPolicy(required_approvals=dict.fromkeys(ApprovalPolicy().required_approvals, 0), auto_publish=True)
    original = decisions.policy_for
    decisions.policy_for = AsyncMock(return_value=auto)
    try:
        review = await submit_for_review(db, kind, listing, version, users["author"].id)
    finally:
        decisions.policy_for = original
    assert review.state == ReviewState.published and version.status == ListingStatus.approved


@pytest.mark.asyncio
async def test_non_skill_public_requeue_is_undone_when_the_item_goes_back_to_team(db):
    from models.prompt import PromptListing, PromptVersion
    from services import teamspace
    from services.review.decisions import open_or_push

    users = await people(db)
    listing = PromptListing(name="p", namespace="tests", slug="p1", owner="a", submitted_by=users["author"].id)
    db.add(listing)
    await db.flush()
    version = PromptVersion(
        listing_id=listing.id,
        version="1.0.0",
        description="d",
        template="hi",
        category="general",
        released_by=users["author"].id,
        released_at=datetime.now(UTC),
        status=ListingStatus.approved,
        reviewed_by=users["reviewer"].id,
    )
    db.add(version)
    await db.flush()
    listing.latest_version_id = version.id
    review = await open_or_push(db, "prompt", listing, version, users["author"].id, backfill=True)
    review.state = ReviewState.published
    listing.is_private = False  # Team approved item becomes public.
    assert await teamspace.review_publication_to_public(listing, users["author"], db, was_private=True)
    assert version.status == ListingStatus.pending and review.state != ReviewState.published
    listing.is_private = True  # ...and is taken back before any global decision.
    await teamspace.review_publication_to_public(listing, users["author"], db, was_private=False)
    assert version.status == ListingStatus.approved and version.reviewed_by == users["reviewer"].id
    assert review.state == ReviewState.published and review.is_private


def test_agent_create_rejects_duplicate_components_and_review_text_rejects_nul():
    from pydantic import ValidationError

    from schemas.agent import AgentCreateRequest
    from schemas.review import VerdictCreate

    ref = {"component_type": "prompt", "component_id": str(uuid.uuid4())}
    base = dict(name="agt", version="1.0.0", owner="o", prompt="p", model_name="m")
    with pytest.raises(ValidationError):
        AgentCreateRequest(**base, components=[ref, ref])
    AgentCreateRequest(**base, components=[ref])
    with pytest.raises(ValidationError):
        VerdictCreate(verdict="comment", body="a\x00b")


@pytest.mark.asyncio
async def test_deep_json_is_rejected_before_recursive_nul_scan():
    from api.middleware.content_type import ContentTypeMiddleware

    app = FastAPI()
    app.add_middleware(ContentTypeMiddleware)

    @app.post("/echo")
    async def echo():
        return {"ok": True}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        deep = await client.post(
            "/echo", content="[" * 350 + "0" + "]" * 350, headers={"Content-Type": "application/json"}
        )
        nul = await client.post("/echo", json={"field": "a\u0000b"})
    assert deep.status_code == 400
    assert "maximum nesting depth" in deep.json()["detail"]
    assert nul.status_code == 422


@pytest.mark.asyncio
async def test_skill_review_withdrawal_accepts_owner_level_coauthors_and_admins(monkeypatch):
    import services.review.decisions as decisions

    author = User(id=uuid.uuid4(), role=UserRole.user)
    coauthor = User(id=uuid.uuid4(), role=UserRole.user)
    admin = User(id=uuid.uuid4(), role=UserRole.admin)
    stranger = User(id=uuid.uuid4(), role=UserRole.user)
    subject = SimpleNamespace(submitted_by=author.id, co_authors=[str(coauthor.id)])
    version = SimpleNamespace(
        released_by=author.id, status=ListingStatus.pending, content_revision=None, pre_public_status=None
    )
    review = SimpleNamespace(subject_type="skill", state=ReviewState.open, id=uuid.uuid4())
    monkeypatch.setattr(decisions, "_lock_review", AsyncMock())
    monkeypatch.setattr(decisions, "_target", AsyncMock(return_value=(subject, version)))
    for user in (coauthor, admin):
        review.state = ReviewState.open
        version.status = ListingStatus.pending
        await withdraw(MagicMock(), review, user)
        assert review.state == ReviewState.closed and version.status == ListingStatus.draft
    review.state = ReviewState.open
    with pytest.raises(HTTPException) as denied:
        await withdraw(MagicMock(), review, stranger)
    assert denied.value.status_code == 403


def test_json_nul_detection_covers_nested_values_and_keys():
    from api.middleware.content_type import _has_nul

    assert _has_nul({"a": ["ok", {"b": "x\x00y"}]}) and _has_nul({"k\x00": 1})
    assert not _has_nul({"a": ["ok", {"b": "\\u0000 literal backslash text"}], "n": 1})
