// SPDX-FileCopyrightText: 2026 Observal Contributors
// SPDX-License-Identifier: Apache-2.0

// Pi registry hooks: the extension verifies a pinned hook exactly as the Python
// CLI does (observal_cli.pi_hooks.hook_status), both produce the same layer
// identity, and the runner executes only the hooks the snapshot verified,
// recording bounded receipts that never carry the command, input or output.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as http from "node:http";
import * as os from "node:os";
import * as path from "node:path";

const home = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "observal-pi-hooks-")));
process.env.HOME = home;
const observalDir = path.join(home, ".observal");
const piDir = path.join(home, ".pi", "agent");
const project = path.join(home, "project");
for (const dir of [observalDir, piDir, project]) fs.mkdirSync(dir, { recursive: true });
const root = path.resolve(import.meta.dirname, "../../..");
const out = path.join(home, "hook-out");
fs.mkdirSync(out);

const snapshots: any[] = [];
const server = http.createServer((request, response) => {
  const chunks: Buffer[] = [];
  request.on("data", (chunk) => chunks.push(chunk));
  request.on("end", () => {
    response.setHeader("Content-Type", "application/json");
    if (request.url === "/api/v1/layer-snapshots") {
      const snapshot = JSON.parse(Buffer.concat(chunks).toString("utf-8"));
      snapshots.push(snapshot);
      response.end(JSON.stringify({ hash: snapshot.hash }));
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
fs.writeFileSync(path.join(observalDir, "config.json"), JSON.stringify({ server_url: url, access_token: "fixture-token" }));

const extension = await import(`../extensions/observal.ts?hooks=${Date.now()}`);
const { parsePiHooks, piHookIntegrity, piHookBinding } = extension;

const agentId = "00000000-0000-4000-8000-000000000001";
const guardId = "66666666-6666-4666-8666-666666666666";
const auditId = "77777777-7777-4777-8777-777777777777";
const guard = { name: "guard", event: "tool_call", type: "command", command: `cat > ${out}/guard-input; echo checked`, timeout: 5 };
const audit = { name: "audit", event: "tool_result", type: "command", command: `cat > ${out}/audit-input`, timeout: 5 };
const activeFile = path.join(piDir, "observal-hooks.json");
function writeHooks(hooks: unknown[], agent = "reviewer", file = activeFile) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify({ schema: "observal-pi-hooks/v1", agent, hooks }));
}
function pinned(id: string, entry: any, agent = "reviewer"): Record<string, unknown> {
  return {
    type: "hook", name: entry.name, id, version: "1.0.0", scope: "user", local_name: entry.name,
    hook_event: entry.event, hook_command: entry.command, hook_agent: "", hook_config: "user:observal-hooks.json",
    hook_profile: agent, hook_integrity: piHookIntegrity(agent, entry),
  };
}
function writeLock(components: unknown[]) {
  const agent = { id: agentId, name: "reviewer", version: "1.0.0", scope: "user", components };
  fs.writeFileSync(path.join(observalDir, "lockfile.json"), JSON.stringify({ lock_version: 2, registries: {
    [url]: { harnesses: { pi: { agents: [agent], standalone: [] } } } } }));
}

function load(tag: string) {
  return import(`../extensions/observal.ts?hooks=${tag}-${Date.now()}`).then((module) => {
    const handlers = new Map<string, (event: unknown, ctx: unknown) => Promise<any>>();
    const entries: Array<{ type: string; data: any }> = [];
    module.default({
      on: (name: string, handler: any) => handlers.set(name, handler),
      registerCommand() {},
      appendEntry: (type: string, data: unknown) => entries.push({ type, data }),
    });
    return { handlers, entries };
  });
}
const context = { cwd: project, hasUI: false, sessionManager: { getSessionFile: () => null, getSessionId: () => "pi-hooks" } };
const main = await load("main");

async function piSnapshot(runtime = main): Promise<any> {
  const count = snapshots.length + 1;
  await runtime.handlers.get("session_start")!({ reason: "start" }, context);
  const deadline = Date.now() + 3000;
  while (snapshots.length < count && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(snapshots.length, count);
  return snapshots[count - 1];
}
function pythonSnapshot(): any {
  const code = `import json\nfrom observal_cli.layer import build_upload_payload\np=build_upload_payload(harness="pi", project_dir=${JSON.stringify(project)})\nprint(json.dumps({"hash":p["hash"],"harnesses":p["harnesses"],"drift":p["drift"]}))`;
  return JSON.parse(execFileSync("uv", ["run", "python", "-c", code],
    { cwd: root, env: { ...process.env, HOME: home }, encoding: "utf8" }));
}
const statusOf = (snapshot: any) => snapshot.drift.hook_verifications.map((item: any) => `${item.alias}:${item.status}`);
function assertParity(pi: any, python: any) {
  assert.equal(pi.hash, python.hash, "same layer identity");
  assert.deepEqual(pi.harnesses, python.harnesses, "same manifest, including the hook-verification entry");
  const pick = (items: any[]) => items.map(({ component_id, status, location_sha256, hook_agent, hook_placement }) =>
    ({ component_id, status, location_sha256, hook_agent, hook_placement }));
  assert.deepEqual(pick(pi.drift.hook_verifications), pick(python.drift.hook_verifications), "same hook results");
}
let callCounter = 0;
async function toolCall(runtime = main, toolName = "bash", input: unknown = { command: "ls" }) {
  const toolCallId = `call_${++callCounter}`;
  const result = await runtime.handlers.get("tool_call")!({ type: "tool_call", toolCallId, toolName, input }, context);
  return { toolCallId, result };
}
async function toolResult(toolCallId: string, runtime = main) {
  return runtime.handlers.get("tool_result")!({ type: "tool_result", toolCallId, toolName: "bash", input: { command: "ls" },
    content: [{ type: "text", text: "secret output" }], isError: false }, context);
}
const receipts = (runtime = main) => runtime.entries.filter((entry) => entry.type === "observal-hook-run").map((entry) => entry.data);

try {
  // Parsing mirrors the server's contract: one invalid entry invalidates the whole file.
  assert.equal(parsePiHooks(Buffer.from(JSON.stringify({ schema: "observal-pi-hooks/v1", agent: "a", hooks: [{ ...guard, event: "Stop" }] }))), null);
  assert.equal(parsePiHooks(Buffer.from(JSON.stringify({ schema: "observal-pi-hooks/v2", agent: "a", hooks: [] }))), null);
  assert.equal(parsePiHooks(Buffer.from(JSON.stringify({ schema: "observal-pi-hooks/v1", agent: "a", hooks: [{ ...guard, timeout: 0 }] }))), null);

  // Pinned and active: verified on both sides, with the same identity.
  writeHooks([guard, audit]);
  writeLock([pinned(guardId, guard), pinned(auditId, audit)]);
  const verified = await piSnapshot();
  assert.deepEqual(statusOf(verified), ["guard:verified", "audit:verified"]);
  assert(verified.harnesses.pi.some((item: any) => item.path === "observal:hook-verification"));
  const hooksEntry = verified.harnesses.pi.find((item: any) => item.path === "user:observal-hooks.json");
  assert.equal(hooksEntry.content, "", "hook commands are hash-only, never uploaded");
  assert.equal(verified.drift.hook_verifications[0].location_sha256, piHookBinding("tool_call", guard.command));
  assertParity(verified, pythonSnapshot());

  // Runner: a silent run, a run with output, and the hook input on stdin. Receipts carry no command or text.
  fs.writeFileSync(path.join(out, "guard-input"), "");
  const first = await toolCall(main, "bash", { command: "rm -rf /tmp/x" });
  assert.equal(first.result, undefined, "exit 0 never blocks");
  const stdin = JSON.parse(fs.readFileSync(path.join(out, "guard-input"), "utf-8"));
  assert.equal(stdin.hook_event_name, "PreToolUse");
  assert.equal(stdin.tool_call_id, first.toolCallId);
  assert.deepEqual(stdin.tool_input, { command: "rm -rf /tmp/x" });
  await toolResult(first.toolCallId);
  const auditInput = JSON.parse(fs.readFileSync(path.join(out, "audit-input"), "utf-8"));
  assert.equal(auditInput.hook_event_name, "PostToolUse");
  assert.deepEqual(auditInput.tool_response, { is_error: false, content: "secret output" });
  assert.deepEqual(receipts(), [
    { v: 1, event: "tool_call", tool_call_id: first.toolCallId, binding: piHookBinding("tool_call", guard.command), outcome: "ran_with_output", exit_code: 0 },
    { v: 1, event: "tool_result", tool_call_id: first.toolCallId, binding: piHookBinding("tool_result", audit.command), outcome: "ran", exit_code: 0 },
  ]);
  const serialized = JSON.stringify(main.entries);
  for (const secret of ["rm -rf", "secret output", "checked", out]) assert(!serialized.includes(secret), `receipt leaked ${secret}`);

  // Exit 2 on tool_call blocks with stderr as the reason and stops later tool_call hooks.
  const blocker = { name: "blocker", event: "tool_call", type: "command", command: "echo 'not allowed: policy' >&2; exit 2", timeout: 5 };
  const after = { name: "after", event: "tool_call", type: "command", command: `touch ${out}/after-ran`, timeout: 5 };
  const postBlock = { name: "post-two", event: "tool_result", type: "command", command: "exit 2", timeout: 5 };
  const failing = { name: "failing", event: "tool_call", type: "command", command: "echo oops >&2; exit 1", timeout: 5 };
  writeHooks([failing, blocker, after, postBlock]);
  writeLock([pinned(guardId, blocker)]);
  await piSnapshot();
  main.entries.length = 0;
  const blocked = await toolCall();
  assert.deepEqual(blocked.result, { block: true, reason: "not allowed: policy" });
  assert(!fs.existsSync(path.join(out, "after-ran")), "a block stops later hooks for that call");
  assert.deepEqual(receipts().map((item) => [item.outcome, item.exit_code]), [["failed", 1], ["blocked", 2]]);
  // On tool_result exit 2 cannot block: it is a failure.
  await toolResult(blocked.toolCallId);
  assert.deepEqual(receipts().at(-1).outcome, "failed");

  // A timeout is a failure with no exit code, and never blocks (fail-open).
  const slow = { name: "slow", event: "tool_call", type: "command", command: "sleep 5; exit 2", timeout: 1 };
  writeHooks([slow]);
  await piSnapshot();
  main.entries.length = 0;
  const started = Date.now();
  const timed = await toolCall();
  assert.equal(timed.result, undefined);
  assert(Date.now() - started < 4000, "killed at its timeout");
  assert.deepEqual(receipts().map((item) => [item.outcome, item.exit_code]), [["failed", null]]);

  // Two copies of the extension in one process (npm package and local file): one run per call.
  writeHooks([guard]);
  writeLock([pinned(guardId, guard)]);
  const second = await load("second");
  await piSnapshot();
  // Same layer hash: the second copy starts its session without re-uploading it.
  await second.handlers.get("session_start")!({ reason: "start" }, context);
  main.entries.length = 0;
  const shared = { type: "tool_call", toolCallId: "call_shared", toolName: "bash", input: {} };
  await main.handlers.get("tool_call")!(shared, context);
  await second.handlers.get("tool_call")!(shared, context);
  assert.equal(receipts().length + receipts(second).length, 1);

  // Edited timeout: drifted, not canonical; same on both sides. The edited file still runs.
  writeHooks([{ ...guard, timeout: 9 }]);
  const drifted = await piSnapshot();
  assert.deepEqual(statusOf(drifted), ["guard:drifted"]);
  assert.equal(drifted.drift.is_canonical, false);
  assertParity(drifted, pythonSnapshot());

  // Duplicate (event, command): receipts could name neither copy, so unverified.
  writeHooks([guard, { ...guard, name: "guard-copy" }]);
  const duplicate = await piSnapshot();
  assert.deepEqual(statusOf(duplicate), ["guard:unverified"]);
  assertParity(duplicate, pythonSnapshot());

  // Another profile is active (same command, other agent): unverified.
  writeHooks([guard], "someone-else");
  const other = await piSnapshot();
  assert.deepEqual(statusOf(other), ["guard:unverified"]);
  assertParity(other, pythonSnapshot());

  // Not activated (no active file): unverified, and nothing runs.
  fs.rmSync(activeFile);
  writeHooks([guard], "reviewer", path.join(piDir, "agents", "reviewer", "observal-hooks.json"));
  const inactive = await piSnapshot();
  assert.deepEqual(statusOf(inactive), ["guard:unverified"]);
  assertParity(inactive, pythonSnapshot());
  main.entries.length = 0;
  await toolCall();
  assert.deepEqual(receipts(), []);

  // An invalid active file: unverified, and none of its hooks run.
  fs.writeFileSync(activeFile, JSON.stringify({ schema: "observal-pi-hooks/v1", agent: "reviewer", hooks: [guard, { ...guard, type: "http" }] }));
  const invalid = await piSnapshot();
  assert.deepEqual(statusOf(invalid), ["guard:unverified"]);
  assertParity(invalid, pythonSnapshot());
  await toolCall();
  assert.deepEqual(receipts(), []);

  console.log("Pi registry hook verification and runner verified");
} finally {
  server.close();
  fs.rmSync(home, { recursive: true, force: true });
}
