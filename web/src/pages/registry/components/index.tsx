// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { Link, useRouter, useSearch } from "@tanstack/react-router";
import type { ComponentsSearch } from "@/routes/_authed/components/index";
import { toast } from "sonner";
import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import {
  Search,
  Puzzle,
  Plus,
  Send,
  FileEdit,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { PickerSelect } from "@/components/ui/picker-select";
import { UserSearchInput } from "@/components/shared/user-search-input";
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
  useTeams,
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
import { PageHeader } from "@/components/layouts/page-header";
import { TableSkeleton, CardSkeleton } from "@/components/shared/skeleton-layouts";
import { ErrorState } from "@/components/shared/error-state";
import { EmptyState } from "@/components/shared/empty-state";
import { StatusBadge } from "@/components/registry/status-badge";
import { RecommendedBadge } from "@/components/registry/recommended-badge";
import { ForkMarkers } from "@/components/registry/fork-provenance";
import { EntityGlyph, toEntityKind } from "@/components/registry/entity-glyph";
import { RegistryName } from "@/components/registry/registry-name";
import { registryItemPath, canonicalRouteParts } from "@/lib/registry-name";
import { compactNumber } from "@/lib/utils";
import {
  TypeTabs,
  ViewToggle,
  RegistryToolbar,
  ToolbarSpacer,
  RegistryNote,
  CatalogGrid,
} from "@/components/registry/registry-primitives";

/* ────────────────────────────────────────────────────────── */
/*  Constants                                                  */
/* ────────────────────────────────────────────────────────── */

type ViewMode = "grid" | "list";
type DiscoveryTab = "discover" | "my" | "pending";
type FilterKey = "category" | "task_type" | "event" | "scope" | "runtime_type";
type TypeFilter = { key: FilterKey; label: string; options: string[] };

/** Registry type tabs — labels match the mockup exactly. */
const TYPES: { value: RegistryType; label: string; singular: string }[] = [
  { value: "mcps", label: "MCP servers", singular: "MCP server" },
  { value: "skills", label: "Skills", singular: "Skill" },
  { value: "hooks", label: "Hooks", singular: "Hook" },
  { value: "prompts", label: "Prompts", singular: "Prompt" },
  { value: "sandboxes", label: "Sandboxes", singular: "Sandbox" },
];

const SINGULAR: Record<string, string> = Object.fromEntries(
  TYPES.map((t) => [t.value, t.singular]),
);
const PLURAL: Record<string, string> = Object.fromEntries(
  TYPES.map((t) => [t.value, t.label]),
);

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

function formatOption(value: string): string {
  return value.replaceAll("-", " ").replace(/\b\w/g, (l) => l.toUpperCase());
}

/** Map registry plural type to entity-glyph kind. */
function typeToGlyphKind(registryType: RegistryType): string {
  const t = registryType.toLowerCase();
  if (t.endsWith("es")) return t.slice(0, -2);
  if (t.endsWith("s")) return t.slice(0, -1);
  return t;
}

/* ────────────────────────────────────────────────────────── */
/*  Component catalog card (mockup: registryComponentCard)     */
/* ────────────────────────────────────────────────────────── */

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
  const status = (item.status as string | undefined) ?? "approved";
  const handle = item.namespace && item.slug
    ? `${item.namespace}/${item.slug}`
    : item.qualified_name || item.name;
  const version = item.version as string | undefined;
  const description = typeof item.description === "string" ? item.description : undefined;
  const usage = item.download_count != null
    ? `${compactNumber(item.download_count as number)} agents`
    : undefined;
  const glyphKind = typeToGlyphKind(registryType);

  const cls = [
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
        <EntityGlyph type={glyphKind} size="sm" labelled />
        <div className="flex items-center gap-1.5">
          {item.is_recommended && <RecommendedBadge />}
          <ForkMarkers provenance={item.forked_from} count={item.fork_count} />
          <StatusBadge status={status} />
        </div>
      </div>

      {/* Title */}
      <div className="mt-3 text-sm font-medium">{item.name}</div>

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

      {usage && (
        <div className="mt-3.5 border-t border-border pt-3 text-[10px] text-muted-foreground">
          {usage}
        </div>
      )}
    </>
  );

  if (canonical) {
    return (
      <Link to="/components/$type/$namespace/$slug" params={{ type: registryType, ...canonical }} className={cls}>
        {body}
      </Link>
    );
  }
  return (
    <Link to="/components/$componentId" params={{ componentId: item.id }} search={{ type: registryType }} className={cls}>
      {body}
    </Link>
  );
}

