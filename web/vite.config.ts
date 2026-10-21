// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { TanStackRouterVite } from "@tanstack/router-plugin/vite";
import { resolve } from "path";
import { readFileSync } from "fs";

const pkg = JSON.parse(readFileSync(resolve(__dirname, "package.json"), "utf-8"));
// Playwright's CI-only Vite server proxies through the disposable stack's load balancer.
const proxyTarget = process.env.OBSERVAL_VITE_PROXY_TARGET || "http://localhost:8000";

export default defineConfig({
  plugins: [TanStackRouterVite({ routesDirectory: "./src/routes" }), react()],
  resolve: {
    alias: {
      "@": resolve(__dirname, "src"),
    },
  },
  define: {
    "import.meta.env.VITE_APP_VERSION": JSON.stringify(pkg.version),
  },
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            { name: "vendor-react", test: /node_modules[\\/](react|react-dom|@tanstack[\\/]react-router|@tanstack[\\/]react-query)[\\/]/ },
            { name: "vendor-ui", test: /node_modules[\\/]@radix-ui[\\/]react-(dialog|dropdown-menu|popover|select|tabs|tooltip)[\\/]/ },
            { name: "vendor-charts", test: /node_modules[\\/]recharts[\\/]/ },
          ],
        },
      },
    },
  },
  server: {
    port: 3000,
    proxy: {
      "/api": {
        target: proxyTarget,
        changeOrigin: true,
        ws: true,
      },
      "/.well-known/ard.json": {
        target: proxyTarget,
        changeOrigin: true,
      },
      "/.well-known/ai-catalog.json": {
        target: proxyTarget,
        changeOrigin: true,
      },
      "/health": {
        target: proxyTarget,
        changeOrigin: true,
      },
    },
  },
});
