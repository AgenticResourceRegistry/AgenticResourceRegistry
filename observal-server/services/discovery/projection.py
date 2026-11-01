# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Project native listings into ``discovery_entries``.

The projection is a pure function of the native rows: running it twice on the
same data yields the same entry (same identifier, same content hash). It is
called from three places — the session hook that reacts to lifecycle changes,
the maintenance job that reprojects everything as a safety net, and the
backfill on first deploy — and all three go through :func:`project_entity`.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from loguru import logger as optic
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from models.agent import AgentStatus, AgentVersion
from models.discovery_entry import (
    DiscoveryEntry,
    DiscoveryKind,
    DiscoveryLifecycle,
    DiscoverySourceKind,
    DiscoveryVisibility,
)
from models.mcp import ListingStatus
from services.discovery.adapters import ADAPTERS, NATIVE_KINDS, NATIVE_MODELS, Projected
from services.discovery.identity import (
    DEFAULT_PUBLISHER_DOMAIN,
    KIND_MEDIA_TYPES,
    build_urn,
    identity_uri,
    is_publisher_domain,
    publisher_domain_from_url,
)
from services.versioning import parse_semver

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# ── Context ──────────────────────────────────────────────────────────────


PUBLISHER_DOMAIN_SETTING = "discovery.publisher_domain"


@dataclass(frozen=True, slots=True)
class ProjectionContext:
    """Deployment facts the projection needs: who publishes, and where artifacts live.

    The publisher domain is part of every permanent identifier, so once a real
    domain has been used it is pinned in the ``discovery.publisher_domain``
    setting and no longer follows ``deployment.public_url``. The artifact base
    URL is a location, not an identity, and always follows the public URL.
    """

    publisher_domain: str
    artifact_base_url: str  # e.g. https://observal.acme.com

    @classmethod
    def from_public_url(cls, public_url: str | None, *, pinned_domain: str | None = None) -> ProjectionContext:
        domain = pinned_domain if pinned_domain and is_publisher_domain(pinned_domain) else None
        domain = domain or publisher_domain_from_url(public_url)
        base = (public_url or "").strip().rstrip("/")
        if not base:
            base = f"https://{domain}"
        elif "://" not in base:
            base = f"https://{base}"
        return cls(publisher_domain=domain, artifact_base_url=base)

    def artifact_url(self, kind: DiscoveryKind, entity_id: uuid.UUID, version: str) -> str:
        return f"{self.artifact_base_url}/api/v1/artifacts/{kind.value}/{entity_id}/{version}"


def default_context() -> ProjectionContext:
    import services.dynamic_settings as ds

    return ProjectionContext.from_public_url(
        ds.get_sync("deployment.public_url", ""),
        pinned_domain=ds.get_sync(PUBLISHER_DOMAIN_SETTING, ""),
    )


async def resolve_context(db: AsyncSession) -> ProjectionContext:
    """Like :func:`default_context`, but pins the publisher domain the first time a real one is seen.

    Identifiers minted under the placeholder domain are expected to change when
    the deployment is configured; identifiers minted under a real domain must
    not change again just because ``deployment.public_url`` moves.
    """
    import services.dynamic_settings as ds
    from models.enterprise_config import EnterpriseConfig

    public_url = await ds.get("deployment.public_url", "")
    pinned = await ds.get(PUBLISHER_DOMAIN_SETTING, "")
    ctx = ProjectionContext.from_public_url(public_url, pinned_domain=pinned)
    if not pinned and ctx.publisher_domain != DEFAULT_PUBLISHER_DOMAIN:
        existing = (
            await db.execute(select(EnterpriseConfig).where(EnterpriseConfig.key == PUBLISHER_DOMAIN_SETTING))
        ).scalar_one_or_none()
        if existing is None:
            db.add(EnterpriseConfig(key=PUBLISHER_DOMAIN_SETTING, value=ctx.publisher_domain))
            await db.flush()
            await ds.invalidate(PUBLISHER_DOMAIN_SETTING)
            optic.info("discovery publisher domain pinned to {}", ctx.publisher_domain)
    return ctx


