// SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com>
// SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
// SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
// SPDX-FileCopyrightText: 2026 Harishankar <harishankar0301@gmail.com>
// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

/**
 * Agent Builder page — multi-step wizard matching the approved HTML mockup.
 *
 * Steps: Identity → Behavior → Components → Review
 * Layout: step nav (top) · main + sidebar (middle) · footer (bottom)
 *
 * All existing functionality (draft save, edit mode, component picker,
 * validation, version bump) is preserved.
 */

import { Suspense, useState, useMemo, useCallback, useEffect, useRef } from "react";
import { useRouter, useSearch } from "@tanstack/react-router";
import {
  Trash2,
  Loader2,
  ArrowRight,
  ArrowLeft,
  Save,
  Info,
  HelpCircle,
  Check,
} from "lucide-react";
import { toast } from "sonner";
import { reviewToast } from "@/lib/review-toast";
import { useHelp } from "@/components/wiki/help-context";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { PickerSelect } from "@/components/ui/picker-select";
import {
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
} from "@/components/ui/tabs";
import { PageHeader } from "@/components/layouts/page-header";
import { BuilderStepNav } from "@/components/registry/registry-primitives";
import { EntityGlyph } from "@/components/registry/entity-glyph";
import { useRegistryItem, useAgentValidation, useTeams, useWhoami, useSaveDraft, useUpdateDraft, useStartEdit } from "@/hooks/use-api";
import { useAuthGuard } from "@/hooks/use-auth";
import { registry, type RegistryType } from "@/lib/api";
import { isValidAgentName, normalizeAgentName, slugifyRegistryText } from "@/lib/registry-name";
import { cn } from "@/lib/utils";
import type { RegistryItem, SuccessCriteria } from "@/lib/types";
import type { ValidationResult } from "@/lib/types";

const DRAFT_STORAGE_KEY = "observal_agent_draft";

import { SortableComponentList } from "@/components/builder/sortable-component-list";
import {
  hasSuccessCriteriaContent,
  normalizeSuccessCriteria,
  SuccessCriteriaSection,
  validateSuccessCriteria,
} from "@/components/builder/success-criteria-section";
import { SubmitComponentDialog } from "@/components/registry/submit-component-dialog";
import { ValidationPanel } from "@/components/builder/validation-panel";
import { PreviewPanel } from "@/components/builder/preview-panel";
import { ModelPicker } from "@/components/builder/model-picker";
import { COMPONENT_TYPES, REVERSE_TYPE_MAP, TYPE_MAP } from "@/components/registry/agent-component-constants";
import { ComponentPicker } from "@/components/registry/component-picker";
import { VersionBumpDialog } from "@/components/registry/version-bump-dialog";

const AGENT_NAME_ERROR = "Must start with a letter/digit, only lowercase letters, digits, hyphens, underscores.";
const CATEGORIES = [
  "Code Review",
  "Testing",
  "Documentation",
  "DevOps",
  "Security",
  "Data",
  "Incident Response",
  "Deployment",
  "Cost Optimization",
  "Other",
];

/* ────────────────────────────────────────────────── */
/*  Step definitions                                  */
/* ────────────────────────────────────────────────── */

const BUILDER_STEPS = [
  { id: "identity", label: "Identity", description: "Name and ownership" },
  { id: "behavior", label: "Behavior", description: "Prompt and outcomes" },
  { id: "components", label: "Components", description: "Tools and capabilities" },
  { id: "review", label: "Review", description: "Validate and submit" },
] as const;

type StepId = (typeof BUILDER_STEPS)[number]["id"];

export default function AgentBuilderPage() {
  return (
    <Suspense>
      <AgentBuilderInner />
    </Suspense>
  );
}

