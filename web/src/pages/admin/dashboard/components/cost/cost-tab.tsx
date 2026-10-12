// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { AlertTriangle, ArrowDownRight, ArrowUpRight, Info, Lightbulb, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useCostIntelligence, useRecomputeCostIntelligence } from "@/hooks/use-api";
import type { CostIntelligence, CostUnitEconomics } from "@/lib/types";
import { ChartLegend, Donut, PairBars, RankedBars, SpendSavingsTrend } from "./cost-charts";
import { OpportunityList, PipelineStages } from "./cost-pipeline";
import { CHART, modelLabel, pct, tokens, usd } from "./format";

function Panel({ title, description, action, children, className = "" }: {
	title: string;
	description?: string;
	action?: React.ReactNode;
	children: React.ReactNode;
	className?: string;
}) {
	return (
		<section className={`min-w-0 rounded-xl bg-card p-5 shadow-sm ${className}`} aria-label={title}>
			<div className="mb-4 flex items-start justify-between gap-3">
				<div className="min-w-0">
					<h3 className="text-sm font-semibold">{title}</h3>
					{description && <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>}
				</div>
				{action}
			</div>
			{children}
		</section>
	);
}

function Kpi({ label, value, detail, tone = "muted" }: {
	label: string;
	value: string;
	detail?: React.ReactNode;
	tone?: "muted" | "success" | "warning";
}) {
	const toneClass = tone === "success" ? "text-success" : tone === "warning" ? "text-warning" : "text-muted-foreground";
	return (
		<div className="min-w-0 bg-card px-5 py-4">
			<p className="text-xs text-muted-foreground">{label}</p>
			<p className="mt-2 text-2xl font-semibold tracking-[-0.025em] tabular-nums">{value}</p>
			{detail && <p className={`mt-1 text-xs ${toneClass}`}>{detail}</p>}
		</div>
	);
}

function SectionHeading({ children }: { children: React.ReactNode }) {
	return <h2 className="pt-2 text-base font-semibold tracking-[-0.01em]">{children}</h2>;
}

function SpendChange({ change }: { change: number | null }) {
	if (change === null) return <>No spend at this point last month</>;
	const Icon = change >= 0 ? ArrowUpRight : ArrowDownRight;
	return (
		<span className="inline-flex items-center gap-1">
			<Icon className="h-3 w-3" aria-hidden="true" />
			{Math.abs(change).toFixed(0)}% vs. same point last month
		</span>
	);
}

function Headline({ data }: { data: CostIntelligence }) {
	const h = data.headline;
	return (
		<div className="grid grid-cols-1 gap-px overflow-hidden rounded-xl bg-border shadow-sm sm:grid-cols-2 xl:grid-cols-4">
			<Kpi label="Total agent spend, month to date" value={usd(h.spend_mtd_usd)} detail={<SpendChange change={h.spend_change_pct} />} />
			<Kpi
				label="Savings identified"
				value={`${usd(h.savings_identified_monthly_usd)}/mo`}
				detail={h.savings_identified_pct_of_spend === null ? "Run rate from the last 30 days" : `${pct(h.savings_identified_pct_of_spend)} of this month's spend`}
				tone="warning"
			/>
			<Kpi
				label="Savings actioned"
				value={`${usd(h.savings_actioned_monthly_usd)}/mo`}
				detail={`${usd(h.savings_realized_mtd_usd)} measured so far this month`}
				tone="success"
			/>
			<Kpi
				label="Realization rate"
				value={pct(h.realization_rate_pct)}
				detail={h.realization_rate_pct === null ? "Nothing identified yet" : "of identified savings implemented"}
			/>
		</div>
	);
}

