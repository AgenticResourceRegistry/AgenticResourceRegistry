// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
// SPDX-License-Identifier: Apache-2.0

// Pi MCP verification: the snapshot says "verified" only for the active,
// unshadowed, fingerprint-equal entry, and never uploads MCP config contents.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as http from "node:http";
import * as os from "node:os";
import * as path from "node:path";

const home = fs.mkdtempSync(path.join(os.tmpdir(), "observal-pi-mcp-verify-"));
const project = path.join(home, "project");
process.env.HOME = home;
const observalDir = path.join(home, ".observal");
const piDir = path.join(home, ".pi", "agent");
for (const dir of [observalDir, piDir, path.join(project, ".pi")]) fs.mkdirSync(dir, { recursive: true });
const root = path.resolve(import.meta.dirname, "../../..");

function python(code: string): any {
  return JSON.parse(execFileSync("uv", ["run", "python", "-c", code],
    { cwd: root, env: { ...process.env, HOME: home }, encoding: "utf8" }));
}

// ── Fingerprint parity with observal_cli.layer.mcp_entry_fingerprint ─────────
const extension = await import(`../extensions/observal.ts?verify=${Date.now()}`);
const entries: Array<Record<string, unknown>> = [
  { command: "python3", args: ["/srv/fixture.py", "team"], env: { SECRET: "never-hashed" } },
  { command: "npx", args: ["-y", "pkg@1.0.0", { nested: { b: 1, a: [true, null] } }], type: "stdio" },
  { command: null },
  {},
  { command: "naïve", args: ["ünïcode", "tab\tand\u2028sep"] },
  { url: "https://User:Pass@Example.COM:0443/mcp/v1?token=secret#frag", type: "http" },
  { url: " HTTPS://host/path with space " },
  { url: "http://host:" },
  { url: "" },
  { url: 42 },
];
const expected = python(`import json\nfrom observal_cli.layer import mcp_entry_fingerprint\nprint(json.dumps([mcp_entry_fingerprint(e) for e in json.loads(${JSON.stringify(JSON.stringify(entries))})]))`);
assert.deepEqual(entries.map((entry) => extension.mcpEntryFingerprint(entry)), expected);
// Inputs whose Python encoding is not reproduced exactly are unverifiable, never drift.
for (const unsupported of [
  { args: [1.5] }, { url: "https://[::1]:8080/x" }, { url: "https://host:99999/" }, { url: "https:/host" },
  { url: "https://host%25zone/" }, { args: ["\ud800"] },
]) assert.equal(extension.mcpEntryFingerprint(unsupported), null);

// ── Snapshot verification ─────────────────────────────────────────────────────
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

const active = { command: "python3", args: ["/srv/fixture.py", "team"], env: { API_KEY: "fixture-private-value" } };
const integrity = extension.mcpEntryFingerprint(active);
const agentId = "00000000-0000-4000-8000-000000000001";
const component = (id: string, alias: string, scope = "user") => ({
  type: "mcp", name: "probe", id, version: "1.0.0", scope, local_name: alias, mcp_integrity: integrity,
});
const lockfile = {
  lock_version: 2,
  registries: {
    [url]: {
      harnesses: {
        pi: {
          agents: [
            { id: agentId, name: "active", version: "1.0.0", scope: "user", components: [
              component("11111111-1111-4111-8111-111111111111", "team-probe"),
              component("22222222-2222-4222-8222-222222222222", "other-profile-probe"),
            ] },
          ],
          standalone: [{ ...component("33333333-3333-4333-8333-333333333333", "project-probe", "project"), directory: project }],
        },
      },
    },
  },
};
fs.writeFileSync(path.join(observalDir, "lockfile.json"), JSON.stringify(lockfile));
const writeJson = (file: string, value: unknown) => {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(value));
};
writeJson(path.join(piDir, "mcp.json"), { mcpServers: { "team-probe": { ...active, directTools: true } } });
writeJson(path.join(project, ".pi", "mcp.json"), { mcpServers: { "project-probe": active } });

