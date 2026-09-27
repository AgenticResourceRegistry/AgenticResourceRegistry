# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""Safe, harness-independent source invocation facts and extractor contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime


@dataclass(frozen=True)
class SourceInvocation:
    source_line_offset: int
    source_block_key: str
    tool_name: str
    tool_use_id: str
    event_time: datetime | None
    result_state: Literal["unknown", "success", "error"]


@dataclass(frozen=True)
class InvocationExtraction:
    status: Literal["supported", "unsupported"]
    invocations: tuple[SourceInvocation, ...]
    malformed_source_records: int = 0


class InvocationExtractor(Protocol):
    def extract(self, rows: Sequence[Mapping[str, object]]) -> InvocationExtraction: ...
