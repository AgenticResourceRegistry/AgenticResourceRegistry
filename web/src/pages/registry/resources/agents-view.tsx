// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { Link, useRouter } from "@tanstack/react-router";
import { useState, useSyncExternalStore } from "react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import { Bot, Trash2, ArchiveRestore, FileEdit, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  useResourcePage,
  useAgentSubmissionCount,
  useArchivedAgentPage,
  useDeletedAgents,
  useRestoreDeletedAgent,
  useSubmitDraft,
} from "@/hooks/use-api";
import { registry, getUserRole } from "@/lib/api";
import { hasMinRole } from "@/hooks/use-role-guard";
import { ErrorState } from "@/components/shared/error-state";
import { EmptyState } from "@/components/shared/empty-state";
import { HarnessBadges } from "@/components/registry/harness-badges";
import { canonicalRouteParts } from "@/lib/registry-name";
import { useHarnesses } from "@/hooks/use-harnesses";
import { compactNumber } from "@/lib/utils";
import { CatalogGrid, StatusStrip } from "@/components/registry/registry-primitives";
import type { RegistryItem } from "@/lib/types";
import type { FilterField } from "@/components/registry/filter-bar";
import {
  CardFooter,
  ListSkeleton,
  ResourceCard,
  ResourceList,
  ResourceRow,
  ResourceToolbar,
  ResourcePager,
  SubmissionRow,
  StatusBadges,
  SubmissionsPanel,
  commonFields,
  monoCell,
  useResourceFilters,
  type ViewMode,
} from "./shared";

const TABS = ["discover", "my", "drafts", "pending", "archived"] as const;
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

function AgentCatalogCard({ agent, className }: { agent: RegistryItem; className?: string }) {
  const canonical = canonicalRouteParts(agent.namespace, agent.slug);
  const downloads = agent.download_count as number | undefined;
  const rating = agent.average_rating as number | null | undefined;

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
      <CardFooter>
        {downloads != null && <span>{compactNumber(downloads)} pulls</span>}
        {rating != null && <span>★ {rating.toFixed(1)}</span>}
      </CardFooter>
    </ResourceCard>
  );
}

