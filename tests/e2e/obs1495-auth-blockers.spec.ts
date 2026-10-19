// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { expect, test } from "@playwright/test";

const profile = { role: "user", name: "A", email: "a@example.test" };

// All exchanges are local and deterministic; no SSO provider or credentials required.
test.beforeEach(async ({ context }) => {
  await context.route("**/__auth_test__", (route) => route.fulfill({
    contentType: "text/html", body: "<!doctype html><title>Auth test</title>",
  }));
  await context.route("**/api/v1/config/public", (route) => route.fulfill({
    contentType: "application/json", body: JSON.stringify({ public_registry_enabled: true }),
  }));
});

test("indeterminate refresh lets a fresh public tab browse but a protected tab requests sign-in", async ({ context, page }) => {
  let refreshes = 0;
  await context.route("**/api/v1/auth/token/refresh", (route) => {
    refreshes++;
    return route.fulfill({ status: 500, body: "{}" });
  });
  await page.goto("/__auth_test__");
  await page.evaluate(async () => {
    const auth = await import("/src/lib/api.ts");
    await auth.commitLogin({ access_token: "old", refresh_token: "uncertain", user: { role: "user", name: "A", email: "a@example.test" } }, auth.loginStart());
    localStorage.setItem("observal_refresh_pending", auth.authEpoch()!);
  });
  const publicTab = await context.newPage();
  await publicTab.goto("/resources/agents");
  await expect(publicTab.getByRole("heading", { name: "Agentic Resources" })).toBeVisible({ timeout: 15_000 });
  await expect(publicTab).toHaveURL(/\/resources\/agents/);
  await publicTab.screenshot({ path: "test-results/obs1495-public-anonymous.png" });
  const protectedTab = await context.newPage();
  await protectedTab.goto("/account");
  await expect(protectedTab).toHaveURL(/\/login\?next=/);
  await expect(protectedTab.getByText("Sign in or browse the public registry")).toBeVisible();
  await protectedTab.screenshot({ path: "test-results/obs1495-protected-reauth.png" });
  expect(refreshes).toBe(0);
});

test("unknown rotation outcome settles a fresh public tab anonymously without retrying the JTI", async ({ context, page }) => {
  let refreshes = 0;
  await context.route("**/api/v1/auth/token/refresh", (route) => {
    refreshes++;
    return route.fulfill({ status: 503, body: "{}" });
  });
  await page.goto("/__auth_test__");
  await page.evaluate(async (user) => {
    const auth = await import("/src/lib/api.ts");
    await auth.commitLogin({ access_token: "old", refresh_token: "uncertain", user }, auth.loginStart());
  }, profile);
  const publicTab = await context.newPage();
  await publicTab.goto("/resources/agents");
  await expect(publicTab.getByRole("heading", { name: "Agentic Resources" })).toBeVisible({ timeout: 15_000 });
  expect(await publicTab.evaluate(() => localStorage.getItem("observal_refresh_pending"))).toBeTruthy();
  expect(refreshes).toBe(1);
});

