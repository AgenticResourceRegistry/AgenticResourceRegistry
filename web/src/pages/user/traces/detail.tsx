// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-FileCopyrightText: 2026 Shreem Seth <shreemseth26@gmail.com>
// SPDX-FileCopyrightText: 2026 SrihariLegend <sriharilegend23@gmail.com>
// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback, useMemo, createElement } from "react";
import { useParams } from "@tanstack/react-router";
import { useSessionDetail, useSessionSubscription } from "@/hooks/use-api";
import type {
	SessionData,
	RawSessionEvent,
	SubagentSession,
} from "@/lib/types";
import { kindChipClasses, eventTextClass } from "@/lib/kind-colors";
import { summarizeToolCalls } from "@/lib/trace-tool-usage";
import {
	FileText,
	ChevronDown,
	ChevronRight,
	ChevronsUpDown,
	Cpu,
	Wrench,
	ShieldCheck,
	MessageSquare,
	Clock,
	Zap,
	Play,
	Square,
	Globe,
	Bot,
	Search,
	Filter,
	X,
	AlertTriangle,
	Bell,
	ListChecks,
	Minimize2,
	GitBranch,
	LogIn,
	Users,
	Info,
	CheckCircle2,
	XCircle,
} from "lucide-react";
import { PageHeader } from "@/components/layouts/page-header";
import { DetailSkeleton } from "@/components/shared/skeleton-layouts";
import { ErrorState } from "@/components/shared/error-state";
import { EmptyState } from "@/components/shared/empty-state";
import { Separator } from "@/components/ui/separator";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

/* ── Helpers ─────────────────────────────────────────────── */

function Badge({
	children,
	variant = "default",
}: {
	children: React.ReactNode;
	variant?: "default" | "success" | "warning" | "muted";
}) {
	const cls = {
		default: "bg-primary/10 text-primary",
		success: "bg-success/10 text-success",
		warning: "bg-warning/10 text-warning",
		muted: "bg-muted text-muted-foreground",
	}[variant];
	return (
		<span
			className={`inline-flex items-center px-1.5 py-0.5 rounded text-2xs font-medium ${cls}`}
		>
			{children}
		</span>
	);
}

function Stat({
	label,
	value,
	icon: Icon,
}: {
	label: string;
	value: string | number;
	icon?: React.ElementType;
}) {
	return (
		<div className="flex items-center gap-1.5 text-xs">
			{Icon && <Icon className="h-3 w-3 text-muted-foreground shrink-0" />}
			<span className="text-muted-foreground">{label}</span>
			<span className="font-medium font-[family-name:var(--font-mono)] tabular-nums">
				{value}
			</span>
		</div>
	);
}

function formatDuration(ms: string | number): string {
	const n = typeof ms === "string" ? parseFloat(ms) : ms;
	if (n < 1000) return `${Math.round(n)}ms`;
	return `${(n / 1000).toFixed(1)}s`;
}

function formatTokens(n: string | number): string {
	const num = typeof n === "string" ? parseInt(n, 10) : n;
	if (num >= 1_000_000) return `${(num / 1_000_000).toFixed(1)}M`;
	if (num >= 1_000) return `${(num / 1_000).toFixed(1)}k`;
	return `${num}`;
}

function isHookEvent(eventName: string): boolean {
	return eventName.startsWith("hook_");
}

function getEventName(evt: RawSessionEvent): string {
	return evt.attributes?.["event.name"] || evt.event_name;
}

function eventIcon(eventName: string) {
	if (eventName === "api_request") return Cpu;
	if (eventName === "tool_result") return Wrench;
	if (eventName === "tool_decision") return ShieldCheck;
	if (eventName === "user_prompt" || eventName === "hook_userpromptsubmit")
		return MessageSquare;
	if (eventName === "hook_posttooluse") return Wrench;
	if (eventName === "hook_pretooluse") return ShieldCheck;
	if (eventName === "hook_posttoolusefailure") return AlertTriangle;
	if (eventName === "hook_subagentstart") return Play;
	if (eventName === "hook_subagentstop") return Square;
	if (eventName === "hook_assistant_response") return Bot;
	if (eventName === "hook_assistant_thinking") return Bot;
	if (eventName === "hook_stop") return Square;
	if (eventName === "hook_stopfailure") return AlertTriangle;
	if (eventName === "hook_sessionstart") return LogIn;
	if (eventName === "hook_notification") return Bell;
	if (eventName === "hook_taskcreated" || eventName === "hook_taskcompleted")
		return ListChecks;
	if (eventName === "hook_precompact" || eventName === "hook_postcompact")
		return Minimize2;
	if (
		eventName === "hook_worktreecreate" ||
		eventName === "hook_worktreeremove"
	)
		return GitBranch;
	if (
		eventName === "hook_elicitation" ||
		eventName === "hook_elicitationresult"
	)
		return Globe;
	if (eventName === "hook_token_usage") return Zap;
	if (isHookEvent(eventName)) return Zap;
	return FileText;
}

/**
 * Event colour, resolved through the event's filter category so the tree and
 * the filter chips always agree. Previously a ladder of raw Tailwind hues
 * (purple/cyan/indigo/...) that ignored the active preset and light/dark mode.
 * Unrecognised hook events stay muted — they are the catch-all bucket.
 */
function eventColor(eventName: string): string {
	return eventTextClass(eventName);
}

/* ── Filter categories ───────────────────────────────────── */

type FilterCategory = {
	key: string;
	label: string;
	match: (eventName: string) => boolean;
	color: string;
};

const FILTER_CATEGORIES: FilterCategory[] = [
	{
		key: "prompts",
		label: "Prompts",
		match: (e) => e === "user_prompt" || e === "hook_userpromptsubmit",
		color: kindChipClasses("prompts"),
	},
	{
		key: "responses",
		label: "Responses",
		match: (e) => e === "hook_assistant_response",
		color: kindChipClasses("responses"),
	},
	{
		key: "thinking",
		label: "Thinking",
		match: (e) => e === "hook_assistant_thinking",
		color: kindChipClasses("thinking"),
	},
	{
		key: "tools",
		label: "Tools",
		match: (e) =>
			[
				"tool_result",
				"tool_decision",
				"hook_posttooluse",
				"hook_pretooluse",
				"hook_posttoolusefailure",
				"hook_toolresult",
			].includes(e),
		color: kindChipClasses("tools"),
	},
	{
		key: "api",
		label: "API",
		match: (e) => e === "api_request",
		color: kindChipClasses("api"),
	},
	{
		key: "agents",
		label: "Agents",
		match: (e) => e === "hook_subagentstart" || e === "hook_subagentstop",
		color: kindChipClasses("agents"),
	},
	{
		key: "lifecycle",
		label: "Lifecycle",
		match: (e) =>
			[
				"hook_sessionstart",
				"hook_stop",
				"hook_stopfailure",
				"hook_precompact",
				"hook_postcompact",
			].includes(e),
		color: kindChipClasses("lifecycle"),
	},
	{
		key: "tasks",
		label: "Tasks",
		match: (e) => e === "hook_taskcreated" || e === "hook_taskcompleted",
		color: kindChipClasses("tasks"),
	},
	{
		key: "mcp",
		label: "MCP",
		match: (e) => e === "hook_elicitation" || e === "hook_elicitationresult",
		color: kindChipClasses("mcp"),
	},
	{
		key: "errors",
		label: "Errors",
		match: (e) => e === "hook_posttoolusefailure" || e === "hook_stopfailure",
		color: kindChipClasses("errors"),
	},
	{
		key: "notifications",
		label: "Notifications",
		match: (e) => e === "hook_notification",
		color: kindChipClasses("notifications"),
	},
	{
		key: "worktree",
		label: "Worktrees",
		match: (e) => e === "hook_worktreecreate" || e === "hook_worktreeremove",
		color: kindChipClasses("worktree"),
	},
];

/* ── Tree data structures ────────────────────────────────── */

interface AgentScope {
	agentId: string;
	agentType: string;
	startEvent?: RawSessionEvent;
	stopEvent?: RawSessionEvent;
	events: RawSessionEvent[];
}

interface Turn {
	promptEvent?: RawSessionEvent; // The user prompt that started this turn
	responseEvent?: RawSessionEvent; // The assistant response text
	thinkingEvents: RawSessionEvent[]; // Assistant thinking/reasoning blocks
	stopEvent?: RawSessionEvent; // The stop/end event
	topLevelEvents: RawSessionEvent[]; // Events not inside any subagent
	agents: AgentScope[]; // Subagent scopes with their events
	allEvents: RawSessionEvent[]; // All events in this turn (for counting)
}

/* ── Dedup + Tree builder ────────────────────────────────── */

