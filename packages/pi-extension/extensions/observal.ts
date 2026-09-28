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
const LAYER_UPLOADED_PATH = path.join(OBSERVAL_DIR, "pi_layer_uploaded.json");
const LOCKFILE_PATH = path.join(OBSERVAL_DIR, "lockfile.json");
const OUTBOX_DIR = path.join(OBSERVAL_DIR, "pi_session_outbox");
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
// pi-mcp-adapter config sources (2.x reads mcp.json; 3.x reads mcp-adapter.json).
// All are hash-only layer files so the verification inputs are part of identity.
const PI_HOME_MCP_SOURCES = [".config/mcp/mcp.json", ".agents/mcp.json", ".agents/mcp/mcp.json"];
const PI_MCP_SOURCES = [
  ...PI_HOME_MCP_SOURCES.map((rel) => `user:${rel}`),
  "user:mcp.json",
  "user:mcp-adapter.json",
  "project:.mcp.json",
  "project:.pi/mcp.json",
  "project:.pi/mcp-adapter.json",
];
const PI_MCP_VERIFICATION_PATH = "observal:mcp-verification";
// Bump when verification semantics change: the layer hash then changes too.
const PI_MCP_VERIFIER = "observal-pi-mcp-verification-v1";
// The alias alphabet the CLI installs and verifies.
const SAFE_MCP_ALIAS = /^[A-Za-z0-9_-]{1,128}$/;

/**
 * Port of ``observal_cli.layer.mcp_entry_fingerprint`` for the inputs whose
 * Python encoding is unambiguous. Anything else returns null (unverified),
 * so a representation difference can never be reported as drift.
 */
export function mcpEntryFingerprint(entry: Record<string, unknown>): string | null {
  const own = (key: string) => Object.prototype.hasOwnProperty.call(entry, key);
  let safeUrl = "";
  if (own("url") && typeof entry.url === "string") {
    const parsed = safeMcpUrl(entry.url);
    if (parsed === null) return null;
    safeUrl = parsed;
  }
  const structure = [own("command") ? entry.command : "", own("args") ? entry.args : [], safeUrl,
    own("type") ? entry.type : ""];
  const encoded = pythonJson(structure);
  if (encoded === null) return null;
  return `sha256-${crypto.createHash("sha256").update(Buffer.from(encoded, "utf-8")).digest("hex")}`;
}

/** ``json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))``, or null. */
function pythonJson(value: unknown): string | null {
  if (value === null) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return Number.isSafeInteger(value) ? String(value) : null;
  if (typeof value === "string") return value.isWellFormed() ? JSON.stringify(value) : null;
  if (Array.isArray(value)) {
    const items: string[] = [];
    for (const item of value) {
      const encoded = pythonJson(item);
      if (encoded === null) return null;
      items.push(encoded);
    }
    return `[${items.join(",")}]`;
  }
  if (typeof value === "object") {
    const keys = Object.keys(value as object);
    // UTF-16 order equals Python's code point order only inside the BMP.
    if (keys.some((key) => !key.isWellFormed() || /[\ud800-\udfff]/.test(key))) return null;
    const parts: string[] = [];
    for (const key of keys.sort()) {
      const encoded = pythonJson((value as Record<string, unknown>)[key]);
      if (encoded === null) return null;
      parts.push(`${JSON.stringify(key)}:${encoded}`);
    }
    return `{${parts.join(",")}}`;
  }
  return null;
}

/**
 * ``urlunsplit((scheme, host[:port], path, "", ""))`` for a plain
 * ``scheme://[userinfo@]host[:port][/path][?query][#fragment]`` URL.
 * Irregular URLs (IPv6 brackets, zone ids, invalid ports) return null.
 */
