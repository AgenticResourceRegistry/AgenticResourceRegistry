# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 RAWx18 <rawx18.dev@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Shared helpers used by all session JSONL parsers."""

from __future__ import annotations

import json
import re

# ANSI escape sequence pattern (CSI sequences + OSC + simple escapes)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[^\[]")


def load_line(raw_line: str) -> dict | None:
    """Decode one stored JSONL line, or return None when it is not a JSON object.

    ``session_events`` keeps ``raw_line`` verbatim even for lines the ingest path
    rejected, so the read path also sees malformed text and bare JSON scalars.
    Callers fall back to ``basic_event`` when this returns None.
    """
    try:
        parsed = json.loads(raw_line)
    except (json.JSONDecodeError, RecursionError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def str_field(parsed: dict, key: str) -> str:
    """Return a discriminator field as a string, or "" when it is any other type.

    Transcript records are untrusted, so a list or object where a tag is expected
    must never reach a set-membership test or a dict lookup.
    """
    value = parsed.get(key)
    return value if isinstance(value, str) else ""


def dict_field(parsed: dict, key: str) -> dict:
    """Return a nested field as a mapping, or {} when it is any other type.

    Same reasoning as :func:`str_field`: a transcript may carry a scalar where
    the format expects an object, and a bare ``.get(key, {}).get(...)`` chain
    would raise ``AttributeError`` on it.
    """
    value = parsed.get(key)
    return value if isinstance(value, dict) else {}


def token_count(usage: dict, key: str) -> int:
    """Return a non-negative token count, or 0 for malformed transcript values."""
    try:
        return max(int(usage.get(key) or 0), 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def uncached_input_tokens(input_tokens: int, *cache_tokens: int) -> int:
    """Return *input_tokens* with the given cache read/write counts removed.

    Observal stores ``input_tokens`` excluding cache reads and writes, as
    Anthropic reports them. Harnesses that report OpenAI-style usage (Codex,
    Copilot CLI, Goose) count cached tokens inside ``input_tokens`` and also
    report them separately, so they must be subtracted to avoid counting them
    twice. Clamped at zero because the counts come from untrusted transcripts.
    """
    return max(input_tokens - sum(cache_tokens), 0)


def list_field(parsed: dict, key: str) -> list:
    """Return a content field as a list, or [] when it is any other type.

    Iterating a scalar raises, and iterating a string silently yields characters,
    so content arrays are normalised before any parser walks them.
    """
    value = parsed.get(key)
    return value if isinstance(value, list) else []


def strip_ansi(text: str) -> str:
    """Remove ANSI terminal escape codes from text for clean web display."""
    return _ANSI_RE.sub("", text) if "\x1b" in text else text


def strip_cursor_xml_tags(text: str) -> str:
    """Remove Cursor's XML wrapper tags from user prompts for clean display."""
    text = re.sub(r"<timestamp>.*?</timestamp>\s*", "", text, flags=re.DOTALL)
    text = re.sub(r"</?user_query>\s*", "", text)
    text = re.sub(r"</?system_reminder>\s*", "", text)
    text = re.sub(r"</?attached_files>\s*", "", text)
    return text.strip()


_EPOCH_SENTINEL = "1970-01-01"


def pick_timestamp(jsonl_ts: str | None, row_ts: str, ingested_at: str) -> str:
    """Return the best available timestamp string.

    Priority:
    1. JSONL-level timestamp (ISO-8601) converted to ClickHouse format
    2. Row timestamp, if it is not the 1970 epoch sentinel
    3. ingested_at fallback

    ``jsonl_ts`` comes straight from an untrusted transcript, so anything that
    is not a string falls through to the row timestamp.
    """
    if isinstance(jsonl_ts, str) and jsonl_ts:
        # Convert "2025-01-01T12:00:00.000Z" -> "2025-01-01 12:00:00.000"
        ts = jsonl_ts.replace("T", " ").replace("Z", "")
        if ts.endswith("+00:00"):
            ts = ts[:-6]
        if _EPOCH_SENTINEL not in ts:
            return ts
    if _EPOCH_SENTINEL not in row_ts:
        return row_ts
    return ingested_at


def basic_event(row: dict) -> dict:
    """Fallback: build a minimal event from stored columns when raw_line is unusable."""
    return {
        "timestamp": row.get("timestamp", ""),
        "event_name": row.get("event_type", ""),
        "body": row.get("content_preview", ""),
        "attributes": {
            "tool_name": row.get("tool_name") or "",
            "tool_id": row.get("tool_id") or "",
            "uuid": row.get("uuid") or "",
            "parent_uuid": row.get("parent_uuid") or "",
            "content_length": str(row.get("content_length", 0)),
            **({"credits": str(row["credits"])} if row.get("credits") else {}),
        },
        "service_name": row.get("harness", ""),
    }


# ---------------------------------------------------------------------------
# Insight meta records
# ---------------------------------------------------------------------------
# ``services.insights.session_meta_extractor`` reads Pi-style records. Each
# harness's ``meta_records`` converts its own lines into these shapes, so the
# extractor never branches on transcript format.


def meta_session(cwd: str, ts: str = "") -> dict:
    """Session header carrying the project directory."""
    return {"type": "session", "cwd": cwd, "timestamp": ts}


def meta_time(ts: str) -> dict:
    """A line with no message content that still bounds the session's time range."""
    return {"type": "time", "timestamp": ts}


def meta_user(ts: str, text: str) -> dict:
    """A prompt the user typed."""
    return {
        "type": "message",
        "timestamp": ts,
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


def meta_assistant(ts: str, model: str = "") -> dict:
    """One model response; harness normalisers append tool calls and set ``usage``."""
    return {"type": "message", "timestamp": ts, "message": {"role": "assistant", "model": model, "content": []}}


def meta_tool_call(name: str, tool_id: str, args: dict, kind: str = "") -> dict:
    """A tool call block for :func:`meta_assistant` content.

    ``kind`` names what the extractor measures (``write``, ``edit``, ``bash``,
    ``subagent``) when the harness's own tool name differs; ``args`` then use the
    Pi argument names (``path``, ``content``, ``edits`` of ``oldText``/``newText``,
    ``command``).
    """
    block = {"type": "toolCall", "name": name, "id": tool_id, "arguments": args}
    if kind:
        block["kind"] = kind
    return block


def meta_usage(input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0) -> dict:
    """Usage for :func:`meta_assistant`, with ``input`` excluding cache reads and writes."""
    return {"input": input_tokens, "output": output_tokens, "cacheRead": cache_read, "cacheWrite": cache_write}


def meta_tool_result(ts: str, tool_name: str, is_error: bool, text: str) -> dict:
    """The result of one tool call."""
    return {
        "type": "message",
        "timestamp": ts,
        "message": {
            "role": "toolResult",
            "toolName": tool_name,
            "isError": is_error,
            "content": [{"type": "text", "text": text}],
        },
    }
