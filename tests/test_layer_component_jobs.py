# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""Phase 1.6 bounded, user-scoped layer projection repair and init ordering."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from jobs import maintenance


def _snapshot(project: str, user: str, layer_hash: str) -> dict:
    return {"project_id": project, "user_id": user, "hash": layer_hash}


@pytest.mark.asyncio
async def test_snapshot_page_reads_only_keys_with_bounded_parameterized_keyset(monkeypatch):
    rows = AsyncMock(return_value=[])
    monkeypatch.setattr(maintenance, "_layer_rows", rows)
    await maintenance._layer_snapshot_page(["project", "user", "hash"], 64)
    sql, params = rows.await_args.args
    assert "SELECT project_id, user_id, hash, toUInt8(" in sql and "FROM layer_snapshots FINAL" in sql
    assert "identity_conflict" in sql
    # Only a server-side conflict flag is derived from content; content itself is never selected.
    assert ", content" not in sql and "LIMIT {limit:UInt16}" in sql
    assert "{after_project:String}" in sql and "ORDER BY project_id, user_id, hash" in sql
    assert params == {
        "param_after_project": "project",
        "param_after_user": "user",
        "param_after_hash": "hash",
        "param_limit": 64,
    }
    with pytest.raises(ValueError):
        await maintenance.backfill_layer_components({}, after=["malformed"], batch_size=64)
    with pytest.raises(ValueError):
        await maintenance.backfill_layer_components({}, batch_size=129)
    assert rows.await_count == 1  # Reject invalid arguments before scanning.


@pytest.mark.asyncio
async def test_batch_status_lookup_is_versioned_scoped_and_does_not_use_failed_attempt(monkeypatch):
    rows = AsyncMock(return_value=[{"project_id": "p", "user_id": "u", "layer_hash": "h", "generation": 12}])
    monkeypatch.setattr(maintenance, "_layer_rows", rows)
    publications = await maintenance._layer_publications([("p", "u", "h"), ("p", "other", "h")])
    assert publications[("p", "u", "h")]["generation"] == 12
    sql, params = rows.await_args.args
    assert "extractor_version = {extractor_version:UInt16}" in sql
    assert "HAVING countIf(status = 'complete') > 0 AND countIf(status = 'failed') = 0" in sql
    assert "argMax(identity_conflict, extraction_generation)" in sql
    assert params["param_p0"] == "p" and params["param_u1"] == "other"
    assert "'other'" not in sql  # No untrusted identity is spliced into SQL.


@pytest.mark.asyncio
async def test_paged_backfill_skips_zero_row_publication_and_resumes_via_arq(monkeypatch):
    page = AsyncMock(
        side_effect=[
            [_snapshot("p", "user-a", "hash"), _snapshot("p", "user-b", "hash")],
            [_snapshot("p", "user-c", "hash")],
        ]
    )
    marker = {"generation": 7, "conflict": 0}  # Complete with zero component rows is still complete.
    publication = AsyncMock(side_effect=[{("p", "user-a", "hash"): marker}, {}])
    ensure = AsyncMock(return_value={"status": "complete"})
    redis = AsyncMock()
    monkeypatch.setattr(maintenance, "_layer_snapshot_page", page)
    monkeypatch.setattr(maintenance, "_layer_publications", publication)
    monkeypatch.setattr("services.layer_components.extractor.ensure_layer_components", ensure)
    first = await maintenance.backfill_layer_components({"redis": redis}, batch_size=2, max_batches=1)
    assert first == {
        "scanned": 2,
        "complete": 1,
        "skipped": 1,
        "missing": 0,
        "failed": 0,
        "reresolved": 0,
        "next_cursor": ["p", "user-b", "hash"],
    }
    ensure.assert_awaited_once_with("p", "user-b", "hash", force=False)
    redis.enqueue_job.assert_awaited_once_with(
        "backfill_layer_components", after=first["next_cursor"], batch_size=2, max_batches=1, reresolve_unresolved=False
    )
    second = await maintenance.backfill_layer_components({"redis": redis}, after=first["next_cursor"], batch_size=2)
    assert second["next_cursor"] is None and second["complete"] == 1
    assert page.await_args_list[1].args[0] == first["next_cursor"]
    assert redis.enqueue_job.await_count == 1


