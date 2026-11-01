// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useMemo, useState } from "react";
import { Loader2, Search } from "lucide-react";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Input } from "@/components/ui/input";
import { Popover, PopoverAnchor, PopoverContent } from "@/components/ui/popover";
import { useUserSearch } from "@/hooks/use-api";
import type { UserSearchResult } from "@/lib/types";
import { cn } from "@/lib/utils";

interface UserSearchInputProps {
	id?: string;
	value: string;
	onValueChange: (value: string) => void;
	onSelect: (user: UserSearchResult) => void;
	placeholder?: string;
	disabled?: boolean;
	className?: string;
	inputClassName?: string;
}

function initials(user: UserSearchResult): string {
	return (user.name || user.username || user.email).slice(0, 2).toUpperCase();
}

export function UserSearchInput({
	id,
	value,
	onValueChange,
	onSelect,
	placeholder = "Search by name, username, or email",
	disabled = false,
	className,
	inputClassName,
}: UserSearchInputProps) {
	const [open, setOpen] = useState(false);
	const [debounced, setDebounced] = useState(value);

	useEffect(() => {
		const timer = setTimeout(() => setDebounced(value), 180);
		return () => clearTimeout(timer);
	}, [value]);

	const query = debounced.trim();
	const enabled = open && query.length >= 2;
	const { data, isFetching } = useUserSearch(query, { enabled, limit: 8 });
	const results = useMemo(() => data ?? [], [data]);
	const showPopover = enabled && (isFetching || results.length > 0 || query.length >= 2);

	return (
		<Popover open={open && showPopover} onOpenChange={setOpen}>
			<PopoverAnchor asChild>
				<div className={cn("relative", className)}>
					<Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
					<Input
						id={id}
						placeholder={placeholder}
						value={value}
						onChange={(event) => {
							onValueChange(event.target.value);
							setOpen(true);
						}}
						onFocus={() => setOpen(true)}
						disabled={disabled}
						className={cn("w-full pl-8", inputClassName)}
					/>
				</div>
			</PopoverAnchor>
			<PopoverContent
				align="start"
				className="w-[var(--radix-popover-trigger-width)] min-w-[280px] p-1"
				onOpenAutoFocus={(event) => event.preventDefault()}
			>
				{isFetching ? (
					<div className="flex items-center gap-2 px-3 py-2 text-xs text-muted-foreground">
						<Loader2 className="h-3.5 w-3.5 animate-spin" />
						Searching users...
					</div>
				) : results.length === 0 ? (
					<div className="px-3 py-2 text-xs text-muted-foreground">No users found</div>
				) : (
					<div className="max-h-64 overflow-y-auto">
						{results.map((user) => (
							<button
								key={user.id}
								type="button"
								className="flex w-full items-center gap-2 rounded px-2 py-2 text-left hover:bg-accent focus:bg-accent focus:outline-none"
								onClick={() => {
									onSelect(user);
									setOpen(false);
								}}
							>
								<Avatar className="h-7 w-7">
									<AvatarImage src={user.avatar_url ?? undefined} alt="" />
									<AvatarFallback className="text-[10px]">{initials(user)}</AvatarFallback>
								</Avatar>
								<span className="min-w-0 flex-1">
									<span className="block truncate text-xs font-medium text-foreground">{user.name}</span>
									<span className="block truncate text-[11px] text-muted-foreground">
										{user.username ? `@${user.username} · ` : ""}{user.email}
									</span>
								</span>
								{!user.is_active && <span className="text-[10px] text-muted-foreground">inactive</span>}
							</button>
						))}
					</div>
				)}
			</PopoverContent>
		</Popover>
	);
}
