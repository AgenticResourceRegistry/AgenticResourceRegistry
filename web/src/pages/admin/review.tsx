// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback, useEffect, useMemo, useRef } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import { CheckCircle2, LayoutGrid, TableProperties, XCircle } from "lucide-react";
import { useSearch } from "@tanstack/react-router";
import {
  useDecideTeamVisibility,
  useReviewAgents,
  useReviewComponents,
  useReviewAction,
  useReviewSubscription,
  useTeamVisibilityRequests,
} from "@/hooks/use-api";
import { useAuthGuard } from "@/hooks/use-auth";
import type { ReviewItem, TeamVisibilityRequest } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EntityGlyph } from "@/components/registry/entity-glyph";
import { StatusBadge } from "@/components/registry/status-badge";
import { ForkedFromChip } from "@/components/registry/fork-provenance";
import { ForkDiffPanel } from "@/components/registry/fork-diff-panel";
import { Tabs, TabsContent, TabsList, TabsTrigger, tabsTriggerVariants } from "@/components/ui/tabs";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { PageHeader } from "@/components/layouts/page-header";
import { ErrorState } from "@/components/shared/error-state";
import { EmptyState } from "@/components/shared/empty-state";
import {
  ValidationDetails,
  ComponentReadinessBadge,
  ValidationCheckCell,
  componentBlockers,
} from "@/components/review/validation-badges";
import { ReviewDiffSheet } from "@/components/review/review-diff-sheet";
import { cn } from "@/lib/utils";

type ViewMode = "list" | "panels";

// ── Formatting helpers ──────────────────────────────────────────────

function ageParts(iso?: string): { short: string; phrase: string } | null {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return null;
  const mins = Math.round((Date.now() - t) / 60_000);
  if (mins < 1) return { short: "now", phrase: "just now" };
  if (mins < 60) return { short: `${mins}m`, phrase: `${mins}m ago` };
  const hours = Math.round(mins / 60);
  if (hours < 24) return { short: `${hours}h`, phrase: `${hours}h ago` };
  const days = Math.round(hours / 24);
  if (days === 1) return { short: "Yesterday", phrase: "yesterday" };
  if (days < 7) return { short: `${days}d`, phrase: `${days}d ago` };
  const date = new Date(t).toLocaleDateString();
  return { short: date, phrase: `on ${date}` };
}

/** Title-cased singular type, for the queue meta rail's third slot. */
function typeLabel(type?: string): string {
  if (!type) return "Item";
  const singular = type.endsWith("s") ? type.slice(0, -1) : type;
  if (singular === "mcp") return "MCP";
  return singular.charAt(0).toUpperCase() + singular.slice(1);
}

function qualifiedHandle(item: ReviewItem): string {
  const name = item.name ?? "unnamed";
  return item.owner ? `${item.owner}/${name}` : name;
}

/** Stable key readers for `useSelected`. */
const itemKey = (item: ReviewItem) => item.review_key ?? item.id;
const teamKey = (team: TeamVisibilityRequest) => team.team_id;

