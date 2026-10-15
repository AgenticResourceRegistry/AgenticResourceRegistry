// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-License-Identifier: Apache-2.0
// Web dev server on :13000 proxied to the demo API on :18000 (run from web/).
import { defineConfig, mergeConfig } from "vite";
import base from "../../web/vite.config";

const api = { target: "http://127.0.0.1:18000", changeOrigin: true };
export default mergeConfig(
  base,
  defineConfig({ server: { port: 13000, strictPort: true, proxy: { "/api": api, "/health": api } } }),
);
