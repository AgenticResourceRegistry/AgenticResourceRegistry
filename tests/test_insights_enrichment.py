# SPDX-FileCopyrightText: 2026 Santhiya Manivannan <70739919+sanraj2000@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for session_meta_extractor — deterministic per-session enrichment."""

from __future__ import annotations

import json
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("DEPLOYMENT_MODE") != "enterprise",
    reason="Insights enrichment tests require DEPLOYMENT_MODE=enterprise",
)


def _raw(entry: dict) -> str:
    return json.dumps(entry)


def _assistant_msg(tool_name: str, args: dict, model: str = "claude-haiku", tool_id: str | None = None) -> str:
    return _raw(
        {
            "type": "message",
            "timestamp": "2026-01-01T10:00:00Z",
            "message": {
                "role": "assistant",
                "model": model,
                "content": [
                    {
                        "type": "toolCall",
                        "name": tool_name,
                        "id": tool_id or f"t_{tool_name}",
                        "arguments": args,
                    }
                ],
                "usage": {
                    "input": 1000,
                    "output": 200,
                    "cacheRead": 0,
                    "cacheWrite": 0,
                    "cost": {"total": 0.01},
                },
            },
        }
    )


def _user_msg(text: str, ts: str = "2026-01-01T10:00:00Z") -> str:
    return _raw(
        {
            "type": "message",
            "timestamp": ts,
            "message": {
                "role": "user",
                "content": text,
            },
        }
    )


def _tool_result(tool_name: str, is_error: bool, content: str) -> str:
    return _raw(
        {
            "type": "message",
            "timestamp": "2026-01-01T10:01:00Z",
            "message": {
                "role": "toolResult",
                "toolName": tool_name,
                "isError": is_error,
                "content": content,
            },
        }
    )


def _session_header(cwd: str = "/repo/app") -> str:
    return _raw({"type": "session", "timestamp": "2026-01-01T10:00:00Z", "cwd": cwd})


