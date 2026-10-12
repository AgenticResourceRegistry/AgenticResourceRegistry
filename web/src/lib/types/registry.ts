// SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
// SPDX-FileCopyrightText: 2026 Harishankar <harishankar0301@gmail.com>
// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-FileCopyrightText: 2026 Swathi Saravanan <ss4522@cornell.edu>
// SPDX-License-Identifier: Apache-2.0

// ── Registry ────────────────────────────────────────────────────────

/** GET /registry/resolve: canonical identity for a UUID or namespace/slug reference. */
export interface RegistryResolution {
	id: string;
	type: string;
	namespace: string;
	slug: string;
	qualified_name: string;
}

export interface ForkProvenance {
	available: boolean;
	id?: string | null;
	type?: string | null;
	namespace?: string | null;
	slug?: string | null;
	qualified_name?: string | null;
	version?: string | null;
	forked_at?: string | null;
}

export interface ForkDiff {
	version: string;
	base_version: string;
	diff: string;
	unchanged: boolean;
}

export interface ForkRequest {
	name: string;
	version: string;
	team_id?: string | null;
	visibility?: "public" | "team";
}

export interface ForksResponse<T> {
	items: T[];
	total: number;
	page: number;
	page_size: number;
}

export interface RegistryItem {
	forked_from?: ForkProvenance | null;
	fork_count?: number;
	id: string;
	name: string;
	namespace?: string;
	slug?: string;
	qualified_name?: string;
	team_id?: string | null;
	visibility?: "public" | "team";
	is_private?: boolean;
	description?: string;
	status?: string;
	rejection_reason?: string;
	created_at?: string;
	updated_at?: string;
	is_recommended?: boolean;
	[key: string]: unknown;
}

// ── Agent enriched types ────────────────────────────────────────────

export interface TopAgentItem {
	id: string;
	name: string;
	namespace?: string;
	slug?: string;
	qualified_name?: string;
	description: string;
	owner: string;
	created_by_username?: string | null;
	version: string;
	download_count: number;
	average_rating: number | null;
}

export interface LeaderboardItem extends TopAgentItem {
	created_by_email?: string;
}
export type LeaderboardWindow = "24h" | "7d" | "30d" | "all";

export interface ComponentLeaderboardItem {
	id: string;
	name: string;
	namespace?: string;
	slug?: string;
	qualified_name?: string;
	component_type: string;
	description: string;
	download_count: number;
	created_by_email: string;
	average_rating: number | null;
	total_reviews: number;
}

export interface VersionSuggestions {
	current: string;
	suggestions: {
		patch: string;
		minor: string;
		major: string;
	};
}

export interface AgentVersionSummary {
	id: string;
	agent_id: string;
	version: string;
	description: string;
	status: string;
	is_prerelease: boolean;
	download_count: number;
	supported_harnesses: string[];
	released_by: string;
	released_at: string | null;
	created_at: string | null;
	rejection_reason: string | null;
	component_count: number;
}

export interface AgentVersionsResponse {
	items: AgentVersionSummary[];
	total: number;
	page: number;
	page_size: number;
}

export interface AgentComponentReference {
	component_type: string;
	component_id: string;
	name?: string;
	component_name?: string;
	mcp_name?: string;
	resolved_version?: string;
	status?: string;
}

/** A component of the agent's current version, as the agent detail response lists it. */
export interface AgentComponentLink {
	component_type: string;
	component_id: string;
	component_name?: string;
	namespace?: string;
	slug?: string;
	qualified_name?: string;
	/** The exact component version this agent version pins. */
	version_ref: string;
	order: number;
	config_override?: Record<string, unknown> | null;
	status?: string | null;
}

export interface SuccessMetric {
	name: string;
	target: string;
	measurement: string;
}

export interface SuccessCriteria {
	intended_purpose: string;
	success_metrics: SuccessMetric[];
	evaluation_notes: string;
}

export interface AgentVersionDetail extends AgentVersionSummary {
	prompt: string;
	model_name: string;
	model_config_json?: Record<string, unknown>;
	models_by_harness?: Record<string, unknown>;
	external_mcps?: unknown[];
	yaml_snapshot?: unknown;
	harness_configs?: Record<string, unknown>;
	required_capabilities?: string[];
	inferred_supported_harnesses?: string[];
	components: AgentComponentReference[];
	success_criteria?: SuccessCriteria | null;
}

