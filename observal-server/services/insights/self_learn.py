# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Self-learning pipeline: applies insight suggestions as pending registry items.

Takes a completed InsightReport's suggestions section and materializes them
into actionable registry submissions:
- config_additions → new AgentVersion with updated prompt (pending review)
- features_to_try (skills) → new SkillListing (pending review)
- features_to_try (hooks) → new HookListing (pending review)
- usage_patterns (copyable prompts) → new PromptListing (pending review)

All items enter the review queue under the agent owner's identity.

Selection: callers pass an optional `selection` dict with index arrays
to choose specific suggestions. Omit or pass None to apply all in a category.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import yaml
from loguru import logger as optic
from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from models.agent import Agent, AgentStatus, AgentVersion
from models.agent_component import AgentComponent
from models.hook import HookListing, HookVersion
from models.insight_report import InsightReport, InsightReportStatus
from models.mcp import ListingStatus
from models.prompt import PromptListing, PromptVersion
from models.review import Review, ReviewEvent, ReviewState
from models.skill import SkillListing, SkillVersion
from models.user import User
from services.registry_namespace import slugify
from services.registry_recommender import (
    ALL_COMPONENT_TYPES,
    COMPONENT_MODELS,
    ResolvedComponent,
    coerce_uuid,
    resolve_component_any_type,
)
from services.review.decisions import open_or_push, sync_state
from services.review.notifications import deliver_event
from services.versioning import bump_version


class _NewComponent:
    """A listing created during this apply run (always version 1.0.0).

    Deliberately a plain class rather than a dataclass: this module combines
    ``from __future__ import annotations`` with being loaded standalone by
    tests via ``importlib`` under a synthetic module name. Dataclass field
    resolution looks that name up in ``sys.modules`` and crashes when it is
    absent, so the decorator would make the module unloadable in that test.
    """

    __slots__ = ("component_id", "component_type")

    def __init__(self, component_type: str, component_id: uuid.UUID) -> None:
        self.component_type = component_type
        self.component_id = component_id


# Action types that mean "attach something the registry already has".
_REUSE_ACTIONS = frozenset({"reuse_existing_component", "attach_registry_component"})

# Separator appended before auto-generated additions to the system prompt
_SELF_LEARN_SEPARATOR = "\n\n# ── Auto-learned from Insights ──\n"

# Maximum slug length for generated names
_MAX_NAME_LEN = 48


