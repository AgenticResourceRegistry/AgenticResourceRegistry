// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-License-Identifier: Apache-2.0


import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { useState, useEffect } from "react";
import { Star, ArrowLeft, History, Loader2, ArrowDownToLine, Archive, ArchiveRestore, AlertTriangle, GitFork } from "lucide-react";
import { toast } from "sonner";
import {
  useRegistryItem,
  useFeedback,
  useFeedbackSummary,
  useMyFeedback,
  useRegistryMetrics,
  useComponentVersions,
  useComponentVersionsPage,
  useComponentVersionDetail,
  useSkillVersionManifest,
  useSubmitSkillFolderDraft,
  useWithdrawSkillFolderVersion,
  useComponentArchive,
  useComponentUnarchive,
  useTeams,
  useUpdateRegistryVisibility,
  useWhoami,
  useMcpSyncWatcher,
  useForkComponent,
  useComponentForks,
  useComponentUpdateDraft,
  useComponentSubmitDraft,
} from "@/hooks/use-api";
import { getUserRole } from "@/lib/api";
import { useOptionalAuth } from "@/hooks/use-auth";
import { useDeploymentConfig } from "@/hooks/use-deployment-config";
import { hasMinRole } from "@/hooks/use-role-guard";
import type { RegistryType } from "@/lib/api";
import type { FeedbackItem, RegistryItem, ComponentVersionSummary, RecommendableType } from "@/lib/types";
import { requiresFolderDelivery } from "@/lib/skill-folder-delivery";
import { compactNumber } from "@/lib/utils";
import { canonicalRouteParts, registryIdentity } from "@/lib/registry-name";
import { tagColorClasses } from "@/lib/tag-colors";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ReviewForm } from "@/components/registry/review-form";
import { VersionDropdown } from "@/components/registry/version-dropdown";
import { ComponentEditForm } from "@/components/registry/component-edit-form";
import { SubmitComponentDialog } from "@/components/registry/submit-component-dialog";
import { ComponentInstallCommand } from "@/components/registry/component-install-command";
import { McpWebhookSyncPanel } from "@/components/registry/mcp-webhook-sync-panel";
import { SkillVersionFiles } from "@/components/registry/skill-version-files";
import { SkillSuccessorDialog } from "@/components/registry/skill-successor-dialog";
import { RegistryName } from "@/components/registry/registry-name";
import { ShareLinkButton } from "@/components/registry/share-link-button";
import { ForkDialog } from "@/components/registry/fork-dialog";
import { ForkedFromChip } from "@/components/registry/fork-provenance";
import { ForkDiffPanel } from "@/components/registry/fork-diff-panel";
import { ForksList } from "@/components/registry/forks-list";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { RecommendedBadge, RecommendedToggle } from "@/components/registry/recommended-badge";
import { Button } from "@/components/ui/button";
import { PickerSelect } from "@/components/ui/picker-select";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PageHeader } from "@/components/layouts/page-header";
import { DetailSkeleton } from "@/components/shared/skeleton-layouts";
import { ErrorState } from "@/components/shared/error-state";
import { EmptyState } from "@/components/shared/empty-state";
import { HarnessBadges } from "@/components/registry/harness-badges";
import { CoAuthorInput, type CoAuthor } from "@/components/registry/co-author-input";

// The visibility PATCH reports whether the flip pushed the listing back into the
// review queue. Read it off the response and return null when the server did not
// say, so the UI never invents an outcome from the caller's role.
function readReturnedToReview(payload: unknown): boolean | null {
  if (!payload || typeof payload !== "object") return null;
  const value = (payload as Record<string, unknown>).returned_to_review;
  return typeof value === "boolean" ? value : null;
}

function statusVariant(status?: string) {
  if (status === "approved") return "default" as const;
  if (status === "rejected") return "destructive" as const;
  return "secondary" as const;
}

function SkillDraftReviewAction({ listingId, version, enabled, onSubmitted }: {
  listingId: string; version: ComponentVersionSummary; enabled: boolean; onSubmitted: () => void;
}) {
  const manifest = useSkillVersionManifest(listingId, version.id);
  const submit = useSubmitSkillFolderDraft();
  const blocked = !enabled && !!manifest.data && requiresFolderDelivery(version, manifest.data);
  return <div className="flex flex-col items-start gap-1">
    <Button type="button" size="sm" variant="outline"
      disabled={!manifest.data || manifest.isError || blocked || submit.isPending}
      onClick={() => submit.mutate({ listingId, versionId: version.id, observedRevision: manifest.data!.revision },
        { onSuccess: onSubmitted })}>
      Submit v{version.version} for review
    </Button>
    {blocked && <span role="status" className="text-xs text-muted-foreground">Complete-folder review is not enabled on this server.</span>}
    {manifest.isError && <Button type="button" variant="ghost" size="sm" onClick={() => void manifest.refetch()}>
      Could not load v{version.version} files. Retry
    </Button>}
  </div>;
}

function formatArchiveDate(item: RegistryItem) {
  const value = item.updated_at ?? item.created_at;
  return value ? new Date(value).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : null;
}

function ArchivedComponentBanner({ item, type, canRestore }: { item: RegistryItem; type: string; canRestore: boolean }) {
  const date = formatArchiveDate(item);

  return (
    <div className="flex items-start justify-between gap-4 rounded-md border border-dark-yellow/30 bg-light-yellow px-4 py-3 text-dark-yellow">
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
        <div className="space-y-1 text-sm">
          <p className="font-medium">
            This {type} was archived{date ? ` on ${date}` : ""}. It is hidden from registry lists.
          </p>
          <p className="text-xs text-dark-yellow/80">
            Installs still work by direct reference, but users will see an archived component warning.
            {canRestore ? " Restore it from the lifecycle panel when it should be discoverable again." : ""}
          </p>
        </div>
      </div>
      <Archive className="mt-0.5 h-4 w-4 shrink-0" />
    </div>
  );
}

