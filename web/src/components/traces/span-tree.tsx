// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0


import { useState, useMemo, useCallback } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PickerSelect } from "@/components/ui/picker-select";
import { cn } from "@/lib/utils";

export interface Span {
  span_id: string;
  trace_id: string;
  parent_span_id?: string | null;
  type: string;
  name: string;
  method?: string;
  input?: unknown;
  output?: unknown;
  error?: string | null;
  start_time: string;
  end_time?: string;
  latency_ms?: number;
  status: string;
  metadata?: Record<string, unknown>;
  token_input?: number;
  token_output?: number;
  tool_schema_valid?: boolean;
}

interface SpanNode {
  span: Span;
  children: SpanNode[];
}

function buildTree(spans: Span[]): SpanNode[] {
  const map = new Map<string, SpanNode>();
  const roots: SpanNode[] = [];
  for (const span of spans) {
    map.set(span.span_id, { span, children: [] });
  }
  for (const span of spans) {
    const node = map.get(span.span_id)!;
    if (span.parent_span_id && map.has(span.parent_span_id)) {
      map.get(span.parent_span_id)!.children.push(node);
    } else {
      roots.push(node);
    }
  }
  return roots;
}

function countDescendants(node: SpanNode): number {
  let count = 0;
  for (const child of node.children) {
    count += 1 + countDescendants(child);
  }
  return count;
}

// Span kinds are categorical identity — what a span *is*, not what state it is
// in — so they ride the `kind-*` token family rather than raw Tailwind palette
// classes, which had no dark-mode or preset behaviour at all (bg-purple-100 is
// the same near-white in every theme). Surfaces are derived with alpha, so a
// kind needs no separate background token.
const threadColor: Record<string, { line: string; bg: string }> = {
  tool_call:    { line: "bg-kind-tool",      bg: "bg-kind-tool/10 text-kind-tool" },
  llm:          { line: "bg-kind-response",  bg: "bg-kind-response/10 text-kind-response" },
  retrieval:    { line: "bg-kind-mcp",       bg: "bg-kind-mcp/10 text-kind-mcp" },
  sandbox_exec: { line: "bg-kind-worktree",  bg: "bg-kind-worktree/10 text-kind-worktree" },
  hook:         { line: "bg-kind-task",      bg: "bg-kind-task/10 text-kind-task" },
  prompt:       { line: "bg-kind-prompt",    bg: "bg-kind-prompt/10 text-kind-prompt" },
  lifecycle:    { line: "bg-muted-foreground", bg: "bg-muted text-muted-foreground" },
};

function isLifecycleSpan(span: Span): boolean {
  return span.input == null && span.output == null;
}

function getColors(type: string) {
  return threadColor[type] ?? { line: "bg-muted-foreground", bg: "bg-muted text-muted-foreground" };
}

function statusDot(status: string) {
  const color =
    status === "error" ? "bg-destructive" :
    status === "timeout" ? "bg-warning" :
    "bg-success";
  return <span className={cn("inline-block h-2 w-2 rounded-full shrink-0", color)} />;
}

const INDENT = 20;

