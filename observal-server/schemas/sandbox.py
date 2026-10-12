# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator, model_validator

from models.mcp import ListingStatus
from schemas.constants import (
    VALID_SANDBOX_NETWORK_POLICIES,
    VALID_SANDBOX_RUNTIME_TYPES,
    RecommendedFlag,
    Visibility,
    make_harness_list_validator,
    make_option_validator,
)
from schemas.fork import ForkProvenance

_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,499}$")


def _validate_runtime_config(runtime_type: str | None, image: str | None, runtime_config: dict | None) -> None:
    if runtime_type == "docker" and not image:
        raise ValueError("image is required for docker sandboxes")
    if runtime_type == "docker" and image and ("://" in image or not _REF_RE.match(image)):
        raise ValueError("docker image must be an OCI/Docker image reference")
    if runtime_type == "lxc" and not image:
        raise ValueError("image is required for lxc sandboxes")
    if runtime_type == "firecracker":
        cfg = runtime_config or {}
        if not (cfg.get("config_path") or (cfg.get("kernel_image_path") and cfg.get("rootfs_path"))):
            raise ValueError(
                "firecracker sandboxes require runtime_config.config_path or kernel_image_path/rootfs_path"
            )
    if runtime_type == "wasm" and not (image or (runtime_config or {}).get("module")):
        raise ValueError("wasm sandboxes require image or runtime_config.module pointing to a WASI module")


class SandboxSubmitRequest(BaseModel):
    name: str
    version: str
    description: str
    owner: str
    team_id: uuid.UUID | None = None
    visibility: Visibility = "public"
    runtime_type: str
    image: str
    resource_limits: dict = {}
    network_policy: str = "none"
    entrypoint: str | None = None
    runtime_config: dict = {}
    supported_harnesses: list[str] = []
    # Source tracking
    source_url: str | None = None
    source_ref: str | None = None
    sandbox_path: str | None = None

    _validate_runtime_type = field_validator("runtime_type")(
        make_option_validator("runtime_type", VALID_SANDBOX_RUNTIME_TYPES)
    )
    _validate_network_policy = field_validator("network_policy")(
        make_option_validator("network_policy", VALID_SANDBOX_NETWORK_POLICIES)
    )
    _validate_ides = field_validator("supported_harnesses")(make_harness_list_validator())

    @model_validator(mode="after")
    def _validate_runtime(self):
        _validate_runtime_config(self.runtime_type, self.image, self.runtime_config)
        return self


class SandboxDraftRequest(BaseModel):
    name: str
    version: str = "0.1.0"
    description: str = ""
    owner: str = ""
    team_id: uuid.UUID | None = None
    visibility: Visibility = "public"
    runtime_type: str = "docker"
    image: str = ""
    resource_limits: dict = {}
    network_policy: str = "none"
    entrypoint: str | None = None
    runtime_config: dict = {}
    supported_harnesses: list[str] = []
    source_url: str | None = None
    source_ref: str | None = None
    sandbox_path: str | None = None

    _validate_runtime_type = field_validator("runtime_type")(
        make_option_validator("runtime_type", VALID_SANDBOX_RUNTIME_TYPES)
    )
    _validate_network_policy = field_validator("network_policy")(
        make_option_validator("network_policy", VALID_SANDBOX_NETWORK_POLICIES)
    )
    _validate_ides = field_validator("supported_harnesses")(make_harness_list_validator())


class SandboxUpdateRequest(BaseModel):
    name: str | None = None
    version: str | None = None
    description: str | None = None
    owner: str | None = None
    team_id: uuid.UUID | None = None
    visibility: Visibility | None = None
    runtime_type: str | None = None
    image: str | None = None
    resource_limits: dict | None = None
    network_policy: str | None = None
    entrypoint: str | None = None
    runtime_config: dict | None = None
    supported_harnesses: list[str] | None = None
    source_url: str | None = None
    source_ref: str | None = None
    sandbox_path: str | None = None

    _validate_runtime_type = field_validator("runtime_type")(
        make_option_validator("runtime_type", VALID_SANDBOX_RUNTIME_TYPES)
    )
    _validate_network_policy = field_validator("network_policy")(
        make_option_validator("network_policy", VALID_SANDBOX_NETWORK_POLICIES)
    )


class SandboxListingResponse(BaseModel):
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
    runtime_type: str
    image: str
    resource_limits: dict
    network_policy: str
    entrypoint: str | None = None
    runtime_config: dict = {}
    source_url: str | None = None
    source_ref: str | None = None
    resolved_sha: str | None = None
    sandbox_path: str | None = None
    supported_harnesses: list[str]
    status: ListingStatus
    rejection_reason: str | None = None
    submitted_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    user_permission: str | None = None
    is_recommended: RecommendedFlag = False

    @field_validator("runtime_config", "resource_limits", mode="before")
    @classmethod
    def _coerce_dicts(cls, v):
        return v if isinstance(v, dict) else {}

    @field_validator(
        "entrypoint", "resolved_sha", "sandbox_path", "source_ref", "source_url", "user_permission", mode="before"
    )
    @classmethod
    def _coerce_optional_strings(cls, v):
        return v if isinstance(v, str) else None

    model_config = {"from_attributes": True}


class SandboxListingSummary(BaseModel):
    forked_from: ForkProvenance | None = None
    fork_count: int = 0
    id: uuid.UUID
    name: str
    namespace: str
    slug: str
    qualified_name: str
    version: str
    description: str
    runtime_type: str
    image: str = ""
    resource_limits: dict = {}
    network_policy: str = "none"
    entrypoint: str | None = None
    runtime_config: dict = {}
    source_url: str | None = None
    source_ref: str | None = None
    sandbox_path: str | None = None
    owner: str
    team_id: uuid.UUID | None = None
    visibility: Visibility = "public"
    is_private: bool = False
    supported_harnesses: list[str]
    status: ListingStatus
    rejection_reason: str | None = None
    updated_at: datetime | None = None
    is_recommended: RecommendedFlag = False

    model_config = {"from_attributes": True}
