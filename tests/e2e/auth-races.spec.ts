// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

// SPDX-License-Identifier: Apache-2.0
import { test, expect, type Page } from "@playwright/test";

// These tests import Vite-served source modules, unavailable from CI's built Docker app.
test.use({ baseURL: process.env.CI ? "http://localhost:3104" : "http://localhost:3000" });

const jwt = (sub: string, suffix: string) => `${btoa('{"alg":"none"}')}.${btoa(JSON.stringify({ sub }))}.${suffix}`;
const A = jwt("actor-a", "access");
const B = jwt("actor-b", "access");
const AR = jwt("actor-a", "refresh");
const BR = jwt("actor-b", "refresh");

test("token subjects decode UTF-8 and reject malformed payloads", async ({ page }) => {
  await page.goto("/login");
  expect(await page.evaluate(async () => {
    const { tokenSubject } = await import("/src/lib/api.ts");
    const subject = "用户-ø";
    const bytes = new TextEncoder().encode(JSON.stringify({ sub: subject }));
    const encoded = btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    return [tokenSubject(`x.${encoded}.y`), tokenSubject("x.////.y")];
  })).toEqual(["用户-ø", null]);
});

async function seed(page: Page, access: string | null, refresh: string) {
  await page.goto("/login");
  await page.evaluate(({ access, refresh }) => {
    if (access) sessionStorage.setItem("observal_access_token", access);
    localStorage.setItem("observal_refresh_token", refresh);
  }, { access, refresh });
}

async function submit(page: Page) {
  return page.evaluate(async () => {
    const { feedback } = await import("/src/lib/api.ts");
    return feedback.submit({ listing_type: "agent", listing_id: "test", rating: 5 })
      .then(() => "ok", (err: Error) => err.name);
  });
}

function barrier() {
  let release!: () => void;
  let entered!: () => void;
  return {
    gate: new Promise<void>((resolve) => { release = resolve; }),
    started: new Promise<void>((resolve) => { entered = resolve; }),
    release: () => release(), entered: () => entered(),
  };
}

