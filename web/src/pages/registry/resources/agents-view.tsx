// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { Link, useRouter } from "@tanstack/react-router";
import { useState, useMemo, useCallback, useSyncExternalStore } from "react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import { Bot, Trash2, ArchiveRestore, FileEdit, Send, Network, Check, Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  useRegistryList,
  useMyAgents,
  useArchivedAgents,
  useDeletedAgents,
  useRestoreDeletedAgent,
  useSubmitDraft,
  useRemoteAgents,
} from "@/hooks/use-api";
import { registry, getUserRole } from "@/lib/api";
import { hasMinRole } from "@/hooks/use-role-guard";
import { ErrorState } from "@/components/shared/error-state";
import { EmptyState } from "@/components/shared/empty-state";
import { HarnessBadges } from "@/components/registry/harness-badges";
import { canonicalRouteParts } from "@/lib/registry-name";
import { useHarnesses } from "@/hooks/use-harnesses";
import { compactNumber } from "@/lib/utils";
import { StatusBadge } from "@/components/registry/status-badge";
import { StatusStrip, RegistryNote, CatalogGrid } from "@/components/registry/registry-primitives";
import type { RegistryItem, RemoteAgent } from "@/lib/types";
import type { FilterField } from "@/components/registry/filter-bar";
import {
  CardFooter,
  ListSkeleton,
  ResourceCard,
  ResourceList,
  ResourceRow,
  ResourceToolbar,
  SubmissionRow,
  StatusBadges,
  SubmissionsPanel,
  commonFields,
  monoCell,
  useResourceFilters,
  type ViewMode,
} from "./shared";

const TABS = ["discover", "my", "pending", "archived"] as const;
type AgentTab = (typeof TABS)[number];

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

const LIST_COLUMNS = "minmax(300px,1.8fr) 115px 105px 75px 80px";

const roleSub = (cb: () => void) => {
  window.addEventListener("storage", cb);
  return () => window.removeEventListener("storage", cb);
};

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

/** Running agents can hand a delegable agent a task through the observal-agents MCP server. */
function DelegableBadge() {
  return (
    <span
      className="inline-flex items-center rounded-full bg-light-blue px-2 py-0.5 text-2xs font-medium text-dark-blue"
      title="Running agents can hand this agent a task through the observal-agents MCP server."
    >
      Delegable
    </span>
  );
}

/** Mirrors the server's `delegable()`: approved, and a supported harness (none listed means all) can run headless. */
function useIsDelegable(agent: RegistryItem) {
  const { data: harnessList = [] } = useHarnesses();
  if ((agent.status ?? "approved") !== "approved") return false;
  const headless = harnessList.filter((h) => h.runtime.headless_run).map((h) => h.name);
  const supported = (agent.supported_harnesses as string[] | undefined) ?? [];
  return supported.length ? supported.some((h) => headless.includes(h)) : headless.length > 0;
}

function RemoteAgentCard({ agent }: { agent: RemoteAgent }) {
  const [copied, setCopied] = useState(false);
  const command = `observal delegate run ${agent.identifier} "<task>"`;
  const lifecycle = agent["obs:lifecycle"];
  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Could not copy to the clipboard.");
    }
  }
  return (
    <div className="flex min-h-[160px] flex-col rounded-xl bg-card p-[18px] shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <span className="inline-flex items-center gap-1.5 text-2xs font-medium text-primary">
          <Network className="h-4 w-4" />
          Remote A2A agent
        </span>
        <div className="flex items-center gap-1.5">
          {agent["obs:delegable"] && <DelegableBadge />}
          <StatusBadge status={lifecycle} />
        </div>
      </div>
      <div className="mt-3 text-sm font-medium">{agent.displayName}</div>
      <div className="mt-[3px] font-mono text-[10px] text-muted-foreground">
        {agent.identifier}
        {agent.version ? ` · ${agent.version}` : ""}
      </div>
      <div className="flex-1" />
      <button
        type="button"
        onClick={copy}
        title="Copy command"
        className="group mt-3 flex w-full items-center gap-2 rounded-md border bg-muted/40 px-2.5 py-1.5 text-left font-mono text-[11px] text-muted-foreground hover:bg-muted"
      >
        <span className="truncate">{command}</span>
        {copied ? <Check className="ml-auto h-3.5 w-3.5 shrink-0 text-success" /> : <Copy className="ml-auto h-3.5 w-3.5 shrink-0 opacity-0 group-hover:opacity-100" />}
      </button>
    </div>
  );
}

