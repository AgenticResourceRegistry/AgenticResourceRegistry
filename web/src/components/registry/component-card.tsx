// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-License-Identifier: Apache-2.0


import { Link } from "@tanstack/react-router";
import { GitBranch } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { RecommendedBadge } from "@/components/registry/recommended-badge";
import { ForkMarkers } from "@/components/registry/fork-provenance";
import type { ForkProvenance } from "@/lib/types";
import { RegistryName } from "@/components/registry/registry-name";
import { canonicalRouteParts } from "@/lib/registry-name";
import type { RegistryType } from "@/lib/api";

interface ComponentCardProps {
  id: string;
  name: string;
  namespace?: string;
  slug?: string;
  qualified_name?: string;
  type: RegistryType;
  description?: string;
  version?: string;
  status?: string;
  git_url?: string;
  is_recommended?: boolean;
  forked_from?: ForkProvenance | null;
  fork_count?: number;
  className?: string;
}

const TYPE_LABELS: Record<string, string> = {
  mcps: "MCP",
  skills: "Skill",
  hooks: "Hook",
  prompts: "Prompt",
  sandboxes: "Sandbox",
};

export function ComponentCard({
  id,
  name,
  namespace,
  slug,
  qualified_name,
  type,
  description,
  version,
  status,
  git_url,
  is_recommended,
  forked_from,
  fork_count,
  className,
}: ComponentCardProps) {
  const cardClassName = [
    "group block bg-card p-[18px] rounded-xl shadow-sm",
    "transition-all duration-200 ease-out",
    "hover:-translate-y-0.5 hover:shadow-md",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
    className ?? "",
  ].join(" ");
  // Prefer the canonical shareable URL; fall back to the UUID route for
  // payloads that predate namespace/slug, or whose namespace is a legacy
  // verbatim username the canonical route cannot resolve.
  const canonical = canonicalRouteParts(namespace, slug);

  const body = (
    <>
      <div className="flex items-start justify-between gap-2">
        <RegistryName
          item={{ name, namespace, slug, qualified_name }}
          nameClassName="font-display text-sm font-semibold leading-tight"
        />
        <div className="flex items-center gap-1.5">
          {is_recommended && <RecommendedBadge />}
          <ForkMarkers provenance={forked_from} count={fork_count} />
          <Badge variant="outline" className="shrink-0 text-[10px] px-1.5 py-0">
            {TYPE_LABELS[type] ?? type}
          </Badge>
        </div>
      </div>

      {description && (
        <p className="mt-1.5 text-xs text-muted-foreground leading-relaxed line-clamp-2">
          {description}
        </p>
      )}

      <div className="mt-3 flex items-center gap-3 text-xs text-muted-foreground">
        {version && (
          <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
            {version}
          </Badge>
        )}
        {status && status !== "approved" && (
          <Badge
            variant={status === "pending" ? "secondary" : "outline"}
            className="text-[10px] px-1.5 py-0"
          >
            {status}
          </Badge>
        )}
        {git_url && (
          <span className="inline-flex items-center gap-1 ml-auto">
            <GitBranch className="h-3 w-3" />
          </span>
        )}
      </div>
    </>
  );

  if (canonical) {
    return (
      <Link
        to="/components/$type/$namespace/$slug"
        params={{ type, ...canonical }}
        className={cardClassName}
      >
        {body}
      </Link>
    );
  }
  return (
    <Link
      to="/components/$componentId"
      params={{ componentId: id }}
      search={{ type }}
      className={cardClassName}
    >
      {body}
    </Link>
  );
}
