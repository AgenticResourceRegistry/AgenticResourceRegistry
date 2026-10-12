// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0


import { useState, useCallback, useMemo, useEffect } from "react";
import type { ReactElement } from "react";
import { Link } from "@tanstack/react-router";
import {
	CheckCircle2,
	X,
	ExternalLink,
	GitBranch,
	AlertCircle,
	File,
	Folder,
	ChevronRight,
	ChevronDown,
} from "lucide-react";
import {
	Sheet,
	SheetContent,
	SheetHeader,
	SheetTitle,
	SheetDescription,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ForkedFromChip } from "@/components/registry/fork-provenance";
import { ForkDiffPanel } from "@/components/registry/fork-diff-panel";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { DetailSkeleton, TableSkeleton } from "@/components/shared/skeleton-layouts";
import {
	Tooltip,
	TooltipContent,
	TooltipProvider,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import {
	ValidationBadge,
	ValidationDetails,
	ComponentReadinessBadge,
} from "./validation-badges";
import {
	useReviewDetail,
	useSkillVersionReview,
	useSkillVersionDecision,
	useRelatedSkills,
	useApproveWithSkills,
	useSkillVersionManifest,
	useSkillFileContent,
} from "@/hooks/use-api";
import yaml from "js-yaml";
import type { ReviewItem, SkillManifestFile } from "@/lib/types";

function toYaml(value: unknown): string {
	try {
		return yaml.dump(value, { lineWidth: 120, indent: 2, noRefs: true }).trimEnd();
	} catch {
		return JSON.stringify(value, null, 2);
	}
}

function reviewItemHref(item: ReviewItem): string {
	if (item.type === "agent") return `/agents/${item.id}`;
	const plural = item.type ? `${item.type}s` : "mcps";
	return `/components/${item.id}?type=${plural}`;
}

function DetailField({ label, value }: { label: string; value: unknown }) {
	if (value === null || value === undefined || value === "") return null;

	return (
		<div>
			<dt className="text-xs font-medium text-muted-foreground">{label}</dt>
			<dd className="text-sm mt-0.5">
				{typeof value === "object" ? (
					<pre className="max-h-40 overflow-auto rounded bg-muted p-2 text-xs whitespace-pre-wrap">
						{toYaml(value)}
					</pre>
				) : typeof value === "boolean" ? (
					value ? (
						"Yes"
					) : (
						"No"
					)
				) : (
					String(value)
				)}
			</dd>
		</div>
	);
}

function McpConfigSection({ detail }: { detail: ReviewItem }) {
	return (
		<dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3">
			<DetailField label="Transport" value={detail.transport} />
			<DetailField label="Framework" value={detail.framework} />
			<DetailField label="Docker Image" value={detail.docker_image} />
			<DetailField label="Command" value={detail.command} />
			<DetailField label="Args" value={detail.args} />
			<DetailField label="URL" value={detail.url} />
			<DetailField label="Auto Approve" value={detail.auto_approve} />
			<DetailField
				label="Setup Instructions"
				value={detail.setup_instructions}
			/>
			<DetailField label="Changelog" value={detail.changelog} />
			<DetailField
				label="Environment Variables"
				value={detail.environment_variables}
			/>
			<DetailField label="Headers" value={detail.headers} />
			<DetailField label="Tools Schema" value={detail.tools_schema} />
		</dl>
	);
}

function useVerifiedReviewBytes(blob: Blob | null, file: SkillManifestFile | undefined, onPreviewError?: () => void) {
	const [result, setResult] = useState<{ source: Blob; sha256: string; verified: Blob | null; error: string } | null>(null);
	useEffect(() => {
		if (!blob || !file) return;
		let active = true;
		void (async () => {
			try {
				if (!globalThis.crypto?.subtle) throw new Error("Verified file review requires HTTPS or localhost.");
				const bytes = await blob.arrayBuffer();
				if (bytes.byteLength !== file.size) throw new Error("File size differs from its reviewed manifest.");
				const hash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), (byte) =>
					byte.toString(16).padStart(2, "0")).join("");
				if (hash !== file.sha256) throw new Error("File checksum differs from its reviewed manifest.");
				if (active) setResult({ source: blob, sha256: file.sha256, verified: blob, error: "" });
			} catch (error) {
				if (active) {
					setResult({ source: blob, sha256: file.sha256, verified: null,
						error: error instanceof Error ? error.message : "File verification failed." });
					onPreviewError?.();
				}
			}
		})();
		return () => { active = false; };
	}, [blob, file?.sha256, file?.size, onPreviewError]);
	return result?.source === blob && result?.sha256 === file?.sha256 ? result : null;
}

