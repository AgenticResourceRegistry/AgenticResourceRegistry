# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""Phase 2.4 best-effort ingestion, late-mapping retry and resumable replay."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jobs import activity


def _session(project: str, user: str, harness: str, session: str) -> dict:
    return {"project_id": project, "user_id": user, "harness": harness, "session_id": session}


@pytest.mark.asyncio
async def test_final_enqueue_is_scoped_and_redis_failure_cannot_fail_ingest(monkeypatch):
    pool = SimpleNamespace(enqueue_job=AsyncMock(return_value=object()))
    get_pool = AsyncMock(return_value=pool)
    monkeypatch.setattr("services.redis._get_arq_pool", get_pool)
    assert await activity.enqueue_activity_projection("p", "u", "claude-code", "s", source_digest="rev-1")
    chain = activity._chain_id("p", "u", "claude-code", "s", "rev-1")
    pool.enqueue_job.assert_awaited_once_with(
        "project_component_activity",
        "p",
        "u",
        "claude-code",
        "s",
        chain=chain,
        follow_subagents=True,
        _job_id=f"activity:{chain}:0",
    )
    # Same scoped source -> same job id (arq coalesces); a repaired source -> a new chain.
    await activity.enqueue_activity_projection("p", "u", "claude-code", "s", source_digest="rev-1")
    assert pool.enqueue_job.await_args_list[1].kwargs["_job_id"] == f"activity:{chain}:0"
    await activity.enqueue_activity_projection("p", "u", "claude-code", "s", source_digest="rev-2")
    assert pool.enqueue_job.await_args_list[2].kwargs["_job_id"] != f"activity:{chain}:0"
    assert activity._chain_id("p", "other", "claude-code", "s", "rev-1") != chain
    get_pool.side_effect = ConnectionError("queue unreachable")
    assert not await activity.enqueue_activity_projection("p", "u", "claude-code", "s")


@pytest.mark.asyncio
async def test_pending_mapping_and_source_retry_are_bounded_not_complete(monkeypatch):
    projector = AsyncMock(return_value={"status": "pending_mapping"})
    redis = SimpleNamespace(enqueue_job=AsyncMock(return_value=object()))
    monkeypatch.setattr(activity, "project_session_activity", projector)
    monkeypatch.setattr(activity, "project_session_skill_evidence", AsyncMock(return_value={"status": "unsupported"}))
    monkeypatch.setattr(activity, "project_session_hook_evidence", AsyncMock(return_value={"status": "unsupported"}))
    pending = await activity.project_component_activity({"redis": redis}, "p", "u", "claude-code", "s", chain="chain-a")
    assert pending == {
        "status": "pending_mapping",
        "skill": {"status": "unsupported"},
        "hook": {"status": "unsupported"},
        "retry_scheduled": True,
    }
    redis.enqueue_job.assert_awaited_once_with(
        "project_component_activity",
        "p",
        "u",
        "claude-code",
        "s",
        retry_count=1,
        chain="chain-a",
        _job_id="activity:chain-a:1",
        _defer_by=timedelta(seconds=30),
    )
    projector.return_value = {"status": "pending_source"}
    await activity.project_component_activity({"redis": redis}, "p", "u", "claude-code", "s", retry_count=2)
    assert redis.enqueue_job.await_args.kwargs["retry_count"] == 3
    # A backfill-started chain still gets deterministic per-retry identities.
    assert redis.enqueue_job.await_args.kwargs["_job_id"].endswith(":3")
    assert redis.enqueue_job.await_args.kwargs["_defer_by"] == timedelta(seconds=120)
    before = redis.enqueue_job.await_count
    result = await activity.project_component_activity(
        {"redis": redis}, "p", "u", "claude-code", "s", retry_count=activity._MAX_RETRIES
    )
    assert not result["retry_scheduled"] and redis.enqueue_job.await_count == before
    projector.return_value = {"status": "unsupported"}
    assert not (await activity.project_component_activity({"redis": redis}, "p", "u", "pi", "s"))["retry_scheduled"]
    assert redis.enqueue_job.await_count == before
    with pytest.raises(ValueError):
        await activity.project_component_activity({}, "p", "u", "pi", "s", retry_count=-1)


