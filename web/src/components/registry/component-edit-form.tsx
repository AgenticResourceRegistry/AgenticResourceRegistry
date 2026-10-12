// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0


import { useState, useEffect, useMemo, useRef, useCallback } from "react";
import { toast } from "sonner";
import { ArrowRight, Loader2, RotateCcw, Construction, Upload, FolderUp, File, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
	CodeEditor,
	codeLanguageFromFilename,
	codeLanguageLabel,
} from "@/components/ui/code-editor";
import { parseMcpConfigJson, applyParsedConfig } from "@/lib/mcp-parser";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import {
	usePublishComponentVersion,
	useComponentVersionSuggestions,
} from "@/hooks/use-api";
import { VersionBumpDialog } from "@/components/registry/version-bump-dialog";
import type { RegistryType } from "@/lib/api";
import type { RegistryItem } from "@/lib/types";

// ── Constants ──────────────────────────────────────────────────────

const HOOK_EVENTS = [
	"SessionStart",
	"PreToolUse",
	"PostToolUse",
	"PostToolUseFailure",
	"SubagentStart",
	"SubagentStop",
	"BeforeShellExecution",
	"AfterShellExecution",
	"AfterFileEdit",
	"PreCompact",
	"Stop",
	"UserPromptSubmit",
];

const HANDLER_TYPES = ["shell", "http", "script"];
const EXECUTION_MODES = ["async", "blocking"];
const SCOPE_OPTIONS = ["agent", "global"];

// ── Types ──────────────────────────────────────────────────────────

interface ComponentEditFormProps {
	listingId: string;
	type: RegistryType;
	currentVersion: string;
	item: RegistryItem;
	onSuccess?: () => void;
}

interface HookFieldState {
	event: string;
	handler_type: string;
	execution_mode: string;
	priority: string;
	handler_config: string;
	scope: string;
	tool_filter: string;
	source_url: string;
	source_ref: string;
	script_content: string;
	script_filename: string;
}

interface SkillExtraFile {
	path: string;
	content: string;
	executable?: boolean;
}

interface SkillFieldState {
	task_type: string;
	skill_path: string;
	git_url: string;
	git_ref: string;
	slash_command: string;
	skill_md_content: string;
	script_content: string;
	script_filename: string;
	extra_files: SkillExtraFile[];
}

interface PromptFieldState {
	category: string;
	template: string;
	variables: string;
	model_hints: string;
	tags: string;
}

// ── MCP Edit Form ──────────────────────────────────────────────────

