# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Agent version locks.

An agent version is the lock for its components. Every ``AgentComponent`` row
pins one exact component version row (``resolved_version_id``) and records the
content digest of that row when the agent version was locked
(``resolved_digest``). ``resolved_version`` keeps the human-readable semver.

This module is the only place that:

* resolves pins when an agent version is written (explicit version, carried
  forward from the previous release, or the latest approved release),
* loads pinned content for installation and reports anything that did not
  match the lock,
* renders the lock document stored in ``agent_versions.lock_snapshot``.

Pins are resolved when an agent version is saved and never at pull time.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from loguru import logger as optic
from pydantic import ValidationError
from sqlalchemy import select

from models.agent_component import AgentComponent
from models.hook import HookListing, HookVersion
from models.mcp import ListingStatus, McpListing, McpVersion
from models.prompt import PromptListing, PromptVersion
from models.sandbox import SandboxListing, SandboxVersion
from models.skill import SkillListing, SkillVersion
from services.skill_bundle import needs_bundle_delivery, validate_skill_bundle
from services.skill_revisions import verified_skill_revision
from services.skill_validator import SkillValidationError

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy.ext.asyncio import AsyncSession

LOCK_VERSION = 1
DIGEST_ALG = "observal-content-v1"
SKILL_DIGEST_ALG_V2 = "observal-content-v2"

LISTING_MODELS: dict[str, type] = {
    "mcp": McpListing,
    "skill": SkillListing,
    "hook": HookListing,
    "prompt": PromptListing,
    "sandbox": SandboxListing,
}
VERSION_MODELS: dict[str, type] = {
    "mcp": McpVersion,
    "skill": SkillVersion,
    "hook": HookVersion,
    "prompt": PromptVersion,
    "sandbox": SandboxVersion,
}

# Archive is recorded on a listing's latest version row. An archived release is
# still the release people installed, so it stays pinnable and installable.
INSTALLABLE_STATUSES = frozenset({ListingStatus.approved, ListingStatus.archived})

# ---------------------------------------------------------------------------
# Content digest
# ---------------------------------------------------------------------------

# Only fields that change what a generator writes to disk are hashed. Review,
# download, edit-lock, and analysis metadata (mcp_validated, tools_schema,
# validated*) change after approval and must not break the digest. Listing
# fields (name, namespace, owner) belong to the listing, not to the release.
# Changing this allowlist changes every digest: bump DIGEST_ALG when you do.
_COMMON_FIELDS = ("description", "supported_harnesses")
CONTENT_FIELDS: dict[str, tuple[str, ...]] = {
    "mcp": (
        "transport",
        "framework",
        "docker_image",
        "command",
        "args",
        "url",
        "headers",
        "auto_approve",
        "environment_variables",
        "setup_instructions",
        "source_url",
        "source_ref",
        "resolved_sha",
    ),
    "skill": (
        "delivery_mode",
        "git_url",
        "git_ref",
        "skill_path",
        "skill_md_content",
        "script_filename",
        "script_content",
        "slash_command",
        "task_type",
        "target_agents",
    ),
    "hook": (
        "event",
        "execution_mode",
        "priority",
        "handler_type",
        "handler_config",
        "scope",
        "tool_filter",
        "source_url",
        "source_ref",
        "source_path",
        "resolved_sha",
        "script_filename",
        "script_content",
        "requirements",
    ),
    "prompt": ("template", "variables", "category", "model_hints"),
    "sandbox": (
        "runtime_type",
        "image",
        "resource_limits",
        "network_policy",
        "entrypoint",
        "runtime_config",
        "source_url",
        "source_ref",
        "resolved_sha",
        "sandbox_path",
    ),
}
# Environment variable and header definitions drive install prompts by name.
# Stored values are never used by a generator and must never be hashed.
_NAMED_FIELDS = frozenset({"environment_variables", "headers"})


def _named(entries: object) -> list[dict]:
    named: list[dict] = []
    for entry in entries or []:
        if isinstance(entry, dict):
            named.append({key: entry[key] for key in ("name", "required") if key in entry})
        else:
            named.append({"name": str(entry)})
    return named


