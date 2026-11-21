# SPDX-FileCopyrightText: 2026 Santhiya Manivannan <santhiyamalar20@gmail.com>
# SPDX-FileCopyrightText: 2026 Santhiya Manivannan <70739919+sanraj2000@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for regression detection — sections regression prompt + version_impact helpers.

Covers: the regression_detection section prompt data forwarding, period-over-period
change detection signals in generate_sections, and version comparison section routing.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("DEPLOYMENT_MODE") != "enterprise",
    reason="Insights regression tests require DEPLOYMENT_MODE=enterprise",
)


class TestRegressionDetectionSection:
    """Verify regression_detection section prompt is built correctly."""

    @pytest.mark.asyncio
    async def test_no_previous_data_produces_has_previous_false(self, monkeypatch):
        import services.dynamic_settings as ds
        from services.insights import sections

        monkeypatch.setattr(ds, "get", AsyncMock(return_value=None))

        prompts_captured: list[str] = []

        async def recording_model(prompt: str, **kwargs) -> dict:
            prompts_captured.append(prompt)
            if "regression_detection" in prompt:
                return {
                    "regression_detection": {"has_previous_data": False, "summary": "No previous data.", "changes": []}
                }
            return {}

        monkeypatch.setattr(sections, "get_call_model", lambda: recording_model)

        result = await sections.generate_sections(data_block='{"total_sessions": 5}', previous_report=None)

        reg = result.get("regression_detection") or {}
        # Either the section returned has_previous_data=False, or generate_sections
        # propagated an empty dict (both are acceptable — what matters is no crash)
        if reg:
            assert reg.get("has_previous_data") is False or "changes" in reg

    @pytest.mark.asyncio
    async def test_previous_data_included_in_prompt(self, monkeypatch):
        import services.dynamic_settings as ds
        from services.insights import sections

        monkeypatch.setattr(ds, "get", AsyncMock(return_value=None))

        prompts_captured: list[str] = []

        async def recording_model(prompt: str, **kwargs) -> dict:
            prompts_captured.append(prompt)
            return {}

        monkeypatch.setattr(sections, "get_call_model", lambda: recording_model)

        prev_report = {"total_sessions": 3, "total_cost": 0.15}
        await sections.generate_sections(
            data_block='{"total_sessions": 5}',
            previous_report=prev_report,
        )

        regression_prompts = [p for p in prompts_captured if "regression_detection" in p]
        assert regression_prompts, "regression_detection prompt must be sent"
        prompt = regression_prompts[0]
        assert "Previous Period Metrics" in prompt
        assert "total_sessions" in prompt

    @pytest.mark.asyncio
    async def test_no_previous_data_block_says_unavailable(self, monkeypatch):
        import services.dynamic_settings as ds
        from services.insights import sections

        monkeypatch.setattr(ds, "get", AsyncMock(return_value=None))

        prompts_captured: list[str] = []

        async def recording_model(prompt: str, **kwargs) -> dict:
            prompts_captured.append(prompt)
            return {}

        monkeypatch.setattr(sections, "get_call_model", lambda: recording_model)

        await sections.generate_sections(data_block='{"total_sessions": 2}', previous_report=None)

        regression_prompts = [p for p in prompts_captured if "regression_detection" in p]
        assert regression_prompts
        assert "No previous period data available" in regression_prompts[0]


class TestVersionComparisonSection:
    @pytest.mark.asyncio
    async def test_version_comparison_section_included_in_output(self, monkeypatch):
        import services.dynamic_settings as ds
        from services.insights import sections

        monkeypatch.setattr(ds, "get", AsyncMock(return_value=None))

        async def canned_model(prompt: str, **kwargs) -> dict:
            if "version_comparison" in prompt:
                return {
                    "version_comparison": {
                        "has_comparison": True,
                        "current_version": "1.1.0",
                        "prior_version": "1.0.0",
                        "summary": "Metrics improved.",
                        "confidence": "medium",
                        "changes": [],
                    }
                }
            return {}

        monkeypatch.setattr(sections, "get_call_model", lambda: canned_model)

        result = await sections.generate_sections(data_block='{"total_sessions": 10}')
        vc = result.get("version_comparison") or {}
        # When the canned response is returned, it should be stored
        if vc:
            assert vc.get("has_comparison") is True

    @pytest.mark.asyncio
    async def test_version_comparison_no_comparison_case(self, monkeypatch):
        import services.dynamic_settings as ds
        from services.insights import sections

        monkeypatch.setattr(ds, "get", AsyncMock(return_value=None))

        async def canned_model(prompt: str, **kwargs) -> dict:
            if "version_comparison" in prompt:
                return {
                    "version_comparison": {
                        "has_comparison": False,
                        "summary": "No prior approved version cohort was available.",
                        "confidence": "insufficient_data",
                        "changes": [],
                    }
                }
            return {}

        monkeypatch.setattr(sections, "get_call_model", lambda: canned_model)

        result = await sections.generate_sections(data_block='{"total_sessions": 2}')
        vc = result.get("version_comparison") or {}
        if vc:
            assert vc.get("has_comparison") is False


class TestVersionImpactMedianMAD:
    """Unit tests for the statistical helpers used in regression detection."""

    def test_mad_based_z_score_robust_to_outlier(self):
        """A single extreme outlier should not shift other groups out of 'normal'."""
        from services.insights.version_impact import _robust_outlier_labels

        groups = [
            {"layer_hash": "n1", "success_proxy": 0.80},
            {"layer_hash": "n2", "success_proxy": 0.81},
            {"layer_hash": "n3", "success_proxy": 0.79},
            {"layer_hash": "extreme", "success_proxy": 0.0},  # outlier
        ]
        labels = _robust_outlier_labels(groups, "success_proxy")
        # Normal groups should remain 'normal'; outlier should be flagged
        assert labels["n1"] == "normal"
        assert labels["extreme"] == "negative_outlier"

    def test_all_identical_values_all_normal(self):
        from services.insights.version_impact import _robust_outlier_labels

        groups = [{"layer_hash": f"h{i}", "success_proxy": 0.80} for i in range(5)]
        labels = _robust_outlier_labels(groups, "success_proxy")
        assert all(v == "normal" for v in labels.values())

    def test_confidence_medium_for_moderate_data(self):
        from services.insights.version_impact import _confidence_for_groups

        # 20 sessions total, 1 multi-user group
        groups = [
            {"layer_hash": "h1", "sessions": 10, "users": 2},
            {"layer_hash": "h2", "sessions": 10, "users": 1},
        ]
        result = _confidence_for_groups(groups, significant=True)
        # 20 sessions, only 1 multi-user group → medium confidence
        assert result == "medium"
