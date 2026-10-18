// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0


import { useState, useMemo } from "react";
import { ArrowRight, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { VersionSuggestions } from "@/lib/types";

type BumpType = "patch" | "minor" | "major";

function bumpVersion(current: string, type: BumpType): string {
  const parts = current.split(".").map(Number);
  if (parts.length !== 3 || parts.some(isNaN)) return current;
  if (type === "major") return `${parts[0] + 1}.0.0`;
  if (type === "minor") return `${parts[0]}.${parts[1] + 1}.0`;
  return `${parts[0]}.${parts[1]}.${parts[2] + 1}`;
}

export { bumpVersion };

interface VersionBumpDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  currentVersion: string;
  suggestions: VersionSuggestions | undefined;
  onConfirm: (version: string) => void;
  publishing: boolean;
  title?: string;
  description?: string;
  confirmLabel?: string;
  /**
   * Agent releases keep every component at the version the current release pins.
   * When provided, the dialog lets the author move them to their latest approved
   * releases instead.
   */
  refreshComponents?: {
    checked: boolean;
    onCheckedChange: (checked: boolean) => void;
  };
}

export function VersionBumpDialog({
  open,
  onOpenChange,
  currentVersion,
  suggestions,
  onConfirm,
  publishing,
  title = "Release New Version",
  description = "Choose how to bump the version for this release.",
  confirmLabel = "Release",
  refreshComponents,
}: VersionBumpDialogProps) {
  const [selection, setSelection] = useState<BumpType>("patch");

  const previewVersion = useMemo(() => {
    if (suggestions) return suggestions.suggestions[selection];
    return bumpVersion(currentVersion, selection);
  }, [currentVersion, selection, suggestions]);

  const options: { value: BumpType; label: string; description: string }[] =
    useMemo(
      () => [
        {
          value: "patch",
          label: "Patch",
          description: `${currentVersion} → ${suggestions?.suggestions.patch ?? bumpVersion(currentVersion, "patch")}`,
        },
        {
          value: "minor",
          label: "Minor",
          description: `${currentVersion} → ${suggestions?.suggestions.minor ?? bumpVersion(currentVersion, "minor")}`,
        },
        {
          value: "major",
          label: "Major",
          description: `${currentVersion} → ${suggestions?.suggestions.major ?? bumpVersion(currentVersion, "major")}`,
        },
      ],
      [currentVersion, suggestions],
    );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>

        <div className="space-y-2 py-2">
          {options.map((opt) => (
            <label
              key={opt.value}
              className={`flex cursor-pointer items-center gap-3 rounded-md border px-4 py-3 transition-colors ${
                selection === opt.value
                  ? "border-primary bg-primary/5"
                  : "border-border hover:bg-muted/50"
              }`}
            >
              <input
                type="radio"
                name="version-bump"
                value={opt.value}
                checked={selection === opt.value}
                onChange={() => setSelection(opt.value)}
                className="h-4 w-4 accent-primary"
              />
              <span className="flex-1">
                <span className="block text-sm font-medium">{opt.label}</span>
                <span className="block font-mono text-xs text-muted-foreground">
                  {opt.description}
                </span>
              </span>
            </label>
          ))}
        </div>

        {refreshComponents && (
          <label className="flex cursor-pointer items-start gap-3 rounded-md border border-border px-4 py-3 transition-colors hover:bg-muted/50">
            <Checkbox
              className="mt-0.5"
              checked={refreshComponents.checked}
              onCheckedChange={(checked) => refreshComponents.onCheckedChange(checked === true)}
              aria-label="Update components to their latest approved versions"
            />
            <span className="flex-1">
              <span className="block text-sm font-medium">Update components to their latest approved versions</span>
              <span className="block text-xs text-muted-foreground">
                Otherwise every component keeps the version this Agent File currently pins.
              </span>
            </span>
          </label>
        )}

        <div className="rounded-md bg-muted/50 px-4 py-2.5 text-center">
          <span className="text-xs text-muted-foreground">New version: </span>
          <span className="font-mono text-sm font-semibold">{previewVersion}</span>
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={publishing}
          >
            Cancel
          </Button>
          <Button onClick={() => onConfirm(previewVersion)} disabled={publishing}>
            {publishing ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <ArrowRight className="mr-2 h-4 w-4" />
            )}
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
