// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-License-Identifier: Apache-2.0


import { useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
  RadarChart, Radar, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
} from "recharts";
import { useExecPlatforms, useExecStrategicInsights } from "@/hooks/use-api";
import type { ExecPlatformScore } from "@/lib/types";

import { CHART_SERIES } from "@/lib/chart-theme";

const COLORS = CHART_SERIES;

function deriveRadarData(p: ExecPlatformScore, bestLatency: number) {
  const speedScore = bestLatency > 0 ? Math.max(0, 100 - ((p.avg_latency_ms / bestLatency) - 1) * 50) : 100;
  const data = [
    { metric: "Speed", value: Math.min(Math.max(speedScore, 0), 100) },
    { metric: "Volume", value: Math.min(Math.max(p.composite_score, 0), 100) },
  ];
  if (p.success_rate !== null) data.unshift({ metric: "Success Rate", value: Math.min(p.success_rate, 100) });
  if (p.error_rate !== null) data.push({ metric: "Reliability", value: Math.min(Math.max(100 - p.error_rate, 0), 100) });
  return data;
}

function formatPercent(value: number | null) {
  return value === null ? "N/A" : `${value}%`;
}

export function InvestmentsTab() {
  const { data: platforms, isLoading, isError } = useExecPlatforms();
  const [selected, setSelected] = useState(0);

  if (isError) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <p className="text-sm text-muted-foreground">Failed to load platform data. Check your connection and try again.</p>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="space-y-6 pt-4">
        <div className="h-80 rounded-xl bg-card shadow-sm animate-pulse" />
      </div>
    );
  }

  if (!platforms || platforms.length === 0) {
    return (
      <div className="space-y-6 pt-4">
        <div className="rounded-xl bg-card p-8 shadow-sm text-center text-muted-foreground">
          <p className="text-sm">No platform data yet. Traces from different harnesses will populate this view.</p>
        </div>
      </div>
    );
  }

  const platform = platforms[selected];
  const bestLatency = Math.min(...platforms.map((p) => p.avg_latency_ms || Infinity));
  const radarData = deriveRadarData(platform, bestLatency);
  const hasCostData = platform.avg_cost > 0;

  const chartData = platforms.map((p, i) => ({
    name: p.platform,
    sessions: p.sessions,
    color: COLORS[i % COLORS.length],
  }));

  return (
    <div className="space-y-6 pt-4">
      <div className="rounded-xl bg-card shadow-sm p-5">
        <h3 className="text-sm font-medium mb-1">Platform Adoption</h3>
        <p className="text-xs text-muted-foreground mb-4">Sorted by usage volume. Click a bar to view platform details.</p>
        <ResponsiveContainer width="100%" height={280}>
          <BarChart data={chartData} margin={{ top: 10, right: 10, bottom: 0, left: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="oklch(var(--border))" strokeOpacity={0.5} vertical={false} />
            <XAxis dataKey="name" tick={{ fill: "var(--chart-axis-tick)", fontSize: 11 }} axisLine={false} tickLine={false} />
            <YAxis tick={{ fill: "var(--chart-axis-tick)", fontSize: 11 }} tickFormatter={(v) => v >= 1000 ? `${(v / 1000).toFixed(0)}K` : v} axisLine={false} tickLine={false} />
            <Tooltip formatter={(value) => [Number(value).toLocaleString(), "Sessions"]} contentStyle={{ background: "oklch(var(--background))", border: "1px solid oklch(var(--border))", borderRadius: 8, fontSize: 12 }} cursor={{ fill: "oklch(var(--muted))", opacity: 0.3 }} />
            <Bar dataKey="sessions" radius={[6, 6, 0, 0]} barSize={48} onClick={(_, index) => setSelected(index)} className="cursor-pointer" activeBar={false}>
              {chartData.map((entry, i) => (
                <Cell key={i} fill={i === selected ? entry.color : `${entry.color}66`} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="rounded-xl bg-card shadow-sm p-5">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <div className="w-3 h-3 rounded" style={{ background: COLORS[selected % COLORS.length] }} />
              <h3 className="text-lg font-semibold">{platform.platform}</h3>
            </div>
            <div className="text-2xl font-bold tabular-nums" style={{ color: COLORS[selected % COLORS.length] }}>
              {(platform.sessions / 1000).toFixed(1)}K
              <span className="text-xs font-normal text-muted-foreground ml-1">sessions</span>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 border-t border-border pt-4 sm:grid-cols-2">
            {hasCostData && (
              <div className="text-center">
                <div className="text-lg font-bold text-success">${platform.avg_cost.toFixed(3)}</div>
                <div className="text-xs text-muted-foreground">Avg Cost/Task</div>
              </div>
            )}
            <div className="text-center">
              <div className="text-lg font-bold">{formatPercent(platform.success_rate)}</div>
              <div className="text-xs text-muted-foreground">Success Rate</div>
            </div>
          </div>

          <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div className="text-center">
              <div className="text-lg font-bold">{platform.avg_latency_ms.toFixed(0)}ms</div>
              <div className="text-xs text-muted-foreground">Avg Latency</div>
            </div>
            <div className="text-center">
              <div className="text-lg font-bold">{formatPercent(platform.error_rate)}</div>
              <div className="text-xs text-muted-foreground">Error Rate</div>
            </div>
            <div className="text-center">
              <div className="text-lg font-bold">{platform.users}</div>
              <div className="text-xs text-muted-foreground">Users</div>
            </div>
          </div>
        </div>
        <div className="rounded-xl bg-card shadow-sm p-5">
          <h3 className="text-sm font-medium mb-2">Platform signals</h3>
          <ResponsiveContainer width="100%" height={300}>
            <RadarChart data={radarData} cx="50%" cy="50%" outerRadius="80%">
              <PolarGrid stroke="oklch(var(--border))" strokeOpacity={0.5} />
              <PolarAngleAxis dataKey="metric" tick={{ fill: "oklch(var(--muted-foreground))", fontSize: 11 }} />
              <PolarRadiusAxis domain={[0, 100]} tick={false} axisLine={false} />
              <Radar
                dataKey="value"
                stroke={COLORS[selected % COLORS.length]}
                fill={COLORS[selected % COLORS.length]}
                fillOpacity={0.2}
                strokeWidth={2.5}
              />
            </RadarChart>
          </ResponsiveContainer>
        </div>
      </div>
      <div className="overflow-x-auto rounded-xl bg-card shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-muted/30">
              <th className="text-left p-3 font-medium">Platform</th>
              <th className="text-left p-3 font-medium">Sessions</th>
              <th className="text-left p-3 font-medium">Users</th>
              <th className="text-left p-3 font-medium">Avg Cost</th>
              <th className="text-left p-3 font-medium">Success</th>
              <th className="text-left p-3 font-medium">Latency</th>
            </tr>
          </thead>
          <tbody>
            {platforms.map((p, i) => (
              <tr
                key={p.platform}
                className={`border-b border-border cursor-pointer hover:bg-muted/20 ${i === selected ? "bg-muted/40" : ""}`}
                onClick={() => setSelected(i)}
              >
                <td className="p-3 font-medium">
                  <div className="flex items-center gap-2">
                    <div className="w-2.5 h-2.5 rounded-sm" style={{ background: COLORS[i % COLORS.length] }} />
                    {p.platform}
                  </div>
                </td>
                <td className="p-3 tabular-nums font-semibold">{p.sessions >= 1000 ? `${(p.sessions / 1000).toFixed(1)}K` : p.sessions}</td>
                <td className="p-3 tabular-nums">{p.users}</td>
                <td className="p-3 tabular-nums font-mono text-xs">${p.avg_cost.toFixed(3)}</td>
                <td className="p-3 tabular-nums">{formatPercent(p.success_rate)}</td>
                <td className="p-3 tabular-nums">{p.avg_latency_ms.toFixed(0)}ms</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ModelComparison />
    </div>
  );
}

function ModelComparison() {
  const { data: insights } = useExecStrategicInsights();
  const [selectedModel, setSelectedModel] = useState(0);

  const models = insights?.model_comparison ?? [];

  if (models.length === 0) return null;

  const model = models[selectedModel];

  return (
    <div className="space-y-6">
      <div className="rounded-xl bg-card shadow-sm p-5">
        <h3 className="text-sm font-medium mb-1">Model Provider Comparison</h3>
        <p className="text-xs text-muted-foreground mb-4">Observed usage, cost, tokens, and outcomes by AI model.</p>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <div className="space-y-2">
            {models.slice(0, 8).map((m, i) => (
              <div
                key={m.model}
                onClick={() => setSelectedModel(i)}
                className={`flex items-center justify-between p-3 rounded-md border cursor-pointer transition-colors ${
                  i === selectedModel ? "border-primary bg-primary/5" : "border-border hover:bg-muted/20"
                }`}
              >
                <div>
                  <p className="text-sm font-medium">{m.model}</p>
                  <p className="text-[11px] text-muted-foreground">{m.best_at}</p>
                </div>
                <div className="text-right">
                  <p className="text-sm font-semibold tabular-nums">${m.avg_cost.toFixed(4)}</p>
                  <p className="text-[11px] text-muted-foreground">{m.success_rate}% · {m.sessions} sessions</p>
                </div>
              </div>
            ))}
          </div>
          <div className="rounded-lg border border-border bg-surface-raised p-4">
            <p className="text-sm font-semibold">{model?.model}</p>
            <dl className="mt-4 grid grid-cols-2 gap-4 text-sm">
              <div>
                <dt className="text-xs text-muted-foreground">Sessions</dt>
                <dd className="mt-1 font-semibold tabular-nums">{model?.sessions.toLocaleString()}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Success rate</dt>
                <dd className="mt-1 font-semibold tabular-nums">{model?.success_rate}%</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Average cost</dt>
                <dd className="mt-1 font-semibold tabular-nums">${model?.avg_cost.toFixed(4)}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Average tokens</dt>
                <dd className="mt-1 font-semibold tabular-nums">{model?.avg_tokens.toLocaleString()}</dd>
              </div>
            </dl>
          </div>
        </div>
      </div>
      <div className="overflow-x-auto rounded-xl bg-card shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-muted/30">
              <th className="text-left p-3 font-medium">Model</th>
              <th className="text-left p-3 font-medium">Sessions</th>
              <th className="text-left p-3 font-medium">Avg Cost</th>
              <th className="text-left p-3 font-medium">Avg Tokens</th>
              <th className="text-left p-3 font-medium">Success Rate</th>
              <th className="text-left p-3 font-medium">Best For</th>
            </tr>
          </thead>
          <tbody>
            {models.map((m, i) => (
              <tr
                key={m.model}
                className={`border-b border-border cursor-pointer hover:bg-muted/20 ${i === selectedModel ? "bg-muted/40" : ""}`}
                onClick={() => setSelectedModel(i)}
              >
                <td className="p-3 font-medium">{m.model}</td>
                <td className="p-3 tabular-nums">{m.sessions.toLocaleString()}</td>
                <td className="p-3 tabular-nums font-mono text-xs">${m.avg_cost.toFixed(4)}</td>
                <td className="p-3 tabular-nums">{m.avg_tokens.toLocaleString()}</td>
                <td className="p-3 tabular-nums">{m.success_rate}%</td>
                <td className="p-3 text-xs text-muted-foreground">{m.best_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
