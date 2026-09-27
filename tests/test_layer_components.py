# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""Phase 1.4/1.5 normalization, resolution and publication contracts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from services.clickhouse.migrations import _split_sql
from services.layer_components import CURRENT_EXTRACTOR_VERSION, extractor, resolver
from services.layer_components.normalizer import normalize_snapshot
from services.layer_components.resolver import ResolvedOccurrence

A = "11111111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"
PARENT = "33333333-3333-4333-8333-333333333333"
VERSION = "44444444-4444-4444-8444-444444444444"


def _pins(raw_id=A):
    return {
        "schema_version": 2,
        "agents": [
            {
                "id": PARENT,
                "version": "2.0",
                "harness": "claude-code",
                "scope": "project",
                "components": [
                    {
                        "type": "mcp",
                        "id": raw_id,
                        "name": "probe",
                        "version": "1.0.0",
                        "scope": "project",
                        "local_name": "alice-probe",
                    },
                    {"type": "prompt", "id": B, "name": "do-not-project"},
                ],
            }
        ],
        "standalone": [
            {
                "type": "mcp",
                "id": B,
                "name": "probe",
                "version": "2.0.0",
                "scope": "user",
                "harness": "claude-code",
                "local_name": "bob-probe",
            }
        ],
    }


def _drift():
    return {
        "is_canonical": True,
        "mcp_verifications": [
            {
                "harness": "claude-code",
                "component_id": A,
                "alias": "alice-probe",
                "scope": "project",
                "parent_agent_id": PARENT,
                "status": "verified",
            },
            {
                "harness": "claude-code",
                "component_id": B,
                "alias": "bob-probe",
                "scope": "user",
                "parent_agent_id": "",
                "status": "verified",
            },
        ],
    }


def test_production_layer_migration_is_exactly_the_pinned_phase0_ddl():
    root = Path(__file__).resolve().parents[1]
    production = _split_sql((root / "observal-server/clickhouse/migrations/006_layer_components.sql").read_text())
    fixture = _split_sql(
        (root / "tests/fixtures/component_insights/clickhouse/projection_tables.sql")
        .read_text()
        .replace("{prefix}", "")
    )[:2]
    assert production == fixture
    assert "component_id" not in production[0].split("ORDER BY (")[1]
    assert "user_id" in production[0].split("ORDER BY (")[1]
    pg = (root / "observal-server/alembic/versions/028_projection_generation.py").read_text()
    assert "projection_generation_seq" in pg and "NO CYCLE CACHE 1" in pg


def test_normalizer_stable_duplicate_keys_v1_and_drift_fail_closed():
    pins = _pins()
    pins["agents"][0]["components"].append(dict(pins["agents"][0]["components"][0]))
    rows = normalize_snapshot(pins, _drift())
    assert len(rows) == 3  # prompt skipped, both identical occurrences retained
    assert len({row.occurrence_key for row in rows}) == 3
    assert sum(row.verification_status == "verified" for row in rows) == 3
    assert [row.occurrence_key for row in rows] == [row.occurrence_key for row in normalize_snapshot(pins, _drift())]
    pins["agents"][0]["components"].reverse()
    assert [row.occurrence_key for row in rows] == [row.occurrence_key for row in normalize_snapshot(pins, _drift())]
    assert not any(row.raw_name == "do-not-project" for row in rows)
    assert all(
        row.verification_status != "verified" for row in normalize_snapshot({**pins, "schema_version": 1}, _drift())
    )
    assert all(row.verification_status != "verified" for row in normalize_snapshot(pins, {"is_canonical": False}))
    assert normalize_snapshot({"agents": {"not": "a list"}, "standalone": [False]}, {}) == []


