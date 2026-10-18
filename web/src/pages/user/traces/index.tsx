// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Harshith Padakanti <harshaharshith31@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-FileCopyrightText: 2026 Shreem Seth <shreemseth26@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { Link, useRouter, useSearch, useLocation } from "@tanstack/react-router";
import { useState, useMemo, useCallback, useRef, useEffect } from "react";
import {
	Activity,
	Search,
	ArrowUpDown,
	ArrowUp,
	ArrowDown,
	SlidersHorizontal,
	HelpCircle,
} from "lucide-react";
import { toast } from "sonner";
import { useHelp } from "@/components/wiki/help-context";
import { useHarnesses } from "@/hooks/use-harnesses";
import {
	useAllSessions2,
	useSessionsSummary,
	useSessionSubscription,
	useWhoami,
} from "@/hooks/use-api";
import {
	useReactTable,
	getCoreRowModel,
	getSortedRowModel,
	flexRender,
	type ColumnDef,
	type SortingState,
} from "@tanstack/react-table";
import {
	Table,
	TableBody,
	TableCell,
	TableHead,
	TableHeader,
	TableRow,
} from "@/components/ui/table";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { SearchField } from "@/components/ui/search-field";
import {
	SummaryBar,
	SummaryBarStat,
	SummaryBarStatus,
} from "@/components/ui/summary-bar";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PageHeader } from "@/components/layouts/page-header";
import { TableSkeleton } from "@/components/shared/skeleton-layouts";
import { ErrorState } from "@/components/shared/error-state";
import { StatusBadge } from "@/components/registry/status-badge";
import { EmptyState } from "@/components/shared/empty-state";
import { cn } from "@/lib/utils";
import type { Session } from "@/lib/types";

/** Quickstart docs URL shown in the first-time empty state CTA. */
const DOCS_QUICKSTART_URL =
	"https://github.com/Observal/Observal/blob/main/docs/getting-started/quickstart.md";

/**
 * How recent the newest event must be for ingest to read as healthy. The
 * summary bar states the verdict in words either way, so this only decides the
 * wording and the dot — never carries the meaning on its own.
 */
const INGEST_FRESH_WINDOW_MS = 5 * 60_000;

const TABLE_COLUMN_WIDTHS: Record<string, string> = {
	session_id: "w-[26%]",
	user_name: "w-[12%]",
	status: "w-[10%]",
	source: "w-[11%]",
	agent_name: "w-[13%]",
	model: "w-[11%]",
	activity: "w-[8%]",
	tokens: "w-[10%]",
	first_event_time: "w-[9%]",
};

function truncateQuery(q: string, max = 50): string {
	return q.length > max ? `${q.slice(0, max)}…` : q;
}

// ── Search query parser (Discord-style: platform:kiro user:"John Doe") ───────

