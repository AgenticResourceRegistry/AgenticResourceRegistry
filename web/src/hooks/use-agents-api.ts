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
  useQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import { draftSavedToast, reviewToast } from "@/lib/review-toast";
import {
  dashboard,
  registry,
  feedback,
  bulk,
  admin,
  remoteAgents,
  discovery,
} from "@/lib/api";
import type { ForkRequest, LeaderboardWindow, SetRecommendedRequest } from "@/lib/types";

// ── Agent-specific ──────────────────────────────────────────────────

export function useForkAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ForkRequest }) => registry.fork("agents", id, body),
    onSuccess: (_fork, { id }) => {
      qc.invalidateQueries({ queryKey: ["registry", "agents"] });
      qc.invalidateQueries({ queryKey: ["registry", "agents", "my"] });
      qc.invalidateQueries({ queryKey: ["registry", "agents", id] });
      toast.success("Agent File fork created as a draft");
    },
  });
}

export function useAgentForks(id: string, page: number, enabled = true) {
  return useQuery({
    queryKey: ["registry", "agents", id, "forks", page],
    queryFn: () => registry.forks("agents", id, page),
    enabled: enabled && !!id,
  });
}

export function useMyAgents(enabled = true) {
  return useQuery({
    queryKey: ["registry", "agents", "my"],
    queryFn: () => registry.my(),
    enabled,
  });
}

export function useRemoteAgents(enabled = true) {
  return useQuery({
    queryKey: ["ard", "imports"],
    queryFn: () => remoteAgents.list(),
    enabled,
  });
}

/** ARD search, disabled until the query has a couple of characters. */
export function useAgentDiscoverySearch(text: string) {
  const trimmed = text.trim();
  return useQuery({
    queryKey: ["discovery", "agents", trimmed],
    queryFn: () => discovery.searchAgents(trimmed),
    enabled: trimmed.length >= 2,
    staleTime: 60 * 1000,
    retry: false,
  });
}

export function useArchivedAgents(enabled = true) {
  return useQuery({
    queryKey: ["registry", "agents", "archived"],
    queryFn: () => registry.archived(),
    enabled,
  });
}

export function useDeletedAgents(enabled = true) {
  return useQuery({
    queryKey: ["registry", "agents", "deleted"],
    queryFn: () => registry.deletedAgents(),
    enabled,
  });
}

export function useAgentResolve(id: string) {
  return useQuery({
    queryKey: ["agent-resolve", id],
    queryFn: () => registry.resolve(id),
    enabled: !!id,
  });
}

export function useAgentDownloads(id: string) {
  return useQuery({
    queryKey: ["agent-downloads", id],
    queryFn: () => registry.downloads(id),
    enabled: !!id,
  });
}

export function useLeaderboard(window?: LeaderboardWindow, limit?: number, user?: string) {
  return useQuery({
    queryKey: ["leaderboard", window, limit, user],
    queryFn: () => dashboard.leaderboard(window, limit, user),
  });
}

export function useComponentLeaderboard(window?: LeaderboardWindow, limit?: number, user?: string) {
  return useQuery({
    queryKey: ["component-leaderboard", window, limit, user],
    queryFn: () => dashboard.componentLeaderboard(window, limit, user),
  });
}

export function useAgentValidation() {
  return useMutation({
    mutationFn: registry.validate,
  });
}

export function useFeedbackSummary(listingId: string | undefined) {
  return useQuery({
    queryKey: ["feedback", "summary", listingId],
    enabled: !!listingId,
    queryFn: () => feedback.summary(listingId!),
  });
}
// ── Archive ────────────────────────────────────────────────────────

export function useArchiveAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => registry.archive(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["registry", "agents"] });
      toast.success("Agent File archived");
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to archive Agent File");
    },
  });
}

export function useUnarchiveAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => registry.unarchive(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["registry", "agents"] });
      toast.success("Agent File restored");
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to restore Agent File");
    },
  });
}

export function useRestoreDeletedAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; name?: string }) => registry.restoreDeletedAgent(vars.id, vars.name ? { name: vars.name } : undefined),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["registry", "agents"] });
      toast.success("Agent File restored");
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to restore Agent File");
    },
  });
}

export function useDeleteAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => registry.delete("agents", id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["registry", "agents"] });
      toast.success("Agent File deleted");
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to delete Agent File");
    },
  });
}

// ── Draft ──────────────────────────────────────────────────────────

export function useSaveDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: unknown) => registry.draft(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["registry", "agents"] });
      toast.success("Draft saved");
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to save draft");
    },
  });
}

export function useUpdateDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; body: unknown }) => registry.updateDraft(vars.id, vars.body),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ["registry", "agents"] });
      qc.invalidateQueries({ queryKey: ["pr-review"] });
      draftSavedToast(result);
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to update draft");
    },
  });
}

export function useUpdateAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; body: unknown }) => registry.updateAgent(vars.id, vars.body),
    onSuccess: (_data: unknown, vars: { id: string; body: unknown }) => {
      qc.invalidateQueries({ queryKey: ["registry", "agents"] });
      qc.invalidateQueries({ queryKey: ["registry", "agents", vars.id] });
      toast.success("Agent File updated successfully");
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to update Agent File");
    },
  });
}

export function useSubmitDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => registry.submitDraft(id),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ["registry", "agents"] });
      qc.invalidateQueries({ queryKey: ["pr-review"] });
      qc.invalidateQueries({ queryKey: ["pr-reviews"] });
      reviewToast("Agent File submitted for review", result);
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to submit draft");
    },
  });
}

// ── Version ────────────────────────────────────────────────────────

export function useCreateAgentVersion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { agentId: string; body: unknown }) =>
      registry.createVersion(vars.agentId, vars.body),
    onSuccess: (result, vars) => {
      qc.invalidateQueries({ queryKey: ["agent-versions", vars.agentId] });
      qc.invalidateQueries({ queryKey: ["registry", "agents", vars.agentId] });
      reviewToast("New version submitted for review", result);
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to release version");
    },
  });
}

export function useVersionSuggestions(id: string | undefined) {
  return useQuery({
    queryKey: ["version-suggestions", id],
    enabled: !!id,
    queryFn: () => registry.versionSuggestions(id!),
  });
}

// ── Bulk ───────────────────────────────────────────────────────────

export function useBulkCreateAgents() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: bulk.createAgents,
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["registry", "agents"] });
      reviewToast(`Submitted ${data.created} Agent Files for review`, data.results.find(item => item.review_number) ?? {});
    },
    onError: (err: Error) => {
      toast.error(err.message || "Bulk create failed");
    },
  });
}

// ── Recommended ────────────────────────────────────────────────────

export function useSetRecommended() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: SetRecommendedRequest) =>
      admin.setRecommended(vars),
    onSuccess: (_data, vars) => {
      // Keep the toggle pending until the active detail query reflects the new value.
      toast.success(vars.recommended ? "Marked as recommended" : "Recommendation removed");
      return qc.invalidateQueries({ queryKey: ["registry"] });
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to update recommendation");
    },
  });
}