function plural(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

function readiness(item: ReviewItem): "ready" | "blocked" {
  const failed = (item.validation_results ?? []).some((v) => !v.passed);
  return item.components_ready === false || failed ? "blocked" : "ready";
}

type CheckTone = "pass" | "fail" | "neutral";
interface CheckCell {
  label: string;
  value: string;
  tone: CheckTone;
}

function reviewChecks(item: ReviewItem): CheckCell[] {
  const results = item.validation_results ?? [];
  const failed = results.filter((v) => !v.passed);

  const validation: CheckCell =
    failed.length > 0
      ? { label: "Validation", value: `${plural(failed.length, "check")} failed`, tone: "fail" }
      : results.length > 0 || item.mcp_validated
        ? { label: "Validation", value: "Schema passed", tone: "pass" }
        : { label: "Validation", value: "Not run", tone: "neutral" };

  const provenance: CheckCell = item.forked_from
    ? { label: "Provenance", value: item.forked_from.available ? "Upstream linked" : "Source unavailable", tone: "neutral" }
    : item.git_url
      ? { label: "Provenance", value: "Source link provided", tone: "neutral" }
      : { label: "Provenance", value: "No source link", tone: "neutral" };

  const blockers = componentBlockers(item);
  const blockedCount = blockers.length || 1;
  const dependencies: CheckCell =
    item.components_ready === false
      ? { label: "Dependencies", value: `${plural(blockedCount, "component")} blocked`, tone: "fail" }
      : typeof item.component_count === "number"
        ? { label: "Dependencies", value: `${plural(item.component_count, "component")} ready`, tone: "pass" }
        : { label: "Dependencies", value: "No blockers", tone: "pass" };

  return [validation, provenance, dependencies];
}

function permissionDelta(item: ReviewItem): string {
  if (item.auto_approve?.length) return item.auto_approve.join(", ");
  if (item.tool_filter?.length) return item.tool_filter.join(", ");
  if (item.scope) return item.scope;
  return "No new permissions";
}

function reviewerContext(item: ReviewItem): string | null {
  if (item.changelog?.trim()) return item.changelog.trim();
  const blockers = componentBlockers(item);
  if (item.components_ready === false && blockers.length > 0) {
    return `Waiting on ${blockers
      .map((b) => `${b.name} (${b.component_type}, ${b.status})`)
      .join(", ")}.`;
  }
  return null;
}

// ── Shared panel pieces ─────────────────────────────────────────────

function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        "font-[family-name:var(--font-mono)] text-2xs text-muted-foreground",
        className,
      )}
    >
      {children}
    </span>
  );
}

function PanelTitle({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("text-base font-medium", className)}>{children}</div>;
}

function ReviewChange({
  label,
  children,
  mono,
}: {
  label: string;
  children: ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="border-t border-border py-3">
      <strong className="block text-2xs font-medium">{label}</strong>
      <p
        className={cn(
          "mt-[3px] text-3xs text-muted-foreground",
          mono && "font-[family-name:var(--font-mono)] break-all",
        )}
      >
        {children}
      </p>
    </div>
  );
}

function DetailHead({
  glyphType,
  title,
  version,
  meta,
  status,
}: {
  glyphType: string;
  title: string;
  version?: string;
  meta: string;
  status: string;
}) {
  return (
    <div className="flex items-start gap-[11px] border-b border-border p-5">
      <EntityGlyph type={glyphType} size="md" className="rounded-md" />
      <div className="min-w-0">
        <h2 className="text-lg font-medium leading-tight tracking-[-0.02em]">
          {title}
          {version && (
            <>
              {" "}
              <Mono>v{version}</Mono>
            </>
          )}
        </h2>
        <p className="mt-[3px] text-3xs text-muted-foreground">{meta}</p>
      </div>
      <StatusBadge status={status} className="ml-auto shrink-0" />
    </div>
  );
}

function ReviewNoteAndActions({
  note,
  onNoteChange,
  noteId,
  children,
}: {
  note: string;
  onNoteChange: (value: string) => void;
  noteId: string;
  children: ReactNode;
}) {
  return (
    <>
      <div className="mt-3.5 grid gap-[7px]">
        <Label htmlFor={noteId} className="text-2xs font-medium text-muted-foreground">
          Review note <Mono>optional when approving</Mono>
        </Label>
        <Textarea
          id={noteId}
          value={note}
          maxLength={500}
          onChange={(event) => onNoteChange(event.target.value)}
          placeholder="Explain the decision or what needs to change…"
          className="focus-visible:ring-2 focus-visible:ring-ring"
        />
      </div>
      <div className="mt-4 flex flex-wrap items-center justify-end gap-2.5 border-t border-border pt-4">
        {children}
      </div>
    </>
  );
}

// ── Queue column ────────────────────────────────────────────────────

