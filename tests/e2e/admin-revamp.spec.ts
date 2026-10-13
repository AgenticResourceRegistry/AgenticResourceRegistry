// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { expect, test } from "@playwright/test";

import { loginToWebUI } from "./helpers";

test.describe("administration surfaces", () => {
  test.beforeEach(async ({ page }) => {
    await loginToWebUI(page);
  });

  test("dashboard tabs remain reachable", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page.getByRole("heading", { name: "AI Adoption Dashboard" })).toBeVisible();

    for (const tab of [
      "AI Adoption",
      "Cost Intelligence",
      "Investments",
      "AI Insights",
      "Departments",
      "Velocity",
    ]) {
      const trigger = page.getByRole("tab", { name: tab });
      await trigger.click();
      await expect(trigger).toHaveAttribute("data-state", "active");
    }
  });

  for (const [path, heading] of [
    ["/users", "Users"],
    ["/audit-log", "Audit Log"],
    ["/security-events", "Security Events"],
    ["/diagnostics", "Diagnostics"],
  ] as const) {
    test(`${heading} exposes its administration heading`, async ({ page }) => {
      await page.goto(path);
      await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible();
    });
  }

  test("users switch to readable cards at narrow widths", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 820 });
    await page.goto("/users");
    await expect(page.getByText("Organization users")).toBeVisible();
    await expect(page.locator("article").first()).toBeVisible();
    await expect(page.locator("table")).toBeHidden();
  });
});