function McpEditForm({
	listingId,
	type,
	currentVersion,
	item,
	onSuccess,
}: {
	listingId: string;
	type: RegistryType;
	currentVersion: string;
	item: RegistryItem;
	onSuccess?: () => void;
}) {
	// Build current config as editable JSON for the textarea
	const itemCommand = (item.command as string) ?? "";
	const itemUrl = (item.url as string) ?? "";
	const itemTransport = (item.transport as string) ?? "";
	const itemName = (item.name as string) ?? "";
	const itemArgs = Array.isArray(item.args) ? (item.args as string[]) : [];
	const itemEnvVars = Array.isArray(item.environment_variables)
		? (item.environment_variables as { name: string }[])
		: [];

	const currentConfigJson = useMemo(() => {
		const cfg: Record<string, unknown> = {};
		if (itemCommand) {
			cfg.command = itemCommand;
			if (itemArgs.length > 0) cfg.args = itemArgs;
			if (itemEnvVars.length > 0) {
				cfg.env = Object.fromEntries(
					itemEnvVars.map((ev) => [ev.name, `$${ev.name}`]),
				);
			}
		} else if (itemUrl) {
			cfg.url = itemUrl;
			if (itemTransport) cfg.type = itemTransport;
		}
		if (Object.keys(cfg).length === 0) return "";
		const wrapper = { mcpServers: { [itemName]: cfg } };
		return JSON.stringify(wrapper, null, 2);
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [
		itemCommand,
		itemUrl,
		itemTransport,
		itemName,
		JSON.stringify(itemArgs),
		JSON.stringify(itemEnvVars),
	]);

	const [jsonInput, setJsonInput] = useState(currentConfigJson);
	const [jsonError, setJsonError] = useState<string | null>(null);
	const [jsonParsed, setJsonParsed] = useState(false);
	const [description, setDescription] = useState(
		(item.description as string) ?? "",
	);
	const [command, setCommand] = useState((item.command as string) ?? "");
	const [args, setArgs] = useState(
		Array.isArray(item.args) ? (item.args as string[]).join(" ") : "",
	);
	const [mcpUrl, setMcpUrl] = useState((item.url as string) ?? "");
	const [transport, setTransport] = useState((item.transport as string) ?? "");
	const [framework, setFramework] = useState((item.framework as string) ?? "");
	const [dockerImage, setDockerImage] = useState(
		(item.docker_image as string) ?? "",
	);
	const [envVars, setEnvVars] = useState<
		{ name: string; description: string; required: boolean }[]
	>(
		Array.isArray(item.environment_variables)
			? (item.environment_variables as {
					name: string;
					description: string;
					required: boolean;
				}[])
			: [],
	);
	const [changelog, setChangelog] = useState("");
	const [showVersionDialog, setShowVersionDialog] = useState(false);
	const [publishing, setPublishing] = useState(false);

	const publishVersion = usePublishComponentVersion();
	const { data: versionSuggestions } = useComponentVersionSuggestions(
		type,
		listingId,
	);

	const itemDescription = (item.description as string) ?? "";
	const isDirty = useMemo(() => {
		return (
			jsonParsed ||
			changelog.trim() !== "" ||
			description !== itemDescription ||
			jsonInput !== currentConfigJson
		);
	}, [
		jsonParsed,
		changelog,
		description,
		jsonInput,
		currentConfigJson,
		itemDescription,
	]);

	const jsonParseTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

	const handleJsonInput = useCallback((value: string) => {
		setJsonInput(value);
		setJsonError(null);
		setJsonParsed(false);

		clearTimeout(jsonParseTimerRef.current);
		if (!value.trim()) return;

		jsonParseTimerRef.current = setTimeout(() => {
			const { parsed, error } = parseMcpConfigJson(value);
			if (error) {
				setJsonError(error);
				return;
			}
			if (!parsed) return;

			applyParsedConfig(
				parsed,
				{
					setCommand,
					setArgs,
					setMcpUrl,
					setTransport,
					setFramework,
					setDockerImage,
					setEnvVars,
					setDescription,
				},
				"fill",
			);
			setJsonParsed(true);
		}, 300);
	}, []);

	function buildBody(version: string): Record<string, unknown> {
		const body: Record<string, unknown> = {
			version,
			description: description.trim() || undefined,
			changelog: changelog.trim() || undefined,
		};
		const extra: Record<string, unknown> = {};
		if (command) extra.command = command;
		if (args.trim()) extra.args = args.split(/\s+/).filter(Boolean);
		if (mcpUrl) extra.url = mcpUrl;
		if (transport) extra.transport = transport;
		if (framework) extra.framework = framework;
		if (dockerImage) extra.docker_image = dockerImage;
		if (envVars.length > 0) extra.environment_variables = envVars;
		if (Object.keys(extra).length > 0) body.extra = extra;
		return body;
	}

	async function handleRelease(selectedVersion: string) {
		setPublishing(true);
		try {
			const body = buildBody(selectedVersion);
			await publishVersion.mutateAsync({ type, listingId, body });
			setShowVersionDialog(false);
			setJsonInput("");
			setJsonParsed(false);
			setChangelog("");
			onSuccess?.();
		} catch {
			// handled by mutation
		} finally {
			setPublishing(false);
		}
	}

	return (
		<div className="space-y-6">
			<section className="space-y-4">
				<div className="space-y-2">
					<Label htmlFor="mcp-name" className="text-sm font-medium">
						Name
					</Label>
					<Input
						id="mcp-name"
						value={item.name}
						disabled
						className="max-w-md bg-muted/40 text-muted-foreground"
					/>
					<p className="text-xs text-muted-foreground">
						Component name cannot be changed after creation.
					</p>
				</div>

				<div className="space-y-2">
					<Label htmlFor="mcp-description" className="text-sm font-medium">
						Description
					</Label>
					<Textarea
						id="mcp-description"
						value={description}
						onChange={(e) => setDescription(e.target.value)}
						rows={3}
						className="max-w-lg resize-y"
					/>
				</div>

				<div className="space-y-2">
					<Label htmlFor="mcp-changelog" className="text-sm font-medium">
						Changelog
					</Label>
					<Textarea
						id="mcp-changelog"
						placeholder="What changed in this version?"
						value={changelog}
						onChange={(e) => setChangelog(e.target.value)}
						rows={2}
						className="max-w-lg resize-y"
					/>
				</div>
			</section>

			<Separator />

			<section className="space-y-4">
				<div>
					<h3 className="text-sm font-medium font-[family-name:var(--font-display)]">
						Update Server Config
					</h3>
					<p className="mt-1 text-xs text-muted-foreground">
						Paste your updated server JSON config below. Accepts harness config,
						bare config, SSE, or server.json formats.
					</p>
				</div>

				<div className="space-y-2">
					<CodeEditor
						id="mcp-json"
						value={jsonInput}
						onChange={handleJsonInput}
						language="json"
						placeholder="Paste your updated MCP JSON config here."
					/>
					<p className="text-xs text-muted-foreground">
						Paste directly or type JSON. Brackets and quotes auto-close.
					</p>
					{jsonError && <p className="text-xs text-destructive">{jsonError}</p>}
					{jsonParsed && (
						<p className="text-xs text-green-600 flex items-center gap-1.5">
							<ArrowRight className="h-3 w-3" />
							Config parsed: {command && `${command} `}
							{args && `${args} `}
							{mcpUrl && `${mcpUrl} `}
							{envVars.length > 0 &&
								`(${envVars.length} env var${envVars.length > 1 ? "s" : ""})`}
						</p>
					)}
				</div>

				{/* Current config summary */}
				<div className="rounded-md border border-border/50 bg-muted/30 px-3 py-2 space-y-1">
					<p className="text-xs font-medium text-muted-foreground">
						Current config:
					</p>
					{command && (
						<p className="text-xs font-mono">
							command: {command} {args}
						</p>
					)}
					{mcpUrl && <p className="text-xs font-mono">url: {mcpUrl}</p>}
					{transport && (
						<p className="text-xs font-mono">transport: {transport}</p>
					)}
					{framework && (
						<p className="text-xs font-mono">framework: {framework}</p>
					)}
					{envVars.length > 0 && (
						<p className="text-xs font-mono">
							env vars: {envVars.map((e) => e.name).join(", ")}
						</p>
					)}
					{!command && !mcpUrl && (
						<p className="text-xs text-muted-foreground italic">
							No config set
						</p>
					)}
				</div>
			</section>

			<Separator />

			<div className="flex items-center gap-3">
				<Button
					onClick={() => setShowVersionDialog(true)}
					disabled={publishing || !isDirty}
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
					variant="ghost"
					onClick={() => {
						setJsonInput(currentConfigJson);
						setJsonError(null);
						setJsonParsed(false);
						setDescription((item.description as string) ?? "");
						setChangelog("");
						setCommand((item.command as string) ?? "");
						setArgs(
							Array.isArray(item.args) ? (item.args as string[]).join(" ") : "",
						);
						setMcpUrl((item.url as string) ?? "");
						setTransport((item.transport as string) ?? "");
						setFramework((item.framework as string) ?? "");
						setDockerImage((item.docker_image as string) ?? "");
						setEnvVars(
							Array.isArray(item.environment_variables)
								? (item.environment_variables as {
										name: string;
										description: string;
										required: boolean;
									}[])
								: [],
						);
					}}
					disabled={!isDirty || publishing}
					className="text-muted-foreground hover:text-foreground"
				>
					<RotateCcw className="mr-2 h-4 w-4" />
					Discard
				</Button>
			</div>

			<VersionBumpDialog
				open={showVersionDialog}
				onOpenChange={setShowVersionDialog}
				currentVersion={currentVersion}
				suggestions={versionSuggestions}
				onConfirm={handleRelease}
				publishing={publishing}
			/>
		</div>
	);
}

function SandboxEditForm({
	listingId,
	type,
	currentVersion,
	item,
	onSuccess,
}: {
	listingId: string;
	type: RegistryType;
	currentVersion: string;
	item: RegistryItem;
	onSuccess?: () => void;
}) {
	const safeJson = (value: unknown) => {
		if (value == null) return "";
		if (typeof value === "string") return value;
		try { return JSON.stringify(value, null, 2); } catch { return ""; }
	};
	const parseJson = (value: string) => {
		if (!value.trim()) return undefined;
		return JSON.parse(value);
	};
	const [description, setDescription] = useState((item.description as string) ?? "");
	const [changelog, setChangelog] = useState("");
	const [runtimeType, setRuntimeType] = useState((item.runtime_type as string) ?? "docker");
	const [image, setImage] = useState((item.image as string) ?? "");
	const [entrypoint, setEntrypoint] = useState((item.entrypoint as string) ?? "");
	const [networkPolicy, setNetworkPolicy] = useState((item.network_policy as string) ?? "none");
	const [resourceLimits, setResourceLimits] = useState(safeJson(item.resource_limits));
	const [runtimeConfig, setRuntimeConfig] = useState(safeJson(item.runtime_config));
	const [sourceUrl, setSourceUrl] = useState((item.source_url as string) ?? "");
	const [sourceRef, setSourceRef] = useState((item.source_ref as string) ?? "");
	const [sandboxPath, setSandboxPath] = useState((item.sandbox_path as string) ?? "");
	const [showVersionDialog, setShowVersionDialog] = useState(false);
	const [publishing, setPublishing] = useState(false);
	const publishVersion = usePublishComponentVersion();
	const { data: versionSuggestions } = useComponentVersionSuggestions(type, listingId);
	const isDirty = true;

	function buildBody(version: string): Record<string, unknown> {
		const extra: Record<string, unknown> = { runtime_type: runtimeType, image, network_policy: networkPolicy };
		if (entrypoint) extra.entrypoint = entrypoint;
		const limits = parseJson(resourceLimits);
		if (limits !== undefined) extra.resource_limits = limits;
		const config = parseJson(runtimeConfig);
		if (config !== undefined) extra.runtime_config = config;
		if (sourceUrl) extra.source_url = sourceUrl;
		if (sourceRef) extra.source_ref = sourceRef;
		if (sandboxPath) extra.sandbox_path = sandboxPath;
		return { version, description: description.trim() || undefined, changelog: changelog.trim() || undefined, extra };
	}

	async function handleRelease(selectedVersion: string) {
		setPublishing(true);
		try {
			await publishVersion.mutateAsync({ type, listingId, body: buildBody(selectedVersion) });
			setShowVersionDialog(false);
			onSuccess?.();
		} finally {
			setPublishing(false);
		}
	}

	return (
		<div className="space-y-6">
			<section className="space-y-4">
				<div className="space-y-2">
					<Label>Name</Label>
					<Input value={String(item.name ?? "")} disabled className="max-w-md bg-muted/40 text-muted-foreground" />
				</div>
				<div className="space-y-2">
					<Label>Description</Label>
					<Textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3} className="max-w-lg" />
				</div>
				<div className="space-y-2">
					<Label>Changelog</Label>
					<Textarea value={changelog} onChange={(e) => setChangelog(e.target.value)} rows={2} placeholder="What changed in this version?" className="max-w-lg" />
				</div>
			</section>
			<Separator />
			<section className="space-y-4">
				<div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
					<div className="space-y-2">
						<Label>Runtime</Label>
						<Input value={runtimeType} onChange={(e) => setRuntimeType(e.target.value)} placeholder="docker" />
					</div>
					<div className="space-y-2">
						<Label>Network Policy</Label>
						<Input value={networkPolicy} onChange={(e) => setNetworkPolicy(e.target.value)} placeholder="none" />
					</div>
				</div>
				<div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
					<div className="space-y-2">
						<Label>Image / Artifact Ref</Label>
						<Input value={image} onChange={(e) => setImage(e.target.value)} placeholder="python:3.12-slim" />
					</div>
					<div className="space-y-2">
						<Label>Entrypoint</Label>
						<Input value={entrypoint} onChange={(e) => setEntrypoint(e.target.value)} placeholder="bash" />
					</div>
				</div>
				<div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
					<div className="space-y-2">
						<Label>Resource Limits JSON</Label>
						<CodeEditor
							value={resourceLimits}
							onChange={setResourceLimits}
							language="json"
							minHeightClassName="min-h-32 [&_.cm-editor]:min-h-32 [&_.cm-scroller]:min-h-32"
							placeholder='{"timeout": 60, "memory_mb": 512}'
						/>
					</div>
					<div className="space-y-2">
						<Label>Runtime Config JSON</Label>
						<CodeEditor
							value={runtimeConfig}
							onChange={setRuntimeConfig}
							language="json"
							minHeightClassName="min-h-32 [&_.cm-editor]:min-h-32 [&_.cm-scroller]:min-h-32"
							placeholder='{"module": "runner.wasm"}'
						/>
					</div>
				</div>
				<div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
					<Input value={sourceUrl} onChange={(e) => setSourceUrl(e.target.value)} placeholder="Source URL" />
					<Input value={sourceRef} onChange={(e) => setSourceRef(e.target.value)} placeholder="Source ref" />
					<Input value={sandboxPath} onChange={(e) => setSandboxPath(e.target.value)} placeholder="Sandbox path" />
				</div>
			</section>
			<div className="flex items-center gap-3">
				<Button onClick={() => setShowVersionDialog(true)} disabled={publishing || !isDirty} className="min-w-[160px]">
					{publishing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <ArrowRight className="mr-2 h-4 w-4" />}
					Save &amp; Release
				</Button>
			</div>
			<VersionBumpDialog open={showVersionDialog} onOpenChange={setShowVersionDialog} currentVersion={currentVersion} suggestions={versionSuggestions} onConfirm={handleRelease} publishing={publishing} />
		</div>
	);
}

