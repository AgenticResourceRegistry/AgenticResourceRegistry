// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0


import { useState, useCallback, useEffect, useRef, useMemo } from "react";
import { Link } from "@tanstack/react-router";
import {
	Settings,
	Plus,
	Pencil,
	Trash2,
	Save,
	X,
	Loader2,
	Info,
	Database,
	Activity,
	Shield,
	HelpCircle,
	Eye,
	Upload,
	RotateCcw,
	Palette,
	AlertTriangle,
	ShieldAlert,
	ArrowLeftRight,
	Power,
} from "lucide-react";
import { InsightsSection } from "./settings/insights-section";
import { UsagePingSection } from "./settings/usage-ping-section";
import { MigrateDialog } from "./dashboard/components/migrate-dialog";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import { useHelp } from "@/components/wiki/help-context";
import { SETTING_DOCS, SECTION_DOCS } from "@/lib/docs-map";
import { useAdminSettings, useAdminSettingsSchema, useRestartApi, useRestartStatus, useSystemWarnings } from "@/hooks/use-api";
import { useDeploymentConfig } from "@/hooks/use-deployment-config";
import { useHarnesses } from "@/hooks/use-harnesses";
import { useRoleGuard, hasMinRole } from "@/hooks/use-role-guard";
import type { AdminSetting, AdminSettingDef, AdminSettingSection, SystemWarning } from "@/lib/types";
import { admin, getUserRole } from "@/lib/api";
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
import { PickerSelect } from "@/components/ui/picker-select";
import { PageHeader } from "@/components/layouts/page-header";
import { TableSkeleton } from "@/components/shared/skeleton-layouts";
import { ErrorState } from "@/components/shared/error-state";

// Sensitive keys that should never be displayed in plaintext.
// The server enforces this too (returns **REDACTED** for these keys),
// but we keep the set here for UI affordances (revoke button, write-only input).
const SENSITIVE_KEYS = new Set([
	"insights.api_key",
	"oauth.client_secret",
	"saml.idp_x509_cert",
	"saml.sp_key_encryption_password",
]);

const REDACTED_VALUE = "**REDACTED**";

const ALLOWED_LOGO_TYPES = [
	"image/png",
	"image/svg+xml",
	"image/x-icon",
	"image/vnd.microsoft.icon",
	"image/jpeg",
	"image/webp",
];
const MAX_LOGO_SIZE = 2 * 1024 * 1024;

const SECTION_ICONS: Record<string, React.ReactNode> = {
	insights: <Activity className="h-3.5 w-3.5" />,
	danger: <AlertTriangle className="h-3.5 w-3.5" />,
	deployment: <Shield className="h-3.5 w-3.5" />,
	security: <ShieldAlert className="h-3.5 w-3.5" />,
	sso: <Shield className="h-3.5 w-3.5" />,
	jwt: <Shield className="h-3.5 w-3.5" />,
	resource: <Database className="h-3.5 w-3.5" />,
	data: <Database className="h-3.5 w-3.5" />,
	observability: <Activity className="h-3.5 w-3.5" />,
	misc: <Settings className="h-3.5 w-3.5" />,
};

function sectionIcon(section: AdminSettingSection) {
	return SECTION_ICONS[section.id] ?? <Settings className="h-3.5 w-3.5" />;
}


function splitHarnessList(value: string): string[] {
	return value
		.split(",")
		.map((item) => item.trim())
		.filter(Boolean);
}

function joinHarnessList(values: string[]): string {
	return Array.from(new Set(values)).join(",");
}

function getHarnessLabel(harnesses: { name: string; display_name: string }[], value: string): string {
	return harnesses.find((harness) => harness.name === value)?.display_name ?? value;
}

function HarnessAllowlistEditor({
	value,
	onChange,
	harnesses,
}: {
	value: string;
	onChange: (value: string) => void;
	harnesses: { name: string; display_name: string }[];
}) {
	const selected = splitHarnessList(value);
	const available = harnesses.filter((harness) => !selected.includes(harness.name));

	const addHarness = (harness: string) => {
		const next = harness.trim();
		if (!next) return;
		onChange(joinHarnessList([...selected, next]));
	};

	const removeHarness = (harness: string) => {
		onChange(joinHarnessList(selected.filter((item) => item !== harness)));
	};

	return (
		<div className="flex-1 space-y-2">
			<div className="flex min-h-8 flex-wrap items-center gap-1.5 rounded-md border border-input bg-background px-2 py-1">
				{selected.length === 0 ? (
					<span className="text-xs text-muted-foreground">All supported harnesses</span>
				) : (
					selected.map((harness) => (
						<span key={harness} className="inline-flex items-center gap-1 rounded-md bg-muted px-2 py-0.5 text-xs text-foreground">
							{getHarnessLabel(harnesses, harness)}
							<button type="button" className="text-muted-foreground hover:text-foreground" onClick={() => removeHarness(harness)}>
								<X className="h-3 w-3" />
							</button>
						</span>
					))
				)}
			</div>
			<div className="flex items-center gap-2">
				<PickerSelect
					value=""
					onValueChange={addHarness}
					placeholder={selected.length === 0 ? "Restrict to specific harnesses" : "Add harness"}
					className="flex-1"
					inputClassName="h-8 text-sm"
					emptyLabel="All listed harnesses selected"
					options={available.map((harness) => ({ value: harness.name, label: harness.display_name }))}
				/>
				{selected.length > 0 && (
					<Button type="button" variant="ghost" size="sm" className="h-8" onClick={() => onChange("")}>Allow all</Button>
				)}
			</div>
		</div>
	);
}

function RestartRequiredMark() {
	return (
		<span
			className="inline-flex items-center gap-1 text-[11px] font-normal text-amber-700 dark:text-amber-300"
			title="Changing this setting requires an API restart"
		>
			<Power className="h-3 w-3" aria-hidden="true" />
			Restart
		</span>
	);
}

function SettingHelpIcon({ settingKey, openHelp }: { settingKey: string; openHelp: (key?: string) => boolean }) {
	if (!SETTING_DOCS[settingKey]) return null;
	return (
		<button
			type="button"
			className="absolute right-2 top-2 text-muted-foreground/40 transition-colors hover:text-foreground"
			onClick={(e) => {
				e.preventDefault();
				e.stopPropagation();
				openHelp(settingKey);
			}}
			aria-label="Open setting help"
		>
			<HelpCircle className="h-4.5 w-4.5" />
		</button>
	);
}

