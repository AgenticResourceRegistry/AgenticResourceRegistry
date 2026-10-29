# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Shared types for OTLP projection and encoding.

``Span`` and ``SpanEvent`` are frozen dataclasses with tuple fields, so two
projections of the same span compare equal with a plain ``==``.  Content
(prompts, responses, tool payloads) lives in its own ``content`` field and is
only encoded when a destination opts in.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

SPAN_KIND_INTERNAL = 1
SPAN_KIND_CLIENT = 3

Row = Mapping[str, Any]
"""One ``session_events`` row, as returned by ``query_session_rows``."""


@dataclass(frozen=True)
class SessionKey:
    """The primary-key prefix of one session in ``session_events``."""

    project_id: str
    user_id: str
    harness: str
    session_id: str


@dataclass(frozen=True)
class SpanEvent:
    name: str
    time_ns: int
    attributes: tuple[tuple[str, object], ...] = ()
    content: tuple[tuple[str, str], ...] = ()  # exported only with include_content


@dataclass(frozen=True)
class Span:
    trace_id: str  # 32 hex chars
    span_id: str  # 16 hex chars
    parent_span_id: str | None
    name: str
    kind: int
    start_ns: int
    end_ns: int
    attributes: tuple[tuple[str, object], ...]  # always exported
    content: tuple[tuple[str, str], ...] = ()  # exported only with include_content
    events: tuple[SpanEvent, ...] = ()
    error: bool = False
    record_offsets: tuple[int, ...] = ()  # line_offsets this span covers
