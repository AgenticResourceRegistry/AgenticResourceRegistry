// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
// SPDX-License-Identifier: Apache-2.0

// Phase 1.2: Pi and Python agree on the v2 manifest-plus-pins identity.
import assert from "node:assert/strict";
import * as fs from "node:fs";
import { execFileSync } from "node:child_process";
import * as http from "node:http";
import * as os from "node:os";
import * as path from "node:path";

const home = fs.mkdtempSync(path.join(os.tmpdir(), "observal-pi-phase0-"));
process.env.HOME = home;
const observalDir = path.join(home, ".observal");
const piDir = path.join(home, ".pi", "agent");
fs.mkdirSync(observalDir, { recursive: true });
fs.mkdirSync(piDir, { recursive: true });
fs.writeFileSync(path.join(piDir, "mcp.json"), JSON.stringify({ mcpServers: { fixture: { command: "fixture" } } }));
fs.writeFileSync(path.join(home, "AGENTS.md"), "Project agent instructions");
fs.mkdirSync(path.join(home, ".pi"), { recursive: true });
fs.writeFileSync(path.join(home, ".pi", "mcp.json"), "{}");
const snapshots: any[] = [];
const server = http.createServer((request, response) => {
  response.setHeader("Content-Type", "application/json");
  const chunks: Buffer[] = [];
  request.on("data", (chunk) => chunks.push(chunk));
  request.on("end", () => {
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
const lockPath = path.join(observalDir, "lockfile.json");
const agent = { id: "00000000-0000-4000-8000-000000000001", name: "fixture", version: "1.0.0", scope: "user", fixture_extra: "spread-unchanged", components: [
  { type: "mcp", name: "probe", id: "11111111-1111-4111-8111-111111111111", version: "1.0.0", scope: "user", local_name: "first", qualified_name: "alice/probe" },
  { type: "mcp", name: "probe", id: "33333333-3333-4333-8333-333333333333", version: "1.0.0", scope: "user", local_name: "bob-probe", qualified_name: "bob/probe" },
] };
const standalone = { type: "mcp", id: "44444444-4444-4444-8444-444444444444", name: "probe", version: "1.2.3", scope: "project", directory: home, local_name: "standalone-probe", qualified_name: "carol/probe" };
function writePins() {
  fs.writeFileSync(lockPath, JSON.stringify({ lock_version: 2, registries: { [url]: { harnesses: { pi: { agents: [agent], standalone: [standalone] } } } } }));
}
writePins();
const handlers = new Map<string, (event: unknown, ctx: unknown) => Promise<void>>();
const pi = { on: (name: string, handler: any) => handlers.set(name, handler), registerCommand() {} };
const extension = await import(`../extensions/observal.ts?phase0=${Date.now()}`);
extension.default(pi);
const context = { cwd: home, hasUI: false, sessionManager: { getSessionFile: () => null, getSessionId: () => "pi-phase0" } };
async function snapshotNumber(count: number) {
  await handlers.get("session_start")!({ reason: "start" }, context);
  const deadline = Date.now() + 3000;
  while (snapshots.length < count && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(snapshots.length, count);
  return snapshots[count - 1];
}
const first = await snapshotNumber(1);
async function nextSnapshot(count: number) {
  writePins();
  return snapshotNumber(count);
}
agent.components[0].id = "22222222-2222-4222-8222-222222222222";
const idOnly = await nextSnapshot(2);
agent.components[0].id = "11111111-1111-4111-8111-111111111111";
agent.components[0].version = "2.0.0";
const versionOnly = await nextSnapshot(3);
agent.components[0].version = "1.0.0";
agent.components[0].local_name = "second";
const aliasOnly = await nextSnapshot(4);
for (const changed of [idOnly, versionOnly, aliasOnly]) {
  assert.notEqual(first.hash, changed.hash);
  assert.deepEqual(first.harnesses, changed.harnesses);
  assert.notDeepEqual(first.pinned_versions, changed.pinned_versions);
}
assert.match(first.hash, /^v2_[0-9a-f]{60}$/);
assert.equal(first.pinned_versions.agents[0].components[0].id, "11111111-1111-4111-8111-111111111111");
assert.equal(idOnly.pinned_versions.agents[0].components[0].id, "22222222-2222-4222-8222-222222222222");
assert.equal(versionOnly.pinned_versions.agents[0].components[0].version, "2.0.0");
assert.equal(aliasOnly.pinned_versions.agents[0].components[0].local_name, "second");
assert.equal(first.pinned_versions.schema_version, 2);
assert.equal(first.pinned_versions.agents[0].fixture_extra, undefined);
assert.deepEqual(first.pinned_versions.agents[0].components.map((item: any) => item.local_name), ["first", "bob-probe"]);
assert.equal(first.pinned_versions.standalone[0].qualified_name, "carol/probe");
assert(first.harnesses.pi.some((item: any) => item.path === "project:.pi/mcp.json"));
// Golden: a real Python snapshot built from the same files and lockfile must
// match Pi's hash and pin projection, not just a second TypeScript rehash.
agent.components[0].local_name = "first";
writePins();
const python = `import json\nfrom observal_cli.layer import build_upload_payload\np=build_upload_payload(harness="pi", project_dir=${JSON.stringify(home)})\nprint(json.dumps({"hash":p["hash"],"pinned_versions":p["pinned_versions"],"harnesses":p["harnesses"]}))`;
const root = path.resolve(import.meta.dirname, "../../..");
const golden = JSON.parse(execFileSync("uv", ["run", "python", "-c", python],
  { cwd: root, env: { ...process.env, HOME: home }, encoding: "utf8" }));
assert.equal(first.hash, golden.hash);
assert.deepEqual(first.pinned_versions, golden.pinned_versions);
assert.deepEqual(first.harnesses, golden.harnesses);
const marker = path.join(observalDir, "pi_layer_uploaded.json");
const deadline = Date.now() + 3000;
while ((!fs.existsSync(marker) || JSON.parse(fs.readFileSync(marker, "utf8")).hash !== aliasOnly.hash)
  && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 10));
assert(fs.existsSync(marker));
fs.writeFileSync(path.join(observalDir, "config.json"), JSON.stringify({
  server_url: url, access_token: "fixture-token", user_id: "other-user",
}));
const otherUser = await snapshotNumber(5);
assert.equal(otherUser.hash, first.hash); // same pins/files, but scoped upload is required for the new user
server.close();
fs.rmSync(home, { recursive: true, force: true });
console.log("Pi/Python layer v2 golden verified");
