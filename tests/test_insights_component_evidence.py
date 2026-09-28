# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Component interpretations use published call names, never user prompts."""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.insights import component_evidence as evidence


@pytest.fixture
def report():
    return SimpleNamespace(
        project_id="default",
        component_type="mcp",
        component_id=uuid.uuid4(),
        component_version_id=None,
        triggered_by=uuid.uuid4(),
        period_start="start",
        period_end="end",
    )


def _result(report, **changes):
    return {
        "subject_id": str(report.component_id),
        "subject_version_id": None,
        "findings": [
            {
                "kind": "workflow",
                "insight": "The published search call invoked this MCP.",
                "confidence": "low",
                "evidence_refs": ["s0-call0"],
            }
        ],
    } | changes


@pytest.mark.asyncio
async def test_sample_never_fetches_user_prompts_or_tool_arguments(monkeypatch, report):
    async def published(*args, **kwargs):
        assert kwargs["limit"] == evidence.MAX_SESSIONS
        return (
            [
                {
                    "user_id": "alice",
                    "harness": "claude-code",
                    "session_id": "shared",
                    "source_state": "available",
                    "projection_state": "complete",
                    "observed_calls": 1,
                    "result_states": {"success": 0, "error": 0, "unknown": 1},
                    "source_references": [
                        {
                            "source_line_offset": 1,
                            "source_block_key": "id:call",
                            "result_state": "unknown",
                            "_source_line_hash": "proof-hash",
                        }
                    ],
                }
            ],
            False,
        )

    query = AsyncMock(
        return_value=SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "data": [
                    {
                        "raw_line": json.dumps(
                            {
                                "type": "assistant",
                                "message": {
                                    "content": [
                                        {
                                            "type": "tool_use",
                                            "id": "call",
                                            "name": "mcp__probe__search",
                                            "input": {"token": "private argument"},
                                        }
                                    ]
                                },
                            }
                        )
                    }
                ]
            },
        )
    )

    async def model(prompt, **_kwargs):
        assert "private argument" not in prompt
        assert "mcp__probe__search" in prompt
        assert "s0-goal" not in prompt
        return _result(report)

    monkeypatch.setattr(evidence, "activity_evidence_sample", published)
    monkeypatch.setattr(evidence, "get_query", lambda: query)
    monkeypatch.setattr(evidence, "get_call_model", lambda: model)
    result = await evidence.component_findings(report)
    assert result["state"] == "assessed"
    assert result["evidence"] == {"s0-call0": "mcp__probe__search (result: unknown)"}
    assert query.await_count == 1
    assert query.await_args.args[1]["param_user_id"] == "alice"
    assert query.await_args.args[1]["param_expected_hash"] == "proof-hash"


@pytest.mark.asyncio
async def test_unattributed_session_does_not_fetch_prompt_or_infer_use(monkeypatch, report):
    monkeypatch.setattr(
        evidence,
        "activity_evidence_sample",
        AsyncMock(
            return_value=(
                [
                    {
                        "user_id": "other",
                        "harness": "claude-code",
                        "session_id": "one",
                        "source_state": "available",
                        "projection_state": "complete",
                        "observed_calls": 0,
                        "source_references": [],
                    }
                ],
                False,
            )
        ),
    )
    query = AsyncMock()
    model = AsyncMock()
    monkeypatch.setattr(evidence, "get_query", lambda: query)
    monkeypatch.setattr(evidence, "get_call_model", lambda: model)
    assert (await evidence.component_findings(report))["state"] == "unknown"
    query.assert_not_awaited()
    model.assert_not_awaited()


def test_inference_and_opportunity_without_user_goal_are_rejected(report):
    for kind in ("inferred_use", "opportunity"):
        with pytest.raises(ValueError):
            evidence._validate(
                _result(
                    report,
                    findings=[
                        {
                            "kind": kind,
                            "insight": "This component may be useful here.",
                            "confidence": "low",
                            "evidence_refs": ["s0-call0"],
                        }
                    ],
                ),
                str(report.component_id),
                None,
                {"s0-call0": "search (result: unknown)"},
            )


@pytest.mark.parametrize(
    "insight",
    [
        "The search completed successfully for the user.",
        "The task was fixed by the tool.",
        "No calls were recorded for this work.",
        "This tool saved the team money.",
        "We saw 100 calls for this request.",
    ],
)
def test_unsupported_model_claims_are_rejected(report, insight):
    with pytest.raises(ValueError):
        evidence._validate(
            _result(
                report,
                findings=[
                    {
                        "kind": "workflow",
                        "insight": insight,
                        "confidence": "low",
                        "evidence_refs": ["s0-call0"],
                    }
                ],
            ),
            str(report.component_id),
            None,
            {"s0-call0": "search (result: unknown)"},
        )


def test_known_error_requires_matching_published_result(report):
    valid = _result(
        report,
        findings=[
            {
                "kind": "friction",
                "insight": "A published call returned an error; task outcome remains unknown.",
                "confidence": "low",
                "evidence_refs": ["s0-call0"],
            }
        ],
    )
    assert (
        evidence._validate(valid, str(report.component_id), None, {"s0-call0": "search (result: error)"})
        .findings[0]
        .kind
        == "friction"
    )
    with pytest.raises(ValueError):
        evidence._validate(valid, str(report.component_id), None, {"s0-call0": "search (result: success)"})


@pytest.mark.asyncio
async def test_bad_model_output_repairs_once_and_redacts_result(monkeypatch, report):
    sample = (
        [{"refs": ["s0-call0"], "excerpts": {"s0-call0": "search (result: unknown)"}}],
        {"s0-call0": "search (result: unknown)"},
        False,
    )
    monkeypatch.setattr(evidence, "_sample", AsyncMock(return_value=sample))
    model = AsyncMock(side_effect=[_result(report, subject_id="wrong"), _result(report)])
    monkeypatch.setattr(evidence, "get_call_model", lambda: model)
    assert (await evidence.component_findings(report))["state"] == "assessed"
    assert model.await_count == 2
    model = AsyncMock(
        return_value=_result(
            report,
            findings=[
                {
                    "kind": "workflow",
                    "insight": "The search accompanied api_key=supersecret123 during the task.",
                    "confidence": "low",
                    "evidence_refs": ["s0-call0"],
                }
            ],
        )
    )
    monkeypatch.setattr(evidence, "get_call_model", lambda: model)
    result = await evidence.component_findings(report)
    assert "supersecret123" not in str(result)
    assert "**REDACTED**" in result["findings"][0]["insight"]


@pytest.mark.asyncio
async def test_missing_evidence_and_oversized_input_abstain(monkeypatch, report):
    model = AsyncMock()
    monkeypatch.setattr(evidence, "get_call_model", lambda: model)
    monkeypatch.setattr(evidence, "_sample", AsyncMock(return_value=([], {}, True)))
    assert (await evidence.component_findings(report))["state"] == "unknown"
    monkeypatch.setattr(
        evidence,
        "_sample",
        AsyncMock(
            return_value=(
                [{"excerpts": {"s0-call0": "x" * evidence.MAX_PROMPT_CHARS}, "refs": ["s0-call0"]}],
                {"s0-call0": "x"},
                True,
            )
        ),
    )
    assert (await evidence.component_findings(report))["state"] == "unknown"
    model.assert_not_awaited()
