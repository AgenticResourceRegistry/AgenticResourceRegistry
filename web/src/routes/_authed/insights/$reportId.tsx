// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { createFileRoute, useNavigate, useParams } from "@tanstack/react-router";
import React from "react";
import { Loader2, ArrowLeft } from "lucide-react";
import { Link } from "@tanstack/react-router";
import type { ComponentInsightMetrics, ComponentInsightNarrative, InsightReport } from "@/lib/types";
import { ErrorState } from "@/components/shared/error-state";
import { useLegacyInsightReport } from "@/hooks/use-insights-api";

function ComponentReport({ report }: { report: InsightReport }) {
  const coverage = report.coverage;
  const metrics = report.metrics as unknown as ComponentInsightMetrics | null;
  const narrative = report.narrative as unknown as ComponentInsightNarrative | null;
  const summary = narrative?.summary;
  const analysis = narrative?.component_analysis;
  const canMeasureCalls = !!coverage && coverage.usage_rate_denominator_sessions > 0;
  return (
    <main className="mx-auto max-w-4xl space-y-8 px-4 py-8 sm:px-6">
      <Link to="/components/$componentId" params={{ componentId: report.component_id ?? "" }} search={{ type: "mcps" }}
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
      {report.status === "failed" ? <ErrorState message={report.error_message ?? "Report generation failed"} /> :
       report.status !== "completed" ? <p role="status" className="text-muted-foreground">Report {report.status}. This page updates automatically.</p> : (
        <>
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
          <section aria-label="Present-session distribution" className="space-y-3 border-b border-border pb-6">
            <h2 className="text-lg font-semibold">Present-session distribution</h2>
            <p className="text-sm text-muted-foreground">A session can appear under more than one installed version.</p>
            <div className="grid gap-6 sm:grid-cols-2">
              <div><h3 className="mb-2 text-sm font-medium">Version</h3>
                <dl className="space-y-1">{Object.entries(metrics?.version_distribution ?? {}).map(([version, count]) =>
                  <div key={version} className="flex justify-between gap-4 text-sm"><dt>{version}</dt><dd className="tabular-nums">{count} sessions</dd></div>
                )}</dl>
              </div>
              <div><h3 className="mb-2 text-sm font-medium">Harness</h3>
                <dl className="space-y-1">{Object.entries(metrics?.harness_distribution ?? {}).map(([harness, count]) =>
                  <div key={harness} className="flex justify-between gap-4 text-sm"><dt>{harness}</dt><dd className="tabular-nums">{count} sessions</dd></div>
                )}</dl>
              </div>
            </div>
          </section>
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
        </>
      )}
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