def _sha256(payload: object) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def content_digest(component_type: str, version: Any) -> str:
    """Digest of a selected version; resource-bearing skills bind every file and mode.

    Keep the existing v1 payload byte-for-byte for resource-less and git-fetch
    pins. The named v2 prefix prevents an old v1 lock from silently accepting
    files that were added to its previously resource-less version.
    """
    payload: dict[str, object] = {}
    for name in (*_COMMON_FIELDS, *CONTENT_FIELDS[component_type]):
        value = getattr(version, name, None)
        if name in _NAMED_FIELDS and value is not None:
            value = _named(value)
        if value is not None:
            payload[name] = value
    if component_type == "skill" and needs_bundle_delivery(version):
        files = validate_skill_bundle(
            delivery_mode=version.delivery_mode,
            skill_md_content=version.skill_md_content,
            script_filename=version.script_filename,
            script_content=version.script_content,
            extra_files=version.extra_files or [],
            enforce_limits=False,
        )
        payload["digest_alg"] = SKILL_DIGEST_ALG_V2
        try:
            payload["files"] = [file.declaration.model_dump() for file in sorted(files, key=lambda file: file.path)]
        except ValidationError as exc:
            raise SkillValidationError("Stored skill folder exceeds manifest limits") from exc
        return f"{SKILL_DIGEST_ALG_V2}:{_sha256(payload)}"
    return _sha256(payload)


def _requires_v2_skill_pin(component: Any, row: Any) -> bool:
    """A pinned whole folder stays exact even if stored bytes later disappear."""
    recorded = getattr(component, "resolved_digest", None)
    return needs_bundle_delivery(row) or (isinstance(recorded, str) and recorded.startswith(f"{SKILL_DIGEST_ALG_V2}:"))


def _external_mcp_digest(mcp: dict) -> str:
    """Digest an inline MCP without hashing its environment values."""
    payload = {key: value for key, value in mcp.items() if key != "env"}
    payload["env"] = sorted((mcp.get("env") or {}).keys()) if isinstance(mcp.get("env"), dict) else []
    return _sha256(payload)


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------

_SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?$")


def release_key(version: str | None) -> tuple[int, int, int] | None:
    """Sort key for a stable release. Pre-releases and malformed strings return None."""
    match = _SEMVER_RE.match(version or "")
    if not match or match.group(4):
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def latest_release(rows: Iterable[Any]) -> Any | None:
    """The one definition of "latest": the highest stable, installable release."""
    candidates = [row for row in rows if row.status in INSTALLABLE_STATUSES and release_key(row.version)]
    return max(candidates, key=lambda row: release_key(row.version), default=None)


def _field(ref: object, name: str, default: Any = None) -> Any:
    return ref.get(name, default) if isinstance(ref, dict) else getattr(ref, name, default)


def _uuid(value: object) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


async def _versions_for(db: AsyncSession, keys: Iterable[tuple[str, uuid.UUID]]) -> dict[tuple[str, uuid.UUID], list]:
    """Every version row of each (type, listing id), in one query per type."""
    by_type: dict[str, set[uuid.UUID]] = {}
    for component_type, listing_id in keys:
        if component_type in VERSION_MODELS:
            by_type.setdefault(component_type, set()).add(listing_id)
    found: dict[tuple[str, uuid.UUID], list] = {}
    for component_type, ids in by_type.items():
        model = VERSION_MODELS[component_type]
        rows = (
            (await db.execute(select(model).where(model.listing_id.in_(ids)).execution_options(populate_existing=True)))
            .scalars()
            .all()
        )
        for row in rows:
            found.setdefault((component_type, row.listing_id), []).append(row)
    return found


async def _listings_for(db: AsyncSession, keys: Iterable[tuple[str, uuid.UUID]]) -> dict[tuple[str, uuid.UUID], Any]:
    by_type: dict[str, set[uuid.UUID]] = {}
    for component_type, listing_id in keys:
        if component_type in LISTING_MODELS:
            by_type.setdefault(component_type, set()).add(listing_id)
    found: dict[tuple[str, uuid.UUID], Any] = {}
    for component_type, ids in by_type.items():
        model = LISTING_MODELS[component_type]
        for row in (await db.execute(select(model).where(model.id.in_(ids)))).scalars().all():
            found[(component_type, row.id)] = row
    return found


