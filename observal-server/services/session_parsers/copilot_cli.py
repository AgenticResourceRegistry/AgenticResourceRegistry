# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-FileCopyrightText: 2026 RAWx18 <rawx18.dev@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Copilot CLI JSONL session parser.

Copilot CLI envelope format - each line is:
    {"agentId": "...", "ts": "ISO-8601", "event": {"type": "...", ...fields}}

The event dict contains the type and all event-specific fields directly.
"""

from __future__ import annotations

import json

from .base import basic_event, load_line, pick_timestamp, str_field

_SKIP_TYPES = frozenset({"assistant.message_delta"})


def _sanitize_raw_line(raw_line: str) -> str:
    """Strip trailing NULs and replace U+2028/U+2029 before JSON parsing."""
    raw_line = raw_line.rstrip("\x00")
    raw_line = raw_line.replace("\u2028", "\\n").replace("\u2029", "\\n")
    return raw_line


def parse_rows(rows: list[dict]) -> list[dict]:
    """Parse raw_line Copilot CLI JSONL rows into normalized frontend events."""
    events: list[dict] = []
    tool_call_index: dict[str, int] = {}

    for row in rows:
        raw_line = row.get("raw_line", "")
        ingested_at = row.get("ingested_at", "")
        row_ts = row.get("timestamp", "")
        harness = row.get("harness", "copilot-cli")

        if not raw_line:
            events.append(basic_event(row))
            continue

        sanitized = _sanitize_raw_line(raw_line)
        parsed = load_line(sanitized)
        if parsed is None:
            events.append(basic_event(row))
            continue

        # Unwrap envelope: {agentId, ts, event: {type, ...fields}}
        envelope_event = parsed.get("event")
        if isinstance(envelope_event, dict):
            # Envelope format
            event_type = str_field(envelope_event, "type")
            data = {k: v for k, v in envelope_event.items() if k != "type"}
            ts = pick_timestamp(parsed.get("ts"), row_ts, ingested_at)
        else:
            # Flat format fallback: {type, data, id, timestamp, parentId}
            event_type = str_field(parsed, "type")
            data = parsed.get("data", {})
            if not isinstance(data, dict):
                data = {}
            ts = pick_timestamp(parsed.get("timestamp"), row_ts, ingested_at)

        if not event_type:
            events.append(basic_event(row))
            continue

        if event_type in _SKIP_TYPES:
            continue

        if event_type == "user.message":
            _handle_user_message(data, ts, harness, events)
        elif event_type == "assistant.message":
            _handle_assistant_message(data, ts, harness, events)
        elif event_type == "tool.call":
            _handle_tool_call(data, ts, harness, events, tool_call_index, str_field(data, "toolCallId"))
        elif event_type in ("tool.result", "tool.execution_complete"):
            _handle_tool_result(data, ts, harness, events, tool_call_index, str_field(data, "toolCallId"))
        elif event_type == "agent.thinking":
            _handle_thinking(data, ts, harness, events)
        elif event_type == "session.start":
            _handle_session_start(data, ts, harness, events)
        elif event_type == "session.end":
            _handle_session_end(data, ts, harness, events)
        else:
            events.append(
                {
                    "timestamp": ts,
                    "event_name": f"copilot_cli_{event_type.replace('.', '_')}",
                    "body": event_type,
                    "attributes": {},
                    "service_name": harness,
                }
            )

    return events


# ---------------------------------------------------------------------------
# Internal handlers
# ---------------------------------------------------------------------------


def _handle_user_message(data: dict, ts: str, harness: str, events: list[dict]) -> None:
    content = data.get("content", "")
    if isinstance(content, dict):
        content = content.get("text", "")
    if not isinstance(content, str):
        content = str(content)
    events.append(
        {
            "timestamp": ts,
            "event_name": "hook_userpromptsubmit",
            "body": content[:100],
            "attributes": {"tool_input": content},
            "service_name": harness,
        }
    )


def _handle_assistant_message(data: dict, ts: str, harness: str, events: list[dict]) -> None:
    content = data.get("content", "")
    if isinstance(content, dict):
        content = content.get("text", "")
    if not isinstance(content, str):
        content = str(content)

    attrs: dict = {"tool_response": content}
    model = data.get("model", "")
    if model:
        attrs["model"] = model
    usage = data.get("usage", {})
    if usage and isinstance(usage, dict):
        if usage.get("input_tokens"):
            attrs["input_tokens"] = str(usage["input_tokens"])
        if usage.get("output_tokens"):
            attrs["output_tokens"] = str(usage["output_tokens"])

    events.append(
        {
            "timestamp": ts,
            "event_name": "hook_assistant_response",
            "body": content[:100],
            "attributes": attrs,
            "service_name": harness,
        }
    )


def _handle_tool_call(
    data: dict, ts: str, harness: str, events: list[dict], tool_call_index: dict[str, int], tool_call_id: str
) -> None:
    tool_name = data.get("name", data.get("toolName", ""))
    tool_input = data.get("input", data.get("args", {}))

    tool_input_str = json.dumps(tool_input) if isinstance(tool_input, dict) else str(tool_input)

    idx = len(events)
    events.append(
        {
            "timestamp": ts,
            "event_name": "hook_pretooluse",
            "body": tool_name,
            "attributes": {
                "tool_name": tool_name,
                "tool_input": tool_input_str,
                "tool_use_id": tool_call_id,
            },
            "service_name": harness,
        }
    )
    if tool_call_id:
        tool_call_index[tool_call_id] = idx


def _handle_tool_result(
    data: dict, ts: str, harness: str, events: list[dict], tool_call_index: dict[str, int], tool_call_id: str
) -> None:
    result = data.get("output", data.get("result", ""))
    if isinstance(result, dict):
        result_text = result.get("textResultForLlm", result.get("text", json.dumps(result)))
    elif isinstance(result, list):
        result_text = "\n".join(str(r) for r in result)
    else:
        result_text = str(result)

    # Merge only when the producer identifies the same tool invocation.
    if tool_call_id and tool_call_id in tool_call_index:
        existing = events[tool_call_index[tool_call_id]]
        existing["attributes"]["tool_response"] = result_text
    else:
        tool_name = data.get("name", data.get("toolName", ""))
        events.append(
            {
                "timestamp": ts,
                "event_name": "hook_toolresult",
                "body": tool_name or "tool_result",
                "attributes": {
                    "tool_name": tool_name,
                    "tool_response": result_text,
                    "tool_use_id": tool_call_id,
                },
                "service_name": harness,
            }
        )


def _handle_thinking(data: dict, ts: str, harness: str, events: list[dict]) -> None:
    content = data.get("content", data.get("thinking", ""))
    if not isinstance(content, str):
        content = str(content)
    events.append(
        {
            "timestamp": ts,
            "event_name": "hook_assistant_thinking",
            "body": content[:100],
            "attributes": {"tool_response": content},
            "service_name": harness,
        }
    )


def _handle_session_start(data: dict, ts: str, harness: str, events: list[dict]) -> None:
    context = data.get("context", {})
    cwd = context.get("cwd", "") if isinstance(context, dict) else ""
    session_id = data.get("sessionId", "")
    body = f"session start (cwd: {cwd})" if cwd else "session start"
    events.append(
        {
            "timestamp": ts,
            "event_name": "hook_sessionstart",
            "body": body[:100],
            "attributes": {"session_id": session_id, "cwd": cwd},
            "service_name": harness,
        }
    )


def _handle_session_end(data: dict, ts: str, harness: str, events: list[dict]) -> None:
    reason = data.get("reason", "")
    events.append(
        {
            "timestamp": ts,
            "event_name": "hook_sessionend",
            "body": f"session end ({reason})" if reason else "session end",
            "attributes": {"reason": reason},
            "service_name": harness,
        }
    )
