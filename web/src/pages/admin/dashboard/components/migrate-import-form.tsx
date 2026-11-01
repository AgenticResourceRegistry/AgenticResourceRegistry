// SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useStartMigrationImport } from "@/hooks/use-admin-api";
import type { MigrationScope } from "@/lib/types/admin";
import { ArtifactPicker, artifactSelectionError, ScopeChoiceGroup } from "./migrate-form-fields";

interface MigrateImportFormProps {
	onJobStarted: (jobId: string) => void;
}

export function MigrateImportForm({ onJobStarted }: MigrateImportFormProps) {
	const [scope, setScope] = useState<MigrationScope>("both");
	const [files, setFiles] = useState<File[]>([]);
	const importMutation = useStartMigrationImport();
	const selectionError = artifactSelectionError(files, scope);

	const handleStart = () => {
		if (selectionError) return;

		const formData = new FormData();
		files.forEach((file) => formData.append("files", file));
		formData.append("scope", scope);

		importMutation.mutate(formData, {
			onSuccess: (data) => onJobStarted(data.job_id),
		});
	};

	return (
		<div className="space-y-5">
			<ArtifactPicker
				files={files}
				onChange={setFiles}
				description="Upload the registry archive, telemetry parquet files, or both. Validation should be run before import."
			/>

			<div className="space-y-2">
				<div>
					<label className="text-sm font-medium">What should be imported?</label>
					<p className="mt-1 text-xs text-muted-foreground">Match this to the artifacts you uploaded.</p>
				</div>
				<ScopeChoiceGroup name="import-scope" value={scope} onChange={setScope} />
			</div>

			{files.length > 0 && selectionError && <p className="text-xs text-destructive">{selectionError}</p>}

			<Button type="button" className="w-full" onClick={handleStart} disabled={importMutation.isPending || Boolean(selectionError)}>
				{importMutation.isPending ? "Starting import..." : "Start import"}
			</Button>
		</div>
	);
}
