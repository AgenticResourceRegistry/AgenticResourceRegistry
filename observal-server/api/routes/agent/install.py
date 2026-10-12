# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Agent install, download stats, traces, resolve, manifest, and validate routes."""

from fastapi import Depends, HTTPException, Query, Request
from loguru import logger as optic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import services.dynamic_settings as _ds
from api.deps import (
    apply_publish_scope,
    apply_visibility_filter,
    check_listing_visibility_async,
    get_db,
    get_effective_agent_permission,
    get_registry_user,
    require_role,
)
from api.routes._component_archive import archived_install_warning
from models.agent import AgentStatus
from models.hook import HookListing
from models.mcp import ListingStatus, McpListing
from models.prompt import PromptListing
from models.sandbox import SandboxListing
from models.skill import SkillListing
from models.user import User, UserRole
from schemas.agent import (
    AgentInstallRequest,
    AgentInstallResponse,
    AgentValidateRequest,
    ValidationIssue,
    ValidationResult,
)
from services.harness import generate_agent_config
from services.registry_telemetry import emit_registry_event
from services.skill_bundle import (
    SKILL_EXTRA_FILES_FEATURE,
    declared_skill_folder_name,
    needs_bundle_delivery,
    prepare_agent_skill_folders,
)
from services.skill_validator import SkillValidationError

from ._router import router
from .helpers import _load_agent, _resolve_component_names