export function SkillFilesSection({ listingId, versionId, baseVersionId, baseDeliveryMode, onPreviewError, onPreviewPendingChange }: {
	listingId: string; versionId: string; baseVersionId?: string | null; baseDeliveryMode?: string | null;
	onPreviewError?: () => void; onPreviewPendingChange?: (pending: boolean) => void;
}) {
	const { data: manifest, isLoading } = useSkillVersionManifest(listingId, versionId);
	const { data: baseManifest, isLoading: isLoadingBase, isError: baseError } = useSkillVersionManifest(
		listingId, baseDeliveryMode === "git_fetch" ? undefined : baseVersionId ?? undefined,
	);
	const [selectedFile, setSelectedFile] = useState<string | null>(null);
	const [expandedDirs, setExpandedDirs] = useState<Set<string>>(new Set());
	const candidateFiles = new Map(manifest?.files.map((file) => [file.path, file]) ?? []);
	const baseFiles = new Map(baseManifest?.files.map((file) => [file.path, file]) ?? []);
	const { data: fileContent, isLoading: isLoadingContent, isError: fileContentError } = useSkillFileContent(
		listingId,
		versionId,
		selectedFile && candidateFiles.has(selectedFile) ? selectedFile : null,
	);
	const { data: baseContent, isLoading: isLoadingBaseContent, isError: baseContentError } = useSkillFileContent(
		listingId,
		baseVersionId ?? undefined,
		selectedFile && baseFiles.has(selectedFile) ? selectedFile : null,
	);
	const candidateMeta = selectedFile ? candidateFiles.get(selectedFile) : undefined;
	const baseMeta = selectedFile ? baseFiles.get(selectedFile) : undefined;
	const candidateBlob = fileContent?.encoding === "binary" ? fileContent.content : null;
	const baseBlob = baseContent?.encoding === "binary" ? baseContent.content : null;
	const candidateTextBlob = useMemo(() => fileContent?.encoding === "utf-8" && typeof fileContent.content === "string"
		? new Blob([fileContent.content]) : null, [fileContent]);
	const baseTextBlob = useMemo(() => baseContent?.encoding === "utf-8" && typeof baseContent.content === "string"
		? new Blob([baseContent.content]) : null, [baseContent]);
	const candidateBinary = useVerifiedReviewBytes(candidateBlob, candidateMeta, onPreviewError);
	const baseBinary = useVerifiedReviewBytes(baseBlob, baseMeta, onPreviewError);
	const candidateText = useVerifiedReviewBytes(candidateTextBlob, candidateMeta, onPreviewError);
	const baseText = useVerifiedReviewBytes(baseTextBlob, baseMeta, onPreviewError);
	const binaryUrl = useMemo(() => candidateBinary?.verified ? URL.createObjectURL(candidateBinary.verified) : null, [candidateBinary]);
	useEffect(() => () => { if (binaryUrl) URL.revokeObjectURL(binaryUrl); }, [binaryUrl]);
	const baseUrl = useMemo(() => baseBinary?.verified ? URL.createObjectURL(baseBinary.verified) : null, [baseBinary]);
	useEffect(() => () => { if (baseUrl) URL.revokeObjectURL(baseUrl); }, [baseUrl]);
	const candidateMismatch = !!fileContent && !!candidateMeta && (fileContent.encoding !== "binary" && (
		fileContent.encoding !== "utf-8" || typeof fileContent.content !== "string" || fileContent.version_id !== versionId ||
		fileContent.revision !== manifest?.revision || fileContent.file?.path !== selectedFile ||
		fileContent.file?.sha256 !== candidateMeta.sha256
	));
	const baseMismatch = !!baseContent && !!baseMeta && (baseContent.encoding !== "binary" && (
		baseContent.encoding !== "utf-8" || typeof baseContent.content !== "string" || baseContent.version_id !== baseVersionId ||
		baseContent.revision !== baseManifest?.revision || baseContent.file?.path !== selectedFile ||
		baseContent.file?.sha256 !== baseMeta.sha256
	));
	useEffect(() => {
		if (selectedFile && (fileContentError || baseContentError || candidateMismatch || baseMismatch ||
			(!!candidateMeta && !isLoadingContent && !fileContent) ||
			(!!baseMeta && !isLoadingBaseContent && !baseContent))) onPreviewError?.();
	}, [selectedFile, candidateMeta, baseMeta, isLoadingContent, isLoadingBaseContent,
		fileContent, baseContent, fileContentError, baseContentError, candidateMismatch, baseMismatch, onPreviewError]);
	useEffect(() => {
		const candidatePending = !!candidateMeta && !(fileContent?.encoding === "binary" ? candidateBinary?.verified : candidateText?.verified);
		const basePending = !!baseMeta && !(baseContent?.encoding === "binary" ? baseBinary?.verified : baseText?.verified);
		onPreviewPendingChange?.(!!selectedFile && (candidatePending || basePending));
	}, [selectedFile, candidateMeta, baseMeta, fileContent, baseContent, candidateBinary, baseBinary,
		candidateText, baseText, onPreviewPendingChange]);
	if (isLoading) {
		return <div className="text-sm text-muted-foreground">Loading files...</div>;
	}

	if (!manifest?.files?.length) {
		return null;
	}

	// Compare manifest hashes and modes, not file names alone. Deleted paths are shown below the candidate tree.
	const added = manifest.files.filter((file) => !baseFiles.has(file.path));
	const modified = manifest.files.filter((file) => {
		const old = baseFiles.get(file.path);
		return old && (old.sha256 !== file.sha256 || old.mode !== file.mode);
	});
	const removed = (baseManifest?.files ?? []).filter((file) => !candidateFiles.has(file.path));

	// Build tree structure from flat file paths
	type TreeNode = { name: string; path: string; isDir: boolean; size?: number; mode?: string; children: TreeNode[] };
	const root: TreeNode = { name: "", path: "", isDir: true, children: [] };

	for (const file of manifest.files) {
		const parts = file.path.split("/");
		let current = root;
		let pathSoFar = "";
		for (let i = 0; i < parts.length; i++) {
			const part = parts[i];
			pathSoFar = pathSoFar ? `${pathSoFar}/${part}` : part;
			const isLast = i === parts.length - 1;
			let child = current.children.find((c) => c.name === part);
			if (!child) {
				child = {
					name: part,
					path: pathSoFar,
					isDir: !isLast,
					size: isLast ? file.size : undefined,
					mode: isLast ? file.mode : undefined,
					children: [],
				};
				current.children.push(child);
			}
			current = child;
		}
	}

	const toggleDir = (path: string) => {
		setExpandedDirs((prev) => {
			const next = new Set(prev);
			if (next.has(path)) next.delete(path);
			else next.add(path);
			return next;
		});
	};

	const renderNode = (node: TreeNode, depth: number): ReactElement | null => {
		if (!node.name) {
			return <>{node.children.map((c) => renderNode(c, 0))}</>;
		}
		const isExpanded = expandedDirs.has(node.path);
		const isSelected = selectedFile === node.path;
		const change = !node.isDir && baseManifest
			? added.some((file) => file.path === node.path) ? "Added"
				: modified.some((file) => file.path === node.path) ? "Modified" : null
			: null;

		return (
			<div key={node.path}>
				<button
					type="button"
					className={`flex w-full items-center gap-1 py-0.5 px-1 rounded text-left text-sm cursor-pointer hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${isSelected ? "bg-primary/10" : ""}`}
					style={{ paddingLeft: `${depth * 12 + 4}px` }}
					onClick={() => node.isDir ? toggleDir(node.path) : setSelectedFile(node.path)}
					aria-expanded={node.isDir ? isExpanded : undefined}
					aria-pressed={node.isDir ? undefined : isSelected}
				>
					{node.isDir ? (
						isExpanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />
					) : (
						<File className="h-3 w-3 text-muted-foreground" />
					)}
					{node.isDir && <Folder className="h-3 w-3 text-warning" />}
					<span className="truncate">{node.name}</span>
					{node.mode === "0755" && <span className="text-[10px] text-muted-foreground ml-1">exec</span>}
					{change && <span className="ml-1 text-[10px] font-medium text-primary">{change}</span>}
					{node.size !== undefined && <span className="text-[10px] text-muted-foreground ml-auto">{formatBytes(node.size)}</span>}
				</button>
				{node.isDir && isExpanded && node.children.map((c) => renderNode(c, depth + 1))}
			</div>
		);
	};

	const formatBytes = (bytes: number) => {
		if (bytes < 1024) return `${bytes} B`;
		if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
		return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
	};

	return (
		<div className="space-y-2">
			<div className="text-xs font-medium text-muted-foreground">Files ({manifest.files.length})</div>
			{baseDeliveryMode === "git_fetch" && <p className="text-xs text-warning">The reviewed Git source has no stored file tree. Inspect the complete candidate below; no file-by-file comparison is possible.</p>}
			{baseVersionId && baseDeliveryMode !== "git_fetch" && isLoadingBase && <p className="text-xs text-muted-foreground">Loading base version comparison…</p>}
			{baseVersionId && baseDeliveryMode !== "git_fetch" && baseError && <p role="alert" className="text-xs text-destructive">Base version unavailable; file changes cannot be verified here.</p>}
			{baseManifest && <p className="text-xs text-muted-foreground">
				Compared to base: {added.length} added, {modified.length} modified, {removed.length} removed.
			</p>}
			<div className="border rounded-md p-2 max-h-48 overflow-auto">
				{renderNode(root, 0)}
				{removed.map((file) => (
					<button key={file.path} type="button" onClick={() => setSelectedFile(file.path)}
						aria-pressed={selectedFile === file.path}
						className="flex w-full items-center gap-2 rounded px-1 text-left text-sm text-destructive hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
						<File className="h-3 w-3" />{file.path} <span className="text-[10px]">Removed</span>
					</button>
				))}
			</div>
			{selectedFile && (
				<div className="space-y-1">
					<div className="flex items-center justify-between">
						<span className="text-xs font-mono text-muted-foreground">{selectedFile}</span>
						<Button variant="ghost" size="sm" className="h-5 px-1" onClick={() => setSelectedFile(null)}>
							<X className="h-3 w-3" />
						</Button>
					</div>
					{candidateFiles.has(selectedFile) && (
						<div>
							<p className="text-xs font-medium">Candidate</p>
							{isLoadingContent ? <p className="text-xs">Loading...</p> : candidateBinary?.error || candidateText?.error ? (
								<p role="alert" className="text-xs text-destructive">{candidateBinary?.error || candidateText?.error}</p>
							) : candidateBlob && !binaryUrl ? <p className="text-xs">Verifying binary…</p> : candidateMismatch ? (
								<p role="alert" className="text-xs text-destructive">Candidate file changed; refresh before review.</p>
							) : binaryUrl ? (
								<a href={binaryUrl} download={selectedFile.split("/").at(-1)} className="text-sm underline">Download candidate binary ({candidateBlob?.size} bytes)</a>
							) : candidateTextBlob && !candidateText?.verified ? <p className="text-xs">Verifying text…</p> : candidateText?.verified ? (
								<pre className="max-h-60 overflow-auto rounded bg-muted p-2 text-xs font-mono whitespace-pre-wrap">{fileContent?.encoding === "utf-8" ? fileContent.content : null}</pre>
							) : <p role="alert" className="text-xs text-destructive">File preview unavailable</p>}
						</div>
					)}
					{baseFiles.has(selectedFile) && (
						<div>
							<p className="text-xs font-medium">Reviewed base</p>
							{isLoadingBaseContent ? <p className="text-xs">Loading...</p> : baseBinary?.error || baseText?.error ? (
								<p role="alert" className="text-xs text-destructive">{baseBinary?.error || baseText?.error}</p>
							) : baseBlob && !baseUrl ? <p className="text-xs">Verifying binary…</p> : baseMismatch ? (
								<p role="alert" className="text-xs text-destructive">Reviewed base file changed; refresh before review.</p>
							) : baseUrl ? (
								<a href={baseUrl} download={selectedFile.split("/").at(-1)} className="text-sm underline">Download base binary ({baseBlob?.size} bytes)</a>
							) : baseTextBlob && !baseText?.verified ? <p className="text-xs">Verifying text…</p> : baseText?.verified ? (
								<pre className="max-h-60 overflow-auto rounded bg-muted p-2 text-xs font-mono whitespace-pre-wrap">{baseContent?.encoding === "utf-8" ? baseContent.content : null}</pre>
							) : <p role="alert" className="text-xs text-destructive">Base preview unavailable</p>}
						</div>
					)}
				</div>
			)}
		</div>
	);
}

