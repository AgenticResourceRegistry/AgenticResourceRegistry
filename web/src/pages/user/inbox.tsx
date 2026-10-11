// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import {
	ArrowLeftRight,
	ArrowUpCircle,
	Check,
	CheckCheck,
	ChevronDown,
	CircleCheck,
	CircleDot,
	CircleX,
	Copy,
	Eye,
	EyeOff,
	FileDiff,
	GitPullRequest,
	Inbox as InboxIcon,
	ListFilter,
	Loader2,
	Megaphone,
	MessageSquare,
	RotateCcw,
	Search,
	SlidersHorizontal,
	Sparkles,
	TriangleAlert,
	UserPlus,
	Users,
	X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Card } from "@/components/ui/card";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuLabel,
	DropdownMenuRadioGroup,
	DropdownMenuRadioItem,
	DropdownMenuSeparator,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { InsightCard, InsightList } from "@/components/ui/insight-card";
import type { InsightTone } from "@/components/ui/insight-card";
import {
	ListItem,
	ListItemCopy,
	ListItemLeading,
	ListItemSub,
	ListItemTitle,
	ListItemTrailing,
} from "@/components/ui/list-item";
import {
	Sheet,
	SheetContent,
	SheetDescription,
	SheetHeader,
	SheetTitle,
} from "@/components/ui/sheet";
import { SplitPane, SplitView } from "@/components/ui/split-view";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { PageHeader, PageIntro } from "@/components/layouts/page-header";
import { DashboardContent, DashboardShell } from "@/components/layouts/dashboard-shell";
import {
	useBulkAction,
	useDismissItem,
	useInbox,
	useInboxCounts,
	useInboxItem,
	useMarkDone,
	useMarkRead,
	useMarkUnread,
	useReadAll,
	useReopenItem,
} from "@/hooks/use-inbox-api";
import {
	INBOX_KIND_LABELS,
	INBOX_KIND_REASONS,
	INBOX_SUBJECT_LABELS,
	type InboxFilters,
	type InboxItem,
	type InboxKind,
	type InboxSort,
	type InboxState,
} from "@/lib/types";
import { cn } from "@/lib/utils";

// ── Presentation tables ────────────────────────────────────────────────

/**
 * The glyph and tint for each kind. The glyph is what makes the list scannable
 * without reading it; the tint drives the `InsightSymbol` in the detail pane,
 * so it is a design-system tone rather than a bare class.
 */
const KIND_META: Record<InboxKind, { icon: LucideIcon; tone: InsightTone }> = {
	review_requested: { icon: GitPullRequest, tone: "primary" },
	review_approved: { icon: CircleCheck, tone: "success" },
	review_rejected: { icon: CircleX, tone: "destructive" },
	review_comment: { icon: MessageSquare, tone: "info" },
	review_approval: { icon: CircleCheck, tone: "success" },
	review_dismissed: { icon: CircleX, tone: "warning" },
	review_ready: { icon: GitPullRequest, tone: "primary" },
	change_requested: { icon: FileDiff, tone: "warning" },
	team_join_requested: { icon: UserPlus, tone: "primary" },
	team_join_decided: { icon: Users, tone: "info" },
	team_created_pending: { icon: Users, tone: "warning" },
	ownership_transfer: { icon: ArrowLeftRight, tone: "warning" },
	update_available: { icon: ArrowUpCircle, tone: "info" },
	insight_ready: { icon: Sparkles, tone: "primary" },
	system_notice: { icon: Megaphone, tone: "info" },
};

/** Kinds whose detail panel really is a summary of a proposed change. */
const CHANGE_KINDS = new Set<InboxKind>(["review_requested", "change_requested"]);

const BUCKETS: { key: InboxState; label: string; icon: LucideIcon }[] = [
	{ key: "open", label: "Inbox", icon: InboxIcon },
	{ key: "done", label: "Done", icon: Check },
	{ key: "dismissed", label: "Dismissed", icon: X },
];

const SORT_LABELS: Record<InboxSort, string> = {
	newest: "Newest to oldest",
	oldest: "Oldest to newest",
};

type GroupMode = "date" | "type" | "none";

const GROUP_LABELS: Record<GroupMode, string> = {
	date: "Date",
	type: "Type",
	none: "No grouping",
};

// ── Small helpers ──────────────────────────────────────────────────────

/**
 * Split a server-built `action_url` into what Link needs, or refuse it.
 *
 * Two jobs. First, safety: the column is a free string and the API contract
 * does not constrain it, so a hostile value reaching Link would be an open
 * redirect out of a trusted surface. Rejected in order: anything not starting
 * with a single slash, protocol-relative `//host`, backslashes (browsers treat
 * them as separators, so `/\host` escapes too), control characters and spaces,
 * any first path segment outside the allowlist below, and `..` segments. The
 * allowlist is the decisive one — the set of routes the server links to is
 * small and known, so anything else is a bug or an injection.
 *
 * Second, correctness: these paths carry query strings that decide what the
 * destination renders — `?type=` picks the component type, `?tab=` picks the
 * review queue tab. Link takes those as a `search` object, not baked into
 * `to`, so a raw string would navigate to the path with the params dropped.
 */
