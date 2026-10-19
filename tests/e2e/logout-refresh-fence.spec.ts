// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { test, expect } from "@playwright/test";

// Uses the Vite module directly so no production credentials or server are needed.
// Two pages in one context share localStorage but not sessionStorage.
// An inert same-origin document avoids unrelated app effects and remote scripts.
test.beforeEach(async ({ context }) => {
  await context.route("**/__auth_test__", (route) => route.fulfill({
    status: 200, contentType: "text/html", body: "<!doctype html><title>Auth race</title>",
  }));
});
for (const status of [200, 401, 429, 503]) test(`late refresh ${status} cannot restore either tab after logout`, async ({ context, page }) => {
  let release!: () => void;
  let entered!: () => void;
  const started = new Promise<void>((resolve) => { entered = resolve; });
  const barrier = new Promise<void>((resolve) => { release = resolve; });
  let refreshes = 0;
  await context.route("**/api/v1/config/**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: "{}" }));
  await context.route("**/api/v1/auth/token/refresh", async (route) => {
    refreshes++;
    entered();
    await barrier;
    await route.fulfill({ status, contentType: "application/json", body: JSON.stringify({
      access_token: "late-access", refresh_token: "late-refresh",
    }) });
  });
  await context.route("**/api/v1/auth/token/revoke", (route) => route.fulfill({ status: 200, body: "{}" }));
  await page.goto("/__auth_test__");
  await page.evaluate(async () => {
    const auth = await import("/src/lib/api.ts");
    await auth.commitLogin({ access_token: "old-access", refresh_token: "old-refresh",
      user: { role: "user", name: "A", email: "a@example.test" } }, auth.loginStart());
  });
  const other = await context.newPage();
  await other.goto("/__auth_test__");
  const pending = other.evaluate(async () => {
    const auth = await import("/src/lib/api.ts");
    return auth.refreshAccessTokenWithReason();
  });
  await started;
  // Sign out marks the tombstone synchronously, even though the refresh owns the lock.
  const logout = page.evaluate(async () => {
    const auth = await import("/src/lib/api.ts");
    await auth.signOut();
  });
  await expect.poll(() => other.evaluate(() => localStorage.getItem("observal_logout_intent"))).not.toBeNull();
  release();
  expect(await pending).toBe("superseded");
  await logout;
  expect(refreshes).toBe(1);
  for (const tab of [page, other]) {
    await expect.poll(() => tab.evaluate(() => sessionStorage.getItem("observal_access_token"))).toBeNull();
    expect(await tab.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBeNull();
    expect(await tab.evaluate(async () => (await import("/src/lib/api.ts")).getAccessToken())).toBeNull();
  }
});

test("fresh guest tab only clears its own access, not another tab's shared login", async ({ context, page }) => {
  await page.goto("/__auth_test__");
  await page.evaluate(async () => {
    const auth = await import("/src/lib/api.ts");
    await auth.commitLogin({ access_token: "A-access", refresh_token: "A-refresh",
      user: { role: "user", name: "A", email: "a@example.test" } }, auth.loginStart());
  });
  const guest = await context.newPage();
  await guest.goto("/__auth_test__");
  const before = await page.evaluate(() => ({ epoch: localStorage.getItem("observal_auth_epoch"),
    refresh: localStorage.getItem("observal_refresh_token") }));
  await guest.evaluate(async () => {
    sessionStorage.setItem("observal_access_token", "guest-only");
    const auth = await import("/src/lib/api.ts");
    auth.clearSession();
    await auth.signOut(); // a fresh tab cannot sign out the other tab either
  });
  expect(await guest.evaluate(() => sessionStorage.getItem("observal_access_token"))).toBeNull();
  expect(await guest.evaluate(() => localStorage.getItem("observal_logout_intent"))).toBeNull();
  expect(await page.evaluate(() => ({ epoch: localStorage.getItem("observal_auth_epoch"),
    refresh: localStorage.getItem("observal_refresh_token") }))).toEqual(before);
  expect(await page.evaluate(async () => (await import("/src/lib/api.ts")).getAccessToken())).toBe("A-access");
});

test("deferred cleanup under lock cannot erase B's newer login", async ({ context, page }) => {
  await page.goto("/__auth_test__");
  await page.evaluate(async () => {
    const auth = await import("/src/lib/api.ts");
    await auth.commitLogin({ access_token: "A-access", refresh_token: "A-refresh",
      user: { role: "user", name: "A", email: "a@example.test" } }, auth.loginStart());
  });
  const other = await context.newPage();
  await other.goto("/__auth_test__");
  // Pause A's cleanup registration, so B can commit before the old cleanup
  // acquires the lock. No storage reads/writes are mocked.
  await page.evaluate(() => {
    const locks = navigator.locks;
    const request = locks.request.bind(locks);
    let resume!: () => void;
    const gate = new Promise<void>((resolve) => { resume = resolve; });
    (window as unknown as { releaseCleanup: () => void }).releaseCleanup = resume;
    Object.defineProperty(navigator, "locks", { configurable: true, value: {
      request: (name: string, ...args: unknown[]) => {
        if (name !== "observal_auth_transition") throw new Error("unexpected lock");
        return gate.then(() => (request as (...params: unknown[]) => Promise<unknown>)(name, ...args));
      },
    } });
    const authPromise = import("/src/lib/api.ts");
    void authPromise.then((auth) => auth.clearSession());
  });
  await expect.poll(() => page.evaluate(() => localStorage.getItem("observal_logout_intent"))).toBeTruthy();
  // The fence is immediate, but a cleanup waiting on the lock has not erased A.
  expect(await page.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBe("A-refresh");
  const committed = await other.evaluate(async () => {
    const auth = await import("/src/lib/api.ts");
    return auth.commitLogin({ access_token: "B-access", refresh_token: "B-refresh",
      user: { role: "user", name: "B", email: "b@example.test" } }, auth.loginStart());
  });
  expect(committed).toBe(true);
  await page.evaluate(() => (window as unknown as { releaseCleanup: () => void }).releaseCleanup());
  // Drain the lock queue so the stale cleanup has definitely run.
  await other.evaluate(() => navigator.locks.request("observal_auth_transition", () => {}));
  expect(await other.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBe("B-refresh");
  expect(await other.evaluate(() => localStorage.getItem("observal_logout_intent"))).toBeNull();
  expect(await other.evaluate(async () => (await import("/src/lib/api.ts")).getAccessToken())).toBe("B-access");
});

test("refresh body stall times out and never retries a potentially consumed JTI", async ({ page }) => {
  await page.goto("/__auth_test__");
  await page.clock.install();
  await page.evaluate(async () => {
    const auth = await import("/src/lib/api.ts");
    await auth.commitLogin({ access_token: "old", refresh_token: "initial",
      user: { role: "user", name: "A", email: "a@example.test" } }, auth.loginStart());
    (window as unknown as { refreshCalls: number }).refreshCalls = 0;
    const original = window.fetch;
    window.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      if (!String(input).endsWith("/auth/token/refresh")) return original(input, init);
      (window as unknown as { refreshCalls: number }).refreshCalls++;
      const stream = new ReadableStream({ start(controller) {
        init?.signal?.addEventListener("abort", () => controller.error(new DOMException("Aborted", "AbortError")));
      } });
      return new Response(stream, { status: 200, headers: { "Content-Type": "application/json" } });
    }) as typeof fetch;
    (window as unknown as { refreshResult: Promise<string> }).refreshResult = auth.refreshAccessTokenWithReason();
  });
  await expect.poll(() => page.evaluate(() => (window as unknown as { refreshCalls: number }).refreshCalls)).toBe(1);
  await page.clock.runFor(8_100);
  expect(await page.evaluate(() => (window as unknown as { refreshResult: Promise<string> }).refreshResult)).toBe("reauth_required");
  expect(await page.evaluate(async () => (await import("/src/lib/api.ts")).refreshAccessTokenWithReason())).toBe("reauth_required");
  expect(await page.evaluate(() => (window as unknown as { refreshCalls: number }).refreshCalls)).toBe(1);
});

test("legacy new tab migrates under the lock and refreshes without discarding tokens", async ({ context, page }) => {
  const used: string[] = [];
  await context.route("**/api/v1/auth/token/refresh", (route) => {
    used.push(route.request().postDataJSON().refresh_token);
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      access_token: "new-access", refresh_token: "new-refresh",
    }) });
  });
  await page.goto("/__auth_test__");
  await page.evaluate(() => {
    localStorage.setItem("observal_refresh_token", "legacy-refresh");
    localStorage.setItem("observal_user_role", "admin");
    sessionStorage.setItem("observal_access_token", "legacy-access");
  });
  const other = await context.newPage();
  await other.goto("/__auth_test__");
  expect(await other.evaluate(async () => {
    const auth = await import("/src/lib/api.ts");
    const legacy = auth.hasLegacySession();
    await auth.migrateLegacySession();
    return { legacy, result: await auth.refreshAccessTokenWithReason() };
  })).toEqual({ legacy: true, result: "ok" });
  expect(used).toEqual(["legacy-refresh"]);
  expect(await other.evaluate(async () => (await import("/src/lib/api.ts")).getAccessToken())).toBe("new-access");
  expect(await page.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBe("new-refresh");
  expect(await page.evaluate(async () => (await import("/src/lib/api.ts")).getAccessToken())).toBeNull();
});

