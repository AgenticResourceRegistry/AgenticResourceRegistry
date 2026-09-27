// SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
// SPDX-FileCopyrightText: 2026 Harishankar <harishankar0301@gmail.com>
// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-FileCopyrightText: 2026 Swathi Saravanan <ss4522@cornell.edu>
// SPDX-License-Identifier: Apache-2.0

// ── Admin ───────────────────────────────────────────────────────────

export interface AdminUser {
	id: string;
	username?: string;
	name?: string;
	email?: string;
	role: string;
	department?: string | null;
	created_at?: string;
}

export interface AdminSetting {
	key: string;
	value: string;
	is_sensitive?: boolean;
	is_set?: boolean;
}

export interface AdminSettingDef {
	key: string;
	label: string;
	subtitle: string;
	default: string;
	requires_feature?: string;
	restart_required?: boolean;
}

export interface AdminSettingSection {
	id: string;
	title: string;
	description?: string;
	danger?: boolean;
	requires_feature?: string;
	settings: AdminSettingDef[];
}

export interface UserSearchResult {
	id: string;
	email: string;
	username?: string | null;
	name: string;
	avatar_url?: string | null;
	role: string;
	is_active: boolean;
}

export interface AuditLogEntry {
	event_id: string;
	timestamp: string;
	actor_id: string;
	actor_email: string;
	actor_role: string;
	action: string;
	resource_type: string;
	resource_id: string;
	resource_name: string;
	http_method: string;
	http_path: string;
	status_code: number;
	ip_address: string;
	user_agent: string;
	detail: string;
	sensitivity: string;
	request_id: string;
	outcome: string;
	duration_ms: number;
	chain_hash: string;
	source: string;
}

export interface SecurityEvent {
	event_id: string;
	timestamp: string;
	event_type: string;
	severity: string;
	actor_id: string;
	actor_email: string;
	actor_role: string;
	target_id: string;
	target_type: string;
	outcome: string;
	source_ip: string;
	user_agent: string;
	detail: string;
}

export interface RestartStatus {
	required: boolean;
	changed_at: string | null;
	keys: string[];
}

export interface UsagePingPayload {
	schema_version: 1;
	ping_id: string;
	installation_id: string;
	sent_at: string;
	identity: { company_name: string; hostname: string };
	instance: { version: string; deployment_type: string };
	counts: Record<string, number>;
	activity: Record<string, number>;
	features: Record<string, boolean>;
	harnesses: Record<string, number>;
}

export type UsagePingFrequency = "every_6_hours" | "daily" | "weekly";

export interface UsagePingStatus {
	enabled: boolean;
	configured: boolean;
	frequency: UsagePingFrequency;
	collector_url: string;
	installation_id: string | null;
	last_attempt_at: string | null;
	last_success_at: string | null;
	last_error: string | null;
	next_scheduled_at: string;
}

export interface UsagePingAdminResponse {
	status: UsagePingStatus;
	payload: UsagePingPayload | null;
}

// ── Insights ───────────────────────────────────────────────────────

export interface ComponentInsightMetrics {
	present_sessions: number;
	present_users: number;
	observed_sessions: number;
	observed_calls: number;
	result_states: { success: number; error: number; unknown: number };
	harness_distribution: Record<string, number>;
	version_distribution: Record<string, number>;
	cohort_collision_calls: number;
	cohort_unmatched_calls: number;
}

export interface ComponentInsightFinding {
	kind: "workflow" | "friction" | "opportunity" | "inferred_use";
	insight: string;
	confidence: "low" | "medium";
	evidence_refs: string[];
}

export interface ComponentInsightNarrative {
	summary?: string;
	component_analysis?: {
		version: number;
		state: "assessed" | "unknown";
		findings: ComponentInsightFinding[];
		evidence?: Record<string, string>;
		sampled_sessions: number;
		truncated: boolean;
	};
}

export interface ComponentInsightCoverage {
	attribution_state: "observed" | "no_observed_calls" | "attribution_not_possible";
	presence: { present_sessions: number; present_users: number; eligible_sessions: number };
	projection: { projection_complete_sessions: number; projection_pending_sessions: number; projection_failed_sessions: number; unsupported_present_sessions: number };
	calls: { collision_calls: number; unmatched_calls: number; unknown_result_calls: number };
	usage_rate_denominator_sessions: number;
	observed_sessions: number;
	usage_rate: number | null;
	reasons: string[];
	limitations: string[];
}

