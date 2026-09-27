# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Phase 1.3 upload identity, scoping, and deferred indexing seam."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from pydantic import ValidationError

from api.ratelimit import limiter
from api.routes import layer_snapshot
from observal_cli.layer import layer_hash_v2 as cli_layer_hash_v2
from services.clickhouse import insert as ch_insert
from services.insights import version_impact
from services.layer_hash import layer_hash_v2
from tests.test_layer_snapshot_routes import _UPLOAD, ClickHouseResponse, _app, _request, _user

FIRST = "11111111-1111-4111-8111-111111111111"
SECOND = "22222222-2222-4222-8222-222222222222"
FILE_HASH = "sha256-" + "a" * 64


@pytest.fixture(autouse=True)
def _disable_rate_limits():
    # The route unit tests mock ClickHouse and do not require a live Redis stack.
    enabled = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = enabled


def _payload(pin_id: str = FIRST) -> dict:
    harnesses = {"pi": [{"path": "user:mcp.json", "hash": FILE_HASH, "size": 2, "content": "{}"}]}
    pins = {
        "schema_version": 2,
        "agents": [
            {
                "id": FIRST,
                "name": "fixture",
                "version": "1.0.0",
                "scope": "user",
                "local_name": "",
                "harness": "pi",
                "components": [
                    {
                        "type": "mcp",
                        "id": pin_id,
                        "name": "probe",
                        "version": "1.0.0",
                        "scope": "user",
                        "local_name": "probe",
                    }
                ],
            }
        ],
        "standalone": [],
    }
    return {"hash": cli_layer_hash_v2(harnesses, pins), "harnesses": harnesses, "pinned_versions": pins}


def test_v2_pin_model_bounds_nested_fields_and_ignores_unknown_keys():
    payload = _payload()
    payload["pinned_versions"]["agents"][0]["components"][0]["secret_extra"] = "ignored"
    request = layer_snapshot.LayerSnapshotRequest.model_validate(payload)
    assert "secret_extra" not in request.pinned_versions.model_dump_json()
    assert request.pinned_versions.schema_version == 2
    assert (
        layer_snapshot.LayerSnapshotRequest.model_validate(
            {"hash": "0123456789abcdef", "pinned_versions": {"agents": [{"id": "legacy-name", "version": "1.0"}]}}
        )
        .pinned_versions.agents[0]
        .id
        == "legacy-name"
    )

    payload["pinned_versions"]["agents"][0]["components"][0]["local_name"] = "x" * 201
    with pytest.raises(ValidationError):
        layer_snapshot.LayerSnapshotRequest.model_validate(payload)
    payload["pinned_versions"]["agents"][0]["components"][0]["local_name"] = "probe"
    payload["pinned_versions"]["agents"][0]["components"][0]["id"] = "not-a-uuid"
    with pytest.raises(ValidationError):
        layer_snapshot.LayerSnapshotRequest.model_validate(payload)
    payload["pinned_versions"]["agents"][0]["components"] = [{}] * 129
    with pytest.raises(ValidationError):
        layer_snapshot.LayerSnapshotRequest.model_validate(payload)


def test_server_verifier_matches_cli_hash_for_pin_changes():
    for pin_id in (FIRST, SECOND):
        payload = _payload(pin_id)
        assert layer_hash_v2(payload["harnesses"], payload["pinned_versions"]) == payload["hash"]
    assert _payload(FIRST)["hash"] != _payload(SECOND)["hash"]
    assert UUID(FIRST)


@pytest.mark.asyncio
async def test_v2_upload_verifies_hash_before_redaction_and_indexes_both_paths(monkeypatch):
    query = AsyncMock(return_value=ClickHouseResponse())
    insert = AsyncMock()
    index = AsyncMock()
    monkeypatch.setattr("services.clickhouse.client._query", query)
    monkeypatch.setattr("services.clickhouse.insert.insert_layer_snapshot", insert)
    monkeypatch.setattr(layer_snapshot, "_ensure_layer_components", index)
    monkeypatch.setattr("services.secrets_redactor.redact_secrets", lambda value: "[redacted]")
    payload = _payload()
    request = layer_snapshot.LayerSnapshotRequest.model_validate(payload)
    first = await _UPLOAD(request, SimpleNamespace(), _user(), AsyncMock())
    assert first.stored
    row = insert.await_args.args[0]
    stored = json.loads(row["content"])
    assert stored["hash_schema_version"] == 2
    assert "identity_status" not in stored
    assert stored["harnesses"]["pi"][0]["content"] == "[redacted]"
    assert row["hash"] == payload["hash"]
    assert query.await_args.args[1]["param_user_id"] == str(_user().id)

    query.return_value = ClickHouseResponse([{"content": row["content"]}])
    second = await _UPLOAD(request, SimpleNamespace(), _user(), AsyncMock())
    assert not second.stored
    insert.assert_awaited_once()
    assert index.await_count == 2
    index.assert_awaited_with("default", str(_user().id), payload["hash"])


