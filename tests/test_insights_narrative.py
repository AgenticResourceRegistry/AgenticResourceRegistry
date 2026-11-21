# SPDX-FileCopyrightText: 2026 Santhiya Manivannan <70739919+sanraj2000@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for sections.py — parallel narrative section prompt execution.

Covers: _call_section, generate_sections, section model routing, and
synthesis prompt construction.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("DEPLOYMENT_MODE") != "enterprise",
    reason="Insights narrative/sections tests require DEPLOYMENT_MODE=enterprise",
)


class TestCallSection:
    @pytest.mark.asyncio
    async def test_returns_section_name_and_result_on_success(self, monkeypatch):
        from services.insights import sections

        call_mock = AsyncMock(return_value={"what_works": {"intro": "it works", "strengths": []}})
        monkeypatch.setattr(sections, "get_call_model", lambda: call_mock)

        name, result = await sections._call_section("what_works", "prompt text")
        assert name == "what_works"
        assert result["what_works"]["intro"] == "it works"

    @pytest.mark.asyncio
    async def test_returns_empty_dict_on_exception(self, monkeypatch):
        from services.insights import sections

        call_mock = AsyncMock(side_effect=Exception("network error"))
        monkeypatch.setattr(sections, "get_call_model", lambda: call_mock)

        name, result = await sections._call_section("what_works", "prompt")
        assert name == "what_works"
        assert result == {}

    @pytest.mark.asyncio
    async def test_propagates_runtime_error(self, monkeypatch):
        from services.insights import sections

        call_mock = AsyncMock(side_effect=RuntimeError("not configured"))
        monkeypatch.setattr(sections, "get_call_model", lambda: call_mock)

        with pytest.raises(RuntimeError):
            await sections._call_section("what_works", "prompt")

    @pytest.mark.asyncio
    async def test_returns_empty_dict_when_model_returns_none(self, monkeypatch):
        from services.insights import sections

        call_mock = AsyncMock(return_value=None)
        monkeypatch.setattr(sections, "get_call_model", lambda: call_mock)

        name, result = await sections._call_section("fun_ending", "prompt")
        assert result == {}

    @pytest.mark.asyncio
    async def test_uses_section_max_tokens_override(self, monkeypatch):
        from services.insights import sections

        calls: list[dict] = []

        async def call_model(prompt, **kwargs):
            calls.append(kwargs)
            return {"fun_ending": {"headline": "x", "detail": "y"}}

        monkeypatch.setattr(sections, "get_call_model", lambda: call_model)

        await sections._call_section("fun_ending", "prompt")
        assert calls[0]["max_tokens"] == 1024  # fun_ending override


