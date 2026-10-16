// SPDX-FileCopyrightText: 2026 Observal Contributors
// SPDX-License-Identifier: Apache-2.0

import assert from "node:assert/strict";
import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

const home = fs.mkdtempSync(path.join(os.tmpdir(), "observal-pi-updates-"));
process.env.HOME = home;
const dir = path.join(home, ".observal");
// The key a real Pi worker uses for a session (the extension derives the same one).
const piKey = (session: string) => crypto.createHash("sha256")
  .update(["http://localhost:8000", "alice", session].join("\0")).digest("hex");
fs.mkdirSync(dir, { recursive: true });
fs.writeFileSync(path.join(dir, "config.json"), JSON.stringify({
  server_url: "http://localhost:8000", access_token: "local-token", user_id: "alice",
}));
const worker = path.join(home, "mock-observal");
fs.writeFileSync(worker, `#!/usr/bin/env node
const fs = require('fs'); const path = require('path');
const args = process.argv.slice(2);
if (args[0] !== '_startup-check' && args[0] !== '_startup-apply') process.exit(2);
const key = args[args.indexOf('--notice-key') + 1];
const session = args[args.indexOf('--session-id') + 1];
fs.appendFileSync(path.join(process.env.HOME, 'worker-starts'), args[0] + ':' + session + '\\n');
setTimeout(() => {
  const root = path.join(process.env.HOME, '.observal', 'update-notices');
  fs.mkdirSync(root, {recursive: true, mode: 0o700});
  fs.writeFileSync(path.join(root, key + '.json'), JSON.stringify({schema: 1,
    registry: 'http://localhost:8000', account_id: 'alice', session_id: session,
    checked_at: Math.floor(Date.now()/1000), items: [{name: 'alice/code', type: 'agent',
      scope: 'user', status: session === 'pilot-session' ? 'updated' : 'available', current_version: '1.0', latest_version: '2.0',
      description: 'Fixed bugs', manual_command: 'observal agent pull alice/code --upgrade'}]}), {mode: 0o600});
}, 200);
`, { mode: 0o700 });
process.env.OBSERVAL_CLI_BIN = worker;

const handlers = new Map<string, (event: any, ctx: any) => Promise<void>>();
const pi = { on: (name: string, fn: any) => handlers.set(name, fn), registerCommand() {} };
const extension = await import(`../extensions/observal.ts?updates=${Date.now()}`);
extension.default(pi);
const messages: string[] = [];
const updateMessages = () => messages.filter((text) => text.includes("Fixed bugs") || text.includes("update check"));
const context = (session: string, ui: boolean) => ({
  cwd: home, hasUI: ui,
  sessionManager: { getSessionFile: () => null, getSessionId: () => session },
  ui: { notify: (text: string) => messages.push(text), theme: { fg: (_: string, text: string) => text }, setStatus() {} },
});
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
async function waitUntil(predicate: () => boolean): Promise<void> {
  for (let i = 0; i < 40; i++) {
    if (predicate()) return;
    await sleep(100);
  }
  assert.fail("startup result not delivered within 4 seconds");
}

await handlers.get("session_start")!({ reason: "startup" }, context("print-session", false));
await sleep(50);
assert.equal(fs.existsSync(path.join(dir, "update-notices")), false, "print mode must not start worker");

await handlers.get("session_start")!({ reason: "startup" }, context("session-a", true));
await handlers.get("session_start")!({ reason: "resume" }, context("session-a", true));
await handlers.get("session_shutdown")!({}, context("session-a", true));
const shutdown = path.join(dir, "update-shutdown");
assert.equal(fs.readdirSync(shutdown).length, 1, "departure marker written before a worker can start mutation");
assert.equal(fs.statSync(path.join(shutdown, fs.readdirSync(shutdown)[0]!)).mode & 0o777, 0o600);
await waitUntil(() => fs.existsSync(path.join(dir, "update-notices"))
  && fs.readdirSync(path.join(dir, "update-notices")).length === 1);
assert.equal(updateMessages().length, 0, "completed check must not notify a departed session");
assert.equal(fs.readdirSync(path.join(dir, "update-notices")).length, 1, "result survives shutdown");

