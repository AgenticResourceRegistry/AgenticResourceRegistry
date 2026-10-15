// SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-License-Identifier: Apache-2.0


import { useState, useCallback, useEffect, useRef } from "react";
import { CheckCircle2, XCircle, Loader2, Maximize2 } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import {
	Dialog,
	DialogContent,
	DialogHeader,
	DialogTitle,
	DialogDescription,
} from "@/components/ui/dialog";
import { useHarnesses } from "@/hooks/use-harnesses";
import { registry } from "@/lib/api";
import type { ValidationResult } from "@/lib/types";

interface PreviewPanelProps {
	name: string;
	description: string;
	modelName?: string;
	selectedComponents: Record<string, { id: string; name: string }[]>;
	prompt?: string;
	pendingComponentBodies?: Record<string, Record<string, unknown>>; // tempId -> body
	validationResult: ValidationResult | null;
}

// ── Shared helpers ────────────────────────────────────────────

function buildMarkdownBody(
	description: string,
	selectedComponents: Record<string, { id: string; name: string }[]>,
	prompt?: string,
	pendingBodies?: Record<string, Record<string, unknown>>,
): string {
	const lines: string[] = [];

	for (const [type, items] of Object.entries(selectedComponents)) {
		if (items.length === 0) continue;
		const heading =
			type === "mcps"
				? "MCP Clients"
				: type.charAt(0).toUpperCase() + type.slice(1);
		lines.push("");
		lines.push(`## ${heading}`);
		lines.push("");
		items.forEach((item) => {
			lines.push(`- **${item.name}**`);
			// Inject content for in-memory pending components
			const body = pendingBodies?.[item.id];
			if (body) {
				const content = (body.template ??
					body.skill_md_content ??
					body.handler_config) as string | undefined;
				if (content && typeof content === "string") {
					lines.push("");
					lines.push(
						content
							.split("\n")
							.map((l) => `  ${l}`)
							.join("\n"),
					);
				}
			}
		});
	}

	if (prompt?.trim()) {
		lines.push("");
		lines.push(prompt.trim());
	}

	return lines.join("\n");
}

interface PreviewFile {
	path: string;
	content: string;
}

// ── Main component ────────────────────────────────────────────

