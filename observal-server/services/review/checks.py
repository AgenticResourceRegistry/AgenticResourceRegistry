# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Review checks (deterministic snapshot checks and live pin readiness)."""

import json
import re

import yaml
from sqlalchemy import select

from models.agent_component import AgentComponent
from services.agent_lock import pinned_component_blockers as legacy_pinned_component_blockers

# Provider-issued token shapes. Modern keys carry hyphens and underscores (sk-proj-..., sk-ant-...).
CREDENTIAL = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{20,}"
    r"|gh[pousr]_[A-Za-z0-9]{30,}"
    r"|github_pat_[A-Za-z0-9_]{40,}"
    r"|AKIA[A-Z0-9]{16}"
    r"|AIza[A-Za-z0-9_-]{35}"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)


def snapshot_checks(files: dict, version=None, *, subject_type=None, base_files=None) -> list[dict]:
    leaked = [path for path, file in files.items() if CREDENTIAL.search(file["content"])]
    if version is not None:
        for column in version.__table__.columns:
            value = getattr(version, column.name)
            if isinstance(value, (str, dict, list)) and CREDENTIAL.search(
                value if isinstance(value, str) else json.dumps(value)
            ):
                leaked.append(column.name)
    checks = [
        {
            "id": "secrets",
            "name": "Secret scan",
            "status": "fail" if leaked else "pass",
            "required": True,
            "details": leaked,
        }
    ]
    if version is None or subject_type is None:
        return checks
    required = {
        # model_name is guaranteed at creation (blank becomes "auto"), so it never blocks publication.
        "agent": ("description", "prompt"),
        "mcp": ("description",),
        "skill": ("description", "task_type"),
        "hook": ("description", "event", "handler_type"),
        "prompt": ("description", "template"),
        "sandbox": ("description", "runtime_type", "image"),
    }
    missing = [name for name in required[subject_type] if not getattr(version, name, None)]
    if subject_type == "mcp" and not (version.command or version.url or version.docker_image):
        missing.append("command/url/docker_image")
    checks.append(
        {
            "id": "schema",
            "name": "Required fields",
            "status": "fail" if missing else "pass",
            "required": True,
            "details": missing,
        }
    )
    if subject_type == "agent" and version.gaming_flags:
        checks.append(
            {
                "id": "prompt_safety",
                "name": "Prompt safety",
                "status": "warn",
                "required": False,
                "details": ["Review anti-gaming flags"],
            }
        )
    if subject_type == "skill":
        checks.extend(_skill_folder_checks(version))
    source = getattr(version, "source_url", None) or getattr(version, "git_url", None)
    if source:
        checks.append(
            {
                "id": "provenance",
                "name": "Source provenance",
                "status": "pass" if getattr(version, "resolved_sha", None) else "warn",
                "required": False,
                "details": [] if getattr(version, "resolved_sha", None) else ["No resolved SHA"],
            }
        )
    changed = permission_delta(base_files or {}, files)
    if changed:
        checks.append(
            {
                "id": "permission_delta",
                "name": "Permission delta",
                "status": "warn",
                "required": False,
                "details": changed,
            }
        )
    return checks


def _skill_folder_checks(version) -> list[dict]:
    """A skill that cannot install as a valid complete folder must never be approved."""
    from services.skill_bundle import declared_skill_folder_name, needs_bundle_delivery, validate_skill_bundle
    from services.skill_validator import SkillValidationError

    try:
        validate_skill_bundle(
            delivery_mode=version.delivery_mode,
            skill_md_content=version.skill_md_content,
            script_content=version.script_content,
            script_filename=version.script_filename,
            extra_files=getattr(version, "extra_files", None) or [],
            enforce_limits=False,
        )
        if needs_bundle_delivery(version):
            declared_skill_folder_name(version.skill_md_content)
        problems: list[str] = []
    except SkillValidationError as exc:
        problems = [str(exc)]
    checks = [
        {
            "id": "skill_folder",
            "name": "Skill folder validity",
            "status": "fail" if problems else "pass",
            "required": True,
            "details": problems,
        }
    ]
    if getattr(version, "base_version_id", None) is not None and version.delivery_mode == "registry_direct":
        checks.append(
            {
                "id": "base_comparison",
                "name": "Base comparison",
                "status": "warn",
                "required": False,
                "details": ["If the approved base is a Git release, its files are not stored and cannot be compared"],
            }
        )
    return checks


_SENSITIVE_FIELDS = ("auto_approve", "tool_filter", "scope", "network_policy", "slash_command")


def _manifest_values(file: dict | None) -> dict:
    try:
        data = yaml.safe_load((file or {}).get("content") or "") or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def permission_delta(base_files: dict, files: dict) -> list[str]:
    """Sensitive manifest fields whose value differs from the review base."""
    changed = []
    for path in sorted(base_files.keys() | files.keys()):
        if not path.endswith(".yaml") or path == "components.yaml":
            continue
        before, after = _manifest_values(base_files.get(path)), _manifest_values(files.get(path))
        changed.extend(
            f"{path}:{field}"
            for field in _SENSITIVE_FIELDS
            if (field in before or field in after) and before.get(field) != after.get(field)
        )
    return changed


async def pinned_component_blockers(db, version_id) -> list[dict]:
    """Same pin resolution the legacy review gate uses, including unlocked legacy pins.

    A public agent may not depend on a team-private skill: approving it would
    publish an agent whose pinned skill most users cannot read or install.
    """
    from models.agent import Agent, AgentVersion
    from models.skill import SkillListing

    rows = (
        (await db.execute(select(AgentComponent).where(AgentComponent.agent_version_id == version_id))).scalars().all()
    )
    blockers: list[dict] = []
    agent_private = await db.scalar(
        select(Agent.is_private)
        .join(AgentVersion, AgentVersion.agent_id == Agent.id)
        .where(AgentVersion.id == version_id)
    )
    if agent_private is False:
        skills = [c for c in rows if c.component_type == "skill"]
        if skills:
            # Share-lock the listings so a concurrent privatization cannot slip past this check.
            visibility = dict(
                (
                    await db.execute(
                        select(SkillListing.id, SkillListing.is_private)
                        .where(SkillListing.id.in_({c.component_id for c in skills}))
                        .order_by(SkillListing.id)
                        .with_for_update(read=True)
                    )
                ).all()
            )
            blockers.extend(
                {
                    "component_type": "skill",
                    "component_id": str(c.component_id),
                    "name": c.component_name,
                    "version": c.resolved_version,
                    "status": "not_public",
                }
                for c in skills
                if visibility.get(c.component_id, True)
            )
    blockers.extend(await legacy_pinned_component_blockers(db, rows))
    return blockers
