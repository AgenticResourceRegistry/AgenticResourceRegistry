# SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Agent composition resolver - looks up and validates all components for an agent."""

import uuid
from typing import Literal

from loguru import logger as optic
from pydantic import BaseModel, Field, computed_field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import apply_publish_scope, apply_visibility_filter
from models.agent import Agent
from models.hook import HookListing
from models.mcp import ListingStatus, McpListing
from models.prompt import PromptListing
from models.sandbox import SandboxListing
from models.skill import SkillListing
from services.shared.utils import registry_item_slug

ComponentType = Literal["mcp", "skill", "hook", "prompt", "sandbox"]

# Maps component_type string to its ORM model
_LISTING_MODELS: dict[str, type] = {
    "mcp": McpListing,
    "skill": SkillListing,
    "hook": HookListing,
    "prompt": PromptListing,
    "sandbox": SandboxListing,
}


class ResolvedComponent(BaseModel):
    """A fully resolved component with its listing data."""

    model_config = {"frozen": True}

    component_type: ComponentType
    component_id: uuid.UUID
    name: str
    version: str
    git_url: str | None = None
    git_ref: str | None = None
    description: str = ""
    order_index: int = 0
    config_override: dict | None = None
    listing_status: str = ""
    extra: dict = Field(default_factory=dict)


class ResolutionError(BaseModel):
    """A single resolution failure."""

    model_config = {"frozen": True}

    component_type: str
    component_id: uuid.UUID
    reason: str


class ResolvedAgent(BaseModel):
    """Complete resolution result for an agent."""

    agent_id: uuid.UUID
    agent_name: str
    agent_version: str
    agent_prompt: str = ""
    agent_description: str = ""
    model_name: str = ""
    models_by_harness: dict[str, str] = Field(default_factory=dict)
    components: list[ResolvedComponent] = Field(default_factory=list)
    errors: list[ResolutionError] = Field(default_factory=list)

    @computed_field
    @property
    def ok(self) -> bool:

        return len(self.errors) == 0

    def components_by_type(self, component_type: str) -> list[ResolvedComponent]:
        optic.trace("filtering components by type {} ({} total)", component_type, len(self.components))
        return [c for c in self.components if c.component_type == component_type]


def _extract_extra(listing, component_type: str) -> dict:
    """Pull type-specific fields from a listing into a flat dict for downstream use."""
    optic.trace("extracting {} metadata from listing", component_type)
    if component_type == "mcp":
        return {
            "transport": getattr(listing, "transport", None),
            "tools_schema": getattr(listing, "tools_schema", None),
            "mcp_validated": getattr(listing, "mcp_validated", False),
            "setup_instructions": getattr(listing, "setup_instructions", None),
        }
    if component_type == "skill":
        return {
            "skill_path": getattr(listing, "skill_path", "/"),
            "task_type": getattr(listing, "task_type", ""),
            "slash_command": getattr(listing, "slash_command", None),
            "skill_md_content": getattr(listing, "skill_md_content", None),
        }
    if component_type == "hook":
        extra = {
            "event": getattr(listing, "event", ""),
            "execution_mode": getattr(listing, "execution_mode", "async"),
            "priority": getattr(listing, "priority", 100),
            "handler_type": getattr(listing, "handler_type", ""),
            "handler_config": getattr(listing, "handler_config", {}),
            "scope": getattr(listing, "scope", "agent"),
        }
        if getattr(listing, "source_url", None):
            extra["source_url"] = listing.source_url
            extra["source_ref"] = getattr(listing, "source_ref", None)
            extra["resolved_sha"] = getattr(listing, "resolved_sha", None)
        if getattr(listing, "script_filename", None):
            extra["script_filename"] = listing.script_filename
        if getattr(listing, "requirements", None):
            extra["requirements"] = listing.requirements
        return extra
    if component_type == "prompt":
        return {
            "template": getattr(listing, "template", ""),
            "variables": getattr(listing, "variables", []),
            "category": getattr(listing, "category", ""),
        }
    if component_type == "sandbox":
        extra = {
            "runtime_type": getattr(listing, "runtime_type", ""),
            "image": getattr(listing, "image", ""),
            "resource_limits": getattr(listing, "resource_limits", {}),
            "network_policy": getattr(listing, "network_policy", "none"),
            "entrypoint": getattr(listing, "entrypoint", None),
            "runtime_config": getattr(listing, "runtime_config", {}),
        }
        if getattr(listing, "sandbox_path", None):
            extra["sandbox_path"] = listing.sandbox_path
        return extra
    return {}


