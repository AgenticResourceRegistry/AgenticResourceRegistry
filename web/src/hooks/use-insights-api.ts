// SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
// SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com>
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
  useQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import {
  admin,
  feedback,
  insights,
} from "@/lib/api";

// ── Insights models ─────────────────────────────────────────────────

export function useInsightsModelProviders() {
  return useQuery({
    queryKey: ["insights", "models", "providers"],
    queryFn: () => admin.insightsModelProviders(),
    staleTime: 60 * 60_000,
  });
}

export function useInsightsModels(provider: string) {
  return useQuery({
    queryKey: ["insights", "models", provider],
    queryFn: () => admin.insightsModels(provider),
    enabled: provider !== "",
    staleTime: 60 * 60_000,
  });
}

// ── Feedback ────────────────────────────────────────────────────────

export function useFeedback(type: string | undefined, id: string | undefined) {
  return useQuery({
    queryKey: ["feedback", type, id],
    enabled: !!type && !!id,
    queryFn: () => feedback.get(type!, id!),
  });
}

export function useSubmitFeedback() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: feedback.submit,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["feedback"] });
      toast.success("Review submitted");
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to submit review");
    },
  });
}

export function useMyFeedback(type: string | undefined, id: string | undefined, enabled = true) {
  return useQuery({
    queryKey: ["feedback", "mine", type, id],
    enabled: !!type && !!id && enabled,
    queryFn: () => feedback.mine(type!, id!),
    retry: (_count, err: unknown) => {
      const status = (err as { status?: number })?.status;
      return status !== 404;
    },
  });
}

export function useUpdateFeedback() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ feedbackId, ...body }: { feedbackId: string; rating?: number; comment?: string; anonymous?: boolean }) =>
      feedback.update(feedbackId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["feedback"] });
      toast.success("Review updated");
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to update review");
    },
  });
}

export function useDeleteFeedback() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (feedbackId: string) => feedback.remove(feedbackId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["feedback"] });
      toast.success("Review deleted");
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to delete review");
    },
  });
}

// ── Insights ───────────────────────────────────────────────────────

export function useComponentInsightReports(type: string, id: string, enabled: boolean) {
  return useQuery({
    queryKey: ["insights", "component-reports", type, id],
    queryFn: () => insights.componentReports(type, id),
    enabled: enabled && !!id,
    refetchInterval: (query) => query.state.data?.some((report) => report.status === "pending" || report.status === "running") ? 3000 : false,
  });
}

export function useGenerateComponentInsight() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { type: string; id: string; periodDays?: number }) =>
      insights.generateComponent(vars.type, vars.id, vars.periodDays),
    onSuccess: (_report, vars) => {
      void qc.invalidateQueries({ queryKey: ["insights", "component-reports", vars.type, vars.id] });
      toast.success("Component report queued");
    },
    onError: (error: Error) => toast.error(error.message || "Could not queue component report"),
  });
}

export function useInsightsStatus(enabled = true) {
  return useQuery({
    queryKey: ["insights", "status"],
    queryFn: () => insights.status(),
    enabled,
    staleTime: 0,
  });
}

export function useInsightSessionCount(agentId: string | undefined, agentVersion?: string | null, enabled = true) {
  return useQuery({
    queryKey: ["insights", "session-count", agentId, agentVersion],
    queryFn: () => insights.sessionCount(agentId!, agentVersion ?? undefined),
    enabled: !!agentId && enabled,
    refetchInterval: 30_000,
  });
}

export function useInsightReports(agentId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: ["insights", "reports", agentId],
    queryFn: () => insights.listReports(agentId!),
    enabled: !!agentId && enabled,
    refetchInterval: (query) => {
      const reports = query.state.data;
      if (Array.isArray(reports) && reports.some((r: { status: string }) => r.status === "pending" || r.status === "running")) {
        return 3000;
      }
      return false;
    },
  });
}

export function useInsightReport(agentId: string, reportId: string) {
  return useQuery({
    queryKey: ["insights", "report", agentId, reportId],
    queryFn: () => insights.getReport(agentId, reportId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === "pending" || status === "running") return 3000;
      return false;
    },
  });
}

export function useLegacyInsightReport(reportId: string) {
  return useQuery({
    queryKey: ["insights", "legacy-report", reportId],
    queryFn: () => insights.getReportById(reportId),
    refetchInterval: (query) => ["pending", "running"].includes(query.state.data?.status ?? "") ? 3000 : false,
  });
}

export function useGenerateInsight() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { agentId: string; periodDays?: number; agentVersion?: string; comparisonAgentVersion?: string }) =>
      insights.generate(vars.agentId, vars.periodDays, vars.agentVersion, vars.comparisonAgentVersion),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["insights", "reports", vars.agentId] });
      toast.success("Insight report queued");
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to generate insight");
    },
  });
}

export function useApplyInsightSuggestions() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { agentId: string; reportId: string; selection?: { config_indices?: number[]; feature_indices?: number[]; pattern_indices?: number[] } }) =>
      insights.applySuggestions(vars.agentId, vars.reportId, vars.selection),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["insights", "report", vars.agentId, vars.reportId] });
      toast.success("Suggestions applied: items added to review queue");
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to apply suggestions");
    },
  });
}
