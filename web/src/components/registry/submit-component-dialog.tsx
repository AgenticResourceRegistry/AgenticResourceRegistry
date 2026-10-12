// SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com>
// SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0


import { useState, useRef, useCallback, useEffect } from "react";
import {
	Dialog,
	DialogContent,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
	CodeEditor,
	codeLanguageFromFilename,
	codeLanguageLabel,
} from "@/components/ui/code-editor";
import { PickerSelect } from "@/components/ui/picker-select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Check, HelpCircle, Info, Loader2, Plus, X, Upload, FolderUp, File, Trash2 } from "lucide-react";
import { toast } from "sonner";
import type { RegistryType } from "@/lib/api";
import { useTeams, useWhoami } from "@/hooks/use-api";
import { useHarnesses } from "@/hooks/use-harnesses";
import { useDeploymentConfig } from "@/hooks/use-deployment-config";
import { parseMcpConfigJson, applyParsedConfig } from "@/lib/mcp-parser";
import type { EnvVar } from "@/lib/mcp-parser";
import { useHelp } from "@/components/wiki/help-context";

export const MCP_CATEGORIES = [
	"browser-automation",
	"cloud-platforms",
	"code-execution",
	"communication",
	"databases",
	"developer-tools",
	"devops",
	"file-systems",
	"finance",
	"knowledge-memory",
	"monitoring",
	"multimedia",
	"productivity",
	"search",
	"security",
	"version-control",
	"ai-ml",
	"data-analytics",
	"general",
];

const MCP_FRAMEWORKS = ["python", "docker", "typescript", "go"];

const MCP_TRANSPORTS = ["stdio", "sse", "streamable-http"];

function mcpExampleConfig(isEditMode: boolean, name: string) {
	return isEditMode
		? `{
  "mcpServers": {
    "${name || "my-server"}": {
      "command": "npx",
      "args": ["-y", "@example/mcp-server@latest"],
      "env": { "API_KEY": "$API_KEY" }
    }
  }
}`
		: `{
  "mcpServers": {
    "my-server": {
      "command": "npx",
      "args": ["-y", "@example/mcp-server"],
      "env": { "API_KEY": "$API_KEY" }
    }
  }
}`;
}

export const SKILL_TASK_TYPES = [
	"code-review",
	"code-generation",
	"testing",
	"documentation",
	"debugging",
	"refactoring",
	"deployment",
	"security-audit",
	"performance",
	"general",
];

export const HOOK_EVENTS = [
	"PreToolUse",
	"PostToolUse",
	"Notification",
	"Stop",
	"SubagentStop",
	"SessionStart",
	"UserPromptSubmit",
];

const HOOK_HANDLER_TYPES = ["command", "http"];
const HOOK_EXECUTION_MODES = ["async", "sync", "blocking"];
export const HOOK_SCOPES = ["agent", "session", "global"];

export const PROMPT_CATEGORIES = [
	"system-prompt",
	"code-review",
	"code-generation",
	"testing",
	"documentation",
	"debugging",
	"general",
];

export const SANDBOX_RUNTIME_TYPES = ["docker", "lxc", "firecracker", "wasm"];
const SANDBOX_NETWORK_POLICIES = ["none", "host", "bridge", "restricted"];

const COMPONENT_HELP_DOCS = {
	mcps: { file: "registry-mcp-helper.md", label: "MCP helper" },
	skills: { file: "registry-skill-helper.md", label: "Skill helper" },
	hooks: { file: "registry-hook-helper.md", label: "Hook helper" },
	sandboxes: { file: "registry-sandbox-helper.md", label: "Sandbox helper" },
	prompts: { file: "cli/prompt.md", label: "Prompt helper" },
	agents: { file: "core-concepts/README.md", label: "Agent helper" },
} as const;

const MAX_SKILL_UPLOAD_BYTES = 2 * 1024 * 1024;

function decodeSkillUpload(bytes: Uint8Array): { content: string; encoding?: "base64" } {
	try {
		return { content: new TextDecoder("utf-8", { fatal: true }).decode(bytes) };
	} catch {
		const chunks: string[] = [];
		for (let offset = 0; offset < bytes.length; offset += 8192) {
			chunks.push(String.fromCharCode(...bytes.subarray(offset, offset + 8192)));
		}
		return { content: btoa(chunks.join("")), encoding: "base64" };
	}
}

interface SubmitComponentDialogProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	type: RegistryType;
	onSubmit: (body: Record<string, unknown>) => void;
	onSaveDraft: (body: Record<string, unknown>) => void;
	onUpdateDraft?: (id: string, body: Record<string, unknown>) => void;
	isSubmitting: boolean;
	isSavingDraft: boolean;
	editItem?: Record<string, unknown> | null;
	fixedTeamId?: string;
	fixedVisibility?: "public" | "team";
}