function SkillConfigSection({ detail, onPreviewError }: { detail: ReviewItem; onPreviewError?: () => void }) {
	// Check if this skill has a pending version with files
	const hasVersionFiles = detail.version_id && detail.files && detail.files.length > 0;

	return (
		<div className="space-y-4">
			<dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3">
				<DetailField label="Task Type" value={detail.task_type} />
				<DetailField label="Slash Command" value={detail.slash_command} />
				<DetailField label="Skill Path" value={detail.skill_path} />
				<DetailField label="Git URL" value={detail.git_url} />
				<DetailField label="Git Ref" value={detail.git_ref} />
				<DetailField label="Validated" value={detail.validated} />
				<DetailField label="Target Agents" value={detail.target_agents} />
			</dl>
			{hasVersionFiles && (
				<SkillFilesSection listingId={detail.id} versionId={detail.version_id!}
					baseVersionId={detail.base_version_id} baseDeliveryMode={detail.base_delivery_mode}
					onPreviewError={onPreviewError} />
			)}
		</div>
	);
}

function HookConfigSection({ detail }: { detail: ReviewItem }) {
	return (
		<dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3">
			<DetailField label="Event" value={detail.event} />
			<DetailField label="Execution Mode" value={detail.execution_mode} />
			<DetailField label="Priority" value={detail.priority} />
			<DetailField label="Handler Type" value={detail.handler_type} />
			<DetailField label="Scope" value={detail.scope} />
			<DetailField label="Tool Filter" value={detail.tool_filter} />
			<DetailField label="Handler Config" value={detail.handler_config} />
			{detail.script_filename && <DetailField label="Script" value={detail.script_filename} />}
			{detail.source_url && <DetailField label="Source" value={`${detail.source_url}@${detail.source_ref || 'main'}`} />}
			{detail.requirements && detail.requirements.length > 0 && <DetailField label="Requirements" value={detail.requirements.join(', ')} />}
		</dl>
	);
}

