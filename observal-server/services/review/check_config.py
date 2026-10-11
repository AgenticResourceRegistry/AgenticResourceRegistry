# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Which automated review checks run, and whether they block publication.

Public items use the organization configuration (edited by admins). Team-visible
items use their teamspace configuration. Missing entries fall back to the
defaults below. Checks that touch the network are off by default.
"""

import json
from dataclasses import dataclass

import services.dynamic_settings as ds

ORG_KEY = "review.checks"


def team_key(team_id) -> str:
    return f"review.checks.team.{team_id}"


@dataclass(frozen=True)
class CheckSpec:
    id: str
    name: str
    description: str
    subjects: tuple[str, ...]
    network: bool = False
    # Built-in integrity checks run on every revision and cannot be switched off.
    locked: bool = False
    enabled: bool = True
    required: bool = False


_ALL = ("agent", "mcp", "skill", "hook", "prompt", "sandbox")

SPECS: tuple[CheckSpec, ...] = (
    CheckSpec(
        "secrets", "Secret scan", "Known credential formats in the submitted content.", _ALL, locked=True, required=True
    ),
    CheckSpec(
        "schema", "Required fields", "Fields every release of this type must have.", _ALL, locked=True, required=True
    ),
    CheckSpec(
        "skill_folder",
        "Skill folder validity",
        "SKILL.md frontmatter, bundle layout and installable folder name.",
        ("skill",),
        locked=True,
        required=True,
    ),
    CheckSpec(
        "agent_config",
        "Harness config generation",
        "Generates the install config for every declared harness. Catches releases that `observal agent pull` "
        "could not build.",
        ("agent",),
    ),
    CheckSpec(
        "script_lint",
        "Script syntax and risky patterns",
        "Parses bundled shell, Python, JSON and YAML (never executes them) and flags patterns such as curl piped "
        "to a shell.",
        ("hook", "skill"),
    ),
    CheckSpec(
        "prompt_template",
        "Prompt template variables",
        "Variables used in the template versus the variables declared, and template size.",
        ("prompt",),
    ),
    CheckSpec(
        "secrets_entropy",
        "High-entropy secret heuristic",
        "Flags credential-like names assigned long random-looking values. Heuristic: expect false positives.",
        _ALL,
    ),
    CheckSpec(
        "sandbox_image_pin",
        "Sandbox image pinned by digest",
        "Warns when the image reference is a mutable tag instead of a digest.",
        ("sandbox",),
    ),
    CheckSpec(
        "git_source",
        "Git source resolves",
        "Confirms the repository and ref exist and match the recorded commit. For skills, compares the stored "
        "SKILL.md with the repository's.",
        ("mcp", "skill", "hook", "sandbox"),
        network=True,
    ),
    CheckSpec(
        "remote_mcp",
        "Remote MCP server responds",
        "Connects anonymously to a public MCP URL and lists its tools. Servers that need authentication are "
        "reported as unverified.",
        ("mcp",),
        network=True,
        enabled=False,
    ),
    CheckSpec(
        "sandbox_image_resolves",
        "Sandbox image exists in its registry",
        "Asks the image registry for the manifest (anonymous access only).",
        ("sandbox",),
        network=True,
        enabled=False,
    ),
    CheckSpec(
        "dependency_audit",
        "Known-vulnerable dependencies",
        "Reads lockfiles from the Git source and queries the OSV database. Sends package names and versions to "
        "osv.dev.",
        ("mcp", "skill", "hook", "sandbox"),
        network=True,
        enabled=False,
    ),
)
SPEC_BY_ID = {spec.id: spec for spec in SPECS}


def parse_config(raw) -> dict[str, dict[str, bool]]:
    """Validate a stored or submitted config. Raises ValueError with a user-facing message."""
    data = json.loads(raw) if isinstance(raw, str) and raw else (raw or {})
    if not isinstance(data, dict) or set(data) - {"checks"}:
        raise ValueError("Check settings must be an object with a 'checks' map")
    checks = data.get("checks", {})
    if not isinstance(checks, dict):
        raise ValueError("'checks' must be a map of check id to settings")
    clean: dict[str, dict[str, bool]] = {}
    for check_id, entry in checks.items():
        spec = SPEC_BY_ID.get(check_id)
        if spec is None:
            raise ValueError(f"Unknown check: {check_id}")
        if spec.locked:
            raise ValueError(f"{spec.name} is built in and cannot be changed")
        if not isinstance(entry, dict) or set(entry) - {"enabled", "required"}:
            raise ValueError(f"Settings for {check_id} must contain only 'enabled' and 'required'")
        enabled = entry.get("enabled", spec.enabled)
        required = entry.get("required", spec.required)
        if type(enabled) is not bool or type(required) is not bool:
            raise ValueError(f"Settings for {check_id} must be booleans")
        if required and not enabled:
            raise ValueError(f"{spec.name} cannot be required while disabled")
        clean[check_id] = {"enabled": enabled, "required": required}
    return clean


def resolve(stored: dict[str, dict[str, bool]] | None) -> dict[str, dict[str, bool]]:
    """Every configurable check with its effective settings."""
    stored = stored or {}
    return {
        spec.id: {
            "enabled": stored.get(spec.id, {}).get("enabled", spec.enabled),
            "required": stored.get(spec.id, {}).get("required", spec.required),
        }
        for spec in SPECS
        if not spec.locked
    }


async def load_stored(team_id=None, *, is_private: bool = False, org_raw=None, team_raw=None) -> dict:
    """Stored overrides for the scope an item belongs to (team-visible items use their teamspace)."""
    if team_id and is_private:
        raw = team_raw if team_raw is not None else await ds.get(team_key(team_id), default="")
    else:
        raw = org_raw if org_raw is not None else await ds.get(ORG_KEY, default="")
    return parse_config(raw)


async def effective(team_id=None, *, is_private: bool = False, org_raw=None, team_raw=None) -> dict:
    return resolve(await load_stored(team_id, is_private=is_private, org_raw=org_raw, team_raw=team_raw))
