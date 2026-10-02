# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""Component observability API schemas (presence, activity and coverage).

``ActivityCoverage`` is the shared coverage contract reused by the
observability API and (Phase 4) component Insights reports.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AttributionState = Literal["observed", "no_observed_calls", "attribution_not_possible"]
SessionProjectionState = Literal[
    "complete",
    "pending",
    "failed",
    "stale",
    "unsupported",
    "source_missing",
    "source_unavailable",
    "source_incomplete",
    "source_too_large",
]

TIME_BASIS = "session last_event_time in [start, end); all attributable calls within selected sessions"
LAYER_STABILITY_LIMITATION = (
    "sender_cached_hash_not_proven_stable: a session carries the first layer hash its sender computed; "
    "in-session configuration changes that did not change that hash cannot be detected"
)
FINAL_PUSH_LIMITATION = (
    "historical_final_push_unproven: backfilled sessions were projected as of their recorded source "
    "revision; a still-incomplete source prefix is not proof of no later use"
)


class PresenceCoverage(BaseModel):
    """Presence stage: which eligible sessions can establish component membership."""

    eligible_sessions: int = 0
    missing_hash_sessions: int = 0
    legacy_hash_sessions: int = 0
    v2_sessions: int = 0
    snapshot_missing_sessions: int = 0
    mapping_pending_sessions: int = 0
    mapping_failed_sessions: int = 0
    stale_extractor_sessions: int = 0
    identity_conflict_sessions: int = 0
    mapping_complete_sessions: int = 0
    # Layer-wide: any unresolved or ambiguous occurrence in the session's layer.
    unresolved_identity_sessions: int = 0
    ambiguous_identity_sessions: int = 0
    # For the selected listing (and version) only.
    unverified_presence_sessions: int = 0
    drifted_presence_sessions: int = 0
    verified_presence_sessions: int = 0
    present_sessions: int = 0
    present_users: int = 0
    current_extractor_version: int = 0


class ProjectionCoverage(BaseModel):
    """Activity stage for present sessions only (never the whole project)."""

    supported_present_sessions: int = 0
    unsupported_present_sessions: int = 0
    projection_complete_sessions: int = 0
    projection_pending_sessions: int = 0
    projection_failed_sessions: int = 0
    projection_stale_sessions: int = 0
    # Source-record availability (not ordinary backlog): no canonical source rows,
    # expired/truncated raw lines, a gap in line offsets, or over the projector budget.
    source_missing_sessions: int = 0
    source_unavailable_sessions: int = 0
    source_incomplete_sessions: int = 0
    source_too_large_sessions: int = 0
    publication_version: int = 0


class CallCoverage(BaseModel):
    """Session-level MCP call counts from complete current publications.

    These counters cover *all* MCP calls in the processed present sessions (the
    candidate universe the matcher saw), not only calls to this component.
    """

    candidate_calls: int = 0
    attributed_calls: int = 0
    collision_calls: int = 0
    unmatched_calls: int = 0
    unknown_result_calls: int = 0


class ActivityCoverage(BaseModel):
    presence: PresenceCoverage
    projection: ProjectionCoverage
    calls: CallCoverage
    usage_rate_denominator_sessions: int = Field(
        0, description="Present sessions on a supported harness with a complete current publication"
    )
    observed_sessions: int = 0
    usage_rate: float | None = None
    attribution_state: AttributionState
    reasons: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=lambda: [LAYER_STABILITY_LIMITATION, FINAL_PUSH_LIMITATION])


class ResultStateCounts(BaseModel):
    success: int = 0
    error: int = 0
    unknown: int = 0


class ActivationActions(BaseModel):
    """Session-linked install/load actions, scoped to the selected component/version. Never usage."""

    context_sessions: int = 0
    next_session_sessions: int = 0
    scope: Literal["component", "component_version"] = "component"
    note: str = (
        "activation/configuration actions only; never counted as observed use. An install counts for the "
        "earliest delivered session that started after it; best effort, not a complete history"
    )


class ComponentRef(BaseModel):
    type: str
    id: str
    qualified_name: str
    component_version_id: str | None = None