function trendSentence(data: CostIntelligence): string {
	const t = data.trend;
	const current = t[t.length - 1];
	if (!current) return "";
	const prior = t.slice(0, -1).filter((p) => (p.spend_usd ?? 0) > 0);
	const spent = current.spend_usd ?? 0;
	const found = current.savings_found_usd ?? 0;
	const gap = found - current.savings_actioned_usd;
	const peak = prior.reduce((best, p) => ((p.spend_usd ?? 0) > (best?.spend_usd ?? 0) ? p : best), prior[0]);
	const parts = [`This month: ${usd(spent)} spent, ${usd(found)} of savings found, ${usd(current.savings_actioned_usd)} actioned.`];
	if (gap > 0) parts.push(`The ${usd(gap)} gap between found and banked is where the next budget conversation should focus.`);
	if (peak && (peak.spend_usd ?? 0) > spent * 2) {
		parts.push(`Spend peaked in ${new Date(`${peak.month}-01T00:00:00Z`).toLocaleString("en-US", { month: "long", timeZone: "UTC" })} at ${usd(peak.spend_usd)}.`);
	}
	return parts.join(" ");
}

function retentionNote(data: CostIntelligence): string | null {
	const missing = data.trend.filter((t) => t.data !== "full").map((t) => t.data);
	if (missing.length === 0 || !data.period.earliest_telemetry) return null;
	const since = new Date(data.period.earliest_telemetry).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
	const retention = data.period.retention_days ? ` (retention is ${data.period.retention_days} days)` : "";
	return `Telemetry is stored from ${since}${retention}. Months before that show no data rather than zero; * marks a partial month.`;
}

function TokenTile({ data }: { data: CostIntelligence }) {
	const t = data.tokens;
	const mix = t.token_mix;
	return (
		<Panel title="Token cost savings" description="From the coding tools' prompt caching, priced against uncached list rates.">
			<p className="text-2xl font-semibold tabular-nums">{usd(t.cache_savings_mtd_usd)}</p>
			<p className="mb-4 text-xs text-muted-foreground">avoided this month · cache hit rate {pct(t.cache_hit_pct)}</p>
			<Donut
				stacked
				ariaLabel="Tokens this month by type"
				format={tokens}
				center={tokens(t.tokens_mtd)}
				centerLabel="tokens"
				slices={[
					{ label: "Cached reads", value: mix.cache_read, color: CHART.actioned },
					{ label: "Cache writes", value: mix.cache_write, color: CHART.found },
					{ label: "Fresh input", value: mix.input, color: "oklch(var(--destructive))" },
					{ label: "Output", value: mix.output, color: "oklch(var(--primary))" },
				].filter((s) => s.value > 0)}
			/>
			<div className="mt-4 border-t border-border pt-4">
				<p className="mb-2 text-xs text-muted-foreground">Cost per 1M tokens</p>
				<PairBars
					format={(v) => `$${v.toFixed(2)}`}
					rows={[
						{ label: "This month", value: t.cost_per_million_usd, tone: "current" },
						{ label: "Without caching", value: t.cost_per_million_without_cache_usd, tone: "reference" },
						{ label: "Last month", value: t.cost_per_million_prev_month_usd, tone: "reference" },
					]}
				/>
			</div>
		</Panel>
	);
}

function InsightTile({ data }: { data: CostIntelligence }) {
	const insight = data.insight;
	return (
		<section aria-label="Agent cost insight" className="flex min-w-0 flex-col rounded-xl bg-surface-raised p-5">
			<div className="mb-4 flex items-center gap-2">
				<Lightbulb className="h-4 w-4 text-warning" aria-hidden="true" />
				<h3 className="text-sm font-semibold">Most material finding</h3>
			</div>
			{insight ? (
				<>
					<p className="text-lg font-medium leading-snug tracking-[-0.01em] text-balance">{insight.text}</p>
					<div className="mt-auto flex items-center justify-between gap-3 pt-6 text-xs text-muted-foreground">
						<span className="truncate">{insight.subject}</span>
						<span className="shrink-0 capitalize">{insight.evidence}</span>
					</div>
				</>
			) : (
				<p className="text-sm text-muted-foreground">Nothing material yet: no spikes, no opportunities above threshold, and no realized savings this month.</p>
			)}
			<p className="mt-3 text-2xs text-muted-foreground">Chosen and worded from the figures on this page; no number here is model-generated.</p>
		</section>
	);
}

