# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator

from models.mcp import ListingStatus
from schemas.constants import (
    VALID_SKILL_TASK_TYPES,
    RecommendedFlag,
    Visibility,
    make_harness_list_validator,
    make_option_validator,
)
from schemas.fork import ForkProvenance
from schemas.skill_commands import normalize_slash_command
from schemas.skill_resources import SkillFolderSnapshot, SkillInstallFolder, SkillResource, SkillRevision


class SkillSubmitRequest(BaseModel):
    extra_files: list[SkillResource] = []
    name: str
    version: str
    description: str
    owner: str
    team_id: uuid.UUID | None = None
    visibility: Visibility = "public"
    skill_path: str = "/"
    git_url: str | None = None
    git_ref: str | None = None
    skill_md_content: str | None = None
    delivery_mode: str = "git_fetch"
    script_content: str | None = None
    script_filename: str | None = None
    target_agents: list[str] = []
    task_type: str
    slash_command: str | None = None
    supported_harnesses: list[str] = []

    _validate_task_type = field_validator("task_type")(make_option_validator("task_type", VALID_SKILL_TASK_TYPES))
    _validate_ides = field_validator("supported_harnesses")(make_harness_list_validator())

    @field_validator("slash_command")
    @classmethod
    def _validate_slash_command(cls, v: str | None) -> str | None:
        return normalize_slash_command(v)


class SkillDraftRequest(BaseModel):
    extra_files: list[SkillResource] = []
    name: str
    version: str = "0.1.0"
    description: str = ""
    owner: str = ""
    team_id: uuid.UUID | None = None
    visibility: Visibility = "public"
    skill_path: str = "/"
    git_url: str | None = None
    git_ref: str | None = None
    skill_md_content: str | None = None
    delivery_mode: str = "git_fetch"
    script_content: str | None = None
    script_filename: str | None = None
    target_agents: list[str] = []
    task_type: str = "general"
    slash_command: str | None = None
    supported_harnesses: list[str] = []

    _validate_ides = field_validator("supported_harnesses")(make_harness_list_validator())

    @field_validator("slash_command")
    @classmethod
    def _validate_slash_command(cls, v: str | None) -> str | None:
        return normalize_slash_command(v)


class SkillUpdateRequest(BaseModel):
    observed_revision: SkillRevision | None = None
    extra_files: list[SkillResource] | None = None
    name: str | None = None
    version: str | None = None
    description: str | None = None
    owner: str | None = None
    team_id: uuid.UUID | None = None
    visibility: Visibility | None = None
    skill_path: str | None = None
    git_url: str | None = None
    git_ref: str | None = None
    skill_md_content: str | None = None
    delivery_mode: str | None = None
    script_content: str | None = None
    script_filename: str | None = None
    target_agents: list[str] | None = None
    task_type: str | None = None
    slash_command: str | None = None
    supported_harnesses: list[str] | None = None

    @model_validator(mode="after")
    def _no_null_resources(self):
        if "extra_files" in self.model_fields_set and self.extra_files is None:
            raise ValueError("extra_files cannot be null; use [] to clear")
        return self

    @field_validator("slash_command")
    @classmethod
    def _validate_slash_command(cls, v: str | None) -> str | None:
        return normalize_slash_command(v)


class SkillFolderDraftRequest(SkillFolderSnapshot):
    """Create an initial direct draft with a complete, valid SKILL.md tree."""

    name: str = Field(min_length=1, max_length=255)
    version: str = Field(pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$", max_length=50)
    description: str = Field(min_length=1)
    owner: str
    task_type: str = "general"
    team_id: uuid.UUID | None = None
    visibility: Visibility = "public"
    supported_harnesses: list[str] = []

    _validate_task_type = field_validator("task_type")(make_option_validator("task_type", VALID_SKILL_TASK_TYPES))
    _validate_ides = field_validator("supported_harnesses")(make_harness_list_validator())

    @field_validator("team_id", mode="before")
    @classmethod
    def _parse_team_id(cls, value: uuid.UUID | str | None) -> uuid.UUID | str | None:
        return uuid.UUID(value) if isinstance(value, str) else value


class SkillCandidateDraftRequest(BaseModel):
    """Fork exactly one approved direct release into a saved candidate draft."""

    model_config = ConfigDict(extra="forbid", strict=True)

    base_version_id: uuid.UUID
    observed_base_revision: SkillRevision
    version: str = Field(pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$", max_length=50)
    description: str = Field(min_length=1, max_length=10_000)
    changelog: str | None = Field(default=None, max_length=10_000)

    @field_validator("base_version_id", mode="before")
    @classmethod
    def _parse_base_version_id(cls, value: uuid.UUID | str) -> uuid.UUID | str:
        return uuid.UUID(value) if isinstance(value, str) else value


class SkillFolderImportDraftRequest(SkillCandidateDraftRequest, SkillFolderSnapshot):
    """Atomic full-folder successor of an approved Git or historical direct release."""


class SkillListingResponse(BaseModel):
    forked_from: ForkProvenance | None = None
    fork_count: int = 0
    warnings: list[str] = []
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
    task_type: str
    target_agents: list[str]
    supported_harnesses: list[str]
    skill_path: str
    git_url: str | None = None
    git_ref: str | None = None
    skill_md_content: str | None = None
    delivery_mode: str = "git_fetch"
    script_content: str | None = None
    script_filename: str | None = None
    validated: bool = False
    slash_command: str | None = None
    status: ListingStatus
    rejection_reason: str | None = None
    submitted_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    download_count: int = 0
    user_permission: str | None = None
    is_recommended: RecommendedFlag = False

    @field_validator("user_permission", mode="before")
    @classmethod
    def _coerce_user_permission(cls, v):
        return v if isinstance(v, str) else None

    model_config = {"from_attributes": True}


class SkillListingSummary(BaseModel):
    forked_from: ForkProvenance | None = None
    fork_count: int = 0
    id: uuid.UUID
    name: str
    namespace: str
    slug: str
    qualified_name: str
    version: str
    description: str
    task_type: str
    owner: str
    team_id: uuid.UUID | None = None
    visibility: Visibility = "public"
    is_private: bool = False
    target_agents: list[str]
    status: ListingStatus
    rejection_reason: str | None = None
    updated_at: datetime | None = None
    is_recommended: RecommendedFlag = False

    model_config = {"from_attributes": True}


class SkillInstallRequest(BaseModel):
    harness: str
    scope: str = "project"
    local_name: str | None = None
    version: str | None = None  # Specific version to install (None = latest)
    supported_features: list[str] = []
    preview: bool = False  # Do not record a download for read-only CLI previews.


class SkillInstallResponse(BaseModel):
    listing_id: uuid.UUID
    harness: str
    config_snippet: dict
    warnings: list[str] = []
    # The exact component version that was installed and its content digest.
    version: str | None = None
    version_id: uuid.UUID | None = None
    digest: str | None = None
    bundle: SkillInstallFolder | None = None

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        result = handler(self)
        if self.bundle is None:
            result.pop("bundle", None)  # Legacy resource-less response stays unchanged.
        return result
