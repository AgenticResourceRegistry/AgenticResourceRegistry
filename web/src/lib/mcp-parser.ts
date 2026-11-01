// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

/**
 * MCP config JSON parser — shared between submit dialog and edit form.
 * Mirrors the CLI's _parse_direct_config logic.
 */

export interface ParsedMcpConfig {
  serverName?: string;
  description?: string;
  command?: string;
  args?: string[];
  url?: string;
  transport?: string;
  framework?: string;
  dockerImage?: string;
  envVars: EnvVar[];
  headers?: { name: string; value: string }[];
  autoApprove?: string[];
}

export interface EnvVar {
  name: string;
  description: string;
  required: boolean;
}

export function parseMcpConfigJson(raw: string): { parsed?: ParsedMcpConfig; error?: string } {
  let cfg: Record<string, unknown>;
  try {
    cfg = JSON.parse(raw);
  } catch {
    return { error: "Invalid JSON" };
  }

  // Registry format: {server: {remotes: [...]}, _meta: {...}}
  let manifest = cfg;
  const serverMeta = cfg.server;
  if (serverMeta && typeof serverMeta === "object" && !Array.isArray(serverMeta)) {
    const sm = serverMeta as Record<string, unknown>;
    if (sm.remotes || sm.packages) {
      manifest = sm;
    }
  }

  // server.json manifest format (packages[]/remotes[])
  if (manifest.packages || manifest.remotes) {
    const result = parseServerJsonManifest(manifest);
    // Extract name/description from registry metadata
    if (serverMeta && typeof serverMeta === "object") {
      const sm = serverMeta as Record<string, string>;
      const regName = sm.title || sm.name;
      if (regName) result.serverName = regName;
      const regDesc = sm.description;
      if (regDesc) result.description = regDesc;
    }
    return { parsed: result };
  }

  // Unwrap harness config formats
  const { inner, serverName } = unwrapMcpConfig(cfg);
  const result: ParsedMcpConfig = { envVars: [] };
  if (serverName) result.serverName = serverName;

  const i = inner as Record<string, unknown>;

  if (i.url && !i.command) {
    // SSE / streamable-http
    result.transport = (i.type as string) || "sse";
    result.url = i.url as string;
    const rawEnv = (i.env || {}) as Record<string, string>;
    result.envVars = Object.keys(rawEnv).map((k) => ({ name: k, description: "", required: true }));
    // Detect $VAR patterns in env values and header values
    const dollarVars = detectDollarVars([], rawEnv);
    if (i.headers && typeof i.headers === "object") {
      const headerEntries = Object.entries(i.headers as Record<string, string>);
      result.headers = headerEntries.map(([k, v]) => ({ name: k, value: v }));
      for (const [, v] of headerEntries) {
        for (const m of v.matchAll(/\$([A-Z][A-Z0-9_]*)/g)) {
          dollarVars.add(m[1]);
        }
      }
    }
    mergeDollarVarsIntoEnv(result, dollarVars);
    if (Array.isArray(i.autoApprove)) result.autoApprove = i.autoApprove as string[];
  } else if (i.command) {
    // stdio
    result.transport = "stdio";
    result.command = i.command as string;
    result.args = Array.isArray(i.args) ? (i.args as string[]) : [];
    const rawEnv = (i.env || {}) as Record<string, string>;
    result.envVars = Object.keys(rawEnv).map((k) => ({ name: k, description: "", required: true }));
    // Detect $VAR patterns in args and env values
    const dollarVars = detectDollarVars(result.args, rawEnv);
    mergeDollarVarsIntoEnv(result, dollarVars);
    // Derive framework
    if (result.command === "docker") {
      result.framework = "docker";
      // The image is the first positional after `docker run` options, not a trailing container arg.
      const image = dockerImageFromArgs(result.args);
      if (image) result.dockerImage = image;
    } else if (result.command === "python" || result.command === "python3") {
      result.framework = "python";
    } else if (result.command === "npx" || result.command === "node") {
      result.framework = "typescript";
    }
    if (Array.isArray(i.autoApprove)) result.autoApprove = i.autoApprove as string[];
  } else {
    return { error: "Could not detect command or url in config" };
  }

  return { parsed: result };
}

