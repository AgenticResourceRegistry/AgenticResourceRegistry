# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Codex 0.163.0 native MCP records: execute vs denial, spoofing and identity."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from services.component_activity import evidence_projector, mcp_projector, projector
from services.component_activity.matcher import match_invocations
from services.session_parsers.codex_mcp_invocations import CodexMCPInvocationExtractor
from services.session_parsers.invocations import extract_invocations

_FIXTURE = Path(__file__).parent / "fixtures/component_insights/codex/mcp_completed.jsonl"
# Structurally derived from the 0.163.0 native executed-error control; the body is synthetic.
_ERROR_FIXTURE = Path(__file__).parent / "fixtures/component_insights/codex/mcp_executed_error.jsonl"
_HASH = "v2_" + "a" * 60
_CANDIDATE = {
    "local_name": "matrix-mcp-probe",
    "component_id": "11111111-1111-4111-8111-111111111111",
    "component_version_id": "22222222-2222-4222-8222-222222222222",
    "identity_status": "resolved",
    "verification_status": "verified",
}


def _rows(*lines: dict) -> list[dict]:
    return [
        {
            "is_source_record": 1,
            "line_offset": offset,
            "raw_line": json.dumps(record),
            "timestamp": "2026-10-17T18:00:01Z",
        }
        for offset, record in enumerate(lines)
    ]


def _fixture() -> tuple[dict, dict]:
    return tuple(json.loads(line) for line in _FIXTURE.read_text().splitlines())


def _error_fixture() -> tuple[dict, dict]:
    return tuple(json.loads(line) for line in _ERROR_FIXTURE.read_text().splitlines())


def _source(*lines: dict) -> list[dict]:
    rows = _rows(*lines)
    for row in rows:
        row["layer_hash"] = _HASH
        row["line_hash"] = f"native-{row['line_offset']}"
        row["source_sha256"] = hashlib.sha256(row["raw_line"].encode()).hexdigest()
        row["raw_line_truncated"] = 0
    return rows


def test_completed_native_item_has_harness_server_identity_without_arguments_or_result_content():
    extracted = CodexMCPInvocationExtractor().extract(_rows(*_fixture()))
    assert extracted.status == "supported" and extracted.malformed_source_records == 0
    assert len(extracted.invocations) == 1
    call = extracted.invocations[0]
    assert (
        call.source_line_offset,
        call.source_block_key,
        call.mcp_server,
        call.tool_name,
        call.result_state,
    ) == (1, "id:call_001", "matrix-mcp-probe", "ping", "success")
    assert call.event_time == datetime(2026, 10, 17, 18, 0, 1, tzinfo=UTC)
    private = json.loads(json.dumps(_fixture()[1]))
    private["payload"]["item"]["arguments"] = {"secret": "PRIVATE_SENTINEL"}
    private["payload"]["item"]["result"]["content"] = [{"text": "PRIVATE_SENTINEL"}]
    assert "PRIVATE_SENTINEL" not in str(extract_invocations("codex", _rows(_fixture()[0], private)))
    matched = match_invocations(extracted.invocations, {_HASH: [_CANDIDATE]}, {1: _HASH})
    assert len(matched.rows) == 1
    assert matched.rows[0]["attribution_method"] == "verified_server"
    assert matched.rows[0]["result_state"] == "success"
    assert (
        match_invocations(extracted.invocations, {_HASH: [_CANDIDATE | {"local_name": "matrix"}]}, {1: _HASH}).rows
        == ()
    )
    assert (
        match_invocations(
            extracted.invocations, {_HASH: [_CANDIDATE | {"verification_status": "drifted"}]}, {1: _HASH}
        ).rows
        == ()
    )
    collision = match_invocations(extracted.invocations, {_HASH: [_CANDIDATE, _CANDIDATE]}, {1: _HASH})
    assert not collision.rows and collision.collision_count == 1


