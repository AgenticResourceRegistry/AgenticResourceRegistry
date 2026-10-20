# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 RAWx18 <rawx18.dev@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Claude Code JSONL session parser.

Handles the Claude Code transcript format where each line has:
  { "type": "user"|"assistant"|"system"|"attachment"|..., "message": {...}, ... }
"""

from __future__ import annotations

import json

from .base import (
    basic_event,
    dict_field,
    list_field,
    load_line,
    meta_assistant,
    meta_session,
    meta_time,
    meta_tool_call,
    meta_tool_result,
    meta_usage,
    meta_user,
    pick_timestamp,
    str_field,
    strip_ansi,
    token_count,
)

_META_TYPES = {
    "agent-setting",
    "debug",
    "file-history-snapshot",
    "last-prompt",
    "meta",
    "mode",
    "permission-mode",
    "pr-link",
    "queue-operation",
    "worktree-state",
}


def parse_rows(rows: list[dict]) -> list[dict]:
    """Parse raw_line Claude Code JSONL rows into normalised frontend events.

    Each ClickHouse row contains a ``raw_line`` field holding one line of the
    Claude Code session transcript.  This function expands each row into one
    or more virtual events that the frontend trace viewer understands.

    Merging: ``tool_result`` blocks are merged back into the preceding
    ``tool_use`` event keyed by ``tool_use_id`` rather than emitting a
    separate event.
    """
    events: list[dict] = []
    # Maps tool_use_id -> index in events for merge-on-result
    tool_use_index: dict[str, int] = {}

    for row in rows:
        raw_line = row.get("raw_line", "")
        ingested_at = row.get("ingested_at", "")
        row_ts = row.get("timestamp", "")
        harness = row.get("harness", "")

        if not raw_line:
            events.append(basic_event(row))
            continue

        line = load_line(raw_line)
        if line is None:
            events.append(basic_event(row))
            continue

        msg_type = str_field(line, "type")

        if msg_type in _META_TYPES:
            continue

        ts = pick_timestamp(line.get("timestamp"), row_ts, ingested_at)

        if msg_type == "user":
            _handle_user(line, ts, harness, events, tool_use_index)

        elif msg_type == "assistant":
            _handle_assistant(line, ts, harness, events, tool_use_index)

        elif msg_type == "system":
            system_text = str_field(line, "content")
            events.append(
                {
                    "timestamp": ts,
                    "event_name": "hook_sessionstart",
                    "body": system_text[:100],
                    "attributes": {},
                    "service_name": harness,
                }
            )

        elif msg_type == "attachment":
            attachment = dict_field(line, "attachment")
            attach_name = str_field(attachment, "name")
            events.append(
                {
                    "timestamp": ts,
                    "event_name": "attachment",
                    "body": attach_name[:100],
                    "attributes": {
                        "attachment_type": attachment.get("type", ""),
                        "attachment_name": attach_name,
                    },
                    "service_name": harness,
                }
            )

        else:
            events.append(basic_event(row))

    return events


# ---------------------------------------------------------------------------
# Internal handlers
# ---------------------------------------------------------------------------


def _handle_user(
    line: dict,
    ts: str,
    harness: str,
    events: list[dict],
    tool_use_index: dict[str, int],
) -> None:
    content = dict_field(line, "message").get("content", [])

    if isinstance(content, str):
        events.append(
            {
                "timestamp": ts,
                "event_name": "hook_userpromptsubmit",
                "body": content[:100],
                "attributes": {"tool_input": content},
                "service_name": harness,
            }
        )
        return

    if not isinstance(content, list):
        return

    text_blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "text"]
    result_blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]

    if text_blocks:
        full_text = "\n".join(str_field(b, "text") for b in text_blocks)
        events.append(
            {
                "timestamp": ts,
                "event_name": "hook_userpromptsubmit",
                "body": full_text[:100],
                "attributes": {"tool_input": full_text},
                "service_name": harness,
            }
        )

    for block in result_blocks:
        tool_use_id = str_field(block, "tool_use_id")
        result_content = block.get("content", "")
        if isinstance(result_content, str):
            result_text = result_content
        elif isinstance(result_content, list):
            result_text = "\n".join(
                str_field(c, "text") for c in result_content if isinstance(c, dict) and c.get("type") == "text"
            )
        else:
            result_text = str(result_content)
        if tool_use_id and tool_use_id in tool_use_index:
            existing = events[tool_use_index[tool_use_id]]
            existing["attributes"]["tool_response"] = result_text
        # else: orphan tool_result -- skip


def _handle_assistant(
    line: dict,
    ts: str,
    harness: str,
    events: list[dict],
    tool_use_index: dict[str, int],
) -> None:
    message = dict_field(line, "message")
    content = message.get("content", [])

    usage = dict_field(message, "usage")
    token_attrs: dict = {}
    if usage:
        if usage.get("input_tokens"):
            token_attrs["input_tokens"] = str(usage["input_tokens"])
        if usage.get("output_tokens"):
            token_attrs["output_tokens"] = str(usage["output_tokens"])
        if usage.get("cache_read_input_tokens"):
            token_attrs["cache_read_tokens"] = str(usage["cache_read_input_tokens"])
        if usage.get("cache_creation_input_tokens"):
            token_attrs["cache_creation_tokens"] = str(usage["cache_creation_input_tokens"])
        if message.get("model"):
            token_attrs["model"] = message["model"]
        if message.get("stop_reason"):
            token_attrs["stop_reason"] = message["stop_reason"]

    if not isinstance(content, list):
        return

    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type", "")

        if block_type == "thinking":
            thinking_text = strip_ansi(str_field(block, "thinking"))
            events.append(
                {
                    "timestamp": ts,
                    "event_name": "hook_assistant_thinking",
                    "body": thinking_text[:100],
                    "attributes": {"tool_response": thinking_text},
                    "service_name": harness,
                }
            )

        elif block_type == "text":
            response_text = str_field(block, "text")
            attrs: dict = {"tool_response": response_text}
            if token_attrs:
                attrs.update(token_attrs)
                token_attrs = {}  # consumed -- only on first text block
            events.append(
                {
                    "timestamp": ts,
                    "event_name": "hook_assistant_response",
                    "body": response_text[:100],
                    "attributes": attrs,
                    "service_name": harness,
                }
            )

        elif block_type == "tool_use":
            tool_use_id = str_field(block, "id")
            tool_name = block.get("name", "")
            tool_input = block.get("input", {})
            idx = len(events)
            events.append(
                {
                    "timestamp": ts,
                    "event_name": "hook_posttooluse",
                    "body": tool_name,
                    "attributes": {
                        "tool_name": tool_name,
                        "tool_input": json.dumps(tool_input),
                        "tool_use_id": tool_use_id,
                    },
                    "service_name": harness,
                }
            )
            if tool_use_id:
                tool_use_index[tool_use_id] = idx

    # If token_attrs were not consumed by a text block (tool-only turn),
    # emit a standalone token_usage event so the UI can show token counts.
    if token_attrs:
        events.append(
            {
                "timestamp": ts,
                "event_name": "hook_token_usage",
                "body": "",
                "attributes": token_attrs,
                "service_name": harness,
            }
        )


# ---------------------------------------------------------------------------
# Insight meta records
# ---------------------------------------------------------------------------

# Claude Code tool -> what the insight extractor measures for it.
_META_KINDS = {
    "Write": "write",
    "Edit": "edit",
    "MultiEdit": "edit",
    "Bash": "bash",
    "Task": "subagent",
    "Agent": "subagent",
}
# Text Claude Code injects as a user turn; the user did not type it.
_INJECTED_PREFIXES = ("<system-reminder>", "<task-notification>", "<local-command-", "<command-message>")


def _meta_args(name: str, args: dict) -> dict:
    if name == "Write":
        return {"path": args.get("file_path", ""), "content": args.get("content", "")}
    if name == "Edit":
        edits = [{"oldText": args.get("old_string", ""), "newText": args.get("new_string", "")}]
        return {"path": args.get("file_path", ""), "edits": edits}
    if name == "MultiEdit":
        edits = [
            {"oldText": edit.get("old_string", ""), "newText": edit.get("new_string", "")}
            for edit in list_field(args, "edits")
            if isinstance(edit, dict)
        ]
        return {"path": args.get("file_path", ""), "edits": edits}
    return args


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return " ".join(
        str(block.get("text", "")) for block in content if isinstance(block, dict) and block.get("type") == "text"
    )


def _typed_text(content) -> str:
    if isinstance(content, str):
        texts = [content]
    elif isinstance(content, list):
        texts = [str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text"]
    else:
        texts = []
    return "\n".join(t for t in texts if t.strip() and not t.lstrip().startswith(_INJECTED_PREFIXES))


def meta_records(entries: list[dict]) -> list[dict]:
    """Convert Claude Code transcript lines into insight meta records.

    Claude Code writes one assistant message as several lines (one per content
    block) that share ``message.id`` and each repeat its usage, so lines are
    merged per message before counting.
    """
    records: list[dict] = []
    assistants: dict[str, dict] = {}
    tool_names: dict[str, str] = {}
    cwd_seen = False

    for entry in entries:
        ts = str_field(entry, "timestamp")
        if not cwd_seen and str_field(entry, "cwd"):
            records.append(meta_session(str_field(entry, "cwd"), ts))
            cwd_seen = True
        line_type = str_field(entry, "type")
        message = dict_field(entry, "message")
        content = message.get("content")

        if line_type == "assistant":
            message_id = str_field(message, "id") or str(len(records))
            record = assistants.get(message_id)
            if record is None:
                model = str_field(message, "model")
                record = meta_assistant(ts, "" if model.startswith("<") else model)
                assistants[message_id] = record
                records.append(record)
            if entry.get("isApiErrorMessage"):
                # Claude Code's own notice when the API refused the request ("Not logged in").
                record["message"]["stopReason"] = "error"
            usage = dict_field(message, "usage")
            if usage:
                record["message"]["usage"] = meta_usage(
                    token_count(usage, "input_tokens"),
                    token_count(usage, "output_tokens"),
                    token_count(usage, "cache_read_input_tokens"),
                    token_count(usage, "cache_creation_input_tokens"),
                )
            for block in list_field(message, "content"):
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = str_field(block, "name")
                    tool_names[str_field(block, "id")] = name
                    args = block.get("input") if isinstance(block.get("input"), dict) else {}
                    record["message"]["content"].append(
                        meta_tool_call(name, str_field(block, "id"), _meta_args(name, args), _META_KINDS.get(name, ""))
                    )
            continue

        if line_type == "user":
            results = [
                b for b in list_field(message, "content") if isinstance(b, dict) and b.get("type") == "tool_result"
            ]
            for block in results:
                name = tool_names.get(str_field(block, "tool_use_id"), "")
                records.append(
                    meta_tool_result(ts, name, bool(block.get("is_error")), _result_text(block.get("content")))
                )
            text = "" if results or entry.get("isMeta") else _typed_text(content)
            if text:
                records.append(meta_user(ts, text))
                continue
            if results:
                continue

        if ts:
            records.append(meta_time(ts))
    return records
