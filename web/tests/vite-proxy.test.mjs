// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { loadConfigFromFile } from "vite";

const configPath = fileURLToPath(new URL("../vite.config.ts", import.meta.url));

for (const [name, override, target] of [
  ["local default", undefined, "http://localhost:8000"],
  ["CI override", "http://localhost:80", "http://localhost:80"],
]) {
  test(`Vite proxies every backend path to the ${name}`, async () => {
    const previous = process.env.OBSERVAL_VITE_PROXY_TARGET;
    try {
      if (override === undefined) delete process.env.OBSERVAL_VITE_PROXY_TARGET;
      else process.env.OBSERVAL_VITE_PROXY_TARGET = override;
      const loaded = await loadConfigFromFile({ command: "serve", mode: "development" }, configPath);
      assert.ok(loaded);
      assert.deepEqual(Object.fromEntries(Object.entries(loaded.config.server.proxy).map(([path, config]) => [path, config.target])), {
        "/api": target,
        "/.well-known/ard.json": target,
        "/.well-known/ai-catalog.json": target,
        "/health": target,
      });
    } finally {
      if (previous === undefined) delete process.env.OBSERVAL_VITE_PROXY_TARGET;
      else process.env.OBSERVAL_VITE_PROXY_TARGET = previous;
    }
  });
}
