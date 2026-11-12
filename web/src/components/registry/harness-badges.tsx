// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useHarnesses } from "@/hooks/use-harnesses";
import { HarnessLogo } from "@/components/registry/harness-logo";

function formatHarnessSlug(harness: string): string {
  return harness
    .split("-")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function getHarnessDisplayName(
  harness: string,
  harnesses: { name: string; display_name: string }[] | undefined,
): string {
  return harnesses?.find((entry) => entry.name === harness)?.display_name ?? formatHarnessSlug(harness);
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
    <div className={["flex flex-wrap items-center gap-[5px]", className ?? ""].join(" ")}>
      {visible.map((harness) => (
        <span
          key={harness}
          className="inline-flex items-center gap-1 rounded-[5px] bg-surface-raised px-[7px] py-0.5 font-mono text-[10px] text-muted-foreground"
        >
          <HarnessLogo harness={harness} className="h-3 w-3" />
          {getHarnessDisplayName(harness, harnessList)}
        </span>
      ))}
      {overflow > 0 && <span className="text-[10px] text-muted-foreground">+{overflow} more</span>}
    </div>
  );
}
