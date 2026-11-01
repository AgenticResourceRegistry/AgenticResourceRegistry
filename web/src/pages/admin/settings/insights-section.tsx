// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { Check, ChevronsUpDown, Eye, EyeOff, Loader2, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
	Command,
	CommandEmpty,
	CommandGroup,
	CommandInput,
	CommandItem,
	CommandList,
} from "@/components/ui/command";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@/components/ui/popover";
import { admin } from "@/lib/api";
import { useInsightsModelProviders, useInsightsModels } from "@/hooks/use-api";
import { ModelCombobox } from "./model-combobox";
import {
	type ProviderId,
	type InsightsStatus,
	type ModelSuggestion,
	PROVIDERS,
	RECOMMENDED_MODELS,
	detectProvider,
	detectProviderFromKey,
	getInsightsStatus,
} from "./insights-constants";
import {
	AdvancedGroup,
	CardFooter,
	SettingRow,
	SettingsCard,
	SettingsGroup,
	StatusBadge,
	SwitchStateLabel
} from "./settings-ui";
import { rowDomId } from "./settings-helpers";

interface InsightsSectionProps {
	entries: { key: string; value: string; is_sensitive?: boolean; is_set?: boolean }[];
	onSave: (key: string, value: string) => Promise<void>;
	onRevoke: (key: string) => void;
	refetch: () => void;
	onDirtyChange?: (sectionId: string, dirty: boolean) => void;
}

/** Server defaults for the advanced numbers, used when no row is stored. */
const ADVANCED_DEFAULTS: Record<string, string> = {
	"insights.batch_period_days": "14",
	"insights.min_sessions": "5",
	"insights.facet_max_calls": "100",
	"insights.facet_concurrency": "25",
};

interface TestResult {
	success: boolean;
	model?: string;
	latency_ms?: number;
	error?: string;
	hint?: string;
}

