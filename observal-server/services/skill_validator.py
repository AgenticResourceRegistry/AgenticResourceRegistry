# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Lightweight SKILL.md validator.

Fetches SKILL.md from a raw GitHub URL (or generic raw URL), parses YAML
frontmatter, validates required fields, and extracts slash_command.  Only a
single HTTP request is made - no git clone at submit time.

Port of vercel-labs/agent-skills parseFrontmatter logic to Python.
"""

from __future__ import annotations

import asyncio
import re

import httpx
import yaml
from loguru import logger as optic
from pydantic import BaseModel, Field

import services.dynamic_settings as ds
from schemas.skill_commands import normalize_slash_command
from services.ssrf_guard import is_private_url

_FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---(?:\r?\n|$)", re.DOTALL)

# GitHub raw URL pattern.
# Input git_url examples:
#   https://github.com/owner/repo          → no .git suffix
#   https://github.com/owner/repo.git      → strip .git
_GITHUB_RE = re.compile(
    r"https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$",
    re.IGNORECASE,
)


class SkillAnalysis(BaseModel):
    """Parsed result of a SKILL.md frontmatter."""

    name: str
    description: str
    slash_command: str | None = None
    raw_content: str = ""
    discovered_path: str | None = None  # Set when server auto-discovered the skill_path


class SkillFrontmatterAnalysis(BaseModel):
    """Strict validation result for stored SKILL.md content."""

    has_frontmatter: bool = False
    frontmatter: dict = Field(default_factory=dict)
    slash_command: str | None = None


class SkillValidationError(Exception):
    """Raised when SKILL.md cannot be fetched or validated."""


# Installed/harness copy prefixes to exclude during discovery (mirrors client-side logic)
_INSTALLED_PREFIX = re.compile(
    r"^(\.agents|\.(?:claude|kiro|cursor|vscode|github|opencode|pi|trae|trae-cn|rovodev|qoder|copilot)|plugin)/"
)


async def _discover_skill_path(client: httpx.AsyncClient, git_url: str, git_ref: str) -> str | None:
    """Try to find SKILL.md in a GitHub repo using the Trees API.

    Returns the skill_path (directory containing SKILL.md) or None if not found.
    Only works for GitHub repos. Filters out harness config copies.
    """
    optic.trace("discovering skill path in repo {}", git_url)
    m = _GITHUB_RE.match(git_url.rstrip("/"))
    if not m:
        return None
    owner = m.group("owner")
    repo = m.group("repo")

    try:
        resp = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/git/trees/{git_ref}",
            params={"recursive": "1"},
            headers={"Accept": "application/vnd.github+json", "User-Agent": "Observal-Skill-Validator"},
            timeout=10.0,
        )
        if resp.status_code != 200:
            return None
        tree = resp.json().get("tree", [])
    except httpx.RequestError:
        return None

    all_skills = [f["path"] for f in tree if f["path"].endswith("/SKILL.md") or f["path"] == "SKILL.md"]
    canonical = [p for p in all_skills if not _INSTALLED_PREFIX.match(p)]
    candidates = canonical if canonical else all_skills

    if len(candidates) == 1:
        path = candidates[0]
        return path.rsplit("/SKILL.md", 1)[0] if "/" in path else "/"
    return None


def _normalize_skill_path(skill_path: str | None) -> str:
    clean_path = (skill_path or "/").strip("/")
    if clean_path.casefold() == "skill.md":
        return ""
    if clean_path.casefold().endswith("/skill.md"):
        return clean_path.rsplit("/", 1)[0]
    return clean_path


def _build_raw_url(git_url: str, skill_path: str, git_ref: str) -> str:
    """Build a raw content URL for SKILL.md.

    Supports GitHub repos (converts to raw.githubusercontent.com).
    Falls back to appending /raw/<ref>/<path> for other hosts (e.g. GitLab).
    """
    optic.trace("building raw URL for skill at {}", skill_path)
    skill_path = _normalize_skill_path(skill_path)
    skill_md = f"{skill_path}/SKILL.md" if skill_path else "SKILL.md"

    m = _GITHUB_RE.match(git_url.rstrip("/"))
    if m:
        owner = m.group("owner")
        repo = m.group("repo")
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{git_ref}/{skill_md}"

    # Generic fallback: assume a /raw/<ref>/<path> structure (GitLab, Gitea, …)
    base = git_url.rstrip("/")
    if base.endswith(".git"):
        base = base[:-4]
    return f"{base}/raw/{git_ref}/{skill_md}"


def parse_skill_frontmatter(content: str) -> dict:
    """Extract and parse YAML frontmatter block from markdown content.

    Mirrors vercel-labs parseFrontmatter: regex extraction + yaml.safe_load.
    Never calls eval.  Returns empty dict if no frontmatter found.
    """
    optic.trace("parsing skill frontmatter ({} chars)", len(content) if content else 0)
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return {}
    raw_yaml = m.group(1)
    try:
        result = yaml.safe_load(raw_yaml)
        return result if isinstance(result, dict) else {}
    except yaml.YAMLError:
        return {}


def _parse_frontmatter(content: str) -> dict:
    return parse_skill_frontmatter(content)


def validate_skill_md_content_frontmatter(
    content: str | None,
    *,
    slash_command: str | None = None,
) -> SkillFrontmatterAnalysis:
    """Validate stored/verbatim SKILL.md frontmatter without rewriting content.

    No frontmatter is accepted. If a frontmatter block is present, it must be
    valid YAML mapping data. A present ``command`` key must normalize to the
    same slash command supplied by the request, when one is supplied.
    """
    normalized_request_command: str | None = None
    if slash_command is not None:
        try:
            normalized_request_command = normalize_slash_command(slash_command)
        except ValueError as exc:
            raise SkillValidationError(f"Invalid slash command: {exc}") from exc

    if not content:
        return SkillFrontmatterAnalysis(slash_command=normalized_request_command)

    if not re.match(r"^---\r?\n", content):
        return SkillFrontmatterAnalysis(slash_command=normalized_request_command)

    match = _FRONTMATTER_RE.match(content)
    if not match:
        raise SkillValidationError("Malformed SKILL.md frontmatter")

    try:
        parsed = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise SkillValidationError("Malformed SKILL.md frontmatter") from exc

    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        raise SkillValidationError("SKILL.md frontmatter must be a YAML mapping")

    frontmatter_command: str | None = None
    if "command" in parsed:
        raw_command = parsed["command"]
        if not isinstance(raw_command, str) or raw_command == "":
            raise SkillValidationError("Invalid slash command: command must match ^[a-z0-9][a-z0-9_-]{0,63}$")
        try:
            frontmatter_command = normalize_slash_command(raw_command)
        except ValueError as exc:
            raise SkillValidationError(f"Invalid slash command: {exc}") from exc

    if normalized_request_command and frontmatter_command and normalized_request_command != frontmatter_command:
        raise SkillValidationError("slash_command does not match SKILL.md frontmatter command")

    return SkillFrontmatterAnalysis(
        has_frontmatter=True,
        frontmatter=parsed,
        slash_command=frontmatter_command or normalized_request_command,
    )


async def validate_skill_md(
    git_url: str,
    skill_path: str = "/",
    git_ref: str = "main",
) -> SkillAnalysis:
    """Fetch SKILL.md and validate its frontmatter.

    Args:
        git_url: Repository URL (GitHub or generic git host).
        skill_path: Path within the repo where the skill directory lives.
        git_ref: Branch or tag to fetch from (default: "main").

    Returns:
        SkillAnalysis with name, description, slash_command, raw_content.

    Raises:
        SkillValidationError: if SKILL.md cannot be fetched or is invalid.
    """
    optic.trace("building raw URL for skill at {}", skill_path)
    skill_path = _normalize_skill_path(skill_path) or "/"
    raw_url = _build_raw_url(git_url, skill_path, git_ref)

    async def _block_private_hosts(request: httpx.Request) -> None:
        # Runs for every request, including each redirect hop.
        if not ds.get_sync_bool("security.allow_internal_git_urls") and await asyncio.to_thread(
            is_private_url, str(request.url)
        ):
            raise SkillValidationError("git_url must not point to a private or internal address")

    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=15.0, event_hooks={"request": [_block_private_hosts]}
        ) as client:
            resp = await client.get(raw_url)

            # If 404 on a GitHub repo with default skill_path, try auto-discovery
            if resp.status_code == 404 and skill_path.strip("/") == "":
                discovered = await _discover_skill_path(client, git_url, git_ref)
                if discovered:
                    skill_path = discovered
                    raw_url = _build_raw_url(git_url, skill_path, git_ref)
                    resp = await client.get(raw_url)

    except httpx.RequestError as exc:
        raise SkillValidationError(f"Network error fetching SKILL.md: {exc}") from exc

    if resp.status_code == 404:
        raise SkillValidationError(
            f"SKILL.md not found at {raw_url!r}. Check that git_url, skill_path, and git_ref are correct."
        )
    if resp.status_code != 200:
        raise SkillValidationError(f"Failed to fetch SKILL.md (HTTP {resp.status_code}): {raw_url!r}")

    content = resp.text
    stored_analysis = validate_skill_md_content_frontmatter(content)
    fm = stored_analysis.frontmatter

    name = fm.get("name", "")
    description = fm.get("description", "")

    if not isinstance(name, str) or not name.strip():
        raise SkillValidationError("SKILL.md frontmatter missing required field: 'name'")
    if not isinstance(description, str) or not description.strip():
        raise SkillValidationError("SKILL.md frontmatter missing required field: 'description'")

    return SkillAnalysis(
        name=name.strip(),
        description=description.strip(),
        slash_command=stored_analysis.slash_command or None,
        raw_content=content,
        discovered_path=skill_path if skill_path.strip("/") else None,
    )