class TestGenerateSections:
    def _make_call_mock(self, responses: dict | None = None) -> AsyncMock:
        """Return a call_model mock that returns canned responses per first-word lookup."""
        defaults = {
            "what_they_work_on": {"what_they_work_on": {"areas": []}},
            "interaction_style": {"interaction_style": {"narrative": "", "key_pattern": ""}},
            "usage_patterns": {"usage_patterns": {"narrative": "", "top_tasks": []}},
            "what_works": {"what_works": {"intro": "", "strengths": []}},
            "friction_analysis": {"friction_analysis": {"intro": "", "categories": []}},
            "suggestions": {"suggestions": {"config_additions": [], "features_to_try": [], "usage_patterns": []}},
            "usage_cost_analysis": {"usage_cost_analysis": {"summary": "", "metrics": {}}},
            "version_comparison": {"version_comparison": {"has_comparison": False}},
            "regression_detection": {"regression_detection": {"has_previous_data": False}},
            "on_the_horizon": {"on_the_horizon": {"intro": "", "opportunities": []}},
            "fun_ending": {"fun_ending": {"headline": "test", "detail": "test detail"}},
            "version_impact": {"version_impact": {"summary": ""}},
            "at_a_glance": {
                "at_a_glance": {
                    "health": "mixed",
                    "whats_working": "",
                    "whats_hindering": "",
                    "quick_win": "",
                    "ambitious_workflows": "",
                }
            },
        }
        if responses:
            defaults.update(responses)

        # The call_model is invoked many times; return canned by checking prompt content
        async def _call(prompt: str, **kwargs) -> dict:
            for key, resp in defaults.items():
                if key in prompt or (key == "at_a_glance" and "At a Glance" in prompt):
                    return resp
            return {"at_a_glance": defaults["at_a_glance"]["at_a_glance"]}

        return _call

    @pytest.mark.asyncio
    async def test_generate_sections_returns_all_section_keys(self, monkeypatch):
        import services.dynamic_settings as ds
        from services.insights import sections

        monkeypatch.setattr(ds, "get", AsyncMock(return_value=None))
        monkeypatch.setattr(sections, "get_call_model", lambda: self._make_call_mock())

        result = await sections.generate_sections(data_block='{"total_sessions": 5}')

        # Core narrative sections must be present
        for key in ("what_works", "friction_analysis", "suggestions", "at_a_glance"):
            assert key in result, f"Missing section: {key}"

    @pytest.mark.asyncio
    async def test_previous_data_block_included_for_regression(self, monkeypatch):
        import services.dynamic_settings as ds
        from services.insights import sections

        monkeypatch.setattr(ds, "get", AsyncMock(return_value=None))

        prompts_seen: list[str] = []

        async def recording_model(prompt: str, **kwargs) -> dict:
            prompts_seen.append(prompt)
            return {
                "at_a_glance": {
                    "health": "mixed",
                    "whats_working": "",
                    "whats_hindering": "",
                    "quick_win": "",
                    "ambitious_workflows": "",
                }
            }

        monkeypatch.setattr(sections, "get_call_model", lambda: recording_model)

        await sections.generate_sections(
            data_block='{"total_sessions": 3}',
            previous_report={"total_sessions": 2},
        )

        regression_prompts = [p for p in prompts_seen if "regression_detection" in p]
        assert regression_prompts
        assert "Previous Period Metrics" in regression_prompts[0]

    @pytest.mark.asyncio
    async def test_catalog_block_included_for_suggestions(self, monkeypatch):
        import services.dynamic_settings as ds
        from services.insights import sections

        monkeypatch.setattr(ds, "get", AsyncMock(return_value=None))

        prompts_seen: list[str] = []

        async def recording_model(prompt: str, **kwargs) -> dict:
            prompts_seen.append(prompt)
            return {}

        monkeypatch.setattr(sections, "get_call_model", lambda: recording_model)

        catalog = {"skills": [{"id": "scope-guard", "description": "guards scope"}]}
        await sections.generate_sections(data_block='{"total_sessions": 3}', registry_catalog=catalog)

        suggestion_prompts = [p for p in prompts_seen if "suggestions" in p.lower() and "scope-guard" in p]
        assert suggestion_prompts

    @pytest.mark.asyncio
    async def test_deep_sections_use_section_model(self, monkeypatch):
        import services.dynamic_settings as ds
        from services.insights import sections

        # Return "opus" for section model, "sonnet" for synthesis
        async def fake_get(key: str):
            if key == "insights.model_sections":
                return "claude-opus-special"
            if key == "insights.model_synthesis":
                return "claude-sonnet-special"
            return None

        monkeypatch.setattr(ds, "get", fake_get)

        model_overrides_seen: list[str | None] = []

        async def recording_model(prompt: str, **kwargs) -> dict:
            model_overrides_seen.append(kwargs.get("model_override"))
            return {}

        monkeypatch.setattr(sections, "get_call_model", lambda: recording_model)

        await sections.generate_sections(data_block='{"total_sessions": 1}')

        # At least one deep section should have used "claude-opus-special"
        assert "claude-opus-special" in model_overrides_seen

    @pytest.mark.asyncio
    async def test_synthesis_failure_sets_empty_at_a_glance(self, monkeypatch):
        import services.dynamic_settings as ds
        from services.insights import sections

        monkeypatch.setattr(ds, "get", AsyncMock(return_value=None))

        call_count = {"n": 0}

        async def always_fail(prompt: str, **kwargs) -> dict:
            call_count["n"] += 1
            raise Exception("synthesis exploded")

        monkeypatch.setattr(sections, "get_call_model", lambda: always_fail)

        result = await sections.generate_sections(data_block='{"total_sessions": 1}')
        assert result.get("at_a_glance") == {}

    @pytest.mark.asyncio
    async def test_section_result_stored_under_correct_key(self, monkeypatch):
        import services.dynamic_settings as ds
        from services.insights import sections

        monkeypatch.setattr(ds, "get", AsyncMock(return_value=None))

        async def canned_model(prompt: str, **kwargs) -> dict:
            if "what_works" in prompt:
                return {"what_works": {"intro": "it works great", "strengths": []}}
            return {}

        monkeypatch.setattr(sections, "get_call_model", lambda: canned_model)

        result = await sections.generate_sections(data_block='{"total_sessions": 5}')
        assert result.get("what_works", {}).get("intro") == "it works great"