@pytest.mark.asyncio
async def test_retry_enqueue_failure_leaves_pending_for_backfill(monkeypatch):
    monkeypatch.setattr(activity, "project_session_activity", AsyncMock(return_value={"status": "pending_mapping"}))
    monkeypatch.setattr(activity, "project_session_skill_evidence", AsyncMock(return_value={"status": "unsupported"}))
    monkeypatch.setattr(activity, "project_session_hook_evidence", AsyncMock(return_value={"status": "unsupported"}))
    redis = SimpleNamespace(enqueue_job=AsyncMock(side_effect=ConnectionError("queue down")))
    assert (await activity.project_component_activity({"redis": redis}, "p", "u", "h", "s")) == {
        "status": "pending_mapping",
        "skill": {"status": "unsupported"},
        "hook": {"status": "unsupported"},
        "retry_scheduled": False,
    }


@pytest.mark.asyncio
async def test_source_page_filters_scoped_canonical_rows_with_bounded_cursor(monkeypatch):
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"data": [_session("project", "owner", "claude-code", "last")]},
    )
    query = AsyncMock(return_value=response)
    monkeypatch.setattr(activity.clickhouse, "_query", query)
    rows = await activity._source_session_page(["project", "owner", "claude-code", "earlier"], 4, "project")
    assert rows == [_session("project", "owner", "claude-code", "last")]
    sql, params = query.await_args.args
    # DISTINCT sorting-key identities are exact without the costly FINAL merge.
    assert "session_events WHERE" in sql and "FINAL" not in sql and "is_source_record = 1" in sql
    assert "ingested_at" not in sql  # no recency window requested
    assert "raw_line" not in sql and "LIMIT {limit:UInt16}" in sql
    assert "(project_id, user_id, harness, session_id) >" in sql
    assert "{project_id:String}" in sql and "'owner'" not in sql
    assert params == {
        "param_limit": 4,
        "param_project_id": "project",
        "param_after_project": "project",
        "param_after_user": "owner",
        "param_after_harness": "claude-code",
        "param_after_session": "earlier",
    }


@pytest.mark.asyncio
async def test_backfill_pages_project_and_session_keys_rechecks_completed_and_resumes(monkeypatch):
    first_page = [_session("project", "owner", "claude-code", "a"), _session("project", "other", "pi", "a")]
    second_page = [_session("project", "other", "claude-code", "b")]
    page = AsyncMock(side_effect=[first_page, second_page])
    projector = AsyncMock(
        side_effect=[
            {"status": "already_complete", "retry_scheduled": False},
            {"status": "unsupported", "retry_scheduled": False},
            {"status": "complete", "retry_scheduled": False},
        ]
    )
    redis = SimpleNamespace(enqueue_job=AsyncMock(return_value=object()))
    monkeypatch.setattr(activity, "_source_session_page", page)
    monkeypatch.setattr(activity, "project_component_activity", projector)
    first = await activity.backfill_component_activity(
        {"redis": redis}, project_id="project", batch_size=2, max_batches=1
    )
    assert first == {
        "scanned": 2,
        "complete": 0,
        "skipped": 1,
        "pending": 0,
        "unsupported": 1,
        "unprojectable": 0,
        "failed": 0,
        "next_cursor": ["project", "other", "pi", "a"],
    }
    redis.enqueue_job.assert_awaited_once_with(
        "backfill_component_activity",
        project_id="project",
        after=first["next_cursor"],
        batch_size=2,
        max_batches=1,
        since_days=activity._DEFAULT_REPAIR_DAYS,
    )
    second = await activity.backfill_component_activity(
        {"redis": redis}, project_id="project", after=first["next_cursor"], batch_size=2
    )
    assert second["next_cursor"] is None and second["complete"] == 1
    assert page.await_args_list[1].args == (first["next_cursor"], 2, "project", activity._DEFAULT_REPAIR_DAYS)
    assert redis.enqueue_job.await_count == 1
    assert projector.await_args_list[2].args[1:] == ("project", "other", "claude-code", "b")