@pytest.mark.asyncio
async def test_reresolve_unresolved_forces_only_published_unresolved(monkeypatch):
    keys = [_snapshot("p", "user-a", "hash"), _snapshot("p", "user-b", "hash")]
    monkeypatch.setattr(maintenance, "_layer_snapshot_page", AsyncMock(return_value=keys))
    monkeypatch.setattr(
        maintenance,
        "_layer_publications",
        AsyncMock(return_value={("p", row["user_id"], "hash"): {"generation": 4} for row in keys}),
    )
    unresolved = AsyncMock(return_value={("p", "user-b", "hash")})
    ensure = AsyncMock(return_value={"status": "complete"})
    monkeypatch.setattr(maintenance, "_unresolved_layer_keys", unresolved)
    monkeypatch.setattr("services.layer_components.extractor.ensure_layer_components", ensure)
    result = await maintenance.backfill_layer_components({}, batch_size=3, reresolve_unresolved=True)
    assert result["reresolved"] == 1 and result["skipped"] == 1 and result["next_cursor"] is None
    ensure.assert_awaited_once_with("p", "user-b", "hash", force=True)
    unresolved.assert_awaited_once()


@pytest.mark.asyncio
async def test_unresolved_query_is_published_generation_only(monkeypatch):
    rows = AsyncMock(return_value=[])
    monkeypatch.setattr(maintenance, "_layer_rows", rows)
    await maintenance._unresolved_layer_keys({("p", "u", "h"): {"generation": 10}})
    sql, params = rows.await_args.args
    assert "FROM layer_components FINAL" in sql
    assert "identity_status IN ('unresolved', 'ambiguous')" in sql
    assert "(project_id, user_id, layer_hash, extraction_generation) IN" in sql
    assert params["param_g0"] == 10


@pytest.mark.asyncio
async def test_failed_snapshot_does_not_stop_page_and_init_fails_loudly(monkeypatch):
    monkeypatch.setattr(maintenance, "_layer_snapshot_page", AsyncMock(return_value=[_snapshot("p", "u", "h")]))
    monkeypatch.setattr(maintenance, "_layer_publications", AsyncMock(return_value={}))
    monkeypatch.setattr(
        "services.layer_components.extractor.ensure_layer_components", AsyncMock(side_effect=RuntimeError("private"))
    )
    result = await maintenance.backfill_layer_components({}, batch_size=2)
    assert result["failed"] == 1 and result["next_cursor"] is None
    job = AsyncMock(return_value=result)
    monkeypatch.setattr(maintenance, "backfill_layer_components", job)
    with pytest.raises(RuntimeError, match="backfill failed for 1 snapshot"):
        await maintenance.initial_layer_component_backfill()


@pytest.mark.asyncio
async def test_init_continues_without_enqueue_and_counts_multiple_pages(monkeypatch):
    job = AsyncMock(
        side_effect=[
            {
                "scanned": 2,
                "complete": 1,
                "skipped": 1,
                "missing": 0,
                "failed": 0,
                "reresolved": 0,
                "next_cursor": ["p", "u", "h"],
            },
            {
                "scanned": 1,
                "complete": 1,
                "skipped": 0,
                "missing": 0,
                "failed": 0,
                "reresolved": 0,
                "next_cursor": None,
            },
        ]
    )
    monkeypatch.setattr(maintenance, "backfill_layer_components", job)
    totals = await maintenance.initial_layer_component_backfill()
    assert totals["scanned"] == 3 and totals["complete"] == 2 and totals["skipped"] == 1
    assert job.await_args_list[0].args == ({},)
    assert job.await_args_list[1].kwargs == {"after": ["p", "u", "h"]}


def test_worker_cron_and_init_invocation_after_migrations():
    from worker import WorkerSettings

    assert maintenance.backfill_layer_components in WorkerSettings.functions
    assert any(job.coroutine == maintenance.backfill_layer_components for job in WorkerSettings.cron_jobs)
    script = (Path(__file__).resolve().parents[1] / "docker/entrypoint.sh").read_text()
    assert script.index("python -m alembic upgrade head") < script.index("python -m services.clickhouse.migrations")
    assert script.index("python -m services.clickhouse.migrations") < script.index("python -m jobs.maintenance")


@pytest.mark.asyncio
async def test_backfill_republishes_when_snapshot_conflict_postdates_complete_publication(monkeypatch):
    rows = [
        _snapshot("p", "conflicted-later", "hash") | {"conflict": 1},
        _snapshot("p", "consistent", "hash") | {"conflict": 0},
    ]
    page = AsyncMock(return_value=rows)
    clean = {"generation": 7, "conflict": 0}
    publication = AsyncMock(return_value={("p", "conflicted-later", "hash"): clean, ("p", "consistent", "hash"): clean})
    ensure = AsyncMock(return_value={"status": "complete"})
    monkeypatch.setattr(maintenance, "_layer_snapshot_page", page)
    monkeypatch.setattr(maintenance, "_layer_publications", publication)
    monkeypatch.setattr("services.layer_components.extractor.ensure_layer_components", ensure)
    result = await maintenance.backfill_layer_components({}, batch_size=4)
    # A stale non-conflicted publication is not skipped; the consistent one is.
    ensure.assert_awaited_once_with("p", "conflicted-later", "hash", force=False)
    assert (result["complete"], result["skipped"]) == (1, 1)
