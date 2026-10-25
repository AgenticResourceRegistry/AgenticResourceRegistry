// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react";
import { GitFork, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PickerSelect } from "@/components/ui/picker-select";
import { slugifyRegistryText } from "@/lib/registry-name";
import type { ForkRequest, Team } from "@/lib/types";

interface ForkDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  kind: string;
  sourceName: string;
  sourceIsPrivate: boolean;
  sourceTeamId?: string | null;
  versions: { version: string; status: string }[];
  initialVersion?: string;
  teams: Team[];
  username?: string | null;
  onFork: (body: ForkRequest) => Promise<void>;
}

export function ForkDialog({ open, onOpenChange, kind, sourceName, sourceIsPrivate, sourceTeamId, versions, initialVersion, teams, username, onFork }: ForkDialogProps) {
  const approved = versions.filter((v) => v.status === "approved");
  const [name, setName] = useState(sourceName);
  const [version, setVersion] = useState(initialVersion && approved.some((v) => v.version === initialVersion) ? initialVersion : approved[0]?.version ?? "");
  const privateTarget = sourceIsPrivate
    ? sourceTeamId ? teams.find((t) => t.id === sourceTeamId) : teams.find((t) => t.is_personal && t.visibility === "private" && t.role === "owner")
    : undefined;
  const [teamId, setTeamId] = useState("");
  const targetTeamId = sourceIsPrivate ? privateTarget?.id ?? "" : teamId;
  const selectedTeam = teams.find((t) => t.id === targetTeamId);
  const locked = sourceIsPrivate || selectedTeam?.visibility === "private";
  const [visibility, setVisibility] = useState<"public" | "team">(locked ? "team" : "public");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const slug = slugifyRegistryText(name);
  const namespace = selectedTeam?.handle ?? username;
  const eligibleTeams = teams.filter((t) => !!t.role && (!t.is_personal || t.role === "owner"));

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setPending(true);
    try {
      await onFork({ name: name.trim(), version, team_id: targetTeamId || null, visibility: locked ? "team" : visibility });
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create fork. Please try again.");
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(value) => { if (!pending) onOpenChange(value); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Fork {kind}</DialogTitle>
          <DialogDescription>Create an independent draft from an approved version. You can edit it before submitting for review.</DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-5">
          <div className="space-y-2">
            <Label htmlFor="fork-name">Name</Label>
            <Input id="fork-name" value={name} maxLength={255} onChange={(e) => { setName(e.target.value); setError(""); }} required autoFocus />
            <p className="text-xs text-muted-foreground">New reference: {namespace && slug ? `${namespace}/${slug}` : "Enter a name to preview the reference"}</p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="fork-version">Base version</Label>
            <select id="fork-version" className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm" value={version} onChange={(e) => setVersion(e.target.value)} required>
              {approved.map((v) => <option value={v.version} key={v.version}>{v.version}</option>)}
            </select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="fork-target">Destination</Label>
            <select id="fork-target" className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm" value={targetTeamId} disabled={sourceIsPrivate} onChange={(e) => {
              const next = e.target.value;
              setTeamId(next);
              setVisibility(teams.find((t) => t.id === next)?.visibility === "private" ? "team" : "public");
            }}>
              {!sourceIsPrivate && <option value="">Your namespace{username ? ` (@${username})` : ""}</option>}
              {eligibleTeams.filter((t) => !sourceIsPrivate || t.id === privateTarget?.id).map((t) => <option value={t.id} key={t.id}>{t.name} (@{t.handle})</option>)}
            </select>
            {sourceIsPrivate && <p className="text-xs text-muted-foreground">Private sources can only be forked into their original private teamspace (or your private personal teamspace).</p>}
          </div>
          <div className="space-y-2">
            <Label>Visibility</Label>
            <PickerSelect ariaLabel="Fork visibility" value={locked ? "team" : visibility} onValueChange={(v) => setVisibility(v as "public" | "team")} options={locked ? [{ value: "team", label: "Team members only" }] : [{ value: "public", label: "Public" }, ...(targetTeamId ? [{ value: "team", label: "Team members only" }] : [])]} disabled={!!locked} />
          </div>
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={pending}>Cancel</Button>
            <Button type="submit" disabled={pending || !name.trim() || !slug || !version || (sourceIsPrivate && !privateTarget)}>
              {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <GitFork className="h-4 w-4" />}
              Create draft fork
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
