// SPDX-FileCopyrightText: 2026 Chandhini Veerabuthiran <chandhini03@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0

/**
 * Shared chart colour helpers.
 *
 * Design tokens in `app.css` are stored as bare OKLCH component triples
 * (e.g. `--primary: 0.222 0.006 91.6`) so they can be composed with alpha via
 * `oklch(var(--x) / 0.5)`. Recharts needs concrete CSS colour strings, so chart
 * code must wrap them in `oklch(...)`.
 *
 * Several charts previously wrapped these in `hsl(...)`, which is invalid for an
 * OKLCH triple and silently fell back to a default colour. Use these helpers
 * instead of hand-writing colour functions in chart components.
 */

/** A themed colour token, resolved against the active preset and mode. */
export function token(name: string): string {
  return `oklch(var(--${name}))`;
}

/** A themed colour token with alpha applied. */
export function tokenAlpha(name: string, alpha: number): string {
  return `oklch(var(--${name}) / ${alpha})`;
}

/** Categorical series palette, cycling the eight chart tokens. */
export const CHART_SERIES = [
  token("chart-1"),
  token("chart-2"),
  token("chart-3"),
  token("chart-4"),
  token("chart-5"),
  token("chart-6"),
  token("chart-7"),
  token("chart-8"),
] as const;

/** Pick a series colour by index, wrapping when there are more series. */
export function seriesColor(index: number): string {
  return CHART_SERIES[index % CHART_SERIES.length];
}

/** Axis tick styling shared by every chart. */
export const AXIS_TICK = {
  fill: token("muted-foreground"),
  fontSize: 11,
} as const;

/** Grid stroke shared by every chart. */
export const GRID_STROKE = token("chart-grid");

/** Tooltip container styling shared by every chart. */
export const TOOLTIP_STYLE = {
  background: token("popover"),
  border: `1px solid ${token("border")}`,
  borderRadius: 10,
  fontSize: 12,
  color: token("popover-foreground"),
} as const;

/** Hover cursor fill for bar charts. */
export const CURSOR_FILL = { fill: token("muted"), opacity: 0.35 } as const;
