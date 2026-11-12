// SPDX-License-Identifier: Apache-2.0
//
// Alternative harness logo component using inline SVGs instead of @lobehub/icons
// to avoid CSP issues with external script loading.

import type { FC, SVGProps } from "react";
import { cn } from "@/lib/utils";

type SVGIconComponent = FC<SVGProps<SVGSVGElement>>;

/** Map from server-supplied harness `name` slug → SVG icon component. */
const HARNESS_SVG_MAP: Record<string, SVGIconComponent> = {
  // We'll create simple SVG icons for each harness
  // These are placeholder SVGs - you should replace with actual brand SVGs
};

/** Create a simple placeholder SVG icon */
const createPlaceholderIcon = (letter: string): SVGIconComponent => {
  return (props) => (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable={false}
      className={cn("shrink-0", props.className)}
      {...props}
    >
      <circle cx="12" cy="12" r="10" />
      <text
        x="12"
        y="16"
        textAnchor="middle"
        fontSize="10"
        fill="currentColor"
        fontWeight="bold"
      >
        {letter}
      </text>
    </svg>
  );
};

// Create simple icon components for each harness
const harnessIcons: Record<string, string> = {
  "claude-code": "C",
  kiro: "K",
  cursor: "Cu",
  pi: "Pi",
  codex: "Cd",
  copilot: "G",
  "copilot-cli": "G",
  opencode: "O",
  antigravity: "A",
  goose: "G",
};

Object.entries(harnessIcons).forEach(([harness, letter]) => {
  HARNESS_SVG_MAP[harness] = createPlaceholderIcon(letter);
});

export interface HarnessLogoFixedProps {
  /** Stable harness slug from `/api/v1/config/harnesses` (`HarnessEntry.name`). */
  harness: string;
  className?: string;
}

/**
 * Renders a simple SVG icon for a harness without external dependencies.
 * This avoids CSP issues with @lobehub/icons loading external scripts.
 */
export function HarnessLogoFixed({ harness, className }: HarnessLogoFixedProps) {
  const Icon = HARNESS_SVG_MAP[harness];

  if (Icon) {
    return (
      <Icon
        className={cn("h-3.5 w-3.5", className)}
      />
    );
  }

  // Neutral fallback for unknown harnesses
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable={false}
      className={cn("h-3.5 w-3.5 shrink-0", className)}
    >
      <path d="M12 3l8 4-8 4-8-4z" />
      <path d="M4 11l8 4 8-4" />
      <path d="M4 15l8 4 8-4" />
    </svg>
  );
}

/**
 * Resolve a raw platform / `service_name` string (as carried on a session) to a
 * known harness slug using the server-provided harness list.
 */
export function resolveHarnessSlugFixed(
  platform: string | undefined | null,
  harnesses: { name: string; display_name: string }[] | undefined,
): string | undefined {
  if (!platform) return undefined;
  const lower = platform.toLowerCase();
  return harnesses?.find(
    (h) => h.name.toLowerCase() === lower || h.display_name.toLowerCase() === lower,
  )?.name;
}