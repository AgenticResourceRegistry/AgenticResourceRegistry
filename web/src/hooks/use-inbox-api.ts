// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { inbox } from "@/lib/api";
import type { InboxCountOptions, InboxFilters } from "@/lib/types";

const INBOX_KEY = ["inbox"] as const;

export function useInbox(filters: InboxFilters = {}) {
	return useQuery({
		queryKey: [...INBOX_KEY, "list", filters],
		queryFn: () => inbox.list(filters),
		retry: false,
	});
}

/**
 * Badge counts, and optionally the filter sheet's breakdown.
 *
 * Every option is part of the key, not just the request. The sheet's numbers
 * describe one folder under one search and toggle set, so a cached response
 * for another view must never be shown beside this one.
 */
export function useInboxCounts(enabled = true, opts: InboxCountOptions = {}) {
	return useQuery({
		queryKey: [
			...INBOX_KEY,
			"count",
			opts.facets ?? false,
			opts.facetState ?? null,
			opts.unread ?? false,
			opts.actionRequired ?? false,
			opts.q ?? "",
		],
		queryFn: () => inbox.counts(opts),
		// The badge should feel live without hammering the API. A minute is
		// short enough that a review assignment does not sit unseen for long.
		// Polling was chosen over SSE deliberately: server-sent events would
		// need a connection story for the multi-replica ECS/Fargate topology,
		// and a badge does not justify one.
		refetchInterval: 60_000,
		refetchOnWindowFocus: true,
		enabled,
		retry: false,
	});
}

export function useInboxItem(id: string | null) {
	return useQuery({
		queryKey: [...INBOX_KEY, "detail", id],
		queryFn: () => inbox.detail(id as string),
		enabled: Boolean(id),
		retry: false,
	});
}

/**
 * Every mutation invalidates the whole inbox namespace rather than patching
 * one row: marking an item read changes the badge counts and can change which
 * filtered lists it belongs to, and a partial update would leave those stale.
 */
function useInboxMutation<TArgs>(
	fn: (args: TArgs) => Promise<unknown>,
	errorMessage: string,
) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: fn,
		onSuccess: () => {
			qc.invalidateQueries({ queryKey: INBOX_KEY });
		},
		// Without this the row simply does not change and the click reads as a
		// no-op, which is indistinguishable from a broken button.
		onError: () => {
			toast.error(errorMessage);
		},
	});
}

export function useMarkRead() {
	return useInboxMutation(
		(id: string) => inbox.read(id),
		"Could not mark that as read. Please try again.",
	);
}

export function useMarkUnread() {
	return useInboxMutation(
		(id: string) => inbox.unread(id),
		"Could not mark that as unread. Please try again.",
	);
}

export function useMarkDone() {
	return useInboxMutation(
		(id: string) => inbox.done(id),
		"Could not resolve that item. Please try again.",
	);
}

export function useDismissItem() {
	return useInboxMutation(
		(id: string) => inbox.dismiss(id),
		"Could not dismiss that item. Please try again.",
	);
}

export function useReopenItem() {
	return useInboxMutation(
		(id: string) => inbox.reopen(id),
		"Could not reopen that item. Please try again.",
	);
}

export function useReadAll() {
	return useInboxMutation(
		(filters: InboxFilters) => inbox.readAll(filters),
		"Could not mark those as read. Please try again.",
	);
}

export type BulkAction = "read" | "unread" | "done" | "dismiss" | "reopen";

const BULK_FNS: Record<BulkAction, (id: string) => Promise<unknown>> = {
	read: inbox.read,
	unread: inbox.unread,
	done: inbox.done,
	dismiss: inbox.dismiss,
	reopen: inbox.reopen,
};

/**
 * Apply one action to a checkbox selection.
 *
 * The API is per-item by design — every transition appends its own history row
 * — so a selection of N is N requests. `allSettled` rather than `all`: one
 * failure must not discard the transitions that did land, and the count of
 * failures is surfaced instead of a success toast over a partial result.
 */
export function useBulkAction() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: async ({ ids, action }: { ids: string[]; action: BulkAction }) => {
			const results = await Promise.allSettled(ids.map((id) => BULK_FNS[action](id)));
			return {
				total: ids.length,
				failed: results.filter((r) => r.status === "rejected").length,
			};
		},
		onSuccess: ({ total, failed }) => {
			qc.invalidateQueries({ queryKey: INBOX_KEY });
			if (failed > 0) {
				toast.error(`${failed} of ${total} could not be updated.`);
			}
		},
		onError: () => {
			toast.error("Could not update the selection. Please try again.");
		},
	});
}
