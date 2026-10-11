// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { test, expect } from "@playwright/test";
import { loginToWebUI, API_BASE, getAccessToken } from "./helpers";

test.describe("Agent Edit Form", () => {
  let agentId: string;

  test.beforeAll(async () => {
    const token = await getAccessToken();

    // Create a test agent via API
    const res = await fetch(`${API_BASE}/api/v1/agents`, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        name: `test-edit-form-${Date.now()}`,
        version: "1.0.0",
        description: "Original description for edit form test",
        owner: "test",
        model_name: "claude-sonnet-4-20250514",
        components: [],
      }),
    });

    if (!res.ok) {
      const body = await res.text();
      throw new Error(`Failed to create test agent: ${res.status} ${body}`);
    }

    const agent = await res.json();
    agentId = agent.id;
  });

  test.afterAll(async () => {
    if (!agentId) return;
    const token = await getAccessToken();
    await fetch(`${API_BASE}/api/v1/agents/${agentId}`, {
      method: "DELETE",
      headers: { "Authorization": `Bearer ${token}` },
    });
  });

  test.beforeEach(async ({ page }) => {
    await loginToWebUI(page);
  });

  test("Edit tab renders the form with agent data pre-filled", async ({ page }) => {
    await page.goto(`/agents/${agentId}`);
    await page.waitForLoadState("networkidle");

    // Click the Edit tab
    await page.getByRole("tab", { name: "Edit" }).click();

    // The form should be visible
    await expect(page.getByLabel("Agent File Name")).toBeVisible();
    await expect(page.getByLabel("Description")).toBeVisible();

    // Agent name field should be disabled
    const nameInput = page.getByLabel("Agent File Name");
    await expect(nameInput).toBeDisabled();

    // Description should be pre-filled
    const descInput = page.getByLabel("Description");
    await expect(descInput).toHaveValue("Original description for edit form test");
  });

  test("Edit tab allows modifying description", async ({ page }) => {
    await page.goto(`/agents/${agentId}`);
    await page.waitForLoadState("networkidle");

    await page.getByRole("tab", { name: "Edit" }).click();

    const descInput = page.getByLabel("Description");
    await descInput.clear();
    await descInput.fill("Updated description");

    // Save Draft button should be enabled after changing description
    const saveDraftBtn = page.getByRole("button", { name: "Save Draft" });
    await expect(saveDraftBtn).toBeEnabled();
  });

  test("Save & Release opens version bump dialog", async ({ page }) => {
    await page.goto(`/agents/${agentId}`);
    await page.waitForLoadState("networkidle");

    await page.getByRole("tab", { name: "Edit" }).click();

    // Make a change to enable the button (form must be dirty)
    const descInput = page.getByLabel("Description");
    await descInput.fill("Updated for release test");

    // Click Save & Release
    const releaseBtn = page.getByRole("button", { name: /Save.*Release/i });
    await expect(releaseBtn).toBeEnabled();
    await releaseBtn.click();

    // Version bump dialog should appear
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByText("Release New Version")).toBeVisible();

    // Dialog should show version bump options
    await expect(page.getByText("Patch")).toBeVisible();
    await expect(page.getByText("Minor")).toBeVisible();
    await expect(page.getByText("Major")).toBeVisible();
  });

  test("Discard button resets form changes", async ({ page }) => {
    await page.goto(`/agents/${agentId}`);
    await page.waitForLoadState("networkidle");

    await page.getByRole("tab", { name: "Edit" }).click();

    // Change description
    const descInput = page.getByLabel("Description");
    await descInput.clear();
    await descInput.fill("Temporary change");

    // Discard should become enabled
    const discardBtn = page.getByRole("button", { name: "Discard" });
    await expect(discardBtn).toBeEnabled();

    // Click discard
    await discardBtn.click();

    // Confirmation dialog should appear
    await expect(page.getByText("Discard changes?")).toBeVisible();

    // Confirm discard
    await page.getByRole("button", { name: "Discard" }).last().click();

    // Description should revert
    await expect(descInput).toHaveValue("Original description for edit form test");
  });
});
