// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useState } from "react";
import { Check, Copy, GitBranch, Loader2, RefreshCw, Webhook } from "lucide-react";
import { toast } from "sonner";
import {
  useConfigureMcpWebhookSync,
  useDisableMcpWebhookSync,
  useMcpWebhookSync,
  useRotateMcpWebhookSecret,
  useRunMcpWebhookSync,
} from "@/hooks/use-api";
import type { McpWebhookProvider, McpWebhookSync } from "@/lib/types";
import { copyToClipboard } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";

const PROVIDER_LABELS: Record<McpWebhookProvider, string> = { github: "GitHub", gitlab: "GitLab" };

/** Same guess the server makes; self-hosted GitLab on another hostname is picked by hand. */
function guessProvider(gitUrl: string): McpWebhookProvider {
  let host = "";
  if (gitUrl.startsWith("git@")) {
    host = gitUrl.slice(4).split(":")[0];
  } else {
    try {
      host = new URL(gitUrl).hostname;
    } catch {
      host = "";
    }
  }
  return host.toLowerCase().includes("gitlab") ? "gitlab" : "github";
}

function CopyField({ label, value, secret = false }: { label: string; value: string; secret?: boolean }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await copyToClipboard(value);
      setCopied(true);
      toast.success(`${label} copied`);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error("Failed to copy");
    }
  }
  return (
    <div className="space-y-1.5">
      <Label className="text-xs text-muted-foreground">{label}</Label>
      <div className="flex items-center gap-2 rounded-md border border-border bg-surface-sunken px-3 py-2">
        <code className={`flex-1 break-all text-xs font-mono ${secret ? "text-warning" : "text-foreground"}`}>
          {value}
        </code>
        <Button variant="ghost" size="icon" className="h-7 w-7 shrink-0" onClick={copy} aria-label={`Copy ${label}`}>
          {copied ? <Check className="h-3.5 w-3.5 text-success" /> : <Copy className="h-3.5 w-3.5" />}
        </Button>
      </div>
    </div>
  );
}

function statusBadge(status?: string | null) {
  if (!status) return <Badge variant="outline">Never synced</Badge>;
  if (status === "success") return <Badge>Synced</Badge>;
  if (status === "failed") return <Badge variant="destructive">Failed</Badge>;
  if (status === "queued" || status === "syncing") {
    return (
      <Badge variant="secondary" className="gap-1">
        <Loader2 className="h-3 w-3 animate-spin" />
        {status === "queued" ? "Queued" : "Syncing"}
      </Badge>
    );
  }
  return <Badge variant="secondary">Skipped</Badge>;
}

function formatTime(value?: string | null) {
  return value ? new Date(value).toLocaleString() : "Never";
}

function githubEvents(state: { sync_on_push: boolean; sync_on_release: boolean }) {
  if (state.sync_on_push && state.sync_on_release) return "Let me select individual events, then Pushes and Releases";
  if (state.sync_on_release) return "Let me select individual events, then Releases";
  return "Just the push event";
}

function gitlabEvents(state: { sync_on_push: boolean; sync_on_release: boolean }) {
  return [state.sync_on_push && "Push events", state.sync_on_release && "Releases events"].filter(Boolean).join(" and ");
}

function SetupSteps({ sync }: { sync: McpWebhookSync }) {
  if (sync.provider === "gitlab") {
    return (
      <ol className="list-decimal space-y-1 pl-5 text-xs text-muted-foreground">
        <li>In the project, open Settings, then Webhooks, then Add new webhook.</li>
        <li>Paste the URL, and paste the secret as the Secret token.</li>
        <li>Under Trigger, select {gitlabEvents(sync)}.</li>
        <li>Keep SSL verification on, then add the webhook.</li>
      </ol>
    );
  }
  return (
    <ol className="list-decimal space-y-1 pl-5 text-xs text-muted-foreground">
      <li>In the repository, open Settings, then Webhooks, then Add webhook.</li>
      <li>Paste the payload URL and the secret.</li>
      <li>Set the content type to application/json.</li>
      <li>Under events, choose: {githubEvents(sync)}.</li>
    </ol>
  );
}