function SpanRow({
  node,
  depth,
  selectedId,
  onSelect,
  collapsed,
  onToggleCollapse,
  matchingSpanIds,
}: {
  node: SpanNode;
  depth: number;
  selectedId?: string;
  onSelect: (span: Span) => void;
  collapsed: Set<string>;
  onToggleCollapse: (id: string) => void;
  matchingSpanIds: Set<string> | null;
}) {
  const hasChildren = node.children.length > 0;
  const isCollapsed = collapsed.has(node.span.span_id);
  const isSelected = selectedId === node.span.span_id;
  const isLifecycle = isLifecycleSpan(node.span);
  const colors = isLifecycle ? threadColor.lifecycle : getColors(node.span.type);
  const descendantCount = isCollapsed ? countDescendants(node) : 0;
  const tokens = (node.span.token_input ?? 0) + (node.span.token_output ?? 0);
  const isAncestorOnly = matchingSpanIds !== null && !matchingSpanIds.has(node.span.span_id);

  return (
    <>
      {/* Span row */}
      <div className="relative">
        {/* Thread lines for each ancestor depth — rendered as absolute positioned lines */}
        {Array.from({ length: depth }, (_, i) => (
          <div
            key={i}
            className="absolute top-0 bottom-0 w-0.5 bg-border"
            style={{ left: `${i * INDENT + 11}px` }}
          />
        ))}

        {/* Clickable thread line for THIS node (only if it has children) */}
        {hasChildren && (
          <div
            role="button"
            tabIndex={0}
            onClick={(e) => { e.stopPropagation(); onToggleCollapse(node.span.span_id); }}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onToggleCollapse(node.span.span_id); } }}
            className={cn(
              "absolute z-10 cursor-pointer group/line",
              isCollapsed ? "top-3 bottom-0" : "top-3 bottom-0"
            )}
            style={{ left: `${depth * INDENT + 10}px`, width: "12px" }}
            title={isCollapsed ? "Expand children" : "Collapse children"}
          >
            <div
              // The old `group-hover/line:${colors.hover}` interpolation never
              // reached Tailwind's scanner, so it compiled to nothing. Width is
              // the hover cue instead, which reads without relying on colour.
              className={cn(
                "absolute left-[5px] top-0 bottom-0 w-0.5 transition-all rounded-full",
                colors.line,
                "group-hover/line:w-[3px] group-hover/line:left-[4px]"
              )}
            />
          </div>
        )}

        <button
          onClick={() => onSelect(node.span)}
          className={cn(
            "relative flex w-full items-center gap-2 rounded-sm px-2 py-1 text-sm transition-colors hover:bg-surface-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
            isSelected && "bg-surface-raised ring-1 ring-border",
            isLifecycle && "opacity-50",
            isAncestorOnly && "opacity-40"
          )}
          style={{ paddingLeft: `${depth * INDENT + 8}px` }}
        >
          {statusDot(node.span.status)}
          <span className="truncate">{node.span.name}</span>
          {isCollapsed && (
            <span className="text-3xs text-muted-foreground whitespace-nowrap">[+{descendantCount}]</span>
          )}
          <Badge variant="outline" className={cn("ml-auto text-3xs px-1.5 py-0 shrink-0 border-transparent", colors.bg)}>
            {isLifecycle ? "lifecycle" : node.span.type}
          </Badge>
          {node.span.latency_ms != null && (
            <span className="text-xs text-muted-foreground tabular-nums shrink-0">{node.span.latency_ms}ms</span>
          )}
          {tokens > 0 && (
            <span className="text-xs text-muted-foreground tabular-nums shrink-0">{tokens}tok</span>
          )}
        </button>
      </div>

      {/* Children */}
      {hasChildren && !isCollapsed &&
        node.children.map((child) => (
          <SpanRow
            key={child.span.span_id}
            node={child}
            depth={depth + 1}
            selectedId={selectedId}
            onSelect={onSelect}
            collapsed={collapsed}
            onToggleCollapse={onToggleCollapse}
            matchingSpanIds={matchingSpanIds}
          />
        ))
      }
    </>
  );
}

interface SpanTreeProps {
  spans: Span[];
  selectedId?: string;
  onSelect: (span: Span) => void;
}