export function InsightsSection({ entries, onSave, onRevoke, refetch, onDirtyChange }: InsightsSectionProps) {
	const getEntryValue = useCallback(
		(key: string) => {
			const entry = entries.find((e) => e.key === key);
			if (!entry) return "";
			if (entry.is_sensitive) return "";
			return entry.value || "";
		},
		[entries],
	);

	const isKeySet = useCallback(
		(key: string) => {
			const entry = entries.find((e) => e.key === key);
			return entry?.is_set || (entry?.value ? entry.value !== "" : false);
		},
		[entries],
	);

	// State
	const [provider, setProvider] = useState<ProviderId | "">("");
	const [providerOpen, setProviderOpen] = useState(false);
	const [apiKeyValue, setApiKeyValue] = useState("");
	const [showApiKey, setShowApiKey] = useState(false);
	const [apiBase, setApiBase] = useState("");
	const [apiVersion, setApiVersion] = useState("");
	const [modelSections, setModelSections] = useState("");
	const [modelSynthesis, setModelSynthesis] = useState("");
	const [modelFacets, setModelFacets] = useState("");
	const [batchEnabled, setBatchEnabled] = useState(true);
	const [batchPeriod, setBatchPeriod] = useState("14");
	const [minSessions, setMinSessions] = useState("5");
	const [maxFacetCalls, setMaxFacetCalls] = useState("100");
	const [facetConcurrency, setFacetConcurrency] = useState("25");
	const [saving, setSaving] = useState<Record<string, boolean>>({});
	const [testingConnection, setTestingConnection] = useState(false);
	const [testResult, setTestResult] = useState<TestResult | null>(null);
	const providerCatalog = useInsightsModelProviders();
	const modelCatalog = useInsightsModels(provider);

	// Sync from entries. A field only follows the server when it has not been edited,
	// so saving one card (which refetches) never discards drafts in another.
	const lastStored = useRef<Record<string, string>>({});
	useEffect(() => {
		const sync = (key: string, setter: (update: (current: string) => string) => void, fallback = "") => {
			const next = getEntryValue(key) || fallback;
			const previous = lastStored.current[key];
			setter((current) => (previous === undefined || current === previous ? next : current));
			lastStored.current[key] = next;
		};
		sync("insights.model_sections", setModelSections);
		sync("insights.model_synthesis", setModelSynthesis);
		sync("insights.model_facets", setModelFacets);
		sync("insights.api_base", setApiBase);
		sync("insights.api_version", setApiVersion);
		sync("insights.batch_period_days", setBatchPeriod, "14");
		sync("insights.min_sessions", setMinSessions, "5");
		sync("insights.facet_max_calls", setMaxFacetCalls, "100");
		sync("insights.facet_concurrency", setFacetConcurrency, "25");
		setBatchEnabled(getEntryValue("insights.batch_enabled") !== "false");

		const detected = detectProvider(getEntryValue("insights.model_sections"));
		if (detected) setProvider(detected);
	}, [entries, getEntryValue]);

	// Auto-detect provider from API key
	useEffect(() => {
		if (apiKeyValue && !provider) {
			const detected = detectProviderFromKey(apiKeyValue);
			if (detected) setProvider(detected);
		}
	}, [apiKeyValue, provider]);

	// Derived values
	const status: InsightsStatus = useMemo(() => getInsightsStatus(entries), [entries]);
	const providerMetadata = useMemo(() => new Map(PROVIDERS.map((p) => [p.id, p])), []);
	const providerOptions = useMemo(
		() => providerCatalog.data?.providers ?? [],
		[providerCatalog.data?.providers],
	);
	const selectedProvider = useMemo(
		() => providerOptions.find((p) => p.id === provider),
		[providerOptions, provider],
	);
	const selectedProviderMetadata = provider ? providerMetadata.get(provider) : undefined;
	const showBaseUrl = provider !== "";
	const showApiVersion = provider === "azure";
	const suggestions: ModelSuggestion[] = useMemo(
		() =>
			(modelCatalog.data?.models ?? []).map((model) => ({
				id: model.litellm_model,
				label: model.model_id,
				tier: "catalog",
			})),
		[modelCatalog.data?.models],
	);
	const showRecommended = useMemo(
		() =>
			provider !== "" &&
			RECOMMENDED_MODELS[provider] &&
			(!modelSections || !modelSynthesis || !modelFacets),
		[provider, modelSections, modelSynthesis, modelFacets],
	);

	// Handlers
	const handleSave = useCallback(
		async (key: string, value: string) => {
			setSaving((s) => ({ ...s, [key]: true }));
			try {
				await onSave(key, value);
			} catch {
				toast.error(`Failed to save ${key.split(".")[1]}`);
			} finally {
				setSaving((s) => ({ ...s, [key]: false }));
			}
		},
		[onSave],
	);

	const handleApiKeySave = useCallback(async () => {
		if (!apiKeyValue) return;
		await handleSave("insights.api_key", apiKeyValue);
		setApiKeyValue("");
	}, [apiKeyValue, handleSave]);

	const handleTestConnection = useCallback(async () => {
		setTestingConnection(true);
		setTestResult(null);
		try {
			const modelsToTest: { model: string; label: string }[] = [];
			if (modelSections) modelsToTest.push({ model: modelSections, label: "Sections" });
			if (modelSynthesis && modelSynthesis !== modelSections) modelsToTest.push({ model: modelSynthesis, label: "Synthesis" });
			if (modelFacets && modelFacets !== modelSections && modelFacets !== modelSynthesis) modelsToTest.push({ model: modelFacets, label: "Facets" });

			if (modelsToTest.length === 0) {
				setTestResult({
					success: false,
					error: "No models configured",
					hint: "Set at least the Sections Model before testing.",
				});
				return;
			}

			for (const { model, label } of modelsToTest) {
				const result = (await admin.testInsightsConnection({ model })) as TestResult;
				if (!result.success) {
					setTestResult({
						...result,
						error: `${label} Model failed: ${result.error}`,
					});
					return;
				}
			}

			setTestResult({
				success: true,
				model: modelsToTest.length === 1
					? modelsToTest[0].model
					: `All ${modelsToTest.length} models connected`,
				latency_ms: undefined,
			});
		} catch (e) {
			setTestResult({
				success: false,
				error: e instanceof Error ? e.message : "Request failed",
				hint: "Could not reach the server. Is it running?",
			});
		} finally {
			setTestingConnection(false);
		}
	}, [modelSections, modelSynthesis, modelFacets]);

	// Typed values are drafted per card and saved from the card footer.
	const handleUseRecommended = useCallback(() => {
		if (!provider || !RECOMMENDED_MODELS[provider]) return;
		const rec = RECOMMENDED_MODELS[provider];
		setModelSections(rec.sections);
		setModelSynthesis(rec.synthesis);
		setModelFacets(rec.facets);
	}, [provider]);

	const providerDrafts: [string, string][] = [
		["insights.api_base", apiBase],
		...(showApiVersion ? ([["insights.api_version", apiVersion]] as [string, string][]) : []),
	];
	const modelDrafts: [string, string][] = [
		["insights.model_sections", modelSections],
		["insights.model_synthesis", modelSynthesis],
		["insights.model_facets", modelFacets],
	];
	const advancedDrafts: [string, string][] = [
		["insights.batch_period_days", batchPeriod],
		["insights.min_sessions", minSessions],
		["insights.facet_max_calls", maxFacetCalls],
		["insights.facet_concurrency", facetConcurrency],
	];
	const storedOrDefault = useCallback((key: string) => getEntryValue(key) || ADVANCED_DEFAULTS[key] || "", [getEntryValue]);
	const changed = (drafts: [string, string][]) => drafts.filter(([key, value]) => value !== storedOrDefault(key));
	const providerChanged = showBaseUrl ? changed(providerDrafts) : [];
	const modelsChanged = changed(modelDrafts);
	const advancedChanged = changed(advancedDrafts);
	const anyDirty = providerChanged.length + modelsChanged.length + advancedChanged.length > 0;
	useEffect(() => {
		onDirtyChange?.("insights", anyDirty);
	}, [anyDirty, onDirtyChange]);
	useEffect(() => () => onDirtyChange?.("insights", false), [onDirtyChange]);

	const [savingCard, setSavingCard] = useState<string | null>(null);
	const saveCard = async (card: string, drafts: [string, string][]) => {
		setSavingCard(card);
		try {
			for (const [key, value] of drafts) await handleSave(key, value);
		} finally {
			setSavingCard(null);
		}
	};
	const resetDrafts = (drafts: [string, string][], setters: Record<string, (value: string) => void>) => {
		for (const [key] of drafts) setters[key]?.(storedOrDefault(key));
	};

	const handleProviderChange = useCallback(
		(id: ProviderId | "") => {
			const previousProvider = provider;
			setProvider(id);

			if (id !== previousProvider && previousProvider !== "") {
				setModelSections("");
				setModelSynthesis("");
				setModelFacets("");
				void (async () => {
					try {
						await Promise.all([
							onSave("insights.model_sections", ""),
							onSave("insights.model_synthesis", ""),
							onSave("insights.model_facets", ""),
						]);
					} catch {
						toast.error("Failed to clear model settings for the previous provider");
						refetch();
					}
				})();
			}

			setProviderOpen(false);
		},
		[onSave, provider, refetch],
	);

	const statusLabel = { not_configured: "Not configured", partial: "Partially configured", ready: "Ready" }[status];
	const statusTone = ({ not_configured: "neutral", partial: "warning", ready: "success" } as const)[status];
	const ids = {
		provider: rowDomId("insights.provider") + "-control",
		apiKey: rowDomId("insights.api_key") + "-control",
		apiBase: rowDomId("insights.api_base") + "-control",
		apiVersion: rowDomId("insights.api_version") + "-control",
		batch: rowDomId("insights.batch_enabled") + "-control",
	};
	// The advanced limits are counts; anything but a positive whole number is unusable.
	const numberError = (value: string) => (/^[1-9]\d*$/.test(value.trim()) ? undefined : "Enter a whole number of 1 or more");
	const advancedInvalid = advancedDrafts.some(([, value]) => !!numberError(value));
	const numberRow = (key: string, label: string, value: string, setter: (value: string) => void) => {
		const error = numberError(value);
		return (
			<SettingRow key={key} settingKey={key} label={label} htmlFor={rowDomId(key) + "-control"} error={error}>
				<Input
					id={rowDomId(key) + "-control"}
					type="number"
					min={1}
					step={1}
					value={value}
					aria-invalid={!!error}
					onChange={(event) => setter(event.target.value)}
					className={cn("min-h-9 flex-1 py-2 text-sm tabular-nums", error && "border-destructive/60")}
				/>
			</SettingRow>
		);
	};

	return (
		<>
			<SettingsGroup
				id="provider"
				title="Agent File Insights"
				description="Configure LLM provider for the insights engine. Supports any LiteLLM-compatible provider."
				docsTitle="Agent File Insights"
				tools={<StatusBadge tone={statusTone}>{statusLabel}</StatusBadge>}
			>
				<SettingsCard
					footer={
						<CardFooter
							dirtyCount={providerChanged.length}
							saving={savingCard === "provider"}
							onSave={() => void saveCard("provider", providerChanged)}
							onDiscard={() => resetDrafts(providerDrafts, { "insights.api_base": setApiBase, "insights.api_version": setApiVersion })}
						/>
					}
				>
					<SettingRow
						settingKey="insights.provider"
						label="Provider"
						htmlFor={ids.provider}
						error={providerCatalog.isError ? "Failed to load LiteLLM providers." : undefined}
					>
						<Popover open={providerOpen} onOpenChange={setProviderOpen}>
							<PopoverTrigger asChild>
								<Button
									id={ids.provider}
									variant="outline"
									role="combobox"
									aria-expanded={providerOpen}
									className={cn("min-h-9 flex-1 justify-between text-sm font-normal", !provider && "text-muted-foreground")}
								>
									{selectedProvider?.label || provider || "Select provider..."}
									<ChevronsUpDown className="size-3.5 shrink-0 opacity-50" aria-hidden="true" />
								</Button>
							</PopoverTrigger>
							<PopoverContent className="w-[var(--radix-popover-trigger-width)] p-0" align="start">
								<Command>
									<CommandInput placeholder="Search providers..." />
									<CommandList>
										<CommandEmpty>No provider found.</CommandEmpty>
										<CommandGroup>
											{providerOptions.map((p) => (
												<CommandItem key={p.id} value={`${p.label} ${p.id}`} onSelect={() => handleProviderChange(p.id)}>
													<Check className={cn("mr-2 h-3.5 w-3.5", provider === p.id ? "opacity-100" : "opacity-0")} />
													<span>{p.label}</span>
													<span className="ml-auto text-xs text-muted-foreground">{p.model_count}</span>
												</CommandItem>
											))}
										</CommandGroup>
									</CommandList>
								</Command>
							</PopoverContent>
						</Popover>
					</SettingRow>

					<SettingRow settingKey="insights.api_key" label="API Key" htmlFor={ids.apiKey}>
						<div className="flex flex-1 flex-col gap-2">
							<div className="relative">
								<Input
									id={ids.apiKey}
									type={showApiKey ? "text" : "password"}
									value={apiKeyValue}
									onChange={(e) => setApiKeyValue(e.target.value)}
									placeholder={isKeySet("insights.api_key") ? "Key is set, enter new value to replace" : "sk-ant-... or sk-... or Bedrock API key"}
									autoComplete="new-password"
									spellCheck={false}
									className="min-h-9 py-2 pr-10 font-mono text-xs"
									onKeyDown={(e) => {
										if (e.key === "Enter") handleApiKeySave();
									}}
								/>
								<button
									type="button"
									className="absolute right-1 top-1/2 grid size-7 -translate-y-1/2 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
									onClick={() => setShowApiKey(!showApiKey)}
									aria-label={showApiKey ? "Hide key" : "Show key"}
									aria-pressed={showApiKey}
								>
									{showApiKey ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
								</button>
							</div>
							<div className="flex flex-wrap justify-end gap-2">
								<Button size="sm" variant="outline" onClick={handleApiKeySave} disabled={!apiKeyValue || saving["insights.api_key"]}>
									{saving["insights.api_key"] ? <Loader2 className="animate-spin" aria-hidden="true" /> : "Save"}
								</Button>
								<Button size="sm" variant="outline" onClick={handleTestConnection} disabled={testingConnection}>
									{testingConnection && <Loader2 className="animate-spin" aria-hidden="true" />}
									Test Connection
								</Button>
								{isKeySet("insights.api_key") && (
									<Button size="sm" variant="ghost" className="text-destructive hover:text-destructive" onClick={() => onRevoke("insights.api_key")}>
										Revoke
									</Button>
								)}
							</div>
							{testResult && (
								<div
									role="status"
									className={cn(
										"flex items-start gap-2 rounded-lg border px-3 py-2 text-xs",
										testResult.success ? "border-success/20 bg-success/10 text-success" : "border-destructive/20 bg-destructive/10 text-destructive",
									)}
								>
									{testResult.success ? (
										<>
											<Check className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
											<span>
												Connected to <span className="font-mono font-medium">{testResult.model}</span>
												{testResult.latency_ms != null && ` (${testResult.latency_ms}ms)`}
											</span>
										</>
									) : (
										<div className="space-y-0.5">
											<p className="font-medium">{testResult.error}</p>
											{testResult.hint && <p className="text-muted-foreground">{testResult.hint}</p>}
										</div>
									)}
								</div>
							)}
						</div>
					</SettingRow>

					<SettingRow
						settingKey="insights.api_base"
						label="Base URL"
						htmlFor={ids.apiBase}
						labelNote={selectedProviderMetadata?.requiresBaseUrl ? `(required for ${selectedProvider?.label || provider})` : "(optional)"}
						hidden={!showBaseUrl}
					>
						<Input
							id={ids.apiBase}
							value={apiBase}
							onChange={(e) => setApiBase(e.target.value)}
							placeholder={selectedProviderMetadata?.baseUrlHint || "https://..."}
							spellCheck={false}
							className="min-h-9 flex-1 py-2 font-mono text-xs"
						/>
					</SettingRow>
					<SettingRow settingKey="insights.api_version" label="API Version" htmlFor={ids.apiVersion} hidden={!showBaseUrl || !showApiVersion}>
						<Input
							id={ids.apiVersion}
							value={apiVersion}
							onChange={(e) => setApiVersion(e.target.value)}
							placeholder="2024-08-01-preview"
							spellCheck={false}
							className="min-h-9 flex-1 py-2 font-mono text-xs"
						/>
					</SettingRow>
				</SettingsCard>
			</SettingsGroup>

			<SettingsGroup
				id="models"
				title="Models"
				description={
					<>
						Only Sections Model is required. Others fall back to it if left empty.
						{modelCatalog.isError && <span className="block text-destructive">Failed to load LiteLLM models for {provider}.</span>}
					</>
				}
				tools={
					showRecommended ? (
						<Button variant="outline" size="sm" onClick={handleUseRecommended}>
							<Sparkles aria-hidden="true" />
							Use recommended models
						</Button>
					) : undefined
				}
			>
				<SettingsCard
					footer={
						<CardFooter
							dirtyCount={modelsChanged.length}
							saving={savingCard === "models"}
							onSave={() => void saveCard("models", modelsChanged)}
							onDiscard={() =>
								resetDrafts(modelDrafts, {
									"insights.model_sections": setModelSections,
									"insights.model_synthesis": setModelSynthesis,
									"insights.model_facets": setModelFacets,
								})
							}
						/>
					}
				>
					{(
						[
							["insights.model_sections", "Sections Model", "Writes the detailed report. Use your best model.", modelSections, setModelSections, "Required"],
							["insights.model_synthesis", "Synthesis Model", "Aggregates patterns. Falls back to Sections if empty.", modelSynthesis, setModelSynthesis, "Optional"],
							["insights.model_facets", "Facets Model", "Scans each session. Runs many times, use cheapest.", modelFacets, setModelFacets, "Optional"],
						] as const
					).map(([key, label, subtitle, value, setter, placeholder]) => (
						<SettingRow key={key} settingKey={key} label={label} description={subtitle}>
							<div className="flex-1">
								<ModelCombobox hideLabel label={label} subtitle={subtitle} value={value} onChange={setter} suggestions={suggestions} placeholder={placeholder} />
							</div>
						</SettingRow>
					))}
				</SettingsCard>
			</SettingsGroup>

			<AdvancedGroup id="insights-advanced" title="Advanced" description="Batch processing and facet limits." count={5}>
				<SettingsCard
					flush
					footer={
						<CardFooter
							dirtyCount={advancedChanged.length}
							invalid={advancedInvalid}
							saving={savingCard === "advanced"}
							onSave={() => void saveCard("advanced", advancedChanged)}
							onDiscard={() =>
								resetDrafts(advancedDrafts, {
									"insights.batch_period_days": setBatchPeriod,
									"insights.min_sessions": setMinSessions,
									"insights.facet_max_calls": setMaxFacetCalls,
									"insights.facet_concurrency": setFacetConcurrency,
								})
							}
						/>
					}
				>
					<SettingRow
						settingKey="insights.batch_enabled"
						label="Batch Processing"
						htmlFor={ids.batch}
						layout="inline"
						description="Automatically generate reports on schedule"
					>
						<SwitchStateLabel checked={batchEnabled} />
						<Switch
							id={ids.batch}
							checked={batchEnabled}
							aria-label="Batch Processing"
							onCheckedChange={(checked) => {
								setBatchEnabled(checked);
								handleSave("insights.batch_enabled", String(checked));
							}}
						/>
					</SettingRow>
					{numberRow("insights.batch_period_days", "Batch Period (days)", batchPeriod, setBatchPeriod)}
					{numberRow("insights.min_sessions", "Min Sessions", minSessions, setMinSessions)}
					{numberRow("insights.facet_max_calls", "Max Facet Calls", maxFacetCalls, setMaxFacetCalls)}
					{numberRow("insights.facet_concurrency", "Facet Concurrency", facetConcurrency, setFacetConcurrency)}
				</SettingsCard>
			</AdvancedGroup>
		</>
	);
}