test("legacy browser startup on a protected route silently migrates", async ({ context, page }) => {
  await context.route("**/api/v1/config/**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: "{}" }));
  await context.route("**/api/v1/auth/whoami", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ email: "a@example.test", role: "user" }) }));
  await context.route("**/api/v1/auth/token/refresh", (route) => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ access_token: "migrated-access", refresh_token: "migrated-refresh" }),
  }));
  await page.goto("/__auth_test__");
  await page.evaluate(() => {
    localStorage.setItem("observal_refresh_token", "legacy-refresh");
    localStorage.setItem("observal_user_role", "user");
    localStorage.setItem("observal_user_email", "a@example.test");
  });
  const startup = await context.newPage();
  await startup.goto("/");
  await expect.poll(() => startup.evaluate(() => sessionStorage.getItem("observal_access_token"))).toBe("migrated-access");
  expect(new URL(startup.url()).pathname).not.toBe("/login");
  expect(await startup.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBe("migrated-refresh");
});

test("established tab with indeterminate refresh navigates to reauthentication", async ({ page, context }) => {
  await context.route("**/api/v1/config/**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: "{}" }));
  await context.route("**/api/v1/agents", (route) => route.fulfill({ status: 401, body: "{}" }));
  await page.goto("/__auth_test__");
  await page.evaluate(async () => {
    const auth = await import("/src/lib/api.ts");
    await auth.commitLogin({ access_token: "old", refresh_token: "initial",
      user: { role: "user", name: "A", email: "a@example.test" } }, auth.loginStart());
    localStorage.setItem("observal_refresh_pending", auth.authEpoch()!);
    void auth.registry.list("agents");
  });
  await expect(page).toHaveURL(/\/login\?reason=session_expired/);
  await expect(page.getByText("Sign in to your account")).toBeVisible();
  await page.screenshot({ path: "test-results/obs1495-reauth-login.png" });
  expect(await page.evaluate(() => localStorage.getItem("observal_logout_intent"))).toBeTruthy();
});

test("expired-access logout does not wait for refresh lock; holder revokes user-wide", async ({ context, page }) => {
  let release!: () => void;
  let entered!: () => void;
  const started = new Promise<void>((resolve) => { entered = resolve; });
  const barrier = new Promise<void>((resolve) => { release = resolve; });
  let logouts = 0;
  let revokes = 0;
  const bearer = `x.${btoa(JSON.stringify({ sub: "A", exp: Math.floor(Date.now() / 1000) + 3600 }))}.y`;
  await context.route("**/api/v1/auth/token/refresh", async (route) => {
    entered();
    await barrier;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      access_token: bearer, refresh_token: "rotated",
    }) });
  });
  await context.route("**/api/v1/auth/logout", (route) => {
    logouts++;
    expect(route.request().headers().authorization).toBe(`Bearer ${bearer}`);
    return route.fulfill({ status: 200, body: "{}" });
  });
  await context.route("**/api/v1/auth/token/revoke", (route) => { revokes++; return route.fulfill({ status: 200, body: "{}" }); });
  await page.goto("/__auth_test__");
  await page.evaluate(async () => {
    const auth = await import("/src/lib/api.ts");
    const expired = `x.${btoa(JSON.stringify({ sub: "A", exp: 1 }))}.y`;
    await auth.commitLogin({ access_token: expired, refresh_token: "initial",
      user: { role: "user", name: "A", email: "a@example.test" } }, auth.loginStart());
  });
  const other = await context.newPage();
  await other.goto("/__auth_test__");
  const pending = other.evaluate(async () => (await import("/src/lib/api.ts")).refreshAccessTokenWithReason());
  await started;
  const logout = page.evaluate(async () => (await import("/src/lib/api.ts")).signOut());
  await expect.poll(() => page.evaluate(() => localStorage.getItem("observal_logout_intent"))).toBeTruthy();
  // Completion while the other tab still owns the lock is essential.
  await logout;
  expect(logouts).toBe(0);
  release();
  expect(await pending).toBe("superseded");
  expect(logouts).toBe(1);
  expect(revokes).toBeGreaterThanOrEqual(1); // old JTI fallback cannot revoke the rotated pair
  expect(await other.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBeNull();
});

