# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""Opt-in canonical-source activity replay against the isolated 007/028 proof DBs.

Run only against observal_phase22_ci on the dedicated :18123 ClickHouse and
observal_phase14_ci on the dedicated :15432 PostgreSQL. No sample migrations,
deletion, real transcript content, credentials or original IDs enter this test.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import urlparse

import pytest
import pytest_asyncio
import xxhash

import services.clickhouse.client as clickhouse
from database import engine
from services.component_activity import projector
from services.layer_components import CURRENT_EXTRACTOR_VERSION
from services.projection_generation import next_projection_generation

_URL = os.getenv("OBSERVAL_CH_PHASE25_URL")
pytestmark = pytest.mark.skipif(not _URL, reason="opt-in isolated Phase 2.5 ClickHouse activity proof")
_HASH = "v2_" + "a" * 60
_ALIAS_A = "super-phase0-probe"
_ALIAS_B = "component-insights-phase0-phase0-probe"
_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/component_insights/claude_code/session.jsonl"


@pytest.fixture(scope="module", autouse=True)
def _isolated_databases_only():
    if not _URL:
        pytest.skip("isolated activity proof not configured")
    target = urlparse(_URL)
    pg = urlparse(os.environ.get("DATABASE_URL", ""))
    assert target.scheme == "http" and target.hostname == "127.0.0.1" and target.port == 18123
    assert target.path == "/observal_phase22_ci" and not target.query and not target.fragment
    assert clickhouse.CLICKHOUSE_HTTP == "http://127.0.0.1:18123"
    assert clickhouse.CLICKHOUSE_DB == "observal_phase22_ci" and clickhouse.CLICKHOUSE_USER == "proof"
    assert pg.hostname == "127.0.0.1" and pg.port == 15432 and pg.path == "/observal_phase14_ci"


@pytest.fixture(autouse=True)
def _fresh_http_client(monkeypatch):
    monkeypatch.setattr(clickhouse, "_client", None)


@pytest_asyncio.fixture(autouse=True)
async def _close_pg_pool():
    yield
    await engine.dispose()


async def _query(sql: str, params: dict | None = None) -> list[dict]:
    response = await clickhouse._query(sql + " FORMAT JSON", params)
    response.raise_for_status()
    return response.json()["data"]


async def _insert(table: str, rows: list[dict]) -> None:
    assert table in {"session_events", "layer_components", "layer_component_extractions", "layer_snapshots"}
    response = await clickhouse._query(
        f"INSERT INTO {table} FORMAT JSONEachRow", data="\n".join(json.dumps(row) for row in rows)
    )
    response.raise_for_status()


def _source(project: str, user: str, session: str, offset: int, record: str | dict, *, repaired: bool = False) -> dict:
    raw = record if isinstance(record, str) else json.dumps(record, separators=(",", ":"))
    now = datetime.now(UTC)
    return {
        "project_id": project,
        "user_id": user,
        "harness": "claude-code",
        "session_id": session,
        "line_offset": offset,
        "line_hash": xxhash.xxh128(raw.encode()).hexdigest(),
        "source_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "is_source_record": 1,
        "rendered": 1,
        "event_type": "tool_call",
        "layer_hash": _HASH,
        "raw_line": raw,
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        "ingested_at": (now + timedelta(days=2 if repaired else 1)).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
    }


def _call(alias: str, call_id: str) -> dict:
    return {"type": "tool_use", "name": f"mcp__{alias}__ping", "id": call_id, "input": {}}


def _record(blocks: list[dict]) -> dict:
    return {"type": "assistant", "timestamp": "2026-01-01T00:00:00Z", "message": {"content": blocks}}


async def _snapshot(project: str, user: str, *, layer_hash: str = _HASH, conflict: bool = False) -> None:
    """A mapping is valid only while the current scoped snapshot is non-conflicted."""
    content = {"pinned_versions": {"schema_version": 2, "agents": [], "standalone": []}}
    if conflict:
        content["identity_status"] = "identity_conflict"
    uploaded = datetime.now(UTC) + (timedelta(seconds=5) if conflict else timedelta())
    await _insert(
        "layer_snapshots",
        [
            {
                "project_id": project,
                "user_id": user,
                "hash": layer_hash,
                "harness": "claude-code",
                "content": json.dumps(content),
                "uploaded_at": uploaded.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            }
        ],
    )


