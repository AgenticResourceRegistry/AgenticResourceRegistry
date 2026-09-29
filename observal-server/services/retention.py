# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Deployment-wide data retention purge service."""

from datetime import UTC, datetime, timedelta

from loguru import logger as optic
from sqlalchemy import delete, select

import services.dynamic_settings as ds
from database import async_session
from models.insight_meta_cache import InsightMetaCache
from models.insight_report import InsightReport, InsightReportStatus
from models.insight_session_facets import InsightSessionFacets
from models.insight_session_meta import InsightSessionMeta
from observal_shared.migration.constants import DEFAULT_PROJECT_ID

TIME_PURGE_TABLES = {"session_events": "timestamp"}
# Lightweight deletes wait until visible before dependent cleanup; the normal
# ingest query timeout (10s) is not sufficient for these maintenance writes.
RETENTION_QUERY_TIMEOUT = 120.0
# Minimum age before an unreferenced snapshot or capability action counts as an
# orphan on a run that deleted no source rows.
RETRY_GRACE = timedelta(days=7)


async def _delete_batch(table: str, time_col: str, project_id: str, cutoff_str: str) -> int:
    """Execute a lightweight delete and return one on success."""
    from services.clickhouse import _query

    sql = (
        f"DELETE FROM {table} "
        f"WHERE project_id = {{pid:String}} AND {time_col} < {{cutoff:String}} "
        "SETTINGS lightweight_deletes_sync = 1"
    )
    response = await _query(sql, {"param_pid": project_id, "param_cutoff": cutoff_str}, timeout=RETENTION_QUERY_TIMEOUT)
    if response.status_code != 200:
        optic.warning(
            "retention delete failed on table {} (status={}): {}", table, response.status_code, response.text[:200]
        )
        return 0
    return 1


async def _has_inflight_insights() -> bool:
    async with async_session() as db:
        report_id = (
            await db.execute(
                select(InsightReport.id)
                .where(InsightReport.status.in_([InsightReportStatus.pending, InsightReportStatus.running]))
                .limit(1)
            )
        ).scalar_one_or_none()
    return report_id is not None


async def _purge_time_based(project_id: str, cutoff_str: str, tables: dict[str, str]) -> dict[str, int]:
    stats: dict[str, int] = {}
    for table, time_col in tables.items():
        stats[table] = await _delete_batch(table, time_col, project_id, cutoff_str)
    return stats


# All session-derived deletes use the complete scoped session key. A bare
# session_id can also belong to a different user or harness in this project.
_SESSION_KEYS = "(user_id, harness, session_id)"
_SOURCE_SESSIONS = "SELECT DISTINCT user_id, harness, session_id FROM session_events WHERE project_id = {pid2:String}"


