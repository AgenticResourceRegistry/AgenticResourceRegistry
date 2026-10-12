# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base
from models.mcp import ListingStatus


class SkillListing(Base):
    __tablename__ = "skill_listings"
    __table_args__ = (
        UniqueConstraint("namespace", "slug", name="uq_skill_listings_namespace_slug"),
        Index("ix_skill_listings_namespace", "namespace"),
        Index("ix_skill_listings_submitted_by", "submitted_by"),
        Index("ix_skill_listings_team_id", "team_id"),
        Index("ix_skill_listings_forked_from_id", "forked_from_id"),
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
        UUID(as_uuid=True), ForeignKey("skill_listings.id", ondelete="SET NULL"), nullable=True
    )
    forked_from_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skill_versions.id", ondelete="SET NULL"), nullable=True
    )
    forked_from_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    forked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latest_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skill_versions.id", use_alter=True, ondelete="SET NULL"),
        nullable=True,
    )

    versions: Mapped[list[SkillVersion]] = relationship(
        back_populates="listing",
        lazy="selectin",
        cascade="all, delete-orphan",
        foreign_keys="SkillVersion.listing_id",
    )
    latest_version: Mapped[SkillVersion | None] = relationship(
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
    def skill_path(self) -> str:
        return self.latest_version.skill_path if self.latest_version else "/"

    @skill_path.setter
    def skill_path(self, value: str) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set skill_path")
        self.latest_version.skill_path = value

    @property
    def git_url(self) -> str | None:
        return self.latest_version.git_url if self.latest_version else None

    @git_url.setter
    def git_url(self, value: str | None) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set git_url")
        self.latest_version.git_url = value

    @property
    def git_ref(self) -> str | None:
        return self.latest_version.git_ref if self.latest_version else None

    @git_ref.setter
    def git_ref(self, value: str | None) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set git_ref")
        self.latest_version.git_ref = value

    @property
    def skill_md_content(self) -> str | None:
        return self.latest_version.skill_md_content if self.latest_version else None

    @skill_md_content.setter
    def skill_md_content(self, value: str | None) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set skill_md_content")
        self.latest_version.skill_md_content = value

    @property
    def delivery_mode(self) -> str:
        return self.latest_version.delivery_mode if self.latest_version else "git_fetch"

    @delivery_mode.setter
    def delivery_mode(self, value: str) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set delivery_mode")
        self.latest_version.delivery_mode = value

    @property
    def extra_files(self) -> list[dict]:
        # JSON columns are not mutable-tracked: prevent in-place edits that
        # appear to work but disappear when the session commits.
        return deepcopy(self.latest_version.extra_files) if self.latest_version else []

    @extra_files.setter
    def extra_files(self, value: list[dict]) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set extra_files")
        self.latest_version.extra_files = value

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
    def validated(self) -> bool:
        return self.latest_version.validated if self.latest_version else False

    @validated.setter
    def validated(self, value: bool) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set validated")
        self.latest_version.validated = value

    @property
    def target_agents(self) -> list:
        return self.latest_version.target_agents if self.latest_version else []

    @target_agents.setter
    def target_agents(self, value: list) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set target_agents")
        self.latest_version.target_agents = value

    @property
    def task_type(self) -> str:
        return self.latest_version.task_type if self.latest_version else ""

    @task_type.setter
    def task_type(self, value: str) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set task_type")
        self.latest_version.task_type = value

    @property
    def slash_command(self) -> str | None:
        return self.latest_version.slash_command if self.latest_version else None

    @slash_command.setter
    def slash_command(self, value: str | None) -> None:
        if not self.latest_version:
            raise RuntimeError(f"{type(self).__name__} has no latest_version; cannot set slash_command")
        self.latest_version.slash_command = value


class SkillDownload(Base):
    __tablename__ = "skill_downloads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    listing_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("skill_listings.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    harness: Mapped[str] = mapped_column(String(50), nullable=False)
    downloaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class SkillVersion(Base):
    __tablename__ = "skill_versions"
    __table_args__ = (
        UniqueConstraint("listing_id", "version"),
        Index("ix_skill_versions_listing_id", "listing_id"),
        Index("ix_skill_versions_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skill_listings.id", ondelete="CASCADE"), nullable=False
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
    skill_path: Mapped[str] = mapped_column(String(500), default="/")
    git_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    git_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    skill_md_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_mode: Mapped[str] = mapped_column(String(20), server_default="git_fetch", nullable=False)
    script_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    script_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Phase 2 must project/defer this field on list and lock queries. Do not
    # defer it globally yet: version publishing snapshots all loaded columns.
    extra_files: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list, server_default="[]")
    validated: Mapped[bool] = mapped_column(Boolean, default=False)
    target_agents: Mapped[list] = mapped_column(JSON, default=list)
    task_type: Mapped[str] = mapped_column(String(100), nullable=False)
    slash_command: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_editing: Mapped[bool] = mapped_column(Boolean, default=False)
    editing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    editing_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    # Saved drafts inherit only from an approved base. Existing versions stay
    # NULL until their first explicit revision-bearing write or review.
    base_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skill_versions.id", name="fk_skill_versions_base_version_id", ondelete="SET NULL"),
        nullable=True,
    )
    base_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    review_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    requires_global_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    pre_public_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    pre_public_reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_skill_versions_pre_public_reviewed_by", ondelete="SET NULL"),
        nullable=True,
    )
    pre_public_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    listing: Mapped[SkillListing] = relationship(back_populates="versions", foreign_keys=[listing_id])
