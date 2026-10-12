# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Shared Pydantic schemas for component version API endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from schemas.skill_resources import SkillFolderSnapshot, SkillRevision


class VersionPublishRequest(BaseModel):
    version: str
    description: str
    changelog: str | None = None
    supported_harnesses: list[str] = []
    extra: dict | None = None


class SkillFolderPublishRequest(SkillFolderSnapshot):
    """One-shot pending version from a full folder, never a partial inheritance."""

    version: str
    description: str
    changelog: str | None = None
    observed_base_revision: SkillRevision | None = None


class VersionReviewRequest(BaseModel):
    action: Literal["approve", "reject"]
    reason: str | None = None
    observed_revision: SkillRevision | None = None
    # Only for approving a Git→direct skill conversion with no stored base file tree.
    git_base_acknowledged: bool = False
