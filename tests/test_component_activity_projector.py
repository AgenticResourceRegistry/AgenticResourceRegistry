# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""Scoped activity publication and safe present-only alias matching (Phase 2.3)."""

import hashlib
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from services.component_activity import projector
from services.component_activity.matcher import match_invocations
from services.session_parsers.invocation_types import SourceInvocation

_HASH_A = "v2_" + "a" * 60
_HASH_B = "v2_" + "b" * 60
_CANDIDATE = {
    "local_name": "super-probe",
    "component_id": "11111111-1111-4111-8111-111111111111",
    "component_version_id": "22222222-2222-4222-8222-222222222222",
    "identity_status": "resolved",
    "verification_status": "verified",
}
_TIME = datetime(2026, 1, 1, tzinfo=UTC)


def _source(offset: int, blocks: list[dict], layer_hash: str = _HASH_A, line_hash: str | None = None) -> dict:
    return {
        "line_offset": offset,
        "line_hash": line_hash or f"line-{offset}",
        "source_sha256": hashlib.sha256(f"safe-source-{offset}".encode()).hexdigest(),
        "layer_hash": layer_hash,
        "timestamp": "2026-01-01 00:00:00.000",
        "raw_line": json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {"content": blocks},
            }
        ),
        "raw_line_truncated": 0,
        "is_source_record": 1,
    }


def _call(alias: str, call_id: str) -> dict:
    return {"type": "tool_use", "name": f"mcp__{alias}__ping", "id": call_id, "input": {"never_store": "sensitive"}}


def test_exact_present_alias_requires_one_verified_occurrence_and_nonempty_tool():
    call = SourceInvocation(0, "id:safe-id", "mcp__super-probe__ping", "safe-id", _TIME, "unknown")
    assert (
        match_invocations([call], {_HASH_A: [_CANDIDATE]}, {0: _HASH_A}).rows[0]["component_id"]
        == _CANDIDATE["component_id"]
    )
    prefix = match_invocations([call], {_HASH_A: [_CANDIDATE | {"local_name": "super"}]}, {0: _HASH_A})
    assert prefix.rows == ()
    assert prefix.unmatched_count == 1
    for suspect in (
        _CANDIDATE | {"identity_status": "unresolved"},
        _CANDIDATE | {"verification_status": "drifted"},
        _CANDIDATE | {"component_version_id": ""},
    ):
        assert match_invocations([call], {_HASH_A: [suspect]}, {0: _HASH_A}).rows == ()
    collision = match_invocations(
        [call], {_HASH_A: [_CANDIDATE, _CANDIDATE | {"verification_status": "unverified"}]}, {0: _HASH_A}
    )
    assert collision.rows == () and collision.collision_count == 1
    assert match_invocations([call], {_HASH_B: [_CANDIDATE]}, {0: _HASH_A}).unmatched_count == 1
    assert match_invocations([call], {_HASH_A: [_CANDIDATE]}, {0: _HASH_A}).unknown_result_count == 1
    unrelated = SourceInvocation(0, "id:search", "ToolSearch", "search", _TIME, "unknown")
    assert match_invocations([unrelated], {_HASH_A: [_CANDIDATE]}, {0: _HASH_A}).candidate_count == 0
    missing_time = call.__class__(0, "index:0", call.tool_name, "", None, "unknown")
    assert match_invocations([missing_time], {_HASH_A: [_CANDIDATE]}, {0: _HASH_A}).rows == ()
    future_time = call.__class__(0, "index:0", call.tool_name, "", datetime(9999, 1, 1, tzinfo=UTC), "unknown")
    assert match_invocations([future_time], {_HASH_A: [_CANDIDATE]}, {0: _HASH_A}).unmatched_count == 1