// ── Sub-form: Hook fields ──────────────────────────────────────────

function HookFields({
	state,
	onChange,
}: {
	state: HookFieldState;
	onChange: (patch: Partial<HookFieldState>) => void;
}) {
	return (
		<div className="space-y-4">
			<div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
				<div className="space-y-2">
					<Label htmlFor="hook-event" className="text-sm font-medium">
						Event
					</Label>
					<select
						id="hook-event"
						value={state.event}
						onChange={(e) => onChange({ event: e.target.value })}
						className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
					>
						<option value="">Select event…</option>
						{HOOK_EVENTS.map((ev) => (
							<option key={ev} value={ev}>
								{ev}
							</option>
						))}
					</select>
				</div>

				<div className="space-y-2">
					<Label htmlFor="hook-handler-type" className="text-sm font-medium">
						Handler Type
					</Label>
					<select
						id="hook-handler-type"
						value={state.handler_type}
						onChange={(e) => onChange({ handler_type: e.target.value })}
						className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
					>
						<option value="">Select type…</option>
						{HANDLER_TYPES.map((t) => (
							<option key={t} value={t}>
								{t}
							</option>
						))}
					</select>
				</div>

				<div className="space-y-2">
					<Label htmlFor="hook-execution-mode" className="text-sm font-medium">
						Execution Mode
					</Label>
					<select
						id="hook-execution-mode"
						value={state.execution_mode}
						onChange={(e) => onChange({ execution_mode: e.target.value })}
						className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
					>
						<option value="">Select mode…</option>
						{EXECUTION_MODES.map((m) => (
							<option key={m} value={m}>
								{m}
							</option>
						))}
					</select>
				</div>

				<div className="space-y-2">
					<Label htmlFor="hook-scope" className="text-sm font-medium">
						Scope
					</Label>
					<select
						id="hook-scope"
						value={state.scope}
						onChange={(e) => onChange({ scope: e.target.value })}
						className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
					>
						<option value="">Select scope…</option>
						{SCOPE_OPTIONS.map((s) => (
							<option key={s} value={s}>
								{s}
							</option>
						))}
					</select>
				</div>

				<div className="space-y-2">
					<Label htmlFor="hook-priority" className="text-sm font-medium">
						Priority
					</Label>
					<Input
						id="hook-priority"
						type="number"
						placeholder="0"
						value={state.priority}
						onChange={(e) => onChange({ priority: e.target.value })}
					/>
					<p className="text-xs text-muted-foreground">
						Lower numbers run first.
					</p>
				</div>

				<div className="space-y-2">
					<Label htmlFor="hook-file-pattern" className="text-sm font-medium">
						Source URL (optional)
					</Label>
					<Input
						id="hook-source-url"
						placeholder="https://github.com/org/hooks"
						value={state.source_url}
						onChange={(e) => onChange({ source_url: e.target.value })}
					/>
					<p className="text-xs text-muted-foreground">
						Git repo containing hook script (for auditability).
					</p>
				</div>
			</div>

			<div className="space-y-2">
				<Label htmlFor="hook-handler-config" className="text-sm font-medium">
					Handler Config
				</Label>
				<Textarea
					id="hook-handler-config"
					placeholder='{"command": "echo hello"}'
					value={state.handler_config}
					onChange={(e) => onChange({ handler_config: e.target.value })}
					rows={4}
					className="resize-y font-[family-name:var(--font-mono)] text-xs"
				/>
				<p className="text-xs text-muted-foreground">
					JSON configuration for the handler.
				</p>
			</div>

			<div className="space-y-2">
				<Label htmlFor="hook-tool-filter" className="text-sm font-medium">
					Tool Filter
				</Label>
				<Textarea
					id="hook-tool-filter"
					placeholder='{"tools": ["bash", "edit"]}'
					value={state.tool_filter}
					onChange={(e) => onChange({ tool_filter: e.target.value })}
					rows={3}
					className="resize-y font-[family-name:var(--font-mono)] text-xs"
				/>
				<p className="text-xs text-muted-foreground">
					JSON filter for which tools trigger this hook.
				</p>
			</div>
		</div>
	);
}