// ── Component Versions ─────────────────────────────────────────────

export interface ComponentVersionSummary {
	id: string;
	listing_id: string;
	version: string;
	description: string;
	changelog: string | null;
	status: string;
	requires_global_review?: boolean;
	rejection_reason: string | null;
	download_count: number;
	supported_harnesses: string[];
	released_by: string;
	released_at: string | null;
	created_at: string | null;
	// Hook fields
	event?: string;
	execution_mode?: string;
	priority?: number;
	handler_type?: string;
	handler_config?: Record<string, unknown>;
	scope?: string;
	tool_filter?: Record<string, unknown>;
	script_content?: string;
	script_filename?: string;
	source_path?: string;
	requirements?: string[];
	// Skill fields
	delivery_mode?: "git_fetch" | "registry_direct";
	skill_path?: string;
	git_url?: string;
	git_ref?: string;
	skill_md_content?: string;
	validated?: boolean;
	target_agents?: string[];
	task_type?: string;
	slash_command?: string;
	// Prompt fields
	category?: string;
	template?: string;
	variables?: unknown[];
	model_hints?: Record<string, unknown>;
	tags?: string[];
	// MCP/Sandbox fields
	source_url?: string;
	source_ref?: string;
	resolved_sha?: string;
	// Sandbox fields
	runtime_type?: string;
	image?: string;
	resource_limits?: Record<string, unknown>;
	network_policy?: string;
	entrypoint?: string;
	runtime_config?: Record<string, unknown>;
	sandbox_path?: string;
}

export interface ComponentVersionsResponse {
	items: ComponentVersionSummary[];
	total: number;
	page: number;
	page_size: number;
}

export type ComponentVersionDetail = ComponentVersionSummary;

export type McpWebhookSyncStatus = "queued" | "syncing" | "success" | "skipped" | "failed";

export type McpWebhookProvider = "github" | "gitlab";

export interface McpWebhookSync {
	enabled: boolean;
	id?: string | null;
	provider?: McpWebhookProvider | null;
	webhook_url?: string | null;
	/** Present only right after sync is first turned on or the secret is rotated. */
	secret?: string | null;
	sync_on_push: boolean;
	sync_on_release: boolean;
	branch?: string | null;
	last_delivery_at?: string | null;
	last_event?: string | null;
	last_sync_status?: McpWebhookSyncStatus | null;
	last_sync_error?: string | null;
	last_synced_at?: string | null;
	last_synced_sha?: string | null;
	last_version?: string | null;
	created_at?: string | null;
}

export interface McpWebhookSyncRequest {
	sync_on_push: boolean;
	sync_on_release: boolean;
	branch: string | null;
	provider?: McpWebhookProvider | null;
}

export interface BulkResultItem {
	name: string;
	status: "created" | "skipped" | "error";
	agent_id?: string | null;
	error?: string | null;
}

export interface BulkResult {
	total: number;
	created: number;
	skipped: number;
	errors: number;
	partial: boolean;
	dry_run: boolean;
	results: BulkResultItem[];
}

export interface FeedbackSummary {
	listing_id: string;
	average_rating: number;
	total_reviews: number;
}

export interface ValidationIssue {
	severity: "error" | "warning";
	component_type?: string;
	component_id?: string;
	message: string;
}

export interface ValidationResult {
	valid: boolean;
	issues: ValidationIssue[];
}

// ── Version Diff ────────────────────────────────────────────────────

export interface ComponentChange {
	type: string;
	name: string;
	change: "added" | "removed" | "updated";
	version?: string;
	from?: string;
	to?: string;
}

export interface VersionDiff {
	agent_id: string;
	version_a: string;
	version_b: string;
	yaml_diff: string;
	component_changes: ComponentChange[];
}

// ── Component pins ──────────────────────────────────────────────────

/** One component an agent version pins, and how that pin compares with the registry. */
export interface ComponentPinFreshness {
	type: string;
	id: string;
	name: string;
	qualified_name: string;
	pinned_version: string | null;
	latest_version: string | null;
	outdated: boolean;
	archived: boolean;
	locked: boolean;
}

export interface AgentVersionOutdated {
	agent_id: string;
	qualified_name: string;
	version: string;
	components: ComponentPinFreshness[];
	summary: { total: number; outdated: number; unlocked: number; archived: number };
}

