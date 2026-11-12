// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-License-Identifier: Apache-2.0
import { test, expect } from "@playwright/test";

const baseURL = process.env.INSIGHTS_RUNTIME_URL;
const reportId = process.env.INSIGHTS_RUNTIME_REPORT;
test.use({ baseURL, trace: "off", extraHTTPHeaders: { "X-Observal-CLI-Version": process.env.INSIGHTS_RUNTIME_VERSION ?? "1.17.0-dev.0" } });
test.skip(!baseURL || !reportId, "Requires a synthetic disposable localhost report");

test("owner previews explicit selections then submits a pending bundle", async ({ page, request }, testInfo) => {
  expect(["localhost", "127.0.0.1"]).toContain(new URL(baseURL!).hostname);
  const login = await request.post("/api/v1/auth/login", { data: {
    email: process.env.INSIGHTS_RUNTIME_EMAIL, password: process.env.INSIGHTS_RUNTIME_PASSWORD,
  } });
  expect(login.ok()).toBeTruthy();
  const auth = await login.json();
  await page.addInitScript(({ access, refresh, user }) => {
    sessionStorage.setItem("observal_access_token", access);
    localStorage.setItem("observal_refresh_token", refresh);
    for (const field of ["role", "name", "email", "username"]) localStorage.setItem(`observal_user_${field}`, user[field]);
  }, { access: auth.access_token, refresh: auth.refresh_token, user: auth.user });
  const report = await request.get(`/api/v1/insights/reports/${reportId}`, { headers: { Authorization: `Bearer ${auth.access_token}` } });
  expect(report.ok()).toBeTruthy();
  const data = await report.json();
  await page.route("**/preview", async route => {
    const response = await route.fetch();
    const preview = await response.json();
    if (preview.ready) preview.artifacts.at(-1).checks.push({ id: "fixture-permission-warning", name: "Permission delta", status: "warn", required: false, details: ["Fixture permission delta: inspect requested access before publication"] });
    await route.fulfill({ response, json: preview });
  });
  await page.goto(`/agents/${data.agent_id}/insights/${reportId}`);
  await page.getByRole("group", { name: "Report sections" }).getByRole("button", { name: "Show all", exact: true }).click();
  const preview = page.getByRole("button", { name: /Preview selected/ });
  await expect(preview).toBeVisible({ timeout: 15000 });
  await expect(preview).toBeDisabled();
  await expect(page.getByRole("checkbox", { name: "Select config addition 1" })).not.toBeChecked();
  await page.getByRole("checkbox", { name: "Select config addition 1" }).check();
  await page.getByRole("checkbox", { name: "Select prompt pattern 1" }).check();
  // Selection lives at page scope: collapsing Suggestions cannot lose it or
  // turn the sticky-header action back into a direct, unvalidated Apply.
  await page.getByRole("group", { name: "Report sections" }).getByRole("button", { name: "Suggestions", exact: true }).click();
  await preview.click();
  await expect(page.getByText("Exact proposed bundle", { exact: true })).toBeVisible();
  const confirm = page.getByRole("button", { name: "Confirm", exact: true });
  await expect(confirm).toBeEnabled({ timeout: 30000 });
  await expect(page.getByText("Public installation requires required reviews to publish", { exact: false })).toBeVisible();
  const artifact = page.locator("summary").filter({ hasText: /^agent: Agent version/ });
  await artifact.click();
  await expect(page.getByText("Fixture permission delta: inspect requested access before publication", { exact: true })).toBeVisible();
  await expect(page.locator("pre").filter({ hasText: "Be precise." }).first()).toBeVisible();
  for (const size of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(size);
    await page.getByText("Exact proposed bundle", { exact: true }).scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
    await page.screenshot({ path: testInfo.outputPath(`apply-preview-${size.width}.png`), fullPage: true });
  }
  const [submitted] = await Promise.all([
    page.waitForResponse(response => response.url().endsWith("/apply") && response.request().method() === "POST"),
    confirm.click(),
  ]);
  expect(submitted.status(), await submitted.text()).toBe(200);
  await expect(page.getByText("Submitted reviews", { exact: true })).toBeVisible({ timeout: 30000 });
  await expect(page.getByRole("link", { name: /Review #/ })).toHaveCount(2);
  await page.screenshot({ path: testInfo.outputPath("apply-submitted-mobile.png"), fullPage: true });
});
