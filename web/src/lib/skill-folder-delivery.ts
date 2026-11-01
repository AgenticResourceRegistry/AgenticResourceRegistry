// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import type { ComponentVersionSummary, SkillVersionManifest } from "@/lib/types";

/** A missing exact manifest cannot prove that older single-file delivery is safe. */
export function requiresFolderDelivery(version: ComponentVersionSummary | null, manifest?: SkillVersionManifest): boolean {
  if (!version || version.delivery_mode !== "registry_direct") return false;
  if (!manifest || manifest.version_id !== version.id) return true;
  return manifest.files.some((file) => file.path !== "SKILL.md" && (
    file.path !== `scripts/${version.script_filename}` || file.size === 0
  ));
}
