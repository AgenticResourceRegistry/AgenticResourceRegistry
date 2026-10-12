# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Inbox delivery, lifecycle, idempotency, and read-time visibility.

These run against a real in-memory SQLite database rather than a fake session.
The behaviour under test IS the database's: a unique constraint, an
``IntegrityError``, and a SAVEPOINT that absorbs it. A mock would assert the
code calls what we already know it calls, and would have happily passed on the
session-rollback bug these tests exist to prevent.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models.agent import Agent
from models.base import Base
from models.hook import HookListing
from models.inbox import InboxItem, InboxItemEvent, InboxKind, InboxState
from models.mcp import McpListing
from models.prompt import PromptListing
from models.sandbox import SandboxListing
from models.skill import SkillListing
from models.team import Team, TeamMembership, TeamRole
from models.user import User, UserRole
from services.inbox import delivery, recipients, visibility
from services.inbox.registry import Subject, spec_for

# The listing tables are here because visibility is resolved against the CURRENT
# subject, not the row's write-time snapshot. `visible_filter` emits one
# correlated EXISTS per subject type, so every one of these tables has to exist
# for the predicate to run at all — and an item whose subject_id matches no row
# is correctly invisible, which is why these tests create real listings.
_TABLES = [
    User.__table__,
    Team.__table__,
    TeamMembership.__table__,
    InboxItem.__table__,
    InboxItemEvent.__table__,
    Agent.__table__,
    McpListing.__table__,
    SkillListing.__table__,
    HookListing.__table__,
    PromptListing.__table__,
    SandboxListing.__table__,
]


