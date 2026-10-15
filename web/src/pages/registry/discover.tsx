// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import {
  BookOpenCheck,
  Bot,
  Box,
  Check,
  Copy,
  GitBranch,
  MessageSquareText,
  Network,
  PlugZap,
  Search,
  type LucideIcon,
} from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { PageHeader } from "@/components/layouts/page-header";
import { HarnessBadges } from "@/components/registry/harness-badges";
import { EmptyState } from "@/components/shared/empty-state";
import { TableSkeleton } from "@/components/shared/skeleton-layouts";
import { useDiscoverySearch } from "@/hooks/use-discovery-api";
import { useHarnesses } from "@/hooks/use-harnesses";
import { registryItemPath } from "@/lib/registry-name";
import { RecommendedBadge } from "@/components/registry/recommended-badge";
import type { DiscoveryAvailability, DiscoveryKind, DiscoverySearchResult } from "@/lib/types";

const EXAMPLES = [
  "review a pull request for security vulnerabilities",
  "query our postgres database",
  "generate playwright tests",
  "write release notes from merged pull requests",
  "run untrusted python in an isolated environment",
];

const KIND_META: Record<DiscoveryKind, { label: string; icon: LucideIcon; color: string }> = {
  agent: { label: "Agent File", icon: Bot, color: "text-primary" },
  mcp: { label: "MCP", icon: PlugZap, color: "text-component-mcp" },
  skill: { label: "Skill", icon: BookOpenCheck, color: "text-component-skill" },
  hook: { label: "Hook", icon: GitBranch, color: "text-component-hook" },
  prompt: { label: "Prompt", icon: MessageSquareText, color: "text-component-prompt" },
  sandbox: { label: "Sandbox", icon: Box, color: "text-component-sandbox" },
  external: { label: "A2A agent", icon: Network, color: "text-primary" },
};

const KIND_OPTIONS: { value: DiscoveryKind | "all"; label: string }[] = [
  { value: "all", label: "All kinds" },
  { value: "agent", label: "Agent Files" },
  { value: "mcp", label: "MCP Clients" },
  { value: "skill", label: "Skills" },
  { value: "hook", label: "Hooks" },
  { value: "prompt", label: "Prompts" },
  { value: "sandbox", label: "Sandboxes" },
  { value: "external", label: "Remote A2A agents" },
];

const AVAILABILITY_META: Record<
  DiscoveryAvailability,
  { label: string; variant: "default" | "secondary" | "outline" | "destructive"; hint: string }
> = {
  now: { label: "Available now", variant: "default", hint: "Text resource: loads into the current session." },
  "next-session": {
    label: "Next session",
    variant: "secondary",
    hint: "Written to harness config by the install command; takes effect after a restart.",
  },
  "explicit-install": {
    label: "Explicit install",
    variant: "secondary",
    hint: "Hooks change lifecycle behaviour and are never activated implicitly.",
  },
  delegate: {
    label: "Delegate now",
    variant: "default",
    hint: "Remote A2A agent: agents hand it tasks directly. Nothing is installed.",
  },
  "not-approved": { label: "Not approved", variant: "destructive", hint: "Only approved resources load automatically." },
  archived: { label: "Archived", variant: "outline", hint: "Retired; still installable with a warning." },
  "unsupported-in-harness": {
    label: "Unsupported here",
    variant: "destructive",
    hint: "The publisher did not list the selected harness.",
  },
};

function nativeIdentity(result: DiscoverySearchResult) {
  const ref = result["obs:nativeRef"];
  if (!ref) return null;
  const [qualified] = ref.split("@", 1);
  const [namespace, slug] = qualified.split("/", 2);
  if (!namespace || !slug) return null;
  return { namespace, slug, qualified_name: qualified };
}

function CopyCommand({ command }: { command: string }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(timer);
  }, [copied]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
    } catch {
      toast.error("Could not copy to the clipboard.");
    }
  }

  return (
    <button
      type="button"
      onClick={copy}
      className="group flex w-full items-center gap-2 rounded-md border bg-muted/40 px-2.5 py-1.5 text-left font-mono text-[11px] text-muted-foreground hover:bg-muted"
      title="Copy command"
    >
      <span className="truncate">{command}</span>
      {copied ? (
        <Check className="ml-auto h-3.5 w-3.5 shrink-0 text-success" />
      ) : (
        <Copy className="ml-auto h-3.5 w-3.5 shrink-0 opacity-0 group-hover:opacity-100" />
      )}
    </button>
  );
}

