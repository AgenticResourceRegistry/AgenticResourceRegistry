# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Additive review endpoints: participant ACL, draft isolation and inbox fan-out."""

import uuid
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.deps import get_current_user, get_db, get_registry_user
from api.routes.agent import router as agent_router
from api.routes.mcp import router as mcp_router
from api.routes.reviews import policy_router
from api.routes.reviews import router as review_router
from api.routes.reviews.queue import router as queue_router
from api.routes.skill import router as skill_router
from models import Base
from models.agent import AgentStatus, AgentVersion
from models.inbox import InboxItem, InboxKind, InboxState
from models.mcp import ListingStatus, McpListing, McpVersion
from models.review import Review, ReviewComment, ReviewSubmission
from models.skill import SkillListing, SkillVersion
from models.team import Team, TeamMembership, TeamRole
from models.user import User, UserRole
from services.review.decisions import open_or_push


@pytest.fixture
async def api(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        author = User(id=uuid.uuid4(), username="author", email="a@x.test", name="Author", role=UserRole.user)
        reviewer = User(id=uuid.uuid4(), username="reviewer", email="r@x.test", name="Reviewer", role=UserRole.reviewer)
        outsider = User(id=uuid.uuid4(), username="outsider", email="o@x.test", name="Outsider", role=UserRole.user)
        second = User(id=uuid.uuid4(), username="second", email="s@x.test", name="Second", role=UserRole.reviewer)
        listing = SkillListing(name="Skill", namespace="tests", slug="skill", owner="author", submitted_by=author.id)
        db.add_all([author, reviewer, outsider, second, listing])
        await db.flush()
        version = SkillVersion(
            listing_id=listing.id,
            version="1.0.0",
            description="ok",
            released_by=author.id,
            released_at=datetime.now(UTC),
            task_type="other",
            skill_md_content="# Hello\n",
        )
        db.add(version)
        await db.flush()
        review = await open_or_push(db, "skill", listing, version, author.id)
        await db.commit()
        rid, number = review.id, review.number
    app = FastAPI()
    app.include_router(queue_router)
    app.include_router(review_router)
    app.include_router(policy_router)
    app.include_router(skill_router)
    app.include_router(mcp_router)
    app.include_router(agent_router)
    user_ref = [author]

    async def db_dep():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = db_dep
    app.dependency_overrides[get_current_user] = lambda: user_ref[0]
    app.dependency_overrides[get_registry_user] = lambda: user_ref[0]

    async def silent(*args, **kwargs):
        return []

    async def checks_off():
        return False

    monkeypatch.setattr("services.review.check_config.enabled", checks_off)
    monkeypatch.setattr("services.review.notifications.deliver", silent)
    monkeypatch.setattr("api.routes.reviews.threads.notify_update", silent)
    monkeypatch.setattr("api.routes.reviews.submissions.notify_update", silent)
    monkeypatch.setattr("api.routes.reviews.detail.notify_update", silent)
    monkeypatch.setattr("api.routes.reviews.actions.notify_update", silent)
    monkeypatch.setattr("api.routes.reviews.policy.notify_update", silent)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, user_ref, (author, reviewer, outsider, second), (rid, number), factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_skill_submit_creates_review_and_old_version_action_is_gone(api):
    client, current, (author, reviewer, _, _), _, factory = api
    current[0] = author
    response = await client.post(
        "/api/v1/skills/submit",
        json={
            "name": "Cutover skill",
            "owner": "author",
            "version": "1.0.0",
            "description": "First version",
            "task_type": "code-review",
            "delivery_mode": "registry_direct",
            "skill_md_content": "---\nname: cutover-skill\ndescription: First version\n---\n# Cutover skill\n",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["review_number"] and data["review_url"] == f"/review/{data['review_number']}"
    detail = await client.get(f"/api/v1/reviews/{data['review_number']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["subject_type"] == "skill"
    current[0] = reviewer
    assert (
        await client.post(f"/api/v1/skills/{data['id']}/versions/1.0.0/review", json={"action": "approve"})
    ).status_code == 404
    async with factory() as db:
        review = await db.scalar(select(Review).where(Review.number == data["review_number"]))
        assert review is not None
        version = await db.get(SkillVersion, review.version_id)
        assert version.status.value == "pending"


@pytest.mark.asyncio
async def test_requested_changes_can_be_edited_and_resubmitted_as_revision(api):
    client, current, (author, reviewer, _, _), _, factory = api
    current[0] = author
    created = await client.post(
        "/api/v1/skills/submit",
        json={
            "name": "Revision skill",
            "owner": "author",
            "version": "1.0.0",
            "description": "Revisable",
            "task_type": "code-review",
            "delivery_mode": "registry_direct",
            "skill_md_content": "---\nname: revision-skill\ndescription: Revisable\n---\n# Before\n",
        },
    )
    assert created.status_code == 200, created.text
    listing_id, number = created.json()["id"], created.json()["review_number"]
    current[0] = reviewer
    verdict = await client.post(
        f"/api/v1/reviews/{number}/submissions",
        json={
            "verdict": "request_changes",
            "body": "Clarify the purpose",
        },
    )
    assert verdict.status_code == 201, verdict.text
    current[0] = author
    async with factory() as db:
        revision = (
            await db.scalar(select(SkillVersion).where(SkillVersion.listing_id == uuid.UUID(listing_id)))
        ).content_revision
    edited = await client.put(
        f"/api/v1/skills/{listing_id}/draft",
        json={
            "observed_revision": revision,
            "skill_md_content": "---\nname: revision-skill\ndescription: Revisable\n---\n# Clarified\n",
        },
    )
    assert edited.status_code == 200, edited.text
    # The save reached the open review as revision 2, so the reviewer sees it immediately.
    assert edited.json()["review_number"] == number and edited.json()["review_revision"] == 2
    async with factory() as db:
        version = await db.scalar(select(SkillVersion).where(SkillVersion.listing_id == uuid.UUID(listing_id)))
        revision, version_id = version.content_revision, version.id
        # The author can keep answering the requested changes until they resubmit.
        assert version.status == ListingStatus.changes_requested
    pushed = await client.post(
        f"/api/v1/skills/{listing_id}/versions/{version_id}/submit",
        json={"observed_revision": revision, "message": "Clarified"},
    )
    assert pushed.status_code == 200, pushed.text
    assert pushed.json()["version_id"] == str(version_id)
    detail = (await client.get(f"/api/v1/reviews/{number}")).json()
    assert detail["head_revision"] == 2 and detail["revisions"][-1]["message"] == "Clarified"
    async with factory() as db:
        review = await db.scalar(select(Review).where(Review.number == number))
        version = await db.get(SkillVersion, review.version_id)
        assert version.status == ListingStatus.pending


@pytest.mark.asyncio
async def test_content_changed_after_approval_never_publishes_without_a_new_revision(api):
    client, current, (author, reviewer, _, _), (rid, number), factory = api
    current[0] = author
    async with factory() as db:
        review = await db.get(Review, rid)
        listing = await db.get(SkillListing, review.subject_id)
        listing.latest_version_id = review.version_id
        admin = User(id=uuid.uuid4(), username="root", email="root@x.test", name="Root", role=UserRole.super_admin)
        db.add(admin)
        await db.commit()
    current[0] = reviewer
    assert (
        await client.post(f"/api/v1/reviews/{number}/submissions", json={"verdict": "approve", "body": "ok"})
    ).status_code == 201
    # The route refuses edits of a pending skill version, so model a racing writer directly.
    async with factory() as db:
        version = await db.get(SkillVersion, review.version_id)
        version.skill_md_content = "---\nname: test-skill\ndescription: Unreviewed\n---\n# Unreviewed behavior\n"
        await db.commit()

    gate = (await client.get(f"/api/v1/reviews/{number}/gate")).json()
    assert gate["hard_blockers"] == ["unsubmitted_changes"] and not gate["ready"]
    blocked = await client.post(f"/api/v1/reviews/{number}/publish", json={})
    assert blocked.status_code == 409 and "unsubmitted_changes" in blocked.json()["detail"]
    current[0] = admin
    overridden = await client.post(f"/api/v1/reviews/{number}/publish", json={"override_reason": "urgent"})
    assert overridden.status_code == 409 and "no override" in overridden.json()["detail"]
    async with factory() as db:
        assert (await db.get(SkillVersion, review.version_id)).status != ListingStatus.approved


async def test_super_admin_override_publishes_without_approvals(api):
    client, current, (author, reviewer, _, _), (rid, number), factory = api
    async with factory() as db:
        admin = User(id=uuid.uuid4(), username="admin", email="admin@x.test", name="Admin", role=UserRole.super_admin)
        db.add(admin)
        await db.commit()
    current[0] = reviewer
    assert (await client.post(f"/api/v1/reviews/{number}/publish", json={"override_reason": "x"})).status_code == 403
    current[0] = admin
    override = await client.post(f"/api/v1/reviews/{number}/publish", json={"override_reason": "Emergency"})
    assert override.status_code == 200, override.text


@pytest.mark.asyncio
async def test_agent_create_opens_review_before_commit(api):
    client, current, (author, _, _, _), _, factory = api
    current[0] = author
    response = await client.post(
        "/api/v1/agents",
        json={
            "name": "review-agent",
            "version": "1.0.0",
            "description": "A reviewable agent",
            "prompt": "Read files carefully.",
            "model_name": "claude-sonnet-4",
            "owner": "author",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["review_number"] and data["review_url"] == f"/review/{data['review_number']}"
    detail = await client.get(f"/api/v1/reviews/{data['review_number']}")
    assert detail.status_code == 200 and detail.json()["subject_type"] == "agent"
    async with factory() as db:
        review = await db.scalar(select(Review).where(Review.number == data["review_number"]))
        assert review is not None
        version = await db.get(AgentVersion, review.version_id)
        assert version.status == AgentStatus.pending


@pytest.mark.asyncio
async def test_detail_and_raw_diff_never_leak_to_nonparticipants(api):
    client, current, (author, reviewer, outsider, _), (rid, number), _ = api
    current[0] = outsider
    for url in (
        f"/api/v1/reviews/{number}",
        f"/api/v1/reviews/{rid}/diff",
        f"/api/v1/reviews/{number}/threads",
        f"/api/v1/reviews/{number}/revisions/1/files/SKILL.md",
    ):
        assert (await client.get(url)).status_code == 404, url
    assert (await client.get("/api/v1/reviews")).json()["items"] == []
    current[0] = author
    assert (await client.get("/api/v1/reviews")).json()["items"][0]["number"] == number
    response = await client.get(f"/api/v1/reviews/{number}")
    assert response.status_code == 200
    assert response.json()["number"] == number
    assert response.json()["author_name"] == "author"
    assert response.json()["base_version"] is None
    assert response.json()["self_approval_allowed"] is False
    assert (await client.get("/api/v1/reviews?author=me")).json()["items"][0]["number"] == number
    current[0] = reviewer
    assert (await client.get(f"/api/v1/reviews/{number}/diff")).status_code == 200
    timeline = (await client.get(f"/api/v1/reviews/{number}/timeline")).json()["items"]
    assert any(event["actor_name"] == "author" for event in timeline if event["actor_id"] == str(author.id))


@pytest.mark.asyncio
async def test_queue_filters_before_page_and_pages_visible_reviews(api):
    client, current, (author, reviewer, outsider, _), (_, first_number), factory = api
    async with factory() as db:
        listing = await db.get(
            SkillListing, (await db.scalar(select(Review).where(Review.number == first_number))).subject_id
        )
        for index in range(8):
            version = SkillVersion(
                listing_id=listing.id,
                version=f"1.{index + 1}.0",
                description="ok",
                released_by=author.id,
                released_at=datetime.now(UTC),
                task_type="other",
                skill_md_content=f"# Step {index}\n",
            )
            db.add(version)
            await db.flush()
            await open_or_push(db, "skill", listing, version, author.id)
        private = SkillListing(
            name="Private",
            namespace="tests",
            slug="private",
            owner="outsider",
            submitted_by=outsider.id,
            is_private=True,
        )
        db.add(private)
        await db.flush()
        version = SkillVersion(
            listing_id=private.id,
            version="1.0.0",
            description="ok",
            released_by=outsider.id,
            released_at=datetime.now(UTC),
            task_type="other",
            skill_md_content="# Private\n",
        )
        db.add(version)
        await db.flush()
        private_review = await open_or_push(db, "skill", private, version, outsider.id)
        await db.commit()
    current[0] = reviewer
    cursor = None
    pages = []
    for _ in range(5):
        params = {"limit": "3", "state": "all"}
        if cursor is not None:
            params["cursor"] = str(cursor)
        response = await client.get("/api/v1/reviews", params=params)
        assert response.status_code == 200, response.text
        page = response.json()
        assert len(page["items"]) <= 3
        pages.extend(row["number"] for row in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(pages) == 9 and pages == sorted(pages, reverse=True)
    assert private_review.number not in pages
    current[0] = author
    mine = (await client.get("/api/v1/reviews", params={"limit": 3, "author": "me"})).json()
    assert len(mine["items"]) == 3 and mine["next_cursor"] is not None
    current[0] = outsider
    assert [row["number"] for row in (await client.get("/api/v1/reviews")).json()["items"]] == [private_review.number]


@pytest.mark.asyncio
async def test_private_review_excludes_global_reviewer_but_keeps_author(api):
    client, current, (author, reviewer, _, _), (rid, number), factory = api
    async with factory() as db:
        review = await db.get(Review, rid)
        listing = await db.get(SkillListing, review.subject_id)
        listing.is_private = True  # The visibility may change after the review snapshot.
        await db.commit()
    current[0] = reviewer
    assert (await client.get(f"/api/v1/reviews/{number}")).status_code == 404
    assert (await client.get("/api/v1/reviews")).json()["items"] == []
    current[0] = author
    assert (await client.get(f"/api/v1/reviews/{number}")).status_code == 200


@pytest.mark.asyncio
async def test_coauthor_with_no_reviewer_role_sees_review_in_queue(api):
    client, current, (_, _, coauthor, _), (rid, number), factory = api
    async with factory() as db:
        review = await db.get(Review, rid)
        listing = await db.get(SkillListing, review.subject_id)
        listing.co_authors = [str(coauthor.id)]
        await db.commit()
    current[0] = coauthor
    assert [item["number"] for item in (await client.get("/api/v1/reviews")).json()["items"]] == [number]
    assert [item["number"] for item in (await client.get("/api/v1/reviews?author=me")).json()["items"]] == [number]


@pytest.mark.asyncio
async def test_ready_queue_keeps_cursor_when_candidate_gate_is_blocked(api):
    from services.review.decisions import submit_verdict

    client, current, (author, reviewer, _, _), (rid, number), factory = api
    async with factory() as db:
        first = await db.get(Review, rid)
        await submit_verdict(db, first, reviewer, "approve")
        listing = await db.get(SkillListing, first.subject_id)
        later = SkillVersion(
            listing_id=listing.id,
            version="2.0.0",
            description="ok",
            released_by=author.id,
            released_at=datetime.now(UTC),
            task_type="other",
            skill_md_content="# Submitted\n",
        )
        db.add(later)
        await db.flush()
        blocked = await open_or_push(db, "skill", listing, later, author.id)
        blocked.state = "approved"  # A saved draft can leave a previously green state stale.
        later.skill_md_content = "# Not submitted\n"
        await db.commit()
    current[0] = reviewer
    first_page = (await client.get("/api/v1/reviews", params={"needs": "ready_to_publish", "limit": 1})).json()
    assert first_page["items"] == [] and first_page["next_cursor"] == blocked.number
    next_page = (
        await client.get(
            "/api/v1/reviews",
            params={
                "needs": "ready_to_publish",
                "limit": 1,
                "cursor": first_page["next_cursor"],
            },
        )
    ).json()
    assert [row["number"] for row in next_page["items"]] == [number]


@pytest.mark.asyncio
async def test_team_reviewer_queue_uses_live_visibility_not_review_snapshot(api):
    client, current, (author, reviewer, outsider, _), (rid, number), factory = api
    async with factory() as db:
        team = Team(name="QA", handle="qa", created_by=author.id, is_private=True)
        db.add(team)
        await db.flush()
        db.add(TeamMembership(team_id=team.id, user_id=outsider.id, role=TeamRole.reviewer))
        review = await db.get(Review, rid)
        listing = await db.get(SkillListing, review.subject_id)
        listing.team_id = team.id
        listing.is_private = True
        review.team_id = team.id
        review.is_private = True
        await db.commit()
    current[0] = outsider
    assert [r["number"] for r in (await client.get("/api/v1/reviews")).json()["items"]] == [number]
    current[0] = reviewer
    assert (await client.get("/api/v1/reviews")).json()["items"] == []
    async with factory() as db:
        listing = await db.get(SkillListing, review.subject_id)
        listing.is_private = False
        team = await db.get(Team, team.id)
        team.is_private = False
        await db.commit()
    assert [r["number"] for r in (await client.get("/api/v1/reviews")).json()["items"]] == [number]


@pytest.mark.asyncio
async def test_draft_private_until_submitted_and_invalid_anchor_rejected(api):
    client, current, (author, reviewer, _, second), (_, number), factory = api
    current[0] = reviewer
    base = f"/api/v1/reviews/{number}"
    assert (
        await client.post(
            base + "/threads", json={"path": "SKILL.md", "side": "head", "start_line": 4, "body": "not here"}
        )
    ).status_code == 422
    created = await client.post(
        base + "/threads",
        json={"path": "SKILL.md", "side": "head", "start_line": 1, "body": "private feedback", "as_draft": True},
    )
    assert created.status_code == 201, created.text
    thread_id = created.json()["thread_id"]
    comment_id = created.json()["id"]
    assert (await client.get(base + "/threads")).json()[0]["end_line"] == 1
    assert (await client.post(base + f"/threads/{thread_id}/resolve")).status_code == 409
    current[0] = second
    assert (await client.patch(base + f"/comments/{comment_id}", json={"body": "guess"})).status_code == 404
    assert (await client.get(base + "/threads")).json() == []
    assert (await client.get(base + "/submissions/draft")).json()["comments"] == []
    assert (await client.post(base + f"/threads/{thread_id}/comments", json={"body": "peek"})).status_code == 404
    current[0] = author
    assert (await client.get(base + "/threads")).json() == []
    assert (await client.delete(base + f"/comments/{comment_id}")).status_code == 404
    current[0] = reviewer
    assert (await client.patch(base + f"/comments/{comment_id}", json={"body": "private feedback"})).status_code == 200
    assert all(e["kind"] != "comment" for e in (await client.get(base + "/timeline")).json()["items"])
    submitted = await client.post(base + "/submissions", json={"verdict": "comment", "body": "my review"})
    assert submitted.status_code == 201, submitted.text
    current[0] = author
    assert (await client.get(base + "/threads")).json()[0]["comments"][0]["body"] == "private feedback"
    async with factory() as db:
        row = await db.get(ReviewSubmission, uuid.UUID(submitted.json()["id"]))
        assert row.state == "submitted"
        assert await db.scalar(select(ReviewComment).where(ReviewComment.submission_id == row.id))


@pytest.mark.asyncio
async def test_reviewer_request_and_subscription_authorization(api):
    client, current, (author, reviewer, outsider, _), (_, number), _ = api
    base = f"/api/v1/reviews/{number}"
    current[0] = author
    assert (await client.post(base + "/reviewers", json={"user_id": str(outsider.id)})).status_code == 422
    assert (await client.put(base + "/subscription", json={"mode": "muted"})).status_code == 200
    assert (await client.put(base + "/subscription", json={"mode": "watching"})).status_code == 200
    assert (await client.post(base + "/reviewers", json={"user_id": str(reviewer.id)})).status_code == 201


@pytest.mark.asyncio
async def test_eligible_reviewers_search_and_scope(api):
    client, current, (author, reviewer, outsider, second), (rid, number), factory = api
    base = f"/api/v1/reviews/{number}/reviewers/eligible"
    current[0] = author
    initial = (await client.get(base)).json()
    assert {person["id"] for person in initial} == {str(reviewer.id), str(second.id)}
    assert all(set(person) == {"id", "name", "username", "role"} for person in initial)
    assert [person["id"] for person in (await client.get(base, params={"q": "Rev"})).json()] == [str(reviewer.id)]
    assert (await client.get(base, params={"q": "no match"})).json() == []
    assert (
        await client.post(f"/api/v1/reviews/{number}/reviewers", json={"user_id": str(reviewer.id)})
    ).status_code == 201
    assert [person["id"] for person in (await client.get(base)).json()] == [str(second.id)]
    response = await client.post(f"/api/v1/reviews/{number}/reviewers", json={"user_id": str(author.id)})
    assert response.status_code == 422
    assert response.json()["detail"] == "Choose someone other than the review author"

    async with factory() as db:
        listing = await db.get(SkillListing, (await db.get(Review, rid)).subject_id)
        listing.is_private = True
        team = Team(name="Private", handle="private", is_private=True, created_by=author.id)
        db.add(team)
        await db.flush()
        listing.team_id = team.id
        db.add(TeamMembership(team_id=team.id, user_id=outsider.id, role=TeamRole.reviewer))
        await db.commit()
    private = (await client.get(base)).json()
    assert [person["id"] for person in private] == [str(outsider.id)]
    assert (await client.get(base, params={"q": "out"})).json()[0]["username"] == "outsider"
    async with factory() as db:
        db.add_all(
            [
                User(id=uuid.uuid4(), username="admin", email="admin@x.test", name="Admin", role=UserRole.admin),
                User(id=uuid.uuid4(), username="super", email="super@x.test", name="Super", role=UserRole.super_admin),
            ]
        )
        team = await db.scalar(select(Team).where(Team.handle == "private"))
        await db.flush()
        owner = User(id=uuid.uuid4(), username="owner", email="owner@x.test", name="Team Owner", role=UserRole.user)
        db.add_all([owner, TeamMembership(team_id=team.id, user_id=owner.id, role=TeamRole.owner)])
        await db.commit()
    assert {person["username"] for person in (await client.get(base)).json()} == {"admin", "super", "outsider", "owner"}
    assert [person["username"] for person in (await client.get(base, params={"q": "team own"})).json()] == ["owner"]
    assert [person["username"] for person in (await client.get(base, params={"q": "SUP"})).json()] == ["super"]
    async with factory() as db:
        team = await db.scalar(select(Team).where(Team.handle == "private"))
        team.visibility_request_status = "pending"
        team.visibility_requested_at = datetime.now(UTC)
        await db.commit()
    assert {person["username"] for person in (await client.get(base)).json()} == {"admin", "super"}
    async with factory() as db:
        team = await db.scalar(select(Team).where(Team.handle == "private"))
        team.visibility_request_status = None
        team.is_private = False
        listing = await db.get(SkillListing, (await db.get(Review, rid)).subject_id)
        listing.is_private = False
        await db.commit()
    assert {person["username"] for person in (await client.get(base)).json()} == {
        "admin",
        "super",
        "second",
        "outsider",
        "owner",
    }
    assert (
        await client.post(f"/api/v1/reviews/{number}/reviewers", json={"user_id": str(owner.id)})
    ).status_code == 201
    current[0] = second
    assert (await client.get(base)).status_code == 200


@pytest.mark.asyncio
async def test_draft_thread_never_blocks_resolved_thread_gate(api):
    from services.review.decisions import gate, submit_verdict
    from services.review.policy import ApprovalPolicy

    client, current, (_, reviewer, _, _), (rid, number), factory = api
    async with factory() as db:
        review = await db.get(Review, rid)
        await submit_verdict(db, review, reviewer, "approve", policy=ApprovalPolicy(require_resolved_threads=True))
        await db.commit()
    current[0] = reviewer
    assert (
        await client.post(f"/api/v1/reviews/{number}/threads", json={"body": "draft", "as_draft": True})
    ).status_code == 201
    async with factory() as db:
        review = await db.get(Review, rid)
        result = await gate(db, review, policy=ApprovalPolicy(require_resolved_threads=True))
        assert result.ready and "unresolved_threads" not in result.requirements


@pytest.mark.asyncio
async def test_org_policy_change_rechecks_approved_review(api, monkeypatch):
    client, current, (_, reviewer, _, _), (rid, number), factory = api
    from models.enterprise_config import EnterpriseConfig
    from models.review import ReviewState

    async def setting(key, default=""):
        async with factory() as db:
            row = await db.scalar(select(EnterpriseConfig).where(EnterpriseConfig.key == key))
            return row.value if row else default

    async def invalidate(_):
        return None

    monkeypatch.setattr("services.dynamic_settings.get", setting)
    monkeypatch.setattr("services.dynamic_settings.invalidate", invalidate)
    current[0] = reviewer
    submitted = await client.post(f"/api/v1/reviews/{number}/submissions", json={"verdict": "approve"})
    assert submitted.status_code == 201
    async with factory() as db:
        admin = User(id=uuid.uuid4(), username="admin", email="ad@x.test", name="Admin", role=UserRole.super_admin)
        db.add(admin)
        await db.commit()
    current[0] = admin
    response = await client.put("/api/v1/admin/review-policy", json={"required_approvals": {"skill": 2}})
    assert response.status_code == 200, response.text
    async with factory() as db:
        review = await db.get(Review, rid)
        assert review.state == ReviewState.open
    assert (await client.get(f"/api/v1/reviews/{number}/gate")).json()["required"] == 2


@pytest.mark.asyncio
async def test_dismissal_requires_admin_or_team_owner(api):
    client, current, (author, reviewer, _, _), (_, number), factory = api
    current[0] = reviewer
    base = f"/api/v1/reviews/{number}"
    submitted = await client.post(base + "/submissions", json={"verdict": "approve"})
    assert submitted.status_code == 201, submitted.text
    submission_id = submitted.json()["id"]
    current[0] = author
    assert (
        await client.post(base + f"/submissions/{submission_id}/dismiss", json={"reason": "obsolete"})
    ).status_code == 403
    async with factory() as db:
        admin = User(id=uuid.uuid4(), username="admin", email="adm@x.test", name="Admin", role=UserRole.super_admin)
        db.add(admin)
        await db.commit()
    current[0] = admin
    response = await client.post(base + f"/submissions/{submission_id}/dismiss", json={"reason": "obsolete"})
    assert response.status_code == 200, response.text
    async with factory() as db:
        row = await db.get(ReviewSubmission, uuid.UUID(submission_id))
        assert row.state == "dismissed" and row.dismissed_by == admin.id


@pytest.mark.asyncio
async def test_inbox_delivery_is_transactional_and_uses_review_link(api, monkeypatch):
    from services.inbox.delivery import deliver as real_deliver
    from services.review.notifications import deliver_event

    _, _, (author, reviewer, _, _), (rid, _), factory = api
    monkeypatch.setattr("services.review.notifications.deliver", real_deliver)
    async with factory() as db:
        review = await db.get(Review, rid)
        await deliver_event(db, review, "approve", reviewer.id, submission_id=uuid.uuid4())
        await db.commit()
    async with factory() as db:
        items = (await db.scalars(select(InboxItem).where(InboxItem.user_id == author.id))).all()
        assert len(items) == 1
        assert items[0].kind == InboxKind.review_approval
        assert items[0].action_url == f"/review/{review.number}"
        assert items[0].action_command is None
        await deliver_event(db, review, "requested", author.id, reviewer_id=reviewer.id)
        await db.commit()
        request = await db.scalar(
            select(InboxItem).where(InboxItem.user_id == reviewer.id, InboxItem.kind == InboxKind.review_requested)
        )
        assert request.action_url == f"/review/{review.number}"
        assert request.action_command == f"observal review show {review.subject_id}"
        from api.routes.inbox import _to_response

        request.action_url = "/review"  # An older delivered notice is upgraded at read time.
        repaired = _to_response(request)
        assert repaired.action_url == f"/review/{review.number}"
        assert repaired.action_command == f"observal review show {review.subject_id}"


@pytest.mark.asyncio
async def test_retracted_reviewer_request_no_longer_requires_action(api, monkeypatch):
    from services.inbox.delivery import deliver as real_deliver

    client, current, (author, _, _, second), (_, number), factory = api
    monkeypatch.setattr("services.review.notifications.deliver", real_deliver)
    current[0] = author
    base = f"/api/v1/reviews/{number}/reviewers"
    assert (await client.post(base, json={"user_id": str(second.id)})).status_code == 201
    async with factory() as db:
        notice = await db.scalar(select(InboxItem).where(InboxItem.user_id == second.id))
        assert notice.state == InboxState.open
    assert (await client.delete(base + f"/{second.id}")).status_code == 200
    async with factory() as db:
        notice = await db.scalar(select(InboxItem).where(InboxItem.user_id == second.id))
        assert notice.state == InboxState.done


@pytest.mark.asyncio
async def test_review_action_items_resolve_on_verdict_and_publish(api, monkeypatch):
    from services.inbox.delivery import deliver as real_deliver

    client, current, (author, reviewer, _, second), (_, number), factory = api
    monkeypatch.setattr("services.review.notifications.deliver", real_deliver)
    base = f"/api/v1/reviews/{number}"
    current[0] = author
    assert (await client.post(base + "/reviewers", json={"user_id": str(reviewer.id)})).status_code == 201
    assert (await client.post(base + "/reviewers", json={"user_id": str(second.id)})).status_code == 201
    current[0] = reviewer
    assert (
        await client.post(base + "/submissions", json={"verdict": "request_changes", "body": "please fix"})
    ).status_code == 201
    async with factory() as db:
        reviewer_items = (
            await db.scalars(
                select(InboxItem).where(InboxItem.user_id == reviewer.id, InboxItem.kind == InboxKind.review_requested)
            )
        ).all()
        assert reviewer_items and all(i.state == InboxState.done for i in reviewer_items)
        author_requests = (
            await db.scalars(
                select(InboxItem).where(InboxItem.user_id == author.id, InboxItem.kind == InboxKind.change_requested)
            )
        ).all()
        assert author_requests and author_requests[0].state == InboxState.open
    assert (await client.post(base + "/submissions", json={"verdict": "approve"})).status_code == 201
    async with factory() as db:
        request = await db.scalar(
            select(InboxItem).where(InboxItem.user_id == author.id, InboxItem.kind == InboxKind.change_requested)
        )
        ready = await db.scalar(
            select(InboxItem).where(InboxItem.user_id == second.id, InboxItem.kind == InboxKind.review_ready)
        )
        author_ready = await db.scalar(
            select(InboxItem).where(InboxItem.user_id == author.id, InboxItem.kind == InboxKind.review_ready)
        )
        assert request.state == InboxState.done
        assert ready.state == InboxState.open and ready.action_required
        assert ready.action_url == f"/review/{number}"
        assert author_ready.action_url == f"/review/{number}"
        assert author_ready.action_required is False
    assert (await client.post(base + "/publish", json={})).status_code == 200
    async with factory() as db:
        items = (await db.scalars(select(InboxItem).where(InboxItem.user_id == second.id))).all()
        assert all(i.state == InboxState.done for i in items if i.action_required)


@pytest.mark.asyncio
async def test_review_notices_reach_reviewers_and_the_badge_counts_open_unread(api, monkeypatch):
    """OBS-1440: every reviewer in scope hears about a submission, and the badge
    counts only unread rows in Open.

    A reviewer request goes to that reviewer alone. When one reviewer approves,
    a requested peer hears it was reviewed; publication reaches the requested
    reviewer and the author, and every reviewer's request moves to Done.
    """
    from api.routes.inbox import inbox_count
    from services.inbox.delivery import deliver as real_deliver
    from services.review.notifications import deliver_event

    client, current, (author, reviewer, outsider, second), (rid, number), factory = api
    monkeypatch.setattr("services.review.notifications.deliver", real_deliver)
    base = f"/api/v1/reviews/{number}"

    async def items_for(user):
        async with factory() as db:
            return (await db.scalars(select(InboxItem).where(InboxItem.user_id == user.id))).all()

    async def badge(user):
        async with factory() as db:
            return (
                await inbox_count(
                    facets=False,
                    facet_state="open",
                    unread=None,
                    action_required=None,
                    q=None,
                    db=db,
                    current_user=user,
                )
            ).unread

    # Opening the review notifies every reviewer who can act on it, nobody else.
    async with factory() as db:
        await deliver_event(db, await db.get(Review, rid), "opened", author.id)
        await db.commit()
    for user in (reviewer, second):
        [request] = await items_for(user)
        assert request.kind == InboxKind.review_requested and request.read_at is None
    assert await items_for(author) == []
    assert await items_for(outsider) == []

    # Requesting one reviewer adds a request for that reviewer only; the author gets nothing for it.
    current[0] = author
    assert (await client.post(base + "/reviewers", json={"user_id": str(second.id)})).status_code == 201
    assert len(await items_for(second)) == 2
    assert len(await items_for(reviewer)) == 1
    assert await items_for(author) == []
    assert await items_for(outsider) == []
    assert await badge(second) == 2

    # One reviewer approves: the other hears it has been reviewed, the author hears the approval.
    current[0] = reviewer
    assert (await client.post(base + "/submissions", json={"verdict": "approve"})).status_code == 201
    second_items = {i.kind: i for i in await items_for(second)}
    approval = second_items[InboxKind.review_approval]
    assert approval.state == InboxState.open and approval.read_at is None
    # A reviewer's copy is re-checked against team membership; the author's is not.
    assert approval.payload.get("audience") == "reviewer"
    [author_approval] = [i for i in await items_for(author) if i.kind == InboxKind.review_approval]
    assert "audience" not in author_approval.payload
    # Whatever the approval resolved no longer counts; only unread Open rows do.
    async with factory() as db:
        open_unread = await db.scalar(
            select(func.count(InboxItem.id)).where(
                InboxItem.user_id == second.id,
                InboxItem.state == InboxState.open,
                InboxItem.read_at.is_(None),
            )
        )
    assert await badge(second) == open_unread

    # Publication reaches the requested reviewer and the author, not the publisher.
    assert (await client.post(base + "/publish", json={})).status_code == 200
    for user in (second, author):
        published = [i for i in await items_for(user) if i.kind == InboxKind.review_approved]
        assert published and published[0].title.startswith("Published:")
    assert not [i for i in await items_for(reviewer) if i.kind == InboxKind.review_approved]
    assert all(i.state == InboxState.done for i in await items_for(second) if i.action_required)
    assert all(i.state == InboxState.done for i in await items_for(reviewer) if i.action_required)
    # Resolved copies no longer count toward any badge.
    for user in (reviewer, second):
        assert await badge(user) == len(
            [i for i in await items_for(user) if i.state == InboxState.open and i.read_at is None]
        )


@pytest.mark.asyncio
async def test_review_websocket_uses_connection_init_bearer_and_rechecks_auth(api, monkeypatch):
    from types import SimpleNamespace

    from api.graphql import Subscription

    _, _, (_, reviewer, _, _), (rid, number), factory = api
    monkeypatch.setattr("database.async_session", factory)
    calls = []

    async def authenticate(token, db):
        calls.append(token)
        return reviewer if token == "valid" and len(calls) <= 2 else None

    async def subscribe(channel):
        assert channel == f"review:{rid}:updated"
        yield {"number": number, "state": "open", "kind": "comment"}
        yield {"number": number, "state": "open", "kind": "comment"}

    monkeypatch.setattr("api.deps._authenticate_via_jwt", authenticate)
    monkeypatch.setattr("api.graphql.subscribe", subscribe)
    info = SimpleNamespace(
        context={
            "user_id": None,
            "connection_params": {"authorization": "Bearer valid"},
            "request": SimpleNamespace(headers={}),
        }
    )
    events = [event async for event in Subscription().review_updated(info, review_id=str(rid))]
    assert len(events) == 1 and events[0].number == number
    assert calls == ["valid", "valid", "valid"]
    info.context["connection_params"] = {}
    with pytest.raises(ValueError, match="Authentication required"):
        await anext(Subscription().review_updated(info, review_id=str(rid)))


@pytest.mark.asyncio
async def test_notifications_skip_actor_and_mute_except_final(api, monkeypatch):
    from services.review.notifications import deliver_event

    client, current, (author, reviewer, _, second), (rid, _), factory = api
    captured = []

    async def capture(db, **kwargs):
        captured.append(kwargs)
        return []

    monkeypatch.setattr("services.review.notifications.deliver", capture)
    async with factory() as db:
        review = await db.get(Review, rid)
        from models.review import ReviewSubscription

        db.add(ReviewSubscription(review_id=rid, user_id=author.id, mode="muted"))
        await db.flush()

        def recipients_of(calls):
            return {uid for call in calls for uid in call["recipients"]}

        await deliver_event(db, review, "approve", reviewer.id, submission_id=uuid.uuid4())
        assert author.id not in recipients_of(captured)
        captured.clear()
        await deliver_event(db, review, "published", reviewer.id)
        assert author.id in recipients_of(captured)


@pytest.mark.asyncio
async def test_author_reopens_a_withdrawn_review_from_the_review(api):
    """OBS-1448: a withdrawn review can be reopened from the review itself, not only from the drafts list."""
    client, current, (author, reviewer, _, _), (rid, number), factory = api
    async with factory() as db:
        review = await db.get(Review, rid)
        listing = await db.get(SkillListing, review.subject_id)
        listing.latest_version_id = review.version_id
        await db.commit()
    current[0] = author
    assert (await client.post(f"/api/v1/reviews/{number}/reopen", json={})).status_code == 409  # Still open.
    assert (await client.post(f"/api/v1/reviews/{number}/withdraw")).status_code == 200
    current[0] = reviewer
    assert (await client.post(f"/api/v1/reviews/{number}/reopen", json={})).status_code == 403  # Not the owner.
    current[0] = author
    reopened = await client.post(f"/api/v1/reviews/{number}/reopen", json={"message": "Back for review"})
    assert reopened.status_code == 200, reopened.text
    assert reopened.json() == {"state": "open", "number": number}
    detail = (await client.get(f"/api/v1/reviews/{number}")).json()
    assert detail["state"] == "open" and detail["revisions"][-1]["message"] == "Back for review"
    async with factory() as db:
        version = await db.get(SkillVersion, (await db.get(Review, rid)).version_id)
        assert version.status == ListingStatus.pending


@pytest.mark.asyncio
async def test_edit_approved_mcp_successor_targets_pending_review_not_installable_release(api):
    client, current, (author, reviewer, outsider, _), _, factory = api
    async with factory() as db:
        listing = McpListing(
            name="Multi-version",
            namespace="tests",
            slug="multi-version",
            category="general",
            owner="author",
            submitted_by=author.id,
        )
        db.add(listing)
        await db.flush()
        stable = McpVersion(
            listing_id=listing.id,
            version="1.0.0",
            description="Stable",
            command="old-command",
            status=ListingStatus.approved,
            released_by=author.id,
            released_at=datetime.now(UTC),
        )
        db.add(stable)
        await db.flush()
        listing.latest_version_id = stable.id
        listing_id, stable_id = listing.id, stable.id
        await db.commit()
    current[0] = author
    created = await client.post(
        f"/api/v1/mcps/{listing_id}/versions",
        json={"version": "1.1.0", "description": "Before edit", "extra": {"command": "new-command"}},
    )
    assert created.status_code == 200, created.text
    number, pending_id = created.json()["review_number"], uuid.UUID(created.json()["id"])
    async with factory() as db:
        assert (await db.get(McpListing, listing_id)).latest_version_id == stable_id
    current[0] = reviewer
    assert (
        await client.post(f"/api/v1/reviews/{number}/submissions", json={"verdict": "approve", "body": "ok"})
    ).status_code == 201
    current[0] = outsider
    public_detail = await client.get(f"/api/v1/mcps/{listing_id}")
    assert public_detail.status_code == 200, public_detail.text
    assert public_detail.json()["version"] == "1.0.0" and public_detail.json()["status"] == "approved"
    current[0] = author
    detail = await client.get(f"/api/v1/mcps/{listing_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["version"] == "1.1.0" and detail.json()["status"] == "pending"
    assert (await client.post(f"/api/v1/mcps/{listing_id}/start-edit")).status_code == 200
    # Listing fields would change the live approved listing without review.
    renamed = await client.put(f"/api/v1/mcps/{listing_id}/draft", json={"name": "Renamed", "description": "x"})
    assert renamed.status_code == 409, renamed.text
    async with factory() as db:
        assert (await db.get(McpListing, listing_id)).name == "Multi-version"
        assert (await db.get(McpVersion, pending_id)).description == "Before edit"
    # Author lists (summaries) show the editable successor too.
    mine = await client.get("/api/v1/mcps/my")
    assert mine.status_code == 200, mine.text
    assert [(row["version"], row["status"]) for row in mine.json()] == [("1.1.0", "pending")]
    edited = await client.put(
        f"/api/v1/mcps/{listing_id}/draft", json={"name": "Multi-version", "description": "Corrected"}
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["version"] == "1.1.0" and edited.json()["review_revision"] == 2
    async with factory() as db:
        assert (await db.get(McpVersion, pending_id)).description == "Corrected"
        assert (await db.get(McpVersion, stable_id)).description == "Stable"
        assert (await db.get(McpListing, listing_id)).latest_version_id == stable_id
    # The corrected revision, not an accidental sibling, is the publish target.
    current[0] = reviewer
    assert (
        await client.post(f"/api/v1/reviews/{number}/submissions", json={"verdict": "approve", "body": "corrected"})
    ).status_code == 201
    published = await client.post(f"/api/v1/reviews/{number}/publish", json={})
    assert published.status_code == 200, published.text
    async with factory() as db:
        assert (await db.get(McpListing, listing_id)).latest_version_id == pending_id
        assert (await db.get(McpVersion, pending_id)).description == "Corrected"


@pytest.mark.asyncio
async def test_publish_clears_abandoned_component_edit_lock(api):
    client, current, (author, reviewer, _, _), _, factory = api
    current[0] = author
    created = await client.post(
        "/api/v1/mcps/submit",
        json={
            "name": "Lock release",
            "owner": "author",
            "version": "1.0.0",
            "description": "First",
            "category": "general",
            "command": "run",
        },
    )
    assert created.status_code == 200, created.text
    listing_id, number = created.json()["id"], created.json()["review_number"]
    current[0] = reviewer
    assert (
        await client.post(f"/api/v1/reviews/{number}/submissions", json={"verdict": "approve", "body": "ok"})
    ).status_code == 201
    current[0] = author
    assert (await client.post(f"/api/v1/mcps/{listing_id}/start-edit")).status_code == 200
    current[0] = reviewer
    published = await client.post(f"/api/v1/reviews/{number}/publish", json={})
    assert published.status_code == 200, published.text
    async with factory() as db:
        version = await db.scalar(select(McpVersion).where(McpVersion.listing_id == uuid.UUID(listing_id)))
        assert version.status == ListingStatus.approved
        assert version.is_editing is False
        assert version.editing_by is None and version.editing_since is None


@pytest.mark.asyncio
async def test_failing_secret_scan_blocks_normal_publish_but_super_admin_can_override(api):
    from models.review import ReviewRevision

    client, current, (author, reviewer, _, _), (rid, number), factory = api
    async with factory() as db:
        review = await db.get(Review, rid)
        head = await db.get(ReviewRevision, review.head_revision_id)
        head.checks = [
            {"id": "secrets", "name": "Secret scan", "status": "fail", "required": True, "details": ["AWS key"]},
            {"id": "schema", "name": "Required fields", "status": "pass", "required": True, "details": []},
        ]
        admin = User(id=uuid.uuid4(), username="root", email="root@x.test", name="Root", role=UserRole.super_admin)
        db.add(admin)
        await db.commit()
    current[0] = reviewer
    assert (
        await client.post(f"/api/v1/reviews/{number}/submissions", json={"verdict": "approve", "body": "ok"})
    ).status_code == 201
    gate = (await client.get(f"/api/v1/reviews/{number}/gate")).json()
    assert gate["blockers"] == ["required_checks"] and gate["hard_blockers"] == [] and not gate["ready"]
    blocked = await client.post(f"/api/v1/reviews/{number}/publish", json={})
    assert blocked.status_code == 409 and "required_checks" in blocked.json()["detail"]
    current[0] = admin
    overridden = await client.post(f"/api/v1/reviews/{number}/publish", json={"override_reason": "False positive"})
    assert overridden.status_code == 200, overridden.text


@pytest.mark.asyncio
async def test_advisory_items_do_not_block_an_approved_publish(api):
    """OBS-1443: change requests and unresolved conversations stay visible but never block."""
    client, current, (author, reviewer, _, second), (rid, number), factory = api
    current[0] = second
    assert (
        await client.post(f"/api/v1/reviews/{number}/submissions", json={"verdict": "request_changes", "body": "nit"})
    ).status_code == 201
    current[0] = reviewer
    assert (
        await client.post(f"/api/v1/reviews/{number}/submissions", json={"verdict": "approve", "body": "ok"})
    ).status_code == 201
    gate = (await client.get(f"/api/v1/reviews/{number}/gate")).json()
    assert "changes_requested" in gate["requirements"] and gate["blockers"] == [] and gate["ready"]
    assert (await client.post(f"/api/v1/reviews/{number}/publish", json={})).status_code == 200


@pytest.mark.asyncio
async def test_withdrawn_successor_of_an_approved_mcp_can_be_edited_and_reopened(api):
    client, current, (author, reviewer, _, _), _, factory = api
    async with factory() as db:
        listing = McpListing(
            name="Reopen", namespace="tests", slug="reopen", category="general", owner="author", submitted_by=author.id
        )
        db.add(listing)
        await db.flush()
        stable = McpVersion(
            listing_id=listing.id, version="1.0.0", description="Stable", command="old",
            status=ListingStatus.approved, released_by=author.id, released_at=datetime.now(UTC),
        )  # fmt: skip
        db.add(stable)
        await db.flush()
        listing.latest_version_id = stable.id
        listing_id, stable_id = listing.id, stable.id
        await db.commit()
    current[0] = author
    created = await client.post(
        f"/api/v1/mcps/{listing_id}/versions",
        json={"version": "1.1.0", "description": "Successor", "extra": {"command": "new"}},
    )
    assert created.status_code == 200, created.text
    number, successor_id = created.json()["review_number"], uuid.UUID(created.json()["id"])
    assert (await client.post(f"/api/v1/reviews/{number}/withdraw")).status_code == 200
    # Edit resource targets the withdrawn successor (now a draft), not the approved release.
    detail = (await client.get(f"/api/v1/mcps/{listing_id}")).json()
    assert (detail["version"], detail["status"]) == ("1.1.0", "draft")
    edited = await client.put(f"/api/v1/mcps/{listing_id}/draft", json={"description": "Fixed successor"})
    assert edited.status_code == 200, edited.text
    reopened = await client.post(f"/api/v1/reviews/{number}/reopen", json={"message": "Fixed"})
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["state"] == "open"
    async with factory() as db:
        successor = await db.get(McpVersion, successor_id)
        assert successor.status == ListingStatus.pending and successor.description == "Fixed successor"
        assert (await db.get(McpVersion, stable_id)).description == "Stable"
        assert (await db.get(McpListing, listing_id)).latest_version_id == stable_id
