// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { Link, useBlocker, useNavigate, useSearch } from "@tanstack/react-router";
import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { EntityGlyph } from "@/components/registry/entity-glyph";
import { SubmitComponentDialog } from "@/components/registry/submit-component-dialog";
import { ErrorState } from "@/components/shared/error-state";
import { useSubjectReview } from "@/hooks/use-review-api";
import { useRegistryItem, useComponentSaveDraft, useComponentSubmit, useComponentSubmitDraft,
  useComponentUpdateDraft, useComponentVersionDetail, useSubmitSkillFolderDraft } from "@/hooks/use-api";
import { canonicalRouteParts } from "@/lib/registry-name";
import type { RegistryItem, SkillVersionManifest } from "@/lib/types";
import { registry, type RegistryType } from "@/lib/api";

const KINDS = [
  { type: "agents", name: "Agent File", summary: "Compose instructions, model and reusable components." },
  { type: "mcps", name: "MCP Client", summary: "Connect a command or remote MCP server." },
  { type: "skills", name: "Skill", summary: "Package task instructions or a versioned skill folder." },
  { type: "hooks", name: "Hook", summary: "Run a handler at a supported harness event." },
  { type: "prompts", name: "Prompt", summary: "Create a reusable template for agents and teams." },
  { type: "sandboxes", name: "Sandbox", summary: "Describe an isolated runtime and its network policy." },
] as const;

type ComponentKind = Exclude<RegistryType, "agents">;

function isSkillManifest(value: RegistryItem | SkillVersionManifest): value is SkillVersionManifest {
  return typeof value.version_id === "string" && typeof value.listing_id === "string";
}

