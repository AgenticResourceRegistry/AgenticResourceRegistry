// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useStartMigrationValidate } from "@/hooks/use-admin-api";
import type { MigrationScope } from "@/lib/types/admin";
import { ArtifactPicker, artifactSelectionError, ScopeChoiceGroup } from "./migrate-form-fields";

interface MigrateValidateFormProps {
	onJobStarted: (jobId: string) => void;
}

export function MigrateValidateForm({ onJobStarted }: MigrateValidateFormProps) {
	const [scope, setScope] = useState<MigrationScope>("both");
	const [files, setFiles] = useState<File[]>([]);
	const validateMutation = useStartMigrationValidate();
	const selectionError = artifactSelectionError(files, scope);

	const handleStart = () => {
		if (selectionError) return;

		const formData = new FormData();
		files.forEach((file) => formData.append("files", file));
		formData.append("scope", scope);

		validateMutation.mutate(formData, {
			onSuccess: (data) => onJobStarted(data.job_id),
		});
	};

	return (
		<div className="space-y-5">
			<ArtifactPicker
				files={files}
				onChange={setFiles}
				description="Validate checksums, table counts, and telemetry references before importing into a target instance."
			/>

			<div className="space-y-2">
				<div>
					<label className="text-sm font-medium">What should be validated?</label>
					<p className="mt-1 text-xs text-muted-foreground">Use the same scope you plan to import.</p>
				</div>
				<ScopeChoiceGroup name="validate-scope" value={scope} onChange={setScope} />
			</div>

			{files.length > 0 && selectionError && <p className="text-xs text-destructive">{selectionError}</p>}

			<Button type="button" className="w-full" onClick={handleStart} disabled={validateMutation.isPending || Boolean(selectionError)}>
				{validateMutation.isPending ? "Starting validation..." : "Start validation"}
			</Button>
		</div>
	);
}