function deduplicateEvents(events: RawSessionEvent[]): RawSessionEvent[] {
	// Check if we have hook data at all
	const hasHooks = events.some((e) => isHookEvent(getEventName(e)));
	if (!hasHooks) return events;

	// OTEL tool_result events have rich metadata (duration_ms, success,
	// tool_result_size_bytes) but NO tool_use_id. Hook PostToolUse events have
	// rich content (tool_input, tool_response) and a tool_use_id.
	// Merge strategy: match by tool_name + timestamp proximity (within 50ms).

	// Index OTEL tool_result events by tool_name for proximity matching
	const sessionToolResults: {
		ts: number;
		attrs: Record<string, string>;
		matched: boolean;
	}[] = [];
	for (const evt of events) {
		const eName = getEventName(evt);
		if (eName === "tool_result") {
			sessionToolResults.push({
				ts: new Date(evt.timestamp).getTime(),
				attrs: evt.attributes ?? {},
				matched: false,
			});
		}
	}

	const result: RawSessionEvent[] = [];
	for (const evt of events) {
		const eName = getEventName(evt);

		// Internal metadata events — consumed by the stats block, never displayed.
		if (eName === "kiro_credits") continue;

		// Drop ALL user_prompt events when hooks are present.
		// hook_userpromptsubmit carries the same (or richer) data.
		// Legacy hook events stored before the event-name fix also resolved
		// as "user_prompt" with prompt_length/tool_name set — keeping those
		// caused duplicate turn boundaries alongside the properly-named
		// hook_userpromptsubmit event for the same prompt.
		if (eName === "user_prompt" && hasHooks) {
			continue;
		}

		// Drop OTEL tool_decision / tool_result — their metadata gets merged into hooks below
		if ((eName === "tool_decision" || eName === "tool_result") && hasHooks)
			continue;

		// For hook PostToolUse, find the closest OTEL tool_result with same tool_name
		// and merge its metadata (duration_ms, success, tool_result_size_bytes)
		if (eName === "hook_posttooluse") {
			const hookTs = new Date(evt.timestamp).getTime();
			const hookTool = evt.attributes?.tool_name ?? "";

			let bestIdx = -1;
			let bestDiff = Infinity;
			for (let i = 0; i < sessionToolResults.length; i++) {
				const o = sessionToolResults[i];
				if (o.matched) continue;
				if (o.attrs.tool_name !== hookTool) continue;
				const diff = Math.abs(o.ts - hookTs);
				if (diff < bestDiff && diff <= 50) {
					bestDiff = diff;
					bestIdx = i;
				}
			}

			if (bestIdx >= 0) {
				sessionToolResults[bestIdx].matched = true;
				const sessionAttrs = sessionToolResults[bestIdx].attrs;
				// Merge OTEL fields the hook doesn't have (don't overwrite hook's richer content)
				const merged = { ...evt.attributes };
				for (const [k, v] of Object.entries(sessionAttrs)) {
					if (!merged[k] || merged[k] === "") merged[k] = v;
				}
				result.push({ ...evt, attributes: merged });
				continue;
			}
		}

		result.push(evt);
	}

	return result;
}

function buildEventTree(events: RawSessionEvent[]): {
	turns: Turn[];
	preSessionEvents: RawSessionEvent[];
} {
	const deduped = deduplicateEvents(events);
	const turns: Turn[] = [];
	const preSessionEvents: RawSessionEvent[] = [];

	let currentTurn: Turn | null = null;
	// Track open agent scopes by agent_id
	const openAgents = new Map<string, AgentScope>();

	for (const evt of deduped) {
		const eName = getEventName(evt);
		const attrs = evt.attributes ?? {};

		// Turn boundary: new prompt starts a turn
		if (eName === "hook_userpromptsubmit" || eName === "user_prompt") {
			// Close previous turn if open
			if (currentTurn) {
				turns.push(currentTurn);
				openAgents.clear();
			}
			currentTurn = {
				promptEvent: evt,
				thinkingEvents: [],
				topLevelEvents: [],
				agents: [],
				allEvents: [evt],
			};
			continue;
		}

		// If no turn started yet, these are pre-session events (session start, etc.)
		if (!currentTurn) {
			preSessionEvents.push(evt);
			continue;
		}

		currentTurn.allEvents.push(evt);

		// SubagentStart: open an agent scope
		if (eName === "hook_subagentstart") {
			const agentId = attrs.agent_id || `agent-${currentTurn.agents.length}`;
			const scope: AgentScope = {
				agentId,
				agentType: attrs.agent_type || "agent",
				startEvent: evt,
				events: [],
			};
			openAgents.set(agentId, scope);
			currentTurn.agents.push(scope);
			continue;
		}

		// SubagentStop: close the agent scope
		if (eName === "hook_subagentstop") {
			const agentId = attrs.agent_id || "";
			const scope = openAgents.get(agentId);
			if (scope) {
				scope.stopEvent = evt;
				openAgents.delete(agentId);
			} else {
				// Unmatched stop — add to top level
				currentTurn.topLevelEvents.push(evt);
			}
			continue;
		}

		// Assistant thinking: collect on the turn
		if (eName === "hook_assistant_thinking") {
			currentTurn.thinkingEvents.push(evt);
			continue;
		}

		// Assistant response: mark on the turn
		if (eName === "hook_assistant_response") {
			currentTurn.responseEvent = evt;
			continue;
		}

		// Stop events: mark turn end
		if (eName === "hook_stop" || eName === "hook_stopfailure") {
			currentTurn.stopEvent = evt;
			// Kiro sends assistant_response inside the stop event (no separate
			// hook_assistant_response). Promote it to responseEvent so the UI
			// renders the response text.
			if (!currentTurn.responseEvent && attrs.tool_response) {
				currentTurn.responseEvent = evt;
			}
			continue;
		}

		// Regular events: check if they belong to an open agent scope
		const evtAgentId = attrs.agent_id;
		if (evtAgentId && openAgents.has(evtAgentId)) {
			openAgents.get(evtAgentId)!.events.push(evt);
		} else {
			currentTurn.topLevelEvents.push(evt);
		}
	}

	// Push final turn
	if (currentTurn) turns.push(currentTurn);

	return { turns, preSessionEvents };
}

/* ── Search helper ───────────────────────────────────────── */

function eventMatchesSearch(evt: RawSessionEvent, q: string): boolean {
	const attrs = evt.attributes ?? {};
	const eName = getEventName(evt);
	return (
		eName.toLowerCase().includes(q) ||
		(evt.body || "").toLowerCase().includes(q) ||
		(attrs.tool_name || "").toLowerCase().includes(q) ||
		(attrs.tool_input || "").toLowerCase().includes(q) ||
		(attrs.tool_response || "").toLowerCase().includes(q) ||
		(attrs.agent_type || "").toLowerCase().includes(q) ||
		(attrs.agent_id || "").toLowerCase().includes(q) ||
		(attrs.error || "").toLowerCase().includes(q) ||
		(attrs.task_subject || "").toLowerCase().includes(q)
	);
}

function eventMatchesFilter(
	evt: RawSessionEvent,
	activeFilters: Set<string>,
): boolean {
	if (activeFilters.size === 0) return true;
	const eName = getEventName(evt);
	const activeCategories = FILTER_CATEGORIES.filter((c) =>
		activeFilters.has(c.key),
	);
	return activeCategories.some((cat) => cat.match(eName));
}

function turnMatchesFilters(
	turn: Turn,
	activeFilters: Set<string>,
	searchQuery: string,
): boolean {
	const q = searchQuery.toLowerCase();
	return turn.allEvents.some((evt) => {
		if (!eventMatchesFilter(evt, activeFilters)) return false;
		if (q && !eventMatchesSearch(evt, q)) return false;
		return true;
	});
}

function filterTurnEvents(
	events: RawSessionEvent[],
	activeFilters: Set<string>,
	searchQuery: string,
): RawSessionEvent[] {
	const q = searchQuery.toLowerCase();
	return events.filter((evt) => {
		if (!eventMatchesFilter(evt, activeFilters)) return false;
		if (q && !eventMatchesSearch(evt, q)) return false;
		return true;
	});
}

/* ── Event inline summary (shown without expanding) ────── */