function ResultCard({ result, harness }: { result: DiscoverySearchResult; harness?: string }) {
  const kind = (result["obs:kind"] ?? "skill") as DiscoveryKind;
  const meta = KIND_META[kind];
  const Icon = meta.icon;
  const availability = result["obs:availability"] ?? "next-session";
  const availabilityMeta = AVAILABILITY_META[availability];
  const identity = nativeIdentity(result);
  const detailPath = identity && kind !== "external" ? registryItemPath(identity, kind, "") : null;
  const delegable = !!result["obs:delegable"];
  // Remote agents are only ever delegated to; registry agents can be pulled or delegated to.
  const command =
    kind === "external"
      ? `observal delegate run ${result.identifier} "<task>"`
      : `observal discover use ${result.identifier}${harness ? ` --harness ${harness}` : ""}`;

  return (
    <div className="flex flex-col gap-3 rounded-lg border bg-card p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <Icon className={`h-4 w-4 ${meta.color}`} />
            {detailPath ? (
              <Link to={detailPath} className="truncate font-medium hover:underline">
                {result.displayName ?? result.identifier}
              </Link>
            ) : (
              <span className="truncate font-medium">{result.displayName ?? result.identifier}</span>
            )}
            <span className="text-xs text-muted-foreground">{meta.label}</span>
            {result.version && <span className="text-xs text-muted-foreground">v{result.version}</span>}
            {identity && <span className="truncate text-xs text-muted-foreground">{identity.qualified_name}</span>}
            {kind === "external" && (result["obs:provider"] ?? result["obs:publisher"]) && (
              <span className="truncate text-xs text-muted-foreground">
                {result["obs:provider"] ?? result["obs:publisher"]}
              </span>
            )}
          </div>
          {result.description && <p className="text-sm text-muted-foreground">{result.description}</p>}
        </div>
        <div className="shrink-0 text-right">
          <div className="text-2xl font-semibold tabular-nums">{result.score}</div>
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground">match</div>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={availabilityMeta.variant} title={availabilityMeta.hint}>
          {availabilityMeta.label}
        </Badge>
        <Badge variant="outline" className="capitalize">
          {result["obs:approval"] ?? "unknown"}
        </Badge>
        {result["obs:recommended"] && <RecommendedBadge />}
        {delegable && kind === "agent" && (
          <Badge
            variant="secondary"
            title="Running agents can hand this agent a task through the observal-agents MCP server."
          >
            Delegable
          </Badge>
        )}
        {result["obs:visibility"] && result["obs:visibility"] !== "public" && (
          <Badge variant="outline" className="capitalize">
            {result["obs:visibility"]}
          </Badge>
        )}
        <HarnessBadges supportedHarnesses={result["obs:supportedHarnesses"]} max={5} />
      </div>

      {result.matchedOn && result.matchedOn.length > 0 && (
        <p className="text-xs text-muted-foreground">
          Matched on{" "}
          {result.matchedOn.map((term, index) => (
            <span key={term}>
              {index > 0 && ", "}
              <span className="rounded bg-muted px-1 py-0.5 font-medium text-foreground">{term}</span>
            </span>
          ))}
        </p>
      )}

      <CopyCommand command={command} />
    </div>
  );
}

export default function DiscoverPage() {
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<DiscoveryKind | "all">("all");
  const [harness, setHarness] = useState<string>("any");
  const [includeUnapproved, setIncludeUnapproved] = useState(false);
  const { data: harnesses } = useHarnesses();

  useEffect(() => {
    const timer = setTimeout(() => setQuery(input), 300);
    return () => clearTimeout(timer);
  }, [input]);

  const filter = useMemo(
    () => ({
      kind: kind === "all" ? undefined : kind,
      harness: harness === "any" ? undefined : harness,
      includeUnapproved,
    }),
    [kind, harness, includeUnapproved],
  );
  const { data, isFetching, isError, error } = useDiscoverySearch(query, filter, 10);
  const results = data?.results ?? [];
  const active = query.trim().length >= 2;

  return (
    <>
      <PageHeader title="Discover" breadcrumbs={[{ label: "Registry", href: "/" }, { label: "Discover" }]} />
      <main className="min-h-0 flex-1 overflow-y-auto bg-surface-sunken/30">
        <div className="mx-auto flex w-full max-w-5xl flex-col gap-6 p-4 sm:p-6 lg:p-8">
      <div className="space-y-3">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            autoFocus
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="What are you trying to do?"
            className="h-12 pl-9 text-base"
            aria-label="Describe the task"
          />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Select value={kind} onValueChange={(value) => setKind(value as DiscoveryKind | "all")}>
            <SelectTrigger className="w-40" aria-label="Resource kind">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {KIND_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={harness} onValueChange={setHarness}>
            <SelectTrigger className="w-44" aria-label="Harness">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="any">Any harness</SelectItem>
              {(harnesses ?? []).map((entry) => (
                <SelectItem key={entry.name} value={entry.name}>
                  {entry.display_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            type="button"
            variant={includeUnapproved ? "secondary" : "outline"}
            size="sm"
            onClick={() => setIncludeUnapproved((value) => !value)}
            aria-pressed={includeUnapproved}
          >
            {includeUnapproved ? "Including my drafts" : "Approved only"}
          </Button>
          {active && (
            <span className="ml-auto text-xs text-muted-foreground">
              {isFetching ? "Searching…" : `${results.length} result${results.length === 1 ? "" : "s"}`}
            </span>
          )}
        </div>
      </div>

      {!active && (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            Search every approved Agent File, remote A2A agent, MCP Client, skill, hook, prompt and sandbox at once. Results are ranked by
            relevance; whether something is approved and whether it can be used right now are shown separately.
          </p>
          <div className="flex flex-wrap gap-2">
            {EXAMPLES.map((example) => (
              <Button key={example} type="button" variant="outline" size="sm" onClick={() => setInput(example)}>
                {example}
              </Button>
            ))}
          </div>
        </div>
      )}

      {active && isError && (
        <EmptyState
          icon={Search}
          title="Search failed"
          description={(error as Error | undefined)?.message ?? "Check your connection and try again."}
        />
      )}

      {active && !isError && !data && isFetching && <TableSkeleton rows={3} />}

      {active && !isError && data && results.length === 0 && (
        <EmptyState
          icon={Search}
          title="Nothing matches yet"
          description="Try fewer or different words, or clear the kind and harness filters. If it truly does not exist, build it and consider publishing it."
        />
      )}

      {active && results.length > 0 && (
        <div className="grid gap-3">
          {results.map((result) => (
            <ResultCard key={result.identifier} result={result} harness={harness === "any" ? undefined : harness} />
          ))}
        </div>
      )}
        </div>
      </main>
    </>
  );
}