def test_native_executed_error_is_attributed_without_leaking_its_result_body():
    meta, error = _error_fixture()
    extracted = extract_invocations("codex", _rows(meta, error))
    assert extracted.status == "supported" and len(extracted.invocations) == 1
    call = extracted.invocations[0]
    assert (call.mcp_server, call.tool_name, call.tool_use_id, call.result_state) == (
        "matrix-mcp-probe",
        "ping_fail",
        "call_error_001",
        "error",
    )
    matched = match_invocations(extracted.invocations, {_HASH: [_CANDIDATE]}, {1: _HASH})
    assert len(matched.rows) == 1 and matched.rows[0]["result_state"] == "error"
    assert "SANITIZED_TEST_ERROR_BODY" not in str(extracted)
    assert "SANITIZED_TEST_ERROR_BODY" not in str(matched)
    assert match_invocations(extracted.invocations, {_HASH: [_CANDIDATE, _CANDIDATE]}, {1: _HASH}).rows == ()


def test_no_tool_is_supported_but_denied_or_incomplete_call_is_not_measured_zero():
    meta, event = _fixture()
    extractor = CodexMCPInvocationExtractor()
    assert extract_invocations("codex", _rows(meta)).status == "supported"
    assert extractor.extract(_rows(meta)).invocations == ()
    item = event["payload"]["item"]
    for replacement in (
        {"status": "failed", "result": None},  # approval denied: no server execution
        {"status": "completed", "result": {}},
        {"status": "completed", "result": {"isError": "false"}},
        {"status": "failed", "result": {"isError": False}},  # contradictory native outcome
        {"status": "completed", "result": {"isError": True}},  # not the observed error shape
        {"server": ""},
        {"tool": ""},
        {"id": None},
    ):
        changed = json.loads(json.dumps(event))
        changed["payload"]["item"].update(replacement)
        assert extractor.extract(_rows(meta, changed)).status == "unsupported"
    error = _error_fixture()[1]
    assert extractor.extract(_rows(meta, error)).invocations[0].result_state == "error"
    # A denied call anywhere in a mixed session prevents a misleading complete denominator.
    assert extractor.extract(_rows(meta, event, error)).status == "supported"
    denied = json.loads(json.dumps(error))
    denied["payload"]["item"].update({"id": "call_denied", "result": None})
    assert extractor.extract(_rows(meta, event, denied)).status == "unsupported"
    assert extractor.extract(_rows(meta, error, error)).status == "unsupported"
    for bad_time in (None, "not-a-date", "2100-01-01T00:00:00Z"):
        untimed = json.loads(json.dumps(event))
        untimed["timestamp"] = bad_time
        assert extractor.extract(_rows(meta, untimed)).status == "unsupported"
    started = json.loads(json.dumps(event))
    started["payload"]["type"] = "item_started"
    assert extractor.extract(_rows(meta, started)).status == "unsupported"
    assert extractor.extract(_rows(meta, started, event)).status == "supported"
    changed_lifecycle = json.loads(json.dumps(event))
    changed_lifecycle["payload"]["type"] = "item_updated"
    assert extractor.extract(_rows(meta, changed_lifecycle)).status == "unsupported"
    assert extractor.extract(_rows(meta, event, event)).status == "unsupported"