function EventSummary({ event }: { event: RawSessionEvent }) {
	const attrs = event.attributes ?? {};
	const eName = getEventName(event);

	if (eName === "api_request") {
		return (
			<div className="flex items-center gap-3 flex-wrap">
				<Badge>{attrs.model || "?"}</Badge>
				{attrs.duration_ms && (
					<Stat
						label=""
						value={formatDuration(attrs.duration_ms)}
						icon={Clock}
					/>
				)}
				{attrs.input_tokens && parseInt(attrs.input_tokens) > 1 && (
					<Stat label="in" value={formatTokens(attrs.input_tokens)} />
				)}
				{attrs.output_tokens && (
					<Stat label="out" value={formatTokens(attrs.output_tokens)} />
				)}
				{attrs.cache_read_tokens && parseInt(attrs.cache_read_tokens) > 0 && (
					<Stat
						label="cache read"
						value={formatTokens(attrs.cache_read_tokens)}
					/>
				)}
				{attrs.cache_creation_tokens &&
					parseInt(attrs.cache_creation_tokens) > 0 && (
						<Stat
							label="cache write"
							value={formatTokens(attrs.cache_creation_tokens)}
						/>
					)}
			</div>
		);
	}

	if (eName === "tool_result") {
		const success = attrs.success === "true";
		return (
			<div className="flex items-center gap-3 flex-wrap">
				<Badge variant={success ? "success" : "warning"}>
					{attrs.tool_name || "?"}
				</Badge>
				{attrs.duration_ms && (
					<Stat
						label=""
						value={formatDuration(attrs.duration_ms)}
						icon={Clock}
					/>
				)}
				{!success && <Badge variant="warning">failed</Badge>}
			</div>
		);
	}

	if (eName === "tool_decision") {
		const accepted = attrs.decision === "accept";
		return (
			<div className="flex items-center gap-3 flex-wrap">
				<Badge variant={accepted ? "muted" : "warning"}>
					{attrs.tool_name || "?"}
				</Badge>
				<span className="text-xs text-muted-foreground">
					{accepted ? "accepted" : "rejected"}
				</span>
			</div>
		);
	}

	if (eName === "hook_posttooluse" || eName === "hook_pretooluse") {
		const success =
			attrs.success !== undefined ? attrs.success === "true" : undefined;
		const hasMcp = !!attrs.mcp_id;
		const schemaValid = attrs.tool_schema_valid === "1";
		return (
			<div className="flex items-center gap-3 flex-wrap">
				<Badge
					variant={
						success === false
							? "warning"
							: eName === "hook_posttooluse"
								? "success"
								: "muted"
					}
				>
					{attrs.tool_name || "?"}
				</Badge>
				{hasMcp && (
					<span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-2xs font-medium bg-kind-mcp/10 text-kind-mcp">
						<Globe className="h-3 w-3" />
						{attrs.mcp_id}
					</span>
				)}
				{attrs.duration_ms && (
					<Stat
						label=""
						value={formatDuration(attrs.duration_ms)}
						icon={Clock}
					/>
				)}
				{attrs.mcp_latency_ms && (
					<Stat
						label="MCP"
						value={formatDuration(attrs.mcp_latency_ms)}
						icon={Clock}
					/>
				)}
				{hasMcp && (
					<span
						className={`inline-flex items-center gap-0.5 text-2xs font-medium ${schemaValid ? "text-success" : "text-destructive"}`}
					>
						{schemaValid ? (
							<CheckCircle2 className="h-3 w-3" />
						) : (
							<XCircle className="h-3 w-3" />
						)}
						schema
					</span>
				)}
				{attrs.tool_result_size_bytes && (
					<Stat
						label="size"
						value={`${formatTokens(attrs.tool_result_size_bytes)}B`}
					/>
				)}
				{success === false && <Badge variant="warning">failed</Badge>}
			</div>
		);
	}

	if (eName === "hook_posttoolusefailure") {
		return (
			<div className="flex items-center gap-3 flex-wrap">
				<Badge variant="warning">{attrs.tool_name || "?"}</Badge>
				<span className="text-xs text-destructive">failed</span>
				{attrs.error && (
					<span className="text-xs text-muted-foreground truncate max-w-md">
						{attrs.error.slice(0, 80)}
					</span>
				)}
			</div>
		);
	}

	if (eName === "hook_stopfailure") {
		return (
			<div className="flex items-center gap-3 flex-wrap">
				<Badge variant="warning">API error</Badge>
				{attrs.error && (
					<span className="text-xs text-destructive truncate max-w-md">
						{attrs.error.slice(0, 80)}
					</span>
				)}
			</div>
		);
	}

	if (eName === "hook_sessionstart") {
		return (
			<Badge variant="success">
				{attrs.session_resumed === "True" ? "resumed" : "new session"}
			</Badge>
		);
	}

	if (eName === "hook_notification") {
		return (
			<div className="flex items-center gap-3 flex-wrap">
				{attrs.notification_title && <Badge>{attrs.notification_title}</Badge>}
				{attrs.tool_response && (
					<span className="text-xs text-muted-foreground truncate max-w-md">
						{attrs.tool_response.slice(0, 80)}
					</span>
				)}
			</div>
		);
	}

	if (eName === "hook_taskcreated" || eName === "hook_taskcompleted") {
		return (
			<div className="flex items-center gap-3 flex-wrap">
				<Badge variant={eName === "hook_taskcompleted" ? "success" : "default"}>
					{attrs.task_subject || attrs.task_id || "task"}
				</Badge>
			</div>
		);
	}

	if (eName === "hook_precompact" || eName === "hook_postcompact") {
		return (
			<Badge variant="muted">
				{eName === "hook_precompact" ? "compacting" : "compacted"}
			</Badge>
		);
	}

	if (eName === "hook_worktreecreate" || eName === "hook_worktreeremove") {
		return (
			<div className="flex items-center gap-3 flex-wrap">
				<Badge>{attrs.branch || "worktree"}</Badge>
				<span className="text-xs text-muted-foreground">
					{eName === "hook_worktreecreate" ? "created" : "removed"}
				</span>
			</div>
		);
	}

	if (eName === "hook_elicitation" || eName === "hook_elicitationresult") {
		return (
			<div className="flex items-center gap-3 flex-wrap">
				<Badge>{attrs.mcp_server_name || "MCP"}</Badge>
				<span className="text-xs text-muted-foreground">
					{eName === "hook_elicitation" ? "ask" : "reply"}
				</span>
			</div>
		);
	}

	if (isHookEvent(eName)) {
		return (
			<div className="flex items-center gap-3 flex-wrap">
				<Badge>
					{attrs.tool_name || attrs.agent_type || eName.replace("hook_", "")}
				</Badge>
			</div>
		);
	}

	return null;
}

/* ── Pretty JSON / content block ──────────────────────── */

/* ── Simple line diff (no external dep) ─────────────────── */

function computeLineDiff(
	oldStr: string,
	newStr: string,
): { type: "same" | "add" | "remove"; text: string }[] {
	const oldLines = oldStr.split("\n");
	const newLines = newStr.split("\n");

	// Simple LCS-based diff for reasonable sizes (< 500 lines each)
	// For very large diffs, fall back to showing old/new blocks
	if (oldLines.length > 500 || newLines.length > 500) {
		return [
			...oldLines.map((l) => ({ type: "remove" as const, text: l })),
			...newLines.map((l) => ({ type: "add" as const, text: l })),
		];
	}

	const m = oldLines.length;
	const n = newLines.length;
	// Build LCS table
	const dp: number[][] = Array.from({ length: m + 1 }, () =>
		Array(n + 1).fill(0),
	);
	for (let i = 1; i <= m; i++) {
		for (let j = 1; j <= n; j++) {
			dp[i][j] =
				oldLines[i - 1] === newLines[j - 1]
					? dp[i - 1][j - 1] + 1
					: Math.max(dp[i - 1][j], dp[i][j - 1]);
		}
	}
	// Backtrack
	const result: { type: "same" | "add" | "remove"; text: string }[] = [];
	let i = m,
		j = n;
	while (i > 0 || j > 0) {
		if (i > 0 && j > 0 && oldLines[i - 1] === newLines[j - 1]) {
			result.push({ type: "same", text: oldLines[i - 1] });
			i--;
			j--;
		} else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
			result.push({ type: "add", text: newLines[j - 1] });
			j--;
		} else {
			result.push({ type: "remove", text: oldLines[i - 1] });
			i--;
		}
	}
	result.reverse();
	return result;
}

function DiffBlock({
	filePath,
	oldStr,
	newStr,
}: {
	filePath: string;
	oldStr: string;
	newStr: string;
}) {
	const [showFull, setShowFull] = useState(false);
	const diff = useMemo(() => computeLineDiff(oldStr, newStr), [oldStr, newStr]);
	const changedCount = diff.filter((d) => d.type !== "same").length;
	const addCount = diff.filter((d) => d.type === "add").length;
	const removeCount = diff.filter((d) => d.type === "remove").length;

	// Collapse to context: show 3 lines around changes
	const contextLines = 3;
	const visible = showFull
		? diff
		: diff.reduce<((typeof diff)[0] | { type: "collapse"; count: number })[]>(
				(acc, line, idx) => {
					const nearChange = diff
						.slice(
							Math.max(0, idx - contextLines),
							Math.min(diff.length, idx + contextLines + 1),
						)
						.some((d) => d.type !== "same");
					if (nearChange || line.type !== "same") {
						acc.push(line);
					} else {
						const prev = acc[acc.length - 1];
						if (prev && "count" in prev) {
							prev.count++;
						} else {
							acc.push({ type: "collapse", count: 1 });
						}
					}
					return acc;
				},
				[],
			);

	// Line numbers
	let oldLine = 1;
	let newLine = 1;

	return (
		<div className="space-y-1">
			<div className="flex items-center gap-2">
				<span className="text-2xs font-medium text-muted-foreground uppercase tracking-wide">
					Diff
				</span>
				<span className="text-2xs font-[family-name:var(--font-mono)] text-muted-foreground">
					{filePath.split("/").pop()}
				</span>
				<span className="text-2xs text-success">+{addCount}</span>
				<span className="text-2xs text-destructive">-{removeCount}</span>
			</div>
			<div className="text-xs font-[family-name:var(--font-mono)] border border-border rounded-md overflow-hidden max-h-[400px] overflow-auto">
				{visible.map((line, idx) => {
					if ("count" in line) {
						return (
							<div
								key={idx}
								className="px-2 py-0.5 text-muted-foreground bg-muted/30 text-center text-2xs"
							>
								··· {line.count} unchanged lines ···
							</div>
						);
					}
					const bgCls =
						line.type === "add"
							? "bg-success/10"
							: line.type === "remove"
								? "bg-destructive/10"
								: "";
					const textCls =
						line.type === "add"
							? "text-success"
							: line.type === "remove"
								? "text-destructive"
								: "text-foreground/60";
					const prefix =
						line.type === "add" ? "+" : line.type === "remove" ? "-" : " ";
					const ln =
						line.type === "remove"
							? oldLine
							: line.type === "add"
								? newLine
								: oldLine;
					if (line.type === "remove" || line.type === "same") oldLine++;
					if (line.type === "add" || line.type === "same") newLine++;
					return (
						<div key={idx} className={`flex ${bgCls} hover:brightness-95`}>
							<span className="w-8 shrink-0 text-right pr-1 text-muted-foreground/40 select-none">
								{ln}
							</span>
							<span className={`px-1 select-none ${textCls}`}>{prefix}</span>
							<span className={`whitespace-pre-wrap break-all ${textCls}`}>
								{line.text || " "}
							</span>
						</div>
					);
				})}
			</div>
			{changedCount > 0 && !showFull && diff.length > 20 && (
				<button
					type="button"
					onClick={() => setShowFull(true)}
					className="text-2xs text-primary-accent hover:underline"
				>
					Show full diff ({diff.length} lines)
				</button>
			)}
			{showFull && (
				<button
					type="button"
					onClick={() => setShowFull(false)}
					className="text-2xs text-primary-accent hover:underline"
				>
					Show context only
				</button>
			)}
		</div>
	);
}