test("session expiration clears local credentials while refresh lock is stalled", async ({ context, page }) => {
  let entered!: () => void;
  let release!: () => void;
  const started = new Promise<void>((resolve) => { entered = resolve; });
  const barrier = new Promise<void>((resolve) => { release = resolve; });
  await context.route("**/api/v1/auth/token/refresh", async (route) => {
    entered(); await barrier;
    await route.fulfill({ status: 401, body: "{}" });
  });
  await page.goto("/__auth_test__");
  await page.evaluate(async () => {
    const auth = await import("/src/lib/api.ts");
    await auth.commitLogin({ access_token: "old", refresh_token: "initial",
      user: { role: "user", name: "A", email: "a@example.test" } }, auth.loginStart());
  });
  const other = await context.newPage();
  await other.goto("/__auth_test__");
  const pending = other.evaluate(async () => (await import("/src/lib/api.ts")).refreshAccessTokenWithReason());
  await started;
  await page.evaluate(async () => (await import("/src/lib/api.ts")).clearSession());
  expect(await page.evaluate(() => localStorage.getItem("observal_logout_intent"))).toBeTruthy();
  expect(await page.evaluate(async () => (await import("/src/lib/api.ts")).getAccessToken())).toBeNull();
  // The stalled lock retains the old refresh until serialized cleanup can run.
  expect(await page.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBe("initial");
  release();
  expect(await pending).toBe("superseded");
});

test("two tabs serialize rotation and an unsupported browser fails closed", async ({ context, page }) => {
  const used: string[] = [];
  await context.route("**/api/v1/config/**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: "{}" }));
  await context.route("**/api/v1/auth/token/refresh", async (route) => {
    const { refresh_token } = route.request().postDataJSON();
    used.push(refresh_token);
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      access_token: `access-${used.length}`, refresh_token: `refresh-${used.length}`,
    }) });
  });
  await page.goto("/__auth_test__");
  await page.evaluate(async () => {
    const auth = await import("/src/lib/api.ts");
    await auth.commitLogin({ access_token: "old", refresh_token: "initial",
      user: { role: "user", name: "A", email: "a@example.test" } }, auth.loginStart());
  });
  const other = await context.newPage();
  await other.goto("/__auth_test__");
  const results = await Promise.all([page, other].map((tab) => tab.evaluate(async () =>
    (await import("/src/lib/api.ts")).refreshAccessTokenWithReason(),
  )));
  expect(results).toEqual(["ok", "ok"]);
  expect(used).toEqual(["initial", "refresh-1"]);
  await other.evaluate(() => Object.defineProperty(navigator, "locks", { value: undefined, configurable: true }));
  expect(await other.evaluate(async () => (await import("/src/lib/api.ts")).getAccessToken())).toBeNull();
  expect(await other.evaluate(async () => {
    try { (await import("/src/lib/api.ts")).loginStart(); return false; }
    catch (error) { return String(error).includes("Web Locks required"); }
  })).toBe(true);
  expect(await other.evaluate(async () => {
    try {
      await (await import("/src/lib/api.ts")).commitLogin({ access_token: "bad", refresh_token: "bad" }, null);
      return false;
    } catch (error) { return String(error).includes("Web Locks required"); }
  })).toBe(true);
  await other.evaluate(async () => (await import("/src/lib/api.ts")).signOut());
  expect(await other.evaluate(() => localStorage.getItem("observal_logout_intent"))).toBeTruthy();
});