const INTERNAL_ROOTS = new Set(["agents", "components", "review", "insights", "teamspaces"]);

function internalTarget(url: string | null): { to: string; search: Record<string, string> } | null {
	if (!url || !url.startsWith("/")) return null;

	// Reject anything that could leave this origin before the path is read.
	// `//evil.example` is protocol-relative, and a backslash is treated as a
	// separator by browsers, so `/\evil.example` escapes too. Control characters
	// and whitespace are rejected because they can be used to hide the real
	// target from anyone eyeballing the link.
	if (url.startsWith("//") || url.includes("\\")) return null;
	// Control characters and spaces are checked by code point rather than with
	// a regex literal, so this source file never has to contain one.
	if ([...url].some((ch) => ch.charCodeAt(0) <= 0x20 || ch.charCodeAt(0) === 0x7f)) return null;

	const [path, query] = url.split("?");

	// An allowlist rather than a denylist. The column is free text, and the set
	// of routes the server links to is small and known, so anything outside it
	// is a bug or an injection — either way not somewhere to navigate.
	const root = path.split("/")[1] ?? "";
	if (!INTERNAL_ROOTS.has(root)) return null;
	// `..` cannot climb out of the SPA, but it can address a route the server
	// never meant to name, so it is refused rather than normalised.
	if (path.split("/").includes("..")) return null;

	const search: Record<string, string> = {};
	if (query) {
		for (const [key, value] of new URLSearchParams(query)) search[key] = value;
	}
	return { to: path, search };
}

function plural(n: number, unit: string): string {
	return `${n} ${unit}${n === 1 ? "" : "s"} ago`;
}

function relativeTime(iso: string): string {
	const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60_000);
	if (mins < 1) return "just now";
	if (mins < 60) return plural(mins, "minute");
	const hours = Math.round(mins / 60);
	if (hours < 24) return plural(hours, "hour");
	const days = Math.round(hours / 24);
	if (days < 7) return plural(days, "day");
	const weeks = Math.round(days / 7);
	if (weeks < 5) return plural(weeks, "week");
	return plural(Math.round(days / 30), "month");
}

/** Where a row says it came from: `namespace/slug` when the subject has one. */
function subjectPath(item: InboxItem): string {
	if (item.subject_namespace && item.subject_slug) {
		return `${item.subject_namespace}/${item.subject_slug}`;
	}
	return item.subject_slug || INBOX_SUBJECT_LABELS[item.subject_type] || item.subject_type;
}

function initialsFor(item: InboxItem): string {
	const source = item.subject_namespace ?? item.title;
	const words = source.split(/[^\p{L}\p{N}]+/u).filter(Boolean);
	if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
	const letters = words[0] ?? "";
	return (letters.slice(0, 2) || "??").toUpperCase();
}

function dateBucket(iso: string): string {
	const then = new Date(iso).getTime();
	const now = new Date();
	const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
	const day = 86_400_000;
	if (then >= midnight) return "Today";
	if (then >= midnight - day) return "Yesterday";
	if (then >= midnight - 7 * day) return "This week";
	if (then >= midnight - 30 * day) return "This month";
	return "Older";
}

/**
 * Partition the page into labelled runs.
 *
 * A Map keyed on the label rather than a walk over adjacent rows: type
 * grouping revisits a label after others have appeared, and a sequential walk
 * would emit that label as several separate headings. Insertion order keeps
 * date groups in the order the sort already put them.
 */
function groupItems(items: InboxItem[], mode: GroupMode): { label: string; items: InboxItem[] }[] {
	if (mode === "none") return [{ label: "", items }];
	const groups = new Map<string, InboxItem[]>();
	for (const item of items) {
		const label =
			mode === "date"
				? dateBucket(item.created_at)
				: INBOX_SUBJECT_LABELS[item.subject_type] || item.subject_type;
		const bucket = groups.get(label);
		if (bucket) bucket.push(item);
		else groups.set(label, [item]);
	}
	return [...groups].map(([label, rows]) => ({ label, items: rows }));
}

function Avatar({ initials, className }: { initials: string; className?: string }) {
	return (
		<span
			aria-hidden="true"
			className={cn(
				"grid h-7 w-7 flex-none place-items-center rounded-full bg-foreground text-[10.5px] font-medium text-background",
				className,
			)}
		>
			{initials}
		</span>
	);
}

function GroupHeading({ children }: { children: ReactNode }) {
	return (
		<div className="px-[11px] pb-1 text-4xs font-medium uppercase tracking-[0.05em] text-muted-foreground">
			{children}
		</div>
	);
}

function PaneState({
	icon: Icon,
	tone = "text-muted-foreground",
	title,
	copy,
	children,
}: {
	icon: LucideIcon;
	tone?: string;
	title: string;
	copy?: string;
	children?: ReactNode;
}) {
	return (
		<div className="flex min-h-[220px] flex-col items-center justify-center gap-2 px-5 py-10 text-center">
			<Icon className={cn("h-6 w-6", tone)} aria-hidden="true" />
			<p className="text-xs font-medium">{title}</p>
			{copy && <p className="max-w-sm text-2xs leading-[1.45] text-muted-foreground">{copy}</p>}
			{children}
		</div>
	);
}

