# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Shared helpers for agent route sub-modules."""

import uuid

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import (
    apply_publish_scope,
    apply_visibility_filter,
    check_listing_visibility_async,
    get_effective_agent_permission,
    may_view_unapproved,
    resolve_prefix_id,
)
from models.agent import Agent, AgentStatus, AgentVersion
from models.mcp import ListingStatus, McpListing, McpVersion
from schemas.agent import (
    AgentResponse,
    ComponentLinkResponse,
    McpLinkResponse,
)
from services.registry_namespace import _namespace_slug_parts
from services.shared.utils import registry_item_slug

_VISIBILITY_UNSET = object()


async def _load_agent(
    db: AsyncSession,
    agent_id: str,
    extra_conditions=None,
    *,
    prefer_user_id: uuid.UUID | None = None,
    current_user=_VISIBILITY_UNSET,
    include_all_statuses: bool = False,
    include_deleted: bool = False,
) -> Agent | None:
    """Load an agent by UUID, prefix, or name with eager loading.

    When *prefer_user_id* is provided and resolution is by name, prefer the
    caller's own agent over agents created by other users with the same name.
    The global name fallback is restricted to active agents and the caller's
    visibility scope when *current_user* is supplied.

    Set *include_all_statuses* to find agents regardless of version status
    (needed for unarchive, delete, etc.).
    """
    conditions = list(extra_conditions or [])
    if not include_deleted:
        conditions.append(Agent.deleted_at.is_(None))

    try:
        agent = await resolve_prefix_id(Agent, agent_id, db, extra_conditions=conditions)
        if current_user is not _VISIBILITY_UNSET and not await check_listing_visibility_async(agent, current_user, db):
            return None
        # The name branch below gates on approved status; this one has to as well.
        # Otherwise a UUID or prefix reads an agent whose version is pending, which
        # is exactly the state a team-private agent lands in the moment it is made
        # public, and its prompt would be readable before any reviewer saw it.
        if not include_all_statuses and agent.status != AgentStatus.approved:
            owner_id = getattr(agent, "created_by", None)
            caller = None if current_user is _VISIBILITY_UNSET else current_user
            permission = get_effective_agent_permission(agent, caller)
            if not may_view_unapproved(permission, caller) and owner_id != prefer_user_id:
                return None
        return agent
    except HTTPException:
        pass

    parts = _namespace_slug_parts(agent_id)
    identity_filter = (
        (Agent.namespace == parts[0]) & (Agent.slug == parts[1])
        if parts
        else or_(Agent.slug == agent_id.lower(), Agent.name == agent_id)
    )
    stmt = select(Agent).join(AgentVersion, Agent.latest_version_id == AgentVersion.id).where(identity_filter)
    if not include_all_statuses:
        visible_status = AgentVersion.status == AgentStatus.approved
        if prefer_user_id is not None:
            visible_status = visible_status | (Agent.created_by == prefer_user_id)
        stmt = stmt.where(visible_status)
    if conditions:
        stmt = stmt.where(*conditions)
    if current_user is not _VISIBILITY_UNSET:
        stmt = apply_visibility_filter(stmt, Agent, current_user)
    results = (await db.execute(stmt.limit(2))).scalars().all()
    if not parts and len(results) > 1:
        choices = ", ".join(item.qualified_name for item in results)
        raise HTTPException(status_code=409, detail=f"'{agent_id}' is ambiguous; use one of: {choices}")
    return results[0] if results else None


def _agent_to_response(
    agent: Agent,
    name_map: dict[str, str] | None = None,
    *,
    created_by_email: str = "",
    created_by_username: str | None = None,
    user_permission: str | None = None,
    status_map: dict[str, str] | None = None,
    identity_map: dict[str, tuple[str, str]] | None = None,
    forked_from: dict | None = None,
    fork_count: int = 0,
    warnings: list[str] | None = None,
) -> AgentResponse:
    name_map = name_map or {}
    status_map = status_map or {}
    identity_map = identity_map or {}
    # Build mcp_links from components with component_type='mcp' (backwards compat)
    mcp_components = [c for c in agent.components if c.component_type == "mcp"]
    mcp_links = [
        McpLinkResponse(
            mcp_listing_id=comp.component_id,
            mcp_name=name_map.get(str(comp.component_id), "(component)"),
            order=comp.order_index,
        )
        for comp in mcp_components
    ]
    # Build full component_links for all types
    component_links = []
    for comp in agent.components:
        component_id = str(comp.component_id)
        identity = identity_map.get(component_id)
        component_links.append(
            ComponentLinkResponse(
                component_type=comp.component_type,
                component_id=comp.component_id,
                component_name=name_map.get(component_id, ""),
                namespace=identity[0] if identity else "",
                slug=identity[1] if identity else "",
                qualified_name=f"{identity[0]}/{identity[1]}" if identity else "",
                version_ref=comp.resolved_version,
                order=comp.order_index,
                config_override=comp.config_override,
                status=status_map.get(component_id),
            )
        )
    # Build agent_dict from table columns plus version-delegate properties.
    agent_dict = {c.key: getattr(agent, c.key) for c in Agent.__table__.columns}
    for field in (
        "version",
        "description",
        "prompt",
        "model_name",
        "model_config_json",
        "models_by_harness",
        "external_mcps",
        "supported_harnesses",
        "required_capabilities",
        "inferred_supported_harnesses",
        "status",
        "rejection_reason",
        "visibility",
        "success_criteria",
    ):
        agent_dict[field] = getattr(agent, field)
    if not isinstance(agent_dict.get("models_by_harness"), dict):
        agent_dict["models_by_harness"] = {}
    if not isinstance(agent_dict.get("team_id"), uuid.UUID):
        agent_dict["team_id"] = None
    if agent_dict.get("visibility") not in ("public", "team"):
        agent_dict["visibility"] = "team" if agent_dict.get("is_private") is True else "public"
    if not isinstance(agent_dict.get("is_private"), bool):
        agent_dict["is_private"] = False
    namespace = agent_dict.get("namespace")
    if not isinstance(namespace, str):
        namespace = created_by_username or str(agent.owner)
    slug = registry_item_slug(agent)
    agent_dict["namespace"] = namespace
    agent_dict["slug"] = slug
    agent_dict["qualified_name"] = f"{namespace}/{slug}"
    agent_dict["mcp_links"] = mcp_links
    agent_dict["component_links"] = component_links
    agent_dict["created_by_email"] = created_by_email
    agent_dict["created_by_username"] = created_by_username
    agent_dict["user_permission"] = user_permission
    agent_dict["forked_from"] = forked_from
    agent_dict["fork_count"] = fork_count
    agent_dict["warnings"] = warnings or []
    # Populate version fields for CLI pull resolution
    approved_versions = [
        v for v in getattr(agent, "versions", []) if getattr(v, "status", None) == AgentStatus.approved
    ]
    latest_approved = max(approved_versions, key=lambda v: v.created_at) if approved_versions else None
    agent_dict["latest_approved_version"] = latest_approved.version if latest_approved else None
    agent_dict["latest_version"] = agent.version if agent.version != "0.0.0" else None
    return AgentResponse(**agent_dict)