await handlers.get("session_start")!({ reason: "startup" }, context("session-b", true));
assert.equal(updateMessages().length, 1);
assert.match(updateMessages()[0], /previous Pi session/);
assert.match(updateMessages()[0], /this check did not change files/);
assert.match(updateMessages()[0], /Fixed bugs/);
assert.match(updateMessages()[0], /1\.0 → 2\.0/);
await waitUntil(() => updateMessages().length === 2);
assert.equal(updateMessages().length, 2, "live result delivered once after child exits");
assert.equal(fs.readdirSync(path.join(dir, "update-notices")).length, 0);
assert.deepEqual(fs.readFileSync(path.join(home, "worker-starts"), "utf-8").trim().split("\n"),
  ["_startup-apply:session-a", "_startup-apply:session-b"], "start once per Pi session");
fs.writeFileSync(path.join(dir, "update-notices", `${piKey("old-c")}.json`), JSON.stringify({
  schema: 1, registry: "http://localhost:8000", account_id: "alice", session_id: "old-c",
  checked_at: Math.floor(Date.now() / 1000), items: [{ name: "alice/code", type: "agent",
    scope: "user", status: "updated", current_version: "1.0", latest_version: "2.0",
    changelog: "Author release notes", reason: "Installed on disk and verified. Reload Pi." }],
}), { mode: 0o600 });
await handlers.get("session_start")!({ reason: "resume" }, context("session-b", true));
assert.match(messages.at(-1)!, /installed on disk/);
assert.match(messages.at(-1)!, /re-select the saved agent with \/agent/);
assert.match(messages.at(-1)!, /Author release notes/);
const multiFile = path.join(dir, "update-notices", `${piKey("old-e")}.json`);
fs.writeFileSync(multiFile, JSON.stringify({
  schema: 1, registry: "http://localhost:8000", account_id: "alice", session_id: "old-e",
  checked_at: Math.floor(Date.now() / 1000), items: Array.from({ length: 20 }, (_, index) => ({
    name: `alice/item-${index}`, scope: "user", status: "available",
    current_version: "1.0", latest_version: "2.0", description: `notes-${index} ${"x".repeat(500)}`,
  })),
}), { mode: 0o600 });
await handlers.get("session_start")!({ reason: "resume" }, context("session-b", true));
assert.ok(messages.some((message) => message.includes("notes-19")), "the last item must not be truncated");
assert.equal(fs.existsSync(multiFile), false);
const recoveryFile = path.join(dir, "update-notices", `${piKey("old-d")}.json`);
fs.writeFileSync(recoveryFile, JSON.stringify({
  schema: 1, registry: "http://localhost:8000", account_id: "alice", session_id: "old-d",
  checked_at: Math.floor(Date.now() / 1000), items: [{name: "alice/code", scope: "user", status: "failed",
    current_version: "1.0", latest_version: "2.0", description: "x".repeat(3900),
    reason: "Installer failed after admission; inspect managed files before retrying."}],
}), { mode: 0o600 });
await handlers.get("session_start")!({ reason: "resume" }, context("session-b", true));
assert.ok(messages.some((message) => message.includes("update failure from a previous Pi session")));
assert.ok(messages.some((message) => message.includes("inspect managed files before retrying")),
  "failure notices must explain manual inspection even with long release notes");
assert.equal(fs.existsSync(recoveryFile), false);
const pendingKey = piKey("session-a");
const pendingFile = path.join(dir, "update-notices", `${pendingKey}.pending`);
const backupDir = path.join(dir, "update-backups", pendingKey);
fs.mkdirSync(backupDir, {recursive: true, mode: 0o700});
fs.writeFileSync(path.join(backupDir, "manifest.json"), JSON.stringify({schema: 1}));
const pendingIdentity = {registry: "http://localhost:8000", account_id: "alice", session_id: "session-a"};
fs.writeFileSync(pendingFile, JSON.stringify({schema: 1, state: "pending", ...pendingIdentity, backup_dir: backupDir,
  checked_at: Math.floor(Date.now() / 1000), item: {name: "alice/code", current_version: "1.0", latest_version: "2.0"}}),
{mode: 0o600});
const beforePending = messages.length;
await handlers.get("session_start")!({ reason: "resume" }, context("session-b", true));
assert.match(messages[beforePending]!, /outcome pending from a Pi session/);
assert.match(messages[beforePending]!, /Files may have changed/);
assert.ok(messages.slice(beforePending).some((message) => message.includes("inspect managed profiles and installed locks")),
  "unsealed mid-write crash must require manual inspection");
