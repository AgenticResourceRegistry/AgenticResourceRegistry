// SPDX-FileCopyrightText: 2026 Observal Contributors
// SPDX-License-Identifier: Apache-2.0

// Exercise the real /agent command and verifier with an isolated home and
// mocked upload endpoint. No user's Pi config, credentials or model is used.
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as http from "node:http";
import * as os from "node:os";
import * as path from "node:path";

const home = fs.mkdtempSync(path.join(os.tmpdir(), "observal-pi-agent-activation-"));
process.env.HOME = home;
const piDir = path.join(home, ".pi", "agent");
const agentsDir = path.join(piDir, "agents");
const project = path.join(home, "project");
const configDir = path.join(home, ".observal");
for (const dir of [piDir, agentsDir, project, configDir]) fs.mkdirSync(dir, { recursive: true });
const writeJson = (file: string, value: unknown) => {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(value));
};
const extension = await import(`../extensions/observal.ts?agent-activation=${Date.now()}`);
const serverEntry = (name: string) => ({ command: "node", args: ["/tmp/isolated-stdio-fixture.js", name] });
const one = serverEntry("one");
const two = serverEntry("two");
const originalLegacy = { mcpServers: { builtIn: serverEntry("builtin") } };
const originalAdapter = { mcpServers: { existing: serverEntry("existing") } };
writeJson(path.join(piDir, "mcp.json"), originalLegacy);
writeJson(path.join(piDir, "mcp-adapter.json"), originalAdapter);
writeJson(path.join(agentsDir, "one", "mcp.json"), { mcpServers: { "one-probe": one } });
writeJson(path.join(agentsDir, "two", "mcp.json"), { mcpServers: { "two-probe": two } });
const manifest = path.join(piDir, "npm", "node_modules", "pi-mcp-adapter", "package.json");
writeJson(manifest, { version: "3.1.0" });

const uploads: any[] = [];
const ingests: any[] = [];
let refuseUploads = false;
let refuseIngest = false;
const server = http.createServer((request, response) => {
  const chunks: Buffer[] = [];
  request.on("data", (chunk) => chunks.push(chunk));
  request.on("end", () => {
    response.setHeader("Content-Type", "application/json");
    if (request.url === "/api/v1/layer-snapshots") {
      const snapshot = JSON.parse(Buffer.concat(chunks).toString("utf-8"));
      uploads.push(snapshot);
      if (refuseUploads) response.statusCode = 503;
      response.end(JSON.stringify({ hash: snapshot.hash }));
    } else if (request.url === "/api/v1/ingest/session") {
      if (refuseIngest) {
        response.statusCode = 503;
        response.end("{}");
        return;
      }
      const payload = JSON.parse(Buffer.concat(chunks).toString("utf-8"));
      ingests.push(payload);
      response.end(JSON.stringify({ acknowledged_line: payload.start_offset + payload.lines.length - 1,
        acknowledged_offset: payload.end_byte_offsets?.at(-1) ?? payload.total_offset ?? 0 }));
    } else {
      response.statusCode = 404;
      response.end("{}");
    }
  });
});
await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
const address = server.address();
assert(address && typeof address === "object");
const url = `http://127.0.0.1:${address.port}`;
writeJson(path.join(configDir, "config.json"), { server_url: url, access_token: "fixture-token" });
const component = (alias: string, id: string, entry: Record<string, unknown>) => ({
  type: "mcp", name: alias, id, version: "1.0.0", scope: "user", local_name: alias,
  mcp_integrity: extension.mcpEntryFingerprint(entry),
});
writeJson(path.join(configDir, "lockfile.json"), {
  lock_version: 2,
  registries: {
    [url]: { harnesses: { pi: { agents: [
      { id: "00000000-0000-4000-8000-000000000001", name: "one", version: "1.0.0", scope: "user",
        components: [component("one-probe", "11111111-1111-4111-8111-111111111111", one)] },
      { id: "00000000-0000-4000-8000-000000000002", name: "two", version: "1.0.0", scope: "user",
        components: [component("two-probe", "22222222-2222-4222-8222-222222222222", two)] },
    ], standalone: [] } } },
  },
});
function loadRuntime() {
  const handlers = new Map<string, (event: any, ctx: any) => Promise<void>>();
  const commands = new Map<string, (args: string, ctx: any) => Promise<void>>();
  extension.default({
    on: (name: string, handler: any) => handlers.set(name, handler),
    registerCommand: (name: string, definition: any) => commands.set(name, definition.handler),
  });
  return { handlers, commands };
}
let runtime = loadRuntime();
let approved = true;
let discardUnsent = false;
let reloads = 0;
let reloadFailure: "before" | "after" | "stall" | null = null;
let currentSessionFile: string | null = null;
let currentSessionId = "pi-agent-activation";
const notices: string[] = [];
const ctx = {
  cwd: project, hasUI: true,
  sessionManager: { getSessionFile: () => currentSessionFile, getSessionId: () => currentSessionId },
  ui: {
    theme: { fg: (_style: string, value: string) => value }, setStatus: () => {},
    notify: (value: string) => notices.push(value),
    confirm: async (title: string) => title === "Discard unsent Pi telemetry?" ? discardUnsent : approved,
  },
  reload: async () => {
    reloads++;
    if (reloadFailure === "before") throw new Error("reload failed before runtime rebuild");
    if (reloadFailure === "stall") return new Promise<void>(() => {});
    await runtime.handlers.get("session_shutdown")!({ reason: "reload" }, ctx);
    runtime = loadRuntime();
    await runtime.handlers.get("session_start")!({ reason: "reload" }, ctx);
    if (reloadFailure === "after") throw new Error("reload failed after runtime rebuild");
  },
};
async function newSession() {
  currentSessionId = `pi-session-${Math.random().toString(36).slice(2)}`;
  currentSessionFile = path.join(home, `${currentSessionId}.jsonl`);
  fs.writeFileSync(currentSessionFile, `${JSON.stringify({ type: "session", timestamp: new Date().toISOString() })}\n`);
  await runtime.handlers.get("session_start")!({ reason: "new" }, ctx);
}
const pendingPath = path.join(configDir, "pi_agent_switch_pending.json");
const readPhase = () => JSON.parse(fs.readFileSync(pendingPath, "utf8")).phase;
async function until(predicate: () => boolean) {
  const deadline = Date.now() + 3000;
  while (!predicate() && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 10));
  assert(predicate(), `timed out waiting for new runtime to finalize switch: ${notices.at(-1)} phase=${fs.existsSync(pendingPath) ? readPhase() : "gone"}`);
}
const statuses = (snapshot: any) => Object.fromEntries(
  snapshot.drift.mcp_verifications.map((item: any) => [item.alias, item.status]),
);
async function activate(name: string): Promise<any> {
  const previous = uploads.length;
  await runtime.commands.get("agent")!(name, ctx);
  if (fs.existsSync(pendingPath) && reloadFailure === null) await until(() => !fs.existsSync(pendingPath));
  if (uploads.length <= previous) return null;
  return uploads.at(-1);
}

