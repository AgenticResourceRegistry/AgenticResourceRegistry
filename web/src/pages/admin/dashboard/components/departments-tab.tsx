// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0


import { useContext } from "react";
import { useExecDepartments, useExecDeptTokens } from "@/hooks/use-api";
import { TrendingUp, TrendingDown, Minus } from "lucide-react";
import { DashboardRangeContext } from "../context";

export function DepartmentsTab() {
  const range = useContext(DashboardRangeContext);
  const { data: depts, isLoading: deptsLoading, isError: deptsError } = useExecDepartments(range);
  const { data: tokens, isLoading: tokensLoading } = useExecDeptTokens(range);

  if (deptsError) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <p className="text-sm text-muted-foreground">Failed to load department data. Check your connection and try again.</p>
      </div>
    );
  }

  if (deptsLoading) {
    return (
      <div className="space-y-6 pt-4">
        <div className="h-64 rounded-xl bg-card shadow-sm animate-pulse" />
      </div>
    );
  }

  const departments = depts?.departments ?? [];

  if (departments.length === 0) {
    return (
      <div className="space-y-6 pt-4">
        <div className="rounded-xl bg-card p-8 shadow-sm text-center text-muted-foreground">
          <p className="text-sm font-medium mb-2">No department data available</p>
          <p className="text-xs">
            Assign departments to users in Settings, or log in via SSO to auto-sync groups from your identity provider.
          </p>
        </div>
      </div>
    );
  }

  // Compute per-dept cost KPIs from token data
  const deptCosts = (tokens ?? []).map((t) => ({
    name: t.department,
    totalCost: t.cost_per_task * t.sessions_per_user * (departments.find((d) => d.department === t.department)?.user_count ?? 1),
    costPerTask: t.cost_per_task,
  })).sort((a, b) => b.totalCost - a.totalCost);

  const totalOrgCost = deptCosts.reduce((s, d) => s + d.totalCost, 0);

  return (
    <div className="space-y-6 pt-4">
      {deptCosts.length > 0 && (
        <div className="rounded-xl bg-card shadow-sm p-5">
          <h3 className="text-sm font-medium mb-3">Monthly AI Spend by Department</h3>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {deptCosts.slice(0, 8).map((d) => (
              <div key={d.name} className="rounded-lg bg-surface-raised p-3 text-center">
                <p className="text-xs text-muted-foreground truncate">{d.name}</p>
                <p className="text-lg font-bold tabular-nums">${d.totalCost >= 1000000 ? `${(d.totalCost / 1000000).toFixed(1)}M` : d.totalCost >= 1000 ? `${(d.totalCost / 1000).toFixed(1)}K` : d.totalCost.toFixed(0)}</p>
                <p className="text-[11px] text-muted-foreground">
                  {totalOrgCost > 0 ? `${((d.totalCost / totalOrgCost) * 100).toFixed(0)}% of total` : ""}
                </p>
              </div>
            ))}
          </div>
        </div>
      )}
      <div className="overflow-x-auto rounded-xl bg-card shadow-sm">
        <div className="p-4 border-b border-border">
          <h3 className="text-sm font-medium">Department Breakdown</h3>
          <p className="text-xs text-muted-foreground">
            {departments.length} departments · {departments.reduce((s, d) => s + d.user_count, 0)} total users
          </p>
        </div>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-muted/30">
              <th className="text-left p-3 font-medium">Department</th>
              <th className="text-left p-3 font-medium">Team Size</th>
              <th className="text-left p-3 font-medium">Agent Files</th>
              <th className="text-left p-3 font-medium">AI Utilization</th>
              <th className="text-left p-3 font-medium">Sessions/User</th>
            </tr>
          </thead>
          <tbody>
            {departments.map((dept) => (
              <tr key={dept.department} className="border-b border-border">
                <td className="p-3 font-semibold">{dept.department}</td>
                <td className="p-3 tabular-nums">{dept.user_count}</td>
                <td className="p-3 tabular-nums font-semibold text-primary">{dept.agent_count}</td>
                <td className="p-3">
                  <div className="flex items-center gap-2">
                    <div className="w-16 h-1.5 bg-muted rounded-full overflow-hidden">
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${dept.utilization_pct}%`,
                          background: dept.utilization_pct > 80 ? "oklch(var(--success))" : dept.utilization_pct > 50 ? "oklch(var(--primary))" : "oklch(var(--warning))",
                        }}
                      />
                    </div>
                    <span className="tabular-nums text-xs font-semibold">{dept.utilization_pct}%</span>
                  </div>
                </td>
                <td className="p-3 tabular-nums">{dept.sessions_per_user}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="overflow-x-auto rounded-xl bg-card shadow-sm">
        <div className="p-4 border-b border-border">
          <h3 className="text-sm font-medium">Token Usage by Department</h3>
          <p className="text-xs text-muted-foreground">Cost and usage efficiency per department</p>
        </div>
        {tokensLoading ? (
          <div className="p-4">
            <div className="h-40 animate-pulse bg-muted/30 rounded" />
          </div>
        ) : !tokens || tokens.length === 0 ? (
          <div className="p-8 text-center text-sm text-muted-foreground">
            No token usage data yet.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/30">
                <th className="text-left p-3 font-medium">Department</th>
                <th className="text-left p-3 font-medium">Tokens Used</th>
                <th className="text-left p-3 font-medium">Cost/Task</th>
                <th className="text-left p-3 font-medium">Sessions/User</th>
                <th className="text-left p-3 font-medium">Trend</th>
              </tr>
            </thead>
            <tbody>
              {tokens.map((t) => (
                <tr key={t.department} className="border-b border-border">
                  <td className="p-3 font-semibold">{t.department}</td>
                  <td className="p-3 tabular-nums">{t.tokens_used >= 1000000 ? `${(t.tokens_used / 1000000).toFixed(1)}M` : `${(t.tokens_used / 1000).toFixed(0)}K`}</td>
                  <td className="p-3 tabular-nums font-mono text-xs">${t.cost_per_task.toFixed(3)}</td>
                  <td className="p-3 tabular-nums">{t.sessions_per_user}</td>
                  <td className="p-3">
                    <div className="flex items-center gap-1">
                      {t.trend_pct > 0 ? (
                        <TrendingUp className="h-3 w-3 text-success" />
                      ) : t.trend_pct < 0 ? (
                        <TrendingDown className="h-3 w-3 text-destructive" />
                      ) : (
                        <Minus className="h-3 w-3 text-muted-foreground" />
                      )}
                      <span className={`text-xs tabular-nums ${t.trend_pct > 0 ? "text-success" : t.trend_pct < 0 ? "text-destructive" : "text-muted-foreground"}`}>
                        {t.trend_pct > 0 ? "+" : ""}{t.trend_pct}%
                      </span>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
