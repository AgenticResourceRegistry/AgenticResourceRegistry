# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base
from models.mcp import ListingStatus


class HookListing(Base):
    __tablename__ = "hook_listings"
    __table_args__ = (
        UniqueConstraint("namespace", "slug", name="uq_hook_listings_namespace_slug"),
        Index("ix_hook_listings_namespace", "namespace"),
        Index("ix_hook_listings_submitted_by", "submitted_by"),
        Index("ix_hook_listings_team_id", "team_id"),
        Index("ix_hook_listings_forked_from_id", "forked_from_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    namespace: Mapped[str] = mapped_column(String(32), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    is_private: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="RESTRICT"), nullable=True
    )
    bundle_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("component_bundles.id"), nullable=True
    )
    submitted_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    co_authors: Mapped[list] = mapped_column(JSON, default=list)
    unique_agents: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    is_recommended: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    forked_from_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hook_listings.id", ondelete="SET NULL"), nullable=True
    )
    forked_from_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hook_versions.id", ondelete="SET NULL"), nullable=True
    )
    forked_from_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    forked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latest_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hook_versions.id", use_alter=True, ondelete="SET NULL"),
        nullable=True,
    )

    versions: Mapped[list[HookVersion]] = relationship(
        back_populates="listing",
        lazy="selectin",
        cascade="all, delete-orphan",
        foreign_keys="HookVersion.listing_id",
    )
    latest_version: Mapped[HookVersion | None] = relationship(
        foreign_keys=[latest_version_id], lazy="selectin", uselist=False, post_update=True
    )

    @property
    def is_fork(self) -> bool:
        return self.forked_from_id is not None or self.forked_from_ref is not None

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}/{self.slug}"

    @property
    def visibility(self) -> str:
        return "team" if self.is_private else "public"

    # ------------------------------------------------------------------
    # Deprecated compatibility properties - delegate to latest_version.
    # ------------------------------------------------------------------
    @property
    def version(self) -> str:
        return self.latest_version.version if self.latest_version else "0.0.0"

    @version.setter
    def version(self, value: str) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set version")
        self.latest_version.version = value

    @property
    def description(self) -> str:
        return self.latest_version.description if self.latest_version else ""

    @description.setter
    def description(self, value: str) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set description")
        self.latest_version.description = value

    @property
    def status(self) -> ListingStatus:
        return self.latest_version.status if self.latest_version else ListingStatus.draft

    @status.setter
    def status(self, value: ListingStatus) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set status")
        self.latest_version.status = value

    @property
    def rejection_reason(self) -> str | None:
        return self.latest_version.rejection_reason if self.latest_version else None

    @rejection_reason.setter
    def rejection_reason(self, value: str | None) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set rejection_reason")
        self.latest_version.rejection_reason = value

    @property
    def download_count(self) -> int:
        return self.latest_version.download_count if self.latest_version else 0

    @property
    def supported_harnesses(self) -> list:
        return self.latest_version.supported_harnesses if self.latest_version else []

    @supported_harnesses.setter
    def supported_harnesses(self, value: list) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set supported_harnesses")
        self.latest_version.supported_harnesses = value

    @property
    def event(self) -> str:
        return self.latest_version.event if self.latest_version else ""

    @event.setter
    def event(self, value: str) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set event")
        self.latest_version.event = value

    @property
    def execution_mode(self) -> str:
        return self.latest_version.execution_mode if self.latest_version else "async"

    @execution_mode.setter
    def execution_mode(self, value: str) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set execution_mode")
        self.latest_version.execution_mode = value

    @property
    def priority(self) -> int:
        return self.latest_version.priority if self.latest_version else 100

    @priority.setter
    def priority(self, value: int) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set priority")
        self.latest_version.priority = value

    @property
    def handler_type(self) -> str:
        return self.latest_version.handler_type if self.latest_version else ""

    @handler_type.setter
    def handler_type(self, value: str) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set handler_type")
        self.latest_version.handler_type = value

    @property
    def handler_config(self) -> dict:
        return self.latest_version.handler_config if self.latest_version else {}

    @handler_config.setter
    def handler_config(self, value: dict) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set handler_config")
        self.latest_version.handler_config = value

    @property
    def scope(self) -> str:
        return self.latest_version.scope if self.latest_version else "agent"

    @scope.setter
    def scope(self, value: str) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set scope")
        self.latest_version.scope = value

    @property
    def tool_filter(self) -> dict | None:
        return self.latest_version.tool_filter if self.latest_version else None

    @tool_filter.setter
    def tool_filter(self, value: dict | None) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set tool_filter")
        self.latest_version.tool_filter = value

    @property
    def source_url(self) -> str | None:
        return self.latest_version.source_url if self.latest_version else None

    @source_url.setter
    def source_url(self, value: str | None) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set source_url")
        self.latest_version.source_url = value

    @property
    def source_ref(self) -> str | None:
        return self.latest_version.source_ref if self.latest_version else None

    @source_ref.setter
    def source_ref(self, value: str | None) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set source_ref")
        self.latest_version.source_ref = value

    @property
    def source_path(self) -> str | None:
        return self.latest_version.source_path if self.latest_version else None

    @source_path.setter
    def source_path(self, value: str | None) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set source_path")
        self.latest_version.source_path = value

    @property
    def resolved_sha(self) -> str | None:
        return self.latest_version.resolved_sha if self.latest_version else None

    @resolved_sha.setter
    def resolved_sha(self, value: str | None) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set resolved_sha")
        self.latest_version.resolved_sha = value

    @property
    def script_content(self) -> str | None:
        return self.latest_version.script_content if self.latest_version else None

    @script_content.setter
    def script_content(self, value: str | None) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set script_content")
        self.latest_version.script_content = value

    @property
    def script_filename(self) -> str | None:
        return self.latest_version.script_filename if self.latest_version else None

    @script_filename.setter
    def script_filename(self, value: str | None) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set script_filename")
        self.latest_version.script_filename = value

    @property
    def requirements(self) -> list | None:
        return self.latest_version.requirements if self.latest_version else None

    @requirements.setter
    def requirements(self, value: list | None) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set requirements")
        self.latest_version.requirements = value


class HookDownload(Base):
    __tablename__ = "hook_downloads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    listing_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("hook_listings.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    harness: Mapped[str] = mapped_column(String(50), nullable=False)
    downloaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class HookVersion(Base):
    __tablename__ = "hook_versions"
    __table_args__ = (
        UniqueConstraint("listing_id", "version"),
        Index("ix_hook_versions_listing_id", "listing_id"),
        Index("ix_hook_versions_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hook_listings.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    changelog: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ListingStatus] = mapped_column(Enum(ListingStatus), default=ListingStatus.pending)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    download_count: Mapped[int] = mapped_column(Integer, default=0)
    released_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    released_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    supported_harnesses: Mapped[list] = mapped_column(JSON, default=list)
    event: Mapped[str] = mapped_column(String(50), nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(10), default="async")
    priority: Mapped[int] = mapped_column(Integer, default=100)
    handler_type: Mapped[str] = mapped_column(String(20), nullable=False)
    handler_config: Mapped[dict] = mapped_column(JSON, default=dict)
    scope: Mapped[str] = mapped_column(String(20), default="agent")
    tool_filter: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Source tracking + script delivery
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    resolved_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    script_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    script_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    requirements: Mapped[list | None] = mapped_column(JSON, nullable=True)
    is_editing: Mapped[bool] = mapped_column(Boolean, default=False)
    editing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    editing_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    listing: Mapped[HookListing] = relationship(back_populates="versions", foreign_keys=[listing_id])
