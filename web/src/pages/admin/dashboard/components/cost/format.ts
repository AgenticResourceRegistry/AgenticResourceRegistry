// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-License-Identifier: Apache-2.0

/** Dollar amounts that stay readable from $0.42 to $3.2M. */
export function usd(value: number | null | undefined): string {
	if (value === null || value === undefined || Number.isNaN(value)) return "—";
	const sign = value < 0 ? "−" : "";
	const abs = Math.abs(value);
	if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(abs >= 10_000_000 ? 0 : 1)}M`;
	if (abs >= 10_000) return `${sign}$${(abs / 1_000).toFixed(abs >= 100_000 ? 0 : 1)}K`;
	if (abs >= 100) return `${sign}$${abs.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
	return `${sign}$${abs.toFixed(2)}`;
}

/** Axis ticks: short, no cents. */
export function usdTick(value: number): string {
	const abs = Math.abs(value);
	if (abs >= 1_000_000) return `$${(value / 1_000_000).toFixed(1)}M`;
	if (abs >= 1_000) return `$${(value / 1_000).toFixed(0)}K`;
	return `$${value.toFixed(0)}`;
}

export function pct(value: number | null | undefined, digits = 0): string {
	if (value === null || value === undefined || Number.isNaN(value)) return "—";
	return `${value.toFixed(digits)}%`;
}

export function tokens(value: number | null | undefined): string {
	if (value === null || value === undefined) return "—";
	if (value >= 1e9) return `${(value / 1e9).toFixed(1)}B`;
	if (value >= 1e6) return `${(value / 1e6).toFixed(1)}M`;
	if (value >= 1e3) return `${(value / 1e3).toFixed(0)}K`;
	return value.toFixed(0);
}

export function monthLabel(month: string, partial = false): string {
	const [year, m] = month.split("-").map(Number);
	const name = new Date(Date.UTC(year, m - 1, 1)).toLocaleString("en-US", { month: "short", timeZone: "UTC" });
	return partial ? `${name} (MTD)` : name;
}

/** Readable label for a model id such as claude-opus-4-6 → Claude Opus 4.6. */
export function modelLabel(model: string): string {
	const match = model.match(/^claude-(opus|sonnet|haiku|fable)-(\d+)(?:-(\d+))?/i);
	if (match) {
		const [, family, major, minor] = match;
		return `${family[0].toUpperCase()}${family.slice(1)} ${major}${minor ? `.${minor}` : ""}`;
	}
	return model;
}

export const CHART = {
	spend: "oklch(var(--muted-fg) / 0.35)",
	found: "oklch(var(--warning))",
	actioned: "oklch(var(--success))",
	grid: "oklch(var(--border))",
	axis: "oklch(var(--muted-fg))",
	series: [
		"oklch(var(--primary))",
		"oklch(var(--success))",
		"oklch(var(--warning))",
		"oklch(var(--destructive))",
		"oklch(var(--chart-2))",
		"oklch(var(--chart-3))",
		"oklch(var(--info))",
		"oklch(var(--chart-8))",
		"oklch(var(--muted-fg) / 0.5)",
	],
} as const;

export const tooltipStyle = {
	background: "oklch(var(--popover))",
	border: "1px solid oklch(var(--border))",
	borderRadius: 8,
	fontSize: 12,
	color: "oklch(var(--popover-fg))",
} as const;