def test_published_row_comparison_normalizes_clickhouse_uint64_and_ignores_publication_key():
    stored = {
        "source_line_offset": "0",
        "source_block_key": "id:safe-id",
        "source_line_hash": "safe-line-hash",
        "layer_hash": _HASH_A,
        "component_type": "mcp",
        "component_id": _CANDIDATE["component_id"],
        "component_version_id": _CANDIDATE["component_version_id"],
        "tool_name": "mcp__super-probe__ping",
        "tool_use_id": "safe-id",
        "event_time": "2026-01-01 00:00:00.000",
        "result_state": "unknown",
        "attribution_method": "verified_alias",
        "matcher_version": 1,
        "extractor_version": 2,
    }
    newly_built = stored | {
        "source_line_offset": 0,
        "project_id": "p",
        "session_id": "s",
        "projection_version": 257,
        "event_time": "2026-01-01 00:00:00.000000",
    }
    assert projector._canonical_row(stored) == projector._canonical_row(newly_built)
    assert projector._canonical_row(stored | {"component_id": "other"}) != projector._canonical_row(newly_built)


def test_source_revision_is_sorted_offset_hash_identity_and_declines_incomplete_source():
    rows = [_source(0, []), _source(1, [])]
    revision = projector._source_revision(rows)
    assert revision == hashlib.sha256(b'[[0,"line-0"],[1,"line-1"]]').hexdigest()
    assert projector._source_revision([rows[1]]) is None
    assert projector._source_revision([rows[0], rows[0]]) is None
    assert projector._source_revision([rows[0], rows[1] | {"line_hash": "repaired"}]) != revision
    assert projector._source_revision([rows[0] | {"raw_line_truncated": 1}, rows[1]]) is None
    assert projector._source_revision([rows[0] | {"raw_line": ""}, rows[1]]) is None


@pytest.mark.asyncio
async def test_rows_acknowledged_before_complete_marker_and_never_store_content(monkeypatch):
    sources = [_source(0, [{"type": "text"}, _call("super-probe", "toolu_safe")])]
    writes = []

    async def fake_query(sql, params=None, *, data=None):
        writes.append((sql, data))
        return []

    monkeypatch.setattr(projector, "_query", fake_query)
    monkeypatch.setattr(projector, "_source_rows", AsyncMock(return_value=sources))
    monkeypatch.setattr(projector, "_mapping", AsyncMock(return_value=("complete", 8, [_CANDIDATE])))
    monkeypatch.setattr(projector, "_latest_complete", AsyncMock(return_value=None))
    monkeypatch.setattr(projector, "next_projection_generation", AsyncMock(return_value=21))
    result = await projector.project_session_activity("p", "u", "claude-code", "s")
    assert result["status"] == "complete" and result["candidate_count"] == 1
    assert result["attributed_count"] == 1 and result["unknown_result_count"] == 1
    assert result["layer_stability"] == "sender_cached_hash_not_proven_stable"
    assert len(writes) == 2 and "INSERT INTO component_activity " in writes[0][0]
    assert "INSERT INTO component_activity_publications " in writes[1][0]
    activity = json.loads(writes[0][1])
    marker = json.loads(writes[1][1])
    assert activity["source_line_offset"] == 0 and activity["source_block_key"] == "id:toolu_safe"
    assert activity["component_id"] == _CANDIDATE["component_id"]
    assert activity["source_line_hash"] == sources[0]["source_sha256"]
    assert activity["projection_version"] == marker["projection_version"] == 257
    assert marker["status"] == "complete" and marker["projection_generation"] == 21
    assert "never_store" not in writes[0][1] + writes[1][1]
    assert "raw_line" not in activity and "input" not in activity and "content" not in activity
    assert projector._source_rows.await_count == 2