function AgentCatalogCard({ agent, className }: { agent: RegistryItem; className?: string }) {
  const canonical = canonicalRouteParts(agent.namespace, agent.slug);
  const downloads = agent.download_count as number | undefined;
  const rating = agent.average_rating as number | null | undefined;
  const delegable = useIsDelegable(agent);

  return (
    <ResourceCard
      item={agent}
      glyph="agent"
      labelled={false}
      className={className}
      render={(p) =>
        canonical ? (
          <Link to="/agents/$namespace/$slug" params={canonical} {...p} />
        ) : (
          <Link to="/agents/$agentId" params={{ agentId: agent.id }} {...p} />
        )
      }
    >
      <HarnessBadges
        supportedHarnesses={agent.supported_harnesses as string[] | undefined}
        inferredSupportedHarnesses={agent.inferred_supported_harnesses as string[] | undefined}
        max={3}
        className="mt-2.5"
      />
      {delegable && (
        <div className="mt-2">
          <DelegableBadge />
        </div>
      )}
      <CardFooter>
        {downloads != null && <span>{compactNumber(downloads)} pulls</span>}
        {rating != null && <span>★ {rating.toFixed(1)}</span>}
      </CardFooter>
    </ResourceCard>
  );
}

function AgentListRow({ agent, onClick }: { agent: RegistryItem; onClick: () => void }) {
  const harnesses =
    (agent.supported_harnesses as string[] | undefined) ??
    (agent.inferred_supported_harnesses as string[] | undefined) ??
    [];
  const harnessText =
    harnesses.length === 0
      ? ""
      : harnesses.length <= 3
        ? harnesses.map((h) => h.split("-").map((p) => p.charAt(0).toUpperCase() + p.slice(1)).join(" ")).join(" · ")
        : `${harnesses.length} supported`;
  const componentCount = agent.component_count as number | undefined;
  const handle = agent.namespace && agent.slug ? `${agent.namespace}/${agent.slug}` : agent.qualified_name || agent.name;
  const delegable = useIsDelegable(agent);
  const subtitle = [handle, harnessText, componentCount != null ? `${componentCount} components` : "", delegable ? "Delegable" : ""]
    .filter(Boolean)
    .join(" · ");

  return (
    <ResourceRow item={agent} glyph="agent" columns={LIST_COLUMNS} subtitle={subtitle} onClick={onClick}>
      <StatusBadges item={agent} />
      <span className={monoCell}>{agent.download_count != null ? compactNumber(agent.download_count as number) : "-"}</span>
      <span className={monoCell}>
        {(agent.average_rating as number | null) != null ? `★ ${(agent.average_rating as number).toFixed(1)}` : "-"}
      </span>
      <span className={monoCell}>{(agent.version as string | undefined) ?? "-"}</span>
    </ResourceRow>
  );
}