export interface InsightReportListItem {
	id: string;
	agent_id: string | null;
	subject_type?: "agent" | "component";
	component_type?: string | null;
	component_id?: string | null;
	component_name?: string | null;
	component_version_id?: string | null;
	component_version?: string | null;
	coverage?: ComponentInsightCoverage | null;
	agent_version_id?: string | null;
	agent_version?: string | null;
	version_scope?: string | null;
	status: "pending" | "running" | "completed" | "failed";
	period_start: string;
	period_end: string;
	sessions_analyzed: number;
	created_at: string;
	completed_at: string | null;
	progress_phase?: string | null;
	progress_current?: number;
	progress_total?: number;
	progress_percent?: number;
	progress_message?: string | null;
	progress_updated_at?: string | null;
}

export interface InsightCostMetrics {
	total_cost_usd: number;
	avg_cost_per_session: number;
	p50_session_cost: number;
	p90_session_cost: number;
	p99_session_cost: number;
	cache_efficiency_ratio: number;
	most_expensive_model: string;
	cost_by_model: { model: string; total_cost_usd: number }[];
}

export interface InsightToolErrors {
	total_categorized: number;
	categories: Record<string, number>;
	by_tool: Record<string, Record<string, number>>;
}

export interface InsightInterruptions {
	stop_reasons: Record<string, number>;
	user_interruptions: number;
	total_stops: number;
}

export interface InsightReconciliation {
	available: boolean;
	reconciled_sessions?: number;
	total_input_tokens?: number;
	total_output_tokens?: number;
	cache_read_tokens?: number;
	cache_creation_tokens?: number;
	thinking_turns?: number;
	tool_uses?: number;
}

export interface InsightMetrics {
	overview: {
		total_sessions: string;
		unique_users: string;
		first_session: string;
		last_session: string;
	};
	tokens: {
		total_input_tokens: string;
		total_output_tokens: string;
		total_tokens: string;
		total_cache_read_tokens: string;
		total_cache_write_tokens: string;
	};
	cost?: InsightCostMetrics;
	duration: {
		session_count: string;
		avg_duration_seconds: string;
		p50_duration_seconds: string;
		p90_duration_seconds: string;
	};
	errors: {
		total_events: string;
		total_tool_calls: string;
		failure_stops: string;
		error_events: string;
		error_rate: number;
	};
	tool_errors?: InsightToolErrors;
	interruptions?: InsightInterruptions;
	reconciliation?: InsightReconciliation;
	tools: {
		name: string;
		invocations: string;
		errors: string;
	}[];
	sessions: {
		session_id: string;
		duration_seconds: string;
		prompt_count: string;
		tool_call_count: string;
		input_tokens: string;
		output_tokens: string;
	}[];
}

export interface InsightNarrative {
	// V3 structured format: each section is a structured object
	// V1 fallback: each section is string[] or string
	// The frontend handles both formats gracefully
	at_a_glance: unknown;
	what_they_work_on?: unknown;
	usage_patterns: unknown;
	interaction_style?: unknown;
	user_experience?: unknown;
	what_works?: unknown;
	friction_analysis: unknown;
	suggestions: unknown;
	usage_cost_analysis?: unknown;
	token_optimization?: unknown;
	version_comparison?: unknown;
	regression_detection?: unknown;
	on_the_horizon?: unknown;
	fun_ending?: unknown;
	regressions?: InsightRegression[];
	/**
	 * Outcome of the reuse search: how many registry components were offered
	 * to the model and how many survived validation. Absent on reports
	 * generated before reuse suggestions existed.
	 */
	registry_match?: RegistryMatchSummary;
}

/** A registry component a suggestion may safely point the reader at.
 *
 * The server attaches this only after re-validating the reference, so its
 * presence — not the suggestion's `action_type` — is what makes a component
 * link safe to render.
 */
export interface ComponentRef {
	type: string;
	id: string;
	name: string;
	qualified_name: string;
	latest_version: string;
}

export interface FeatureSuggestion {
	feature: string;
	action_type?: string;
	name?: string;
	one_liner: string;
	why_for_you: string;
	example: string;
	match_reason?: string;
	/** Present only on validated reuse suggestions. Older reports omit it. */
	component_ref?: ComponentRef | null;
	confidence?: string;
	risk?: string;
}

/** Why a report suggested nothing to reuse. */
export interface RegistryMatchSummary {
	enabled?: boolean;
	offered?: number;
	reused?: number;
	registry_has_components?: boolean | null;
}

export interface InsightRegression {
	metric: string;
	direction: "improved" | "degraded";
	magnitude: number;
	current_value: number;
	previous_value: number;
	severity: "low" | "medium" | "high";
}