// ── Filter rail (lives in the filter sheet) ────────────────────────────

function RailRow({
	icon: Icon,
	label,
	count,
	active,
	onClick,
}: {
	icon?: LucideIcon;
	label: string;
	count?: number;
	active: boolean;
	onClick: () => void;
}) {
	return (
		<button
			type="button"
			onClick={onClick}
			aria-current={active ? "true" : undefined}
			className={cn(
				"flex w-full items-center gap-2 rounded-lg px-[11px] py-[7px] text-left text-xs transition-colors duration-[120ms] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
				active
					? "bg-surface-raised font-medium text-foreground ring-1 ring-border"
					: "text-muted-foreground hover:bg-surface-raised hover:text-foreground",
			)}
		>
			{Icon && <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />}
			<span className="min-w-0 flex-1 truncate">{label}</span>
			{count ? <span className="shrink-0 text-3xs tabular-nums">{count}</span> : null}
		</button>
	);
}

function InboxRail({
	bucket,
	unreadOnly,
	kind,
	subjectType,
	actionOnly,
	counts,
	onBucket,
	onUnreadOnly,
	onKind,
	onSubjectType,
	onActionOnly,
	onMarkAllRead,
	markAllDisabled,
	className,
}: {
	bucket: InboxState;
	unreadOnly: boolean;
	kind: InboxKind | undefined;
	subjectType: string | undefined;
	actionOnly: boolean;
	counts:
		| {
				open: number;
				done: number;
				dismissed: number;
				action_required: number;
				by_kind: Partial<Record<InboxKind, number>>;
				by_subject_type: Record<string, number>;
		  }
		| undefined;
	onBucket: (b: InboxState) => void;
	onUnreadOnly: (value: boolean) => void;
	onKind: (k: InboxKind | undefined) => void;
	onSubjectType: (t: string | undefined) => void;
	onActionOnly: (v: boolean) => void;
	onMarkAllRead: () => void;
	markAllDisabled: boolean;
	className?: string;
}) {
	const byKind = counts?.by_kind ?? {};
	const bySubject = counts?.by_subject_type ?? {};

	// Only kinds actually present in this bucket get a row; the full list lives
	// behind "More filters" so a rail with two kinds in it does not render ten
	// dead zeroes.
	const presentKinds = (Object.keys(byKind) as InboxKind[])
		.filter((k) => (byKind[k] ?? 0) > 0)
		.sort((a, b) => (byKind[b] ?? 0) - (byKind[a] ?? 0));
	const hiddenKinds = (Object.keys(INBOX_KIND_LABELS) as InboxKind[]).filter(
		(k) => !presentKinds.includes(k),
	);
	const subjectTypes = Object.keys(bySubject)
		.filter((t) => bySubject[t] > 0)
		.sort((a, b) => bySubject[b] - bySubject[a]);

	const bucketCount = (b: InboxState) =>
		b === "open" ? counts?.open : b === "done" ? counts?.done : counts?.dismissed;

	return (
		<aside data-testid="inbox-rail" className={cn("overflow-y-auto p-3", className)}>
			<nav className="grid gap-1">
				{BUCKETS.map((entry) => (
					<RailRow
						key={entry.key}
						icon={entry.icon}
						label={entry.label}
						count={bucketCount(entry.key)}
						active={bucket === entry.key}
						onClick={() => onBucket(entry.key)}
					/>
				))}
			</nav>

			<div className="my-3 border-t border-border" />

			<div className="px-[11px] pb-1 text-4xs font-medium uppercase tracking-[0.05em] text-muted-foreground">
				Filters
			</div>
			<nav className="grid gap-1">
				<RailRow
					icon={EyeOff}
					label="Unread"
					active={unreadOnly}
					onClick={() => onUnreadOnly(!unreadOnly)}
				/>
				<RailRow
					icon={CircleDot}
					label="Needs action"
					count={counts?.action_required}
					active={actionOnly}
					onClick={() => onActionOnly(!actionOnly)}
				/>
				{presentKinds.map((k) => (
					<RailRow
						key={k}
						icon={KIND_META[k]?.icon ?? Megaphone}
						label={INBOX_KIND_LABELS[k] ?? k.replaceAll("_", " ")}
						count={byKind[k]}
						active={kind === k}
						onClick={() => onKind(kind === k ? undefined : k)}
					/>
				))}
				{hiddenKinds.length > 0 && (
					<DropdownMenu>
						<DropdownMenuTrigger asChild>
							<button
								type="button"
								className="flex w-full items-center gap-2 rounded-lg px-[11px] py-[7px] text-left text-xs text-muted-foreground transition-colors duration-[120ms] hover:bg-surface-raised hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
							>
								<ListFilter className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
								<span className="flex-1 truncate">More filters</span>
							</button>
						</DropdownMenuTrigger>
						<DropdownMenuContent align="start" className="w-56">
							<DropdownMenuLabel>Filter by kind</DropdownMenuLabel>
							<DropdownMenuSeparator />
							{hiddenKinds.map((k) => (
								<DropdownMenuItem key={k} onSelect={() => onKind(k)}>
									{INBOX_KIND_LABELS[k]}
								</DropdownMenuItem>
							))}
						</DropdownMenuContent>
					</DropdownMenu>
				)}
			</nav>

			{subjectTypes.length > 0 && (
				<>
					<div className="my-3 border-t border-border" />
					<div className="px-[11px] pb-1 text-4xs font-medium uppercase tracking-[0.05em] text-muted-foreground">
						Types
					</div>
					<nav className="grid gap-1">
						{subjectTypes.map((t) => (
							<RailRow
								key={t}
								label={INBOX_SUBJECT_LABELS[t] || t}
								count={bySubject[t]}
								active={subjectType === t}
								onClick={() => onSubjectType(subjectType === t ? undefined : t)}
							/>
						))}
					</nav>
				</>
			)}

			<div className="my-3 border-t border-border" />
			<DropdownMenu>
				<DropdownMenuTrigger asChild>
					<button
						type="button"
						className="flex w-full items-center gap-1 rounded-lg px-[11px] py-[7px] text-left text-xs text-muted-foreground transition-colors duration-[120ms] hover:bg-surface-raised hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
					>
						<span className="flex-1 truncate">Manage notifications</span>
						<ChevronDown className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
					</button>
				</DropdownMenuTrigger>
				<DropdownMenuContent align="start" className="w-56">
					<DropdownMenuItem disabled={markAllDisabled} onSelect={() => onMarkAllRead()}>
						<CheckCheck className="mr-2 h-4 w-4" />
						Mark these as read
					</DropdownMenuItem>
					<DropdownMenuSeparator />
					<DropdownMenuItem
						onSelect={() => {
							onKind(undefined);
							onSubjectType(undefined);
							onActionOnly(false);
						}}
					>
						Clear filters
					</DropdownMenuItem>
				</DropdownMenuContent>
			</DropdownMenu>
		</aside>
	);
}