export default function ComponentDetailPage({
  componentId,
  componentType,
}: {
  componentId?: string;
  componentType?: RegistryType;
} = {}) {
  // Rendered from two routes: the canonical /components/$type/$namespace/$slug
  // route passes the resolved UUID and type as props; the legacy
  // /components/$componentId route supplies the id as a path param and the
  // type as a query param.
  const params = useParams({ strict: false }) as { componentId?: string };
  const search = useSearch({ strict: false }) as { type?: string; tab?: string };
  const id = componentId ?? params.componentId ?? "";
  const type = (componentType ?? search.type ?? "mcps") as RegistryType;
  const navigate = useNavigate();
  const singularType = type === "sandboxes" ? "sandbox" : type.replace(/s$/, "");
  const { isAuthenticated } = useOptionalAuth();
  const { skillFolderDeliveryEnabled } = useDeploymentConfig();
  const { data: item, isLoading, isError, error, refetch } = useRegistryItem(type, id);
  const { data: feedbackItems, refetch: refetchFeedback } = useFeedback(singularType, id);
  const { data: feedbackSummary, refetch: refetchSummary } = useFeedbackSummary(id);
  const { data: myReview } = useMyFeedback(singularType, id, isAuthenticated);
  const { data: rawMetrics } = useRegistryMetrics(type, id, isAuthenticated);
  const { data: versionsData, isLoading: versionsLoading } = useComponentVersions(type, id);
  const [versionPage, setVersionPage] = useState(1);
  const [olderVersions, setOlderVersions] = useState<ComponentVersionSummary[]>([]);
  const { data: olderPage, isFetching: olderLoading } = useComponentVersionsPage(type, id, versionPage);
  useEffect(() => { setVersionPage(1); setOlderVersions([]); }, [id, type]);
  useEffect(() => {
    if (!olderPage || versionPage <= 1) return;
    setOlderVersions((current) => {
      const seen = new Set(current.map((version) => version.id));
      return [...current, ...olderPage.items.filter((version) => !seen.has(version.id))];
    });
  }, [olderPage, versionPage]);
  const [selectedVersion, setSelectedVersion] = useState<string | null>(null);
  // Skill version detail includes stored file bytes. Use the bounded summary
  // and explicit manifest/file endpoints instead of fetching every file on view.
  const { data: versionDetail } = useComponentVersionDetail(singularType === "skill" ? undefined : type, id, selectedVersion);
  const { data: whoami } = useWhoami(isAuthenticated);
  const { data: teams = [] } = useTeams(isAuthenticated);
  const updateVisibility = useUpdateRegistryVisibility();
  const withdrawFolderVersion = useWithdrawSkillFolderVersion();
  const submitFolderDraft = useSubmitSkillFolderDraft();
  const [forkOpen, setForkOpen] = useState(false);
  const [forkPage, setForkPage] = useState(1);
  const [activeTab, setActiveTab] = useState(search.tab === "edit" ? "edit" : "overview");
  useEffect(() => { if (search.tab === "edit") setActiveTab("edit"); }, [id, search.tab]);
  const forkComponent = useForkComponent(type);
  const updateDraft = useComponentUpdateDraft(type);
  const submitDraft = useComponentSubmitDraft(type);
  const [draftEditOpen, setDraftEditOpen] = useState(false);
  const { data: publicForks, isLoading: forksLoading, error: forksError } = useComponentForks(type, id, forkPage, activeTab === "forks");
  const canEdit = isAuthenticated && (item?.user_permission === "owner");
  // Only owners can read sync status, so only their open page follows GitHub syncs.
  useMcpSyncWatcher(item ? String(item.id) : undefined, canEdit && type === "mcps" && !!item?.git_url);
  const isEditableDraft = item?.status === "draft" || item?.status === "rejected";
  useEffect(() => {
    if (search.tab === "edit" && isEditableDraft) setDraftEditOpen(true);
  }, [id, search.tab, isEditableDraft]);
  const isAdmin = isAuthenticated && hasMinRole(getUserRole(), "admin");
  const owningTeam = item?.team_id ? teams.find((team) => team.id === String(item.team_id)) : undefined;
  const personalTeam = teams.find((team) => team.is_personal && team.visibility === "private");
  const teamRole = owningTeam?.role;
  // Mirror the server rule in PATCH /registry/{type}/{id}/visibility exactly.
  // Admins are privileged; a global REVIEWER is not, because a team-private
  // listing belongs to its teamspace. A team-owned listing needs a team owner or
  // team reviewer, and a personal one needs its creator. Showing the control any
  // wider just invites a 403.
  const canChangeVisibility = Boolean(
    item &&
      (hasMinRole(getUserRole(), "admin") ||
        (item.team_id
          ? teamRole === "owner" || teamRole === "reviewer"
          : !!whoami?.id && whoami.id === String(item.submitted_by))),
  );
  const canTransferOwnership = !!(whoami?.id && item?.submitted_by && whoami.id === String(item.submitted_by));
  const currentVisibility = (item?.visibility as string | undefined) ?? (item?.is_private ? "team" : "public");
  const visibilityOptions = owningTeam?.visibility === "private"
    ? [{ value: "team", label: "Team members only" }]
    : [
        { value: "public", label: "Public" },
        { value: "team", label: "Team members only" },
      ];
  const showVisibilityControl = canChangeVisibility && Boolean(item?.team_id || personalTeam);
  const [confirmPublicOpen, setConfirmPublicOpen] = useState(false);
  const [successorOpen, setSuccessorOpen] = useState(false);

  // Co-authors
  const [coAuthors, setCoAuthors] = useState<CoAuthor[]>([]);
  useEffect(() => {
    if (!isAuthenticated) {
      setCoAuthors([]);
      return;
    }
    const controller = new AbortController();
    const token = sessionStorage.getItem("observal_access_token");
    const headers: Record<string, string> = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;
    fetch(`/api/v1/${type}/${id}/co-authors`, { headers, signal: controller.signal })
      .then((r) => (r.ok ? r.json() : []))
      .then((data) => {
        if (!controller.signal.aborted) setCoAuthors(data);
      })
      .catch(() => {});
    return () => controller.abort();
  }, [type, id, isAuthenticated]);

  const versions = [...(versionsData?.items ?? []), ...olderVersions.filter((version) => version.listing_id === String(item?.id))];
  // VersionDropdown expects AgentVersionSummary shape; ComponentVersionSummary is compatible
  const versionsForDropdown = versions.filter((v) => v.status === "approved" && !v.requires_global_review) as unknown as import("@/lib/types").AgentVersionSummary[];
  const latestApprovedVersion = versions.find((v) => v.status === "approved" && !v.requires_global_review)?.version;
  const effectiveVersion = selectedVersion ?? latestApprovedVersion ?? (item?.version as string | undefined);
  // Never substitute the listing-level latest release when the viewer selected
  // an older version that is missing or still loading. Both the command and
  // resource view must resolve to the exact approved version UUID.
  const selectedApprovedSkillVersion = singularType === "skill"
    ? versions.find((version) => version.status === "approved" && !version.requires_global_review && version.version === effectiveVersion) ?? null
    : null;
  const latestApprovedSkillVersion = singularType === "skill"
    ? versions.find((version) => version.status === "approved" && !version.requires_global_review) ?? null
    : null;
  const successorDrafts = canEdit && singularType === "skill" && item?.status === "approved"
    ? versions.filter((version) => ["draft", "rejected"].includes(version.status) && version.delivery_mode === "registry_direct")
    : [];
  const existingSuccessorDraft = successorDrafts[0] ?? null;
  // Never show an unapproved tree to other viewers. The manifest/file APIs
  // enforce authorization as well; this avoids even attempting their fetch.
  const ownerSkillFolderVersion = canEdit && singularType === "skill"
    ? (item?.status === "approved"
      ? versions.find((version) => version.status === "pending" && version.delivery_mode === "registry_direct") ?? existingSuccessorDraft
      : ["draft", "pending", "rejected"].includes(String(item?.status))
        ? versions.find((version) => version.version === effectiveVersion && ["draft", "pending", "rejected"].includes(version.status)
          && version.delivery_mode === "registry_direct") ?? null : null)
    : null;
  const editableSkillFolderDraft = existingSuccessorDraft ?? (
    ownerSkillFolderVersion && ["draft", "rejected"].includes(ownerSkillFolderVersion.status)
      ? ownerSkillFolderVersion : null
  );
  const ownerDraftManifest = useSkillVersionManifest(
    editableSkillFolderDraft ? String(item?.id) : undefined, editableSkillFolderDraft?.id,
  );
  const ownerDraftReviewBlocked = !skillFolderDeliveryEnabled && !!ownerDraftManifest.data &&
    requiresFolderDelivery(editableSkillFolderDraft, ownerDraftManifest.data);
  const { data: selectedManifest } = useSkillVersionManifest(
    singularType === "skill" && selectedApprovedSkillVersion?.delivery_mode === "registry_direct" ? String(item?.id) : undefined,
    selectedApprovedSkillVersion?.delivery_mode === "registry_direct" ? selectedApprovedSkillVersion.id : undefined,
  );
  const { data: latestManifest } = useSkillVersionManifest(
    singularType === "skill" && latestApprovedSkillVersion?.delivery_mode === "registry_direct" ? String(item?.id) : undefined,
    latestApprovedSkillVersion?.delivery_mode === "registry_direct" ? latestApprovedSkillVersion.id : undefined,
  );
  // Overlay version-specific description when a version is selected
  const effectiveItem: RegistryItem | undefined = item
    ? { ...item, ...((selectedApprovedSkillVersion ?? versionDetail) as unknown as RegistryItem | undefined),
        // The listing contains the newest SKILL.md, not necessarily the selected
        // approved release. Show folder prose only from its exact file preview.
        ...(selectedApprovedSkillVersion?.delivery_mode === "registry_direct" ? { skill_md_content: undefined } : {}) }
    : undefined;

  // Header/breadcrumb show the bare name; the install command needs the
  // canonical `namespace/slug` the CLI resolves.
  const identity = registryIdentity(item, id.slice(0, 8));
  const componentName = identity.name;
  const componentRef = identity.qualified;
  // Canonical shareable path from the explicit columns only, and only when the
  // namespace/slug actually resolve server-side (legacy verbatim-username
  // namespaces do not).
  const canonicalParts = canonicalRouteParts(item?.namespace, item?.slug);
  const canonicalComponentPath = canonicalParts
    ? `/components/${type}/${canonicalParts.namespace}/${canonicalParts.slug}`
    : undefined;

  // Legacy /components/<uuid>?type= entry: swap the address bar to the
  // shareable URL only for approved components. /registry/resolve returns
  // approved-or-owned only, so redirecting a reviewer/admin viewing a pending
  // component would strand them on a 404; their UUID URL keeps working.
  const componentApproved = (item?.status as string | undefined) === "approved";
  useEffect(() => {
    if (componentId || !canonicalParts || !componentApproved) return;
    navigate({
      to: "/components/$type/$namespace/$slug",
      params: { type, ...canonicalParts },
      replace: true,
    });
  }, [componentId, type, canonicalParts, componentApproved, navigate]);

  function applyVisibility(visibility: "public" | "team") {
    updateVisibility.mutate(
      { type, id, visibility },
      {
        onSuccess: (data) => {
          setConfirmPublicOpen(false);
          if (visibility === "team" && data.qualified_name.includes("/")) {
            const [namespace, slug] = data.qualified_name.split("/", 2);
            navigate({
              to: "/components/$type/$namespace/$slug",
              params: { type, namespace, slug },
              replace: true,
            });
            return;
          }
          if (visibility !== "public") return;
          const returnedToReview = readReturnedToReview(data);
          // The shared mutation hook already raised a generic "Visibility updated"
          // toast. Clear it so the user reads one accurate outcome, not two.
          toast.dismiss();
          if (returnedToReview === null) {
            toast.error(
              `${componentName} is now public, but the server did not report whether it went back for review.`,
            );
            return;
          }
          toast.success(
            returnedToReview
              ? `${componentName} was submitted for review. It is public now, but stays out of the catalog until a reviewer approves it.`
              : `${componentName} is now public.`,
          );
        },
      },
    );
  }

  const avgRating = feedbackSummary?.average_rating;
  const totalReviews = feedbackSummary?.total_reviews ?? 0;
  const showSkillFeedback = singularType !== "skill" || item?.status === "approved" || item?.status === "archived";
  const metricsEntries: [string, string][] = isAuthenticated && rawMetrics && typeof rawMetrics === "object"
    ? Object.entries(rawMetrics as Record<string, unknown>).map(([k, v]) => [k, typeof v === "number" ? v.toLocaleString() : String(v ?? "")])
    : [];

  return (
    <>
      <PageHeader
        title={isLoading ? "Component" : componentName}
        breadcrumbs={[
          { label: "Registry", href: "/" },
          { label: "Components", href: "/components" },
          { label: isLoading ? "..." : componentName },
        ]}
        actionButtonsLeft={
          <Button variant="ghost" size="sm" className="h-7 px-2 gap-1 text-muted-foreground" asChild>
            <Link to="/components">
              <ArrowLeft className="h-3.5 w-3.5" />
              <span className="text-xs">Back</span>
            </Link>
          </Button>
        }
        actionButtonsRight={
          item ? <div className="flex items-center gap-2">
            {isAuthenticated && item.status === "approved" && latestApprovedVersion && (
              <Button size="sm" variant="outline" onClick={() => setForkOpen(true)}><GitFork className="h-4 w-4" /> Fork</Button>
            )}
            <ShareLinkButton path={canonicalComponentPath ?? `/components/${id}?type=${type}`} />
          </div> : undefined
        }
      />
      <div className="page-body w-full max-w-[1440px] mx-auto space-y-5">
        {isLoading ? (
          <DetailSkeleton />
        ) : isError ? (
          <ErrorState message={error?.message} onRetry={() => refetch()} />
        ) : !item ? (
          <ErrorState message="Component not found" />
        ) : (
          <div className="animate-in space-y-6">
            {item.status === "archived" && (
              <ArchivedComponentBanner item={item} type={singularType} canRestore={canEdit} />
            )}

            {/* Header */}
            <div className="space-y-2">
              <div className="flex items-start gap-3 flex-wrap">
                <RegistryName
                  item={item}
                  as="h1"
                  nameClassName="text-2xl font-display font-bold tracking-tight"
                  handleClassName="text-sm text-muted-foreground"
                />
                <span className={`rounded-full px-2.5 py-0.5 text-2xs font-medium ${tagColorClasses(singularType)}`}>
                  {singularType}
                </span>
                {item.status && (
                  <Badge
                    variant={statusVariant(item.status)}
                    className={item.status === "archived" ? "bg-light-yellow text-dark-yellow" : undefined}
                  >
                    {item.status}
                  </Badge>
                )}
                {item.is_recommended && showSkillFeedback && <RecommendedBadge />}
                {!!item.fork_count && item.fork_count > 0 && <span className="inline-flex items-center gap-1 text-xs text-muted-foreground"><GitFork className="h-3.5 w-3.5" />{item.fork_count} public forks</span>}
                {showVisibilityControl && (
                  <PickerSelect
                    value={currentVisibility}
                    onValueChange={(value) => {
                      if (value === currentVisibility) return;
                      // Going public widens the audience and re-opens review, so it
                      // is confirmed. Narrowing to the teamspace needs no warning.
                      if (value === "public") {
                        setConfirmPublicOpen(true);
                        return;
                      }
                      applyVisibility("team");
                    }}
                    options={visibilityOptions}
                    ariaLabel="Listing visibility"
                    className="w-40"
                    inputClassName="h-7 px-2 text-xs"
                    disabled={
                      updateVisibility.isPending ||
                      (owningTeam?.visibility === "private" && currentVisibility === "team")
                    }
                  />
                )}
                {versionsForDropdown.length > 0 ? (
                  <VersionDropdown
                    versions={versionsForDropdown}
                    currentVersion={effectiveVersion ?? ""}
                    onSelect={setSelectedVersion}
                  />
                ) : effectiveVersion ? (
                  <Badge variant="secondary" className="text-xs">v{effectiveVersion}</Badge>
                ) : null}
              </div>
              <ForkedFromChip provenance={item.forked_from} />
              {item.forked_from?.available && effectiveVersion && (
                <ForkDiffPanel type={type} id={id} version={effectiveVersion} />
              )}
              {effectiveItem?.description && (
                <p className="text-sm text-foreground/80 leading-relaxed max-w-2xl">{effectiveItem.description as string}</p>
              )}
              {showSkillFeedback && avgRating != null && (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <div className="flex items-center gap-0.5">
                    {Array.from({ length: 5 }).map((_, i) => (
                      <Star
                        key={i}
                        className={`h-3.5 w-3.5 ${i < Math.round(avgRating) ? "fill-current text-warning" : "text-muted-foreground/30"}`}
                      />
                    ))}
                  </div>
                  <span className="text-xs">{avgRating.toFixed(1)} ({totalReviews} review{totalReviews !== 1 ? "s" : ""})</span>
                </div>
              )}
            </div>

            {editableSkillFolderDraft && successorDrafts.length <= 1 && (
              <div className="flex flex-wrap items-center gap-3 rounded-md border border-border bg-card p-4">
                <div className="flex-1 space-y-1 text-sm">
                  <strong>Draft v{editableSkillFolderDraft.version}</strong>
                  <p className="text-muted-foreground">Keep editing your saved files, or submit this exact version for review.</p>
                  {ownerDraftReviewBlocked && <p role="status" className="text-muted-foreground">
                    Complete-folder review is not enabled on this server. Your draft remains saved.
                  </p>}
                  {ownerDraftManifest.isError && <div role="alert" className="text-destructive">
                    Could not load the saved version. <Button variant="outline" size="sm" onClick={() => void ownerDraftManifest.refetch()}>Retry</Button>
                  </div>}
                </div>
                <Button type="button" disabled={!ownerDraftManifest.data || ownerDraftReviewBlocked || submitFolderDraft.isPending}
                  onClick={() => submitFolderDraft.mutate({ listingId: String(item.id), versionId: editableSkillFolderDraft.id,
                    observedRevision: ownerDraftManifest.data!.revision }, { onSuccess: () => void refetch() })}>
                  {submitFolderDraft.isPending ? "Submitting…" : "Submit for review"}
                </Button>
              </div>
            )}
            {canEdit && singularType === "skill" && item.status === "approved" && (
              <div className="flex flex-wrap items-center gap-3 rounded-md border border-border bg-card p-4">
                <div className="flex-1 text-sm">
                  <strong>{existingSuccessorDraft ? "Continue your saved successor" : "Create a new folder version"}</strong>
                  <p className="text-xs text-muted-foreground">Approved releases cannot be edited. {successorDrafts.length > 1
                    ? "Multiple saved drafts exist. Use Versions to inspect and submit the exact one you choose."
                    : existingSuccessorDraft ? "Resume the existing draft, or create a different next version."
                    : "Fork the reviewed folder or import a complete local folder for a Git or historical direct skill."}</p>
                </div>
                {existingSuccessorDraft && <Button asChild><Link to="/components" search={{ type: "skills", folderListingId: String(item.id),
                  folderVersionId: existingSuccessorDraft.id, folderVersion: existingSuccessorDraft.version }}>
                  Edit saved draft v{existingSuccessorDraft.version}
                </Link></Button>}
                <Button type="button" variant="outline" onClick={() => setSuccessorOpen(true)}>Create next version</Button>
              </div>
            )}
            {canEdit && singularType === "skill" && item.status === "approved" && (
              <SkillSuccessorDialog open={successorOpen} onOpenChange={setSuccessorOpen} listingId={String(item.id)}
                onCreated={(manifest, draftVersion) => {
                  setSuccessorOpen(false);
                  navigate({ to: "/components", search: {
                    type: "skills", folderListingId: String(item.id), folderVersionId: manifest.version_id,
                    folderVersion: draftVersion,
                  } });
                }} />
            )}
            {isAdmin && showSkillFeedback && (
              <div className="lg:hidden">
                <RecommendedToggle
                  entityType={singularType as RecommendableType}
                  entityId={String(item.id)}
                  isRecommended={!!item.is_recommended}
                />
              </div>
            )}

            {/* Grid: Main + Sidebar */}
            <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-8 items-start">
            {/* Tabs */}
            <Tabs value={activeTab} onValueChange={setActiveTab} className="min-w-0">
              <TabsList>
                <TabsTrigger value="overview">Overview</TabsTrigger>
                {showSkillFeedback && <TabsTrigger value="reviews">
                  Reviews
                  {totalReviews > 0 && (
                    <span className="ml-1.5 text-[10px] bg-muted px-1.5 py-0.5 rounded-full">
                      {totalReviews}
                    </span>
                  )}
                </TabsTrigger>}
                <TabsTrigger value="versions">
                  Versions
                  {versions.length > 0 && (
                    <span className="ml-1.5 text-[10px] bg-muted px-1.5 py-0.5 rounded-full">
                      {versions.length}
                    </span>
                  )}
                </TabsTrigger>
                <TabsTrigger value="forks">Forks{item.fork_count ? ` (${item.fork_count})` : ""}</TabsTrigger>
                {canEdit && <TabsTrigger value="edit">Edit</TabsTrigger>}
                {canEdit && singularType === "mcp" && <TabsTrigger value="sync">Sync</TabsTrigger>}
              </TabsList>

              <TabsContent value="overview" forceMount className="mt-6 data-[state=inactive]:hidden">
                <div className="space-y-6 w-full min-h-[400px]">
                  <ComponentMetadata item={effectiveItem ?? item} />
                  {(selectedApprovedSkillVersion?.delivery_mode === "registry_direct" || ownerSkillFolderVersion) && (
                    <SkillVersionFiles
                      key={(selectedApprovedSkillVersion ?? ownerSkillFolderVersion)?.id}
                      listingId={String(item.id)}
                      versionId={(selectedApprovedSkillVersion ?? ownerSkillFolderVersion)!.id}
                      status={selectedApprovedSkillVersion ? "approved" : ownerSkillFolderVersion!.status}
                    />
                  )}
                  {selectedApprovedSkillVersion && ownerSkillFolderVersion && (
                    <div className="space-y-2">
                      <p className="text-sm font-medium">Your separate {ownerSkillFolderVersion.status === "pending" ? "submission" : "draft"} v{ownerSkillFolderVersion.version} (not part of the selected approved release)</p>
                      <SkillVersionFiles key={ownerSkillFolderVersion.id} listingId={String(item.id)}
                        versionId={ownerSkillFolderVersion.id} status={ownerSkillFolderVersion.status} />
                    </div>
                  )}
                </div>
              </TabsContent>

              {showSkillFeedback && <TabsContent value="reviews" forceMount className="mt-6 data-[state=inactive]:hidden">
                <div className="space-y-6 w-full min-h-[400px]">
                {isAuthenticated && (
                  <>
                    <ReviewForm
                      listingId={id}
                      listingType={singularType}
                      onSuccess={() => {
                        refetchFeedback();
                        refetchSummary();
                      }}
                    />
                    <Separator />
                  </>
                )}

                {!feedbackItems || feedbackItems.length === 0 ? (
                  <EmptyState
                    icon={Star}
                    title="No reviews yet"
                    description={
                      isAuthenticated
                        ? `Be the first to review this ${singularType}.`
                        : "Log in to leave a review."
                    }
                  />
                ) : (
                  <div className="space-y-4">
                    {feedbackItems
                      .filter((fb: FeedbackItem) => !isAuthenticated || !myReview || fb.id !== myReview.id)
                      .map((fb: FeedbackItem) => (
                      <div key={fb.id} className="rounded-md border border-border p-4 space-y-2">
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-1">
                            {Array.from({ length: 5 }).map((_, i) => (
                              <Star
                                key={i}
                                className={`h-3.5 w-3.5 ${
                                  i < fb.rating
                                    ? "fill-current text-warning"
                                    : "text-muted-foreground/30"
                                }`}
                              />
                            ))}
                          </div>
                          <span className="text-xs text-muted-foreground">
                            {fb.username ?? fb.user ?? "Anonymous"}
                            {fb.created_at && ` · ${new Date(fb.created_at).toLocaleDateString()}`}
                          </span>
                        </div>
                        {fb.comment && (
                          <p className="text-sm text-muted-foreground leading-relaxed">{fb.comment}</p>
                        )}
                      </div>
                    ))}
                  </div>
                )}
                </div>
              </TabsContent>}

              <TabsContent value="versions" forceMount className="mt-6 data-[state=inactive]:hidden">
                <div className="space-y-4 w-full min-h-[400px]">
                  {versionsLoading ? (
                    <div className="flex items-center justify-center py-12">
                      <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                    </div>
                  ) : versions.length === 0 ? (
                    <EmptyState
                      icon={History}
                      title="No versions yet"
                      description="Release a new version from the Edit tab to start tracking version history."
                    />
                  ) : (
                    <div className="space-y-2">
                      {versions.map((v: ComponentVersionSummary) => (
                        <div
                          key={v.id}
                          className="flex items-start justify-between gap-4 rounded-md border border-border px-4 py-3"
                        >
                          <div className="space-y-1 min-w-0">
                            <div className="flex items-center gap-2 flex-wrap">
                              <span className="font-mono text-sm font-medium">v{v.version}</span>
                              <Badge variant={statusVariant(v.status)} className="text-[10px]">
                                {v.status}
                              </Badge>
                            </div>
                            {v.description && (
                              <p className="text-xs text-muted-foreground truncate max-w-xl">{v.description}</p>
                            )}
                            {v.changelog && (
                              <p className="text-xs text-muted-foreground/70 italic truncate max-w-xl">{v.changelog}</p>
                            )}
                          </div>
                          {canEdit && singularType === "skill" && ["draft", "rejected"].includes(v.status) && (
                            <div className="flex flex-wrap items-center gap-3">
                              <Link to="/components" search={{ type: "skills", folderListingId: String(item.id),
                                folderVersionId: v.id, folderVersion: v.version }} className="text-xs underline underline-offset-2">
                                Edit exact draft
                              </Link>
                              {successorDrafts.length > 1 && <SkillDraftReviewAction listingId={String(item.id)} version={v}
                                enabled={skillFolderDeliveryEnabled} onSubmitted={() => void refetch()} />}
                            </div>
                          )}
                          {canEdit && singularType === "skill" && v.status === "pending" &&
                            v.delivery_mode === "registry_direct" && !v.requires_global_review && (
                            <Button type="button" variant="outline" size="sm" disabled={withdrawFolderVersion.isPending}
                              onClick={() => withdrawFolderVersion.mutate({ listingId: String(item.id), versionId: v.id })}>
                              Withdraw to draft
                            </Button>
                          )}
                          {v.released_at && (
                            <div className="shrink-0 text-right space-y-0.5">
                              <p className="text-xs text-muted-foreground">
                                {new Date(v.released_at).toLocaleDateString()}
                              </p>
                            </div>
                          )}
                        </div>
                      ))}
                      {versionsData && versions.length < versionsData.total && (
                        <Button type="button" variant="outline" disabled={olderLoading} onClick={() => setVersionPage((page) => page + 1)}>
                          {olderLoading ? "Loading older versions…" : "Show older versions"}
                        </Button>
                      )}
                    </div>
                  )}
                </div>
              </TabsContent>

              <TabsContent value="forks" className="mt-6">
                <ForksList type={type} result={publicForks} page={forkPage} onPage={setForkPage} isLoading={forksLoading} error={forksError} />
              </TabsContent>
              {canEdit && (
                <TabsContent value="edit" forceMount className="mt-6 data-[state=inactive]:hidden">
                  <div className="w-full min-h-[400px]">
                    {item.status === "approved" && singularType === "skill" && selectedApprovedSkillVersion?.delivery_mode === "registry_direct" ? (
                      <div className="rounded-md border border-border bg-card p-5 space-y-3">
                        <h2 className="font-semibold">Approved skill releases are immutable</h2>
                        <p className="text-sm text-muted-foreground">The approved release stays available. {existingSuccessorDraft
                          ? "Continue editing your saved successor, or create a different version."
                          : "Create a new folder version to change its files or metadata."}</p>
                        {existingSuccessorDraft && <Button asChild><Link to="/components" search={{ type: "skills", folderListingId: String(item.id),
                          folderVersionId: existingSuccessorDraft.id, folderVersion: existingSuccessorDraft.version }}>
                          Edit saved draft v{existingSuccessorDraft.version}
                        </Link></Button>}
                        <Button type="button" variant="outline" onClick={() => setSuccessorOpen(true)}>Create next version</Button>
                      </div>
                    ) : editableSkillFolderDraft ? (
                      <div className="rounded-md border border-border bg-card p-5 space-y-3">
                        <h2 className="font-semibold">Edit the exact folder draft</h2>
                        <p className="text-sm text-muted-foreground">This skill has a saved file tree. Open its version-bound editor to update the folder and its metadata together.</p>
                        <Button asChild><Link to="/components" search={{ type: "skills", folderListingId: String(item.id),
                          folderVersionId: editableSkillFolderDraft.id, folderVersion: editableSkillFolderDraft.version }}>
                          Edit draft v{editableSkillFolderDraft.version}
                        </Link></Button>
                      </div>
                    ) : isEditableDraft ? (
                      <div className="space-y-3 rounded-md border border-border p-5">
                        <h2 className="text-sm font-semibold">Finish your draft</h2>
                        <p className="text-sm text-muted-foreground">Edit the forked configuration, then submit it for review. It will not appear in public Forks until approved.</p>
                        <Button onClick={() => setDraftEditOpen(true)}>Edit draft</Button>
                      </div>
                    ) : <ComponentEditForm
                      listingId={id}
                      type={type}
                      currentVersion={effectiveVersion ?? "1.0.0"}
                      item={effectiveItem ?? item}
                      onSuccess={() => refetch()}
                    />}
                  </div>
                </TabsContent>
              )}

              {canEdit && singularType === "mcp" && (
                <TabsContent value="sync" className="mt-6">
                  <div className="w-full min-h-[400px]">
                    <McpWebhookSyncPanel listingId={String(item.id)} gitUrl={item.git_url as string | undefined} />
                  </div>
                </TabsContent>
              )}
            </Tabs>

            {/* Sidebar */}
            <aside className="space-y-5">
              {/* Install command (MCPs, Skills, Hooks only) */}
              {(item.status === "approved" || item.status === "archived") &&
                (singularType === "mcp" || singularType === "skill" || singularType === "hook") && (
                <ComponentInstallCommand
                  componentType={singularType}
                  componentName={componentRef}
                  selectedSkillVersion={selectedApprovedSkillVersion}
                  skillFolderDeliveryEnabled={skillFolderDeliveryEnabled}
                  selectedVersionRequiresFolderDelivery={requiresFolderDelivery(selectedApprovedSkillVersion, selectedManifest)}
                  latestMayRequireFolderDelivery={versionsLoading || !latestApprovedSkillVersion ||
                    requiresFolderDelivery(latestApprovedSkillVersion, latestManifest)}
                />
              )}

              {/* Stats */}
              <div className="border border-border rounded-md p-4 space-y-4">
                <h3 className="text-xs font-semibold font-display uppercase tracking-wider text-muted-foreground">
                  Stats
                </h3>
                <div className="space-y-3">
                  {effectiveVersion && (
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-muted-foreground">Version</span>
                      <span className="font-mono font-medium">{effectiveVersion}</span>
                    </div>
                  )}
                  {item.created_at && (
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-muted-foreground">Created</span>
                      <span className="font-mono font-medium text-xs">{new Date(item.created_at).toLocaleDateString()}</span>
                    </div>
                  )}
                  {(item as Record<string, unknown>).download_count != null && (item as Record<string, unknown>).download_count !== 0 && (
                    <div className="flex items-center justify-between text-sm">
                      <span className="inline-flex items-center gap-2 text-muted-foreground">
                        <ArrowDownToLine className="h-3.5 w-3.5" />
                        Downloads
                      </span>
                      <span className="font-mono font-medium">
                        {compactNumber((item as Record<string, unknown>).download_count as number)}
                      </span>
                    </div>
                  )}
                  {metricsEntries.map(([key, val]) => (
                    <div key={key} className="flex items-center justify-between text-sm">
                      <span className="text-muted-foreground capitalize">{key.replace(/_/g, " ")}</span>
                      <span className="font-mono font-medium">{val}</span>
                    </div>
                  ))}
                  {showSkillFeedback && avgRating != null && (
                    <div className="flex items-center justify-between text-sm">
                      <span className="inline-flex items-center gap-2 text-muted-foreground">
                        <Star className="h-3.5 w-3.5" />
                        Rating
                      </span>
                      <span className="font-mono font-medium">
                        {avgRating.toFixed(1)}{" "}
                        <span className="text-xs text-muted-foreground font-normal">({totalReviews})</span>
                      </span>
                    </div>
                  )}
                </div>
              </div>

              {/* harness Compatibility */}
              {Array.isArray(item.supported_harnesses) && (item.supported_harnesses as string[]).length > 0 && (
                <div className="border border-border rounded-md p-4 space-y-3">
                  <h3 className="text-xs font-semibold font-display uppercase tracking-wider text-muted-foreground">
                    harness Compatibility
                  </h3>
                  <HarnessBadges supportedHarnesses={item.supported_harnesses as string[]} max={7} />
                </div>
              )}

              {/* Publisher */}
              {!!item.owner && (
                <div className="border border-border rounded-md p-4 space-y-2">
                  <h3 className="text-xs font-semibold font-display uppercase tracking-wider text-muted-foreground">
                    Publisher
                  </h3>
                  <p className="text-sm">{String(item.owner)}</p>
                </div>
              )}

              {isAdmin && showSkillFeedback && (
                <RecommendedToggle
                  entityType={singularType as RecommendableType}
                  entityId={String(item.id)}
                  isRecommended={!!item.is_recommended}
                />
              )}

              {(canEdit || coAuthors.length > 0) && (
                <div className="border border-border rounded-md p-4 space-y-4">
                  <h3 className="text-xs font-semibold font-display uppercase tracking-wider text-muted-foreground">
                    Danger zone
                  </h3>

                  <CoAuthorInput
                    entityType={type}
                    entityId={id}
                    coAuthors={coAuthors}
                    onChange={setCoAuthors}
                    canManage={canEdit}
                    canTransferOwnership={canTransferOwnership}
                    onTransferOwnership={() => refetch()}
                  />

                  {canEdit && (item.status === "approved" || item.status === "archived") && (
                    <div className="border-t border-border pt-3 space-y-2">
                      <p className="text-sm font-medium">Lifecycle</p>
                      <ComponentArchiveButton type={type} item={item} onSuccess={() => refetch()} />
                    </div>
                  )}
                </div>
              )}
            </aside>
            </div>
          </div>
        )}
      </div>

      {draftEditOpen && item && isEditableDraft && !editableSkillFolderDraft && canEdit && (
        <SubmitComponentDialog
          key={id}
          open={draftEditOpen}
          onOpenChange={setDraftEditOpen}
          type={type}
          editItem={item}
          onSubmit={(body) => {
            void (async () => {
              try {
                await updateDraft.mutateAsync({ id, body });
                await submitDraft.mutateAsync(id);
                setDraftEditOpen(false);
                await refetch();
              } catch { /* mutation hooks show the error; keep the draft open */ }
            })();
          }}
          onSaveDraft={(body) => {
            updateDraft.mutate({ id, body }, { onSuccess: () => { setDraftEditOpen(false); void refetch(); } });
          }}
          onUpdateDraft={(draftId, body) => {
            updateDraft.mutate({ id: draftId, body }, { onSuccess: () => { setDraftEditOpen(false); void refetch(); } });
          }}
          isSubmitting={updateDraft.isPending || submitDraft.isPending}
          isSavingDraft={updateDraft.isPending}
        />
      )}
      {forkOpen && item && (
        <ForkDialog
          open={forkOpen}
          onOpenChange={setForkOpen}
          kind={singularType}
          sourceName={item.name}
          sourceIsPrivate={!!item.is_private}
          sourceTeamId={item.team_id}
          versions={versions}
          initialVersion={versions.find((v) => v.version === effectiveVersion)?.status === "approved" ? effectiveVersion : latestApprovedVersion}
          teams={teams}
          username={whoami?.username}
          onFork={async (body) => {
            const fork = await forkComponent.mutateAsync({ id, body });
            await navigate({ to: "/components/$componentId", params: { componentId: fork.id }, search: { type, tab: "edit" } });
          }}
        />
      )}
      <AlertDialog open={confirmPublicOpen} onOpenChange={setConfirmPublicOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Make {componentName} public?</AlertDialogTitle>
            <AlertDialogDescription>
              Everyone who can reach this registry will be able to find and install this {singularType}, not just the
              members of its teamspace. Publishing publicly also returns it to the review queue, so it leaves the
              catalog until a reviewer approves it. Approval is manual, so it will not be immediate.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={updateVisibility.isPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={(event) => {
                event.preventDefault();
                applyVisibility("public");
              }}
              disabled={updateVisibility.isPending}
            >
              {updateVisibility.isPending ? (
                <><Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />Publishing...</>
              ) : (
                "Make public"
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

function ComponentArchiveButton({
  type,
  item,
  onSuccess,
}: {
  type: RegistryType;
  item: RegistryItem;
  onSuccess: () => void;
}) {
  const [open, setOpen] = useState(false);
  const archiveMutation = useComponentArchive(type);
  const unarchiveMutation = useComponentUnarchive(type);
  const isArchived = item.status === "archived";
  const isBusy = archiveMutation.isPending || unarchiveMutation.isPending;

  function submit() {
    const mutation = isArchived ? unarchiveMutation : archiveMutation;
    mutation.mutate(item.id, {
      onSuccess: () => {
        setOpen(false);
        onSuccess();
      },
    });
  }

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        className={isArchived ? "h-8" : "h-8 border-dark-yellow/40 bg-light-yellow text-dark-yellow hover:bg-light-yellow/80"}
        onClick={() => setOpen(true)}
        disabled={isBusy}
      >
        {isArchived ? <ArchiveRestore className="mr-1 h-3.5 w-3.5" /> : <Archive className="mr-1 h-3.5 w-3.5" />}
        {isArchived ? "Restore" : "Archive"}
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{isArchived ? `Restore ${item.name}?` : `Archive ${item.name}?`}</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            {isArchived
              ? "This makes the component discoverable again and removes archived install warnings."
              : "Archived components stop appearing in registry lists and insight suggestions. Direct installs and agent pulls still work, but users will see a warning."}
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
            <Button
              variant={isArchived ? "default" : "outline"}
              className={isArchived ? undefined : "border-dark-yellow/40 bg-light-yellow text-dark-yellow hover:bg-light-yellow/80"}
              onClick={submit}
              disabled={isBusy}
            >
              {isBusy ? <><Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />Saving...</> : isArchived ? "Restore" : "Archive"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function ComponentMetadata({ item }: { item: RegistryItem }) {
  const fields: { label: string; value: string; mono?: boolean; href?: string }[] = [];
  if ("git_url" in item && item.git_url != null) fields.push({ label: "Source", value: String(item.git_url), href: String(item.git_url) });
  if ("command" in item && item.command != null) fields.push({ label: "Command", value: String(item.command), mono: true });
  if ("url" in item && item.url != null) fields.push({ label: "URL", value: String(item.url), href: String(item.url) });
  if ("transport" in item && item.transport != null) fields.push({ label: "Transport", value: String(item.transport) });
  if ("framework" in item && item.framework != null) fields.push({ label: "Framework", value: String(item.framework) });
  if ("docker_image" in item && item.docker_image != null) fields.push({ label: "Docker Image", value: String(item.docker_image), mono: true });
  if ("hook_type" in item && item.hook_type != null) fields.push({ label: "Hook Type", value: String(item.hook_type) });
  if ("trigger_event" in item && item.trigger_event != null) fields.push({ label: "Trigger Event", value: String(item.trigger_event) });
  if ("runtime" in item && item.runtime != null) fields.push({ label: "Runtime", value: String(item.runtime) });
  if ("runtime_type" in item && item.runtime_type != null) fields.push({ label: "Runtime", value: String(item.runtime_type) });
  if ("image" in item && item.image != null) fields.push({ label: "Image / Artifact", value: String(item.image), mono: true });
  if ("network_policy" in item && item.network_policy != null) fields.push({ label: "Network Policy", value: String(item.network_policy) });
  if ("entrypoint" in item && item.entrypoint != null) fields.push({ label: "Entrypoint", value: String(item.entrypoint), mono: true });
  if ("source_url" in item && item.source_url != null) fields.push({ label: "Source URL", value: String(item.source_url), href: String(item.source_url) });
  if ("sandbox_path" in item && item.sandbox_path != null) fields.push({ label: "Sandbox Path", value: String(item.sandbox_path), mono: true });

  const setupInstructions = "setup_instructions" in item && item.setup_instructions ? String(item.setup_instructions) : null;
  const changelog = "changelog" in item && item.changelog ? String(item.changelog) : null;
  const skillMd = "skill_md_content" in item && item.skill_md_content ? String(item.skill_md_content) : null;
  const promptTemplate = "template" in item && item.template ? String(item.template) : null;
  const promptText = "prompt_text" in item && item.prompt_text ? String(item.prompt_text) : null;
  // Frontmatter is metadata, not prose. Keep the stored/reviewed bytes intact;
  // the exact file viewer below remains available for byte-for-byte inspection.
  const frontmatter = skillMd?.match(/^---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/);
  const markdownContent = (skillMd ? skillMd.slice(frontmatter?.[0].length ?? 0) : null) || promptTemplate || promptText;
  const envVars = "environment_variables" in item && Array.isArray(item.environment_variables) ? item.environment_variables as { name: string; description?: string; required?: boolean }[] : [];
  const resourceLimits = "resource_limits" in item && item.resource_limits ? JSON.stringify(item.resource_limits, null, 2) : null;
  const runtimeConfig = "runtime_config" in item && item.runtime_config ? JSON.stringify(item.runtime_config, null, 2) : null;

  const hasContent = fields.length > 0 || markdownContent || setupInstructions || changelog || envVars.length > 0 || resourceLimits || runtimeConfig;

  if (!hasContent) {
    return (
      <div className="rounded-md border border-dashed border-border p-8 text-center">
        <p className="text-sm text-muted-foreground">No additional details available for this component.</p>
      </div>
    );
  }

  return (
    <>
      {fields.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
          {fields.map((f) => (
            <div key={f.label} className="rounded-md border border-border p-3 space-y-1">
              <span className="text-xs text-muted-foreground">{f.label}</span>
              {f.href ? (
                <p><a href={f.href} className="text-sm text-primary hover:underline break-all" target="_blank" rel="noopener noreferrer">{f.value}</a></p>
              ) : (
                <p className={f.mono ? "font-mono text-sm" : "text-sm"}>{f.value}</p>
              )}
            </div>
          ))}
        </div>
      )}
      {resourceLimits && resourceLimits !== "{}" && (
        <div className="space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Resource Limits</h3>
          <pre className="rounded-md border border-border bg-muted/20 p-3 text-xs overflow-auto">{resourceLimits}</pre>
        </div>
      )}
      {runtimeConfig && runtimeConfig !== "{}" && (
        <div className="space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Runtime Config</h3>
          <pre className="rounded-md border border-border bg-muted/20 p-3 text-xs overflow-auto">{runtimeConfig}</pre>
        </div>
      )}
      {envVars.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Environment Variables</h3>
          <div className="rounded-md border border-border divide-y divide-border">
            {envVars.map((ev) => (
              <div key={ev.name} className="px-3 py-2 flex items-start justify-between gap-3 text-sm">
                <code className="font-mono text-xs shrink-0 pt-0.5">{ev.name}</code>
                <div className="min-w-0 flex flex-wrap items-start justify-end gap-2 text-right">
                  {ev.description && <span className="min-w-0 break-words text-xs leading-relaxed text-muted-foreground">{ev.description}</span>}
                  {ev.required && <Badge variant="secondary" className="shrink-0 text-[10px]">required</Badge>}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
      {setupInstructions && (
        <div className="space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Setup Instructions</h3>
          <div className="rounded-md border border-border bg-muted/20 p-4 overflow-y-auto max-h-[400px]">
            <div className="prose prose-sm dark:prose-invert max-w-none text-foreground/90 leading-relaxed">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{setupInstructions}</ReactMarkdown>
            </div>
          </div>
        </div>
      )}
      {markdownContent && (
        <div className="space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            {skillMd ? "Skill File" : "Prompt Template"}
          </h3>
          <div className="rounded-md border border-border bg-muted/20 p-4 overflow-y-auto max-h-[360px]">
            <div className="prose prose-sm dark:prose-invert max-w-none text-foreground/90 leading-relaxed prose-h1:text-xl prose-h1:leading-snug prose-h2:text-lg prose-h2:leading-snug">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdownContent}</ReactMarkdown>
            </div>
          </div>
        </div>
      )}
      {changelog && (
        <div className="space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Changelog</h3>
          <div className="rounded-md border border-border bg-muted/20 p-4 overflow-y-auto max-h-[300px]">
            <div className="prose prose-sm dark:prose-invert max-w-none text-foreground/90 leading-relaxed">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{changelog}</ReactMarkdown>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
