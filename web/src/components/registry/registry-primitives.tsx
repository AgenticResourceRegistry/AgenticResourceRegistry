// SPDX-FileCopyrightText: 2026 Chandhini Veerabuthiran <chandhini03@users.noreply.github.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

/**
 * Shared visual primitives for all Registry surfaces.
 *
 * Every component here mirrors a pattern from the approved mock design and is
 * reused across all seven Registry tabs (Home, Agents, Leaderboard, Components,
 * Teamspaces, Builder, Wiki) so the visual language stays consistent.
 *
 * Uses the existing OKLCH tokens from app.css — no inline hex or rgb.
 */

import { cn } from "@/lib/utils";
import { Link } from "@tanstack/react-router";
import { Search } from "lucide-react";
import type { ReactNode } from "react";

/* ─── Stat Strip ─────────────────────────────────── */

interface StatCell {
  label: string;
  value: string | number;
}

export function StatStrip({
  cells,
  className,
}: {
  cells: StatCell[];
  className?: string;
}) {
  return (
    <div
      className={cn(
        "grid overflow-hidden rounded-xl bg-border",
        cells.length === 4
          ? "grid-cols-2 gap-px sm:grid-cols-4"
          : "grid-cols-1 gap-px sm:grid-cols-3",
        className,
      )}
    >
      {cells.map((c) => (
        <div key={c.label} className="bg-card px-5 py-4">
          <span className="text-xs text-muted-foreground">{c.label}</span>
          <strong className="mt-1.5 block text-2xl font-bold tracking-tight tabular-nums">
            {c.value}
          </strong>
        </div>
      ))}
    </div>
  );
}

/* ─── Panel (card with header) ───────────────────── */

export function Panel({
  title,
  subtitle,
  action,
  badge,
  children,
  className,
  wide,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
  badge?: ReactNode;
  children: ReactNode;
  className?: string;
  wide?: boolean;
}) {
  return (
    <section
      className={cn(
        "rounded-xl bg-card p-5 shadow-sm",
        wide && "col-span-full xl:col-span-1",
        className,
      )}
    >
      <header className="mb-[16px] flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-base font-medium">{title}</h2>
          {subtitle && (
            <p className="mt-[3px] text-2xs text-muted-foreground">{subtitle}</p>
          )}
        </div>
        <div className="flex items-center gap-2">
          {badge}
          {action}
        </div>
      </header>
      {children}
    </section>
  );
}

/* ─── Compact Row ────────────────────────────────── */

export function CompactRow({
  icon,
  title,
  description,
  meta,
  href,
  onClick,
  className,
}: {
  icon: ReactNode;
  title: string;
  description?: string;
  meta?: ReactNode;
  href?: string;
  onClick?: () => void;
  className?: string;
}) {
  const inner = (
    <>
      <div className="shrink-0">{icon}</div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-xs font-medium">{title}</div>
        {description && (
          <div className="mt-0.5 line-clamp-2 text-[10px] leading-relaxed text-muted-foreground">
            {description}
          </div>
        )}
      </div>
      {meta && (
        <div className="shrink-0 text-right font-mono text-[10px] text-muted-foreground">
          {meta}
        </div>
      )}
    </>
  );

  const rowClass = cn(
    "grid grid-cols-[32px_minmax(0,1fr)_auto] items-center gap-2.5 border-t border-border py-3 first:border-t-0 first:pt-0 last:pb-0",
    (href || onClick) &&
      "cursor-pointer rounded-lg -mx-2 px-2 transition-colors hover:bg-surface-raised",
    className,
  );

  if (href) {
    return (
      <Link to={href} className={rowClass}>
        {inner}
      </Link>
    );
  }
  if (onClick) {
    return (
      <button type="button" onClick={onClick} className={cn(rowClass, "w-full text-left")}>
        {inner}
      </button>
    );
  }
  return <div className={rowClass}>{inner}</div>;
}

/* ─── Registry Toolbar ───────────────────────────── */