def test_normalizer_occurrence_key_is_full_canonical_pin_digest_not_verification():
    pins = _pins()
    pins["agents"][0]["components"].append(dict(pins["agents"][0]["components"][0]))
    rows = normalize_snapshot(pins, _drift())
    bundled = [row for row in rows if row.source == "agent"]
    assert len(bundled) == 2
    for ordinal, row in enumerate(bundled):
        fields = [
            row.source,
            row.component_type,
            row.raw_listing_id,
            row.raw_version,
            row.harness,
            row.scope,
            row.local_name,
            row.parent_agent_id,
            row.parent_agent_version,
            row.qualified_name,
            row.raw_name,
            ordinal,
        ]
        expected = hashlib.sha256(json.dumps(fields, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        assert re.fullmatch(r"[0-9a-f]{64}", row.occurrence_key)
        assert row.occurrence_key == expected
    assert [row.occurrence_key for row in rows] == [
        row.occurrence_key for row in normalize_snapshot(pins, {"is_canonical": False})
    ]
    assert [row.occurrence_key for row in rows] == [
        row.occurrence_key for row in normalize_snapshot({**pins, "schema_version": 1}, _drift())
    ]
    pins["agents"][0]["components"][0]["name"] = "Cafe\u0301"
    decomposed = normalize_snapshot(pins, _drift())
    pins["agents"][0]["components"][0]["name"] = "Caf\u00e9"
    assert [row.occurrence_key for row in decomposed] == [
        row.occurrence_key for row in normalize_snapshot(pins, _drift())
    ]


def test_normalizer_malformed_optional_fields_do_not_create_false_verified_presence():
    pins = _pins()
    pins["agents"].extend([False, None, {"components": "not an array"}])
    pins["standalone"].extend([None, {"type": "sandbox", "id": A}, {"type": "skill", "name": "unknown"}])
    pins["agents"][0]["components"].append({"type": "mcp", "id": [A], "name": 4, "local_name": False})
    rows = normalize_snapshot(pins, {"is_canonical": None, "mcp_verifications": [None, False]})
    assert len(rows) == 4
    assert all(row.verification_status != "verified" for row in rows)
    assert rows == normalize_snapshot(pins, {"is_canonical": None, "mcp_verifications": [None, False]})


def test_normalizer_mixed_verified_and_unverified_mcp_preserves_specific_evidence():
    drift = _drift()
    drift["is_canonical"] = None  # CLI saw one remote MCP it cannot verify.
    drift["mcp_verifications"][1]["status"] = "unverified"
    rows = {row.raw_listing_id: row for row in normalize_snapshot(_pins(), drift)}
    assert rows[A].verification_status == "verified"
    assert rows[B].verification_status == "unverified"
    drift["is_canonical"] = False  # A genuine layer drift still fails closed.
    rows = {row.raw_listing_id: row for row in normalize_snapshot(_pins(), drift)}
    assert rows[A].verification_status != "verified"
    assert rows[B].verification_status != "verified"


def test_normalizer_rejects_alias_collision_between_distinct_mcp_claims():
    pins = _pins()
    pins["standalone"][0]["scope"] = "project"
    pins["standalone"][0]["local_name"] = "alice-probe"
    drift = _drift()
    drift["mcp_verifications"][1]["scope"] = "project"
    drift["mcp_verifications"][1]["alias"] = "alice-probe"
    rows = normalize_snapshot(pins, drift)
    assert len(rows) == 2
    assert all(row.verification_status == "unverified" for row in rows)


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _FakeDB:
    def __init__(
        self, *, listings=None, parent=None, components=None, versions=None, skill_listings=None, skill_versions=None
    ):
        self.listings = listings or []
        self.parent = parent or []
        self.components = components or []
        self.versions = versions or []
        self.skill_listings = skill_listings or []
        self.skill_versions = skill_versions or []
        self.calls = []

    async def execute(self, statement):
        sql = str(statement)
        self.calls.append(sql)
        if "FROM mcp_listings" in sql:
            return _Rows(self.listings)
        if "FROM agent_versions" in sql:
            return _Rows(self.parent)
        if "FROM agent_components" in sql:
            return _Rows(self.components)
        if "FROM mcp_versions" in sql:
            return _Rows(self.versions)
        if "FROM skill_listings" in sql:
            return _Rows(self.skill_listings)
        if "FROM skill_versions" in sql:
            return _Rows(self.skill_versions)
        return _Rows([])


def _row(**kwargs):
    return SimpleNamespace(**kwargs)


@pytest.mark.asyncio
async def test_resolver_direct_deleted_parent_name_ambiguity_and_independent_version():
    from dataclasses import replace

    original = normalize_snapshot(_pins(), _drift())[0]
    assert original.raw_listing_id == A  # sorting puts bundled occurrence first
    direct = original
    deleted = replace(original, raw_listing_id=B)
    wrong_type = replace(original, raw_listing_id="not-a-uuid", parent_agent_id="")
    ambiguous = replace(original, raw_listing_id="", parent_agent_id="", qualified_name="")
    parent_match = replace(original, raw_listing_id="", raw_name="probe")
    db = _FakeDB(
        listings=[
            _row(id=UUID(A), name="probe", namespace="alice", slug="probe"),
            _row(id=UUID(B), name="probe", namespace="bob", slug="probe"),
        ],
        parent=[_row(id=UUID(VERSION), agent_id=UUID(PARENT), version="2.0")],
        components=[
            _row(
                agent_version_id=UUID(VERSION),
                component_type="mcp",
                component_id=UUID(A),
                component_name="probe",
                resolved_version="1.0.0",
            )
        ],
        versions=[
            _row(id=UUID(VERSION), listing_id=UUID(A), version="1.0.0"),
            _row(id=UUID(PARENT), listing_id=UUID(B), version="1.0.0"),
        ],
    )
    resolved = await resolver.resolve_occurrences(db, [direct, parent_match, wrong_type, ambiguous])
    assert [(item.component_id, item.component_version_id, item.identity_status) for item in resolved] == [
        (A, VERSION, "resolved"),
        (A, VERSION, "resolved"),
        ("", "", "ambiguous"),
        ("", "", "ambiguous"),
    ]
    deleted = replace(deleted, raw_listing_id="55555555-5555-4555-8555-555555555555")
    result = await resolver.resolve_occurrences(db, [deleted])
    assert result[0].identity_status == "unresolved"  # valid but deleted UUID cannot be guessed by name
    assert sum("FROM mcp_listings" in sql for sql in db.calls) <= 3  # batched, not per occurrence
    no_version = await resolver.resolve_occurrences(
        _FakeDB(listings=[_row(id=UUID(A), name="probe", namespace="alice", slug="probe")]),
        [direct],
    )
    assert no_version[0].component_id == A and no_version[0].component_version_id == ""
    deleted_parent = replace(parent_match, parent_agent_id="55555555-5555-4555-8555-555555555555")
    assert (await resolver.resolve_occurrences(db, [deleted_parent]))[0].identity_status == "unresolved"
    assert CURRENT_EXTRACTOR_VERSION == 2  # Key-contract change rebuilds v1 projections.


@pytest.mark.asyncio
async def test_resolver_correct_type_uuid_unique_name_semver_and_unknown_versions():
    direct = normalize_snapshot(_pins(), _drift())[0]
    missing_id = "55555555-5555-4555-8555-555555555555"
    cases = [
        replace(direct, raw_listing_id=B, parent_agent_id="", qualified_name=""),  # valid but wrong type
        replace(direct, component_type="skill", raw_listing_id=B, parent_agent_id="", qualified_name=""),
        replace(direct, raw_listing_id=missing_id, parent_agent_id=""),  # deleted, no name fallback
        replace(direct, raw_listing_id="", parent_agent_id="", qualified_name="alice/probe"),
        replace(direct, raw_listing_id="", parent_agent_id="", qualified_name="missing/probe"),
        replace(direct, raw_listing_id=A, raw_version="9.9.9"),
    ]
    db = _FakeDB(
        listings=[_row(id=UUID(A), name="probe", namespace="alice", slug="probe")],
        skill_listings=[_row(id=UUID(B), name="probe", namespace="bob", slug="probe")],
        versions=[_row(id=UUID(VERSION), listing_id=UUID(A), version="1.0.0")],
        skill_versions=[_row(id=UUID(PARENT), listing_id=UUID(B), version="1.0.0")],
    )
    results = await resolver.resolve_occurrences(db, cases)
    assert [(item.identity_status, item.component_id, item.component_version_id) for item in results] == [
        ("unresolved", "", ""),
        ("resolved", B, PARENT),
        ("unresolved", "", ""),
        ("resolved", A, VERSION),
        ("unresolved", "", ""),
        ("resolved", A, ""),
    ]
    assert sum("FROM mcp_listings" in statement for statement in db.calls) == 1
    assert sum("FROM skill_listings" in statement for statement in db.calls) == 1


@pytest.mark.asyncio
async def test_resolver_ambiguous_name_and_parent_component_match_are_not_guessed():
    original = normalize_snapshot(_pins(), _drift())[0]
    missing_pin = replace(original, raw_listing_id="", parent_agent_id="", qualified_name="")
    parent_pin = replace(original, raw_listing_id="", qualified_name="")
    db = _FakeDB(
        listings=[
            _row(id=UUID(A), name="probe", namespace="alice", slug="probe"),
            _row(id=UUID(B), name="probe", namespace="bob", slug="probe"),
        ],
        versions=[
            _row(id=UUID(VERSION), listing_id=UUID(A), version="1.0.0"),
            _row(id=UUID(PARENT), listing_id=UUID(B), version="1.0.0"),
        ],
        parent=[_row(id=UUID(VERSION), agent_id=UUID(PARENT), version="2.0")],
        components=[
            _row(
                agent_version_id=UUID(VERSION),
                component_type="mcp",
                component_id=UUID(A),
                component_name="probe",
                resolved_version="1.0.0",
            ),
            _row(
                agent_version_id=UUID(VERSION),
                component_type="mcp",
                component_id=UUID(B),
                component_name="probe",
                resolved_version="1.0.0",
            ),
        ],
    )
    results = await resolver.resolve_occurrences(db, [missing_pin, parent_pin])
    assert [item.identity_status for item in results] == ["ambiguous", "ambiguous"]
    assert all(not item.component_id for item in results)


@pytest.mark.asyncio
async def test_extractor_snapshot_lookup_is_user_scoped_and_conflicts_are_not_winners(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "data": [
                    {"content": json.dumps({"pinned_versions": {"schema_version": 2, "agents": []}})},
                    {"content": json.dumps({"pinned_versions": {"schema_version": 2, "agents": [1]}})},
                ]
            }

    query = AsyncMock(return_value=Response())
    monkeypatch.setattr(extractor.clickhouse, "_query", query)
    snapshot, conflict = await extractor._snapshot("project", "owner", "v2_hash")
    assert conflict and snapshot is not None
    assert query.await_args.args[1] == {
        "param_project_id": "project",
        "param_user_id": "owner",
        "param_layer_hash": "v2_hash",
    }


@pytest.mark.asyncio
async def test_extractor_publishes_after_insert_and_failed_retry_preserves_marker(monkeypatch):
    events = []
    pin = normalize_snapshot(_pins(), _drift())[0]
    db = SimpleNamespace()

    class Session:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_):
            return False

    monkeypatch.setattr(extractor, "async_session", Session)
    monkeypatch.setattr(
        extractor, "_snapshot", AsyncMock(return_value=({"pinned_versions": _pins(), "drift": _drift()}, False))
    )
    monkeypatch.setattr(extractor, "_latest_complete", AsyncMock(return_value=None))
    monkeypatch.setattr(extractor, "next_projection_generation", AsyncMock(return_value=17))
    monkeypatch.setattr(
        extractor, "resolve_occurrences", AsyncMock(return_value=[ResolvedOccurrence(pin, A, VERSION, "resolved")])
    )

    async def insert(sql, params=None, *, data=None):
        events.append(("insert", sql, data))
        return []

    async def marker(*args, status, **kwargs):
        events.append(("marker", status))

    monkeypatch.setattr(extractor, "_query", insert)
    monkeypatch.setattr(extractor, "_marker", marker)
    result = await extractor.ensure_layer_components("project", "user", "v2_" + "a" * 60)
    assert result["status"] == "complete" and result["generation"] == 17
    assert [event[0] for event in events] == ["insert", "marker"]
    assert events[1] == ("marker", "complete")
    assert json.loads(events[0][2])["extraction_generation"] == 17
    events.clear()
    monkeypatch.setattr(extractor, "_latest_complete", AsyncMock(return_value={"identity_conflict": 0}))
    assert (await extractor.ensure_layer_components("project", "user", "v2_" + "a" * 60))[
        "status"
    ] == "already_complete"
    assert not events
    monkeypatch.setattr(extractor, "_latest_complete", AsyncMock(return_value=None))

    async def fail_insert(sql, params=None, *, data=None):
        raise RuntimeError("insert failed")

    monkeypatch.setattr(extractor, "_query", fail_insert)
    with pytest.raises(RuntimeError, match="insert failed"):
        await extractor.ensure_layer_components("project", "user", "v2_" + "a" * 60)
    assert events[-1] == ("marker", "failed")


@pytest.mark.asyncio
async def test_extractor_zero_occurrences_publishes_complete(monkeypatch):
    events = []
    monkeypatch.setattr(extractor, "_snapshot", AsyncMock(return_value=({"pinned_versions": {}, "drift": {}}, False)))
    monkeypatch.setattr(extractor, "_latest_complete", AsyncMock(return_value=None))
    monkeypatch.setattr(extractor, "next_projection_generation", AsyncMock(return_value=10))
    monkeypatch.setattr(extractor, "async_session", lambda: None)  # not used if resolver handles zero
    monkeypatch.setattr(extractor, "resolve_occurrences", AsyncMock(return_value=[]))

    async def marker(*args, status, **kwargs):
        events.append(status)

    monkeypatch.setattr(extractor, "_marker", marker)
    result = await extractor.ensure_layer_components("project", "user", "v2_" + "a" * 60)
    assert result["status"] == "complete" and result["occurrences"] == 0
    assert events == ["complete"]


@pytest.mark.asyncio
async def test_extractor_complete_marker_failure_emits_failed_marker_without_claiming_publication(monkeypatch):
    pin = normalize_snapshot(_pins(), _drift())[0]
    db = SimpleNamespace()

    class Session:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_):
            return False

    monkeypatch.setattr(extractor, "async_session", Session)
    monkeypatch.setattr(
        extractor, "_snapshot", AsyncMock(return_value=({"pinned_versions": _pins(), "drift": _drift()}, False))
    )
    monkeypatch.setattr(extractor, "_latest_complete", AsyncMock(return_value=None))
    monkeypatch.setattr(extractor, "next_projection_generation", AsyncMock(return_value=37))
    monkeypatch.setattr(
        extractor, "resolve_occurrences", AsyncMock(return_value=[ResolvedOccurrence(pin, A, VERSION, "resolved")])
    )
    monkeypatch.setattr(extractor, "_query", AsyncMock(return_value=[]))
    marker = AsyncMock(side_effect=[RuntimeError("marker unavailable"), None])
    monkeypatch.setattr(extractor, "_marker", marker)
    with pytest.raises(RuntimeError, match="marker unavailable"):
        await extractor.ensure_layer_components("project", "owner", "v2_" + "a" * 60)
    assert [call.kwargs["status"] for call in marker.await_args_list] == ["complete", "failed"]
    assert {call.kwargs["count"] for call in marker.await_args_list} == {1}
    assert [call.args[3] for call in marker.await_args_list] == [37, 37]