export default function AgentsView() {
  const f = useResourceFilters();
  const { namespace, team, category, harness } = f.searchParams;
  const router = useRouter();
  const { data: harnessList = [] } = useHarnesses();
  const [layout, setLayout] = useState<ViewMode>("grid");
  const tab = TABS.find((t) => t === f.searchParams.view) ?? "discover";

  const { data: agents, isLoading, isError, error, refetch } = useRegistryList("agents", {
    ...(f.debouncedSearch ? { search: f.debouncedSearch } : {}),
    ...(namespace ? { namespace } : {}),
    ...(f.selectedTeam ? { team_id: f.selectedTeam.id } : {}),
    ...(category ? { category } : {}),
    ...(harness ? { harness } : {}),
  });

  const { data: remoteAgents = { items: [], total: 0 } } = useRemoteAgents(f.isAuthenticated);
  const { data: myAgents } = useMyAgents(f.isAuthenticated);
  const isAdmin = useSyncExternalStore(
    roleSub,
    () => f.isAuthenticated && hasMinRole(getUserRole(), "admin"),
    () => false,
  );
  const { data: allArchivedAgents } = useArchivedAgents(isAdmin);
  const { data: deletedAgents = [] } = useDeletedAgents(f.isAuthenticated);
  const submitDraft = useSubmitDraft();
  const [deletingDraftId, setDeletingDraftId] = useState<string | null>(null);
  const qc = useQueryClient();

  const scopedMyAgents = useMemo(() => (f.isAuthenticated ? (myAgents ?? []) : []), [f.isAuthenticated, myAgents]);
  const drafts = useMemo(
    () => scopedMyAgents.filter((a) => a.status === "draft" || a.status === "rejected" || a.status === "pending"),
    [scopedMyAgents],
  );
  const pendingAgents = useMemo(() => (myAgents ?? []).filter((a) => a.status === "pending"), [myAgents]);
  const archivedAgents = useMemo(
    () => (isAdmin && allArchivedAgents ? allArchivedAgents : scopedMyAgents.filter((a) => a.status === "archived")),
    [isAdmin, allArchivedAgents, scopedMyAgents],
  );

  const { filtered, pendingCount } = useMemo(() => {
    const active = agents ?? [];
    const activeIds = new Set(active.map((a) => a.id));
    const pending = scopedMyAgents.filter(
      (a) => !["approved", "draft", "rejected", "archived"].includes(a.status ?? "") && !activeIds.has(a.id),
    );
    return { filtered: [...pending, ...active], pendingCount: pending.length };
  }, [agents, scopedMyAgents]);

  // Remote A2A agents carry no namespace, team, category or harness, so those filters hide them.
  const visibleRemote = useMemo(() => {
    if (tab !== "discover" || namespace || team || category || harness) return [];
    const q = f.debouncedSearch.trim().toLowerCase();
    return remoteAgents.items.filter(
      (r) => !q || r.displayName.toLowerCase().includes(q) || r.identifier.toLowerCase().includes(q),
    );
  }, [tab, namespace, team, category, harness, f.debouncedSearch, remoteAgents]);

  const fields: FilterField[] = [
    ...commonFields(f, [
      { value: "discover", label: "Discover", count: filtered.length },
      { value: "my", label: "My Agent Files", count: (myAgents ?? []).length },
      { value: "pending", label: "Pending review", count: pendingAgents.length },
      { value: "archived", label: "Archived", count: archivedAgents.length },
    ]),
    { key: "category", label: "Category", hint: "Agent File category", options: AGENT_CATEGORIES.map((c) => ({ value: c, label: c })) },
    { key: "harness", label: "Harness", hint: "Supported harness", options: harnessList.map((h) => ({ value: h.name, label: h.display_name })) },
  ];

  const visibleAgents = { discover: filtered, my: myAgents ?? [], pending: pendingAgents, archived: archivedAgents }[tab];

  const handleRowClick = useCallback(
    (id: string) => router.navigate({ to: "/agents/$agentId", params: { agentId: id } }),
    [router],
  );

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

  const hasFilters = !!(f.search || namespace || team || category || harness);

  return (
    <>
      <ResourceToolbar filters={f} fields={fields} noun="Agent Files" layout={layout} onLayoutChange={setLayout} />

      {tab === "discover" && drafts.length > 0 && (
        <StatusStrip
          icon={
            <span className="inline-grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-warning/10 font-mono text-[11px] font-semibold text-warning">
              D
            </span>
          }
          title="Your drafts and submissions"
          subtitle={drafts
            .map((d) => `${d.name} is ${d.status === "draft" ? "a draft" : d.status === "pending" ? "pending review" : d.status}`)
            .join(" · ")}
          badge={
            <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-light-yellow px-2.5 py-0.5 text-2xs font-medium text-dark-yellow">
              <span className="inline-block h-[5px] w-[5px] rounded-full bg-dark-yellow" />
              {drafts.length} item{drafts.length === 1 ? "" : "s"}
            </span>
          }
          action={
            <Button variant="outline" size="sm" className="h-7 shrink-0 text-xs" onClick={() => f.update({ view: "my" })}>
              Open
            </Button>
          }
        />
      )}

      {tab === "discover" && pendingCount > 0 && (
        <RegistryNote className="mb-3.5">
          Pending and rejected Agent Files remain visible to their submitters. Approved Agent Files are preferred for everyone else.
        </RegistryNote>
      )}

      {tab === "discover" && isLoading ? (
        <ListSkeleton view={layout} />
      ) : tab === "discover" && isError ? (
        <ErrorState message={error?.message} onRetry={() => refetch()} />
      ) : visibleAgents.length === 0 && visibleRemote.length === 0 ? (
        <EmptyState
          icon={Bot}
          title={{ discover: "No Agent Files published yet", my: "No Agent Files yet", pending: "No pending reviews", archived: "No archived Agent Files" }[tab]}
          description={
            tab === "discover"
              ? hasFilters
                ? "No Agent Files match the active search and filters."
                : "No Agent Files have been submitted yet. Be the first to publish one."
              : { my: "Create your first Agent File using the builder.", pending: "Nothing waiting for review right now.", archived: "No archived Agent Files." }[tab]
          }
          actionLabel={tab === "discover" ? "Back to Registry" : undefined}
          actionHref={tab === "discover" ? "/" : undefined}
        />
      ) : visibleAgents.length === 0 ? null : layout === "grid" ? (
        <CatalogGrid className="animate-in">
          {visibleAgents.map((agent, i) => (
            <AgentCatalogCard key={agent.id} agent={agent} className={`animate-in stagger-${Math.min(i + 1, 5)}`} />
          ))}
        </CatalogGrid>
      ) : (
        <ResourceList
          title="Agent File catalogue"
          subtitle="Approved and owner-visible Agent Files in the current view"
          count={`${compactNumber(visibleAgents.length)} Agent Files`}
          columns={LIST_COLUMNS}
          headers={["Agent File", "Status", "Pulls", "Rating", "Version"]}
        >
          {visibleAgents.map((agent) => (
            <AgentListRow key={agent.id} agent={agent} onClick={() => handleRowClick(agent.id)} />
          ))}
        </ResourceList>
      )}

      {visibleRemote.length > 0 && (
        <section className="mt-6" aria-label="Remote A2A agents">
          <h2 className="mb-2.5 text-xs font-medium text-muted-foreground">
            Remote A2A agents · {visibleRemote.length}
          </h2>
          <CatalogGrid>
            {visibleRemote.map((r) => (
              <RemoteAgentCard key={r.identifier} agent={r} />
            ))}
          </CatalogGrid>
        </section>
      )}

      {tab === "archived" && archivedAgents.length > 0 && layout === "list" && (
        <div className="mt-2 text-[10px] text-muted-foreground">
          Click an Agent File row to view details. Use the restore or delete buttons on the detail page.
        </div>
      )}

      {tab === "my" && drafts.length > 0 && (
        <SubmissionsPanel title="Drafts &amp; submissions">
          {drafts.map((draft) => (
            <SubmissionRow
              key={draft.id}
              item={draft}
              glyph="agent"
              actions={
                <>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 text-xs"
                    onClick={() => router.navigate({ to: "/agents/builder", search: { draft: draft.id } })}
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
                </>
              }
            />
          ))}
        </SubmissionsPanel>
      )}

      {tab === "archived" && deletedAgents.length > 0 && (
        <SubmissionsPanel
          title={
            <>
              <Trash2 className="h-4 w-4 text-muted-foreground" />
              Deleted
              <span className="ml-1 font-mono text-[10px] text-muted-foreground">{deletedAgents.length}</span>
            </>
          }
        >
          {deletedAgents.map((agent) => (
            <SubmissionRow
              key={agent.id}
              item={agent}
              glyph="agent"
              status="deleted"
              actions={<RestoreDeletedAgentButton agent={agent} />}
            />
          ))}
        </SubmissionsPanel>
      )}
    </>
  );
}