export function RegistryToolbar({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "mb-3.5 flex flex-wrap items-center gap-2",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function ToolbarSpacer() {
  return <span className="flex-1" />;
}

/* ─── View Toggle (grid / list) ──────────────────── */

export function ViewToggle({
  view,
  onViewChange,
}: {
  view: "grid" | "list";
  onViewChange: (v: "grid" | "list") => void;
}) {
  return (
    <div className="flex overflow-hidden rounded-[9px] bg-surface-raised">
      <button
        type="button"
        onClick={() => onViewChange("grid")}
        className={cn(
          "grid h-[34px] w-9 place-items-center text-muted-foreground transition-colors",
          view === "grid" && "bg-card text-foreground shadow-sm",
        )}
        aria-label="Grid view"
        aria-pressed={view === "grid"}
      >
        <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
          <rect x="1" y="1" width="6" height="6" rx="1" />
          <rect x="9" y="1" width="6" height="6" rx="1" />
          <rect x="1" y="9" width="6" height="6" rx="1" />
          <rect x="9" y="9" width="6" height="6" rx="1" />
        </svg>
      </button>
      <button
        type="button"
        onClick={() => onViewChange("list")}
        className={cn(
          "grid h-[34px] w-9 place-items-center border-l border-border text-muted-foreground transition-colors",
          view === "list" && "bg-card text-foreground shadow-sm",
        )}
        aria-label="List view"
        aria-pressed={view === "list"}
      >
        <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
          <rect x="1" y="2" width="14" height="2" rx="0.5" />
          <rect x="1" y="7" width="14" height="2" rx="0.5" />
          <rect x="1" y="12" width="14" height="2" rx="0.5" />
        </svg>
      </button>
    </div>
  );
}

/* ─── Type Tabs ──────────────────────────────────── */

interface TypeTab {
  value: string;
  label: string;
  count?: number | string;
}

export function TypeTabs({
  tabs,
  active,
  onTabChange,
  className,
  border = true,
}: {
  tabs: TypeTab[];
  active: string;
  onTabChange: (v: string) => void;
  className?: string;
  border?: boolean;
}) {
  return (
    <div
      className={cn(
        "mb-[15px] flex gap-0 overflow-x-auto",
        border && "border-b border-border",
        className,
      )}
    >
      {tabs.map((tab) => (
        <button
          key={tab.value}
          type="button"
          onClick={() => onTabChange(tab.value)}
          className={cn(
            "whitespace-nowrap border-b-2 border-transparent px-3.5 py-2.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground",
            active === tab.value && "border-foreground text-foreground",
          )}
        >
          {tab.label}
          {tab.count != null && (
            <span className="ml-1.5 font-mono text-[10px] opacity-70">
              {tab.count}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}

/* ─── Segmented Control ──────────────────────────── */

export function SegmentedControl({
  options,
  value,
  onChange,
  className,
}: {
  options: { value: string; label: string }[];
  value: string;
  onChange: (v: string) => void;
  className?: string;
}) {
  return (
    <div className={cn("inline-flex rounded-[11px] bg-surface-raised p-1", className)}>
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => onChange(opt.value)}
          className={cn(
            "min-w-[40px] rounded-lg px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors",
            value === opt.value &&
              "bg-card text-foreground shadow-sm",
          )}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

/* ─── Intent Search Box ──────────────────────────── */

export function IntentSearch({
  value,
  onChange,
  onSubmit,
  placeholder = "Search…",
  className,
  kbdHint,
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit?: () => void;
  placeholder?: string;
  className?: string;
  /** Optional keyboard shortcut hint rendered before the submit button. */
  kbdHint?: string;
}) {
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit?.();
      }}
      className={cn(
        "flex items-center gap-2.5 rounded-[11px] border border-border bg-surface-raised pl-4 pr-2.5 h-[50px]",
        className,
      )}
    >
      <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="min-w-0 flex-1 border-0 bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
      />
      {kbdHint && (
        <kbd className="hidden shrink-0 rounded-[5px] bg-card px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground sm:inline-block">
          {kbdHint}
        </kbd>
      )}
      {onSubmit && (
        <button
          type="submit"
          disabled={!value.trim()}
          className="rounded-[9px] bg-primary px-3.5 py-1.5 text-xs font-medium text-primary-foreground transition-opacity hover:opacity-85 disabled:opacity-50"
        >
          Search
        </button>
      )}
    </form>
  );
}

/* ─── Intent Chip ────────────────────────────────── */

export function IntentChip({
  children,
  onClick,
  href,
}: {
  children: ReactNode;
  onClick?: () => void;
  href?: string;
}) {
  const cls =
    "rounded-full bg-surface-raised px-2.5 py-1.5 text-2xs font-medium text-muted-foreground transition-colors hover:text-foreground";
  if (href) {
    return (
      <Link to={href} className={cls}>
        {children}
      </Link>
    );
  }
  return (
    <button type="button" onClick={onClick} className={cls}>
      {children}
    </button>
  );
}

/* ─── Registry Note ──────────────────────────────── */

export function RegistryNote({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-[11px] bg-surface-raised px-4 py-3 text-2xs leading-relaxed text-muted-foreground",
        className,
      )}
    >
      {children}
    </div>
  );
}

/* ─── Status Strip ───────────────────────────────── */

export function StatusStrip({
  icon,
  title,
  subtitle,
  badge,
  action,
  className,
}: {
  icon: ReactNode;
  title: string;
  subtitle?: string;
  badge?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "mb-3.5 flex items-center gap-[11px] rounded-[11px] bg-surface-raised px-3.5 py-3",
        className,
      )}
    >
      <div className="shrink-0">{icon}</div>
      <div className="min-w-0 flex-1">
        <div className="text-xs font-medium">{title}</div>
        {subtitle && (
          <div className="text-[10px] text-muted-foreground">{subtitle}</div>
        )}
      </div>
      {badge}
      {action}
    </div>
  );
}

/* ─── Catalog Grid ───────────────────────────────── */

export function CatalogGrid({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "grid grid-cols-1 gap-3.5 sm:grid-cols-2 lg:grid-cols-3",
        className,
      )}
    >
      {children}
    </div>
  );
}

