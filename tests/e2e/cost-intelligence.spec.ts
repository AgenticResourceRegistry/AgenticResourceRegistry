// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { expect, test } from "@playwright/test";

import { loginToWebUI } from "./helpers";

test.describe("cost intelligence", () => {
  test.beforeEach(async ({ page }) => {
    await loginToWebUI(page);
  });

  test("renders every layer or an explicit empty state", async ({ page }) => {
    const payload = page.waitForResponse((r) => r.url().includes("/api/v1/exec/cost/intelligence"));
    await page.goto("/dashboard?tab=cost");
    const response = await payload;
    expect(response.status()).toBe(200);
    const data = await response.json();

    const hasSpend = (data.trend as { spend_usd: number | null }[]).some((t) => (t.spend_usd ?? 0) > 0);
    if (!hasSpend) {
      await expect(page.getByText("No priced agent usage in the last six months.")).toBeVisible();
      return;
    }
    for (const label of ["Total agent spend, month to date", "Savings identified", "Savings actioned", "Realization rate"]) {
      await expect(page.getByText(label, { exact: true })).toBeVisible();
    }
    for (const heading of [
      "Spend vs. savings found and actioned",
      "Token cost savings",
      "Most material finding",
      "Context cost",
      "Top cost drivers",
      "Savings pipeline",
      "How spend is billed",
      "Unit economics",
    ]) {
      await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible();
    }
  });

  test("the dashboard read never fails the page when telemetry is down", async ({ page }) => {
    await page.route("**/api/v1/exec/cost/intelligence**", (route) =>
      route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Cost data is temporarily unavailable" }) }),
    );
    await page.goto("/dashboard?tab=cost");
    await expect(page.getByText("Cost data could not be loaded.")).toBeVisible();
    await expect(page.getByText(/never shown as zero/)).toBeVisible();
  });

  test("driver views switch between agent, model and harness", async ({ page }) => {
    await page.goto("/dashboard?tab=cost");
    const group = page.getByRole("group", { name: "Group spend by" });
    if (!(await group.isVisible().catch(() => false))) test.skip(true, "no priced usage on this stack");
    for (const view of ["Model", "Harness", "Agent"]) {
      const button = group.getByRole("button", { name: view });
      await button.click();
      await expect(button).toHaveAttribute("aria-pressed", "true");
    }
  });
});
