
// SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
// SPDX-FileCopyrightText: 2026 Harishankar <harishankar0301@gmail.com>
// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-FileCopyrightText: 2026 Shreem Seth <shreemseth26@gmail.com>
// SPDX-FileCopyrightText: 2026 SrihariLegend <sriharilegend23@gmail.com>
// SPDX-FileCopyrightText: 2026 Swathi Saravanan <ss4522@cornell.edu>
// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.work@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import type {
	OverviewStats,
	TopItem,
	TopAgentItem,
	TrendPoint,
	SessionsStats,
	SessionTrace,
	SessionData,
	TokenStats,
	FeedbackItem,
	FeedbackSummary,
	HarnessUsageData,
	AdminUser,
	AdminSetting,
	AdminSettingSection,
	Session,
	SessionsSummary,
	SessionErrorEvent,
	TelemetryStatus,
	ReviewItem,
	SkillFolderDraftRequest,
	SkillVersionManifest,
	SkillApprovedBase,
	SkillSuccessorRequest,
	SkillFileContents,
	SkillBinaryContents,
	RegistryItem,
	ForkRequest,
	ForkDiff,
	ForksResponse,
	LeaderboardItem,
	LeaderboardWindow,
	ValidationResult,
	VersionSuggestions,
	AgentVersionDetail,
	AgentVersionsResponse,
	ComponentVersionsResponse,
	McpWebhookSync,
	McpWebhookSyncRequest,
	ComponentVersionDetail,
	VersionDiff,
	AgentVersionOutdated,
	BulkResult,
	ComponentLeaderboardItem,
	AuditLogEntry,
	SecurityEvent,
	DiagnosticsResponse,
	RestartStatus,
	SystemWarning,
	UsagePingAdminResponse,
	UsagePingStatus,
	InsightReportListItem,
	InsightReport,
	InsightAppliedItems,
	ExecAdoptionResponse,
	ExecAgentCounts,
	ExecUsageByCategory,
	ExecPlatformCoverage,
	ExecPlatformScore,
	ExecVelocityResponse,
	ExecTopAgent,
	ExecConfig,
	ExecDepartmentsResponse,
	ExecDeptTokenItem,
	ExecCostSummary,
	ExecROIProjectionsResponse,
	ExecStrategicInsightsResponse,
	ExecDeveloperBreakdown,
	ExecInactivityAlerts,
	ExecTimeToValueResponse,
	ExecAIInsightsResponse,
	UserSearchResult,
	RegistryResolution,
	Team,
	TeamInvite,
	TeamInviteCreated,
	TeamInvitePreview,
	TeamJoinRequest,
	TeamJoinRequestStatus,
	TeamVisibilityRequest,
	TeamMember,
	TeamMemberUpsertBody,
	TeamRole,
	TeamUpdateBody,
	RecommendationsResponse,
	SetRecommendedRequest,
	SetRecommendedResponse,
	DiscoverySearchFilter,
	DiscoverySearchResponse,
	InboxItem,
	InboxItemDetail,
	InboxListResponse,
	InboxCounts,
	InboxFilters,
	InboxState,
} from "./types";
import { sessionExpiredLoginUrl } from "./safe-next";

const API = "/api/v1";

const STORAGE_KEY_ACCESS_TOKEN = "observal_access_token";
const STORAGE_KEY_REFRESH_TOKEN = "observal_refresh_token";
const STORAGE_KEY_USER_ROLE = "observal_user_role";
const STORAGE_KEY_USER_NAME = "observal_user_name";
const STORAGE_KEY_USER_EMAIL = "observal_user_email";
const STORAGE_KEY_USER_USERNAME = "observal_user_username";
const STORAGE_KEY_USER_AVATAR = "observal_user_avatar";

// Access token is stored in sessionStorage (clears on tab close) to reduce
// the XSS exposure window. Refresh token stays in localStorage so silent
// refresh survives page reloads across tabs.
// TODO(SEC-025): migrate to HttpOnly cookies set by the API and proxied
// through nginx to reduce token exfiltration risk. HttpOnly does not prevent
// authenticated requests from XSS payloads, so retain XSS and CSRF defenses.
function getAccessToken(): string | null {
	if (typeof window === "undefined") return null;
	return sessionStorage.getItem(STORAGE_KEY_ACCESS_TOKEN);
}

function getRefreshToken(): string | null {
	if (typeof window === "undefined") return null;
	return localStorage.getItem(STORAGE_KEY_REFRESH_TOKEN);
}

export function setTokens(accessToken: string, refreshToken: string) {
	sessionStorage.setItem(STORAGE_KEY_ACCESS_TOKEN, accessToken);
	localStorage.setItem(STORAGE_KEY_REFRESH_TOKEN, refreshToken);
	window.dispatchEvent(new Event("storage"));
}

export function clearSession() {
	sessionStorage.removeItem(STORAGE_KEY_ACCESS_TOKEN);
	localStorage.removeItem(STORAGE_KEY_REFRESH_TOKEN);
	localStorage.removeItem("observal_api_key"); // clean up legacy
	localStorage.removeItem(STORAGE_KEY_USER_ROLE);
	localStorage.removeItem(STORAGE_KEY_USER_NAME);
	localStorage.removeItem(STORAGE_KEY_USER_EMAIL);
	localStorage.removeItem(STORAGE_KEY_USER_USERNAME);
	localStorage.removeItem(STORAGE_KEY_USER_AVATAR);
	window.dispatchEvent(new Event("observal:session-cleared"));
	window.dispatchEvent(new Event("storage"));
}

export function setUserRole(role: string) {
	localStorage.setItem(STORAGE_KEY_USER_ROLE, role);
}

export function getUserRole(): string | null {
	if (typeof window === "undefined") return null;
	return localStorage.getItem(STORAGE_KEY_USER_ROLE);
}

export function setUserName(name: string) {
	localStorage.setItem(STORAGE_KEY_USER_NAME, name);
}

export function getUserName(): string | null {
	if (typeof window === "undefined") return null;
	return localStorage.getItem(STORAGE_KEY_USER_NAME);
}

export function setUserEmail(email: string) {
	localStorage.setItem(STORAGE_KEY_USER_EMAIL, email);
}

export function getUserEmail(): string | null {
	if (typeof window === "undefined") return null;
	return localStorage.getItem(STORAGE_KEY_USER_EMAIL);
}

export function setUserUsername(username: string) {
	localStorage.setItem(STORAGE_KEY_USER_USERNAME, username);
}

export function getUserUsername(): string | null {
	if (typeof window === "undefined") return null;
	return localStorage.getItem(STORAGE_KEY_USER_USERNAME);
}

export function setUserAvatar(avatar: string | null) {
	if (avatar) {
		localStorage.setItem(STORAGE_KEY_USER_AVATAR, avatar);
	} else {
		localStorage.removeItem(STORAGE_KEY_USER_AVATAR);
	}
	window.dispatchEvent(new Event("storage"));
}