async def _mapping(project: str, user: str, aliases: list[tuple[str, str, str]], *, layer_hash: str = _HASH) -> int:
    await _snapshot(project, user, layer_hash=layer_hash)
    generation = await next_projection_generation()
    if aliases:
        await _insert(
            "layer_components",
            [
                {
                    "project_id": project,
                    "user_id": user,
                    "layer_hash": layer_hash,
                    "hash_schema_version": 2,
                    "extractor_version": CURRENT_EXTRACTOR_VERSION,
                    "extraction_generation": generation,
                    "occurrence_key": f"safe-occurrence-{index}",
                    "component_type": "mcp",
                    "source": "standalone",
                    "harness": "claude-code",
                    "scope": "project",
                    "raw_listing_id": component_id,
                    "raw_name": "synthetic-probe",
                    "raw_version": "1.0.0",
                    "local_name": alias,
                    "component_id": component_id,
                    "component_version_id": "22222222-2222-4222-8222-222222222222",
                    "identity_status": "resolved",
                    "verification_status": verification,
                }
                for index, (alias, component_id, verification) in enumerate(aliases)
            ],
        )
    await _insert(
        "layer_component_extractions",
        [
            {
                "project_id": project,
                "user_id": user,
                "layer_hash": layer_hash,
                "extractor_version": CURRENT_EXTRACTOR_VERSION,
                "extraction_generation": generation,
                "status": "complete",
                "occurrence_count": len(aliases),
            }
        ],
    )
    return generation


async def _publication(project: str, user: str, session: str, version: int) -> tuple[dict, list[dict]]:
    params = {"param_project": project, "param_user": user, "param_session": session, "param_version": version}
    markers = await _query(
        "SELECT projection_generation, status, source_revision, candidate_count, attributed_count, "
        "collision_count, unmatched_count, unknown_result_count "
        "FROM component_activity_publications "
        "WHERE project_id = {project:String} AND user_id = {user:String} AND harness = 'claude-code' "
        "AND session_id = {session:String} AND projection_version = {version:UInt16} "
        "ORDER BY projection_generation, status",
        params,
    )
    complete_generations = [int(row["projection_generation"]) for row in markers if row["status"] == "complete"]
    if not complete_generations:
        return {}, markers
    latest = max(complete_generations)
    rows = await _query(
        "SELECT source_line_offset, source_block_key, component_id, result_state, matcher_version "
        "FROM component_activity FINAL "
        "WHERE project_id = {project:String} AND user_id = {user:String} AND harness = 'claude-code' "
        "AND session_id = {session:String} AND projection_version = {version:UInt16} "
        "AND projection_generation = {generation:UInt64} ORDER BY source_line_offset, source_block_key",
        params | {"param_generation": latest},
    )
    return {"generation": latest, "rows": rows}, markers


