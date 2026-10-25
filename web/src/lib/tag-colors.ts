// SPDX-FileCopyrightText: 2026 Chandhini Veerabuthiran <chandhini03@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0

/**
 * Per-component-type identity colours.
 *
 * Each registry component type gets a consistent colour pairing (readable
 * foreground on a tinted surface), backed by the `--tag-*` tokens in
 * `app.css` so the pairing adapts to the active preset and light/dark mode.
 *
 * Accepts both the plural registry type (`mcps`) and the singular form
 * (`mcp`) because different call sites carry different shapes.
 */

const TAG_CLASSES: Record<string, string> = {
  mcp: "bg-tag-mcp-bg text-tag-mcp",
  skill: "bg-tag-skill-bg text-tag-skill",
  hook: "bg-tag-hook-bg text-tag-hook",
  prompt: "bg-tag-prompt-bg text-tag-prompt",
  agent: "bg-tag-agent-bg text-tag-agent",
  // Sandboxes have no dedicated colour in the design; reuse the hook family so
  // they still read as a distinct type rather than falling back to grey.
  sandbox: "bg-tag-hook-bg text-tag-hook",
};

/** Normalise a plural registry type to its singular tag key. */
function tagKey(type: string): string {
  const t = type.toLowerCase();
  return t.endsWith("es") ? t.slice(0, -2) : t.endsWith("s") ? t.slice(0, -1) : t;
}

/**
 * Tailwind classes for a component type's tag chip.
 * Falls back to muted styling for unknown types.
 */
export function tagColorClasses(type: string | undefined | null): string {
  if (!type) return "bg-muted text-muted-foreground";
  return TAG_CLASSES[tagKey(type)] ?? "bg-muted text-muted-foreground";
}