export function SpanTree({ spans, selectedId, onSelect }: SpanTreeProps) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [agentFilter, setAgentFilter] = useState("all");
  const [modelFilter, setModelFilter] = useState("all");

  const onToggleCollapse = useCallback((id: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  // Extract unique agent and model names from span metadata
  const { agentNames, modelNames } = useMemo(() => {
    const agents = new Set<string>();
    const models = new Set<string>();
    for (const span of spans) {
      const agent =
        (span.metadata?.["agent_name"] as string | undefined) ??
        (span.metadata?.["agent.name"] as string | undefined);
      if (agent) agents.add(agent);
      const model =
        (span.metadata?.["gen_ai.request.model"] as string | undefined) ??
        (span.metadata?.["model"] as string | undefined);
      if (model) models.add(model);
    }
    return {
      agentNames: Array.from(agents).sort(),
      modelNames: Array.from(models).sort(),
    };
  }, [spans]);

  const hasFilterableData = agentNames.length > 0 || modelNames.length > 0;
  const filtersActive = agentFilter !== "all" || modelFilter !== "all";

  // Build parent lookup for ancestor expansion
  const parentMap = useMemo(() => {
    const map = new Map<string, string>();
    for (const span of spans) {
      if (span.parent_span_id) {
        map.set(span.span_id, span.parent_span_id);
      }
    }
    return map;
  }, [spans]);

  // Compute matching and visible span IDs
  const { matchingSpanIds, visibleSpanIds } = useMemo(() => {
    if (!filtersActive) {
      return { matchingSpanIds: null, visibleSpanIds: null };
    }

    const matching = new Set<string>();
    for (const span of spans) {
      const agent =
        (span.metadata?.["agent_name"] as string | undefined) ??
        (span.metadata?.["agent.name"] as string | undefined);
      const model =
        (span.metadata?.["gen_ai.request.model"] as string | undefined) ??
        (span.metadata?.["model"] as string | undefined);

      const agentMatch = agentFilter === "all" || agent === agentFilter;
      const modelMatch = modelFilter === "all" || model === modelFilter;

      if (agentMatch && modelMatch) {
        matching.add(span.span_id);
      }
    }

    // Expand to include all ancestors
    const visible = new Set(matching);
    for (const id of matching) {
      let current = parentMap.get(id);
      while (current) {
        if (visible.has(current)) break;
        visible.add(current);
        current = parentMap.get(current);
      }
    }

    return { matchingSpanIds: matching, visibleSpanIds: visible };
  }, [spans, agentFilter, modelFilter, filtersActive, parentMap]);

  // Filter spans to only visible ones and build tree
  const filteredSpans = useMemo(() => {
    if (!visibleSpanIds) return spans;
    return spans.filter((s) => visibleSpanIds.has(s.span_id));
  }, [spans, visibleSpanIds]);

  const roots = useMemo(() => buildTree(filteredSpans), [filteredSpans]);

  if (spans.length === 0) {
    return <p className="p-4 text-sm text-muted-foreground">No spans</p>;
  }

  return (
    <div className="py-2">
      {hasFilterableData && (
        <div className="flex items-center gap-2 px-2 pb-2">
          {agentNames.length > 0 && (
            <PickerSelect
              value={agentFilter}
              onValueChange={setAgentFilter}
              placeholder="Agent File"
              className="w-[160px]"
              inputClassName="h-7 text-xs"
              options={[{ value: "all", label: "All Agent Files" }, ...agentNames.map((name) => ({ value: name, label: name }))]}
            />
          )}
          {modelNames.length > 0 && (
            <PickerSelect
              value={modelFilter}
              onValueChange={setModelFilter}
              placeholder="Model"
              className="w-[160px]"
              inputClassName="h-7 text-xs"
              options={[{ value: "all", label: "All models" }, ...modelNames.map((name) => ({ value: name, label: name }))]}
            />
          )}
          {filtersActive && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs"
              onClick={() => {
                setAgentFilter("all");
                setModelFilter("all");
              }}
            >
              Clear filters
            </Button>
          )}
        </div>
      )}
      {roots.length === 0 ? (
        <p className="p-4 text-sm text-muted-foreground">No spans match filters</p>
      ) : (
        roots.map((node) => (
          <SpanRow
            key={node.span.span_id}
            node={node}
            depth={0}
            selectedId={selectedId}
            onSelect={onSelect}
            collapsed={collapsed}
            onToggleCollapse={onToggleCollapse}
            matchingSpanIds={matchingSpanIds}
          />
        ))
      )}
    </div>
  );
}