@pytest.mark.asyncio
async def test_revision_replay_uses_persisted_cursor_and_recovers_after_enqueue_failure(monkeypatch):
    values = {}

    async def get(key):
        return values.get(key)

    async def set_value(key, value):
        values[key] = value

    async def incrby(key, value):
        values[key] = int(values.get(key, 0)) + value

    async def delete(key):
        values.pop(key, None)

    redis = SimpleNamespace(
        get=AsyncMock(side_effect=get),
        set=AsyncMock(side_effect=set_value),
        incrby=AsyncMock(side_effect=incrby),
        delete=AsyncMock(side_effect=delete),
        enqueue_job=AsyncMock(side_effect=ConnectionError("temporary queue failure")),
    )
    cursor = ["project", "owner", "claude-code", "old-session"]
    backfill = AsyncMock(
        side_effect=[
            {"next_cursor": cursor, "failed": 0},
            {"next_cursor": None, "failed": 0},
        ]
    )
    monkeypatch.setattr(activity, "backfill_component_activity", backfill)
    first = await activity.replay_activity_revision({"redis": redis})
    assert first["status"] == "continuing"
    assert backfill.await_args.kwargs == {
        "after": None,
        "since_days": None,
        "batch_size": 64,
        "max_batches": 1,
        "enqueue_continuation": False,
    }
    second = await activity.replay_activity_revision({"redis": redis})
    assert second["status"] == "complete"
    assert backfill.await_args.kwargs["after"] == cursor
    assert await redis.get(f"observal:activity:full-replay:{activity.publication_version()}") == "complete"
    assert (await activity.replay_activity_revision({"redis": redis}))["status"] == "complete"
    assert backfill.await_count == 2


@pytest.mark.asyncio
async def test_revision_replay_retries_failed_sessions_in_following_pass(monkeypatch):
    values = {}

    async def get(key):
        return values.get(key)

    async def set_value(key, value):
        values[key] = value

    async def incrby(key, value):
        values[key] = int(values.get(key, 0)) + value

    async def delete(key):
        values.pop(key, None)

    redis = SimpleNamespace(get=get, set=set_value, incrby=incrby, delete=delete)
    backfill = AsyncMock(
        side_effect=[
            {"next_cursor": None, "failed": 1},
            {"next_cursor": None, "failed": 0},
        ]
    )
    monkeypatch.setattr(activity, "backfill_component_activity", backfill)
    assert (await activity.replay_activity_revision({"redis": redis}))["status"] == "retry"
    assert (await activity.replay_activity_revision({"redis": redis}))["status"] == "complete"
    assert backfill.await_count == 2


def test_worker_registers_revision_replay_cron():
    from worker import WorkerSettings

    assert activity.replay_activity_revision in WorkerSettings.functions
    assert any(job.coroutine is activity.replay_activity_revision for job in WorkerSettings.cron_jobs)


@pytest.mark.asyncio
async def test_backfill_catches_failed_session_without_losing_checkpoint(monkeypatch):
    page = AsyncMock(return_value=[_session("p", "u", "claude-code", "s")])
    monkeypatch.setattr(activity, "_source_session_page", page)
    monkeypatch.setattr(activity, "project_component_activity", AsyncMock(side_effect=RuntimeError("worker failed")))
    result = await activity.backfill_component_activity({}, batch_size=2)
    assert result["failed"] == 1 and result["scanned"] == 1 and result["next_cursor"] is None
    with pytest.raises(ValueError):
        await activity.backfill_component_activity({}, after=["p", "u", "s"])
    with pytest.raises(ValueError):
        await activity.backfill_component_activity({}, project_id="other", after=["p", "u", "h", "s"])
    with pytest.raises(ValueError):
        await activity.backfill_component_activity({}, batch_size=129)
    page.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("integrity_ok,should_enqueue", [(True, True), (False, False)])
async def test_final_ingest_enqueues_only_integrity_checked_source_and_never_fails_on_queue(
    monkeypatch, integrity_ok, should_enqueue
):
    from starlette.requests import Request

    from api.routes.ingest import SessionIngestRequest, ingest_session
    from services import redis as redis_service
    from services import session_ingest

    monkeypatch.setattr(
        session_ingest,
        "ingest_session_lines",
        AsyncMock(return_value=SimpleNamespace(ingested=1, skipped=0, errors=0)),
    )
    monkeypatch.setattr(session_ingest, "advance_session_checkpoint", AsyncMock(return_value=(0, 10)))
    monkeypatch.setattr(
        session_ingest,
        "check_session_integrity",
        AsyncMock(
            return_value=SimpleNamespace(
                ok=integrity_ok,
                server_hash="digest",
                repair_from_line=None,
            )
        ),
    )
    enqueue = AsyncMock(side_effect=ConnectionError("transient"))
    monkeypatch.setattr(activity, "enqueue_activity_projection", enqueue)
    monkeypatch.setattr(redis_service, "publish", AsyncMock())
    req = SessionIngestRequest(
        session_id="s",
        harness="claude-code",
        lines=["{}"],
        final=True,
        total_line_count=1,
        total_offset=10,
    )
    request = Request({"type": "http", "method": "POST", "path": "/api/v1/ingest/session", "headers": []})
    result = await ingest_session.__wrapped__(req, request, SimpleNamespace(id="owner"))
    assert result.ingested == 1 and result.integrity_ok is integrity_ok
    assert enqueue.await_count == int(should_enqueue)
    if should_enqueue:
        from observal_shared.migration.constants import DEFAULT_PROJECT_ID

        enqueue.assert_awaited_once_with(DEFAULT_PROJECT_ID, "owner", "claude-code", "s", source_digest="digest")


