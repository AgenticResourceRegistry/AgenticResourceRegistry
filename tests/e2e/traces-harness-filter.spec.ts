// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { expect, test } from "@playwright/test";
import { API_BASE, getAccessToken, loginToWebUI } from "./helpers";

test("Traces harness filter uses ids and retains options after selection", async ({ page }) => {
  const token = await getAccessToken();
  const headers = { "Content-Type": "application/json", Authorization: `Bearer ${token}` };
  const suffix = crypto.randomUUID();
  for (const [harness, line] of [
    ["kiro", { kind: "Prompt", data: { meta: { timestamp: Math.floor(Date.now() / 1000) }, content: [{ kind: "text", data: `kiro-${suffix}` }] } }],
    ["claude-code", { type: "user", timestamp: new Date().toISOString(), message: { role: "user", content: `claude-${suffix}` } }],
  ] as const) {
    const response = await fetch(`${API_BASE}/api/v1/ingest/session`, {
      method: "POST", headers,
      body: JSON.stringify({ session_id: `${harness}-${suffix}`, harness, lines: [JSON.stringify(line)] }),
    });
    expect(response.ok, await response.text()).toBe(true);
  }

  await loginToWebUI(page);
  await page.goto("/traces");
  const filter = page.getByRole("combobox", { name: "Filter by harness" });
  await expect(filter).toBeVisible();
  await filter.click();
  await page.getByRole("option", { name: "Kiro" }).click();
  await expect(page.getByRole("row").filter({ hasText: `kiro-${suffix}` })).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: `claude-code-${suffix}` })).toHaveCount(0);
  await expect(page.getByRole("searchbox", { name: "Search traces" })).toHaveValue("platform:kiro");

  // The selected filter must not erase the other harnesses from the menu.
  await filter.click();
  await expect(page.getByRole("option", { name: "Claude Code" })).toBeVisible();

  // Existing bookmarked display-name filters keep the canonical menu label
  // without adding a duplicate Kiro option alongside the registry entry.
  await page.goto("/traces?search=platform%3AKiro");
  await expect(page.getByRole("row").filter({ hasText: `kiro-${suffix}` })).toBeVisible();
  await expect(filter).toHaveText("Kiro");
  await filter.click();
  await expect(page.getByRole("option", { name: "Kiro", exact: true })).toHaveCount(1);
});