def _pinned_row(component: Any, rows: list) -> tuple[Any | None, str]:
    """Find the row an existing pin names: by id first, then by the recorded string."""
    version_id = getattr(component, "resolved_version_id", None)
    if version_id is not None:
        row = next((r for r in rows if r.id == version_id), None)
        if row is not None:
            return row, "lock"
    recorded = getattr(component, "resolved_version", None)
    if recorded and recorded != "latest":
        row = next((r for r in rows if r.version == recorded), None)
        if row is not None:
            return row, "version"
    return None, "fallback-latest"


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------


def may_pin_unapproved(listing: Any, user: Any) -> bool:
    """Whether ``user`` may pin a release of ``listing`` that is not approved.

    A pin exposes the pinned release: the agent's YAML snapshot renders its
    content and an install generates files from it. So the rule is the one that
    guards reading an unapproved version directly: owners, co-authors, admins,
    and reviewers only. Without a user, nobody may.
    """
    if listing is None or user is None:
        return False
    from api.deps import get_effective_component_permission, may_view_unapproved

    return may_view_unapproved(get_effective_component_permission(listing, user), user)


def _choose_pin(
    component_type: str,
    rows: list,
    listing: Any,
    *,
    requested: str | None,
    previous: Any | None,
    require_approved: bool,
    may_see_unapproved: bool,
) -> tuple[Any | None, str | None]:
    if requested:
        row = next((r for r in rows if r.version == requested), None)
        if row is not None and row.status not in INSTALLABLE_STATUSES and not may_see_unapproved:
            # Report an unapproved release the caller may not read as missing,
            # exactly as the component version routes do.
            row = None
        if row is None:
            return None, f"{component_type} version {requested!r} does not exist"
        if require_approved and row.status not in INSTALLABLE_STATUSES:
            return None, f"{component_type} version {requested!r} is {row.status.value}, not approved"
        return row, None
    if previous is not None:
        row, source = _pinned_row(previous, rows)
        if row is not None and source != "fallback-latest":
            return row, None
    row = latest_release(rows)
    if row is None and not require_approved and may_see_unapproved and listing is not None:
        # A component submitted together with a draft agent has no approved
        # release yet; pin the owner's pending version so review can approve both.
        row = next((r for r in rows if r.id == listing.latest_version_id), None)
    if row is None:
        return None, f"{component_type} has no approved version to pin"
    return row, None


