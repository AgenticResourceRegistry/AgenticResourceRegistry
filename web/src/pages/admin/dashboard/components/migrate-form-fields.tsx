// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { UploadCloud, X } from "lucide-react";
import type { ChangeEvent } from "react";
import { cn } from "@/lib/utils";
import type { MigrationScope } from "@/lib/types/admin";

type ScopeOption = {
	value: MigrationScope;
	title: string;
	description: string;
	disabled?: boolean;
	disabledReason?: string;
};

export const ALL_SCOPE_OPTIONS: ScopeOption[] = [
	{
		value: "postgres",
		title: "Registry data",
		description: "Users, Agent Files, components, settings, reviews, and metadata.",
	},
	{
		value: "clickhouse",
		title: "Telemetry data",
		description: "Trace and span history stored in ClickHouse.",
	},
	{
		value: "both",
		title: "Registry + telemetry",
		description: "Use for a full instance move when both stores are available.",
	},
];

export function ScopeChoiceGroup({
	name,
	value,
	onChange,
	options = ALL_SCOPE_OPTIONS,
}: {
	name: string;
	value: MigrationScope;
	onChange: (value: MigrationScope) => void;
	options?: ScopeOption[];
}) {
	return (
		<div className={cn("grid gap-2", options.length === 2 ? "sm:grid-cols-2" : "sm:grid-cols-3")}>
			{options.map((option) => {
				const checked = value === option.value;
				return (
					<label
						key={option.value}
						className={cn(
							"relative rounded-md border bg-card p-3 text-left transition-colors",
							checked ? "border-primary bg-primary/5" : "border-border hover:border-primary/50",
							option.disabled && "cursor-not-allowed opacity-50 hover:border-border",
						)}
					>
						<input
							type="radio"
							name={name}
							value={option.value}
							checked={checked}
							disabled={option.disabled}
							onChange={() => onChange(option.value)}
							className="sr-only"
						/>
						<span className="block text-sm font-medium">{option.title}</span>
						<span className="mt-1 block text-xs leading-5 text-muted-foreground">{option.description}</span>
						{option.disabledReason && (
							<span className="mt-2 block text-xs text-muted-foreground">{option.disabledReason}</span>
						)}
					</label>
				);
			})}
		</div>
	);
}

function isArchive(file: File): boolean {
	return /\.(?:tar\.gz|tgz|gz)$/i.test(file.name);
}

function isTelemetryArtifact(file: File): boolean {
	return file.name.toLowerCase().startsWith("telemetry") || file.name.toLowerCase().endsWith(".parquet");
}

export function artifactSelectionError(files: File[], scope: MigrationScope): string | null {
	const hasRegistry = files.some((file) => isArchive(file) && !isTelemetryArtifact(file));
	const hasTelemetry = files.some(isTelemetryArtifact);
	if (scope === "postgres" && (!hasRegistry || hasTelemetry)) return "Select the PostgreSQL registry archive only.";
	if (scope === "clickhouse" && (!hasTelemetry || hasRegistry)) return "Select telemetry artifacts only.";
	if (scope === "both" && (!hasRegistry || !hasTelemetry)) {
		return "Select both the PostgreSQL registry archive and the telemetry archive.";
	}
	return null;
}

function formatFileSize(size: number): string {
	if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
	if (size < 1024 * 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MB`;
	return `${(size / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export function ArtifactPicker({
	files,
	onChange,
	description,
}: {
	files: File[];
	onChange: (files: File[]) => void;
	description: string;
}) {
	const handleChange = (event: ChangeEvent<HTMLInputElement>) => {
		const next = [...files];
		for (const selected of Array.from(event.target.files ?? [])) {
			const duplicate = next.some(
				(file) => file.name === selected.name && file.size === selected.size && file.lastModified === selected.lastModified,
			);
			if (!duplicate) next.push(selected);
		}
		onChange(next);
		event.target.value = "";
	};

	const removeFile = (index: number) => {
		onChange(files.filter((_, fileIndex) => fileIndex !== index));
	};

	return (
		<div className="space-y-2">
			<label className="block rounded-md border border-dashed border-border bg-muted/20 p-4 transition-colors hover:border-primary/50 hover:bg-muted/30">
				<input type="file" multiple accept=".tar.gz,.tgz,.gz,.parquet" onChange={handleChange} className="sr-only" />
				<div className="flex items-start gap-3">
					<div className="rounded-md border border-border bg-background p-2">
						<UploadCloud className="h-4 w-4 text-muted-foreground" />
					</div>
					<div className="min-w-0 flex-1">
						<p className="text-sm font-medium">Choose migration artifacts</p>
						<p className="mt-1 text-xs leading-5 text-muted-foreground">{description}</p>
						<p className="mt-2 text-xs font-medium text-foreground">
							{files.length > 0 ? `${files.length} file${files.length === 1 ? "" : "s"} selected` : "No files selected"}
						</p>
					</div>
				</div>
			</label>
			{files.length > 0 && (
				<ul className="space-y-1.5" aria-label="Selected migration artifacts">
					{files.map((file, index) => (
						<li
							key={`${file.name}-${file.size}-${file.lastModified}`}
							className="flex items-center justify-between gap-3 rounded-md border border-border bg-card px-3 py-2"
						>
							<div className="min-w-0">
								<p className="truncate text-xs font-medium text-foreground">{file.name}</p>
								<p className="text-xs text-muted-foreground">{formatFileSize(file.size)}</p>
							</div>
							<button
								type="button"
								onClick={() => removeFile(index)}
								className="rounded-sm p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
								aria-label={`Remove ${file.name}`}
							>
								<X className="h-3.5 w-3.5" />
							</button>
						</li>
					))}
				</ul>
			)}
		</div>
	);
}