@pytest.mark.asyncio
async def test_scoped_codex_projection_publishes_only_executed_verified_calls(monkeypatch):
    meta, event = _fixture()
    rows = _source(meta, event)
    writes: list[tuple[str, dict]] = []

    async def query(sql, params=None, *, data=None):
        if data is not None:
            writes.extend((sql, json.loads(line)) for line in data.splitlines())
        return []

    for rail in (projector, evidence_projector):
        monkeypatch.setattr(rail, "_query", query)
        monkeypatch.setattr(rail, "_source_rows", AsyncMock(return_value=rows))
        monkeypatch.setattr(rail, "_mapping", AsyncMock(return_value=("complete", 8, [_CANDIDATE])))
        monkeypatch.setattr(rail, "_latest_complete", AsyncMock(return_value=None))
        monkeypatch.setattr(rail, "next_projection_generation", AsyncMock(return_value=55))

    matched = await mcp_projector.project_session_activity("p", "u", "codex", "s")
    assert matched["status"] == "complete" and matched["candidate_count"] == matched["attributed_count"] == 1
    activity = [row for sql, row in writes if "INSERT INTO component_activity " in sql]
    assert len(activity) == 1
    assert activity[0]["attribution_method"] == "verified_server"
    assert activity[0]["result_state"] == "success" and activity[0]["tool_name"] == "ping"
    assert "arguments" not in activity[0] and "result" not in activity[0]

    writes.clear()
    error = _error_fixture()[1]
    rows[:] = _source(meta, error)
    executed_error = await mcp_projector.project_session_activity("p", "u", "codex", "s-error")
    assert executed_error["status"] == "complete"
    assert executed_error["candidate_count"] == executed_error["attributed_count"] == 1
    activity = [row for sql, row in writes if "INSERT INTO component_activity " in sql]
    assert len(activity) == 1 and activity[0]["result_state"] == "error"
    assert "SANITIZED_TEST_ERROR_BODY" not in str(writes)

    writes.clear()
    rows[:] = _source(meta)
    no_tool = await mcp_projector.project_session_activity("p", "u", "codex", "s2")
    assert no_tool["status"] == "complete" and no_tool["candidate_count"] == 0
    assert not any("INSERT INTO component_activity " in sql for sql, _ in writes)

    writes.clear()
    denied = json.loads(json.dumps(event))
    denied["payload"]["item"].update({"status": "failed", "result": None})
    rows[:] = _source(meta, denied)
    assert (await mcp_projector.project_session_activity("p", "u", "codex", "s3"))["status"] == "unsupported"
    assert not any("INSERT INTO component_activity" in sql for sql, _ in writes)


@pytest.mark.parametrize("version", ["0.163.0", "0.164.0"])
def test_tested_native_version_supports_both_success_and_executed_error(version):
    meta, success = _fixture()
    _unused, error = _error_fixture()
    meta["payload"]["cli_version"] = version
    extracted = extract_invocations("codex", _rows(meta, success, error))
    assert extracted.status == "supported"
    assert [call.result_state for call in extracted.invocations] == ["success", "error"]
    denied = json.loads(json.dumps(error))
    denied["payload"]["item"].update({"result": None, "id": "call_denied"})
    assert extract_invocations("codex", _rows(meta, success, denied)).status == "unsupported"


def test_only_native_item_completed_and_exact_tested_version_are_supported():
    meta, event = _fixture()
    extractor = CodexMCPInvocationExtractor()
    for record in (
        {**meta, "payload": {**meta["payload"], "cli_version": "0.165.0"}},
        {**meta, "payload": {**meta["payload"], "cwd": "relative/path"}},
    ):
        assert extractor.extract(_rows(record, event)).status == "unsupported"
    assert extractor.extract(_rows(meta, meta, event)).status == "unsupported"
    assert extractor.extract(_rows(event)).status == "unsupported"
    # A pasted/tool-generated lookalike is not an event_msg issued by Codex.
    for record_type in ("response_item", "message", "user"):
        impostor = {**event, "type": record_type}
        assert extractor.extract(_rows(meta, impostor)).invocations == ()
    row = _rows(meta, event)
    row[1]["raw_line_truncated"] = 1
    assert extractor.extract(row).status == "unsupported"
    row = _rows(meta, event)
    row[1]["raw_line"] = "{invalid"
    assert extractor.extract(row).status == "unsupported"


@pytest.mark.parametrize("alias", ["matrix-mcp-probe", "matrix-mcp-probe-2"])
def test_exact_server_name_alone_is_not_a_verified_component(alias):
    meta, event = _fixture()
    event["payload"]["item"]["server"] = alias
    extracted = CodexMCPInvocationExtractor().extract(_rows(meta, event))
    result = match_invocations(extracted.invocations, {_HASH: [_CANDIDATE]}, {1: _HASH})
    assert bool(result.rows) is (alias == _CANDIDATE["local_name"])
