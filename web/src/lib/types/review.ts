// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

export type ReviewState = "open" | "changes_requested" | "approved" | "published" | "closed";
export type ReviewVerdict = "comment" | "approve" | "request_changes";

export interface EligibleReviewer {
  id: string;
  name: string;
  username: string;
  role: "reviewer" | "admin" | "super_admin" | "user";
}

export interface ReviewGate {
  ready: boolean;
  approvals: number;
  required: number;
  policy_source: string;
  requirements: string[];
  outstanding_requests?: number;
  blocking_checks?: ReviewGateCheck[];
  /** Open items that stop a normal publish (approvals, required checks, integrity rules). */
  blockers?: string[];
  /** Blockers not even a super admin override skips. */
  hard_blockers?: string[];
}

export interface ReviewGateCheck {
  id: string;
  name: string;
  status: string;
  details?: string[] | string | null;
}

export interface ReviewSummary {
  id: string;
  number: number;
  title: string;
  subject_type: "agent" | "mcp" | "skill" | "hook" | "prompt" | "sandbox";
  subject_id: string;
  version: string;
  team_id: string | null;
  state: ReviewState;
  author_id: string | null;
  author_name?: string | null;
  head_revision: number;
  updated_at: string;
  gate: ReviewGate;
  threads: { total: number; unresolved: number };
  checks: Record<CheckStatus, number>;
  depends_on: number[];
  requested_from_me: boolean;
  my_last_submission: { verdict: ReviewVerdict; revision_id: string } | null;
}

export interface ReviewDetail extends ReviewSummary {
  body: string;
  base_version_id: string | null;
  base_version?: string | null;
  head_revision_id: string;
  opened_at: string;
  closed_at: string | null;
  closed_reason: string | null;
  published_at: string | null;
  published_by: string | null;
  revisions: Array<{ id: string; number: number; message: string | null; created_by: string | null; created_at: string; pruned: boolean }>;
  requested_reviewers: string[];
  reviewer_names?: Record<string, string>;
  my_subscription: "watching" | "muted" | null;
  self_approval_allowed: boolean;
}

export interface ReviewFile {
  path: string;
  status: "added" | "removed" | "modified" | "renamed" | "unchanged";
  old_path?: string;
  lang: string;
  pinned: boolean;
  generated: boolean;
  additions: number | null;
  deletions: number | null;
  too_large: boolean;
  hunks?: Array<{ base_start: number; base_lines: number; head_start: number; head_lines: number; lines: Array<{ t: "ctx" | "add" | "del"; b?: number; h?: number; s: string }> }>;
}

export interface ReviewThread {
  id: string;
  path: string | null;
  side: "head" | "base" | null;
  start_line: number | null;
  end_line: number | null;
  outdated: boolean;
  resolved_at: string | null;
  comments: Array<{ id: string; body: string; author_id: string; created_at: string; suggestion?: string | null; pending?: boolean }>;
}

export interface ReviewTimelineEntry {
  id: string;
  kind: string;
  actor_id: string | null;
  actor_name?: string | null;
  created_at: string;
  payload?: Record<string, unknown>;
  verdict?: ReviewVerdict;
  body?: string;
  state?: string;
  /** Revision a verdict was submitted on. */
  revision_id?: string;
}

export type CheckStatus = "pass" | "fail" | "warn" | "skipped" | "queued" | "running" | "error";

export interface ReviewCheck {
  id: string;
  name: string;
  status: CheckStatus;
  required: boolean;
  details?: string[] | string;
  kind?: "automated";
  network?: boolean;
  revision: number;
}

/** One configurable (or built-in) automated review check, with its effective settings for a scope. */
export interface CheckSetting {
  id: string;
  name: string;
  description: string;
  subjects: string[];
  network: boolean;
  locked: boolean;
  enabled: boolean;
  required: boolean;
}

export interface CheckSettings {
  scope: "organization" | "teamspace";
  checks: CheckSetting[];
}

export interface PublicReviewPolicy {
  required_approvals: Record<string, number>;
  self_approval: "not_counted" | "counted";
  dismiss_stale_approvals: boolean;
  require_resolved_threads: boolean;
  auto_publish: boolean;
}

export interface ReviewPolicy extends PublicReviewPolicy {
  /** Teamspace only: rules for items published to the public registry. Never looser than the organization's. */
  public?: PublicReviewPolicy | null;
}