@pytest.mark.asyncio
async def test_upload_retries_and_conflicts_repair_same_user_even_when_indexing_fails(monkeypatch):
    from services.layer_components import extractor

    query = AsyncMock(return_value=ClickHouseResponse())
    insert = AsyncMock()
    repair = AsyncMock(side_effect=RuntimeError("index failed"))
    monkeypatch.setattr("services.clickhouse.client._query", query)
    monkeypatch.setattr("services.clickhouse.insert.insert_layer_snapshot", insert)
    monkeypatch.setattr("services.secrets_redactor.redact_secrets", lambda content: "[redacted]")
    monkeypatch.setattr(extractor, "ensure_layer_components", repair)
    payload = _payload()
    request = layer_snapshot.LayerSnapshotRequest.model_validate(payload)
    user = _user(user_id=UUID(SECOND))
    first = await _UPLOAD(request, SimpleNamespace(), user, AsyncMock())
    assert first.stored and insert.await_count == 1
    original = insert.await_args.args[0]["content"]
    query.return_value = ClickHouseResponse([{"content": original}])
    again = await _UPLOAD(request, SimpleNamespace(), user, AsyncMock())
    assert not again.stored and insert.await_count == 1
    different = layer_snapshot.LayerSnapshotRequest.model_validate(payload)
    different.pinned_versions.agents[0].components[0].version = "2.0.0"
    conflicting = await _UPLOAD(different, SimpleNamespace(), user, AsyncMock())
    assert conflicting.stored and insert.await_count == 2
    assert json.loads(insert.await_args.args[0]["content"])["identity_status"] == "identity_conflict"
    assert query.await_count == 3 and repair.await_count == 3
    assert all(call.args == ("default", str(user.id), payload["hash"]) for call in repair.await_args_list)
    assert all(call.args[1]["param_user_id"] == str(user.id) for call in query.await_args_list)


@pytest.mark.asyncio
async def test_upload_serializes_user_hash_before_scoped_lookup(monkeypatch):
    events = []

    async def acquire_lock(*args, **kwargs):
        events.append("lock")

    async def lookup(*args, **kwargs):
        events.append("lookup")
        return ClickHouseResponse()

    async def insert(*args, **kwargs):
        events.append("insert")

    db = SimpleNamespace(execute=AsyncMock(side_effect=acquire_lock))
    monkeypatch.setattr("services.clickhouse.client._query", AsyncMock(side_effect=lookup))
    monkeypatch.setattr("services.clickhouse.insert.insert_layer_snapshot", AsyncMock(side_effect=insert))
    monkeypatch.setattr(layer_snapshot, "_ensure_layer_components", AsyncMock())
    monkeypatch.setattr("services.secrets_redactor.redact_secrets", lambda value: value)
    await _UPLOAD(layer_snapshot.LayerSnapshotRequest.model_validate(_payload()), SimpleNamespace(), _user(), db)
    assert events == ["lock", "lookup", "insert"]
    assert "pg_advisory_xact_lock" in str(db.execute.await_args.args[0])


@pytest.mark.asyncio
async def test_v2_hash_mismatch_is_stored_as_conflict(monkeypatch):
    query = AsyncMock(return_value=ClickHouseResponse())
    insert = AsyncMock()
    monkeypatch.setattr("services.clickhouse.client._query", query)
    monkeypatch.setattr("services.clickhouse.insert.insert_layer_snapshot", insert)
    monkeypatch.setattr(layer_snapshot, "_ensure_layer_components", AsyncMock())
    monkeypatch.setattr("services.secrets_redactor.redact_secrets", lambda value: value)
    request = layer_snapshot.LayerSnapshotRequest.model_validate(_payload())
    request.pinned_versions.agents[0].components[0].version = "9.9.9"
    result = await _UPLOAD(request, SimpleNamespace(), _user(), AsyncMock())
    assert result.stored
    assert json.loads(insert.await_args.args[0]["content"])["identity_status"] == "identity_conflict"


@pytest.mark.asyncio
async def test_same_user_legacy_hash_pin_collision_is_persistently_conflicted(monkeypatch):
    query = AsyncMock(return_value=ClickHouseResponse())
    insert = AsyncMock()
    monkeypatch.setattr("services.clickhouse.client._query", query)
    monkeypatch.setattr("services.clickhouse.insert.insert_layer_snapshot", insert)
    monkeypatch.setattr(layer_snapshot, "_ensure_layer_components", AsyncMock())
    monkeypatch.setattr("services.secrets_redactor.redact_secrets", lambda value: value)
    first = layer_snapshot.LayerSnapshotRequest(hash="0123456789abcdef", pinned_versions={"agents": [{"id": FIRST}]})
    second = layer_snapshot.LayerSnapshotRequest(hash=first.hash, pinned_versions={"agents": [{"id": SECOND}]})
    await _UPLOAD(first, SimpleNamespace(), _user(), AsyncMock())
    original = insert.await_args.args[0]["content"]
    query.return_value = ClickHouseResponse([{"content": original}])
    await _UPLOAD(second, SimpleNamespace(), _user(), AsyncMock())
    conflict = insert.await_args.args[0]["content"]
    assert json.loads(conflict)["identity_status"] == "identity_conflict"
    assert insert.await_args.args[0]["uploaded_at"]
    query.return_value = ClickHouseResponse([{"content": conflict}])
    await _UPLOAD(first, SimpleNamespace(), _user(), AsyncMock())
    assert json.loads(insert.await_args.args[0]["content"])["identity_status"] == "identity_conflict"
    assert insert.await_count == 3


