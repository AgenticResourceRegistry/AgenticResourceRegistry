// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import {
	Bar,
	BarChart,
	CartesianGrid,
	Cell,
	ComposedChart,
	LabelList,
	Line,
	Pie,
	PieChart,
	ResponsiveContainer,
	Tooltip,
	XAxis,
	YAxis,
} from "recharts";
import type { CostDriver, CostTrendPoint } from "@/lib/types";
import { CHART, monthLabel, pct, tooltipStyle, usd, usdTick } from "./format";

/** Spend bars with savings found and actioned lines, month by month. */
export function SpendSavingsTrend({ data }: { data: CostTrendPoint[] }) {
	// Months outside telemetry retention render as "no data", never as a $0 bar.
	const rows = data.map((d) => ({
		...d,
		label: `${monthLabel(d.month, d.partial)}${d.data === "partial" ? "*" : ""}`,
		savings_actioned_usd: d.data === "none" ? null : d.savings_actioned_usd,
	}));
	const summary = rows
		.map((r) =>
			r.data === "none"
				? `${r.label}: no telemetry retained`
				: `${r.label}: spend ${usd(r.spend_usd)}, found ${usd(r.savings_found_usd)}, actioned ${usd(r.savings_actioned_usd)}`,
		)
		.join("; ");
	return (
		<figure aria-label="Monthly spend versus savings found and actioned" className="m-0">
			<ResponsiveContainer width="100%" height={300}>
				<ComposedChart data={rows} margin={{ top: 24, right: 12, bottom: 0, left: 4 }}>
					<CartesianGrid stroke={CHART.grid} strokeOpacity={0.6} vertical={false} />
					<XAxis dataKey="label" tick={{ fill: CHART.axis, fontSize: 12 }} axisLine={false} tickLine={false} />
					<YAxis tickFormatter={usdTick} tick={{ fill: CHART.axis, fontSize: 12 }} axisLine={false} tickLine={false} width={56} />
					<Tooltip
						contentStyle={tooltipStyle}
						cursor={{ fill: "oklch(var(--muted) / 0.4)" }}
						formatter={(value, name) => [
							value === null || value === undefined ? "No data retained" : usd(Number(value)),
							name === "spend_usd" ? "Total spend" : name === "savings_found_usd" ? "Savings found" : "Savings actioned",
						]}
					/>
					<Bar dataKey="spend_usd" fill={CHART.spend} radius={[6, 6, 0, 0]} maxBarSize={56} isAnimationActive={false}>
						<LabelList
							dataKey="spend_usd"
							position="top"
							formatter={(v) => (v === null || v === undefined ? "" : usd(Number(v)))}
							style={{ fill: "oklch(var(--fg))", fontSize: 12, fontWeight: 600 }}
						/>
					</Bar>
					<Line type="monotone" dataKey="savings_found_usd" stroke={CHART.found} strokeWidth={2.5} dot={{ r: 3.5, fill: CHART.found }} isAnimationActive={false} />
					<Line type="monotone" dataKey="savings_actioned_usd" stroke={CHART.actioned} strokeWidth={2.5} dot={{ r: 3.5, fill: CHART.actioned }} isAnimationActive={false} />
				</ComposedChart>
			</ResponsiveContainer>
			<figcaption className="sr-only">{summary}</figcaption>
		</figure>
	);
}

export function ChartLegend({ items }: { items: { label: string; color: string; shape?: "bar" | "dot" }[] }) {
	return (
		<ul className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
			{items.map((item) => (
				<li key={item.label} className="flex items-center gap-1.5">
					<span
						aria-hidden="true"
						className={item.shape === "bar" ? "h-2.5 w-2.5 rounded-[3px]" : "h-2 w-2 rounded-full"}
						style={{ background: item.color }}
					/>
					{item.label}
				</li>
			))}
		</ul>
	);
}

export interface DonutSlice {
	label: string;
	value: number;
	color?: string;
}

