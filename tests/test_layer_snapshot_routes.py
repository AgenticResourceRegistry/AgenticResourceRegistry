# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Focused coverage for the layer snapshot routes."""

from __future__ import annotations

import inspect
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from api.deps import get_current_user, get_db
from api.ratelimit import limiter
from api.routes import layer_snapshot
from models.user import UserRole
from observal_shared.migration.constants import DEFAULT_PROJECT_ID

USER_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
HASH_A = "0123456789abcdef"
HASH_B = "fedcba9876543210"
LOCK_HASH = "1122334455667788"

_CHECK_SQL = """
    SELECT content, uploaded_at
    FROM layer_snapshots
    WHERE project_id = {project_id:String}
      AND user_id = {user_id:String}
      AND hash = {hash:String}
    ORDER BY uploaded_at DESC
    LIMIT 1000
    FORMAT JSON
"""
_GET_SQL = """
    SELECT hash, harness, content, uploaded_at, file_count, total_size, lockfile_hash
    FROM layer_snapshots
    WHERE project_id = {project_id:String}
      AND user_id = {user_id:String}
      AND hash = {hash:String}
    ORDER BY uploaded_at DESC
    LIMIT 1000
    FORMAT JSON
"""
_DIFF_SQL = """
    SELECT hash, content
    FROM layer_snapshots
    WHERE project_id = {project_id:String}
      AND user_id = {user_id:String}
      AND hash IN ({hash_a:String}, {hash_b:String})
    ORDER BY uploaded_at DESC
    LIMIT 2000
    FORMAT JSON
"""
_BASELINE_SQL = """
    INSERT INTO layer_snapshots (hash, project_id, user_id, harness, content, file_count, total_size, lockfile_hash)
    VALUES (
        {hash:String},
        {project_id:String},
        {user_id:String},
        'baseline',
        {content:String},
        0, 0, ''
    )
"""

_UPLOAD = inspect.unwrap(layer_snapshot.upload_layer_snapshot)


class ClickHouseResponse:
    def __init__(self, rows=None, *, error: Exception | None = None, events: list[str] | None = None):
        self.rows = list(rows or [])
        self.error = error
        self.events = events
        self.raise_calls = 0
        self.json_calls = 0

    def raise_for_status(self):
        self.raise_calls += 1
        if self.events is not None:
            self.events.append("check status")
        if self.error is not None:
            raise self.error

    def json(self):
        self.json_calls += 1
        if self.events is not None:
            self.events.append("decode result")
        return {"data": self.rows}


def _compact(sql: str) -> str:
    return " ".join(sql.split())


def _user(*, user_id: uuid.UUID = USER_ID, role: UserRole = UserRole.user):
    return SimpleNamespace(
        id=user_id,
        role=role,
        email="member@example.test",
        username="member",
        auth_provider="local",
    )