class TestExtractSessionMeta:
    def test_empty_lines_returns_zero_stats(self):
        from services.insights.session_meta_extractor import extract_session_meta

        meta = extract_session_meta("s1", [])
        assert meta["session_id"] == "s1"
        assert meta["total_messages"] == 0
        assert meta["tool_errors"] == 0

    def test_user_message_count(self):
        from services.insights.session_meta_extractor import extract_session_meta

        lines = [_user_msg("hello"), _user_msg("thanks")]
        meta = extract_session_meta("s1", lines)
        assert meta["user_message_count"] == 2

    def test_tool_call_counted(self):
        from services.insights.session_meta_extractor import extract_session_meta

        lines = [_assistant_msg("bash", {"command": "ls -la"})]
        meta = extract_session_meta("s1", lines)
        assert meta["tool_counts"].get("bash", 0) == 1

    def test_tool_call_deduplication_by_id(self):
        """Same tool_id appearing twice must be counted once."""
        from services.insights.session_meta_extractor import extract_session_meta

        line = _assistant_msg("bash", {"command": "echo hi"}, tool_id="dup-id")
        meta = extract_session_meta("s1", [line, line])
        assert meta["tool_counts"].get("bash", 0) == 1

    def test_git_commit_detection(self):
        from services.insights.session_meta_extractor import extract_session_meta

        lines = [_assistant_msg("bash", {"command": "git commit -m 'fix: test'"})]
        meta = extract_session_meta("s1", lines)
        assert meta["git_commits"] == 1

    def test_git_push_detection(self):
        from services.insights.session_meta_extractor import extract_session_meta

        lines = [_assistant_msg("bash", {"command": "git push origin main"})]
        meta = extract_session_meta("s1", lines)
        assert meta["git_pushes"] == 1

    def test_tool_error_counted(self):
        from services.insights.session_meta_extractor import extract_session_meta

        lines = [_tool_result("bash", True, "command failed with exit code 1")]
        meta = extract_session_meta("s1", lines)
        assert meta["tool_errors"] == 1

    def test_tool_error_category_command_failed(self):
        from services.insights.session_meta_extractor import extract_session_meta

        lines = [_tool_result("bash", True, "command failed with exit code 1")]
        meta = extract_session_meta("s1", lines)
        assert meta["tool_error_categories"].get("command_failed", 0) == 1

    def test_tool_error_category_file_not_found(self):
        from services.insights.session_meta_extractor import extract_session_meta

        lines = [_tool_result("read", True, "no such file or directory")]
        meta = extract_session_meta("s1", lines)
        assert meta["tool_error_categories"].get("file_not_found", 0) == 1

    def test_interruption_detected(self):
        from services.insights.session_meta_extractor import extract_session_meta

        lines = [_user_msg("[Request interrupted by user for new prompt]")]
        meta = extract_session_meta("s1", lines)
        assert meta["user_interruptions"] == 1

    def test_subagent_detection(self):
        from services.insights.session_meta_extractor import extract_session_meta

        lines = [_assistant_msg("subagent", {})]
        meta = extract_session_meta("s1", lines)
        assert meta["uses_subagent"] is True

    def test_mcp_tool_detection(self):
        from services.insights.session_meta_extractor import extract_session_meta

        lines = [_assistant_msg("mcp__github__get_pr", {})]
        meta = extract_session_meta("s1", lines)
        assert meta["uses_mcp"] is True

    def test_language_detected_from_write_path(self):
        from services.insights.session_meta_extractor import extract_session_meta

        lines = [_assistant_msg("write", {"path": "/repo/app/main.py", "content": "print('hi')\n"})]
        meta = extract_session_meta("s1", lines)
        assert "Python" in meta["languages"]

    def test_language_detected_from_edit_path(self):
        from services.insights.session_meta_extractor import extract_session_meta

        lines = [
            _assistant_msg(
                "edit",
                {
                    "path": "/repo/app/index.ts",
                    "edits": [{"oldText": "old\n", "newText": "new\n"}],
                },
            )
        ]
        meta = extract_session_meta("s1", lines)
        assert "TypeScript" in meta["languages"]

    def test_lines_added_from_write(self):
        from services.insights.session_meta_extractor import extract_session_meta

        content = "line1\nline2\nline3"
        lines = [_assistant_msg("write", {"path": "/a.py", "content": content})]
        meta = extract_session_meta("s1", lines)
        assert meta["lines_added"] == 3  # 2 newlines + 1

    def test_project_path_from_session_header(self):
        from services.insights.session_meta_extractor import extract_session_meta

        lines = [_session_header("/home/user/myproject")]
        meta = extract_session_meta("s1", lines)
        assert meta["project_path"] == "/home/user/myproject"

    def test_duration_computed_from_timestamps(self):
        from services.insights.session_meta_extractor import extract_session_meta

        line1 = _raw(
            {
                "type": "message",
                "timestamp": "2026-01-01T10:00:00Z",
                "message": {"role": "user", "content": "start"},
            }
        )
        line2 = _raw(
            {
                "type": "message",
                "timestamp": "2026-01-01T10:10:00Z",
                "message": {"role": "user", "content": "end"},
            }
        )
        meta = extract_session_meta("s1", [line1, line2])
        assert meta["duration_seconds"] == pytest.approx(600.0)

    def test_invalid_json_lines_are_skipped(self):
        from services.insights.session_meta_extractor import extract_session_meta

        # Only lines that raise JSONDecodeError are skipped; "null" parses to None
        # and is also handled. Use truly broken JSON and empty lines.
        meta = extract_session_meta("s1", ["{broken json", "", "   "])
        assert meta["total_messages"] == 0

    def test_model_usage_aggregated(self):
        from services.insights.session_meta_extractor import extract_session_meta

        lines = [_assistant_msg("bash", {}, model="claude-haiku-20250514")]
        meta = extract_session_meta("s1", lines)
        assert "claude-haiku-20250514" in meta["model_usage"]
        assert meta["model_usage"]["claude-haiku-20250514"]["input_tokens"] == 1000


