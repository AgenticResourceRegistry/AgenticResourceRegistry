// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0


import { Link, useParams, useRouter } from "@tanstack/react-router";
import { REVERSE_TYPE_MAP } from "@/components/registry/agent-component-constants";
import { RegistryMark, useRegistryName } from "@/components/registry/registry-mark";
import React from "react";
import {
	ArrowLeft,
	Lightbulb,
	Loader2,
	CheckCircle2,
	XCircle,
	Clock,
	Coins,
	Zap,
	Users,
	Timer,
	AlertTriangle,
	Wrench,
	TrendingUp,
	TrendingDown,
	DollarSign,
	Database,
	ThumbsUp,
	Sparkles,
	ArrowUpRight,
	ArrowDownRight,
	ArrowRight,
	Info,
	Target,
	Shield,
	Download,
	MessageSquare,
	Copy,
	Check,
	Brain,
} from "lucide-react";
import { useInsightReport } from "@/hooks/use-api";
import { useApplyInsightSuggestions } from "@/hooks/use-insights-api";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/layouts/page-header";
import { ErrorState } from "@/components/shared/error-state";
import { insights } from "@/lib/api";
import { registryItemPath } from "@/lib/registry-name";
import type {
	ComponentRef,
	FeatureSuggestion,
	InsightReport,
	RegistryMatchSummary,
} from "@/lib/types";

// ── Status Indicator ──────────────────────────────────────────────────────

function StatusIndicator({ status }: { status: InsightReport["status"] }) {
	switch (status) {
		case "completed":
			return (
				<div className="inline-flex items-center gap-1.5 text-sm font-medium text-success">
					<CheckCircle2 className="h-4 w-4" /> Completed
				</div>
			);
		case "running":
			return (
				<div className="inline-flex items-center gap-1.5 text-sm font-medium text-info">
					<Loader2 className="h-4 w-4 animate-spin" /> Generating report...
				</div>
			);
		case "pending":
			return (
				<div className="inline-flex items-center gap-1.5 text-sm font-medium text-muted-foreground">
					<Clock className="h-4 w-4" /> Queued
				</div>
			);
		case "failed":
			return (
				<div className="inline-flex items-center gap-1.5 text-sm font-medium text-destructive">
					<XCircle className="h-4 w-4" /> Failed
				</div>
			);
	}
}

// ── Metric Card ──────────────────────────────────────────────────────────

function MetricCard({
	label,
	value,
	icon: Icon,
	subtext,
}: {
	label: string;
	value: string | number;
	icon: React.ComponentType<{ className?: string }>;
	subtext?: string;
}) {
	return (
		<div className="rounded-lg border border-border bg-card p-4">
			<div className="flex items-center gap-2 text-muted-foreground mb-1">
				<Icon className="h-4 w-4" />
				<span className="text-xs font-medium uppercase tracking-wider">
					{label}
				</span>
			</div>
			<div className="font-[family-name:var(--font-mono)] text-2xl font-bold tabular-nums">
				{value}
			</div>
			{subtext && (
				<div className="text-xs text-muted-foreground mt-1">{subtext}</div>
			)}
		</div>
	);
}

function hasText(value: unknown): value is string {
	return typeof value === "string" && value.trim().length > 0;
}

function hasPositiveNumber(value: unknown): boolean {
	return typeof value === "number" && Number.isFinite(value) && value > 0;
}

function formatInsightLabel(value: string): string {
	return value.replace(/_/g, " ");
}

// ── At a Glance (executive summary) ──────────────────────────────────────

function AtAGlance({ data }: { data: unknown }) {
	if (!data) return null;

	// Handle V1 string format
	if (typeof data === "string") {
		return (
			<div className="rounded-lg border border-primary-accent/20 bg-primary-accent/5 p-5">
				<div className="flex items-center gap-2 mb-2">
					<Lightbulb className="h-4 w-4 text-primary-accent" />
					<h3 className="font-[family-name:var(--font-display)] text-sm font-semibold">
						At a Glance
					</h3>
				</div>
				<p className="text-sm leading-relaxed">{data}</p>
			</div>
		);
	}

	// V3 structured format (4-panel like pi /insights)
	const obj = data as Record<string, string>;
	const panels = [
		{ key: "whats_working", label: "What's Working", color: "text-success" },
		{ key: "whats_hindering", label: "What's Hindering", color: "text-destructive" },
		{ key: "quick_win", label: "Quick Wins", color: "text-primary-accent" },
		{ key: "ambitious_workflows", label: "Ambitious Workflows", color: "text-purple-400" },
	];

	const hasContent = panels.some(({ key }) => obj[key]);
	if (!hasContent) return null;

	return (
		<div className="rounded-lg border border-border overflow-hidden">
			<div className="flex items-center justify-between px-5 py-3 border-b border-border bg-card">
				<div className="flex items-center gap-2">
					<Lightbulb className="h-4 w-4 text-primary-accent" />
					<h3 className="font-[family-name:var(--font-display)] text-sm font-semibold">
						At a Glance
					</h3>
				</div>
				{obj.health && (
					<span
						className={`text-xs px-2 py-0.5 rounded-full font-medium ${
							obj.health === "healthy"
								? "bg-success/20 text-success"
								: obj.health === "concerning"
									? "bg-destructive/20 text-destructive"
									: "bg-warning/20 text-warning"
						}`}
					>
						{obj.health}
					</span>
				)}
			</div>
			<div className="divide-y divide-border">
				{panels.map(
					({ key, label, color }) =>
						obj[key] && (
							<div key={key} className="px-5 py-4">
								<h4 className={`text-xs font-semibold uppercase tracking-wider mb-2 ${color}`}>
									{label}
								</h4>
								<p className="text-sm text-foreground/80 leading-relaxed">
									{obj[key]}
								</p>
							</div>
						),
				)}
			</div>
		</div>
	);
}

// ── Usage Patterns Section ──────────────────────────────────────────────

function UsagePatterns({ data }: { data: unknown }) {
	if (!data) return null;

	// Handle V1 array/string format
	if (Array.isArray(data) || typeof data === "string") {
		return (
			<GenericBulletSection
				title="Usage Patterns"
				icon={TrendingUp}
				items={data}
			/>
		);
	}

	const obj = data as Record<string, unknown>;
	const narrative = obj.narrative as string | undefined;
	const toolDist = obj.tool_distribution as
		| { tool: string; calls: number; error_rate: number }[]
		| undefined;
	const sessionProfile = obj.session_profile as
		| Record<string, unknown>
		| undefined;

	return (
		<div className="rounded-lg border border-border bg-card">
			<div className="flex items-center gap-2 px-5 py-3 border-b border-border">
				<TrendingUp className="h-4 w-4 text-primary-accent" />
				<h3 className="font-[family-name:var(--font-display)] text-sm font-semibold">
					How the Agent File Is Used
				</h3>
			</div>
			<div className="px-5 py-4 space-y-4">
				{narrative && (
					<p className="text-sm leading-relaxed text-foreground/80">
						{narrative}
					</p>
				)}

				{/* Session profile summary */}
				{sessionProfile && (
					<div className="flex flex-wrap gap-4 py-2">
						{sessionProfile.avg_duration_minutes != null && (
							<div className="text-center">
								<div className="text-lg font-bold tabular-nums">{`${sessionProfile.avg_duration_minutes}m`}</div>
								<div className="text-xs text-muted-foreground">
									avg duration
								</div>
							</div>
						)}
						{sessionProfile.avg_tool_calls != null && (
							<div className="text-center">
								<div className="text-lg font-bold tabular-nums">{`${sessionProfile.avg_tool_calls}`}</div>
								<div className="text-xs text-muted-foreground">
									avg tool calls
								</div>
							</div>
						)}
						{sessionProfile.session_type != null && (
							<div className="text-center">
								<div className="text-lg font-bold">
									{`${sessionProfile.session_type}`.replace(/_/g, " ")}
								</div>
								<div className="text-xs text-muted-foreground">
									typical session
								</div>
							</div>
						)}
					</div>
				)}

				{/* Tool distribution bars */}
				{toolDist && toolDist.length > 0 && (
					<div className="space-y-2">
						<div className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
							Tool Usage
						</div>
						{toolDist.slice(0, 6).map((tool) => {
							const maxCalls = Math.max(...toolDist.map((t) => t.calls));
							const pct = maxCalls > 0 ? (tool.calls / maxCalls) * 100 : 0;
							return (
								<div
									key={tool.tool}
									className="flex items-center gap-3 text-sm"
								>
									<span className="w-20 truncate font-[family-name:var(--font-mono)] text-xs">
										{tool.tool}
									</span>
									<div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
										<div
											className="h-full bg-primary-accent/60 rounded-full"
											style={{ width: `${pct}%` }}
										/>
									</div>
									<span className="w-12 text-right tabular-nums text-xs">
										{tool.calls}
									</span>
									{tool.error_rate > 0 && (
										<span className="text-xs text-destructive">
											{tool.error_rate.toFixed(0)}% err
										</span>
									)}
								</div>
							);
						})}
					</div>
				)}
			</div>
		</div>
	);
}

// ── Strengths Section ────────────────────────────────────────────────────

