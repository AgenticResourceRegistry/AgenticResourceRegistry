// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-FileCopyrightText: 2026 Deepesh Kumar <akadeepesh1710@gmail.com>
// SPDX-License-Identifier: Apache-2.0


import { Button } from "@/components/ui/button";
import { useExecAIInsights, useExecDeveloperBreakdown, useGenerateExecAIInsights } from "@/hooks/use-api";
import { Zap, AlertTriangle, TrendingUp, Users, Cpu, Crown, Loader2, RefreshCw } from "lucide-react";
import { CardErrorBoundary } from "./card-error-boundary";

const effortColors: Record<string, { bg: string; text: string; label: string }> = {
  low: { bg: "bg-success/10", text: "text-success", label: "Quick Win" },
  medium: { bg: "bg-warning/10", text: "text-warning", label: "Medium Effort" },
  high: { bg: "bg-destructive/10", text: "text-destructive", label: "High Effort" },
};

function DeveloperBreakdown() {
  const { data, isLoading } = useExecDeveloperBreakdown(20);

  if (isLoading) {
    return <div className="h-48 rounded-xl bg-card shadow-sm animate-pulse" />;
  }

  if (!data || data.active_developers === 0) return null;

  return (
    <div className="rounded-xl bg-card shadow-sm p-5">
      <div className="flex items-center gap-2 mb-1">
        <Crown className="h-4 w-4 text-warning" />
        <span className="text-[11px] font-semibold uppercase tracking-wide text-warning">Developer Activity</span>
      </div>
      <h3 className="text-sm font-semibold mb-1">
        {data.active_developers} active developers — top 20% drive {data.top_20_value_pct}% of value
      </h3>
      <p className="text-xs text-muted-foreground mb-4">
        {data.total_developers} total developers in org, {data.active_developers} active in the last 30 days.
      </p>

      <div className="overflow-x-auto rounded-md bg-card shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-muted/30">
              <th className="text-left p-2.5 font-medium text-xs">#</th>
              <th className="text-left p-2.5 font-medium text-xs">Developer</th>
              <th className="text-left p-2.5 font-medium text-xs">Department</th>
              <th className="text-left p-2.5 font-medium text-xs">Sessions</th>
              <th className="text-left p-2.5 font-medium text-xs">Tokens</th>
              <th className="text-left p-2.5 font-medium text-xs">Cost</th>
              <th className="text-left p-2.5 font-medium text-xs">Percentile</th>
            </tr>
          </thead>
          <tbody>
            {data.developers.map((dev, i) => (
              <tr key={dev.user_id} className="border-b border-border last:border-0">
                <td className="p-2.5 text-muted-foreground font-mono text-xs">{i + 1}</td>
                <td className="p-2.5 font-medium">{dev.name}</td>
                <td className="p-2.5 text-muted-foreground">{dev.department}</td>
                <td className="p-2.5 tabular-nums">{dev.sessions.toLocaleString()}</td>
                <td className="p-2.5 tabular-nums text-xs">{dev.tokens_consumed >= 1000000 ? `${(dev.tokens_consumed / 1000000).toFixed(1)}M` : `${(dev.tokens_consumed / 1000).toFixed(0)}K`}</td>
                <td className="p-2.5 tabular-nums text-xs font-mono">${dev.cost.toFixed(3)}</td>
                <td className="p-2.5">
                  <span className={`text-xs px-1.5 py-0.5 rounded ${
                    dev.percentile >= 80 ? "bg-success/10 text-success" :
                    dev.percentile >= 50 ? "bg-info/10 text-info" :
                    "bg-muted text-muted-foreground"
                  }`}>
                    Top {100 - dev.percentile}%
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function InsightsTab() {
  const { data: insights, isLoading, isError } = useExecAIInsights();
  const generateInsights = useGenerateExecAIInsights();
  const generatedAt = insights?.generated_at
    ? new Date(insights.generated_at).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
    })
    : null;

  if (isError) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <p className="text-sm text-muted-foreground">Failed to load insights. Check your connection and try again.</p>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="space-y-6 pt-4">
        <div className="rounded-xl bg-card shadow-sm p-8 flex flex-col items-center justify-center text-center">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground mb-3" />
          <p className="text-sm font-medium">Loading cached AI insights...</p>
          <p className="text-xs text-muted-foreground mt-1">No new report is generated until you request one.</p>
        </div>
      </div>
    );
  }

  if (!insights || !insights.generated) {
    return (
      <div className="space-y-6 pt-4">
        <div className="rounded-xl bg-card p-8 shadow-sm text-center text-muted-foreground">
          <Cpu className="h-8 w-8 mx-auto mb-3 opacity-50" />
          <p className="text-sm font-medium mb-1">No cached AI insights report</p>
          <p className="text-xs mb-4">Generate a report when you want fresh executive recommendations.</p>
          <Button
            size="sm"
            disabled={generateInsights.isPending}
            onClick={() => generateInsights.mutate()}
          >
            {generateInsights.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            Generate report
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6 pt-4">
      <div className="rounded-xl bg-card shadow-sm p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-3">
              <div className="w-2.5 h-2.5 rounded-full bg-success" />
              <h2 className="text-base font-semibold">AI Insights</h2>
            </div>
            <p className="text-sm text-muted-foreground mt-1">
              Showing the cached executive report{generatedAt ? ` generated ${generatedAt}` : ""}.
            </p>
          </div>
          <Button
            size="sm"
            variant="outline"
            disabled={generateInsights.isPending}
            onClick={() => generateInsights.mutate()}
          >
            {generateInsights.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            Generate new report
          </Button>
        </div>
      </div>
      {insights.quick_wins.length > 0 && (
        <div className="rounded-xl bg-card shadow-sm p-5">
          <div className="flex items-center gap-2 mb-1">
            <Zap className="h-4 w-4 text-success" />
            <span className="text-[11px] font-semibold uppercase tracking-wide text-success">Quick Wins</span>
          </div>
          <h3 className="text-sm font-semibold mb-1">Immediate savings with minimal effort</h3>
          <p className="text-xs text-muted-foreground mb-4">
            Combined potential: {insights.quick_wins.map(w => w.estimated_savings).join(" + ")} saved
          </p>
          <div className="space-y-3">
            {insights.quick_wins.map((win, i) => {
              const effort = effortColors[win.effort] ?? effortColors.medium;
              return (
                <div key={i} className="rounded-md bg-card shadow-sm p-4">
                  <div className="flex items-center justify-between mb-2">
                    <h4 className="text-sm font-medium">{win.title}</h4>
                    <span className={`text-[10px] px-2 py-0.5 rounded ${effort.bg} ${effort.text} font-semibold`}>
                      {effort.label} · {win.estimated_savings}
                    </span>
                  </div>
                  <p className="text-xs text-muted-foreground leading-relaxed">{win.detail}</p>
                </div>
              );
            })}
          </div>
        </div>
      )}
      {insights.adoption_gaps.length > 0 && (
        <div className="rounded-xl bg-card shadow-sm p-5">
          <div className="flex items-center gap-2 mb-1">
            <AlertTriangle className="h-4 w-4 text-destructive" />
            <span className="text-[11px] font-semibold uppercase tracking-wide text-destructive">Adoption Gaps</span>
            <span className="text-[10px] px-2 py-0.5 rounded bg-success/10 text-success font-semibold">High Impact</span>
          </div>
          <div className="space-y-4 mt-3">
            {insights.adoption_gaps.map((gap, i) => (
              <div key={i}>
                <h4 className="text-sm font-semibold mb-1">{gap.title}</h4>
                <p className="text-sm text-muted-foreground leading-relaxed">{gap.detail}</p>
              </div>
            ))}
          </div>
        </div>
      )}
      {insights.platform_insight.title && insights.platform_insight.title !== "Insufficient data" && (
        <div className="rounded-xl bg-card shadow-sm p-5">
          <div className="flex items-center gap-2 mb-1">
            <Users className="h-4 w-4 text-violet-400" />
            <span className="text-[11px] font-semibold uppercase tracking-wide text-violet-400">harness Performance</span>
            <span className="text-[10px] px-2 py-0.5 rounded bg-success/10 text-success font-semibold">High Impact</span>
          </div>
          <h4 className="text-sm font-semibold mt-2 mb-1">{insights.platform_insight.title}</h4>
          <p className="text-sm text-muted-foreground leading-relaxed">{insights.platform_insight.detail}</p>
        </div>
      )}
      {insights.automation_opportunity.title && insights.automation_opportunity.title !== "Insufficient data" && (
        <div className="rounded-lg border border-dashed border-warning/30 bg-warning/5 p-5">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-warning">Automation Opportunity</span>
          </div>
          <h4 className="text-sm font-semibold mb-1">{insights.automation_opportunity.title}</h4>
          <p className="text-sm text-muted-foreground leading-relaxed">{insights.automation_opportunity.detail}</p>
        </div>
      )}
      {insights.model_insight.title && insights.model_insight.title !== "Insufficient data" && (
        <div className="rounded-xl bg-card shadow-sm p-5">
          <div className="flex items-center gap-2 mb-1">
            <TrendingUp className="h-4 w-4 text-info" />
            <span className="text-[11px] font-semibold uppercase tracking-wide text-info">Model Provider</span>
            <span className="text-[10px] px-2 py-0.5 rounded bg-success/10 text-success font-semibold">High Impact</span>
          </div>
          <h4 className="text-sm font-semibold mt-2 mb-1">{insights.model_insight.title}</h4>
          <p className="text-sm text-muted-foreground leading-relaxed">{insights.model_insight.detail}</p>
        </div>
      )}
      {insights.usage_pattern.title && insights.usage_pattern.title !== "Insufficient data" && (
        <div className="rounded-xl bg-card shadow-sm p-5">
          <div className="flex items-center gap-2 mb-1">
            <Crown className="h-4 w-4 text-warning" />
            <span className="text-[11px] font-semibold uppercase tracking-wide text-warning">Usage Pattern</span>
          </div>
          <h4 className="text-sm font-semibold mt-2 mb-1">{insights.usage_pattern.title}</h4>
          <p className="text-sm text-muted-foreground leading-relaxed">{insights.usage_pattern.detail}</p>
        </div>
      )}
      <CardErrorBoundary>
        <DeveloperBreakdown />
      </CardErrorBoundary>
    </div>
  );
}
