# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""Opt-in proof against *isolated* migration-runner database, never the sample.

Run with OBSERVAL_CH_PHASE14_URL=http://127.0.0.1:18123/observal_phase14_ci,
CLICKHOUSE_URL=clickhouse://proof:proof@127.0.0.1:18123/observal_phase14_ci,
and OBSERVAL_CH_PHASE14_USER/PASSWORD=proof. The database must already have
production migrations through 006 applied by the real migration runner.
No existing sample database/tables are altered. Generated project keys isolate
repeat runs; proof rows persist in the dedicated disposable test container.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

import pytest
import pytest_asyncio
from sqlalchemy import select

import services.clickhouse.client as clickhouse
from database import async_session, engine
from jobs.maintenance import backfill_layer_components
from models.mcp import ListingStatus, McpListing, McpVersion
from models.user import User
from observal_cli.layer import layer_hash_v2
from services.layer_components import CURRENT_EXTRACTOR_VERSION, queries
from services.layer_components.extractor import ensure_layer_components
from services.layer_components.queries import presence_cohort, presence_coverage

URL = os.getenv("OBSERVAL_CH_PHASE14_URL")
pytestmark = pytest.mark.skipif(not URL, reason="opt-in isolated CH migration proof")
COMPONENT = "11111111-1111-4111-8111-111111111111"
VERSION = "22222222-2222-4222-8222-222222222222"
OTHER_VERSION = "33333333-3333-4333-8333-333333333333"


def _http(sql: str) -> str:
    assert URL is not None
    parsed = urlparse(URL)
    client = urlparse(os.environ.get("CLICKHOUSE_URL", "").replace("clickhouse://", "http://"))
    assert parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
    assert parsed.path == "/observal_phase14_ci" and not parsed.query and not parsed.fragment
    assert client.hostname == parsed.hostname and client.port == parsed.port and client.path == parsed.path
    args = urlencode(
        {
            "database": "observal_phase14_ci",
            "user": os.getenv("OBSERVAL_CH_PHASE14_USER", "proof"),
            "password": os.getenv("OBSERVAL_CH_PHASE14_PASSWORD", "proof"),
        }
    )
    endpoint = f"http://{parsed.hostname}:{parsed.port or 18123}/?{args}"
    try:
        with urlopen(Request(endpoint, data=sql.encode(), method="POST"), timeout=30) as response:
            return response.read().decode()
    except Exception as error:
        raise AssertionError(f"isolated ClickHouse query failed ({type(error).__name__})") from None


def _rows(sql: str) -> list[dict]:
    return [json.loads(line) for line in _http(sql + " FORMAT JSONEachRow").splitlines() if line]


def _insert(table: str, rows: list[dict]) -> None:
    assert table in {"session_stats_agg", "layer_snapshots", "layer_components", "layer_component_extractions"} and rows
    _http(f"INSERT INTO {table} FORMAT JSONEachRow\n" + "\n".join(json.dumps(row) for row in rows))


@pytest.fixture(autouse=True)
def _fresh_client_for_pytest_event_loop(monkeypatch):
    monkeypatch.setattr(clickhouse, "_client", None)


@pytest_asyncio.fixture(autouse=True)
async def _close_db_pool_before_event_loop_shutdown():
    yield
    # Each pytest-asyncio test has its own event loop. Do not reuse an asyncpg
    # connection bound to the previous loop for another generation allocation.
    await engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _isolated_runner_proof():
    if not URL:
        pytest.skip("isolated CH proof not configured")
    assert _rows("SELECT currentDatabase() AS db")[0]["db"] == "observal_phase14_ci"
    versions = {item["version"] for item in _rows("SELECT version FROM clickhouse_schema_migrations")}
    assert "006_layer_components" in versions  # Runner, not fixture-only DDL.
    tables = {item["name"] for item in _rows("SELECT name FROM system.tables WHERE database = 'observal_phase14_ci'")}
    assert {"layer_components", "layer_component_extractions", "session_stats_agg"} <= tables


