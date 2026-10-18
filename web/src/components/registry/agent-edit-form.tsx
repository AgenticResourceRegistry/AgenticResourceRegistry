// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0


import {
  useState,
  useEffect,
  useMemo,
  useCallback,
  useRef,
} from "react";
import {
  ArrowRight,
  Loader2,
  Save,
  RotateCcw,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  useAgentValidation,
  useCreateAgentVersion,
  useUpdateAgent,
  useUpdateDraft,
  useVersionSuggestions,
} from "@/hooks/use-api";
import type {
  AgentVersionDetail,
  RegistryItem,
  SuccessCriteria,
  ValidationResult,
} from "@/lib/types";
import { registry, type RegistryType } from "@/lib/api";
import { ModelPicker } from "@/components/builder/model-picker";
import { SortableComponentList } from "@/components/builder/sortable-component-list";
import {
  normalizeSuccessCriteria,
  SuccessCriteriaSection,
  validateSuccessCriteria,
} from "@/components/builder/success-criteria-section";
import { ValidationPanel } from "@/components/builder/validation-panel";
import { COMPONENT_TYPES, REVERSE_TYPE_MAP, TYPE_MAP } from "@/components/registry/agent-component-constants";
import { ComponentPicker } from "@/components/registry/component-picker";
import { VersionBumpDialog } from "@/components/registry/version-bump-dialog";
import { AUTO_MODEL } from "@/lib/agent-model";

// ── Types ─────────────────────────────────────────────────────────

interface AgentDetail {
  name: string;
  status?: string;
  version?: string;
  owner?: string;
  team_id?: string | null;
  visibility?: "public" | "team";
  user_permission?: string;
  description?: string;
  prompt?: string;
  model_name?: string;
  models_by_harness?: Record<string, string>;
  component_links?: ComponentLink[];
  mcp_links?: ComponentLink[];
  supported_harnesses?: string[];
  [key: string]: unknown;
}

interface ComponentLink {
  component_name?: string;
  mcp_name?: string;
  name?: string;
  component_type?: string;
  component_id?: string;
  mcp_id?: string;
}



export interface AgentEditFormProps {
  agentId: string;
  agent: AgentDetail;
  versionDetail?: AgentVersionDetail;
  currentVersion: string;
  onSuccess?: () => void;
}

// ── Main Component ────────────────────────────────────────────────

