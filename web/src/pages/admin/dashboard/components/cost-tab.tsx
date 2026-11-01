// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0


import { useState, useContext } from "react";
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Line, BarChart, Bar } from "recharts";
import { useExecCostSummary, useExecROIProjections } from "@/hooks/use-api";
import { exec } from "@/lib/api";
import { StatCard } from "./stat-card";
import { Loader2, TrendingUp } from "lucide-react";
import { toast } from "sonner";
import { DashboardRangeContext } from "../context";

const DEFAULT_CATEGORIES = [
  "Code Review",
  "Testing",
  "Documentation",
  "Incident Response",
  "Deployment",
  "Security Scanning",
];

function BaselinesConfigForm({ onSaved }: { onSaved: () => void }) {
  const [hourlyDevCost, setHourlyDevCost] = useState("75");
  const [baselines, setBaselines] = useState<Record<string, string>>(
    Object.fromEntries(DEFAULT_CATEGORIES.map((c) => [c, ""]))
  );
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    setSaving(true);
    try {
      const preAiBaselines: Record<string, number> = {};
      for (const [key, val] of Object.entries(baselines) as [string, string][]) {
        const num = parseFloat(val);
        if (!isNaN(num) && num > 0) preAiBaselines[key] = num;
      }
      await exec.updateConfig({
        hourly_dev_cost: parseFloat(hourlyDevCost) || 75,
        pre_ai_baselines: preAiBaselines,
      });
      onSaved();
    } catch {
      toast.error("Failed to save baselines. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-6 pt-4">
      <div className="rounded-xl bg-card p-5 shadow-sm">
        <h3 className="text-sm font-semibold mb-1">Configure Cost Baselines</h3>
        <p className="text-xs text-muted-foreground mb-6">
          Enter what tasks cost before AI Agent Files were deployed. This allows the dashboard to compute savings and ROI.
        </p>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <label className="text-xs font-medium">Hourly Developer Cost ($)</label>
            <input
              type="number"
              value={hourlyDevCost}
              onChange={(e) => setHourlyDevCost(e.target.value)}
              className="flex h-8 w-40 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              placeholder="75"
            />
            <p className="text-[11px] text-muted-foreground">Used to compute developer hours reclaimed.</p>
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-medium">Pre-AI Cost per Task by Category ($)</label>
            <p className="text-[11px] text-muted-foreground mb-2">
              Average cost to complete one task manually, before AI. Leave blank for categories that don&apos;t apply.
            </p>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {DEFAULT_CATEGORIES.map((cat) => (
                <div key={cat} className="flex items-center gap-2">
                  <span className="text-xs w-32 truncate">{cat}</span>
                  <input
                    type="number"
                    value={baselines[cat] ?? ""}
                    onChange={(e) => setBaselines({ ...baselines, [cat]: e.target.value })}
                    className="flex h-7 w-20 rounded-md border border-input bg-transparent px-2 py-1 text-xs shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                    placeholder="0.00"
                    step="0.01"
                  />
                </div>
              ))}
            </div>
          </div>

          <button
            onClick={handleSave}
            disabled={saving}
            className="inline-flex items-center gap-1.5 px-4 py-2 text-xs font-medium rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            {saving && <Loader2 className="h-3 w-3 animate-spin" />}
            Save Baselines
          </button>
        </div>
      </div>
    </div>
  );
}

function ROIProjections() {
  const { data: roi, isLoading } = useExecROIProjections();

  if (isLoading) {
    return <div className="h-64 rounded-xl bg-card shadow-sm animate-pulse" />;
  }

  if (!roi || roi.projections.length === 0) {
    return null;
  }

  return (
    <div className="rounded-xl bg-card shadow-sm p-5">
      <div className="flex items-center justify-between mb-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <TrendingUp className="h-4 w-4 text-success" />
            <h3 className="text-sm font-semibold">ROI Projections</h3>
          </div>
          <p className="text-xs text-muted-foreground">Quarterly savings forecast based on current growth trajectory</p>
        </div>
        <div className="text-right">
          <p className="text-2xl font-bold text-success">{roi.roi_multiple}x</p>
          <p className="text-[11px] text-muted-foreground">ROI multiple</p>
        </div>
      </div>
      <div className="mb-6 grid grid-cols-1 gap-px overflow-hidden rounded-xl bg-border sm:grid-cols-2 xl:grid-cols-4">
        <div className="bg-surface-raised p-3 text-center">
          <p className="text-lg font-bold text-foreground">
            ${roi.total_saved >= 1000 ? `${(roi.total_saved / 1000).toFixed(1)}K` : roi.total_saved.toFixed(0)}
          </p>
          <p className="text-[11px] text-muted-foreground">Total Saved</p>
        </div>
        <div className="bg-surface-raised p-3 text-center">
          <p className="text-lg font-bold text-foreground">
            ${roi.total_invested >= 1000 ? `${(roi.total_invested / 1000).toFixed(1)}K` : roi.total_invested.toFixed(0)}
          </p>
          <p className="text-[11px] text-muted-foreground">Total Invested</p>
        </div>
        <div className="bg-surface-raised p-3 text-center">
          <p className="text-lg font-bold text-success">{roi.growth_rate_pct}%</p>
          <p className="text-[11px] text-muted-foreground">Growth Rate</p>
        </div>
        <div className="bg-surface-raised p-3 text-center">
          <p className="text-lg font-bold text-foreground">
            {roi.time_to_breakeven_months === null
              ? "—"
              : roi.time_to_breakeven_months === 0
              ? "Done"
              : `${roi.time_to_breakeven_months}mo`}
          </p>
          <p className="text-[11px] text-muted-foreground">To Breakeven</p>
        </div>
      </div>
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={roi.projections} margin={{ top: 5, right: 5, bottom: 5, left: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="oklch(var(--border))" strokeOpacity={0.5} vertical={false} />
          <XAxis dataKey="quarter" tick={{ fill: "oklch(var(--muted-foreground))", fontSize: 11 }} axisLine={false} tickLine={false} />
          <YAxis tick={{ fill: "oklch(var(--muted-foreground))", fontSize: 11 }} tickFormatter={(v) => `$${(v / 1000).toFixed(0)}K`} axisLine={false} tickLine={false} />
          <Tooltip
            formatter={(value, name) => [
              `$${(Number(value) / 1000).toFixed(1)}K`,
              name === "projected_savings" ? "Quarterly Savings" : "Cumulative",
            ]}
            contentStyle={{ background: "oklch(var(--background))", border: "1px solid oklch(var(--border))", borderRadius: 8, fontSize: 12 }}
            cursor={{ fill: "oklch(var(--muted))", opacity: 0.3 }}
          />
          <Bar dataKey="projected_savings" fill="oklch(var(--primary))" radius={[4, 4, 0, 0]} activeBar={false} />
        </BarChart>
      </ResponsiveContainer>
      <div className="mt-4 grid grid-cols-1 gap-3 border-t border-border pt-4 sm:grid-cols-2 xl:grid-cols-4">
        {roi.projections.map((p) => (
          <div
            key={p.quarter}
            className={`rounded-md p-3 text-center border ${
              p.confidence >= 0.8 ? "border-border" : "border-dashed border-muted-foreground/30"
            }`}
          >
            <p className="text-[11px] text-muted-foreground mb-1">{p.quarter}</p>
            <p className="text-sm font-bold text-success">
              ${(p.projected_savings / 1000).toFixed(1)}K
            </p>
            <p className="text-[10px] text-muted-foreground">
              Cum: ${(p.cumulative_savings / 1000).toFixed(1)}K
            </p>
            <p className="text-[10px] text-muted-foreground/50">
              {Math.round(p.confidence * 100)}% confidence
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

export function CostTab() {
  const range = useContext(DashboardRangeContext);
  const { data: cost, isLoading, isError, refetch } = useExecCostSummary(range);
  const [showEditBaselines, setShowEditBaselines] = useState(false);

  if (isError) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <p className="text-sm text-muted-foreground">Failed to load cost data. Check your connection and try again.</p>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="space-y-6 pt-4">
        <div className="grid grid-cols-1 gap-px overflow-hidden rounded-xl bg-border shadow-sm sm:grid-cols-2 xl:grid-cols-4">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="h-24 rounded-xl bg-card shadow-sm animate-pulse" />
          ))}
        </div>
        <div className="h-64 rounded-xl bg-card shadow-sm animate-pulse" />
      </div>
    );
  }

  if (!cost?.configured || showEditBaselines) {
    return <BaselinesConfigForm onSaved={() => { refetch(); setShowEditBaselines(false); }} />;
  }

  return (
    <div className="space-y-6 pt-4">
      <div className="flex items-center justify-end mb-2">
        <button
          onClick={() => setShowEditBaselines(true)}
          className="inline-flex items-center px-3 py-1.5 text-[11px] font-medium rounded-md bg-card shadow-sm hover:bg-muted/50 transition-colors"
        >
          Edit baselines
        </button>
      </div>
      <div className="grid grid-cols-1 gap-px overflow-hidden rounded-xl bg-border shadow-sm sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Monthly Savings" value={`$${(cost.monthly_savings / 1000).toFixed(1)}K`} />
        <StatCard label="Cost Reduction" value={`${cost.cost_reduction_pct}%`} />
        <StatCard label="Projected Annual" value={`$${(cost.projected_annual_savings / 1000).toFixed(0)}K`} />
        <StatCard label="Avg Cost/Session" value={`$${cost.cost_per_task.toFixed(3)}`} />
      </div>
      <div className="rounded-xl bg-card shadow-sm p-5">
        <h3 className="text-sm font-medium mb-1">Savings vs AI Spend</h3>
        <p className="text-xs text-muted-foreground mb-4">Monthly savings generated vs platform spend</p>
        {cost.monthly_trend.length > 0 ? (
          <ResponsiveContainer width="100%" height={280}>
            <AreaChart data={cost.monthly_trend} margin={{ top: 10, right: 10, bottom: 0, left: 10 }}>
              <defs>
                <linearGradient id="savingsGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="oklch(var(--success))" stopOpacity={0.25} />
                  <stop offset="100%" stopColor="oklch(var(--success))" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="oklch(var(--border))" strokeOpacity={0.5} vertical={false} />
              <XAxis dataKey="month" tick={{ fill: "oklch(var(--muted-foreground))", fontSize: 11 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: "oklch(var(--muted-foreground))", fontSize: 11 }} tickFormatter={(v) => `$${(v / 1000).toFixed(1)}K`} axisLine={false} tickLine={false} />
              <Tooltip
                formatter={(value, name) => [`$${Number(value).toFixed(2)}`, name === "savings" ? "Savings" : "AI Spend"]}
                contentStyle={{ background: "oklch(var(--background))", border: "1px solid oklch(var(--border))", borderRadius: 8, fontSize: 12 }}
              />
              <Area type="natural" dataKey="savings" stroke="oklch(var(--success))" strokeWidth={2.5} fill="url(#savingsGrad)" dot={false} activeDot={{ r: 4, strokeWidth: 2, fill: "oklch(var(--background))", stroke: "oklch(var(--success))" }} />
              <Line type="natural" dataKey="ai_spend" stroke="oklch(var(--destructive))" strokeWidth={2} strokeDasharray="4 4" dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <div className="h-64 flex items-center justify-center text-muted-foreground text-sm">
            No cost data available yet — spans need a populated cost column.
          </div>
        )}
        <div className="flex gap-4 mt-3 text-xs text-muted-foreground">
          <div className="flex items-center gap-2">
            <div className="w-3 h-0.5 bg-success rounded" />
            <span>Savings</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-3 h-0.5 bg-destructive rounded" />
            <span>AI Spend</span>
          </div>
        </div>
      </div>
      {cost.by_category.length > 0 && (
        <div className="rounded-xl bg-card shadow-sm p-5">
          <h3 className="text-sm font-medium mb-1">Cost per Task by Category</h3>
          <p className="text-xs text-muted-foreground mb-4">Pre-AI baseline vs actual AI cost</p>
          <div className="space-y-4">
            {(() => { const maxCost = Math.max(...cost.by_category.map((c) => Math.max(c.baseline_cost, c.actual_cost)), 1); return cost.by_category.map((cat) => (
              <div key={cat.category} className="flex items-center gap-3">
                <span className="text-sm w-32 truncate font-medium">{cat.category}</span>
                <div className="flex-1 space-y-1">
                  <div className="flex items-center gap-2">
                    <div
                      className="h-1.5 bg-muted-foreground/30 rounded-full"
                      style={{ width: `${Math.min((cat.baseline_cost / maxCost) * 100, 100)}%` }}
                    />
                    <span className="text-xs text-muted-foreground">${cat.baseline_cost.toFixed(2)}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div
                      className="h-1.5 bg-success rounded-full"
                      style={{ width: `${Math.min((cat.actual_cost / maxCost) * 100, 100)}%` }}
                    />
                    <span className="text-xs text-success font-semibold">${cat.actual_cost.toFixed(2)}</span>
                  </div>
                </div>
                <span className="text-xs font-semibold text-success bg-success/10 px-2 py-0.5 rounded">
                  {cat.saved_pct}%
                </span>
              </div>
            )); })()}
          </div>
          <div className="flex gap-4 mt-4 pt-3 border-t border-border text-xs text-muted-foreground">
            <div className="flex items-center gap-2">
              <div className="w-3 h-1.5 bg-muted-foreground/30 rounded" />
              <span>Before (manual)</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-3 h-1.5 bg-success rounded" />
              <span>After (AI Agent Files)</span>
            </div>
          </div>
        </div>
      )}
      <ROIProjections />
    </div>
  );
}
