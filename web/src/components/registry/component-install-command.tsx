// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback, useEffect, useMemo } from "react";
import { Check, Copy, Terminal } from "lucide-react";
import { toast } from "sonner";
import { copyToClipboard } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { PickerSelect } from "@/components/ui/picker-select";
import { useHarnesses } from "@/hooks/use-harnesses";
import type { ComponentVersionSummary } from "@/lib/types";

interface ComponentInstallCommandProps {
  componentType: string;
  componentName: string;
  /** The approved version actually selected in the detail view; null while unresolved. */
  selectedSkillVersion?: ComponentVersionSummary | null;
  skillFolderDeliveryEnabled?: boolean;
  selectedVersionRequiresFolderDelivery?: boolean;
  latestMayRequireFolderDelivery?: boolean;
}

export function ComponentInstallCommand({
  componentType, componentName, selectedSkillVersion, skillFolderDeliveryEnabled,
  selectedVersionRequiresFolderDelivery, latestMayRequireFolderDelivery,
}: ComponentInstallCommandProps) {
  const { data: harnesses, defaultHarness } = useHarnesses();
  const requiredCapability = componentType === "mcp" ? "mcp_servers" : `${componentType}s`;
  const supportedHarnesses = useMemo(
    () => (harnesses ?? []).filter((entry) => entry.capabilities.includes(requiredCapability)),
    [harnesses, requiredCapability],
  );
  const [harness, setHarness] = useState("");
  useEffect(() => {
    if (supportedHarnesses.length === 0) return;
    if (!supportedHarnesses.some((entry) => entry.name === harness)) {
      setHarness(supportedHarnesses.find((entry) => entry.name === defaultHarness)?.name ?? supportedHarnesses[0].name);
    }
  }, [supportedHarnesses, defaultHarness, harness]);
  const [copied, setCopied] = useState(false);
  const [scope, setScope] = useState("user");
  const [release, setRelease] = useState("pinned");

  const effectiveHarness = supportedHarnesses.some((entry) => entry.name === harness)
    ? harness : (supportedHarnesses.find((entry) => entry.name === defaultHarness)?.name ?? supportedHarnesses[0]?.name);
  const skillVersion = selectedSkillVersion?.status === "approved" ? selectedSkillVersion : null;
  const blockedByRollout = componentType === "skill" && !skillFolderDeliveryEnabled && (
    release === "latest" ? (latestMayRequireFolderDelivery ?? true) :
      (skillVersion ? (selectedVersionRequiresFolderDelivery ?? true) : false)
  );
  const command = effectiveHarness && !blockedByRollout && (componentType !== "skill" || release === "latest" || skillVersion)
    ? `observal registry ${componentType} install ${componentName} --harness ${effectiveHarness}` +
      (componentType === "skill" ? `${release === "pinned" ? ` --version ${skillVersion!.version}` : ""} --scope ${scope}` : "")
    : null;
  useEffect(() => setCopied(false), [command]);

  const handleCopy = useCallback(async () => {
    if (!command) return;
    try {
      await copyToClipboard(command);
      setCopied(true);
      toast.success("Copied to clipboard");
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error("Failed to copy");
    }
  }, [command]);

  return (
    <div className="border border-border rounded-md bg-surface-sunken">
      <div className="space-y-2 px-3 py-3 border-b border-border">
        <div className="flex items-center gap-2">
          <Terminal className="h-3.5 w-3.5 text-muted-foreground" />
          <span className="text-xs font-medium text-muted-foreground">Install</span>
        </div>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {componentType === "skill" && (
            <>
              <PickerSelect
                value={release}
                onValueChange={setRelease}
                ariaLabel="Skill release selection"
                className="w-full min-w-0"
                inputClassName="h-7 border-border text-xs"
                options={[{ value: "pinned", label: "Shown version" }, { value: "latest", label: "Follow latest" }]}
              />
              <PickerSelect
                value={scope}
                onValueChange={setScope}
                ariaLabel="Skill installation scope"
                className="w-full min-w-0"
                inputClassName="h-7 border-border text-xs"
                options={[{ value: "user", label: "User (global)" }, { value: "project", label: "Project" }]}
              />
            </>
          )}
          <PickerSelect
            value={effectiveHarness ?? ""}
            onValueChange={setHarness}
            ariaLabel="Harness"
            className="w-full min-w-0 sm:col-span-2"
            inputClassName="h-7 border-border text-xs"
            options={supportedHarnesses.map((entry) => ({ value: entry.name, label: entry.display_name }))}
          />
        </div>
      </div>
      <div className="flex items-center gap-2 p-3">
        {command ? (
          <>
            <code className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap text-sm font-mono select-all text-foreground leading-relaxed">
              <span className="text-muted-foreground">$</span> {command}
            </code>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 shrink-0 hover:bg-accent"
              onClick={handleCopy}
              aria-label="Copy command"
            >
              {copied ? <Check className="h-3.5 w-3.5 text-success" /> : <Copy className="h-3.5 w-3.5" />}
            </Button>
          </>
        ) : (
          <p role="status" className="text-sm text-muted-foreground">
            {blockedByRollout ? "Complete-folder installs are not available on this server yet." :
              componentType === "skill" && !skillVersion && release === "pinned"
                ? (selectedSkillVersion ? "The shown version is not approved yet; choose Follow latest or an approved version." : "Loading approved version…")
                : "No supported harness available"}
          </p>
        )}
      </div>
      {componentType === "skill" && command && (
        <p className="px-3 pb-3 text-xs text-muted-foreground">
          Scroll the command to read it in full, or copy it. {scope === "project" ? "Run from the project root; this installs into that project." : "Installs for your user account."}
          {release === "latest" ? " Follow latest may install a newer release than the one shown here." : " The command installs exactly the approved release shown."}
        </p>
      )}
    </div>
  );
}
