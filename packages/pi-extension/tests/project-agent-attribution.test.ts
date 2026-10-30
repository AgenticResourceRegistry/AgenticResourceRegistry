// SPDX-FileCopyrightText: 2026 Dheirav <dheirav2005@gmail.com>
// SPDX-License-Identifier: Apache-2.0

// A project-scope pull binds its agent to that project only (OBS-1453). Pi
// sessions elsewhere on the machine must not be tagged with it, even when an
// older CLI left it behind as the global active_agent.

import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as http from "node:http";
import * as os from "node:os";
import * as path from "node:path";

const home = fs.mkdtempSync(path.join(os.tmpdir(), "observal-pi-project-agent-"));
process.env.HOME = home;
process.env.USERPROFILE = home; // os.homedir() reads this on Windows
delete process.env.OBSERVAL_DELEGATION_TASK_ID;
const observalDir = path.join(home, ".observal");
fs.mkdirSync(observalDir, { recursive: true });
const projectA = path.join(home, "project-a");
const nestedB = path.join(projectA, "packages", "b");
const unrelated = path.join(home, "unrelated");
for (const dir of [nestedB, unrelated]) fs.mkdirSync(dir, { recursive: true });

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
const serverUrl = `http://127.0.0.1:${address.port}`;

const projectAgent = { name: "pr-verify-qa", id: "agent-x", version: "1.0.0", scope: "project", directory: projectA };
const nestedAgent = { name: "pkg-b", id: "agent-b", version: "2.0.0", scope: "project", directory: nestedB };
const userAgent = { name: "everyday", id: "agent-y", version: "3.0.0", scope: "user", directory: home };

function writeState(activeAgent: Record<string, unknown> | undefined, agents: Array<Record<string, unknown>>): void {
  fs.writeFileSync(
    path.join(observalDir, "config.json"),
    JSON.stringify({ server_url: serverUrl, access_token: "token", user_id: "user", active_agent: activeAgent }),
  );
  fs.writeFileSync(
    path.join(observalDir, "lockfile.json"),
    JSON.stringify({ registries: { [serverUrl]: { server_url: serverUrl, harnesses: { pi: { agents } } } } }),
  );
}

const handlers = new Map<string, (event: unknown, ctx: unknown) => Promise<void>>();
const pi = { on: (name: string, handler: any) => handlers.set(name, handler), registerCommand() {} };
const extension = await import(`../extensions/observal.ts?project-agent=${Date.now()}`);
extension.default(pi);

let sessions = 0;
async function sessionIn(cwd: string): Promise<{ agent_id: unknown; agent_version: unknown }> {
  sessions += 1;
  const sessionFile = path.join(home, `session-${sessions}.jsonl`);
  fs.writeFileSync(sessionFile, `${JSON.stringify({ type: "message", index: 0 })}\n`);
  const context = {
    cwd,
    hasUI: false,
    sessionManager: { getSessionFile: () => sessionFile, getSessionId: () => `pi-session-${sessions}` },
  };
  const before = payloads.length;
  await handlers.get("session_start")!({ reason: "start" }, context);
  await handlers.get("agent_end")!({}, context);
  assert.equal(payloads.length, before + 1);
  const payload = payloads.at(-1)!;
  return { agent_id: payload.agent_id, agent_version: payload.agent_version ?? null };
}

// What an older CLI left after `observal agent pull pr-verify-qa --scope project` in project A.
writeState({ id: "agent-x", name: "pr-verify-qa", version: "1.0.0" }, [projectAgent]);
assert.deepEqual(await sessionIn(unrelated), { agent_id: null, agent_version: null });
assert.deepEqual(await sessionIn(projectA), { agent_id: "agent-x", agent_version: "1.0.0" });

// A fixed CLI writes no active_agent for a project-scope pull; the project still binds.
writeState(undefined, [projectAgent]);
assert.deepEqual(await sessionIn(projectA), { agent_id: "agent-x", agent_version: "1.0.0" });
assert.deepEqual(await sessionIn(unrelated), { agent_id: null, agent_version: null });

// The deepest project wins inside nested installs, and a user-scope agent covers everywhere else.
writeState({ id: "agent-y", name: "everyday" }, [projectAgent, nestedAgent, userAgent]);
assert.deepEqual(await sessionIn(path.join(nestedB, "src")), { agent_id: "agent-b", agent_version: "2.0.0" });
assert.deepEqual(await sessionIn(projectA), { agent_id: "agent-x", agent_version: "1.0.0" });
assert.deepEqual(await sessionIn(unrelated), { agent_id: "agent-y", agent_version: "3.0.0" });

// An agent pulled at both scopes stays the machine-wide default.
writeState({ id: "agent-x", name: "pr-verify-qa" }, [projectAgent, { ...projectAgent, scope: "user", directory: home }]);
assert.deepEqual(await sessionIn(unrelated), { agent_id: "agent-x", agent_version: "1.0.0" });

server.close();
fs.rmSync(home, { recursive: true, force: true });
console.log("project agent attribution ok");
