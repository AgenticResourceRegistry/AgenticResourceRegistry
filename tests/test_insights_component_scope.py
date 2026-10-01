# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Same bare session ID must never join two users' Insights data."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.insights.scope import SessionKey


@pytest.mark.asyncio
async def test_transcript_uses_all_four_identity_fields(monkeypatch):
    from services.insights import transcript

    alice = SessionKey("default", "alice", "claude-code", "same-id")
    bob = SessionKey("default", "bob", "claude-code", "same-id")
    calls = []

    async def query(sql, params):
        calls.append((sql, params))
        assert all(field in sql for field in ("project_id =", "user_id =", "harness =", "session_id ="))
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "data": [
                    {
                        "event_type": "user_prompt",
                        "raw_line": '{"message":{"content":"' + params["param_user_id"] + '"}}',
                    }
                ]
            },
        )

    monkeypatch.setattr(transcript, "get_query", lambda: query)
    assert "alice" in await transcript.build_session_transcript(alice)
    assert "bob" in await transcript.build_session_transcript(bob)
    assert calls[0][1]["param_user_id"] != calls[1][1]["param_user_id"]


@pytest.mark.asyncio
async def test_meta_discovery_preserves_collisions_and_filters_source_by_tuple(monkeypatch):
    from services.insights import session_meta_extractor as meta

    alice = SessionKey("default", "alice", "claude-code", "same-id")
    bob = SessionKey("default", "bob", "claude-code", "same-id")
    queries = []

    async def query(sql, params):
        queries.append((sql, params))
        if "SELECT project_id, user_id, harness, session_id, raw_line" in sql:
            assert "{user_0:String}" in sql and "{user_1:String}" in sql
            assert {params["param_user_0"], params["param_user_1"]} == {"alice", "bob"}
            rows = [
                {
                    "project_id": "default",
                    "user_id": user,
                    "harness": "claude-code",
                    "session_id": "same-id",
                    "raw_line": '{"type":"message","message":{"role":"user","content":"hello ' + user + '"}}',
                }
                for user in ("alice", "bob")
            ]
        else:
            rows = [
                dict(zip(("project_id", "user_id", "harness", "session_id"), key.as_tuple(), strict=True))
                for key in (alice, bob)
            ]
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": rows})

    monkeypatch.setattr(meta, "get_query", lambda: query)
    lines = await meta.fetch_all_session_transcripts("agent", "start", "end")
    assert set(lines) == {alice, bob}
    assert lines[alice] != lines[bob]
    assert "GROUP BY project_id, user_id, harness, session_id" in queries[0][0]


@pytest.mark.asyncio
async def test_facets_cache_is_scoped_not_agent_or_bare_session(monkeypatch):
    from services.insights import facets

    alice = SessionKey("default", "alice", "pi", "same-id")
    bob = SessionKey("default", "bob", "pi", "same-id")
    cache_rows = [
        SimpleNamespace(
            project_id="default",
            user_id="alice",
            harness="pi",
            session_id="same-id",
            facet_version=1,
            facets={"goal": "Alice's goal"},
        )
    ]
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: cache_rows)))
    )
    from models.insight_session_facets import InsightSessionFacets

    monkeypatch.setattr(facets, "get_facets_model", lambda: InsightSessionFacets)
    result = await facets.load_cached_facets_batch([alice, bob], db)
    assert result == {alice: {"goal": "Alice's goal"}}
    assert bob not in result
    sql = str(db.execute.await_args.args[0].compile(compile_kwargs={"literal_binds": True}))
    assert "user_id" in sql and "harness" in sql and "project_id" in sql


@pytest.mark.asyncio
async def test_component_content_uses_published_summary_without_claiming_missing_use(monkeypatch):
    from schemas.component_activity import ActivityCoverage, CallCoverage, PresenceCoverage, ProjectionCoverage
    from services.insights import component_report

    summary = {
        "present_sessions": 2,
        "present_users": 2,
        "observed_sessions": 0,
        "observed_calls": 0,
        "result_states": {"success": 0, "error": 0, "unknown": 0},
        "harness_distribution": {"claude-code": 2},
        "version_distribution": {"1.0.0": 2},
        "activation_actions": {},
        "coverage": ActivityCoverage(
            presence=PresenceCoverage(present_sessions=2),
            projection=ProjectionCoverage(projection_pending_sessions=2),
            calls=CallCoverage(),
            attribution_state="attribution_not_possible",
            reasons=["projection_pending_sessions"],
        ),
    }
    summary_fn = AsyncMock(return_value=summary)
    monkeypatch.setattr(component_report, "activity_summary", summary_fn)
    report = SimpleNamespace(
        subject_type="component",
        project_id="default",
        component_type="mcp",
        component_id=uuid.uuid4(),
        component_version_id=None,
        component_version=None,
        triggered_by=None,
        period_start="start",
        period_end="end",
    )
    result = await component_report.generate_component_content(report)
    assert summary_fn.await_count == 1
    assert result["coverage"]["attribution_state"] == "attribution_not_possible"
    assert "unavailable" in result["narrative"]["summary"]
    assert "unused" not in result["narrative"]["summary"]


