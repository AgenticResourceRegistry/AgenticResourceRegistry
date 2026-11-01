# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Attribute harness-neutral skill evidence to verified installed skills.

A fact is attributed only when exactly one resolved, *verified-present*
installed skill in that session's published layer has the fact's install
scope and alias **and** the same SKILL.md location: the location the harness
recorded must hash to the absolute path the verifier fingerprinted. A
same-named skill elsewhere (another user's directory, another agent dir) is
not that install. Several distinct candidates are a collision and nothing is
attributed; none is unmatched. Facts never fall back to a registry-wide name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from services.session_parsers.skill_evidence import SkillEvidence

SKILL_MATCHER_VERSION = 2


@dataclass(frozen=True)
class SkillMatchResult:
    rows: tuple[dict, ...]
    candidate_count: int
    collision_count: int
    unmatched_count: int
    unknown_result_count: int


def match_skill_evidence(
    evidence: Sequence[SkillEvidence],
    candidates_by_hash: Mapping[str, Sequence[dict]],
    hashes_by_offset: Mapping[int, str],
) -> SkillMatchResult:
    rows: list[dict] = []
    collisions = unmatched = unknown = 0
    for fact in evidence:
        if fact.kind == "load" and fact.result_state == "unknown":
            unknown += 1
        candidates = candidates_by_hash.get(hashes_by_offset.get(fact.source_line_offset, ""), ())
        matches = {
            (c.get("component_id"), c.get("component_version_id") or "")
            for c in candidates
            if c.get("identity_status") == "resolved"
            and c.get("verification_status") == "verified"
            and c.get("local_name") == fact.alias
            and c.get("scope") == fact.scope
            and fact.location_sha256
            and c.get("location_sha256") == fact.location_sha256
            and c.get("component_id")
        }
        if len({component for component, _ in matches}) > 1:
            collisions += 1
            continue
        if not matches:
            unmatched += 1
            continue
        component_id = next(iter(matches))[0]
        # The same component pinned at two versions: attribute it, version unknown.
        versions = {version for _, version in matches}
        version_id = versions.pop() if len(versions) == 1 else ""
        rows.append(
            {
                "source_line_offset": fact.source_line_offset,
                "source_block_key": fact.source_block_key,
                "component_type": "skill",
                "component_id": component_id,
                "component_version_id": version_id,
                "evidence_kind": f"skill_{fact.kind}",
                "tool_name": "",
                "tool_use_id": fact.tool_use_id,
                "event_time": fact.event_time,
                "result_state": fact.result_state,
                "attribution_method": "verified_skill_install",
            }
        )
    return SkillMatchResult(tuple(rows), len(evidence), collisions, unmatched, unknown)