try {
  await runtime.handlers.get("session_start")!({ reason: "start" }, ctx);
  await until(() => uploads.length > 0);
  assert.equal(uploads.length, 1);

  approved = false;
  await activate("one");
  assert.equal(reloads, 0);
  assert(!fs.existsSync(path.join(agentsDir, "default")));
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(piDir, "mcp-adapter.json"), "utf8")), originalAdapter);
  approved = true;

  const activeOne = await activate("one");
  assert.equal(reloads, 1);
  assert.deepEqual(statuses(activeOne), { "one-probe": "verified", "two-probe": "unverified" });
  assert.equal(activeOne.drift.mcp_verifications.find((item: any) => item.alias === "one-probe")?.parent_agent_id,
    "00000000-0000-4000-8000-000000000001");
  assert.equal(JSON.parse(fs.readFileSync(path.join(configDir, "config.json"), "utf8")).active_agent.id,
    "00000000-0000-4000-8000-000000000001");
  assert(!fs.existsSync(path.join(piDir, "mcp.json")), "adapter 3.x must not activate Pi built-in MCP too");
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(piDir, "mcp-adapter.json"), "utf8")),
    { mcpServers: { "one-probe": one } });
  const defaultDir = path.join(agentsDir, "default");
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(defaultDir, "mcp.json"), "utf8")), originalLegacy);
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(defaultDir, "mcp-adapter.json"), "utf8")), originalAdapter);

  // Reject ambiguous hand-edited profiles before deleting either active file.
  writeJson(path.join(agentsDir, "two", "mcp-adapter.json"), { mcpServers: { "two-probe": one } });
  assert.equal(await activate("two"), null);
  assert.equal(reloads, 1);
  assert.match(notices.at(-1)!, /conflicting MCP configs/);
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(piDir, "mcp-adapter.json"), "utf8")),
    { mcpServers: { "one-probe": one } });
  fs.rmSync(path.join(agentsDir, "two", "mcp-adapter.json"));

  const activeTwo = await activate("two");
  assert.deepEqual(statuses(activeTwo), { "one-probe": "unverified", "two-probe": "verified" });
  assert.equal(reloads, 2);
  // Another adapter config could win the alias. Never label that call as
  // observed use of the pinned listing, even when the active profile matches.
  const globalConfig = path.join(home, ".config", "mcp", "mcp.json");
  writeJson(globalConfig, { mcpServers: { "two-probe": serverEntry("shadow") } });
  assert.equal(statuses(await activate("two"))["two-probe"], "unverified");
  fs.rmSync(globalConfig);
  const original = await activate("default");
  assert.deepEqual(statuses(original), { "one-probe": "unverified", "two-probe": "unverified" });
  assert.equal(JSON.parse(fs.readFileSync(path.join(configDir, "config.json"), "utf8")).active_agent, undefined);
  assert.equal(reloads, 4);
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(piDir, "mcp.json"), "utf8")), originalLegacy);
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(piDir, "mcp-adapter.json"), "utf8")), originalAdapter);

  // Adapter 2.x still reads the active legacy path, not adapter 3.x's file.
  writeJson(manifest, { version: "2.38.0" });
  const legacy = await activate("one");
  assert.deepEqual(statuses(legacy), { "one-probe": "verified", "two-probe": "unverified" });
  assert(!fs.existsSync(path.join(piDir, "mcp-adapter.json")));
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(piDir, "mcp.json"), "utf8")),
    { mcpServers: { "one-probe": one } });
  await activate("default");
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(piDir, "mcp-adapter.json"), "utf8")), originalAdapter);

  // A pre-existing default profile with no 3.x file must not later capture
  // the selected agent's adapter config as the default on the next swap.
  fs.rmSync(defaultDir, { recursive: true });
  fs.rmSync(path.join(piDir, "mcp-adapter.json"));
  fs.mkdirSync(defaultDir);
  writeJson(path.join(defaultDir, "mcp.json"), originalLegacy);
  writeJson(manifest, { version: "3.1.0" });
  await activate("one");
  await activate("default");
  assert(!fs.existsSync(path.join(piDir, "mcp-adapter.json")));
  assert.equal(reloads, 8);

  // An old default backup predating adapter 3.x must preserve the still-active
  // 3.x config before the first upgraded swap; the previous command never did.
  fs.rmSync(defaultDir, { recursive: true });
  fs.mkdirSync(defaultDir);
  writeJson(path.join(defaultDir, "mcp.json"), originalLegacy);
  writeJson(path.join(piDir, "mcp-adapter.json"), originalAdapter);
  await activate("one");
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(defaultDir, "mcp-adapter.json"), "utf8")), originalAdapter);
  await activate("default");
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(piDir, "mcp-adapter.json"), "utf8")), originalAdapter);
  assert.equal(reloads, 10);

  // An absent or unsupported manifest is ambiguous even when mcp.json is
  // present. An explicit override is necessary for manual installs.
  fs.rmSync(manifest);
  const beforeUnknown = uploads.length;
  await runtime.commands.get("agent")!("one", ctx);
  assert.match(notices.at(-1)!, /Cannot identify pi-mcp-adapter/);
  assert.equal(uploads.length, beforeUnknown);
  assert(!fs.existsSync(pendingPath));
  // Adapter 4.x uses the same active mcp-adapter.json as 3.x; a later major is not assumed.
  writeJson(manifest, { version: "4.0.0" });
  const adapter4 = await activate("one");
  assert.deepEqual(statuses(adapter4), { "one-probe": "verified", "two-probe": "unverified" });
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(piDir, "mcp-adapter.json"), "utf8")),
    { mcpServers: { "one-probe": one } });
  await activate("default");
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(piDir, "mcp-adapter.json"), "utf8")), originalAdapter);
  writeJson(manifest, { version: "5.0.0" });
  const beforeFive = uploads.length;
  await runtime.commands.get("agent")!("one", ctx);
  assert.match(notices.at(-1)!, /Cannot identify pi-mcp-adapter/);
  assert.equal(uploads.length, beforeFive);
  assert(!fs.existsSync(pendingPath));
  fs.rmSync(manifest);
  const configPath = path.join(configDir, "config.json");
  const config = () => JSON.parse(fs.readFileSync(configPath, "utf8"));
  writeJson(configPath, { ...config(), pi_mcp_runtime: "adapter3" });
  assert.equal(statuses(await activate("one"))["one-probe"], "verified");
  await activate("default");
  writeJson(configPath, { ...config(), pi_mcp_runtime: "builtin" });
  const builtIn = await activate("one");
  assert.equal(statuses(builtIn)["one-probe"], "unverified", "built-in is not adapter-observed use");
  assert(fs.existsSync(path.join(piDir, "mcp.json")));
  assert(!fs.existsSync(path.join(piDir, "mcp-adapter.json")));
  await activate("default");
  writeJson(configPath, { ...config(), pi_mcp_runtime: "adapter3" });

  // A reload that throws before or AFTER the new runtime starts must leave
  // the binding unchanged, restore active files, and block verified attribution.
  for (const failure of ["before", "after"] as const) {
    await newSession();
    const bindingBefore = config().active_agent;
    const activeBefore = fs.readFileSync(path.join(piDir, "mcp-adapter.json"));
    const uploadCount = uploads.length;
    reloadFailure = failure;
    await runtime.commands.get("agent")!("one", ctx);
    assert.equal(readPhase(), "rollback");
    assert.deepEqual(config().active_agent, bindingBefore);
    assert.equal(uploads.length, uploadCount, "old or partly rebuilt runtime must not upload a new snapshot");
    assert(fs.readFileSync(path.join(piDir, "mcp-adapter.json")).equals(activeBefore));
    const beforeTainted = ingests.length;
    fs.appendFileSync(currentSessionFile!, `${JSON.stringify({ type: "message", failure })}\n`);
    await runtime.handlers.get("agent_end")!({}, ctx);
    assert.equal(ingests.length, beforeTainted, "mixed-runtime transcript must not inherit the old summary hash");
    reloadFailure = null;
    // A fresh Pi process can confirm the restored files; the interrupted
    // session remains unverified, even after rollback is acknowledged.
    runtime = loadRuntime();
    await runtime.handlers.get("session_start")!({ reason: "startup" }, ctx);
    assert(!fs.existsSync(pendingPath));
    assert.deepEqual(config().active_agent, bindingBefore);
  }

  // Crash before ctx.reload() completes: only a fresh process may recover the
  // staged activation. A different file hash must leave it pending.
  await newSession();
  reloadFailure = "stall";
  void runtime.commands.get("agent")!("two", ctx);
  await until(() => fs.existsSync(pendingPath) && Boolean(JSON.parse(fs.readFileSync(pendingPath, "utf8")).expected_hash));
  const candidate = JSON.parse(fs.readFileSync(pendingPath, "utf8"));
  assert.equal(candidate.phase, "activating");
  assert.deepEqual(config().active_agent, undefined);
  const expectedFiles = fs.readFileSync(path.join(piDir, "mcp-adapter.json"));
  writeJson(path.join(piDir, "mcp-adapter.json"), { mcpServers: { unrelated: one } });
  runtime = loadRuntime();
  await runtime.handlers.get("session_start")!({ reason: "startup" }, ctx);
  assert(fs.existsSync(pendingPath), "hash mismatch cannot establish an agent binding");
  assert.equal(config().active_agent, undefined);
  fs.writeFileSync(path.join(piDir, "mcp-adapter.json"), expectedFiles);
  reloadFailure = null;
  runtime = loadRuntime();
  await runtime.handlers.get("session_start")!({ reason: "startup" }, ctx);
  assert(!fs.existsSync(pendingPath));
  assert.equal(config().active_agent.id, "00000000-0000-4000-8000-000000000002");
  // Old transcript is never backfilled with the newly selected agent.
  const beforeOld = ingests.length;
  fs.appendFileSync(currentSessionFile!, `${JSON.stringify({ type: "message", old: true })}\n`);
  await runtime.handlers.get("agent_end")!({}, ctx);
  assert.equal(ingests.length, beforeOld);
  const oldSessionId = currentSessionId;
  const oldSessionFile = currentSessionFile;
  await newSession();
  fs.appendFileSync(currentSessionFile!, `${JSON.stringify({ type: "message", new: true })}\n`);
  await runtime.handlers.get("agent_end")!({}, ctx);
  assert.equal(ingests.at(-1).agent_id, "00000000-0000-4000-8000-000000000002");
  assert.match(ingests.at(-1).layer_hash, /^v2_[a-f0-9]{60}$/);
  currentSessionId = oldSessionId;
  currentSessionFile = oldSessionFile;
  runtime = loadRuntime();
  await runtime.handlers.get("session_start")!({ reason: "startup" }, ctx);
  const beforeResume = ingests.length;
  fs.appendFileSync(currentSessionFile!, `${JSON.stringify({ type: "message", resumed: true })}\n`);
  await runtime.handlers.get("agent_end")!({}, ctx);
  assert.equal(ingests.length, beforeResume, "resuming a switching session cannot inherit old attribution");
  await activate("default");

  // An unavailable server must not prevent a local switch. The session stays
  // unverified until a later runtime uploads the exact switched snapshot.
  const offlinePath = path.join(configDir, "pi_agent_switch_offline.json");
  await newSession();
  refuseUploads = true;
  const bindingBeforeUpload = config().active_agent;
  await runtime.commands.get("agent")!("one", ctx);
  await until(() => fs.existsSync(offlinePath));
  assert(!fs.existsSync(pendingPath));
  assert.deepEqual(config().active_agent, bindingBeforeUpload);
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(piDir, "mcp-adapter.json"), "utf8")),
    { mcpServers: { "one-probe": one } });
  refuseUploads = false;
  runtime = loadRuntime();
  await runtime.handlers.get("session_start")!({ reason: "startup" }, ctx);
  assert(!fs.existsSync(offlinePath));
  assert.equal(config().active_agent.id, "00000000-0000-4000-8000-000000000001");
  await activate("default");

  // No credentials at all: switch between two local profiles while offline.
  // A later login can verify the final state but not the old mixed session.
  const withCredentials = config();
  writeJson(configPath, { server_url: url, pi_mcp_runtime: "adapter3" });
  await newSession();
  const priorUploads = uploads.length;
  await activate("one");
  assert(fs.existsSync(offlinePath));
  assert.equal(uploads.length, priorUploads);
  reloadFailure = "after";
  await runtime.commands.get("agent")!("two", ctx);
  assert.equal(readPhase(), "rollback");
  reloadFailure = null;
  runtime = loadRuntime();
  await runtime.handlers.get("session_start")!({ reason: "startup" }, ctx);
  assert(!fs.existsSync(pendingPath));
  assert(fs.existsSync(offlinePath), "rolling back a second offline switch must retain the first unverified marker");
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(piDir, "mcp-adapter.json"), "utf8")),
    { mcpServers: { "one-probe": one } });
  await activate("two");
  assert(fs.existsSync(offlinePath));
  assert.equal(uploads.length, priorUploads);
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(piDir, "mcp-adapter.json"), "utf8")),
    { mcpServers: { "two-probe": two } });
  writeJson(configPath, withCredentials);
  runtime = loadRuntime();
  await runtime.handlers.get("session_start")!({ reason: "startup" }, ctx);
  assert(!fs.existsSync(offlinePath), notices.at(-1));
  assert.equal(config().active_agent.id, "00000000-0000-4000-8000-000000000002");
  await newSession();
  assert.match((await activate("default")).hash, /^v2_[a-f0-9]{60}$/);

  // Try to deliver all pre-switch lines. Without an acknowledgement, decline
  // the separate destructive prompt: no files, outbox, or binding may change.
  await newSession();
  refuseIngest = true;
  const legacyConfig = fs.readFileSync(path.join(piDir, "mcp-adapter.json"));
  const outbox = path.join(configDir, "pi_session_outbox");
  const markers = path.join(configDir, "pi_agent_switch_sessions");
  const markerCount = fs.readdirSync(markers).length;
  const beforeRefusal = reloads;
  await activate("one");
  assert.equal(reloads, beforeRefusal);
  assert(fs.readFileSync(path.join(piDir, "mcp-adapter.json")).equals(legacyConfig));
  assert(fs.readdirSync(outbox).some((name) => name.endsWith(".json")), "unsent batch must survive refusal");
  assert(!fs.existsSync(pendingPath));
  assert.equal(fs.readdirSync(markers).length, markerCount, "refusal must not taint the session");
  discardUnsent = true;
  await activate("one");
  assert.equal(reloads, beforeRefusal + 1);
  assert.equal(fs.readdirSync(markers).length, markerCount + 1);
  assert(!fs.readdirSync(outbox).some((name) => name.endsWith(".json")), "accepted discard removes old unsent batch");
  refuseIngest = false;
  discardUnsent = false;
  await newSession();
  await activate("default");

  assert(!JSON.stringify(uploads).includes("fixture-token"));
  console.log("Pi user-scope profile swap and snapshot verifier passed (mock adapter package)");
} finally {
  server.close();
  fs.rmSync(home, { recursive: true, force: true });
}
