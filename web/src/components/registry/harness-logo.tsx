// SPDX-License-Identifier: Apache-2.0
//
// Harness identity marks using brand-accurate SVG logos.
// Logos are served from /logos/ directory as static assets.
//
// The marks are keyed by the stable `name` slug from /api/v1/config/harnesses.
// A neutral fallback renders for any unknown slug so there is never a broken
// image or missing gap.
//
// Every use-site adds aria-hidden / decorative treatment — the visible
// display_name is always rendered next to the icon.

import { cn } from "@/lib/utils";

/** Map from server-supplied harness `name` slug → logo file path. */
const HARNESS_LOGO_MAP: Record<string, string> = {
	"claude-code": "/logos/claude-code.svg",
	kiro: "/logos/kiro.svg",
	cursor: "/logos/cursor.svg",
	pi: "/logos/pi.svg",
	codex: "/logos/codex.svg",
	copilot: "/logos/copilot.svg",
	"copilot-cli": "/logos/copilot.svg",
	opencode: "/logos/opencode.svg",
	antigravity: "/logos/antigravity.svg",
	goose: "/logos/goose.svg",
};

export interface HarnessLogoProps {
	/** Stable harness slug from `/api/v1/config/harnesses` (`HarnessEntry.name`). */
	harness: string;
	className?: string;
}

/**
 * Renders a brand-accurate logo for a harness, keyed by its server-supplied slug.
 * Unknown slugs render a neutral fallback so the adjacent display name is always
 * paired with something legible. The icon is intentionally decorative — callers
 * must render the visible display name beside it.
 */
export function HarnessLogo({ harness, className }: HarnessLogoProps) {
	const logoPath = HARNESS_LOGO_MAP[harness];

	if (logoPath) {
		return (
			<img
				src={logoPath}
				alt=""
				aria-hidden="true"
				className={cn("shrink-0", className)}
				style={{ display: "inline-block" }}
			/>
		);
	}

	// Neutral fallback for unknown / future harnesses — a simple layers mark.
	return (
		<svg
			viewBox="0 0 24 24"
			fill="none"
			stroke="#94A3B8"
			strokeWidth={2}
			strokeLinecap="round"
			strokeLinejoin="round"
			aria-hidden="true"
			focusable={false}
			className={cn("shrink-0", className)}
		>
			<path d="M12 3l8 4-8 4-8-4z" />
			<path d="M4 11l8 4 8-4" />
			<path d="M4 15l8 4 8-4" />
		</svg>
	);
}

/**
 * Resolve a raw platform / `service_name` string (as carried on a session) to a
 * known harness slug using the server-provided harness list. Matching is
 * case-insensitive against both `name` and `display_name` so values like
 * `"kiro"`, `"Kiro"`, `"Copilot CLI"` and `"copilot-cli"` all resolve.
 * Returns `undefined` when the value is not a recognised harness.
 */
export function resolveHarnessSlug(
	platform: string | undefined | null,
	harnesses: { name: string; display_name: string }[] | undefined,
): string | undefined {
	if (!platform) return undefined;
	const lower = platform.toLowerCase();
	return harnesses?.find(
		(h) => h.name.toLowerCase() === lower || h.display_name.toLowerCase() === lower,
	)?.name;
}
