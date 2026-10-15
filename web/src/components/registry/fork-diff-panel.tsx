// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useId, useState } from "react";
import { GitCompareArrows } from "lucide-react";
import { Button } from "@/components/ui/button";
import { YamlDiffView } from "@/components/review/yaml-diff-view";
import { useForkDiff } from "@/hooks/use-api";
import type { RegistryType } from "@/lib/api";

export function ForkDiffPanel({ type, id, version }: { type: RegistryType; id: string; version: string }) {
  const [open, setOpen] = useState(false);
  const regionId = useId();
  const { data, isLoading, error } = useForkDiff(type, id, version, open);

  return (
    <section className="space-y-3" aria-label="Changes from upstream">
      <Button variant="outline" size="sm" aria-controls={regionId} aria-expanded={open} onClick={() => setOpen(!open)}>
        <GitCompareArrows className="h-4 w-4" /> {open ? "Hide upstream diff" : "Diff vs upstream"}
      </Button>
      {open && (
        <div id={regionId} role="region" aria-label="Upstream diff" className="space-y-2">
          {isLoading && <p role="status" className="text-sm text-muted-foreground">Comparing versions…</p>}
          {error && <p role="alert" className="text-sm text-destructive">Upstream comparison unavailable: {error.message}</p>}
          {data?.unchanged && <p className="text-sm text-muted-foreground">Unchanged from upstream</p>}
          {data && !data.unchanged && (
            <div className="h-72 overflow-hidden rounded-md border border-border">
              <YamlDiffView diff={data.diff} versionA={data.base_version} versionB={data.version} />
            </div>
          )}
        </div>
      )}
    </section>
  );
}
