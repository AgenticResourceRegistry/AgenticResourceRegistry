// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { defineConfig } from "@playwright/test";

const isCI = !!process.env.CI;

export default defineConfig({
  testDir: "../tests/e2e",
  timeout: 60_000,
  retries: 1,
  use: {
    // CI: Docker stack serves on port 80 via lb
    // Local: Vite dev server on port 3000
    baseURL: isCI ? "http://localhost:80" : "http://localhost:3000",
    headless: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  // Module-level tests use a fresh Vite server in CI; other CI specs still use the Docker stack.
  webServer: {
    command: isCI
      ? "OBSERVAL_VITE_PROXY_TARGET=http://localhost:80 pnpm exec vite --port 3104 --strictPort"
      : "pnpm dev",
    port: isCI ? 3104 : 3000,
    reuseExistingServer: !isCI,
    timeout: 120_000,
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
