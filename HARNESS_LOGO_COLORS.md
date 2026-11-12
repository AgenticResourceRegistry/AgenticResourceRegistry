<!-- SPDX-FileCopyrightText: 2026 Agentic Resource Registry Contributors -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Harness Logo Color Scheme

This document describes the color scheme applied to harness logos throughout the web UI.

## Color Palette

Each harness now has a distinctive, brand-appropriate color that works well in both light and dark themes:

| Harness | Primary Color | Accent/Stroke | Rationale |
|---------|---------------|---------------|-----------|
| **Kiro** | `#FFD700` (Gold) | `#FFA500` (Orange) | Lightning bolt - energetic, electric gold/orange |
| **Copilot** | `#7B68EE` (Medium Slate Blue) | — | Purple/blue representing GitHub's AI assistant |
| **Claude Code** | `#D97706` (Amber) | — | Warm amber representing Claude's conversational AI |
| **Cursor** | `#3B82F6` (Blue) | `#2563EB` (Darker Blue) | Classic cursor blue - tech-forward and professional |
| **Antigravity** | `#EF4444` (Red) | `#DC2626` (Darker Red) | Rocket red - energy, motion, lift-off |
| **Pi** | `#10B981` (Emerald Green) | — | Mathematical precision with fresh green |
| **Goose** | `#F59E0B` (Amber/Orange) | — | Warm golden-orange representing a goose |
| **Codex** | `#06B6D4` (Cyan) | — | Tech cyan for OpenAI's code model |
| **OpenCode** | `#8B5CF6` (Purple) | — | Vibrant purple for open-source coding |
| **Fallback** | `#94A3B8` (Slate Gray) | — | Neutral gray for unknown/future harnesses |

## Design Principles

1. **Theme Compatibility**: All colors work in both light and dark themes without requiring color-mode adjustments
2. **Accessibility**: Colors meet WCAG contrast requirements when displayed at the standard icon sizes (3.5x3.5 to 3x3)
3. **Distinctiveness**: Each color is visually distinct from others, making harnesses easily recognizable at a glance
4. **Brand Alignment**: Colors were chosen to reflect each tool's brand identity or conceptual association
5. **Consistency**: The same colors are used across all UI surfaces (dropdowns, badges, cards, filters, etc.)

## Implementation

- **SVG Files**: Static colors defined directly in `/web/public/logos/*.svg`
- **Component**: `web/src/components/registry/harness-logo.tsx` renders the logos
- **No Runtime Logic**: Colors are baked into the SVG assets, not computed at runtime
- **Fallback Behavior**: Unknown harness IDs show a neutral gray layer icon with visible text label

## Previous Implementation

Previously, all logos used `currentColor` which inherited the text color from their parent container, resulting in black (light mode) or white (dark mode) logos without brand differentiation.

## Related Files

- `web/public/logos/*.svg` - Individual logo SVG files
- `web/src/components/registry/harness-logo.tsx` - Logo component
- GitHub Issue: [#2196](https://github.com/AgenticResourceRegistry/AgenticResourceRegistry/issues/2196)
