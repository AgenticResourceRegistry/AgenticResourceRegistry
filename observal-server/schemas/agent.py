# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
# SPDX-FileCopyrightText: 2026 Subramania Raja <dhanpraja231@gmail.com>
# SPDX-FileCopyrightText: 2026 Harishankar <harishankar0301@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, field_validator, model_serializer

from models.agent import AgentStatus
from schemas.constants import AGENT_NAME_REGEX, RecommendedFlag, Visibility, make_name_validator
from schemas.fork import ForkProvenance
from schemas.skill_resources import SkillInstallFolder
from services.versioning import validate_semver

VALID_COMPONENT_TYPES = {"mcp", "skill", "hook", "prompt", "sandbox"}

SUCCESS_METRIC_MAX_LENGTH = 200
SUCCESS_CRITERIA_TEXT_MAX_LENGTH = 2000
SUCCESS_METRICS_MAX_COUNT = 10


class ExternalMcp(BaseModel):
    name: str
    command: str = "npx"
    args: list[str] = []
    env: dict[str, str] = {}
    url: str | None = None  # source URL for reference


class SuccessMetric(BaseModel):
    name: str
    target: str
    measurement: str

    @field_validator("name", "target", "measurement")
    @classmethod
    def _not_blank_and_bounded(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Must not be blank")
        if len(v) > SUCCESS_METRIC_MAX_LENGTH:
            raise ValueError("Must be at most 200 characters")
        return v.strip()


class SuccessCriteria(BaseModel):
    intended_purpose: str
    success_metrics: list[SuccessMetric] = []
    evaluation_notes: str = ""

    @field_validator("intended_purpose")
    @classmethod
    def _purpose_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Intended purpose must not be blank")
        if len(v) > SUCCESS_CRITERIA_TEXT_MAX_LENGTH:
            raise ValueError("Intended purpose must be at most 2000 characters")
        return v.strip()

    @field_validator("success_metrics")
    @classmethod
    def _max_metrics(cls, v: list[SuccessMetric]) -> list[SuccessMetric]:
        if len(v) > SUCCESS_METRICS_MAX_COUNT:
            raise ValueError("At most 10 success metrics allowed")
        return v

    @field_validator("evaluation_notes")
    @classmethod
    def _notes_bounded(cls, v: str) -> str:
        if len(v) > SUCCESS_CRITERIA_TEXT_MAX_LENGTH:
            raise ValueError("Evaluation notes must be at most 2000 characters")
        return v.strip()


ComponentType = Literal["mcp", "skill", "hook", "prompt", "sandbox"]


_COMPONENT_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?$")


class ComponentRef(BaseModel):
    """Reference to a registry component to include in an agent.

    ``version`` pins an exact component release. Without it, the agent keeps the
    version its previous release pinned, or pins the latest approved release for
    a component it did not have before.
    """

    component_type: ComponentType
    component_id: uuid.UUID
    version: str | None = None
    config_override: dict | None = None

    @field_validator("version")
    @classmethod
    def _validate_version(cls, v: str | None) -> str | None:
        if v is not None and (len(v) > 50 or not _COMPONENT_VERSION_RE.match(v)):
            raise ValueError(f"Invalid component version '{v}'. Use an exact version such as 1.2.0")
        return v


class AgentCreateRequest(BaseModel):
    name: str
    version: str
    description: str = ""
    category: str | None = None
    owner: str
    team_id: uuid.UUID | None = None
    visibility: Visibility = "public"
    prompt: str = ""
    model_name: str
    model_config_json: dict = {}
    models_by_harness: dict[str, str] = {}
    supported_harnesses: list[str] = []
    mcp_server_ids: list[uuid.UUID] = []  # kept for backwards compat
    components: list[ComponentRef] = []  # new: all component types
    external_mcps: list[ExternalMcp] = []
    success_criteria: SuccessCriteria | None = None

    _validate_name = field_validator("name")(make_name_validator("name"))

    @field_validator("version")
    @classmethod
    def _validate_version(cls, v: str) -> str:
        if not validate_semver(v):
            raise ValueError(f"Invalid version '{v}'. Must be semver format: x.y.z (e.g. 1.0.0)")
        return v

    @field_validator("prompt", mode="after")
    @classmethod
    def _require_prompt_or_prompt_component(cls, v: str, info) -> str:
        components = (info.data or {}).get("components", [])
        has_prompt_component = any(c.component_type == "prompt" for c in components)
        if not v and not has_prompt_component:
            raise ValueError("A system prompt is required. Either set a custom prompt or add a Prompt component.")
        return v


class AgentUpdateRequest(BaseModel):
    name: str | None = None
    version: str | None = None
    version_bump_type: Literal["patch", "minor", "major"] | None = None
    description: str | None = None
    category: str | None = None
    owner: str | None = None
    team_id: uuid.UUID | None = None
    visibility: Visibility | None = None
    prompt: str | None = None
    model_name: str | None = None
    model_config_json: dict | None = None
    models_by_harness: dict[str, str] | None = None
    supported_harnesses: list[str] | None = None
    mcp_server_ids: list[uuid.UUID] | None = None  # kept for backwards compat
    components: list[ComponentRef] | None = None  # new: all component types
    external_mcps: list[ExternalMcp] | None = None
    success_criteria: SuccessCriteria | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _validate_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if len(v) > 64:
            raise ValueError("name must be at most 64 characters")
        if not AGENT_NAME_REGEX.match(v):
            raise ValueError(
                f"Invalid name '{v}'. "
                "Must start with a letter or digit and contain only lowercase letters, digits, hyphens, and underscores."
            )
        return v

    @field_validator("version", mode="before")
    @classmethod
    def _validate_version(cls, v: str | None) -> str | None:
        if v is not None and not validate_semver(v):
            raise ValueError(f"Invalid version '{v}'. Must be semver format: x.y.z (e.g. 1.0.0)")
        return v


class McpLinkResponse(BaseModel):
    mcp_listing_id: uuid.UUID
    mcp_name: str
    order: int
    model_config = {"from_attributes": True}


class ComponentLinkResponse(BaseModel):
    """A component attached to an agent."""

    component_type: str
    component_id: uuid.UUID
    component_name: str = ""
    namespace: str = ""
    slug: str = ""
    qualified_name: str = ""
    version_ref: str
    order: int
    config_override: dict | None = None
    status: str | None = None
    model_config = {"from_attributes": True}


class AgentResponse(BaseModel):
    id: uuid.UUID
    name: str
    namespace: str
    slug: str
    qualified_name: str
    version: str
    description: str
    owner: str
    team_id: uuid.UUID | None = None
    visibility: Visibility = "public"
    is_private: bool = False
    prompt: str
    model_name: str
    model_config_json: dict
    models_by_harness: dict[str, str] = {}
    external_mcps: list = []
    supported_harnesses: list[str]
    required_capabilities: list[str] = []
    inferred_supported_harnesses: list[str] = []
    success_criteria: dict | None = None
    status: AgentStatus
    rejection_reason: str | None = None
    created_by: uuid.UUID
    created_by_email: str = ""
    created_by_username: str | None = None
    created_at: datetime
    deleted_at: datetime | None = None
    updated_at: datetime
    mcp_links: list[McpLinkResponse] = []
    component_links: list[ComponentLinkResponse] = []
    user_permission: str | None = None
    latest_approved_version: str | None = None
    latest_version: str | None = None
    is_recommended: RecommendedFlag = False
    forked_from: ForkProvenance | None = None
    fork_count: int = 0
    warnings: list[str] = []

    model_config = {"from_attributes": True}


class AgentSummary(BaseModel):
    id: uuid.UUID
    name: str
    namespace: str
    slug: str
    qualified_name: str
    version: str
    description: str
    owner: str
    team_id: uuid.UUID | None = None
    visibility: Visibility = "public"
    is_private: bool = False
    model_name: str
    supported_harnesses: list[str]
    required_capabilities: list[str] = []
    inferred_supported_harnesses: list[str] = []
    status: AgentStatus
    rejection_reason: str | None = None
    download_count: int = 0
    average_rating: float | None = None
    component_count: int = 0
    created_by: uuid.UUID | None = None
    created_by_email: str = ""
    created_by_username: str | None = None
    created_at: datetime | None = None
    deleted_at: datetime | None = None
    updated_at: datetime | None = None
    components_ready: bool = True
    blocking_components: list = []
    is_recommended: RecommendedFlag = False
    forked_from: ForkProvenance | None = None
    fork_count: int = 0

    model_config = {"from_attributes": True}


class AgentValidateRequest(BaseModel):
    components: list[ComponentRef] = []
    team_id: uuid.UUID | None = None
    visibility: Visibility = "public"


class ValidationIssue(BaseModel):
    severity: Literal["error", "warning"]
    component_type: str | None = None
    component_id: uuid.UUID | None = None
    message: str


class ValidationResult(BaseModel):
    valid: bool
    issues: list[ValidationIssue] = []


class AgentInstallRequest(BaseModel):
    harness: str
    env_values: dict[str, dict[str, str]] = {}  # {mcp_listing_id: {VAR: value}}
    header_values: dict[str, dict[str, str]] = {}  # {mcp_listing_id: {Header-Name: value}}
    # harness-specific install options (e.g. scope, model, tools, color for Claude Code)
    options: dict = {}
    platform: str = ""  # e.g. "win32", "darwin", "linux" - empty = Unix default
    version: str | None = None  # Specific version to install (None = latest)
    # Refuse the install when any component is unlocked, changed after it was
    # locked, or pinned to a version that is not approved.
    strict: bool = False
    supported_features: list[str] = []


class AgentInstallResponse(BaseModel):
    agent_id: uuid.UUID
    harness: str
    # The agent version that was actually installed.
    version: str | None = None
    config_snippet: dict
    warnings: list[str] = []
    # The component versions that were installed and how they matched the lock.
    lock: dict | None = None
    skill_bundles: list[SkillInstallFolder] = []

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        result = handler(self)
        if not self.skill_bundles:
            result.pop("skill_bundles", None)  # Old agent install clients keep the legacy response.
        return result


class AgentVersionCreateRequest(BaseModel):
    version: str
    description: str = ""
    prompt: str = ""
    model_name: str
    model_config_json: dict = {}
    models_by_harness: dict[str, str] = {}
    external_mcps: list[ExternalMcp] = []
    supported_harnesses: list[str] = []
    components: list[ComponentRef] = []
    yaml_snapshot: str | None = None
    is_prerelease: bool = False
    save_as_draft: bool = False
    success_criteria: SuccessCriteria | None = None
    # Authors upgrade components deliberately: without this, a release keeps every
    # component pin from the agent's current release unless a component names a
    # version. With it, unversioned components move to their latest approved release.
    refresh_components: bool = False

    @field_validator("version")
    @classmethod
    def _validate_version(cls, v: str) -> str:
        if not validate_semver(v):
            raise ValueError(f"Invalid version '{v}'. Must be semver format: x.y.z (e.g. 1.0.0)")
        return v


class AgentRestoreRequest(BaseModel):
    name: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _validate_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if len(v) > 64:
            raise ValueError("name must be at most 64 characters")
        if not AGENT_NAME_REGEX.match(v):
            raise ValueError(
                f"Invalid name '{v}'. "
                "Must start with a letter or digit and contain only lowercase letters, digits, hyphens, and underscores."
            )
        return v


class AgentVersionReviewRequest(BaseModel):
    action: Literal["approve", "reject"]
    reason: str | None = None
