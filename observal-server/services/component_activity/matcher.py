# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""Exact, present-candidate-only matching for fixture-verified MCP call identities.

Two identity forms exist. Claude Code encodes the installed alias in the tool
name (``mcp__<alias>__<tool>``). Pi's MCP adapter reports the configured server
name it dispatched to, carried as ``SourceInvocation.mcp_server``; that name is
compared for exact equality with the installed alias, never by prefix.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from services.session_parsers.invocation_types import SourceInvocation


@dataclass(frozen=True)
class MatchResult:
    rows: tuple[dict, ...]
    candidate_count: int
    collision_count: int
    unmatched_count: int
    unknown_result_count: int


def match_invocations(
    invocations: Sequence[SourceInvocation],
    candidates_by_hash: Mapping[str, Sequence[dict]],
    hashes_by_offset: Mapping[int, str],
    *,
    malformed_source_records: int = 0,
) -> MatchResult:
    """Keep only exactly one verified, resolved alias in the source line's layer.

    *All* published MCP occurrences participate in collision detection: another
    unverified occurrence of the same alias cannot make one verified occurrence
    unambiguous. No registry-wide name lookup or prefix-only alias match exists,
    and a tool remainder containing ``__`` is an unresolvable split (collision).
    """
    rows: list[dict] = []
    collisions = 0
    unmatched = malformed_source_records
    unknown = 0
    candidate_count = malformed_source_records
    for call in invocations:
        if not call.is_mcp_candidate:
            continue
        candidate_count += 1
        unknown += int(call.result_state == "unknown")
        layer_hash = hashes_by_offset.get(call.source_line_offset, "")
        candidates = candidates_by_hash.get(layer_hash, ())
        if call.mcp_server is not None:
            # Harness-reported server identity: exact alias equality only. An
            # empty server is an MCP call whose identity could not be established.
            matches = [c for c in candidates if call.mcp_server and c.get("local_name") == call.mcp_server]
            if len(matches) > 1:
                collisions += 1
                continue
        else:
            matches = [
                candidate
                for candidate in candidates
                if candidate.get("local_name")
                and call.tool_name.startswith(f"mcp__{candidate['local_name']}__")
                and len(call.tool_name) > len(f"mcp__{candidate['local_name']}__")
            ]
            # ``mcp__<server>__<tool>`` cannot be split uniquely when the remainder
            # itself contains ``__``: an unregistered server ``a__b`` would look
            # like registry alias ``a`` with tool ``b__x``. Decline, never guess.
            if len(matches) > 1 or any("__" in call.tool_name[len(f"mcp__{c['local_name']}__") :] for c in matches):
                collisions += 1
                continue
        if (
            len(matches) != 1
            or call.event_time is None
            or not datetime(1971, 1, 1, tzinfo=UTC) <= call.event_time < datetime(2100, 1, 1, tzinfo=UTC)
        ):
            unmatched += 1
            continue
        candidate = matches[0]
        if (
            candidate.get("identity_status") != "resolved"
            or candidate.get("verification_status") != "verified"
            or not candidate.get("component_id")
            or not candidate.get("component_version_id")
        ):
            unmatched += 1
            continue
        rows.append(
            {
                "source_line_offset": call.source_line_offset,
                "source_block_key": call.source_block_key,
                "layer_hash": layer_hash,
                "component_type": "mcp",
                "component_id": candidate["component_id"],
                "component_version_id": candidate["component_version_id"],
                "tool_name": call.tool_name,
                "tool_use_id": call.tool_use_id,
                "event_time": call.event_time,
                "result_state": call.result_state,
                "attribution_method": "verified_alias" if call.mcp_server is None else "verified_server",
            }
        )
    # Malformed source records cannot be enumerated as calls, but must not be
    # silently represented as zero possible activity in the publication totals.
    return MatchResult(tuple(rows), candidate_count, collisions, unmatched, unknown)
