// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-License-Identifier: Apache-2.0
import { test, expect } from "@playwright/test";

// Opt-in: uses only a synthetic report in a disposable localhost stack.
const baseURL = process.env.INSIGHTS_RUNTIME_URL;
const reportId = process.env.INSIGHTS_RUNTIME_REPORT;
test.use({ baseURL, extraHTTPHeaders: { "X-Observal-CLI-Version": process.env.INSIGHTS_RUNTIME_VERSION ?? "1.17.0-dev.0" } });
test.skip(!baseURL || !reportId, "Requires a disposable runtime report");

test("partial sections are visible and failed suggestions cannot be applied", async ({ page, request }, testInfo) => {
  const url = new URL(baseURL!);
  expect(["localhost", "127.0.0.1"]).toContain(url.hostname);
  const login = await request.post("/api/v1/auth/login", {
    data: { email: process.env.INSIGHTS_RUNTIME_EMAIL, password: process.env.INSIGHTS_RUNTIME_PASSWORD },
  });
  expect(login.ok()).toBeTruthy();
  const auth = await login.json();
  await page.addInitScript(({ access, refresh, user }) => {
    sessionStorage.setItem("observal_access_token", access);
    localStorage.setItem("observal_refresh_token", refresh);
    for (const field of ["role", "name", "email", "username"]) {
      localStorage.setItem(`observal_user_${field}`, user[field]);
    }
  }, { access: auth.access_token, refresh: auth.refresh_token, user: auth.user });
  const response = await request.get(`/api/v1/insights/reports/${reportId}`, {
    headers: { Authorization: `Bearer ${auth.access_token}` },
  });
  expect(response.ok()).toBeTruthy();
  const report = await response.json();
  const pageErrors: string[] = [];
  page.on("pageerror", error => pageErrors.push(error.message));
  // Exercise malformed historical/provider diagnostics at the browser boundary;
  // the underlying disposable report is still fetched from the real API.
  await page.route(`**/insights/reports/${reportId}`, async route => {
    const response = await route.fetch();
    const body = await response.json();
    body.narrative.section_errors = [null, {}, { section: 123 }, ...body.narrative.section_errors];
    await route.fulfill({ response, json: body });
  });
  await page.goto(`/agents/${report.agent_id}/insights/${reportId}`);
  await expect(page.getByText("This report is partially available", { exact: true })).toBeVisible({ timeout: 15000 });
  const deferContact = page.getByRole("button", { name: "Not now", exact: true });
  if (await deferContact.isVisible()) await deferContact.click();
  // Preserve main's collapsed key-points UI and explicitly open its details.
  await page.getByRole("group", { name: "Report sections" }).getByRole("button", { name: "Show all", exact: true }).click();
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    await expect(page.getByText("This report is partially available", { exact: true })).toBeVisible();
    await expect(page.getByText("Suggestions unavailable", { exact: true })).toBeVisible();
    await expect(page.getByText(/unknown, unknown, unknown/)).toBeVisible();
    await expect(page.getByText("Synthetic answer delivered", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: /(?:Apply(?: suggestions)? \(\d+\)|Preview selected)/ })).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
    await page.getByText("This report is partially available", { exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath(`malformed-section-errors-${viewport.width}.png`), fullPage: true });
    await page.getByText("Suggestions unavailable", { exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath(`suggestions-unavailable-${viewport.width}.png`), fullPage: true });
  }
  expect(pageErrors).toEqual([]);
});