@pytest.mark.asyncio
async def test_extractor_conflict_change_forces_rebuild_without_explicit_force(monkeypatch):
    monkeypatch.setattr(extractor, "_snapshot", AsyncMock(return_value=({"pinned_versions": {}, "drift": {}}, True)))
    monkeypatch.setattr(extractor, "_latest_complete", AsyncMock(return_value={"identity_conflict": 0}))
    monkeypatch.setattr(extractor, "next_projection_generation", AsyncMock(return_value=38))
    marker = AsyncMock()
    monkeypatch.setattr(extractor, "_marker", marker)
    rebuilt = await extractor.ensure_layer_components("project", "owner", "v2_" + "a" * 60)
    assert rebuilt == {
        "status": "complete",
        "occurrences": 0,
        "diagnostics": 0,
        "generation": 38,
        "identity_conflict": True,
    }
    assert marker.await_args.kwargs["conflict"] is True


def test_verification_lookup_is_indexed_first_match_and_tolerates_malformed_records():
    from services.layer_components.normalizer import normalize_snapshot

    component = "11111111-1111-4111-8111-111111111111"
    pins = {
        "schema_version": 2,
        "agents": [],
        "standalone": [
            {
                "type": "mcp",
                "id": component,
                "name": "p",
                "version": "1",
                "harness": "h",
                "scope": "project",
                "local_name": "p",
            }
        ],
    }
    key = {"harness": "h", "component_id": component, "alias": "p", "scope": "project", "parent_agent_id": ""}
    drift = {
        "is_canonical": True,
        "mcp_verifications": [
            "malformed",
            {**key, "status": ["unhashable"]},
            {**key, "status": "verified"},  # later duplicate cannot override the first record
            {"harness": ["unhashable"]},
        ],
    }
    assert normalize_snapshot(pins, drift)[0].verification_status == "unverified"
    drift["mcp_verifications"] = [{**key, "status": "verified"}, {**key, "status": "drifted"}]
    assert normalize_snapshot(pins, drift)[0].verification_status == "verified"