function PromptConfigSection({ detail }: { detail: ReviewItem }) {
	return (
		<dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3">
			<DetailField label="Category" value={detail.category} />
			<DetailField label="Tags" value={detail.tags} />
			<DetailField label="Variables" value={detail.variables} />
			<DetailField label="Model Hints" value={detail.model_hints} />
			{detail.template && (
				<div className="col-span-full">
					<dt className="text-xs font-medium text-muted-foreground">
						Template
					</dt>
					<dd className="mt-0.5">
						<pre className="max-h-60 overflow-auto rounded bg-muted p-2 text-2xs font-mono leading-relaxed break-words">
							{detail.template}
						</pre>
					</dd>
				</div>
			)}
		</dl>
	);
}

function SandboxConfigSection({ detail }: { detail: ReviewItem }) {
	return (
		<dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3">
			<DetailField label="Runtime Type" value={detail.runtime_type} />
			<DetailField label="Image" value={detail.image} />
			<DetailField label="Network Policy" value={detail.network_policy} />
			<DetailField label="Entrypoint" value={detail.entrypoint} />
			<DetailField label="Resource Limits" value={detail.resource_limits} />
			{detail.sandbox_path && <DetailField label="Sandbox Path" value={detail.sandbox_path} />}
			{detail.source_url && <DetailField label="Source" value={detail.source_url} />}
		</dl>
	);
}