@pytest.mark.asyncio
async def test_zero_call_publication_and_matcher_only_bump_rebuilds_even_without_rows(monkeypatch):
    sources = [_source(0, [{"type": "text"}])]
    writes = []
    previous = {257: None, 258: None}

    async def fake_query(sql, params=None, *, data=None):
        writes.append(json.loads(data))
        return []

    async def latest(_params, version):
        return previous[version]

    monkeypatch.setattr(projector, "_query", fake_query)
    monkeypatch.setattr(projector, "_source_rows", AsyncMock(return_value=sources))
    monkeypatch.setattr(projector, "_mapping", AsyncMock(return_value=("complete", 8, [])))
    monkeypatch.setattr(projector, "_latest_complete", latest)
    monkeypatch.setattr(projector, "_published_rows", AsyncMock(return_value=[]))
    monkeypatch.setattr(projector, "next_projection_generation", AsyncMock(side_effect=[21, 22]))
    first = await projector.project_session_activity("p", "u", "claude-code", "s")
    previous[257] = {
        "projection_generation": 21,
        "source_revision": first["source_revision"],
        **{
            key: first[key]
            for key in (
                "candidate_count",
                "attributed_count",
                "collision_count",
                "unmatched_count",
                "unknown_result_count",
            )
        },
    }
    assert (await projector.project_session_activity("p", "u", "claude-code", "s"))["status"] == "already_complete"
    monkeypatch.setattr(projector, "MATCHER_VERSION", 2)
    bumped = await projector.project_session_activity("p", "u", "claude-code", "s")
    assert first["publication_version"] == 257 and bumped["publication_version"] == 258
    assert [marker["status"] for marker in writes] == ["complete", "complete"]
    assert [marker["attributed_count"] for marker in writes] == [0, 0]
    monkeypatch.setattr(projector, "MATCHER_VERSION", 256)
    with pytest.raises(ValueError):
        projector.publication_version()


@pytest.mark.asyncio
async def test_missing_mapping_and_unstable_or_legacy_hashes_do_not_publish(monkeypatch):
    source = [_source(0, [_call("super-probe", "safe")])]
    writes = AsyncMock()
    monkeypatch.setattr(projector, "_query", writes)
    monkeypatch.setattr(projector, "_source_rows", AsyncMock(return_value=source))
    monkeypatch.setattr(projector, "_mapping", AsyncMock(return_value=("pending_mapping", 0, [])))
    pending = await projector.project_session_activity("p", "u", "claude-code", "s")
    assert pending["status"] == "pending_mapping"
    assert pending["candidate_count"] == pending["unmatched_count"] == 1
    assert writes.await_count == 0
    assert (await projector.project_session_activity("p", "u", "pi", "s"))["status"] == "unsupported"
    assert writes.await_count == 0
    source[0]["layer_hash"] = "legacy"
    assert (await projector.project_session_activity("p", "u", "claude-code", "s"))["status"] == "legacy_layer"
    source[0]["layer_hash"] = _HASH_A
    source.append(_source(1, [], ""))
    assert (await projector.project_session_activity("p", "u", "claude-code", "s"))["status"] == "unstable_layer"
    assert writes.await_count == 0


@pytest.mark.asyncio
async def test_late_source_repair_marks_generation_failed_not_complete(monkeypatch):
    source = [_source(0, [_call("super-probe", "safe")])]
    repaired = [source[0] | {"line_hash": "repaired"}]
    writes = []

    async def fake_query(sql, params=None, *, data=None):
        writes.append((sql, json.loads(data)))
        return []

    monkeypatch.setattr(projector, "_query", fake_query)
    monkeypatch.setattr(projector, "_source_rows", AsyncMock(side_effect=[source, repaired]))
    monkeypatch.setattr(projector, "_mapping", AsyncMock(return_value=("complete", 8, [_CANDIDATE])))
    monkeypatch.setattr(projector, "_latest_complete", AsyncMock(return_value=None))
    monkeypatch.setattr(projector, "next_projection_generation", AsyncMock(return_value=55))
    with pytest.raises(RuntimeError, match="Canonical source or published layer mapping changed"):
        await projector.project_session_activity("p", "u", "claude-code", "s")
    assert len(writes) == 2
    assert writes[1][1]["status"] == "failed" and writes[1][1]["projection_generation"] == 55


@pytest.mark.asyncio
async def test_source_layer_change_during_publication_fails_even_when_line_hash_is_unchanged(monkeypatch):
    original = _source(0, [_call("super-probe", "safe")], _HASH_A)
    changed = original | {"layer_hash": _HASH_B}
    writes = []

    async def fake_query(sql, params=None, *, data=None):
        writes.append((sql, json.loads(data)))
        return []

    monkeypatch.setattr(projector, "_query", fake_query)
    monkeypatch.setattr(projector, "_source_rows", AsyncMock(side_effect=[[original], [changed]]))
    monkeypatch.setattr(projector, "_mapping", AsyncMock(return_value=("complete", 8, [_CANDIDATE])))
    monkeypatch.setattr(projector, "_latest_complete", AsyncMock(return_value=None))
    monkeypatch.setattr(projector, "next_projection_generation", AsyncMock(return_value=56))
    with pytest.raises(RuntimeError, match="Canonical source or published layer mapping changed"):
        await projector.project_session_activity("p", "u", "claude-code", "s")
    assert writes[-1][1]["status"] == "failed" and writes[-1][1]["projection_generation"] == 56
    assert not any(data["status"] == "complete" for sql, data in writes if "publications" in sql)


