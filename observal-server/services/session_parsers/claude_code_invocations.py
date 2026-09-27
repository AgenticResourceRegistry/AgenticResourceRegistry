# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""Claude Code activity facts from canonical JSONL source records only.

No transcript content, invocation arguments, or result output leave this module.
Results require one unambiguous ``tool_use_id`` link and an explicit boolean
``is_error``; an absent flag does not establish success.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

from .base import list_field, load_line, str_field
from .invocation_types import InvocationExtraction, SourceInvocation

_TOOL_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_MAX_TOOL_NAME = 256


def _tool_id(value: object) -> str:
    return value if isinstance(value, str) and _TOOL_ID.fullmatch(value) else ""


def _event_time(record: dict, row: Mapping[str, object]) -> datetime | None:
    for value in (record.get("timestamp"), row.get("timestamp")):
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
        else:
            continue
        if parsed.year == 1970:
            continue
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    return None


class ClaudeCodeInvocationExtractor:
    def extract(self, rows: Sequence[Mapping[str, object]]) -> InvocationExtraction:
        calls: list[tuple[int, int, str, str, datetime | None]] = []
        results: dict[str, list[Literal["unknown", "success", "error"]]] = {}
        malformed = 0
        for row in rows:
            if row.get("is_source_record") != 1:
                continue
            offset = row.get("line_offset")
            raw_line = row.get("raw_line")
            if type(offset) is not int or offset < 0 or not isinstance(raw_line, str):
                malformed += 1
                continue
            record = load_line(raw_line)
            if record is None:
                malformed += 1
                continue
            record_type = str_field(record, "type")
            if record_type not in ("assistant", "user"):
                continue
            message = record.get("message")
            if not isinstance(message, dict):
                malformed += 1
                continue
            content = list_field(message, "content")
            if record_type == "assistant":
                if not isinstance(message.get("content"), list):
                    malformed += 1
                    continue
                time = _event_time(record, row)
                for index, block in enumerate(content):
                    if not isinstance(block, dict) or str_field(block, "type") != "tool_use":
                        continue
                    name = str_field(block, "name")
                    name = name if len(name) <= _MAX_TOOL_NAME else ""
                    calls.append((offset, index, name, _tool_id(block.get("id")), time))
            else:
                for block in content:
                    if not isinstance(block, dict) or str_field(block, "type") != "tool_result":
                        continue
                    linked_id = _tool_id(block.get("tool_use_id"))
                    if not linked_id:
                        continue
                    error = block.get("is_error")
                    state: Literal["unknown", "success", "error"] = "unknown"
                    if type(error) is bool:
                        state = "error" if error else "success"
                    results.setdefault(linked_id, []).append(state)

        # Call IDs reused across records cannot establish a unique result link.
        call_counts = Counter(call_id for _, _, _, call_id, _ in calls if call_id)
        line_counts = Counter((offset, call_id) for offset, _, _, call_id, _ in calls if call_id)
        invocations = []
        for offset, index, name, call_id, time in calls:
            unique_in_line = bool(call_id and line_counts[offset, call_id] == 1)
            block_key = f"id:{call_id}" if unique_in_line else f"index:{index}"
            linked = results.get(call_id, ()) if call_id and call_counts[call_id] == 1 else ()
            state: Literal["unknown", "success", "error"] = linked[0] if len(linked) == 1 else "unknown"
            invocations.append(SourceInvocation(offset, block_key, name, call_id, time, state))
        return InvocationExtraction(
            status="supported", invocations=tuple(invocations), malformed_source_records=malformed
        )
