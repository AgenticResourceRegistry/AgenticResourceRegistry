// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react";
import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useStartMigrationExport } from "@/hooks/use-admin-api";
import type { MigrationScope } from "@/lib/types/admin";
import { ScopeChoiceGroup } from "./migrate-form-fields";

interface MigrateExportFormProps {
	onJobStarted: (jobId: string) => void;
}

const EXPORT_SCOPE_OPTIONS = [
	{
		value: "postgres" as const,
		title: "Registry data",
		description: "Users, Agent Files, components, settings, reviews, and metadata.",
	},
	{
		value: "both" as const,
		title: "Registry + telemetry",
		description: "Full instance export with registry records and ClickHouse trace history.",
	},
];

export function MigrateExportForm({ onJobStarted }: MigrateExportFormProps) {
	const [scope, setScope] = useState<MigrationScope>("postgres");
	const exportMutation = useStartMigrationExport();

	const handleStart = () => {
		exportMutation.mutate(scope, {
			onSuccess: (data) => onJobStarted(data.job_id),
		});
	};

	return (
		<div className="space-y-5">
			<div className="rounded-md border border-warning/30 bg-warning/10 p-3">
				<div className="flex gap-2">
					<AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
					<div className="space-y-1">
						<p className="text-sm font-medium">Artifacts contain sensitive data</p>
						<p className="text-xs leading-5 text-muted-foreground">
							Exports may include hashed credentials, API keys, and telemetry that can contain PII. Store them like production backups.
						</p>
					</div>
				</div>
			</div>

			<div className="space-y-2">
				<div>
					<label className="text-sm font-medium">What should be exported?</label>
					<p className="mt-1 text-xs text-muted-foreground">Telemetry-only export is not supported here. Choose Registry + telemetry for a full move.</p>
				</div>
				<ScopeChoiceGroup name="export-scope" value={scope} onChange={setScope} options={EXPORT_SCOPE_OPTIONS} />
			</div>

			<Button type="button" className="w-full" onClick={handleStart} disabled={exportMutation.isPending}>
				{exportMutation.isPending ? "Starting export..." : "Start export"}
			</Button>
		</div>
	);
}
