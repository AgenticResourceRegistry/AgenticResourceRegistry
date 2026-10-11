// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useState } from "react";
import { Link } from "@tanstack/react-router";
import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowLeft, ShieldCheck } from "lucide-react";
import { prReviews } from "@/lib/api";
import type { ReviewPolicy } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { PolicyFields, defaultPolicy } from "@/components/review/policy-form";

const defaults: ReviewPolicy = defaultPolicy;

export default function ReviewPolicyPage() {
  const { data, isPending, isError, refetch } = useQuery({ queryKey: ["review-policy", "organization"], queryFn: () => prReviews.orgPolicy() });
  const [value, setValue] = useState<ReviewPolicy>(defaults);
  useEffect(() => { if (data) setValue({ ...defaults, ...data }); }, [data]);
  const save = useMutation({ mutationFn: () => prReviews.setOrgPolicy(value), onSuccess: async () => { toast.success("Review policy saved and open reviews re-evaluated"); await refetch(); }, onError: (error: Error) => toast.error(error.message) });
  return <div className="mx-auto max-w-4xl space-y-5 px-4 py-6 sm:px-6"><Link to="/settings" className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" /> Settings</Link><header><h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight"><ShieldCheck className="size-6" /> Review policy</h1><p className="mt-2 text-sm text-muted-foreground">Control the publication gate for new and open reviews. Changes re-evaluate open reviews immediately.</p></header>{isPending ? <p className="text-sm text-muted-foreground">Loading policy…</p> : isError ? <p className="text-sm text-destructive">Could not load review policy.</p> : <form onSubmit={event => { event.preventDefault(); save.mutate(); }} className="rounded-xl border border-border bg-card p-5"><PolicyFields value={value} onChange={next => setValue(previous => ({ ...previous, ...next }))} selfApprovalHint="Applies to private teamspaces that opt in. Public items follow this organization rule." /><div className="mt-6 flex justify-end"><Button type="submit" disabled={save.isPending}>{save.isPending ? "Saving…" : "Save review policy"}</Button></div></form>}</div>;
}