async def _materialize_insight_suggestions(
    report_id: str | uuid.UUID,
    db: AsyncSession,
    triggered_by: uuid.UUID,
    selection: dict | None = None,
) -> dict:
    """Apply suggestions from a completed insight report.

    Args:
        report_id: The insight report to apply.
        db: Async DB session.
        triggered_by: The admin user triggering the apply.
        selection: Optional dict with keys config_indices, feature_indices,
                   pattern_indices. Each is a list of 0-based indices to apply.
                   None or missing key means apply all in that category.

    Returns a summary dict of what was created.
    """
    # Load report. Accept either a UUID or its string form — callers come
    # from both HTTP paths (string) and internal code (UUID).
    report_uuid = coerce_uuid(report_id)
    if report_uuid is None:
        raise ValueError("Report not found")

    stmt = select(InsightReport).where(InsightReport.id == report_uuid).with_for_update()
    result = await db.execute(stmt)
    report = result.scalar_one_or_none()

    if not report:
        raise ValueError("Report not found")
    if report.status != InsightReportStatus.completed:
        raise ValueError("Report is not completed")
    if report.applied_at is not None:
        raise ValueError("Suggestions have already been applied for this report")

    # Match publication's review -> agent lock order before superseding proposals.
    await db.execute(
        select(Review.id)
        .where(
            Review.subject_type == "agent",
            Review.subject_id == report.agent_id,
            Review.state.in_([ReviewState.open, ReviewState.changes_requested, ReviewState.approved]),
        )
        .order_by(Review.id)
        .with_for_update()
    )

    # Load agent
    agent_stmt = select(Agent).where(Agent.id == report.agent_id).with_for_update()
    agent_result = await db.execute(agent_stmt)
    agent = agent_result.scalar_one_or_none()
    if not agent:
        raise ValueError("Agent not found")

    if isinstance(agent, Agent):
        await db.refresh(agent, attribute_names=["latest_version"])

    # Resolve the submitting user (agent owner)
    owner_user = await _resolve_owner_user(agent, db)
    submitter_id = owner_user.id if owner_user else triggered_by

    # Extract suggestions from narrative
    if report.narrative is not None and not isinstance(report.narrative, dict):
        raise ValueError("Report narrative has an invalid shape")
    if isinstance(report.narrative, dict) and not isinstance(report.narrative.get("section_errors", []), list):
        raise ValueError("Report section-error metadata has an invalid shape")
    narrative = report.narrative or {}
    from services.insights.section_validation import suggestions_failed

    if suggestions_failed(narrative):
        raise ValueError("Suggestions generation failed; generate a new report before applying")
    suggestions = narrative.get("suggestions", {})
    if not suggestions:
        raise ValueError("Report has no suggestions to apply")
    if not isinstance(suggestions, dict) or any(
        not isinstance(suggestions.get(key, []), list)
        for key in ("config_additions", "features_to_try", "usage_patterns")
    ):
        raise ValueError("Suggestion categories must be lists")

    applied: dict = {
        "agent_version": None,
        "skills": [],
        "hooks": [],
        "prompts": [],
        "linked_existing": [],
        "removed_components": [],
    }

    approved_base = await db.scalar(
        select(AgentVersion)
        .where(AgentVersion.agent_id == agent.id, AgentVersion.status == AgentStatus.approved)
        .order_by(AgentVersion.created_at.desc())
        .limit(1)
    )
    if approved_base is None:
        raise ValueError("Agent has no approved version to improve")
    agent._insights_harnesses = list(approved_base.supported_harnesses or [])

    # Determine which indices to apply per category
    config_indices = _resolve_indices(
        selection.get("config_indices") if selection else None,
        suggestions.get("config_additions", []),
    )
    feature_indices = _resolve_indices(
        selection.get("feature_indices") if selection else None,
        suggestions.get("features_to_try", []),
    )
    pattern_indices = _resolve_indices(
        selection.get("pattern_indices") if selection else None,
        suggestions.get("usage_patterns", []),
    )

    # Regeneration semantics: applying a newer report hard-replaces older
    # pending insight-generated versions so users do not see competing fixes.
    superseded_versions = await _withdraw_stale_insight_generated_versions(agent, db, triggered_by)
    if superseded_versions:
        applied["superseded_agent_versions"] = superseded_versions

    # 1. Create SkillListings and HookListings from features_to_try
    features_to_try = suggestions.get("features_to_try", [])
    created_skill_ids: list[uuid.UUID] = []
    created_hook_ids: list[uuid.UUID] = []
    # Reused registry components, keyed by type. Values carry the resolved
    # name and version so the AgentComponent row is written accurately.
    linked_existing: list[ResolvedComponent] = []
    removed_component_ids: list[uuid.UUID] = []

    for idx in feature_indices:
        if idx >= len(features_to_try):
            continue
        feature = features_to_try[idx]
        if not isinstance(feature, dict):
            raise ValueError("Selected feature has an invalid shape")
        if any(
            feature.get(field) is not None and not isinstance(feature[field], str)
            for field in (
                "feature",
                "name",
                "one_liner",
                "why_for_you",
                "example",
                "existing_component_id",
                "action_type",
            )
        ):
            raise ValueError("Selected feature metadata must use string fields")
        action_type = str(feature.get("action_type") or "").lower()
        if action_type not in _REUSE_ACTIONS | {"create_new_skill", "create_new_hook", "remove_component", "no_action"}:
            raise ValueError("Selected feature has no supported explicit action_type")
        if action_type == "no_action":
            continue
        existing_id = feature.get("existing_component_id")
        if action_type in _REUSE_ACTIONS and not existing_id:
            raise ValueError("A selected reuse action needs an explicit component ID")
        if action_type in {"create_new_skill", "create_new_hook"} and existing_id:
            raise ValueError("Creation actions cannot silently replace an existing component reference")
        if action_type == "create_new_skill" and str(feature.get("feature", "")).strip().lower() not in {
            "skill",
            "custom skill",
        }:
            raise ValueError("New skill actions must declare feature=Skill")
        if action_type == "create_new_hook" and str(feature.get("feature", "")).strip().lower() not in {
            "hook",
            "lifecycle hook",
        }:
            raise ValueError("New hook actions must declare feature=Hook")
        if action_type in _REUSE_ACTIONS and existing_id:
            resolved = await _resolve_reusable_component(feature, agent, db)
            if not resolved:
                optic.info("self_learn_reuse_rejected: report_id={}", str(report_uuid))
                raise ValueError("Selected registry component is unavailable or not authorized")
            linked_existing.append(resolved)
            applied["linked_existing"].append(
                {
                    "type": resolved.component_type,
                    "id": str(resolved.id),
                    "name": resolved.name,
                    "qualified_name": resolved.qualified_name,
                    "version": resolved.latest_version,
                    "reason": feature.get("why_for_you"),
                    "confidence": feature.get("confidence"),
                    "risk": feature.get("risk"),
                }
            )
            continue
        # Creating brand-new MCPs from an LLM suggestion is never safe: they
        # carry commands, images and credentials. Reuse above is allowed.
        if str(feature.get("feature", "")).strip().lower() in {"mcp", "mcp server", "server"}:
            raise ValueError("New executable MCP suggestions are not supported; select a reviewed existing component")
        if action_type == "remove_component" and existing_id:
            try:
                cid = uuid.UUID(str(existing_id))
            except ValueError:
                cid = None

            attached = (
                await db.scalar(
                    select(AgentComponent.component_id)
                    .join(AgentVersion, AgentVersion.id == AgentComponent.agent_version_id)
                    .where(
                        AgentVersion.id == approved_base.id,
                        AgentComponent.component_id == cid,
                    )
                    .limit(1)
                )
                if cid
                else None
            )
            if attached is None:
                raise ValueError("Selected removal does not reference an attached component")
            if cid:
                removed_component_ids.append(cid)
                applied["removed_components"].append(
                    {
                        "id": str(cid),
                        "name": feature.get("name"),
                        "reason": feature.get("why_for_you"),
                        "confidence": feature.get("confidence"),
                        "risk": feature.get("risk"),
                    }
                )
            continue
        if action_type == "create_new_skill":
            skill_info = await _create_skill_listing(
                agent=agent,
                feature=feature,
                submitter_id=submitter_id,
                db=db,
                actor_id=triggered_by,
            )
            if not skill_info:
                raise ValueError("Selected skill could not be materialized; check its content and name")
            if skill_info:
                skill_info["confidence"] = feature.get("confidence")
                skill_info["risk"] = feature.get("risk")
                skill_info["why"] = feature.get("why_for_you")
                applied["skills"].append(skill_info)
                created_skill_ids.append(uuid.UUID(skill_info["id"]))
        elif action_type == "create_new_hook":
            hook_info = await _create_hook_listing(
                agent=agent,
                feature=feature,
                submitter_id=submitter_id,
                db=db,
                actor_id=triggered_by,
            )
            if not hook_info:
                raise ValueError("Selected hook could not be materialized; check its content and name")
            if hook_info:
                hook_info["confidence"] = feature.get("confidence")
                hook_info["risk"] = feature.get("risk")
                hook_info["why"] = feature.get("why_for_you")
                applied["hooks"].append(hook_info)
                created_hook_ids.append(uuid.UUID(hook_info["id"]))

    # 2. Create PromptListings from usage_patterns
    usage_patterns = suggestions.get("usage_patterns", [])
    created_prompt_ids: list[uuid.UUID] = []

    for idx in pattern_indices:
        if idx >= len(usage_patterns):
            continue
        pattern = usage_patterns[idx]
        if not isinstance(pattern, dict) or any(
            pattern.get(field) is not None and not isinstance(pattern[field], str)
            for field in ("title", "detail", "suggestion", "copyable_prompt")
        ):
            raise ValueError("Selected prompt pattern has invalid string metadata")
        if (
            not isinstance(pattern, dict)
            or not isinstance(pattern.get("copyable_prompt"), str)
            or not pattern["copyable_prompt"].strip()
        ):
            raise ValueError("Selected prompt pattern has no complete copyable_prompt")
        if pattern.get("copyable_prompt"):
            prompt_info = await _create_prompt_listing(
                agent=agent,
                pattern=pattern,
                submitter_id=submitter_id,
                db=db,
                actor_id=triggered_by,
            )
            if not prompt_info:
                raise ValueError("Selected prompt could not be materialized; check its content and name")
            if prompt_info:
                applied["prompts"].append(prompt_info)
                created_prompt_ids.append(uuid.UUID(prompt_info["id"]))

    # 3. Create new AgentVersion with selected config additions + linked components
    config_additions = suggestions.get("config_additions", [])
    selected_additions = [config_additions[i] for i in config_indices]
    if any(
        not isinstance(item, dict)
        or item.get("action_type", "modify_prompt") != "modify_prompt"
        or item.get("where", "system_prompt") not in ("system_prompt", "AGENTS.md")
        or not isinstance(item.get("addition"), str)
        or not item["addition"].strip()
        for item in selected_additions
    ):
        raise ValueError("Selected prompt additions have unsupported destinations or empty content")
    for item in selected_additions:
        required = item.get("requires_feature_indices")
        if not isinstance(required, list) or any(
            type(i) is not int or i < 0 or i >= len(features_to_try) for i in required
        ):
            raise ValueError("Prompt addition lacks valid explicit dependency metadata; generate a new report")
        if not set(required).issubset(feature_indices):
            raise ValueError("A selected prompt addition requires an unselected feature")
        if any(
            str(features_to_try[i].get("action_type") or "").lower() in {"no_action", "remove_component"}
            for i in required
        ):
            raise ValueError("A required feature must be created or attached, not removed or skipped")
    if selected_additions:
        applied["prompt_additions"] = [
            {
                "addition": item.get("addition"),
                "where": item.get("where"),
                "why": item.get("why"),
                "confidence": item.get("confidence"),
                "risk": item.get("risk") or "low",
            }
            for item in selected_additions
        ]

    # Components created in this run are always at 1.0.0 by construction;
    # reused ones carry whatever version the registry resolved.
    new_components = (
        [_NewComponent("skill", cid) for cid in created_skill_ids]
        + [_NewComponent("hook", cid) for cid in created_hook_ids]
        + [_NewComponent("prompt", cid) for cid in created_prompt_ids]
    )

    if selected_additions or new_components or linked_existing or removed_component_ids:
        version_info = await _create_agent_version_with_additions(
            agent=agent,
            config_additions=selected_additions,
            submitter_id=submitter_id,
            db=db,
            new_components=new_components,
            linked_existing=linked_existing,
            removed_component_ids=removed_component_ids,
            actor_id=triggered_by,
        )
        if version_info:
            applied["agent_version"] = version_info

    # Mark report as applied
    report.applied_at = datetime.now(UTC)
    report.applied_items = applied

    await db.flush()

    optic.info(
        "self_learn_staged: report_id={}, agent_id={}, version_created={}, skills_created={}, hooks_created={}, prompts_created={}",
        report_id,
        str(agent.id),
        applied["agent_version"] is not None,
        len(applied["skills"]),
        len(applied["hooks"]),
        len(applied["prompts"]),
    )

    return applied


