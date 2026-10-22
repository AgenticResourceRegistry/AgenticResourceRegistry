// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

/**
 * A settings section rendered from the server schema (services/dynamic_settings.py).
 *
 * Boolean settings save as soon as they are flipped. Typed values are drafted in the
 * card and saved together from the card footer.
 */
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PickerSelect } from "@/components/ui/picker-select";
import { Switch } from "@/components/ui/switch";
import { admin } from "@/lib/api";
import type { AdminSetting, AdminSettingDef, AdminSettingSection } from "@/lib/types";
import { cn } from "@/lib/utils";
import { PURGE_KEY, SENSITIVE_KEYS, isBoolSetting, rowDomId, settingDescription } from "./settings-helpers";
import {
	AdvancedGroup,
	CardFooter,
	RestartBadge,
	SettingRow,
	SettingsCard,
	SettingsGroup,
	SwitchStateLabel,
} from "./settings-ui";

/** Fixed value sets documented in services/dynamic_settings.py. */
const SELECT_OPTIONS: Record<string, string[]> = {
	"observability.log_level": ["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
	"observability.log_format": ["json", "console"],
};

function splitHarnessList(value: string): string[] {
	return value
		.split(",")
		.map((item) => item.trim())
		.filter(Boolean);
}

function joinHarnessList(values: string[]): string {
	return Array.from(new Set(values)).join(",");
}

interface SchemaSectionProps {
	section: AdminSettingSection;
	settings: AdminSettingDef[];
	entries: AdminSetting[];
	harnesses: { name: string; display_name: string }[];
	advanced?: boolean;
	docsTitle?: string;
	onSaved: () => void;
	onRevoke: (key: string) => void;
	onPurge: () => void;
	purging: boolean;
	onDirtyChange: (sectionId: string, dirty: boolean) => void;
}

export function SchemaSection({
	section,
	settings,
	entries,
	harnesses,
	advanced,
	docsTitle,
	onSaved,
	onRevoke,
	onPurge,
	purging,
	onDirtyChange,
}: SchemaSectionProps) {
	const queryClient = useQueryClient();
	const [drafts, setDrafts] = useState<Record<string, string>>({});
	const [saving, setSaving] = useState(false);
	const [toggling, setToggling] = useState<Record<string, boolean>>({});

	const entryByKey = useMemo(() => new Map(entries.map((entry) => [entry.key, entry])), [entries]);
	const isSensitive = (key: string) => !!entryByKey.get(key)?.is_sensitive || SENSITIVE_KEYS.has(key);
	const isSet = (key: string) => {
		const entry = entryByKey.get(key);
		return entry?.is_set ?? !!entry?.value;
	};
	/** Stored value shown in the control. Write-only values are never shown. */
	const storedValue = (key: string) => (isSensitive(key) ? "" : (entryByKey.get(key)?.value ?? ""));
	const valueOf = (key: string) => drafts[key] ?? storedValue(key);

	const dirtyKeys = Object.keys(drafts).filter((key) => drafts[key] !== storedValue(key));
	const dirty = dirtyKeys.length > 0;
	useEffect(() => {
		onDirtyChange(section.id, dirty);
	}, [dirty, onDirtyChange, section.id]);
	// A section that unmounts (schema refetch, error state) must not leave a dirty flag behind.
	useEffect(() => () => onDirtyChange(section.id, false), [onDirtyChange, section.id]);

	const setDraft = (key: string, value: string) => setDrafts((current) => ({ ...current, [key]: value }));

	const toggle = async (def: AdminSettingDef, checked: boolean) => {
		setToggling((current) => ({ ...current, [def.key]: true }));
		try {
			await admin.updateSetting(def.key, { value: checked ? "true" : "false" });
			await queryClient.invalidateQueries({ queryKey: ["config", "public"] });
			onSaved();
			toast.success(`${def.label} ${checked ? "enabled" : "disabled"}`);
		} catch (error) {
			toast.error(error instanceof Error ? error.message : `Failed to update ${def.label}`);
		} finally {
			setToggling((current) => ({ ...current, [def.key]: false }));
		}
	};

	const save = async () => {
		setSaving(true);
		const saved: string[] = [];
		try {
			for (const key of dirtyKeys) {
				await admin.updateSetting(key, { value: drafts[key] });
				saved.push(key);
				toast.success(`Saved ${key}`);
			}
		} catch (error) {
			toast.error(error instanceof Error ? error.message : "Failed to save");
		} finally {
			if (saved.some((key) => key === "misc.harness_allowlist" || key === "misc.default_harness")) {
				queryClient.invalidateQueries({ queryKey: ["config", "harnesses"] });
			}
			setDrafts((current) => {
				const next = { ...current };
				for (const key of saved) delete next[key];
				return next;
			});
			if (saved.length) onSaved();
			setSaving(false);
		}
	};

	const renderRow = (def: AdminSettingDef) => {
		const id = rowDomId(def.key) + "-control";
		const badges = def.restart_required ? <RestartBadge /> : undefined;
		const description = settingDescription(def);
		const common = { settingKey: def.key, label: def.label, showKey: true, badges, description };

		if (def.key === PURGE_KEY) {
			return (
				<SettingRow key={def.key} {...common} layout="inline">
					<Button variant="destructive" size="sm" onClick={onPurge} disabled={purging}>
						{purging ? <Loader2 className="animate-spin" aria-hidden="true" /> : <Trash2 aria-hidden="true" />}
						Purge traces &amp; insights
					</Button>
				</SettingRow>
			);
		}

		if (isBoolSetting(def)) {
			const checked = (entryByKey.get(def.key)?.value || def.default) === "true";
			return (
				<SettingRow key={def.key} {...common} htmlFor={id} layout="inline">
					<SwitchStateLabel checked={checked} />
					<Switch id={id} checked={checked} disabled={toggling[def.key]} aria-label={def.label} onCheckedChange={(next) => void toggle(def, next)} />
				</SettingRow>
			);
		}

		if (def.key === "misc.harness_allowlist") {
			const selected = splitHarnessList(valueOf(def.key));
			const flip = (name: string) =>
				setDraft(def.key, joinHarnessList(selected.includes(name) ? selected.filter((item) => item !== name) : [...selected, name]));
			return (
				<SettingRow key={def.key} {...common} layout="full">
					<div className="flex w-full flex-col gap-1.5">
						<div className="flex flex-wrap gap-1.5" role="group" aria-label={def.label}>
							{harnesses.map((harness) => {
								const on = selected.includes(harness.name);
								return (
									<button
										key={harness.name}
										type="button"
										aria-pressed={on}
										onClick={() => flip(harness.name)}
										className={cn(
											"inline-flex min-h-7 items-center rounded-full border px-2.5 text-xs transition-colors",
											on
												? "border-primary bg-primary text-primary-foreground"
												: "border-input bg-background text-foreground/75 hover:border-foreground/30 hover:text-foreground",
										)}
									>
										{harness.display_name}
									</button>
								);
							})}
						</div>
						<div className="flex min-h-[30px] items-center gap-2">
							{selected.length === 0 ? (
								<span className="text-xs text-muted-foreground">All supported harnesses</span>
							) : (
								<Button type="button" variant="ghost" size="sm" onClick={() => setDraft(def.key, "")}>
									Allow all
								</Button>
							)}
						</div>
					</div>
				</SettingRow>
			);
		}

		if (def.key === "misc.default_harness") {
			return (
				<SettingRow key={def.key} {...common}>
					<PickerSelect
						id={id}
						ariaLabel={def.label}
						value={valueOf(def.key) || "__none__"}
						onValueChange={(value) => setDraft(def.key, value === "__none__" ? "" : value)}
						placeholder="Choose default harness"
						className="flex-1"
						options={[
							{ value: "__none__", label: "Use first allowed harness" },
							...harnesses.map((harness) => ({ value: harness.name, label: harness.display_name })),
						]}
					/>
				</SettingRow>
			);
		}

		if (SELECT_OPTIONS[def.key]) {
			const current = valueOf(def.key);
			const values = SELECT_OPTIONS[def.key];
			const options = [...(current && !values.includes(current) ? [current] : []), ...values];
			return (
				<SettingRow key={def.key} {...common}>
					<PickerSelect
						id={id}
						ariaLabel={def.label}
						value={current || def.default}
						onValueChange={(value) => setDraft(def.key, value === def.default && !storedValue(def.key) ? "" : value)}
						className="flex-1"
						options={options.map((value) => ({ value, label: value }))}
					/>
				</SettingRow>
			);
		}

		const sensitive = isSensitive(def.key);
		const set = sensitive && isSet(def.key);
		return (
			<SettingRow key={def.key} {...common} htmlFor={id}>
				<Input
					id={id}
					type={sensitive ? "password" : "text"}
					value={valueOf(def.key)}
					onChange={(event) => setDraft(def.key, event.target.value)}
					onKeyDown={(event) => {
						if (event.key === "Enter" && dirty) void save();
					}}
					placeholder={set ? "Set, enter a new value to replace" : def.default || "Enter value..."}
					autoComplete={sensitive ? "new-password" : "off"}
					spellCheck={false}
					className="min-h-9 flex-1 py-2 font-mono text-xs"
				/>
				{set && (
					<Button type="button" variant="ghost" size="sm" className="text-destructive hover:text-destructive" onClick={() => onRevoke(def.key)}>
						Revoke
					</Button>
				)}
			</SettingRow>
		);
	};

	const card = (
		<SettingsCard
			flush={advanced}
			footer={
				<CardFooter
					dirtyCount={dirtyKeys.length}
					saving={saving}
					onSave={() => void save()}
					onDiscard={() => setDrafts({})}
				/>
			}
		>
			{settings.map(renderRow)}
		</SettingsCard>
	);

	if (advanced) {
		return (
			<AdvancedGroup id={section.id} title={section.title} description={section.description} count={settings.length}>
				{card}
			</AdvancedGroup>
		);
	}
	return (
		<SettingsGroup
			id={section.id}
			title={section.title}
			description={section.description}
			docsTitle={docsTitle}
			caution={section.danger}
			danger={section.id === "danger"}
		>
			{card}
		</SettingsGroup>
	);
}