def _marker(
    project: str,
    user: str,
    layer_hash: str,
    generation: int,
    status: str,
    version: int = CURRENT_EXTRACTOR_VERSION,
    conflict: int = 0,
) -> dict:
    return {
        "project_id": project,
        "user_id": user,
        "layer_hash": layer_hash,
        "extractor_version": version,
        "extraction_generation": generation,
        "status": status,
        "identity_conflict": conflict,
    }


def _component(
    project: str,
    user: str,
    layer_hash: str,
    generation: int,
    *,
    component_version: str = VERSION,
    extractor_version: int = CURRENT_EXTRACTOR_VERSION,
) -> dict:
    return {
        "project_id": project,
        "user_id": user,
        "layer_hash": layer_hash,
        "hash_schema_version": 2,
        "extractor_version": extractor_version,
        "extraction_generation": generation,
        "occurrence_key": "proof-one",
        "component_type": "mcp",
        "source": "standalone",
        "harness": "claude-code",
        "scope": "project",
        "parent_agent_id": "",
        "parent_agent_version": "",
        "raw_listing_id": COMPONENT,
        "raw_name": "probe",
        "raw_version": "1.0.0",
        "qualified_name": "proof/probe",
        "local_name": "probe",
        "component_id": COMPONENT,
        "component_version_id": component_version,
        "identity_status": "resolved",
        "verification_status": "verified",
    }


def _session(project: str, user: str, layer_hash: str, session: str) -> dict:
    return {
        "project_id": project,
        "user_id": user,
        "harness": "claude-code",
        "session_id": session,
        "layer_hash": layer_hash,
        "first_event_time": "2026-01-01 12:00:00.000",
        "last_event_time": "2026-01-01 12:00:00.000",
    }


@pytest.mark.asyncio
async def test_user_project_version_and_latest_complete_publication():
    project = "phase14-" + uuid.uuid4().hex
    other_project = project + "-other"
    layer_hash = "v2_" + "a" * 60
    user, other = "owner", "other"
    period = (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC))
    _insert(
        "session_stats_agg",
        [
            _session(project, user, layer_hash, "present"),
            _session(project, other, layer_hash, "other-user"),
            _session(other_project, user, layer_hash, "other-project"),
        ],
    )
    _insert(
        "layer_components",
        [
            _component(project, user, layer_hash, 1),
            _component(other_project, user, layer_hash, 1, component_version=OTHER_VERSION),
        ],
    )
    assert await presence_cohort(project, "mcp", COMPONENT, VERSION, period) == []
    _insert(
        "layer_component_extractions",
        [_marker(project, user, layer_hash, 1, "complete"), _marker(other_project, user, layer_hash, 1, "complete")],
    )
    cohort = await presence_cohort(project, "mcp", COMPONENT, VERSION, period)
    assert {(row["user_id"], row["session_id"]) for row in cohort} == {(user, "present")}
    assert await presence_cohort(project, "mcp", COMPONENT, OTHER_VERSION, period) == []
    _insert("layer_component_extractions", [_marker(project, user, layer_hash, 2, "failed")])
    assert len(await presence_cohort(project, "mcp", COMPONENT, VERSION, period)) == 1
    _insert("layer_components", [_component(project, user, layer_hash, 3, component_version=OTHER_VERSION)])
    assert len(await presence_cohort(project, "mcp", COMPONENT, VERSION, period)) == 1
    _insert("layer_component_extractions", [_marker(project, user, layer_hash, 3, "complete")])
    assert await presence_cohort(project, "mcp", COMPONENT, VERSION, period) == []
    assert len(await presence_cohort(project, "mcp", COMPONENT, OTHER_VERSION, period)) == 1
    _insert(
        "layer_component_extractions",
        [_marker(project, user, layer_hash, 4, "complete", version=CURRENT_EXTRACTOR_VERSION + 1)],
    )
    assert (
        len(await presence_cohort(project, "mcp", COMPONENT, OTHER_VERSION, period)) == 1
    )  # no newer-version fallback
    coverage = await presence_coverage(project, "mcp", COMPONENT, OTHER_VERSION, period)
    assert coverage["eligible_sessions"] == 2 and coverage["present_sessions"] == 1
    assert coverage["present_users"] == 1