// ── Sub-form: Skill fields ─────────────────────────────────────────

function SkillFields({
	state,
	onChange,
}: {
	state: SkillFieldState;
	onChange: (patch: Partial<SkillFieldState>) => void;
}) {
	const scriptLanguage = codeLanguageFromFilename(state.script_filename);
	const scriptLanguageName = codeLanguageLabel(scriptLanguage);
	const defaultTab = state.git_url ? "git" : "paste";

	return (
		<div className="space-y-4">
			<div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
				<div className="space-y-2">
					<Label htmlFor="skill-task-type" className="text-sm font-medium">
						Task Type
					</Label>
					<Input
						id="skill-task-type"
						placeholder="code-review"
						value={state.task_type}
						onChange={(e) => onChange({ task_type: e.target.value })}
					/>
				</div>

				<div className="space-y-2">
					<Label htmlFor="skill-slash-command" className="text-sm font-medium">
						Slash Command
					</Label>
					<Input
						id="skill-slash-command"
						placeholder="/review"
						value={state.slash_command}
						onChange={(e) => onChange({ slash_command: e.target.value })}
					/>
				</div>
			</div>

			{state.extra_files.length > 0 && (
				<p role="status" className="text-sm text-muted-foreground">
					This release contains folder files. This editor cannot save changes to them; use a version-bound folder draft instead.
				</p>
			)}
			<Tabs defaultValue={defaultTab} className="w-full">
				<TabsList className="grid w-full grid-cols-3">
					<TabsTrigger value="git">Git</TabsTrigger>
					<TabsTrigger value="paste">Paste</TabsTrigger>
					<TabsTrigger value="upload" disabled title="Folder uploads require a version-bound draft editor">Upload (unavailable)</TabsTrigger>
				</TabsList>

				<TabsContent value="git" className="space-y-4 pt-4">
					<div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
						<div className="space-y-2">
							<Label htmlFor="skill-skill-path" className="text-sm font-medium">
								Skill Path
							</Label>
							<Input
								id="skill-skill-path"
								placeholder="skills/my-skill"
								value={state.skill_path}
								onChange={(e) => onChange({ skill_path: e.target.value })}
							/>
						</div>

						<div className="space-y-2">
							<Label htmlFor="skill-git-ref" className="text-sm font-medium">
								Git Ref
							</Label>
							<Input
								id="skill-git-ref"
								placeholder="main"
								value={state.git_ref}
								onChange={(e) => onChange({ git_ref: e.target.value })}
							/>
						</div>
					</div>

					<div className="space-y-2">
						<Label htmlFor="skill-git-url" className="text-sm font-medium">
							Git URL
						</Label>
						<Input
							id="skill-git-url"
							placeholder="https://github.com/org/skills"
							value={state.git_url}
							onChange={(e) => onChange({ git_url: e.target.value })}
						/>
					</div>
				</TabsContent>

				<TabsContent value="paste" className="space-y-4 pt-4">
					<div className="space-y-2">
						<Label htmlFor="skill-md-content" className="text-sm font-medium">
							SKILL.md
						</Label>
						<Textarea
							id="skill-md-content"
							placeholder="---\nname: my-skill\ndescription: What this skill does\n---\n\n## Instructions"
							value={state.skill_md_content}
							onChange={(e) => onChange({ skill_md_content: e.target.value })}
							rows={10}
							className="resize-y font-[family-name:var(--font-mono)] text-xs leading-relaxed"
						/>
					</div>

					<div className="space-y-2">
						<Label htmlFor="skill-script-filename" className="text-sm font-medium">
							Script Filename
						</Label>
						<Input
							id="skill-script-filename"
							placeholder="run.sh"
							value={state.script_filename}
							onChange={(e) => onChange({ script_filename: e.target.value })}
							className="font-[family-name:var(--font-mono)]"
						/>
					</div>

					<div className="space-y-2">
						<Label htmlFor="skill-script-content" className="text-sm font-medium">
							Script ({scriptLanguageName})
						</Label>
						<CodeEditor
							id="skill-script-content"
							value={state.script_content}
							onChange={(script_content) => onChange({ script_content })}
							language={scriptLanguage}
							placeholder="Paste or type the skill script here."
						/>
						<p className="text-xs text-muted-foreground">
							Detected from filename. Use .sh for Bash, .py for Python, or .mjs/.js for JavaScript.
						</p>
					</div>
				</TabsContent>

			</Tabs>
		</div>
	);
}

