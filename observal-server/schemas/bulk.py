# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import uuid  # noqa: TC003 - needed at runtime by Pydantic

from pydantic import BaseModel, Field, field_validator


class BulkAgentItem(BaseModel):
    """Single agent in a bulk create request. Extends AgentCreateRequest fields with relaxed defaults."""

    name: str = Field(min_length=1, max_length=255)
    version: str = "1.0.0"
    description: str = ""
    owner: str = ""
    prompt: str = ""
    model_name: str = "claude-sonnet-4"
    model_config_json: dict = Field(default_factory=dict)
    supported_harnesses: list[str] = Field(default_factory=list)
    components: list[dict] = Field(default_factory=list)  # [{component_type, component_id}]
    external_mcps: list[dict] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Agent name must not be empty")
        return value


class BulkAgentRequest(BaseModel):
    agents: list[BulkAgentItem] = Field(min_length=1, max_length=50)
    dry_run: bool = False


class BulkResultItem(BaseModel):
    name: str
    status: str  # "created", "skipped", "error"
    agent_id: uuid.UUID | None = None
    review_number: int | None = None
    error: str | None = None


class BulkResult(BaseModel):
    total: int
    created: int
    skipped: int
    errors: int
    partial: bool
    dry_run: bool
    results: list[BulkResultItem]
