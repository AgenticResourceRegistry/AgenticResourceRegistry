# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from models.team import TeamRole


class TeamCreateRequest(BaseModel):
    name: str = Field(max_length=255)
    handle: str | None = None
    description: str | None = None
    visibility: Literal["public", "private"] = "public"


class TeamUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    description: str | None = None


class TeamVisibilityUpdateRequest(BaseModel):
    visibility: Literal["public", "private"]


class TeamVisibilityDecisionRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class TeamResponse(BaseModel):
    id: uuid.UUID
    name: str
    handle: str
    description: str | None = None
    visibility: str = "public"
    is_personal: bool = False
    visibility_request_status: Literal["pending", "approved", "rejected"] | None = None
    visibility_rejection_reason: str | None = None
    role: str | None = None
    member_count: int | None = None
    created_at: datetime | None = None
    model_config = ConfigDict(from_attributes=True)


class TeamVisibilityRequestResponse(BaseModel):
    team_id: uuid.UUID
    name: str
    handle: str
    description: str | None = None
    requested_by: uuid.UUID | None
    requested_by_username: str | None = None
    requested_at: datetime


class TeamMemberResponse(BaseModel):
    id: uuid.UUID
    email: str
    username: str | None = None
    name: str | None = None
    role: str
    model_config = ConfigDict(from_attributes=True)


class TeamMemberUpsertRequest(BaseModel):
    email: str | None = None
    username: str | None = None
    user_id: uuid.UUID | None = None
    role: TeamRole = TeamRole.member

    @model_validator(mode="after")
    def _require_identifier(self) -> "TeamMemberUpsertRequest":
        if not (self.email or self.username or self.user_id):
            raise ValueError("One of email, username, or user_id is required")
        return self


class TeamJoinRequestCreate(BaseModel):
    message: str | None = Field(default=None, max_length=500)
    invite_token: str | None = Field(default=None, max_length=128)


class TeamJoinDecisionRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class TeamInviteCreateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    expires_in_days: int = Field(default=7, ge=1, le=365)
    max_uses: int | None = Field(default=None, ge=1, le=10000)


class TeamInvitePreviewRequest(BaseModel):
    token: str = Field(max_length=128)


class TeamInviteResponse(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    name: str
    url: str | None = None
    invited_by_username: str | None = None
    max_uses: int | None = None
    use_count: int
    expires_at: datetime
    revoked_at: datetime | None = None
    created_at: datetime | None = None
    state: str
    model_config = ConfigDict(from_attributes=True)


class TeamInviteCreatedResponse(TeamInviteResponse):
    token: str
    url: str


class TeamInviteCallerRequestResponse(BaseModel):
    id: uuid.UUID
    status: str
    decision_reason: str | None = None
    created_at: datetime | None = None
    decided_at: datetime | None = None


class TeamInvitePreviewResponse(BaseModel):
    valid: bool
    invite_state: str | None = None
    is_member: bool = False
    team_id: uuid.UUID | None = None
    team_name: str | None = None
    team_handle: str | None = None
    team_description: str | None = None
    invited_by: str | None = None
    request: TeamInviteCallerRequestResponse | None = None


class TeamJoinRequestResponse(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    user_id: uuid.UUID
    invite_id: uuid.UUID | None = None
    email: str | None = None
    username: str | None = None
    name: str | None = None
    status: str
    message: str | None = None
    decided_by: uuid.UUID | None = None
    decided_by_username: str | None = None
    decided_at: datetime | None = None
    decision_reason: str | None = None
    created_at: datetime | None = None
    model_config = ConfigDict(from_attributes=True)