@pytest_asyncio.fixture()
async def sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=_TABLES)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture()
async def fk_sessions():
    """Sessions with SQLite foreign keys enforced.

    SQLite ignores foreign keys unless the pragma is set per connection, so the
    default fixture above cannot observe ON DELETE CASCADE. Only the cascade
    test needs this; the rest would just pay for the extra event listener.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_fk(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=_TABLES)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _user(db, role=UserRole.user, email=None) -> User:
    user = User(
        id=uuid.uuid4(),
        email=email or f"{uuid.uuid4().hex[:8]}@example.test",
        username=uuid.uuid4().hex[:12],
        name="Test User",
        password_hash="x",
        role=role,
    )
    db.add(user)
    await db.flush()
    return user


def _subject(**kw) -> Subject:
    base = {"type": "mcp", "id": uuid.uuid4(), "name": "demo-server", "version": "1.0.0"}
    base.update(kw)
    return Subject(**base)


async def _listed_subject(db, owner, *, is_private=False, team_id=None, **kw) -> Subject:
    """Insert a real mcp listing and return a Subject pointing at it.

    Use this wherever a test asserts on visibility. ``_subject()`` alone invents
    an id with no listing behind it, and since visibility now reads the listing
    rather than the row's snapshot, such an item is invisible by design.
    """
    listing = McpListing(
        id=uuid.uuid4(),
        name=kw.get("name", "demo-server"),
        namespace=kw.get("namespace") or "ns",
        slug=kw.get("slug") or uuid.uuid4().hex[:12],
        category="tools",
        owner=owner.email,
        is_private=is_private,
        team_id=team_id,
        submitted_by=owner.id,
    )
    db.add(listing)
    await db.flush()
    return _subject(
        id=listing.id,
        is_private=is_private,
        team_id=team_id,
        namespace=listing.namespace,
        slug=listing.slug,
        **{k: v for k, v in kw.items() if k not in ("namespace", "slug")},
    )


# ── Idempotency ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_same_fact_delivered_twice_creates_one_row(sessions):
    async with sessions() as db:
        user = await _user(db)
        subject = _subject()

        first = await delivery.deliver_one(db, kind=InboxKind.review_requested, user_id=user.id, subject=subject)
        second = await delivery.deliver_one(db, kind=InboxKind.review_requested, user_id=user.id, subject=subject)
        await db.commit()

        assert first is not None
        # A repeat is not an error; it is simply already delivered.
        assert second is None
        rows = (await db.execute(select(InboxItem).where(InboxItem.user_id == user.id))).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_collision_does_not_poison_the_rest_of_the_batch(sessions):
    """A duplicate for one recipient must not cost the others their row."""
    async with sessions() as db:
        first_user = await _user(db)
        second_user = await _user(db)
        third_user = await _user(db)
        subject = _subject()

        # Pre-deliver to the middle recipient so the batch hits a collision
        # partway through rather than on its first or last iteration.
        await delivery.deliver_one(db, kind=InboxKind.review_requested, user_id=second_user.id, subject=subject)

        delivered = await delivery.deliver(
            db,
            kind=InboxKind.review_requested,
            recipients=[first_user.id, second_user.id, third_user.id],
            subject=subject,
        )
        await db.commit()

        assert len(delivered) == 2
        for user in (first_user, second_user, third_user):
            rows = (await db.execute(select(InboxItem).where(InboxItem.user_id == user.id))).scalars().all()
            assert len(rows) == 1, f"{user.email} should hold exactly one row"


@pytest.mark.asyncio
async def test_collision_does_not_roll_back_the_originating_work(sessions):
    """The regression that the SAVEPOINT exists for.

    ``deliver`` runs inside the caller's transaction. A recovery that rolled the
    whole session back would discard the caller's own uncommitted writes — an
    approval undoing itself because its notice was a duplicate. Here a team is
    renamed in the same transaction as a colliding delivery, and the rename must
    survive.
    """
    async with sessions() as db:
        user = await _user(db)
        team = Team(id=uuid.uuid4(), name="Original", handle="orig", created_by=user.id)
        db.add(team)
        await db.flush()
        subject = _subject()

        await delivery.deliver_one(db, kind=InboxKind.review_requested, user_id=user.id, subject=subject)
        await db.commit()

        # Originating work + a redelivery of a fact already present.
        team.name = "Renamed"
        duplicate = await delivery.deliver_one(db, kind=InboxKind.review_requested, user_id=user.id, subject=subject)
        await db.commit()

        assert duplicate is None
        refreshed = await db.get(Team, team.id)
        assert refreshed.name == "Renamed", "the collision rolled back the caller's own work"


@pytest.mark.asyncio
async def test_a_newer_version_is_a_new_item_not_a_duplicate(sessions):
    async with sessions() as db:
        user = await _user(db)
        component_id = uuid.uuid4()
        old = await delivery.deliver_one(
            db,
            kind=InboxKind.update_available,
            user_id=user.id,
            subject=_subject(id=component_id, version="1.1.0"),
            context={"current_version": "1.0.0"},
        )
        new = await delivery.deliver_one(
            db,
            kind=InboxKind.update_available,
            user_id=user.id,
            subject=_subject(id=component_id, version="2.0.0"),
            context={"current_version": "1.0.0"},
        )
        assert old is not None and new is not None
        assert old.dedupe_key != new.dedupe_key

        count = await delivery.supersede(db, user_id=user.id, keep=new, others=[old])
        await db.commit()

        assert count == 1
        assert old.state == InboxState.done
        assert new.state == InboxState.open
        events = (await db.execute(select(InboxItemEvent).where(InboxItemEvent.item_id == old.id))).scalars().all()
        assert "superseded" in {e.event for e in events}


@pytest.mark.asyncio
async def test_a_non_dedupe_integrity_error_is_not_swallowed(fk_sessions):
    """Only the dedupe constraint means "already delivered".

    A recipient deleted between resolution and flush trips a foreign key, which
    is the same exception type. Reading that as a duplicate would drop the item
    with no error, which is the one outcome this module must not produce.
    """
    async with fk_sessions() as db:
        ghost = uuid.uuid4()  # never inserted into users
        with pytest.raises(IntegrityError):
            await delivery.deliver_one(db, kind=InboxKind.review_requested, user_id=ghost, subject=_subject())


@pytest.mark.asyncio
async def test_redelivery_reopens_a_resolved_item(sessions):
    """Resubmitting the same version after a rejection must reach the reviewer.

    The dedupe key has no attempt counter, so the second submission collides
    with the first. If the reviewer already resolved their copy, that collision
    is a new fact, not a duplicate.
    """
    async with sessions() as db:
        user = await _user(db)
        subject = _subject()

        first = await delivery.deliver_one(db, kind=InboxKind.review_requested, user_id=user.id, subject=subject)
        delivery.mark_read(db, first)
        delivery.resolve(db, first, state=InboxState.done, actor_id=user.id)
        await db.commit()
        assert first.state == InboxState.done

        again = await delivery.deliver_one(db, kind=InboxKind.review_requested, user_id=user.id, subject=subject)
        await db.commit()

        assert again is not None
        assert again.id == first.id, "reopened in place rather than duplicated"
        assert again.state == InboxState.open
        assert again.read_at is None, "a fact that came back is unread again"
        events = (await db.execute(select(InboxItemEvent).where(InboxItemEvent.item_id == first.id))).scalars().all()
        assert "reopened" in {e.event for e in events}


@pytest.mark.asyncio
async def test_reopened_review_updates_visibility_and_namespace_snapshot(sessions):
    async with sessions() as db:
        reviewer = await _user(db, role=UserRole.reviewer)
        team_id = uuid.uuid4()
        subject = _subject(is_private=True, team_id=team_id, namespace="old-team", slug="draft")
        initial = await delivery.deliver_one(db, kind=InboxKind.review_requested, user_id=reviewer.id, subject=subject)
        delivery.resolve(db, initial, state=InboxState.done, actor_id=reviewer.id)
        await db.commit()

        published = _subject(
            type=subject.type,
            id=subject.id,
            name=subject.name,
            version=subject.version,
            is_private=False,
            team_id=None,
            namespace="new-owner",
            slug="draft",
        )
        again = await delivery.deliver_one(db, kind=InboxKind.review_requested, user_id=reviewer.id, subject=published)
        await db.commit()

        assert again.id == initial.id
        assert again.state == InboxState.open
        assert again.is_private_subject is False
        assert again.team_id is None
        assert again.subject_namespace == "new-owner"


@pytest.mark.asyncio
async def test_dismissed_update_notice_stays_dismissed_on_redelivery(sessions):
    """``observal outdated`` re-reports the same fact on every run.

    A dismissed update notice must stay dismissed for as long as the version it
    names is still the latest — otherwise dismissal means nothing for a
    recurring check. A NEWER version is a new dedupe key and a new item, so it
    still gets through.
    """
    async with sessions() as db:
        user = await _user(db)
        component_id = uuid.uuid4()
        context = {"current_version": "1.0.0"}

        item = await delivery.deliver_one(
            db,
            kind=InboxKind.update_available,
            user_id=user.id,
            subject=_subject(id=component_id, version="2.0.0"),
            context=context,
        )
        delivery.resolve(db, item, state=InboxState.dismissed, actor_id=user.id)
        await db.commit()

        again = await delivery.deliver_one(
            db,
            kind=InboxKind.update_available,
            user_id=user.id,
            subject=_subject(id=component_id, version="2.0.0"),
            context=context,
        )
        await db.commit()

        assert again is None
        assert item.state == InboxState.dismissed, "a recurring report resurrected a dismissed notice"

        newer = await delivery.deliver_one(
            db,
            kind=InboxKind.update_available,
            user_id=user.id,
            subject=_subject(id=component_id, version="3.0.0"),
            context=context,
        )
        await db.commit()
        assert newer is not None and newer.state == InboxState.open


@pytest.mark.asyncio
async def test_open_item_content_refreshes_on_redelivery(sessions):
    """The dedupe key names the fact, not its wording.

    A second rejection of the same version carries a new reason. The recipient
    must see the decision currently on record, and the new text is unseen, so
    the item goes unread again with an ``updated`` history entry.
    """
    async with sessions() as db:
        user = await _user(db)
        subject = _subject()

        item = await delivery.deliver_one(
            db, kind=InboxKind.review_rejected, user_id=user.id, subject=subject, body="reason A"
        )
        delivery.mark_read(db, item)
        await db.commit()

        again = await delivery.deliver_one(
            db, kind=InboxKind.review_rejected, user_id=user.id, subject=subject, body="reason B"
        )
        await db.commit()

        assert again is None, "still not a new delivery"
        assert item.body == "reason B"
        assert item.read_at is None, "unseen content must read as unseen"
        rows = (await db.execute(select(InboxItem).where(InboxItem.user_id == user.id))).scalars().all()
        assert len(rows) == 1
        events = (await db.execute(select(InboxItemEvent).where(InboxItemEvent.item_id == item.id))).scalars().all()
        assert "updated" in {e.event for e in events}

        # Same content again: no event churn, no unread flapping.
        delivery.mark_read(db, item)
        await db.commit()
        await delivery.deliver_one(
            db, kind=InboxKind.review_rejected, user_id=user.id, subject=subject, body="reason B"
        )
        await db.commit()
        assert item.read_at is not None


@pytest.mark.asyncio
async def test_decision_clears_every_reviewers_request_item(sessions):
    """One reviewer's decision resolves the other reviewers' copies too.

    Their open ``review_requested`` items would otherwise keep counting as
    outstanding work and link to a queue that no longer holds the submission.
    """
    from services.inbox import sources

    async with sessions() as db:
        deciding = await _user(db, UserRole.reviewer)
        other = await _user(db, UserRole.reviewer)
        admin = await _user(db, UserRole.admin)
        submitter = await _user(db)

        class _Entity:
            id = uuid.uuid4()
            name = "demo-server"
            namespace = None
            slug = None
            team_id = None
            is_private = False
            versions = []
            latest_version = None

        entity = _Entity()
        delivered = await sources.on_review_requested(
            db, entity, subject_type="mcp", actor_id=submitter.id, version="1.0.0"
        )
        await db.commit()
        assert delivered == 3

        await sources.on_review_decided(
            db,
            entity,
            subject_type="mcp",
            approved=True,
            actor_id=deciding.id,
            version="1.0.0",
            submitter_id=submitter.id,
        )
        await db.commit()

        for reviewer in (deciding, other, admin):
            row = (
                await db.execute(
                    select(InboxItem).where(
                        InboxItem.user_id == reviewer.id, InboxItem.kind == InboxKind.review_requested
                    )
                )
            ).scalar_one()
            assert row.state == InboxState.done, f"{reviewer.email} still holds open work that no longer exists"
            events = (await db.execute(select(InboxItemEvent).where(InboxItemEvent.item_id == row.id))).scalars().all()
            assert "done" in {e.event for e in events}

        # The submitter heard the outcome.
        approval = (
            await db.execute(
                select(InboxItem).where(InboxItem.user_id == submitter.id, InboxItem.kind == InboxKind.review_approved)
            )
        ).scalar_one()
        assert approval.state == InboxState.open


@pytest.mark.asyncio
async def test_withdrawal_closes_all_reviewers_without_faking_an_outcome(sessions):
    from services.inbox import sources

    async with sessions() as db:
        reviewers = [await _user(db, UserRole.reviewer) for _ in range(2)]
        author = await _user(db)

        class _Entity:
            id = uuid.uuid4()
            name = "withdrawn-skill"
            namespace = None
            slug = None
            team_id = None
            is_private = False

        entity = _Entity()
        await sources.on_review_requested(db, entity, subject_type="skill", actor_id=author.id, version="1.2.0")
        await db.commit()
        assert (
            await sources.on_review_withdrawn(db, entity, subject_type="skill", actor_id=author.id, version="1.2.0")
            >= 2
        )
        await db.commit()
        for reviewer in reviewers:
            items = (await db.execute(select(InboxItem).where(InboxItem.user_id == reviewer.id))).scalars().all()
            assert len(items) == 1 and items[0].kind == InboxKind.review_requested
            assert items[0].state == InboxState.done
        assert (await db.execute(select(InboxItem).where(InboxItem.user_id == author.id))).scalars().all() == []


@pytest.mark.asyncio
async def test_redelivering_an_open_item_stays_a_no_op(sessions):
    async with sessions() as db:
        user = await _user(db)
        subject = _subject()
        await delivery.deliver_one(db, kind=InboxKind.review_requested, user_id=user.id, subject=subject)
        await db.commit()

        again = await delivery.deliver_one(db, kind=InboxKind.review_requested, user_id=user.id, subject=subject)
        await db.commit()
        assert again is None
        rows = (await db.execute(select(InboxItem).where(InboxItem.user_id == user.id))).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_supersede_refuses_items_owned_by_another_user(sessions):
    """The user_id argument is a scope, not documentation."""
    async with sessions() as db:
        mine = await _user(db)
        theirs = await _user(db)
        component = uuid.uuid4()

        keep = await delivery.deliver_one(
            db,
            kind=InboxKind.update_available,
            user_id=mine.id,
            subject=_subject(id=component, version="2.0.0"),
            context={"current_version": "1.0.0"},
        )
        other = await delivery.deliver_one(
            db,
            kind=InboxKind.update_available,
            user_id=theirs.id,
            subject=_subject(id=component, version="1.5.0"),
            context={"current_version": "1.0.0"},
        )
        await db.commit()

        count = await delivery.supersede(db, user_id=mine.id, keep=keep, others=[other])
        await db.commit()

        assert count == 0
        assert other.state == InboxState.open, "another user's row must be untouched"


# ── Read is not resolved ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_marking_read_does_not_resolve_or_hide_the_item(sessions):
    """The completion signal: items must not vanish merely because they were read."""
    async with sessions() as db:
        user = await _user(db)
        item = await delivery.deliver_one(db, kind=InboxKind.review_requested, user_id=user.id, subject=_subject())
        await db.commit()

        assert delivery.mark_read(db, item) is True
        await db.commit()

        assert item.read_at is not None
        assert item.state == InboxState.open, "read must not change lifecycle state"
        assert item.resolved_at is None
        assert item.action_required is True

        # Still returned by the default list and the needs-action filter.
        open_rows = (
            (
                await db.execute(
                    select(InboxItem).where(InboxItem.user_id == user.id, InboxItem.state == InboxState.open)
                )
            )
            .scalars()
            .all()
        )
        assert item.id in {row.id for row in open_rows}

        action_rows = (
            (
                await db.execute(
                    select(InboxItem).where(
                        InboxItem.user_id == user.id,
                        InboxItem.action_required == True,  # noqa: E712
                        InboxItem.state == InboxState.open,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert item.id in {row.id for row in action_rows}


@pytest.mark.asyncio
async def test_read_is_reversible(sessions):
    async with sessions() as db:
        user = await _user(db)
        item = await delivery.deliver_one(db, kind=InboxKind.review_requested, user_id=user.id, subject=_subject())
        delivery.mark_read(db, item)
        assert delivery.mark_unread(db, item) is True
        await db.commit()
        assert item.read_at is None
        # A second call is a no-op rather than a spurious history entry.
        assert delivery.mark_unread(db, item) is False


# ── History ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_history_appends_for_every_transition(sessions):
    async with sessions() as db:
        user = await _user(db)
        item = await delivery.deliver_one(db, kind=InboxKind.review_requested, user_id=user.id, subject=_subject())
        delivery.mark_read(db, item, actor_id=user.id)
        delivery.resolve(db, item, state=InboxState.done, actor_id=user.id)
        delivery.resolve(db, item, state=InboxState.open, actor_id=user.id)
        delivery.resolve(db, item, state=InboxState.dismissed, actor_id=user.id)
        await db.commit()

        events = (
            (
                await db.execute(
                    select(InboxItemEvent)
                    .where(InboxItemEvent.item_id == item.id)
                    .order_by(InboxItemEvent.created_at, InboxItemEvent.id)
                )
            )
            .scalars()
            .all()
        )
        assert [e.event for e in events] == ["created", "read", "done", "reopened", "dismissed"]


@pytest.mark.asyncio
async def test_resolving_stamps_and_clears_resolved_at(sessions):
    async with sessions() as db:
        user = await _user(db)
        item = await delivery.deliver_one(db, kind=InboxKind.review_requested, user_id=user.id, subject=_subject())
        delivery.resolve(db, item, state=InboxState.done)
        assert item.resolved_at is not None
        delivery.resolve(db, item, state=InboxState.open)
        assert item.resolved_at is None
        await db.commit()


# ── Recipients ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_review_notifications_follow_item_and_team_visibility(sessions):
    async with sessions() as db:
        admin = await _user(db, UserRole.admin)
        global_reviewer = await _user(db, UserRole.reviewer)
        team_owner = await _user(db)
        team_reviewer = await _user(db)
        team_member = await _user(db)
        other_owner = await _user(db)

        team = Team(id=uuid.uuid4(), name="Team", handle="team", created_by=team_owner.id)
        other = Team(id=uuid.uuid4(), name="Other", handle="other", created_by=other_owner.id)
        db.add_all([team, other])
        await db.flush()
        db.add_all(
            [
                TeamMembership(team_id=team.id, user_id=team_owner.id, role=TeamRole.owner),
                TeamMembership(team_id=team.id, user_id=team_reviewer.id, role=TeamRole.reviewer),
                TeamMembership(team_id=team.id, user_id=team_member.id, role=TeamRole.member),
                TeamMembership(team_id=other.id, user_id=other_owner.id, role=TeamRole.owner),
            ]
        )
        await db.commit()

        class _Entity:
            def __init__(self, is_private, team_id):
                self.is_private = is_private
                self.team_id = team_id

        cases = [
            ("public", _Entity(False, None), {admin.id, global_reviewer.id}),
            (
                "public-team",
                _Entity(False, team.id),
                {admin.id, global_reviewer.id, team_owner.id, team_reviewer.id},
            ),
            ("team-private", _Entity(True, team.id), {team_owner.id, team_reviewer.id}),
            ("personal-private", _Entity(True, None), {admin.id}),
        ]
        for label, entity, expected in cases:
            actual = set(await recipients.reviewers_for(db, entity))
            assert actual == expected, label


@pytest.mark.asyncio
async def test_team_owners_receive_team_scoped_items(sessions):
    async with sessions() as db:
        owner = await _user(db)
        member = await _user(db)
        team = Team(id=uuid.uuid4(), name="T", handle="t", created_by=owner.id)
        db.add(team)
        await db.flush()
        db.add(TeamMembership(team_id=team.id, user_id=owner.id, role=TeamRole.owner))
        db.add(TeamMembership(team_id=team.id, user_id=member.id, role=TeamRole.member))
        await db.commit()

        got = await recipients.team_owners(db, team.id)
        assert got == [owner.id]


@pytest.mark.asyncio
async def test_an_admin_who_is_also_a_team_reviewer_is_listed_once(sessions):
    async with sessions() as db:
        admin = await _user(db, UserRole.admin)
        team = Team(id=uuid.uuid4(), name="T", handle="t", created_by=admin.id)
        db.add(team)
        await db.flush()
        db.add(TeamMembership(team_id=team.id, user_id=admin.id, role=TeamRole.reviewer))
        await db.commit()

        class _Entity:
            is_private = True
            team_id = team.id

        got = await recipients.reviewers_for(db, _Entity())
        assert got.count(admin.id) == 1


@pytest.mark.asyncio
async def test_actor_is_not_notified_of_their_own_action(sessions):
    async with sessions() as db:
        actor = await _user(db)
        other = await _user(db)
        delivered = await delivery.deliver(
            db,
            kind=InboxKind.review_requested,
            recipients=[actor.id, other.id],
            subject=_subject(),
            actor_id=actor.id,
        )
        await db.commit()
        assert [item.user_id for item in delivered] == [other.id]


# ── Read-time visibility ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_removed_member_stops_seeing_a_private_subject(sessions):
    async with sessions() as db:
        owner = await _user(db)
        member = await _user(db)
        team = Team(id=uuid.uuid4(), name="T", handle="t", created_by=owner.id)
        db.add(team)
        await db.flush()
        membership = TeamMembership(team_id=team.id, user_id=member.id, role=TeamRole.reviewer)
        db.add(membership)
        await db.commit()

        item = await delivery.deliver_one(
            db,
            kind=InboxKind.review_requested,
            user_id=member.id,
            subject=await _listed_subject(db, owner, team_id=team.id, is_private=True),
        )
        await db.commit()

        assert await visibility.visible_to(db, item, member) is True

        # Membership revoked after the item was already delivered.
        await db.delete(membership)
        await db.commit()

        assert await visibility.visible_to(db, item, member) is False


@pytest.mark.asyncio
async def test_public_subjects_stay_visible(sessions):
    async with sessions() as db:
        user = await _user(db)
        item = await delivery.deliver_one(
            db,
            kind=InboxKind.review_requested,
            user_id=user.id,
            subject=await _listed_subject(db, user),
        )
        await db.commit()
        assert await visibility.visible_to(db, item, user) is True


@pytest.mark.asyncio
async def test_a_decision_stays_readable_after_losing_membership(sessions):
    """A decision reports what happened to work the recipient wrote.

    Their own submission's outcome does not become unreadable because the
    listing moved somewhere they can no longer follow, so these kinds opt out of
    the membership re-check.
    """
    async with sessions() as db:
        owner = await _user(db)
        submitter = await _user(db)
        team = Team(id=uuid.uuid4(), name="T", handle="t", created_by=owner.id)
        db.add(team)
        await db.commit()

        item = await delivery.deliver_one(
            db,
            kind=InboxKind.review_rejected,
            user_id=submitter.id,
            subject=_subject(team_id=team.id, is_private=True),
        )
        await db.commit()

        assert spec_for(InboxKind.review_rejected).recheck_visibility is False
        assert await visibility.visible_to(db, item, submitter) is True


@pytest.mark.asyncio
async def test_admins_keep_seeing_private_subjects(sessions):
    async with sessions() as db:
        admin = await _user(db, UserRole.admin)
        team = Team(id=uuid.uuid4(), name="T", handle="t", created_by=admin.id)
        db.add(team)
        await db.commit()

        item = await delivery.deliver_one(
            db,
            kind=InboxKind.review_requested,
            user_id=admin.id,
            subject=_subject(team_id=team.id, is_private=True),
        )
        await db.commit()
        assert await visibility.visible_to(db, item, admin) is True


@pytest.mark.asyncio
async def test_filter_visible_omits_rather_than_redacts(sessions):
    async with sessions() as db:
        owner = await _user(db)
        member = await _user(db)
        team = Team(id=uuid.uuid4(), name="T", handle="t", created_by=owner.id)
        db.add(team)
        await db.commit()

        hidden = await delivery.deliver_one(
            db,
            kind=InboxKind.review_requested,
            user_id=member.id,
            subject=await _listed_subject(db, owner, team_id=team.id, is_private=True),
        )
        shown = await delivery.deliver_one(
            db,
            kind=InboxKind.review_requested,
            user_id=member.id,
            subject=await _listed_subject(db, owner),
        )
        await db.commit()

        kept = await visibility.filter_visible(db, [hidden, shown], member)
        assert [item.id for item in kept] == [shown.id]


# ── Registry ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_every_kind_has_a_spec():
    for kind in InboxKind:
        spec = spec_for(kind)
        subject = _subject()
        assert spec.title(subject, {})
        assert spec.dedupe(subject, {})


@pytest.mark.asyncio
async def test_titles_and_keys_are_bounded(sessions):
    """A long component name must not overflow the column and 500 the request."""
    async with sessions() as db:
        user = await _user(db)
        item = await delivery.deliver_one(
            db,
            kind=InboxKind.review_requested,
            user_id=user.id,
            subject=_subject(name="x" * 4000),
        )
        await db.commit()
        assert len(item.title) <= 255
        assert len(item.dedupe_key) <= 255


@pytest.mark.asyncio
async def test_component_links_name_their_type(sessions):
    """/components/$componentId defaults ?type= to "mcps".

    Omitting it made a skill, hook, prompt or sandbox open as an MCP, so the
    page loaded the wrong record. Every component link names its own type.
    """
    expected = {
        "mcp": "mcps",
        "skill": "skills",
        "hook": "hooks",
        "prompt": "prompts",
        "sandbox": "sandboxes",
    }
    async with sessions() as db:
        user = await _user(db)
        for singular, plural in expected.items():
            item = await delivery.deliver_one(
                db,
                kind=InboxKind.review_approved,
                user_id=user.id,
                subject=_subject(type=singular, id=uuid.uuid4()),
            )
            assert item.action_url.endswith(f"?type={plural}"), f"{singular} -> {item.action_url}"
        await db.commit()


@pytest.mark.asyncio
async def test_registry_actions_use_canonical_urls(sessions):
    expected = {
        "mcp": "mcps",
        "skill": "skills",
        "hook": "hooks",
        "prompt": "prompts",
        "sandbox": "sandboxes",
    }
    async with sessions() as db:
        user = await _user(db)
        agent = await delivery.deliver_one(
            db,
            kind=InboxKind.review_approved,
            user_id=user.id,
            subject=_subject(type="agent", namespace="acme", slug="reviewer"),
        )
        assert agent.action_url == "/agents/acme/reviewer"

        for singular, plural in expected.items():
            item = await delivery.deliver_one(
                db,
                kind=InboxKind.review_approved,
                user_id=user.id,
                subject=_subject(type=singular, namespace="acme", slug=f"{singular}-tool"),
            )
            assert item.action_url == f"/components/{plural}/acme/{singular}-tool"
        await db.commit()


@pytest.mark.asyncio
async def test_registry_actions_keep_uuid_fallback_for_legacy_namespaces(sessions):
    async with sessions() as db:
        user = await _user(db)
        subject = _subject(type="skill", namespace="Legacy_User", slug="helper")
        item = await delivery.deliver_one(
            db,
            kind=InboxKind.review_approved,
            user_id=user.id,
            subject=subject,
        )
        await db.commit()
        assert item.action_url == f"/components/{subject.id}?type=skills"


@pytest.mark.asyncio
async def test_review_links_open_the_tab_holding_the_item(sessions):
    """The review queue opens on the agents tab by default.

    A component review that linked to a bare /review dropped the reviewer on a
    tab that does not contain the submission they were sent to look at.
    """
    async with sessions() as db:
        user = await _user(db)
        agent_item = await delivery.deliver_one(
            db,
            kind=InboxKind.review_requested,
            user_id=user.id,
            subject=_subject(type="agent", id=uuid.uuid4()),
        )
        mcp_item = await delivery.deliver_one(
            db,
            kind=InboxKind.review_requested,
            user_id=user.id,
            subject=_subject(type="mcp", id=uuid.uuid4()),
        )
        await db.commit()
        assert agent_item.action_url == "/review?tab=agents"
        assert mcp_item.action_url == "/review?tab=components"


@pytest.mark.asyncio
async def test_every_action_url_is_a_same_origin_path(sessions):
    """No kind may emit something the web app would treat as external."""
    async with sessions() as db:
        user = await _user(db)
        for kind in InboxKind:
            item = await delivery.deliver_one(
                db,
                kind=kind,
                user_id=user.id,
                subject=_subject(id=uuid.uuid4(), handle="platform"),
                context={"notice_id": uuid.uuid4().hex, "title": "n"},
            )
            url = item.action_url
            if url is None:
                continue
            assert url.startswith("/"), f"{kind.value} -> {url}"
            assert not url.startswith("//"), f"{kind.value} is protocol-relative -> {url}"
        await db.commit()


@pytest.mark.parametrize(
    ("item_type", "expected_command"),
    [
        ("agent", "observal agent pull acme/widget --harness claude-code --no-prompt"),
        ("mcp", "observal registry mcp install acme/widget --harness claude-code --no-prompt"),
        ("skill", "observal registry skill install acme/widget --harness claude-code"),
        ("hook", "observal registry hook install acme/widget --harness claude-code"),
    ],
)
@pytest.mark.asyncio
async def test_update_available_carries_a_type_specific_upgrade_command(sessions, item_type, expected_command):
    async with sessions() as db:
        user = await _user(db)
        item = await delivery.deliver_one(
            db,
            kind=InboxKind.update_available,
            user_id=user.id,
            subject=_subject(type=item_type, namespace="acme", slug="widget", version="2.0.0"),
            context={"current_version": "1.0.0", "harness": "claude-code"},
        )
        await db.commit()
        # Shown, never run: the command is the whole point of an actionable feed.
        assert item.action_command == expected_command
        assert "1.0.0" in item.title and "2.0.0" in item.title


@pytest.mark.parametrize("context", [{}, {"harness": "unsafe; harness"}])
def test_update_available_omits_invalid_upgrade_commands(context):
    spec = spec_for(InboxKind.update_available)

    assert spec.command(_subject(type="agent", namespace="acme", slug="widget"), context) is None


# ── Retention ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_open_items_are_never_purged_but_resolved_ones_age_out(sessions):
    """Deleting unactioned work silently is the failure this feature prevents."""
    from sqlalchemy import delete

    async with sessions() as db:
        user = await _user(db)
        long_ago = datetime.now(UTC) - timedelta(days=400)

        stale_open = await delivery.deliver_one(
            db, kind=InboxKind.review_requested, user_id=user.id, subject=_subject(id=uuid.uuid4())
        )
        stale_done = await delivery.deliver_one(
            db, kind=InboxKind.review_requested, user_id=user.id, subject=_subject(id=uuid.uuid4())
        )
        stale_open.created_at = long_ago
        stale_done.created_at = long_ago
        stale_done.state = InboxState.done
        stale_done.resolved_at = long_ago
        await db.commit()

        # The predicate purge_inbox_items uses.
        cutoff = datetime.now(UTC) - timedelta(days=90)
        doomed = (
            (
                await db.execute(
                    select(InboxItem.id).where(
                        InboxItem.state.in_([InboxState.done, InboxState.dismissed]),
                        InboxItem.resolved_at.is_not(None),
                        InboxItem.resolved_at < cutoff,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert set(doomed) == {stale_done.id}

        await db.execute(delete(InboxItem).where(InboxItem.id.in_(list(doomed))))
        await db.commit()

        surviving = (await db.execute(select(InboxItem.id).where(InboxItem.user_id == user.id))).scalars().all()
        assert set(surviving) == {stale_open.id}


@pytest.mark.asyncio
async def test_deleting_an_item_cascades_its_history(fk_sessions):
    """Deleting an item takes its history with it, via ON DELETE CASCADE.

    Uses the foreign-key-enforcing fixture and deletes ONLY the parent row. An
    earlier version of this test deleted the event rows itself first, which made
    the assertion pass regardless of whether the cascade existed.
    """
    from sqlalchemy import delete

    async with fk_sessions() as db:
        user = await _user(db)
        item = await delivery.deliver_one(db, kind=InboxKind.review_requested, user_id=user.id, subject=_subject())
        delivery.mark_read(db, item)
        await db.commit()
        item_id = item.id

        assert (await db.execute(select(InboxItemEvent).where(InboxItemEvent.item_id == item_id))).scalars().all(), (
            "history should exist before the delete"
        )

        await db.execute(delete(InboxItem).where(InboxItem.id == item_id))
        await db.commit()

        left = (await db.execute(select(InboxItemEvent).where(InboxItemEvent.item_id == item_id))).scalars().all()
        assert left == []


# ── List query surface: search, sort, facets ───────────────────────
#
# These call the route coroutines directly, which means FastAPI's dependency
# resolution never runs and every parameter whose default is a ``Query(...)``
# marker arrives as the marker object rather than its value. An unresolved
# marker reaches SQLAlchemy as a bind parameter and fails there, so the two
# helpers below supply the full argument set the way a real request would.


async def _list(db, user, **overrides):
    from api.routes.inbox import list_inbox

    params: dict = {
        "state": None,
        "kind": None,
        "action_required": None,
        "unread": None,
        "subject_type": None,
        "q": None,
        "sort": "newest",
        "page": 1,
        "page_size": 25,
    }
    params.update(overrides)
    return await list_inbox(**params, db=db, current_user=user)


async def _counts(db, user, *, facets=False, facet_state=InboxState.open):
    from api.routes.inbox import inbox_count

    return await inbox_count(facets=facets, facet_state=facet_state, db=db, current_user=user)


async def _deliver_named(db, user, *, namespace, slug, kind=InboxKind.review_requested, subject_type="mcp"):
    subject = await _listed_subject(db, user, name=slug, namespace=namespace, slug=slug)
    if subject_type != "mcp":
        subject = _subject(type=subject_type, id=subject.id, name=slug, namespace=namespace, slug=slug)
    return await delivery.deliver_one(db, kind=kind, user_id=user.id, subject=subject)


@pytest.mark.asyncio
async def test_search_matches_title_namespace_and_slug(sessions):
    """Search is server-side so it can see past the page the browser holds."""
    async with sessions() as db:
        user = await _user(db)
        await _deliver_named(db, user, namespace="super", slug="slack-notifier")
        await _deliver_named(db, user, namespace="kaushikkkkk", slug="test-generator")
        await db.commit()

        by_slug = await _list(db, user, q="slack")
        assert [i.subject_slug for i in by_slug.items] == ["slack-notifier"]
        # `total` has to describe the filtered set, not the unfiltered one, or
        # the pager below the list counts rows the list does not contain.
        assert by_slug.total == 1

        by_namespace = await _list(db, user, q="kaushik")
        assert [i.subject_slug for i in by_namespace.items] == ["test-generator"]

        # The title is built by the kind spec and carries the subject name.
        assert (await _list(db, user, q="Review requested")).total == 2


@pytest.mark.asyncio
async def test_search_treats_like_wildcards_as_literals(sessions):
    """An unescaped % or _ in the box would silently match everything."""
    async with sessions() as db:
        user = await _user(db)
        await _deliver_named(db, user, namespace="super", slug="slack-notifier")
        percent = await _deliver_named(db, user, namespace="super", slug="coverage")
        percent.title = "100% coverage"
        await db.commit()

        assert [i.title for i in (await _list(db, user, q="%")).items] == ["100% coverage"]
        # `_` is the single-character wildcard, and neither subject contains a
        # literal underscore, so a leaked wildcard would match both rows here.
        assert (await _list(db, user, q="_")).total == 0


@pytest.mark.asyncio
async def test_sort_reverses_the_page(sessions):
    async with sessions() as db:
        user = await _user(db)
        old = await _deliver_named(db, user, namespace="super", slug="first")
        new = await _deliver_named(db, user, namespace="super", slug="second")
        old.created_at = datetime.now(UTC) - timedelta(days=2)
        new.created_at = datetime.now(UTC)
        await db.commit()

        assert [i.subject_slug for i in (await _list(db, user)).items] == ["second", "first"]
        assert [i.subject_slug for i in (await _list(db, user, sort="oldest")).items] == ["first", "second"]


@pytest.mark.asyncio
async def test_counts_omit_facets_unless_asked(sessions):
    """The nav badge polls this on a timer; it must not pay for the breakdown."""
    async with sessions() as db:
        user = await _user(db)
        await _deliver_named(db, user, namespace="super", slug="slack-notifier")
        await db.commit()

        plain = await _counts(db, user)
        assert plain.open == 1
        assert plain.by_kind == {} and plain.by_subject_type == {}

        detailed = await _counts(db, user, facets=True)
        assert detailed.by_kind == {"review_requested": 1}
        assert detailed.by_subject_type == {"mcp": 1}


@pytest.mark.asyncio
async def test_facets_describe_the_bucket_they_are_scoped_to(sessions):
    """Sidebar numbers must add up to the list beside them, not a different set."""
    async with sessions() as db:
        user = await _user(db)
        still_open = await _deliver_named(db, user, namespace="super", slug="open-one")
        finished = await _deliver_named(
            db, user, namespace="super", slug="done-one", kind=InboxKind.update_available, subject_type="skill"
        )
        delivery.resolve(db, finished, state=InboxState.done)
        await db.commit()

        opened = await _counts(db, user, facets=True, facet_state=InboxState.open)
        assert opened.by_kind == {"review_requested": 1}
        assert opened.by_subject_type == {"mcp": 1}

        done = await _counts(db, user, facets=True, facet_state=InboxState.done)
        assert done.by_kind == {"update_available": 1}
        assert done.by_subject_type == {"skill": 1}

        # Lifecycle totals are unscoped: they are what the bucket rows count.
        assert (opened.open, opened.done, opened.dismissed) == (1, 1, 0)
        assert still_open.state is InboxState.open


@pytest.mark.asyncio
async def test_subject_turning_private_hides_an_already_delivered_item(sessions):
    """The leak that reading the listing instead of the snapshot closes.

    The item is written while the listing is public, so its
    ``is_private_subject`` snapshot says False forever. Trusting that snapshot
    would keep the item readable by a non-member after the listing was made
    private, disclosing its name and version.
    """
    async with sessions() as db:
        owner = await _user(db)
        outsider = await _user(db)
        team = Team(id=uuid.uuid4(), name="T", handle="t", created_by=owner.id)
        db.add(team)
        await db.flush()

        subject = await _listed_subject(db, owner)
        item = await delivery.deliver_one(db, kind=InboxKind.review_requested, user_id=outsider.id, subject=subject)
        await db.commit()

        assert item.is_private_subject is False
        assert await visibility.visible_to(db, item, outsider) is True

        # The listing is made team-private after the fact. Updated by statement
        # rather than by loading the entity: McpListing has selectin
        # relationships to version tables this fixture does not create.
        await db.execute(update(McpListing).where(McpListing.id == subject.id).values(is_private=True, team_id=team.id))
        await db.commit()

        # The row still claims a public subject; the listing is what decides.
        assert item.is_private_subject is False
        assert await visibility.visible_to(db, item, outsider) is False

        # And the SQL predicate has to agree, or the count would still show it.
        visible_ids = (
            (
                await db.execute(
                    select(InboxItem.id).where(InboxItem.user_id == outsider.id, visibility.visible_filter(outsider))
                )
            )
            .scalars()
            .all()
        )
        assert item.id not in visible_ids


@pytest.mark.asyncio
async def test_a_member_still_sees_a_subject_that_turned_private(sessions):
    """Turning private must not hide it from people who belong to the team."""
    async with sessions() as db:
        owner = await _user(db)
        member = await _user(db)
        team = Team(id=uuid.uuid4(), name="T", handle="t", created_by=owner.id)
        db.add(team)
        await db.flush()
        db.add(TeamMembership(team_id=team.id, user_id=member.id, role=TeamRole.reviewer))

        subject = await _listed_subject(db, owner)
        item = await delivery.deliver_one(db, kind=InboxKind.review_requested, user_id=member.id, subject=subject)
        await db.commit()

        await db.execute(update(McpListing).where(McpListing.id == subject.id).values(is_private=True, team_id=team.id))
        await db.commit()

        assert await visibility.visible_to(db, item, member) is True
        visible_ids = (
            (
                await db.execute(
                    select(InboxItem.id).where(InboxItem.user_id == member.id, visibility.visible_filter(member))
                )
            )
            .scalars()
            .all()
        )
        assert item.id in visible_ids