// ── Row ────────────────────────────────────────────────────────────────

function HoverAction({
	label,
	icon: Icon,
	onClick,
}: {
	label: string;
	icon: LucideIcon;
	onClick: () => void;
}) {
	return (
		<Tooltip>
			<TooltipTrigger asChild>
				<button
					type="button"
					aria-label={label}
					onClick={onClick}
					className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors duration-[120ms] hover:bg-card hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
				>
					<Icon className="h-3.5 w-3.5" aria-hidden="true" />
				</button>
			</TooltipTrigger>
			<TooltipContent>{label}</TooltipContent>
		</Tooltip>
	);
}

/** Inbox row with sibling selection and action controls. */
function ItemRow({
	item,
	checked,
	bulkActive,
	selected,
	onCheck,
	onSelect,
}: {
	item: InboxItem;
	checked: boolean;
	bulkActive: boolean;
	selected: boolean;
	onCheck: (value: boolean) => void;
	onSelect: () => void;
}) {
	const markRead = useMarkRead();
	const markUnread = useMarkUnread();
	const markDone = useMarkDone();
	const dismiss = useDismissItem();
	const reopen = useReopenItem();

	const showCheckbox = checked || bulkActive;

	return (
		<li className="group relative min-w-0">
			<ListItem
				selected={selected}
				onClick={onSelect}
				className="min-w-0 overflow-hidden pr-[108px]"
			>
				<ListItemLeading className="w-7 justify-center">

					<Avatar
						initials={initialsFor(item)}
						className={cn(
							"transition-opacity duration-[120ms]",
							showCheckbox ? "opacity-0" : "group-hover:opacity-0",
						)}
					/>
				</ListItemLeading>

				<ListItemCopy>
					<ListItemTitle className={cn(!item.read && "font-semibold")}>{item.title}</ListItemTitle>
					<ListItemSub>
						{subjectPath(item)} · {relativeTime(item.created_at)}
					</ListItemSub>
					{/* The dot and the pill below are hidden while the hover cluster is
					    up, so the state is spelled out here where nothing hides it. */}
					<span className="sr-only">
						{item.read ? "Read" : "Unread"}
						{item.action_required && item.state === "open" ? ", needs action" : ""}
					</span>
				</ListItemCopy>

				<ListItemTrailing
					aria-hidden="true"
					className="gap-2 transition-opacity duration-[120ms] group-hover:opacity-0"
				>
					{item.action_required && item.state === "open" && (
						<TriangleAlert className="h-3.5 w-3.5 shrink-0 text-warning" />
					)}
				</ListItemTrailing>
			</ListItem>

			{/* Centred in the avatar slot: 11px row padding + (28 - 16) / 2. */}
			<Checkbox
				checked={checked}
				onCheckedChange={(value) => onCheck(value === true)}
				aria-label={`Select ${item.title}`}
				className={cn(
					"absolute top-1/2 left-[17px] z-10 -translate-y-1/2 bg-surface-raised transition-opacity duration-[120ms]",
					showCheckbox
						? "opacity-100"
						: "pointer-events-none opacity-0 group-hover:pointer-events-auto group-hover:opacity-100 focus-visible:pointer-events-auto focus-visible:opacity-100",
				)}
			/>

			{/* The row reserves this space, so actions never overlap its copy. */}
			<div className="pointer-events-none absolute inset-y-px right-px z-10 flex items-center gap-0.5 rounded-r-[7px] bg-surface-raised px-2 opacity-0 transition-opacity duration-[120ms] group-hover:pointer-events-auto group-hover:opacity-100 focus-within:pointer-events-auto focus-within:opacity-100">
				{item.state === "open" ? (
					<>
						<HoverAction label="Done" icon={Check} onClick={() => markDone.mutate(item.id)} />
						<HoverAction label="Dismiss" icon={X} onClick={() => dismiss.mutate(item.id)} />
					</>
				) : (
					<HoverAction label="Reopen" icon={RotateCcw} onClick={() => reopen.mutate(item.id)} />
				)}
				<HoverAction
					label={item.read ? "Mark as unread" : "Mark as read"}
					icon={item.read ? EyeOff : Eye}
					onClick={() => (item.read ? markUnread.mutate(item.id) : markRead.mutate(item.id))}
				/>
			</div>
		</li>
	);
}

