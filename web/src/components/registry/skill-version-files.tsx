// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react";
import { Download, FolderTree } from "lucide-react";
import { Button } from "@/components/ui/button";
import { SkillFileTree } from "@/components/registry/skill-file-tree";
import { useSkillFileContent, useSkillVersionManifest } from "@/hooks/use-api";

/** Show an exact version's files; the server authorizes draft access. */
export function SkillVersionFiles({ listingId, versionId, status = "approved" }: { listingId: string; versionId: string; status?: string }) {
  const versionLabel = status === "pending" ? "submission" : status === "approved" ? "release" : "draft";
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const manifestQuery = useSkillVersionManifest(listingId, versionId);
  const manifest = manifestQuery.data?.version_id === versionId ? manifestQuery.data : undefined;
  const selectedFile = manifest?.files.find((file) => file.path === selectedPath);
  const fileQuery = useSkillFileContent(listingId, versionId, selectedFile?.path ?? null);
  const file = fileQuery.data;
  const [downloadError, setDownloadError] = useState("");

  async function downloadBinary(blob: Blob) {
    if (!selectedFile) return;
    setDownloadError("");
    try {
      if (!globalThis.crypto?.subtle) throw new Error("Verified downloads require HTTPS or localhost. Reopen Observal over a secure connection.");
      const bytes = await blob.arrayBuffer();
      if (bytes.byteLength !== selectedFile.size) throw new Error("The file changed; reload the manifest before downloading.");
      const hash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), (byte) =>
        byte.toString(16).padStart(2, "0")).join("");
      if (hash !== selectedFile.sha256) throw new Error("The downloaded file did not match its reviewed checksum.");
      // The server sends octet-stream, not an active HTML/SVG document. Never
      // render uploaded binary bytes in a frame or a page-controlled URL.
      const url = URL.createObjectURL(new Blob([bytes], { type: "application/octet-stream" }));
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = selectedFile.path.split("/").at(-1) || "download";
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (error) {
      setDownloadError(error instanceof Error ? error.message : "Could not verify the downloaded file.");
    }
  }

  return (
    <section aria-label={status === "approved" ? "Reviewed skill files" : status === "pending" ? "Submitted skill files" : "Saved skill draft files"} className="rounded-md border border-border overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
        <FolderTree className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
        <h2 className="text-sm font-semibold">Files in this {versionLabel}</h2>
      </div>
      {manifestQuery.isPending ? <p className="p-4 text-sm text-muted-foreground">Loading file list…</p>
        : manifestQuery.isError || !manifest ? (
          <p role="alert" className="p-4 text-sm text-destructive">
            This version's files are unavailable. Refresh before relying on its contents.
          </p>
        ) : (
          <div className="grid gap-0 md:grid-cols-[minmax(0,240px)_minmax(0,1fr)]">
            <SkillFileTree
              manifest={manifest.files}
              selectedPath={selectedPath ?? undefined}
              onSelectFile={(path) => { setSelectedPath(path); setDownloadError(""); }}
              readOnly
              className="border-b md:border-b-0 md:border-r border-border"
            />
            <div className="min-w-0 p-4" aria-live="polite">
              {!selectedFile ? <p className="text-sm text-muted-foreground">Select a file to inspect its contents.</p>
                : <>
                  <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground mb-3">
                    <span className="font-mono break-all text-foreground">{selectedFile.path}</span>
                    <span>{selectedFile.size.toLocaleString()} bytes</span>
                    {selectedFile.mode === "0755" && <span>Executable</span>}
                  </div>
                  {fileQuery.isPending ? <p className="text-sm text-muted-foreground">Loading file…</p>
                    : fileQuery.isError || !file ? <p role="alert" className="text-sm text-destructive">File preview unavailable. Refresh before installing.</p>
                    : file.encoding === "utf-8" ? (
                      file.version_id !== versionId || file.revision !== manifest.revision ||
                      file.file.path !== selectedFile.path || file.file.sha256 !== selectedFile.sha256 ? (
                        <p role="alert" className="text-sm text-destructive">The file changed. Refresh this version.</p>
                      ) : <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-md bg-surface-sunken p-3 text-xs font-mono">{file.content || "(empty file)"}</pre>
                    ) : <Button size="sm" variant="outline" onClick={() => void downloadBinary(file.content)}>
                      <Download className="mr-2 h-4 w-4" />Download binary file
                    </Button>}
                  {downloadError && <p role="alert" className="mt-2 text-xs text-destructive">{downloadError}</p>}
                </>}
            </div>
          </div>
        )}
    </section>
  );
}