class ActivitySummaryResponse(BaseModel):
    component: ComponentRef
    period_days: int
    period_start: str
    period_end: str
    time_basis: str = TIME_BASIS
    present_sessions: int
    present_users: int
    observed_sessions: int
    observed_calls: int
    result_states: ResultStateCounts
    harness_distribution: dict[str, int]
    version_distribution: dict[str, int] = Field(default_factory=dict)
    activation_actions: ActivationActions
    coverage: ActivityCoverage


class SourceReference(BaseModel):
    source_line_offset: int
    source_block_key: str
    result_state: str


SourceAvailabilityState = Literal[
    "available", "source_missing", "source_unavailable", "source_incomplete", "source_too_large", "unsupported"
]


class ActivitySession(BaseModel):
    user_id: str
    harness: str
    session_id: str
    last_event_time: str
    projection_state: SessionProjectionState
    source_state: SourceAvailabilityState
    observed_calls: int
    result_states: ResultStateCounts
    source_references: list[SourceReference]
    source_references_truncated: bool


class ActivitySessionsResponse(BaseModel):
    component: ComponentRef
    period_days: int
    time_basis: str = TIME_BASIS
    sessions: list[ActivitySession]
    next_cursor: str | None = None
    pagination_note: str = (
        "Ordered by immutable (user_id, harness, session_id); the window end is pinned, "
        "but newly arriving sessions can join the cohort. Not a point-in-time snapshot."
    )


# ── Skills ─────────────────────────────────────────────────────────────────
# Skill evidence has its own vocabulary. None of these fields means "the skill
# helped": a load or invocation shows its instructions entered context.

SkillAttributionState = Literal["observed", "no_observed_skill_use", "attribution_not_possible"]
SKILL_INVOCATIONS_LIMITATION = (
    "invocations_not_recorded: no harness in this cohort records a distinguishable explicit skill "
    "invocation, so invocation counts are unknown (null), not zero"
)
SKILL_AVAILABILITY_LIMITATION = (
    "availability_not_recorded: no harness in this cohort records which skill file it advertised, "
    "so availability is unknown (null), not zero"
)
SKILL_CONTEXT_LIMITATION = (
    "entered_context_not_helped: a confirmed load or an invocation shows the skill's instructions "
    "entered the model's context, not that the skill was followed or improved the outcome"
)


class SkillEvidenceCoverage(BaseModel):
    """Skill facts extracted from complete current skill projections in the present cohort."""

    candidate_facts: int = 0
    attributed_facts: int = 0
    collision_facts: int = 0
    unmatched_facts: int = 0
    unknown_load_results: int = 0


class SkillActivityCoverage(BaseModel):
    presence: PresenceCoverage
    projection: ProjectionCoverage
    evidence: SkillEvidenceCoverage
    usage_rate_denominator_sessions: int = Field(
        0, description="Present sessions on a skill-supported harness with a complete current skill projection"
    )
    observed_sessions: int = Field(0, description="Sessions with a confirmed load or an invocation")
    usage_rate: float | None = None
    attribution_state: SkillAttributionState
    reasons: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(
        default_factory=lambda: [SKILL_CONTEXT_LIMITATION, LAYER_STABILITY_LIMITATION, FINAL_PUSH_LIMITATION]
    )


class SkillActivitySummaryResponse(BaseModel):
    component: ComponentRef
    period_days: int
    period_start: str
    period_end: str
    time_basis: str = TIME_BASIS
    present_sessions: int
    present_users: int
    available_sessions: int | None = Field(
        description="Sessions where the skill was advertised to the model; null when no harness in the cohort records it"
    )
    loaded_sessions: int = Field(description="Sessions with at least one confirmed load (a successful read)")
    confirmed_loads: int
    load_attempts: int = Field(description="Reads that failed or whose result is unknown; not confirmed loads")
    invoked_sessions: int | None = Field(description="Null when no harness in the cohort records invocations")
    invocations: int | None
    harness_distribution: dict[str, int]
    version_distribution: dict[str, int] = Field(default_factory=dict)
    activation_actions: ActivationActions
    coverage: SkillActivityCoverage


class SkillActivitySession(BaseModel):
    user_id: str
    harness: str
    session_id: str
    last_event_time: str
    projection_state: SessionProjectionState
    source_state: SourceAvailabilityState
    available: bool | None = Field(description="Null when this harness does not record availability")
    confirmed_loads: int
    load_attempts: int
    invocations: int | None = Field(description="Null when this harness does not record invocations")


