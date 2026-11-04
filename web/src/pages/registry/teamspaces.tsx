// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { Link } from "@tanstack/react-router";
import { useState, useMemo } from "react";
import { Loader2, Lock, RefreshCw, Search, Users } from "lucide-react";
import { PageHeader } from "@/components/layouts/page-header";
import { EmptyState } from "@/components/shared/empty-state";
import { ErrorState } from "@/components/shared/error-state";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { useAllTeams, useClaimPersonalTeamspace, useCreateTeam } from "@/hooks/use-api";
import { hasMinRole } from "@/hooks/use-role-guard";
import { getUserRole } from "@/lib/api";
import { slugifyRegistryText } from "@/lib/registry-name";
import { cn } from "@/lib/utils";
import { EntityGlyph } from "@/components/registry/entity-glyph";
import { StatusBadge } from "@/components/registry/status-badge";
import {
  TypeTabs,
  ViewToggle,
  RegistryToolbar,
  ToolbarSpacer,
  RegistryNote,
  TeamspaceCardShell,
} from "@/components/registry/registry-primitives";
import type { Team } from "@/lib/types";

/* ────────────────────────────────────────────────────────── */
/*  Extended team type with mockup-specific display fields    */
/* ────────────────────────────────────────────────────────── */

interface TeamDisplay extends Team {
  agent_count?: number;
  component_count?: number;
  review_count?: number;
  display_role?: string;
}

/** Convert API data into the display shape used by this page. */
function toDisplay(team: Team): TeamDisplay {
  return { ...team };
}

/* ────────────────────────────────────────────────────────── */
/*  Constants                                                  */
/* ────────────────────────────────────────────────────────── */

type ViewMode = "grid" | "list";
type TeamTab = "all" | "my" | "discoverable";
type VisibilityFilter = "all" | "public" | "private";

const CONTROL_CLASS_NAME =
  "bg-background/80 border-input/90 placeholder:text-muted-foreground/80 hover:border-primary-accent/50 focus-visible:border-primary-accent focus-visible:ring-primary-accent/30";

function slugifyHandle(value: string) {
  const base = slugifyRegistryText(value, { maxLength: 32 });
  return base && base.length < 3 ? `${base}-team` : base;
}

/** Resolve the API visibility for display. */
function effectiveVisibility(team: TeamDisplay): string {
  return team.visibility ?? "private";
}

/** Map visibility to badge color tone matching the HTML mockup exactly:
 *  Private → warning (yellow/amber)
 *  Internal → success (green)
 *  Public → accent (primary blue)
 */
function visibilityTone(vis: string): { bg: string; text: string; label: string } {
  switch (vis) {
    case "private":
      return { bg: "bg-light-yellow", text: "text-dark-yellow", label: "Private" };
    case "public":
      return { bg: "bg-primary-accent/15", text: "text-primary-accent", label: "Public" };
    default: // "internal" or any other
      return { bg: "bg-light-green", text: "text-dark-green", label: "Internal" };
  }
}

/** Visibility badge matching mockup colours exactly. */
function VisibilityBadge({ visibility, className }: { visibility: string; className?: string }) {
  const t = visibilityTone(visibility);
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-2xs font-medium", t.bg, t.text, className)}>
      {t.label}
    </span>
  );
}

function VisibilityReviewStatus({ team, className }: { team: TeamDisplay; className?: string }) {
  if (team.visibility_request_status === "pending") {
    return (
      <div className={cn("flex items-center gap-1.5 text-2xs text-muted-foreground", className)}>
        <StatusBadge status="Pending" />
        <span>Public visibility pending review</span>
      </div>
    );
  }

  if (team.visibility_request_status === "rejected") {
    return (
      <div className={cn("space-y-1 text-2xs text-muted-foreground", className)}>
        <StatusBadge status="Rejected" />
        <p>{team.visibility_rejection_reason || "Public visibility request was rejected."}</p>
      </div>
    );
  }

  return null;
}