async def _purge_session_orphans(project_id: str, cutoff_str: str) -> dict[str, int]:
    """Remove session projections only after the source delete is visible.

    Repeated runs are safe, including after a partial failure. Capability
    actions need a cutoff: a fresh action may precede the first source push.
    """
    from services.clickhouse import _query

    tables = ("session_stats_agg", "session_checkpoints", "component_activity", "component_activity_publications")
    stats: dict[str, int] = {}
    for table in tables:
        sql = (
            f"DELETE FROM {table} WHERE project_id = {{pid:String}} "
            f"AND {_SESSION_KEYS} NOT IN ({_SOURCE_SESSIONS}) "
            "SETTINGS lightweight_deletes_sync = 1"
        )
        response = await _query(
            sql, {"param_pid": project_id, "param_pid2": project_id}, timeout=RETENTION_QUERY_TIMEOUT
        )
        if response.status_code != 200:
            raise RuntimeError(f"Retention cleanup failed for {table} (HTTP {response.status_code})")
        stats[table] = 1

    response = await _query(
        "DELETE FROM session_capabilities WHERE project_id = {pid:String} "
        "AND used_at < {cutoff:String} "
        f"AND {_SESSION_KEYS} NOT IN ({_SOURCE_SESSIONS}) "
        "SETTINGS lightweight_deletes_sync = 1",
        {"param_pid": project_id, "param_pid2": project_id, "param_cutoff": cutoff_str},
        timeout=RETENTION_QUERY_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Retention cleanup failed for session_capabilities (HTTP {response.status_code})")
    stats["session_capabilities"] = 1
    return stats


async def _purge_layer_orphans(project_id: str, cutoff_str: str) -> dict[str, int]:
    """Keep one user's hash while ANY retained session still references it.

    Fresh, not-yet-used snapshots have a grace period until the existing trace
    cutoff. Mappings are removed only once the matching snapshot is gone.
    """
    from services.clickhouse import _query

    response = await _query(
        "DELETE FROM layer_snapshots WHERE project_id = {pid:String} "
        "AND uploaded_at < {cutoff:String} "
        "AND (user_id, hash) NOT IN ("
        "  SELECT DISTINCT user_id, layer_hash FROM session_events "
        "  WHERE project_id = {pid2:String} AND layer_hash IS NOT NULL AND layer_hash != ''"
        ") SETTINGS lightweight_deletes_sync = 1",
        {"param_pid": project_id, "param_pid2": project_id, "param_cutoff": cutoff_str},
        timeout=RETENTION_QUERY_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Retention cleanup failed for layer_snapshots (HTTP {response.status_code})")
    stats = {"layer_snapshots": 1}
    for table in ("layer_components", "layer_component_extractions"):
        response = await _query(
            f"DELETE FROM {table} WHERE project_id = {{pid:String}} "
            "AND (user_id, layer_hash) NOT IN ("
            "  SELECT DISTINCT user_id, hash FROM layer_snapshots WHERE project_id = {pid2:String}"
            ") SETTINGS lightweight_deletes_sync = 1",
            {"param_pid": project_id, "param_pid2": project_id},
            timeout=RETENTION_QUERY_TIMEOUT,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Retention cleanup failed for {table} (HTTP {response.status_code})")
        stats[table] = 1
    return stats


async def _purge_session_facets(project_id: str) -> int:
    """Delete facet caches with no retained source, using bounded scoped reads.

    The source and cache live in different databases; never try to join by a
    bare session ID or delete caches when the ClickHouse read fails.
    """
    from services.clickhouse import _query

    removed = 0
    after = None
    while True:
        async with async_session() as db:
            stmt = select(
                InsightSessionFacets.id,
                InsightSessionFacets.user_id,
                InsightSessionFacets.harness,
                InsightSessionFacets.session_id,
            ).where(InsightSessionFacets.project_id == project_id)
            if after is not None:
                stmt = stmt.where(InsightSessionFacets.id > after)
            rows = (await db.execute(stmt.order_by(InsightSessionFacets.id).limit(100))).all()
            if not rows:
                break
            after = rows[-1].id
            params: dict[str, str] = {"param_pid": project_id}
            clauses = []
            for index, row in enumerate(rows):
                clauses.append(
                    f"(user_id = {{u{index}:String}} AND harness = {{h{index}:String}} "
                    f"AND session_id = {{s{index}:String}})"
                )
                params.update(
                    {f"param_u{index}": row.user_id, f"param_h{index}": row.harness, f"param_s{index}": row.session_id}
                )
            response = await _query(
                "SELECT DISTINCT user_id, harness, session_id FROM session_events "
                "WHERE project_id = {pid:String} AND (" + " OR ".join(clauses) + ") FORMAT JSON",
                params,
                timeout=RETENTION_QUERY_TIMEOUT,
            )
            response.raise_for_status()
            retained = {(r["user_id"], r["harness"], r["session_id"]) for r in response.json()["data"]}
            expired = [row.id for row in rows if (row.user_id, row.harness, row.session_id) not in retained]
            if expired:
                result = await db.execute(delete(InsightSessionFacets).where(InsightSessionFacets.id.in_(expired)))
                await db.commit()
                removed += result.rowcount or 0
    return removed


def _parse_period(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00").replace(" ", "T"))
    except (AttributeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def _purge_legacy_insight_meta(cutoff_str: str) -> dict[str, int]:
    """Conservative cleanup for legacy, no-longer-written Insights metadata.

    ``insight_session_meta`` is keyed by ``(agent_id, session_id)`` only, with
    no project, user or harness. A row is removed only if it was computed
    before the cutoff AND its session ID has no retained source event under ANY
    project, user or harness. An ambiguous bare ID therefore keeps the row.

    ``insight_meta_cache`` holds per-period session metadata for an agent. A
    cache whose period starts before the cutoff may contain expired sessions;
    it is removed (it is a cache and only costs a recompute). An unparseable
    period cannot be shown to be fresh, so it is removed too.
    """
    from services.clickhouse import _query

    cutoff = _parse_period(cutoff_str)
    if cutoff is None:
        raise ValueError("Invalid retention cutoff")
    removed_meta = 0
    after = None
    while True:
        async with async_session() as db:
            stmt = select(InsightSessionMeta.id, InsightSessionMeta.session_id).where(
                InsightSessionMeta.computed_at < cutoff
            )
            if after is not None:
                stmt = stmt.where(InsightSessionMeta.id > after)
            rows = (await db.execute(stmt.order_by(InsightSessionMeta.id).limit(100))).all()
            if not rows:
                break
            after = rows[-1].id
            ids = sorted({row.session_id for row in rows})
            response = await _query(
                "SELECT DISTINCT session_id FROM session_events WHERE session_id IN {ids:Array(String)} FORMAT JSON",
                {"param_ids": "[" + ",".join(_ch_string(value) for value in ids) + "]"},
                timeout=RETENTION_QUERY_TIMEOUT,
            )
            response.raise_for_status()
            retained = {row["session_id"] for row in response.json()["data"]}
            expired = [row.id for row in rows if row.session_id not in retained]
            if expired:
                result = await db.execute(delete(InsightSessionMeta).where(InsightSessionMeta.id.in_(expired)))
                await db.commit()
                removed_meta += result.rowcount or 0

    removed_cache = 0
    async with async_session() as db:
        caches = (await db.execute(select(InsightMetaCache.id, InsightMetaCache.period_start))).all()
        stale = [row.id for row in caches if (start := _parse_period(row.period_start)) is None or start < cutoff]
        for index in range(0, len(stale), 500):
            result = await db.execute(
                delete(InsightMetaCache).where(InsightMetaCache.id.in_(stale[index : index + 500]))
            )
            removed_cache += result.rowcount or 0
        await db.commit()
    return {"insight_session_meta": removed_meta, "insight_meta_cache": removed_cache}


def _ch_string(value: str) -> str:
    """Quote one element of a ClickHouse ``Array(String)`` query parameter."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


async def _purge_source_orphans(project_id: str, cutoff_str: str) -> dict[str, int]:
    """Retry-safe cleanup after source expiry or count-based source removal."""
    stats = await _purge_session_orphans(project_id, cutoff_str)
    stats.update(await _purge_layer_orphans(project_id, cutoff_str))
    stats["insight_session_facets"] = await _purge_session_facets(project_id)
    stats.update(await _purge_legacy_insight_meta(cutoff_str))
    return stats


async def _purge_insight_reports(score_cutoff: datetime) -> int:
    async with async_session() as db:
        completed = await db.execute(
            delete(InsightReport).where(
                InsightReport.completed_at < score_cutoff,
                InsightReport.status == InsightReportStatus.completed,
            )
        )
        stuck = await db.execute(
            delete(InsightReport).where(
                InsightReport.created_at < score_cutoff,
                InsightReport.status.in_([InsightReportStatus.failed, InsightReportStatus.pending]),
            )
        )
        await db.commit()
    return int(completed.rowcount or 0) + int(stuck.rowcount or 0)


async def _purge_count_based(project_id: str, max_trace_count: int) -> dict:
    """Delete the oldest source sessions beyond the limit. Derived cleanup runs separately.

    Returns ``{"deleted": 0|1, "cutoff": str | None, "failed": bool}``. ``failed``
    means the source may be partly deleted or unknown, so derived cleanup must wait.
    """
    from services.clickhouse import _query

    sql = (
        "SELECT toDate(timestamp) AS day, count(DISTINCT session_id) AS cnt "
        "FROM session_events WHERE project_id = {pid:String} "
        "AND timestamp >= now() - INTERVAL 730 DAY "
        "GROUP BY day ORDER BY day DESC LIMIT 730 FORMAT JSON"
    )
    response = await _query(sql, {"param_pid": project_id})
    if response.status_code != 200:
        return {"deleted": 0, "cutoff": None, "failed": True}
    data = response.json().get("data", [])
    running_total = 0
    cutoff_day = None
    for row in data:
        running_total += int(row["cnt"])
        if running_total > max_trace_count:
            cutoff_day = row["day"]
            break
    if cutoff_day is None:
        return {"deleted": 0, "cutoff": None, "failed": False}

    cutoff_str = f"{cutoff_day} 00:00:00.000"
    if not await _delete_batch("session_events", "timestamp", project_id, cutoff_str):
        return {"deleted": 0, "cutoff": cutoff_str, "failed": True}
    return {"deleted": 1, "cutoff": cutoff_str, "failed": False}


async def _retry_cutoff(project_id: str, now: datetime) -> str | None:
    """Grace cutoff for derived cleanup on a run that deleted no source rows.

    The older of the oldest retained source event and ``now - RETRY_GRACE``,
    so a fresh capability action or snapshot for a session whose first push
    has not arrived yet is never treated as an orphan. ``None`` (skip) when
    the source cannot be read.
    """
    from services.clickhouse import _query

    response = await _query(
        "SELECT toString(min(timestamp)) AS oldest, count() AS n FROM session_events "
        "WHERE project_id = {pid:String} FORMAT JSON",
        {"param_pid": project_id},
        timeout=RETENTION_QUERY_TIMEOUT,
    )
    if response.status_code != 200:
        return None
    grace = now - RETRY_GRACE
    rows = response.json().get("data", [])
    if rows and int(rows[0].get("n") or 0):
        oldest = datetime.fromisoformat(str(rows[0]["oldest"]).replace(" ", "T")).replace(tzinfo=UTC)
        grace = min(grace, oldest)
    return grace.strftime("%Y-%m-%d %H:%M:%S.000")


async def run_retention_purge(ctx: dict | None = None):
    """Run the configured retention policy for the deployment."""
    del ctx
    if not await ds.get_bool("retention.enabled"):
        return

    trace_days = await ds.get_int("retention.trace_days", default=0)
    score_days = await ds.get_int("retention.score_days", default=0)
    max_trace_count = await ds.get_int("retention.max_trace_count", default=0)
    if not trace_days and not score_days and not max_trace_count:
        return
    # Even an empty source may have orphan projections or old reports after
    # a previous, partially completed purge. Never skip their cleanup.
    if await _has_inflight_insights():
        optic.info("skipping retention purge while insights are in flight")
        return

    now = datetime.now(UTC)
    stats: dict[str, object] = {}
    source_failed = False
    cutoffs: list[str] = []
    if trace_days:
        cutoff = (now - timedelta(days=trace_days)).strftime("%Y-%m-%d %H:%M:%S.000")
        stats["time"] = await _purge_time_based(DEFAULT_PROJECT_ID, cutoff, TIME_PURGE_TABLES)
        source_failed |= not all(stats["time"].values())
        cutoffs.append(cutoff)
    if max_trace_count:
        count = await _purge_count_based(DEFAULT_PROJECT_ID, max_trace_count)
        stats["count_purge"] = count["deleted"]
        source_failed |= count["failed"]
        if count["cutoff"]:
            cutoffs.append(count["cutoff"])

    # Derived cleanup runs on EVERY run with a source policy, not only on runs
    # that deleted source rows: a derived delete that failed after a successful
    # source delete is retried even once the source is back under its limit.
    if trace_days or max_trace_count:
        if source_failed:
            optic.warning("retention source purge failed; deferring derived cleanup")
        else:
            # The oldest applicable cutoff deletes the least.
            derived_cutoff = min(cutoffs) if cutoffs else await _retry_cutoff(DEFAULT_PROJECT_ID, now)
            if derived_cutoff is None:
                optic.warning("retention source unreadable; deferring derived cleanup")
            else:
                try:
                    stats["derived"] = await _purge_source_orphans(DEFAULT_PROJECT_ID, derived_cutoff)
                except Exception as error:
                    # Retried next run; never blocks the report purge below.
                    optic.warning("retention derived cleanup incomplete: {}", type(error).__name__)

    score_days = score_days or (trace_days * 2 if trace_days else 0)
    if score_days:
        stats["insight_reports"] = await _purge_insight_reports(now - timedelta(days=max(score_days, 30)))

    optic.info("retention purge complete: {}", stats)