def _app(user=None, *, authenticated: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(layer_snapshot.router)
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    if authenticated:
        app.dependency_overrides[get_current_user] = lambda: user or _user()
    return app


async def _request(app: FastAPI, method: str, path: str, **kwargs):
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        return await client.request(method, path, **kwargs)


def _file(
    path: str,
    file_hash: str,
    size: int,
    *,
    source: str = "user",
    content: str = "",
) -> dict:
    return {
        "path": path,
        "hash": file_hash,
        "size": size,
        "source": source,
        "content": content,
    }


@pytest.fixture(autouse=True)
def _disable_rate_limits(monkeypatch):
    enabled = limiter.enabled
    limiter.enabled = False
    # These route tests mock ClickHouse; the separate projection tests exercise
    # the real indexer rather than re-entering these mocked query boundaries.
    monkeypatch.setattr(layer_snapshot, "_ensure_layer_components", AsyncMock())
    yield
    limiter.enabled = enabled


@pytest.fixture
def boundaries(monkeypatch):
    response = ClickHouseResponse()
    query = AsyncMock(return_value=response)
    insert = AsyncMock()
    redact = MagicMock(side_effect=lambda value: f"redacted:{value}")
    monkeypatch.setattr("services.clickhouse.client._query", query)
    monkeypatch.setattr("services.clickhouse.insert.insert_layer_snapshot", insert)
    monkeypatch.setattr("services.secrets_redactor.redact_secrets", redact)
    return SimpleNamespace(response=response, query=query, insert=insert, redact=redact)


@pytest.mark.asyncio
async def test_upload_serializes_redacted_manifest_and_inserts_exact_row_in_order(monkeypatch):
    events: list[str] = []
    check_response = ClickHouseResponse(events=events)

    async def query(sql, params):
        events.append("check duplicate")
        return check_response

    async def insert(row):
        events.append("insert snapshot")

    def redact(value):
        events.append(f"redact {value}")
        return "safe content"

    query_mock = AsyncMock(side_effect=query)
    insert_mock = AsyncMock(side_effect=insert)
    monkeypatch.setattr("services.clickhouse.client._query", query_mock)
    monkeypatch.setattr("services.clickhouse.insert.insert_layer_snapshot", insert_mock)
    monkeypatch.setattr("services.secrets_redactor.redact_secrets", redact)

    payload = layer_snapshot.LayerSnapshotRequest.model_validate(
        {
            "hash": HASH_A,
            "harnesses": {
                "cursor": [
                    _file(
                        "user:mcp.json",
                        "sha256-file-a",
                        17,
                        content="token=secret-value",
                    )
                ],
                "kiro": [
                    _file(
                        "user:agents/reviewer.json",
                        "sha256-file-b",
                        4,
                        source="observal",
                    )
                ],
            },
            "lockfile_hash": LOCK_HASH,
            "pinned_versions": {"agents": [{"id": "agent-1", "version": "1.2.3"}]},
            "drift": {"is_canonical": False, "drifted_files": [{"path": "user:mcp.json"}]},
        }
    )

    response = await _UPLOAD(payload, SimpleNamespace(), _user(), AsyncMock())

    assert response.model_dump() == {"stored": True, "hash": HASH_A, "file_count": 2}
    assert events == [
        "redact token=secret-value",
        "check duplicate",
        "check status",
        "decode result",
        "insert snapshot",
    ]
    expected_manifest = {
        "harnesses": {
            "cursor": [
                _file(
                    "user:mcp.json",
                    "sha256-file-a",
                    17,
                    content="safe content",
                )
            ],
            "kiro": [
                _file(
                    "user:agents/reviewer.json",
                    "sha256-file-b",
                    4,
                    source="observal",
                )
            ],
        },
        "lockfile_hash": LOCK_HASH,
        "pinned_versions": {"agents": [{"id": "agent-1", "version": "1.2.3"}]},
        "drift": {"is_canonical": False, "drifted_files": [{"path": "user:mcp.json"}]},
        "hash_schema_version": 1,
    }
    assert query_mock.await_args.args[1] == {
        "param_project_id": DEFAULT_PROJECT_ID,
        "param_user_id": str(USER_ID),
        "param_hash": HASH_A,
    }
    assert _compact(query_mock.await_args.args[0]) == _compact(_CHECK_SQL)
    stored = dict(insert_mock.await_args.args[0])
    assert json.loads(stored.pop("content")) == expected_manifest
    assert stored == {
        "hash": HASH_A,
        "project_id": DEFAULT_PROJECT_ID,
        "user_id": str(USER_ID),
        "harness": "cursor,kiro",
        "file_count": 2,
        "total_size": 21,
        "lockfile_hash": LOCK_HASH,
    }


@pytest.mark.asyncio
async def test_upload_http_response_uses_real_json_and_does_not_mutate_request(boundaries):
    payload = {
        "hash": HASH_A,
        "harnesses": {"cursor": [_file("project:.cursor/rules/review.mdc", "sha256-rule", 8, content="review")]},
    }
    original = json.loads(json.dumps(payload))

    response = await _request(_app(), "POST", "/api/v1/layer-snapshots", json=payload)

    assert response.status_code == 200
    assert response.json() == {"stored": True, "hash": HASH_A, "file_count": 1}
    assert payload == original
    stored = boundaries.insert.await_args.args[0]
    assert json.loads(stored["content"]) == {
        "harnesses": {
            "cursor": [
                _file(
                    "project:.cursor/rules/review.mdc",
                    "sha256-rule",
                    8,
                    content="redacted:review",
                )
            ]
        },
        "lockfile_hash": "",
        "pinned_versions": {},
        "drift": {},
        "hash_schema_version": 1,
    }


@pytest.mark.asyncio
async def test_duplicate_upload_is_a_no_mutation_success(boundaries):
    boundaries.response.rows = [
        {
            "content": json.dumps(
                {
                    "harnesses": {"cursor": [_file("user:mcp.json", "sha256-a", 3, content="redacted:abc")]},
                    "lockfile_hash": "",
                    "pinned_versions": {},
                    "drift": {},
                    "hash_schema_version": 1,
                }
            )
        }
    ]
    payload = {
        "hash": HASH_A,
        "harnesses": {"cursor": [_file("user:mcp.json", "sha256-a", 3, content="abc")]},
    }

    response = await _request(_app(), "POST", "/api/v1/layer-snapshots", json=payload)

    assert response.status_code == 200
    assert response.json() == {"stored": False, "hash": HASH_A, "file_count": 1}
    assert boundaries.response.raise_calls == 1
    boundaries.redact.assert_called_once_with("abc")
    boundaries.insert.assert_not_awaited()
    assert boundaries.query.await_args.args[1] == {
        "param_project_id": DEFAULT_PROJECT_ID,
        "param_user_id": str(USER_ID),
        "param_hash": HASH_A,
    }


@pytest.mark.asyncio
async def test_duplicate_check_failure_fails_closed_without_insert(boundaries):
    boundaries.query.side_effect = RuntimeError("ClickHouse unavailable")
    payload = layer_snapshot.LayerSnapshotRequest(
        hash=HASH_A,
        harnesses={"cursor": [layer_snapshot.LayerFile(path="user:mcp.json", hash="sha256-a", size=3)]},
    )

    with pytest.raises(HTTPException) as exc:
        await _UPLOAD(payload, SimpleNamespace(), _user(), AsyncMock())

    assert exc.value.status_code == 503
    boundaries.insert.assert_not_awaited()
    boundaries.redact.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("file_count", "content_size", "detail"),
    [
        (201, 0, "Snapshot exceeds 200 file limit (201 files)"),
        (11, 500_000, "Snapshot exceeds 5MB total content limit"),
    ],
    ids=["file-count", "total-content"],
)
async def test_upload_caps_fail_before_any_service_call(boundaries, file_count, content_size, detail):
    harnesses = {
        "cursor": [
            layer_snapshot.LayerFile(path=f"file-{index}", hash="h", size=0, content="x" * content_size)
            for index in range(file_count)
        ]
    }
    request = layer_snapshot.LayerSnapshotRequest(hash=HASH_A, harnesses=harnesses)

    with pytest.raises(HTTPException) as exc:
        await _UPLOAD(request, SimpleNamespace(), _user(), AsyncMock())

    assert exc.value.status_code == 422
    assert exc.value.detail == detail
    boundaries.query.assert_not_awaited()
    boundaries.redact.assert_not_called()
    boundaries.insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_accepts_exact_total_content_cap(boundaries):
    files = [
        layer_snapshot.LayerFile(path=f"file-{index}", hash="h", size=524_288, content="x" * 524_288)
        for index in range(10)
    ]

    boundaries.response.rows = [
        {
            "content": json.dumps(
                {
                    "harnesses": {
                        "cursor": [dict(file.model_dump(), content="redacted:" + file.content) for file in files]
                    },
                    "lockfile_hash": "",
                    "pinned_versions": {},
                    "drift": {},
                    "hash_schema_version": 1,
                }
            )
        }
    ]
    response = await _UPLOAD(
        layer_snapshot.LayerSnapshotRequest(hash=HASH_A, harnesses={"cursor": files}),
        SimpleNamespace(),
        _user(),
        AsyncMock(),
    )

    assert response.model_dump() == {"stored": False, "hash": HASH_A, "file_count": 10}
    boundaries.query.assert_awaited_once()
    boundaries.insert.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected_detail"),
    [
        (
            {"hash": "short"},
            [
                {
                    "type": "string_too_short",
                    "loc": ["body", "hash"],
                    "msg": "String should have at least 8 characters",
                    "input": "short",
                    "ctx": {"min_length": 8},
                }
            ],
        ),
        (
            {
                "hash": HASH_A,
                "harnesses": {"cursor": [{"path": "x", "hash": "h", "size": -1}]},
            },
            [
                {
                    "type": "greater_than_equal",
                    "loc": ["body", "harnesses", "cursor", 0, "size"],
                    "msg": "Input should be greater than or equal to 0",
                    "input": -1,
                    "ctx": {"ge": 0},
                }
            ],
        ),
        (
            {"hash": HASH_A, "lockfile_hash": "x" * 65},
            [
                {
                    "type": "string_too_long",
                    "loc": ["body", "lockfile_hash"],
                    "msg": "String should have at most 64 characters",
                    "input": "x" * 65,
                    "ctx": {"max_length": 64},
                }
            ],
        ),
    ],
    ids=["short-hash", "negative-size", "long-lock-hash"],
)
async def test_malformed_uploads_return_exact_validation_contract(boundaries, payload, expected_detail):
    response = await _request(_app(), "POST", "/api/v1/layer-snapshots", json=payload)

    assert response.status_code == 422
    assert response.json() == {"detail": expected_detail}
    boundaries.query.assert_not_awaited()
    boundaries.redact.assert_not_called()
    boundaries.insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_per_file_content_cap_returns_exact_validation_contract(boundaries):
    content = "x" * 524_289
    payload = {
        "hash": HASH_A,
        "harnesses": {"cursor": [{"path": "large", "hash": "h", "size": len(content), "content": content}]},
    }

    response = await _request(_app(), "POST", "/api/v1/layer-snapshots", json=payload)

    assert response.status_code == 422
    assert response.json() == {
        "detail": [
            {
                "type": "string_too_long",
                "loc": ["body", "harnesses", "cursor", 0, "content"],
                "msg": "String should have at most 524288 characters",
                "input": content,
                "ctx": {"max_length": 524_288},
            }
        ]
    }
    boundaries.query.assert_not_awaited()
    boundaries.redact.assert_not_called()
    boundaries.insert.assert_not_awaited()