@pytest.mark.asyncio
async def test_real_derived_parallel_records_and_constructed_multiblock_publish_exact_counts():
    project, session, user = "phase25-" + uuid.uuid4().hex, "fixture-session", "fixture-owner"
    fixture = _FIXTURE.read_text().splitlines()
    assert len(fixture) == 10
    constructed = _record(
        [
            {"type": "text"},
            _call(_ALIAS_A, "constructed-a"),
            _call(_ALIAS_B, "constructed-b"),
            _call(_ALIAS_A, "constructed-c"),
        ]
    )
    await _insert(
        "session_events",
        [_source(project, user, session, index, raw) for index, raw in enumerate([*fixture, constructed])],
    )
    first_id, second_id = str(uuid.uuid4()), str(uuid.uuid4())
    await _mapping(project, user, [(_ALIAS_A, first_id, "verified"), (_ALIAS_B, second_id, "verified")])
    first = await projector.project_session_activity(project, user, "claude-code", session)
    assert first["status"] == "complete"
    assert (
        first["candidate_count"],
        first["attributed_count"],
        first["collision_count"],
        first["unmatched_count"],
        first["unknown_result_count"],
    ) == (6, 6, 0, 0, 5)
    published, markers = await _publication(project, user, session, projector.publication_version())
    assert len(markers) == 1 and published["generation"] == first["generation"]
    assert [(int(row["source_line_offset"]), row["source_block_key"]) for row in published["rows"]] == [
        (4, "id:toolu_fixture_first"),
        (5, "id:toolu_fixture_second"),
        (8, "id:toolu_fixture_error"),
        (10, "id:constructed-a"),
        (10, "id:constructed-b"),
        (10, "id:constructed-c"),
    ]
    assert [row["result_state"] for row in published["rows"]] == [
        "unknown",
        "unknown",
        "error",
        "unknown",
        "unknown",
        "unknown",
    ]
    assert [row["component_id"] for row in published["rows"]] == [
        second_id,
        first_id,
        first_id,
        first_id,
        second_id,
        first_id,
    ]
    assert (await projector.project_session_activity(project, user, "claude-code", session))[
        "status"
    ] == "already_complete"
    forced = await projector.project_session_activity(project, user, "claude-code", session, force=True)
    assert forced["status"] == "complete" and forced["generation"] > first["generation"]
    optimized = await clickhouse._query("OPTIMIZE TABLE component_activity FINAL")
    optimized.raise_for_status()
    republished, _ = await _publication(project, user, session, projector.publication_version())
    assert [(row["source_line_offset"], row["source_block_key"]) for row in republished["rows"]] == [
        (row["source_line_offset"], row["source_block_key"]) for row in published["rows"]
    ]
    assert republished["generation"] == forced["generation"] and len(republished["rows"]) == 6
    # Identical session ID/hash in another user's source cannot inherit this mapping.
    await _insert("session_events", [_source(project, "other", session, 0, _record([_call(_ALIAS_A, "other")]))])
    assert (await projector.project_session_activity(project, "other", "claude-code", session))[
        "status"
    ] == "pending_mapping"
    assert (await _publication(project, "other", session, projector.publication_version()))[0] == {}


@pytest.mark.asyncio
async def test_collision_failed_attempt_and_source_rewind_never_leave_stale_published_positives(monkeypatch):
    project, session, user = "phase25-" + uuid.uuid4().hex, "rebuild", "fixture-owner"
    first_id = str(uuid.uuid4())
    await _insert("session_events", [_source(project, user, session, 0, _record([_call(_ALIAS_A, "first")]))])
    await _mapping(project, user, [(_ALIAS_A, first_id, "verified")])
    first = await projector.project_session_activity(project, user, "claude-code", session)
    assert first["attributed_count"] == 1
    await _mapping(project, user, [(_ALIAS_A, first_id, "verified"), (_ALIAS_A, str(uuid.uuid4()), "unverified")])
    original_check = projector._inputs_unchanged
    monkeypatch.setattr(projector, "_inputs_unchanged", AsyncMock(return_value=False))
    with pytest.raises(RuntimeError, match="Canonical source or published layer mapping changed"):
        await projector.project_session_activity(project, user, "claude-code", session)
    previous, markers = await _publication(project, user, session, projector.publication_version())
    assert [marker["status"] for marker in markers] == ["complete", "failed"]
    assert previous["generation"] == first["generation"] and len(previous["rows"]) == 1
    monkeypatch.setattr(projector, "_inputs_unchanged", original_check)
    collision = await projector.project_session_activity(project, user, "claude-code", session)
    assert collision["status"] == "complete" and collision["collision_count"] == 1
    assert (collision["attributed_count"], collision["candidate_count"]) == (0, 1)
    published, _ = await _publication(project, user, session, projector.publication_version())
    assert published["generation"] == collision["generation"] and published["rows"] == []
    # Simulate the NEXT matcher revision, relative to the real current one.
    monkeypatch.setattr(projector, "MATCHER_VERSION", projector.MATCHER_VERSION + 1)
    next_version = projector.publication_version()
    bumped = await projector.project_session_activity(project, user, "claude-code", session)
    assert bumped["status"] == "complete" and bumped["publication_version"] == next_version
    assert (await _publication(project, user, session, next_version))[0]["rows"] == []
    repaired = _source(project, user, session, 0, _record([{"type": "text"}]), repaired=True)
    await _insert("session_events", [repaired])
    zero = await projector.project_session_activity(project, user, "claude-code", session)
    assert zero["status"] == "complete" and zero["candidate_count"] == 0
    assert zero["source_revision"] != bumped["source_revision"]
    assert (await _publication(project, user, session, next_version))[0]["rows"] == []