class TestAggregateMetas:
    def _make_meta(
        self,
        session_id: str = "s1",
        cost: float = 0.05,
        input_tokens: int = 1000,
        output_tokens: int = 200,
        duration: float = 300.0,
        tool_counts: dict | None = None,
    ) -> dict:
        return {
            "session_id": session_id,
            "total_messages": 5,
            "duration_seconds": duration,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "total_cost": cost,
            "credits": 0.0,
            "lines_added": 10,
            "lines_removed": 5,
            "files_modified": 2,
            "git_commits": 1,
            "git_pushes": 0,
            "tool_errors": 0,
            "user_interruptions": 0,
            "uses_subagent": False,
            "uses_mcp": False,
            "tool_counts": tool_counts or {"bash": 3},
            "languages": {"Python": 2},
            "tool_error_categories": {},
            "model_usage": {
                "claude-haiku": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost": cost,
                    "messages": 5,
                }
            },
            "project_path": "/repo/app",
            "user_response_times": [10.0, 20.0],
            "message_hours": [10, 11],
            "start_time": "2026-01-01T10:00:00Z",
            "harness": "claude-code",
            "user_message_count": 3,
            "user_message_timestamps": [],
            "layer_hash": "",
            "agent_version": "",
        }

    def test_aggregates_totals(self):
        from services.insights.session_meta_extractor import aggregate_metas

        metas = [self._make_meta("s1", cost=0.1), self._make_meta("s2", cost=0.2)]
        agg = aggregate_metas(metas)
        assert agg["total_sessions"] == 2
        assert agg["total_cost"] == pytest.approx(0.3)
        assert agg["total_messages"] == 10

    def test_aggregates_tool_counts(self):
        from services.insights.session_meta_extractor import aggregate_metas

        metas = [
            self._make_meta("s1", tool_counts={"bash": 3, "edit": 2}),
            self._make_meta("s2", tool_counts={"bash": 5}),
        ]
        agg = aggregate_metas(metas)
        assert agg["tool_counts"]["bash"] == 8
        assert agg["tool_counts"]["edit"] == 2

    def test_top_tools_sorted_by_frequency(self):
        from services.insights.session_meta_extractor import aggregate_metas

        metas = [self._make_meta(tool_counts={"read": 1, "bash": 10, "edit": 3})]
        agg = aggregate_metas(metas)
        top_names = [t[0] for t in agg["top_tools"]]
        assert top_names[0] == "bash"

    def test_days_active_counted(self):
        from services.insights.session_meta_extractor import aggregate_metas

        m1 = self._make_meta("s1")
        m1["start_time"] = "2026-01-01T10:00:00Z"
        m2 = self._make_meta("s2")
        m2["start_time"] = "2026-01-02T10:00:00Z"
        agg = aggregate_metas([m1, m2])
        assert agg["days_active"] == 2

    def test_empty_metas_returns_zero_sessions(self):
        from services.insights.session_meta_extractor import aggregate_metas

        agg = aggregate_metas([])
        assert agg["total_sessions"] == 0

    def test_model_tier_subscription_detected(self):
        """Model with high token usage but near-zero cost gets 'subscription' tier."""
        from services.insights.session_meta_extractor import aggregate_metas

        meta = self._make_meta(input_tokens=100_000, output_tokens=20_000, cost=0.0)
        meta["model_usage"] = {
            "claude-sub": {"input_tokens": 100_000, "output_tokens": 20_000, "cost": 0.0, "messages": 50}
        }
        agg = aggregate_metas([meta])
        assert agg.get("model_tiers", {}).get("claude-sub") == "subscription"