// ── Detail pane ────────────────────────────────────────────────────────

function Panel({
	title,
	className,
	children,
}: {
	title: string;
	className?: string;
	children: ReactNode;
}) {
	return (
		<Card className={cn("min-w-0 p-5", className)}>
			<div className="text-base font-medium">{title}</div>
			{children}
		</Card>
	);
}

function DetailPane({ itemId, fallback }: { itemId: string; fallback: InboxItem | undefined }) {
	const { data: detail, isLoading, isError, refetch } = useInboxItem(itemId);
	const markDone = useMarkDone();
	const dismiss = useDismissItem();
	const reopen = useReopenItem();

	const item = detail ?? fallback;

	if (isLoading && !item) {
		return (
			<div className="flex min-h-[220px] items-center justify-center">
				<Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-hidden="true" />
				<span className="sr-only">Loading notification</span>
			</div>
		);
	}
	// The query does not retry, so a failure leaves isLoading false and item
	// undefined. Falling back to the spinner here would leave it turning forever
	// with nothing on the way.
	if ((isError && !detail) || !item) {
		return (
			<PaneState
				icon={TriangleAlert}
				tone="text-destructive"
				title="Could not load this item"
				copy="It may have been removed, or the request failed."
			>
				<Button variant="ghost" onClick={() => refetch()}>
					Try again
				</Button>
			</PaneState>
		);
	}

	const meta = KIND_META[item.kind] ?? KIND_META.system_notice;
	const KindIcon = meta.icon;
	const target = internalTarget(item.action_url);
	const openLabel = CHANGE_KINDS.has(item.kind) ? "Open review" : "Open";
	const panelTitle = CHANGE_KINDS.has(item.kind) ? "What changed" : "Details";

	const copyCommand = async () => {
		if (!item.action_command) return;
		try {
			await navigator.clipboard.writeText(item.action_command);
			toast.success("Command copied");
		} catch {
			toast.error("Could not copy the command");
		}
	};

	return (
		<>
			<PageIntro
				size="pane"
				title={subjectPath(item)}
				subtitle={item.body ?? undefined}
			/>

			<Panel title={panelTitle} className="mt-[18px]">
				<InsightList className="mt-3">
					<InsightCard
						tone={meta.tone}
						symbol={<KindIcon />}
						title={item.title}
						copy={`${INBOX_KIND_REASONS[item.kind] ?? item.kind} · ${new Date(
							item.created_at,
						).toLocaleString()}${item.state !== "open" ? ` · ${item.state}` : ""}`}
					/>

					{item.action_required && item.state === "open" && (
						<InsightCard
							tone="warning"
							symbol="!"
							title="Action required"
							copy="This notification is waiting on you before it can move on."
						/>
					)}

					{item.action_command && (
						<InsightCard
							tone="primary"
							symbol="$"
							title="Run this yourself"
							/* Shown, never executed: the web app does not run commands. */
							copy={
								<code className="block overflow-x-auto whitespace-nowrap font-mono text-2xs">
									{item.action_command}
								</code>
							}
						>
							<Button
								variant="ghost"
								size="icon"
								className="h-7 w-7 shrink-0"
								onClick={copyCommand}
								aria-label="Copy command"
							>
								<Copy className="h-3 w-3" />
							</Button>
						</InsightCard>
					)}
				</InsightList>
			</Panel>


			<div className="mt-3.5 flex items-center gap-2 max-[760px]:w-full max-[760px]:flex-wrap">
				{target && (
					<Button asChild>
						<Link to={target.to} search={target.search}>
							{openLabel}
						</Link>
					</Button>
				)}
				{item.state === "open" ? (
					<>
						<Button
							variant="ghost"
							onClick={() => markDone.mutate(item.id)}
							disabled={markDone.isPending}
						>
							<Check className="h-3.5 w-3.5" />
							Done
						</Button>
						<Button
							variant="ghost"
							onClick={() => dismiss.mutate(item.id)}
							disabled={dismiss.isPending}
						>
							<X className="h-3.5 w-3.5" />
							Dismiss
						</Button>
					</>
				) : (
					<Button
						variant="ghost"
						onClick={() => reopen.mutate(item.id)}
						disabled={reopen.isPending}
					>
						<RotateCcw className="h-3.5 w-3.5" />
						Reopen
					</Button>
				)}
			</div>

			{detail && detail.history.length > 0 && (
				<Panel title="History" className="mt-3.5">
					<ol className="mt-3 grid gap-2">
						{detail.history.map((event) => (
							<li key={event.id} className="flex flex-wrap items-baseline gap-2 text-2xs">
								<span className="font-mono text-3xs text-muted-foreground">
									{new Date(event.created_at).toLocaleString()}
								</span>
								<span className="text-foreground">{event.event}</span>
								{event.detail && <span className="text-muted-foreground">— {event.detail}</span>}
							</li>
						))}
					</ol>
				</Panel>
			)}
		</>
	);
}