function StrengthsSection({ data }: { data: unknown }) {
	if (!data) return null;

	if (Array.isArray(data) || typeof data === "string") {
		return (
			<GenericBulletSection
				title="What Works Well"
				icon={ThumbsUp}
				items={data}
				color="text-success"
			/>
		);
	}

	const obj = data as Record<string, unknown>;
	const intro = obj.intro as string | undefined;
	const strengths = obj.strengths as
		| { title: string; description: string }[]
		| undefined;

	if (!strengths || strengths.length === 0) return null;

	return (
		<div className="rounded-lg border border-border bg-card">
			<div className="flex items-center gap-2 px-5 py-3 border-b border-border">
				<ThumbsUp className="h-4 w-4 text-primary-accent" />
				<h3 className="font-[family-name:var(--font-display)] text-sm font-semibold">
					What Works Well
				</h3>
			</div>
			<div className="px-5 py-4 space-y-3">
				{intro && <p className="text-sm text-muted-foreground">{intro}</p>}
				<div className="space-y-3">
					{strengths.map((s, i) => (
						<div
							key={i}
							className="rounded-md border border-success/20 bg-success/5 p-3"
						>
							<div className="font-medium text-sm text-success mb-1">
								{s.title}
							</div>
							<div className="text-sm text-foreground/70">{s.description}</div>
						</div>
					))}
				</div>
			</div>
		</div>
	);
}

// ── Friction Section ─────────────────────────────────────────────────────

function FrictionSection({ data }: { data: unknown }) {
	if (!data) return null;

	if (Array.isArray(data) || typeof data === "string") {
		return (
			<GenericBulletSection
				title="Where Things Go Wrong"
				icon={AlertTriangle}
				items={data}
				color="text-destructive"
			/>
		);
	}

	const obj = data as Record<string, unknown>;
	const intro = obj.intro as string | undefined;
	const categories = obj.categories as
		| {
				title: string;
				severity: string;
				description: string;
				evidence: string;
				impact: string;
		  }[]
		| undefined;

	if (!categories || categories.length === 0) return null;

	return (
		<div className="rounded-lg border border-border bg-card">
			<div className="flex items-center gap-2 px-5 py-3 border-b border-border">
				<AlertTriangle className="h-4 w-4 text-primary-accent" />
				<h3 className="font-[family-name:var(--font-display)] text-sm font-semibold">
					Where Things Go Wrong
				</h3>
			</div>
			<div className="px-5 py-4 space-y-3">
				{intro && <p className="text-sm text-muted-foreground">{intro}</p>}
				<div className="space-y-3">
					{categories.map((cat, i) => (
						<div
							key={i}
							className="rounded-md border border-destructive/20 bg-destructive/5 p-3"
						>
							<div className="flex items-center justify-between mb-1">
								<div className="font-medium text-sm text-destructive">
									{cat.title}
								</div>
								<span
									className={`text-xs px-2 py-0.5 rounded-full ${
										cat.severity === "high"
											? "bg-destructive/20 text-destructive"
											: cat.severity === "medium"
												? "bg-warning/20 text-warning"
												: "bg-muted text-muted-foreground"
									}`}
								>
									{cat.severity}
								</span>
							</div>
							<div className="text-sm text-foreground/70 mb-1">
								{cat.description}
							</div>
							{cat.evidence && (
								<div className="text-xs text-muted-foreground font-[family-name:var(--font-mono)] mt-2 p-2 bg-muted/30 rounded">
									{cat.evidence}
								</div>
							)}
						</div>
					))}
				</div>
			</div>
		</div>
	);
}

// ── Copy Button helper ─────────────────────────────────────────────────