function ReviewQueuePanel({
  item,
  selected,
  dense,
  onSelect,
}: {
  item: ReviewItem;
  selected: boolean;
  dense: boolean;
  onSelect: (id: string) => void;
}) {
  const age = ageParts(item.submitted_at ?? item.created_at);

  return (
    <Card asChild interactive selected={selected}>
      <button
        type="button"
        data-review-item={itemKey(item)}
        onClick={() => onSelect(itemKey(item))}
        className={cn("block", dense ? "p-2.5" : "p-3.5")}
      >
        {dense ? (
          <span className="flex items-center gap-2.5">
            <EntityGlyph type={item.type} size="sm" />
            <span className="min-w-0 flex-1 truncate text-2xs font-medium">
              {item.name ?? "Unnamed"}
            </span>
            {item.version && <Mono className="shrink-0">v{item.version}</Mono>}
            <StatusBadge status={readiness(item)} className="shrink-0" />
          </span>
        ) : (
          <>
            {/* .review-panel-top */}
            <span className="flex items-center justify-between gap-2.5">
              <EntityGlyph type={item.type} size="md" labelled={false} className="rounded-md" />
              <StatusBadge status={readiness(item)} />
            </span>
            <h3 className="mb-[3px] mt-2.5 text-sm font-medium">
              {item.name ?? "Unnamed"}
              {item.version && (
                <>
                  {" "}
                  <Mono>v{item.version}</Mono>
                </>
              )}
            </h3>
            <p className="m-0 truncate text-3xs leading-[1.45] text-muted-foreground">
              {qualifiedHandle(item)}
            </p>
            {/* .review-panel-meta */}
            <span className="mt-[11px] flex items-center gap-[9px] border-t border-border pt-2.5 text-3xs text-muted-foreground">
              {item.submitted_by && <span className="truncate">{item.submitted_by}</span>}
              {age && <span className="shrink-0">{age.short}</span>}
              <span className="shrink-0">{typeLabel(item.type)}</span>
            </span>
          </>
        )}
      </button>
    </Card>
  );
}

function QueueColumn({ children, label }: { children: ReactNode; label: string }) {
  const ref = useRef<HTMLElement>(null);

  const onKeyDown = useCallback((event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    const panels = Array.from(
      ref.current?.querySelectorAll<HTMLButtonElement>("[data-review-item]") ?? [],
    );
    const index = panels.indexOf(document.activeElement as HTMLButtonElement);
    if (index === -1) return;
    event.preventDefault();
    const next =
      event.key === "ArrowDown"
        ? panels[Math.min(index + 1, panels.length - 1)]
        : panels[Math.max(index - 1, 0)];
    next?.focus();
  }, []);

  return (
    <aside
      ref={ref}
      aria-label={label}
      onKeyDown={onKeyDown}
      className="grid content-start gap-1.5 min-[900px]:sticky min-[900px]:top-[70px]"
    >
      {children}
    </aside>
  );
}

function ReviewWorkspace({ detail, queue }: { detail: ReactNode; queue: ReactNode }) {
  return (
    <div className="grid grid-cols-1 items-start gap-[18px] min-[900px]:grid-cols-[minmax(0,1.35fr)_minmax(320px,0.72fr)]">
      {detail}
      {queue}
    </div>
  );
}

function DetailPanel({ children }: { children: ReactNode }) {
  return (
    <Card className="min-h-[510px]" aria-live="polite" aria-atomic="false">
      {children}
    </Card>
  );
}

// ── Detail panels ───────────────────────────────────────────────────

function ReviewDetailPanel({
  item,
  onViewDiff,
}: {
  item: ReviewItem;
  onViewDiff: (item: ReviewItem) => void;
}) {
  const age = ageParts(item.submitted_at ?? item.created_at);
  const checks = reviewChecks(item);
  const context = reviewerContext(item);
  const summary =
    item.description ?? "No description was supplied with this submission.";
  const meta = [
    qualifiedHandle(item),
    item.submitted_by ? `submitted by ${item.submitted_by}` : null,
    age?.phrase ?? null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <>
      <DetailHead
        glyphType={item.type ?? "agent"}
        title={item.name ?? "Unnamed"}
        version={item.version}
        meta={meta}
        status={item.status ?? "pending"}
      />
      <div className="p-5">
        <PanelTitle>Release summary</PanelTitle>
        <p className="mt-1.5 text-sm text-muted-foreground">{summary}</p>
        {item.forked_from && <div className="mt-2"><ForkedFromChip provenance={item.forked_from} /></div>}
        {item.forked_from?.available && item.version && item.type && (
          <div className="mt-3"><ForkDiffPanel type={(item.type === "agent" ? "agents" : `${item.type}s`) as import("@/lib/api").RegistryType} id={item.id} version={item.version} /></div>
        )}

        {/* .validation-grid — a hairline strip of three raised cells */}
        <div className="my-4 grid grid-cols-1 gap-px overflow-hidden rounded-xl bg-border sm:grid-cols-3">
          {checks.map((check) => (
            <ValidationCheckCell
              key={check.label}
              label={check.label}
              value={check.value}
              tone={check.tone}
            />
          ))}
        </div>


        <div className="space-y-1.5 empty:hidden [&:not(:empty)]:mb-4">
          <ValidationDetails results={item.validation_results} />
          <ComponentReadinessBadge item={item} />
        </div>

        <PanelTitle className="mt-4">Changes in this release</PanelTitle>
        <ReviewChange label="Prompt and behavior">{summary}</ReviewChange>
        <ReviewChange label="Permission delta" mono>
          {permissionDelta(item)}
        </ReviewChange>
        {context && <ReviewChange label="Reviewer context">{context}</ReviewChange>}

        <div className="mt-5 flex justify-end border-t border-border pt-4">
          <Button variant="ghost" onClick={() => onViewDiff(item)}>
            View full diff
          </Button>
        </div>
      </div>
    </>
  );
}

