// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import React from "react";
import type { InsightReport } from "@/lib/types";

// ── Key points ──────────────────────────────────────────────────────────

export type KeyPointTone = "problem" | "action" | "good" | "info" | "regression" | "discover";

export interface KeyPointDetail {
	label?: string;
	text: string;
	/** Text the reader is meant to paste somewhere, shown monospaced. */
	code?: boolean;
}

export interface KeyPoint {
	id: string;
	tone: KeyPointTone;
	title: string;
	summary: string;
	details: KeyPointDetail[];
}

export const MAX_KEY_POINTS = 5;

export type Obj = Record<string, unknown>;

export function asObj(value: unknown): Obj | undefined {
	return value && typeof value === "object" && !Array.isArray(value) ? (value as Obj) : undefined;
}

export function str(value: unknown): string | undefined {
	return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function objects(value: unknown): Obj[] {
	return Array.isArray(value) ? value.map(asObj).filter((item): item is Obj => !!item) : [];
}

function num(value: unknown): number {
	const n = Number(value);
	return Number.isFinite(n) ? n : 0;
}

/** Report text is shown without em dashes: one joining two clauses becomes a comma. */
export function withoutEmDashes(text: string): string {
	return text.replace(/\s*—\s*/g, ", ");
}

/** The first sentence of model prose, without bold markers, capped so a bullet stays one line or two. */
export function firstSentence(text: string, max = 200): string {
	const plain = text.replace(/\*\*/g, "").replace(/\s+/g, " ").trim();
	const match = plain.match(/^.*?[.!?](?=\s|$)/);
	const sentence = match ? match[0] : plain;
	if (sentence.length <= max) return sentence;
	return `${sentence.slice(0, max - 1).replace(/\s+\S*$/, "")}…`;
}

/** Expanding a point should add information, so a detail that opens with the bullet's own sentence drops it. */
function withoutSummary(text: string, summary: string): string {
	if (!summary || firstSentence(text) !== summary) return text;
	return text.replace(/^.*?[.!?](?=\s|$)\s*/, "");
}

const SEVERITY_RANK: Record<string, number> = { high: 0, medium: 1, low: 2 };

function plural(n: number, word: string): string {
	return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/**
 * At most five things worth knowing about a report, most actionable first.
 *
 * Built from sections the report already holds, so older reports get a summary too. Every point carries its
 * own evidence for the expanded view; a point is skipped rather than padded when the report has nothing for it.
 */
export function buildKeyPoints(report: InsightReport): KeyPoint[] {
	const narrative = asObj(report.narrative) ?? {};
	const glance = asObj(narrative.at_a_glance) ?? {};
	const points: KeyPoint[] = [];

	// 1. The biggest problem, with what happened, an example and what it costs.
	const friction = asObj(narrative.friction_analysis);
	const categories = objects(friction?.categories).sort(
		(a, b) => (SEVERITY_RANK[String(a.severity)] ?? 3) - (SEVERITY_RANK[String(b.severity)] ?? 3),
	);
	const topProblem = categories[0];
	const hindering = str(glance.whats_hindering);
	if (topProblem && str(topProblem.title)) {
		const details: KeyPointDetail[] = [];
		if (str(topProblem.description)) details.push({ label: "What happened", text: str(topProblem.description)! });
		for (const example of (Array.isArray(topProblem.examples) ? topProblem.examples : []).slice(0, 2)) {
			if (str(example)) details.push({ label: "Example", text: str(example)! });
		}
		if (str(topProblem.evidence)) details.push({ label: "Evidence", text: str(topProblem.evidence)! });
		if (str(topProblem.impact)) details.push({ label: "Impact", text: str(topProblem.impact)! });
		const others = categories.slice(1, 3).filter((c) => str(c.title));
		if (others.length) {
			details.push({ label: "Also seen", text: others.map((c) => `${str(c.title)} (${str(c.severity) ?? "unrated"})`).join("; ") });
		}
		points.push({
			id: "problem",
			tone: "problem",
			title: str(topProblem.title)!,
			summary: firstSentence(str(topProblem.description) ?? hindering ?? ""),
			details,
		});
	} else if (hindering) {
		points.push({ id: "problem", tone: "problem", title: "What's holding it back", summary: firstSentence(hindering), details: [{ text: hindering }] });
	}

	// 2. The one change to make first, with the exact text to paste.
	const suggestions = asObj(narrative.suggestions);
	const configs = objects(suggestions?.config_additions);
	const features = objects(suggestions?.features_to_try);
	const patterns = objects(suggestions?.usage_patterns);
	const quickWin = str(glance.quick_win);
	const firstConfig = configs[0];
	if (quickWin || firstConfig) {
		const details: KeyPointDetail[] = [];
		if (quickWin) details.push({ label: "Why this first", text: quickWin });
		if (firstConfig && str(firstConfig.addition)) {
			details.push({ label: `Add to ${str(firstConfig.where) ?? "the system prompt"}`, text: str(firstConfig.addition)!, code: true });
			if (str(firstConfig.why)) details.push({ label: "Reason", text: str(firstConfig.why)! });
		}
		const remaining = configs.length + features.length + patterns.length - (firstConfig ? 1 : 0);
		if (remaining > 0) details.push({ text: `${plural(remaining, "more suggestion")} in Suggestions below; apply them from the top bar.` });
		points.push({
			id: "action",
			tone: "action",
			title: "Do this first",
			summary: firstSentence(quickWin ?? str(firstConfig?.why) ?? str(firstConfig?.addition) ?? ""),
			details,
		});
	}

	// 3. What is working, so it is kept.
	const strengths = objects(asObj(narrative.what_works)?.strengths).filter((s) => str(s.title));
	const working = str(glance.whats_working);
	if (strengths.length || working) {
		const details: KeyPointDetail[] = strengths.slice(0, 3).map((s) => ({ label: str(s.title), text: str(s.description) ?? "" }));
		if (working) details.push({ label: "Overall", text: working });
		points.push({
			id: "good",
			tone: "good",
			title: strengths.length ? str(strengths[0].title)! : "What's working",
			summary: firstSentence(str(strengths[0]?.description) ?? working ?? ""),
			details: details.filter((d) => d.text),
		});
	}

	// 4. Usage and outcomes, straight from the counters.
	const rich = asObj(asObj(report.metrics)?.rich) ?? {};
	const facets = asObj(asObj((report as unknown as Obj).aggregated_data)?.facets_summary) ?? asObj(report.facets_summary) ?? {};
	const sessions = num(rich.total_sessions) || num(asObj(asObj(report.metrics)?.overview)?.total_sessions) || report.sessions_analyzed;
	if (sessions > 0) {
		const outcomes = asObj(facets.outcomes) ?? {};
		const faceted = Object.values(outcomes).reduce<number>((sum, n) => sum + num(n), 0);
		const achieved = num(outcomes.fully_achieved) + num(outcomes.mostly_achieved);
		const cost = num(rich.total_cost_usd);
		const toolErrors = num(rich.tool_errors);
		const parts = [
			faceted > 0 ? `${achieved} of ${faceted} reached their goal` : undefined,
			toolErrors > 0 ? plural(toolErrors, "tool error") : "no tool errors",
			cost > 0 ? `$${cost.toFixed(2)} spent` : undefined,
		].filter(Boolean);
		const details: KeyPointDetail[] = [];
		const hours = num(rich.active_hours);
		details.push({
			label: "Activity",
			text: `${plural(num(rich.total_messages), "message")}, ${hours < 1 ? `${Math.round(hours * 60)} min` : `${hours.toFixed(1)} h`} active over ${plural(num(rich.days_active) || 1, "day")}`,
		});
		if (num(rich.lines_added) || num(rich.lines_removed) || num(rich.git_commits)) {
			details.push({ label: "Code", text: `+${num(rich.lines_added)} / -${num(rich.lines_removed)} lines, ${plural(num(rich.files_modified), "file")}, ${plural(num(rich.git_commits), "commit")}` });
		}
		if (faceted > 0) {
			details.push({ label: "Outcomes", text: Object.entries(outcomes).map(([k, v]) => `${k.replace(/_/g, " ")} ${num(v)}`).join(", ") });
		}
		const frictionTypes = Array.isArray(facets.friction_types) ? (facets.friction_types as unknown[]) : [];
		const topFriction = frictionTypes
			.filter((f): f is [string, number] => Array.isArray(f) && f.length === 2)
			.slice(0, 3)
			.map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`);
		if (topFriction.length) details.push({ label: "Most common friction", text: topFriction.join(", ") });
		if (cost > 0) details.push({ label: "Cost", text: `$${cost.toFixed(2)} total, $${(cost / sessions).toFixed(2)} per session` });
		points.push({
			id: "usage",
			tone: "info",
			title: `${plural(sessions, "session")} analysed`,
			summary: `${parts.join(", ")}.`.replace(/^./, (c) => c.toUpperCase()),
			details,
		});
	}

	// 5. One more thing, only if the report has something worth a bullet.
	const degraded = (Array.isArray(narrative.regressions) ? objects(narrative.regressions) : []).filter((r) => r.direction === "degraded");
	const discovery = asObj(narrative.discovery);
	const discoverySuggestion = discovery?.state === "assessed" ? objects(discovery.suggestions)[0] : undefined;
	const reuse = features.find((f) => asObj(f.component_ref));
	if (degraded.length) {
		const worst = degraded.sort((a, b) => (SEVERITY_RANK[String(a.severity)] ?? 3) - (SEVERITY_RANK[String(b.severity)] ?? 3))[0];
		const metric = String(worst.metric ?? "metric").replace(/_/g, " ");
		points.push({
			id: "regression",
			tone: "regression",
			title: `${metric} got worse`,
			summary: `From ${num(worst.previous_value)} to ${num(worst.current_value)} since the previous report.`,
			details: degraded.map((r) => ({ label: String(r.metric ?? "").replace(/_/g, " "), text: `${num(r.previous_value)} → ${num(r.current_value)} (${str(r.severity) ?? "unrated"} severity)` })),
		});
	} else if (discoverySuggestion && str(discoverySuggestion.suggested_description)) {
		points.push({
			id: "discover",
			tone: "discover",
			title: "Make it easier to find",
			summary: `Suggested description: "${str(discoverySuggestion.suggested_description)}"`,
			details: [
				{ label: "Example search", text: str(discoverySuggestion.illustrative_query) ?? "" },
				{ label: "Why", text: str(discoverySuggestion.rationale) ?? "" },
				{ label: "ARD relevance for that search", text: `${num(discoverySuggestion.relevance_before)} → ${num(discoverySuggestion.relevance_after)} (illustrative, not a search position)` },
			].filter((d) => d.text),
		});
	} else if (reuse) {
		const ref = asObj(reuse.component_ref)!;
		points.push({
			id: "reuse",
			tone: "discover",
			title: `Reuse ${str(ref.qualified_name) ?? str(ref.name) ?? "an existing component"}`,
			summary: firstSentence(str(reuse.one_liner) ?? str(reuse.why_for_you) ?? ""),
			details: [
				{ label: "Why for you", text: str(reuse.why_for_you) ?? "" },
				{ label: "Why it fits", text: str(reuse.match_reason) ?? "" },
			].filter((d) => d.text),
		});
	} else if (categories[1] && str(categories[1].title)) {
		points.push({
			id: "problem-2",
			tone: "problem",
			title: str(categories[1].title)!,
			summary: firstSentence(str(categories[1].description) ?? ""),
			details: [
				{ label: "What happened", text: str(categories[1].description) ?? "" },
				{ label: "Impact", text: str(categories[1].impact) ?? "" },
			].filter((d) => d.text),
		});
	}

	return points
		.filter((p) => p.summary || p.details.length)
		.slice(0, MAX_KEY_POINTS)
		.map((p) => ({
			...p,
			title: withoutEmDashes(p.title),
			summary: withoutEmDashes(p.summary),
			details: p.details
				.map((d) => (d.code ? d : { ...d, text: withoutEmDashes(withoutSummary(d.text, p.summary)) }))
				.filter((d) => d.text),
		}));
}

// ── Section toggles ─────────────────────────────────────────────────────

export const REPORT_SECTION_GROUPS = [
	{ id: "suggestions", label: "Suggestions" },
	{ id: "analysis", label: "Analysis" },
	{ id: "metrics", label: "Metrics" },
	{ id: "charts", label: "Charts" },
	{ id: "cost", label: "Cost & versions" },
	{ id: "outlook", label: "Outlook" },
] as const;

export type ReportSectionGroup = (typeof REPORT_SECTION_GROUPS)[number]["id"];

const SECTION_STORAGE_KEY = "observal.insights.visibleSections";
const DEFAULT_SECTIONS: ReportSectionGroup[] = ["suggestions"];

function readStoredSections(): Set<ReportSectionGroup> {
	try {
		const raw = window.localStorage.getItem(SECTION_STORAGE_KEY);
		if (raw) {
			const known = new Set<string>(REPORT_SECTION_GROUPS.map((g) => g.id));
			const parsed = JSON.parse(raw);
			if (Array.isArray(parsed)) return new Set(parsed.filter((id): id is ReportSectionGroup => known.has(id)));
		}
	} catch {
		// Private windows and blocked storage fall back to the default.
	}
	return new Set(DEFAULT_SECTIONS);
}

/** Which detail groups are shown under the key points; remembered per browser as a reading preference. */
export function useReportSections() {
	const [visible, setVisible] = React.useState<Set<ReportSectionGroup>>(readStoredSections);
	const update = (next: Set<ReportSectionGroup>) => {
		setVisible(next);
		try {
			window.localStorage.setItem(SECTION_STORAGE_KEY, JSON.stringify([...next]));
		} catch {
			// The choice still applies for this visit.
		}
	};
	return {
		visible,
		toggle: (id: ReportSectionGroup) => {
			const next = new Set(visible);
			if (next.has(id)) next.delete(id);
			else next.add(id);
			update(next);
		},
		showAll: () => update(new Set(REPORT_SECTION_GROUPS.map((g) => g.id))),
		hideAll: () => update(new Set()),
	};
}

