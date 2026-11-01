# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 RAWx18 <rawx18.dev@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Kiro JSONL session parser.

Handles two transcript formats.

The CLI writes flat lines keyed by ``kind``:
  { "version": "v1", "kind": "Prompt"|"AssistantMessage"|"ToolResults", "data": {...} }

The IDE writes enveloped records keyed by ``payload.type``:
  { "id": ..., "timestamp": ..., "payload": {"type": "user"|"assistant"|..., ...} }

Both are normalised to the same ``hook_*`` events, because that is the
vocabulary the trace viewer renders. The event names produced by
``ingest_classify`` (assistant_text, thinking, ...) are a separate vocabulary
used for ClickHouse event_type and counting; a session that reaches the UI
carrying those names has fallen through to ``basic_event`` and will render
blank, since the viewer reads content from ``attributes`` and never ``body``.

Key differences from the Claude Code format:
- Top-level discriminator is ``kind`` (not ``type``)
- All payload lives under ``data``
- Content items use ``{kind, data}`` instead of ``{type, text/id/name/...}``
- Timestamps come from ``data.meta.timestamp`` (unix epoch *seconds*, integer) and
  are only present on ``Prompt`` lines -- subsequent lines inherit the same ts
- No token-usage data in the JSONL
- Tool inputs carry a Kiro-internal ``__tool_use_purpose`` key that is stripped
- ToolResults carries both a simple ``content`` array and a richer ``results`` map;
  we read from ``content`` so we never need the ``results`` map
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime

from .base import basic_event, dict_field, list_field, load_line, pick_timestamp, str_field


def parse_rows(rows: list[dict]) -> list[dict]:
    """Parse raw_line Kiro JSONL rows into normalised frontend events.

    Each ClickHouse row contains a ``raw_line`` field holding one line of the
    Kiro CLI session transcript.  This function expands each row into one or
    more virtual events that the frontend trace viewer understands.

    Merging: ``ToolResults`` rows are merged back into the preceding
    ``AssistantMessage`` tool-use events keyed by ``toolUseId``.
    """
    events: list[dict] = []
    # Maps toolUseId -> index in events list for merge-on-result
    tool_use_index: dict[str, int] = {}
    latest_model = ""

    # A sub-execution's prompt is supplied by the client rather than read from
    # the transcript, so it arrives appended and would otherwise sort after the
    # reply it prompted. Rows are ordered by line_offset, so hoist it here.
    rows = sorted(rows, key=_sub_execution_prompt_first)

    for row in rows:
        raw_line = row.get("raw_line", "")
        ingested_at = row.get("ingested_at", "")
        row_ts = row.get("timestamp", "")
        harness = row.get("harness", "kiro")

        if not raw_line:
            events.append(basic_event(row))
            continue

        line = load_line(raw_line)
        if line is None:
            events.append(basic_event(row))
            continue

        payload = line.get("payload")
        if isinstance(payload, dict):
            if payload.get("type") == "assistant":
                latest_model = extract_usage(line)["model"] or latest_model
            _handle_ide_payload(
                payload,
                pick_timestamp(line.get("timestamp"), row_ts, ingested_at),
                harness,
                events,
                tool_use_index,
            )
            continue

        kind = line.get("kind", "")
        data = line.get("data", {})
        if not isinstance(data, dict):
            events.append(basic_event(row))
            continue

        # Timestamps only exist on Prompt lines (meta.timestamp = unix epoch seconds).
        # For everything else we fall back to the row ts / ingested_at via pick_timestamp.
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        epoch_s = meta.get("timestamp") if meta else None
        jsonl_ts = _epoch_to_clickhouse(epoch_s) if epoch_s else None
        ts = pick_timestamp(jsonl_ts, row_ts, ingested_at)

        if kind == "KiroCredits":
            # Synthetic row written by kiro_session_push with lifetime credits.
            row_credits = row.get("credits") or data.get("credits")
            credits = _as_credits(row_credits)
            if row_credits and credits is not None:
                attributes = {"credits": str(row_credits)}
                if latest_model:
                    attributes["model"] = latest_model
                events.append(
                    {
                        "timestamp": ts,
                        "event_name": "kiro_credits",
                        "body": f"{credits:.4f} credits",
                        "attributes": attributes,
                        "service_name": harness,
                    }
                )
        elif kind == "Prompt":
            _handle_prompt(data, ts, harness, events)
        elif kind == "AssistantMessage":
            latest_model = extract_usage(line)["model"] or latest_model
            _handle_assistant_message(data, ts, harness, events, tool_use_index)
        elif kind == "ToolResults":
            _handle_tool_results(data, ts, harness, events, tool_use_index)
        else:
            events.append(basic_event(row))

    return events


