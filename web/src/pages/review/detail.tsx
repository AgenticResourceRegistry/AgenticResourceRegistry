// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ArrowLeft, Bell, Check, ChevronDown, CircleAlert, FileCode2, GitCommitHorizontal, MessageSquare, Plus, X } from "lucide-react";
import { toast } from "sonner";
import { auth, prReviews } from "@/lib/api";
import type { ReviewDetail, ReviewFile, ReviewGate, ReviewThread, ReviewTimelineEntry, ReviewVerdict } from "@/lib/types";
import { useAuthGuard } from "@/hooks/use-auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ReviewAvatar, ReviewStatePill, reviewDate, reviewPerson } from "@/components/review/review-primitives";

const activeStates = new Set(["open", "changes_requested", "approved"]);
const markdownClass = "prose prose-sm dark:prose-invert max-w-[75ch] break-words prose-a:text-primary";
const componentTypes = { mcp: "mcps", skill: "skills", hook: "hooks", prompt: "prompts", sandbox: "sandboxes" } as const;

type ReviewTab = "conversation" | "files" | "revisions" | "checks";

function Markdown({ body }: { body: string }) {
  return <div className={markdownClass}><ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown></div>;
}

function Composer({ onSubmit, busy, label = "Comment", context = "General comment · not tied to a line" }: { onSubmit: (body: string) => Promise<unknown>; busy: boolean; label?: string; context?: string }) {
  const [body, setBody] = useState("");
  const [preview, setPreview] = useState(false);
  const submit = async () => {
    if (!body.trim() || busy) return;
    try { await onSubmit(body.trim()); setBody(""); } catch { /* The mutation reports the error; keep the draft. */ }
  };
  return <div className="overflow-hidden rounded-xl border border-border bg-card shadow-sm">
    <div className="flex gap-1 border-b border-border bg-muted/40 px-3 text-xs">
      {["Write", "Preview"].map((name, index) => <button key={name} type="button" onClick={() => setPreview(index === 1)} aria-pressed={preview === (index === 1)} className={`my-1 rounded-md px-2.5 py-1.5 focus-visible:outline-2 focus-visible:outline-ring ${preview === (index === 1) ? "bg-surface-raised font-semibold text-foreground" : "text-muted-foreground hover:text-foreground"}`}>{name}</button>)}
    </div>
    <div className="p-3">{preview ? <div className="min-h-24 rounded-md p-2">{body.trim() ? <Markdown body={body} /> : <span className="text-sm text-muted-foreground">Nothing to preview yet.</span>}</div> : <Textarea aria-label={label} value={body} onChange={event => setBody(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) { event.preventDefault(); void submit(); } }} rows={4} placeholder="Add to the conversation (Markdown supported)" />}
      <div className="mt-3 flex items-center justify-between gap-3"><span className="text-xs text-muted-foreground">{context}</span><Button size="sm" variant="outline" disabled={busy || !body.trim()} onClick={() => void submit()}>{label}</Button></div>
    </div>
  </div>;
}

function ThreadCard({ thread, onReply, onResolve, busy, names, active }: { thread: ReviewThread; onReply: (id: string, body: string) => Promise<unknown>; onResolve: (id: string, resolved: boolean) => void; busy: boolean; names: Record<string, string>; active: boolean }) {
  return <section id={`thread-${thread.id}`} aria-label={`Conversation on ${thread.path ?? "review"}`} className="my-3 overflow-hidden rounded-xl border border-border bg-card shadow-sm">
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border bg-muted/40 px-3 py-2 text-xs"><span className="min-w-0 break-all font-mono font-medium">{thread.path ? `${thread.path} · line ${thread.start_line}${thread.end_line && thread.end_line !== thread.start_line ? `–${thread.end_line}` : ""}` : "General conversation"}{thread.outdated ? " · Outdated" : ""}</span>{active && <Button variant="ghost" size="sm" disabled={busy} onClick={() => onResolve(thread.id, !thread.resolved_at)}>{thread.resolved_at ? "Reopen" : "Resolve conversation"}</Button>}</div>
    {thread.comments.map(comment => <div key={comment.id} className="border-b border-border px-3 py-3 text-sm"><p className="mb-2 flex items-center gap-2 text-xs text-muted-foreground"><ReviewAvatar name={reviewPerson(names[comment.author_id], comment.author_id)} /><span className="font-semibold text-foreground">{reviewPerson(names[comment.author_id], comment.author_id)}</span>{reviewDate(comment.created_at)}{comment.pending ? " · Pending" : ""}</p><Markdown body={comment.body} />{comment.suggestion && <div className="mt-3 rounded-md border border-success/30 bg-success/10 p-3 text-xs"><span className="font-semibold">Suggestion · apply manually</span><pre className="mt-2 overflow-auto font-mono">{comment.suggestion}</pre></div>}</div>)}
    {active && <div className="p-3"><Composer label="Reply" context="Reply to this conversation" busy={busy} onSubmit={body => onReply(thread.id, body)} /></div>}
  </section>;
}