async def attach_pinned_components(
    db: AsyncSession,
    agent_version_id: uuid.UUID,
    refs: Iterable[object],
    *,
    previous: Iterable[AgentComponent] = (),
    refresh: bool = False,
    require_approved: bool = False,
    order_start: int = 0,
    current_user: Any = None,
) -> list[AgentComponent]:
    """Resolve a pin for each component reference and add the AgentComponent rows.

    A reference may carry an exact ``version``. Otherwise the pin recorded for the
    same component in ``previous`` is kept, so releasing an agent never upgrades
    a component its author did not touch. ``refresh`` drops those carried pins
    and resolves every unversioned component to its latest approved release.

    Only ``current_user`` decides whether a release that is not approved yet can
    be pinned (see :func:`may_pin_unapproved`); ``require_approved`` refuses them
    for everyone.
    """
    refs = list(refs)
    if not refs:
        return []
    keys = [(_field(ref, "component_type"), _uuid(_field(ref, "component_id"))) for ref in refs]
    versions = await _versions_for(db, keys)
    listings = await _listings_for(db, keys)
    carried = {} if refresh else {(c.component_type, c.component_id): c for c in previous}

    chosen: list[tuple[object, tuple[str, uuid.UUID], Any]] = []
    errors: list[dict] = []
    for ref, key in zip(refs, keys, strict=True):
        row, reason = _choose_pin(
            key[0],
            versions.get(key, []),
            listings.get(key),
            requested=_field(ref, "version"),
            previous=carried.get(key),
            require_approved=require_approved,
            may_see_unapproved=may_pin_unapproved(listings.get(key), current_user),
        )
        if row is None:
            errors.append({"component_type": key[0], "component_id": str(key[1]), "reason": reason})
            continue
        if key[0] == "skill" and row.delivery_mode == "registry_direct":
            try:
                if listings.get(key) is None:
                    raise SkillValidationError("Skill listing no longer exists")
                verified_skill_revision(listings[key], row)
            except SkillValidationError:
                errors.append(
                    {
                        "component_type": key[0],
                        "component_id": str(key[1]),
                        "reason": "skill folder has no valid review revision",
                    }
                )
                continue
        chosen.append((ref, key, row))
    if errors:
        raise HTTPException(status_code=400, detail=errors)

    created: list[AgentComponent] = []
    for offset, (ref, key, row) in enumerate(chosen):
        component = AgentComponent(
            agent_version_id=agent_version_id,
            component_type=key[0],
            component_id=key[1],
            component_name=_field(ref, "component_name", "") or "",
            resolved_version=row.version,
            resolved_version_id=row.id,
            resolved_digest=content_digest(key[0], row),
            order_index=order_start + offset,
            config_override=_field(ref, "config_override"),
        )
        db.add(component)
        created.append(component)
    optic.debug("pinned {} component(s) for agent version {}", len(created), agent_version_id)
    return created


# ---------------------------------------------------------------------------
# Lock document
# ---------------------------------------------------------------------------


def lock_status(entries: list[dict]) -> str:
    unlocked = sum(entry.get("source") == "fallback-latest" for entry in entries)
    if not unlocked:
        return "locked"
    return "unlocked" if unlocked == len(entries) else "partial"


def lock_digest(document: dict) -> str:
    """Digest of a lock document, independent of when it was rendered."""
    return _sha256({key: value for key, value in document.items() if key not in ("locked_at", "digest")})


def _entry(component: Any, row: Any, listing: Any, source: str) -> dict:
    return {
        "type": component.component_type,
        "id": str(component.component_id),
        "qualified_name": getattr(listing, "qualified_name", "") if listing is not None else "",
        "version": getattr(row, "version", None) if row is not None else component.resolved_version,
        "version_id": str(row.id) if row is not None else None,
        "digest": content_digest(component.component_type, row) if row is not None else None,
        "source": source,
    }


async def build_lock_document(db: AsyncSession, agent: Any, version: Any, *, persist: bool = False) -> dict:
    """Render the lock for one agent version.

    With ``persist`` the pins are refreshed from their version rows (id and
    digest) and the document is stored in ``lock_snapshot``. That happens every
    time a draft or pending version is saved and once more when it is approved,
    which is when the lock freezes: nothing writes an approved version's
    components afterwards.
    """
    components = (
        (
            await db.execute(
                select(AgentComponent)
                .where(AgentComponent.agent_version_id == version.id)
                .order_by(AgentComponent.order_index)
            )
        )
        .scalars()
        .all()
    )
    keys = [(c.component_type, c.component_id) for c in components]
    versions = await _versions_for(db, keys)
    listings = await _listings_for(db, keys)
    entries: list[dict] = []
    for component in components:
        key = (component.component_type, component.component_id)
        row, source = _pinned_row(component, versions.get(key, []))
        if persist and key[0] == "skill" and row is not None and row.delivery_mode == "registry_direct":
            try:
                if listings.get(key) is None:
                    raise SkillValidationError("Skill listing no longer exists")
                verified_skill_revision(listings[key], row)
            except SkillValidationError as exc:
                raise HTTPException(status_code=409, detail="Pinned skill folder has no valid review revision") from exc
        entry = _entry(component, row, listings.get(key), source)
        entries.append(entry)
        if persist and row is not None:
            component.resolved_version_id = row.id
            component.resolved_version = row.version
            component.resolved_digest = entry["digest"]
    document = {
        "lock_version": LOCK_VERSION,
        "digest_alg": SKILL_DIGEST_ALG_V2
        if any(
            entry["type"] == "skill" and (entry["digest"] or "").startswith(f"{SKILL_DIGEST_ALG_V2}:")
            for entry in entries
        )
        else DIGEST_ALG,
        "agent": {
            "id": str(agent.id),
            "qualified_name": f"{agent.namespace}/{agent.slug}",
            "version": version.version,
            "version_id": str(version.id),
        },
        "status": lock_status(entries),
        "components": [{k: v for k, v in entry.items() if k != "source"} for entry in entries],
        "external_mcps": [
            {"name": mcp.get("name", ""), "digest": _external_mcp_digest(mcp)}
            for mcp in (version.external_mcps or [])
            if isinstance(mcp, dict)
        ],
    }
    document["digest"] = lock_digest(document)
    document["locked_at"] = datetime.now(UTC).isoformat()
    if persist:
        version.lock_snapshot = json.dumps(document, indent=2)
    return document


