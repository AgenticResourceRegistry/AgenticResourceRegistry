# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator

from models.mcp import ListingStatus
from schemas.constants import (
    VALID_HOOK_EVENTS,
    VALID_HOOK_EXECUTION_MODES,
    VALID_HOOK_HANDLER_TYPES,
    VALID_HOOK_SCOPES,
    RecommendedFlag,
    Visibility,
    make_harness_list_validator,
    make_option_validator,
)
from schemas.fork import ForkProvenance


class HookSubmitRequest(BaseModel):
    name: str
    version: str
    description: str
    owner: str
    team_id: uuid.UUID | None = None
    visibility: Visibility = "public"
    event: str
    execution_mode: str = "async"
    priority: int = 100
    handler_type: str
    handler_config: dict = {}
    scope: str = "agent"
    tool_filter: list[str] | None = None
    supported_harnesses: list[str] = []
    # Script delivery
    script_content: str | None = None
    script_filename: str | None = None
    # Source tracking
    source_url: str | None = None
    source_ref: str | None = None
    source_path: str | None = None
    # Requirements
    requirements: list[str] | None = None

    _validate_event = field_validator("event")(make_option_validator("event", VALID_HOOK_EVENTS))
    _validate_handler_type = field_validator("handler_type")(
        make_option_validator("handler_type", VALID_HOOK_HANDLER_TYPES)
    )
    _validate_execution_mode = field_validator("execution_mode")(
        make_option_validator("execution_mode", VALID_HOOK_EXECUTION_MODES)
    )
    _validate_scope = field_validator("scope")(make_option_validator("scope", VALID_HOOK_SCOPES))
    _validate_ides = field_validator("supported_harnesses")(make_harness_list_validator())


class HookDraftRequest(BaseModel):
    name: str
    version: str = "0.1.0"
    description: str = ""
    owner: str = ""
    team_id: uuid.UUID | None = None
    visibility: Visibility = "public"
    event: str = "PreToolUse"
    execution_mode: str = "async"
    priority: int = 100
    handler_type: str = "command"
    handler_config: dict = {}
    scope: str = "agent"
    tool_filter: list[str] | None = None
    supported_harnesses: list[str] = []
    script_content: str | None = None
    script_filename: str | None = None
    source_url: str | None = None
    source_ref: str | None = None
    source_path: str | None = None
    requirements: list[str] | None = None

    _validate_ides = field_validator("supported_harnesses")(make_harness_list_validator())


class HookUpdateRequest(BaseModel):
    name: str | None = None
    version: str | None = None
    description: str | None = None
    owner: str | None = None
    team_id: uuid.UUID | None = None
    visibility: Visibility | None = None
    event: str | None = None
    execution_mode: str | None = None
    priority: int | None = None
    handler_type: str | None = None
    handler_config: dict | None = None
    scope: str | None = None
    tool_filter: list[str] | None = None
    supported_harnesses: list[str] | None = None
    script_content: str | None = None
    script_filename: str | None = None
    source_url: str | None = None
    source_ref: str | None = None
    source_path: str | None = None
    requirements: list[str] | None = None


class HookListingResponse(BaseModel):
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
    event: str
    execution_mode: str
    priority: int
    handler_type: str
    handler_config: dict
    scope: str
    supported_harnesses: list[str]
    script_content: str | None = None
    script_filename: str | None = None
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


class HookListingSummary(BaseModel):
    forked_from: ForkProvenance | None = None
    fork_count: int = 0
    id: uuid.UUID
    name: str
    namespace: str
    slug: str
    qualified_name: str
    version: str
    description: str
    event: str
    scope: str
    owner: str
    team_id: uuid.UUID | None = None
    visibility: Visibility = "public"
    is_private: bool = False
    status: ListingStatus
    rejection_reason: str | None = None
    updated_at: datetime | None = None
    is_recommended: RecommendedFlag = False

    model_config = {"from_attributes": True}


class HookInstallRequest(BaseModel):
    harness: str
    local_name: str | None = None
    platform: str = ""  # e.g. "win32", "darwin", "linux" - empty = Unix default
    version: str | None = None  # Specific version to install (None = latest)


class HookFileEntry(BaseModel):
    path: str
    content: str
    executable: bool = False


class HookInstallResponse(BaseModel):
    listing_id: uuid.UUID
    harness: str
    config_snippet: dict
    config_path: str = ""
    files: list[HookFileEntry] = []
    requirements: list[str] = []
    source_fetch: dict | None = None
    notes: list[str] = []
    warnings: list[str] = []
    # The exact component version that was installed and its content digest.
    version: str | None = None
    version_id: uuid.UUID | None = None
    digest: str | None = None