/**
 * The teamspace equivalent. Teamspaces carry no schema, provenance or
 * dependency signal, so the validation strip is omitted rather than filled
 * with invented cells; everything else keeps the same panel vocabulary.
 */
function TeamspaceDetailPanel({
  team,
  pending,
  onApprove,
  onReject,
}: {
  team: TeamVisibilityRequest;
  pending: boolean;
  onApprove: (team: TeamVisibilityRequest) => void;
  onReject: (team: TeamVisibilityRequest, note: string) => void;
}) {
  const [note, setNote] = useState("");
  const age = ageParts(team.requested_at);
  const requester = team.requested_by_username ? `@${team.requested_by_username}` : "a user";
  const meta = [team.handle, `requested by ${requester}`, age?.phrase ?? null]
    .filter(Boolean)
    .join(" · ");

  return (
    <>
      <DetailHead glyphType="teamspace" title={team.name} meta={meta} status="pending" />
      <div className="p-5">
        <PanelTitle>Visibility request</PanelTitle>
        <p className="mt-1.5 text-sm text-muted-foreground">
          {team.description ??
            "This teamspace has asked to become publicly discoverable in the registry."}
        </p>

        <PanelTitle className="mt-4">Details of this request</PanelTitle>
        <ReviewChange label="Requested visibility">
          Public — anyone can discover this teamspace and the catalogue it publishes.
        </ReviewChange>
        <ReviewChange label="Teamspace handle" mono>
          {team.handle}
        </ReviewChange>
        <ReviewChange label="Requested by">
          {requester}
          {age ? ` · ${age.phrase}` : ""}
        </ReviewChange>

        <ReviewNoteAndActions
          note={note}
          onNoteChange={setNote}
          noteId={`teamspace-note-${team.team_id}`}
        >
          <Button
            variant="ghost"
            className="text-destructive hover:text-destructive"
            disabled={pending}
            onClick={() => onReject(team, note.trim())}
          >
            <XCircle aria-hidden="true" />
            Reject
          </Button>
          <Button disabled={pending} onClick={() => onApprove(team)}>
            Approve teamspace
          </Button>
        </ReviewNoteAndActions>
      </div>
    </>
  );
}

// ── Loading shell ───────────────────────────────────────────────────