function ContentBlock({ label, content }: { label: string; content: string }) {
	let display = content;
	let isJson = false;
	try {
		const parsed = JSON.parse(content);
		display = JSON.stringify(parsed, null, 2);
		isJson = true;
	} catch {
		/* not JSON */
	}

	const lines = display.split("\n").length;
	const isLong = lines > 20;
	const [showFull, setShowFull] = useState(false);
	const shown =
		isLong && !showFull
			? display.split("\n").slice(0, 20).join("\n") + "\n..."
			: display;

	return (
		<div className="space-y-1">
			<span className="text-2xs font-medium text-muted-foreground uppercase tracking-wide">
				{label}
			</span>
			<pre
				className={`text-xs font-[family-name:var(--font-mono)] whitespace-pre-wrap break-all bg-background/50 border border-border rounded-md p-2.5 max-h-[400px] overflow-auto ${isJson ? "text-foreground" : "text-foreground/80"}`}
			>
				{shown}
			</pre>
			{isLong && (
				<button
					type="button"
					onClick={() => setShowFull(!showFull)}
					className="text-2xs text-primary-accent hover:underline"
				>
					{showFull ? "Show less" : `Show all ${lines} lines`}
				</button>
			)}
		</div>
	);
}

/* ── Event detail (shown when expanded) ────────────────── */

function EventDetail({ event }: { event: RawSessionEvent }) {
	const attrs = event.attributes ?? {};
	const eName = getEventName(event);

	if (isHookEvent(eName) && (attrs.tool_input || attrs.tool_response)) {
		// Try to render a diff view for Edit tool events
		const toolName = attrs.tool_name ?? "";
		let diffView: { filePath: string; oldStr: string; newStr: string } | null =
			null;

		if (toolName === "Edit" && attrs.tool_input) {
			try {
				const parsed = JSON.parse(attrs.tool_input);
				if (parsed.old_string && parsed.new_string) {
					diffView = {
						filePath: parsed.file_path || "unknown",
						oldStr: parsed.old_string,
						newStr: parsed.new_string,
					};
				}
			} catch {
				/* not valid JSON, fall through */
			}
		}

		return (
			<div className="ml-6 mr-3 mb-2 mt-1 space-y-3">
				{diffView ? (
					<DiffBlock
						filePath={diffView.filePath}
						oldStr={diffView.oldStr}
						newStr={diffView.newStr}
					/>
				) : (
					<>
						{attrs.tool_input && (
							<ContentBlock label="Input" content={attrs.tool_input} />
						)}
						{attrs.tool_response && (
							<ContentBlock label="Response" content={attrs.tool_response} />
						)}
					</>
				)}
				<HookMetaGrid attrs={attrs} />
			</div>
		);
	}

	const skip = new Set([
		"event.name",
		"event.sequence",
		"event.timestamp",
		"session.id",
		"user.id",
		"terminal.type",
		"prompt.id",
	]);
	const entries = Object.entries(attrs)
		.filter(([k]) => !skip.has(k))
		.sort(([a], [b]) => a.localeCompare(b));
	if (entries.length === 0) return null;

	return (
		<div className="ml-6 mr-3 mb-2 mt-1">
			<div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 text-xs font-[family-name:var(--font-mono)] bg-surface-sunken rounded-md p-3">
				{entries.map(([key, value]) => (
					<div key={key} className="contents">
						<span className="text-muted-foreground">{key}</span>
						<span className="text-foreground truncate">{value}</span>
					</div>
				))}
			</div>
		</div>
	);
}

const MCP_FIELD_LABELS: Record<string, string> = {
	mcp_id: "MCP Client",
	mcp_method: "Method",
	mcp_latency_ms: "MCP Latency",
	tool_schema_valid: "Schema Valid",
	tools_available: "Tools Available",
	mcp_status: "MCP Status",
	mcp_span_id: "MCP Span ID",
	mcp_trace_id: "MCP Trace ID",
	_sources: "Data Sources",
	source: "Source",
};

function HookMetaGrid({ attrs }: { attrs: Record<string, string> }) {
	const skip = new Set([
		"event.name",
		"session.id",
		"tool_input",
		"tool_response",
		"hook_event",
		"tool_name",
		"mcp_input",
		"mcp_output",
		"mcp_error",
	]);
	const entries = Object.entries(attrs)
		.filter(([k]) => !skip.has(k))
		.sort(([a], [b]) => a.localeCompare(b));
	if (entries.length === 0) return null;
	return (
		<div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 text-xs font-[family-name:var(--font-mono)] bg-surface-sunken rounded-md p-2">
			{entries.map(([key, value]) => {
				const label = MCP_FIELD_LABELS[key] || key;
				const isMcpField =
					key.startsWith("mcp_") ||
					key === "tool_schema_valid" ||
					key === "tools_available" ||
					key === "_sources";
				return (
					<div key={key} className="contents">
						<span
							className={
								isMcpField
									? "text-kind-mcp"
									: "text-muted-foreground"
							}
						>
							{label}
						</span>
						<span className="text-foreground truncate">
							{key === "tool_schema_valid"
								? value === "1"
									? "yes"
									: "no"
								: value}
							{key === "mcp_latency_ms" ? "ms" : ""}
						</span>
					</div>
				);
			})}
		</div>
	);
}

/* ── Assistant response block ──────────────────────────────── */

function AssistantResponseBlock({ event }: { event: RawSessionEvent }) {
	const [expanded, setExpanded] = useState(false);
	const attrs = event.attributes ?? {};
	const fullResponse = attrs.tool_response || "";
	const seq = attrs.message_sequence;
	const total = attrs.message_total;
	const seqLabel = seq && total ? ` (${seq}/${total})` : "";
	const lines = fullResponse.split("\n");
	const isLong = lines.length > 15;
	const shown =
		isLong && !expanded ? lines.slice(0, 15).join("\n") + "\n…" : fullResponse;

	if (!fullResponse) return null;

	return (
		<div className="mx-3 mt-2 mb-3 rounded-md border border-kind-response/20 bg-kind-response/5 overflow-hidden">
			<div className="flex items-center gap-1.5 px-3 py-1.5 border-b border-kind-response/10">
				<Bot className="h-3 w-3 text-kind-response" />
				<span className="text-2xs font-medium text-kind-response">
					Assistant Response{seqLabel}
				</span>
				{event.timestamp && (
					<span className="ml-auto text-2xs text-muted-foreground tabular-nums">
						{new Date(event.timestamp).toLocaleTimeString()}
					</span>
				)}
			</div>
			<div className="px-3 py-2.5 text-sm text-foreground whitespace-pre-wrap break-words leading-relaxed max-h-[400px] overflow-auto">
				{shown}
			</div>
			{isLong && (
				<button
					type="button"
					onClick={() => setExpanded(!expanded)}
					className="w-full text-center py-1 text-2xs text-kind-response hover:underline border-t border-kind-response/10"
				>
					{expanded ? "Show less" : `Show all ${lines.length} lines`}
				</button>
			)}
		</div>
	);
}

/* ── Thinking block (chain-of-thought) ────────────────────── */

function ThinkingBlock({ event }: { event: RawSessionEvent }) {
	const [expanded, setExpanded] = useState(false);
	const attrs = event.attributes ?? {};
	const fullText = attrs.tool_response || "";
	const seq = attrs.message_sequence;
	const total = attrs.message_total;
	const seqLabel = seq && total ? ` (${seq}/${total})` : "";
	const lines = fullText.split("\n");
	const isLong = lines.length > 8;
	const shown =
		isLong && !expanded ? lines.slice(0, 8).join("\n") + "\n…" : fullText;

	if (!fullText) return null;

	return (
		<div className="mx-3 mt-2 mb-1 rounded-md border border-kind-thinking/20 bg-kind-thinking/5 overflow-hidden">
			<div className="flex items-center gap-1.5 px-3 py-1.5 border-b border-kind-thinking/10">
				<Bot className="h-3 w-3 text-kind-thinking" />
				<span className="text-2xs font-medium text-kind-thinking">
					Thinking{seqLabel}
				</span>
				{event.timestamp && (
					<span className="ml-auto text-2xs text-muted-foreground tabular-nums">
						{new Date(event.timestamp).toLocaleTimeString()}
					</span>
				)}
			</div>
			<div className="px-3 py-2 text-sm text-foreground/80 whitespace-pre-wrap break-words leading-relaxed max-h-[300px] overflow-auto italic">
				{shown}
			</div>
			{isLong && (
				<button
					type="button"
					onClick={() => setExpanded(!expanded)}
					className="w-full text-center py-1 text-2xs text-kind-thinking hover:underline border-t border-kind-thinking/10"
				>
					{expanded ? "Show less" : `Show all ${lines.length} lines`}
				</button>
			)}
		</div>
	);
}

/* ── Friendly event label ────────────────────────────────── */

