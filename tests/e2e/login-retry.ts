// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { API_BASE } from "./helpers";

/** Login without synthetic credentials; wait for the shared IP bucket to reopen. */
export async function loginWithRetry(email: string, password: string): Promise<string> {
  const deadline = Date.now() + 120_000;
  while (true) {
    const response = await fetch(`${API_BASE}/api/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (response.status === 429 && Date.now() + 15_000 < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 15_000));
      continue;
    }
    if (!response.ok) throw new Error(`Login for ${email} failed: ${response.status} ${await response.text()}`);
    const body = await response.json();
    if (!body.access_token) throw new Error(`Login for ${email} returned no access token`);
    return body.access_token as string;
  }
}