/* ────────────────────────────────────────────────────────── */
/*  Component list row (mockup: componentListRow)              */
/*  Grid: minmax(260px,1.7fr) 105px 86px minmax(120px,.7fr) 100px */
/* ────────────────────────────────────────────────────────── */

function ComponentListRow({
  item,
  registryType,
  onClick,
}: {
  item: RegistryItem;
  registryType: RegistryType;
  onClick: () => void;
}) {
  const status = (item.status as string | undefined) ?? "approved";
  const handle = item.namespace && item.slug
    ? `${item.namespace}/${item.slug}`
    : item.qualified_name || item.name;
  const glyphKind = typeToGlyphKind(registryType);
  const version = (item.version as string | undefined) ?? "-";
  const usage = item.download_count != null
    ? `${compactNumber(item.download_count as number)} agents`
    : "-";

  return (
    <div
      className="grid min-h-[66px] cursor-pointer items-center gap-3 border-t border-border px-[22px] py-3 transition-colors first:border-t-0 hover:bg-surface-raised"
      style={{ gridTemplateColumns: "minmax(260px,1.7fr) 86px minmax(120px,.7fr) 100px" }}
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") onClick(); }}
    >
      {/* Component identity */}
      <div className="flex items-center gap-[11px] min-w-0">
        <EntityGlyph type={glyphKind} size="sm" labelled={false} />
        <div className="min-w-0">
          <strong className="block truncate text-xs font-medium">{item.name}</strong>
          <span className="block truncate mt-0.5 font-mono text-[9px] text-muted-foreground">{handle}</span>
        </div>
      </div>

      {/* Version */}
      <span className="font-mono text-[10px] text-muted-foreground">{version}</span>

      {/* Used by */}
      <span className="text-[10px] text-muted-foreground">{usage}</span>

      {/* Status */}
      <div className="flex items-center gap-1.5">
        {item.is_recommended && <RecommendedBadge />}
        <ForkMarkers provenance={item.forked_from} count={item.fork_count} />
        <StatusBadge status={status} />
      </div>
    </div>
  );
}

/* ────────────────────────────────────────────────────────── */
/*  Main page                                                  */
/* ────────────────────────────────────────────────────────── */

