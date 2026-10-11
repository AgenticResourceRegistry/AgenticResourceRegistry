// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useMemo, useState } from "react";
import { Link, useSearch } from "@tanstack/react-router";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { ArrowUpRight, Check, MessageSquare, Search } from "lucide-react";
import { auth, getUserRole, prReviews, teams } from "@/lib/api";
import type { ReviewSummary } from "@/lib/types";
import { useAuthGuard } from "@/hooks/use-auth";
import { useReviewSubscription } from "@/hooks/use-review-api";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ReviewStatePill, ReviewTypeGlyph, reviewDate, reviewPerson } from "@/components/review/review-primitives";

const filters = [
  ["open", "All open"],
  ["my_review", "Needs my review"],
  ["ready_to_publish", "Ready to publish"],
  ["changes_requested", "Changes requested"],
  ["mine", "Mine"],
  ["closed", "Closed"],
] as const;
const componentTypes = new Set(["mcp", "skill", "hook", "prompt", "sandbox"]);

function checkStatusLabel(checks: ReviewSummary["checks"]): string {
  if (checks.fail > 0) return "Checks failing";
  if (checks.warn > 0) return "Checks have warnings";
  if (checks.skipped > 0) return checks.pass > 0 ? `Checks passed · ${checks.skipped} skipped` : "Checks skipped";
  return checks.pass > 0 ? "Checks passed" : "No checks recorded";
}

function ReviewRow({ item, nested = false }: { item: ReviewSummary; nested?: boolean }) {
  const approvals = item.gate.approvals;
  const required = item.gate.required;
  const checksLabel = checkStatusLabel(item.checks);
  return <Link to="/review/$number" params={{ number: String(item.number) }}
    className={`group grid grid-cols-[28px_minmax(0,1fr)] items-start gap-3 border-b border-border px-4 py-3.5 transition-colors last:border-0 hover:bg-muted/40 focus-visible:outline-2 focus-visible:outline-ring sm:grid-cols-[28px_minmax(0,1fr)_auto] ${nested ? "sm:pl-12" : ""}`}>
    <ReviewTypeGlyph type={item.subject_type} />
    <div className="min-w-0 space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 break-words text-sm font-semibold group-hover:underline group-hover:underline-offset-4">{item.title}</span>
        <ReviewStatePill state={item.state} />
        {item.requested_from_me && <span className="text-xs font-medium text-foreground">Requested from you</span>}
      </div>
      <p className="flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-xs text-muted-foreground">
        <span className="font-mono">#{item.number}</span>
        <span className="capitalize">{item.subject_type}</span>
        <span>opened by <span className="font-medium text-foreground">{reviewPerson(item.author_name, item.author_id)}</span></span>
        <span>revision {item.head_revision}</span>
        <span>updated {reviewDate(item.updated_at)}</span>
      </p>
      {item.depends_on.length > 0 && <p className="text-xs text-muted-foreground">Waits on {item.depends_on.map(n => `#${n}`).join(", ")}</p>}
      {item.state === "approved" && <p className="text-xs font-medium text-success">Gate is green · awaiting publication</p>}
      {item.state === "changes_requested" && <p className="text-xs font-medium text-foreground">Changes requested · waiting for the author</p>}
      <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground sm:hidden"><span>{approvals}/{required} approvals</span><span aria-hidden="true">·</span><span className="inline-flex items-center gap-1" aria-label={`${item.threads.unresolved} unresolved conversations`}><MessageSquare className="size-3.5" aria-hidden="true" />{item.threads.unresolved}</span><span aria-hidden="true">·</span><span>{checksLabel}</span></p>
    </div>
    <div className="hidden min-w-36 flex-col items-end gap-1.5 text-xs text-muted-foreground sm:flex">
      <span className="flex items-center gap-2" aria-label={`${approvals} of ${required} approvals`}>
        {required > 0 && required <= 5 && <span className="flex gap-1" aria-hidden="true">{Array.from({ length: required }, (_, index) => <span key={index} className={`h-1.5 w-3.5 rounded-full ${index < approvals ? "bg-success" : "bg-border"}`} />)}</span>}
        {approvals}/{required} approvals
      </span>
      <span className="flex items-center gap-2"><span className="inline-flex items-center gap-1" aria-label={`${item.threads.unresolved} unresolved conversations`}><MessageSquare className="size-3.5" aria-hidden="true" />{item.threads.unresolved}</span><span aria-hidden="true">·</span><span>{checksLabel}</span></span>
    </div>
  </Link>;
}

