// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

/** Gate-on acceptance. This test creates and reviews releases: disposable CI stack ONLY. */
import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { API_BASE, getAccessToken, loginToWebUI } from "./helpers";

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
    await call("POST", `/review/skills/${base.listing_id}/versions/${base.version_id}/decision`, {
      action: "approve", observed_revision: submittedBase.revision,
    });
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
    await loginToWebUI(page);
    await page.goto("/review?tab=components");
    await page.getByRole("tab", { name: /components/i }).click();
    await page.locator(`[data-review-item="skill:${next.version_id}"]`).click();
    await page.getByRole("button", { name: "View full diff" }).click();
    const sheet = page.getByRole("dialog");
    await expect(sheet.getByText(/Compared to base/)).toBeVisible();
    // The CI gate-on job creates this pending version itself. A same-size altered
    // HTTP 200 preview must fail SHA-256 verification before approval is offered.
    const fileUrl = `**/api/v1/skills/${base.listing_id}/versions/${next.version_id}/files/SKILL.md`;
    let tampered = false;
    await page.route(fileUrl, async (route) => {
      const response = await route.fetch();
      const file = await response.json();
      if (response.status() !== 200 || !file.content.startsWith("---")) throw new Error("Expected candidate SKILL.md");
      tampered = true;
      await route.fulfill({ response, json: { ...file, content: `+${file.content.slice(1)}` } });
    });
    await sheet.getByRole("button", { name: "SKILL.md" }).click();
    await expect(sheet.getByText("File checksum differs from its reviewed manifest.")).toBeVisible();
    expect(tampered).toBe(true);
    await expect(sheet.getByRole("button", { name: "Approve", exact: true })).toBeDisabled();
    await page.unroute(fileUrl);
    await call("POST", `/review/skills/${base.listing_id}/versions/${next.version_id}/decision`, {
      action: "approve", observed_revision: pending.revision,
    });
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