function safeMcpUrl(raw: string): string | null {
  // urlsplit strips *leading* C0 controls and spaces, and removes tab/CR/LF anywhere.
  const url = raw.replace(/^[\x00-\x20]+/, "").replace(/[\t\r\n]/g, "");
  const match = /^([A-Za-z][A-Za-z0-9+.-]*):\/\/([^/?#]*)([^?#]*)/.exec(url);
  if (!match) return url === "" ? "" : null;
  const [, scheme, netloc, urlPath] = match;
  const hostinfo = netloc.slice(netloc.lastIndexOf("@") + 1);
  if (/[[\]%]/.test(netloc) || !/^[\x21-\x7e]*$/.test(netloc)) return null;
  const colon = hostinfo.indexOf(":");
  const host = (colon < 0 ? hostinfo : hostinfo.slice(0, colon)).toLowerCase();
  const portText = colon < 0 ? "" : hostinfo.slice(colon + 1);
  if (!host) return null;
  let authority = host;
  if (portText) {
    if (!/^[0-9]+$/.test(portText)) return null;
    const port = Number(portText);
    if (port > 65535) return null;
    authority = `${host}:${port}`;
  }
  return `${scheme.toLowerCase()}://${authority}${urlPath}`;
}

export function acknowledgementCovers(acknowledgement: unknown, pending: PendingBatch): boolean {
  if (!acknowledgement || typeof acknowledgement !== "object") return false;
  const acknowledgedLine = (acknowledgement as Record<string, unknown>).acknowledged_line;
  return Number.isInteger(acknowledgedLine) && Number(acknowledgedLine) >= pending.end_line;
}

// ─── Extension Entry ─────────────────────────────────────────────────────────

export default function (pi: ExtensionAPI) {
  let state: ObservalState | null = null;

  pi.on("session_start", async (event, ctx) => {
    state = initState(ctx);

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

          state.layerSnapshot = buildPiLayerSnapshot(true, ctx.cwd);
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

    const layerSnapshot = buildPiLayerSnapshot(true, ctx.cwd);
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

  function buildPiLayerSnapshot(includeContent: boolean, cwd: string): LayerSnapshot {
    const piHome = path.join(os.homedir(), ".pi", "agent");
    const registry = currentRegistryLockfile();
    const manifest: LayerFileEntry[] = [];
    // The exact bytes hashed for each MCP config source, reused for verification.
    const mcpSourceBytes = new Map<string, Buffer>();
    const files: Array<[string, string, string]> = [];
    for (const [scope, root] of [["user", piHome], ["project", cwd]] as const) {
      for (const file of discoverPiLayerFiles(root, scope)) files.push([scope, root, file]);
    }
    for (const file of discoverHomeMcpSources()) files.push(["user", os.homedir(), file]);
    for (const [scope, root, file] of files) {
      try {
        const rel = path.relative(root, file).split(path.sep).join("/");
        const display = `${scope}:${rel}`;
        if (manifest.some((entry) => entry.path === display)) continue;
        const content = fs.readFileSync(file);
        const entry: LayerFileEntry = {
          path: display,
          hash: `sha256-${sha256(content)}`,
          size: content.length,
          source: "user",
        };
        // MCP/settings JSON may contain inline credentials. Keep the hash of
        // the original bytes for v2 identity but never upload their contents.
        if (includeContent) entry.content = isSensitivePiConfig(display) ? "" : content.toString("utf-8");
        if (PI_MCP_SOURCES.includes(display)) mcpSourceBytes.set(display, content);
        manifest.push(entry);
      } catch {
        continue;
      }
    }
    const verification = piMcpVerificationEntry(registry, cwd);
    if (verification) {
      if (includeContent) verification.content = "";
      manifest.push(verification);
    }
    manifest.sort((a, b) => Buffer.compare(Buffer.from(a.path), Buffer.from(b.path)));
    const pins = readPinnedVersions(registry, cwd);
    return {
      hash: layerHashV2({ pi: manifest }, pins),
      harnesses: { pi: manifest },
      lockfile_hash: computeLockfileHash(registry),
      pinned_versions: pins,
      drift: computePiMcpDrift(registry, cwd, mcpSourceBytes, piHome),
    };
  }

  /**
   * Hash-only entry binding MCP verification inputs outside the hashed files
   * (install fingerprints, verifier revision) to the layer identity, so one
   * hash implies one ``drift`` result. Mirrors
   * ``observal_cli.layer.pi_mcp_verification_entry`` byte for byte.
   */
  function piMcpVerificationEntry(registry: Record<string, any> | null, cwd: string): LayerFileEntry | null {
    const section = registry?.harnesses?.pi;
    if (!section || typeof section !== "object" || Array.isArray(section)) return null;
    const directory = path.resolve(cwd);
    const included = (item: Record<string, any>): boolean =>
      item.scope === "user" || (typeof item.directory === "string" && path.resolve(item.directory) === directory);
    const rows: string[][] = [];
    const parents: Array<[Record<string, any> | null, unknown]> = [];
    for (const agent of Array.isArray(section.agents) ? section.agents : []) {
      if (isPlainObject(agent) && included(agent)) parents.push([agent, agent.components]);
    }
    parents.push([null, (Array.isArray(section.standalone) ? section.standalone : [])
      .filter((item: unknown) => isPlainObject(item) && included(item))]);
    for (const [parent, components] of parents) {
      for (const component of Array.isArray(components) ? components : []) {
        if (!isPlainObject(component) || component.type !== "mcp") continue;
        const scope = text(component.scope) || (parent ? text(parent.scope) : "") || "project";
        rows.push([parent ? uuid(parent.id) : "", uuid(component.id), text(component.local_name), scope,
          text(component.mcp_integrity)]);
      }
    }
    if (!rows.length) return null;
    rows.sort((left, right) => {
      for (let index = 0; index < left.length; index++) {
        const difference = Buffer.compare(Buffer.from(left[index]), Buffer.from(right[index]));
        if (difference) return difference;
      }
      return 0;
    });
    const data = Buffer.from(JSON.stringify([PI_MCP_VERIFIER, rows]), "utf-8");
    return { path: PI_MCP_VERIFICATION_PATH, hash: `sha256-${sha256(data)}`, size: data.length, source: "observal" };
  }

  function isSensitivePiConfig(display: string): boolean {
    return display === "user:settings.json" || display.endsWith("mcp.json") || display.endsWith("mcp-adapter.json");
  }

  /** User-global MCP files outside ~/.pi/agent that pi-mcp-adapter also loads. */
  function discoverHomeMcpSources(): string[] {
    const home = os.homedir();
    let homeReal: string;
    try {
      homeReal = fs.realpathSync(home);
    } catch {
      return [];
    }
    const found: string[] = [];
    for (const rel of PI_HOME_MCP_SOURCES) {
      const abs = path.join(home, ...rel.split("/"));
      try {
        const stat = fs.statSync(abs);
        if (!stat.isFile() || stat.size > MAX_LAYER_FILE_SIZE) continue;
        const real = fs.realpathSync(abs);
        if (!real.startsWith(`${homeReal}${path.sep}`)) continue;
        found.push(abs);
      } catch {
        continue;
      }
    }
    return found;
  }

  /**
   * Verify each pinned MCP against the config Pi actually loads, fail closed.
   *
   * ``verified`` requires the lockfile's install fingerprint to equal the
   * entry in the scope's active Pi MCP file, that file to be part of this
   * snapshot's identity, and no other pi-mcp-adapter config source to define
   * the same server name differently. An alias absent from the active file
   * (an inactive ``/agent`` profile, or a removed server) is ``unverified``,
   * never evidence of use. A changed definition is ``drifted``.
   */
  function computePiMcpDrift(
    registry: Record<string, any> | null,
    cwd: string,
    sourceBytes: Map<string, Buffer>,
    piHome: string,
  ): Record<string, unknown> {
    const verifications: Array<Record<string, string>> = [];
    const drifted: Array<Record<string, string>> = [];
    const section = registry?.harnesses?.pi;
    if (!section || typeof section !== "object" || Array.isArray(section)) {
      return { is_canonical: null, drifted_files: [], mcp_verifications: [] };
    }
    const sources = new Map<string, Record<string, unknown> | null>();
    let unverifiable = false;
    for (const display of PI_MCP_SOURCES) {
      const bytes = sourceBytes.get(display);
      if (!bytes) {
        // A source that exists but is not in the manifest (oversize, symlink
        // escape) cannot be proven not to shadow a server.
        if (fs.existsSync(piMcpSourcePath(display, cwd, piHome))) unverifiable = true;
        continue;
      }
      const parsed = parsePiMcpSource(bytes);
      if (parsed === "unsupported") unverifiable = true;
      sources.set(display, parsed === "unsupported" ? null : parsed);
    }
    const directory = path.resolve(cwd);
    const included = (item: Record<string, any>): boolean =>
      item.scope === "user" || (typeof item.directory === "string" && path.resolve(item.directory) === directory);
    const parents: Array<[Record<string, any> | null, unknown[]]> = [];
    for (const agent of Array.isArray(section.agents) ? section.agents : []) {
      if (agent && typeof agent === "object" && !Array.isArray(agent) && included(agent)) {
        parents.push([agent, Array.isArray(agent.components) ? agent.components : []]);
      }
    }
    parents.push([null, (Array.isArray(section.standalone) ? section.standalone : []).filter(
      (item: any) => item && typeof item === "object" && !Array.isArray(item) && included(item))]);
    for (const [parent, components] of parents) {
      for (const component of components as Array<Record<string, any>>) {
        if (!component || typeof component !== "object" || Array.isArray(component) || component.type !== "mcp") continue;
        const alias = typeof component.local_name === "string" && SAFE_MCP_ALIAS.test(component.local_name)
          ? component.local_name : "";
        const scope = text(component.scope) || (parent ? text(parent.scope) : "") || "project";
        let status = "unverified";
        const active = scope === "user" ? "user:mcp.json" : scope === "project" ? "project:.pi/mcp.json" : "";
        const integrity = typeof component.mcp_integrity === "string" ? component.mcp_integrity : "";
        const servers = active ? sources.get(active) : undefined;
        const entry = alias && servers ? servers[alias] : undefined;
        if (!unverifiable && alias && integrity && isPlainObject(entry)) {
          const fingerprint = mcpEntryFingerprint(entry);
          if (fingerprint !== null && fingerprint !== integrity) {
            status = "drifted";
          } else if (fingerprint !== null) {
            status = "verified";
            for (const [display, other] of sources) {
              if (display === active) continue;
              if (other === null) {
                status = "unverified";
                break;
              }
              if (Object.prototype.hasOwnProperty.call(other, alias)) {
                const shadow = other[alias];
                if (!isPlainObject(shadow) || mcpEntryFingerprint(shadow) !== fingerprint) {
                  status = "unverified";
                  break;
                }
              }
            }
          }
        }
        const record = {
          harness: "pi",
          component_id: uuid(component.id),
          alias,
          scope,
          parent_agent_id: parent ? uuid(parent.id) : "",
          status,
        };
        verifications.push(record);
        if (status === "drifted") {
          drifted.push({ harness: "pi", component: record.component_id, alias, status });
        }
      }
    }
    return { is_canonical: drifted.length ? false : null, drifted_files: drifted, mcp_verifications: verifications };
  }

  function piMcpSourcePath(display: string, cwd: string, piHome: string): string {
    const [scope, rel] = [display.slice(0, display.indexOf(":")), display.slice(display.indexOf(":") + 1)];
    if (scope === "project") return path.join(cwd, ...rel.split("/"));
    return PI_HOME_MCP_SOURCES.includes(rel) ? path.join(os.homedir(), ...rel.split("/")) : path.join(piHome, rel);
  }

  /** The server map of one MCP config, or "unsupported" when it may load servers indirectly. */
  function parsePiMcpSource(bytes: Buffer): Record<string, unknown> | "unsupported" {
    let data: unknown;
    try {
      data = JSON.parse(bytes.toString("utf-8"));
    } catch {
      return "unsupported";
    }
    if (!isPlainObject(data)) return "unsupported";
    // Imports, plugins, and ancestor discovery can add servers from files this
    // snapshot does not identify; a server name then cannot be proven unique.
    const settings = isPlainObject(data.settings) ? data.settings : {};
    for (const key of ["imports", "claudePlugins"]) {
      const value = data[key];
      if (value !== undefined && !(Array.isArray(value) && value.length === 0)) return "unsupported";
    }
    for (const key of ["ancestorConfigRoots", "agentPluginPaths"]) {
      const value = settings[key];
      if (value !== undefined && !(Array.isArray(value) && value.length === 0)) return "unsupported";
    }
    if (data.mcpServers === undefined) return {};
    return isPlainObject(data.mcpServers) ? data.mcpServers : "unsupported";
  }

  function isPlainObject(value: unknown): value is Record<string, any> {
    return typeof value === "object" && value !== null && !Array.isArray(value);
  }

  function discoverPiLayerFiles(root: string, scope: "user" | "project"): string[] {
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
        if (!isPiLayerFile(rel, scope)) continue;

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

  function isPiLayerFile(rel: string, scope: "user" | "project"): boolean {
    const prefix = scope === "user" ? "" : ".pi/";
    if (scope === "user" && rel === "settings.json") return true;
    if (scope === "project" && rel === ".mcp.json") return true;
    if (["AGENTS.md", `${prefix}SYSTEM.md`, `${prefix}APPEND_SYSTEM.md`, `${prefix}mcp.json`,
      `${prefix}mcp-adapter.json`].includes(rel)) return true;
    return new RegExp(`^${prefix.replace(".", "\\.")}skills/[^/]+/SKILL\\.md$`).test(rel)
      || rel.startsWith(`${prefix}sandboxes/`)
      || new RegExp(`^${prefix.replace(".", "\\.")}agents/[^/]+/(AGENTS\\.md|SYSTEM\\.md|APPEND_SYSTEM\\.md|mcp\\.json)$`).test(rel)
      || new RegExp(`^${prefix.replace(".", "\\.")}agents/[^/]+/skills/[^/]+/SKILL\\.md$`).test(rel)
      || new RegExp(`^${prefix.replace(".", "\\.")}agents/[^/]+/sandboxes/`).test(rel);
  }

  function sha256(content: Buffer): string {
    return crypto.createHash("sha256").update(content).digest("hex");
  }

  function text(value: unknown): string {
    return typeof value === "string" ? value.normalize("NFC") : "";
  }

  function uuid(value: unknown): string {
    const raw = text(value).replace(/^urn:uuid:/i, "").replace(/^\{(.*)\}$/, "$1").replace(/-/g, "");
    if (!/^[0-9a-f]{32}$/i.test(raw)) return "";
    const hex = raw.toLowerCase();
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }

  function layerHashV2(harnesses: Record<string, LayerFileEntry[]>, pins: Record<string, any>): string {
    const files = new Map<string, string>();
    for (const [harness, manifest] of Object.entries(harnesses)) {
      for (const entry of manifest) {
        const filePath = `${harness}/${entry.path}`.normalize("NFC").replace(/\\/g, "/");
        if (!/^sha256-[0-9a-f]{64}$/.test(entry.hash)) throw new Error("Invalid layer manifest hash");
        if (files.has(filePath) && files.get(filePath) !== entry.hash) throw new Error("Conflicting layer manifest path");
        files.set(filePath, entry.hash);
      }
    }
    const byteCompare = (left: string, right: string) => Buffer.compare(Buffer.from(left), Buffer.from(right));
    const pairs = [...files].sort((left, right) => byteCompare(left[0], right[0]));
    const tuples: string[][] = [];
    for (const agent of pins.agents) {
      tuples.push(["agent", "agent", agent.id, agent.version, agent.harness, agent.scope,
        agent.local_name ?? "", "", "", agent.qualified_name ?? "", agent.name]);
      for (const component of agent.components) {
        tuples.push(["agent_component", component.type, component.id, component.version, agent.harness,
          component.scope, component.local_name, agent.id, agent.version, component.qualified_name ?? "", component.name]);
      }
    }
    for (const item of pins.standalone) {
      tuples.push(["standalone", item.type, item.id, item.version, item.harness, item.scope,
        item.local_name, "", "", item.qualified_name ?? "", item.name]);
    }
    tuples.sort((left, right) => {
      for (let index = 0; index < left.length; index++) {
        const difference = byteCompare(left[index], right[index]);
        if (difference) return difference;
      }
      return 0;
    });
    return `v2_${sha256(Buffer.from(JSON.stringify(["observal-layer-v2", pairs, tuples]), "utf-8")).slice(0, 60)}`;
  }

  function computeLockfileHash(registry: Record<string, any> | null): string {
    return registry ? sha256(Buffer.from(JSON.stringify(registry))).slice(0, 16) : "0".repeat(16);
  }

  function readPinnedVersions(registry: Record<string, any> | null, cwd: string): Record<string, any> {
    const pins: Record<string, any> = { schema_version: 2, agents: [], standalone: [] };
    const section = registry?.harnesses?.pi;
    if (!section || typeof section !== "object" || Array.isArray(section)) return pins;
    const directory = path.resolve(cwd);
    const included = (item: Record<string, any>): boolean =>
      item.scope === "user" || (typeof item.directory === "string" && path.resolve(item.directory) === directory);
    const common = (item: Record<string, any>, scope: string): Record<string, any> => {
      const projected: Record<string, any> = {
        type: text(item.type), id: uuid(item.id), name: text(item.name), version: text(item.version),
        scope: text(item.scope) || scope, local_name: text(item.local_name),
      };
      if (text(item.qualified_name)) projected.qualified_name = text(item.qualified_name);
      return projected;
    };
    const agents = Array.isArray(section.agents) ? section.agents : [];
    const standalone = Array.isArray(section.standalone) ? section.standalone : [];
    for (const agent of agents) {
      if (!agent || typeof agent !== "object" || Array.isArray(agent) || !included(agent)) continue;
      const raw = Array.isArray(agent.components) ? agent.components : [];
      if (raw.length > 128) throw new Error("Too many component pins");
      const components = raw.filter((item: any) => item && typeof item === "object" && !Array.isArray(item))
        .map((item: any) => common(item, text(agent.scope) || "project"));
      const { type: _type, ...projected } = common(agent, text(agent.scope) || "project");
      pins.agents.push({ ...projected, harness: "pi", components });
    }
    for (const item of standalone) {
      if (item && typeof item === "object" && !Array.isArray(item) && included(item)) {
        pins.standalone.push({ ...common(item, "project"), harness: "pi" });
      }
    }
    if (pins.agents.length > 128 || pins.standalone.length > 512) throw new Error("Too many layer pins");
    return pins;
  }

  function needsLayerUpload(config: ObservalConfig, hash: string): boolean {
    try {
      if (!/^v2_[0-9a-f]{60}$/.test(hash)) return true;
      const marker = JSON.parse(fs.readFileSync(LAYER_UPLOADED_PATH, "utf-8"));
      return marker.hash !== hash || marker.server_url !== config.server_url.replace(/\/$/, "")
        || marker.user_id !== (config.user_id ?? "");
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
    if (!needsLayerUpload(config, snapshot.hash)) return true;
    const result = await postJsonWithTimeout(config, "/api/v1/layer-snapshots", JSON.stringify(snapshot));
    if (result?.hash !== snapshot.hash) return false;
    saveLayerSnapshot(snapshot);
    try {
      fs.writeFileSync(LAYER_UPLOADED_PATH, JSON.stringify({
        hash: snapshot.hash, server_url: config.server_url.replace(/\/$/, ""), user_id: config.user_id ?? "",
      }));
    } catch {
      // Retry on next session if the acknowledgement cannot be cached.
    }
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

      // getSessionDir() is already this project's directory. Without it, rebuild the
      // name the way Pi does: "/home/me/repo" -> "--home-me-repo--".
      const fullDir = (ctx.sessionManager as any).getSessionDir?.()
        ?? path.join(
          os.homedir(),
          ".pi",
          "agent",
          "sessions",
          `--${ctx.cwd.replace(/^[/\\]/, "").replace(/[/\\:]/g, "-")}--`,
        );
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
