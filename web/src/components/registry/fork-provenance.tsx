// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { Link } from "@tanstack/react-router";
import { GitFork } from "lucide-react";
import type { ForkProvenance } from "@/lib/types";
import { canonicalRouteParts } from "@/lib/registry-name";

export function ForkedFromChip({ provenance }: { provenance?: ForkProvenance | null }) {
  if (!provenance) return null;
  const parts = provenance.available ? canonicalRouteParts(provenance.namespace ?? undefined, provenance.slug ?? undefined) : null;
  const label = provenance.available ? provenance.qualified_name ?? "Source" : "Source unavailable";
  const content = (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
      <GitFork className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      {provenance.available ? "Forked from " : ""}<span className={provenance.available ? "font-medium text-foreground" : ""}>{label}</span>
      {provenance.available && provenance.version ? ` @${provenance.version}` : ""}
      {provenance.available && provenance.forked_at ? ` · ${new Date(provenance.forked_at).toLocaleDateString()}` : ""}
    </span>
  );
  if (!parts || !provenance.id) return content;
  if (provenance.type === "agent") {
    return <Link to="/agents/$namespace/$slug" params={parts} className="inline-flex hover:underline underline-offset-2">{content}</Link>;
  }
  const type = provenance.type === "sandbox" ? "sandboxes" : provenance.type === "mcp" ? "mcps" : `${provenance.type}s`;
  if (!["mcps", "skills", "hooks", "prompts", "sandboxes"].includes(type)) return content;
  return <Link to="/components/$type/$namespace/$slug" params={{ type: type as "mcps" | "skills" | "hooks" | "prompts" | "sandboxes", ...parts }} className="inline-flex hover:underline underline-offset-2">{content}</Link>;
}

export function ForkMarkers({ provenance, count }: { provenance?: ForkProvenance | null; count?: number }) {
  if (!provenance && !count) return null;
  return (
    <span className="inline-flex items-center gap-2 text-[10px] text-muted-foreground">
      {provenance && <span className="inline-flex items-center gap-1" title="Forked item"><GitFork className="h-3 w-3" aria-hidden="true" /> Fork</span>}
      {!!count && count > 0 && <span className="inline-flex items-center gap-1" title="Public approved forks"><GitFork className="h-3 w-3" aria-hidden="true" /> {count} forks</span>}
    </span>
  );
}
