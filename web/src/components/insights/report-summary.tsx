// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import React from "react";
import { AlertTriangle, BarChart3, ChevronDown, Search, Target, ThumbsUp, TrendingDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { InsightReport } from "@/lib/types";
import { asObj, buildKeyPoints, REPORT_SECTION_GROUPS, str, withoutEmDashes, type KeyPointTone, type useReportSections } from "@/lib/insight-key-points";

// ── Rich text ───────────────────────────────────────────────────────────

/** Report prose marks key phrases with **double asterisks** and commands with `backticks`; render both instead of the raw marks. */
export function RichText({ text }: { text: string | null | undefined }) {
	// Model output can leave a field out (or return a non-string); show nothing then, like a plain {text} would.
	if (typeof text !== "string" || !text) return null;
	return (
		<>
			{withoutEmDashes(text).split(/(\*\*.+?\*\*|`[^`\n]+`)/).map((segment, i) => {
				if (segment.startsWith("**") && segment.endsWith("**") && segment.length > 4) {
					return <strong key={i} className="font-semibold text-foreground">{segment.slice(2, -2)}</strong>;
				}
				if (segment.startsWith("`") && segment.endsWith("`") && segment.length > 2) {
					return <code key={i} className="break-words rounded bg-muted px-1.5 py-0.5 font-[family-name:var(--font-mono)] text-[0.85em]">{segment.slice(1, -1)}</code>;
				}
				return <React.Fragment key={i}>{segment}</React.Fragment>;
			})}
		</>
	);
}

const TONE_STYLE: Record<KeyPointTone, { icon: React.ComponentType<{ className?: string }>; className: string; label: string }> = {
	problem: { icon: AlertTriangle, className: "text-destructive", label: "Problem" },
	action: { icon: Target, className: "text-primary-accent", label: "Action" },
	good: { icon: ThumbsUp, className: "text-success", label: "Working" },
	info: { icon: BarChart3, className: "text-info", label: "Usage" },
	regression: { icon: TrendingDown, className: "text-warning", label: "Regression" },
	discover: { icon: Search, className: "text-primary-accent", label: "Opportunity" },
};

const HEALTH_STYLE: Record<string, string> = {
	healthy: "bg-success/20 text-success",
	mixed: "bg-warning/20 text-warning",
	concerning: "bg-destructive/20 text-destructive",
};

export function KeyPointsSummary({ report }: { report: InsightReport }) {
	const points = React.useMemo(() => buildKeyPoints(report), [report]);
	const [open, setOpen] = React.useState<Set<string>>(new Set());
	if (!points.length) return null;
	const health = str(asObj(asObj(report.narrative)?.at_a_glance)?.health);
	// Only points with details beyond their summary open; the rest are complete as shown.
	const expandable = points.filter((p) => p.details.length).map((p) => p.id);
	const allOpen = expandable.length > 0 && expandable.every((id) => open.has(id));
	const toggle = (id: string) =>
		setOpen((current) => {
			const next = new Set(current);
			if (next.has(id)) next.delete(id);
			else next.add(id);
			return next;
		});

	return (
		<section aria-labelledby="key-points-heading" className="rounded-lg border border-border bg-card">
			<div className="flex items-center justify-between gap-3 border-b border-border px-5 py-3">
				<div className="flex items-center gap-2">
					<h2 id="key-points-heading" className="font-[family-name:var(--font-display)] text-base font-semibold">
						Key points
					</h2>
					{health && (
						<span className={`rounded-full px-2 py-0.5 text-xs font-medium ${HEALTH_STYLE[health] ?? "bg-muted text-muted-foreground"}`}>{health}</span>
					)}
				</div>
				{expandable.length > 0 && (
					<Button variant="ghost" size="sm" className="h-7 text-xs" onClick={() => setOpen(allOpen ? new Set() : new Set(expandable))}>
						{allOpen ? "Collapse all" : "Expand all"}
					</Button>
				)}
			</div>
			<ul className="divide-y divide-border">
				{points.map((point) => {
					const style = TONE_STYLE[point.tone];
					const Icon = style.icon;
					const hasDetails = point.details.length > 0;
					const isOpen = hasDetails && open.has(point.id);
					// Icon, text and chevron sit in fixed columns so every headline, explanation and detail
					// starts on the same left edge, whatever the icon or the length of the text.
					const row = "grid w-full grid-cols-[1rem_minmax(0,1fr)_1rem] items-start gap-x-3 px-5 py-4 text-left";
					const headline = (
						<>
							<Icon className={`mt-1 h-4 w-4 ${style.className}`} aria-label={style.label} />
							<span className="text-sm">
								<strong className="block font-semibold leading-6 text-foreground">{point.title}</strong>
								{point.summary && <span className="mt-0.5 block leading-6 text-foreground/75">{point.summary}</span>}
							</span>
						</>
					);
					return (
						<li key={point.id}>
							{hasDetails ? (
								<button
									type="button"
									aria-expanded={isOpen}
									aria-controls={`key-point-${point.id}`}
									onClick={() => toggle(point.id)}
									className={`${row} transition-colors hover:bg-muted/40`}
								>
									{headline}
									<ChevronDown className={`mt-1 h-4 w-4 text-muted-foreground transition-transform ${isOpen ? "rotate-180" : ""}`} aria-hidden />
								</button>
							) : (
								<div className={row}>{headline}</div>
							)}
							{isOpen && (
								<div className="grid grid-cols-[1rem_minmax(0,1fr)_1rem] gap-x-3 px-5 pb-5">
									<dl id={`key-point-${point.id}`} className="col-start-2 space-y-3 border-l-2 border-border pl-4 text-sm">
										{point.details.map((detail, i) => (
											<div key={i} className="space-y-1">
												{detail.label && <dt className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{detail.label}</dt>}
												<dd className={detail.code ? "whitespace-pre-wrap rounded-md bg-muted/40 p-3 font-[family-name:var(--font-mono)] text-xs leading-5 text-foreground/80" : "leading-6 text-foreground/80"}>
													{detail.code ? detail.text : <RichText text={detail.text} />}
												</dd>
											</div>
										))}
									</dl>
								</div>
							)}
						</li>
					);
				})}
			</ul>
		</section>
	);
}

// ── Section toggles ──────────────────────────────────────────────────────

export function SectionToggles({ sections }: { sections: ReturnType<typeof useReportSections> }) {
	const allShown = sections.visible.size === REPORT_SECTION_GROUPS.length;
	return (
		<div role="group" aria-label="Report sections" className="flex flex-wrap items-center gap-2">
			<span className="text-xs font-medium text-muted-foreground">More detail:</span>
			{REPORT_SECTION_GROUPS.map((group) => {
				const pressed = sections.visible.has(group.id);
				return (
					<button
						key={group.id}
						type="button"
						aria-pressed={pressed}
						onClick={() => sections.toggle(group.id)}
						className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
							pressed ? "border-primary/50 bg-primary/10 text-foreground" : "border-border text-muted-foreground hover:bg-muted/50"
						}`}
					>
						{group.label}
					</button>
				);
			})}
			<Button variant="ghost" size="sm" className="h-7 text-xs" onClick={allShown ? sections.hideAll : sections.showAll}>
				{allShown ? "Hide all" : "Show all"}
			</Button>
		</div>
	);
}
