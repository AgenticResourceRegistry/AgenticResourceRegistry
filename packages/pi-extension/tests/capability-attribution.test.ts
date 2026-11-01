// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

// The Pi extension must attach capability-lock uses to the session payload the
// same way the Python session push does (capability_lock.for_session): every
// use within this harness and tree, even a hinted one; a hinted use only to its
// own session; a next-session install as a candidate for every session that
// started after it (the server keeps the earliest); other uses by the session's
// start window.

import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as http from "node:http";
import * as os from "node:os";
import * as path from "node:path";

const home = fs.mkdtempSync(path.join(os.tmpdir(), "observal-pi-capabilities-"));
process.env.HOME = home;
const observalDir = path.join(home, ".observal");
fs.mkdirSync(observalDir, { recursive: true });

const projectDir = path.join(home, "project");
fs.mkdirSync(path.join(projectDir, "sub"), { recursive: true });
const sessionFile = path.join(home, "session.jsonl");
fs.writeFileSync(sessionFile, `${JSON.stringify({ type: "message", index: 0 })}\n`);

const now = new Date();
const iso = (deltaMs: number) => new Date(now.getTime() + deltaMs).toISOString().replace(/\.\d{3}Z$/, "Z");
const URN = "urn:air:observal.example.com:skill:0f3c4d5e-6a7b-4c8d-9e0f-1a2b3c4d5e6f";
const lockLines = [
  // In window, same tree, right harness: attributed.
  { ts: iso(-60_000), harness: "pi", cwd: projectDir, identifier: URN, kind: "skill", component_id: "0f3c4d5e", version: "1.0.0", digest: "sha256:old", mode: "context", source: "discover-cli" },
  // Newer use of the same resource wins.
  { ts: iso(-30_000), harness: "pi", cwd: path.join(projectDir, "sub"), identifier: URN, kind: "skill", component_id: "0f3c4d5e", version: "1.1.0", digest: "sha256:new", mode: "context", source: "discover-cli" },
  // Different harness: excluded.
  { ts: iso(-20_000), harness: "kiro", cwd: projectDir, identifier: "urn:air:x:skill:other", kind: "skill", mode: "context", source: "discover-cli" },
  // Different directory: excluded.
  { ts: iso(-20_000), harness: "pi", cwd: path.join(home, "elsewhere"), identifier: "urn:air:x:mcp:far", kind: "mcp", mode: "next-session", source: "install" },
  // Too old: excluded.
  { ts: iso(-3 * 24 * 60 * 60 * 1000), harness: "pi", cwd: projectDir, identifier: "urn:air:x:prompt:old", kind: "prompt", mode: "context", source: "discover-cli" },
  // Exact session hint: attributed regardless of age, within this harness and tree.
  { ts: iso(-5 * 24 * 60 * 60 * 1000), harness: "pi", cwd: projectDir, identifier: "urn:air:x:skill:hinted", kind: "skill", mode: "context", source: "discover-cli", session_hint: "pi-session" },
  // The same session ID from another harness is a different session.
  { ts: iso(-10_000), harness: "cursor", cwd: projectDir, identifier: "urn:air:x:skill:cursor-hinted", kind: "skill", mode: "context", source: "discover-cli", session_hint: "pi-session" },
  // Hinted to ANOTHER session: never window-matched onto this one.
  { ts: iso(-10_000), harness: "pi", cwd: projectDir, identifier: "urn:air:x:skill:theirs", kind: "skill", mode: "context", source: "discover-cli", session_hint: "other-session" },
  // Installed by this very session: takes effect in the NEXT one, not here.
  { ts: iso(-50_000), harness: "pi", cwd: projectDir, identifier: "urn:air:x:mcp:installed-here", kind: "mcp", mode: "next-session", source: "install", session_hint: "pi-session" },
  // Installed (from a plain terminal) before this session started: this is the next session.
  { ts: iso(-120_000), harness: "pi", cwd: projectDir, identifier: "urn:air:x:mcp:installed-before", kind: "mcp", mode: "next-session", source: "install" },
  "not json at all",
];
fs.writeFileSync(
  path.join(observalDir, "capability_lock.jsonl"),
  lockLines.map((line) => (typeof line === "string" ? line : JSON.stringify(line))).join("\n") + "\n",
);

