# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Worker entry point for automated review checks."""

from services.review import ci


async def run_review_checks(ctx) -> int:
    """Poll for queued checks. Claims are row-locked, so overlapping ticks never run the same check twice."""
    return await ci.run_pending()
