// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useState } from "react";
import { Loader2, Plus, Search } from "lucide-react";
import type { RegistryItem } from "@/lib/types";
import type { RegistryType } from "@/lib/api";
import { useRegistryList } from "@/hooks/use-api";
import { useComposableOwnerComponents } from "@/hooks/use-registry-api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

const OWNER_WORK_STATUSES = new Set(["draft", "pending", "changes_requested", "rejected"]);

export function ComponentPicker({
  type,
  label,
  selected,
  onToggle,
  onCreateNew,
  targetTeamId,
}: {
  type: RegistryType;
  label?: string;
  selected: Set<string>;
  onToggle: (item: RegistryItem) => void;
  onCreateNew?: () => void;
  targetTeamId?: string;
}) {
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [search]);

  // Both catalogues search on the server. A newly saved owner draft must remain
  // selectable if it is removed from the Agent before either review completes.
  const approved = useRegistryList(type, {
    ...(targetTeamId ? { composable_for_team_id: targetTeamId } : { public_only: "true" }),
    ...(query ? { search: query } : {}),
    limit: "200",
  });
  const mine = useComposableOwnerComponents(type, query);
  const searchLabel = label ?? type;
  const searchTerm = search.trim().toLowerCase();
  const matches = (item: RegistryItem) => !searchTerm ||
    item.name.toLowerCase().includes(searchTerm) ||
    (item.description?.toLowerCase().includes(searchTerm) ?? false);
  const ownDrafts = (mine.data?.items ?? []).filter((item) =>
    OWNER_WORK_STATUSES.has(item.status ?? "") &&
    (item.visibility !== "team" || (!!targetTeamId && item.team_id === targetTeamId)) && matches(item),
  );
  const approvedItems = (approved.data ?? []).filter(matches);
  const loading = query !== search.trim() || approved.isLoading || mine.isLoading;

  function option(item: RegistryItem, ownerDraft: boolean) {
    const isSelected = selected.has(item.id);
    const status = item.status === "changes_requested" ? "Needs changes"
      : item.status === "rejected" ? "Rejected"
      : item.status === "pending" ? "In review" : "Draft";
    return (
      <button
        key={item.id}
        type="button"
        aria-pressed={isSelected}
        onClick={() => onToggle(item)}
        className={`flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
          isSelected ? "bg-accent text-accent-foreground" : "hover:bg-muted/50"
        }`}
      >
        <span className="min-w-0 flex-1">
          <span className="block truncate font-medium">{item.name}</span>
          <span className="block truncate text-[11px] text-muted-foreground">
            {ownerDraft ? status : (item.qualified_name ?? item.name)}
            {item.visibility === "team" ? " · team-only" : " · public"}
          </span>
          {item.description && <span className="block truncate text-xs text-muted-foreground">{item.description}</span>}
        </span>
        {isSelected && <span className="shrink-0 text-xs text-muted-foreground">Added</span>}
      </button>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <div className="relative min-w-0 flex-1">
          <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
          <Input
            placeholder={`Search ${searchLabel}...`}
            aria-label={`Search ${searchLabel}`}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            className="h-9 pl-9 text-sm"
          />
        </div>
        {onCreateNew && (
          <Button type="button" variant="outline" size="sm" className="h-9 shrink-0 text-xs" onClick={onCreateNew}>
            <Plus className="mr-1 h-3 w-3" aria-hidden="true" />
            Create new
          </Button>
        )}
      </div>
      {loading ? (
        <div role="status" className="flex items-center justify-center py-6 text-sm text-muted-foreground">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
          {search ? "Searching…" : "Loading components…"}
        </div>
      ) : (
        <>
          {mine.isError && <p role="alert" className="text-xs text-destructive">
            Could not load your saved components. <button type="button" className="font-medium underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => void mine.refetch()}>Retry</button>
          </p>}
          {approved.isError && <p role="alert" className="text-xs text-destructive">
            Could not load approved components. <button type="button" className="font-medium underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => void approved.refetch()}>Retry</button>
          </p>}
          {ownDrafts.length > 0 && <p className="text-xs font-semibold text-foreground">Your drafts &amp; submissions</p>}
          {(ownDrafts.length > 0 || approvedItems.length > 0) && (
            <div className="max-h-56 space-y-1 overflow-y-auto" aria-label={`${searchLabel} available to add`}>
              {ownDrafts.map((item) => option(item, true))}
              {ownDrafts.length > 0 && approvedItems.length > 0 && <p className="px-3 pt-2 text-xs font-semibold text-foreground">Approved resources</p>}
              {approvedItems.map((item) => option(item, false))}
            </div>
          )}
          {ownDrafts.length > 0 && <p className="text-xs text-muted-foreground">Removing a link here will not delete the component from My submissions.</p>}
          {!mine.isError && !approved.isError && ownDrafts.length === 0 && approvedItems.length === 0 && (
            <p className="py-4 text-center text-sm text-muted-foreground">
              {search ? "No matches. Try another search or create a new component." : `No ${searchLabel.toLowerCase()} available here yet. Create one to get started.`}
            </p>
          )}
          {(approved.data?.length === 200 || (mine.data?.total ?? 0) > 200) && (
            <p className="text-xs text-muted-foreground">Showing the first 200 results. Narrow your search to find others.</p>
          )}
        </>
      )}
    </div>
  );
}