for (const callback of ["code", "saml_code", "saml_token"] as const) {
  test(`${callback} callback exchanges once under StrictMode and completes full-page return`, async ({ page, context }) => {
    const path = callback === "saml_token" ? "**/api/v1/sso/saml/exchange?token_id=once" : "**/api/v1/auth/exchange";
    let exchanges = 0;
    await context.route(path, (route) => {
      exchanges++;
      return route.fulfill({ contentType: "application/json", status: exchanges === 1 ? 200 : 400,
        body: JSON.stringify({ access_token: "accepted", refresh_token: "accepted-refresh", user: profile }),
      });
    });
    await page.goto("/__auth_test__");
    await page.evaluate(() => sessionStorage.setItem("observal_sso_start", ""));
    await page.goto(`/login?${callback}=once&next=%2Fresources%2Fagents`);
    await expect(page).toHaveURL(/\/resources\/agents/);
    await expect(page.getByRole("heading", { name: "Agentic Resources" })).toBeVisible({ timeout: 15_000 });
    expect(exchanges).toBe(1);
    expect(await page.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBe("accepted-refresh");
  });
}

test("callback without a start marker never consumes a one-time code", async ({ page, context }) => {
  let exchanges = 0;
  await context.route("**/api/v1/auth/exchange", (route) => {
    exchanges++;
    return route.fulfill({ status: 400, body: "{}" });
  });
  await page.goto("/login?code=unclaimed");
  await expect(page.getByText("Sign-in was cancelled. Please try again.")).toBeVisible();
  expect(exchanges).toBe(0);
});

test("unsupported owner logout clears shared credentials and attempts remote revoke", async ({ page, context }) => {
  const revokes: string[] = [];
  await context.route("**/api/v1/auth/token/revoke", (route) => {
    revokes.push(route.request().postDataJSON().refresh_token);
    return route.fulfill({ status: 200, body: "{}" });
  });
  await page.goto("/__auth_test__");
  await page.evaluate(async (user) => {
    const auth = await import("/src/lib/api.ts");
    await auth.commitLogin({ access_token: "expired", refresh_token: "owned-refresh", user }, auth.loginStart());
    Object.defineProperty(navigator, "locks", { configurable: true, value: undefined });
    await auth.signOut();
  }, profile);
  expect(revokes).toEqual(["owned-refresh"]);
  expect(await page.evaluate(() => ({ refresh: localStorage.getItem("observal_refresh_token"),
    role: localStorage.getItem("observal_user_role"), email: localStorage.getItem("observal_user_email"),
    intent: localStorage.getItem("observal_logout_intent") }))).toMatchObject({ refresh: null, role: null, email: null, intent: expect.any(String) });
});

test("unsupported owner attempts account logout without erasing a later login", async ({ context, page }) => {
  let entered!: () => void;
  let release!: () => void;
  const started = new Promise<void>((resolve) => { entered = resolve; });
  const barrier = new Promise<void>((resolve) => { release = resolve; });
  let bearerSeen = "";
  await context.route("**/api/v1/auth/logout", async (route) => {
    bearerSeen = route.request().headers().authorization;
    entered();
    await barrier;
    await route.fulfill({ status: 401, body: "{}" });
  });
  await context.route("**/api/v1/auth/token/revoke", (route) => route.fulfill({ status: 200, body: "{}" }));
  await page.goto("/__auth_test__");
  const logout = page.evaluate(async (user) => {
    const auth = await import("/src/lib/api.ts");
    const access = `x.${btoa(JSON.stringify({ sub: "A", exp: Math.floor(Date.now() / 1000) + 3600 }))}.y`;
    await auth.commitLogin({ access_token: access, refresh_token: "old-refresh", user }, auth.loginStart());
    Object.defineProperty(navigator, "locks", { configurable: true, value: undefined });
    await auth.signOut();
    return access;
  }, profile);
  await started;
  expect(await page.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBeNull();
  const other = await context.newPage();
  await other.goto("/__auth_test__");
  await other.evaluate(async (user) => {
    const auth = await import("/src/lib/api.ts");
    await auth.commitLogin({ access_token: "new-access", refresh_token: "new-refresh", user }, auth.loginStart());
  }, { role: "user", name: "B", email: "b@example.test" });
  release();
  expect(bearerSeen).toBe(`Bearer ${await logout}`);
  expect(await other.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBe("new-refresh");
});

test("unsupported guest cannot erase another tab's shared refresh", async ({ context, page }) => {
  await page.goto("/__auth_test__");
  await page.evaluate(async (user) => {
    const auth = await import("/src/lib/api.ts");
    await auth.commitLogin({ access_token: "old", refresh_token: "other-refresh", user }, auth.loginStart());
  }, profile);
  const guest = await context.newPage();
  await guest.goto("/__auth_test__");
  await guest.evaluate(async () => {
    Object.defineProperty(navigator, "locks", { configurable: true, value: undefined });
    await (await import("/src/lib/api.ts")).signOut();
  });
  expect(await page.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBe("other-refresh");
  expect(await page.evaluate(() => localStorage.getItem("observal_logout_intent"))).toBeNull();
});
