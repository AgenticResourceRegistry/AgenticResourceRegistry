# SPDX-FileCopyrightText: 2026 RAWx18 <rawx18.dev@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Shared oracle for the session-transcript fuzz targets.

Both session targets drive the same trust boundary and differ only in how
they produce input, so the pipeline replay and its invariants live here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import _paths
import orjson

_paths.add_source_roots()

if TYPE_CHECKING:
    from collections.abc import Iterable

from observal_shared.harness_registry import HARNESS_REGISTRY  # noqa: E402
from services.session_ingest import _ch_string, _ch_timestamp, _extract_usage_tokens, _extract_uuid  # noqa: E402
from services.session_parsers import parse_raw_events  # noqa: E402
from services.session_parsers.ingest_classify import extract_timestamp, get_classifier  # noqa: E402

# Sorted so a given input always selects the same harness across runs.
HARNESSES = tuple(sorted(HARNESS_REGISTRY))

# Every event handed to the trace viewer by api/routes/sessions.py carries these.
_FRONTEND_EVENT_KEYS = frozenset({"timestamp", "event_name", "body", "attributes", "service_name"})

# Fixed row metadata so all input entropy lands in the transcript line itself
# rather than in columns the parsers only echo back.
_ROW_TIMESTAMP = "2026-01-01 00:00:00.000"
_ROW_INGESTED_AT = "2026-01-01 00:00:01.000"


def _stored_row(harness: str, raw_line: str, event_type: str, rendered: bool) -> dict:
    """Build the subset of a ``session_events`` row that the read path consumes."""
    return {
        "harness": harness,
        "raw_line": raw_line,
        "event_type": event_type,
        "is_source_record": 1,
        "rendered": int(rendered),
        "timestamp": _ROW_TIMESTAMP,
        "ingested_at": _ROW_INGESTED_AT,
        "content_preview": "",
        "content_length": len(raw_line),
        "tool_name": None,
        "tool_id": None,
        "uuid": None,
        "parent_uuid": None,
        "credits": None,
    }


def replay(harness: str, raw_lines: Iterable[str]) -> None:
    """Run one transcript through ingest classification and the read-path parser.

    Mirrors the per-line loop in ``services.session_ingest`` and then feeds the
    stored rows to ``parse_raw_events``, asserting the contract both halves owe
    their callers. Returns early when the input would be rejected before it
    could reach storage.
    """
    classify_fn, preview_fn, tool_info_fn = get_classifier(harness)
    rows: list[dict] = []

    for raw_line in raw_lines:
        rendered = False
        try:
            try:
                candidate = orjson.loads(raw_line)
            except RecursionError:
                return  # orjson refuses inputs beyond its nesting limit
            if not isinstance(candidate, dict):
                raise ValueError("session record must be a JSON object")
            event_type = classify_fn(candidate)
            rendered = event_type is not None
            if event_type is None:
                event_type = "_ignored"
            ts = extract_timestamp(harness, candidate)
            if ts is not None:
                _ch_timestamp(ts)
            if rendered:
                _ch_string(preview_fn(candidate, event_type), "content_preview")
                tool_name, tool_id = tool_info_fn(candidate)
                _ch_string(tool_name, "tool_name", nullable=True)
                _ch_string(tool_id, "tool_id", nullable=True)
            uuid, parent_uuid = _extract_uuid(candidate, harness)
            _ch_string(uuid, "uuid", nullable=True)
            _ch_string(parent_uuid, "parent_uuid", nullable=True)
            _ch_string(event_type, "event_type")
            _extract_usage_tokens(candidate, harness)
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError):
            # Match ingest's per-source-record boundary. Other exceptions (and
            # failures in row storage/read parsing) are infrastructure findings.
            event_type = "_parse_error"
            rendered = False

        # Lines that failed to decode are stored too, so the read path sees them.
        rows.append(_stored_row(harness, raw_line, event_type, rendered))

    for event in parse_raw_events(rows):
        assert event.keys() >= _FRONTEND_EVENT_KEYS, f"{harness} event is missing {_FRONTEND_EVENT_KEYS - event.keys()}"
        assert isinstance(event["attributes"], dict), f"{harness} event attributes must be a mapping"
