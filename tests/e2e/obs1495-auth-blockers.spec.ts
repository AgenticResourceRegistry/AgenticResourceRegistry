// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

const profile = { role: "user", name: "A", email: "a@example.test" };
const jwt = (sub: string) => `x.${btoa(JSON.stringify({ sub }))}.y`;

async function mockPublicAgentPage(context: BrowserContext) {
  // Keep route rendering independent of a running API; tests still assert the
  // actual registry heading and the guest/authenticated session behavior.
  for (const [path, body] of [
    ["config/harnesses", { harnesses: [], default_harness: null }],
    ["config/version", { server_version: "dev" }],
    ["teams", []],
    ["inbox/count", { unread: 0 }],
    ["agents/my", []],
    ["agents/deleted", []],
  ] as const) {
    await context.route(`**/api/v1/${path}`, (route) => route.fulfill({ json: body }));
  }
  await context.route(/\/api\/v1\/agents(?:\?.*)?$/, (route) => route.fulfill({ json: [] }));
}

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
    await mockPublicAgentPage(context);
    await page.goto("/__auth_test__");
    await page.evaluate(() => sessionStorage.setItem("observal_sso_start", ""));
    await page.goto(`/login?${callback}=once&next=%2Fresources%2Fagents`);
    await expect(page).toHaveURL(/\/resources\/agents/);
    await expect(page.getByRole("heading", { name: "Agentic Resources" })).toBeVisible({ timeout: 15_000 });
    expect(exchanges).toBe(1);
    expect(await page.evaluate(() => ({
      access: sessionStorage.getItem("observal_access_token"),
      refresh: localStorage.getItem("observal_refresh_token"),
    }))).toEqual({ access: "accepted", refresh: "accepted-refresh" });
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

for (const [label, subject, symbol] of [["hyphen", "a࠾z", "-"], ["underscore", "aa¿z", "_"]] as const) {
test(`base64url JWT ${label} subject and expiry allow account-wide logout`, async ({ page, context }) => {
  const logouts: string[] = [];
  await context.route("**/api/v1/auth/logout", (route) => {
    logouts.push(route.request().headers().authorization);
    return route.fulfill({ status: 200, body: "{}" });
  });
  await page.goto("/__auth_test__");
  const token = await page.evaluate(async ({ user, subject }) => {
    const auth = await import("/src/lib/api.ts");
    const bytes = new TextEncoder().encode(JSON.stringify({ sub: subject, exp: Math.floor(Date.now() / 1000) + 3600 }));
    const access = `x.${btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "")}.y`;
    await auth.commitLogin({ access_token: access, refresh_token: "old-refresh", user }, auth.loginStart());
    await auth.signOut();
    return access;
  }, { user: profile, subject });
  expect(token.split(".")[1]).toContain(symbol);
  expect(logouts).toEqual([`Bearer ${token}`]);
});
}

for (const [status, retry] of [[204, false], [200, false], [204, true], [200, true]] as const) {
  test(`request rejects superseded ${status} ${retry ? "401 retry" : "initial"} response`, async ({ page, context }) => {
    let entered!: () => void;
    let release!: () => void;
    const waiting = new Promise<void>((resolve) => { entered = resolve; });
    const barrier = new Promise<void>((resolve) => { release = resolve; });
    await context.route("**/api/v1/auth/token/refresh", (route) => route.fulfill({ json: { access_token: "A-new", refresh_token: "A-rotated" } }));
    let requests = 0;
    await context.route("**/api/v1/overview/stats", async (route) => {
      requests++;
      if (retry && requests === 1) return route.fulfill({ status: 401 });
      entered();
      await barrier;
      await route.fulfill(status === 204 ? { status: 204 } : { json: profile });
    });
    await page.goto("/__auth_test__");
    await page.evaluate(async (user) => {
      const auth = await import("/src/lib/api.ts");
      await auth.commitLogin({ access_token: "A", refresh_token: "A-refresh", user }, auth.loginStart());
      (window as unknown as { pending: Promise<unknown> }).pending = auth.dashboard.stats()
        .then(() => "success", (err: Error) => err.message);
    }, profile);
    await waiting;
    await page.evaluate(async () => {
      const auth = await import("/src/lib/api.ts");
      await auth.commitLogin({ access_token: "B", refresh_token: "B-refresh",
        user: { role: "user", name: "B", email: "b@example.test" } }, auth.loginStart());
    });
    release();
    expect(await page.evaluate(() => (window as unknown as { pending: Promise<string> }).pending)).toBe("Session changed");
  });
}