export function getUserAvatar(): string | null {
	if (typeof window === "undefined") return null;
	return localStorage.getItem(STORAGE_KEY_USER_AVATAR);
}

let _refreshPromise: Promise<RefreshResult> | null = null;

type RefreshResult = "ok" | "rejected" | "network_error";

async function _tryRefreshToken(): Promise<RefreshResult> {
	const refreshToken = getRefreshToken();
	if (!refreshToken) return "rejected";

	try {
		const res = await fetch(`${API}/auth/token/refresh`, {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ refresh_token: refreshToken }),
		});

		if (!res.ok) return "rejected";

		const data = await res.json();
		setTokens(data.access_token, data.refresh_token);
		return "ok";
	} catch {
		return "network_error";
	}
}

/**
 * Public wrapper for silent token refresh (e.g. new tab with no sessionStorage).
 * Returns true if the access token was restored successfully.
 */
export async function refreshAccessToken(): Promise<boolean> {
	const result = await _tryRefreshToken();
	return result === "ok";
}

export async function refreshAccessTokenWithReason(): Promise<RefreshResult> {
	return _tryRefreshToken();
}

async function decodeResponse<T>(response: Response, fileResponse: boolean): Promise<T> {
	if (fileResponse && response.headers.get("content-type")?.includes("application/octet-stream")) {
		return { encoding: "binary", content: await response.blob() } as T;
	}
	return response.json() as Promise<T>;
}

async function request<T = unknown>(
	method: string,
	path: string,
	body?: unknown,
	fileResponse = false,
): Promise<T> {
	const headers: Record<string, string> = {
		"Content-Type": "application/json",
	};
	const token = getAccessToken();
	if (token) headers["Authorization"] = `Bearer ${token}`;

	let res: Response | undefined;
	for (let attempt = 0; attempt < 2; attempt++) {
		res = await fetch(`${API}${path}`, {
			method,
			headers,
			body: body !== undefined ? JSON.stringify(body) : undefined,
			cache: "no-store",
		});
		if (res.status < 500) break;
		// Brief pause before retry on 5xx
		if (attempt === 0) await new Promise((r) => setTimeout(r, 500));
	}
	const response = res!;

	if (!response.ok) {
		// Auto-refresh on 401 (except for auth endpoints where 401 means bad credentials)
		if (response.status === 401 && !path.startsWith("/auth/")) {
			// Deduplicate concurrent refresh attempts
			if (!_refreshPromise) {
				_refreshPromise = _tryRefreshToken().finally(() => {
					_refreshPromise = null;
				});
			}
			const refreshResult = await _refreshPromise;

			if (refreshResult === "ok") {
				// Retry the original request with new token
				const newToken = getAccessToken();
				if (newToken) headers["Authorization"] = `Bearer ${newToken}`;
				const retryRes = await fetch(`${API}${path}`, {
					method,
					headers,
					body: body !== undefined ? JSON.stringify(body) : undefined,
					cache: "no-store",
				});
				if (retryRes.ok) {
					if (retryRes.status === 204) return undefined as T;
					return decodeResponse<T>(retryRes, fileResponse);
				}
				const retryText = await retryRes.text().catch(() => "Request failed");
				const retryErr = new Error(retryText);
				(retryErr as Error & { status: number }).status = retryRes.status;
				throw retryErr;
			}

			if (refreshResult === "network_error") {
				throw new Error("Network unavailable");
			}

			// Real rejection: session is truly expired
			clearSession();
			if (typeof window !== "undefined") {
				window.location.href = sessionExpiredLoginUrl();
				return new Promise<T>(() => {});
			}
			throw new Error("Session expired");
		}

		const text = await response.text().catch(() => response.statusText);
		let detail = text;

		// Guard against raw HTML responses (e.g. nginx 502 Bad Gateway)
		if (text.trim().startsWith("<")) {
			detail =
				response.status >= 500
					? "Unable to reach the server. Please try again later."
					: `Request failed (${response.status})`;
		} else {
			try {
				const parsed = JSON.parse(text);
				if (parsed.detail) {
					if (typeof parsed.detail === "string") {
						detail = parsed.detail;
					} else if (Array.isArray(parsed.detail)) {
						detail = parsed.detail
							.map(
								(e: { msg?: string }) =>
									e.msg?.replace(/^Value error, /i, "") || "Validation error",
							)
							.join(". ");
					} else {
						detail = JSON.stringify(parsed.detail);
					}
				} else if (parsed.error) {
					detail =
						typeof parsed.error === "string"
							? parsed.error
							: JSON.stringify(parsed.error);
				}
			} catch {
				if (detail.length > 200 || detail.includes("Traceback") || detail.includes("Error:")) {
					detail = `Request failed (${response.status})`;
				}
			}
		}
		const err = new Error(detail);
		(err as Error & { status: number }).status = response.status;
		throw err;
	}

	if (response.status === 204) return undefined as T;

	return decodeResponse<T>(response, fileResponse);
}

function get<T = unknown>(path: string) {
	return request<T>("GET", path);
}
function post<T = unknown>(path: string, body?: unknown) {
	return request<T>("POST", path, body);
}
function put<T = unknown>(path: string, body?: unknown) {
	return request<T>("PUT", path, body);
}
function del<T = unknown>(path: string) {
	return request<T>("DELETE", path);
}
function patch<T = unknown>(path: string, body?: unknown) {
	return request<T>("PATCH", path, body);
}

export async function graphql<T = unknown>(
	query: string,
	variables?: Record<string, unknown>,
): Promise<T> {
	const res = await post<{ data: T; errors?: { message: string }[] }>(
		"/graphql",
		{ query, variables },
	);
	if (res.errors?.length) throw new Error(res.errors[0].message);
	return res.data;
}

// ── Auth ────────────────────────────────────────────────────────────
type AuthResponse = {
	user: {
		id: string;
		email: string;
		username?: string | null;
		name: string;
		role: string;
		avatar_url?: string | null;
		created_at: string;
	};
	access_token: string;
	refresh_token: string;
	expires_in: number;
};

export const auth = {
	init: (body: { email: string; name: string; password?: string }) =>
		post<AuthResponse>("/auth/init", body),
	login: (body: { email: string; password: string }) =>
		post<AuthResponse & { must_change_password?: boolean }>(
			"/auth/login",
			body,
		),
	register: (body: { email: string; name: string; username?: string; password: string }) =>
		post<AuthResponse>("/auth/register", body),
	whoami: () =>
		get<{
			id: string;
			email: string;
			username?: string | null;
			name: string;
			role: string;
			avatar_url?: string | null;
		}>("/auth/whoami"),
	exchangeCode: (body: { code: string }) =>
		post<AuthResponse>("/auth/exchange", body),
	deviceConfirm: (userCode: string) =>
		post<{ message: string }>("/auth/device/confirm", { user_code: userCode }),
	changePassword: (body: { current_password: string; new_password: string }) =>
		put<{ message: string }>("/auth/profile/password", body),
	uploadAvatar: (body: { avatar_url: string }) =>
		put<{ avatar_url: string | null }>("/auth/profile/avatar", body),
	deleteAvatar: () => del<{ avatar_url: null }>("/auth/profile/avatar"),
	ssoErrorDiagnostics: (corrId: string) =>
		get<E2eStatusResult>(
			`/auth/sso/diagnostics/${encodeURIComponent(corrId)}`,
		),
};

