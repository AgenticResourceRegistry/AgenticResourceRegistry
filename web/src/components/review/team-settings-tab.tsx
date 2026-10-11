// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Trash2 } from "lucide-react";
import { prReviews } from "@/lib/api";
import type { ReviewPolicy } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { ErrorState } from "@/components/shared/error-state";
import { TableSkeleton } from "@/components/shared/skeleton-layouts";
import { PolicyFields, defaultPolicy } from "@/components/review/policy-form";
import { useDeploymentConfig } from "@/hooks/use-deployment-config";
import { CheckSettingsPanel } from "@/components/review/check-settings";

/** Teamspace settings: review rules for private and public items, plus the danger zone. */
export function TeamSettingsTab({ teamId, teamName, canDelete, deleting, onRequestDelete }: {
  teamId: string;
  teamName: string;
  canDelete: boolean;
  deleting: boolean;
  onRequestDelete: () => void;
}) {
  const { experimentalReviewChecks } = useDeploymentConfig();
  const { data, isPending, isError, error, refetch } = useQuery({
    queryKey: ["review-policy", teamId],
    queryFn: () => prReviews.teamPolicy(teamId),
  });
  const [value, setValue] = useState<ReviewPolicy>(defaultPolicy);
  useEffect(() => { setValue({ ...defaultPolicy, ...(data ?? {}) }); }, [data]);
  const save = useMutation({
    mutationFn: () => prReviews.setTeamPolicy(teamId, value),
    onSuccess: async () => { toast.success("Teamspace settings saved and open reviews re-evaluated"); await refetch(); },
    onError: (err: Error) => toast.error(err.message),
  });
  const separatePublic = Boolean(value.public);

  if (isPending) return <TableSkeleton rows={3} cols={3} />;
  if (isError) return <ErrorState message={error?.message} onRetry={() => refetch()} />;
  return (
    <div className="space-y-6">
      <form onSubmit={event => { event.preventDefault(); save.mutate(); }} className="space-y-6">
        <section className="rounded-xl border border-border bg-card p-5">
          <h2 className="text-base font-semibold">Team-visible items</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Rules for work published with team visibility. Only this teamspace's reviewers can see and decide these reviews.
          </p>
          <div className="mt-4">
            <PolicyFields
              value={value}
              onChange={next => setValue(previous => ({ ...previous, ...next }))}
              selfApprovalHint="Lets authors on a small team approve and publish their own team-visible work."
            />
          </div>
        </section>

        <section className="rounded-xl border border-border bg-card p-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="text-base font-semibold">Public namespace</h2>
              <p className="mt-1 text-xs text-muted-foreground">
                Rules for what {teamName} publishes to the public registry. They can only tighten the organization policy:
                self-approval and automatic publishing take effect only where the organization allows them.
              </p>
            </div>
            <Switch
              aria-label="Use separate rules for the public namespace"
              checked={separatePublic}
              onCheckedChange={on => setValue(previous => ({ ...previous, public: on ? { ...defaultPolicy, required_approvals: { ...previous.required_approvals } } : null }))}
            />
          </div>
          {separatePublic ? (
            <div className="mt-4">
              <PolicyFields
                value={value.public ?? defaultPolicy}
                onChange={next => setValue(previous => ({ ...previous, public: next }))}
                selfApprovalHint="Counted only when the organization also counts self-approval."
              />
            </div>
          ) : (
            <p className="mt-3 text-xs text-muted-foreground">Public items use the team-visible rules above, combined with the organization policy.</p>
          )}
        </section>

        <div className="flex justify-end">
          <Button type="submit" disabled={save.isPending}>{save.isPending ? "Saving…" : "Save settings"}</Button>
        </div>
      </form>

      {experimentalReviewChecks && <section className="rounded-xl border border-border bg-card p-5">
        <h2 className="text-base font-semibold">Automated checks</h2>
        <p className="mt-1 mb-3 text-xs text-muted-foreground">Which checks run on team-visible items, and which must pass before publishing. Public items use the organization's settings.</p>
        <CheckSettingsPanel teamId={teamId} />
      </section>}

      {canDelete && (
        <section className="rounded-xl border border-destructive/40 bg-destructive/5 p-5">
          <h2 className="text-base font-semibold text-destructive">Danger zone</h2>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-4">
            <p className="text-sm text-muted-foreground">
              Permanently remove this empty teamspace and its membership records. Teamspaces that own registry items cannot be deleted.
            </p>
            <Button type="button" variant="destructive" size="sm" onClick={onRequestDelete} disabled={deleting}>
              <Trash2 className="mr-1.5 h-3.5 w-3.5" /> Delete teamspace
            </Button>
          </div>
        </section>
      )}
    </div>
  );
}
