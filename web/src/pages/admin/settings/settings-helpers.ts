// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

/** Non-component helpers shared by the settings page modules. */
import type { AdminSettingDef } from "@/lib/types";
import { SETTING_DESCRIPTIONS } from "./setting-descriptions";

export const slug = (value: string) => value.replace(/[^a-z0-9]+/gi, "-").toLowerCase();
export const areaDomId = (id: string) => `settings-area-${id}`;
export const sectionDomId = (id: string) => `settings-section-${id}`;
export const rowDomId = (key: string) => `setting-${slug(key)}`;

export const SENSITIVE_KEYS = new Set([
	"insights.api_key",
	"oauth.client_secret",
	"saml.idp_x509_cert",
	"saml.sp_key_encryption_password",
]);

export const PURGE_KEY = "danger.purge_traces_insights";

export function isBoolSetting(def: AdminSettingDef) {
	return def.default === "true" || def.default === "false";
}

export function settingDescription(def: AdminSettingDef) {
	return def.subtitle || SETTING_DESCRIPTIONS[def.key] || "";
}
