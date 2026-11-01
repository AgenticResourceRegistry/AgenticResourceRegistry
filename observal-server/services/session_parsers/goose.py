# SPDX-FileCopyrightText: 2026 RAWx18 <rawx18.dev@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Goose session parser.

Goose persists sessions in SQLite, so the CLI adapter projects each session
onto an append-only JSONL mirror (see ``observal_cli.sessions.goose``).  Every
mirrored line is one of:

``{"type": "session", ...}``      session metadata boundary
``{"type": "message", ...}``      a ``messages`` row: role, content blocks, metadata
``{"type": "session_end", ...}``  final boundary with the session's usage totals

Content blocks use goose's camelCase tags: ``text``, ``image``, ``toolRequest``,
``toolResponse``, ``thinking``, ``redactedThinking``, ``toolConfirmationRequest``,
``frontendToolRequest``, ``actionRequired``, ``systemNotification`` and ``error``.
"""

from __future__ import annotations

import json
from typing import Any

from .base import basic_event, load_line, pick_timestamp, strip_ansi, token_count, uncached_input_tokens

_MAX_BODY = 120
_MAX_RESPONSE = 2000


def parse_rows(rows: list[dict]) -> list[dict]:
    """Parse mirrored Goose session rows into normalised frontend events."""
    events: list[dict] = []
    # Maps a goose tool id -> index of its tool_call event, for merge-on-result.
    tool_index: dict[str, int] = {}

    for row in rows:
        raw_line = row.get("raw_line", "")
        harness = row.get("harness", "goose")
        if not raw_line:
            events.append(basic_event(row))
            continue
        line = load_line(raw_line)
        if line is None:
            events.append(basic_event(row))
            continue

        ts = pick_timestamp(line.get("timestamp"), row.get("timestamp", ""), row.get("ingested_at", ""))
        record_type = line.get("type", "")

        if record_type == "session":
            events.append(_session_event(line, ts, harness))
        elif record_type == "session_end":
            events.append(_session_end_event(line, ts, harness))
        elif record_type == "message":
            _handle_message(line, ts, harness, events, tool_index)
        else:
            events.append(basic_event(row))

    return events


# ---------------------------------------------------------------------------
# Boundary events
# ---------------------------------------------------------------------------


def _session_event(line: dict, ts: str, harness: str) -> dict:
    attributes = {
        key: str(line.get(key) or "")
        for key in ("working_dir", "session_type", "provider", "model", "goose_mode")
        if line.get(key)
    }
    if line.get("parent_session_id"):
        attributes["parent_session_id"] = str(line["parent_session_id"])
    return {
        "timestamp": ts,
        "event_name": "hook_sessionstart",
        "body": str(line.get("name") or line.get("session_id") or "")[:_MAX_BODY],
        "attributes": attributes,
        "service_name": harness,
    }


def _session_end_event(line: dict, ts: str, harness: str) -> dict:
    usage = line.get("usage") if isinstance(line.get("usage"), dict) else {}
    attributes = {
        name: str(usage[key])
        for key, name in (
            ("inputTokens", "input_tokens"),
            ("outputTokens", "output_tokens"),
            ("totalTokens", "total_tokens"),
            ("cost", "cost"),
        )
        if usage.get(key)
    }
    return {
        "timestamp": ts,
        "event_name": "hook_stop",
        "body": "",
        "attributes": attributes,
        "service_name": harness,
    }


# ---------------------------------------------------------------------------
# Message events
# ---------------------------------------------------------------------------


def _handle_message(
    line: dict,
    ts: str,
    harness: str,
    events: list[dict],
    tool_index: dict[str, int],
) -> None:
    content = line.get("content")
    if not isinstance(content, list):
        return
    role = str(line.get("role") or "")
    token_attrs = _token_attributes(line.get("metadata"))

    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type", "")

        if block_type == "text":
            text = strip_ansi(str(block.get("text") or ""))
            if not text.strip():
                continue
            if role == "user":
                events.append(_event(ts, harness, "hook_userpromptsubmit", text, {"tool_input": text}))
            else:
                attributes: dict[str, str] = {"tool_response": text[:_MAX_RESPONSE]}
                attributes.update(token_attrs)
                token_attrs = {}  # consumed by the first assistant text block
                events.append(_event(ts, harness, "hook_assistant_response", text, attributes))

        elif block_type == "thinking":
            thinking = strip_ansi(str(block.get("thinking") or ""))
            if thinking.strip():
                events.append(
                    _event(
                        ts, harness, "hook_assistant_thinking", thinking, {"tool_response": thinking[:_MAX_RESPONSE]}
                    )
                )

        elif block_type == "redactedThinking":
            events.append(_event(ts, harness, "hook_assistant_thinking", "[redacted]", {}))

        elif block_type in ("toolRequest", "frontendToolRequest"):
            tool_id = str(block.get("id") or "")
            name, arguments, error = _tool_call(block.get("toolCall"))
            attributes = {"tool_name": name, "tool_input": json.dumps(arguments), "tool_use_id": tool_id}
            if error:
                attributes["tool_status"] = "error"
                attributes["tool_response"] = error[:_MAX_RESPONSE]
            if tool_id:
                tool_index[tool_id] = len(events)
            events.append(_event(ts, harness, "hook_posttooluse", name, attributes))

        elif block_type == "toolResponse":
            _merge_tool_response(block, ts, harness, events, tool_index)

        elif block_type == "toolConfirmationRequest":
            tool_name = str(block.get("toolName") or "")
            events.append(
                _event(
                    ts,
                    harness,
                    "hook_pretooluse",
                    tool_name,
                    {"tool_name": tool_name, "tool_input": json.dumps(block.get("arguments") or {})},
                )
            )

        elif block_type == "error":
            message = str(block.get("message") or block.get("msg") or "")
            events.append(_event(ts, harness, "hook_error", message, {"tool_response": message[:_MAX_RESPONSE]}))

    if token_attrs:
        # A tool-only assistant turn still reports usage; surface it standalone.
        events.append(_event(ts, harness, "hook_token_usage", "", token_attrs))


def _event(ts: str, harness: str, event_name: str, body: str, attributes: dict[str, str]) -> dict:
    return {
        "timestamp": ts,
        "event_name": event_name,
        "body": body[:_MAX_BODY],
        "attributes": attributes,
        "service_name": harness,
    }


def _token_attributes(metadata: Any) -> dict[str, str]:
    """Extract per-message token counts and the resolved model from metadata."""
    if not isinstance(metadata, dict):
        return {}
    usage = metadata.get("usage")
    attributes: dict[str, str] = {}
    if isinstance(usage, dict):
        # Goose counts cache reads and writes inside inputTokens.
        input_tokens = uncached_input_tokens(
            token_count(usage, "inputTokens"),
            token_count(usage, "cacheReadTokens"),
            token_count(usage, "cacheWriteTokens"),
        )
        if input_tokens:
            attributes["input_tokens"] = str(input_tokens)
        for key, name in (
            ("outputTokens", "output_tokens"),
            ("cacheReadTokens", "cache_read_tokens"),
            ("cacheWriteTokens", "cache_creation_tokens"),
        ):
            count = token_count(usage, key)
            if count:
                attributes[name] = str(count)
        if usage.get("cost"):
            attributes["cost"] = str(usage["cost"])
    inference = metadata.get("inference")
    if isinstance(inference, dict):
        model = inference.get("resolvedModel") or inference.get("requestedModel")
        if model:
            attributes["model"] = str(model)
    return attributes


def _tool_call(tool_call: Any) -> tuple[str, dict, str]:
    """Return ``(name, arguments, error)`` from a goose ``toolCall`` envelope."""
    if not isinstance(tool_call, dict):
        return "", {}, ""
    if tool_call.get("status") == "error" or "error" in tool_call:
        return "", {}, str(tool_call.get("error") or "")
    value = tool_call.get("value")
    if not isinstance(value, dict):
        return "", {}, ""
    arguments = value.get("arguments")
    return str(value.get("name") or ""), arguments if isinstance(arguments, dict) else {}, ""


def _merge_tool_response(block: dict, ts: str, harness: str, events: list[dict], tool_index: dict[str, int]) -> None:
    """Attach a tool result to its request, or emit it standalone when orphaned."""
    tool_id = str(block.get("id") or "")
    text, failed = _tool_result_text(block.get("toolResult"))
    index = tool_index.get(tool_id) if tool_id else None
    if index is not None and index < len(events):
        target = events[index]
        target["attributes"]["tool_response"] = text[:_MAX_RESPONSE]
        if failed:
            target["attributes"]["tool_status"] = "error"
        return
    attributes = {"tool_use_id": tool_id, "tool_response": text[:_MAX_RESPONSE]}
    if failed:
        attributes["tool_status"] = "error"
    events.append(_event(ts, harness, "hook_posttooluse", text, attributes))


def _tool_result_text(tool_result: Any) -> tuple[str, bool]:
    """Return ``(text, failed)`` for a goose ``toolResult`` envelope.

    Handles both the current ``CallToolResult`` shape (``value.content`` plus
    ``value.isError``) and the legacy shape where ``value`` is the content list.
    """
    if not isinstance(tool_result, dict):
        return "", False
    if tool_result.get("status") == "error" or "error" in tool_result:
        return str(tool_result.get("error") or ""), True
    value = tool_result.get("value")
    if isinstance(value, dict):
        return _content_text(value.get("content")), bool(value.get("isError"))
    return _content_text(value), False


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [str(item.get("text") or "") for item in content if isinstance(item, dict) and item.get("type") == "text"]
    return "\n".join(part for part in parts if part)