/** Role display text. */
function roleLabel(team: TeamDisplay, isAdmin: boolean): string {
  if (team.display_role) return team.display_role;
  if (team.role) return team.role.charAt(0).toUpperCase() + team.role.slice(1);
  return isAdmin ? "Admin access" : "Discoverable";
}

/* ────────────────────────────────────────────────────────── */
/*  Teamspace card (mockup: teamspace-card)                    */
/* ────────────────────────────────────────────────────────── */

function TeamspaceCard({ team, isAdmin }: { team: TeamDisplay; isAdmin: boolean }) {
  return (
    <TeamspaceCardShell href={`/teamspaces/${team.handle}`}>
      {/* Head: icon + visibility badge */}
      <div className="flex items-start justify-between gap-2.5">
        <EntityGlyph type="teamspace" size="lg" labelled />
        <VisibilityBadge visibility={effectiveVisibility(team)} />
      </div>
      <VisibilityReviewStatus team={team} className="mt-2" />

      {/* Name */}
      <h3 className="mt-3.5 text-sm font-medium">{team.name}</h3>

      {/* Handle */}
      <div className="mt-0.5 font-mono text-[10px] text-muted-foreground">
        {team.handle}/
      </div>

      {/* Description */}
      <p className="mt-2 min-h-[34px] text-2xs leading-relaxed text-muted-foreground line-clamp-3">
        {team.description || ""}
      </p>

      {/* Meta row – matches mockup: members · agents · reviews · role */}
      <div className="mt-3.5 flex items-center gap-3 border-t border-border pt-2.5 text-[10px] text-muted-foreground">
        {team.member_count != null && <span>{team.member_count} members</span>}
        {team.agent_count != null && <span>{team.agent_count} Agent Files</span>}
        {team.review_count != null && team.review_count > 0 && (
          <span>{team.review_count} {team.review_count === 1 ? "review" : "reviews"}</span>
        )}
        <span>{roleLabel(team, isAdmin)}</span>
      </div>
    </TeamspaceCardShell>
  );
}

/* ────────────────────────────────────────────────────────── */
/*  Teamspace list row                                         */
/*  Grid: minmax(260px,1.6fr) 100px 90px 82px minmax(130px,.8fr) 80px */
/* ────────────────────────────────────────────────────────── */

function TeamspaceListRow({ team, isAdmin }: { team: TeamDisplay; isAdmin: boolean }) {
  /* Catalogue string: "38 agents · 64 parts" or fallback "—" */
  const catalogue =
    team.agent_count != null
      ? `${team.agent_count} Agent Files` + (team.component_count != null ? ` · ${team.component_count} parts` : "")
      : "—";
  const reviews = team.review_count != null ? String(team.review_count) : "—";

  return (
    <Link
      to="/teamspaces/$handle"
      params={{ handle: team.handle }}
      className="grid min-h-[66px] items-center gap-3 border-t border-border px-[22px] py-3 transition-colors first:border-t-0 hover:bg-surface-raised"
      style={{ gridTemplateColumns: "minmax(260px,1.6fr) 100px 90px 82px minmax(130px,.8fr) 80px" }}
    >
      {/* Identity */}
      <div className="flex items-center gap-[11px] min-w-0">
        <EntityGlyph type="teamspace" size="sm" labelled={false} />
        <div className="min-w-0">
          <strong className="block truncate text-xs font-medium">{team.name}</strong>
          <span className="block truncate mt-0.5 font-mono text-[9px] text-muted-foreground">{team.handle}/</span>
        </div>
      </div>

      {/* Visibility */}
      <div className="min-w-0">
        <VisibilityBadge visibility={effectiveVisibility(team)} />
        <VisibilityReviewStatus team={team} className="mt-1.5" />
      </div>

      {/* Role */}
      <span className="text-[10px] text-muted-foreground">{roleLabel(team, isAdmin)}</span>

      {/* Members */}
      <span className="font-mono text-[10px] text-muted-foreground">
        {team.member_count ?? "-"}
      </span>

      {/* Catalogue */}
      <span className="text-[10px] text-muted-foreground">{catalogue}</span>

      {/* Reviews */}
      <span className="font-mono text-[10px] text-muted-foreground">{reviews}</span>
    </Link>
  );
}