function AgentListRow({ agent }: { agent: RegistryItem }) {
  const canonical = canonicalRouteParts(agent.namespace, agent.slug);
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
  const subtitle = [handle, harnessText, componentCount != null ? `${componentCount} components` : ""]
    .filter(Boolean)
    .join(" · ");

  return (
    <ResourceRow item={agent} glyph="agent" columns={LIST_COLUMNS} subtitle={subtitle}
      renderLink={(content) => canonical ? (
        <Link to="/agents/$namespace/$slug" params={canonical} className="flex min-w-0 items-center gap-[11px] rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring hover:underline">{content}</Link>
      ) : (
        <Link to="/agents/$agentId" params={{ agentId: agent.id }} className="flex min-w-0 items-center gap-[11px] rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring hover:underline">{content}</Link>
      )}>
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

  const page = (f.searchParams.page ?? 1) - 1;
  const isAdmin = useSyncExternalStore(
    roleSub,
    () => f.isAuthenticated && hasMinRole(getUserRole(), "admin"),
    () => false,
  );
  const filters = {
    ...(f.debouncedSearch ? { search: f.debouncedSearch } : {}),
    ...(namespace ? { namespace } : {}),
    ...(f.selectedTeam ? { team_id: f.selectedTeam.id } : {}),
    ...(category ? { category } : {}),
    ...(harness ? { harness } : {}),
  };
  const publicPage = useResourcePage("agents", false, filters, page, tab === "discover" && !f.unresolvedTeam);
  const ownerPage = useResourcePage("agents", true, {
    ...filters,
    ...(tab === "drafts" ? { status: "draft" } : tab === "pending" ? { status: "pending" } : tab === "archived" ? { status: "archived" } : {}),
  }, page, f.isAuthenticated && tab !== "discover" && !(tab === "archived" && isAdmin) && !f.unresolvedTeam);
  const pendingCountQuery = useAgentSubmissionCount("pending", tab === "discover" && f.isAuthenticated && !f.unresolvedTeam);
  const rejectedCountQuery = useAgentSubmissionCount("rejected", tab === "discover" && f.isAuthenticated && !f.unresolvedTeam);
  const submissionCount = (pendingCountQuery.data?.total ?? 0) + (rejectedCountQuery.data?.total ?? 0);
  const adminArchive = useArchivedAgentPage(filters, page, tab === "archived" && isAdmin && !f.unresolvedTeam);
  const activePage = tab === "discover" ? publicPage : tab === "archived" && isAdmin ? adminArchive : ownerPage;
  const { isLoading, isError, error, refetch } = activePage;
  const myAgents = ownerPage.data?.items ?? [];
  const { data: deletedAgents = [] } = useDeletedAgents(f.isAuthenticated && tab === "archived");
  const submitDraft = useSubmitDraft();
  const [deletingDraftId, setDeletingDraftId] = useState<string | null>(null);
  const qc = useQueryClient();

  const drafts = myAgents.filter((a) => ["draft", "pending", "rejected", "changes_requested"].includes(a.status ?? ""));
  const archivedAgents = tab === "archived" ? activePage.data?.items ?? [] : [];
  const total = activePage.data?.total ?? 0;

  const fields: FilterField[] = [
    ...commonFields(f, [
      { value: "discover", label: "Discover", count: tab === "discover" ? total : undefined },
      { value: "my", label: "My Agent Files", count: tab === "my" ? total : undefined },
      { value: "drafts", label: "My drafts", count: tab === "drafts" ? total : undefined },
      { value: "pending", label: "Pending review", count: tab === "pending" ? total : undefined },
      { value: "archived", label: "Archived", count: tab === "archived" ? total : undefined },
    ]),
    { key: "category", label: "Category", hint: "Agent File category", options: AGENT_CATEGORIES.map((c) => ({ value: c, label: c })) },
    { key: "harness", label: "Harness", hint: "Supported harness", options: harnessList.map((h) => ({ value: h.name, label: h.display_name })) },
  ];

  const visibleAgents = activePage.data?.items ?? [];


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
      <ResourceToolbar filters={f} fields={fields} noun="Agent Files" layout={layout} onLayoutChange={setLayout}
        actions={f.isAuthenticated ? (
          <Button size="sm" asChild className="h-[34px] shrink-0 rounded-[9px] px-3.5 text-xs font-medium">
            <Link to="/agents/builder">Create Agent File</Link>
          </Button>
        ) : undefined}
      />

      {tab === "discover" && !f.unresolvedTeam && submissionCount > 0 && (
        <StatusStrip
          icon={<span className="inline-grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-warning/10 font-mono text-[11px] font-semibold text-warning">D</span>}
          title="Your Agent File submissions · all teamspaces"
          subtitle={`${pendingCountQuery.data?.total ?? 0} pending review · ${rejectedCountQuery.data?.total ?? 0} rejected. Open My Agent Files to manage them.`}
          badge={<span className="rounded-full bg-light-yellow px-2.5 py-0.5 text-2xs font-medium text-dark-yellow">{submissionCount} item{submissionCount === 1 ? "" : "s"}</span>}
          action={<Button variant="outline" size="sm" onClick={() => f.update({ view: "my" })}>Open submissions</Button>}
        />
      )}

      {f.unresolvedTeam ? (
        <EmptyState icon={Bot} title="Teamspace unavailable"
          description="This teamspace is still loading or is no longer accessible to your account. Clear the team filter to browse other resources."
          actionLabel="Clear team filter" onAction={() => f.update({ team: undefined })} />
      ) : isLoading ? (
        <ListSkeleton view={layout} />
      ) : isError ? (
        <ErrorState message={error?.message} onRetry={() => refetch()} />
      ) : visibleAgents.length === 0 ? (
        <EmptyState
          icon={Bot}
          title={{ discover: "No Agent Files published yet", my: "No Agent Files yet", drafts: "No Agent File drafts", pending: "No pending reviews", archived: "No archived Agent Files" }[tab]}
          description={
            tab === "discover"
              ? hasFilters
                ? "No Agent Files match the active search and filters."
                : "No Agent Files have been submitted yet. Create one to start a draft for review."
              : { my: "Create your first Agent File using the builder.", drafts: "Create an Agent File and save a draft to continue it here.", pending: "Nothing waiting for review right now.", archived: "No archived Agent Files." }[tab]
          }
          actionLabel={hasFilters ? "Clear filters" : undefined}
          onAction={hasFilters ? f.clear : undefined}
        />
      ) : layout === "grid" ? (
        <CatalogGrid className="animate-in">
          {visibleAgents.map((agent, i) => (
            <AgentCatalogCard key={agent.id} agent={agent} className={`animate-in stagger-${Math.min(i + 1, 5)}`} />
          ))}
        </CatalogGrid>
      ) : (
        <ResourceList
          title="Agent File catalogue"
          subtitle="Approved and owner-visible Agent Files in the current view"
          count={`${compactNumber(total)} Agent Files`}
          columns={LIST_COLUMNS}
          headers={["Agent File", "Status", "Pulls", "Rating", "Version"]}
        >
          {visibleAgents.map((agent) => (
            <AgentListRow key={agent.id} agent={agent} />
          ))}
        </ResourceList>
      )}

      {!f.unresolvedTeam && !isLoading && !isError && <ResourcePager page={page} total={total} onPageChange={(next) => f.update({ page: next + 1 > 1 ? next + 1 : undefined })} />}

      {tab === "archived" && archivedAgents.length > 0 && layout === "list" && (
        <div className="mt-2 text-[10px] text-muted-foreground">
          Open an Agent File using its name to view details. Restore or delete it from the detail page.
        </div>
      )}

      {(tab === "my" || tab === "drafts") && drafts.length > 0 && (
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
                  {draft.status === "changes_requested" && (
                    <Button variant="outline" size="sm" className="h-7 text-xs"
                      onClick={() => draft.review_number
                        ? router.navigate({ to: "/review/$number", params: { number: String(draft.review_number) } })
                        : router.navigate({ to: "/agents/$agentId", params: { agentId: draft.id } })}>
                      {draft.review_number ? "Read feedback" : "Open Agent review"}
                    </Button>
                  )}
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
