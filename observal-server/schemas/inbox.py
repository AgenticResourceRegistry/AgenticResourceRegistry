# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Request and response shapes for the inbox API."""

# No `from __future__ import annotations` here, matching the other schema
# modules: Pydantic resolves these annotations at runtime to build validators.
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class InboxItemResponse(BaseModel):
    id: uuid.UUID
    kind: str
    state: str
    # Independent of state: an item can be read and still open.
    read: bool
    read_at: datetime | None = None
    action_required: bool
    title: str
    body: str | None = None
    subject_type: str
    subject_id: uuid.UUID | None = None
    subject_namespace: str | None = None
    subject_slug: str | None = None
    action_url: str | None = None
    action_command: str | None = None
    actor_id: uuid.UUID | None = None
    team_id: uuid.UUID | None = None
    payload: dict = Field(default_factory=dict)
    created_at: datetime
    resolved_at: datetime | None = None


class InboxItemEventResponse(BaseModel):
    id: uuid.UUID
    event: str
    actor_id: uuid.UUID | None = None
    detail: str | None = None
    created_at: datetime


class InboxItemDetailResponse(InboxItemResponse):
    history: list[InboxItemEventResponse] = Field(default_factory=list)


class InboxListResponse(BaseModel):
    """Matches the pagination shape used elsewhere in the API."""

    items: list[InboxItemResponse]
    total: int
    # Unread rows among ``total``: the same filters, so the two always agree.
    unread: int = 0
    page: int
    page_size: int


class InboxCountResponse(BaseModel):
    """Badge numbers, plus the per-facet breakdown the sidebar needs.

    ``by_kind`` and ``by_subject_type`` are only populated when the caller asks
    for them. The nav badge polls this endpoint on a timer and needs one number;
    making it pay for two extra grouped scans every minute to fill a sidebar it
    never renders would be a cost with no reader.
    """

    # Unread items in the Open bucket: exactly the unread rows the Inbox lists.
    unread: int
    action_required: int
    open: int
    done: int = 0
    dismissed: int = 0
    # The rest is filled only when facets are requested.
    # Unread items per folder: what each folder in the filter sheet shows.
    unread_by_state: dict[str, int] = Field(default_factory=dict)
    # The selected folder (or every folder) under the active search.
    scope_total: int = 0
    scope_unread: int = 0
    scope_action_required: int = 0
    # Also narrowed by the Unread and Needs action toggles, so they add up to the list.
    by_kind: dict[str, int] = Field(default_factory=dict)
    by_subject_type: dict[str, int] = Field(default_factory=dict)


class OutdatedItem(BaseModel):
    """One outdated finding reported by the CLI.

    The character-set patterns are not cosmetic. ``namespace``, ``slug`` and
    ``harness`` are interpolated into the type-specific upgrade command that the
    web UI and CLI present as a command to run, so they are constrained to what
    a real registry identifier can contain. The versions carry ``min_length=1``
    because an empty version would truncate the ``update_available`` dedupe key
    and collide with a different fact.
    """

    type: str = Field(max_length=32, pattern=r"^[a-z_]+$")
    component_id: uuid.UUID
    name: str = Field(default="", max_length=255)
    namespace: str | None = Field(default=None, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    slug: str | None = Field(default=None, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    current_version: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9.+_-]+$")
    latest_version: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9.+_-]+$")
    harness: str | None = Field(default=None, max_length=50, pattern=r"^[a-zA-Z0-9._-]+$")


class OutdatedReportRequest(BaseModel):
    # A lock file with more entries than this is not a realistic install; the
    # bound keeps one report from writing an unbounded number of rows.
    items: list[OutdatedItem] = Field(default_factory=list, max_length=200)


class OutdatedReportResponse(BaseModel):
    created: int
    superseded: int


class BulkReadResponse(BaseModel):
    updated: int