@pytest.mark.asyncio
async def test_component_routes_authorize_before_any_activity_or_report_read(monkeypatch):
    from fastapi import HTTPException

    from api.routes import insights

    user = SimpleNamespace(id=uuid.uuid4())
    db = SimpleNamespace(execute=AsyncMock())
    activity = AsyncMock()
    monkeypatch.setattr(insights, "activity_summary", activity)

    async def deny(*_args):
        raise HTTPException(status_code=403, detail="Not an owner")

    monkeypatch.setattr(insights, "_authorize_component", deny)
    with pytest.raises(HTTPException) as exc:
        await insights.generate_component_insight("mcp", "listing", None, db, user)
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException):
        await insights.list_component_reports("mcp", "listing", db, user)
    assert not activity.await_count
    assert not db.execute.await_count


@pytest.mark.asyncio
async def test_component_report_read_and_export_recheck_ownership(monkeypatch):
    from fastapi import HTTPException

    from api.routes import insights
    from models.insight_report import InsightReportStatus

    report = SimpleNamespace(
        id=uuid.uuid4(),
        subject_type="component",
        component_type="mcp",
        component_id=uuid.uuid4(),
        component_version_id=None,
        status=InsightReportStatus.completed,
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: report)))

    async def deny(*_args):
        raise HTTPException(status_code=403, detail="Not an owner")

    monkeypatch.setattr(insights, "_authorize_component", deny)
    for handler in (insights.get_report, insights.export_report_html, insights.delete_report):
        with pytest.raises(HTTPException) as exc:
            await handler(str(report.id), db, SimpleNamespace(id=uuid.uuid4()))
        assert exc.value.status_code == 403
    assert db.execute.await_count == 3


@pytest.mark.asyncio
async def test_agent_generator_keeps_same_id_users_separate(monkeypatch):
    from services.insights import generator

    alice = SessionKey("default", "alice", "pi", "same-id")
    bob = SessionKey("default", "bob", "pi", "same-id")
    # The pipeline's real aggregation is exercised with two distinct selected metas.
    from services.insights.session_meta_extractor import extract_session_meta

    raw = '{"type":"message","timestamp":"2026-01-01T00:00:00Z","message":{"role":"user","content":"hello"}}'
    metas = [extract_session_meta("same-id", [raw]), extract_session_meta("same-id", [raw])]
    for meta in metas:
        meta.update(credits=0, harness="pi", layer_hash="", agent_version="")
    metas[0].update(session_key=alice, user_id="alice")
    metas[1].update(session_key=bob, user_id="bob")
    cache = AsyncMock(return_value={alice: {"goal_categories": ["fix_bug"]}, bob: {"goal_categories": ["write_tests"]}})
    monkeypatch.setattr(generator, "extract_all_session_metas", AsyncMock(return_value=metas))
    monkeypatch.setattr(generator, "load_cached_facets_batch", cache)
    monkeypatch.setattr(generator, "generate_sections", AsyncMock(return_value={"ok": True}))
    report = await generator.generate_report_content(
        agent_name="test",
        agent_id="id",
        period_start="2026-01-01",
        period_end="2026-01-02",
        db=SimpleNamespace(),
    )
    assert set(cache.await_args.args[0]) == {alice, bob}
    assert report["sessions_analyzed"] == 2
    assert report["facets_summary"]["sessions_with_facets"] == 2


