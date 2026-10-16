// SPDX-FileCopyrightText: 2026 Observal Contributors
// SPDX-License-Identifier: Apache-2.0

import type { ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { ArrowLeft, CheckCircle2, Clock3, Loader2, XCircle } from "lucide-react";
import { PageHeader } from "@/components/layouts/page-header";
import type { InsightReport } from "@/lib/types";

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
              className="inline-flex items-center gap-2 rounded-md px-2 py-1 text-sm text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary">
              <ArrowLeft className="h-4 w-4" aria-hidden="true" /> Back to component
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