function ContextTile({ data }: { data: CostIntelligence }) {
	const c = data.context;
	const other = Math.max(data.headline.spend_mtd_usd - c.context_spend_mtd_usd, 0);
	return (
		<Panel title="Context cost" description="Prompt, skills, and tool definitions re-read on every call.">
			<p className="text-2xl font-semibold tabular-nums">{usd(c.context_spend_mtd_usd)}</p>
			<p className="mb-4 text-xs text-muted-foreground">spent carrying context this month · {pct(c.context_share_pct)} of spend</p>
			<Donut
				stacked
				ariaLabel="Context spend versus other spend this month"
				slices={[
					{ label: "Carried context", value: c.context_spend_mtd_usd, color: CHART.found },
					{ label: "Everything else", value: other, color: CHART.spend },
				]}
			/>
			<dl className="mt-4 space-y-2 border-t border-border pt-4 text-xs">
				<div className="flex justify-between gap-3">
					<dt className="text-muted-foreground">Median first-turn context</dt>
					<dd className="tabular-nums">{c.median_first_turn_tokens === null ? "—" : `${tokens(c.median_first_turn_tokens)} tokens`}</dd>
				</div>
				<div className="flex justify-between gap-3">
					<dt className="text-muted-foreground">Trimming opportunity</dt>
					<dd className="font-semibold tabular-nums">
						{usd(c.pruning_opportunity_monthly_usd)}/mo
						{c.agents_over_budget > 0 && <span className="font-normal text-muted-foreground"> · {c.agents_over_budget} agent(s)</span>}
					</dd>
				</div>
			</dl>
		</Panel>
	);
}

type DriverView = "by_agent" | "by_model" | "by_harness";
const DRIVER_VIEWS: { key: DriverView; label: string }[] = [
	{ key: "by_agent", label: "Agent" },
	{ key: "by_model", label: "Model" },
	{ key: "by_harness", label: "Harness" },
];

