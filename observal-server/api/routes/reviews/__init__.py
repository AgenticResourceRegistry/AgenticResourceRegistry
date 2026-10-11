# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Additive PR-review API; the legacy review routes stay active until cutover."""

from fastapi import APIRouter

from . import actions, detail, policy, submissions, threads

router = APIRouter(prefix="/api/v1/reviews", tags=["reviews"])
for part in (detail, threads, submissions, actions):
    router.include_router(part.router)
policy_router = policy.router
