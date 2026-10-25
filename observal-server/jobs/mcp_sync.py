# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""MCP GitHub webhook sync job."""

from loguru import logger as optic

# A failed fetch is retried: GitHub can deliver a release before its tag is fetchable,
# and a network blip should not lose a release.
MAX_TRIES = 3
RETRY_DELAY_SECONDS = 15


async def sync_mcp_webhook(ctx: dict, sync_id: str, trigger: str, ref: str | None, changelog: str = ""):
    """Background job: re-read an MCP's repository and publish a new version."""
    optic.debug("sync_mcp_webhook sync_id={} trigger={}", sync_id, trigger)
    from arq import Retry

    from services.mcp_webhook_sync import FetchError, SyncRequest, run_sync

    job_try = ctx.get("job_try", 1)
    try:
        await run_sync(
            sync_id, SyncRequest(trigger=trigger, ref=ref, changelog=changelog), final_attempt=job_try >= MAX_TRIES
        )
    except FetchError as e:
        raise Retry(defer=job_try * RETRY_DELAY_SECONDS) from e
