# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Generate harness-specific skill files."""

from __future__ import annotations

import yaml

from observal_shared.harness_registry import HARNESS_REGISTRY
from services.harness import ensure_loaded, get_adapter


def _yaml_frontmatter(data: dict[str, str | bool]) -> str:
    body = yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True).strip()
    return f"---\n{body}\n---\n\n"


def generate_skill(skill: dict, harness: str, scope: str = "project") -> dict | None:
    """Generate an harness-specific skill file entry.

    Returns a dict with 'path' and 'content' keys, or None when the registry
    has no skill paths for the requested harness. Install callers enforce
    capability support separately.

    This is the canonical implementation used by both code paths.
    """
    harness_key = harness.replace("_", "-")
    spec = HARNESS_REGISTRY.get(harness_key, {})
    skill_paths = spec.get("skills")
    if not skill_paths:
        return None

    name = skill["name"]
    desc = skill.get("description", "")
    slash_cmd = skill.get("slash_command")
    path = skill_paths.get(scope, next(iter(skill_paths.values()))).format(name=name)

    ensure_loaded()
    adapter = get_adapter(harness_key)
    if adapter is None:
        raise ValueError(f"No adapter registered for harness: {harness_key!r}")

    skill_format = spec.get("skill_format")
    if skill_format == "yaml_frontmatter":
        frontmatter = {"name": name}
        if desc:
            frontmatter["description"] = desc
        frontmatter.update(adapter.skill_frontmatter_extra(slash_cmd))
        content = _yaml_frontmatter(frontmatter) + f"{desc}\n"
    else:
        frontmatter = {"description": desc, "alwaysApply": False}
        content = _yaml_frontmatter(frontmatter) + f"# {name}\n\n{desc}\n"

    return {"path": path, "content": content}
