// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

/**
 * Layout primitives for the admin settings page.
 *
 * Every setting uses the same row anatomy: label, optional key and description on
 * the left, the control in a fixed-width column on the right. Rows sit in one card
 * per section, and a footer slides in when a card holds unsaved typed values.
 */
import { ChevronRight, HelpCircle, Loader2, Power, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { useHelp } from "@/components/wiki/help-context";
import { SECTION_DOCS } from "@/lib/docs-map";
import { cn } from "@/lib/utils";

import { areaDomId, rowDomId, sectionDomId } from "./settings-helpers";

/** Sections scroll to just below the sticky page header and settings nav. */
const SCROLL_MARGIN = "scroll-mt-[var(--settings-stick,8rem)]";

export function RestartBadge() {
	return (
		<span
			className="inline-flex items-center gap-1 rounded-full bg-warning/12 px-2 py-0.5 text-2xs font-medium text-warning"
			title="Changing this setting requires an API restart"
		>
			<Power className="size-3" aria-hidden="true" />
			Restart
		</span>
	);
}

export function CautionBadge() {
	return (
		<span className="ml-1 rounded-full bg-warning/12 px-2 py-0.5 text-3xs font-medium uppercase tracking-[0.04em] text-warning">
			Caution
		</span>
	);
}

export function StatusBadge({ tone, children }: { tone: "neutral" | "warning" | "success"; children: ReactNode }) {
	return (
		<span
			className={cn(
				"inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-2xs font-medium",
				tone === "neutral" && "bg-surface-raised text-muted-foreground",
				tone === "warning" && "bg-warning/12 text-warning",
				tone === "success" && "bg-success/12 text-success",
			)}
		>
			<span className="size-1.5 rounded-full bg-current" aria-hidden="true" />
			{children}
		</span>
	);
}

function DocsButton({ title }: { title: string }) {
	const { openHelp } = useHelp();
	if (!SECTION_DOCS[title]) return null;
	return (
		<button
			type="button"
			onClick={() => openHelp({ sectionTitle: title })}
			className="inline-flex min-h-7 items-center gap-1.5 rounded-lg px-2 text-xs text-muted-foreground transition-colors hover:bg-surface-raised hover:text-foreground"
			aria-label={`Open ${title} documentation`}
		>
			<HelpCircle className="size-3.5" aria-hidden="true" />
			Docs
		</button>
	);
}

/** One settings area: a titled block in the single-page layout. */
export function SettingsArea({ id, label, sectionCount, children }: { id: string; label: string; sectionCount: number; children: ReactNode }) {
	return (
		<section
			id={areaDomId(id)}
			data-settings-area={id}
			aria-labelledby={`${areaDomId(id)}-title`}
			tabIndex={-1}
			className={cn("outline-none [&+&]:mt-5", SCROLL_MARGIN)}
		>
			<div className="mb-5 flex items-baseline justify-between gap-4 border-b border-border pb-2.5">
				<h2 id={`${areaDomId(id)}-title`} className="text-xl tracking-[-0.025em]">
					{label}
				</h2>
				<span className="text-xs text-muted-foreground">
					{sectionCount} {sectionCount === 1 ? "section" : "sections"}
				</span>
			</div>
			{children}
		</section>
	);
}

/** A section: heading outside, one card of rows inside. */
export function SettingsGroup({
	id,
	title,
	description,
	docsTitle,
	caution,
	danger,
	tools,
	children,
}: {
	id: string;
	title: string;
	description?: ReactNode;
	docsTitle?: string;
	caution?: boolean;
	danger?: boolean;
	tools?: ReactNode;
	children: ReactNode;
}) {
	return (
		<section id={sectionDomId(id)} data-settings-section={id} aria-labelledby={`${sectionDomId(id)}-title`} className={cn("mb-9", SCROLL_MARGIN)}>
			<div className="mb-3 flex items-start justify-between gap-4">
				<div className="min-w-0">
					<h3
						id={`${sectionDomId(id)}-title`}
						tabIndex={-1}
						className={cn("flex items-center gap-2 text-base outline-none", danger && "text-destructive")}
					>
						{danger && <TriangleAlert className="size-3.5" aria-hidden="true" />}
						{title}
						{caution && <CautionBadge />}
					</h3>
					{description && <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>}
				</div>
				{(tools || docsTitle) && (
					<div className="flex shrink-0 items-center gap-1">
						{tools}
						{docsTitle && <DocsButton title={docsTitle} />}
					</div>
				)}
			</div>
			{children}
		</section>
	);
}

/** A collapsed section for rarely touched settings. */
export function AdvancedGroup({
	id,
	title,
	description,
	count,
	children,
}: {
	id: string;
	title: string;
	description?: string;
	count: number;
	children: ReactNode;
}) {
	return (
		<details
			id={sectionDomId(id)}
			data-settings-section={id}
			className={cn("group/adv mb-9 overflow-hidden rounded-xl border border-border bg-card shadow-sm", SCROLL_MARGIN)}
		>
			<summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-[1.125rem] py-3.5 transition-colors hover:bg-muted/45 group-open/adv:border-b group-open/adv:border-border [&::-webkit-details-marker]:hidden">
				<span className="min-w-0">
					<strong className="block text-sm font-medium">{title}</strong>
					{description && <span className="mt-0.5 block text-xs text-muted-foreground">{description}</span>}
				</span>
				<span className="inline-flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
					{count} settings
					<ChevronRight className="size-4 transition-transform duration-200 group-open/adv:rotate-90" aria-hidden="true" />
				</span>
			</summary>
			{children}
		</details>
	);
}

export function SettingsCard({
	children,
	footer,
	flush,
	className,
}: {
	children: ReactNode;
	footer?: ReactNode;
	/** Rendered inside an AdvancedGroup, which already draws the outline. */
	flush?: boolean;
	className?: string;
}) {
	return (
		<div
			data-settings-card
			className={cn(
				"divide-y divide-border overflow-hidden transition-shadow duration-[1200ms]",
				!flush && "rounded-xl border border-border bg-card shadow-sm",
				className,
			)}
		>
			{children}
			{footer}
		</div>
	);
}

type RowLayout = "control" | "inline" | "full";

export function SettingRow({
	settingKey,
	label,
	htmlFor,
	showKey,
	badges,
	labelNote,
	meta,
	description,
	error,
	layout = "control",
	hidden,
	children,
}: {
	settingKey: string;
	label: ReactNode;
	htmlFor?: string;
	showKey?: boolean;
	badges?: ReactNode;
	labelNote?: ReactNode;
	meta?: ReactNode;
	description?: ReactNode;
	error?: string;
	layout?: RowLayout;
	hidden?: boolean;
	children?: ReactNode;
}) {
	if (hidden) return null;
	const LabelTag = htmlFor ? "label" : "span";
	return (
		<div
			id={rowDomId(settingKey)}
			data-setting-row={settingKey}
			className={cn(
				"grid items-center gap-x-8 gap-y-2.5 px-4 py-3.5 transition-[background-color,box-shadow] duration-[1200ms] sm:px-[1.125rem]",
				layout === "control" && "grid-cols-1 sm:grid-cols-[minmax(0,1fr)_var(--settings-control,clamp(17rem,30vw,28rem))]",
				layout === "inline" && "grid-cols-[minmax(0,1fr)_auto]",
				layout === "full" && "grid-cols-1",
				SCROLL_MARGIN,
			)}
		>
			<div className="min-w-0">
				<div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm font-medium">
					<LabelTag htmlFor={htmlFor} className={htmlFor ? "cursor-pointer" : undefined}>
						{label}
					</LabelTag>
					{labelNote && <span className="text-xs font-normal text-muted-foreground">{labelNote}</span>}
					{badges}
				</div>
				{showKey && <div className="mt-px break-all font-mono text-[11px] text-muted-foreground/85">{settingKey}</div>}
				{meta && <p className="mt-0.5 text-xs font-medium">{meta}</p>}
				{description && <p className="mt-0.5 text-pretty text-xs text-muted-foreground">{description}</p>}
				{error && (
					<p className="mt-1 text-xs text-destructive" role="alert">
						{error}
					</p>
				)}
			</div>
			{children !== undefined && (
				<div
					className={cn(
						"flex min-w-0 items-center gap-2",
						layout === "control" && "sm:justify-end",
						layout === "inline" && "justify-end",
					)}
				>
					{children}
				</div>
			)}
		</div>
	);
}

/** Switch with its state word, used for every boolean row. */
export function SwitchStateLabel({ checked }: { checked: boolean }) {
	return <span className="hidden min-w-[4.5rem] text-right text-xs text-muted-foreground sm:inline">{checked ? "Enabled" : "Disabled"}</span>;
}

/**
 * Footer for a card with typed values. Hidden until something changes, unless
 * `persistent` (forms that already had an explicit save button keep it visible).
 */
export function CardFooter({
	dirtyCount,
	invalid,
	persistent,
	allowClean,
	saving,
	saveLabel = "Save",
	onSave,
	onDiscard,
	extra,
}: {
	dirtyCount: number;
	invalid?: boolean;
	persistent?: boolean;
	/** Keep Save enabled with no changes (an explicit consent save, for example). */
	allowClean?: boolean;
	saving?: boolean;
	saveLabel?: string;
	onSave: () => void;
	onDiscard?: () => void;
	extra?: ReactNode;
}) {
	const dirty = dirtyCount > 0;
	const open = dirty || persistent;
	const note = invalid
		? "Fix the highlighted value to save"
		: dirty
			? `${dirtyCount} unsaved ${dirtyCount === 1 ? "change" : "changes"}`
			: "No changes";
	return (
		<div
			className={cn(
				"grid transition-[grid-template-rows] duration-200 ease-[var(--ease-out-expo)]",
				open ? "grid-rows-[1fr]" : "grid-rows-[0fr] !border-t-0",
			)}
			aria-hidden={!open}
			inert={!open}
		>
			<div className="overflow-hidden">
				<div className="flex flex-wrap items-center justify-end gap-2 bg-surface-raised/45 px-4 py-2.5 sm:px-[1.125rem]">
					<span className="mr-auto inline-flex items-center gap-2 text-xs text-muted-foreground" aria-live="polite">
						<span className={cn("size-1.5 rounded-full", dirty ? "bg-warning" : "bg-muted-foreground/50")} aria-hidden="true" />
						{note}
					</span>
					{extra}
					{onDiscard && (
						<Button type="button" variant="ghost" size="sm" onClick={onDiscard} disabled={!dirty || saving}>
							Discard
						</Button>
					)}
					<Button type="button" size="sm" onClick={onSave} disabled={(!dirty && !allowClean) || invalid || saving}>
						{saving && <Loader2 className="animate-spin" aria-hidden="true" />}
						{saveLabel}
					</Button>
				</div>
			</div>
		</div>
	);
}