async def _withdraw_stale_insight_generated_versions(agent: Agent, db: AsyncSession, actor_id: uuid.UUID) -> list[dict]:
    """Supersede only provably generated pending proposals, never a description-name guess."""
    from api.deps import get_effective_agent_permission

    generated_ids = set(
        (
            await db.scalars(
                select(Review.version_id)
                .join(ReviewEvent, ReviewEvent.review_id == Review.id)
                .where(
                    Review.subject_type == "agent",
                    Review.subject_id == agent.id,
                    ReviewEvent.kind == "insights_generated",
                )
            )
        ).all()
    )
    for prior in (
        await db.scalars(
            select(InsightReport).where(InsightReport.agent_id == agent.id, InsightReport.applied_at.is_not(None))
        )
    ).all():
        info = prior.applied_items.get("agent_version") if isinstance(prior.applied_items, dict) else None
        if isinstance(info, dict) and (vid := coerce_uuid(info.get("id"))) is not None:
            generated_ids.add(vid)
    pending = (
        await db.scalars(
            select(AgentVersion).where(AgentVersion.agent_id == agent.id, AgentVersion.status == AgentStatus.pending)
        )
    ).all()
    superseded = []
    for version in pending:
        if version.id not in generated_ids:
            continue
        actor = await db.get(User, actor_id)
        if get_effective_agent_permission(agent, actor) not in {"owner", "edit"}:
            raise ValueError("Not authorized to replace prior generated proposals")
        old_review = await db.scalar(
            select(Review).where(Review.subject_type == "agent", Review.version_id == version.id)
        )
        if old_review and old_review.state in {ReviewState.closed, ReviewState.published}:
            continue
        if old_review:
            old_review.state = ReviewState.closed
            old_review.closed_reason = "superseded"
            old_review.closed_at = datetime.now(UTC)
            old_review.closed_by = actor_id
            db.add(ReviewEvent(review_id=old_review.id, kind="superseded", actor_id=actor_id))
            await deliver_event(db, old_review, "superseded", actor_id)
        version.status = AgentStatus.rejected
        version.rejection_reason = "Superseded by newer insight-generated proposal"
        superseded.append({"id": str(version.id), "version": version.version})
    await db.flush()
    return superseded


