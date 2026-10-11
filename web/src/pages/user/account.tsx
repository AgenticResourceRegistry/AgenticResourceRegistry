// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-FileCopyrightText: 2026 Tanvi Reddy <reddyplayer22@gmail.com>
// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0


import { useState, useCallback, useSyncExternalStore } from "react";
import { useTheme } from "@/lib/theme";
import { AlertTriangle, Check, Loader2 } from "lucide-react";
import { toast } from "sonner";
import {
	getUserName,
	getUserEmail,
	getUserRole,
	getUserUsername,
	getUserAvatar,
	setUserUsername,
	setTokens,
	auth,
} from "@/lib/api";
import { ROLE_LABELS, type Role } from "@/hooks/use-role-guard";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/layouts/page-header";
import { AvatarEditable } from "@/components/account/avatar-upload";
import { NAMESPACE_RULE_TEXT, isValidNamespace } from "@/lib/registry-name";

// ── Theme definitions ──────────────────────────────────────────────────────
// ── Appearance presets (primary) ───────────────────────────────────────────
// Swatches: [canvas, accent, text] per mode, taken from the design mockup's
// preset definitions so each chip previews the mode it will apply.
const PRESET_OPTIONS = [
	{
		value: "monochrome",
		label: "Monochrome",
		light: ["oklch(0.967 0.007 88.6)", "oklch(0.222 0.006 91.6)", "oklch(0.531 0.011 93.7)"],
		dark: ["oklch(0.16 0.004 264)", "oklch(0.898 0.01 87.5)", "oklch(0.56 0.005 264)"],
	},
	{
		value: "slate",
		label: "Slate",
		light: ["oklch(0.972 0.007 259.5)", "oklch(0.54 0.124 256.5)", "oklch(0.542 0.033 249.6)"],
		dark: ["oklch(0.181 0.017 269.7)", "oklch(0.669 0.055 249.4)", "oklch(0.549 0.032 250.5)"],
	},
	{
		value: "copper",
		label: "Copper",
		light: ["oklch(0.972 0.01 87.5)", "oklch(0.545 0.075 78.4)", "oklch(0.535 0.02 84.6)"],
		dark: ["oklch(0.183 0.01 62.4)", "oklch(0.708 0.062 71.9)", "oklch(0.573 0.025 79.6)"],
	},
] as const;

const MODE_OPTIONS = [
	{ value: "light", label: "Light" },
	{ value: "dark", label: "Dark" },
	{ value: "system", label: "System" },
] as const;

// ── localStorage sync helpers ──────────────────────────────────────────────
function subscribe(cb: () => void) {
	window.addEventListener("storage", cb);
	return () => window.removeEventListener("storage", cb);
}

function getNameSnapshot() {
	if (typeof window === "undefined") return "";
	return getUserName() ?? "";
}

function getEmailSnapshot() {
	if (typeof window === "undefined") return "";
	return getUserEmail() ?? "";
}

function getRoleSnapshot() {
	if (typeof window === "undefined") return "";
	return getUserRole() ?? "";
}

function getServerSnapshot() {
	return "";
}

function initials(name: string) {
	return name
		.split(" ")
		.map((w) => w[0])
		.join("")
		.toUpperCase()
		.slice(0, 2);
}