function AgentConfigSection({ detail }: { detail: ReviewItem }) {
	return (
		<dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3">
			<DetailField label="Model" value={detail.model_name} />
			<DetailField label="External MCPs" value={detail.external_mcps} />
			<DetailField
				label="Required harness Features"
				value={detail.required_capabilities}
			/>
			<DetailField label="Model Config" value={detail.model_config_json} />
			{detail.prompt && (
				<div className="col-span-full">
					<dt className="text-xs font-medium text-muted-foreground">Prompt</dt>
					<dd className="mt-0.5">
						<pre className="max-h-60 overflow-auto rounded bg-muted p-2 text-2xs font-mono leading-relaxed break-words whitespace-pre-wrap">
							{detail.prompt}
						</pre>
					</dd>
				</div>
			)}
			{detail.success_criteria && detail.success_criteria.intended_purpose && (
				<div className="col-span-full">
					<dt className="text-xs font-medium text-muted-foreground">Success Criteria</dt>
					<dd className="mt-0.5 space-y-2 text-sm">
						<div>
							<span className="text-2xs font-medium text-muted-foreground uppercase">Purpose</span>
							<p className="text-xs whitespace-pre-wrap">{detail.success_criteria.intended_purpose}</p>
						</div>
						{(detail.success_criteria.success_metrics?.length ?? 0) > 0 && (
							<div>
								<span className="text-2xs font-medium text-muted-foreground uppercase">Metrics</span>
								<div className="mt-1 space-y-1">
									{detail.success_criteria.success_metrics.map((m, i) => (
										<div key={i} className="flex flex-wrap gap-x-2 text-xs rounded bg-muted/50 px-2 py-1">
											<span className="font-medium">{m.name}</span>
											<span className="text-muted-foreground">target: <span className="font-mono">{m.target}</span></span>
											<span className="text-muted-foreground">via: {m.measurement}</span>
										</div>
									))}
								</div>
							</div>
						)}
						{detail.success_criteria.evaluation_notes && (
							<div>
								<span className="text-2xs font-medium text-muted-foreground uppercase">Evaluation Notes</span>
								<p className="text-xs whitespace-pre-wrap">{detail.success_criteria.evaluation_notes}</p>
							</div>
						)}
					</dd>
				</div>
			)}
			{detail.components && detail.components.length > 0 && (
				<div className="col-span-full">
					<dt className="text-xs font-medium text-muted-foreground mb-1">
						Components ({detail.components.length})
					</dt>
					<dd className="space-y-2">
						{detail.components.map((c, i) => {
							const comp = c as Record<string, unknown>;
							const name = (comp.name as string) || `${c.component_type} component`;
							const description = comp.description as string | undefined;
							const CONTENT_KEYS = ["template", "skill_md_content", "handler_config", "input_schema", "output_schema", "source_url", "git_url", "config_json"];
							const contentEntries = Object.entries(comp).filter(([k]) => CONTENT_KEYS.includes(k) && comp[k]);
							return (
								<div key={i} className="rounded border border-border overflow-hidden">
									<div className="flex items-center gap-2 px-3 py-2 bg-muted/50">
										<Badge variant="outline" className="text-2xs shrink-0">
											{c.component_type as string}
										</Badge>
										<span className="text-xs font-medium">{name}</span>
									</div>
									{description && (
										<p className="px-3 py-1.5 text-2xs text-muted-foreground border-b border-border/50">{description}</p>
									)}
									{contentEntries.length > 0 && (
										<details open className="group">
											<summary className="cursor-pointer select-none px-3 py-1.5 text-2xs font-medium text-muted-foreground hover:text-foreground list-none flex items-center gap-1">
												<span className="group-open:rotate-90 transition-transform inline-block">▶</span>
												Content
											</summary>
											<pre className="px-3 py-2 text-2xs font-mono leading-relaxed overflow-auto max-h-80 bg-background border-t border-border/50 break-words">
												{toYaml(Object.fromEntries(contentEntries))}
											</pre>
										</details>
									)}
								</div>
							);
						})}
					</dd>
				</div>
			)}
		</dl>
	);
}

function ConfigSection({ detail, onSkillPreviewError }: { detail: ReviewItem; onSkillPreviewError?: () => void }) {
	switch (detail.type) {
		case "mcp":
			return <McpConfigSection detail={detail} />;
		case "skill":
			return <SkillConfigSection detail={detail} onPreviewError={onSkillPreviewError} />;
		case "hook":
			return <HookConfigSection detail={detail} />;
		case "prompt":
			return <PromptConfigSection detail={detail} />;
		case "sandbox":
			return <SandboxConfigSection detail={detail} />;
		case "agent":
			return <AgentConfigSection detail={detail} />;
		default:
			return null;
	}
}

