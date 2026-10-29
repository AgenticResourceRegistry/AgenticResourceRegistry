// SPDX-FileCopyrightText: 2026 Tanvi Reddy <tanvi.reddy330@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { test, expect } from "@playwright/test";

const EMAIL = process.env.DEMO_ADMIN_EMAIL ?? "admin@demo.example";
const PASSWORD = process.env.DEMO_ADMIN_PASSWORD ?? "admin-changeme";

/**
 * P0: Login with wrong password — error shown
 * Issue #927
 */
test("login with wrong password shows error and stays on /login", async ({ page }) => {
  await page.goto("/login");
  await page.fill("#email", EMAIL);
  await page.fill("#password", "wrong-password-123");
  await page.click('button[type="submit"]');

  // Should stay on /login
  await expect(page).toHaveURL(/\/login/, { timeout: 10_000 });

  // Error message should appear
  await expect(page.locator("span").filter({ hasText: /invalid|incorrect|wrong|failed|credentials|password/i }).first()).toBeVisible({ timeout: 10_000 });
});

/**
 * P0: Logout — redirected to /login, protected pages inaccessible
 * Issue #927
 */
test("logout redirects to /login and blocks protected pages", async ({ page }) => {
  // Login first
  await page.goto("/login");
  await page.fill("#email", EMAIL);
  await page.fill("#password", PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 15_000 });

  // Open user menu and click Sign out
  await page.locator("button").filter({ hasText: /demo admin|admin/i }).first().click();
  await page.locator("text=Sign out").click();

  // Should redirect to /login
  await expect(page).toHaveURL(/\/login/, { timeout: 10_000 });

  // Protected page should redirect back to /login
  await page.goto("/dashboard");
  await expect(page).toHaveURL(/\/login/, { timeout: 10_000 });
});

/**
 * OBS-1495: a refresh that is in flight when the session is cleared must not restore it.
 */
test("a refresh that lands after sign-out does not restore the session", async ({ page, context }) => {
  await page.goto("/login");
  await page.fill("#email", EMAIL);
  await page.fill("#password", PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 15_000 });

  // Hold the silent refresh a new tab starts (access token gone, refresh token stored).
  let release!: () => void;
  const gate = new Promise<void>((resolve) => (release = resolve));
  let refreshStarted!: () => void;
  const started = new Promise<void>((resolve) => (refreshStarted = resolve));
  await page.route("**/api/v1/auth/token/refresh", async (route) => {
    const response = await route.fetch();
    refreshStarted();
    await gate;
    await route.fulfill({ response });
  });
  await page.evaluate(() => sessionStorage.removeItem("observal_access_token"));
  await page.reload();
  await started;

  // Another tab signs out while the refresh is in flight.
  const other = await context.newPage();
  await other.goto("/login");
  await other.evaluate(() => localStorage.removeItem("observal_refresh_token"));

  release();
  await page.waitForTimeout(500);
  expect(await page.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBeNull();
  await expect(page).toHaveURL(/\/login/, { timeout: 15_000 });
});

test("public registry can be entered as a guest", async ({ page }) => {
  await page.route("**/api/v1/config/public", async (route) => {
    const response = await route.fetch();
    const config = await response.json();
    await route.fulfill({ response, json: { ...config, public_registry_enabled: true } });
  });

  await page.goto("/login");
  const guestButton = page.getByRole("button", { name: "Sign in as guest" });
  await expect(guestButton).toBeVisible();
  await guestButton.click();
  await expect(page).toHaveURL(/\/$/, { timeout: 10_000 });
});

test("logout clears account-scoped query data without a reload", async ({ page }) => {
  let myAgentsRequests = 0;
  await page.route("**/api/v1/agents/my", async (route) => {
    myAgentsRequests += 1;
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });

  const login = async () => {
    await page.goto("/login");
    await page.fill("#email", EMAIL);
    await page.fill("#password", PASSWORD);
    await page.click('button[type="submit"]');
    await page.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 15_000 });
  };

  await login();
  await page.goto("/resources/agents");
  await expect.poll(() => myAgentsRequests).toBeGreaterThan(0);
  const requestsBeforeLogout = myAgentsRequests;

  await page.locator("button").filter({ hasText: /demo admin|admin/i }).first().click();
  await page.locator("text=Sign out").click();
  await expect(page).toHaveURL(/\/login/, { timeout: 10_000 });

  await login();
  await page.goto("/resources/agents");
  await expect.poll(() => myAgentsRequests).toBeGreaterThan(requestsBeforeLogout);
});