// ── Change Username ───────────────────────────────────────────────────────
function ChangeUsernameSection() {
	const [newUsername, setNewUsername] = useState("");
	const [saving, setSaving] = useState(false);
	const currentUsername = useSyncExternalStore(
		(cb) => {
			window.addEventListener("storage", cb);
			return () => window.removeEventListener("storage", cb);
		},
		() => getUserUsername() ?? "",
		() => "",
	);

	// A username that is not a valid namespace predates namespace validation.
	// Publishing rejects it, so surface why rather than letting them find out
	// at submit time.
	const namespaceInvalid = !!currentUsername && !isValidNamespace(currentUsername);

	const handleSubmit = useCallback(async () => {
		if (!newUsername.trim()) {
			toast.error("Username cannot be empty");
			return;
		}
		if (newUsername.length < 3) {
			toast.error("Username must be at least 3 characters");
			return;
		}
		if (newUsername.length > 32) {
			toast.error("Username must be at most 32 characters");
			return;
		}
		if (!isValidNamespace(newUsername)) {
			toast.error(NAMESPACE_RULE_TEXT);
			return;
		}
		if (newUsername === currentUsername) {
			toast.error("New username is the same as current username");
			return;
		}

		setSaving(true);
		try {
			const res = await fetch("/api/v1/auth/profile/username", {
				method: "PUT",
				headers: {
					"Content-Type": "application/json",
					Authorization: `Bearer ${sessionStorage.getItem("observal_access_token")}`,
				},
				body: JSON.stringify({ username: newUsername }),
			});
			if (!res.ok) {
				const err = await res.json();
				throw new Error(err.detail || "Failed to update username");
			}
			const data = await res.json();
			setUserUsername(data.username);
			toast.success("Username updated successfully");
			setNewUsername("");
		} catch (e) {
			toast.error(e instanceof Error ? e.message : "Failed to update username");
		} finally {
			setSaving(false);
		}
	}, [newUsername, currentUsername]);

	return (
		<section className="animate-in stagger-0">
			<h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">
				Change Username
			</h3>
			<Card>
				<CardContent className="p-4 space-y-3">
					{namespaceInvalid && (
						<div className="flex items-start gap-2 rounded-md border border-dark-yellow/30 bg-light-yellow px-3 py-2 text-dark-yellow">
							<AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
							<p className="text-xs">
								<span className="font-medium">@{currentUsername}</span> cannot be used as a
								registry namespace, so publishing agents and components is blocked. Choose a
								valid username below — anything you already published moves with you.
							</p>
						</div>
					)}
					<div>
						<label className="text-xs text-muted-foreground mb-1 block">
							Current Username
						</label>
						<Input
							type="text"
							value={currentUsername || "—"}
							disabled
							className="h-8 text-sm bg-muted"
						/>
					</div>
					<div>
						<label className="text-xs text-muted-foreground mb-1 block">
							New Username
						</label>
						<Input
							type="text"
							value={newUsername}
							onChange={(e) => setNewUsername(e.target.value.toLowerCase())}
							className="h-8 text-sm"
							placeholder="3-32 chars, lowercase alphanumeric + hyphens/dots"
							onKeyDown={(e) => {
								if (e.key === "Enter") handleSubmit();
							}}
						/>
						<p className="text-xs text-muted-foreground mt-1.5">{NAMESPACE_RULE_TEXT}.</p>
					</div>
					<Button
						size="sm"
						className="h-8"
						onClick={handleSubmit}
						disabled={
							saving || !newUsername.trim() || newUsername === currentUsername
						}
					>
						{saving ? (
							<Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
						) : null}
						Update Username
					</Button>
				</CardContent>
			</Card>
		</section>
	);
}

// ── Password strength helpers ────────────────────────────────────────────
const PASSWORD_RULES = [
	{
		id: "len",
		label: "At least 8 characters",
		test: (p: string) => p.length >= 8,
	},
	{
		id: "upper",
		label: "One uppercase letter",
		test: (p: string) => /[A-Z]/.test(p),
	},
	{ id: "digit", label: "One number", test: (p: string) => /[0-9]/.test(p) },
	{
		id: "special",
		label: "One special character",
		test: (p: string) => /[^A-Za-z0-9]/.test(p),
	},
];

function passwordIsStrong(p: string) {
	return PASSWORD_RULES.every((r) => r.test(p));
}

