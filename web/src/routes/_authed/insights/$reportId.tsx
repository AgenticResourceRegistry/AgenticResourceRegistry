// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { createFileRoute, useNavigate, useParams } from "@tanstack/react-router";
import React from "react";
import { Loader2 } from "lucide-react";
import { ComponentReportFrame, CoveragePanel, EvidencePanel } from "@/components/insights/component-report-frame";
import { SkillEvidenceBreakdown } from "@/components/insights/skill-evidence-breakdown";
import type {
  ComponentInsightCoverage,
  ComponentInsightMetrics,
  ComponentInsightNarrative,
  HookInsightCoverage,
  HookInsightMetrics,
  InsightReport,
  SkillInsightCoverage,
  SkillInsightMetrics,
  SkillInsightNarrative,
} from "@/lib/types";
import { ErrorState } from "@/components/shared/error-state";
import { useLegacyInsightReport } from "@/hooks/use-insights-api";

function DistributionSection({ versions, harnesses }: { versions?: Record<string, number>; harnesses?: Record<string, number> }) {
  return (
    <section aria-label="Present-session distribution" className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold">Present-session distribution</h3>
        <p className="text-sm text-muted-foreground">A session can appear under more than one installed version.</p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {([{ label: "Version", values: versions }, { label: "Harness", values: harnesses }] as const).map(({ label, values }) => (
          <div key={label} className="min-w-0 rounded-lg border border-border bg-card p-5">
            <h4 className="mb-3 text-sm font-medium">{label}</h4>
            {Object.keys(values ?? {}).length === 0 ? <p className="text-sm text-muted-foreground">No verified distribution recorded.</p> : (
              <dl className="space-y-2">{Object.entries(values ?? {}).map(([name, count]) => (
                <div key={name} className="flex justify-between gap-4 text-sm"><dt className="min-w-0 break-words text-muted-foreground">{name}</dt><dd className="shrink-0 tabular-nums">{count} {count === 1 ? "session" : "sessions"}</dd></div>
              ))}</dl>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

/** A count that is either measured, not measured for this period, or never recorded by the cohort's harnesses. */
function SkillCount({ value, measured, partial = false }: { value: number | null | undefined; measured: boolean; partial?: boolean }) {
  if (value === null) return <>Not recorded</>;
  if (!measured || value === undefined) return <>Not measured</>;
  return <>{value}{partial && <span className="block text-xs font-normal text-muted-foreground">Only harnesses that record it</span>}</>;
}

function SkillReport({ report }: { report: InsightReport }) {
  const coverage = report.coverage as SkillInsightCoverage | null | undefined;
  const metrics = report.metrics as unknown as SkillInsightMetrics | null;
  const narrative = report.narrative as unknown as SkillInsightNarrative | null;
  const measured = !!coverage && coverage.usage_rate_denominator_sessions > 0;
  const notRecorded = [
    metrics?.available_sessions === null ? "which skill files were advertised" : null,
    metrics?.invoked_sessions === null ? "explicit invocations" : null,
  ].filter(Boolean);
  return (
    <ComponentReportFrame report={report}>
        <EvidencePanel summary={narrative?.summary} stats={[
          { label: "Present sessions", value: metrics?.present_sessions ?? "—" },
          { label: "Confirmed loads", value: <SkillCount value={metrics?.loaded_sessions} measured={measured} />, note: "Sessions where instructions entered context" },
          { label: "Invocations", value: <SkillCount value={metrics?.invoked_sessions} measured={measured} partial={coverage?.reasons.includes("invoked_not_recorded_on_some_harnesses")} /> },
          { label: "Offered", value: <SkillCount value={metrics?.available_sessions} measured={measured} partial={coverage?.reasons.includes("available_not_recorded_on_some_harnesses")} /> },
        ]}>
          {measured && !!metrics?.load_attempts && <p className="text-sm text-muted-foreground">{metrics.load_attempts} load {metrics.load_attempts === 1 ? "attempt" : "attempts"} failed or had no confirmed result, and {metrics.load_attempts === 1 ? "is" : "are"} not counted as {metrics.load_attempts === 1 ? "a load" : "loads"}.</p>}
          {notRecorded.length > 0 && <p className="text-sm text-muted-foreground">The harnesses in this cohort do not record {notRecorded.join(" or ")}, so those counts are unknown rather than zero.</p>}
          {!measured && <p>Skill evidence is unavailable for this period; missing measurements do not imply no use.</p>}
        </EvidencePanel>
        <CoveragePanel coverage={coverage}>
          {coverage && <p>{coverage.observed_sessions} {coverage.observed_sessions === 1 ? "session" : "sessions"} had a confirmed load or an invocation. {coverage.evidence.collision_facts} ambiguous and {coverage.evidence.unmatched_facts} unmatched skill records were not counted.</p>}
        </CoveragePanel>
        <DistributionSection versions={metrics?.version_distribution} harnesses={metrics?.harness_distribution} />
        <SkillEvidenceBreakdown metrics={metrics} coverage={coverage} />
    </ComponentReportFrame>
  );
}

function HookReport({ report }: { report: InsightReport }) {
  const coverage = report.coverage as HookInsightCoverage | null | undefined;
  const metrics = report.metrics as unknown as HookInsightMetrics | null;
  const narrative = report.narrative as unknown as SkillInsightNarrative | null;
  const measured = !!coverage && coverage.usage_rate_denominator_sessions > 0;
  const eligibility = coverage?.eligibility;
  const couldNotRun = [
    eligibility?.headless_sessions ? `${eligibility.headless_sessions} ran the hook's agent headless, where this hook's placement cannot run` : null,
    eligibility?.agent_inactive_sessions ? `${eligibility.agent_inactive_sessions} did not run the hook's agent` : null,
    eligibility?.mode_unknown_sessions ? `${eligibility.mode_unknown_sessions} did not record whether they were headless` : null,
  ].filter(Boolean);
  const notMeasured = <>Not measured</>;
  // A lower bound only where some harness in the cohort leaves silent runs unrecorded.
  const lowerBound = !!coverage?.limitations.some((note) => note.startsWith("silent_success_unrecorded"));
  const silentRuns = metrics?.silent_runs ?? null;
  return (
    <ComponentReportFrame report={report}>
        <EvidencePanel summary={narrative?.summary} stats={[
          { label: "Present sessions", value: metrics?.present_sessions ?? "—" },
          { label: "Could run", value: metrics?.eligible_sessions ?? "—", note: "Processed, eligible sessions" },
          { label: "Recorded runs", value: measured ? metrics?.sessions_with_recorded_run ?? "—" : notMeasured, note: lowerBound ? "At least; silent runs may be unrecorded" : undefined },
          { label: "Failed or blocked", value: measured ? (metrics ? metrics.failures + metrics.blocks : "—") : notMeasured },
        ]}>
          {measured && metrics && <p className="text-sm text-muted-foreground">Recorded runs: {metrics.runs_with_output} succeeded with output, {silentRuns !== null && <>{silentRuns} succeeded silently, </>}{metrics.failures} failed, {metrics.blocks} blocked an action.</p>}
          {couldNotRun.length > 0 && <p className="text-sm text-muted-foreground">Excluded because the hook could not run: {couldNotRun.join("; ")}.</p>}
          {eligibility?.agent_unknown_sessions ? <p className="text-sm text-muted-foreground">Excluded because it is not known whether the hook could run: {eligibility.agent_unknown_sessions} subagent {eligibility.agent_unknown_sessions === 1 ? "session" : "sessions"} where neither the subagent nor its parent session recorded which agent ran.</p> : null}
          {eligibility?.version_unverified_sessions ? <p className="text-sm text-muted-foreground">Excluded because it is not known whether the hook could run: {eligibility.version_unverified_sessions} {eligibility.version_unverified_sessions === 1 ? "session was" : "sessions were"} recorded by a harness version whose hook records are not verified yet.</p> : null}
          {!measured && <p>There were no processed sessions where this hook could run; missing measurements do not imply no use.</p>}
        </EvidencePanel>
        <CoveragePanel coverage={coverage}>
          {coverage && <p>{coverage.observed_sessions} of {coverage.usage_rate_denominator_sessions} eligible {coverage.usage_rate_denominator_sessions === 1 ? "session" : "sessions"} had a recorded run. {coverage.evidence.collision_runs} ambiguous and {coverage.evidence.unmatched_runs} unmatched runs were not counted.</p>}
        </CoveragePanel>
        <DistributionSection versions={metrics?.version_distribution} harnesses={metrics?.harness_distribution} />
    </ComponentReportFrame>
  );
}

function ComponentReport({ report }: { report: InsightReport }) {
  if (report.component_type === "skill") return <SkillReport report={report} />;
  if (report.component_type === "hook") return <HookReport report={report} />;
  const coverage = report.coverage as ComponentInsightCoverage | null | undefined;
  const metrics = report.metrics as unknown as ComponentInsightMetrics | null;
  const narrative = report.narrative as unknown as ComponentInsightNarrative | null;
  const summary = narrative?.summary;
  const analysis = narrative?.component_analysis;
  const canMeasureCalls = !!coverage && coverage.usage_rate_denominator_sessions > 0;
  return (
    <ComponentReportFrame report={report}>
          <EvidencePanel summary={summary} stats={[
            { label: "Present sessions", value: metrics?.present_sessions ?? "—" },
            { label: "Observed calls", value: canMeasureCalls ? metrics?.observed_calls ?? "—" : "Not measured" },
            { label: "Sessions with calls", value: canMeasureCalls ? metrics?.observed_sessions ?? "—" : "Not measured" },
            { label: "Known errors", value: canMeasureCalls ? metrics?.result_states.error ?? "—" : "Not measured", note: "Unknown results are not successes" },
          ]}>
            {!canMeasureCalls && <p>Attribution is unavailable for this period; missing measurements do not imply no use.</p>}
          </EvidencePanel>
          <CoveragePanel coverage={coverage}>
            {coverage && <p>{coverage.observed_sessions} {coverage.observed_sessions === 1 ? "session" : "sessions"} had attributed calls. {metrics?.cohort_collision_calls ?? coverage.calls.collision_calls} collisions and {metrics?.cohort_unmatched_calls ?? coverage.calls.unmatched_calls} unmatched calls across the present-session cohort were not assigned to this MCP.</p>}
          </CoveragePanel>
          <DistributionSection versions={metrics?.version_distribution} harnesses={metrics?.harness_distribution} />
          <section aria-label="Interpretive insights" className="space-y-4 rounded-lg border border-border bg-card p-5">
            <h3 className="text-lg font-semibold">What the published calls suggest</h3>
            {!analysis ? (
              <p className="max-w-[70ch] text-sm text-muted-foreground">This report predates evidence-backed analysis. Generate a new report to assess sampled sessions.</p>
            ) : analysis.state !== "assessed" || analysis.findings.length === 0 ? (
              <p className="max-w-[70ch] text-sm text-muted-foreground">No grounded findings could be drawn from the available sample. This is not evidence that the component was unused.</p>
            ) : (
              <ol className="space-y-5">
                {analysis.findings.map((finding, index) => (
                  <li key={`${finding.kind}-${index}`} className="space-y-2">
                    <p className="max-w-[70ch] text-sm leading-relaxed">{finding.insight}</p>
                    <p className="text-xs text-muted-foreground">Interpretation of published calls · {finding.kind.replaceAll("_", " ")} · {finding.confidence} confidence</p>
                    <ul className="space-y-1 pl-4 text-xs text-muted-foreground">
                      {finding.evidence_refs.map((ref) => <li key={ref} className="list-disc break-words"><span className="font-medium text-foreground">{ref}</span>{analysis.evidence?.[ref] ? ` · ${analysis.evidence[ref]}` : " · Referenced excerpt unavailable"}</li>)}
                    </ul>
                  </li>
                ))}
              </ol>
            )}
            {analysis && <p className="text-xs text-muted-foreground">Analysis considered {analysis.sampled_sessions} sampled sessions{analysis.truncated ? " (sample or excerpts truncated)" : ""}. Findings are interpretations, not cohort-wide conclusions.</p>}
          </section>
    </ComponentReportFrame>
  );
}

function LegacyInsightRedirect() {
  const { reportId } = useParams({ from: "/_authed/insights/$reportId" });
  const navigate = useNavigate();
  const { data: report, isLoading, isError } = useLegacyInsightReport(reportId);

  React.useEffect(() => {
    if (!report || report.subject_type === "component" || !report.agent_id) return;
    void navigate({
      to: "/agents/$agentId/insights/$reportId",
      params: { agentId: report.agent_id, reportId: report.id },
      replace: true,
    });
  }, [navigate, report]);

  if (isError) return <ErrorState message="Failed to load report" />;
  if (report?.subject_type === "component") return <ComponentReport report={report} />;

  return (
    <div className="flex items-center justify-center py-20">
      {isLoading ? <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /> : null}
    </div>
  );
}

export const Route = createFileRoute("/_authed/insights/$reportId")({
  component: LegacyInsightRedirect,
});
