// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import type { ReviewState, ReviewSummary } from "@/lib/types";

const states: Record<ReviewState, { label: string; color: string }> = {
  open: { label: "Open", color: "bg-info/10 text-info" },
  changes_requested: { label: "Changes requested", color: "bg-warning/15 text-foreground" },
  approved: { label: "Ready to publish", color: "bg-success/10 text-success" },
  published: { label: "Published", color: "bg-success text-success-foreground" },
  closed: { label: "Closed", color: "bg-destructive/10 text-destructive" },
};

const types: Record<ReviewSummary["subject_type"], string> = {
  agent: "bg-tag-agent-bg text-tag-agent",
  mcp: "bg-tag-mcp-bg text-tag-mcp",
  skill: "bg-tag-skill-bg text-tag-skill",
  hook: "bg-tag-hook-bg text-tag-hook",
  prompt: "bg-tag-prompt-bg text-tag-prompt",
  sandbox: "bg-muted text-muted-foreground",
};

export function ReviewStatePill({ state, large = false }: { state: ReviewState; large?: boolean }) {
  const { label, color } = states[state];
  return <span className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 font-medium ${large ? "text-xs" : "text-2xs"} ${color}`}>
    <span className="size-1.5 rounded-full bg-current" aria-hidden="true" />{label}
  </span>;
}

export function ReviewTypeGlyph({ type }: { type: ReviewSummary["subject_type"] }) {
  return <span className={`inline-grid size-7 shrink-0 place-items-center rounded-md text-3xs font-bold uppercase ${types[type]}`} aria-hidden="true">
    {type.slice(0, 2)}
  </span>;
}

export function ReviewAvatar({ name }: { name: string }) {
  return <span className="inline-grid size-6 shrink-0 place-items-center rounded-full bg-surface-raised text-2xs font-semibold text-foreground ring-2 ring-background" aria-hidden="true">{name[0]?.toUpperCase() ?? "?"}</span>;
}

export function reviewPerson(name?: string | null, id?: string | null) {
  return name || (id ? `User ${id.slice(0, 8)}` : "Former user");
}

export function reviewDate(value: string) {
  return new Date(value).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}
