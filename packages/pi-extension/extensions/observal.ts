// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-License-Identifier: Apache-2.0

/**
 * Observal session telemetry extension for Pi.
 *
 * Reads the session JSONL file incrementally on lifecycle events and POSTs
 * raw lines to the Observal ingest API. Zero runtime dependencies - uses
 * only node:* built-ins.
 *
 * Design principles:
 * - Fail-open: never throw, never crash pi
 * - 5s timeout on all HTTP calls
 * - Generation counter for async safety
 * - Durable batches before network delivery
 * - Cursor advancement only after contiguous server acknowledgement
 * - Chunk at 500 lines per POST to avoid 413
 */

import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { spawn } from "node:child_process";
import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as http from "node:http";
import * as https from "node:https";
import * as os from "node:os";
import * as path from "node:path";

// ─── Types ───────────────────────────────────────────────────────────────────

interface ObservalConfig {
  server_url: string;
  access_token: string;
  user_id?: string;
  agent_id?: string;
  agent_version?: string;
}

export interface PendingBatch {
  session_id: string;
  destination: string;
  user_id?: string;
  payload: Record<string, unknown>;
  end_line: number;
  end_offset: number;
  final: boolean;
}

interface CursorEntry {
  offset: number;
  line_count: number;
  finalized?: boolean;
  last_pushed_at?: number;
  local_valid?: boolean;
}

interface LayerFileEntry {
  path: string;
  hash: string;
  size: number;
  source: string;
  content?: string;
}

interface LayerSnapshot {
  hash: string;
  harnesses: Record<string, LayerFileEntry[]>;
  lockfile_hash: string;
  pinned_versions: Record<string, unknown>;
  drift: Record<string, unknown>;
}

interface ObservalState {
  config: ObservalConfig | null;
  sessionFile: string | null;
  sessionId: string;
  cwd: string;
  byteOffset: number;
  lineCount: number;
  generation: number;
  layerHash: string | null;
  layerSnapshot: LayerSnapshot | null;
}

// ─── Constants ───────────────────────────────────────────────────────────────

const OBSERVAL_DIR = path.join(os.homedir(), ".observal");
const CONFIG_PATH = path.join(OBSERVAL_DIR, "config.json");
const SYNC_STATE_PATH = path.join(OBSERVAL_DIR, "sync_state.json");
const LAYER_SNAPSHOT_PATH = path.join(OBSERVAL_DIR, "layer_snapshot.json");
const LOCKFILE_PATH = path.join(OBSERVAL_DIR, "lockfile.json");
const OUTBOX_DIR = path.join(OBSERVAL_DIR, "pi_session_outbox");
const UPDATE_NOTICE_DIR = path.join(OBSERVAL_DIR, "update-notices");
const UPDATE_SHUTDOWN_DIR = path.join(OBSERVAL_DIR, "update-shutdown");
const UPDATE_NOTICE_MAX_BYTES = 64 * 1024;
const UPDATE_NOTICE_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;
// Written by `observal discover use` and the install commands; read here so the
// session payload can say which registry resources this session relied on.
const CAPABILITY_LOCK_PATH = path.join(OBSERVAL_DIR, "capability_lock.jsonl");
const CAPABILITY_LEAD_MS = 15 * 60 * 1000;
const CAPABILITY_FALLBACK_MS = 24 * 60 * 60 * 1000;
const MAX_CAPABILITIES_PER_PUSH = 200;
const TIMEOUT_MS = 5_000;
const MAX_LINES_PER_CHUNK = 500;
const RECOVERY_MAX_SESSIONS = 5;
const RECOVERY_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000; // 7 days
const MAX_LAYER_FILE_SIZE = 512 * 1024;
const MAX_OUTBOX_BYTES = 256 * 1024 * 1024;

export function acknowledgementCovers(acknowledgement: unknown, pending: PendingBatch): boolean {
  if (!acknowledgement || typeof acknowledgement !== "object") return false;
  const acknowledgedLine = (acknowledgement as Record<string, unknown>).acknowledged_line;
  return Number.isInteger(acknowledgedLine) && Number(acknowledgedLine) >= pending.end_line;
}

// ─── Extension Entry ─────────────────────────────────────────────────────────

