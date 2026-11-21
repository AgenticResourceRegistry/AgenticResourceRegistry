// SPDX-FileCopyrightText: 2026 Observal Contributors
// SPDX-License-Identifier: Apache-2.0

import type { SkillInsightCoverage, SkillInsightMetrics } from "@/lib/types";

type Fact = { label: string; value: number | null | undefined; description?: string };

function FactGroup({ title, facts }: { title: string; facts: Fact[] }) {
  return (
    <div className="min-w-0 space-y-3">
      <h4 className="text-sm font-semibold">{title}</h4>
      <dl className="space-y-2 text-sm">
        {facts.map(({ label, value, description }) => (
          <div key={label} className="flex flex-wrap justify-between gap-x-4 gap-y-1 border-b border-border/60 pb-2 last:border-0">
            <dt className="text-muted-foreground">{label}{description && <span className="block text-xs">{description}</span>}</dt>
            <dd className="font-medium tabular-nums">{value === null ? "Not recorded" : value === undefined ? "Unavailable" : value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/** Additional published facts only. Never infer skill effectiveness from a load or an activation. */
export function SkillEvidenceBreakdown({ metrics, coverage }: {
  metrics: SkillInsightMetrics | null;
  coverage: SkillInsightCoverage | null | undefined;
}) {
  if (!metrics || !coverage) return null;
  const hasCodex = "codex" in metrics.harness_distribution;
  const codexOnly = Object.keys(metrics.harness_distribution).length === 1 && hasCodex;
  return (
    <details className="rounded-lg border border-border bg-card p-5">
      <summary className="cursor-pointer rounded-sm text-lg font-semibold focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary">
        Detailed skill evidence
      </summary>
      <p className="mt-3 max-w-[70ch] text-sm text-muted-foreground">
        Presence, projection and recorded skill facts are different stages. Legacy sessions without a verified install are not counted as present; a confirmed load is not proof that the skill helped.{hasCodex && " Native Codex selection is not a separately confirmed load."}
      </p>
      <div className="mt-6 grid gap-x-10 gap-y-8 md:grid-cols-2">
        <FactGroup title="Presence" facts={[
          { label: "Project sessions eligible for matching", value: coverage.presence.eligible_sessions },
          { label: "Legacy layer hashes", value: coverage.presence.legacy_hash_sessions, description: "Cannot establish verified presence" },
          { label: "Missing install snapshots", value: coverage.presence.snapshot_missing_sessions },
          { label: "Verified-present sessions", value: coverage.presence.verified_presence_sessions ?? coverage.presence.present_sessions },
          { label: "Sessions with a drifted install", value: coverage.presence.drifted_presence_sessions },
          { label: "Distinct users in present sessions", value: metrics.present_users },
        ]} />
        <FactGroup title="Projection of present sessions" facts={[
          { label: "Processed", value: coverage.projection.projection_complete_sessions },
          { label: "Pending", value: coverage.projection.projection_pending_sessions },
          { label: "Failed", value: coverage.projection.projection_failed_sessions },
          { label: "Stale", value: coverage.projection.projection_stale_sessions },
          { label: "Source missing", value: coverage.projection.source_missing_sessions },
          { label: "Source unavailable", value: coverage.projection.source_unavailable_sessions },
          { label: "Source incomplete", value: coverage.projection.source_incomplete_sessions },
        ]} />
        <FactGroup title="Skill records in the processed cohort" facts={[
          { label: "Candidate records", value: coverage.evidence.candidate_facts },
          { label: "Attributed records", value: coverage.evidence.attributed_facts },
          { label: "Unmatched records", value: coverage.evidence.unmatched_facts, description: "Not assigned to this verified skill" },
          { label: "Ambiguous records", value: coverage.evidence.collision_facts },
          { label: "Unknown load results", value: metrics.load_attempts === null ? null : coverage.evidence.unknown_load_results, description: "Not confirmed loads" },
          { label: "Confirmed loads", value: metrics.confirmed_loads },
          { label: "Unconfirmed load attempts", value: metrics.load_attempts },
          { label: codexOnly ? "Native Codex selections" : hasCodex ? "Invocations / selections" : "Explicit invocations", value: metrics.invocations, description: hasCodex ? "Not separately confirmed loads" : undefined },
        ]} />
        <div className="space-y-3">
          <FactGroup title="Activation actions" facts={[
            { label: "In-session actions", value: metrics.activation_actions?.context_sessions },
            { label: "Actions attributed to the next session", value: metrics.activation_actions?.next_session_sessions },
          ]} />
          <p className="max-w-[45ch] text-xs text-muted-foreground">Activation is configuration activity, not observed skill use. Counts are best effort and do not represent every install.</p>
        </div>
      </div>
    </details>
  );
}
