// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-License-Identifier: Apache-2.0

// A Pi session started by a delegation (ADR 0002) belongs to the delegated
// agent, not to whichever agent Pi has selected with /observal-agent.

import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as http from "node:http";
import * as os from "node:os";
import * as path from "node:path";

const home = fs.mkdtempSync(path.join(os.tmpdir(), "observal-pi-delegated-"));
process.env.HOME = home;
const observalDir = path.join(home, ".observal");
fs.mkdirSync(observalDir, { recursive: true });
const sessionFile = path.join(home, "session.jsonl");
fs.writeFileSync(sessionFile, `${JSON.stringify({ type: "message", index: 0 })}\n`);

const payloads: Array<Record<string, any>> = [];
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
    if (request.url === "/api/v1/layer-snapshots") {
      response.end(JSON.stringify({ hash: payload.hash }));
      return;
    }
    payloads.push(payload);
    const last = payload.start_offset + payload.lines.length - 1;
    response.end(JSON.stringify({ acknowledged_line: last, acknowledged_offset: payload.end_byte_offsets?.at(-1) ?? 0 }));
  });
});
await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
const address = server.address();
assert(address && typeof address === "object");
fs.writeFileSync(
  path.join(observalDir, "config.json"),
  JSON.stringify({
    server_url: `http://127.0.0.1:${address.port}`,
    access_token: "token",
    user_id: "user",
    active_agent: { id: "selected-agent", name: "selected" },
  }),
);

process.env.OBSERVAL_DELEGATION_TASK_ID = "0b6f0000-0000-4000-8000-000000000000";
process.env.OBSERVAL_AGENT_ID = "cba33b65-1ac3-443c-ad63-c65e105914ec";
process.env.OBSERVAL_AGENT_VERSION = "1.0.0";

const handlers = new Map<string, (event: unknown, ctx: unknown) => Promise<void>>();
const pi = { on: (name: string, handler: any) => handlers.set(name, handler), registerCommand() {} };
const extension = await import(`../extensions/observal.ts?delegated=${Date.now()}`);
extension.default(pi);
const context = {
  cwd: home,
  hasUI: false,
  sessionManager: { getSessionFile: () => sessionFile, getSessionId: () => "pi-child-session" },
};
await handlers.get("session_start")!({ reason: "start" }, context);
await handlers.get("agent_end")!({}, context);

assert.equal(payloads.length, 1);
assert.equal(payloads[0].agent_id, "cba33b65-1ac3-443c-ad63-c65e105914ec");
assert.equal(payloads[0].agent_version, "1.0.0");

server.close();
fs.rmSync(home, { recursive: true, force: true });
console.log("delegated attribution ok");