export default function ComponentsPage(): React.JSX.Element {
  const router = useRouter();
  const searchParams: ComponentsSearch = useSearch({ from: "/_authed/components/" });
  const { ready: authReady, role, isAuthenticated } = useOptionalAuth();
  const { data: teams = [] } = useTeams(isAuthenticated);
  const activeType: RegistryType = searchParams.type ?? "mcps";
  const [search, setSearch] = useState(searchParams.search ?? "");
  const [debouncedSearch, setDebouncedSearch] = useState(searchParams.search ?? "");
  const [publisherQuery, setPublisherQuery] = useState(searchParams.namespace ? `@${searchParams.namespace}` : "");
  const [view, setView] = useState<ViewMode>("grid");
  const [discoveryTab, setDiscoveryTab] = useState<DiscoveryTab>("discover");
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
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const attemptedFolderLink = useRef<string | null>(null);
  const [folderLinkError, setFolderLinkError] = useState("");
  const [folderLinkRetry, setFolderLinkRetry] = useState(0);
  const linkedListingId = activeType === "skills" ? searchParams.folderListingId : undefined;
  const linkedVersionId = activeType === "skills" ? searchParams.folderVersionId : undefined;
  const linkedVersion = activeType === "skills" ? searchParams.folderVersion : undefined;
  const { data: linkedListing, isError: linkedListingError } = useRegistryItem("skills", linkedListingId);
  const { data: linkedDraft, isError: linkedDraftError } = useComponentVersionDetail(
    linkedVersion ? "skills" : undefined, linkedListingId, linkedVersion ?? null,
  );

  useEffect(() => {
    timerRef.current = setTimeout(() => setDebouncedSearch(search), 300);
    return () => { if (timerRef.current) clearTimeout(timerRef.current); };
  }, [search]);

  useEffect(() => {
    setPublisherQuery(searchParams.namespace ? `@${searchParams.namespace}` : "");
  }, [searchParams.namespace]);

  const typeFilters = TYPE_FILTERS[activeType] ?? [];
  const selectedTeam = teams.find((team) => team.handle === searchParams.team);
  const registryFilters: Record<string, string> = {
    ...(debouncedSearch ? { search: debouncedSearch } : {}),
    ...(searchParams.namespace ? { namespace: searchParams.namespace } : {}),
    ...(selectedTeam ? { team_id: selectedTeam.id } : {}),
  };
  for (const filter of typeFilters) {
    const value = searchParams[filter.key];
    if (value) registryFilters[filter.key] = value;
  }

  const { data, isLoading, isError, error, refetch } = useRegistryList(activeType, registryFilters);

  const { data: myItems } = useMyComponents(activeType, isAuthenticated);
  const myDrafts = useMemo(
    () => isAuthenticated
      ? (myItems ?? []).filter((i) => activeType === "skills" || ["draft", "pending", "rejected", "archived"].includes(i.status ?? ""))
      : [],
    [isAuthenticated, myItems, activeType],
  );
  const pendingItems = useMemo(
    () => isAuthenticated ? (myItems ?? []).filter((i) => i.status === "pending") : [],
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
  const activeTypeRef = useRef(activeType);
  activeTypeRef.current = activeType;

  useEffect(() => {
    const handleBeforeUnload = () => {
      const item = editItemRef.current;
      if (item?.status === "pending") {
        const type = activeTypeRef.current;
        const token = sessionStorage.getItem("observal_access_token");
        fetch(`/api/v1/${type}/${item.id}/cancel-edit`, {
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
  }, []);

  const items = useMemo(() => data ?? [], [data]);

  /* Active tab content */
  const visibleItems = useMemo(() => {
    switch (discoveryTab) {
      case "my":
        return myDrafts;
      case "pending":
        return pendingItems;
      case "discover":
      default:
        return items;
    }
  }, [discoveryTab, items, myDrafts, pendingItems]);

  /* Tab data */
  const discoveryTabData = useMemo(() => [
    { value: "discover", label: "Discover", count: undefined },
    { value: "my", label: "My submissions", count: myDrafts.length || undefined },
    { value: "pending", label: "Pending review", count: pendingItems.length || undefined },
  ], [myDrafts.length, pendingItems.length]);

  const typeTabData = useMemo(() =>
    TYPES.map((t) => ({ value: t.value, label: t.label, count: undefined })),
    [],
  );

  const handleRowClick = useCallback(
    (id: string) => {
      router.navigate({ to: "/components/$componentId", params: { componentId: id }, search: { type: activeType } });
    },
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

  function updateFilters(next: Partial<typeof searchParams>) {
    router.navigate({
      to: "/components",
      search: { ...searchParams, ...next },
      replace: true,
    });
  }

  function clearFilters() {
    setSearch("");
    setDebouncedSearch("");
    setPublisherQuery("");
    updateFilters({
      search: undefined,
      namespace: undefined,
      team: undefined,
      category: undefined,
      task_type: undefined,
      event: undefined,
      scope: undefined,
      runtime_type: undefined,
    });
  }

  const hasFilters = !!(
    search ||
    searchParams.namespace ||
    searchParams.team ||
    typeFilters.some((filter) => searchParams[filter.key])
  );

  const typeSingular = SINGULAR[activeType] ?? activeType;
  const typePlural = PLURAL[activeType] ?? activeType;

  return (
    <>
      <PageHeader
        title="Components"
        breadcrumbs={[
          { label: "Registry", href: "/" },
          { label: "Components" },
        ]}
      />

      <div className="page-body w-full mx-auto">
        <section aria-labelledby="component-view-label" className="mb-3.5">
          <p id="component-view-label" className="mb-1.5 text-2xs font-medium uppercase tracking-[0.06em] text-muted-foreground">
            View
          </p>
          <TypeTabs
            tabs={discoveryTabData}
            active={discoveryTab}
            onTabChange={(v) => setDiscoveryTab(v as DiscoveryTab)}
          />
        </section>

        <section aria-labelledby="component-type-label" className="mb-3.5">
          <p id="component-type-label" className="mb-1.5 text-2xs font-medium uppercase tracking-[0.06em] text-muted-foreground">
            Component type
          </p>
          <TypeTabs
            tabs={typeTabData}
            active={activeType}
            onTabChange={(v) => {
              updateFilters({
                type: v as RegistryType,
                category: undefined,
                task_type: undefined,
                event: undefined,
                scope: undefined,
                runtime_type: undefined,
              });
            }}
          />
        </section>

        {/* ── Toolbar ── */}
        <RegistryToolbar>
          <div className="relative w-[min(360px,100%)] shrink-[2]">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <input
              aria-label={`Search ${typePlural}`}
              type="text"
              placeholder={`Search ${typePlural}`}
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                updateFilters({ search: e.target.value || undefined });
              }}
              className="h-[34px] w-full min-w-0 rounded-[9px] border border-border bg-transparent pl-9 pr-3 text-xs text-foreground outline-none placeholder:text-muted-foreground focus:ring-1 focus:ring-ring"
            />
          </div>
          {isAuthenticated && (
            <>
              <PickerSelect
                value={searchParams.team ?? ""}
                onValueChange={(value) => updateFilters({ team: value || undefined })}
                options={[
                  { value: "", label: "All visible teamspaces" },
                  ...teams.map((team) => ({ value: team.handle, label: `Team: ${team.name}` })),
                ]}
                placeholder="Teamspace"
                className="min-w-[190px] w-auto"
                inputClassName="h-[34px]"
              />
              <UserSearchInput
                value={publisherQuery}
                onValueChange={(value) => {
                  setPublisherQuery(value);
                  if (searchParams.namespace && value !== searchParams.namespace && value !== `@${searchParams.namespace}`) {
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
            </>
          )}
          {typeFilters.map((filter) => (
            <PickerSelect
              key={filter.key}
              value={searchParams[filter.key] ?? ""}
              onValueChange={(value) => updateFilters({ [filter.key]: value || undefined })}
              options={[
                { value: "", label: `Any ${filter.label.toLowerCase()}` },
                ...filter.options.map((option) => ({ value: option, label: formatOption(option) })),
              ]}
              placeholder={filter.label}
              className="min-w-[145px] w-auto"
              inputClassName="h-[34px]"
            />
          ))}
          <ToolbarSpacer />
          {authReady && role && (
            <Button
              size="sm"
              className="h-[34px] shrink-0 rounded-[9px] bg-primary px-3.5 text-xs font-medium text-primary-foreground"
              onClick={() => { setEditItem(null); setSubmitOpen(true); }}
            >
              Create
            </Button>
          )}
          <ViewToggle view={view} onViewChange={setView} />
        </RegistryToolbar>

        {/* ── Active filter chips ── */}
        {hasFilters && (
          <div className="mb-3.5 flex min-h-7 items-center gap-2 flex-wrap" aria-label="Active filters">
            {searchParams.team && (
              <Button variant="secondary" size="sm" className="h-7 gap-1 px-2 text-xs" onClick={() => updateFilters({ team: undefined })}>
                Team: {selectedTeam?.name ?? searchParams.team}
                <X className="h-3 w-3" />
              </Button>
            )}
            {searchParams.namespace && (
              <Button variant="secondary" size="sm" className="h-7 gap-1 px-2 text-xs" onClick={() => updateFilters({ namespace: undefined })}>
                Publisher: @{searchParams.namespace}
                <X className="h-3 w-3" />
              </Button>
            )}
            {typeFilters.map((filter) => {
              const value = searchParams[filter.key];
              if (!value) return null;
              return (
                <Button key={filter.key} variant="secondary" size="sm" className="h-7 gap-1 px-2 text-xs" onClick={() => updateFilters({ [filter.key]: undefined })}>
                  {filter.label}: {formatOption(value)}
                  <X className="h-3 w-3" />
                </Button>
              );
            })}
            <Button variant="ghost" size="sm" className="h-7 px-2 text-xs text-muted-foreground" onClick={clearFilters}>
              Clear all
            </Button>
          </div>
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
        ) : visibleItems.length === 0 ? (
          <EmptyState
            icon={Puzzle}
            title={`No ${typePlural.toLowerCase()} found`}
            description={
              hasFilters
                ? `No ${typePlural.toLowerCase()} match the active search and filters.`
                : `No ${typePlural.toLowerCase()} have been registered yet.`
            }
            actionLabel="Back to Registry"
            actionHref="/"
          />
        ) : view === "grid" ? (
          /* ── Grid view ── */
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
          /* ── List view ── */
          <section className="overflow-hidden rounded-xl bg-card shadow-sm animate-in">
            {/* Table head */}
            <div className="flex items-center justify-between border-b border-border px-[22px] py-4">
              <div>
                <div className="text-sm font-medium">Component catalogue</div>
                <div className="mt-[3px] text-2xs text-muted-foreground">
                  Reusable building blocks matching the current type and filters
                </div>
              </div>
              <span className="font-mono text-xs text-muted-foreground">
                {compactNumber(visibleItems.length)} {typePlural.toLowerCase()}
              </span>
            </div>

            {/* Column headers */}
            <div
              className="grid min-h-[40px] items-center gap-3 border-b border-border px-[22px] text-2xs font-medium uppercase tracking-[0.05em] text-muted-foreground"
              style={{ gridTemplateColumns: "minmax(260px,1.7fr) 86px minmax(120px,.7fr) 100px" }}
            >
              <span>Component</span>
              <span>Version</span>
              <span>Used by</span>
              <span>Status</span>
            </div>

            {/* Rows */}
            <div className="overflow-x-auto">
              {visibleItems.map((item) => (
                <ComponentListRow
                  key={item.id}
                  item={item}
                  registryType={activeType}
                  onClick={() => handleRowClick(item.id)}
                />
              ))}
            </div>
          </section>
        )}

        {/* ── Inline submissions (My submissions tab) ── */}
        {discoveryTab === "my" && myDrafts.length > 0 && (
          <section className="mt-3.5 rounded-xl bg-card p-5 shadow-sm">
            <div className="mb-4 text-sm font-medium">Drafts &amp; submissions</div>
            <div className="divide-y divide-border">
              {myDrafts.map((item) => (
                <div key={item.id} className="flex items-center gap-4 py-3">
                  <EntityGlyph type={typeToGlyphKind(activeType)} size="sm" labelled={false} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-start gap-2">
                      <RegistryName item={item} nameClassName="text-xs font-medium" />
                      <StatusBadge status={item.status ?? "draft"} />
                    </div>
                    {item.status === "rejected" && item.rejection_reason && (
                      <p className="text-[10px] text-destructive mt-0.5">
                        Rejected: {item.rejection_reason}
                      </p>
                    )}
                    {item.description && (
                      <p className="truncate text-[10px] text-muted-foreground mt-0.5">
                        {item.description}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
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
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}
      </div>

      {(linkedListingError || linkedDraftError || folderLinkError) && linkedVersionId && (
        <div role="alert" className="rounded-md border border-destructive p-3 text-sm text-destructive">
          {folderLinkError || "The exact folder draft is unavailable. Check permissions and refresh the Versions tab."}
          <Button type="button" variant="outline" className="ml-3" onClick={() => setFolderLinkRetry((value) => value + 1)}>Retry draft</Button>
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

      {isAuthenticated && <SubmitComponentDialog
        key={editItem?.id ?? "new"}
        open={submitOpen}
        onOpenChange={(v) => {
          if (!v && editItem?.status === "pending") {
            cancelEditMutation.mutate(editItem.id);
          }
          setSubmitOpen(v);
          if (!v) setEditItem(null);
        }}
        type={activeType}
        editItem={editItem as Record<string, unknown> | null}
        onSubmit={(body) => {
          if (editItem) {
            if (editItem.status === "pending") {
              updateDraftMutation.mutate({ id: editItem.id, body }, {
                onSuccess: () => { setSubmitOpen(false); setEditItem(null); },
              });
            } else {
              void (async () => {
                try {
                  await updateDraftMutation.mutateAsync({ id: editItem.id, body });
                  await submitDraftMutation.mutateAsync(editItem.id);
                  setSubmitOpen(false);
                  setEditItem(null);
                } catch { /* mutation hooks show the error; leave the editor open */ }
              })();
            }
          } else {
            submitMutation.mutate(body, {
              onSuccess: () => setSubmitOpen(false),
            });
          }
        }}
        onSaveDraft={(body) => {
          saveDraftMutation.mutate(body, {
            onSuccess: () => setSubmitOpen(false),
          });
        }}
        onUpdateDraft={(id, body) => {
          if (editItem?.folder_version_id && editItem.folder_revision) {
            updateFolderMutation.mutate({
              listingId: id, versionId: String(editItem.folder_version_id),
              observedRevision: String(editItem.folder_revision), body,
            }, { onSuccess: () => { setSubmitOpen(false); setEditItem(null); } });
          } else {
            updateDraftMutation.mutate({ id, body }, {
              onSuccess: () => { setSubmitOpen(false); setEditItem(null); },
            });
          }
        }}
        isSubmitting={submitMutation.isPending || submitDraftMutation.isPending || submitFolderMutation.isPending}
        isSavingDraft={saveDraftMutation.isPending || updateDraftMutation.isPending || updateFolderMutation.isPending}
      />}
    </>
  );
}