/** Part-to-whole with few slices; values listed beside it so nothing depends on reading angles. */
export function Donut({
	slices,
	center,
	centerLabel,
	format = usd,
	ariaLabel,
	stacked = false,
}: {
	slices: DonutSlice[];
	center?: string;
	centerLabel?: string;
	format?: (v: number) => string;
	ariaLabel: string;
	/** Legend below the ring, for narrow panels where a side legend would truncate. */
	stacked?: boolean;
}) {
	const total = slices.reduce((sum, s) => sum + s.value, 0);
	const colored = slices.map((s, i) => ({ ...s, color: s.color ?? CHART.series[i % CHART.series.length] }));
	if (total <= 0) {
		return <p className="py-6 text-sm text-muted-foreground">No measured spend in this period.</p>;
	}
	return (
		<figure aria-label={ariaLabel} className={`m-0 flex flex-col items-center gap-4 ${stacked ? "" : "sm:flex-row sm:items-center"}`}>
			<div className="relative h-36 w-36 shrink-0">
				<ResponsiveContainer width="100%" height="100%">
					<PieChart>
						<Pie
							data={colored}
							dataKey="value"
							nameKey="label"
							innerRadius="62%"
							outerRadius="100%"
							paddingAngle={colored.length > 1 ? 1.5 : 0}
							stroke="none"
							isAnimationActive={false}
						>
							{colored.map((s) => (
								<Cell key={s.label} fill={s.color} />
							))}
						</Pie>
						<Tooltip contentStyle={tooltipStyle} formatter={(v, n) => [format(Number(v)), String(n)]} />
					</PieChart>
				</ResponsiveContainer>
				{center && (
					<div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center text-center">
						<span className="text-base font-semibold tabular-nums">{center}</span>
						{centerLabel && <span className="text-2xs text-muted-foreground">{centerLabel}</span>}
					</div>
				)}
			</div>
			<ul className="w-full min-w-0 space-y-1.5 text-xs">
				{colored.map((s) => (
					<li key={s.label} className="flex items-center gap-2">
						<span aria-hidden="true" className="h-2 w-2 shrink-0 rounded-full" style={{ background: s.color }} />
						<span className="min-w-0 flex-1 truncate" title={s.label}>{s.label}</span>
						<span className="tabular-nums text-muted-foreground">{pct((100 * s.value) / total)}</span>
						<span className="w-16 text-right font-medium tabular-nums">{format(s.value)}</span>
					</li>
				))}
			</ul>
		</figure>
	);
}

/** Ranked magnitudes, largest first, labels and values readable without hovering. */
export function RankedBars({ items, color = "oklch(var(--primary))", ariaLabel }: { items: CostDriver[]; color?: string; ariaLabel: string }) {
	if (items.length === 0) {
		return <p className="py-6 text-sm text-muted-foreground">No measured spend in this period.</p>;
	}
	const height = Math.max(items.length * 34 + 8, 80);
	return (
		<figure aria-label={ariaLabel} className="m-0">
			<ResponsiveContainer width="100%" height={height}>
				<BarChart data={items} layout="vertical" margin={{ top: 0, right: 64, bottom: 0, left: 0 }} barCategoryGap={8}>
					<XAxis type="number" hide domain={[0, "dataMax"]} />
					<YAxis
						type="category"
						dataKey="label"
						width={150}
						tick={{ fill: "oklch(var(--fg))", fontSize: 12 }}
						axisLine={false}
						tickLine={false}
						tickFormatter={(v: string) => (v.length > 22 ? `${v.slice(0, 21)}…` : v)}
					/>
					<Tooltip
						contentStyle={tooltipStyle}
						cursor={{ fill: "oklch(var(--muted) / 0.4)" }}
						formatter={(v, _n, item) => [`${usd(Number(v))} · ${pct((item?.payload as CostDriver)?.share_pct, 1)}`, "Spend"]}
					/>
					<Bar dataKey="spend_usd" radius={[0, 6, 6, 0]} isAnimationActive={false}>
						{items.map((item) => (
							<Cell key={item.label} fill={item.label === "No agent" || item.label === "Other" ? CHART.spend : color} />
						))}
						<LabelList dataKey="spend_usd" position="right" formatter={(v) => usd(Number(v))} style={{ fill: "oklch(var(--fg))", fontSize: 12, fontWeight: 600 }} />
					</Bar>
				</BarChart>
			</ResponsiveContainer>
			<figcaption className="sr-only">{items.map((i) => `${i.label}: ${usd(i.spend_usd)}`).join("; ")}</figcaption>
		</figure>
	);
}

/** Two values for the same measure side by side (this month vs last, with vs without caching). */
export function PairBars({
	rows,
	format = usd,
}: {
	rows: { label: string; value: number | null; tone: "current" | "reference" }[];
	format?: (v: number) => string;
}) {
	const max = Math.max(...rows.map((r) => r.value ?? 0), 0);
	return (
		<div className="space-y-2">
			{rows.map((row) => (
				<div key={row.label} className="grid grid-cols-[minmax(0,7rem)_1fr_auto] items-center gap-3 text-xs">
					<span className="truncate text-muted-foreground">{row.label}</span>
					<div className="h-2 rounded-full bg-muted">
						<div
							className="h-2 rounded-full"
							style={{
								width: row.value && max ? `${Math.max((100 * row.value) / max, 2)}%` : "0%",
								// Neutral on purpose: whether higher is good depends on the measure.
								background: row.tone === "current" ? "oklch(var(--primary))" : CHART.spend,
							}}
						/>
					</div>
					<span className={`tabular-nums ${row.tone === "current" ? "font-semibold" : "text-muted-foreground"}`}>
						{row.value === null ? "—" : format(row.value)}
					</span>
				</div>
			))}
		</div>
	);
}