// ── Sub-form: Prompt fields ────────────────────────────────────────

function PromptFields({
	state,
	onChange,
}: {
	state: PromptFieldState;
	onChange: (patch: Partial<PromptFieldState>) => void;
}) {
	return (
		<div className="space-y-4">
			<div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
				<div className="space-y-2">
					<Label htmlFor="prompt-category" className="text-sm font-medium">
						Category
					</Label>
					<Input
						id="prompt-category"
						placeholder="code-review"
						value={state.category}
						onChange={(e) => onChange({ category: e.target.value })}
					/>
				</div>

				<div className="space-y-2">
					<Label htmlFor="prompt-tags" className="text-sm font-medium">
						Tags
					</Label>
					<Input
						id="prompt-tags"
						placeholder="review, quality (comma-separated)"
						value={state.tags}
						onChange={(e) => onChange({ tags: e.target.value })}
					/>
					<p className="text-xs text-muted-foreground">Comma-separated tags.</p>
				</div>
			</div>

			<div className="space-y-2">
				<Label htmlFor="prompt-template" className="text-sm font-medium">
					Template
				</Label>
				<Textarea
					id="prompt-template"
					placeholder="You are a helpful assistant…"
					value={state.template}
					onChange={(e) => onChange({ template: e.target.value })}
					rows={8}
					className="resize-y font-[family-name:var(--font-mono)] text-xs leading-relaxed"
				/>
			</div>

			<div className="space-y-2">
				<Label htmlFor="prompt-variables" className="text-sm font-medium">
					Variables
				</Label>
				<Textarea
					id="prompt-variables"
					placeholder='[{"name": "language", "type": "string"}]'
					value={state.variables}
					onChange={(e) => onChange({ variables: e.target.value })}
					rows={3}
					className="resize-y font-[family-name:var(--font-mono)] text-xs"
				/>
				<p className="text-xs text-muted-foreground">
					JSON array of variable definitions.
				</p>
			</div>

			<div className="space-y-2">
				<Label htmlFor="prompt-model-hints" className="text-sm font-medium">
					Model Hints
				</Label>
				<Textarea
					id="prompt-model-hints"
					placeholder='{"preferred_model": "claude-sonnet"}'
					value={state.model_hints}
					onChange={(e) => onChange({ model_hints: e.target.value })}
					rows={3}
					className="resize-y font-[family-name:var(--font-mono)] text-xs"
				/>
				<p className="text-xs text-muted-foreground">
					JSON hints for model selection.
				</p>
			</div>
		</div>
	);
}