async def handle_component_rejection(
    component_type: str,
    component_id: uuid.UUID,
    db: AsyncSession,
) -> None:
    """Handle rejection of a self-learn component.

    When a component created by self-learn gets rejected:
    1. Find any InsightReport that references this component in applied_items.
    2. Remove the rejected item from the report's applied_items.
    3. If the agent version depends on the rejected component, mark it as withdrawn.

    Called from the review endpoint when rejecting a listing.
    """
    # Find reports that reference this component

    reports = await db.execute(
        select(InsightReport).where(
            InsightReport.applied_items.isnot(None),
        )
    )

    for report in reports.scalars().all():
        items = report.applied_items or {}
        modified = False

        # Check skills, hooks, prompts lists
        for category in ("skills", "hooks", "prompts"):
            entries = items.get(category, [])
            original_len = len(entries)
            items[category] = [e for e in entries if e.get("id") != str(component_id)]
            if len(items[category]) < original_len:
                modified = True

        if modified:
            report.applied_items = items

            # If an agent version was linked and depends on the rejected component,
            # withdraw it
            version_info = items.get("agent_version")
            if version_info and version_info.get("id"):
                await _withdraw_version_if_dependency_rejected(
                    version_id=uuid.UUID(version_info["id"]),
                    db=db,
                )

    await db.flush()


async def _withdraw_version_if_dependency_rejected(
    version_id: uuid.UUID,
    db: AsyncSession,
) -> None:
    """Mark an agent version as withdrawn if it had a rejected dependency."""
    version_stmt = select(AgentVersion).where(AgentVersion.id == version_id)
    result = await db.execute(version_stmt)
    version = result.scalar_one_or_none()

    if version and version.status == AgentStatus.pending:
        version.status = AgentStatus.rejected
        version.description = (version.description or "") + " [auto-withdrawn: linked component rejected]"


def _resolve_indices(selected: list[int] | None, items: list) -> list[int]:
    """Return the indices to process. If selected is None, return all indices."""
    if selected is None:
        return list(range(len(items)))
    if len(selected) != len(set(selected)) or any(
        not isinstance(i, int) or isinstance(i, bool) or i < 0 or i >= len(items) for i in selected
    ):
        raise ValueError("Selected suggestion indices are invalid or duplicated")
    return selected


async def _resolve_owner_user(agent: Agent, db: AsyncSession) -> User | None:
    """Resolve the agent's owner to a User record."""
    if agent.created_by:
        stmt = select(User).where(User.id == agent.created_by)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()
    return None


async def _create_agent_version_with_additions(
    agent: Agent,
    config_additions: list[dict],
    submitter_id: uuid.UUID,
    db: AsyncSession,
    new_components: list[_NewComponent] | None = None,
    linked_existing: list[ResolvedComponent] | None = None,
    removed_component_ids: list[uuid.UUID] | None = None,
    actor_id: uuid.UUID | None = None,
) -> dict | None:
    """Create a new pending AgentVersion with config_additions appended to the prompt.

    Links created and reused components as AgentComponents so the review
    queue enforces approval dependency.
    """
    # Get the latest approved version
    latest_stmt = (
        select(AgentVersion)
        .where(
            AgentVersion.agent_id == agent.id,
            AgentVersion.status == AgentStatus.approved,
        )
        .order_by(AgentVersion.created_at.desc())
        .limit(1)
    )
    latest_result = await db.execute(latest_stmt)
    latest_version = latest_result.scalar_one_or_none()

    if not latest_version:
        optic.warning("self_learn_no_approved_version: agent_id={}", str(agent.id))
        return None

    # Build the new prompt with additions, skipping exact duplicates already present.
    current_prompt = latest_version.prompt or ""
    existing_prompt_lower = current_prompt.lower()
    config_additions = [
        item
        for item in config_additions
        if item.get("addition") and item.get("addition", "").strip().lower() not in existing_prompt_lower
    ]
    additions_text = _build_additions_text(config_additions)

    new_components = new_components or []
    linked_existing = linked_existing or []

    # Even without text additions, we might be linking components
    if not additions_text.strip() and not new_components and not linked_existing and not removed_component_ids:
        return None

    new_prompt = current_prompt
    if additions_text.strip():
        new_prompt = current_prompt + _SELF_LEARN_SEPARATOR + additions_text

    # Bump patch version
    current_ver = latest_version.version or "1.0.0"
    new_ver = bump_version(current_ver, "patch")

    # Ensure no version collision
    dup_stmt = select(AgentVersion).where(
        AgentVersion.agent_id == agent.id,
        AgentVersion.version == new_ver,
    )
    if (await db.execute(dup_stmt)).scalar_one_or_none():
        for _ in range(10):
            new_ver = bump_version(new_ver, "patch")
            dup_stmt = select(AgentVersion).where(
                AgentVersion.agent_id == agent.id,
                AgentVersion.version == new_ver,
            )
            if not (await db.execute(dup_stmt)).scalar_one_or_none():
                break
        else:
            optic.error("self_learn_version_exhaustion: agent_id={}", str(agent.id))
            return None

    total_linked = len(new_components) + len(linked_existing)
    now = datetime.now(UTC)
    new_version = AgentVersion(
        agent_id=agent.id,
        version=new_ver,
        description=latest_version.description,
        prompt=new_prompt,
        model_name=latest_version.model_name,
        model_config_json=latest_version.model_config_json,
        models_by_harness=latest_version.models_by_harness,
        external_mcps=latest_version.external_mcps,
        supported_harnesses=latest_version.supported_harnesses,
        is_prerelease=False,
        status=AgentStatus.pending,
        released_by=submitter_id,
        released_at=now,
    )
    db.add(new_version)
    await db.flush()

    # Copy components from the latest version
    order_idx = 0
    removed_set = set(removed_component_ids or [])
    carried_ids: set[uuid.UUID] = set()
    carried_components = (
        await db.scalars(
            select(AgentComponent)
            .where(AgentComponent.agent_version_id == latest_version.id)
            .order_by(AgentComponent.order_index)
        )
    ).all()
    for comp in carried_components:
        if comp.component_id in removed_set:
            continue
        carried_ids.add(comp.component_id)
        db.add(
            AgentComponent(
                agent_version_id=new_version.id,
                component_type=comp.component_type,
                component_id=comp.component_id,
                component_name=comp.component_name,
                resolved_version=comp.resolved_version,
                resolved_version_id=comp.resolved_version_id,
                resolved_digest=comp.resolved_digest,
                order_index=comp.order_index,
                config_override=comp.config_override,
            )
        )
        order_idx = max(order_idx, (comp.order_index or 0) + 1)

    # Link components created in this run (pinned to the pending release they
    # were born with) and reused registry components (pinned to the release the
    # registry actually has; pinning a version that does not exist breaks pulls).
    #
    # Skip anything the agent already carries. The insights shortlist excludes
    # attached components, but `_find_existing_skill_match` does not, and an
    # agent can gain a component between report generation and apply. Adding it
    # twice would write two AgentComponent rows for one component, at differing
    # resolved_versions.
    created_names = await _component_names(new_components, db)
    refs: list[dict] = [
        {
            "component_type": component.component_type,
            "component_id": component.component_id,
            "component_name": created_names.get(component.component_id, ""),
        }
        for component in new_components
    ]
    for resolved in linked_existing:
        if resolved.id in carried_ids:
            optic.info(
                "self_learn_reuse_already_attached: agent_id={}, component_id={}",
                str(agent.id),
                str(resolved.id),
            )
            continue
        carried_ids.add(resolved.id)
        refs.append(
            {
                "component_type": resolved.component_type,
                "component_id": resolved.id,
                "component_name": resolved.name,
                "version": resolved.latest_version,
            }
        )
    from services.agent_lock import attach_pinned_components, lock_agent_version

    # The components this run created are pending releases submitted under the
    # agent owner's identity, so pin them as that submitter.
    submitter = await db.get(User, submitter_id)
    await attach_pinned_components(db, new_version.id, refs, order_start=order_idx, current_user=submitter)

    await db.flush()
    await _refresh_capability_inference(new_version, db)
    await lock_agent_version(db, agent, new_version)

    # A pending self-learned version sits in the same queue as a hand-released
    # one; the reviewers who will clear it are told the same way.
    await _submit_insights_review(db, "agent", agent, new_version, actor_id)

    return {
        "id": str(new_version.id),
        "version": new_ver,
        "base_version_id": str(latest_version.id),
        "additions_count": len(config_additions),
        "linked_components": total_linked,
        "removed_components": len(removed_component_ids or []),
    }