function DriversPanel({ data }: { data: CostIntelligence }) {
	const [view, setView] = useState<DriverView>("by_agent");
	const items = data.drivers[view].map((d) => (view === "by_model" ? { ...d, label: modelLabel(d.label) } : d));
	const models = data.drivers.by_model.map((d) => ({ label: modelLabel(d.label), value: d.spend_usd }));
	return (
		<Panel
			title="Top cost drivers"
			description="Month-to-date spend, largest first."
			action={
				<div className="flex rounded-lg bg-surface-raised p-0.5" role="group" aria-label="Group spend by">
					{DRIVER_VIEWS.map((v) => (
						<button
							key={v.key}
							type="button"
							onClick={() => setView(v.key)}
							aria-pressed={view === v.key}
							className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
								view === v.key ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"
							}`}
						>
							{v.label}
						</button>
					))}
				</div>
			}
		>
			<RankedBars items={items} ariaLabel={`Spend by ${view.replace("by_", "")}`} />
			{view === "by_agent" && models.length > 0 && (
				<div className="mt-5 border-t border-border pt-4">
					<p className="mb-3 text-xs text-muted-foreground">Share of spend by model</p>
					<Donut ariaLabel="Share of spend by model" slices={models} />
				</div>
			)}
		</Panel>
	);
}

function UnitRow({ label, current, previous, note }: { label: string; current: number | null; previous: number | null; note?: string }) {
	return (
		<div>
			<div className="mb-1.5 flex items-baseline justify-between gap-3">
				<p className="text-xs font-medium">{label}</p>
				{note && <p className="text-2xs text-muted-foreground">{note}</p>}
			</div>
			<PairBars
				rows={[
					{ label: "This month", value: current, tone: "current" },
					{ label: "Last month", value: previous, tone: "reference" },
				]}
			/>
		</div>
	);
}

function UnitEconomicsPanel({
	current,
	previous,
	criteria,
}: {
	current: CostUnitEconomics;
	previous: CostUnitEconomics;
	criteria: CostIntelligence["unit_economics"]["criteria"];
}) {
	return (
		<Panel title="Unit economics" description="What each unit of output costs, so rising spend is read against what it produced.">
			<div className="space-y-5">
				<UnitRow
					label="Cost per successful task"
					current={current.cost_per_successful_session_usd}
					previous={previous.cost_per_successful_session_usd}
					note={current.outcome_coverage_pct === null ? "No outcomes judged yet" : `${current.judged_sessions} of ${current.sessions} sessions judged (${pct(current.outcome_coverage_pct)})`}
				/>
				<UnitRow label="Cost per pull request" current={current.cost_per_pr_usd} previous={previous.cost_per_pr_usd} note={`${current.prs_created} PRs opened this month`} />
				<UnitRow label="Cost per session" current={current.cost_per_session_usd} previous={previous.cost_per_session_usd} note={`${current.sessions} sessions`} />
			</div>
			<p className="mt-4 text-2xs text-muted-foreground">
				Successful means the session's goal was judged fully or mostly achieved in Agent Insights. Failed attempts are included in the cost, so
				each success carries what it took to get there.
				{criteria.agents_with_spend > 0 &&
					` ${criteria.agents_with_criteria} of ${criteria.agents_with_spend} agents with spend this month define creator success criteria; adding them in the Agent Builder makes "successful" mean what the agent was built to do.`}
			</p>
		</Panel>
	);
}

function AnomalyLine({ data }: { data: CostIntelligence }) {
	const a = data.anomalies[0];
	if (!a) return null;
	return (
		<div role="status" className="flex items-start gap-2.5 rounded-xl bg-warning/10 px-4 py-3 text-sm">
			<AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" aria-hidden="true" />
			<p>
				<span className="font-medium">Spend spike:</span> {a.agent} spent {usd(a.spend_usd)} on{" "}
				{new Date(`${a.date}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" })}
				{a.multiple ? `, ${a.multiple}× its usual day` : ""}
				{a.top_model ? `, mostly ${modelLabel(a.top_model)}` : ""}.
				{data.anomalies.length > 1 && <span className="text-muted-foreground"> {data.anomalies.length - 1} more this month.</span>}
			</p>
		</div>
	);
}

function CoverageNote({ data }: { data: CostIntelligence }) {
	const c = data.coverage;
	const unpricedModels = Object.keys(c.unpriced_models);
	const credits = Object.entries(c.unpriced_credits);
	return (
		<div className="flex items-start gap-2.5 rounded-xl bg-surface-raised px-4 py-3 text-xs text-muted-foreground">
			<Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
			<div className="space-y-1">
				<p>
					{c.priced_token_pct === null ? "No token usage this month." : `${pct(c.priced_token_pct, 1)} of this month's tokens are priced.`} {c.basis}
					{c.customized_pricing && " Custom price overrides are active."}
				</p>
				{(unpricedModels.length > 0 || credits.length > 0) && (
					<p>
						Not included:{" "}
						{[
							...unpricedModels.map((m) => `${m} (${tokens(c.unpriced_models[m])} tokens, no price)`),
							...credits.map(([h, v]) => `${v.toFixed(1)} ${h} credits (no credit rate set)`),
						].join("; ")}
						. Add prices in{" "}
						<Link to="/settings" className="underline underline-offset-2 hover:text-foreground">
							Settings → Cost
						</Link>
						.
					</p>
				)}
			</div>
		</div>
	);
}

function LoadingState() {
	return (
		<div className="space-y-5 pt-4" aria-busy="true" aria-label="Loading cost intelligence">
			<div className="grid grid-cols-1 gap-px overflow-hidden rounded-xl bg-border sm:grid-cols-2 xl:grid-cols-4">
				{[0, 1, 2, 3].map((i) => (
					<div key={i} className="h-24 animate-pulse bg-card" />
				))}
			</div>
			<div className="h-80 animate-pulse rounded-xl bg-card" />
			<div className="grid gap-5 lg:grid-cols-3">
				{[0, 1, 2].map((i) => (
					<div key={i} className="h-72 animate-pulse rounded-xl bg-card" />
				))}
			</div>
		</div>
	);
}

export function CostTab() {
	const { data, isLoading, isError, error, refetch } = useCostIntelligence();
	const recompute = useRecomputeCostIntelligence();

	if (isLoading) return <LoadingState />;
	if (isError || !data) {
		return (
			<div className="flex flex-col items-center gap-3 py-16 text-center">
				<p className="text-sm font-medium">Cost data could not be loaded.</p>
				<p className="max-w-md text-sm text-muted-foreground">
					{(error as Error | null)?.message || "Telemetry may be temporarily unavailable."} Spend is never shown as zero when it cannot be read.
				</p>
				<Button variant="outline" size="sm" onClick={() => refetch()}>
					Try again
				</Button>
			</div>
		);
	}

	const hasSpend = data.trend.some((t) => (t.spend_usd ?? 0) > 0);
	const retention = retentionNote(data);

	return (
		<div className="space-y-5 pt-4">
			<div className="flex flex-wrap items-center justify-between gap-3">
				<p className="text-xs text-muted-foreground">
					{data.period.label} · updated{" "}
					{new Date(data.generated_at).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}
				</p>
				<Button variant="outline" size="sm" onClick={() => recompute.mutate()} disabled={recompute.isPending}>
					<RefreshCw className={`h-3.5 w-3.5 ${recompute.isPending ? "animate-spin" : ""}`} />
					Recompute
				</Button>
			</div>

			{!hasSpend ? (
				<div className="rounded-xl bg-card p-8 text-center shadow-sm">
					<p className="text-sm font-medium">No priced agent usage in the last six months.</p>
					<p className="mx-auto mt-1 max-w-lg text-sm text-muted-foreground">
						Spend appears once harness sessions with token usage reach Observal. Run <code className="font-mono text-xs">observal doctor patch</code>{" "}
						on a developer machine to install session telemetry.
					</p>
				</div>
			) : (
				<>
					<AnomalyLine data={data} />
					<SectionHeading>Spend and savings</SectionHeading>
					<Headline data={data} />
					<Panel
						title="Spend vs. savings found and actioned"
						description={trendSentence(data)}
						action={
							<ChartLegend
								items={[
									{ label: "Total spend", color: CHART.spend, shape: "bar" },
									{ label: "Savings found", color: CHART.found },
									{ label: "Savings actioned", color: CHART.actioned },
								]}
							/>
						}
					>
						<SpendSavingsTrend data={data.trend} />
						{retention && <p className="mt-2 text-2xs text-muted-foreground">{retention}</p>}
					</Panel>

					<SectionHeading>Where the savings come from</SectionHeading>
					<div className="grid gap-5 lg:grid-cols-3">
						<TokenTile data={data} />
						<InsightTile data={data} />
						<ContextTile data={data} />
					</div>

					<SectionHeading>Supporting detail</SectionHeading>
					<div className="grid gap-5 lg:grid-cols-2">
						<DriversPanel data={data} />
						<Panel
							title="Savings pipeline"
							description={`Monthly run rates from the last ${data.period.detection_window_days} days. Actioned savings are measured after release, not assumed.`}
						>
							<PipelineStages pipeline={data.pipeline} />
							<div className="mt-5 border-t border-border pt-2">
								<OpportunityList data={data} />
							</div>
						</Panel>
					</div>
					<UnitEconomicsPanel
						current={data.unit_economics.current}
						previous={data.unit_economics.previous}
						criteria={data.unit_economics.criteria}
					/>
				</>
			)}
			<CoverageNote data={data} />
		</div>
	);
}