// ── Inner form (for hook/skill/prompt) ────────────────────────────

function EditFormInner({
	listingId,
	type,
	singularType,
	currentVersion,
	item,
	onSuccess,
}: {
	listingId: string;
	type: RegistryType;
	singularType: string;
	currentVersion: string;
	item: RegistryItem;
	onSuccess?: () => void;
}) {
	// ── Init helpers ──────────────────────────────────────────────

	function safeJson(value: unknown): string {
		if (value == null) return "";
		if (typeof value === "string") return value;
		try {
			return JSON.stringify(value, null, 2);
		} catch {
			return "";
		}
	}

	function commaList(value: unknown): string {
		if (Array.isArray(value)) return value.join(", ");
		if (typeof value === "string") return value;
		return "";
	}

	// ── Shared state ──────────────────────────────────────────────
	const initialDescription = (item.description as string) ?? "";
	const initialChangelog = "";

	const [description, setDescription] = useState(initialDescription);
	const [changelog, setChangelog] = useState(initialChangelog);

	// ── Type-specific state ───────────────────────────────────────
	const initialHook: HookFieldState = {
		event: (item.event as string) ?? "",
		handler_type: (item.handler_type as string) ?? "",
		execution_mode: (item.execution_mode as string) ?? "",
		priority: item.priority != null ? String(item.priority) : "",
		handler_config: safeJson(item.handler_config),
		scope: (item.scope as string) ?? "",
		tool_filter: safeJson(item.tool_filter),
		source_url: (item.source_url as string) ?? "",
		source_ref: (item.source_ref as string) ?? "",
		script_content: (item.script_content as string) ?? "",
		script_filename: (item.script_filename as string) ?? "",
	};
	const initialSkill: SkillFieldState = {
		task_type: (item.task_type as string) ?? "",
		skill_path: (item.skill_path as string) ?? "",
		git_url: (item.git_url as string) ?? "",
		git_ref: (item.git_ref as string) ?? "",
		slash_command: (item.slash_command as string) ?? "",
		skill_md_content: (item.skill_md_content as string) ?? "",
		script_content: (item.script_content as string) ?? "",
		script_filename: (item.script_filename as string) ?? "",
		extra_files: (item.extra_files as SkillExtraFile[]) ?? [],
	};
	const initialPrompt: PromptFieldState = {
		category: (item.category as string) ?? "",
		template: (item.template as string) ?? "",
		variables: safeJson(item.variables),
		model_hints: safeJson(item.model_hints),
		tags: commaList(item.tags),
	};

	const [hookState, setHookState] = useState<HookFieldState>(initialHook);
	const [skillState, setSkillState] = useState<SkillFieldState>(initialSkill);
	const [promptState, setPromptState] =
		useState<PromptFieldState>(initialPrompt);

	// ── Dialog / loading state ────────────────────────────────────
	const [showVersionDialog, setShowVersionDialog] = useState(false);
	const [showDiscardConfirm, setShowDiscardConfirm] = useState(false);
	const [publishing, setPublishing] = useState(false);

	// ── Dirty tracking ────────────────────────────────────────────
	const initialRef = useRef({
		description: initialDescription,
		changelog: initialChangelog,
		hook: initialHook,
		skill: initialSkill,
		prompt: initialPrompt,
	});
	const [isDirty, setIsDirty] = useState(false);

	useEffect(() => {
		const init = initialRef.current;
		const dirty =
			description !== init.description ||
			changelog !== init.changelog ||
			JSON.stringify(hookState) !== JSON.stringify(init.hook) ||
			JSON.stringify(skillState) !== JSON.stringify(init.skill) ||
			JSON.stringify(promptState) !== JSON.stringify(init.prompt);
		setIsDirty(dirty);
	}, [description, changelog, hookState, skillState, promptState]);

	// ── API ───────────────────────────────────────────────────────
	const publishVersion = usePublishComponentVersion();
	const { data: versionSuggestions } = useComponentVersionSuggestions(
		type,
		listingId,
	);

	// ── Build request body ────────────────────────────────────────

	function tryParseJson(value: string): unknown {
		if (!value.trim()) return undefined;
		try {
			return JSON.parse(value);
		} catch {
			return value;
		}
	}

	function buildBody(version: string): Record<string, unknown> {
		const extra: Record<string, unknown> = {};

		if (singularType === "hook") {
			if (hookState.event) extra.event = hookState.event;
			if (hookState.handler_type) extra.handler_type = hookState.handler_type;
			if (hookState.execution_mode)
				extra.execution_mode = hookState.execution_mode;
			if (hookState.priority !== "")
				extra.priority = Number(hookState.priority);
			if (hookState.scope) extra.scope = hookState.scope;
			if (hookState.handler_config)
				extra.handler_config = tryParseJson(hookState.handler_config);
			if (hookState.tool_filter)
				extra.tool_filter = tryParseJson(hookState.tool_filter);
			if (hookState.source_url) extra.source_url = hookState.source_url;
			if (hookState.source_ref) extra.source_ref = hookState.source_ref;
			if (hookState.script_content) extra.script_content = hookState.script_content;
			if (hookState.script_filename) extra.script_filename = hookState.script_filename;
		} else if (singularType === "skill") {
			if (skillState.task_type) extra.task_type = skillState.task_type;
			if (skillState.skill_path) extra.skill_path = skillState.skill_path;
			if (skillState.git_url) extra.git_url = skillState.git_url;
			if (skillState.git_ref) extra.git_ref = skillState.git_ref;
			if (skillState.slash_command)
				extra.slash_command = skillState.slash_command;
			if (skillState.skill_md_content)
				extra.skill_md_content = skillState.skill_md_content;
			if (skillState.script_content)
				extra.script_content = skillState.script_content;
			if (skillState.script_filename)
				extra.script_filename = skillState.script_filename;
		} else if (singularType === "prompt") {
			if (promptState.category) extra.category = promptState.category;
			if (promptState.template) extra.template = promptState.template;
			if (promptState.variables)
				extra.variables = tryParseJson(promptState.variables);
			if (promptState.model_hints)
				extra.model_hints = tryParseJson(promptState.model_hints);
			if (promptState.tags) {
				extra.tags = promptState.tags
					.split(",")
					.map((s) => s.trim())
					.filter(Boolean);
			}
		}

		return {
			version,
			description: description.trim() || undefined,
			changelog: changelog.trim() || undefined,
			extra: Object.keys(extra).length > 0 ? extra : undefined,
		};
	}

	// ── Handlers ─────────────────────────────────────────────────

	async function handleRelease(selectedVersion: string) {
		if (singularType === "skill" && skillState.extra_files.length > 0) {
			toast.error("Folder files require the version-bound editor; this release form cannot save them. Use CLI replace-files.");
			return;
		}
		setPublishing(true);
		try {
			const body = buildBody(selectedVersion);
			await publishVersion.mutateAsync({ type, listingId, body });
			setShowVersionDialog(false);
			// Reset dirty state
			initialRef.current = {
				description,
				changelog,
				hook: hookState,
				skill: skillState,
				prompt: promptState,
			};
			setIsDirty(false);
			onSuccess?.();
		} catch {
			// toast is handled by the mutation
		} finally {
			setPublishing(false);
		}
	}

	function handleDiscard() {
		if (isDirty) {
			setShowDiscardConfirm(true);
		}
	}

	function confirmDiscard() {
		const init = initialRef.current;
		setDescription(init.description);
		setChangelog(init.changelog);
		setHookState(init.hook);
		setSkillState(init.skill);
		setPromptState(init.prompt);
		setIsDirty(false);
		setShowDiscardConfirm(false);
	}

	return (
		<div className="space-y-6">
			{/* Shared fields */}
			<section className="space-y-4">
				<div className="space-y-2">
					<Label htmlFor="comp-name" className="text-sm font-medium">
						Name
					</Label>
					<Input
						id="comp-name"
						value={item.name}
						disabled
						className="max-w-md bg-muted/40 text-muted-foreground"
					/>
					<p className="text-xs text-muted-foreground">
						Component name cannot be changed after creation.
					</p>
				</div>

				<div className="space-y-2">
					<Label htmlFor="comp-description" className="text-sm font-medium">
						Description
					</Label>
					<Textarea
						id="comp-description"
						placeholder={`What does this ${singularType} do?`}
						value={description}
						onChange={(e) => setDescription(e.target.value)}
						rows={3}
						className="max-w-lg resize-y"
					/>
				</div>

				<div className="space-y-2">
					<Label htmlFor="comp-changelog" className="text-sm font-medium">
						Changelog
					</Label>
					<Textarea
						id="comp-changelog"
						placeholder="What changed in this version?"
						value={changelog}
						onChange={(e) => setChangelog(e.target.value)}
						rows={2}
						className="max-w-lg resize-y"
					/>
					<p className="text-xs text-muted-foreground">
						Briefly describe what changed for users reviewing this version.
					</p>
				</div>
			</section>

			<Separator />

			{/* Type-specific fields */}
			<section className="space-y-4">
				<div>
					<h3 className="text-sm font-medium font-[family-name:var(--font-display)]">
						{singularType === "hook"
							? "Hook Configuration"
							: singularType === "skill"
								? "Skill Configuration"
								: "Prompt Configuration"}
					</h3>
					<p className="mt-1 text-xs text-muted-foreground">
						Configure the {singularType}-specific fields for this version.
					</p>
				</div>

				{singularType === "hook" && (
					<HookFields
						state={hookState}
						onChange={(patch) =>
							setHookState((prev) => ({ ...prev, ...patch }))
						}
					/>
				)}

				{singularType === "skill" && (
					<SkillFields
						state={skillState}
						onChange={(patch) =>
							setSkillState((prev) => ({ ...prev, ...patch }))
						}
					/>
				)}

				{singularType === "prompt" && (
					<PromptFields
						state={promptState}
						onChange={(patch) =>
							setPromptState((prev) => ({ ...prev, ...patch }))
						}
					/>
				)}
			</section>

			<Separator />

			{/* Actions */}
			<div className="flex items-center gap-3">
				<Button
					onClick={() => setShowVersionDialog(true)}
					disabled={publishing || !isDirty}
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
					variant="ghost"
					onClick={handleDiscard}
					disabled={!isDirty || publishing}
					className="text-muted-foreground hover:text-foreground"
				>
					<RotateCcw className="mr-2 h-4 w-4" />
					Discard
				</Button>
			</div>

			{/* Version Bump Dialog */}
			<VersionBumpDialog
				open={showVersionDialog}
				onOpenChange={setShowVersionDialog}
				currentVersion={currentVersion}
				suggestions={versionSuggestions}
				onConfirm={handleRelease}
				publishing={publishing}
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
						<Button
							variant="outline"
							onClick={() => setShowDiscardConfirm(false)}
						>
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

// ── Public export ──────────────────────────────────────────────────

export function ComponentEditForm({
	listingId,
	type,
	currentVersion,
	item,
	onSuccess,
}: ComponentEditFormProps) {
	const singularType =
		type === "sandboxes" ? "sandbox" : type.replace(/s$/, "");

	if (item.status === "pending") {
		return (
			<div className="rounded-md border border-dashed border-border p-8 text-center space-y-3">
				<Construction className="h-8 w-8 mx-auto text-muted-foreground" />
				<h3 className="text-sm font-semibold font-[family-name:var(--font-display)]">
					Pending Review
				</h3>
				<p className="text-xs text-muted-foreground max-w-md mx-auto">
					This component is currently pending review and cannot be edited. You
					can edit it once it has been approved or rejected.
				</p>
				<Badge variant="secondary" className="text-[10px]">
					Pending
				</Badge>
			</div>
		);
	}

	if (singularType === "mcp") {
		return (
			<McpEditForm
				listingId={listingId}
				type={type}
				currentVersion={currentVersion}
				item={item}
				onSuccess={onSuccess}
			/>
		);
	}

	if (singularType === "sandbox") {
		return (
			<SandboxEditForm
				listingId={listingId}
				type={type}
				currentVersion={currentVersion}
				item={item}
				onSuccess={onSuccess}
			/>
		);
	}

	return (
		<EditFormInner
			listingId={listingId}
			type={type}
			singularType={singularType}
			currentVersion={currentVersion}
			item={item}
			onSuccess={onSuccess}
		/>
	);
}
