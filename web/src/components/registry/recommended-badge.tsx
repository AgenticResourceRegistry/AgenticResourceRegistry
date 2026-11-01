// SPDX-FileCopyrightText: 2026 Chandhini Veerabuthiran <Chandhini03@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0

import { Award } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useSetRecommended } from "@/hooks/use-api";
import type { RecommendableType } from "@/lib/types";
import { cn } from "@/lib/utils";

const RECOMMENDED_TONE = "bg-light-blue text-dark-blue";

/** A small "Recommended" pill in the StatusBadge treatment. */
export function RecommendedBadge({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-2xs font-medium",
        RECOMMENDED_TONE,
        className,
      )}
    >
      <Award className="h-3 w-3" />
      Recommended
    </span>
  );
}

/** Admin-only sidebar control that marks or unmarks an entity as recommended. */
export function RecommendedToggle({
  entityType,
  entityId,
  isRecommended,
}: {
  entityType: RecommendableType;
  entityId: string;
  isRecommended: boolean;
}) {
  const mutation = useSetRecommended();
  return (
    <div className="border border-border rounded-md p-4 space-y-2">
      <h3 className="text-xs font-semibold font-display uppercase tracking-wider text-muted-foreground">
        Admin curation
      </h3>
      <Button
        variant="outline"
        size="sm"
        className={cn("h-8 gap-1.5", isRecommended && `${RECOMMENDED_TONE} border-transparent hover:bg-light-blue hover:text-dark-blue`)}
        disabled={mutation.isPending}
        onClick={() => mutation.mutate({ entity_type: entityType, entity_id: entityId, recommended: !isRecommended })}
      >
        <Award className="h-3.5 w-3.5" />
        {mutation.isPending ? "Saving..." : isRecommended ? "Remove recommendation" : "Mark as recommended"}
      </Button>
    </div>
  );
}