assert.ok(messages.slice(beforePending).some((message) => message.includes(backupDir)),
  "unresolved outcome must surface its private backup when available");
assert.ok(fs.existsSync(pendingFile), "unresolved journal must never be deleted on notification");
const unsealed = path.join(dir, "update-notices", `${pendingKey}.json`);
fs.writeFileSync(unsealed, JSON.stringify({schema: 1, ...pendingIdentity, journaled: true,
  outcome_final: true, checked_at: Math.floor(Date.now() / 1000), items: [{name: "alice/code",
    status: "updated", current_version: "1.0", latest_version: "2.0", scope: "user"}]}), {mode: 0o600});
const beforeSeal = messages.length;
await handlers.get("session_start")!({ reason: "resume" }, context("session-b", true));
assert.equal(messages.length, beforeSeal, "an unsealed final file cannot claim a verified install");
assert.ok(fs.existsSync(pendingFile) && fs.existsSync(unsealed));
const sealFile = path.join(dir, "update-notices", `${pendingKey}.complete`);
fs.writeFileSync(sealFile, JSON.stringify({schema: 1, state: "complete", ...pendingIdentity}), {mode: 0o600});
await handlers.get("session_start")!({ reason: "resume" }, context("session-b", true));
assert.match(messages.at(-1)!, /installed on disk/);
assert.ok(!fs.existsSync(pendingFile) && !fs.existsSync(unsealed) && !fs.existsSync(sealFile));
await handlers.get("session_shutdown")!({}, context("session-b", true));

await handlers.get("session_start")!({ reason: "startup" }, context("pilot-no-ui", false));
await sleep(100);
assert.equal(fs.readFileSync(path.join(home, "worker-starts"), "utf-8").trim().split("\n").length, 2,
  "pilot never launches an installer without UI");
await handlers.get("session_start")!({ reason: "startup" }, context("pilot-session", true));
await waitUntil(() => messages.some((message) => message.includes("installed on disk") && message.includes("Fixed bugs")));
assert.match(fs.readFileSync(path.join(home, "worker-starts"), "utf-8"), /_startup-apply:pilot-session/);
await handlers.get("session_shutdown")!({}, context("pilot-session", true));

// Claude Code shares this directory, registry and account but uses its own key.
// Pi must neither show nor delete another host's notice, journal or seal.
const claudeKey = (session: string) => crypto.createHash("sha256")
  .update(["claude-code", "http://localhost:8000", "alice", session].join("\0")).digest("hex");
const claudeDir = path.join(dir, "update-notices");
const claudeIdentity = { registry: "http://localhost:8000", account_id: "alice", session_id: "claude-session" };
const claudeNotice = path.join(claudeDir, `${claudeKey("claude-session")}.json`);
const claudeJournal = path.join(claudeDir, `${claudeKey("claude-session")}.pending`);
const claudeSeal = path.join(claudeDir, `${claudeKey("claude-session")}.complete`);
const orphanSeal = path.join(claudeDir, `${claudeKey("orphan-session")}.complete`);
fs.writeFileSync(claudeNotice, JSON.stringify({schema: 1, harness: "claude-code", ...claudeIdentity,
  journaled: true, outcome_final: true, checked_at: Math.floor(Date.now() / 1000), items: [{name: "alice/claude-agent",
    status: "updated", current_version: "1.0", latest_version: "2.0", scope: "user"}]}), {mode: 0o600});
const eightDaysAgo = new Date(Date.now() - 8 * 24 * 60 * 60 * 1000);
fs.utimesSync(claudeNotice, eightDaysAgo, eightDaysAgo); // Expiry must not delete another host's file either.
fs.writeFileSync(claudeSeal, JSON.stringify({schema: 1, state: "complete", ...claudeIdentity}), {mode: 0o600});
fs.writeFileSync(claudeJournal, JSON.stringify({schema: 1, state: "pending", ...claudeIdentity,
  item: {name: "alice/claude-agent", current_version: "1.0", latest_version: "2.0"}}), {mode: 0o600});
fs.writeFileSync(orphanSeal, JSON.stringify({schema: 1, state: "complete", ...claudeIdentity, session_id: "orphan-session"}),
  {mode: 0o600});