# ── Version selection ────────────────────────────────────────────────────

_APPROVED = {ListingStatus.approved, AgentStatus.approved}
_ARCHIVED = {ListingStatus.archived, AgentStatus.archived}


def _semver_key(version: Any) -> tuple:
    parsed = parse_semver(version.version) or (-1, -1, -1)
    created = getattr(version, "created_at", None) or getattr(version, "released_at", None)
    return (*parsed, created or datetime.min.replace(tzinfo=UTC))


def choose_version(listing: Any, versions: list[Any]) -> tuple[Any | None, DiscoveryLifecycle]:
    """Pick the version an entry should point at, and the lifecycle it implies.

    The listing's own status (its latest version's status) decides archived.
    Otherwise the newest approved version wins so everyone sees a usable
    resource; if nothing is approved yet, the newest version is exposed with
    its real status so owners can find their own pending work.
    """
    if not versions:
        return None, DiscoveryLifecycle.draft
    ordered = sorted(versions, key=_semver_key, reverse=True)
    latest = ordered[0]
    if getattr(latest, "status", None) in _ARCHIVED:
        return latest, DiscoveryLifecycle.archived
    stable = [v for v in ordered if not getattr(v, "is_prerelease", False)] or ordered
    approved = [v for v in stable if getattr(v, "status", None) in _APPROVED]
    if approved:
        return approved[0], DiscoveryLifecycle.approved
    status = getattr(latest, "status", None)
    value = status.value if hasattr(status, "value") else str(status or "draft")
    try:
        return latest, DiscoveryLifecycle(value)
    except ValueError:
        return latest, DiscoveryLifecycle.draft


# ── Building an entry ────────────────────────────────────────────────────

_TOKEN_RE = re.compile(r"[^a-z0-9]+")


def build_search_document(*parts: Any) -> str:
    """Lower-cased, space-joined text the LIKE prefilter runs over."""
    words: list[str] = []
    for part in parts:
        if part is None:
            continue
        if isinstance(part, list | tuple | set):
            words.extend(build_search_document(*part).split())
            continue
        words.extend(w for w in _TOKEN_RE.sub(" ", str(part).lower()).split() if w)
    seen: set[str] = set()
    out: list[str] = []
    for word in words:
        if word not in seen:
            seen.add(word)
            out.append(word)
    return " ".join(out)


def _visibility(listing: Any) -> DiscoveryVisibility:
    if not getattr(listing, "is_private", False):
        return DiscoveryVisibility.public
    return DiscoveryVisibility.team if getattr(listing, "team_id", None) else DiscoveryVisibility.owner


def _owner_id(listing: Any) -> uuid.UUID | None:
    return getattr(listing, "submitted_by", None) or getattr(listing, "created_by", None)


def _co_author_ids(listing: Any) -> str:
    return " ".join(str(uid) for uid in (getattr(listing, "co_authors", None) or []))


