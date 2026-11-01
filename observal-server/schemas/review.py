# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Input contracts for the additive PR-style review API."""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Text(BaseModel):
    """PostgreSQL text cannot hold NUL; refuse it at the edge instead of failing in the database."""

    @model_validator(mode="after")
    def _no_nul(self):
        for name, value in self:
            if isinstance(value, str) and "\x00" in value:
                raise ValueError(f"{name} must not contain NUL characters")
        return self


class ReviewEdit(_Text):
    title: str | None = Field(None, min_length=1, max_length=255)
    body: str | None = Field(None, max_length=100_000)


class CommentCreate(_Text):
    body: str = Field(min_length=1, max_length=100_000)
    suggestion: str | None = Field(None, max_length=100_000)
    as_draft: bool = False


class ThreadCreate(CommentCreate):
    path: str | None = None
    side: Literal["head", "base"] | None = None
    start_line: int | None = Field(None, ge=1)
    end_line: int | None = Field(None, ge=1)

    @model_validator(mode="after")
    def check_anchor(self):
        if self.path is None:
            if any(v is not None for v in (self.side, self.start_line, self.end_line)):
                raise ValueError("A file is required for line comments")
        elif self.side is None or self.start_line is None:
            raise ValueError("File comments need a side and start line")
        if self.end_line is not None and self.start_line is not None and self.end_line < self.start_line:
            raise ValueError("End line must not precede start line")
        return self


class CommentEdit(_Text):
    body: str = Field(min_length=1, max_length=100_000)


class VerdictCreate(_Text):
    verdict: Literal["comment", "approve", "request_changes"]
    body: str = Field("", max_length=100_000)


class Reason(_Text):
    reason: str = Field(min_length=1, max_length=100_000)


class ReopenRequest(_Text):
    message: str | None = Field(default=None, max_length=5000)


class PublishRequest(_Text):
    category: str | None = None
    override_reason: str | None = None


class ReviewerRequest(BaseModel):
    user_id: uuid.UUID


class SubscriptionUpdate(BaseModel):
    mode: Literal["watching", "muted"]


class PublicPolicyUpdate(BaseModel):
    """Rules for items a teamspace publishes to the public registry; never looser than the organization's."""

    model_config = ConfigDict(extra="forbid", strict=True)

    required_approvals: dict[str, int] = Field(default_factory=dict)
    self_approval: Literal["not_counted", "counted"] = "not_counted"
    dismiss_stale_approvals: bool = True
    require_resolved_threads: bool = False
    auto_publish: bool = False


class PolicyUpdate(PublicPolicyUpdate):
    public: PublicPolicyUpdate | None = None