def test_activity_jobs_are_registered_and_backfill_has_daily_safety_net():
    from worker import WorkerSettings

    assert activity.project_component_activity in WorkerSettings.functions
    assert activity.backfill_component_activity in WorkerSettings.functions
    assert any(job.coroutine is activity.backfill_component_activity for job in WorkerSettings.cron_jobs)


@pytest.mark.asyncio
async def test_recency_window_and_unprojectable_statuses(monkeypatch):
    response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": []})
    query = AsyncMock(return_value=response)
    monkeypatch.setattr(activity.clickhouse, "_query", query)
    await activity._source_session_page(None, 4, None, 7)
    sql, params = query.await_args.args
    assert "ingested_at >= now64(3) - toIntervalDay({since_days:UInt16})" in sql and params["param_since_days"] == 7

    page = AsyncMock(return_value=[_session("p", "u", "claude-code", str(i)) for i in range(3)])
    statuses = [{"status": s} for s in ("source_too_large", "identity_conflict", "pending_mapping")]
    monkeypatch.setattr(activity, "_source_session_page", page)
    monkeypatch.setattr(activity, "project_component_activity", AsyncMock(side_effect=statuses))
    result = await activity.backfill_component_activity({}, batch_size=4, since_days=None)
    assert (result["unprojectable"], result["pending"]) == (2, 1)
    assert page.await_args.args[3] is None
    with pytest.raises(ValueError):
        await activity.backfill_component_activity({}, since_days=0)


@pytest.mark.asyncio
async def test_a_projection_race_is_logged_with_its_reason_and_other_errors_by_type_only(monkeypatch):
    """Regression: the worker logged only "RuntimeError" for the designed fail-closed race."""
    from services.component_activity.projector import ProjectionRaceError

    logged = []
    monkeypatch.setattr(activity.optic, "warning", lambda message, *args: logged.append(message.format(*args)))
    monkeypatch.setattr(activity, "project_session_activity", AsyncMock(return_value={"status": "complete"}))
    monkeypatch.setattr(
        activity,
        "project_session_skill_evidence",
        AsyncMock(
            side_effect=ProjectionRaceError("Canonical source or published skill mapping changed during publication")
        ),
    )
    monkeypatch.setattr(
        activity,
        "project_session_hook_evidence",
        AsyncMock(side_effect=ValueError("SELECT secret FROM sessions WHERE user = 'alice'")),
    )
    result = await activity.project_component_activity({}, "default", "u1", "claude-code", "s1")
    assert result["skill"] == {"status": "failed"} and result["hook"] == {"status": "failed"}
    assert (
        "skill evidence projection failed: ProjectionRaceError: "
        "Canonical source or published skill mapping changed during publication"
    ) in logged
    assert "hook evidence projection failed: ValueError" in logged
    assert not any("secret" in line for line in logged), "other exceptions are logged by type only"


def _no_projection(monkeypatch, status: str = "complete") -> None:
    monkeypatch.setattr(activity, "project_session_activity", AsyncMock(return_value={"status": status}))
    monkeypatch.setattr(activity, "project_session_skill_evidence", AsyncMock(return_value={"status": "complete"}))
    monkeypatch.setattr(activity, "project_session_hook_evidence", AsyncMock(return_value={"status": "complete"}))


