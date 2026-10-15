// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { expect, test } from "@playwright/test";

/** Review activity remains visible in the existing inbox after the cutover. */
test("new review notices render in the existing inbox", async ({ page }, testInfo) => {
  const id = "e2d6c522-969d-4725-81ab-ed87c17af09c";
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.addInitScript(() => {
    sessionStorage.setItem("observal_access_token", "inbox-ui-test");
    localStorage.setItem("observal_user_role", "user");
  });
  const item = {
    id,
    kind: "review_ready",
    state: "open",
    read: false,
    read_at: null,
    action_required: true,
    title: "Ready to publish: Test skill v1.0.0",
    body: null,
    subject_type: "skill",
    subject_id: id,
    subject_namespace: "tests",
    subject_slug: "test-skill",
    action_url: "/review/42",
    action_command: null,
    actor_id: null,
    team_id: null,
    payload: { review_number: 42 },
    created_at: new Date().toISOString(),
    resolved_at: null,
  };
  await page.route("**/api/v1/inbox**", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    const body = pathname.endsWith("/count")
      ? {
          unread: 1,
          action_required: 1,
          open: 1,
          done: 0,
          dismissed: 0,
          by_kind: { review_ready: 1 },
          by_subject_type: { skill: 1 },
        }
      : pathname.endsWith(`/${id}`)
        ? { ...item, history: [] }
        : { items: [item], total: 1, page: 1, page_size: 25 };
    await route.fulfill({ json: body });
  });
  await page.route("**/api/v1/auth/whoami", (route) =>
    route.fulfill({ json: { id, username: "tester", name: "Test User", role: "user" } }),
  );
  await page.goto("/inbox");
  await page.getByRole("button", { name: "Search and filter inbox" }).click();
  await expect(page.getByTestId("inbox-rail").getByText("Ready to publish")).toBeVisible();
  await expect(page.getByTestId("inbox-detail").getByText("Ready to publish: Test skill v1.0.0")).toBeVisible();
  await page.waitForTimeout(350); // Let the filter drawer finish its transition for the screenshot.
  await page.screenshot({ path: testInfo.outputPath("review-inbox-phase2.png"), fullPage: true });
});