# ---------------------------------------------------------------------------
# IDE format
# ---------------------------------------------------------------------------


def _sub_execution_prompt_first(row: dict) -> int:
    """Sort key placing an injected sub-execution prompt before the reply."""
    raw = row.get("raw_line", "")
    if not raw or "_observalSubExecutionPrompt" not in raw:
        return 1
    line = load_line(raw)
    payload = line.get("payload") if isinstance(line, dict) else None
    if isinstance(payload, dict) and payload.get("_observalSubExecutionPrompt"):
        return 0
    return 1


# Payload types that carry no content the trace viewer can render. The ingest
# classifier stores them as "meta" so nothing is lost from the counts; emitting
# them here would only produce blank rows.
_IDE_SKIPPED_TYPES = frozenset(
    {
        "session_start",
        "session_event",
        "session_metadata",
        "turn_start",
        "turn_end",
        "pending_interaction",
        "interaction_resolved",
        "ContextualHookInvoked",
        "sub_agent_start",
        "sub_agent_complete",
        # Per-turn credit amounts. The session total is already written once as
        # a synthetic kiro_credits row (see extra_rows), and that total is the
        # sum of exactly these payloads - rendering both shows the same spend
        # twice.
        "usage_summary",
    }
)


def _is_elided_reasoning(text: str) -> bool:
    """Return True when a Reasoning payload carries no readable trace.

    Kiro does not persist reasoning text. Every Reasoning payload observed
    holds the literal placeholder "..." while the real trace stays sealed in
    ``reasoningSignature``, so rendering them produces a run of "Thinking ..."
    rows with nothing inside. The Kiro CLI transcript has no reasoning records
    at all, so skipping these also keeps the two layouts consistent.

    Written as a content check rather than a blanket skip so that a future Kiro
    that does persist reasoning renders it without needing a code change.
    """
    return not text.strip(". \u2026\t\n\r")


def _handle_ide_payload(
    payload: dict,
    ts: str,
    harness: str,
    events: list[dict],
    tool_use_index: dict[str, int],
) -> None:
    """Expand one IDE payload into the hook_* events the viewer renders."""
    ptype = str_field(payload, "type")

    if ptype == "user":
        text = str_field(payload, "content")
        if text.strip():
            events.append(
                {
                    "timestamp": ts,
                    "event_name": "hook_userpromptsubmit",
                    "body": text[:100],
                    "attributes": {"tool_input": text},
                    "service_name": harness,
                }
            )
        return

    if ptype == "assistant":
        text = str_field(payload, "content")
        if not text.strip():
            return
        # Reasoning is the model's internal trace, not user-visible output.
        if str_field(payload, "operationType") == "Reasoning":
            if _is_elided_reasoning(text):
                return
            events.append(
                {
                    "timestamp": ts,
                    "event_name": "hook_assistant_thinking",
                    "body": text[:100],
                    "attributes": {"tool_response": text},
                    "service_name": harness,
                }
            )
            return
        events.append(
            {
                "timestamp": ts,
                "event_name": "hook_assistant_response",
                "body": text[:100],
                "attributes": {"tool_response": text},
                "service_name": harness,
            }
        )
        return

    if ptype == "tool_call":
        tool_name = str_field(payload, "toolName")
        tool_call_id = str_field(payload, "toolCallId")
        args = dict_field(payload, "args")
        attributes = {
            "tool_name": tool_name,
            "tool_input": json.dumps(args),
            "tool_use_id": tool_call_id,
        }
        title = str_field(payload, "title")
        if title:
            attributes["tool_title"] = title
        tool_use_index[tool_call_id] = len(events)
        events.append(
            {
                "timestamp": ts,
                "event_name": "hook_posttooluse",
                "body": tool_name,
                "attributes": attributes,
                "service_name": harness,
            }
        )
        return

    if ptype == "tool_result":
        # Merge back into the tool_call this result belongs to, matching how the
        # CLI parser merges ToolResults, so the viewer shows one tool event with
        # both its input and its output.
        tool_call_id = str_field(payload, "toolCallId")
        index = tool_use_index.get(tool_call_id)
        if index is None:
            return  # orphan result -- the call was never seen
        attributes = events[index]["attributes"]
        attributes["tool_response"] = str_field(payload, "content")
        duration = payload.get("durationMs")
        if isinstance(duration, int | float):
            attributes["duration_ms"] = str(int(duration))
        if payload.get("success") is False:
            attributes["tool_status"] = "error"
            attributes["success"] = "false"
        else:
            attributes["success"] = "true"
        return

    if ptype in _IDE_SKIPPED_TYPES:
        return

    # Unknown payload -- surface it rather than dropping it silently.
    events.append(
        {
            "timestamp": ts,
            "event_name": "system",
            "body": ptype,
            "attributes": {"payload_type": ptype},
            "service_name": harness,
        }
    )


