// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react";
import { Check, ChevronDown, Loader2, RotateCcw, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useCostOpportunityAction } from "@/hooks/use-api";
import type { CostIntelligence, CostOpportunity, CostRealizedOpportunity } from "@/lib/types";
import { CHART, modelLabel, pct, usd } from "./format";

const STAGES = [
	{ key: "identified_monthly_usd", label: "Identified", color: CHART.spend },
	{ key: "approved_monthly_usd", label: "Approved", color: CHART.found },
	{ key: "implemented_monthly_usd", label: "Implemented", color: CHART.actioned },
] as const;

const DETECTOR_LABEL: Record<string, string> = {
	model_routing: "Model routing",
	cache_efficiency: "Prompt caching",
	context_overhead: "Context size",
	plan_fit: "Seat right-sizing",
};

export function PipelineStages({ pipeline }: { pipeline: CostIntelligence["pipeline"] }) {
	const max = Math.max(pipeline.identified_monthly_usd, 1);
	return (
		<div className="space-y-3">
			{STAGES.map((stage) => {
				const value = pipeline[stage.key];
				const count = pipeline.counts[stage.label.toLowerCase() as keyof typeof pipeline.counts];
				return (
					<div key={stage.key} className="grid grid-cols-[6.5rem_1fr_auto] items-center gap-3">
						<span className="text-xs text-muted-foreground">{stage.label}</span>
						<div className="h-3 rounded-full bg-muted" role="presentation">
							<div
								className="h-3 rounded-full transition-[width] duration-200"
								style={{ width: value > 0 ? `${Math.max((100 * value) / max, 2)}%` : "0%", background: stage.color }}
							/>
						</div>
						<span className="text-right text-xs tabular-nums">
							<span className="font-semibold">{usd(value)}</span>
							<span className="text-muted-foreground">/mo · {count}</span>
						</span>
					</div>
				);
			})}
			{pipeline.blocker && <p className="pt-1 text-xs text-muted-foreground">{pipeline.blocker}</p>}
		</div>
	);
}

function EvidenceLine({ opp }: { opp: CostOpportunity }) {
	const ev = opp.evidence as Record<string, number | string | boolean | null | undefined>;
	if (opp.detector === "model_routing") {
		const parity =
			ev.validated === true
				? `${pct(Number(ev.target_success_pct))} judged successful on ${modelLabel(String(ev.to_model))} vs ${pct(Number(ev.premium_success_pct))} on ${modelLabel(String(ev.from_model))}`
				: "Outcome parity not shown yet: review results on the cheaper model before approving";
		return (
			<>
				{ev.premium_sessions} sessions on {modelLabel(String(ev.from_model))} cost {usd(Number(ev.premium_cost_usd))}; the same work at{" "}
				{modelLabel(String(ev.to_model))} rates is {usd(Number(ev.repriced_cost_usd))}. The agent already ran {ev.target_sessions} sessions on{" "}
				{modelLabel(String(ev.to_model))}. {parity}.
			</>
		);
	}
	if (opp.detector === "cache_efficiency") {
		return (
			<>
				Cache hit rate {pct(Number(ev.agent_hit_pct))} across {ev.sessions} sessions, against {pct(Number(ev.deployment_hit_pct))} for the deployment.
			</>
		);
	}
	if (opp.detector === "plan_fit") {
		return (
			<>
				{ev.seats} {String(ev.harness)} seats at {usd(Number(ev.seat_usd_month))}/mo; {ev.active_users} active users used{" "}
				{usd(Number(ev.usage_value_monthly_usd))}/mo at API rates. {ev.underused_users} seats cost more than their user&apos;s usage
				{Number(ev.idle_seats) > 0 ? ` and ${ev.idle_seats} had no usage at all` : ""}. Dropping or switching those seats to pay-per-token
				saves the difference.
			</>
		);
	}
	if (opp.detector === "context_overhead") {
		return (
			<>
				Median first-turn context {Number(ev.agent_median_context_tokens).toLocaleString()} tokens vs {Number(ev.deployment_median_context_tokens).toLocaleString()} for
				a typical agent, across {ev.sessions} sessions.
			</>
		);
	}
	return null;
}