function RelatedSkillsSection({
	mcpId,
	onApproveWithSkills,
}: {
	mcpId: string;
	onApproveWithSkills: (mcpId: string, skillIds: string[]) => void;
}) {
	const { data: skills, isLoading } = useRelatedSkills(mcpId);
	const approveWithSkills = useApproveWithSkills();
	const [deselected, setDeselected] = useState<Set<string>>(new Set());

	const allIds = useMemo(
		() => new Set(skills?.map((s) => s.id) ?? []),
		[skills],
	);
	const selected = useMemo(
		() => new Set([...allIds].filter((id) => !deselected.has(id))),
		[allIds, deselected],
	);

	if (isLoading) {
		return <TableSkeleton rows={1} cols={2} />;
	}

	if (!skills?.length) return null;

	const toggleSkill = (id: string) => {
		setDeselected((prev) => {
			const next = new Set(prev);
			if (next.has(id)) next.delete(id);
			else next.add(id);
			return next;
		});
	};

	return (
		<div className="space-y-3">
			<h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
				Related Pending Skills ({skills.length})
			</h4>
			<div className="space-y-2">
				{skills.map((skill) => (
					<label
						key={skill.id}
						className="flex items-center gap-3 px-3 py-2 rounded border border-border hover:bg-muted/30 transition-colors cursor-pointer"
					>
						<Checkbox
							checked={selected.has(skill.id)}
							onCheckedChange={() => toggleSkill(skill.id)}
						/>
						<div className="min-w-0 flex-1">
							<p className="text-sm font-medium truncate">{skill.name}</p>
							<div className="flex items-center gap-2 text-xs text-muted-foreground">
								{skill.version && <span>v{skill.version}</span>}
								{skill.task_type && <span>{skill.task_type}</span>}
							</div>
						</div>
					</label>
				))}
			</div>
			{selected.size > 0 && (
				<Button
					size="sm"
					className="w-full h-8 text-xs bg-success/10 hover:bg-success/20 text-success border border-success/25 shadow-none"
					disabled={approveWithSkills.isPending}
					onClick={() => onApproveWithSkills(mcpId, Array.from(selected))}
				>
					<CheckCircle2 className="h-3.5 w-3.5 mr-1.5" />
					Approve MCP + {selected.size} skill{selected.size !== 1 ? "s" : ""}
				</Button>
			)}
		</div>
	);
}

interface ReviewDetailSheetProps {
	item: ReviewItem | null;
	open: boolean;
	onOpenChange: (open: boolean) => void;
	onApprove: (id: string, type?: string, category?: string) => void;
	onReject: (id: string, reason: string, type?: string) => void;
}

export function ReviewDetailSheet({
	item,
	open,
	onOpenChange,
	onApprove,
	onReject,
}: ReviewDetailSheetProps) {
	return (
		<Sheet open={open} onOpenChange={onOpenChange}>
			<SheetContent side="right" className="sm:max-w-2xl overflow-y-auto">
				{item ? (
					<SheetBody
						key={item.review_key ?? item.id}
						item={item}
						open={open}
						onOpenChange={onOpenChange}
						onApprove={onApprove}
						onReject={onReject}
					/>
				) : (
					<div className="pt-6">
						<DetailSkeleton />
					</div>
				)}
			</SheetContent>
		</Sheet>
	);
}