@router.post("/{agent_id}/install", response_model=AgentInstallResponse)
async def install_agent(
    agent_id: str,
    req: AgentInstallRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_registry_user),
):
    optic.debug("installing agent")
    agent = await _load_agent(
        db,
        agent_id,
        prefer_user_id=current_user.id if current_user else None,
        current_user=current_user,
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.status != AgentStatus.approved and not (
        current_user is not None
        and _ds.get_sync_bool("security.allow_draft_install")
        and agent.created_by == current_user.id
    ):
        raise HTTPException(status_code=404, detail="Agent not found or not approved for installation")
    if get_effective_agent_permission(agent, current_user) == "none":
        raise HTTPException(status_code=403, detail="Insufficient permissions to install this agent")

    # Resolve specific version if requested, otherwise use latest
    if req.version:
        from models.agent import AgentVersion

        version_stmt = select(AgentVersion).where(
            AgentVersion.agent_id == agent.id,
            AgentVersion.version == req.version,
            AgentVersion.status == AgentStatus.approved,
        )
        version_result = await db.execute(version_stmt)
        target_version = version_result.scalar_one_or_none()
        if not target_version:
            raise HTTPException(
                status_code=404,
                detail=f"Version {req.version!r} not found or not approved for this agent",
            )
        install_version = target_version
    elif not agent.latest_version:
        raise HTTPException(status_code=400, detail="Agent has no published version available for install")
    else:
        install_version = agent.latest_version

    install_components = list(install_version.components or [])
    component_target_team_id = agent.team_id if agent.is_private else None

    class _InstallAgentProxy:
        _version_fields = {
            "components",
            "description",
            "external_mcps",
            "inferred_supported_harnesses",
            "model_config_json",
            "model_name",
            "models_by_harness",
            "prompt",
            "required_capabilities",
            "supported_harnesses",
            "version",
        }

        def __getattr__(self, name):
            if name == "components":
                return install_components
            if name in self._version_fields:
                return getattr(install_version, name)
            return getattr(agent, name)

    install_agent_obj = _InstallAgentProxy()

    # Pre-load MCP listings for config generation
    mcp_comp_ids = [c.component_id for c in install_components if c.component_type == "mcp"]
    mcp_listings_map = {}
    if mcp_comp_ids:
        mcp_stmt = apply_publish_scope(
            apply_visibility_filter(
                select(McpListing).where(McpListing.id.in_(mcp_comp_ids)), McpListing, current_user
            ),
            McpListing,
            component_target_team_id,
        )
        mcp_rows = (await db.execute(mcp_stmt)).scalars().all()
        mcp_listings_map = {row.id: row for row in mcp_rows}

    # Pre-load skill listings for skill file generation
    skill_comp_ids = [c.component_id for c in install_components if c.component_type == "skill"]
    skill_listings_map = {}
    if skill_comp_ids:
        # Serialize against skill privatization, re-review and decisions. Those
        # writers take listing FOR UPDATE before touching versions; re-run the
        # visibility query after the wait, not against a cached ORM instance.
        await db.execute(
            select(SkillListing.id)
            .where(SkillListing.id.in_(skill_comp_ids))
            .order_by(SkillListing.id)
            .with_for_update(read=True)
        )
        skill_stmt = apply_publish_scope(
            apply_visibility_filter(
                select(SkillListing).where(SkillListing.id.in_(skill_comp_ids)), SkillListing, current_user
            ),
            SkillListing,
            component_target_team_id,
        )
        skill_rows = (await db.execute(skill_stmt.execution_options(populate_existing=True))).scalars().all()
        # EXISTS cannot hold the membership row: deletion can race after the
        # query returned. A shared grant lock protects pinned bytes until the
        # installer transaction completes, or observes a finished revocation.
        skill_listings_map = {
            row.id: row for row in skill_rows if await check_listing_visibility_async(row, current_user, db)
        }
        if set(skill_comp_ids) - skill_listings_map.keys():
            raise HTTPException(status_code=404, detail="Agent references an unavailable skill")

    # Pre-load hook listings for hook config generation
    hook_comp_ids = [c.component_id for c in install_components if c.component_type == "hook"]
    hook_listings_map = {}
    if hook_comp_ids:
        hook_rows = (
            (
                await db.execute(
                    apply_publish_scope(
                        apply_visibility_filter(
                            select(HookListing)
                            .options(selectinload(HookListing.latest_version))
                            .where(HookListing.id.in_(hook_comp_ids)),
                            HookListing,
                            current_user,
                        ),
                        HookListing,
                        component_target_team_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        hook_listings_map = {row.id: row for row in hook_rows}

    # Pre-load prompt listings for template injection
    prompt_comp_ids = [c.component_id for c in install_components if c.component_type == "prompt"]
    prompt_listings_map = {}
    if prompt_comp_ids:
        prompt_rows = (
            (
                await db.execute(
                    apply_publish_scope(
                        apply_visibility_filter(
                            select(PromptListing)
                            .options(selectinload(PromptListing.latest_version))
                            .where(PromptListing.id.in_(prompt_comp_ids)),
                            PromptListing,
                            current_user,
                        ),
                        PromptListing,
                        component_target_team_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        prompt_listings_map = {row.id: row for row in prompt_rows}

    # Pre-load sandbox listings for rules content injection
    sandbox_comp_ids = [c.component_id for c in install_components if c.component_type == "sandbox"]
    sandbox_listings_map = {}
    if sandbox_comp_ids:
        sandbox_rows = (
            (
                await db.execute(
                    apply_publish_scope(
                        apply_visibility_filter(
                            select(SandboxListing)
                            .options(selectinload(SandboxListing.latest_version))
                            .where(SandboxListing.id.in_(sandbox_comp_ids)),
                            SandboxListing,
                            current_user,
                        ),
                        SandboxListing,
                        component_target_team_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        sandbox_listings_map = {row.id: row for row in sandbox_rows}

    component_maps = (
        (mcp_comp_ids, mcp_listings_map),
        (skill_comp_ids, skill_listings_map),
        (hook_comp_ids, hook_listings_map),
        (prompt_comp_ids, prompt_listings_map),
        (sandbox_comp_ids, sandbox_listings_map),
    )
    if any(set(ids) - set(listings) for ids, listings in component_maps):
        raise HTTPException(status_code=404, detail="Agent contains a component unavailable to this agent target")

    # Generate every component from the exact version this agent release pinned,
    # never from the listing's latest release.
    from services.agent_lock import LOCK_VERSION, load_pinned_listings, stored_lock_digest

    pins = await load_pinned_listings(
        db,
        install_components,
        {
            "mcp": mcp_listings_map,
            "skill": skill_listings_map,
            "hook": hook_listings_map,
            "prompt": prompt_listings_map,
            "sandbox": sandbox_listings_map,
        },
    )
    if req.strict and pins.problems:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Strict install refused: {'; '.join(pins.problems)}. Install without strict mode to accept "
                "these with warnings, or ask the agent author to release a new version."
            ),
        )
    # Non-strict mode is only a compatibility fallback for old resource-less
    # locks. It must never publish an unapproved or public-re-review skill's
    # bytes, even when its bundle has no extra files and the caller owns it.
    from services.agent_lock import INSTALLABLE_STATUSES

    if any(
        (selected := getattr(row, "pinned_version", None) or getattr(row, "latest_version", None)) is None
        or selected.status not in INSTALLABLE_STATUSES
        or getattr(selected, "requires_global_review", False)
        for row in pins.listings["skill"].values()
    ):
        raise HTTPException(status_code=409, detail="Agent skill pin is not approved or requires public review")
    # The pinned proxy, not the listing pointer, selects each complete folder.
    bundled_skills = any(needs_bundle_delivery(row) for row in pins.listings["skill"].values())
    if bundled_skills and SKILL_EXTRA_FILES_FEATURE not in req.supported_features:
        raise HTTPException(status_code=409, detail="Client must support skill_extra_files_v1 to install this agent")
    if bundled_skills and not await _ds.get_bool("registry.skill_folder_delivery_enabled", False):
        raise HTTPException(status_code=409, detail="Skill folder delivery is disabled until fleet rollout")
    lock_digest = stored_lock_digest(install_version)
    mcp_listings_map = pins.listings["mcp"]
    skill_listings_map = pins.listings["skill"]
    hook_listings_map = pins.listings["hook"]
    prompt_listings_map = pins.listings["prompt"]
    sandbox_listings_map = pins.listings["sandbox"]
    skill_folder_names = {}
    if SKILL_EXTRA_FILES_FEATURE in req.supported_features:
        for listing_id, row in skill_listings_map.items():
            if not row.extra_files:
                continue  # Resource-less and legacy empty-script releases keep their aliases.
            try:
                skill_folder_names[listing_id] = declared_skill_folder_name(row.skill_md_content)
            except SkillValidationError as exc:
                raise HTTPException(status_code=409, detail="Pinned skill has no valid installed folder name") from exc

    archived_warnings = []
    setup_warnings = []
    for item_type, rows in (
        ("MCP", mcp_listings_map.values()),
        ("skill", skill_listings_map.values()),
        ("hook", hook_listings_map.values()),
        ("prompt", prompt_listings_map.values()),
        ("sandbox", sandbox_listings_map.values()),
    ):
        for row in rows:
            if getattr(row, "listing_status", row.status) == ListingStatus.archived:
                archived_warnings.append(archived_install_warning(item_type, row.name))
            if item_type == "MCP" and row.setup_instructions:
                setup_warnings.append(f"MCP '{row.name}' requires local setup before use:\n{row.setup_instructions}")

    # Resolve all component names for rules file content
    name_map = await _resolve_component_names(install_components, db)

    from api.routes.config import derive_endpoints
    from services.model_resolver import resolve_model_for_harness

    endpoints = await derive_endpoints(request)

    install_options: dict = dict(req.options or {})
    raw_override = install_options.get("model") or None
    if isinstance(raw_override, str) and raw_override.strip().lower() == "inherit":
        raw_override = None
    resolved_model, model_warnings = await resolve_model_for_harness(
        req.harness,
        model_name=getattr(agent, "model_name", "") or "",
        models_by_harness=getattr(agent, "models_by_harness", {}) or {},
        override=raw_override,
    )
    install_options["_resolved_model"] = resolved_model
    install_options["_model_warnings"] = model_warnings
    install_options["_delegation"] = _ds.get_sync_bool("discovery.delegation_enabled", True)

    snippet = generate_agent_config(
        install_agent_obj,
        req.harness,
        observal_url=endpoints["api"],
        mcp_listings=mcp_listings_map,
        component_names=name_map,
        env_values=req.env_values,
        header_values=req.header_values,
        options=install_options,
        platform=req.platform,
        skill_listings=skill_listings_map,
        hook_listings=hook_listings_map,
        prompt_listings=prompt_listings_map,
        sandbox_listings=sandbox_listings_map,
        skill_folder_names=skill_folder_names,
    )

    try:
        skill_bundles = (
            prepare_agent_skill_folders(
                skill_listings_map,
                snippet,
                req.harness,
                scope=req.options.get("scope"),
                folder_names=skill_folder_names,
            )
            if SKILL_EXTRA_FILES_FEATURE in req.supported_features
            else []
        )
    except SkillValidationError as exc:
        raise HTTPException(status_code=409, detail=f"Cannot deliver complete agent skill folders: {exc}") from exc

    # Capture agent.id before any DB operations that might expire the ORM
    # instance (e.g. savepoint rollback on duplicate download).
    resolved_agent_id = agent.id

    if current_user is not None:
        from services.download_tracker import record_agent_download

        await record_agent_download(
            agent_id=resolved_agent_id,
            user_id=current_user.id,
            source="api",
            harness=req.harness,
            request=request,
            db=db,
        )
        await db.commit()

        emit_registry_event(
            action="agent.install",
            user_id=str(current_user.id),
            user_email=current_user.email,
            user_role=current_user.role.value,
            agent_id=str(resolved_agent_id),
            resource_name=agent.name,
            metadata={
                "harness": req.harness,
                "version": install_version.version,
                "lock_status": pins.status,
                "lock_digest": lock_digest or "",
            },
        )

    warnings = pins.warnings + archived_warnings + setup_warnings + snippet.pop("_warnings", [])
    lock = {
        "lock_version": LOCK_VERSION,
        "status": pins.status,
        "digest": lock_digest,
        "components": pins.entries,
        "problems": pins.problems,
    }
    return AgentInstallResponse(
        agent_id=resolved_agent_id,
        harness=req.harness,
        version=install_version.version,
        config_snippet=snippet,
        warnings=warnings,
        lock=lock,
        skill_bundles=skill_bundles,
    )


@router.get("/{agent_id}/downloads")
async def agent_download_stats(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("agent_id={}", agent_id)
    agent = await _load_agent(
        db,
        agent_id,
        prefer_user_id=current_user.id,
        current_user=current_user,
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if get_effective_agent_permission(agent, current_user) == "none":
        raise HTTPException(status_code=403, detail="Insufficient permissions to view stats for this agent")
    from services.download_tracker import get_download_stats

    stats = await get_download_stats(agent.id, db)
    return stats


@router.get("/{agent_id}/traces")
async def get_agent_traces(
    agent_id: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Return all traces where this agent participated."""
    optic.trace("agent_id={}, limit={}", agent_id, limit)
    agent = await _load_agent(
        db,
        agent_id,
        prefer_user_id=current_user.id,
        current_user=current_user,
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if get_effective_agent_permission(agent, current_user) == "none":
        raise HTTPException(status_code=403, detail="Insufficient permissions to view this agent")
    return {"agent_id": str(agent.id), "traces": [], "count": 0}


@router.get("/{agent_id}/resolve")
async def resolve_agent_components(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_registry_user),
):
    """Resolve all components for an agent - validates they exist and are approved."""
    optic.trace("agent_id={}", agent_id)
    agent = await _load_agent(
        db,
        agent_id,
        prefer_user_id=current_user.id if current_user else None,
        current_user=current_user,
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if get_effective_agent_permission(agent, current_user) == "none":
        raise HTTPException(status_code=403, detail="Insufficient permissions to resolve this agent")
    from services.agent_resolver import resolve_agent

    resolved = await resolve_agent(agent, db, current_user=current_user)
    from services.agent_builder import build_composition_summary

    return build_composition_summary(resolved)


@router.get("/{agent_id}/manifest")
async def get_agent_manifest(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_registry_user),
):
    """Generate a portable agent manifest with all resolved components."""
    optic.trace("agent_id={}", agent_id)
    agent = await _load_agent(
        db,
        agent_id,
        prefer_user_id=current_user.id if current_user else None,
        current_user=current_user,
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if get_effective_agent_permission(agent, current_user) == "none":
        raise HTTPException(status_code=403, detail="Insufficient permissions to view this agent's manifest")
    from services.agent_resolver import resolve_agent

    resolved = await resolve_agent(agent, db, current_user=current_user)
    if not resolved.ok:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Agent has unresolvable components",
                "errors": [
                    {"component_type": e.component_type, "component_id": str(e.component_id), "reason": e.reason}
                    for e in resolved.errors
                ],
            },
        )
    from services.agent_builder import build_agent_manifest

    return build_agent_manifest(resolved)


@router.post("/validate", response_model=ValidationResult)
async def validate_agent_composition(
    req: AgentValidateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Validate a set of components for compatibility before publishing an agent."""
    optic.trace("req={}", req)
    if not req.components:
        return ValidationResult(valid=True, issues=[])

    from services.agent_resolver import validate_component_ids

    errors = await validate_component_ids(
        [{"component_type": c.component_type, "component_id": c.component_id} for c in req.components],
        db,
        require_approved=False,
        current_user=current_user,
        target_team_id=req.team_id if req.visibility == "team" else None,
        enforce_target=True,
    )
    issues = [
        ValidationIssue(
            severity="error",
            component_type=e.component_type,
            component_id=e.component_id,
            message=e.reason,
        )
        for e in errors
    ]
    return ValidationResult(valid=len(issues) == 0, issues=issues)
