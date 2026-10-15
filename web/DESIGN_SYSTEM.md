<!-- SPDX-FileCopyrightText: 2026 Chandhini Veerabuthiran <chandhini03@users.noreply.github.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Observal design system

Shared foundations for the UI revamp. Read this before adding or restyling UI:
the point of these tokens and primitives is that Registry, Workspace, and
Administration surfaces do not each grow their own visual system.

Tokens live in `src/app.css`. Primitives live in `src/components/ui/` (generic)
and `src/components/registry/` (domain-specific).

## Appearance model: preset × mode

Two **independent** dimensions, not one flat list of themes.

| Dimension | Values | Default |
|---|---|---|
| Preset | `monochrome`, `slate`, `copper`, `verdant`, `indigo`, `rose` | `monochrome` |
| Mode | `light`, `dark`, `system` | `system` |

`ThemeProvider` (`src/lib/theme.tsx`) applies both as classes on `<html>`, e.g.
`class="preset-copper dark"`. Every preset defines a complete palette for *both*
modes — 12 palettes total.

`mode` is persisted as the user's **intent**: choosing `system` stores `system`
and keeps tracking the OS preference live via `matchMedia`. It is never
flattened to a literal on first paint.

```tsx
const { preset, setPreset, mode, setMode, resolvedMode } = useTheme();
```

`useTheme()` also returns `{ theme, setTheme }` for backwards compatibility.
`theme` is the preset name (or active legacy theme), so anything needing an
actual light/dark value must read `resolvedMode`.

### Legacy themes

Thirteen pre-existing themes (`midnight`, `forest`, `dracula`, `nord`, …) are
preserved and **excluded** from the preset × mode matrix. They carry a complete
one-dimensional palette, apply their class alone, and the mode toggle does not
affect them — forcing them into the matrix would distort appearances users
already rely on.

## Colour tokens

Tokens are stored as **bare OKLCH component triples** so they can be composed
with alpha:

```css
--primary: 0.222 0.006 91.6;   /* not a colour function */
```

Consume them through Tailwind utilities (`bg-primary`, `text-muted-foreground`).
In raw CSS or JS, wrap them:

```ts
`oklch(var(--primary))`            // opaque
`oklch(var(--primary) / 0.5)`      // 50% alpha
```

> **Do not write `hsl(var(--token))`.** The triple is OKLCH, so `hsl()` produces
> an invalid colour that silently falls back. This was a real bug across 47 chart
> call sites; `src/lib/chart-theme.ts` exists to stop it recurring.

### Semantic roles

| Token | Use |
|---|---|
| `background` / `foreground` | Page canvas and primary text |
| `card` / `card-foreground` | Raised surfaces |
| `muted` / `muted-foreground` | Secondary surfaces and de-emphasised text |
| `border`, `input` | Hairlines and field outlines |
| `primary` | Brand accent, primary actions |
| `surface-raised` / `surface-sunken` | Nested elevation |
| `success`, `warning`, `destructive`, `info` | Semantic states |
| `chart-1`…`chart-8` | Categorical series |

Semantic colours are deliberately muted rather than saturated dashboard greens
and yellows.

### Component-type tags

Each registry component type has an identity colour pairing a readable
foreground with a subtly contrasting surface, so type boxes read as separate
from the card behind them:

```ts
import { tagColorClasses } from "@/lib/tag-colors";
<span className={tagColorClasses("mcps")}>MCP</span>  // plural or singular
```

Backed by `--tag-{mcp,agent,skill,hook,prompt}` and `-bg` variants, defined per
mode.

## Typography

Inter for UI, JetBrains Mono for code and numeric columns. Loaded locally from
`public/fonts/` — no CDN request. Headings differentiate by **weight and
tracking**, not by typeface.

| Token | Size | Use |
|---|---|---|
| `text-2xs` | 11px | Uppercase labels, table headers, nav group headings |
| `text-xs` | 12px | Secondary text, table cells, badges |
| `text-sm` | 13.5px | Base body, nav items |
| `text-base` | 14px | Panel titles |
| `text-lg` | 16px | Section headings |
| `text-2xl` | 26px | Page `h1` |

