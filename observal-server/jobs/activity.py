# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""Bounded session activity replay, late-mapping retry and source-keyset backfill."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta

from loguru import logger as optic

import services.clickhouse.client as clickhouse
from services.component_activity import project_session_activity

_MAX_RETRIES = 5
_RETRY_STATUSES = frozenset({"pending_source", "pending_mapping"})
# The daily safety net replays recently active sessions (late snapshots, missed
# enqueues). Matcher/projection version bumps need an explicit full replay:
# ``backfill_component_activity(ctx, since_days=None)``.
_DEFAULT_REPAIR_DAYS = 7


def _chain_id(project_id: str, user_id: str, harness: str, session_id: str, revision: str = "") -> str:
    """Deterministic, opaque arq job identity for one scoped session (and source revision)."""
    digest = hashlib.sha256(
        json.dumps([project_id, user_id, harness, session_id, revision], separators=(",", ":")).encode()
    ).hexdigest()
    return digest[:40]


async def enqueue_activity_projection(
    project_id: str, user_id: str, harness: str, session_id: str, *, source_digest: str = ""
) -> bool:
    """Queue a final canonical session best-effort, never failing its ingest.

    Repeated final deliveries of the same source coalesce onto one job id; a
    repaired source (new digest) gets a fresh chain.
    """
    from services.redis import _get_arq_pool

    chain = _chain_id(project_id, user_id, harness, session_id, source_digest)
    try:
        pool = await _get_arq_pool()
        return bool(
            await pool.enqueue_job(
                "project_component_activity",
                project_id,
                user_id,
                harness,
                session_id,
                chain=chain,
                _job_id=f"activity:{chain}:0",
            )
        )
    except Exception as error:
        optic.warning("activity projection enqueue failed: {}", type(error).__name__)
        return False


async def project_component_activity(
    ctx: dict,
    project_id: str,
    user_id: str,
    harness: str,
    session_id: str,
    *,
    retry_count: int = 0,
    chain: str = "",
) -> dict:
    """Process one fully scoped session; defer missing source/mapping a bounded number of times.

    The daily keyset backfill remains the repair path after retries expire, a
    snapshot arrives late, a source rewinds, or a matcher/publication version
    changes. A pending attempt is never published as a complete zero-call session.
    """
    if not 0 <= retry_count <= _MAX_RETRIES:
        raise ValueError("Activity retry count is out of bounds")
    result = await project_session_activity(project_id, user_id, harness, session_id)
    scheduled = False
    if result["status"] in _RETRY_STATUSES and retry_count < _MAX_RETRIES and ctx.get("redis") is not None:
        chain = chain or _chain_id(project_id, user_id, harness, session_id, "repair")
        try:
            queued = await ctx["redis"].enqueue_job(
                "project_component_activity",
                project_id,
                user_id,
                harness,
                session_id,
                retry_count=retry_count + 1,
                chain=chain,
                _job_id=f"activity:{chain}:{retry_count + 1}",
                _defer_by=timedelta(seconds=min(30 * 2**retry_count, 480)),
            )
            scheduled = queued is not None
        except Exception as error:
            optic.warning("activity projection retry enqueue failed: {}", type(error).__name__)
    return {**result, "retry_scheduled": scheduled}


