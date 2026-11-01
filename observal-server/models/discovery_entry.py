# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Discovery read model.

One row per discoverable resource, shaped for search and for serialisation as
an ARD entry. Rows are projected from the native listing/version tables
(``services.discovery.projection``) and are never edited by hand: the native
tables stay authoritative, and a reprojection of the same source rows always
produces the same entry. See ``docs/adr/0001-agentic-resource-discovery.md``.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Enum, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class DiscoveryKind(str, enum.Enum):
    """Resource kind. Native kinds match Observal's component vocabulary."""

    agent = "agent"
    mcp = "mcp"
    skill = "skill"
    hook = "hook"
    prompt = "prompt"
    sandbox = "sandbox"
    external = "external"


class DiscoverySourceKind(str, enum.Enum):
    local = "local"  # projected from a native Observal listing
    imported = "imported"  # external resource brought into the governed registry
    mirrored = "mirrored"  # cached from a configured catalog
    federated = "federated"  # returned live from another registry, never persisted as owned
    generated = "generated"  # derived from observed artifacts


class DiscoveryLifecycle(str, enum.Enum):
    """Lifecycle of the version an entry currently points at."""

    approved = "approved"
    pending = "pending"
    rejected = "rejected"
    archived = "archived"
    draft = "draft"


class DiscoveryVisibility(str, enum.Enum):
    public = "public"
    team = "team"  # private, owned by a team: visible to members
    owner = "owner"  # private, no team: visible to the submitter and co-authors


class DiscoveryEntry(Base):
    __tablename__ = "discovery_entries"
    __table_args__ = (
        UniqueConstraint("ard_identifier", name="uq_discovery_entries_ard_identifier"),
        UniqueConstraint("kind", "local_entity_id", name="uq_discovery_entries_kind_entity"),
        Index("ix_discovery_entries_kind", "kind"),
        Index("ix_discovery_entries_lifecycle", "lifecycle_status"),
        Index("ix_discovery_entries_visibility", "visibility", "team_id"),
        Index("ix_discovery_entries_owner", "owner_user_id"),
        Index("ix_discovery_entries_tombstoned", "tombstoned_at"),
        Index(
            "ix_discovery_entries_search_trgm",
            "search_document",
            postgresql_using="gin",
            postgresql_ops={"search_document": "gin_trgm_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # ── ARD identity and descriptive terms ──────────────────────────────
    ard_identifier: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[DiscoveryKind] = mapped_column(Enum(DiscoveryKind), nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    capabilities: Mapped[list] = mapped_column(JSON, default=list)
    capabilities_source: Mapped[str] = mapped_column(String(16), nullable=False, default="derived")
    representative_queries: Mapped[list] = mapped_column(JSON, default=list)
    representative_queries_source: Mapped[str] = mapped_column(String(16), nullable=False, default="derived")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    artifact_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    artifact_digest: Mapped[str | None] = mapped_column(String(80), nullable=True)
    updated_at_source: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Link back to the native resource ────────────────────────────────
    native_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)  # namespace/slug@version
    local_entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    local_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    supported_harnesses: Mapped[list] = mapped_column(JSON, default=list)

    # ── Origin ───────────────────────────────────────────────────────────
    source_kind: Mapped[DiscoverySourceKind] = mapped_column(
        Enum(DiscoverySourceKind), nullable=False, default=DiscoverySourceKind.local
    )
    publisher_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    source_registry_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_identifier: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── Visibility and lifecycle (mirrors the native rules exactly) ─────
    visibility: Mapped[DiscoveryVisibility] = mapped_column(Enum(DiscoveryVisibility), nullable=False)
    team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Space-joined UUID strings so the owner-fallback check is one portable LIKE.
    co_author_ids: Mapped[str] = mapped_column(Text, nullable=False, default="")
    lifecycle_status: Mapped[DiscoveryLifecycle] = mapped_column(Enum(DiscoveryLifecycle), nullable=False)
    activatable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # ── Search ───────────────────────────────────────────────────────────
    # Lower-cased, space-joined tokens. Prefiltered with LIKE (trigram-indexed
    # on PostgreSQL) and ranked in Python; see services.discovery.search.
    search_document: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ── Bookkeeping ──────────────────────────────────────────────────────
    raw_entry: Mapped[dict] = mapped_column(JSON, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    tombstoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def is_live(self) -> bool:
        return self.tombstoned_at is None