async def _resolve_component_names(components: list, db: AsyncSession) -> dict[str, str]:
    """Batch-resolve component_id -> name for all component types."""
    if not components:
        return {}
    from services.agent_resolver import _LISTING_MODELS

    by_type: dict[str, list[uuid.UUID]] = {}
    for comp in components:
        by_type.setdefault(comp.component_type, []).append(comp.component_id)

    name_map: dict[str, str] = {}
    for comp_type, ids in by_type.items():
        model = _LISTING_MODELS.get(comp_type)
        if not model:
            continue
        rows = (await db.execute(select(model.id, model.name).where(model.id.in_(ids)))).all()
        for row in rows:
            name_map[str(row[0])] = row[1]
    return name_map


async def _resolve_component_identities(components: list, db: AsyncSession) -> dict[str, tuple[str, str]]:
    """Batch-resolve component IDs to canonical namespace and slug pairs."""
    if not components:
        return {}
    from services.agent_resolver import _LISTING_MODELS

    by_type: dict[str, list[uuid.UUID]] = {}
    for comp in components:
        by_type.setdefault(comp.component_type, []).append(comp.component_id)

    identity_map: dict[str, tuple[str, str]] = {}
    for comp_type, ids in by_type.items():
        model = _LISTING_MODELS.get(comp_type)
        if not model:
            continue
        rows = (await db.execute(select(model.id, model.namespace, model.slug).where(model.id.in_(ids)))).all()
        for component_id, namespace, slug in rows:
            identity_map[str(component_id)] = (namespace, slug)
    return identity_map


async def _resolve_component_statuses(components: list, db: AsyncSession) -> dict[str, str]:
    """Batch-resolve component_id to current listing status for all component types."""
    if not components:
        return {}
    from models.hook import HookVersion
    from models.mcp import McpVersion
    from models.prompt import PromptVersion
    from models.sandbox import SandboxVersion
    from models.skill import SkillVersion
    from services.agent_resolver import _LISTING_MODELS

    version_models = {
        "mcp": McpVersion,
        "skill": SkillVersion,
        "hook": HookVersion,
        "prompt": PromptVersion,
        "sandbox": SandboxVersion,
    }

    by_type: dict[str, list[uuid.UUID]] = {}
    for comp in components:
        by_type.setdefault(comp.component_type, []).append(comp.component_id)

    status_map: dict[str, str] = {}
    for comp_type, ids in by_type.items():
        model = _LISTING_MODELS.get(comp_type)
        version_model = version_models.get(comp_type)
        if not model or not version_model:
            continue
        rows = (
            await db.execute(
                select(model.id, version_model.status)
                .join(version_model, model.latest_version_id == version_model.id)
                .where(model.id.in_(ids))
            )
        ).all()
        for row in rows:
            status_map[str(row[0])] = getattr(row[1], "value", str(row[1]))
    return status_map


async def _validate_mcp_ids(
    mcp_ids: list[uuid.UUID],
    db: AsyncSession,
    *,
    current_user=None,
    target_team_id: uuid.UUID | None = None,
    enforce_target: bool = False,
) -> list[McpListing]:
    listings = []
    for mid in mcp_ids:
        stmt = (
            select(McpListing)
            .join(McpVersion, McpListing.latest_version_id == McpVersion.id)
            .where(McpListing.id == mid, McpVersion.status == ListingStatus.approved)
        )
        if current_user is not None:
            stmt = apply_visibility_filter(stmt, McpListing, current_user)
        if enforce_target:
            stmt = apply_publish_scope(stmt, McpListing, target_team_id)
        result = await db.execute(stmt)
        listing = result.scalar_one_or_none()
        if not listing:
            raise HTTPException(status_code=400, detail=f"MCP server {mid} not found or not approved")
        listings.append(listing)
    return listings