# ---------------------------------------------------------------------------
# Value helpers
# ---------------------------------------------------------------------------


def _epoch_to_clickhouse(epoch_s: int | float) -> str | None:
    """Convert unix epoch seconds to a ClickHouse datetime string.

    Returns None for values a transcript can carry but ``datetime`` cannot
    represent, so the caller falls back to the row timestamp.
    """
    try:
        dt = datetime.fromtimestamp(float(epoch_s), tz=UTC)
    except (OverflowError, OSError, TypeError, ValueError):
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S.000")


def _as_credits(value: object) -> float | None:
    """Return a credit balance as a float, or None when the transcript lied."""
    if isinstance(value, bool):
        return None
    try:
        credits = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return credits if math.isfinite(credits) else None


# ---------------------------------------------------------------------------
# Internal handlers
# ---------------------------------------------------------------------------


def _handle_prompt(data: dict, ts: str, harness: str, events: list[dict]) -> None:
    content = list_field(data, "content")
    text_parts = [
        item.get("data", "")
        for item in content
        if isinstance(item, dict) and item.get("kind") == "text" and isinstance(item.get("data"), str)
    ]
    full_text = "\n".join(text_parts)
    if full_text:
        events.append(
            {
                "timestamp": ts,
                "event_name": "hook_userpromptsubmit",
                "body": full_text[:100],
                "attributes": {"tool_input": full_text},
                "service_name": harness,
            }
        )


def _handle_assistant_message(
    data: dict,
    ts: str,
    harness: str,
    events: list[dict],
    tool_use_index: dict[str, int],
) -> None:
    content = list_field(data, "content")

    for item in content:
        if not isinstance(item, dict):
            continue
        item_kind = item.get("kind", "")
        item_data = item.get("data", "")

        if item_kind == "text":
            text = item_data if isinstance(item_data, str) else ""
            if not text.strip():
                continue  # skip empty filler blocks Kiro emits between tool calls
            events.append(
                {
                    "timestamp": ts,
                    "event_name": "hook_assistant_response",
                    "body": text[:100],
                    "attributes": {"tool_response": text},
                    "service_name": harness,
                }
            )

        elif item_kind == "toolUse":
            if not isinstance(item_data, dict):
                continue
            tool_use_id = str_field(item_data, "toolUseId")
            tool_name = item_data.get("name", "")
            tool_input = dict_field(item_data, "input")
            # Strip Kiro-internal annotation key
            clean_input = {k: v for k, v in tool_input.items() if not k.startswith("__")}
            idx = len(events)
            events.append(
                {
                    "timestamp": ts,
                    "event_name": "hook_posttooluse",
                    "body": tool_name,
                    "attributes": {
                        "tool_name": tool_name,
                        "tool_input": json.dumps(clean_input),
                        "tool_use_id": tool_use_id,
                    },
                    "service_name": harness,
                }
            )
            if tool_use_id:
                tool_use_index[tool_use_id] = idx


