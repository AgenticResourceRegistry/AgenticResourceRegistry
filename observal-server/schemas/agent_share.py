# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""API schemas for Agent share manifests."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from services.versioning import validate_semver


class AgentShareItemCreate(BaseModel):
    agent_id: uuid.UUID
    version: str = Field(min_length=1, max_length=50)

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        value = value.strip()
        if not validate_semver(value):
            raise ValueError("version must use semantic version format")
        return value


class AgentShareCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    expires_in_days: int = Field(default=7, ge=1, le=30)
    items: list[AgentShareItemCreate] = Field(min_length=1, max_length=50)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("title must not contain control characters")
        return value or None

    @field_validator("items")
    @classmethod
    def reject_duplicate_items(cls, value: list[AgentShareItemCreate]) -> list[AgentShareItemCreate]:
        keys = {(item.agent_id, item.version) for item in value}
        if len(keys) != len(value):
            raise ValueError("share items must be unique")
        return value


class AgentShareCreateResponse(BaseModel):
    token: str
    url: str
    created_at: datetime
    expires_at: datetime
    item_count: int


class SharedAgentSummary(BaseModel):
    agent_id: uuid.UUID
    version: str
    name: str
    namespace: str
    slug: str
    qualified_name: str
    description: str
    supported_harnesses: list[str]
    required_capabilities: list[str]
    position: int


class AgentShareResponse(BaseModel):
    token: str
    title: str | None
    created_at: datetime
    expires_at: datetime
    created_by_username: str
    items: list[SharedAgentSummary]
    unavailable_count: int


class AgentShareRevokeResponse(BaseModel):
    revoked: bool