export interface InsightReport {
	id: string;
	agent_id: string | null;
	subject_type?: "agent" | "component";
	component_type?: string | null;
	component_id?: string | null;
	component_name?: string | null;
	component_version_id?: string | null;
	component_version?: string | null;
	coverage?: ComponentInsightCoverage | null;
	agent_version_id?: string | null;
	agent_version?: string | null;
	version_scope?: string | null;
	comparison_agent_version_id?: string | null;
	comparison_agent_version?: string | null;
	triggered_by: string | null;
	status: "pending" | "running" | "completed" | "failed";
	period_start: string;
	period_end: string;
	metrics: InsightMetrics | null;
	narrative: InsightNarrative | null;
	facets_summary: Record<string, unknown> | null;
	sessions_analyzed: number;
	llm_model_used: string | null;
	error_message: string | null;
	started_at: string;
	completed_at: string | null;
	created_at: string;
	progress_phase?: string | null;
	progress_current?: number;
	progress_total?: number;
	progress_percent?: number;
	progress_message?: string | null;
	progress_updated_at?: string | null;
	applied_at: string | null;
	applied_items: InsightAppliedItems | null;
}

export interface InsightAppliedItems {
	agent_version: { id: string; version: string; additions_count: number; linked_components: number } | null;
	skills: { id: string; name: string; description: string; type: string }[];
	hooks: { id: string; name: string; description: string; type: string }[];
	prompts: { id: string; name: string; description: string; type: string }[];
}

// ── Telemetry ───────────────────────────────────────────────────────

export interface TelemetryStatus {
	clickhouse: boolean;
	traces_count: number;
	spans_count: number;
	scores_count: number;
}

export interface LiteLLMProvider {
	id: string;
	label: string;
	model_count: number;
}

export interface LiteLLMProviderList {
	providers: LiteLLMProvider[];
}

export interface LiteLLMModel {
	model_id: string;
	litellm_provider: string;
	litellm_model: string;
	mode: string;
	max_input_tokens: number | null;
	max_output_tokens: number | null;
	input_cost_per_token: number | null;
	output_cost_per_token: number | null;
	deprecation_date: string | null;
	deprecated: boolean;
	capabilities: string[];
}

export interface LiteLLMModelList {
	provider: string;
	models: LiteLLMModel[];
}

export interface SystemWarning {
	level: "critical" | "warning" | "info";
	code: string;
	message: string;
}

// ── Exec Dashboard ─────────────────────────────────────────────────

export interface ExecAdoptionResponse {
	monthly: { month: string; adoption_pct: number }[];
	current_pct: number;
	total_users: number;
	active_users: number;
	departments_covered: number;
}

export interface ExecAgentCounts {
	total: number;
	active: number;
	published: number;
	in_development: number;
	by_category: { category: string; count: number }[];
}

export interface ExecUsageByCategory {
	category: string;
	sessions: number;
	growth_pct: number;
}

export interface ExecPlatformCoverage {
	platform: string;
	users: number;
	sessions: number;
}

export interface ExecPlatformScore {
	platform: string;
	composite_score: number;
	sessions: number;
	avg_cost: number;
	avg_latency_ms: number;
	success_rate: number | null;
	error_rate: number | null;
	users: number;
}

export interface ExecVelocityResponse {
	weekly: { week: string; traces: number }[];
	current_weekly_avg: number;
	baseline_weekly_avg: number;
	multiplier: number;
}

export interface ExecTopAgent {
	id: string;
	name: string;
	category: string;
	composite_score: number;
	sessions: number;
	downloads: number;
	avg_rating: number | null;
	weekly_trend: number[];
}

export interface ExecConfig {
	id: string;
	hourly_dev_cost: number;
	pre_ai_baselines: Record<string, number>;
	department_budgets: Record<string, { headcount: number; monthly_budget: number }>;
	target_adoption_pct: number;
	target_adoption_date: string | null;
}

export interface ExecDepartmentItem {
	department: string;
	user_count: number;
	agent_count: number;
	utilization_pct: number;
	sessions_per_user: number;
}

export interface ExecDepartmentsResponse {
	departments: ExecDepartmentItem[];
}

export interface ExecDeptTokenItem {
	department: string;
	tokens_used: number;
	cost_per_task: number;
	sessions_per_user: number;
	trend_pct: number;
}

export interface ExecCostByCategory {
	category: string;
	baseline_cost: number;
	actual_cost: number;
	saved_pct: number;
}

export interface ExecCostSummary {
	monthly_savings: number;
	cost_reduction_pct: number;
	projected_annual_savings: number;
	cost_per_task: number;
	monthly_trend: { month: string; ai_spend: number; savings: number }[];
	by_category: ExecCostByCategory[];
	configured: boolean;
}

