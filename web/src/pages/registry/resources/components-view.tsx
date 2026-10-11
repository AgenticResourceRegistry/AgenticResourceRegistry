// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { Link, useRouter } from "@tanstack/react-router";
import { toast } from "sonner";
import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import { Puzzle, FileEdit, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { PickerSelect } from "@/components/ui/picker-select";
import {
  useRegistryList,
  useMyComponents,
  useComponentSubmit,
  useComponentSaveDraft,
  useComponentSubmitDraft,
  useComponentUpdateDraft,
  useStartEdit,
  useCancelEdit,
  useLoadSkillFolderDraft,
  useUpdateSkillFolderDraft,
  useSubmitSkillFolderDraft,
  useSkillVersionManifest,
  useRegistryItem,
  useComponentVersionDetail,
} from "@/hooks/use-api";
import { useOptionalAuth } from "@/hooks/use-auth";
import { useDeploymentConfig } from "@/hooks/use-deployment-config";
import { requiresFolderDelivery } from "@/lib/skill-folder-delivery";
import { registry, type RegistryType } from "@/lib/api";
import type { RegistryItem, ComponentVersionSummary } from "@/lib/types";
import {
  HOOK_EVENTS,
  HOOK_SCOPES,
  MCP_CATEGORIES,
  PROMPT_CATEGORIES,
  SANDBOX_RUNTIME_TYPES,
  SKILL_TASK_TYPES,
  SubmitComponentDialog,
} from "@/components/registry/submit-component-dialog";
import { ErrorState } from "@/components/shared/error-state";
import { EmptyState } from "@/components/shared/empty-state";
import { canonicalRouteParts } from "@/lib/registry-name";
import { compactNumber } from "@/lib/utils";
import { CatalogGrid } from "@/components/registry/registry-primitives";
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
  formatOption,
  monoCell,
  useResourceFilters,
  type ViewMode,
} from "./shared";
import type { ResourcesSearch } from "@/routes/_authed/resources/$type";
import type { FilterField } from "@/components/registry/filter-bar";

const TABS = ["discover", "my", "pending"] as const;
type FilterKey = "category" | "task_type" | "event" | "scope" | "runtime_type";
type TypeFilter = { key: FilterKey & keyof ResourcesSearch; label: string; options: string[] };

const NOUNS: Record<string, string> = {
  mcps: "MCP Clients",
  skills: "Skills",
  hooks: "Hooks",
  prompts: "Prompts",
  sandboxes: "Sandboxes",
};

const TYPE_FILTERS: Partial<Record<RegistryType, TypeFilter[]>> = {
  mcps: [{ key: "category", label: "Category", options: MCP_CATEGORIES }],
  skills: [{ key: "task_type", label: "Task type", options: SKILL_TASK_TYPES }],
  hooks: [
    { key: "event", label: "Event", options: HOOK_EVENTS },
    { key: "scope", label: "Scope", options: HOOK_SCOPES },
  ],
  prompts: [{ key: "category", label: "Category", options: PROMPT_CATEGORIES }],
  sandboxes: [{ key: "runtime_type", label: "Runtime", options: SANDBOX_RUNTIME_TYPES }],
};

const LIST_COLUMNS = "minmax(260px,1.7fr) 86px minmax(120px,.7fr) 100px";

function usageLabel(item: RegistryItem): string | undefined {
  return item.download_count != null ? `${compactNumber(item.download_count as number)} Agent Files` : undefined;
}

function ComponentCatalogCard({
  item,
  registryType,
  className,
}: {
  item: RegistryItem;
  registryType: RegistryType;
  className?: string;
}) {
  const canonical = canonicalRouteParts(item.namespace, item.slug);
  const usage = usageLabel(item);

  return (
    <ResourceCard
      item={item}
      glyph={registryType}
      labelled
      className={className}
      render={(p) =>
        canonical ? (
          <Link to="/components/$type/$namespace/$slug" params={{ type: registryType, ...canonical }} {...p} />
        ) : (
          <Link to="/components/$componentId" params={{ componentId: item.id }} search={{ type: registryType }} {...p} />
        )
      }
    >
      {usage && <CardFooter>{usage}</CardFooter>}
    </ResourceCard>
  );
}

function ComponentListRow({
  item,
  registryType,
  onClick,
}: {
  item: RegistryItem;
  registryType: RegistryType;
  onClick: () => void;
}) {
  return (
    <ResourceRow item={item} glyph={registryType} columns={LIST_COLUMNS} onClick={onClick}>
      <span className={monoCell}>{(item.version as string | undefined) ?? "-"}</span>
      <span className="text-[10px] text-muted-foreground">{usageLabel(item) ?? "-"}</span>
      <StatusBadges item={item} />
    </ResourceRow>
  );
}

