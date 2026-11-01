// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { Link } from "@tanstack/react-router";
import { ClipboardCheck } from "lucide-react";
import type { ReviewSummary } from "@/lib/types";
import { EmptyState } from "@/components/shared/empty-state";
import { ErrorState } from "@/components/shared/error-state";
import { TableSkeleton } from "@/components/shared/skeleton-layouts";

export function TeamReviewLinks({ items, isLoading, isError, errorMessage, onRetry, onOpenSettings, canEditPolicy }: {
  items: ReviewSummary[];
  isLoading: boolean;
  isError: boolean;
  errorMessage?: string;
  onRetry: () => void;
  onOpenSettings: () => void;
  canEditPolicy: boolean;
}) {
  if (isLoading) return <TableSkeleton rows={3} cols={3} />;
  if (isError) return <ErrorState message={errorMessage} onRetry={onRetry} />;
  return <div className="space-y-4">
    {canEditPolicy && <button type="button" onClick={onOpenSettings} className="inline-flex rounded-lg border border-border bg-card px-3 py-2 text-xs font-medium hover:bg-muted">Teamspace review settings →</button>}
    {!items.length ? <EmptyState icon={ClipboardCheck} title="Nothing waiting on you" description="Open teamspace reviews appear here when a version is submitted." />
      : <div className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-card">{items.map(item => <Link key={item.id} to="/review/$number" params={{ number: String(item.number) }} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 text-sm hover:bg-muted/50"><span className="font-medium">{item.title}<span className="ml-2 text-xs font-normal capitalize text-muted-foreground">{item.subject_type} · revision {item.head_revision}</span></span><span className="text-xs text-muted-foreground">#{item.number} · {item.gate.approvals}/{item.gate.required} approvals</span></Link>)}</div>}
  </div>;
}
