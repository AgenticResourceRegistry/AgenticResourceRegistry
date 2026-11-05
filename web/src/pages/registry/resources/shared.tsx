// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

/**
 * Pieces shared by the agent and component views of Agentic Resources:
 * URL-backed filter state, toolbar, filter chips, catalog card, list table,
 * and the drafts/submissions panel.
 */

import { useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useRef, useState, type ReactElement, type ReactNode } from "react";
import { FilterBar, type FilterField } from "@/components/registry/filter-bar";
import { CardSkeleton, TableSkeleton } from "@/components/shared/skeleton-layouts";
import { StatusBadge } from "@/components/registry/status-badge";
import { RecommendedBadge } from "@/components/registry/recommended-badge";
import { ForkMarkers } from "@/components/registry/fork-provenance";
import { EntityGlyph } from "@/components/registry/entity-glyph";
import { RegistryName } from "@/components/registry/registry-name";
import { RegistryToolbar, ViewToggle } from "@/components/registry/registry-primitives";
import { useTeams } from "@/hooks/use-api";
import { useOptionalAuth } from "@/hooks/use-auth";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import type { RegistryItem } from "@/lib/types";
import type { ResourcesSearch } from "@/routes/_authed/resources/$type";

export type ViewMode = "grid" | "list";

export function formatOption(value: string): string {
  return value.replaceAll("-", " ").replace(/\b\w/g, (l) => l.toUpperCase());
}

/** "namespace/slug" when both exist, otherwise whatever name the item has. */
function itemHandle(item: RegistryItem): string {
  return item.namespace && item.slug ? `${item.namespace}/${item.slug}` : item.qualified_name || item.name;
}

/** Recommended and fork markers followed by the status badge, as shown on every catalog card and row. */
export function StatusBadges({ item }: { item: RegistryItem }) {
  return (
    <div className="flex items-center gap-1.5">
      {item.is_recommended && <RecommendedBadge />}
      <ForkMarkers provenance={item.forked_from} count={item.fork_count} />
      <StatusBadge status={(item.status as string | undefined) ?? "approved"} />
    </div>
  );
}

/* ─── Filter state ───────────────────────────────── */

export function useResourceFilters() {
  const searchParams = useSearch({ from: "/_authed/resources/$type" });
  const navigate = useNavigate({ from: "/resources/$type" });
  const { isAuthenticated } = useOptionalAuth();
  const { data: teams = [] } = useTeams(isAuthenticated);
  const [search, setSearch] = useState(searchParams.search ?? "");
  const debounced = useDebouncedValue(search);
  // The last search value this page wrote to the URL. A different URL value means
  // it changed from outside (back/forward, a pushed link), so the input must follow.
  const pushedSearch = useRef(searchParams.search);
  useEffect(() => {
    if (searchParams.search === pushedSearch.current) return;
    pushedSearch.current = searchParams.search;
    setSearch(searchParams.search ?? "");
  }, [searchParams.search]);

  return {
    searchParams,
    isAuthenticated,
    teams,
    selectedTeam: teams.find((t) => t.handle === searchParams.team),
    search,
    changeSearch: (text: string) => {
      setSearch(text);
      pushedSearch.current = text || undefined;
      navigate({ search: (cur) => ({ ...cur, search: text || undefined }), replace: true });
    },
    // Cleared immediately so "Clear all" doesn't refetch with the stale term.
    debouncedSearch: search ? debounced : "",
    update: (next: Partial<ResourcesSearch>) =>
      navigate({ search: (cur) => ({ ...cur, ...next }), replace: true }),
    clear: () => {
      setSearch("");
      pushedSearch.current = undefined;
      navigate({ search: {}, replace: true });
    },
  };
}

export type ResourceFilters = ReturnType<typeof useResourceFilters>;

/* ─── Search bar ─────────────────────────────────── */

/** The view (Discover / mine / pending / …), teamspace and publisher filters every type shares. */
export function commonFields(
  f: ResourceFilters,
  views: { value: string; label: string; count?: number }[],
): FilterField[] {
  return [
    ...(f.isAuthenticated
      ? [
          {
            key: "view",
            label: "View",
            hint: "Discover, your submissions, pending review",
            defaultValue: "discover",
            options: views.map((v) => ({ value: v.value, label: v.label, hint: v.count ? String(v.count) : undefined })),
          },
          {
            key: "team",
            label: "Team",
            hint: "Teamspace",
            options: f.teams.map((t) => ({ value: t.handle, label: t.name, hint: t.handle })),
          },
          { key: "namespace", label: "Publisher", hint: "Username of the publisher", user: true },
        ]
      : []),
  ];
}

/** One GitHub-style search bar (text plus qualifier chips), then `actions` and the grid/list toggle. */
export function ResourceToolbar({
  filters: f,
  fields,
  noun,
  layout,
  onLayoutChange,
  actions,
}: {
  filters: ResourceFilters;
  fields: FilterField[];
  noun: string;
  layout: ViewMode;
  onLayoutChange: (v: ViewMode) => void;
  actions?: ReactNode;
}) {
  return (
    <RegistryToolbar>
      <FilterBar
        fields={fields}
        values={f.searchParams as Record<string, string | undefined>}
        onValueChange={(key, value) => f.update({ [key]: value })}
        text={f.search}
        onTextChange={f.changeSearch}
        onClear={f.clear}
        placeholder={`Search ${noun} or filter by…`}
      />
      {actions}
      <ViewToggle view={layout} onViewChange={onLayoutChange} />
    </RegistryToolbar>
  );
}

