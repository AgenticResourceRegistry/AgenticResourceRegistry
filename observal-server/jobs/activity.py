# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""Bounded session activity replay, late-mapping retry and source-keyset backfill."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta

from loguru import logger as optic

import services.clickhouse.client as clickhouse
from observal_shared.harness_registry import HARNESS_REGISTRY
from services.component_activity import (
    project_session_activity,
    project_session_hook_evidence,
    project_session_skill_evidence,
    publication_version,
)
from services.component_activity.hook_projector import MAX_SUBAGENT_SESSIONS, subagent_sessions
from services.component_activity.projector import ProjectionRaceError

_MAX_RETRIES = 5
_RETRY_STATUSES = frozenset({"pending_source", "pending_mapping"})
# A parent's final push re-projects its subagent sessions after this delay, so the
# subagents' own final pushes (sent right after the parent's) usually land first.
_SUBAGENT_DELAY = timedelta(seconds=60)
# The daily safety net replays recently active sessions (late snapshots, missed
# enqueues). The revision-triggered job below runs a durable full replay.
_DEFAULT_REPAIR_DAYS = 7


def _describe(error: Exception) -> str:
    """Type name, plus the message only for our own fixed-text race error.

    Other exceptions (ClickHouse, HTTP, parsing) can carry query text or data,
    so only their type is logged.
    """
    if isinstance(error, ProjectionRaceError):
        return f"{type(error).__name__}: {error}"
    return type(error).__name__


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
    repaired source (new digest) gets a fresh chain. The job also re-projects
    the session's subagent sessions, whose hook context reads this source.
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
                follow_subagents=True,
                _job_id=f"activity:{chain}:0",
            )
        )
    except Exception as error:
        optic.warning("activity projection enqueue failed: {}", type(error).__name__)
        return False


async def _enqueue_subagent_projections(
    ctx: dict, project_id: str, user_id: str, harness: str, session_id: str, chain: str
) -> int:
    """Re-project a parent's subagent sessions: their hook context names the agent from this source.

    Best-effort; the daily backfill repairs anything missed. Job ids derive from
    the parent's chain, so a repaired parent source re-projects them again.
    """
    if ctx.get("redis") is None or not HARNESS_REGISTRY.get(harness, {}).get("hook_evidence_extractor"):
        return 0
    queued = 0
    try:
        children = await subagent_sessions(project_id, user_id, harness, session_id)
        if len(children) >= MAX_SUBAGENT_SESSIONS:
            optic.warning("subagent re-projection capped: sessions={}", len(children))
        for child in children:
            child_chain = _chain_id(project_id, user_id, harness, child, f"parent:{chain}")
            job = await ctx["redis"].enqueue_job(
                "project_component_activity",
                project_id,
                user_id,
                harness,
                child,
                chain=child_chain,
                _job_id=f"activity:{child_chain}:0",
                _defer_by=_SUBAGENT_DELAY,
            )
            queued += job is not None
    except Exception as error:
        optic.warning("subagent re-projection enqueue failed: {}", type(error).__name__)
    return queued


async def project_component_activity(
    ctx: dict,
    project_id: str,
    user_id: str,
    harness: str,
    session_id: str,
    *,
    retry_count: int = 0,
    chain: str = "",
    follow_subagents: bool = False,
) -> dict:
    """Process one fully scoped session; defer missing source/mapping a bounded number of times.

    The daily keyset backfill remains the repair path after retries expire, a
    snapshot arrives late, a source rewinds, or a matcher/publication version
    changes. A pending attempt is never published as a complete zero-call session.

    ``follow_subagents`` (set by a final ingest, first attempt only) also queues
    the session's subagent sessions, whose hook context reads this session.
    """
    if not 0 <= retry_count <= _MAX_RETRIES:
        raise ValueError("Activity retry count is out of bounds")
    result = await project_session_activity(project_id, user_id, harness, session_id)
    # Skill and hook evidence are independent projections: their outcomes never
    # change the MCP result, and a failure in one never blocks another.
    extra: dict[str, dict] = {}
    for name, project in (("skill", project_session_skill_evidence), ("hook", project_session_hook_evidence)):
        try:
            extra[name] = await project(project_id, user_id, harness, session_id)
        except Exception as error:
            optic.warning("{} evidence projection failed: {}", name, _describe(error))
            extra[name] = {"status": "failed"}
    pending = result["status"] in _RETRY_STATUSES or any(value["status"] in _RETRY_STATUSES for value in extra.values())
    scheduled = False
    if pending and retry_count < _MAX_RETRIES and ctx.get("redis") is not None:
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
    outcome = {**result, **extra, "retry_scheduled": scheduled}
    if follow_subagents and retry_count == 0:
        parent_chain = chain or _chain_id(project_id, user_id, harness, session_id, "repair")
        outcome["subagents_queued"] = await _enqueue_subagent_projections(
            ctx, project_id, user_id, harness, session_id, parent_chain
        )
    return outcome


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
    enqueue_continuation: bool = True,
) -> dict:
    """Bounded project/session-keyset replay; return cursor for resumable runs.

    ``since_days`` limits the scan to recently ingested sessions (the daily
    safety net); the revision job passes ``None`` for a full replay after a bump.

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
                optic.warning("activity backfill session projection failed: {}", _describe(error))
                continue
            if any(result.get(name, {}).get("status") == "failed" for name in ("skill", "hook")):
                # The MCP projection succeeded; still retry the failed one on the next replay pass.
                counts["failed"] += 1
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
    if cursor is not None and enqueue_continuation and ctx.get("redis") is not None:
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


async def replay_activity_revision(ctx: dict) -> dict:
    """Durably replay all historical source sessions once per publication version.

    The Redis checkpoint advances only after a bounded page completes. A crash
    or enqueue failure can repeat a page but cannot skip it; the scheduled job
    resumes the checkpoint even when the immediate continuation was lost.
    """
    redis = ctx["redis"]
    version = publication_version()
    key = f"observal:activity:full-replay:{version}"
    failed_key = f"{key}:failures"
    saved = await redis.get(key)
    if saved in ("complete", b"complete"):
        return {"status": "complete", "publication_version": version}
    after = json.loads(saved) if saved else None
    result = await backfill_component_activity(
        ctx, after=after, since_days=None, batch_size=64, max_batches=1, enqueue_continuation=False
    )
    if result["failed"]:
        await redis.incrby(failed_key, result["failed"])
    cursor = result["next_cursor"]
    if cursor is None:
        failures = int(await redis.get(failed_key) or 0)
        if failures:
            # Retry transient projection errors in another scheduled pass.
            await redis.set(key, "")
            await redis.delete(failed_key)
            optic.warning("activity full replay will retry: version={} failures={}", version, failures)
            return {"status": "retry", "publication_version": version, **result}
        await redis.set(key, "complete")
        return {"status": "complete", "publication_version": version, **result}
    await redis.set(key, json.dumps(cursor))
    try:
        digest = hashlib.sha256(json.dumps(cursor, separators=(",", ":")).encode()).hexdigest()[:24]
        await redis.enqueue_job("replay_activity_revision", _job_id=f"activity:full:{version}:{digest}")
    except Exception as error:
        optic.warning("activity full replay continuation enqueue failed: {}", type(error).__name__)
    return {"status": "continuing", "publication_version": version, **result}