function AgentBuilderInner() {
  const { ready } = useAuthGuard();
  const helpCtx = useHelp();
  const router = useRouter();
  const { edit: editId, draft: draftParam, team: teamParam } = useSearch({ from: "/_authed/agents/builder" });
  const isEditMode = !!editId;

  const { data: whoami } = useWhoami();
  const { data: teams = [] } = useTeams();
  const { data: existingAgent } = useRegistryItem("agents", editId ?? draftParam ?? undefined);

  /* ── Step state ── */
  const [activeStep, setActiveStep] = useState<StepId>("identity");

  /* ── Form state ── */
  const [name, setName] = useState("");
  const [nameError, setNameError] = useState("");
  const [promptError, setPromptError] = useState("");
  const [description, setDescription] = useState("");
  const [version, setVersion] = useState("1.0.0");
  const [category, setCategory] = useState("");
  const [modelName, setModelName] = useState("");
  const [modelsByHarness, setModelsByIde] = useState<Record<string, string>>({});
  const [publishing, setPublishing] = useState(false);
  const [activeTab, setActiveTab] = useState<RegistryType>("mcps");
  const [teamId, setTeamId] = useState(teamParam ?? "");
  const [visibility, setVisibility] = useState<"public" | "team">("public");
  const selectedTeam = teams.find((team) => team.id === teamId);
  const visibilityOptions = selectedTeam?.visibility === "private"
    ? [{ value: "team", label: "Team members only" }]
    : [
        { value: "public", label: "Public after approval" },
        { value: "team", label: "Team only" },
      ];

  useEffect(() => {
    if (selectedTeam?.visibility === "private") setVisibility("team");
  }, [selectedTeam?.visibility]);

  const [showVersionDialog, setShowVersionDialog] = useState(false);
  const [draftId, setDraftId] = useState<string | null>(null);
  const [savingDraft, setSavingDraft] = useState(false);
  const [showRestoreBanner, setShowRestoreBanner] = useState(false);
  const [pendingComponents, setPendingComponents] = useState<Array<{
    id: string;
    type: RegistryType;
    name: string;
    body: Record<string, unknown>;
  }>>([]);
  const [createDialogType, setCreateDialogType] = useState<RegistryType | null>(null);
  const saveDraft = useSaveDraft();
  const updateDraft = useUpdateDraft();
  const autoSaveTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const editLoadedRef = useRef(false);

  const [selectedComponents, setSelectedComponents] = useState<
    Record<string, RegistryItem[]>
  >({
    mcps: [],
    skills: [],
    hooks: [],
    prompts: [],
    sandboxes: [],
  });

  const [systemPrompt, setSystemPrompt] = useState<string>("");
  const [successCriteria, setSuccessCriteria] = useState<SuccessCriteria | null>(null);

  const validation = useAgentValidation();
  const [validationResult, setValidationResult] = useState<ValidationResult | null>(null);
  const validateTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const validationKeyRef = useRef("");

  /* ── Load existing agent ── */
  useEffect(() => {
    if (!existingAgent || editLoadedRef.current) return;
    editLoadedRef.current = true;

    setName(existingAgent.name ?? "");
    setDescription(existingAgent.description ?? "");
    const agentVersion = (existingAgent as Record<string, unknown>).version;
    if (typeof agentVersion === "string") setVersion(agentVersion);
    const agentModel = (existingAgent as Record<string, unknown>).model_name;
    if (typeof agentModel === "string") setModelName(agentModel);
    const agentModelsByIde = (existingAgent as Record<string, unknown>).models_by_harness;
    if (agentModelsByIde && typeof agentModelsByIde === "object" && !Array.isArray(agentModelsByIde)) {
      setModelsByIde(agentModelsByIde as Record<string, string>);
    }
    const agentCategory = (existingAgent as Record<string, unknown>).category;
    if (typeof agentCategory === "string") setCategory(agentCategory);
    const agentTeamId = (existingAgent as Record<string, unknown>).team_id;
    if (typeof agentTeamId === "string") setTeamId(agentTeamId);
    const agentVisibility = (existingAgent as Record<string, unknown>).visibility;
    if (agentVisibility === "public" || agentVisibility === "team") setVisibility(agentVisibility);

    if (draftParam) setDraftId(draftParam);

    const agentComponents = (existingAgent as Record<string, unknown>).components;
    if (Array.isArray(agentComponents)) {
      const grouped: Record<string, RegistryItem[]> = {
        mcps: [], skills: [], hooks: [], prompts: [], sandboxes: [],
      };
      for (const comp of agentComponents) {
        const c = comp as Record<string, unknown>;
        const pluralType = REVERSE_TYPE_MAP[c.component_type as string] ?? (c.component_type as string);
        if (grouped[pluralType]) {
          grouped[pluralType].push({
            id: c.component_id as string,
            name: (c.name as string) ?? (c.component_id as string),
            description: c.description as string | undefined,
          });
        }
      }
      setSelectedComponents(grouped);
    }

    const promptField = (existingAgent as Record<string, unknown>).prompt;
    if (typeof promptField === "string") setSystemPrompt(promptField);

    const agentCriteria = (existingAgent as Record<string, unknown>).success_criteria;
    if (agentCriteria && typeof agentCriteria === "object" && !Array.isArray(agentCriteria)) {
      setSuccessCriteria(agentCriteria as SuccessCriteria);
    }
  }, [existingAgent, draftParam]);

  /* ── Edit lock ── */
  const agentIdParam = editId ?? draftParam;
  const startEdit = useStartEdit("agents");
  const editLockAcquiredRef = useRef(false);

  useEffect(() => {
    if (!agentIdParam || !existingAgent) return;
    if ((existingAgent as Record<string, unknown>).status !== "pending") return;
    if (editLockAcquiredRef.current) return;
    editLockAcquiredRef.current = true;

    startEdit.mutate(agentIdParam, {
      onError: () => { editLockAcquiredRef.current = false; },
    });

    const releaseLock = () => {
      const token = sessionStorage.getItem("observal_access_token");
      fetch(`/api/v1/agents/${agentIdParam}/cancel-edit`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        keepalive: true,
      });
    };

    window.addEventListener("beforeunload", releaseLock);
    return () => {
      window.removeEventListener("beforeunload", releaseLock);
      releaseLock();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentIdParam, existingAgent]);

  const selectedIds = useMemo(() => {
    const ids = new Set<string>();
    Object.values(selectedComponents).forEach((items) =>
      items.forEach((item) => ids.add(item.id)),
    );
    return ids;
  }, [selectedComponents]);

  /* ── Debounced validation ── */
  useEffect(() => {
    if (validateTimerRef.current) clearTimeout(validateTimerRef.current);

    const allComponents = Object.entries(selectedComponents).flatMap(
      ([type, items]) =>
        items.map((item) => ({
          component_type: TYPE_MAP[type] ?? type,
          component_id: item.id,
        })),
    );

    const validationKey = JSON.stringify({
      components: allComponents,
      teamId: teamId || undefined,
      visibility,
    });
    validationKeyRef.current = validationKey;
    setValidationResult(null);

    if (allComponents.length === 0) return;

    validateTimerRef.current = setTimeout(() => {
      validation.mutate(
        {
          components: allComponents,
          team_id: teamId || undefined,
          visibility,
        },
        {
          onSuccess: (result) => {
            if (validationKeyRef.current === validationKey) setValidationResult(result);
          },
          onError: () => {
            if (validationKeyRef.current === validationKey) {
              setValidationResult({ valid: false, issues: [{ severity: "error", message: "Validation request failed" }] });
            }
          },
        },
      );
    }, 500);

    return () => {
      if (validateTimerRef.current) clearTimeout(validateTimerRef.current);
    };
  }, [selectedComponents, teamId, visibility]); // eslint-disable-line react-hooks/exhaustive-deps

  /* ── Local draft restore ── */
  useEffect(() => {
    if (isEditMode) return;
    try {
      const stored = localStorage.getItem(DRAFT_STORAGE_KEY);
      if (stored) setShowRestoreBanner(true);
    } catch { /* ignore */ }
  }, [isEditMode]);

  /* ── Auto-save ── */
  useEffect(() => {
    if (isEditMode) return;
    if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);

    autoSaveTimerRef.current = setTimeout(() => {
      const hasContent = name || description || modelName || version !== "1.0.0" ||
        Object.keys(modelsByHarness).length > 0 ||
        Object.values(selectedComponents).some((items) => items.length > 0) ||
        systemPrompt.trim().length > 0 ||
        hasSuccessCriteriaContent(successCriteria);

      if (!hasContent) return;
      try {
        const draft = {
          name, description, version,
          model_name: modelName,
          models_by_harness: modelsByHarness,
          components: selectedComponents,
          prompt: systemPrompt,
          success_criteria: successCriteria,
          draft_id: draftId,
          team_id: teamId,
          visibility,
          saved_at: new Date().toISOString(),
        };
        localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(draft));
      } catch { /* ignore */ }
    }, 2000);

    return () => {
      if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    };
  }, [name, description, version, modelName, modelsByHarness, selectedComponents, systemPrompt, successCriteria, draftId, teamId, visibility, isEditMode]);

  function restoreLocalDraft() {
    try {
      const stored = localStorage.getItem(DRAFT_STORAGE_KEY);
      if (!stored) return;
      const draft = JSON.parse(stored);
      if (draft.name) setName(draft.name);
      if (draft.description) setDescription(draft.description);
      if (draft.version) setVersion(draft.version);
      if (draft.model_name) setModelName(draft.model_name);
      if (draft.models_by_harness && typeof draft.models_by_harness === "object") {
        setModelsByIde(draft.models_by_harness);
      }
      if (draft.components) setSelectedComponents(draft.components);
      if (typeof draft.prompt === "string") setSystemPrompt(draft.prompt);
      if (draft.success_criteria && typeof draft.success_criteria === "object") {
        setSuccessCriteria(draft.success_criteria as SuccessCriteria);
      }
      if (draft.draft_id) setDraftId(draft.draft_id);
      if (typeof draft.team_id === "string") setTeamId(draft.team_id);
      if (draft.visibility === "public" || draft.visibility === "team") setVisibility(draft.visibility);
      setShowRestoreBanner(false);
      toast.success("Draft restored");
    } catch { toast.error("Failed to restore draft"); }
  }

  function discardLocalDraft() {
    try { localStorage.removeItem(DRAFT_STORAGE_KEY); } catch { /* ignore */ }
    setShowRestoreBanner(false);
  }

  /* ── Component handlers ── */
  const handleToggle = useCallback(
    (type: string) => (item: RegistryItem) => {
      setSelectedComponents((prev) => {
        const current = prev[type] ?? [];
        const exists = current.some((c) => c.id === item.id);
        return {
          ...prev,
          [type]: exists
            ? current.filter((c) => c.id !== item.id)
            : [...current, item],
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

  /* ── Build request body ── */
  function buildRequestBody(versionOverride?: string) {
    const components: { component_type: string; component_id: string }[] = [];
    for (const [type, items] of Object.entries(selectedComponents)) {
      const singularType = TYPE_MAP[type] ?? type;
      for (const item of items) {
        components.push({ component_type: singularType, component_id: item.id });
      }
    }
    const normalizedCriteria = normalizeSuccessCriteria(successCriteria);
    const body: Record<string, unknown> = {
      name: normalizeAgentName(name),
      version: (versionOverride ?? version).trim() || "1.0.0",
      description: description.trim(),
      category: category || undefined,
      owner: whoami?.username || whoami?.email || "unknown",
      prompt: systemPrompt.trim(),
      model_name: modelName,
      models_by_harness: modelsByHarness,
      components: components.length > 0 ? components : [],
      visibility,
      success_criteria: normalizedCriteria,
    };
    if (teamId) body.team_id = teamId;
    return body;
  }

  /* ── Draft save ── */
  async function handleSaveDraft() {
    if (!name.trim()) { toast.error("Agent name is required"); return; }
    if (!isValidAgentName(name)) { toast.error(`Invalid agent name. ${AGENT_NAME_ERROR}`); return; }
    const hasPromptComponent = (selectedComponents.prompts ?? []).length > 0;
    if (!systemPrompt.trim() && !hasPromptComponent) {
      setPromptError("An agent prompt is required.");
      toast.error("An agent prompt is required.");
      return;
    }
    const criteriaError = validateSuccessCriteria(successCriteria);
    if (criteriaError) { toast.error(criteriaError); return; }

    setSavingDraft(true);
    try {
      const body = buildRequestBody();
      if (draftId) {
        await updateDraft.mutateAsync({ id: draftId, body });
      } else {
        const created = await saveDraft.mutateAsync(body);
        setDraftId(created.id);
      }
      try { localStorage.removeItem(DRAFT_STORAGE_KEY); } catch { /* ignore */ }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to save draft";
      toast.error(msg);
    } finally {
      setSavingDraft(false);
    }
  }

  /* ── Publish ── */
  async function handlePublish() {
    if (!name.trim()) { toast.error("Agent name is required"); return; }
    const hasPromptComponent = (selectedComponents.prompts ?? []).length > 0;
    if (!systemPrompt.trim() && !hasPromptComponent) {
      setPromptError("An agent prompt is required.");
      toast.error("An agent prompt is required.");
      return;
    }
    if (!isValidAgentName(name)) { toast.error(`Invalid agent name. ${AGENT_NAME_ERROR}`); return; }
    const criteriaError = validateSuccessCriteria(successCriteria);
    if (criteriaError) { toast.error(criteriaError); return; }

    if (isEditMode) { setShowVersionDialog(true); return; }

    setPublishing(true);
    try {
      const flushedIds: { type: RegistryType; id: string; name: string }[] = [];
      for (const pc of pendingComponents) {
        const created = await registry.submit(pc.type, pc.body);
        flushedIds.push({ type: pc.type, id: created.id, name: created.name });
      }
      if (flushedIds.length) {
        for (const { type, id, name } of flushedIds) {
          const plural = type as string;
          selectedComponents[plural] = [...(selectedComponents[plural] ?? []), { id, name }];
        }
        setPendingComponents([]);
      }
      const body = buildRequestBody();
      if (draftId) {
        await updateDraft.mutateAsync({ id: draftId, body });
        const review = await registry.submitDraft(draftId);
        reviewToast("Agent revision submitted for review", review);
        router.navigate({ to: "/agents/$agentId", params: { agentId: draftId } });
      } else {
        const created = await registry.create("agents", body);
        reviewToast("Agent submitted for review", created);
        router.navigate({ to: "/agents/$agentId", params: { agentId: created.id } });
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to publish agent";
      toast.error(msg);
    } finally {
      setPublishing(false);
    }
  }

  async function handleUpdateWithVersion(selectedVersion: string) {
    if (!editId) return;
    const criteriaError = validateSuccessCriteria(successCriteria);
    if (criteriaError) { toast.error(criteriaError); return; }

    setPublishing(true);
    try {
      const body = buildRequestBody(selectedVersion);
      await registry.updateDraft(editId, body);
      const review = await registry.submitDraft(editId);
      setVersion(selectedVersion);
      setShowVersionDialog(false);
      reviewToast("Agent revision submitted for review", review);
      router.navigate({ to: "/agents/$agentId", params: { agentId: editId } });
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to update agent";
      toast.error(msg);
    } finally {
      setPublishing(false);
    }
  }

  /* ── Computed values ── */
  const totalComponents = Object.values(selectedComponents).reduce((sum, items) => sum + items.length, 0);
  const mcpCount = (selectedComponents.mcps ?? []).length;
  const skillCount = (selectedComponents.skills ?? []).length;
  const namespace = teamId
    ? (teams.find((t) => t.id === teamId)?.handle ?? "team")
    : (whoami?.username || whoami?.email || "acme");

  const identityComplete = Boolean(name.trim() && description.trim());
  const behaviorComplete = Boolean(systemPrompt.trim() || (selectedComponents.prompts ?? []).length > 0);
  const componentsComplete = totalComponents > 0;
  const componentsValid = componentsComplete && validationResult?.valid === true;
  const readinessPercent = Math.round(
    ([identityComplete, behaviorComplete, componentsValid].filter(Boolean).length / 3) * 100,
  );

  if (!ready) return null;

  /* ────────────────────────────────────────────────── */
  /*  Render                                            */
  /* ────────────────────────────────────────────────── */

  return (
    <>
      <PageHeader
        title={isEditMode ? "Edit Agent" : "Agent Builder"}
        breadcrumbs={[
          { label: "Registry", href: "/" },
          { label: "Agents", href: "/agents" },
          { label: isEditMode ? "Edit" : "Builder" },
        ]}
        actionButtonsRight={
          <button
            type="button"
            className="text-muted-foreground hover:text-primary transition-colors"
            onClick={() => helpCtx.openHelp({ pageKey: "agents.builder" })}
            title="Agent builder documentation"
          >
            <HelpCircle className="h-4 w-4" />
          </button>
        }
      />

      <div className="page-body w-full mx-auto">
        {/* Restore draft banner */}
        {showRestoreBanner && (
          <div className="mb-4 flex items-center gap-3 rounded-lg border border-info/20 bg-info/5 px-4 py-3">
            <p className="flex-1 text-sm text-info">You have an unsaved draft.</p>
            <Button variant="outline" size="sm" onClick={restoreLocalDraft}>Restore</Button>
            <Button variant="ghost" size="sm" onClick={discardLocalDraft}>Discard</Button>
          </div>
        )}

        {/* ── Step Nav ── */}
        <BuilderStepNav
          steps={[...BUILDER_STEPS]}
          active={activeStep}
          onStepClick={(id) => setActiveStep(id as StepId)}
        />

        {/* ── Workspace: main + sidebar ── */}
        <div className="grid grid-cols-1 items-start gap-3.5 lg:grid-cols-[minmax(0,1fr)_310px]">
          {/* ── Main content area ── */}
          <main className="min-w-0 animate-in">
            {activeStep === "identity" && (
              <IdentityStage
                name={name} setName={setName}
                nameError={nameError} setNameError={setNameError}
                description={description} setDescription={setDescription}
                version={version} setVersion={setVersion}
                category={category} setCategory={setCategory}
                teamId={teamId} setTeamId={setTeamId}
                visibility={visibility} setVisibility={setVisibility}
                teams={teams} whoami={whoami}
                visibilityOptions={visibilityOptions}
                selectedTeam={selectedTeam}
                isEditMode={isEditMode}
                namespace={namespace}
              />
            )}

            {activeStep === "behavior" && (
              <BehaviorStage
                systemPrompt={systemPrompt} setSystemPrompt={setSystemPrompt}
                promptError={promptError} setPromptError={setPromptError}
                modelName={modelName} setModelName={setModelName}
                modelsByHarness={modelsByHarness} setModelsByIde={setModelsByIde}
                successCriteria={successCriteria} setSuccessCriteria={setSuccessCriteria}
              />
            )}

            {activeStep === "components" && (
              <ComponentsStage
                activeTab={activeTab} setActiveTab={setActiveTab}
                selectedComponents={selectedComponents}
                selectedIds={selectedIds}
                handleToggle={handleToggle}
                removeComponent={removeComponent}
                handleReorder={handleReorder}
                setCreateDialogType={setCreateDialogType}
                pendingComponents={pendingComponents}
                setPendingComponents={setPendingComponents}
                validationResult={validationResult}
                isValidating={validation.isPending}
                teamId={teamId} visibility={visibility}
              />
            )}

            {activeStep === "review" && (
              <ReviewStage
                name={name} namespace={namespace} version={version}
                description={description}
                systemPrompt={systemPrompt}
                selectedComponents={selectedComponents}
                totalComponents={totalComponents}
                mcpCount={mcpCount} skillCount={skillCount}
                onEditStep={setActiveStep}
                validationResult={validationResult}
                componentsValid={componentsValid}
                buildRequestBody={buildRequestBody}
              />
            )}
          </main>

          {/* ── Right sidebar: summary ── */}
          <aside className="sticky top-[70px]">
            <SummaryPanel
              name={name} namespace={namespace} version={version}
              description={description}
              visibility={visibility}
              modelName={modelName}
              totalComponents={totalComponents}
              mcpCount={mcpCount} skillCount={skillCount}
              identityComplete={identityComplete}
              behaviorComplete={behaviorComplete}
              componentsValid={componentsValid}
              readinessPercent={readinessPercent}
            />
          </aside>
        </div>

        {/* ── Footer ── */}
        <BuilderFooter
          activeStep={activeStep}
          setActiveStep={setActiveStep}
          onSaveDraft={handleSaveDraft}
          onPublish={handlePublish}
          savingDraft={savingDraft}
          publishing={publishing}
          isEditMode={isEditMode}
          existingStatus={existingAgent?.status}
        />
      </div>

      {/* Create in-memory component dialog */}
      {createDialogType && (
        <SubmitComponentDialog
          key={createDialogType}
          open={!!createDialogType}
          onOpenChange={(v) => { if (!v) setCreateDialogType(null); }}
          type={createDialogType}
          editItem={null}
          onSubmit={(body) => {
            const tempId = Math.random().toString(36).slice(2);
            const cname = (body.name as string) || createDialogType.replace(/s$/, "");
            const targetBody = teamId && !body.team_id ? { ...body, team_id: teamId, visibility } : body;
            setPendingComponents((prev) => [...prev, { id: tempId, type: createDialogType!, name: cname, body: targetBody }]);
            setCreateDialogType(null);
            toast.success(`${cname} added, will be submitted with the agent.`);
          }}
          onSaveDraft={(body) => {
            const tempId = Math.random().toString(36).slice(2);
            const cname = (body.name as string) || createDialogType.replace(/s$/, "");
            const targetBody = teamId && !body.team_id ? { ...body, team_id: teamId, visibility } : body;
            setPendingComponents((prev) => [...prev, { id: tempId, type: createDialogType!, name: cname, body: targetBody }]);
            setCreateDialogType(null);
            toast.success(`${cname} added, will be submitted with the agent.`);
          }}
          isSubmitting={false}
          isSavingDraft={false}
          fixedTeamId={teamId || undefined}
          fixedVisibility={visibility}
        />
      )}

      <VersionBumpDialog
        open={showVersionDialog}
        onOpenChange={setShowVersionDialog}
        currentVersion={version}
        suggestions={undefined}
        onConfirm={handleUpdateWithVersion}
        publishing={publishing}
      />
    </>
  );
}

/* ═══════════════════════════════════════════════════════════ */
/*  Step stages                                               */
/* ═══════════════════════════════════════════════════════════ */

/* ── Identity Stage ── */
function IdentityStage(props: {
  name: string; setName: (v: string) => void;
  nameError: string; setNameError: (v: string) => void;
  description: string; setDescription: (v: string) => void;
  version: string; setVersion: (v: string) => void;
  category: string; setCategory: (v: string) => void;
  teamId: string; setTeamId: (v: string) => void;
  visibility: string; setVisibility: (v: "public" | "team") => void;
  teams: Array<{ id: string; name: string; handle?: string; visibility?: string }>;
  whoami: { username?: string | null; email?: string } | undefined;
  visibilityOptions: Array<{ value: string; label: string }>;
  selectedTeam: { visibility?: string } | undefined;
  isEditMode: boolean;
  namespace: string;
}) {
  return (
    <section className="rounded-xl bg-card p-5 shadow-sm">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-medium">Give the agent a stable identity</h2>
          <p className="mt-0.5 text-2xs text-muted-foreground">
            This becomes the install name developers use from the CLI.
          </p>
        </div>
        {props.name && props.description && (
          <span className="inline-flex items-center gap-1.5 rounded-full bg-surface-raised px-2.5 py-1 text-2xs font-medium text-success">
            <span className="h-1.5 w-1.5 rounded-full bg-success" />
            Complete
          </span>
        )}
      </div>

      <div className="grid gap-3.5 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label className="text-2xs font-medium">Agent name</Label>
          <Input
            className="font-mono"
            value={props.name}
            onChange={(e) => {
              const slugged = slugifyRegistryText(e.target.value, { allowUnderscore: true, preserveTrailingSeparator: true });
              props.setName(slugged);
              props.setNameError(slugged && !isValidAgentName(slugged) ? AGENT_NAME_ERROR : "");
            }}
            placeholder="migration-guide"
            disabled={props.isEditMode}
          />
          {props.nameError && <p className="text-2xs text-destructive">{props.nameError}</p>}
        </div>
        <div className="space-y-1.5">
          <Label className="text-2xs font-medium">Version</Label>
          <Input className="font-mono" value={props.version} onChange={(e) => props.setVersion(e.target.value)} placeholder="0.4.0" />
        </div>
        <div className="space-y-1.5 sm:col-span-2">
          <Label className="text-2xs font-medium">Description</Label>
          <Textarea value={props.description} onChange={(e) => props.setDescription(e.target.value)} placeholder="Plans safe, reversible database and infrastructure migrations." rows={3} className="resize-y" />
        </div>
        <div className="space-y-1.5">
          <Label className="text-2xs font-medium">Publish to</Label>
          <PickerSelect
            value={props.teamId || "personal"}
            onValueChange={(value) => {
              const next = value === "personal" ? "" : value;
              props.setTeamId(next);
              if (!next) props.setVisibility("public");
              else if (props.teams.find((t) => t.id === next)?.visibility === "private") props.setVisibility("team");
            }}
            options={[
              { value: "personal", label: `Personal · ${props.whoami?.username || props.whoami?.email || "me"}/` },
              ...props.teams.map((t) => ({ value: t.id, label: `Team · ${t.name}` })),
            ]}
          />
        </div>
        <div className="space-y-1.5">
          <Label className="text-2xs font-medium">Visibility</Label>
          <PickerSelect
            value={props.visibility}
            onValueChange={(v) => props.setVisibility(v as "public" | "team")}
            options={props.visibilityOptions}
            disabled={props.selectedTeam?.visibility === "private"}
          />
        </div>
        <div className="space-y-1.5">
          <Label className="text-2xs font-medium">Category</Label>
          <PickerSelect
            value={props.category || "__none__"}
            onValueChange={(v) => props.setCategory(v === "__none__" ? "" : v)}
            options={[
              { value: "__none__", label: "Select category..." },
              ...CATEGORIES.map((c) => ({ value: c, label: c })),
            ]}
          />
        </div>
        <div className="space-y-1.5">
          <Label className="text-2xs font-medium">Owner</Label>
          <Input value={props.whoami?.username || props.whoami?.email || "—"} disabled />
        </div>
      </div>

      <div className="mt-4 rounded-[11px] bg-surface-raised p-3.5 text-2xs text-muted-foreground">
        Install identity preview: <span className="font-mono">observal agent pull {props.namespace}/{props.name || "migration-guide"}</span>
      </div>
    </section>
  );
}

/* ── Behavior Stage ── */
function BehaviorStage(props: {
  systemPrompt: string; setSystemPrompt: (v: string) => void;
  promptError: string; setPromptError: (v: string) => void;
  modelName: string; setModelName: (v: string) => void;
  modelsByHarness: Record<string, string>; setModelsByIde: (v: Record<string, string>) => void;
  successCriteria: SuccessCriteria | null; setSuccessCriteria: (v: SuccessCriteria | null) => void;
}) {
  return (
    <>
      <section className="rounded-xl bg-card p-5 shadow-sm">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <h2 className="text-sm font-medium">Define how the agent should behave</h2>
            <p className="mt-0.5 text-2xs text-muted-foreground">
              Keep the operating instructions specific, testable, and clear about when to stop.
            </p>
          </div>
          {props.systemPrompt.trim() && (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-surface-raised px-2.5 py-1 text-2xs font-medium text-success">
              <span className="h-1.5 w-1.5 rounded-full bg-success" />
              Prompt valid
            </span>
          )}
        </div>

        <div className="space-y-1.5">
          <Label className="text-2xs font-medium">Agent prompt</Label>
          <Textarea
            value={props.systemPrompt}
            onChange={(e) => { props.setSystemPrompt(e.target.value); if (e.target.value.trim()) props.setPromptError(""); }}
            placeholder="You are a migration planning agent for production systems…"
            rows={8}
            className={cn("min-h-[210px] resize-y font-mono text-2xs", props.promptError && "border-destructive")}
          />
          {props.promptError ? (
            <p className="text-2xs text-destructive">{props.promptError}</p>
          ) : (
            <p className="text-[10px] text-muted-foreground">Required. You can also link a Prompt component in the next step.</p>
          )}
        </div>

        <div className="mt-5">
          <div className="mb-4">
            <h2 className="text-sm font-medium">Model strategy</h2>
            <p className="mt-0.5 text-2xs text-muted-foreground">
              Set a default and override only where a harness requires it.
            </p>
          </div>
          <ModelPicker
            modelName={props.modelName}
            onModelNameChange={props.setModelName}
            modelsByHarness={props.modelsByHarness}
            onModelsByHarnessChange={props.setModelsByIde}
          />
        </div>
      </section>

      <section className="mt-3 rounded-xl bg-card p-5 shadow-sm">
        <SuccessCriteriaSection
          value={props.successCriteria}
          onChange={props.setSuccessCriteria}
        />
      </section>
    </>
  );
}

/* ── Components Stage ── */
function ComponentsStage(props: {
  activeTab: RegistryType; setActiveTab: (v: RegistryType) => void;
  selectedComponents: Record<string, RegistryItem[]>;
  selectedIds: Set<string>;
  handleToggle: (type: string) => (item: RegistryItem) => void;
  removeComponent: (type: string, id: string) => void;
  handleReorder: (type: string) => (items: { id: string; name: string }[]) => void;
  setCreateDialogType: (v: RegistryType | null) => void;
  pendingComponents: Array<{ id: string; type: RegistryType; name: string; body: Record<string, unknown> }>;
  setPendingComponents: (fn: (prev: Array<{ id: string; type: RegistryType; name: string; body: Record<string, unknown> }>) => Array<{ id: string; type: RegistryType; name: string; body: Record<string, unknown> }>) => void;
  validationResult: ValidationResult | null;
  isValidating: boolean;
  teamId: string; visibility: string;
}) {
  return (
    <section className="rounded-xl bg-card p-5 shadow-sm">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-medium">Choose building blocks</h2>
          <p className="mt-0.5 text-2xs text-muted-foreground">
            Add only what this agent needs. You can reorder selected components before review.
          </p>
        </div>
        <button
          type="button"
          className="rounded-lg px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-surface-raised hover:text-foreground"
          onClick={() => props.setCreateDialogType(props.activeTab)}
        >
          Create component
        </button>
      </div>

      <Tabs value={props.activeTab} onValueChange={(v) => props.setActiveTab(v as RegistryType)}>
        <TabsList>
          {COMPONENT_TYPES.map((ct) => {
            const count = (props.selectedComponents[ct.value] ?? []).length;
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
              label={ct.label}
              selected={props.selectedIds}
              onToggle={props.handleToggle(ct.value)}
              onCreateNew={() => props.setCreateDialogType(ct.value)}
              targetTeamId={props.visibility === "team" ? props.teamId || undefined : undefined}
            />
            {props.pendingComponents.filter((p) => p.type === ct.value).map((p) => (
              <div key={p.id} className="mt-2 flex items-center gap-2 rounded border border-dashed border-border px-3 py-1.5 text-xs">
                <span className="font-medium">{p.name}</span>
                <span className="text-muted-foreground italic">not yet submitted</span>
                <button
                  type="button"
                  className="ml-auto text-muted-foreground hover:text-destructive"
                  onClick={() => props.setPendingComponents((prev) => prev.filter((x) => x.id !== p.id))}
                >✕</button>
              </div>
            ))}
            {(props.selectedComponents[ct.value] ?? []).length > 0 && (
              <div className="mt-3">
                <SortableComponentList
                  items={(props.selectedComponents[ct.value] ?? []).map((item) => ({ id: item.id, name: item.name }))}
                  onReorder={props.handleReorder(ct.value)}
                  onRemove={(id) => props.removeComponent(ct.value, id)}
                />
              </div>
            )}
          </TabsContent>
        ))}
      </Tabs>

      <div className="mt-4">
        <ValidationPanel result={props.validationResult} isValidating={props.isValidating} />
      </div>
    </section>
  );
}

/* ── Review Stage ── */
function ReviewStage(props: {
  name: string; namespace: string; version: string;
  description: string;
  systemPrompt: string;
  selectedComponents: Record<string, RegistryItem[]>;
  totalComponents: number;
  mcpCount: number; skillCount: number;
  onEditStep: (step: StepId) => void;
  validationResult: ValidationResult | null;
  componentsValid: boolean;
  buildRequestBody: () => Record<string, unknown>;
}) {
  const checks = [
    {
      label: "Identity",
      detail: `${props.namespace}/${props.name || "—"} · v${props.version} · Public`,
      ok: Boolean(props.name && props.description),
      step: "identity" as StepId,
    },
    {
      label: "Behavior",
      detail: props.systemPrompt.trim()
        ? "System prompt is provided."
        : (props.selectedComponents.prompts ?? []).length > 0
          ? "Prompt component is included."
          : "Add a system prompt or prompt component.",
      ok: Boolean(props.systemPrompt.trim() || (props.selectedComponents.prompts ?? []).length > 0),
      step: "behavior" as StepId,
    },
    {
      label: "Components",
      detail: props.componentsValid
        ? `${props.mcpCount} MCP servers · ${props.skillCount} skill${props.skillCount !== 1 ? "s" : ""} · validation passed`
        : props.totalComponents > 0
          ? "Run component validation before submission."
          : "Add at least one component.",
      ok: props.componentsValid,
      step: "components" as StepId,
    },
    {
      label: "Ownership",
      detail: "You will be recorded as this agent's point of contact.",
      ok: true,
      step: "review" as StepId,
    },
  ];

  const isReady = checks.slice(0, 3).every((check) => check.ok);
  const body = props.buildRequestBody();
  const previewYaml = `${props.namespace}/${props.name || "agent"}@${props.version}
model: ${body.model_name || "<harness default>"}
visibility: ${body.visibility || "public"}
components:
  mcps: ${props.mcpCount}
  skills: ${props.skillCount}
review: required`;

  return (
    <>
      <section className="rounded-xl bg-card p-5 shadow-sm">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <h2 className="text-sm font-medium">Review the package</h2>
            <p className="mt-0.5 text-2xs text-muted-foreground">
              Confirm identity, behavior, compatibility, and ownership before submission.
            </p>
          </div>
          <span className={cn(
            "inline-flex items-center gap-1.5 rounded-full bg-surface-raised px-2.5 py-1 text-2xs font-medium",
            isReady ? "text-success" : "text-warning",
          )}>
            <span className={cn("h-1.5 w-1.5 rounded-full", isReady ? "bg-success" : "bg-warning")} />
            {isReady ? "Ready to submit" : "Needs attention"}
          </span>
        </div>

        <div className="grid gap-px overflow-hidden rounded-[11px] bg-border">
          {checks.map((c) => (
            <div key={c.label} className="grid grid-cols-[24px_minmax(0,1fr)_auto] items-center gap-2 bg-card p-3">
              <span className={cn(
                "grid h-[22px] w-[22px] place-items-center rounded-full text-xs font-bold",
                c.ok
                  ? "bg-success/10 text-success"
                  : "bg-warning/10 text-warning"
              )}>
                {c.ok ? "✓" : "!"}
              </span>
              <div>
                <strong className="block text-2xs font-medium">{c.label}</strong>
                <span className="text-[10px] text-muted-foreground">{c.detail}</span>
              </div>
              {c.step !== "review" && (
                <button
                  type="button"
                  className="rounded-lg px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-surface-raised hover:text-foreground"
                  onClick={() => props.onEditStep(c.step)}
                >
                  Edit
                </button>
              )}
              {c.step === "review" && (
                <span className="font-mono text-[10px] text-muted-foreground">Confirmed</span>
              )}
            </div>
          ))}
        </div>
      </section>

      <section className="mt-3 rounded-xl bg-card p-5 shadow-sm">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <h2 className="text-sm font-medium">Package preview</h2>
            <p className="mt-0.5 text-2xs text-muted-foreground">
              The exact version that will enter the review queue.
            </p>
          </div>
          <button
            type="button"
            className="rounded-lg px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-surface-raised hover:text-foreground"
          >
            View YAML
          </button>
        </div>
        <pre className="min-h-[150px] rounded-[11px] border border-border bg-surface-raised p-4 font-mono text-2xs leading-relaxed whitespace-pre-wrap">
          {previewYaml}
        </pre>
      </section>
    </>
  );
}

/* ═══════════════════════════════════════════════════════════ */
/*  Summary sidebar (right)                                   */
/* ═══════════════════════════════════════════════════════════ */

function SummaryPanel(props: {
  name: string; namespace: string; version: string;
  description: string;
  visibility: string;
  modelName: string;
  totalComponents: number;
  mcpCount: number; skillCount: number;
  identityComplete: boolean;
  behaviorComplete: boolean;
  componentsValid: boolean;
  readinessPercent: number;
}) {
  return (
    <>
      <section className="rounded-xl bg-card p-5 shadow-sm">
        <div className="mb-3 flex items-start justify-between gap-3">
          <div>
            <h3 className="text-sm font-medium">Agent at a glance</h3>
            <p className="mt-0.5 text-2xs text-muted-foreground">What reviewers will see</p>
          </div>
          <span className="inline-flex items-center gap-1.5 rounded-full bg-surface-raised px-2.5 py-1 text-2xs font-medium text-warning">
            <span className="h-1.5 w-1.5 rounded-full bg-warning" />
            Draft
          </span>
        </div>

        <div className="flex items-center gap-3">
          <EntityGlyph type="agent" size="md" />
          <div>
            <div className="text-base font-semibold">{props.name || "migration-guide"}</div>
            <div className="font-mono text-[10px] text-muted-foreground">
              {props.namespace}/{props.name || "migration-guide"} · v{props.version}
            </div>
          </div>
        </div>

        <p className="mt-3 text-2xs text-muted-foreground">
          {props.description || "Plans safe, reversible database and infrastructure migrations."}
        </p>

        {/* Settings rows */}
        <div className="mt-3 space-y-0 divide-y divide-border">
          <SettingRow label="Publish to" sub="Personal namespace" value={`${props.namespace}/`} />
          <SettingRow label="Visibility" sub="Available after approval" value={props.visibility === "team" ? "Team only" : "Public"} />
          <SettingRow label="Model" sub="Default across harnesses" value={props.modelName || "Harness default"} />
          <SettingRow label="Components" sub={`${props.mcpCount} MCP servers · ${props.skillCount} skill${props.skillCount !== 1 ? "s" : ""}`} value={String(props.totalComponents)} />
        </div>
      </section>

      <section className="mt-2.5 rounded-xl bg-card p-5 shadow-sm">
        <h3 className="text-sm font-medium">Draft readiness</h3>
        <div className="mt-3 space-y-2">
          <ReadinessRow label="Identity and description" ok={props.identityComplete} />
          <ReadinessRow label="Prompt" ok={props.behaviorComplete} />
          <ReadinessRow label="Component validation" ok={props.componentsValid} />
        </div>
        {/* Progress bar */}
        <div className="mt-3 h-1 overflow-hidden rounded-full bg-surface-raised">
          <div
            className="h-full rounded-full bg-primary transition-all"
            style={{ width: `${props.readinessPercent}%` }}
          />
        </div>
      </section>
    </>
  );
}

function SettingRow({ label, sub, value }: { label: string; sub: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 py-3.5">
      <div>
        <strong className="block text-xs font-medium">{label}</strong>
        <span className="text-2xs text-muted-foreground">{sub}</span>
      </div>
      <span className="font-mono text-2xs text-muted-foreground">{value}</span>
    </div>
  );
}

function ReadinessRow({ label, ok }: { label: string; ok: boolean }) {
  return (
    <div className="flex items-center justify-between text-2xs">
      <span>{label}</span>
      <span className={ok ? "font-semibold text-success" : "font-semibold text-destructive"}>
        {ok ? "Passed" : "Needs target"}
      </span>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════ */
/*  Footer                                                    */
/* ═══════════════════════════════════════════════════════════ */

const STEP_ORDER: StepId[] = ["identity", "behavior", "components", "review"];

function BuilderFooter({
  activeStep,
  setActiveStep,
  onSaveDraft,
  onPublish,
  savingDraft,
  publishing,
  isEditMode,
  existingStatus,
}: {
  activeStep: StepId;
  setActiveStep: (s: StepId) => void;
  onSaveDraft: () => void;
  onPublish: () => void;
  savingDraft: boolean;
  publishing: boolean;
  isEditMode: boolean;
  existingStatus?: string;
}) {
  const idx = STEP_ORDER.indexOf(activeStep);
  const prev = idx > 0 ? STEP_ORDER[idx - 1] : null;
  const next = idx < STEP_ORDER.length - 1 ? STEP_ORDER[idx + 1] : null;
  const isLast = idx === STEP_ORDER.length - 1;

  const nextLabels: Record<string, string> = {
    identity: "Continue to behavior",
    behavior: "Continue to components",
    components: "Continue to review",
  };

  return (
    <footer className="mt-3.5 flex flex-wrap items-center gap-2 rounded-xl bg-card px-5 py-3.5 shadow-sm">
      <p className="flex-1 text-2xs text-muted-foreground">
        Nothing is published until review.
      </p>
      {prev ? (
        <Button variant="outline" size="sm" onClick={() => setActiveStep(prev)}>
          <ArrowLeft className="mr-1.5 h-3.5 w-3.5" />
          Back
        </Button>
      ) : (
        <Button variant="outline" size="sm" onClick={onSaveDraft} disabled={savingDraft}>
          {savingDraft ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Save className="mr-1.5 h-3.5 w-3.5" />}
          Save draft
        </Button>
      )}
      {next ? (
        <Button size="sm" onClick={() => setActiveStep(next)}>
          {nextLabels[activeStep] ?? "Next"} <ArrowRight className="ml-1.5 h-3.5 w-3.5" />
        </Button>
      ) : (
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={onSaveDraft} disabled={savingDraft}>
            {savingDraft ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Save className="mr-1.5 h-3.5 w-3.5" />}
            Save draft
          </Button>
          <Button size="sm" onClick={onPublish} disabled={publishing}>
            {publishing ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <ArrowRight className="mr-1.5 h-3.5 w-3.5" />}
            {isEditMode || existingStatus === "pending" ? "Submit revision" : "Submit for review"}
          </Button>
        </div>
      )}
    </footer>
  );
}