export function ListSkeleton({ view }: { view: ViewMode }) {
  return view === "grid" ? <CardSkeleton count={6} columns={3} /> : <TableSkeleton rows={6} cols={5} />;
}

/* ─── Catalog card / list ────────────────────────── */

type LinkRender = (p: { className: string; children: ReactNode }) => ReactElement;

export function ResourceCard({
  item,
  glyph,
  labelled,
  render,
  className,
  children,
}: {
  item: RegistryItem;
  glyph: string;
  labelled?: boolean;
  /** Renders the card's `<Link>`; the target differs for agents and components. */
  render: LinkRender;
  className?: string;
  /** Rendered under the description (harness chips, usage footer, …). */
  children?: ReactNode;
}) {
  const version = item.version as string | undefined;
  const description = typeof item.description === "string" ? item.description : undefined;
  return render({
    className: [
      "flex flex-col rounded-xl bg-card p-[18px] shadow-sm min-h-[160px]",
      "transition-all duration-200 ease-out",
      "hover:-translate-y-px hover:shadow-[0_4px_16px_rgba(28,27,24,.08)]",
      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
      className ?? "",
    ].join(" "),
    children: (
      <>
        <div className="flex items-start justify-between gap-3">
          <EntityGlyph type={glyph} size="sm" labelled={labelled} />
          <StatusBadges item={item} />
        </div>
        <div className="mt-3 text-sm font-medium">{item.name}</div>
        <div className="mt-[3px] font-mono text-[10px] text-muted-foreground">
          {itemHandle(item)}
          {version ? ` · ${version}` : ""}
        </div>
        {description && (
          <p className="mt-[9px] flex-1 text-xs leading-relaxed text-muted-foreground line-clamp-3">{description}</p>
        )}
        {children}
      </>
    ),
  });
}

export function CardFooter({ children }: { children: ReactNode }) {
  return <div className="mt-3.5 flex items-center gap-3 border-t border-border pt-3 text-[10px] text-muted-foreground">{children}</div>;
}

export function ResourceList({
  title,
  subtitle,
  count,
  columns,
  headers,
  children,
}: {
  title: string;
  subtitle: string;
  count: string;
  /** CSS grid template shared by the header and every row. */
  columns: string;
  headers: string[];
  children: ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded-xl bg-card shadow-sm animate-in">
      <div className="flex items-center justify-between border-b border-border px-[22px] py-4">
        <div>
          <div className="text-sm font-medium">{title}</div>
          <div className="mt-[3px] text-2xs text-muted-foreground">{subtitle}</div>
        </div>
        <span className="font-mono text-xs text-muted-foreground">{count}</span>
      </div>
      <div
        className="grid min-h-[40px] items-center gap-3 border-b border-border px-[22px] text-2xs font-medium uppercase tracking-[0.05em] text-muted-foreground"
        style={{ gridTemplateColumns: columns }}
      >
        {headers.map((h) => (
          <span key={h}>{h}</span>
        ))}
      </div>
      <div className="overflow-x-auto">{children}</div>
    </section>
  );
}

export function ResourceRow({
  item,
  glyph,
  columns,
  subtitle,
  onClick,
  children,
}: {
  item: RegistryItem;
  glyph: string;
  columns: string;
  subtitle?: string;
  onClick: () => void;
  /** The cells after the identity column. */
  children: ReactNode;
}) {
  return (
    <div
      className="grid min-h-[66px] cursor-pointer items-center gap-3 border-t border-border px-[22px] py-3 transition-colors first:border-t-0 hover:bg-surface-raised"
      style={{ gridTemplateColumns: columns }}
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onClick();
      }}
    >
      <div className="flex items-center gap-[11px] min-w-0">
        <EntityGlyph type={glyph} size="sm" labelled={false} />
        <div className="min-w-0">
          <strong className="block truncate text-xs font-medium">{item.name}</strong>
          <span className="block truncate mt-0.5 font-mono text-[9px] text-muted-foreground">
            {subtitle ?? itemHandle(item)}
          </span>
        </div>
      </div>
      {children}
    </div>
  );
}

export const monoCell = "font-mono text-[10px] text-muted-foreground";

/* ─── Drafts / submissions panel ─────────────────── */

export function SubmissionsPanel({ title, children }: { title: ReactNode; children: ReactNode }) {
  return (
    <section className="mt-3.5 rounded-xl bg-card p-5 shadow-sm">
      <div className="mb-4 flex items-center gap-2 text-sm font-medium">{title}</div>
      <div className="divide-y divide-border">{children}</div>
    </section>
  );
}

export function SubmissionRow({
  item,
  glyph,
  status,
  actions,
}: {
  item: RegistryItem;
  glyph: string;
  status?: string;
  actions: ReactNode;
}) {
  return (
    <div className="flex items-center gap-4 py-3">
      <EntityGlyph type={glyph} size="sm" labelled={false} />
      <div className="min-w-0 flex-1">
        <div className="flex items-start gap-2">
          <RegistryName item={item} nameClassName="text-xs font-medium" />
          <StatusBadge status={status ?? item.status ?? "draft"} />
        </div>
        {item.status === "rejected" && item.rejection_reason && (
          <p className="text-[10px] text-destructive mt-0.5">Rejected: {item.rejection_reason as string}</p>
        )}
        {item.description && <p className="truncate text-[10px] text-muted-foreground mt-0.5">{item.description}</p>}
      </div>
      <div className="flex items-center gap-2 shrink-0">{actions}</div>
    </div>
  );
}
