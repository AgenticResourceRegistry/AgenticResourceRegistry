// SPDX-FileCopyrightText: 2026 Chandhini Veerabuthiran <chandhini03@users.noreply.github.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

/**
 * Semantic entity glyphs.
 *
 * Registry entities are identified by a distinct symbol rather than an initial
 * letter or an identical coloured pill, so the type is recognisable at a glance
 * and does not depend on colour alone (the icon carries the meaning, the tint
 * reinforces it).
 *
 * The glyph sits on a subtly contrasting surface (`--tag-*-bg`) so the container
 * reads as separate from the card or page background behind it.
 *
 * Icons intentionally reuse the vocabulary already established in the sidebar
 * and registry navigation so the same concept looks the same everywhere.
 */

import {
  Bot,
  Server,
  Sparkles,
  Webhook,
  MessageSquareText,
  Box,
  Users,
  Puzzle,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { tagColorClasses } from "@/lib/tag-colors";

/** Entity kinds that have a dedicated glyph. */
export type EntityKind =
  | "agent"
  | "mcp"
  | "skill"
  | "hook"
  | "prompt"
  | "sandbox"
  | "teamspace";

const GLYPHS: Record<EntityKind, { icon: LucideIcon; label: string }> = {
  agent: { icon: Bot, label: "Agent File" },
  mcp: { icon: Server, label: "MCP Client" },
  skill: { icon: Sparkles, label: "Skill" },
  hook: { icon: Webhook, label: "Hook" },
  prompt: { icon: MessageSquareText, label: "Prompt" },
  sandbox: { icon: Box, label: "Sandbox" },
  teamspace: { icon: Users, label: "Teamspace" },
};

const FALLBACK = { icon: Puzzle, label: "Component" };

/**
 * Normalise a registry type to an entity kind. Accepts plural registry types
 * (`mcps`), singular forms (`mcp`), and `teamspace`/`team`.
 */
export function toEntityKind(type: string | undefined | null): EntityKind | null {
  if (!type) return null;
  const t = type.toLowerCase();
  const singular = t.endsWith("es") ? t.slice(0, -2) : t.endsWith("s") ? t.slice(0, -1) : t;
  if (singular === "team") return "teamspace";
  return singular in GLYPHS ? (singular as EntityKind) : null;
}

const SIZES = {
  sm: { box: "h-6 w-6 rounded-md", icon: "h-3 w-3" },
  md: { box: "h-7 w-7 rounded-lg", icon: "h-3.5 w-3.5" },
  lg: { box: "h-9 w-9 rounded-lg", icon: "h-4 w-4" },
} as const;

interface EntityGlyphProps {
  /** Registry type or entity kind. Plural and singular forms both work. */
  type: string | undefined | null;
  size?: keyof typeof SIZES;
  /**
   * When false the glyph is treated as decoration and hidden from assistive
   * tech. Use this where an adjacent text label already names the type.
   */
  labelled?: boolean;
  className?: string;
}

/**
 * A type-coloured glyph on a contrasting surface.
 *
 * Renders an accessible label by default so screen readers announce the entity
 * type; pass `labelled={false}` when a visible label already does that.
 */
export function EntityGlyph({
  type,
  size = "md",
  labelled = true,
  className,
}: EntityGlyphProps) {
  const kind = toEntityKind(type);
  const { icon: Icon, label } = kind ? GLYPHS[kind] : FALLBACK;
  const dims = SIZES[size];

  return (
    <span
      className={cn(
        "inline-grid shrink-0 place-items-center",
        dims.box,
        tagColorClasses(kind ?? undefined),
        className,
      )}
      {...(labelled ? { role: "img", "aria-label": label } : { "aria-hidden": true })}
    >
      <Icon className={dims.icon} strokeWidth={1.8} />
    </span>
  );
}