@pytest.mark.asyncio
async def test_component_export_escapes_saved_name_and_never_fetches_transcripts(monkeypatch):
    from datetime import UTC, datetime

    from api.routes import insights
    from models.insight_report import InsightReportStatus

    now = datetime.now(UTC)
    report = SimpleNamespace(
        id=uuid.uuid4(),
        subject_type="component",
        component_type="mcp",
        component_id=uuid.uuid4(),
        component_version_id=None,
        component_name="<script>alert(1)</script>",
        status=InsightReportStatus.completed,
        metrics={"observed_calls": 1},
        narrative={
            "summary": "<img src=x onerror=alert(1)>",
            "component_analysis": {
                "state": "assessed",
                "findings": [{"insight": "<script>bad</script>"}],
                "evidence": {"s0-goal": "<img src=x>"},
            },
        },
        coverage={"reasons": []},
        period_start=now,
        period_end=now,
    )
    monkeypatch.setattr(insights, "_authorize_component", AsyncMock(return_value=(object(), object(), None)))
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: report)))
    response = await insights.export_report_html(str(report.id), db, SimpleNamespace(id=uuid.uuid4()))
    assert "<script>" not in response.body.decode()
    assert "<img" not in response.body.decode()
    assert "&lt;script&gt;" in response.body.decode()
    assert "s0-goal" not in response.body.decode()  # legacy prompt evidence is suppressed
    assert "bad" not in response.body.decode()


def test_legacy_component_analysis_is_removed_before_delivery():
    from api.routes.insights import _safe_component_narrative

    old = SimpleNamespace(
        narrative={
            "summary": "safe counts",
            "component_analysis": {
                "version": 2,
                "evidence": {"s0-goal": "private user text"},
            },
        }
    )
    assert _safe_component_narrative(old) == {"summary": "safe counts"}
    current = SimpleNamespace(
        narrative={
            "component_analysis": {
                "version": 3,
                "evidence": {
                    "s0-call0": "search (result: unknown)",
                },
            }
        }
    )
    assert _safe_component_narrative(current) == current.narrative


@pytest.mark.asyncio
async def test_component_report_history_uses_stable_scoped_keyset(monkeypatch):
    from datetime import UTC, datetime

    from api.routes import insights

    listing_id = uuid.uuid4()
    first_id = uuid.uuid4()
    when = datetime.now(UTC)
    monkeypatch.setattr(
        insights,
        "_authorize_component",
        AsyncMock(
            return_value=(
                SimpleNamespace(id=listing_id),
                object(),
                None,
            )
        ),
    )
    row = SimpleNamespace(id=first_id, created_at=when)
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [row]),
            )
        )
    )
    monkeypatch.setattr(insights.InsightReportListItem, "model_validate", lambda r: r)
    await insights.list_component_reports(
        "mcp",
        "listing",
        db,
        SimpleNamespace(),
        before_created_at=when,
        before_id=first_id,
        limit=20,
    )
    sql = str(db.execute.await_args.args[0].compile(compile_kwargs={"literal_binds": True}))
    assert listing_id.hex in sql and "created_at <" in sql and "insight_reports.id <" in sql
    assert "ORDER BY insight_reports.created_at DESC, insight_reports.id DESC" in sql
    assert "LIMIT 20" in sql
    from fastapi import HTTPException

    with pytest.raises(HTTPException, match="report cursor"):
        await insights.list_component_reports(
            "mcp",
            "listing",
            db,
            SimpleNamespace(),
            before_created_at=when,
            before_id=None,
            limit=20,
        )


@pytest.mark.asyncio
async def test_queued_component_job_checks_team_visibility_after_enqueue(monkeypatch):
    from fastapi import HTTPException

    from api import deps
    from models.user import UserRole
    from services.insights.batch import _authorize_component_report_job

    owner = SimpleNamespace(id=uuid.uuid4(), role=UserRole.user)
    team_id, listing_id = uuid.uuid4(), uuid.uuid4()
    listing = SimpleNamespace(
        id=listing_id,
        submitted_by=uuid.uuid4(),
        co_authors=[owner.id],
        is_private=True,
        team_id=team_id,
        namespace="private",
        slug="probe",
    )
    report = SimpleNamespace(
        triggered_by=owner.id,
        component_id=listing_id,
        component_type="mcp",
        component_version_id=None,
        component_version=None,
    )
    # In a real enqueue this co-author was a team member. At execution time
    # permission is still "owner", but membership has been revoked.
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[owner, None]))
    monkeypatch.setattr(deps, "resolve_listing", AsyncMock(return_value=listing))
    assert deps.get_effective_component_permission(listing, owner) == "owner"
    with pytest.raises(HTTPException) as exc:
        await _authorize_component_report_job(db, report)
    assert exc.value.status_code == 404
    assert db.scalar.await_count == 2  # requester, then current team membership

    # A restored membership permits the same job without changing ownership.
    db.scalar = AsyncMock(side_effect=[owner, uuid.uuid4()])
    await _authorize_component_report_job(db, report)