/** The workspace skeleton keeps the two-column shape while data lands. */
function WorkspaceSkeleton({ view }: { view: ViewMode }) {
  return (
    <ReviewWorkspace
      detail={
        <DetailPanel>
          <div className="flex items-start gap-[11px] border-b border-border p-5">
            <Skeleton className="h-7 w-7 rounded-lg" />
            <div className="flex-1 space-y-2">
              <Skeleton className="h-4 w-52" />
              <Skeleton className="h-2.5 w-72" />
            </div>
            <Skeleton className="h-5 w-20 rounded-full" />
          </div>
          <div className="space-y-3 p-5">
            <Skeleton className="h-3.5 w-36" />
            <Skeleton className="h-3 w-full max-w-md" />
            <Skeleton className="h-[72px] w-full rounded-xl" />
            <Skeleton className="h-3 w-full" />
            <Skeleton className="h-3 w-4/5" />
            <Skeleton className="h-20 w-full rounded-[11px]" />
          </div>
        </DetailPanel>
      }
      queue={
        <QueueColumn label="Pending submissions">
          {[0, 1, 2].map((i) => (
            <Card key={i} className={view === "panels" ? "p-3.5" : "p-2.5"}>
              {view === "panels" ? (
                <div className="space-y-2.5">
                  <div className="flex items-center justify-between gap-2.5">
                    <Skeleton className="h-7 w-7 rounded-lg" />
                    <Skeleton className="h-5 w-16 rounded-full" />
                  </div>
                  <Skeleton className="h-3.5 w-36" />
                  <Skeleton className="h-2.5 w-28" />
                  <Skeleton className="h-2.5 w-full" />
                </div>
              ) : (
                <div className="flex items-center gap-2.5">
                  <Skeleton className="h-6 w-6 rounded-md" />
                  <Skeleton className="h-3 flex-1" />
                </div>
              )}
            </Card>
          ))}
        </QueueColumn>
      }
    />
  );
}

function ViewToggle({ view, onChange }: { view: ViewMode; onChange: (view: ViewMode) => void }) {
  const options = [
    { value: "list" as const, label: "List", Icon: TableProperties },
    { value: "panels" as const, label: "Panels", Icon: LayoutGrid },
  ];
  return (
    <div className="flex items-center gap-1" role="group" aria-label="Queue density">
      {options.map(({ value, label, Icon }) => (
        <button
          key={value}
          type="button"
          aria-pressed={view === value}
          onClick={() => onChange(value)}
          className={cn(
            tabsTriggerVariants({ variant: "rail" }),
            view === value && "bg-surface-raised text-foreground",
          )}
        >
          <Icon className="h-3.5 w-3.5" aria-hidden="true" />
          {label}
        </button>
      ))}
    </div>
  );
}

function tabLabel(name: string, loading: boolean, count: number): string {
  return loading ? name : `${name} · ${count}`;
}

/**
 * Keeps a selection pinned to an item that still exists. When the list
 * identity changes — a refetch, an approval, a tab switch — the selection
 * falls back to the first item rather than emptying the detail panel.
 */
function useSelected<T>(items: T[], keyOf: (item: T) => string) {
  const [id, setId] = useState<string | null>(null);
  const selected = useMemo(
    () => items.find((item) => keyOf(item) === id) ?? items[0] ?? null,
    // `keyOf` is a stable module-level reader in every call site.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [items, id],
  );
  return [selected, setId] as const;
}

/**
 * The agent queue, preserving bundle grouping: agents submitted together stay
 * together under their bundle name instead of being scattered through the
 * stack.
 */
function AgentQueuePanels({
  items,
  selectedId,
  view,
  onSelect,
}: {
  items: ReviewItem[];
  selectedId: string | null;
  view: ViewMode;
  onSelect: (id: string) => void;
}) {
  const grouped = useMemo(() => {
    const bundles = new Map<string, { name: string; items: ReviewItem[] }>();
    const ungrouped: ReviewItem[] = [];
    for (const item of items) {
      if (item.bundle_id && item.bundle_name) {
        const existing = bundles.get(item.bundle_id);
        if (existing) {
          existing.items.push(item);
        } else {
          bundles.set(item.bundle_id, { name: item.bundle_name, items: [item] });
        }
      } else {
        ungrouped.push(item);
      }
    }
    return { bundles: Array.from(bundles.values()), ungrouped };
  }, [items]);

  const panels = (list: ReviewItem[]) =>
    list.map((item) => (
      <ReviewQueuePanel
        key={itemKey(item)}
        item={item}
        selected={itemKey(item) === selectedId}
        dense={view === "list"}
        onSelect={onSelect}
      />
    ));

  if (grouped.bundles.length === 0) return <>{panels(items)}</>;

  return (
    <>
      {grouped.bundles.map((bundle) => (
        <div key={bundle.name} className="grid gap-1.5">
          <h3 className="px-1 pt-1.5 text-4xs font-medium uppercase tracking-[0.05em] text-muted-foreground">
            Bundle · {bundle.name}
          </h3>
          {panels(bundle.items)}
        </div>
      ))}
      {grouped.ungrouped.length > 0 && (
        <div className="grid gap-1.5">
          <h3 className="px-1 pt-1.5 text-4xs font-medium uppercase tracking-[0.05em] text-muted-foreground">
            Standalone agents
          </h3>
          {panels(grouped.ungrouped)}
        </div>
      )}
    </>
  );
}

