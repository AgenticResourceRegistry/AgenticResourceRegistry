# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""Real ClickHouse 26.6 proof against an already-migrated local sample database.

Run `make test-ch`. Only tables prefixed phase0_ci_ are created/written. This
suite never runs migrations, alters existing tables or deletes test data/tables.
Override OBSERVAL_CH_TEST_URL and OBSERVAL_CH_TEST_PASSWORD for a different
*local* sample database; remote hosts and database names other than `observal`
are rejected to avoid accidentally writing production data. Default `make test`
excludes this suite. Test tables remain for explicit later cleanup by the owner.
"""

from __future__ import annotations

import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

import pytest

from services.clickhouse.migrations import BASELINE_TABLES, MIGRATIONS_DIR, _split_sql

URL = os.getenv("OBSERVAL_CH_TEST_URL")
pytestmark = pytest.mark.skipif(not URL, reason="opt-in: set OBSERVAL_CH_TEST_URL (or run make test-ch)")
PREFIX = "phase0_ci_"
DDL = Path(__file__).resolve().parents[1] / "fixtures" / "component_insights" / "clickhouse" / "projection_tables.sql"


def _http(sql: str) -> str:
    assert URL is not None
    parsed = urlparse(URL)
    assert parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
    assert parsed.path == "/observal" and not parsed.query and not parsed.fragment
    params = urlencode(
        {
            "database": "observal",
            "user": os.getenv("OBSERVAL_CH_TEST_USER", "default"),
            "password": os.getenv("OBSERVAL_CH_TEST_PASSWORD", "clickhouse"),
            "wait_for_async_insert": "1",
        }
    )
    endpoint = f"http://{parsed.hostname}:{parsed.port or 8123}/?{params}"
    # Do not print HTTP errors: they may contain connection parameters. The test SQL
    # and generated fixture IDs are non-sensitive and may be printed by pytest.
    try:
        with urlopen(Request(endpoint, data=sql.encode(), method="POST"), timeout=45) as response:
            return response.read().decode()
    except Exception as exc:
        raise AssertionError(f"local ClickHouse query failed ({type(exc).__name__})") from None


def _rows(sql: str) -> list[dict]:
    return [json.loads(line) for line in _http(sql + " FORMAT JSONEachRow").splitlines() if line]


def _insert(table: str, rows: list[dict]) -> None:
    assert table.startswith(PREFIX) and rows
    _http(f"INSERT INTO {table} FORMAT JSONEachRow\n" + "\n".join(json.dumps(row) for row in rows))


@pytest.fixture(scope="session", autouse=True)
def _existing_migrated_sample_and_test_tables():
    if not URL:
        pytest.skip("opt-in ClickHouse suite")
    assert _rows("SELECT currentDatabase() AS db")[0]["db"] == "observal"
    versions = {r["version"] for r in _rows("SELECT version FROM clickhouse_schema_migrations")}
    expected = {p.stem for p in MIGRATIONS_DIR.glob("*.sql") if p.stem < "006_"}
    assert expected <= versions  # Phase 0 sample is NOT a target for new production migrations.
    assert int(_rows("SELECT count() AS n FROM session_events")[0]["n"]) > 0  # existing sample, read-only
    tables = {r["name"] for r in _rows("SELECT name FROM system.tables WHERE database = 'observal'")}
    assert tables >= BASELINE_TABLES
    statements = _split_sql(DDL.read_text().replace("{prefix}", PREFIX))
    assert len(statements) == 4
    assert all(stmt.startswith("CREATE TABLE IF NOT EXISTS phase0_ci_") for stmt in statements)
    for statement in statements:
        _http(statement)


def _layer(project: str, user: str, layer_hash: str, generation: int, occurrence: str = "one", **extras) -> dict:
    return {
        "project_id": project,
        "user_id": user,
        "layer_hash": layer_hash,
        "hash_schema_version": 2,
        "extractor_version": 1,
        "extraction_generation": generation,
        "occurrence_key": occurrence,
        "component_type": "mcp",
        "source": "agent",
        "harness": "claude-code",
        "scope": "project",
        "parent_agent_id": "fixture-agent",
        "parent_agent_version": "1.0.0",
        "raw_listing_id": "fixture-component",
        "raw_name": "probe",
        "raw_version": "1.0.0",
        "qualified_name": "fixture/probe",
        "local_name": "fixture-probe",
        "component_id": "fixture-component",
        "component_version_id": "fixture-version",
        "identity_status": "resolved",
        "verification_status": "verified",
        **extras,
    }


def _marker(project: str, user: str, layer_hash: str, generation: int, status: str, version: int = 1) -> dict:
    return {
        "project_id": project,
        "user_id": user,
        "layer_hash": layer_hash,
        "extractor_version": version,
        "extraction_generation": generation,
        "status": status,
    }


def _published_layer(project: str, user: str, layer_hash: str, version: int = 1) -> list[dict]:
    key = f"project_id = '{project}' AND user_id = '{user}' AND layer_hash = '{layer_hash}' AND extractor_version = {version}"
    sql = f"""SELECT occurrence_key, component_id, extraction_generation
       FROM {PREFIX}layer_components FINAL WHERE {key} AND extraction_generation = (
         SELECT max(extraction_generation) FROM (
            SELECT extraction_generation FROM {PREFIX}layer_component_extractions
            WHERE {key} GROUP BY extraction_generation
            HAVING countIf(status = 'complete') > 0 AND countIf(status = 'failed') = 0
         )
       ) ORDER BY occurrence_key"""
    return _rows(sql)


def _published_membership(project: str, user: str, layer_hash: str) -> list[dict]:
    # A synthetic cohort row keeps the owner's existing session_stats_agg untouched.
    key = f"c.project_id = '{project}' AND c.user_id = '{user}' AND c.layer_hash = '{layer_hash}'"
    marker_key = f"project_id = '{project}' AND user_id = '{user}' AND layer_hash = '{layer_hash}'"
    return _rows(f"""SELECT s.session_id FROM
      (SELECT '{project}' AS project_id, '{user}' AS user_id, '{layer_hash}' AS layer_hash, 'fixture-session' AS session_id) AS s
      INNER JOIN {PREFIX}layer_components AS c FINAL
        ON c.project_id = s.project_id AND c.user_id = s.user_id AND c.layer_hash = s.layer_hash
      WHERE {key} AND c.component_id = 'fixture-component' AND c.extractor_version = 1
        AND c.extraction_generation = (
          SELECT max(extraction_generation) FROM (
            SELECT extraction_generation FROM {PREFIX}layer_component_extractions
            WHERE {marker_key} AND extractor_version = 1 GROUP BY extraction_generation
            HAVING countIf(status = 'complete') > 0 AND countIf(status = 'failed') = 0
          )
        )""")


def test_layer_published_generations_failed_retry_late_markers_and_zero_rows():
    project, owner, other = "phase0-" + uuid.uuid4().hex, "owner", "other"
    layer_hash = "v2-fixture"
    _insert(PREFIX + "layer_components", [_layer(project, owner, layer_hash, 1)])
    assert _published_layer(project, owner, layer_hash) == []  # Unpublished rows are invisible.
    assert _published_membership(project, owner, layer_hash) == []  # Even with a cohort row and FINAL.
    _insert(PREFIX + "layer_component_extractions", [_marker(project, owner, layer_hash, 1, "complete")])
    assert len(_published_layer(project, owner, layer_hash)) == 1
    assert [r["session_id"] for r in _published_membership(project, owner, layer_hash)] == ["fixture-session"]
    _insert(PREFIX + "layer_component_extractions", [_marker(project, owner, layer_hash, 2, "failed")])
    assert len(_published_layer(project, owner, layer_hash)) == 1  # Failure does not erase success.
    _insert(PREFIX + "layer_components", [_layer(project, owner, layer_hash, 3, "new")])
    assert len(_published_layer(project, owner, layer_hash)) == 1
    _insert(PREFIX + "layer_component_extractions", [_marker(project, owner, layer_hash, 3, "complete")])
    assert [r["occurrence_key"] for r in _published_layer(project, owner, layer_hash)] == ["new"]
    # An older, late marker cannot supersede generation 3.
    _insert(PREFIX + "layer_component_extractions", [_marker(project, owner, layer_hash, 2, "complete")])
    assert [r["occurrence_key"] for r in _published_layer(project, owner, layer_hash)] == ["new"]
    # A conflicting terminal marker for generation 2 is excluded entirely.
    assert _published_layer(project, owner, layer_hash, version=2) == []  # No old-version fallback.
    assert _published_layer(project, other, layer_hash) == []  # Same hash, different user.
    _insert(PREFIX + "layer_component_extractions", [_marker(project, owner, layer_hash, 4, "complete")])
    assert _published_layer(project, owner, layer_hash) == []  # Published, complete zero-row layer.
    assert _published_membership(project, owner, layer_hash) == []
    assert (
        _rows(
            f"SELECT count() AS n FROM {PREFIX}layer_component_extractions WHERE project_id = '{project}' AND extraction_generation = 4"
        )[0]["n"]
        == 1
    )
    assert _published_layer("different-project", owner, layer_hash) == []


def _activity(project: str, owner: str, session: str, generation: int, offset: int = 33, **extras) -> dict:
    return {
        "project_id": project,
        "user_id": owner,
        "harness": "claude-code",
        "session_id": session,
        "projection_version": 1,
        "projection_generation": generation,
        "source_line_offset": offset,
        "source_block_key": "toolu_fixture_first",
        "source_line_hash": "source-hash",
        "layer_hash": "v2-fixture",
        "component_type": "mcp",
        "component_id": "fixture-component",
        "component_version_id": "fixture-version",
        "tool_name": "mcp__fixture__ping",
        "tool_use_id": "toolu_fixture_first",
        "event_time": "2026-01-01 00:00:00.000",
        "result_state": "success",
        "attribution_method": "verified_alias",
        "matcher_version": 1,
        "extractor_version": 1,
        **extras,
    }


def _activity_marker(project: str, owner: str, session: str, generation: int, status: str) -> dict:
    return {
        "project_id": project,
        "user_id": owner,
        "harness": "claude-code",
        "session_id": session,
        "projection_version": 1,
        "projection_generation": generation,
        "status": status,
    }


def _published_activity(project: str, owner: str, session: str, version: int = 1) -> list[dict]:
    key = f"project_id = '{project}' AND user_id = '{owner}' AND harness = 'claude-code' AND session_id = '{session}' AND projection_version = {version}"
    return _rows(f"""SELECT source_line_offset, component_id, projection_generation
       FROM {PREFIX}component_activity FINAL WHERE {key} AND projection_generation = (
         SELECT max(projection_generation) FROM (
            SELECT projection_generation FROM {PREFIX}component_activity_publications
            WHERE {key} GROUP BY projection_generation
            HAVING countIf(status = 'complete') > 0 AND countIf(status = 'failed') = 0
         )
       ) ORDER BY source_line_offset""")


def test_activity_redelivery_replay_zero_and_failure_do_not_leave_stale_positive():
    project, owner, session = "phase0-" + uuid.uuid4().hex, "owner", "fixture-session"
    call = _activity(project, owner, session, 1)
    _insert(PREFIX + "component_activity", [call, call])
    assert _published_activity(project, owner, session) == []
    _insert(PREFIX + "component_activity_publications", [_activity_marker(project, owner, session, 1, "complete")])
    assert len(_published_activity(project, owner, session)) == 1  # FINAL deduplicates redelivery.
    _insert(PREFIX + "component_activity_publications", [_activity_marker(project, owner, session, 2, "failed")])
    assert len(_published_activity(project, owner, session)) == 1
    # Matcher/source-revision rebuild removes a former positive, without mutating old rows.
    _insert(PREFIX + "component_activity_publications", [_activity_marker(project, owner, session, 3, "complete")])
    assert _published_activity(project, owner, session) == []
    assert (
        _rows(
            f"SELECT count() AS n FROM {PREFIX}component_activity_publications WHERE project_id = '{project}' AND projection_generation = 3"
        )[0]["n"]
        == 1
    )  # Completed zero-call session, not unprocessed.
    assert _published_activity(project, "other-user", session) == []
    assert _published_activity(project, owner, session, version=2) == []


def test_concurrent_late_marker_and_atomic_insert_visibility():
    project, owner, layer_hash = "phase0-" + uuid.uuid4().hex, "owner", "v2-concurrency"
    # Two workers start together, but the lower generation deliberately publishes last.
    # Generations are assumed to have been allocated uniquely and monotonically before work starts.
    start, newer_published = Barrier(2), Event()

    def publish_old():
        start.wait(timeout=30)
        _insert(PREFIX + "layer_components", [_layer(project, owner, layer_hash, 1, "older")])
        assert newer_published.wait(timeout=30)
        _insert(PREFIX + "layer_component_extractions", [_marker(project, owner, layer_hash, 1, "complete")])

    def publish_new():
        start.wait(timeout=30)
        _insert(PREFIX + "layer_components", [_layer(project, owner, layer_hash, 2, "newer")])
        _insert(PREFIX + "layer_component_extractions", [_marker(project, owner, layer_hash, 2, "complete")])
        newer_published.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        old, new = pool.submit(publish_old), pool.submit(publish_new)
        old.result(timeout=60)
        new.result(timeout=60)
    assert [r["occurrence_key"] for r in _published_layer(project, owner, layer_hash)] == ["newer"]
    batch = [_layer(project, owner, layer_hash, 3, f"batch-{i:03d}") for i in range(100)]
    assert len(_published_layer(project, owner, layer_hash)) == 1
    _insert(PREFIX + "layer_components", batch[:50])
    assert len(_published_layer(project, owner, layer_hash)) == 1  # Partial batches stay hidden.
    _insert(PREFIX + "layer_components", batch[50:])
    assert len(_published_layer(project, owner, layer_hash)) == 1
    _insert(PREFIX + "layer_component_extractions", [_marker(project, owner, layer_hash, 3, "complete")])
    assert len(_published_layer(project, owner, layer_hash)) == 100
    assert len({r["occurrence_key"] for r in _published_layer(project, owner, layer_hash)}) == 100


def test_activity_concurrent_late_publication_and_multibatch_invisibility():
    project, owner, session = "phase0-" + uuid.uuid4().hex, "owner", "fixture-session"
    start, newer_published = Barrier(2), Event()

    def publish_old():
        start.wait(timeout=30)
        _insert(PREFIX + "component_activity", [_activity(project, owner, session, 1, offset=1)])
        assert newer_published.wait(timeout=30)
        _insert(PREFIX + "component_activity_publications", [_activity_marker(project, owner, session, 1, "complete")])

    def publish_new():
        start.wait(timeout=30)
        _insert(PREFIX + "component_activity", [_activity(project, owner, session, 2, offset=2)])
        _insert(PREFIX + "component_activity_publications", [_activity_marker(project, owner, session, 2, "complete")])
        newer_published.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        old, new = pool.submit(publish_old), pool.submit(publish_new)
        old.result(timeout=60)
        new.result(timeout=60)
    assert [r["source_line_offset"] for r in _published_activity(project, owner, session)] == [2]
    _insert(PREFIX + "component_activity_publications", [_activity_marker(project, owner, session, 3, "failed")])
    assert [r["source_line_offset"] for r in _published_activity(project, owner, session)] == [2]
    batch = [_activity(project, owner, session, 4, offset=i + 100) for i in range(100)]
    _insert(PREFIX + "component_activity", batch[:50])
    assert [r["source_line_offset"] for r in _published_activity(project, owner, session)] == [2]
    _insert(PREFIX + "component_activity", batch[50:])
    assert [r["source_line_offset"] for r in _published_activity(project, owner, session)] == [2]
    _insert(PREFIX + "component_activity_publications", [_activity_marker(project, owner, session, 4, "complete")])
    assert len(_published_activity(project, owner, session)) == 100
    assert len({r["source_line_offset"] for r in _published_activity(project, owner, session)}) == 100
