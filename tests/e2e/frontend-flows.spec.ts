// SPDX-FileCopyrightText: 2025 Observal Contributors
// SPDX-License-Identifier: Apache-2.0

import { test, expect } from "@playwright/test";
import { loginToWebUI, API_BASE, getAccessToken } from "./helpers";

/**
 * Frontend E2E tests — browser-level tests that drive the registry UI
 * and verify real user flows.
 */
test.describe("Frontend Flows", () => {
  let agentName: string;

  test.beforeAll(async () => {
    const token = await getAccessToken();
    const list = await fetch(`${API_BASE}/api/v1/agents`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!list.ok) throw new Error(`List agents failed (${list.status}): ${await list.text()}`);
    const agents = await list.json();
    if (agents.length > 0) {
      agentName = agents[0].name;
      return;
    }

    // Fresh stack: create a review, then explicitly publish its submitted
    // snapshot. The removed /review/agents/{id}/approve route cannot do this.
    const login = await fetch(`${API_BASE}/api/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: process.env.DEMO_SUPER_ADMIN_EMAIL ?? "super@demo.example",
        password: process.env.DEMO_SUPER_ADMIN_PASSWORD ?? "super-changeme",
      }),
    });
    if (!login.ok) throw new Error(`Super admin login failed (${login.status}): ${await login.text()}`);
    const superToken = (await login.json()).access_token;
    const headers = { "Content-Type": "application/json", Authorization: `Bearer ${superToken}` };
    agentName = `e2e-agent-${Date.now()}`;
    const create = await fetch(`${API_BASE}/api/v1/agents`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        name: agentName,
        description: "Agent for frontend e2e tests",
        version: "1.0.0",
        owner: "admin",
        model_name: "claude-sonnet-4-20250514",
        prompt: "You are a test agent.",
      }),
    });
    if (!create.ok) throw new Error(`Create agent failed (${create.status}): ${await create.text()}`);
    const { review_number: reviewNumber } = await create.json();
    expect(reviewNumber).toBeGreaterThan(0);
    const publish = await fetch(`${API_BASE}/api/v1/reviews/${reviewNumber}/publish`, {
      method: "POST",
      headers,
      body: JSON.stringify({ override_reason: "Publish a fixture for the frontend E2E flow" }),
    });
    if (!publish.ok) throw new Error(`Publish agent failed (${publish.status}): ${await publish.text()}`);
  });

  /**
   * Flow 2: Registry home → search for an agent → open agent detail page
   */
  test("search for agent and open detail", async ({ page }) => {
    await loginToWebUI(page);
    await page.goto("/agents");
    await page.waitForLoadState("networkidle");

    // Use the search input on the agents page
    const searchInput = page.locator('input[placeholder*="Search"], input[type="search"]').first();
    await searchInput.fill(agentName);
    // Wait for results to filter (debounced)
    await page.waitForTimeout(1000);

    // Click the agent in results
    const agentLink = page.locator(`a:has-text("${agentName}")`).first();
    await expect(agentLink).toBeVisible({ timeout: 20_000 });
    await agentLink.click();

    // Should land on agent detail page
    await page.waitForURL(/\/agents\//, { timeout: 10_000 });
    await expect(page.locator("body")).toContainText(agentName);
  });

  /**
   * Flow 3: Agent detail page → copy pull command → verify copy button works
   */
  test("copy pull command on agent detail", async ({ page, context }) => {
    await loginToWebUI(page);
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);

    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto(`/agents/${agentName}`);
    await page.waitForLoadState("networkidle");

    // The install command now lives in the detail sidebar.
    const copyBtn = page.getByRole("button", { name: "Copy command" }).first();
    await expect(copyBtn).toBeVisible({ timeout: 5_000 });
    await copyBtn.click();

    // After clicking, a toast "Copied to clipboard" appears
    await expect(page.locator("text=Copied to clipboard")).toBeVisible({ timeout: 3_000 });

    // Verify clipboard contains the pull command
    const clipboardText = await page.evaluate(() => navigator.clipboard.readText());
    expect(clipboardText).toContain("observal agent pull");
    expect(clipboardText).toContain(agentName);
  });

  /**
   * Flow 4: Component browser → filter by type (MCP / skill / hook) → results update
   */
  test("component browser filter by type", async ({ page }) => {
    await loginToWebUI(page);
    await page.goto("/components");
    await page.waitForLoadState("networkidle");

    // The component page has custom tab buttons for each type
    const mcpTab = page.locator('button:has-text("MCP servers")');
    const skillsTab = page.locator('button:has-text("Skills")');
    const hooksTab = page.locator('button:has-text("Hooks")');

    await expect(mcpTab).toBeVisible();

    // MCP servers tab should be active by default (gets border-foreground)
    await expect(mcpTab).toHaveClass(/border-foreground/);

    // Click Skills tab — should become active
    await skillsTab.click();
    await page.waitForLoadState("networkidle");
    await expect(skillsTab).toHaveClass(/border-foreground/);
    await expect(mcpTab).not.toHaveClass(/border-foreground/);

    // Click Hooks tab
    await hooksTab.click();
    await page.waitForLoadState("networkidle");
    await expect(hooksTab).toHaveClass(/border-foreground/);
    await expect(skillsTab).not.toHaveClass(/border-foreground/);
  });
});
