// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { expect, test } from "@playwright/test";

test("registration explains username rules and blocks invalid names before sending a request", async ({ page }) => {
  await page.route("**/api/v1/config/public", async (route) => {
    const response = await route.fetch();
    const config = await response.json();
    await route.fulfill({ response, json: { ...config, self_registration_enabled: true, sso_only: false } });
  });
  let registrationRequests = 0;
  let submittedUsername: string | undefined;
  await page.route("**/api/v1/auth/register", async (route) => {
    registrationRequests++;
    submittedUsername = route.request().postDataJSON().username;
    await route.fulfill({ status: 400, contentType: "application/json", body: '{"detail":"test"}' });
  });

  await page.goto("/register");
  const username = page.getByRole("textbox", { name: /username/i });
  await expect(page.getByText(/Uppercase becomes lowercase/)).toBeVisible();
  await page.getByLabel("Full name").fill("Test User");
  await page.getByLabel("Email").fill("test@example.com");
  await username.fill("test_user");
  await page.getByLabel("Password", { exact: true }).fill("ValidPass1!");
  await page.getByLabel("Confirm password").fill("ValidPass1!");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(username).toHaveAttribute("aria-invalid", "true");
  await expect(page.getByRole("alert").filter({ hasText: /valid username/ })).toBeVisible();
  expect(registrationRequests).toBe(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("alert").filter({ hasText: /valid username/ })).toBeVisible();

  await username.fill("Test-User");
  await expect(username).toHaveAttribute("aria-invalid", "false");
  await expect(page.getByRole("alert").filter({ hasText: /valid username/ })).toHaveCount(0);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect.poll(() => registrationRequests).toBe(1);
  expect(submittedUsername).toBe("test-user");
});

// Run against a disposable stack with self-registration enabled to exercise the
// actual browser, API and database rather than a mocked registration response.
test("live registration accepts a valid username", async ({ page }) => {
  test.skip(!process.env.LIVE_REGISTER, "Requires self-registration on a disposable stack");
  const suffix = Date.now().toString(36);
  const username = `signup-${suffix}`;
  await page.goto("/register");
  await page.getByLabel("Full name").fill("Signup Test");
  await page.getByLabel("Email").fill(`${username}@example.com`);
  const usernameInput = page.getByRole("textbox", { name: /username/i });
  await usernameInput.fill(`${username}_bad`);
  await page.getByLabel("Password", { exact: true }).fill("ValidPass1!");
  await page.getByLabel("Confirm password").fill("ValidPass1!");
  let registrationRequests = 0;
  page.on("request", (request) => {
    if (request.url().includes("/api/v1/auth/register")) registrationRequests++;
  });
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(usernameInput).toHaveAttribute("aria-invalid", "true");
  expect(registrationRequests).toBe(0);
  await usernameInput.fill(username.toUpperCase());
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/resources|\/$/, { timeout: 15_000 });
  expect(registrationRequests).toBe(1);
  await expect.poll(async () => page.evaluate(() => sessionStorage.getItem("observal_access_token"))).not.toBeNull();
  const response = await page.request.get("/api/v1/auth/whoami", {
    headers: { Authorization: `Bearer ${await page.evaluate(() => sessionStorage.getItem("observal_access_token"))}` },
  });
  expect(response.ok()).toBe(true);
  expect((await response.json()).username).toBe(username);
});
