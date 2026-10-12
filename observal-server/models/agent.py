# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
# SPDX-FileCopyrightText: 2026 Subramania Raja <dhanpraja231@gmail.com>
# SPDX-FileCopyrightText: 2026 Harishankar <harishankar0301@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base


class AgentStatus(str, enum.Enum):
    draft = "draft"
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    archived = "archived"


class AgentVersion(Base):
    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_version"),
        Index("ix_agent_versions_agent_id", "agent_id"),
        Index("ix_agent_versions_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    model_config_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # Per-harness model overrides: {"claude-code": "claude-sonnet-4-5", "kiro": "claude-opus-4-5", ...}
    # Empty dict means "use model_name as the default for every harness that accepts a model choice".
    # Missing key for an harness that accepts a choice means "emit auto sentinel".
    models_by_harness: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    external_mcps: Mapped[list] = mapped_column(JSON, default=list)
    supported_harnesses: Mapped[list] = mapped_column(JSON, default=list)
    required_capabilities: Mapped[list] = mapped_column(JSON, default=list)
    inferred_supported_harnesses: Mapped[list] = mapped_column(JSON, default=list)
    yaml_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    harness_configs: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    lock_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[AgentStatus] = mapped_column(Enum(AgentStatus), default=AgentStatus.pending)
    is_prerelease: Mapped[bool] = mapped_column(Boolean, default=False)
    promoted_from: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    download_count: Mapped[int] = mapped_column(Integer, default=0)
    released_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    released_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    is_editing: Mapped[bool] = mapped_column(Boolean, default=False)
    editing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    editing_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    gaming_flags: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    success_criteria: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    agent: Mapped["Agent"] = relationship(back_populates="versions", foreign_keys=[agent_id])
    components: Mapped[list["AgentComponent"]] = relationship(
        back_populates="agent_version",
        lazy="selectin",
        order_by="AgentComponent.order_index",
        cascade="all, delete-orphan",
    )


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    namespace: Mapped[str] = mapped_column(String(32), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    is_private: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="RESTRICT"), nullable=True
    )
    co_authors: Mapped[list] = mapped_column(JSON, default=list)
    latest_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_versions.id", use_alter=True, ondelete="SET NULL"),
        nullable=True,
    )
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    is_recommended: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    forked_from_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    forked_from_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_versions.id", ondelete="SET NULL"), nullable=True
    )
    forked_from_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    forked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "uq_agents_active_namespace_slug",
            "namespace",
            "slug",
            unique=True,
            postgresql_where=deleted_at.is_(None),
            sqlite_where=deleted_at.is_(None),
        ),
        Index("ix_agents_team_id", "team_id"),
        Index("ix_agents_forked_from_id", "forked_from_id"),
    )

    versions: Mapped[list["AgentVersion"]] = relationship(
        back_populates="agent",
        lazy="selectin",
        cascade="all, delete-orphan",
        foreign_keys="AgentVersion.agent_id",
    )
    latest_version: Mapped["AgentVersion | None"] = relationship(
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
    # version-aware API endpoints (issues #620/#621) replace them.
    # ------------------------------------------------------------------
    @property
    def version(self) -> str:
        return self.latest_version.version if self.latest_version else "0.0.0"

    @version.setter
    def version(self, value: str) -> None:
        if not self.latest_version:
            raise RuntimeError("Agent has no latest_version; cannot set version")
        self.latest_version.version = value

    @property
    def description(self) -> str:
        return self.latest_version.description if self.latest_version else ""

    @description.setter
    def description(self, value: str) -> None:
        if not self.latest_version:
            raise RuntimeError("Agent has no latest_version; cannot set description")
        self.latest_version.description = value

    @property
    def prompt(self) -> str:
        return self.latest_version.prompt if self.latest_version else ""

    @prompt.setter
    def prompt(self, value: str) -> None:
        if not self.latest_version:
            raise RuntimeError("Agent has no latest_version; cannot set prompt")
        self.latest_version.prompt = value

    @property
    def model_name(self) -> str:
        return self.latest_version.model_name if self.latest_version else ""

    @model_name.setter
    def model_name(self, value: str) -> None:
        if not self.latest_version:
            raise RuntimeError("Agent has no latest_version; cannot set model_name")
        self.latest_version.model_name = value

    @property
    def model_config_json(self) -> dict:
        return self.latest_version.model_config_json if self.latest_version else {}

    @model_config_json.setter
    def model_config_json(self, value: dict) -> None:
        if not self.latest_version:
            raise RuntimeError("Agent has no latest_version; cannot set model_config_json")
        self.latest_version.model_config_json = value

    @property
    def models_by_harness(self) -> dict:
        return self.latest_version.models_by_harness if self.latest_version else {}

    @models_by_harness.setter
    def models_by_harness(self, value: dict) -> None:
        if not self.latest_version:
            raise RuntimeError("Agent has no latest_version; cannot set models_by_harness")
        self.latest_version.models_by_harness = value

    @property
    def external_mcps(self) -> list:
        return self.latest_version.external_mcps if self.latest_version else []

    @external_mcps.setter
    def external_mcps(self, value: list) -> None:
        if not self.latest_version:
            raise RuntimeError("Agent has no latest_version; cannot set external_mcps")
        self.latest_version.external_mcps = value

    @property
    def supported_harnesses(self) -> list:
        return self.latest_version.supported_harnesses if self.latest_version else []

    @supported_harnesses.setter
    def supported_harnesses(self, value: list) -> None:
        if not self.latest_version:
            raise RuntimeError("Agent has no latest_version; cannot set supported_harnesses")
        self.latest_version.supported_harnesses = value

    @property
    def required_capabilities(self) -> list:
        return self.latest_version.required_capabilities if self.latest_version else []

    @required_capabilities.setter
    def required_capabilities(self, value: list) -> None:
        if not self.latest_version:
            raise RuntimeError("Agent has no latest_version; cannot set required_capabilities")
        self.latest_version.required_capabilities = value

    @property
    def inferred_supported_harnesses(self) -> list:
        return self.latest_version.inferred_supported_harnesses if self.latest_version else []

    @inferred_supported_harnesses.setter
    def inferred_supported_harnesses(self, value: list) -> None:
        if not self.latest_version:
            raise RuntimeError("Agent has no latest_version; cannot set inferred_supported_harnesses")
        self.latest_version.inferred_supported_harnesses = value

    @property
    def status(self) -> "AgentStatus":
        return self.latest_version.status if self.latest_version else AgentStatus.draft

    @status.setter
    def status(self, value: "AgentStatus") -> None:
        if not self.latest_version:
            raise RuntimeError("Agent has no latest_version; cannot set status")
        self.latest_version.status = value

    @property
    def rejection_reason(self) -> str | None:
        return self.latest_version.rejection_reason if self.latest_version else None

    @rejection_reason.setter
    def rejection_reason(self, value: str | None) -> None:
        if not self.latest_version:
            raise RuntimeError("Agent has no latest_version; cannot set rejection_reason")
        self.latest_version.rejection_reason = value

    @property
    def download_count(self) -> int:
        return self.latest_version.download_count if self.latest_version else 0

    @property
    def unique_users(self) -> int:
        return 0

    @property
    def git_url(self) -> str | None:
        return None

    @property
    def components(self) -> list:
        return self.latest_version.components if self.latest_version else []

    @property
    def success_criteria(self) -> dict | None:
        return self.latest_version.success_criteria if self.latest_version else None


from models.agent_component import AgentComponent  # noqa: E402

AgentComponent.agent_version = relationship("AgentVersion", back_populates="components")