def stored_lock_digest(version: Any) -> str | None:
    """Digest of the lock stored on an agent version, if it has a readable one."""
    snapshot = getattr(version, "lock_snapshot", None)
    if not isinstance(snapshot, str):
        return None
    try:
        document = json.loads(snapshot)
    except ValueError:
        return None
    digest = document.get("digest") if isinstance(document, dict) else None
    return digest if isinstance(digest, str) else None


async def lock_agent_version(db: AsyncSession, agent: Any, version: Any) -> dict:
    """Refresh and store the lock for a version that is being saved or approved."""
    await db.flush()
    return await build_lock_document(db, agent, version, persist=True)


async def pinned_versions(db: AsyncSession, components: Iterable[Any]) -> dict[tuple[str, uuid.UUID], Any]:
    """The version row each component is pinned to, keyed by (type, listing id)."""
    components = list(components)
    versions = await _versions_for(db, [(c.component_type, c.component_id) for c in components])
    pinned: dict[tuple[str, uuid.UUID], Any] = {}
    for component in components:
        key = (component.component_type, component.component_id)
        row, _source = _pinned_row(component, versions.get(key, []))
        if row is not None:
            pinned[key] = row
    return pinned


async def pinned_component_blockers(db: AsyncSession, components: Iterable[Any]) -> list[dict]:
    """Components whose pinned version is not approved, for the review gate."""
    components = list(components)
    keys = [(c.component_type, c.component_id) for c in components]
    versions = await _versions_for(db, keys)
    listings = await _listings_for(db, keys)
    blockers: list[dict] = []
    for component in components:
        key = (component.component_type, component.component_id)
        listing = listings.get(key)
        if listing is None:
            blockers.append(
                {
                    "component_type": component.component_type,
                    "component_id": str(component.component_id),
                    "name": component.component_name,
                    "version": getattr(component, "resolved_version", "?"),
                    "status": "missing_listing",
                }
            )
            continue
        row, _source = _pinned_row(component, versions.get(key, []))
        if (
            component.component_type == "skill"
            and component.resolved_version_id is not None
            and (row is None or row.id != component.resolved_version_id)
        ):
            blockers.append(
                {
                    "component_type": "skill",
                    "component_id": str(component.component_id),
                    "name": listing.name,
                    "version": component.resolved_version,
                    "status": "missing_pin",
                }
            )
            continue
        if row is None:
            row = next((r for r in versions.get(key, []) if r.id == listing.latest_version_id), None)
        if component.component_type == "skill" and row is not None and _requires_v2_skill_pin(component, row):
            try:
                pinned_digest = content_digest("skill", row)
            except SkillValidationError:
                pinned_digest = None
            if component.resolved_version_id != row.id or component.resolved_digest != pinned_digest:
                blockers.append(
                    {
                        "component_type": "skill",
                        "component_id": str(component.component_id),
                        "name": listing.name,
                        "version": row.version,
                        "status": "invalid_bundle_pin",
                    }
                )
                continue
        if component.component_type == "skill" and row is not None and row.delivery_mode == "registry_direct":
            try:
                verified_skill_revision(listing, row)
            except SkillValidationError:
                blockers.append(
                    {
                        "component_type": "skill",
                        "component_id": str(component.component_id),
                        "name": listing.name,
                        "version": row.version,
                        "status": "invalid_review_revision",
                    }
                )
                continue
        status = getattr(row, "status", None)
        if status in INSTALLABLE_STATUSES and not (
            component.component_type == "skill" and getattr(row, "requires_global_review", False) is True
        ):
            continue
        blockers.append(
            {
                "component_type": component.component_type,
                "component_id": str(component.component_id),
                "name": getattr(listing, "name", "") or component.component_name,
                "version": getattr(row, "version", component.resolved_version),
                "status": (
                    "pending_public_review"
                    if component.component_type == "skill" and getattr(row, "requires_global_review", False) is True
                    else getattr(status, "value", "missing")
                ),
            }
        )
    return blockers


