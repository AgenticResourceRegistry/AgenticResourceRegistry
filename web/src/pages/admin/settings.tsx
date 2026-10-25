// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-License-Identifier: Apache-2.0

/**
 * Admin settings: one page, grouped into areas.
 *
 * A sticky nav jumps to each area (click) or to a section within it (hover menu),
 * and the active area follows the scroll position. Switches save as soon as they
 * are flipped; typed values are saved per card from the card footer.
 */
import { useState, useCallback, useEffect, useLayoutEffect, useRef, useMemo } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from "react";
import { Link, useBlocker } from "@tanstack/react-router";
import {
	AlertTriangle,
	ArrowLeftRight,
	ArrowRight,
	ChevronRight,
	HelpCircle,
	Loader2,
	Power,
	RotateCcw,
	ShieldAlert,
	ShieldCheck,
	Trash2,
	Upload,
} from "lucide-react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import { InsightsSection } from "./settings/insights-section";
import { UsagePingSection } from "./settings/usage-ping-section";
import { SchemaSection } from "./settings/schema-section";
import { PURGE_KEY, areaDomId, rowDomId, sectionDomId, settingDescription } from "./settings/settings-helpers";
import { SettingsNav, type NavArea, type NavSection } from "./settings/settings-nav";
import {
	CardFooter,
	SettingRow,
	SettingsArea,
	SettingsCard,
	SettingsGroup,
	SwitchStateLabel,
} from "./settings/settings-ui";
import { MigrateDialog } from "./dashboard/components/migrate-dialog";
import { useHelp } from "@/components/wiki/help-context";
import { useAdminSettings, useAdminSettingsSchema, useRestartApi, useRestartStatus, useSystemWarnings } from "@/hooks/use-api";
import { useDeploymentConfig } from "@/hooks/use-deployment-config";
import { useHarnesses } from "@/hooks/use-harnesses";
import { LEADERBOARD_DEPRECATION_NOTICE } from "@/lib/leaderboard";
import { useRoleGuard } from "@/hooks/use-role-guard";
import type { AdminSetting, AdminSettingDef, AdminSettingSection, SystemWarning } from "@/lib/types";
import { admin } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
	Dialog,
	DialogContent,
	DialogHeader,
	DialogTitle,
	DialogDescription,
	DialogFooter,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { PageHeader } from "@/components/layouts/page-header";
import { TableSkeleton } from "@/components/shared/skeleton-layouts";
import { ErrorState } from "@/components/shared/error-state";

// Keys with a dedicated control on this page; never rendered again by a schema section.
const DEDICATED_KEYS = new Set([
	"deployment.public_registry_enabled",
	"deployment.leaderboard_enabled",
	"security.trace_privacy",
	"registry.registered_agents_only",
	"retention.enabled",
	"retention.trace_days",
	"retention.score_days",
	"retention.max_trace_count",
]);
// Schema sections with their own page (SSO) or their own component on this page.
const EXCLUDED_SECTIONS = new Set(["sso", "usage_ping", "experimental", "insights"]);

const ALLOWED_LOGO_TYPES = [
	"image/png",
	"image/svg+xml",
	"image/x-icon",
	"image/vnd.microsoft.icon",
	"image/jpeg",
	"image/webp",
];
const MAX_LOGO_SIZE = 2 * 1024 * 1024;

/** Where each schema section lives. Unknown sections fall back to Deployment. */
const SCHEMA_LAYOUT: Record<string, { area: string; advanced?: boolean }> = {
	auth: { area: "access" },
	registry: { area: "registry" },
	discovery: { area: "registry" },
	misc: { area: "registry" },
	data: { area: "data" },
	danger: { area: "data" },
	security: { area: "security" },
	jwt: { area: "security" },
	deployment: { area: "deployment" },
	observability: { area: "deployment" },
	resource: { area: "deployment", advanced: true },
};

/** A setting the search can find. `key` doubles as the row's DOM id source. */
interface SearchEntry {
	key: string;
	label: string;
	description: string;
	area: string;
	section: string;
	sectionTitle: string;
	/** Schema settings show their key, like their row does. */
	showKey?: boolean;
}

/** Areas, in page order, with the dedicated sections they hold. */
const AREAS: { id: string; label: string }[] = [
	{ id: "general", label: "General" },
	{ id: "access", label: "Access" },
	{ id: "registry", label: "Registry" },
	{ id: "insights", label: "Agent Insights" },
	{ id: "data", label: "Data" },
	{ id: "security", label: "Security" },
	{ id: "deployment", label: "Deployment" },
];

/** Dedicated (non-schema) sections and their rows, for the nav and the search. */
const DEDICATED_SECTIONS: { area: string; id: string; title: string; description?: string; rows: [string, string, string][]; after?: string }[] = [
	{ area: "general", id: "appearance", title: "Appearance", rows: [
		["branding.logo", "Icon", "PNG, SVG, ICO, JPEG, or WEBP. Max 2MB."],
		["branding.wordmark", "Wordmark", "Optional, replaces text"],
		["branding.app_name", "App Name", "Used when no wordmark"],
		["branding.preview", "Preview", ""],
	] },
	{ area: "general", id: "interface", title: "Interface", rows: [
		["deployment.leaderboard_enabled", "Show the Leaderboard", "Off by default. When off, the Leaderboard tab, its home page link, the page, and its API are unavailable to everyone, including admins."],
	] },
	{ area: "general", id: "experimental", title: "Experimental", description: "Features that are not ready for general use. Off by default, and hidden everywhere while off.", rows: [
		["experimental.review_checks", "Review checks", "Automated checks on review revisions, with per-organization and per-teamspace settings."],
	] },
	{ area: "access", id: "overview", title: "System Overview", rows: [
		["status.sso", "SSO (OAuth/OIDC)", ""],
		["status.saml", "SAML SSO", ""],
	] },
	{ area: "access", id: "public-registry", title: "Public Registry", after: "auth", rows: [
		["deployment.public_registry_enabled", "Allow public browsing and installs", "Signed-out visitors can browse and install approved public Agent Files and components."],
	] },
	{ area: "access", id: "trace-privacy", title: "Trace Privacy", rows: [
		["security.trace_privacy", "Restrict trace visibility", "When enabled, all users (including admins) can only see their own traces."],
	] },
	{ area: "access", id: "registered-only", title: "Registered Agent Files Only", rows: [
		["registry.registered_agents_only", "Only trace registered Agent Files", "Unstable. When enabled, only registered Agent Files are traced."],
	] },
	{ area: "registry", id: "review-policy", title: "Review policy", rows: [
		["review-policy", "Review policy", "Approval counts and publication rules"],
	] },
	{ area: "insights", id: "provider", title: "Agent File Insights", description: "Configure LLM provider for the insights engine.", rows: [
		["insights.provider", "Provider", ""],
		["insights.api_key", "API Key", ""],
		["insights.api_base", "Base URL", ""],
		["insights.api_version", "API Version", ""],
	] },
	{ area: "insights", id: "models", title: "Models", description: "Only Sections Model is required. Others fall back to it if left empty.", rows: [
		["insights.model_sections", "Sections Model", "Writes the detailed report. Use your best model."],
		["insights.model_synthesis", "Synthesis Model", "Aggregates patterns. Falls back to Sections if empty."],
		["insights.model_facets", "Facets Model", "Scans each session. Runs many times, use cheapest."],
	] },
	{ area: "insights", id: "insights-advanced", title: "Advanced", description: "Batch processing and facet limits.", rows: [
		["insights.batch_enabled", "Batch Processing", "Automatically generate reports on schedule"],
		["insights.batch_period_days", "Batch Period (days)", ""],
		["insights.min_sessions", "Min Sessions", ""],
		["insights.facet_max_calls", "Max Facet Calls", ""],
		["insights.facet_concurrency", "Facet Concurrency", ""],
	] },
	{ area: "data", id: "retention", title: "Data Retention", rows: [
		["retention.enabled", "Enable data retention", "Automatically purge telemetry data older than the configured period."],
		["retention.trace_days", "Trace retention (days)", ""],
		["retention.score_days", "Score & insight retention (days)", ""],
		["retention.max_trace_count", "Max trace count (optional)", ""],
	] },
	{ area: "data", id: "usage", title: "Usage reporting", rows: [
		["usage_ping.enabled", "Share aggregate product usage with Observal", "Sends aggregate reports to usage.observal.io on the schedule you choose."],
		["usage_ping.company_name", "Company name", ""],
		["usage_ping.frequency", "Reporting frequency", ""],
	] },
	{ area: "data", id: "migration", title: "Data Migration", rows: [
		["migration", "Export, import, and validate instance data", "Move registry data and telemetry between Observal instances."],
	] },
];

