// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0


import { AlertCircle, AlertTriangle, CheckCircle2 } from "lucide-react";
import type { ValidationResult } from "@/lib/types";

interface ValidationPanelProps {
  result: ValidationResult | null;
  isValidating: boolean;
}

export function ValidationPanel({ result, isValidating }: ValidationPanelProps) {
  if (isValidating) {
    return (
      <div role="status" className="rounded-md border border-border p-3 text-sm text-muted-foreground">
        Checking component compatibility…
      </div>
    );
  }

  if (!result) return null;

  const errors = result.issues.filter((i) => i.severity === "error");
  const warnings = result.issues.filter((i) => i.severity === "warning");

  if (result.valid && errors.length === 0 && warnings.length === 0) {
    return (
      <div className="rounded-md border border-success/50 bg-success/5 p-3 text-sm space-y-2">
        <div className="flex items-center gap-2 text-success">
          <CheckCircle2 className="h-4 w-4" />
          <span>All components valid</span>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {!result.valid && errors.length === 0 && warnings.length === 0 && (
        <p role="alert" className="rounded-md border border-destructive/50 bg-destructive/5 p-3 text-sm text-destructive">
          Could not validate these components. Check the selection and try again.
        </p>
      )}
      {errors.length > 0 && (
        <div className="rounded-md border border-destructive/50 bg-destructive/5 p-3 text-sm space-y-2">
          {errors.map((error, idx) => (
            <div
              key={idx}
              className="flex items-start gap-2 text-destructive"
            >
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{error.message}</span>
            </div>
          ))}
        </div>
      )}
      {warnings.length > 0 && (
        <div className="rounded-md border border-warning/50 bg-warning/5 p-3 text-sm space-y-2">
          {warnings.map((warning, idx) => (
            <div
              key={idx}
              className="flex items-start gap-2 text-warning"
            >
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{warning.message}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
