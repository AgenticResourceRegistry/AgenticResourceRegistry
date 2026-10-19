// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useDeploymentConfig } from "@/hooks/use-deployment-config";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ArrowLeft, Bell, Check, ChevronDown, CircleAlert, FileCode2, GitCommitHorizontal, Loader2, MessageSquare, Plus, X } from "lucide-react";
import { toast } from "sonner";
import { auth, prReviews } from "@/lib/api";
import type { ReviewDetail, ReviewFile, ReviewGate, ReviewGateCheck, ReviewThread, ReviewTimelineEntry, ReviewVerdict } from "@/lib/types";
import { useAuthGuard } from "@/hooks/use-auth";
import { AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { ReviewAvatar, ReviewStatePill, reviewDate, reviewPerson } from "@/components/review/review-primitives";

const activeStates = new Set(["open", "changes_requested", "approved"]);
// Mirrors services.review.decisions.SYSTEM_REVISION_MESSAGES: revisions the system pushed (draft saves,
// MCP validation), which are not the author's resubmission.
const SYSTEM_REVISION_MESSAGES = new Set(["Draft updated after submission", "Updated by MCP validation"]);
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

// Mirrors services.review.decisions.ADVISORY_REQUIREMENTS: discussed, never blocking a publish.
const ADVISORY_REQUIREMENTS = new Set(["changes_requested", "unresolved_threads", "edit_lock"]);
const requirementLabels: Record<string, string> = {
  required_checks: "Required checks are not passing", required_approvals: "More approvals required", changes_requested: "A reviewer requested changes",
  pinned_components: "Pinned components are not published yet", unresolved_threads: "Conversations are unresolved",
  mcp_validation: "MCP validation has not passed", unsubmitted_changes: "The author has draft changes not yet submitted as a revision", edit_lock: "The version is being edited",
  skill_folder_rollout: "Skill folders with extra files cannot be installed yet", approved_base_changed: "The base version changed since this release was opened",
  skill_folder: "The skill folder is not valid",
};

function gateBlockers(gate: ReviewGate): { blocking: string[]; hard: string[] } {
  // Older servers report only requirements; derive blockers from the advisory set.
  const blocking = gate.blockers ?? gate.requirements.filter(key => !ADVISORY_REQUIREMENTS.has(key));
  return { blocking, hard: gate.hard_blockers ?? [] };
}

function requirementMessages(gate: ReviewGate, keys: string[]): string[] {
  return keys.flatMap(key => {
    if (key === "required_approvals") {
      const missing = Math.max(gate.required - gate.approvals, 0);
      return [`${gate.approvals} of ${gate.required} required approvals${missing ? ` (${missing} more needed)` : ""}`];
    }
    if (key === "required_checks" && gate.blocking_checks?.length) return gate.blocking_checks.map(describeCheck);
    return [requirementLabels[key] ?? key.replaceAll("_", " ")];
  });
}

function checkDetails(check: ReviewGateCheck): string[] {
  return (Array.isArray(check.details) ? check.details : check.details ? [check.details] : []).map(String);
}

function describeCheck(check: ReviewGateCheck): string {
  const details = checkDetails(check);
  if (check.id === "schema" && details.length) return `${check.name}: missing ${details.join(", ")}`;
  const state = check.status === "queued" || check.status === "running" ? "still running" : check.status === "error" ? "could not run" : "failed";
  return `${check.name} ${state}${details.length ? `: ${details.join(", ")}` : ""}`;
}

/** What stops a person publishing without an override. */
function publishBlockers(gate: ReviewGate): string[] {
  return requirementMessages(gate, gateBlockers(gate).blocking);
}

/** Advisory open items, one plain-language line each. They never block publishing. */
function openItemMessages(gate: ReviewGate): string[] {
  const { blocking } = gateBlockers(gate);
  return requirementMessages(gate, gate.requirements.filter(key => !blocking.includes(key)));
}

const blockingNote = <span className="ml-1.5 rounded-full bg-destructive/10 px-1.5 py-0.5 text-2xs font-normal text-destructive">Blocks publishing</span>;
const advisoryNote = <span className="ml-1.5 rounded-full bg-muted px-1.5 py-0.5 text-2xs font-normal text-muted-foreground">Advisory</span>;

function PublicationGate({ experimentalReviewChecks, review, gate, isAuthor, busy, onPublish, onWithdraw, onClose, isSuperAdmin }: { experimentalReviewChecks: boolean; review: ReviewDetail; gate: ReviewGate; isAuthor: boolean; busy: boolean; onPublish: (override: boolean) => void; onWithdraw: () => void; onClose: () => void; isSuperAdmin: boolean }) {
  const openItems = new Set(gate.requirements);
  const { blocking, hard } = gateBlockers(gate);
  const note = (key: string) => blocking.includes(key) ? blockingNote : advisoryNote;
  const active = activeStates.has(review.state);
  const checksFailing = openItems.has("required_checks") || openItems.has("mcp_validation");
  // Built-in checks (secret scan, required fields) run even while automated checks are off, so always show a failure.
  const showChecks = experimentalReviewChecks || checksFailing;
  const blockers = publishBlockers(gate);
  const advisory = openItemMessages(gate);
  const canPublishNormally = !(isAuthor && !review.self_approval_allowed);
  // A super admin can publish past approvals, checks and their own work, never past integrity rules.
  const showOverride = isSuperAdmin && hard.length === 0 && (!gate.ready || !canPublishNormally);
  const publishButton = <Button size="sm" disabled={!gate.ready || busy} aria-describedby={blockers.length ? "publish-blockers" : undefined} onClick={() => onPublish(false)}>Publish v{review.version}</Button>;
  return <section className={`overflow-hidden rounded-xl border bg-card shadow-sm ${gate.ready ? "border-success/50" : "border-border"}`} aria-label="Publication gate">
    <h2 className="sr-only">Publication gate</h2>
    <div className="flex items-start gap-3 border-b border-border px-4 py-3"><span className={`mt-0.5 grid size-5 shrink-0 place-items-center rounded-full ${gate.ready ? "bg-success text-success-foreground" : "bg-warning/20 text-foreground"}`}>{gate.ready ? <Check className="size-3.5" /> : <CircleAlert className="size-3.5" />}</span><div><p className="text-sm font-semibold">Publication gate</p>{(!gate.ready || advisory.length > 0) && <p className="text-xs text-muted-foreground">{gate.ready ? `${advisory.length} open ${advisory.length === 1 ? "item is" : "items are"} worth a look before publishing.` : "Publishing unlocks when the approvals and required checks pass. Change requests, conversations and edit locks are advisory."}</p>}</div></div>
    <div className="divide-y divide-border text-sm">
      <div className="flex items-start gap-3 px-4 py-2.5"><span className={`mt-0.5 grid size-4 shrink-0 place-items-center rounded-full text-2xs ${gate.approvals >= gate.required ? "bg-success text-success-foreground" : "bg-warning/20 text-foreground"}`}>{gate.approvals >= gate.required ? <Check className="size-3" /> : "·"}</span><div><p className="font-medium">{gate.approvals} of {gate.required} approvals on revision {review.head_revision}</p><p className="text-xs text-muted-foreground">{gate.policy_source} policy · required to publish</p></div></div>
      {showChecks && <div className="flex items-start gap-3 px-4 py-2.5">{checksFailing ? <CircleAlert className="mt-0.5 size-4 shrink-0 text-warning" /> : <Check className="mt-0.5 size-4 shrink-0 text-success" />}<div><p className="font-medium">{checksFailing ? <>Required checks not passing{openItems.has("required_checks") ? note("required_checks") : note("mcp_validation")}</> : "Required checks passed"}</p>{checksFailing && <ul className="mt-0.5 space-y-0.5 text-xs text-muted-foreground">{(gate.blocking_checks ?? []).map(check => <li key={check.id}>{describeCheck(check)}</li>)}{openItems.has("mcp_validation") && <li>{requirementLabels.mcp_validation}</li>}</ul>}{checksFailing && isAuthor && <p className="mt-0.5 text-xs text-muted-foreground">Fix these in the version, then submit a new revision.</p>}{!checksFailing && <p className="text-xs text-muted-foreground">{review.checks.queued + review.checks.running > 0 ? `${review.checks.queued + review.checks.running} automated check(s) still running` : "Automated checks configured for this scope; see the Checks tab for what each covered"}</p>}</div></div>}
      {review.subject_type === "agent" && <div className="flex items-start gap-3 px-4 py-2.5">{openItems.has("pinned_components") ? <CircleAlert className="mt-0.5 size-4 shrink-0 text-warning" /> : <Check className="mt-0.5 size-4 shrink-0 text-success" />}<div><p className="font-medium">{openItems.has("pinned_components") ? <>Pinned components not published{note("pinned_components")}</> : "Pinned components published"}</p>{review.depends_on.length > 0 && <p className="text-xs text-muted-foreground">Depends on {review.depends_on.map(n => `#${n}`).join(", ")}</p>}</div></div>}
      {gate.requirements.filter(key => !["required_approvals", "required_checks", "pinned_components", "mcp_validation", "unresolved_threads"].includes(key)).map(key => <div key={key} className="flex items-start gap-3 px-4 py-2.5"><CircleAlert className="mt-0.5 size-4 shrink-0 text-warning" /><span className="text-sm font-medium">{requirementLabels[key] ?? key.replaceAll("_", " ")}{note(key)}</span></div>)}
      <div className="flex items-start gap-3 px-4 py-2.5"><MessageSquare className="mt-0.5 size-4 shrink-0 text-muted-foreground" /><div><p className="font-medium">{review.threads.unresolved} unresolved {review.threads.unresolved === 1 ? "conversation" : "conversations"}{openItems.has("unresolved_threads") && advisoryNote}</p><p className="text-xs text-muted-foreground">Advisory · does not block publishing</p></div></div>
    </div>
    {active && <div className="flex flex-wrap items-center gap-2 border-t border-border px-4 py-3">
      {showOverride ? <Button size="sm" disabled={busy} onClick={() => onPublish(true)}>Publish with override</Button> : !canPublishNormally ? <span className="text-xs text-muted-foreground">A reviewer will publish once the required approvals are met.</span> : blockers.length ? <TooltipProvider delayDuration={100}><Tooltip><TooltipTrigger asChild><span tabIndex={0} className="inline-flex cursor-not-allowed rounded-[var(--radius-control-compact)] focus-visible:outline-2 focus-visible:outline-ring">{publishButton}</span></TooltipTrigger><TooltipContent side="top" align="start" className="max-w-xs"><p className="font-semibold">Publishing is blocked</p><ul className="mt-1 list-disc space-y-0.5 pl-4">{blockers.map(message => <li key={message}>{message}</li>)}</ul>{advisory.length > 0 && <><p className="mt-2 font-semibold">Open items (advisory)</p><ul className="mt-1 list-disc space-y-0.5 pl-4">{advisory.map(message => <li key={message}>{message}</li>)}</ul></>}</TooltipContent></Tooltip></TooltipProvider> : publishButton}
      <span className="flex-1" />
      {isAuthor ? <Button size="sm" variant="outline" disabled={busy} onClick={onWithdraw}>Withdraw</Button> : <Button size="sm" variant="ghost" className="text-destructive" disabled={busy} onClick={onClose}>Close review</Button>}
      {blockers.length > 0 && <p id="publish-blockers" className="basis-full text-xs text-muted-foreground"><span className="font-medium text-foreground">Blocked:</span> {blockers.join(" · ")}</p>}
      {hard.length > 0 && <p className="basis-full text-xs text-muted-foreground">Resolve these before publishing: no override skips them.</p>}
    </div>}
  </section>;
}

export default function ReviewDetailPage() {
  useAuthGuard();
  const { number } = useParams({ from: "/_authed/review_/$number" });
  const qc = useQueryClient();
  const { experimentalReviewChecks } = useDeploymentConfig();
  const [tab, setTab] = useState<ReviewTab>("conversation");
  const [from, setFrom] = useState("base");
  const [to, setTo] = useState("head");
  const [full, setFull] = useState(false);
  const [summary, setSummary] = useState("");
  const [verdict, setVerdict] = useState<ReviewVerdict>("comment");
  const [showSubmit, setShowSubmit] = useState(false);
  const [publishConfirmation, setPublishConfirmation] = useState<{ override: boolean } | null>(null);
  const [overrideReason, setOverrideReason] = useState("");
  const [closing, setClosing] = useState(false);
  const [closeReason, setCloseReason] = useState("");
  const [requestedId, setRequestedId] = useState("");
  const { data: me } = useQuery({ queryKey: ["auth", "whoami"], queryFn: auth.whoami });
  const { data: review, isPending, isError, refetch } = useQuery({ queryKey: ["pr-review", number], queryFn: () => prReviews.detail(number), refetchInterval: query => (query.state.data?.checks.queued ?? 0) + (query.state.data?.checks.running ?? 0) > 0 ? 5000 : false });
  const { data: liveGate } = useQuery({ queryKey: ["pr-review", number, "gate"], queryFn: () => prReviews.gate(number), enabled: !!review });
  const { data: timeline } = useQuery({ queryKey: ["pr-review", number, "timeline"], queryFn: () => prReviews.timeline(number), enabled: !!review });
  const { data: threads } = useQuery({ queryKey: ["pr-review", number, "threads"], queryFn: () => prReviews.threads(number), enabled: !!review && (tab === "conversation" || tab === "files") });
  const { data: fileMetadata } = useQuery({ queryKey: ["pr-review", number, "files-metadata"], queryFn: () => prReviews.files(number), enabled: !!review });
  const { data: files, isPending: filesPending, isError: filesError, refetch: refetchFiles } = useQuery({ queryKey: ["pr-review", number, "files", from, to, full], queryFn: () => prReviews.diff(number, { from, to, ...(full ? { full: "true" } : {}) }), enabled: !!review && tab === "files" });
  const { data: checks, isPending: checksPending } = useQuery({ queryKey: ["pr-review", number, "checks"], queryFn: () => prReviews.checks(number), enabled: !!review && experimentalReviewChecks && tab === "checks", refetchInterval: query => query.state.data?.some(check => check.status === "queued" || check.status === "running") ? 5000 : false });
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
  // Authors cannot review their own resource unless the policy counts self-approval.
  const canReview = !isAuthor || !!review?.self_approval_allowed;
  const names = review?.reviewer_names ?? {};
  const gate = liveGate ?? review?.gate;
  const unresolved = useMemo(() => (threads ?? []).filter(thread => !thread.resolved_at), [threads]);
  const reviewers = useMemo(() => {
    const verdicts = (timeline?.items ?? []).filter(entry => entry.kind === "verdict" && entry.actor_id && entry.state !== "dismissed");
    const ids = new Set([...(review?.requested_reviewers ?? []), ...verdicts.map(entry => entry.actor_id!).filter(Boolean)]);
    // A change request is answered once the author resubmits a newer revision (automatic draft pushes do not count).
    const revisionNumber = new Map((review?.revisions ?? []).map(rev => [rev.id, rev.number]));
    const resubmitted = Math.max(0, ...(review?.revisions ?? []).filter(rev => !SYSTEM_REVISION_MESSAGES.has(rev.message ?? "")).map(rev => rev.number));
    return [...ids].map(id => {
      const latest = [...verdicts].reverse().find(entry => entry.actor_id === id);
      const answered = latest?.verdict === "request_changes" && (revisionNumber.get(latest.revision_id ?? "") ?? Infinity) < resubmitted;
      // The gate counts approvals on the head revision; an approval of an earlier revision is stale.
      const approvedRevision = latest?.verdict === "approve" && latest.revision_id && latest.revision_id !== review?.head_revision_id ? revisionNumber.get(latest.revision_id) : undefined;
      return { id, name: reviewPerson(names[id], id), verdict: latest?.verdict, answered, approvedRevision };
    });
  }, [review?.requested_reviewers, review?.revisions, review?.head_revision_id, timeline?.items, names]);
  useEffect(() => { const onKey = (event: KeyboardEvent) => { if (event.key === "r" && isOpen && canReview && !(event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement)) setShowSubmit(true); }; window.addEventListener("keydown", onKey); return () => window.removeEventListener("keydown", onKey); }, [isOpen, canReview]);
  if (isPending) return <div className="mx-auto max-w-7xl space-y-4 p-6"><Skeleton className="h-12 w-1/2" /><Skeleton className="h-9" /><Skeleton className="h-96" /></div>;
  if (isError || !review || !gate) return <div className="mx-auto max-w-7xl p-8 text-sm"><h1 className="text-xl font-semibold">Review unavailable</h1><p className="my-3 text-muted-foreground">This review may be private or no longer available.</p><Button variant="outline" onClick={() => refetch()}>Try again</Button></div>;
  // Same prominence as "Review changes": editing is the author's main action on an open review.
  // The author can reopen a review they withdrew (a closed rejection or supersession needs a new submission).
  const reopenable = isAuthor && review.state === "closed" && review.closed_reason === "withdrawn";
  const editButton = <Button size="sm" variant="outline" asChild>{review.subject_type === "agent" ? <Link to="/agents/builder" search={{ edit: review.subject_id }}>Edit resource</Link> : <Link to="/components/$componentId" params={{ componentId: review.subject_id }} search={{ type: componentTypes[review.subject_type], tab: "edit" }}>Edit resource</Link>}</Button>;
  const editLink = isAuthor && isOpen && <Button size="sm" asChild>{review.subject_type === "agent" ? <Link to="/agents/builder" search={{ edit: review.subject_id }}>Edit resource</Link> : <Link to="/components/$componentId" params={{ componentId: review.subject_id }} search={{ type: componentTypes[review.subject_type], tab: "edit" }}>Edit resource</Link>}</Button>;
  const openItems = openItemMessages(gate);
  const totalAdditions = (fileMetadata ?? []).reduce((sum, file) => sum + (file.additions ?? 0), 0);
  const totalDeletions = (fileMetadata ?? []).reduce((sum, file) => sum + (file.deletions ?? 0), 0);
  const publish = (override: boolean) => { setOverrideReason(""); setPublishConfirmation({ override }); };
  const overriding = !!publishConfirmation?.override;
  const confirmPublish = () => {
    if (!publishConfirmation || action.isPending || (overriding && !overrideReason.trim())) return;
    action.mutate(
      () => prReviews.publish(number, undefined, overriding ? overrideReason.trim() : undefined),
      { onSuccess: () => setPublishConfirmation(null) },
    );
  };
  const confirmClose = () => {
    if (!closeReason.trim() || action.isPending) return;
    action.mutate(() => prReviews.close(number, closeReason.trim()), { onSuccess: () => { setClosing(false); setCloseReason(""); } });
  };
  const overriddenItems = publishBlockers(gate);
  const reply = (id: string, body: string) => action.mutateAsync(() => prReviews.reply(number, id, body));
  const resolve = (id: string, value: boolean) => action.mutate(() => prReviews.resolve(number, id, value));
  return <div className="mx-auto w-full max-w-7xl space-y-5 px-4 py-6 sm:px-6">
    <AlertDialog open={publishConfirmation !== null} onOpenChange={open => { if (!open && !action.isPending) setPublishConfirmation(null); }}>
      <AlertDialogContent className="w-[calc(100vw-2rem)] max-w-md rounded-xl">
        <AlertDialogHeader>
          <AlertDialogTitle>Publish v{review.version}?</AlertDialogTitle>
          <AlertDialogDescription>
            This makes revision {review.head_revision} of {review.title} available in the registry.
          </AlertDialogDescription>
        </AlertDialogHeader>
        {overriding && <div className="space-y-3 rounded-lg border border-warning/30 bg-warning/10 p-3 text-sm text-foreground">
          <div>
            <p className="font-medium">Super admin override</p>
            <p className="mt-1 text-muted-foreground">This bypasses review-gate criteria and is recorded in the audit trail. Confidentiality restrictions still apply.</p>
          </div>
          {overriddenItems.length > 0 && <ul className="list-disc space-y-0.5 pl-5">{overriddenItems.map(item => <li key={item}>{item}</li>)}</ul>}
          <div className="space-y-1.5">
            <label htmlFor="override-reason" className="text-xs font-medium">Reason (required)</label>
            <Textarea id="override-reason" value={overrideReason} onChange={event => setOverrideReason(event.target.value)} rows={3} placeholder="Why this release must publish now" className="bg-background" />
          </div>
        </div>}
        {openItems.length > 0 && <div className="rounded-lg border border-border bg-muted/40 p-3 text-sm">
          <p className="font-medium">Open items · advisory</p>
          <ul className="mt-2 max-h-36 list-disc space-y-1 overflow-y-auto pl-5 text-muted-foreground">
            {openItems.map(item => <li key={item}>{item}</li>)}
          </ul>
          <p className="mt-2 text-xs text-muted-foreground">These do not block manual publishing.</p>
        </div>}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={action.isPending}>Cancel</AlertDialogCancel>
          <Button type="button" disabled={action.isPending || (overriding && !overrideReason.trim())} onClick={confirmPublish}>
            {action.isPending && <Loader2 className="size-4 animate-spin" aria-hidden="true" />}
            {action.isPending ? "Publishing…" : overriding ? "Confirm override" : `Publish v${review.version}`}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
    <AlertDialog open={closing} onOpenChange={open => { if (!open && !action.isPending) { setClosing(false); setCloseReason(""); } }}>
      <AlertDialogContent className="w-[calc(100vw-2rem)] max-w-md rounded-xl">
        <AlertDialogHeader>
          <AlertDialogTitle>Close review #{review.number}?</AlertDialogTitle>
          <AlertDialogDescription>Closing rejects v{review.version}. The author sees your reason and can submit a new version.</AlertDialogDescription>
        </AlertDialogHeader>
        <div className="space-y-1.5">
          <label htmlFor="close-reason" className="text-xs font-medium">Reason (required)</label>
          <Textarea id="close-reason" value={closeReason} onChange={event => setCloseReason(event.target.value)} rows={3} placeholder="Why this version will not be published" />
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={action.isPending}>Cancel</AlertDialogCancel>
          <Button type="button" variant="destructive" disabled={action.isPending || !closeReason.trim()} onClick={confirmClose}>
            {action.isPending && <Loader2 className="size-4 animate-spin" aria-hidden="true" />}
            {action.isPending ? "Closing…" : "Close review"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
    <header className="space-y-3">
      <Link to="/review" className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"><ArrowLeft className="size-3.5" /> Review queue</Link>
      <div className="flex flex-wrap items-baseline gap-2"><h1 className="min-w-0 break-words text-2xl font-semibold tracking-tight">{review.title}</h1><span className="font-mono text-xl text-muted-foreground">#{review.number}</span></div>
      <div className="flex flex-wrap items-center gap-2.5 text-sm text-muted-foreground"><ReviewStatePill state={review.state} large /><span><span className="font-medium text-foreground">{reviewPerson(review.author_name, review.author_id)}</span> wants to publish <span className="font-mono text-foreground">v{review.version}</span>{review.base_version ? <> from <span className="font-mono">v{review.base_version}</span></> : " · first release"}</span><span className="rounded-full border border-border bg-card px-2 py-0.5 text-xs capitalize">{review.subject_type}</span><span className="ml-auto">{editLink}</span></div>
    </header>
    {review.state === "closed" && (review.closed_reason || reopenable) && <div className="flex flex-wrap items-center gap-3 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-foreground"><span className="min-w-0 flex-1">{review.closed_reason && <span className="capitalize">{review.closed_reason}.</span>}{reopenable && " Edit the resource if needed, then reopen the review to send it back to reviewers."}</span>{reopenable && <>{editButton}<Button size="sm" disabled={action.isPending} onClick={() => action.mutate(() => prReviews.reopen(number))}>Reopen review</Button></>}</div>}
    {isAuthor && review.state === "changes_requested" && <div className="rounded-lg border border-warning/30 bg-warning/10 p-3 text-sm text-foreground">Use Edit resource to make the changes, then submit them. Submitting a new revision answers the request and returns the review to reviewers.</div>}
    <div className="flex flex-wrap items-center gap-x-2 border-b border-border">
      <nav aria-label="Review sections" className="flex min-w-0 flex-wrap gap-1">{([
        ["conversation", "Conversation", (timeline?.items.length ?? 0) + review.threads.total],
        ["files", "Files changed", fileMetadata?.length],
        ["revisions", "Revisions", review.revisions.length],
        ["checks", "Checks", review.checks.pass + review.checks.fail + review.checks.warn + review.checks.skipped + review.checks.queued + review.checks.running + review.checks.error],
      ] as const).filter(([name]) => name !== "checks" || experimentalReviewChecks).map(([name, label, count]) => <button key={name} type="button" onClick={() => setTab(name)} aria-current={tab === name ? "page" : undefined} className={`inline-flex items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2.5 text-sm transition-colors focus-visible:outline-2 focus-visible:outline-ring ${tab === name ? "border-foreground font-semibold text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"}`}>{label}{count !== undefined && <span className="rounded-full bg-muted px-1.5 text-2xs font-medium text-muted-foreground">{count}</span>}</button>)}</nav>
      <div className="ml-auto flex items-center gap-3 py-1.5"><span className="hidden font-mono text-xs sm:inline"><span className="text-success">+{totalAdditions}</span> <span className="text-destructive">−{totalDeletions}</span></span>{isOpen && !canReview && <TooltipProvider delayDuration={100}><Tooltip><TooltipTrigger asChild><span tabIndex={0} aria-describedby="review-own-note" className="inline-flex cursor-not-allowed rounded-[var(--radius-control-compact)] focus-visible:outline-2 focus-visible:outline-ring"><Button size="sm" disabled>Review changes <ChevronDown className="size-4" /></Button></span></TooltipTrigger><TooltipContent id="review-own-note" side="bottom" align="end">You cannot review your own resources.</TooltipContent></Tooltip></TooltipProvider>}{isOpen && canReview && <Popover open={showSubmit} onOpenChange={setShowSubmit}><PopoverTrigger asChild><Button size="sm">Review changes <ChevronDown className="size-4" /></Button></PopoverTrigger><PopoverContent align="end" className="w-[min(420px,calc(100vw-32px))] rounded-xl p-4"><h2 className="text-sm font-semibold">Finish your review</h2><Textarea aria-label="Review summary" className="mt-3" value={summary} onChange={event => setSummary(event.target.value)} placeholder="Leave a summary comment (Markdown)" rows={3} /><div className="mt-3 space-y-1.5">{(["comment", "approve", "request_changes"] as const).map(choice => <label key={choice} className="flex cursor-pointer items-start gap-2.5 rounded-md p-2 text-sm hover:bg-muted/40"><input className="mt-1" type="radio" name="verdict" checked={verdict === choice} onChange={() => setVerdict(choice)} disabled={isAuthor && (choice === "request_changes" || (choice === "approve" && !review.self_approval_allowed))} /><span><span className="block font-medium">{choice === "request_changes" ? "Request changes" : choice === "approve" ? "Approve" : "Comment"}</span><span className="block text-xs text-muted-foreground">{choice === "comment" ? "General feedback without a verdict." : choice === "approve" ? "An approval counts toward the publication gate." : "Asks the author for changes. Advisory: it does not block publishing."}</span></span></label>)}</div>{verdict === "approve" && openItems.length > 0 && <p role="status" className="mt-3 rounded-md border border-warning/30 bg-warning/10 p-2 text-xs text-foreground"><span className="font-medium">Open items (advisory, they do not block publishing):</span> {openItems.join(" · ")}</p>}<div className="mt-4 flex items-center justify-between gap-2"><span className="text-xs text-muted-foreground">Publishing is a separate step.</span><Button size="sm" disabled={action.isPending || (verdict === "request_changes" && !summary.trim())} onClick={() => action.mutate(() => prReviews.submit(number, verdict, summary), { onSuccess: () => { setShowSubmit(false); setSummary(""); } })}>Submit review</Button></div></PopoverContent></Popover>}</div>
    </div>
    {tab === "files" ? <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-card px-3 py-2 text-xs"><label>Compare <select className="ml-1 rounded-md border border-border bg-background p-1.5" value={from} onChange={event => setFrom(event.target.value)}><option value="base">Base {review.base_version ? `v${review.base_version}` : "(first release)"}</option>{review.revisions.map(rev => <option key={rev.id} value={`r${rev.number}`}>Revision {rev.number}</option>)}</select></label><span aria-hidden="true">→</span><label className="sr-only" htmlFor="review-to">To revision</label><select id="review-to" className="rounded-md border border-border bg-background p-1.5" value={to} onChange={event => setTo(event.target.value)}><option value="head">Head · revision {review.head_revision}</option>{review.revisions.map(rev => <option key={rev.id} value={`r${rev.number}`}>Revision {rev.number}</option>)}</select><label className="ml-auto flex items-center gap-1.5"><input type="checkbox" checked={full} onChange={event => setFull(event.target.checked)} /> Full context</label><span className="text-muted-foreground">{unresolved.length} open conversations</span></div>
      {filesPending ? <Skeleton className="h-72" /> : filesError ? <p className="rounded-lg border border-border p-4 text-sm">Could not load this diff. <button className="underline underline-offset-4" onClick={() => refetchFiles()}>Try again</button></p> : <div className="grid items-start gap-4 lg:grid-cols-[200px_minmax(0,1fr)]"><nav aria-label="Changed files" className="min-w-0 space-y-1 rounded-lg border border-border bg-card p-2 text-xs lg:sticky lg:top-6">{files?.filter(file => file.status !== "unchanged").map(file => <a key={file.path} href={`#file-${encodeURIComponent(file.path)}`} className="flex min-w-0 items-center gap-2 rounded-md px-2 py-1.5 hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring"><FileCode2 className="size-3.5 shrink-0 text-muted-foreground" /><span className="min-w-0 flex-1 truncate font-mono" title={file.path}>{file.path}</span><span className="shrink-0 text-success">+{file.additions ?? 0}</span></a>)}</nav><div className="min-w-0">{files?.filter(file => file.status !== "unchanged").map(file => <DiffFile key={file.path} number={number} revision={review.head_revision} file={file} threads={threads ?? []} names={names} busy={action.isPending} onComment={(path, line, body, draft) => action.mutateAsync(() => prReviews.comment(number, body, path, line, draft))} onReply={reply} onResolve={resolve} active={isOpen} />)}{files?.every(file => file.status === "unchanged") && <p className="rounded-lg border border-border p-5 text-sm text-muted-foreground">No changes between these revisions.</p>}</div></div>}
    </div> : <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_280px]"><main className="min-w-0 space-y-4">
      {tab === "conversation" && <><Timeline review={review} entries={timeline?.items ?? []} names={names} threads={threads ?? []} onReply={reply} onResolve={resolve} busy={action.isPending} active={isOpen} />{(threads ?? []).filter(thread => !(timeline?.items ?? []).some(entry => entry.kind === "comment" && entry.payload?.thread_id === thread.id)).map(thread => <ThreadCard key={thread.id} thread={thread} names={names} busy={action.isPending} onReply={reply} onResolve={resolve} active={isOpen} />)}{isOpen && <div className="flex items-start gap-4"><ReviewAvatar name={reviewPerson(me?.username, me?.id)} /><div className="min-w-0 flex-1"><Composer busy={action.isPending} onSubmit={body => action.mutateAsync(() => prReviews.comment(number, body))} /></div></div>}</>}
      {tab === "revisions" && <div className="overflow-hidden rounded-xl border border-border bg-card shadow-sm">{[...review.revisions].reverse().map(rev => <div key={rev.id} className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-3 text-sm last:border-0"><GitCommitHorizontal className="size-4 text-muted-foreground" /><span className="font-semibold">Revision {rev.number}</span><span className="min-w-0 flex-1 text-muted-foreground">{rev.message || "Changes submitted"} · {reviewDate(rev.created_at)}</span><Button variant="outline" size="sm" disabled={rev.pruned} onClick={() => { setFrom(rev.number === 1 ? "base" : `r${rev.number - 1}`); setTo(`r${rev.number}`); setTab("files"); }}>Compare with previous</Button></div>)}</div>}
      {tab === "checks" && experimentalReviewChecks && <div className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground"><p>Automated checks read and parse content; none runs submitted code. A check that could not run says so instead of passing.</p>{review.state !== "closed" && review.state !== "published" && <Button type="button" variant="outline" size="sm" disabled={action.isPending} onClick={() => action.mutate(() => prReviews.rerunChecks(number))}>Rerun checks</Button>}</div>
        <div className="overflow-hidden rounded-xl border border-border bg-card shadow-sm">{checksPending ? <div className="p-4"><Skeleton className="h-16" /></div> : checks?.length ? checks.map(check => <div key={check.id} className="flex items-start gap-3 border-b border-border px-4 py-3 text-sm last:border-0">{check.status === "pass" ? <Check className="mt-0.5 size-4 shrink-0 text-success" /> : check.status === "fail" || check.status === "error" ? <X className="mt-0.5 size-4 shrink-0 text-destructive" /> : check.status === "queued" || check.status === "running" ? <Loader2 className="mt-0.5 size-4 shrink-0 animate-spin text-muted-foreground" /> : <CircleAlert className="mt-0.5 size-4 shrink-0 text-warning" />}<span className="min-w-0 flex-1"><span className="font-medium">{check.name}</span>{check.network && <span className="ml-2 text-2xs text-muted-foreground">contacts external hosts</span>}{(Array.isArray(check.details) ? check.details : check.details ? [check.details] : []).map((line, index) => <span key={index} className="block break-words text-xs text-muted-foreground">{line}</span>)}</span><span className="shrink-0 text-xs text-muted-foreground">{check.required ? "Blocks publishing" : "Advisory"} · {check.status}</span></div>) : <p className="p-5 text-sm text-muted-foreground">No checks recorded.</p>}</div>
      </div>}
      <PublicationGate experimentalReviewChecks={experimentalReviewChecks} review={review} gate={gate} isAuthor={isAuthor} busy={action.isPending} isSuperAdmin={me?.role === "super_admin"} onPublish={publish} onWithdraw={() => { if (window.confirm("Withdraw this review?")) action.mutate(() => prReviews.withdraw(number)); }} onClose={() => { setCloseReason(""); setClosing(true); }} />
    </main><aside className="min-w-0 space-y-4 text-sm lg:sticky lg:top-6">
      <section className="border-b border-border pb-4"><h2 className="mb-3 text-xs font-semibold">Reviewers</h2>{reviewers.length ? reviewers.map(person => <div key={person.id} className="mb-2 flex min-w-0 items-center gap-2"><ReviewAvatar name={person.name} /><span className="min-w-0 flex-1 truncate text-xs" title={person.name}>{person.name}</span><span className="shrink-0 text-xs text-muted-foreground">{person.verdict === "approve" ? (person.approvedRevision ? `Approved revision ${person.approvedRevision} (outdated)` : "Approved") : person.answered ? "Re-review requested" : person.verdict === "request_changes" ? "Changes requested" : person.verdict === "comment" ? "Commented" : "Requested"}</span></div>) : <p className="text-xs text-muted-foreground">No reviewers requested individually.</p>}{isOpen && <div className="mt-3 flex flex-wrap gap-2"><Input aria-label="Reviewer user ID" placeholder="Reviewer user ID" value={requestedId} onChange={event => setRequestedId(event.target.value)} className="h-8 min-w-32 flex-1" /><Button size="sm" variant="outline" disabled={!requestedId.trim() || action.isPending} onClick={() => action.mutate(() => prReviews.requestReviewer(number, requestedId.trim()), { onSuccess: () => setRequestedId("") })}>Request reviewer</Button></div>}</section>
      <section className="border-b border-border pb-4"><h2 className="mb-2 text-xs font-semibold">Approval policy</h2><p className="text-xs"><strong>{gate.approvals} of {gate.required}</strong> approvals · {gate.policy_source}</p><p className="mt-1 text-xs text-muted-foreground">Approvals are counted on the reviewed revision.</p></section>
      <section className="border-b border-border pb-4"><h2 className="mb-2 text-xs font-semibold">Subject</h2><dl className="grid grid-cols-[5.5rem_minmax(0,1fr)] gap-x-2 gap-y-1 text-xs"><dt className="text-muted-foreground">Type</dt><dd className="capitalize">{review.subject_type}</dd><dt className="text-muted-foreground">Version</dt><dd className="break-all font-mono">v{review.version}</dd><dt className="text-muted-foreground">Base</dt><dd className="font-mono">{review.base_version ? `v${review.base_version}` : "First release"}</dd><dt className="text-muted-foreground">Head</dt><dd>Revision {review.head_revision}</dd></dl>{review.subject_type === "agent" ? <Link to="/agents/$agentId" params={{ agentId: review.subject_id }} className="mt-2 inline-block text-xs underline underline-offset-4 hover:text-primary">Open listing page</Link> : <Link to="/components/$componentId" params={{ componentId: review.subject_id }} search={{ type: componentTypes[review.subject_type] }} className="mt-2 inline-block text-xs underline underline-offset-4 hover:text-primary">Open listing page</Link>}{review.depends_on.length > 0 && <div className="mt-3"><h3 className="mb-1 text-xs font-semibold">Pinned dependencies</h3>{review.depends_on.map(id => <Link key={id} to="/review/$number" params={{ number: String(id) }} className="mr-2 text-xs underline underline-offset-4 hover:text-primary">#{id}</Link>)}</div>}</section>
      <section><h2 className="mb-2 text-xs font-semibold">Notifications</h2><Button size="sm" variant="outline" onClick={() => action.mutate(() => prReviews.subscribe(number, review.my_subscription === "muted" ? "watching" : "muted"))}><Bell className="size-3.5" />{review.my_subscription === "muted" ? "Watch review" : "Watching"}</Button></section>
    </aside></div>}
  </div>;
}