function parseServerJsonManifest(cfg: Record<string, unknown>): ParsedMcpConfig {
  const result: ParsedMcpConfig = { envVars: [] };
  const packages = Array.isArray(cfg.packages) ? cfg.packages : [];
  const remotes = Array.isArray(cfg.remotes) ? cfg.remotes : [];

  for (const pkg of packages) {
    for (const arg of ((pkg as Record<string, unknown[]>).runtimeArguments || [])) {
      const value = (arg as Record<string, string>).value || "";
      if (value.includes("=")) {
        const varName = value.split("=", 1)[0];
        if (varName && varName === varName.toUpperCase()) {
          result.envVars.push({ name: varName, description: (arg as Record<string, string>).description || "", required: true });
        }
      }
    }
  }

  for (const remote of remotes) {
    const r = remote as Record<string, unknown>;
    if (r.url && !result.url) {
      result.url = r.url as string;
      result.transport = (r.type as string) || "sse";
    }
    for (const [key, meta] of Object.entries((r.variables || {}) as Record<string, unknown>)) {
      const desc = meta && typeof meta === "object" ? ((meta as Record<string, string>).description || "") : "";
      result.envVars.push({ name: key, description: desc, required: true });
    }
  }

  if (!result.url) {
    const hasRemotes = Array.isArray(cfg.remotes) && cfg.remotes.length > 0;
    if (!hasRemotes) {
      // Packages-only manifest implies stdio (Docker typically)
      result.transport = "stdio";
      result.framework = "docker";
    }
    // else: remotes without a URL — don't assume transport
  }

  return result;
}

/** Detect $VAR patterns in args and env values, returning a set of variable names. */
function detectDollarVars(args: string[], env: Record<string, string>): Set<string> {
  const vars = new Set<string>();
  for (const arg of args) {
    for (const m of arg.matchAll(/\$([A-Z][A-Z0-9_]*)/g)) {
      vars.add(m[1]);
    }
  }
  for (const v of Object.values(env)) {
    for (const m of v.matchAll(/\$([A-Z][A-Z0-9_]*)/g)) {
      vars.add(m[1]);
    }
  }
  return vars;
}

/** Merge detected $VAR names into envVars, skipping names already present from explicit env keys. */
function mergeDollarVarsIntoEnv(result: ParsedMcpConfig, dollarVars: Set<string>): void {
  const existing = new Set(result.envVars.map((ev) => ev.name));
  for (const name of dollarVars) {
    if (!existing.has(name)) {
      result.envVars.push({ name, description: "", required: true });
    }
  }
}

export interface McpFieldSetters {
  setCommand: (v: string) => void;
  setArgs: (v: string) => void;
  setMcpUrl: (v: string) => void;
  setTransport: (v: string) => void;
  setFramework: (v: string) => void;
  setDockerImage: (v: string) => void;
  setEnvVars: (v: EnvVar[]) => void;
  setName?: (v: string) => void;
  setDescription?: (v: string) => void;
}

/**
 * Apply parsed MCP config fields to state setters.
 * `mode` controls whether blank fields in parsed config overwrite existing state:
 * - "overwrite": always set all fields (for edit mode)
 * - "fill": only set fields that have values (for new submissions / edit form)
 */
export function applyParsedConfig(
  parsed: ParsedMcpConfig,
  setters: McpFieldSetters,
  mode: "overwrite" | "fill" = "fill",
): void {
  if (mode === "overwrite") {
    if (parsed.serverName && setters.setName) setters.setName(parsed.serverName);
    if (parsed.description && setters.setDescription) setters.setDescription(parsed.description);
    setters.setCommand(parsed.command || "");
    setters.setArgs(parsed.args ? parsed.args.join(" ") : "");
    setters.setMcpUrl(parsed.url || "");
    setters.setTransport(parsed.transport || "");
    setters.setFramework(parsed.framework || "");
    setters.setDockerImage(parsed.dockerImage || "");
    setters.setEnvVars(parsed.envVars.length > 0 ? parsed.envVars : []);
  } else {
    if (parsed.serverName && setters.setName) setters.setName(parsed.serverName);
    if (parsed.description && setters.setDescription) setters.setDescription(parsed.description);
    if (parsed.command) setters.setCommand(parsed.command);
    if (parsed.args) setters.setArgs(parsed.args.join(" "));
    if (parsed.url) setters.setMcpUrl(parsed.url);
    if (parsed.transport) setters.setTransport(parsed.transport);
    if (parsed.framework) setters.setFramework(parsed.framework);
    if (parsed.dockerImage) setters.setDockerImage(parsed.dockerImage);
    if (parsed.envVars.length > 0) setters.setEnvVars(parsed.envVars);
  }
}