// ── Registry (all 8 types) ─────────────────────────────────────────
export type RegistryType =
	| "mcps"
	| "agents"
	| "skills"
	| "hooks"
	| "prompts"
	| "sandboxes";

// GET /registry/resolve takes the singular form of each registry type.
const SINGULAR_REGISTRY_TYPE: Record<RegistryType, string> = {
	agents: "agent",
	mcps: "mcp",
	skills: "skill",
	hooks: "hook",
	prompts: "prompt",
	sandboxes: "sandbox",
};

export const registry = {
	list: (type: RegistryType, params?: Record<string, string>) => {
		const qs = params ? `?${new URLSearchParams(params)}` : "";
		return get<RegistryItem[]>(`/${type}${qs}`);
	},
	get: (type: RegistryType, id: string) => get<RegistryItem>(`/${type}/${id}`),
	forkDiff: (type: RegistryType, id: string, version: string) =>
		get<ForkDiff>(`/${type}/${id}/fork-diff?${new URLSearchParams({ version })}`),
	fork: (type: RegistryType, id: string, body: ForkRequest) =>
		post<RegistryItem>(`/${type}/${encodeURIComponent(id)}/fork`, body),
	forks: (type: RegistryType, id: string, page = 1, pageSize = 12) =>
		get<ForksResponse<RegistryItem>>(
			`/${type}/${encodeURIComponent(id)}/forks?limit=${pageSize}&offset=${(page - 1) * pageSize}`,
		),
	create: (type: RegistryType, body: unknown) =>
		post<RegistryItem>(`/${type}`, body),
	install: (type: RegistryType, id: string, body?: unknown) =>
		post<unknown>(`/${type}/${id}/install`, body),
	delete: (type: RegistryType, id: string) => del(`/${type}/${id}`),
	metrics: (type: RegistryType, id: string) =>
		get<unknown>(`/${type}/${id}/metrics`),
	resolve: (id: string) => get<unknown>(`/agents/${id}/resolve`),
	// Canonical `namespace/slug` (or UUID) → registry identity, for shareable URLs.
	resolveIdentifier: (type: RegistryType, identifier: string) =>
		get<RegistryResolution>(
			`/registry/resolve?type=${SINGULAR_REGISTRY_TYPE[type]}&identifier=${encodeURIComponent(identifier)}`,
		),
	manifest: (id: string) =>
		get<Record<string, unknown>>(`/agents/${id}/manifest`),
	downloads: (id: string) =>
		get<{ total: number; unique_users: number; recent_7d: number }>(
			`/agents/${id}/downloads`,
		),
	validate: (body: {
		components: { component_type: string; component_id: string }[];
		team_id?: string;
		visibility?: "public" | "team";
	}) => post<ValidationResult>("/agents/validate", body),
	previewConfig: (body: {
		name: string;
		description: string;
		prompt: string;
		model_name: string;
		components: { component_type: string; component_id: string }[];
		target_harnesses?: string[];
	}) =>
		post<{ configs: Record<string, Record<string, string>> }>(
			"/agents/preview-config",
			body,
		),
	my: (type?: RegistryType) => get<RegistryItem[]>(`/${type ?? "agents"}/my`),
	archived: () => get<RegistryItem[]>("/agents/archived"),
	deletedAgents: () => get<RegistryItem[]>("/agents/deleted"),
	archive: (id: string) => patch(`/agents/${id}/archive`),
	unarchive: (id: string) => patch(`/agents/${id}/unarchive`),
	restoreDeletedAgent: (id: string, body?: { name?: string }) => patch(`/agents/${id}/restore`, body ?? {}),
	archiveComponent: (type: RegistryType, id: string) => patch(`/${type}/${id}/archive`),
	unarchiveComponent: (type: RegistryType, id: string) => patch(`/${type}/${id}/unarchive`),
	draft: (body: unknown, type?: RegistryType) =>
		post<RegistryItem>(`/${type ?? "agents"}/draft`, body),
	folderDraft: (body: SkillFolderDraftRequest) =>
		post<SkillVersionManifest>("/skills/folder-drafts", {
			name: body.name,
			version: body.version,
			description: body.description,
			owner: body.owner,
			task_type: body.task_type,
			skill_md_content: body.skill_md_content,
			extra_files: body.extra_files,
			team_id: body.team_id,
			visibility: body.visibility,
			supported_harnesses: body.supported_harnesses,
		}),
	updateDraft: (id: string, body: unknown, type?: RegistryType) =>
		put<RegistryItem>(`/${type ?? "agents"}/${id}/draft`, body),
	updateAgent: (id: string, body: unknown) =>
		put<RegistryItem>(`/agents/${id}`, body),
	submitDraft: (id: string, type?: RegistryType) =>
		post(`/${type ?? "agents"}/${id}/submit`),
	submit: (type: RegistryType, body: unknown) =>
		post<RegistryItem>(`/${type}/submit`, body),
	updateVisibility: (type: RegistryType, id: string, visibility: "public" | "team") =>
		patch<{ id: string; qualified_name: string; team_id: string | null; visibility: "public" | "team" }>(
			`/registry/${type === "agents" ? "agent" : type.slice(0, -1)}/${id}/visibility`,
			{ visibility },
		),
	versionSuggestions: (id: string) =>
		get<VersionSuggestions>(`/agents/${id}/version-suggestions`),
	listVersions: (agentId: string, page = 1, pageSize = 50) =>
		get<AgentVersionsResponse>(
			`/agents/${agentId}/versions?page=${page}&page_size=${pageSize}`,
		),
	getVersion: (agentId: string, version: string) =>
		get<AgentVersionDetail>(`/agents/${agentId}/versions/${version}`),
	createVersion: (agentId: string, body: unknown) =>
		post<unknown>(`/agents/${agentId}/versions`, body),
	getVersionDiff: (agentId: string, v1: string, v2: string) =>
		get<VersionDiff>(`/agents/${agentId}/versions/${v1}/diff/${v2}`),
	getVersionOutdated: (agentId: string, version: string) =>
		get<AgentVersionOutdated>(`/agents/${agentId}/versions/${version}/outdated`),

	// Component versions
	mcpWebhookSync: (listingId: string) => get<McpWebhookSync>(`/mcps/${listingId}/webhook-sync`),
	configureMcpWebhookSync: (listingId: string, body: McpWebhookSyncRequest) =>
		put<McpWebhookSync>(`/mcps/${listingId}/webhook-sync`, body),
	rotateMcpWebhookSecret: (listingId: string) =>
		post<McpWebhookSync>(`/mcps/${listingId}/webhook-sync/rotate-secret`),
	runMcpWebhookSync: (listingId: string) => post<McpWebhookSync>(`/mcps/${listingId}/webhook-sync/run`),
	disableMcpWebhookSync: (listingId: string) => del(`/mcps/${listingId}/webhook-sync`),
	listComponentVersions: (
		type: RegistryType,
		listingId: string,
		page = 1,
		pageSize = 50,
	) =>
		get<ComponentVersionsResponse>(
			`/${type}/${listingId}/versions?page=${page}&page_size=${pageSize}`,
		),
	getComponentVersion: (
		type: RegistryType,
		listingId: string,
		version: string,
	) => get<ComponentVersionDetail>(`/${type}/${listingId}/versions/${version}`),
	publishComponentVersion: (
		type: RegistryType,
		listingId: string,
		body: unknown,
	) => post<ComponentVersionDetail>(`/${type}/${listingId}/versions`, body),
	componentVersionSuggestions: (type: RegistryType, listingId: string) =>
		get<VersionSuggestions>(`/${type}/${listingId}/version-suggestions`),
	getSkillApprovedBase: (listingId: string) =>
		get<SkillApprovedBase>(`/skills/${listingId}/approved-base`),
	forkSkillVersion: (listingId: string, body: SkillSuccessorRequest) =>
		post<SkillVersionManifest>(`/skills/${listingId}/drafts`, body),
	importSkillFolder: (listingId: string, body: SkillSuccessorRequest & {
		skill_md_content: string; extra_files: SkillFolderDraftRequest["extra_files"];
	}) => post<SkillVersionManifest>(`/skills/${listingId}/folder-import-drafts`, body),
	getSkillVersionManifest: (listingId: string, versionId: string) =>
		get<SkillVersionManifest>(`/skills/${listingId}/versions/${versionId}/manifest`),
	updateSkillVersionDraft: (listingId: string, versionId: string, body: unknown) =>
		put<SkillVersionManifest>(`/skills/${listingId}/versions/${versionId}/draft`, body),
	submitSkillVersionDraft: (listingId: string, versionId: string, observedRevision: string) =>
		post<SkillVersionManifest>(`/skills/${listingId}/versions/${versionId}/submit`, { observed_revision: observedRevision }),
	withdrawSkillVersion: (listingId: string, versionId: string, observedRevision: string) =>
		post<SkillVersionManifest>(`/skills/${listingId}/versions/${versionId}/withdraw`, { observed_revision: observedRevision }),
	getSkillFileContent: (listingId: string, versionId: string, filePath: string) =>
		request<SkillFileContents | SkillBinaryContents>(
			"GET", `/skills/${listingId}/versions/${versionId}/files/${encodeURIComponent(filePath)}`, undefined, true
		),
	startEdit: (id: string, type?: RegistryType) =>
		post<{ status: string }>(`/${type ?? "agents"}/${id}/start-edit`),
	cancelEdit: (id: string, type?: RegistryType) =>
		post<{ status: string }>(`/${type ?? "agents"}/${id}/cancel-edit`),
};

