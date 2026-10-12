// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { Link, useRouter, useSearch } from "@tanstack/react-router";
import { Suspense, useState, useEffect, useRef, useMemo, useCallback, useSyncExternalStore } from "react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import {
  Search,
  Bot,
  Trash2,
  Archive,
  ArchiveRestore,
  FileEdit,
  Send,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { PickerSelect } from "@/components/ui/picker-select";
import { UserSearchInput } from "@/components/shared/user-search-input";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import {
  useRegistryList,
  useMyAgents,
  useArchivedAgents,
  useDeletedAgents,
  useWhoami,
  useArchiveAgent,
  useUnarchiveAgent,
  useDeleteAgent,
  useRestoreDeletedAgent,
  useSubmitDraft,
  useTeams,
} from "@/hooks/use-api";
import { registry, getUserRole } from "@/lib/api";
import { useOptionalAuth } from "@/hooks/use-auth";
import { hasMinRole } from "@/hooks/use-role-guard";
import { PageHeader } from "@/components/layouts/page-header";
import { TableSkeleton, CardSkeleton } from "@/components/shared/skeleton-layouts";
import { ErrorState } from "@/components/shared/error-state";
import { EmptyState } from "@/components/shared/empty-state";
import { StatusBadge } from "@/components/registry/status-badge";
import { RecommendedBadge } from "@/components/registry/recommended-badge";
import { ForkMarkers } from "@/components/registry/fork-provenance";
import { EntityGlyph } from "@/components/registry/entity-glyph";
import { RegistryName } from "@/components/registry/registry-name";
import { HarnessBadges } from "@/components/registry/harness-badges";
import { registryItemPath, canonicalRouteParts } from "@/lib/registry-name";
import { useHarnesses } from "@/hooks/use-harnesses";
import { compactNumber } from "@/lib/utils";
import {
  TypeTabs,
  ViewToggle,
  RegistryToolbar,
  ToolbarSpacer,
  StatusStrip,
  RegistryNote,
  CatalogGrid,
} from "@/components/registry/registry-primitives";
import type { RegistryItem } from "@/lib/types";

/* ────────────────────────────────────────────────────────── */
/*  Constants                                                  */
/* ────────────────────────────────────────────────────────── */

type ViewMode = "grid" | "list";
type AgentTab = "discover" | "my" | "pending" | "archived";

const AGENT_CATEGORIES = [
  "Code Review",
  "Testing",
  "Documentation",
  "DevOps",
  "Security",
  "Data",
  "Incident Response",
  "Deployment",
  "Cost Optimization",
  "Other",
];

const roleSub = (cb: () => void) => {
  window.addEventListener("storage", cb);
  return () => window.removeEventListener("storage", cb);
};

/* ────────────────────────────────────────────────────────── */
/*  Action buttons (preserved from original)                   */
/* ────────────────────────────────────────────────────────── */