export function McpWebhookSyncPanel({ listingId, gitUrl }: { listingId: string; gitUrl?: string | null }) {
  const { data: sync, isLoading } = useMcpWebhookSync(listingId, !!gitUrl);
  const configure = useConfigureMcpWebhookSync(listingId);
  const rotate = useRotateMcpWebhookSecret(listingId);
  const run = useRunMcpWebhookSync(listingId);
  const disable = useDisableMcpWebhookSync(listingId);

  const [onPush, setOnPush] = useState(true);
  const [onRelease, setOnRelease] = useState(false);
  const [branch, setBranch] = useState("");
  const [provider, setProvider] = useState<McpWebhookProvider>(() => guessProvider(gitUrl ?? ""));
  // The server returns the secret once; keep it on screen until the user leaves the page.
  const [secret, setSecret] = useState<string | null>(null);
  const [confirmDisable, setConfirmDisable] = useState(false);

  useEffect(() => {
    if (!sync?.enabled) return;
    setOnPush(sync.sync_on_push);
    setOnRelease(sync.sync_on_release);
    setBranch(sync.branch ?? "");
    if (sync.provider) setProvider(sync.provider);
  }, [sync?.enabled, sync?.sync_on_push, sync?.sync_on_release, sync?.branch, sync?.provider]);

  function keepSecret(result: McpWebhookSync) {
    if (result.secret) setSecret(result.secret);
  }

  if (!gitUrl) {
    return (
      <div className="rounded-md border border-border p-6 text-sm text-muted-foreground">
        Add a git repository URL to this MCP server in the Edit tab to sync it from GitHub or GitLab.
      </div>
    );
  }
  if (isLoading || !sync) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading sync settings
      </div>
    );
  }

  const noTrigger = !onPush && !onRelease;
  const dirty =
    !sync.enabled ||
    onPush !== sync.sync_on_push ||
    onRelease !== sync.sync_on_release ||
    provider !== sync.provider ||
    (branch.trim() || null) !== (sync.branch ?? null);
  const label = PROVIDER_LABELS[sync.enabled && sync.provider ? sync.provider : provider];

  function save() {
    configure.mutate(
      { sync_on_push: onPush, sync_on_release: onRelease, branch: branch.trim() || null, provider },
      { onSuccess: keepSecret },
    );
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <div className="space-y-1">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <Webhook className="h-4 w-4" /> {label} webhook sync
        </h3>
        <p className="text-sm text-muted-foreground">
          Publish a new version automatically when{" "}
          <span className="font-mono text-xs text-foreground break-all">{gitUrl}</span> changes. Synced versions are
          approved right away, so anyone who can push to the tracked branch or publish a release can change what this
          MCP server installs.
        </p>
      </div>

      <div className="space-y-4 rounded-md border border-border p-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <Label htmlFor="sync-provider" className="text-sm">Repository host</Label>
            <p className="text-xs text-muted-foreground">
              Detected from the repository URL. Pick GitLab for a self-hosted GitLab on its own hostname.
            </p>
          </div>
          <Select value={provider} onValueChange={(value) => setProvider(value as McpWebhookProvider)}>
            <SelectTrigger id="sync-provider" className="h-8 w-32 shrink-0">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="github">GitHub</SelectItem>
              <SelectItem value="gitlab">GitLab</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="flex items-start justify-between gap-4">
          <div>
            <Label htmlFor="sync-push" className="text-sm">On push</Label>
            <p className="text-xs text-muted-foreground">
              Each push to the tracked branch publishes a patch version, or the version in pyproject.toml or
              package.json when you raise it.
            </p>
          </div>
          <Switch id="sync-push" checked={onPush} onCheckedChange={setOnPush} />
        </div>
        <div className="flex items-start justify-between gap-4">
          <div>
            <Label htmlFor="sync-release" className="text-sm">On release</Label>
            <p className="text-xs text-muted-foreground">
              Publishing a {PROVIDER_LABELS[provider]} release publishes its tag (v1.2.3 or 1.2.3) as the version.
            </p>
          </div>
          <Switch id="sync-release" checked={onRelease} onCheckedChange={setOnRelease} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="sync-branch" className="flex items-center gap-1.5 text-sm">
            <GitBranch className="h-3.5 w-3.5" /> Branch
          </Label>
          <Input
            id="sync-branch"
            value={branch}
            onChange={(e) => setBranch(e.target.value)}
            placeholder="Repository default branch"
            className="h-8 max-w-xs text-sm"
          />
        </div>
        {noTrigger && <p className="text-xs text-destructive">Turn on at least one trigger.</p>}
        <div className="flex gap-2">
          <Button size="sm" onClick={save} disabled={noTrigger || !dirty || configure.isPending}>
            {configure.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {sync.enabled ? "Save changes" : "Turn on sync"}
          </Button>
        </div>
      </div>

      {sync.enabled && sync.webhook_url && (
        <>
          <div className="space-y-4 rounded-md border border-border p-4">
            <h4 className="text-xs font-semibold font-display uppercase tracking-wider text-muted-foreground">
              {label} setup
            </h4>
            <CopyField label={sync.provider === "gitlab" ? "URL" : "Payload URL"} value={sync.webhook_url} />
            {secret ? (
              <div className="space-y-1.5">
                <CopyField label={sync.provider === "gitlab" ? "Secret token" : "Secret"} value={secret} secret />
                <p className="text-xs text-warning">This secret is shown only once. Add it in {label} now.</p>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                The secret was shown when sync was turned on. Lost it? Rotate it and update {label}.
              </p>
            )}
            <SetupSteps sync={sync} />
          </div>

          <div className="space-y-3 rounded-md border border-border p-4">
            <div className="flex items-center justify-between">
              <h4 className="text-xs font-semibold font-display uppercase tracking-wider text-muted-foreground">
                Last sync
              </h4>
              {statusBadge(sync.last_sync_status)}
            </div>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
              <dt className="text-muted-foreground">Last delivery</dt>
              <dd>
                {formatTime(sync.last_delivery_at)}
                {sync.last_event && <span className="text-muted-foreground"> ({sync.last_event})</span>}
              </dd>
              <dt className="text-muted-foreground">Last synced</dt>
              <dd>{formatTime(sync.last_synced_at)}</dd>
              {sync.last_version && (
                <>
                  <dt className="text-muted-foreground">Version</dt>
                  <dd className="font-mono">{sync.last_version}</dd>
                </>
              )}
              {sync.last_synced_sha && (
                <>
                  <dt className="text-muted-foreground">Commit</dt>
                  <dd className="font-mono">{sync.last_synced_sha.slice(0, 12)}</dd>
                </>
              )}
            </dl>
            {sync.last_sync_error && (
              <p
                className={`text-xs ${sync.last_sync_status === "failed" ? "text-destructive" : "text-muted-foreground"}`}
              >
                {sync.last_sync_error}
              </p>
            )}
          </div>

          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" onClick={() => run.mutate(undefined)} disabled={run.isPending}>
              <RefreshCw className={`h-3.5 w-3.5 ${run.isPending ? "animate-spin" : ""}`} /> Sync now
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => rotate.mutate(undefined, { onSuccess: keepSecret })}
              disabled={rotate.isPending}
            >
              Rotate secret
            </Button>
            <Button size="sm" variant="ghost" className="text-destructive" onClick={() => setConfirmDisable(true)}>
              Turn off sync
            </Button>
          </div>
        </>
      )}

      <Dialog open={confirmDisable} onOpenChange={setConfirmDisable}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Turn off webhook sync?</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            {label} deliveries will be rejected. Versions that were already published stay. Remove the webhook from
            the repository settings as well.
          </p>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmDisable(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={disable.isPending}
              onClick={() =>
                disable.mutate(undefined, {
                  onSuccess: () => {
                    setConfirmDisable(false);
                    setSecret(null);
                  },
                })
              }
            >
              Turn off sync
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