def test_component_sections_never_rewrite_observability_or_claim_unobserved_use():
    from services.insights.sections import generate_component_sections

    summary = {"present_sessions": 3, "observed_calls": 0, "activation_actions": {"context_sessions": 1}}
    coverage = {
        "attribution_state": "attribution_not_possible",
        "reasons": ["projection_pending_sessions"],
        "calls": {"collision_calls": 2},
        "projection": {"projection_complete_sessions": 0},
        "observed_sessions": 0,
        "limitations": ["historical_final_push_unproven"],
    }
    narrative = generate_component_sections(summary, coverage)
    assert narrative["evidence"]["activated"]["context_sessions"] == 1
    assert narrative["evidence"]["observed"] == 0
    assert narrative["evidence"]["not_observed"]["processed_present_sessions_without_attributed_calls"] == 0
    assert narrative["evidence"]["cohort_collisions"] == 2
    assert narrative["synthesis"]["coverage"]["attribution_state"] == "attribution_not_possible"
    assert "unused" not in str(narrative).lower()


@pytest.mark.asyncio
async def test_worker_does_not_read_activity_after_team_visibility_revoked(monkeypatch):
    from datetime import UTC, datetime, timedelta

    from api import deps
    from models.insight_report import InsightReportStatus
    from models.user import UserRole
    from services.insights import batch, component_report

    owner = SimpleNamespace(id=uuid.uuid4(), role=UserRole.user)
    listing_id = uuid.uuid4()
    listing = SimpleNamespace(
        id=listing_id,
        submitted_by=uuid.uuid4(),
        co_authors=[owner.id],
        is_private=True,
        team_id=uuid.uuid4(),
        namespace="private",
        slug="probe",
    )
    monkeypatch.setattr(deps, "resolve_listing", AsyncMock(return_value=listing))
    now = datetime.now(UTC)
    report = SimpleNamespace(
        id=uuid.uuid4(),
        subject_type="component",
        component_type="mcp",
        component_id=listing_id,
        component_version_id=None,
        component_version=None,
        triggered_by=owner.id,
        status=InsightReportStatus.pending,
        started_at=now,
        period_start=now - timedelta(days=1),
        period_end=now,
        progress_phase="queued",
        error_message=None,
    )

    class Session:
        execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: report))
        scalar = AsyncMock(side_effect=[owner, None])  # requester exists, team membership revoked
        commit = AsyncMock()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    session = Session()
    monkeypatch.setattr(batch, "async_session", lambda: session)
    monkeypatch.setattr(batch, "_reap_stale_reports", AsyncMock(return_value=0))
    read = AsyncMock()
    monkeypatch.setattr(component_report, "generate_component_content", read)
    await batch.run_single_report(str(report.id))
    assert report.status == InsightReportStatus.failed
    read.assert_not_awaited()


@pytest.mark.asyncio
async def test_skill_report_generation_uses_skill_evidence_never_mcp_calls(monkeypatch):
    from api.routes import insights
    from services.component_activity import skill_queries
    from services.component_activity.skill_queries import build_skill_coverage

    listing_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4())
    added: list = []

    async def flush():
        for report in added:
            report.id = report.id or uuid.uuid4()
            report.created_at = report.started_at
            for column in ("sessions_analyzed", "progress_current", "progress_total", "progress_percent"):
                setattr(report, column, getattr(report, column) or 0)  # server defaults on insert

    db = SimpleNamespace(add=added.append, flush=flush, commit=AsyncMock())
    ref = SimpleNamespace(component_version_id=None, qualified_name="team/review")
    monkeypatch.setattr(
        insights, "_authorize_component", AsyncMock(return_value=(SimpleNamespace(id=listing_id), ref, None))
    )
    mcp = AsyncMock()
    monkeypatch.setattr(insights, "activity_summary", mcp)
    coverage = build_skill_coverage(
        {"present_sessions": 2}, {"projection_complete_sessions": 2, "observed_sessions": 1}, 515
    )
    skill = AsyncMock(return_value={"present_sessions": 2, "coverage": coverage})
    monkeypatch.setattr(skill_queries, "skill_activity_summary", skill)
    pool = SimpleNamespace(enqueue_job=AsyncMock())
    monkeypatch.setattr(insights, "_get_arq_pool", AsyncMock(return_value=pool))

    item = await insights.generate_component_insight("skill", "team/review", None, db, user)
    assert item.component_type == "skill" and item.status == "pending"
    assert (added[0].component_type, added[0].coverage["attribution_state"]) == ("skill", "observed")
    assert "evidence" in added[0].coverage and "calls" not in added[0].coverage
    skill.assert_awaited_once()
    mcp.assert_not_awaited()
    pool.enqueue_job.assert_awaited_once_with("generate_insight_report", str(added[0].id))