function eventLabel(evt: RawSessionEvent): string {
	const eName = getEventName(evt);
	const attrs = evt.attributes ?? {};
	if (eName === "hook_posttooluse" || eName === "hook_pretooluse")
		return attrs.tool_name || "tool";
	if (eName === "hook_posttoolusefailure")
		return attrs.tool_name || "tool fail";
	if (eName === "hook_taskcreated") return "task new";
	if (eName === "hook_taskcompleted") return "task done";
	if (eName === "hook_precompact") return "compact";
	if (eName === "hook_postcompact") return "compacted";
	if (eName === "hook_worktreecreate") return "worktree+";
	if (eName === "hook_worktreeremove") return "worktree-";
	if (eName === "hook_elicitation") return "MCP ask";
	if (eName === "hook_elicitationresult") return "MCP reply";
	if (eName === "hook_notification") return "notify";
	if (eName === "hook_sessionstart") return "session";
	if (eName === "hook_userpromptsubmit") return "prompt";
	if (eName === "hook_assistant_response") return "response";
	if (eName === "hook_assistant_thinking") return "thinking";
	if (eName === "hook_subagentstart") return attrs.agent_type || "agent+";
	if (eName === "hook_subagentstop") return attrs.agent_type || "agent-";
	if (eName === "hook_stop") return attrs.stop_reason || "end_turn";
	if (isHookEvent(eName)) return attrs.tool_name || eName.replace("hook_", "");
	return eName;
}

/* ── Leaf event row (used inside tree) ───────────────────── */

function LeafEvent({
	event,
	isExpanded,
	onToggle,
	depth = 0,
}: {
	event: RawSessionEvent;
	isExpanded: boolean;
	onToggle: () => void;
	depth?: number;
}) {
	const eName = getEventName(event);
	const attrs = event.attributes ?? {};
	const icon = eventIcon(eName);
	const color = eventColor(eName);

	return (
		<div>
			<button
				type="button"
				onClick={onToggle}
				className="flex items-center gap-2 w-full text-left py-1.5 px-3 rounded-md hover:bg-muted/50 transition-colors"
				style={{ paddingLeft: `${12 + depth * 20}px` }}
				aria-expanded={isExpanded}
			>
				{isExpanded ? (
					<ChevronDown className="h-3 w-3 text-muted-foreground shrink-0" />
				) : (
					<ChevronRight className="h-3 w-3 text-muted-foreground shrink-0" />
				)}
				{createElement(icon, { className: `h-3.5 w-3.5 shrink-0 ${color}` })}
				<span className="text-xs font-medium w-24 shrink-0 truncate">
					{eventLabel(event)}
				</span>
				{attrs.agent_id && (
					<span className="text-2xs px-1 py-0.5 rounded bg-kind-agent/10 text-kind-agent font-medium shrink-0">
						{attrs.agent_type || "agent"}
					</span>
				)}
				<div className="flex-1 min-w-0">
					<EventSummary event={event} />
				</div>
				{event.timestamp && (
					<span className="ml-auto text-2xs text-muted-foreground tabular-nums shrink-0 pl-2">
						{new Date(event.timestamp).toLocaleTimeString()}
					</span>
				)}
			</button>
			{isExpanded && (
				<div style={{ paddingLeft: `${depth * 20}px` }}>
					<EventDetail event={event} />
				</div>
			)}
		</div>
	);
}

/* ── Agent scope node (collapsible sub-tree) ─────────────── */

function AgentNode({
	agent,
	expandedSet,
	onToggleEvent,
	activeFilters,
	searchQuery,
	depth = 1,
}: {
	agent: AgentScope;
	expandedSet: Set<string>;
	onToggleEvent: (key: string) => void;
	activeFilters: Set<string>;
	searchQuery: string;
	depth?: number;
}) {
	const nodeKey = `agent-${agent.agentId}`;
	const isOpen = expandedSet.has(nodeKey);
	const filtered = filterTurnEvents(agent.events, activeFilters, searchQuery);

	// Compute agent-level token stats
	const agentTokens = useMemo(() => {
		let inputTokens = 0;
		let outputTokens = 0;
		let toolCount = 0;
		for (const evt of agent.events) {
			const en = getEventName(evt);
			const a = evt.attributes ?? {};
			if (en === "api_request") {
				inputTokens += parseInt(a.input_tokens || "0", 10);
				outputTokens += parseInt(a.output_tokens || "0", 10);
			}
			if (en === "hook_posttooluse") toolCount++;
		}
		return { inputTokens, outputTokens, toolCount };
	}, [agent.events]);

	if (filtered.length === 0 && activeFilters.size > 0) return null;

	return (
		<div>
			<button
				type="button"
				onClick={() => onToggleEvent(nodeKey)}
				className="flex items-center gap-2 w-full text-left py-1.5 px-3 rounded-md hover:bg-kind-agent/5 transition-colors"
				style={{ paddingLeft: `${12 + depth * 20}px` }}
				aria-expanded={isOpen}
			>
				{isOpen ? (
					<ChevronDown className="h-3.5 w-3.5 text-kind-agent shrink-0" />
				) : (
					<ChevronRight className="h-3.5 w-3.5 text-kind-agent shrink-0" />
				)}
				<Users className="h-3.5 w-3.5 text-kind-agent shrink-0" />
				<span className="text-xs font-semibold text-kind-agent">
					{agent.agentType}
				</span>
				<div className="flex items-center gap-1.5">
					{agentTokens.toolCount > 0 && (
						<Badge variant="muted">
							<Wrench className="h-2.5 w-2.5 mr-0.5 inline" />
							{agentTokens.toolCount}
						</Badge>
					)}
					{agentTokens.inputTokens > 0 && (
						<Badge variant="muted">
							{formatTokens(agentTokens.inputTokens)} in
						</Badge>
					)}
					{agentTokens.outputTokens > 0 && (
						<Badge variant="muted">
							{formatTokens(agentTokens.outputTokens)} out
						</Badge>
					)}
				</div>
				{agent.startEvent?.timestamp && (
					<span className="ml-auto text-2xs text-muted-foreground tabular-nums shrink-0">
						{new Date(agent.startEvent.timestamp).toLocaleTimeString()}
						{agent.stopEvent?.timestamp && (
							<> — {new Date(agent.stopEvent.timestamp).toLocaleTimeString()}</>
						)}
					</span>
				)}
			</button>
			{isOpen && (
				<div
					className="border-l-2 border-kind-agent/20"
					style={{ marginLeft: `${22 + depth * 20}px` }}
				>
					{filtered.length === 0 ? (
						<p className="text-xs text-muted-foreground py-2 pl-4">
							No matching events in this agent.
						</p>
					) : (
						filtered.map((evt, i) => {
							const key = `agent-${agent.agentId}-evt-${i}`;
							return (
								<LeafEvent
									key={key}
									event={evt}
									isExpanded={expandedSet.has(key)}
									onToggle={() => onToggleEvent(key)}
									depth={depth + 1}
								/>
							);
						})
					)}
					{agent.events.length > filtered.length && activeFilters.size > 0 && (
						<p className="text-2xs text-muted-foreground pl-4 py-1">
							{agent.events.length - filtered.length} event
							{agent.events.length - filtered.length !== 1 ? "s" : ""} hidden by
							filters
						</p>
					)}
				</div>
			)}
		</div>
	);
}

/* ── Inline subagent block (JSONL-sourced, nested at Agent tool_call) ── */

function InlineSubagentBlock({
	subagent,
	parentKey,
	expandedSet,
	onToggleEvent,
	activeFilters,
	searchQuery,
}: {
	subagent: SubagentSession;
	parentKey: string;
	expandedSet: Set<string>;
	onToggleEvent: (key: string) => void;
	activeFilters: Set<string>;
	searchQuery: string;
}) {
	const nodeKey = `${parentKey}-subagent`;
	const isOpen = expandedSet.has(nodeKey);
	const { turns } = useMemo(
		() => buildEventTree(subagent.events),
		[subagent.events],
	);
	return (
		<div className="ml-6 mr-3 mb-2 rounded-md border border-kind-agent/30 overflow-hidden">
			<button
				type="button"
				onClick={() => onToggleEvent(nodeKey)}
				className="flex items-center gap-2 w-full text-left px-3 py-1.5 bg-kind-agent/5 hover:bg-kind-agent/10 transition-colors"
				aria-expanded={isOpen}
			>
				{isOpen ? (
					<ChevronDown className="h-3.5 w-3.5 text-kind-agent shrink-0" />
				) : (
					<ChevronRight className="h-3.5 w-3.5 text-kind-agent shrink-0" />
				)}
				<Users className="h-3.5 w-3.5 text-kind-agent shrink-0" />
				<span className="text-xs font-semibold text-kind-agent">
					Subagent
				</span>
				<span className="text-2xs text-muted-foreground ml-1 font-mono">
					{subagent.session_id.slice(0, 8)}
				</span>
				<Badge variant="muted">
					{turns.length} turn{turns.length !== 1 ? "s" : ""}
				</Badge>
			</button>
			{isOpen && (
				<div className="divide-y divide-border">
					{turns.map((turn, ti) => (
						<TurnNode
							key={`${nodeKey}-turn-${ti}`}
							turn={turn}
							index={ti}
							expandedSet={expandedSet}
							onToggleEvent={onToggleEvent}
							activeFilters={activeFilters}
							searchQuery={searchQuery}
						/>
					))}
				</div>
			)}
		</div>
	);
}

/* ── Turn node (collapsible root) ────────────────────────── */