// ── Review ──────────────────────────────────────────────────────────
export const review = {
	list: (params?: Record<string, string>) => {
		const qs = params ? `?${new URLSearchParams(params)}` : "";
		return get<ReviewItem[]>(`/review${qs}`);
	},
	listAgents: () => get<ReviewItem[]>("/review?tab=agents"),
	/**
	 * Pending queue narrowed to one teamspace. The server answers 403 when the
	 * caller does not review for that teamspace, so callers must gate on the
	 * team role before asking.
	 */
	listForTeam: (teamId: string, params?: Record<string, string>) =>
		get<ReviewItem[]>(`/review?${new URLSearchParams({ ...params, team_id: teamId })}`),
	get: (id: string) => get<ReviewItem>(`/review/${id}`),
	getSkillVersion: (id: string, versionId: string) => get<ReviewItem>(`/review/skills/${id}/versions/${versionId}`),
	decideSkillVersion: (id: string, versionId: string, action: "approve" | "reject", observedRevision: string, reason?: string, gitBaseAcknowledged = false) =>
		post(`/review/skills/${id}/versions/${versionId}/decision`, {
			action, observed_revision: observedRevision, reason, git_base_acknowledged: gitBaseAcknowledged,
		}),
	approve: (id: string) => post(`/review/${id}/approve`),
	reject: (id: string, body: { reason: string }) =>
		post(`/review/${id}/reject`, body),
	approveAgent: (id: string, body?: { category?: string }) =>
		post(`/review/agents/${id}/approve`, body),
	rejectAgent: (id: string, body: { reason: string }) =>
		post(`/review/agents/${id}/reject`, body),
	approveBundle: (id: string) => post(`/review/bundles/${id}/approve`),
	rejectBundle: (id: string, body: { reason: string }) =>
		post(`/review/bundles/${id}/reject`, body),
	relatedSkills: (id: string) =>
		get<{ skills: ReviewItem[] }>(`/review/${id}/related-skills`),
	approveWithSkills: (id: string, body: { skill_ids: string[] }) =>
		post(`/review/${id}/approve-with-skills`, body),
};

// ── Telemetry ───────────────────────────────────────────────────────
export const telemetry = {
	status: () => get<TelemetryStatus>("/telemetry/status"),
};

// ── Users ───────────────────────────────────────────────────────────
export const users = {
	search: (params: { q: string; limit?: number }) => {
		const qs = new URLSearchParams({ q: params.q });
		if (params.limit) qs.set("limit", String(params.limit));
		return get<UserSearchResult[]>(`/users/search?${qs}`);
	},
};