@pytest.mark.asyncio
async def test_real_extractor_generation_zero_row_idempotence_and_force():
    parsed = urlparse(os.environ.get("DATABASE_URL", ""))
    if parsed.hostname != "127.0.0.1" or parsed.port != 15432 or parsed.path != "/observal_phase14_ci":
        pytest.skip("requires isolated proof PostgreSQL at localhost:15432")
    project = "phase14-" + uuid.uuid4().hex
    layer_hash = "v2_" + uuid.uuid4().hex + uuid.uuid4().hex[:28]
    _insert(
        "layer_snapshots",
        [
            {
                "project_id": project,
                "user_id": "owner",
                "hash": layer_hash,
                "harness": "claude-code",
                "file_count": 0,
                "total_size": 0,
                "content": json.dumps(
                    {
                        "pinned_versions": {"schema_version": 2, "agents": [], "standalone": []},
                        "drift": {"is_canonical": True},
                    }
                ),
            }
        ],
    )
    first = await ensure_layer_components(project, "owner", layer_hash)
    assert first["status"] == "complete" and first["occurrences"] == 0
    assert (await ensure_layer_components(project, "owner", layer_hash))["status"] == "already_complete"
    rebuilt = await ensure_layer_components(project, "owner", layer_hash, force=True)
    assert rebuilt["generation"] > first["generation"] and rebuilt["occurrences"] == 0
    markers = _rows(
        f"SELECT extraction_generation, status FROM layer_component_extractions "
        f"WHERE project_id = '{project}' ORDER BY extraction_generation"
    )
    assert [row["status"] for row in markers] == ["complete", "complete"]


@pytest.mark.asyncio
async def test_legacy_conflicts_unverified_and_zero_row_publications_are_unknown():
    project = "phase14-" + uuid.uuid4().hex
    user = "owner"
    period = (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC))
    legacy_hash, layer_hash, zero_hash = "abcdabcdabcdabcd", "v2_" + "b" * 60, "v2_" + "c" * 60
    _insert(
        "session_stats_agg",
        [
            _session(project, user, legacy_hash, "legacy"),
            _session(project, user, layer_hash, "conflict"),
            _session(project, user, zero_hash, "zero"),
        ],
    )
    _insert("layer_components", [_component(project, user, legacy_hash, 1), _component(project, user, layer_hash, 2)])
    _insert(
        "layer_component_extractions",
        [
            _marker(project, user, legacy_hash, 1, "complete"),
            _marker(project, user, layer_hash, 2, "complete", conflict=1),
            _marker(project, user, zero_hash, 3, "complete"),
        ],
    )
    assert await presence_cohort(project, "mcp", COMPONENT, None, period) == []
    coverage = await presence_coverage(project, "mcp", COMPONENT, None, period)
    assert coverage["legacy_hash_sessions"] == 1
    assert coverage["identity_conflict_sessions"] == 1
    assert coverage["mapping_complete_sessions"] == 2  # legacy diagnostics + empty v2 layer
    assert coverage["present_sessions"] == 0


@pytest.mark.asyncio
async def test_isolated_backfill_is_scoped_and_resumable_with_zero_row_publications():
    parsed = urlparse(os.environ.get("DATABASE_URL", ""))
    if parsed.hostname != "127.0.0.1" or parsed.port != 15432 or parsed.path != "/observal_phase14_ci":
        pytest.skip("requires isolated proof PostgreSQL at localhost:15432")
    # This range sorts after the other isolated proof projects; old runs of
    # this test sort before the fresh nanosecond prefix.
    prefix = f"zzzzzz-phase16-{time.time_ns():020d}-"
    project = prefix + uuid.uuid4().hex
    layer_hash = "v2_" + uuid.uuid4().hex + uuid.uuid4().hex[:28]
    _insert(
        "layer_snapshots",
        [
            {
                "project_id": project,
                "user_id": user,
                "hash": layer_hash,
                "harness": "claude-code",
                "file_count": 0,
                "total_size": 0,
                "content": json.dumps(
                    {
                        "pinned_versions": {"schema_version": 2, "agents": [], "standalone": []},
                        "drift": {"is_canonical": True},
                    }
                ),
            }
            for user in ("owner", "other")
        ],
    )
    cursor = [prefix, "", ""]
    first = await backfill_layer_components({}, after=cursor, batch_size=8, max_batches=1)
    assert first["scanned"] == 2 and first["complete"] == 2 and first["failed"] == 0
    assert first["next_cursor"] is None
    second = await backfill_layer_components({}, after=cursor, batch_size=8, max_batches=1)
    assert second["scanned"] == 2 and second["skipped"] == 2 and second["complete"] == 0
    rows = _rows(
        f"SELECT user_id, status, occurrence_count FROM layer_component_extractions "
        f"WHERE project_id = '{project}' ORDER BY user_id"
    )
    assert [(row["user_id"], row["status"], row["occurrence_count"]) for row in rows] == [
        ("other", "complete", 0),
        ("owner", "complete", 0),
    ]