function CopyButton({ text, className }: { text: string; className?: string }) {
	const [copied, setCopied] = React.useState(false);
	return (
		<button
			type="button"
			className={`inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-md border border-border hover:bg-muted/50 text-muted-foreground transition-colors ${className || ""}`}
			onClick={(event) => {
				event.stopPropagation();
				navigator.clipboard.writeText(text).then(() => {
					setCopied(true);
					setTimeout(() => setCopied(false), 2000);
				}).catch(() => {});
			}}
		>
			{copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
			{copied ? "Copied" : "Copy"}
		</button>
	);
}

// ── Interaction Style Section ──────────────────────────────────────────

function InteractionStyleSection({ data }: { data: unknown }) {
	if (!data || typeof data !== "object") return null;
	const obj = data as { narrative?: string; key_pattern?: string };
	if (!obj.narrative) return null;

	return (
		<div className="rounded-lg border border-border bg-card">
			<div className="flex items-center gap-2 px-5 py-3 border-b border-border">
				<MessageSquare className="h-4 w-4 text-primary-accent" />
				<h3 className="font-[family-name:var(--font-display)] text-sm font-semibold">
					Interaction Style
				</h3>
			</div>
			<div className="px-5 py-4 space-y-4">
				<div className="text-sm leading-relaxed text-foreground/80 space-y-3">
					{obj.narrative.split(/\n\n/).map((para: string, i: number) => (
						<p key={i}>
							{para.split(/\*\*(.+?)\*\*/).map((seg: string, j: number) =>
								j % 2 === 1 ? <strong key={j}>{seg}</strong> : <span key={j}>{seg}</span>
							)}
						</p>
					))}
				</div>
				{obj.key_pattern && (
					<div className="px-4 py-3 rounded-md border border-primary-accent/20 bg-primary-accent/5 text-sm italic text-primary-accent">
						&ldquo;{obj.key_pattern}&rdquo;
					</div>
				)}
			</div>
		</div>
	);
}

// ── Facets Bar Chart Section ──────────────────────────────────────────

function FacetsCharts({ facets }: { facets: Record<string, unknown> | undefined }) {
	if (!facets) return null;

	const goalCats = (facets.goal_categories || []) as [string, number][];
	const outcomes = facets.outcomes as Record<string, number> | undefined;
	const satisfaction = facets.satisfaction as Record<string, number> | undefined;
	const frictionTypes = (facets.friction_types || []) as [string, number][];

	const hasData = goalCats.length > 0 || outcomes || satisfaction || frictionTypes.length > 0;
	if (!hasData) return null;

	const renderBarChart = (items: [string, number][], color: string) => {
		const max = items[0]?.[1] || 1;
		return items.slice(0, 8).map(([name, count]) => (
			<div key={name} className="flex items-center gap-2 mb-1.5 text-xs">
				<span className="w-36 shrink-0 text-muted-foreground truncate capitalize">
					{name.replace(/_/g, " ")}
				</span>
				<div className="flex-1 h-2.5 bg-muted/30 rounded-full overflow-hidden">
					<div
						className={`h-full rounded-full transition-all ${color}`}
						style={{ width: `${(count / max) * 100}%` }}
					/>
				</div>
				<span className="w-10 text-right text-muted-foreground tabular-nums font-mono">{count}</span>
			</div>
		));
	};

	const renderRecordChart = (rec: Record<string, number>, color: string) => {
		const sorted = Object.entries(rec).sort((a, b) => b[1] - a[1]);
		return renderBarChart(sorted as [string, number][], color);
	};

	return (
		<div className="grid grid-cols-1 md:grid-cols-2 gap-4">
			{goalCats.length > 0 && (
				<div className="rounded-lg border border-border bg-card p-4">
					<h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">Goal Categories</h3>
					{renderBarChart(goalCats, "bg-primary-accent")}
				</div>
			)}
			{outcomes && Object.keys(outcomes).length > 0 && (
				<div className="rounded-lg border border-border bg-card p-4">
					<h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">Outcomes</h3>
					{renderRecordChart(outcomes, "bg-amber-400")}
				</div>
			)}
			{satisfaction && Object.keys(satisfaction).length > 0 && (
				<div className="rounded-lg border border-border bg-card p-4">
					<h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">Satisfaction</h3>
					{renderRecordChart(satisfaction, "bg-violet-400")}
				</div>
			)}
			{frictionTypes.length > 0 && (
				<div className="rounded-lg border border-border bg-card p-4">
					<h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">Friction Types</h3>
					{renderBarChart(frictionTypes, "bg-destructive/60")}
				</div>
			)}
		</div>
	);
}

// ── Project Areas Section ──────────────────────────────────────────────

function ProjectAreas({ data }: { data: unknown }) {
	if (!data || typeof data !== "object") return null;
	const obj = data as { areas?: { name: string; sessions: number; description: string }[] };
	const areas = obj.areas;
	if (!areas || areas.length === 0) return null;

	return (
		<div className="rounded-lg border border-border bg-card">
			<div className="flex items-center gap-2 px-5 py-3 border-b border-border">
				<Target className="h-4 w-4 text-primary-accent" />
				<h3 className="font-[family-name:var(--font-display)] text-sm font-semibold">
					Project Areas
				</h3>
			</div>
			<div className="p-4 grid grid-cols-1 md:grid-cols-2 gap-3">
				{areas.map((area, i) => (
					<div key={i} className="rounded-md border border-border bg-muted/10 p-3">
						<div className="flex items-center justify-between mb-1">
							<div className="font-medium text-sm">{area.name}</div>
							<span className="text-xs px-2 py-0.5 rounded-full bg-muted text-muted-foreground">
								{area.sessions} sessions
							</span>
						</div>
						<p className="text-sm text-foreground/60">{area.description}</p>
					</div>
				))}
			</div>
		</div>
	);
}

// ── Suggestions Section (V4: config_additions + features + patterns) ──

/** A resolved registry reference, attached server-side after validation. */
const COMPONENT_TYPE_LABELS: Record<string, string> = {
	skill: "Skill",
	hook: "Hook",
	prompt: "Prompt",
	mcp: "MCP Client",
	sandbox: "Sandbox",
};

/**
 * The identity of a component the registry already holds.
 *
 * This is the whole point of the feature — "you already own this, don't build
 * it again" — so it leads with the instance's own mark and reads as a
 * destination, not a footnote. The link stops propagation because the
 * surrounding card owns selection for the Apply flow; navigating and
 * selecting are different intents and must not fight.
 */
function ReuseBadge({ componentRef }: { componentRef: ComponentRef }) {
	const label = COMPONENT_TYPE_LABELS[componentRef.type] ?? componentRef.type;
	const registryName = useRegistryName();
	return (
		<div className="mt-3 rounded-lg border border-primary-accent/35 bg-primary-accent/[0.07] overflow-hidden">
			<div className="flex items-center gap-2 px-3 py-2 border-b border-primary-accent/20 bg-primary-accent/[0.06]">
				<RegistryMark size={15} />
				<span className="text-xs font-semibold tracking-tight text-primary-accent">
					Already in {registryName}
				</span>
				<span className="ml-auto text-[11px] uppercase tracking-wide text-muted-foreground">
					{label}
				</span>
			</div>

			<Link
				to={registryItemPath(
					{ qualified_name: componentRef.qualified_name },
					REVERSE_TYPE_MAP[componentRef.type] ?? "mcps",
					componentRef.id,
				)}
				onClick={(event) => event.stopPropagation()}
				className="group/ref flex items-center gap-2 px-3 py-2.5 transition-colors duration-150 hover:bg-primary-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-accent/60 focus-visible:ring-inset"
			>
				<span className="min-w-0 flex-1">
					<span className="block font-[family-name:var(--font-mono)] text-sm font-medium text-foreground break-all group-hover/ref:text-primary-accent transition-colors duration-150">
						{componentRef.qualified_name || componentRef.name}
					</span>
					<span className="mt-0.5 block text-xs text-muted-foreground">
						v{componentRef.latest_version} · Open in registry
					</span>
				</span>
				<ArrowRight className="h-4 w-4 shrink-0 text-primary-accent transition-transform duration-150 group-hover/ref:translate-x-0.5 motion-reduce:transition-none motion-reduce:group-hover/ref:translate-x-0" />
			</Link>
		</div>
	);
}

/** What the reuse search did. Absent on reports generated before this existed. */
/**
 * Explains the outcome of the reuse search when it produced nothing.
 *
 * Silence is ambiguous: a reader cannot tell "we searched and nothing fit"
 * from "we never searched". Both are legitimate outcomes, and each suggests a
 * different action, so each gets said out loud. Renders nothing when reuse
 * suggestions were actually made — the cards speak for themselves.
 */
function RegistryMatchNote({ summary }: { summary?: RegistryMatchSummary }) {
	const registryName = useRegistryName();
	if (!summary) return null;
	if ((summary.reused ?? 0) > 0) return null;

	let message: string;
	if (summary.enabled === false) {
		message = `Reuse suggestions are turned off, so this report only proposes new components.`;
	} else if (summary.registry_has_components === false) {
		message = `No components have been published to ${registryName} yet, so there was nothing to reuse. Publish skills, hooks or prompts and future reports will point at them.`;
	} else if ((summary.offered ?? 0) === 0) {
		message = `Nothing in ${registryName} looked close enough to this Agent File's work to recommend. More sessions give the match more to go on.`;
	} else {
		message = `Checked ${summary.offered} component${summary.offered === 1 ? "" : "s"} already in ${registryName}; none fit this Agent File's problems closely enough to recommend.`;
	}

	return (
		<div className="flex items-start gap-2 rounded-md border border-border bg-muted/20 px-3 py-2.5">
			<Info className="h-4 w-4 shrink-0 mt-0.5 text-muted-foreground" />
			<p className="text-xs text-muted-foreground">{message}</p>
		</div>
	);
}

function SuggestionsSection({
	data,
	report,
	registryMatch,
}: {
	data: unknown;
	report?: InsightReport;
	registryMatch?: RegistryMatchSummary;
}) {
	const applySuggestions = useApplyInsightSuggestions();
	const [showConfirm, setShowConfirm] = React.useState(false);
	const [selectedConfigs, setSelectedConfigs] = React.useState<Set<number>>(new Set());
	const [selectedFeatures, setSelectedFeatures] = React.useState<Set<number>>(new Set());
	const [selectedPatterns, setSelectedPatterns] = React.useState<Set<number>>(new Set());
	const [initialized, setInitialized] = React.useState(false);

	if (!data) return null;

	if (Array.isArray(data) || typeof data === "string") {
		return (
			<GenericBulletSection
				title="Suggestions"
				icon={Lightbulb}
				items={data}
				numbered
			/>
		);
	}

	const obj = data as Record<string, unknown>;

	// V4 format
	const configAdditions = obj.config_additions as { addition: string; why: string; where: string; confidence?: string; risk?: string }[] | undefined;
	const featuresToTry = obj.features_to_try as FeatureSuggestion[] | undefined;
	const usagePatterns = obj.usage_patterns as { title: string; suggestion: string; detail: string; copyable_prompt: string }[] | undefined;

	// V3 fallback
	const items = obj.items as { title: string; action: string; why: string; priority: string }[] | undefined;

	const hasV4 = (configAdditions && configAdditions.length > 0) || (featuresToTry && featuresToTry.length > 0) || (usagePatterns && usagePatterns.length > 0);

	// Initialize all selected by default
	if (!initialized && hasV4) {
		const cSet = new Set(configAdditions?.map((_, i) => i) ?? []);
		const fSet = new Set(featuresToTry?.map((_, i) => i) ?? []);
		const pSet = new Set(usagePatterns?.map((_, i) => i) ?? []);
		if (cSet.size || fSet.size || pSet.size) {
			setSelectedConfigs(cSet);
			setSelectedFeatures(fSet);
			setSelectedPatterns(pSet);
			setInitialized(true);
		}
	}

	const toggleConfig = (i: number) => {
		setSelectedConfigs((s) => { const n = new Set(s); n.has(i) ? n.delete(i) : n.add(i); return n; });
	};
	const toggleFeature = (i: number) => {
		setSelectedFeatures((s) => { const n = new Set(s); n.has(i) ? n.delete(i) : n.add(i); return n; });
	};
	const togglePattern = (i: number) => {
		setSelectedPatterns((s) => { const n = new Set(s); n.has(i) ? n.delete(i) : n.add(i); return n; });
	};

	const totalSelected = selectedConfigs.size + selectedFeatures.size + selectedPatterns.size;
	const totalSelectable = (configAdditions?.length ?? 0) + (featuresToTry?.length ?? 0) + (usagePatterns?.length ?? 0);
	const allSelected = totalSelectable > 0 && totalSelected === totalSelectable;

	const selectAll = () => {
		setSelectedConfigs(new Set(configAdditions?.map((_, i) => i) ?? []));
		setSelectedFeatures(new Set(featuresToTry?.map((_, i) => i) ?? []));
		setSelectedPatterns(new Set(usagePatterns?.map((_, i) => i) ?? []));
		setShowConfirm(false);
	};

	const unselectAll = () => {
		setSelectedConfigs(new Set());
		setSelectedFeatures(new Set());
		setSelectedPatterns(new Set());
		setShowConfirm(false);
	};

	const handleApply = () => {
		if (!report) return;
		const selection: { config_indices?: number[]; feature_indices?: number[]; pattern_indices?: number[] } = {};
		if (selectedConfigs.size < (configAdditions?.length ?? 0)) {
			selection.config_indices = [...selectedConfigs];
		}
		if (selectedFeatures.size < (featuresToTry?.length ?? 0)) {
			selection.feature_indices = [...selectedFeatures];
		}
		if (selectedPatterns.size < (usagePatterns?.length ?? 0)) {
			selection.pattern_indices = [...selectedPatterns];
		}
		const hasSelection = Object.keys(selection).length > 0;
		applySuggestions.mutate({
			agentId: report.agent_id,
			reportId: report.id,
			selection: hasSelection ? selection : undefined,
		});
		setShowConfirm(false);
	};

	if (!hasV4 && (!items || items.length === 0)) return null;

	return (
		<div className="rounded-lg border border-border bg-card">
			<div className="flex items-center justify-between px-5 py-3 border-b border-border">
				<div className="flex items-center gap-2">
					<Lightbulb className="h-4 w-4 text-primary-accent" />
					<h3 className="font-[family-name:var(--font-display)] text-sm font-semibold">
						Suggestions
					</h3>
				</div>
				{report && !report.applied_at && hasV4 && (
					<div className="relative">
						{!showConfirm ? (
							<div className="flex items-center gap-2">
								<Button
									variant="ghost"
									size="sm"
									className="h-8 text-xs"
									disabled={applySuggestions.isPending || totalSelectable === 0}
									onClick={allSelected ? unselectAll : selectAll}
								>
									{allSelected ? "Unselect all" : "Select all"}
								</Button>
								<Button
									variant="outline"
									size="sm"
									className="gap-1.5 text-xs"
									disabled={applySuggestions.isPending || totalSelected === 0}
									onClick={() => setShowConfirm(true)}
								>
									<Brain className="h-3.5 w-3.5" /> Apply Selected ({totalSelected})
								</Button>
							</div>
						) : (
							<div className="flex items-center gap-2">
								<span className="text-xs text-muted-foreground">Submit {totalSelected} items to review?</span>
								<Button
									size="sm"
									className="h-7 text-xs gap-1"
									disabled={applySuggestions.isPending}
									onClick={handleApply}
								>
									{applySuggestions.isPending ? (
										<Loader2 className="h-3 w-3 animate-spin" />
									) : (
										<CheckCircle2 className="h-3 w-3" />
									)}
									Confirm
								</Button>
								<Button
									variant="ghost"
									size="sm"
									className="h-7 text-xs"
									onClick={() => setShowConfirm(false)}
								>
									Cancel
								</Button>
							</div>
						)}
					</div>
				)}
				{report?.applied_at && (
					<span className="inline-flex items-center gap-1.5 text-xs font-medium text-success bg-success/10 px-2.5 py-1 rounded-full">
						<CheckCircle2 className="h-3 w-3" /> Applied
					</span>
				)}
			</div>
			<div className="px-5 py-4 space-y-6">
				{configAdditions && configAdditions.length > 0 && (
					<div>
						<h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">Config Additions</h4>
						<p className="text-xs text-muted-foreground mb-3">Add these to your Agent File&apos;s system prompt or AGENTS.md.</p>
						<div className="space-y-2">
							{configAdditions.map((c, i) => (
								<div
									key={i}
									className={`rounded-md border p-3 transition-colors ${!report?.applied_at ? "cursor-pointer" : ""} ${selectedConfigs.has(i) ? "border-primary/40 bg-primary/5" : "border-border bg-muted/10 opacity-60"}`}
									onClick={!report?.applied_at ? () => toggleConfig(i) : undefined}
								>
									<div className="flex items-start gap-3">
										{!report?.applied_at && (
											<input type="checkbox" checked={selectedConfigs.has(i)} onClick={(event) => event.stopPropagation()} onChange={() => toggleConfig(i)} className="mt-1 h-4 w-4 rounded border-border" />
										)}
										<div className="flex-1">
											<span className="text-xs px-2 py-0.5 rounded-full bg-muted text-muted-foreground mr-2">{c.where}</span>
											{c.confidence && <span className="text-xs px-2 py-0.5 rounded-full bg-muted text-muted-foreground mr-2">{c.confidence} confidence</span>}
											{c.risk && <span className="text-xs px-2 py-0.5 rounded-full bg-muted text-muted-foreground mr-2">{c.risk} risk</span>}
											<span className="text-sm font-medium">{c.addition}</span>
										</div>
										<CopyButton text={c.addition} />
									</div>
									{c.why && <p className="text-xs text-muted-foreground mt-2 italic ml-7">Why: {c.why}</p>}
								</div>
							))}
						</div>
					</div>
				)}

				{/* V4: Features to Try */}
				{featuresToTry && featuresToTry.length > 0 && (
					<div>
						<h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">Features to Try</h4>
						<div className="mb-3">
							<RegistryMatchNote summary={registryMatch} />
						</div>
						<div className="grid grid-cols-1 md:grid-cols-2 gap-3">
							{/* Reuse suggestions lead: "you already own this" is a better
							    answer than "build this", so it should be read first. */}
							{featuresToTry
								.map((f, i) => ({ f, i }))
								.sort((a, b) => Number(Boolean(b.f.component_ref)) - Number(Boolean(a.f.component_ref)))
								.map(({ f, i }) => (
								<div
									key={i}
									className={`rounded-md border p-3 transition-colors ${!report?.applied_at ? "cursor-pointer" : ""} ${selectedFeatures.has(i) ? "border-primary/40 bg-primary/5" : "border-border bg-muted/10 opacity-60"}`}
									onClick={!report?.applied_at ? () => toggleFeature(i) : undefined}
								>
									<div className="flex items-start gap-2">
										{!report?.applied_at && (
											<input type="checkbox" checked={selectedFeatures.has(i)} onClick={(event) => event.stopPropagation()} onChange={() => toggleFeature(i)} className="mt-1 h-4 w-4 rounded border-border" />
										)}
										<div className="flex-1">
											<span
												className={`text-xs px-2 py-0.5 rounded-full ${
													f.component_ref
														? "bg-primary-accent/15 text-primary-accent font-medium"
														: "bg-muted text-muted-foreground"
												}`}
											>
												{f.component_ref ? "Reuse — don't rebuild" : (f.action_type ?? f.feature)}
											</span>
											{f.confidence && <span className="ml-1 text-xs px-2 py-0.5 rounded-full bg-muted text-muted-foreground">{f.confidence} confidence</span>}
											{f.risk && <span className="ml-1 text-xs px-2 py-0.5 rounded-full bg-muted text-muted-foreground">{f.risk} risk</span>}
											<div className="font-medium text-sm mt-2">{f.one_liner}</div>
											<p className="text-xs text-foreground/60 mt-1">{f.why_for_you}</p>
											{f.component_ref && <ReuseBadge componentRef={f.component_ref} />}
											{f.match_reason && (
												<p className="text-xs text-muted-foreground mt-2 italic">Why this fits: {f.match_reason}</p>
											)}
											{/* A reuse suggestion has no generated body to copy. */}
											{f.example && !f.component_ref && (
												<>
													<pre className="mt-2 p-2 rounded bg-muted/30 text-xs font-[family-name:var(--font-mono)] text-foreground/70 whitespace-pre-wrap break-all">
														{f.example}
													</pre>
													<CopyButton text={f.example} className="mt-1" />
												</>
											)}
										</div>
									</div>
								</div>
							))}
						</div>
					</div>
				)}

				{/* V4: Usage Patterns with copyable prompts */}
				{usagePatterns && usagePatterns.length > 0 && (
					<div>
						<h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">Usage Patterns</h4>
						<div className="space-y-3">
							{usagePatterns.map((p, i) => (
								<div
									key={i}
									className={`rounded-md border p-3 transition-colors ${!report?.applied_at ? "cursor-pointer" : ""} ${selectedPatterns.has(i) ? "border-primary/40 bg-primary/5" : "border-border bg-muted/10 opacity-60"}`}
									onClick={!report?.applied_at ? () => togglePattern(i) : undefined}
								>
									<div className="flex items-start gap-2">
										{!report?.applied_at && (
											<input type="checkbox" checked={selectedPatterns.has(i)} onClick={(event) => event.stopPropagation()} onChange={() => togglePattern(i)} className="mt-1 h-4 w-4 rounded border-border" />
										)}
										<div className="flex-1">
											<div className="font-medium text-sm">{p.title}</div>
											<p className="text-sm text-foreground/70 mt-1">{p.suggestion}</p>
											<p className="text-xs text-muted-foreground mt-2">{p.detail}</p>
											{p.copyable_prompt && (
												<>
													<pre className="mt-2 p-2 rounded bg-muted/30 text-xs font-[family-name:var(--font-mono)] text-foreground/70 whitespace-pre-wrap break-all">
														{p.copyable_prompt}
													</pre>
													<CopyButton text={p.copyable_prompt} className="mt-1" />
												</>
											)}
										</div>
									</div>
								</div>
							))}
						</div>
					</div>
				)}

				{/* V3 fallback: items array */}
				{!hasV4 && items && items.map((item, i) => (
					<div
						key={i}
						className="rounded-md border border-border bg-muted/10 p-3"
					>
						<div className="flex items-center justify-between mb-1">
							<div className="font-medium text-sm">{item.title}</div>
							<span
								className={`text-xs px-2 py-0.5 rounded-full ${
									item.priority === "high"
										? "bg-destructive/20 text-destructive"
										: item.priority === "medium"
											? "bg-warning/20 text-warning"
											: "bg-muted text-muted-foreground"
								}`}
							>
								{item.priority}
							</span>
						</div>
						<div className="text-sm text-foreground/80 mt-1">{item.action}</div>
						{item.why && (
							<div className="text-xs text-muted-foreground mt-2 italic">
								{item.why}
							</div>
						)}
					</div>
				))}
			</div>
		</div>
	);
}

// ── On the Horizon Section ──────────────────────────────────────────────

function OnTheHorizon({ data }: { data: unknown }) {
	if (!data || typeof data !== "object") return null;
	const obj = data as { intro?: string; opportunities?: { title: string; whats_possible: string; how_to_try: string; copyable_prompt: string }[] };
	const opportunities = obj.opportunities;
	if (!opportunities || opportunities.length === 0) return null;

	return (
		<div className="rounded-lg border border-border bg-card">
			<div className="flex items-center gap-2 px-5 py-3 border-b border-border">
				<Zap className="h-4 w-4 text-violet-400" />
				<h3 className="font-[family-name:var(--font-display)] text-sm font-semibold">
					On the Horizon
				</h3>
			</div>
			<div className="px-5 py-4 space-y-4">
				{obj.intro && <p className="text-sm text-muted-foreground">{obj.intro}</p>}
				{opportunities.map((opp, i) => (
					<div key={i} className="rounded-md border border-border bg-muted/10 p-4">
						<div className="font-medium text-sm text-violet-300">{opp.title}</div>
						<p className="text-sm text-foreground/70 mt-2">{opp.whats_possible}</p>
						<p className="text-xs text-muted-foreground mt-2">{opp.how_to_try}</p>
						{opp.copyable_prompt && (
							<>
								<pre className="mt-2 p-2 rounded bg-muted/30 text-xs font-[family-name:var(--font-mono)] text-foreground/70 whitespace-pre-wrap break-all">
									{opp.copyable_prompt}
								</pre>
								<CopyButton text={opp.copyable_prompt} className="mt-1" />
							</>
						)}
					</div>
				))}
			</div>
		</div>
	);
}

// ── Token/Cost Section ──────────────────────────────────────────────────

function TokenSection({
	data,
	metrics,
}: {
	data: unknown;
	metrics: InsightReport["metrics"];
}) {
	if (!data && !metrics?.cost) return null;

	// If we have structured V2 data
	if (data && typeof data === "object" && !Array.isArray(data)) {
		const obj = data as Record<string, unknown>;
		const summary = hasText(obj.summary) ? obj.summary : undefined;
		const costMetrics = obj.metrics as
			| {
					total_cost_usd?: number;
					cost_per_session?: number;
					cache_efficiency_pct?: number;
			  }
			| undefined;
		const opportunities = obj.opportunities as
			| { title: string; description: string; estimated_savings: string }[]
			| undefined;
		const hasCostMetrics = Boolean(
			costMetrics &&
				(hasPositiveNumber(costMetrics.total_cost_usd) ||
					hasPositiveNumber(costMetrics.cost_per_session) ||
					hasPositiveNumber(costMetrics.cache_efficiency_pct))
		);
		const hasOpportunities = Boolean(opportunities && opportunities.length > 0);
		if (!summary && !hasCostMetrics && !hasOpportunities) return null;

		return (
			<div className="rounded-lg border border-border bg-card">
				<div className="flex items-center gap-2 px-5 py-3 border-b border-border">
					<DollarSign className="h-4 w-4 text-primary-accent" />
					<h3 className="font-[family-name:var(--font-display)] text-sm font-semibold">
						Cost & Token Efficiency
					</h3>
				</div>
				<div className="px-5 py-4 space-y-4">
					{summary && <p className="text-sm text-foreground/80">{summary}</p>}

					{hasCostMetrics && costMetrics && (
						<div className="grid grid-cols-3 gap-3">
							<div className="text-center p-2 rounded bg-muted/30">
								<div className="text-lg font-bold">
									${Number(costMetrics.total_cost_usd || 0).toFixed(4)}
								</div>
								<div className="text-xs text-muted-foreground">total cost</div>
							</div>
							<div className="text-center p-2 rounded bg-muted/30">
								<div className="text-lg font-bold">
									${Number(costMetrics.cost_per_session || 0).toFixed(4)}
								</div>
								<div className="text-xs text-muted-foreground">per session</div>
							</div>
							<div className="text-center p-2 rounded bg-muted/30">
								<div className="text-lg font-bold">
									{Number(costMetrics.cache_efficiency_pct || 0).toFixed(0)}%
								</div>
								<div className="text-xs text-muted-foreground">
									cache efficiency
								</div>
							</div>
						</div>
					)}

					{opportunities && opportunities.length > 0 && (
						<div className="space-y-2">
							<div className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
								Optimization Opportunities
							</div>
							{opportunities.map((opp, i) => (
								<div
									key={i}
									className="flex gap-3 text-sm p-2 rounded bg-muted/20"
								>
									<span className="text-primary-accent font-semibold shrink-0">
										{i + 1}.
									</span>
									<div>
										<span className="font-medium">{opp.title}</span>
										<span className="text-foreground/60">
											{" "}
											— {opp.description}
										</span>
										{opp.estimated_savings && (
											<span className="text-success ml-1">
												({opp.estimated_savings})
											</span>
										)}
									</div>
								</div>
							))}
						</div>
					)}
				</div>
			</div>
		);
	}

	// Fallback: render from metrics.cost
	if (metrics?.cost && metrics.cost.total_cost_usd > 0) {
		const cost = metrics.cost;
		return (
			<div className="rounded-lg border border-border bg-card">
				<div className="flex items-center gap-2 px-5 py-3 border-b border-border">
					<DollarSign className="h-4 w-4 text-primary-accent" />
					<h3 className="font-[family-name:var(--font-display)] text-sm font-semibold">
						Cost Summary
					</h3>
				</div>
				<div className="px-5 py-4">
					<div className="grid grid-cols-2 md:grid-cols-4 gap-3">
						<MetricCard
							label="Total Cost"
							value={`$${cost.total_cost_usd.toFixed(4)}`}
							icon={DollarSign}
						/>
						<MetricCard
							label="Per Session"
							value={`$${cost.avg_cost_per_session.toFixed(4)}`}
							icon={DollarSign}
						/>
						<MetricCard
							label="Cache Efficiency"
							value={`${(cost.cache_efficiency_ratio * 100).toFixed(0)}%`}
							icon={Database}
						/>
						<MetricCard
							label="P90 Session"
							value={`$${cost.p90_session_cost.toFixed(4)}`}
							icon={DollarSign}
						/>
					</div>
				</div>
			</div>
		);
	}

	// V1 bullets fallback
	if (Array.isArray(data) || typeof data === "string") {
		return (
			<GenericBulletSection
				title="Token Optimization"
				icon={DollarSign}
				items={data}
			/>
		);
	}

	return null;
}

// ── User Experience Section ─────────────────────────────────────────────

function UserExperienceSection({ data }: { data: unknown }) {
	if (!data) return null;

	if (Array.isArray(data) || typeof data === "string") {
		return (
			<GenericBulletSection title="User Experience" icon={Users} items={data} />
		);
	}

	const obj = data as Record<string, unknown>;
	const narrative = obj.narrative as string | undefined;
	const signals = obj.signals as
		| { signal: string; interpretation: string }[]
		| undefined;
	const satisfaction = obj.satisfaction_indicators as
		| Record<string, string>
		| undefined;

	return (
		<div className="rounded-lg border border-border bg-card">
			<div className="flex items-center gap-2 px-5 py-3 border-b border-border">
				<Users className="h-4 w-4 text-primary-accent" />
				<h3 className="font-[family-name:var(--font-display)] text-sm font-semibold">
					User Experience
				</h3>
			</div>
			<div className="px-5 py-4 space-y-4">
				{narrative && (
					<p className="text-sm leading-relaxed text-foreground/80">
						{narrative}
					</p>
				)}

				{satisfaction && (
					<div className="flex flex-wrap gap-4 py-2 px-3 rounded bg-muted/20">
						{satisfaction.completion_rate &&
							satisfaction.completion_rate !== "N/A" && (
								<div className="text-center">
									<div className="text-sm font-bold">
										{satisfaction.completion_rate}
									</div>
									<div className="text-xs text-muted-foreground">
										completion
									</div>
								</div>
							)}
						{satisfaction.interruption_rate &&
							satisfaction.interruption_rate !== "N/A" && (
								<div className="text-center">
									<div className="text-sm font-bold">
										{satisfaction.interruption_rate}
									</div>
									<div className="text-xs text-muted-foreground">
										interruptions
									</div>
								</div>
							)}
					</div>
				)}

				{signals && signals.length > 0 && (
					<div className="space-y-2">
						{signals.map((s, i) => (
							<div key={i} className="text-sm flex gap-2">
								<span className="text-muted-foreground shrink-0">•</span>
								<div>
									<span className="font-medium">{s.signal}</span>
									{s.interpretation && (
										<span className="text-muted-foreground">
											{" "}
											— {s.interpretation}
										</span>
									)}
								</div>
							</div>
						))}
					</div>
				)}
			</div>
		</div>
	);
}

// ── Version Comparison Section ──────────────────────────────────────────

function VersionComparisonSection({ data }: { data: unknown }) {
	if (!data || typeof data !== "object" || Array.isArray(data)) return null;

	const obj = data as Record<string, unknown>;
	if (obj.has_comparison === false) return null;

	const summary = hasText(obj.summary) ? obj.summary : undefined;
	const confidence = hasText(obj.confidence) ? obj.confidence : undefined;
	const changes = (obj.changes as
		| {
				metric?: string;
				direction?: string;
				prior_value?: string;
				current_value?: string;
				attribution?: string;
				risk?: string;
				evidence?: string;
		  }[]
		| undefined) ?? [];

	if (!summary && !confidence && changes.length === 0) return null;

	return (
		<div className="rounded-lg border border-border bg-card">
			<div className="flex items-center gap-2 px-5 py-3 border-b border-border">
				<TrendingUp className="h-4 w-4 text-primary-accent" />
				<h3 className="text-sm font-semibold">Version Comparison</h3>
			</div>
			<div className="px-5 py-4 space-y-4">
				{summary && <p className="text-sm text-foreground/80">{summary}</p>}
				{confidence && (
					<div className="text-xs text-muted-foreground">
						Confidence: <span className="font-medium">{confidence}</span>
					</div>
				)}
				{changes.length > 0 && (
					<div className="space-y-3">
						{changes.slice(0, 8).map((change, i) => {
							const direction = change.direction || "stable";
							const directionClass =
								direction === "improved"
									? "text-success"
									: direction === "degraded"
										? "text-destructive"
										: "text-muted-foreground";
							return (
								<div key={i} className="rounded-md border border-border bg-muted/10 p-3">
									<div className="flex flex-wrap items-center gap-2 text-sm">
										<span className="font-medium">
											{formatInsightLabel(change.metric || "change")}
										</span>
										<span className={directionClass}>{direction}</span>
									</div>
									{(change.prior_value || change.current_value) && (
										<div className="mt-1 text-sm text-muted-foreground">
											{change.prior_value || "?"} → {change.current_value || "?"}
										</div>
									)}
									{(change.attribution || change.risk) && (
										<div className="mt-2 flex flex-wrap gap-2 text-xs text-muted-foreground">
											{change.attribution && <span>Attribution: {change.attribution}</span>}
											{change.risk && <span>Risk: {change.risk}</span>}
										</div>
									)}
									{change.evidence && (
										<p className="mt-2 text-sm text-foreground/70">{change.evidence}</p>
									)}
								</div>
							);
						})}
					</div>
				)}
			</div>
		</div>
	);
}

// ── Regression Section ──────────────────────────────────────────────────

function RegressionSection({
	data,
	regressions,
}: {
	data: unknown;
	regressions?: unknown[];
}) {
	// Handle structured regression data from narrative
	if (data && typeof data === "object" && !Array.isArray(data)) {
		const obj = data as Record<string, unknown>;
		if (obj.has_previous_data === false) return null;
		const changes = obj.changes as
			| {
					metric: string;
					direction: string;
					previous_value: string;
					current_value: string;
					magnitude_pct: number;
					significance: string;
			  }[]
			| undefined;
		if (!changes || changes.length === 0) return null;

		return (
			<div className="rounded-lg border border-border bg-card">
				<div className="flex items-center gap-2 px-5 py-3 border-b border-border">
					<TrendingDown className="h-4 w-4 text-primary-accent" />
					<h3 className="font-[family-name:var(--font-display)] text-sm font-semibold">
						Changes vs Previous Period
					</h3>
				</div>
				<div className="px-5 py-4 space-y-2">
					{obj.summary ? (
						<p className="text-sm text-muted-foreground mb-3">
							{String(obj.summary)}
						</p>
					) : null}
					{changes.map((c, i) => (
						<div key={i} className="flex items-center gap-3 text-sm">
							{c.direction === "improved" ? (
								<ArrowUpRight className="h-4 w-4 text-success shrink-0" />
							) : c.direction === "degraded" ? (
								<ArrowDownRight className="h-4 w-4 text-destructive shrink-0" />
							) : (
								<span className="w-4 h-4 shrink-0" />
							)}
							<span className="flex-1">
								<span className="font-medium">
									{c.metric.replace(/_/g, " ")}
								</span>{" "}
								<span
									className={
										c.direction === "improved"
											? "text-success"
											: c.direction === "degraded"
												? "text-destructive"
												: "text-muted-foreground"
									}
								>
									{c.direction} {Math.abs(c.magnitude_pct).toFixed(1)}%
								</span>
								<span className="text-muted-foreground ml-2">
									({c.previous_value} → {c.current_value})
								</span>
							</span>
							{c.significance && (
								<span
									className={`text-xs px-2 py-0.5 rounded-full ${
										c.significance === "meaningful"
											? "bg-primary-accent/20 text-primary-accent"
											: "bg-muted text-muted-foreground"
									}`}
								>
									{c.significance}
								</span>
							)}
						</div>
					))}
				</div>
			</div>
		);
	}

	// Handle V1 regressions array from metrics
	if (regressions && regressions.length > 0) {
		return (
			<div className="rounded-lg border border-border bg-card">
				<div className="flex items-center gap-2 px-5 py-3 border-b border-border">
					<TrendingDown className="h-4 w-4 text-primary-accent" />
					<h3 className="font-[family-name:var(--font-display)] text-sm font-semibold">
						Changes vs Previous Period
					</h3>
				</div>
				<div className="px-5 py-4 space-y-2">
					{(
						regressions as {
							metric: string;
							direction: string;
							magnitude: number;
							current_value: number;
							previous_value: number;
							severity: string;
						}[]
					).map((r, i) => (
						<div key={i} className="flex items-center gap-3 text-sm">
							{r.direction === "improved" ? (
								<ArrowUpRight className="h-4 w-4 text-success shrink-0" />
							) : (
								<ArrowDownRight className="h-4 w-4 text-destructive shrink-0" />
							)}
							<span className="flex-1">
								<span className="font-medium">
									{r.metric.replace(/_/g, " ")}
								</span>{" "}
								<span
									className={
										r.direction === "improved"
											? "text-success"
											: "text-destructive"
									}
								>
									{r.direction} {Math.abs(r.magnitude).toFixed(1)}%
								</span>
								<span className="text-muted-foreground ml-2">
									({r.previous_value} → {r.current_value})
								</span>
							</span>
						</div>
					))}
				</div>
			</div>
		);
	}

	// V1 bullets
	if (Array.isArray(data) || typeof data === "string") {
		const items = Array.isArray(data) ? data : [data];
		if (
			items.length === 1 &&
			typeof items[0] === "string" &&
			items[0].includes("No previous")
		)
			return null;
		return (
			<GenericBulletSection
				title="Regression Detection"
				icon={TrendingDown}
				items={data}
			/>
		);
	}

	return null;
}

// ── Fun Ending ──────────────────────────────────────────────────────────

function FunEnding({ data }: { data: unknown }) {
	if (!data) return null;

	if (typeof data === "string") {
		return (
			<div className="rounded-lg border border-border bg-muted/10 p-5">
				<div className="flex items-center gap-2 mb-2">
					<Sparkles className="h-4 w-4 text-primary-accent" />
					<h3 className="font-[family-name:var(--font-display)] text-sm font-semibold">
						Notable Moment
					</h3>
				</div>
				<p className="text-sm leading-relaxed text-muted-foreground italic">
					{data}
				</p>
			</div>
		);
	}

	const obj = data as { headline?: string; detail?: string };
	if (!obj.headline && !obj.detail) return null;

	return (
		<div className="rounded-lg border border-border bg-muted/10 p-5 text-center">
			<Sparkles className="h-5 w-5 text-primary-accent mx-auto mb-2" />
			{obj.headline && (
				<div className="font-[family-name:var(--font-display)] text-sm font-semibold mb-1">
					{obj.headline}
				</div>
			)}
			{obj.detail && (
				<p className="text-sm text-muted-foreground italic">{obj.detail}</p>
			)}
		</div>
	);
}

// ── Tools Table ─────────────────────────────────────────────────────────

function ToolsTable({
	tools,
}: {
	tools: { name: string; invocations: string; errors: string }[] | undefined;
}) {
	if (!tools || tools.length === 0) return null;

	return (
		<div className="rounded-lg border border-border bg-card overflow-hidden">
			<div className="flex items-center gap-2 px-5 py-3 border-b border-border">
				<Wrench className="h-4 w-4 text-primary-accent" />
				<h3 className="font-[family-name:var(--font-display)] text-sm font-semibold">
					Top Tools
				</h3>
			</div>
			<div className="overflow-x-auto">
				<table className="w-full text-sm">
					<thead>
						<tr className="border-b border-border text-xs text-muted-foreground uppercase tracking-wider">
							<th className="text-left px-5 py-2 font-medium">Tool</th>
							<th className="text-right px-5 py-2 font-medium">Calls</th>
							<th className="text-right px-5 py-2 font-medium">Errors</th>
							<th className="text-right px-5 py-2 font-medium">Error Rate</th>
						</tr>
					</thead>
					<tbody>
						{tools.slice(0, 10).map((tool) => {
							const invocations = Number(tool.invocations) || 0;
							const errors = Number(tool.errors) || 0;
							const errorRate =
								invocations > 0
									? ((errors / invocations) * 100).toFixed(1)
									: "0.0";
							return (
								<tr
									key={tool.name}
									className="border-b border-border last:border-0 hover:bg-muted/30"
								>
									<td className="px-5 py-2.5 font-[family-name:var(--font-mono)] text-xs">
										{tool.name}
									</td>
									<td className="px-5 py-2.5 text-right tabular-nums">
										{invocations.toLocaleString()}
									</td>
									<td className="px-5 py-2.5 text-right tabular-nums">
										{errors}
									</td>
									<td className="px-5 py-2.5 text-right tabular-nums">
										<span
											className={
												Number(errorRate) > 10
													? "text-destructive font-medium"
													: ""
											}
										>
											{errorRate}%
										</span>
									</td>
								</tr>
							);
						})}
					</tbody>
				</table>
			</div>
		</div>
	);
}

// ── Error Categories ────────────────────────────────────────────────────

function ErrorCategories({
	toolErrors,
}: {
	toolErrors:
		| { total_categorized: number; categories: Record<string, number> }
		| undefined;
}) {
	if (!toolErrors || toolErrors.total_categorized === 0) return null;

	const categories = Object.entries(toolErrors.categories).sort(
		([, a], [, b]) => b - a,
	);
	const labelMap: Record<string, string> = {
		command_failed: "Command Failed",
		user_rejected: "User Rejected",
		edit_failed: "Edit Failed",
		file_changed: "File Changed",
		file_too_large: "File Too Large",
		file_not_found: "File Not Found",
		timeout: "Timeout",
		permission_denied: "Permission Denied",
		other: "Other",
	};

	return (
		<div className="rounded-lg border border-border bg-card overflow-hidden">
			<div className="flex items-center gap-2 px-5 py-3 border-b border-border">
				<Shield className="h-4 w-4 text-primary-accent" />
				<h3 className="font-[family-name:var(--font-display)] text-sm font-semibold">
					Error Categories ({toolErrors.total_categorized} total)
				</h3>
			</div>
			<div className="px-5 py-4 space-y-2">
				{categories.map(([cat, count]) => {
					const pct = ((count / toolErrors.total_categorized) * 100).toFixed(0);
					return (
						<div key={cat} className="flex items-center gap-3 text-sm">
							<span className="w-32 truncate text-muted-foreground">
								{labelMap[cat] ?? cat}
							</span>
							<div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
								<div
									className="h-full bg-destructive/60 rounded-full"
									style={{ width: `${pct}%` }}
								/>
							</div>
							<span className="w-16 text-right tabular-nums text-xs">
								{count} ({pct}%)
							</span>
						</div>
					);
				})}
			</div>
		</div>
	);
}

// ── Generic Bullet fallback (V1 compatibility) ──────────────────────────

function GenericBulletSection({
	title,
	icon: Icon,
	items,
	color,
	numbered,
}: {
	title: string;
	icon: React.ComponentType<{ className?: string }>;
	items: unknown[] | string | undefined;
	color?: string;
	numbered?: boolean;
}) {
	if (!items) return null;
	const list = Array.isArray(items) ? items : [items];
	if (list.length === 0) return null;

	return (
		<div className="rounded-lg border border-border bg-card">
			<div className="flex items-center gap-2 px-5 py-3 border-b border-border">
				<Icon className="h-4 w-4 text-primary-accent" />
				<h3 className="font-[family-name:var(--font-display)] text-sm font-semibold">
					{title}
				</h3>
			</div>
			<div className="px-5 py-4">
				<ul className="space-y-2">
					{list.map((item, i) => (
						<li key={i} className="flex gap-2 text-sm">
							{numbered ? (
								<span className="text-primary-accent font-semibold mt-0.5 shrink-0">
									{i + 1}.
								</span>
							) : (
								<span
									className={`mt-0.5 shrink-0 ${color || "text-muted-foreground"}`}
								>
									•
								</span>
							)}
							<span>{formatItem(item)}</span>
						</li>
					))}
				</ul>
			</div>
		</div>
	);
}

function formatItem(item: unknown): string {
	if (typeof item === "string") return item;
	if (item && typeof item === "object") {
		const obj = item as Record<string, unknown>;
		if (obj.action) {
			const prefix = obj.type ? `[${obj.type}] ` : "";
			const suffix = obj.priority ? ` (${obj.priority})` : "";
			return `${prefix}${obj.action}${suffix}`;
		}
		return (
			Object.values(obj)
				.filter((v) => typeof v === "string")
				.join(" — ") || JSON.stringify(obj)
		);
	}
	return String(item);
}

// ── Main Report Content ─────────────────────────────────────────────────

function ReportContent({ report }: { report: InsightReport }) {
	const metrics = report.metrics;
	const narrative = report.narrative;
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	const rich = (metrics as any)?.rich as Record<string, unknown> | undefined;
	// facets_summary is stored inside aggregated_data by the batch job
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	const aggData = (report as any)?.aggregated_data as Record<string, unknown> | undefined;
	const facetsSummary = (aggData?.facets_summary || report.facets_summary) as Record<string, unknown> | undefined;

	const totalSessions = Number(metrics?.overview?.total_sessions) || 0;
	const uniqueUsers = Number(metrics?.overview?.unique_users) || 0;
	const inputTokens = Number(rich?.total_input_tokens || metrics?.tokens?.total_input_tokens) || 0;
	const outputTokens = Number(rich?.total_output_tokens || metrics?.tokens?.total_output_tokens) || 0;
	const cacheReadTokens = Number(rich?.total_cache_read_tokens || metrics?.tokens?.total_cache_read_tokens) || 0;
	const cacheWriteTokens = Number(rich?.total_cache_write_tokens || metrics?.tokens?.total_cache_write_tokens) || 0;
	const cacheHitRate = typeof rich?.cache_hit_rate_pct === "number" ? rich.cache_hit_rate_pct : (cacheReadTokens + inputTokens + cacheWriteTokens > 0 ? (cacheReadTokens / (cacheReadTokens + inputTokens + cacheWriteTokens)) * 100 : null);
	const totalCost = Number(rich?.total_cost_usd || metrics?.cost?.total_cost_usd) || 0;
	const activeHours = Number(rich?.active_hours) || 0;
	const daysActive = Number(rich?.days_active) || 0;
	const totalMessages = Number(rich?.total_messages) || 0;
	const linesAdded = Number(rich?.lines_added) || 0;
	const linesRemoved = Number(rich?.lines_removed) || 0;
	const gitCommits = Number(rich?.git_commits) || 0;
	const gitPushes = Number(rich?.git_pushes) || 0;
	const filesModified = Number(rich?.files_modified) || 0;
	const toolErrors = Number(rich?.tool_errors || metrics?.errors?.error_events) || 0;
	const interruptions = Number(rich?.interruptions) || 0;
	const subagentSessions = Number(rich?.subagent_sessions) || 0;
	const mcpSessions = Number(rich?.mcp_sessions) || 0;
	const totalCredits = Number(rich?.total_credits) || 0;
	const sessionsWithTokens = Number(rich?.sessions_with_tokens) || 0;
	const sessionsWithCredits = Number(rich?.sessions_with_credits) || 0;
	const toolCalls = Number(metrics?.errors?.total_tool_calls) || 0;
	const showTokenSection = sessionsWithTokens > 0 || totalCost > 0;
	const topTools = (rich?.top_tools || []) as [string, number][];
	const topLanguages = (rich?.top_languages || []) as [string, number][];
	const toolErrorCats = (rich?.tool_error_categories || {}) as Record<string, number>;
	const canonicalDirty = (rich?.canonical_dirty_summary || {}) as Record<string, number>;

	const fmt = (n: number) => {
		if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
		if (n >= 1_000) return `${(n / 1_000).toFixed(0)}k`;
		return n.toString();
	};

	const fmtHours = (h: number) => h < 1 ? `${Math.round(h * 60)}m` : `${h.toFixed(1)}h`;

	return (
		<div className="space-y-6">
			{/* At a Glance (4-panel executive summary) */}
			<AtAGlance data={narrative?.at_a_glance} />

			{/* Stats Overview (matching pi /insights layout) */}
			<div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
				<MetricCard label="Sessions" value={totalSessions} icon={Zap} subtext={daysActive > 0 ? `${daysActive} active days` : uniqueUsers > 1 ? `${uniqueUsers} users` : undefined} />
				<MetricCard label="Messages" value={fmt(totalMessages)} icon={Database} subtext={totalSessions > 0 ? `${(totalMessages / totalSessions).toFixed(1)} per session` : undefined} />
				<MetricCard label="Active Time" value={fmtHours(activeHours)} icon={Timer} />
				{sessionsWithTokens > 0 && <MetricCard label="Tokens In" value={fmt(inputTokens)} icon={Database} />}
				{sessionsWithTokens > 0 && <MetricCard label="Tokens Out" value={fmt(outputTokens)} icon={Database} />}
				{cacheReadTokens > 0 && <MetricCard label="Cache Read" value={fmt(cacheReadTokens)} icon={Database} />}
				{cacheWriteTokens > 0 && <MetricCard label="Cache Write" value={fmt(cacheWriteTokens)} icon={Database} />}
				{cacheHitRate != null && <MetricCard label="Cache Efficiency" value={`${cacheHitRate.toFixed(1)}%`} icon={Zap} subtext={`${fmt(cacheReadTokens)} tokens saved`} />}
				{totalCost > 0 && <MetricCard label="Total Cost" value={`$${totalCost.toFixed(2)}`} icon={DollarSign} subtext={totalSessions > 0 ? `$${(totalCost / totalSessions).toFixed(2)}/session` : undefined} />}
				{sessionsWithCredits > 0 && <MetricCard label="Credits Used" value={totalCredits.toFixed(2)} icon={Coins} subtext={totalSessions > 0 ? `${(totalCredits / totalSessions).toFixed(2)}/session` : undefined} />}
				<MetricCard label="Lines Added" value={fmt(linesAdded)} icon={Zap} />
				<MetricCard label="Lines Removed" value={fmt(linesRemoved)} icon={AlertTriangle} />
				<MetricCard label="Git Commits" value={gitCommits} icon={Wrench} subtext={gitPushes > 0 ? `${gitPushes} pushes` : undefined} />
				<MetricCard label="Files Modified" value={fmt(filesModified)} icon={Database} />
				<MetricCard label="Tool Errors" value={toolErrors} icon={AlertTriangle} />
				<MetricCard label="Interruptions" value={interruptions} icon={AlertTriangle} />
				{subagentSessions > 0 && <MetricCard label="Subagent Sessions" value={subagentSessions} icon={Users} />}
				{mcpSessions > 0 && <MetricCard label="MCP Sessions" value={mcpSessions} icon={Wrench} />}
			</div>

			{(canonicalDirty.canonical_sessions || canonicalDirty.dirty_sessions) && (
				<div className="rounded-lg border border-border bg-card p-4">
					<div className="text-xs font-medium uppercase tracking-wider text-muted-foreground mb-3">Canonical vs Dirty Installs</div>
					<div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
						<div><div className="text-lg font-bold tabular-nums">{canonicalDirty.canonical_sessions ?? 0}</div><div className="text-xs text-muted-foreground">canonical sessions</div></div>
						<div><div className="text-lg font-bold tabular-nums">{canonicalDirty.dirty_sessions ?? 0}</div><div className="text-xs text-muted-foreground">dirty sessions</div></div>
						<div><div className="text-lg font-bold tabular-nums">{canonicalDirty.canonical_users ?? 0}</div><div className="text-xs text-muted-foreground">canonical users</div></div>
						<div><div className="text-lg font-bold tabular-nums">{canonicalDirty.dirty_users ?? 0}</div><div className="text-xs text-muted-foreground">dirty users</div></div>
					</div>
				</div>
			)}

			{/* Top Tools + Languages (bar charts like pi /insights) */}
			{(topTools.length > 0 || topLanguages.length > 0) && (
				<div className="grid grid-cols-1 md:grid-cols-2 gap-4">
					{topTools.length > 0 && (
						<div className="rounded-lg border border-border bg-card p-4">
							<h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">Top Tools</h3>
							{topTools.slice(0, 10).map(([name, count]) => {
								const max = topTools[0]?.[1] || 1;
								return (
									<div key={name} className="flex items-center gap-2 mb-1.5 text-xs">
										<span className="w-32 shrink-0 text-muted-foreground truncate capitalize">{name}</span>
										<div className="flex-1 h-2.5 bg-muted/30 rounded-full overflow-hidden">
											<div className="h-full bg-primary-accent rounded-full transition-all" style={{ width: `${(count / max) * 100}%` }} />
										</div>
										<span className="w-12 text-right text-muted-foreground tabular-nums font-mono">{fmt(count)}</span>
									</div>
								);
							})}
						</div>
					)}
					{topLanguages.length > 0 && (
						<div className="rounded-lg border border-border bg-card p-4">
							<h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">Languages</h3>
							{topLanguages.map(([name, count]) => {
								const max = topLanguages[0]?.[1] || 1;
								return (
									<div key={name} className="flex items-center gap-2 mb-1.5 text-xs">
										<span className="w-32 shrink-0 text-muted-foreground truncate">{name}</span>
										<div className="flex-1 h-2.5 bg-muted/30 rounded-full overflow-hidden">
											<div className="h-full bg-teal-400 rounded-full transition-all" style={{ width: `${(count / max) * 100}%` }} />
										</div>
										<span className="w-12 text-right text-muted-foreground tabular-nums font-mono">{fmt(count)}</span>
									</div>
								);
							})}
						</div>
					)}
				</div>
			)}

			{/* Tool Error Categories */}
			{Object.keys(toolErrorCats).length > 0 && (
				<div className="rounded-lg border border-border bg-card p-4">
					<h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">Tool Error Breakdown</h3>
					{Object.entries(toolErrorCats).sort((a, b) => b[1] - a[1]).map(([cat, count]) => {
						const max = Object.values(toolErrorCats).reduce((a, b) => Math.max(a, b), 1);
						return (
							<div key={cat} className="flex items-center gap-2 mb-1.5 text-xs">
								<span className="w-32 shrink-0 text-muted-foreground truncate capitalize">{cat.replace(/_/g, " ")}</span>
								<div className="flex-1 h-2.5 bg-muted/30 rounded-full overflow-hidden">
									<div className="h-full bg-destructive/60 rounded-full" style={{ width: `${(count / max) * 100}%` }} />
								</div>
								<span className="w-12 text-right text-muted-foreground tabular-nums font-mono">{count}</span>
							</div>
						);
					})}
				</div>
			)}

			{/* Facets Charts (Goals, Outcomes, Satisfaction, Friction) */}
			<FacetsCharts facets={facetsSummary} />

			{/* Project Areas */}
			<ProjectAreas data={narrative?.what_they_work_on} />

			{/* Usage Patterns */}
			<UsagePatterns data={narrative?.usage_patterns} />

			{/* Interaction Style */}
			<InteractionStyleSection data={narrative?.interaction_style} />

			{/* What Works (Impressive Workflows) */}
			<StrengthsSection data={narrative?.what_works} />

			{/* Friction Analysis */}
			<FrictionSection data={narrative?.friction_analysis} />

			{/* Suggestions */}
			<SuggestionsSection
				data={narrative?.suggestions}
				report={report}
				registryMatch={narrative?.registry_match as RegistryMatchSummary | undefined}
			/>

			{/* On the Horizon */}
			<OnTheHorizon data={narrative?.on_the_horizon} />

			{/* Cost and token efficiency needs actual token or pay as you go cost data. */}
			{showTokenSection && <TokenSection data={narrative?.usage_cost_analysis || narrative?.token_optimization} metrics={metrics} />}

			<VersionComparisonSection data={narrative?.version_comparison} />

			{/* Regression Detection */}
			<RegressionSection
				data={narrative?.regression_detection}
				regressions={narrative?.regressions}
			/>

			{/* Fun Ending */}
			<FunEnding data={narrative?.fun_ending} />

			{/* No narrative fallback */}
			{!narrative && metrics && (
				<div className="rounded-lg border border-border bg-muted/30 p-5 text-center">
					<p className="text-sm text-muted-foreground">
						Narrative analysis unavailable. Configure insights models in Settings to enable
						LLM-powered analysis.
					</p>
				</div>
			)}
		</div>
	);
}

// ── Page Component ──────────────────────────────────────────────────────

export default function InsightReportPage() {
	const { agentId, reportId } = useParams({ from: "/_authed/agents/$agentId/insights/$reportId" });
	const router = useRouter();
	const { data: report, isLoading, isError } = useInsightReport(agentId, reportId);

	return (
		<>
			<PageHeader
				title="Insight Report"
				breadcrumbs={[
					{ label: "Agentic Resources", href: "/resources/agents" },
					{ label: `Insight report · ${agentId}` },
				]}
				actionButtonsRight={
					<div className="flex items-center gap-2">
						{report?.status === "completed" && (
							<Button
								variant="outline"
								size="sm"
								className="gap-1.5"
								onClick={() => insights.exportHtml(agentId, reportId)}
							>
								<Download className="h-4 w-4" /> Export HTML
							</Button>
						)}
						<Button variant="ghost" size="sm" className="gap-1.5" onClick={() => router.history.back()}>
							<ArrowLeft className="h-4 w-4" /> Back
						</Button>
					</div>
				}
			/>

			<div className="p-4 sm:p-6 w-full">
				{isLoading && (
					<div className="flex items-center justify-center py-20">
						<Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
					</div>
				)}

				{isError && <ErrorState message="Failed to load report" />}

				{report && (
					<div className="space-y-6">
						{/* Header with status and metadata */}
						<div className="flex items-center justify-between">
							<StatusIndicator status={report.status} />
							<div className="text-xs text-muted-foreground space-x-3">
								{report.agent_version && <span>Version v{report.agent_version}</span>}
								{report.comparison_agent_version && <span>Compared to v{report.comparison_agent_version}</span>}
								<span>
									{new Date(report.period_start).toLocaleDateString()} -{" "}
									{new Date(report.period_end).toLocaleDateString()}
								</span>
								{report.sessions_analyzed > 0 && (
									<span>{report.sessions_analyzed} sessions analyzed</span>
								)}
							</div>
						</div>

						{/* Loading state */}
						{(report.status === "pending" || report.status === "running") && (
							<div className="flex flex-col items-center justify-center py-16 gap-4">
								<Loader2 className="h-8 w-8 animate-spin text-primary-accent" />
								<div className="w-full max-w-md space-y-2">
									<div className="h-2 rounded-full bg-muted overflow-hidden">
										<div className="h-full bg-primary-accent transition-all" style={{ width: `${report.progress_percent ?? 0}%` }} />
									</div>
									<p className="text-sm text-center text-muted-foreground">
										{report.progress_message || (report.status === "pending" ? "Waiting in queue..." : "Computing metrics and generating analysis...")}
									</p>
									{report.progress_phase && (
										<p className="text-xs text-center text-muted-foreground font-mono">
											{report.progress_phase.replace(/_/g, " ")} · {report.progress_percent ?? 0}%
										</p>
									)}
								</div>
							</div>
						)}

						{/* Error state */}
						{report.status === "failed" && (
							<div className="rounded-lg border border-destructive/30 bg-destructive/5 p-5">
								<p className="text-sm font-medium text-destructive">
									Report generation failed
								</p>
								{report.error_message && (
									<p className="text-xs text-muted-foreground mt-1 font-[family-name:var(--font-mono)]">
										{report.error_message}
									</p>
								)}
							</div>
						)}

						{/* Completed: show content */}
						{report.status === "completed" && <ReportContent report={report} />}
					</div>
				)}
			</div>
		</>
	);
}
