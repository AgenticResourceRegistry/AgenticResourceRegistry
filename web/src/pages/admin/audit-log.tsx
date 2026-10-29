// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useMemo, useRef, useState } from "react";
import { useLocation, useSearch } from "@tanstack/react-router";
import { AlertTriangle, ChevronDown, ChevronRight, Clock, Download, ScrollText, Shield } from "lucide-react";
import { toast } from "sonner";

import { PageHeader } from "@/components/layouts/page-header";
import { EmptyState } from "@/components/shared/empty-state";
import { ErrorState } from "@/components/shared/error-state";
import { TableSkeleton } from "@/components/shared/skeleton-layouts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { SearchField } from "@/components/ui/search-field";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAuditLog } from "@/hooks/use-api";
import { admin } from "@/lib/api";
import type { AuditLogEntry } from "@/lib/types";
import { AdminMetric, AdminMetricStrip, AdminPanel, AdminTableFooter } from "./components/admin-surface";

const PAGE_SIZE = 50;
const FILTER_HINTS = ["actor:", "action:", "outcome:", "sensitivity:", "source:", "type:", "ip:"];

function parseSearchQuery(query: string): Record<string, string> {
  const params: Record<string, string> = {};
  const tokens = query.match(/(\w+):(?:"([^"]*)"|([^\s]*))/g);
  for (const token of tokens ?? []) {
    const colon = token.indexOf(":");
    const key = token.slice(0, colon).toLowerCase();
    let value = token.slice(colon + 1);
    if (value.startsWith('"') && value.endsWith('"')) value = value.slice(1, -1);
    const apiKey = {
      actor: "actor",
      action: "action",
      type: "resource_type",
      resource: "resource_type",
      outcome: "outcome",
      sensitivity: "sensitivity",
      source: "source",
      method: "http_method",
      ip: "ip_address",
    }[key];
    if (apiKey) params[apiKey] = value;
  }
  return params;
}