export interface ExecROIProjectionPoint {
	quarter: string;
	projected_savings: number;
	cumulative_savings: number;
	confidence: number;
}

export interface ExecROIProjectionsResponse {
	projections: ExecROIProjectionPoint[];
	growth_rate_pct: number;
	time_to_breakeven_months: number | null;
	total_invested: number;
	total_saved: number;
	roi_multiple: number;
}

export interface ExecModelComparison {
	model: string;
	sessions: number;
	avg_cost: number;
	avg_tokens: number;
	success_rate: number;
	best_at: string;
}

export interface ExecDepartmentGap {
	department: string;
	adoption_pct: number;
	sessions: number;
	opportunity: string;
}

export interface ExecQuickWin {
	title: string;
	detail: string;
	estimated_savings: number;
	effort: string;
}

export interface ExecPlatformComparison {
	platform: string;
	avg_task_time_ms: number;
	sessions: number;
	success_rate: number;
}

export interface ExecStrategicInsightsResponse {
	model_comparison: ExecModelComparison[];
	department_gaps: ExecDepartmentGap[];
	quick_wins: ExecQuickWin[];
	platform_comparison: ExecPlatformComparison[];
	power_user_pct: number;
	power_user_value_pct: number;
	total_active_users: number;
	automatable_pct: number;
}

export interface ExecDeveloperItem {
	user_id: string;
	name: string;
	department: string;
	sessions: number;
	tokens_consumed: number;
	cost: number;
	percentile: number;
}

export interface ExecDeveloperBreakdown {
	total_developers: number;
	active_developers: number;
	top_20_value_pct: number;
	developers: ExecDeveloperItem[];
}

export interface ExecInactiveAgent {
	id: string;
	name: string;
	category: string;
	last_session_days_ago: number;
	previous_sessions: number;
}

export interface ExecInactiveUser {
	user_id: string;
	name: string;
	department: string;
	last_session_days_ago: number;
	previous_sessions: number;
}

export interface ExecInactivityAlerts {
	inactive_agents: ExecInactiveAgent[];
	inactive_users: ExecInactiveUser[];
}

export interface ExecTimeToValueItem {
	id: string;
	name: string;
	category: string;
	created_at: string;
	days_to_100: number | null;
	current_sessions: number;
}

export interface ExecTimeToValueResponse {
	agents: ExecTimeToValueItem[];
	avg_days_to_100: number | null;
}

export interface ExecAIInsightsResponse {
	quick_wins: { title: string; detail: string; estimated_savings: string; effort: string }[];
	adoption_gaps: { title: string; detail: string; impact: string }[];
	platform_insight: { title: string; detail: string };
	model_insight: { title: string; detail: string };
	automation_opportunity: { title: string; detail: string };
	usage_pattern: { title: string; detail: string };
	generated: boolean;
	generated_at?: string | null;
}

// ── Migration ───────────────────────────────────────────────────────

export type MigrationOperation = "export" | "import" | "validate";
export type MigrationScope = "postgres" | "clickhouse" | "both";
export type MigrationStatus = "queued" | "running" | "completed" | "failed";

export interface MigrationArtifactMeta {
	name: string;
	size_bytes: number;
	sha256: string;
	kind: "archive" | "parquet" | "manifest";
}

export interface MigrationJob {
	id: string;
	operation_type: MigrationOperation;
	data_scope: MigrationScope;
	status: MigrationStatus;
	progress_phase: string | null;
	progress_pct: number;
	progress_message: string | null;
	error_message: string | null;
	created_at: string;
	finished_at: string | null;
	artifacts: MigrationArtifactMeta[];
	result:
		| MigrationExportResult
		| MigrationImportResult
		| MigrationValidateResult
		| null;
	schema_version: string | null;
}

export interface MigrationExportResult {
	table_counts: Record<string, number>;
	total_rows: number;
	archive_size_bytes: number | null;
	telemetry_size_bytes: number | null;
	schema_version_diff: string | null;
}

export interface MigrationImportResult {
	rows_inserted: Record<string, number>;
	rows_skipped: Record<string, number>;
	tables_skipped: string[];
	schema_version_diff: string | null;
}

export interface MigrationValidateResult {
	checksums_valid: boolean;
	checksum_details: Record<string, boolean>;
	row_count_comparison: Record<string, [number, number]> | null;
	orphaned_fk_refs: Record<string, string[]> | null;
	schema_version_diff: string | null;
}

export interface MigrationDownloadToken {
	token: string;
	expires_at: string;
}