function OpportunityRow({ opp, realized }: { opp: CostOpportunity; realized?: CostRealizedOpportunity }) {
	const [open, setOpen] = useState(false);
	const [version, setVersion] = useState("");
	const action = useCostOpportunityAction();
	const busy = action.isPending;
	const run = (body: Parameters<typeof action.mutate>[0]["body"]) => action.mutate({ id: opp.id, body });

	return (
		<li className="py-3">
			<div className="flex items-start gap-3">
				<button
					type="button"
					onClick={() => setOpen((v) => !v)}
					aria-expanded={open}
					className="mt-0.5 rounded p-0.5 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
					aria-label={open ? "Hide evidence" : "Show evidence"}
				>
					<ChevronDown className={`h-4 w-4 transition-transform duration-150 ${open ? "rotate-180" : ""}`} />
				</button>
				<div className="min-w-0 flex-1">
					<p className="text-sm font-medium">{opp.title}</p>
					<p className="text-xs text-muted-foreground">
						{opp.agent} · {DETECTOR_LABEL[opp.detector] ?? opp.detector}
						{opp.baseline_version ? ` · v${opp.baseline_version}` : ""}
						{opp.status === "implemented" && opp.implemented_version ? ` → v${opp.implemented_version}` : ""}
					</p>
				</div>
				<div className="text-right">
					<p className="text-sm font-semibold tabular-nums">{usd(opp.estimated_monthly_usd)}/mo</p>
					{realized?.realized_measurable ? (
						<p className={`text-xs tabular-nums ${realized.realized_mtd_usd >= 0 ? "text-success" : "text-destructive"}`}>
							{usd(realized.realized_mtd_usd)} realized this month
							{realized.realized_low_usd !== null && realized.realized_high_usd !== null && (
								<span className="text-muted-foreground">
									{" "}
									({usd(realized.realized_low_usd)} to {usd(realized.realized_high_usd)})
								</span>
							)}
						</p>
					) : (
						<p className="text-xs capitalize text-muted-foreground">{opp.status}</p>
					)}
				</div>
			</div>
			{open && (
				<div className="ml-7 mt-2 space-y-3 text-xs text-muted-foreground">
					<p>
						<EvidenceLine opp={opp} />
					</p>
					{opp.evidence.formula && <p className="font-mono text-2xs">{String(opp.evidence.formula)}</p>}
					{realized && !realized.realized_measurable && realized.realized_reason && <p>Realized savings: {realized.realized_reason}.</p>}
					{realized?.realized_measurable && (
						<p>
							Measured over {realized.realized_sessions} sessions on v{opp.implemented_version}{" "}
							{realized.realized_normalized
								? "per successful session, so easier tasks are not counted as savings."
								: "per session; not enough judged outcomes to compare per successful session."}{" "}
							Range is a 90% bootstrap interval.
						</p>
					)}
					{(opp.evidence as { billing_class?: string }).billing_class === "capacity" && (
						<p>Subscription usage: this frees plan capacity; the seat fee does not change.</p>
					)}
					{opp.baseline?.cost_per_session != null && (
						<p>
							Baseline frozen at approval: {usd(opp.baseline.cost_per_session)} per session over {opp.baseline.sessions} sessions on v{opp.baseline.version}.
						</p>
					)}
					<div className="flex flex-wrap items-center gap-2">
						{opp.status === "identified" && (
							<>
								<Button size="sm" onClick={() => run({ action: "approve" })} disabled={busy}>
									{busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
									Approve
								</Button>
								<Button size="sm" variant="ghost" onClick={() => run({ action: "dismiss" })} disabled={busy}>
									<X className="h-3.5 w-3.5" />
									Dismiss
								</Button>
							</>
						)}
						{opp.status === "approved" && !opp.agent_id && (
							<Button size="sm" onClick={() => run({ action: "implemented" })} disabled={busy}>
								Mark implemented
							</Button>
						)}
						{opp.status === "approved" && opp.agent_id && (
							<form
								className="flex flex-wrap items-center gap-2"
								onSubmit={(e) => {
									e.preventDefault();
									if (version.trim()) run({ action: "implemented", version: version.trim() });
								}}
							>
								<label className="sr-only" htmlFor={`version-${opp.id}`}>Released version</label>
								<input
									id={`version-${opp.id}`}
									value={version}
									onChange={(e) => setVersion(e.target.value)}
									placeholder="Released version, e.g. 1.2.0"
									className="h-8 w-48 rounded-md border border-input bg-transparent px-2 text-xs text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
								/>
								<Button size="sm" type="submit" disabled={busy || !version.trim()}>
									Mark implemented
								</Button>
								<Button size="sm" variant="ghost" type="button" onClick={() => run({ action: "reopen" })} disabled={busy}>
									<RotateCcw className="h-3.5 w-3.5" />
									Back to identified
								</Button>
								<span className="w-full text-2xs">Releases of this agent are detected automatically once they have sessions.</span>
							</form>
						)}
					</div>
				</div>
			)}
		</li>
	);
}

export function OpportunityList({ data }: { data: CostIntelligence }) {
	const realizedById = new Map(data.realized.map((r) => [r.id, r]));
	if (data.opportunities.length === 0) {
		return (
			<p className="text-sm text-muted-foreground">
				No savings opportunities above the threshold in the last {data.period.detection_window_days} days. Detectors look for agents running
				premium models where a cheaper one already succeeds, low prompt-cache hit rates, and oversized fixed context.
			</p>
		);
	}
	return (
		<ul className="divide-y divide-border">
			{data.opportunities.map((opp) => (
				<OpportunityRow key={opp.id} opp={opp} realized={realizedById.get(opp.id)} />
			))}
		</ul>
	);
}