function DiffFile({ file, threads, onComment, onReply, onResolve, busy, revision, number, names, active }: { file: ReviewFile; threads: ReviewThread[]; onComment: (path: string, line: number, body: string, draft: boolean) => Promise<unknown>; onReply: (id: string, body: string) => Promise<unknown>; onResolve: (id: string, resolved: boolean) => void; busy: boolean; revision: number; number: string; names: Record<string, string>; active: boolean }) {
  const key = `observal-review-viewed-${number}-${revision}-${file.path}`;
  const [viewed, setViewed] = useState(() => localStorage.getItem(key) === "1");
  const [line, setLine] = useState<number | null>(null);
  const [draft, setDraft] = useState(false);
  const [body, setBody] = useState("");
  const [expanded, setExpanded] = useState(false);
  return <section id={`file-${encodeURIComponent(file.path)}`} className="mb-4 scroll-mt-14 overflow-hidden rounded-xl border border-border bg-card shadow-sm">
    <div className="flex flex-wrap items-center gap-2 border-b border-border bg-muted/40 px-3 py-2.5 text-xs"><FileCode2 className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" /><span className="min-w-0 break-all font-mono font-semibold">{file.path}</span><span className="capitalize text-muted-foreground">{file.status}</span>{file.pinned && <span className="rounded-full bg-info/10 px-2 text-info">Pinned</span>}{file.generated && <span className="rounded-full bg-muted px-2 text-muted-foreground">Generated</span>}<span className="ml-auto font-mono text-success">+{file.additions ?? "?"}</span><span className="font-mono text-destructive">−{file.deletions ?? "?"}</span><label className="ml-2 flex cursor-pointer items-center gap-1"><input type="checkbox" checked={viewed} onChange={event => { setViewed(event.target.checked); if (event.target.checked) localStorage.setItem(key, "1"); else localStorage.removeItem(key); }} /> Viewed</label></div>
    {file.too_large ? <div className="p-4 text-sm text-muted-foreground">Large file. <button className="underline underline-offset-4" type="button" onClick={() => setExpanded(true)}>Load anyway</button>{expanded && <p>Reload the comparison with the full-file option above.</p>}</div> : viewed ? <div className="p-3 text-xs text-muted-foreground">Marked as viewed. Uncheck to show this file.</div> : <div className="overflow-x-auto"><table className="w-full border-collapse font-mono text-xs" aria-label={`Diff for ${file.path}`}><tbody>{file.hunks?.map((hunk, index) => <FragmentHunk key={index} hunk={hunk} file={file} line={line} setLine={setLine} threads={threads} draft={draft} setDraft={setDraft} body={body} setBody={setBody} onComment={onComment} onReply={onReply} onResolve={onResolve} busy={busy} names={names} active={active} />)}</tbody></table></div>}
  </section>;
}

function FragmentHunk({ hunk, file, line, setLine, threads, draft, setDraft, body, setBody, onComment, onReply, onResolve, busy, names, active }: { hunk: NonNullable<ReviewFile["hunks"]>[number]; file: ReviewFile; line: number | null; setLine: (line: number | null) => void; threads: ReviewThread[]; draft: boolean; setDraft: (value: boolean) => void; body: string; setBody: (value: string) => void; onComment: (path: string, line: number, body: string, draft: boolean) => Promise<unknown>; onReply: (id: string, body: string) => Promise<unknown>; onResolve: (id: string, resolved: boolean) => void; busy: boolean; names: Record<string, string>; active: boolean }) {
  return <><tr><td colSpan={4} className="bg-info/10 px-3 py-1 text-info">@@ −{hunk.base_start},{hunk.base_lines} +{hunk.head_start},{hunk.head_lines} @@</td></tr>{hunk.lines.map((entry, index) => <tr key={`${index}-${entry.b}-${entry.h}`} className={`group ${entry.t === "add" ? "bg-success/10" : entry.t === "del" ? "bg-destructive/10" : ""}`} aria-label={`line ${entry.h ?? entry.b}, ${entry.t}`}><td className="w-10 select-none border-r border-border px-2 text-right text-muted-foreground">{entry.b}</td><td className="w-10 select-none border-r border-border px-2 text-right text-muted-foreground">{entry.h}</td><td className="w-7 text-center">{active && entry.h && <button aria-label={`Comment on ${file.path} line ${entry.h}`} type="button" className="rounded opacity-70 hover:bg-primary hover:text-primary-foreground focus-visible:opacity-100 sm:opacity-0 sm:group-hover:opacity-100" onClick={() => setLine(entry.h!)}><Plus className="size-3.5" /></button>}</td><td className="whitespace-pre px-2">{entry.t === "add" ? "+" : entry.t === "del" ? "−" : " "}{entry.s}</td></tr>)}
    {line !== null && hunk.lines.some(entry => entry.h === line) && <tr><td colSpan={4} className="p-3 font-sans"><div className="max-w-2xl space-y-2"><label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={draft} onChange={event => setDraft(event.target.checked)} /> Add to pending review</label><Textarea aria-label={`Comment on ${file.path} line ${line}`} value={body} onChange={event => setBody(event.target.value)} placeholder="Comment on this line…" rows={3} /><div className="flex gap-2"><Button size="sm" disabled={!body.trim() || busy} onClick={() => { void onComment(file.path, line, body.trim(), draft).then(() => { setBody(""); setLine(null); }).catch(() => {}); }}>{draft ? "Start a review" : "Add single comment"}</Button><Button size="sm" variant="ghost" onClick={() => setLine(null)}>Cancel</Button></div></div></td></tr>}
    {threads.filter(thread => thread.path === file.path && hunk.lines.some(entry => entry.h === thread.start_line)).map(thread => <tr key={thread.id}><td colSpan={4} className="p-3 font-sans"><ThreadCard thread={thread} names={names} onReply={onReply} onResolve={onResolve} busy={busy} active={active} /></td></tr>)}
  </>;
}