def test_hash_schemas_enforce_length_but_do_not_require_hexadecimal():
    snapshot = layer_snapshot.LayerSnapshotRequest(hash="not-hex!", lockfile_hash="also-not-hex")
    baseline = layer_snapshot.BaselinePinRequest(agent_id="agent", layer_hash="not-hex!")

    assert snapshot.hash == baseline.layer_hash == "not-hex!"
    assert snapshot.lockfile_hash == "also-not-hex"


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["redaction", "insert"])
async def test_upload_service_failures_return_500_without_false_success(boundaries, stage):
    payload = {
        "hash": HASH_A,
        "harnesses": {"cursor": [_file("user:mcp.json", "sha256-a", 3, content="secret-value")]},
    }
    if stage == "redaction":
        boundaries.redact.side_effect = RuntimeError("redaction unavailable")
    else:
        boundaries.insert.side_effect = RuntimeError("insert unavailable")

    response = await _request(_app(), "POST", "/api/v1/layer-snapshots", json=payload)

    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    if stage == "redaction":
        boundaries.insert.assert_not_awaited()
    else:
        boundaries.insert.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_snapshot_flattens_harnesses_and_prefers_manifest_lock_hash(monkeypatch):
    first = _file("user:mcp.json", "sha256-a", 3, content="one")
    second = _file("project:.kiro/agents/reviewer.json", "sha256-b", 7, source="observal", content="two")
    row = {
        "hash": HASH_A,
        "harness": "stale-column-value",
        "content": json.dumps(
            {
                "harnesses": {"cursor": [first], "kiro": [second]},
                "lockfile_hash": LOCK_HASH,
                "pinned_versions": {"agents": []},
                "drift": {"is_canonical": True},
            }
        ),
        "uploaded_at": "2026-07-01 12:00:00.000",
        "file_count": "2",
        "total_size": "10",
        "lockfile_hash": "stale-lock-column",
    }
    result = ClickHouseResponse([row])
    query = AsyncMock(return_value=result)
    monkeypatch.setattr("services.clickhouse.client._query", query)

    response = await _request(_app(), "GET", f"/api/v1/layer-snapshots/{HASH_A}")

    assert response.status_code == 200
    assert response.json() == {
        "hash": HASH_A,
        "harness": "cursor,kiro",
        "files": [first, second],
        "lockfile_hash": LOCK_HASH,
        "uploaded_at": "2026-07-01 12:00:00.000",
        "file_count": 2,
        "total_size": 10,
    }
    assert result.raise_calls == 1
    assert result.json_calls == 1
    assert _compact(query.await_args.args[0]) == _compact(_GET_SQL)
    assert query.await_args.args[1] == {
        "param_project_id": DEFAULT_PROJECT_ID,
        "param_user_id": str(USER_ID),
        "param_hash": HASH_A,
    }


