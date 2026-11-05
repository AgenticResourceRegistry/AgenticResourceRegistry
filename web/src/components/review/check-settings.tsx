// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Globe, Lock, Settings } from "lucide-react";
import { prReviews } from "@/lib/api";
import type { CheckSetting } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Switch } from "@/components/ui/switch";
import { ErrorState } from "@/components/shared/error-state";
import { TableSkeleton } from "@/components/shared/skeleton-layouts";

type Draft = Record<string, { enabled: boolean; required: boolean }>;

/**
 * Which automated checks run, and which of them block publishing.
 * Without a teamId this edits the organization (public) settings, which only admins may change.
 */
export function CheckSettingsPanel({ teamId }: { teamId?: string }) {
  const { data, isPending, isError, error, refetch } = useQuery({
    queryKey: ["review-checks", teamId ?? "organization"],
    queryFn: () => prReviews.checkSettings(teamId),
  });
  const [draft, setDraft] = useState<Draft>({});
  useEffect(() => {
    if (data) setDraft(Object.fromEntries(data.checks.filter(check => !check.locked).map(check => [check.id, { enabled: check.enabled, required: check.required }])));
  }, [data]);
  const save = useMutation({
    mutationFn: () => prReviews.setCheckSettings(draft, teamId),
    onSuccess: async () => { toast.success("Check settings saved. They apply to new revisions; use Rerun checks on an open review."); await refetch(); },
    onError: (err: Error) => toast.error(err.message),
  });
  if (isPending) return <TableSkeleton rows={4} cols={2} />;
  if (isError) return <ErrorState message={error?.message} onRetry={() => refetch()} />;

  const set = (id: string, patch: Partial<{ enabled: boolean; required: boolean }>) =>
    setDraft(previous => {
      const next = { ...previous[id], ...patch };
      if (!next.enabled) next.required = false; // A disabled check cannot block publishing.
      return { ...previous, [id]: next };
    });
  const row = (check: CheckSetting) => {
    const value = check.locked ? { enabled: true, required: true } : draft[check.id] ?? { enabled: check.enabled, required: check.required };
    return (
      <li key={check.id} className="flex flex-wrap items-start justify-between gap-3 border-b border-border py-3 last:border-0">
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
            {check.name}
            {check.network && <span className="inline-flex items-center gap-1 rounded border border-border px-1.5 py-0.5 text-2xs font-normal text-muted-foreground"><Globe className="size-3" /> Contacts external hosts</span>}
            {check.locked && <span className="inline-flex items-center gap-1 rounded border border-border px-1.5 py-0.5 text-2xs font-normal text-muted-foreground"><Lock className="size-3" /> Built in</span>}
          </p>
          <p className="mt-0.5 text-xs text-muted-foreground">{check.description}</p>
          <p className="mt-0.5 text-2xs text-muted-foreground">Applies to: <span className="capitalize">{check.subjects.join(", ")}</span></p>
        </div>
        <div className="flex shrink-0 items-center gap-5 text-xs">
          <label className="flex items-center gap-2">Run<Switch aria-label={`Run ${check.name}`} checked={value.enabled} disabled={check.locked} onCheckedChange={enabled => set(check.id, { enabled })} /></label>
          <label className="flex items-center gap-2">Blocks publishing<Switch aria-label={`${check.name} blocks publishing`} checked={value.required} disabled={check.locked || !value.enabled} onCheckedChange={required => set(check.id, { required })} /></label>
        </div>
      </li>
    );
  };
  return (
    <form onSubmit={event => { event.preventDefault(); save.mutate(); }}>
      <p className="mb-2 text-xs text-muted-foreground">
        These checks read and parse content; none of them runs submitted code. A check that cannot run reports skipped or error, never passed.
        Advisory checks inform reviewers; a check set to block publishing must pass first.
      </p>
      <ul>{data.checks.map(row)}</ul>
      <div className="mt-4 flex justify-end"><Button type="submit" disabled={save.isPending}>{save.isPending ? "Saving…" : "Save check settings"}</Button></div>
    </form>
  );
}

/** Gear button for the public (organization) check settings; render it for admins only. */
export function CheckSettingsDialog() {
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button type="button" variant="outline" size="icon" aria-label="Automated check settings" title="Automated check settings"><Settings className="size-4" /></Button>
      </DialogTrigger>
      <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Automated check settings</DialogTitle>
          <DialogDescription>Applies to everything published to the public registry. Teamspaces set their own for team-visible items.</DialogDescription>
        </DialogHeader>
        <CheckSettingsPanel />
      </DialogContent>
    </Dialog>
  );
}
