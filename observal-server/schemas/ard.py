# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Request schemas for the ARD endpoints (spec v0.91 §5.3)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

FederationMode = Literal["auto", "referrals", "none"]


class ArdError(BaseModel):
    """the ARD routes' 400 response."""

    error_code: str = Field(alias="errorCode", examples=["INVALID_ARGUMENT"])
    message: str


class ArdQuery(BaseModel):
    """The common query object shared by Search and Explore (§5.3.1)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    context: dict[str, Any] | str | list[Any] | None = Field(default=None, alias="@context")
    text: str | None = None
    filter: dict[str, Any] | None = None


class ArdSearchRequest(BaseModel):
    """POST /search body (§5.3.2). ``federation`` omitted means ``auto`` (ADR 0001, Decision 5)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    query: ArdQuery
    federation: FederationMode | None = None
    page_size: int | None = Field(default=None, alias="pageSize", ge=1, le=100)
    page_token: str | None = Field(default=None, alias="pageToken", max_length=512)


class ArdExploreRequest(BaseModel):
    """POST /explore body (§5.3.3). Accepted so the endpoint can answer 501 cleanly."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    query: ArdQuery | None = None
    result_type: dict[str, Any] | None = Field(default=None, alias="resultType")
