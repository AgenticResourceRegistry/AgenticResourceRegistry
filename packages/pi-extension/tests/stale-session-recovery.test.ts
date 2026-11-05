// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-License-Identifier: Apache-2.0

// Startup recovery finds unfinished sessions in Pi's per-project session
// directory, both from getSessionDir() and from the cwd fallback.

import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as http from "node:http";
import * as os from "node:os";
import * as path from "node:path";

const home = fs.mkdtempSync(path.join(os.tmpdir(), "observal-pi-recovery-"));
process.env.HOME = home;
const observalDir = path.join(home, ".observal");
fs.mkdirSync(observalDir, { recursive: true });

// Pi stores sessions for cwd /work/repo under sessions/--work-repo--/.
const projectDir = path.join(home, ".pi", "agent", "sessions", "--work-repo--");
fs.mkdirSync(projectDir, { recursive: true });
const staleFile = path.join(projectDir, "2026-10-18T10-00-00-000Z_stale-session.jsonl");
fs.writeFileSync(staleFile, `${JSON.stringify({ type: "session", id: "stale-session", cwd: "/work/repo" })}\n`);
fs.writeFileSync(
  path.join(observalDir, "sync_state.json"),
  JSON.stringify({ "stale-session": { offset: 0, line_count: 0, finalized: false } }),
);

const recovered: string[] = [];
const server = http.createServer((request, response) => {
  response.setHeader("Content-Type", "application/json");
  if (request.method === "GET") {
    response.end(JSON.stringify({ acknowledged_line: -1, acknowledged_offset: 0 }));
    return;
  }
  const chunks: Buffer[] = [];
  request.on("data", (chunk) => chunks.push(chunk));
  request.on("end", () => {
    const payload = JSON.parse(Buffer.concat(chunks).toString("utf-8"));
    if (request.url === "/api/v1/ingest/session") {
      recovered.push(payload.session_id);
      const last = payload.start_offset + payload.lines.length - 1;
      response.end(JSON.stringify({ acknowledged_line: last, acknowledged_offset: payload.end_byte_offsets.at(-1) ?? 0 }));
      return;
    }
    response.end(JSON.stringify({ hash: payload.hash }));
  });
});
await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
const address = server.address();
assert(address && typeof address === "object");
fs.writeFileSync(
  path.join(observalDir, "config.json"),
  JSON.stringify({ server_url: `http://127.0.0.1:${address.port}`, access_token: "token", user_id: "user" }),
);

async function startAndRecover(sessionManager: Record<string, () => string>): Promise<void> {
  const handlers = new Map();
  const extension = await import(`../extensions/observal.ts?test=${Date.now()}-${Math.random()}`);
  extension.default({ on: (name: string, handler: unknown) => handlers.set(name, handler), registerCommand() {} });
  const currentFile = path.join(projectDir, "2026-10-19T10-00-00-000Z_current.jsonl");
  fs.writeFileSync(currentFile, `${JSON.stringify({ type: "session", id: "current", cwd: "/work/repo" })}\n`);
  const context = {
    cwd: "/work/repo",
    hasUI: false,
    sessionManager: { getSessionFile: () => currentFile, getSessionId: () => "current", ...sessionManager },
  };
  await handlers.get("session_start")({ reason: "startup" }, context);
  for (let i = 0; i < 50 && !recovered.includes("stale-session"); i++) {
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}

try {
  await startAndRecover({ getSessionDir: () => projectDir });
  assert.deepEqual(recovered, ["stale-session"], "recovery reads getSessionDir() as the project directory");

  recovered.length = 0;
  fs.writeFileSync(
    path.join(observalDir, "sync_state.json"),
    JSON.stringify({ "stale-session": { offset: 0, line_count: 0, finalized: false } }),
  );
  await startAndRecover({});
  assert.deepEqual(recovered, ["stale-session"], "without getSessionDir(), recovery derives Pi's directory name");
} finally {
  server.close();
}