async def _component_names(components: list[_NewComponent], db: AsyncSession) -> dict[uuid.UUID, str]:
    """Look up display names for freshly created listings."""
    names: dict[uuid.UUID, str] = {}
    by_type: dict[str, list[uuid.UUID]] = {}
    for component in components:
        by_type.setdefault(component.component_type, []).append(component.component_id)

    for component_type, ids in by_type.items():
        models = COMPONENT_MODELS.get(component_type)
        if not models:
            continue
        listing_model = models[0]
        rows = (await db.execute(select(listing_model.id, listing_model.name).where(listing_model.id.in_(ids)))).all()
        for row in rows:
            names[row[0]] = row[1]
    return names


async def _refresh_capability_inference(version: AgentVersion, db: AsyncSession) -> None:
    """Recompute required capabilities for a version we just built.

    The HTTP publish paths do this after every component change; self-learn
    previously did not, so insight-generated versions silently lost the
    harness-compatibility warnings shown at pull time.
    """
    from services.harness_capability_inference import NoCompatibleHarnessError, apply_harness_inference

    components = (
        (await db.execute(select(AgentComponent).where(AgentComponent.agent_version_id == version.id))).scalars().all()
    )

    skill_ids = [c.component_id for c in components if c.component_type == "skill"]
    skill_listings: dict[uuid.UUID, SkillListing] = {}
    if skill_ids:
        rows = (await db.execute(select(SkillListing).where(SkillListing.id.in_(skill_ids)))).scalars().all()
        skill_listings = {listing.id: listing for listing in rows}

    class _VersionProxy:
        """Shape `infer_required_features` expects: components + external_mcps."""

        def __init__(self) -> None:
            self.components = components
            self.external_mcps = version.external_mcps

    try:
        apply_harness_inference(version, _VersionProxy(), skill_listings=skill_listings)
    except NoCompatibleHarnessError:
        # A version no declared harness can run must fail the apply, not be saved half-narrowed.
        raise
    except Exception as e:  # pragma: no cover - inference must never block the apply
        optic.warning(
            "self_learn_capability_inference_failed: version_id={}, error_type={}", str(version.id), type(e).__name__
        )
        raise ValueError("Harness compatibility could not be validated") from None


def _build_additions_text(config_additions: list[dict]) -> str:
    """Build text block from config_additions list."""
    lines = []
    for addition in config_additions:
        text = addition.get("addition", "").strip()
        if not text:
            continue
        where = addition.get("where", "system_prompt")
        why = addition.get("why", "")
        if where in ("system_prompt", "AGENTS.md"):
            if why:
                lines.append(f"# Reason: {why}")
            lines.append(text)
            lines.append("")
    return "\n".join(lines).strip()


def _is_skill_suggestion(feature: dict) -> bool:
    """Check if a features_to_try entry is a skill suggestion."""
    feature_name = (feature.get("feature") or "").lower()
    return "skill" in feature_name or "custom skill" in feature_name


def _is_hook_suggestion(feature: dict) -> bool:
    """Check if a features_to_try entry is a hook suggestion."""
    feature_name = (feature.get("feature") or "").lower()
    return "hook" in feature_name or "lifecycle" in feature_name or "pre-commit" in feature_name