// ── Teamspaces ──────────────────────────────────────────────────────
export const teams = {
	list: () => get<Team[]>("/teams"),
	listAll: () => get<Team[]>("/teams/all"),
	get: (id: string) => get<Team>(`/teams/${id}`),
	byHandle: (handle: string) => get<Team>(`/teams/by-handle/${encodeURIComponent(handle)}`),
	create: (body: { name: string; handle?: string; description?: string; visibility?: "public" | "private" }) =>
		post<Team>("/teams", body),
	claimPersonal: () => post<Team>("/teams/claim-personal"),
	update: (id: string, body: TeamUpdateBody) =>
		put<Team>(`/teams/${id}`, body),
	updateVisibility: (id: string, visibility: "public" | "private") =>
		patch<Team>(`/teams/${id}/visibility`, { visibility }),
	visibilityRequests: () => get<TeamVisibilityRequest[]>("/teams/visibility-requests"),
	approveVisibility: (id: string) => post<Team>(`/teams/${id}/visibility-request/approve`),
	rejectVisibility: (id: string, reason?: string) =>
		post<Team>(`/teams/${id}/visibility-request/reject`, reason ? { reason } : {}),
	delete: (id: string) => del(`/teams/${id}`),
	members: (id: string) => get<TeamMember[]>(`/teams/${id}/members`),
	upsertMember: (
		id: string,
		body: TeamMemberUpsertBody,
	) => post<TeamMember>(`/teams/${id}/members`, body),
	removeMember: (id: string, userId: string) => del(`/teams/${id}/members/${userId}`),
	leave: (id: string) => post(`/teams/${id}/leave`),
	invites: (id: string) => get<TeamInvite[]>(`/teams/${id}/invites`),
	createInvite: (id: string, body: { name?: string; expires_in_days?: number; max_uses?: number | null }) =>
		post<TeamInviteCreated>(`/teams/${id}/invites`, body),
	revokeInvite: (teamId: string, inviteId: string) =>
		post<TeamInvite>(`/teams/${teamId}/invites/${inviteId}/revoke`),
	deleteInvite: (teamId: string, inviteId: string) => del(`/teams/${teamId}/invites/${inviteId}`),
	inviteRequests: (teamId: string, inviteId: string) =>
		get<TeamJoinRequest[]>(`/teams/${teamId}/invites/${inviteId}/requests`),
	previewInvite: (token: string) =>
		post<TeamInvitePreview>("/teams/invites/preview", { token }),
	// Membership join requests: a shared teamspace link leads to an explicit
	// request; only an owner's approval changes the roster.
	requestJoin: (id: string, body: { message?: string; invite_token?: string }) =>
		post<TeamJoinRequest>(`/teams/${id}/join-requests`, body),
	joinRequests: (id: string, status?: TeamJoinRequestStatus) =>
		get<TeamJoinRequest[]>(`/teams/${id}/join-requests${status ? `?status=${status}` : ""}`),
	myJoinRequests: (id: string) => get<TeamJoinRequest[]>(`/teams/${id}/join-requests/mine`),
	approveJoinRequest: (id: string, requestId: string) =>
		post<TeamJoinRequest>(`/teams/${id}/join-requests/${requestId}/approve`),
	rejectJoinRequest: (id: string, requestId: string, body?: { reason?: string }) =>
		post<TeamJoinRequest>(`/teams/${id}/join-requests/${requestId}/reject`, body ?? {}),
	cancelJoinRequest: (id: string, requestId: string) =>
		del(`/teams/${id}/join-requests/${requestId}`),
};

// ── Dashboard ───────────────────────────────────────────────────────
export const dashboard = {
	stats: (range?: string) =>
		get<OverviewStats>(`/overview/stats${range ? `?range=${range}` : ""}`),
	topMcps: () => get<TopItem[]>("/overview/top-mcps"),
	topAgents: (limit?: number) =>
		get<TopAgentItem[]>(
			`/overview/top-agents${limit ? `?limit=${limit}` : ""}`,
		),
	leaderboard: (window?: LeaderboardWindow, limit?: number, user?: string) => {
		const params = new URLSearchParams();
		if (window) params.set("window", window);
		if (limit) params.set("limit", String(limit));
		if (user) params.set("user", user);
		const qs = params.toString();
		return get<LeaderboardItem[]>(`/overview/leaderboard${qs ? `?${qs}` : ""}`);
	},
	componentLeaderboard: (window?: LeaderboardWindow, limit?: number, user?: string) => {
		const params = new URLSearchParams();
		if (window) params.set("window", window);
		if (limit) params.set("limit", String(limit));
		if (user) params.set("user", user);
		const qs = params.toString();
		return get<ComponentLeaderboardItem[]>(
			`/overview/component-leaderboard${qs ? `?${qs}` : ""}`,
		);
	},
	trends: (range?: string) =>
		get<TrendPoint[]>(`/overview/trends${range ? `?range=${range}` : ""}`),
	tokenStats: (range?: string) =>
		get<TokenStats>(`/dashboard/tokens${range ? `?range=${range}` : ""}`),
	harnessUsage: () => get<HarnessUsageData>("/dashboard/harness-usage"),
	sessions: (params?: {
		status?: string;
		platform?: string;
		user?: string;
		days?: number;
		limit?: number;
		offset?: number;
		mine?: boolean;
	}) => {
		const qs = new URLSearchParams();
		if (params?.status) qs.set("status", params.status);
		if (params?.platform) qs.set("platform", params.platform);
		if (params?.user) qs.set("user", params.user);
		if (params?.days) qs.set("days", String(params.days));
		if (params?.limit) qs.set("limit", String(params.limit));
		if (params?.offset) qs.set("offset", String(params.offset));
		if (params?.mine) qs.set("mine", "true");
		const suffix = qs.toString() ? `?${qs}` : "";
		return get<Session[]>(`/sessions${suffix}`);
	},
	sessionsSummary: () => get<SessionsSummary>("/sessions/summary"),
	session: (id: string) =>
		get<SessionData>(`/sessions/${encodeURIComponent(id)}`),
	sessionsStats: () => get<SessionsStats>("/sessions/stats"),
	sessionsErrors: () => get<SessionErrorEvent[]>("/sessions/errors"),
};

// ── Feedback ────────────────────────────────────────────────────────
export const feedback = {
	submit: (body: {
		listing_type: string;
		listing_id: string;
		rating: number;
		comment?: string;
		anonymous?: boolean;
	}) => post<FeedbackItem>("/feedback", body),
	get: (type: string, id: string) =>
		get<FeedbackItem[]>(`/feedback/${type}/${id}`),
	summary: (id: string) => get<FeedbackSummary>(`/feedback/summary/${id}`),
	mine: (type: string, id: string) =>
		get<FeedbackItem>(`/feedback/mine/${type}/${id}`),
	update: (feedbackId: string, body: {
		rating?: number;
		comment?: string;
		anonymous?: boolean;
	}) => put<FeedbackItem>(`/feedback/${feedbackId}`, body),
	remove: (feedbackId: string) => del<void>(`/feedback/${feedbackId}`),
};

