// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useState } from "react";
import { useCreateSkillSuccessor, useImportSkillFolder, useSkillApprovedBase } from "@/hooks/use-api";
import type { SkillResource, SkillVersionManifest } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

function suggestedVersion(base: string): string {
  const parsed = /^(\d+)\.(\d+)\.(\d+)$/.exec(base);
  return parsed ? `${parsed[1]}.${parsed[2]}.${Number(parsed[3]) + 1}` : "";
}

async function captureFolder(files: FileList): Promise<{ skill_md_content: string; extra_files: SkillResource[] }> {
  const selected = Array.from(files);
  if (selected.length < 1 || selected.length > 129) throw new Error(`Select a folder with SKILL.md and at most 128 other files (got ${selected.length}).`);
  const bytes = selected.reduce((sum, file) => sum + file.size, 0);
  if (bytes > 4 * 1024 * 1024) throw new Error(`Folder has ${bytes} bytes; the maximum is 4194304.`);
  if (selected.some((file) => file.size > 2 * 1024 * 1024)) throw new Error("Each folder file must be at most 2097152 bytes.");
  const paths = selected.map((file) => {
    const relative = file.webkitRelativePath;
    if (!relative || !relative.includes("/")) throw new Error("Browser cannot preserve relative paths. Use observal registry skill import-folder instead.");
    return relative.slice(relative.indexOf("/") + 1);
  });
  if (!paths.includes("SKILL.md")) throw new Error("The selected folder must have SKILL.md at its root.");
  if (paths.some((path) => /(?:secret|credential|private|password|token|\.env|\.pem|\.key)/i.test(path))) {
    throw new Error("A likely-sensitive file was selected. Review it locally and use the CLI's explicit --allow-sensitive option if intentional.");
  }
  if (paths.some((path) => !path || path.length > 240 || path.split("/").some((part) =>
    !part || part.startsWith(".") || part === "node_modules" || part === ".venv"))) {
    throw new Error("Hidden, excluded or unsafe paths cannot be imported in the browser. Use the CLI to review exclusions.");
  }
  if (new Set(paths.map((path) => path.normalize("NFC").toLowerCase())).size !== paths.length) {
    throw new Error("The selected folder has duplicate or case-colliding paths.");
  }
  const entries = await Promise.all(selected.map(async (file, index) => {
    const content = new Uint8Array(await file.arrayBuffer());
    try {
      return { path: paths[index], content: new TextDecoder("utf-8", { fatal: true }).decode(content) } satisfies SkillResource;
    } catch {
      const chunks: string[] = [];
      for (let i = 0; i < content.length; i += 8192) {
        chunks.push(String.fromCharCode(...content.subarray(i, i + 8192)));
      }
      return { path: paths[index], content: btoa(chunks.join("")), encoding: "base64" as const } satisfies SkillResource;
    }
  }));
  const skillMd = entries.find((entry) => entry.path === "SKILL.md");
  if (!skillMd || skillMd.encoding === "base64") throw new Error("SKILL.md must be UTF-8 text.");
  return { skill_md_content: skillMd.content, extra_files: entries.filter((entry) => entry.path !== "SKILL.md") };
}

export function SkillSuccessorDialog({ open, onOpenChange, listingId, onCreated }: {
  open: boolean;
  onOpenChange: (value: boolean) => void;
  listingId: string;
  onCreated: (manifest: SkillVersionManifest, version: string) => void;
}) {
  const { data: base, isPending, isError, refetch } = useSkillApprovedBase(listingId, open);
  const fork = useCreateSkillSuccessor();
  const importFolder = useImportSkillFolder();
  const [version, setVersion] = useState("");
  const [description, setDescription] = useState("");
  const [changelog, setChangelog] = useState("");
  const [importMode, setImportMode] = useState(false);
  const [files, setFiles] = useState<FileList | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (open) void refetch();
    else {
      setVersion("");
      setDescription("");
      setChangelog("");
      setFiles(null);
      setImportMode(false);
      setError("");
    }
  }, [open, refetch]);
  useEffect(() => {
    if (open && base) {
      setVersion((current) => current || suggestedVersion(base.version));
      setImportMode(base.delivery_mode === "git_fetch" || base.import_required === true);
    }
  }, [open, base]);

  async function create() {
    if (!base) return;
    setError("");
    const body = {
      version: version.trim(), description: description.trim(), changelog: changelog.trim(),
      base_version_id: base.version_id, observed_base_revision: base.revision,
    };
    try {
      const result = importMode
        ? await importFolder.mutateAsync({ listingId, body: { ...body, ...await captureFolder(files!) } })
        : await fork.mutateAsync({ listingId, body });
      onCreated(result, body.version);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not create this version; your input is preserved.");
    }
  }
  const busy = fork.isPending || importFolder.isPending;
  return (
    <Dialog open={open} onOpenChange={(value) => { if (!busy) onOpenChange(value); }}>
      <DialogContent className="max-w-xl">
        <DialogHeader><DialogTitle>Create next skill folder version</DialogTitle></DialogHeader>
        {isPending ? <p className="text-sm text-muted-foreground">Checking the exact approved base…</p> : isError || !base ? (
          <div role="alert" className="space-y-2 text-sm text-destructive">Cannot read the approved base. No draft was created.
            <Button type="button" variant="outline" onClick={() => void refetch()}>Retry</Button>
          </div>
        ) : (
          <div className="space-y-4 text-sm">
            <p>Reviewed base <strong>v{base.version}</strong> · <code className="text-xs">{base.version_id.slice(0, 8)}</code>. This release stays immutable; your changes are a separate draft.</p>
            <label className="block space-y-1">Next version
              <Input value={version} onChange={(event) => setVersion(event.target.value)} placeholder="1.1.0" autoComplete="off" required />
            </label>
            <label className="block space-y-1">Description
              <Input value={description} onChange={(event) => setDescription(event.target.value)} required />
            </label>
            <label className="block space-y-1">Changelog (optional)
              <Input value={changelog} onChange={(event) => setChangelog(event.target.value)} />
            </label>
            {importMode && <p className="text-xs text-muted-foreground">This reviewed {base.delivery_mode === "git_fetch" ? "Git" : "historical direct"} release cannot be forked from stored files. Select the complete local folder you reviewed; the old release is unchanged.</p>}
            {importMode && <div className="space-y-2">
              <label className="block space-y-1">Complete local folder (including SKILL.md)
                <Input type="file" multiple ref={(element) => { element?.setAttribute("webkitdirectory", ""); }}
                  onChange={(event) => setFiles(event.target.files)} />
              </label>
              <p className="text-xs text-muted-foreground">The old Git files are not stored for comparison. Browser uploads cannot infer executable permissions; open the draft and set script modes before saving. Limit: 128 extra files, 2 MiB per file, 4 MiB total.</p>
            </div>}
            {!importMode && <p className="text-xs text-muted-foreground">The exact reviewed folder is copied into your new draft. You can upload files and edit modes before submitting.</p>}
            {error && <p role="alert" className="text-destructive">{error}</p>}
          </div>
        )}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>Cancel</Button>
          <Button type="button" disabled={!base || busy || !version.trim() || !description.trim() || (importMode && !files?.length)}
            onClick={() => void create()}>{busy ? "Creating…" : importMode ? "Import folder as next version" : "Create new folder version"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
