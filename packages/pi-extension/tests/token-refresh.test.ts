// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-License-Identifier: Apache-2.0

// A rejected token is refreshed once and the batch retried, instead of every
// batch staying in the outbox until the CLI happens to refresh the config.

import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as http from "node:http";
import * as os from "node:os";
import * as path from "node:path";

const home = fs.mkdtempSync(path.join(os.tmpdir(), "observal-pi-refresh-"));
process.env.HOME = home;
const observalDir = path.join(home, ".observal");
fs.mkdirSync(observalDir, { recursive: true });
const sessionFile = path.join(home, "session.jsonl");
fs.writeFileSync(sessionFile, `${JSON.stringify({ type: "message", index: 0 })}\n`);

let ingested = 0;
let refreshes = 0;
const server = http.createServer((request, response) => {
  response.setHeader("Content-Type", "application/json");
  const chunks: Buffer[] = [];
  request.on("data", (chunk) => chunks.push(chunk));
  request.on("end", () => {
    const body = chunks.length ? JSON.parse(Buffer.concat(chunks).toString("utf-8")) : {};
    if (request.url === "/api/v1/auth/token/refresh") {
      assert.equal(request.headers.authorization, undefined);
      refreshes += 1;
      if (body.refresh_token !== "refresh-1" || refreshes > 1) {
        response.statusCode = 401; // single use, as on the server
        response.end("{}");
        return;
      }
      response.end(JSON.stringify({ access_token: "fresh", refresh_token: "refresh-2" }));
      return;
    }
    if (request.headers.authorization !== "Bearer fresh") {
      response.statusCode = 401;
      response.end("{}");
      return;
    }
    if (request.method === "GET") {
      response.end(JSON.stringify({ acknowledged_line: -1, acknowledged_offset: 0 }));
      return;
    }
    if (request.url === "/api/v1/layer-snapshots") {
      response.end(JSON.stringify({ hash: body.hash }));
      return;
    }
    ingested += 1;
    const last = body.start_offset + body.lines.length - 1;
    response.end(JSON.stringify({ acknowledged_line: last, acknowledged_offset: body.end_byte_offsets?.at(-1) ?? 0 }));
  });
});
await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
const address = server.address();
assert(address && typeof address === "object");
const configPath = path.join(observalDir, "config.json");
const serverUrl = `http://127.0.0.1:${address.port}`;
// A hooks token from an earlier login, which this server no longer accepts.
fs.writeFileSync(
  configPath,
  JSON.stringify({ server_url: serverUrl, api_key: "stale", access_token: "old", refresh_token: "refresh-1", user_id: "user" }),
);

const handlers = new Map<string, (event: unknown, ctx: unknown) => Promise<void>>();
const pi = { on: (name: string, handler: any) => handlers.set(name, handler), registerCommand() {} };
const extension = await import(`../extensions/observal.ts?refresh=${Date.now()}`);
extension.default(pi);
const context = {
  cwd: home,
  hasUI: false,
  sessionManager: { getSessionFile: () => sessionFile, getSessionId: () => "pi-refresh-session" },
};
await handlers.get("session_start")!({ reason: "start" }, context);
await handlers.get("session_shutdown")!({}, context);

assert.equal(ingested, 1);
assert.equal(refreshes, 1); // the layer upload and the batch run concurrently and share one refresh
const saved = JSON.parse(fs.readFileSync(configPath, "utf-8"));
assert.equal(saved.access_token, "fresh");
assert.equal(saved.refresh_token, "refresh-2");
assert.equal(saved.api_key, undefined); // the rejected hooks token is not tried again
const outbox = path.join(observalDir, "pi_session_outbox");
assert.deepEqual(fs.existsSync(outbox) ? fs.readdirSync(outbox) : [], []);

server.close();
fs.rmSync(home, { recursive: true, force: true });
console.log("token refresh ok");