function Timeline({ review, entries, names, threads, onReply, onResolve, busy, active }: { review: ReviewDetail; entries: ReviewTimelineEntry[]; names: Record<string, string>; threads: ReviewThread[]; onReply: (id: string, body: string) => Promise<unknown>; onResolve: (id: string, resolved: boolean) => void; busy: boolean; active: boolean }) {
  const seenThreads = new Set<string>();
  const visibleEntries = entries.filter(entry => {
    if (entry.kind !== "comment" || typeof entry.payload?.thread_id !== "string") return entry.kind !== "opened" && entry.kind !== "submission";
    if (seenThreads.has(entry.payload.thread_id)) return false;
    seenThreads.add(entry.payload.thread_id);
    return true;
  });
  return <div className="relative space-y-3 before:absolute before:bottom-0 before:left-[11px] before:top-0 before:w-px before:bg-border">
    <div className="relative flex items-start gap-4"><ReviewAvatar name={reviewPerson(review.author_name, review.author_id)} /><div className="min-w-0 flex-1 overflow-hidden rounded-xl border border-border bg-card shadow-sm"><div className="flex flex-wrap items-center gap-1.5 border-b border-border bg-muted/40 px-3 py-2 text-xs"><strong className="text-foreground">{reviewPerson(review.author_name, review.author_id)}</strong><span className="text-muted-foreground">opened this review {reviewDate(review.opened_at)} · revision 1</span><span className="ml-auto rounded-full border border-border bg-card px-2 text-muted-foreground">Author</span></div><div className="p-4 text-sm"><Markdown body={review.body || "No description provided."} /></div></div></div>
    {visibleEntries.map(entry => {
      const actor = reviewPerson(entry.actor_name || names[entry.actor_id ?? ""], entry.actor_id);
      const thread = entry.kind === "comment" ? threads.find(item => item.id === entry.payload?.thread_id) : undefined;
      const approval = entry.kind === "verdict" && entry.verdict === "approve";
      const request = entry.kind === "verdict" && entry.verdict === "request_changes";
      const eventName = entry.kind === "verdict" ? (approval ? "approved these changes" : request ? "requested changes" : "reviewed") : entry.kind === "comment" ? `commented${thread?.path ? ` on ${thread.path}` : ""}` : entry.kind === "revision_pushed" ? "pushed a new revision" : entry.kind.replaceAll("_", " ");
      return <div key={entry.id} className="relative flex items-start gap-4"><span className={`z-10 grid size-6 shrink-0 place-items-center rounded-full ring-2 ring-background ${approval || entry.kind === "gate_ready" || entry.kind === "published" ? "bg-success text-success-foreground" : request ? "bg-warning text-warning-foreground" : "bg-surface-raised text-foreground"}`} aria-hidden="true">{approval || entry.kind === "gate_ready" ? <Check className="size-3.5" /> : request ? <CircleAlert className="size-3.5" /> : <GitCommitHorizontal className="size-3.5" />}</span><div className="min-w-0 flex-1 py-0.5 text-sm"><p className="text-muted-foreground"><span className="font-semibold text-foreground">{actor}</span> {eventName} · {reviewDate(entry.created_at)}{entry.state === "dismissed" && <span className="ml-2 rounded-full bg-muted px-2 py-0.5 text-xs">Dismissed</span>}</p>{entry.body && <div className="mt-2 rounded-xl border border-border bg-card p-4 shadow-sm"><Markdown body={entry.body} /></div>}{thread && <ThreadCard thread={thread} names={names} onReply={onReply} onResolve={onResolve} busy={busy} active={active} />}</div></div>;
    })}
  </div>;
}

const requirementLabels: Record<string, string> = {
  required_checks: "Required checks must pass", required_approvals: "More approvals required", changes_requested: "Changes requested by a reviewer",
  pinned_components: "Pinned components must be published", unresolved_threads: "Resolve outstanding conversations",
  mcp_validation: "MCP validation must pass", unsubmitted_changes: "Submit the latest draft as a revision", edit_lock: "Wait for the current edit to finish",
};