export default function ReviewPage() {
  useAuthGuard();
  useReviewSubscription();
  const { data: agents, isLoading: agentsLoading, isError: agentsError, error: agentsErr, refetch: refetchAgents } = useReviewAgents();
  const { data: components, isLoading: componentsLoading, isError: componentsError, error: componentsErr, refetch: refetchComponents } = useReviewComponents();
  const { data: teamspaces, isLoading: teamspacesLoading, isError: teamspacesError, error: teamspacesErr, refetch: refetchTeamspaces } = useTeamVisibilityRequests();
  const reviewAction = useReviewAction();
  const teamspaceAction = useDecideTeamVisibility();
  const [view, setView] = useState<ViewMode>("panels");
  // ?tab= lets an inbox item open the tab that actually holds the submission it
  // names. Absent, the queue opens on agents as before.
  const { tab: tabFromUrl } = useSearch({ from: "/_authed/_admin/review" });
  const [activeTab, setActiveTab] = useState<string>(tabFromUrl ?? "agents");
  // useState only reads the initial value, so a ?tab= change while this page
  // is already mounted (an inbox link clicked from the sidebar) must be
  // applied explicitly or the link silently does nothing.
  useEffect(() => {
    if (tabFromUrl) setActiveTab(tabFromUrl);
  }, [tabFromUrl]);
  const [diffItem, setDiffItem] = useState<ReviewItem | null>(null);
  const [nestedDiffItem, setNestedDiffItem] = useState<ReviewItem | null>(null);
  const [rejectTeamspace, setRejectTeamspace] = useState<TeamVisibilityRequest | null>(null);
  const [teamspaceReason, setTeamspaceReason] = useState("");

  const agentList = useMemo(() => agents ?? [], [agents]);
  const componentList = useMemo(() => components ?? [], [components]);
  const teamspaceList = useMemo(() => teamspaces ?? [], [teamspaces]);

  const agentCount = agentList.length;
  const componentCount = componentList.length;
  const teamspaceCount = teamspaceList.length;

  const [selectedAgent, setSelectedAgentId] = useSelected(agentList, itemKey);
  const [selectedComponent, setSelectedComponentId] = useSelected(componentList, itemKey);
  const [selectedTeamspace, setSelectedTeamspaceId] = useSelected(teamspaceList, teamKey);

  const handleApprove = useCallback(
    (id: string, type?: string, category?: string) => reviewAction.mutate({ id, type, action: "approve", category }),
    [reviewAction],
  );

  const handleReject = useCallback(
    (id: string, reason: string, type?: string) => reviewAction.mutate({ id, type, action: "reject", reason }),
    [reviewAction],
  );

  const handleViewDiff = useCallback((item: ReviewItem) => {
    setDiffItem(item);
  }, []);

  const handleOpenComponentReview = useCallback(
    async (id: string, type: string) => {
      const singularType = type.replace(/s$/, "");
      let list = components ?? [];
      // If not found yet, refetch first (component may have just been submitted)
      if (!list.find((c) => c.id === id)) {
        const result = await refetchComponents();
        list = result.data ?? list;
      }
      const found = list.find((c) => c.id === id || (c.type === singularType && c.id === id));
      if (found) setNestedDiffItem(found);
    },
    [components, refetchComponents],
  );

  const handleApproveWithRefetch = useCallback(
    (id: string, type?: string) => {
      reviewAction.mutate({ id, type, action: "approve" }, {
        onSuccess: async () => {
          setNestedDiffItem(null);
          const [freshAgents] = await Promise.all([refetchAgents(), refetchComponents()]);
          // Update diffItem in-place with fresh blocking_components so the sheet reflects the change
          setDiffItem((prev) => {
            if (!prev) return prev;
            const updated = (freshAgents.data ?? []).find((a) => a.id === prev.id);
            return updated ?? prev;
          });
        },
      });
    },
    [reviewAction, refetchAgents, refetchComponents],
  );

  const handleTeamspaceReject = useCallback(
    (team: TeamVisibilityRequest, note: string) => {
      // The 500-character reason dialog stays the confirmation step; the inline
      // note seeds it so nothing the reviewer already typed is thrown away.
      setTeamspaceReason(note);
      setRejectTeamspace(team);
    },
    [],
  );

  /** One tab's body: loading shell, error, empty, or the workspace. */
  const renderTab = ({
    loading,
    error,
    errorMessage,
    onRetry,
    count,
    emptyTitle,
    emptyDescription,
    detail,
    queue,
    queueLabel,
  }: {
    loading: boolean;
    error: boolean;
    errorMessage?: string;
    onRetry: () => void;
    count: number;
    emptyTitle: string;
    emptyDescription: string;
    detail: ReactNode;
    queue: ReactNode;
    queueLabel: string;
  }) => {
    if (loading) return <WorkspaceSkeleton view={view} />;
    if (error) return <ErrorState message={errorMessage} onRetry={onRetry} />;
    if (count === 0) {
      return (
        <EmptyState icon={CheckCircle2} title={emptyTitle} description={emptyDescription} />
      );
    }
    return (
      <ReviewWorkspace
        detail={<DetailPanel>{detail}</DetailPanel>}
        queue={<QueueColumn label={queueLabel}>{queue}</QueueColumn>}
      />
    );
  };

  return (
    <>

      <PageHeader
        title="Review Queue"
        breadcrumbs={[
          { label: "Dashboard", href: "/dashboard" },
          { label: "Review" },
        ]}
      />
      <div className="page-body mx-auto w-full">
        <Tabs value={activeTab} onValueChange={setActiveTab}>

          <div className="mb-3.5 flex flex-wrap items-center gap-1 border-b border-border pb-2">
            <TabsList variant="rail" className="w-auto flex-none border-b-0 p-0 pb-0">
              <TabsTrigger variant="rail" value="agents">
                {tabLabel("Agents", agentsLoading, agentCount)}
              </TabsTrigger>
              <TabsTrigger variant="rail" value="components">
                {tabLabel("Components", componentsLoading, componentCount)}
              </TabsTrigger>
              <TabsTrigger variant="rail" value="teamspaces">
                {tabLabel("Teamspaces", teamspacesLoading, teamspaceCount)}
              </TabsTrigger>
            </TabsList>
            <span className="flex-1" aria-hidden="true" />
            <ViewToggle view={view} onChange={setView} />
          </div>

          <TabsContent value="agents" className="mt-0">
            {renderTab({
              loading: agentsLoading,
              error: agentsError,
              errorMessage: agentsErr?.message,
              onRetry: () => refetchAgents(),
              count: agentCount,
              emptyTitle: "No agents to review",
              emptyDescription:
                "All agent submissions have been reviewed. New items will appear here when agents are submitted.",
              queueLabel: "Pending agents",
              detail: selectedAgent && (
                <ReviewDetailPanel
                  key={selectedAgent.id}
                  item={selectedAgent}
                  onViewDiff={handleViewDiff}
                />
              ),
              queue: (
                <AgentQueuePanels
                  items={agentList}
                  selectedId={selectedAgent?.id ?? null}
                  view={view}
                  onSelect={setSelectedAgentId}
                />
              ),
            })}
          </TabsContent>

          <TabsContent value="components" className="mt-0">
            {renderTab({
              loading: componentsLoading,
              error: componentsError,
              errorMessage: componentsErr?.message,
              onRetry: () => refetchComponents(),
              count: componentCount,
              emptyTitle: "No components to review",
              emptyDescription:
                "All component submissions have been reviewed. New items will appear here when components are submitted.",
              queueLabel: "Pending components",
              detail: selectedComponent && (
                <ReviewDetailPanel
                  key={itemKey(selectedComponent)}
                  item={selectedComponent}
                  onViewDiff={handleViewDiff}
                />
              ),
              queue: componentList.map((item) => (
                <ReviewQueuePanel
                  key={itemKey(item)}
                  item={item}
                  selected={itemKey(item) === (selectedComponent ? itemKey(selectedComponent) : null)}
                  dense={view === "list"}
                  onSelect={setSelectedComponentId}
                />
              )),
            })}
          </TabsContent>

          <TabsContent value="teamspaces" className="mt-0">
            {renderTab({
              loading: teamspacesLoading,
              error: teamspacesError,
              errorMessage: teamspacesErr?.message,
              onRetry: () => refetchTeamspaces(),
              count: teamspaceCount,
              emptyTitle: "No teamspaces to review",
              emptyDescription: "Public visibility requests will appear here.",
              queueLabel: "Pending teamspaces",
              detail: selectedTeamspace && (
                <TeamspaceDetailPanel
                  key={selectedTeamspace.team_id}
                  team={selectedTeamspace}
                  pending={teamspaceAction.isPending}
                  onApprove={(team) => teamspaceAction.mutate({ id: team.team_id, approve: true })}
                  onReject={handleTeamspaceReject}
                />
              ),
              queue: teamspaceList.map((team) => {
                const selected = team.team_id === selectedTeamspace?.team_id;
                const age = ageParts(team.requested_at);
                return (
                  <Card key={team.team_id} asChild interactive selected={selected}>
                    <button
                      type="button"
                      data-review-item={team.team_id}
                      data-testid={`teamspace-review-${team.team_id}`}
                      onClick={() => setSelectedTeamspaceId(team.team_id)}
                      className={cn("block", view === "list" ? "p-2.5" : "p-3.5")}
                    >
                      {view === "list" ? (
                        <span className="flex items-center gap-2.5">
                          <EntityGlyph type="teamspace" size="sm" />
                          <span className="min-w-0 flex-1 truncate text-2xs font-medium">
                            {team.name}
                          </span>
                          <StatusBadge status="pending" className="shrink-0" />
                        </span>
                      ) : (
                        <>
                          <span className="flex items-center justify-between gap-2.5">
                            <EntityGlyph type="teamspace" size="md" labelled={false} className="rounded-md" />
                            <StatusBadge status="pending" />
                          </span>
                          <h3 className="mb-[3px] mt-2.5 text-sm font-medium">{team.name}</h3>
                          <p className="m-0 truncate text-3xs leading-[1.45] text-muted-foreground">
                            {team.handle}
                          </p>
                          <span className="mt-[11px] flex items-center gap-[9px] border-t border-border pt-2.5 text-3xs text-muted-foreground">
                            <span className="truncate">
                              {team.requested_by_username ? `@${team.requested_by_username}` : "A user"}
                            </span>
                            {age && <span className="shrink-0">{age.short}</span>}
                            <span className="shrink-0">Teamspace</span>
                          </span>
                        </>
                      )}
                    </button>
                  </Card>
                );
              }),
            })}
          </TabsContent>
        </Tabs>
      </div>

      <Dialog
        open={!!rejectTeamspace}
        onOpenChange={(open) => {
          if (!open) {
            setRejectTeamspace(null);
            setTeamspaceReason("");
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reject public visibility</DialogTitle>
            <DialogDescription>
              The teamspace stays private, and its owner can submit another request after making changes.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="teamspace-rejection-reason">Reason (optional)</Label>
            <Textarea
              id="teamspace-rejection-reason"
              value={teamspaceReason}
              maxLength={500}
              onChange={(event) => setTeamspaceReason(event.target.value)}
              placeholder="Explain what needs to change before this teamspace can become public."
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRejectTeamspace(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={teamspaceAction.isPending}
              onClick={() => {
                if (!rejectTeamspace) return;
                teamspaceAction.mutate(
                  { id: rejectTeamspace.team_id, approve: false, reason: teamspaceReason.trim() || undefined },
                  {
                    onSuccess: () => {
                      setRejectTeamspace(null);
                      setTeamspaceReason("");
                    },
                  },
                );
              }}
            >
              Reject request
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ReviewDiffSheet
        item={diffItem}
        open={!!diffItem}
        onOpenChange={(open) => { if (!open) setDiffItem(null); }}
        onApprove={handleApprove}
        onReject={handleReject}
        onOpenComponentReview={handleOpenComponentReview}
      />
      <ReviewDiffSheet
        item={nestedDiffItem}
        open={!!nestedDiffItem}
        onOpenChange={(open) => { if (!open) setNestedDiffItem(null); }}
        onApprove={handleApproveWithRefetch}
        onReject={handleReject}
      />
    </>
  );
}
