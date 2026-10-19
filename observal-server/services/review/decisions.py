# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Transactional review rules. Not called by legacy routes until the API/UI cutover."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func, select, update

from api.routes._skill_lock import should_promote_skill_version
from models.agent import Agent, AgentStatus, AgentVersion
from models.agent_component import AgentComponent
from models.mcp import ListingStatus
from models.review import (
    Review,
    ReviewComment,
    ReviewEvent,
    ReviewRevision,
    ReviewState,
    ReviewSubmission,
    ReviewThread,
)
from models.skill import SkillListing, SkillVersion
from models.user import UserRole
from services import dynamic_settings as _ds
from services.agent_lock import (
    INSTALLABLE_STATUSES,
    LISTING_MODELS,
    VERSION_MODELS,
    latest_release,
    lock_agent_version,
)
from services.editing_lock import is_actively_editing, release_edit_lock
from services.review import check_config, ci
from services.review.anchors import reanchor
from services.review.checks import pinned_component_blockers, snapshot_checks
from services.review.files import render_files
from services.review.policy import ApprovalPolicy, policy_for
from services.review.requests import ensure_request_threads, reconcile_request_threads, review_decisions
from services.skill_bundle import needs_bundle_delivery
from services.teamspace import can_review, review_scope
from services.versioning import parse_semver

_PRERELEASE_RE = re.compile(r"^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?$")


# Revision note of a draft save pushed automatically (services.review.cutover.push_draft_edit).
# Such a revision shows reviewers the change but is not the author's resubmission.
DRAFT_EDIT_MESSAGE = "Draft updated after submission"
# Revision note of a revision pushed by MCP validation (services.mcp_validator).
MCP_VALIDATION_MESSAGE = "Updated by MCP validation"
# Revisions the system pushed; none of them is the author's resubmission.
SYSTEM_REVISION_MESSAGES = frozenset({DRAFT_EDIT_MESSAGE, MCP_VALIDATION_MESSAGE})

# Unresolved conversations and abandoned edit locks are advisory; an explicit
# change request remains a publication blocker until withdrawn or approved.
ADVISORY_REQUIREMENTS = frozenset({"unresolved_threads", "edit_lock"})
# Integrity rules no publish path skips, including a super admin override: content changed
# without a new revision, an uninstallable or rollout-gated skill folder, a moved approved
# base, and an agent pinning components that are not published.
HARD_REQUIREMENTS = frozenset(
    {"unsubmitted_changes", "skill_folder_rollout", "skill_folder", "approved_base_changed", "pinned_components"}
)


def _now():
    return datetime.now(UTC)


def _event(db, review, kind, actor_id=None, *, at=None, **payload):
    db.add(ReviewEvent(review_id=review.id, kind=kind, actor_id=actor_id, created_at=at or _now(), payload=payload))