@pytest.mark.asyncio
async def test_two_verified_source_line_hashes_split_only_with_scoped_mapping(monkeypatch):
    source = [
        _source(0, [_call("super-probe", "first")], _HASH_A),
        _source(1, [_call("super-probe", "second")], _HASH_B),
    ]
    writes = []

    async def fake_query(sql, params=None, *, data=None):
        writes.extend(json.loads(line) for line in data.splitlines())
        return []

    async def mapping(_project, _user, layer_hash, _harness):
        return "complete", 8 if layer_hash == _HASH_A else 9, [_CANDIDATE | {"component_id": layer_hash}]

    monkeypatch.setattr(projector, "_query", fake_query)
    monkeypatch.setattr(projector, "_source_rows", AsyncMock(return_value=source))
    monkeypatch.setattr(projector, "_mapping", mapping)
    monkeypatch.setattr(projector, "_latest_complete", AsyncMock(return_value=None))
    monkeypatch.setattr(projector, "next_projection_generation", AsyncMock(return_value=55))
    result = await projector.project_session_activity("p", "u", "claude-code", "s")
    assert result["status"] == "complete" and result["attributed_count"] == 2
    assert [row["layer_hash"] for row in writes[:2]] == [_HASH_A, _HASH_B]
    assert [row["component_id"] for row in writes[:2]] == [_HASH_A, _HASH_B]


def test_tool_remainder_containing_double_underscore_is_an_unresolvable_split():
    # An unregistered server "super-probe__shadow" is indistinguishable from
    # registry alias "super-probe" with tool "shadow__ping": never attribute.
    call = SourceInvocation(0, "id:x", "mcp__super-probe__shadow__ping", "x", _TIME, "unknown")
    result = match_invocations([call], {_HASH_A: [_CANDIDATE]}, {0: _HASH_A})
    assert result.rows == () and result.collision_count == 1
    alias_with_separator = _CANDIDATE | {"local_name": "super-probe__shadow"}
    exact = match_invocations([call], {_HASH_A: [alias_with_separator]}, {0: _HASH_A})
    assert len(exact.rows) == 1  # the full remainder "ping" is unambiguous for that alias


@pytest.mark.asyncio
async def test_oversized_source_is_declined_before_raw_lines_are_loaded(monkeypatch):
    queries = []

    async def fake_query(sql, params=None, *, data=None):
        queries.append(sql)
        return [{"records": 10, "bytes": projector.MAX_SOURCE_BYTES + 1}]

    monkeypatch.setattr(projector, "_query", fake_query)
    result = await projector.project_session_activity("p", "u", "claude-code", "s")
    assert result["status"] == "source_too_large"
    assert len(queries) == 1 and "sum(content_length)" in queries[0]
    assert "SELECT line_offset" not in queries[0]


@pytest.mark.asyncio
async def test_current_snapshot_conflict_invalidates_an_earlier_complete_mapping(monkeypatch):
    responses = {
        "layer_component_extractions": [{"extraction_generation": 8, "conflict": 0}],
        "layer_snapshots": [{"conflict": 1}],
    }

    async def fake_query(sql, params=None, *, data=None):
        for table, rows in responses.items():
            if f"FROM {table}" in sql:
                assert params["param_user_id"] == "u" and params["param_project_id"] == "p"
                return rows
        raise AssertionError("candidates must not be loaded for a conflicted snapshot")

    monkeypatch.setattr(projector, "_query", fake_query)
    assert await projector._mapping("p", "u", _HASH_A, "claude-code") == ("identity_conflict", 8, [])
    responses["layer_snapshots"] = []
    assert (await projector._mapping("p", "u", _HASH_A, "claude-code"))[0] == "pending_mapping"