function TurnNode({
	turn,
	index,
	expandedSet,
	onToggleEvent,
	activeFilters,
	searchQuery,
	subagentSessions = [],
}: {
	turn: Turn;
	index: number;
	expandedSet: Set<string>;
	onToggleEvent: (key: string) => void;
	activeFilters: Set<string>;
	searchQuery: string;
	subagentSessions?: SubagentSession[];
}) {
	// Build lookup: spawned_by uuid → subagent session
	const subagentByUuid = useMemo(() => {
		const m = new Map<string, SubagentSession>();
		for (const s of subagentSessions) {
			if (s.spawned_by) m.set(s.spawned_by, s);
		}
		return m;
	}, [subagentSessions]);

	const nodeKey = `turn-${index}`;
	const isOpen = expandedSet.has(nodeKey);
	const attrs = turn.promptEvent?.attributes ?? {};
	const fullPrompt = attrs.tool_input || attrs.prompt_text || "";
	const promptPreview = fullPrompt.slice(0, 80);

	const filteredTop = filterTurnEvents(
		turn.topLevelEvents,
		activeFilters,
		searchQuery,
	);

	// Count totals for the turn
	const toolCount = turn.allEvents.filter((e) => {
		const en = getEventName(e);
		return (
			en === "hook_posttooluse" ||
			en === "hook_pretooluse" ||
			en === "hook_toolresult" ||
			en === "tool_result"
		);
	}).length;
	const apiCount = turn.allEvents.filter(
		(e) => getEventName(e) === "api_request",
	).length;
	// Surfaced on the collapsed header: without it a turn containing failures
	// looks identical to a clean one until you expand it.
	const errorCount = turn.allEvents.filter((e) => {
		const en = getEventName(e);
		const attrs = e.attributes ?? {};
		return (
			en === "hook_posttoolusefailure" ||
			en === "hook_stopfailure" ||
			(en === "hook_posttooluse" && attrs.success === "false") ||
			(en === "tool_result" && attrs.success !== "true")
		);
	}).length;
	const agentCount = turn.agents.length + subagentSessions.length;

	// Timestamps
	const startTime = turn.promptEvent?.timestamp;
	const endTime = turn.stopEvent?.timestamp || turn.responseEvent?.timestamp;
	const duration =
		startTime && endTime
			? new Date(endTime).getTime() - new Date(startTime).getTime()
			: null;

	return (
		<div className="rounded-lg border border-border overflow-hidden">
			{/* Turn header */}
			<button
				type="button"
				onClick={() => onToggleEvent(nodeKey)}
				className="flex items-start gap-2 w-full text-left py-2.5 px-3 hover:bg-kind-prompt/5 transition-colors"
				aria-expanded={isOpen}
			>
				{isOpen ? (
					<ChevronDown className="h-4 w-4 text-kind-prompt mt-0.5 shrink-0" />
				) : (
					<ChevronRight className="h-4 w-4 text-kind-prompt mt-0.5 shrink-0" />
				)}
				<MessageSquare className="h-4 w-4 text-kind-prompt mt-0.5 shrink-0" />
				<div className="flex-1 min-w-0">
					<div className="flex items-center gap-2 flex-wrap">
						<span className="text-sm font-semibold">Turn {index + 1}</span>
						<div className="flex items-center gap-1.5">
							{toolCount > 0 && (
								<Badge variant="muted">
									<Wrench className="h-2.5 w-2.5 mr-0.5 inline" />
									{toolCount}
								</Badge>
							)}
							{apiCount > 0 && (
								<Badge variant="muted">
									<Cpu className="h-2.5 w-2.5 mr-0.5 inline" />
									{apiCount}
								</Badge>
							)}
							{agentCount > 0 && (
								<Badge variant="default">
									<Users className="h-2.5 w-2.5 mr-0.5 inline" />
									{agentCount}
								</Badge>
							)}
							{errorCount > 0 && (
								<Badge variant="warning">
									<AlertTriangle className="h-2.5 w-2.5 mr-0.5 inline" />
									{errorCount} failed
								</Badge>
							)}
							{duration !== null && (
								<Stat label="" value={formatDuration(duration)} icon={Clock} />
							)}
						</div>
					</div>
					{promptPreview && (
						<p className="text-xs text-muted-foreground mt-0.5 truncate max-w-2xl">
							{promptPreview}
							{fullPrompt.length > 80 ? "…" : ""}
						</p>
					)}
				</div>
				{startTime && (
					<span className="text-2xs text-muted-foreground tabular-nums shrink-0 mt-1">
						{new Date(startTime).toLocaleTimeString()}
					</span>
				)}
			</button>

			{/* Turn children */}
			{isOpen && (
				<div className="border-t border-border">
					{/* Full user prompt */}
					{fullPrompt && (
						<div className="mx-3 mt-3 mb-2 rounded-md border border-kind-prompt/20 bg-kind-prompt/5 overflow-hidden">
							<div className="flex items-center gap-1.5 px-3 py-1.5 border-b border-kind-prompt/10">
								<MessageSquare className="h-3 w-3 text-kind-prompt" />
								<span className="text-2xs font-medium text-kind-prompt">
									User Prompt
								</span>
							</div>
							<div className="px-3 py-2.5 text-sm text-foreground whitespace-pre-wrap break-words leading-relaxed max-h-[300px] overflow-auto">
								{fullPrompt}
							</div>
						</div>
					)}
					{/* Top-level events — render each; if it's an Agent tool_call,
					    also render the matching subagent inline beneath it */}
					{filteredTop.map((evt, i) => {
						const key = `turn-${index}-evt-${i}`;
						const evtAttrs = evt.attributes ?? {};
						const evtUuid = evtAttrs.uuid || evtAttrs.message_id || "";
						const isAgentCall =
							getEventName(evt) === "tool_call" &&
							evtAttrs.tool_name === "Agent" &&
							subagentByUuid.has(evtUuid);
						const subagent = isAgentCall
							? subagentByUuid.get(evtUuid)
							: undefined;
						return (
							<div key={key}>
								<LeafEvent
									event={evt}
									isExpanded={expandedSet.has(key)}
									onToggle={() => onToggleEvent(key)}
									depth={1}
								/>
								{subagent && (
									<InlineSubagentBlock
										subagent={subagent}
										parentKey={key}
										expandedSet={expandedSet}
										onToggleEvent={onToggleEvent}
										activeFilters={activeFilters}
										searchQuery={searchQuery}
									/>
								)}
							</div>
						);
					})}

					{/* Legacy hook-based agent sub-trees (pre-JSONL harnesses) */}
					{turn.agents.map((agent, ai) => (
						<AgentNode
							key={`turn-${index}-agent-${ai}`}
							agent={agent}
							expandedSet={expandedSet}
							onToggleEvent={onToggleEvent}
							activeFilters={activeFilters}
							searchQuery={searchQuery}
							depth={1}
						/>
					))}

					{/* Thinking blocks (before response, chronological) */}
					{turn.thinkingEvents.length > 0 &&
						turn.thinkingEvents.map((evt, ti) => (
							<ThinkingBlock key={`turn-${index}-think-${ti}`} event={evt} />
						))}

					{/* Assistant response */}
					{turn.responseEvent && (
						<AssistantResponseBlock event={turn.responseEvent} />
					)}

					{/* Turn end marker */}
					{turn.stopEvent && (
						<div
							className="flex items-center gap-2 py-1 px-3 text-2xs text-muted-foreground"
							style={{ paddingLeft: "32px" }}
						>
							<Square className="h-2.5 w-2.5 text-muted-foreground" />
							<span>
								{turn.stopEvent.attributes?.stop_reason || "end_turn"}
							</span>
							{turn.stopEvent.timestamp && (
								<span className="ml-auto tabular-nums">
									{new Date(turn.stopEvent.timestamp).toLocaleTimeString()}
								</span>
							)}
						</div>
					)}
				</div>
			)}
		</div>
	);
}

/* ── Session summary stats ─────────────────────────────── */

