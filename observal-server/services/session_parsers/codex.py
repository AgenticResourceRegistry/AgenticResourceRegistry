# SPDX-FileCopyrightText: 2026 Tanvi Reddy <reddyplayer22@gmail.com>
# SPDX-FileCopyrightText: 2026 RAWx18 <rawx18.dev@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Codex CLI JSONL session parser (READ path).

Normalizes raw ClickHouse rows into frontend-displayable events for the
trace viewer. Codex uses a distinct format:
  - {"type": "event_msg", "payload": {"type": "user_message|agent_message|..."}}
  - {"type": "response_item", "payload": {"role": "user|assistant", "content": [...]}}
  - {"type": "session_meta|turn_context"}
"""

from __future__ import annotations

import json

from .base import (
    basic_event,
    dict_field,
    list_field,
    load_line,
    pick_timestamp,
    str_field,
    token_count,
    uncached_input_tokens,
)


def parse_rows(rows: list[dict]) -> list[dict]:
    """Parse raw_line Codex JSONL rows into normalized frontend events."""
    events: list[dict] = []

    for row in rows:
        raw_line = row.get("raw_line", "")
        ingested_at = row.get("ingested_at", "")
        row_ts = row.get("timestamp", "")
        harness = row.get("harness", "codex")

        if not raw_line:
            events.append(basic_event(row))
            continue

        parsed = load_line(raw_line)
        if parsed is None:
            events.append(basic_event(row))
            continue

        ts = pick_timestamp(parsed.get("timestamp"), row_ts, ingested_at)
        line_type = parsed.get("type", "")

        if line_type == "event_msg":
            _handle_event_msg(parsed, ts, harness, events)
        elif line_type == "response_item":
            _handle_response_item(parsed, ts, harness, events)
        elif line_type in ("session_meta", "turn_context"):
            events.append(
                {
                    "timestamp": ts,
                    "event_name": "system",
                    "body": line_type,
                    "attributes": {},
                    "service_name": harness,
                }
            )
        else:
            events.append(
                {
                    "timestamp": ts,
                    "event_name": "system",
                    "body": line_type or "unknown",
                    "attributes": {},
                    "service_name": harness,
                }
            )

    return events


def _handle_event_msg(parsed: dict, ts: str, harness: str, events: list[dict]) -> None:
    payload = parsed.get("payload", {})
    if not isinstance(payload, dict):
        return
    payload_type = payload.get("type", "")

    if payload_type == "user_message":
        message = payload.get("message", "")
        events.append(
            {
                "timestamp": ts,
                "event_name": "hook_userpromptsubmit",
                "body": str(message)[:200],
                "attributes": {"tool_input": str(message)},
                "service_name": harness,
            }
        )
    elif payload_type == "agent_message":
        message = payload.get("message", "")
        events.append(
            {
                "timestamp": ts,
                "event_name": "hook_assistant_response",
                "body": str(message)[:200],
                "attributes": {"tool_response": str(message)},
                "service_name": harness,
            }
        )
    elif payload_type == "token_count":
        # Emit token usage as attributes for the detail view to sum
        info = dict_field(payload, "info")
        usage = dict_field(info, "last_token_usage") or dict_field(info, "total_token_usage")
        # Try to get model from rate_limits (Codex sometimes includes it)
        model = ""
        rate_limits = payload.get("rate_limits", {})
        if isinstance(rate_limits, dict):
            model = rate_limits.get("model", "")
        # Codex input_tokens includes cached_input_tokens (OpenAI convention).
        cached = token_count(usage, "cached_input_tokens")
        events.append(
            {
                "timestamp": ts,
                "event_name": "hook_token_usage",
                "body": "token_count",
                "attributes": {
                    "input_tokens": str(uncached_input_tokens(token_count(usage, "input_tokens"), cached)),
                    "output_tokens": str(token_count(usage, "output_tokens")),
                    "cache_read_tokens": str(cached),
                    "model": model,
                },
                "service_name": harness,
            }
        )
    elif payload_type in ("task_started", "task_complete"):
        events.append(
            {
                "timestamp": ts,
                "event_name": "system",
                "body": payload_type,
                "attributes": {},
                "service_name": harness,
            }
        )
    else:
        events.append(
            {
                "timestamp": ts,
                "event_name": "system",
                "body": payload_type,
                "attributes": {},
                "service_name": harness,
            }
        )


def _handle_response_item(parsed: dict, ts: str, harness: str, events: list[dict]) -> None:
    payload = parsed.get("payload", {})
    if not isinstance(payload, dict):
        return
    role = payload.get("role", "")
    content = list_field(payload, "content")
    payload_type = payload.get("type", "")

    # Direct function_call / function_call_output at payload level
    if payload_type == "function_call":
        events.append(
            {
                "timestamp": ts,
                "event_name": "hook_pretooluse",
                "body": payload.get("name", ""),
                "attributes": {
                    "tool_name": payload.get("name", ""),
                    "tool_input": payload.get("arguments", ""),
                    "tool_use_id": payload.get("call_id", ""),
                },
                "service_name": harness,
            }
        )
        return

    if payload_type == "function_call_output":
        events.append(
            {
                "timestamp": ts,
                "event_name": "hook_posttooluse",
                "body": payload.get("name", "tool_result"),
                "attributes": {
                    "tool_name": payload.get("name", ""),
                    "tool_response": str(payload.get("output", ""))[:500],
                    "tool_use_id": payload.get("call_id", ""),
                    "success": "true",
                },
                "service_name": harness,
            }
        )
        return

    if role == "user":
        # Injected context (AGENTS.md, permissions) - system, not real user input
        events.append(
            {
                "timestamp": ts,
                "event_name": "system",
                "body": "context",
                "attributes": {},
                "service_name": harness,
            }
        )
    elif role == "assistant":
        # Check for tool calls vs text response
        text_parts = []
        tool_calls = []
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type", "")
                if btype == "output_text":
                    text_parts.append(str_field(block, "text"))
                elif btype == "function_call":
                    tool_calls.append(block)
                elif btype == "function_call_output":
                    events.append(
                        {
                            "timestamp": ts,
                            "event_name": "tool_result",
                            "body": block.get("name", "tool_result"),
                            "attributes": {
                                "tool_name": block.get("name", ""),
                                "tool_response": str(block.get("output", ""))[:500],
                            },
                            "service_name": harness,
                        }
                    )

        for tc in tool_calls:
            events.append(
                {
                    "timestamp": ts,
                    "event_name": "tool_call",
                    "body": tc.get("name", ""),
                    "attributes": {
                        "tool_name": tc.get("name", ""),
                        "tool_input": json.dumps(tc.get("arguments", {})),
                        "tool_use_id": tc.get("call_id", ""),
                    },
                    "service_name": harness,
                }
            )

        if text_parts and not tool_calls:
            body = " ".join(text_parts)[:200]
            events.append(
                {
                    "timestamp": ts,
                    "event_name": "hook_assistant_response",
                    "body": body,
                    "attributes": {"tool_response": " ".join(text_parts)},
                    "service_name": harness,
                }
            )
    elif role == "developer":
        events.append(
            {
                "timestamp": ts,
                "event_name": "system",
                "body": "developer instructions",
                "attributes": {},
                "service_name": harness,
            }
        )
    else:
        events.append(
            {
                "timestamp": ts,
                "event_name": "system",
                "body": role or "response_item",
                "attributes": {},
                "service_name": harness,
            }
        )
