// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback } from "react";
import { X, Plus, Loader2, ArrowRightLeft } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { UserSearchInput } from "@/components/shared/user-search-input";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";

const API = "/api/v1";

function getAccessToken(): string | null {
	if (typeof window === "undefined") return null;
	return sessionStorage.getItem("observal_access_token");
}

export interface CoAuthor {
	id: string;
	email: string;
	username?: string | null;
	is_active?: boolean;
}

interface CoAuthorInputProps {
	/** Entity type: "agents" | "mcps" | "hooks" | "sandboxes" | "prompts" */
	entityType: string;
	/** UUID of the entity */
	entityId: string;
	/** Current co-authors list */
	coAuthors: CoAuthor[];
	/** Callback when list changes */
	onChange: (coAuthors: CoAuthor[]) => void;
	/** Whether the current user can manage co-authors */
	canManage?: boolean;
	/** Whether the current user can transfer ownership */
	canTransferOwnership?: boolean;
	/** Callback after ownership changes */
	onTransferOwnership?: () => void;
}

export function CoAuthorInput({
	entityType,
	entityId,
	coAuthors,
	onChange,
	canManage = true,
	canTransferOwnership = false,
	onTransferOwnership,
}: CoAuthorInputProps) {
	const [input, setInput] = useState("");
	const [selectedAddUserId, setSelectedAddUserId] = useState<string | null>(null);
	const [transferTarget, setTransferTarget] = useState("");
	const [selectedTransferUserId, setSelectedTransferUserId] = useState<string | null>(null);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const [addOpen, setAddOpen] = useState(false);
	const [transferOpen, setTransferOpen] = useState(false);
	const [confirmRemove, setConfirmRemove] = useState<CoAuthor | null>(null);

	const headers = useCallback(() => {
		const h: Record<string, string> = { "Content-Type": "application/json" };
		const token = getAccessToken();
		if (token) h.Authorization = `Bearer ${token}`;
		return h;
	}, []);

	const resetAdd = () => {
		setAddOpen(false);
		setInput("");
		setSelectedAddUserId(null);
		setError(null);
	};

	const resetTransfer = () => {
		setTransferOpen(false);
		setTransferTarget("");
		setSelectedTransferUserId(null);
		setError(null);
	};

	const executeAdd = useCallback(async () => {
		const value = input.trim();
		if (!value) return;
		setLoading(true);
		setError(null);

		try {
			const isEmail = value.includes("@") && !value.startsWith("@") && value.indexOf("@") < value.length - 1;
			const body = selectedAddUserId
				? { user_id: selectedAddUserId }
				: isEmail
					? { email: value.toLowerCase() }
					: { username: value.replace(/^@/, "") };

			const res = await fetch(`${API}/${entityType}/${entityId}/co-authors`, {
				method: "POST",
				headers: headers(),
				body: JSON.stringify(body),
			});

			if (!res.ok) {
				const data = await res.json().catch(() => ({}));
				setError(data.detail || `Failed to add co-author (${res.status})`);
				return;
			}

			const added: CoAuthor = await res.json();
			onChange([...coAuthors, added]);
			resetAdd();
		} catch {
			setError("Network error");
		} finally {
			setLoading(false);
		}
	}, [entityType, entityId, coAuthors, onChange, headers, input, selectedAddUserId]);

	const executeRemove = useCallback(
		async (userId: string) => {
			setLoading(true);
			setError(null);

			try {
				const h = headers();
				delete h["Content-Type"];
				const res = await fetch(`${API}/${entityType}/${entityId}/co-authors/${userId}`, {
					method: "DELETE",
					headers: h,
				});

				if (!res.ok) {
					const data = await res.json().catch(() => ({}));
					setError(data.detail || `Failed to remove co-author (${res.status})`);
					return;
				}

				onChange(coAuthors.filter((c) => c.id !== userId));
				setConfirmRemove(null);
			} catch {
				setError("Network error");
			} finally {
				setLoading(false);
			}
		},
		[entityType, entityId, coAuthors, onChange, headers],
	);

	const executeTransfer = useCallback(async () => {
		const target = transferTarget.trim().replace(/^@/, "");
		if (!target) return;
		setLoading(true);
		setError(null);

		try {
			const isEmail = target.includes("@") && target.indexOf("@") < target.length - 1;
			const body = selectedTransferUserId
				? { user_id: selectedTransferUserId }
				: isEmail
					? { email: target.toLowerCase() }
					: { username: target };
			const res = await fetch(`${API}/${entityType}/${entityId}/transfer-ownership`, {
				method: "POST",
				headers: headers(),
				body: JSON.stringify(body),
			});

			if (!res.ok) {
				const data = await res.json().catch(() => ({}));
				setError(data.detail || `Failed to transfer ownership (${res.status})`);
				return;
			}

			resetTransfer();
			onTransferOwnership?.();
		} catch {
			setError("Network error");
		} finally {
			setLoading(false);
		}
	}, [entityType, entityId, headers, onTransferOwnership, selectedTransferUserId, transferTarget]);

	return (
		<div className="space-y-3">
			<div className="flex items-center justify-between gap-3">
				<Label>Co-Authors</Label>
				{canManage && (
					<Button type="button" variant="outline" size="sm" className="h-8" onClick={() => setAddOpen(true)}>
						<Plus className="h-3.5 w-3.5" />
						Add
					</Button>
				)}
			</div>

			{coAuthors.length > 0 ? (
				<div className="flex flex-wrap gap-2">
					{coAuthors.map((author) => (
						<Badge key={author.id} variant="secondary" className="flex items-center gap-1 px-2 py-1">
							<span className="text-xs">{author.username || author.email}</span>
							{canManage && (
								<button
									type="button"
									onClick={() => setConfirmRemove(author)}
									className="ml-1 rounded-full p-0.5 hover:bg-muted-foreground/20"
									disabled={loading}
								>
									<X className="h-3 w-3" />
								</button>
							)}
						</Badge>
					))}
				</div>
			) : (
				<p className="text-xs text-muted-foreground">No co-authors</p>
			)}

			{canTransferOwnership && (
				<div className="border-t border-border pt-3">
					<div className="flex items-center justify-between gap-3">
						<div className="space-y-0.5">
							<p className="text-sm font-medium">Transfer owner</p>
							<p className="text-xs text-muted-foreground">Move ownership to another username.</p>
						</div>
						<Button type="button" variant="outline" size="sm" className="h-8" onClick={() => setTransferOpen(true)}>
							<ArrowRightLeft className="h-3.5 w-3.5" />
							Transfer
						</Button>
					</div>
				</div>
			)}

			{error && !addOpen && !transferOpen && <p className="text-xs text-destructive">{error}</p>}

			<Dialog open={addOpen} onOpenChange={(open) => { if (!open) resetAdd(); else setAddOpen(true); }}>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>Add co-author</DialogTitle>
						<DialogDescription>
							Co-authors can edit, publish, and manage this item with owner-level access.
						</DialogDescription>
					</DialogHeader>
					<div className="space-y-2">
						<Label htmlFor="co-author-user">Name, username, or email</Label>
						<UserSearchInput
							id="co-author-user"
							placeholder="Search people"
							value={input}
							onValueChange={(value) => {
								setInput(value);
								setSelectedAddUserId(null);
								setError(null);
							}}
							onSelect={(user) => {
								setInput(user.username ? `@${user.username}` : user.email);
								setSelectedAddUserId(user.id);
								setError(null);
							}}
							disabled={loading}
						/>
						{error && <p className="text-xs text-destructive">{error}</p>}
					</div>
					<DialogFooter>
						<Button variant="outline" onClick={resetAdd} disabled={loading}>Cancel</Button>
						<Button onClick={executeAdd} disabled={loading || !input.trim()}>
							{loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
							Add co-author
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>

			<Dialog open={!!confirmRemove} onOpenChange={(open) => { if (!open) setConfirmRemove(null); }}>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>Remove co-author</DialogTitle>
						<DialogDescription>
							Remove <span className="font-medium text-foreground">{confirmRemove?.username || confirmRemove?.email}</span> as a co-author? They will lose edit and publish access immediately.
						</DialogDescription>
					</DialogHeader>
					<DialogFooter>
						<Button variant="outline" onClick={() => setConfirmRemove(null)} disabled={loading}>Cancel</Button>
						<Button variant="destructive" onClick={() => confirmRemove && executeRemove(confirmRemove.id)} disabled={loading}>
							{loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
							Remove
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>

			<Dialog open={transferOpen} onOpenChange={(open) => { if (!open) resetTransfer(); else setTransferOpen(true); }}>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>Transfer ownership</DialogTitle>
						<DialogDescription>
							Transfer ownership to another user. You will lose owner-only controls immediately.
						</DialogDescription>
					</DialogHeader>
					<div className="space-y-2">
						<Label htmlFor="transfer-owner-user">New owner</Label>
						<UserSearchInput
							id="transfer-owner-user"
							placeholder="Search people"
							value={transferTarget}
							onValueChange={(value) => {
								setTransferTarget(value);
								setSelectedTransferUserId(null);
								setError(null);
							}}
							onSelect={(user) => {
								setTransferTarget(user.username ? `@${user.username}` : user.email);
								setSelectedTransferUserId(user.id);
								setError(null);
							}}
							disabled={loading}
						/>
						{error && <p className="text-xs text-destructive">{error}</p>}
					</div>
					<DialogFooter>
						<Button variant="outline" onClick={resetTransfer} disabled={loading}>Cancel</Button>
						<Button onClick={executeTransfer} disabled={loading || !transferTarget.trim()}>
							{loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
							Transfer owner
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
		</div>
	);
}
