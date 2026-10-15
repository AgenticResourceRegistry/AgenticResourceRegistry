// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { test, expect } from "@playwright/test";
import { getAccessToken, loginToWebUI, API_BASE } from "./helpers";

const WEB_BASE = "http://localhost:3000";

test.describe("Visibility removal smoke test", () => {
  test("Agent list page loads without visibility UI", async ({ page }) => {
    await page.goto(`${WEB_BASE}/resources/agents`);
    await page.waitForLoadState("networkidle");

    // Page should not have visibility-related UI elements
    await expect(page.locator("h3:has-text('Visibility')")).not.toBeVisible();
    await expect(page.locator("text=Team Access")).not.toBeVisible();
  });

  test("Agent detail page has no access settings widget", async ({ page }) => {
    await loginToWebUI(page);
    await page.goto(`${WEB_BASE}/resources/agents`);
    await page.waitForLoadState("networkidle");

    // Click first agent
    const firstAgent = page.locator('a[href*="/agents/"]').first();
    await firstAgent.click();
    await page.waitForLoadState("networkidle");

    // No visibility or access settings UI
    await expect(page.locator("text=Access Settings")).not.toBeVisible();
    await expect(page.locator("h3:has-text('Visibility')")).not.toBeVisible();
    await expect(page.locator("text=Team Permissions")).not.toBeVisible();
  });

  test("Agent builder has no visibility or team access section", async ({ page }) => {
    await loginToWebUI(page);
    await page.goto(`${WEB_BASE}/agents/builder`);
    await page.waitForLoadState("networkidle");

    // No visibility or team access UI
    await expect(page.locator("text=Visibility & Access")).not.toBeVisible();
    await expect(page.locator("h3:has-text('Team Access')")).not.toBeVisible();
    await expect(page.locator("select")).not.toContainText("Private (Team Access Only)");
  });

  test("API response has no visibility or team_accesses fields", async ({ request }) => {
    const res = await request.get(`${API_BASE}/api/v1/agents`);
    expect(res.ok()).toBeTruthy();
    const agents = await res.json();
    expect(agents.length).toBeGreaterThan(0);

    for (const agent of agents) {
      expect(agent).not.toHaveProperty("visibility");
      expect(agent).not.toHaveProperty("team_accesses");
    }

    // Check detail endpoint
    const token = await getAccessToken();
    const detailRes = await request.get(`${API_BASE}/api/v1/agents/${agents[0].id}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(detailRes.ok()).toBeTruthy();
    const detail = await detailRes.json();
    expect(detail).not.toHaveProperty("visibility");
    expect(detail).not.toHaveProperty("team_accesses");
  });
});