// ── Change Password ───────────────────────────────────────────────────────
function ChangePasswordSection() {
	const [currentPassword, setCurrentPassword] = useState("");
	const [newPassword, setNewPassword] = useState("");
	const [confirmPassword, setConfirmPassword] = useState("");
	const [saving, setSaving] = useState(false);
	const [touched, setTouched] = useState(false);

	const strong = passwordIsStrong(newPassword);
	const matches = newPassword === confirmPassword;
	const canSubmit = currentPassword && strong && matches && confirmPassword;

	const handleSubmit = useCallback(async () => {
		if (!strong) {
			toast.error("Password does not meet the requirements");
			return;
		}
		if (!matches) {
			toast.error("Passwords do not match");
			return;
		}
		setSaving(true);
		try {
			// Changing the password signs out every session; keep this one on the fresh pair it hands back.
			const { exchange_code } = await auth.changePassword({
				current_password: currentPassword,
				new_password: newPassword,
			});
			const session = await auth.exchangeCode({ code: exchange_code });
			setTokens(session.access_token, session.refresh_token);
			toast.success("Password changed successfully");
			setCurrentPassword("");
			setNewPassword("");
			setConfirmPassword("");
			setTouched(false);
		} catch (e) {
			toast.error(e instanceof Error ? e.message : "Failed to change password");
		} finally {
			setSaving(false);
		}
	}, [currentPassword, newPassword, confirmPassword, strong, matches]);

	return (
		<section className="animate-in stagger-1">
			<h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">
				Change Password
			</h3>
			<Card>
				<CardContent className="p-4 space-y-3">
					<div>
						<label className="text-xs text-muted-foreground mb-1 block">
							Current Password
						</label>
						<Input
							type="password"
							value={currentPassword}
							onChange={(e) => setCurrentPassword(e.target.value)}
							className="h-8 text-sm"
							placeholder="Enter current password"
						/>
					</div>
					<div>
						<label className="text-xs text-muted-foreground mb-1 block">
							New Password
						</label>
						<Input
							type="password"
							value={newPassword}
							onChange={(e) => {
								setNewPassword(e.target.value);
								setTouched(true);
							}}
							className={`h-8 text-sm ${
								touched && newPassword
									? strong
										? "border-success focus-visible:ring-success"
										: "border-destructive focus-visible:ring-destructive"
									: ""
							}`}
							placeholder="At least 8 characters"
						/>
						{/* Requirements checklist — shown once user starts typing */}
						{touched && newPassword && (
							<ul className="mt-2 space-y-1">
								{PASSWORD_RULES.map((rule) => {
									const ok = rule.test(newPassword);
									return (
										<li
											key={rule.id}
											className={`flex items-center gap-1.5 text-xs ${
												ok
													? "text-success"
													: "text-muted-foreground"
											}`}
										>
											<span>{ok ? "✓" : "○"}</span>
											{rule.label}
										</li>
									);
								})}
							</ul>
						)}
					</div>
					<div>
						<label className="text-xs text-muted-foreground mb-1 block">
							Confirm New Password
						</label>
						<Input
							type="password"
							value={confirmPassword}
							onChange={(e) => setConfirmPassword(e.target.value)}
							className={`h-8 text-sm ${
								confirmPassword
									? matches
										? "border-success focus-visible:ring-success"
										: "border-destructive focus-visible:ring-destructive"
									: ""
							}`}
							placeholder="Re-enter new password"
							onKeyDown={(e) => {
								if (e.key === "Enter") handleSubmit();
							}}
						/>
						{confirmPassword && !matches && (
							<p className="text-xs text-destructive mt-1">
								Passwords do not match
							</p>
						)}
					</div>
					<Button
						size="sm"
						className="h-8"
						onClick={handleSubmit}
						disabled={saving || !canSubmit}
					>
						{saving ? (
							<Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
						) : null}
						Update Password
					</Button>
				</CardContent>
			</Card>
		</section>
	);
}

function getUsernameSnapshot() {
	if (typeof window === "undefined") return "";
	return getUserUsername() ?? "";
}

// ── Page ───────────────────────────────────────────────────────────────────
function getAvatarSnapshot() {
	if (typeof window === "undefined") return null;
	return getUserAvatar();
}