const handlers = new Map<string, (event: unknown, ctx: unknown) => Promise<void>>();
extension.default({ on: (name: string, handler: any) => handlers.set(name, handler), registerCommand() {} });
const context = { cwd: project, hasUI: false, sessionManager: { getSessionFile: () => null, getSessionId: () => "pi-verify" } };
async function snapshot(): Promise<any> {
  const count = snapshots.length + 1;
  await handlers.get("session_start")!({ reason: "start" }, context);
  const deadline = Date.now() + 3000;
  while (snapshots.length < count && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(snapshots.length, count);
  return snapshots[count - 1];
}
const statuses = (value: any) => Object.fromEntries(value.drift.mcp_verifications.map((item: any) => [item.alias, item.status]));

const first = await snapshot();
assert.deepEqual(statuses(first), {
  "team-probe": "verified",
  // An inactive /agent profile's server is absent from the active config.
  "other-profile-probe": "unverified",
  "project-probe": "verified",
});
assert.equal(first.drift.is_canonical, null);
assert.deepEqual(first.drift.mcp_verifications[0], {
  harness: "pi", component_id: "11111111-1111-4111-8111-111111111111", alias: "team-probe", scope: "user",
  parent_agent_id: agentId, status: "verified",
});
assert.equal(first.drift.mcp_verifications[2].parent_agent_id, "");

// Shadow sources are hash-only identity inputs.
writeJson(path.join(home, ".config", "mcp", "mcp.json"), { mcpServers: { "team-probe": active } });
writeJson(path.join(piDir, "mcp-adapter.json"), { mcpServers: { unrelated: { command: "x" } } });
writeJson(path.join(project, ".mcp.json"), { mcpServers: { unrelated: { command: "y" } } });
const sameShadow = await snapshot();
assert.notEqual(sameShadow.hash, first.hash);
assert.equal(statuses(sameShadow)["team-probe"], "verified");
for (const display of ["user:.config/mcp/mcp.json", "user:mcp-adapter.json", "project:.mcp.json", "user:mcp.json",
  "project:.pi/mcp.json"]) {
  assert.equal(sameShadow.harnesses.pi.find((item: any) => item.path === display)?.content, "", display);
}
assert(!JSON.stringify(snapshots).includes("fixture-private-value"));

// Python builds the same manifest (and hash) from the same files.
const golden = python(`import json\nfrom observal_cli.layer import build_upload_payload\np=build_upload_payload(harness="pi", project_dir=${JSON.stringify(project)})\nprint(json.dumps({"hash":p["hash"],"harnesses":p["harnesses"]}))`);
assert.equal(sameShadow.hash, golden.hash);
assert.deepEqual(sameShadow.harnesses, golden.harnesses);

// pi-mcp-adapter 3.x uses mcp-adapter.json, not mcp.json. A pulled project
// profile activated there must be verifiable without a legacy active file.
fs.rmSync(path.join(project, ".pi", "mcp.json"));
writeJson(path.join(project, ".pi", "mcp-adapter.json"), { mcpServers: { "project-probe": active } });
const adapter3 = await snapshot();
assert.equal(statuses(adapter3)["project-probe"], "verified");
assert.equal(adapter3.harnesses.pi.find((item: any) => item.path === "project:.pi/mcp-adapter.json")?.content, "");
writeJson(path.join(project, ".pi", "mcp.json"), { mcpServers: { "project-probe": { command: "shadow" } } });
assert.equal(statuses(await snapshot())["project-probe"], "unverified");
fs.rmSync(path.join(project, ".pi", "mcp.json"));
fs.rmSync(path.join(piDir, "mcp.json"));
writeJson(path.join(piDir, "mcp-adapter.json"), { mcpServers: { "team-probe": active } });
assert.equal(statuses(await snapshot())["team-probe"], "verified");
writeJson(path.join(piDir, "mcp.json"), { mcpServers: { "team-probe": { command: "shadow" } } });
assert.equal(statuses(await snapshot())["team-probe"], "unverified");
writeJson(path.join(piDir, "mcp.json"), { mcpServers: { "team-probe": { ...active, directTools: true } } });
writeJson(path.join(piDir, "mcp-adapter.json"), { mcpServers: { unrelated: { command: "x" } } });

// A different definition of the same name elsewhere may be what the adapter runs.
writeJson(path.join(home, ".agents", "mcp.json"), { mcpServers: { "team-probe": { command: "other" } } });
assert.equal(statuses(await snapshot())["team-probe"], "unverified");
fs.rmSync(path.join(home, ".agents"), { recursive: true });

// Imports can add servers from files outside the snapshot.
writeJson(path.join(piDir, "mcp-adapter.json"), { imports: ["cursor"], mcpServers: {} });
assert.deepEqual(new Set(Object.values(statuses(await snapshot()))), new Set(["unverified"]));
writeJson(path.join(piDir, "mcp-adapter.json"), { settings: { directTools: true }, mcpServers: {} });

// A changed definition in the active file is drift, and fails the snapshot closed.
writeJson(path.join(piDir, "mcp.json"), { mcpServers: { "team-probe": { ...active, args: ["/srv/other.py"] } } });
const drifted = await snapshot();
assert.equal(statuses(drifted)["team-probe"], "drifted");
assert.equal(drifted.drift.is_canonical, false);
assert.deepEqual(drifted.drift.drifted_files, [
  { harness: "pi", component: "11111111-1111-4111-8111-111111111111", alias: "team-probe", status: "drifted" },
]);

// Unparseable active config, or a pin without an install fingerprint, is unverified.
fs.writeFileSync(path.join(piDir, "mcp.json"), "{not json");
assert.equal(statuses(await snapshot())["team-probe"], "unverified");
writeJson(path.join(piDir, "mcp.json"), { mcpServers: { "team-probe": active } });
const restored = await snapshot();
assert.equal(statuses(restored)["team-probe"], "verified");
delete (lockfile.registries[url].harnesses.pi.agents[0].components[0] as any).mcp_integrity;
fs.writeFileSync(path.join(observalDir, "lockfile.json"), JSON.stringify(lockfile));
const withoutIntegrity = await snapshot();
assert.equal(statuses(withoutIntegrity)["team-probe"], "unverified");
// Same files, different verification inputs: a new identity, never a conflicting one.
assert.notEqual(withoutIntegrity.hash, restored.hash);

server.close();
fs.rmSync(home, { recursive: true, force: true });
console.log("Pi MCP verification verified");