/* ────────────────────────────────────────────────────────── */
/*  Create panel (preserved from original)                     */
/* ────────────────────────────────────────────────────────── */

function CreatePanel({
  onCreated,
  onCancel,
  firstTeamspace = false,
  personalClaimed,
}: {
  onCreated: () => void;
  onCancel?: () => void;
  firstTeamspace?: boolean;
  personalClaimed: boolean;
}) {
  const createTeam = useCreateTeam();
  const claimPersonal = useClaimPersonalTeamspace();
  const [name, setName] = useState("");
  const [handle, setHandle] = useState("");
  const [handleEdited, setHandleEdited] = useState(false);
  const [description, setDescription] = useState("");
  const [visibility, setVisibility] = useState<"public" | "private">("public");
  const generatedHandle = slugifyHandle(name);
  const submittedHandle = handleEdited ? slugifyHandle(handle) : generatedHandle;
  const previewHandle = submittedHandle || "team";

  function submit() {
    createTeam.mutate(
      {
        name: name.trim(),
        handle: submittedHandle || undefined,
        description: description.trim() || undefined,
        visibility,
      },
      {
        onSuccess: () => {
          setName("");
          setHandle("");
          setHandleEdited(false);
          setDescription("");
          setVisibility("public");
          onCreated();
        },
      },
    );
  }

  return (
    <section className="grid min-h-[620px] w-full overflow-hidden rounded-xl bg-card shadow-sm 2xl:grid-cols-[minmax(260px,0.82fr)_minmax(0,1.5fr)]">
      <aside className="grid gap-8 border-b border-border bg-surface-raised/40 p-5 sm:p-6 md:grid-cols-[minmax(0,1fr)_minmax(260px,0.9fr)] 2xl:flex 2xl:flex-col 2xl:justify-between 2xl:border-b-0 2xl:border-r">
        <div>
          <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
            <EntityGlyph type="teamspace" size="sm" labelled={false} />
            <span>Registry identity</span>
          </div>
          <h2 className="mt-6 max-w-xs text-2xl font-semibold tracking-tight">
            {firstTeamspace ? "Give your team a home." : "Define the namespace your team will publish from."}
          </h2>
          <p className="mt-3 max-w-sm text-[13px] leading-6 text-muted-foreground">
            The name is for people. The handle is the stable slug that travels with every install command.
          </p>
        </div>

        <div className="self-center" aria-live="polite" aria-atomic="true">
          <p className="text-xs font-medium text-muted-foreground">Live install identity</p>
          <div className="mt-3 min-h-16 border-b border-border pb-4 font-mono text-xl tracking-tight 2xl:text-2xl">
            <span className="text-muted-foreground">observal agent pull </span>
            <span key={previewHandle} className="inline-block text-foreground">
              {previewHandle}
            </span>
            <span className="text-muted-foreground">/agent-name</span>
          </div>
          <p className="mt-3 text-xs leading-5 text-muted-foreground">
            This preview follows the same namespace rules as the final handle.
          </p>
        </div>

        <div className="hidden border-t border-border pt-4 text-xs leading-5 text-muted-foreground 2xl:block">
          <p className="font-medium text-foreground">After creation</p>
          <p className="mt-1">Invite members, assign roles, and publish Agent Files or components under this namespace.</p>
        </div>
      </aside>

      <div className="flex min-w-0 flex-col">
        <header className="flex items-start justify-between gap-4 border-b border-border px-6 py-6 sm:px-8 sm:py-8">
          <div>
            <p className="text-2xs font-medium uppercase tracking-[0.06em] text-muted-foreground">New teamspace</p>
            <h3 className="mt-2 text-2xl font-semibold tracking-tight">
              {firstTeamspace ? "Create your first teamspace" : "Create a teamspace"}
            </h3>
            <p className="mt-2 max-w-xl text-[13px] leading-6 text-muted-foreground">
              Create a shared namespace for publishing Agent Files and components with your team.
            </p>
          </div>
          {onCancel && (
            <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
              ×
            </Button>
          )}
        </header>

        <form className="flex min-h-0 flex-1 flex-col" onSubmit={(e) => { e.preventDefault(); submit(); }}>
          <div className="grid flex-1 content-start gap-6 p-6 sm:p-8 md:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="team-name">Name</Label>
              <Input
                id="team-name"
                autoFocus={firstTeamspace}
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Platform Tools"
                className={`${CONTROL_CLASS_NAME} h-[34px]`}
              />
            </div>
            <div className="space-y-2">
              <div className="flex items-center justify-between gap-3">
                <Label htmlFor="team-handle">Handle</Label>
                {handleEdited && (
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                    onClick={() => { setHandle(""); setHandleEdited(false); }}
                  >
                    <RefreshCw className="h-3 w-3" /> Use generated
                  </button>
                )}
              </div>
              <Input
                id="team-handle"
                value={handleEdited ? handle : generatedHandle}
                onChange={(e) => {
                  setHandle(slugifyRegistryText(e.target.value, { maxLength: 32, preserveTrailingSeparator: true }));
                  setHandleEdited(true);
                }}
                placeholder="platform-tools"
                className={`${CONTROL_CLASS_NAME} h-[34px] font-mono`}
              />
              <p className="text-[10px] leading-5 text-muted-foreground">
                Generated from the name. You can edit it before creation.
              </p>
            </div>
            <fieldset className="space-y-2 md:col-span-2">
              <legend className="text-sm font-medium">Visibility</legend>
              <div className="grid gap-2 sm:grid-cols-2">
                {([
                  { value: "public" as const, title: "Public", blurb: "Requires reviewer approval. It stays locked and private until approved." },
                  { value: "private" as const, title: "Private", blurb: "Hidden from non-members. Administrators can still access it." },
                ] as const).map((option) => (
                  <label
                    key={option.value}
                    className={cn(
                      "relative cursor-pointer rounded-[11px] border p-3 text-left transition-colors focus-within:ring-2 focus-within:ring-ring",
                      visibility === option.value
                        ? "border-foreground/30 bg-surface-raised"
                        : "border-border hover:border-foreground/20",
                    )}
                  >
                    <input
                      type="radio"
                      name="team-visibility"
                      value={option.value}
                      checked={visibility === option.value}
                      onChange={() => setVisibility(option.value)}
                      className="sr-only"
                    />
                    <span className="block text-xs font-medium">{option.title}</span>
                    <span className="mt-1 block text-[10px] leading-5 text-muted-foreground">{option.blurb}</span>
                  </label>
                ))}
              </div>
            </fieldset>
            <div className="space-y-2 md:col-span-2">
              <Label htmlFor="team-description">
                Description <span className="font-normal text-muted-foreground">(optional)</span>
              </Label>
              <Textarea
                id="team-description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={4}
                placeholder="What this team publishes, who it serves, and what belongs in this namespace"
                className={`${CONTROL_CLASS_NAME} min-h-[110px] resize-y`}
              />
            </div>
          </div>
          <footer className="flex flex-col gap-3 border-t border-border px-6 py-5 sm:flex-row sm:items-center sm:justify-between sm:px-8">
            <p className="text-[10px] leading-5 text-muted-foreground">You can manage members and roles after creation.</p>
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
              {!personalClaimed && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-[34px] text-xs"
                  disabled={claimPersonal.isPending}
                  onClick={() => claimPersonal.mutate(undefined, { onSuccess: () => onCreated() })}
                >
                  {claimPersonal.isPending ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Lock className="mr-1 h-3 w-3" />}
                  Claim private teamspace
                </Button>
              )}
              {onCancel && (
                <Button type="button" variant="outline" size="sm" className="h-[34px] text-xs" onClick={onCancel}>
                  Cancel
                </Button>
              )}
              <Button
                type="submit"
                size="sm"
                className="h-[34px] rounded-[9px] bg-primary px-3.5 text-xs font-medium text-primary-foreground"
                disabled={!name.trim() || createTeam.isPending}
              >
                {createTeam.isPending && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
                {visibility === "public" ? "Submit for review" : "Create teamspace"}
              </Button>
            </div>
          </footer>
        </form>
      </div>
    </section>
  );
}