// ── Page ───────────────────────────────────────────────────────────────

export default function InboxPage() {
	const [bucket, setBucket] = useState<InboxState>("open");
	const [unreadOnly, setUnreadOnly] = useState(false);
	const [actionOnly, setActionOnly] = useState(false);
	const [kind, setKind] = useState<InboxKind | undefined>();
	const [subjectType, setSubjectType] = useState<string | undefined>();
	const [sort, setSort] = useState<InboxSort>("newest");
	const [group, setGroup] = useState<GroupMode>("date");
	const [search, setSearch] = useState("");
	const [query, setQuery] = useState("");
	const [page, setPage] = useState(1);
	const [selected, setSelected] = useState<Set<string>>(new Set());
	const [selectedId, setSelectedId] = useState<string | null>(null);
	const [railOpen, setRailOpen] = useState(false);
	const detailRef = useRef<HTMLDivElement>(null);

	// Typing must not fire a request per keystroke, and the delay has to clear
	// the page reset too or page 3 outlives the filter that produced it.
	useEffect(() => {
		const timer = setTimeout(() => {
			setQuery(search.trim());
			setPage(1);
		}, 300);
		return () => clearTimeout(timer);
	}, [search]);

	const listFilters: InboxFilters = useMemo(
		() => ({
			state: bucket,
			...(unreadOnly ? { unread: true } : {}),
			...(actionOnly ? { action_required: true } : {}),
			...(kind ? { kind } : {}),
			...(subjectType ? { subject_type: subjectType } : {}),
			...(query ? { q: query } : {}),
			sort,
		}),
		[bucket, unreadOnly, actionOnly, kind, subjectType, query, sort],
	);

	const { data, isLoading, isFetching, isError, refetch } = useInbox({ ...listFilters, page });
	const { data: counts } = useInboxCounts(true, { facets: true, facetState: bucket });
	const readAll = useReadAll();
	const bulk = useBulkAction();
	const markRead = useMarkRead();

	const items = useMemo(() => data?.items ?? [], [data]);
	// Trust the server's page size, not a copy of its default: the numbers below
	// must describe the response they annotate.
	const total = data?.total ?? 0;
	const pageSize = data?.page_size ?? 25;
	const firstRow = (page - 1) * pageSize + 1;
	const lastRow = (page - 1) * pageSize + items.length;
	// not the same as opening: this never marks anything read, which is why it
	// does not go through `openItem` below.
	useEffect(() => {
		if (items.length === 0) {
			if (selectedId !== null) setSelectedId(null);
			return;
		}
		if (!selectedId || !items.some((item) => item.id === selectedId)) {
			setSelectedId(items[0].id);
		}
	}, [items, selectedId]);

	// A selection is only meaningful for rows still on screen. Keeping ids from
	// a previous page would let a bulk action hit rows the user cannot see.
	const visibleSelected = useMemo(
		() => items.filter((item) => selected.has(item.id)).map((item) => item.id),
		[items, selected],
	);
	const allChecked = items.length > 0 && visibleSelected.length === items.length;
	const someChecked = visibleSelected.length > 0 && !allChecked;

	const resetPage = () => {
		setPage(1);
		setSelected(new Set());
	};

	const toggleOne = (id: string, value: boolean) => {
		setSelected((prev) => {
			const next = new Set(prev);
			if (value) next.add(id);
			else next.delete(id);
			return next;
		});
	};

	const runBulk = (action: "read" | "unread" | "done" | "dismiss" | "reopen") => {
		if (visibleSelected.length === 0) return;
		bulk.mutate({ ids: visibleSelected, action });
		setSelected(new Set());
	};

	/**
	 * Opening a row is the act of reading it. The API is idempotent here, but
	 * the guard keeps a click on an already-read row from writing a second
	 * history entry that says nothing happened.
	 *
	 * Below 900px the panes stack, so the detail the click just produced is off
	 * the bottom of the screen; bring it into view rather than leaving the
	 * click looking like it did nothing.
	 */
	const openItem = (item: InboxItem) => {
		setSelectedId(item.id);
		if (!item.read) markRead.mutate(item.id);
		if (typeof window !== "undefined" && window.matchMedia("(max-width: 899px)").matches) {
			const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
			requestAnimationFrame(() =>
				detailRef.current?.scrollIntoView({
					block: "start",
					behavior: reduced ? "auto" : "smooth",
				}),
			);
		}
	};

	const groups = useMemo(() => groupItems(items, group), [items, group]);
	const filtersActive = Boolean(kind || subjectType || actionOnly || query || unreadOnly);
	const selectedItem = items.find((item) => item.id === selectedId);

	const railProps = {
		bucket,
		unreadOnly,
		kind,
		subjectType,
		actionOnly,
		counts,
		onBucket: (b: InboxState) => {
			setBucket(b);
			resetPage();
		},
		onUnreadOnly: (value: boolean) => {
			setUnreadOnly(value);
			resetPage();
		},
		onKind: (k: InboxKind | undefined) => {
			setKind(k);
			resetPage();
		},
		onSubjectType: (t: string | undefined) => {
			setSubjectType(t);
			resetPage();
		},
		onActionOnly: (v: boolean) => {
			setActionOnly(v);
			resetPage();
		},
		onMarkAllRead: () => readAll.mutate(listFilters),
		markAllDisabled: readAll.isPending || items.length === 0,
	};

	return (
		<DashboardShell>
			<PageHeader title="Inbox" breadcrumbs={[{ label: "Inbox" }]} />

			<DashboardContent className="max-[760px]:px-4 max-[760px]:pt-4 max-[760px]:pb-4">
				<PageIntro className="max-[760px]:flex-col max-[760px]:items-start">
					<Button
						variant="ghost"
						onClick={() => setRailOpen(true)}
						aria-label="Search and filter inbox"
					>
						<SlidersHorizontal className="h-3.5 w-3.5" />
						Filter
					</Button>
					<Button
						variant="default"
						onClick={() => readAll.mutate(listFilters)}
						disabled={readAll.isPending || items.length === 0}
					>
						<CheckCheck className="h-3.5 w-3.5" />
						Mark all read
					</Button>
				</PageIntro>


				<SplitView>
					{/* ── Left pane: the notification stack ── */}
					<SplitPane className="flex flex-col">
						<div data-testid="inbox-feed" className="min-h-0 flex-1">
							{isLoading ? (
								<div className="flex min-h-[220px] items-center justify-center">
									<Loader2
										className="h-4 w-4 animate-spin text-muted-foreground"
										aria-hidden="true"
									/>
									<span className="sr-only">Loading notifications</span>
								</div>
							) : isError ? (
								/* The query does not retry, so a failure leaves isLoading false
								   and items empty. Without this branch the empty state claims
								   the user is all caught up, which is the opposite of true. */
								<PaneState
									icon={TriangleAlert}
									tone="text-destructive"
									title="Could not load your inbox"
									copy="The request failed, so this list is not showing anything — not even items you may have waiting."
								>
									<Button variant="ghost" onClick={() => refetch()}>
										Try again
									</Button>
								</PaneState>
							) : items.length === 0 ? (
								<PaneState
									icon={InboxIcon}
									title={filtersActive ? "No matching notifications" : "You are all caught up"}
									copy={
										filtersActive
											? "Try clearing a filter or searching for something else."
											: "Review assignments, decisions on your submissions, and update notices show up here."
									}
								/>
							) : (
								groups.map((section) => (
									<div key={section.label || "all"} className="mt-3 first:mt-0">
										{section.label && <GroupHeading>{section.label}</GroupHeading>}

										<ul className="grid min-w-0 gap-1">
											{section.items.map((item) => (
												<ItemRow
													key={item.id}
													item={item}
													checked={selected.has(item.id)}
													bulkActive={visibleSelected.length > 0}
													selected={item.id === selectedId}
													onCheck={(value) => toggleOne(item.id, value)}
													onSelect={() => openItem(item)}
												/>
											))}
										</ul>
									</div>
								))
							)}
						</div>

						{/* Bulk actions and paging, on a hairline at the foot of the pane. */}
						<div className="mt-3 border-t border-border pt-3">
							<div className="flex flex-wrap items-center gap-2">
								<Checkbox
									checked={allChecked ? true : someChecked ? "indeterminate" : false}
									onCheckedChange={(value) =>
										setSelected(value === true ? new Set(items.map((i) => i.id)) : new Set())
									}
									aria-label="Select all"
									disabled={items.length === 0}
								/>
								<span className="text-3xs text-muted-foreground">
									{visibleSelected.length > 0 ? `${visibleSelected.length} selected` : "Select all"}
								</span>
								<div className="ml-auto flex items-center gap-1">
									{visibleSelected.length > 0 ? (
										<>
											{bucket === "open" ? (
												<>
													<Button
														variant="ghost"
														size="sm"
														disabled={bulk.isPending}
														onClick={() => runBulk("done")}
													>
														<Check className="h-3.5 w-3.5" />
														Done
													</Button>
													<Button
														variant="ghost"
														size="sm"
														disabled={bulk.isPending}
														onClick={() => runBulk("dismiss")}
													>
														<X className="h-3.5 w-3.5" />
														Dismiss
													</Button>
												</>
											) : (
												<Button
													variant="ghost"
													size="sm"
													disabled={bulk.isPending}
													onClick={() => runBulk("reopen")}
												>
													<RotateCcw className="h-3.5 w-3.5" />
													Reopen
												</Button>
											)}
											<Button
												variant="ghost"
												size="sm"
												disabled={bulk.isPending}
												onClick={() => runBulk("read")}
											>
												<Eye className="h-3.5 w-3.5" />
												Read
											</Button>
										</>
									) : (
										<Button
											variant="ghost"
											size="sm"
											disabled={readAll.isPending || items.length === 0}
											onClick={() => readAll.mutate(listFilters)}
										>
											<CheckCheck className="h-3.5 w-3.5" />
											Mark all read
										</Button>
									)}
								</div>
							</div>

							{/* Without this, anything past the first page simply does not
							    exist as far as the web UI is concerned. */}
							{total > pageSize && (
								<div className="mt-2 flex items-center justify-between gap-2">
									<span className="text-3xs tabular-nums text-muted-foreground">
										{items.length > 0 ? `${firstRow}–${lastRow} of ${total}` : `0 of ${total}`}
									</span>
									<div className="flex gap-1">
										<Button
											variant="ghost"
											size="sm"
											disabled={page === 1 || isFetching}
											onClick={() => {
												setPage((p) => Math.max(1, p - 1));
												setSelected(new Set());
											}}
										>
											Previous
										</Button>
										<Button
											variant="ghost"
											size="sm"
											disabled={lastRow >= total || isFetching}
											onClick={() => {
												setPage((p) => p + 1);
												setSelected(new Set());
											}}
										>
											Next
										</Button>
									</div>
								</div>
							)}
						</div>
					</SplitPane>

					{/* ── Right pane: the selected notification ── */}
					<SplitPane ref={detailRef} data-testid="inbox-detail" className="scroll-mt-4">
						{selectedId ? (
							<DetailPane key={selectedId} itemId={selectedId} fallback={selectedItem} />
						) : (
							<PaneState
								icon={InboxIcon}
								title={filtersActive ? "No matching notifications" : "You are all caught up"}
								copy={
									filtersActive
										? "Try clearing a filter or searching for something else."
										: "Pick a notification on the left to see what it is asking for."
								}
							/>
						)}
					</SplitPane>
				</SplitView>
			</DashboardContent>

			{/* Search, sort, grouping and the folder/kind/type rail. */}
			<Sheet open={railOpen} onOpenChange={setRailOpen}>
				<SheetContent side="left" className="flex w-72 flex-col gap-0 p-0">
					<SheetHeader className="px-3 pt-3">
						<SheetTitle className="text-base font-medium">Search and filters</SheetTitle>
						<SheetDescription className="text-2xs">
							Folders, kinds, subject types, sorting, and grouping.
						</SheetDescription>
					</SheetHeader>

					<div className="grid gap-2 px-3 pt-3">
						<div className="relative">
							<Search
								className="pointer-events-none absolute top-1/2 left-2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
								aria-hidden="true"
							/>
							<Input
								value={search}
								onChange={(e) => setSearch(e.target.value)}
								placeholder="Search notifications"
								aria-label="Search notifications"
								className="h-[34px] rounded-lg pl-7 text-xs"
							/>
						</div>

						<div className="flex flex-wrap gap-1">
							<DropdownMenu>
								<DropdownMenuTrigger asChild>
									<Button variant="ghost" size="sm">
										Sort: {SORT_LABELS[sort]}
										<ChevronDown className="h-3.5 w-3.5" />
									</Button>
								</DropdownMenuTrigger>
								<DropdownMenuContent align="start">
									<DropdownMenuRadioGroup
										value={sort}
										onValueChange={(v) => {
											setSort(v as InboxSort);
											resetPage();
										}}
									>
										{(Object.keys(SORT_LABELS) as InboxSort[]).map((value) => (
											<DropdownMenuRadioItem key={value} value={value}>
												{SORT_LABELS[value]}
											</DropdownMenuRadioItem>
										))}
									</DropdownMenuRadioGroup>
								</DropdownMenuContent>
							</DropdownMenu>

							<DropdownMenu>
								<DropdownMenuTrigger asChild>
									<Button variant="ghost" size="sm">
										Group: {GROUP_LABELS[group]}
										<ChevronDown className="h-3.5 w-3.5" />
									</Button>
								</DropdownMenuTrigger>
								<DropdownMenuContent align="start">
									<DropdownMenuRadioGroup
										value={group}
										onValueChange={(v) => setGroup(v as GroupMode)}
									>
										{(Object.keys(GROUP_LABELS) as GroupMode[]).map((value) => (
											<DropdownMenuRadioItem key={value} value={value}>
												{GROUP_LABELS[value]}
											</DropdownMenuRadioItem>
										))}
									</DropdownMenuRadioGroup>
								</DropdownMenuContent>
							</DropdownMenu>
						</div>
					</div>

					<InboxRail {...railProps} className="min-h-0 flex-1" />
				</SheetContent>
			</Sheet>
		</DashboardShell>
	);
}
