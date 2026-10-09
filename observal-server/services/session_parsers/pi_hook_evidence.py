# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Pi registry-hook evidence from the Observal extension's run receipts only.

Pi has no native command hooks. Registry hooks run inside the Observal Pi
extension (``packages/pi-extension/extensions/observal.ts``), which appends a
receipt after each run with ``pi.appendEntry()``. Recorded on Pi 1.0.4
(``tests/fixtures/component_insights/pi/hook_*``), a receipt is a top-level
custom record placed between the assistant ``toolCall`` and its ``toolResult``::

    {"type": "custom", "customType": "observal-hook-run", "data": {
        "v": 1, "event": "tool_call", "tool_call_id": "<Pi tool-call id>",
        "binding": "<sha256(event NUL command)>", "outcome": "blocked", "exit_code": 2}}

* **ran_with_output**: ``outcome: ran_with_output``, exit code 0.
* **failed**: ``outcome: failed``: a non-zero exit (or ``null`` for a timeout or a
  command that could not start), including exit 2 on ``tool_result``.
* **blocked**: ``outcome: blocked``, ``event: tool_call``, exit code 2. Pi then
  records the blocked call's ``toolResult`` with ``isError: true`` and the hook's
  stderr as its text, which is not read here.
* **ran_silently**: ``outcome: ran``, exit code 0, no output. The extension
  records every run, so Pi run counts are not a lower bound
  (``records_silent_success = True``).

Trust boundary: model output and user text cannot create a top-level custom
record, so typed or pasted text never becomes evidence. Any other extension in
the same Pi process can, however, call ``pi.appendEntry()`` and write an
identical receipt; this is not stronger attestation than the extension API
gives. Each receipt must also name a tool call recorded *earlier* in the same
transcript (nested calls by their ``<parent id>/<n>`` id), and a ``blocked``
receipt must not contradict a successful ``toolResult``. Anything else is
malformed, never evidence. Duplicate receipts for one (event, call, binding)
count once.

A hook that runs from the active ``observal-hooks.json`` runs in every Pi mode
the extension loads in (print mode was recorded), and Pi hooks are not scoped
to an agent inside the session: the verified layer itself proves which profile
was active. So ``agent_hooks_run_headless`` is True and ``agents`` is empty.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .base import load_line, str_field
from .hook_evidence import HookEvidence, HookEvidenceExtraction, HookSession

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

RECEIPT_TYPE = "observal-hook-run"
_EVENTS = frozenset({"tool_call", "tool_result"})
_KINDS = {"ran": "ran_silently", "ran_with_output": "ran_with_output", "failed": "failed", "blocked": "blocked"}
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_TOOL_ID = re.compile(r"[\x21-\x7e]{1,256}\Z")
_NESTED = re.compile(r"(?P<root>[^/]+)(?:/[0-9]{1,6})+\Z")
_BLOCK_EXIT = 2


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


def _consistent(event: str, outcome: str, code: object) -> bool:
    if code is not None and type(code) is not int:
        return False
    if outcome in ("ran", "ran_with_output"):
        return code == 0
    if outcome == "blocked":
        return event == "tool_call" and code == _BLOCK_EXIT
    return outcome == "failed" and code != 0


def _root_call(tool_call_id: str) -> str:
    nested = _NESTED.fullmatch(tool_call_id)
    return nested["root"] if nested else tool_call_id


class PiHookEvidenceExtractor:
    observed_kinds: frozenset = frozenset({"ran_with_output", "ran_silently", "failed", "blocked"})
    # The extension writes a receipt for every run, silent successes (``ran``) included.
    records_silent_success = True

    def extract(self, rows: Sequence[Mapping[str, object]]) -> HookEvidenceExtraction:
        records: list[tuple[int, dict, Mapping[str, object]]] = []
        malformed = 0
        for row in rows:
            if row.get("is_source_record") != 1:
                continue
            offset, raw_line = row.get("line_offset"), row.get("raw_line")
            if type(offset) is not int or offset < 0 or not isinstance(raw_line, str) or row.get("raw_line_truncated"):
                malformed += 1
                continue
            record = load_line(raw_line)
            if record is None:
                malformed += 1
                continue
            records.append((offset, record, row))
        records.sort(key=lambda item: item[0])

        # Harness-written structure: where each tool call was made, and its result's error flag.
        calls: dict[str, int] = {}
        results: dict[str, bool] = {}
        for offset, record, _row in records:
            message = record.get("message")
            if str_field(record, "type") != "message" or not isinstance(message, dict):
                continue
            role = message.get("role")
            if role == "assistant" and isinstance(message.get("content"), list):
                for block in message["content"]:
                    if isinstance(block, dict) and block.get("type") == "toolCall" and isinstance(block.get("id"), str):
                        calls.setdefault(block["id"], offset)
            elif (
                role == "toolResult"
                and isinstance(message.get("toolCallId"), str)
                and isinstance(message.get("isError"), bool)
            ):
                results.setdefault(message["toolCallId"], message["isError"])

        evidence: list[HookEvidence] = []
        seen: set[tuple[str, str, str]] = set()
        for offset, record, row in records:
            if str_field(record, "type") != "custom" or record.get("customType") != RECEIPT_TYPE:
                continue
            data = record.get("data")
            if not isinstance(data, dict) or data.get("v") != 1:
                malformed += 1
                continue
            event, call_id, binding = data.get("event"), data.get("tool_call_id"), data.get("binding")
            outcome, code = data.get("outcome"), data.get("exit_code")
            if (
                event not in _EVENTS
                or not isinstance(call_id, str)
                or not _TOOL_ID.fullmatch(call_id)
                or not isinstance(binding, str)
                or not _DIGEST.fullmatch(binding)
                or not isinstance(outcome, str)
                or not _consistent(event, outcome, code)
            ):
                malformed += 1
                continue
            made_at = calls.get(_root_call(call_id))
            if made_at is None or made_at > offset:
                malformed += 1  # not a call this transcript recorded before the receipt
                continue
            if outcome == "blocked" and results.get(call_id) is False:
                malformed += 1  # Pi recorded the call as succeeding
                continue
            key = (event, call_id, binding)
            if key in seen:
                continue
            seen.add(key)
            kind = _KINDS[outcome]
            evidence.append(HookEvidence(kind, binding, offset, "hook-run:0", _event_time(record, row), call_id))  # type: ignore[arg-type]

        return HookEvidenceExtraction(
            status="supported",
            evidence=tuple(evidence),
            session=HookSession(headless=None, agents=frozenset(), agent_hooks_run_headless=True),
            malformed_source_records=malformed,
        )