export function AgentEditForm({
  agentId,
  agent,
  versionDetail,
  currentVersion,
  onSuccess,
}: AgentEditFormProps) {
  // Merge version-specific fields over base agent data
  const vd = versionDetail;
  const initialDescription = vd?.description ?? agent.description ?? "";
  const initialModelName = (vd?.model_name ?? agent.model_name ?? "").trim() || AUTO_MODEL;
  const initialModelsByIde = (vd?.models_by_harness ?? agent.models_by_harness ?? {}) as Record<string, string>;
  const initialPrompt = vd?.prompt ?? agent.prompt ?? "";

  // ── Form state ───────────────────────────────────────────────
  const [description, setDescription] = useState(initialDescription);
  const [modelName, setModelName] = useState(initialModelName);
  const [modelsByHarness, setModelsByIde] = useState<Record<string, string>>(initialModelsByIde);
  // Releases carry every component pin forward unless the author opts in here.
  const [refreshComponents, setRefreshComponents] = useState(false);
  const [activeTab, setActiveTab] = useState<RegistryType>("mcps");
  const [selectedComponents, setSelectedComponents] = useState<
    Record<string, RegistryItem[]>
  >({ mcps: [], skills: [], hooks: [], prompts: [], sandboxes: [] });
  const [prompt, setPrompt] = useState<string>(initialPrompt);
  const [successCriteria, setSuccessCriteria] = useState<SuccessCriteria | null>(
    vd?.success_criteria ?? null
  );

  // ── Dialog / loading state ────────────────────────────────────
  const [showVersionDialog, setShowVersionDialog] = useState(false);
  const [showDiscardConfirm, setShowDiscardConfirm] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [savingDraft, setSavingDraft] = useState(false);

  // ── Dirty tracking ────────────────────────────────────────────
  const initialStateRef = useRef({
    description: initialDescription,
    modelName: initialModelName,
    modelsByHarness: initialModelsByIde,
    prompt: initialPrompt,
    selectedComponents: {} as Record<string, RegistryItem[]>,
    successCriteria: (vd?.success_criteria ?? null) as SuccessCriteria | null,
  });
  const [isDirty, setIsDirty] = useState(false);

  // ── Validation ────────────────────────────────────────────────
  const validation = useAgentValidation();
  const [validationResult, setValidationResult] = useState<ValidationResult | null>(null);
  const validateTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  // ── Mutations ─────────────────────────────────────────────────
  const createVersion = useCreateAgentVersion();
  const updateDraft = useUpdateDraft();
  // An unpublished version (draft, under review, changes requested, rejected) is amended in place:
  // the version number stays and an open review gets the change as a new revision. Only an
  // approved version needs a new release.
  const amendInPlace = ["draft", "pending", "changes_requested", "rejected"].includes(agent.status ?? "");
  const underReview = agent.status === "pending" || agent.status === "changes_requested";
  const updateAgent = useUpdateAgent();
  const { data: versionSuggestions } = useVersionSuggestions(agentId);

  // ── Initialize form from agent data ──────────────────────────
  const fingerprint = useMemo(
    () =>
      JSON.stringify([
        agent.name,
        currentVersion,
        versionDetail?.description,
        versionDetail?.model_name,
        versionDetail?.models_by_harness,
        versionDetail?.prompt,
        versionDetail?.components,
        versionDetail?.success_criteria,
      ]),
    [agent.name, currentVersion, versionDetail],
  );

  useEffect(() => {
    // Reset description / modelName from latest props
    setDescription(initialDescription);
    setModelName(initialModelName);
    setModelsByIde(initialModelsByIde);

    const links: ComponentLink[] = versionDetail?.components
      ? versionDetail.components.map((component) => ({
          component_type: component.component_type,
          component_id: component.component_id,
          component_name: component.component_name,
          mcp_name: component.mcp_name,
          name: component.name,
        }))
      : (agent.component_links ?? agent.mcp_links ?? []);
    const grouped: Record<string, RegistryItem[]> = {
      mcps: [], skills: [], hooks: [], prompts: [], sandboxes: [],
    };
    for (const comp of links) {
      const singularType = comp.component_type ?? "mcp";
      const pluralType = REVERSE_TYPE_MAP[singularType] ?? singularType;
      const compId = comp.component_id ?? comp.mcp_id;
      const compName = comp.component_name ?? comp.mcp_name ?? comp.name ?? compId ?? "";
      if (grouped[pluralType] && compId) {
        grouped[pluralType].push({ id: compId, name: compName });
      }
    }
    setSelectedComponents(grouped);

    // Load goal template sections

    setPrompt(initialPrompt);
    setSuccessCriteria(vd?.success_criteria ?? null);

    // Sync initial state ref so dirty detection works correctly after re-init
    initialStateRef.current = {
      description: initialDescription,
      modelName: initialModelName,
      modelsByHarness: initialModelsByIde,
      prompt: initialPrompt,
      selectedComponents: grouped,
      successCriteria: vd?.success_criteria ?? null,
    };
    setIsDirty(false);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fingerprint]);

  // ── Dirty detection ───────────────────────────────────────────
  useEffect(() => {
    const init = initialStateRef.current;
    const normalizeCriteria = (c: SuccessCriteria | null) =>
      c?.intended_purpose?.trim() ? c : null;
    const dirty =
      description !== init.description ||
      modelName !== init.modelName ||
      JSON.stringify(modelsByHarness) !== JSON.stringify(init.modelsByHarness) ||
      prompt !== init.prompt ||
      JSON.stringify(selectedComponents) !== JSON.stringify(init.selectedComponents) ||
      JSON.stringify(normalizeCriteria(successCriteria)) !== JSON.stringify(normalizeCriteria(init.successCriteria));
    setIsDirty(dirty);
  }, [description, modelName, modelsByHarness, prompt, selectedComponents, successCriteria]);

  // ── Debounced validation ──────────────────────────────────────
  useEffect(() => {
    if (validateTimerRef.current) clearTimeout(validateTimerRef.current);

    const allComponents = Object.entries(selectedComponents).flatMap(
      ([type, items]) =>
        items.map((item) => ({
          component_type: TYPE_MAP[type] ?? type,
          component_id: item.id,
        })),
    );

    if (allComponents.length === 0) {
      setValidationResult(null);
      return;
    }

    validateTimerRef.current = setTimeout(() => {
      validation.mutate(
        {
          components: allComponents,
          team_id: agent.team_id ?? undefined,
          visibility: agent.visibility ?? "public",
        },
        {
          onSuccess: (result) => setValidationResult(result),
          onError: () =>
            setValidationResult({
              valid: false,
              issues: [{ severity: "error", message: "Validation request failed" }],
            }),
        },
      );
    }, 500);

    return () => {
      if (validateTimerRef.current) clearTimeout(validateTimerRef.current);
    };
  }, [selectedComponents]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Handlers ─────────────────────────────────────────────────

  const selectedIds = useMemo(() => {
    const ids = new Set<string>();
    Object.values(selectedComponents).forEach((items) =>
      items.forEach((item) => ids.add(item.id)),
    );
    return ids;
  }, [selectedComponents]);

  const handleToggle = useCallback(
    (type: string) => (item: RegistryItem) => {
      setSelectedComponents((prev) => {
        const current = prev[type] ?? [];
        const exists = current.some((c) => c.id === item.id);
        return {
          ...prev,
          [type]: exists ? current.filter((c) => c.id !== item.id) : [...current, item],
        };
      });
    },
    [],
  );

  const removeComponent = useCallback((type: string, id: string) => {
    setSelectedComponents((prev) => ({
      ...prev,
      [type]: (prev[type] ?? []).filter((c) => c.id !== id),
    }));
  }, []);

  const handleReorder = useCallback(
    (type: string) => (items: { id: string; name: string }[]) => {
      setSelectedComponents((prev) => {
        const current = prev[type] ?? [];
        const ordered = items
          .map((item) => current.find((c) => c.id === item.id))
          .filter(Boolean) as RegistryItem[];
        return { ...prev, [type]: ordered };
      });
    },
    [],
  );






  // Only a confirmed release may refresh pins. Drafts always keep them, whatever
  // was left ticked in a cancelled release dialog.
  function buildVersionBody(version: string, refresh = false) {
    const components: { component_type: string; component_id: string }[] = [];
    for (const [type, items] of Object.entries(selectedComponents)) {
      const singularType = TYPE_MAP[type] ?? type;
      for (const item of items) {
        components.push({ component_type: singularType, component_id: item.id });
      }
    }

    const normalizedCriteria = normalizeSuccessCriteria(successCriteria);

    return {
      version,
      description: description.trim(),
      prompt: prompt.trim(),
      model_name: modelName.trim() || AUTO_MODEL,
      model_config_json: {},
      models_by_harness: modelsByHarness,
      external_mcps: [],
      supported_harnesses: agent.supported_harnesses ?? [],
      components: components.length > 0 ? components : [],
      yaml_snapshot: null,
      is_prerelease: false,
      success_criteria: normalizedCriteria,
      refresh_components: refresh,
    };
  }

  async function handleRelease(selectedVersion: string) {
    const criteriaError = validateSuccessCriteria(successCriteria);
    if (criteriaError) {
      toast.error(criteriaError);
      return;
    }
    setPublishing(true);
    try {
      const body = buildVersionBody(selectedVersion, refreshComponents);
      await createVersion.mutateAsync({ agentId, body });
      setShowVersionDialog(false);
      setRefreshComponents(false);
      // Reset dirty state
      initialStateRef.current = {
        description,
        modelName,
        modelsByHarness,
        prompt,
        selectedComponents,
        successCriteria,
      };
      setIsDirty(false);
      onSuccess?.();
    } catch {
      // toast handled by mutation
    } finally {
      setPublishing(false);
    }
  }

  async function handleAmend() {
    const criteriaError = validateSuccessCriteria(successCriteria);
    if (criteriaError) {
      toast.error(criteriaError);
      return;
    }
    setPublishing(true);
    try {
      const { description: d, prompt: p, model_name, models_by_harness, components, success_criteria } =
        buildVersionBody(currentVersion);
      await updateDraft.mutateAsync({
        id: agentId,
        body: { description: d, prompt: p, model_name, models_by_harness, components, success_criteria },
      });
      // Under review, saving here is the author's resubmission: it answers requested changes.
      if (underReview) await registry.submitDraft(agentId);
      initialStateRef.current = { description, modelName, modelsByHarness, prompt, selectedComponents, successCriteria };
      setIsDirty(false);
      onSuccess?.();
    } catch {
      // toast handled by mutation
    } finally {
      setPublishing(false);
    }
  }

  async function handleSaveDraft() {
    const criteriaError = validateSuccessCriteria(successCriteria);
    if (criteriaError) {
      toast.error(criteriaError);
      return;
    }
    setSavingDraft(true);
    try {
      const draftVersion = versionSuggestions?.suggestions?.patch ?? currentVersion;
      const body = { ...buildVersionBody(draftVersion), save_as_draft: true };
      await createVersion.mutateAsync({ agentId, body });
      initialStateRef.current = {
        description,
        modelName,
        modelsByHarness,
        prompt,
        selectedComponents,
        successCriteria,
      };
      setIsDirty(false);
      onSuccess?.();
    } catch {
      // toast handled by mutation
    } finally {
      setSavingDraft(false);
    }
  }

  function handleDiscard() {
    if (isDirty) {
      setShowDiscardConfirm(true);
    }
  }

  function confirmDiscard() {
    const init = initialStateRef.current;
    setDescription(init.description);
    setModelName(init.modelName);
    setModelsByIde(init.modelsByHarness);
    setPrompt(init.prompt ?? "");
    setSuccessCriteria(init.successCriteria ?? null);
    setSelectedComponents(
      Object.keys(init.selectedComponents).length > 0
        ? init.selectedComponents
        : { mcps: [], skills: [], hooks: [], prompts: [], sandboxes: [] },
    );
    setIsDirty(false);
    setShowDiscardConfirm(false);
  }

  return (
    <div className="space-y-6">
      {/* Agent name, read-only */}
      <section className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="agent-name" className="text-sm font-medium">
            Agent File Name
            <span className="ml-1 text-destructive">*</span>
          </Label>
          <Input
            id="agent-name"
            value={agent.name}
            disabled
            className="max-w-md bg-muted/40 text-muted-foreground"
          />
          <p className="text-xs text-muted-foreground">
            Agent File name cannot be changed after creation.
          </p>
        </div>

        <div className="space-y-2">
          <Label htmlFor="agent-description" className="text-sm font-medium">
            Description
          </Label>
          <Textarea
            id="agent-description"
            placeholder="What does this Agent File do?"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            className="max-w-lg resize-y"
          />
        </div>

        <div className="max-w-2xl">
          <ModelPicker
            modelName={modelName}
            onModelNameChange={setModelName}
            modelsByHarness={modelsByHarness}
            onModelsByHarnessChange={setModelsByIde}
          />
        </div>
      </section>

      {/* Agent Prompt */}
      <section className="space-y-2">
        <Label htmlFor="agent-prompt" className="text-sm font-medium">
          Agent File Prompt
          <span className="ml-1 text-destructive">*</span>
        </Label>
        <Textarea
          id="agent-prompt"
          placeholder="You are a senior Python engineer. You write tests first, prefer composition over inheritance, always explain your reasoning, and never delete existing tests."
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={8}
          className="resize-y text-sm font-mono"
        />
        <p className="text-xs text-muted-foreground">Required. Or link a Prompt component in the Components section below.</p>
      </section>

      {/* Success Criteria */}
      <SuccessCriteriaSection
        value={successCriteria}
        onChange={setSuccessCriteria}
      />

      <Separator />

      {/* Components */}
      <section className="space-y-4">
        <div>
          <h3 className="text-sm font-medium font-[family-name:var(--font-display)]">
            Components
          </h3>
          <p className="mt-1 text-xs text-muted-foreground">
            Select the MCPs, skills, hooks, prompts, and sandboxes for this Agent File. Drag to reorder.
          </p>
        </div>

        <Tabs
          value={activeTab}
          onValueChange={(v) => setActiveTab(v as RegistryType)}
        >
          <TabsList>
            {COMPONENT_TYPES.map((ct) => {
              const count =
                (selectedComponents[ct.value] ?? []).length +
                0;
              return (
                <TabsTrigger key={ct.value} value={ct.value}>
                  {ct.label}
                  {count > 0 && (
                    <span className="ml-1.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-medium text-primary-foreground">
                      {count}
                    </span>
                  )}
                </TabsTrigger>
              );
            })}
          </TabsList>

          {COMPONENT_TYPES.map((ct) => (
            <TabsContent key={ct.value} value={ct.value}>
              <ComponentPicker
                type={ct.value}
                selected={selectedIds}
                onToggle={handleToggle(ct.value)}
                targetTeamId={agent.visibility === "team" ? agent.team_id ?? undefined : undefined}
              />

              {(selectedComponents[ct.value] ?? []).length > 0 && (
                <div className="mt-3">
                  <SortableComponentList
                    items={(selectedComponents[ct.value] ?? []).map((item) => ({
                      id: item.id,
                      name: item.name,
                    }))}
                    onReorder={handleReorder(ct.value)}
                    onRemove={(id) => removeComponent(ct.value, id)}
                  />
                </div>
              )}

            </TabsContent>
          ))}
        </Tabs>

        <ValidationPanel
          result={validationResult}
          isValidating={validation.isPending}
        />
      </section>


      <Separator />

      {/* Actions */}
      {amendInPlace && (
        <p className="text-xs text-muted-foreground">
          v{currentVersion} is not published yet, so changes are saved to it in place
          {underReview ? " and reviewers see them as a new revision of the same review" : ""}. No new version is created.
        </p>
      )}
      <div className="flex items-center gap-3">
        {amendInPlace ? (
          <Button onClick={handleAmend} disabled={publishing || !isDirty} className="min-w-[160px]">
            {publishing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
            {underReview ? "Save & update review" : "Save changes"}
          </Button>
        ) : (<>
        <Button
          onClick={() => setShowVersionDialog(true)}
          disabled={publishing || savingDraft || !isDirty}
          className="min-w-[160px]"
        >
          {publishing ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <ArrowRight className="mr-2 h-4 w-4" />
          )}
          Save &amp; Release
        </Button>

        <Button
          variant="outline"
          onClick={handleSaveDraft}
          disabled={savingDraft || publishing || !isDirty}
          className="min-w-[120px]"
        >
          {savingDraft ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <Save className="mr-2 h-4 w-4" />
          )}
          Save Draft
        </Button>
        </>)}

        <Button
          variant="ghost"
          onClick={handleDiscard}
          disabled={!isDirty || publishing || savingDraft}
          className="text-muted-foreground hover:text-foreground"
        >
          <RotateCcw className="mr-2 h-4 w-4" />
          Discard
        </Button>
      </div>

      {/* Version Bump Dialog */}
      <VersionBumpDialog
        open={showVersionDialog}
        onOpenChange={(open) => {
          setShowVersionDialog(open);
          // Reopening the dialog starts from the safe default: keep every pin.
          if (!open) setRefreshComponents(false);
        }}
        currentVersion={currentVersion}
        suggestions={versionSuggestions}
        onConfirm={handleRelease}
        publishing={publishing}
        refreshComponents={{ checked: refreshComponents, onCheckedChange: setRefreshComponents }}
      />

      {/* Discard Confirm Dialog */}
      <Dialog open={showDiscardConfirm} onOpenChange={setShowDiscardConfirm}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>Discard changes?</DialogTitle>
            <DialogDescription>
              All unsaved changes will be lost. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowDiscardConfirm(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={confirmDiscard}>
              Discard
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
