# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""``llm_model_used`` names the models that produced a report's output.

Regression: no generator ever filled it (the agent pipeline returned a hard-coded
empty ``models_used``, and component reports never set it), so every report said
no model was used even when one wrote its findings.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def configured_model(monkeypatch):
    import services.dynamic_settings as ds
    import services.insights as insights

    async def setting(key, *_args, **_kwargs):
        return "us.anthropic.claude-test-v1" if key == "insights.model_sections" else None

    monkeypatch.setattr(ds, "get", setting)
    responses: list[dict] = []
    calls: list[str] = []

    async def litellm(prompt, model, max_tokens=16384):
        calls.append(model)
        return responses.pop(0)

    monkeypatch.setattr(insights, "_call_litellm", litellm)
    return insights, responses, calls


@pytest.mark.asyncio
async def test_only_a_model_that_returned_output_is_recorded(configured_model):
    from services.insights._deps import recording_models

    insights, responses, calls = configured_model
    responses.extend([{}, {"findings": []}])
    with recording_models() as used:
        assert await insights.call_model("p") == {}
        assert used == set(), "a failed or empty call is not provenance"
        await insights.call_model("p", model_override="openai/gpt-test")
    assert used == {"openai/gpt-test"}
    assert calls == ["bedrock/us.anthropic.claude-test-v1", "openai/gpt-test"], "the normalized ID is what is recorded"


@pytest.mark.asyncio
async def test_concurrent_calls_of_one_report_share_its_record(configured_model):
    from services.insights._deps import recording_models

    insights, responses, _calls = configured_model
    responses.extend([{"a": 1}, {"b": 2}])
    with recording_models() as used:
        await asyncio.gather(
            insights.call_model("p", model_override="openai/facets"),
            insights.call_model("p", model_override="openai/sections"),
        )
    assert used == {"openai/facets", "openai/sections"}


@pytest.mark.asyncio
async def test_calls_outside_a_report_are_not_recorded_anywhere(configured_model):
    from services.insights._deps import _models_used, recording_models

    insights, responses, _calls = configured_model
    responses.extend([{"a": 1}, {"b": 2}, {"c": 3}])
    await insights.call_model("p")
    assert _models_used.get() is None
    with recording_models() as first:
        await insights.call_model("p", model_override="openai/one")
    with recording_models() as second:
        await insights.call_model("p", model_override="openai/two")
    assert (first, second) == ({"openai/one"}, {"openai/two"}), "one report never inherits another's models"


def _component_report():
    from models.insight_report import InsightReportStatus

    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid.uuid4(),
        subject_type="component",
        component_type="mcp",
        component_id=uuid.uuid4(),
        component_version_id=None,
        component_version=None,
        triggered_by=uuid.uuid4(),
        status=InsightReportStatus.pending,
        started_at=now,
        period_start=now - timedelta(days=1),
        period_end=now,
        progress_phase="queued",
        error_message=None,
        llm_model_used="stale",
    )


async def _run_component_job(monkeypatch, report, content_fn):
    from services.insights import batch, component_report

    class Session:
        execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: report))
        commit = AsyncMock()
        refresh = AsyncMock()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(batch, "async_session", lambda: Session())
    monkeypatch.setattr(batch, "_reap_stale_reports", AsyncMock(return_value=0))
    monkeypatch.setattr(batch, "_authorize_component_report_job", AsyncMock())
    monkeypatch.setattr(batch, "_update_report_progress", AsyncMock())
    monkeypatch.setattr(component_report, "generate_component_content", content_fn)
    await batch.run_single_report(str(report.id))


CONTENT = {"metrics": {}, "narrative": {}, "coverage": {}, "sessions_analyzed": 1}


@pytest.mark.asyncio
async def test_component_report_records_the_model_that_wrote_its_findings(monkeypatch):
    from models.insight_report import InsightReportStatus
    from services.insights import _deps

    async def content(_report):
        _deps.note_model_used("bedrock/us.anthropic.claude-test-v1")
        return CONTENT

    report = _component_report()
    await _run_component_job(monkeypatch, report, content)
    assert report.status == InsightReportStatus.completed
    assert report.llm_model_used == "bedrock/us.anthropic.claude-test-v1"


@pytest.mark.asyncio
async def test_component_report_without_a_model_answer_records_none(monkeypatch):
    from models.insight_report import InsightReportStatus

    report = _component_report()
    await _run_component_job(monkeypatch, report, AsyncMock(return_value=CONTENT))
    assert report.status == InsightReportStatus.completed
    assert report.llm_model_used is None, "a deterministic or abstained report names no model"
