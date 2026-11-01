// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useMemo, useState } from "react";
import { AlertTriangle, ChevronDown, ChevronRight, CircleAlert, ShieldAlert, ShieldCheck } from "lucide-react";

import { PageHeader } from "@/components/layouts/page-header";
import { EmptyState } from "@/components/shared/empty-state";
import { ErrorState } from "@/components/shared/error-state";
import { TableSkeleton } from "@/components/shared/skeleton-layouts";
import { UserSearchInput } from "@/components/shared/user-search-input";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PickerSelect } from "@/components/ui/picker-select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useSecurityEvents } from "@/hooks/use-api";
import type { SecurityEvent } from "@/lib/types";
import { AdminMetric, AdminMetricStrip, AdminPanel, AdminTableFooter } from "./components/admin-surface";

const EVENT_TYPES = [
  "all",
  "auth.login.success",
  "auth.login.failure",
  "auth.sso.success",
  "authz.permission_denied",
  "authz.role_changed",
  "admin.user.created",
  "admin.user.deleted",
  "admin.setting.changed",
  "admin.alert_rule.changed",
  "agent.injection_detected",
  "ingestion.secrets_redacted",
  "ingestion.malformed_otlp",
];
const SEVERITIES = ["all", "info", "warning", "critical"];
const PAGE_SIZE = 50;

function formatTimestamp(timestamp: string) {
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? timestamp : date.toLocaleString();
}

function severityBadge(severity: string) {
  const style = severity === "critical"
    ? "border-destructive/20 bg-destructive/12 text-destructive"
    : severity === "warning"
      ? "border-warning/20 bg-warning/12 text-warning"
      : "border-border bg-surface-raised text-muted-foreground";
  return <Badge variant="outline" className={`gap-1.5 text-3xs ${style}`}><span className="h-1.5 w-1.5 rounded-full bg-current" />{severity}</Badge>;
}

function outcomeBadge(outcome: string) {
  const failed = outcome === "failure" || outcome === "denied";
  return (
    <Badge variant="outline" className={`text-3xs ${failed ? "border-destructive/20 bg-destructive/12 text-destructive" : "border-success/20 bg-success/12 text-success"}`}>
      {outcome}
    </Badge>
  );
}

function EventDetails({ event }: { event: SecurityEvent }) {
  return (
    <dl className="grid gap-2 text-2xs sm:grid-cols-2">
      <div><dt className="inline text-muted-foreground">Source IP: </dt><dd className="inline font-mono">{event.source_ip || "—"}</dd></div>
      <div><dt className="inline text-muted-foreground">Target: </dt><dd className="inline break-all font-mono">{event.target_type ? `${event.target_type}:${event.target_id}` : "—"}</dd></div>
      <div className="sm:col-span-2"><dt className="inline text-muted-foreground">User-Agent: </dt><dd className="inline break-all font-mono">{event.user_agent || "—"}</dd></div>
      <div className="sm:col-span-2"><dt className="inline text-muted-foreground">Detail: </dt><dd className="inline break-all font-mono">{event.detail || "—"}</dd></div>
    </dl>
  );
}

function EventRow({ event }: { event: SecurityEvent }) {
  const [open, setOpen] = useState(false);
  const hasDetail = Boolean(event.detail && event.detail !== "{}") || Boolean(event.source_ip || event.user_agent);
  return (
    <>
      <TableRow>
        <TableCell className="whitespace-nowrap text-2xs tabular-nums text-muted-foreground">{formatTimestamp(event.timestamp)}</TableCell>
        <TableCell><code className="rounded bg-surface-raised px-1.5 py-1 text-3xs">{event.event_type}</code></TableCell>
        <TableCell>{severityBadge(event.severity)}</TableCell>
        <TableCell className="max-w-[200px] truncate text-xs">{event.actor_email || event.actor_id || "—"}</TableCell>
        <TableCell className="max-w-[220px] truncate font-mono text-3xs text-muted-foreground">{event.target_type ? `${event.target_type}:${event.target_id}` : "—"}</TableCell>
        <TableCell>{outcomeBadge(event.outcome)}</TableCell>
        <TableCell>
          {hasDetail && (
            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setOpen(!open)} aria-expanded={open} aria-label={`${open ? "Hide" : "Show"} event details`}>
              {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
            </Button>
          )}
        </TableCell>
      </TableRow>
      {open && hasDetail && <TableRow><TableCell colSpan={7} className="bg-surface-raised/60 px-6 py-4"><EventDetails event={event} /></TableCell></TableRow>}
    </>
  );
}