def _handle_tool_results(
    data: dict,
    ts: str,
    harness: str,
    events: list[dict],
    tool_use_index: dict[str, int],
) -> None:
    content = list_field(data, "content")

    for item in content:
        if not isinstance(item, dict) or item.get("kind") != "toolResult":
            continue
        item_data = item.get("data", {})
        if not isinstance(item_data, dict):
            continue

        tool_use_id = str_field(item_data, "toolUseId")
        status = item_data.get("status", "success")
        result_content = list_field(item_data, "content")

        result_text = _extract_result_text(result_content)
        if status == "error":
            result_text = f"[error] {result_text}" if result_text else "[error]"

        if tool_use_id and tool_use_id in tool_use_index:
            existing = events[tool_use_index[tool_use_id]]
            existing["attributes"]["tool_response"] = result_text
            if status == "error":
                existing["attributes"]["tool_status"] = "error"
        # else: orphan tool result -- skip


def _extract_result_text(result_content: list) -> str:
    """Extract plain text from a Kiro tool-result content array.

    Each item is ``{kind: "text"|"json", data: str|{content:[{type,text}]}}``.
    """
    parts: list[str] = []
    for c in result_content:
        if not isinstance(c, dict):
            continue
        c_kind = c.get("kind", "")
        c_data = c.get("data", "")
        if c_kind == "text" and isinstance(c_data, str):
            parts.append(c_data)
        elif c_kind == "json" and isinstance(c_data, dict):
            # Nested Claude-style content array: [{type:"text", text:"..."}]
            for sub in list_field(c_data, "content"):
                if isinstance(sub, dict) and sub.get("type") == "text":
                    parts.append(str_field(sub, "text"))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Write-path helpers - called by session_ingest's harness dispatch
# ---------------------------------------------------------------------------


def extract_usage(parsed: dict) -> dict:
    """Extract Kiro's model from CLI assistant blocks (no token counts in JSONL).

    The Claude Code usage extractor reads ``message.model``; Kiro's CLI puts
    ``modelId`` inside each content block's ``data`` instead. IDE records may
    carry a model on the payload, but must never be interpreted as Claude rows.
    """
    model = ""
    if parsed.get("kind") == "AssistantMessage":
        data = dict_field(parsed, "data")
        for item in list_field(data, "content"):
            if not isinstance(item, dict):
                continue
            block = item.get("data")
            if isinstance(block, dict):
                model = str_field(block, "modelId") or model
    else:
        payload = parsed.get("payload")
        if isinstance(payload, dict) and payload.get("type") == "assistant":
            model = str_field(payload, "modelId")
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "model": model,
    }


# ---------------------------------------------------------------------------
# Write-path extras - called by ingest_classify.get_extra_rows()
# ---------------------------------------------------------------------------


def extra_ingest_rows(
    session_id: str,
    project_id: str,
    user_id: str,
    agent_id: str | None,
    agent_version: str | None,
    harness: str,
    total_credits: float | None,
) -> list[dict]:
    """Return Kiro-specific extra rows to write after the main ingest loop.

    Stores a single ``kiro_credits`` row at line_offset=0xFFFFFFFF so the
    sessions list query can aggregate ``sum(credits)`` per session.
    ReplacingMergeTree deduplication makes repeated inserts idempotent.
    Returns an empty list when no credits are available.
    """
    if total_credits is None or total_credits <= 0:
        return []
    return [
        {
            "session_id": session_id,
            "project_id": project_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "agent_version": agent_version,
            "harness": harness,
            "line_offset": 0xFFFFFFFF,
            "line_hash": "",
            "layer_hash": None,
            "event_type": "kiro_credits",
            "timestamp": "2099-12-31 00:00:00.000",
            "uuid": None,
            "parent_uuid": None,
            "tool_name": None,
            "tool_id": None,
            "content_preview": f"{total_credits:.6f} credits",
            "content_length": 0,
            "raw_line": json.dumps({"kind": "KiroCredits", "credits": total_credits}),
            "credits": total_credits,
        }
    ]