function DeleteAgentButton({ agent }: { agent: RegistryItem }) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const deleteMutation = useDeleteAgent();
  const { data: whoami } = useWhoami();
  const isAdmin = useSyncExternalStore(roleSub, () => hasMinRole(getUserRole(), "admin"), () => false);
  const canDelete = isAdmin || (whoami?.id && agent.created_by && whoami.id === String(agent.created_by));

  async function handleDelete(e: React.MouseEvent) {
    e.stopPropagation();
    deleteMutation.mutate(agent.id, {
      onSuccess: () => setConfirmOpen(false),
    });
  }

  if (!canDelete) return null;

  return (
    <>
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-7 p-0 text-muted-foreground hover:text-destructive"
              onClick={(e) => {
                e.stopPropagation();
                setConfirmOpen(true);
              }}
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Delete</TooltipContent>
        </Tooltip>
      </TooltipProvider>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent onClick={(e) => e.stopPropagation()}>
          <DialogHeader>
            <DialogTitle>Delete {agent.name}?</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            This soft deletes the agent, hides it from registry lists, and frees the name for reuse.
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>Cancel</Button>
            <Button variant="destructive" onClick={handleDelete} disabled={deleteMutation.isPending}>
              {deleteMutation.isPending ? "Deleting..." : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function ArchiveAgentButton({ agent }: { agent: RegistryItem }) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const isAdmin = useSyncExternalStore(roleSub, () => hasMinRole(getUserRole(), "admin"), () => false);
  const { data: whoami } = useWhoami();
  const archiveMutation = useArchiveAgent();
  const isOwner = whoami?.id && agent.created_by && whoami.id === String(agent.created_by);

  if ((!isAdmin && !isOwner) || agent.status === "archived") return null;

  return (
    <>
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-7 p-0 text-muted-foreground hover:text-orange-600"
              onClick={(e) => {
                e.stopPropagation();
                setConfirmOpen(true);
              }}
            >
              <Archive className="h-3.5 w-3.5" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Archive</TooltipContent>
        </Tooltip>
      </TooltipProvider>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent onClick={(e) => e.stopPropagation()}>
          <DialogHeader>
            <DialogTitle>Archive Agent</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            This will hide the agent from the registry. The agent data will be preserved.
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>Cancel</Button>
            <Button
              variant="outline"
              className="border-dark-yellow/40 bg-light-yellow text-dark-yellow hover:bg-light-yellow/80"
              onClick={(e) => {
                e.stopPropagation();
                archiveMutation.mutate(agent.id, {
                  onSuccess: () => setConfirmOpen(false),
                });
              }}
              disabled={archiveMutation.isPending}
            >
              {archiveMutation.isPending ? "Archiving..." : "Archive"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function RestoreDeletedAgentButton({ agent }: { agent: RegistryItem }) {
  const restoreMutation = useRestoreDeletedAgent();

  return (
    <Button
      variant="outline"
      size="sm"
      className="h-7 text-xs"
      onClick={() => restoreMutation.mutate({ id: agent.id })}
      disabled={restoreMutation.isPending}
    >
      <ArchiveRestore className="mr-1 h-3 w-3" />
      {restoreMutation.isPending ? "Restoring..." : "Restore"}
    </Button>
  );
}

function UnarchiveAgentButton({ agent }: { agent: RegistryItem }) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const unarchiveMutation = useUnarchiveAgent();

  if (agent.status !== "archived") return null;

  return (
    <>
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-7 p-0 text-muted-foreground hover:text-success"
              onClick={(e) => {
                e.stopPropagation();
                setConfirmOpen(true);
              }}
            >
              <ArchiveRestore className="h-3.5 w-3.5" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Restore</TooltipContent>
        </Tooltip>
      </TooltipProvider>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent onClick={(e) => e.stopPropagation()}>
          <DialogHeader>
            <DialogTitle>Restore Agent</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            This will restore the agent to the public registry.
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>Cancel</Button>
            <Button
              onClick={(e) => {
                e.stopPropagation();
                unarchiveMutation.mutate(agent.id, {
                  onSuccess: () => setConfirmOpen(false),
                });
              }}
              disabled={unarchiveMutation.isPending}
            >
              {unarchiveMutation.isPending ? "Restoring..." : "Restore"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

/* ────────────────────────────────────────────────────────── */
/*  Agent catalog card (mockup: registryAgentCard)             */
/* ────────────────────────────────────────────────────────── */

function AgentCatalogCard({
  agent,
  className,
}: {
  agent: RegistryItem;
  className?: string;
}) {
  const canonical = canonicalRouteParts(agent.namespace, agent.slug);
  const status = (agent.status as string | undefined) ?? "approved";
  const name = agent.name;
  const handle = agent.namespace && agent.slug
    ? `${agent.namespace}/${agent.slug}`
    : agent.qualified_name || agent.name;
  const version = agent.version as string | undefined;
  const description = typeof agent.description === "string" ? agent.description : undefined;
  const downloads = agent.download_count as number | undefined;
  const rating = agent.average_rating as number | null | undefined;

  const cardClass = [
    "flex flex-col rounded-xl bg-card p-[18px] shadow-sm min-h-[160px]",
    "transition-all duration-200 ease-out",
    "hover:-translate-y-px hover:shadow-[0_4px_16px_rgba(28,27,24,.08)]",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
    className ?? "",
  ].join(" ");

  const body = (
    <>
      {/* Top row: icon + status badge */}
      <div className="flex items-start justify-between gap-3">
        <EntityGlyph type="agent" size="sm" labelled={false} />
        <div className="flex items-center gap-1.5">
          {agent.is_recommended && <RecommendedBadge />}
          <ForkMarkers provenance={agent.forked_from} count={agent.fork_count} />
          <StatusBadge status={status} />
        </div>
      </div>

      {/* Title */}
      <div className="mt-3 text-sm font-medium">{name}</div>

      {/* Handle · version */}
      <div className="mt-[3px] font-mono text-[10px] text-muted-foreground">
        {handle}
        {version ? ` · ${version}` : ""}
      </div>

      {/* Description */}
      {description && (
        <p className="mt-[9px] flex-1 text-xs leading-relaxed text-muted-foreground line-clamp-3">
          {description}
        </p>
      )}

      {/* Harness chips */}
      <HarnessBadges
        supportedHarnesses={agent.supported_harnesses as string[] | undefined}
        inferredSupportedHarnesses={agent.inferred_supported_harnesses as string[] | undefined}
        max={3}
        className="mt-2.5"
      />

      {/* Meta row */}
      <div className="mt-3.5 flex items-center gap-3 border-t border-border pt-3 text-[10px] text-muted-foreground">
        {downloads != null && <span>{compactNumber(downloads)} pulls</span>}
        {rating != null && <span>★ {rating.toFixed(1)}</span>}
      </div>
    </>
  );

  if (canonical) {
    return (
      <Link to="/agents/$namespace/$slug" params={canonical} className={cardClass}>
        {body}
      </Link>
    );
  }
  return (
    <Link to="/agents/$agentId" params={{ agentId: agent.id }} className={cardClass}>
      {body}
    </Link>
  );
}

/* ────────────────────────────────────────────────────────── */
/*  Agent list row (mockup: agentListRow)                      */
/*  Grid: minmax(300px,1.8fr) 115px 105px 75px 80px           */
/* ────────────────────────────────────────────────────────── */

function AgentListRow({ agent, onClick }: { agent: RegistryItem; onClick: () => void }) {
  const status = (agent.status as string | undefined) ?? "approved";
  const handle = agent.namespace && agent.slug
    ? `${agent.namespace}/${agent.slug}`
    : agent.qualified_name || agent.name;
  const harnesses = (agent.supported_harnesses as string[] | undefined) ?? (agent.inferred_supported_harnesses as string[] | undefined) ?? [];
  const harnessText = harnesses.length > 0
    ? harnesses.length <= 3
      ? harnesses.map((h) => h.split("-").map((p) => p.charAt(0).toUpperCase() + p.slice(1)).join(" ")).join(" · ")
      : `${harnesses.length} supported`
    : "";
  const componentCount = agent.component_count as number | undefined;
  const sub = [handle, harnessText, componentCount != null ? `${componentCount} components` : ""].filter(Boolean).join(" · ");

  return (
    <div
      className="grid min-h-[66px] cursor-pointer items-center gap-3 border-t border-border px-[22px] py-3 transition-colors first:border-t-0 hover:bg-surface-raised"
      style={{ gridTemplateColumns: "minmax(300px,1.8fr) 115px 105px 75px 80px" }}
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") onClick(); }}
    >
      {/* Agent identity */}
      <div className="flex items-center gap-[11px] min-w-0">
        <EntityGlyph type="agent" size="sm" labelled={false} />
        <div className="min-w-0">
          <strong className="block truncate text-xs font-medium">{agent.name}</strong>
          <span className="block truncate mt-0.5 font-mono text-[9px] text-muted-foreground">{sub}</span>
        </div>
      </div>

      {/* Status */}
      <div className="flex items-center gap-1.5">
        {agent.is_recommended && <RecommendedBadge />}
        <ForkMarkers provenance={agent.forked_from} count={agent.fork_count} />
        <StatusBadge status={status} />
      </div>

      {/* Pulls */}
      <span className="font-mono text-[10px] text-muted-foreground">
        {agent.download_count != null ? compactNumber(agent.download_count as number) : "-"}
      </span>

      {/* Rating */}
      <span className="font-mono text-[10px] text-muted-foreground">
        {(agent.average_rating as number | null) != null
          ? `★ ${(agent.average_rating as number).toFixed(1)}`
          : "-"}
      </span>

      {/* Version */}
      <span className="font-mono text-[10px] text-muted-foreground">
        {(agent.version as string | undefined) ?? "-"}
      </span>
    </div>
  );
}

/* ────────────────────────────────────────────────────────── */
/*  Main page                                                  */
/* ────────────────────────────────────────────────────────── */

export default function AgentListPage() {
  return (
    <Suspense>
      <AgentListContent />
    </Suspense>
  );
}

function AgentListContent() {
  const { search: searchParam, namespace, team, category, harness } = useSearch({ from: "/_authed/agents/" });
  const router = useRouter();
  const { isAuthenticated } = useOptionalAuth();
  const { data: teams = [] } = useTeams(isAuthenticated);
  const { data: harnessList = [] } = useHarnesses();
  const selectedTeam = teams.find((item) => item.handle === team);
  const initialSearch = searchParam ?? "";
  const [search, setSearch] = useState(initialSearch);
  const [debouncedSearch, setDebouncedSearch] = useState(initialSearch);
  const [publisherQuery, setPublisherQuery] = useState(namespace ? `@${namespace}` : "");
  const [view, setView] = useState<ViewMode>("grid");
  const [tab, setTab] = useState<AgentTab>("discover");
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    timerRef.current = setTimeout(() => setDebouncedSearch(search), 300);
    return () => { if (timerRef.current) clearTimeout(timerRef.current); };
  }, [search]);

  useEffect(() => {
    setPublisherQuery(namespace ? `@${namespace}` : "");
  }, [namespace]);

  const {
    data: agents,
    isLoading,
    isError,
    error,
    refetch,
  } = useRegistryList("agents", {
    ...(debouncedSearch ? { search: debouncedSearch } : {}),
    ...(namespace ? { namespace } : {}),
    ...(selectedTeam ? { team_id: selectedTeam.id } : {}),
    ...(category ? { category } : {}),
    ...(harness ? { harness } : {}),
  });

  const { data: myAgents } = useMyAgents(isAuthenticated);
  const isAdmin = useSyncExternalStore(
    roleSub,
    () => isAuthenticated && hasMinRole(getUserRole(), "admin"),
    () => false,
  );
  const { data: allArchivedAgents } = useArchivedAgents(isAdmin);
  const { data: deletedAgents = [] } = useDeletedAgents(isAuthenticated);
  const submitDraft = useSubmitDraft();
  const [deletingDraftId, setDeletingDraftId] = useState<string | null>(null);
  const qc = useQueryClient();

  const scopedMyAgents = useMemo(
    () => (isAuthenticated ? (myAgents ?? []) : []),
    [isAuthenticated, myAgents],
  );

  const drafts = useMemo(() => {
    return scopedMyAgents.filter((a) => a.status === "draft" || a.status === "rejected" || a.status === "pending");
  }, [scopedMyAgents]);

  const pendingAgents = useMemo(() => {
    return (myAgents ?? []).filter((a) => a.status === "pending");
  }, [myAgents]);

  const archivedAgents = useMemo(() => {
    if (isAdmin && allArchivedAgents) return allArchivedAgents;
    return scopedMyAgents.filter((a) => a.status === "archived");
  }, [isAdmin, allArchivedAgents, scopedMyAgents]);

  const { filtered, pendingCount } = useMemo(() => {
    const active = agents ?? [];
    const activeIds = new Set(active.map((a) => a.id));
    const pending = scopedMyAgents.filter(
      (a) => a.status !== "approved" && a.status !== "draft" && a.status !== "rejected" && a.status !== "archived" && !activeIds.has(a.id),
    );
    return { filtered: [...pending, ...active], pendingCount: pending.length };
  }, [agents, scopedMyAgents]);

  /* Tab counts */
  const tabData = useMemo(() => [
    { value: "discover", label: "Discover", count: filtered.length || undefined },
    { value: "my", label: "My agents", count: (myAgents ?? []).length || undefined },
    { value: "pending", label: "Pending review", count: pendingAgents.length || undefined },
    { value: "archived", label: "Archived", count: archivedAgents.length || undefined },
  ], [filtered.length, myAgents, pendingAgents.length, archivedAgents.length]);

  /* Current tab content */
  const visibleAgents = useMemo(() => {
    switch (tab) {
      case "my":
        return myAgents ?? [];
      case "pending":
        return pendingAgents;
      case "archived":
        return archivedAgents;
      case "discover":
      default:
        return filtered;
    }
  }, [tab, filtered, myAgents, pendingAgents, archivedAgents]);

  const handleRowClick = useCallback(
    (id: string) => {
      router.navigate({ to: "/agents/$agentId", params: { agentId: id } });
    },
    [router],
  );

  function handleEditDraft(draft: RegistryItem) {
    router.navigate({ to: "/agents/builder", search: { draft: draft.id } });
  }

  async function handleDeleteDraft(id: string) {
    setDeletingDraftId(id);
    try {
      await registry.delete("agents", id);
      qc.invalidateQueries({ queryKey: ["registry", "agents"] });
      toast.success("Draft deleted");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete draft");
    } finally {
      setDeletingDraftId(null);
    }
  }

  function updateFilters(next: { search?: string; namespace?: string; team?: string; category?: string; harness?: string }) {
    router.navigate({
      to: "/agents",
      search: (current) => ({ ...current, ...next }),
      replace: true,
    });
  }

  function clearFilters() {
    setSearch("");
    setDebouncedSearch("");
    setPublisherQuery("");
    updateFilters({ search: undefined, namespace: undefined, team: undefined, category: undefined, harness: undefined });
  }

  const hasFilters = !!(search || namespace || team || category || harness);

  return (
    <>
      <PageHeader
        title="Agents"
        breadcrumbs={[
          { label: "Registry", href: "/" },
          { label: "Agents" },
        ]}
      />

      <div className="page-body w-full mx-auto">
        {/* ── Type Tabs ── */}
        <TypeTabs
          tabs={tabData}
          active={tab}
          onTabChange={(v) => setTab(v as AgentTab)}
        />

        {/* ── Toolbar ── */}
        <RegistryToolbar>
          <div className="relative w-[min(360px,100%)] shrink-[2]">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <input
              aria-label="Search agents"
              type="text"
              placeholder="Search name, slug, or description"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                updateFilters({ search: e.target.value || undefined });
              }}
              className="h-[34px] w-full min-w-0 rounded-[9px] border border-border bg-transparent pl-9 pr-3 text-xs text-foreground outline-none placeholder:text-muted-foreground focus:ring-1 focus:ring-ring"
            />
          </div>
          <PickerSelect
            value={team ?? ""}
            onValueChange={(value) => updateFilters({ team: value || undefined })}
            options={[
              { value: "", label: "All visible teamspaces" },
              ...teams.map((item) => ({ value: item.handle, label: `Team: ${item.name}` })),
            ]}
            placeholder="Teamspace"
            className="min-w-[190px] w-auto"
            inputClassName="h-[34px]"
          />
          <UserSearchInput
            value={publisherQuery}
            onValueChange={(value) => {
              setPublisherQuery(value);
              if (namespace && value !== namespace && value !== `@${namespace}`) {
                updateFilters({ namespace: undefined });
              }
            }}
            onSelect={(user) => {
              if (!user.username) return;
              setPublisherQuery(`@${user.username}`);
              updateFilters({ namespace: user.username });
            }}
            placeholder="Publisher"
            className="min-w-[145px] w-auto"
            inputClassName="h-[34px]"
          />
          <PickerSelect
            value={category ?? ""}
            onValueChange={(value) => updateFilters({ category: value || undefined })}
            options={[
              { value: "", label: "All categories" },
              ...AGENT_CATEGORIES.map((item) => ({ value: item, label: item })),
            ]}
            placeholder="Category"
            className="min-w-[145px] w-auto"
            inputClassName="h-[34px]"
          />
          <PickerSelect
            value={harness ?? ""}
            onValueChange={(value) => updateFilters({ harness: value || undefined })}
            options={[
              { value: "", label: "Any harness" },
              ...harnessList.map((h) => ({ value: h.name, label: h.display_name })),
            ]}
            placeholder="Harness"
            className="min-w-[145px] w-auto"
            inputClassName="h-[34px]"
          />
          <ToolbarSpacer />
          <ViewToggle view={view} onViewChange={setView} />
        </RegistryToolbar>

        {/* ── Active filter chips ── */}
        {hasFilters && (
          <div className="mb-3.5 flex min-h-7 items-center gap-2 flex-wrap" aria-label="Active filters">
            {team && (
              <Button variant="secondary" size="sm" className="h-7 gap-1 px-2 text-xs" onClick={() => updateFilters({ team: undefined })}>
                Team: {selectedTeam?.name ?? team}
                <X className="h-3 w-3" />
              </Button>
            )}
            {namespace && (
              <Button variant="secondary" size="sm" className="h-7 gap-1 px-2 text-xs" onClick={() => updateFilters({ namespace: undefined })}>
                Publisher: @{namespace}
                <X className="h-3 w-3" />
              </Button>
            )}
            {category && (
              <Button variant="secondary" size="sm" className="h-7 gap-1 px-2 text-xs" onClick={() => updateFilters({ category: undefined })}>
                Category: {category}
                <X className="h-3 w-3" />
              </Button>
            )}
            {harness && (
              <Button variant="secondary" size="sm" className="h-7 gap-1 px-2 text-xs" onClick={() => updateFilters({ harness: undefined })}>
                Harness: {harnessList.find((h) => h.name === harness)?.display_name ?? harness}
                <X className="h-3 w-3" />
              </Button>
            )}
            <Button variant="ghost" size="sm" className="h-7 px-2 text-xs text-muted-foreground" onClick={clearFilters}>
              Clear all
            </Button>
          </div>
        )}

        {/* ── Status strip (drafts & pending) ── */}
        {tab === "discover" && drafts.length > 0 && (
          <StatusStrip
            icon={
              <span className="inline-grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-warning/10 font-mono text-[11px] font-semibold text-warning">
                D
              </span>
            }
            title="Your drafts and submissions"
            subtitle={drafts.map((d) => {
              const s = d.status as string;
              return `${d.name} is ${s === "draft" ? "a draft" : s === "pending" ? "pending review" : s}`;
            }).join(" · ")}
            badge={
              <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-light-yellow px-2.5 py-0.5 text-2xs font-medium text-dark-yellow">
                <span className="inline-block h-[5px] w-[5px] rounded-full bg-dark-yellow" />
                {drafts.length} item{drafts.length === 1 ? "" : "s"}
              </span>
            }
            action={
              <Button
                variant="outline"
                size="sm"
                className="h-7 shrink-0 text-xs"
                onClick={() => setTab("my")}
              >
                Open
              </Button>
            }
          />
        )}

        {/* ── Registry note ── */}
        {tab === "discover" && pendingCount > 0 && (
          <RegistryNote className="mb-3.5">
            Pending and rejected agents remain visible to their submitters. Approved agents are preferred for everyone else.
          </RegistryNote>
        )}

        {/* ── Content ── */}
        {isLoading ? (
          view === "grid" ? (
            <CardSkeleton count={6} columns={3} />
          ) : (
            <TableSkeleton rows={6} cols={5} />
          )
        ) : isError ? (
          <ErrorState message={error?.message} onRetry={() => refetch()} />
        ) : visibleAgents.length === 0 ? (
          <EmptyState
            icon={Bot}
            title={tab === "discover" ? "No agents published yet" : tab === "my" ? "No agents yet" : tab === "pending" ? "No pending reviews" : "No archived agents"}
            description={
              tab === "discover" && hasFilters
                ? "No agents match the active search and filters."
                : tab === "discover"
                  ? "No agents have been submitted yet. Be the first to publish one."
                  : tab === "my"
                    ? "Create your first agent using the builder."
                    : tab === "pending"
                      ? "Nothing waiting for review right now."
                      : "No archived agents."
            }
            actionLabel={tab === "discover" ? "Back to Registry" : undefined}
            actionHref={tab === "discover" ? "/" : undefined}
          />
        ) : view === "grid" ? (
          /* ── Grid view (catalog-grid) ── */
          <CatalogGrid className="animate-in">
            {visibleAgents.map((agent, i) => (
              <AgentCatalogCard
                key={agent.id}
                agent={agent}
                className={`animate-in stagger-${Math.min(i + 1, 5)}`}
              />
            ))}
          </CatalogGrid>
        ) : (
          /* ── List view (agent-list) ── */
          <section className="overflow-hidden rounded-xl bg-card shadow-sm animate-in">
            {/* Table head */}
            <div className="flex items-center justify-between border-b border-border px-[22px] py-4">
              <div>
                <div className="text-sm font-medium">Agent catalogue</div>
                <div className="mt-[3px] text-2xs text-muted-foreground">
                  Approved and owner-visible agents in the current view
                </div>
              </div>
              <span className="font-mono text-xs text-muted-foreground">
                {compactNumber(visibleAgents.length)} agents
              </span>
            </div>

            {/* Column headers */}
            <div
              className="grid min-h-[40px] items-center gap-3 border-b border-border px-[22px] text-2xs font-medium uppercase tracking-[0.05em] text-muted-foreground"
              style={{ gridTemplateColumns: "minmax(300px,1.8fr) 115px 105px 75px 80px" }}
            >
              <span>Agent</span>
              <span>Status</span>
              <span>Pulls</span>
              <span>Rating</span>
              <span>Version</span>
            </div>

            {/* Rows */}
            <div className="overflow-x-auto">
              {visibleAgents.map((agent) => (
                <AgentListRow
                  key={agent.id}
                  agent={agent}
                  onClick={() => handleRowClick(agent.id)}
                />
              ))}
            </div>
          </section>
        )}

        {/* ── Inline draft management (My agents tab) ── */}
        {tab === "my" && drafts.length > 0 && (
          <section className="mt-3.5 rounded-xl bg-card p-5 shadow-sm">
            <div className="mb-4 text-sm font-medium">Drafts &amp; submissions</div>
            <div className="divide-y divide-border">
              {drafts.map((draft) => (
                <div key={draft.id} className="flex items-center gap-4 py-3">
                  <EntityGlyph type="agent" size="sm" labelled={false} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-start gap-2">
                      <RegistryName item={draft} nameClassName="text-xs font-medium" />
                      {(draft.status === "rejected" || draft.status === "pending") && (
                        <StatusBadge status={draft.status as string} />
                      )}
                    </div>
                    {draft.status === "rejected" && draft.rejection_reason && (
                      <p className="text-[10px] text-destructive mt-0.5">
                        Reason: {draft.rejection_reason as string}
                      </p>
                    )}
                    {draft.description && (
                      <p className="truncate text-[10px] text-muted-foreground mt-0.5">
                        {draft.description}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-7 text-xs"
                      onClick={() => handleEditDraft(draft)}
                    >
                      <FileEdit className="mr-1 h-3 w-3" />
                      Edit
                    </Button>
                    {(draft.status === "draft" || draft.status === "rejected") && (
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-7 text-xs"
                        disabled={submitDraft.isPending}
                        onClick={() => submitDraft.mutate(draft.id)}
                      >
                        <Send className="mr-1 h-3 w-3" />
                        {draft.status === "rejected" ? "Resubmit" : "Submit"}
                      </Button>
                    )}
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 w-7 p-0 text-muted-foreground hover:text-destructive"
                      disabled={deletingDraftId === draft.id}
                      onClick={() => handleDeleteDraft(draft.id)}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* ── Archived tab: action buttons ── */}
        {tab === "archived" && archivedAgents.length > 0 && view === "list" && (
          <div className="mt-2 text-[10px] text-muted-foreground">
            Click an agent row to view details. Use the restore or delete buttons on the detail page.
          </div>
        )}

        {/* ── Deleted agents (admin only, inline in archived tab) ── */}
        {tab === "archived" && deletedAgents.length > 0 && (
          <section className="mt-3.5 rounded-xl bg-card p-5 shadow-sm">
            <div className="mb-4 flex items-center gap-2 text-sm font-medium">
              <Trash2 className="h-4 w-4 text-muted-foreground" />
              Deleted
              <span className="ml-1 font-mono text-[10px] text-muted-foreground">{deletedAgents.length}</span>
            </div>
            <div className="divide-y divide-border">
              {deletedAgents.map((agent) => (
                <div key={agent.id} className="flex items-center gap-4 py-3">
                  <EntityGlyph type="agent" size="sm" labelled={false} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-start gap-2">
                      <RegistryName item={agent} nameClassName="text-xs font-medium" />
                      <StatusBadge status="deleted" />
                    </div>
                    {agent.description && (
                      <p className="truncate text-[10px] text-muted-foreground mt-0.5">
                        {agent.description}
                      </p>
                    )}
                  </div>
                  <RestoreDeletedAgentButton agent={agent} />
                </div>
              ))}
            </div>
          </section>
        )}
      </div>
    </>
  );
}