async def pin_freshness(db: AsyncSession, components: Iterable[Any]) -> list[dict]:
    """For each pin, the latest approved release and whether the pin is behind it."""
    components = list(components)
    keys = [(c.component_type, c.component_id) for c in components]
    versions = await _versions_for(db, keys)
    listings = await _listings_for(db, keys)
    report: list[dict] = []
    for component in components:
        key = (component.component_type, component.component_id)
        rows = versions.get(key, [])
        listing = listings.get(key)
        row, source = _pinned_row(component, rows)
        latest = latest_release(rows)
        pinned_key = release_key(getattr(row, "version", None))
        latest_key = release_key(getattr(latest, "version", None))
        report.append(
            {
                "type": component.component_type,
                "id": str(component.component_id),
                "name": getattr(listing, "name", "") or component.component_name,
                "qualified_name": getattr(listing, "qualified_name", ""),
                # An unlocked legacy pin reports what it recorded, usually "latest".
                "pinned_version": getattr(row, "version", None) or component.resolved_version,
                "latest_version": getattr(latest, "version", None),
                "outdated": bool(pinned_key and latest_key and latest_key > pinned_key),
                "archived": getattr(listing, "status", None) == ListingStatus.archived,
                "locked": source != "fallback-latest",
            }
        )
    return report


# ---------------------------------------------------------------------------
# Read path
# ---------------------------------------------------------------------------

_IDENTITY_EXTRAS = frozenset({"qualified_name", "visibility", "latest_version", "versions", "validation_results"})
_ALIASES = {"git_url": "source_url", "git_ref": "source_ref"}
_IDENTITY_CACHE: dict[type, frozenset[str]] = {}


def _identity_fields(listing: Any) -> frozenset[str]:
    cls = type(listing)
    if cls not in _IDENTITY_CACHE:
        table = getattr(cls, "__table__", None)
        columns = {column.key for column in table.columns} if table is not None else set()
        _IDENTITY_CACHE[cls] = frozenset(columns | _IDENTITY_EXTRAS)
    return _IDENTITY_CACHE[cls]


class PinnedListing:
    """A listing whose content comes from one pinned version row.

    Config generators read listings through attribute access, and listing
    properties delegate content to ``latest_version``. This proxy keeps the
    listing's identity (``id``, ``name``, ``namespace``, ``slug``, visibility)
    and serves content from the pinned version. ``id`` must stay the listing id:
    install requests key environment and header values by it.
    """

    __slots__ = ("_listing", "_version")

    def __init__(self, listing: Any, version: Any) -> None:
        self._listing = listing
        self._version = version

    @property
    def listing(self) -> Any:
        return self._listing

    @property
    def pinned_version(self) -> Any:
        return self._version

    @property
    def listing_status(self) -> Any:
        return self._listing.status

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__") or name in ("_listing", "_version"):
            raise AttributeError(name)
        listing, version = self._listing, self._version
        if name in _identity_fields(listing):
            return getattr(listing, name)
        if name in _ALIASES and not hasattr(version, name):
            return getattr(version, _ALIASES[name])
        if hasattr(version, name):
            return getattr(version, name)
        return getattr(listing, name)


