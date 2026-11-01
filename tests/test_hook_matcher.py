# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Hook runs attach only to the verified binding, and each verified hook says whether it could run."""

from __future__ import annotations

from services.component_activity.hook_matcher import match_hook_evidence
from services.session_parsers.hook_evidence import (
    HookEvidence,
    HookEvidenceExtraction,
    HookSession,
    hook_binding_sha256,
)

HASH = "v2_" + "e" * 60
BINDING = hook_binding_sha256("PostToolUse", ".claude/hooks/probe-fail.sh")


def _hook(**overrides) -> dict:
    return {
        "component_id": "hook-1",
        "component_version_id": "",
        "identity_status": "resolved",
        "verification_status": "verified",
        "location_sha256": BINDING,
        "binding_agent": "",
    } | overrides


def _run(binding: str = BINDING, kind: str = "failed") -> HookEvidence:
    return HookEvidence(kind, binding, 5, "hook-run:0", None)


def _match(facts, candidates, *, headless=False, agents=(), runs_headless=False):
    session = HookSession(headless, frozenset(agents), runs_headless)
    extraction = HookEvidenceExtraction("supported", tuple(facts), session)
    return match_hook_evidence(extraction, {HASH: candidates}, {0: HASH, 5: HASH})


def _kinds(result) -> list[str]:
    return sorted(row["evidence_kind"] for row in result.rows)


def test_a_run_matching_the_verified_binding_is_attributed_with_an_eligible_context():
    result = _match([_run()], [_hook()])
    assert _kinds(result) == ["hook_context_eligible", "hook_failed"]
    assert result.attributed_count == 1, "context rows are not attributed runs"
    context = next(r for r in result.rows if r["evidence_kind"].startswith("hook_context"))
    assert (context["source_line_offset"], context["source_block_key"]) == (0, "hook-context:hook-1")


def test_other_bindings_unverified_installs_and_duplicates_are_never_attributed():
    other = hook_binding_sha256("PreToolUse", ".claude/hooks/probe-fail.sh")
    assert _match([_run(other)], [_hook()]).unmatched_count == 1
    unverified = _match([_run()], [_hook(verification_status="unverified")])
    assert (unverified.rows, unverified.unmatched_count) == ((), 1)
    twin = _match([_run()], [_hook(), _hook(component_id="hook-2")])
    assert twin.collision_count == 1 and not any(r["evidence_kind"] == "hook_failed" for r in twin.rows)


def test_agent_hooks_could_run_only_in_interactive_sessions_of_their_agent():
    agent_hook = _hook(binding_agent="probe-agent")
    assert _kinds(_match([], [agent_hook], headless=False, agents=["probe-agent"])) == ["hook_context_eligible"]
    assert _kinds(_match([], [agent_hook], headless=True, agents=["probe-agent"])) == ["hook_context_headless"]
    assert _kinds(_match([], [agent_hook], headless=False, agents=[])) == ["hook_context_agent_inactive"]
    assert _kinds(_match([], [agent_hook], headless=None, agents=["probe-agent"])) == ["hook_context_mode_unknown"]
    # A harness whose extractor declares agent hooks run headless is not penalised.
    assert _kinds(_match([], [agent_hook], headless=True, agents=["probe-agent"], runs_headless=True)) == [
        "hook_context_eligible"
    ]
    # Settings hooks run in every mode.
    assert _kinds(_match([], [_hook()], headless=True)) == ["hook_context_eligible"]


def test_a_recorded_run_proves_the_hook_could_run():
    result = _match([_run()], [_hook(binding_agent="probe-agent")], headless=True, agents=["probe-agent"])
    assert _kinds(result) == ["hook_context_eligible", "hook_failed"]