function SessionStats({
	events,
	sessionId,
	serviceName,
}: {
	events: RawSessionEvent[];
	sessionId: string;
	serviceName?: string;
}) {
	const stats = useMemo(() => {
		let totalInputTokens = 0;
		let totalOutputTokens = 0;
		let totalCacheRead = 0;
		let totalCacheWrite = 0;
		let apiCalls = 0;
		const { toolCalls, tools } = summarizeToolCalls(events);
		let hookEvents = 0;
		let credits = 0;
		let isKiro = serviceName === "kiro" || sessionId.startsWith("kiro-");
		const isGemini = serviceName === "gemini";
		const isCopilotCli = isCopilotCliService(serviceName ?? "", sessionId);
		const models = new Set<string>();

		for (const evt of events) {
			const attrs = evt.attributes ?? {};
			const eName = getEventName(evt);
			const svc = evt.service_name ?? "";

			if (svc === "kiro") isKiro = true;

			if (eName === "api_request" || eName === "hook_userpromptsubmit") {
				apiCalls++;
			}

			// Accumulate token counts from any event that carries them.
			// JSONL-ingested sessions surface tokens on hook_assistant_response
			// and hook_token_usage; hook-instrumented sessions use api_request /
			// hook_userpromptsubmit.  All four can carry cache fields.
			if (
				attrs.input_tokens ||
				attrs.output_tokens ||
				attrs.cache_read_tokens ||
				attrs.cache_creation_tokens
			) {
				if (attrs.input_tokens)
					totalInputTokens += parseInt(attrs.input_tokens, 10);
				if (attrs.output_tokens)
					totalOutputTokens += parseInt(attrs.output_tokens, 10);
				if (attrs.cache_read_tokens)
					totalCacheRead += parseInt(attrs.cache_read_tokens, 10);
				if (attrs.cache_creation_tokens)
					totalCacheWrite += parseInt(attrs.cache_creation_tokens, 10);
			}

			// Kiro enriched stop events carry credits and model
			if (attrs.credits) credits += parseFloat(attrs.credits) || 0;
			if (attrs.model) models.add(attrs.model);

			if (isHookEvent(eName)) hookEvents++;
		}

		// Detect reconciliation enrichment (JSONL data was reconciled to server)
		const isEnriched = events.some((e) => {
			const en = getEventName(e);
			return (
				en === "reconcile_enrichment" || e.attributes?.["_enriched"] === "true"
			);
		});

		return {
			totalInputTokens,
			totalOutputTokens,
			totalCacheRead,
			totalCacheWrite,
			apiCalls,
			toolCalls,
			hookEvents,
			credits,
			isKiro,
			isGemini,
			isCopilotCli,
			models,
			tools,
			isEnriched,
		};
	}, [events, sessionId, serviceName]);

	const formatCredits = (c: number) => (c < 0.01 ? c.toFixed(4) : c.toFixed(2));

	return (
		<div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-4">
			{stats.isKiro ? (
				<div className="space-y-1">
					<p className="text-2xs text-muted-foreground uppercase tracking-wide">
						Credits
					</p>
					<p className="text-lg font-semibold tabular-nums text-warning">
						{stats.credits > 0 ? formatCredits(stats.credits) : "—"}
					</p>
				</div>
			) : !stats.isCopilotCli ? (
				<>
					<div className="space-y-1">
						<p className="text-2xs text-muted-foreground uppercase tracking-wide">
							Input Tokens
						</p>
						<p className="text-lg font-semibold tabular-nums">
							{formatTokens(stats.totalInputTokens)}
						</p>
					</div>
					<div className="space-y-1">
						<p className="text-2xs text-muted-foreground uppercase tracking-wide">
							Output Tokens
						</p>
						<p className="text-lg font-semibold tabular-nums">
							{formatTokens(stats.totalOutputTokens)}
						</p>
					</div>
					{!stats.isGemini && (
						<>
							<div className="space-y-1">
								<p className="text-2xs text-muted-foreground uppercase tracking-wide">
									Cache Read
								</p>
								<p className="text-lg font-semibold tabular-nums">
									{formatTokens(stats.totalCacheRead)}
								</p>
							</div>
							<div className="space-y-1">
								<p className="text-2xs text-muted-foreground uppercase tracking-wide">
									Cache Write
								</p>
								<p className="text-lg font-semibold tabular-nums">
									{formatTokens(stats.totalCacheWrite)}
								</p>
							</div>
						</>
					)}
				</>
			) : null}
			{!stats.isGemini && !stats.isCopilotCli && (
				<div className="space-y-1">
					<p className="text-2xs text-muted-foreground uppercase tracking-wide">
						API Calls
					</p>
					<p className="text-lg font-semibold tabular-nums">{stats.apiCalls}</p>
				</div>
			)}
			<div className="space-y-1">
				<p className="text-2xs text-muted-foreground uppercase tracking-wide">
					Tool Calls
				</p>
				<p className="text-lg font-semibold tabular-nums">{stats.toolCalls}</p>
			</div>
			{stats.hookEvents > 0 && (
				<div className="space-y-1">
					<p className="text-2xs text-muted-foreground uppercase tracking-wide">
						Hook Captures
					</p>
					<p className="text-lg font-semibold tabular-nums text-warning">
						{stats.hookEvents}
					</p>
				</div>
			)}
			{stats.isEnriched && (
				<div className="space-y-1">
					<p className="text-2xs text-muted-foreground uppercase tracking-wide">
						Data Source
					</p>
					<div className="flex items-center gap-1.5">
						<span className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs font-semibold bg-success/15 text-success border border-success/25">
							<CheckCircle2 className="h-3.5 w-3.5" />
							Enriched
						</span>
					</div>
				</div>
			)}
			{!stats.isCopilotCli && (
				<>
					<div className="col-span-full">
						<Separator />
					</div>
					<div className="col-span-full space-y-1">
						<p className="text-2xs text-muted-foreground uppercase tracking-wide">
							Models
						</p>
						<div className="flex flex-wrap gap-1">
							{[...stats.models].map((m) => (
								<Badge key={m}>{m.replace("claude-", "")}</Badge>
							))}
						</div>
					</div>
				</>
			)}
			{Object.keys(stats.tools).length > 0 && (
				<div className="col-span-full space-y-1">
					<p className="text-2xs text-muted-foreground uppercase tracking-wide">
						Tools Used
					</p>
					<div className="flex flex-wrap gap-1.5">
						{Object.entries(stats.tools)
							.sort(([, a], [, b]) => b - a)
							.map(([tool, count]) => (
								<Badge key={tool} variant="muted">
									{tool} ({count})
								</Badge>
							))}
					</div>
				</div>
			)}
		</div>
	);
}

/* ── Session Info tab ─────────────────────────────────────── */

function isCopilotCliService(serviceName: string, sessionId: string): boolean {
	return (
		serviceName === "copilot-cli" ||
		serviceName === "copilot" ||
		serviceName === "GitHub Copilot" ||
		sessionId.startsWith("copilot-cli-")
	);
}
/** Unix epoch sentinel: timestamps at/before 1971 or after 2099 are placeholders. */
const EPOCH_CUTOFF_MS = new Date("1971-01-01").getTime();
const TS_UPPER_BOUND_MS = new Date("2099-01-01").getTime();
const isRealTs = (ts: string | undefined): ts is string =>
	!!ts && new Date(ts).getTime() > EPOCH_CUTOFF_MS && new Date(ts).getTime() < TS_UPPER_BOUND_MS;

function SessionInfoTab({
	events,
	serviceName,
	agentName,
	agentId,
	agentVersion,
}: {
	events: RawSessionEvent[];
	sessionId: string;
	serviceName: string;
	agentName?: string | null;
	agentId?: string | null;
	agentVersion?: string | null;
}) {
	// Uses isRealTs to skip 1970-epoch sentinel timestamps from Kiro lines
	// that don't carry a real timestamp (AssistantMessage/ToolResults).
	const sessionMeta = useMemo(() => {
		const startEvt = events.find(
			(e) => getEventName(e) === "hook_sessionstart",
		);
		const attrs = startEvt?.attributes ?? {};
		const firstReal = events.find((e) => isRealTs(e.timestamp));
		const lastReal = [...events].reverse().find((e) => isRealTs(e.timestamp));
		const duration =
			firstReal && lastReal
				? new Date(lastReal.timestamp).getTime() -
					new Date(firstReal.timestamp).getTime()
				: 0;

		return {
			source: attrs.session_source || "startup",
			resumed:
				attrs.session_resumed === "true" || attrs.session_resumed === "True",
			cwd: attrs.cwd || "",
			permissionMode: attrs.permission_mode || "",
			startTime: firstReal?.timestamp
				? new Date(firstReal.timestamp).toLocaleString()
				: "",
			endTime: lastReal?.timestamp
				? new Date(lastReal.timestamp).toLocaleString()
				: "",
			duration,
			totalEvents: events.length,
		};
	}, [events]);

	return (
		<div className="space-y-6">
			{/* Session metadata */}
			<div className="space-y-3">
				<h3 className="text-sm font-semibold flex items-center gap-1.5">
					<Info className="h-4 w-4 text-muted-foreground" />
					Session Details
				</h3>
				<div className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm bg-surface-sunken rounded-lg p-4">
					<span className="text-muted-foreground">Service</span>
					<span>{serviceName || "unknown"}</span>
					{(agentName || agentId) && (
						<>
							<span className="text-muted-foreground">Agent File</span>
							<span>{agentName || agentId}</span>
						</>
					)}
					{agentVersion && (
						<>
							<span className="text-muted-foreground">Agent File Version</span>
							<span>v{agentVersion}</span>
						</>
					)}
					<span className="text-muted-foreground">Source</span>
					<span className="capitalize">
						{sessionMeta.source}
						{sessionMeta.resumed ? " (resumed)" : ""}
					</span>
					{sessionMeta.cwd && (
						<>
							<span className="text-muted-foreground">Working Dir</span>
							<span className="font-[family-name:var(--font-mono)] text-xs truncate">
								{sessionMeta.cwd}
							</span>
						</>
					)}
					{sessionMeta.permissionMode && (
						<>
							<span className="text-muted-foreground">Permission Mode</span>
							<span>{sessionMeta.permissionMode}</span>
						</>
					)}
					<span className="text-muted-foreground">Started</span>
					<span className="tabular-nums">{sessionMeta.startTime}</span>
					<span className="text-muted-foreground">Last Event</span>
					<span className="tabular-nums">{sessionMeta.endTime}</span>
					<span className="text-muted-foreground">Duration</span>
					<span className="tabular-nums">
						{sessionMeta.duration > 0
							? formatDuration(sessionMeta.duration)
							: "-"}
					</span>
					<span className="text-muted-foreground">Total Events</span>
					<span className="tabular-nums">{sessionMeta.totalEvents}</span>
				</div>
			</div>
		</div>
	);
}
/* ── Page ───────────────────────────────────────────────── */