// ── Review ──────────────────────────────────────────────────────────

export interface McpValidationResult {
	stage: string;
	passed: boolean;
	details?: string;
	run_at?: string;
}

export interface ReviewComponentBlocker {
	component_type: string;
	component_id: string;
	name: string;
	status: string;
}

export interface ReviewItem {
	id: string;
	name?: string;
	description?: string;
	version?: string;
	owner?: string;
	type?: string;
	listing_type?: string;
	submitted_by?: string;
	submitted_at?: string;
	created_at?: string;
	updated_at?: string;
	status?: string;
	mcp_validated?: boolean;
	validation_results?: McpValidationResult[];
	components_ready?: boolean;
	component_blockers?: ReviewComponentBlocker[];
	blocking_components?: ReviewComponentBlocker[];
	bundle_id?: string;
	bundle_name?: string;
	rejection_reason?: string;
	forked_from?: ForkProvenance | null;

	// Common detail fields
	git_url?: string;
	git_ref?: string;
	supported_harnesses?: string[];

	// MCP-specific
	transport?: string;
	framework?: string;
	docker_image?: string;
	command?: string;
	args?: string[];
	url?: string;
	headers?: unknown[];
	auto_approve?: string[];
	tools_schema?: Record<string, unknown>;
	environment_variables?: unknown[];
	setup_instructions?: string;
	changelog?: string;

	// Skill-specific
	skill_path?: string;
	skill_md_content?: string;
	validated?: boolean;
	target_agents?: string[];
	task_type?: string;
	slash_command?: string;
	version_id?: string;
	base_version_id?: string | null;
	base_version?: string | null;
	base_delivery_mode?: "git_fetch" | "registry_direct" | null;
	base_git_url?: string | null;
	base_git_ref?: string | null;
	review_key?: string;
	revision?: string;
	files?: SkillManifestFile[];
	pending_version_id?: string;
	has_extra_files?: boolean;

	// Hook-specific
	event?: string;
	execution_mode?: string;
	handler_type?: string;
	handler_config?: Record<string, unknown>;
	scope?: string;
	tool_filter?: string[];
	priority?: number;
	script_content?: string;
	script_filename?: string;
	source_url?: string;
	source_ref?: string;
	source_path?: string;
	resolved_sha?: string;
	requirements?: string[];

	// Prompt-specific
	category?: string;
	template?: string;
	variables?: unknown[];
	model_hints?: Record<string, unknown>;
	tags?: string[];

	// Sandbox-specific
	runtime_type?: string;
	image?: string;
	resource_limits?: Record<string, unknown>;
	network_policy?: string;
	entrypoint?: string;
	sandbox_path?: string;
	validated_at?: string;

	// Agent-specific
	prompt?: string;
	model_name?: string;
	model_config_json?: Record<string, unknown>;
	external_mcps?: unknown[];
	required_capabilities?: string[];
	component_count?: number;
	components?: { component_type: string; component_id: string }[];
	success_criteria?: SuccessCriteria | null;
}

// ── Skill Folder Resources ───────────────────────────────────────────

/** A file resource in a skill folder (for authoring). */
export interface SkillResource {
	path: string;
	content: string;
	executable?: boolean;
	/** Only present for binary files */
	encoding?: "base64";
}

/** A file in a skill version manifest (from server). */
export interface SkillManifestFile {
	path: string;
	size: number;
	sha256: string;
	mode: "0644" | "0755";
}

/** Manifest for a skill version's files. */
export interface SkillVersionManifest {
	listing_id: string;
	version_id: string;
	revision: string;
	files: SkillManifestFile[];
}

/** Owner-only pointer to the current immutable reviewed release. */
export interface SkillApprovedBase {
	listing_id: string;
	version_id: string;
	version: string;
	revision: string;
	delivery_mode: "registry_direct" | "git_fetch";
	import_required: boolean;
}

export interface SkillSuccessorRequest {
	version: string;
	description: string;
	changelog?: string;
	base_version_id: string;
	observed_base_revision: string;
}

/** Request body for creating a folder draft. */
export interface SkillFolderDraftRequest {
	name: string;
	version: string;
	description: string;
	owner: string;
	task_type?: string;
	skill_md_content: string;
	extra_files: SkillResource[];
	supported_harnesses?: string[];
	team_id?: string;
	visibility?: "public" | "team";
}