@pytest.mark.asyncio
async def test_rebuild_requires_current_extractor_version_and_no_older_fallback(monkeypatch):
    project = "phase17-" + uuid.uuid4().hex
    layer_hash = "v2_" + "d" * 60
    period = (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC))
    _insert("session_stats_agg", [_session(project, "owner", layer_hash, "version-bump")])
    _insert("layer_components", [_component(project, "owner", layer_hash, 1, extractor_version=1)])
    _insert("layer_component_extractions", [_marker(project, "owner", layer_hash, 1, "complete", version=1)])
    assert await presence_cohort(project, "mcp", COMPONENT, VERSION, period) == []  # no fallback to version 1

    monkeypatch.setattr(queries, "CURRENT_EXTRACTOR_VERSION", 1)
    assert len(await presence_cohort(project, "mcp", COMPONENT, VERSION, period)) == 1
    monkeypatch.setattr(queries, "CURRENT_EXTRACTOR_VERSION", CURRENT_EXTRACTOR_VERSION)
    _insert(
        "layer_component_extractions",
        [_marker(project, "owner", layer_hash, 2, "complete", version=CURRENT_EXTRACTOR_VERSION)],
    )
    assert await presence_cohort(project, "mcp", COMPONENT, VERSION, period) == []  # version-2 zero rows
    _insert(
        "layer_components",
        [_component(project, "owner", layer_hash, 3, extractor_version=CURRENT_EXTRACTOR_VERSION)],
    )
    assert await presence_cohort(project, "mcp", COMPONENT, VERSION, period) == []  # unpublished rebuild
    _insert(
        "layer_component_extractions",
        [_marker(project, "owner", layer_hash, 3, "complete", version=CURRENT_EXTRACTOR_VERSION)],
    )
    assert len(await presence_cohort(project, "mcp", COMPONENT, VERSION, period)) == 1


@pytest.mark.asyncio
async def test_late_older_complete_and_conflicted_newer_marker_never_displace_winner():
    project = "phase17-" + uuid.uuid4().hex
    layer_hash = "v2_" + "e" * 60
    period = (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC))
    _insert("session_stats_agg", [_session(project, "owner", layer_hash, "parallel-publication")])
    _insert(
        "layer_components",
        [
            _component(project, "owner", layer_hash, 1, component_version=OTHER_VERSION),
            _component(project, "owner", layer_hash, 2, component_version=OTHER_VERSION),
            _component(project, "owner", layer_hash, 3),
        ],
    )
    _insert("layer_component_extractions", [_marker(project, "owner", layer_hash, 3, "complete")])
    assert len(await presence_cohort(project, "mcp", COMPONENT, VERSION, period)) == 1
    _insert("layer_component_extractions", [_marker(project, "owner", layer_hash, 1, "complete")])
    _insert("layer_component_extractions", [_marker(project, "owner", layer_hash, 2, "complete")])
    assert await presence_cohort(project, "mcp", COMPONENT, OTHER_VERSION, period) == []
    _insert("layer_components", [_component(project, "owner", layer_hash, 4, component_version=OTHER_VERSION)])
    _insert(
        "layer_component_extractions",
        [_marker(project, "owner", layer_hash, 4, "complete"), _marker(project, "owner", layer_hash, 4, "failed")],
    )
    assert len(await presence_cohort(project, "mcp", COMPONENT, VERSION, period)) == 1
    assert await presence_cohort(project, "mcp", COMPONENT, OTHER_VERSION, period) == []