/** Section order within each area: dedicated and schema sections interleaved. */
const AREA_ORDER: Record<string, string[]> = {
	general: ["appearance", "interface", "experimental"],
	access: ["overview", "auth", "public-registry", "trace-privacy", "registered-only"],
	registry: ["review-policy", "registry", "discovery", "misc"],
	insights: ["provider", "models", "insights-advanced"],
	data: ["retention", "usage", "migration", "data", "danger"],
	security: ["security", "jwt"],
	deployment: ["deployment", "observability", "resource"],
};

// Highlight applied after a jump. Literal strings so Tailwind generates them.
const FLASH_ROW = ["!transition-none", "bg-tag-mcp/10", "shadow-[inset_0_0_0_1.5px_color-mix(in_oklch,var(--color-tag-mcp)_45%,transparent)]"];
const FLASH_CARD = [
	"!transition-none",
	"shadow-[0_0_0_2px_color-mix(in_oklch,var(--color-tag-mcp)_55%,transparent),0_0_0_6px_color-mix(in_oklch,var(--color-tag-mcp)_12%,transparent)]",
];
function flash(element: Element | null, classes: string[]) {
	if (!element) return;
	element.classList.add(...classes);
	window.setTimeout(() => element.classList.remove(...classes), 900);
}
const prefersReducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export default function SettingsPage() {
	const { ready } = useRoleGuard("super_admin");
	const queryClient = useQueryClient();
	const { data: settings, isLoading, isError, error, refetch } = useAdminSettings();
	const { data: settingsSchema = [] } = useAdminSettingsSchema();
	const { data: restartStatus, refetch: refetchRestartStatus } = useRestartStatus();
	const { data: systemWarnings } = useSystemWarnings();
	const { data: harnesses = [] } = useHarnesses();
	const {
		ssoEnabled,
		samlEnabled,
		publicRegistryEnabled,
		experimentalReviewChecks,
		leaderboardEnabled,
		brandingLogo,
		brandingAppName,
		brandingWordmark,
	} = useDeploymentConfig();
	const { openHelp } = useHelp();

	const [revokeConfirmKey, setRevokeConfirmKey] = useState<string | null>(null);
	const [purgingTracesInsights, setPurgingTracesInsights] = useState(false);
	const [showPurgeConfirm, setShowPurgeConfirm] = useState(false);
	const [tracePrivacy, setTracePrivacy] = useState(false);
	const [tracePrivacyLoading, setTracePrivacyLoading] = useState(true);
	const [tracePrivacyToggling, setTracePrivacyToggling] = useState(false);
	const [publicRegistryToggling, setPublicRegistryToggling] = useState(false);
	const [reviewChecksToggling, setReviewChecksToggling] = useState(false);
	const [leaderboardToggling, setLeaderboardToggling] = useState(false);
	const [showLeaderboardConfirm, setShowLeaderboardConfirm] = useState(false);
	const [registeredAgentsOnly, setRegisteredAgentsOnly] = useState(false);
	const [registeredAgentsOnlyLoading, setRegisteredAgentsOnlyLoading] = useState(true);
	const [registeredAgentsOnlyToggling, setRegisteredAgentsOnlyToggling] = useState(false);
	const [retentionEnabled, setRetentionEnabled] = useState(false);
	const [retentionDays, setRetentionDays] = useState<string>("");
	const [scoreRetentionDays, setScoreRetentionDays] = useState<string>("");
	const [maxTraceCount, setMaxTraceCount] = useState<string>("");
	const [retentionSaved, setRetentionSaved] = useState({ enabled: false, days: "", score: "", max: "" });
	const [retentionGlobal, setRetentionGlobal] = useState(90);
	const [retentionLoading, setRetentionLoading] = useState(true);
	const [retentionSaving, setRetentionSaving] = useState(false);
	const [showRetentionConfirm, setShowRetentionConfirm] = useState(false);
	const [retentionConfirmChecked, setRetentionConfirmChecked] = useState(false);
	const [retentionPreview, setRetentionPreview] = useState<Record<string, number | string> | null>(null);
	const retentionWasEnabled = useRef(false);
	const [logoOverride, setLogoOverride] = useState<string | null | undefined>(undefined);
	const [wordmarkOverride, setWordmarkOverride] = useState<string | null | undefined>(undefined);
	const [appNameOverride, setAppNameOverride] = useState<string | undefined>(undefined);
	const [brandingSaving, setBrandingSaving] = useState(false);
	const [migrateOpen, setMigrateOpen] = useState(false);
	const [dirtySections, setDirtySections] = useState<Set<string>>(() => new Set());
	const [query, setQuery] = useState("");
	const [activeArea, setActiveArea] = useState(AREAS[0].id);
	const [currentSection, setCurrentSection] = useState<string | null>(null);

	const handleRestarted = useCallback(() => {
		refetchRestartStatus();
		refetch();
	}, [refetch, refetchRestartStatus]);
	const { restarting, restartApi } = useRestartApi(handleRestarted);
	const fileInputRef = useRef<HTMLInputElement>(null);
	const wordmarkInputRef = useRef<HTMLInputElement>(null);
	const rootRef = useRef<HTMLDivElement>(null);
	const navRef = useRef<HTMLDivElement>(null);
	// True while a programmatic jump scrolls; tracking pauses so the nav doesn't flicker.
	const jumping = useRef(false);
	const searchingRef = useRef(false);

	const logoPreview = logoOverride !== undefined ? logoOverride : brandingLogo;
	const wordmarkPreview = wordmarkOverride !== undefined ? wordmarkOverride : brandingWordmark;
	const appNameDraft = appNameOverride !== undefined ? appNameOverride : brandingAppName || "";

	const applyRetention = useCallback(
		(res: { retention_enabled: boolean; data_retention_days?: number | null; score_retention_days?: number | null; max_trace_count?: number | null }) => {
			const saved = {
				enabled: res.retention_enabled,
				days: res.data_retention_days?.toString() || "",
				score: res.score_retention_days?.toString() || "",
				max: res.max_trace_count?.toString() || "",
			};
			setRetentionEnabled(saved.enabled);
			retentionWasEnabled.current = saved.enabled;
			setRetentionDays(saved.days);
			setScoreRetentionDays(saved.score);
			setMaxTraceCount(saved.max);
			setRetentionSaved(saved);
		},
		[],
	);

	useEffect(() => {
		admin
			.getTracePrivacy()
			.then((res) => setTracePrivacy(res.trace_privacy))
			.catch(() => {
				toast.error("Failed to load trace privacy setting");
			})
			.finally(() => setTracePrivacyLoading(false));
		admin
			.getRegisteredAgentsOnly()
			.then((res) => setRegisteredAgentsOnly(res.registered_agents_only))
			.catch(() => {
				toast.error("Failed to load registered-agents-only setting");
			})
			.finally(() => setRegisteredAgentsOnlyLoading(false));
		admin
			.getRetention()
			.then((res) => {
				applyRetention(res);
				setRetentionGlobal(res.global_retention_days);
			})
			.catch(() => {
				toast.error("Failed to load retention settings");
			})
			.finally(() => setRetentionLoading(false));
	}, [applyRetention]);

	const handlePurgeTracesInsights = useCallback(async () => {
		setShowPurgeConfirm(false);
		setPurgingTracesInsights(true);
		try {
			const res = await admin.purgeTracesAndInsights();
			queryClient.invalidateQueries();
			toast.success(`Purged telemetry and insights (${res.deleted_reports ?? 0} reports removed)`);
		} catch (e) {
			toast.error(e instanceof Error ? e.message : "Failed to purge traces and insights");
		} finally {
			setPurgingTracesInsights(false);
		}
	}, [queryClient]);

	const handleTracePrivacyToggle = useCallback(async (checked: boolean) => {
		setTracePrivacyToggling(true);
		try {
			const res = await admin.setTracePrivacy(checked);
			setTracePrivacy(res.trace_privacy);
			toast.success(`Trace privacy ${res.trace_privacy ? "enabled" : "disabled"}`);
		} catch (e) {
			toast.error(e instanceof Error ? e.message : "Failed to update trace privacy");
		} finally {
			setTracePrivacyToggling(false);
		}
	}, []);

	const handleReviewChecksToggle = useCallback(
		async (checked: boolean) => {
			setReviewChecksToggling(true);
			try {
				await admin.updateSetting("experimental.review_checks", { value: checked ? "true" : "false" });
				await queryClient.invalidateQueries({ queryKey: ["config", "public"] });
				await refetch();
				toast.success(`Review checks ${checked ? "enabled" : "disabled"}`);
			} catch (e) {
				toast.error(e instanceof Error ? e.message : "Failed to update review checks");
			} finally {
				setReviewChecksToggling(false);
			}
		},
		[queryClient, refetch],
	);

	const handlePublicRegistryToggle = useCallback(
		async (checked: boolean) => {
			setPublicRegistryToggling(true);
			try {
				await admin.updateSetting("deployment.public_registry_enabled", { value: checked ? "true" : "false" });
				await queryClient.invalidateQueries({ queryKey: ["config", "public"] });
				await refetch();
				toast.success(`Public registry access ${checked ? "enabled" : "disabled"}`);
			} catch (e) {
				toast.error(e instanceof Error ? e.message : "Failed to update public registry access");
			} finally {
				setPublicRegistryToggling(false);
			}
		},
		[queryClient, refetch],
	);

	const handleLeaderboardToggle = useCallback(
		async (checked: boolean) => {
			setLeaderboardToggling(true);
			try {
				await admin.updateSetting("deployment.leaderboard_enabled", { value: checked ? "true" : "false" });
				await queryClient.invalidateQueries({ queryKey: ["config", "public"] });
				await refetch();
				toast.success(`Leaderboard turned ${checked ? "on" : "off"}`);
			} catch (e) {
				toast.error(e instanceof Error ? e.message : "Failed to update leaderboard visibility");
			} finally {
				setLeaderboardToggling(false);
			}
		},
		[queryClient, refetch],
	);

	const handleRegisteredAgentsOnlyToggle = useCallback(async (checked: boolean) => {
		setRegisteredAgentsOnlyToggling(true);
		try {
			const res = await admin.setRegisteredAgentsOnly(checked);
			setRegisteredAgentsOnly(res.registered_agents_only);
			toast.success(`Registered Agent Files only ${res.registered_agents_only ? "enabled" : "disabled"}`);
		} catch (e) {
			toast.error(e instanceof Error ? e.message : "Failed to update setting");
		} finally {
			setRegisteredAgentsOnlyToggling(false);
		}
	}, []);

	const retentionErrors = useMemo(() => {
		const errors: { data_retention_days?: string; score_retention_days?: string; max_trace_count?: string; general?: string } = {};
		const days = retentionDays ? parseInt(retentionDays, 10) : null;
		const scoreDays = scoreRetentionDays ? parseInt(scoreRetentionDays, 10) : null;
		const maxCount = maxTraceCount ? parseInt(maxTraceCount, 10) : null;

		if (days !== null && !isNaN(days)) {
			if (days < 7) errors.data_retention_days = "Minimum 7 days";
			else if (retentionGlobal > 0 && days > retentionGlobal) errors.data_retention_days = `Cannot exceed global limit of ${retentionGlobal} days`;
		}
		if (scoreDays !== null && !isNaN(scoreDays)) {
			if (scoreDays < 7) errors.score_retention_days = "Minimum 7 days";
			else if (days && scoreDays < days) errors.score_retention_days = `Must be ≥ trace retention (${days} days)`;
		}
		if (maxCount !== null && !isNaN(maxCount)) {
			if (maxCount < 1000) errors.max_trace_count = "Minimum 1,000 traces";
		}
		if (retentionEnabled && !days && !maxCount) {
			errors.general = "Set at least one retention threshold to enable";
		}
		return errors;
	}, [retentionDays, scoreRetentionDays, maxTraceCount, retentionEnabled, retentionGlobal]);
	const hasRetentionErrors = Object.keys(retentionErrors).length > 0;
	const retentionDirtyCount = [
		retentionEnabled !== retentionSaved.enabled,
		retentionDays !== retentionSaved.days,
		scoreRetentionDays !== retentionSaved.score,
		maxTraceCount !== retentionSaved.max,
	].filter(Boolean).length;

	const saveRetention = useCallback(
		async (enabled: boolean, successMessage: string, errorMessage: string) => {
			setRetentionSaving(true);
			try {
				const res = await admin.setRetention({
					retention_enabled: enabled,
					data_retention_days: retentionDays ? parseInt(retentionDays, 10) : null,
					score_retention_days: scoreRetentionDays ? parseInt(scoreRetentionDays, 10) : null,
					max_trace_count: maxTraceCount ? parseInt(maxTraceCount, 10) : null,
				});
				applyRetention(res);
				queryClient.invalidateQueries({ queryKey: ["admin", "retention"] });
				toast.success(successMessage);
			} catch (e) {
				toast.error(e instanceof Error ? e.message : errorMessage);
			} finally {
				setRetentionSaving(false);
			}
		},
		[retentionDays, scoreRetentionDays, maxTraceCount, applyRetention, queryClient],
	);

	const handleRetentionSave = useCallback(async () => {
		const days = retentionDays ? parseInt(retentionDays, 10) : null;
		// Turning retention on deletes data, so it asks first with a deletion estimate.
		if (retentionEnabled && !retentionWasEnabled.current && days) {
			setShowRetentionConfirm(true);
			admin
				.previewRetention(days)
				.then(setRetentionPreview)
				.catch(() => setRetentionPreview(null));
			return;
		}
		await saveRetention(retentionEnabled, "Retention settings updated", "Failed to update retention");
	}, [retentionEnabled, retentionDays, saveRetention]);

	const handleRetentionConfirm = useCallback(async () => {
		setShowRetentionConfirm(false);
		setRetentionConfirmChecked(false);
		await saveRetention(true, "Data retention enabled", "Failed to enable retention");
		setRetentionPreview(null);
	}, [saveRetention]);

	const discardRetention = () => {
		setRetentionEnabled(retentionSaved.enabled);
		setRetentionDays(retentionSaved.days);
		setScoreRetentionDays(retentionSaved.score);
		setMaxTraceCount(retentionSaved.max);
	};

	const handleImageFile = useCallback((file: File, setter: (v: string) => void) => {
		if (!ALLOWED_LOGO_TYPES.includes(file.type)) {
			toast.error("Unsupported file type. Use PNG, SVG, ICO, JPEG, or WEBP.");
			return;
		}
		if (file.size > MAX_LOGO_SIZE) {
			toast.error(`File too large (${Math.round(file.size / 1024)}KB). Maximum: 2MB.`);
			return;
		}
		const reader = new FileReader();
		reader.onload = () => setter(reader.result as string);
		reader.readAsDataURL(file);
	}, []);

	const handleSaveBranding = useCallback(async () => {
		setBrandingSaving(true);
		try {
			if (logoPreview !== brandingLogo) {
				await admin.updateSetting("branding.logo", { value: logoPreview || "" });
			}
			if (wordmarkPreview !== brandingWordmark) {
				await admin.updateSetting("branding.wordmark", { value: wordmarkPreview || "" });
			}
			const trimmedName = appNameDraft.trim();
			if (trimmedName !== (brandingAppName || "")) {
				await admin.updateSetting("branding.app_name", { value: trimmedName });
			}
			setLogoOverride(undefined);
			setWordmarkOverride(undefined);
			setAppNameOverride(undefined);
			queryClient.invalidateQueries({ queryKey: ["config", "public"] });
			toast.success("Appearance updated");
		} catch (e) {
			toast.error(e instanceof Error ? e.message : "Failed to save appearance");
		} finally {
			setBrandingSaving(false);
		}
	}, [logoPreview, brandingLogo, wordmarkPreview, brandingWordmark, appNameDraft, brandingAppName, queryClient]);

	const handleResetBranding = useCallback(async () => {
		setBrandingSaving(true);
		try {
			await admin.updateSetting("branding.logo", { value: "" });
			await admin.updateSetting("branding.wordmark", { value: "" });
			await admin.updateSetting("branding.app_name", { value: "" });
			setLogoOverride(undefined);
			setWordmarkOverride(undefined);
			setAppNameOverride(undefined);
			queryClient.invalidateQueries({ queryKey: ["config", "public"] });
			toast.success("Appearance reset to defaults");
		} catch (e) {
			toast.error(e instanceof Error ? e.message : "Failed to reset appearance");
		} finally {
			setBrandingSaving(false);
		}
	}, [queryClient]);

	const brandingDirtyCount = [
		logoPreview !== brandingLogo,
		wordmarkPreview !== brandingWordmark,
		appNameDraft.trim() !== (brandingAppName || ""),
	].filter(Boolean).length;

	const entries: AdminSetting[] = useMemo(
		() =>
			(Array.isArray(settings)
				? settings.map((s: AdminSetting) => ({ key: s.key, value: s.value, is_sensitive: s.is_sensitive, is_set: s.is_set }))
				: Object.entries(settings ?? {}).map(([k, v]) => ({ key: k, value: String(v) }))
			).filter((e) => !e.key.startsWith("branding.")),
		[settings],
	);

	const onSchemaSaved = useCallback(() => {
		refetch();
		refetchRestartStatus();
	}, [refetch, refetchRestartStatus]);

	const onDirtyChange = useCallback((sectionId: string, dirty: boolean) => {
		setDirtySections((current) => {
			if (current.has(sectionId) === dirty) return current;
			const next = new Set(current);
			if (dirty) next.add(sectionId);
			else next.delete(sectionId);
			return next;
		});
	}, []);

	// ── Schema sections, placed into areas ──────────────────────────────
	const schemaSections = useMemo(() => {
		const placed: Record<string, { section: AdminSettingSection; settings: AdminSettingDef[]; advanced: boolean }> = {};
		for (const section of settingsSchema) {
			if (EXCLUDED_SECTIONS.has(section.id)) continue;
			const visible = section.settings.filter((setting) => !DEDICATED_KEYS.has(setting.key));
			if (visible.length === 0) continue;
			placed[section.id] = { section, settings: visible, advanced: !!SCHEMA_LAYOUT[section.id]?.advanced };
		}
		return placed;
	}, [settingsSchema]);

	const areaOrder = useMemo(() => {
		const order: Record<string, string[]> = Object.fromEntries(Object.entries(AREA_ORDER).map(([area, ids]) => [area, [...ids]]));
		for (const id of Object.keys(schemaSections)) {
			if (!SCHEMA_LAYOUT[id]) order.deployment.push(id);
		}
		return order;
	}, [schemaSections]);

	const dedicatedById = useMemo(() => new Map(DEDICATED_SECTIONS.map((section) => [section.id, section])), []);

	// Sections that exist right now (schema sections only once loaded).
	const sectionsByArea = useMemo(() => {
		const result: Record<string, NavSection[]> = {};
		for (const area of AREAS) {
			result[area.id] = areaOrder[area.id].flatMap((id): NavSection[] => {
				const dedicated = dedicatedById.get(id);
				if (dedicated) {
					return [{ id, title: dedicated.title, description: dedicated.description, count: dedicated.rows.length, secondary: id === "insights-advanced" }];
				}
				const schema = schemaSections[id];
				if (!schema) return [];
				return [{
					id,
					title: schema.section.title,
					description: schema.section.description,
					count: schema.settings.length,
					secondary: schema.advanced || id === "danger",
					danger: id === "danger",
				}];
			});
		}
		return result;
	}, [areaOrder, dedicatedById, schemaSections]);

	const navAreas: NavArea[] = AREAS.map((area) => ({
		...area,
		sections: sectionsByArea[area.id],
		attention: area.id === "security" && !!systemWarnings?.length,
		dirty:
			sectionsByArea[area.id].some((section) => dirtySections.has(section.id)) ||
			(area.id === "general" && brandingDirtyCount > 0) ||
			(area.id === "data" && retentionDirtyCount > 0) ||
			(area.id === "insights" && dirtySections.has("insights")),
	}));
	const anyDirty = navAreas.some((area) => area.dirty);

	// Drafts live only in this page, so leaving it (a sidebar link, back, a tab
	// close or reload) would drop them silently. The router asks first.
	const leaveBlocker = useBlocker({
		shouldBlockFn: () => anyDirty,
		enableBeforeUnload: () => anyDirty,
		withResolver: true,
	});

	// ── Search ──────────────────────────────────────────────────────────
	const searchIndex = useMemo(() => {
		const index: SearchEntry[] = [];
		for (const section of DEDICATED_SECTIONS) {
			for (const [key, label, description] of section.rows) {
				if (key === "branding.preview" || key.startsWith("status.")) continue;
				index.push({ key, label, description, area: section.area, section: section.id, sectionTitle: section.title });
			}
		}
		for (const [id, { section, settings: defs }] of Object.entries(schemaSections)) {
			const area = SCHEMA_LAYOUT[id]?.area ?? "deployment";
			for (const def of defs) {
				index.push({ key: def.key, label: def.label, description: settingDescription(def), area, section: id, sectionTitle: section.title, showKey: true });
			}
		}
		return index;
	}, [schemaSections]);

	const term = query.trim().toLowerCase();
	const hits = useMemo(() => {
		if (!term) return null;
		const words = term.split(/\s+/);
		const areaLabel = (id: string) => AREAS.find((area) => area.id === id)?.label ?? "";
		return searchIndex
			.filter((entry) => {
				const hay = `${entry.label} ${entry.description} ${entry.key} ${entry.sectionTitle} ${areaLabel(entry.area)}`.toLowerCase();
				return words.every((word) => hay.includes(word));
			})
			.sort((a, b) => Number(b.label.toLowerCase().includes(term)) - Number(a.label.toLowerCase().includes(term)));
	}, [term, searchIndex]);
	const hitCounts = useMemo(() => {
		if (!hits) return null;
		const counts: Record<string, number> = {};
		for (const hit of hits) counts[hit.area] = (counts[hit.area] ?? 0) + 1;
		return counts;
	}, [hits]);

	// ── Scrolling: sticky offset, jumps, and the active area ────────────
	const scroller = () => rootRef.current?.closest("main") ?? null;

	useLayoutEffect(() => {
		const root = rootRef.current;
		const nav = navRef.current;
		if (!root || !nav) return;
		const header = root.previousElementSibling as HTMLElement | null;
		const update = () => root.style.setProperty("--settings-stick", `${(header?.offsetHeight ?? 54) + nav.offsetHeight + 12}px`);
		update();
		const observer = new ResizeObserver(update);
		observer.observe(nav);
		if (header) observer.observe(header);
		return () => observer.disconnect();
	}, [ready]);

	// The URL stays /settings; the active area is page state only.
	const markArea = useCallback((id: string) => setActiveArea(id), []);

	const measure = useCallback(() => {
		const root = rootRef.current;
		const container = scroller();
		// Search results hide the areas, so their positions mean nothing then.
		if (!root || !container || jumping.current || searchingRef.current) return;
		const stick = parseFloat(root.style.getPropertyValue("--settings-stick")) || 130;
		const line = container.getBoundingClientRect().top + stick + 24;
		let area = AREAS[0].id;
		root.querySelectorAll<HTMLElement>("[data-settings-area]").forEach((element) => {
			if (element.getBoundingClientRect().top <= line) area = element.dataset.settingsArea ?? area;
		});
		if (container.scrollTop + container.clientHeight >= container.scrollHeight - 4) area = AREAS[AREAS.length - 1].id;
		let section: string | null = null;
		root.querySelectorAll<HTMLElement>(`#${areaDomId(area)} [data-settings-section]`).forEach((element) => {
			if (section === null || element.getBoundingClientRect().top <= line) section = element.dataset.settingsSection ?? section;
		});
		setCurrentSection(section);
		setActiveArea(area);
	}, []);

	// Measure at most once per frame while scrolling.
	const frame = useRef(0);
	const spy = useCallback(() => {
		cancelAnimationFrame(frame.current);
		frame.current = requestAnimationFrame(measure);
	}, [measure]);

	useEffect(() => {
		const container = scroller();
		if (!container || !ready) return;
		container.addEventListener("scroll", spy, { passive: true });
		return () => {
			container.removeEventListener("scroll", spy);
			cancelAnimationFrame(frame.current);
		};
	}, [spy, ready]);

	// Search results replace the areas, which collapses the page and resets the scroll
	// position. It is recorded before the first keystroke renders (in updateQuery) and
	// restored when search closes; a jump to a result runs next frame and scrolls from there.
	const searching = hits !== null;
	const savedScroll = useRef<number | null>(null);
	const updateQuery = (value: string) => {
		if (!query.trim() && value.trim() && savedScroll.current === null) savedScroll.current = scroller()?.scrollTop ?? null;
		setQuery(value);
	};
	useLayoutEffect(() => {
		searchingRef.current = searching;
		if (searching) return;
		const container = scroller();
		if (container && savedScroll.current !== null) container.scrollTop = savedScroll.current;
		savedScroll.current = null;
		spy();
	}, [searching, spy]);

	const scrollTo = (element: Element | null, block: ScrollLogicalPosition, smooth = true) => {
		const container = scroller();
		if (!element || !container) return;
		jumping.current = true;
		let done = false;
		const finish = () => {
			if (done) return;
			done = true;
			container.removeEventListener("scrollend", finish);
			jumping.current = false;
			spy();
		};
		// scrollend marks the true end of a smooth scroll; the timeout covers browsers without it
		// and jumps that don't move at all.
		container.addEventListener("scrollend", finish);
		window.setTimeout(finish, 1500);
		element.scrollIntoView({ block, behavior: smooth && !prefersReducedMotion() ? "smooth" : "auto" });
	};

	const goToArea = (areaId: string, smooth = true) => {
		setQuery("");
		markArea(areaId);
		requestAnimationFrame(() => {
			const element = document.getElementById(areaDomId(areaId));
			scrollTo(element, "start", smooth);
			element?.focus({ preventScroll: true });
		});
	};

	const goToSection = (areaId: string, sectionId: string) => {
		setQuery("");
		markArea(areaId);
		requestAnimationFrame(() => {
			const element = document.getElementById(sectionDomId(sectionId));
			if (!element) return;
			if (element instanceof HTMLDetailsElement) element.open = true;
			scrollTo(element, "start");
			flash(element instanceof HTMLDetailsElement ? element : element.querySelector("[data-settings-card]"), FLASH_CARD);
			const target = element.querySelector<HTMLElement>("summary") ?? element.querySelector<HTMLElement>("h3");
			target?.focus({ preventScroll: true });
		});
	};

	const goToSetting = (entry: SearchEntry) => {
		setQuery("");
		markArea(entry.area);
		requestAnimationFrame(() => {
			const row = document.getElementById(rowDomId(entry.key));
			const target = row ?? document.getElementById(sectionDomId(entry.section));
			if (!target) return;
			const details = target.closest("details");
			if (details) details.open = true;
			scrollTo(target, row ? "center" : "start");
			flash(row ?? target.querySelector("[data-settings-card]"), row ? FLASH_ROW : FLASH_CARD);
			(row?.querySelector<HTMLElement>("input, button, [role=switch], [role=combobox]") ?? target).focus({ preventScroll: true });
		});
	};

	const onSearchKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
		if (event.key === "Escape") setQuery("");
		if (event.key === "Enter" && hits?.length) {
			event.preventDefault();
			goToSetting(hits[0]);
		}
		if (event.key === "ArrowDown" && hits?.length) {
			event.preventDefault();
			document.querySelector<HTMLButtonElement>("[data-settings-result]")?.focus();
		}
	};

	if (!ready) return null;

	// ── Rendering helpers ───────────────────────────────────────────────
	const schemaSection = (id: string) => {
		const placed = schemaSections[id];
		if (!placed) return null;
		return (
			<SchemaSection
				key={id}
				section={placed.section}
				settings={placed.settings}
				entries={entries}
				harnesses={harnesses}
				advanced={placed.advanced}
				docsTitle={placed.section.title}
				onSaved={onSchemaSaved}
				onRevoke={setRevokeConfirmKey}
				onPurge={() => setShowPurgeConfirm(true)}
				purging={purgingTracesInsights}
				onDirtyChange={onDirtyChange}
			/>
		);
	};

	const switchRow = (
		key: string,
		label: string,
		description: ReactNode,
		checked: boolean,
		onChange: (checked: boolean) => void,
		disabled: boolean,
		ariaLabel = label,
	) => (
		<SettingRow settingKey={key} label={label} htmlFor={`${rowDomId(key)}-control`} layout="inline" description={description}>
			<SwitchStateLabel checked={checked} />
			<Switch id={`${rowDomId(key)}-control`} checked={checked} onCheckedChange={onChange} disabled={disabled} aria-label={ariaLabel} />
		</SettingRow>
	);

	const uploadRow = (
		key: string,
		label: string,
		labelNote: string | undefined,
		description: string | undefined,
		preview: string | null | undefined,
		inputRef: React.RefObject<HTMLInputElement | null>,
		setter: (value: string | null) => void,
		wide: boolean,
	) => (
		<SettingRow settingKey={key} label={label} labelNote={labelNote} description={description}>
			<button
				type="button"
				aria-label={`Upload ${label.toLowerCase()}`}
				onClick={() => inputRef.current?.click()}
				onDragOver={(event) => event.preventDefault()}
				onDrop={(event) => {
					event.preventDefault();
					const file = event.dataTransfer.files[0];
					if (file) handleImageFile(file, setter);
				}}
				className={cn(
					"grid h-11 shrink-0 place-items-center rounded-lg border-[1.5px] border-dashed border-border bg-muted/40 text-muted-foreground transition-colors hover:border-foreground/35 hover:text-foreground",
					wide ? "w-28" : "w-11",
				)}
			>
				{preview ? <img src={preview} alt="" className={wide ? "h-6 max-w-24 object-contain" : "size-7 object-contain"} /> : <Upload className="size-3.5" aria-hidden="true" />}
			</button>
			<input
				ref={inputRef}
				type="file"
				accept="image/png,image/svg+xml,image/x-icon,image/jpeg,image/webp"
				className="hidden"
				onChange={(event) => {
					const file = event.target.files?.[0];
					if (file) handleImageFile(file, setter);
					event.target.value = "";
				}}
			/>
			<Button type="button" variant="outline" size="sm" onClick={() => inputRef.current?.click()}>
				Upload
			</Button>
			{preview && (
				<Button type="button" variant="ghost" size="sm" onClick={() => setter(null)}>
					Remove
				</Button>
			)}
		</SettingRow>
	);

	const numberRow = (key: string, label: string, value: string, onChange: (value: string) => void, placeholder: string, min: number, errorText?: string) => (
		<SettingRow settingKey={key} label={label} htmlFor={`${rowDomId(key)}-control`} error={errorText}>
			<Input
				id={`${rowDomId(key)}-control`}
				type="number"
				min={min}
				value={value}
				onChange={(event) => onChange(event.target.value)}
				placeholder={placeholder}
				aria-invalid={!!errorText}
				className={cn("min-h-9 flex-1 py-2 tabular-nums", errorText && "border-destructive/60")}
			/>
		</SettingRow>
	);

	const sections: Record<string, ReactNode> = {
		appearance: (
			<SettingsGroup key="appearance" id="appearance" title="Appearance" docsTitle="Appearance">
				<SettingsCard
					footer={
						<CardFooter
							persistent
							dirtyCount={brandingDirtyCount}
							saving={brandingSaving}
							onSave={() => void handleSaveBranding()}
							onDiscard={() => {
								setLogoOverride(undefined);
								setWordmarkOverride(undefined);
								setAppNameOverride(undefined);
							}}
							extra={
								(brandingLogo || brandingAppName || brandingWordmark) && (
									<Button type="button" variant="ghost" size="sm" onClick={() => void handleResetBranding()} disabled={brandingSaving}>
										<RotateCcw aria-hidden="true" />
										Reset
									</Button>
								)
							}
						/>
					}
				>
					{uploadRow("branding.logo", "Icon", undefined, "PNG, SVG, ICO, JPEG, or WEBP. Max 2MB. Transparent images recommended for theme compatibility.", logoPreview, fileInputRef, setLogoOverride, false)}
					{uploadRow("branding.wordmark", "Wordmark", "(optional, replaces text)", undefined, wordmarkPreview, wordmarkInputRef, setWordmarkOverride, true)}
					<SettingRow settingKey="branding.app_name" label="App Name" labelNote="(used when no wordmark)" htmlFor={`${rowDomId("branding.app_name")}-control`}>
						<div className="flex flex-1 flex-col gap-1">
							<Input
								id={`${rowDomId("branding.app_name")}-control`}
								value={appNameDraft}
								onChange={(event) => setAppNameOverride(event.target.value)}
								placeholder="Observal"
								autoComplete="off"
								maxLength={30}
								className="min-h-9 py-2"
							/>
							<span className="text-right text-3xs tabular-nums text-muted-foreground">{appNameDraft.length}/30</span>
						</div>
					</SettingRow>
					<SettingRow settingKey="branding.preview" label="Preview">
						<div className="inline-flex h-12 items-center gap-[11px] rounded-[9px] border border-border bg-sidebar pr-4 pl-3" aria-live="polite">
							<img src={logoPreview || "/observal-logo.svg"} alt="" width={22} height={22} className="object-contain" />
							{wordmarkPreview ? (
								<img src={wordmarkPreview} alt="" width={140} height={16} className="h-4 max-w-35 object-contain object-left" />
							) : (
								<span className="max-w-48 truncate text-base font-semibold tracking-tight text-sidebar-foreground">{appNameDraft.trim() || "Observal"}</span>
							)}
						</div>
					</SettingRow>
				</SettingsCard>
			</SettingsGroup>
		),
		interface: (
			<SettingsGroup key="interface" id="interface" title="Interface">
				<SettingsCard>
					{switchRow(
						"deployment.leaderboard_enabled",
						"Show the Leaderboard",
						`Off by default. When off, the Leaderboard tab, its home page link, the page, and its API are unavailable to everyone, including admins. Ranking data is kept. ${LEADERBOARD_DEPRECATION_NOTICE}`,
						leaderboardEnabled,
						(checked) => {
							if (checked) setShowLeaderboardConfirm(true);
							else void handleLeaderboardToggle(false);
						},
						leaderboardToggling,
					)}
				</SettingsCard>
			</SettingsGroup>
		),
		experimental: (
			<SettingsGroup key="experimental" id="experimental" title="Experimental" description="Features that are not ready for general use. Off by default, and hidden everywhere while off.">
				<SettingsCard>
					{switchRow(
						"experimental.review_checks",
						"Review checks",
						"Automated checks on review revisions, with per-organization and per-teamspace settings. Behavior and APIs may change or be removed.",
						experimentalReviewChecks,
						(checked) => void handleReviewChecksToggle(checked),
						reviewChecksToggling,
						"Enable experimental review checks",
					)}
				</SettingsCard>
			</SettingsGroup>
		),
		overview: (
			<SettingsGroup key="overview" id="overview" title="System Overview">
				<SettingsCard>
					<SettingRow settingKey="status.sso" label="SSO (OAuth/OIDC)" layout="inline">
						<span className={cn("text-xs font-medium", ssoEnabled ? "text-success" : "text-muted-foreground")}>{ssoEnabled ? "Enabled" : "Disabled"}</span>
					</SettingRow>
					<SettingRow settingKey="status.saml" label="SAML SSO" layout="inline">
						<span className={cn("text-xs font-medium", samlEnabled ? "text-success" : "text-muted-foreground")}>{samlEnabled ? "Configured" : "Not configured"}</span>
					</SettingRow>
				</SettingsCard>
			</SettingsGroup>
		),
		"public-registry": (
			<SettingsGroup key="public-registry" id="public-registry" title="Public Registry">
				<SettingsCard>
					{switchRow(
						"deployment.public_registry_enabled",
						"Allow public browsing and installs",
						"Signed-out visitors can browse and install approved public Agent Files and components. Publishing, private content, telemetry, and administration still require sign-in.",
						publicRegistryEnabled,
						(checked) => void handlePublicRegistryToggle(checked),
						publicRegistryToggling,
						"Allow public registry browsing and installs",
					)}
				</SettingsCard>
			</SettingsGroup>
		),
		"trace-privacy": (
			<SettingsGroup key="trace-privacy" id="trace-privacy" title="Trace Privacy" docsTitle="Trace Privacy">
				<SettingsCard>
					{switchRow(
						"security.trace_privacy",
						"Restrict trace visibility",
						"When enabled, all users (including admins) can only see their own traces. Super-admins always retain full visibility across all traces.",
						tracePrivacy,
						(checked) => void handleTracePrivacyToggle(checked),
						tracePrivacyLoading || tracePrivacyToggling,
					)}
				</SettingsCard>
			</SettingsGroup>
		),
		"registered-only": (
			<SettingsGroup key="registered-only" id="registered-only" title="Registered Agent Files Only" docsTitle="Registered Agent Files Only">
				<SettingsCard>
					{switchRow(
						"registry.registered_agents_only",
						"Only trace registered Agent Files",
						"Unstable. When enabled, only registered Agent Files are traced. Unregistered Agent File activity may be missing from traces.",
						registeredAgentsOnly,
						(checked) => void handleRegisteredAgentsOnlyToggle(checked),
						registeredAgentsOnlyLoading || registeredAgentsOnlyToggling,
					)}
				</SettingsCard>
			</SettingsGroup>
		),
		"review-policy": (
			<SettingsGroup key="review-policy" id="review-policy" title="Review policy">
				<SettingsCard>
					<SettingRow settingKey="review-policy" label="Review policy" description="Approval counts and publication rules" layout="inline">
						<Button asChild variant="outline" size="sm">
							<Link to="/review-policy">
								<ShieldCheck aria-hidden="true" />
								Review policy
								<ArrowRight aria-hidden="true" />
							</Link>
						</Button>
					</SettingRow>
				</SettingsCard>
			</SettingsGroup>
		),
		insights: (
			<InsightsSection
				key="insights"
				entries={entries}
				onSave={async (key, value) => {
					await admin.updateSetting(key, { value });
					refetch();
				}}
				onRevoke={(key) => setRevokeConfirmKey(key)}
				refetch={refetch}
				onDirtyChange={onDirtyChange}
			/>
		),
		retention: (
			<SettingsGroup key="retention" id="retention" title="Data Retention" docsTitle="Data Retention">
				<SettingsCard
					footer={
						<CardFooter
							persistent
							dirtyCount={retentionDirtyCount}
							invalid={hasRetentionErrors}
							saving={retentionSaving}
							onSave={() => void handleRetentionSave()}
							onDiscard={discardRetention}
						/>
					}
				>
					<SettingRow
						settingKey="retention.enabled"
						label="Enable data retention"
						htmlFor={`${rowDomId("retention.enabled")}-control`}
						layout="inline"
						description={`Automatically purge telemetry data older than the configured period. Global ceiling: ${retentionGlobal > 0 ? `${retentionGlobal} days` : "disabled"}.`}
						error={retentionErrors.general}
					>
						<SwitchStateLabel checked={retentionEnabled} />
						<Switch
							id={`${rowDomId("retention.enabled")}-control`}
							checked={retentionEnabled}
							onCheckedChange={setRetentionEnabled}
							disabled={retentionLoading}
							aria-label="Enable data retention"
						/>
					</SettingRow>
					{retentionEnabled && (
						<>
							{numberRow("retention.trace_days", "Trace retention (days)", retentionDays, setRetentionDays, "e.g. 30", 7, retentionErrors.data_retention_days)}
							{numberRow("retention.score_days", "Score & insight retention (days)", scoreRetentionDays, setScoreRetentionDays, "e.g. 30 (default: 2x trace retention)", 7, retentionErrors.score_retention_days)}
							{numberRow("retention.max_trace_count", "Max trace count (optional)", maxTraceCount, setMaxTraceCount, "e.g. 100000", 1000, retentionErrors.max_trace_count)}
						</>
					)}
				</SettingsCard>
			</SettingsGroup>
		),
		usage: <UsagePingSection key="usage" settings={entries} onChanged={() => void refetch()} onDirtyChange={onDirtyChange} />,
		migration: (
			<SettingsGroup key="migration" id="migration" title="Data Migration" docsTitle="Data Migration">
				<SettingsCard>
					<SettingRow
						settingKey="migration"
						label="Export, import, and validate instance data"
						description="Move registry data and telemetry between Observal instances. Start with the guide if this is your first run."
						layout="inline"
					>
						<div className="flex flex-wrap justify-end gap-2">
							<Button type="button" variant="outline" size="sm" onClick={() => openHelp({ pageKey: "migration" })}>
								<HelpCircle aria-hidden="true" />
								Guide
							</Button>
							<Button type="button" size="sm" onClick={() => setMigrateOpen(true)}>
								<ArrowLeftRight aria-hidden="true" />
								Migrate
							</Button>
						</div>
					</SettingRow>
				</SettingsCard>
				<MigrateDialog open={migrateOpen} onOpenChange={setMigrateOpen} />
			</SettingsGroup>
		),
	};

	const areaContent = (areaId: string) => (
		<>
			{areaId === "security" &&
				systemWarnings?.map((warning: SystemWarning) => (
					<div
						key={warning.code}
						role="note"
						className={cn(
							"mb-7 flex items-start gap-3 rounded-lg border px-4 py-3",
							warning.level === "critical" ? "border-destructive/40 bg-destructive/10" : "border-warning/40 bg-warning/10",
						)}
					>
						{warning.level === "critical" ? (
							<ShieldAlert className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden="true" />
						) : (
							<AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden="true" />
						)}
						<div>
							<p className={cn("text-sm font-medium", warning.level === "critical" ? "text-destructive" : "text-warning")}>
								{warning.level === "critical" ? "Critical" : "Warning"}
							</p>
							<p className="mt-0.5 text-xs text-foreground/70">{warning.message}</p>
						</div>
					</div>
				))}
			{areaId === "insights"
				? sections.insights
				: areaOrder[areaId].map((id) => sections[id] ?? schemaSection(id))}
		</>
	);

	return (
		<>
			<PageHeader title="Settings" breadcrumbs={[{ label: "Dashboard", href: "/dashboard" }, { label: "Settings" }]} />
			<div ref={rootRef} className="w-full px-4 pb-16 sm:px-[30px]">
				<SettingsNav
					ref={navRef}
					areas={navAreas}
					activeArea={activeArea}
					currentSection={currentSection}
					query={query}
					onQueryChange={updateQuery}
					onQueryKeyDown={onSearchKeyDown}
					hitCounts={hitCounts}
					onAreaSelect={(areaId) => goToArea(areaId)}
					onSectionSelect={goToSection}
				/>

				{restartStatus?.required && (
					<div role="status" className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-warning/40 bg-warning/10 px-4 py-3">
						<Power className="size-4 shrink-0 text-warning" aria-hidden="true" />
						<div className="min-w-0 flex-1">
							<p className="text-sm font-medium">Saved, API restart required</p>
							{restartStatus.keys.length > 0 && <p className="mt-0.5 break-words text-xs text-foreground/70">{restartStatus.keys.join(", ")}</p>}
						</div>
						<Button size="sm" variant="outline" onClick={restartApi} disabled={restarting}>
							{restarting ? <Loader2 className="animate-spin" aria-hidden="true" /> : <Power aria-hidden="true" />}
							{restarting ? "Restarting" : "Restart API"}
						</Button>
					</div>
				)}

				{hits && (
					<section aria-live="polite" aria-labelledby="settings-results-title">
						<div className="mb-3 flex items-baseline justify-between gap-4">
							<h2 id="settings-results-title" className="text-base">
								{hits.length ? `${hits.length} ${hits.length === 1 ? "setting" : "settings"} match “${query.trim()}”` : "No matching settings"}
							</h2>
							<span className="hidden text-xs text-muted-foreground sm:inline">Enter opens the first result · Esc clears</span>
						</div>
						<div className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card shadow-sm">
							{hits.length ? (
								hits.map((hit) => (
									<button
										key={`${hit.section}-${hit.key}`}
										type="button"
										data-settings-result
										onClick={() => goToSetting(hit)}
										onKeyDown={(event) => {
											const target = event.currentTarget;
											if (event.key === "ArrowDown") {
												event.preventDefault();
												(target.nextElementSibling as HTMLElement | null)?.focus();
											}
											if (event.key === "ArrowUp") {
												event.preventDefault();
												((target.previousElementSibling as HTMLElement | null) ?? document.querySelector<HTMLInputElement>("input[name=settings-filter]"))?.focus();
											}
											if (event.key === "Escape") setQuery("");
										}}
										className="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-4 px-4 py-3 text-left transition-colors hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:outline-none sm:px-[1.125rem]"
									>
										<span className="min-w-0">
											<span className="flex items-center gap-1 text-2xs text-muted-foreground">
												{AREAS.find((area) => area.id === hit.area)?.label}
												<ChevronRight className="size-3" aria-hidden="true" />
												{hit.sectionTitle}
											</span>
											<strong className="block text-sm font-medium">{hit.label}</strong>
											{hit.showKey && <span className="block truncate font-mono text-[11px] text-muted-foreground/85">{hit.key}</span>}
											{hit.description && <span className="block truncate text-xs text-muted-foreground">{hit.description}</span>}
										</span>
										<ArrowRight className="size-4 text-muted-foreground" aria-hidden="true" />
									</button>
								))
							) : (
								<div className="px-6 py-10 text-center">
									<strong className="block font-medium">Nothing here by that name</strong>
									<p className="mt-1 text-xs text-muted-foreground">
										Try a broader word such as “token”, “retention” or “harness”, or search by setting key like <code>jwt.</code>
									</p>
								</div>
							)}
						</div>
					</section>
				)}

				<div hidden={!!hits}>
					{isLoading ? (
						<TableSkeleton rows={5} cols={2} />
					) : isError ? (
						<ErrorState message={error?.message} onRetry={() => refetch()} />
					) : (
						AREAS.map((area) => (
							<SettingsArea key={area.id} id={area.id} label={area.label} sectionCount={sectionsByArea[area.id].length}>
								{areaContent(area.id)}
							</SettingsArea>
						))
					)}
				</div>
			</div>

			<Dialog
				open={leaveBlocker.status === "blocked"}
				onOpenChange={(open) => {
					if (!open && leaveBlocker.status === "blocked") leaveBlocker.reset();
				}}
			>
				<DialogContent className="max-w-md">
					<DialogHeader>
						<DialogTitle className="flex items-center gap-2 text-sm">
							<AlertTriangle className="h-4 w-4 text-warning" />
							Leave with unsaved changes?
						</DialogTitle>
						<DialogDescription className="text-xs">
							Changes you haven't saved on this page will be lost.
						</DialogDescription>
					</DialogHeader>
					<DialogFooter>
						<Button size="sm" variant="outline" onClick={() => leaveBlocker.status === "blocked" && leaveBlocker.reset()}>
							Keep editing
						</Button>
						<Button size="sm" variant="destructive" onClick={() => leaveBlocker.status === "blocked" && leaveBlocker.proceed()}>
							Discard and leave
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>

			<Dialog open={showLeaderboardConfirm} onOpenChange={setShowLeaderboardConfirm}>
				<DialogContent className="max-w-md">
					<DialogHeader>
						<DialogTitle className="flex items-center gap-2 text-sm">
							<AlertTriangle className="h-4 w-4 text-warning" />
							Turn on the Leaderboard?
						</DialogTitle>
						<DialogDescription className="text-xs">
							{LEADERBOARD_DEPRECATION_NOTICE} Everyone will see the Leaderboard tab and page until you turn it off again.
						</DialogDescription>
					</DialogHeader>
					<DialogFooter>
						<Button size="sm" variant="outline" onClick={() => setShowLeaderboardConfirm(false)}>
							Cancel
						</Button>
						<Button
							size="sm"
							onClick={() => {
								setShowLeaderboardConfirm(false);
								void handleLeaderboardToggle(true);
							}}
						>
							Turn on
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>

			<Dialog
				open={showRetentionConfirm}
				onOpenChange={(open) => {
					if (!open) {
						setShowRetentionConfirm(false);
						setRetentionPreview(null);
					}
				}}
			>
				<DialogContent className="max-w-md">
					<DialogHeader>
						<DialogTitle className="flex items-center gap-2 text-sm">
							<AlertTriangle className="h-4 w-4 text-warning" />
							Enable Data Retention?
						</DialogTitle>
						<DialogDescription className="text-xs">
							This will permanently delete telemetry data older than {retentionDays} days. Purges run automatically every 6 hours. This action cannot be undone.
						</DialogDescription>
					</DialogHeader>
					{retentionPreview && (
						<div className="space-y-1 rounded bg-muted/50 p-3 text-xs">
							<p className="font-medium text-muted-foreground">Estimated deletions:</p>
							{Object.entries(retentionPreview)
								.filter(([k]) => !k.startsWith("_"))
								.map(([k, v]) => (
									<p key={k}>
										{k}: {typeof v === "number" ? v.toLocaleString() : v} rows
									</p>
								))}
						</div>
					)}
					<label className="flex cursor-pointer items-center gap-2 text-xs">
						<Checkbox checked={retentionConfirmChecked} onCheckedChange={(checked) => setRetentionConfirmChecked(checked === true)} />
						I understand this will permanently delete data
					</label>
					<DialogFooter>
						<Button
							size="sm"
							variant="outline"
							onClick={() => {
								setShowRetentionConfirm(false);
								setRetentionPreview(null);
							}}
						>
							Cancel
						</Button>
						<Button size="sm" variant="destructive" onClick={handleRetentionConfirm} disabled={!retentionConfirmChecked}>
							Enable Retention
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>

			<Dialog open={showPurgeConfirm} onOpenChange={setShowPurgeConfirm}>
				<DialogContent className="max-w-md">
					<DialogHeader>
						<DialogTitle className="flex items-center gap-2 text-sm">
							<Trash2 className="h-4 w-4 text-destructive" />
							Purge traces &amp; insights?
						</DialogTitle>
						<DialogDescription className="text-xs">
							Permanently delete all traces/session telemetry and insight reports for this deployment? This cannot be undone.
						</DialogDescription>
					</DialogHeader>
					<DialogFooter>
						<Button size="sm" variant="outline" onClick={() => setShowPurgeConfirm(false)}>
							Cancel
						</Button>
						<Button size="sm" variant="destructive" onClick={() => void handlePurgeTracesInsights()} data-setting-confirm={PURGE_KEY}>
							Purge traces &amp; insights
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>

			<Dialog open={revokeConfirmKey !== null} onOpenChange={(open) => { if (!open) setRevokeConfirmKey(null); }}>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>Revoke secret</DialogTitle>
						<DialogDescription>
							This will permanently delete the stored value for <strong>{revokeConfirmKey}</strong>. Any features depending on this credential will stop working immediately. This cannot be undone.
						</DialogDescription>
					</DialogHeader>
					<DialogFooter>
						<Button variant="outline" onClick={() => setRevokeConfirmKey(null)}>
							Cancel
						</Button>
						<Button
							variant="destructive"
							onClick={async () => {
								try {
									await admin.revokeSetting(revokeConfirmKey!);
									refetch();
									refetchRestartStatus();
									toast.success(`Revoked ${revokeConfirmKey}`);
								} catch (e: unknown) {
									toast.error(e instanceof Error ? e.message : "Failed to revoke setting");
								} finally {
									setRevokeConfirmKey(null);
								}
							}}
						>
							Revoke
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
		</>
	);
}
