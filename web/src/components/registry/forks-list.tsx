// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { Link } from "@tanstack/react-router";
import { GitFork } from "lucide-react";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/shared/empty-state";
import type { ForksResponse, RegistryItem } from "@/lib/types";
import type { RegistryType } from "@/lib/api";
import { ForkMarkers } from "@/components/registry/fork-provenance";

export function ForksList({ type, result, page, onPage, isLoading, error }: {
  type: RegistryType;
  result?: ForksResponse<RegistryItem>;
  page: number;
  onPage: (page: number) => void;
  isLoading: boolean;
  error?: Error | null;
}) {
  if (isLoading) return <p className="py-8 text-sm text-muted-foreground" role="status">Loading public forks…</p>;
  if (error) return <p className="py-8 text-sm text-destructive" role="alert">Could not load forks: {error.message}</p>;
  if (!result?.items.length && page === 1) return <EmptyState icon={GitFork} title="No public forks yet" description="Approved public forks will appear here." />;
  return (
    <div className="space-y-4">
      <ul className="divide-y divide-border rounded-md border border-border">
        {result?.items.map((item) => (
          <li key={item.id}>
            {type === "agents" ? (
              <Link to="/agents/$agentId" params={{ agentId: item.id }} className="block p-4 hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                <span className="font-medium text-sm">{item.qualified_name ?? item.name}</span>
                {item.description && <p className="mt-1 text-xs text-muted-foreground line-clamp-2">{item.description}</p>}
                <ForkMarkers provenance={item.forked_from} count={item.fork_count} />
              </Link>
            ) : (
              <Link to="/components/$componentId" params={{ componentId: item.id }} search={{ type }} className="block p-4 hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                <span className="font-medium text-sm">{item.qualified_name ?? item.name}</span>
                {item.description && <p className="mt-1 text-xs text-muted-foreground line-clamp-2">{item.description}</p>}
                <ForkMarkers provenance={item.forked_from} count={item.fork_count} />
              </Link>
            )}
          </li>
        ))}
      </ul>
      <div className="flex items-center justify-between gap-4 text-xs text-muted-foreground">
        <span>{result?.total ?? 0} public fork{result?.total === 1 ? "" : "s"}</span>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</Button>
          <span>Page {page}</span>
          <Button variant="outline" size="sm" disabled={!result || page * result.page_size >= result.total} onClick={() => onPage(page + 1)}>Next</Button>
        </div>
      </div>
    </div>
  );
}