@pytest.mark.asyncio
async def test_legacy_same_file_hash_with_two_users_different_pins_is_unknown_for_both():
    project = "phase17-" + uuid.uuid4().hex
    legacy_hash = "abcdefabcdefabcd"
    period = (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC))
    _insert("session_stats_agg", [_session(project, user, legacy_hash, user) for user in ("owner", "other")])
    _insert(
        "layer_snapshots",
        [
            {
                "project_id": project,
                "user_id": user,
                "hash": legacy_hash,
                "harness": "claude-code",
                "file_count": 0,
                "total_size": 0,
                "content": json.dumps({"pinned_versions": {"agents": [{"id": pin_id}]}}),
            }
            for user, pin_id in (("owner", COMPONENT), ("other", OTHER_VERSION))
        ],
    )
    _insert("layer_components", [_component(project, "owner", legacy_hash, 1)])
    _insert("layer_component_extractions", [_marker(project, "owner", legacy_hash, 1, "complete")])
    assert await presence_cohort(project, "mcp", COMPONENT, VERSION, period) == []
    coverage = await presence_coverage(project, "mcp", COMPONENT, VERSION, period)
    assert coverage["eligible_sessions"] == coverage["legacy_hash_sessions"] == 2
    assert coverage["present_sessions"] == 0


@pytest.mark.asyncio
async def test_sanitized_bundled_fixture_aliases_have_exact_versioned_presence():
    fixture = Path(__file__).resolve().parents[1] / "fixtures/component_insights/claude_code"
    observed = json.loads((fixture / "registry_components.json").read_text())["components"]
    original = json.loads((fixture / "pinned_snapshot.json").read_text())
    assert len(observed) == len(original["pinned_versions"]["agents"][0]["components"]) == 2
    # The historical pinned snapshot omitted nested IDs. Adapt only the
    # separately verified sanitized registry ID+alias pairs; do not claim
    # these v2 pins came from that historical installation.
    project = "phase17-" + uuid.uuid4().hex
    layer_hash = "v2_" + uuid.uuid4().hex + uuid.uuid4().hex[:28]
    period = (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC))
    _insert("session_stats_agg", [_session(project, "owner", layer_hash, "bundled-fixture")])
    rows = []
    for index, component in enumerate(observed):
        assert component["installed_alias"]
        row = _component(project, "owner", layer_hash, 1)
        row.update(
            {
                "source": "agent",
                "raw_listing_id": component["id"],
                "component_id": component["id"],
                "qualified_name": component["qualified_name"],
                "local_name": component["installed_alias"],
                "occurrence_key": f"bundled-{index}",
                "component_version_id": VERSION if index == 0 else OTHER_VERSION,
            }
        )
        rows.append(row)
    _insert("layer_components", rows)
    assert await presence_cohort(project, "mcp", observed[0]["id"], VERSION, period) == []
    _insert("layer_component_extractions", [_marker(project, "owner", layer_hash, 1, "complete")])
    for index, component in enumerate(observed):
        scoped = VERSION if index == 0 else OTHER_VERSION
        cohort = await presence_cohort(project, "mcp", component["id"], scoped, period)
        assert {(row["user_id"], row["session_id"]) for row in cohort} == {("owner", "bundled-fixture")}
    assert await presence_cohort(project, "mcp", observed[0]["id"], OTHER_VERSION, period) == []


