// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

/**
 * Sticky settings navigation: one button per area plus the settings search.
 *
 * Clicking an area scrolls to it. Hovering (or ArrowDown on a focused area) opens
 * a menu of that area's sections; choosing one scrolls to the section.
 */
import { ChevronDown, ChevronLeft, ChevronRight } from "lucide-react";
import { forwardRef, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { SearchField } from "@/components/ui/search-field";
import { cn } from "@/lib/utils";

export interface NavSection {
	id: string;
	title: string;
	description?: string;
	count: number;
	/** Advanced and danger sections are listed below a divider. */
	secondary?: boolean;
	danger?: boolean;
}

export interface NavArea {
	id: string;
	label: string;
	sections: NavSection[];
	attention?: boolean;
	dirty?: boolean;
}

interface SettingsNavProps {
	areas: NavArea[];
	activeArea: string;
	currentSection: string | null;
	query: string;
	onQueryChange: (value: string) => void;
	onQueryKeyDown: (event: KeyboardEvent<HTMLInputElement>) => void;
	hitCounts: Record<string, number> | null;
	onAreaSelect: (areaId: string) => void;
	onSectionSelect: (areaId: string, sectionId: string) => void;
}

const HOVER_OPEN_DELAY = 120;
const HOVER_CLOSE_DELAY = 180;

export const SettingsNav = forwardRef<HTMLDivElement, SettingsNavProps>(function SettingsNav(
	{ areas, activeArea, currentSection, query, onQueryChange, onQueryKeyDown, hitCounts, onAreaSelect, onSectionSelect },
	ref,
) {
	const [openArea, setOpenArea] = useState<string | null>(null);
	const [focusFirst, setFocusFirst] = useState(false);
	const buttonRefs = useRef<Record<string, HTMLButtonElement | null>>({});
	const openTimer = useRef<number | undefined>(undefined);
	const closeTimer = useRef<number | undefined>(undefined);
	const searchRef = useRef<HTMLInputElement>(null);
	const stripRef = useRef<HTMLDivElement>(null);
	const [edges, setEdges] = useState({ left: false, right: false });

	// The area strip scrolls sideways when it doesn't fit. Track which edges have
	// more areas beyond them so the arrow buttons only show when useful.
	useLayoutEffect(() => {
		const strip = stripRef.current;
		if (!strip) return;
		const update = () => {
			const left = strip.scrollLeft > 1;
			const right = strip.scrollLeft + strip.clientWidth < strip.scrollWidth - 1;
			setEdges((current) => (current.left === left && current.right === right ? current : { left, right }));
		};
		// A vertical wheel over the strip scrolls it sideways, until it reaches that end.
		const onWheel = (event: WheelEvent) => {
			if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
			const max = strip.scrollWidth - strip.clientWidth;
			if (max <= 0) return;
			const next = Math.min(max, Math.max(0, strip.scrollLeft + event.deltaY));
			if (next === strip.scrollLeft) return;
			event.preventDefault();
			strip.scrollLeft = next;
		};
		update();
		strip.addEventListener("scroll", update, { passive: true });
		strip.addEventListener("wheel", onWheel, { passive: false });
		const observer = new ResizeObserver(update);
		observer.observe(strip);
		return () => {
			strip.removeEventListener("scroll", update);
			strip.removeEventListener("wheel", onWheel);
			observer.disconnect();
		};
	}, [areas.length]);

	// Keep the active area visible inside the strip as the page scrolls.
	useEffect(() => {
		const strip = stripRef.current;
		const button = buttonRefs.current[activeArea];
		if (!strip || !button || strip.scrollWidth <= strip.clientWidth) return;
		const start = button.offsetLeft - strip.offsetLeft;
		const end = start + button.offsetWidth;
		if (start < strip.scrollLeft + 24) strip.scrollTo({ left: Math.max(0, start - 32), behavior: "smooth" });
		else if (end > strip.scrollLeft + strip.clientWidth - 24) strip.scrollTo({ left: end - strip.clientWidth + 32, behavior: "smooth" });
	}, [activeArea]);

	const scrollStrip = (direction: -1 | 1) => {
		const strip = stripRef.current;
		if (strip) strip.scrollBy({ left: direction * strip.clientWidth * 0.7, behavior: "smooth" });
	};

	const clearTimers = () => {
		window.clearTimeout(openTimer.current);
		window.clearTimeout(closeTimer.current);
	};
	const close = useCallback((refocus?: string) => {
		clearTimers();
		setOpenArea(null);
		setFocusFirst(false);
		if (refocus) buttonRefs.current[refocus]?.focus();
	}, []);
	// Only keyboard opens move focus into the menu; hover never does.
	const scheduleClose = () => {
		window.clearTimeout(closeTimer.current);
		closeTimer.current = window.setTimeout(() => {
			setOpenArea(null);
			setFocusFirst(false);
		}, HOVER_CLOSE_DELAY);
	};
	useEffect(() => clearTimers, []);

	// "/" focuses search from anywhere on the page.
	useEffect(() => {
		const onKey = (event: globalThis.KeyboardEvent) => {
			const target = event.target as HTMLElement | null;
			if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
			if (target?.closest("input, textarea, select, [contenteditable=true], [role=dialog]")) return;
			event.preventDefault();
			searchRef.current?.focus();
		};
		document.addEventListener("keydown", onKey);
		return () => document.removeEventListener("keydown", onKey);
	}, []);

	const index = areas.findIndex((area) => area.id === openArea);
	const searching = hitCounts !== null;

	const onButtonKeyDown = (event: KeyboardEvent<HTMLButtonElement>, position: number) => {
		const move = (to: number) => {
			event.preventDefault();
			close();
			buttonRefs.current[areas[(to + areas.length) % areas.length].id]?.focus();
		};
		if (event.key === "ArrowRight") move(position + 1);
		else if (event.key === "ArrowLeft") move(position - 1);
		else if (event.key === "Home") move(0);
		else if (event.key === "End") move(areas.length - 1);
		else if (event.key === "ArrowDown") {
			event.preventDefault();
			setFocusFirst(true);
			setOpenArea(areas[position].id);
		} else if (event.key === "Escape") close();
	};

	return (
		<div
			ref={ref}
			className="sticky top-[54px] z-20 -mx-4 mb-6 flex flex-col gap-2 bg-background px-4 py-3 sm:-mx-[30px] sm:px-[30px] md:flex-row md:items-stretch md:gap-3 md:py-3.5"
		>
			<nav aria-label="Jump to a settings area" className="relative min-w-0 flex-1">
				{edges.left && (
					<button
						type="button"
						tabIndex={-1}
						aria-hidden="true"
						onClick={() => scrollStrip(-1)}
						className="absolute inset-y-0 left-0 z-10 flex w-10 items-center justify-start rounded-l-[var(--radius-control)] bg-gradient-to-r from-surface-raised from-55% to-transparent pl-1.5 text-muted-foreground transition-colors hover:text-foreground"
					>
						<ChevronLeft className="size-4" />
					</button>
				)}
				{edges.right && (
					<button
						type="button"
						tabIndex={-1}
						aria-hidden="true"
						onClick={() => scrollStrip(1)}
						className="absolute inset-y-0 right-0 z-10 flex w-10 items-center justify-end rounded-r-[var(--radius-control)] bg-gradient-to-l from-surface-raised from-55% to-transparent pr-1.5 text-muted-foreground transition-colors hover:text-foreground"
					>
						<ChevronRight className="size-4" />
					</button>
				)}
				<div
					ref={stripRef}
					className="flex items-center gap-0.5 overflow-x-auto rounded-[var(--radius-control)] bg-surface-raised p-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
				>
					{areas.map((area, position) => {
						// Search results replace the page, so no area is "current" while searching.
						const active = area.id === activeArea && hitCounts === null;
						const expanded = area.id === openArea;
						const hits = hitCounts?.[area.id] ?? 0;
						return (
							<button
								key={area.id}
								ref={(node) => {
									buttonRefs.current[area.id] = node;
								}}
								type="button"
								aria-current={active ? "true" : undefined}
								aria-haspopup="menu"
								aria-expanded={expanded}
								data-settings-area-link={area.id}
								onPointerEnter={(event) => {
									if (event.pointerType !== "mouse") return;
									clearTimers();
									openTimer.current = window.setTimeout(() => {
										setFocusFirst(false);
										setOpenArea(area.id);
									}, openArea ? 0 : HOVER_OPEN_DELAY);
								}}
								onPointerLeave={(event) => {
									if (event.pointerType !== "mouse") return;
									window.clearTimeout(openTimer.current);
									scheduleClose();
								}}
								onClick={() => {
									close();
									onAreaSelect(area.id);
								}}
								onKeyDown={(event) => onButtonKeyDown(event, position)}
								className={cn(
									"inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-[var(--radius-lg)] px-3 py-[7px] text-xs font-medium text-muted-foreground transition-[color,background-color,box-shadow,opacity] duration-[120ms] hover:text-foreground md:flex-1",
									active && "bg-card text-foreground shadow-sm",
									expanded && !active && "bg-card/60 text-foreground",
									searching && hits === 0 && "opacity-45",
								)}
							>
								{area.label}
								{area.attention && (
									<>
										<span className="size-1.5 rounded-full bg-warning" aria-hidden="true" />
										<span className="sr-only">(needs attention)</span>
									</>
								)}
								{area.dirty && (
									<>
										<span className="size-1.5 rounded-full bg-foreground/55" aria-hidden="true" />
										<span className="sr-only">(unsaved changes)</span>
									</>
								)}
								{hits > 0 && (
									<span className="inline-flex min-w-[1.125rem] justify-center rounded-full bg-muted px-1 text-3xs tabular-nums text-muted-foreground">
										{hits}
									</span>
								)}
								<ChevronDown
									className={cn("-mr-1 size-3 opacity-55 transition-transform duration-200", expanded && "rotate-180 opacity-100")}
									aria-hidden="true"
								/>
							</button>
						);
					})}
				</div>
			</nav>
			<SearchField
				ref={searchRef}
				size="sm"
				value={query}
				onValueChange={onQueryChange}
				onKeyDown={onQueryKeyDown}
				placeholder="Find a setting"
				aria-label="Find a setting"
				aria-describedby="settings-search-help"
				name="settings-filter"
				autoComplete="off"
				spellCheck={false}
				kbd="/"
				containerClassName="order-first w-full md:order-none md:w-[clamp(14rem,22vw,20rem)] md:min-h-0"
			/>
			<span id="settings-search-help" className="sr-only">
				Searches setting names, descriptions and keys across every area. Press Escape to clear.
			</span>
			{openArea && index >= 0 && (
				<SectionMenu
					area={areas[index]}
					anchor={buttonRefs.current[areas[index].id]}
					currentSection={areas[index].id === activeArea && !searching ? currentSection : null}
					focusFirst={focusFirst}
					onPointerEnter={() => window.clearTimeout(closeTimer.current)}
					onPointerLeave={scheduleClose}
					onClose={(refocus) => close(refocus ? areas[index].id : undefined)}
					onMoveArea={(step) => {
						const next = areas[(index + step + areas.length) % areas.length];
						buttonRefs.current[next.id]?.focus();
						setFocusFirst(true);
						setOpenArea(next.id);
					}}
					onSelect={(sectionId) => {
						const areaId = areas[index].id;
						close();
						onSectionSelect(areaId, sectionId);
					}}
				/>
			)}
		</div>
	);
});

function SectionMenu({
	area,
	anchor,
	currentSection,
	focusFirst,
	onPointerEnter,
	onPointerLeave,
	onClose,
	onMoveArea,
	onSelect,
}: {
	area: NavArea;
	anchor: HTMLButtonElement | null | undefined;
	currentSection: string | null;
	focusFirst: boolean;
	onPointerEnter: () => void;
	onPointerLeave: () => void;
	onClose: (refocus: boolean) => void;
	onMoveArea: (step: number) => void;
	onSelect: (sectionId: string) => void;
}) {
	const menuRef = useRef<HTMLDivElement>(null);
	const [position, setPosition] = useState<{ left: number; top: number } | null>(null);

	useLayoutEffect(() => {
		const place = () => {
			const menu = menuRef.current;
			if (!anchor || !menu) return;
			const rect = anchor.getBoundingClientRect();
			const left = Math.min(Math.max(8, rect.left + rect.width / 2 - menu.offsetWidth / 2), window.innerWidth - menu.offsetWidth - 8);
			setPosition({ left, top: rect.bottom });
		};
		place();
		window.addEventListener("resize", place);
		window.addEventListener("scroll", place, true);
		return () => {
			window.removeEventListener("resize", place);
			window.removeEventListener("scroll", place, true);
		};
	}, [anchor, area.id]);

	useEffect(() => {
		if (focusFirst) menuRef.current?.querySelector<HTMLButtonElement>("[role=menuitem]")?.focus();
	}, [focusFirst, area.id]);

	// Close on outside press. The callback is read through a ref so the listener
	// isn't re-bound on every parent render.
	const onCloseRef = useRef(onClose);
	useEffect(() => {
		onCloseRef.current = onClose;
	});
	useEffect(() => {
		const onDown = (event: PointerEvent) => {
			const target = event.target as Node;
			if (menuRef.current?.contains(target) || anchor?.contains(target)) return;
			onCloseRef.current(false);
		};
		document.addEventListener("pointerdown", onDown);
		return () => document.removeEventListener("pointerdown", onDown);
	}, [anchor]);

	const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
		const items = Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>("[role=menuitem]") ?? []);
		const at = items.indexOf(document.activeElement as HTMLButtonElement);
		const focus = (i: number) => {
			event.preventDefault();
			items[(i + items.length) % items.length]?.focus();
		};
		if (event.key === "ArrowDown") focus(at + 1);
		else if (event.key === "ArrowUp") focus(at - 1);
		else if (event.key === "Home") focus(0);
		else if (event.key === "End") focus(items.length - 1);
		else if (event.key === "Escape") {
			event.preventDefault();
			onClose(true);
		} else if (event.key === "Tab") onClose(true);
		else if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
			event.preventDefault();
			onMoveArea(event.key === "ArrowRight" ? 1 : -1);
		}
	};

	const primary = area.sections.filter((section) => !section.secondary);
	const secondary = area.sections.filter((section) => section.secondary);
	const item = (section: NavSection) => (
		<button
			key={section.id}
			type="button"
			role="menuitem"
			tabIndex={-1}
			aria-current={section.id === currentSection ? "true" : undefined}
			onClick={() => onSelect(section.id)}
			className="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-3 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-surface-raised focus-visible:bg-surface-raised focus-visible:outline-none"
		>
			<span className="min-w-0">
				<strong
					className={cn(
						"flex items-center gap-1.5 text-sm font-medium",
						section.danger && "text-destructive",
						section.id === currentSection && "before:size-[5px] before:rounded-full before:bg-foreground before:content-['']",
					)}
				>
					{section.title}
				</strong>
				{section.description && <span className="mt-px block truncate text-xs text-muted-foreground">{section.description}</span>}
			</span>
			<span className="whitespace-nowrap text-2xs tabular-nums text-muted-foreground">
				{section.count} {section.count === 1 ? "setting" : "settings"}
			</span>
		</button>
	);

	return createPortal(
		<div
			ref={menuRef}
			role="menu"
			aria-label={`${area.label} sections`}
			onPointerEnter={onPointerEnter}
			onPointerLeave={(event) => {
				if (event.pointerType === "mouse") onPointerLeave();
			}}
			onKeyDown={onKeyDown}
			style={position ? { left: position.left, top: position.top } : { visibility: "hidden" }}
			// The transparent top padding bridges the gap between the area button and the menu, so hover survives the trip.
			className="fixed z-40 w-80 max-w-[calc(100vw-2rem)] pt-2.5"
		>
			<div className="max-h-[min(28rem,calc(100vh-8rem))] overflow-auto rounded-xl border border-border bg-popover p-1.5 text-popover-foreground shadow-lg animate-in fade-in-0 slide-in-from-top-1">
			<div className="px-2.5 pt-1.5 pb-1 text-2xs font-medium uppercase tracking-[0.06em] text-muted-foreground">{area.label}</div>
			{primary.map(item)}
			{secondary.length > 0 && (
				<>
					<div role="separator" className="mx-1 my-1.5 h-px bg-border" />
					{secondary.map(item)}
				</>
			)}
			</div>
		</div>,
		document.body,
	);
}