/* ─── Registry Home Grid (2-column layout) ───────── */

export function RegistryHomeGrid({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "grid items-start gap-3.5",
        "grid-cols-1 xl:grid-cols-[minmax(0,1.55fr)_minmax(260px,0.72fr)]",
        className,
      )}
    >
      {children}
    </div>
  );
}

/* ─── Ranking Row ────────────────────────────────── */

export function RankingRow({
  position,
  children,
  downloads,
  rating,
  change,
  isTop,
  className,
}: {
  position: number | string;
  children: ReactNode;
  downloads?: string;
  rating?: string;
  change?: string;
  isTop?: boolean;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "grid grid-cols-[42px_minmax(0,1fr)_120px_90px] items-center gap-3 border-t border-border px-5 py-3.5 transition-colors hover:bg-surface-raised",
        className,
      )}
    >
      <span
        className={cn(
          "font-mono text-xs font-semibold text-muted-foreground",
          isTop && "text-base text-warning",
        )}
      >
        {typeof position === "number"
          ? String(position).padStart(2, "0")
          : position}
      </span>
      <div className="min-w-0">{children}</div>
      {downloads !== undefined && (
        <span className="text-right font-mono text-2xs">{downloads} pulls</span>
      )}
      {rating !== undefined && (
        <span className="text-right font-mono text-2xs">★ {rating}</span>
      )}
      {change !== undefined && (
        <span className="text-right font-mono text-[10px] text-success">
          {change}
        </span>
      )}
    </div>
  );
}

/* ─── Ranking Head ───────────────────────────────── */

export function RankingHead({ entityLabel = "Agent File" }: { entityLabel?: string }) {
  return (
    <div className="grid grid-cols-[42px_minmax(0,1fr)_120px_90px] items-center gap-3 px-5 py-2.5 text-2xs font-medium uppercase tracking-[0.05em] text-muted-foreground">
      <span>Rank</span>
      <span>{entityLabel}</span>
      <span className="text-right">Downloads</span>
      <span className="text-right">Rating</span>
    </div>
  );
}

/* ─── Leader Feature Card ────────────────────────── */