@pytest.mark.asyncio
async def test_genuine_standalone_install_fixture_extracts_exact_scoped_cohort():
    parsed = urlparse(os.environ.get("DATABASE_URL", ""))
    if parsed.hostname != "127.0.0.1" or parsed.port != 15432 or parsed.path != "/observal_phase14_ci":
        pytest.skip("requires isolated proof PostgreSQL at localhost:15432")
    fixture_path = (
        Path(__file__).resolve().parents[1] / "fixtures/component_insights/claude_code/standalone_install.json"
    )
    fixture = json.loads(fixture_path.read_text())
    listing = fixture["listing"]
    install = fixture["installation"]
    assert install["registry_identity_tracked"] and install["effective_alias_verified"] and install["v2_hash_built"]
    assert fixture["pinned_versions"]["standalone"][0]["id"] == listing["id"]
    assert fixture["drift"]["mcp_verifications"][0]["status"] == "verified"

    # Seed the isolated registry with *replacement* fixture UUIDs; no original
    # identity or original file/settings content is part of the test database.
    listing_id = uuid.UUID(listing["id"])
    version_id = uuid.UUID("88888888-8888-4888-8888-888888888888")
    async with async_session() as db:
        row = await db.get(McpListing, listing_id)
        if row is None:
            owner = User(
                email=f"phase17-{uuid.uuid4().hex}@example.invalid",
                username=f"phase17{uuid.uuid4().hex[:12]}",
                name="Isolated fixture owner",
            )
            db.add(owner)
            await db.flush()
            namespace, slug = listing["qualified_name"].split("/", 1)
            row = McpListing(
                id=listing_id,
                name=listing["name"],
                namespace=namespace,
                slug=slug,
                category="developer-tools",
                owner=owner.username,
                submitted_by=owner.id,
            )
            db.add(row)
            await db.flush()
            version = McpVersion(
                id=version_id,
                listing_id=listing_id,
                version=listing["version"],
                description="synthetic sanitized fixture version",
                released_by=owner.id,
                released_at=datetime.now(UTC),
                status=ListingStatus.approved,
            )
            db.add(version)
            await db.flush()
            row.latest_version_id = version.id
            await db.commit()
        else:
            version = (await db.execute(select(McpVersion).where(McpVersion.listing_id == listing_id))).scalar_one()
            assert version.id == version_id

    project = "phase17-" + uuid.uuid4().hex
    # The manifest digest is a constructed safe stand-in for redacted config;
    # this is not a replay of the original upload hash.
    file_digest = hashlib.sha256(b"sanitized standalone manifest").hexdigest()
    layer_hash = layer_hash_v2(
        {"claude-code": [{"path": "project:.mcp.json", "hash": f"sha256-{file_digest}"}]},
        fixture["pinned_versions"],
    )
    _insert(
        "layer_snapshots",
        [
            {
                "project_id": project,
                "user_id": "owner",
                "hash": layer_hash,
                "harness": "claude-code",
                "file_count": 1,
                "total_size": 0,
                "content": json.dumps({"pinned_versions": fixture["pinned_versions"], "drift": fixture["drift"]}),
            }
        ],
    )
    _insert(
        "session_stats_agg",
        [
            _session(project, "owner", layer_hash, "tracked-standalone"),
            _session(project, "other", layer_hash, "wrong-user"),
        ],
    )
    indexed = await ensure_layer_components(project, "owner", layer_hash)
    assert indexed["status"] == "complete" and indexed["occurrences"] == 1
    period = (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC))
    cohort = await presence_cohort(project, "mcp", listing["id"], str(version_id), period)
    assert {(row["user_id"], row["session_id"]) for row in cohort} == {("owner", "tracked-standalone")}
    assert await presence_cohort(project, "mcp", listing["id"], OTHER_VERSION, period) == []
    assert await presence_cohort(project + "-other", "mcp", listing["id"], str(version_id), period) == []

    # A later conflicting upload invalidates the earlier complete mapping even
    # before (or without) a successful re-extraction.
    _insert(
        "layer_snapshots",
        [
            {
                "project_id": project,
                "user_id": "owner",
                "hash": layer_hash,
                "harness": "claude-code",
                "file_count": 1,
                "total_size": 0,
                "content": json.dumps({"identity_status": "identity_conflict"}),
                "uploaded_at": (datetime.now(UTC) + timedelta(seconds=5)).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            }
        ],
    )
    assert await presence_cohort(project, "mcp", listing["id"], str(version_id), period) == []
    coverage = await presence_coverage(project, "mcp", listing["id"], str(version_id), period)
    assert coverage["identity_conflict_sessions"] >= 1 and coverage["present_sessions"] == 0