@pytest.mark.asyncio
async def test_get_snapshot_uses_column_fallbacks_for_an_empty_manifest(monkeypatch):
    query = AsyncMock(
        return_value=ClickHouseResponse(
            [
                {
                    "hash": HASH_B,
                    "harness": "cursor",
                    "content": json.dumps({"harnesses": {}}),
                    "lockfile_hash": LOCK_HASH,
                }
            ]
        )
    )
    monkeypatch.setattr("services.clickhouse.client._query", query)

    response = await _request(_app(), "GET", f"/api/v1/layer-snapshots/{HASH_B}")

    assert response.status_code == 200
    assert response.json() == {
        "hash": HASH_B,
        "harness": "cursor",
        "files": [],
        "lockfile_hash": LOCK_HASH,
        "uploaded_at": "",
        "file_count": 0,
        "total_size": 0,
    }


@pytest.mark.asyncio
async def test_get_snapshot_not_found_has_exact_contract(monkeypatch):
    query = AsyncMock(return_value=ClickHouseResponse())
    monkeypatch.setattr("services.clickhouse.client._query", query)

    response = await _request(_app(), "GET", f"/api/v1/layer-snapshots/{HASH_A}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Layer snapshot not found"}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["query", "status", "content"])
async def test_get_snapshot_database_and_content_failures_return_500(monkeypatch, failure):
    if failure == "query":
        query = AsyncMock(side_effect=RuntimeError("query unavailable"))
    elif failure == "status":
        query = AsyncMock(return_value=ClickHouseResponse(error=RuntimeError("query rejected")))
    else:
        query = AsyncMock(
            return_value=ClickHouseResponse(
                [
                    {
                        "hash": HASH_A,
                        "harness": "cursor",
                        "content": "not-json",
                        "file_count": 0,
                        "total_size": 0,
                    }
                ]
            )
        )
    monkeypatch.setattr("services.clickhouse.client._query", query)

    response = await _request(_app(), "GET", f"/api/v1/layer-snapshots/{HASH_A}")

    assert response.status_code == 500
    assert response.text == "Internal Server Error"