Headings are weight 500 with negative tracking. Metric values are weight 700
with `tracking-[-0.02em]` and `tabular-nums`.

Any column of figures should carry `tabular-nums` so values do not jitter as
they change.

## Radius, elevation, spacing

| Token | Value | Use |
|---|---|---|
| `rounded-sm` | 6px | Chips, inline controls |
| `rounded-md` | 8px | Inputs, buttons |
| `rounded-lg` | 10px | Nested containers |
| `rounded-xl` | 13px | Cards, panels, table wrappers |

Cards and panels use `rounded-xl` + `shadow-sm`. Overlays use the heavier
elevation. Page content uses `p-6`; table cells `px-3 py-2.5`.

## Primitives

Generic — `src/components/ui/`:

| Primitive | Notes |
|---|---|
| `card` | `rounded-xl`, `shadow-sm` |
| `table` | Tinted header, uppercase tracked labels. Wrap in a `rounded-xl` bordered card. |
| `tabs` | Radix-backed segmented control |
| `badge` | Pill; four variants |
| `sidebar` | Stateful: cookie-persisted collapse, `Cmd+B`, mobile sheet. **Restyle classes only — do not reimplement the logic.** |

Domain — `src/components/registry/`:

| Primitive | Notes |
|---|---|
| `status-badge` | 22 states. Dot + text, so state never rests on colour alone. |
| `entity-glyph` | Distinct symbol per entity type on a contrasting surface |
| `harness-badges`, `registry-name` | Existing shared display helpers |

Layout — `src/components/layouts/`: `page-header` (two-row header with
breadcrumbs, actions, and router-backed tabs).

### Entity glyphs

Use a symbol, never an initial letter or an identical coloured pill:

```tsx
import { EntityGlyph } from "@/components/registry/entity-glyph";

<EntityGlyph type="mcps" />                      // labelled for screen readers
<EntityGlyph type="agent" labelled={false} />    // adjacent text already names it
<EntityGlyph type="hooks" size="sm" />           // sm | md | lg
```

Covers agent, mcp, skill, hook, prompt, sandbox, teamspace; unknown types fall
back to a neutral glyph. Accepts plural or singular type names.

### Charts

Always import from `src/lib/chart-theme.ts` rather than hand-writing colours:

```tsx
import { AXIS_TICK, GRID_STROKE, TOOLTIP_STYLE, seriesColor, token } from "@/lib/chart-theme";

<CartesianGrid stroke={GRID_STROKE} />
<XAxis tick={AXIS_TICK} />
<Tooltip contentStyle={TOOLTIP_STYLE} />
<Bar fill={seriesColor(i)} />
```

## Accessibility

**Never rely on colour alone.** `StatusBadge` pairs a dot with a text label;
`EntityGlyph` pairs a symbol with a tint and an `aria-label`. Destructive
actions need an icon or explicit wording, not just red.

**Reduced motion** is handled globally in `app.css`: under
`prefers-reduced-motion: reduce`, animations and transitions collapse to 1ms
(rather than being removed, which can strand elements mid-animation), hover
lifts are neutralised, indefinite loops stop, and spinners slow. No
per-component guard is needed.

**Focus** must stay visible — keep `focus-visible:ring-2 focus-visible:ring-ring`
on interactive elements.

**Contrast** should be checked in both modes; `muted-foreground` on `background`
is the tightest pairing and the one most worth verifying.

## Adding new UI

1. Check whether a primitive already exists. Prefer restyling it over a local copy.
2. Use tokens. No raw hex, and no Tailwind palette classes like `text-green-600` —
   use `text-success`.
3. Wrap OKLCH triples in `oklch(...)`, never `hsl(...)`.
4. Pair colour with a symbol or text for anything semantic.
5. Verify light and dark, plus at least one non-monochrome preset.
6. Keep component APIs stable — visual changes should not alter props or behaviour.
