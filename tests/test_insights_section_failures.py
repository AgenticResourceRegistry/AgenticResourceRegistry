# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Invalid model sections must be visible and must not turn into Apply success."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.insights import sections
from services.insights.section_validation import section_valid, suggestions_failed

EMPTY = {"suggestions": {"config_additions": [], "features_to_try": [], "usage_patterns": []}}


@pytest.mark.parametrize(
    "raw",
    [
        {},
        [],
        {"unrelated": {"config_additions": []}},
        {"suggestions": {}},
        {"suggestions": {"config_additions": "not a list", "features_to_try": [], "usage_patterns": []}},
    ],
)
@pytest.mark.asyncio
async def test_invalid_or_missing_schema_retries_then_records_failure(monkeypatch, raw):
    model = AsyncMock(return_value=raw)
    monkeypatch.setattr(sections, "get_call_model", lambda: model)
    name, result = await sections._call_section("suggestions", "original")
    assert name == "suggestions" and result["_section_error"]["attempts"] == 2
    assert result["_section_error"]["code"] == "invalid_or_empty_response"
    assert model.await_count == 2 and "exact 'suggestions' envelope" in model.await_args.args[0]


@pytest.mark.asyncio
async def test_legitimate_empty_suggestions_succeeds_without_retry(monkeypatch):
    model = AsyncMock(return_value=EMPTY)
    monkeypatch.setattr(sections, "get_call_model", lambda: model)
    assert await sections._call_section("suggestions", "p") == ("suggestions", EMPTY)
    assert model.await_count == 1


@pytest.mark.asyncio
async def test_successful_retry_returns_only_valid_result(monkeypatch):
    model = AsyncMock(side_effect=[{}, EMPTY])
    monkeypatch.setattr(sections, "get_call_model", lambda: model)
    assert await sections._call_section("suggestions", "p") == ("suggestions", EMPTY)


@pytest.mark.asyncio
async def test_exception_diagnostics_never_contain_model_or_credentials(monkeypatch):
    model = AsyncMock(side_effect=ValueError("SECRET_PROVIDER_TOKEN private response"))
    monkeypatch.setattr(sections, "get_call_model", lambda: model)
    _, result = await sections._call_section("suggestions", "p")
    assert result["_section_error"]["code"] == "model_call_failed"
    assert "SECRET_PROVIDER_TOKEN" not in str(result)


@pytest.mark.asyncio
async def test_failed_section_does_not_discard_available_sections(monkeypatch):
    monkeypatch.setattr(sections, "SECTION_PROMPTS", {"suggestions": "{data_block}", "what_works": "{data_block}"})
    monkeypatch.setattr(sections, "_get_section_model", AsyncMock(return_value="test"))
    monkeypatch.setattr(sections, "_get_synthesis_model", AsyncMock(return_value="test"))

    async def model(prompt, **kwargs):
        if '"at_a_glance"' in prompt:
            return {
                "at_a_glance": {
                    "health": "mixed",
                    "whats_working": "ok",
                    "whats_hindering": "unknown",
                    "quick_win": "retry",
                    "ambitious_workflows": "none",
                }
            }
        if "exact 'suggestions'" in prompt:
            return {}
        # The first two original prompts are identical: use a queued answer.
        return responses.pop(0)

    responses = [{}, {"what_works": {"intro": "Delivered", "strengths": []}}]
    monkeypatch.setattr(sections, "get_call_model", lambda: model)
    result = await sections.generate_sections("data")
    assert result["generation_state"] == "partial"
    assert result["what_works"]["intro"] == "Delivered"
    assert result["section_errors"] == [{"section": "suggestions", "code": "invalid_or_empty_response", "attempts": 2}]
    assert suggestions_failed(result)


def test_section_validator_checks_envelope_not_first_value():
    assert not section_valid("suggestions", {"something_else": EMPTY["suggestions"]})
    assert section_valid("version_impact", {"version_impact": None})


@pytest.mark.asyncio
async def test_apply_rejects_persisted_suggestions_error_before_writes():
    import uuid

    from models.insight_report import InsightReportStatus
    from services.insights.self_learn import apply_insight_suggestions

    report = SimpleNamespace(
        status=InsightReportStatus.completed,
        applied_at=None,
        agent_id=uuid.uuid4(),
        narrative={**EMPTY, "section_errors": [{"section": "suggestions", "code": "model_call_failed", "attempts": 2}]},
    )
    agent = SimpleNamespace(created_by=None)
    transaction = SimpleNamespace(is_active=True, rollback=AsyncMock())
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=lambda stmt: SimpleNamespace(
                scalar_one_or_none=lambda: report if "insight_reports" in str(stmt) else agent
            )
        ),
        commit=AsyncMock(),
        rollback=AsyncMock(),
        begin_nested=AsyncMock(return_value=transaction),
    )
    with pytest.raises(ValueError, match="Suggestions generation failed"):
        await apply_insight_suggestions(uuid.uuid4(), db, uuid.uuid4())
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_malformed_provider_json_reaches_bounded_retry(monkeypatch):
    import services.dynamic_settings as ds
    import services.insights as insights

    provider = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"suggestions": "unterminated'))]
        )
    )
    monkeypatch.setattr(insights.litellm, "acompletion", provider)

    async def setting(key, *args):
        return "openai/fixture" if key == "insights.model_sections" else None

    monkeypatch.setattr(ds, "get", setting)
    monkeypatch.setattr(sections, "get_call_model", lambda: insights.call_model)
    _, result = await sections._call_section("suggestions", "p")
    assert result["_section_error"]["code"] == "invalid_or_empty_response"
    assert provider.await_count == 2