def _content_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_ard_entry(
    ctx: ProjectionContext,
    kind: DiscoveryKind,
    listing: Any,
    version: Any,
    projected: Projected,
    lifecycle: DiscoveryLifecycle,
) -> dict[str, Any]:
    """The ARD JSON for an entry: core terms plus Observal's ``obs:`` namespace."""
    identifier = build_urn(ctx.publisher_domain, kind, listing.id)
    entry: dict[str, Any] = {
        "@context": ["https://agenticresourcediscovery.org/context/v1", {"obs": "https://observal.io/ns#"}],
        "identifier": identifier,
        "displayName": projected.display_name,
        "type": KIND_MEDIA_TYPES[kind],
        "url": ctx.artifact_url(kind, listing.id, version.version),
        "description": projected.description,
        "version": version.version,
        "capabilities": projected.capabilities,
        "representativeQueries": projected.representative_queries,
        "tags": projected.tags,
        "trustManifest": {"identity": identity_uri(ctx.publisher_domain), "identityType": "https"},
        "obs:kind": kind.value,
        "obs:nativeRef": f"{listing.namespace}/{listing.slug}@{version.version}",
        "obs:supportedHarnesses": projected.supported_harnesses,
        "obs:lifecycle": lifecycle.value,
        "obs:visibility": _visibility(listing).value,
        "obs:activatable": projected.activatable and lifecycle == DiscoveryLifecycle.approved,
        "obs:artifactDigest": projected.artifact.digest,
        "obs:recommended": bool(getattr(listing, "is_recommended", False)),
        "obs:capabilitiesSource": "derived",
        "obs:representativeQueriesSource": "derived",
    }
    updated = getattr(version, "reviewed_at", None) or getattr(version, "created_at", None)
    if updated:
        entry["updatedAt"] = updated.isoformat()
    entry.update(projected.extra)
    return entry


async def _load_listing(db: AsyncSession, kind: DiscoveryKind, entity_id: uuid.UUID) -> tuple[Any | None, list[Any]]:
    listing_model, version_model, fk = NATIVE_MODELS[kind]
    listing = (await db.execute(select(listing_model).where(listing_model.id == entity_id))).scalar_one_or_none()
    if listing is None:
        return None, []
    stmt = select(version_model).where(getattr(version_model, fk) == entity_id)
    if kind == DiscoveryKind.agent:
        stmt = stmt.options(selectinload(AgentVersion.components))
    versions = list((await db.execute(stmt)).scalars().all())
    return listing, versions


async def _existing_entry(db: AsyncSession, kind: DiscoveryKind, entity_id: uuid.UUID) -> DiscoveryEntry | None:
    stmt = select(DiscoveryEntry).where(DiscoveryEntry.kind == kind, DiscoveryEntry.local_entity_id == entity_id)
    return (await db.execute(stmt)).scalar_one_or_none()


def _tombstone(entry: DiscoveryEntry | None, now: datetime) -> DiscoveryEntry | None:
    if entry is not None and entry.tombstoned_at is None:
        entry.tombstoned_at = now
        entry.activatable = False
    return entry


async def project_entity(
    db: AsyncSession,
    kind: DiscoveryKind,
    entity_id: uuid.UUID,
    *,
    ctx: ProjectionContext | None = None,
    now: datetime | None = None,
) -> DiscoveryEntry | None:
    """Create, refresh or tombstone the entry for one native resource.

    Returns the entry (possibly tombstoned) or ``None`` when the resource never
    existed. Does not commit; callers own the transaction.
    """
    ctx = ctx or default_context()
    now = now or datetime.now(UTC)
    listing, versions = await _load_listing(db, kind, entity_id)
    existing = await _existing_entry(db, kind, entity_id)

    if listing is None or getattr(listing, "deleted_at", None) is not None:
        return _tombstone(existing, now)

    version, lifecycle = choose_version(listing, versions)
    if version is None:
        return _tombstone(existing, now)

    projected = ADAPTERS[kind](listing, version)
    ard = build_ard_entry(ctx, kind, listing, version, projected, lifecycle)
    content_hash = _content_hash(ard)

    entry = existing or DiscoveryEntry(id=uuid.uuid4(), kind=kind, local_entity_id=listing.id, indexed_at=now)
    if existing is not None and existing.content_hash == content_hash and existing.tombstoned_at is None:
        existing.last_seen_at = now
        return existing

    entry.ard_identifier = ard["identifier"]
    entry.media_type = ard["type"]
    entry.display_name = projected.display_name[:255]
    entry.description = projected.description
    entry.capabilities = projected.capabilities
    entry.capabilities_source = "derived"
    entry.representative_queries = projected.representative_queries
    entry.representative_queries_source = "derived"
    entry.tags = projected.tags
    entry.version = version.version
    entry.artifact_url = ard["url"]
    entry.artifact_digest = projected.artifact.digest
    entry.updated_at_source = getattr(version, "reviewed_at", None) or getattr(version, "created_at", None)
    entry.native_ref = ard["obs:nativeRef"][:160]
    entry.local_version_id = version.id
    entry.supported_harnesses = projected.supported_harnesses
    entry.source_kind = DiscoverySourceKind.local
    entry.publisher_domain = ctx.publisher_domain
    entry.visibility = _visibility(listing)
    entry.team_id = getattr(listing, "team_id", None)
    entry.owner_user_id = _owner_id(listing)
    entry.co_author_ids = _co_author_ids(listing)
    entry.lifecycle_status = lifecycle
    entry.activatable = bool(ard["obs:activatable"])
    entry.search_document = build_search_document(
        projected.display_name,
        listing.slug,
        listing.namespace,
        kind.value,
        projected.description,
        projected.representative_queries,
        projected.capabilities,
        projected.tags,
        projected.supported_harnesses,
    )
    entry.raw_entry = ard
    entry.content_hash = content_hash
    entry.last_seen_at = now
    entry.tombstoned_at = None
    if existing is None:
        db.add(entry)
    return entry


