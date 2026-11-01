# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Maintenance background jobs: ClickHouse optimization, component source sync, retention, discovery."""

from loguru import logger as optic


async def sync_component_sources(ctx: dict):
    """Background job: sync component sources that are due for re-sync."""
    optic.debug("sync_component_sources")
    import asyncio
    from datetime import UTC, datetime

    from sqlalchemy import or_, select

    from database import async_session
    from models.component_source import ComponentSource
    from services.git_mirror_service import sync_source

    async with async_session() as db:
        # Find sources due for sync
        now = datetime.now(UTC)
        stmt = select(ComponentSource).where(
            ComponentSource.auto_sync_interval.isnot(None),
            or_(
                ComponentSource.last_synced_at.is_(None),
                ComponentSource.last_synced_at + ComponentSource.auto_sync_interval < now,
            ),
        )
        result = await db.execute(stmt)
        sources = result.scalars().all()

        for source in sources:
            optic.info("Syncing component source {} ({})", source.id, source.url)
            source.sync_status = "syncing"
            await db.commit()

            sync_result = await asyncio.to_thread(sync_source, source.url, source.component_type)

            source.last_synced_at = now
            source.sync_status = "success" if sync_result.success else "failed"
            source.sync_error = sync_result.error if not sync_result.success else None
            await db.commit()
            optic.info(
                "Sync {}: {} ({} components)",
                source.url,
                source.sync_status,
                len(sync_result.components),
            )


async def purge_inbox_items(ctx: dict):
    """Delete resolved inbox items past the retention horizon.

    Only ``done`` and ``dismissed`` items are eligible. An ``open`` item is
    unactioned work, and deleting work silently is the exact failure the inbox
    exists to prevent, so it is never purged on age. History rows go with the
    item through ON DELETE CASCADE.
    """
    optic.debug("purge_inbox_items")
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import delete, func, select

    import services.dynamic_settings as ds
    from database import async_session
    from models.inbox import InboxItem, InboxState

    retention_days = await ds.get_int("inbox.retention_days", 90)
    if retention_days <= 0:
        optic.info("inbox retention disabled (inbox.retention_days={})", retention_days)
        return

    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    # Eligibility is re-stated in the DELETE, not just used to gather ids. A user
    # can reopen an item between the two statements, and deleting by id alone
    # would destroy work they just pulled back into their queue along with its
    # history. The predicates make the delete self-guarding.
    eligible = (
        InboxItem.state.in_([InboxState.done, InboxState.dismissed]),
        InboxItem.resolved_at.is_not(None),
        InboxItem.resolved_at < cutoff,
    )
    async with async_session() as db:
        pending = (await db.execute(select(func.count(InboxItem.id)).where(*eligible))).scalar() or 0
        if not pending:
            optic.debug("inbox retention: nothing older than {} days", retention_days)
            return
        result = await db.execute(delete(InboxItem).where(*eligible))
        await db.commit()
        # Report what the delete actually removed, which can be fewer than the
        # count above if someone reopened an item in between.
        optic.info(
            "inbox retention: purged {} resolved item(s) older than {} days",
            result.rowcount,
            retention_days,
        )


async def maintain_clickhouse(ctx: dict):
    """Periodic ClickHouse maintenance: compact parts to prevent OOM on long-running agents.

    OPTIMIZE TABLE (without FINAL) merges small parts into larger ones.
    This is lightweight and safe to run frequently.  Without it, a
    month-long agent session accumulates thousands of tiny parts that
    bloat memory during merges and FINAL queries.
    """
    optic.debug("maintain_clickhouse")
    from services.clickhouse.client import _query

    tables = ["session_events", "session_stats_agg"]
    for table in tables:
        try:
            await _query(f"OPTIMIZE TABLE {table}")
        except Exception as e:
            optic.warning("ClickHouse OPTIMIZE {} failed: {}", table, type(e).__name__)

    # Check part health: warn before things get critical
    try:
        resp = await _query(
            "SELECT table, count() as parts, sum(rows) as total_rows "
            "FROM system.parts WHERE database = currentDatabase() AND active "
            "GROUP BY table FORMAT JSON"
        )
        if resp.status_code == 200:
            for row in resp.json().get("data", []):
                parts = int(row.get("parts", 0))
                if parts > 300:
                    optic.warning(
                        "ClickHouse table {} has {} active parts, merges may be falling behind",
                        row["table"],
                        parts,
                    )
    except Exception as e:
        optic.debug("Part health check failed: {}", type(e).__name__)


