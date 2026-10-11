// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { Page } from "@playwright/test";

/** Base URL for the Observal API */
export const API_BASE = process.env.API_BASE ?? "http://localhost";

let _cachedToken: string | null = null;

/** Get an access token by logging in with demo admin credentials (cached per worker) */
export async function getAccessToken(): Promise<string> {
  if (_cachedToken) return _cachedToken;
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
    if (data.access_token) {
      _cachedToken = data.access_token;
      return data.access_token as string;
    }
    throw new Error(`Login response missing access token (${res.status})`);
  }
  throw new Error("Login failed after retries");
}

/** @deprecated Use getAccessToken */
export const getApiKey = getAccessToken;

/** Login to the web UI by setting localStorage */
export async function loginToWebUI(page: Page) {
  const token = await getAccessToken();
  await page.goto("/");
  await page.evaluate((t) => {
    sessionStorage.setItem("observal_access_token", t);
    localStorage.setItem("observal_user_role", "admin");
  }, token);
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