export function PreviewPanel({
	name,
	description,
	modelName,
	selectedComponents,
	prompt,
	pendingComponentBodies,
	validationResult,
}: PreviewPanelProps) {
	const { data: harnessList } = useHarnesses();
	const [harness, setHarness] = useState("claude-code");
	const [modalOpen, setModalOpen] = useState(false);
	const [modalHarness, setModalHarness] = useState("claude-code");
	const [fullConfigs, setFullConfigs] = useState<Record<
		string,
		Record<string, string>
	> | null>(null);
	const [fullLoading, setFullLoading] = useState(false);
	const [fullError, setFullError] = useState<string | null>(null);
	const modalScrollRef = useRef<HTMLDivElement>(null);

	useEffect(() => {
		if (!harnessList || harnessList.length === 0) return;
		if (!harnessList.some((opt) => opt.name === harness)) setHarness(harnessList[0].name);
		if (!harnessList.some((opt) => opt.name === modalHarness)) setModalHarness(harnessList[0].name);
	}, [harnessList, harness, modalHarness]);

	const body = buildMarkdownBody(
		description,
		selectedComponents,
		prompt,
		pendingComponentBodies,
	);

	const files: PreviewFile[] = fullConfigs?.[harness]
		? Object.entries(fullConfigs[harness]).map(([path, content]) => ({ path, content }))
		: [];

	const fetchFullConfig = useCallback(async () => {
		const components: { component_type: string; component_id: string }[] = [];
		for (const [type, items] of Object.entries(selectedComponents)) {
			const componentType =
				type === "mcps"
					? "mcp"
					: type === "skills"
						? "skill"
						: type === "hooks"
							? "hook"
							: type === "prompts"
								? "prompt"
								: null;
			if (!componentType) continue;
			for (const item of items) {
				components.push({
					component_type: componentType,
					component_id: item.id,
				});
			}
		}

		setFullLoading(true);
		setFullError(null);

		try {
			const res = await registry.previewConfig({
				name: name || "untitled",
				description,
				prompt: body,
				model_name: modelName ?? "",
				components,
			});
			setFullConfigs(res.configs);
		} catch (e) {
			setFullError(
				e instanceof Error ? e.message : "Failed to generate config",
			);
		} finally {
			setFullLoading(false);
		}
	}, [name, description, body, modelName, selectedComponents]);

	useEffect(() => {
		const timer = window.setTimeout(() => {
			void fetchFullConfig();
		}, 300);
		return () => window.clearTimeout(timer);
	}, [fetchFullConfig]);

	const handleOpenFullPreview = useCallback(() => {
		setModalHarness(harness);
		setModalOpen(true);
	}, [harness]);

	// Files for the modal view
	const modalFiles: PreviewFile[] =
		fullConfigs && fullConfigs[modalHarness]
			? Object.entries(fullConfigs[modalHarness]).map(([path, content]) => ({
					path,
					content,
				}))
			: [];

	const errorCount = validationResult
		? validationResult.issues.filter((i) => i.severity === "error").length
		: 0;

	return (
		<div className="space-y-3">
			<div className="flex items-center justify-between">
				<h3 className="text-xs font-medium uppercase tracking-widest text-muted-foreground">
					Preview
				</h3>
				<div className="flex items-center gap-2">
					<button
						type="button"
						onClick={handleOpenFullPreview}
						className="inline-flex items-center gap-1.5 rounded-md border border-primary/30 bg-primary/10 px-2.5 py-1 text-[11px] font-medium text-primary transition-colors hover:bg-primary/20"
					>
						<Maximize2 className="h-3 w-3" />
						Full Config
					</button>
					{validationResult && (
						<span className="inline-flex items-center gap-1 text-xs">
							{validationResult.valid ? (
								<>
									<CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
									<span className="text-emerald-600 dark:text-emerald-400">
										Valid
									</span>
								</>
							) : (
								<>
									<XCircle className="h-3.5 w-3.5 text-destructive" />
									<span className="text-destructive">
										{errorCount} {errorCount === 1 ? "error" : "errors"}
									</span>
								</>
							)}
						</span>
					)}
				</div>
			</div>

			{/* harness selector */}
			<div className="flex flex-wrap gap-1">
				{(harnessList ?? []).map((opt) => (
					<button
						key={opt.name}
						type="button"
						onClick={() => setHarness(opt.name)}
						className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
							harness === opt.name
								? "bg-primary text-primary-foreground"
								: "bg-muted/50 text-muted-foreground hover:bg-muted"
						}`}
					>
						{opt.display_name}
					</button>
				))}
			</div>

			{/* Server-generated file previews */}
			<Card>
				<CardContent className="p-0 divide-y">
					{fullLoading ? (
						<div className="flex items-center justify-center py-12 text-muted-foreground">
							<Loader2 className="mr-2 h-4 w-4 animate-spin" />
							<span className="text-sm">Generating preview...</span>
						</div>
					) : fullError ? (
						<div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
							<XCircle className="mb-2 h-5 w-5 text-destructive" />
							<span className="text-sm">{fullError}</span>
						</div>
					) : files.length === 0 ? (
						<div className="flex items-center justify-center py-12 text-muted-foreground">
							<span className="text-sm">No config generated for this harness.</span>
						</div>
					) : (
						files.map((file) => (
							<div key={file.path}>
								<div className="px-4 py-2 text-[11px] font-medium text-muted-foreground bg-muted/40 font-[family-name:var(--font-mono)]">
									{file.path}
								</div>
								<pre className="overflow-x-auto min-h-[100px] whitespace-pre p-4 text-sm leading-relaxed font-[family-name:var(--font-mono)] text-foreground/80">
									{file.content}
								</pre>
							</div>
						))
					)}
				</CardContent>
			</Card>

			<p className="text-[11px] text-muted-foreground">
				Telemetry hooks and environment variables are configured during
				installation via{" "}
				<code className="font-[family-name:var(--font-mono)]">
					observal pull
				</code>
				.
			</p>

			{/* Full config modal */}
			<Dialog open={modalOpen} onOpenChange={setModalOpen}>
				<DialogContent className="max-w-4xl max-h-[90vh] flex flex-col p-0">
					<DialogHeader className="px-6 pt-6 pb-0">
						<DialogTitle>Full Config Preview</DialogTitle>
						<DialogDescription>
							Exact files written by{" "}
							<code className="font-[family-name:var(--font-mono)]">
								observal pull
							</code>
							. Server URLs are placeholders.
						</DialogDescription>
					</DialogHeader>

					{/* harness tabs inside modal */}
					<div className="flex flex-wrap gap-1 px-6 pt-3">
						{(harnessList ?? []).map((opt) => (
							<button
								key={opt.name}
								type="button"
								onClick={() => {
									setModalHarness(opt.name);
									modalScrollRef.current?.scrollTo({ top: 0 });
								}}
								className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
									modalHarness === opt.name
										? "bg-primary text-primary-foreground"
										: "bg-muted/50 text-muted-foreground hover:bg-muted"
								}`}
							>
								{opt.display_name}
							</button>
						))}
					</div>

					{/* File tabs — sticky below harness tabs */}
					{!fullLoading && !fullError && modalFiles.length > 1 && (
						<div className="flex flex-wrap gap-1 px-6 pb-2 pt-1 border-b border-border">
							<span className="text-[10px] uppercase tracking-wider text-muted-foreground/60 self-center mr-1">
								Files
							</span>
							{modalFiles.map((file, i) => (
								<button
									key={file.path}
									type="button"
									onClick={() => {
										document
											.getElementById(`preview-file-${i}`)
											?.scrollIntoView({ behavior: "smooth", block: "start" });
									}}
									className="rounded px-2 py-0.5 text-[11px] font-medium font-[family-name:var(--font-mono)] text-muted-foreground border border-border/50 bg-background hover:bg-muted hover:text-foreground transition-colors"
								>
									{file.path.split("/").pop()}
								</button>
							))}
						</div>
					)}

					{/* Modal file content */}
					<div
						ref={modalScrollRef}
						className="flex-1 overflow-y-auto px-6 pb-6 pt-3"
					>
						{fullLoading ? (
							<div className="flex items-center justify-center py-16 text-muted-foreground">
								<Loader2 className="h-5 w-5 animate-spin mr-2" />
								<span className="text-sm">Generating configs...</span>
							</div>
						) : fullError ? (
							<div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
								<XCircle className="h-5 w-5 mb-2 text-destructive" />
								<span className="text-sm">{fullError}</span>
							</div>
						) : modalFiles.length === 0 ? (
							<div className="flex items-center justify-center py-16 text-muted-foreground">
								<span className="text-sm">
									No config generated for this harness.
								</span>
							</div>
						) : (
							<div className="space-y-3">
								{modalFiles.map((file, i) => (
									<div
										key={file.path}
										id={`preview-file-${i}`}
										className="rounded-md border border-border overflow-hidden"
									>
										<div className="px-4 py-2 text-[11px] font-medium text-muted-foreground bg-muted/40 font-[family-name:var(--font-mono)]">
											{file.path}
										</div>
										<pre className="overflow-x-auto whitespace-pre p-4 text-sm leading-relaxed font-[family-name:var(--font-mono)] text-foreground/80 bg-background">
											{file.content}
										</pre>
									</div>
								))}
							</div>
						)}
					</div>
				</DialogContent>
			</Dialog>
		</div>
	);
}
