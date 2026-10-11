// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-License-Identifier: Apache-2.0

export type InboxKind =
	| "review_requested"
	| "review_approved"
	| "review_rejected"
	| "review_comment"
	| "review_approval"
	| "review_dismissed"
	| "review_ready"
	| "change_requested"
	| "team_join_requested"
	| "team_join_decided"
	| "team_created_pending"
	| "ownership_transfer"
	| "update_available"
	| "insight_ready"
	| "system_notice";

export type InboxState = "open" | "done" | "dismissed";

export interface InboxItem {
	id: string;
	kind: InboxKind;
	state: InboxState;
	/** Independent of `state` — an item can be read and still be open work. */
	read: boolean;
	read_at: string | null;
	action_required: boolean;
	title: string;
	body: string | null;
	subject_type: string;
	subject_id: string | null;
	subject_namespace: string | null;
	subject_slug: string | null;
	action_url: string | null;
	action_command: string | null;
	actor_id: string | null;
	team_id: string | null;
	payload: Record<string, unknown>;
	created_at: string;
	resolved_at: string | null;
}

export interface InboxItemEvent {
	id: string;
	event: string;
	actor_id: string | null;
	detail: string | null;
	created_at: string;
}

export interface InboxItemDetail extends InboxItem {
	history: InboxItemEvent[];
}

export interface InboxListResponse {
	items: InboxItem[];
	total: number;
	page: number;
	page_size: number;
}

export interface InboxCounts {
	unread: number;
	action_required: number;
	open: number;
	done: number;
	dismissed: number;
	/** Both are empty unless the request asked for facets. */
	by_kind: Partial<Record<InboxKind, number>>;
	by_subject_type: Record<string, number>;
}

export type InboxSort = "newest" | "oldest";

export interface InboxFilters {
	state?: InboxState;
	kind?: InboxKind;
	action_required?: boolean;
	unread?: boolean;
	subject_type?: string;
	/** Free text, matched server-side against title, body, namespace and slug. */
	q?: string;
	sort?: InboxSort;
	page?: number;
	page_size?: number;
}

/**
 * The short "why you got this" tag on the right of a row.
 *
 * Written out rather than lower-casing {@link INBOX_KIND_LABELS}, because the
 * two answer different questions. A label names the thing ("Changes needed");
 * a reason explains the delivery ("a reviewer asked for changes"), and for
 * several kinds the natural phrasing is not the label in lower case.
 */
export const INBOX_KIND_REASONS: Record<InboxKind, string> = {
	review_requested: "review requested",
	review_approved: "approved",
	review_rejected: "changes needed",
	review_comment: "commented",
	review_approval: "review approved",
	review_dismissed: "submission dismissed",
	review_ready: "ready to publish",
	change_requested: "changes requested",
	team_join_requested: "join request",
	team_join_decided: "join decided",
	team_created_pending: "awaiting approval",
	ownership_transfer: "transfer offered",
	update_available: "new version",
	insight_ready: "insight ready",
	system_notice: "system",
};

/**
 * Plural display names for `subject_type`, which is an open string column
 * rather than an enum. Anything unmapped falls back to the raw value, so a
 * subject type added server-side degrades to readable instead of blank.
 */
export const INBOX_SUBJECT_LABELS: Record<string, string> = {
	agent: "Agents",
	mcp: "MCPs",
	skill: "Skills",
	hook: "Hooks",
	prompt: "Prompts",
	sandbox: "Sandboxes",
	team: "Teamspaces",
	insight_report: "Insight reports",
	system: "System",
};

export const INBOX_KIND_LABELS: Record<InboxKind, string> = {
	review_requested: "Review requested",
	review_approved: "Approved",
	review_rejected: "Changes needed",
	review_comment: "Comment",
	review_approval: "Review approval",
	review_dismissed: "Dismissed submission",
	review_ready: "Ready to publish",
	change_requested: "Changes requested",
	team_join_requested: "Join request",
	team_join_decided: "Join decision",
	team_created_pending: "Teamspace pending",
	ownership_transfer: "Ownership transfer",
	update_available: "Update available",
	insight_ready: "Insight ready",
	system_notice: "System notice",
};