export default function ReviewQueue() {
  useAuthGuard();
  useReviewSubscription();
  const { tab: tabFromUrl } = useSearch({ from: "/_authed/review" });
  const canReviewTeamspaces = ["reviewer", "admin", "super_admin"].includes(getUserRole() ?? "");
  const [tab, setTab] = useState(tabFromUrl === "teamspaces" && !canReviewTeamspaces ? "agents" : tabFromUrl ?? "agents");
  const [filter, setFilter] = useState<string>("open");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [selection, setSelection] = useState(0);
  useEffect(() => { if (tabFromUrl) setTab(tabFromUrl === "teamspaces" && !canReviewTeamspaces ? "agents" : tabFromUrl); }, [tabFromUrl, canReviewTeamspaces]);
  useEffect(() => { const timer = window.setTimeout(() => setQuery(search.trim()), 250); return () => window.clearTimeout(timer); }, [search]);
  const { data: me } = useQuery({ queryKey: ["auth", "whoami"], queryFn: auth.whoami });
  const params: Record<string, string> = {
    state: filter === "closed" ? "completed" : filter === "changes_requested" ? "changes_requested" : "open",
    type: tab === "agents" ? "agent" : "component",
    limit: "25",
  };
  if (filter === "my_review" || filter === "ready_to_publish") params.needs = filter;
  if (filter === "mine") params.author = "me";
  if (query) params.q = query;
  const { data, isPending, isError, refetch, fetchNextPage, hasNextPage, isFetchingNextPage } = useInfiniteQuery({
    queryKey: ["pr-reviews", params],
    queryFn: ({ pageParam }) => prReviews.list({ ...params, ...(pageParam ? { cursor: String(pageParam) } : {}) }),
    initialPageParam: null as number | null,
    getNextPageParam: last => last.next_cursor,
    enabled: tab !== "teamspaces",
  });
  const { data: dependencies } = useQuery({ queryKey: ["pr-reviews", "open-dependencies"], queryFn: () => prReviews.list({ state: "open", type: "component", limit: "100" }), enabled: tab === "agents" });
  const { data: requests, isPending: teamPending, isError: teamError } = useQuery({ queryKey: ["review", "teamspaces"], queryFn: teams.visibilityRequests, enabled: tab === "teamspaces" });
  const rows = useMemo(() => {
    const primary = data?.pages.flatMap(page => page.items) ?? [];
    const pinned = new Set(primary.filter(item => item.subject_type === "agent").flatMap(item => item.depends_on));
    const all = [...primary, ...(dependencies?.items ?? []).filter(item => pinned.has(item.number) && !primary.some(row => row.id === item.id))];
    return all.filter(item => (tab === "components" ? componentTypes.has(item.subject_type) : item.subject_type === "agent" || pinned.has(item.number)) && (filter !== "closed" || ["published", "closed"].includes(item.state)));
  }, [data, dependencies, tab, filter]);
  const byNumber = new Map(rows.map(row => [row.number, row]));
  const children = new Set(rows.flatMap(row => row.depends_on));
  const ordered = rows.flatMap(row => children.has(row.number) && row.subject_type !== "agent" ? [] : [row, ...row.depends_on.map(n => byNumber.get(n)).filter((entry): entry is ReviewSummary => !!entry)]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement || event.altKey || event.metaKey || event.ctrlKey) return;
      if (event.key === "j" || event.key === "k") { event.preventDefault(); setSelection(index => Math.max(0, Math.min(ordered.length - 1, index + (event.key === "j" ? 1 : -1)))); }
      if (event.key === "Enter" && ordered[selection]) window.location.assign(`/review/${ordered[selection].number}`);
      if (event.key === "o" && ordered[selection]) window.open(`/review/${ordered[selection].number}`, "_blank", "noopener");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ordered, selection]);
  const tabs = canReviewTeamspaces ? ["agents", "components", "teamspaces"] as const : ["agents", "components"] as const;
  return <div className="w-full space-y-5 px-4 py-6 sm:px-6">
    <h1 className="sr-only">Review</h1>
    <nav aria-label="Review type" className="flex gap-1 border-b border-border">{tabs.map(name => <button key={name} type="button" onClick={() => { setTab(name); setSelection(0); }} aria-current={tab === name ? "page" : undefined} className={`border-b-2 px-3 py-2.5 text-sm capitalize transition-colors focus-visible:outline-2 focus-visible:outline-ring ${tab === name ? "border-foreground font-semibold text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"}`}>{name}</button>)}</nav>
    {tab !== "teamspaces" && <div className="flex flex-wrap items-center gap-2">
      {filters.map(([key, label]) => <button type="button" key={key} onClick={() => { setFilter(key); setSelection(0); }} aria-pressed={filter === key} className={`rounded-full border px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline-2 focus-visible:outline-ring ${filter === key ? "border-primary bg-primary text-primary-foreground" : "border-border bg-card text-muted-foreground hover:bg-muted hover:text-foreground"}`}>{label}</button>)}
      <label className="relative ml-auto w-full sm:w-60"><Search aria-hidden="true" className="pointer-events-none absolute left-3 top-2 size-4 text-muted-foreground" /><Input aria-label="Search reviews" value={search} onChange={event => setSearch(event.target.value)} placeholder="Search reviews" className="h-8 bg-card pl-9" /></label>
    </div>}
    <div data-testid="review-list" className="overflow-hidden rounded-xl border border-border bg-card shadow-sm">
      <div className="flex flex-wrap items-center gap-2 border-b border-border bg-muted/40 px-4 py-2.5 text-xs text-muted-foreground">
        <span className="font-semibold text-foreground">{tab === "teamspaces" ? `${requests?.length ?? 0} visibility requests` : `${ordered.length} reviews${hasNextPage ? " loaded" : ""}`}</span>
        <span className="ml-auto">{tab === "teamspaces" ? "Public visibility decisions" : "Newest first"}</span>
      </div>
      {tab === "teamspaces" ? teamPending ? <div className="space-y-2 p-4"><Skeleton className="h-16" /><Skeleton className="h-16" /></div> : teamError ? <div className="p-8 text-sm text-destructive">Could not load requests.</div> : requests?.length ? requests.map(team => <Link key={team.team_id} to="/review/teamspace/$teamId" params={{ teamId: team.team_id }} className="flex items-start gap-3 border-b border-border px-4 py-4 text-sm last:border-0 hover:bg-muted/40"><span className="grid size-7 shrink-0 place-items-center rounded-md bg-muted text-xs font-semibold">T</span><span className="min-w-0 flex-1"><span className="font-semibold">{team.name}</span><span className="ml-2 inline-flex rounded-full bg-info/10 px-2 py-0.5 text-xs text-info">Requests public visibility</span><span className="mt-1 block text-xs text-muted-foreground">A teamspace visibility decision; individual items still require review.</span></span><ArrowUpRight className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" /></Link>) : <p className="p-10 text-center text-sm text-muted-foreground">No teamspace requests need a decision.</p>
      : isPending ? <div className="space-y-2 p-4"><Skeleton className="h-16" /><Skeleton className="h-16" /><Skeleton className="h-16" /></div>
      : isError ? <div className="p-8 text-center text-sm">Could not load reviews. <button className="underline underline-offset-4" type="button" onClick={() => refetch()}>Try again</button></div>
      : ordered.length ? ordered.map((item, index) => <div key={item.id} className={index === selection ? "bg-muted/20" : ""}><ReviewRow item={item} nested={children.has(item.number) && item.subject_type !== "agent"} /></div>)
      : <div className="p-12 text-center"><Check className="mx-auto size-6 text-muted-foreground" aria-hidden="true" /><p className="mt-3 text-sm font-semibold">{hasNextPage ? "No matches on this page" : "Nothing to review here"}</p><p className="mt-1 text-xs text-muted-foreground">{hasNextPage ? "Load more reviews to keep searching." : "Try another filter or come back after the next submission."}</p></div>}
    </div>
    {tab !== "teamspaces" && <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground"><p>{ordered.length} reviews · j/k to select · Enter to open · o for new tab{me && getUserRole() === "super_admin" ? " · Policy in Settings" : ""}</p>{hasNextPage && <button type="button" onClick={() => fetchNextPage()} disabled={isFetchingNextPage} className="rounded-[var(--radius-control-compact)] border border-border bg-card px-3 py-2 font-medium text-foreground hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-50">{isFetchingNextPage ? "Loading reviews…" : "Load more reviews"}</button>}</div>}
  </div>;
}
