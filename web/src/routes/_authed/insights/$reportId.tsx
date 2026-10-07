// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { createFileRoute, useNavigate, useParams } from "@tanstack/react-router";
import React from "react";
import { Loader2, ArrowLeft } from "lucide-react";
import { Link } from "@tanstack/react-router";
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

function ReportHeader({ report }: { report: InsightReport }) {
  return (
    <>
      <Link to="/components/$componentId" params={{ componentId: report.component_id ?? "" }}
        search={{ type: report.component_type === "skill" ? "skills" : report.component_type === "hook" ? "hooks" : "mcps" }}
        className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary">
        <ArrowLeft className="h-4 w-4" aria-hidden="true" /> Back to component
      </Link>
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">{report.component_name ?? "Component"} Insights</h1>
        <p className="text-sm text-muted-foreground">
          {new Date(report.period_start).toLocaleDateString()} – {new Date(report.period_end).toLocaleDateString()}
          {report.component_version ? ` · Version ${report.component_version}` : " · All versions"}
        </p>
      </header>
    </>
  );
}

function DistributionSection({ versions, harnesses }: { versions?: Record<string, number>; harnesses?: Record<string, number> }) {
  return (
    <section aria-label="Present-session distribution" className="space-y-3 border-b border-border pb-6">
      <h2 className="text-lg font-semibold">Present-session distribution</h2>
      <p className="text-sm text-muted-foreground">A session can appear under more than one installed version.</p>
      <div className="grid gap-6 sm:grid-cols-2">
        <div><h3 className="mb-2 text-sm font-medium">Version</h3>
          <dl className="space-y-1">{Object.entries(versions ?? {}).map(([version, count]) =>
            <div key={version} className="flex justify-between gap-4 text-sm"><dt>{version}</dt><dd className="tabular-nums">{count} {count === 1 ? "session" : "sessions"}</dd></div>
          )}</dl>
        </div>
        <div><h3 className="mb-2 text-sm font-medium">Harness</h3>
          <dl className="space-y-1">{Object.entries(harnesses ?? {}).map(([harness, count]) =>
            <div key={harness} className="flex justify-between gap-4 text-sm"><dt>{harness}</dt><dd className="tabular-nums">{count} {count === 1 ? "session" : "sessions"}</dd></div>
          )}</dl>
        </div>
      </div>
    </section>
  );
}

function ReportStatus({ report, children }: { report: InsightReport; children: React.ReactNode }) {
  if (report.status === "failed") return <ErrorState message={report.error_message ?? "Report generation failed"} />;
  if (report.status !== "completed") return <p role="status" className="text-muted-foreground">Report {report.status}. This page updates automatically.</p>;
  return <>{children}</>;
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
    <main className="mx-auto max-w-4xl space-y-8 px-4 py-8 sm:px-6">
      <ReportHeader report={report} />
      <ReportStatus report={report}>
        <section aria-label="Evidence" className="space-y-4 border-b border-border pb-6">
          <h2 className="text-lg font-semibold">What the data shows</h2>
          <p className="max-w-[70ch] text-sm leading-relaxed">{narrative?.summary}</p>
          <dl className="grid gap-4 sm:grid-cols-4">
            <div><dt className="text-sm text-muted-foreground">Present sessions</dt><dd className="text-xl font-semibold tabular-nums">{metrics?.present_sessions ?? "—"}</dd></div>
            <div><dt className="text-sm text-muted-foreground">Sessions with a confirmed load</dt><dd className="text-xl font-semibold tabular-nums"><SkillCount value={metrics?.loaded_sessions} measured={measured} /></dd></div>
            <div><dt className="text-sm text-muted-foreground">Sessions with an invocation</dt><dd className="text-xl font-semibold tabular-nums"><SkillCount value={metrics?.invoked_sessions} measured={measured} partial={coverage?.reasons.includes("invoked_not_recorded_on_some_harnesses")} /></dd></div>
            <div><dt className="text-sm text-muted-foreground">Sessions where offered</dt><dd className="text-xl font-semibold tabular-nums"><SkillCount value={metrics?.available_sessions} measured={measured} partial={coverage?.reasons.includes("available_not_recorded_on_some_harnesses")} /></dd></div>
          </dl>
          {measured && !!metrics?.load_attempts && <p className="text-sm text-muted-foreground">{metrics.load_attempts} load {metrics.load_attempts === 1 ? "attempt" : "attempts"} failed or had no confirmed result, and {metrics.load_attempts === 1 ? "is" : "are"} not counted as {metrics.load_attempts === 1 ? "a load" : "loads"}.</p>}
          {notRecorded.length > 0 && <p className="text-sm text-muted-foreground">The harnesses in this cohort do not record {notRecorded.join(" or ")}, so those counts are unknown rather than zero.</p>}
          {!measured && <p className="text-sm text-muted-foreground">Skill evidence is unavailable for this period; missing measurements do not imply no use.</p>}
        </section>
        <DistributionSection versions={metrics?.version_distribution} harnesses={metrics?.harness_distribution} />
        <section aria-label="Attribution coverage" className="space-y-3">
          <h2 className="text-lg font-semibold">Attribution coverage</h2>
          {coverage ? <>
            <p className="text-sm">{coverage.projection.projection_complete_sessions} of {coverage.presence.present_sessions} present sessions processed. {coverage.observed_sessions} had a confirmed load or an invocation.</p>
            <p className="text-sm text-muted-foreground">{coverage.evidence.collision_facts} ambiguous and {coverage.evidence.unmatched_facts} unmatched skill records could not be tied to this verified install, and are not counted.</p>
            {coverage.reasons.length > 0 && <p className="text-sm text-muted-foreground">Gaps: {coverage.reasons.join(", ").replaceAll("_", " ")}.</p>}
            <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
              {coverage.limitations.map((note) => <li key={note}>{note.replaceAll("_", " ")}</li>)}
            </ul>
          </> : <p className="text-sm text-muted-foreground">Coverage is unavailable for this report.</p>}
        </section>
      </ReportStatus>
    </main>
  );
}

