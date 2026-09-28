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
    # Harness-reported configured MCP server name. ``None`` means the harness
    # encodes MCP identity in ``tool_name`` (Claude Code's ``mcp__<alias>__``);
    # an empty string is an MCP call whose server could not be established.
    mcp_server: str | None = None

    @property
    def is_mcp_candidate(self) -> bool:
        return self.mcp_server is not None or self.tool_name.startswith("mcp__")


@dataclass(frozen=True)
class InvocationExtraction:
    status: Literal["supported", "unsupported"]
    invocations: tuple[SourceInvocation, ...]
    malformed_source_records: int = 0


class InvocationExtractor(Protocol):
    def extract(self, rows: Sequence[Mapping[str, object]]) -> InvocationExtraction: ...