@pytest.mark.asyncio
async def test_two_users_with_same_legacy_hash_are_independent(monkeypatch):
    first_user = _user()
    other_user = _user(user_id=UUID(SECOND))
    known = {
        "content": json.dumps(
            {
                "harnesses": {},
                "lockfile_hash": "",
                "pinned_versions": {"agents": [{"id": FIRST}]},
                "drift": {},
                "hash_schema_version": 1,
            }
        )
    }

    async def scoped_query(sql, params):
        return ClickHouseResponse([known] if params["param_user_id"] == str(first_user.id) else [])

    query = AsyncMock(side_effect=scoped_query)
    insert = AsyncMock()
    monkeypatch.setattr("services.clickhouse.client._query", query)
    monkeypatch.setattr("services.clickhouse.insert.insert_layer_snapshot", insert)
    monkeypatch.setattr(layer_snapshot, "_ensure_layer_components", AsyncMock())
    request = layer_snapshot.LayerSnapshotRequest(hash="0123456789abcdef", pinned_versions={"agents": [{"id": SECOND}]})
    response = await _UPLOAD(request, SimpleNamespace(), other_user, AsyncMock())
    assert response.stored
    assert "identity_status" not in json.loads(insert.await_args.args[0]["content"])
    assert query.await_args.args[1]["param_user_id"] == str(other_user.id)


@pytest.mark.asyncio
async def test_snapshot_reads_reject_conflicts_instead_of_choosing_a_winner(monkeypatch):
    first = {
        "hash": "0123456789abcdef",
        "harness": "pi",
        "content": json.dumps({"harnesses": {}, "pinned_versions": {"id": FIRST}}),
    }
    second = {**first, "content": json.dumps({"harnesses": {}, "pinned_versions": {"id": SECOND}})}
    query = AsyncMock(return_value=ClickHouseResponse([first, second]))
    monkeypatch.setattr("services.clickhouse.client._query", query)
    response = await _request(_app(), "GET", "/api/v1/layer-snapshots/0123456789abcdef")
    assert response.status_code == 409
    assert query.await_args.args[1]["param_user_id"] == str(_user().id)
    response = await _request(_app(), "GET", "/api/v1/layer-snapshots/0123456789abcdef/diff/fedcba9876543210")
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_version_impact_lookup_is_user_scoped_and_rejects_legacy_conflicts(monkeypatch):
    query = AsyncMock(
        return_value=ClickHouseResponse(
            [
                {"hash": "v2_" + "a" * 60, "content": json.dumps({"harnesses": {}})},
                {"hash": "v2_" + "b" * 60, "content": json.dumps({"identity_status": "identity_conflict"})},
            ]
        )
    )
    monkeypatch.setattr(version_impact, "get_query", lambda: query)
    snapshots = await version_impact.fetch_layer_snapshots_for_groups(
        "default", str(_user().id), ["v2_" + "a" * 60, "v2_" + "b" * 60]
    )
    assert set(snapshots) == {"v2_" + "a" * 60}
    assert "user_id = {user_id:String}" in query.await_args.args[0]
    assert query.await_args.args[1]["param_user_id"] == str(_user().id)


@pytest.mark.asyncio
async def test_conflict_marker_insert_preserves_explicit_newer_version(monkeypatch):
    query = AsyncMock(return_value=SimpleNamespace(raise_for_status=lambda: None))
    monkeypatch.setattr("services.clickhouse.client._query", query)
    row = {
        "hash": "legacy",
        "project_id": "default",
        "user_id": str(_user().id),
        "harness": "pi",
        "content": "{}",
        "file_count": 0,
        "total_size": 0,
        "lockfile_hash": "",
        "uploaded_at": "2026-07-01 12:00:00.002",
    }
    await ch_insert.insert_layer_snapshot(row)
    assert "lockfile_hash, uploaded_at) FORMAT JSONEachRow" in query.await_args.args[0]
    assert json.loads(query.await_args.kwargs["data"])["uploaded_at"] == row["uploaded_at"]


@pytest.mark.asyncio
async def test_indexing_helper_soft_fails_when_extractor_is_unavailable(monkeypatch):
    # Phase 1.5 supplies the real extractor; Phase 1.3 must remain fail-open.
    import sys
    from types import ModuleType

    package = ModuleType("services.layer_components")
    package.__path__ = []
    extractor = ModuleType("services.layer_components.extractor")
    extractor.ensure_layer_components = AsyncMock(side_effect=RuntimeError("index unavailable"))
    monkeypatch.setitem(sys.modules, package.__name__, package)
    monkeypatch.setitem(sys.modules, extractor.__name__, extractor)
    await layer_snapshot._ensure_layer_components("default", str(_user().id), "legacy-hash")
    extractor.ensure_layer_components.assert_awaited_once_with("default", str(_user().id), "legacy-hash")
