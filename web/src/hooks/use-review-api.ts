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


import { useEffect, useRef } from "react";
import {
  useQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import {
  registry,
  review,
  type RegistryType,
} from "@/lib/api";

// ── Review ──────────────────────────────────────────────────────────

export function useReviewList(typeFilter?: string) {
  const params = typeFilter ? { type: typeFilter } : undefined;
  return useQuery({
    queryKey: ["review", params],
    queryFn: async () => {
      const [components, agents] = await Promise.all([
        review.list(params),
        review.listAgents(),
      ]);
      return [...agents, ...components];
    },
  });
}

export function useReviewAction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; type?: string; action: "approve" | "reject"; reason?: string; category?: string }) => {
      if (vars.type === "agent") {
        return vars.action === "approve"
          ? review.approveAgent(vars.id, vars.category ? { category: vars.category } : undefined)
          : review.rejectAgent(vars.id, { reason: vars.reason ?? "" });
      }
      return vars.action === "approve"
        ? review.approve(vars.id)
        : review.reject(vars.id, { reason: vars.reason ?? "" });
    },
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["review"] });
      qc.invalidateQueries({ queryKey: ["registry"] });
      toast.success(vars.action === "approve" ? "Submission approved" : "Submission rejected");
    },
    onError: (err: Error) => {
      toast.error(err.message || "Review action failed");
    },
  });
}
// ── Bundle Review ──────────────────────────────────────────────────

export function useBundleReviewAction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; action: "approve" | "reject"; reason?: string }) =>
      vars.action === "approve"
        ? review.approveBundle(vars.id)
        : review.rejectBundle(vars.id, { reason: vars.reason ?? "" }),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["review"] });
      qc.invalidateQueries({ queryKey: ["registry"] });
      toast.success(vars.action === "approve" ? "Bundle approved" : "Bundle rejected");
    },
    onError: (err: Error) => {
      toast.error(err.message || "Bundle review action failed");
    },
  });
}

// ── Review (agents-only list) ──────────────────────────────────────

export function useReviewAgents() {
  return useQuery({
    queryKey: ["review", "agents"],
    queryFn: () => review.listAgents(),
  });
}

export function useReviewDetail(id: string | undefined) {
  return useQuery({
    queryKey: ["review", "detail", id],
    enabled: !!id,
    queryFn: () => review.get(id!),
  });
}

export function useSkillVersionReview(id: string | undefined, versionId: string | undefined) {
  return useQuery({
    queryKey: ["review", "skill", id, versionId],
    enabled: !!id && !!versionId,
    queryFn: () => review.getSkillVersion(id!, versionId!),
  });
}

export function useSkillVersionDecision() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; versionId: string; revision: string; action: "approve" | "reject"; reason?: string; gitBaseAcknowledged?: boolean }) =>
      review.decideSkillVersion(vars.id, vars.versionId, vars.action, vars.revision, vars.reason, vars.gitBaseAcknowledged),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["review"] });
      toast.success(vars.action === "approve" ? "Skill version approved" : "Skill version rejected");
    },
    onError: (err: Error) => toast.error(err.message || "Skill version changed; refresh before review"),
  });
}

export function useRelatedSkills(id: string | undefined) {
  return useQuery({
    queryKey: ["review", "related-skills", id],
    enabled: !!id,
    queryFn: () => review.relatedSkills(id!).then((r) => r.skills),
  });
}

export function useApproveWithSkills() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; skillIds: string[] }) =>
      review.approveWithSkills(vars.id, { skill_ids: vars.skillIds }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["review"] });
      toast.success("MCP and related skills approved");
    },
    onError: (err: Error) => {
      toast.error(err.message || "Bulk approve failed");
    },
  });
}

/**
 * Pending queue for one teamspace, used by the teamspace Review tab.
 *
 * Only a team owner or team reviewer may call this, so `enabled` must carry the
 * team-role check: asking without the role earns a 403 rather than an empty
 * list. The key stays under "review" so useReviewAction's invalidation reaches it.
 */
export function useTeamReviewQueue(teamId: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["review", "team", teamId],
    enabled: !!teamId && enabled,
    queryFn: () => review.listForTeam(teamId!),
  });
}

export function useReviewComponents(typeFilter?: string) {
  const params: Record<string, string> = { tab: "components" };
  if (typeFilter) params.type = typeFilter;
  return useQuery({
    queryKey: ["review", "components", params],
    queryFn: () => review.list(params),
  });
}

export function useReviewSubscription() {
  const qc = useQueryClient();
  const listDebounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    let unsubscribe: (() => void) | undefined;

    import("@/lib/graphql-ws").then(({ subscribeToReviewUpdates }) => {
      unsubscribe = subscribeToReviewUpdates(() => {
        // Debounce the list refetch (rapid approve/reject actions)
        clearTimeout(listDebounceRef.current);
        listDebounceRef.current = setTimeout(() => {
          qc.invalidateQueries({ queryKey: ["review"] });
        }, 300);
      });
    });

    return () => {
      clearTimeout(listDebounceRef.current);
      unsubscribe?.();
    };
  }, [qc]);
}