function EventCard({ event }: { event: SecurityEvent }) {
  const [open, setOpen] = useState(false);
  const hasDetail = Boolean(event.detail && event.detail !== "{}") || Boolean(event.source_ip || event.user_agent);
  return (
    <article className="p-4">
      <button type="button" className="w-full text-left" onClick={() => hasDetail && setOpen(!open)} aria-expanded={hasDetail ? open : undefined}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0"><code className="block truncate text-2xs font-medium">{event.event_type}</code><p className="mt-1 truncate text-xs text-muted-foreground">{event.actor_email || event.actor_id || "Unknown principal"}</p></div>
          {hasDetail && (open ? <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" /> : <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />)}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">{severityBadge(event.severity)}{outcomeBadge(event.outcome)}<span className="ml-auto text-3xs tabular-nums text-muted-foreground">{formatTimestamp(event.timestamp)}</span></div>
      </button>
      {open && <div className="mt-4 border-t border-border pt-4"><EventDetails event={event} /></div>}
    </article>
  );
}

export default function SecurityEventsPage() {
  const [eventType, setEventType] = useState("all");
  const [severity, setSeverity] = useState("all");
  const [actorEmail, setActorEmail] = useState("");
  const [page, setPage] = useState(0);
  const filters = useMemo(() => {
    const value: Record<string, string> = { limit: String(PAGE_SIZE), offset: String(page * PAGE_SIZE) };
    if (eventType !== "all") value.event_type = eventType;
    if (severity !== "all") value.severity = severity;
    if (actorEmail.trim()) value.actor_email = actorEmail.trim();
    return value;
  }, [eventType, severity, actorEmail, page]);

  const { data, isLoading, isError, error, refetch } = useSecurityEvents(filters);
  const events = data?.events ?? [];
  const critical = events.filter((event) => event.severity === "critical").length;
  const warnings = events.filter((event) => event.severity === "warning").length;
  const failures = events.filter((event) => event.outcome === "failure" || event.outcome === "denied").length;

  return (
    <>
      <PageHeader title="Security Events" breadcrumbs={[{ label: "Administration" }, { label: "Security" }]} />
      <div className="page-body mx-auto w-full">
        <div className="space-y-5">
          {data && (
            <AdminMetricStrip>
              <AdminMetric label="Total events" value={data.total ?? events.length} detail="Matching current filters" icon={<ShieldAlert />} />
              <AdminMetric label="Visible events" value={events.length} detail={`Page ${page + 1}`} icon={<ShieldCheck />} />
              <AdminMetric label="Critical shown" value={critical} detail="Requires attention" icon={<CircleAlert />} tone={critical ? "destructive" : "default"} />
              <AdminMetric label="Warnings shown" value={warnings} detail={`${failures} failed outcomes`} icon={<AlertTriangle />} tone={warnings || failures ? "warning" : "default"} />
            </AdminMetricStrip>
          )}

          <AdminPanel title="Event stream" subtitle="Latest security-relevant activity" contentClassName="p-4">
            <div className="grid gap-3 md:grid-cols-[minmax(220px,1fr)_160px_minmax(240px,1fr)]">
              <PickerSelect value={eventType} onValueChange={(value) => { setEventType(value); setPage(0); }} placeholder="Event type" inputClassName="h-10 text-xs" options={EVENT_TYPES.map((value) => ({ value, label: value === "all" ? "All event types" : value }))} />
              <PickerSelect value={severity} onValueChange={(value) => { setSeverity(value); setPage(0); }} placeholder="Severity" inputClassName="h-10 text-xs" options={SEVERITIES.map((value) => ({ value, label: value === "all" ? "All severities" : value }))} />
              <UserSearchInput placeholder="Actor name, username, or email" value={actorEmail} onValueChange={(value) => { setActorEmail(value); setPage(0); }} onSelect={(user) => { setActorEmail(user.email); setPage(0); }} className="h-10 w-full text-xs" />
            </div>
          </AdminPanel>

          {isLoading ? (
            <TableSkeleton rows={10} cols={7} />
          ) : isError ? (
            <ErrorState message={(error as Error)?.message} onRetry={() => refetch()} />
          ) : !events.length ? (
            <EmptyState icon={ShieldAlert} title="No security events" description="Security events will appear here when they occur." />
          ) : (
            <AdminPanel>
              <div className="hidden overflow-x-auto lg:block">
                <Table>
                  <TableHeader><TableRow><TableHead>Timestamp</TableHead><TableHead>Event type</TableHead><TableHead>Severity</TableHead><TableHead>Actor</TableHead><TableHead>Target</TableHead><TableHead>Outcome</TableHead><TableHead><span className="sr-only">Details</span></TableHead></TableRow></TableHeader>
                  <TableBody>{events.map((event) => <EventRow key={event.event_id} event={event} />)}</TableBody>
                </Table>
              </div>
              <div className="divide-y divide-border lg:hidden">{events.map((event) => <EventCard key={event.event_id} event={event} />)}</div>
              <AdminTableFooter>
                <p className="text-2xs text-muted-foreground">Showing {page * PAGE_SIZE + 1}–{page * PAGE_SIZE + events.length}{data?.total ? ` of ${data.total}` : ""}</p>
                <div className="flex gap-2"><Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</Button><Button variant="outline" size="sm" disabled={events.length < PAGE_SIZE} onClick={() => setPage(page + 1)}>Next</Button></div>
              </AdminTableFooter>
            </AdminPanel>
          )}
        </div>
      </div>
    </>
  );
}