function PublicationGate({ review, gate, isAuthor, busy, onPublish, onWithdraw, onClose, category, setCategory, override, setOverride, reason, setReason, isSuperAdmin }: { review: ReviewDetail; gate: ReviewGate; isAuthor: boolean; busy: boolean; onPublish: (overrideReason?: string) => void; onWithdraw: () => void; onClose: () => void; category: string; setCategory: (value: string) => void; override: string; setOverride: (value: string) => void; reason: string; setReason: (value: string) => void; isSuperAdmin: boolean }) {
  const blockers = new Set(gate.requirements);
  const active = activeStates.has(review.state);
  return <section className={`overflow-hidden rounded-xl border bg-card shadow-sm ${gate.ready ? "border-success/50" : "border-border"}`} aria-label="Publication gate">
    <h2 className="sr-only">Publication gate</h2>
    <div className="flex items-start gap-3 border-b border-border px-4 py-3"><span className={`mt-0.5 grid size-5 shrink-0 place-items-center rounded-full ${gate.ready ? "bg-success text-success-foreground" : "bg-warning/20 text-foreground"}`}>{gate.ready ? <Check className="size-3.5" /> : <CircleAlert className="size-3.5" />}</span><div><p className="text-sm font-semibold">{gate.ready ? "Ready to publish" : "Publication gate"}</p><p className="text-xs text-muted-foreground">{gate.ready ? "All required conditions are met." : "Publishing unlocks when the required conditions are met."}</p></div></div>
    <div className="divide-y divide-border text-sm">
      <div className="flex items-start gap-3 px-4 py-2.5"><Check className={`mt-0.5 size-4 shrink-0 ${blockers.has("required_checks") || blockers.has("mcp_validation") ? "text-destructive" : "text-success"}`} /><div><p className="font-medium">{blockers.has("required_checks") || blockers.has("mcp_validation") ? "Required checks failing" : "Required checks passed"}</p><p className="text-xs text-muted-foreground">Validation and review checks for this revision</p></div></div>
      {review.subject_type === "agent" && <div className="flex items-start gap-3 px-4 py-2.5"><Check className={`mt-0.5 size-4 shrink-0 ${blockers.has("pinned_components") ? "text-destructive" : "text-success"}`} /><div><p className="font-medium">{blockers.has("pinned_components") ? "Pinned components not published" : "Pinned components published"}</p>{review.depends_on.length > 0 && <p className="text-xs text-muted-foreground">Depends on {review.depends_on.map(n => `#${n}`).join(", ")}</p>}</div></div>}
      <div className="flex items-start gap-3 px-4 py-2.5"><span className={`mt-0.5 grid size-4 shrink-0 place-items-center rounded-full text-2xs ${gate.approvals >= gate.required ? "bg-success text-success-foreground" : "bg-warning/20 text-foreground"}`}>{gate.approvals >= gate.required ? <Check className="size-3" /> : "·"}</span><div><p className="font-medium">{gate.approvals} of {gate.required} approvals on revision {review.head_revision}</p><p className="text-xs text-muted-foreground">{gate.policy_source} policy</p></div></div>
      {gate.requirements.filter(key => !["required_approvals", "required_checks", "pinned_components", "mcp_validation"].includes(key)).map(key => <div key={key} className="flex items-start gap-3 px-4 py-2.5"><CircleAlert className="mt-0.5 size-4 shrink-0 text-warning" /><span className="text-sm font-medium">{requirementLabels[key] ?? key.replaceAll("_", " ")}</span></div>)}
      <div className="flex items-start gap-3 px-4 py-2.5"><MessageSquare className="mt-0.5 size-4 shrink-0 text-muted-foreground" /><div><p className="font-medium">{review.threads.unresolved} unresolved {review.threads.unresolved === 1 ? "conversation" : "conversations"}</p><p className="text-xs text-muted-foreground">{blockers.has("unresolved_threads") ? "Required by policy" : "Not required by policy"}</p></div></div>
    </div>
    {active && <div className="flex flex-wrap items-center gap-2 border-t border-border px-4 py-3">
      {isAuthor && !review.self_approval_allowed ? <span className="text-xs text-muted-foreground">A reviewer will publish when the gate is ready.</span> : <Button size="sm" disabled={!gate.ready || busy} onClick={() => onPublish()}>Publish v{review.version}</Button>}
      {review.subject_type === "agent" && !isAuthor && <Input className="h-8 w-36" value={category} onChange={event => setCategory(event.target.value)} placeholder="Agent category" aria-label="Agent category" />}
      {isSuperAdmin && !gate.ready && <div className="flex flex-wrap items-center gap-2"><Input className="h-8 w-44" value={override} onChange={event => setOverride(event.target.value)} placeholder="Override reason" aria-label="Override reason" /><Button variant="outline" size="sm" disabled={!override.trim() || busy} onClick={() => onPublish(override)}>Publish with override</Button></div>}
      <span className="flex-1" />
      {isAuthor ? <Button size="sm" variant="outline" disabled={busy} onClick={onWithdraw}>Withdraw</Button> : <div className="flex flex-wrap items-center gap-2"><Input className="h-8 w-36" value={reason} onChange={event => setReason(event.target.value)} placeholder="Close reason" aria-label="Close reason" /><Button size="sm" variant="ghost" className="text-destructive" disabled={!reason.trim() || busy} onClick={onClose}>Close review</Button></div>}
    </div>}
  </section>;
}

