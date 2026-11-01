// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0


import { useState } from "react";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Line,
} from "recharts";
import { useExecVelocity, useExecTopAgents, useExecTimeToValue } from "@/hooks/use-api";
import { StatCard } from "./stat-card";

function Sparkline({ data }: { data: number[] }) {
  if (!data || data.length < 2) return null;
  const max = Math.max(...data);
  const min = Math.min(...data);
  const range = max - min || 1;
  const points = data.map((v, i) => `${(i / (data.length - 1)) * 60},${20 - ((v - min) / range) * 18}`).join(" ");

  return (
    <svg width="60" height="20" className="inline-block">
      <polyline
        points={points}
        fill="none"
        stroke="oklch(var(--primary))"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function VelocityTab() {
  const { data: velocity, isLoading: velLoading, isError: velError } = useExecVelocity();
  const { data: topAgents, isLoading: agentsLoading } = useExecTopAgents(10);

  if (velError) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <p className="text-sm text-muted-foreground">Failed to load velocity data. Check your connection and try again.</p>
      </div>
    );
  }

  if (velLoading) {
    return (
      <div className="space-y-6 pt-4">
        <div className="h-80 rounded-xl bg-card shadow-sm animate-pulse" />
      </div>
    );
  }

  return (
    <div className="space-y-6 pt-4">
      <div className="grid grid-cols-1 gap-px overflow-hidden rounded-xl bg-border sm:grid-cols-3">
        <StatCard label="Multiplier" value={`${velocity?.multiplier ?? 0}x`} subtitle="vs baseline" />
        <StatCard label="Current Avg" value={Math.round(velocity?.current_weekly_avg ?? 0)} subtitle="traces/week" />
        <StatCard label="Baseline Avg" value={Math.round(velocity?.baseline_weekly_avg ?? 0)} subtitle="traces/week (first 4 weeks)" />
      </div>
      <VelocityChart weekly={velocity?.weekly ?? []} />
      <div className="overflow-x-auto rounded-xl bg-card shadow-sm">
        <div className="p-4 border-b border-border">
          <h3 className="text-sm font-medium">Best Agent Files</h3>
          <p className="text-xs text-muted-foreground">Ranked by composite score (sessions × 0.4 + downloads × 0.3 + rating × 0.3)</p>
        </div>
        {agentsLoading ? (
          <div className="p-4">
            <div className="h-40 animate-pulse bg-muted/30 rounded" />
          </div>
        ) : !topAgents || topAgents.length === 0 ? (
          <div className="p-8 text-center text-sm text-muted-foreground">
            No Agent Files with session data yet.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/30">
                <th className="text-left p-3 font-medium">#</th>
                <th className="text-left p-3 font-medium">Agent File</th>
                <th className="text-left p-3 font-medium">Category</th>
                <th className="text-left p-3 font-medium">Score</th>
                <th className="text-left p-3 font-medium">Sessions</th>
                <th className="text-left p-3 font-medium">Downloads</th>
                <th className="text-left p-3 font-medium">Rating</th>
                <th className="text-left p-3 font-medium">Trend</th>
              </tr>
            </thead>
            <tbody>
              {topAgents.map((agent, i) => (
                <tr key={agent.id} className="border-b border-border">
                  <td className="p-3 text-muted-foreground font-mono text-xs">{i + 1}</td>
                  <td className="p-3 font-semibold">{agent.name}</td>
                  <td className="p-3 text-muted-foreground">{agent.category}</td>
                  <td className="p-3">
                    <div className="flex items-center gap-2">
                      <div className="w-12 h-1.5 bg-muted rounded-full overflow-hidden">
                        <div className="h-full bg-primary rounded-full" style={{ width: `${agent.composite_score}%` }} />
                      </div>
                      <span className="text-xs tabular-nums text-muted-foreground">{agent.composite_score}</span>
                    </div>
                  </td>
                  <td className="p-3 tabular-nums">{agent.sessions.toLocaleString()}</td>
                  <td className="p-3 tabular-nums">{agent.downloads.toLocaleString()}</td>
                  <td className="p-3 tabular-nums">{agent.avg_rating ? `${agent.avg_rating}/5` : "—"}</td>
                  <td className="p-3">
                    <Sparkline data={agent.weekly_trend} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <TimeToValue />
    </div>
  );
}

function VelocityChart({ weekly }: { weekly: { week: string; traces: number }[] }) {
  const [showBaseline, setShowBaseline] = useState(false);

  // Baseline: repeat the avg of first 4 weeks as a flat dashed line
  const baselineAvg = weekly.length >= 4
    ? Math.round(weekly.slice(0, 4).reduce((s, w) => s + w.traces, 0) / 4)
    : 0;

  const chartData = weekly.map((point) => ({
    ...point,
    baseline: showBaseline ? baselineAvg : undefined,
  }));

  return (
    <div className="rounded-xl bg-card shadow-sm p-5">
      <div className="flex items-center justify-between mb-1">
        <h3 className="text-sm font-medium">Development Velocity</h3>
        {weekly.length >= 4 && (
          <button
            onClick={() => setShowBaseline(!showBaseline)}
            className={`text-[11px] px-2.5 py-1 rounded border transition-colors ${
              showBaseline
                ? "border-primary bg-primary/10 text-primary"
                : "border-border text-muted-foreground hover:text-foreground"
            }`}
          >
            vs baseline period
          </button>
        )}
      </div>
      <p className="text-xs text-muted-foreground mb-4">Traces per week over the last 12 weeks</p>
      {weekly.length > 0 ? (
        <>
          <ResponsiveContainer width="100%" height={280}>
            <AreaChart data={chartData} margin={{ top: 10, right: 10, bottom: 0, left: 5 }}>
              <defs>
                <linearGradient id="velGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="oklch(var(--primary))" stopOpacity={0.3} />
                  <stop offset="100%" stopColor="oklch(var(--primary))" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="oklch(var(--border))" strokeOpacity={0.5} vertical={false} />
              <XAxis dataKey="week" tick={{ fill: "var(--chart-axis-tick)", fontSize: 11 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: "var(--chart-axis-tick)", fontSize: 11 }} axisLine={false} tickLine={false} />
              <Tooltip formatter={(value, name) => [Number(value).toLocaleString(), name === "baseline" ? "Baseline (first 4 weeks)" : "Traces"]} contentStyle={{ background: "oklch(var(--background))", border: "1px solid oklch(var(--border))", borderRadius: 8, fontSize: 12 }} />
              <Area type="natural" dataKey="traces" stroke="oklch(var(--primary))" strokeWidth={2.5} fill="url(#velGrad)" dot={false} activeDot={{ r: 4, strokeWidth: 2, fill: "oklch(var(--background))" }} />
              {showBaseline && (
                <Line type="monotone" dataKey="baseline" stroke="oklch(var(--muted-foreground))" strokeWidth={1.5} strokeDasharray="6 3" dot={false} />
              )}
            </AreaChart>
          </ResponsiveContainer>
          {showBaseline && (
            <div className="flex gap-4 mt-2 text-[11px] text-muted-foreground">
              <div className="flex items-center gap-1.5">
                <div className="w-3 h-0.5 bg-primary rounded" />
                <span>Current</span>
              </div>
              <div className="flex items-center gap-1.5">
                <div className="w-3 h-0.5 bg-muted-foreground rounded" />
                <span>Baseline avg ({baselineAvg}/week)</span>
              </div>
            </div>
          )}
        </>
      ) : (
        <div className="h-64 flex items-center justify-center text-muted-foreground text-sm">
          Not enough data yet — need at least 4 weeks of traces.
        </div>
      )}
    </div>
  );
}

function TimeToValue() {
  const { data, isLoading } = useExecTimeToValue();

  if (isLoading) {
    return <div className="h-40 rounded-xl bg-card shadow-sm animate-pulse" />;
  }

  if (!data || data.agents.length === 0) return null;

  return (
    <div className="overflow-x-auto rounded-xl bg-card shadow-sm">
      <div className="p-4 border-b border-border flex items-center justify-between">
        <div>
          <h3 className="text-sm font-medium">Time to Value</h3>
          <p className="text-xs text-muted-foreground">Days from deployment to reaching 100 sessions</p>
        </div>
        {data.avg_days_to_100 !== null && (
          <div className="text-right">
            <p className="text-lg font-bold tabular-nums">{data.avg_days_to_100} days</p>
            <p className="text-[11px] text-muted-foreground">avg time to 100 sessions</p>
          </div>
        )}
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border bg-muted/30">
            <th className="text-left p-3 font-medium">Agent File</th>
            <th className="text-left p-3 font-medium">Category</th>
            <th className="text-left p-3 font-medium">Deployed</th>
            <th className="text-left p-3 font-medium">Days to 100</th>
            <th className="text-left p-3 font-medium">Sessions</th>
          </tr>
        </thead>
        <tbody>
          {data.agents.slice(0, 10).map((agent) => (
            <tr key={agent.id} className="border-b border-border last:border-0">
              <td className="p-3 font-medium">{agent.name}</td>
              <td className="p-3 text-muted-foreground">{agent.category}</td>
              <td className="p-3 tabular-nums text-xs text-muted-foreground">{agent.created_at}</td>
              <td className="p-3 tabular-nums">
                {agent.days_to_100 !== null ? (
                  <span className={agent.days_to_100 <= 7 ? "text-success font-semibold" : agent.days_to_100 <= 30 ? "text-foreground" : "text-warning"}>
                    {agent.days_to_100}d
                  </span>
                ) : (
                  <span className="text-muted-foreground">—</span>
                )}
              </td>
              <td className="p-3 tabular-nums">{agent.current_sessions.toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
