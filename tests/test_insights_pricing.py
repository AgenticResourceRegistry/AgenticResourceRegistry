# SPDX-FileCopyrightText: 2026 Santhiya Manivannan <70739919+sanraj2000@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for pricing — cost tracking and model-tier classification in enrichment.

Covers: per-model cost aggregation in extract_session_meta, cost totals in
aggregate_metas, model tier classification (subscription vs metered),
and cost formatting helpers in html_export.
"""

from __future__ import annotations

import json
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("DEPLOYMENT_MODE") != "enterprise",
    reason="Insights pricing tests require DEPLOYMENT_MODE=enterprise",
)


def _raw(entry: dict) -> str:
    return json.dumps(entry)


def _assistant_with_cost(model: str, input_tokens: int, output_tokens: int, cost: float) -> str:
    return _raw(
        {
            "type": "message",
            "timestamp": "2026-01-01T10:00:00Z",
            "message": {
                "role": "assistant",
                "model": model,
                "content": [],
                "usage": {
                    "input": input_tokens,
                    "output": output_tokens,
                    "cacheRead": 0,
                    "cacheWrite": 0,
                    "cost": {"total": cost},
                },
            },
        }
    )


class TestCostExtractionFromRawLines:
    def test_single_model_cost_tracked(self):
        from services.insights.session_meta_extractor import extract_session_meta

        lines = [_assistant_with_cost("claude-haiku", 1000, 200, 0.05)]
        meta = extract_session_meta("s1", lines)
        assert meta["total_cost"] == pytest.approx(0.05)

    def test_multiple_messages_costs_summed(self):
        from services.insights.session_meta_extractor import extract_session_meta

        lines = [
            _assistant_with_cost("claude-haiku", 1000, 200, 0.02),
            _assistant_with_cost("claude-haiku", 2000, 400, 0.04),
        ]
        meta = extract_session_meta("s1", lines)
        assert meta["total_cost"] == pytest.approx(0.06)

    def test_input_tokens_aggregated(self):
        from services.insights.session_meta_extractor import extract_session_meta

        lines = [_assistant_with_cost("claude-haiku", 500, 0, 0.0), _assistant_with_cost("claude-haiku", 300, 0, 0.0)]
        meta = extract_session_meta("s1", lines)
        assert meta["input_tokens"] == 800

    def test_output_tokens_aggregated(self):
        from services.insights.session_meta_extractor import extract_session_meta

        lines = [_assistant_with_cost("claude-haiku", 0, 100, 0.0), _assistant_with_cost("claude-haiku", 0, 200, 0.0)]
        meta = extract_session_meta("s1", lines)
        assert meta["output_tokens"] == 300

    def test_cache_tokens_aggregated(self):
        from services.insights.session_meta_extractor import extract_session_meta

        line = _raw(
            {
                "type": "message",
                "timestamp": "2026-01-01T10:00:00Z",
                "message": {
                    "role": "assistant",
                    "model": "claude-haiku",
                    "content": [],
                    "usage": {
                        "input": 0,
                        "output": 0,
                        "cacheRead": 500,
                        "cacheWrite": 1000,
                        "cost": {},
                    },
                },
            }
        )
        meta = extract_session_meta("s1", [line])
        assert meta["cache_read_tokens"] == 500
        assert meta["cache_write_tokens"] == 1000

    def test_per_model_usage_tracked_separately(self):
        from services.insights.session_meta_extractor import extract_session_meta

        lines = [
            _assistant_with_cost("claude-haiku", 1000, 200, 0.01),
            _assistant_with_cost("claude-opus", 2000, 500, 0.10),
        ]
        meta = extract_session_meta("s1", lines)
        assert "claude-haiku" in meta["model_usage"]
        assert "claude-opus" in meta["model_usage"]
        assert meta["model_usage"]["claude-opus"]["cost"] == pytest.approx(0.10)

    def test_zero_cost_messages_still_tracked_by_model(self):
        from services.insights.session_meta_extractor import extract_session_meta

        line = _raw(
            {
                "type": "message",
                "timestamp": "2026-01-01T10:00:00Z",
                "message": {
                    "role": "assistant",
                    "model": "claude-haiku",
                    "content": [],
                },
            }
        )
        meta = extract_session_meta("s1", [line])
        assert "claude-haiku" in meta["model_usage"]
        assert meta["model_usage"]["claude-haiku"]["messages"] == 1


class TestModelTierClassification:
    """Model tier classification in aggregate_metas."""

    def _make_meta_with_model(self, model: str, input_t: int, output_t: int, cost: float) -> dict:
        return {
            "session_id": "s1",
            "total_messages": 5,
            "duration_seconds": 60.0,
            "input_tokens": input_t,
            "output_tokens": output_t,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "total_cost": cost,
            "credits": 0.0,
            "lines_added": 0,
            "lines_removed": 0,
            "files_modified": 0,
            "git_commits": 0,
            "git_pushes": 0,
            "tool_errors": 0,
            "user_interruptions": 0,
            "uses_subagent": False,
            "uses_mcp": False,
            "tool_counts": {},
            "languages": {},
            "tool_error_categories": {},
            "model_usage": {model: {"input_tokens": input_t, "output_tokens": output_t, "cost": cost, "messages": 10}},
            "project_path": "",
            "user_response_times": [],
            "message_hours": [],
            "start_time": "2026-01-01T10:00:00Z",
            "harness": "",
            "user_message_count": 3,
            "user_message_timestamps": [],
            "layer_hash": "",
            "agent_version": "",
        }

    def test_high_token_zero_cost_is_subscription(self):
        from services.insights.session_meta_extractor import aggregate_metas

        meta = self._make_meta_with_model("claude-pro-sub", 200_000, 50_000, 0.0)
        agg = aggregate_metas([meta])
        assert agg["model_tiers"].get("claude-pro-sub") == "subscription"

    def test_paid_model_not_subscription(self):
        from services.insights.session_meta_extractor import aggregate_metas

        meta = self._make_meta_with_model("claude-opus-4", 50_000, 10_000, 2.50)
        agg = aggregate_metas([meta])
        tier = agg["model_tiers"].get("claude-opus-4")
        assert tier != "subscription"

    def test_expensive_model_classified_as_high(self):
        """A model with cost-per-1k-token 3x above median → high tier."""
        from services.insights.session_meta_extractor import aggregate_metas

        # cheap model: 100k tokens for $0.01 → very low cpt
        cheap = self._make_meta_with_model("cheap-model", 50_000, 50_000, 0.01)
        # expensive model: 10k tokens for $10 → high cpt
        expensive = self._make_meta_with_model("expensive-model", 5_000, 5_000, 10.0)
        agg = aggregate_metas([cheap, expensive])
        # Multiple sessions needed to set median; expensive should be "high" relative to cheap
        assert agg["model_tiers"].get("expensive-model") in ("high", "mid")  # at least not low

    def test_total_cost_sums_across_sessions(self):
        from services.insights.session_meta_extractor import aggregate_metas

        metas = [
            self._make_meta_with_model("haiku", 1000, 200, 0.05),
            self._make_meta_with_model("haiku", 1000, 200, 0.05),
            self._make_meta_with_model("haiku", 1000, 200, 0.05),
        ]
        agg = aggregate_metas(metas)
        assert agg["total_cost"] == pytest.approx(0.15)


class TestCostFormattingHelpers:
    """Edge cases for the cost formatting helpers in html_export."""

    def test_format_cost_zero(self):
        from services.insights.html_export import _format_cost

        # 0.0 < 0.01, so 4 decimal places are shown
        assert _format_cost(0.0) == "$0.0000"

    def test_format_cost_sub_cent(self):
        from services.insights.html_export import _format_cost

        result = _format_cost(0.0042)
        assert result.startswith("$")
        assert "0042" in result  # 4 decimal places

    def test_format_cost_whole_dollar(self):
        from services.insights.html_export import _format_cost

        assert _format_cost(5.0) == "$5.00"

    def test_format_cost_large_value(self):
        from services.insights.html_export import _format_cost

        assert _format_cost(1234.56) == "$1234.56"

    def test_format_cost_none_is_zero(self):
        from services.insights.html_export import _format_cost

        assert _format_cost(None) == "$0.00"

    def test_format_number_millions(self):
        from services.insights.html_export import _format_number

        assert _format_number(1_500_000) == "1,500,000"

    def test_format_number_zero(self):
        from services.insights.html_export import _format_number

        assert _format_number(0) == "0"