test("old login cannot commit after logout, new login survives delayed remote failure", async ({ context, page }) => {
  let reached!: () => void;
  let release!: () => void;
  const started = new Promise<void>((resolve) => { reached = resolve; });
  const barrier = new Promise<void>((resolve) => { release = resolve; });
  await context.route("**/api/v1/auth/logout", async (route) => {
    reached();
    await barrier;
    await route.fulfill({ status: 401, body: "{}" });
  });
  await context.route("**/api/v1/auth/token/revoke", (route) => route.fulfill({ status: 200, body: "{}" }));
  await page.goto("/__auth_test__");
  const oldStart = await page.evaluate(async () => {
    const auth = await import("/src/lib/api.ts");
    const token = `x.${btoa(JSON.stringify({ sub: "A", exp: Math.floor(Date.now() / 1000) + 3600 }))}.y`;
    await auth.commitLogin({ access_token: token, refresh_token: "a-refresh",
      user: { role: "user", name: "A", email: "a@example.test" } }, auth.loginStart());
    return auth.loginStart();
  });
  const logout = page.evaluate(async () => (await import("/src/lib/api.ts")).signOut());
  await started;
  const result = await page.evaluate(async (captured) => {
    const auth = await import("/src/lib/api.ts");
    const stale = await auth.commitLogin({ access_token: "stale", refresh_token: "stale-refresh" }, captured);
    const fresh = await auth.commitLogin({ access_token: "B-access", refresh_token: "B-refresh",
      user: { role: "user", name: "B", email: "b@example.test" } }, auth.loginStart());
    return { stale, fresh };
  }, oldStart);
  expect(result).toEqual({ stale: false, fresh: true });
  release();
  await logout;
  expect(await page.evaluate(async () => (await import("/src/lib/api.ts")).getAccessToken())).toBe("B-access");
  expect(await page.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBe("B-refresh");
});

test("repeated logout clicks keep tombstone and a restored tab stays signed out", async ({ context, page }) => {
  await context.route("**/api/v1/auth/token/refresh", (route) => route.fulfill({ status: 401, body: "{}" }));
  await context.route("**/api/v1/auth/token/revoke", (route) => route.fulfill({ status: 200, body: "{}" }));
  await page.goto("/__auth_test__");
  await page.evaluate(async () => {
    const auth = await import("/src/lib/api.ts");
    await auth.commitLogin({ access_token: "old", refresh_token: "initial",
      user: { role: "user", name: "A", email: "a@example.test" } }, auth.loginStart());
  });
  await page.evaluate(async () => {
    const auth = await import("/src/lib/api.ts");
    await Promise.all([auth.signOut(), auth.signOut()]);
  });
  const tombstone = await page.evaluate(() => localStorage.getItem("observal_logout_intent"));
  expect(tombstone).toBeTruthy();
  const restored = await context.newPage();
  await restored.goto("/__auth_test__");
  expect(await restored.evaluate(async () => (await import("/src/lib/api.ts")).refreshAccessTokenWithReason())).toBe("reauth_required");
  expect(await page.evaluate(() => localStorage.getItem("observal_logout_intent"))).toBe(tombstone);
  expect(await restored.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBeNull();
});

test("expired bearer uses private refresh for remote logout, then narrow revoke fallback", async ({ context, page }) => {
  let refreshes = 0;
  let logouts = 0;
  let revokes = 0;
  await context.route("**/api/v1/auth/token/refresh", (route) => {
    refreshes++;
    const bearer = `x.${btoa(JSON.stringify({ sub: "A", exp: Math.floor(Date.now() / 1000) + 3600 }))}.y`;
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      access_token: bearer, refresh_token: "rotated",
    }) });
  });
  await context.route("**/api/v1/auth/logout", (route) => {
    logouts++;
    return route.fulfill({ status: 401, body: "{}" });
  });
  await context.route("**/api/v1/auth/token/revoke", (route) => {
    revokes++;
    expect(route.request().postDataJSON().refresh_token).toBe("old-refresh");
    return route.fulfill({ status: 200, body: "{}" });
  });
  await page.goto("/__auth_test__");
  await page.evaluate(async () => {
    const auth = await import("/src/lib/api.ts");
    const expired = `x.${btoa(JSON.stringify({ sub: "A", exp: 1 }))}.y`;
    await auth.commitLogin({ access_token: expired, refresh_token: "old-refresh",
      user: { role: "user", name: "A", email: "a@example.test" } }, auth.loginStart());
    await auth.signOut();
  });
  expect({ refreshes, logouts, revokes }).toEqual({ refreshes: 1, logouts: 1, revokes: 1 });
  expect(await page.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBeNull();
});