/** Request body for replacing all files in a skill version. */
export interface SkillFilesReplaceRequest {
	observed_revision: string;
	skill_md_content: string;
	extra_files?: SkillResource[];
}

/** A single file operation for PATCH. */
export interface SkillFileOperation {
	action: "put" | "delete" | "rename";
	path?: string;
	new_path?: string;
	file?: SkillResource;
}

/** Request body for patching files in a skill version. */
export interface SkillFilesPatchRequest {
	observed_revision: string;
	operations: SkillFileOperation[];
}

/** Response from file read endpoint (text files). */
export interface SkillFileContents {
	version_id: string;
	revision: string;
	file: SkillManifestFile;
	content: string;
	encoding: "utf-8";
}

/** Binary previews are octet-stream attachments, never JSON. */
export interface SkillBinaryContents {
	encoding: "binary";
	content: Blob;
}

// ── Scores ──────────────────────────────────────────────────────────

export interface Score {
	score_id: string;
	trace_id: string;
	span_id?: string;
	name: string;
	source: string;
	data_type: string;
	value?: number;
	string_value?: string;
	comment?: string;
	timestamp: string;
}

// ── Feedback ────────────────────────────────────────────────────────

export interface FeedbackItem {
	id: string;
	listing_id?: string;
	listing_name?: string;
	listing_type?: string;
	user_id?: string | null;
	rating: number;
	comment?: string;
	anonymous?: boolean;
	user?: string;
	username?: string;
	created_at?: string;
	updated_at?: string | null;
}

// ── Personalised recommendations ──────────────────────────────────────────

export interface RecommendationItem {
	type: string;
	id: string;
	name: string;
	namespace: string;
	slug: string;
	qualified_name: string;
	description: string;
	category: string | null;
	latest_version: string;
	download_count: number;
	matched_on: string[];
	score: number;
	reason: string;
}

export interface RecommendationsResponse {
	items: RecommendationItem[];
	/** False when the user has no session history, so the UI can say so. */
	personalized: boolean;
	profile_sessions: number;
	topics: string[];
}

// ── Admin Recommended ────────────────────────────────────────────────

export type RecommendableType = "agent" | "mcp" | "skill" | "hook" | "prompt" | "sandbox";

export interface SetRecommendedRequest {
	entity_type: RecommendableType;
	entity_id: string;
	recommended: boolean;
}

export interface SetRecommendedResponse {
	entity_type: RecommendableType;
	entity_id: string;
	is_recommended: boolean;
}

// ── Discovery (ARD) ─────────────────────────────────────────────────

/** `external` is a remote agent registered by its A2A Agent Card (ADR 0002). */
export type DiscoveryKind = "agent" | "mcp" | "skill" | "hook" | "prompt" | "sandbox" | "external";

export type DiscoveryApproval = "approved" | "pending" | "rejected" | "archived" | "draft";

export type DiscoveryAvailability =
	| "now"
	| "next-session"
	| "explicit-install"
	| "delegate"
	| "not-approved"
	| "archived"
	| "unsupported-in-harness";

/** One entry of `results` in an ARD Search response. `score` is relevance only. */
export interface DiscoverySearchResult {
	identifier: string;
	displayName?: string;
	type?: string;
	url?: string;
	version?: string;
	description?: string;
	capabilities?: string[];
	score: number;
	source: string;
	matchedOn?: string[];
	"obs:kind"?: DiscoveryKind;
	"obs:nativeRef"?: string | null;
	"obs:approval"?: DiscoveryApproval;
	"obs:visibility"?: "public" | "team" | "owner";
	"obs:supportedHarnesses"?: string[];
	"obs:availability"?: DiscoveryAvailability;
	"obs:activatable"?: boolean;
	/** Can take a delegated task right now (approved agent with a headless harness, or remote A2A agent). */
	"obs:delegable"?: boolean;
	/** An admin marked this as recommended. Editorial only; never part of `score`. */
	"obs:recommended"?: boolean;
	"obs:artifactDigest"?: string | null;
	"obs:publisher"?: string;
	/** Organization named on a remote A2A agent's card, as the card states it. */
	"obs:provider"?: string;
}

export interface DiscoverySearchResponse {
	results: DiscoverySearchResult[];
	pageToken?: string;
}

export interface DiscoverySearchFilter {
	kind?: DiscoveryKind;
	harness?: string;
	includeUnapproved?: boolean;
}