async def _source_session_page(
    after: list[str] | None, batch_size: int, project_id: str | None, since_days: int | None = None
) -> list[dict]:
    """Read only scoped source identities; never fetch raw transcript in the scan.

    No FINAL: replacement rows share their (sorting-key) identity columns, so
    DISTINCT keys are exact without the expensive deduplicating merge.
    """
    params: dict = {"param_limit": batch_size}
    clauses = ["is_source_record = 1"]
    if since_days is not None:
        clauses.append("ingested_at >= now64(3) - toIntervalDay({since_days:UInt16})")
        params["param_since_days"] = since_days
    if project_id is not None:
        clauses.append("project_id = {project_id:String}")
        params["param_project_id"] = project_id
    if after is not None:
        clauses.append(
            "(project_id, user_id, harness, session_id) > "
            "({after_project:String}, {after_user:String}, {after_harness:String}, {after_session:String})"
        )
        params.update(
            {
                "param_after_project": after[0],
                "param_after_user": after[1],
                "param_after_harness": after[2],
                "param_after_session": after[3],
            }
        )
    sql = (
        "SELECT DISTINCT project_id, user_id, harness, session_id FROM session_events WHERE "
        + " AND ".join(clauses)
        + " ORDER BY project_id, user_id, harness, session_id LIMIT {limit:UInt16} FORMAT JSON"
    )
    response = await clickhouse._query(sql, params)
    response.raise_for_status()
    return response.json().get("data", [])


async def backfill_component_activity(
    ctx: dict,
    *,
    project_id: str | None = None,
    after: list[str] | None = None,
    batch_size: int = 64,
    max_batches: int = 8,
    since_days: int | None = _DEFAULT_REPAIR_DAYS,
) -> dict:
    """Bounded project/session-keyset replay; return cursor for resumable manual runs.

    ``since_days`` limits the scan to recently ingested sessions (the daily
    safety net); pass ``None`` for a full replay after a version bump.

    Rechecking completed sessions is necessary: a repaired source or mapping
    and an independent matcher/publication version bump can remove old positives.
    The projector compares revisions/rows and skips unchanged publications.
    """
    if not 1 <= batch_size <= 128 or not 1 <= max_batches <= 32:
        raise ValueError("Activity backfill batch size/count is out of bounds")
    if since_days is not None and not 1 <= since_days <= 365:
        raise ValueError("Activity backfill since_days is out of bounds")
    if project_id is not None and (not isinstance(project_id, str) or not project_id):
        raise ValueError("Activity backfill project_id must be nonempty")
    if after is not None and (
        not isinstance(after, list) or len(after) != 4 or not all(isinstance(item, str) and item for item in after)
    ):
        raise ValueError("Activity backfill cursor needs project, user, harness and session")
    if after is not None and project_id is not None and after[0] != project_id:
        raise ValueError("Activity backfill cursor must belong to requested project")
    counts = {
        "scanned": 0,
        "complete": 0,
        "skipped": 0,
        "pending": 0,
        "unsupported": 0,
        "unprojectable": 0,
        "failed": 0,
    }
    cursor = after
    for _ in range(max_batches):
        page = await _source_session_page(cursor, batch_size, project_id, since_days)
        if not page:
            cursor = None
            break
        for row in page:
            counts["scanned"] += 1
            try:
                result = await project_component_activity(
                    ctx, row["project_id"], row["user_id"], row["harness"], row["session_id"]
                )
            except Exception as error:
                counts["failed"] += 1
                optic.warning("activity backfill session projection failed: {}", type(error).__name__)
                continue
            status = result["status"]
            if status == "complete":
                counts["complete"] += 1
            elif status == "already_complete":
                counts["skipped"] += 1
            elif status == "unsupported":
                counts["unsupported"] += 1
            elif status in _RETRY_STATUSES:
                counts["pending"] += 1
            else:
                # legacy/unstable layer, identity conflict or oversized source:
                # explicit unknown coverage that retrying alone cannot resolve.
                counts["unprojectable"] += 1
        cursor = [page[-1][field] for field in ("project_id", "user_id", "harness", "session_id")]
        if len(page) < batch_size:
            cursor = None
            break
    if cursor is not None and ctx.get("redis") is not None:
        try:
            await ctx["redis"].enqueue_job(
                "backfill_component_activity",
                project_id=project_id,
                after=cursor,
                batch_size=batch_size,
                max_batches=max_batches,
                since_days=since_days,
            )
        except Exception as error:
            optic.warning("activity backfill continuation enqueue failed: {}", type(error).__name__)
    optic.info("activity backfill: counts={} continuing={}", counts, cursor is not None)
    return {**counts, "next_cursor": cursor}