test("an A mutation cannot be replayed with B's shared refresh token", async ({ context, page }) => {
  await seed(page, A, AR);
  const tabB = await context.newPage();
  await seed(tabB, B, AR);
  const pending = barrier();
  let retries = 0;
  await page.route("**/api/v1/feedback", async (route) => {
    if (route.request().headers().authorization === `Bearer ${B}`) retries++;
    pending.entered();
    await pending.gate;
    await route.fulfill({ status: 401, body: "unauthorized" });
  });
  await page.route("**/api/v1/auth/token/refresh", async (route) => {
    await route.fulfill({ json: { access_token: B, refresh_token: BR } });
  });
  const result = submit(page);
  await pending.started;
  await tabB.evaluate(({ b, br }) => {
    sessionStorage.setItem("observal_access_token", b);
    localStorage.setItem("observal_refresh_token", br);
  }, { b: B, br: BR });
  pending.release();
  expect(await result).toBe("AuthContextChanged");
  expect(retries).toBe(0);
  expect(await tabB.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBe(BR);
  expect(await page.evaluate(async () => {
    const { getTabActor } = await import("/src/lib/api.ts");
    return [getTabActor(), sessionStorage.getItem("observal_access_token")];
  })).toEqual(["actor-a", null]);
});

test("late refresh success and rejection cannot replay after B login", async ({ page }) => {
  for (const status of [200, 401]) {
    await seed(page, A, AR);
    const pending = barrier();
    let retries = 0;
    await page.route("**/api/v1/feedback", async (route) => {
      retries++;
      await route.fulfill({ status: 401, body: "unauthorized" });
    });
    await page.route("**/api/v1/auth/token/refresh", async (route) => {
      pending.entered();
      await pending.gate;
      await route.fulfill(status === 200
        ? { json: { access_token: A, refresh_token: AR } }
        : { status, body: "rejected" });
    });
    const result = submit(page);
    await pending.started;
    await page.evaluate(({ b, br }) => {
      sessionStorage.setItem("observal_access_token", b);
      localStorage.setItem("observal_refresh_token", br);
    }, { b: B, br: BR });
    pending.release();
    expect(await result).toBe("AuthContextChanged");
    expect(retries).toBe(1);
    expect(await page.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBe(BR);
    await page.unrouteAll({ behavior: "wait" });
  }
});

test("same-actor refresh replays POST; transient failures never clear refresh", async ({ page }) => {
  await seed(page, A, AR);
  const rotated = jwt("actor-a", "rotated");
  let calls = 0;
  await page.route("**/api/v1/feedback", async (route) => {
    calls++;
    await route.fulfill(calls === 1 ? { status: 401 } : { json: { id: "ok" } });
  });
  await page.route("**/api/v1/auth/token/refresh", async (route) => {
    await route.fulfill({ json: { access_token: rotated, refresh_token: jwt("actor-a", "rotated-refresh") } });
  });
  expect(await submit(page)).toBe("ok");
  expect(calls).toBe(2);
  await page.unrouteAll({ behavior: "wait" });
  for (const status of [429, 503]) {
    await page.route("**/api/v1/auth/token/refresh", async (route) => route.fulfill({ status }));
    expect(await page.evaluate(async () => (await import("/src/lib/api.ts")).refreshAccessTokenWithReason()))
      .toBe("network_error");
    expect(await page.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBe(jwt("actor-a", "rotated-refresh"));
    await page.unrouteAll({ behavior: "wait" });
  }
});

test("losing tab retries a superseded same-actor refresh with the winner's shared token", async ({ context, page }) => {
  await seed(page, A, AR);
  const winnerTab = await context.newPage();
  await seed(winnerTab, A, AR);
  const pending = barrier();
  const winnerAccess = jwt("actor-a", "winner-access");
  const winnerRefresh = jwt("actor-a", "winner-refresh");
  const replayAccess = jwt("actor-a", "replay-access");
  const replayRefresh = jwt("actor-a", "replay-refresh");
  const bearers: Array<string | undefined> = [];
  const refreshTokens: string[] = [];
  await page.route("**/api/v1/feedback", async (route) => {
    bearers.push(route.request().headers().authorization);
    await route.fulfill(bearers.length === 1 ? { status: 401 } : { json: { id: "written" } });
  });
  await page.route("**/api/v1/auth/token/refresh", async (route) => {
    const refresh = JSON.parse(route.request().postData()!).refresh_token;
    refreshTokens.push(refresh);
    if (refresh === AR) {
      pending.entered();
      await pending.gate;
      await route.fulfill({ status: 401 });
    } else {
      expect(refresh).toBe(winnerRefresh);
      await route.fulfill({ json: { access_token: replayAccess, refresh_token: replayRefresh } });
    }
  });
  await winnerTab.route("**/api/v1/auth/token/refresh", (route) => {
    expect(JSON.parse(route.request().postData()!).refresh_token).toBe(AR);
    return route.fulfill({ json: { access_token: winnerAccess, refresh_token: winnerRefresh } });
  });
  const result = submit(page);
  await pending.started;
  expect(await winnerTab.evaluate(async () => (await import("/src/lib/api.ts")).refreshAccessTokenWithReason())).toBe("ok");
  pending.release();
  expect(await result).toBe("ok");
  expect(bearers).toEqual([`Bearer ${A}`, `Bearer ${replayAccess}`]);
  expect(refreshTokens).toEqual([AR, winnerRefresh]);
  expect(await page.evaluate(() => sessionStorage.getItem("observal_tab_actor"))).toBe("actor-a");
  expect(await page.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBe(replayRefresh);
});

test("a second superseded refresh cannot loop or replay without a new bearer", async ({ page }) => {
  await seed(page, A, AR);
  const rotated = jwt("actor-a", "shared-refresh");
  const newer = jwt("actor-a", "newer-refresh");
  const pending = [barrier(), barrier()];
  let writes = 0;
  let refreshes = 0;
  await page.route("**/api/v1/feedback", (route) => {
    writes++;
    return route.fulfill({ status: 401 });
  });
  await page.route("**/api/v1/auth/token/refresh", async (route) => {
    const index = refreshes++;
    expect(JSON.parse(route.request().postData()!).refresh_token).toBe(index === 0 ? AR : rotated);
    pending[index].entered();
    await pending[index].gate;
    await route.fulfill({ status: 401 });
  });
  const result = submit(page);
  for (const [index, token] of [rotated, newer].entries()) {
    await pending[index].started;
    await page.evaluate((value) => localStorage.setItem("observal_refresh_token", value), token);
    pending[index].release();
  }
  expect(await result).toBe("AuthContextChanged");
  expect([writes, refreshes]).toEqual([1, 2]);
  expect(await page.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBe(newer);
});

test("new tab bootstrap works but an unauthenticated original POST is never replayed", async ({ page }) => {
  await seed(page, null, BR);
  let calls = 0;
  await page.route("**/api/v1/feedback", async (route) => {
    calls++;
    await route.fulfill({ status: 401 });
  });
  await page.route("**/api/v1/auth/token/refresh", async (route) => {
    await route.fulfill({ json: { access_token: B, refresh_token: BR } });
  });
  expect(await submit(page)).toBe("AuthContextChanged");
  expect(calls).toBe(1);
  expect(await page.evaluate(async () => (await import("/src/lib/api.ts")).refreshAccessTokenWithReason()))
    .toBe("ok");
  expect(await page.evaluate(() => sessionStorage.getItem("observal_tab_actor"))).toBe("actor-b");
});

test("malformed refresh and reload of quarantined tab cannot adopt B", async ({ page }) => {
  await seed(page, A, "malformed");
  let refreshCalls = 0;
  await page.route("**/api/v1/auth/token/refresh", async (route) => {
    refreshCalls++;
    await route.fulfill({ json: { access_token: B, refresh_token: BR } });
  });
  expect(await page.evaluate(async () => (await import("/src/lib/api.ts")).refreshAccessTokenWithReason()))
    .toBe("account_changed");
  await page.evaluate(({ br }) => localStorage.setItem("observal_refresh_token", br), { br: BR });
  await page.reload();
  expect(await page.evaluate(async () => (await import("/src/lib/api.ts")).refreshAccessTokenWithReason()))
    .toBe("account_changed");
  expect(await page.evaluate(() => sessionStorage.getItem("observal_tab_actor"))).toBe("actor-a");
  expect(refreshCalls).toBe(0);
});

test("malformed legacy access cannot become an unpinned B bootstrap", async ({ page }) => {
  await seed(page, "invalid.jwt", BR);
  let calls = 0;
  await page.route("**/api/v1/auth/token/refresh", async (route) => {
    calls++;
    await route.fulfill({ json: { access_token: B, refresh_token: BR } });
  });
  expect(await page.evaluate(async () => (await import("/src/lib/api.ts")).refreshAccessTokenWithReason()))
    .toBe("account_changed");
  await page.reload();
  expect(await page.evaluate(async () => (await import("/src/lib/api.ts")).refreshAccessTokenWithReason()))
    .toBe("account_changed");
  expect(calls).toBe(0);
});

test("same-account competing rotation does not clear the winner", async ({ page }) => {
  await seed(page, A, AR);
  const pending = barrier();
  const winner = jwt("actor-a", "winner");
  await page.route("**/api/v1/auth/token/refresh", async (route) => {
    pending.entered();
    await pending.gate;
    await route.fulfill({ status: 401 });
  });
  const result = page.evaluate(async () => (await import("/src/lib/api.ts")).refreshAccessTokenWithReason());
  await pending.started;
  await page.evaluate((winner) => localStorage.setItem("observal_refresh_token", winner), winner);
  pending.release();
  expect(await result).toBe("superseded");
  expect(await page.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBe(winner);
  expect(await page.evaluate(() => sessionStorage.getItem("observal_tab_actor"))).toBe("actor-a");
});

test("storage event quarantines A before a protected view renders B's shared role", async ({ page }) => {
  await page.route("**/api/v1/config/public", async (route) => route.fulfill({ json: { public_registry_enabled: false } }));
  await seed(page, A, AR);
  await page.evaluate(() => localStorage.setItem("observal_user_role", "user"));
  await page.goto("/resources/agents");
  await expect(page.getByText("Sign out")).toHaveCount(0); // menu is closed
  await page.evaluate(({ br }) => {
    localStorage.setItem("observal_refresh_token", br);
    localStorage.setItem("observal_user_role", "admin");
    window.dispatchEvent(new Event("storage"));
  }, { br: BR });
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem("observal_access_token"))).toBeNull();
  expect(await page.evaluate(() => sessionStorage.getItem("observal_tab_actor"))).toBe("actor-a");
  await expect(page).toHaveURL(/\/login/);
  await page.reload();
  expect(await page.evaluate(() => sessionStorage.getItem("observal_access_token"))).toBeNull();
});

test("returning after missing the storage event blocks A's protected content", async ({ context, page }) => {
  await page.route("**/api/v1/config/public", async (route) => route.fulfill({ json: { public_registry_enabled: false } }));
  await seed(page, A, AR);
  await page.evaluate(() => localStorage.setItem("observal_user_role", "user"));
  await page.goto("about:blank");
  const tabB = await context.newPage();
  await seed(tabB, B, AR);
  await tabB.evaluate((br) => localStorage.setItem("observal_refresh_token", br), BR);
  await page.goto("/resources/agents");
  expect(await page.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBe(BR);
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem("observal_access_token"))).toBeNull();
  expect(await page.evaluate(() => sessionStorage.getItem("observal_tab_actor"))).toBe("actor-a");
  await expect(page.getByRole("button", { name: /user/i })).toHaveCount(0);
});

test("pending whoami cannot write A's role over B's shared profile", async ({ page }) => {
  const pending = barrier();
  await page.route("**/api/v1/config/public", async (route) => route.fulfill({ json: { public_registry_enabled: false } }));
  await page.route("**/api/v1/auth/whoami", async (route) => {
    pending.entered();
    await pending.gate;
    await route.fulfill({ json: { role: "user", name: "A" } });
  });
  await seed(page, A, AR);
  await page.goto("/resources/agents");
  await pending.started;
  await page.evaluate((br) => {
    localStorage.setItem("observal_refresh_token", br);
    localStorage.setItem("observal_user_role", "admin");
  }, BR);
  pending.release();
  await expect(page).toHaveURL(/\/login/);
  expect(await page.evaluate(() => localStorage.getItem("observal_user_role"))).toBe("admin");
  expect(await page.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBe(BR);
});

test("pending A whoami rejection cannot clear B's shared profile", async ({ page }) => {
  await page.route("**/api/v1/config/public", async (route) => route.fulfill({ json: { public_registry_enabled: false } }));
  const pending = barrier();
  await page.route("**/api/v1/auth/whoami", async (route) => {
    pending.entered();
    await pending.gate;
    await route.fulfill({ status: 401 });
  });
  await seed(page, A, AR);
  await page.goto("/resources/agents");
  await pending.started;
  await page.evaluate((br) => {
    localStorage.setItem("observal_refresh_token", br);
    localStorage.setItem("observal_user_role", "admin");
  }, BR);
  pending.release();
  await expect(page).toHaveURL(/\/login/);
  expect(await page.evaluate(() => localStorage.getItem("observal_user_role"))).toBe("admin");
  expect(await page.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBe(BR);
});

test("mounted A sign out cannot send or delete B's refresh", async ({ page }) => {
  await page.route("**/api/v1/config/public", async (route) => route.fulfill({ json: { public_registry_enabled: false } }));
  await seed(page, A, AR);
  await page.evaluate(() => localStorage.setItem("observal_user_role", "user"));
  await page.goto("/resources/agents");
  const menu = page.getByRole("button", { name: /user/i }).last();
  await expect(menu).toBeVisible();
  let logouts = 0;
  await page.route("**/api/v1/auth/logout", async (route) => {
    logouts++;
    await route.fulfill({ status: 204 });
  });
  await menu.click();
  // Simulate B's write immediately before the already-mounted handler runs, without an event.
  await page.evaluate((br) => {
    const item = [...document.querySelectorAll('[role="menuitem"]')]
      .find((node) => node.textContent?.includes("Sign out"));
    item?.addEventListener("click", () => localStorage.setItem("observal_refresh_token", br), { capture: true });
  }, BR);
  await page.getByText("Sign out").click();
  await expect(page).toHaveURL(/\/login/);
  expect(logouts).toBe(0);
  expect(await page.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBe(BR);
});

test("quarantined tab can explicitly sign in or exchange without its stale bearer or retries", async ({ page }) => {
  await seed(page, A, BR);
  await page.evaluate(async () => {
    const { getTabActor, quarantineTab } = await import("/src/lib/api.ts");
    getTabActor();
    quarantineTab();
  });
  expect(await page.evaluate(() => sessionStorage.getItem("observal_tab_actor"))).toBe("actor-a");
  const requests: Array<{ path: string; authorization: string | undefined }> = [];
  for (const path of ["login", "exchange"]) {
    await page.route(`**/api/v1/auth/${path}`, async (route) => {
      requests.push({ path, authorization: route.request().headers().authorization });
      await route.fulfill(path === "login"
        ? { status: 503, body: "unavailable" }
        : { json: { access_token: B, refresh_token: BR } });
    });
  }
  const result = await page.evaluate(async () => {
    const { auth, setTokens } = await import("/src/lib/api.ts");
    const login = await auth.login({ email: "b@example.com", password: "password" })
      .then(() => "ok", (err: Error) => err.name);
    const session = await auth.exchangeCode({ code: "new-code" });
    setTokens(session.access_token, session.refresh_token);
    return login;
  });
  expect(result).toBe("Error");
  expect(requests).toEqual([
    { path: "login", authorization: undefined },
    { path: "exchange", authorization: undefined },
  ]);
  expect(await page.evaluate(() => sessionStorage.getItem("observal_tab_actor"))).toBe("actor-b");
});

test("rejected refresh keeps A pinned through B login in another tab", async ({ context, page }) => {
  await seed(page, A, AR);
  await page.route("**/api/v1/feedback", async (route) => route.fulfill({ status: 401 }));
  await page.route("**/api/v1/auth/token/refresh", async (route) => route.fulfill({ status: 401 }));
  void submit(page).catch(() => {}); // session expiry navigates rather than resolving the request
  await expect(page).toHaveURL(/\/login\?reason=session_expired/);
  expect(await page.evaluate(() => sessionStorage.getItem("observal_tab_actor"))).toBe("actor-a");
  const tabB = await context.newPage();
  await seed(tabB, B, BR);
  await page.reload();
  expect(await page.evaluate(async () => (await import("/src/lib/api.ts")).refreshAccessTokenWithReason()))
    .toBe("account_changed");
  expect(await page.evaluate(() => sessionStorage.getItem("observal_tab_actor"))).toBe("actor-a");
});

test("replayed success decoded after B signs in cannot return A data", async ({ page }) => {
  await seed(page, A, AR);
  await page.route("**/api/v1/feedback", async (route) => route.fulfill(
    route.request().headers().authorization === `Bearer ${A}` ? { status: 401 } : { json: { id: "old-A" } },
  ));
  const rotated = jwt("actor-a", "rotated-access");
  await page.route("**/api/v1/auth/token/refresh", async (route) =>
    route.fulfill({ json: { access_token: rotated, refresh_token: jwt("actor-a", "rotated-refresh") } }));
  await page.evaluate(() => {
    const original = Response.prototype.json;
    let release!: () => void;
    (window as any).releaseDecode = () => release();
    Response.prototype.json = async function (...args) {
      if (this.url.includes("/api/v1/feedback") && this.status === 200) {
        (window as any).decodeEntered = true;
        await new Promise<void>((resolve) => { release = resolve; });
      }
      return original.apply(this, args);
    };
  });
  const result = submit(page);
  await expect.poll(() => page.evaluate(() => (window as any).decodeEntered ?? false)).toBe(true);
  await page.evaluate(({ b, br }) => {
    const { releaseDecode } = window as any;
    sessionStorage.setItem("observal_access_token", b);
    localStorage.setItem("observal_refresh_token", br);
    releaseDecode();
  }, { b: B, br: BR });
  expect(await result).toBe("AuthContextChanged");
});

test("pending whoami accepts same-actor success and retries stale rejection", async ({ page }) => {
  await page.route("**/api/v1/config/public", async (route) => route.fulfill({ json: { public_registry_enabled: false } }));
  const pending = barrier();
  let calls = 0;
  await page.route("**/api/v1/auth/whoami", async (route) => {
    calls++;
    if (calls === 1) {
      pending.entered();
      await pending.gate;
      await route.fulfill({ status: 401 });
    } else {
      await route.fulfill({ json: { role: "user", name: "A" } });
    }
  });
  await seed(page, A, AR);
  await page.goto("/resources/agents");
  await pending.started;
  const rotated = jwt("actor-a", "whoami-rotated");
  await page.evaluate(({ access, refresh }) => {
    sessionStorage.setItem("observal_access_token", access);
    localStorage.setItem("observal_refresh_token", refresh);
  }, { access: rotated, refresh: jwt("actor-a", "whoami-refresh") });
  pending.release();
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem("observal_access_token"))).toBe(rotated);
  // A superseded rejection is retried, not treated as an expired session.
  await expect.poll(() => calls, { timeout: 10_000 }).toBe(2);
  await expect.poll(() => page.evaluate(() => localStorage.getItem("observal_user_role"))).toBe("user");
  expect(await page.evaluate(() => sessionStorage.getItem("observal_tab_actor"))).toBe("actor-a");
});

test("pending whoami success may write role after same-actor rotation", async ({ page }) => {
  await page.route("**/api/v1/config/public", async (route) => route.fulfill({ json: { public_registry_enabled: false } }));
  const pending = barrier();
  await page.route("**/api/v1/auth/whoami", async (route) => {
    pending.entered();
    await pending.gate;
    await route.fulfill({ json: { role: "user", name: "A" } });
  });
  await seed(page, A, AR);
  await page.goto("/resources/agents");
  await pending.started;
  await page.evaluate(({ access, refresh }) => {
    sessionStorage.setItem("observal_access_token", access);
    localStorage.setItem("observal_refresh_token", refresh);
  }, { access: jwt("actor-a", "new-access"), refresh: jwt("actor-a", "new-refresh") });
  pending.release();
  await expect.poll(() => page.evaluate(() => localStorage.getItem("observal_user_role"))).toBe("user");
  expect(await page.evaluate(() => sessionStorage.getItem("observal_tab_actor"))).toBe("actor-a");
});

test("mounted GraphQL subscription reconnects on same-actor rotation, never under B", async ({ page }) => {
  // Mock only the GraphQL transport; Vite's HMR WebSocket remains real.
  await page.addInitScript(() => {
    const NativeWebSocket = WebSocket;
    (window as any).connections = [];
    class MockSocket extends EventTarget {
      static OPEN = 1;
      readyState = 0;
      onopen?: () => void;
      onmessage?: (event: MessageEvent) => void;
      onclose?: (event: CloseEvent) => void;
      bearer?: string;
      subscribed = false;
      constructor() {
        super();
        (window as any).connections.push(this);
        queueMicrotask(() => {
          this.readyState = 1;
          this.onopen?.();
        });
      }
      send(raw: string) {
        const message = JSON.parse(raw);
        if (message.type === "connection_init") {
          this.bearer = message.payload?.authorization;
          queueMicrotask(() => this.onmessage?.(new MessageEvent("message", { data: JSON.stringify({ type: "connection_ack" }) })));
        }
        if (message.type === "subscribe") {
          this.subscribed = true;
          queueMicrotask(() => this.onmessage?.(new MessageEvent("message", { data: JSON.stringify({
            id: message.id, type: "next", payload: { data: { sessionUpdated: { sessionId: "session", eventName: "created" } } },
          }) })));
        }
      }
      close() {
        this.readyState = 3;
        queueMicrotask(() => this.onclose?.(new CloseEvent("close", { code: 1000, wasClean: true })));
      }
    }
    window.WebSocket = class {
      static OPEN = 1;
      static CLOSING = 2;
      constructor(url: string | URL, protocols?: string | string[]) {
        if (String(url).endsWith("/api/v1/graphql")) return new MockSocket();
        return new NativeWebSocket(url, protocols);
      }
    } as unknown as typeof WebSocket;
  });
  await seed(page, A, AR);
  await page.evaluate(async () => {
    const { subscribeToSessionUpdates } = await import("/src/lib/graphql-ws.ts");
    (window as any).events = [];
    (window as any).stopSubscription = subscribeToSessionUpdates((id, event) =>
      (window as any).events.push([id, event]));
  });
  const connections = () => page.evaluate(() => (window as any).connections.map((ws: any) => ({ bearer: ws.bearer, subscribed: ws.subscribed })));
  await expect.poll(async () => (await connections()).length).toBe(1);
  await expect.poll(async () => (await connections())[0].subscribed).toBe(true);
  expect((await connections())[0].bearer).toBe(`Bearer ${A}`);
  await page.evaluate(({ access, refresh }) => {
    sessionStorage.setItem("observal_access_token", access);
    localStorage.setItem("observal_refresh_token", refresh);
    window.dispatchEvent(new Event("storage"));
  }, { access: jwt("actor-a", "ws-rotated"), refresh: jwt("actor-a", "ws-refresh") });
  await expect.poll(async () => (await connections()).length).toBe(2);
  await expect.poll(async () => (await connections())[1].subscribed).toBe(true);
  expect((await connections())[1].bearer).toBe(`Bearer ${jwt("actor-a", "ws-rotated")}`);
  await page.evaluate(({ b, br }) => {
    sessionStorage.setItem("observal_access_token", b);
    localStorage.setItem("observal_refresh_token", br);
    window.dispatchEvent(new Event("storage"));
  }, { b: B, br: BR });
  expect(await connections()).toHaveLength(2);
  expect(await page.evaluate(() => (window as any).events)).toEqual([
    ["session", "created"], ["session", "created"],
  ]);
});

test("public registry stays anonymous-ready after account switch, including reload", async ({ page }) => {
  await page.route("**/api/v1/config/public", async (route) => route.fulfill({ json: { public_registry_enabled: true } }));
  const anonymousLists: string[] = [];
  await page.route("**/api/v1/agents?*", async (route) => {
    if (!route.request().headers().authorization) anonymousLists.push(route.request().url());
    await route.fulfill({ json: [] });
  });
  const bRequests: string[] = [];
  page.on("request", (request) => {
    if (request.headers().authorization === `Bearer ${B}`) bRequests.push(request.url());
  });
  await seed(page, A, AR);
  await page.evaluate(() => localStorage.setItem("observal_user_role", "user"));
  await page.goto("/resources/agents");
  await page.evaluate(({ br }) => {
    localStorage.setItem("observal_refresh_token", br);
    localStorage.setItem("observal_user_role", "admin");
    window.dispatchEvent(new Event("storage"));
  }, { br: BR });
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem("observal_access_token"))).toBeNull();
  await expect(page.getByRole("heading", { name: "Agentic Resources" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Sign in to contribute" })).toBeVisible();
  await expect.poll(() => anonymousLists.length).toBeGreaterThan(0);
  const listsBeforeReload = anonymousLists.length;
  await page.reload();
  await expect(page.getByRole("heading", { name: "Agentic Resources" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Sign in to contribute" })).toBeVisible();
  await expect.poll(() => anonymousLists.length).toBeGreaterThan(listsBeforeReload);
  await expect(page.getByText("Account changed. Sign in again to continue.")).toHaveCount(0);
  expect(bRequests).toEqual([]);
  expect(await page.evaluate(() => sessionStorage.getItem("observal_tab_actor"))).toBe("actor-a");
  expect(await page.evaluate(() => sessionStorage.getItem("observal_access_token"))).toBeNull();
});

test("audit CSV export still succeeds for an unchanged actor", async ({ page }) => {
  await seed(page, A, AR);
  await page.route("**/api/v1/admin/audit-log/export**", async (route) => {
    expect(route.request().headers().authorization).toBe(`Bearer ${A}`);
    await route.fulfill({ body: "event_id,actor\n1,A", contentType: "text/csv" });
  });
  expect(await page.evaluate(async () => (await import("/src/lib/api.ts")).admin.auditLogExport()))
    .toBe("event_id,actor\n1,A");
});

test("audit CSV export refuses a quarantined tab before sending A or B bearer", async ({ page }) => {
  await seed(page, A, BR);
  let calls = 0;
  await page.route("**/api/v1/admin/audit-log/export**", async (route) => {
    calls++;
    await route.fulfill({ body: "private-A" });
  });
  expect(await page.evaluate(async () => (await import("/src/lib/api.ts")).admin.auditLogExport()
    .then(() => "ok", (err: Error) => err.name))).toBe("AuthContextChanged");
  expect(calls).toBe(0);
});

test("audit CSV export rejects A data after B switches during response or text decoding", async ({ page }) => {
  for (const phase of ["response", "decode"]) {
    await seed(page, A, AR);
    const pending = barrier();
    let authorization: string | undefined;
    await page.route("**/api/v1/admin/audit-log/export**", async (route) => {
      authorization = route.request().headers().authorization;
      if (phase === "response") {
        pending.entered();
        await pending.gate;
      }
      await route.fulfill({ body: "private-A" });
    });
    if (phase === "decode") {
      await page.evaluate(() => {
        const original = Response.prototype.text;
        Response.prototype.text = async function (...args) {
          if (this.url.includes("/api/v1/admin/audit-log/export")) {
            (window as any).decodeEntered = true;
            await new Promise<void>((resolve) => { (window as any).releaseDecode = resolve; });
          }
          return original.apply(this, args);
        };
      });
    }
    const result = page.evaluate(async () => (await import("/src/lib/api.ts")).admin.auditLogExport()
      .then(() => "ok", (err: Error) => err.name));
    if (phase === "response") await pending.started;
    else await expect.poll(() => page.evaluate(() => (window as any).decodeEntered ?? false)).toBe(true);
    await page.evaluate(({ br, phase }) => {
      localStorage.setItem("observal_refresh_token", br);
      if (phase === "decode") (window as any).releaseDecode();
    }, { br: BR, phase });
    if (phase === "response") pending.release();
    expect(await result).toBe("AuthContextChanged");
    expect(authorization).toBe(`Bearer ${A}`);
    expect(await page.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBe(BR);
    await page.unrouteAll({ behavior: "wait" });
    await page.reload();
  }
});

test("audit CSV UI does not download A response after B account switch", async ({ page }) => {
  await page.route("**/api/v1/config/public", async (route) => route.fulfill({ json: { public_registry_enabled: false } }));
  await page.route("**/api/v1/admin/audit-log", async (route) => route.fulfill({ json: [] }));
  await seed(page, A, AR);
  await page.evaluate(() => localStorage.setItem("observal_user_role", "admin"));
  await page.goto("/audit-log");
  const exportButton = page.getByRole("button", { name: "Export CSV" });
  await expect(exportButton).toBeVisible();
  const pending = barrier();
  let downloads = 0;
  page.on("download", () => { downloads++; });
  await page.route("**/api/v1/admin/audit-log/export**", async (route) => {
    pending.entered();
    await pending.gate;
    await route.fulfill({ body: "private-A", contentType: "text/csv" });
  });
  await exportButton.click();
  await pending.started;
  await page.evaluate((br) => localStorage.setItem("observal_refresh_token", br), BR);
  pending.release();
  await expect(page).toHaveURL(/\/login/);
  expect(downloads).toBe(0);
});

test("audit CSV UI rechecks identity immediately before download", async ({ page }) => {
  await page.route("**/api/v1/config/public", async (route) => route.fulfill({ json: { public_registry_enabled: false } }));
  await page.route("**/api/v1/admin/audit-log**", async (route) => route.fulfill(
    route.request().url().includes("/export") ? { body: "private-A" } : { json: [] },
  ));
  await seed(page, A, AR);
  await page.evaluate(() => localStorage.setItem("observal_user_role", "admin"));
  await page.goto("/audit-log");
  await expect(page.getByRole("button", { name: "Export CSV" })).toBeVisible();
  await page.evaluate((br) => {
    const create = URL.createObjectURL.bind(URL);
    URL.createObjectURL = (blob) => {
      const url = create(blob);
      localStorage.setItem("observal_refresh_token", br);
      return url;
    };
  }, BR);
  let downloads = 0;
  page.on("download", () => { downloads++; });
  await page.getByRole("button", { name: "Export CSV" }).click();
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem("observal_access_token"))).toBeNull();
  expect(downloads).toBe(0);
  expect(await page.evaluate(() => localStorage.getItem("observal_refresh_token"))).toBe(BR);
});

test("username PUT refuses a mismatched actor before sending", async ({ page }) => {
  await page.route("**/api/v1/config/public", (route) => route.fulfill({ json: { public_registry_enabled: false } }));
  await seed(page, A, AR);
  await page.evaluate(() => localStorage.setItem("observal_user_role", "user"));
  await page.goto("/account");
  await page.getByPlaceholder("3-32 chars, lowercase alphanumeric + hyphens/dots").fill("new-name");
  let calls = 0;
  await page.route("**/api/v1/auth/profile/username", (route) => { calls++; return route.fulfill({ json: { username: "new-name" } }); });
  await page.getByRole("button", { name: "Update Username" }).evaluate((button, br) => {
    button.addEventListener("click", () => {
      localStorage.setItem("observal_refresh_token", br);
      localStorage.setItem("observal_user_username", "b-name");
    }, { capture: true, once: true });
    (button as HTMLElement).click();
  }, BR);
  expect(calls).toBe(0);
  expect(await page.evaluate(() => localStorage.getItem("observal_user_username"))).toBe("b-name");
  expect(await page.evaluate(() => sessionStorage.getItem("observal_access_token"))).toBeNull();
});

test("username success survives same-actor rotation and updates cached username", async ({ page }) => {
  await page.route("**/api/v1/config/public", (route) => route.fulfill({ json: { public_registry_enabled: false } }));
  await page.route("**/api/v1/auth/whoami", (route) => route.fulfill({ json: { id: "actor-a", role: "user" } }));
  await seed(page, A, AR);
  await page.evaluate(() => localStorage.setItem("observal_user_role", "user"));
  await page.goto("/account");
  await page.getByPlaceholder("3-32 chars, lowercase alphanumeric + hyphens/dots").fill("new-name");
  const pending = barrier();
  let calls = 0;
  await page.route("**/api/v1/auth/profile/username", async (route) => {
    calls++;
    expect(route.request().headers().authorization).toBe(`Bearer ${A}`);
    pending.entered(); await pending.gate;
    await route.fulfill({ json: { username: "new-name" } });
  });
  await page.getByRole("button", { name: "Update Username" }).click();
  await pending.started;
  await page.evaluate((access) => sessionStorage.setItem("observal_access_token", access), jwt("actor-a", "username-rotated"));
  pending.release();
  await expect.poll(() => page.evaluate(() => localStorage.getItem("observal_user_username"))).toBe("new-name");
  expect(calls).toBe(1);
});

test("late username PUT cannot overwrite B's shared username", async ({ page }) => {
  await page.route("**/api/v1/config/public", (route) => route.fulfill({ json: { public_registry_enabled: false } }));
  await seed(page, A, AR);
  await page.evaluate(() => localStorage.setItem("observal_user_role", "user"));
  await page.goto("/account");
  await page.getByPlaceholder("3-32 chars, lowercase alphanumeric + hyphens/dots").fill("new-name");
  const pending = barrier();
  let bearer: string | undefined;
  await page.route("**/api/v1/auth/profile/username", async (route) => {
    bearer = route.request().headers().authorization;
    pending.entered();
    await pending.gate;
    await route.fulfill({ json: { username: "new-name" } });
  });
  await page.getByRole("button", { name: "Update Username" }).click();
  await pending.started;
  await page.evaluate((br) => {
    localStorage.setItem("observal_refresh_token", br);
    localStorage.setItem("observal_user_username", "b-name");
  }, BR);
  pending.release();
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem("observal_access_token"))).toBeNull();
  expect(bearer).toBe(`Bearer ${A}`);
  expect(await page.evaluate(() => localStorage.getItem("observal_user_username"))).toBe("b-name");
});

test("direct co-author and transfer mutations never use a switched actor or commit stale results", async ({ page }) => {
  const actions = ["add", "remove", "transfer"] as const;
  for (const action of actions) {
    await seed(page, A, AR);
    await page.evaluate(async () => {
      const { default: React } = await import("/node_modules/.vite/deps/react.js");
      const { default: ReactDOM } = await import("/node_modules/.vite/deps/react-dom_client.js");
      const { CoAuthorInput } = await import("/src/components/registry/co-author-input.tsx");
      const hookSource = await (await fetch("/src/hooks/use-user-search.ts")).text();
      const queryModule = hookSource.match(/from "([^\"]*\/\@tanstack_react-query\.js\?v=[^\"]+)"/)?.[1];
      if (!queryModule) throw new Error("Query module URL unavailable");
      const { QueryClient, QueryClientProvider } = await import(/* @vite-ignore */ queryModule);
      const node = document.createElement("div"); document.body.appendChild(node);
      (window as any).changes = []; (window as any).transfers = 0;
      ReactDOM.createRoot(node).render(React.createElement(QueryClientProvider, { client: new QueryClient() },
        React.createElement(CoAuthorInput, {
          entityType: "agents", entityId: "test", coAuthors: [{ id: "author", email: "author@example.com" }],
          onChange: (authors: unknown) => (window as any).changes.push(authors),
          canTransferOwnership: true, onTransferOwnership: () => (window as any).transfers++,
        })));
    });
    if (action === "add") {
      await page.getByRole("button", { name: "Add", exact: true }).click();
      await page.getByPlaceholder("Search people").fill("@another");
    } else if (action === "remove") {
      await page.locator("button:has(svg.lucide-x)").last().click();
    } else {
      await page.getByRole("button", { name: "Transfer", exact: true }).click();
      await page.getByPlaceholder("Search people").fill("@another");
    }
    const endpoint = action === "transfer" ? "transfer-ownership" : action === "remove" ? "co-authors/author" : "co-authors";
    const button = action === "add" ? "Add co-author" : action === "remove" ? "Remove" : "Transfer owner";
    let calls = 0;
    const pending = barrier();
    await page.route(`**/api/v1/agents/test/${endpoint}`, async (route) => {
      calls++;
      expect(route.request().headers().authorization).toBe(`Bearer ${A}`);
      pending.entered(); await pending.gate;
      await route.fulfill(action === "remove" ? { status: 204 } : { json: { id: "new", email: "new@example.com" } });
    });
    await page.evaluate((br) => localStorage.setItem("observal_refresh_token", br), BR);
    await page.getByRole("button", { name: button, exact: true }).evaluate((el: HTMLElement) => el.click());
    expect(calls).toBe(0);
    await page.evaluate((ar) => localStorage.setItem("observal_refresh_token", ar), AR);
    // The quarantined tab cannot silently resume; explicitly restore A to test late response.
    await page.evaluate(async ({ a, ar }) => (await import("/src/lib/api.ts")).setTokens(a, ar), { a: A, ar: AR });
    await page.getByRole("button", { name: button, exact: true }).evaluate((el: HTMLElement) => el.click());
    await pending.started;
    await page.evaluate((br) => localStorage.setItem("observal_refresh_token", br), BR);
    pending.release();
    await expect.poll(() => page.evaluate(() => sessionStorage.getItem("observal_access_token"))).toBeNull();
    expect(calls).toBe(1);
    expect(await page.evaluate(() => [(window as any).changes, (window as any).transfers])).toEqual([[], 0]);
    await page.unrouteAll({ behavior: "wait" });
  }
});

test("co-author writes commit successful responses after same-actor rotation", async ({ page }) => {
  for (const action of ["add", "transfer"] as const) {
    await seed(page, A, AR);
    await page.evaluate(async () => {
      const { default: React } = await import("/node_modules/.vite/deps/react.js");
      const { default: ReactDOM } = await import("/node_modules/.vite/deps/react-dom_client.js");
      const { CoAuthorInput } = await import("/src/components/registry/co-author-input.tsx");
      const hookSource = await (await fetch("/src/hooks/use-user-search.ts")).text();
      const queryModule = hookSource.match(/from "([^\"]*\/\@tanstack_react-query\.js\?v=[^\"]+)"/)?.[1];
      if (!queryModule) throw new Error("Query module URL unavailable");
      const { QueryClient, QueryClientProvider } = await import(/* @vite-ignore */ queryModule);
      const node = document.createElement("div"); document.body.appendChild(node);
      (window as any).changes = []; (window as any).transfers = 0;
      ReactDOM.createRoot(node).render(React.createElement(QueryClientProvider, { client: new QueryClient() },
        React.createElement(CoAuthorInput, {
          entityType: "agents", entityId: "test", coAuthors: [],
          onChange: (authors: unknown) => (window as any).changes.push(authors),
          canTransferOwnership: true, onTransferOwnership: () => (window as any).transfers++,
        })));
    });
    await page.getByRole("button", { name: action === "add" ? "Add" : "Transfer", exact: true }).click();
    await page.getByPlaceholder("Search people").fill("@another");
    const pending = barrier();
    let calls = 0;
    await page.route(`**/api/v1/agents/test/${action === "add" ? "co-authors" : "transfer-ownership"}`, async (route) => {
      calls++;
      expect(route.request().headers().authorization).toBe(`Bearer ${A}`);
      pending.entered(); await pending.gate;
      await route.fulfill({ json: { id: "new", email: "new@example.com" } });
    });
    await page.getByRole("button", { name: action === "add" ? "Add co-author" : "Transfer owner", exact: true })
      .evaluate((el: HTMLElement) => el.click());
    await pending.started;
    await page.evaluate((access) => sessionStorage.setItem("observal_access_token", access), jwt("actor-a", `coauthor-${action}`));
    pending.release();
    if (action === "add") {
      await expect.poll(() => page.evaluate(() => (window as any).changes)).toEqual([[{ id: "new", email: "new@example.com" }]]);
    } else {
      await expect.poll(() => page.evaluate(() => (window as any).transfers)).toBe(1);
    }
    expect(calls).toBe(1);
    await page.unrouteAll({ behavior: "wait" });
    await page.reload();
  }
});

test("completed POST survives same-actor rotation without replay, but not A to B", async ({ page }) => {
  for (const nextActor of ["actor-a", "actor-b"]) {
    await seed(page, A, AR);
    const pending = barrier();
    let writes = 0;
    await page.route("**/api/v1/feedback", async (route) => {
      writes++;
      pending.entered();
      await pending.gate;
      await route.fulfill({ json: { id: "written" } });
    });
    const result = submit(page);
    await pending.started;
    await page.evaluate(({ access, refresh }) => {
      sessionStorage.setItem("observal_access_token", access);
      localStorage.setItem("observal_refresh_token", refresh);
    }, { access: jwt(nextActor, "new-access"), refresh: jwt(nextActor, "new-refresh") });
    pending.release();
    expect(await result).toBe(nextActor === "actor-a" ? "ok" : "AuthContextChanged");
    expect(writes).toBe(1);
    await page.unrouteAll({ behavior: "wait" });
  }
});

test("same-actor 401 supersession retries once with current bearer", async ({ page }) => {
  for (const phase of ["response", "refresh"]) {
    await seed(page, A, AR);
    const pending = barrier();
    let writes = 0;
    let refreshes = 0;
    const rotated = jwt("actor-a", `rotated-${phase}`);
    await page.route("**/api/v1/feedback", async (route) => {
      writes++;
      if (writes === 1) {
        if (phase === "response") { pending.entered(); await pending.gate; }
        await route.fulfill({ status: 401 });
      } else {
        expect(route.request().headers().authorization).toBe(`Bearer ${rotated}`);
        await route.fulfill({ json: { id: "written" } });
      }
    });
    await page.route("**/api/v1/auth/token/refresh", async (route) => {
      refreshes++;
      pending.entered(); await pending.gate;
      await route.fulfill({ status: 401 }); // stale refresh must not expire the rotated session
    });
    const result = submit(page);
    await pending.started;
    await page.evaluate(({ rotated, refresh }) => {
      sessionStorage.setItem("observal_access_token", rotated);
      localStorage.setItem("observal_refresh_token", refresh);
    }, { rotated, refresh: jwt("actor-a", `refresh-${phase}`) });
    pending.release();
    expect(await result).toBe("ok");
    expect(writes).toBe(2);
    expect(refreshes).toBe(phase === "response" ? 0 : 1);
    await page.unrouteAll({ behavior: "wait" });
  }
});

test("direct authenticated contexts accept same-actor rotation but not a missing access token", async ({ page }) => {
  await seed(page, A, AR);
  expect(await page.evaluate((rotated) => {
    const result = (check: () => void) => {
      try { check(); return "ok"; } catch (error) { return (error as Error).name; }
    };
    return import("/src/lib/api.ts").then(({ authenticatedReadContext, authenticatedRequestContext }) => {
      const read = authenticatedReadContext();
      const write = authenticatedRequestContext();
      sessionStorage.setItem("observal_access_token", rotated);
      const sameActor = [result(read.check), result(write.check)];
      sessionStorage.removeItem("observal_access_token");
      const missing = result(read.check);
      sessionStorage.setItem("observal_access_token", "x." + btoa(JSON.stringify({ sub: "actor-b" })) + ".y");
      return [...sameActor, missing, result(write.check)];
    });
  }, jwt("actor-a", "rotated-access"))).toEqual(["ok", "ok", "AuthContextChanged", "AuthContextChanged"]);
});

for (const kind of ["agents", "mcps"]) {
  test(`late ${kind} coauthor GET survives same-actor access rotation`, async ({ page }) => {
    await seed(page, A, AR);
    await expect(page.getByText(/Sign in (to your account|or browse the public registry)/)).toBeVisible();
    await page.evaluate(() => localStorage.setItem("observal_user_role", "user"));
    await page.route("**/api/v1/config/public", (route) => route.fulfill({ json: { public_registry_enabled: true } }));
    await page.route("**/api/v1/auth/whoami", (route) => route.fulfill({ json: { id: "actor-a", role: "user" } }));
    const endpoint = kind === "mcps" ? "mcp" : kind;
    await page.route(`**/api/v1/${endpoint}/test`, (route) => route.fulfill({ json: {
      id: "test", name: "Test", slug: "test", status: "approved", version: "1.0.0", user_permission: "owner",
    } }));
    const pending = barrier();
    await page.route(`**/api/v1/${endpoint}/test/co-authors`, async (route) => {
      expect(route.request().headers().authorization).toBe(`Bearer ${A}`);
      pending.entered(); await pending.gate;
      await route.fulfill({ json: [{ id: "private", email: "private-a@example.com" }] });
    });
    await page.goto(kind === "agents" ? "/agents/test" : "/components/test?type=mcp", { waitUntil: "commit" });
    await pending.started;
    await page.evaluate(({ access, refresh }) => {
      sessionStorage.setItem("observal_access_token", access);
      localStorage.setItem("observal_refresh_token", refresh);
    }, { access: jwt("actor-a", "rotated-access"), refresh: jwt("actor-a", "rotated-refresh") });
    pending.release();
    await expect(page.getByText("private-a@example.com")).toBeVisible();
    expect(await page.evaluate(() => sessionStorage.getItem("observal_tab_actor"))).toBe("actor-a");
  });

  test(`late ${kind} coauthor GET cannot restore emails after A to B quarantine`, async ({ page }) => {
    await seed(page, A, AR);
    await expect(page.getByText(/Sign in (to your account|or browse the public registry)/)).toBeVisible();
    await page.evaluate(() => localStorage.setItem("observal_user_role", "user"));
    await page.route("**/api/v1/config/public", (route) => route.fulfill({ json: { public_registry_enabled: true } }));
    await page.route("**/api/v1/auth/whoami", (route) => route.fulfill({ json: { id: "actor-a", role: "user" } }));
    const endpoint = kind === "mcps" ? "mcp" : kind;
    await page.route(`**/api/v1/${endpoint}/test`, (route) => route.fulfill({ json: {
      id: "test", name: "Test", slug: "test", status: "approved", version: "1.0.0", user_permission: "owner",
    } }));
    const pending = barrier();
    const completed = barrier();
    await page.route(`**/api/v1/${endpoint}/test/co-authors`, async (route) => {
      expect(route.request().headers().authorization).toBe(`Bearer ${A}`);
      pending.entered(); await pending.gate;
      await route.fulfill({ json: [{ id: "private", email: "private-a@example.com" }] });
      completed.entered();
    });
    await page.goto(kind === "agents" ? "/agents/test" : "/components/test?type=mcp", { waitUntil: "commit" });
    await pending.started;
    await page.evaluate((br) => localStorage.setItem("observal_refresh_token", br), BR);
    pending.release();
    await completed.started;
    await expect.poll(() => page.evaluate(() => sessionStorage.getItem("observal_access_token"))).toBeNull();
    await expect(page.getByText("private-a@example.com")).toHaveCount(0);
    await page.unrouteAll({ behavior: "wait" });
  });
}

const exportHtml = (page: Page) => page.evaluate(async () => {
  const { insights } = await import("/src/lib/api.ts");
  return insights.exportHtml("agent", "report").then(() => "ok", (err: Error) => err.name);
});

test("HTML export 200 downloads once after same-actor rotation during response or blob decoding", async ({ page }) => {
  for (const phase of ["response", "blob"]) {
    await seed(page, A, AR);
    const pending = barrier();
    let calls = 0;
    let downloads = 0;
    page.on("download", () => { downloads++; });
    await page.route("**/api/v1/agents/*/insights/reports/*/export/html", async (route) => {
      calls++;
      expect(route.request().headers().authorization).toBe(`Bearer ${A}`);
      if (phase === "response") { pending.entered(); await pending.gate; }
      await route.fulfill({ body: "private-A", contentType: "text/html" });
    });
    if (phase === "blob") {
      await page.evaluate(() => {
        const original = Response.prototype.blob;
        Response.prototype.blob = async function (...args) {
          if (this.url.includes("/export/html")) {
            (window as any).blobEntered = true;
            await new Promise<void>((resolve) => { (window as any).releaseBlob = resolve; });
          }
          return original.apply(this, args);
        };
      });
    }
    const result = exportHtml(page);
    if (phase === "response") await pending.started;
    else await expect.poll(() => page.evaluate(() => (window as any).blobEntered ?? false)).toBe(true);
    await page.evaluate(({ access, refresh }) => {
      sessionStorage.setItem("observal_access_token", access);
      localStorage.setItem("observal_refresh_token", refresh);
      (window as any).releaseBlob?.();
    }, { access: jwt("actor-a", `export-${phase}`), refresh: jwt("actor-a", `export-refresh-${phase}`) });
    if (phase === "response") pending.release();
    expect(await result).toBe("ok");
    await expect.poll(() => downloads).toBe(1);
    expect(calls).toBe(1);
    await page.unrouteAll({ behavior: "wait" });
    await page.reload();
  }
});

test("HTML export 200 cannot download A after B switches during response or blob decoding", async ({ page }) => {
  for (const phase of ["response", "blob"]) {
    await seed(page, A, AR);
    const pending = barrier();
    let calls = 0;
    let downloads = 0;
    page.on("download", () => { downloads++; });
    await page.route("**/api/v1/agents/*/insights/reports/*/export/html", async (route) => {
      calls++;
      expect(route.request().headers().authorization).toBe(`Bearer ${A}`);
      if (phase === "response") { pending.entered(); await pending.gate; }
      await route.fulfill({ body: "private-A", contentType: "text/html" });
    });
    if (phase === "blob") {
      await page.evaluate(() => {
        const original = Response.prototype.blob;
        Response.prototype.blob = async function (...args) {
          if (this.url.includes("/export/html")) {
            (window as any).blobEntered = true;
            await new Promise<void>((resolve) => { (window as any).releaseBlob = resolve; });
          }
          return original.apply(this, args);
        };
      });
    }
    const result = exportHtml(page);
    if (phase === "response") await pending.started;
    else await expect.poll(() => page.evaluate(() => (window as any).blobEntered ?? false)).toBe(true);
    await page.evaluate(({ b, br }) => {
      sessionStorage.setItem("observal_access_token", b);
      localStorage.setItem("observal_refresh_token", br);
      (window as any).releaseBlob?.();
    }, { b: B, br: BR });
    if (phase === "response") pending.release();
    expect(await result).toBe("AuthContextChanged");
    expect(calls).toBe(1);
    expect(downloads).toBe(0);
    expect(await page.evaluate(() => sessionStorage.getItem("observal_access_token"))).toBeNull();
    await page.unrouteAll({ behavior: "wait" });
    await page.reload();
  }
});

test("HTML export 401 response retries rotated A bearer but never B bearer", async ({ page }) => {
  for (const actor of ["actor-a", "actor-b"]) {
    await seed(page, A, AR);
    const pending = barrier();
    const rotated = jwt(actor, "export-response-access");
    const refresh = jwt(actor, "export-response-refresh");
    const bearers: Array<string | undefined> = [];
    let refreshes = 0;
    let downloads = 0;
    page.on("download", () => { downloads++; });
    await page.route("**/api/v1/agents/*/insights/reports/*/export/html", async (route) => {
      bearers.push(route.request().headers().authorization);
      if (bearers.length === 1) { pending.entered(); await pending.gate; }
      await route.fulfill(bearers.length === 1 ? { status: 401 } : { body: "private-A", contentType: "text/html" });
    });
    await page.route("**/api/v1/auth/token/refresh", async (route) => {
      refreshes++;
      await route.fulfill({ status: 401 });
    });
    const result = exportHtml(page);
    await pending.started;
    await page.evaluate(({ access, refresh }) => {
      sessionStorage.setItem("observal_access_token", access);
      localStorage.setItem("observal_refresh_token", refresh);
    }, { access: rotated, refresh });
    pending.release();
    expect(await result).toBe(actor === "actor-a" ? "ok" : "AuthContextChanged");
    expect(bearers).toEqual(actor === "actor-a" ? [`Bearer ${A}`, `Bearer ${rotated}`] : [`Bearer ${A}`]);
    expect(refreshes).toBe(0);
    if (actor === "actor-a") await expect.poll(() => downloads).toBe(1);
    else expect(downloads).toBe(0);
    await page.unrouteAll({ behavior: "wait" });
  }
});

test("HTML export 401 retries a superseded A refresh with the winner's bearer, never B's", async ({ page }) => {
  for (const actor of ["actor-a", "actor-b"]) {
    await seed(page, A, AR);
    const pending = barrier();
    const rotated = jwt(actor, "export-winner-access");
    const refresh = jwt(actor, "export-winner-refresh");
    const bearers: Array<string | undefined> = [];
    let refreshes = 0;
    let downloads = 0;
    page.on("download", () => { downloads++; });
    await page.route("**/api/v1/agents/*/insights/reports/*/export/html", async (route) => {
      bearers.push(route.request().headers().authorization);
      await route.fulfill(bearers.length === 1 ? { status: 401 } : { body: "private-A", contentType: "text/html" });
    });
    await page.route("**/api/v1/auth/token/refresh", async (route) => {
      refreshes++;
      pending.entered(); await pending.gate;
      await route.fulfill({ status: 401 }); // winner rotated shared refresh before stale response
    });
    const result = exportHtml(page);
    await pending.started;
    await page.evaluate(({ access, refresh }) => {
      sessionStorage.setItem("observal_access_token", access);
      localStorage.setItem("observal_refresh_token", refresh);
    }, { access: rotated, refresh });
    pending.release();
    expect(await result).toBe(actor === "actor-a" ? "ok" : "AuthContextChanged");
    expect(bearers).toEqual(actor === "actor-a" ? [`Bearer ${A}`, `Bearer ${rotated}`] : [`Bearer ${A}`]);
    expect(refreshes).toBe(1);
    if (actor === "actor-a") await expect.poll(() => downloads).toBe(1);
    else expect(downloads).toBe(0);
    await page.unrouteAll({ behavior: "wait" });
  }
});

test("HTML export superseded 401 without a new A bearer cannot replay", async ({ page }) => {
  await seed(page, A, AR);
  const pending = barrier();
  let exports = 0;
  let refreshes = 0;
  let downloads = 0;
  page.on("download", () => { downloads++; });
  await page.route("**/api/v1/agents/*/insights/reports/*/export/html", (route) => {
    exports++;
    return route.fulfill({ status: 401 });
  });
  await page.route("**/api/v1/auth/token/refresh", async (route) => {
    refreshes++;
    pending.entered(); await pending.gate;
    await route.fulfill({ status: 401 });
  });
  const result = exportHtml(page);
  await pending.started;
  await page.evaluate((refresh) => localStorage.setItem("observal_refresh_token", refresh), jwt("actor-a", "export-winner-refresh"));
  pending.release();
  expect(await result).toBe("AuthContextChanged");
  expect([exports, refreshes, downloads]).toEqual([1, 1, 0]);
  expect(await page.evaluate(() => sessionStorage.getItem("observal_access_token"))).toBe(A);
});

test("HTML export mismatch never retries or downloads", async ({ page }) => {
  await seed(page, A, BR);
  let calls = 0;
  await page.route("**/api/v1/agents/*/insights/reports/*/export/html", async (route) => {
    calls++;
    await route.fulfill({ status: 401 });
  });
  const result = await page.evaluate(async () => {
    const { insights } = await import("/src/lib/api.ts");
    return insights.exportHtml("agent", "report").then(() => "ok", (err: Error) => err.name);
  });
  expect(result).toBe("AuthContextChanged");
  expect(calls).toBe(0);
});