export default function (pi: ExtensionAPI) {
  let state: ObservalState | null = null;
  let updateCheckSession: string | null = null;
  const startedChecks = new Set<string>();
  const pendingWarningsShown = new Set<string>();

  pi.on("session_start", async (event, ctx) => {
    updateCheckSession = null;
    state = initState(ctx);

    // Notices are delivered after the local worker exits, never by blocking
    // Pi startup or sending subprocess output through the session protocol.
    if (ctx.hasUI && state.config?.user_id && ![
      "OBSERVAL_ACCESS_TOKEN", "OBSERVAL_API_KEY", "OBSERVAL_TOKEN", "OBSERVAL_SERVER_URL",
    ].some((name) => process.env[name] || process.env[`${name}_FILE`])) {
      try {
        updateCheckSession = state.sessionId;
        deliverPendingUpdateNotices(state.config, ctx);
        const key = noticeKey(state.config, state.sessionId);
        if (!startedChecks.has(key)) {
          if (startedChecks.size >= 100) startedChecks.clear();
          startedChecks.add(key);
          startUpdateCheck(state.config, state.sessionId, ctx);
        }
      } catch {
        // An invalid local registry must never interrupt Pi startup.
      }
    }

    if (state.config && state.layerSnapshot) {
      uploadLayerSnapshot(state.config, state.layerSnapshot)
        .then((ok) => {
          if (!ok && ctx.hasUI) ctx.ui.notify("Layer snapshot upload failed", "warning");
        })
        .catch((err) => {
          if (ctx.hasUI) ctx.ui.notify(`Layer snapshot upload failed: ${err.message}`, "warning");
        });
    }

    // On fresh startup, attempt crash recovery (fire-and-forget)
    if (event.reason === "startup" && state.config) {
      recoverStaleSessions(state, ctx).catch(() => {});
    }

    if (state.config && ctx.hasUI) {
      const theme = ctx.ui.theme;
      ctx.ui.setStatus("observal", theme.fg("dim", "● observal"));
    }
  });

  pi.on("agent_end", async (_event, _ctx) => {
    if (!state?.config || !state.sessionFile) return;
    await pushNewLines(state, { final: false });
  });

  pi.on("session_shutdown", async (_event, _ctx) => {
    // Record departure before awaiting telemetry. The apply worker checks
    // the marker under its install gate, but Pi does not share that gate:
    // shutdown racing after the last check can still lead to a verified install.
    if (updateCheckSession && state?.config?.user_id) {
      try {
        fs.mkdirSync(UPDATE_SHUTDOWN_DIR, { recursive: true, mode: 0o700 });
        const marker = path.join(UPDATE_SHUTDOWN_DIR, `${noticeKey(state.config, updateCheckSession)}.json`);
        const temp = `${marker}.${process.pid}.${crypto.randomUUID()}.tmp`;
        fs.writeFileSync(temp, "{}", { mode: 0o600, flag: "wx" });
        fs.renameSync(temp, marker);
      } catch { /* A failed marker write cannot guarantee shutdown prevents admission; verify before enabling apply. */ }
    }
    updateCheckSession = null;
    if (!state?.config || !state.sessionFile) return;
    await pushNewLines(state, { final: true });
    state = null;
  });

  // ─── /obs-sync command ─────────────────────────────────────────────────

  pi.registerCommand("agent", {
    description: "Manage and swap active Observal agents",
    handler: async (args, ctx) => {
      const agentId = args.trim();
      const PI_HOME = path.join(os.homedir(), ".pi", "agent");
      const AGENTS_DIR = path.join(PI_HOME, "agents");

      function backupDefault() {
        if (!fs.existsSync(AGENTS_DIR)) fs.mkdirSync(AGENTS_DIR, { recursive: true });
        const defaultDir = path.join(AGENTS_DIR, "default");
        if (fs.existsSync(defaultDir)) return; // already backed up

        fs.mkdirSync(defaultDir, { recursive: true });

        const filesToCopy = [
          { name: "AGENTS.md", isDir: false },
          { name: "SYSTEM.md", isDir: false },
          { name: "mcp.json", isDir: false },
          { name: "skills", isDir: true },
          { name: "sandboxes", isDir: true }
        ];

        for (const f of filesToCopy) {
          const src = path.join(PI_HOME, f.name);
          const dest = path.join(defaultDir, f.name);
          if (fs.existsSync(src)) {
            fs.cpSync(src, dest, { recursive: true });
          }
        }
      }

      function applyProfile(name: string) {
        const profileDir = path.join(AGENTS_DIR, name);
        if (!fs.existsSync(profileDir)) throw new Error(`Profile ${name} not found`);

        const activeItems = ["AGENTS.md", "SYSTEM.md", "mcp.json", "skills", "sandboxes"];
        for (const f of activeItems) {
          const target = path.join(PI_HOME, f);
          if (fs.existsSync(target)) {
            fs.rmSync(target, { recursive: true, force: true });
          }
        }

        for (const f of activeItems) {
          const src = path.join(profileDir, f);
          const dest = path.join(PI_HOME, f);
          if (fs.existsSync(src)) {
            fs.cpSync(src, dest, { recursive: true });
          }
        }
      }

      if (!fs.existsSync(AGENTS_DIR)) {
        fs.mkdirSync(AGENTS_DIR, { recursive: true });
      }

      // Automatically populate AGENTS_DIR from normal .pi/agent files if it's currently holding an active agent but no profile exists for it
      // but primarily we rely on observal agent pull populating agents/.
      backupDefault();

      let choice = agentId;

      if (!choice) {
        const profiles = fs.readdirSync(AGENTS_DIR).filter(d => fs.statSync(path.join(AGENTS_DIR, d)).isDirectory());
        if (profiles.length === 0) {
          ctx.ui.notify("No agents installed yet. Use the Observal skill or 'observal agent pull <agent> --harness pi' to install one.", "info");
          return;
        }

        const selected = await ctx.ui.select("Select agent to swap to:", profiles);
        if (!selected) return;
        choice = selected;
      }

      try {
        applyProfile(choice);

        if (state?.config) {
          const binding = resolvePiAgentBinding(choice);
          state.config.agent_id = choice === "default" ? undefined : binding.id;
          state.config.agent_version = choice === "default" ? undefined : binding.version;
          try {
            const configRaw = fs.readFileSync(CONFIG_PATH, "utf-8");
            const configJson = JSON.parse(configRaw);
            if (choice === "default") {
              delete configJson.active_agent;
            } else {
              configJson.active_agent = {
                id: binding.id,
                name: binding.name,
                ...(binding.version ? { version: binding.version } : {}),
              };
            }
            fs.writeFileSync(CONFIG_PATH, JSON.stringify(configJson, null, 2));
          } catch (err) {
            // ignore
          }

          state.layerSnapshot = buildPiLayerSnapshot(true);
          state.layerHash = state.layerSnapshot.hash;
          if (!(await uploadLayerSnapshot(state.config, state.layerSnapshot))) {
            ctx.ui.notify("Layer snapshot upload failed", "warning");
          }
        }

        const ok = await ctx.ui.confirm("Agent Swapped", `Swapped to ${choice}. Reload session now?`);
        if (ok) {
          await ctx.reload();
        }
      } catch (e: any) {
        ctx.ui.notify(`Error swapping agent: ${e.message}`, "error");
      }
    },
  });

  pi.registerCommand("obs-sync", {
    description: "Observal telemetry sync status",
    handler: async (args, ctx) => {
      const sub = args.trim();
      if (sub === "flush") {
        if (!state?.config || !state.sessionFile) {
          ctx.ui.notify("No active session or config", "warning");
          return;
        }
        await pushNewLines(state, { final: false });
        ctx.ui.notify(`Flushed (${state.lineCount} lines total)`, "info");
      } else if (sub === "config") {
        ctx.ui.notify(
          `Config: ${CONFIG_PATH}\nServer: ${state?.config?.server_url ?? "not configured"}`,
          "info",
        );
      } else {
        const synced = state?.lineCount ?? 0;
        const server = state?.config?.server_url ?? "not configured";
        ctx.ui.notify(`Observal: ${synced} lines pushed\nServer: ${server}`, "info");
      }
    },
  });

  // ─── Startup update notices and user-consented Pi apply ────────────────

  function registryKey(config: ObservalConfig): string {
    const url = new URL(config.server_url);
    url.hash = "";
    url.search = "";
    url.pathname = url.pathname.replace(/\/+$/, "");
    return url.toString().replace(/\/$/, "");
  }

  function noticeKey(config: ObservalConfig, sessionId: string): string {
    return crypto.createHash("sha256").update(
      [registryKey(config), config.user_id ?? "", sessionId].join("\0"),
    ).digest("hex");
  }

  // Claude Code workers share this directory and the same registry/account but
  // use a different key. A file belongs to this extension only if its name is the
  // Pi key derived from its own session_id; never consume or delete another host's.
  function ownsNoticeFile(config: ObservalConfig, fileName: string, record: any): boolean {
    return typeof record?.session_id === "string" && record.session_id.length > 0
      && fileName.slice(0, 64) === noticeKey(config, record.session_id);
  }

  // One unreadable or corrupt spool file must not stop delivery of the others,
  // and is never deleted because its owner cannot be established.
  function readSpoolJson(file: string): { value: any; mtimeMs: number } | null {
    try {
      const info = fs.lstatSync(file);
      if (!info.isFile() || info.isSymbolicLink() || info.size > UPDATE_NOTICE_MAX_BYTES) return null;
      return { value: JSON.parse(fs.readFileSync(file, "utf-8")), mtimeMs: info.mtimeMs };
    } catch { return null; }
  }

  function safeNotice(value: unknown, limit = 600): string {
    return String(value ?? "").replace(/[\x00-\x1f\x7f-\x9f]/g, " ").slice(0, limit);
  }

  function deliverPendingUpdateNotices(config: ObservalConfig, ctx: ExtensionContext): void {
    if (!ctx.hasUI || !fs.existsSync(UPDATE_NOTICE_DIR)) return;
    try {
      const files = fs.readdirSync(UPDATE_NOTICE_DIR);
      const isSealedOutcome = (key: string, identity: any): boolean => {
        try {
          const resultFile = path.join(UPDATE_NOTICE_DIR, `${key}.json`);
          const sealFile = path.join(UPDATE_NOTICE_DIR, `${key}.complete`);
          const resultStat = fs.lstatSync(resultFile);
          const sealStat = fs.lstatSync(sealFile);
          if (!resultStat.isFile() || resultStat.isSymbolicLink() || resultStat.size > UPDATE_NOTICE_MAX_BYTES
            || !sealStat.isFile() || sealStat.isSymbolicLink() || sealStat.size > UPDATE_NOTICE_MAX_BYTES) return false;
          const result = JSON.parse(fs.readFileSync(resultFile, "utf-8"));
          const seal = JSON.parse(fs.readFileSync(sealFile, "utf-8"));
          return result?.outcome_final === true && result.journaled === true && seal?.state === "complete"
            && result.registry === identity.registry && result.account_id === identity.account_id
            && result.session_id === identity.session_id && seal.registry === identity.registry
            && seal.account_id === identity.account_id && seal.session_id === identity.session_id;
        } catch { return false; }
      };
      // The final notice was delivered and deleted but the cleanup of its
      // completion seal failed. A seal alone is not an unresolved install.
      for (const name of files.filter((file) => /^[0-9a-f]{64}\.complete$/.test(file))) {
        const key = name.slice(0, 64);
        if (!fs.existsSync(path.join(UPDATE_NOTICE_DIR, `${key}.json`))
          && !fs.existsSync(path.join(UPDATE_NOTICE_DIR, `${key}.pending`))) {
          const sealPath = path.join(UPDATE_NOTICE_DIR, name);
          const sealInfo = fs.lstatSync(sealPath);
          if (!sealInfo.isFile() || sealInfo.isSymbolicLink() || sealInfo.size > UPDATE_NOTICE_MAX_BYTES) continue;
          let seal: any;
          try { seal = JSON.parse(fs.readFileSync(sealPath, "utf-8")); } catch { continue; }
          if (seal?.registry === registryKey(config) && seal.account_id === config.user_id
            && ownsNoticeFile(config, name, seal)) fs.unlinkSync(sealPath);
        }
      }
      // A write-ahead record survives a crash or a full/unwritable spool after
      // mutation. Never consume it as a success, or delete it on delivery.
      for (const name of files.filter((file) => /^[0-9a-f]{64}\.pending$/.test(file)).slice(0, 50)) {
        const file = path.join(UPDATE_NOTICE_DIR, name);
        const loadedRecord = readSpoolJson(file);
        if (!loadedRecord) continue;
        const record = loadedRecord.value;
        if (record?.schema !== 1 || record.state !== "pending" || record.registry !== registryKey(config)
          || record.account_id !== config.user_id || !ownsNoticeFile(config, name, record)) continue;
        if (isSealedOutcome(name.slice(0, 64), record)) continue;
        const shownKey = `${updateCheckSession}:${name}`;
        if (pendingWarningsShown.has(shownKey)) continue;
        const item = record.item ?? {};
        const label = safeNotice(item.name, 180);
        const completed = Array.isArray(record.completed) ? record.completed : [];
        const previous = completed.slice(0, 8)
          .map((entry: any) => `${safeNotice(entry.name, 80)} (${safeNotice(entry.status, 20)})`).join(", ");
        ctx.ui.notify(`Observal update outcome pending from a Pi session: ${label} `
          + `${safeNotice(item.current_version, 80)} → ${safeNotice(item.latest_version, 80)}. `
          + (previous ? `Earlier items in this worker: ${previous}${completed.length > 8 ? ", and more in the record" : ""}. ` : "")
          + "Files may have changed; inspect managed profiles and installed locks before trying again. "
          + `Unresolved local record: ${safeNotice(file, 1200)}`, "warning");
        if (typeof record.backup_dir === "string" && fs.existsSync(path.join(record.backup_dir, "manifest.json"))) {
          ctx.ui.notify(`Verified pre-update bytes are saved at ${safeNotice(record.backup_dir, 1200)}. `
            + "Do not restore over unrecognized edits; inspect the lock and files first.", "warning");
        }
        pendingWarningsShown.add(shownKey);
      }
      const pending = files.filter((name) => /^[0-9a-f]{64}\.json$/.test(name)).slice(0, 50);
      for (const name of pending) {
        const file = path.join(UPDATE_NOTICE_DIR, name);
        const loadedNotice = readSpoolJson(file);
        if (!loadedNotice) continue;
        const notice = loadedNotice.value;
        const stat = { mtimeMs: loadedNotice.mtimeMs };
        if (notice?.schema !== 1 || notice.registry !== registryKey(config)
          || notice.account_id !== config.user_id || !Array.isArray(notice.items)
          || !ownsNoticeFile(config, name, notice)) continue;
        // Expire only our own check-only results. A journaled outcome keeps its
        // pending record alive until it is delivered, so deleting it would make
        // a verified result look unresolved and block later installs.
        if (notice.journaled !== true && Date.now() - stat.mtimeMs > UPDATE_NOTICE_MAX_AGE_MS) {
          fs.unlinkSync(file);
          continue;
        }
        if (notice.journaled === true && !isSealedOutcome(name.slice(0, 64), notice)) continue;
        const messages: string[] = [];
        if (notice.session_id !== updateCheckSession) {
          const when = Number.isFinite(notice.checked_at)
            ? new Date(notice.checked_at * 1000).toLocaleString() : "earlier";
          messages.push(notice.items.some((item: any) => item.status === "updated")
            ? `Observal update result from a previous Pi session (${when}); re-select the saved agent with /agent and reload to activate it.`
            : notice.items.some((item: any) => item.status === "failed")
              ? `Observal update failure from a previous Pi session (${when}); inspect managed files before re-pulling.`
              : `Observal update check from a previous Pi session (${when}); this check did not change files.`);
        }
        if (notice.warning) messages.push(`Observal: ${safeNotice(notice.warning)}`);
        for (const item of notice.items.slice(0, 20)) {
          const version = `${safeNotice(item.current_version, 80)} → ${safeNotice(item.latest_version, 80)}`;
          const label = item.status === "updated" ? "installed on disk" : item.status === "failed"
            ? "update failed" : item.status === "skipped" ? "automatic update skipped"
            : item.status === "available" ? "update available" : "newer version unverified";
          messages.push(`Observal: ${label} ${safeNotice(item.name, 180)} ${version} (${safeNotice(item.scope, 20)})`);
          if (item.status !== "unverified") {
            const target = safeNotice(item.latest_version, 80);
            messages.push(item.description || item.changelog
              ? `Author notes for target release ${target}: ${safeNotice(item.changelog || item.description)}`
              : `No release notes supplied for target release ${target}.`);
          }
          if (item.reason) messages.push(safeNotice(item.reason));
          if (item.manual_command) messages.push(`To update manually: ${safeNotice(item.manual_command, 400)}`);
        }
        if (messages.length > 0) {
          messages.push("`observal freeze` disables future auto-updates; manual updates remain available.");
          // Deliver every item: truncating a combined notice and deleting its
          // spool would silently lose version changes later in the list.
          let chunk = "";
          for (const line of messages) {
            if (chunk && chunk.length + line.length + 1 > 3900) {
              ctx.ui.notify(chunk, "info");
              chunk = "";
            }
            chunk += `${chunk ? "\n" : ""}${line}`;
          }
          if (chunk) ctx.ui.notify(chunk, "info");
        }
        // Only a *durable verified worker outcome* may resolve its journal;
        // a bridge diagnostic or check-only result must never erase it.
        if (notice.journaled === true) {
          const journal = path.join(UPDATE_NOTICE_DIR, `${name.slice(0, 64)}.pending`);
          if (fs.existsSync(journal)) {
            const loadedJournal = readSpoolJson(journal);
            if (!loadedJournal) continue;
            const pendingRecord = loadedJournal.value;
            if (pendingRecord?.registry !== notice.registry || pendingRecord.account_id !== notice.account_id
              || pendingRecord.session_id !== notice.session_id || pendingRecord.state !== "pending") continue;
            fs.unlinkSync(journal);
          }
        }
        // Mark delivered only after the UI accepts the notification. The
        // worker's file is never reused as a prompt or model message.
        fs.unlinkSync(file);
        if (notice.journaled === true) fs.unlinkSync(path.join(UPDATE_NOTICE_DIR, `${name.slice(0, 64)}.complete`));
      }
    } catch {
      // Never interfere with the Pi session; leave undelivered results for next startup.
    }
  }

  function startUpdateCheck(config: ObservalConfig, sessionId: string, ctx: ExtensionContext): void {
    if (!sessionId || !config.user_id) return;
    const key = noticeKey(config, sessionId);
    const noticeFile = path.join(UPDATE_NOTICE_DIR, `${key}.json`);
    const diagnostic = (warning: string) => {
      try {
        if (fs.existsSync(noticeFile)) return;
        fs.mkdirSync(UPDATE_NOTICE_DIR, { recursive: true, mode: 0o700 });
        const temp = path.join(UPDATE_NOTICE_DIR, `.${key}.${process.pid}.tmp`);
        fs.writeFileSync(temp, JSON.stringify({ schema: 1, registry: registryKey(config),
          account_id: config.user_id, session_id: sessionId, checked_at: Math.floor(Date.now() / 1000),
          items: [], warning }), { mode: 0o600, flag: "wx" });
        fs.renameSync(temp, noticeFile);
      } catch { /* no startup failure for an unwritable spool */ }
    };
    const command = process.env.OBSERVAL_CLI_BIN || "observal";
    // Frozen accounts get notices only; the Python worker checks consent and
    // exact ownership before it can launch any installer. Never kill a worker
    // that may have started writing, even after Pi exits.
    try {
      const child = spawn(command, ["_startup-apply", "--cwd", ctx.cwd,
        "--session-id", sessionId, "--notice-key", key], { stdio: "ignore", shell: false });
      child.unref();
      child.once("error", () => {
        diagnostic("Update worker could not start; run `observal outdated` manually.");
      });
      child.once("close", (code) => {
        if (code !== 0) diagnostic("Update worker stopped unexpectedly; inspect managed files before trying again.");
        if (updateCheckSession === sessionId) deliverPendingUpdateNotices(config, ctx);
      });
    } catch {
      diagnostic("Update check could not start; run `observal outdated` manually.");
    }
  }

  // ─── Helpers ─────────────────────────────────────────────────────────────

  function initState(ctx: ExtensionContext): ObservalState {
    const config = loadConfig();
    const sessionFile = ctx.sessionManager.getSessionFile() ?? null;
    const sessionId = ctx.sessionManager.getSessionId();

    let byteOffset = 0;
    let lineCount = 0;

    if (sessionId) {
      const cursor = readCursor(sessionId);
      byteOffset = cursor.offset;
      lineCount = cursor.line_count;
    }

    const layerSnapshot = buildPiLayerSnapshot(true);
    const layerHash = layerSnapshot.hash;

    // Tools Pi runs (the bash tool included) inherit this process's environment,
    // so `observal discover use` can record the exact Pi session it ran in.
    if (sessionId) process.env.OBSERVAL_SESSION_ID = sessionId;
    process.env.OBSERVAL_HARNESS = "pi";

    return { config, sessionFile, sessionId, cwd: ctx.cwd, byteOffset, lineCount, generation: 0, layerHash, layerSnapshot };
  }

  // ─── Capability attribution ───────────────────────────────────────────────

  function isSameOrUnder(candidate: string, root: string): boolean {
    const relative = path.relative(path.resolve(root), path.resolve(candidate));
    return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
  }

  function firstLineTimestampMs(sessionFile: string): number | null {
    // Pi transcripts open with {"type":"session", "timestamp": ...}. ctime is
    // not used: on Linux it moves with every write.
    try {
      const fd = fs.openSync(sessionFile, "r");
      try {
        const buffer = Buffer.alloc(4096);
        const read = fs.readSync(fd, buffer, 0, buffer.length, 0);
        const first = buffer.toString("utf-8", 0, read).split("\n")[0] ?? "";
        const record = JSON.parse(first);
        const value = record?.timestamp ?? record?.ts ?? record?.created_at;
        const parsed = typeof value === "number" ? (value > 1e11 ? value : value * 1000) : Date.parse(String(value ?? ""));
        return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
      } finally {
        fs.closeSync(fd);
      }
    } catch {
      return null;
    }
  }

  function sessionStartedAtMs(sessionFile: string | null): number {
    if (sessionFile) {
      try {
        const stat = fs.statSync(sessionFile);
        if (stat.birthtimeMs > 0) return stat.birthtimeMs - CAPABILITY_LEAD_MS;
      } catch { }
      const first = firstLineTimestampMs(sessionFile);
      if (first !== null) return first - CAPABILITY_LEAD_MS;
    }
    return Date.now() - CAPABILITY_FALLBACK_MS;
  }

  /** Capability-lock uses that belong to this session, shaped for the ingest payload. Best effort. */
  function capabilitiesForSession(s: ObservalState): Array<Record<string, unknown>> {
    try {
      if (!fs.existsSync(CAPABILITY_LOCK_PATH)) return [];
      const since = sessionStartedAtMs(s.sessionFile);
      const latest = new Map<string, Record<string, unknown>>();
      for (const line of fs.readFileSync(CAPABILITY_LOCK_PATH, "utf-8").split("\n")) {
        if (!line.trim()) continue;
        let use: Record<string, unknown>;
        try { use = JSON.parse(line); } catch { continue; }
        if (typeof use.ts !== "string" || typeof use.kind !== "string") continue;
        const exact = typeof use.session_hint === "string" && use.session_hint === s.sessionId;
        if (!exact) {
          if (typeof use.harness === "string" && use.harness !== "pi") continue;
          if (typeof use.cwd === "string" && use.cwd && s.cwd && !isSameOrUnder(use.cwd, s.cwd)) continue;
          const at = Date.parse(use.ts);
          if (Number.isNaN(at) || at < since) continue;
        }
        const key = (use.identifier as string) || `${use.kind}:${use.component_id ?? use.native_ref ?? ""}`;
        const previous = latest.get(key);
        if (!previous || String(previous.used_at) <= String(use.ts)) {
          latest.set(key, {
            identifier: use.identifier ?? null,
            kind: use.kind,
            component_id: use.component_id ?? null,
            native_ref: use.native_ref ?? null,
            version: use.version ?? null,
            digest: use.digest ?? null,
            mode: use.mode ?? "context",
            source: use.source ?? "unknown",
            used_at: use.ts,
            confidence: exact ? "exact" : s.sessionFile ? "window" : "loose",
          });
        }
      }
      return [...latest.values()]
        .sort((a, b) => String(b.used_at).localeCompare(String(a.used_at)))
        .slice(0, MAX_CAPABILITIES_PER_PUSH);
    } catch {
      return [];
    }
  }

  function loadConfig(): ObservalConfig | null {
    try {
      if (!fs.existsSync(CONFIG_PATH)) return null;
      const raw = fs.readFileSync(CONFIG_PATH, "utf-8");
      const data = JSON.parse(raw);
      const accessToken = data.api_key || data.access_token;
      if (!data.server_url || !accessToken) return null;
      const config: ObservalConfig = {
        server_url: data.server_url,
        access_token: accessToken,
        user_id: data.user_id || undefined,
      };
      // A delegated child (ADR 0002) runs as the agent it was delegated to, whatever agent Pi has selected.
      const delegatedAgent = process.env.OBSERVAL_DELEGATION_TASK_ID ? process.env.OBSERVAL_AGENT_ID : undefined;
      if (delegatedAgent) {
        config.agent_id = delegatedAgent;
        if (process.env.OBSERVAL_AGENT_VERSION) config.agent_version = process.env.OBSERVAL_AGENT_VERSION;
      } else if (data.active_agent?.id) {
        const binding = resolvePiAgentBinding(String(data.active_agent.id), data.active_agent.name, data.active_agent.version);
        config.agent_id = binding.id;
        if (binding.version) config.agent_version = binding.version;
      }
      return config;
    } catch {
      return null;
    }
  }

  function currentRegistryLockfile(): Record<string, any> | null {
    try {
      const config = loadConfig();
      if (!config || !fs.existsSync(LOCKFILE_PATH)) return null;
      const url = new URL(config.server_url);
      url.hash = "";
      url.search = "";
      url.pathname = url.pathname.replace(/\/$/, "");
      const key = url.toString().replace(/\/$/, "");
      const data = JSON.parse(fs.readFileSync(LOCKFILE_PATH, "utf-8"));
      return data.registries?.[key] ?? null;
    } catch {
      return null;
    }
  }

  function resolvePiAgentBinding(agent: string, rawName?: unknown, rawVersion?: unknown): { id: string; name: string; version?: string } {
    const name = typeof rawName === "string" && rawName.trim() ? rawName.trim() : agent;
    const entry = findPiLockfileAgent(agent, name);
    return {
      id: typeof entry?.id === "string" && entry.id.trim() ? entry.id : agent,
      name: typeof entry?.name === "string" && entry.name.trim() ? entry.name : name,
      version: normalizeAgentVersion(entry?.version) ?? normalizeAgentVersion(rawVersion),
    };
  }

  function findPiLockfileAgent(agent: string, name: string): Record<string, any> | null {
    try {
      const agents = currentRegistryLockfile()?.harnesses?.pi?.agents;
      if (!Array.isArray(agents)) return null;
      const keys = new Set([agent, name, safeAgentName(agent), safeAgentName(name)].filter(Boolean));
      return agents.find((item) => keys.has(String(item?.id ?? "")))
        ?? agents.find((item) => keys.has(String(item?.name ?? "")) || keys.has(safeAgentName(String(item?.name ?? ""))))
        ?? null;
    } catch {
      return null;
    }
  }

  function normalizeAgentVersion(version: unknown): string | undefined {
    if (typeof version !== "string") return undefined;
    const trimmed = version.trim();
    return trimmed && trimmed !== "latest" ? trimmed : undefined;
  }

  function safeAgentName(name: string): string {
    return name.replace(/[^a-zA-Z0-9_-]/g, "-");
  }

  function buildPiLayerSnapshot(includeContent: boolean): LayerSnapshot {
    const piHome = path.join(os.homedir(), ".pi", "agent");
    const files = discoverPiLayerFiles(piHome);
    const manifest: LayerFileEntry[] = [];

    for (const file of files) {
      try {
        const rel = path.relative(piHome, file).split(path.sep).join("/");
        const content = fs.readFileSync(file);
        const entry: LayerFileEntry = {
          path: `user:${rel}`,
          hash: `sha256-${sha256(content)}`,
          size: content.length,
          source: "user",
        };
        if (includeContent) {
          entry.content = content.toString("utf-8");
        }
        manifest.push(entry);
      } catch {
        continue;
      }
    }

    manifest.sort((a, b) => a.path.localeCompare(b.path));
    const hashEntries = manifest.map((entry) => [`pi/${entry.path}`, entry.hash] as [string, string]);
    const layerHash = hashEntries.length === 0 ? "0".repeat(16) : sha256(Buffer.from(pyJsonPairs(hashEntries))).slice(0, 16);

    return {
      hash: layerHash,
      harnesses: { pi: manifest },
      lockfile_hash: computeLockfileHash(),
      pinned_versions: readPinnedVersions(),
      drift: { is_canonical: true, drifted_files: [] },
    };
  }

  function discoverPiLayerFiles(root: string): string[] {
    if (!fs.existsSync(root)) return [];
    let rootReal: string;
    try {
      rootReal = fs.realpathSync(root);
    } catch {
      return [];
    }
    const found: string[] = [];
    const skipDirs = new Set([".git", "node_modules", "sessions"]);

    function walk(dir: string): void {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        if (entry.isDirectory() && skipDirs.has(entry.name)) continue;
        const abs = path.join(dir, entry.name);
        if (entry.isDirectory()) {
          walk(abs);
          continue;
        }
        if (!entry.isFile()) continue;

        const rel = path.relative(root, abs).split(path.sep).join("/");
        if (!isPiLayerFile(rel)) continue;

        try {
          const stat = fs.statSync(abs);
          if (stat.size > MAX_LAYER_FILE_SIZE) continue;
          const real = fs.realpathSync(abs);
          if (real !== rootReal && !real.startsWith(`${rootReal}${path.sep}`)) continue;
          found.push(abs);
        } catch {
          continue;
        }
      }
    }

    try {
      walk(root);
    } catch {
      return [];
    }

    return found.sort().slice(0, 200);
  }

  function isPiLayerFile(rel: string): boolean {
    return ["AGENTS.md", "SYSTEM.md", "APPEND_SYSTEM.md", "mcp.json", "settings.json"].includes(rel)
      || /^skills\/[^/]+\/SKILL\.md$/.test(rel)
      || rel.startsWith("sandboxes/")
      || /^agents\/[^/]+\/(AGENTS\.md|SYSTEM\.md|APPEND_SYSTEM\.md|mcp\.json)$/.test(rel)
      || /^agents\/[^/]+\/skills\/[^/]+\/SKILL\.md$/.test(rel)
      || /^agents\/[^/]+\/sandboxes\//.test(rel);
  }

  function sha256(content: Buffer): string {
    return crypto.createHash("sha256").update(content).digest("hex");
  }

  function pyJsonPairs(entries: [string, string][]): string {
    return `[${entries.map(([left, right]) => `[${JSON.stringify(left)}, ${JSON.stringify(right)}]`).join(", ")}]`;
  }

  function computeLockfileHash(): string {
    const registry = currentRegistryLockfile();
    return registry ? sha256(Buffer.from(JSON.stringify(registry))).slice(0, 16) : "0".repeat(16);
  }

  function readPinnedVersions(): Record<string, unknown> {
    try {
      const registry = currentRegistryLockfile();
      if (!registry) return { agents: [], standalone: [] };
      const agents: Record<string, unknown>[] = [];
      const standalone: Record<string, unknown>[] = [];
      for (const [harness, section] of Object.entries((registry.harnesses ?? {}) as Record<string, any>)) {
        for (const agent of section.agents ?? []) {
          agents.push({ ...agent, harness });
        }
        for (const item of section.standalone ?? []) {
          standalone.push({ ...item, harness });
        }
      }
      return { agents, standalone };
    } catch {
      return { agents: [], standalone: [] };
    }
  }

  function needsLayerUpload(hash: string): boolean {
    try {
      if (!fs.existsSync(LAYER_SNAPSHOT_PATH)) return true;
      const data = JSON.parse(fs.readFileSync(LAYER_SNAPSHOT_PATH, "utf-8"));
      return data.hash !== hash;
    } catch {
      return true;
    }
  }

  function saveLayerSnapshot(snapshot: LayerSnapshot): void {
    try {
      const serialized = JSON.stringify(snapshot, null, 2);
      if (serialized.length > 5 * 1024 * 1024) return;
      fs.mkdirSync(OBSERVAL_DIR, { recursive: true });
      fs.writeFileSync(LAYER_SNAPSHOT_PATH, `${serialized}\n`);
    } catch {
      return;
    }
  }

  async function uploadLayerSnapshot(config: ObservalConfig, snapshot: LayerSnapshot): Promise<boolean> {
    if (!needsLayerUpload(snapshot.hash)) return true;
    const result = await postJsonWithTimeout(config, "/api/v1/layer-snapshots", JSON.stringify(snapshot));
    if (result?.hash !== snapshot.hash) return false;
    saveLayerSnapshot(snapshot);
    return true;
  }

  function readCursor(sessionId: string): CursorEntry {
    try {
      if (!fs.existsSync(SYNC_STATE_PATH)) return { offset: 0, line_count: 0, local_valid: false };
      const data = JSON.parse(fs.readFileSync(SYNC_STATE_PATH, "utf-8"));
      const entry = data[sessionId];
      if (!entry || !Number.isInteger(entry.offset) || !Number.isInteger(entry.line_count)
        || entry.offset < 0 || entry.line_count < 0) {
        return { offset: 0, line_count: 0, local_valid: false };
      }
      return { ...entry, local_valid: true };
    } catch {
      return { offset: 0, line_count: 0, local_valid: false };
    }
  }

  function writeCursor(sessionId: string, offset: number, lineCount: number, finalized = false): boolean {
    try {
      fs.mkdirSync(OBSERVAL_DIR, { recursive: true });
      let data: Record<string, CursorEntry> = {};
      if (fs.existsSync(SYNC_STATE_PATH)) {
        data = JSON.parse(fs.readFileSync(SYNC_STATE_PATH, "utf-8"));
      }
      data[sessionId] = { offset, line_count: lineCount, finalized, last_pushed_at: Date.now() };
      const temporary = `${SYNC_STATE_PATH}.${process.pid}.${Date.now()}.tmp`;
      fs.writeFileSync(temporary, JSON.stringify(data, null, 2), { mode: 0o600 });
      fs.renameSync(temporary, SYNC_STATE_PATH);
      return true;
    } catch {
      return false;
    }
  }

  function pendingPath(sessionId: string): string {
    return path.join(OUTBOX_DIR, `${sha256(Buffer.from(sessionId))}.json`);
  }

  function readPending(sessionId: string): PendingBatch | null {
    const file = pendingPath(sessionId);
    if (!fs.existsSync(file)) return null;
    const pending = JSON.parse(fs.readFileSync(file, "utf-8"));
    if (pending?.session_id !== sessionId || !pending?.payload) {
      throw new Error(`invalid Pi outbox entry: ${file}`);
    }
    return pending;
  }

  function outboxBytes(exclude: string): number {
    if (!fs.existsSync(OUTBOX_DIR)) return 0;
    let total = 0;
    for (const name of fs.readdirSync(OUTBOX_DIR)) {
      const file = path.join(OUTBOX_DIR, name);
      if (file === exclude || !name.endsWith(".json")) continue;
      try { total += fs.statSync(file).size; } catch { }
    }
    return total;
  }

  function writePending(pending: PendingBatch): boolean {
    try {
      fs.mkdirSync(OUTBOX_DIR, { recursive: true });
      const file = pendingPath(pending.session_id);
      const serialized = JSON.stringify(pending);
      if (outboxBytes(file) + Buffer.byteLength(serialized) > MAX_OUTBOX_BYTES) return false;
      const temporary = `${file}.${process.pid}.${Date.now()}.tmp`;
      fs.writeFileSync(temporary, serialized, { mode: 0o600 });
      fs.renameSync(temporary, file);
      return true;
    } catch {
      return false;
    }
  }

  function removePending(sessionId: string): void {
    try { fs.unlinkSync(pendingPath(sessionId)); } catch { }
  }

  async function deliverPending(
    config: ObservalConfig,
    pending: PendingBatch,
  ): Promise<"delivered" | "repair" | false> {
    if (pending.destination.replace(/\/$/, "") !== config.server_url.replace(/\/$/, "")) return false;
    if (pending.user_id && pending.user_id !== config.user_id) return false;

    const acknowledgement = await postJsonWithTimeout(
      config,
      "/api/v1/ingest/session",
      JSON.stringify(pending.payload),
      TIMEOUT_MS,
    );
    if (Number.isInteger(acknowledgement?.repair_from_line)) {
      const repairFromLine = Number(acknowledgement.repair_from_line);
      const acknowledgedOffset = Number(acknowledgement.acknowledged_offset || 0);
      if (!writeCursor(pending.session_id, acknowledgedOffset, repairFromLine, false)) return false;
      removePending(pending.session_id);
      return "repair";
    }
    if (!acknowledgementCovers(acknowledgement, pending)) return false;
    if (!writeCursor(pending.session_id, pending.end_offset, pending.end_line + 1, pending.final)) return false;
    removePending(pending.session_id);
    return "delivered";
  }

  function hashSessionFile(sessionFile: string): { hash: string; lineCount: number } {
    const content = fs.readFileSync(sessionFile);
    const hasher = crypto.createHash("sha256");
    let lineCount = 0;
    let start = 0;
    for (let index = 0; index < content.length; index++) {
      if (content[index] !== 10) continue;
      const line = content.subarray(start, index).toString("utf-8").replace(/\r$/, "");
      if (line.trim()) {
        hasher.update(crypto.createHash("sha256").update(line, "utf-8").digest("hex"));
        hasher.update("\n");
        lineCount++;
      }
      start = index + 1;
    }
    return { hash: hasher.digest("hex"), lineCount };
  }

  function checkpointByteOffset(sessionFile: string, lineCount: number, serverOffset: number): number | null {
    try {
      const content = fs.readFileSync(sessionFile);
      if (serverOffset > 0) {
        return serverOffset <= content.length && content[serverOffset - 1] === 10 ? serverOffset : null;
      }
      if (lineCount === 0) return 0;
      let seen = 0;
      let start = 0;
      for (let index = 0; index < content.length; index++) {
        if (content[index] !== 10) continue;
        if (content.subarray(start, index).toString("utf-8").trim()) {
          seen++;
          if (seen === lineCount) return index + 1;
        }
        start = index + 1;
      }
    } catch { }
    return null;
  }

  async function recoverCursorFromServer(
    config: ObservalConfig,
    sessionId: string,
    sessionFile: string,
  ): Promise<CursorEntry | null> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), TIMEOUT_MS);
    try {
      const url = new URL("/api/v1/ingest/session/checkpoint", config.server_url);
      url.searchParams.set("session_id", sessionId);
      url.searchParams.set("harness", "pi");
      const response = await fetch(url, {
        headers: { Authorization: `Bearer ${config.access_token}` },
        signal: controller.signal,
      });
      if (!response.ok) return null;
      const checkpoint = await response.json();
      if (!Number.isInteger(checkpoint?.acknowledged_line)) return null;
      const lineCount = checkpoint.acknowledged_line + 1;
      const byteOffset = checkpointByteOffset(
        sessionFile,
        lineCount,
        Number(checkpoint.acknowledged_offset || 0),
      );
      if (byteOffset === null) return null;
      if (!writeCursor(sessionId, byteOffset, lineCount, false)) return null;
      return readCursor(sessionId);
    } catch {
      return null;
    } finally {
      clearTimeout(timeout);
    }
  }

  async function pushNewLines(
    s: ObservalState,
    opts: { final: boolean; repairAttempted?: boolean },
  ): Promise<void> {
    if (!s.config || !s.sessionFile) return;

    const gen = ++s.generation;

    try {
      let cursor = readCursor(s.sessionId);
      const storedPending = readPending(s.sessionId);
      if (storedPending) {
        const result = await deliverPending(s.config, storedPending);
        if (!result) return;
        if (s.generation !== gen) return;
        cursor = readCursor(s.sessionId);
      }
      if (!cursor.local_valid) {
        cursor = await recoverCursorFromServer(s.config, s.sessionId, s.sessionFile) ?? cursor;
        if (s.generation !== gen) return;
      }
      s.byteOffset = cursor.offset;
      s.lineCount = cursor.line_count;

      const stat = fs.statSync(s.sessionFile);
      const audit = opts.final ? hashSessionFile(s.sessionFile) : null;
      const newBytes = stat.size - s.byteOffset;
      if (newBytes < 0) return;

      let lines: string[] = [];
      let endByteOffsets: number[] = [];
      let consumedBytes = 0;

      if (newBytes > 0) {
        const buffer = Buffer.alloc(newBytes);
        const fd = fs.openSync(s.sessionFile, "r");
        try {
          fs.readSync(fd, buffer, 0, newBytes, s.byteOffset);
        } finally {
          fs.closeSync(fd);
        }

        if (s.generation !== gen) return;
        const rawLines = buffer.toString("utf-8").split("\n");
        for (let i = 0; i < rawLines.length - 1; i++) {
          const line = rawLines[i]!;
          consumedBytes += Buffer.byteLength(line, "utf-8") + 1;
          if (line.trim()) {
            lines.push(line);
            endByteOffsets.push(s.byteOffset + consumedBytes);
          }
        }
        if (endByteOffsets.length > 0) {
          endByteOffsets[endByteOffsets.length - 1] = s.byteOffset + consumedBytes;
        }
      }

      if (lines.length === 0) {
        if (consumedBytes > 0) {
          s.byteOffset += consumedBytes;
          if (!writeCursor(s.sessionId, s.byteOffset, s.lineCount, false)) return;
        }
        if (!opts.final) return;

        const payload: Record<string, unknown> = {
          session_id: s.sessionId,
          harness: "pi",
          agent_id: s.config.agent_id ?? null,
          agent_version: s.config.agent_version ?? null,
          layer_hash: s.layerHash,
          lines: [],
          end_byte_offsets: [],
          start_offset: s.lineCount,
          hook_event: "SessionShutdown",
          final: true,
          total_line_count: s.lineCount,
          total_offset: s.byteOffset,
          session_hash: audit?.hash,
          hashed_line_count: audit?.lineCount,
        };
        const finalCapabilities = capabilitiesForSession(s);
        if (finalCapabilities.length > 0) payload.capabilities_used = finalCapabilities;
        const pending: PendingBatch = {
          session_id: s.sessionId,
          destination: s.config.server_url,
          user_id: s.config.user_id,
          payload,
          end_line: s.lineCount - 1,
          end_offset: s.byteOffset,
          final: true,
        };
        if (!writePending(pending)) return;
        const result = await deliverPending(s.config, pending);
        if (result === "repair" && !opts.repairAttempted) {
          await pushNewLines(s, { final: true, repairAttempted: true });
        }
        return;
      }

      const initialLineCount = s.lineCount;
      const finalOffset = s.byteOffset + consumedBytes;
      for (let offset = 0; offset < lines.length; offset += MAX_LINES_PER_CHUNK) {
        if (s.generation !== gen) return;
        const chunk = lines.slice(offset, offset + MAX_LINES_PER_CHUNK);
        const chunkEndOffsets = endByteOffsets.slice(offset, offset + MAX_LINES_PER_CHUNK);
        const isLastChunk = offset + MAX_LINES_PER_CHUNK >= lines.length;
        const endLine = initialLineCount + offset + chunk.length - 1;
        const endOffset = chunkEndOffsets[chunkEndOffsets.length - 1]!;
        const payload: Record<string, unknown> = {
          session_id: s.sessionId,
          harness: "pi",
          agent_id: s.config.agent_id ?? null,
          agent_version: s.config.agent_version ?? null,
          layer_hash: s.layerHash,
          lines: chunk,
          end_byte_offsets: chunkEndOffsets,
          start_offset: initialLineCount + offset,
          hook_event: opts.final && isLastChunk ? "SessionShutdown" : "AgentEnd",
          final: opts.final && isLastChunk,
          ...(opts.final && isLastChunk
            ? {
                total_line_count: initialLineCount + lines.length,
                total_offset: finalOffset,
                session_hash: audit?.hash,
                hashed_line_count: audit?.lineCount,
              }
            : {}),
        };
        const chunkCapabilities = capabilitiesForSession(s);
        if (chunkCapabilities.length > 0) payload.capabilities_used = chunkCapabilities;
        const pending: PendingBatch = {
          session_id: s.sessionId,
          destination: s.config.server_url,
          user_id: s.config.user_id,
          payload,
          end_line: endLine,
          end_offset: endOffset,
          final: opts.final && isLastChunk,
        };
        if (!writePending(pending)) return;
        const result = await deliverPending(s.config, pending);
        if (!result) return;
        if (result === "repair") {
          if (!opts.repairAttempted) {
            await pushNewLines(s, { final: opts.final, repairAttempted: true });
          }
          return;
        }
        if (s.generation !== gen) return;
        s.byteOffset = endOffset;
        s.lineCount = endLine + 1;
      }
    } catch {
      // Fail-open
    }
  }

  // A rejected token is refreshed once, as the CLI's session hooks do (observal_cli/sessions/base.py);
  // otherwise an expired hooks token or access token would leave every batch in the outbox.
  async function postJsonWithTimeout(
    config: ObservalConfig,
    urlPath: string,
    body: string,
    timeoutMs = TIMEOUT_MS * 2,
  ): Promise<any | null> {
    const token = config.access_token;
    let response = await postJson(config.server_url, urlPath, body, timeoutMs, token);
    // Another request may already have refreshed the token while this one was in flight.
    if (response?.status === 401 && (config.access_token !== token || (await refreshOnce(config)))) {
      response = await postJson(config.server_url, urlPath, body, timeoutMs, config.access_token);
    }
    return response && response.status >= 200 && response.status < 300 ? response.body : null;
  }

  // Refresh tokens are single-use, so concurrent requests share one refresh.
  let refreshing: Promise<boolean> | null = null;
  function refreshOnce(config: ObservalConfig): Promise<boolean> {
    refreshing ??= refreshAccessToken(config).finally(() => {
      refreshing = null;
    });
    return refreshing;
  }

  async function refreshAccessToken(config: ObservalConfig): Promise<boolean> {
    try {
      const saved = JSON.parse(fs.readFileSync(CONFIG_PATH, "utf-8"));
      // Never store a token from one server in a config that now points at another.
      if (!saved.refresh_token || saved.server_url !== config.server_url) return false;
      const response = await postJson(
        config.server_url,
        "/api/v1/auth/token/refresh",
        JSON.stringify({ refresh_token: saved.refresh_token }),
        TIMEOUT_MS,
      );
      const accessToken = response?.status === 200 ? response.body?.access_token : undefined;
      if (typeof accessToken !== "string" || !accessToken) return false;
      const current = JSON.parse(fs.readFileSync(CONFIG_PATH, "utf-8"));
      // The server just rejected the hooks token: keep it and every later batch would be refused too.
      if (current.api_key && current.api_key === config.access_token) delete current.api_key;
      current.access_token = accessToken;
      if (response.body.refresh_token) current.refresh_token = response.body.refresh_token;
      const temporary = `${CONFIG_PATH}.${process.pid}.${Date.now()}.tmp`;
      fs.writeFileSync(temporary, JSON.stringify(current, null, 2), { mode: 0o600 });
      fs.renameSync(temporary, CONFIG_PATH);
      config.access_token = accessToken;
      return true;
    } catch {
      return false;
    }
  }

  function postJson(
    serverUrl: string,
    urlPath: string,
    body: string,
    timeoutMs: number,
    token?: string,
  ): Promise<{ status: number; body: any } | null> {
    return new Promise((resolve) => {
      try {
        const url = new URL(urlPath, serverUrl);
        const mod = url.protocol === "https:" ? https : http;
        const timer = setTimeout(() => {
          req.destroy();
          resolve(null);
        }, timeoutMs);

        const headers: Record<string, string> = {
          "Content-Type": "application/json",
          "Content-Length": String(Buffer.byteLength(body)),
        };
        if (token) headers.Authorization = `Bearer ${token}`;
        const req = mod.request(url, { method: "POST", headers }, (res) => {
          clearTimeout(timer);
          const chunks: Buffer[] = [];
          res.on("data", (c) => chunks.push(c));
          res.on("end", () => {
            let parsed: any = null;
            try {
              parsed = JSON.parse(Buffer.concat(chunks).toString("utf-8"));
            } catch {
              parsed = null;
            }
            resolve({ status: res.statusCode ?? 0, body: parsed });
          });
        });

        req.on("error", () => {
          clearTimeout(timer);
          resolve(null);
        });

        req.write(body);
        req.end();
      } catch {
        resolve(null);
      }
    });
  }


  async function drainStoredOutbox(config: ObservalConfig): Promise<void> {
    if (!fs.existsSync(OUTBOX_DIR)) return;
    for (const name of fs.readdirSync(OUTBOX_DIR)) {
      if (!name.endsWith(".json")) continue;
      try {
        const pending = JSON.parse(fs.readFileSync(path.join(OUTBOX_DIR, name), "utf-8"));
        if (!pending?.session_id || !pending?.payload) continue;
        await deliverPending(config, pending);
      } catch {
        // Keep corrupt or unreachable entries for manual recovery.
      }
    }
  }

  async function recoverStaleSessions(s: ObservalState, ctx: ExtensionContext): Promise<void> {
    try {
      if (!s.config) return;
      await drainStoredOutbox(s.config);
      if (!fs.existsSync(SYNC_STATE_PATH)) return;
      const data: Record<string, CursorEntry> = JSON.parse(
        fs.readFileSync(SYNC_STATE_PATH, "utf-8"),
      );

      const sessionsDir = (ctx.sessionManager as any).getSessionDir?.()
        ?? path.join(os.homedir(), ".pi", "agent", "sessions");
      const projectKey = ctx.cwd.replace(/\//g, "-");
      const fullDir = path.join(sessionsDir, `-${projectKey}-`);
      let recovered = 0;
      const now = Date.now();

      for (const [sessionId, storedEntry] of Object.entries(data)) {
        if (sessionId === s.sessionId || recovered >= RECOVERY_MAX_SESSIONS) continue;
        const entry = storedEntry;
        if (entry.finalized) continue;
        if (!fs.existsSync(fullDir)) continue;

        const files = fs.readdirSync(fullDir).filter((f) => f.includes(sessionId));
        if (files.length === 0) continue;
        const filePath = path.join(fullDir, files[0]!);
        if (!fs.existsSync(filePath)) continue;
        const fileStat = fs.statSync(filePath);
        if (now - fileStat.mtimeMs > RECOVERY_MAX_AGE_MS) continue;

        const recoveryState: ObservalState = {
          ...s,
          sessionFile: filePath,
          sessionId,
          byteOffset: entry.offset,
          lineCount: entry.line_count,
          generation: 0,
        };
        await pushNewLines(recoveryState, { final: true });
        if (readCursor(sessionId).finalized) recovered++;
      }

      pruneSyncState();
    } catch {
      // Fail-open
    }
  }

  function pruneSyncState(): void {
    try {
      if (!fs.existsSync(SYNC_STATE_PATH)) return;
      const data: Record<string, CursorEntry> = JSON.parse(
        fs.readFileSync(SYNC_STATE_PATH, "utf-8"),
      );
      const entries = Object.entries(data);
      if (entries.length <= 50) return;

      const required = entries.filter(([, value]) => !value.finalized);
      const recentFinalized = entries
        .filter(([, value]) => value.finalized)
        .sort((a, b) => (b[1].last_pushed_at ?? 0) - (a[1].last_pushed_at ?? 0))
        .slice(0, Math.max(0, 50 - required.length));
      const pruned: Record<string, CursorEntry> = {};
      for (const [key, value] of [...required, ...recentFinalized]) {
        pruned[key] = value;
      }
      const temporary = `${SYNC_STATE_PATH}.${process.pid}.${Date.now()}.tmp`;
      fs.writeFileSync(temporary, JSON.stringify(pruned, null, 2), { mode: 0o600 });
      fs.renameSync(temporary, SYNC_STATE_PATH);
    } catch {
      // Fail-open
    }
  }
}