function formatTimestamp(timestamp: string) {
  const normalized = /(?:Z|[+-]\d\d:\d\d)$/.test(timestamp) ? timestamp : `${timestamp.replace(" ", "T")}Z`;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return timestamp;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function outcomeBadge(outcome: string) {
  const style = outcome === "success"
    ? "border-success/20 bg-success/12 text-success"
    : outcome === "denied" || outcome === "error"
      ? "border-destructive/20 bg-destructive/12 text-destructive"
      : "border-border bg-surface-raised text-muted-foreground";
  return <Badge variant="outline" className={`text-3xs ${style}`}>{outcome || "unknown"}</Badge>;
}

function sensitivityBadge(level: string) {
  const style = level === "phi_adjacent"
    ? "border-destructive/20 bg-destructive/12 text-destructive"
    : level === "admin"
      ? "border-warning/20 bg-warning/12 text-warning"
      : level === "high"
        ? "border-info/20 bg-info/12 text-info"
        : "border-border bg-surface-raised text-muted-foreground";
  return <Badge variant="outline" className={`text-3xs ${style}`}>{level === "phi_adjacent" ? "PHI" : level}</Badge>;
}

function AuditDetails({ entry }: { entry: AuditLogEntry }) {
  const details = [
    ["Event ID", entry.event_id],
    ["Request ID", entry.request_id || "—"],
    ["HTTP", `${entry.http_method} ${entry.http_path}`],
    ["Actor ID", entry.actor_id || "—"],
    ["Role", entry.actor_role || "—"],
    ["Resource", `${entry.resource_type}${entry.resource_id ? ` (${entry.resource_id})` : ""}`],
    ["Source", entry.source || "—"],
    ["User-Agent", entry.user_agent || "—"],
  ];

  return (
    <div className="grid gap-x-8 gap-y-2 text-2xs sm:grid-cols-2">
      {details.map(([label, value]) => (
        <div key={label} className={label === "User-Agent" ? "sm:col-span-2" : undefined}>
          <span className="text-muted-foreground">{label}: </span>
          <span className="break-all font-mono">{value}</span>
        </div>
      ))}
      {entry.detail && <div className="sm:col-span-2"><span className="text-muted-foreground">Detail: </span>{entry.detail}</div>}
      <div className="border-t border-border pt-2 sm:col-span-2">
        <span className="text-muted-foreground">Chain hash: </span>
        <span className="break-all font-mono text-3xs">{entry.chain_hash || "—"}</span>
      </div>
    </div>
  );
}

function DetailRow({ entry }: { entry: AuditLogEntry }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <TableRow>
        <TableCell className="whitespace-nowrap font-mono text-3xs text-muted-foreground">{formatTimestamp(entry.timestamp)}</TableCell>
        <TableCell className="max-w-[160px] truncate text-xs">{entry.actor_email || <span className="italic text-muted-foreground">anonymous</span>}</TableCell>
        <TableCell><code className="rounded bg-surface-raised px-1.5 py-1 text-3xs">{entry.action}</code></TableCell>
        <TableCell>{outcomeBadge(entry.outcome)}</TableCell>
        <TableCell>{sensitivityBadge(entry.sensitivity)}</TableCell>
        <TableCell className="font-mono text-3xs text-muted-foreground">{entry.status_code}</TableCell>
        <TableCell className="font-mono text-3xs text-muted-foreground">{entry.duration_ms > 0 ? `${entry.duration_ms.toFixed(0)}ms` : "—"}</TableCell>
        <TableCell className="font-mono text-3xs text-muted-foreground">{entry.ip_address || "—"}</TableCell>
        <TableCell>
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setOpen(!open)} aria-expanded={open} aria-label={`${open ? "Hide" : "Show"} details for ${entry.action}`}>
            {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          </Button>
        </TableCell>
      </TableRow>
      {open && (
        <TableRow>
          <TableCell colSpan={9} className="bg-surface-raised/60 px-6 py-4"><AuditDetails entry={entry} /></TableCell>
        </TableRow>
      )}
    </>
  );
}