export function LeaderFeatureCard({
  rank,
  title,
  handle,
  description,
  stats,
  children,
  className,
}: {
  rank: string;
  title: string;
  handle: string;
  description: string;
  stats: { label: string; value: string | ReactNode }[];
  children?: ReactNode;
  className?: string;
}) {
  return (
    <article
      className={cn(
        "relative overflow-hidden rounded-xl bg-card p-7 shadow-sm",
        className,
      )}
    >
      {children}
      <span className="relative z-[1] text-2xs font-semibold uppercase tracking-[0.08em] font-mono text-muted-foreground">
        {rank}
      </span>
      <h2 className="relative z-[1] mt-5 text-2xl font-medium">{title}</h2>
      <div className="relative z-[1] font-mono text-[10px] text-muted-foreground">{handle}</div>
      <p className="relative z-[1] mt-2.5 max-w-[590px] text-xs leading-relaxed text-muted-foreground">
        {description}
      </p>
      <div className="relative z-[1] mt-6 flex flex-wrap gap-7">
        {stats.map((s) => (
          <div key={s.label}>
            <span className="text-[10px] text-muted-foreground">{s.label}</span>
            <strong className="mt-1 block text-base font-semibold">{s.value}</strong>
          </div>
        ))}
      </div>
    </article>
  );
}

/* ─── Builder Step Nav ───────────────────────────── */

interface BuilderStepDef {
  id: string;
  label: string;
  description: string;
}

export function BuilderStepNav({
  steps,
  active,
  onStepClick,
  className,
}: {
  steps: BuilderStepDef[];
  active: string;
  onStepClick?: (id: string) => void;
  className?: string;
}) {
  const activeIdx = steps.findIndex((s) => s.id === active);
  return (
    <div className={cn("mb-4 grid grid-cols-2 gap-1.5 sm:grid-cols-4", className)}>
      {steps.map((step, i) => {
        const isDone = i < activeIdx;
        const isActive = step.id === active;
        return (
          <button
            key={step.id}
            type="button"
            onClick={() => onStepClick?.(step.id)}
            className={cn(
              "flex items-center gap-2 rounded-[11px] bg-card px-3 py-2.5 text-left shadow-sm transition-colors",
              isActive && "bg-surface-raised text-foreground",
              !isActive && !isDone && "text-muted-foreground",
              isDone && "text-foreground",
            )}
          >
            <span
              className={cn(
                "grid h-[22px] w-[22px] shrink-0 place-items-center rounded-full bg-surface-raised font-mono text-[10px]",
                isActive && "border border-foreground/70 bg-transparent text-foreground",
                isDone && "text-success",
              )}
            >
              {isDone ? "✓" : i + 1}
            </span>
            <span className="min-w-0">
              <strong className="block text-2xs font-medium">{step.label}</strong>
              <small className="block text-[10px] text-muted-foreground">
                {step.description}
              </small>
            </span>
          </button>
        );
      })}
    </div>
  );
}

/* ─── Wiki Split Layout ──────────────────────────── */

export function WikiSplitLayout({
  sidebar,
  children,
  className,
}: {
  sidebar: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "grid grid-cols-1 gap-3.5 md:grid-cols-[200px_minmax(0,1fr)]",
        className,
      )}
    >
      <aside className="self-start rounded-xl bg-card p-2 shadow-sm">
        {sidebar}
      </aside>
      <article className="rounded-xl bg-card p-6 shadow-sm">{children}</article>
    </div>
  );
}

/* ─── Wiki Nav Item ──────────────────────────────── */

export function WikiNavItem({
  children,
  active,
  onClick,
}: {
  children: ReactNode;
  active?: boolean;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex w-full items-center gap-2.5 rounded-[10px] px-3 py-2.5 text-left text-xs transition-colors",
        active
          ? "bg-surface-raised text-foreground font-medium"
          : "text-muted-foreground hover:bg-surface-raised hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

/* ─── Teamspace Card (mock-aligned) ──────────────── */

export function TeamspaceCardShell({
  children,
  href,
  className,
  dashed,
}: {
  children: ReactNode;
  href?: string;
  className?: string;
  dashed?: boolean;
}) {
  const cls = cn(
    "flex min-h-[176px] flex-col rounded-xl bg-card p-[18px] shadow-sm transition-all duration-200 ease-out",
    "hover:-translate-y-0.5 hover:shadow-md",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
    dashed && "border border-dashed border-border",
    className,
  );
  if (href) {
    return (
      <Link to={href} className={cls}>
        {children}
      </Link>
    );
  }
  return <div className={cls}>{children}</div>;
}