export default function ComponentsView({ type: activeType }: { type: RegistryType }) {
  const router = useRouter();
  const f = useResourceFilters();
  const { ready: authReady, role } = useOptionalAuth();
  const { isAuthenticated } = f;
  const [layout, setLayout] = useState<ViewMode>("grid");
  const discoveryTab = TABS.find((t) => t === f.searchParams.view) ?? "discover";
  const [submitOpen, setSubmitOpen] = useState(false);
  const [editItem, setEditItem] = useState<RegistryItem | null>(null);
  const [folderSelection, setFolderSelection] = useState<{
    item: RegistryItem; action: "edit" | "submit"; versions: ComponentVersionSummary[]; versionId: string;
  } | null>(null);
  const [findingFolder, setFindingFolder] = useState(false);
  const { skillFolderDeliveryEnabled } = useDeploymentConfig();
  const selectedFolderVersion = folderSelection?.versions.find((version) => version.id === folderSelection.versionId) ?? null;
  const selectedFolderManifest = useSkillVersionManifest(
    folderSelection?.action === "submit" ? folderSelection.item.id : undefined,
    folderSelection?.action === "submit" ? folderSelection.versionId : undefined,
  );
  const folderSubmitBlocked = folderSelection?.action === "submit" && !skillFolderDeliveryEnabled &&
    !!selectedFolderManifest.data && requiresFolderDelivery(selectedFolderVersion, selectedFolderManifest.data);
  const folderConfirmRef = useRef<HTMLButtonElement>(null);
  const attemptedFolderLink = useRef<string | null>(null);
  const [folderLinkError, setFolderLinkError] = useState("");
  const [folderLinkRetry, setFolderLinkRetry] = useState(0);
  const linkedListingId = activeType === "skills" ? f.searchParams.folderListingId : undefined;
  const linkedVersionId = activeType === "skills" ? f.searchParams.folderVersionId : undefined;
  const linkedVersion = activeType === "skills" ? f.searchParams.folderVersion : undefined;
  const { data: linkedListing, isError: linkedListingError, refetch: refetchListing } = useRegistryItem("skills", linkedListingId);
  const { data: linkedDraft, isError: linkedDraftError, refetch: refetchDraft } = useComponentVersionDetail(
    linkedVersion ? "skills" : undefined, linkedListingId, linkedVersion ?? null,
  );

  const typeFilters = TYPE_FILTERS[activeType] ?? [];
  const noun = NOUNS[activeType] ?? activeType;
  const registryFilters: Record<string, string> = {
    ...(f.debouncedSearch ? { search: f.debouncedSearch } : {}),
    ...(f.searchParams.namespace ? { namespace: f.searchParams.namespace } : {}),
    ...(f.selectedTeam ? { team_id: f.selectedTeam.id } : {}),
  };
  for (const filter of typeFilters) {
    const value = f.searchParams[filter.key];
    if (value) registryFilters[filter.key] = value;
  }

  const { data, isLoading, isError, error, refetch } = useRegistryList(activeType, registryFilters);

  const { data: myItems } = useMyComponents(activeType, isAuthenticated);
  const myDrafts = useMemo(
    () =>
      isAuthenticated
        ? (myItems ?? []).filter((i) => activeType === "skills" || ["draft", "pending", "rejected", "archived"].includes(i.status ?? ""))
        : [],
    [isAuthenticated, myItems, activeType],
  );
  const pendingItems = useMemo(
    () => (isAuthenticated ? (myItems ?? []).filter((i) => i.status === "pending") : []),
    [isAuthenticated, myItems],
  );

  const submitMutation = useComponentSubmit(activeType);
  const saveDraftMutation = useComponentSaveDraft(activeType);
  const submitDraftMutation = useComponentSubmitDraft(activeType);
  const updateDraftMutation = useComponentUpdateDraft(activeType);
  const startEditMutation = useStartEdit(activeType);
  const cancelEditMutation = useCancelEdit(activeType);
  const loadFolderMutation = useLoadSkillFolderDraft();
  const updateFolderMutation = useUpdateSkillFolderDraft();
  const submitFolderMutation = useSubmitSkillFolderDraft();

  // Deep link from the skill detail page to one exact folder draft.
  useEffect(() => {
    if (!linkedListingId || !linkedVersionId || !linkedVersion || !linkedListing || !linkedDraft) return;
    const key = `${linkedListingId}/${linkedVersionId}/${folderLinkRetry}`;
    if (attemptedFolderLink.current === key) return;
    attemptedFolderLink.current = key;
    let active = true;
    if (linkedDraft.id !== linkedVersionId || linkedDraft.version !== linkedVersion ||
        !["draft", "rejected"].includes(linkedDraft.status) || linkedListing.user_permission !== "owner") {
      setFolderLinkError("The exact draft is not editable or no longer belongs to this listing. Refresh its Versions tab.");
      return;
    }
    setFolderLinkError("");
    void loadFolderMutation.mutateAsync({ listingId: linkedListingId, versionId: linkedVersionId })
      .then((snapshot) => {
        if (!active) return;
        setEditItem({ ...linkedListing, version: linkedVersion, description: linkedDraft.description,
          task_type: linkedDraft.task_type ?? linkedListing.task_type,
          supported_harnesses: linkedDraft.supported_harnesses,
          delivery_mode: "registry_direct", folder_version_id: linkedVersionId,
          folder_revision: snapshot.revision, skill_md_content: snapshot.skill_md_content,
          extra_files: snapshot.extra_files });
        setSubmitOpen(true);
      }).catch((error: unknown) => {
        if (active) setFolderLinkError(error instanceof Error ? error.message : "The draft could not be loaded. Retry this exact version.");
      });
    return () => { active = false; };
  }, [linkedListingId, linkedVersionId, linkedVersion, linkedListing, linkedDraft, loadFolderMutation.mutateAsync, folderLinkRetry]);

  const editItemRef = useRef(editItem);
  editItemRef.current = editItem;

  // Release the edit lock on a pending item if the tab closes mid-edit; fetch+keepalive survives unload.
  useEffect(() => {
    const handleBeforeUnload = () => {
      const item = editItemRef.current;
      if (item?.status === "pending") {
        const token = sessionStorage.getItem("observal_access_token");
        fetch(`/api/v1/${activeType}/${item.id}/cancel-edit`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          keepalive: true,
        });
      }
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [activeType]);

  const visibleItems = { discover: data ?? [], my: myDrafts, pending: pendingItems }[discoveryTab];

  const fields: FilterField[] = [
    ...commonFields(f, [
      { value: "discover", label: "Discover" },
      { value: "my", label: "My submissions", count: myDrafts.length },
      { value: "pending", label: "Pending review", count: pendingItems.length },
    ]),
    ...typeFilters.map((filter) => ({
      key: filter.key,
      label: filter.label,
      hint: `${filter.label} of the ${noun.toLowerCase()}`,
      options: filter.options.map((o) => ({ value: o, label: formatOption(o) })),
    })),
  ];

  const handleRowClick = useCallback(
    (id: string) => router.navigate({ to: "/components/$componentId", params: { componentId: id }, search: { type: activeType } }),
    [router, activeType],
  );

  async function openSubmission(item: RegistryItem, action: "edit" | "submit") {
    if (activeType === "skills") {
      setFindingFolder(true);
      try {
        const first = await registry.listComponentVersions("skills", item.id, 1, 100);
        const allVersions = [...first.items];
        // Only page on an explicit owner action. Never silently pick a draft
        // from a partial history or fetch a version list for every catalog card.
        for (let page = 2; allVersions.length < first.total && page <= 10; page++) {
          const next = await registry.listComponentVersions("skills", item.id, page, 100);
          if (next.items.length === 0) break;
          allVersions.push(...next.items);
        }
        if (allVersions.length < first.total) {
          toast.error("More than 1,000 skill versions. Open the exact draft from its Versions page or use the CLI.");
          return;
        }
        const folders = allVersions.filter((version) =>
          version.delivery_mode === "registry_direct" && ["draft", "rejected"].includes(version.status),
        );
        if (folders.length > 0) {
          setFolderSelection({ item, action, versions: folders, versionId: folders[0].id });
          return;
        }
        if (allVersions.some((version) => version.delivery_mode === "registry_direct" && version.status === "pending")) {
          toast.error("Withdraw the pending folder version before editing or resubmitting it");
          return;
        }
        if (item.status === "approved") {
          toast.error("No saved folder draft found for this skill");
          return;
        }
      } catch (error) {
        toast.error(error instanceof Error ? error.message : "Could not inspect skill versions");
        return;
      } finally {
        setFindingFolder(false);
      }
    }
    if (action === "submit") {
      submitDraftMutation.mutate(item.id);
    } else if (item.status === "pending") {
      startEditMutation.mutate(item.id, {
        onSuccess: () => { setEditItem(item); setSubmitOpen(true); },
      });
    } else {
      setEditItem(item);
      setSubmitOpen(true);
    }
  }

  async function continueFolderSelection() {
    if (!folderSelection) return;
    const { item, action, versionId, versions } = folderSelection;
    const version = versions.find((entry) => entry.id === versionId);
    if (!version) return;
    try {
      if (action === "submit") {
        const manifest = await registry.getSkillVersionManifest(item.id, version.id);
        await submitFolderMutation.mutateAsync({ listingId: item.id, versionId: version.id, observedRevision: manifest.revision });
      } else {
        const snapshot = await loadFolderMutation.mutateAsync({ listingId: item.id, versionId: version.id });
        setEditItem({
          ...item, version: version.version, description: version.description,
          task_type: version.task_type ?? item.task_type,
          supported_harnesses: version.supported_harnesses,
          delivery_mode: "registry_direct", folder_version_id: version.id,
          folder_revision: snapshot.revision,
          skill_md_content: snapshot.skill_md_content, extra_files: snapshot.extra_files,
        });
        setSubmitOpen(true);
      }
      setFolderSelection(null);
    } catch {
      // The mutation displays the server error; keep the version choice available.
    }
  }

  const hasFilters = discoveryTab === "discover" && !!(f.search || f.searchParams.namespace || f.searchParams.team || typeFilters.some((t) => f.searchParams[t.key]));

  const closeDialog = () => {
    setSubmitOpen(false);
    setEditItem(null);
  };

  return (
    <>
      <ResourceToolbar
        filters={f}
        fields={fields}
        noun={noun}
        layout={layout}
        onLayoutChange={setLayout}
        actions={
          authReady && role && (
            <Button
              size="sm"
              className="h-[34px] shrink-0 rounded-[9px] bg-primary px-3.5 text-xs font-medium text-primary-foreground"
              onClick={() => {
                setEditItem(null);
                setSubmitOpen(true);
              }}
            >
              Create
            </Button>
          )
        }
      />

      {discoveryTab === "discover" && isLoading ? (
        <ListSkeleton view={layout} />
      ) : discoveryTab === "discover" && isError ? (
        <ErrorState message={error?.message} onRetry={() => refetch()} />
      ) : visibleItems.length === 0 ? (
        <EmptyState
          icon={Puzzle}
          title={`No ${noun.toLowerCase()} found`}
          description={
            hasFilters
              ? `No ${noun.toLowerCase()} match the active search and filters.`
              : `No ${noun.toLowerCase()} have been registered yet.`
          }
          actionLabel="Back to Registry"
          actionHref="/"
        />
      ) : layout === "grid" ? (
        <CatalogGrid className="animate-in">
          {visibleItems.map((item, i) => (
            <ComponentCatalogCard
              key={item.id}
              item={item}
              registryType={activeType}
              className={`animate-in stagger-${Math.min(i + 1, 5)}`}
            />
          ))}
        </CatalogGrid>
      ) : (
        <ResourceList
          title="Component catalogue"
          subtitle="Reusable building blocks matching the current type and filters"
          count={`${compactNumber(visibleItems.length)} ${noun.toLowerCase()}`}
          columns={LIST_COLUMNS}
          headers={["Component", "Version", "Used by", "Status"]}
        >
          {visibleItems.map((item) => (
            <ComponentListRow key={item.id} item={item} registryType={activeType} onClick={() => handleRowClick(item.id)} />
          ))}
        </ResourceList>
      )}

      {discoveryTab === "my" && myDrafts.length > 0 && (
        <SubmissionsPanel title="Drafts &amp; submissions">
          {myDrafts.map((item) => (
            <SubmissionRow
              key={item.id}
              item={item}
              glyph={activeType}
              actions={
                <>
                  {(item.status === "draft" || item.status === "rejected" || item.status === "pending" || (activeType === "skills" && item.status === "approved")) && (
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-7 text-xs"
                      disabled={findingFolder || startEditMutation.isPending || loadFolderMutation.isPending}
                      onClick={() => void openSubmission(item, "edit")}
                    >
                      <FileEdit className="h-3 w-3 mr-1" />
                      {item.status === "approved" ? "Manage drafts" : "Edit"}
                    </Button>
                  )}
                  {(item.status === "draft" || item.status === "rejected" || (activeType === "skills" && item.status === "approved")) && (
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-7 text-xs"
                      onClick={() => void openSubmission(item, "submit")}
                      disabled={findingFolder || submitDraftMutation.isPending || submitFolderMutation.isPending}
                    >
                      <Send className="h-3 w-3 mr-1" />
                      {item.status === "rejected" ? "Resubmit" : item.status === "approved" ? "Submit draft" : "Submit"}
                    </Button>
                  )}
                </>
              }
            />
          ))}
        </SubmissionsPanel>
      )}

      {(linkedListingError || linkedDraftError || folderLinkError) && linkedVersionId && (
        <div role="alert" className="rounded-md border border-destructive p-3 text-sm text-destructive">
          {folderLinkError || "The exact folder draft is unavailable. Check permissions and refresh the Versions tab."}
          <Button type="button" variant="outline" className="ml-3" onClick={() => void Promise.all([refetchListing(), refetchDraft()]).finally(() => setFolderLinkRetry((value) => value + 1))}>Retry draft</Button>
        </div>
      )}
      <Dialog open={!!folderSelection} onOpenChange={(open) => { if (!open) setFolderSelection(null); }}>
        <DialogContent onOpenAutoFocus={(event) => { event.preventDefault(); folderConfirmRef.current?.focus(); }}>
          <DialogHeader><DialogTitle>Choose the exact folder version</DialogTitle></DialogHeader>
          {folderSelection && (
            <div className="space-y-4">
              <p className="text-sm text-muted-foreground">{folderSelection.item.name}: select the saved version to {folderSelection.action}. No other version will be changed.</p>
              <PickerSelect
                value={folderSelection.versionId}
                onValueChange={(versionId) => setFolderSelection((current) => current ? { ...current, versionId } : null)}
                options={folderSelection.versions.map((version) => ({
                  value: version.id, label: `${version.version} · ${version.status} · ${version.id.slice(0, 8)}`,
                }))}
              />
              {folderSubmitBlocked && <p role="status" className="text-sm text-muted-foreground">
                Complete-folder review is not enabled on this server. This draft is saved; you can continue editing it.
              </p>}
              {folderSelection.action === "submit" && selectedFolderManifest.isError &&
                <div role="alert" className="flex items-center gap-2 text-sm text-destructive">
                  Could not load this exact version.
                  <Button variant="outline" size="sm" onClick={() => void selectedFolderManifest.refetch()}>Retry</Button>
                </div>}
              <Button
                ref={folderConfirmRef}
                onClick={() => void continueFolderSelection()}
                disabled={loadFolderMutation.isPending || submitFolderMutation.isPending ||
                  (folderSelection.action === "submit" && (!selectedFolderManifest.data || folderSubmitBlocked))}
              >
                {folderSelection.action === "edit" ? "Edit selected version" : "Submit selected version"}
              </Button>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {isAuthenticated && (
        <SubmitComponentDialog
          key={editItem?.id ?? "new"}
          open={submitOpen}
          onOpenChange={(v) => {
            if (!v && editItem?.status === "pending") cancelEditMutation.mutate(editItem.id);
            setSubmitOpen(v);
            if (!v) setEditItem(null);
          }}
          type={activeType}
          editItem={editItem as Record<string, unknown> | null}
          onSubmit={(body) => {
            if (editItem?.status === "pending") {
              updateDraftMutation.mutate({ id: editItem.id, body }, { onSuccess: closeDialog });
            } else if (editItem) {
              void (async () => {
                try {
                  await updateDraftMutation.mutateAsync({ id: editItem.id, body });
                  await submitDraftMutation.mutateAsync(editItem.id);
                  closeDialog();
                } catch { /* mutation hooks show the error; leave the editor open */ }
              })();
            } else {
              submitMutation.mutate(body, { onSuccess: () => setSubmitOpen(false) });
            }
          }}
          onSaveDraft={(body) => saveDraftMutation.mutate(body, { onSuccess: () => setSubmitOpen(false) })}
          onUpdateDraft={(id, body) => {
            if (editItem?.folder_version_id && editItem.folder_revision) {
              updateFolderMutation.mutate({
                listingId: id, versionId: String(editItem.folder_version_id),
                observedRevision: String(editItem.folder_revision), body,
              }, { onSuccess: closeDialog });
            } else {
              updateDraftMutation.mutate({ id, body }, { onSuccess: closeDialog });
            }
          }}
          isSubmitting={submitMutation.isPending || submitDraftMutation.isPending || submitFolderMutation.isPending}
          isSavingDraft={saveDraftMutation.isPending || updateDraftMutation.isPending || updateFolderMutation.isPending}
        />
      )}
    </>
  );
}
