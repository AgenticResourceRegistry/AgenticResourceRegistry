// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { Link, useParams } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { ArrowLeft, Users } from "lucide-react";
import { teams } from "@/lib/api";
import { useAuthGuard } from "@/hooks/use-auth";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

export default function TeamspaceReview() {
  useAuthGuard();
  const { teamId } = useParams({ from: "/_authed/_admin/review/teamspace/$teamId" });
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const { data, isPending, isError } = useQuery({ queryKey: ["review", "teamspaces"], queryFn: teams.visibilityRequests });
  const request = data?.find(item => item.team_id === teamId);
  const decision = useMutation({ mutationFn: (approve: boolean) => approve ? teams.approveVisibility(teamId) : teams.rejectVisibility(teamId, note.trim() || undefined), onSuccess: () => { qc.invalidateQueries({ queryKey: ["review", "teamspaces"] }); toast.success("Teamspace request decided"); }, onError: (error: Error) => toast.error(error.message) });
  return <div className="mx-auto max-w-7xl space-y-5 px-4 py-6 sm:px-6"><Link to="/review" search={{ tab: "teamspaces" }} className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" /> Review queue</Link>{isPending ? <p className="text-sm text-muted-foreground">Loading request…</p> : isError || !request ? <div className="rounded-xl border border-border bg-card p-8"><h1 className="text-xl font-semibold">Request unavailable</h1><p className="mt-2 text-sm text-muted-foreground">This teamspace request may already have been decided.</p></div> : <><header><h1 className="text-2xl font-semibold tracking-tight">@{request.handle} requests public visibility</h1><p className="mt-2 text-sm text-muted-foreground"><span className="rounded-full bg-info/10 px-2 py-0.5 text-info">Open</span> Requested {new Date(request.requested_at).toLocaleString()}</p></header><div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_270px]"><main className="space-y-5"><section className="rounded-xl border border-border bg-card p-5"><h2 className="mb-2 text-sm font-semibold">Visibility request</h2><p className="text-sm">{request.name} would like to be visible to everyone in the registry.</p><p className="mt-2 text-sm text-muted-foreground">{request.description || "No description provided."}</p></section><section className="rounded-xl border border-border bg-card p-5"><h2 className="text-sm font-semibold">Decision</h2><p className="my-2 text-xs text-muted-foreground">Approval makes this teamspace public. Rejection keeps it private. You can include a note when rejecting.</p><Textarea aria-label="Decision note" value={note} onChange={e => setNote(e.target.value)} placeholder="Optional note for the requester" rows={3} /><div className="mt-3 flex gap-2"><Button disabled={decision.isPending} onClick={() => decision.mutate(true)}>Approve visibility</Button><Button variant="outline" disabled={decision.isPending} onClick={() => decision.mutate(false)}>Reject request</Button></div></section></main><aside className="rounded-xl border border-border bg-card p-4 text-sm"><h2 className="flex items-center gap-2 font-semibold"><Users className="size-4" /> Teamspace</h2><p className="mt-2 font-medium">{request.name}</p><p className="text-xs text-muted-foreground">@{request.handle}</p><p className="mt-4 text-xs text-muted-foreground">Currently private · requested by {request.requested_by_username || request.requested_by || "a member"}</p></aside></div></>}</div>;
}