for (const retry of [false, true]) {
test(`request rejects same-actor login during ${retry ? "retried" : "initial"} JSON decode`, async ({ page, context }) => {
  let requests = 0;
  await context.route("**/api/v1/auth/token/refresh", (route) => route.fulfill({ json: { access_token: "A-rotated", refresh_token: "A-rotated-refresh" } }));
  await context.route("**/api/v1/overview/stats", (route) => {
    requests++;
    return route.fulfill(retry && requests === 1 ? { status: 401 } : { json: profile });
  });
  await page.goto("/__auth_test__");
  await page.evaluate(async (user) => {
    const auth = await import("/src/lib/api.ts");
    await auth.commitLogin({ access_token: "A", refresh_token: "A-refresh", user }, auth.loginStart());
    const original = Response.prototype.json;
    let entered!: () => void;
    let release!: () => void;
    (window as unknown as { entered: Promise<void>; release: () => void; pending: Promise<string> }).entered = new Promise<void>((resolve) => { entered = resolve; });
    const barrier = new Promise<void>((resolve) => { release = resolve; });
    (window as unknown as { release: () => void }).release = release;
    Response.prototype.json = async function () {
      if (this.url.includes("/overview/stats")) { entered(); await barrier; }
      return original.call(this);
    };
    (window as unknown as { pending: Promise<string> }).pending = auth.dashboard.stats()
      .then(() => "success", (err: Error) => err.message);
  }, profile);
  await page.evaluate(() => (window as unknown as { entered: Promise<void> }).entered);
  await page.evaluate(async () => {
    const auth = await import("/src/lib/api.ts");
    await auth.commitLogin({ access_token: "A-again", refresh_token: "A-new",
      user: { role: "user", name: "A", email: "a@example.test" } }, auth.loginStart());
    (window as unknown as { release: () => void }).release();
  });
  expect(await page.evaluate(() => (window as unknown as { pending: Promise<string> }).pending)).toBe("Session changed");
});
}

test("current-bearer GraphQL auth error still clears its owner", async ({ page }) => {
  let fail!: () => void;
  let subscribed!: () => void;
  const ready = new Promise<void>((resolve) => { subscribed = resolve; });
  await page.routeWebSocket("**/api/v1/graphql", (ws) => {
    ws.onMessage((raw) => {
      const message = JSON.parse(String(raw));
      if (message.type === "connection_init") ws.send(JSON.stringify({ type: "connection_ack" }));
      if (message.type === "subscribe") {
        fail = () => ws.send(JSON.stringify({ type: "error", id: message.id, payload: [{ message: "invalid token" }] }));
        subscribed();
      }
    });
  });
  await page.goto("/__auth_test__");
  await page.evaluate(async (user) => {
    const auth = await import("/src/lib/api.ts");
    await auth.commitLogin({ access_token: "bad-access", refresh_token: "bad-refresh", user }, auth.loginStart());
    (await import("/src/lib/graphql-ws.ts")).subscribeToSessionUpdates(() => {});
  }, profile);
  await ready;
  fail();
  await expect(page).toHaveURL(/\/login\?reason=session_expired/);
  expect(await page.evaluate(() => localStorage.getItem("observal_logout_intent"))).not.toBeNull();
});