@dataclass
class LoadedPins:
    """Pinned listings for one install, and what did not match the lock."""

    listings: dict[str, dict[uuid.UUID, Any]] = field(default_factory=dict)
    entries: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return lock_status(self.entries)


async def load_pinned_listings(
    db: AsyncSession,
    components: Iterable[Any],
    listings_by_type: dict[str, dict[uuid.UUID, Any]],
) -> LoadedPins:
    """Replace each visible listing with its pinned version for config generation.

    ``listings_by_type`` holds the listings the caller may install, already
    filtered for visibility. A pin that cannot be found falls back to the
    listing's latest version, which is how legacy agent versions keep working;
    every such fallback, digest mismatch, or unapproved pin is reported.
    """
    components = [c for c in components if c.component_id in listings_by_type.get(c.component_type, {})]
    versions = await _versions_for(db, [(c.component_type, c.component_id) for c in components])
    loaded = LoadedPins(listings={kind: dict(rows) for kind, rows in listings_by_type.items()})
    for component in components:
        kind = component.component_type
        listing = listings_by_type[kind][component.component_id]
        row, source = _pinned_row(component, versions.get((kind, component.component_id), []))
        label = f"{kind} '{listing.name}'"
        if kind == "skill" and getattr(component, "resolved_version_id", None) is not None and source != "lock":
            # A missing explicit UUID must not silently become a semver match
            # or the listing's latest release. Old ID-less resource-less locks
            # still use the documented compatibility fallback below.
            raise HTTPException(status_code=409, detail=f"{label} pinned version no longer exists")
        if row is None:
            row = getattr(listing, "latest_version", None)
            loaded.problems.append(f"{label} is not locked")
            loaded.warnings.append(
                f"{label} has no locked version in this agent release; installed the latest version "
                f"{getattr(row, 'version', '?')}. Ask the agent author to release a new version."
            )
        if row is None:
            loaded.entries.append(_entry(component, None, listing, source))
            continue
        if kind == "skill" and needs_bundle_delivery(row) and source != "lock":
            raise HTTPException(status_code=409, detail=f"{label} requires an exact v2 version pin")
        try:
            entry = _entry(component, row, listing, source)
        except SkillValidationError as exc:
            raise HTTPException(status_code=409, detail=f"{label} has an invalid stored skill folder") from exc
        if kind == "skill" and _requires_v2_skill_pin(component, row) and component.resolved_digest != entry["digest"]:
            raise HTTPException(status_code=409, detail=f"{label} pinned folder differs from its v2 digest")
        if kind == "skill" and row.delivery_mode == "registry_direct":
            try:
                verified_skill_revision(listing, row)
            except SkillValidationError as exc:
                raise HTTPException(status_code=409, detail=f"{label} has an invalid stored skill folder") from exc
        loaded.entries.append(entry)
        loaded.listings[kind][component.component_id] = PinnedListing(listing, row)
        if component.resolved_digest and source != "fallback-latest" and component.resolved_digest != entry["digest"]:
            loaded.problems.append(f"{label} {row.version} changed after it was locked")
            loaded.warnings.append(
                f"{label} {row.version} no longer matches the digest recorded when this agent version was locked."
            )
        if row.status not in INSTALLABLE_STATUSES:
            loaded.problems.append(f"{label} {row.version} is {row.status.value}")
            loaded.warnings.append(f"{label} {row.version} is {row.status.value}, not approved.")
    return loaded


async def select_install_version(
    db: AsyncSession, component_type: str, listing: Any, requested: str | None
) -> Any | None:
    """The version row a standalone install asked for, or None for the current release.

    A requested version must be approved or archived, or share the listing's own
    status so owners can install their pending submissions.
    """
    if not requested:
        return None
    model = VERSION_MODELS[component_type]
    row = (
        await db.execute(select(model).where(model.listing_id == listing.id, model.version == requested))
    ).scalar_one_or_none()
    if row is None or row.status not in {*INSTALLABLE_STATUSES, listing.status}:
        raise HTTPException(status_code=404, detail=f"Version {requested!r} not found for this {component_type}")
    return row