@pytest.mark.asyncio
async def test_diff_snapshots_returns_exact_changes_and_query(monkeypatch):
    unchanged = _file("same.md", "sha256-same", 1)
    before = _file("changed.md", "sha256-old", 2, content="old")
    after = _file("changed.md", "sha256-new", 3, content="new")
    removed = _file("removed.md", "sha256-removed", 4)
    added = _file("added.md", "sha256-added", 5)
    kiro_same = _file("same.md", "sha256-kiro", 6)
    rows = [
        {
            "hash": HASH_B,
            "content": json.dumps({"harnesses": {"cursor": [unchanged, after, added], "kiro": [kiro_same]}}),
        },
        {
            "hash": HASH_A,
            "content": json.dumps({"harnesses": {"cursor": [unchanged, before, removed], "kiro": [kiro_same]}}),
        },
    ]
    result = ClickHouseResponse(rows)
    query = AsyncMock(return_value=result)
    monkeypatch.setattr("services.clickhouse.client._query", query)

    response = await _request(_app(), "GET", f"/api/v1/layer-snapshots/{HASH_A}/diff/{HASH_B}")

    assert response.status_code == 200
    assert response.json() == {
        "added": [added],
        "removed": [removed],
        "modified": [
            {
                "path": "cursor/changed.md",
                "before": before,
                "after": after,
            }
        ],
        "unchanged_count": 2,
    }
    assert result.raise_calls == 1
    assert _compact(query.await_args.args[0]) == _compact(_DIFF_SQL)
    assert query.await_args.args[1] == {
        "param_project_id": DEFAULT_PROJECT_ID,
        "param_user_id": str(USER_ID),
        "param_hash_a": HASH_A,
        "param_hash_b": HASH_B,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rows", "detail"),
    [
        ([{"hash": HASH_B, "content": "{}"}], f"Snapshot {HASH_A} not found"),
        ([{"hash": HASH_A, "content": "{}"}], f"Snapshot {HASH_B} not found"),
    ],
    ids=["first-missing", "second-missing"],
)
async def test_diff_not_found_contract_identifies_the_missing_hash(monkeypatch, rows, detail):
    monkeypatch.setattr("services.clickhouse.client._query", AsyncMock(return_value=ClickHouseResponse(rows)))

    response = await _request(_app(), "GET", f"/api/v1/layer-snapshots/{HASH_A}/diff/{HASH_B}")

    assert response.status_code == 404
    assert response.json() == {"detail": detail}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["query", "status", "content"])