async def resolve_agent(
    agent: Agent,
    db: AsyncSession,
    *,
    require_approved: bool = True,
    current_user=None,
) -> ResolvedAgent:
    """Resolve all components for an agent.

    Looks up each AgentComponent's listing in the correct table,
    validates status, and returns a ResolvedAgent with full details.

    Components are batched by type so each type requires at most one
    SELECT ... WHERE id IN (...) query, regardless of how many components
    of that type exist.
    """
    optic.debug("resolving agent components (require_approved={})", require_approved)
    components: list[ResolvedComponent] = []
    errors: list[ResolutionError] = []

    # Group components by type for batched lookups (max 5 queries total)
    by_type: dict[str, list] = {}
    for comp in agent.components:
        model = _LISTING_MODELS.get(comp.component_type)
        if model is None:
            errors.append(
                ResolutionError(
                    component_type=comp.component_type,
                    component_id=comp.component_id,
                    reason=f"Unknown component type: {comp.component_type}",
                )
            )
            continue
        by_type.setdefault(comp.component_type, []).append(comp)

    # Fetch all listings per type in one query each
    found: dict[uuid.UUID, object] = {}
    for comp_type, comps in by_type.items():
        model = _LISTING_MODELS[comp_type]
        ids = [c.component_id for c in comps]
        stmt = select(model).where(model.id.in_(ids))
        if current_user is not None:
            stmt = apply_visibility_filter(stmt, model, current_user)
        stmt = apply_publish_scope(stmt, model, agent.team_id if agent.is_private else None)
        result = await db.execute(stmt)
        for listing in result.scalars().all():
            found[listing.id] = listing

    # Process in original order to preserve deterministic output
    for comp in agent.components:
        if comp.component_type not in _LISTING_MODELS:
            continue  # Already recorded as error above

        listing = found.get(comp.component_id)
        if listing is None:
            errors.append(
                ResolutionError(
                    component_type=comp.component_type,
                    component_id=comp.component_id,
                    reason=f"{comp.component_type} listing {comp.component_id} not found",
                )
            )
            continue

        if require_approved and listing.status != ListingStatus.approved:
            errors.append(
                ResolutionError(
                    component_type=comp.component_type,
                    component_id=comp.component_id,
                    reason=f"{comp.component_type} '{listing.name}' is not approved (status: {listing.status.value})",
                )
            )
            continue

        components.append(
            ResolvedComponent(
                component_type=comp.component_type,
                component_id=comp.component_id,
                name=registry_item_slug(listing),
                version=listing.version,
                git_url=getattr(listing, "git_url", None),
                git_ref=getattr(listing, "git_ref", None),
                description=listing.description,
                order_index=comp.order_index,
                config_override=comp.config_override,
                listing_status=listing.status.value,
                extra=_extract_extra(listing, comp.component_type),
            )
        )

    raw_models_by_harness = getattr(agent, "models_by_harness", None)
    models_by_harness = raw_models_by_harness if isinstance(raw_models_by_harness, dict) else {}
    return ResolvedAgent(
        agent_id=agent.id,
        agent_name=registry_item_slug(agent),
        agent_version=agent.version,
        agent_prompt=agent.prompt or "",
        agent_description=agent.description or "",
        model_name=agent.model_name or "",
        models_by_harness=models_by_harness,
        components=components,
        errors=errors,
    )


async def validate_component_ids(
    components: list[dict],
    db: AsyncSession,
    *,
    require_approved: bool = True,
    current_user=None,
    target_team_id: uuid.UUID | None = None,
    enforce_target: bool = False,
) -> list[ResolutionError]:
    """Validate a list of component references before attaching them to an agent.

    Each dict should have 'component_type' and 'component_id' keys.
    Returns a list of errors (empty if all valid).
    """
    optic.debug("validating {} component references", len(components))
    errors = []
    for ref in components:
        ctype = ref.get("component_type", "")
        cid = ref.get("component_id")
        if cid is None:
            errors.append(
                ResolutionError(
                    component_type=ctype,
                    component_id=uuid.UUID(int=0),
                    reason=f"Missing component_id for {ctype}",
                )
            )
            continue

        model = _LISTING_MODELS.get(ctype)
        if model is None:
            errors.append(
                ResolutionError(
                    component_type=ctype,
                    component_id=cid,
                    reason=f"Unknown component type: {ctype}",
                )
            )
            continue

        stmt = select(model).where(model.id == cid)
        if current_user is not None:
            stmt = apply_visibility_filter(stmt, model, current_user)
        if enforce_target:
            stmt = apply_publish_scope(stmt, model, target_team_id)
        listing = (await db.execute(stmt)).scalar_one_or_none()

        if listing is None:
            errors.append(
                ResolutionError(
                    component_type=ctype,
                    component_id=cid,
                    reason=f"{ctype} listing {cid} not found",
                )
            )
            continue

        if require_approved and listing.status != ListingStatus.approved:
            errors.append(
                ResolutionError(
                    component_type=ctype,
                    component_id=cid,
                    reason=f"{ctype} '{listing.name}' is not approved (status: {listing.status.value})",
                )
            )

    return errors