function AuditCard({ entry }: { entry: AuditLogEntry }) {
  const [open, setOpen] = useState(false);
  return (
    <article className="p-4">
      <button type="button" className="w-full text-left" onClick={() => setOpen(!open)} aria-expanded={open}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <code className="block truncate text-2xs font-medium">{entry.action}</code>
            <p className="mt-1 truncate text-xs text-muted-foreground">{entry.actor_email || "Anonymous"}</p>
          </div>
          {open ? <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" /> : <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {outcomeBadge(entry.outcome)}
          {sensitivityBadge(entry.sensitivity)}
          <span className="ml-auto font-mono text-3xs text-muted-foreground">{formatTimestamp(entry.timestamp)}</span>
        </div>
      </button>
      {open && <div className="mt-4 border-t border-border pt-4"><AuditDetails entry={entry} /></div>}
    </article>
  );
}

export default function AuditLogPage() {
  const { search: searchParam } = useSearch({ from: "/_authed/_admin/audit-log" });
  const { pathname } = useLocation();
  const [searchQuery, setSearchQuery] = useState(searchParam ?? "");
  const [page, setPage] = useState(0);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  const updateURL = useCallback((value: string) => {
    const params = new URLSearchParams(window.location.search);
    if (value) params.set("search", value);
    else params.delete("search");
    const query = params.toString();
    window.history.replaceState(null, "", query ? `${pathname}?${query}` : pathname);
  }, [pathname]);

  const handleSearchChange = useCallback((value: string) => {
    setSearchQuery(value);
    setPage(0);
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => updateURL(value), 300);
  }, [updateURL]);

  const filters = useMemo(() => ({
    ...parseSearchQuery(searchQuery),
    limit: String(PAGE_SIZE),
    offset: String(page * PAGE_SIZE),
  }), [searchQuery, page]);

  const { data, isLoading, isError, error, refetch } = useAuditLog(filters);

  const handleExport = useCallback(async () => {
    try {
      const csv = await admin.auditLogExport(parseSearchQuery(searchQuery));
      const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `observal_audit-log_${new Date().toISOString().replace(/[-:]/g, "").slice(0, 15)}Z.csv`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch {
      toast.error("Failed to export audit log. Please try again.");
    }
  }, [searchQuery]);

  const denied = data?.filter((entry) => entry.outcome === "denied").length ?? 0;
  const sensitive = data?.filter((entry) => entry.sensitivity === "phi_adjacent").length ?? 0;

  return (
    <>
      <PageHeader title="Audit Log" breadcrumbs={[{ label: "Administration" }, { label: "Audit Log" }]} />
      <div className="page-body mx-auto w-full">
        <div className="space-y-5">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0 flex-1 sm:pr-3">
              <SearchField
                value={searchQuery}
                onValueChange={handleSearchChange}
                placeholder="actor:name action:login outcome:denied sensitivity:high"
                aria-label="Search audit events"
                mono
              />
            </div>
            <Button variant="outline" size="sm" onClick={handleExport} className="shrink-0">
              <Download className="h-3.5 w-3.5" />
              Export CSV
            </Button>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-1 text-3xs text-muted-foreground">Filter syntax</span>
            {FILTER_HINTS.map((hint) => (
              <button key={hint} className="rounded-md bg-surface-raised px-2 py-1 font-mono text-3xs text-muted-foreground transition-colors hover:text-foreground" onClick={() => handleSearchChange(`${searchQuery}${searchQuery && !searchQuery.endsWith(" ") ? " " : ""}${hint}`)}>
                {hint}
              </button>
            ))}
          </div>

          {data && data.length > 0 && (
            <AdminMetricStrip className="xl:grid-cols-3">
              <AdminMetric label="Events shown" value={data.length} detail={`Page ${page + 1}`} icon={<Clock />} />
              <AdminMetric label="Denied" value={denied} detail="Visible results" icon={<AlertTriangle />} tone={denied ? "destructive" : "default"} />
              <AdminMetric label="PHI-adjacent" value={sensitive} detail="Sensitive activity" icon={<Shield />} tone={sensitive ? "warning" : "default"} />
            </AdminMetricStrip>
          )}

          {isLoading ? (
            <TableSkeleton rows={10} cols={9} />
          ) : isError ? (
            <ErrorState message={(error as Error)?.message} onRetry={() => refetch()} />
          ) : !data?.length ? (
            <EmptyState icon={ScrollText} title="No audit events" description="Events will appear here as API requests are made." />
          ) : (
            <AdminPanel title="Recent audit events" subtitle="Times shown in your local timezone">
              <div className="hidden overflow-x-auto lg:block">
                <Table>
                  <TableHeader><TableRow>
                    <TableHead>Time</TableHead><TableHead>Actor</TableHead><TableHead>Action</TableHead><TableHead>Outcome</TableHead><TableHead>Sensitivity</TableHead><TableHead>Status</TableHead><TableHead>Latency</TableHead><TableHead>IP</TableHead><TableHead><span className="sr-only">Details</span></TableHead>
                  </TableRow></TableHeader>
                  <TableBody>{data.map((entry) => <DetailRow key={entry.event_id} entry={entry} />)}</TableBody>
                </Table>
              </div>
              <div className="divide-y divide-border lg:hidden">{data.map((entry) => <AuditCard key={entry.event_id} entry={entry} />)}</div>
              <AdminTableFooter>
                <p className="text-2xs text-muted-foreground">Page {page + 1} · {data.length} results</p>
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</Button>
                  <Button variant="outline" size="sm" disabled={data.length < PAGE_SIZE} onClick={() => setPage(page + 1)}>Next</Button>
                </div>
              </AdminTableFooter>
            </AdminPanel>
          )}
        </div>
      </div>
    </>
  );
}