# ── Bulk reprojection ────────────────────────────────────────────────────


@dataclass(slots=True)
class ProjectionStats:
    projected: int = 0
    tombstoned: int = 0
    failed: int = 0


async def reproject_all(
    db: AsyncSession,
    *,
    kinds: tuple[DiscoveryKind, ...] = NATIVE_KINDS,
    ctx: ProjectionContext | None = None,
) -> ProjectionStats:
    """Reproject every native resource and tombstone entries whose source is gone.

    Idempotent and safe to run at any time; this is the safety net under the
    per-change hook. Commits once at the end.
    """
    ctx = ctx or await resolve_context(db)
    now = datetime.now(UTC)
    stats = ProjectionStats()

    for kind in kinds:
        listing_model = NATIVE_MODELS[kind][0]
        ids = list((await db.execute(select(listing_model.id))).scalars().all())
        live: set[uuid.UUID] = set()
        failed: set[uuid.UUID] = set()
        for entity_id in ids:
            # A SAVEPOINT per resource: one bad row must not abort the batch
            # transaction and take every other update down with it.
            try:
                async with db.begin_nested():
                    entry = await project_entity(db, kind, entity_id, ctx=ctx, now=now)
            except Exception:
                stats.failed += 1
                failed.add(entity_id)
                optic.exception("discovery projection failed kind={} id={}", kind.value, entity_id)
                continue
            if entry is None:
                continue
            if entry.tombstoned_at is None:
                live.add(entity_id)
                stats.projected += 1
            else:
                stats.tombstoned += 1

        stale_stmt = select(DiscoveryEntry).where(
            DiscoveryEntry.kind == kind,
            DiscoveryEntry.source_kind == DiscoverySourceKind.local,
            DiscoveryEntry.tombstoned_at.is_(None),
        )
        for entry in (await db.execute(stale_stmt)).scalars().all():
            # A resource that failed to project is unknown, not gone; leave its entry alone.
            if entry.local_entity_id not in live and entry.local_entity_id not in failed:
                _tombstone(entry, now)
                stats.tombstoned += 1

    await db.commit()
    optic.info(
        "discovery reprojection projected={} tombstoned={} failed={}", stats.projected, stats.tombstoned, stats.failed
    )
    return stats


__all__ = [
    "PUBLISHER_DOMAIN_SETTING",
    "ProjectionContext",
    "ProjectionStats",
    "build_ard_entry",
    "build_search_document",
    "choose_version",
    "default_context",
    "project_entity",
    "reproject_all",
    "resolve_context",
]
