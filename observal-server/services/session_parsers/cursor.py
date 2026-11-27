# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 RAWx18 <rawx18.dev@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Cursor JSONL session parser.

Cursor uses the same content structure as Claude Code (message.content with
typed blocks) but uses ``role`` instead of ``type`` at the top level:
  { "role": "user"|"assistant", "message": {"content": [...]} }

This parser normalizes ``role`` → ``type`` in the parsed line and delegates
to the Claude Code parser handlers.
"""

from __future__ import annotations

from .base import basic_event, dict_field, load_line, pick_timestamp, str_field, strip_cursor_xml_tags
from .claude_code import _handle_assistant, _handle_user


def _clean_user_content(line: dict) -> dict:
    """Strip Cursor XML tags from text blocks in user messages."""
    content = dict_field(line, "message").get("content", [])
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                block["text"] = strip_cursor_xml_tags(str_field(block, "text"))
    return line


def parse_rows(rows: list[dict]) -> list[dict]:
    """Parse raw_line Cursor JSONL rows into normalised frontend events."""
    events: list[dict] = []
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

        role = line.get("role", "")
        ts = pick_timestamp(line.get("timestamp"), row_ts, ingested_at)

        if role == "user":
            _clean_user_content(line)
            _handle_user(line, ts, harness, events, tool_use_index)
        elif role == "assistant":
            _handle_assistant(line, ts, harness, events, tool_use_index, row)
        else:
            events.append(basic_event(row))

    return events