export default function SettingsPage() {
	const { ready } = useRoleGuard("super_admin");
	const queryClient = useQueryClient();
	const {
		data: settings,
		isLoading,
		isError,
		error,
		refetch,
	} = useAdminSettings();
	const { data: settingsSchema = [] } = useAdminSettingsSchema();
	const { data: restartStatus, refetch: refetchRestartStatus } = useRestartStatus();
	const { data: systemWarnings } = useSystemWarnings();
	const { data: harnesses = [] } = useHarnesses();
	const {
		ssoEnabled,
		samlEnabled,
		publicRegistryEnabled,
		brandingLogo,
		brandingAppName,
		brandingWordmark,
	} = useDeploymentConfig();
	const [editingKey, setEditingKey] = useState<string | null>(null);
	const [editingValue, setEditingValue] = useState("");
	const [revokeConfirmKey, setRevokeConfirmKey] = useState<string | null>(null);
	const [saving, setSaving] = useState(false);
	const [applyingResources, setApplyingResources] = useState(false);
	const [purgingTracesInsights, setPurgingTracesInsights] = useState(false);
	const [tracePrivacy, setTracePrivacy] = useState(false);
	const [tracePrivacyLoading, setTracePrivacyLoading] = useState(true);
	const [tracePrivacyToggling, setTracePrivacyToggling] = useState(false);
	const [publicRegistryToggling, setPublicRegistryToggling] = useState(false);
	const [registeredAgentsOnly, setRegisteredAgentsOnly] = useState(false);
	const [registeredAgentsOnlyLoading, setRegisteredAgentsOnlyLoading] =
		useState(() => hasMinRole(getUserRole(), "super_admin"));
	const [registeredAgentsOnlyToggling, setRegisteredAgentsOnlyToggling] =
		useState(false);
	const [retentionEnabled, setRetentionEnabled] = useState(false);
	const [retentionDays, setRetentionDays] = useState<string>("");
	const [scoreRetentionDays, setScoreRetentionDays] = useState<string>("");
	const [maxTraceCount, setMaxTraceCount] = useState<string>("");
	const [retentionGlobal, setRetentionGlobal] = useState(90);
	const [retentionLoading, setRetentionLoading] = useState(true);
	const [retentionSaving, setRetentionSaving] = useState(false);
	const [showRetentionConfirm, setShowRetentionConfirm] = useState(false);
	const [retentionConfirmChecked, setRetentionConfirmChecked] = useState(false);
	const [retentionPreview, setRetentionPreview] = useState<Record<
		string,
		number | string
	> | null>(null);
	const retentionWasEnabled = useRef(false);
	const [logoOverride, setLogoOverride] = useState<string | null | undefined>(
		undefined,
	);
	const [wordmarkOverride, setWordmarkOverride] = useState<
		string | null | undefined
	>(undefined);
	const [appNameOverride, setAppNameOverride] = useState<string | undefined>(
		undefined,
	);
	const [brandingSaving, setBrandingSaving] = useState(false);
	const [migrateOpen, setMigrateOpen] = useState(false);
	const handleRestarted = useCallback(() => {
		refetchRestartStatus();
		refetch();
	}, [refetch, refetchRestartStatus]);
	const { restarting, restartApi } = useRestartApi(handleRestarted);
	const fileInputRef = useRef<HTMLInputElement>(null);
	const wordmarkInputRef = useRef<HTMLInputElement>(null);

	// Help mode: modifier key + click opens contextual docs (provided by HelpProvider)
	const { helpActive, openHelp: openHelpCtx } = useHelp();
	const [helpBannerDismissed, setHelpBannerDismissed] = useState(() =>
		sessionStorage.getItem("observal_help_banner_dismissed") === "1"
	);

	const logoPreview = logoOverride !== undefined ? logoOverride : brandingLogo;

	/** Open the help panel for a setting key or section title */
	const openHelp = useCallback((settingKey?: string, sectionTitle?: string) => {
		return openHelpCtx({ settingKey, sectionTitle });
	}, [openHelpCtx]);

	/** CSS class applied to setting cards that have docs, when help mode is active */
	const helpTargetClass = (key: string) =>
		helpActive && SETTING_DOCS[key]
			? "ring-2 ring-primary/60 cursor-help transition-shadow"
			: "";
	const sectionHelpHeadingClass = (sectionTitle: string) =>
		helpActive && SECTION_DOCS[sectionTitle]
			? "cursor-help ring-2 ring-primary/60 ring-offset-2 ring-offset-background rounded-sm"
			: "";
	const wordmarkPreview =
		wordmarkOverride !== undefined ? wordmarkOverride : brandingWordmark;
	const appNameDraft =
		appNameOverride !== undefined ? appNameOverride : brandingAppName || "";

	useEffect(() => {
		admin
			.getTracePrivacy()
			.then((res) => setTracePrivacy(res.trace_privacy))
			.catch(() => { toast.error("Failed to load trace privacy setting"); })
			.finally(() => setTracePrivacyLoading(false));
		if (hasMinRole(getUserRole(), "super_admin")) {
			admin
				.getRegisteredAgentsOnly()
				.then((res) => setRegisteredAgentsOnly(res.registered_agents_only))
				.catch(() => { toast.error("Failed to load registered-agents-only setting"); })
				.finally(() => setRegisteredAgentsOnlyLoading(false));
		}
		admin
			.getRetention()
			.then((res) => {
				setRetentionEnabled(res.retention_enabled);
				retentionWasEnabled.current = res.retention_enabled;
				setRetentionDays(res.data_retention_days?.toString() || "");
				setScoreRetentionDays(res.score_retention_days?.toString() || "");
				setMaxTraceCount(res.max_trace_count?.toString() || "");
				setRetentionGlobal(res.global_retention_days);
			})
			.catch(() => { toast.error("Failed to load retention settings"); })
			.finally(() => setRetentionLoading(false));
	}, []);

	const handlePurgeTracesInsights = useCallback(async () => {
		if (!window.confirm("Permanently delete all traces/session telemetry and insight reports for this deployment? This cannot be undone.")) {
			return;
		}
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
			toast.success(
				`Trace privacy ${res.trace_privacy ? "enabled" : "disabled"}`,
			);
		} catch (e) {
			toast.error(
				e instanceof Error ? e.message : "Failed to update trace privacy",
			);
		} finally {
			setTracePrivacyToggling(false);
		}
	}, []);

	const handlePublicRegistryToggle = useCallback(async (checked: boolean) => {
		setPublicRegistryToggling(true);
		try {
			await admin.updateSetting("deployment.public_registry_enabled", {
				value: checked ? "true" : "false",
			});
			await queryClient.invalidateQueries({ queryKey: ["config", "public"] });
			await refetch();
			toast.success(`Public registry access ${checked ? "enabled" : "disabled"}`);
		} catch (e) {
			toast.error(
				e instanceof Error ? e.message : "Failed to update public registry access",
			);
		} finally {
			setPublicRegistryToggling(false);
		}
	}, [queryClient, refetch]);

	const handleRegisteredAgentsOnlyToggle = useCallback(
		async (checked: boolean) => {
			setRegisteredAgentsOnlyToggling(true);
			try {
				const res = await admin.setRegisteredAgentsOnly(checked);
				setRegisteredAgentsOnly(res.registered_agents_only);
				toast.success(
					`Registered agents only ${res.registered_agents_only ? "enabled" : "disabled"}`,
				);
			} catch (e) {
				toast.error(
					e instanceof Error ? e.message : "Failed to update setting",
				);
			} finally {
				setRegisteredAgentsOnlyToggling(false);
			}
		},
		[],
	);

	const retentionErrors = useMemo(() => {
		const errors: {
			data_retention_days?: string;
			score_retention_days?: string;
			max_trace_count?: string;
			general?: string;
		} = {};
		const days = retentionDays ? parseInt(retentionDays, 10) : null;
		const scoreDays = scoreRetentionDays
			? parseInt(scoreRetentionDays, 10)
			: null;
		const maxCount = maxTraceCount ? parseInt(maxTraceCount, 10) : null;

		if (days !== null && !isNaN(days)) {
			if (days < 7) errors.data_retention_days = "Minimum 7 days";
			else if (retentionGlobal > 0 && days > retentionGlobal)
				errors.data_retention_days = `Cannot exceed global limit of ${retentionGlobal} days`;
		}
		if (scoreDays !== null && !isNaN(scoreDays)) {
			if (scoreDays < 7) errors.score_retention_days = "Minimum 7 days";
			else if (days && scoreDays < days)
				errors.score_retention_days = `Must be ≥ trace retention (${days} days)`;
		}
		if (maxCount !== null && !isNaN(maxCount)) {
			if (maxCount < 1000) errors.max_trace_count = "Minimum 1,000 traces";
		}
		if (retentionEnabled && !days && !maxCount) {
			errors.general = "Set at least one retention threshold to enable";
		}

		return errors;
	}, [
		retentionDays,
		scoreRetentionDays,
		maxTraceCount,
		retentionEnabled,
		retentionGlobal,
	]);

	const hasRetentionErrors = Object.keys(retentionErrors).length > 0;

	const handleRetentionSave = useCallback(async () => {
		const days = retentionDays ? parseInt(retentionDays, 10) : null;
		const scoreDays = scoreRetentionDays
			? parseInt(scoreRetentionDays, 10)
			: null;
		const maxCount = maxTraceCount ? parseInt(maxTraceCount, 10) : null;

		if (retentionEnabled && !retentionWasEnabled.current && days) {
			setShowRetentionConfirm(true);
			admin
				.previewRetention(days)
				.then(setRetentionPreview)
				.catch(() => setRetentionPreview(null));
			return;
		}

		setRetentionSaving(true);
		try {
			const res = await admin.setRetention({
				retention_enabled: retentionEnabled,
				data_retention_days: days,
				score_retention_days: scoreDays,
				max_trace_count: maxCount,
			});
			setRetentionEnabled(res.retention_enabled);
			retentionWasEnabled.current = res.retention_enabled;
			setRetentionDays(res.data_retention_days?.toString() || "");
			setScoreRetentionDays(res.score_retention_days?.toString() || "");
			setMaxTraceCount(res.max_trace_count?.toString() || "");
			queryClient.invalidateQueries({ queryKey: ["admin", "retention"] });
			toast.success("Retention settings updated");
		} catch (e) {
			toast.error(
				e instanceof Error ? e.message : "Failed to update retention",
			);
		} finally {
			setRetentionSaving(false);
		}
	}, [
		retentionEnabled,
		retentionDays,
		scoreRetentionDays,
		maxTraceCount,
		queryClient,
	]);

	const handleRetentionConfirm = useCallback(async () => {
		setShowRetentionConfirm(false);
		setRetentionConfirmChecked(false);
		setRetentionSaving(true);
		const days = retentionDays ? parseInt(retentionDays, 10) : null;
		const scoreDays = scoreRetentionDays
			? parseInt(scoreRetentionDays, 10)
			: null;
		const maxCount = maxTraceCount ? parseInt(maxTraceCount, 10) : null;
		try {
			const res = await admin.setRetention({
				retention_enabled: true,
				data_retention_days: days,
				score_retention_days: scoreDays,
				max_trace_count: maxCount,
			});
			setRetentionEnabled(res.retention_enabled);
			retentionWasEnabled.current = res.retention_enabled;
			setRetentionDays(res.data_retention_days?.toString() || "");
			setScoreRetentionDays(res.score_retention_days?.toString() || "");
			setMaxTraceCount(res.max_trace_count?.toString() || "");
			queryClient.invalidateQueries({ queryKey: ["admin", "retention"] });
			toast.success("Data retention enabled");
		} catch (e) {
			toast.error(
				e instanceof Error ? e.message : "Failed to enable retention",
			);
		} finally {
			setRetentionSaving(false);
			setRetentionPreview(null);
		}
	}, [retentionDays, scoreRetentionDays, maxTraceCount, queryClient]);

	const handleImageFile = useCallback(
		(file: File, setter: (v: string) => void) => {
			if (!ALLOWED_LOGO_TYPES.includes(file.type)) {
				toast.error("Unsupported file type. Use PNG, SVG, ICO, JPEG, or WEBP.");
				return;
			}
			if (file.size > MAX_LOGO_SIZE) {
				toast.error(
					`File too large (${Math.round(file.size / 1024)}KB). Maximum: 2MB.`,
				);
				return;
			}
			const reader = new FileReader();
			reader.onload = () => setter(reader.result as string);
			reader.readAsDataURL(file);
		},
		[],
	);

	const handleSaveBranding = useCallback(async () => {
		setBrandingSaving(true);
		try {
			if (logoPreview !== brandingLogo) {
				await admin.updateSetting("branding.logo", {
					value: logoPreview || "",
				});
			}
			if (wordmarkPreview !== brandingWordmark) {
				await admin.updateSetting("branding.wordmark", {
					value: wordmarkPreview || "",
				});
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
	}, [
		logoPreview,
		brandingLogo,
		wordmarkPreview,
		brandingWordmark,
		appNameDraft,
		brandingAppName,
		queryClient,
	]);

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

	const hasBrandingChanges =
		logoPreview !== brandingLogo ||
		wordmarkPreview !== brandingWordmark ||
		appNameDraft.trim() !== (brandingAppName || "");

	const entries: { key: string; value: string }[] = (
		Array.isArray(settings)
			? settings.map((s: AdminSetting) => ({ key: s.key, value: s.value }))
			: Object.entries(settings ?? {}).map(([k, v]) => ({
					key: k,
					value: String(v),
				}))
	).filter((e) => !e.key.startsWith("branding."));

	const settingSections = settingsSchema.filter((section) => section.id !== "sso" && section.id !== "usage_ping");
	const settingByKey = useMemo(() => {
		const map = new Map<string, AdminSettingDef>();
		for (const section of settingSections) {
			for (const setting of section.settings) map.set(setting.key, setting);
		}
		return map;
	}, [settingSections]);


	const handleInlineSave = useCallback(async () => {
		if (!editingKey) return;
		setSaving(true);
		try {
			await admin.updateSetting(editingKey, { value: editingValue });
			toast.success(`Saved ${editingKey}`);
			if (editingKey === "misc.harness_allowlist" || editingKey === "misc.default_harness") {
				queryClient.invalidateQueries({ queryKey: ["config", "harnesses"] });
			}
			setEditingKey(null);
			setEditingValue("");
			refetch();
			refetchRestartStatus();
		} catch (e) {
			toast.error(e instanceof Error ? e.message : "Failed to save");
		} finally {
			setSaving(false);
		}
	}, [editingKey, editingValue, queryClient, refetch, refetchRestartStatus]);

	const renderSettingEditor = (key: string) => {
		if (key === "misc.harness_allowlist") {
			return <HarnessAllowlistEditor value={editingValue} onChange={setEditingValue} harnesses={harnesses} />;
		}
		if (key === "misc.default_harness") {
			return (
				<PickerSelect
					value={editingValue || "__none__"}
					onValueChange={(value) => setEditingValue(value === "__none__" ? "" : value)}
					placeholder="Choose default harness"
					className="flex-1"
					inputClassName="h-8 text-sm"
					options={[
						{ value: "__none__", label: "Use first allowed harness" },
						...harnesses.map((harness) => ({ value: harness.name, label: harness.display_name })),
					]}
				/>
			);
		}
		return (
			<Input
				value={editingValue}
				onChange={(e) => setEditingValue(e.target.value)}
				placeholder={settingByKey.get(key)?.default || "Enter value..."}
				className="h-8 text-sm flex-1 font-[family-name:var(--font-mono)]"
				autoFocus
				onKeyDown={(e) => {
					if (e.key === "Enter") handleInlineSave();
					if (e.key === "Escape") {
						setEditingKey(null);
						setEditingValue("");
					}
				}}
			/>
		);
	};

	const handleApplyResources = useCallback(async () => {
		setApplyingResources(true);
		try {
			const res = await admin.applyResources();
			const count = Object.keys(res.applied).length;
			if (count > 0) {
				toast.success(
					`Applied ${count} resource setting${count > 1 ? "s" : ""} to ClickHouse`,
				);
			} else {
				toast.info(
					"No resource settings configured yet. Add resource.* settings first.",
				);
			}
		} catch (e) {
			toast.error(
				e instanceof Error ? e.message : "Failed to apply resource settings",
			);
		} finally {
			setApplyingResources(false);
		}
	}, []);


	if (!ready) return null;

	return (
		<>
			<PageHeader
				title="Settings"
				breadcrumbs={[
					{ label: "Dashboard", href: "/dashboard" },
					{ label: "Settings" },
				]}
				actionButtonsRight={
					<div className="flex items-center gap-2">
						<span
							className={`rounded-full border px-2.5 py-1 text-xs font-medium ${
								restartStatus?.required
									? "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300"
									: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
							}`}
						>
							{restartStatus?.required ? "Saved, API restart required" : "Settings live"}
						</span>
						{restartStatus?.required && (
							<Button size="sm" variant="outline" onClick={restartApi} disabled={restarting}>
								{restarting ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Power className="mr-1.5 h-3.5 w-3.5" />}
								{restarting ? "Restarting" : "Restart API"}
							</Button>
						)}
					</div>
				}
			/>
			<div className="page-body w-full mx-auto space-y-5">
				{getUserRole() === "super_admin" && <Link to="/review-policy" className="flex items-center justify-between rounded-lg border border-border bg-card px-4 py-3 text-sm font-medium hover:bg-muted/50 focus-visible:outline-2 focus-visible:outline-ring"><span className="flex items-center gap-2"><Shield className="size-4" /> Review policy</span><span className="text-xs font-normal text-muted-foreground">Approval counts and publication rules →</span></Link>}
				{/* Security warnings */}
				{systemWarnings && systemWarnings.length > 0 && (
					<section className="animate-in">
						<div className="space-y-2">
							{systemWarnings.map((w: SystemWarning) => (
								<div
									key={w.code}
									className={`rounded-md border px-4 py-3 flex items-start gap-3 ${
										w.level === "critical"
											? "border-destructive/40 bg-destructive/10"
											: "border-warning/40 bg-warning/10"
									}`}
								>
									{w.level === "critical" ? (
										<ShieldAlert className="h-4 w-4 mt-0.5 shrink-0 text-destructive" />
									) : (
										<AlertTriangle className="h-4 w-4 mt-0.5 shrink-0 text-warning" />
									)}
									<div>
										<p
											className={`text-sm font-medium ${w.level === "critical" ? "text-destructive" : "text-warning"}`}
										>
											{w.level === "critical" ? "Critical" : "Warning"}
										</p>
										<p className="text-xs text-muted-foreground mt-0.5">
											{w.message}
										</p>
									</div>
								</div>
							))}
						</div>
					</section>
				)}
				{/* System Overview */}
				<section className="animate-in">
					<h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">
						System Overview
					</h3>
					<div className="rounded-md border border-border bg-card px-4 py-3 space-y-2">
						<div className="flex items-center justify-between py-1">
							<span className="text-xs text-muted-foreground">
								SSO (OAuth/OIDC)
							</span>
							<span
								className={`text-xs font-medium ${ssoEnabled ? "text-success" : "text-muted-foreground"}`}
							>
								{ssoEnabled ? "Enabled" : "Disabled"}
							</span>
						</div>
						<div className="flex items-center justify-between py-1 border-t border-border">
							<span className="text-xs text-muted-foreground">SAML SSO</span>
							<span
								className={`text-xs font-medium ${samlEnabled ? "text-success" : "text-muted-foreground"}`}
							>
								{samlEnabled ? "Configured" : "Not configured"}
							</span>
						</div>
					</div>
				</section>

				<UsagePingSection settings={entries as AdminSetting[]} onChanged={() => { void refetch(); }} />

				{/* Appearance */}
				<section className="animate-in">
					<h3 className={`text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3 flex items-center gap-1.5 ${sectionHelpHeadingClass("Appearance")}`} onClick={(event) => { if ((event.ctrlKey || event.metaKey) && openHelp(undefined, "Appearance")) event.preventDefault(); }}>
						<Palette className="h-3.5 w-3.5" />
						Appearance
						<button type="button" className="text-muted-foreground hover:text-primary" onClick={(event) => { event.preventDefault(); event.stopPropagation(); openHelp(undefined, "Appearance"); }} aria-label="Open Appearance documentation">
							<HelpCircle className="h-3.5 w-3.5" />
						</button>
					</h3>
					<div className="rounded-md border border-border bg-card px-4 py-3 space-y-3">
						<p className="text-xs text-muted-foreground">
							PNG, SVG, ICO, JPEG, or WEBP. Max 2MB. Transparent images
							recommended for theme compatibility.
						</p>
						<div className="flex flex-wrap gap-4">
							{/* Logo icon */}
							<div className="space-y-1.5">
								<p className="text-xs font-medium">Icon</p>
								<div
									className="w-12 h-12 rounded border-2 border-dashed border-border flex items-center justify-center cursor-pointer hover:border-primary/50 transition-colors bg-muted/30"
									onClick={() => fileInputRef.current?.click()}
									onDragOver={(e) => e.preventDefault()}
									onDrop={(e) => {
										e.preventDefault();
										const f = e.dataTransfer.files[0];
										if (f) handleImageFile(f, setLogoOverride);
									}}
								>
									{logoPreview ? (
										<img
											src={logoPreview}
											alt="Icon"
											width={32}
											height={32}
											className="object-contain"
										/>
									) : (
										<Upload className="h-4 w-4 text-muted-foreground" />
									)}
								</div>
								<input
									ref={fileInputRef}
									type="file"
									accept="image/png,image/svg+xml,image/x-icon,image/jpeg,image/webp"
									className="hidden"
									onChange={(e) => {
										const f = e.target.files?.[0];
										if (f) handleImageFile(f, setLogoOverride);
										e.target.value = "";
									}}
								/>
								<div className="flex gap-1">
									<Button
										variant="ghost"
										size="sm"
										className="h-6 text-[11px] px-1.5"
										onClick={() => fileInputRef.current?.click()}
									>
										Upload
									</Button>
									{logoPreview && (
										<Button
											variant="ghost"
											size="sm"
											className="h-6 text-[11px] px-1.5 text-muted-foreground"
											onClick={() => setLogoOverride(null)}
										>
											Remove
										</Button>
									)}
								</div>
							</div>
							{/* Wordmark */}
							<div className="space-y-1.5">
								<p className="text-xs font-medium">
									Wordmark{" "}
									<span className="text-muted-foreground font-normal">
										(optional, replaces text)
									</span>
								</p>
								<div
									className="w-28 h-12 rounded border-2 border-dashed border-border flex items-center justify-center cursor-pointer hover:border-primary/50 transition-colors bg-muted/30"
									onClick={() => wordmarkInputRef.current?.click()}
									onDragOver={(e) => e.preventDefault()}
									onDrop={(e) => {
										e.preventDefault();
										const f = e.dataTransfer.files[0];
										if (f) handleImageFile(f, setWordmarkOverride);
									}}
								>
									{wordmarkPreview ? (
										<img
											src={wordmarkPreview}
											alt="Wordmark"
											width={96}
											height={24}
											className="h-6 max-w-24 object-contain"
										/>
									) : (
										<Upload className="h-4 w-4 text-muted-foreground" />
									)}
								</div>
								<input
									ref={wordmarkInputRef}
									type="file"
									accept="image/png,image/svg+xml,image/x-icon,image/jpeg,image/webp"
									className="hidden"
									onChange={(e) => {
										const f = e.target.files?.[0];
										if (f) handleImageFile(f, setWordmarkOverride);
										e.target.value = "";
									}}
								/>
								<div className="flex gap-1">
									<Button
										variant="ghost"
										size="sm"
										className="h-6 text-[11px] px-1.5"
										onClick={() => wordmarkInputRef.current?.click()}
									>
										Upload
									</Button>
									{wordmarkPreview && (
										<Button
											variant="ghost"
											size="sm"
											className="h-6 text-[11px] px-1.5 text-muted-foreground"
											onClick={() => setWordmarkOverride(null)}
										>
											Remove
										</Button>
									)}
								</div>
							</div>
							{/* App name (text fallback) */}
							<div className="space-y-1.5">
								<p className="text-xs font-medium">
									App Name{" "}
									<span className="text-muted-foreground font-normal">
										(used when no wordmark)
									</span>
								</p>
								<Input
									value={appNameDraft}
									onChange={(e) => setAppNameOverride(e.target.value)}
									placeholder="Observal"
									autoComplete="off"
									maxLength={30}
									className="h-8 text-sm w-48"
								/>
								<p className="text-[11px] text-muted-foreground">
									{appNameDraft.length}/30
								</p>
							</div>
						</div>
						{/* Preview + actions */}
						<div className="flex items-center gap-4 pt-1 border-t border-border">
							<div className="rounded bg-sidebar px-3 py-2 inline-flex items-center gap-2">
								<div className="flex size-8 shrink-0 items-center justify-center">
									{logoPreview ? (
										<img
											src={logoPreview}
											alt=""
											width={20}
											height={20}
											className="object-contain"
										/>
									) : (
										<img
											src="/observal-logo.svg"
											alt=""
											width={20}
											height={20}
											className="object-contain"
										/>
									)}
								</div>
								{wordmarkPreview ? (
									<img
										src={wordmarkPreview}
										alt=""
										width={140}
										height={16}
										className="h-4 max-w-35 object-contain object-left"
									/>
								) : (
									<span className="text-sm font-semibold tracking-tight font-display text-sidebar-foreground truncate max-w-35">
										{appNameDraft.trim() || "Observal"}
									</span>
								)}
							</div>
							<div className="flex items-center gap-2">
								<Button
									size="sm"
									className="h-7 text-xs"
									onClick={handleSaveBranding}
									disabled={brandingSaving || !hasBrandingChanges}
								>
									{brandingSaving ? (
										<Loader2 className="mr-1 h-3 w-3 animate-spin" />
									) : (
										<Save className="mr-1 h-3 w-3" />
									)}
									Save
								</Button>
								{(brandingLogo || brandingAppName || brandingWordmark) && (
									<Button
										size="sm"
										variant="outline"
										className="h-7 text-xs"
										onClick={handleResetBranding}
										disabled={brandingSaving}
									>
										<RotateCcw className="mr-1 h-3 w-3" />
										Reset
									</Button>
								)}
							</div>
						</div>
					</div>
				</section>

				{/* Public registry access */}
				<section className="animate-in">
					<h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3 flex items-center gap-1.5">
						<Eye className="h-3.5 w-3.5" />
						Public Registry
					</h3>
					<div className="rounded-md border border-border bg-card px-4 py-3">
						<div className="flex items-center justify-between gap-4">
							<div className="flex-1">
								<p className="text-sm font-medium">Allow public browsing and installs</p>
								<p className="text-xs text-muted-foreground mt-0.5">
									Signed-out visitors can browse and install approved public agents and components. Publishing, private content, telemetry, and administration still require sign-in.
								</p>
							</div>
							<Switch
								checked={publicRegistryEnabled}
								onCheckedChange={handlePublicRegistryToggle}
								disabled={publicRegistryToggling}
								aria-label="Allow public registry browsing and installs"
							/>
						</div>
					</div>
				</section>

				{/* Trace Privacy */}
				<section className="animate-in">
					<h3 className={`text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3 flex items-center gap-1.5 ${sectionHelpHeadingClass("Trace Privacy")}`} onClick={(event) => { if ((event.ctrlKey || event.metaKey) && openHelp(undefined, "Trace Privacy")) event.preventDefault(); }}>
						<Eye className="h-3.5 w-3.5" />
						Trace Privacy
						<button type="button" className="text-muted-foreground hover:text-primary" onClick={(event) => { event.preventDefault(); event.stopPropagation(); openHelp(undefined, "Trace Privacy"); }} aria-label="Open Trace Privacy documentation">
							<HelpCircle className="h-3.5 w-3.5" />
						</button>
					</h3>
					<div className="rounded-md border border-border bg-card px-4 py-3">
						<div className="flex items-center justify-between">
							<div className="flex-1">
								<p className="text-sm font-medium">Restrict trace visibility</p>
								<p className="text-xs text-muted-foreground mt-0.5">
									When enabled, all users (including admins) can only see their
									own traces. Super-admins always retain full visibility across
									all traces.
								</p>
							</div>
							<Switch
								checked={tracePrivacy}
								onCheckedChange={handleTracePrivacyToggle}
								disabled={tracePrivacyLoading || tracePrivacyToggling}
							/>
						</div>
					</div>
				</section>

				{/* Registered Agents Only, super_admin only */}
				{hasMinRole(getUserRole(), "super_admin") && (
					<section className="animate-in">
						<h3 className={`text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3 flex items-center gap-1.5 ${sectionHelpHeadingClass("Registered Agents Only")}`} onClick={(event) => { if ((event.ctrlKey || event.metaKey) && openHelp(undefined, "Registered Agents Only")) event.preventDefault(); }}>
							<Shield className="h-3.5 w-3.5" />
							Registered Agents Only
							<button type="button" className="text-muted-foreground hover:text-primary" onClick={(event) => { event.preventDefault(); event.stopPropagation(); openHelp(undefined, "Registered Agents Only"); }} aria-label="Open Registered Agents Only documentation">
								<HelpCircle className="h-3.5 w-3.5" />
							</button>
						</h3>
						<div className="rounded-md border border-border bg-card px-4 py-3">
							<div className="flex items-center justify-between">
								<div className="flex-1">
									<p className="text-sm font-medium">
										Only trace registered agents
									</p>
									<p className="text-xs text-muted-foreground mt-0.5">
										Unstable. When enabled, only registered agents are traced.
										Unregistered agent activity may be missing from traces.
									</p>
								</div>
								<Switch
									checked={registeredAgentsOnly}
									onCheckedChange={handleRegisteredAgentsOnlyToggle}
									disabled={
										registeredAgentsOnlyLoading || registeredAgentsOnlyToggling
									}
								/>
							</div>
						</div>
					</section>
				)}

				{/* Data Migration, super_admin only */}
				{hasMinRole(getUserRole(), "super_admin") && (
					<section className="animate-in">
						<h3 className={`text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3 flex items-center gap-1.5 ${sectionHelpHeadingClass("Data Migration")}`} onClick={(event) => { if ((event.ctrlKey || event.metaKey) && openHelp(undefined, "Data Migration")) event.preventDefault(); }}>
							<ArrowLeftRight className="h-3.5 w-3.5" />
							Data Migration
							<button type="button" className="text-muted-foreground hover:text-primary" onClick={(event) => { event.preventDefault(); event.stopPropagation(); openHelp(undefined, "Data Migration"); }} aria-label="Open Data Migration documentation">
								<HelpCircle className="h-3.5 w-3.5" />
							</button>
						</h3>
						<div className="rounded-md border border-border bg-card px-4 py-3">
							<div className="flex items-center justify-between gap-4">
								<div className="flex-1">
									<p className="text-sm font-medium">
										Export, import, and validate instance data
									</p>
									<p className="text-xs text-muted-foreground mt-0.5">
										Move registry data and telemetry between Observal instances. Start with the guide if this is your first run.
									</p>
								</div>
								<div className="flex items-center gap-2">
									<Button type="button" variant="outline" size="sm" onClick={() => openHelpCtx({ pageKey: "migration" })}>
										<HelpCircle className="h-3.5 w-3.5" />
										Guide
									</Button>
									<Button type="button" size="sm" onClick={() => setMigrateOpen(true)}>
										<ArrowLeftRight className="h-3.5 w-3.5" />
										Migrate
									</Button>
								</div>
							</div>
						</div>
						<MigrateDialog open={migrateOpen} onOpenChange={setMigrateOpen} />
					</section>
				)}

				{/* Data Retention, super_admin only */}
				{hasMinRole(getUserRole(), "super_admin") && (
					<section className="animate-in">
						<h3 className={`text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3 flex items-center gap-1.5 ${sectionHelpHeadingClass("Data Retention")}`} onClick={(event) => { if ((event.ctrlKey || event.metaKey) && openHelp(undefined, "Data Retention")) event.preventDefault(); }}>
							<Database className="h-3.5 w-3.5" />
							Data Retention
							<button type="button" className="text-muted-foreground hover:text-primary" onClick={(event) => { event.preventDefault(); event.stopPropagation(); openHelp(undefined, "Data Retention"); }} aria-label="Open Data Retention documentation">
								<HelpCircle className="h-3.5 w-3.5" />
							</button>
						</h3>
						<div className="rounded-md border border-border bg-card p-4 space-y-4">
							<div className="flex items-center justify-between">
								<div className="flex-1">
									<p className="text-sm font-medium">Enable data retention</p>
									<p className="text-xs text-muted-foreground mt-0.5">
										Automatically purge telemetry data older than the configured
										period. Global ceiling:{" "}
										{retentionGlobal > 0
											? `${retentionGlobal} days`
											: "disabled"}
										.
									</p>
								</div>
								<Switch
									checked={retentionEnabled}
									onCheckedChange={setRetentionEnabled}
									disabled={retentionLoading}
								/>
							</div>

							{retentionEnabled && (
								<div className="space-y-3 pt-2 border-t border-border/50">
									<div>
										<label className="text-xs text-muted-foreground">
											Trace retention (days)
										</label>
										<Input
											type="number"
											min={7}
											max={retentionGlobal > 0 ? retentionGlobal : undefined}
											value={retentionDays}
											onChange={(e) => setRetentionDays(e.target.value)}
											placeholder="e.g. 30"
											className="h-8 text-sm mt-1 max-w-[200px]"
										/>
										{retentionErrors.data_retention_days && (
											<p className="text-xs text-destructive mt-1">
												{retentionErrors.data_retention_days}
											</p>
										)}
									</div>
									<div>
										<label className="text-xs text-muted-foreground">
											Score & insight retention (days)
										</label>
										<Input
											type="number"
											min={7}
											value={scoreRetentionDays}
											onChange={(e) => setScoreRetentionDays(e.target.value)}
											placeholder="e.g. 30 (default: 2x trace retention)"
											className="h-8 text-sm mt-1 max-w-[200px]"
										/>
										{retentionErrors.score_retention_days && (
											<p className="text-xs text-destructive mt-1">
												{retentionErrors.score_retention_days}
											</p>
										)}
									</div>
									<div>
										<label className="text-xs text-muted-foreground">
											Max trace count (optional)
										</label>
										<Input
											type="number"
											min={1000}
											value={maxTraceCount}
											onChange={(e) => setMaxTraceCount(e.target.value)}
											placeholder="e.g. 100000"
											className="h-8 text-sm mt-1 max-w-[200px]"
										/>
										{retentionErrors.max_trace_count && (
											<p className="text-xs text-destructive mt-1">
												{retentionErrors.max_trace_count}
											</p>
										)}
									</div>
									{retentionErrors.general && (
										<p className="text-xs text-destructive">
											{retentionErrors.general}
										</p>
									)}
								</div>
							)}

							<div className="flex justify-end pt-2">
								<Button
									size="sm"
									className="h-8"
									onClick={handleRetentionSave}
									disabled={
										retentionLoading || retentionSaving || hasRetentionErrors
									}
								>
									{retentionSaving ? (
										<Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" />
									) : (
										<Save className="h-3.5 w-3.5 mr-1.5" />
									)}
									Save
								</Button>
							</div>
						</div>

						{/* Confirmation dialog */}
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
										<AlertTriangle className="h-4 w-4 text-amber-500" />
										Enable Data Retention?
									</DialogTitle>
									<DialogDescription className="text-xs">
										This will permanently delete telemetry data older than{" "}
										{retentionDays} days. Purges run automatically every 6
										hours. This action cannot be undone.
									</DialogDescription>
								</DialogHeader>
								{retentionPreview && (
									<div className="rounded bg-muted/50 p-3 text-xs space-y-1">
										<p className="font-medium text-muted-foreground">
											Estimated deletions:
										</p>
										{Object.entries(retentionPreview)
											.filter(([k]) => !k.startsWith("_"))
											.map(([k, v]) => (
												<p key={k}>
													{k}: {typeof v === "number" ? v.toLocaleString() : v}{" "}
													rows
												</p>
											))}
									</div>
								)}
								<label className="flex items-center gap-2 text-xs cursor-pointer">
									<Checkbox
										checked={retentionConfirmChecked}
										onCheckedChange={(checked) =>
											setRetentionConfirmChecked(checked === true)
										}
									/>
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
									<Button
										size="sm"
										variant="destructive"
										onClick={handleRetentionConfirm}
										disabled={!retentionConfirmChecked}
									>
										Enable Retention
									</Button>
								</DialogFooter>
							</DialogContent>
						</Dialog>
					</section>
				)}

				{isLoading ? (
					<TableSkeleton rows={5} cols={2} />
				) : isError ? (
					<ErrorState message={error?.message} onRetry={() => refetch()} />
				) : (
					<div className="animate-in space-y-6">
						{/* Help mode hint banner */}
						{!helpBannerDismissed && (
							<div className="flex items-center gap-3 rounded-md border border-primary/30 bg-primary/5 px-4 py-2.5">
								<HelpCircle className="h-3.5 w-3.5 shrink-0 text-primary" />
								<span className="text-sm text-foreground/80">Use guide icons for docs. Hold {navigator.platform?.includes("Mac") ? "Command" : "Ctrl"} and click highlighted settings for field-level help.</span>
								<button
									type="button"
									className="ml-auto text-muted-foreground hover:text-foreground transition-colors"
									onClick={() => { setHelpBannerDismissed(true); sessionStorage.setItem("observal_help_banner_dismissed", "1"); }}
								>
									<X className="h-3.5 w-3.5" />
								</button>
							</div>
						)}
						{/* Add new setting form */}
						{/* Unified sections, each setting stays in its section */}
						{settingSections.filter((s) => !s.danger).map((section) => {
								const visibleSettings = section.settings.filter(
									(setting) => setting.key !== "deployment.public_registry_enabled",
								);
								if (visibleSettings.length === 0) return null;

								if (section.title === "Agent Insights") {
									return (
										<InsightsSection
											key={section.title}
											entries={entries as { key: string; value: string; is_sensitive?: boolean; is_set?: boolean }[]}
											onSave={async (key, value) => {
												await admin.updateSetting(key, { value });
												refetch();
											}}
											onRevoke={(key) => setRevokeConfirmKey(key)}
											refetch={refetch}
										/>
									);
								}

								// Check for deprecated AWS settings that are still configured
								const deprecatedAwsKeys = ["insights.aws_region", "insights.aws_access_key_id", "insights.aws_secret_access_key", "insights.aws_session_token", "insights.model_url", "insights.model_api_key"];
								const hasNewApiKey = entries.some((e) => e.key === "insights.api_key" && e.value && e.value !== "");
								const hasDeprecatedSettings = section.title === "Agent Insights" && !hasNewApiKey && entries.some((e) => deprecatedAwsKeys.includes(e.key) && e.value && e.value !== "");
								return (
								<section key={section.title} className="mb-6">
									<h3
										className={`text-sm font-semibold uppercase tracking-wider text-foreground/80 mb-1 flex items-center gap-1.5 ${helpActive && SECTION_DOCS[section.title] ? "cursor-help ring-2 ring-primary/60 ring-offset-2 ring-offset-background rounded-sm" : ""}`}
										onClick={(event) => {
											if ((event.ctrlKey || event.metaKey) && openHelp(undefined, section.title)) {
												event.preventDefault();
											}
										}}
									>
										{sectionIcon(section)}
										{section.title}
										{SECTION_DOCS[section.title] && (
											<button type="button" className="ml-1 text-muted-foreground hover:text-primary" onClick={(event) => { event.preventDefault(); event.stopPropagation(); openHelp(undefined, section.title); }} aria-label={`Open ${section.title} documentation`}>
												<HelpCircle className="h-3.5 w-3.5" />
											</button>
										)}
									</h3>
									{section.description && (
										<p className="text-xs text-foreground/60 mb-3">{section.description}</p>
									)}
									{hasDeprecatedSettings && (
										<div className="mb-3 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-900 dark:text-amber-200">
											<strong>Deprecated settings detected.</strong> AWS-specific credential fields are no longer used. Please configure the API Key field above with your provider key (or a{" "}
											<a href="https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys.html" target="_blank" rel="noopener noreferrer" className="underline text-amber-950 hover:text-amber-800 dark:text-amber-300 dark:hover:text-amber-100">Bedrock API key</a>
											) and use{" "}
											<a href="https://docs.litellm.ai/docs/providers" target="_blank" rel="noopener noreferrer" className="underline text-amber-950 hover:text-amber-800 dark:text-amber-300 dark:hover:text-amber-100">LiteLLM provider format</a>
											{" "}for model IDs. You can safely delete the old AWS settings.
										</div>
									)}
									<div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
									{visibleSettings.map((d) => {
										const existing = entries.find((e) => e.key === d.key);
										const isEditing = editingKey === d.key;
										if (isEditing) {
											return (
												<div key={d.key} className={`rounded-md border-2 border-primary/50 bg-card p-3 ${helpTargetClass(d.key)}`} onClick={(e) => { if ((e.ctrlKey || e.metaKey) && openHelp(d.key)) { e.preventDefault(); e.stopPropagation(); } }}>
													<div className="mb-2 flex items-center gap-2"><span className="text-sm font-semibold text-foreground">{d.label}</span>{d.restart_required && <RestartRequiredMark />}</div>
													<div className="flex items-center gap-2">
														{renderSettingEditor(d.key)}
														<Button size="sm" className="h-8" onClick={handleInlineSave} disabled={saving}>{saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}</Button>
														<Button size="sm" variant="ghost" className="h-8" onClick={() => { setEditingKey(null); setEditingValue(""); }}><X className="h-3.5 w-3.5" /></Button>
													</div>
												</div>
											);
										}
										if (existing && existing.value) {
											const isSensitive = (existing as AdminSetting).is_sensitive || SENSITIVE_KEYS.has(d.key);
											const isSet = (existing as AdminSetting).is_set ?? !!existing.value;
											return (
												<div key={d.key} className={`rounded-md border-2 border-border bg-card p-3 relative ${helpTargetClass(d.key)}`} onClick={(e) => { if ((e.ctrlKey || e.metaKey) && openHelp(d.key)) { e.preventDefault(); e.stopPropagation(); } }}>
													<SettingHelpIcon settingKey={d.key} openHelp={openHelp} />
													<div className="flex items-center gap-2 pr-6"><span className="text-sm font-semibold text-foreground">{d.label}</span>{d.restart_required && <RestartRequiredMark />}</div>
													<div className="flex items-center gap-2 mt-1.5">
														{isSensitive ? (
															<span className="text-xs text-foreground/70 font-[family-name:var(--font-mono)] truncate flex-1">{isSet ? REDACTED_VALUE : "Not set"}</span>
														) : (
															<span className="text-xs text-foreground/70 font-[family-name:var(--font-mono)] truncate flex-1">{existing.value}</span>
														)}
														<Button variant="ghost" size="sm" className="h-6 w-6 p-0" onClick={() => { setEditingKey(d.key); setEditingValue(isSensitive ? "" : (existing?.value ?? "")); }}><Pencil className="h-3 w-3 text-muted-foreground" /></Button>
														{isSensitive && isSet ? (
															<Button variant="ghost" size="sm" className="h-6 w-6 p-0" onClick={() => setRevokeConfirmKey(d.key)}><Trash2 className="h-3 w-3 text-muted-foreground hover:text-destructive" /></Button>
														) : (
															<Button variant="ghost" size="sm" className="h-6 w-6 p-0" onClick={async () => { await admin.updateSetting(d.key, { value: "" }); refetch(); refetchRestartStatus(); toast.success(`Cleared ${d.label}`); }}><Trash2 className="h-3 w-3 text-muted-foreground hover:text-destructive" /></Button>
														)}
													</div>
												</div>
											);
										}
										return (
											<button key={d.key} type="button" onClick={(e) => { if ((e.ctrlKey || e.metaKey) && openHelp(d.key)) { e.preventDefault(); return; } setEditingKey(d.key); setEditingValue(""); }} className={`text-left rounded-md border-2 border-dashed border-border/80 p-3 hover:border-primary/40 hover:bg-background transition-colors relative ${helpTargetClass(d.key)}`}>
												<SettingHelpIcon settingKey={d.key} openHelp={openHelp} />
												<div className="flex items-center gap-2 pr-6"><span className="text-sm font-semibold text-foreground/60">+ {d.label}</span>{d.restart_required && <RestartRequiredMark />}</div>
											</button>
										);
									})}
									</div>
								</section>
								);
							})}

							{/* Danger Zone */}
							{settingSections.some((s) => s.danger) && (
								<section className="mt-8">
									<div className="border-t-2 border-amber-500/30 pt-6">
										<h2 className="text-sm font-semibold text-amber-600 dark:text-amber-400 flex items-center gap-2 mb-1">
											<AlertTriangle className="h-4 w-4" />
											Danger Zone
										</h2>
										<p className="text-xs text-foreground/60 mb-4">These settings can affect authentication, security, and data integrity.</p>
										<div className="space-y-4">
											{settingSections.filter((s) => s.danger).map((section) => {
												const visibleDangerSettings = section.settings.filter(
													(setting) => setting.key !== "deployment.public_registry_enabled",
												);
												if (visibleDangerSettings.length === 0) return null;
												return (
												<details key={section.title} className="group rounded-md border border-amber-500/40 bg-card">
													<summary className="flex items-center gap-2 px-4 py-3 cursor-pointer select-none hover:bg-muted/30 transition-colors">
														{sectionIcon(section)}
														<span className="text-sm font-semibold text-foreground/80 flex-1">{section.title}</span>
														{SECTION_DOCS[section.title] && (
															<button type="button" className="text-muted-foreground hover:text-primary" onClick={(e) => { e.preventDefault(); e.stopPropagation(); openHelp(undefined, section.title); }} aria-label={`Open ${section.title} documentation`}>
																<HelpCircle className="h-3.5 w-3.5" />
															</button>
														)}
														<span className="text-[10px] text-amber-600 dark:text-amber-400 font-medium">CAUTION</span>
													</summary>
													<div className="px-4 pb-4 pt-1">
														{section.description && (
															<p className="text-xs text-foreground/60 mb-3">{section.description}</p>
														)}
														<div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
															{visibleDangerSettings.map((d) => {
														if (d.key === "danger.purge_traces_insights") {
															return (
																<div key={d.key} className={`rounded-md border-2 border-destructive/40 bg-destructive/5 p-3 relative ${helpTargetClass(d.key)}`}>
																	<SettingHelpIcon settingKey={d.key} openHelp={openHelp} />
																	<span className="text-sm font-semibold text-destructive">{d.label}</span>
																	<p className="text-xs text-foreground/60 mt-1 pr-6">{d.subtitle}</p>
																	<Button variant="destructive" size="sm" className="mt-3 h-8" onClick={handlePurgeTracesInsights} disabled={purgingTracesInsights}>
																		{purgingTracesInsights ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : <Trash2 className="mr-1 h-3.5 w-3.5" />}
																		Purge traces & insights
																	</Button>
																</div>
															);
														}
														const existing = entries.find((e) => e.key === d.key);
														const isEditing = editingKey === d.key;
														if (isEditing) {
															return (
																<div key={d.key} className="rounded-md border-2 border-primary/50 bg-card p-3">
																	<div className="mb-2 flex items-center gap-2"><span className="text-sm font-semibold text-foreground">{d.label}</span>{d.restart_required && <RestartRequiredMark />}</div>
																	<div className="flex items-center gap-2">
																		{renderSettingEditor(d.key)}
																		<Button size="sm" className="h-8" onClick={handleInlineSave} disabled={saving}>{saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}</Button>
																		<Button size="sm" variant="ghost" className="h-8" onClick={() => { setEditingKey(null); setEditingValue(""); }}><X className="h-3.5 w-3.5" /></Button>
																	</div>
																</div>
															);
														}
														if (existing && existing.value) {
															const isSensitive = (existing as AdminSetting).is_sensitive || SENSITIVE_KEYS.has(d.key);
															const isSet = (existing as AdminSetting).is_set ?? !!existing.value;
															return (
																<div key={d.key} className={`rounded-md border-2 border-border bg-card p-3 relative ${helpTargetClass(d.key)}`} onClick={(e) => { if ((e.ctrlKey || e.metaKey) && openHelp(d.key)) { e.preventDefault(); e.stopPropagation(); } }}>
																	<SettingHelpIcon settingKey={d.key} openHelp={openHelp} />
																	<div className="flex items-center gap-2 pr-6"><span className="text-sm font-semibold text-foreground">{d.label}</span>{d.restart_required && <RestartRequiredMark />}</div>
																	<div className="flex items-center gap-2 mt-1.5">
																		{isSensitive ? (
																			<span className="text-xs text-foreground/70 font-[family-name:var(--font-mono)] truncate flex-1">{isSet ? REDACTED_VALUE : "Not set"}</span>
																		) : (
																			<span className="text-xs text-foreground/70 font-[family-name:var(--font-mono)] truncate flex-1">{existing.value}</span>
																		)}
																		<Button variant="ghost" size="sm" className="h-6 w-6 p-0" onClick={() => { setEditingKey(d.key); setEditingValue(isSensitive ? "" : existing.value); }}><Pencil className="h-3 w-3 text-muted-foreground" /></Button>
																		{isSensitive && isSet ? (
																			<Button variant="ghost" size="sm" className="h-6 w-6 p-0" onClick={() => setRevokeConfirmKey(d.key)}><Trash2 className="h-3 w-3 text-muted-foreground hover:text-destructive" /></Button>
																		) : (
																			<Button variant="ghost" size="sm" className="h-6 w-6 p-0" onClick={async () => { await admin.updateSetting(d.key, { value: "" }); refetch(); refetchRestartStatus(); toast.success(`Cleared ${d.label}`); }}><Trash2 className="h-3 w-3 text-muted-foreground hover:text-destructive" /></Button>
																		)}
																	</div>
																</div>
															);
														}
														return (
															<button key={d.key} type="button" onClick={(e) => { if ((e.ctrlKey || e.metaKey) && openHelp(d.key)) { e.preventDefault(); return; } setEditingKey(d.key); setEditingValue(""); }} className={`text-left rounded-md border-2 border-dashed border-border/80 p-3 hover:border-primary/40 hover:bg-background transition-colors relative ${helpTargetClass(d.key)}`}>
																<SettingHelpIcon settingKey={d.key} openHelp={openHelp} />
																<div className="flex items-center gap-2 pr-6"><span className="text-sm font-semibold text-foreground/60">+ {d.label}</span>{d.restart_required && <RestartRequiredMark />}</div>
															</button>
														);
															})}
														</div>
													</div>
												</details>
											); })}
										</div>
									</div>
								</section>
							)}
					</div>
				)}
			</div>
			<Dialog open={revokeConfirmKey !== null} onOpenChange={(open) => { if (!open) setRevokeConfirmKey(null); }}>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>Revoke secret</DialogTitle>
						<DialogDescription>
							This will permanently delete the stored value for <strong>{revokeConfirmKey}</strong>. Any features depending on this credential will stop working immediately. This cannot be undone.
						</DialogDescription>
					</DialogHeader>
					<DialogFooter>
						<Button variant="outline" onClick={() => setRevokeConfirmKey(null)}>Cancel</Button>
						<Button variant="destructive" onClick={async () => { try { await admin.revokeSetting(revokeConfirmKey!); refetch(); refetchRestartStatus(); toast.success(`Revoked ${revokeConfirmKey}`); } catch (e: unknown) { toast.error(e instanceof Error ? e.message : "Failed to revoke setting"); } finally { setRevokeConfirmKey(null); } }}>Revoke</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
		</>
	);
}
