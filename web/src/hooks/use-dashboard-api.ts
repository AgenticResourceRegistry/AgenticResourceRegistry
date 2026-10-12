// SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
// SPDX-FileCopyrightText: 2026 Harishankar <harishankar0301@gmail.com>
// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-FileCopyrightText: 2026 Shreem Seth <shreemseth26@gmail.com>
// SPDX-FileCopyrightText: 2026 SrihariLegend <sriharilegend23@gmail.com>
// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-License-Identifier: Apache-2.0


import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import {
  dashboard,
  exec,
} from "@/lib/api";
import type { CostOpportunityAction } from "@/lib/types";

// ── Dashboard ───────────────────────────────────────────────────────

export function useOverviewStats(range?: string) {
  return useQuery({ queryKey: ["overview", "stats", range], queryFn: () => dashboard.stats(range) });
}

export function useTopMcps() {
  return useQuery({ queryKey: ["overview", "top-mcps"], queryFn: dashboard.topMcps });
}

export function useTopAgents(limit?: number) {
  return useQuery({ queryKey: ["overview", "top-agents", limit], queryFn: () => dashboard.topAgents(limit) });
}

export function useTrends(range?: string) {
  return useQuery({ queryKey: ["overview", "trends", range], queryFn: () => dashboard.trends(range) });
}

// ── New Dashboard Hooks ─────────────────────────────────────────────

export function useTokenStats(range?: string) {
  return useQuery({ queryKey: ['dashboard', 'tokens', range], queryFn: () => dashboard.tokenStats(range) });
}
export function useHarnessUsage() {
  return useQuery({ queryKey: ['dashboard', 'harness-usage'], queryFn: dashboard.harnessUsage });
}
// ── Exec Dashboard ─────────────────────────────────────────────────

export function useExecAdoption() {
  return useQuery({ queryKey: ["exec", "adoption"], queryFn: exec.adoption });
}

export function useExecAgentCounts() {
  return useQuery({ queryKey: ["exec", "agent-counts"], queryFn: exec.agentCounts });
}

export function useExecUsageByCategory(range?: string) {
  return useQuery({ queryKey: ["exec", "usage-by-category", range], queryFn: () => exec.usageByCategory(range) });
}

export function useExecPlatformCoverage() {
  return useQuery({ queryKey: ["exec", "platform-coverage"], queryFn: exec.platformCoverage });
}

export function useExecPlatforms() {
  return useQuery({ queryKey: ["exec", "platforms"], queryFn: exec.platforms });
}

export function useExecVelocity() {
  return useQuery({ queryKey: ["exec", "velocity"], queryFn: exec.velocity });
}

export function useExecTopAgents(limit?: number) {
  return useQuery({ queryKey: ["exec", "top-agents", limit], queryFn: () => exec.topAgents(limit) });
}

export function useExecDepartments(range?: string) {
  return useQuery({ queryKey: ["exec", "departments", range], queryFn: () => exec.departments(range) });
}

export function useExecDeptTokens(range?: string) {
  return useQuery({ queryKey: ["exec", "dept-tokens", range], queryFn: () => exec.deptTokens(range) });
}

export function useCostIntelligence() {
  return useQuery({ queryKey: ["exec", "cost-intelligence"], queryFn: () => exec.costIntelligence() });
}

export function useRecomputeCostIntelligence() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => exec.costIntelligence(true),
    onSuccess: (data) => qc.setQueryData(["exec", "cost-intelligence"], data),
    onError: () => toast.error("Could not recompute cost data. Telemetry may be unavailable."),
  });
}

export function useCostOpportunityAction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: CostOpportunityAction }) => exec.costOpportunityAction(id, body),
    onSuccess: async () => {
      const fresh = await exec.costIntelligence(true);
      qc.setQueryData(["exec", "cost-intelligence"], fresh);
    },
    onError: (error: Error) => toast.error(error.message || "That change could not be saved."),
  });
}

export function useExecConfig() {
  return useQuery({ queryKey: ["exec", "config"], queryFn: exec.config });
}

export function useExecStrategicInsights() {
  return useQuery({ queryKey: ["exec", "strategic-insights"], queryFn: exec.strategicInsights });
}

export function useExecDeveloperBreakdown(limit?: number) {
  return useQuery({ queryKey: ["exec", "developer-breakdown", limit], queryFn: () => exec.developerBreakdown(limit) });
}

export function useExecInactivityAlerts() {
  return useQuery({ queryKey: ["exec", "inactivity-alerts"], queryFn: exec.inactivityAlerts });
}

export function useExecTimeToValue() {
  return useQuery({ queryKey: ["exec", "time-to-value"], queryFn: exec.timeToValue });
}

export function useExecAIInsights() {
  return useQuery({
    queryKey: ["exec", "ai-insights"],
    queryFn: exec.aiInsights,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
}

export function useGenerateExecAIInsights() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: exec.generateAiInsights,
    onSuccess: (data) => {
      qc.setQueryData(["exec", "ai-insights"], data);
      toast.success("Executive insight report generated");
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to generate executive insight report");
    },
  });
}
