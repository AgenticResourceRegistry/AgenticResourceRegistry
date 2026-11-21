# SPDX-FileCopyrightText: 2026 Santhiya Manivannan <70739919+sanraj2000@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for version_impact.py — anonymized cross-user layer analysis.

Covers: _median, _robust_outlier_labels, _confidence_for_groups,
diff_snapshots, extract_content_summary, and build_version_impact_data
(lightweight and significant paths).

"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("DEPLOYMENT_MODE") != "enterprise",
    reason="Insights anonymize/version_impact tests require DEPLOYMENT_MODE=enterprise",
)


def _group(layer_hash: str, sessions: int = 10, users: int = 2, success: float = 0.8, error_rate: float = 0.05) -> dict:
    return {
        "agent_version": "1.0.0",
        "layer_hash": layer_hash,
        "sessions": sessions,
        "users": users,
        "avg_prompts": 5.0,
        "avg_tool_calls": 10.0,
        "avg_duration_seconds": 300.0,
        "avg_cost": 0.05,
        "avg_tokens": 20000,
        "tool_error_rate": error_rate,
        "success_proxy": success,
    }


def _snapshot(is_canonical: bool = True, files: list | None = None) -> dict:
    return {
        "drift": {"is_canonical": is_canonical},
        "harnesses": {
            "claude-code": files
            or [
                {"path": "CLAUDE.md", "hash": "abc123", "content": "## Rules\nAlways show diffs."},
            ]
        },
        "pinned_versions": {"scope-guard": "1.1.0"},
    }


class TestMedian:
    def test_empty_returns_zero(self):
        from services.insights.version_impact import _median

        assert _median([]) == 0.0

    def test_single_value(self):
        from services.insights.version_impact import _median

        assert _median([5.0]) == 5.0

    def test_odd_count(self):
        from services.insights.version_impact import _median

        assert _median([1.0, 2.0, 3.0]) == 2.0

    def test_even_count(self):
        from services.insights.version_impact import _median

        assert _median([1.0, 2.0, 3.0, 4.0]) == 2.5


class TestRobustOutlierLabels:
    def test_fewer_than_three_groups_all_normal(self):
        from services.insights.version_impact import _robust_outlier_labels

        groups = [_group("h1"), _group("h2")]
        labels = _robust_outlier_labels(groups, "success_proxy")
        assert all(v == "normal" for v in labels.values())

    def test_positive_outlier_labeled(self):
        from services.insights.version_impact import _robust_outlier_labels

        # One group has dramatically higher success than the rest
        groups = [
            _group("low1", success=0.3),
            _group("low2", success=0.3),
            _group("low3", success=0.3),
            _group("high", success=0.95),
        ]
        labels = _robust_outlier_labels(groups, "success_proxy")
        assert labels["high"] == "positive_outlier"

    def test_negative_outlier_labeled(self):
        from services.insights.version_impact import _robust_outlier_labels

        groups = [
            _group("high1", success=0.9),
            _group("high2", success=0.9),
            _group("high3", success=0.9),
            _group("low", success=0.1),
        ]
        labels = _robust_outlier_labels(groups, "success_proxy")
        assert labels["low"] == "negative_outlier"

    def test_similar_groups_all_normal(self):
        from services.insights.version_impact import _robust_outlier_labels

        groups = [_group(f"h{i}", success=0.8 + i * 0.01) for i in range(5)]
        labels = _robust_outlier_labels(groups, "success_proxy")
        assert all(v == "normal" for v in labels.values())


class TestConfidenceForGroups:
    def test_insufficient_data_too_few_sessions(self):
        from services.insights.version_impact import _confidence_for_groups

        groups = [_group("h1", sessions=3), _group("h2", sessions=4)]
        assert _confidence_for_groups(groups, significant=True) == "insufficient_data"

    def test_high_confidence_many_sessions_multi_user(self):
        from services.insights.version_impact import _confidence_for_groups

        groups = [
            _group("h1", sessions=20, users=3),
            _group("h2", sessions=15, users=2),
        ]
        assert _confidence_for_groups(groups, significant=True) == "high"

    def test_low_confidence_when_not_significant(self):
        from services.insights.version_impact import _confidence_for_groups

        groups = [_group("h1", sessions=20, users=2), _group("h2", sessions=15, users=1)]
        assert _confidence_for_groups(groups, significant=False) == "low"


class TestDiffSnapshots:
    def test_added_files_detected(self):
        from services.insights.version_impact import diff_snapshots

        snap_a = {"harnesses": {"cursor": []}}
        snap_b = {"harnesses": {"cursor": [{"path": "rules.md", "hash": "x"}]}}
        diff = diff_snapshots(snap_a, snap_b)
        assert "cursor/rules.md" in diff["added"]

    def test_removed_files_detected(self):
        from services.insights.version_impact import diff_snapshots

        snap_a = {"harnesses": {"cursor": [{"path": "rules.md", "hash": "x"}]}}
        snap_b = {"harnesses": {"cursor": []}}
        diff = diff_snapshots(snap_a, snap_b)
        assert "cursor/rules.md" in diff["removed"]

    def test_modified_files_detected(self):
        from services.insights.version_impact import diff_snapshots

        snap_a = {"harnesses": {"cursor": [{"path": "rules.md", "hash": "aaa"}]}}
        snap_b = {"harnesses": {"cursor": [{"path": "rules.md", "hash": "bbb"}]}}
        diff = diff_snapshots(snap_a, snap_b)
        assert "cursor/rules.md" in diff["modified"]

    def test_identical_snapshots_empty_diff(self):
        from services.insights.version_impact import diff_snapshots

        snap = {"harnesses": {"cursor": [{"path": "rules.md", "hash": "abc"}]}}
        diff = diff_snapshots(snap, snap)
        assert diff["added"] == []
        assert diff["removed"] == []
        assert diff["modified"] == []

    def test_empty_snapshots(self):
        from services.insights.version_impact import diff_snapshots

        diff = diff_snapshots({}, {})
        assert diff["added"] == []
        assert diff["removed"] == []
        assert diff["modified"] == []