// ── Admin ───────────────────────────────────────────────────────────
export const admin = {
	settings: () =>
		get<AdminSetting[] | Record<string, string>>("/admin/settings"),
	settingsSchema: () => get<AdminSettingSection[]>("/admin/settings/schema"),
	usagePingStatus: () => get<UsagePingStatus>("/admin/usage-ping/status"),
	usagePingPreview: () => get<UsagePingAdminResponse>("/admin/usage-ping/preview"),
	sendUsagePing: () => post<UsagePingAdminResponse>("/admin/usage-ping/send", {}),
	updateSetting: (key: string, body: unknown) =>
		put<unknown>(`/admin/settings/${key}`, body),
	deleteSetting: (key: string) => del(`/admin/settings/${key}`),
	revokeSetting: (key: string) =>
		post<{ revoked: string; message: string }>(
			`/admin/settings/${key}/revoke`,
			{},
		),
	testInsightsConnection: (body?: { model?: string }) =>
		post<{
			success: boolean;
			model?: string;
			latency_ms?: number;
			error?: string;
			hint?: string;
		}>("/admin/insights/test-connection", body ?? {}),
	insightsModelProviders: () => get<import("./types").LiteLLMProviderList>("/admin/insights/models/providers"),
	insightsModels: (provider: string) =>
		get<import("./types").LiteLLMModelList>(`/admin/insights/models?provider=${encodeURIComponent(provider)}`),
	purgeTracesAndInsights: () =>
		post<{
			project_id: string;
			clickhouse_tables: string[];
			deleted_reports?: number;
			deleted_facets?: number;
			deleted_session_meta?: number;
			deleted_meta_cache?: number;
		}>("/admin/settings/danger/purge-traces-insights", {}),
	users: () => get<AdminUser[]>("/admin/users"),
	createUser: (body: {
		email: string;
		name: string;
		username?: string;
		role?: string;
	}) =>
		post<{
			id: string;
			email: string;
			name: string;
			username?: string;
			role: string;
			password: string;
		}>("/admin/users", body),
	updateRole: (id: string, body: { role: string }) =>
		put<AdminUser>(`/admin/users/${id}/role`, body),
	updateDepartment: (id: string, body: { department: string | null }) =>
		put<AdminUser>(`/admin/users/${id}/department`, body),
	bulkDepartment: (entries: { email: string; department: string }[]) =>
		post<{ updated: number; not_found: string[] }>(
			"/admin/users/bulk-department",
			{ entries },
		),
	resetPassword: (
		id: string,
		body: { new_password?: string; generate?: boolean },
	) =>
		put<{
			message: string;
			generated_password?: string;
			must_change_password?: string;
		}>(`/admin/users/${id}/password`, body),
	deleteUser: (id: string) => del(`/admin/users/${id}`),
	applyResources: () =>
		post<{ applied: Record<string, string>; message: string }>(
			"/admin/resources/apply",
			{},
		),
	getTracePrivacy: () =>
		get<{ trace_privacy: boolean }>("/admin/trace-privacy"),
	setTracePrivacy: (enabled: boolean) =>
		put<{ trace_privacy: boolean }>("/admin/trace-privacy", {
			trace_privacy: enabled,
		}),
	getRegisteredAgentsOnly: () =>
		get<{ registered_agents_only: boolean }>(
			"/admin/registered-agents-only",
		),
	setRegisteredAgentsOnly: (enabled: boolean) =>
		put<{ registered_agents_only: boolean }>(
			"/admin/registered-agents-only",
			{ registered_agents_only: enabled },
		),
	auditLog: (params?: Record<string, string>) => {
		const qs = params ? `?${new URLSearchParams(params)}` : "";
		return get<AuditLogEntry[]>(`/admin/audit-log${qs}`);
	},
	auditLogExport: async (params?: Record<string, string>) => {
		const qs = params ? `?${new URLSearchParams(params)}` : "";
		const token = getAccessToken();
		const headers: Record<string, string> = {};
		if (token) headers["Authorization"] = `Bearer ${token}`;
		const res = await fetch(`${API}/admin/audit-log/export${qs}`, { headers });
		if (!res.ok) throw new Error("Export failed");
		return res.text();
	},
	securityEvents: (params?: Record<string, string>) => {
		const qs = params ? `?${new URLSearchParams(params)}` : "";
		return get<{ events: SecurityEvent[]; total: number }>(
			`/admin/security-events${qs}`,
		);
	},
	diagnostics: () => get<DiagnosticsResponse>("/admin/diagnostics"),
	restartStatus: () => get<RestartStatus>("/admin/restart/status"),
	systemWarnings: () => get<SystemWarning[]>("/admin/system-warnings"),
	samlConfig: () => get<Record<string, unknown>>("/admin/saml-config"),
	updateSamlConfig: (body: Record<string, unknown>) =>
		put<Record<string, unknown>>("/admin/saml-config", body),
	deleteSamlConfig: () => del("/admin/saml-config"),
	restartApi: () =>
		post<{ detail: string; delay_seconds: number }>("/admin/restart", {}),
	validateOidc: () => post<ValidateResult>("/admin/sso/validate-oidc", {}),
	validateSaml: () => post<ValidateResult>("/admin/sso/validate-saml", {}),
	e2eOidcStart: () =>
		post<E2eStartResult>("/admin/sso/e2e/oidc/start", {}),
	e2eSamlStart: () =>
		post<E2eStartResult>("/admin/sso/e2e/saml/start", {}),
	e2eStatus: (sessionId: string) =>
		get<E2eStatusResult>(`/admin/sso/e2e/status/${encodeURIComponent(sessionId)}`),
	scimTokens: () =>
		get<
			{
				id: string;
				description: string;
				active: boolean;
				created_at: string;
				token_prefix: string;
			}[]
		>("/admin/scim-tokens"),
	createScimToken: (body: { description?: string }) =>
		post<{ id: string; token: string; description: string; message: string }>(
			"/admin/scim-tokens",
			body,
		),
	revokeScimToken: (id: string) => del(`/admin/scim-tokens/${id}`),
	getRetention: () => get<RetentionConfig>("/admin/retention"),
	setRetention: (body: RetentionConfigUpdate) =>
		put<RetentionConfig>("/admin/retention", body),
	previewRetention: (days: number) =>
		get<RetentionPreview>(`/admin/retention/preview?days=${days}`),
	getRetentionStats: () => get<RetentionStats>("/admin/retention/stats"),
	getRetentionWarnings: () =>
		get<RetentionWarnings>("/admin/retention/warnings"),
	// ── Migration ──────────────────────────────────────────────
	migrateExport: (scope: string) =>
		post<{ job_id: string }>("/admin/migrate/export", { scope }),
	migrateImport: async (formData: FormData) => {
		const token = getAccessToken();
		const headers: Record<string, string> = {};
		if (token) headers["Authorization"] = `Bearer ${token}`;
		const res = await fetch(`${API}/admin/migrate/import`, {
			method: "POST",
			headers,
			body: formData,
		});
		if (!res.ok) {
			const text = await res.text().catch(() => "Import failed");
			let detail = text;
			try {
				const parsed = JSON.parse(text);
				if (parsed.detail) detail = parsed.detail;
			} catch {
				/* raw text */
			}
			throw new Error(detail);
		}
		return res.json() as Promise<{ job_id: string }>;
	},
	migrateValidate: async (formData: FormData) => {
		const token = getAccessToken();
		const headers: Record<string, string> = {};
		if (token) headers["Authorization"] = `Bearer ${token}`;
		const res = await fetch(`${API}/admin/migrate/validate`, {
			method: "POST",
			headers,
			body: formData,
		});
		if (!res.ok) {
			const text = await res.text().catch(() => "Validate failed");
			let detail = text;
			try {
				const parsed = JSON.parse(text);
				if (parsed.detail) detail = parsed.detail;
			} catch {
				/* raw text */
			}
			throw new Error(detail);
		}
		return res.json() as Promise<{ job_id: string }>;
	},
	migrateJob: (id: string) =>
		get<import("./types/admin").MigrationJob>(`/admin/migrate/jobs/${id}`),
	migrateJobs: () =>
		get<import("./types/admin").MigrationJob[]>("/admin/migrate/jobs"),
	migrateDownloadToken: (jobId: string, name: string) =>
		post<import("./types/admin").MigrationDownloadToken>(
			`/admin/migrate/jobs/${jobId}/artifacts/${name}/token`,
			{},
		),
	setRecommended: (body: SetRecommendedRequest) =>
		patch<SetRecommendedResponse>("/admin/recommended", body),
};