export function SubmitComponentDialog({
	open,
	onOpenChange,
	type,
	onSubmit,
	onSaveDraft,
	onUpdateDraft,
	isSubmitting,
	isSavingDraft,
	editItem,
	fixedTeamId,
	fixedVisibility,
}: SubmitComponentDialogProps) {
	const d = editItem as Record<string, unknown> | null;
	const helpCtx = useHelp();
	const { data: whoami } = useWhoami();
	const { data: teams = [] } = useTeams();
	const { data: harnessList } = useHarnesses();
	const { skillFolderDeliveryEnabled, loading: folderConfigLoading, configError: folderConfigError } = useDeploymentConfig();
	const defaultOwner =
		(d?.owner as string) ||
		whoami?.username ||
		whoami?.email ||
		"";

	// ── Common ──────────────────────────────────────────────
	const [name, setName] = useState((d?.name as string) ?? "");
	const [version, setVersion] = useState((d?.version as string) ?? (type === "skills" ? "1.0.0" : "0.1.0"));
	useEffect(() => {
		if (!open && !editItem) setVersion(type === "skills" ? "1.0.0" : "0.1.0");
	}, [type, open, editItem]);
	const [description, setDescription] = useState(
		(d?.description as string) ?? "",
	);
	const owner = defaultOwner;
	const [teamId, setTeamId] = useState<string>((d?.team_id as string) ?? "");
	const [visibility, setVisibility] = useState<"public" | "team">(
		(d?.visibility as "public" | "team") ?? "public",
	);
	const selectedTeam = teams.find((team) => team.id === (fixedTeamId ?? teamId));
	const teamRequiresPrivate = selectedTeam?.visibility === "private";
	const visibilityOptions = teamRequiresPrivate
		? [{ value: "team", label: "Team members only" }]
		: [
				{ value: "public", label: "Public" },
				{ value: "team", label: "Team members only" },
			];

	useEffect(() => {
		if (teamRequiresPrivate) setVisibility("team");
	}, [teamRequiresPrivate]);

	const [supportedHarnesses, setSupportedHarnesses] = useState<string[]>(
		Array.isArray(d?.supported_harnesses) ? (d.supported_harnesses as string[]) : [],
	);

	// ── MCP ─────────────────────────────────────────────────
	const [mcpMode, setMcpMode] = useState<"json" | "manual">("json");
	const [jsonInput, setJsonInput] = useState("");
	const [jsonError, setJsonError] = useState<string | null>(null);
	const [jsonParsed, setJsonParsed] = useState(false);
	const [category, setCategory] = useState(
		(d?.category as string) ?? "general",
	);
	const [gitUrl, setGitUrl] = useState((d?.git_url as string) ?? "");
	const [command, setCommand] = useState((d?.command as string) ?? "");
	const [args, setArgs] = useState(
		Array.isArray(d?.args) ? (d.args as string[]).join(" ") : "",
	);
	const [mcpUrl, setMcpUrl] = useState((d?.url as string) ?? "");
	const [transport, setTransport] = useState((d?.transport as string) ?? "");
	const [framework, setFramework] = useState((d?.framework as string) ?? "");
	const [dockerImage, setDockerImage] = useState(
		(d?.docker_image as string) ?? "",
	);
	const [envVars, setEnvVars] = useState<EnvVar[]>(
		Array.isArray(d?.environment_variables)
			? (d.environment_variables as EnvVar[])
			: [],
	);
	const [setupInstructions, setSetupInstructions] = useState(
		(d?.setup_instructions as string) ?? "",
	);

	// ── Skill ───────────────────────────────────────────────
	const [taskType, setTaskType] = useState(
		(d?.task_type as string) ?? "general",
	);
	const [skillGitUrl, setSkillGitUrl] = useState((d?.git_url as string) ?? "");
	const [skillPath, setSkillPath] = useState((d?.skill_path as string) ?? "/");
	const [skillPathAuto, setSkillPathAuto] = useState(true); // true = auto-discovered or default
	const [skillPathHint, setSkillPathHint] = useState<string | null>(null);
	const [skillGitRef, setSkillGitRef] = useState((d?.git_ref as string) ?? "");
	const [skillMdContent, setSkillMdContent] = useState(
		(d?.skill_md_content as string) ?? "",
	);
	const [skillScriptContent, setSkillScriptContent] = useState(
		(d?.script_content as string) ?? "",
	);
	const [skillScriptFilename, setSkillScriptFilename] = useState(
		(d?.script_filename as string) ?? "",
	);
	const isFolderVersion = type === "skills" && typeof d?.folder_version_id === "string";
	const [skillMode, setSkillMode] = useState<"git" | "paste" | "upload">(
		isFolderVersion ? "upload" : d?.delivery_mode === "registry_direct" ? "paste" : "git",
	);
	// Folder mode: extra files beyond SKILL.md
	const [skillExtraFiles, setSkillExtraFiles] = useState<Array<{ path: string; content: string; executable?: boolean; encoding?: "base64" }>>(
		Array.isArray(d?.extra_files) ? d.extra_files as Array<{ path: string; content: string; executable?: boolean; encoding?: "base64" }> : [],
	);
	const [pendingSkillUploads, setPendingSkillUploads] = useState(0);
	const skillUploadGeneration = useRef(0);
	const [selectedFilePath, setSelectedFilePath] = useState<string | null>(null);

	async function handleSkillFiles(files: FileList | null, folder: boolean) {
		const selected = Array.from(files ?? []);
		if (!selected.length) return;
		if (folder && selected.some((file) => !file.webkitRelativePath || !file.webkitRelativePath.includes("/"))) {
			toast.error("Browser cannot preserve this folder's relative paths. Use `skill replace-files --from-dir` instead.");
			return;
		}
		const paths = selected.map((file) =>
			folder && file.webkitRelativePath
				? file.webkitRelativePath.split("/").slice(1).join("/")
				: file.name,
		);
		const omitted = paths.some((path) =>
			!path || path.length > 240 || path.split("/").some((segment) =>
				!segment || segment.startsWith(".") || segment === "node_modules" || segment === ".venv"),
		);
		if (omitted) {
			toast.error("Upload refused: hidden, excluded or unsafe paths were found. Use the CLI to review exclusions.");
			return;
		}
		const selectedBytes = selected.reduce((total, file) => total + file.size, 0);
		if (selected.length > 129 || selected.some((file) => file.size > MAX_SKILL_UPLOAD_BYTES)
			|| selectedBytes > 4 * 1024 * 1024) {
			toast.error(`Selected ${selected.length - (paths.includes("SKILL.md") ? 1 : 0)} extra files (${selectedBytes} bytes). Limits: 128 extra, 2097152 bytes per file, 4194304 bytes total.`);
			return;
		}
		if (folder && !paths.includes("SKILL.md")) {
			toast.error("Folder must contain SKILL.md at its root.");
			return;
		}
		if (new Set(paths.map((path) => path.normalize("NFC").toLowerCase())).size !== paths.length) {
			toast.error("Folder contains duplicate or case-colliding file paths.");
			return;
		}
		if (paths.some((path) => /(?:secret|credential|private|password|token|\.env|\.pem|\.key)/i.test(path))
			&& !window.confirm("The selected skill contains likely-sensitive files. Upload them anyway?")) return;

		const generation = skillUploadGeneration.current;
		setPendingSkillUploads((count) => count + 1);
		try {
			const captured = await Promise.all(selected.map(async (file, index) => ({
				path: paths[index],
				...decodeSkillUpload(new Uint8Array(await file.arrayBuffer())),
			})));
			if (generation !== skillUploadGeneration.current) return;
			const skillMd = captured.find((file) => file.path === "SKILL.md");
			if (skillMd?.encoding === "base64") throw new Error("SKILL.md must be UTF-8 text");
			const resources = captured.filter((file) => file.path !== "SKILL.md").map((file) => ({
				...file, executable: skillExtraFiles.find((existing) => existing.path === file.path)?.executable ?? false,
			}));
			if (folder) {
				const priorPaths = new Set(skillExtraFiles.map((file) => file.path));
				const newPaths = new Set(resources.map((file) => file.path));
				const removed = [...priorPaths].filter((path) => !newPaths.has(path));
				if (removed.length && !window.confirm(
					`Replace the whole unsaved folder? ${[...newPaths].filter((path) => !priorPaths.has(path)).length} added, ` +
					`${[...newPaths].filter((path) => priorPaths.has(path)).length} replaced, ${removed.length} removed.\n\n` +
					`Removed files: ${removed.slice(0, 10).join(", ")}${removed.length > 10 ? "…" : ""}`,
				)) return;
			}
			if (skillMd) {
				setSkillMdContent(skillMd.content);
				const frontmatter = skillMd.content.match(/^---\r?\n([\s\S]*?)\r?\n---/);
				for (const line of frontmatter?.[1].split(/\r?\n/) ?? []) {
					const parsedName = line.match(/^name:\s*(.+)$/);
					const parsedDescription = line.match(/^description:\s*["']?(.+?)["']?$/);
					if (parsedName && !name) setName(parsedName[1].trim());
					if (parsedDescription && !description) setDescription(parsedDescription[1].trim());
				}
			}
			if (resources.some((file) => !skillExtraFiles.some((old) => old.path === file.path) && /\.(?:sh|py|js|ts)$/.test(file.path))) {
				toast.message("New scripts are not executable by default. Review their executable switches before saving.");
			}
			if (folder) {
				setSkillExtraFiles(resources);
			} else {
				setSkillExtraFiles((previous) => [...previous.filter((existing) =>
					!resources.some((file) => file.path === existing.path)), ...resources]);
			}
		} catch (error) {
			if (generation === skillUploadGeneration.current) {
				toast.error(error instanceof Error ? error.message : "Could not read the selected skill files.");
			}
		} finally {
			if (generation === skillUploadGeneration.current) {
				setPendingSkillUploads((count) => count - 1);
			}
		}
	}

	// Auto-discover skill_path from GitHub Trees API when git_url changes
	const skillDiscoverRef = useRef<ReturnType<typeof setTimeout>>(undefined);
	const [skillDiscovering, setSkillDiscovering] = useState(false);
	const handleSkillGitUrl = useCallback(
		(url: string) => {
			setSkillGitUrl(url);
			setSkillPathHint(null);
			clearTimeout(skillDiscoverRef.current);
			if (!url.trim()) return;
			// Parse GitHub owner/repo from URL
			const m = url.match(/github\.com\/([^/]+)\/([^/.]+)/);
			if (!m) return;
			const [, owner, repo] = m;
			skillDiscoverRef.current = setTimeout(async () => {
				setSkillDiscovering(true);
				try {
					const ref = skillGitRef || "main";
					const res = await fetch(
						`https://api.github.com/repos/${owner}/${repo}/git/trees/${ref}?recursive=1`,
					);
					if (!res.ok) {
						setSkillDiscovering(false);
						return;
					}
					const data = await res.json();
					// Filter: find SKILL.md files, excluding harness config copies
					// harness dirs (.claude/, .kiro/, .agents/, etc.) are installed copies, not sources
					const INSTALLED_PREFIX =
						/^(\.agents|\.(claude|kiro|cursor|gemini|github|opencode|pi|trae|trae-cn|rovodev|qoder|copilot)|plugin)\//;
					const allSkillFiles = (data.tree || []).filter(
						(f: { path: string }) =>
							f.path.endsWith("/SKILL.md") || f.path === "SKILL.md",
					);
					// Prefer canonical (non-installed) paths; fall back to all if none found
					const canonical = allSkillFiles.filter(
						(f: { path: string }) => !INSTALLED_PREFIX.test(f.path),
					);
					const skillFiles = canonical.length > 0 ? canonical : allSkillFiles;
					if (skillFiles.length === 1) {
						const found =
							skillFiles[0].path.replace(/\/?SKILL\.md$/, "") || "/";
						setSkillPath(found);
						setSkillPathAuto(true);
						setSkillPathHint(
							`Found SKILL.md at ${found === "/" ? "repo root" : found}`,
						);
					} else if (skillFiles.length > 1) {
						setSkillPathAuto(false);
						setSkillPathHint(
							`${skillFiles.length} SKILL.md files found, pick a path`,
						);
					} else {
						setSkillPathAuto(false);
						setSkillPathHint("No SKILL.md found in repo");
					}
				} catch {
					/* network error, user can enter path manually */
				}
				setSkillDiscovering(false);
			}, 600);
		},
		[skillGitRef],
	);

	// ── Hook ────────────────────────────────────────────────
	const [event, setEvent] = useState((d?.event as string) ?? "PreToolUse");
	const [handlerType, setHandlerType] = useState(
		(d?.handler_type as string) ?? "command",
	);
	const [executionMode, setExecutionMode] = useState(
		(d?.execution_mode as string) ?? "async",
	);
	const [hookScope, setHookScope] = useState((d?.scope as string) ?? "agent");
	const [handlerConfig, setHandlerConfig] = useState(
		d?.handler_config && typeof d.handler_config === "object"
			? JSON.stringify(d.handler_config, null, 2)
			: "",
	);
	const [scriptContent, setScriptContent] = useState(
		(d?.script_content as string) ?? "",
	);
	const [scriptFilename, setScriptFilename] = useState(
		(d?.script_filename as string) ?? "",
	);

	// ── Prompt ──────────────────────────────────────────────
	const [promptCategory, setPromptCategory] = useState(
		type === "prompts" ? ((d?.category as string) ?? "general") : "general",
	);
	const [template, setTemplate] = useState((d?.template as string) ?? "");

	// ── Sandbox ─────────────────────────────────────────────
	const [runtimeType, setRuntimeType] = useState(
		(d?.runtime_type as string) ?? "docker",
	);
	const [image, setImage] = useState((d?.image as string) ?? "");
	const [networkPolicy, setNetworkPolicy] = useState(
		(d?.network_policy as string) ?? "none",
	);
	const [entrypoint, setEntrypoint] = useState((d?.entrypoint as string) ?? "");
	const [sandboxResourceLimits, setSandboxResourceLimits] = useState(
		d?.resource_limits && typeof d.resource_limits === "object" ? JSON.stringify(d.resource_limits, null, 2) : "{}",
	);
	const [sandboxRuntimeConfig, setSandboxRuntimeConfig] = useState(
		d?.runtime_config && typeof d.runtime_config === "object" ? JSON.stringify(d.runtime_config, null, 2) : "{}",
	);
	const [sandboxSourceUrl, setSandboxSourceUrl] = useState((d?.source_url as string) ?? "");
	const [sandboxSourceRef, setSandboxSourceRef] = useState((d?.source_ref as string) ?? "");
	const [sandboxPath, setSandboxPath] = useState((d?.sandbox_path as string) ?? "");

	function bumpPatchVersion(ver: string): string {
		const parts = ver.split(".");
		if (parts.length === 3) {
			const patch = parseInt(parts[2], 10);
			if (!isNaN(patch)) return `${parts[0]}.${parts[1]}.${patch + 1}`;
		}
		return ver;
	}

	const jsonParseTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
	const isEditing = !!editItem;

	const handleJsonInput = useCallback(
		(value: string) => {
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

				const setters = {
					setCommand,
					setArgs,
					setMcpUrl,
					setTransport,
					setFramework,
					setDockerImage,
					setEnvVars,
					setName: isEditing ? setName : !name ? setName : undefined,
					setDescription: isEditing
						? setDescription
						: !description
							? setDescription
							: undefined,
				};

				if (isEditing) {
					applyParsedConfig(parsed, setters, "overwrite");
					setVersion(bumpPatchVersion(version));
				} else {
					applyParsedConfig(parsed, setters, "fill");
				}
				setJsonParsed(true);
			}, 300);
		},
		[isEditing, name, description, version],
	);

	function reset() {
		setName("");
		setVersion(type === "skills" ? "1.0.0" : "0.1.0");
		setDescription("");
		// The publication target has to reset with everything else. Leaving it means
		// the next submission silently inherits the previous teamspace and
		// visibility, so a component meant to be public is published team-private,
		// or worse, one meant for a teamspace is published to the whole registry.
		setTeamId("");
		setVisibility("public");
		setSupportedHarnesses([]);
		setMcpMode("json");
		setJsonInput("");
		setJsonError(null);
		setJsonParsed(false);
		setCategory("general");
		setGitUrl("");
		setCommand("");
		setArgs("");
		setMcpUrl("");
		setTransport("");
		setFramework("");
		setDockerImage("");
		setEnvVars([]);
		setSetupInstructions("");
		setTaskType("general");
		setSkillGitUrl("");
		setSkillPath("/");
		setSkillPathAuto(true);
		setSkillPathHint(null);
		setSkillGitRef("");
		setSkillMdContent("");
		setSkillScriptContent("");
		setSkillScriptFilename("");
		skillUploadGeneration.current += 1;
		setPendingSkillUploads(0);
		setSkillExtraFiles([]);
		setSelectedFilePath(null);
		setSkillMode("git");
		setEvent("PreToolUse");
		setHandlerType("command");
		setExecutionMode("async");
		setHookScope("agent");
		setHandlerConfig("");
		setPromptCategory("general");
		setTemplate("");
		setRuntimeType("docker");
		setImage("");
		setNetworkPolicy("none");
		setEntrypoint("");
		setSandboxResourceLimits("{}");
		setSandboxRuntimeConfig("{}");
		setSandboxSourceUrl("");
		setSandboxSourceRef("");
		setSandboxPath("");
	}

	const isEditMode = !!editItem;
	const isPendingEdit = isEditMode && d?.status === "pending";

	function buildBody(): Record<string, unknown> {
		const effectiveTeamId = fixedTeamId ?? teamId;
		const effectiveVisibility = fixedVisibility ?? visibility;
		const base: Record<string, unknown> = {
			name,
			version,
			description,
			owner,
			visibility: effectiveVisibility,
		};
		if (effectiveTeamId) base.team_id = effectiveTeamId;
		if (supportedHarnesses.length > 0) base.supported_harnesses = supportedHarnesses;

		switch (type) {
			case "mcps": {
				const body: Record<string, unknown> = {
					...base,
					category,
				};
				if (gitUrl) body.git_url = gitUrl;
				if (command) body.command = command;
				if (args.trim()) body.args = args.split(/\s+/).filter(Boolean);
				if (mcpUrl) body.url = mcpUrl;
				if (transport) body.transport = transport;
				if (framework) body.framework = framework;
				if (dockerImage) body.docker_image = dockerImage;
				if (envVars.length > 0) body.environment_variables = envVars;
				if (setupInstructions) body.setup_instructions = setupInstructions;
				return body;
			}
			case "skills": {
				const skillBody: Record<string, unknown> = {
					...base,
					task_type: taskType,
					delivery_mode: skillMode === "git" ? "git_fetch" : "registry_direct",
				};
				if (skillMode === "git") {
					skillBody.git_url = skillGitUrl || undefined;
					skillBody.skill_path = skillPath || "/";
					if (skillGitRef) skillBody.git_ref = skillGitRef;
				} else if (skillMode === "paste") {
					if (skillMdContent) skillBody.skill_md_content = skillMdContent;
					if (skillScriptContent) skillBody.script_content = skillScriptContent;
					if (skillScriptFilename) skillBody.script_filename = skillScriptFilename;
				} else if (skillMode === "upload") {
					// Folder mode: SKILL.md + extra files
					if (skillMdContent) skillBody.skill_md_content = skillMdContent;
					skillBody.extra_files = skillExtraFiles.map((file) => ({
						path: file.path,
						content: file.content,
						encoding: file.encoding ?? "utf-8",
						executable: file.executable ?? false,
					}));
				}
				return skillBody;
			}
			case "hooks": {
				const body: Record<string, unknown> = {
					...base,
					event,
					handler_type: handlerType,
					execution_mode: executionMode,
					scope: hookScope,
				};
				if (handlerConfig.trim()) {
					try {
						body.handler_config = JSON.parse(handlerConfig);
					} catch {
						/* leave as default {} */
					}
				}
				if (scriptContent.trim()) {
					body.script_content = scriptContent;
					body.script_filename = scriptFilename || undefined;
				}
				return body;
			}
			case "prompts":
				return { ...base, category: promptCategory, template };
			case "sandboxes": {
				const body: Record<string, unknown> = {
					...base,
					runtime_type: runtimeType,
					image,
					network_policy: networkPolicy,
					entrypoint: entrypoint || undefined,
				};
				try {
					body.resource_limits = JSON.parse(sandboxResourceLimits || "{}");
					body.runtime_config = JSON.parse(sandboxRuntimeConfig || "{}");
				} catch {
					/* validation below reports JSON errors */
				}
				if (sandboxSourceUrl) body.source_url = sandboxSourceUrl;
				if (sandboxSourceRef) body.source_ref = sandboxSourceRef;
				if (sandboxPath) body.sandbox_path = sandboxPath;
				return body;
			}
			default:
				return base;
		}
	}

	function validateForSubmit(): string | null {
		if (!name) return "Name is required";
		if (!description) return "Description is required";
		if (type === "skills" && skillMode === "upload" && pendingSkillUploads > 0) {
			return "Wait for all skill files to finish uploading";
		}
		if (type === "skills" && skillMode === "upload" && !skillMdContent.trim()) {
			return "SKILL.md is required for a folder draft";
		}

		if (type === "mcps") {
			if (mcpMode === "json" && !jsonParsed && !isEditMode) {
				return "Paste a valid server config JSON";
			}
			if (mcpMode === "json" && !jsonParsed && isEditMode) {
				const d = editItem as Record<string, unknown> | null;
				if (!d?.command && !d?.url) {
					return "Paste a new server config JSON to update";
				}
			}
			if (mcpMode === "manual" && !command && !mcpUrl) {
				return "Command or Server URL is required";
			}
		}
		if (type === "prompts" && !template) {
			return "Template is required";
		}
		if (type === "sandboxes" && !image) {
			return "Image is required";
		}
		if (type === "sandboxes") {
			try {
				JSON.parse(sandboxResourceLimits || "{}");
				JSON.parse(sandboxRuntimeConfig || "{}");
			} catch {
				return "Sandbox resource limits and runtime config must be valid JSON";
			}
		}
		return null;
	}

	function handleSubmit() {
		const err = validateForSubmit();
		if (err) {
			toast.error(err);
			return;
		}
		if (type === "skills" && skillMode === "upload") {
			onSaveDraft(buildBody());
			return;
		}
		onSubmit(buildBody());
	}

	function handleDraft() {
		const err = type === "skills" && skillMode === "upload" ? validateForSubmit() : !name ? "Name is required" : null;
		if (err) {
			toast.error(err);
			return;
		}
		onSaveDraft(buildBody());
	}

	function addEnvVar() {
		setEnvVars((prev) => [
			...prev,
			{ name: "", description: "", required: true },
		]);
	}

	function updateEnvVar(
		index: number,
		field: keyof EnvVar,
		value: string | boolean,
	) {
		setEnvVars((prev) =>
			prev.map((ev, i) => (i === index ? { ...ev, [field]: value } : ev)),
		);
	}

	function removeEnvVar(index: number) {
		setEnvVars((prev) => prev.filter((_, i) => i !== index));
	}

	function toggleHarness(harness: string) {
		setSupportedHarnesses((prev) =>
			prev.includes(harness) ? prev.filter((item) => item !== harness) : [...prev, harness],
		);
	}

	const busy = isSubmitting || isSavingDraft;
	const typeLabel =
		type === "mcps"
			? "MCP Server"
			: type === "sandboxes"
				? "Sandbox"
				: type.charAt(0).toUpperCase() + type.slice(1, -1);

	const submitError = validateForSubmit();
	const jsonExample = mcpExampleConfig(isEditMode, name);
	const skillScriptLanguage = codeLanguageFromFilename(skillScriptFilename);
	const skillScriptLanguageName = codeLanguageLabel(skillScriptLanguage);
	const helpDoc = COMPONENT_HELP_DOCS[type as keyof typeof COMPONENT_HELP_DOCS];

	return (
		<Dialog
			modal={false}
			open={open}
			onOpenChange={(v) => {
				if (!v) reset();
				onOpenChange(v);
			}}
		>
			<DialogContent
				className={type === "skills" && skillMode === "upload"
					? "sm:max-w-3xl max-h-[90vh] overflow-y-auto"
					: "max-w-lg max-h-[85vh] overflow-y-auto"}
				onPointerDownOutside={(event) => {
					const target = event.target;
					if (target instanceof Node && document.querySelector('[data-help-panel="true"]')?.contains(target)) {
						event.preventDefault();
					}
				}}
			>
				{helpDoc && (
					<Button
						type="button"
						variant="ghost"
						size="sm"
						className="absolute right-10 top-4 h-6 w-6 p-0 text-muted-foreground hover:text-foreground"
						onClick={(event) => {
							event.preventDefault();
							event.stopPropagation();
							helpCtx.openHelp({ docRef: helpDoc });
						}}
						aria-label={`Open ${helpDoc.label}`}
						title={`Open ${helpDoc.label}`}
					>
						<HelpCircle className="h-4 w-4" />
					</Button>
				)}
				<DialogHeader>
					<DialogTitle>
						{isEditMode ? `Edit ${typeLabel}` : `Submit ${typeLabel}`}
					</DialogTitle>
				</DialogHeader>

				<div className="space-y-4 pt-2">
					<div className="flex items-start gap-2 rounded-md border border-border/50 bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
						<Info className="h-3.5 w-3.5 mt-0.5 shrink-0" />
						<span>
							Only submit components you created (private) or are the
							point-of-contact for (external).
						</span>
					</div>

					{/* ── Common fields ──────────────────────────────── */}
					<div className="grid grid-cols-2 gap-3">
						<div className="space-y-1.5">
							<Label htmlFor="comp-name">Name *</Label>
							<Input
								id="comp-name"
								value={name}
								onChange={(e) => setName(e.target.value)}
								placeholder="my-component"
								disabled={isFolderVersion}
							/>
						</div>
						<div className="space-y-1.5">
							<Label htmlFor="comp-version">Version</Label>
							<Input
								id="comp-version"
								value={version}
								onChange={(e) => setVersion(e.target.value)}
								placeholder={type === "skills" ? "1.0.0" : "0.1.0"}
								disabled={isFolderVersion}
							/>
						</div>
					</div>

					<div className="space-y-1.5">
						<Label htmlFor="comp-desc">Description *</Label>
						<Textarea
							id="comp-desc"
							value={description}
							onChange={(e) => setDescription(e.target.value)}
							placeholder="What does this component do?"
							rows={3}
						/>
					</div>

					<div className="grid grid-cols-2 gap-3">
						<div className="space-y-1.5">
							<Label htmlFor="component-publish-to">Publish to</Label>
							<PickerSelect
								id="component-publish-to"
								value={(fixedTeamId ?? teamId) || "personal"}
								disabled={isFolderVersion || fixedTeamId !== undefined || fixedVisibility !== undefined}
								onValueChange={(value) => {
									const next = value === "personal" ? "" : value;
									setTeamId(next);
									if (!next) {
										setVisibility("public");
									} else if (teams.find((team) => team.id === next)?.visibility === "private") {
										setVisibility("team");
									}
								}}
								options={[
									{ value: "personal", label: `Personal (${whoami?.username || whoami?.email || "me"})` },
									...teams.map((team) => ({ value: team.id, label: `Team: ${team.name}` })),
								]}
							/>
						</div>
						<div className="space-y-1.5">
							<Label htmlFor="component-visibility">Visibility</Label>
							<PickerSelect
								id="component-visibility"
								value={fixedVisibility ?? visibility}
								disabled={isFolderVersion || fixedVisibility !== undefined || teamRequiresPrivate}
								onValueChange={(value) => {
									if (value === "team" && !(fixedTeamId ?? teamId)) {
										toast.error("Team visibility requires a teamspace");
										return;
									}
									setVisibility(value as "public" | "team");
								}}
								options={visibilityOptions}
							/>
						</div>
					</div>
					<p className="text-xs text-muted-foreground">
						Public teamspace items are visible to everyone. Team-only items are limited to team members.
					</p>

					{/* ── MCP-specific ──────────────────────────────── */}
					{type === "mcps" && (
						<>
							<div className="space-y-1.5">
								<Label htmlFor="component-category">Category</Label>
								<PickerSelect
									id="component-category"
									value={category}
									onValueChange={setCategory}
									options={MCP_CATEGORIES.map((c) => ({ value: c, label: c }))}
								/>
							</div>

							{/* ── JSON paste mode (default) ─────────────── */}
							{mcpMode === "json" && (
								<>
									<div className="space-y-1.5">
										<Label htmlFor="mcp-git-url-json">
											Git URL for local OCI setup (optional)
										</Label>
										<Input
											id="mcp-git-url-json"
											value={gitUrl}
											onChange={(e) => setGitUrl(e.target.value)}
											placeholder="https://github.com/user/mcp-server"
										/>
										<p className="text-xs text-muted-foreground">
											Observal still needs pasted MCP JSON. The git repo is only
											used to detect Dockerfile, Containerfile, or compose build
											setup instructions.
										</p>
									</div>

									<div className="space-y-1.5">
										<div className="flex items-center justify-between gap-3">
											<Label htmlFor="mcp-json">
												{isEditMode
													? "Paste Updated Config (JSON)"
													: "Server Config (JSON)"}
											</Label>
											<Button
												type="button"
												variant="ghost"
												size="sm"
												onClick={() => handleJsonInput(jsonExample)}
												className="h-7 text-xs"
											>
												{jsonInput ? "Replace example" : "Insert example"}
											</Button>
										</div>
										<CodeEditor
											id="mcp-json"
											value={jsonInput}
											onChange={handleJsonInput}
											language="json"
											placeholder="Paste MCP JSON here, or insert the example."
										/>
										<p className="text-xs text-muted-foreground">
											Paste directly or type JSON. Brackets and quotes auto-close.
										</p>
										{jsonError && (
											<p className="text-xs text-destructive">{jsonError}</p>
										)}
										{jsonParsed && (
											<div className="flex items-center gap-1.5 text-xs text-green-600">
												<Check className="h-3 w-3" />
												<span>
													{isEditMode ? "Config updated" : "Config parsed"}:{" "}
													{command && `${command} `}
													{args && `${args} `}
													{mcpUrl && `${mcpUrl} `}
													{envVars.length > 0 &&
														`(${envVars.length} env var${envVars.length > 1 ? "s" : ""})`}
													{isEditMode && `, version bumped to ${version}`}
												</span>
											</div>
										)}
									</div>

									<button
										type="button"
										onClick={() => setMcpMode("manual")}
										className="text-xs text-muted-foreground hover:text-foreground underline underline-offset-2"
									>
										Advanced: enter command or URL manually
									</button>
								</>
							)}

							{/* ── Manual mode (field-by-field) ──────────── */}
							{mcpMode === "manual" && (
								<>
									<button
										type="button"
										onClick={() => setMcpMode("json")}
										className="text-xs text-muted-foreground hover:text-foreground underline underline-offset-2"
									>
										Switch to paste config
									</button>

									<div className="space-y-1.5">
										<Label>Transport</Label>
										<PickerSelect
											value={transport || "auto"}
											onValueChange={(v) => setTransport(v === "auto" ? "" : v)}
											options={[
												{ value: "auto", label: "Auto-detect" },
												...MCP_TRANSPORTS.map((t) => ({ value: t, label: t })),
											]}
										/>
									</div>

									<div className="space-y-1.5">
										<Label htmlFor="mcp-git-url">Git URL</Label>
										<Input
											id="mcp-git-url"
											value={gitUrl}
											onChange={(e) => setGitUrl(e.target.value)}
											placeholder="https://github.com/user/mcp-server"
										/>
									</div>

									<div className="grid grid-cols-2 gap-3">
										<div className="space-y-1.5">
											<Label htmlFor="mcp-command">Command</Label>
											<Input
												id="mcp-command"
												value={command}
												onChange={(e) => setCommand(e.target.value)}
												placeholder="npx, uvx, node..."
											/>
										</div>
										<div className="space-y-1.5">
											<Label htmlFor="mcp-args">Args</Label>
											<Input
												id="mcp-args"
												value={args}
												onChange={(e) => setArgs(e.target.value)}
												placeholder="space-separated args"
											/>
										</div>
									</div>

									<div className="space-y-1.5">
										<Label htmlFor="mcp-url">Server URL (SSE/HTTP)</Label>
										<Input
											id="mcp-url"
											value={mcpUrl}
											onChange={(e) => setMcpUrl(e.target.value)}
											placeholder="http://localhost:3000/sse"
										/>
									</div>

									{!command && !mcpUrl && (
										<p className="text-xs text-destructive">
											Command or Server URL is required for manual submission.
										</p>
									)}

									<div className="grid grid-cols-2 gap-3">
										<div className="space-y-1.5">
											<Label>Framework</Label>
											<PickerSelect
												value={framework || "none"}
												onValueChange={(v) => setFramework(v === "none" ? "" : v)}
												options={[
													{ value: "none", label: "None" },
													...MCP_FRAMEWORKS.map((f) => ({ value: f, label: f })),
												]}
											/>
										</div>
										<div className="space-y-1.5">
											<Label htmlFor="mcp-docker">Docker Image</Label>
											<Input
												id="mcp-docker"
												value={dockerImage}
												onChange={(e) => setDockerImage(e.target.value)}
												placeholder="user/image:tag"
											/>
										</div>
									</div>

									{/* Environment Variables */}
									<div className="space-y-2">
										<div className="flex items-center justify-between">
											<Label>Environment Variables</Label>
											<Button
												type="button"
												variant="ghost"
												size="sm"
												className="h-7 text-xs"
												onClick={addEnvVar}
											>
												<Plus className="h-3 w-3 mr-1" /> Add
											</Button>
										</div>
										{envVars.map((ev, i) => (
											<div key={i} className="flex items-center gap-2">
												<Input
													value={ev.name}
													onChange={(e) =>
														updateEnvVar(i, "name", e.target.value)
													}
													placeholder="ENV_NAME"
													className="flex-1 h-8 text-xs font-mono"
												/>
												<Input
													value={ev.description}
													onChange={(e) =>
														updateEnvVar(i, "description", e.target.value)
													}
													placeholder="Description"
													className="flex-1 h-8 text-xs"
												/>
												<Button
													type="button"
													variant="ghost"
													size="sm"
													className="h-7 w-7 p-0 shrink-0"
													onClick={() => removeEnvVar(i)}
												>
													<X className="h-3 w-3" />
												</Button>
											</div>
										))}
									</div>

									<div className="space-y-1.5">
										<Label htmlFor="mcp-setup">Setup Instructions</Label>
										<Textarea
											id="mcp-setup"
											value={setupInstructions}
											onChange={(e) => setSetupInstructions(e.target.value)}
											placeholder="Steps to configure this MCP server..."
											rows={3}
											className="text-sm"
										/>
									</div>
								</>
							)}
						</>
					)}

					{/* ── Skill-specific ────────────────────────────── */}
					{type === "skills" && (
						<>
							<div className="space-y-1.5">
								<Label>Task Type</Label>
								<PickerSelect
									value={taskType}
									onValueChange={setTaskType}
									options={SKILL_TASK_TYPES.map((t) => ({ value: t, label: t }))}
								/>
							</div>

							<Tabs value={skillMode} onValueChange={(v) => setSkillMode(v as "git" | "paste" | "upload")} className="w-full">
								<TabsList className="grid w-full grid-cols-3">
									<TabsTrigger value="git" disabled={isFolderVersion}>Git</TabsTrigger>
									<TabsTrigger value="paste" disabled={isFolderVersion}>Paste</TabsTrigger>
									<TabsTrigger value="upload">Upload</TabsTrigger>
								</TabsList>

								<TabsContent value="git" className="space-y-3 pt-3">
									<div className="grid grid-cols-2 gap-3">
										<div className="space-y-1.5">
											<Label htmlFor="skill-git-url">Git URL *</Label>
											<Input
												id="skill-git-url"
												value={skillGitUrl}
												onChange={(e) => handleSkillGitUrl(e.target.value)}
												placeholder="https://github.com/org/skills"
											/>
											{skillDiscovering && (
												<p className="text-xs text-muted-foreground flex items-center gap-1">
													<Loader2 className="h-3 w-3 animate-spin" />
													Looking for SKILL.md…
												</p>
											)}
											{skillPathHint && !skillDiscovering && (
												<p
													className={`text-xs ${skillPathAuto ? "text-green-600" : "text-amber-600"}`}
												>
													{skillPathHint}
												</p>
											)}
										</div>
										{!skillPathAuto && (
											<div className="space-y-1.5">
												<Label htmlFor="skill-path">Skill Path</Label>
												<Input
													id="skill-path"
													value={skillPath}
													onChange={(e) => {
														setSkillPath(e.target.value);
														setSkillPathAuto(false);
													}}
													placeholder="skills/my-skill"
												/>
											</div>
										)}
									</div>
									<div className="space-y-1.5">
										<Label htmlFor="skill-git-ref">Git Ref (branch / tag)</Label>
										<Input
											id="skill-git-ref"
											value={skillGitRef}
											onChange={(e) => setSkillGitRef(e.target.value)}
											placeholder="main"
										/>
									</div>
								</TabsContent>

								<TabsContent value="paste" className="space-y-3 pt-3">
									<div className="space-y-1.5">
										<Label htmlFor="skill-md-content">SKILL.md *</Label>
										<Textarea
											id="skill-md-content"
											value={skillMdContent}
											onChange={(e) => {
												const raw = e.target.value;
												setSkillMdContent(raw);
												// Auto-fill name/description from frontmatter
												const fmMatch = raw.match(/^---\r?\n([\s\S]*?)\r?\n---/);
												if (fmMatch) {
													const lines = fmMatch[1].split(/\r?\n/);
													for (const line of lines) {
														const nm = line.match(/^name:\s*(.+)$/);
														if (nm && !name) setName(nm[1].trim());
														const dm = line.match(
															/^description:\s*["']?(.+?)["']?$/,
														);
														if (dm && !description) setDescription(dm[1].trim());
													}
												}
											}}
											placeholder={`---\nname: my-skill\ndescription: What this skill does\ncommand: /my-skill\n---\n\n## Instructions\n\nYour skill instructions here...`}
											rows={10}
											className="font-mono text-xs"
										/>
										<p className="text-xs text-muted-foreground">
											Frontmatter auto-fills name and description above.
										</p>
									</div>
									<div className="space-y-1.5">
										<Label htmlFor="skill-script-filename">Script Filename (optional)</Label>
										<Input
											id="skill-script-filename"
											value={skillScriptFilename}
											onChange={(e) => setSkillScriptFilename(e.target.value)}
											placeholder="run.sh"
											className="font-mono"
										/>
										<p className="text-xs text-muted-foreground">
											Name of the script file that will be written on install.
										</p>
									</div>
									<div className="space-y-1.5">
										<Label htmlFor="skill-script-content">
											Script (optional, {skillScriptLanguageName})
										</Label>
										<CodeEditor
											id="skill-script-content"
											value={skillScriptContent}
											onChange={setSkillScriptContent}
											language={skillScriptLanguage}
											placeholder="Paste or type the skill script here."
										/>
										<p className="text-xs text-muted-foreground">
											Detected from filename. Use .sh for Bash, .py for Python, or .mjs/.js for JavaScript.
										</p>
									</div>
								</TabsContent>

								<TabsContent value="upload" className="space-y-3 pt-3">
									{!folderConfigLoading && !skillFolderDeliveryEnabled && (
										<p role="status" className="rounded-md border border-border bg-muted/40 p-3 text-sm text-foreground">
											{folderConfigError
												? "Could not verify whether complete-folder review is available. You can save a draft, but confirm deployment readiness with your administrator before planning a review."
												: "Complete-folder review and installation are not enabled on this deployment. You can save and edit a draft, but an administrator must finish the rollout before it can be submitted or installed."}
										</p>
									)}
									<p className="text-xs text-muted-foreground">Save a complete folder draft; it will not be submitted for review yet. Choose Folder preserves nested paths (your browser will ask for permission). Add Files places files at the root. Browser uploads cannot read executable permissions: select each runnable file and enable its executable switch before saving.</p>
									{/* Hidden file inputs */}
									<input
										type="file"
										id="skill-file-upload"
										className="hidden"
										multiple
										onChange={(e) => {
											void handleSkillFiles(e.currentTarget.files, false);
											e.currentTarget.value = "";
										}}
									/>
									<input
										type="file"
										id="skill-folder-upload"
										className="hidden"
										{...({ webkitdirectory: "" } as React.InputHTMLAttributes<HTMLInputElement>)}
										onChange={(e) => {
											void handleSkillFiles(e.currentTarget.files, true);
											e.currentTarget.value = "";

										}}
									/>

									{/* Upload buttons */}
									<div className="flex gap-2">
										<Button
											type="button"
											variant="outline"
											size="sm"
											onClick={() => document.getElementById("skill-file-upload")?.click()}
										>
											<Upload className="h-4 w-4 mr-1" />
											Add Files
										</Button>
										<Button
											type="button"
											variant="outline"
											size="sm"
											onClick={() => document.getElementById("skill-folder-upload")?.click()}
										>
											<FolderUp className="h-4 w-4 mr-1" />
											{skillMdContent || skillExtraFiles.length > 0 ? "Replace Whole Folder" : "Choose Folder"}
										</Button>
										{(skillMdContent || skillExtraFiles.length > 0) && (
											<Button
												type="button"
												variant="ghost"
												size="sm"
												onClick={() => {
													setSkillMdContent("");
													setSkillExtraFiles([]);
													setSelectedFilePath(null);
												}}
											>
												<Trash2 className="h-4 w-4 mr-1" />
												Clear All
											</Button>
										)}
									</div>

									{/* File tree */}
									{(skillMdContent || skillExtraFiles.length > 0) && (
										<div className="border rounded-md divide-y max-h-72 overflow-auto" aria-label="Skill folder files">
											{skillMdContent && (
												<button type="button"
												className={`flex w-full items-center justify-between px-3 py-1.5 text-sm text-left hover:bg-muted/50 ${selectedFilePath === "SKILL.md" ? "bg-primary/10" : ""}`}
												aria-label="Edit SKILL.md"
												onClick={() => setSelectedFilePath("SKILL.md")}
												>
													<span className="flex items-center gap-2">
														<File className="h-4 w-4 text-muted-foreground" />
														<span className="font-mono text-xs">SKILL.md</span>
													</span>
													<span className="text-xs text-muted-foreground">{(new Blob([skillMdContent]).size / 1024).toFixed(1)} KB</span>
												</button>
											)}
											{skillExtraFiles.map((file, idx) => (
												<div key={file.path} className={`flex items-center justify-between text-sm ${selectedFilePath === file.path ? "bg-primary/10" : ""}`}>
													<button type="button" className="flex min-w-0 flex-1 items-center justify-between gap-2 px-3 py-1.5 text-left hover:bg-muted/50"
														aria-label={`Edit ${file.path}`} onClick={() => setSelectedFilePath(file.path)}>
													<span className="flex items-center gap-2">
														<File className="h-4 w-4 text-muted-foreground" />
														<span className="font-mono text-xs truncate">{file.path}</span>
														{file.executable && <span className="text-[10px] text-success font-medium">exec</span>}
													</span>
														<span className="text-xs text-muted-foreground">{((file.encoding === "base64" ? atob(file.content).length : new Blob([file.content]).size) / 1024).toFixed(1)} KB</span>
													</button>
													<div className="flex items-center gap-2 px-2">
														<Button
															type="button"
															variant="ghost"
															size="icon"
															className="h-5 w-5"
															aria-label={`Remove ${file.path}`}
															onClick={(e) => {
																e.stopPropagation();
																setSkillExtraFiles(skillExtraFiles.filter((_, i) => i !== idx));
																if (selectedFilePath === file.path) setSelectedFilePath(null);
															}}
														>
															<X className="h-3 w-3" />
														</Button>
													</div>
												</div>
											))}
										</div>
									)}

									{/* File editor */}
									{selectedFilePath && (
										<div className="space-y-1.5">
											<div className="flex items-center justify-between">
												<Label className="font-mono text-xs">{selectedFilePath}</Label>
												{selectedFilePath !== "SKILL.md" && (
													<label className="flex items-center gap-1 text-xs cursor-pointer">
														<input
															type="checkbox"
															checked={skillExtraFiles.find((f) => f.path === selectedFilePath)?.executable || false}
															onChange={(e) => setSkillExtraFiles(skillExtraFiles.map((f) => f.path === selectedFilePath ? { ...f, executable: e.target.checked } : f))}
														/>
														Executable
													</label>
												)}
											</div>
											{skillExtraFiles.find((f) => f.path === selectedFilePath)?.encoding === "base64" && (
												<p className="text-xs text-muted-foreground">Binary file preserved exactly. Replace it with another upload to edit.</p>
											)}
											<Textarea
												disabled={skillExtraFiles.find((f) => f.path === selectedFilePath)?.encoding === "base64"}
												value={
													selectedFilePath === "SKILL.md"
														? skillMdContent
														: skillExtraFiles.find((f) => f.path === selectedFilePath)?.encoding === "base64"
															? ""
															: skillExtraFiles.find((f) => f.path === selectedFilePath)?.content || ""
												}
												onChange={(e) => {
													if (selectedFilePath === "SKILL.md") {
														setSkillMdContent(e.target.value);
													} else {
														setSkillExtraFiles(skillExtraFiles.map((f) => f.path === selectedFilePath ? { ...f, content: e.target.value } : f));
													}
												}}
												rows={8}
												className="font-mono text-xs"
												placeholder="File content..."
											/>
										</div>
									)}

									{!skillMdContent && skillExtraFiles.length === 0 && (
										<div className="border-2 border-dashed rounded-md p-6 text-center text-muted-foreground">
											<FolderUp className="h-8 w-8 mx-auto mb-2 opacity-50" />
											<p className="text-sm">Upload files or a folder containing SKILL.md</p>
											<p className="text-xs mt-1">Supports scripts, templates, and assets</p>
										</div>
									)}
								</TabsContent>
							</Tabs>
						</>
					)}

					{/* ── Hook-specific ─────────────────────────────── */}
					{type === "hooks" && (
						<>
							<div className="grid grid-cols-2 gap-3">
								<div className="space-y-1.5">
									<Label>Event</Label>
									<PickerSelect
										value={event}
										onValueChange={setEvent}
										options={HOOK_EVENTS.map((e) => ({ value: e, label: e }))}
									/>
								</div>
								<div className="space-y-1.5">
									<Label>Handler Type</Label>
									<PickerSelect
										value={handlerType}
										onValueChange={setHandlerType}
										options={HOOK_HANDLER_TYPES.map((h) => ({ value: h, label: h }))}
									/>
								</div>
							</div>
							<div className="grid grid-cols-2 gap-3">
								<div className="space-y-1.5">
									<Label>Execution Mode</Label>
									<PickerSelect
										value={executionMode}
										onValueChange={setExecutionMode}
										options={HOOK_EXECUTION_MODES.map((m) => ({ value: m, label: m }))}
									/>
								</div>
								<div className="space-y-1.5">
									<Label>Scope</Label>
									<PickerSelect
										value={hookScope}
										onValueChange={setHookScope}
										options={HOOK_SCOPES.map((s) => ({ value: s, label: s }))}
									/>
								</div>
							</div>
							<div className="space-y-1.5">
								<Label htmlFor="hook-config">Handler Config (JSON)</Label>
								<Textarea
									id="hook-config"
									value={handlerConfig}
									onChange={(e) => setHandlerConfig(e.target.value)}
									placeholder='{"command": "./my-hook.sh"}'
									rows={3}
									className="font-mono text-sm"
								/>
							</div>
							<div className="space-y-1.5">
								<Label htmlFor="hook-script-filename">
									Script Filename (optional)
								</Label>
								<input
									id="hook-script-filename"
									type="text"
									value={scriptFilename}
									onChange={(e) => setScriptFilename(e.target.value)}
									placeholder="my-hook.sh"
									className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring font-mono"
								/>
								<p className="text-xs text-muted-foreground">
									Name of the script file that will be written on install.
								</p>
							</div>
							<div className="space-y-1.5">
								<Label htmlFor="hook-script">Hook Script (optional)</Label>
								<Textarea
									id="hook-script"
									value={scriptContent}
									onChange={(e) => setScriptContent(e.target.value)}
									placeholder={
										"#!/bin/bash\nINPUT=$(cat)\n# Your hook logic here\nexit 0"
									}
									rows={8}
									className="font-mono text-sm"
								/>
								<p className="text-xs text-muted-foreground">
									Script content stored in the registry and delivered on
									install. Leave empty for inline commands.
								</p>
							</div>
						</>
					)}

					{/* ── Prompt-specific ───────────────────────────── */}
					{type === "prompts" && (
						<>
							<div className="space-y-1.5">
								<Label>Category</Label>
								<PickerSelect
									value={promptCategory}
									onValueChange={setPromptCategory}
									options={PROMPT_CATEGORIES.map((c) => ({ value: c, label: c }))}
								/>
							</div>
							<div className="space-y-1.5">
								<Label htmlFor="prompt-template">Template *</Label>
								<Textarea
									id="prompt-template"
									value={template}
									onChange={(e) => setTemplate(e.target.value)}
									placeholder={
										"You are a {{role}} that helps with {{task}}.\n\nUse {{variable}} syntax for template variables."
									}
									rows={6}
									className="font-mono text-sm"
								/>
							</div>
						</>
					)}

					{/* ── Sandbox-specific ──────────────────────────── */}
					{type === "sandboxes" && (
						<>
							<div className="grid grid-cols-2 gap-3">
								<div className="space-y-1.5">
									<Label>Runtime Type</Label>
									<PickerSelect
										value={runtimeType}
										onValueChange={setRuntimeType}
										options={SANDBOX_RUNTIME_TYPES.map((r) => ({ value: r, label: r }))}
									/>
								</div>
								<div className="space-y-1.5">
									<Label>Network Policy</Label>
									<PickerSelect
										value={networkPolicy}
										onValueChange={setNetworkPolicy}
										options={SANDBOX_NETWORK_POLICIES.map((p) => ({ value: p, label: p }))}
									/>
								</div>
							</div>
							<div className="grid grid-cols-2 gap-3">
								<div className="space-y-1.5">
									<Label htmlFor="sandbox-image">Image / Artifact Ref *</Label>
									<Input
										id="sandbox-image"
										value={image}
										onChange={(e) => setImage(e.target.value)}
										placeholder="ubuntu:22.04"
									/>
								</div>
								<div className="space-y-1.5">
									<Label htmlFor="sandbox-entry">Entrypoint</Label>
									<Input
										id="sandbox-entry"
										value={entrypoint}
										onChange={(e) => setEntrypoint(e.target.value)}
										placeholder="/bin/bash"
									/>
								</div>
							</div>
							<div className="grid grid-cols-2 gap-3">
								<div className="space-y-1.5">
									<Label htmlFor="sandbox-limits">Resource Limits JSON</Label>
									<CodeEditor
										id="sandbox-limits"
										value={sandboxResourceLimits}
										onChange={setSandboxResourceLimits}
										language="json"
										minHeightClassName="min-h-28 [&_.cm-editor]:min-h-28 [&_.cm-scroller]:min-h-28"
										placeholder='{"timeout": 60, "memory_mb": 512}'
									/>
								</div>
								<div className="space-y-1.5">
									<Label htmlFor="sandbox-runtime-config">Runtime Config JSON</Label>
									<CodeEditor
										id="sandbox-runtime-config"
										value={sandboxRuntimeConfig}
										onChange={setSandboxRuntimeConfig}
										language="json"
										minHeightClassName="min-h-28 [&_.cm-editor]:min-h-28 [&_.cm-scroller]:min-h-28"
										placeholder='{"module": "runner.wasm"}'
									/>
								</div>
							</div>
							<div className="grid grid-cols-3 gap-3">
								<Input value={sandboxSourceUrl} onChange={(e) => setSandboxSourceUrl(e.target.value)} placeholder="Source URL" />
								<Input value={sandboxSourceRef} onChange={(e) => setSandboxSourceRef(e.target.value)} placeholder="Source ref" />
								<Input value={sandboxPath} onChange={(e) => setSandboxPath(e.target.value)} placeholder="Sandbox path" />
							</div>
						</>
					)}

					{/* ── Supported harnesses (all types) ────────────────── */}
					<div className="space-y-1.5">
						<Label>Supported harnesses</Label>
						<div className="flex flex-wrap gap-1.5">
							{(harnessList ?? []).filter((harness) => type !== "skills" || harness.capabilities.includes("skills") || supportedHarnesses.includes(harness.name)).map((harness) => (
								<button
									key={harness.name}
									type="button"
									onClick={() => toggleHarness(harness.name)}
									className={`rounded-md px-2 py-1 text-xs font-medium transition-colors ${
										supportedHarnesses.includes(harness.name)
											? "bg-primary text-primary-foreground"
											: "bg-muted/50 text-muted-foreground hover:bg-muted"
									}`}
								>
									{harness.display_name}
								</button>
							))}
						</div>
					</div>

					{/* ── Actions ───────────────────────────────────── */}
					{type === "skills" && skillMode === "upload" && submitError && (
						<p role="status" className="text-xs text-muted-foreground">{submitError}</p>
					)}
					<div className="flex justify-end gap-2 pt-2">
						{isEditMode ? (
							<>
								{isFolderVersion ? (
									<Button
										onClick={() => {
										const err = validateForSubmit();
										if (err) { toast.error(err); return; }
										onUpdateDraft?.(String(d?.id), buildBody());
									}}
										disabled={busy || pendingSkillUploads > 0}
									>
										{isSavingDraft && <Loader2 className="h-4 w-4 animate-spin mr-1.5" />}
										Save folder changes
									</Button>
								) : isPendingEdit ? (
									<Button
										onClick={() => {
											const err = validateForSubmit();
											if (err) {
												toast.error(err);
												return;
											}
											onUpdateDraft?.(
												(editItem as Record<string, unknown>).id as string,
												buildBody(),
											);
										}}
										disabled={busy || !!submitError}
										title={submitError ?? undefined}
									>
										{isSavingDraft && (
											<Loader2 className="h-4 w-4 animate-spin mr-1.5" />
										)}
										Save Changes
									</Button>
								) : (
									<>
										<Button
											variant="outline"
											onClick={() => {
												if (!name) {
													toast.error("Name is required");
													return;
												}
												onUpdateDraft?.(
													(editItem as Record<string, unknown>).id as string,
													buildBody(),
												);
											}}
											disabled={busy || !name}
										>
											{isSavingDraft && (
												<Loader2 className="h-4 w-4 animate-spin mr-1.5" />
											)}
											Save Changes
										</Button>
										<Button
											onClick={() => {
												const err = validateForSubmit();
												if (err) {
													toast.error(err);
													return;
												}
												onSubmit(buildBody());
											}}
											disabled={busy || !!submitError}
											title={submitError ?? undefined}
										>
											{isSubmitting && (
												<Loader2 className="h-4 w-4 animate-spin mr-1.5" />
											)}
											Save & Resubmit
										</Button>
									</>
								)}
							</>
						) : (
							<>
								{!(type === "skills" && skillMode === "upload") && <Button
									variant="outline"
									onClick={handleDraft}
									disabled={busy || !name || pendingSkillUploads > 0}
								>
									{isSavingDraft && (
										<Loader2 className="h-4 w-4 animate-spin mr-1.5" />
									)}
									Save Draft
								</Button>}
								<Button
									onClick={handleSubmit}
									disabled={busy || !!submitError || pendingSkillUploads > 0}
									title={submitError ?? undefined}
								>
									{(isSubmitting || (type === "skills" && skillMode === "upload" && isSavingDraft)) && (
										<Loader2 className="h-4 w-4 animate-spin mr-1.5" />
									)}
									{type === "skills" && skillMode === "upload" ? "Save folder draft" : "Submit for Review"}
								</Button>
							</>
						)}
					</div>
				</div>
			</DialogContent>
		</Dialog>
	);
}