def _is_mcp_suggestion(feature: dict) -> bool:
    """Check if a features_to_try entry is an MCP suggestion (skip these)."""
    feature_name = (feature.get("feature") or "").lower()
    return "mcp" in feature_name or "server" in feature_name


def _derive_name(agent_name: str, label: str, max_len: int = _MAX_NAME_LEN) -> str:
    """Generate a reasonable, readable slug from agent name and label.

    Extracts key action words from the label rather than truncating the
    full text. Aims for names like 'ultra-pi-scope-guard' not
    'ultra-pi-hook-that-checks-whether-planned-file-e'.
    """
    prefix = _slugify(agent_name)[:16].rstrip("-")

    # Extract meaningful words: nouns and verbs, skip filler
    suffix = _extract_keywords(label)
    if not suffix:
        suffix = _slugify(label)[:20]

    available = max_len - len(prefix) - 1
    if available < 4:
        return _slugify(label)[:max_len]
    suffix = suffix[:available].rstrip("-")

    combined = f"{prefix}-{suffix}".rstrip("-")
    return combined or "unnamed"


def _extract_keywords(text: str, max_words: int = 4) -> str:
    """Extract up to max_words meaningful keywords from text for a slug.

    Filters out common stop words and verb fillers to get the
    essence: 'PR review workflow that checks tests' -> 'pr-review-checks-tests'
    """
    stop_words = {
        "a",
        "an",
        "the",
        "that",
        "which",
        "this",
        "with",
        "and",
        "or",
        "for",
        "from",
        "into",
        "onto",
        "upon",
        "about",
        "after",
        "before",
        "during",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "shall",
        "can",
        "to",
        "of",
        "in",
        "on",
        "at",
        "by",
        "up",
        "it",
        "its",
        "custom",
        "performs",
        "whether",
        "your",
        "when",
        "how",
    }
    words = re.findall(r"[a-z]+", text.lower())
    keywords = [w for w in words if w not in stop_words and len(w) > 2]
    return "-".join(keywords[:max_words])


