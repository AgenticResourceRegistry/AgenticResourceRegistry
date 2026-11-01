# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Request and visibility-safe provenance shapes for registry forks."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ForkRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    version: str | None = Field(default=None, min_length=1, max_length=50)
    new_version: str | None = Field(default=None, min_length=1, max_length=50)
    team_id: uuid.UUID | None = None
    visibility: Literal["public", "team"] | None = None


class ForkProvenance(BaseModel):
    available: bool
    id: uuid.UUID | None = None
    type: str | None = None
    namespace: str | None = None
    slug: str | None = None
    qualified_name: str | None = None
    version: str | None = None
    forked_at: datetime | None = None