def _hash(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def _target(db, review):
    # populate_existing discards unflushed changes, so persist the caller's pending edits first.
    await db.flush()
    if review.subject_type == "agent":
        version = await db.get(AgentVersion, review.version_id, populate_existing=True)
        subject = await db.get(Agent, review.subject_id, populate_existing=True)
    else:
        model = VERSION_MODELS[review.subject_type]
        version = await db.get(model, review.version_id, populate_existing=True)
        subject = await db.get(LISTING_MODELS[review.subject_type], review.subject_id, populate_existing=True)
    if (
        not version
        or not subject
        or (version.agent_id if review.subject_type == "agent" else version.listing_id) != subject.id
    ):
        raise HTTPException(404, "Review subject no longer exists")
    return subject, version


def _own_work(subject, version, actor_id) -> bool:
    owner_id = getattr(subject, "created_by", None) or getattr(subject, "submitted_by", None)
    return actor_id in (version.released_by, owner_id) or str(actor_id) in (subject.co_authors or [])


async def _require_reviewer(db, review, user):
    subject, version = await _target(db, review)
    scope = await review_scope(db, user)
    if not can_review(subject, scope):
        raise HTTPException(403, "Not a reviewer in scope")
    # A skill made public after team review needs a global decision, not a team one.
    if getattr(version, "requires_global_review", False) and not scope.is_global_reviewer:
        raise HTTPException(403, "This release needs a global reviewer")


async def _lock_review(db, review):
    # Skill edits, installs and visibility changes lock listing, then version, then
    # review. Take the same order here so concurrent actions cannot deadlock.
    if review.subject_type == "skill":
        await db.execute(select(SkillListing.id).where(SkillListing.id == review.subject_id).with_for_update())
        await db.execute(select(SkillVersion.id).where(SkillVersion.id == review.version_id).with_for_update())
    elif review.subject_type == "agent":
        # Skill publication locks the skill listing, then re-syncs dependent agent
        # reviews. Take the pinned skill listings first (share) so both orders agree.
        skill_ids = (
            await db.scalars(
                select(AgentComponent.component_id)
                .where(AgentComponent.agent_version_id == review.version_id, AgentComponent.component_type == "skill")
                .order_by(AgentComponent.component_id)
            )
        ).all()
        if skill_ids:
            await db.execute(
                select(SkillListing.id)
                .where(SkillListing.id.in_(skill_ids))
                .order_by(SkillListing.id)
                .with_for_update(read=True)
            )
    # Re-read under the row lock: a caller that waited here holds a stale copy of the state.
    await db.flush()
    await db.refresh(review, with_for_update=True)


async def _head(db, review):
    head = await db.get(ReviewRevision, review.head_revision_id)
    if head is None:
        raise HTTPException(409, "Review has no head revision")
    return head


async def _base_files(db, subject_type, version, base_version_id) -> dict:
    """Render the frozen review base; checks compare against it, never the previous revision."""
    if base_version_id is None:
        return {}
    base = await db.get(type(version), base_version_id)
    return await render_files(subject_type, base, db) if base is not None else {}


async def open_or_push(
    db,
    subject_type: str,
    subject,
    version,
    actor_id,
    *,
    message=None,
    backfill=False,
    policy=None,
    visibility_requeue=False,
):
    """Create a review or push a revision in the caller's transaction."""
    if subject_type not in (*VERSION_MODELS, "agent"):
        raise ValueError("Unsupported review subject")
    review = (
        await db.execute(
            select(Review).where(Review.subject_type == subject_type, Review.version_id == version.id).with_for_update()
        )
    ).scalar_one_or_none()
    if review and backfill:
        return review  # Never rewrite an active review from a stale legacy version.
    if review and review.state == ReviewState.published and not visibility_requeue:
        raise HTTPException(409, "Published versions require a new version and review")
    if visibility_requeue and review and review.state != ReviewState.published:
        raise HTTPException(409, "Only published reviews can be requeued for public visibility")
    files = await render_files(subject_type, version, db)
    digest = _hash(files)
    if review:
        if review.state == ReviewState.closed and review.closed_reason == "superseded":
            raise HTTPException(409, "Superseded reviews cannot be reopened; release a new version")
        head = await _head(db, review)
        base_files = await _base_files(db, subject_type, version, review.base_version_id)
        if (
            head.content_hash == digest
            and not visibility_requeue
            and not (review.state == ReviewState.closed and review.closed_reason == "withdrawn")
        ):
            raise HTTPException(
                409,
                f"Already under review as #{review.number} with no changes since revision {head.number}. "
                "Edit the resource first; saving it adds a new revision to the same review.",
            )
        if visibility_requeue:
            # Team-private approvals must not count toward a public decision,
            # including when policy permits approvals from earlier revisions.
            await db.execute(
                update(ReviewSubmission)
                .where(ReviewSubmission.review_id == review.id, ReviewSubmission.state == "submitted")
                .values(
                    state="dismissed",
                    dismissed_by=actor_id,
                    dismissed_at=_now(),
                    dismissed_reason="Visibility changed to public",
                )
            )
            await db.flush()
            await reconcile_request_threads(db, review, actor_id=actor_id)
            from models.inbox import InboxKind
            from services.review.notifications import resolve_review_work

            await resolve_review_work(db, review, actor_id, kinds=(InboxKind.change_requested,))
            review.published_at = None
            review.published_by = None
            review.is_private = subject.is_private
            review.team_id = subject.team_id
            _event(db, review, "visibility_requeued", actor_id)
        next_number = head.number + 1
        threads = (await db.execute(select(ReviewThread).where(ReviewThread.review_id == review.id))).scalars().all()
        moved, outdated = reanchor(threads, head.files, files)
        was_closed = review.state == ReviewState.closed
        review.state = ReviewState.open
        review.closed_reason = None
        review.closed_at = None
        review.closed_by = None
        _event(
            db,
            review,
            "reopened" if was_closed else "revision_pushed",
            actor_id,
            revision=next_number,
            moved=moved,
            outdated=outdated,
        )
    else:
        latest = await db.get(type(version), subject.latest_version_id) if subject.latest_version_id else None
        if latest is None or latest.status not in (
            ListingStatus.approved,
            AgentStatus.approved,
            ListingStatus.archived,
        ):
            siblings = (
                (
                    await db.execute(
                        select(type(version)).where(
                            (AgentVersion.agent_id if subject_type == "agent" else type(version).listing_id)
                            == subject.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            latest = latest_release(siblings)
        base_files = await _base_files(db, subject_type, version, latest.id if latest else None)
        review = Review(
            subject_type=subject_type,
            subject_id=subject.id,
            version_id=version.id,
            version=version.version,
            base_version_id=latest.id if latest else None,
            state=ReviewState.open,
            title=f"{subject.name} v{version.version}",
            body=getattr(version, "changelog", None) or "",
            opened_by=actor_id if backfill or visibility_requeue else version.released_by,
            opened_at=version.released_at if backfill else _now(),
            team_id=subject.team_id,
            is_private=subject.is_private,
        )
        if db.bind.dialect.name == "sqlite":
            # SQLite test databases have no sequences; PostgreSQL always uses review_number_seq.
            review.number = (await db.scalar(select(func.coalesce(func.max(Review.number), 0)))) + 1
        db.add(review)
        await db.flush()
        # PostgreSQL assigns the non-PK sequence value, but SQLAlchemy may
        # leave it unloaded after INSERT; async lazy loading then fails in
        # notifications and the create response.
        await db.refresh(review, attribute_names=["number"])
        next_number = 1
        _event(db, review, "opened", actor_id, at=review.opened_at, revision=1)
    revision = ReviewRevision(
        review_id=review.id,
        number=next_number,
        files=files,
        content_hash=digest,
        checks=snapshot_checks(files, version, subject_type=subject_type, base_files=base_files),
        message=message,
        created_by=actor_id,
    )
    if not backfill:
        await ci.attach_queued(revision, subject, subject_type)
    db.add(revision)
    await db.flush()
    review.head_revision_id = revision.id
    # Historical backfill never changes version status.
    if not backfill:
        version.status = AgentStatus.pending if subject_type == "agent" else ListingStatus.pending
        if next_number > 1:
            # A withdrawn review may reopen a request that was nonblocking while
            # closed. Reopen its conversation before evaluating the new gate.
            await reconcile_request_threads(db, review, actor_id=actor_id)
            # Approvals that still count under the policy can make the new head publishable.
            await sync_state(db, review, actor_id=actor_id, policy=policy)
    return review


@dataclass(frozen=True)
class Gate:
    """Publication state of a review.

    ``requirements`` lists every open item. ``ready`` means a person may publish: every open
    item is advisory (``ADVISORY_REQUIREMENTS``). ``hard`` lists the items not even a super
    admin override skips (``HARD_REQUIREMENTS``); other blockers (approvals, required checks,
    MCP validation) can be overridden by a super admin with a reason. ``clear`` is True when
    nothing is open; unattended auto-publish needs it.
    """

    ready: bool
    approvals: int
    required: int
    policy_source: str
    requirements: tuple[str, ...]
    outstanding_requests: int
    # Required checks that are failing, so clients can say which check failed and why.
    blocking_checks: tuple[dict, ...] = ()
    clear: bool = False
    hard: tuple[str, ...] = ()

    @property
    def blockers(self) -> tuple[str, ...]:
        """Open items that stop a person publishing without an override."""
        return tuple(r for r in self.requirements if r not in ADVISORY_REQUIREMENTS)


async def _skill_blockers(db, review, version) -> list[str]:
    """Skill-specific publication rules that the generic gate cannot see."""
    blockers = []
    if needs_bundle_delivery(version) and not await _ds.get_bool("registry.skill_folder_delivery_enabled", False):
        # Installers cannot yet deliver extra files; approving would publish an uninstallable skill.
        blockers.append("skill_folder_rollout")
    if version.requires_global_review and version.pre_public_status is not None:
        return blockers  # Re-review of an already-approved release has no base or pointer rule.
    if version.base_version_id is not None:
        latest_id = await db.scalar(select(SkillListing.latest_version_id).where(SkillListing.id == review.subject_id))
        base = await db.get(SkillVersion, version.base_version_id)
        if (
            latest_id != version.base_version_id
            or base is None
            or base.status not in INSTALLABLE_STATUSES
            or base.requires_global_review
        ):
            blockers.append("approved_base_changed")
    return blockers


async def gate(db, review, *, policy: ApprovalPolicy | None = None) -> Gate:
    policy = policy or await policy_for(review, db=db)
    head = await _head(db, review)
    _, latest, _ = await review_decisions(db, review)
    # Dismissed decisions are terminal; neither an earlier approval nor an
    # earlier change request may silently reappear after withdrawal.
    approvals = sum(
        s.state == "submitted"
        and s.verdict == "approve"
        and (s.revision_id == head.id or not policy.dismiss_stale_approvals)
        and (not s.self_review or policy.self_approval == "counted")
        for s in latest.values()
    )
    # A change request remains blocking until its reviewer approves or withdraws it;
    # an author resubmitting new content does not silently clear the request.
    requests = {uid: s for uid, s in latest.items() if s.state == "submitted" and s.verdict == "request_changes"}
    blockers = []
    if approvals < policy.required_approvals[review.subject_type]:
        blockers.append("required_approvals")
    if requests:
        blockers.append("changes_requested")
    blocking_checks = tuple(
        {
            "id": check.get("id"),
            "name": check.get("name"),
            "status": check.get("status"),
            "details": check.get("details"),
        }
        for check in ci.visible(head.checks, await check_config.enabled())
        if check.get("required") and check.get("status") in ci.BLOCKING
    )
    if blocking_checks:
        blockers.append("required_checks")
    _, version = await _target(db, review)
    # Draft saves push a new revision (cutover.push_draft_edit), so this only shows when content
    # changed by another path. Publishing it would ship bytes no reviewer saw: never bypassed.
    if _hash(await render_files(review.subject_type, version, db)) != head.content_hash:
        blockers.append("unsubmitted_changes")
    if is_actively_editing(version):
        blockers.append("edit_lock")
    # Validation runs after submission, so this is evaluated live rather than snapshotted.
    # Direct-config MCPs (no git source) are never analysed; their config is user-provided, so they are exempt.
    if review.subject_type == "mcp" and getattr(version, "git_url", None) and not version.mcp_validated:
        blockers.append("mcp_validation")
    if review.subject_type == "agent" and await pinned_component_blockers(db, review.version_id):
        blockers.append("pinned_components")
    if review.subject_type == "skill":
        blockers.extend(await _skill_blockers(db, review, version))
        # An uninstallable folder is an integrity failure, not a check an admin may waive.
        if any(c.get("id") == "skill_folder" and c.get("status") != "pass" for c in head.checks or []):
            blockers.append("skill_folder")
    if policy.require_resolved_threads:
        threads = (
            (
                await db.execute(
                    select(ReviewThread)
                    .join(ReviewComment, ReviewComment.thread_id == ReviewThread.id)
                    .outerjoin(ReviewSubmission, ReviewSubmission.id == ReviewComment.submission_id)
                    .where(
                        ReviewThread.review_id == review.id,
                        ReviewThread.resolved_at.is_(None),
                        ReviewThread.outdated.is_(False),
                        (ReviewComment.submission_id.is_(None) | (ReviewSubmission.state != "draft")),
                    )
                    .limit(1)
                )
            )
            .scalars()
            .all()
        )
        if threads:
            blockers.append("unresolved_threads")
    return Gate(
        all(b in ADVISORY_REQUIREMENTS for b in blockers),
        approvals,
        policy.required_approvals[review.subject_type],
        policy.source,
        tuple(blockers),
        len(requests),
        blocking_checks,
        not blockers,
        tuple(b for b in blockers if b in HARD_REQUIREMENTS),
    )


_BLOCK_LABELS = {
    "required_checks": "required checks are failing",
    "changes_requested": "a reviewer must withdraw or approve their change request",
    "mcp_validation": "MCP validation has not passed",
    "unsubmitted_changes": "the content changed after the latest revision; submit it as a new revision",
    "skill_folder_rollout": "skill folders with extra files cannot be installed yet",
    "skill_folder": "the skill folder is not valid",
    "approved_base_changed": "the approved base version changed; refresh before publishing",
    "pinned_components": "pinned components must be published first",
}


def publish_block_message(blockers, result=None) -> str:
    """One readable phrase per blocker, with the approval count when approvals are missing."""
    parts = []
    for key in blockers:
        if key == "required_approvals" and result is not None:
            missing = result.required - result.approvals
            parts.append(
                f"needs {missing} more approval{'s' if missing != 1 else ''} "
                f"({result.approvals} of {result.required} on the latest revision)"
            )
        else:
            parts.append(_BLOCK_LABELS.get(key, key.replace("_", " ")))
    return "; ".join(parts)


def _auto_publish_allowed(policy, version) -> bool:
    # Folders with resources and public re-reviews always need a human decision.
    is_bundle = isinstance(version, SkillVersion) and needs_bundle_delivery(version)
    return policy.auto_publish and not is_bundle and not getattr(version, "requires_global_review", False)


async def sync_state(db, review, *, actor_id=None, policy=None):
    if review.state in (ReviewState.closed, ReviewState.published):
        return None
    result = await gate(db, review, policy=policy)
    if result.ready and review.state != ReviewState.approved:
        review.state = ReviewState.approved
        _, version = await _target(db, review)
        version.status = AgentStatus.pending if review.subject_type == "agent" else ListingStatus.pending
        _event(db, review, "gate_ready", actor_id)
        # Unattended publishing still needs every open item cleared; only people may publish past them.
        if result.clear and _auto_publish_allowed(policy or await policy_for(review, db=db), version):
            await publish(db, review, None, auto=True, policy=policy)
    elif result.ready and review.state == ReviewState.approved:
        _, version = await _target(db, review)
        if result.clear and _auto_publish_allowed(policy or await policy_for(review, db=db), version):
            await publish(db, review, None, auto=True, policy=policy)
    elif not result.ready and review.state == ReviewState.approved:
        review.state = ReviewState.changes_requested if result.outstanding_requests else ReviewState.open
        _, version = await _target(db, review)
        version.status = (
            (AgentStatus.changes_requested if review.subject_type == "agent" else ListingStatus.changes_requested)
            if result.outstanding_requests
            else (AgentStatus.pending if review.subject_type == "agent" else ListingStatus.pending)
        )
        _event(db, review, "gate_lost", actor_id, requirements=result.requirements)
    elif not result.ready and result.outstanding_requests and review.state != ReviewState.changes_requested:
        review.state = ReviewState.changes_requested
        _, version = await _target(db, review)
        version.status = (
            AgentStatus.changes_requested if review.subject_type == "agent" else ListingStatus.changes_requested
        )
    elif not result.outstanding_requests and review.state == ReviewState.changes_requested:
        review.state = ReviewState.open
        _, version = await _target(db, review)
        version.status = AgentStatus.pending if review.subject_type == "agent" else ListingStatus.pending
    return result


async def submit_verdict(db, review, user, verdict, body="", *, policy=None):
    if verdict not in ("approve", "request_changes", "comment"):
        raise HTTPException(422, "Invalid verdict")
    await _lock_review(db, review)
    subject, version = await _target(db, review)
    if verdict == "comment":
        if not (can_review(subject, await review_scope(db, user)) or _own_work(subject, version, user.id)):
            raise HTTPException(403, "Only participants may comment")
    else:
        await _require_reviewer(db, review, user)
    if review.state in (ReviewState.closed, ReviewState.published):
        raise HTTPException(409, "Review is closed")
    if verdict != "comment" and is_actively_editing(version):
        raise HTTPException(409, "Version is being edited")
    own = _own_work(subject, version, user.id)
    if own and verdict == "request_changes":
        raise HTTPException(403, "Cannot request changes on your own work")
    # Submitting a pending review publishes all of that user's draft comments
    # atomically with their verdict, rather than stranding them in a draft row.
    submission = await db.scalar(
        select(ReviewSubmission)
        .where(
            ReviewSubmission.review_id == review.id,
            ReviewSubmission.reviewer_id == user.id,
            ReviewSubmission.state == "draft",
        )
        .with_for_update()
    )
    if submission is None:
        submission = ReviewSubmission(review_id=review.id, reviewer_id=user.id)
        db.add(submission)
    submission.state = "submitted"
    submission.verdict = verdict
    submission.body = body
    submission.revision_id = review.head_revision_id
    submission.self_review = own
    submission.submitted_at = _now()
    if verdict == "request_changes":
        review.state = ReviewState.changes_requested
        version.status = (
            AgentStatus.changes_requested if review.subject_type == "agent" else ListingStatus.changes_requested
        )
    _event(db, review, "submission", user.id, verdict=verdict)
    await db.flush()
    if verdict == "request_changes":
        thread = ReviewThread(review_id=review.id, change_request_id=submission.id, created_by=user.id)
        db.add(thread)
        await db.flush()
        db.add(
            ReviewComment(
                review_id=review.id,
                thread_id=thread.id,
                submission_id=submission.id,
                author_id=user.id,
                body=body,
            )
        )
        await db.flush()
    await reconcile_request_threads(db, review, actor_id=user.id)
    await sync_state(db, review, actor_id=user.id, policy=policy)
    return submission


async def may_withdraw_request(db, review, submission, user):
    subject, version = await _target(db, review)
    if _own_work(subject, version, user.id):
        return False
    scope = await review_scope(db, user)
    if submission.reviewer_id == user.id:
        return can_review(subject, scope) and (
            not getattr(version, "requires_global_review", False) or scope.is_global_reviewer
        )
    if user.role in (UserRole.admin, UserRole.super_admin):
        return True
    if not can_review(subject, scope) or (
        getattr(version, "requires_global_review", False) and not scope.is_global_reviewer
    ):
        return False
    from models.team import TeamMembership, TeamRole

    return bool(
        await db.scalar(
            select(TeamMembership.user_id).where(
                TeamMembership.team_id == subject.team_id,
                TeamMembership.user_id == user.id,
                TeamMembership.role == TeamRole.owner,
            )
        )
    )


async def dismiss(db, review, submission, user, reason, *, policy=None, withdraw_request=False):
    await _lock_review(db, review)
    await db.refresh(submission, with_for_update=True)
    if review.state in (ReviewState.closed, ReviewState.published):
        raise HTTPException(409, "Review is closed")
    if not reason.strip():
        raise HTTPException(422, "A dismissal reason is required")
    if submission.review_id != review.id or submission.state != "submitted":
        raise HTTPException(409, "Submission is not active")
    if withdraw_request and submission.verdict != "request_changes":
        raise HTTPException(409, "Only change requests can be withdrawn")
    if submission.verdict == "request_changes":
        _, latest, _ = await review_decisions(db, review)
        if latest.get(submission.reviewer_id) is None or latest[submission.reviewer_id].id != submission.id:
            raise HTTPException(409, "Change request is no longer outstanding")
        if not await may_withdraw_request(db, review, submission, user):
            raise HTTPException(403, "Only the reviewer or a disinterested moderator may clear this request")
        if not withdraw_request and submission.reviewer_id == user.id:
            raise HTTPException(403, "Use withdraw-request to retract your own change request")
    elif withdraw_request:
        raise HTTPException(409, "Only change requests can be withdrawn")
    elif user.role not in (UserRole.admin, UserRole.super_admin):
        from models.team import TeamMembership, TeamRole

        subject, _ = await _target(db, review)
        row = await db.scalar(
            select(TeamMembership).where(
                TeamMembership.team_id == subject.team_id,
                TeamMembership.user_id == user.id,
                TeamMembership.role == TeamRole.owner,
            )
        )
        if row is None:
            raise HTTPException(403, "Only admins and team owners may dismiss reviews")
    submission.state = "dismissed"
    submission.dismissed_by = user.id
    submission.dismissed_at = _now()
    submission.dismissed_reason = reason
    _event(
        db,
        review,
        "request_withdrawn" if submission.verdict == "request_changes" else "approval_dismissed",
        user.id,
        submission_id=str(submission.id),
        reason=reason,
    )
    await db.flush()
    await reconcile_request_threads(db, review, actor_id=user.id)
    return await sync_state(db, review, actor_id=user.id, policy=policy)


async def publish(db, review, user, *, category=None, override_reason=None, auto=False, policy=None):
    """No commit: caller must commit status, promotion, lock and review atomically."""
    await _lock_review(db, review)
    if review.state in (ReviewState.closed, ReviewState.published):
        raise HTTPException(409, "Review is closed")
    subject, version = await _target(db, review)
    policy = policy or await policy_for(review, db=db)
    if override_reason is not None and not override_reason.strip():
        override_reason = None
    if override_reason and (auto or user is None or user.role != UserRole.super_admin):
        raise HTTPException(403, "Only a super admin may publish with an override reason")
    # A super admin override publishes past approvals and admin-required checks, never past HARD_REQUIREMENTS.
    override = bool(override_reason)
    if not auto:
        if user is None:
            raise HTTPException(403, "Publisher required")
        await _require_reviewer(db, review, user)
        if (
            _own_work(subject, version, user.id)
            and not (subject.is_private and policy.source == "teamspace" and policy.self_approval == "counted")
            and not override
        ):
            raise HTTPException(403, "Authors and co-authors cannot publish their own work")
    await ensure_request_threads(db, review, persist=False, update_state=False)
    if review.subject_type == "agent":
        # Confidentiality, not review quality: a public agent must never expose a team-private
        # skill. Unlike the advisory gate items, no publish path (including an override) skips this.
        private = [b for b in await pinned_component_blockers(db, version.id) if b.get("status") == "not_public"]
        if private:
            names = ", ".join(str(b.get("name") or b["component_id"]) for b in private)
            raise HTTPException(
                422,
                f"A public agent cannot pin team-private skills ({names}). "
                "Make the agent team-visible or pin public skills, then submit a new revision.",
            )
    result = await gate(db, review, policy=policy)
    if result.hard:
        raise HTTPException(
            409,
            f"Review gate blocked (no override): {', '.join(result.hard)}. {publish_block_message(result.hard)}.",
        )
    if auto:
        # Unattended publishing never skips an open item.
        if not result.clear:
            raise HTTPException(409, f"Auto-publish waits for every open item: {', '.join(result.requirements)}")
    elif not override and not result.ready:
        raise HTTPException(
            409,
            f"Review gate blocked: {', '.join(result.blockers)}. {publish_block_message(result.blockers, result)}. "
            "Unresolved conversations and edit locks are advisory; change requests block publication. "
            "A super admin can publish past approvals and checks with an override reason.",
        )
    re_review = review.subject_type == "skill" and version.pre_public_status is not None
    version.status = (
        AgentStatus.approved
        if review.subject_type == "agent"
        else ListingStatus(version.pre_public_status)
        if re_review
        else ListingStatus.approved
    )
    version.rejection_reason = None
    # Publication supersedes any abandoned author edit lock on this version.
    release_edit_lock(version, version.editing_by, force=True)
    version.reviewed_at = _now()
    # A counted approver is the reviewer of record; an override can have none.
    approvals = (
        (
            await db.execute(
                select(ReviewSubmission)
                .where(
                    ReviewSubmission.review_id == review.id,
                    ReviewSubmission.revision_id == review.head_revision_id,
                    ReviewSubmission.verdict == "approve",
                    ReviewSubmission.state == "submitted",
                )
                .order_by(ReviewSubmission.submitted_at.desc())
            )
        )
        .scalars()
        .all()
    )
    version.reviewed_by = next(
        (s.reviewer_id for s in approvals if not s.self_review or policy.self_approval == "counted"), None
    )
    await db.flush()
    if review.subject_type == "agent":
        await lock_agent_version(db, subject, version)
        if category:
            subject.category = category
    current = await db.get(type(version), subject.latest_version_id) if subject.latest_version_id else None
    new_key, old_key = parse_semver(version.version), parse_semver(current.version) if current else None
    if review.subject_type == "skill":
        promote = False
        marked = version.requires_global_review
        if marked:
            version.requires_global_review = False
            version.pre_public_status = None
            version.pre_public_reviewed_by = None
            version.pre_public_reviewed_at = None
            await db.flush()
        if re_review:
            await _repair_skill_pointer(db, subject)  # Older rows can clear out of order.
        else:
            promote = subject.latest_version_id is None or await should_promote_skill_version(
                db, subject.latest_version_id, version
            )
            # An override still publishes; it just leaves the newer release as the default.
            if (
                not promote
                and not override
                and ("-" not in version.version or not _PRERELEASE_RE.fullmatch(version.version))
            ):
                raise HTTPException(409, "Newer approved skill release exists; refresh before publishing")
    else:
        promote = current is None or (new_key is not None and (old_key is None or new_key >= old_key))
    if promote:
        await db.execute(
            update(type(subject)).where(type(subject).id == subject.id).values(latest_version_id=version.id)
        )
        # Avoid a stale identity-map relationship when the request reuses subject.
        db.expire(subject, ["latest_version_id", "latest_version"])
    review.state = ReviewState.published
    review.published_at = _now()
    review.published_by = user.id if user else None
    review.publish_override_reason = override_reason
    _event(
        db,
        review,
        "publish_override" if override_reason else "auto_published" if auto else "published",
        user.id if user else None,
        reason=override_reason,
    )
    # A newer published release supersedes older still-pending reviews of this subject.
    pending = (
        (
            await db.execute(
                select(Review).where(
                    Review.subject_type == review.subject_type,
                    Review.subject_id == subject.id,
                    Review.id != review.id,
                    Review.state.in_((ReviewState.open, ReviewState.changes_requested, ReviewState.approved)),
                )
            )
        )
        .scalars()
        .all()
    )
    for older in pending:
        older_version = await db.get(type(version), older.version_id)
        requeued = await db.scalar(
            select(ReviewEvent.id)
            .where(ReviewEvent.review_id == older.id, ReviewEvent.kind == "visibility_requeued")
            .limit(1)
        )
        if (
            older_version
            and requeued is None  # A requeued public re-review would lose its earlier approval.
            and not getattr(older_version, "requires_global_review", False)
            and parse_semver(older.version)
            and new_key
            and parse_semver(older.version) < new_key
        ):
            older.state = ReviewState.closed
            older.closed_reason = "superseded"
            older.closed_at = _now()
            older_version.status = AgentStatus.rejected if review.subject_type == "agent" else ListingStatus.rejected
            older_version.rejection_reason = f"Superseded by v{version.version}"
            _event(db, older, "superseded", user.id if user else None, by=review.number)
            await reconcile_request_threads(db, older, actor_id=user.id if user else None)
            from models.inbox import InboxKind
            from services.review.notifications import resolve_review_work

            await resolve_review_work(db, older, user.id if user else None, kinds=(InboxKind.change_requested,))
    if review.subject_type != "agent":
        dependent_ids = (
            (
                await db.execute(
                    select(AgentComponent.agent_version_id).where(
                        AgentComponent.component_type == review.subject_type,
                        AgentComponent.resolved_version_id == version.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        for dependent in (
            (
                await db.execute(
                    select(Review).where(
                        Review.subject_type == "agent",
                        Review.version_id.in_(dependent_ids),
                        Review.state.in_((ReviewState.open, ReviewState.changes_requested, ReviewState.approved)),
                    )
                )
            )
            .scalars()
            .all()
        ):
            await sync_state(db, dependent, actor_id=user.id if user else None)
    return review


async def _repair_skill_pointer(db, listing) -> None:
    """After a historical re-review clears, point at the highest cleared stable release without demoting."""
    cleared = (
        await db.execute(
            select(SkillVersion.id, SkillVersion.version, SkillVersion.status).where(
                SkillVersion.listing_id == listing.id,
                SkillVersion.status.in_(INSTALLABLE_STATUSES),
                SkillVersion.requires_global_review.is_(False),
            )
        )
    ).all()
    current = next((row for row in cleared if row.id == listing.latest_version_id), None)
    if current is not None and current.status == ListingStatus.archived:
        return  # Preserve an intentionally archived pointer.
    approved = [row for row in cleared if row.status == ListingStatus.approved]
    stable = [(row, parse_semver(row.version)) for row in (approved or cleared)]
    stable = [(row, number) for row, number in stable if number is not None]
    if stable:
        best, best_number = max(stable, key=lambda pair: (pair[1], str(pair[0].id)))
        current_number = parse_semver(current.version) if current is not None else None
        if current is None or (current_number is not None and best_number > current_number):
            await db.execute(
                update(SkillListing).where(SkillListing.id == listing.id).values(latest_version_id=best.id)
            )
            db.expire(listing, ["latest_version_id", "latest_version"])
    elif current is None and len(cleared) == 1:
        await db.execute(
            update(SkillListing).where(SkillListing.id == listing.id).values(latest_version_id=cleared[0].id)
        )
        db.expire(listing, ["latest_version_id", "latest_version"])
    elif current is None:
        raise HTTPException(409, "Cannot select a default from historical skill versions")


async def close(db, review, user, reason):
    await _lock_review(db, review)
    await _require_reviewer(db, review, user)
    subject, version = await _target(db, review)
    if _own_work(subject, version, user.id):
        raise HTTPException(403, "Cannot close your own review")
    if not reason.strip() or review.state in (ReviewState.closed, ReviewState.published):
        raise HTTPException(409, "Review is closed or no reason was given")
    if is_actively_editing(version):
        raise HTTPException(409, "Version is being edited")
    review.state = ReviewState.closed
    review.closed_reason = "rejected"
    review.closed_at = _now()
    review.closed_by = user.id
    version.status = AgentStatus.rejected if review.subject_type == "agent" else ListingStatus.rejected
    version.rejection_reason = reason
    _event(db, review, "closed", user.id, reason=reason)
    if review.subject_type != "agent":
        from services.insights.self_learn import handle_component_rejection

        await handle_component_rejection(review.subject_type, subject.id, db)
    return review


async def withdraw(db, review, user):
    await _lock_review(db, review)
    subject, version = await _target(db, review)
    if review.subject_type == "skill":
        # Version-bound skill withdrawal grants owner access to co-authors and
        # admins as well; keep the shared decision path consistent with the route.
        from api.deps import get_effective_component_permission

        allowed = get_effective_component_permission(subject, user) == "owner"
    else:
        allowed = user.id in (
            version.released_by,
            getattr(subject, "created_by", None),
            getattr(subject, "submitted_by", None),
        )
    if not allowed:
        raise HTTPException(403, "Not the author or owner")
    if review.state in (ReviewState.closed, ReviewState.published):
        raise HTTPException(409, "Review is closed")
    if review.subject_type == "skill":
        if version.pre_public_status is not None:
            # Withdrawing would overwrite the release's approved state.
            raise HTTPException(409, "A public re-review of an approved release cannot be withdrawn")
        if version.content_revision is not None:
            # Revision-bound versions void old reviewer observations; unbound legacy rows have none to void.
            from services.skill_revisions import skill_content_revision

            version.review_epoch = (version.review_epoch or 0) + 1
            version.content_revision = skill_content_revision(subject, version)
    review.state = ReviewState.closed
    review.closed_reason = "withdrawn"
    review.closed_at = _now()
    review.closed_by = user.id
    version.status = AgentStatus.draft if review.subject_type == "agent" else ListingStatus.draft
    _event(db, review, "withdrawn", user.id)
    return review