function unwrapMcpConfig(cfg: Record<string, unknown>): { inner: Record<string, unknown>; serverName?: string } {
  // Shape 1: {mcpServers: {name: config}}
  if (cfg.mcpServers && typeof cfg.mcpServers === "object") {
    const servers = cfg.mcpServers as Record<string, unknown>;
    const keys = Object.keys(servers);
    if (keys.length === 1 && typeof servers[keys[0]] === "object") {
      return { inner: servers[keys[0]] as Record<string, unknown>, serverName: keys[0] };
    }
    return { inner: cfg };
  }
  // Shape 3: bare config
  if (cfg.command || cfg.url || cfg.type) return { inner: cfg };
  // Shape 2: single named key
  const keys = Object.keys(cfg);
  if (keys.length === 1 && typeof cfg[keys[0]] === "object") {
    const inner = cfg[keys[0]] as Record<string, unknown>;
    if (inner.command || inner.url || inner.type) return { inner, serverName: keys[0] };
  }
  return { inner: cfg };
}

// `docker run` options that take a separate value argument. Mirrors observal_shared.mcp_analysis.
const DOCKER_RUN_VALUE_FLAGS = new Set([
  "-a", "--attach", "--add-host", "--annotation", "--blkio-weight", "--blkio-weight-device",
  "--cap-add", "--cap-drop", "--cgroup-parent", "--cgroupns", "--cidfile", "--cpu-period", "--cpu-quota",
  "--cpu-rt-period", "--cpu-rt-runtime", "-c", "--cpu-shares", "--cpus", "--cpuset-cpus", "--cpuset-mems",
  "--detach-keys", "--device", "--device-cgroup-rule", "--device-read-bps", "--device-read-iops",
  "--device-write-bps", "--device-write-iops", "--dns", "--dns-option", "--dns-search", "--domainname",
  "--entrypoint", "-e", "--env", "--env-file", "--expose", "--gpus", "--group-add", "--health-cmd",
  "--health-interval", "--health-retries", "--health-start-interval", "--health-start-period",
  "--health-timeout", "-h", "--hostname", "--ip", "--ip6", "--ipc", "--isolation", "--kernel-memory",
  "-l", "--label", "--label-file", "--link", "--link-local-ip", "--log-driver", "--log-opt",
  "--mac-address", "-m", "--memory", "--memory-reservation", "--memory-swap", "--memory-swappiness",
  "--mount", "--name", "--network", "--net", "--network-alias", "--net-alias", "--oom-score-adj", "--pid",
  "--pids-limit", "--platform", "-p", "--publish", "--pull", "--restart", "--runtime", "--security-opt",
  "--shm-size", "--stop-signal", "--stop-timeout", "--storage-opt", "--sysctl", "--tmpfs", "--ulimit",
  "-u", "--user", "--userns", "--uts", "-v", "--volume", "--volume-driver", "--volumes-from", "-w",
  "--workdir",
]);
const DOCKER_SHORT_VALUE_FLAGS = new Set([...DOCKER_RUN_VALUE_FLAGS].filter((flag) => flag.length === 2).map((flag) => flag[1]));

/** The image in `docker run [OPTIONS] IMAGE [COMMAND] [ARG...]`. Args may omit `run` and start with options. */
export function dockerImageFromArgs(args: string[]): string | undefined {
  let tokens = [...args];
  if (tokens[0] === "container" && tokens[1] === "run") tokens = tokens.slice(2);
  else if (tokens[0] === "run") tokens = tokens.slice(1);
  else if (!tokens[0]?.startsWith("-")) return undefined;
  for (let index = 0; index < tokens.length; ) {
    const token = tokens[index];
    if (token === "--") return tokens[index + 1];
    if (token.startsWith("--")) {
      index += token.includes("=") || !DOCKER_RUN_VALUE_FLAGS.has(token) ? 1 : 2;
      continue;
    }
    if (DOCKER_RUN_VALUE_FLAGS.has(`-${token}`)) {
      // A single-dash long flag such as -env (common in hand-written configs) takes a value.
      index += 2;
      continue;
    }
    if (token.startsWith("-") && token.length > 1) {
      // Grouped short flags such as -it; a value flag takes the rest of the token or the next one.
      const letters = token.slice(1);
      const valueAt = [...letters].findIndex((letter) => DOCKER_SHORT_VALUE_FLAGS.has(letter));
      index += valueAt === letters.length - 1 ? 2 : 1;
      continue;
    }
    return token;
  }
  return undefined;
}
