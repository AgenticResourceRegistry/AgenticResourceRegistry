# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

_REF_NAME_RE = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")

WebhookProvider = Literal["github", "gitlab"]


def valid_ref_name(name: str) -> bool:
    """A git branch or tag name safe to put after refs/heads/ or refs/tags/."""
    return bool(_REF_NAME_RE.match(name)) and not name.startswith(("-", "/")) and ".." not in name


class McpWebhookSyncRequest(BaseModel):
    sync_on_push: bool = True
    sync_on_release: bool = False
    # None keeps the current provider, or detects it from the repository URL when
    # sync is first turned on.
    provider: WebhookProvider | None = None
    # None tracks the repository's default branch.
    branch: str | None = Field(None, max_length=255)

    @field_validator("branch")
    @classmethod
    def _check_branch(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        if not valid_ref_name(value):
            raise ValueError("Branch name contains unsupported characters")
        return value

    @model_validator(mode="after")
    def _one_trigger(self):
        if not self.sync_on_push and not self.sync_on_release:
            raise ValueError("Turn on push sync, release sync, or both. To stop syncing, disable webhook sync.")
        return self


class McpWebhookSyncResponse(BaseModel):
    enabled: bool
    id: uuid.UUID | None = None
    provider: WebhookProvider | None = None
    webhook_url: str | None = None
    # Present only right after the secret is created or rotated; it is never shown again.
    secret: str | None = None
    sync_on_push: bool = False
    sync_on_release: bool = False
    branch: str | None = None
    last_delivery_at: datetime | None = None
    last_event: str | None = None
    last_sync_status: str | None = None
    last_sync_error: str | None = None
    last_synced_at: datetime | None = None
    last_synced_sha: str | None = None
    last_version: str | None = None
    created_at: datetime | None = None


class McpWebhookDeliveryResponse(BaseModel):
    status: str  # queued, ignored
    reason: str | None = None
    trigger: str | None = None
    ref: str | None = None