export default function AccountPage() {
	const name = useSyncExternalStore(
		subscribe,
		getNameSnapshot,
		getServerSnapshot,
	);
	const email = useSyncExternalStore(
		subscribe,
		getEmailSnapshot,
		getServerSnapshot,
	);
	const role = useSyncExternalStore(
		subscribe,
		getRoleSnapshot,
		getServerSnapshot,
	);
	const username = useSyncExternalStore(
		subscribe,
		getUsernameSnapshot,
		getServerSnapshot,
	);
	const avatar = useSyncExternalStore(
		subscribe,
		getAvatarSnapshot,
		() => null as string | null,
	);

	const {
		preset,
		setPreset,
		mode,
		setMode,
		resolvedMode,
		legacyTheme,
		setLegacyTheme,
	} = useTheme();

	const displayName = name || "—";
	const displayEmail = email || "—";
	const roleLabel = role ? (ROLE_LABELS[role as Role] ?? role) : "—";

	return (
		<>
			<PageHeader title="Account" />
			<div className="page-body w-full mx-auto max-w-2xl space-y-6">
				{/* ── Section 1: Profile ─────────────────────────────────────────── */}
				<section className="animate-in">
					<h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">
						Profile
					</h3>
					<Card>
						<CardContent className="p-4 space-y-3">
							<div className="flex items-center gap-4">
								<AvatarEditable name={displayName} avatarUrl={avatar} />
								<div className="min-w-0 flex-1">
									<p className="text-sm font-semibold truncate">
										{displayName}
									</p>
									<p className="text-xs text-muted-foreground truncate mt-0.5">
										{displayEmail}
									</p>
								</div>
								<Badge variant="secondary" className="shrink-0 text-xs">
									{roleLabel}
								</Badge>
							</div>
							{username && (
								<div className="flex items-center gap-2">
									<p className="text-xs text-muted-foreground">Username:</p>
									<p className="text-sm font-mono">@{username}</p>
								</div>
							)}
						</CardContent>
					</Card>
				</section>

				{/* ── Section 2: Change Username ───────────────────────────────── */}
				<ChangeUsernameSection />

				{/* ── Section 3: Change Password ───────────────────────────────── */}
				<ChangePasswordSection />

				{/* ── Section 4: Appearance ──────────────────────────────────────── */}
				<section className="animate-in stagger-1">
					<h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">
						Appearance
					</h3>

					{/* Mode: light / dark / system. System follows the OS preference. */}
					<div className="mb-4">
						<p className="text-xs text-muted-foreground mb-2">Mode</p>
						<div className="inline-flex rounded-md border border-border bg-muted p-1 gap-0.5">
							{MODE_OPTIONS.map((m) => (
								<button
									key={m.value}
									type="button"
									onClick={() => setMode(m.value)}
									aria-pressed={mode === m.value}
									className={
										"rounded px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" +
										(mode === m.value
											? " bg-background text-foreground shadow-sm"
											: " text-muted-foreground hover:text-foreground")
									}
								>
									{m.label}
								</button>
							))}
						</div>
						{mode === "system" && (
							<p className="mt-1.5 text-[11px] text-muted-foreground">
								Following your system preference ({resolvedMode}).
							</p>
						)}
					</div>

					{/* Presets: each works in both light and dark. */}
					<p className="text-xs text-muted-foreground mb-2">Preset</p>
					<div className="grid grid-cols-3 gap-2">
						{PRESET_OPTIONS.map((p) => {
							const isActive = !legacyTheme && preset === p.value;
							const swatches = resolvedMode === "dark" ? p.dark : p.light;
							return (
								<button
									key={p.value}
									type="button"
									onClick={() => setPreset(p.value)}
									className={
										"rounded-md border p-3 text-left transition-colors hover:bg-accent/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" +
										(isActive
											? " border-primary-accent bg-accent/20"
											: " border-border bg-card")
									}
								>
									<div className="rounded overflow-hidden mb-2.5 h-8 flex flex-col gap-px">
										{swatches.map((color, i) => (
											<div
												key={i}
												className="flex-1"
												style={{ backgroundColor: color }}
											/>
										))}
									</div>
									<div className="flex items-center justify-between">
										<span className="text-xs font-medium">{p.label}</span>
										{isActive && (
											<Check className="h-3 w-3 text-primary-accent" />
										)}
									</div>
								</button>
							);
						})}
					</div>

				</section>
			</div>
		</>
	);
}
