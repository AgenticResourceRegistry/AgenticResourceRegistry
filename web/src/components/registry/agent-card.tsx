// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0


import { Link } from "@tanstack/react-router";
import { ArrowDownToLine, Puzzle, Star } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { RecommendedBadge } from "@/components/registry/recommended-badge";
import { ForkMarkers } from "@/components/registry/fork-provenance";
import type { ForkProvenance } from "@/lib/types";
import { HarnessBadges } from "@/components/registry/harness-badges";
import { RegistryName } from "@/components/registry/registry-name";
import { canonicalRouteParts } from "@/lib/registry-name";
import { compactNumber } from "@/lib/utils";

interface AgentCardProps {
  id: string;
  name: string;
  namespace?: string;
  slug?: string;
  qualified_name?: string;
  description?: string;
  model_name?: string;
  owner?: string;
  created_by_username?: string | null;
  downloads?: number;
  score?: number;
  version?: string;
  component_count?: number;
  status?: string;
  supported_harnesses?: string[];
  inferred_supported_harnesses?: string[];
  is_recommended?: boolean;
  forked_from?: ForkProvenance | null;
  fork_count?: number;
  className?: string;
}

export function AgentCard({
  id,
  name,
  namespace,
  slug,
  qualified_name,
  description,
  owner,
  created_by_username,
  downloads,
  score,
  version,
  component_count,
  supported_harnesses,
  inferred_supported_harnesses,
  is_recommended,
  forked_from,
  fork_count,
  className,
}: AgentCardProps) {
  const cardClassName = [
    "group flex h-full min-h-40 flex-col rounded-xl bg-card p-[18px] shadow-sm",
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
      <div className="flex items-start justify-between gap-3">
        <RegistryName
          item={{ name, namespace, slug, qualified_name }}
          nameClassName="font-display text-sm font-semibold leading-tight"
        />
        <div className="flex items-center gap-1.5">
          {is_recommended && <RecommendedBadge />}
          <ForkMarkers provenance={forked_from} count={fork_count} />
          {version && (
            <Badge variant="secondary" className="shrink-0 text-[10px] px-1.5 py-0">
              {version}
            </Badge>
          )}
        </div>
      </div>

      {description && (
        <p className="mt-1.5 text-xs text-muted-foreground leading-relaxed line-clamp-2">
          {description}
        </p>
      )}

      {/* The namespace under the name already reads as @owner, so only fall back
          to the creator line when the item carries no namespace. */}
      {!namespace && !qualified_name?.includes("/") && (created_by_username || owner) && (
        <p className="mt-2 text-[11px] text-muted-foreground/70 truncate">
          {created_by_username ? `@${created_by_username}` : owner}
        </p>
      )}

      <div className="mt-auto flex items-center gap-4 border-t border-border pt-3 mt-3.5 text-[10px] text-muted-foreground">
        {downloads != null && (
          <span className="inline-flex items-center gap-1">
            <ArrowDownToLine className="h-3 w-3" />
            {compactNumber(downloads)}
          </span>
        )}
        {score != null && (
          <span className="inline-flex items-center gap-1">
            <Star className="h-3 w-3" />
            {score.toFixed(1)}
          </span>
        )}
        {component_count != null && component_count > 0 && (
          <span className="inline-flex items-center gap-1">
            <Puzzle className="h-3 w-3" />
            {component_count}
          </span>
        )}
      </div>

      <HarnessBadges
        supportedHarnesses={supported_harnesses}
        inferredSupportedHarnesses={inferred_supported_harnesses}
        max={3}
        className="mt-2"
      />
    </>
  );

  if (canonical) {
    return (
      <Link to="/agents/$namespace/$slug" params={canonical} className={cardClassName}>
        {body}
      </Link>
    );
  }
  return (
    <Link to="/agents/$agentId" params={{ agentId: id }} className={cardClassName}>
      {body}
    </Link>
  );
}
