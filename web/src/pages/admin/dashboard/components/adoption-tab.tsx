// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0


import { useContext, useState } from "react";
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Line } from "recharts";
import { useExecAdoption, useExecAgentCounts, useExecUsageByCategory, useExecPlatformCoverage } from "@/hooks/use-api";
import { StatCard } from "./stat-card";
import { DashboardRangeContext } from "../context";

export function AdoptionTab() {
  const range = useContext(DashboardRangeContext);
  const { data: adoption, isLoading: adoptionLoading, isError: adoptionError } = useExecAdoption();
  const { data: agents, isLoading: agentsLoading, isError: agentsError } = useExecAgentCounts();
  const { data: usage } = useExecUsageByCategory(range);
  const { data: platforms } = useExecPlatformCoverage();

  if (adoptionError || agentsError) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <p className="text-sm text-muted-foreground">Failed to load adoption data. Check your connection and try again.</p>
      </div>
    );
  }

  if (adoptionLoading || agentsLoading) {
    return (
      <div className="space-y-6 pt-4">
        <div className="grid grid-cols-1 gap-px overflow-hidden rounded-xl bg-border sm:grid-cols-2 xl:grid-cols-4">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="h-24 rounded-xl bg-card shadow-sm animate-pulse" />
          ))}
        </div>
        <div className="h-64 rounded-xl bg-card shadow-sm animate-pulse" />
      </div>
    );
  }

  return (
    <div className="space-y-6 pt-4">
      <div className="grid grid-cols-1 gap-px overflow-hidden rounded-xl bg-border shadow-sm sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="AI Adoption" value={`${adoption?.current_pct ?? 0}%`} subtitle="of users active" />
        <StatCard label="Active Users" value={adoption?.active_users ?? 0} subtitle={`of ${adoption?.total_users ?? 0} total`} />
        <StatCard label="Departments" value={adoption?.departments_covered ?? 0} subtitle="with AI usage" />
        <StatCard label="Active Agent Files" value={agents?.active ?? 0} subtitle={`of ${agents?.total ?? 0} total`} />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <AdoptionChart monthly={adoption?.monthly ?? []} />


        <div className="rounded-xl bg-card shadow-sm p-5">
          <h3 className="text-sm font-medium mb-4">Agent Files by Category</h3>
          <div className="flex items-center gap-4 mb-4 text-xs text-muted-foreground">
            <span><strong className="text-foreground">{agents?.published ?? 0}</strong> published</span>
            <span><strong className="text-foreground">{agents?.in_development ?? 0}</strong> in dev</span>
          </div>
          <div className="space-y-2">
            {agents?.by_category?.map((cat) => (
              <div key={cat.category} className="flex items-center justify-between text-sm">
                <span className="truncate">{cat.category}</span>
                <span className="font-semibold tabular-nums">{cat.count}</span>
              </div>
            ))}
            {(!agents?.by_category || agents.by_category.length === 0) && (
              <p className="text-xs text-muted-foreground">No Agent Files yet.</p>
            )}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="rounded-xl bg-card shadow-sm p-5">
          <h3 className="text-sm font-medium mb-4">Agent File Usage by Category</h3>
          {usage && usage.length > 0 ? (
            <div className="space-y-3">
              {usage.slice(0, 8).map((item) => (
                <div key={item.category} className="flex items-center gap-3">
                  <span className="text-sm w-32 truncate">{item.category}</span>
                  <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
                    <div
                      className="h-full bg-primary rounded-full"
                      style={{ width: `${Math.min((item.sessions / (usage[0]?.sessions || 1)) * 100, 100)}%` }}
                    />
                  </div>
                  <span className="text-xs tabular-nums text-muted-foreground w-16 text-right">{item.sessions.toLocaleString()}</span>
                  <span className={`text-xs tabular-nums w-12 text-right ${item.growth_pct > 0 ? "text-success" : item.growth_pct < 0 ? "text-destructive" : "text-muted-foreground"}`}>
                    {item.growth_pct > 0 ? "+" : ""}{item.growth_pct}%
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No usage data yet.</p>
          )}
        </div>

        <div className="rounded-xl bg-card shadow-sm p-5">
          <h3 className="text-sm font-medium mb-4">Platform Coverage</h3>
          {platforms && platforms.length > 0 ? (
            <div className="space-y-3">
              {platforms.map((p) => (
                <div key={p.platform} className="flex items-center justify-between text-sm">
                  <span className="font-medium">{p.platform}</span>
                  <div className="flex items-center gap-4 text-xs text-muted-foreground">
                    <span>{p.users} users</span>
                    <span className="tabular-nums font-semibold text-foreground">{p.sessions.toLocaleString()} sessions</span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No platform data yet.</p>
          )}
        </div>
      </div>
    </div>
  );
}

function AdoptionChart({ monthly }: { monthly: { month: string; adoption_pct: number }[] }) {
  const [showPrevious, setShowPrevious] = useState(false);

  // Build comparison data: overlay previous N months as a dashed line
  const halfLen = Math.ceil(monthly.length / 2);
  const chartData = monthly.map((point, i) => ({
    ...point,
    previous_pct: showPrevious && i >= halfLen && monthly[i - halfLen]
      ? monthly[i - halfLen].adoption_pct
      : undefined,
  }));

  return (
    <div className="lg:col-span-2 rounded-xl bg-card shadow-sm p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-medium">AI Adoption Over Time</h3>
        {monthly.length >= 4 && (
          <button
            onClick={() => setShowPrevious(!showPrevious)}
            className={`text-[11px] px-2.5 py-1 rounded border transition-colors ${
              showPrevious
                ? "border-primary bg-primary/10 text-primary"
                : "border-border text-muted-foreground hover:text-foreground"
            }`}
          >
            vs previous period
          </button>
        )}
      </div>
      {monthly.length > 0 ? (
        <>
          <ResponsiveContainer width="100%" height={260}>
            <AreaChart data={chartData} margin={{ top: 10, right: 10, bottom: 0, left: 5 }}>
              <defs>
                <linearGradient id="adoptionGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="oklch(var(--primary))" stopOpacity={0.3} />
                  <stop offset="100%" stopColor="oklch(var(--primary))" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="oklch(var(--border))" strokeOpacity={0.5} vertical={false} />
              <XAxis dataKey="month" tick={{ fill: "var(--chart-axis-tick)", fontSize: 11 }} axisLine={false} tickLine={false} />
              <YAxis domain={[0, 100]} tickFormatter={(v) => `${v}%`} tick={{ fill: "var(--chart-axis-tick)", fontSize: 11 }} axisLine={false} tickLine={false} />
              <Tooltip formatter={(value, name) => [`${value}%`, name === "previous_pct" ? "Previous Period" : "Current"]} contentStyle={{ background: "oklch(var(--background))", border: "1px solid oklch(var(--border))", borderRadius: 8, fontSize: 12 }} />
              <Area type="natural" dataKey="adoption_pct" stroke="oklch(var(--primary))" strokeWidth={2.5} fill="url(#adoptionGrad)" dot={false} activeDot={{ r: 4, strokeWidth: 2, fill: "oklch(var(--background))" }} />
              {showPrevious && (
                <Line type="natural" dataKey="previous_pct" stroke="oklch(var(--muted-foreground))" strokeWidth={1.5} strokeDasharray="4 4" dot={false} connectNulls={false} />
              )}
            </AreaChart>
          </ResponsiveContainer>
          {showPrevious && (
            <div className="flex gap-4 mt-2 text-[11px] text-muted-foreground">
              <div className="flex items-center gap-1.5">
                <div className="w-3 h-0.5 bg-primary rounded" />
                <span>Current</span>
              </div>
              <div className="flex items-center gap-1.5">
                <div className="w-3 h-0.5 bg-muted-foreground rounded border-dashed" />
                <span>Previous period</span>
              </div>
            </div>
          )}
        </>
      ) : (
        <div className="h-64 flex items-center justify-center text-muted-foreground text-sm">
          No adoption data yet — traces will populate this chart.
        </div>
      )}
    </div>
  );
}