const ingestPayloads: Array<Record<string, any>> = [];
let acknowledgedLine = -1;
let acknowledgedOffset = 0;
const server = http.createServer((request, response) => {
  response.setHeader("Content-Type", "application/json");
  if (request.method === "GET" && request.url?.startsWith("/api/v1/ingest/session/checkpoint")) {
    response.end(JSON.stringify({ session_id: "pi-session", harness: "pi", acknowledged_line: acknowledgedLine, acknowledged_offset: acknowledgedOffset }));
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
    ingestPayloads.push(payload);
    if (payload.lines.length > 0) {
      acknowledgedLine = payload.start_offset + payload.lines.length - 1;
      acknowledgedOffset = payload.end_byte_offsets.at(-1);
    }
    response.end(JSON.stringify({ acknowledged_line: acknowledgedLine, acknowledged_offset: acknowledgedOffset }));
  });
});
await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
const address = server.address();
assert(address && typeof address === "object");
fs.writeFileSync(
  path.join(observalDir, "config.json"),
  JSON.stringify({ server_url: `http://127.0.0.1:${address.port}`, access_token: "token", user_id: "user" }),
);

const handlers = new Map<string, (event: unknown, ctx: unknown) => Promise<void>>();
const pi = { on: (name: string, handler: any) => handlers.set(name, handler), registerCommand() {} };
const extension = await import(`../extensions/observal.ts?capabilities=${Date.now()}`);
extension.default(pi);
const context = {
  cwd: projectDir,
  hasUI: false,
  sessionManager: { getSessionFile: () => sessionFile, getSessionId: () => "pi-session" },
};

delete process.env.OBSERVAL_SESSION_ID;
await handlers.get("session_start")!({ reason: "start" }, context);
assert.equal(process.env.OBSERVAL_SESSION_ID, "pi-session", "session id is exported for the tools Pi runs");
assert.equal(process.env.OBSERVAL_HARNESS, "pi");

await handlers.get("agent_end")!({}, context);
assert.equal(ingestPayloads.length, 1);
const used = ingestPayloads[0].capabilities_used;
assert(Array.isArray(used), "payload carries capabilities_used");
const byId = Object.fromEntries(used.map((u: any) => [u.identifier, u]));
assert.deepEqual(
  Object.keys(byId).sort(),
  [URN, "urn:air:x:skill:hinted", "urn:air:x:mcp:installed-before"].sort(),
);
assert.equal(byId[URN].version, "1.1.0", "latest use of a resource wins");
assert.equal(byId[URN].digest, "sha256:new");
assert.equal(byId[URN].confidence, "window");
assert.equal(byId["urn:air:x:skill:hinted"].confidence, "exact");
assert.equal(byId["urn:air:x:mcp:installed-before"].confidence, "window");
assert.equal(byId["urn:air:x:mcp:installed-before"].mode, "next-session");

// Nothing is claimed locally: attribution writes no shared state.
assert(!fs.existsSync(path.join(observalDir, "capability_claims.json")));

// A later session in the same tree is also a candidate for the install; the
// server keeps the earliest-started one, whatever order they arrive in.
const laterFile = path.join(home, "later.jsonl");
// Two lines: the mock checkpoint acknowledges line 0 for any session.
fs.writeFileSync(laterFile, [0, 1].map((index) => JSON.stringify({ type: "message", index })).join("\n") + "\n");
const later = { ...context, sessionManager: { getSessionFile: () => laterFile, getSessionId: () => "later-session" } };
const before = ingestPayloads.length;
await handlers.get("session_start")!({ reason: "start" }, later);
await handlers.get("agent_end")!({}, later);
assert.equal(ingestPayloads.length, before + 1, "the later session pushed its own payload");
assert.equal(ingestPayloads.at(-1)!.session_id, "later-session");
const laterIds = (ingestPayloads.at(-1)!.capabilities_used ?? []).map((u: any) => u.identifier);
assert(laterIds.includes("urn:air:x:mcp:installed-before"), "every later session is a candidate");
assert(!laterIds.includes("urn:air:x:skill:hinted"), "a hinted use stays with its own session");
await handlers.get("session_start")!({ reason: "start" }, context);

// Without a lock file the field is simply absent.
fs.unlinkSync(path.join(observalDir, "capability_lock.jsonl"));
fs.appendFileSync(sessionFile, `${JSON.stringify({ type: "message", index: 1 })}\n`);
await handlers.get("agent_end")!({}, context);
assert.equal(ingestPayloads.at(-1)!.capabilities_used, undefined);

server.close();
fs.rmSync(home, { recursive: true, force: true });
console.log("capability attribution ok");
