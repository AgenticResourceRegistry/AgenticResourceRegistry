# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Versioned usage-ping payloads and administrator-facing status schemas."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

UsagePingFrequency = Literal["every_6_hours", "daily", "weekly"]


class UsagePingIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_name: str = Field(min_length=1, max_length=160)
    hostname: str = Field(min_length=1, max_length=253)
    # Domains users reach this server on, most used first. IP literals are reported only
    # as "localhost", "private-ip", or "public-ip".
    access_hosts: list[str] = Field(default_factory=list, max_length=5)
    # User counts per email domain.
    email_domains: dict[str, int] = Field(default_factory=dict, max_length=10)
    # Super administrators' email addresses. Empty when usage_ping.include_admin_emails is off.
    admin_emails: list[str] = Field(default_factory=list, max_length=5)
    # Hostnames of the configured OIDC or SAML identity provider.
    sso_hosts: list[str] = Field(default_factory=list, max_length=5)
    # Sign-in allowlists configured by the administrator.
    sso_allowed_domains: list[str] = Field(default_factory=list, max_length=10)
    github_orgs: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("company_name", "hostname")
    @classmethod
    def strip_identity(cls, value: str) -> str:
        return value.strip()


class UsagePingInstance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1, max_length=64)
    deployment_type: Literal["self-managed", "cloud", "development"]


class UsagePingCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    users: int = Field(ge=0)
    teams: int = Field(ge=0)
    agents: int = Field(ge=0)
    mcp_servers: int = Field(ge=0)
    skills: int = Field(ge=0)
    hooks: int = Field(ge=0)
    prompts: int = Field(ge=0)
    sandboxes: int = Field(ge=0)
    agent_installs: int = Field(ge=0)
    sessions_total: int = Field(ge=0)
    sessions_7d: int = Field(ge=0)
    sessions_30d: int = Field(ge=0)


class UsagePingActivity(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    active_users_7d: int = Field(ge=0)
    active_users_30d: int = Field(ge=0)
    active_agents_7d: int = Field(ge=0)
    active_agents_30d: int = Field(ge=0)
    events_7d: int = Field(ge=0)
    events_30d: int = Field(ge=0)
    prompts_7d: int = Field(ge=0)
    prompts_30d: int = Field(ge=0)
    tool_calls_7d: int = Field(ge=0)
    tool_calls_30d: int = Field(ge=0)
    tool_results_7d: int = Field(ge=0)
    tool_results_30d: int = Field(ge=0)
    input_tokens_7d: int = Field(ge=0)
    input_tokens_30d: int = Field(ge=0)
    output_tokens_7d: int = Field(ge=0)
    output_tokens_30d: int = Field(ge=0)
    cache_read_tokens_7d: int = Field(ge=0)
    cache_read_tokens_30d: int = Field(ge=0)
    cache_write_tokens_7d: int = Field(ge=0)
    cache_write_tokens_30d: int = Field(ge=0)
    credits_7d: float = Field(ge=0)
    credits_30d: float = Field(ge=0)
    average_session_duration_seconds_30d: float = Field(ge=0)
    average_prompts_per_session_30d: float = Field(ge=0)
    average_tool_calls_per_session_30d: float = Field(ge=0)
    sessions_with_tools_30d: int = Field(ge=0)
    sessions_with_tokens_30d: int = Field(ge=0)
    registered_agent_sessions_30d: int = Field(ge=0)
    unregistered_agent_sessions_30d: int = Field(ge=0)
    top_level_sessions_30d: int = Field(ge=0)
    subagent_sessions_30d: int = Field(ge=0)
    distinct_agent_versions_30d: int = Field(ge=0)
    distinct_models_30d: int = Field(ge=0)
    parse_errors_30d: int = Field(ge=0)
    truncated_events_30d: int = Field(ge=0)


TeamSize = Literal["", "1-10", "11-50", "51-200", "201-1000", "1000+"]


class UsagePingContact(BaseModel):
    """A contact person a super administrator chose to share with the collector."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    role: str = Field(default="", max_length=80)
    team_size: TeamSize = ""
    consented_at: datetime

    @field_validator("name", "role", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        """Trim free-text fields before the length checks, so a blank name is rejected."""
        return value.strip() if isinstance(value, str) else value


class UsagePingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[3] = 3
    ping_id: UUID
    installation_id: UUID
    sent_at: datetime
    identity: UsagePingIdentity
    instance: UsagePingInstance
    counts: UsagePingCounts
    activity: UsagePingActivity
    features: dict[str, bool] = Field(default_factory=dict, max_length=32)
    harnesses: dict[str, int] = Field(default_factory=dict, max_length=32)
    # Present only after a super administrator opts in to being contacted.
    contact: UsagePingContact | None = None


class UsagePingContactUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    role: str = Field(default="", max_length=80)
    team_size: TeamSize = ""
    consent: Literal[True]

    @field_validator("name", "role", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        """Trim free-text fields before the length checks, so a blank name is rejected."""
        return value.strip() if isinstance(value, str) else value


class UsagePingContactDefer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    never: bool = False


class UsagePingContactState(BaseModel):
    # "dialog" on a fresh installation, "banner" once a deferred installation has grown.
    prompt: Literal["dialog", "banner"] | None
    contact: UsagePingContact | None


class UsagePingStatus(BaseModel):
    enabled: bool
    configured: bool
    frequency: UsagePingFrequency
    collector_url: str
    installation_id: UUID | None
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    next_scheduled_at: datetime
    # Super administrator addresses that reports carry while include_admin_emails is on.
    admin_emails: list[str] = Field(default_factory=list)


class UsagePingAdminResponse(BaseModel):
    status: UsagePingStatus
    payload: UsagePingPayload | None = None