async def test_diff_database_and_content_failures_return_500(monkeypatch, failure):
    if failure == "query":
        query = AsyncMock(side_effect=RuntimeError("query unavailable"))
    elif failure == "status":
        query = AsyncMock(return_value=ClickHouseResponse(error=RuntimeError("query rejected")))
    else:
        query = AsyncMock(return_value=ClickHouseResponse([{"hash": HASH_A, "content": "not-json"}]))
    monkeypatch.setattr("services.clickhouse.client._query", query)

    response = await _request(_app(), "GET", f"/api/v1/layer-snapshots/{HASH_A}/diff/{HASH_B}")

    assert response.status_code == 500
    assert response.text == "Internal Server Error"


@pytest.mark.asyncio
async def test_pin_baseline_serializes_exact_marker_and_query(monkeypatch):
    query = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr("services.clickhouse.client._query", query)
    request = layer_snapshot.BaselinePinRequest(agent_id="agent-123", layer_hash=HASH_A)

    response = await inspect.unwrap(layer_snapshot.pin_baseline)(request, SimpleNamespace(), _user())

    assert response.model_dump() == {"agent_id": "agent-123", "layer_hash": HASH_A, "pinned": True}
    assert _compact(query.await_args.args[0]) == _compact(_BASELINE_SQL)
    params = dict(query.await_args.args[1])
    assert json.loads(params.pop("param_content")) == {
        "agent_id": "agent-123",
        "baseline": True,
        "pinned_hash": HASH_A,
    }
    assert params == {
        "param_hash": "baseline:agent-123",
        "param_project_id": DEFAULT_PROJECT_ID,
        "param_user_id": str(USER_ID),
    }


@pytest.mark.asyncio
async def test_pin_baseline_rejects_oversized_agent_id_before_query(monkeypatch):
    query = AsyncMock()
    monkeypatch.setattr("services.clickhouse.client._query", query)
    agent_id = "a" * 101

    response = await _request(
        _app(),
        "POST",
        "/api/v1/layer-snapshots/baseline",
        json={"agent_id": agent_id, "layer_hash": HASH_A},
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": [
            {
                "type": "string_too_long",
                "loc": ["body", "agent_id"],
                "msg": "String should have at most 100 characters",
                "input": agent_id,
                "ctx": {"max_length": 100},
            }
        ]
    }
    query.assert_not_awaited()


