# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Attribute hook runs to verified installed hooks, and record whether each could run.

A recorded run is attributed only when exactly one resolved, verified-present
hook in the session's published layer has the run's binding digest (the
exact event and command the harness recorded). Several distinct candidates are
a collision; none is unmatched.

Because a hook that succeeds silently may leave no record, "no recorded run"
is never evidence of no run. To keep the denominator honest, each verified
hook in the session also gets one context row saying whether it could run:

* ``eligible``: a standalone settings-file hook; an agent hook in the agent
  file whose agent was active in an interactive session; or an agent hook in
  settings.json behind the agent gate whose agent was active in any mode. Any
  attributed run also makes a hook eligible.
* ``agent_inactive``: its agent did not run in this session, so it could not fire.
* ``headless``: its agent ran headless, and the harness's extractor declares
  that agent-file hooks do not run headless (Claude Code's recorded behaviour).
* ``mode_unknown``: the session did not record whether it was headless.
* ``agent_unknown``: a gated hook in a subagent's own transcript, which records
  that a subagent ran but not which agent it was.

Only ``eligible`` sessions enter the denominator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from services.session_parsers.hook_evidence import HookEvidenceExtraction, HookSession

# 2: agent hooks placed in settings.json behind the agent gate (binding_placement
# 'gated_settings') can run in headless sessions, and are 'agent_unknown' in a
# subagent's own transcript. Frontmatter and standalone hooks are unchanged, and
# no gated placement existed before, so earlier publications stay correct.
HOOK_MATCHER_VERSION = 2
_RESULT = {"ran_with_output": "success", "failed": "error", "blocked": "error"}


@dataclass(frozen=True)
class HookMatchResult:
    rows: tuple[dict, ...]
    candidate_count: int
    collision_count: int
    unmatched_count: int
    unknown_result_count: int = 0
    # Attributed runs only; context rows are not facts.
    attributed_count: int = 0


def _verified(candidates: Sequence[dict]) -> list[dict]:
    return [
        c
        for c in candidates
        if c.get("identity_status") == "resolved"
        and c.get("verification_status") == "verified"
        and c.get("component_id")
    ]


def _state(candidate: dict, session: HookSession) -> str:
    agent = candidate.get("binding_agent") or ""
    if not agent:
        return "eligible"
    if candidate.get("binding_placement") == "gated_settings":
        # The gate runs it whenever its agent is active, interactive or headless.
        if agent in session.agents:
            return "eligible"
        return "agent_unknown" if session.subagent else "agent_inactive"
    if agent not in session.agents:
        return "agent_inactive"
    if session.agent_hooks_run_headless:
        return "eligible"
    if session.headless is None:
        return "mode_unknown"
    return "headless" if session.headless else "eligible"


def match_hook_evidence(
    extraction: HookEvidenceExtraction,
    candidates_by_hash: Mapping[str, Sequence[dict]],
    hashes_by_offset: Mapping[int, str],
) -> HookMatchResult:
    rows: list[dict] = []
    collisions = unmatched = 0
    ran: set[tuple[str, str]] = set()  # (layer hash, component id) with an attributed run
    for fact in extraction.evidence:
        layer_hash = hashes_by_offset.get(fact.source_line_offset, "")
        matches = {
            (c["component_id"], c.get("component_version_id") or "")
            for c in _verified(candidates_by_hash.get(layer_hash, ()))
            if c.get("location_sha256") and c.get("location_sha256") == fact.binding_sha256
        }
        if len({component for component, _ in matches}) > 1:
            collisions += 1
            continue
        if not matches:
            unmatched += 1
            continue
        component_id = next(iter(matches))[0]
        versions = {version for _, version in matches}
        ran.add((layer_hash, component_id))
        rows.append(
            {
                "source_line_offset": fact.source_line_offset,
                "source_block_key": fact.source_block_key,
                "component_type": "hook",
                "component_id": component_id,
                "component_version_id": versions.pop() if len(versions) == 1 else "",
                "evidence_kind": f"hook_{fact.kind}",
                "tool_name": "",
                "tool_use_id": fact.tool_use_id,
                "event_time": fact.event_time,
                "result_state": _RESULT[fact.kind],
                "attribution_method": "verified_hook_install",
            }
        )
    first_offsets: dict[str, int] = {}
    for offset, layer_hash in sorted(hashes_by_offset.items()):
        first_offsets.setdefault(layer_hash, offset)
    for layer_hash, offset in sorted(first_offsets.items(), key=lambda item: item[1]):
        by_component: dict[str, list[dict]] = {}
        for candidate in _verified(candidates_by_hash.get(layer_hash, ())):
            by_component.setdefault(candidate["component_id"], []).append(candidate)
        for component_id, group in sorted(by_component.items()):
            states = {_state(candidate, extraction.session) for candidate in group}
            state = "eligible" if (layer_hash, component_id) in ran or "eligible" in states else sorted(states)[0]
            versions = {candidate.get("component_version_id") or "" for candidate in group}
            rows.append(
                {
                    "source_line_offset": offset,
                    "source_block_key": f"hook-context:{component_id}",
                    "component_type": "hook",
                    "component_id": component_id,
                    "component_version_id": versions.pop() if len(versions) == 1 else "",
                    "evidence_kind": f"hook_context_{state}",
                    "tool_name": "",
                    "tool_use_id": "",
                    "event_time": None,
                    "result_state": "unknown",
                    "attribution_method": "verified_hook_install",
                }
            )
    attributed = sum(not row["evidence_kind"].startswith("hook_context_") for row in rows)
    return HookMatchResult(tuple(rows), len(extraction.evidence), collisions, unmatched, 0, attributed)
