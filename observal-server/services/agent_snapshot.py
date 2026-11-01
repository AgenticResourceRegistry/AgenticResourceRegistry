# SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Build a deterministic YAML snapshot of an :class:`AgentVersion`.

The snapshot is the canonical text the reviewer reads when approving a
new version, and the source for the version-diff endpoint when neither
side carries a client-supplied snapshot. Centralising the shape here
guarantees the web builder, the CLI publish flow and the diff fallback
all surface the same fields - including per-harness model overrides
(``models_by_harness``) which are otherwise easy to omit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml
from sqlalchemy import select

from models.agent_component import AgentComponent
from models.hook import HookListing
from models.mcp import McpListing
from models.prompt import PromptListing
from models.sandbox import SandboxListing
from models.skill import SkillListing

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from models.agent import AgentVersion
from loguru import logger as optic

_LISTING_MODELS = {
    "mcp": McpListing,
    "skill": SkillListing,
    "hook": HookListing,
    "prompt": PromptListing,
    "sandbox": SandboxListing,
}


def _normalise_models_by_harness(value: object) -> dict[str, str]:
    """Coerce ``models_by_harness`` into a plain dict for YAML serialisation."""
    optic.trace("normalising models_by_harness value: {}", type(value).__name__)
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items() if v}


async def _resolve_component_details(ver: AgentVersion, db: AsyncSession) -> list[dict]:
    """Return human-friendly component entries for the snapshot.

    Re-queries ``agent_components`` from the database rather than reading
    ``ver.components`` directly. The relationship attribute may be stale
    when a freshly created version's components were added in the same
    session but the back-reference wasn't synchronously synced.
    """
    optic.trace("resolving component details for version {}", getattr(ver, "version", "?"))
    rows = (
        (
            await db.execute(
                select(AgentComponent)
                .where(AgentComponent.agent_version_id == ver.id)
                .order_by(AgentComponent.order_index)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return []
    # Reviewers approve the pinned releases, so describe those, not the latest.
    from services.agent_lock import pinned_versions

    pinned = await pinned_versions(db, rows)
    details: list[dict] = []
    for comp in rows:
        entry: dict = {
            "type": comp.component_type,
            "id": str(comp.component_id),
        }
        model = _LISTING_MODELS.get(comp.component_type)
        listing = None
        if model is not None:
            listing = (await db.execute(select(model).where(model.id == comp.component_id))).scalar_one_or_none()
        if listing is not None:
            content = pinned.get((comp.component_type, comp.component_id), listing)
            entry["name"] = getattr(listing, "name", "") or comp.component_name or ""
            if comp.component_type == "prompt":
                entry["template"] = getattr(content, "template", "") or ""
            else:
                entry["description"] = getattr(content, "description", "") or ""
        else:
            entry["name"] = comp.component_name or str(comp.component_id)[:8]
        if comp.resolved_version:
            entry["version"] = comp.resolved_version
        if comp.config_override:
            entry["config_override"] = comp.config_override
        details.append(entry)
    return details


async def build_yaml_snapshot(ver: AgentVersion, db: AsyncSession) -> str:
    """Render *ver* as a YAML document suitable for ``ver.yaml_snapshot``.

    The returned string is deterministic: keys are emitted in a fixed order
    and ``models_by_harness`` is always present (empty dict when the author
    didn't override anything) so a reviewer can trust an empty section
    means "no per-harness overrides", not "missing data".
    """
    optic.trace("resolving component details for version {}", getattr(ver, "version", "?"))
    components = await _resolve_component_details(ver, db)
    data: dict = {
        "version": ver.version,
        "description": ver.description or "",
        "model_name": ver.model_name or "",
        "models_by_harness": _normalise_models_by_harness(ver.models_by_harness),
        "supported_harnesses": list(ver.supported_harnesses or []),
        "external_mcps": list(ver.external_mcps or []),
        "components": components,
        "prompt": ver.prompt or "",
    }
    if ver.model_config_json:
        data["model_config_json"] = ver.model_config_json
    if ver.success_criteria:
        data["success_criteria"] = ver.success_criteria
    header = "# Auto-generated snapshot - review the structured fields above and the prompt below.\n"
    return header + yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True)