class TestExtractContentSummary:
    def test_returns_behavioral_file_content(self):
        from services.insights.version_impact import extract_content_summary

        snap = {
            "harnesses": {
                "claude-code": [
                    {"path": "CLAUDE.md", "hash": "x", "content": "## Rules\nAlways show diffs."},
                ]
            }
        }
        summary = extract_content_summary(snap)
        assert "CLAUDE.md" in summary
        assert "Always show diffs." in summary

    def test_excludes_non_behavioral_files(self):
        from services.insights.version_impact import extract_content_summary

        snap = {
            "harnesses": {
                "claude-code": [
                    {"path": "package.json", "hash": "x", "content": '{"name": "app"}'},
                    {"path": "AGENTS.md", "hash": "y", "content": "agent rules here"},
                ]
            }
        }
        summary = extract_content_summary(snap)
        assert "package.json" not in summary
        assert "AGENTS.md" in summary

    def test_truncates_long_content(self):
        from services.insights.version_impact import extract_content_summary

        snap = {
            "harnesses": {
                "claude-code": [
                    {"path": "CLAUDE.md", "hash": "x", "content": "x" * 2000},
                ]
            }
        }
        summary = extract_content_summary(snap, max_chars=500)
        assert len(summary) <= 1000  # generous bound, just ensure truncation

    def test_empty_snapshot_returns_placeholder(self):
        from services.insights.version_impact import extract_content_summary

        assert extract_content_summary({}) == "(no behavioral content captured)"


class TestBuildVersionImpactData:
    @pytest.mark.asyncio
    async def test_returns_none_when_fewer_than_two_groups(self, monkeypatch):
        from services.insights import version_impact

        monkeypatch.setattr(version_impact, "detect_layer_groups", AsyncMock(return_value=[_group("h1")]))

        result = await version_impact.build_version_impact_data(
            agent_id="a1", period_start="2026-01-01", period_end="2026-01-31"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_lightweight_summary_when_no_significant_gap(self, monkeypatch):
        from services.insights import version_impact

        # Two groups with similar success rates — no significant gap
        groups = [_group("h1", success=0.81), _group("h2", success=0.80)]
        monkeypatch.setattr(version_impact, "detect_layer_groups", AsyncMock(return_value=groups))

        result = await version_impact.build_version_impact_data(
            agent_id="a1", period_start="2026-01-01", period_end="2026-01-31"
        )
        assert result is not None
        assert result["significant"] is False
        assert "finding" in result

    @pytest.mark.asyncio
    async def test_fetches_snapshots_for_significant_gap(self, monkeypatch):
        from services.insights import version_impact

        # Significant success gap: 0.9 vs 0.3 → 60% gap
        groups = [_group("h1", success=0.9, sessions=20, users=3), _group("h2", success=0.3, sessions=20, users=3)]
        monkeypatch.setattr(version_impact, "detect_layer_groups", AsyncMock(return_value=groups))

        snapshots = {"h1": _snapshot(is_canonical=True), "h2": _snapshot(is_canonical=False)}
        fetch_mock = AsyncMock(return_value=snapshots)
        monkeypatch.setattr(version_impact, "fetch_layer_snapshots_for_groups", fetch_mock)

        result = await version_impact.build_version_impact_data(
            agent_id="a1", period_start="2026-01-01", period_end="2026-01-31"
        )
        fetch_mock.assert_called_once()
        assert result is not None
        assert result["significant"] is True

    @pytest.mark.asyncio
    async def test_returns_none_when_snapshots_insufficient(self, monkeypatch):
        from services.insights import version_impact

        groups = [_group("h1", success=0.9, sessions=20, users=3), _group("h2", success=0.3, sessions=20, users=3)]
        monkeypatch.setattr(version_impact, "detect_layer_groups", AsyncMock(return_value=groups))
        # Only one snapshot returned → can't compare best vs worst
        monkeypatch.setattr(
            version_impact, "fetch_layer_snapshots_for_groups", AsyncMock(return_value={"h1": _snapshot()})
        )

        result = await version_impact.build_version_impact_data(
            agent_id="a1", period_start="2026-01-01", period_end="2026-01-31"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_canonical_dirty_counts_populated(self, monkeypatch):
        from services.insights import version_impact

        groups = [
            _group("canon", success=0.9, sessions=15, users=3),
            _group("dirty", success=0.4, sessions=15, users=3),
        ]
        monkeypatch.setattr(version_impact, "detect_layer_groups", AsyncMock(return_value=groups))
        snapshots = {
            "canon": _snapshot(is_canonical=True),
            "dirty": _snapshot(is_canonical=False),
        }
        monkeypatch.setattr(version_impact, "fetch_layer_snapshots_for_groups", AsyncMock(return_value=snapshots))

        result = await version_impact.build_version_impact_data(
            agent_id="a1", period_start="2026-01-01", period_end="2026-01-31"
        )
        assert result is not None
        canon_dirty = result["canonical_dirty_summary"]
        assert canon_dirty["canonical_sessions"] == 15
        assert canon_dirty["dirty_sessions"] == 15
