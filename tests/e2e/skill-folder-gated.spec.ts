// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

/** Gate-on acceptance. This test creates and reviews releases: disposable CI stack ONLY. */
import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { API_BASE, getAccessToken, loginToWebUI, publishSubject, reviewNumber } from "./helpers";

test("isolated owner successor is reviewed and its displayed version installs exactly", async ({ page }) => {
  test.skip(process.env.OBSERVAL_ISOLATED_STACK !== "true" || process.env.OBSERVAL_RUN_GATE_ON !== "true",
    "Gate-on approval requires a disposable Compose project with separate volumes");
  test.setTimeout(90_000);
  const headers = { "Content-Type": "application/json", Authorization: `Bearer ${await getAccessToken()}` };
  const call = async (method: string, path: string, body?: unknown) => {
    const response = await fetch(`${API_BASE}/api/v1${path}`, {
      method, headers, body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) throw new Error(`${method} ${path}: ${response.status} ${await response.text()}`);
    return response.json();
  };
  const setting = "/admin/settings/registry.skill_folder_delivery_enabled";
  try {
    await call("PUT", setting, { value: "true" });
    const name = `gated-folder-${randomUUID().slice(0, 8)}`;
    const skillMd = `---\nname: ${name}\ndescription: Disposable gated workflow\n---\n\n# Instructions\n`;
    const base = await call("POST", "/skills/folder-drafts", {
      name, owner: "admin", version: "1.0.0", description: "Reviewed base", task_type: "general",
      skill_md_content: skillMd, extra_files: [{ path: "templates/note.txt", content: "before\n" }],
    });
    const prefix = `/skills/${base.listing_id}/versions/`;
    const submittedBase = await call("POST", `${prefix}${base.version_id}/submit`, { observed_revision: base.revision });
    expect(submittedBase.revision).toBeTruthy();
    await publishSubject("skill", base.listing_id);
    const reviewedBase = await call("GET", `${prefix}${base.version_id}/manifest`);
    const next = await call("POST", `/skills/${base.listing_id}/drafts`, {
      base_version_id: base.version_id, observed_base_revision: reviewedBase.revision,
      version: "1.1.0", description: "Reviewed next version",
    });
    const saved = await call("PUT", `${prefix}${next.version_id}/files`, {
      observed_revision: next.revision, skill_md_content: skillMd,
      extra_files: [
        { path: "templates/note.txt", content: "after\n" },
        { path: "scripts/hello.sh", content: "#!/bin/sh\necho hi\n", executable: true },
        { path: "assets/empty.bin", content: "" },
      ],
    });
    const pending = await call("POST", `${prefix}${next.version_id}/submit`, { observed_revision: saved.revision });
    expect(pending.revision).toBeTruthy();
    await loginToWebUI(page);
    // The successor has its own review, separate from the published base's closed one.
    await page.goto(`/review/${await reviewNumber("skill", base.listing_id)}`);
    await expect(page.getByText("v1.1.0").first()).toBeVisible();
    await page.getByRole("button", { name: "Files changed" }).click();
    await expect(page.getByText("templates/note.txt").first()).toBeVisible();
    await expect(page.getByText("scripts/hello.sh").first()).toBeVisible();
    await publishSubject("skill", base.listing_id);
    await page.goto(`/components/${base.listing_id}?type=skills`);
    const install = page.locator("aside");
    await expect(install.locator("code")).toContainText("--version 1.1.0");
    await expect(page.getByRole("region", { name: "Reviewed skill files" }).getByRole("button", { name: "Select scripts/hello.sh" })).toBeVisible();
    if (process.env.OBSERVAL_1730_SCREENSHOT_DIR) {
      await page.screenshot({ path: `${process.env.OBSERVAL_1730_SCREENSHOT_DIR}/approved-successor-install.png`, fullPage: true, animations: "disabled" });
    }
  } finally {
    await call("PUT", setting, { value: "false" });
  }
});