function HookReport({ report }: { report: InsightReport }) {
  const coverage = report.coverage as HookInsightCoverage | null | undefined;
  const metrics = report.metrics as unknown as HookInsightMetrics | null;
  const narrative = report.narrative as unknown as SkillInsightNarrative | null;
  const measured = !!coverage && coverage.usage_rate_denominator_sessions > 0;
  const eligibility = coverage?.eligibility;
  const couldNotRun = [
    eligibility?.headless_sessions ? `${eligibility.headless_sessions} ran headless, where agent hooks do not run` : null,
    eligibility?.agent_inactive_sessions ? `${eligibility.agent_inactive_sessions} did not run the hook's agent` : null,
    eligibility?.mode_unknown_sessions ? `${eligibility.mode_unknown_sessions} did not record whether they were headless` : null,
  ].filter(Boolean);
  const notMeasured = <>Not measured</>;
  return (
    <main className="mx-auto max-w-4xl space-y-8 px-4 py-8 sm:px-6">
      <ReportHeader report={report} />
      <ReportStatus report={report}>
        <section aria-label="Evidence" className="space-y-4 border-b border-border pb-6">
          <h2 className="text-lg font-semibold">What the data shows</h2>
          <p className="max-w-[70ch] text-sm leading-relaxed">{narrative?.summary}</p>
          <dl className="grid gap-4 sm:grid-cols-4">
            <div><dt className="text-sm text-muted-foreground">Present sessions</dt><dd className="text-xl font-semibold tabular-nums">{metrics?.present_sessions ?? "—"}</dd></div>
            <div><dt className="text-sm text-muted-foreground">Sessions where it could run</dt><dd className="text-xl font-semibold tabular-nums">{metrics?.eligible_sessions ?? "—"}</dd></div>
            <div><dt className="text-sm text-muted-foreground">Sessions with a recorded run</dt><dd className="text-xl font-semibold tabular-nums">{measured ? <>{metrics?.sessions_with_recorded_run}<span className="block text-xs font-normal text-muted-foreground">At least; silent runs are not counted</span></> : notMeasured}</dd></div>
            <div><dt className="text-sm text-muted-foreground">Failed or blocked runs</dt><dd className="text-xl font-semibold tabular-nums">{measured ? (metrics ? metrics.failures + metrics.blocks : "—") : notMeasured}</dd></div>
          </dl>
          {measured && metrics && <p className="text-sm text-muted-foreground">Recorded runs: {metrics.runs_with_output} succeeded with output, {metrics.failures} failed, {metrics.blocks} blocked an action.</p>}
          {couldNotRun.length > 0 && <p className="text-sm text-muted-foreground">Excluded because the hook could not run: {couldNotRun.join("; ")}.</p>}
          {eligibility?.agent_unknown_sessions ? <p className="text-sm text-muted-foreground">Excluded because it is not known whether the hook could run: {eligibility.agent_unknown_sessions} subagent {eligibility.agent_unknown_sessions === 1 ? "session" : "sessions"} where neither the subagent nor its parent session recorded which agent ran.</p> : null}
          {!measured && <p className="text-sm text-muted-foreground">There were no processed sessions where this hook could run; missing measurements do not imply no use.</p>}
        </section>
        <DistributionSection versions={metrics?.version_distribution} harnesses={metrics?.harness_distribution} />
        <section aria-label="Attribution coverage" className="space-y-3">
          <h2 className="text-lg font-semibold">Attribution coverage</h2>
          {coverage ? <>
            <p className="text-sm">{coverage.projection.projection_complete_sessions} of {coverage.presence.present_sessions} present sessions processed. {coverage.observed_sessions} of {coverage.usage_rate_denominator_sessions} sessions where it could run had a recorded run.</p>
            <p className="text-sm text-muted-foreground">{coverage.evidence.collision_runs} ambiguous and {coverage.evidence.unmatched_runs} unmatched recorded runs could not be tied to this verified hook, and are not counted.</p>
            {coverage.reasons.length > 0 && <p className="text-sm text-muted-foreground">Gaps: {coverage.reasons.join(", ").replaceAll("_", " ")}.</p>}
            <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
              {coverage.limitations.map((note) => <li key={note}>{note.replaceAll("_", " ")}</li>)}
            </ul>
          </> : <p className="text-sm text-muted-foreground">Coverage is unavailable for this report.</p>}
        </section>
      </ReportStatus>
    </main>
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
    <main className="mx-auto max-w-4xl space-y-8 px-4 py-8 sm:px-6">
      <ReportHeader report={report} />
      <ReportStatus report={report}>
          <section aria-label="Interpretive insights" className="space-y-4 border-b border-border pb-7">
            <h2 className="text-lg font-semibold">What the published calls suggest</h2>
            {!analysis ? (
              <p className="max-w-[70ch] text-sm text-muted-foreground">This report predates evidence-backed analysis. Generate a new report to assess sampled sessions.</p>
            ) : analysis.state !== "assessed" || analysis.findings.length === 0 ? (
              <p className="max-w-[70ch] text-sm text-muted-foreground">No grounded findings could be drawn from the available sample. This is not evidence that the component was unused.</p>
            ) : (
              <ol className="space-y-5">
                {analysis.findings.map((finding, index) => (
                  <li key={`${finding.kind}-${index}`} className="space-y-2">
                    <p className="max-w-[70ch] text-sm leading-relaxed text-foreground">{finding.insight}</p>
                    <p className="text-xs text-muted-foreground">
                      {`Interpretation of published calls · ${finding.kind.replaceAll("_", " ")}`}
                      {` · ${finding.confidence} confidence`}
                    </p>
                    <ul className="space-y-1 pl-4 text-xs text-muted-foreground">
                      {finding.evidence_refs.map((ref) => (
                        <li key={ref} className="list-disc break-words">
                          <span className="font-medium text-foreground">{ref}</span>
                          {analysis.evidence?.[ref] ? ` · ${analysis.evidence[ref]}` : " · Referenced excerpt unavailable"}
                        </li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ol>
            )}
            {analysis && <p className="text-xs text-muted-foreground">Analysis considered {analysis.sampled_sessions} sampled sessions{analysis.truncated ? " (sample or excerpts truncated)" : ""}. Findings are interpretations, not cohort-wide conclusions.</p>}
          </section>
          <section aria-label="Evidence" className="space-y-3 border-b border-border pb-6">
            <h2 className="text-lg font-semibold">What the data shows</h2>
            <p className="max-w-[70ch] text-sm leading-relaxed">{summary}</p>
            <dl className="grid gap-4 sm:grid-cols-3">
              <div><dt className="text-sm text-muted-foreground">Present sessions</dt><dd className="text-xl font-semibold tabular-nums">{metrics?.present_sessions ?? "—"}</dd></div>
              <div><dt className="text-sm text-muted-foreground">Observed calls</dt><dd className="text-xl font-semibold tabular-nums">{canMeasureCalls ? (metrics?.observed_calls ?? "—") : "Not measured"}</dd></div>
              <div><dt className="text-sm text-muted-foreground">Known errors</dt><dd className="text-xl font-semibold tabular-nums">{canMeasureCalls ? (metrics?.result_states.error ?? "—") : "Not measured"}</dd></div>
            </dl>
            {!canMeasureCalls && <p className="text-sm text-muted-foreground">Attribution is unavailable for this period; missing measurements do not imply no use.</p>}
          </section>
          <DistributionSection versions={metrics?.version_distribution} harnesses={metrics?.harness_distribution} />
          <section aria-label="Attribution coverage" className="space-y-3">
            <h2 className="text-lg font-semibold">Attribution coverage</h2>
            {coverage ? <>
              <p className="text-sm">{coverage.projection.projection_complete_sessions} of {coverage.presence.present_sessions} present sessions processed. {coverage.observed_sessions} had attributed calls.</p>
              <p className="text-sm text-muted-foreground">{metrics?.cohort_collision_calls ?? coverage.calls.collision_calls} collisions and {metrics?.cohort_unmatched_calls ?? coverage.calls.unmatched_calls} unmatched calls across the present-session cohort could not be assigned to this component or any other.</p>
              {coverage.reasons.length > 0 && <p className="text-sm text-muted-foreground">Gaps: {coverage.reasons.join(", ").replaceAll("_", " ")}.</p>}
              <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
                {coverage.limitations.map((note) => <li key={note}>{note.replaceAll("_", " ")}</li>)}
              </ul>
            </> : <p className="text-sm text-muted-foreground">Coverage is unavailable for this report.</p>}
          </section>
      </ReportStatus>
    </main>
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
