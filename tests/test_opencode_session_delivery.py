# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import re
import subprocess
from typing import TYPE_CHECKING

import pytest

from observal_shared.opencode_plugin_source import OPENCODE_PLUGIN_SOURCE, OPENCODE_PLUGIN_VERSION

if TYPE_CHECKING:
    from pathlib import Path


def _node_supports_strip_types() -> tuple[bool, str]:
    """Return whether `node --experimental-strip-types` can run on this machine."""
    try:
        version_output = subprocess.run(
            ["node", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, OSError):
        return False, "node executable not found"
    if version_output.returncode != 0:
        return False, f"node --version failed: {version_output.stderr.strip()}"
    match = re.search(r"v(\d+)\.(\d+)", version_output.stdout)
    if not match:
        return False, f"could not parse node version from {version_output.stdout.strip()!r}"
    major, minor = int(match.group(1)), int(match.group(2))
    # --experimental-strip-types landed in Node 22.6.0 (package.json engines).
    if (major, minor) < (22, 6):
        return False, f"requires Node >= 22.6 for --experimental-strip-types (found {version_output.stdout.strip()})"
    return True, ""


def test_generated_plugin_uses_durable_acknowledged_delivery():
    assert OPENCODE_PLUGIN_VERSION == "8"
    assert "data.registries?.[registryKey(config.server_url)]" in OPENCODE_PLUGIN_SOURCE
    assert "saveSessionState(state); // Durable before network delivery." in OPENCODE_PLUGIN_SOURCE
    assert "acknowledged_line" in OPENCODE_PLUGIN_SOURCE
    assert "pushedMessageIds" not in OPENCODE_PLUGIN_SOURCE
    assert "state.acknowledgedLine + 1" in OPENCODE_PLUGIN_SOURCE


def test_native_outbox_survives_restart_and_clears_only_after_ack(tmp_path: Path):
    supported, reason = _node_supports_strip_types()
    if not supported:
        pytest.fail(f"{reason}; see CONTRIBUTING.md prerequisites")
    plugin = tmp_path / "plugin.ts"
    plugin.write_text(
        OPENCODE_PLUGIN_SOURCE
        + "\nexport { newSessionState, statePath, loadSessionState, saveSessionState, applyAcknowledgement };\n"
    )
    runner = tmp_path / "runner.mjs"
    runner.write_text(
        """
import assert from "node:assert/strict";
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const plugin = await import(pathToFileURL(process.argv[2]));
let state = plugin.newSessionState("session");
state.agentName = "agent";
state.pending = {
  destination: "http://server",
  userId: "user",
  startLine: 0,
  endLine: 1,
  lines: ["one", "two"],
  agentId: "agent-id",
  agentVersion: "1.0.0",
  final: false,
};
plugin.saveSessionState(state);
const path = plugin.statePath("session");
assert.equal(existsSync(path), true);
assert.equal(Number.parseInt((await import("node:fs")).statSync(path).mode.toString(8).slice(-3)), 600);

state = plugin.loadSessionState("session");
assert.equal(state.pending.endLine, 1);
assert.equal(plugin.applyAcknowledgement(state, { acknowledged_line: 0 }), false);
assert.equal(plugin.loadSessionState("session").pending.endLine, 1);
assert.equal(plugin.applyAcknowledgement(state, { acknowledged_line: 1 }), true);
state = plugin.loadSessionState("session");
assert.equal(state.pending, null);
assert.equal(state.acknowledgedLine, 1);

let repair = plugin.newSessionState("repair");
repair.pending = { ...state.pending, startLine: 2, endLine: 2, lines: ["three"], final: true };
assert.equal(plugin.applyAcknowledgement(repair, {
  acknowledged_line: 0,
  acknowledged_offset: 4,
  repair_from_line: 1,
}), "repair");
repair = plugin.loadSessionState("repair");
assert.equal(repair.acknowledgedLine, 0);
assert.equal(repair.pending, null);
assert.equal(repair.needsReplay, true);
assert.equal(repair.repairFinal, true);

writeFileSync(path, "not-json");
state = plugin.loadSessionState("session");
assert.equal(state.acknowledgedLine, -1);
assert.equal(existsSync(path), false);
assert.equal(readdirSync(path.slice(0, path.lastIndexOf("/"))).some((name) => name.includes(".corrupt.")), true);

const observalDir = join(process.env.HOME, ".observal");
mkdirSync(observalDir, { recursive: true });
writeFileSync(join(observalDir, "config.json"), JSON.stringify({
  server_url: "http://server",
  access_token: "token",
  user_id: "user",
}));
writeFileSync(join(observalDir, "lockfile.json"), JSON.stringify({
  lock_version: 2,
  registries: {
    "http://server": {
      server_url: "http://server",
      harnesses: { opencode: { agents: [{ name: "custom", id: "agent-id", version: "1.0.0", scope: "user" }] } },
    },
  },
}));
let clientMessages = [
  { info: { id: "m1", role: "user", timestamp: "2026-01-01T00:00:00Z" }, parts: [{ type: "text", text: "hello" }] },
  { info: { id: "m2", role: "assistant", timestamp: "2026-01-01T00:00:01Z" }, parts: [{ type: "text", text: "hi" }] },
];
const client = {
  app: { log: async () => {} },
  session: { messages: async () => ({ data: clientMessages }) },
};
let shouldFail = true;
let repairFinalOnce = false;
let requests = 0;
let ingestRequests = 0;
let checkpointLine = -1;
const ingestPayloads = [];
globalThis.fetch = async (_url, options = {}) => {
  requests += 1;
  if (!options.method || options.method === "GET") {
    return { ok: true, json: async () => ({ acknowledged_line: checkpointLine, acknowledged_offset: 0 }) };
  }
  ingestRequests += 1;
  const payload = JSON.parse(options.body);
  ingestPayloads.push(payload);
  if (shouldFail) return { ok: false, status: 503 };
  if (repairFinalOnce && payload.final) {
    repairFinalOnce = false;
    checkpointLine = 499;
    return { ok: true, json: async () => ({
      acknowledged_line: 499,
      acknowledged_offset: 0,
      repair_from_line: 500,
      integrity_ok: false,
    }) };
  }
  checkpointLine = payload.start_offset + payload.lines.length - 1;
  return { ok: true, json: async () => ({ acknowledged_line: checkpointLine, acknowledged_offset: 0 }) };
};
const hooks = await plugin.ObservalPlugin({ client, directory: "/work" });
await hooks.event({ event: { type: "session.created", properties: { sessionID: "delivery", agent: "custom" } } });
await hooks.event({ event: { type: "message.updated", properties: { sessionID: "delivery" } } });
await hooks.event({ event: { type: "session.idle", properties: { sessionID: "delivery" } } });
let delivery = plugin.loadSessionState("delivery");
assert.equal(delivery.acknowledgedLine, -1);
assert.equal(delivery.pending.endLine, 1);
shouldFail = false;
await hooks.event({ event: { type: "session.idle", properties: { sessionID: "delivery" } } });
delivery = plugin.loadSessionState("delivery");
assert.equal(delivery.pending, null);
assert.equal(delivery.acknowledgedLine, 1);
assert.equal(requests, 3);
assert.equal(ingestRequests, 2);

await hooks.event({ event: { type: "session.created", properties: { sessionID: "recovered", agent: "custom" } } });
await hooks.event({ event: { type: "message.updated", properties: { sessionID: "recovered" } } });
await hooks.event({ event: { type: "session.idle", properties: { sessionID: "recovered" } } });
const recovered = plugin.loadSessionState("recovered");
assert.equal(recovered.acknowledgedLine, 1);
assert.equal(recovered.pending, null);
assert.equal(ingestRequests, 2);

checkpointLine = -1;
repairFinalOnce = true;
clientMessages = Array.from({ length: 1001 }, (_, index) => ({
  info: { id: `large-${index}`, role: index % 2 ? "assistant" : "user", timestamp: "2026-01-01T00:00:00Z" },
  parts: [{ type: "text", text: String(index) }],
}));
await hooks.event({ event: { type: "session.created", properties: { sessionID: "large", agent: "custom" } } });
await hooks.event({ event: { type: "message.updated", properties: { sessionID: "large" } } });
await hooks.event({ event: { type: "session.idle", properties: { sessionID: "large", final: true } } });
const large = plugin.loadSessionState("large");
assert.equal(large.acknowledgedLine, 1000);
assert.equal(large.pending, null);
assert.equal(large.needsReplay, false);
const largePayloads = ingestPayloads.slice(2);
assert.deepEqual(largePayloads.map((payload) => payload.lines.length), [500, 500, 1, 500, 1]);
assert.deepEqual(largePayloads.map((payload) => payload.start_offset), [0, 500, 1000, 500, 1000]);
assert.equal(largePayloads.at(-1).final, true);

// Sessions without a pulled registry agent are still reported, unattributed.
const before = ingestPayloads.length;
shouldFail = false;
checkpointLine = -1;
clientMessages = clientMessages.slice(0, 2);
for (const [id, properties] of [["builtin", { agent: "build" }], ["plain", {}]]) {
  await hooks.event({ event: { type: "session.created", properties: { sessionID: id, ...properties } } });
  await hooks.event({ event: { type: "message.updated", properties: { sessionID: id } } });
  await hooks.event({ event: { type: "session.idle", properties: { sessionID: id } } });
  checkpointLine = -1;
}
const plainPayloads = ingestPayloads.slice(before);
assert.deepEqual(plainPayloads.map((payload) => payload.session_id), ["builtin", "plain"]);
assert.ok(plainPayloads.every((payload) => payload.agent_id === null && payload.agent_version === null));
assert.ok(plainPayloads.every((payload) => payload.lines.length === 2));
"""
    )
    env = os.environ | {"HOME": str(tmp_path)}

    result = subprocess.run(
        ["node", "--experimental-strip-types", str(runner), str(plugin)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