async def reproject_discovery_entries(ctx: dict):
    """Rebuild the discovery index from the native registry tables.

    The per-change session hook keeps the index current; this is the safety
    net for anything it missed (a crash between commit and reprojection, a
    manual database edit). Idempotent: unchanged resources only get their
    ``last_seen_at`` touched, removed resources are tombstoned.
    """
    optic.debug("reproject_discovery_entries")
    from database import async_session
    from services.discovery.projection import reproject_all

    async with async_session() as db:
        stats = await reproject_all(db)
    optic.info(
        "discovery reprojection job projected={} tombstoned={} failed={}",
        stats.projected,
        stats.tombstoned,
        stats.failed,
    )
    return {"projected": stats.projected, "tombstoned": stats.tombstoned, "failed": stats.failed}


async def _layer_rows(sql: str, params: dict) -> list[dict]:
    from services.clickhouse.client import _query

    response = await _query(sql, params)
    response.raise_for_status()
    return response.json().get("data", [])


async def _layer_snapshot_page(after: list[str] | None, batch_size: int) -> list[dict]:
    """Read bounded identity keys only. Snapshot content stays in the extractor."""
    where = ""
    params: dict = {"param_limit": batch_size}
    if after is not None:
        where = "WHERE (project_id, user_id, hash) > ({after_project:String}, {after_user:String}, {after_hash:String})"
        params.update(param_after_project=after[0], param_after_user=after[1], param_after_hash=after[2])
    from services.layer_components.queries import SNAPSHOT_CONFLICT_EXPR

    return await _layer_rows(
        f"SELECT project_id, user_id, hash, toUInt8({SNAPSHOT_CONFLICT_EXPR}) AS conflict "
        f"FROM layer_snapshots FINAL {where} "
        "ORDER BY project_id, user_id, hash LIMIT {limit:UInt16} FORMAT JSON",
        params,
    )


def _layer_key_filter(keys: list[tuple[str, str, str]]) -> tuple[str, dict]:
    """Build a bounded, parameterized tuple predicate for a snapshot page."""
    tuples = []
    params: dict = {}
    for index, (project, user, layer_hash) in enumerate(keys):
        tuples.append(f"({{p{index}:String}}, {{u{index}:String}}, {{h{index}:String}})")
        params.update({f"param_p{index}": project, f"param_u{index}": user, f"param_h{index}": layer_hash})
    return "(" + ", ".join(tuples) + ")", params


async def _layer_publications(keys: list[tuple[str, str, str]]) -> dict[tuple[str, str, str], dict]:
    """Fetch the latest *complete* current-version attempt for all page keys."""
    from services.layer_components import CURRENT_EXTRACTOR_VERSION

    predicate, params = _layer_key_filter(keys)
    params["param_extractor_version"] = CURRENT_EXTRACTOR_VERSION
    rows = await _layer_rows(
        "SELECT project_id, user_id, layer_hash, max(extraction_generation) AS generation, "
        "argMax(identity_conflict, extraction_generation) AS conflict FROM ("
        "SELECT project_id, user_id, layer_hash, extraction_generation, "
        "max(identity_conflict) AS identity_conflict FROM layer_component_extractions "
        "WHERE extractor_version = {extractor_version:UInt16} "
        f"AND (project_id, user_id, layer_hash) IN {predicate} "
        "GROUP BY project_id, user_id, layer_hash, extraction_generation "
        "HAVING countIf(status = 'complete') > 0 AND countIf(status = 'failed') = 0"
        ") GROUP BY project_id, user_id, layer_hash FORMAT JSON",
        params,
    )
    return {(row["project_id"], row["user_id"], row["layer_hash"]): row for row in rows}