// ── Retention Types ───────────────────────────────────────────────
export type RetentionConfig = {
	retention_enabled: boolean;
	data_retention_days: number | null;
	score_retention_days: number | null;
	max_trace_count: number | null;
	global_retention_days: number;
};

export type RetentionConfigUpdate = {
	retention_enabled: boolean;
	data_retention_days?: number | null;
	score_retention_days?: number | null;
	max_trace_count?: number | null;
};

export type RetentionPreview = {
	traces: number;
	spans: number;
	scores: number;
	session_events: number;
	insight_reports: number;
};

export type RetentionStats = {
	retention_enabled: boolean;
	data_retention_days: number | null;
	score_retention_days: number | null;
	total_traces: number;
	oldest_trace_age_days: number;
	traces_expiring_7d: number;
	next_purge_approx: string | null;
};

export type RetentionWarnings = {
	warnings: {
		agent_id: string;
		agent_name: string;
		traces_expiring_soon: number;
		last_insight_report: string | null;
	}[];
	retention_days: number | null;
	retention_enabled: boolean;
};

// ── Config ─────────────────────────────────────────────────────────
export type PublicConfig = {
	licensed: boolean;
	licensed_features: string[];
	sso_enabled: boolean;
	google_sso_enabled: boolean;
	github_sso_enabled: boolean;
	sso_only: boolean;
	public_registry_enabled: boolean;
	self_registration_enabled: boolean;
	/** Public rollout hint; server install and review endpoints remain authoritative. */
	skill_folder_delivery_enabled?: boolean;
	saml_enabled: boolean;
	exec_dashboard_available: boolean;
	enabled_features: string[];
	branding_logo: string | null;
	branding_app_name: string | null;
	branding_wordmark: string | null;
};

export type VersionConfig = {
	server_version: string;
	max_cli_version: string | null;
	api_version: string | null;
	frontend_version: string;
	recommended_cli_version: string;
};

export type HarnessMcpInstallMode = "file" | "setup_command" | "adapter" | "user_only";

/** Verified per-harness runtime facts (see harness_registry.py). */
export interface HarnessRuntimeFacts {
	mcp_install_mode: HarnessMcpInstallMode;
	dynamic_tools: boolean;
	prompt_context_injection: boolean;
	guidance_file_write: boolean;
}

export interface HarnessEntry {
	name: string;
	display_name: string;
	capabilities: string[];
	supported_models: string[];
	runtime: HarnessRuntimeFacts;
}

interface HarnessesResponse {
	harnesses: HarnessEntry[];
	default_harness?: string | null;
}

export type HealthCheck = {
	name: string;
	label: string;
	status: "pass" | "fail" | "skip";
	message?: string;
	hint?: string;
};

export type SsoProbeResult = {
	ok: boolean;
	latency_ms?: number;
	error?: string;
	checks?: HealthCheck[];
};

export type SsoHealthResult = {
	oidc: SsoProbeResult | null;
	saml: SsoProbeResult | null;
};

export type ValidateResult = {
	success: boolean;
	issuer?: string;
	idp_entity_id?: string;
	latency_ms?: number;
	error?: string;
	hint?: string;
	checks?: HealthCheck[];
};

export type E2eStartResult = {
	success: boolean;
	session_id?: string;
	login_url?: string;
	issuer?: string;
	idp_entity_id?: string;
	redirect_uri?: string;
	instructions?: string;
	error?: string;
	hint?: string;
	checks?: HealthCheck[];
};

export type E2eStatusResult = {
	session_id: string;
	provider: "oidc" | "saml";
	mode: "real" | "e2e";
	ok: boolean | null;
	checks: HealthCheck[];
	actor_email: string | null;
	summary: string | null;
	started_at: number | null;
	finished_at: number | null;
};

export const config = {
	public: () => get<PublicConfig>("/config/public"),
	version: () => get<VersionConfig>("/config/version"),
	harnesses: () => get<HarnessesResponse>("/config/harnesses"),
	ssoHealth: () => get<SsoHealthResult>("/config/sso-health"),
};

// ── Bulk ───────────────────────────────────────────────────────────
export const bulk = {
	createAgents: (body: { agents: unknown[]; dry_run?: boolean }) =>
		post<BulkResult>("/bulk/agents", body),
};

// ── Insights ───────────────────────────────────────────────────────
export const recommendations = {
	me: (limit = 8, type?: string) =>
		get<RecommendationsResponse>(
			`/recommendations/me?limit=${limit}${type ? `&type=${encodeURIComponent(type)}` : ""}`,
		),
	feedback: (componentType: string, componentId: string, action: string) =>
		post<void>("/recommendations/feedback", {
			component_type: componentType,
			component_id: componentId,
			action,
		}),
};

// ── Discovery (ARD) ─────────────────────────────────────────────────
const DISCOVERY_MEDIA_TYPES: Record<string, string> = {
	skill: "application/ai-skill+md",
	mcp: "application/mcp-server-card+json",
	prompt: "application/vnd.observal.prompt+json",
	sandbox: "application/vnd.observal.sandbox+json",
	hook: "application/vnd.observal.hook+json",
	agent: "application/vnd.observal.agent+json",
	external: "application/a2a-agent-card+json",
};

export const discovery = {
	/** ARD Search. Omitting federation means the spec's `auto`, bounded by the admin allowlist. */
	search: (text: string, filter: DiscoverySearchFilter = {}, pageSize = 10, pageToken?: string) => {
		const ardFilter: Record<string, string[]> = {};
		if (filter.kind) ardFilter.type = [DISCOVERY_MEDIA_TYPES[filter.kind]];
		if (filter.harness) ardFilter["obs:supportedHarnesses"] = [filter.harness];
		if (!filter.includeUnapproved) ardFilter["obs:lifecycle"] = ["approved"];
		const body: Record<string, unknown> = {
			query: { text, filter: ardFilter },
			federation: "none",
			pageSize,
		};
		if (pageToken) body.pageToken = pageToken;
		return post<DiscoverySearchResponse>("/ard/search", body);
	},
	entry: (identifier: string) =>
		get<Record<string, unknown>>(`/ard/entries/${encodeURIComponent(identifier)}`),
};

