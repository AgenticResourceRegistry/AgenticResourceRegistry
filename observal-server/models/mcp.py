# SPDX-FileCopyrightText: 2026 Subramania Raja <dhanpraja231@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base


class ListingStatus(str, enum.Enum):
    draft = "draft"
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    archived = "archived"


class McpListing(Base):
    __tablename__ = "mcp_listings"
    __table_args__ = (
        UniqueConstraint("namespace", "slug", name="uq_mcp_listings_namespace_slug"),
        Index("ix_mcp_listings_namespace", "namespace"),
        Index("ix_mcp_listings_submitted_by", "submitted_by"),
        Index("ix_mcp_listings_team_id", "team_id"),
        Index("ix_mcp_listings_forked_from_id", "forked_from_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    namespace: Mapped[str] = mapped_column(String(32), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
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
        UUID(as_uuid=True), ForeignKey("mcp_listings.id", ondelete="SET NULL"), nullable=True
    )
    forked_from_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mcp_versions.id", ondelete="SET NULL"), nullable=True
    )
    forked_from_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    forked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    latest_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mcp_versions.id", use_alter=True, ondelete="SET NULL"),
        nullable=True,
    )

    validation_results: Mapped[list[McpValidationResult]] = relationship(
        back_populates="listing", lazy="selectin", cascade="all, delete-orphan"
    )
    versions: Mapped[list[McpVersion]] = relationship(
        back_populates="listing",
        lazy="selectin",
        cascade="all, delete-orphan",
        foreign_keys="McpVersion.listing_id",
    )
    latest_version: Mapped[McpVersion | None] = relationship(
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
    # These allow existing route/service code to keep working until the
    # version-aware API endpoints replace them.
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
    def changelog(self) -> str | None:
        return self.latest_version.changelog if self.latest_version else None

    @changelog.setter
    def changelog(self, value: str | None) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set changelog")
        self.latest_version.changelog = value

    @property
    def transport(self) -> str | None:
        return self.latest_version.transport if self.latest_version else None

    @transport.setter
    def transport(self, value: str | None) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set transport")
        self.latest_version.transport = value

    @property
    def framework(self) -> str | None:
        return self.latest_version.framework if self.latest_version else None

    @framework.setter
    def framework(self, value: str | None) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set framework")
        self.latest_version.framework = value

    @property
    def docker_image(self) -> str | None:
        return self.latest_version.docker_image if self.latest_version else None

    @docker_image.setter
    def docker_image(self, value: str | None) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set docker_image")
        self.latest_version.docker_image = value

    @property
    def command(self) -> str | None:
        return self.latest_version.command if self.latest_version else None

    @command.setter
    def command(self, value: str | None) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set command")
        self.latest_version.command = value

    @property
    def args(self) -> list | None:
        return self.latest_version.args if self.latest_version else None

    @args.setter
    def args(self, value: list | None) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set args")
        self.latest_version.args = value

    @property
    def url(self) -> str | None:
        return self.latest_version.url if self.latest_version else None

    @url.setter
    def url(self, value: str | None) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set url")
        self.latest_version.url = value

    @property
    def headers(self) -> list | None:
        return self.latest_version.headers if self.latest_version else None

    @headers.setter
    def headers(self, value: list | None) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set headers")
        self.latest_version.headers = value

    @property
    def auto_approve(self) -> list | None:
        return self.latest_version.auto_approve if self.latest_version else None

    @auto_approve.setter
    def auto_approve(self, value: list | None) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set auto_approve")
        self.latest_version.auto_approve = value

    @property
    def mcp_validated(self) -> bool:
        return self.latest_version.mcp_validated if self.latest_version else False

    @mcp_validated.setter
    def mcp_validated(self, value: bool) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set mcp_validated")
        self.latest_version.mcp_validated = value

    @property
    def tools_schema(self) -> dict | None:
        return self.latest_version.tools_schema if self.latest_version else None

    @property
    def environment_variables(self) -> list | None:
        return self.latest_version.environment_variables if self.latest_version else None

    @environment_variables.setter
    def environment_variables(self, value: list | None) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set environment_variables")
        self.latest_version.environment_variables = value

    @property
    def setup_instructions(self) -> str | None:
        return self.latest_version.setup_instructions if self.latest_version else None

    @setup_instructions.setter
    def setup_instructions(self, value: str | None) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set setup_instructions")
        self.latest_version.setup_instructions = value

    @property
    def git_url(self) -> str | None:
        return self.latest_version.source_url if self.latest_version else None

    @git_url.setter
    def git_url(self, value: str | None) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set git_url")
        self.latest_version.source_url = value

    @property
    def git_ref(self) -> str | None:
        return self.latest_version.source_ref if self.latest_version else None

    @git_ref.setter
    def git_ref(self, value: str | None) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set git_ref")
        self.latest_version.source_ref = value


class McpDownload(Base):
    __tablename__ = "mcp_downloads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    listing_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("mcp_listings.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    harness: Mapped[str] = mapped_column(String(50), nullable=False)
    downloaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class McpValidationResult(Base):
    __tablename__ = "mcp_validation_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    listing_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("mcp_listings.id"), nullable=False)
    stage: Mapped[str] = mapped_column(String(100), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    listing: Mapped[McpListing] = relationship(back_populates="validation_results")


class McpVersion(Base):
    __tablename__ = "mcp_versions"
    __table_args__ = (
        UniqueConstraint("listing_id", "version"),
        Index("ix_mcp_versions_listing_id", "listing_id"),
        Index("ix_mcp_versions_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mcp_listings.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    changelog: Mapped[str | None] = mapped_column(Text, nullable=True)
    transport: Mapped[str | None] = mapped_column(String(20), nullable=True)
    framework: Mapped[str | None] = mapped_column(String(100), nullable=True)
    docker_image: Mapped[str | None] = mapped_column(String(500), nullable=True)
    command: Mapped[str | None] = mapped_column(String(500), nullable=True)
    args: Mapped[list | None] = mapped_column(JSON, nullable=True)
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    headers: Mapped[list | None] = mapped_column(JSON, nullable=True)
    auto_approve: Mapped[list | None] = mapped_column(JSON, nullable=True)
    mcp_validated: Mapped[bool] = mapped_column(Boolean, default=False)
    tools_schema: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    environment_variables: Mapped[list | None] = mapped_column(JSON, nullable=True)
    supported_harnesses: Mapped[list] = mapped_column(JSON, default=list)
    setup_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resolved_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[ListingStatus] = mapped_column(Enum(ListingStatus), default=ListingStatus.pending)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    download_count: Mapped[int] = mapped_column(Integer, default=0)
    released_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    released_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    is_editing: Mapped[bool] = mapped_column(Boolean, default=False)
    editing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    editing_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    listing: Mapped[McpListing] = relationship(back_populates="versions", foreign_keys=[listing_id])
