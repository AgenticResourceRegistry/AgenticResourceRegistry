// SPDX-FileCopyrightText: 2026 SK Akram <skcodewizard786@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { test, expect } from "@playwright/test";
import { API_BASE, getAccessToken, loginToWebUI, waitForAPI } from "./helpers";

const bannerName = "Build your Agent File in your harness";
const dismissalKey = "observal_builder_skill_banner_dismissed";

test.describe("Builder skill recommendation", () => {
  let agentId: string;
  let token: string;

  test.beforeAll(async () => {
    await waitForAPI();
    token = await getAccessToken();
    const response = await fetch(`${API_BASE}/api/v1/agents/draft`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({
        name: `banner-test-${Date.now()}`,
        description: "Builder recommendation test",
        owner: "test",
        version: "1.0.0",
        model_name: "",
        prompt: "You are a test agent.",
        components: [],
      }),
    });
    const body = await response.text();
    expect(response.ok, body).toBeTruthy();
    agentId = JSON.parse(body).id;
  });

  test.afterAll(async () => {
    if (!agentId) return;
    const response = await fetch(`${API_BASE}/api/v1/agents/${agentId}`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok).toBeTruthy();
  });

  test.beforeEach(async ({ page }) => {
    await loginToWebUI(page);
    await page.evaluate(() => {
      localStorage.removeItem("observal_builder_skill_banner_dismissed");
      localStorage.removeItem("observal_agent_draft");
    });
  });

  test("first visit offers the skill and links to its wiki guide", async ({ page }, testInfo) => {
    await page.goto("/agents/builder");
    const banner = page.getByRole("region", { name: bannerName });
    await expect(banner).toBeVisible();
    await expect(banner).toContainText("Ask your coding assistant to use the Observal skill to create an agent.");
    await expect(page.getByRole("button", { name: "Continue to behavior" })).toBeEnabled();
    await page.screenshot({ path: testInfo.outputPath("create-desktop.png"), fullPage: true });
    const link = banner.getByRole("link", { name: "How to use the skill" });
    await link.focus();
    await expect(link).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/wiki\?doc=getting-started%2Fobserval-skill\.md/);
    await expect(page.getByRole("heading", { name: "Create agents with the Observal skill", exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Ask for an agent", exact: true })).toBeVisible();
  });

  test("empty preview follows the name and description fields", async ({ page }, testInfo) => {
    await page.goto("/agents/builder");
    const preview = page.locator("aside");
    const name = page.getByPlaceholder("e.g. code-reviewer", { exact: true });
    const description = page.getByPlaceholder("e.g. Reviews pull requests for correctness and security.");

    await expect(preview.getByText("Agent Name", { exact: true })).toBeVisible();
    await expect(preview.getByText(/\/<agent-name> · v1\.0\.0/)).toBeVisible();
    await expect(preview.getByText("Add a description to preview it here.")).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("empty-preview-desktop.png"), fullPage: true });
    await name.fill("review-assistant");
    await description.fill("Reviews pull requests for security issues.");
    await expect(preview.getByText("review-assistant", { exact: true })).toBeVisible();
    await expect(preview.getByText(/\/review-assistant · v1\.0\.0/)).toBeVisible();
    await expect(preview.getByText("Reviews pull requests for security issues.")).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: testInfo.outputPath("filled-preview-mobile.png"), fullPage: true });
    await name.clear();
    await description.clear();
    await expect(preview.getByText("Agent Name", { exact: true })).toBeVisible();
    await expect(preview.getByText("Add a description to preview it here.")).toBeVisible();
  });

  test("keyboard dismissal persists across reload and edit visits without changing form state", async ({ page }, testInfo) => {
    await page.goto("/agents/builder");
    const name = page.getByPlaceholder("e.g. code-reviewer", { exact: true });
    await name.fill("banner-example");
    const steps = page.getByRole("button", { name: /Identity Name and ownership/ }).locator("..");
    const before = await steps.boundingBox();
    const dismiss = page.getByRole("button", { name: "Don’t show this again" });
    await dismiss.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("region", { name: bannerName })).toBeHidden();
    await expect(name).toHaveValue("banner-example");
    const after = await steps.boundingBox();
    expect(after?.x).toBe(before?.x);
    expect(after?.width).toBe(before?.width);
    expect(await page.evaluate((key) => localStorage.getItem(key), dismissalKey)).toBe("1");
    await page.screenshot({ path: testInfo.outputPath("dismissed-desktop.png"), fullPage: true });
    await page.reload();
    await expect(page.getByRole("button", { name: "Continue to behavior" })).toBeVisible();
    await expect(page.getByRole("region", { name: bannerName })).toBeHidden();
    await page.goto(`/agents/builder?edit=${agentId}`);
    await expect(page.getByRole("heading", { name: "Edit Agent", exact: true })).toBeVisible();
    await expect(page.getByRole("region", { name: bannerName })).toBeHidden();
  });

  for (const mode of ["edit", "draft"]) {
    test(`shown on ${mode} without blocking navigation`, async ({ page }, testInfo) => {
      await page.goto(`/agents/builder?${mode}=${agentId}`);
      await expect(page.getByRole("region", { name: bannerName })).toBeVisible();
      await expect(page.getByPlaceholder("e.g. code-reviewer", { exact: true })).toHaveValue(/banner-test-/);
      await page.screenshot({ path: testInfo.outputPath(`${mode}-desktop.png`), fullPage: true });
      await page.getByRole("button", { name: "Continue to behavior" }).click();
      await expect(page.getByRole("heading", { name: "Define how the agent should behave" })).toBeVisible();
    });
  }

  test("draft recovery and recommendation dismissal are independent on mobile", async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.evaluate(() => localStorage.setItem("observal_agent_draft", JSON.stringify({ name: "saved-example" })));
    await page.goto("/agents/builder");
    await expect(page.getByRole("region", { name: bannerName })).toBeVisible();
    await expect(page.getByText("You have an unsaved draft.")).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("create-mobile.png"), fullPage: true });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
    await page.getByRole("button", { name: "Discard", exact: true }).click();
    await expect(page.getByRole("region", { name: bannerName })).toBeVisible();
    await page.getByRole("button", { name: "Don’t show this again" }).click();
    await page.getByRole("button", { name: "Continue to behavior" }).click();
    await expect(page.getByRole("heading", { name: "Define how the agent should behave" })).toBeVisible();
  });

  test("storage failure still permits dismissal for this visit", async ({ page }) => {
    await page.addInitScript(() => {
      const original = Storage.prototype.setItem;
      Storage.prototype.setItem = function (key, value) {
        if (key === "observal_builder_skill_banner_dismissed") throw new DOMException("Storage unavailable", "QuotaExceededError");
        original.call(this, key, value);
      };
    });
    await page.goto("/agents/builder");
    await expect(page.getByRole("region", { name: bannerName })).toBeVisible();
    await page.getByRole("button", { name: "Don’t show this again" }).click();
    await expect(page.getByRole("region", { name: bannerName })).toBeHidden();
    await page.getByRole("button", { name: "Continue to behavior" }).click();
    await expect(page.getByRole("heading", { name: "Define how the agent should behave" })).toBeVisible();
  });
});
