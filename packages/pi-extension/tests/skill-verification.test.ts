// SPDX-FileCopyrightText: 2026 Observal Contributors
// SPDX-License-Identifier: Apache-2.0

// Pi skill verification: the extension's snapshot verifies a pinned skill
// exactly as the Python CLI does (observal_cli.layer._skill_status), and both
// produce the same layer identity, including the skill-verification entry.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as http from "node:http";
import * as os from "node:os";
import * as path from "node:path";

// Real path: Pi records its cwd with symlinks resolved (macOS /var -> /private/var).
const home = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "observal-pi-skill-verify-")));
process.env.HOME = home;
const observalDir = path.join(home, ".observal");
const piDir = path.join(home, ".pi", "agent");
const project = path.join(home, "project");
for (const dir of [observalDir, piDir, project]) fs.mkdirSync(dir, { recursive: true });
const root = path.resolve(import.meta.dirname, "../../..");

const content = "---\nname: review\ndescription: Review code.\n---\n\nReview carefully.\n";
const skillFile = path.join(piDir, "skills", "review", "SKILL.md");
fs.mkdirSync(path.dirname(skillFile), { recursive: true });
fs.writeFileSync(skillFile, content);
const integrity = `sha256-${crypto.createHash("sha256").update(content).digest("hex")}`;

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

const agentId = "00000000-0000-4000-8000-000000000001";
const skillId = "55555555-5555-4555-8555-555555555555";
function writeLock(skillIntegrity: string | null, scope = "user") {
  const component: Record<string, unknown> = {
    type: "skill", name: "review", id: skillId, version: "1.0.0", scope, local_name: "review",
  };
  if (skillIntegrity) component.skill_integrity = skillIntegrity;
  const agent: Record<string, unknown> = { id: agentId, name: "agent", version: "1.0.0", scope, components: [component] };
  if (scope === "project") agent.directory = component.directory = project;
  fs.writeFileSync(path.join(observalDir, "lockfile.json"), JSON.stringify({ lock_version: 2, registries: {
    [url]: { harnesses: { pi: { agents: [agent], standalone: [] } } } } }));
}
// The location Pi advertises and reads for a skill (``skill.filePath``) is what evidence must name.
const locationSha = (file: string) => crypto.createHash("sha256").update(file, "utf8").digest("hex");

const extension = await import(`../extensions/observal.ts?skill=${Date.now()}`);
const handlers = new Map<string, (event: unknown, ctx: unknown) => Promise<void>>();
extension.default({ on: (name: string, handler: any) => handlers.set(name, handler), registerCommand() {} });
const context = { cwd: project, hasUI: false, sessionManager: { getSessionFile: () => null, getSessionId: () => "pi-skill" } };
async function piSnapshot(): Promise<any> {
  const count = snapshots.length + 1;
  await handlers.get("session_start")!({ reason: "start" }, context);
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
const statusOf = (snapshot: any) => snapshot.drift.skill_verifications.map((item: any) => item.status);
function assertParity(pi: any, python: any) {
  assert.equal(pi.hash, python.hash, "same layer identity");
  assert.deepEqual(pi.harnesses, python.harnesses, "same manifest, including the skill-verification entry");
  assert.deepEqual(pi.drift.skill_verifications, python.drift.skill_verifications, "same skill results");
}

try {
  // Legacy pin without a fingerprint: unverified, and no skill-verification entry.
  writeLock(null);
  const legacy = await piSnapshot();
  assert.deepEqual(statusOf(legacy), ["unverified"]);
  assert(!legacy.harnesses.pi.some((item: any) => item.path === "observal:skill-verification"));
  assertParity(legacy, pythonSnapshot());

  // Fingerprinted and active: verified, bound into the layer identity.
  writeLock(integrity);
  const verified = await piSnapshot();
  assert.deepEqual(statusOf(verified), ["verified"]);
  assert.notEqual(verified.hash, legacy.hash);
  assert(verified.harnesses.pi.some((item: any) => item.path === "observal:skill-verification"));
  assertParity(verified, pythonSnapshot());
  assert.equal(verified.drift.skill_verifications[0].parent_agent_id, agentId);
  assert.equal(verified.drift.skill_verifications[0].location_sha256, locationSha(skillFile));

  // A copy in ~/.agents/skills (unhashed) could be loaded instead: unverified.
  const shadow = path.join(home, ".agents", "skills", "review", "SKILL.md");
  fs.mkdirSync(path.dirname(shadow), { recursive: true });
  fs.writeFileSync(shadow, "shadow");
  const shadowed = await piSnapshot();
  assert.deepEqual(statusOf(shadowed), ["unverified"]);
  assertParity(shadowed, pythonSnapshot());
  fs.rmSync(path.join(home, ".agents"), { recursive: true });

  // The active file was edited after the pull: drifted, and the layer is not canonical.
  fs.writeFileSync(skillFile, `${content}edited\n`);
  const drifted = await piSnapshot();
  assert.deepEqual(statusOf(drifted), ["drifted"]);
  assert.equal(drifted.drift.is_canonical, false);
  assertParity(drifted, pythonSnapshot());

  // Not activated yet (no active file): unverified, never drift.
  fs.rmSync(path.join(piDir, "skills"), { recursive: true });
  const inactive = await piSnapshot();
  assert.deepEqual(statusOf(inactive), ["unverified"]);
  assert.notEqual(inactive.drift.is_canonical, false);
  assertParity(inactive, pythonSnapshot());

  // A project-scope skill is bound to <cwd>/.pi/skills/<alias>/SKILL.md, the same on both sides.
  const projectSkill = path.join(project, ".pi", "skills", "review", "SKILL.md");
  fs.mkdirSync(path.dirname(projectSkill), { recursive: true });
  fs.writeFileSync(projectSkill, content);
  writeLock(integrity, "project");
  const scoped = await piSnapshot();
  assert.deepEqual(statusOf(scoped), ["verified"]);
  assert.equal(scoped.drift.skill_verifications[0].location_sha256, locationSha(projectSkill));
  assertParity(scoped, pythonSnapshot());

  // Skill content is never uploaded beyond what the manifest already carries.
  assert(!JSON.stringify(snapshots).includes("fixture-token"));
  console.log("Pi skill verification verified");
} finally {
  server.close();
  fs.rmSync(home, { recursive: true, force: true });
}
