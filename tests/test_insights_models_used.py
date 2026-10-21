# SPDX-FileCopyrightText: 2026 Dheirav <dheirav2005@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""REGRESSION (OBS-1482): a report must record which models wrote it.

``models_used`` was hard-coded to ``[]``, so every report stored
``llm_model_used = None`` even though the narrative came from a model.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.test_insights_facets_transcript import _meta


def _settings(values: dict):
    async def get(key: str):
        return values.get(key, "")

    return get


async def _run(monkeypatch, settings: dict, transcript: str) -> dict:
    import services.dynamic_settings as ds
    from services.insights import generator

    monkeypatch.setattr(generator, "extract_all_session_metas", AsyncMock(return_value=[_meta("s1", 60, 2)]))
    monkeypatch.setattr(generator, "load_cached_facets_batch", AsyncMock(return_value={}))
    monkeypatch.setattr(generator, "build_session_transcript", AsyncMock(return_value=transcript))
    monkeypatch.setattr(generator, "_extract_facets_batch", AsyncMock(return_value={}))
    monkeypatch.setattr(generator, "generate_sections", AsyncMock(return_value={}))
    monkeypatch.setattr(ds, "get", _settings(settings))

    return await generator.generate_report_content(
        agent_name="agent",
        agent_id="agent-id",
        period_start="2026-01-01",
        period_end="2026-01-02",
        db=SimpleNamespace(),
    )


@pytest.mark.asyncio
async def test_report_records_section_synthesis_and_facet_models(monkeypatch):
    settings = {
        "insights.model_sections": "bedrock/opus",
        "insights.model_synthesis": "bedrock/sonnet",
        "insights.model_facets": "bedrock/haiku",
    }

    report = await _run(monkeypatch, settings, transcript="user: fix the bug")

    assert report["models_used"] == ["bedrock/opus", "bedrock/sonnet", "bedrock/haiku"]


@pytest.mark.asyncio
async def test_unset_stage_models_record_the_section_model_they_fall_back_to(monkeypatch):
    report = await _run(monkeypatch, {"insights.model_sections": "openai/gpt"}, transcript="user: fix the bug")

    assert report["models_used"] == ["openai/gpt"]


@pytest.mark.asyncio
async def test_facet_model_is_omitted_when_no_facets_were_extracted(monkeypatch):
    settings = {"insights.model_sections": "bedrock/opus", "insights.model_facets": "bedrock/haiku"}

    report = await _run(monkeypatch, settings, transcript="")

    assert report["models_used"] == ["bedrock/opus"]