/* ────────────────────────────────────────────────────────── */
/*  Main page                                                  */
/* ────────────────────────────────────────────────────────── */

export default function TeamspacesPage() {
  const { data: apiTeams = [], isLoading, isError, error, refetch } = useAllTeams();
  const isAdmin = hasMinRole(getUserRole(), "admin");
  const [showCreate, setShowCreate] = useState(false);
  const [teamQuery, setTeamQuery] = useState("");
  const [view, setView] = useState<ViewMode>("grid");
  const [tab, setTab] = useState<TeamTab>("all");
  const [visibilityFilter, setVisibilityFilter] = useState<VisibilityFilter>("all");

  const teams = useMemo(() => apiTeams.map(toDisplay), [apiTeams]);
  const query = teamQuery.trim().toLowerCase();
  const myTeams = useMemo(() => teams.filter((team) => team.role && ["owner", "member", "reviewer"].includes(team.role)), [teams]);
  const discoverable = useMemo(() => teams.filter((team) => team.visibility === "public" || (!team.role && team.visibility !== "private")), [teams]);

  const visibleTeams = useMemo(() => {
    const base = tab === "my" ? myTeams : tab === "discoverable" ? discoverable : teams;
    return base.filter((team) => {
      const matchesQuery = !query || team.name.toLowerCase().includes(query) || team.handle.toLowerCase().includes(query);
      const matchesVisibility = visibilityFilter === "all" || team.visibility === visibilityFilter;
      return matchesQuery && matchesVisibility;
    });
  }, [discoverable, myTeams, query, tab, teams, visibilityFilter]);

  const tabData = useMemo(() => [
    { value: "all", label: "All visible", count: teams.length || undefined },
    { value: "my", label: "My teamspaces", count: myTeams.length || undefined },
    { value: "discoverable", label: "Discoverable", count: discoverable.length || undefined },
  ], [teams.length, myTeams.length, discoverable.length]);

  const firstTeamspace = !isLoading && apiTeams.length === 0;
  const personalClaimed = apiTeams.some((t) => t.is_personal && t.role === "owner");

  if (isError) {
    return (
      <>
        <PageHeader title="Teamspaces" breadcrumbs={[{ label: "Registry", href: "/" }, { label: "Teamspaces" }]} />
        <div className="page-body w-full mx-auto">
          <ErrorState message={error?.message} onRetry={() => refetch()} />
        </div>
      </>
    );
  }

  /* For a first-time setup (no teams at all), show the panel inline instead of as a modal. */
  if (firstTeamspace) {
    return (
      <>
        <PageHeader title="Teamspaces" breadcrumbs={[{ label: "Registry", href: "/" }, { label: "Teamspaces" }]} />
        <div className="page-body w-full mx-auto">
          <CreatePanel
            firstTeamspace
            personalClaimed={personalClaimed}
            onCreated={() => setShowCreate(false)}
          />
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader title="Teamspaces" breadcrumbs={[{ label: "Registry", href: "/" }, { label: "Teamspaces" }]} />

      <div className="page-body w-full mx-auto">
        {/* ── Tabs ── */}
        <TypeTabs
          tabs={tabData}
          active={tab}
          onTabChange={(v) => setTab(v as TeamTab)}
        />

        {/* ── Toolbar ── */}
        <RegistryToolbar>
          <div className="relative w-[min(360px,100%)] shrink-[2]">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <input
              aria-label="Search teamspaces"
              type="text"
              placeholder="Search teamspaces"
              value={teamQuery}
              onChange={(e) => setTeamQuery(e.target.value)}
              className="h-[34px] w-full min-w-0 rounded-[9px] border border-border bg-transparent pl-9 pr-3 text-xs text-foreground outline-none placeholder:text-muted-foreground focus:ring-1 focus:ring-ring"
            />
          </div>
          <select
            aria-label="Filter by visibility"
            value={visibilityFilter}
            onChange={(event) => setVisibilityFilter(event.target.value as VisibilityFilter)}
            className="h-[34px] min-w-[145px] rounded-[9px] border border-border bg-transparent px-3 text-xs text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <option value="all">All visible teamspaces</option>
            <option value="public">Public teamspaces</option>
            <option value="private">Private teamspaces</option>
          </select>
          <ToolbarSpacer />
          <Button
            size="sm"
            className="h-[34px] shrink-0 rounded-[9px] bg-primary px-3.5 text-xs font-medium text-primary-foreground"
            onClick={() => setShowCreate(true)}
          >
            New teamspace
          </Button>
          <ViewToggle view={view} onViewChange={setView} />
        </RegistryToolbar>

        {/* ── Registry note ── */}
        <RegistryNote className="mb-4">
          <strong>Teamspaces are publishing namespaces.</strong> Open one to browse its Agent Files and components, manage members, and clear its review queue.
        </RegistryNote>

        {/* ── Content ── */}
        {isLoading ? (
          <div className="grid gap-3.5 sm:grid-cols-2 lg:grid-cols-3" aria-label="Loading teamspaces">
            {[1, 2, 3].map((i) => (
              <div key={i} className="h-[176px] animate-pulse rounded-xl bg-muted/60" />
            ))}
          </div>
        ) : visibleTeams.length === 0 ? (
          <EmptyState
            icon={Users}
            title={tab === "all" ? "No teamspaces yet" : `No ${tabData.find((t) => t.value === tab)?.label.toLowerCase() ?? "items"}`}
            description={
              query || visibilityFilter !== "all"
                ? "No teamspaces match the selected filters."
                : tab === "all"
                  ? "Create the first teamspace to give your team a shared publishing namespace."
                  : "Nothing here yet."
            }
          />
        ) : view === "grid" ? (
          /* ── Grid view ── */
          <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2 lg:grid-cols-3 animate-in">
            {visibleTeams.map((team) => (
              <TeamspaceCard key={team.id} team={team} isAdmin={isAdmin} />
            ))}
          </div>
        ) : (
          /* ── List view ── */
          <section className="overflow-hidden rounded-xl bg-card shadow-sm animate-in">
            <div className="flex items-center justify-between border-b border-border px-[22px] py-4">
              <div>
                <div className="text-sm font-medium">Visible teamspaces</div>
                <div className="mt-[3px] text-2xs text-muted-foreground">
                  Publishing namespaces you own, belong to, or can discover
                </div>
              </div>
              <span className="font-mono text-xs text-muted-foreground">
                {visibleTeams.length} teamspaces
              </span>
            </div>

            <div
              className="grid min-h-[40px] items-center gap-3 border-b border-border px-[22px] text-2xs font-medium uppercase tracking-[0.05em] text-muted-foreground"
              style={{ gridTemplateColumns: "minmax(260px,1.6fr) 100px 90px 82px minmax(130px,.8fr) 80px" }}
            >
              <span>Teamspace</span>
              <span>Visibility</span>
              <span>Your role</span>
              <span>Members</span>
              <span>Catalogue</span>
              <span>Reviews</span>
            </div>

            <div className="overflow-x-auto">
              {visibleTeams.map((team) => (
                <TeamspaceListRow key={team.id} team={team} isAdmin={isAdmin} />
              ))}
            </div>
          </section>
        )}
      </div>

      <Dialog open={showCreate} onOpenChange={setShowCreate}>
        <DialogContent className="max-h-[min(680px,calc(100vh-40px))] max-w-[min(1060px,calc(100vw-40px))] overflow-y-auto p-0">
          <DialogTitle className="sr-only">Create a teamspace</DialogTitle>
          <CreatePanel
            firstTeamspace={false}
            personalClaimed={personalClaimed}
            onCreated={() => setShowCreate(false)}
          />
        </DialogContent>
      </Dialog>
    </>
  );
}
