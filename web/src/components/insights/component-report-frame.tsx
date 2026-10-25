// SPDX-FileCopyrightText: 2026 Observal Contributors
// SPDX-License-Identifier: Apache-2.0

import type { ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { ArrowLeft, CheckCircle2, Clock3, Loader2, XCircle } from "lucide-react";
import { PageHeader } from "@/components/layouts/page-header";
import type { CoverageFacts, InsightReport } from "@/lib/types";

/** The same report shell and status hierarchy used for Agent Insights, with component-specific facts. */
export function ComponentReportFrame({ report, children }: { report: InsightReport; children: ReactNode }) {
  const componentType = report.component_type === "skill" ? "skills" : report.component_type === "hook" ? "hooks" : "mcps";
  const name = report.component_name || "Component";
  const pending = report.status === "pending" || report.status === "running";

  return (
    <>
      <PageHeader
        title={`${name} Insights`}
        breadcrumbs={[{ label: "Agentic Resources", href: `/resources/${componentType}` }, { label: name }, { label: "Insights" }]}
        actionButtonsRight={
          report.component_id ? (
            <Link to="/components/$componentId" params={{ componentId: report.component_id }} search={{ type: componentType }}
              className="inline-flex shrink-0 items-center gap-2 whitespace-nowrap rounded-md px-2 py-1 text-sm text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary">
              <ArrowLeft className="h-4 w-4" aria-hidden="true" /> <span className="sm:hidden">Back</span><span className="hidden sm:inline">Back to component</span>
            </Link>
          ) : null
        }
      />
      <main className="mx-auto w-full max-w-6xl space-y-6 px-4 py-6 sm:px-6">
        <div className="space-y-3 border-b border-border pb-5">
          <h2 className="text-2xl font-semibold tracking-tight">{name} Insights</h2>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-muted-foreground">
            <span className={report.status === "completed" ? "inline-flex items-center gap-1.5 text-success" : report.status === "failed" ? "inline-flex items-center gap-1.5 text-destructive" : "inline-flex items-center gap-1.5 text-info"}>
              {report.status === "completed" ? <CheckCircle2 className="h-4 w-4" aria-hidden="true" /> : report.status === "failed" ? <XCircle className="h-4 w-4" aria-hidden="true" /> : pending && report.status === "running" ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Clock3 className="h-4 w-4" aria-hidden="true" />}
              <span className="font-medium capitalize">{report.status === "pending" ? "Queued" : report.status}</span>
            </span>
            <span>{new Date(report.period_start).toLocaleDateString()} – {new Date(report.period_end).toLocaleDateString()}</span>
            <span>{report.component_version ? `Version ${report.component_version}` : "All versions"}</span>
          </div>
        </div>
        {pending && (
          <div role="status" className="mx-auto max-w-md space-y-3 py-12 text-center">
            <Loader2 className="mx-auto h-7 w-7 animate-spin text-primary" aria-hidden="true" />
            <p className="text-sm">{report.progress_message || (report.status === "pending" ? "Waiting in queue…" : "Generating report…")}</p>
            <div className="h-2 overflow-hidden rounded-full bg-muted" role="progressbar" aria-label="Report progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={report.progress_percent ?? 0}>
              <div className="h-full bg-primary transition-[width]" style={{ width: `${Math.min(100, Math.max(0, report.progress_percent ?? 0))}%` }} />
            </div>
          </div>
        )}
        {report.status === "failed" && (
          <div role="alert" className="rounded-lg border border-destructive/30 bg-destructive/5 p-5">
            <p className="font-medium text-destructive">Report generation failed</p>
            <p className="mt-1 text-sm text-muted-foreground">{report.error_message || "Try generating this report again from the component page."}</p>
          </div>
        )}
        {report.status === "completed" && children}
      </main>
    </>
  );
}

export function EvidencePanel({ summary, stats, children }: {
  summary?: string;
  stats: { label: string; value: ReactNode; note?: string }[];
  children?: ReactNode;
}) {
  return (
    <section aria-label="Evidence" className="space-y-5">
      <div className="space-y-2">
        <h3 className="text-lg font-semibold">What the data shows</h3>
        <p className="max-w-[70ch] text-sm leading-relaxed text-muted-foreground">{summary || "No summary is available for this report."}</p>
      </div>
      <dl className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        {stats.map(({ label, value, note }) => (
          <div key={label} className="min-w-0 rounded-lg border border-border bg-card p-4">
            <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</dt>
            <dd className="mt-2 break-words text-xl font-semibold tabular-nums text-foreground sm:text-2xl">{value}</dd>
            {note && <dd className="mt-1 text-xs text-muted-foreground">{note}</dd>}
          </div>
        ))}
      </dl>
      {children && <div className="space-y-2 text-sm text-muted-foreground">{children}</div>}
    </section>
  );
}

export function CoveragePanel({ coverage, children }: { coverage: CoverageFacts | null | undefined; children?: ReactNode }) {
  return (
    <section aria-label="Attribution coverage" className="space-y-4 rounded-lg border border-border bg-card p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-lg font-semibold">Attribution coverage</h3>
        {coverage && <span className="rounded-md bg-muted px-2 py-1 text-xs font-medium text-muted-foreground">{coverage.attribution_state.replaceAll("_", " ")}</span>}
      </div>
      {!coverage ? <p className="text-sm text-muted-foreground">Coverage is unavailable for this report.</p> : <>
        <p className="text-sm">{coverage.projection.projection_complete_sessions} of {coverage.presence.present_sessions} verified-present {coverage.presence.present_sessions === 1 ? "session" : "sessions"} processed.
          {coverage.projection.projection_pending_sessions > 0 && ` ${coverage.projection.projection_pending_sessions} pending.`}
          {coverage.projection.projection_failed_sessions > 0 && ` ${coverage.projection.projection_failed_sessions} failed.`}
          {coverage.projection.unsupported_present_sessions > 0 && ` ${coverage.projection.unsupported_present_sessions} on unsupported harnesses.`}
        </p>
        {children && <div className="space-y-2 text-sm text-muted-foreground">{children}</div>}
        {(!!coverage.presence.legacy_hash_sessions || !!coverage.presence.snapshot_missing_sessions) &&
          <div role="note" className="space-y-1 rounded-md border border-border bg-muted/40 p-3 text-sm">
            <p className="font-medium">Some sessions lack installation evidence</p>
            <ul className="list-disc pl-5 text-muted-foreground">
              {!!coverage.presence.legacy_hash_sessions && <li>{coverage.presence.legacy_hash_sessions} project {coverage.presence.legacy_hash_sessions === 1 ? "session has a" : "sessions have"} legacy layer {coverage.presence.legacy_hash_sessions === 1 ? "hash" : "hashes"}.</li>}
              {!!coverage.presence.snapshot_missing_sessions && <li>{coverage.presence.snapshot_missing_sessions} project {coverage.presence.snapshot_missing_sessions === 1 ? "session has" : "sessions have"} no installation snapshot.</li>}
            </ul>
            <p className="text-muted-foreground">These are project-wide gaps, not proof that this component was installed or used. They are excluded from verified presence, use rates, and distinct-user counts. A missing v2 snapshot may arrive late if its original matching evidence still exists. For older sessions, replaying a transcript or checking today's install cannot prove what was installed then.</p>
          </div>}
        {coverage.reasons.length > 0 && <p className="text-sm text-muted-foreground">Evidence gaps: {coverage.reasons.map((reason) => reason.replaceAll("_", " ")).join(", ")}.</p>}
        {coverage.limitations.length > 0 && <details className="text-sm text-muted-foreground">
          <summary className="cursor-pointer rounded-sm font-medium text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary">How to interpret these counts</summary>
          <ul className="mt-3 list-disc space-y-1 pl-5">
            {coverage.limitations.map((note) => <li key={note}>{note.replaceAll("_", " ")}</li>)}
          </ul>
        </details>}
      </>}
    </section>
  );
}
