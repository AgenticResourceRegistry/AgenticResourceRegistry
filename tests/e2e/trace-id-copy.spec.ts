// SPDX-FileCopyrightText: 2026 Sagar Kumar <sagar0206mahran@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { expect, test, type Page } from "@playwright/test";

const sessionId = "8c6f2b1e-5d4a-4f3b-9e2d-1a0b9c8d7e6f";
const now = new Date().toISOString();
const session = {
  session_id: sessionId,
  first_event_time: now,
  last_event_time: now,
  is_active: false,
  prompt_count: 1,
  api_request_count: 1,
  tool_result_count: 0,
  total_input_tokens: 1200,
  total_output_tokens: 340,
  model: "sonnet-4",
  service_name: "claude-code",
  platform: "Claude Code",
  user_name: "Trace Tester",
};
const prompt = { timestamp: now, event_name: "user_prompt", body: "hello", attributes: { "event.name": "user_prompt" } };

async function mockTraceApi(page: Page) {
  await page.addInitScript(() => {
    sessionStorage.setItem("observal_access_token", "trace-id-copy-test");
    localStorage.setItem("observal_user_role", "user");
  });
  await page.route("**/api/v1/**", (route) => route.fulfill({ json: {} }));
  await page.route("**/api/v1/auth/whoami", (route) =>
    route.fulfill({ json: { id: "u1", username: "tracer", name: "Trace Tester", role: "user" } }),
  );
  await page.route("**/api/v1/sessions?*", (route) => route.fulfill({ json: [session] }));
  await page.route("**/api/v1/sessions/summary", (route) =>
    route.fulfill({ json: { total_sessions: 1, today_sessions: 1 } }),
  );
  await page.route(`**/api/v1/sessions/${sessionId}`, (route) =>
    route.fulfill({ json: { session_id: sessionId, events: [prompt], traces: [], service_name: "claude-code" } }),
  );
}

async function clearClipboard(page: Page) {
  await page.evaluate(() => navigator.clipboard.writeText(""));
}

async function readClipboard(page: Page) {
  return page.evaluate(() => navigator.clipboard.readText());
}

test.beforeEach(async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await mockTraceApi(page);
});

test("trace list copies the trace ID without opening the trace", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/traces");
  const copy = page.getByRole("row").filter({ hasText: sessionId }).getByRole("button", { name: "Copy trace ID", exact: true });
  await expect(copy).toBeVisible();

  await clearClipboard(page);
  await copy.click();
  await expect(page.getByText("Trace ID copied").last()).toBeVisible();
  expect(await readClipboard(page)).toBe(sessionId);
  await expect(page).toHaveURL(/\/traces$/);

  // Enter on the button copies too, and the row's keyboard handler must not open the trace.
  await clearClipboard(page);
  await copy.focus();
  await page.keyboard.press("Enter");
  await expect.poll(() => readClipboard(page)).toBe(sessionId);
  await expect(page).toHaveURL(/\/traces$/);

  // A click just outside the small icon still lands on the button, not the row.
  await clearClipboard(page);
  const box = await copy.boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.click(box!.x - 3, box!.y + box!.height / 2);
  await expect.poll(() => readClipboard(page)).toBe(sessionId);
  await expect(page).toHaveURL(/\/traces$/);

  // The narrow cards are role="button" themselves, so they carry no nested copy button.
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("button", { name: "Copy trace ID", exact: true }).filter({ visible: true })).toHaveCount(0);
});

test("trace detail header and Session Info copy the trace ID", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`/traces/${sessionId}`);
  await expect(page.getByRole("tab", { name: "Session Info" })).toBeVisible();

  await clearClipboard(page);
  await page.locator("header").getByRole("button", { name: "Copy trace ID", exact: true }).click();
  await expect(page.getByText("Trace ID copied").last()).toBeVisible();
  expect(await readClipboard(page)).toBe(sessionId);

  await page.getByRole("tab", { name: "Session Info" }).click();
  const details = page.getByRole("tabpanel", { name: "Session Info" });
  await expect(details.getByText(sessionId)).toBeVisible();
  await clearClipboard(page);
  await details.getByRole("button", { name: "Copy trace ID", exact: true }).click();
  await expect.poll(() => readClipboard(page)).toBe(sessionId);
});

test("copy shows an error toast when the clipboard is unavailable", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", { value: undefined });
    document.execCommand = () => false;
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`/traces/${sessionId}`);
  await page.locator("header").getByRole("button", { name: "Copy trace ID", exact: true }).click();
  await expect(page.getByText("Could not copy the trace ID").last()).toBeVisible();
});