// ── Inbox ──────────────────────────────────────────────────────────
function inboxQuery(filters: InboxFilters = {}): string {
	const params = new URLSearchParams();
	for (const [key, value] of Object.entries(filters)) {
		if (value !== undefined && value !== null && value !== "") {
			params.set(key, String(value));
		}
	}
	const qs = params.toString();
	return qs ? `?${qs}` : "";
}

export const inbox = {
	list: (filters: InboxFilters = {}) =>
		get<InboxListResponse>(`/inbox${inboxQuery(filters)}`),
	// Facets are opt-in: the nav badge polls this every minute for one number
	// and should not pay for the sidebar's per-kind breakdown.
	counts: (opts: { facets?: boolean; facetState?: InboxState } = {}) =>
		get<InboxCounts>(
			`/inbox/count${
				opts.facets
					? `?facets=true${opts.facetState ? `&facet_state=${opts.facetState}` : ""}`
					: ""
			}`,
		),
	detail: (id: string) => get<InboxItemDetail>(`/inbox/${id}`),
	read: (id: string) => post<InboxItem>(`/inbox/${id}/read`),
	unread: (id: string) => post<InboxItem>(`/inbox/${id}/unread`),
	done: (id: string) => post<InboxItem>(`/inbox/${id}/done`),
	dismiss: (id: string) => post<InboxItem>(`/inbox/${id}/dismiss`),
	reopen: (id: string) => post<InboxItem>(`/inbox/${id}/reopen`),
	// Filter-scoped on purpose: a blanket read-all over an actionable feed
	// clears the unread signal on work nobody has looked at.
	readAll: (filters: InboxFilters = {}) =>
		post<{ updated: number }>(`/inbox/read-all${inboxQuery(filters)}`),
};

export const insights = {
	status: () => get<{ available: boolean; reason: string | null }>("/insights/status"),
	sessionCount: (agentId: string, agentVersion?: string) =>
		get<{ session_count: number; agent_version?: string; agent_version_id?: string }>(
			`/agents/${agentId}/insights/session-count${agentVersion ? `?agent_version=${encodeURIComponent(agentVersion)}` : ""}`,
		),
	generate: (agentId: string, periodDays?: number, agentVersion?: string, comparisonAgentVersion?: string) =>
		post<InsightReportListItem>(`/agents/${agentId}/insights/reports`, {
			...(periodDays ? { period_days: periodDays } : {}),
			...(agentVersion ? { agent_version: agentVersion } : {}),
			...(comparisonAgentVersion ? { comparison_agent_version: comparisonAgentVersion } : {}),
		}),
	listReports: (agentId: string) =>
		get<InsightReportListItem[]>(`/agents/${agentId}/insights/reports`),
	getReport: (agentId: string, reportId: string) =>
		get<InsightReport>(`/agents/${agentId}/insights/reports/${reportId}`),
	getReportById: (reportId: string) =>
		get<InsightReport>(`/insights/reports/${reportId}`),
	applySuggestions: (agentId: string, reportId: string, selection?: { config_indices?: number[]; feature_indices?: number[]; pattern_indices?: number[] }) =>
		post<{ applied: boolean; report_id: string; items: InsightAppliedItems }>(
			`/agents/${agentId}/insights/reports/${reportId}/apply`,
			selection ?? {},
		),
	exportHtml: async (agentId: string, reportId: string): Promise<void> => {
		let token = getAccessToken();
		let res = await fetch(`${API}/agents/${agentId}/insights/reports/${reportId}/export/html`, {
			headers: token ? { Authorization: `Bearer ${token}` } : {},
		});
		if (res.status === 401) {
			const refreshed = await _tryRefreshToken();
			if (refreshed) {
				token = getAccessToken();
				res = await fetch(`${API}/agents/${agentId}/insights/reports/${reportId}/export/html`, {
					headers: token ? { Authorization: `Bearer ${token}` } : {},
				});
			}
		}
		if (!res.ok) throw new Error("Export failed");
		const blob = await res.blob();
		const url = URL.createObjectURL(blob);
		const a = document.createElement("a");
		a.href = url;
		a.download = `insight-report-${reportId.slice(0, 8)}.html`;
		a.click();
		URL.revokeObjectURL(url);
	},
};

// ── Exec Dashboard ─────────────────────────────────────────────────
export const exec = {
	adoption: () => get<ExecAdoptionResponse>("/exec/adoption"),
	agentCounts: () => get<ExecAgentCounts>("/exec/agent-counts"),
	usageByCategory: (range?: string) =>
		get<ExecUsageByCategory[]>(
			`/exec/usage-by-category${range ? `?range=${range}` : ""}`,
		),
	platformCoverage: () =>
		get<ExecPlatformCoverage[]>("/exec/platform-coverage"),
	platforms: () => get<ExecPlatformScore[]>("/exec/platforms"),
	velocity: () => get<ExecVelocityResponse>("/exec/velocity"),
	topAgents: (limit?: number) =>
		get<ExecTopAgent[]>(`/exec/top-agents${limit ? `?limit=${limit}` : ""}`),
	departments: (range?: string) =>
		get<ExecDepartmentsResponse>(
			`/exec/departments${range ? `?range=${range}` : ""}`,
		),
	deptTokens: (range?: string) =>
		get<ExecDeptTokenItem[]>(
			`/exec/dept-tokens${range ? `?range=${range}` : ""}`,
		),
	costSummary: (range?: string) =>
		get<ExecCostSummary>(`/exec/cost-summary${range ? `?range=${range}` : ""}`),
	roiProjections: () =>
		get<ExecROIProjectionsResponse>("/exec/roi-projections"),
	strategicInsights: () =>
		get<ExecStrategicInsightsResponse>("/exec/strategic-insights"),
	developerBreakdown: (limit?: number) =>
		get<ExecDeveloperBreakdown>(
			`/exec/developer-breakdown${limit ? `?limit=${limit}` : ""}`,
		),
	inactivityAlerts: () => get<ExecInactivityAlerts>("/exec/inactivity-alerts"),
	timeToValue: () => get<ExecTimeToValueResponse>("/exec/time-to-value"),
	aiInsights: () => get<ExecAIInsightsResponse>("/exec/ai-insights"),
	generateAiInsights: () => post<ExecAIInsightsResponse>("/exec/ai-insights"),
	config: () => get<ExecConfig | null>("/exec/config"),
	updateConfig: (data: Partial<ExecConfig>) =>
		put<ExecConfig>("/exec/config", data),
};

// ── Health ──────────────────────────────────────────────────────────
export const health = () => fetch("/health").then((r) => r.json());