test("old-bearer GraphQL error recovers subscription after another tab rotates", async ({ page, context }) => {
  let sendError!: () => void;
  let subscribed!: () => void;
  const ready = new Promise<void>((resolve) => { subscribed = resolve; });
  let subscriptions = 0;
  const refreshes: string[] = [];
  await context.route("**/api/v1/auth/token/refresh", (route) => {
    refreshes.push(route.request().postDataJSON().refresh_token);
    return route.fulfill({ contentType: "application/json", body: JSON.stringify({
      access_token: `new-access-${refreshes.length}`, refresh_token: `new-refresh-${refreshes.length}`,
    }) });
  });
  await page.routeWebSocket("**/api/v1/graphql", (ws) => {
    ws.onMessage((raw) => {
      const message = JSON.parse(String(raw));
      if (message.type === "connection_init") ws.send(JSON.stringify({ type: "connection_ack" }));
      if (message.type === "subscribe") {
        subscriptions++;
        sendError = () => ws.send(JSON.stringify({ type: "error", id: message.id, payload: [{ message: "invalid token" }] }));
        subscribed();
      }
    });
  });
  await page.goto("/__auth_test__");
  await page.evaluate(async (user) => {
    const auth = await import("/src/lib/api.ts");
    await auth.commitLogin({ access_token: "old-access", refresh_token: "old-refresh", user }, auth.loginStart());
    (await import("/src/lib/graphql-ws.ts")).subscribeToSessionUpdates(() => {});
  }, profile);
  await ready;
  const other = await context.newPage();
  await other.goto("/__auth_test__");
  await other.evaluate(async () => {
    const auth = await import("/src/lib/api.ts");
    await auth.refreshAccessToken();
  });
  // Replace the backend refresh call deterministically; only the shared JTI
  // changes, while the original subscription's bearer remains in the old tab.
  expect(await other.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBe("new-refresh-1");
  sendError();
  await expect.poll(() => subscriptions).toBe(2);
  expect(refreshes).toEqual(["old-refresh", "new-refresh-1"]);
  expect(await other.evaluate(() => ({ refresh: localStorage.getItem("observal_refresh_token"), intent: localStorage.getItem("observal_logout_intent") })))
    .toMatchObject({ refresh: "new-refresh-2", intent: null });
  expect(await page.evaluate(() => sessionStorage.getItem("observal_access_token"))).toBe("new-access-2");
});

async function warmDevice(page: Page) {
  await page.goto("/device");
  // Vite's initial dependency optimization can restart module delivery.
  if (!page.url().includes("/login")) await page.reload();
  await expect(page).toHaveURL(/\/login\?next=/);
}

test("device link migrates legacy refresh before redirecting to login", async ({ page, context }) => {
  const refreshes: string[] = [];
  await context.route("**/api/v1/auth/token/refresh", (route) => {
    refreshes.push(route.request().postDataJSON().refresh_token);
    return route.fulfill({ contentType: "application/json", body: JSON.stringify({ access_token: "restored", refresh_token: "rotated" }) });
  });
  await warmDevice(page);
  await page.goto("/__auth_test__");
  await page.evaluate(() => {
    localStorage.setItem("observal_refresh_token", "legacy-refresh");
    sessionStorage.setItem("observal_access_token", "legacy-access");
  });
  await page.goto("/device?code=ABCD1234");
  await expect(page.getByRole("button", { name: "Authorize Device" })).toBeVisible();
  await expect(page.locator("#user-code")).toHaveValue("ABCD-1234");
  expect(refreshes).toEqual(["legacy-refresh"]);
  expect(await page.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBe("rotated");
});

test("device link redirects after rejected legacy refresh and preserves code", async ({ page, context }) => {
  await context.route("**/api/v1/auth/token/refresh", (route) => route.fulfill({ status: 401, body: "{}" }));
  await warmDevice(page);
  await page.goto("/__auth_test__");
  await page.evaluate(() => localStorage.setItem("observal_refresh_token", "legacy-refresh"));
  await page.goto("/device?code=ABCD1234");
  await expect(page).toHaveURL(/\/login\?next=.*device/);
  expect(new URL(page.url()).searchParams.get("next")).toBe("/device?code=ABCD1234");
});

test("legacy guest login does not silently reauthenticate on the public registry", async ({ page, context }) => {
  let refreshes = 0;
  await context.route("**/api/v1/auth/token/refresh", (route) => {
    refreshes++;
    return route.fulfill({ contentType: "application/json", body: JSON.stringify({ access_token: "restored", refresh_token: "rotated" }) });
  });
  // Preload the login route before seeding legacy storage so Vite's first
  // dependency optimization cannot interrupt the navigation under test.
  await page.goto("/login?next=%2Fresources%2Fagents");
  await expect(page.getByRole("button", { name: /Sign in as guest/ })).toBeVisible();
  await page.evaluate(() => {
    localStorage.setItem("observal_refresh_token", `x.${btoa(JSON.stringify({ sub: "A" }))}.y`);
    sessionStorage.setItem("observal_access_token", `x.${btoa(JSON.stringify({ sub: "A" }))}.y`);
  });
  await page.getByRole("button", { name: /Sign in as guest/ }).click();
  await expect(page).toHaveURL(/\/resources\/agents/);
  expect(await page.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBeNull();
  expect(refreshes).toBe(0);
});

test("legacy owner guest removes shared refresh while a refresh-only guest cannot", async ({ page, context }) => {
  await page.goto("/__auth_test__");
  await page.evaluate(() => {
    localStorage.setItem("observal_refresh_token", `x.${btoa(JSON.stringify({ sub: "A" }))}.y`);
    sessionStorage.setItem("observal_access_token", `x.${btoa(JSON.stringify({ sub: "A" }))}.y`);
  });
  const guest = await context.newPage();
  await guest.goto("/__auth_test__");
  await guest.evaluate(async () => (await import("/src/lib/api.ts")).leaveForGuest());
  expect(await page.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBe(jwt("A"));
  await page.evaluate(async () => (await import("/src/lib/api.ts")).leaveForGuest());
  expect(await page.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBeNull();
  expect(await page.evaluate(() => localStorage.getItem("observal_logout_intent"))).toBeTruthy();
});

test("refresh-only legacy guest stays anonymous across reload without clearing another tab", async ({ page, context }) => {
  await mockPublicAgentPage(context);
  let refreshes = 0;
  await context.route("**/api/v1/auth/token/refresh", (route) => {
    refreshes++;
    return route.fulfill({ contentType: "application/json", body: JSON.stringify({ access_token: "restored", refresh_token: "rotated" }) });
  });
  await page.goto("/login?next=%2Fresources%2Fagents");
  await expect(page.getByRole("button", { name: /Sign in as guest/ })).toBeVisible();
  const owner = await context.newPage();
  await owner.goto("/__auth_test__");
  await owner.evaluate(() => {
    localStorage.setItem("observal_refresh_token", "legacy-refresh");
    sessionStorage.setItem("observal_access_token", "legacy-access");
  });
  await page.getByRole("button", { name: /Sign in as guest/ }).click();
  await expect(page.getByRole("heading", { name: "Agentic Resources" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Agentic Resources" })).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem("observal_tab_guest"))).toBe("1");
  expect(await owner.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBe("legacy-refresh");
  expect(await owner.evaluate(() => sessionStorage.getItem("observal_access_token"))).toBe("legacy-access");
  expect(refreshes).toBe(0);
});

test("refresh-only legacy guest without another tab cannot reauthenticate", async ({ page, context }) => {
  await mockPublicAgentPage(context);
  let refreshes = 0;
  await context.route("**/api/v1/auth/token/refresh", (route) => {
    refreshes++;
    return route.fulfill({ contentType: "application/json", body: JSON.stringify({ access_token: "restored", refresh_token: "rotated" }) });
  });
  await page.goto("/login?next=%2Fresources%2Fagents");
  await expect(page.getByRole("button", { name: /Sign in as guest/ })).toBeVisible();
  await page.evaluate(() => localStorage.setItem("observal_refresh_token", "legacy-refresh"));
  await page.getByRole("button", { name: /Sign in as guest/ }).click();
  await expect(page.getByRole("heading", { name: "Agentic Resources" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Agentic Resources" })).toBeVisible();
  expect(refreshes).toBe(0);
  expect(await page.evaluate(() => sessionStorage.getItem("observal_tab_guest"))).toBe("1");
});

test("legacy owner without Web Locks can leave as guest without waiting", async ({ page }) => {
  await page.goto("/__auth_test__");
  await page.evaluate(async () => {
    localStorage.setItem("observal_refresh_token", `x.${btoa(JSON.stringify({ sub: "A" }))}.y`);
    sessionStorage.setItem("observal_access_token", `x.${btoa(JSON.stringify({ sub: "A" }))}.y`);
    Object.defineProperty(navigator, "locks", { configurable: true, value: undefined });
    await (await import("/src/lib/api.ts")).leaveForGuest();
  });
  expect(await page.evaluate(() => ({ refresh: localStorage.getItem("observal_refresh_token"),
    intent: localStorage.getItem("observal_logout_intent"), guest: sessionStorage.getItem("observal_tab_guest") })))
    .toMatchObject({ refresh: null, intent: expect.any(String), guest: "1" });
});

for (const action of ["signOut", "leaveForGuest"] as const) {
  for (const lockMode of ["rejected", "unavailable"] as const) {
    test(`legacy owner ${action} fences synchronously with ${lockMode} lock; new tab cannot bootstrap`, async ({ page, context }) => {
      const calls: { endpoint: string; refresh?: string; bearer?: string }[] = [];
      await context.route("**/api/v1/auth/token/refresh", (route) => {
        calls.push({ endpoint: "refresh", refresh: route.request().postDataJSON().refresh_token });
        return route.fulfill({ contentType: "application/json", body: JSON.stringify({ access_token: "restored", refresh_token: "rotated" }) });
      });
      await context.route("**/api/v1/auth/logout", (route) => {
        calls.push({ endpoint: "logout", refresh: route.request().postDataJSON().refresh_token,
          bearer: route.request().headers().authorization });
        return route.fulfill({ status: 200, body: "{}" });
      });
      await context.route("**/api/v1/auth/token/revoke", (route) => {
        calls.push({ endpoint: "revoke", refresh: route.request().postDataJSON().refresh_token });
        return route.fulfill({ status: 200, body: "{}" });
      });
      await page.goto("/__auth_test__");
      const token = await page.evaluate(async ({ action, lockMode }) => {
        const auth = await import("/src/lib/api.ts");
        const access = `x.${btoa(JSON.stringify({ sub: "A", exp: Math.floor(Date.now() / 1000) + 3600 }))}.y`;
        localStorage.setItem("observal_refresh_token", `x.${btoa(JSON.stringify({ sub: "A" }))}.y`);
        localStorage.setItem("observal_user_email", "a@example.test");
        sessionStorage.setItem("observal_access_token", access);
        Object.defineProperty(navigator, "locks", { configurable: true, value: lockMode === "rejected"
          ? { request: () => Promise.reject(new Error("lock rejected")) } : undefined });
        (window as unknown as { legacyDone: Promise<void> }).legacyDone = auth[action]();
        // The fence must precede even the first microtask / failed lock request.
        return { access, immediate: { refresh: localStorage.getItem("observal_refresh_token"),
          intent: localStorage.getItem("observal_logout_intent"), role: localStorage.getItem("observal_user_email") } };
      }, { action, lockMode });
      expect(token.immediate).toEqual({ refresh: null, intent: expect.any(String), role: null });
      await page.evaluate(() => (window as unknown as { legacyDone: Promise<void> }).legacyDone);
      const later = await context.newPage();
      await later.goto("/__auth_test__");
      expect(await later.evaluate(async () => {
        const auth = await import("/src/lib/api.ts");
        await auth.migrateLegacySession();
        return auth.refreshAccessTokenWithReason();
      })).toBe("reauth_required");
      expect(await later.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBeNull();
      await later.goto("/account");
      await expect(later).toHaveURL(/\/login\?next=/);
      expect(calls).toEqual(action === "signOut" ? [{ endpoint: "logout", refresh: jwt("A"),
        bearer: `Bearer ${token.access}` }] : []);
    });
  }
}

test("rejected legacy logout falls back to captured refresh on account logout failure", async ({ page, context }) => {
  const calls: string[] = [];
  await context.route("**/api/v1/auth/logout", (route) => {
    calls.push("logout");
    return route.fulfill({ status: 503, body: "{}" });
  });
  await context.route("**/api/v1/auth/token/revoke", (route) => {
    calls.push(route.request().postDataJSON().refresh_token);
    return route.fulfill({ status: 200, body: "{}" });
  });
  await page.goto("/__auth_test__");
  await page.evaluate(async () => {
    const auth = await import("/src/lib/api.ts");
    localStorage.setItem("observal_refresh_token", `x.${btoa(JSON.stringify({ sub: "A" }))}.y`);
    sessionStorage.setItem("observal_access_token", `x.${btoa(JSON.stringify({ sub: "A", exp: Math.floor(Date.now() / 1000) + 3600 }))}.y`);
    Object.defineProperty(navigator, "locks", { configurable: true, value: { request: () => Promise.reject(new Error("lock rejected")) } });
    await auth.signOut();
  });
  expect(calls).toEqual(["logout", jwt("A")]);
});

test("legacy logout with mismatched refresh subject leaves the shared B session untouched", async ({ page, context }) => {
  const calls: string[] = [];
  await context.route("**/api/v1/auth/logout", (route) => { calls.push("logout"); return route.fulfill({ status: 200, body: "{}" }); });
  await context.route("**/api/v1/auth/token/revoke", (route) => {
    calls.push(route.request().postDataJSON().refresh_token);
    return route.fulfill({ status: 200, body: "{}" });
  });
  await page.goto("/__auth_test__");
  const refresh = await page.evaluate(async () => {
    const auth = await import("/src/lib/api.ts");
    const token = (sub: string) => `x.${btoa(JSON.stringify({ sub, exp: Math.floor(Date.now() / 1000) + 3600 }))}.y`;
    const refresh = token("other-actor");
    localStorage.setItem("observal_refresh_token", refresh);
    sessionStorage.setItem("observal_access_token", token("owner"));
    Object.defineProperty(navigator, "locks", { configurable: true, value: { request: () => Promise.reject(new Error("lock rejected")) } });
    await auth.signOut();
    return refresh;
  });
  expect(calls).toEqual([]);
  expect(await page.evaluate(() => ({ refresh: localStorage.getItem("observal_refresh_token"),
    intent: localStorage.getItem("observal_logout_intent"), access: sessionStorage.getItem("observal_access_token") })))
    .toEqual({ refresh, intent: null, access: null });
});

for (const action of ["leaveForGuest", "signOut"] as const) {
  test(`stale legacy A access cannot ${action} B shared refresh`, async ({ page, context }) => {
    const calls: string[] = [];
    for (const endpoint of ["logout", "token/revoke"]) {
      await context.route(`**/api/v1/auth/${endpoint}`, (route) => {
        calls.push(endpoint);
        return route.fulfill({ status: 200, body: "{}" });
      });
    }
    await page.goto("/__auth_test__");
    await page.evaluate(() => {
      sessionStorage.setItem("observal_access_token", `x.${btoa(JSON.stringify({ sub: "A" }))}.y`);
      localStorage.setItem("observal_refresh_token", `x.${btoa(JSON.stringify({ sub: "B" }))}.y`);
      localStorage.setItem("observal_user_email", "b@example.test");
    });
    const immediate = await page.evaluate((action) => {
      const done = import("/src/lib/api.ts").then((auth) => auth[action]());
      return done.then(() => ({
        access: sessionStorage.getItem("observal_access_token"),
        refresh: localStorage.getItem("observal_refresh_token"),
        email: localStorage.getItem("observal_user_email"),
        intent: localStorage.getItem("observal_logout_intent"),
      }));
    }, action);
    expect(immediate).toEqual({ access: null, refresh: jwt("B"), email: "b@example.test", intent: null });
    expect(calls).toEqual([]);
  });
}

test("legacy access without a nonempty subject cannot claim shared refresh", async ({ page, context }) => {
  const calls: string[] = [];
  for (const endpoint of ["logout", "token/revoke"]) {
    await context.route(`**/api/v1/auth/${endpoint}`, (route) => {
      calls.push(endpoint);
      return route.fulfill({ status: 200, body: "{}" });
    });
  }
  await page.goto("/__auth_test__");
  for (const sub of ["", 123, null]) {
    await page.evaluate((sub) => {
      sessionStorage.setItem("observal_access_token", `x.${btoa(JSON.stringify({ sub }))}.y`);
      localStorage.setItem("observal_refresh_token", `x.${btoa(JSON.stringify({ sub }))}.y`);
    }, sub);
    await page.evaluate(async () => (await import("/src/lib/api.ts")).signOut());
    expect(await page.evaluate(() => ({ access: sessionStorage.getItem("observal_access_token"),
      refresh: localStorage.getItem("observal_refresh_token"), intent: localStorage.getItem("observal_logout_intent") })))
      .toEqual({ access: null, refresh: `x.${btoa(JSON.stringify({ sub }))}.y`, intent: null });
    // The payload is deliberately malformed; no tab may revoke its shared JTI.
  }
  expect(calls).toEqual([]);
});

test("explicit A to B login drops A GraphQL connection before B subscribes", async ({ page }) => {
  const connections: { init: string[]; subscriptions: number; closed: boolean }[] = [];
  await page.routeWebSocket("**/api/v1/graphql", (ws) => {
    const connection = { init: [] as string[], subscriptions: 0, closed: false };
    connections.push(connection);
    ws.onClose(() => { connection.closed = true; });
    ws.onMessage((raw) => {
      const message = JSON.parse(String(raw));
      if (message.type === "connection_init") {
        connection.init.push(message.payload?.authorization ?? "");
        ws.send(JSON.stringify({ type: "connection_ack" }));
      }
      if (message.type === "subscribe") connection.subscriptions++;
    });
  });
  await page.goto("/__auth_test__");
  await page.evaluate(async () => {
    const auth = await import("/src/lib/api.ts");
    await auth.commitLogin({ access_token: "A-access", refresh_token: "A-refresh", user: { role: "user", name: "A", email: "a@example.test" } }, auth.loginStart());
    (await import("/src/lib/graphql-ws.ts")).subscribeToSessionUpdates(() => {});
  });
  await expect.poll(() => connections[0]?.subscriptions).toBe(1);
  await page.evaluate(async () => {
    const auth = await import("/src/lib/api.ts");
    const ws = await import("/src/lib/graphql-ws.ts");
    let clearedWith: string | null = null;
    window.addEventListener("observal:session-cleared", () => {
      clearedWith = sessionStorage.getItem("observal_access_token");
    }, { once: true });
    const committed = await auth.commitLogin({ access_token: "B-access", refresh_token: "B-refresh", user: { role: "user", name: "B", email: "b@example.test" } }, auth.loginStart());
    if (!committed || clearedWith !== "A-access" || auth.getAccessToken() !== "B-access")
      throw new Error("A cleanup did not precede B publication");
    ws.subscribeToSessionUpdates(() => {});
  });
  await expect.poll(() => connections.length).toBe(2);
  await expect.poll(() => connections[1]?.subscriptions).toBe(1);
  await expect.poll(() => connections[0]?.closed).toBe(true);
  expect(connections.map(({ init, subscriptions }) => ({ init, subscriptions }))).toEqual([
    { init: ["Bearer A-access"], subscriptions: 1 },
    { init: ["Bearer B-access"], subscriptions: 1 },
  ]);
  expect(await page.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBe("B-refresh");
});

test("legacy refresh-only guest never fences or remotely revokes another tab's session", async ({ page, context }) => {
  const calls: string[] = [];
  for (const endpoint of ["logout", "token/revoke", "token/refresh"]) {
    await context.route(`**/api/v1/auth/${endpoint}`, (route) => {
      calls.push(endpoint);
      return route.fulfill({ status: 200, body: "{}" });
    });
  }
  await page.goto("/__auth_test__");
  await page.evaluate(() => {
    localStorage.setItem("observal_refresh_token", "other-refresh");
    sessionStorage.setItem("observal_access_token", "owner-access");
  });
  const guest = await context.newPage();
  await guest.goto("/__auth_test__");
  await guest.evaluate(async () => {
    Object.defineProperty(navigator, "locks", { configurable: true, value: { request: () => Promise.reject(new Error("lock rejected")) } });
    const auth = await import("/src/lib/api.ts");
    await auth.leaveForGuest();
    await auth.signOut();
  });
  expect(await page.evaluate(() => ({ refresh: localStorage.getItem("observal_refresh_token"),
    intent: localStorage.getItem("observal_logout_intent"), access: sessionStorage.getItem("observal_access_token") })))
    .toEqual({ refresh: "other-refresh", intent: null, access: "owner-access" });
  expect(calls).toEqual([]);
});

test("unsupported legacy owner signs out locally and best-effort revokes", async ({ page, context }) => {
  const revokes: string[] = [];
  await context.route("**/api/v1/auth/token/revoke", (route) => {
    revokes.push(route.request().postDataJSON().refresh_token);
    return route.fulfill({ status: 200, body: "{}" });
  });
  await page.goto("/__auth_test__");
  await page.evaluate(async () => {
    localStorage.setItem("observal_refresh_token", `x.${btoa(JSON.stringify({ sub: "A" }))}.y`);
    sessionStorage.setItem("observal_access_token", `x.${btoa(JSON.stringify({ sub: "A" }))}.y`);
    Object.defineProperty(navigator, "locks", { configurable: true, value: undefined });
    await (await import("/src/lib/api.ts")).signOut();
  });
  expect(revokes).toEqual([jwt("A")]);
  expect(await page.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBeNull();
});

for (const path of ["/account", "/resources/agents"] as const) {
  test(`rejected refresh lock settles ${path} without unhandled rejection`, async ({ page, context }) => {
    let refreshes = 0;
    const unhandled: string[] = [];
    page.on("pageerror", (err) => unhandled.push(err.message));
    await context.route("**/api/v1/auth/token/refresh", (route) => {
      refreshes++;
      return route.fulfill({ status: 401 });
    });
    await page.goto("/__auth_test__");
    await page.evaluate(async (user) => {
      const auth = await import("/src/lib/api.ts");
      await auth.commitLogin({ access_token: "A", refresh_token: "A-refresh", user }, auth.loginStart());
    }, profile);
    await context.addInitScript(() => {
      Object.defineProperty(navigator, "locks", { configurable: true, value: {
        request: () => Promise.reject(new Error("startup lock rejected")),
      } });
    });
    const fresh = await context.newPage();
    fresh.on("pageerror", (err) => unhandled.push(err.message));
    await fresh.goto(path);
    if (path === "/account") {
      await expect(fresh).toHaveURL(/\/login\?next=/);
    } else {
      await expect(fresh.getByRole("heading", { name: "Agentic Resources" })).toBeVisible();
    }
    expect(await fresh.evaluate(() => sessionStorage.getItem("observal_tab_guest"))).toBeNull();
    expect(refreshes).toBe(0);
    expect(unhandled).toEqual([]);
  });

  test(`rejected migration lock settles ${path} without silently refreshing`, async ({ page, context }) => {
    let refreshes = 0;
    await context.route("**/api/v1/auth/token/refresh", (route) => {
      refreshes++;
      return route.fulfill({ status: 401, body: "{}" });
    });
    // Warm Vite before installing the failing lock and entering the guarded route.
    await page.goto("/__auth_test__");
    await page.evaluate(() => localStorage.setItem("observal_refresh_token", "legacy-refresh"));
    await page.addInitScript(() => {
      Object.defineProperty(navigator, "locks", { configurable: true, value: {
        request: () => Promise.reject(new Error("lock rejected")),
      } });
    });
    await page.goto(path);
    if (path === "/account") {
      await expect(page).toHaveURL(/\/login\?next=/);
      await expect(page.getByText("Sign in or browse the public registry")).toBeVisible();
    } else {
      await expect(page.getByRole("heading", { name: "Agentic Resources" })).toBeVisible();
    }
    expect(await page.evaluate(() => sessionStorage.getItem("observal_tab_guest"))).toBe("1");
    expect(refreshes).toBe(0);
  });
}

test("unsupported browser with legacy refresh redirects guarded route to sign-in", async ({ page }) => {
  await page.goto("/__auth_test__");
  await page.evaluate(() => localStorage.setItem("observal_refresh_token", "legacy-refresh"));
  await page.addInitScript(() => Object.defineProperty(navigator, "locks", { configurable: true, value: undefined }));
  await page.goto("/account");
  await expect(page).toHaveURL(/\/login\?next=/);
  await expect(page.getByText("This browser does not support secure sign-in (Web Locks required).")).toBeVisible();
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