export default function ReviewDetailPage() {
  useAuthGuard();
  const { number } = useParams({ from: "/_authed/review_/$number" });
  const qc = useQueryClient();
  const [tab, setTab] = useState<ReviewTab>("conversation");
  const [from, setFrom] = useState("base");
  const [to, setTo] = useState("head");
  const [full, setFull] = useState(false);
  const [summary, setSummary] = useState("");
  const [verdict, setVerdict] = useState<ReviewVerdict>("comment");
  const [showSubmit, setShowSubmit] = useState(false);
  const [category, setCategory] = useState("");
  const [override, setOverride] = useState("");
  const [reason, setReason] = useState("");
  const [requestedId, setRequestedId] = useState("");
  const { data: me } = useQuery({ queryKey: ["auth", "whoami"], queryFn: auth.whoami });
  const { data: review, isPending, isError, refetch } = useQuery({ queryKey: ["pr-review", number], queryFn: () => prReviews.detail(number) });
  const { data: liveGate } = useQuery({ queryKey: ["pr-review", number, "gate"], queryFn: () => prReviews.gate(number), enabled: !!review });
  const { data: timeline } = useQuery({ queryKey: ["pr-review", number, "timeline"], queryFn: () => prReviews.timeline(number), enabled: !!review });
  const { data: threads } = useQuery({ queryKey: ["pr-review", number, "threads"], queryFn: () => prReviews.threads(number), enabled: !!review && (tab === "conversation" || tab === "files") });
  const { data: fileMetadata } = useQuery({ queryKey: ["pr-review", number, "files-metadata"], queryFn: () => prReviews.files(number), enabled: !!review });
  const { data: files, isPending: filesPending, isError: filesError, refetch: refetchFiles } = useQuery({ queryKey: ["pr-review", number, "files", from, to, full], queryFn: () => prReviews.diff(number, { from, to, ...(full ? { full: "true" } : {}) }), enabled: !!review && tab === "files" });
  const { data: checks, isPending: checksPending } = useQuery({ queryKey: ["pr-review", number, "checks"], queryFn: () => prReviews.checks(number), enabled: !!review && tab === "checks" });
  const action = useMutation({ mutationFn: (run: () => Promise<unknown>) => run(), onSuccess: async () => { await qc.invalidateQueries({ queryKey: ["pr-review", number] }); await qc.invalidateQueries({ queryKey: ["pr-reviews"] }); toast.success("Review updated"); }, onError: (err: Error) => toast.error(err.message) });
  useEffect(() => {
    if (!review?.id) return;
    let closed = false;
    let unsubscribe: (() => void) | undefined;
    import("@/lib/graphql-ws").then(({ subscribeToReviewId }) => {
      if (!closed) unsubscribe = subscribeToReviewId(review.id, () => {
        qc.invalidateQueries({ queryKey: ["pr-review", number] });
        qc.invalidateQueries({ queryKey: ["pr-reviews"] });
      });
    });
    return () => { closed = true; unsubscribe?.(); };
  }, [review?.id, number, qc]);
  const isAuthor = !!review && me?.id === review.author_id;
  const isOpen = !!review && activeStates.has(review.state);
  const names = review?.reviewer_names ?? {};
  const gate = liveGate ?? review?.gate;
  const unresolved = useMemo(() => (threads ?? []).filter(thread => !thread.resolved_at), [threads]);
  const reviewers = useMemo(() => {
    const verdicts = (timeline?.items ?? []).filter(entry => entry.kind === "verdict" && entry.actor_id && entry.state !== "dismissed");
    const ids = new Set([...(review?.requested_reviewers ?? []), ...verdicts.map(entry => entry.actor_id!).filter(Boolean)]);
    return [...ids].map(id => ({ id, name: reviewPerson(names[id], id), verdict: [...verdicts].reverse().find(entry => entry.actor_id === id)?.verdict }));
  }, [review?.requested_reviewers, timeline?.items, names]);
  useEffect(() => { const onKey = (event: KeyboardEvent) => { if (event.key === "r" && isOpen && !(event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement)) setShowSubmit(true); }; window.addEventListener("keydown", onKey); return () => window.removeEventListener("keydown", onKey); }, [isOpen]);
  if (isPending) return <div className="mx-auto max-w-7xl space-y-4 p-6"><Skeleton className="h-12 w-1/2" /><Skeleton className="h-9" /><Skeleton className="h-96" /></div>;
  if (isError || !review || !gate) return <div className="mx-auto max-w-7xl p-8 text-sm"><h1 className="text-xl font-semibold">Review unavailable</h1><p className="my-3 text-muted-foreground">This review may be private or no longer available.</p><Button variant="outline" onClick={() => refetch()}>Try again</Button></div>;
  const editLink = isAuthor && isOpen && (review.subject_type === "agent" ? <Link to="/agents/builder" search={{ edit: review.subject_id }} className="inline-flex items-center rounded-[var(--radius-control-compact)] border border-border bg-card px-3 py-1.5 text-xs font-medium shadow-sm hover:bg-muted">Edit version</Link> : <Link to="/components/$componentId" params={{ componentId: review.subject_id }} search={{ type: componentTypes[review.subject_type] }} className="inline-flex items-center rounded-[var(--radius-control-compact)] border border-border bg-card px-3 py-1.5 text-xs font-medium shadow-sm hover:bg-muted">Edit version</Link>);
  const totalAdditions = (fileMetadata ?? []).reduce((sum, file) => sum + (file.additions ?? 0), 0);
  const totalDeletions = (fileMetadata ?? []).reduce((sum, file) => sum + (file.deletions ?? 0), 0);
  const publish = (overrideReason?: string) => { if (window.confirm(overrideReason ? "Publish without required approvals? This action is audited." : `Publish v${review.version}?`)) action.mutate(() => prReviews.publish(number, category || undefined, overrideReason)); };
  const reply = (id: string, body: string) => action.mutateAsync(() => prReviews.reply(number, id, body));
  const resolve = (id: string, value: boolean) => action.mutate(() => prReviews.resolve(number, id, value));
  return <div className="mx-auto w-full max-w-7xl space-y-5 px-4 py-6 sm:px-6">
    <header className="space-y-3">
      <Link to="/review" className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"><ArrowLeft className="size-3.5" /> Review queue</Link>
      <div className="flex flex-wrap items-baseline gap-2"><h1 className="min-w-0 break-words text-2xl font-semibold tracking-tight">{review.title}</h1><span className="font-mono text-xl text-muted-foreground">#{review.number}</span></div>
      <div className="flex flex-wrap items-center gap-2.5 text-sm text-muted-foreground"><ReviewStatePill state={review.state} large /><span><span className="font-medium text-foreground">{reviewPerson(review.author_name, review.author_id)}</span> wants to publish <span className="font-mono text-foreground">v{review.version}</span>{review.base_version ? <> from <span className="font-mono">v{review.base_version}</span></> : " · first release"}</span><span className="rounded-full border border-border bg-card px-2 py-0.5 text-xs capitalize">{review.subject_type}</span><span className="ml-auto">{editLink}</span></div>
    </header>
    {review.state === "published" && <div className="rounded-lg border border-success/30 bg-success/10 p-3 text-sm text-foreground">Published v{review.version}. This release is available in the registry.</div>}
    {review.state === "closed" && <div className="rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-foreground">This review was closed{review.closed_reason ? ` (${review.closed_reason})` : ""}.</div>}
    {isAuthor && review.state === "changes_requested" && <div className="rounded-lg border border-warning/30 bg-warning/10 p-3 text-sm text-foreground">Changes were requested. Edit this version and submit a new revision so reviewers can see what changed.</div>}
    <div className="flex flex-wrap items-center gap-x-2 border-b border-border">
      <nav aria-label="Review sections" className="flex min-w-0 flex-wrap gap-1">{([
        ["conversation", "Conversation", (timeline?.items.length ?? 0) + review.threads.total],
        ["files", "Files changed", fileMetadata?.length],
        ["revisions", "Revisions", review.revisions.length],
        ["checks", "Checks", review.checks.pass + review.checks.fail + review.checks.warn + review.checks.skipped],
      ] as const).map(([name, label, count]) => <button key={name} type="button" onClick={() => setTab(name)} aria-current={tab === name ? "page" : undefined} className={`inline-flex items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2.5 text-sm transition-colors focus-visible:outline-2 focus-visible:outline-ring ${tab === name ? "border-foreground font-semibold text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"}`}>{label}{count !== undefined && <span className="rounded-full bg-muted px-1.5 text-2xs font-medium text-muted-foreground">{count}</span>}</button>)}</nav>
      <div className="ml-auto flex items-center gap-3 py-1.5"><span className="hidden font-mono text-xs sm:inline"><span className="text-success">+{totalAdditions}</span> <span className="text-destructive">−{totalDeletions}</span></span>{isOpen && <Popover open={showSubmit} onOpenChange={setShowSubmit}><PopoverTrigger asChild><Button size="sm">Review changes <ChevronDown className="size-4" /></Button></PopoverTrigger><PopoverContent align="end" className="w-[min(420px,calc(100vw-32px))] rounded-xl p-4"><h2 className="text-sm font-semibold">Finish your review</h2><Textarea aria-label="Review summary" className="mt-3" value={summary} onChange={event => setSummary(event.target.value)} placeholder="Leave a summary comment (Markdown)" rows={3} /><div className="mt-3 space-y-1.5">{(["comment", "approve", "request_changes"] as const).map(choice => <label key={choice} className="flex cursor-pointer items-start gap-2.5 rounded-md p-2 text-sm hover:bg-muted/40"><input className="mt-1" type="radio" name="verdict" checked={verdict === choice} onChange={() => setVerdict(choice)} disabled={isAuthor && (choice === "request_changes" || (choice === "approve" && !review.self_approval_allowed))} /><span><span className="block font-medium">{choice === "request_changes" ? "Request changes" : choice === "approve" ? "Approve" : "Comment"}</span><span className="block text-xs text-muted-foreground">{choice === "comment" ? "General feedback without a verdict." : choice === "approve" ? "An approval counts toward the publication gate." : "Blocks publishing until this request is cleared."}</span></span></label>)}</div><div className="mt-4 flex items-center justify-between gap-2"><span className="text-xs text-muted-foreground">Publishing is a separate step.</span><Button size="sm" disabled={action.isPending || (verdict === "request_changes" && !summary.trim())} onClick={() => action.mutate(() => prReviews.submit(number, verdict, summary), { onSuccess: () => { setShowSubmit(false); setSummary(""); } })}>Submit review</Button></div></PopoverContent></Popover>}</div>
    </div>
    {tab === "files" ? <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-card px-3 py-2 text-xs"><label>Compare <select className="ml-1 rounded-md border border-border bg-background p-1.5" value={from} onChange={event => setFrom(event.target.value)}><option value="base">Base {review.base_version ? `v${review.base_version}` : "(first release)"}</option>{review.revisions.map(rev => <option key={rev.id} value={`r${rev.number}`}>Revision {rev.number}</option>)}</select></label><span aria-hidden="true">→</span><label className="sr-only" htmlFor="review-to">To revision</label><select id="review-to" className="rounded-md border border-border bg-background p-1.5" value={to} onChange={event => setTo(event.target.value)}><option value="head">Head · revision {review.head_revision}</option>{review.revisions.map(rev => <option key={rev.id} value={`r${rev.number}`}>Revision {rev.number}</option>)}</select><label className="ml-auto flex items-center gap-1.5"><input type="checkbox" checked={full} onChange={event => setFull(event.target.checked)} /> Full context</label><span className="text-muted-foreground">{unresolved.length} open conversations</span></div>
      {filesPending ? <Skeleton className="h-72" /> : filesError ? <p className="rounded-lg border border-border p-4 text-sm">Could not load this diff. <button className="underline underline-offset-4" onClick={() => refetchFiles()}>Try again</button></p> : <div className="grid items-start gap-4 lg:grid-cols-[200px_minmax(0,1fr)]"><nav aria-label="Changed files" className="min-w-0 space-y-1 rounded-lg border border-border bg-card p-2 text-xs lg:sticky lg:top-6">{files?.filter(file => file.status !== "unchanged").map(file => <a key={file.path} href={`#file-${encodeURIComponent(file.path)}`} className="flex min-w-0 items-center gap-2 rounded-md px-2 py-1.5 hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring"><FileCode2 className="size-3.5 shrink-0 text-muted-foreground" /><span className="min-w-0 flex-1 truncate font-mono" title={file.path}>{file.path}</span><span className="shrink-0 text-success">+{file.additions ?? 0}</span></a>)}</nav><div className="min-w-0">{files?.filter(file => file.status !== "unchanged").map(file => <DiffFile key={file.path} number={number} revision={review.head_revision} file={file} threads={threads ?? []} names={names} busy={action.isPending} onComment={(path, line, body, draft) => action.mutateAsync(() => prReviews.comment(number, body, path, line, draft))} onReply={reply} onResolve={resolve} active={isOpen} />)}{files?.every(file => file.status === "unchanged") && <p className="rounded-lg border border-border p-5 text-sm text-muted-foreground">No changes between these revisions.</p>}</div></div>}
    </div> : <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_280px]"><main className="min-w-0 space-y-4">
      {tab === "conversation" && <><Timeline review={review} entries={timeline?.items ?? []} names={names} threads={threads ?? []} onReply={reply} onResolve={resolve} busy={action.isPending} active={isOpen} />{(threads ?? []).filter(thread => !(timeline?.items ?? []).some(entry => entry.kind === "comment" && entry.payload?.thread_id === thread.id)).map(thread => <ThreadCard key={thread.id} thread={thread} names={names} busy={action.isPending} onReply={reply} onResolve={resolve} active={isOpen} />)}{isOpen && <div className="flex items-start gap-4"><ReviewAvatar name={reviewPerson(me?.username, me?.id)} /><div className="min-w-0 flex-1"><Composer busy={action.isPending} onSubmit={body => action.mutateAsync(() => prReviews.comment(number, body))} /></div></div>}</>}
      {tab === "revisions" && <div className="overflow-hidden rounded-xl border border-border bg-card shadow-sm">{[...review.revisions].reverse().map(rev => <div key={rev.id} className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-3 text-sm last:border-0"><GitCommitHorizontal className="size-4 text-muted-foreground" /><span className="font-semibold">Revision {rev.number}</span><span className="min-w-0 flex-1 text-muted-foreground">{rev.message || "Changes submitted"} · {reviewDate(rev.created_at)}</span><Button variant="outline" size="sm" disabled={rev.pruned} onClick={() => { setFrom(rev.number === 1 ? "base" : `r${rev.number - 1}`); setTo(`r${rev.number}`); setTab("files"); }}>Compare with previous</Button></div>)}</div>}
      {tab === "checks" && <div className="overflow-hidden rounded-xl border border-border bg-card shadow-sm">{checksPending ? <div className="p-4"><Skeleton className="h-16" /></div> : checks?.length ? checks.map(check => <div key={check.id} className="flex items-start gap-3 border-b border-border px-4 py-3 text-sm last:border-0">{check.status === "pass" ? <Check className="mt-0.5 size-4 shrink-0 text-success" /> : check.status === "fail" ? <X className="mt-0.5 size-4 shrink-0 text-destructive" /> : <CircleAlert className="mt-0.5 size-4 shrink-0 text-warning" />}<span className="min-w-0 flex-1"><span className="font-medium">{check.name}</span>{check.details && <span className="block text-xs text-muted-foreground">{check.details}</span>}</span><span className="shrink-0 text-xs text-muted-foreground">{check.required ? "Required" : "Advisory"} · {check.status}</span></div>) : <p className="p-5 text-sm text-muted-foreground">No checks recorded.</p>}</div>}
      <PublicationGate review={review} gate={gate} isAuthor={isAuthor} busy={action.isPending} isSuperAdmin={me?.role === "super_admin"} onPublish={publish} onWithdraw={() => { if (window.confirm("Withdraw this review?")) action.mutate(() => prReviews.withdraw(number)); }} onClose={() => action.mutate(() => prReviews.close(number, reason))} category={category} setCategory={setCategory} override={override} setOverride={setOverride} reason={reason} setReason={setReason} />
    </main><aside className="min-w-0 space-y-4 text-sm lg:sticky lg:top-6">
      <section className="border-b border-border pb-4"><h2 className="mb-3 text-xs font-semibold">Reviewers</h2>{reviewers.length ? reviewers.map(person => <div key={person.id} className="mb-2 flex min-w-0 items-center gap-2"><ReviewAvatar name={person.name} /><span className="min-w-0 flex-1 truncate text-xs" title={person.name}>{person.name}</span><span className="shrink-0 text-xs text-muted-foreground">{person.verdict === "approve" ? "Approved" : person.verdict === "request_changes" ? "Changes requested" : person.verdict === "comment" ? "Commented" : "Requested"}</span></div>) : <p className="text-xs text-muted-foreground">No reviewers requested individually.</p>}{isOpen && <div className="mt-3 flex flex-wrap gap-2"><Input aria-label="Reviewer user ID" placeholder="Reviewer user ID" value={requestedId} onChange={event => setRequestedId(event.target.value)} className="h-8 min-w-32 flex-1" /><Button size="sm" variant="outline" disabled={!requestedId.trim() || action.isPending} onClick={() => action.mutate(() => prReviews.requestReviewer(number, requestedId.trim()), { onSuccess: () => setRequestedId("") })}>Request reviewer</Button></div>}</section>
      <section className="border-b border-border pb-4"><h2 className="mb-2 text-xs font-semibold">Approval policy</h2><p className="text-xs"><strong>{gate.approvals} of {gate.required}</strong> approvals · {gate.policy_source}</p><p className="mt-1 text-xs text-muted-foreground">Approvals are counted on the reviewed revision.</p></section>
      <section className="border-b border-border pb-4"><h2 className="mb-2 text-xs font-semibold">Subject</h2><dl className="grid grid-cols-[5.5rem_minmax(0,1fr)] gap-x-2 gap-y-1 text-xs"><dt className="text-muted-foreground">Type</dt><dd className="capitalize">{review.subject_type}</dd><dt className="text-muted-foreground">Version</dt><dd className="break-all font-mono">v{review.version}</dd><dt className="text-muted-foreground">Base</dt><dd className="font-mono">{review.base_version ? `v${review.base_version}` : "First release"}</dd><dt className="text-muted-foreground">Head</dt><dd>Revision {review.head_revision}</dd></dl>{review.subject_type === "agent" ? <Link to="/agents/$agentId" params={{ agentId: review.subject_id }} className="mt-2 inline-block text-xs underline underline-offset-4 hover:text-primary">Open listing page</Link> : <Link to="/components/$componentId" params={{ componentId: review.subject_id }} search={{ type: componentTypes[review.subject_type] }} className="mt-2 inline-block text-xs underline underline-offset-4 hover:text-primary">Open listing page</Link>}{review.depends_on.length > 0 && <div className="mt-3"><h3 className="mb-1 text-xs font-semibold">Pinned dependencies</h3>{review.depends_on.map(id => <Link key={id} to="/review/$number" params={{ number: String(id) }} className="mr-2 text-xs underline underline-offset-4 hover:text-primary">#{id}</Link>)}</div>}</section>
      <section><h2 className="mb-2 text-xs font-semibold">Notifications</h2><Button size="sm" variant="outline" onClick={() => action.mutate(() => prReviews.subscribe(number, review.my_subscription === "muted" ? "watching" : "muted"))}><Bell className="size-3.5" />{review.my_subscription === "muted" ? "Watch review" : "Watching"}</Button></section>
    </aside></div>}
  </div>;
}