@pytest.mark.asyncio
async def test_late_mapping_retry_zero_call_and_version_bump_are_not_unknown_zero(monkeypatch):
    project, user = "phase25-" + uuid.uuid4().hex, "fixture-owner"
    session = "late-mapping"
    await _insert("session_events", [_source(project, user, session, 0, _record([_call(_ALIAS_A, "late")]))])
    pending = await projector.project_session_activity(project, user, "claude-code", session)
    assert pending["status"] == "pending_mapping" and pending["candidate_count"] == 1
    assert (await _publication(project, user, session, projector.publication_version())) == ({}, [])
    await _mapping(project, user, [(_ALIAS_A, str(uuid.uuid4()), "verified")])
    accepted = await projector.project_session_activity(project, user, "claude-code", session)
    assert accepted["status"] == "complete" and accepted["attributed_count"] == 1
    empty = "supported-zero-call"
    await _insert("session_events", [_source(project, user, empty, 0, _record([{"type": "text"}]))])
    completed = await projector.project_session_activity(project, user, "claude-code", empty)
    assert completed["status"] == "complete" and completed["candidate_count"] == completed["attributed_count"] == 0
    assert (await _publication(project, user, empty, projector.publication_version()))[0]["rows"] == []
    # Simulate the NEXT matcher revision, relative to the real current one.
    monkeypatch.setattr(projector, "MATCHER_VERSION", projector.MATCHER_VERSION + 1)
    next_version = projector.publication_version()
    revised = await projector.project_session_activity(project, user, "claude-code", empty)
    assert revised["status"] == "complete" and revised["generation"] > completed["generation"]
    assert revised["publication_version"] == next_version
    assert len((await _publication(project, user, empty, next_version))[1]) == 1
    # A harness without a verified invocation extractor is unsupported, never zero.
    from observal_shared.harness_registry import HARNESS_REGISTRY

    unsupported = next(
        name for name, entry in sorted(HARNESS_REGISTRY.items()) if not entry.get("invocation_extractor")
    )
    assert (await projector.project_session_activity(project, user, unsupported, empty))["status"] == "unsupported"


@pytest.mark.asyncio
async def test_later_snapshot_conflict_invalidates_earlier_complete_mapping():
    project, user, session = "phase25-" + uuid.uuid4().hex, "fixture-owner", "conflict-after-complete"
    await _insert("session_events", [_source(project, user, session, 0, _record([_call(_ALIAS_A, "c")]))])
    await _mapping(project, user, [(_ALIAS_A, str(uuid.uuid4()), "verified")])
    first = await projector.project_session_activity(project, user, "claude-code", session)
    assert first["status"] == "complete" and first["attributed_count"] == 1
    # A later conflicting upload is stored, but its re-extraction never ran.
    await _snapshot(project, user, conflict=True)
    replay = await projector.project_session_activity(project, user, "claude-code", session)
    assert replay["status"] == "identity_conflict" and replay["generation"] > first["generation"]
    assert (replay["attributed_count"], replay["candidate_count"], replay["unmatched_count"]) == (0, 1, 1)
    published, markers = await _publication(project, user, session, projector.publication_version())
    # The earlier positive row is no longer in the latest complete generation.
    assert published["generation"] == replay["generation"] and published["rows"] == []
    assert [marker["status"] for marker in markers] == ["complete", "complete"]