@pytest.mark.asyncio
async def test_pin_baseline_exception_has_exact_500_contract(monkeypatch):
    query = AsyncMock(side_effect=RuntimeError("insert unavailable"))
    monkeypatch.setattr("services.clickhouse.client._query", query)

    response = await _request(
        _app(),
        "POST",
        "/api/v1/layer-snapshots/baseline",
        json={"agent_id": "agent-123", "layer_hash": HASH_A},
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Failed to pin baseline"}
    query.assert_awaited_once()


@pytest.mark.asyncio
async def test_pin_baseline_does_not_check_returned_http_status(monkeypatch):
    result = ClickHouseResponse(error=RuntimeError("HTTP 500"))
    query = AsyncMock(return_value=result)
    monkeypatch.setattr("services.clickhouse.client._query", query)

    response = await _request(
        _app(),
        "POST",
        "/api/v1/layer-snapshots/baseline",
        json={"agent_id": "agent-123", "layer_hash": HASH_A},
    )

    assert response.status_code == 200
    assert response.json() == {"agent_id": "agent-123", "layer_hash": HASH_A, "pinned": True}
    assert result.raise_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("POST", "/api/v1/layer-snapshots", {"hash": HASH_A}),
        ("GET", f"/api/v1/layer-snapshots/{HASH_A}", None),
        ("GET", f"/api/v1/layer-snapshots/{HASH_A}/diff/{HASH_B}", None),
        (
            "POST",
            "/api/v1/layer-snapshots/baseline",
            {"agent_id": "agent-123", "layer_hash": HASH_A},
        ),
    ],
    ids=["upload", "get", "diff", "baseline"],
)
async def test_every_route_requires_bearer_authentication(method, path, payload):
    kwargs = {"json": payload} if payload is not None else {}

    response = await _request(_app(authenticated=False), method, path, **kwargs)

    assert response.status_code == 401
    assert response.json() == {"detail": "Missing credentials"}


@pytest.mark.asyncio
@pytest.mark.parametrize("role", list(UserRole))
async def test_every_authenticated_role_is_authorized_for_snapshot_reads(monkeypatch, role):
    query = AsyncMock(return_value=ClickHouseResponse())
    monkeypatch.setattr("services.clickhouse.client._query", query)

    response = await _request(_app(_user(role=role)), "GET", f"/api/v1/layer-snapshots/{HASH_A}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Layer snapshot not found"}
    query.assert_awaited_once()


def test_router_exposes_only_the_current_snapshot_contract():
    routes = {(frozenset(route.methods), route.path) for route in layer_snapshot.router.routes}

    assert routes == {
        (frozenset({"POST"}), "/api/v1/layer-snapshots"),
        (frozenset({"GET"}), "/api/v1/layer-snapshots/{snapshot_hash}"),
        (frozenset({"GET"}), "/api/v1/layer-snapshots/{hash_a}/diff/{hash_b}"),
        (frozenset({"POST"}), "/api/v1/layer-snapshots/baseline"),
    }


@pytest.mark.asyncio
async def test_identity_check_failure_never_logs_credential_bearing_error_text(boundaries):
    from loguru import logger

    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(str(message)), level="WARNING")
    boundaries.query.side_effect = RuntimeError("GET http://ch:8123/?password=fixture-secret failed")
    payload = layer_snapshot.LayerSnapshotRequest(
        hash=HASH_A,
        harnesses={"cursor": [layer_snapshot.LayerFile(path="user:mcp.json", hash="sha256-a", size=3)]},
    )
    try:
        with pytest.raises(HTTPException):
            await _UPLOAD(payload, SimpleNamespace(), _user(), AsyncMock())
    finally:
        logger.remove(sink)
    assert messages and not any("fixture-secret" in message for message in messages)


def test_drift_verifications_are_bounded_before_extraction():
    ok = {"mcp_verifications": [{"harness": "claude-code", "alias": "a", "status": "verified"}], "extra": {"any": 1}}
    assert layer_snapshot.LayerSnapshotRequest(hash=HASH_A, drift=ok).drift == ok  # stored representation unchanged
    too_many = {"mcp_verifications": [{}] * (layer_snapshot._MAX_MCP_VERIFICATIONS + 1)}
    for drift in (
        too_many,
        {"mcp_verifications": "verified"},
        {"mcp_verifications": [{"alias": "x" * 301}]},
        {"mcp_verifications": [{"alias": ["nested"]}]},
    ):
        with pytest.raises(ValueError):
            layer_snapshot.LayerSnapshotRequest(hash=HASH_A, drift=drift)