def _slugify(text: str) -> str:
    """Convert text to a valid slug."""
    slug = text.lower().strip()
    # Remove common filler words for brevity
    slug = re.sub(r"\b(custom|for|the|a|an|with|and|from)\b", "", slug)
    slug = re.sub(r"[^a-z0-9\-]", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


def _declared_component_type(feature: dict) -> str | None:
    """Best guess at the component type an LLM suggestion refers to.

    Returns None when the wording is ambiguous; the caller then falls back to
    resolving by id across every type, which is authoritative anyway.
    """
    if _is_skill_suggestion(feature):
        return "skill"
    if _is_hook_suggestion(feature):
        return "hook"
    if _is_mcp_suggestion(feature):
        return "mcp"
    label = f"{feature.get('feature') or ''} {feature.get('name') or ''}".lower()
    if "prompt" in label:
        return "prompt"
    if "sandbox" in label:
        return "sandbox"
    return None


async def _resolve_reusable_component(feature: dict, agent: Agent, db: AsyncSession) -> ResolvedComponent | None:
    """Validate a reuse suggestion against the registry.

    The declared type is a hint only. The id is resolved across every
    component type and the *actual* type wins, so a mislabelled suggestion
    still attaches correctly instead of being silently dropped.

    Scoped to the agent's visibility so a suggestion can never attach another
    user's private component.
    """
    component_id = coerce_uuid(feature.get("existing_component_id"))
    if component_id is None:
        return None

    declared = _declared_component_type(feature)
    ordered_types = (
        [declared, *[t for t in ALL_COMPONENT_TYPES if t != declared]] if declared else list(ALL_COMPONENT_TYPES)
    )
    return await resolve_component_any_type(
        db,
        component_id,
        user_id=agent.created_by,
        component_types=ordered_types,
    )


async def _create_skill_listing(
    agent: Agent,
    feature: dict,
    submitter_id: uuid.UUID,
    db: AsyncSession,
    actor_id: uuid.UUID | None = None,
) -> dict | None:
    """Create a pending SkillListing from a features_to_try suggestion.

    Validates through SkillSubmitRequest and SKILL.md frontmatter validation to
    ensure proper field values.
    Wraps the example in proper SKILL.md frontmatter if not already formatted.
    """
    from pydantic import ValidationError

    from schemas.skill import SkillSubmitRequest
    from services.skill_validator import SkillValidationError, validate_skill_md_content_frontmatter

    one_liner = feature.get("one_liner", "")
    example = feature.get("example", "")

    if not example:
        return None

    # Prefer the LLM-provided name if it's short and valid, else derive one
    raw_name = feature.get("name", "")
    if raw_name and len(raw_name) <= 30 and re.match(r"^[a-z0-9\-]+$", raw_name):
        name = f"{_slugify(agent.name)[:16].rstrip('-')}-{raw_name}"
    else:
        name = _derive_name(agent.name, one_liner or feature.get("feature", "skill"))

    # Build proper SKILL.md content if the example is raw text
    skill_md = _ensure_skill_md_format(name, one_liner, example)

    from observal_shared.harness_registry import HARNESS_REGISTRY

    declared = getattr(agent, "_insights_harnesses", agent.supported_harnesses) or []
    skill_harnesses = [h for h in declared if "skills" in HARNESS_REGISTRY.get(h, {}).get("capabilities", set())]
    if not skill_harnesses:
        raise ValueError("Selected skill is unsupported by every declared agent harness")

    # Validate through Pydantic schema and stored SKILL.md frontmatter rules.
    try:
        validate_skill_md_content_frontmatter(skill_md)
        validated = SkillSubmitRequest(
            name=name,
            version="1.0.0",
            description=one_liner or f"Skill for {agent.name}",
            owner=agent.owner or "",
            skill_md_content=skill_md,
            delivery_mode="registry_direct",
            target_agents=[agent.name],
            task_type="general",
            supported_harnesses=skill_harnesses,
        )
    except (SkillValidationError, ValidationError) as e:
        optic.warning("self_learn_skill_validation_failed: error_type={}", type(e).__name__)
        return None

    # Check for existing
    existing = await db.execute(
        select(SkillListing).where(
            SkillListing.namespace == agent.namespace,
            SkillListing.slug == slugify(name),
        )
    )
    if existing.scalar_one_or_none():
        return None

    listing = SkillListing(
        name=validated.name,
        namespace=agent.namespace,
        slug=slugify(validated.name),
        owner=validated.owner,
        submitted_by=submitter_id,
    )
    db.add(listing)
    await db.flush()

    version = SkillVersion(
        listing_id=listing.id,
        version=validated.version,
        description=validated.description,
        skill_md_content=validated.skill_md_content,
        delivery_mode=validated.delivery_mode,
        target_agents=validated.target_agents,
        task_type=validated.task_type,
        supported_harnesses=validated.supported_harnesses,
        status=ListingStatus.pending,
        released_by=submitter_id,
        released_at=datetime.now(UTC),
    )
    db.add(version)
    await db.flush()

    listing.latest_version_id = version.id

    # Self-learned components enter the same review queue as hand-submitted
    # ones, so the reviewers who own that queue hear about them the same way.
    await _submit_insights_review(db, "skill", listing, version, actor_id)

    return {
        "id": str(listing.id),
        "name": name,
        "description": one_liner,
        "type": "skill",
    }


def _ensure_skill_md_format(name: str, description: str, raw_example: str) -> str:
    """Wrap raw example text in proper SKILL.md frontmatter if missing."""
    if raw_example.strip().startswith("---"):
        return raw_example  # Already has frontmatter

    frontmatter = {
        "name": name,
        "description": description,
        "version": "1.0.0",
        "task_type": "general",
    }
    body = yaml.safe_dump(frontmatter, sort_keys=False, default_flow_style=False, allow_unicode=True).strip()
    return f"""---
{body}
---

# {name}

{raw_example}
"""


async def _create_hook_listing(
    agent: Agent,
    feature: dict,
    submitter_id: uuid.UUID,
    db: AsyncSession,
    actor_id: uuid.UUID | None = None,
) -> dict | None:
    """Create a pending HookListing from a features_to_try suggestion.

    Requires explicit hook metadata and valid Bash through HookSubmitRequest.
    Parse syntax in a worker thread without executing the script; check delivery
    and declared requirements before staging a pending review item.
    """
    from pydantic import ValidationError

    from schemas.hook import HookSubmitRequest

    one_liner = feature.get("one_liner", "")
    example = feature.get("example", "")

    if not example:
        return None

    # Prefer the LLM-provided name if it's short and valid, else derive one
    raw_name = feature.get("name", "")
    if raw_name and len(raw_name) <= 30 and re.match(r"^[a-z0-9\-]+$", raw_name):
        name = f"{_slugify(agent.name)[:16].rstrip('-')}-{raw_name}"
    else:
        name = _derive_name(agent.name, one_liner or feature.get("feature", "hook"))

    # Parse hook metadata from the example
    event, execution_mode, script_content = _parse_hook_example(example)

    # Convert to proper executable script
    script_content = await asyncio.to_thread(_normalize_script, script_content, name)
    script_filename = f"{name}.sh"
    from observal_shared.harness_registry import HARNESS_REGISTRY

    harnesses = getattr(agent, "_insights_harnesses", agent.supported_harnesses) or []
    if not harnesses:
        raise ValueError("Agent must declare harnesses before creating an executable hook")
    from services.harness import ensure_loaded, get_adapter

    ensure_loaded()
    config = {
        "name": name,
        "event": event,
        "handler_type": "command",
        "handler_config": {"inline": True},
        "script_content": script_content,
        "script_filename": script_filename,
    }
    unsupported = []
    for harness in harnesses:
        adapter = get_adapter(harness)
        if adapter is None or adapter.hook_delivery_errors(config, HARNESS_REGISTRY[harness]["default_scope"]):
            unsupported.append(harness if harness in HARNESS_REGISTRY else "unregistered harness")
    if unsupported:
        raise ValueError("Selected script hook cannot be installed for: " + ", ".join(unsupported))
    raw_requirements = feature.get("requirements")
    raw_requirements = [] if raw_requirements is None else raw_requirements
    if not isinstance(raw_requirements, list) or any(
        not isinstance(item, str) or not item.strip() for item in raw_requirements
    ):
        raise ValueError("Hook requirements must be a list of executable dependency names")
    requirements = ["bash", *raw_requirements]
    if re.search(r"\bjq\b", script_content) and "jq" not in requirements:
        raise ValueError("Generated hook uses jq without declaring it in requirements")

    # Validate through Pydantic schema
    try:
        validated = HookSubmitRequest(
            name=name,
            version="1.0.0",
            description=one_liner or f"Hook for {agent.name}",
            owner=agent.owner or "",
            event=event,
            execution_mode=execution_mode,
            priority=100,
            # "script" is not a valid handler_type — only "command" and
            # "http" are (schemas/constants.VALID_HOOK_HANDLER_TYPES). Using
            # it meant every generated hook silently failed validation and
            # was dropped. Inline scripts are delivered as "command".
            handler_type="command",
            handler_config={"inline": True},
            scope="agent",
            script_content=script_content,
            script_filename=script_filename,
            supported_harnesses=harnesses,
            requirements=requirements,
        )
    except ValidationError as e:
        optic.warning("self_learn_hook_validation_failed: error_type={}", type(e).__name__)
        return None

    # Check for existing
    existing = await db.execute(
        select(HookListing).where(
            HookListing.namespace == agent.namespace,
            HookListing.slug == slugify(name),
        )
    )
    if existing.scalar_one_or_none():
        return None

    listing = HookListing(
        name=validated.name,
        namespace=agent.namespace,
        slug=slugify(validated.name),
        owner=validated.owner,
        submitted_by=submitter_id,
    )
    db.add(listing)
    await db.flush()

    version = HookVersion(
        listing_id=listing.id,
        version=validated.version,
        description=validated.description,
        event=validated.event,
        execution_mode=validated.execution_mode,
        priority=validated.priority,
        handler_type=validated.handler_type,
        handler_config=validated.handler_config,
        scope=validated.scope,
        script_content=validated.script_content,
        script_filename=validated.script_filename,
        supported_harnesses=validated.supported_harnesses,
        requirements=validated.requirements,
        status=ListingStatus.pending,
        released_by=submitter_id,
        released_at=datetime.now(UTC),
    )
    db.add(version)
    await db.flush()

    listing.latest_version_id = version.id

    # Same review queue as a hand-submitted hook, so the same notification.
    await _submit_insights_review(db, "hook", listing, version, actor_id)

    return {
        "id": str(listing.id),
        "name": name,
        "description": one_liner,
        "type": "hook",
    }


def _parse_hook_example(example: str) -> tuple[str, str, str]:
    """Require one explicit canonical event or documented alias; never guess from prose."""
    event_map = {
        "before tool use": ("PreToolUse", "sync"),
        "pretooluse": ("PreToolUse", "sync"),
        "after tool use": ("PostToolUse", "async"),
        "posttooluse": ("PostToolUse", "async"),
        "session end": ("Stop", "blocking"),
        "stop": ("Stop", "blocking"),
        "before prompt": ("UserPromptSubmit", "sync"),
        "userpromptsubmit": ("UserPromptSubmit", "sync"),
        "session start": ("SessionStart", "async"),
        "sessionstart": ("SessionStart", "async"),
        "notification": ("Notification", "async"),
        "subagentstop": ("SubagentStop", "async"),
    }
    lines = example.strip().splitlines()
    headers = [line for line in lines if line.lower().strip().startswith("# hook:")]
    if len(headers) != 1:
        raise ValueError("Hook needs exactly one explicit # Hook: event header")
    declared = headers[0].split(":", 1)[1].strip().lower()
    if declared not in event_map:
        raise ValueError("Hook event is unsupported or ambiguous; use a canonical event name")
    event, mode = event_map[declared]
    return event, mode, "\n".join(line for line in lines if line != headers[0]).strip()


def _normalize_script(raw_script: str, name: str) -> str:
    """Accept declared executable source, never turn pseudocode into a successful no-op."""
    import subprocess

    if not raw_script.startswith(("#!/usr/bin/env bash\n", "#!/bin/bash\n")):
        raise ValueError("Generated hooks require a complete Bash script with an explicit shebang")
    try:
        checked = subprocess.run(["bash", "-n"], input=raw_script, text=True, capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("Bash syntax validation is unavailable") from None
    if checked.returncode:
        raise ValueError("Generated hook failed Bash syntax validation")
    return raw_script


async def _create_prompt_listing(
    agent: Agent,
    pattern: dict,
    submitter_id: uuid.UUID,
    db: AsyncSession,
    actor_id: uuid.UUID | None = None,
) -> dict | None:
    """Create a pending PromptListing from a usage_patterns suggestion.

    Validates through PromptSubmitRequest.
    """
    from pydantic import ValidationError

    from schemas.prompt import PromptSubmitRequest

    title = pattern.get("title", "")
    copyable_prompt = pattern.get("copyable_prompt", "")
    detail = pattern.get("detail", "")

    if not copyable_prompt:
        return None

    name = _derive_name(agent.name, title or "prompt")

    # Validate through Pydantic schema
    try:
        validated = PromptSubmitRequest(
            name=name,
            version="1.0.0",
            description=detail or f"Prompt pattern for {agent.name}",
            owner=agent.owner or "",
            category="general",
            template=copyable_prompt,
            variables=[],
            tags=["self-learn", agent.name],
            supported_harnesses=[],
        )
    except ValidationError as e:
        optic.warning("self_learn_prompt_validation_failed: error_type={}", type(e).__name__)
        return None

    # Check for existing
    existing = await db.execute(
        select(PromptListing).where(
            PromptListing.namespace == agent.namespace,
            PromptListing.slug == slugify(name),
        )
    )
    if existing.scalar_one_or_none():
        return None

    listing = PromptListing(
        name=validated.name,
        namespace=agent.namespace,
        slug=slugify(validated.name),
        owner=validated.owner,
        submitted_by=submitter_id,
    )
    db.add(listing)
    await db.flush()

    version = PromptVersion(
        listing_id=listing.id,
        version=validated.version,
        description=validated.description,
        category=validated.category,
        template=validated.template,
        variables=validated.variables,
        tags=validated.tags,
        status=ListingStatus.pending,
        released_by=submitter_id,
        released_at=datetime.now(UTC),
    )
    db.add(version)
    await db.flush()

    listing.latest_version_id = version.id

    # Same review queue as a hand-submitted prompt, so the same notification.
    await _submit_insights_review(db, "prompt", listing, version, actor_id)

    return {
        "id": str(listing.id),
        "name": name,
        "description": detail or title,
        "type": "prompt",
    }


async def _submit_insights_review(db, subject_type, subject, version, actor_id):
    """Stage a normal revision with durable provenance before any auto-publication check."""
    review = await open_or_push(db, subject_type, subject, version, actor_id)
    db.add(ReviewEvent(review_id=review.id, kind="insights_generated", actor_id=actor_id))
    await db.flush()
    await sync_state(db, review, actor_id=actor_id)
    await deliver_event(db, review, "opened", actor_id, revision=1)
    return review


async def apply_insight_suggestions(report_id, db, triggered_by, selection=None, *, preview_digest=None):
    """Submit selected insight artifacts through the shared deterministic preflight builder."""
    from services.insights.apply_preview import submit_suggestions

    return await submit_suggestions(report_id, db, triggered_by, selection, preview_digest=preview_digest)


async def preview_insight_suggestions(report_id, db, triggered_by, selection):
    """Preview the shared proposal builder without retaining staged writes."""
    from services.insights.apply_preview import preview_suggestions

    return await preview_suggestions(report_id, db, triggered_by, selection)
