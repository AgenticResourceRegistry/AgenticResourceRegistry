// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { Page } from "@playwright/test";

/** Base URL for the Observal API */
export const API_BASE = process.env.API_BASE ?? "http://localhost";

type TestLogin = {
  access_token: string;
  refresh_token: string;
  user: { role: string; name: string; email: string; username?: string | null; avatar_url?: string | null };
};

let _cachedLogin: TestLogin | null = null;

/** Cache the complete server-issued pair so browser fixtures use real auth state. */
async function getLogin(): Promise<TestLogin> {
  if (_cachedLogin) return _cachedLogin;
  const email = process.env.DEMO_ADMIN_EMAIL ?? "admin@demo.example";
  const password = process.env.DEMO_ADMIN_PASSWORD ?? "admin-changeme";
  for (let attempt = 0; attempt < 5; attempt++) {
    const res = await fetch(`${API_BASE}/api/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (res.status === 429) {
      await new Promise((r) => setTimeout(r, 15_000));
      continue;
    }
    if (!res.ok) throw new Error(`Login failed (${res.status}): ${(await res.text()).slice(0, 200)}`);
    const data = await res.json();
    if (data.access_token && data.refresh_token && data.user) {
      _cachedLogin = data as TestLogin;
      return _cachedLogin;
    }
    throw new Error(`Login response missing credentials or user (${res.status})`);
  }
  throw new Error("Login failed after retries");
}

/** Get the server-issued access token for API-only fixtures. */
export async function getAccessToken(): Promise<string> {
  return (await getLogin()).access_token;
}

/** @deprecated Use getAccessToken */
export const getApiKey = getAccessToken;

/** Initialize browser auth using the same atomic login path as the product. */
export async function loginToWebUI(page: Page) {
  const login = await getLogin();
  await page.goto("/");
  const committed = await page.evaluate(async (data) => {
    const { commitLogin, loginStart } = await import("/src/lib/api.ts");
    return commitLogin(data, loginStart());
  }, login);
  if (!committed) throw new Error("Browser rejected server-issued login pair");
  await page.reload();
}

/** Wait for API to be healthy */
export async function waitForAPI() {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    const remaining = deadline - Date.now();
    try {
      const response = await fetch(`${API_BASE}/health`, {
        signal: AbortSignal.timeout(Math.min(5000, remaining)),
      });
      if (response.ok) return;
    } catch {
      // Retry until the API reaches a healthy state.
    }
    const delay = Math.min(2000, deadline - Date.now());
    if (delay > 0) {
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
  }
  throw new Error("API not healthy after 60s");
}

/** Send a raw OTLP trace payload to simulate Kiro telemetry */
export async function sendKiroOTLPTrace(payload: object) {
  const res = await fetch(`${API_BASE}/v1/traces`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}

/** Send a raw OTLP log payload to simulate Kiro telemetry */
export async function sendKiroOTLPLog(payload: object) {
  const res = await fetch(`${API_BASE}/v1/logs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}

/** Send a hook event payload to simulate Kiro hook firing */
export async function sendKiroHookEvent(payload: object) {
  const res = await fetch(`${API_BASE}/api/v1/telemetry/hooks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}

/** Build a realistic Kiro OTLP resourceSpans payload */
export function buildKiroOTLPTracePayload(options: {
  traceId: string;
  spanId: string;
  sessionId?: string;
  spanName: string;
  model?: string;
  inputTokens?: number;
  outputTokens?: number;
}) {
  return {
    resourceSpans: [
      {
        resource: {
          attributes: [
            { key: "service.name", value: { stringValue: "kiro" } },
            {
              key: "telemetry.sdk.name",
              value: { stringValue: "kiro-cli" },
            },
            ...(options.sessionId
              ? [
                  {
                    key: "session.id",
                    value: { stringValue: options.sessionId },
                  },
                ]
              : []),
          ],
        },
        scopeSpans: [
          {
            scope: { name: "kiro.telemetry" },
            spans: [
              {
                traceId: options.traceId,
                spanId: options.spanId,
                name: options.spanName,
                kind: 3, // CLIENT
                startTimeUnixNano: String(Date.now() * 1_000_000),
                endTimeUnixNano: String((Date.now() + 500) * 1_000_000),
                status: { code: 1 },
                attributes: [
                  ...(options.model
                    ? [
                        {
                          key: "gen_ai.request.model",
                          value: { stringValue: options.model },
                        },
                      ]
                    : []),
                  ...(options.inputTokens != null
                    ? [
                        {
                          key: "gen_ai.usage.input_tokens",
                          value: { intValue: String(options.inputTokens) },
                        },
                      ]
                    : []),
                  ...(options.outputTokens != null
                    ? [
                        {
                          key: "gen_ai.usage.output_tokens",
                          value: { intValue: String(options.outputTokens) },
                        },
                      ]
                    : []),
                ],
                events: [],
              },
            ],
          },
        ],
      },
    ],
  };
}

/** Build a realistic Kiro OTLP resourceLogs payload */
export function buildKiroOTLPLogPayload(options: {
  sessionId: string;
  promptId: string;
  eventName: string;
  body?: string;
  attributes?: Record<string, string>;
}) {
  const attrs = Object.entries(options.attributes ?? {}).map(([key, val]) => ({
    key,
    value: { stringValue: val },
  }));

  return {
    resourceLogs: [
      {
        resource: {
          attributes: [
            { key: "service.name", value: { stringValue: "kiro" } },
            {
              key: "session.id",
              value: { stringValue: options.sessionId },
            },
          ],
        },
        scopeLogs: [
          {
            scope: { name: "kiro.telemetry" },
            logRecords: [
              {
                timeUnixNano: String(Date.now() * 1_000_000),
                severityNumber: 9,
                body: { stringValue: options.body ?? "" },
                attributes: [
                  {
                    key: "event.name",
                    value: { stringValue: options.eventName },
                  },
                  {
                    key: "session.id",
                    value: { stringValue: options.sessionId },
                  },
                  {
                    key: "prompt.id",
                    value: { stringValue: options.promptId },
                  },
                  ...attrs,
                ],
              },
            ],
          },
        ],
      },
    ],
  };
}

let _superToken: string | null = null;

/** Super admin token (cached per worker). Overriding a review's gate needs this role. */
async function getSuperToken(): Promise<string> {
  if (_superToken) return _superToken;
  const email = process.env.DEMO_SUPER_EMAIL ?? "super@demo.example";
  const password = process.env.DEMO_SUPER_PASSWORD ?? "super-changeme";
  for (let attempt = 0; attempt < 5; attempt++) {
    const res = await fetch(`${API_BASE}/api/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (res.status === 429) {
      await new Promise((r) => setTimeout(r, 15_000));
      continue;
    }
    if (!res.ok) throw new Error(`Super admin login failed (${res.status}): ${(await res.text()).slice(0, 200)}`);
    _superToken = (await res.json()).access_token as string;
    return _superToken;
  }
  throw new Error("Super admin login failed after retries");
}

/**
 * Publish a submitted registry item through the PR-style review API.
 * Authors cannot count their own approval, so this uses the audited super admin override path. Returns the review number.
 */
export async function publishSubject(
  subjectType: "agent" | "mcp" | "skill" | "hook" | "prompt" | "sandbox",
  subjectId: string,
  reason = "e2e approval",
): Promise<number> {
  const headers = { Authorization: `Bearer ${await getSuperToken()}`, "Content-Type": "application/json" };
  let cursor: number | undefined;
  for (let page = 0; page < 10; page++) {
    const query = `state=all&type=${subjectType}&limit=100${cursor ? `&cursor=${cursor}` : ""}`;
    const list = await fetch(`${API_BASE}/api/v1/reviews?${query}`, { headers });
    if (!list.ok) throw new Error(`GET /reviews: ${list.status} ${await list.text()}`);
    const body = await list.json();
    const found = body.items.find((item: { subject_id: string }) => item.subject_id === subjectId);
    if (found) {
      const published = await fetch(`${API_BASE}/api/v1/reviews/${found.number}/publish`, {
        method: "POST", headers, body: JSON.stringify({ override_reason: reason }),
      });
      if (!published.ok) throw new Error(`publish #${found.number}: ${published.status} ${await published.text()}`);
      return found.number as number;
    }
    if (!body.next_cursor) break;
    cursor = body.next_cursor;
  }
  throw new Error(`No review found for ${subjectType} ${subjectId}`);
}

/** The review number for an item, without deciding it. */
export async function reviewNumber(subjectType: string, subjectId: string): Promise<number> {
  const headers = { Authorization: `Bearer ${await getAccessToken()}` };
  const list = await fetch(`${API_BASE}/api/v1/reviews?state=all&type=${subjectType}&limit=100`, { headers });
  const found = (await list.json()).items.find((item: { subject_id: string }) => item.subject_id === subjectId);
  if (!found) throw new Error(`No review found for ${subjectType} ${subjectId}`);
  return found.number as number;
}