class SkillActivitySessionsResponse(BaseModel):
    component: ComponentRef
    period_days: int
    time_basis: str = TIME_BASIS
    sessions: list[SkillActivitySession]
    next_cursor: str | None = None
    pagination_note: str = (
        "Ordered by immutable (user_id, harness, session_id); the window end is pinned, "
        "but newly arriving sessions can join the cohort. Not a point-in-time snapshot."
    )


# ── Hooks ──────────────────────────────────────────────────────────────────
# Hook evidence has its own vocabulary. A recorded run shows the hook executed;
# it never shows what the hook changed or that it helped.

HookAttributionState = Literal["observed", "no_recorded_runs", "attribution_not_possible"]
HookEligibilityState = Literal[
    "eligible", "headless", "agent_inactive", "mode_unknown", "agent_unknown", "not_processed"
]
HOOK_EFFECT_LIMITATION = (
    "effect_not_observed: a recorded run shows the hook executed, not what it changed or whether it helped"
)
HOOK_SILENT_LIMITATION = (
    "silent_success_unrecorded: a hook that succeeds without printing output leaves no record, so recorded "
    "runs are a lower bound and no recorded run is not proof the hook did not run"
)


class HookEvidenceCoverage(BaseModel):
    """Recorded hook runs from complete current hook projections in the present cohort."""

    candidate_runs: int = 0
    attributed_runs: int = 0
    collision_runs: int = 0
    unmatched_runs: int = 0


class HookEligibility(BaseModel):
    """Whether each processed present session's installed hook could run at all."""

    eligible_sessions: int = Field(
        0,
        description=(
            "Settings hooks, agent-file hooks whose agent ran interactively, or gated settings hooks "
            "whose agent ran in any mode"
        ),
    )
    headless_sessions: int = Field(0, description="Agent-file hooks whose agent ran headless, where they do not run")
    agent_inactive_sessions: int = Field(0, description="Agent hooks whose agent did not run in the session")
    mode_unknown_sessions: int = 0
    agent_unknown_sessions: int = Field(
        0, description="Gated agent hooks in a subagent's own transcript, which does not record which agent ran"
    )


class HookActivityCoverage(BaseModel):
    presence: PresenceCoverage
    projection: ProjectionCoverage
    eligibility: HookEligibility
    evidence: HookEvidenceCoverage
    usage_rate_denominator_sessions: int = Field(
        0, description="Processed present sessions where the hook could run (eligible)"
    )
    observed_sessions: int = Field(0, description="Eligible sessions with at least one recorded run")
    usage_rate: float | None = Field(None, description="A lower bound when silent successes are unrecorded")
    attribution_state: HookAttributionState
    reasons: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(
        default_factory=lambda: [HOOK_EFFECT_LIMITATION, LAYER_STABILITY_LIMITATION, FINAL_PUSH_LIMITATION]
    )


class HookActivitySummaryResponse(BaseModel):
    component: ComponentRef
    period_days: int
    period_start: str
    period_end: str
    time_basis: str = TIME_BASIS
    present_sessions: int
    present_users: int
    eligible_sessions: int = Field(description="Processed present sessions where the hook could run")
    sessions_with_recorded_run: int
    runs_with_output: int = Field(description="Successful runs that printed output (silent successes are unrecorded)")
    failures: int
    blocks: int
    harness_distribution: dict[str, int]
    version_distribution: dict[str, int] = Field(default_factory=dict)
    activation_actions: ActivationActions
    coverage: HookActivityCoverage


class HookActivitySession(BaseModel):
    user_id: str
    harness: str
    session_id: str
    last_event_time: str
    projection_state: SessionProjectionState
    source_state: SourceAvailabilityState
    eligibility: HookEligibilityState
    runs_with_output: int
    failures: int
    blocks: int


class HookActivitySessionsResponse(BaseModel):
    component: ComponentRef
    period_days: int
    time_basis: str = TIME_BASIS
    sessions: list[HookActivitySession]
    next_cursor: str | None = None
    pagination_note: str = (
        "Ordered by immutable (user_id, harness, session_id); the window end is pinned, "
        "but newly arriving sessions can join the cohort. Not a point-in-time snapshot."
    )