function parseSearchQuery(query: string): { text: string; filters: Record<string, string> } {
	const filters: Record<string, string> = {};
	const tokens = query.match(/(\w+):(?:"([^"]*)"|([^\s]*))/g);
	if (tokens) {
		for (const token of tokens) {
			const colonIdx = token.indexOf(":");
			const key = token.slice(0, colonIdx).toLowerCase();
			let value = token.slice(colonIdx + 1);
			if (value.startsWith('"') && value.endsWith('"')) {
				value = value.slice(1, -1);
			}
			const keyMap: Record<string, string> = {
				platform: "platform",
				harness: "platform",
				user: "user",
				agent: "agent",
				model: "model",
				days: "days",
				status: "status",
			};
			const apiKey = keyMap[key];
			if (apiKey) filters[apiKey] = value;
		}
	}
	// Remaining text after removing filter tokens
	const text = query.replace(/(\w+):(?:"[^"]*"|[^\s]*)/g, "").trim();
	return { text, filters };
}

function filterTokensOnly(query: string): string {
	const tokens = query.match(/(\w+):(?:"[^"]*"|[^\s]*)/g);
	return tokens ? tokens.join(" ").trim() : "";
}

/**
 * Replace (or drop, when `value` is null) a single `key:value` token inside the
 * query string. The toolbar selects write through this so the search field
 * stays the one source of truth for filtering — the same string the user can
 * type by hand, and the same string the URL carries.
 */
function withFilterToken(query: string, key: string, value: string | null): string {
	const pattern = new RegExp(`(?:^|\\s)${key}:(?:"[^"]*"|[^\\s]*)`, "gi");
	let next = query.replace(pattern, " ").replace(/\s+/g, " ").trim();
	if (value) {
		const token = /\s/.test(value) ? `${key}:"${value}"` : `${key}:${value}`;
		next = next ? `${next} ${token}` : token;
	}
	return next;
}

type TracesEmptyStateKind = "first-time" | "filter" | "search" | "fallback";

function applyParameterFilters(
	sessions: Session[],
	filters: Record<string, string>,
): Session[] {
	let result = sessions;

	if (filters.platform) {
		const p = filters.platform.toLowerCase();
		result = result.filter(
			(s) =>
				(s.service_name ?? "").toLowerCase().includes(p) ||
				(s.platform ?? "").toLowerCase().includes(p),
		);
	}
	if (filters.user) {
		const u = filters.user.toLowerCase();
		result = result.filter((s) =>
			(s.user_name ?? "").toLowerCase().includes(u),
		);
	}
	if (filters.agent) {
		const a = filters.agent.toLowerCase();
		result = result.filter((s) =>
			(s.agent_name ?? "").toLowerCase().includes(a),
		);
	}
	if (filters.model) {
		const m = filters.model.toLowerCase();
		result = result.filter((s) =>
			(s.model ?? "").toLowerCase().includes(m),
		);
	}
	if (filters.days) {
		const d = parseInt(filters.days, 10);
		if (!isNaN(d) && d > 0) {
			const cutoff = Date.now() - d * 86_400_000;
			result = result.filter((s) =>
				s.first_event_time && toDate(s.first_event_time).getTime() >= cutoff,
			);
		}
	}
	if (filters.status) {
		const st = filters.status.toLowerCase();
		if (st === "active") result = result.filter((s) => s.is_active);
		else if (st === "inactive") result = result.filter((s) => !s.is_active);
	}

	return result;
}

function applyTextFilter(sessions: Session[], text: string): Session[] {
	if (!text) return sessions;
	const lower = text.toLowerCase();
	return sessions.filter(
		(s) =>
			(s.session_id ?? "").toLowerCase().includes(lower) ||
			(s.user_name ?? "").toLowerCase().includes(lower) ||
			(s.agent_name ?? "").toLowerCase().includes(lower) ||
			(s.model ?? "").toLowerCase().includes(lower) ||
			(s.platform ?? "").toLowerCase().includes(lower),
	);
}

function getTracesEmptyStateKind(
	sessions: Session[],
	query: string,
): TracesEmptyStateKind {
	const { text, filters } = parseSearchQuery(query);
	const hasFilters = Object.keys(filters).length > 0;
	const hasText = text.length > 0;

	if (sessions.length === 0) {
		if (hasText) return "search";
		if (hasFilters) return "filter";
		return "first-time";
	}

	if (hasFilters) {
		const afterFilters = applyParameterFilters(sessions, filters);
		if (afterFilters.length === 0) return "filter";
		if (hasText && applyTextFilter(afterFilters, text).length === 0) {
			return "search";
		}
		return "fallback";
	}

	if (hasText && applyTextFilter(sessions, text).length === 0) return "search";
	return "fallback";
}

// ── Helpers ──────────────────────────────────────────────────────────

function isKiroSession(row: Session): boolean {
	return row.service_name === "kiro" || row.session_id.startsWith("kiro-");
}

function isCursorSession(row: Session): boolean {
	return row.service_name === "cursor" || row.platform === "Cursor";
}

function isCopilotCliSession(row: Session): boolean {
	return (
		row.service_name === "copilot-cli" ||
		row.service_name === "copilot" ||
		row.service_name === "GitHub Copilot" ||
		row.session_id.startsWith("copilot-cli-")
	);
}

function fmtTokens(n: number | string | undefined): string {
	if (n == null) return "0";
	const num = typeof n === "string" ? parseInt(n, 10) : n;
	if (isNaN(num)) return "0";
	if (num >= 1_000_000) return `${(num / 1_000_000).toFixed(1)}M`;
	if (num >= 1_000) return `${(num / 1_000).toFixed(1)}k`;
	return `${num}`;
}

function fmtCredits(c: number | string | undefined | null): string | null {
	if (c === null || c === undefined || c === "") return null;
	const num = typeof c === "number" ? c : parseFloat(c as string);
	if (isNaN(num) || num <= 0) return null;
	return num < 0.01 ? num.toFixed(4) : num.toFixed(2);
}

const TS_UPPER_BOUND_MS = new Date("2099-01-01").getTime();

function fmtDuration(first?: string, last?: string): string {
	if (!first || !last) return "–";
	const t1 = toDate(first).getTime();
	const t2 = toDate(last).getTime();
	if (t1 >= TS_UPPER_BOUND_MS || t2 >= TS_UPPER_BOUND_MS) return "–";
	const ms = t2 - t1;
	if (ms < 0) return "–";
	const mins = Math.floor(ms / 60_000);
	const hours = Math.floor(mins / 60);
	if (hours > 0) return `${hours}h ${String(mins % 60).padStart(2, "0")}m`;
	if (mins > 0) return `${mins}m`;
	return "< 1m";
}

function toDate(ts: string): Date {
	if (ts.endsWith("Z") || /[+-]\d{2}:\d{2}$/.test(ts)) return new Date(ts);
	// ClickHouse returns DateTime64 as "YYYY-MM-DD HH:MM:SS.mmm" (space, no Z).
	// Replace the space with T and append Z for valid ISO 8601 UTC parsing.
	return new Date(ts.replace(" ", "T") + "Z");
}

function relTime(ts?: string): string {
	if (!ts) return "–";
	const ms = Date.now() - toDate(ts).getTime();
	if (ms < 0) return "just now";
	const mins = Math.floor(ms / 60_000);
	const hours = Math.floor(ms / 3_600_000);
	const days = Math.floor(ms / 86_400_000);
	if (days > 0) return `${days}d ago`;
	if (hours > 0) return `${hours}h ago`;
	if (mins > 0) return `${mins}m ago`;
	return "just now";
}

function absTime(ts?: string): string {
	if (!ts) return "";
	return toDate(ts).toLocaleString();
}

function shortModel(raw?: string): string {
	if (!raw) return "";
	return raw
		.replace("claude-", "")
		.replace("anthropic.", "")
		.replace(/-\d{8}$/, "");
}

function derivePlatform(row: Session): string {
	if (row.platform) return row.platform;
	if (isCursorSession(row)) return "Cursor";
	if (isKiroSession(row)) return "Kiro";
	if (isCopilotCliSession(row)) return "Copilot CLI";
	return "Claude Code";
}

function sessionLabel(row: Session): string {
	const model = shortModel(row.model);
	const count = row.prompt_count ?? 0;
	const suffix = count === 1 ? "prompt" : "prompts";
	const version = row.agent_version ? ` v${row.agent_version}` : "";
	const agent = row.agent_name ? `${row.agent_name}${version} · ` : "";
	if (model) return `${agent}${model} · ${count} ${suffix}`;
	return `${agent}${count} ${suffix}`;
}

function sessionSubLabel(row: Session): string {
	return row.session_id;
}

function tokenTotal(row: Session): number {
	return (row.total_input_tokens ?? 0) + (row.total_output_tokens ?? 0);
}

// ── Sort Icon ────────────────────────────────────────────────────────

function SortIcon({ sorted }: { sorted: false | "asc" | "desc" }) {
	if (sorted === "asc") return <ArrowUp className="h-3 w-3" />;
	if (sorted === "desc") return <ArrowDown className="h-3 w-3" />;
	return <ArrowUpDown className="h-3 w-3 opacity-25" />;
}

// ── Cell fragments (shared by the table and the narrow card list) ─────

function TraceTitle({ row }: { row: Session }) {
	return (
		<div className="min-w-0 min-[1020px]:min-w-[250px]">
			<Link
				to="/traces/$traceId"
				params={{ traceId: row.session_id }}
				className="block truncate rounded-sm text-xs font-medium text-foreground transition-colors hover:text-foreground/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
				onClick={(e) => e.stopPropagation()}
			>
				{sessionLabel(row)}
			</Link>
			<span className="mt-[3px] block truncate font-mono text-4xs text-muted-foreground">
				{sessionSubLabel(row)}
			</span>
		</div>
	);
}

function TraceStatus({ row }: { row: Session }) {
	return (
		<StatusBadge status={row.is_active ? "live" : "completed"} />
	);
}

function TraceSource({ row }: { row: Session }) {
	return (
		<span className="block max-w-[140px] truncate text-2xs font-medium text-foreground">
			{derivePlatform(row)}
		</span>
	);
}

function TraceActivity({ row }: { row: Session }) {
	const tools = row.tool_result_count ?? 0;
	return (
		<span className="whitespace-nowrap text-2xs font-medium text-foreground">
			{tools} tools
		</span>
	);
}

/** Token input/output pair. Kiro reports credits instead of tokens. */
function TraceTokens({ row }: { row: Session }) {
	if (isKiroSession(row)) {
		const credits = fmtCredits(row.total_credits ?? row.credits);
		return (
			<span className="whitespace-nowrap font-mono text-2xs tabular-nums text-warning">
				{credits ? `${credits} cr` : "–"}
			</span>
		);
	}
	if (!row.total_input_tokens && !row.total_output_tokens) {
		return <span className="text-2xs text-muted-foreground">—</span>;
	}
	return (
		<span
			className="whitespace-nowrap font-mono text-2xs tabular-nums"
			title={`In: ${row.total_input_tokens?.toLocaleString() ?? 0} · Out: ${row.total_output_tokens?.toLocaleString() ?? 0}`}
		>
			<span className="text-success">{fmtTokens(row.total_input_tokens)}</span>
			<span className="text-muted-foreground/50"> / </span>
			<span className="text-info">{fmtTokens(row.total_output_tokens)}</span>
		</span>
	);
}

// ── Page ─────────────────────────────────────────────────────────────

type SavedView = "all" | "active" | "mine";

export default function TracesPage() {
	const helpCtx = useHelp();
	const router = useRouter();
	const { search: searchParam } = useSearch({ from: "/_authed/_user/traces/" });
	const { pathname } = useLocation();
	const [page, setPage] = useState(0);
	const PAGE_SIZE = 50;
	const [sorting, setSorting] = useState<SortingState>([
		{ id: "first_event_time", desc: true },
	]);
	const [searchValue, setSearchValue] = useState(searchParam ?? "");
	const [globalFilter, setGlobalFilter] = useState(searchParam ?? "");
	const [savedView, setSavedView] = useState<SavedView>("all");
	const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);
	const searchRef = useRef<HTMLInputElement>(null);
	const serverQuery = useMemo(() => parseSearchQuery(globalFilter), [globalFilter]);

	const {
		data: sessions,
		isLoading,
		isError,
		error,
		refetch,
	} = useAllSessions2({
		platform: serverQuery.filters.platform,
		user: serverQuery.filters.user,
		days: serverQuery.filters.days && !isNaN(parseInt(serverQuery.filters.days, 10)) ? parseInt(serverQuery.filters.days, 10) : undefined,
	});
	const { data: summary } = useSessionsSummary();
	const { data: availableHarnesses = [] } = useHarnesses();
	const { data: whoami } = useWhoami();
	useSessionSubscription();

	const updateURL = useCallback(
		(value: string) => {
			const params = new URLSearchParams(window.location.search);
			if (value) {
				params.set("search", value);
			} else {
				params.delete("search");
			}
			const qs = params.toString();
			window.history.replaceState(null, "", qs ? `${pathname}?${qs}` : pathname);
		},
		[pathname],
	);

	const handleSearch = useCallback(
		(value: string) => {
			setSearchValue(value);
			setPage(0);
			clearTimeout(debounceRef.current);
			debounceRef.current = setTimeout(() => {
				setGlobalFilter(value);
				updateURL(value);
			}, 300);
		},
		[updateURL],
	);

	/**
	 * Same effect as `handleSearch` without the typing debounce — used by the
	 * toolbar selects, which are discrete choices rather than keystrokes.
	 */
	const applyQuery = useCallback(
		(value: string) => {
			clearTimeout(debounceRef.current);
			setSearchValue(value);
			setGlobalFilter(value);
			updateURL(value);
			setPage(0);
		},
		[updateURL],
	);

	const clearSearch = useCallback(() => {
		const remaining = filterTokensOnly(globalFilter);
		clearTimeout(debounceRef.current);
		setSearchValue(remaining);
		setGlobalFilter(remaining);
		updateURL(remaining);
	}, [globalFilter, updateURL]);

	const clearFilters = useCallback(() => {
		const { text } = parseSearchQuery(globalFilter);
		clearTimeout(debounceRef.current);
		setSearchValue(text);
		setGlobalFilter(text);
		updateURL(text);
	}, [globalFilter, updateURL]);
	// than decorative.
	useEffect(() => {
		const onKeyDown = (e: KeyboardEvent) => {
			if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
				e.preventDefault();
				searchRef.current?.focus();
				searchRef.current?.select();
			}
		};
		window.addEventListener("keydown", onKeyDown);
		return () => window.removeEventListener("keydown", onKeyDown);
	}, []);

	useEffect(() => () => clearTimeout(debounceRef.current), []);

	const allSessions = useMemo(() => (sessions ?? []) as Session[], [sessions]);

	const parsedQuery = useMemo(() => parseSearchQuery(globalFilter), [globalFilter]);
	const emptyStateKind = useMemo(
		() => getTracesEmptyStateKind(allSessions, globalFilter),
		[allSessions, globalFilter],
	);

	const filteredSessions = useMemo(() => {
		const { text, filters } = parseSearchQuery(globalFilter);
		return applyTextFilter(applyParameterFilters(allSessions, filters), text);
	}, [allSessions, globalFilter]);

	// Identities that count as "mine" for the saved view. Sessions carry a
	// display name, so match it against every name this account is known by.
	const myNames = useMemo(() => {
		const keys = new Set<string>();
		for (const value of [whoami?.name, whoami?.username, whoami?.email]) {
			if (value) keys.add(String(value).toLowerCase());
		}
		if (whoami?.email) keys.add(String(whoami.email).split("@")[0].toLowerCase());
		return keys;
	}, [whoami]);

	const isMine = useCallback(
		(s: Session) => !!s.user_name && myNames.has(s.user_name.toLowerCase()),
		[myNames],
	);

	const data = useMemo(() => {
		if (savedView === "active") return filteredSessions.filter((s) => s.is_active);
		if (savedView === "mine") return filteredSessions.filter(isMine);
		return filteredSessions;
	}, [filteredSessions, savedView, isMine]);

	const columns = useMemo<ColumnDef<Session>[]>(
		() => [
			{
				accessorKey: "session_id",
				header: "Session",
				cell: ({ row }) => <TraceTitle row={row.original} />,
			},
			{
				accessorKey: "user_name",
				header: "User",
				cell: ({ row }) => (
					<span className="block max-w-[140px] truncate text-2xs font-medium text-foreground">
						{row.original.user_name || "—"}
					</span>
				),
			},
			{
				// A live session used to be signalled only by a green bar down the
				// left edge — no icon, no label, so it did not survive a
				// colour-vision difference or a monochrome override. StatusBadge
				// states it in words, so color is never the only cue.
				id: "status",
				accessorFn: (row) => (row.is_active ? "live" : "completed"),
				header: "Status",
				cell: ({ row }) => <TraceStatus row={row.original} />,
			},
			{
				id: "source",
				accessorFn: (row) => derivePlatform(row),
				header: "Source",
				cell: ({ row }) => <TraceSource row={row.original} />,
			},
			{
				accessorKey: "agent_name",
				header: "Agent File",
				cell: ({ row }) => (
					<span className="block max-w-[180px] truncate text-2xs font-medium text-foreground">
						{row.original.agent_name || "—"}
					</span>
				),
			},
			{
				id: "model",
				accessorFn: (row) => shortModel(row.model),
				header: "Model",
				cell: ({ row }) => (
					<span className="block max-w-[160px] truncate text-2xs font-medium text-foreground">
						{shortModel(row.original.model) || "—"}
					</span>
				),
			},
			{
				id: "activity",
				accessorFn: (row) => row.tool_result_count ?? 0,
				header: "Activity",
				cell: ({ row }) => <TraceActivity row={row.original} />,
			},
			{
				id: "tokens",
				header: "Tokens",
				accessorFn: (row) => tokenTotal(row),
				cell: ({ row }) => <TraceTokens row={row.original} />,
			},
			{
				accessorKey: "first_event_time",
				header: "Started",
				cell: ({ row }) => (
					<span
						className="whitespace-nowrap text-2xs font-medium text-foreground"
						title={absTime(row.original.first_event_time)}
					>
						{relTime(row.original.first_event_time)}
					</span>
				),
				sortingFn: (a, b) => {
					const ta = a.original.first_event_time
						? toDate(a.original.first_event_time).getTime()
						: 0;
					const tb = b.original.first_event_time
						? toDate(b.original.first_event_time).getTime()
						: 0;
					return ta - tb;
				},
			},
		],
		[],
	);

	const table = useReactTable({
		data,
		columns,
		state: { sorting },
		onSortingChange: setSorting,
		getCoreRowModel: getCoreRowModel(),
		getSortedRowModel: getSortedRowModel(),
	});

	const sortedRows = table.getRowModel().rows;
	const rows = sortedRows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

	// ── Toolbar state derived from the query / sort ──────────────────
	// Values are stable registry ids; display names belong only in the menu.
	// Deriving options from filtered sessions would erase them on selection.
	const harnesses = useMemo(
		() => [...availableHarnesses].sort((a, b) => a.display_name.localeCompare(b.display_name)),
		[availableHarnesses],
	);

	const rawPlatform = serverQuery.filters.platform?.trim();
	const matchedHarness = rawPlatform
		? harnesses.find(
				(h) =>
					h.name.toLowerCase() === rawPlatform.toLowerCase() ||
					h.display_name.toLowerCase() === rawPlatform.toLowerCase(),
			)
		: undefined;
	const harnessValue = matchedHarness?.name ?? rawPlatform ?? "all";

	const sortValue = useMemo(() => {
		const s = sorting[0];
		if (!s) return "custom";
		if (s.id === "first_event_time") return s.desc ? "newest" : "oldest";
		if (s.id === "tokens" && s.desc) return "tokens";
		if (s.id === "activity" && s.desc) return "activity";
		return "custom";
	}, [sorting]);

	const onSortChange = useCallback((value: string) => {
		if (value === "newest") setSorting([{ id: "first_event_time", desc: true }]);
		else if (value === "oldest") setSorting([{ id: "first_event_time", desc: false }]);
		else if (value === "tokens") setSorting([{ id: "tokens", desc: true }]);
		else if (value === "activity") setSorting([{ id: "activity", desc: true }]);
		setPage(0);
	}, []);

	// ── Summary bar figures ─────────────────────────────────────────
	const todaySessions = summary?.today_sessions ?? allSessions.length;
	const totalSessions = summary?.total_sessions ?? allSessions.length;
	const activeCount = useMemo(
		() => allSessions.filter((s) => s.is_active).length,
		[allSessions],
	);
	const activeInViewCount = useMemo(
		() => filteredSessions.filter((s) => s.is_active).length,
		[filteredSessions],
	);
	const mineInViewCount = useMemo(
		() => filteredSessions.filter(isMine).length,
		[filteredSessions, isMine],
	);
	const toolCalls = useMemo(
		() => allSessions.reduce((sum, s) => sum + (s.tool_result_count ?? 0), 0),
		[allSessions],
	);
	const observedTokens = useMemo(
		() => allSessions.reduce((sum, s) => sum + tokenTotal(s), 0),
		[allSessions],
	);
	const latestEvent = useMemo(() => {
		let bestTs: string | undefined;
		let best = 0;
		for (const s of allSessions) {
			if (!s.last_event_time) continue;
			const t = toDate(s.last_event_time).getTime();
			if (t > best && t < TS_UPPER_BOUND_MS) {
				best = t;
				bestTs = s.last_event_time;
			}
		}
		return bestTs;
	}, [allSessions]);
	const ingestFresh =
		!!latestEvent &&
		Date.now() - toDate(latestEvent).getTime() < INGEST_FRESH_WINDOW_MS;

	const copySearchLink = useCallback(() => {
		const url = `${window.location.origin}${pathname}${searchValue ? `?search=${encodeURIComponent(searchValue)}` : ""}`;
		// The clipboard API is absent on insecure origins, so the optional call
		// has to be guarded rather than chained straight into `.then`.
		const write = navigator.clipboard?.writeText(url);
		if (!write) {
			toast.error("Could not copy the link to this view");
			return;
		}
		write
			.then(() => toast.success("Search link copied"))
			.catch(() => toast.error("Could not copy the search link"));
	}, [pathname, searchValue]);

	const tracesEmptyState = (() => {
		switch (emptyStateKind) {
			case "search":
				return (
					<EmptyState
						icon={Search}
						title="No traces match your search"
						description={`No traces found for "${truncateQuery(parsedQuery.text)}". Try a different search term.`}
						actionLabel="Clear search"
						onAction={clearSearch}
					/>
				);
			case "filter":
				return (
					<EmptyState
						icon={SlidersHorizontal}
						title="No results for this filter"
						description="No traces match the selected filters. Try adjusting your filter criteria or clear all filters."
						actionLabel="Clear filters"
						onAction={clearFilters}
					/>
				);
			case "first-time":
				return (
					<EmptyState
						icon={Activity}
						title="No traces yet"
						description="Traces are created automatically when tools are invoked through Observal. Connect a harness to start collecting traces."
						actionLabel="Connect your first harness"
						actionHref={DOCS_QUICKSTART_URL}
					/>
				);
			default:
				return (
					<EmptyState
						icon={Activity}
						title="No matching sessions"
						description="No sessions match the current view."
					/>
				);
		}
	})();

	const openRow = useCallback(
		(sessionId: string) => {
			router.navigate({ to: "/traces/$traceId", params: { traceId: sessionId } });
		},
		[router],
	);

	return (
		<>
			<PageHeader
				title="Traces"
				breadcrumbs={[
					{ label: "Dashboard", href: "/dashboard" },
					{ label: "Traces" },
				]}
				actionButtonsRight={
					<button
						type="button"
						className="rounded-sm text-muted-foreground transition-colors hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
						onClick={() => helpCtx.openHelp({ pageKey: "traces" })}
						title="Telemetry documentation"
					>
						<HelpCircle className="h-4 w-4" />
					</button>
				}
			/>
			<div className="page-body mx-auto w-full">
				{isLoading ? (
					<TableSkeleton rows={8} cols={8} />
				) : isError ? (
					<ErrorState message={error?.message} onRetry={() => refetch()} />
				) : allSessions.length === 0 ? (
					tracesEmptyState
				) : (
					<div className="animate-in">

						<SummaryBar className="mb-3.5">
							<SummaryBarStatus
								tone={ingestFresh ? "success" : "warning"}
								pulse={ingestFresh}
								title={
									ingestFresh
										? "Telemetry is arriving normally"
										: "Telemetry has not arrived recently"
								}
								detail={`Last event received ${relTime(latestEvent)}`}
							/>
							<SummaryBarStat label="Sessions today" value={todaySessions} />
							<SummaryBarStat
								label="Active now"
								value={activeCount}
								className="[&_strong]:text-success"
							/>
							<SummaryBarStat label="Tool calls" value={toolCalls} />
							<SummaryBarStat
								label="Observed tokens"
								value={fmtTokens(observedTokens)}
							/>
						</SummaryBar>


						<div className="mb-2.5 grid grid-cols-2 gap-2 min-[760px]:grid-cols-[minmax(300px,1fr)_auto_auto]">
							<SearchField
								ref={searchRef}
								size="sm"
								mono
								kbd="⌘ K"
								className="col-span-2 min-[760px]:col-span-1"
								value={searchValue}
								onValueChange={handleSearch}
								placeholder="days:7"
								aria-label="Search traces"
								title="Filter with platform: user: agent: model: days: status: or free text"
							/>
							<Select
								value={harnessValue}
								onValueChange={(v) =>
									applyQuery(
										withFilterToken(searchValue, "platform", v === "all" ? null : v),
									)
								}
							>
								<SelectTrigger
									aria-label="Filter by harness"
									className="min-w-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring min-[760px]:w-[160px]"
								>
									<SelectValue placeholder="All harnesses" />
								</SelectTrigger>
								<SelectContent>
									<SelectItem value="all">All harnesses</SelectItem>
									{/* A `platform:` token typed by hand may not match any
									    harness on this page; keep it selectable so the control
									    still reflects the query it is bound to. */}
									{harnessValue !== "all" && !harnesses.some((h) => h.name === harnessValue) && (
										<SelectItem value={harnessValue}>{harnessValue}</SelectItem>
									)}
									{harnesses.map((h) => (
										<SelectItem key={h.name} value={h.name}>
											{h.display_name}
										</SelectItem>
									))}
								</SelectContent>
							</Select>
							<Select value={sortValue} onValueChange={onSortChange}>
								<SelectTrigger
									aria-label="Sort sessions"
									className="min-w-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring min-[760px]:w-[160px]"
								>
									<SelectValue placeholder="Newest first" />
								</SelectTrigger>
								<SelectContent>
									<SelectItem value="newest">Newest first</SelectItem>
									<SelectItem value="oldest">Oldest first</SelectItem>
									<SelectItem value="tokens">Most tokens</SelectItem>
									<SelectItem value="activity">Most activity</SelectItem>
									{sortValue === "custom" && (
										<SelectItem value="custom">Custom order</SelectItem>
									)}
								</SelectContent>
							</Select>
						</div>


						<Tabs
							value={savedView}
							onValueChange={(v) => {
								setSavedView(v as SavedView);
								setPage(0);
							}}
						>
							<div className="mb-[13px] flex items-center gap-1">
								<TabsList variant="ghost" className="flex-1" aria-label="Saved views">
									<TabsTrigger variant="ghost" value="all">
										All sessions · {totalSessions}
									</TabsTrigger>
									<TabsTrigger variant="ghost" value="active">
										Active · {activeInViewCount}
									</TabsTrigger>
									<TabsTrigger variant="ghost" value="mine">
										Mine · {mineInViewCount}
									</TabsTrigger>
								</TabsList>
								<Button
									type="button"
									variant="ghost"
									size="sm"
									onClick={copySearchLink}
									title="Copy a link for this search"
									className="shrink-0 gap-1.5 rounded-md px-3 py-[7px] text-3xs text-info hover:bg-surface-raised hover:text-info"
								>
									<span aria-hidden="true">＋</span>
									Copy search link
								</Button>
							</div>


							<TabsContent value={savedView} className="mt-0">
								<Card className="overflow-hidden">
									{rows.length === 0 ? (
										tracesEmptyState
									) : (
										<>

											<div className="hidden overflow-x-auto min-[1020px]:block">
												<Table className="w-full min-w-[1120px] table-fixed border-collapse">
													<TableHeader className="bg-surface-raised [&_tr]:border-b-0">
														{table.getHeaderGroups().map((hg) => (
															<TableRow
																key={hg.id}
																className="border-0 hover:bg-transparent"
															>
																{hg.headers.map((header) => {
																	const sorted = header.column.getIsSorted();
																	return (
																		<TableHead
																			key={header.id}
																			className={cn(
													"h-10 select-none px-4 text-left",
													TABLE_COLUMN_WIDTHS[header.column.id],
												)}
																			// Sorting was bound to onClick on the <th>,
																			// so it could not be reached by keyboard at
																			// all. A real button gets focus, Enter and
																			// Space for free, and aria-sort tells a
																			// screen reader the current direction.
																			aria-sort={
																				sorted === "asc"
																					? "ascending"
																					: sorted === "desc"
																						? "descending"
																						: "none"
																			}
																		>
																			<button
																				type="button"
																				onClick={header.column.getToggleSortingHandler()}
																				className="inline-flex items-center gap-1 rounded-sm text-2xs font-medium uppercase tracking-[0.05em] text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
																			>
																				{flexRender(
																					header.column.columnDef.header,
																					header.getContext(),
																				)}
																				<SortIcon sorted={sorted} />
																			</button>
																		</TableHead>
																	);
																})}
															</TableRow>
														))}
													</TableHeader>
													<TableBody>
														{rows.map((row) => (
															<TableRow
																key={row.id}
																className={cn(
																	"cursor-pointer border-b-0 border-t border-border transition-colors duration-100 hover:bg-surface-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
																	// session. It repeats what the Live badge
																	// already says, so it is never the only cue.
																	row.original.is_active && "bg-success/[0.06]",
																)}
																// The row was click-only, so every cell outside
																// the session link was a dead target for keyboard
																// and screen-reader users. The link stays the
																// primary affordance; this makes the row itself
																// reachable too.
																tabIndex={0}
																onClick={() => openRow(row.original.session_id)}
																onKeyDown={(e) => {
																	if (e.target !== e.currentTarget) return;
																	if (e.key === "Enter" || e.key === " ") {
																		e.preventDefault();
																		openRow(row.original.session_id);
																	}
																}}
															>
																{row.getVisibleCells().map((cell) => (
																	<TableCell
																		key={cell.id}
																		className="h-[60px] px-4 py-2.5 align-middle text-2xs text-muted-foreground"
																	>
																		{flexRender(
																			cell.column.columnDef.cell,
																			cell.getContext(),
																		)}
																	</TableCell>
																))}
															</TableRow>
														))}
													</TableBody>
												</Table>
											</div>


											<ul className="min-[1020px]:hidden">
												{rows.map((row) => {
													const s = row.original;
													return (
														<li key={row.id}>
															<div
																role="button"
																tabIndex={0}
																onClick={() => openRow(s.session_id)}
																onKeyDown={(e) => {
																	if (e.target !== e.currentTarget) return;
																	if (e.key === "Enter" || e.key === " ") {
																		e.preventDefault();
																		openRow(s.session_id);
																	}
																}}
																className={cn(
																	"grid cursor-pointer grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-2 border-t border-border px-4 py-3.5 transition-colors duration-100 hover:bg-surface-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring min-[560px]:grid-cols-[minmax(0,1fr)_92px_82px_74px] min-[560px]:gap-y-0 min-[560px]:px-[22px]",
																	s.is_active && "bg-success/[0.06]",
																)}
															>
																<div className="min-w-0">
																	<h3 className="truncate text-xs font-medium text-foreground">
																		{sessionLabel(s)}
																	</h3>
																	<p className="mt-[3px] truncate text-3xs text-muted-foreground">
																		<span className="font-mono text-3xs">
																			{s.session_id}
																		</span>{" "}
																		· {derivePlatform(s)} ·{" "}
																		{s.agent_name || shortModel(s.model) || "—"}
																	</p>
																</div>
																<span className="justify-self-start">
																	<TraceStatus row={s} />
																</span>
																<span className="font-mono text-3xs leading-[1.5] tabular-nums text-muted-foreground">
																	{fmtDuration(s.first_event_time, s.last_event_time)}
																	<br />
																	{s.tool_result_count ?? 0} tools
																</span>
																<span
																	className="font-mono text-3xs tabular-nums text-muted-foreground"
																	title={absTime(s.first_event_time)}
																>
																	{relTime(s.first_event_time)}
																</span>
															</div>
														</li>
													);
												})}
											</ul>
										</>
									)}


									<footer className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-4 py-3 text-3xs text-muted-foreground">
										<span>
											Showing{" "}
											<span className="tabular-nums">{rows.length}</span> of{" "}
											<span className="tabular-nums">{sortedRows.length}</span>{" "}
											session{sortedRows.length === 1 ? "" : "s"} · Updated live
										</span>
										<div className="flex gap-1.5">
											<Button
												type="button"
												variant="ghost"
												className="text-xs disabled:cursor-not-allowed disabled:opacity-40"
												disabled={page === 0}
												onClick={() => setPage(page - 1)}
											>
												Previous
											</Button>
											<Button
												type="button"
												variant="ghost"
												className="text-xs disabled:cursor-not-allowed disabled:opacity-40"
												disabled={(page + 1) * PAGE_SIZE >= sortedRows.length}
												onClick={() => setPage(page + 1)}
											>
												Next
											</Button>
										</div>
									</footer>
								</Card>
							</TabsContent>
						</Tabs>
					</div>
				)}
			</div>
		</>
	);
}