async def _unresolved_layer_keys(publications: dict[tuple[str, str, str], dict]) -> set[tuple[str, str, str]]:
    """Only re-resolve rows from the published generation; never retry every layer."""
    from services.layer_components import CURRENT_EXTRACTOR_VERSION

    if not publications:
        return set()
    tuples = []
    params: dict = {"param_extractor_version": CURRENT_EXTRACTOR_VERSION}
    for index, (key, publication) in enumerate(publications.items()):
        project, user, layer_hash = key
        tuples.append(f"({{p{index}:String}}, {{u{index}:String}}, {{h{index}:String}}, {{g{index}:UInt64}})")
        params.update(
            {
                f"param_p{index}": project,
                f"param_u{index}": user,
                f"param_h{index}": layer_hash,
                f"param_g{index}": int(publication["generation"]),
            }
        )
    rows = await _layer_rows(
        "SELECT DISTINCT project_id, user_id, layer_hash FROM layer_components FINAL "
        "WHERE extractor_version = {extractor_version:UInt16} "
        "AND identity_status IN ('unresolved', 'ambiguous') "
        f"AND (project_id, user_id, layer_hash, extraction_generation) IN ({', '.join(tuples)}) FORMAT JSON",
        params,
    )
    return {(row["project_id"], row["user_id"], row["layer_hash"]) for row in rows}


async def backfill_layer_components(
    ctx: dict,
    *,
    after: list[str] | None = None,
    batch_size: int = 64,
    max_batches: int = 8,
    reresolve_unresolved: bool = False,
) -> dict:
    """Resume from a keyset cursor; queue the next bounded batch when run by arq.

    Default mode skips complete current-version publications (including zero-row
    ones). Explicit reresolution forces only a current published unresolved or
    ambiguous identity; snapshot conflicts and legacy presence remain unknown.
    """
    from services.layer_components.extractor import ensure_layer_components

    if not 1 <= batch_size <= 128 or not 1 <= max_batches <= 32:
        raise ValueError("Layer backfill batch_size/max_batches must be bounded positive integers")
    if after is not None and (
        not isinstance(after, list) or len(after) != 3 or not all(isinstance(v, str) for v in after)
    ):
        raise ValueError("Layer backfill cursor must contain three identity strings")
    counts = {"scanned": 0, "complete": 0, "skipped": 0, "missing": 0, "failed": 0, "reresolved": 0}
    next_cursor = after
    for _ in range(max_batches):
        page = await _layer_snapshot_page(next_cursor, batch_size)
        if not page:
            next_cursor = None
            break
        keys = [(row["project_id"], row["user_id"], row["hash"]) for row in page]
        snapshot_conflicts = {
            (row["project_id"], row["user_id"], row["hash"]): bool(int(row.get("conflict") or 0)) for row in page
        }
        publications = await _layer_publications(keys)
        unresolved = await _unresolved_layer_keys(publications) if reresolve_unresolved else set()
        for key in keys:
            counts["scanned"] += 1
            previous = publications.get(key)
            # A publication is current only if it reflects the snapshot's current
            # conflict state; otherwise the extractor must republish (fail closed).
            stale_conflict = bool(previous) and bool(int(previous.get("conflict") or 0)) != snapshot_conflicts[key]
            if previous and not stale_conflict and (not reresolve_unresolved or key not in unresolved):
                counts["skipped"] += 1
                continue
            try:
                result = await ensure_layer_components(*key, force=bool(previous and key in unresolved))
            except Exception as error:
                optic.warning("layer component backfill failed: {}", type(error).__name__)
                counts["failed"] += 1
                continue
            status = result["status"]
            if status == "complete":
                counts["complete"] += 1
                counts["reresolved"] += int(bool(previous and key in unresolved))
            elif status == "already_complete":
                counts["skipped"] += 1
            else:
                counts["missing"] += 1
        next_cursor = list(keys[-1])
        if len(page) < batch_size:
            next_cursor = None
            break
    if next_cursor is not None and ctx.get("redis") is not None:
        await ctx["redis"].enqueue_job(
            "backfill_layer_components",
            after=next_cursor,
            batch_size=batch_size,
            max_batches=max_batches,
            reresolve_unresolved=reresolve_unresolved,
        )
    optic.info("layer component backfill: counts={} continuing={}", counts, next_cursor is not None)
    return {**counts, "next_cursor": next_cursor}