function ComponentWorkspace({ type, draft }: { type: ComponentKind; draft?: string }) {
  const navigate = useNavigate({ from: "/build" });
  const detail = useRegistryItem(type, draft);
  const save = useComponentSaveDraft(type);
  const update = useComponentUpdateDraft(type);
  const submit = useComponentSubmit(type);
  const submitDraft = useComponentSubmitDraft(type);
  const submitExactSkill = useSubmitSkillFolderDraft();
  const [operationError, setOperationError] = useState("");
  const operationErrorRef = useRef<HTMLDivElement>(null);
  useEffect(() => { if (operationError) operationErrorRef.current?.focus(); }, [operationError]);
  const [dirty, setDirty] = useState(false);
  const dirtyRef = useRef(false);
  const blocker = useBlocker({ shouldBlockFn: () => dirtyRef.current, withResolver: true, enableBeforeUnload: dirty });
  const markDirty = useCallback((value: boolean) => { dirtyRef.current = value; setDirty(value); }, []);
  const busy = save.isPending || update.isPending || submit.isPending || submitDraft.isPending || submitExactSkill.isPending;
  const item = draft ? detail.data : undefined;
  const reviewedSkill = type === "skills" && item && ["rejected", "changes_requested"].includes(item.status ?? "");
  const skillRevisionCandidate = type === "skills" && item &&
    (reviewedSkill || (item.delivery_mode === "registry_direct" && !!item.skill_md_content));
  const skillVersion = useComponentVersionDetail(skillRevisionCandidate ? "skills" : undefined, draft,
    skillRevisionCandidate ? String(item.version) : null);
  const versionBoundSkill = reviewedSkill || !!(skillVersion.data?.content_revision || skillVersion.data?.base_version_id || skillVersion.data?.review_epoch);
  const editable = item?.user_permission === "owner" && ["draft", "rejected", "changes_requested"].includes(item.status ?? "");
  const needsReviewChanges = item?.status === "rejected" || item?.status === "changes_requested";
  const { data: activeReview } = useSubjectReview(draft, !!needsReviewChanges);
  const reviewRoute = item ? canonicalRouteParts(item.namespace, item.slug) : null;

  function goToDetail(result: RegistryItem) {
    const canonical = canonicalRouteParts(result.namespace, result.slug);
    if (canonical) void navigate({ to: "/components/$type/$namespace/$slug", params: { type, ...canonical } });
    else void navigate({ to: "/components/$componentId", params: { componentId: result.id }, search: { type } });
  }

  async function saveDraft(body: Record<string, unknown>) {
    setOperationError("");
    try {
      const result = await save.mutateAsync(body) as RegistryItem | SkillVersionManifest;
      markDirty(false);
      if (isSkillManifest(result)) {
        await navigate({ to: "/resources/$type", params: { type: "skills" }, search: {
          folderListingId: result.listing_id, folderVersionId: result.version_id,
          folderVersion: String(body.version ?? "1.0.0"),
        } });
      } else {
        await navigate({ to: "/build", search: { kind: type, draft: result.id }, replace: true });
      }
    } catch (error) {
      setOperationError(error instanceof Error ? error.message : "Could not save draft; your edits are still here.");
    }
  }

  async function submitForReview(body: Record<string, unknown>) {
    setOperationError("");
    try {
      if (draft && item) {
        if (versionBoundSkill) {
          const observed = skillVersion.data;
          if (!observed?.revision) throw new Error("Load the exact Skill revision before resubmitting");
          await update.mutateAsync({ id: draft, body: { ...body, observed_revision: observed.revision } });
          const revised = await registry.getComponentVersion("skills", draft, String(item.version));
          if (!revised.revision) throw new Error("Could not verify the saved Skill revision");
          await submitExactSkill.mutateAsync({ listingId: draft, versionId: revised.id, observedRevision: revised.revision });
          markDirty(false);
          goToDetail(item);
        } else {
          await update.mutateAsync({ id: draft, body });
          const submitted = await submitDraft.mutateAsync(draft);
          markDirty(false);
          goToDetail(submitted);
        }
      } else {
        const submitted = await submit.mutateAsync(body) as RegistryItem;
        markDirty(false);
        goToDetail(submitted);
      }
    } catch (error) {
      setOperationError(error instanceof Error ? error.message : "Could not submit. Your edits are still here.");
    }
  }

  if (draft && (detail.isLoading || (skillRevisionCandidate && skillVersion.isLoading))) return <p role="status" className="text-sm text-muted-foreground">Loading your saved draft…</p>;
  if (draft && (detail.isError || !item)) return <ErrorState message="This draft is unavailable. Check your access or return to your submissions." onRetry={() => void detail.refetch()} />;
  if (skillRevisionCandidate && (skillVersion.isError || !skillVersion.data?.revision)) return <ErrorState message="This Skill revision is unavailable. Retry or open its version in My submissions." onRetry={() => void skillVersion.refetch()} />;
  if (versionBoundSkill && skillVersion.data?.delivery_mode === "registry_direct") return (
    <div className="rounded-xl border border-border bg-card p-6 space-y-3">
      <h2 className="font-semibold">Edit the exact Skill version</h2>
      <p className="text-sm text-muted-foreground">This reviewed Skill has a version-bound draft. Open it in My submissions to edit its folder and resubmit the observed revision.</p>
      <Button asChild variant="outline"><Link to="/resources/$type" params={{ type: "skills" }} search={{ view: "my", folderListingId: draft, folderVersionId: skillVersion.data.id, folderVersion: String(item?.version ?? "") }}>Open versioned Skill editor</Link></Button>
    </div>
  );
  if (draft && !editable) return (
    <div className="rounded-xl border border-border bg-card p-6 space-y-3">
      <h2 className="font-semibold">This resource cannot be edited here</h2>
      <p className="text-sm text-muted-foreground">Use the listing’s version controls for approved resources. Pending reviews use the existing edit-lock workflow in My submissions.</p>
      <Button asChild variant="outline"><Link to="/resources/$type" params={{ type }} search={{ view: "my" }}>Open My submissions</Link></Button>
    </div>
  );

  return (
    <div className="space-y-4">
      {operationError && <div ref={operationErrorRef} role="alert" tabIndex={-1} className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive focus:outline-none">{operationError}</div>}
      {needsReviewChanges && item && (
        <div className="rounded-lg border border-warning/40 bg-warning/5 p-4 text-sm">
          <p className="font-medium text-foreground">Review feedback is waiting</p>
          <p className="mt-1 text-muted-foreground">{item.status === "changes_requested"
            ? "Read the requested changes before editing, then save and resubmit this resource for review."
            : "Read the review decision before editing, then save and resubmit this resource for review."}</p>
          {activeReview ? (
            <Link to="/review/$number" params={{ number: String(activeReview.number) }}
              className="mt-2 inline-flex font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">Read review feedback</Link>
          ) : reviewRoute ? (
            <Link to="/components/$type/$namespace/$slug" params={{ type, ...reviewRoute }}
              className="mt-2 inline-flex font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">Open resource review</Link>
          ) : (
            <Link to="/components/$componentId" params={{ componentId: item.id }} search={{ type }}
              className="mt-2 inline-flex font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">Open resource review</Link>
          )}
        </div>
      )}
      <SubmitComponentDialog key={`${type}/${draft ?? "new"}`} open onOpenChange={() => {}} surface="page" onDirtyChange={markDirty}
        type={type} editItem={item as Record<string, unknown> | undefined}
        onSubmit={(body) => void submitForReview(body)}
        onSaveDraft={(body) => void saveDraft(body)}
        onUpdateDraft={(id, body) => {
          setOperationError("");
          void update.mutateAsync({ id, body: versionBoundSkill && skillVersion.data?.revision
            ? { ...body, observed_revision: skillVersion.data.revision } : body }).then(async () => {
              if (versionBoundSkill) {
                const current = await skillVersion.refetch();
                if (!current.data?.revision) throw new Error("Could not verify the updated Skill revision");
              }
              markDirty(false);
            }).catch((error: Error) => setOperationError(error.message));
        }}
        isSubmitting={busy} isSavingDraft={busy} />
      <AlertDialog open={blocker.status === "blocked"} onOpenChange={(open) => { if (!open) blocker.reset?.(); }}>
        <AlertDialogContent>
          <AlertDialogHeader><AlertDialogTitle>Discard unsaved changes?</AlertDialogTitle>
            <AlertDialogDescription>Your edits have not been saved. Stay here to keep working or discard them and leave this resource.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => blocker.reset?.()}>Keep editing</AlertDialogCancel>
            <AlertDialogAction onClick={() => { markDirty(false); blocker.proceed?.(); }}>Discard changes</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

export default function BuildPage() {
  const { kind, draft } = useSearch({ from: "/_authed/build" });
  const selected = KINDS.find((entry) => entry.type === kind);
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => { headingRef.current?.focus(); }, [kind, draft]);
  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-8 sm:py-10">
      <div className="mb-7 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <Link to="/resources/$type" params={{ type: selected?.type ?? "agents" }} className="inline-flex items-center gap-1 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"><ArrowLeft className="h-3.5 w-3.5" /> Agentic Resources</Link>
        <span aria-hidden="true">/</span><span className="text-foreground">Builder</span>
      </div>
      <div className="mb-7">
        <h1 ref={headingRef} tabIndex={-1} className="text-2xl font-semibold tracking-tight sm:text-3xl focus:outline-none">{selected ? `${draft ? "Edit" : "Build"} a ${selected.name}` : "What will you build?"}</h1>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">{draft ? "Save your changes, then submit for review when ready." : "Save a private working draft, or submit a complete resource for review."} Submission is not publication.</p>
      </div>
      {selected && selected.type !== "agents" && <Link to="/build" className="mb-5 inline-flex items-center gap-1 rounded-sm text-sm font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"><ArrowLeft className="h-4 w-4" /> {draft ? "Back to builder" : "Change resource kind"}</Link>}
      {!selected && <nav aria-label="Choose resource kind" className="mb-8 space-y-5">
        <Link to="/agents/builder"
          className="group flex min-h-28 items-start gap-4 rounded-xl border border-primary/35 bg-primary/5 p-5 transition-colors hover:border-primary/60 hover:bg-primary/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:items-center">
          <EntityGlyph type="agents" size="lg" />
          <span className="min-w-0 flex-1">
            <span className="block text-base font-semibold text-foreground">Agent File</span>
            <span className="mt-1 block text-sm leading-relaxed text-muted-foreground">Compose instructions, choose your model, and add reusable components.</span>
            <span className="mt-1.5 block text-xs text-muted-foreground">Start here to assemble a complete agent.</span>
          </span>
          <ArrowRight className="mt-1 size-5 shrink-0 text-primary transition-transform group-hover:translate-x-0.5 sm:mt-0" aria-hidden="true" />
        </Link>
        <div>
          <h2 className="text-sm font-semibold text-foreground">Build a component</h2>
          <p className="mt-1 text-xs text-muted-foreground">Create a reusable resource on its own, or add one while building an Agent File.</p>
        </div>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {KINDS.filter(({ type }) => type !== "agents").map(({ type, name, summary }) => (
            <Link key={type} to="/build" search={{ kind: type }}
              className="group flex min-h-24 items-start gap-3 rounded-xl border border-border bg-card p-4 transition-colors hover:border-primary/40 hover:bg-muted/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              <EntityGlyph type={type} size="lg" />
              <span className="min-w-0 flex-1"><span className="block text-sm font-semibold">{name}</span><span className="mt-1 block text-xs leading-relaxed text-muted-foreground">{summary}</span></span>
              <ArrowRight className="mt-1 size-4 shrink-0 text-muted-foreground group-hover:text-foreground" aria-hidden="true" />
            </Link>
          ))}
        </div>
      </nav>}
      {selected && selected.type !== "agents" && (
        <div className="mx-auto max-w-4xl">
          <ComponentWorkspace key={selected.type} type={selected.type as ComponentKind} draft={draft} />
        </div>
      )}
    </div>
  );
}
