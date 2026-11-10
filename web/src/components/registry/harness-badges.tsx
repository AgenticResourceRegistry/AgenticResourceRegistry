// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useHarnesses } from "@/hooks/use-harnesses";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type { HarnessEntry } from "@/lib/api";

function formatHarnessSlug(harness: string): string {
  return harness
    .split("-")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function formatCapability(cap: string): string {
  return cap
    .split("_")
    .map((part) => (part === "mcp" ? "MCP" : part.charAt(0).toUpperCase() + part.slice(1)))
    .join(" ");
}

function getHarnessEntry(
  harness: string,
  harnesses: HarnessEntry[] | undefined,
): HarnessEntry | undefined {
  return harnesses?.find((entry) => entry.name === harness);
}

interface HarnessBadgesProps {
  supportedHarnesses?: string[];
  inferredSupportedHarnesses?: string[];
  max?: number;
  className?: string;
}

export function HarnessBadges({
  supportedHarnesses,
  inferredSupportedHarnesses,
  max = 4,
  className,
}: HarnessBadgesProps) {
  const harnesses = supportedHarnesses && supportedHarnesses.length > 0 ? supportedHarnesses : inferredSupportedHarnesses ?? [];
  const { data: harnessList } = useHarnesses();

  if (harnesses.length === 0) return null;

  const visible = harnesses.slice(0, max);
  const overflow = harnesses.length - max;

  return (
    <TooltipProvider>
      <div className={["flex flex-wrap items-center gap-[5px]", className ?? ""].join(" ")}>
        {visible.map((harness) => {
          const entry = getHarnessEntry(harness, harnessList);
          const displayName = entry?.display_name ?? formatHarnessSlug(harness);
          const capabilities = entry?.capabilities ?? [];
          return (
            <Tooltip key={harness}>
              <TooltipTrigger asChild>
                <span className="rounded-[5px] bg-surface-raised px-[7px] py-0.5 font-mono text-[10px] text-muted-foreground">
                  {displayName}
                </span>
              </TooltipTrigger>
              <TooltipContent side="top" className="max-w-[240px]">
                <p className="font-medium">{displayName}</p>
                {capabilities.length > 0 ? (
                  <p className="text-primary-foreground/80">{capabilities.map(formatCapability).join(" · ")}</p>
                ) : (
                  <p className="text-primary-foreground/70">No capability data available</p>
                )}
              </TooltipContent>
            </Tooltip>
          );
        })}
        {overflow > 0 && <span className="text-[10px] text-muted-foreground">+{overflow} more</span>}
      </div>
    </TooltipProvider>
  );
}
