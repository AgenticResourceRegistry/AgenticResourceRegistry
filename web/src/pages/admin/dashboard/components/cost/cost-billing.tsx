// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useMemo, useState } from "react";
import { Loader2, Pencil } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useCostSettings, useUpdateCostSetting } from "@/hooks/use-api";
import type { CostHarnessBilling, CostIntelligence } from "@/lib/types";
import { CHART, usd } from "./format";

const MODE_LABEL: Record<CostHarnessBilling["mode"], string> = {
	metered: "Pay per token",
	subscription: "Subscription",
	unconfigured: "Not set",
};

type BillingConfig = Record<
	string,
	{ mode: "metered" | "subscription"; seats?: number; seat_usd_month?: number; plan_credits?: number; overage_usd_per_credit?: number }
>;

function parseConfig(raw: string | undefined): BillingConfig {
	if (!raw) return {};
	try {
		const data = JSON.parse(raw);
		return data && typeof data === "object" ? (data as BillingConfig) : {};
	} catch {
		return {};
	}
}

function Field({ label, value, onChange, step = "1" }: { label: string; value: number | undefined; onChange: (v: number) => void; step?: string }) {
	return (
		<label className="flex flex-col gap-1 text-2xs text-muted-foreground">
			{label}
			<input
				type="number"
				min={0}
				step={step}
				value={value ?? ""}
				onChange={(e) => onChange(e.target.value === "" ? 0 : Number(e.target.value))}
				className="h-8 w-28 rounded-md border border-input bg-transparent px-2 text-xs text-foreground tabular-nums focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
			/>
		</label>
	);
}

function HarnessEditor({ harness, current, onDone }: { harness: string; current: BillingConfig; onDone: () => void }) {
	const existing = current[harness];
	const [mode, setMode] = useState<"metered" | "subscription">(existing?.mode ?? "subscription");
	const [seats, setSeats] = useState(existing?.seats ?? 0);
	const [seatPrice, setSeatPrice] = useState(existing?.seat_usd_month ?? 0);
	const [planCredits, setPlanCredits] = useState(existing?.plan_credits ?? 0);
	const [overage, setOverage] = useState(existing?.overage_usd_per_credit ?? 0);
	const update = useUpdateCostSetting();

	const save = () => {
		const next: BillingConfig = { ...current };
		next[harness] =
			mode === "metered"
				? { mode }
				: {
						mode,
						seats,
						seat_usd_month: seatPrice,
						...(planCredits > 0 ? { plan_credits: planCredits, overage_usd_per_credit: overage } : {}),
					};
		update.mutate({ key: "cost.billing", value: JSON.stringify(next) }, { onSuccess: onDone });
	};

	return (
		<div className="mt-2 space-y-3 rounded-lg bg-surface-raised p-3">
			<div className="flex gap-1 rounded-md bg-card p-0.5" role="group" aria-label={`How ${harness} is paid for`}>
				{(["metered", "subscription"] as const).map((m) => (
					<button
						key={m}
						type="button"
						aria-pressed={mode === m}
						onClick={() => setMode(m)}
						className={`flex-1 rounded px-2 py-1 text-xs font-medium transition-colors duration-150 ${
							mode === m ? "bg-surface-raised text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"
						}`}
					>
						{m === "metered" ? "Pay per token (API key, Bedrock, Vertex)" : "Subscription (seats)"}
					</button>
				))}
			</div>
			{mode === "subscription" && (
				<div className="flex flex-wrap gap-3">
					<Field label="Seats" value={seats} onChange={setSeats} />
					<Field label="Price per seat / month ($)" value={seatPrice} onChange={setSeatPrice} step="0.01" />
					<Field label="Credits included per seat" value={planCredits} onChange={setPlanCredits} />
					<Field label="Overage per credit ($)" value={overage} onChange={setOverage} step="0.01" />
				</div>
			)}
			<div className="flex items-center gap-2">
				<Button size="sm" onClick={save} disabled={update.isPending}>
					{update.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
					Save
				</Button>
				<Button size="sm" variant="ghost" onClick={onDone}>
					Cancel
				</Button>
			</div>
		</div>
	);
}

/** How this month's spend is billed, per harness, with inline billing-mode setup. */
export function BillingPanel({ data }: { data: CostIntelligence }) {
	const { data: settings } = useCostSettings();
	const config = useMemo(() => parseConfig(settings?.settings.find((s) => s.key === "cost.billing")?.value), [settings]);
	const [editing, setEditing] = useState<string | null>(null);
	const b = data.billing;
	const max = Math.max(...b.harnesses.map((h) => Math.max(h.billed_usd, h.usage_value_usd)), 1);

	return (
		<div className="space-y-4">
			{!b.complete && (
				<p className="rounded-lg bg-warning/10 px-3 py-2 text-xs">
					{usd(b.unclassified_usd)} of usage has no billing mode yet and is counted as pay-per-token. Set how each harness is
					paid for below so spend reflects your actual bill.
				</p>
			)}
			<ul className="space-y-3">
				{b.harnesses.map((h) => (
					<li key={h.harness}>
						<div className="flex items-start justify-between gap-3">
							<div className="min-w-0">
								<p className="text-sm font-medium">{h.harness}</p>
								<p className="text-2xs text-muted-foreground" title={h.evidence}>
									{MODE_LABEL[h.mode]}
									{h.source === "detected" ? " · detected" : h.source === "configured" ? " · configured" : ""}
									{h.mode === "subscription" && h.seats ? ` · ${h.seats} seats` : ""}
								</p>
							</div>
							<div className="flex items-center gap-2 text-right">
								<div>
									<p className="text-sm font-semibold tabular-nums">{usd(h.billed_usd)}</p>
									<p className="text-2xs text-muted-foreground tabular-nums">{usd(h.usage_value_usd)} at API rates</p>
								</div>
								<Button
									size="sm"
									variant="ghost"
									aria-label={`Edit billing for ${h.harness}`}
									onClick={() => setEditing(editing === h.harness ? null : h.harness)}
								>
									<Pencil className="h-3.5 w-3.5" />
								</Button>
							</div>
						</div>
						<div className="mt-1.5 space-y-1" aria-hidden="true">
							<div className="h-1.5 rounded-full bg-muted">
								<div className="h-1.5 rounded-full" style={{ width: `${(100 * h.billed_usd) / max}%`, background: "oklch(var(--primary))" }} />
							</div>
							<div className="h-1.5 rounded-full bg-muted">
								<div className="h-1.5 rounded-full" style={{ width: `${(100 * h.usage_value_usd) / max}%`, background: CHART.spend }} />
							</div>
						</div>
						{editing === h.harness && <HarnessEditor harness={h.harness} current={config} onDone={() => setEditing(null)} />}
					</li>
				))}
			</ul>
			<p className="text-2xs text-muted-foreground">
				Top bar: billed this month. Lower bar: the same usage at provider API rates. A subscription bills its seats whatever
				the usage; Bedrock, Vertex and Azure endpoints are detected as pay-per-token automatically.
			</p>
		</div>
	);
}