export default function TraceDetailPage() {
	const { traceId: id } = useParams({ from: "/_authed/_user/traces/$traceId" });
	const { data, isLoading, isError, error, refetch } = useSessionDetail(id);
	useSessionSubscription();

	const session = data as SessionData;
	const events: RawSessionEvent[] = useMemo(
		() => session?.events ?? [],
		[session],
	);
	const subagentSessions: SubagentSession[] = useMemo(
		() => session?.subagent_sessions ?? [],
		[session],
	);
	const sessionTiming = useMemo(() => {
		const timestampedEvents = events
			.filter((event) => isRealTs(event.timestamp))
			.sort((left, right) => new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime());
		return {
			first: timestampedEvents[0],
			last: timestampedEvents.at(-1),
		};
	}, [events]);

	const [expandedSet, setExpandedSet] = useState<Set<string>>(new Set());
	const [activeFilters, setActiveFilters] = useState<Set<string>>(new Set());
	const [searchQuery, setSearchQuery] = useState("");

	const toggleFilter = useCallback((key: string) => {
		setActiveFilters((prev) => {
			const next = new Set(prev);
			if (next.has(key)) next.delete(key);
			else next.add(key);
			return next;
		});
	}, []);

	const clearFilters = useCallback(() => {
		setActiveFilters(new Set());
		setSearchQuery("");
	}, []);

	// Build the tree
	const tree = useMemo(() => buildEventTree(events), [events]);

	// Filter counts (on deduplicated events)
	const allDeduped = useMemo(() => deduplicateEvents(events), [events]);
	const filterCounts = useMemo(() => {
		const counts: Record<string, number> = {};
		for (const cat of FILTER_CATEGORIES) {
			counts[cat.key] = allDeduped.filter((evt) =>
				cat.match(getEventName(evt)),
			).length;
		}
		return counts;
	}, [allDeduped]);


	// Visible turns after filtering
	const visibleTurns = useMemo(() => {
		if (activeFilters.size === 0 && !searchQuery.trim()) return tree.turns;
		return tree.turns.filter((t) =>
			turnMatchesFilters(t, activeFilters, searchQuery),
		);
	}, [tree.turns, activeFilters, searchQuery]);

	const onToggleEvent = useCallback((key: string) => {
		setExpandedSet((prev) => {
			const next = new Set(prev);
			if (next.has(key)) next.delete(key);
			else next.add(key);
			return next;
		});
	}, []);

	const expandAllTurns = useCallback(() => {
		setExpandedSet((prev) => {
			const allTurnKeys = visibleTurns.map(
				(_, i) => `turn-${tree.turns.indexOf(visibleTurns[i])}`,
			);
			const allOpen = allTurnKeys.every((k) => prev.has(k));
			if (allOpen) return new Set(); // collapse all
			const next = new Set(prev);
			for (const k of allTurnKeys) next.add(k);
			return next;
		});
	}, [visibleTurns, tree.turns]);

	return (
		<>
			<PageHeader
				title={(() => {
					if (isLoading) return "Trace";
					const firstReal = events.find((e) => isRealTs(e.timestamp));
					return session?.service_name && firstReal
						? `${session.service_name} · ${new Date(firstReal.timestamp).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}`
						: "Trace";
				})()}
				breadcrumbs={[
					{ label: "Dashboard", href: "/dashboard" },
					{ label: "Traces", href: "/traces" },
					{
						label: (() => {
							const firstReal = events.find((e) => isRealTs(e.timestamp));
							return session?.service_name && firstReal
								? `${session.service_name} · ${new Date(firstReal.timestamp).toLocaleDateString([], { month: "short", day: "numeric" })}`
								: "Trace";
						})(),
					},
				]}
			/>
			<div className="page-body w-full mx-auto space-y-5">
				{isLoading ? (
					<DetailSkeleton />
				) : isError ? (
					<ErrorState message={error?.message} onRetry={() => refetch()} />
				) : !data ? (
					<ErrorState message="Session not found" />
				) : (
					<>
						{/* Header info */}
						<div className="animate-in grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3 xl:grid-cols-5">
							{session.service_name && (
								<div>
									<span className="text-xs text-muted-foreground block mb-0.5">
										harness
									</span>
									<span className="text-sm">{session.service_name}</span>
								</div>
							)}
							{sessionTiming.first && (
								<div>
									<span className="text-xs text-muted-foreground block mb-0.5">
										First Event
									</span>
									<span className="text-sm tabular-nums">
										{new Date(sessionTiming.first.timestamp).toLocaleString()}
									</span>
								</div>
							)}
							<div>
								<span className="text-xs text-muted-foreground block mb-0.5">Duration</span>
								<span className="text-sm tabular-nums">
									{sessionTiming.first && sessionTiming.last && sessionTiming.first !== sessionTiming.last
										? formatDuration(new Date(sessionTiming.last.timestamp).getTime() - new Date(sessionTiming.first.timestamp).getTime())
										: "-"}
								</span>
							</div>
							<div>
								<span className="text-xs text-muted-foreground block mb-0.5">Last event</span>
								<span className="text-sm tabular-nums">
									{sessionTiming.last ? new Date(sessionTiming.last.timestamp).toLocaleString() : "-"}
								</span>
							</div>
							<div>
								<span className="text-xs text-muted-foreground block mb-0.5">
									Turns
								</span>
								<span className="text-sm font-semibold tabular-nums">
									{tree.turns.length}
								</span>
							</div>
						</div>

						<Separator />
						<SessionStats
							events={events}
							sessionId={id}
							serviceName={session.service_name}
						/>


						{/* Tabbed content */}
						{events.length === 0 ? (
							<EmptyState
								icon={FileText}
								title="No events in this session"
								description="Events will appear here once telemetry data is recorded."
							/>
						) : (
							<Tabs defaultValue="traces" className="animate-in stagger-1">
								<TabsList>
									<TabsTrigger value="traces">Traces</TabsTrigger>
									<TabsTrigger value="info">Session Info</TabsTrigger>
								</TabsList>
								<TabsContent value="traces" className="space-y-2 mt-4">
									{/* Search + Filter bar */}
									<div className="space-y-2 mb-3">
										<div className="flex items-center gap-2">
											<div className="relative flex-1 max-w-sm">
												<Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
												<Input
													placeholder="Search events, tools, content..."
													value={searchQuery}
													onChange={(e) => setSearchQuery(e.target.value)}
													className="pl-8 h-8 text-sm"
												/>
												{searchQuery && (
													<button
														type="button"
														onClick={() => setSearchQuery("")}
														className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
														aria-label="Clear search"
													>
														<X className="h-3.5 w-3.5" />
													</button>
												)}
											</div>
											<Button
												variant="ghost"
												size="sm"
												onClick={expandAllTurns}
												className="h-8 text-xs gap-1"
											>
												<ChevronsUpDown className="h-3 w-3" />
												Toggle turns
											</Button>
										</div>
										<div className="flex items-center gap-1.5 flex-wrap">
											<Filter className="h-3 w-3 text-muted-foreground shrink-0" />
											{FILTER_CATEGORIES.filter(
												(cat) => filterCounts[cat.key] > 0,
											).map((cat) => (
												<button
													type="button"
													key={cat.key}
													onClick={() => toggleFilter(cat.key)}
									aria-pressed={activeFilters.has(cat.key)}
													className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-2xs font-medium border transition-all ${
														activeFilters.has(cat.key)
															? cat.color + " border-current"
															: "bg-muted/50 text-muted-foreground border-transparent hover:bg-muted"
													}`}
												>
													{cat.label}
													<span className="opacity-60">
														{filterCounts[cat.key]}
													</span>
												</button>
											))}
											{(activeFilters.size > 0 || searchQuery) && (
												<button
													type="button"
													onClick={clearFilters}
													className="inline-flex items-center gap-0.5 px-1.5 py-0.5 text-2xs text-muted-foreground hover:text-foreground"
												>
													<X className="h-3 w-3" /> Clear
												</button>
											)}
										</div>
									</div>

									{/* Turn count */}
									<div className="flex items-center justify-between">
										<span className="text-xs text-muted-foreground">
											{visibleTurns.length === tree.turns.length
												? `${tree.turns.length} turn${tree.turns.length !== 1 ? "s" : ""}`
												: `${visibleTurns.length} of ${tree.turns.length} turns`}
											{" · "}
											{allDeduped.length} events (deduped from {events.length})
										</span>
									</div>

									{/* Pre-session events */}
									{tree.preSessionEvents.length > 0 && (
										<div className="rounded-lg border border-border/50 p-2 space-y-0.5">
											<span className="text-2xs text-muted-foreground uppercase tracking-wide px-2">
												Pre-session
											</span>
											{tree.preSessionEvents.map((evt, i) => {
												const key = `pre-${i}`;
												return (
													<LeafEvent
														key={key}
														event={evt}
														isExpanded={expandedSet.has(key)}
														onToggle={() => onToggleEvent(key)}
														depth={0}
													/>
												);
											})}
										</div>
									)}

									{/* Turn nodes */}
									{visibleTurns.length === 0 ? (
										<div className="text-center py-8 text-sm text-muted-foreground">
											No turns match your filters.
										</div>
									) : (
										<div className="space-y-2">
											{visibleTurns.map((turn) => {
												const originalIndex = tree.turns.indexOf(turn);
												return (
													<TurnNode
														key={`turn-${originalIndex}`}
														turn={turn}
														index={originalIndex}
														expandedSet={expandedSet}
														onToggleEvent={onToggleEvent}
														activeFilters={activeFilters}
														searchQuery={searchQuery}
														subagentSessions={subagentSessions}
													/>
												);
											})}
										</div>
									)}
								</TabsContent>
								<TabsContent value="info" className="mt-4">
									<SessionInfoTab
										events={events}
										sessionId={id}
										serviceName={session.service_name}
										agentName={session.agent_name}
										agentId={session.agent_id}
										agentVersion={session.agent_version}
									/>
								</TabsContent>
							</Tabs>
						)}
					</>
				)}
			</div>
		</>
	);
}