const beforeClaude = messages.length;
await handlers.get("session_start")!({ reason: "resume" }, context("pi-after-claude", true));
await sleep(150);
assert.equal(messages.slice(beforeClaude).some((text) => text.includes("claude-agent") || text.includes("pending from a Pi session")),
  false, "Pi must not report a Claude Code result or journal as its own");
for (const kept of [claudeNotice, claudeJournal, claudeSeal, orphanSeal]) {
  assert.ok(fs.existsSync(kept), `Pi must not delete another host's file: ${path.basename(kept)}`);
}
await handlers.get("session_shutdown")!({}, context("pi-after-claude", true));
for (const leftover of [claudeNotice, claudeJournal, claudeSeal, orphanSeal]) fs.rmSync(leftover, { force: true });

// A sealed, journaled Pi outcome must survive the expiry window until it is delivered;
// deleting it would leave its pending record looking unresolved and block later installs.
const oldKey = piKey("old-journal");
const oldIdentity = { registry: "http://localhost:8000", account_id: "alice", session_id: "old-journal" };
const oldResult = path.join(claudeDir, `${oldKey}.json`);
const oldJournal = path.join(claudeDir, `${oldKey}.pending`);
const oldSealFile = path.join(claudeDir, `${oldKey}.complete`);
fs.writeFileSync(oldResult, JSON.stringify({schema: 1, ...oldIdentity, journaled: true, outcome_final: true,
  checked_at: Math.floor(Date.now() / 1000), items: [{name: "alice/code", status: "updated", current_version: "1.0",
    latest_version: "2.0", scope: "user"}]}), {mode: 0o600});
fs.writeFileSync(oldSealFile, JSON.stringify({schema: 1, state: "complete", ...oldIdentity}), {mode: 0o600});
fs.writeFileSync(oldJournal, JSON.stringify({schema: 1, state: "pending", ...oldIdentity,
  item: {name: "alice/code", current_version: "1.0", latest_version: "2.0"}}), {mode: 0o600});
fs.utimesSync(oldResult, eightDaysAgo, eightDaysAgo);
const beforeOld = messages.length;
await handlers.get("session_start")!({ reason: "resume" }, context("pi-old-journal", true));
await sleep(150);
assert.ok(messages.slice(beforeOld).some((text) => text.includes("installed on disk")),
  "an old but verified journaled outcome must still be delivered");
assert.ok(!messages.slice(beforeOld).some((text) => text.includes("pending from a Pi session")),
  "its journal must not be reported as unresolved");
assert.ok(!fs.existsSync(oldResult) && !fs.existsSync(oldJournal) && !fs.existsSync(oldSealFile),
  "delivery clears the result, journal and seal together");
await handlers.get("session_shutdown")!({}, context("pi-old-journal", true));

// A corrupt spool file (sorting first) must not stop later results from being delivered,
// and must not be deleted since its owner cannot be established.
const corruptJson = path.join(claudeDir, `${"0".repeat(64)}.json`);
const corruptPending = path.join(claudeDir, `${"0".repeat(63)}1.pending`);
fs.writeFileSync(corruptJson, "{not json", { mode: 0o600 });
fs.writeFileSync(corruptPending, "{also not json", { mode: 0o600 });
const goodKey = piKey("after-corrupt");
const goodNotice = path.join(claudeDir, `${goodKey}.json`);
fs.writeFileSync(goodNotice, JSON.stringify({schema: 1, registry: "http://localhost:8000", account_id: "alice",
  session_id: "after-corrupt", checked_at: Math.floor(Date.now() / 1000), items: [{name: "alice/code",
    type: "agent", scope: "user", status: "available", current_version: "1.0", latest_version: "2.0",
    description: "Delivered despite corruption"}]}), { mode: 0o600 });
const beforeCorrupt = messages.length;
await handlers.get("session_start")!({ reason: "resume" }, context("pi-corrupt", true));
await sleep(150);
assert.ok(messages.slice(beforeCorrupt).some((text) => text.includes("Delivered despite corruption")),
  "a corrupt spool file must not block delivery of later results");
assert.ok(fs.existsSync(corruptJson) && fs.existsSync(corruptPending), "corrupt files of unknown owner are left alone");
assert.ok(!fs.existsSync(goodNotice));
await handlers.get("session_shutdown")!({}, context("pi-corrupt", true));
fs.rmSync(corruptJson, { force: true });
fs.rmSync(corruptPending, { force: true });

fs.rmSync(home, { recursive: true, force: true });
console.log("startup update notices ok");
