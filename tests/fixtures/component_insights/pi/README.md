<!-- SPDX-FileCopyrightText: 2026 Naraen Rammoorthi -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Pi MCP activity fixtures

Live-derived **sanitized** copies of two consented headless Pi sessions, recorded on 2026-09-29 with Pi 0.84.3 and `pi-mcp-adapter` 2.38.0 in an isolated `HOME`. The pulled Agent was `component-insights-phase0/phase0-observability-probe`, whose MCP servers were installed under the aliases `super-phase0-probe` and `component-insights-phase0-phase0-probe`. Each server was a local stdio fixture with `ping` (succeeds) and `fail` (returns an MCP tool error).

- `proxy_session.jsonl`: three calls through the adapter's `mcp` proxy tool (`ping` on each server, then `fail`), plus one `mcp({search})` request, which is not a tool invocation.
- `direct_session.jsonl`: `super-phase0-probe` was set to `directTools: true`. The session has a direct `ping` call, two parallel proxy calls in **one** assistant record, and a direct `fail` call.

Every line of the original files is kept in order. Session, entry, response, and tool-call IDs were replaced with fixture values. The call IDs keep the provider's `call_…|fc_…` shape. User text, assistant text and thinking, signatures, tool-result content, and `details.mcpResult` output were replaced with placeholders, and search matches were truncated. The session `cwd` was replaced. Tool names, arguments (which are empty or name the server and tool only), block types and ordering, `toolCallId` links, `isError` flags, `details.mode`, `details.server`, `details.tool`, `details.error`, `details.canonicalTool`, timestamps, model, and usage come from the recording.

## Observed identity contract

Tool names alone do not identify an MCP server in Pi:

- proxy calls are all named `mcp`;
- direct tools are named `<server-prefix>_<tool>`, with the prefix configurable (`server`, `short`, `none`).

The adapter records the configured server name it actually dispatched to in the tool result's `details`:

- proxy success: `{"mode":"call","server":…,"tool":…,"canonicalTool":…}`;
- proxy failure: the same object plus `"error":"tool_error"`;
- direct success: `{"server":…,"tool":…}`;
- direct failure: `{"error":"tool_error","server":…}`, which has **no** `tool`.

`isError` is an explicit boolean on every result. The same `details` shapes appear in the `pi-mcp-adapter` 3.2.0 source (`proxy-modes.ts` `callIdentity`, `direct-tools.ts`). Version 3.x reads `mcp-adapter.json` instead of Pi's `mcp.json`, which is why the Pi extension hashes and checks both file sets before reporting an MCP as verified.
