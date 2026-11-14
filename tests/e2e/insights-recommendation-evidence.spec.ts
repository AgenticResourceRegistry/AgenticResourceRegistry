// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-License-Identifier: Apache-2.0
import { test, expect } from "@playwright/test";
const baseURL = process.env.INSIGHTS_RUNTIME_URL;
const reportId = process.env.INSIGHTS_RUNTIME_REPORT;
test.use({ baseURL, trace: "off", extraHTTPHeaders: { "X-Observal-CLI-Version": process.env.INSIGHTS_RUNTIME_VERSION ?? "1.18.0-dev.0" } });
test.skip(!baseURL || !reportId, "Requires disposable integration report");
test("only grounded changes are shown with auditable references", async ({ page, request }, info) => {
  expect(["localhost", "127.0.0.1"]).toContain(new URL(baseURL!).hostname);
  const login = await request.post("/api/v1/auth/login", { data: { email: process.env.INSIGHTS_RUNTIME_EMAIL, password: process.env.INSIGHTS_RUNTIME_PASSWORD } });
  expect(login.ok()).toBeTruthy(); const auth = await login.json();
  await page.addInitScript(({ access, refresh, user }) => {
    sessionStorage.setItem("observal_access_token", access); localStorage.setItem("observal_refresh_token", refresh);
    for (const field of ["role", "name", "email", "username"]) localStorage.setItem(`observal_user_${field}`, user[field]);
  }, { access: auth.access_token, refresh: auth.refresh_token, user: auth.user });
  const response = await request.get(`/api/v1/insights/reports/${reportId}`, { headers: { Authorization: `Bearer ${auth.access_token}` } });
  expect(response.ok()).toBeTruthy(); const report = await response.json();
  await page.goto(`/agents/${report.agent_id}/insights/${reportId}`);
  await expect(page.getByText("Recommendation evidence", { exact: true })).toBeVisible({ timeout: 15000 });
  await page.getByRole("group", { name: "Report sections" }).getByRole("button", { name: "Show all", exact: true }).click();
  await expect(page.getByText(/1 in-scope sources eligible for changes/)).toBeVisible();
  await expect(page.getByText(/linear: 1/)).toBeVisible();
  await expect(page.getByText("Scoped candidate rule", { exact: true })).toBeVisible();
  await expect(page.getByText("DELETECACHESRULE", { exact: false })).toHaveCount(0);
  await expect(page.getByText(/Sources: s_/)).toBeVisible();
  await page.getByText("Source details", { exact: true }).click();
  await expect(page.getByText(/out_of_scope \(model-assessed\)/)).toBeVisible();
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    await page.getByText("Recommendation evidence", { exact: true }).scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
    await page.screenshot({ path: info.outputPath(`recommendation-evidence-${viewport.width}.png`), fullPage: true });
  }
});