function SheetBody({
	item,
	open,
	onOpenChange,
	onApprove,
	onReject,
}: {
	item: ReviewItem;
	open: boolean;
	onOpenChange: (open: boolean) => void;
	onApprove: (id: string, type?: string, category?: string) => void;
	onReject: (id: string, reason: string, type?: string) => void;
}) {
	const versionId = item.type === "skill" ? item.version_id : undefined;
	const { data: detail, isLoading } = useReviewDetail(
		open && !versionId ? item.id : undefined,
	);
	const { data: selectedReview, isLoading: isLoadingVersion } = useSkillVersionReview(
		open && versionId ? item.id : undefined, versionId,
	);
	const skillDecision = useSkillVersionDecision();
	const approveWithSkills = useApproveWithSkills();
	const [showRejectInput, setShowRejectInput] = useState(false);
	const [rejectReason, setRejectReason] = useState("");
	const [gitBaseAcknowledged, setGitBaseAcknowledged] = useState(false);
	const [previewFailed, setPreviewFailed] = useState(false);
	const reportPreviewError = useCallback(() => setPreviewFailed(true), []);
	useEffect(() => { setGitBaseAcknowledged(false); setPreviewFailed(false); }, [versionId]);
	const { data: reviewedManifest, isPending: manifestPending, isError: manifestError } = useSkillVersionManifest(
		selectedReview?.files?.length ? item.id : undefined,
		selectedReview?.files?.length ? versionId : undefined,
	);
	const { data: reviewedBaseManifest, isPending: basePending, isError: baseError } = useSkillVersionManifest(
		selectedReview?.base_delivery_mode === "registry_direct" ? item.id : undefined,
		selectedReview?.base_delivery_mode === "registry_direct" ? selectedReview.base_version_id ?? undefined : undefined,
	);

	const merged = useMemo<ReviewItem>(() => {
		if (selectedReview) return { ...item, ...selectedReview };
		if (detail) return { ...item, ...detail };
		return item;
	}, [item, detail, selectedReview]);

	const handleReject = useCallback(() => {
		if (!showRejectInput) {
			setShowRejectInput(true);
			return;
		}
		if (!rejectReason.trim()) return;
		if (versionId) {
			if (!selectedReview?.revision) return;
			skillDecision.mutate({ id: item.id, versionId, revision: selectedReview.revision, action: "reject", reason: rejectReason },
				{ onSuccess: () => onOpenChange(false) });
			return;
		}
		onReject(merged.id, rejectReason, merged.type);
		setShowRejectInput(false);
		setRejectReason("");
		onOpenChange(false);
	}, [showRejectInput, rejectReason, merged, onReject, onOpenChange, versionId, selectedReview, skillDecision, item.id]);

	const handleApprove = useCallback(() => {
		if (versionId) {
			if (!selectedReview?.revision) return;
			skillDecision.mutate({ id: item.id, versionId, revision: selectedReview.revision, action: "approve", gitBaseAcknowledged },
				{ onSuccess: () => onOpenChange(false) });
			return;
		}
		onApprove(merged.id, merged.type);
		onOpenChange(false);
	}, [merged, onApprove, onOpenChange, versionId, selectedReview, skillDecision, item.id, gitBaseAcknowledged]);

	const handleApproveWithSkills = useCallback(
		(mcpId: string, skillIds: string[]) => {
			approveWithSkills.mutate(
				{ id: mcpId, skillIds },
				{ onSuccess: () => onOpenChange(false) },
			);
		},
		[approveWithSkills, onOpenChange],
	);

	const disableApprove =
		(merged.type === "agent" && merged.components_ready === false) ||
		(merged.type === "skill" && (!!versionId && (isLoadingVersion || !selectedReview?.revision ||
			previewFailed || (selectedReview.base_delivery_mode === "git_fetch" && !gitBaseAcknowledged) ||
			(!!selectedReview.files?.length && (manifestPending || manifestError || reviewedManifest?.revision !== selectedReview.revision)) ||
			(selectedReview.base_delivery_mode === "registry_direct" && (basePending || baseError || !reviewedBaseManifest)))));

	return (
		<div className="flex flex-col gap-6">
			{/* Header */}
			<SheetHeader>
				<div className="flex items-center gap-2 flex-wrap">
					{merged.type && (
						<Badge variant="outline" className="text-2xs">
							{merged.type}
						</Badge>
					)}
					{merged.version && (
						<span className="text-xs text-muted-foreground">
							v{merged.version}
						</span>
					)}
					<ValidationBadge item={merged} />
				</div>
				<SheetTitle className="text-lg font-[family-name:var(--font-display)]">
					{merged.name ?? "Unnamed"}
				</SheetTitle>
				<ForkedFromChip provenance={merged.forked_from} />
				{merged.forked_from?.available && merged.version && merged.type && (
					<ForkDiffPanel type={(merged.type === "agent" ? "agents" : `${merged.type}s`) as import("@/lib/api").RegistryType} id={merged.id} version={merged.version} />
				)}
				{merged.description && (
					<SheetDescription>{merged.description}</SheetDescription>
				)}
			</SheetHeader>

			{/* Overview */}
			<div className="space-y-3">
				<h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
					Overview
				</h4>
				<dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3">
					<DetailField label="Owner" value={merged.owner} />
					<DetailField label="Submitted By" value={merged.submitted_by} />
					<DetailField
						label="Submitted"
						value={
							merged.submitted_at || merged.created_at
								? new Date(
										(merged.submitted_at ?? merged.created_at)!,
									).toLocaleDateString()
								: undefined
						}
					/>
					<DetailField
						label="Updated"
						value={
							merged.updated_at
								? new Date(merged.updated_at).toLocaleDateString()
								: undefined
						}
					/>
					<DetailField
						label="Supported harnesses"
						value={
							merged.supported_harnesses?.length
								? merged.supported_harnesses.join(", ")
								: undefined
						}
					/>
					{merged.git_url && (
						<div>
							<dt className="text-xs font-medium text-muted-foreground">
								Git URL
							</dt>
							<dd className="text-sm mt-0.5">
								<a
									href={merged.git_url}
									target="_blank"
									rel="noopener noreferrer"
									className="inline-flex items-center gap-1 text-primary hover:underline break-all"
								>
									<GitBranch className="h-3 w-3 shrink-0" />
									{merged.git_url}
								</a>
							</dd>
						</div>
					)}
					<DetailField label="Git Ref" value={merged.git_ref} />
				</dl>
			</div>

			{/* Type-specific config */}
			{isLoading ? (
				<DetailSkeleton />
			) : (
				<div className="space-y-3">
					<h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
						Configuration
					</h4>
					<ConfigSection detail={merged} onSkillPreviewError={reportPreviewError} />
				</div>
			)}

			{/* Validation (MCP only) */}
			{merged.type === "mcp" && merged.validation_results?.length ? (
				<div className="space-y-3">
					<h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
						Validation Results
					</h4>
					<div className="space-y-2">
						{merged.validation_results.map((vr, i) => (
							<div
								key={i}
								className={`flex items-start gap-2 p-2 rounded border text-xs ${
									vr.passed
										? "border-success/25 bg-success/5"
										: "border-destructive/25 bg-destructive/5"
								}`}
							>
								<span
									className={vr.passed ? "text-success" : "text-destructive"}
								>
									{vr.passed ? "✓" : "✗"}
								</span>
								<div className="min-w-0 flex-1">
									<p className="font-medium">{vr.stage}</p>
									{vr.details && (
										<p className="text-muted-foreground whitespace-pre-wrap mt-0.5">
											{vr.details}
										</p>
									)}
									{vr.run_at && (
										<p className="text-muted-foreground mt-0.5">
											{new Date(vr.run_at).toLocaleString()}
										</p>
									)}
								</div>
							</div>
						))}
					</div>
					<ValidationDetails results={merged.validation_results} />
				</div>
			) : null}

			{/* Component Readiness (Agent only) */}
			{merged.type === "agent" && <ComponentReadinessBadge item={merged} />}

			{/* Rejection Reason */}
			{merged.rejection_reason && (
				<div className="p-3 rounded bg-destructive/5 border border-destructive/15">
					<p className="text-xs font-medium text-destructive flex items-center gap-1">
						<AlertCircle className="h-3 w-3" /> Previous Rejection
					</p>
					<p className="text-sm text-muted-foreground mt-1">
						{merged.rejection_reason}
					</p>
				</div>
			)}

			{/* Related Skills (MCP only) */}
			{merged.type === "mcp" && (
				<RelatedSkillsSection
					mcpId={merged.id}
					onApproveWithSkills={handleApproveWithSkills}
				/>
			)}

			{selectedReview?.base_delivery_mode === "git_fetch" && (
				<label className="flex items-start gap-2 rounded-md border border-warning/40 bg-warning/5 p-3 text-sm">
					<input type="checkbox" checked={gitBaseAcknowledged}
						onChange={(event) => setGitBaseAcknowledged(event.target.checked)} className="mt-1" />
					<span>I inspected this exact candidate folder. The old Git release's file contents are not stored, so they cannot be compared to it.
						{selectedReview.base_git_url && <span className="block mt-1 text-xs break-all text-muted-foreground">Old source: {selectedReview.base_git_url} {selectedReview.base_git_ref ? `(${selectedReview.base_git_ref})` : ""}</span>}
					</span>
				</label>
			)}
			{/* Actions */}
			<div className="space-y-3 border-t border-border pt-4">
				<div className="flex items-center justify-between">
					<Link
						to={reviewItemHref(merged)}
						className="text-xs text-muted-foreground hover:text-foreground inline-flex items-center gap-1"
					>
						<ExternalLink className="h-3 w-3" /> View public page
					</Link>
				</div>

				{showRejectInput && (
					<div className="flex items-center gap-2">
						<Input
							placeholder="Reason for rejection..."
							value={rejectReason}
							onChange={(e) => setRejectReason(e.target.value)}
							className="h-8 text-xs flex-1"
							onKeyDown={(e) => {
								if (e.key === "Enter") handleReject();
								if (e.key === "Escape") {
									setShowRejectInput(false);
									setRejectReason("");
								}
							}}
							autoFocus
						/>
						<Button
							variant="ghost"
							size="sm"
							className="h-8 w-8 p-0"
							aria-label="Cancel rejection"
							onClick={() => {
								setShowRejectInput(false);
								setRejectReason("");
							}}
						>
							<X className="h-3.5 w-3.5" />
						</Button>
					</div>
				)}

				<div className="flex items-center gap-2">
					{disableApprove ? (
						<TooltipProvider>
							<Tooltip>
								<TooltipTrigger asChild>
									<span className="flex-1">
										<Button
											size="sm"
											className="h-8 text-xs w-full bg-success/10 text-success border border-success/25 shadow-none opacity-50 cursor-not-allowed"
											disabled
										>
											Approve
										</Button>
									</span>
								</TooltipTrigger>
								<TooltipContent>
									<p>{selectedReview?.base_delivery_mode === "git_fetch" && !gitBaseAcknowledged
										? "Inspect the full candidate and acknowledge that Git base files cannot be compared."
										: "The exact candidate or reviewed base is unavailable; refresh before approving."}</p>
								</TooltipContent>
							</Tooltip>
						</TooltipProvider>
					) : (
						<Button
							size="sm"
							className="h-8 text-xs flex-1 bg-success/10 hover:bg-success/20 text-success border border-success/25 shadow-none"
							onClick={handleApprove}
						>
							Approve
						</Button>
					)}
					<Button
						size="sm"
						className="h-8 text-xs flex-1 bg-destructive/10 hover:bg-destructive/20 text-destructive border border-destructive/25 shadow-none"
						onClick={handleReject}
					>
						{showRejectInput ? "Confirm" : "Reject"}
					</Button>
				</div>
			</div>
		</div>
	);
}