test("delayed profile writer cannot restore cached account after logout", async ({ page, context }) => {
  await context.route("**/api/v1/auth/token/refresh", (route) => route.fulfill({ status: 401, body: "{}" }));
  await context.route("**/api/v1/auth/token/revoke", (route) => route.fulfill({ status: 200, body: "{}" }));
  await page.goto("/__auth_test__");
  const captured = await page.evaluate(async () => {
    const auth = await import("/src/lib/api.ts");
    await auth.commitLogin({ access_token: "old", refresh_token: "initial",
      user: { role: "user", name: "A", email: "a@example.test" } }, auth.loginStart());
    return { epoch: auth.authEpoch(), actor: auth.getUserEmail() };
  });
  expect(await page.evaluate(async ({ epoch, actor }) => {
    const auth = await import("/src/lib/api.ts");
    await auth.signOut();
    return auth.updateProfile(epoch, actor, () => auth.setUserAvatar("stale-avatar"));
  }, captured)).toBe(false);
  expect(await page.evaluate(() => localStorage.getItem("observal_user_avatar"))).toBeNull();
});

test("unloaded refresh holder leaves consumed JTI indeterminate", async ({ context, page }) => {
  let entered!: () => void;
  const started = new Promise<void>((resolve) => { entered = resolve; });
  let refreshes = 0;
  let release!: () => void;
  const barrier = new Promise<void>((resolve) => { release = resolve; });
  await context.route("**/api/v1/config/**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: "{}" }));
  await context.route("**/api/v1/auth/token/refresh", async (route) => {
    refreshes++;
    entered();
    // The server may already have consumed this JTI; the holder disappears.
    await barrier;
    await route.abort().catch(() => {});
  });
  await page.goto("/__auth_test__");
  await page.evaluate(async () => {
    const auth = await import("/src/lib/api.ts");
    await auth.commitLogin({ access_token: "old", refresh_token: "initial",
      user: { role: "user", name: "A", email: "a@example.test" } }, auth.loginStart());
  });
  const pending = page.evaluate(async () => (await import("/src/lib/api.ts")).refreshAccessTokenWithReason());
  void pending.catch(() => {});
  await started;
  await page.close();
  release();
  const other = await context.newPage();
  await other.goto("/__auth_test__");
  expect(await other.evaluate(async () => (await import("/src/lib/api.ts")).refreshAccessTokenWithReason())).toBe("reauth_required");
  expect(refreshes).toBe(1);
});
