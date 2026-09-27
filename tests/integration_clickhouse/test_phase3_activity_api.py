# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""Opt-in Phase 3 observability-service proof on the isolated 007/028 databases.

Uses only observal_phase22_ci (:18123) and observal_phase14_ci (:15432); never
the sample. Synthetic, uniquely keyed rows are retained after the run.
"""

from __future__ import annotations

import json
import os
import statistics
import time
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import pytest
import pytest_asyncio
from tests.integration_clickhouse.test_phase25_activity import (
    _ALIAS_A,
    _ALIAS_B,
    _FIXTURE,
    _HASH,
    _call,
    _mapping,
    _record,
    _source,
)

import services.clickhouse.client as clickhouse
from database import engine
from services.component_activity import projector, queries
from services.projection_generation import next_projection_generation

_URL = os.getenv("OBSERVAL_CH_PHASE25_URL")
pytestmark = pytest.mark.skipif(not _URL, reason="opt-in isolated Phase 3 ClickHouse API proof")


@pytest.fixture(scope="module", autouse=True)
def _isolated_databases_only():
    if not _URL:
        pytest.skip("isolated activity proof not configured")
    target = urlparse(_URL)
    pg = urlparse(os.environ.get("DATABASE_URL", ""))
    assert target.hostname == "127.0.0.1" and target.port == 18123 and target.path == "/observal_phase22_ci"
    assert clickhouse.CLICKHOUSE_DB == "observal_phase22_ci" and clickhouse.CLICKHOUSE_USER == "proof"
    assert pg.hostname == "127.0.0.1" and pg.port == 15432 and pg.path == "/observal_phase14_ci"


@pytest.fixture(autouse=True)
def _fresh_http_client(monkeypatch):
    monkeypatch.setattr(clickhouse, "_client", None)


@pytest_asyncio.fixture(autouse=True)
async def _close_pg_pool():
    yield
    await engine.dispose()


def _ts(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


async def _insert(table: str, rows: list[dict]) -> None:
    assert table in {
        "session_events",
        "session_stats_agg",
        "session_capabilities",
        "component_activity_publications",
        "component_activity",
    }
    response = await clickhouse._query(
        f"INSERT INTO {table} FORMAT JSONEachRow", data="\n".join(json.dumps(row) for row in rows)
    )
    response.raise_for_status()


def _stats(project: str, user: str, session: str, when: datetime, harness: str = "claude-code") -> dict:
    return {
        "project_id": project,
        "user_id": user,
        "harness": harness,
        "session_id": session,
        "layer_hash": _HASH,
        "first_event_time": _ts(when),
        "last_event_time": _ts(when),
    }


def _period() -> tuple[datetime, datetime]:
    end = datetime.now(UTC) + timedelta(minutes=1)
    return end - timedelta(days=1), end


@pytest.mark.asyncio
async def test_fixture_counts_match_projection_and_coverage_isolates_projects_and_users():
    project, other_project = "phase3-" + uuid.uuid4().hex, "phase3o-" + uuid.uuid4().hex
    owner, stranger, session = "fixture-owner", "stranger", "fixture-session"
    when = datetime.now(UTC) - timedelta(hours=1)
    first_id, second_id = str(uuid.uuid4()), str(uuid.uuid4())
    constructed = _record([{"type": "text"}, _call(_ALIAS_A, "c-a"), _call(_ALIAS_B, "c-b"), _call(_ALIAS_A, "c-c")])
    lines = [*_FIXTURE.read_text().splitlines(), constructed]
    aliases = [(_ALIAS_A, first_id, "verified"), (_ALIAS_B, second_id, "verified")]
    for scoped_project in (project, other_project):
        await _insert(
            "session_events", [_source(scoped_project, owner, session, i, raw) for i, raw in enumerate(lines)]
        )
        await _mapping(scoped_project, owner, aliases)
    # Two further present sessions: one never projected (pending), one failed only.
    for extra in ("pending-session", "failed-session"):
        await _insert("session_events", [_source(project, owner, extra, 0, _record([_call(_ALIAS_A, extra)]))])
    generation = await next_projection_generation()
    await _insert(
        "component_activity_publications",
        [
            {
                "project_id": project,
                "user_id": owner,
                "harness": "claude-code",
                "session_id": "failed-session",
                "projection_version": projector.publication_version(),
                "projection_generation": generation,
                "status": "failed",
            }
        ],
    )
    # The same session id and hash for another user (no mapping) and another project.
    await _insert(
        "session_stats_agg",
        [
            _stats(project, owner, session, when),
            _stats(project, owner, "pending-session", when),
            _stats(project, owner, "failed-session", when),
            _stats(project, stranger, session, when),
            _stats(other_project, owner, session, when),
        ],
    )
    projected = await projector.project_session_activity(project, owner, "claude-code", session)
    assert projected["status"] == "complete" and projected["attributed_count"] == 6
    await _insert(
        "session_capabilities",
        [
            {
                "project_id": project,
                "user_id": owner,
                "harness": "claude-code",
                "session_id": session,
                "kind": "mcp",
                "component_id": first_id,
                "version": version,
                "mode": mode,
                "identifier": f"version-{version}",
                "used_at": _ts(when),
            }
            for version, mode in (("1.0.0", "context"), ("2.0.0", "next-session"))
        ],
    )
    assert (await projector.project_session_activity(other_project, owner, "claude-code", session))[
        "status"
    ] == "complete"

    period = _period()
    summary = await queries.activity_summary(project, "mcp", first_id, None, period)
    coverage = summary["coverage"]
    # Phase 2 projection attributed offsets 5, 8, 10:c-a and 10:c-c to first_id.
    assert (summary["present_sessions"], summary["present_users"]) == (3, 1)
    assert (summary["observed_sessions"], summary["observed_calls"]) == (1, 4)
    assert summary["result_states"] == {"success": 0, "error": 1, "unknown": 3}
    assert summary["harness_distribution"] == {"claude-code": 3}
    assert summary["activation_actions"]["scope"] == "component"
    assert summary["activation_actions"]["next_session_sessions"] == 1
    version_summary = await queries.activity_summary(
        project,
        "mcp",
        first_id,
        "22222222-2222-4222-8222-222222222222",
        period,
        component_version="1.0.0",
    )
    assert version_summary["activation_actions"]["scope"] == "component_version"
    assert version_summary["activation_actions"]["context_sessions"] == 1
    assert version_summary["activation_actions"]["next_session_sessions"] == 0
    projection = coverage.projection
    assert (
        projection.supported_present_sessions,
        projection.projection_complete_sessions,
        projection.projection_pending_sessions,
        projection.projection_failed_sessions,
        projection.projection_stale_sessions,
    ) == (3, 1, 1, 1, 0)
    assert (coverage.calls.candidate_calls, coverage.calls.attributed_calls, coverage.calls.collision_calls) == (
        6,
        6,
        0,
    )
    assert coverage.usage_rate_denominator_sessions == 1 and coverage.attribution_state == "observed"
    assert {"projection_pending_sessions", "projection_failed_sessions"} <= set(coverage.reasons)
    second = await queries.activity_summary(project, "mcp", second_id, None, period)
    assert (second["observed_calls"], second["result_states"]["unknown"]) == (2, 2)
    other = await queries.activity_summary(other_project, "mcp", first_id, None, period)
    assert (other["present_sessions"], other["observed_calls"]) == (1, 4)  # no cross-project merge
    unknown_version = await queries.activity_summary(
        project, "mcp", first_id, str(uuid.uuid4()), period, component_version="9.9.9"
    )
    assert unknown_version["present_sessions"] == 0
    assert unknown_version["coverage"].attribution_state == "attribution_not_possible"

    scope = (project, "fixture-owner", "mcp", first_id, "", "1")
    seen, cursor = [], None
    for _ in range(4):
        page, next_cursor = await queries.activity_sessions(
            project, "mcp", first_id, None, period, limit=2, cursor=cursor, cursor_scope=scope
        )
        seen.extend(page)
        if next_cursor is None:
            break
        pinned, cursor = queries.decode_cursor(next_cursor, scope=scope)
        period = (pinned - timedelta(days=1), pinned)
    assert sorted((s["user_id"], s["session_id"]) for s in seen) == [
        (owner, "failed-session"),
        (owner, "fixture-session"),
        (owner, "pending-session"),
    ]
    by_session = {s["session_id"]: s for s in seen}
    assert by_session["fixture-session"]["projection_state"] == "complete"
    assert by_session["fixture-session"]["source_state"] == "available"
    assert [
        (r["source_line_offset"], r["source_block_key"]) for r in by_session["fixture-session"]["source_references"]
    ] == [(5, "id:toolu_fixture_second"), (8, "id:toolu_fixture_error"), (10, "id:c-a"), (10, "id:c-c")]
    assert by_session["pending-session"]["projection_state"] == "pending"
    assert by_session["failed-session"]["projection_state"] == "failed"
    assert by_session["failed-session"]["observed_calls"] == 0


@pytest.mark.asyncio
async def test_latency_on_synthetic_isolated_dataset(capsys):
    project, owner = "phase3l-" + uuid.uuid4().hex, "latency-owner"
    component = str(uuid.uuid4())
    await _mapping(project, owner, [(_ALIAS_A, component, "verified")])
    now = datetime.now(UTC) - timedelta(hours=2)
    count = 3000
    version = projector.publication_version()
    generation = await next_projection_generation()
    stats, markers, activity = [], [], []
    for index in range(count):
        session = f"synthetic-{index:05d}"
        stats.append(_stats(project, owner, session, now + timedelta(seconds=index)))
        markers.append(
            {
                "project_id": project,
                "user_id": owner,
                "harness": "claude-code",
                "session_id": session,
                "projection_version": version,
                "projection_generation": generation + index,
                "status": "complete",
                "candidate_count": 1,
                "attributed_count": index % 2,
            }
        )
        if index % 2:
            activity.append(
                {
                    "project_id": project,
                    "user_id": owner,
                    "harness": "claude-code",
                    "session_id": session,
                    "projection_version": version,
                    "projection_generation": generation + index,
                    "source_line_offset": 0,
                    "source_block_key": "id:synthetic",
                    "layer_hash": _HASH,
                    "component_type": "mcp",
                    "component_id": component,
                    "component_version_id": "22222222-2222-4222-8222-222222222222",
                    "tool_name": f"mcp__{_ALIAS_A}__ping",
                    "event_time": _ts(now),
                    "result_state": "success",
                    "attribution_method": "verified_alias",
                    "matcher_version": projector.MATCHER_VERSION,
                    "extractor_version": 2,
                }
            )
    await _insert("session_stats_agg", stats)
    # One published session has a source row whose raw line is no longer
    # available (e.g. TTL); 2,999 others have no canonical source rows.
    await _insert("session_events", [_source(project, owner, "synthetic-00000", 0, "")])
    await _insert("component_activity_publications", markers)
    await _insert("component_activity", activity)
    period = _period()
    summary = await queries.activity_summary(project, "mcp", component, None, period)
    assert summary["present_sessions"] == count and summary["observed_sessions"] == count // 2
    assert summary["coverage"].projection.source_missing_sessions == count - 1
    assert summary["coverage"].projection.source_unavailable_sessions == 1
    assert summary["coverage"].projection.projection_complete_sessions == count

    def timed(runs: list[float], started: float) -> None:
        runs.append((time.perf_counter() - started) * 1000)

    summary_ms, page_ms = [], []
    for _ in range(5):
        started = time.perf_counter()
        await queries.activity_summary(project, "mcp", component, None, period)
        timed(summary_ms, started)
        started = time.perf_counter()
        page, _cursor = await queries.activity_sessions(
            project,
            "mcp",
            component,
            None,
            period,
            limit=100,
            cursor=None,
            cursor_scope=(project, owner, "mcp", component, "", "1"),
        )
        timed(page_ms, started)
        assert len(page) == 100
    with capsys.disabled():
        print(
            f"\nphase3 latency ({count} present sessions): summary median={statistics.median(summary_ms):.0f}ms "
            f"max={max(summary_ms):.0f}ms; sessions page(100) median={statistics.median(page_ms):.0f}ms "
            f"max={max(page_ms):.0f}ms"
        )
