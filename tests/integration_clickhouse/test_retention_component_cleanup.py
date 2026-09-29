# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Opt-in retention proof against a disposable, fully migrated ClickHouse DB.

Set OBSERVAL_CH_RETENTION_URL and CLICKHOUSE_URL to the same isolated local
retention_proof database. Never run this against a development sample database.
"""

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import pytest

from services.clickhouse import _query
from services.retention import _delete_batch, _purge_layer_orphans, _purge_session_facets, _purge_session_orphans

_URL = os.getenv("OBSERVAL_CH_RETENTION_URL")
_PG_URL = os.getenv("OBSERVAL_PG_RETENTION_URL")
pytestmark = pytest.mark.skipif(not _URL, reason="requires disposable retention_proof ClickHouse")


@pytest.fixture(autouse=True)
def _fresh_clickhouse_client(monkeypatch):
    import services.clickhouse.client as clickhouse

    monkeypatch.setattr(clickhouse, "_client", None)


def _ts(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")[:23]


async def _insert(table: str, rows: list[dict]) -> None:
    assert table in {
        "session_events",
        "session_stats_agg",
        "session_checkpoints",
        "session_capabilities",
        "layer_snapshots",
        "layer_components",
        "layer_component_extractions",
        "component_activity",
        "component_activity_publications",
    }
    response = await _query(f"INSERT INTO {table} FORMAT JSONEachRow", data="\n".join(json.dumps(row) for row in rows))
    response.raise_for_status()


async def _rows(table: str, project: str, fields: str) -> list[dict]:
    assert table in {
        "session_events",
        "session_stats_agg",
        "session_checkpoints",
        "session_capabilities",
        "layer_snapshots",
        "layer_components",
        "layer_component_extractions",
        "component_activity",
        "component_activity_publications",
    }
    response = await _query(
        f"SELECT {fields} FROM {table} WHERE project_id = {{pid:String}} FORMAT JSON",
        {"param_pid": project},
    )
    response.raise_for_status()
    return response.json()["data"]


@pytest.mark.asyncio
async def test_retention_scopes_session_and_shared_snapshot():
    assert _URL
    target = urlparse(_URL.replace("clickhouse://", "http://"))
    actual = urlparse(os.getenv("CLICKHOUSE_URL", "").replace("clickhouse://", "http://"))
    assert target.hostname == actual.hostname == "127.0.0.1"
    assert target.port == actual.port and target.path == actual.path == "/retention_proof"
    project = "retention-" + uuid.uuid4().hex
    other_project = "other-" + uuid.uuid4().hex
    old = datetime.now(UTC) - timedelta(days=40)
    recent = datetime.now(UTC) - timedelta(days=1)
    cutoff = _ts(datetime.now(UTC) - timedelta(days=20))
    shared = "v2_shared_proof"
    other = "v2_recent_proof"
    # Alice has an old Claude session and a retained one on the same layer;
    # her Pi session reuses the old Claude session ID. Bob has the same hash
    # and session ID but no retained source in this project.
    source = [
        (project, "alice", "claude-code", "same", shared, old),
        (project, "alice", "claude-code", "keep", shared, recent),
        (project, "alice", "pi", "same", other, recent),
        (project, "bob", "claude-code", "same", shared, old),
        (other_project, "bob", "claude-code", "same", shared, old),
    ]
    await _insert(
        "session_events",
        [
            {
                "project_id": p,
                "user_id": user,
                "harness": harness,
                "session_id": sid,
                "layer_hash": layer,
                "timestamp": _ts(when),
                "event_type": "tool_call",
                "line_offset": 0,
                "raw_line": "{}",
                "content_length": 2,
            }
            for p, user, harness, sid, layer, when in source
        ],
    )
    await _insert(
        "session_stats_agg",
        [
            {
                "project_id": p,
                "user_id": user,
                "harness": harness,
                "session_id": sid,
                "layer_hash": layer,
                "first_event_time": _ts(when),
                "last_event_time": _ts(when),
            }
            for p, user, harness, sid, layer, when in source
        ],
    )
    await _insert(
        "session_checkpoints",
        [
            {
                "project_id": p,
                "user_id": user,
                "harness": harness,
                "session_id": sid,
                "acknowledged_line": 0,
                "checkpoint_version": 1,
            }
            for p, user, harness, sid, _, _ in source
        ],
    )
    await _insert(
        "session_capabilities",
        [
            {
                "project_id": p,
                "user_id": user,
                "harness": harness,
                "session_id": sid,
                "kind": "mcp",
                "mode": "context",
                "source": "discover-cli",
                "confidence": "exact",
                "used_at": _ts(when),
            }
            for p, user, harness, sid, _, when in source
        ],
    )
    await _insert(
        "component_activity_publications",
        [
            {
                "project_id": p,
                "user_id": user,
                "harness": harness,
                "session_id": sid,
                "projection_version": 259,
                "projection_generation": 1,
                "status": "complete",
            }
            for p, user, harness, sid, _, _ in source
        ],
    )
    await _insert(
        "component_activity",
        [
            {
                "project_id": p,
                "user_id": user,
                "harness": harness,
                "session_id": sid,
                "projection_version": 259,
                "projection_generation": 1,
                "source_line_offset": 0,
                "source_block_key": "id:call",
                "source_line_hash": "proof",
                "layer_hash": layer,
                "component_type": "mcp",
                "component_id": "fixture",
                "component_version_id": "fixture",
                "tool_name": "fixture",
                "tool_use_id": "call",
                "event_time": _ts(when),
                "result_state": "unknown",
                "attribution_method": "verified_alias",
                "matcher_version": 3,
                "extractor_version": 1,
            }
            for p, user, harness, sid, layer, when in source
        ],
    )
    snapshots = [
        (project, "alice", shared, old),
        (project, "bob", shared, old),
        (project, "alice", other, recent),
        (project, "bob", other, recent),
        (other_project, "bob", shared, old),
    ]
    await _insert(
        "layer_snapshots",
        [
            {
                "project_id": p,
                "user_id": user,
                "hash": layer,
                "harness": "claude-code",
                "content": "{}",
                "uploaded_at": _ts(when),
                "file_count": 0,
                "total_size": 0,
            }
            for p, user, layer, when in snapshots
        ],
    )
    await _insert(
        "layer_components",
        [
            {
                "project_id": p,
                "user_id": user,
                "layer_hash": layer,
                "hash_schema_version": 2,
                "extractor_version": 1,
                "extraction_generation": 1,
                "occurrence_key": "fixture",
                "component_type": "mcp",
                "source": "standalone",
                "harness": "claude-code",
                "scope": "user",
                "parent_agent_id": "",
                "parent_agent_version": "",
                "raw_listing_id": "fixture",
                "raw_name": "fixture",
                "raw_version": "1.0.0",
                "qualified_name": "fixture/component",
                "local_name": "fixture",
                "component_id": "fixture",
                "component_version_id": "fixture",
                "identity_status": "resolved",
                "verification_status": "verified",
            }
            for p, user, layer, _ in snapshots
        ],
    )
    await _insert(
        "layer_component_extractions",
        [
            {
                "project_id": p,
                "user_id": user,
                "layer_hash": layer,
                "extractor_version": 1,
                "extraction_generation": 1,
                "status": "complete",
            }
            for p, user, layer, _ in snapshots
        ],
    )

    assert await _delete_batch("session_events", "timestamp", project, cutoff)
    await _purge_session_orphans(project, cutoff)
    await _purge_layer_orphans(project, cutoff)
    keep = {("alice", "claude-code", "keep"), ("alice", "pi", "same")}
    for table in (
        "session_events",
        "session_stats_agg",
        "session_checkpoints",
        "component_activity",
        "component_activity_publications",
        "session_capabilities",
    ):
        assert {
            (r["user_id"], r["harness"], r["session_id"])
            for r in await _rows(table, project, "user_id,harness,session_id")
        } == keep, table
    remaining = {("alice", shared), ("alice", other), ("bob", other)}
    for table, column in (
        ("layer_snapshots", "hash"),
        ("layer_components", "layer_hash"),
        ("layer_component_extractions", "layer_hash"),
    ):
        assert {(r["user_id"], r[column]) for r in await _rows(table, project, f"user_id,{column}")} == remaining
    assert {
        (r["user_id"], r["session_id"]) for r in await _rows("session_events", other_project, "user_id,session_id")
    } == {("bob", "same")}

    # Last reference expires; Alice's old shared snapshot is now removable,
    # while Bob's newly uploaded, never-used snapshot survives the time cutoff.
    response = await _query(
        "DELETE FROM session_events WHERE project_id = {pid:String} AND user_id = 'alice' "
        "AND harness = 'claude-code' AND session_id = 'keep' SETTINGS lightweight_deletes_sync = 1",
        {"param_pid": project},
        timeout=120,
    )
    response.raise_for_status()
    await _purge_session_orphans(project, cutoff)
    await _purge_layer_orphans(project, cutoff)
    assert {r["session_id"] for r in await _rows("component_activity", project, "session_id")} == {"same"}
    assert {r["user_id"] for r in await _rows("layer_snapshots", project, "user_id") if r["user_id"] == "bob"} == {
        "bob"
    }
    assert {r["hash"] for r in await _rows("layer_snapshots", project, "hash")} == {other}
    # Idempotent retry after any partial cleanup must not remove fresh data.
    await _purge_session_orphans(project, cutoff)
    await _purge_layer_orphans(project, cutoff)
    assert {r["hash"] for r in await _rows("layer_snapshots", project, "hash")} == {other}


@pytest.mark.skipif(not _PG_URL, reason="requires disposable retention_proof_pg PostgreSQL")
@pytest.mark.asyncio
async def test_scoped_facet_cache_cleanup_after_source_expiry():
    assert _URL and _PG_URL
    pg = urlparse(_PG_URL)
    ch = urlparse(_URL.replace("clickhouse://", "http://"))
    assert pg.hostname == ch.hostname == "127.0.0.1"
    assert pg.path == "/retention_proof_pg" and ch.path == "/retention_proof"
    assert os.getenv("DATABASE_URL") == _PG_URL

    from sqlalchemy import text

    from database import async_session, engine

    project = "retention-facets-" + uuid.uuid4().hex
    another_project = "retention-facets-other-" + uuid.uuid4().hex
    same_session = "shared-name"
    now = datetime.now(UTC)
    old = now - timedelta(days=40)
    cutoff = _ts(now - timedelta(days=20))
    facets = [
        (project, "alice", "claude-code", same_session),
        (project, "bob", "claude-code", same_session),
        (project, "alice", "pi", same_session),
        (another_project, "alice", "claude-code", same_session),
    ]
    async with engine.begin() as conn:
        # Minimal *disposable* table compatible with the current model. No
        # migration or schema operation on the existing development database.
        await conn.execute(
            text("""CREATE TABLE IF NOT EXISTS insight_session_facets (
            id UUID PRIMARY KEY, agent_id UUID, project_id VARCHAR(255) NOT NULL,
            user_id VARCHAR(255) NOT NULL, harness VARCHAR(100) NOT NULL,
            session_id TEXT NOT NULL, facet_version INTEGER NOT NULL DEFAULT 1,
            extracted_at TIMESTAMPTZ, model_used VARCHAR(255), facets JSON NOT NULL
        )""")
        )
    async with async_session() as db:
        for p, user, harness, sid in facets:
            await db.execute(
                text("""INSERT INTO insight_session_facets
                (id, project_id, user_id, harness, session_id, facets)
                VALUES (:id, :project, :user, :harness, :sid, '{}')"""),
                {"id": uuid.uuid4(), "project": p, "user": user, "harness": harness, "sid": sid},
            )
        await db.commit()
    # Retain Bob's same-ID session and Alice's same-ID Pi session; expire
    # Alice's Claude session only. The other project is never scanned.
    await _insert(
        "session_events",
        [
            {
                "project_id": p,
                "user_id": user,
                "harness": harness,
                "session_id": sid,
                "layer_hash": "v2_facet_proof",
                "timestamp": _ts(when),
                "event_type": "user_prompt",
                "line_offset": 0,
                "raw_line": "{}",
                "content_length": 2,
            }
            for p, user, harness, sid, when in (
                (project, "alice", "claude-code", same_session, old),
                (project, "bob", "claude-code", same_session, now),
                (project, "alice", "pi", same_session, now),
                (another_project, "alice", "claude-code", same_session, old),
            )
        ],
    )
    assert await _delete_batch("session_events", "timestamp", project, cutoff)
    assert await _purge_session_facets(project) == 1
    async with async_session() as db:
        remaining = (
            await db.execute(
                text("""SELECT project_id, user_id, harness, session_id
            FROM insight_session_facets WHERE project_id IN (:project, :other_project)"""),
                {"project": project, "other_project": another_project},
            )
        ).all()
    assert {tuple(row) for row in remaining} == set(facets) - {(project, "alice", "claude-code", same_session)}
    assert await _purge_session_facets(project) == 0
    await engine.dispose()


@pytest.mark.skipif(not _PG_URL, reason="requires disposable retention_proof_pg PostgreSQL")
@pytest.mark.asyncio
async def test_legacy_insight_meta_cleanup_is_conservative():
    assert _URL and _PG_URL and os.getenv("DATABASE_URL") == _PG_URL
    assert urlparse(_PG_URL).path == "/retention_proof_pg"
    from sqlalchemy import text

    from database import async_session, engine
    from services.retention import _purge_legacy_insight_meta

    now = datetime.now(UTC)
    old = now - timedelta(days=40)
    cutoff = _ts(now - timedelta(days=20))
    project = "retention-legacy-" + uuid.uuid4().hex
    shared, expired, fresh = (f"{name}-{uuid.uuid4().hex}" for name in ("shared", "expired", "fresh"))
    agent = uuid.uuid4()
    async with engine.begin() as conn:
        # Disposable, FK-free copies of the legacy tables' current shape.
        await conn.execute(
            text("""CREATE TABLE IF NOT EXISTS insight_session_meta (
            id UUID PRIMARY KEY, agent_id UUID NOT NULL, session_id TEXT NOT NULL,
            computed_at TIMESTAMPTZ, meta JSON NOT NULL)""")
        )
        await conn.execute(
            text("""CREATE TABLE IF NOT EXISTS insight_meta_cache (
            id UUID PRIMARY KEY, agent_id UUID NOT NULL, period_start VARCHAR(30) NOT NULL,
            period_end VARCHAR(30) NOT NULL, session_metas JSON NOT NULL,
            created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ)""")
        )
        await conn.execute(text("DELETE FROM insight_meta_cache"))
    async with async_session() as db:
        for sid, computed in ((shared, old), (expired, old), (fresh, now)):
            await db.execute(
                text("INSERT INTO insight_session_meta VALUES (:id, :agent, :sid, :at, '{}')"),
                {"id": uuid.uuid4(), "agent": agent, "sid": sid, "at": computed},
            )
        fresh_start = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        for start in (old.strftime("%Y-%m-%dT%H:%M:%SZ"), fresh_start, "garbage"):
            await db.execute(
                text("INSERT INTO insight_meta_cache VALUES (:id, :agent, :start, 'x', '{}', now(), now())"),
                {"id": uuid.uuid4(), "agent": agent, "start": start},
            )
        await db.commit()
    # The shared ID survives in ANOTHER user's and harness's session; the
    # legacy row has no scope, so it must be kept.
    await _insert(
        "session_events",
        [
            {
                "project_id": project,
                "user_id": "someone-else",
                "harness": "pi",
                "session_id": shared,
                "timestamp": _ts(now),
                "event_type": "user_prompt",
                "line_offset": 0,
                "raw_line": "{}",
                "content_length": 2,
            },
        ],
    )
    result = await _purge_legacy_insight_meta(cutoff)
    async with async_session() as db:
        left = {
            r[0]
            for r in (
                await db.execute(
                    text("SELECT session_id FROM insight_session_meta WHERE session_id IN (:a, :b, :c)"),
                    {"a": shared, "b": expired, "c": fresh},
                )
            ).all()
        }
        caches = {r[0] for r in (await db.execute(text("SELECT period_start FROM insight_meta_cache"))).all()}
    assert left == {shared, fresh}  # fresh: computed after the cutoff
    assert caches == {now.strftime("%Y-%m-%dT%H:%M:%SZ")}
    assert result == {"insight_session_meta": 1, "insight_meta_cache": 2}
    assert await _purge_legacy_insight_meta(cutoff) == {"insight_session_meta": 0, "insight_meta_cache": 0}
    await engine.dispose()