@pytest.mark.asyncio
async def test_a_final_parent_re_projects_its_subagent_sessions_later(monkeypatch):
    """A subagent's hook context reads its parent session, which may arrive after the subagent's projection."""
    _no_projection(monkeypatch)
    children = AsyncMock(return_value=["sub-a", "sub-b"])
    monkeypatch.setattr(activity, "subagent_sessions", children)
    redis = SimpleNamespace(enqueue_job=AsyncMock(side_effect=[object(), None]))
    result = await activity.project_component_activity(
        {"redis": redis}, "p", "u", "claude-code", "parent", chain="parent-rev-1", follow_subagents=True
    )
    assert result["subagents_queued"] == 1, "an already queued job id is not counted"
    children.assert_awaited_once_with("p", "u", "claude-code", "parent")
    first = redis.enqueue_job.await_args_list[0]
    child_chain = activity._chain_id("p", "u", "claude-code", "sub-a", "parent:parent-rev-1")
    assert first.args == ("project_component_activity", "p", "u", "claude-code", "sub-a")
    assert first.kwargs == {
        "chain": child_chain,
        "_job_id": f"activity:{child_chain}:0",
        "_defer_by": timedelta(seconds=60),
    }, "children never follow their own subagents"
    # A repaired parent source is a new chain, so its subagents are projected again.
    assert activity._chain_id("p", "u", "claude-code", "sub-a", "parent:parent-rev-2") != child_chain


@pytest.mark.asyncio
async def test_subagents_are_followed_only_on_a_first_final_attempt_of_a_hook_harness(monkeypatch):
    _no_projection(monkeypatch)
    children = AsyncMock(return_value=["sub-a"])
    monkeypatch.setattr(activity, "subagent_sessions", children)
    redis = SimpleNamespace(enqueue_job=AsyncMock(return_value=object()))
    plain = await activity.project_component_activity({"redis": redis}, "p", "u", "claude-code", "s", chain="c")
    assert "subagents_queued" not in plain, "backfill and retries do not fan out"
    retry = await activity.project_component_activity(
        {"redis": redis}, "p", "u", "claude-code", "s", retry_count=1, chain="c", follow_subagents=True
    )
    assert "subagents_queued" not in retry
    pi = await activity.project_component_activity({"redis": redis}, "p", "u", "pi", "s", follow_subagents=True)
    assert pi["subagents_queued"] == 0, "Pi hook evidence cannot resolve subagents"
    assert children.await_count == 0 and redis.enqueue_job.await_count == 0
    assert (await activity.project_component_activity({}, "p", "u", "claude-code", "s", follow_subagents=True))[
        "subagents_queued"
    ] == 0


@pytest.mark.asyncio
async def test_a_subagent_follow_up_failure_never_fails_the_parent(monkeypatch):
    _no_projection(monkeypatch)
    logged = []
    monkeypatch.setattr(activity.optic, "warning", lambda message, *args: logged.append(message.format(*args)))
    monkeypatch.setattr(activity, "subagent_sessions", AsyncMock(side_effect=ConnectionError("SELECT secret")))
    redis = SimpleNamespace(enqueue_job=AsyncMock(return_value=object()))
    result = await activity.project_component_activity(
        {"redis": redis}, "p", "u", "claude-code", "s", chain="c", follow_subagents=True
    )
    assert result["status"] == "complete" and result["subagents_queued"] == 0
    assert logged == ["subagent re-projection enqueue failed: ConnectionError"]


@pytest.mark.asyncio
async def test_subagent_sessions_are_scoped_to_the_parents_project_user_and_harness(monkeypatch):
    from services.component_activity import hook_projector

    seen = []

    async def fake_query(sql, params=None, *, data=None):
        seen.append((sql, params))
        return [{"session_id": "sub-a"}, {"session_id": ""}]

    monkeypatch.setattr(hook_projector, "_query", fake_query)
    assert await hook_projector.subagent_sessions("p", "u", "claude-code", "parent") == ["sub-a"]
    sql, params = seen[0]
    for clause in (
        "project_id = {project_id:String}",
        "user_id = {user_id:String}",
        "harness = {harness:String}",
        "parent_session_id = {session_id:String}",
        "is_source_record = 1",
        "LIMIT {limit:UInt16}",
    ):
        assert clause in sql
    assert params == {
        "param_project_id": "p",
        "param_user_id": "u",
        "param_harness": "claude-code",
        "param_session_id": "parent",
        "param_limit": hook_projector.MAX_SUBAGENT_SESSIONS,
    }
